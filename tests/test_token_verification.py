"""Stage 5B.2 evidence: what the token verifier accepts and refuses.

Every token here is minted in-process with throwaway keys. Nothing
contacts a network, so this runs in the fast lane.

The interesting cases are the refusals. A verifier that says yes to a
valid token is easy; one that says no to a token signed with the
published *public* key as an HMAC secret is the point.
"""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

jwt = pytest.importorskip(
    "jwt", reason="PyJWT[crypto] is required: pip install -r requirements.txt"
)
pytest.importorskip("cryptography", reason="PyJWT[crypto] pulls in cryptography")

from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: E402
from jwt.algorithms import ECAlgorithm, RSAAlgorithm  # noqa: E402

from token_verification import (  # noqa: E402
    JwksCache,
    SigningKeysUnavailable,
    TokenExpired,
    TokenRejected,
    issuer_for,
    jwks_url_for,
    verify_token,
)

ISSUER = "https://example.supabase.co/auth/v1"
AUDIENCE = "authenticated"
SUBJECT = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------
# Helpers: a throwaway signing key and the JWKS document that publishes it
# ---------------------------------------------------------------------


def make_ec_key(kid="ec-key-1"):
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_jwk = json.loads(ECAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": kid, "alg": "ES256", "use": "sig"})
    return private_key, public_jwk


def make_rsa_key(kid="rsa-key-1"):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return private_key, public_jwk


def make_token(private_key, kid, *, algorithm="ES256", issuer=ISSUER, audience=AUDIENCE,
               subject=SUBJECT, expires_in=3600, issued_at=None, omit=()):
    now = int(issued_at if issued_at is not None else time.time())
    claims = {
        "sub": subject,
        "aud": audience,
        "iss": issuer,
        "iat": now,
        "exp": now + expires_in,
        "email": "analyst@algoguard.invalid",
        "role": "authenticated",
    }
    for claim in omit:
        claims.pop(claim, None)
    return jwt.encode(claims, private_key, algorithm=algorithm, headers={"kid": kid})


def cache_for(*jwks, fetcher=None, clock=None):
    """A JwksCache serving these keys, counting how often it fetched."""
    document = {"keys": list(jwks)}
    calls = []

    def default_fetcher(url, timeout):
        calls.append(url)
        return document

    cache = JwksCache(
        "https://example.supabase.co/auth/v1/.well-known/jwks.json",
        fetcher=fetcher or default_fetcher,
        clock=clock or (lambda: 0.0),
        min_refresh_seconds=30.0,
    )
    cache.fetch_calls = calls  # type: ignore[attr-defined]
    return cache


# ---------------------------------------------------------------------
# Accepting a good token
# ---------------------------------------------------------------------


def test_a_valid_es256_token_is_accepted():
    private_key, public_jwk = make_ec_key()
    token = make_token(private_key, "ec-key-1")

    verified = verify_token(token, keys=cache_for(public_jwk), issuer=ISSUER)

    assert verified.subject == SUBJECT
    assert verified.email == "analyst@algoguard.invalid"
    assert verified.expires_at > datetime.now(timezone.utc)
    assert verified.seconds_remaining > 0


def test_a_valid_rs256_token_is_accepted():
    private_key, public_jwk = make_rsa_key()
    token = make_token(private_key, "rsa-key-1", algorithm="RS256")

    verified = verify_token(token, keys=cache_for(public_jwk), issuer=ISSUER)

    assert verified.subject == SUBJECT


def test_expiry_leeway_tolerates_small_clock_skew():
    private_key, public_jwk = make_ec_key()
    token = make_token(private_key, "ec-key-1", expires_in=-5)

    verified = verify_token(token, keys=cache_for(public_jwk), issuer=ISSUER, leeway=30)

    assert verified.subject == SUBJECT


# ---------------------------------------------------------------------
# The attacks
# ---------------------------------------------------------------------


def test_a_token_signed_with_the_public_key_as_an_hmac_secret_is_refused():
    """Algorithm confusion. The public key is public; if HS256 were
    accepted, anyone who can read the JWKS could mint tokens."""
    _, public_jwk = make_ec_key()
    # Construct the hostile wire token directly: newer PyJWT versions refuse
    # to sign with JWK text as an HMAC secret before our verifier is exercised.
    header = {"alg": "HS256", "typ": "JWT", "kid": "ec-key-1"}
    claims = {
            "sub": SUBJECT, "aud": AUDIENCE, "iss": ISSUER,
            "iat": int(time.time()), "exp": int(time.time()) + 3600,
    }
    signing_input = b".".join(
        base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=")
        for part in (header, claims)
    )
    signature = hmac.new(json.dumps(public_jwk).encode(), signing_input, hashlib.sha256).digest()
    forged = (signing_input + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()

    with pytest.raises(TokenRejected, match="Unsupported token algorithm"):
        verify_token(forged, keys=cache_for(public_jwk), issuer=ISSUER)


def test_an_unsigned_token_is_refused():
    unsigned = jwt.encode(
        {
            "sub": SUBJECT, "aud": AUDIENCE, "iss": ISSUER,
            "iat": int(time.time()), "exp": int(time.time()) + 3600,
        },
        key="",
        algorithm="none",
        headers={"kid": "ec-key-1"},
    )
    _, public_jwk = make_ec_key()

    with pytest.raises(TokenRejected, match="Unsupported token algorithm"):
        verify_token(unsigned, keys=cache_for(public_jwk), issuer=ISSUER)


def test_a_symmetric_key_in_the_published_key_set_is_refused():
    """Defence in depth: even if the allowlist were widened one day, a
    published `oct` key is never a thing to verify against."""
    private_key, public_jwk = make_ec_key()
    token = make_token(private_key, "ec-key-1")
    symmetric = {"kty": "oct", "kid": "ec-key-1", "k": "c2VjcmV0", "alg": "HS256"}

    with pytest.raises(TokenRejected):
        verify_token(token, keys=cache_for(symmetric), issuer=ISSUER)


def test_a_tampered_payload_is_refused():
    private_key, public_jwk = make_ec_key()
    token = make_token(private_key, "ec-key-1")
    header, payload, signature = token.split(".")
    other = make_token(private_key, "ec-key-1", subject="22222222-2222-2222-2222-222222222222")
    swapped = f"{header}.{other.split('.')[1]}.{signature}"

    with pytest.raises(TokenRejected):
        verify_token(swapped, keys=cache_for(public_jwk), issuer=ISSUER)


def test_a_token_from_another_project_is_refused():
    private_key, public_jwk = make_ec_key()
    token = make_token(private_key, "ec-key-1", issuer="https://attacker.supabase.co/auth/v1")

    with pytest.raises(TokenRejected):
        verify_token(token, keys=cache_for(public_jwk), issuer=ISSUER)


def test_a_token_for_another_audience_is_refused():
    private_key, public_jwk = make_ec_key()
    token = make_token(private_key, "ec-key-1", audience="some-other-service")

    with pytest.raises(TokenRejected):
        verify_token(token, keys=cache_for(public_jwk), issuer=ISSUER)


def test_an_expired_token_is_reported_as_expired_not_as_invalid():
    """5D refreshes once on expiry and stops capture if that fails; it
    must be able to tell expiry from a bad token."""
    private_key, public_jwk = make_ec_key()
    token = make_token(private_key, "ec-key-1", expires_in=-3600)

    with pytest.raises(TokenExpired):
        verify_token(token, keys=cache_for(public_jwk), issuer=ISSUER)


def test_a_token_missing_a_required_claim_is_refused():
    private_key, public_jwk = make_ec_key()
    token = make_token(private_key, "ec-key-1", omit=("iat",))

    with pytest.raises(TokenRejected):
        verify_token(token, keys=cache_for(public_jwk), issuer=ISSUER)


def test_a_token_without_a_key_id_is_refused():
    private_key, public_jwk = make_ec_key()
    token = jwt.encode(
        {
            "sub": SUBJECT, "aud": AUDIENCE, "iss": ISSUER,
            "iat": int(time.time()), "exp": int(time.time()) + 3600,
        },
        private_key,
        algorithm="ES256",
    )

    with pytest.raises(TokenRejected, match="no key id"):
        verify_token(token, keys=cache_for(public_jwk), issuer=ISSUER)


def test_garbage_is_refused_without_raising_anything_else():
    _, public_jwk = make_ec_key()
    with pytest.raises(TokenRejected):
        verify_token("not-a-token", keys=cache_for(public_jwk), issuer=ISSUER)
    with pytest.raises(TokenRejected):
        verify_token("", keys=cache_for(public_jwk), issuer=ISSUER)


# ---------------------------------------------------------------------
# Key rotation
# ---------------------------------------------------------------------


def test_a_token_signed_with_a_rotated_in_key_verifies_after_one_refresh():
    old_private, old_public = make_ec_key("ec-key-old")
    new_private, new_public = make_ec_key("ec-key-new")

    published = {"keys": [old_public]}
    fetches = []
    now = [0.0]

    def fetcher(url, timeout):
        fetches.append(url)
        return published

    cache = JwksCache(
        jwks_url_for("https://example.supabase.co"),
        fetcher=fetcher,
        clock=lambda: now[0],
        min_refresh_seconds=30.0,
    )

    verify_token(make_token(old_private, "ec-key-old"), keys=cache, issuer=ISSUER)
    assert len(fetches) == 1

    # The project rotates its signing key; the next token uses the new one.
    published["keys"] = [old_public, new_public]
    now[0] = 120.0

    verified = verify_token(make_token(new_private, "ec-key-new"), keys=cache, issuer=ISSUER)

    assert verified.subject == SUBJECT
    assert len(fetches) == 2, "rotation should cost exactly one extra fetch"


def test_a_stream_of_unknown_key_ids_does_not_become_a_stream_of_fetches():
    """Otherwise a forged `kid` is a free way to make every installation
    hammer the auth server."""
    private_key, public_jwk = make_ec_key("ec-key-1")
    fetches = []
    now = [0.0]

    def fetcher(url, timeout):
        fetches.append(url)
        return {"keys": [public_jwk]}

    cache = JwksCache(
        jwks_url_for("https://example.supabase.co"),
        fetcher=fetcher,
        clock=lambda: now[0],
        min_refresh_seconds=30.0,
    )
    verify_token(make_token(private_key, "ec-key-1"), keys=cache, issuer=ISSUER)
    assert len(fetches) == 1

    for attempt in range(5):
        now[0] += 1.0
        with pytest.raises(TokenRejected, match="unknown key"):
            verify_token(make_token(private_key, f"forged-{attempt}"), keys=cache, issuer=ISSUER)

    assert len(fetches) == 1, f"suppressed refresh expected, saw {len(fetches)} fetches"


def test_an_unreachable_key_endpoint_is_not_reported_as_a_bad_token():
    """'We could not check' and 'we checked and it failed' are different
    answers, and 5D reacts to them differently."""
    private_key, _ = make_ec_key()

    def failing_fetcher(url, timeout):
        raise SigningKeysUnavailable("network down")

    cache = JwksCache(
        jwks_url_for("https://example.supabase.co"),
        fetcher=failing_fetcher,
        clock=lambda: 0.0,
    )

    with pytest.raises(SigningKeysUnavailable):
        verify_token(make_token(private_key, "ec-key-1"), keys=cache, issuer=ISSUER)


# ---------------------------------------------------------------------
# URL derivation — no new environment variable for the analyst
# ---------------------------------------------------------------------


def test_urls_are_derived_from_the_project_url():
    assert issuer_for("https://abc.supabase.co") == "https://abc.supabase.co/auth/v1"
    assert issuer_for("https://abc.supabase.co/") == "https://abc.supabase.co/auth/v1"
    assert jwks_url_for("https://abc.supabase.co") == (
        "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
    )


def test_the_local_stack_is_allowed_over_plain_http_and_nothing_else_is():
    assert issuer_for("http://127.0.0.1:54321") == "http://127.0.0.1:54321/auth/v1"
    with pytest.raises(ValueError):
        issuer_for("http://abc.supabase.co")
    with pytest.raises(ValueError):
        issuer_for("not-a-url")
    with pytest.raises(ValueError):
        issuer_for("")


def test_seconds_remaining_is_negative_for_an_expired_token():
    private_key, public_jwk = make_ec_key()
    token = make_token(private_key, "ec-key-1", expires_in=-5)
    verified = verify_token(token, keys=cache_for(public_jwk), issuer=ISSUER, leeway=30)
    assert verified.seconds_remaining < 0
    assert verified.expires_at < datetime.now(timezone.utc) + timedelta(seconds=1)
