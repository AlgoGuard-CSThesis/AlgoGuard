"""Process-memory cloud identities and opaque browser sessions (Stage 5D.1).

Access and refresh tokens live only in this process. The browser receives a
random session identifier in an HttpOnly/SameSite cookie; restarting the
application therefore forgets every session and requires online login again,
even when a verified model is cached locally.

Authorization is never taken from token claims. Each identity is rebuilt from
the protected current profile and role rows, and PostgreSQL rechecks node
membership for every operation.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from uuid import UUID

from flask import g
from flask.sessions import SessionInterface, SessionMixin

from cloud_repository import CloudRepository, RepositoryError, UserNodeContext
from token_verification import (
    JwksCache,
    SigningKeysUnavailable,
    TokenError,
    issuer_for,
    jwks_url_for,
    verify_token,
)

REFRESH_MARGIN_SECONDS = 60
REFRESH_RETRY_SECONDS = 15
MAX_SESSIONS = 256
ANONYMOUS_IDLE_SECONDS = 3600
SIGNED_IN_IDLE_SECONDS = 12 * 3600


@dataclass(eq=False)
class CloudIdentity:
    user_id: UUID
    profile_id: int
    node_id: UUID
    username: str
    roles: tuple[str, ...]
    expires_at: float
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    active: bool = True
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    refresh_after: float = field(default=0.0, repr=False)

    @property
    def is_administrator(self):
        return "administrator" in self.roles

    @property
    def display_role(self):
        return "Administrator" if self.is_administrator else "Analyst"

    @property
    def session_bound(self):
        """A browser login can refresh; a bearer identity lasts one token."""
        return self.refresh_token is not None

    def clear(self):
        with self.lock:
            self.active = False
            self.access_token = ""
            self.refresh_token = None


class CloudAuth:
    def __init__(self, project_url, publishable_key, node_id, *, jwks=None):
        self.url = project_url
        self.key = publishable_key
        self.node_id = UUID(str(node_id))
        self.keys = jwks or JwksCache(jwks_url_for(project_url))
        self.key_lock = threading.RLock()

    def _transport(self, token, user_id=None, profile_id=1):
        return CloudRepository(
            self.url,
            self.key,
            UserNodeContext(user_id or UUID(int=0), profile_id, self.node_id, token),
        )

    def _verify(self, token):
        try:
            with self.key_lock:
                return verify_token(token, keys=self.keys, issuer=issuer_for(self.url), leeway=0)
        except SigningKeysUnavailable:
            raise RepositoryError("transient") from None
        except TokenError:
            raise RepositoryError("authentication") from None

    def authenticate_access(self, token, refresh_token=None):
        verified = self._verify(token)
        user_id = UUID(verified.subject)
        repo = self._transport(token, user_id)
        profiles = repo._request(
            "GET",
            "/rest/v1/profile",
            params={
                "auth_user_id": "eq." + str(user_id),
                "is_active": "eq.true",
                "select": "profile_id,username,legacy_username,is_active",
            },
        )
        if not isinstance(profiles, list) or len(profiles) != 1:
            # A historical profile has no Auth UUID, and a deactivated one is hidden.
            raise RepositoryError("permission")
        profile = profiles[0]
        try:
            profile_id = int(profile["profile_id"])
            username = str(profile.get("legacy_username") or profile["username"])
        except (KeyError, TypeError, ValueError):
            raise RepositoryError("protocol") from None
        roles = repo.list_records("user_role", filters={"profile_id": profile_id})
        return CloudIdentity(
            user_id,
            profile_id,
            self.node_id,
            username,
            tuple(sorted(str(row.get("role")) for row in roles.rows)),
            verified.expires_at.timestamp(),
            token,
            refresh_token,
        )

    def login(self, email, password):
        if (
            not isinstance(email, str)
            or not isinstance(password, str)
            or not email.strip()
            or len(email) > 254
            or not password
            or len(password) > 128
        ):
            raise RepositoryError("validation")
        data = self._transport(self.key)._request(
            "POST",
            "/auth/v1/token",
            params={"grant_type": "password"},
            body={"email": email.strip(), "password": password},
        )
        if not isinstance(data, dict):
            raise RepositoryError("protocol")
        try:
            return self.authenticate_access(data["access_token"], data["refresh_token"])
        except (KeyError, TypeError):
            raise RepositoryError("protocol") from None

    def repository(self, identity, *, refresh=True):
        """A repository carrying this identity's current token, refreshing at most once.

        The identity lock serialises refresh for everything sharing the identity
        (page requests, monitor polling, and the upload worker), so a rotated
        refresh token is spent exactly once.
        """
        with identity.lock:
            if not identity.active:
                raise RepositoryError("authentication")
            now = time.time()
            expired = now >= identity.expires_at
            due = (
                identity.refresh_token
                and now >= identity.expires_at - REFRESH_MARGIN_SECONDS
                and time.monotonic() >= identity.refresh_after
            )
            if expired or (refresh and due):
                if not refresh or not identity.refresh_token:
                    identity.clear()
                    raise RepositoryError("authentication")
                try:
                    data = self._transport(self.key)._request(
                        "POST",
                        "/auth/v1/token",
                        params={"grant_type": "refresh_token"},
                        body={"refresh_token": identity.refresh_token},
                    )
                    if not isinstance(data, dict):
                        raise RepositoryError("protocol")
                    replacement = self.authenticate_access(
                        data["access_token"], data["refresh_token"]
                    )
                    if replacement.user_id != identity.user_id:
                        raise RepositoryError("authentication")
                    identity.access_token = replacement.access_token
                    identity.refresh_token = replacement.refresh_token
                    identity.expires_at = replacement.expires_at
                    identity.roles = replacement.roles
                    identity.profile_id = replacement.profile_id
                except RepositoryError as error:
                    if error.retryable and time.time() < identity.expires_at:
                        # Refresh is unavailable but the current token still works.
                        # Back off so a stalled network cannot make every request
                        # wait for a refresh timeout.
                        identity.refresh_after = time.monotonic() + REFRESH_RETRY_SECONDS
                        return self._transport(
                            identity.access_token, identity.user_id, identity.profile_id
                        )
                    identity.clear()
                    raise RepositoryError("authentication") from None
                except (KeyError, TypeError):
                    identity.clear()
                    raise RepositoryError("authentication") from None
            return self._transport(identity.access_token, identity.user_id, identity.profile_id)

    def current_roles(self, identity):
        """Protected records stay authoritative after demotion or deactivation."""
        repo = self.repository(identity)
        profiles = repo.list_records("profile", filters={"profile_id": identity.profile_id})
        if not profiles.rows or profiles.rows[0].get("is_active") is not True:
            identity.clear()
            raise RepositoryError("permission")
        identity.roles = tuple(
            sorted(
                str(row.get("role"))
                for row in repo.list_records(
                    "user_role", filters={"profile_id": identity.profile_id}
                ).rows
            )
        )
        return identity.roles

    def logout(self, identity):
        with identity.lock:
            token = identity.access_token
            identity.clear()
        if token:
            try:
                # scope=local ends this installation's session only; the same
                # user's sessions on other enrolled nodes keep working.
                self._transport(token)._request(
                    "POST", "/auth/v1/logout", params={"scope": "local"}, body={}
                )
            except RepositoryError:
                pass  # Local tokens are already gone, even if remote logout is unavailable.


class MemorySession(dict, SessionMixin):
    def __init__(self, sid=None):
        super().__init__()
        self.sid = sid or secrets.token_urlsafe(32)
        self.identity: CloudIdentity | None = None
        self.touched = time.time()


class MemorySessionInterface(SessionInterface):
    """The browser receives only a random ID; restart invalidates every session."""

    def __init__(self, *, limit=MAX_SESSIONS, clock=time.time):
        self.sessions = {}
        self.lock = threading.RLock()
        self.limit = limit
        self.clock = clock

    def _signed_in(self, item):
        return item.identity is not None and item.identity.active

    def _prune_locked(self):
        now = self.clock()
        for key, item in list(self.sessions.items()):
            idle = SIGNED_IN_IDLE_SECONDS if self._signed_in(item) else ANONYMOUS_IDLE_SECONDS
            if now - item.touched > idle:
                if item.identity:
                    item.identity.clear()
                del self.sessions[key]

    def open_session(self, app, request):
        sid = request.cookies.get(self.get_cookie_name(app))
        with self.lock:
            self._prune_locked()
            existing = self.sessions.get(sid) if sid else None
            if existing is not None:
                existing.touched = self.clock()
                return existing
        return MemorySession()

    def rotate(self, session):
        with self.lock:
            self.sessions.pop(session.sid, None)
            session.sid = secrets.token_urlsafe(32)

    def forget(self, session):
        with self.lock:
            self.sessions.pop(session.sid, None)

    def save_session(self, app, session, response):
        if not isinstance(session, MemorySession):
            raise TypeError("Cloud sessions must use MemorySession.")
        if g.get("bearer"):
            return  # API callers never receive or extend a browser session.
        if not session and session.identity is None:
            return  # Nothing to remember; do not hand out a cookie.
        with self.lock:
            if session.sid not in self.sessions and len(self.sessions) >= self.limit:
                anonymous = [
                    (item.touched, key)
                    for key, item in self.sessions.items()
                    if not self._signed_in(item)
                ]
                if not anonymous:
                    # Refuse excess sessions rather than retaining unbounded secrets.
                    response.status_code = 503
                    return
                del self.sessions[min(anonymous)[1]]
            self.sessions[session.sid] = session
        response.set_cookie(
            self.get_cookie_name(app),
            session.sid,
            httponly=True,
            secure=self.get_cookie_secure(app),
            samesite="Lax",
            path="/",
        )
