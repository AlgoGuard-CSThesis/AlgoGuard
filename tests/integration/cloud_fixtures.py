"""Disposable real users; maintenance is used only for setup/cleanup, never API impersonation."""

import uuid

import pytest
from stack_support import connect_local_database, require_status


@pytest.fixture(scope="module")
def cloud(local_stack, local_http):
    import psycopg2

    db = connect_local_database(psycopg2.connect, local_stack["db_url"])
    users = []
    prefix = "zz_migration_smoke_" + uuid.uuid4().hex[:10]
    maintenance = {
        "apikey": local_stack["secret_key"],
        "Authorization": "Bearer " + local_stack["secret_key"],
    }

    def request(method, path, actor=None, **kwargs):
        headers = {"apikey": local_stack["publishable_key"], "Prefer": "return=representation"}
        if actor:
            headers["Authorization"] = "Bearer " + actor["token"]
        return local_http.request(method, local_stack["api_url"] + path, headers=headers, **kwargs)

    def rpc(name, body, actor):
        return request("POST", "/rest/v1/rpc/" + name, actor, json=body)

    def admin(body, actor=None, request_id=None):
        return request(
            "POST",
            "/functions/v1/account_admin",
            actor or users[0],
            json={"request_id": request_id or str(uuid.uuid4()), **body},
        )

    def sql(statement, parameters=()):
        with db:
            with db.cursor() as cursor:
                cursor.execute(statement, parameters)
                return cursor.fetchall() if cursor.description else []

    try:
        for index, role in enumerate(("administrator", "analyst", "analyst")):
            email = f"{prefix}_{index}@algoguard.invalid"
            password = "Integration!" + uuid.uuid4().hex
            created = local_http.post(
                local_stack["api_url"] + "/auth/v1/admin/users",
                headers=maintenance,
                json={"email": email, "password": password, "email_confirm": True},
            )
            require_status(created, (200, 201), "fixture account")
            user = {"id": created.json()["id"]}
            users.append(user)
            signed = local_http.post(
                local_stack["api_url"] + "/auth/v1/token",
                headers={"apikey": local_stack["publishable_key"]},
                params={"grant_type": "password"},
                json={"email": email, "password": password},
            )
            require_status(signed, (200,), "fixture login")
            user["token"] = signed.json()["access_token"]
            user["profile"] = sql(
                "insert into public.profile(auth_user_id,username,email) values(%s,%s,%s) "
                "returning profile_id",
                (user["id"], f"{prefix}_{index}", email),
            )[0][0]
            sql(
                "insert into public.user_role(profile_id,role) values(%s,%s)",
                (user["profile"], role),
            )
        yield dict(
            users=users,
            request=request,
            rpc=rpc,
            admin=admin,
            sql=sql,
            maintenance=maintenance,
            prefix=prefix,
        )
    finally:
        # Prefix-limited cleanup, including accounts created by administration tests.
        profiles = sql(
            "select profile_id,auth_user_id from public.profile where username like %s",
            (prefix + "%",),
        )
        ids = [row[0] for row in profiles]
        if ids:
            sql("delete from public.ingest_event where owner_profile_id=any(%s)", (ids,))
            sql(
                "delete from public.report_alert where report_id in "
                "(select report_id from public.report where owner_profile_id=any(%s))",
                (ids,),
            )
            sql("delete from public.report where owner_profile_id=any(%s)", (ids,))
            sql("delete from public.system_log where profile_id=any(%s)", (ids,))
            for table in ("alert", "prediction", "network_traffic", "capture_session"):
                sql(f"delete from public.{table} where owner_profile_id=any(%s)", (ids,))
            sql("delete from public.model_manifest where created_by=any(%s)", (ids,))
            sql("delete from public.model_deployment where deployed_by=any(%s)", (ids,))
            sql(
                "delete from public.detection_model where run_id in "
                "(select run_id from public.training_run where created_by=any(%s))",
                (ids,),
            )
            sql("delete from public.training_run where created_by=any(%s)", (ids,))
            sql("delete from private.administration_request where actor_profile_id=any(%s)", (ids,))
            sql("delete from public.node_membership where profile_id=any(%s)", (ids,))
            sql("delete from public.node where display_name like %s", (prefix + "%",))
            sql("delete from public.user_role where profile_id=any(%s)", (ids,))
            sql("delete from public.profile where profile_id=any(%s)", (ids,))
        for auth_id in {row[1] for row in profiles} | {user["id"] for user in users}:
            local_http.delete(
                local_stack["api_url"] + "/auth/v1/admin/users/" + auth_id, headers=maintenance
            )
        db.close()


@pytest.fixture(scope="module")
def scope(cloud):
    admin, a, b = cloud["users"]
    sql = cloud["sql"]
    nodes = [str(uuid.uuid4()), str(uuid.uuid4())]
    for actor, node in zip((a, b), nodes):
        sql(
            "insert into public.node(node_id,display_name,status,approved_at,approved_by) "
            "values(%s,%s,'approved',now(),%s)",
            (node, cloud["prefix"], admin["profile"]),
        )
        sql(
            "insert into public.node_membership(node_id,profile_id,status,decided_at,decided_by) "
            "values(%s,%s,'approved',now(),%s)",
            (node, actor["profile"], admin["profile"]),
        )
    sql(
        "insert into public.node_membership(node_id,profile_id,status,decided_at,decided_by) "
        "values(%s,%s,'approved',now(),%s)",
        (nodes[0], admin["profile"], admin["profile"]),
    )
    run = sql(
        "insert into public.training_run(created_by,filename,upload_timestamp,stored_filename) "
        "values(%s,%s,'2026-09-26 00:00:00','PRIVATE_TRAINING_PATH') returning run_id",
        (admin["profile"], cloud["prefix"]),
    )[0][0]
    model = sql(
        "insert into public.detection_model(run_id,model_name,artifact_path) "
        "values(%s,'fixture','PRIVATE_MODEL_PATH') returning model_id",
        (run,),
    )[0][0]
    deployment = sql(
        "insert into public.model_deployment(model_id,run_id,deployed_by,artifact_path,"
        "deployed_at,is_active) values(%s,%s,%s,'PRIVATE_DEPLOY_PATH',"
        "'2026-09-26 00:00:00',0) returning deployment_id",
        (model, run, admin["profile"]),
    )[0][0]
    return dict(nodes=nodes, model=model, deployment=deployment, run=run)
