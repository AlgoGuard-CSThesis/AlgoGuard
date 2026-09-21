"""Offline regression coverage for destructive local Supabase smoke tests."""

from types import SimpleNamespace

import pytest
from stack_support import (
    LocalStackConfigError,
    LocalStackHttp,
    MissingLocalStackConfig,
    build_local_stack,
    cleanup_storage,
    connect_local_database,
    load_local_env,
    require_status,
)


@pytest.fixture
def local_values():
    return {
        "API_URL": "http://127.0.0.1:54321",
        "REST_URL": "http://127.0.0.1:54321/rest/v1",
        "FUNCTIONS_URL": "http://127.0.0.1:54321/functions/v1",
        "DB_URL": "postgresql://postgres:SYNTHETIC_DB_PASSWORD@127.0.0.1:54322/postgres",
        "PUBLISHABLE_KEY": "SYNTHETIC_PUBLISHABLE_KEY",
        "SECRET_KEY": "SYNTHETIC_SECRET_KEY",
        "ANON_KEY": "SYNTHETIC_ANON_KEY",
        "SERVICE_ROLE_KEY": "SYNTHETIC_SERVICE_ROLE_KEY",
    }


@pytest.mark.parametrize("field", ["API_URL", "REST_URL", "FUNCTIONS_URL", "STORAGE_S3_URL"])
@pytest.mark.parametrize(
    "url",
    [
        "https://cloud.example.invalid",
        "http://localhost.example.invalid:54321",
        "http://127.0.0.1.example.invalid:54321",
        "http://192.168.1.10:54321",
        "http://0.0.0.0:54321",
        "http://127.0.0.1:54321@cloud.example.invalid",
        "http://SYNTHETIC_USER:SYNTHETIC_PASSWORD@127.0.0.1:54321",
        "http://127.0.0.1:54321?apikey=SYNTHETIC_SECRET_KEY",
        "http://127.0.0.1:invalid",
        "http://[::1",
        "http://127.0.0.1:\n54321",
    ],
)
def test_rejects_unsafe_http_endpoints_without_exposing_values(local_values, field, url):
    local_values[field] = url
    with pytest.raises(LocalStackConfigError) as caught:
        build_local_stack(local_values, {})
    assert str(caught.value) == f"{field} must be a loopback-only local Supabase URL."
    assert "SYNTHETIC" not in str(caught.value)


@pytest.mark.parametrize(
    "db_url",
    [
        "postgresql://user:SYNTHETIC_PASSWORD@cloud.example.invalid/postgres",
        "postgresql://user:SYNTHETIC_PASSWORD@127.0.0.1,cloud.example.invalid/postgres",
        "postgresql:///postgres?host=cloud.example.invalid",
        "host=cloud.example.invalid password=SYNTHETIC_PASSWORD",
        "postgresql://127.0.0.1/postgres?host=cloud.example.invalid",
        "postgresql://127.0.0.1/postgres?hostaddr=198.51.100.1",
        "postgresql://127.0.0.1/postgres?service=production",
        "postgresql://127.0.0.1/postgres?%68ost=cloud.example.invalid",
        "postgresql://127.0.0.1/postgres?%68ostaddr=198.51.100.1",
        "postgresql://127.0.0.1/postgres?%73ervice=production",
        "postgresql://127.0.0.1/postgres?dbname=postgresql://cloud.example.invalid/db",
        "postgresql://127.0.0.1/postgres?sslmode=disable&host=cloud.example.invalid",
        "postgresql://127.0.0.1",
    ],
)
def test_rejects_remote_database_and_libpq_overrides_before_connecting(local_values, db_url):
    local_values["DB_URL"] = db_url
    with pytest.raises(LocalStackConfigError, match="DB_URL must be") as caught:
        build_local_stack(local_values, {})
    assert "SYNTHETIC" not in str(caught.value)

    calls = []
    with pytest.raises(LocalStackConfigError):
        connect_local_database(lambda *args, **kwargs: calls.append((args, kwargs)), db_url)
    assert calls == []


@pytest.mark.parametrize("host", ["127.0.0.1", "[::1]", "localhost"])
def test_loopback_hosts_accepted_and_localhost_pinned(local_values, host):
    local_values["API_URL"] = f"http://{host}:54321/"
    local_values["DB_URL"] = f"postgresql://user:password@{host}:54322/postgres?sslmode=disable"
    stack = build_local_stack(local_values, {})
    expected_host = "127.0.0.1" if host == "localhost" else host
    assert stack["api_url"] == f"http://{expected_host}:54321"
    assert f"@{expected_host}:54322" in stack["db_url"]


def test_existing_file_wins_as_one_complete_source(local_values):
    ambient = {key: "SYNTHETIC_AMBIENT_CLOUD_VALUE" for key in local_values}
    stack = build_local_stack(local_values, ambient)
    assert stack["secret_key"] == local_values["SECRET_KEY"]
    assert stack["api_url"] == local_values["API_URL"]
    assert stack["storage_s3_url"] == ""


@pytest.mark.parametrize("missing_key", ["API_URL", "SECRET_KEY"])
def test_incomplete_existing_file_never_falls_back_to_ambient(local_values, missing_key):
    incomplete = {key: value for key, value in local_values.items() if key != missing_key}
    with pytest.raises(MissingLocalStackConfig, match=missing_key):
        build_local_stack(incomplete, local_values)


def test_absent_file_uses_environment_and_empty_file_does_not(local_values):
    assert build_local_stack(None, local_values)["api_url"] == local_values["API_URL"]
    with pytest.raises(MissingLocalStackConfig):
        build_local_stack({}, local_values)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_env_file_accepts_supabase_status_encodings(tmp_path, encoding):
    path = tmp_path / "local.env"
    assert load_local_env(path) is None
    path.write_text(
        "# comment\nAPI_URL=\"http://127.0.0.1:54321\"\nSECRET_KEY='key'\n", encoding=encoding
    )
    assert load_local_env(path) == {"API_URL": "http://127.0.0.1:54321", "SECRET_KEY": "key"}


class StubSession:
    def __init__(self):
        self.trust_env = True
        self.proxies = {"https": "http://cloud.example.invalid:8080"}
        self.calls = []
        self.response = SimpleNamespace(
            status_code=302, headers={"Location": "https://cloud.example.invalid/collect"}
        )

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


def test_http_client_disables_proxies_and_redirects(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://cloud.example.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://cloud.example.invalid:8080")
    session = StubSession()
    http = LocalStackHttp(session)
    response = http.post(
        "http://localhost:54321/auth/v1/signup",
        headers={"apikey": "SYNTHETIC_SECRET_KEY"},
        allow_redirects=True,
        proxies={"http": "http://cloud.example.invalid:8080"},
    )
    assert response.status_code == 302
    assert session.trust_env is False
    assert session.proxies == {}
    assert len(session.calls) == 1
    _, url, kwargs = session.calls[0]
    assert url == "http://127.0.0.1:54321/auth/v1/signup"
    assert kwargs["allow_redirects"] is False
    assert kwargs["proxies"] == {}


def test_http_client_rejects_remote_request_before_session_is_called():
    session = StubSession()
    http = LocalStackHttp(session)
    with pytest.raises(LocalStackConfigError):
        http.get("https://cloud.example.invalid", headers={"apikey": "SYNTHETIC_SECRET_KEY"})
    assert session.calls == []


def test_http_transport_failure_does_not_echo_credentials():
    session = StubSession()

    def fail(*args, **kwargs):
        raise OSError("SYNTHETIC_SECRET_KEY")

    session.request = fail
    with pytest.raises(RuntimeError) as caught:
        LocalStackHttp(session).get("http://127.0.0.1:54321")
    assert str(caught.value) == "Local Supabase HTTP request failed."
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_database_connection_pins_both_host_and_hostaddr(monkeypatch, host):
    for key in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGPORT"):
        monkeypatch.setenv(key, "SYNTHETIC_REMOTE_OVERRIDE")
    calls = []

    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        return "connection"

    assert connect_local_database(connect, f"postgresql://{host}:54322/postgres") == "connection"
    _, kwargs = calls[0]
    expected_host = "::1" if host == "[::1]" else "127.0.0.1"
    assert kwargs["host"] == kwargs["hostaddr"] == expected_host
    assert kwargs["port"] == 54322
    assert kwargs["connect_timeout"] == 10


def test_database_connection_failure_does_not_echo_credentials(local_values):
    def fail(*args, **kwargs):
        raise OSError("SYNTHETIC_DB_PASSWORD")

    with pytest.raises(RuntimeError) as caught:
        connect_local_database(fail, local_values["DB_URL"])
    assert str(caught.value) == "Could not connect to the local smoke-test database."
    assert caught.value.__suppress_context__


def test_status_error_does_not_echo_response_body():
    response = SimpleNamespace(status_code=403, text="SYNTHETIC_SECRET_KEY")
    with pytest.raises(AssertionError, match="Auth signup failed with HTTP 403") as caught:
        require_status(response, (200, 201), "Auth signup")
    assert "SYNTHETIC" not in str(caught.value)


class StubCleanupHttp:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def delete(self, url, **kwargs):
        self.calls.append((url, kwargs))
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(status_code=result, text="SYNTHETIC_SECRET_KEY")


@pytest.mark.parametrize(
    "results, errors",
    [
        ([400, 200], ["object: HTTP 400"]),
        ([200, 400], ["bucket: HTTP 400"]),
        ([400, 500], ["object: HTTP 400", "bucket: HTTP 500"]),
        ([OSError("SYNTHETIC_SECRET_KEY"), 200], ["object: request failed"]),
        (
            [OSError("SYNTHETIC_SECRET_KEY"), OSError("SYNTHETIC_SECRET_KEY")],
            ["object: request failed", "bucket: request failed"],
        ),
    ],
)
def test_storage_cleanup_attempts_both_deletions_and_reports_failures(results, errors):
    http = StubCleanupHttp(results)
    with pytest.raises(AssertionError) as caught:
        cleanup_storage(http, "http://127.0.0.1:54321", "bucket", "smoke.txt", {})
    assert len(http.calls) == 2
    for error in errors:
        assert error in str(caught.value)
    assert "SYNTHETIC" not in str(caught.value)


def test_storage_cleanup_uses_bodyless_deletes_without_json_content_type():
    http = StubCleanupHttp([200, 204])
    headers = {"Content-Type": "application/json", "apikey": "SYNTHETIC_SECRET_KEY"}
    cleanup_storage(http, "http://127.0.0.1:54321", "bucket", "smoke.txt", headers)
    assert [url for url, _ in http.calls] == [
        "http://127.0.0.1:54321/storage/v1/object/bucket/smoke.txt",
        "http://127.0.0.1:54321/storage/v1/bucket/bucket",
    ]
    for _, kwargs in http.calls:
        assert kwargs == {"headers": {"apikey": "SYNTHETIC_SECRET_KEY"}, "timeout": 10}
    assert headers["Content-Type"] == "application/json"
