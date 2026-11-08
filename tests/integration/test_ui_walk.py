"""Stage 6D.4: every page and traffic source in a real browser, at 1366 px and 390 px.

Opt-in, because it needs Microsoft Edge and a Python environment with Playwright:

    set RUN_UI_WALK=1
    set UI_WALK_PYTHON=<python.exe of an environment with playwright installed>
    set UI_WALK_OUTPUT=test-results\\iter6_6d\\ui   (optional: report and screenshots)
    pytest -m integration tests\\integration\\test_ui_walk.py

A cloud app with an approved installation for an analyst and an administrator is served on
127.0.0.1 and walked by ui_walk_driver.py. The walk passes only with no console or page
errors, no failed or unexpected error responses, and no page wider than the screen.
"""

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

import pytest
from werkzeug.serving import make_server

import config
from tests.integration.app_fixtures import make_account, publish_fixture_model, start_app
from tests.test_traffic_sources import write_sample_pcap

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_UI_WALK") != "1" or not os.environ.get(
        "UI_WALK_PYTHON"), reason="set RUN_UI_WALK=1 and UI_WALK_PYTHON to walk the pages"),
]
PORT = 5078


def test_every_page_and_source_at_desktop_and_phone_widths(
        cloud, local_stack, local_http, tmp_path, stacking_artifact_bytes):
    analyst = make_account(cloud, local_stack, local_http, "ui_analyst")
    admin = make_account(cloud, local_stack, local_http, "ui_admin", role="administrator")
    model_path = os.environ.get("CLEAN_INSTALL_MODEL")
    artifact = Path(model_path).read_bytes() if model_path else stacking_artifact_bytes
    manifest = publish_fixture_model(cloud, local_stack, tmp_path, artifact)
    captures = config.get_config().capture_folder
    captures.mkdir(parents=True, exist_ok=True)
    write_sample_pcap(captures / "ui-walk.pcap", sessions=10)
    running = start_app(cloud, local_stack, tmp_path / "state", [analyst, admin])
    running.app.config["TESTING"] = False  # serve exactly as an installation does
    server = make_server("127.0.0.1", PORT, running.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output = Path(os.environ.get("UI_WALK_OUTPUT") or tmp_path / "ui")
    settings = {
        "base": f"http://127.0.0.1:{PORT}", "output": str(output), "pcap": "ui-walk.pcap",
        "analyst": {"email": analyst.email, "password": analyst.password},
        "admin": {"email": admin.email, "password": admin.password},
        "new_account": {"email": f"{cloud['prefix']}_ui_new@algoguard.invalid",
                        "username": f"{cloud['prefix']}_ui_new",
                        "password": "Walk!Aa1" + uuid.uuid4().hex},
    }
    settings_file = tmp_path / "walk.json"
    settings_file.write_text(json.dumps(settings), encoding="utf-8")
    try:
        walked = subprocess.run(
            [os.environ["UI_WALK_PYTHON"], str(Path(__file__).with_name("ui_walk_driver.py")),
             str(settings_file)], capture_output=True, text=True, timeout=1800)
        assert walked.returncode == 0, walked.stderr[-4000:]
        result = json.loads(walked.stdout.strip().splitlines()[-1])
    finally:
        server.shutdown()
        running.services.close()
        local_http.delete(local_stack["api_url"] + "/storage/v1/object/models/"
                          + manifest["object_path"], headers=cloud["maintenance"])
    # One pixel is sub-pixel rounding of fractional widths, not a scrollable page.
    overflowing = [item for item in result["pages"] if item["overflow_px"] > 1]
    summary = {
        "pages_visited": len(result["pages"]),
        "distinct_pages": sorted({(item["role"], item["page"]) for item in result["pages"]}),
        "problems": result["problems"], "overflowing": overflowing,
        "sessions": result["sessions"], "pages": result["pages"],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "ui_walk.json").write_text(json.dumps(summary, indent=2, default=list) + "\n",
                                         encoding="utf-8")
    assert not result["problems"], result["problems"]
    assert not overflowing, overflowing
    for item in result["sessions"]:
        assert "error" not in item["state"].lower() and item["state"] != "not started", item
        if item["source"] != "live":
            assert item["feed_rows"] > 0 and item["score_band_shown"], item
