from concurrent.futures import ThreadPoolExecutor

import pytest

from node_enrollment import local_node_id


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
