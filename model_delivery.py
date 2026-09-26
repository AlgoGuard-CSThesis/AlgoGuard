"""User-authorized model download and verified atomic cache promotion."""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from cloud_repository import RepositoryError, _NoRedirect
from model_runtime import ModelDeliveryError, verify_artifact_contract, verify_manifest


@dataclass(frozen=True)
class PinnedModel:
    artifact: dict
    manifest: MappingProxyType
    cache_path: Path


def _digest(stream):
    digest = hashlib.sha256()
    count = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
        count += len(chunk)
    return digest.hexdigest(), count


class ModelCache:
    def __init__(self, repository, directory: Path):
        self.repository = repository
        self.directory = Path(directory)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def load_active(self):
        # Always fetch current protected metadata, including when bytes are cached.
        # Cached bytes are never a substitute for online authenticated startup.
        page = self.repository.list_records("model_manifest", filters={"status": "active"})
        if len(page.rows) != 1:
            raise ModelDeliveryError(
                "No active compatible model is available. Contact maintenance."
            )
        manifest = dict(page.rows[0])
        verify_manifest(manifest)
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / (
            f"{manifest['manifest_id']}-{manifest['object_sha256']}.joblib"
        )
        if not destination.exists():
            self._download(manifest, destination)
        # Open once: hash and load the same file descriptor, even across rename.
        try:
            with destination.open("rb") as stream:
                digest, size = _digest(stream)
                if digest != manifest["object_sha256"] or size != manifest["object_bytes"]:
                    raise ModelDeliveryError("The cached model is corrupt. Remove it and retry.")
                stream.seek(0)
                import joblib

                artifact = joblib.load(stream)
            from services.deployment_service import _validate_stacking_artifact

            _validate_stacking_artifact(artifact)
            verify_artifact_contract(artifact)
        except ModelDeliveryError:
            raise
        except Exception:
            raise ModelDeliveryError("The verified model could not be loaded.") from None
        return PinnedModel(artifact, MappingProxyType(manifest), destination)

    def _download(self, manifest, destination):
        path = urllib.parse.quote(manifest["object_path"], safe="/")
        signed = self.repository._request(
            "POST", "/storage/v1/object/sign/models/" + path, body={"expiresIn": 60}
        )
        if not isinstance(signed, dict) or not isinstance(signed.get("signedURL"), str):
            raise ModelDeliveryError("Model download authorization failed.")
        signed_path = signed["signedURL"]
        # Storage returns a relative URL. Refuse foreign origins and unexpected
        # object paths instead of forwarding signed credentials to another host.
        if not signed_path.startswith("/object/sign/models/" + path + "?"):
            raise ModelDeliveryError("Model download authorization was invalid.")
        url = self.repository.base_url + "/storage/v1" + signed_path
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.directory, suffix=".part", delete=False
            ) as out:
                temporary = Path(out.name)
                digest = hashlib.sha256()
                count = 0
                with self._opener.open(url, timeout=10) as response:
                    while chunk := response.read(1024 * 1024):
                        count += len(chunk)
                        if count > manifest["object_bytes"]:
                            raise ModelDeliveryError(
                                "The model download exceeds its manifest size."
                            )
                        digest.update(chunk)
                        out.write(chunk)
                if (
                    count != manifest["object_bytes"]
                    or digest.hexdigest() != manifest["object_sha256"]
                ):
                    raise ModelDeliveryError("The model download failed integrity verification.")
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, destination)
        except (OSError, urllib.error.URLError, RepositoryError):
            raise ModelDeliveryError("Model download failed. Reconnect and retry.") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
