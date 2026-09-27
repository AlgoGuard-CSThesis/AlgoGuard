"""Windows evidence: a separate maintenance process and unrelated database traffic."""

import os
import socket
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.integration


def test_separate_maintenance_process_is_excluded(local_stack, tmp_path, monkeypatch):
    from config import get_config, reset_config_cache
    from maintenance_connections import SharedConnections
    from services.traffic_source_service import LiveCaptureSource

    if os.environ.get("RUN_WINDOWS_CAPTURE_TESTS") != "1":
        pytest.skip("Set RUN_WINDOWS_CAPTURE_TESTS=1 on Windows with Npcap.")
    monkeypatch.setenv("ALGOGUARD_STATE_DIR", str(tmp_path / "state"))
    reset_config_cache()
    source = LiveCaptureSource(r"\Device\NPF_Loopback")
    source.prepare()
    control = socket.socket()
    flows = []
    registry = SharedConnections(get_config().state_dir)
    try:
        child = (
            "import os; from maintenance_connections import connect_database; "
            "\nfor i in range(2):\n"
            " c=connect_database(os.environ['TEST_DATABASE_URL'],sslmode='prefer',"
            "connect_timeout=10)\n"
            " with c.cursor() as q:\n  q.execute('select 1')\n  assert q.fetchone()==(1,)\n"
            " c.close()\n")
        result = subprocess.run([sys.executable, "-c", child],
                                env={**os.environ, "TEST_DATABASE_URL": local_stack["db_url"]},
                                capture_output=True, timeout=30)
        assert result.returncode == 0, "Maintenance child failed; no credentials are logged."
        # Local-stack address is validated by its fixture; plain PostgreSQL SSLRequest.
        from urllib.parse import urlsplit
        target = urlsplit(local_stack["db_url"])
        control.connect((target.hostname, target.port))
        control_port = control.getsockname()[1]
        control.sendall(bytes.fromhex("0000000804d2162f"))
        control.recv(1)
        control.close()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            event = source.next_event(timeout=0.2)
            if event is not None:
                flows.append(event)
        import sqlite3
        with sqlite3.connect(registry.path) as conn:
            owned_ports = {row[0] for row in conn.execute(
                "select local_port from lease where remote_port=?", (target.port,))}
        assert len(owned_ports) == 2
        assert not any(row.get("source_port") in owned_ports or row.get("destination_port")
                       in owned_ports for row in flows)
        assert any(control_port in (row.get("source_port"), row.get("destination_port"))
                   for row in flows)
        assert source.stats()["excluded_cloud"] > 0
        print("Maintenance reconnect evidence:", source.stats(),
              "owned connections:", len(owned_ports), "unrelated connection visible: true")
    finally:
        control.close()
        source.close()
        registry.close()
