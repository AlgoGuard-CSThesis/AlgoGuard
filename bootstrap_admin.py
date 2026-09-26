"""Create the first Administrator account. MAINTAINER ONLY.

Stage 5B.2: public sign-up is disabled, so no first account can create
itself, and there is deliberately no shared default password to become
the well-known credential on every installation. Trusted maintenance
creates the first Administrator with this script; from 5B.3 onwards, that
Administrator creates everyone else through a checked Edge Function.

What it does, in order:

  1. Creates (or finds) the Auth user, using the service key over HTTPS.
  2. Creates (or finds) the matching `public.profile` row, and grants the
     `administrator` record, over the direct maintenance connection.

Those are two systems, so a run can fail between them — a user with no
profile, or a profile with no role. Re-running fixes whichever half is
missing instead of creating a second account: every step is keyed on the
email address and the Auth user id.

Usage:
    python bootstrap_admin.py --email you@example.com --username admin
    python bootstrap_admin.py --email you@example.com --password-stdin

The generated password is printed once, to stdout only. It is never
written to a file, never logged, and cannot be recovered afterwards —
put it in your password manager before closing the terminal.
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from maintainer_env import (
    describe_target,
    load_maintainer_env,
    require_database_url,
    resolve_sslmode,
    safe,
)

# Long enough that the generated value is not the weak link, and made of
# characters that survive a copy-paste out of a terminal.
PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
PASSWORD_LENGTH = 24

REQUEST_TIMEOUT_SECONDS = 15


class BootstrapError(RuntimeError):
    """Anything that should stop the run with a readable message."""


@dataclass(frozen=True)
class Outcome:
    auth_user_id: str
    profile_id: int
    email: str
    username: str
    auth_user_created: bool
    profile_created: bool
    role_granted: bool
    password: str | None = field(repr=False)

    def summary(self) -> str:
        lines = [
            f"Auth user   {self.auth_user_id}  "
            f"({'created' if self.auth_user_created else 'already existed'})",
            f"Profile     {self.profile_id}  "
            f"({'created' if self.profile_created else 'already existed'})",
            f"Role        administrator  ({'granted' if self.role_granted else 'already held'})",
        ]
        return "\n".join(lines)


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    """A password nobody chose, so no installation shares one."""
    if length < 16:
        raise BootstrapError("Refusing to generate a password shorter than 16 characters.")
    # Match the configured Auth policy deterministically, not just usually.
    characters = [
        secrets.choice(group)
        for group in (string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%^&*-_=+")
    ]
    characters.extend(secrets.choice(PASSWORD_ALPHABET) for _ in range(length - 4))
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def api_url_from_jwks_url(jwks_url: str) -> str:
    """`https://<ref>.supabase.co/auth/v1/.well-known/jwks.json` is already
    in `.env.maintainer`; the API base is the same origin."""
    parts = urlsplit((jwks_url or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise BootstrapError(
            "Set SUPABASE_API_URL in .env.maintainer (or pass --api-url): "
            "SUPABASE_JWKS_URL is missing or not a URL."
        )
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def resolve_api_url(explicit: str | None, environment) -> str:
    for candidate in (explicit, environment.get("SUPABASE_API_URL")):
        if candidate and candidate.strip():
            parts = urlsplit(candidate.strip())
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise BootstrapError(f"{candidate!r} is not a valid Supabase API URL.")
            return urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    return api_url_from_jwks_url(environment.get("SUPABASE_JWKS_URL", ""))


def normalise_email(email: str) -> str:
    cleaned = (email or "").strip().lower()
    if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
        raise BootstrapError(f"{email!r} is not an email address.")
    return cleaned


def default_username(email: str) -> str:
    return normalise_email(email).split("@", 1)[0][:40] or "administrator"


# ---------------------------------------------------------------------
# Auth admin API. requests is a maintainer-only dependency, so it is
# imported where it is used, not at module import.
# ---------------------------------------------------------------------


def _session(secret_key: str):
    import requests

    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "apikey": secret_key,
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        }
    )
    return session


def find_auth_user(session, api_url: str, email: str) -> dict | None:
    """Look the user up by email, walking pages rather than trusting a
    filter parameter to exist on every Auth version."""
    page = 1
    while page <= 20:
        response = session.get(
            f"{api_url}/auth/v1/admin/users",
            params={"page": page, "per_page": 200},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            raise BootstrapError(f"Listing users failed with HTTP {response.status_code}.")
        users = response.json().get("users", [])
        if not users:
            return None
        for user in users:
            if (user.get("email") or "").lower() == email:
                return user
        page += 1
    return None


def create_auth_user(session, api_url: str, email: str, password: str) -> dict:
    response = session.post(
        f"{api_url}/auth/v1/admin/users",
        json={"email": email, "password": password, "email_confirm": True},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code in (200, 201):
        return response.json()
    if response.status_code in (409, 422):
        existing = find_auth_user(session, api_url, email)
        if existing:
            return existing
    raise BootstrapError(
        f"Creating the Auth user failed with HTTP {response.status_code}. "
        "Check that SUPABASE_SECRET_KEY is the service key for this project."
    )


# ---------------------------------------------------------------------
# Database half
# ---------------------------------------------------------------------


def upsert_profile_and_role(connection, auth_user_id: str, email: str, username: str):
    """Profile + administrator record, in one transaction, idempotently."""
    with connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select profile_id, username from public.profile where auth_user_id = %s",
                (auth_user_id,),
            )
            row = cursor.fetchone()
            profile_created = row is None
            if row is None:
                cursor.execute(
                    "insert into public.profile (auth_user_id, username, email) "
                    "values (%s, %s, %s) returning profile_id",
                    (auth_user_id, username, email),
                )
                profile_id = cursor.fetchone()[0]
            else:
                profile_id = row[0]

            # granted_by stays NULL: this is the trusted-maintenance path,
            # and the 5B.2 trigger reserves it for exactly that.
            cursor.execute(
                "insert into public.user_role (profile_id, role) values (%s, 'administrator') "
                "on conflict (profile_id, role) do nothing",
                (profile_id,),
            )
            role_granted = cursor.rowcount == 1

    return profile_id, profile_created, role_granted


def bootstrap(args, environment) -> Outcome:
    email = normalise_email(args.email)
    username = (args.username or default_username(email)).strip()
    api_url = resolve_api_url(args.api_url, environment)

    secret_key = environment.get("SUPABASE_SECRET_KEY", "").strip()
    if not secret_key:
        raise BootstrapError(
            "SUPABASE_SECRET_KEY is not set. Add it to .env.maintainer — it never "
            "belongs in .env, which ships to analyst installations."
        )

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
        if len(password) < 16:
            raise BootstrapError("Refusing a password shorter than 16 characters.")
        generated = None
    else:
        password = generate_password()
        generated = password

    dsn = require_database_url()
    print(f"Auth API:   {api_url}")
    print(f"Database:   {describe_target(dsn)}")

    session = _session(secret_key)
    existing = find_auth_user(session, api_url, email)
    if existing:
        auth_user = existing
        auth_user_created = False
        generated = None  # an existing account keeps its own password
    else:
        auth_user = create_auth_user(session, api_url, email, password)
        auth_user_created = True

    auth_user_id = auth_user.get("id")
    if not auth_user_id:
        raise BootstrapError("Auth did not return a user id.")

    import psycopg2

    connection = psycopg2.connect(
        dsn,
        sslmode=resolve_sslmode(dsn),
        connect_timeout=10,
        application_name="algoguard-bootstrap-admin",
    )
    try:
        profile_id, profile_created, role_granted = upsert_profile_and_role(
            connection, auth_user_id, email, username
        )
    finally:
        connection.close()

    return Outcome(
        auth_user_id=auth_user_id,
        profile_id=profile_id,
        email=email,
        username=username,
        auth_user_created=auth_user_created,
        profile_created=profile_created,
        role_granted=role_granted,
        password=generated,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="the Administrator's login email")
    parser.add_argument("--username", help="display name (defaults to the email's local part)")
    parser.add_argument("--api-url", help="https://<project-ref>.supabase.co")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin instead of generating one",
    )
    args = parser.parse_args(argv)

    load_maintainer_env()
    import os

    try:
        outcome = bootstrap(args, os.environ)
    except BootstrapError as exc:
        print(f"error: {safe(str(exc))}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - message may carry a DSN
        print(f"error: {safe(str(exc).strip())}", file=sys.stderr)
        return 1

    print()
    print(outcome.summary())
    if outcome.password:
        print()
        print("Generated password (shown once, not stored anywhere):")
        print(f"    {outcome.password}")
        print("Save it now. Change it after first sign-in if you prefer your own.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
