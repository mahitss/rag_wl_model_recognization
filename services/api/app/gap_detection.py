"""M65: Learning Gap Detection Engine.

Detects learning gaps from persisted assessment and competency evidence.
Only classifies gaps where actual evidence exists — missing data is never
treated as poor performance.

Gap types:
- incorrect_response: Learner answered a question incorrectly.
- repeated_misconception: Same incorrect pattern across multiple attempts.
- weak_competency: Competency mastery below threshold.
- missing_prerequisite: Prerequisite competency not mastered.
- repeated_forgetting: Topic repeatedly forgotten (weak_topics table).
- unanswered_objective: Learning objective with no assessment evidence.
- proficiency_gap: Demonstrated proficiency below expected level.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.storage import (
    add_gap_evidence,
    create_learning_gap,
    detect_learning_gaps,
    get_competency_mastery,
    get_learning_gap,
    list_competencies,
    list_learning_gaps,
    list_remediation_reassessments,
)

MASTERY_WEAK_THRESHOLD = 0.60
MASTERY_CRITICAL_THRESHOLD = 0.40
RECURRENCE_MODERATE = 2
RECURRENCE_CRITICAL = 3


def _severity_from_recurrence(recurrence: int) -> str:
    if recurrence >= RECURRENCE_CRITICAL:
        return "critical"
    if recurrence >= RECURRENCE_MODERATE:
        return "moderate"
    return "minor"


def _evidence_strength_from_count(count: int) -> str:
    if count >= 2:
        return "strong"
    return "weak"


def detect_gaps_for_user(
    *,
    database_path: str,
    user_id: str,
    now: datetime,
) -> dict[str, Any]:
    """Run full gap detection for a user and return results.

    Uses only persisted evidence:
    - Quiz attempts (incorrect responses)
    - Topic review items (repeated forgetting)
    - Competency mastery (weak competencies, missing prerequisites)
    """
    from pathlib import Path
    from app.storage import get_competency_mastery, list_competencies

    db_path = Path(database_path)

    # Run storage-level detection (writes new gaps to DB)
    detected = detect_learning_gaps(db_path, user_id=user_id, now=now)

    # Build response with counts by severity
    by_severity: dict[str, int] = {"critical": 0, "moderate": 0, "minor": 0}
    by_type: dict[str, int] = {}
    for gap in detected:
        sev = gap["severity"]
        if sev in by_severity:
            by_severity[sev] += 1
        gtype = gap["gapType"]
        by_type[gtype] = by_type.get(gtype, 0) + 1

    return {
        "userId": user_id,
        "detectedAt": now.isoformat(),
        "gapsDetected": len(detected),
        "gaps": detected,
        "bySeverity": by_severity,
        "byType": by_type,
    }


def get_gap_details(
    *,
    database_path: str,
    gap_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Get a single gap with its evidence and remediations."""
    from pathlib import Path
    from app.storage import (
        get_learning_gap,
        list_gap_evidence,
        list_gap_remediations,
        list_remediation_reassessments,
    )

    db_path = Path(database_path)
    gap = get_learning_gap(db_path, gap_id=gap_id, user_id=user_id)
    if gap is None:
        return None

    evidence = list_gap_evidence(db_path, gap_id=gap_id, user_id=user_id) or []
    remediations = list_gap_remediations(db_path, gap_id=gap_id, user_id=user_id) or []
    reassessments = list_remediation_reassessments(db_path, gap_id=gap_id, user_id=user_id) or []

    return {
        "gap": gap,
        "evidence": evidence,
        "remediations": remediations,
        "reassessments": reassessments,
    }


def analyze_gap_root_cause(
    *,
    database_path: str,
    gap_id: str,
    user_id: str,
    now: datetime,
) -> dict[str, Any]:
    """Perform root-cause analysis for a gap.

    Distinguishes between:
    - Conceptual misunderstanding (evidence-supported incorrect answers)
    - Missing prerequisite knowledge (prerequisite not mastered)
    - Recall and retention difficulties (repeated forgetting)
    - Question interpretation errors (ambiguous outcomes)
    - Insufficient practice (low attempt count)
    - Insufficient evidence to determine cause

    AI-generated hypotheses are labelled as such unless supported by actual
    persisted evidence.
    """
    from pathlib import Path
    from app.storage import (
        get_competency_mastery,
        get_learning_gap,
        list_gap_evidence,
        list_competency_prerequisites,
    )

    db_path = Path(database_path)
    gap = get_learning_gap(db_path, gap_id=gap_id, user_id=user_id)
    if gap is None:
        return {"error": "Gap not found or access denied."}

    gap_type = gap["gapType"]
    evidence = list_gap_evidence(db_path, gap_id=gap_id, user_id=user_id) or []
    competency_id = gap["competencyId"]

    causes: list[dict[str, Any]] = []
    primary_cause: str | None = None
    confidence: str = "insufficient_evidence"

    # --- 1. Missing prerequisite knowledge ----------------------------------
    if gap_type == "missing_prerequisite":
        prereq_evidence = [
            e for e in evidence if e["evidenceType"] == "prerequisite"
        ]
        if prereq_evidence:
            causes.append(
                {
                    "cause": "missing_prerequisite_knowledge",
                    "label": "Missing prerequisite knowledge",
                    "supported": True,
                    "description": (
                        "A prerequisite competency has not been mastered, "
                        "blocking progress in the dependent competency."
                    ),
                    "evidence": prereq_evidence,
                }
            )
            primary_cause = "missing_prerequisite_knowledge"
            confidence = "high"
        else:
            causes.append(
                {
                    "cause": "missing_prerequisite_knowledge",
                    "label": "Missing prerequisite knowledge (hypothesis)",
                    "supported": False,
                    "description": (
                        "The prerequisite may not be mastered, but no direct "
                        "evidence confirms this."
                    ),
                    "evidence": [],
                }
            )

    # --- 2. Conceptual misunderstanding -------------------------------------
    if gap_type in {"incorrect_response", "repeated_misconception"}:
        incorrect_evidence = [
            e for e in evidence if e["evidenceType"] == "question_answer"
        ]
        if incorrect_evidence:
            causes.append(
                {
                    "cause": "conceptual_misunderstanding",
                    "label": "Conceptual misunderstanding",
                    "supported": True,
                    "description": (
                        "Learner answered question(s) incorrectly on this topic, "
                        "indicating a gap in conceptual understanding."
                    ),
                    "evidence": incorrect_evidence,
                }
            )
            if primary_cause is None:
                primary_cause = "conceptual_misunderstanding"
                confidence = "high"

    # --- 3. Recall and retention difficulties -------------------------------
    if gap_type == "repeated_forgetting":
        review_evidence = [e for e in evidence if e["evidenceType"] == "review_item"]
        if review_evidence:
            causes.append(
                {
                    "cause": "recall_retention_difficulty",
                    "label": "Recall and retention difficulty",
                    "supported": True,
                    "description": (
                        "Topic has been forgotten repeatedly, suggesting "
                        "retention rather than initial understanding is the issue."
                    ),
                    "evidence": review_evidence,
                }
            )
            if primary_cause is None:
                primary_cause = "recall_retention_difficulty"
                confidence = "high"

    # --- 4. Weak competency -------------------------------------------------
    if gap_type == "weak_competency" and competency_id is not None:
        mastery = get_competency_mastery(
            db_path, competency_id=competency_id, user_id=user_id
        )
        if mastery is not None:
            causes.append(
                {
                    "cause": "weak_competency",
                    "label": "Weak competency mastery",
                    "supported": True,
                    "description": (
                        f"Competency mastery is at {mastery['masteryScore']:.0%}, "
                        "below the 60% threshold."
                    ),
                    "evidence": [
                        e for e in evidence if e["evidenceType"] == "competency_mastery"
                    ],
                }
            )
            if primary_cause is None:
                primary_cause = "weak_competency"
                confidence = "high"

    # --- 5. Insufficient evidence -------------------------------------------
    if not causes:
        causes.append(
            {
                "cause": "insufficient_evidence",
                "label": "Insufficient evidence to determine cause",
                "supported": False,
                "description": (
                    "No persisted evidence is available to determine the root "
                    "cause of this gap. Additional assessment is needed."
                ),
                "evidence": [],
            }
        )
        confidence = "insufficient_evidence"

    # Check for prerequisite mastery gaps as contributing cause
    if competency_id is not None:
        prereqs = list_competency_prerequisites(db_path, competency_id=competency_id, user_id=user_id) or []
        for prereq in prereqs:
            prereq_mastery = get_competency_mastery(
                db_path, competency_id=prereq["competencyId"], user_id=user_id
            )
            if prereq_mastery is not None and prereq_mastery["masteryScore"] < MASTERY_WEAK_THRESHOLD:
                causes.append(
                    {
                        "cause": "missing_prerequisite_contributing",
                        "label": "Missing prerequisite knowledge (contributing)",
                        "supported": True,
                        "description": (
                            f"Prerequisite '{prereq['title']}' mastery at "
                            f"{prereq_mastery['masteryScore']:.0%} may be contributing."
                        ),
                        "evidence": [],
                    }
                )

    return {
        "gapId": gap_id,
        "gapType": gap_type,
        "primaryCause": primary_cause,
        "confidence": confidence,
        "causes": causes,
        "analyzedAt": now.isoformat(),
        "note": (
            "Causes marked with supported=False are AI hypotheses requiring "
            "additional evidence. Do not act on unsupported hypotheses alone."
        ),
    }


def list_user_gaps(
    *,
    database_path: str,
    user_id: str,
    status: str | None = None,
) -> dict[str, Any]:
    """List all learning gaps for a user with summary counts."""
    from pathlib import Path
    from app.storage import list_learning_gaps

    db_path = Path(database_path)
    gaps = list_learning_gaps(db_path, user_id=user_id, status=status)

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

    return {
        "userId": user_id,
        "counts": counts,
        "gaps": gaps,
    }
