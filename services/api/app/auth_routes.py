"""Authentication routes: register, login, logout, current user.

All endpoints are publicly accessible (no prior session required) except
``/auth/me`` which requires a valid session, and ``/auth/logout`` which
requires a valid session cookie.

CSRF protection applies to ``POST /auth/login`` and ``POST /auth/logout``
because they mutate session state.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.auth import (
    AuthUser,
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    clear_csrf_cookie,
    clear_session_cookie,
    create_session,
    destroy_session,
    generate_csrf_token,
    get_current_user,
    hash_password,
    require_auth,
    set_csrf_cookie,
    set_session_cookie,
    verify_csrf_token,
    verify_password,
)
from app.config import Settings, get_settings
from app.errors import ApiError
from app.storage import (
    create_user,
    get_user_by_id,
    get_user_by_username,
)

router = APIRouter(prefix="/api/v1/auth")


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post("/register", status_code=201, tags=["auth"])
async def register(request: Request) -> dict[str, Any]:
    """Register a new user account.

    Request body: ``{"username": str, "password": str}``
    Username must be 3–64 characters, password must be at least 8 characters.
    """
    settings = _settings(request)
    database_path = settings.database_path

    try:
        body = await request.json()
    except Exception as exc:
        raise ApiError(422, "INVALID_REQUEST", "Invalid JSON body") from exc

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or len(username) < 3 or len(username) > 64:
        raise ApiError(422, "INVALID_USERNAME", "Username must be 3–64 characters")
    if not password or len(password) < 8:
        raise ApiError(422, "INVALID_PASSWORD", "Password must be at least 8 characters")

    if get_user_by_username(database_path, username) is not None:
        raise ApiError(409, "USERNAME_TAKEN", "Username is already registered")

    password_hash = hash_password(password)
    user_id = create_user(database_path, username, password_hash)

    return {"userId": user_id, "username": username}


@router.post("/login", tags=["auth"])
async def login(request: Request, response: Response) -> dict[str, Any]:
    """Log in and establish a session.

    Request body: ``{"username": str, "password": str}``
    On success, sets the session cookie and CSRF token cookie.
    """
    settings = _settings(request)
    database_path = settings.database_path

    try:
        body = await request.json()
    except Exception as exc:
        raise ApiError(422, "INVALID_REQUEST", "Invalid JSON body") from exc

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        raise ApiError(422, "INVALID_CREDENTIALS", "Username and password are required")

    user = get_user_by_username(database_path, username)
    if user is None or not verify_password(password, user["password_hash"]):
        # Generic failure message to prevent account enumeration
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid username or password")

    session_token, expires_at = create_session(database_path, user["user_id"])
    csrf_token = generate_csrf_token()

    set_session_cookie(response, session_token, is_production=settings.is_production)
    set_csrf_cookie(response, csrf_token, is_production=settings.is_production)

    return {
        "userId": user["user_id"],
        "username": user["username"],
        "expiresAt": expires_at.isoformat(),
    }


@router.post("/logout", tags=["auth"])
async def logout(request: Request, response: Response) -> dict[str, Any]:
    """Log out and destroy the current session.

    Requires a valid session cookie.  Clears both session and CSRF cookies.
    """
    settings = _settings(request)
    database_path = settings.database_path

    user = get_current_user(request)
    if user is None:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required")

    session_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if session_cookie:
        destroy_session(database_path, session_cookie)

    clear_session_cookie(response)
    clear_csrf_cookie(response)

    return {"message": "Logged out"}


@router.get("/me", tags=["auth"])
async def current_user(request: Request) -> dict[str, Any]:
    """Return the currently authenticated user's profile.

    Requires a valid session cookie.
    """
    user = require_auth(request)
    return {"userId": user.user_id, "username": user.username}


@router.get("/csrf-token", tags=["auth"])
async def csrf_token(request: Request, response: Response) -> dict[str, Any]:
    """Return a CSRF token for the frontend to use on state-changing requests.

    The token is also set as a cookie so the frontend can read it and echo it
    back via the ``X-CSRF-Token`` header.
    """
    settings = _settings(request)
    token = generate_csrf_token()
    set_csrf_cookie(response, token, is_production=settings.is_production)
    return {"csrfToken": token}
