"""Safety checks shared by local-stack smoke tests and their offline tests.

This module deliberately has no optional maintainer dependencies, so the guard
and cleanup failure paths are exercised by the default unit-test lane.
"""

from collections.abc import Mapping
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit


class LocalStackConfigError(ValueError):
    """Unsafe local-stack configuration; messages must never contain values."""


class MissingLocalStackConfig(ValueError):
    """The selected configuration source is incomplete."""


def load_local_env(path: Path) -> dict[str, str] | None:
    """Return None only when absent; an existing file is authoritative."""
    if not path.exists():
        return None
    try:
        raw_text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raw_text = path.read_text(encoding="utf-16")
    values = {}
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _local_url(value: str, field: str, *, database: bool = False) -> str:
    """Accept literal loopback addresses only, with localhost pinned to IPv4.

    For database URIs, a small query allowlist excludes libpq's host, hostaddr,
    service, and dbname overrides (including their percent-encoded forms).
    """
    invalid = f"{field} must be a loopback-only local Supabase URL."
    try:
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError
        parts = urlsplit(value)
        schemes = {"postgres", "postgresql"} if database else {"http", "https"}
        if parts.scheme not in schemes or not parts.hostname or parts.fragment:
            raise ValueError
        host = parts.hostname
        if host.lower() == "localhost":
            host = "127.0.0.1"
        address = ip_address(host)
        if not address.is_loopback or "%" in host:
            raise ValueError
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        if database:
            if not parts.path or parts.path == "/":
                raise ValueError
            permitted = {"sslmode", "connect_timeout", "application_name"}
            if any(key not in permitted for key, _ in parse_qsl(parts.query, strict_parsing=True)):
                raise ValueError
        elif parts.username is not None or parts.password is not None or parts.query:
            raise ValueError
        authority = f"[{address}]" if address.version == 6 else str(address)
        if port is not None:
            authority += f":{port}"
        if database and "@" in parts.netloc:
            authority = parts.netloc.rsplit("@", 1)[0] + "@" + authority
        return urlunsplit((parts.scheme, authority, parts.path.rstrip("/"), parts.query, ""))
    except (ValueError, TypeError):
        raise LocalStackConfigError(invalid) from None


def build_local_stack(
    file_values: Mapping[str, str] | None, environment: Mapping[str, str]
) -> dict[str, str]:
    """Use the whole env file if present, otherwise the process environment.

    Never fill gaps in a local file with possibly cloud-targeting ambient values.
    Validate every supplied endpoint before exposing any credentials to callers.
    """
    source = file_values if file_values is not None else environment
    urls = ("API_URL", "REST_URL", "FUNCTIONS_URL", "DB_URL", "STORAGE_S3_URL")
    values = {}
    for key in urls:
        if source.get(key):
            values[key.lower()] = _local_url(source[key], key, database=key == "DB_URL")
    keys = ("PUBLISHABLE_KEY", "SECRET_KEY", "ANON_KEY", "SERVICE_ROLE_KEY")
    for key in (*urls[:-1], *keys):
        if not source.get(key):
            origin = ".env.supabase.local" if file_values is not None else "the process environment"
            raise MissingLocalStackConfig(
                f"{key} is missing from {origin}. Run: supabase status -o env > .env.supabase.local"
            )
    values.update({key.lower(): source[key] for key in keys})
    values.setdefault("storage_s3_url", "")
    return values


class LocalStackHttp:
    """HTTP client that never forwards local credentials to proxies/redirects."""

    def __init__(self, session):
        self.session = session
        session.trust_env = False
        session.proxies.clear()

    def request(self, method, url, **kwargs):
        url = _local_url(url, "HTTP endpoint")
        kwargs["allow_redirects"] = False
        kwargs["proxies"] = {}
        kwargs.setdefault("timeout", 10)
        try:
            return self.session.request(method, url, **kwargs)
        except Exception:
            # Requests exception messages can contain request or proxy details.
            raise RuntimeError("Local Supabase HTTP request failed.") from None

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)


def connect_local_database(connect, db_url):
    """Pin libpq's destination even when PGHOSTADDR/PGPORT/PGSERVICE are set."""
    db_url = _local_url(db_url, "DB_URL", database=True)
    parts = urlsplit(db_url)
    try:
        return connect(
            db_url,
            host=parts.hostname,
            hostaddr=parts.hostname,
            port=parts.port or 5432,
            connect_timeout=10,
            sslmode="prefer",
        )
    except Exception:
        raise RuntimeError("Could not connect to the local smoke-test database.") from None


def require_status(response, allowed, operation):
    """Report status codes without echoing response bodies containing tokens."""
    if response.status_code not in allowed:
        raise AssertionError(f"{operation} failed with HTTP {response.status_code}.")


def cleanup_storage(http, api_url, bucket_name, object_path, headers):
    """Attempt both deletions, then report every unsuccessful cleanup step."""
    headers = {key: value for key, value in headers.items() if key.lower() != "content-type"}
    failures = []
    for label, suffix in (
        ("object", f"object/{bucket_name}/{object_path}"),
        ("bucket", f"bucket/{bucket_name}"),
    ):
        try:
            response = http.delete(f"{api_url}/storage/v1/{suffix}", headers=headers, timeout=10)
            if response.status_code not in (200, 204):
                failures.append(f"{label}: HTTP {response.status_code}")
        except Exception:
            failures.append(f"{label}: request failed")
    if failures:
        raise AssertionError("Storage cleanup failed (" + "; ".join(failures) + ").")
