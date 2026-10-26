"""Stage 6B.2 against the local stack: node health needs membership and records server time."""

import pytest

pytestmark = pytest.mark.integration

HEALTH = {"app_version": "0.6.0+test", "deployment_id": None, "capture_status": "running",
          "pending_events": 3, "dropped_events": 0, "rejected_events": 1}


def report(cloud, actor, node, health=HEALTH):
    return cloud["rpc"]("report_node_health", {"p_node_id": node, "p_health": health}, actor)


def test_a_member_reports_health_stamped_with_server_time(cloud, scope):
    analyst = cloud["users"][1]
    response = report(cloud, analyst, scope["nodes"][0])
    assert response.status_code == 200, response.text
    row = cloud["sql"](
        "select reported_by, capture_status, pending_events, rejected_events, "
        "abs(extract(epoch from received_at - now())) < 60, "
        "(select last_seen_at from public.node where node_id=%s) = received_at "
        "from public.node_health where node_id=%s",
        (scope["nodes"][0], scope["nodes"][0]),
    )[0]
    assert row == (analyst["profile"], "running", 3, 1, True, True)


def test_reports_need_an_approved_membership_and_a_valid_body(cloud, scope):
    analyst_on_other_node = cloud["users"][2]
    assert report(cloud, analyst_on_other_node, scope["nodes"][0]).status_code == 403
    member = cloud["users"][1]
    for body in ({**HEALTH, "remote_command": "stop"}, {**HEALTH, "capture_status": "rebooting"},
                 {**HEALTH, "pending_events": -1}):
        assert report(cloud, member, scope["nodes"][0], body).status_code == 400
    anonymous = cloud["request"]("POST", "/rest/v1/rpc/report_node_health", None,
                                 json={"p_node_id": scope["nodes"][0], "p_health": HEALTH})
    assert anonymous.status_code in (401, 403)


def test_administrators_read_every_node_and_analysts_only_their_own(cloud, scope):
    report(cloud, cloud["users"][1], scope["nodes"][0])
    report(cloud, cloud["users"][2], scope["nodes"][1])

    def visible(actor):
        rows = cloud["request"]("GET", "/rest/v1/node_health", actor,
                                params={"select": "node_id"}).json()
        return {row["node_id"] for row in rows} & set(scope["nodes"])

    assert visible(cloud["users"][0]) == set(scope["nodes"])
    assert visible(cloud["users"][1]) == {scope["nodes"][0]}
    # Nobody writes the table directly: the server's receipt time cannot be forged.
    forged = cloud["request"]("POST", "/rest/v1/node_health", cloud["users"][1],
                              json={"node_id": scope["nodes"][0], "reported_by": 1,
                                    "received_at": "2099-01-01T00:00:00Z", **HEALTH})
    assert forged.status_code in (401, 403)
