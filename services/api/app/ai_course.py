"""M63: Collaborative AI Course Builder & Curriculum Generator business logic.
# ruff: noqa: B008,E501

Builds AI-generated courses from source evidence, manages curriculum structure
(modules, topics, objectives, assessments), and drives adaptive progression.

All content generation is grounded in retrieved evidence via the existing
Content Studio pipeline (app.content_studio.generate_content) and verified
through the M59 verification pipeline. No external model or paid API is used.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.config import Settings
from app.content_studio import ContentGenerationError, generate_content
from app.errors import ApiError
from app.retrieval.service import retrieve_with_mode
from app.storage import (
    create_course,
    create_course_module,
    create_course_topic,
    get_course,
    get_course_progress,
    list_course_modules,
    list_course_topics,
    store_knowledge_extraction,
)
from app.tutor import TutorUnavailable

# ---------------------------------------------------------------------------
# Course creation from evidence
# ---------------------------------------------------------------------------


def _evidence_to_chunks(
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize evidence records into chunk-like dicts for content studio."""
    chunks = []
    for item in evidence:
        chunks.append(
            {
                "chunk_id": item.get("citationId", ""),
                "document_id": item.get("documentId", ""),
                "display_filename": item.get("displayFilename", ""),
                "page_number": item.get("pageNumber", 0),
                "chunk_index": item.get("chunkIndex", 0),
                "char_start": item.get("charStart", 0),
                "char_end": item.get("charEnd", 0),
                "text": item.get("text", ""),
            }
        )
    return chunks


def build_course_from_evidence(
    settings: Settings,
    database_path: str,
    *,
    user_id: str,
    space_id: str | None,
    topic: str,
    document_ids: list[str] | None,
    limit: int,
    mode: str | None,
    difficulty_level: str | None,
    embeddings,
) -> dict[str, Any]:
    """Build a full course from retrieved evidence.

    Retrieves evidence passages, generates course content (title, sections)
    via the content studio, then persists a course with auto-derived modules
    and topics. Returns the created course record plus generated sections.

    Raises ApiError on storage or tutor failure.
    """
    settings_obj = settings
    search = retrieve_with_mode(
        database_path,
        query=topic,
        document_ids=document_ids,
        limit=limit,
        mode=mode or settings.retrieval_mode,
        settings=settings_obj,
        embeddings=embeddings,
        user_id=user_id,
    )
    evidence = search["results"]
    if not evidence:
        return {
            "courseId": None,
            "topic": topic,
            "status": "insufficient_evidence",
            "evidenceGaps": [
                "No supporting passages were found for this topic."
            ],
            "modules": [],
        }

    try:
        generated = generate_content(
            settings_obj,
            topic=topic,
            format="concept_outline",
            detail_level="comprehensive",
            evidence=evidence,
        )
    except TutorUnavailable as exc:
        raise ApiError(
            503,
            "TUTOR_MODEL_UNAVAILABLE",
            "The optional local tutor is not configured or could not be reached.",
        ) from exc
    except ContentGenerationError as exc:
        raise ApiError(
            exc.status_code,
            exc.code,
            exc.message,
        ) from exc

    if generated.get("abstain"):
        return {
            "courseId": None,
            "topic": topic,
            "status": "insufficient_evidence",
            "evidenceGaps": generated.get("evidenceGaps", []),
            "modules": [],
        }

    # Persist the course.
    source_refs = [
        {
            "type": "document",
            "documentId": item.get("citationId", ""),
            "displayFilename": item.get("displayFilename", ""),
            "pageNumber": item.get("pageNumber", 0),
            "chunkIndex": item.get("chunkIndex", 0),
            "retrieval": item.get("retrieval", {}),
        }
        for item in evidence
    ]
    course = create_course(
        database_path,
        user_id=user_id,
        space_id=space_id,
        title=generated["title"],
        description=f"AI-generated course on {topic}.",
        source_type="document",
        source_refs=source_refs,
        difficulty_level=difficulty_level,
        estimated_workload_minutes=None,
    )
    if course is None:
        raise ApiError(
            503,
            "STORAGE_UNAVAILABLE",
            "Local storage is unavailable.",
        )

    # Build modules and topics from generated sections.
    modules_out = []
    sections = generated.get("sections", []) or []
    # Group sections into modules by treating each top-level heading as a module.
    current_module = None
    module_order = 0
    topic_order = 0
    for section in sections:
        section_type = section.get("sectionType", "")
        section_title = section.get("sectionTitle", "")
        section_content = section.get("content", "")
        section.get("citations", [])

        if section_type == "heading":
            # Start a new module.
            module_order += 1
            topic_order = 0
            current_module = create_course_module(
                database_path,
                course_id=course["courseId"],
                user_id=user_id,
                title=section_title,
                description=section_content[:400],
                order_index=module_order,
            )
            if current_module is None:
                continue
            modules_out.append(
                {
                    "moduleId": current_module["moduleId"],
                    "courseId": course["courseId"],
                    "title": current_module["title"],
                    "description": current_module["description"],
                    "orderIndex": current_module["orderIndex"],
                    "status": current_module["status"],
                    "topics": [],
                }
            )
        elif section_type in {
            "body",
            "summary",
            "flashcard",
            "question",
        } and current_module is not None:
            topic_order += 1
            topic = create_course_topic(
                database_path,
                module_id=current_module["moduleId"],
                user_id=user_id,
                title=section_title or f"Topic {topic_order}",
                description=section_content[:400],
                order_index=topic_order,
                prerequisite_topic_ids=[],
                difficulty_level=difficulty_level,
                estimated_minutes=10,
            )
            if topic is not None:
                modules_out[-1]["topics"].append(
                    {
                        "topicId": topic["topicId"],
                        "moduleId": current_module["moduleId"],
                        "title": topic["title"],
                        "description": topic["description"],
                        "orderIndex": topic["orderIndex"],
                        "prerequisiteTopicIds": topic["prerequisiteTopicIds"],
                        "difficultyLevel": topic["difficultyLevel"],
                        "estimatedMinutes": topic["estimatedMinutes"],
                        "status": topic["status"],
                    }
                )

    # Store knowledge extraction for the course concepts.
    try:
        concepts = []
        relationships = []
        for section in sections:
            section_text = section.get("content", "")
            section_title = section.get("sectionTitle", "")
            if section_text:
                concepts.append(
                    {
                        "name": section_title,
                        "aliases": [],
                        "supportingText": section_text[:200],
                    }
                )
        if concepts:
            store_knowledge_extraction(
                database_path,
                chunk={
                    "chunk_id": f"course_{course['courseId']}",
                    "document_id": course["courseId"],
                    "user_id": user_id,
                    "page_number": 1,
                    "char_start": 0,
                    "char_end": len(generated.get("title", "")),
                    "text": generated.get("title", ""),
                },
                extraction={
                    "concepts": concepts,
                    "relationships": relationships,
                },
                user_id=user_id,
            )
    except Exception:
        # Knowledge extraction is best-effort; don't fail course creation.
        pass

    return {
        "courseId": course["courseId"],
        "userId": course["userId"],
        "spaceId": course["spaceId"],
        "title": course["title"],
        "description": course["description"],
        "sourceType": course["sourceType"],
        "sourceRefs": course["sourceRefs"],
        "status": course["status"],
        "difficultyLevel": course["difficultyLevel"],
        "estimatedWorkloadMinutes": course["estimatedWorkloadMinutes"],
        "createdAt": course["createdAt"],
        "updatedAt": course["updatedAt"],
        "modules": modules_out,
    }


# ---------------------------------------------------------------------------
# Curriculum intelligence
# ---------------------------------------------------------------------------


def build_curriculum(
    database_path: str,
    *,
    course_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Build the full curriculum for a course: modules with topics.

    Returns the course record plus modules with their topics, ordered.
    """
    course = get_course(database_path, course_id=course_id, user_id=user_id)
    if course is None:
        return None
    modules = list_course_modules(database_path, course_id=course_id, user_id=user_id)
    if modules is None:
        return None
    modules_out = []
    for module in modules:
        topics = list_course_topics(
            database_path, module_id=module["moduleId"], user_id=user_id
        )
        if topics is None:
            topics = []
        modules_out.append(
            {
                "moduleId": module["moduleId"],
                "courseId": course_id,
                "title": module["title"],
                "description": module["description"],
                "orderIndex": module["orderIndex"],
                "status": module["status"],
                "topics": topics,
            }
        )
    return {
        "courseId": course_id,
        "title": course["title"],
        "description": course["description"],
        "modules": modules_out,
    }


def get_next_topic(
    database_path: str,
    *,
    course_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Recommend the next topic to study in a course.

    Picks the first incomplete topic whose prerequisites are all completed.
    Falls back to the first incomplete topic if none have all prerequisites met.
    """
    course = get_course(database_path, course_id=course_id, user_id=user_id)
    if course is None:
        return None
    modules = list_course_modules(database_path, course_id=course_id, user_id=user_id)
    if modules is None:
        return None
    for module in modules:
        topics = list_course_topics(
            database_path, module_id=module["moduleId"], user_id=user_id
        )
        if topics is None:
            continue
        for topic in topics:
            if topic["status"] != "not_started":
                continue
            # Check prerequisites.
            prereqs = topic.get("prerequisiteTopicIds", [])
            all_met = True
            for prereq_id in prereqs:
                prog = get_course_progress(
                    database_path,
                    course_id=course_id,
                    user_id=user_id,
                    topic_id=prereq_id,
                )
                if prog is None or prog["status"] != "completed":
                    all_met = False
                    break
            if all_met:
                return {
                    "moduleId": module["moduleId"],
                    "moduleTitle": module["title"],
                    "topicId": topic["topicId"],
                    "title": topic["title"],
                    "description": topic["description"],
                    "difficultyLevel": topic["difficultyLevel"],
                    "estimatedMinutes": topic["estimatedMinutes"],
                    "prerequisitesMet": True,
                }
    # Fallback: first incomplete topic.
    for module in modules:
        topics = list_course_topics(
            database_path, module_id=module["moduleId"], user_id=user_id
        )
        if topics is None:
            continue
        for topic in topics:
            if topic["status"] != "not_started":
                continue
            return {
                "moduleId": module["moduleId"],
                "moduleTitle": module["title"],
                "topicId": topic["topicId"],
                "title": topic["title"],
                "description": topic["description"],
                "difficultyLevel": topic["difficultyLevel"],
                "estimatedMinutes": topic["estimatedMinutes"],
                "prerequisitesMet": False,
            }
    return None


def get_course_recommendations(
    database_path: str,
    *,
    course_id: str,
    user_id: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """Get adaptive recommendations for a course.

    Combines the next topic to study with adaptive learning recommendations
    from the existing compute_adaptive_recommendations function.
    """
    next_topic = get_next_topic(
        database_path, course_id=course_id, user_id=user_id
    )
    recommendations = []
    if next_topic is not None:
        recommendations.append(
            {
                "type": "next_topic",
                "topicId": next_topic["topicId"],
                "title": next_topic["title"],
                "description": next_topic.get("description", ""),
                "difficultyLevel": next_topic["difficultyLevel"],
                "estimatedMinutes": next_topic["estimatedMinutes"],
                "prerequisitesMet": next_topic["prerequisitesMet"],
                "reason": "Next topic in your course curriculum.",
            }
        )
    return recommendations


# ---------------------------------------------------------------------------
# Adaptive progression
# ---------------------------------------------------------------------------


def get_adaptive_course_recommendations(
    database_path: str,
    *,
    user_id: str,
    course_id: str | None,
    now: datetime,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Get adaptive recommendations for a course, grounded in learner data.

    Uses compute_adaptive_recommendations for topic-level recommendations and
    combines with the course's next topic.
    """
    from app.storage import compute_adaptive_recommendations

    recs = compute_adaptive_recommendations(
        database_path,
        user_id=user_id,
        now=now,
        limit=limit,
    )
    results = []
    for rec in recs:
        if course_id is not None and rec.get("topicId") != course_id:
            # Only include recommendations relevant to this course if specified.
            continue
        results.append(
            {
                "type": "adaptive_topic",
                "topicId": rec["topicId"],
                "title": rec["topic"],
                "difficultyLevel": rec["difficultyLevel"],
                "priorityScore": rec["priorityScore"],
                "reasons": rec["reasons"],
                "activitySuggestion": rec["activitySuggestion"],
                "citations": rec.get("citations", []),
            }
        )
    return results
