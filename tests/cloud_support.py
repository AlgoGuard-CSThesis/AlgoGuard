"""Shared helpers for the Stage 5D cloud-application tests (offline and integration)."""

from __future__ import annotations

import re
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from cloud_app import build_services, create_cloud_app
from model_runtime import compatibility_metadata
from tests.fake_supabase import FakeSupabase

ADMIN = ("admin@example.test", "Admin-Password-1!", "admin")
ANALYST = ("analyst@example.test", "Analyst-Password-1!", "analyst")
OTHER = ("other@example.test", "Other-Password-1!", "other")
CSRF = re.compile(r'name="_csrf_token" value="([^"]+)"')


def train_stacking_artifact(folder):
    """A real, current fifteen-feature Stacking artifact trained on a small sample."""
    from services.preprocessing_service import prepare_dataset
    from services.training_service import train_and_compare_models

    base = Path(__file__).resolve().parents[1]
    frame = pd.read_csv(base / "datasets" / "algoguard_big.csv")
    sample = pd.concat(
        [group.sample(150, random_state=7) for _, group in frame.groupby("label")]
    ).sample(frac=1, random_state=7)
    csv_path = folder / "sample.csv"
    sample.to_csv(csv_path, index=False)
    prepared = prepare_dataset(csv_path)
    result = train_and_compare_models(prepared, str(folder / "models"), 1)
    stacking = next(model for model in result["model_results"] if model["model_id"] == "stacking")
    return Path(stacking["model_path"]).read_bytes()


def make_cloud(tmp_path, artifact, *, approve=True, start=True):
    fake = FakeSupabase().start()
    fake.publish_model(artifact, compatibility_metadata())
    admin = fake.add_user(*ADMIN, role="administrator")
    analyst = fake.add_user(*ANALYST)
    other = fake.add_user(*OTHER)
    cfg = SimpleNamespace(
        state_dir=tmp_path / "state",
        supabase_url=fake.url,
        supabase_publishable_key=fake.publishable_key,
        secure_cookies=False,
    )
    services = build_services(cfg)
    app = create_cloud_app(cfg, services=services, start_workers=False)
    app.config["TESTING"] = True
    if start:
        services.sync.start()
    if approve:
        fake.approve(admin["profile_id"], services.node_id)
        fake.approve(analyst["profile_id"], services.node_id)
    return SimpleNamespace(fake=fake, app=app, services=services, cfg=cfg, admin=admin,
                           analyst=analyst, other=other, node=str(services.node_id))


def csrf(client, path="/login"):
    match = CSRF.search(client.get(path).get_data(as_text=True))
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def login(client, email, password, **extra):
    token = csrf(client)
    return client.post("/login", data={"email": email, "password": password,
                                       "_csrf_token": token, **extra})


def signed_in(app, account=ANALYST):
    client = app.test_client()
    response = login(client, account[0], account[1])
    assert response.status_code == 302, response.get_data(as_text=True)
    client.csrf = csrf(client, "/")
    return client


def post_json(client, path, body=None):
    return client.post(path, json=body or {}, headers={"X-CSRF-Token": client.csrf})


def wait_for(predicate, timeout=10.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not reached in time")


def monitor_until(client, predicate, timeout=15.0):
    def check():
        data = client.get("/monitor/status").get_json()
        return data if predicate(data) else None

    return wait_for(check, timeout)


def attack_rows(limit=40):
    """Labelled Attack flows from the bundled sample, as a browser form would send them."""
    import csv

    path = Path(__file__).resolve().parents[1] / "datasets" / "algoguard_big.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["label"] == "Attack"]
    return [{key: value for key, value in row.items() if key != "label"} for row in rows[:limit]]
