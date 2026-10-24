"""Authentication routes for local, cookie-backed user sessions."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import (
    DUMMY_PASSWORD_HASH,
    SESSION_COOKIE_NAME,
    clear_csrf_cookie,
    clear_session_cookie,
    create_session,
    destroy_session,
    generate_csrf_token,
    get_current_user,
    get_session,
    hash_password,
    require_auth,
    set_csrf_cookie,
    set_session_cookie,
    verify_password,
)
from app.config import Settings
from app.schemas import CsrfTokenResponse, LogoutResponse, RegistrationResponse, SessionUserResponse
from app.storage import create_user, get_user_by_username

router = APIRouter(prefix="/api/v1/auth")


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post(
    "/register",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RegistrationResponse,
    tags=["auth"],
)
def register(payload: Credentials, request: Request) -> dict[str, str]:
    """Create an account without revealing whether a username already exists."""
    settings = _settings(request)
    password_hash = hash_password(payload.password)
    admin_flag = payload.username.strip().lower() in settings.configured_admin_usernames
    try:
        create_user(
            settings.database_path,
            payload.username,
            password_hash,
            is_admin=admin_flag,
        )
    except sqlite3.IntegrityError:
        pass
    return {"message": "If the account can be registered, you can try signing in."}


@router.post("/login", response_model=SessionUserResponse, tags=["auth"])
def login(payload: Credentials, request: Request, response: Response) -> dict[str, Any]:
    settings = _settings(request)
    user = get_user_by_username(settings.database_path, payload.username)
    stored_hash = user["password_hash"] if user is not None else DUMMY_PASSWORD_HASH
    password_matches = verify_password(payload.password, stored_hash)
    if user is None or not password_matches:
        raise _invalid_credentials()

    existing_token = request.cookies.get(SESSION_COOKIE_NAME)
    if existing_token:
        destroy_session(settings.database_path, existing_token)
    session_token, expires_at = create_session(
        settings.database_path,
        user["user_id"],
        ttl_seconds=settings.session_ttl_seconds,
    )
    set_session_cookie(
        response,
        session_token,
        is_production=settings.is_production,
        max_age=settings.session_ttl_seconds,
    )
    is_admin = bool(user["is_admin"]) or (
        user["username"].lower() in settings.configured_admin_usernames
    )
    return {
        "userId": user["user_id"],
        "username": user["username"],
        "expiresAt": expires_at.isoformat(),
        "isAdmin": is_admin,
    }


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid username or password.",
    )


@router.post("/logout", response_model=LogoutResponse, tags=["auth"])
def logout(request: Request, response: Response) -> dict[str, str]:
    settings = _settings(request)
    user = get_current_user(request)
    if user is None:
        raise _invalid_credentials()

    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if session_token:
        destroy_session(settings.database_path, session_token)
    clear_session_cookie(response, is_production=settings.is_production)
    clear_csrf_cookie(response, is_production=settings.is_production)
    return {"message": "Logged out."}


@router.get("/me", response_model=SessionUserResponse, tags=["auth"])
def current_user(request: Request) -> dict[str, Any]:
    user = require_auth(request)
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    session = (
        get_session(_settings(request).database_path, session_token) if session_token else None
    )
    if session is None:
        raise _invalid_credentials()
    is_admin = (
        user.is_admin or user.username.lower() in _settings(request).configured_admin_usernames
    )
    return {
        "userId": user.user_id,
        "username": user.username,
        "expiresAt": session["expires_at"],
        "isAdmin": is_admin,
    }


@router.get("/csrf-token", response_model=CsrfTokenResponse, tags=["auth"])
def csrf_token(request: Request, response: Response) -> dict[str, str]:
    settings = _settings(request)
    token = generate_csrf_token()
    set_csrf_cookie(
        response,
        token,
        is_production=settings.is_production,
        max_age=settings.session_ttl_seconds,
    )
    return {"csrfToken": token}
