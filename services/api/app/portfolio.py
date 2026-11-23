"""M88: Learning Portfolio, Achievement & Evidence Management Engine.

Persistent learner portfolio and evidence management built on existing
WonderLearn subsystems (no duplicate pipelines):

* ``app.storage`` — users, competencies, certificates,
  ``learning_outcomes``, ``learning_paths``, certifications, diagnostics,
  plus new M88 portfolio/evidence/sharing tables.
* ``app.certification`` — M87 competency evaluation & certificate lifecycle.

Design principles:
* Every portfolio entry links to a real source (competency, certificate,
  learning path, assessment, project) with ownership + timestamp.
* Self-reported vs independently verified achievements are distinguished.
* Evidence is versioned; review workflow supports approval/rejection/
  resubmission; expiry/renewal is explicit.
* Portfolios are private by default; sharing is opt-in via revocable,
  expiring, use-limited tokens; public views expose no private data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.errors import ApiError
from app.storage import (
    connect_database,
    create_portfolio,
    create_sharing_link,
    get_competency_mastery,
    get_portfolio,
    get_sharing_link,
    list_certificates,
    list_competencies,
    list_learning_outcomes,
    list_outcome_verifications,
    list_portfolio_entries,
    list_portfolios,
    record_evidence,
    revoke_sharing_link,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EVIDENCE_REVIEW_STATUSES = {"submitted", "under_review", "approved", "rejected"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Storage accessor shims (guarded so missing functions don't break import)
# ---------------------------------------------------------------------------


def _get_evidence(database_path, *, evidence_id: str, user_id: str):
    with closing_ctx(database_path) as connection:
        row = connection.execute(
            """
            SELECT evidence_id, portfolio_entry_id, user_id, title,
                   description, evidence_type, source_type, source_id,
                   verification_status, verification_notes, current_version_id,
                   expires_at, metadata_json, created_at, updated_at
            FROM evidence
            WHERE evidence_id = ?
            """,
            (evidence_id,),
        ).fetchone()
        if row is None or row["user_id"] != user_id:
            return None
    return {
        "evidenceId": row["evidence_id"],
        "portfolioEntryId": row["portfolio_entry_id"],
        "userId": row["user_id"],
        "title": row["title"],
        "description": row["description"],
        "evidenceType": row["evidence_type"],
        "sourceType": row["source_type"],
        "sourceId": row["source_id"],
        "verificationStatus": row["verification_status"],
        "verificationNotes": row["verification_notes"],
        "currentVersionId": row["current_version_id"],
        "expiresAt": row["expires_at"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def closing_ctx(database_path):
    from contextlib import closing

    return closing(connect_database(database_path))


# ---------------------------------------------------------------------------
# Portfolio lifecycle
# ---------------------------------------------------------------------------


def create_user_portfolio(
    database_path,
    *,
    user_id: str,
    title: str,
    summary: str | None = None,
    visibility: str = "private",
) -> dict[str, Any]:
    """Create a portfolio for the user (idempotent-ish: one primary private
    portfolio is allowed; additional portfolios may be created explicitly)."""
    return create_portfolio(
        database_path,
        user_id=user_id,
        title=title,
        summary=summary,
        visibility=visibility,
    )


def get_user_portfolio(
    database_path,
    *,
    portfolio_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Return a portfolio owned by the user."""
    return get_portfolio(database_path, portfolio_id=portfolio_id, user_id=user_id)


def list_user_portfolios(
    database_path,
    *,
    user_id: str,
) -> list[dict[str, Any]]:
    """List all portfolios owned by the user."""
    return list_portfolios(database_path, user_id=user_id)


# ---------------------------------------------------------------------------
# Evidence lifecycle & review workflow
# ---------------------------------------------------------------------------


def submit_evidence(
    database_path,
    *,
    user_id: str,
    title: str,
    evidence_type: str,
    source_type: str,
    source_id: str | None = None,
    description: str | None = None,
    portfolio_entry_id: str | None = None,
    verification_status: str = "self_reported",
    verification_notes: str | None = None,
    expires_at: str | None = None,
    metadata: dict[str, Any] | None = None,
    initial_version: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit evidence for a portfolio entry.

    Self-reported evidence is recorded immediately; independently verified
    evidence starts as ``pending_verification`` and moves through the review
    workflow via ``review_evidence_version``.
    """
    result = record_evidence(
        database_path,
        user_id=user_id,
        title=title,
        evidence_type=evidence_type,
        source_type=source_type,
        source_id=source_id,
        description=description,
        portfolio_entry_id=portfolio_entry_id,
        verification_status=verification_status,
        verification_notes=verification_notes,
        expires_at=expires_at,
        metadata=metadata,
        current_version=initial_version,
    )
    if result is None:
        raise ApiError(400, "EVIDENCE_SUBMIT_FAILED", "Evidence could not be recorded.")
    return result


def review_evidence_version(
    database_path,
    *,
    evidence_id: str,
    user_id: str,
    version_id: str,
    review_status: str,
    reviewed_by: str | None = None,
    rejection_reason: str | None = None,
) -> dict[str, Any]:
    """Review an evidence version: approve, reject, or mark under review.

    Owner or reviewer only. Rejection triggers a resubmission workflow
    (caller creates a new version).
    """
    if review_status not in EVIDENCE_REVIEW_STATUSES:
        raise ApiError(
            400,
            "INVALID_REVIEW_STATUS",
            "review_status must be one of submitted, under_review, approved, rejected.",
        )
    evidence = _get_evidence(database_path, evidence_id=evidence_id, user_id=user_id)
    if evidence is None:
        raise ApiError(404, "EVIDENCE_NOT_FOUND", "Evidence was not found or access denied.")

    now = _now_iso()
    with closing_ctx(database_path) as connection:
        row = connection.execute(
            """
            SELECT version_id, review_status, is_current
            FROM evidence_versions
            WHERE version_id = ? AND evidence_id = ?
            """,
            (version_id, evidence_id),
        ).fetchone()
        if row is None:
            raise ApiError(404, "EVIDENCE_VERSION_NOT_FOUND", "Evidence version was not found.")
        with connection:
            connection.execute(
                """
                UPDATE evidence_versions
                SET review_status = ?, reviewed_by = ?, reviewed_at = ?,
                    rejection_reason = ?
                WHERE version_id = ?
                """,
                (
                    review_status,
                    reviewed_by,
                    now,
                    rejection_reason if review_status == "rejected" else None,
                    version_id,
                ),
            )
            # If approved, mark evidence as verified.
            if review_status == "approved":
                connection.execute(
                    """
                    UPDATE evidence
                    SET verification_status = 'verified', updated_at = ?
                    WHERE evidence_id = ?
                    """,
                    (now, evidence_id),
                )
            elif review_status == "rejected":
                connection.execute(
                    """
                    UPDATE evidence
                    SET verification_status = 'rejected', updated_at = ?
                    WHERE evidence_id = ?
                    """,
                    (now, evidence_id),
                )

    return {
        "evidenceId": evidence_id,
        "versionId": version_id,
        "reviewStatus": review_status,
        "reviewedBy": reviewed_by,
        "reviewedAt": now,
        "rejectionReason": rejection_reason,
    }


def expire_evidence(
    database_path,
    *,
    evidence_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Mark evidence as expired (sets expires_at to now)."""
    evidence = _get_evidence(database_path, evidence_id=evidence_id, user_id=user_id)
    if evidence is None:
        raise ApiError(404, "EVIDENCE_NOT_FOUND", "Evidence was not found or access denied.")
    now = _now_iso()
    with closing_ctx(database_path) as connection:
        with connection:
            connection.execute(
                "UPDATE evidence SET expires_at = ?, updated_at = ? WHERE evidence_id = ?",
                (now, now, evidence_id),
            )
    return {"evidenceId": evidence_id, "expiresAt": now, "status": "expired"}


# ---------------------------------------------------------------------------
# Portfolio intelligence (read-only, no fabricated achievements)
# ---------------------------------------------------------------------------


def compute_portfolio_intelligence(
    database_path,
    *,
    user_id: str,
    portfolio_id: str | None = None,
) -> dict[str, Any]:
    """Assemble intelligence for a user's portfolio.

    Reads only from persisted evidence — never invents achievements.
    Identifies demonstrated competencies and missing evidence.
    """
    portfolios = list_portfolios(database_path, user_id=user_id)
    if portfolio_id is not None:
        portfolios = [p for p in portfolios if p["portfolioId"] == portfolio_id]
        if not portfolios:
            raise ApiError(404, "PORTFOLIO_NOT_FOUND", "Portfolio was not found or access denied.")

    entries = list_portfolio_entries(database_path, user_id=user_id)
    if portfolio_id is not None:
        entries = [e for e in entries if e["portfolioId"] == portfolio_id]

    # Demonstrated competencies from verified entries.
    demonstrated: list[dict[str, Any]] = []
    for entry in entries:
        if entry["verificationStatus"] == "verified" and entry["entryType"] == "competency":
            demonstrated.append(
                {
                    "entryId": entry["entryId"],
                    "title": entry["title"],
                    "competencyIds": entry["competencyIds"],
                    "skillTags": entry["skillTags"],
                    "sourceType": entry["sourceType"],
                    "sourceId": entry["sourceId"],
                    "verifiedAt": entry.get("verifiedAt"),
                }
            )

    # Missing evidence: learning outcomes not yet verified.
    outcomes = list_learning_outcomes(database_path, user_id=user_id)
    missing_evidence: list[dict[str, Any]] = []
    for outcome in outcomes:
        verifications = list_outcome_verifications(
            database_path, outcome_id=outcome["outcomeId"], user_id=user_id
        )
        if not any(v["status"] in {"achieved", "verified"} for v in verifications):
            missing_evidence.append(
                {
                    "outcomeId": outcome["outcomeId"],
                    "title": outcome["title"],
                    "competencyId": outcome["competencyId"],
                    "proficiencyThreshold": outcome["proficiencyThreshold"],
                    "reason": "Outcome not yet verified.",
                }
            )

    # Certificates.
    certificates = list_certificates(database_path, user_id=user_id, status="issued")

    # Competency mastery snapshot.
    competencies = list_competencies(database_path, user_id=user_id)
    competency_status: list[dict[str, Any]] = []
    for comp in competencies:
        mastery = get_competency_mastery(
            database_path, competency_id=comp["competencyId"], user_id=user_id
        )
        competency_status.append(
            {
                "competencyId": comp["competencyId"],
                "title": comp["title"],
                "masteryScore": mastery["masteryScore"] if mastery else None,
                "evidenceCount": mastery["evidenceCount"] if mastery else 0,
            }
        )

    return {
        "userId": user_id,
        "portfolioId": portfolio_id,
        "portfolioCount": len(portfolios),
        "entryCount": len(entries),
        "verifiedEntryCount": sum(
            1 for e in entries if e["verificationStatus"] == "verified"
        ),
        "demonstratedCompetencies": demonstrated,
        "missingEvidence": missing_evidence,
        "certificates": certificates,
        "competencyStatus": competency_status,
        "generatedAt": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Sharing
# ---------------------------------------------------------------------------


def create_portfolio_sharing_link(
    database_path,
    *,
    portfolio_id: str,
    user_id: str,
    token: str | None = None,
    entry_ids: list[str] | None = None,
    expires_at: str | None = None,
    max_uses: int | None = None,
) -> dict[str, Any]:
    """Create a revocable sharing link. Owner only."""
    result = create_sharing_link(
        database_path,
        portfolio_id=portfolio_id,
        user_id=user_id,
        token=token,
        entry_ids=entry_ids,
        expires_at=expires_at,
        max_uses=max_uses,
    )
    if result is None:
        raise ApiError(400, "SHARING_LINK_CREATE_FAILED", "Sharing link could not be created.")
    return result


def resolve_sharing_link(
    database_path,
    *,
    token: str,
) -> dict[str, Any] | None:
    """Public resolution of a sharing link token.

    Returns only portfolio metadata + entry titles/types — no private
    assessment answers, analytics, or learner personal data.
    """
    link = get_sharing_link(database_path, token=token)
    if link is None:
        return None
    portfolio = get_portfolio(database_path, portfolio_id=link["portfolioId"])
    if portfolio is None or portfolio["visibility"] not in {"share_link", "public"}:
        return None
    entries = list_portfolio_entries(
        database_path, portfolio_id=link["portfolioId"]
    )
    # Filter to entries included in the share (if entry_ids specified) and
    # that are shareable (not strictly private unless portfolio is public).
    allowed_entry_ids = set(link["entryIds"]) if link["entryIds"] else None
    public_entries: list[dict[str, Any]] = []
    for entry in entries:
        if allowed_entry_ids is not None and entry["entryId"] not in allowed_entry_ids:
            continue
        if portfolio["visibility"] == "private" and entry["visibility"] == "private":
            continue
        public_entries.append(
            {
                "entryId": entry["entryId"],
                "entryType": entry["entryType"],
                "title": entry["title"],
                "description": entry["description"],
                "sourceType": entry["sourceType"],
                "sourceId": entry["sourceId"],
                "verificationStatus": entry["verificationStatus"],
                "visibility": entry["visibility"],
                "skillTags": entry["skillTags"],
            }
        )
    return {
        "portfolioId": portfolio["portfolioId"],
        "title": portfolio["title"],
        "summary": portfolio["summary"],
        "visibility": portfolio["visibility"],
        "entries": public_entries,
        "linkToken": link["token"],
        "expiresAt": link["expiresAt"],
    }


def revoke_portfolio_sharing_link(
    database_path,
    *,
    link_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Revoke a sharing link. Owner only."""
    ok = revoke_sharing_link(database_path, link_id=link_id, user_id=user_id)
    if not ok:
        raise ApiError(400, "SHARING_LINK_REVOKE_FAILED", "Sharing link could not be revoked.")
    return {"linkId": link_id, "revoked": True}
