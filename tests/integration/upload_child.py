"""Child process for the Stage 6C.2 kill/restart drills; the parent kills it without warning.

``upload``: upload one spool through the fault relay with the real upload worker until nothing is
pending, then print ``done``. ``write``: keep adding events to a spool, printing a count every
fifty, until killed.
"""

import json
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cloud_auth import CloudAuth  # noqa: E402
from cloud_outbox import Outbox  # noqa: E402
from cloud_repository import UserNodeContext  # noqa: E402
from cloud_sync import SyncWorker  # noqa: E402


def write(config):
    owner = UserNodeContext(UUID(config["user_id"]), config["profile_id"], UUID(config["node_id"]),
                            "memory-only-token")
    spool = Outbox(config["spool"])
    spool.start()
    count = 0
    print("ready", flush=True)
    while True:
        spool.submit(owner, "drill", {"event_uuid": str(uuid4()), "deployment_id": 1,
                                      "padding": "x" * 2000}, capped=False)
        count += 1
        if count % 50 == 0:
            print(count, flush=True)
        time.sleep(0.001)


def upload(config):
    auth = CloudAuth(config["api_url"], config["publishable_key"], config["node_id"])
    original = auth._transport

    def routed(*args, **kwargs):
        repo = original(*args, **kwargs)
        repo.base_url = config["proxy_url"]
        return repo

    auth._transport = routed
    identity = auth.authenticate_access(config["token"])
    spool = Outbox(config["spool"])
    spool.start()
    worker = SyncWorker(auth, spool)
    print("ready", flush=True)
    while True:
        worker.retry.clear()
        worker.sync_once(identity)
        counts = spool.counts(identity)
        if not counts["pending"] and not counts["summaries_pending"]:
            print("done " + json.dumps({key: counts[key] for key in ("synced", "rejected")}),
                  flush=True)
            spool.close()
            return
        time.sleep(0.2)


if __name__ == "__main__":
    settings = json.loads(sys.argv[1])
    (write if settings["mode"] == "write" else upload)(settings)
