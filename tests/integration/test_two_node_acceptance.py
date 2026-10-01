"""Local two-client concurrency smoke on the real Auth, Data API and Storage."""

import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest

from cloud_repository import CloudRepository, RepositoryError, UserNodeContext
from model_delivery import ModelCache

from . import test_cloud_app_stack
from .test_atomic_flows import event

pytestmark = pytest.mark.integration
published = test_cloud_app_stack.published


def test_two_independent_nodes_download_and_write_concurrently(
    cloud, local_stack, scope, published, tmp_path
):
    repositories = []
    caches = []
    for actor_index, node_id in zip((1, 2), scope["nodes"]):
        actor = cloud["users"][actor_index]
        repository = CloudRepository(
            local_stack["api_url"],
            local_stack["publishable_key"],
            UserNodeContext(UUID(actor["id"]), actor["profile"], UUID(node_id), actor["token"]),
        )
        repositories.append(repository)
        cache = ModelCache(repository, tmp_path / str(node_id) / "models")
        assert not cache.directory.exists()
        model = cache.load_active()
        assert model.cache_path.is_file()
        assert model.cache_path.stat().st_size == model.manifest["object_bytes"]
        caches.append(cache)
    assert repositories[0].context.node_id != repositories[1].context.node_id
    assert caches[0].directory != caches[1].directory
    assert (
        caches[0].load_active().manifest["object_sha256"]
        == caches[1].load_active().manifest["object_sha256"]
    )

    def submit_batches(repository):
        latencies = []
        receipts = []
        for _ in range(3):
            batch = [
                event({"model": published["model_id"], "deployment": published["deployment_id"]})
                for _ in range(10)
            ]
            started = time.perf_counter()
            committed = repository.store_flows(batch)
            latencies.append((time.perf_counter() - started) * 1000)
            assert len(committed) == len(batch)
            assert not any(row.replayed for row in committed)
            receipts.extend(committed)
        return receipts, latencies

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        completed = list(pool.map(submit_batches, repositories))
    elapsed = time.perf_counter() - started
    all_receipts = [receipt for receipts, _ in completed for receipt in receipts]
    all_latencies = [latency for _, values in completed for latency in values]
    assert len(all_receipts) == 60

    event_ids = [str(receipt.event_uuid) for receipt in all_receipts]
    stored = cloud["sql"](
        "select node_id::text,owner_profile_id,count(*) from public.ingest_event "
        "where event_uuid=any(%s::uuid[]) group by node_id,owner_profile_id",
        (event_ids,),
    )
    assert {(row[0], row[1], row[2]) for row in stored} == {
        (str(repository.context.node_id), repository.context.profile_id, 30)
        for repository in repositories
    }

    # A still-valid token loses access as soon as its approved membership is revoked.
    revoked = repositories[1]
    try:
        cloud["sql"](
            "update public.node_membership set status='revoked',is_default=false "
            "where node_id=%s and profile_id=%s",
            (str(revoked.context.node_id), revoked.context.profile_id),
        )
        assert not revoked.node_access()
        with pytest.raises(RepositoryError, match="permission"):
            revoked.store_flows(
                [event({"model": published["model_id"], "deployment": published["deployment_id"]})]
            )
        admin = cloud["users"][0]
        for repository in repositories:
            response = cloud["request"](
                "GET",
                "/rest/v1/network_traffic",
                admin,
                params={"node_id": "eq." + str(repository.context.node_id)},
            )
            assert response.status_code == 200 and len(response.json()) == 30
    finally:
        cloud["sql"](
            "update public.node_membership set status='approved' "
            "where node_id=%s and profile_id=%s",
            (str(revoked.context.node_id), revoked.context.profile_id),
        )

    print(
        json.dumps(
            {
                "scope": "local two-client smoke; not a hardware benchmark",
                "clients": 2,
                "events": len(all_receipts),
                "concurrent_seconds": round(elapsed, 3),
                "acknowledged_events_per_second": round(len(all_receipts) / elapsed, 2),
                "request_rtt_ms_p50": round(statistics.median(all_latencies), 2),
                "request_rtt_ms_p95_nearest_rank": round(sorted(all_latencies)[-1], 2),
            },
            sort_keys=True,
        )
    )
