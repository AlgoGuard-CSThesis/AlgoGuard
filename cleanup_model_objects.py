"""Maintainer-only removal of model uploads that nothing uses (Stage 6C.3). A dry run unless --yes.

An object in the private ``models`` bucket is removed only when it is proven unreferenced:

* no manifest names it, or every manifest that names it is a publication that never finished
  (``draft``) or whose activation failed (``orphaned``); and
* the object and every manifest naming it are older than the grace period (24 hours by
  default), so a publication still in progress is never touched; and
* no active deployment names it as its artifact.

Objects named by an ``active``, ``published`` or ``superseded`` manifest are always kept, so the
active model and every rollback artifact survive. Manifest rows are never deleted: an orphaned
manifest whose object was removed stays as the record, and a retry of that publication uploads
the bytes again. Deletion goes through the Storage API so the stored file goes with its row; each
object is checked again immediately before it is removed.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import timedelta

from cloud_connections import owned_opener
from cloud_repository import _NoRedirect
from publish_model import validate_api_url

GRACE = timedelta(hours=24)
KEEP_STATUSES = ("active", "published", "superseded")

_CANDIDATES = """
    select o.name, o.created_at, coalesce((o.metadata->>'size')::bigint, 0),
           coalesce(array_agg(m.status order by m.manifest_id)
                    filter (where m.manifest_id is not null), '{}')
    from storage.objects o
    left join public.model_manifest m on m.object_path = o.name
    where o.bucket_id = 'models' and (%(name)s::text is null or o.name = %(name)s)
    group by o.name, o.created_at, o.metadata
    having o.created_at < now() - %(grace)s
       and bool_and(m.status is null or (m.status in ('draft','orphaned')
                                         and m.created_at < now() - %(grace)s))
       and not exists (select 1 from public.model_deployment d
                       where d.artifact_path = o.name and d.is_active = 1)
    order by o.created_at, o.name
"""


def plan(connection, grace=GRACE, name=None):
    """Objects that may be removed now, with the manifest states that name them."""
    with connection, connection.cursor() as cursor:
        cursor.execute("set transaction isolation level repeatable read, read only")
        cursor.execute(_CANDIDATES, {"grace": grace, "name": name})
        return [{"object_path": row[0], "created_at": row[1].isoformat(), "bytes": row[2],
                 "manifest_statuses": list(row[3])} for row in cursor.fetchall()]


def _remove(api_url, secret_key, name):
    request = urllib.request.Request(
        api_url.rstrip("/") + "/storage/v1/object/models",
        data=json.dumps({"prefixes": [name]}).encode(),
        method="DELETE",
        headers={"apikey": secret_key, "Authorization": "Bearer " + secret_key,
                 "Content-Type": "application/json"},
    )
    try:
        with owned_opener(_NoRedirect()).open(request, timeout=10) as response:
            removed = json.loads(response.read() or b"[]")
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return any(isinstance(item, dict) and item.get("name") == name for item in removed)


def clean(connection, api_url, secret_key, grace=GRACE):
    """Remove each planned object after checking once more that it is still unreferenced."""
    validate_api_url(api_url)
    removed, skipped = [], []
    for candidate in plan(connection, grace):
        still = plan(connection, grace, candidate["object_path"])
        if still and _remove(api_url, secret_key, candidate["object_path"]):
            removed.append(candidate)
        else:
            skipped.append(candidate)
    return removed, skipped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="remove the listed objects")
    parser.add_argument("--grace-hours", type=float, default=24.0,
                        help="only objects and manifests older than this (at least 1)")
    args = parser.parse_args(argv)
    if args.grace_hours < 1:
        parser.error("The grace period must be at least one hour.")
    from cloud_migrate import connect
    from maintainer_env import load_maintainer_env, require_database_url

    load_maintainer_env()
    api_url = os.environ.get("SUPABASE_API_URL", "")
    secret_key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not api_url or not secret_key:
        parser.error("Separate maintainer API URL and secret key are required.")
    connection = connect(require_database_url())
    grace = timedelta(hours=args.grace_hours)
    try:
        if not args.yes:
            candidates = plan(connection, grace)
            for item in candidates:
                print(json.dumps(item))
            print(f"{len(candidates)} unreferenced model object(s) would be removed. "
                  "Run again with --yes to remove them.")
            return 0
        removed, skipped = clean(connection, api_url, secret_key, grace)
        for item in removed:
            print("removed " + json.dumps(item))
        for item in skipped:
            print("kept (changed or refused) " + json.dumps(item))
        print(f"Removed {len(removed)} unreferenced model object(s); kept {len(skipped)}.")
        return 0 if not skipped else 1
    except Exception:
        print("Model object cleanup failed. Nothing further was removed.")
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
