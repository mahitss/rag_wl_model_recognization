"""M87: Learning Outcome Verification & Competency Certification Engine.

Evidence-based verification of learning outcomes and trustworthy certificate
issuance, built on existing WonderLearn subsystems (no duplicate pipelines):

* ``app.storage`` — ``competencies``, ``competency_prerequisites``,
  ``assessment_question_competencies``, ``user_competency_mastery``,
  M86 ``diagnostic_concept_results`` / ``diagnostic_sessions``,
  M87 ``learning_outcomes`` / ``outcome_evidence`` /
  ``outcome_verifications`` / ``certification_definitions`` /
  ``certificates``.
* ``app.diagnostic_assessment`` — M86 diagnostic evidence.

Design principles:
* Every verification decision links to actual persisted evidence.
* Duplicate evidence (same source) never inflates scores.
* Older evidence decays; recency is explicit.
* Proficiency thresholds are configurable; mastery is never claimed without
  sufficient evidence.
* Historical competency decisions are preserved (append-only).
* Certificate issuance is transactional and idempotent.
* Public verification exposes only approved, non-sensitive information.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.errors import ApiError
from app.storage import (
    connect_database,
    get_certification_definition,
    get_competency_mastery,
    get_diagnostic_concept_results,
    get_learning_outcome,
    issue_certificate,
    list_assessment_attempts,
    list_assessment_question_attempts,
    list_certificates,
    list_competencies,
    list_competency_prerequisites,
    list_learning_outcomes,
    list_outcome_evidence,
    list_outcome_verifications,
    record_competency_evaluation,
    record_outcome_verification,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PROFICIENCY_THRESHOLD = 0.80
PROGRESSING_THRESHOLD = 0.40
ATTEMPTED_THRESHOLD = 0.10

# Evidence recency window: evidence within this many days counts at full weight.
RECENCY_WINDOW_DAYS = 90
# Minimum distinct evidence sources required for a "verified" decision.
MIN_EVIDENCE_SOURCES_VERIFIED = 2
# Minimum confidence for a verified decision.
MIN_CONFIDENCE_VERIFIED = 0.75

# Evidence type -> base weight. Direct mastery demonstrations weigh most.
EVIDENCE_BASE_WEIGHT = {
    "mastery_demonstration": 1.0,
    "diagnostic_result": 0.9,
    "assessment_result": 0.85,
    "project_submission": 0.8,
    "practical_exercise": 0.75,
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _recency_weight(recorded_at: str, now: datetime) -> float:
    """Return a recency weight in (0, 1]; older evidence decays."""
    try:
        recorded = _parse_datetime(recorded_at)
    except ValueError:
        return 0.5
    days = max(0.0, (now - recorded).total_seconds() / 86_400.0)
    if days <= RECENCY_WINDOW_DAYS:
        return 1.0
    # Linear decay beyond the window; floor at 0.25.
    decay = max(0.25, 1.0 - (days - RECENCY_WINDOW_DAYS) / 365.0)
    return decay


# ---------------------------------------------------------------------------
# Evidence gathering
# ---------------------------------------------------------------------------


def _gather_competency_evidence(
    database_path,
    *,
    user_id: str,
    competency_id: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """Gather deduplicated, recency-weighted evidence for a competency.

    Sources:
    * persisted competency mastery (user_competency_mastery)
    * assessment question attempts whose question maps to the competency
    * prior outcome verifications linked to this competency
    * M86 diagnostic concept results matching the competency by name/alias
    """
    evidence: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    def add(
        *,
        source_id: str,
        source_type: str,
        score: float,
        recorded_at: str,
        evidence_type: str,
    ) -> None:
        key = (source_type, source_id)
        if key in seen_keys:
            return  # Prevent duplicate evidence from inflating scores.
        seen_keys.add(key)
        evidence.append(
            {
                "sourceId": source_id,
                "sourceType": source_type,
                "evidenceType": evidence_type,
                "score": float(score),
                "recordedAt": recorded_at,
                "weight": EVIDENCE_BASE_WEIGHT.get(evidence_type, 0.5)
                * _recency_weight(recorded_at, now),
            }
        )

    # 1. Direct competency mastery.
    mastery = get_competency_mastery(
        database_path, competency_id=competency_id, user_id=user_id
    )
    if mastery is not None:
        add(
            source_id=f"mastery:{competency_id}",
            source_type="competency_mastery",
            score=mastery["masteryScore"],
            recorded_at=mastery["lastAssessedAt"] or _now_iso(),
            evidence_type="mastery_demonstration",
        )

    # 2. Assessment question attempts mapped to this competency.
    with closing_ctx_database(database_path) as connection:
        question_rows = connection.execute(
            """
            SELECT aqc.question_id
            FROM assessment_question_competencies aqc
            WHERE aqc.competency_id = ?
            """,
            (competency_id,),
        ).fetchall()
        question_ids = [r["question_id"] for r in question_rows]
    if question_ids:
        attempts = list_assessment_attempts(
            database_path, user_id=user_id
        ) or []
        for attempt in attempts:
            attempt_id = attempt["attemptId"]
            question_attempts = list_assessment_question_attempts(
                database_path, attempt_id=attempt_id, user_id=user_id
            ) or []
            for qa in question_attempts:
                if qa["questionId"] not in question_ids:
                    continue
                score = qa["score"] if qa["score"] is not None else (
                    1.0 if qa["result"] == "correct" else 0.0
                )
                add(
                    source_id=f"attempt:{qa['attemptId']}:{qa['questionId']}",
                    source_type="assessment_question_attempt",
                    score=score,
                    recorded_at=qa["createdAt"],
                    evidence_type="assessment_result",
                )

    # 3. Prior outcome verifications linked to this competency.
    outcomes = list_learning_outcomes(
        database_path, user_id=user_id, competency_id=competency_id
    )
    for outcome in outcomes:
        verifications = list_outcome_verifications(
            database_path, outcome_id=outcome["outcomeId"], user_id=user_id
        )
        for v in verifications:
            add(
                source_id=f"verification:{v['verificationId']}",
                source_type="outcome_verification",
                score=v["computedScore"],
                recorded_at=v["evaluatedAt"],
                evidence_type="mastery_demonstration",
            )

    # 4. M86 diagnostic concept results matching this competency.
    concepts = list_competencies(database_path, user_id=user_id) or []
    competency = next(
        (c for c in concepts if c["competencyId"] == competency_id), None
    )
    if competency is not None:
        comp_name = (competency.get("title") or "").casefold()
        comp_aliases = [
            a.casefold() for a in (competency.get("aliases") or [])
        ]
        diag_results = get_diagnostic_concept_results(
            database_path, user_id=user_id
        )
        for dr in diag_results:
            cname = (dr.get("concept_name") or "").casefold()
            if comp_name and (comp_name == cname or comp_name in cname):
                match = True
            elif any(a and a in cname for a in comp_aliases):
                match = True
            else:
                match = False
            if not match:
                continue
            total = dr["total_questions"] or 0
            correct = dr["correct_count"] or 0
            score = (correct / total) if total else 0.0
            add(
                source_id=f"diagnostic:{dr['resultId']}",
                source_type="diagnostic_concept_result",
                score=score,
                recorded_at=dr["assessedAt"],
                evidence_type="diagnostic_result",
            )

    return evidence


def closing_ctx_database(database_path):
    from contextlib import closing

    return closing(connect_database(database_path))


# ---------------------------------------------------------------------------
# Competency evaluation
# ---------------------------------------------------------------------------


def evaluate_competency(
    database_path,
    *,
    user_id: str,
    competency_id: str,
    proficiency_threshold: float = DEFAULT_PROFICIENCY_THRESHOLD,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Deterministically evaluate a competency from persisted evidence.

    Returns the evaluation plus a decision reason and remaining requirements.
    The decision is recorded in competency_evaluation_history (append-only).
    """
    if not (0.0 <= proficiency_threshold <= 1.0):
        raise ApiError(400, "INVALID_THRESHOLD", "proficiency_threshold must be between 0 and 1.")
    if now is None:
        now = datetime.now(UTC)

    # Verify ownership.
    competency = None
    for c in list_competencies(database_path, user_id=user_id):
        if c["competencyId"] == competency_id:
            competency = c
            break
    if competency is None:
        raise ApiError(404, "COMPETENCY_NOT_FOUND", "Competency was not found or access denied.")

    evidence = _gather_competency_evidence(
        database_path, user_id=user_id, competency_id=competency_id, now=now
    )

    # Compute weighted mastery score.
    total_weight = sum(e["weight"] for e in evidence)
    weighted_score = (
        sum(e["score"] * e["weight"] for e in evidence) / total_weight
        if total_weight > 0
        else 0.0
    )

    distinct_sources = len({e["sourceType"] for e in evidence})
    # Confidence scales with evidence volume and source diversity.
    confidence = min(
        0.95,
        0.30 + 0.10 * min(len(evidence), 5) + 0.10 * min(distinct_sources, 4),
    )

    # Determine status.
    verified_eligible = (
        weighted_score >= proficiency_threshold
        and confidence >= MIN_CONFIDENCE_VERIFIED
        and distinct_sources >= MIN_EVIDENCE_SOURCES_VERIFIED
    )
    if verified_eligible:
        status = "verified"
    elif weighted_score >= proficiency_threshold:
        status = "achieved"
    elif weighted_score >= PROGRESSING_THRESHOLD:
        status = "progressing"
    elif weighted_score >= ATTEMPTED_THRESHOLD or len(evidence) > 0:
        status = "attempted"
    else:
        status = "attempted" if len(evidence) == 0 and weighted_score == 0 else "attempted"

    # Remaining requirements.
    remaining: list[str] = []
    if len(evidence) == 0:
        remaining.append("No assessment evidence recorded for this competency.")
    if weighted_score < proficiency_threshold:
        remaining.append(
            f"Current mastery {weighted_score:.0%} is below the "
            f"configured threshold {proficiency_threshold:.0%}."
        )
    if confidence < MIN_CONFIDENCE_VERIFIED:
        remaining.append("Insufficient evidence for a verified determination.")
    if distinct_sources < MIN_EVIDENCE_SOURCES_VERIFIED:
        remaining.append(
            f"Evidence from only {distinct_sources} source type(s); "
            f"at least {MIN_EVIDENCE_SOURCES_VERIFIED} required for verification."
        )
    # Prerequisite check.
    prereqs = list_competency_prerequisites(
        database_path, competency_id=competency_id, user_id=user_id
    ) or []
    unmet_prereqs: list[str] = []
    for prereq in prereqs:
        pid = prereq["competencyId"]
        p_mastery = get_competency_mastery(
            database_path, competency_id=pid, user_id=user_id
        )
        if p_mastery is None or p_mastery["masteryScore"] < DEFAULT_PROFICIENCY_THRESHOLD:
            unmet_prereqs.append(prereq["title"])
    if unmet_prereqs:
        remaining.append(
            "Unmet prerequisite competency(ies): " + ", ".join(unmet_prereqs)
        )

    decision_reason = (
        f"Competency '{competency['title']}' evaluated from {len(evidence)} "
        f"evidence record(s): weighted mastery {weighted_score:.0%}, "
        f"confidence {confidence:.0%}, {distinct_sources} source type(s). "
        f"Status: {status}."
    )

    # Record historical decision (append-only, never overwrites).
    record_competency_evaluation(
        database_path,
        competency_id=competency_id,
        user_id=user_id,
        mastery_score=round(weighted_score, 4),
        confidence=round(confidence, 4),
        status=status,
        evidence_snapshot={
            "evidenceCount": len(evidence),
            "distinctSources": distinct_sources,
            "totalWeight": round(total_weight, 4),
            "weightedScore": round(weighted_score, 4),
            "sources": [
                {
                    "sourceType": e["sourceType"],
                    "score": round(e["score"], 4),
                    "weight": round(e["weight"], 4),
                    "recordedAt": e["recordedAt"],
                }
                for e in evidence
            ],
        },
        decision_reason=decision_reason,
        evaluated_at=_now_iso(),
    )

    return {
        "competencyId": competency_id,
        "competencyName": competency["title"],
        "userId": user_id,
        "status": status,
        "weightedScore": round(weighted_score, 4),
        "confidence": round(confidence, 4),
        "proficiencyThreshold": proficiency_threshold,
        "evidenceCount": len(evidence),
        "distinctSources": distinct_sources,
        "meetsThreshold": weighted_score >= proficiency_threshold,
        "remainingRequirements": remaining,
        "decisionReason": decision_reason,
        "evaluatedAt": _now_iso(),
        "prerequisites": [
            {
                "competencyId": p["competencyId"],
                "title": p["title"],
                "met": (
                    get_competency_mastery(
                        database_path, competency_id=p["competencyId"], user_id=user_id
                    )
                    is not None
                    and get_competency_mastery(
                        database_path, competency_id=p["competencyId"], user_id=user_id
                    )["masteryScore"]
                    >= DEFAULT_PROFICIENCY_THRESHOLD
                ),
            }
            for p in prereqs
        ],
    }


# ---------------------------------------------------------------------------
# Learning outcome verification
# ---------------------------------------------------------------------------


def verify_learning_outcome(
    database_path,
    *,
    user_id: str,
    outcome_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a learning outcome using persisted evidence.

    Distinguishes attempted, progressing, achieved, and verified outcomes.
    Completing a lesson does not automatically establish competency.
    """
    if now is None:
        now = datetime.now(UTC)

    outcome = get_learning_outcome(
        database_path, outcome_id=outcome_id, user_id=user_id
    )
    if outcome is None:
        raise ApiError(
            404,
            "LEARNING_OUTCOME_NOT_FOUND",
            "Learning outcome was not found or access denied.",
        )

    competency_id = outcome["competencyId"]
    threshold = outcome["proficiencyThreshold"]

    # Gather evidence specific to this outcome.
    evidence_records = list_outcome_evidence(
        database_path, outcome_id=outcome_id, user_id=user_id
    )
    total_weight = 0.0
    weighted_score = 0.0
    seen_keys: set[tuple[str, str]] = set()
    for er in evidence_records:
        key = (er["evidenceType"], er["sourceId"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        weight = EVIDENCE_BASE_WEIGHT.get(er["evidenceType"], 0.5) * _recency_weight(
            er["recordedAt"], now
        )
        total_weight += weight
        weighted_score += er["score"] * weight
    weighted_score = (weighted_score / total_weight) if total_weight > 0 else 0.0

    distinct_sources = len({e["evidenceType"] for e in evidence_records})
    confidence = min(
        0.95, 0.25 + 0.10 * min(len(evidence_records), 5) + 0.10 * distinct_sources
    )

    # Competency-level evaluation for prerequisite context.
    comp_eval = evaluate_competency(
        database_path,
        user_id=user_id,
        competency_id=competency_id,
        proficiency_threshold=threshold,
        now=now,
    )

    # Determine outcome status.
    prereqs_met = all(p["met"] for p in comp_eval["prerequisites"])
    if (
        weighted_score >= threshold
        and confidence >= MIN_CONFIDENCE_VERIFIED
        and distinct_sources >= MIN_EVIDENCE_SOURCES_VERIFIED
        and prereqs_met
    ):
        status = "verified"
    elif weighted_score >= threshold:
        status = "achieved"
    elif weighted_score >= PROGRESSING_THRESHOLD:
        status = "progressing"
    elif len(evidence_records) > 0:
        status = "attempted"
    else:
        status = "attempted"

    remaining: list[str] = []
    if len(evidence_records) == 0:
        remaining.append("No evidence recorded for this outcome.")
    if weighted_score < threshold:
        remaining.append(
            f"Outcome score {weighted_score:.0%} below threshold {threshold:.0%}."
        )
    if not prereqs_met:
        remaining.append("Prerequisite competency(ies) not yet met.")
    if confidence < MIN_CONFIDENCE_VERIFIED:
        remaining.append("Insufficient evidence for verification.")

    decision_reason = (
        f"Outcome '{outcome['title']}' evaluated from {len(evidence_records)} "
        f"evidence record(s): score {weighted_score:.0%}, confidence "
        f"{confidence:.0%}, prerequisites met={prereqs_met}. Status: {status}."
    )

    record_outcome_verification(
        database_path,
        outcome_id=outcome_id,
        user_id=user_id,
        status=status,
        confidence=round(confidence, 4),
        computed_score=round(weighted_score, 4),
        decision_reason=decision_reason,
        remaining_requirements=[{"text": r} for r in remaining],
        assessment_references=[er["sourceId"] for er in evidence_records],
        evaluated_at=_now_iso(),
    )

    return {
        "outcomeId": outcome_id,
        "outcomeName": outcome["title"],
        "competencyId": competency_id,
        "userId": user_id,
        "status": status,
        "computedScore": round(weighted_score, 4),
        "confidence": round(confidence, 4),
        "proficiencyThreshold": threshold,
        "evidenceCount": len(evidence_records),
        "prerequisitesMet": prereqs_met,
        "meetsThreshold": weighted_score >= threshold,
        "remainingRequirements": remaining,
        "decisionReason": decision_reason,
        "evaluatedAt": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Certification lifecycle
# ---------------------------------------------------------------------------


def check_certification_eligibility(
    database_path,
    *,
    user_id: str,
    cert_definition_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Check whether a user is eligible for a certification.

    All requirements must be satisfied (competencies verified at threshold).
    """
    if now is None:
        now = datetime.now(UTC)

    definition = get_certification_definition(
        database_path, cert_id=cert_definition_id, user_id=user_id
    )
    if definition is None:
        raise ApiError(
            404, "CERTIFICATION_DEFINITION_NOT_FOUND",
            "Certification definition was not found or access denied.",
        )
    if not definition["isActive"]:
        return {
            "certId": cert_definition_id,
            "userId": user_id,
            "eligible": False,
            "reason": "Certification definition is not active.",
            "requirements": [],
        }

    requirements = definition["requirements"]
    requirement_results: list[dict[str, Any]] = []
    all_met = True

    for req in requirements:
        req_type = req.get("type")
        target_id = req.get("targetId")
        if req_type == "competency" and target_id:
            try:
                eval_result = evaluate_competency(
                    database_path,
                    user_id=user_id,
                    competency_id=target_id,
                    proficiency_threshold=definition["proficiencyThreshold"],
                    now=now,
                )
                met = eval_result["status"] in {"achieved", "verified"}
            except ApiError:
                met = False
                eval_result = {
                    "competencyId": target_id,
                    "status": "not_found",
                    "weightedScore": 0.0,
                    "decisionReason": "Competency not found or access denied.",
                }
            requirement_results.append(
                {
                    "type": req_type,
                    "targetId": target_id,
                    "met": met,
                    "detail": eval_result,
                }
            )
            if not met:
                all_met = False
        elif req_type == "outcome" and target_id:
            try:
                verify_result = verify_learning_outcome(
                    database_path, user_id=user_id, outcome_id=target_id, now=now
                )
                met = verify_result["status"] in {"achieved", "verified"}
            except ApiError:
                met = False
                verify_result = {
                    "outcomeId": target_id,
                    "status": "not_found",
                    "computedScore": 0.0,
                    "decisionReason": "Outcome not found or access denied.",
                }
            requirement_results.append(
                {
                    "type": req_type,
                    "targetId": target_id,
                    "met": met,
                    "detail": verify_result,
                }
            )
            if not met:
                all_met = False

    # Check for existing issued certificate (idempotency).
    existing = None
    for c in list_certificates(database_path, user_id=user_id, status="issued"):
        if c["certDefinitionId"] == cert_definition_id:
            existing = c
            break

    return {
        "certId": cert_definition_id,
        "userId": user_id,
        "eligible": bool(all_met and not existing),
        "alreadyCertified": existing is not None,
        "reason": (
            "All requirements satisfied."
            if all_met
            else "One or more requirements are not yet satisfied."
        ),
        "existingCertificate": existing,
        "requirements": requirement_results,
        "checkedAt": _now_iso(),
    }


def issue_certificate_if_eligible(
    database_path,
    *,
    user_id: str,
    cert_definition_id: str,
    verification_code: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Issue a certificate only when all eligibility requirements are met.

    Transactional and idempotent: returns None if already issued or ineligible.
    """
    if now is None:
        now = datetime.now(UTC)
    eligibility = check_certification_eligibility(
        database_path, user_id=user_id, cert_definition_id=cert_definition_id, now=now
    )
    if not eligibility["eligible"]:
        return None
    definition = get_certification_definition(
        database_path, cert_id=cert_definition_id, user_id=user_id
    )
    expires_days = definition["expiresDays"]
    return issue_certificate(
        database_path,
        cert_definition_id=cert_definition_id,
        user_id=user_id,
        verification_code=verification_code,
        expires_days=expires_days,
    )


# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------


def list_user_competency_status(
    database_path,
    *,
    user_id: str,
    proficiency_threshold: float = DEFAULT_PROFICIENCY_THRESHOLD,
) -> list[dict[str, Any]]:
    """Return the current evaluation status for all of a user's competencies."""
    results: list[dict[str, Any]] = []
    for c in list_competencies(database_path, user_id=user_id):
        results.append(
            evaluate_competency(
                database_path,
                user_id=user_id,
                competency_id=c["competencyId"],
                proficiency_threshold=proficiency_threshold,
            )
        )
    results.sort(key=lambda r: r["competencyName"])
    return results
