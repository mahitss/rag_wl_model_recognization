"""M73: Institution Management & Multi-Tenant Education Platform API routes.

Endpoints:

* POST   /api/v1/institutions - Create institution
* GET    /api/v1/institutions - List institutions (optionally ?mine=true)
* GET    /api/v1/institutions/{institution_id} - Get institution
* PATCH  /api/v1/institutions/{institution_id} - Update institution (admin)
* DELETE /api/v1/institutions/{institution_id} - Soft-delete institution (admin)
* POST   /api/v1/institutions/{institution_id}/restore - Restore deleted institution (admin)

* POST   /api/v1/institutions/{institution_id}/members     - Add member (admin)
* GET    /api/v1/institutions/{institution_id}/members     - List members
* POST   /api/v1/institutions/{institution_id}/members/suspend - Suspend member (admin)
* POST   /api/v1/institutions/{institution_id}/members/revoke  - Revoke member (admin)

* POST   /api/v1/institutions/{institution_id}/invitations - Create invitation (admin)
* GET    /api/v1/institutions/{institution_id}/invitations - List invitations (admin)
* POST   /api/v1/invitations/accept                        - Accept invitation

* POST   /api/v1/institutions/{institution_id}/departments - Create department (admin)
* GET    /api/v1/institutions/{institution_id}/departments - List departments

* POST   /api/v1/institutions/{institution_id}/programs    - Create program (admin)
* GET    /api/v1/institutions/{institution_id}/programs    - List programs

* GET    /api/v1/institutions/{institution_id}/settings    - Get settings
* PATCH  /api/v1/institutions/{institution_id}/settings    - Update setting (admin)

* GET    /api/v1/institutions/{institution_id}/plan        - Get plan entitlements
* PATCH  /api/v1/institutions/{institution_id}/plan        - Set plan entitlements (admin)

All endpoints require authentication. Institution mutations require an active
institution_admin membership. Reads require active membership.
Guardian access is NOT implemented (see institutions.py for rationale).

CSRF protection is applied globally via CSRFProtectionMiddleware.
"""
# ruff: noqa: B008

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request

from app.auth import AuthUser, require_auth
from app.errors import ApiError
from app.institutions import (
    accept_institution_invitation_service,
    add_institution_member_service,
    create_institution_department_service,
    create_institution_invitation_service,
    create_institution_program_service,
    create_institution_service,
    delete_institution_service,
    get_institution_plan_entitlements_service,
    get_institution_service,
    get_institution_settings_service,
    list_institution_departments_service,
    list_institution_invitations_service,
    list_institution_members_service,
    list_institution_programs_service,
    list_institutions_service,
    restore_institution_service,
    revoke_institution_member_service,
    set_institution_plan_entitlements_service,
    suspend_institution_member_service,
    update_institution_service,
    update_institution_setting_service,
)
from app.schemas import (
    AcceptInvitationRequest,
    AddInstitutionMemberRequest,
    CreateDepartmentRequest,
    CreateInvitationRequest,
    CreateProgramRequest,
    InstitutionCreateRequest,
    InstitutionUpdateRequest,
    RevokeMemberRequest,
    SetPlanEntitlementsRequest,
    SuspendMemberRequest,
    UpdateInstitutionSettingRequest,
)

router = APIRouter(prefix="/api/v1", tags=["institutions"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


# ---------------------------------------------------------------------------
# Institution CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/institutions",
    response_model=dict[str, Any],
    status_code=201,
)
def create_institution_route(
    request: Request,
    payload: InstitutionCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a new institution. Creator becomes institution_admin."""
    result = create_institution_service(
        database_path=_database_path(request),
        name=payload.name,
        domain=payload.domain,
        created_by=current_user.user_id,
        settings=None,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid institution name or domain.",
        )
    return result


@router.get(
    "/institutions",
    response_model=dict[str, Any],
)
def list_institutions_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    mine: bool = Query(default=False),
) -> dict[str, Any]:
    """List active institutions. With ?mine=true, list only the current
    user's memberships."""
    institutions = list_institutions_service(
        database_path=_database_path(request),
        user_id=current_user.user_id if mine else None,
    )
    return {"institutions": institutions}


@router.get(
    "/institutions/{institution_id}",
    response_model=dict[str, Any],
)
def get_institution_route(
    request: Request,
    institution_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get an institution by ID."""
    result = get_institution_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        viewer_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            404,
            "INSTITUTION_NOT_FOUND",
            "Institution not found or access denied.",
        )
    return result


@router.patch(
    "/institutions/{institution_id}",
    response_model=dict[str, Any],
)
def update_institution_route(
    request: Request,
    institution_id: str = Path(...),
    payload: InstitutionUpdateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update an institution. Editor must be an active institution_admin."""
    result = update_institution_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        editor_id=current_user.user_id,
        name=payload.name,
        domain=payload.domain,
        settings=payload.settings,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid update or you are not an institution administrator.",
        )
    return result


@router.delete(
    "/institutions/{institution_id}",
    response_model=dict[str, Any],
)
def delete_institution_route(
    request: Request,
    institution_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Soft-delete an institution. Editor must be an active institution_admin."""
    result = delete_institution_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        editor_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not authorized to delete this institution or it is already deleted.",
        )
    return result


@router.post(
    "/institutions/{institution_id}/restore",
    response_model=dict[str, Any],
)
def restore_institution_route(
    request: Request,
    institution_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Restore a soft-deleted institution."""
    result = restore_institution_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        editor_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not authorized to restore this institution or it is not deleted.",
        )
    return result


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


@router.post(
    "/institutions/{institution_id}/members",
    response_model=dict[str, Any],
    status_code=201,
)
def add_institution_member_route(
    request: Request,
    institution_id: str = Path(...),
    payload: AddInstitutionMemberRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Add a user to an institution. Inviter must be an active institution_admin."""
    result = add_institution_member_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        user_id=payload.userId,
        inviter_id=current_user.user_id,
        role=payload.role,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid member input or you are not an institution administrator.",
        )
    return result


@router.get(
    "/institutions/{institution_id}/members",
    response_model=dict[str, Any],
)
def list_institution_members_route(
    request: Request,
    institution_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List active members of an institution. Viewer must be a member."""
    members = list_institution_members_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        viewer_id=current_user.user_id,
    )
    if members is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not a member of this institution.",
        )
    return {"members": members}


@router.post(
    "/institutions/{institution_id}/members/suspend",
    response_model=dict[str, Any],
)
def suspend_institution_member_route(
    request: Request,
    institution_id: str = Path(...),
    payload: SuspendMemberRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Suspend a member. Actor must be an active institution_admin."""
    result = suspend_institution_member_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        user_id=payload.userId,
        actor_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not authorized to suspend this member.",
        )
    return result


@router.post(
    "/institutions/{institution_id}/members/revoke",
    response_model=dict[str, Any],
)
def revoke_institution_member_route(
    request: Request,
    institution_id: str = Path(...),
    payload: RevokeMemberRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Revoke a member. Actor must be an active institution_admin."""
    result = revoke_institution_member_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        user_id=payload.userId,
        actor_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not authorized to revoke this member.",
        )
    return result


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


@router.post(
    "/institutions/{institution_id}/invitations",
    response_model=dict[str, Any],
    status_code=201,
)
def create_institution_invitation_route(
    request: Request,
    institution_id: str = Path(...),
    payload: CreateInvitationRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create an invitation. Inviter must be an active institution_admin."""
    result = create_institution_invitation_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        email=payload.email,
        role=payload.role,
        inviter_id=current_user.user_id,
        expires_in_days=payload.expiresInDays,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid invitation input or you are not an institution administrator.",
        )
    return result


@router.get(
    "/institutions/{institution_id}/invitations",
    response_model=dict[str, Any],
)
def list_institution_invitations_route(
    request: Request,
    institution_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List invitations. Viewer must be an active institution_admin."""
    invitations = list_institution_invitations_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        viewer_id=current_user.user_id,
    )
    if invitations is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not an administrator of this institution.",
        )
    return {"invitations": invitations}


@router.post(
    "/invitations/accept",
    response_model=dict[str, Any],
)
def accept_invitation_route(
    request: Request,
    payload: AcceptInvitationRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Accept an invitation and join the institution."""
    result = accept_institution_invitation_service(
        database_path=_database_path(request),
        token=payload.token,
        user_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid, expired, or already-accepted invitation, or email mismatch.",
        )
    return result


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


@router.post(
    "/institutions/{institution_id}/departments",
    response_model=dict[str, Any],
    status_code=201,
)
def create_institution_department_route(
    request: Request,
    institution_id: str = Path(...),
    payload: CreateDepartmentRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a department. Creator must be an active institution_admin."""
    result = create_institution_department_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        name=payload.name,
        description=payload.description,
        head_user_id=payload.headUserId,
        creator_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid department input or you are not an institution administrator.",
        )
    return result


@router.get(
    "/institutions/{institution_id}/departments",
    response_model=dict[str, Any],
)
def list_institution_departments_route(
    request: Request,
    institution_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List departments. Viewer must be a member."""
    departments = list_institution_departments_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        viewer_id=current_user.user_id,
    )
    if departments is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not a member of this institution.",
        )
    return {"departments": departments}


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------


@router.post(
    "/institutions/{institution_id}/programs",
    response_model=dict[str, Any],
    status_code=201,
)
def create_institution_program_route(
    request: Request,
    institution_id: str = Path(...),
    payload: CreateProgramRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Create a program. Creator must be an active institution_admin."""
    result = create_institution_program_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        department_id=payload.departmentId,
        name=payload.name,
        description=payload.description,
        academic_year=payload.academicYear,
        term=payload.term,
        creator_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid program input or you are not an institution administrator.",
        )
    return result


@router.get(
    "/institutions/{institution_id}/programs",
    response_model=dict[str, Any],
)
def list_institution_programs_route(
    request: Request,
    institution_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List programs. Viewer must be a member."""
    programs = list_institution_programs_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        viewer_id=current_user.user_id,
    )
    if programs is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not a member of this institution.",
        )
    return {"programs": programs}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@router.get(
    "/institutions/{institution_id}/settings",
    response_model=dict[str, Any],
)
def get_institution_settings_route(
    request: Request,
    institution_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get institution settings. Viewer must be a member."""
    settings = get_institution_settings_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        viewer_id=current_user.user_id,
    )
    if settings is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not a member of this institution.",
        )
    return settings


@router.patch(
    "/institutions/{institution_id}/settings",
    response_model=dict[str, Any],
)
def update_institution_setting_route(
    request: Request,
    institution_id: str = Path(...),
    payload: UpdateInstitutionSettingRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update an institution setting. Editor must be an active institution_admin."""
    result = update_institution_setting_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        setting_key=payload.settingKey,
        setting_value=payload.settingValue,
        editor_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid setting input or you are not an institution administrator.",
        )
    return result


# ---------------------------------------------------------------------------
# Plan entitlements
# ---------------------------------------------------------------------------


@router.get(
    "/institutions/{institution_id}/plan",
    response_model=dict[str, Any],
)
def get_institution_plan_route(
    request: Request,
    institution_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get plan entitlements. Viewer must be a member."""
    plan = get_institution_plan_entitlements_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        viewer_id=current_user.user_id,
    )
    if plan is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not a member of this institution.",
        )
    return plan


@router.patch(
    "/institutions/{institution_id}/plan",
    response_model=dict[str, Any],
)
def set_institution_plan_route(
    request: Request,
    institution_id: str = Path(...),
    payload: SetPlanEntitlementsRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Set plan entitlements. Editor must be an active institution_admin."""
    result = set_institution_plan_entitlements_service(
        database_path=_database_path(request),
        institution_id=institution_id,
        editor_id=current_user.user_id,
        plan=payload.plan,
        max_users=payload.maxUsers,
        max_classrooms=payload.maxClassrooms,
        max_courses=payload.maxCourses,
        max_storage_mb=payload.maxStorageMb,
        features=payload.features,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid plan input or you are not an institution administrator.",
        )
    return result
