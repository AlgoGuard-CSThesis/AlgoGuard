"""Verify Supabase access tokens locally, against published public keys.

Stage 5B.2. An analyst installation holds no signing secret and never
will: it fetches the project's public JWKS over HTTPS and checks
signatures with it. That is the whole reason for asymmetric signing —
with the old shared HS256 secret, every installation able to *verify* a
token was equally able to *mint* one.

What this module refuses, and why each one matters:

  * Symmetric algorithms and `alg: none`. The classic attack is to take
    a published RSA public key, sign a forged token with it as an HMAC
    secret, and send `alg: HS256`. A verifier that trusts the header's
    algorithm accepts it. This one takes the algorithm from an allowlist
    of asymmetric algorithms and rejects any `oct` key outright.
  * A missing or unknown `kid`. An unknown one triggers at most one JWKS
    refresh (rate-limited), so key rotation recovers on its own without
    handing an attacker a way to make the app hammer the auth server.
  * Wrong issuer or audience, or a missing `exp`, `iat`, `sub`, `aud` or
    `iss`. Each is required explicitly rather than trusted to be present.

Usage (5D wires this into the request path):

    keys = JwksCache(jwks_url_for(project_url))
    token = verify_token(raw, keys=keys, issuer=issuer_for(project_url))
    profile = repository.profile_for_auth_user(token.subject)

The token says who the caller is. It never says what they may do: that
is a database question (`private.is_administrator()`, and the policies
in 5B.4), because a token minted before a demotion still carries the old
claim until it expires.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import jwt
from jwt import PyJWK, PyJWKSet

__all__ = [
    "ALLOWED_ALGORITHMS",
    "JwksCache",
    "SigningKeysUnavailable",
    "TokenError",
    "TokenExpired",
    "TokenRejected",
    "VerifiedToken",
    "issuer_for",
    "jwks_url_for",
    "verify_token",
]

# Asymmetric only. Adding "HS256" here would re-introduce the shared
# secret this stage exists to remove.
ALLOWED_ALGORITHMS = frozenset({"ES256", "RS256"})

REQUIRED_CLAIMS = ("exp", "iat", "sub", "aud", "iss")

DEFAULT_AUDIENCE = "authenticated"
DEFAULT_LEEWAY_SECONDS = 10.0
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MIN_REFRESH_SECONDS = 30.0


class TokenError(Exception):
    """Base class. Callers distinguish these three, not PyJWT's taxonomy."""


class TokenExpired(TokenError):
    """Valid signature, past its expiry. The caller should refresh once."""


class TokenRejected(TokenError):
    """Not a token this installation will act on. Never retried."""


class SigningKeysUnavailable(TokenError):
    """The JWKS could not be fetched. Transient; the token is unjudged.

    Distinct from TokenRejected on purpose: 'we could not check' must not
    be reported as 'we checked and it was bad'.
    """


@dataclass(frozen=True)
class VerifiedToken:
    subject: str
    email: str | None
    expires_at: datetime
    claims: Mapping[str, Any]

    @property
    def seconds_remaining(self) -> float:
        return (self.expires_at - datetime.now(timezone.utc)).total_seconds()


def _base_url(project_url: str) -> str:
    """Validate the project URL and return it without a trailing slash."""
    parts = urlsplit((project_url or "").strip())
    host = (parts.hostname or "").lower()
    is_loopback = host in {"127.0.0.1", "localhost", "::1"}
    if parts.scheme not in {"http", "https"} or not host:
        raise ValueError("Supabase project URL must be an http(s) URL.")
    if parts.scheme == "http" and not is_loopback:
        # The local Docker stack serves plain HTTP on loopback; a remote
        # project over HTTP would mean fetching signing keys from anyone
        # positioned on the path.
        raise ValueError("Refusing a non-loopback Supabase URL over plain HTTP.")
    return f"{parts.scheme}://{parts.netloc}"


def issuer_for(project_url: str) -> str:
    """The `iss` claim Supabase Auth puts in its tokens."""
    return f"{_base_url(project_url)}/auth/v1"


def jwks_url_for(project_url: str) -> str:
    return f"{issuer_for(project_url)}/.well-known/jwks.json"


def _fetch_json(url: str, timeout: float) -> dict:
    # Stage 5D.4: register the socket so live capture never classifies it.
    from cloud_connections import owned_opener

    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with owned_opener().open(request, timeout=timeout) as response:
            if response.status != 200:
                raise SigningKeysUnavailable(f"JWKS endpoint returned HTTP {response.status}.")
            return json.loads(response.read().decode("utf-8"))
    except SigningKeysUnavailable:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SigningKeysUnavailable(
            f"Could not fetch signing keys: {exc.__class__.__name__}"
        ) from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SigningKeysUnavailable("Signing key endpoint did not return JSON.") from None


class JwksCache:
    """Public signing keys, cached, refreshed only when a key is unknown.

    Rotation works by itself: a token signed with a new key arrives, its
    `kid` is not cached, one refresh happens, and it verifies. The
    minimum refresh interval keeps a stream of forged `kid`s from turning
    into a stream of outbound requests.
    """

    def __init__(
        self,
        url: str,
        *,
        fetcher: Callable[[str, float], dict] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        min_refresh_seconds: float = DEFAULT_MIN_REFRESH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._fetch = fetcher or _fetch_json
        self._timeout = timeout
        self._min_refresh_seconds = min_refresh_seconds
        self._clock = clock
        self._keys: dict[str, PyJWK] = {}
        self._last_refresh: float | None = None

    @property
    def url(self) -> str:
        return self._url

    def refresh(self) -> None:
        """Fetch the key set. Rate-limited; a suppressed call is a no-op."""
        now = self._clock()
        if self._last_refresh is not None and now - self._last_refresh < self._min_refresh_seconds:
            return
        self._last_refresh = now
        document = self._fetch(self._url, self._timeout)
        try:
            key_set = PyJWKSet.from_dict(document)
        except Exception:
            raise SigningKeysUnavailable("Signing key set could not be parsed.") from None
        self._keys = {key.key_id: key for key in key_set.keys if key.key_id}

    def key_for(self, kid: str) -> PyJWK:
        """Return the key for this id, refreshing at most once."""
        key = self._keys.get(kid)
        if key is None:
            self.refresh()
            key = self._keys.get(kid)
        if key is None:
            raise TokenRejected("Token was signed with an unknown key.")
        return key

    @property
    def known_key_ids(self) -> frozenset[str]:
        return frozenset(self._keys)


def verify_token(
    token: str,
    *,
    keys: JwksCache,
    issuer: str,
    audience: str = DEFAULT_AUDIENCE,
    leeway: float = DEFAULT_LEEWAY_SECONDS,
) -> VerifiedToken:
    """Verify a Supabase access token, or raise a TokenError subclass."""
    if not isinstance(token, str) or not token.strip():
        raise TokenRejected("No access token supplied.")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError:
        raise TokenRejected("Malformed access token.") from None

    algorithm = header.get("alg")
    if algorithm not in ALLOWED_ALGORITHMS:
        # Covers `none`, every HS* variant, and anything unrecognised.
        raise TokenRejected(f"Unsupported token algorithm: {algorithm!r}.")

    key_id = header.get("kid")
    if not key_id or not isinstance(key_id, str):
        raise TokenRejected("Access token carries no key id.")

    signing_key = keys.key_for(key_id)

    if signing_key.key_type == "oct":
        # A symmetric key in the published key set: verifying with it
        # would mean anyone holding the same public document can mint
        # tokens. Never, regardless of the header.
        raise TokenRejected("Refusing a symmetric signing key.")

    declared = getattr(signing_key, "algorithm_name", None)
    if declared and declared != algorithm:
        raise TokenRejected("Token algorithm does not match its signing key.")

    try:
        claims = jwt.decode(
            token,
            key=signing_key.key,
            algorithms=[algorithm],
            audience=audience,
            issuer=issuer,
            leeway=leeway,
            options={
                "require": list(REQUIRED_CLAIMS),
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except jwt.ExpiredSignatureError:
        raise TokenExpired("Access token has expired.") from None
    except jwt.InvalidTokenError as exc:
        # PyJWT's message names the failing claim and contains no secret.
        raise TokenRejected(f"Access token rejected: {exc}") from None

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise TokenRejected("Access token has no subject.")

    email = claims.get("email")
    return VerifiedToken(
        subject=subject,
        email=email if isinstance(email, str) and email else None,
        expires_at=datetime.fromtimestamp(float(claims["exp"]), tz=timezone.utc),
        claims=claims,
    )
