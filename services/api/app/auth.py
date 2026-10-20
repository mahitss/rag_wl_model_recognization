"""Authentication, password hashing, and session management for WonderLearn.

Provides:

1. **Password hashing** with Argon2id via the `argon2-cffi` package.
2. **Session management** — sessions are stored in SQLite (``user_sessions``
   table) and identified by a random session token.  The session token is
   transmitted to the client in an ``HttpOnly``, ``SameSite=Lax`` cookie
   (``Secure`` in production), so it is never accessible to client-side
   JavaScript.
3. **CSRF protection** — state-changing requests (POST/PATCH/DELETE) require a
   matching CSRF token sent via the ``X-CSRF-Token`` header.  The token is
   issued in a separate ``csrf_token`` cookie (not HttpOnly, so the frontend
   can read it) and must be echoed back on every state-changing request.
4. **FastAPI dependencies** — ``get_current_user`` resolves the authenticated
   user from the session cookie; ``require_auth`` raises 401 when no session
   exists; ``get_csrf_token`` returns the current CSRF token for the frontend.

All functions are dependency-free apart from ``argon2`` and the standard
library, appropriate for a ₹0-budget deployment.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyError, InvalidHashError
from fastapi import HTTPException, Request, Response, status

from app.config import Settings


# ---------------------------------------------------------------------------
# Password hashing (Argon2id)
# ---------------------------------------------------------------------------

# Argon2id parameters: 64 MiB memory, 3 iterations, 4 parallel lanes.
# These are modest but appropriate for a single-instance local deployment.
password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    """Return an Argon2id hash of ``password`` suitable for storage."""
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify ``password`` against an Argon2id hash.

    Returns ``False`` (never raises) for invalid or malformed hashes so the
    caller can treat all authentication failures uniformly.
    """
    try:
        password_hasher.verify(password_hash, password)
        return True
    except (VerifyError, InvalidHashError):
        return False


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

SESSION_COOKIE_NAME = "wl_session"
CSRF_COOKIE_NAME = "wl_csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"

# Session lifetime: 30 days of absolute inactivity.
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60


def generate_session_token() -> str:
    """Return a cryptographically random session token."""
    return secrets.token_urlsafe(32)


def generate_csrf_token() -> str:
    """Return a cryptographically random CSRF token."""
    return secrets.token_urlsafe(32)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def create_session(
    database_path: Path,
    user_id: str,
    *,
    ttl_seconds: int = SESSION_TTL_SECONDS,
) -> tuple[str, datetime]:
    """Create a new session for ``user_id``.

    Returns ``(session_token, expires_at)``.  The caller is responsible for
    setting the session cookie on the response.
    """
    session_token = generate_session_token()
    expires_at = _now_utc() + timedelta(seconds=ttl_seconds)
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        with connection:
            connection.execute(
                """INSERT INTO user_sessions(
                       session_id, user_id, expires_at, created_at
                   ) VALUES (?, ?, ?, ?)""",
                (session_token, user_id, expires_at.isoformat(), _now_utc().isoformat()),
            )
    return session_token, expires_at


def get_session(
    database_path: Path, session_token: str
) -> dict[str, Any] | None:
    """Return the session row for ``session_token`` if it exists and is not
    expired, otherwise ``None``.  Expired sessions are deleted lazily."""
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        row = connection.execute(
            """SELECT session_id, user_id, expires_at, created_at
               FROM user_sessions WHERE session_id = ?""",
            (session_token,),
        ).fetchone()
        if row is None:
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= _now_utc():
            connection.execute(
                "DELETE FROM user_sessions WHERE session_id = ?", (session_token,)
            )
            connection.commit()
            return None
        return {
            "session_id": row["session_id"],
            "user_id": row["user_id"],
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
        }


def destroy_session(database_path: Path, session_token: str) -> None:
    """Remove ``session_token`` from the session store (no-op if absent)."""
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        with connection:
            connection.execute(
                "DELETE FROM user_sessions WHERE session_id = ?", (session_token,)
            )


def cleanup_expired_sessions(database_path: Path) -> int:
    """Delete all expired sessions and return the number removed."""
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        with connection:
            cursor = connection.execute(
                "DELETE FROM user_sessions WHERE expires_at <= ?",
                (_now_utc().isoformat(),),
            )
            return cursor.rowcount


# ---------------------------------------------------------------------------
# FastAPI authentication dependencies
# ---------------------------------------------------------------------------


class AuthUser:
    """Authenticated user information available on ``request.state.user``."""

    def __init__(self, user_id: str, username: str) -> None:
        self.user_id = user_id
        self.username = username


def get_current_user(request: Request) -> AuthUser | None:
    """Return the authenticated user for ``request``, or ``None`` if no valid
    session cookie is present.

    The session cookie is read from the request, validated against the
    database, and the corresponding user is looked up.  Expired sessions are
    removed automatically.
    """
    from app.config import get_settings
    from app.storage import get_user_by_session

    settings = get_settings()
    database_path = settings.database_path

    session_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_cookie:
        return None

    session = get_session(database_path, session_cookie)
    if session is None:
        return None

    user = get_user_by_id(database_path, session["user_id"])
    return user


def require_auth(request: Request) -> AuthUser:
    """Dependency that requires an authenticated user.

    Raises 401 if no valid session exists.  Use this on all endpoints that
    create, read, update, or delete user-owned resources.
    """
    user = get_current_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def optional_auth(request: Request) -> AuthUser | None:
    """Dependency that returns the current user if authenticated, else None.

    Use this on endpoints that behave differently for authenticated vs.
    anonymous users (e.g. showing legacy data only when no session exists).
    """
    return get_current_user(request)


def verify_csrf_token(request: Request) -> None:
    """Validate the CSRF token for state-changing requests.

    Compares the ``X-CSRF-Token`` header against the ``wl_csrf_token`` cookie.
    Raises 403 if they are missing or do not match.
    """
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_token or not header_token or cookie_token != header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or invalid",
        )


def set_session_cookie(response: Response, token: str, *, is_production: bool) -> None:
    """Attach the session cookie to ``response`` with secure attributes."""
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        secure=is_production,
        samesite="lax",
        path="/",
        max_age=SESSION_TTL_SECONDS,
    )


def clear_session_cookie(response: Response) -> None:
    """Clear the session cookie on ``response``."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def set_csrf_cookie(response: Response, token: str, *, is_production: bool) -> None:
    """Attach the CSRF token cookie to ``response``."""
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        httponly=False,  # frontend must read this
        secure=is_production,
        samesite="lax",
        path="/",
        max_age=SESSION_TTL_SECONDS,
    )


def clear_csrf_cookie(response: Response) -> None:
    """Clear the CSRF cookie on ``response``."""
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
