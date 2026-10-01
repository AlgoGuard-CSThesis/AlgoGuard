from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from node_enrollment import local_node_id, select_node


def test_parallel_startups_share_one_persistent_identity(tmp_path):
    path = tmp_path / "node-id"
    with ThreadPoolExecutor(max_workers=8) as pool:
        identities = list(pool.map(lambda _: local_node_id(path), range(16)))
    assert len(set(identities)) == 1
    assert local_node_id(path) == identities[0]
    path.write_text("corrupted", encoding="ascii")
    with pytest.raises(ValueError):
        local_node_id(path)
    assert path.read_text() == "corrupted"


def test_independent_installations_keep_distinct_stable_identities(tmp_path):
    first = local_node_id(tmp_path / "installation-a")
    second = local_node_id(tmp_path / "installation-b")

    assert first != second
    assert local_node_id(tmp_path / "installation-a") == first
    assert local_node_id(tmp_path / "installation-b") == second


def test_local_default_and_explicit_approved_alternative():
    local, other = uuid4(), uuid4()
    memberships = [
        dict(node_id=str(node), status="approved", node_status="approved")
        for node in (local, other)
    ]
    assert select_node(local, memberships) == local
    assert select_node(local, memberships, other) == other
    memberships[0]["status"] = "revoked"
    with pytest.raises(PermissionError):
        select_node(local, memberships)
