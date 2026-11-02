"""Stage 6C helpers: real accounts with passwords, a published model, and cloud apps per folder.

Maintenance SQL creates fixtures and observes results only; every application action uses a real
Auth user's token through the Data API. Names carry the module's fixture prefix, so the ``cloud``
fixture's cleanup removes them.
"""

import uuid
from types import SimpleNamespace

import pytest
from stack_support import connect_local_database, require_status

from cloud_app import build_services, create_cloud_app
from tests.cloud_support import csrf


def make_account(cloud, local_stack, local_http, label, role="analyst"):
    email = f"{cloud['prefix']}_{label}@algoguard.invalid"
    password = "Lifecycle!Aa1" + uuid.uuid4().hex
    created = local_http.post(
        local_stack["api_url"] + "/auth/v1/admin/users",
        headers=cloud["maintenance"],
        json={"email": email, "password": password, "email_confirm": True},
    )
    require_status(created, (200, 201), "fixture account")
    profile = cloud["sql"](
        "insert into public.profile(auth_user_id,username,email) values(%s,%s,%s) "
        "returning profile_id",
        (created.json()["id"], f"{cloud['prefix']}_{label}", email),
    )[0][0]
    cloud["sql"]("insert into public.user_role(profile_id,role) values(%s,%s)", (profile, role))
    return SimpleNamespace(email=email, password=password, profile=profile,
                           user_id=created.json()["id"], label=label)


def publish_fixture_model(cloud, local_stack, folder, artifact_bytes):
    """Publish a real Stacking artifact through the maintainer path; returns its manifest."""
    import psycopg2

    from publish_model import publish

    path = folder / f"stacking-{uuid.uuid4().hex[:8]}.joblib"
    path.write_bytes(artifact_bytes)
    model = dict(model_name="Stacking Ensemble", version="stacking-five-v3",
                 run_training_status="completed", evaluation_status="completed",
                 accuracy=95.0, f1_score=95.0, roc_auc=95.0)
    cloud["sql"]("NOTIFY pgrst, 'reload schema'")
    connection = connect_local_database(psycopg2.connect, local_stack["db_url"])
    try:
        manifest = publish(connection, local_stack["api_url"], local_stack["secret_key"],
                           uuid.uuid4(), model, path, cloud["users"][0]["profile"])
    finally:
        connection.close()
    assert manifest["status"] == "active"
    return manifest


@pytest.fixture(scope="module")
def published(cloud, local_stack, local_http, tmp_path_factory, stacking_artifact_bytes):
    manifest = publish_fixture_model(cloud, local_stack, tmp_path_factory.mktemp("artifact"),
                                     stacking_artifact_bytes)
    yield manifest
    local_http.delete(local_stack["api_url"] + "/storage/v1/object/models/"
                      + manifest["object_path"], headers=cloud["maintenance"])


def start_app(cloud, local_stack, state_dir, members, *, start_sync=True):
    """A cloud app on its own state folder (its own computer), with these approved members."""
    cfg = SimpleNamespace(state_dir=state_dir, supabase_url=local_stack["api_url"],
                          supabase_publishable_key=local_stack["publishable_key"],
                          secure_cookies=False)
    services = build_services(cfg)
    node = str(services.node_id)
    admin_profile = cloud["users"][0]["profile"]
    cloud["sql"](
        "insert into public.node(node_id,display_name,status,approved_at,approved_by) "
        "values(%s,%s,'approved',now(),%s) on conflict do nothing",
        (node, cloud["prefix"], admin_profile),
    )
    for account in members:
        cloud["sql"]("update public.node_membership set is_default=false where profile_id=%s",
                     (account.profile,))
        cloud["sql"](
            "insert into public.node_membership(node_id,profile_id,status,is_default,decided_at,"
            "decided_by) values(%s,%s,'approved',true,now(),%s) on conflict do nothing",
            (node, account.profile, admin_profile),
        )
    app = create_cloud_app(cfg, services=services, start_workers=False)
    app.config["TESTING"] = True
    if start_sync:
        services.sync.start()
    else:
        services.outbox.start()  # events still become durable; they just are not uploaded
    return SimpleNamespace(app=app, services=services, node=node, cfg=cfg)


def sign_in(app, account):
    client = app.test_client()
    response = client.post("/login", data={
        "email": account.email, "password": account.password, "_csrf_token": csrf(client)})
    assert response.status_code == 302, response.get_data(as_text=True)[:1000]
    client.csrf = csrf(client, "/")
    return client


def identity_for(app, account):
    for item in list(app.session_interface.sessions.values()):
        if item.identity is not None and str(item.identity.user_id) == account.user_id:
            return item.identity
    raise AssertionError(f"{account.label} is not signed in")
