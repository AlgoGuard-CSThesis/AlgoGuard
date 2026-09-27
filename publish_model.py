"""Maintainer-only immutable model publication. Never imported by analyst startup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.parse
from pathlib import Path
from uuid import UUID

from model_runtime import (
    MAX_MODEL_BYTES,
    ModelDeliveryError,
    compatibility_metadata,
    verify_artifact_contract,
)


def validate_api_url(api_url):
    parts = urllib.parse.urlsplit(api_url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
        or (parts.scheme == "http" and parts.hostname not in {"127.0.0.1", "localhost", "::1"})
    ):
        raise ModelDeliveryError("Use an HTTPS project URL, or a loopback URL for local tests.")


def _manifest(connection, publication_id):
    with connection.cursor() as cursor:
        cursor.execute(
            "select row_to_json(m) from public.model_manifest m where publication_id=%s",
            (str(publication_id),),
        )
        row = cursor.fetchone()
        return row[0] if row else None


def _required_manifest(connection, publication_id):
    manifest = _manifest(connection, publication_id)
    if manifest is None:
        raise ModelDeliveryError("The publication manifest is unavailable.")
    if not isinstance(manifest, dict):
        raise ModelDeliveryError("The publication manifest is invalid.")
    return manifest


def stage_publication(connection, publication_id, model, artifact_path, created_by=None):
    """Quality check before any cloud write; durable intent precedes object upload."""
    import joblib

    from services.deployment_service import (
        _validate_stacking_artifact,
        stacking_deployment_eligibility,
    )

    metadata = compatibility_metadata()
    eligible, reason = stacking_deployment_eligibility(model)
    if not eligible:
        raise ModelDeliveryError(reason)
    path = Path(artifact_path)
    if not 0 < path.stat().st_size <= MAX_MODEL_BYTES:
        raise ModelDeliveryError("Model artifact size is unsupported.")
    content = path.read_bytes()
    # The maintainer selected this local training artifact; clients never load it
    # until its protected manifest and hash have passed the distribution checks.
    import io

    artifact = joblib.load(io.BytesIO(content))
    _validate_stacking_artifact(artifact)
    verify_artifact_contract(artifact)
    digest = hashlib.sha256(content).hexdigest()
    object_path = f"releases/{publication_id}/{digest}.joblib"
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select pg_advisory_xact_lock(hashtextextended(%s, 24680))", (str(publication_id),)
            )
            existing = _manifest(connection, publication_id)
            if existing:
                if existing["source_sha256"] != digest:
                    raise ModelDeliveryError("Publication identity already belongs to other bytes.")
                return existing, content
            cursor.execute(
                "insert into public.training_run (created_by,filename,upload_timestamp,"
                "training_status,validation_status,preprocessing_status) "
                "values (%s,'published training run',to_char(now() at time zone 'UTC',"
                "'YYYY-MM-DD HH24:MI:SS'),'completed','completed','completed') returning run_id",
                (created_by,),
            )
            run_id = cursor.fetchone()[0]
            cursor.execute(
                "insert into public.detection_model (run_id,model_name,model_type,version,"
                "accuracy,f1_score,roc_auc,evaluation_status) values "
                "(%s,%s,'ensemble',%s,%s,%s,%s,'completed') returning model_id",
                (
                    run_id,
                    model["model_name"],
                    model["version"],
                    model["accuracy"],
                    model["f1_score"],
                    model["roc_auc"],
                ),
            )
            model_id = cursor.fetchone()[0]
            cursor.execute(
                "insert into public.model_deployment (model_id,run_id,deployed_by,artifact_path,"
                "deployed_at,is_active) values (%s,%s,%s,%s,to_char(now() at time zone 'UTC',"
                "'YYYY-MM-DD HH24:MI:SS'),0) returning deployment_id",
                (model_id, run_id, created_by, object_path),
            )
            deployment_id = cursor.fetchone()[0]
            cursor.execute(
                "insert into public.model_manifest (deployment_id,model_id,object_path,"
                "object_sha256,object_bytes,workflow_version,feature_schema_version,python_version,"
                "dependency_versions,dependency_lock,publication_id,source_sha256,created_by) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    deployment_id,
                    model_id,
                    object_path,
                    digest,
                    len(content),
                    metadata["workflow_version"],
                    metadata["feature_schema_version"],
                    metadata["python_version"],
                    metadata["dependency_versions"],
                    metadata["dependency_lock"],
                    str(publication_id),
                    digest,
                    created_by,
                ),
            )
            return _required_manifest(connection, publication_id), content


def publish(connection, api_url, secret_key, publication_id, model, artifact_path, created_by=None):
    import requests

    validate_api_url(api_url)
    manifest, content = stage_publication(
        connection, publication_id, model, artifact_path, created_by
    )
    if manifest["status"] in {"active", "superseded"}:
        return manifest
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"apikey": secret_key, "Authorization": "Bearer " + secret_key})
    object_url = api_url.rstrip("/") + "/storage/v1/object/models/" + manifest["object_path"]
    phase = "upload"
    try:
        # A retry first reconciles the immutable object; it does not re-upload
        # bytes already delivered before an activation failure or lost response.
        with session.head(object_url, timeout=10, allow_redirects=False) as existing:
            present = existing.status_code == 200
            if existing.status_code not in {200, 400, 404}:
                raise ModelDeliveryError("Model object reconciliation failed.")
        if not present:
            response = session.post(
                object_url,
                data=content,
                headers={"Content-Type": "application/octet-stream", "x-upsert": "false"},
                timeout=10,
                allow_redirects=False,
            )
            # A concurrent publisher may have won; trust only verified bytes.
            if response.status_code not in {200, 201, 400, 409}:
                raise ModelDeliveryError("Model upload failed; retry the publication identity.")
        phase = "remote verification"
        digest = hashlib.sha256()
        count = 0
        with session.get(object_url, stream=True, timeout=10, allow_redirects=False) as remote:
            if remote.status_code != 200:
                raise ModelDeliveryError(f"Remote verification returned HTTP {remote.status_code}.")
            for chunk in remote.iter_content(1024 * 1024):
                count += len(chunk)
                if count > manifest["object_bytes"]:
                    raise ModelDeliveryError("Remote model size mismatch.")
                digest.update(chunk)
        if count != manifest["object_bytes"] or digest.hexdigest() != manifest["object_sha256"]:
            raise ModelDeliveryError("Remote model hash mismatch.")
        phase = "publication metadata"
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "update public.model_manifest set status='published',note=null "
                    "where publication_id=%s and status in ('draft','orphaned')",
                    (str(publication_id),),
                )
        phase = "activation"
        with connection:
            with connection.cursor() as cursor:
                cursor.execute("set local lock_timeout = '10s'")
                cursor.execute("set local statement_timeout = '30s'")
                cursor.execute(
                    "select public.activate_model_publication(%s)", (str(publication_id),)
                )
        return _required_manifest(connection, publication_id)
    except Exception as failure:
        # Never blindly delete uploaded bytes after an ambiguous commit. A fresh
        # invocation reads the durable identity and reconciles active/superseded.
        try:
            connection.rollback()
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "update public.model_manifest set status='orphaned', "
                        "note='Publication incomplete; retry by publication identity' "
                        "where publication_id=%s and status in ('draft','published')",
                        (str(publication_id),),
                    )
        except Exception:
            pass  # The original durable intent remains available for reconciliation.
        detail = str(failure) if isinstance(failure, ModelDeliveryError) else "Operation failed."
        raise ModelDeliveryError(
            f"Publication incomplete during {phase}. {detail} Retry the same publication identity."
        ) from None
    finally:
        session.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-id", type=int, required=True, help="evaluated local training model"
    )
    parser.add_argument(
        "--publication-id", type=UUID, required=True, help="stable UUID for retries"
    )
    parser.add_argument("--created-by", type=int, help="optional cloud profile ID")
    parser.add_argument("--yes", action="store_true", help="confirm remote publication")
    args = parser.parse_args(argv)
    from cloud_migrate import connect, is_remote
    from maintainer_env import load_maintainer_env, require_database_url
    from services.database_service import get_model_result

    load_maintainer_env()
    dsn = require_database_url()
    if is_remote(dsn) and not args.yes:
        parser.error("Remote publication requires --yes.")
    api_url = os.environ.get("SUPABASE_API_URL", "")
    secret_key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not api_url or not secret_key:
        parser.error("Separate maintainer API URL and secret key are required.")
    connection = connect(dsn)
    try:
        model = get_model_result(args.model_id)
        if not model:
            raise ModelDeliveryError("The evaluated local model does not exist.")
        result = publish(
            connection,
            api_url,
            secret_key,
            args.publication_id,
            model,
            model["artifact_path"],
            args.created_by,
        )
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in ("publication_id", "manifest_id", "deployment_id", "status")
                }
            )
        )
        return 0
    except Exception:
        print("Publication failed. Check configuration and retry the same publication identity.")
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
