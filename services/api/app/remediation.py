"""M65: Personalized Remediation Engine.

For each verified learning gap, recommends remediation activities ranked by
transparent, configurable rules. Supports the closed-loop improvement cycle:
detect → show evidence → recommend → learner chooses → record → reassess →
compare vs baseline → update progress.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.storage import (
    accept_remediation,
    complete_remediation,
    create_gap_remediation,
    create_remediation_reassessment,
    dismiss_remediation,
    get_competency_mastery,
    get_learning_gap,
    list_gap_remediations,
    list_learning_gaps,
    list_remediation_reassessments,
)

MASTERY_WEAK_THRESHOLD = 0.60

# ---------------------------------------------------------------------------
# Remediation activity type configuration.
#
# Each activity type has:
#   - base_priority: starting priority score (higher = more urgent)
#   - description: human-readable purpose
# ---------------------------------------------------------------------------

_REMEDIATION_CONFIG: dict[str, dict[str, Any]] = {
    "prerequisite_revision": {
        "base_priority": 95.0,
        "description": "Review and master the prerequisite competency first.",
    },
    "targeted_explanation": {
        "base_priority": 85.0,
        "description": "Focused explanation of the misunderstood concept.",
    },
    "source_material": {
        "base_priority": 75.0,
        "description": "Read relevant source passages cited as evidence.",
    },
    "practice_questions": {
        "base_priority": 70.0,
        "description": "Answer practice questions targeting the gap topic.",
    },
    "flashcards": {
        "base_priority": 60.0,
        "description": "Review flashcards for active recall of key facts.",
    },
    "concept_diagram": {
        "base_priority": 55.0,
        "description": "Study a visual concept diagram for the topic.",
    },
    "focused_quiz": {
        "base_priority": 65.0,
        "description": "Take a short quiz focused on the gap topic.",
    },
    "scheduled_revision": {
        "base_priority": 50.0,
        "description": "Add to spaced-repetition revision schedule.",
    },
    "competency_assessment": {
        "base_priority": 80.0,
        "description": "Reassess competency mastery after remediation.",
    },
}

_SEVERITY_PRIORITY_BOOST = {
    "critical": 30.0,
    "moderate": 15.0,
    "minor": 5.0,
}

_EVIDENCE_PRIORITY_BOOST = {
    "strong": 10.0,
    "moderate": 5.0,
    "weak": 0.0,
}

_RECURRENCE_PRIORITY_BOOST = {
    0: 0.0,
    1: 5.0,
    2: 10.0,
    3: 20.0,
}


def _priority_score(
    *,
    severity: str,
    evidence_strength: str,
    recurrence: int,
    activity_type: str,
    has_prerequisite_gap: bool = False,
) -> float:
    """Compute a transparent priority score for a remediation activity.

    Score = base_priority + severity_boost + evidence_boost + recurrence_boost
    (+ prerequisite_boost if the activity is prerequisite_revision and the gap
    involves a missing prerequisite).
    All components are configurable and auditable.
    """
    base = _REMEDIATION_CONFIG[activity_type]["base_priority"]
    severity_boost = _SEVERITY_PRIORITY_BOOST.get(severity, 0.0)
    evidence_boost = _EVIDENCE_PRIORITY_BOOST.get(evidence_strength, 0.0)
    recurrence_boost = _RECURRENCE_PRIORITY_BOOST.get(
        min(recurrence, 3), 0.0
    )
    prerequisite_boost = 25.0 if (has_prerequisite_gap and activity_type == "prerequisite_revision") else 0.0

    return round(base + severity_boost + evidence_boost + recurrence_boost + prerequisite_boost, 1)


def generate_remediations_for_gap(
    *,
    database_path: str,
    gap_id: str,
    user_id: str,
    now: datetime,
) -> dict[str, Any]:
    """Generate ranked remediation recommendations for a gap.

    Only generates recommendations for gaps with actual evidence. Does not
    fabricate weaknesses.
    """
    db_path = Path(database_path)
    gap = get_learning_gap(db_path, gap_id=gap_id, user_id=user_id)
    if gap is None:
        return {"error": "Gap not found or access denied."}

    if gap["status"] in {"resolved", "dismissed"}:
        return {
            "gapId": gap_id,
            "status": gap["status"],
            "remediations": [],
            "message": f"Gap is already {gap['status']}; no remediations generated.",
        }

    # Check existing remediations to avoid duplicates
    existing = list_gap_remediations(db_path, gap_id=gap_id, user_id=user_id) or []
    existing_types = {r["activityType"] for r in existing}

    # Determine which activities are relevant for this gap type
    gap_type = gap["gapType"]
    severity = gap["severity"]
    evidence_strength = gap["evidenceStrength"]
    recurrence = gap["recurrence"]

    relevant_types: list[str] = []
    if gap_type == "missing_prerequisite":
        # Prerequisite revision is essential
        relevant_types = ["prerequisite_revision", "targeted_explanation", "source_material", "focused_quiz"]
    elif gap_type == "weak_competency":
        relevant_types = ["targeted_explanation", "source_material", "practice_questions", "flashcards", "competency_assessment"]
    elif gap_type == "repeated_forgetting":
        relevant_types = ["scheduled_revision", "flashcards", "practice_questions", "targeted_explanation"]
    elif gap_type in {"incorrect_response", "repeated_misconception"}:
        relevant_types = ["targeted_explanation", "source_material", "practice_questions", "focused_quiz"]
    elif gap_type == "proficiency_gap":
        relevant_types = ["targeted_explanation", "source_material", "practice_questions", "competency_assessment"]
    elif gap_type == "unanswered_objective":
        relevant_types = ["targeted_explanation", "source_material", "focused_quiz"]
    else:
        relevant_types = ["targeted_explanation", "source_material", "practice_questions"]

    # Determine if prerequisite gap boost applies
    has_prerequisite_gap = gap_type == "missing_prerequisite"

    generated: list[dict[str, Any]] = []
    for activity_type in relevant_types:
        if activity_type in existing_types:
            continue  # Don't duplicate existing recommendations
        score = _priority_score(
            severity=severity,
            evidence_strength=evidence_strength,
            recurrence=recurrence,
            activity_type=activity_type,
            has_prerequisite_gap=has_prerequisite_gap,
        )
        config = _REMEDIATION_CONFIG[activity_type]
        rationale = (
            f"{config['description']} Priority boosted by "
            f"{_SEVERITY_PRIORITY_BOOST[severity]} for {severity} severity, "
            f"{_EVIDENCE_PRIORITY_BOOST[evidence_strength]} for {evidence_strength} evidence."
        )
        if has_prerequisite_gap and activity_type == "prerequisite_revision":
            rationale += " Additional boost for prerequisite gap."

        created = create_gap_remediation(
            db_path,
            gap_id=gap_id,
            user_id=user_id,
            activity_type=activity_type,
            title=f"{config['description'].capitalize()} for {gap['topic'] or gap['gapType']}",
            description=config["description"],
            priority_score=score,
            rationale=rationale,
        )
        if created:
            generated.append(created)

    # Sort by priority descending
    generated.sort(key=lambda r: -r["priorityScore"])

    return {
        "gapId": gap_id,
        "gapType": gap_type,
        "severity": severity,
        "remediations": generated,
        "generatedAt": now.isoformat(),
        "rankingMethod": (
            "base_priority + severity_boost + evidence_boost + "
            "recurrence_boost + prerequisite_boost"
        ),
    }


def accept_remediation_activity(
    *,
    database_path: str,
    remediation_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Learner accepts a remediation recommendation."""
    from app.storage import accept_remediation as _accept
    return _accept(database_path, remediation_id=remediation_id, user_id=user_id)


def dismiss_remediation_activity(
    *,
    database_path: str,
    remediation_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Learner dismisses a remediation recommendation."""
    from app.storage import dismiss_remediation as _dismiss
    return _dismiss(database_path, remediation_id=remediation_id, user_id=user_id)


def complete_remediation_activity(
    *,
    database_path: str,
    remediation_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Learner marks a remediation as completed."""
    from app.storage import complete_remediation as _complete
    return _complete(database_path, remediation_id=remediation_id, user_id=user_id)


def record_reassessment(
    *,
    database_path: str,
    gap_id: str,
    user_id: str,
    activity_id: str | None,
    before_mastery: float,
    after_mastery: float,
    resolved: bool = False,
) -> dict[str, Any] | None:
    """Record a reassessment comparing before/after mastery.

    A gap is only marked resolved if after_mastery >= 0.60 (mastery threshold)
    AND the learner explicitly requests resolution. Completion of an activity
    alone does NOT resolve a gap.
    """
    db_path = Path(database_path)
    gap = get_learning_gap(db_path, gap_id=gap_id, user_id=user_id)
    if gap is None:
        return {"error": "Gap not found or access denied."}

    # Only resolve if mastery threshold is met AND learner requests it
    can_resolve = resolved and after_mastery >= MASTERY_WEAK_THRESHOLD

    result = create_remediation_reassessment(
        db_path,
        gap_id=gap_id,
        user_id=user_id,
        activity_id=activity_id,
        before_mastery=before_mastery,
        after_mastery=after_mastery,
        resolved=can_resolve,
    )
    if result is None:
        return None

    return {
        "reassessmentId": result["reassessmentId"],
        "gapId": gap_id,
        "activityId": activity_id,
        "beforeMastery": before_mastery,
        "afterMastery": after_mastery,
        "improvement": result["improvement"],
        "resolved": can_resolve,
        "reassessedAt": result["reassessedAt"],
        "thresholdMet": after_mastery >= MASTERY_WEAK_THRESHOLD,
        "note": (
            "A gap is resolved only when post-remediation mastery meets the "
            "threshold AND the learner explicitly requests resolution."
        ),
    }


def get_remediation_dashboard(
    *,
    database_path: str,
    user_id: str,
) -> dict[str, Any]:
    """Get the learning improvement dashboard for a user."""
    db_path = Path(database_path)
    gaps = list_learning_gaps(db_path, user_id=user_id)

    counts = {
        "total": len(gaps),
        "critical": sum(1 for g in gaps if g["severity"] == "critical"),
        "moderate": sum(1 for g in gaps if g["severity"] == "moderate"),
        "minor": sum(1 for g in gaps if g["severity"] == "minor"),
        "detected": sum(1 for g in gaps if g["status"] == "detected"),
        "acknowledged": sum(1 for g in gaps if g["status"] == "acknowledged"),
        "remediating": sum(1 for g in gaps if g["status"] == "remediating"),
        "resolved": sum(1 for g in gaps if g["status"] == "resolved"),
        "dismissed": sum(1 for g in gaps if g["status"] == "dismissed"),
    }

    # Build dashboard entries with remediation status
    dashboard_gaps = []
    for gap in gaps:
        remediations = list_gap_remediations(db_path, gap_id=gap["gapId"], user_id=user_id) or []
        reassessments = list_remediation_reassessments(db_path, gap_id=gap["gapId"], user_id=user_id) or []

        # Latest reassessment
        latest_reassessment = reassessments[0] if reassessments else None

        # Remediation status
        accepted = [r for r in remediations if r["status"] == "accepted"]
        completed = [r for r in remediations if r["status"] == "completed"]
        dismissed_recs = [r for r in remediations if r["status"] == "dismissed"]

        dashboard_gaps.append({
            **gap,
            "remediationCount": len(remediations),
            "acceptedCount": len(accepted),
            "completedCount": len(completed),
            "dismissedCount": len(dismissed_recs),
            "latestReassessment": latest_reassessment,
            "hasUnaddressedRemediation": any(r["status"] == "recommended" for r in remediations),
        })

    # Sort: critical first, then by detected date descending
    dashboard_gaps.sort(
        key=lambda g: (
            {"critical": 0, "moderate": 1, "minor": 2}.get(g["severity"], 3),
            g["detectedAt"],
        ),
        reverse=False,
    )

    return {
        "userId": user_id,
        "counts": counts,
        "gaps": dashboard_gaps,
        "generatedAt": datetime.now(UTC).isoformat(),
    }
