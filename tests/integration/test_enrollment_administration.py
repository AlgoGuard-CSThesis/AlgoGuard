import uuid

import pytest

pytestmark = pytest.mark.integration


def test_reassignment_rejection_and_account_deactivation(cloud):
    admin, _, analyst = cloud["users"]
    old, new = str(uuid.uuid4()), str(uuid.uuid4())
    for node in (old, new):
        assert (
            cloud["rpc"](
                "request_enrollment",
                {"p_node_id": node, "p_display_name": cloud["prefix"]},
                analyst,
            ).status_code
            == 200
        )
    assert (
        cloud["admin"](
            {
                "action": "membership",
                "node_id": old,
                "profile_id": analyst["profile"],
                "status": "approved",
                "is_default": True,
            }
        ).status_code
        == 200
    )
    assert (
        cloud["admin"](
            {
                "action": "reassign",
                "node_id": new,
                "from_node_id": old,
                "profile_id": analyst["profile"],
                "status": "approved",
                "is_default": True,
            }
        ).status_code
        == 200
    )
    assert cloud["sql"](
        "select status,is_default from public.node_membership where profile_id=%s and node_id=%s",
        (analyst["profile"], old),
    ) == [("revoked", False)]
    assert (
        cloud["admin"]({"action": "node_status", "node_id": new, "status": "rejected"}).status_code
        == 200
    )
    assert (
        cloud["rpc"](
            "request_enrollment", {"p_node_id": new, "p_display_name": cloud["prefix"]}, analyst
        ).json()["status"]
        == "rejected"
    )
    assert (
        cloud["admin"](
            {"action": "account_status", "profile_id": analyst["profile"], "is_active": False}
        ).status_code
        == 200
    )
    try:
        assert (
            cloud["rpc"](
                "request_enrollment", {"p_node_id": new, "p_display_name": cloud["prefix"]}, analyst
            ).status_code
            == 403
        )
    finally:
        cloud["sql"](
            "update public.profile set is_active=true where profile_id=%s", (analyst["profile"],)
        )
    assert (
        cloud["admin"](
            {"action": "role", "profile_id": admin["profile"], "role": "administrator"}
        ).status_code
        == 403
    )


def test_enrollment_repeats_and_membership_lifecycle(cloud):
    admin, analyst, _ = cloud["users"]
    nodes = [str(uuid.uuid4()), str(uuid.uuid4())]
    for node in nodes:
        body = {"p_node_id": node, "p_display_name": cloud["prefix"]}
        first = cloud["rpc"]("request_enrollment", body, analyst)
        assert first.status_code == 200, first.json()
        assert first.json()["status"] == "pending"
        assert cloud["rpc"]("request_enrollment", body, analyst).json() == first.json()
        assert cloud["sql"]("select count(*) from public.node where node_id=%s", (node,))[0][0] == 1
        approved = cloud["admin"](
            {
                "action": "membership",
                "node_id": node,
                "profile_id": analyst["profile"],
                "status": "approved",
                "is_default": True,
            }
        )
        assert approved.status_code == 200
    memberships = cloud["sql"](
        "select node_id::text,status,is_default from public.node_membership "
        "where profile_id=%s order by requested_at",
        (analyst["profile"],),
    )
    assert len(memberships) == 2
    assert sum(row[2] for row in memberships) == 1
    revoked = cloud["admin"](
        {
            "action": "membership",
            "node_id": nodes[0],
            "profile_id": analyst["profile"],
            "status": "revoked",
        }
    )
    assert revoked.status_code == 200
    assert (
        cloud["rpc"](
            "request_enrollment",
            {"p_node_id": nodes[0], "p_display_name": cloud["prefix"]},
            analyst,
        ).json()["status"]
        == "revoked"
    )


def test_edge_refuses_anonymous_analyst_and_demoted_admin(cloud):
    admin, analyst, _ = cloud["users"]
    path = "/functions/v1/account_admin"
    assert cloud["request"]("POST", path, json={}).status_code == 401
    assert cloud["request"]("POST", path, analyst, json={}).status_code == 403
    cloud["sql"]("delete from public.user_role where profile_id=%s", (admin["profile"],))
    try:
        assert cloud["request"]("POST", path, admin, json={}).status_code == 403
    finally:
        cloud["sql"](
            "insert into public.user_role(profile_id,role) values(%s,'administrator')",
            (admin["profile"],),
        )
    assert (
        cloud["rpc"](
            "administration_command",
            {"p_actor": admin["id"], "p_request_id": str(uuid.uuid4()), "p_body": {}},
            analyst,
        ).status_code
        == 403
    )


def test_create_retry_conflict_and_partial_auth_recovery(cloud, local_stack, local_http):
    admin = cloud["users"][0]
    for partial in (False, True):
        request_id = str(uuid.uuid4())
        username = cloud["prefix"] + ("_partial" if partial else "_created")
        body = {
            "action": "create_account",
            "email": username + "@algoguard.invalid",
            "username": username,
            "role": "analyst",
        }
        password = "Integration!" + uuid.uuid4().hex
        if partial:
            reserved = local_http.post(
                local_stack["rest_url"] + "/rpc/administration_command",
                headers=cloud["maintenance"],
                json={"p_actor": admin["id"], "p_request_id": request_id, "p_body": body},
            )
            assert reserved.status_code == 200
            created = local_http.post(
                local_stack["api_url"] + "/auth/v1/admin/users",
                headers=cloud["maintenance"],
                json={
                    "email": body["email"],
                    "password": password,
                    "email_confirm": True,
                    "app_metadata": {"algoguard_request_id": request_id},
                },
            )
            assert created.status_code in (200, 201)
        result = cloud["admin"]({**body, "password": password}, request_id=request_id)
        assert result.status_code == 200, (partial, result.json())
        assert (
            cloud["admin"]({**body, "password": password}, request_id=request_id).json()
            == result.json()
        )
        assert cloud["admin"]({**body, "password": password}).status_code == 409
        assert (
            cloud["admin"](
                {**body, "username": username + "other", "password": password},
                request_id=request_id,
            ).status_code
            == 409
        )
    assert cloud["admin"]({"action": "membership", "node_id": "bad"}).status_code == 400
