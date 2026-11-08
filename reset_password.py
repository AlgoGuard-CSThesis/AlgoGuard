"""Give an existing account a new password. MAINTAINER ONLY.

AlgoGuard has no page for changing or resetting a password in this release,
and Supabase's e-mailed recovery links have nowhere to land in the app. When a
user forgets a password, the maintainer sets a new one here and passes it on;
the user keeps that password, because nobody can change it in the app.

The new password is generated (24 characters) and printed once, to stdout
only, unless one is supplied with --password-stdin; a supplied password must
meet the same rule as the Accounts page. A reset also ends the account's
running sign-ins: they can no longer renew, so each stops when its current
access token expires and the user signs in again. To lock someone out at once,
deactivate the account on the Accounts page instead.

Usage:
    python reset_password.py --email analyst@example.com
    python reset_password.py --email analyst@example.com --password-stdin
"""

from __future__ import annotations

import argparse
import sys
from uuid import UUID

from bootstrap_admin import (
    REQUEST_TIMEOUT_SECONDS,
    BootstrapError,
    _session,
    find_auth_user,
    generate_password,
    normalise_email,
    resolve_api_url,
)
from maintainer_env import load_maintainer_env, safe
from services.password_policy import PASSWORD_RULE_TEXT, password_is_strong


def set_password(session, api_url: str, email: str, password: str) -> str:
    """Set the password of the Auth user with this e-mail; return the user's id."""
    user = find_auth_user(session, api_url, email)
    if not user:
        raise BootstrapError(f"No account uses {email}.")
    try:
        user_id = str(UUID(str(user.get("id"))))
    except ValueError:
        raise BootstrapError("Auth returned an invalid user id.") from None
    response = session.put(
        f"{api_url}/auth/v1/admin/users/{user_id}",
        json={"password": password},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise BootstrapError(
            f"Changing the password failed with HTTP {response.status_code}. "
            "Check that SUPABASE_SECRET_KEY is the secret key for this project."
        )
    return user_id


def reset(args, environment, stdin=sys.stdin) -> str | None:
    """Reset the password; return the generated password, or None if it was supplied."""
    email = normalise_email(args.email)
    api_url = resolve_api_url(args.api_url, environment)
    secret_key = environment.get("SUPABASE_SECRET_KEY", "").strip()
    if not secret_key:
        raise BootstrapError(
            "SUPABASE_SECRET_KEY is not set. Add it to .env.maintainer — it never "
            "belongs in .env, which ships to analyst installations."
        )
    if args.password_stdin:
        password = stdin.readline().rstrip("\r\n")
        if not password_is_strong(password):
            raise BootstrapError(PASSWORD_RULE_TEXT)
        generated = None
    else:
        password = generate_password()
        generated = password
    print(f"Auth API:   {api_url}")
    session = _session(secret_key)
    try:
        set_password(session, api_url, email, password)
    finally:
        session.close()
    return generated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "AlgoGuard maintenance").splitlines()[0]
    )
    parser.add_argument("--email", required=True, help="the account's sign-in e-mail")
    parser.add_argument("--api-url", help="https://<project-ref>.supabase.co")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the new password from stdin instead of generating one",
    )
    args = parser.parse_args(argv)

    load_maintainer_env()
    import os

    try:
        generated = reset(args, os.environ)
    except BootstrapError as exc:
        print(f"error: {safe(str(exc))}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - the message may carry a URL or key
        print(f"error: {safe(str(exc).strip())}", file=sys.stderr)
        return 1

    print(f"Password changed for {normalise_email(args.email)}.")
    if generated:
        print()
        print("New password (shown once, not stored anywhere):")
        print(f"    {generated}")
        print("Give it to the user privately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
