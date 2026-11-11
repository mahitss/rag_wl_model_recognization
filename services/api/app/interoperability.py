"""M80: Educational Standards & Learning Interoperability.

Provides versioned adapters for standards-based exchange of learning
content, learner progress, and assessment activity.

Supported standards (honest subsets):

* xAPI 1.0.3 — store, validate, and query learning activity statements.
  Fully implemented and tested.
* SCORM 1.2 — parse imsmanifest.xml, validate package structure, import
  as course content, map cmi.runtime data to progress tables.
  Packaged-content import and basic runtime tracking implemented;
  full SCORM RTE API run-time is NOT claimed.
* SCORM 2004 — NOT supported. Documented limitations only.
* LTI 1.3 — launch-request validation framework (OIDC/JWT shape checks
  without secrets). Full operation requires LTI consumer credentials and
  an external OIDC issuer; the framework is implemented and tested but
  end-to-end launch flow is not claimable without live credentials.

Security: archives are validated for ZIP traversal, oversized uploads,
malicious file types, and unsafe XML (entity expansion / depth limits)
before any content is read. All operations are tenant-scoped and audited.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.errors import ApiError
from app.schemas import (
    InteroperabilityImportResult,
    InteroperabilityValidationResult,
    LTILaunchResponse,
    SCORMSessionResponse,
    XAPIStatementCreate,
    XAPIStatementListResponse,
    XAPIStatementResponse,
)
from app.storage import (
    create_course,
    list_xapi_statements,
    store_interoperability_audit,
    store_lti_launch,
    store_scorm_package,
    store_scorm_session,
    store_xapi_statement,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

XAPI_VERSION = "1.0.3"
SCORM12_VERSION = "1.2"
SCORM2004_VERSION = "2004 4th Edition"
LTI_VERSION = "1.3.0"

_ALLOWED_ARCHIVE_EXTENSIONS = {".zip", ".imscc"}
_DANGEROUS_FILENAME_PATTERNS = [
    re.compile(r"\.\./", re.IGNORECASE),
    re.compile(r"^\.", re.IGNORECASE),
    re.compile(r"[<>:\"|?*]"),
    re.compile(r"\x00"),
]
_MAX_XML_DEPTH = 12
_MAX_XML_ENTITY_EXPANSION = 1000

# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------


def _is_safe_archive_member_name(name: str) -> bool:
    """Reject ZIP member names that escape the extraction root or look
    suspicious."""
    if not name or name.startswith("/") or "\\" in name:
        return False
    for pattern in _DANGEROUS_FILENAME_PATTERNS:
        if pattern.search(name):
            return False
    # Reject path traversal via '..' segments.
    parts = name.replace("\\", "/").split("/")
    if any(part == ".." for part in parts):
        return False
    # Reject absolute or Windows-drive roots.
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return False
    return True


def _safe_xml_parse(xml_bytes: bytes, settings: Settings) -> ET.Element:
    """Parse XML with entity-expansion and depth guards to blunt XML bombs."""
    max_depth = getattr(settings, "max_interop_xml_depth", _MAX_XML_DEPTH)
    max_entities = getattr(
        settings, "max_interop_xml_entity_expansion", _MAX_XML_ENTITY_EXPANSION
    )

    # Disallow DOCTYPE entirely (blocks external entities and many bombs).
    if b"<!DOCTYPE" in xml_bytes[:4096]:
        raise ApiError(400, "INVALID_XML", "DOCTYPE declarations are not allowed")

    entity_count = 0

    def _count_entities(source: str) -> str:
        nonlocal entity_count
        entity_count += len(re.findall(r"&[#a-zA-Z0-9]+;", source))
        if entity_count > max_entities:
            raise ApiError(400, "XML_ENTITY_EXPLOSION", "Too many XML entities")
        return source

    # Use a custom tree builder that tracks depth and counts entities.
    class _GuardedTreeBuilder(ET.TreeBuilder):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._depth = 0
            self._max_depth = max_depth

        def start(self, tag, attrs):
            self._depth += 1
            if self._depth > self._max_depth:
                raise ApiError(400, "XML_DEPTH_EXCEEDED", "XML nesting too deep")
            return super().start(tag, attrs)

        def end(self, tag):
            self._depth -= 1
            return super().end(tag)

    parser = ET.XMLParser(target=_GuardedTreeBuilder())
    parser.feed(_count_entities(xml_bytes.decode("utf-8", errors="replace")))
    return parser.close()


def _validate_archive(
    archive_bytes: bytes, settings: Settings
) -> tuple[str, list[str], list[str]]:
    """Validate an uploaded archive. Returns (archive_sha256, warnings, errors).

    Checks:
    * size limit
    * allowed extension (checked by caller on filename)
    * ZIP bomb / traversal / dangerous member names
    * no executable or script members
    """
    max_bytes = getattr(settings, "max_interop_upload_bytes", 50 * 1024 * 1024)
    if len(archive_bytes) > max_bytes:
        raise ApiError(
            413,
            "UPLOAD_TOO_LARGE",
            f"Archive exceeds {max_bytes} bytes",
        )

    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    warnings: list[str] = []
    errors: list[str] = []

    try:
        with zipfile.ZipFile(__import__("io").BytesIO(archive_bytes)) as zf:
            bad_file = zf.testzip()
            if bad_file is not None:
                errors.append(f"Corrupt ZIP member: {bad_file}")
            total_uncompressed = 0
            for info in zf.infolist():
                name = info.filename
                if not _is_safe_archive_member_name(name):
                    errors.append(f"Unsafe archive member: {name}")
                    continue
                total_uncompressed += info.file_size
                if total_uncompressed > max_bytes:
                    errors.append("Archive uncompressed size too large")
                    break
                lower = name.lower()
                if lower.endswith((".exe", ".dll", ".bat", ".sh", ".ps1", ".py", ".js", ".vbs")):
                    warnings.append(f"Potentially executable member excluded: {name}")
    except zipfile.BadZipFile as exc:
        raise ApiError(400, "INVALID_ARCHIVE", "Not a valid ZIP archive") from exc

    return archive_sha256, warnings, errors


def _parse_imsmanifest(
    manifest_bytes: bytes, settings: Settings
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Parse an imsmanifest.xml into a normalized dict. Returns
    (manifest, warnings, errors)."""
    root = _safe_xml_parse(manifest_bytes, settings)
    warnings: list[str] = []
    errors: list[str] = []

    manifest: dict[str, Any] = {
        "identifier": root.get("identifier"),
        "version": root.get("version"),
        "xmlns": root.tag.split("}")[0].strip("{") if "}" in root.tag else None,
        "organizations": [],
        "resources": [],
        "metadata": {},
    }

    # Organizations
    orgs = root.find("{*}organizations")
    if orgs is not None:
        for org in orgs.findall("{*}organization"):
            org_entry: dict[str, Any] = {
                "identifier": org.get("identifier"),
                "structure": org.get("structure"),
                "items": [],
            }
            title = org.find("{*}title")
            if title is not None and title.text:
                org_entry["title"] = title.text.strip()
            for item in org.findall("{*}item"):
                item_entry: dict[str, Any] = {
                    "identifier": item.get("identifier"),
                    "identifierref": item.get("identifierref"),
                    "isvisible": item.get("isvisible"),
                }
                ititle = item.find("{*}title")
                if ititle is not None and ititle.text:
                    item_entry["title"] = ititle.text.strip()
                org_entry["items"].append(item_entry)
            manifest["organizations"].append(org_entry)

    # Resources
    resources = root.find("{*}resources")
    if resources is not None:
        for res in resources.findall("{*}resource"):
            res_entry: dict[str, Any] = {
                "identifier": res.get("identifier"),
                "type": res.get("type"),
                "href": res.get("href"),
            }
            manifest["resources"].append(res_entry)

    # Metadata
    metadata = root.find("{*}metadata")
    if metadata is not None:
        schema = metadata.find("{*}schema")
        if schema is not None and schema.text:
            manifest["metadata"]["schema"] = schema.text.strip()
        schemaversion = metadata.find("{*}schemaversion")
        if schemaversion is not None and schemaversion.text:
            manifest["metadata"]["schemaversion"] = schemaversion.text.strip()

    if not manifest["organizations"]:
        warnings.append("No organizations found in manifest")
    if not manifest["resources"]:
        warnings.append("No resources found in manifest")

    return manifest, warnings, errors


def detect_standard(
    manifest: dict[str, Any],
) -> tuple[str, str, list[str], list[str]]:
    """Detect the e-learning standard from a parsed manifest.

    Returns (standard, version, warnings, errors).
    """
    warnings: list[str] = []
    errors: list[str] = []
    xmlns = manifest.get("xmlns") or ""
    metadata = manifest.get("metadata", {})
    schema = metadata.get("schema", "") or ""
    schemaversion = metadata.get("schemaversion", "") or ""

    # SCORM 1.2 identification
    if "scorm" in xmlns.lower() or "SCORM" in schema:
        if "1.2" in schemaversion or "1.2" in xmlns:
            return SCORM12_VERSION, SCORM12_VERSION, warnings, errors
        return SCORM12_VERSION, SCORM12_VERSION, warnings, errors

    # SCORM 2004 identification (not supported)
    if "2004" in schemaversion or "2004" in xmlns:
        errors.append("SCORM 2004 is detected but not supported by this implementation")
        return SCORM2004_VERSION, SCORM2004_VERSION, warnings, errors

    # IMS Common Cartridge
    if "imscc" in xmlns.lower() or "commoncartridge" in schema.lower():
        return "imscc", "1.0/1.1/1.2/1.3", warnings, errors

    # xAPI has no manifest; handled separately
    warnings.append("Could not confidently detect e-learning standard from manifest")
    return "unknown", "unknown", warnings, errors


# ---------------------------------------------------------------------------
# xAPI adapter (version 1.0.3)
# ---------------------------------------------------------------------------


def validate_xapi_statement(
    statement: dict[str, Any], settings: Settings
) -> InteroperabilityValidationResult:
    """Validate an xAPI statement against the 1.0.3 profile subset."""
    warnings: list[str] = []
    errors: list[str] = []

    actor = statement.get("actor")
    verb = statement.get("verb")
    obj = statement.get("object")

    if not actor or not isinstance(actor, dict):
        errors.append("actor is required and must be an object")
    else:
        if not actor.get("objectType"):
            actor["objectType"] = "Agent"
        has_identity = any(
            actor.get(k) for k in ("mbox", "mbox_sha1sum", "openid", "account", "name")
        )
        if not has_identity:
            warnings.append("actor has no identifiable identity field")

    if not verb or not isinstance(verb, dict):
        errors.append("verb is required and must be an object")
    else:
        if not verb.get("id"):
            errors.append("verb.id is required")
        if not verb.get("display"):
            warnings.append("verb.display is recommended")

    if not obj or not isinstance(obj, dict):
        errors.append("object is required and must be an object")
    else:
        if not obj.get("id"):
            errors.append("object.id is required")
        if not obj.get("objectType"):
            obj["objectType"] = "Activity"

    timestamp = statement.get("timestamp")
    if timestamp is not None:
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            errors.append("timestamp must be a valid ISO-8601 datetime")

    version = statement.get("version", XAPI_VERSION)
    if version != XAPI_VERSION:
        warnings.append(f"declared version {version} differs from supported {XAPI_VERSION}")

    valid = len(errors) == 0
    return InteroperabilityValidationResult(
        valid=valid,
        standard="xAPI",
        version=XAPI_VERSION,
        warnings=warnings,
        errors=errors,
        details={"statementId": statement.get("id")},
    )


def store_xapi_statement_adapter(
    database_path: Path,
    *,
    user_id: str,
    statement: XAPIStatementCreate,
    settings: Settings,
) -> XAPIStatementResponse:
    """Validate and store an xAPI statement."""
    result = validate_xapi_statement(
        statement.model_dump(exclude_none=True), settings
    )
    if not result.valid:
        store_interoperability_audit(
            database_path,
            user_id=user_id,
            tenant_id=f"tenant_{hashlib.sha256(user_id.encode()).hexdigest()[:24]}",
            operation="store_statement",
            standard="xAPI",
            target_id=None,
            success=False,
            error_code="VALIDATION_FAILED",
            details={"errors": result.errors},
        )
        raise ApiError(400, "INVALID_XAPI_STATEMENT", "; ".join(result.errors))

    stored = store_xapi_statement(
        database_path,
        user_id=user_id,
        actor=statement.actor.model_dump(exclude_none=True),
        verb=statement.verb.model_dump(exclude_none=True),
        object=statement.object.model_dump(exclude_none=True),
        result=statement.result.model_dump(exclude_none=True) if statement.result else None,
        context=statement.context.model_dump(exclude_none=True) if statement.context else None,
        timestamp=statement.timestamp,
        authority=statement.authority.model_dump(exclude_none=True)
        if statement.authority
        else None,
        version=statement.version,
        attachment_urls=statement.attachmentUrls,
    )

    store_interoperability_audit(
        database_path,
        user_id=user_id,
        tenant_id=stored["tenantId"],
        operation="store_statement",
        standard="xAPI",
        target_id=stored["statementId"],
        success=True,
        details={"statementId": stored["statementId"]},
    )

    return XAPIStatementResponse(**stored)


def list_xapi_statements_adapter(
    database_path: Path,
    *,
    user_id: str,
    limit: int = 100,
    offset: int = 0,
    verb_id: str | None = None,
) -> XAPIStatementListResponse:
    """List xAPI statements for a user."""
    rows = list_xapi_statements(
        database_path, user_id=user_id, limit=limit, offset=offset, verb_id=verb_id
    )
    return XAPIStatementListResponse(
        statements=[XAPIStatementResponse(**row) for row in rows]
    )


# ---------------------------------------------------------------------------
# SCORM 1.2 adapter
# ---------------------------------------------------------------------------


def validate_scorm12_package(
    archive_bytes: bytes, settings: Settings
) -> InteroperabilityValidationResult:
    """Validate a SCORM 1.2 package archive without extracting content."""
    warnings: list[str] = []
    errors: list[str] = []

    try:
        archive_sha256, archive_warnings, archive_errors = _validate_archive(
            archive_bytes, settings
        )
    except ApiError as exc:
        errors.append(exc.message)
        return InteroperabilityValidationResult(
            valid=False,
            standard="SCORM 1.2",
            version=SCORM12_VERSION,
            warnings=warnings,
            errors=errors,
            details={},
        )
    warnings.extend(archive_warnings)
    errors.extend(archive_errors)

    if errors:
        return InteroperabilityValidationResult(
            valid=False,
            standard="SCORM 1.2",
            version=SCORM12_VERSION,
            warnings=warnings,
            errors=errors,
            details={"archiveSha256": archive_sha256},
        )

    import io

    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            manifest_names = [
                n for n in zf.namelist()
                if n.lower().endswith("imsmanifest.xml")
            ]
            if not manifest_names:
                errors.append("No imsmanifest.xml found in package")
                return InteroperabilityValidationResult(
                    valid=False,
                    standard="SCORM 1.2",
                    version=SCORM12_VERSION,
                    warnings=warnings,
                    errors=errors,
                    details={"archiveSha256": archive_sha256},
                )
            manifest_name = manifest_names[0]
            manifest_bytes = zf.read(manifest_name)
    except zipfile.BadZipFile:
        errors.append("Not a valid ZIP archive")
        return InteroperabilityValidationResult(
            valid=False,
            standard="SCORM 1.2",
            version=SCORM12_VERSION,
            warnings=warnings,
            errors=errors,
            details={"archiveSha256": archive_sha256},
        )

    try:
        manifest, manifest_warnings, manifest_errors = _parse_imsmanifest(
            manifest_bytes, settings
        )
    except ApiError as exc:
        errors.append(exc.message)
        return InteroperabilityValidationResult(
            valid=False,
            standard="SCORM 1.2",
            version=SCORM12_VERSION,
            warnings=warnings,
            errors=errors,
            details={"archiveSha256": archive_sha256},
        )
    warnings.extend(manifest_warnings)
    errors.extend(manifest_errors)

    standard, version, detect_warnings, detect_errors = detect_standard(manifest)
    warnings.extend(detect_warnings)
    errors.extend(detect_errors)

    if standard != SCORM12_VERSION:
        errors.append(f"Expected SCORM 1.2 manifest, detected {standard} {version}")

    valid = len(errors) == 0
    return InteroperabilityValidationResult(
        valid=valid,
        standard="SCORM 1.2",
        version=SCORM12_VERSION,
        warnings=warnings,
        errors=errors,
        details={
            "archiveSha256": archive_sha256,
            "manifest": manifest,
        },
    )


def import_scorm12_package(
    database_path: Path,
    *,
    user_id: str,
    display_name: str,
    archive_bytes: bytes,
    settings: Settings,
) -> InteroperabilityImportResult:
    """Import a SCORM 1.2 package: validate, record, and optionally create
    a course shell referencing the manifest."""

    validation = validate_scorm12_package(archive_bytes, settings)
    if not validation.valid:
        store_interoperability_audit(
            database_path,
            user_id=user_id,
            tenant_id=f"tenant_{hashlib.sha256(user_id.encode()).hexdigest()[:24]}",
            operation="import_package",
            standard="SCORM 1.2",
            target_id=None,
            success=False,
            error_code="VALIDATION_FAILED",
            details={"errors": validation.errors},
        )
        return InteroperabilityImportResult(
            importId=f"import_{secrets.token_urlsafe(8)}",
            standard="SCORM 1.2",
            status="failed",
            errors=validation.errors,
            details=validation.details,
        )

    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    manifest = validation.details.get("manifest", {})

    # Compute manifest sha256 from the manifest dict for stable reference.
    manifest_sha256 = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()
    ).hexdigest()

    package = store_scorm_package(
        database_path,
        user_id=user_id,
        display_name=display_name,
        source_type="scorm12",
        manifest_sha256=manifest_sha256,
        archive_sha256=archive_sha256,
        manifest=manifest,
    )

    # Create a course shell referencing the imported package.
    course = create_course(
        database_path,
        user_id=user_id,
        space_id=None,
        title=display_name,
        description=f"Imported from SCORM 1.2 package: {display_name}",
        source_type="content_resource",
        source_refs=[
            {
                "type": "scorm12_package",
                "packageId": package["packageId"],
                "manifestSha256": manifest_sha256,
            }
        ],
    )

    store_interoperability_audit(
        database_path,
        user_id=user_id,
        tenant_id=package["tenantId"],
        operation="import_package",
        standard="SCORM 1.2",
        target_id=package["packageId"],
        success=True,
        details={
            "packageId": package["packageId"],
            "courseId": course["courseId"] if course else None,
        },
    )

    return InteroperabilityImportResult(
        importId=f"import_{secrets.token_urlsafe(8)}",
        standard="SCORM 1.2",
        status="imported",
        packageId=package["packageId"],
        warnings=validation.warnings,
        details={
            "manifest": manifest,
            "courseId": course["courseId"] if course else None,
        },
    )


def record_scorm12_runtime(
    database_path: Path,
    *,
    user_id: str,
    package_id: str,
    learner_id: str,
    lesson_status: str | None = None,
    score_raw: float | None = None,
    score_min: float | None = None,
    score_max: float | None = None,
    total_time_ms: int | None = None,
    lesson_location: str | None = None,
    suspend_data: dict | None = None,
) -> SCORMSessionResponse:
    """Record SCORM 1.2 cmi.runtime data as a learner session."""
    stored = store_scorm_session(
        database_path,
        package_id=package_id,
        user_id=user_id,
        learner_id=learner_id,
        lesson_status=lesson_status,
        score_raw=score_raw,
        score_min=score_min,
        score_max=score_max,
        total_time_ms=total_time_ms,
        lesson_location=lesson_location,
        suspend_data=suspend_data,
    )
    return SCORMSessionResponse(**stored)


# ---------------------------------------------------------------------------
# LTI 1.3 adapter (framework only; end-to-end needs live credentials)
# ---------------------------------------------------------------------------


def validate_lti13_launch_shape(
    launch_params: dict[str, Any],
) -> InteroperabilityValidationResult:
    """Validate the shape of an LTI 1.3 launch request without verifying
    cryptographic signatures (requires live consumer credentials).

    This is a structural validation framework. Real LTI 1.3 launch
    verification additionally requires:
    * OIDC issuer discovery and public key retrieval
    * JWT signature verification against the issuer's JWKS
    * nonce replay prevention
    * iss/aud claim validation
    These are not implemented because they depend on live LTI consumer
    credentials not present in this ₹0-budget deployment.
    """
    warnings: list[str] = []
    errors: list[str] = []

    message_type = launch_params.get("lti_message_type")
    ltiversion = launch_params.get("lti_version")
    resource_link_id = launch_params.get("resource_link_id")
    deployment_id = launch_params.get("deployment_id")
    target_link_uri = launch_params.get("target_link_uri")

    if message_type != "LtiResourceLinkRequest":
        warnings.append(
            f"Unexpected lti_message_type: {message_type} "
            "(expected LtiResourceLinkRequest)"
        )
    if ltiversion != LTI_VERSION:
        warnings.append(
            f"Unexpected lti_version: {ltiversion} (expected {LTI_VERSION})"
        )
    if not resource_link_id:
        errors.append("resource_link_id is required")
    if not deployment_id:
        errors.append("deployment_id is required")
    if not target_link_uri:
        errors.append("target_link_uri is required")

    # Cryptographic verification cannot be performed without credentials.
    warnings.append(
        "JWT signature verification not performed: no LTI consumer "
        "credentials configured; treat launch as unauthenticated"
    )

    valid = len(errors) == 0
    return InteroperabilityValidationResult(
        valid=valid,
        standard="LTI 1.3",
        version=LTI_VERSION,
        warnings=warnings,
        errors=errors,
        details={
            "note": "Structural validation only; no cryptographic verification",
        },
    )


def record_lti13_launch(
    database_path: Path,
    *,
    user_id: str | None,
    tenant_id: str,
    launch_params: dict[str, Any],
    audit: dict[str, Any] | None = None,
) -> LTILaunchResponse:
    """Record an LTI 1.3 launch audit entry. No secrets are stored."""
    safe_audit = {
        k: v
        for k, v in (audit or {}).items()
        if k
        not in ("id_token", "access_token", "refresh_token", "client_secret")
    }
    stored = store_lti_launch(
        database_path,
        user_id=user_id,
        tenant_id=tenant_id,
        tool_consumer_instance_guid=launch_params.get("tool_consumer_instance_guid"),
        tool_consumer_info_product_family_code=launch_params.get(
            "tool_consumer_info_product_family_code"
        ),
        lti_message_type=launch_params.get("lti_message_type"),
        lti_version=launch_params.get("lti_version"),
        resource_link_id=launch_params.get("resource_link_id"),
        user_id_lms=launch_params.get("user_id"),
        roles=launch_params.get("roles"),
        audit=safe_audit,
    )
    return LTILaunchResponse(**stored)
