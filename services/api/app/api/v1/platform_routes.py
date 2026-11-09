"""M74: API Platform & Third-Party Integration Ecosystem API routes.

Endpoints:

* GET    /api/v1/platform/version               - API version info
* GET    /api/v1/platform/health                - Health check
* POST   /api/v1/platform/integrations          - Create integration (admin)
* GET    /api/v1/platform/integrations          - List integrations (member)
* GET    /api/v1/platform/integrations/{integration_id} - Get integration
* PATCH  /api/v1/platform/integrations/{integration_id} - Update integration (admin)
* DELETE /api/v1/platform/integrations/{integration_id} - Delete integration (admin)
* POST   /api/v1/platform/integrations/{integration_id}/credentials - Add credential (admin)
* GET    /api/v1/platform/integrations/{integration_id}/credentials - List credential names (member)
* POST   /api/v1/platform/webhooks              - Register webhook endpoint (admin)
* GET    /api/v1/platform/webhooks              - List webhook endpoints (member)
* GET    /api/v1/platform/webhooks/{endpoint_id} - Get webhook endpoint
* PATCH  /api/v1/platform/webhooks/{endpoint_id} - Update webhook endpoint (admin)
* DELETE /api/v1/platform/webhooks/{endpoint_id} - Delete webhook endpoint (admin)
* POST   /api/v1/platform/webhooks/{endpoint_id}/rotate-secret - Rotate secret (admin)
* GET    /api/v1/platform/webhooks/events       - List webhook events (member)
* GET    /api/v1/platform/webhooks/{endpoint_id}/deliveries - List deliveries (member)
* POST   /api/v1/platform/webhooks/deliver      - Deliver pending events (admin)
* GET    /api/v1/platform/idempotency/cleanup   - Cleanup expired idempotency keys (admin)

All endpoints require authentication. Integration/webhook mutations require
an active institution_admin membership. Reads require active membership.

Idempotency: POST/PATCH/DELETE endpoints accept an ``Idempotency-Key``
header.  Repeated requests with the same key and equivalent body return the
cached response instead of re-executing.

CSRF protection is applied globally via CSRFProtectionMiddleware.
"""
# ruff: noqa: B008

from __future__ import annotations

import json
from contextlib import closing
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Path, Query, Request

from app.api_platform import (
    api_health,
    api_version_info,
    check_idempotency,
    cleanup_idempotency_keys,
    paginate,
    paginate_slice,
    store_idempotency_response,
)
from app.auth import AuthUser, require_auth
from app.errors import ApiError
from app.integrations import (
    add_integration_credential,
    create_integration,
    delete_integration,
    get_integration,
    list_integration_credential_names,
    list_integrations,
    update_integration,
)
from app.schemas import (
    IntegrationCreateRequest,
    IntegrationCredentialCreateRequest,
    IntegrationUpdateRequest,
    WebhookEndpointCreateRequest,
    WebhookEndpointUpdateRequest,
    WebhookSecretRotateRequest,
)
from app.storage import (
    connect_database,
)
from app.storage import (
    delete_webhook_endpoint as _delete_webhook_endpoint_storage,
)
from app.storage import (
    get_webhook_endpoint as _get_webhook_endpoint_storage,
)
from app.webhooks import (
    _is_webhook_admin,
    deliver_pending_events,
    list_webhook_deliveries_service,
    list_webhook_endpoints_service,
    list_webhook_events_service,
    register_webhook_endpoint,
    rotate_webhook_secret_service,
    update_webhook_endpoint_service,
)

router = APIRouter(prefix="/api/v1/platform", tags=["platform"])


def _database_path(request: Request):
    return request.app.state.settings.database_path


def _require_membership(
    database_path, institution_id: str, viewer_id: str
) -> None:
    """Raise 403 if the viewer is not a member of the institution."""
    from app.institutions import _is_institution_member

    if not _is_institution_member(database_path, institution_id, viewer_id):
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not a member of this institution.",
        )


def _error_response(status_code: int, code: str, message: str, details=None, request_id=None):
    from app.api.v1.routes import make_error_response
    return make_error_response(
        status_code, code, message, details, request_id=request_id
    )


# ---------------------------------------------------------------------------
# Version & health
# ---------------------------------------------------------------------------


@router.get("/version", response_model=dict[str, Any])
def get_api_version() -> dict[str, Any]:
    """Return API version information."""
    return api_version_info()


@router.get("/health", response_model=dict[str, Any])
def get_api_health() -> dict[str, Any]:
    """Return API health status."""
    return api_health()


# ---------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------


@router.post(
    "/integrations",
    response_model=dict[str, Any],
    status_code=201,
)
def create_integration_route(
    request: Request,
    payload: IntegrationCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Create an API integration. Requires institution_admin membership.
    The institution context comes from the caller's active membership."""
    database_path = _database_path(request)
    from app.institutions import list_institutions_service

    institutions = list_institutions_service(
        database_path, user_id=current_user.user_id
    )
    if not institutions:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not a member of any institution.",
        )
    # Use the caller's first active institution as context.
    institution_id = institutions[0]["institutionId"]

    # Idempotency check.
    body_bytes = json.dumps(
        payload.model_dump(), sort_keys=True
    ).encode("utf-8")
    if idempotency_key:
        cached = check_idempotency(
            database_path,
            idempotency_key=idempotency_key,
            user_id=current_user.user_id,
            method="POST",
            path=request.url.path,
            body=body_bytes,
        )
        if cached is not None:
            return cached["responseBody"]

    result = create_integration(
        database_path,
        institution_id=institution_id,
        provider=payload.provider,
        provider_type=payload.providerType,
        name=payload.name,
        description=payload.description,
        base_url=payload.baseUrl,
        config=payload.config,
        created_by=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid integration input or you are not an institution administrator.",
        )
    if idempotency_key:
        store_idempotency_response(
            database_path,
            idempotency_key=idempotency_key,
            user_id=current_user.user_id,
            method="POST",
            path=request.url.path,
            body=body_bytes,
            response_status=201,
            response_body=result,
        )
    return result


@router.get("/integrations", response_model=dict[str, Any])
def list_integrations_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=1000),
) -> dict[str, Any]:
    """List integrations for the caller's institutions."""
    database_path = _database_path(request)
    from app.institutions import list_institutions_service

    institutions = list_institutions_service(
        database_path, user_id=current_user.user_id
    )
    all_items: list[dict[str, Any]] = []
    for inst in institutions:
        items = list_integrations(
            database_path,
            institution_id=inst["institutionId"],
            viewer_id=current_user.user_id,
        )
        if items:
            all_items.extend(items)

    total = len(all_items)
    page_meta = paginate(total_items=total, page=page, page_size=page_size)
    sliced = paginate_slice(all_items, page=page, page_size=page_size)
    return {
        "items": sliced,
        "total": total,
        "page": page_meta["page"],
        "pageSize": page_meta["pageSize"],
        "totalPages": page_meta["totalPages"],
    }


@router.get("/integrations/{integration_id}", response_model=dict[str, Any])
def get_integration_route(
    request: Request,
    integration_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get an integration by ID."""
    database_path = _database_path(request)
    result = get_integration(
        database_path, integration_id=integration_id, viewer_id=current_user.user_id
    )
    if result is None:
        raise ApiError(
            404,
            "INTEGRATION_NOT_FOUND",
            "Integration not found or access denied.",
        )
    return result


@router.patch("/integrations/{integration_id}", response_model=dict[str, Any])
def update_integration_route(
    request: Request,
    integration_id: str = Path(...),
    payload: IntegrationUpdateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Update an integration. Requires institution_admin membership."""
    database_path = _database_path(request)
    from app.storage import get_api_integration

    existing = get_api_integration(
        database_path, integration_id=integration_id, viewer_id=current_user.user_id
    )
    if existing is None:
        raise ApiError(
            404,
            "INTEGRATION_NOT_FOUND",
            "Integration not found or access denied.",
        )
    body_bytes = json.dumps(
        payload.model_dump(exclude_unset=True), sort_keys=True
    ).encode("utf-8")
    if idempotency_key:
        cached = check_idempotency(
            database_path,
            idempotency_key=idempotency_key,
            user_id=current_user.user_id,
            method="PATCH",
            path=request.url.path,
            body=body_bytes,
        )
        if cached is not None:
            return cached["responseBody"]

    result = update_integration(
        database_path,
        integration_id=integration_id,
        editor_id=current_user.user_id,
        name=payload.name,
        description=payload.description,
        base_url=payload.baseUrl,
        config=payload.config,
        status=payload.status,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid update or you are not an institution administrator.",
        )
    if idempotency_key:
        store_idempotency_response(
            database_path,
            idempotency_key=idempotency_key,
            user_id=current_user.user_id,
            method="PATCH",
            path=request.url.path,
            body=body_bytes,
            response_status=200,
            response_body=result,
        )
    return result


@router.delete("/integrations/{integration_id}", response_model=dict[str, Any])
def delete_integration_route(
    request: Request,
    integration_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Delete an integration. Requires institution_admin membership."""
    database_path = _database_path(request)
    from app.storage import get_api_integration

    existing = get_api_integration(
        database_path, integration_id=integration_id, viewer_id=current_user.user_id
    )
    if existing is None:
        raise ApiError(
            404,
            "INTEGRATION_NOT_FOUND",
            "Integration not found or access denied.",
        )

    body_bytes = b""
    if idempotency_key:
        cached = check_idempotency(
            database_path,
            idempotency_key=idempotency_key,
            user_id=current_user.user_id,
            method="DELETE",
            path=request.url.path,
            body=body_bytes,
        )
        if cached is not None:
            return cached["responseBody"]

    result = delete_integration(
        database_path, integration_id=integration_id, editor_id=current_user.user_id
    )
    if result is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not authorized to delete this integration.",
        )
    if idempotency_key:
        store_idempotency_response(
            database_path,
            idempotency_key=idempotency_key,
            user_id=current_user.user_id,
            method="DELETE",
            path=request.url.path,
            body=body_bytes,
            response_status=200,
            response_body=result,
        )
    return result


@router.post(
    "/integrations/{integration_id}/credentials",
    response_model=dict[str, Any],
    status_code=201,
)
def add_integration_credential_route(
    request: Request,
    integration_id: str = Path(...),
    payload: IntegrationCredentialCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Add or rotate a credential for an integration. Requires institution_admin."""
    database_path = _database_path(request)
    from app.storage import get_api_integration

    existing = get_api_integration(
        database_path, integration_id=integration_id, viewer_id=current_user.user_id
    )
    if existing is None:
        raise ApiError(
            404,
            "INTEGRATION_NOT_FOUND",
            "Integration not found or access denied.",
        )

    result = add_integration_credential(
        database_path,
        integration_id=integration_id,
        secret_name=payload.secretName,
        secret_value=payload.secretValue,
        editor_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid credential input or you are not an institution administrator.",
        )
    return result


@router.get(
    "/integrations/{integration_id}/credentials",
    response_model=dict[str, Any],
)
def list_integration_credentials_route(
    request: Request,
    integration_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """List credential names (without values) for an integration."""
    database_path = _database_path(request)
    from app.storage import get_api_integration

    existing = get_api_integration(
        database_path, integration_id=integration_id, viewer_id=current_user.user_id
    )
    if existing is None:
        raise ApiError(
            404,
            "INTEGRATION_NOT_FOUND",
            "Integration not found or access denied.",
        )

    credentials = list_integration_credential_names(
        database_path,
        integration_id=integration_id,
        viewer_id=current_user.user_id,
    )
    if credentials is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not a member of this institution.",
        )
    return {"credentials": credentials}


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


@router.post("/webhooks", response_model=dict[str, Any], status_code=201)
def register_webhook_endpoint_route(
    request: Request,
    payload: WebhookEndpointCreateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Register a webhook endpoint. Requires institution_admin membership."""
    database_path = _database_path(request)
    from app.institutions import list_institutions_service

    institutions = list_institutions_service(
        database_path, user_id=current_user.user_id
    )
    if not institutions:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not a member of any institution.",
        )
    institution_id = institutions[0]["institutionId"]

    result = register_webhook_endpoint(
        database_path,
        institution_id=institution_id,
        url=payload.url,
        secret=payload.secret,
        event_types=payload.eventTypes,
        creator_id=current_user.user_id,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid webhook input or you are not an institution administrator.",
        )
    return result


@router.get("/webhooks", response_model=dict[str, Any])
def list_webhook_endpoints_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=1000),
) -> dict[str, Any]:
    """List webhook endpoints for the caller's institutions."""
    database_path = _database_path(request)
    from app.institutions import list_institutions_service

    institutions = list_institutions_service(
        database_path, user_id=current_user.user_id
    )
    all_items: list[dict[str, Any]] = []
    for inst in institutions:
        items = list_webhook_endpoints_service(
            database_path,
            institution_id=inst["institutionId"],
            viewer_id=current_user.user_id,
        )
        if items:
            all_items.extend(items)

    total = len(all_items)
    page_meta = paginate(total_items=total, page=page, page_size=page_size)
    sliced = paginate_slice(all_items, page=page, page_size=page_size)
    return {
        "items": sliced,
        "total": total,
        "page": page_meta["page"],
        "pageSize": page_meta["pageSize"],
        "totalPages": page_meta["totalPages"],
    }


@router.get("/webhooks/{endpoint_id}", response_model=dict[str, Any])
def get_webhook_endpoint_route(
    request: Request,
    endpoint_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Get a webhook endpoint by ID."""
    database_path = _database_path(request)
    result = _get_webhook_endpoint_storage(
        database_path, endpoint_id=endpoint_id, viewer_id=current_user.user_id
    )
    if result is None:
        raise ApiError(
            404,
            "WEBHOOK_NOT_FOUND",
            "Webhook endpoint not found or access denied.",
        )
    return result


@router.patch("/webhooks/{endpoint_id}", response_model=dict[str, Any])
def update_webhook_endpoint_route(
    request: Request,
    endpoint_id: str = Path(...),
    payload: WebhookEndpointUpdateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Update a webhook endpoint. Requires institution_admin membership."""
    database_path = _database_path(request)
    if not _is_webhook_admin(database_path, endpoint_id, current_user.user_id):
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not authorized to update this webhook endpoint.",
        )
    result = update_webhook_endpoint_service(
        database_path,
        endpoint_id=endpoint_id,
        editor_id=current_user.user_id,
        url=payload.url,
        secret=payload.secret,
        active=payload.active,
        event_types=payload.eventTypes,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid webhook update input.",
        )
    return result


@router.delete("/webhooks/{endpoint_id}", response_model=dict[str, Any])
def delete_webhook_endpoint_route(
    request: Request,
    endpoint_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Delete a webhook endpoint. Requires institution_admin membership."""
    database_path = _database_path(request)
    if not _is_webhook_admin(database_path, endpoint_id, current_user.user_id):
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not authorized to delete this webhook endpoint.",
        )
    result = _delete_webhook_endpoint_storage(
        database_path, endpoint_id=endpoint_id, editor_id=current_user.user_id
    )
    if result is None:
        raise ApiError(
            404,
            "WEBHOOK_NOT_FOUND",
            "Webhook endpoint not found or access denied.",
        )
    return result


@router.post(
    "/webhooks/{endpoint_id}/rotate-secret",
    response_model=dict[str, Any],
)
def rotate_webhook_secret_route(
    request: Request,
    endpoint_id: str = Path(...),
    payload: WebhookSecretRotateRequest = Body(...),
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Rotate the webhook signing secret. Requires institution_admin."""
    database_path = _database_path(request)
    if not _is_webhook_admin(database_path, endpoint_id, current_user.user_id):
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not authorized to rotate this webhook secret.",
        )
    result = rotate_webhook_secret_service(
        database_path,
        endpoint_id=endpoint_id,
        editor_id=current_user.user_id,
        new_secret=payload.newSecret,
    )
    if result is None:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "Invalid secret input.",
        )
    return result


@router.get("/webhooks/events", response_model=dict[str, Any])
def list_webhook_events_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=1000),
) -> dict[str, Any]:
    """List webhook events for the caller's institutions."""
    database_path = _database_path(request)
    from app.institutions import list_institutions_service

    institutions = list_institutions_service(
        database_path, user_id=current_user.user_id
    )
    all_items: list[dict[str, Any]] = []
    for inst in institutions:
        items = list_webhook_events_service(
            database_path,
            institution_id=inst["institutionId"],
            viewer_id=current_user.user_id,
            limit=10000,
            offset=0,
        )
        if items:
            all_items.extend(items)

    total = len(all_items)
    page_meta = paginate(total_items=total, page=page, page_size=page_size)
    sliced = paginate_slice(all_items, page=page, page_size=page_size)
    return {
        "items": sliced,
        "total": total,
        "page": page_meta["page"],
        "pageSize": page_meta["pageSize"],
        "totalPages": page_meta["totalPages"],
    }


@router.get(
    "/webhooks/{endpoint_id}/deliveries",
    response_model=dict[str, Any],
)
def list_webhook_deliveries_route(
    request: Request,
    endpoint_id: str = Path(...),
    current_user: AuthUser = Depends(require_auth),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=1000),
) -> dict[str, Any]:
    """List webhook deliveries for an endpoint."""
    database_path = _database_path(request)
    # Verify access via the endpoint's institution.

    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            "SELECT institution_id FROM webhook_endpoints WHERE endpoint_id = ?",
            (endpoint_id,),
        ).fetchone()
    if row is None:
        raise ApiError(
            404,
            "WEBHOOK_NOT_FOUND",
            "Webhook endpoint not found.",
        )
    institution_id = row["institution_id"]
    _require_membership(database_path, institution_id, current_user.user_id)

    deliveries = list_webhook_deliveries_service(
        database_path,
        endpoint_id=endpoint_id,
        viewer_id=current_user.user_id,
        limit=10000,
        offset=0,
    )
    if deliveries is None:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not a member of this institution.",
        )

    total = len(deliveries)
    page_meta = paginate(total_items=total, page=page, page_size=page_size)
    sliced = paginate_slice(deliveries, page=page, page_size=page_size)
    return {
        "items": sliced,
        "total": total,
        "page": page_meta["page"],
        "pageSize": page_meta["pageSize"],
        "totalPages": page_meta["totalPages"],
    }


@router.post("/webhooks/deliver", response_model=dict[str, Any])
def deliver_webhooks_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Deliver pending webhook events. Requires institution_admin membership
    of at least one institution."""
    database_path = _database_path(request)
    from app.institutions import list_institutions_service

    institutions = list_institutions_service(
        database_path, user_id=current_user.user_id
    )
    if not institutions:
        raise ApiError(
            403,
            "ACCESS_DENIED",
            "You are not a member of any institution.",
        )
    # Deliver events for all institutions the user administers.
    results = []
    for inst in institutions:
        result = deliver_pending_events(
            database_path, institution_id=inst["institutionId"]
        )
        results.append(
            {
                "institutionId": inst["institutionId"],
                "eventsProcessed": result["eventsProcessed"],
                "deliveriesAttempted": result["deliveriesAttempted"],
                "deliveriesSucceeded": result["deliveriesSucceeded"],
            }
        )
    return {"results": results}


# ---------------------------------------------------------------------------
# Idempotency maintenance
# ---------------------------------------------------------------------------


@router.post("/idempotency/cleanup", response_model=dict[str, Any])
def cleanup_idempotency_route(
    request: Request,
    current_user: AuthUser = Depends(require_auth),
) -> dict[str, Any]:
    """Clean up expired idempotency keys. Requires authentication."""
    database_path = _database_path(request)
    removed = cleanup_idempotency_keys(database_path)
    return {"removed": removed}
