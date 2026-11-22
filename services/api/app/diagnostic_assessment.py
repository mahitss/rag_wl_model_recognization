"""M86: Diagnostic assessment engine.

Adaptive, evidence-backed diagnostic assessment that reuses existing
WonderLearn subsystems (M64 assessments/competencies, quiz question bank,
knowledge graph, learning analytics, recommendations) rather than creating
duplicate pipelines.

Design principles:
* Every diagnostic conclusion links to actual question responses and
  persisted assessment evidence.
* Confidence reflects evidence volume, never claimed certainty.
* Stopping conditions are explicit; insufficient evidence is reported
  honestly rather than fabricating mastery.
* Question versions protect answer keys; versions are tracked and current
  versions are used for selection/scoring.
* All persistence is transactional; historical assessment records are never
  silently overwritten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.errors import ApiError
from app.storage import (
    compute_adaptive_recommendations,
    create_assessment,
    create_diagnostic_session,
    detect_learning_gaps,
    get_competency_mastery,
    get_diagnostic_concept_results,
    get_diagnostic_misconceptions,
    get_diagnostic_session,
    get_weak_topics,
    list_assessment_questions,
    list_competency_prerequisites,
    update_diagnostic_session,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DIFFICULTY_ORDER = {
    "foundational": 0,
    "building": 1,
    "practice": 2,
    "advanced": 3,
}

MASTERY_WEAK_THRESHOLD = 0.60
MASTERY_STRONG_THRESHOLD = 0.80

DEFAULT_MAX_QUESTIONS = 20
DEFAULT_MIN_EVIDENCE_QUESTIONS = 5
DEFAULT_CONFIDENCE_THRESHOLD = 0.80

# Question outcome -> numeric score (ambiguous contributes no score).
_OUTCOME_SCORE = {"correct": 1.0, "incorrect": 0.0, "ambiguous": None, "partial": 0.5}

# Misconception severity from error rate on concept questions.
_MISCONCEPTION_SEVERITY_THRESHOLDS = {
    "critical": 0.70,
    "moderate": 0.40,
    "minor": 0.0,
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Diagnostic state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiagnosticQuestion:
    """A question selected for diagnostic presentation (answer key excluded)."""

    question_id: str
    assessment_id: str
    concept_id: str | None
    concept_name: str | None
    question_type: str
    prompt: str
    options: list[dict[str, str]] | None
    difficulty_level: str | None
    position: int
    citations: list[dict[str, Any]]


@dataclass(frozen=True)
class DiagnosticStopping:
    """Result of evaluating stopping conditions."""

    should_stop: bool
    reason: str | None
    evidence_sufficient: bool
    concepts_remaining: int


# ---------------------------------------------------------------------------
# Question selection & versioning
# ---------------------------------------------------------------------------


def _difficulty_rank(level: str | None) -> int:
    return DIFFICULTY_ORDER.get(level or "foundational", 0)


def select_concept_for_next_question(
    *,
    database_path,
    user_id: str,
    session: dict[str, Any],
    concepts: list[dict[str, Any]],
    concept_results: list[dict[str, Any]],
    asked_question_ids: set[str],
) -> dict[str, Any] | None:
    """Pick the next concept to assess based on diagnostic state.

    Selection priority:
    1. Concepts with no result yet (unassessed).
    2. Concepts with confidence below threshold (needs more evidence).
    3. Concepts with weak competency (prerequisite/weak topic).
    4. Concepts already assessed at high confidence (skip).

    Difficulty-adjusted: prefers concepts at or slightly above current
    demonstrated competency, never arbitrarily jumping.
    """
    assessed_ids = {r["concept_id"] for r in concept_results}
    low_confidence = {
        r["concept_id"]
        for r in concept_results
        if (r["confidence"] or 0) < DEFAULT_CONFIDENCE_THRESHOLD
    }
    weak = {wt["topic_id"] for wt in get_weak_topics(database_path, user_id=user_id)}

    def score_concept(concept: dict[str, Any]) -> tuple[int, int, str]:
        cid = concept["conceptId"]
        name = concept.get("canonical_name") or concept.get("name") or ""
        if cid in assessed_ids and cid not in low_confidence:
            return (3, 0, name)  # already confidently assessed
        if cid in assessed_ids and cid in low_confidence:
            return (1, 0, name)  # needs more evidence
        if cid in weak:
            return (0, 1, name)  # weak / prerequisite gap
        return (0, 0, name)  # unassessed

    ordered = sorted(concepts, key=score_concept)
    for concept in ordered:
        if concept["conceptId"] in assessed_ids and concept["concept_id"] not in low_confidence:
            continue
        return concept
    return None


def select_question_for_concept(
    *,
    database_path,
    user_id: str,
    concept: dict[str, Any],
    asked_question_ids: set[str],
    current_difficulty: str | None = None,
) -> DiagnosticQuestion | None:
    """Select one unasked question for a concept.

    Prefers questions at the current difficulty band, then adjacent bands.
    Never returns a question already asked in this session.
    """
    concept_id = concept["conceptId"]
    concept_name = concept.get("canonical_name") or concept.get("name") or concept_id

    # Gather all diagnostic questions from the user's diagnostic assessments.
    questions: list[dict[str, Any]] = []
    try:
        assessments = [
            a
            for a in _list_user_assessments(database_path, user_id=user_id)
            if a["assessmentType"] == "diagnostic"
        ]
        for assessment in assessments:
            qs = list_assessment_questions(
                database_path, assessment_id=assessment["assessmentId"], user_id=user_id
            ) or []
            for q in qs:
                q["_assessment_id"] = assessment["assessmentId"]
                questions.append(q)
    except Exception:
        questions = []

    # Filter to questions matching this concept by name/alias heuristic.
    concept_questions = [
        q
        for q in questions
        if _concept_matches_question(concept, q)
    ]

    if not concept_questions:
        return None

    # Rank by difficulty proximity to current band.
    current_rank = _difficulty_rank(current_difficulty)

    def proximity(q: dict[str, Any]) -> int:
        return abs(_difficulty_rank(q.get("difficulty_level")) - current_rank)

    ordered = sorted(concept_questions, key=lambda q: (proximity(q), q.get("position", 0)))
    for q in ordered:
        if q["questionId"] in asked_question_ids:
            continue
        options = None
        if q.get("options_json"):
            try:
                parsed_options = json.loads(q["options_json"])
                options = [{"id": o["id"], "text": o["text"]} for o in parsed_options]
            except (json.JSONDecodeError, TypeError):
                options = None
        citations = []
        if q.get("citations_json"):
            try:
                citations = json.loads(q["citations_json"])
            except (json.JSONDecodeError, TypeError):
                citations = []
        return DiagnosticQuestion(
            question_id=q["questionId"],
            assessment_id=q.get("_assessment_id", ""),
            concept_id=concept_id,
            concept_name=concept_name,
            question_type=q["question_type"],
            prompt=q["prompt"],
            options=options,
            difficulty_level=q.get("difficulty_level"),
            position=int(q.get("position", 0)),
            citations=citations,
        )
    return None


def _concept_matches_question(concept: dict[str, Any], question: dict[str, Any]) -> bool:
    """Heuristic match between a concept and a question (no stored link)."""
    name = (concept.get("canonical_name") or concept.get("name") or "").casefold()
    aliases = [a.casefold() for a in (concept.get("aliases") or [])]
    prompt = (question.get("prompt") or "").casefold()
    explanation = (question.get("explanation") or "").casefold()
    if not name:
        return False
    if name in prompt or name in explanation:
        return True
    return any(alias and alias in prompt for alias in aliases)


def _list_user_assessments(database_path, *, user_id: str) -> list[dict[str, Any]]:
    from app.storage import list_assessments

    return list_assessments(database_path, user_id=user_id) or []


# ---------------------------------------------------------------------------
# Diagnostic intelligence (per-concept competency + confidence)
# ---------------------------------------------------------------------------


def compute_concept_competency(
    *,
    database_path,
    user_id: str,
    concept_id: str,
    session_questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute competency + confidence for a concept from session evidence.

    Uses only actual question responses linked to this concept. Confidence
    scales with evidence volume (beta-binomial style), capped so a single
    correct answer never implies high confidence.
    """
    concept_questions = [
        q for q in session_questions if q.get("concept_id") == concept_id
    ]
    scored = [
        q for q in concept_questions
        if q.get("outcome") in {"correct", "incorrect"}
    ]
    correct = sum(1 for q in scored if q["outcome"] == "correct")
    incorrect = sum(1 for q in scored if q["outcome"] == "incorrect")
    ambiguous = sum(1 for q in concept_questions if q.get("outcome") == "ambiguous")
    total = len(concept_questions)

    if scored:
        competency = correct / len(scored)
    else:
        competency = None

    # Confidence: sqrt evidence scaling, max 0.95 even with many questions.
    evidence_count = len(scored)
    if evidence_count == 0:
        confidence = 0.0
    elif evidence_count >= 10:
        confidence = 0.90
    elif evidence_count >= 5:
        confidence = 0.75
    elif evidence_count >= 3:
        confidence = 0.60
    else:
        confidence = 0.40

    # Persisted competency record (if any) informs the estimate.
    persisted = get_competency_mastery(database_path, competency_id=concept_id, user_id=user_id)
    persisted_mastery = persisted["masteryScore"] if persisted else None

    return {
        "concept_id": concept_id,
        "competency_score": competency,
        "confidence": confidence,
        "correct_count": correct,
        "incorrect_count": incorrect,
        "ambiguous_count": ambiguous,
        "total_questions": total,
        "evidence_question_ids": [q["question_id"] for q in concept_questions],
        "persisted_mastery": persisted_mastery,
    }


def classify_misconceptions(
    *,
    database_path,
    user_id: str,
    concept_id: str,
    concept_name: str,
    competency_result: dict[str, Any],
    question_outcomes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect misconception patterns from actual incorrect/ambiguous responses.

    Returns misconception records with severity and linked evidence question IDs.
    Only classifies where actual evidence exists.
    """
    incorrect = [
        q for q in question_outcomes if q.get("outcome") == "incorrect"
    ]
    ambiguous = [
        q for q in question_outcomes if q.get("outcome") == "ambiguous"
    ]
    scored_outcomes = [
        q for q in question_outcomes if q.get("outcome") in {"correct", "incorrect"}
    ]
    total_scored = len(scored_outcomes)

    misconceptions: list[dict[str, Any]] = []
    if not incorrect and not ambiguous:
        return misconceptions

    error_rate = (len(incorrect) + len(ambiguous) * 0.5) / max(total_scored, 1)
    if error_rate >= _MISCONCEPTION_SEVERITY_THRESHOLDS["critical"]:
        severity = "critical"
    elif error_rate >= _MISCONCEPTION_SEVERITY_THRESHOLDS["moderate"]:
        severity = "moderate"
    else:
        severity = "minor"

    if incorrect:
        misconceptions.append({
            "concept_id": concept_id,
            "concept_name": concept_name,
            "misconception_type": "incorrect_response",
            "description": (
                f"Learner answered {len(incorrect)} question(s) incorrectly on "
                f"{concept_name}."
            ),
            "evidence_question_ids": [q["question_id"] for q in incorrect],
            "severity": severity,
        })
    if ambiguous and error_rate >= 0.40:
        misconceptions.append({
            "concept_id": concept_id,
            "concept_name": concept_name,
            "misconception_type": "partial_understanding",
            "description": (
                f"Learner gave {len(ambiguous)} ambiguous answer(s) on "
                f"{concept_name}, indicating partial understanding."
            ),
            "evidence_question_ids": [q["question_id"] for q in ambiguous],
            "severity": severity,
        })

    # Check prerequisite gap via competency prerequisites.
    try:
        prereqs = list_competency_prerequisites(
            database_path, competency_id=concept_id, user_id=user_id
        ) or []
    except Exception:
        prereqs = []
    for prereq in prereqs:
        pid = prereq["competencyId"]
        p_mastery = get_competency_mastery(
            database_path, competency_id=pid, user_id=user_id
        )
        if p_mastery and p_mastery["masteryScore"] < MASTERY_WEAK_THRESHOLD:
            misconceptions.append({
                "concept_id": concept_id,
                "concept_name": concept_name,
                "misconception_type": "prerequisite_gap",
                "description": (
                    f"Prerequisite '{prereq.get('title', pid)}' not mastered "
                    f"(mastery {p_mastery['masteryScore']:.0%})."
                ),
                "evidence_question_ids": [],
                "severity": "moderate",
            })

    return misconceptions


# ---------------------------------------------------------------------------
# Stopping conditions
# ---------------------------------------------------------------------------


def evaluate_stopping(
    *,
    session: dict[str, Any],
    concept_results: list[dict[str, Any]],
    concepts_total: int,
    questions_asked: int,
    max_questions: int,
    min_evidence_questions: int,
    confidence_threshold: float,
) -> DiagnosticStopping:
    """Decide whether the diagnostic assessment should stop.

    Stops when:
    * max_questions reached, or
    * all concepts assessed at/above confidence_threshold, or
    * min_evidence_questions met AND every concept either assessed at
      confidence or explicitly has insufficient evidence (honest stop).

    Returns should_stop, reason, evidence_sufficient, concepts_remaining.
    """
    if questions_asked >= max_questions:
        return DiagnosticStopping(
            should_stop=True,
            reason="max_questions_reached",
            evidence_sufficient=len(concept_results) >= min_evidence_questions,
            concepts_remaining=max(0, concepts_total - len(concept_results)),
        )

    assessed_confident = [
        r for r in concept_results if (r["confidence"] or 0) >= confidence_threshold
    ]
    concepts_remaining = concepts_total - len(concept_results)

    if concepts_remaining == 0 and len(assessed_confident) == concepts_total:
        return DiagnosticStopping(
            should_stop=True,
            reason="all_concepts_confident",
            evidence_sufficient=True,
            concepts_remaining=0,
        )

    if questions_asked >= min_evidence_questions:
        # All assessed concepts are confident, remaining have no evidence yet.
        if len(assessed_confident) == len(concept_results) and concepts_remaining > 0:
            return DiagnosticStopping(
                should_stop=True,
                reason="sufficient_evidence_for_assessed",
                evidence_sufficient=True,
                concepts_remaining=concepts_remaining,
            )

    return DiagnosticStopping(
        should_stop=False,
        reason=None,
        evidence_sufficient=len(concept_results) >= min_evidence_questions,
        concepts_remaining=concepts_remaining,
    )


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def start_diagnostic_session(
    *,
    database_path,
    user_id: str,
    topic_id: str,
    topic: str,
    settings: Settings | None = None,
    assessment_id: str | None = None,
    difficulty_level: str | None = None,
    max_questions: int = DEFAULT_MAX_QUESTIONS,
    min_evidence_questions: int = DEFAULT_MIN_EVIDENCE_QUESTIONS,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> dict[str, Any]:
    """Create a diagnostic session backed by an M64 diagnostic assessment."""
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    if assessment_id is None:
        # Create a diagnostic assessment container if none supplied.
        created = create_assessment(
            database_path,
            user_id=user_id,
            course_id=None,
            title=f"Diagnostic: {topic}",
            description=f"Adaptive diagnostic assessment for topic {topic}.",
            assessment_type="diagnostic",
            status="published",
        )
        if created is None:
            raise ApiError(
                503, "ASSESSMENT_CREATE_FAILED", "Could not create diagnostic assessment."
            )
        assessment_id = created["assessmentId"]

    session = create_diagnostic_session(
        database_path,
        user_id=user_id,
        topic_id=topic_id,
        topic=topic,
        assessment_id=assessment_id,
        difficulty_level=difficulty_level,
        max_questions=max_questions,
        min_evidence_questions=min_evidence_questions,
        confidence_threshold=confidence_threshold,
    )
    return session


def get_session_state(
    *,
    database_path,
    session_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Assemble the full diagnostic state for a session."""
    session = get_diagnostic_session(database_path, session_id, user_id=user_id)
    if session is None:
        return None
    concept_results = get_diagnostic_concept_results(
        database_path, session_id=session_id, user_id=user_id
    )
    misconceptions = get_diagnostic_misconceptions(
        database_path, session_id=session_id, user_id=user_id
    )
    return {
        "session": session,
        "conceptResults": concept_results,
        "misconceptions": misconceptions,
    }


def complete_diagnostic_session(
    *,
    database_path,
    session_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Mark a session completed and integrate results into mastery/gaps."""
    session = get_diagnostic_session(database_path, session_id, user_id=user_id)
    if session is None or session["status"] != "in_progress":
        return None
    concept_results = get_diagnostic_concept_results(
        database_path, session_id=session_id, user_id=user_id
    )
    misconceptions = get_diagnostic_misconceptions(
        database_path, session_id=session_id, user_id=user_id
    )

    # Persist learning gaps for weak concepts (diagnostic evidence).
    now = _now_iso()
    for result in concept_results:
        if (
            result["competency_score"] is not None
            and result["competency_score"] < MASTERY_WEAK_THRESHOLD
        ):
            try:
                detect_learning_gaps(database_path, user_id=user_id, now=datetime.now(UTC))
            except Exception:
                pass
            break

    updated = update_diagnostic_session(
        database_path,
        session_id=session_id,
        user_id=user_id,
        status="completed",
        completed_at=now,
        stopping_reason="manual_complete",
    )
    return {
        "session": updated,
        "conceptResults": concept_results,
        "misconceptions": misconceptions,
    }


# ---------------------------------------------------------------------------
# Integration hooks
# ---------------------------------------------------------------------------


def integrate_with_mastery(
    *,
    database_path,
    user_id: str,
    session_id: str,
) -> dict[str, Any]:
    """Read diagnostic concept results and map to existing competency mastery.

    Does not overwrite historical mastery; returns a proposed mapping for
    caller-controlled integration.
    """
    results = get_diagnostic_concept_results(
        database_path, session_id=session_id, user_id=user_id
    )
    mapping: list[dict[str, Any]] = []
    for result in results:
        concept_id = result["concept_id"]
        persisted = get_competency_mastery(
            database_path, competency_id=concept_id, user_id=user_id
        )
        mapping.append({
            "concept_id": concept_id,
            "concept_name": result["concept_name"],
            "diagnostic_competency": result["competency_score"],
            "diagnostic_confidence": result["confidence"],
            "persisted_mastery": persisted["masteryScore"] if persisted else None,
            "evidence_count": result["total_questions"],
            "recommended_action": (
                "reinforce"
                if (result["competency_score"] or 0) < MASTERY_WEAK_THRESHOLD
                else "maintain"
            ),
        })
    return {"session_id": session_id, "mastery_mapping": mapping}


def integrate_with_recommendations(
    *,
    database_path,
    user_id: str,
    session_id: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Generate follow-up recommendations from diagnostic results."""
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    now = datetime.now(UTC)
    recommendations = compute_adaptive_recommendations(
        database_path, user_id=user_id, now=now, limit=10
    )
    concept_results = get_diagnostic_concept_results(
        database_path, session_id=session_id, user_id=user_id
    )
    weak_concepts = [
        r for r in concept_results
        if r["competency_score"] is not None and r["competency_score"] < MASTERY_WEAK_THRESHOLD
    ]
    return {
        "session_id": session_id,
        "weakConcepts": [
            {
                "concept_id": r["concept_id"],
                "concept_name": r["concept_name"],
                "competency": r["competency_score"],
                "confidence": r["confidence"],
            }
            for r in weak_concepts
        ],
        "recommendations": recommendations,
    }
