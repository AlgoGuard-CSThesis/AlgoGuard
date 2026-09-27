import io
import urllib.error
from email.message import Message
from typing import Any
from uuid import uuid4

import pytest

from cloud_repository import CloudRepository, RepositoryError, UserNodeContext


def repo():
    return CloudRepository(
        "https://example.supabase.co",
        "sb_publishable_test",
        UserNodeContext(uuid4(), 1, uuid4(), "private-token"),
    )


def test_context_redacts_token_and_rejects_privileged_credentials():
    client = repo()
    assert "private-token" not in repr(client.context)
    for url, key in (
        ("http://remote.example", "sb_publishable_x"),
        ("https://example.supabase.co", "sb_secret_x"),
        ("https://user:password@example.com", "sb_publishable_x"),
    ):
        with pytest.raises(RepositoryError):
            CloudRepository(url, key, client.context)


@pytest.mark.parametrize(
    "status,category",
    [
        (400, "validation"),
        (401, "authentication"),
        (403, "permission"),
        (404, "not_found"),
        (409, "conflict"),
        (429, "transient"),
        (503, "transient"),
    ],
)
def test_errors_are_stable_and_do_not_echo_server_details(status, category, monkeypatch):
    client = repo()

    class Opener:
        def open(self, request, timeout):
            assert timeout == 10
            raise urllib.error.HTTPError(
                request.full_url, status, "SECRET DETAILS", Message(), io.BytesIO(b'"HIDDEN ROW"')
            )

    monkeypatch.setattr(client, "_opener", Opener())
    with pytest.raises(RepositoryError) as caught:
        client.statistics()
    assert caught.value.category == category
    assert caught.value.retryable == (category == "transient")
    assert "SECRET" not in str(caught.value) and "HIDDEN" not in str(caught.value)


def test_input_limits_do_not_send_requests(monkeypatch):
    client = repo()

    def forbidden(*args, **kwargs):
        pytest.fail("invalid input reached network")

    monkeypatch.setattr(client, "_request", forbidden)
    for events in (
        [],
        [{}] * 51,
        [{"event_uuid": "invalid"}],
        [{"event_uuid": str(uuid4()), "value": float("nan")}],
    ):
        with pytest.raises(RepositoryError):
            client.store_flows(events)
    cases: tuple[tuple[str, dict[str, Any]], ...] = (
        ("private", {}),
        ("alert", {"page_size": 101}),
        ("alert", {"offset": -1}),
        ("alert", {"filters": {"or": "forged"}}),
    )
    for resource, options in cases:
        with pytest.raises(RepositoryError):
            client.list_records(resource, **options)


def test_uncommitted_or_mismatched_acknowledgement_is_not_accepted(monkeypatch):
    client = repo()
    payload = {"event_uuid": str(uuid4())}
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: [{"event_uuid": str(uuid4()), "persistence": "queued"}],
    )
    with pytest.raises(RepositoryError) as caught:
        client.store_flows([payload])
    assert caught.value.category == "protocol"
