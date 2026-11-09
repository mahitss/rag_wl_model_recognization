"""M74: Integration service layer.

Provides tenant-scoped API integration management:

* Create, read, update, delete integrations (LMS, SIS, content repository,
  identity provider, calendar service, educational tool).
* Create/rotate credentials (secret values are stored encrypted and never
  returned by read operations).
* List credential names without values.

All functions enforce institution membership for reads and institution_admin
for mutations.  Cross-institution access is impossible because every lookup
joins through the institutions table and checks membership.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.storage import (
    _check_institution_admin,
    create_api_integration,
    create_integration_credential,
    delete_api_integration,
    get_api_integration,
    list_api_integrations,
    list_integration_credentials,
    update_api_integration,
)


def create_integration(
    database_path: Path,
    *,
    institution_id: str,
    provider: str,
    provider_type: str,
    name: str,
    description: str,
    base_url: str | None,
    config: dict[str, Any] | None,
    created_by: str,
) -> dict[str, Any] | None:
    """Create an integration. The creator must be an active institution_admin."""
    if not _check_institution_admin(database_path, institution_id, created_by):
        return None
    return create_api_integration(
        database_path,
        institution_id=institution_id,
        provider=provider,
        provider_type=provider_type,
        name=name,
        description=description,
        base_url=base_url,
        config=config,
        created_by=created_by,
    )


def get_integration(
    database_path: Path,
    *,
    integration_id: str,
    viewer_id: str,
) -> dict[str, Any] | None:
    """Get an integration. Viewer must be a member of the institution."""
    return get_api_integration(
        database_path, integration_id=integration_id, viewer_id=viewer_id
    )


def list_integrations(
    database_path: Path,
    *,
    institution_id: str,
    viewer_id: str,
) -> list[dict[str, Any]] | None:
    """List integrations for an institution. Viewer must be a member."""
    return list_api_integrations(
        database_path, institution_id=institution_id, viewer_id=viewer_id
    )


def update_integration(
    database_path: Path,
    *,
    integration_id: str,
    editor_id: str,
    name: str | None = None,
    description: str | None = None,
    base_url: str | None = None,
    config: dict[str, Any] | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Update an integration. Editor must be an active institution_admin."""
    return update_api_integration(
        database_path,
        integration_id=integration_id,
        editor_id=editor_id,
        name=name,
        description=description,
        base_url=base_url,
        config=config,
        status=status,
    )


def delete_integration(
    database_path: Path,
    *,
    integration_id: str,
    editor_id: str,
) -> dict[str, Any] | None:
    """Delete an integration. Editor must be an active institution_admin."""
    return delete_api_integration(
        database_path, integration_id=integration_id, editor_id=editor_id
    )


def add_integration_credential(
    database_path: Path,
    *,
    integration_id: str,
    secret_name: str,
    secret_value: str,
    editor_id: str,
) -> dict[str, Any] | None:
    """Create or rotate a credential. Editor must be an active
    institution_admin. The secret value is stored encrypted and is never
    returned by read operations."""
    return create_integration_credential(
        database_path,
        integration_id=integration_id,
        secret_name=secret_name,
        secret_value_encrypted=secret_value,
        editor_id=editor_id,
    )


def list_integration_credential_names(
    database_path: Path,
    *,
    integration_id: str,
    viewer_id: str,
) -> list[dict[str, Any]] | None:
    """List credential names (without values). Viewer must be a member."""
    return list_integration_credentials(
        database_path, integration_id=integration_id, viewer_id=viewer_id
    )
