"""M73: Institution Management & Multi-Tenant Education Platform service layer.

Provides institution-scoped authorization and multi-tenant isolation:

* Institution CRUD (create, read, update, soft-delete, restore).
* Membership management (add, list, suspend, revoke).
* Invitations (create, accept, list).
* Departments and programs (institution hierarchy).
* Institution settings and plan entitlements.

All functions enforce server-side authorization:

* Only an active ``institution_admin`` may mutate institution resources.
* Membership is required to read institution resources.
* Invitations are email-verified and time-limited.

Guardian access from M72 is NOT extended to institutions — there is no
guardian/parent relationship architecture in this codebase, and none is
introduced here.

Existing user-owned resources (classrooms, courses, documents, quizzes,
notifications) remain user-scoped. Institutions are an overlay membership
layer; this service does NOT re-assign existing resources to institutions.
Cross-institution isolation is enforced by checking membership on every
read and admin role on every write.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.storage import (
    _check_institution_admin,
    accept_institution_invitation,
    add_institution_member,
    create_institution,
    create_institution_department,
    create_institution_invitation,
    create_institution_program,
    delete_institution,
    get_institution,
    get_institution_plan_entitlements,
    get_institution_settings,
    list_institution_departments,
    list_institution_invitations,
    list_institution_members,
    list_institution_programs,
    list_institutions,
    restore_institution,
    revoke_institution_member,
    set_institution_plan_entitlements,
    suspend_institution_member,
    update_institution,
    update_institution_setting,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


# ---------------------------------------------------------------------------
# Institution CRUD
# ---------------------------------------------------------------------------


def create_institution_service(
    database_path: Path,
    *,
    name: str,
    domain: str | None,
    created_by: str,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Create an institution and make the creator its institution_admin."""
    return create_institution(
        database_path,
        name=name,
        domain=domain,
        created_by=created_by,
        settings=settings,
    )


def get_institution_service(
    database_path: Path,
    *,
    institution_id: str,
    viewer_id: str | None = None,
) -> dict[str, Any] | None:
    """Return an institution if it exists and is active (or the viewer is
    an admin viewing a deleted/restorable institution)."""
    institution = get_institution(database_path, institution_id=institution_id)
    if institution is None:
        return None
    if institution["status"] == "deleted":
        # Only admins can see deleted institutions for restoration.
        if viewer_id is None:
            return None
        if not _check_institution_admin(database_path, institution_id, viewer_id):
            return None
    return institution


def list_institutions_service(
    database_path: Path,
    *,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """List active institutions, optionally filtered to a user's memberships."""
    return list_institutions(database_path, user_id=user_id)


def update_institution_service(
    database_path: Path,
    *,
    institution_id: str,
    editor_id: str,
    name: str | None = None,
    domain: str | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Update an institution. Editor must be an active institution_admin."""
    return update_institution(
        database_path,
        institution_id=institution_id,
        editor_id=editor_id,
        name=name,
        domain=domain,
        settings=settings,
    )


def delete_institution_service(
    database_path: Path,
    *,
    institution_id: str,
    editor_id: str,
) -> dict[str, Any] | None:
    """Soft-delete an institution. Editor must be an active institution_admin."""
    return delete_institution(
        database_path,
        institution_id=institution_id,
        editor_id=editor_id,
    )


def restore_institution_service(
    database_path: Path,
    *,
    institution_id: str,
    editor_id: str,
) -> dict[str, Any] | None:
    """Restore a soft-deleted institution."""
    return restore_institution(
        database_path,
        institution_id=institution_id,
        editor_id=editor_id,
    )


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


def add_institution_member_service(
    database_path: Path,
    *,
    institution_id: str,
    user_id: str,
    inviter_id: str,
    role: str = "learner",
) -> dict[str, Any] | None:
    """Add a user to an institution. Inviter must be an active
    institution_admin."""
    return add_institution_member(
        database_path,
        institution_id=institution_id,
        user_id=user_id,
        inviter_id=inviter_id,
        role=role,
    )


def list_institution_members_service(
    database_path: Path,
    *,
    institution_id: str,
    viewer_id: str,
) -> list[dict[str, Any]] | None:
    """List active members of an institution."""
    return list_institution_members(
        database_path,
        institution_id=institution_id,
        viewer_id=viewer_id,
    )


def suspend_institution_member_service(
    database_path: Path,
    *,
    institution_id: str,
    user_id: str,
    actor_id: str,
) -> dict[str, Any] | None:
    """Suspend a member. Actor must be an active institution_admin."""
    return suspend_institution_member(
        database_path,
        institution_id=institution_id,
        user_id=user_id,
        actor_id=actor_id,
    )


def revoke_institution_member_service(
    database_path: Path,
    *,
    institution_id: str,
    user_id: str,
    actor_id: str,
) -> dict[str, Any] | None:
    """Revoke a member. Actor must be an active institution_admin."""
    return revoke_institution_member(
        database_path,
        institution_id=institution_id,
        user_id=user_id,
        actor_id=actor_id,
    )


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


def create_institution_invitation_service(
    database_path: Path,
    *,
    institution_id: str,
    email: str,
    role: str,
    inviter_id: str,
    expires_in_days: int = 7,
) -> dict[str, Any] | None:
    """Create an invitation. Inviter must be an active institution_admin."""
    return create_institution_invitation(
        database_path,
        institution_id=institution_id,
        email=email,
        role=role,
        inviter_id=inviter_id,
        expires_in_days=expires_in_days,
    )


def accept_institution_invitation_service(
    database_path: Path,
    *,
    token: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Accept an invitation and join the institution."""
    return accept_institution_invitation(
        database_path,
        token=token,
        user_id=user_id,
    )


def list_institution_invitations_service(
    database_path: Path,
    *,
    institution_id: str,
    viewer_id: str,
) -> list[dict[str, Any]] | None:
    """List invitations. Viewer must be an active institution_admin."""
    return list_institution_invitations(
        database_path,
        institution_id=institution_id,
        viewer_id=viewer_id,
    )


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


def create_institution_department_service(
    database_path: Path,
    *,
    institution_id: str,
    name: str,
    description: str,
    head_user_id: str | None,
    creator_id: str,
) -> dict[str, Any] | None:
    """Create a department. Creator must be an active institution_admin."""
    return create_institution_department(
        database_path,
        institution_id=institution_id,
        name=name,
        description=description,
        head_user_id=head_user_id,
        creator_id=creator_id,
    )


def list_institution_departments_service(
    database_path: Path,
    *,
    institution_id: str,
    viewer_id: str,
) -> list[dict[str, Any]] | None:
    """List departments. Viewer must be a member."""
    return list_institution_departments(
        database_path,
        institution_id=institution_id,
        viewer_id=viewer_id,
    )


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------


def create_institution_program_service(
    database_path: Path,
    *,
    institution_id: str,
    department_id: str | None,
    name: str,
    description: str,
    academic_year: str | None,
    term: str | None,
    creator_id: str,
) -> dict[str, Any] | None:
    """Create a program. Creator must be an active institution_admin."""
    return create_institution_program(
        database_path,
        institution_id=institution_id,
        department_id=department_id,
        name=name,
        description=description,
        academic_year=academic_year,
        term=term,
        creator_id=creator_id,
    )


def list_institution_programs_service(
    database_path: Path,
    *,
    institution_id: str,
    viewer_id: str,
) -> list[dict[str, Any]] | None:
    """List programs. Viewer must be a member."""
    return list_institution_programs(
        database_path,
        institution_id=institution_id,
        viewer_id=viewer_id,
    )


# ---------------------------------------------------------------------------
# Settings & plan entitlements
# ---------------------------------------------------------------------------


def get_institution_settings_service(
    database_path: Path,
    *,
    institution_id: str,
    viewer_id: str,
) -> dict[str, Any] | None:
    """Get institution settings. Viewer must be a member."""
    return get_institution_settings(
        database_path,
        institution_id=institution_id,
        viewer_id=viewer_id,
    )


def update_institution_setting_service(
    database_path: Path,
    *,
    institution_id: str,
    setting_key: str,
    setting_value: str,
    editor_id: str,
) -> dict[str, Any] | None:
    """Update an institution setting. Editor must be an active
    institution_admin."""
    return update_institution_setting(
        database_path,
        institution_id=institution_id,
        setting_key=setting_key,
        setting_value=setting_value,
        editor_id=editor_id,
    )


def get_institution_plan_entitlements_service(
    database_path: Path,
    *,
    institution_id: str,
    viewer_id: str,
) -> dict[str, Any] | None:
    """Get plan entitlements. Viewer must be a member."""
    return get_institution_plan_entitlements(
        database_path,
        institution_id=institution_id,
        viewer_id=viewer_id,
    )


def set_institution_plan_entitlements_service(
    database_path: Path,
    *,
    institution_id: str,
    editor_id: str,
    plan: str,
    max_users: int | None = None,
    max_classrooms: int | None = None,
    max_courses: int | None = None,
    max_storage_mb: int | None = None,
    features: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Set plan entitlements. Editor must be an active institution_admin."""
    return set_institution_plan_entitlements(
        database_path,
        institution_id=institution_id,
        editor_id=editor_id,
        plan=plan,
        max_users=max_users,
        max_classrooms=max_classrooms,
        max_courses=max_courses,
        max_storage_mb=max_storage_mb,
        features=features,
    )


# ---------------------------------------------------------------------------
# Tenant isolation helpers
# ---------------------------------------------------------------------------


def require_institution_membership(
    database_path: Path,
    *,
    institution_id: str,
    user_id: str,
) -> bool:
    """Return True if the user is an active member of the institution."""
    from app.storage import _is_institution_member

    return _is_institution_member(
        database_path, institution_id=institution_id, user_id=user_id
    )


def require_institution_admin(
    database_path: Path,
    *,
    institution_id: str,
    user_id: str,
) -> bool:
    """Return True if the user is an active institution_admin."""
    from app.storage import _check_institution_admin

    return _check_institution_admin(
        database_path, institution_id=institution_id, user_id=user_id
    )
