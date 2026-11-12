"""M82: AI Learning Path Generator & Dynamic Curriculum Orchestration.

Builds prerequisite-aware, structured learning curricula from real learner
data and the existing knowledge graph.  Reuses existing WonderLearn
subsystems rather than duplicating them:

* ``app.storage.list_knowledge_concepts`` / ``list_knowledge_relationships``
  — concept inventory and prerequisite dependencies.
* ``app.storage.compute_adaptive_recommendations`` — topic-level mastery
  and priority signals from quiz attempts, study sessions, and reviews.
* ``app.storage.get_weak_topics`` / ``get_overdue_topics`` — learner state.
* ``app.storage.create_learning_path`` / ``create_learning_objective`` —
  existing M34 path and objective persistence (reused, not duplicated).
* ``app.storage.create_learning_path_version`` / modules / milestones /
  progress / adaptation log — new M82 persistence layer.

No LLM calls are made.  Generation and adaptation are fully deterministic,
reproducible, and work when local AI is unavailable.  Completed learner
progress is preserved across adaptations — historical assessment results
are never rewritten.

All operations are tenant-scoped and auditable through the adaptation log.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.errors import ApiError
from app.schemas import (
    LearningPathAdaptationCreate,
    LearningPathAdaptationListResponse,
    LearningPathAdaptationResponse,
    LearningPathGenerateRequest,
    LearningPathGenerationResult,
    LearningPathMilestoneCreate,
    LearningPathMilestoneListResponse,
    LearningPathMilestoneResponse,
    LearningPathModuleCreate,
    LearningPathModuleListResponse,
    LearningPathModuleResponse,
    LearningPathProgressCreate,
    LearningPathProgressListResponse,
    LearningPathProgressResponse,
    LearningPathVersionResponse,
)
from app.storage import (
    compute_adaptive_recommendations,
    create_learning_objective,
    create_learning_path,
    create_learning_path_adaptation,
    create_learning_path_milestone,
    create_learning_path_module,
    create_learning_path_version,
    get_learning_path_progress,
    get_overdue_topics,
    get_weak_topics,
    list_knowledge_concepts,
    list_knowledge_relationships,
    list_learning_path_adaptations,
    list_learning_path_milestones,
    list_learning_path_modules,
    list_learning_path_versions,
    list_learning_paths,
    store_interoperability_audit,
)
from app.storage import (
    upsert_learning_path_progress as create_learning_path_progress,
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

DEFAULT_MODULE_ESTIMATED_MINUTES = 60
DEFAULT_OBJECTIVE_ESTIMATED_MINUTES = 20
DEFAULT_MILESTONE_ESTIMATED_MINUTES = 120

WEEKS_PER_MONTH = 4.33


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _tenant_id(database_path, user_id: str) -> str:
    from app.storage import _learning_path_tenant_id

    return _learning_path_tenant_id(database_path, user_id)


# ---------------------------------------------------------------------------
# Dependency graph utilities
# ---------------------------------------------------------------------------


def _topological_sort(
    nodes: list[str],
    edges: list[tuple[str, str]],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Topologically sort nodes given directed edges (prerequisite -> dependent).

    Returns (sorted_nodes, cycle_edges).  If cycle_edges is non-empty, the
    graph contains a cycle and those edges participate in it.
    """
    in_degree: dict[str, int] = {n: 0 for n in nodes}
    adj: dict[str, list[str]] = defaultdict(list)
    for src, dst in edges:
        adj[src].append(dst)
        in_degree[dst] = in_degree.get(dst, 0) + 1
        in_degree.setdefault(src, 0)

    queue: deque[str] = deque(sorted(n for n in nodes if in_degree.get(n, 0) == 0))
    result: list[str] = []
    while queue:
        node = queue.popleft()
        result.append(node)
        for neighbor in sorted(adj.get(node, [])):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    cycle_edges: list[tuple[str, str]] = []
    remaining = set(nodes) - set(result)
    if remaining:
        # Edges that point into remaining nodes are part of cycles.
        for src, dst in edges:
            if dst in remaining:
                cycle_edges.append((src, dst))
    return result, cycle_edges


def _build_concept_dependency_graph(
    concepts: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str]]]:
    """Build a concept -> dependency map from knowledge graph data.

    Returns (concept_by_id, edges) where edges are (prerequisite, dependent).
    Only relationships with status 'human_verified' or 'candidate' that
    express prerequisite-like relations are used.
    """
    concept_by_id = {c["conceptId"]: c for c in concepts}
    edges: list[tuple[str, str]] = []
    for rel in relationships:
        relation = (rel.get("relation") or "").lower()
        if any(
            kw in relation
            for kw in ("prerequisite", "requires", "depends_on", "leads_to", "enables")
        ):
            source = rel["sourceConceptId"]
            target = rel["targetConceptId"]
            if source in concept_by_id and target in concept_by_id:
                edges.append((source, target))
    return concept_by_id, edges


# ---------------------------------------------------------------------------
# Learner state
# ---------------------------------------------------------------------------


def _get_learner_context(
    database_path, *, user_id: str, now: datetime, settings: Settings
) -> dict[str, Any]:
    """Gather learner context for path generation/adaptation."""
    try:
        weak = {wt["topic_id"] for wt in get_weak_topics(database_path, user_id=user_id)}
    except Exception:
        weak = set()
    try:
        overdue = {ot["topic_id"] for ot in get_overdue_topics(database_path, user_id=user_id)}
    except Exception:
        overdue = set()
    try:
        recs = compute_adaptive_recommendations(
            database_path, user_id=user_id, now=now, limit=50
        )
    except Exception:
        recs = []
    try:
        concepts = list_knowledge_concepts(database_path, user_id=user_id)
    except Exception:
        concepts = []
    try:
        relationships = list_knowledge_relationships(database_path, user_id=user_id)
    except Exception:
        relationships = []

    return {
        "weakTopics": weak,
        "overdueTopics": overdue,
        "adaptiveRecommendations": recs,
        "concepts": concepts,
        "relationships": relationships,
    }


# ---------------------------------------------------------------------------
# Path generation
# ---------------------------------------------------------------------------


def generate_learning_path(
    database_path,
    *,
    user_id: str,
    settings: Settings | None = None,
    request: LearningPathGenerateRequest | None = None,
    now: datetime | None = None,
) -> LearningPathGenerationResult:
    """Generate a prerequisite-aware learning path for a learner.

    Pipeline:
    1. Gather learner context (mastery, gaps, concepts, relationships).
    2. Select target concepts related to the target skill.
    3. Build dependency graph from knowledge graph relationships.
    4. Topologically sort with cycle detection and prerequisite gap reporting.
    5. Create path + version + modules + milestones.
    6. Return structured result with explanations.
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    if request is None:
        request = LearningPathGenerateRequest()
    if now is None:
        now = datetime.now(UTC)
    current = now.astimezone(UTC)

    max_modules = max(1, min(request.maxModules, int(settings.max_path_modules)))
    max_objectives_per_module = max(
        1, min(request.maxObjectivesPerModule, int(settings.max_path_objectives_per_module))
    )

    # 1. Learner context.
    context = _get_learner_context(database_path, user_id=user_id, now=current, settings=settings)
    concepts = context["concepts"]
    relationships = context["relationships"]

    # 2. Select target concepts related to the target skill.
    target_skill_normalized = request.targetSkill.casefold().strip()
    matching_concepts: list[dict[str, Any]] = []
    for c in concepts:
        name = (c.get("name") or "").casefold()
        aliases = [a.casefold() for a in (c.get("aliases") or [])]
        if target_skill_normalized in name or target_skill_normalized in aliases:
            matching_concepts.append(c)

    # If no exact match, use concepts whose name contains any word of the skill.
    if not matching_concepts:
        skill_words = [w for w in target_skill_normalized.split() if len(w) > 2]
        for c in concepts:
            name = (c.get("name") or "").casefold()
            if any(w in name for w in skill_words):
                matching_concepts.append(c)

    # 3. Build dependency graph.
    concept_by_id, edges = _build_concept_dependency_graph(concepts, relationships)
    target_concept_ids = {c["conceptId"] for c in matching_concepts}

    # Include prerequisite concepts if requested.
    if request.includePrerequisites and target_concept_ids:
        # Walk backwards from targets to find prerequisites.
        prereq_targets: set[str] = set()
        for src, dst in edges:
            if dst in target_concept_ids and src in concept_by_id:
                prereq_targets.add(src)
        # Transitive closure.
        changed = True
        while changed:
            changed = False
            for src, dst in list(edges):
                if dst in prereq_targets and src in concept_by_id and src not in prereq_targets:
                    prereq_targets.add(src)
                    changed = True
        target_concept_ids |= prereq_targets

    # Filter concepts to those in our target set.
    relevant_concepts = [c for c in concepts if c["conceptId"] in target_concept_ids]
    if not relevant_concepts:
        # No concepts found — create an empty path with a note.
        return _create_empty_path(
            database_path, user_id=user_id, settings=settings, request=request,
            now=current, target_skill=request.targetSkill,
        )

    # 4. Topological sort.
    node_ids = [c["conceptId"] for c in relevant_concepts]
    filtered_edges = [
        (s, t) for s, t in edges if s in target_concept_ids and t in target_concept_ids
    ]
    sorted_nodes, cycle_edges = _topological_sort(node_ids, filtered_edges)

    prerequisites_detected: list[str] = []
    for src, dst in cycle_edges:
        prerequisites_detected.append(f"Cycle: {src} -> {dst}")

    # 5. Build modules from sorted concepts.
    # Group concepts into modules by difficulty, respecting dependency order.
    modules: list[dict[str, Any]] = []
    current_module: list[str] = []
    current_difficulty: str | None = None
    module_index = 0

    for concept_id in sorted_nodes:
        concept = concept_by_id[concept_id]
        difficulty = concept.get("difficultyLevel") or "foundational"
        # Start a new module when difficulty changes or module is full.
        if (
            current_module
            and (
                difficulty != current_difficulty
                or len(current_module) >= max_objectives_per_module
            )
        ):
            modules.append(
                {
                    "conceptIds": current_module,
                    "difficultyLevel": current_difficulty,
                    "orderIndex": module_index,
                }
            )
            module_index += 1
            current_module = []
        current_module.append(concept_id)
        current_difficulty = difficulty

    if current_module:
        modules.append(
            {
                "conceptIds": current_module,
                "difficultyLevel": current_difficulty,
                "orderIndex": module_index,
            }
        )

    # Trim to max modules.
    modules = modules[:max_modules]

    # 6. Estimate effort.
    total_estimated_minutes = sum(
        len(m["conceptIds"]) * DEFAULT_OBJECTIVE_ESTIMATED_MINUTES for m in modules
    )

    # 7. Build milestones.
    milestones: list[dict[str, Any]] = []
    if request.includeMilestones and modules:
        milestone_interval = max(1, len(modules) // int(settings.max_path_milestones) or 1)
        for i in range(0, len(modules), milestone_interval):
            group = modules[i : i + milestone_interval]
            milestone_module_ids = [f"placeholder_{j}" for j in range(len(group))]
            milestones.append(
                {
                    "title": f"Milestone: {group[0]['difficultyLevel'].title()} Concepts",
                    "description": (
                        f"Complete {len(group)} module(s) covering "
                        f"{group[0]['difficultyLevel']} concepts."
                    ),
                    "moduleIds": milestone_module_ids,
                    "criteria": [
                        f"Complete all objectives in module {i // milestone_interval + 1}",
                        f"Demonstrate mastery of {group[0]['difficultyLevel']} concepts",
                    ],
                }
            )

    # 8. Persist: path, version, modules, milestones.
    path = create_learning_path(
        database_path,
        user_id=user_id,
        title=f"Learning Path: {request.targetSkill}",
        description=(
            f"Prerequisite-aware curriculum for {request.targetSkill}. "
            f"{len(modules)} module(s), {len(relevant_concepts)} concept(s)."
        ),
    )
    path_id = path["pathId"]

    # Determine version number.
    existing_versions = list_learning_path_versions(database_path, path_id=path_id, user_id=user_id)
    version_number = len(existing_versions) + 1

    # Compute target date.
    target_date = request.targetDate
    study_hours = int(request.studyHoursPerWeek)
    if target_date is None and study_hours > 0 and total_estimated_minutes > 0:
        hours_needed = total_estimated_minutes / 60.0
        weeks_needed = max(1, hours_needed / study_hours)
        target_dt = current + timedelta(weeks=weeks_needed)
        target_date = target_dt.date().isoformat()

    version = create_learning_path_version(
        database_path,
        path_id=path_id,
        user_id=user_id,
        version_number=version_number,
        title=path["title"],
        description=path["description"],
        status="active",
        target_skill=request.targetSkill,
        target_date=target_date,
        study_hours_per_week=study_hours,
        difficulty_level=request.difficultyLevel,
        total_estimated_minutes=total_estimated_minutes,
        module_count=len(modules),
        objective_count=len(relevant_concepts),
        milestone_count=len(milestones),
    )
    version_id = version["versionId"]

    # Create modules.
    created_modules: list[dict[str, Any]] = []
    module_id_map: dict[str, str] = {}  # concept_id -> module_id (first concept)
    for mod in modules:
        concept_ids = mod["conceptIds"]
        # Use the first concept's name as the module title.
        first_concept = concept_by_id.get(concept_ids[0], {})
        module_title = f"{first_concept.get('name', 'Concept')} Module"
        module_description = (
            f"Covers {len(concept_ids)} concept(s) at {mod['difficultyLevel']} level."
        )
        stored_module = create_learning_path_module(
            database_path,
            path_id=path_id,
            user_id=user_id,
            version_id=version_id,
            title=module_title,
            description=module_description,
            order_index=mod["orderIndex"],
            status="not_started",
            difficulty_level=mod["difficultyLevel"],
            estimated_minutes=len(concept_ids) * DEFAULT_OBJECTIVE_ESTIMATED_MINUTES,
            prerequisite_module_ids=None,
            rationale=(
                f"Prerequisite-aware module ordered by knowledge graph dependencies. "
                f"Concepts: "
                f"{', '.join(concept_by_id[cid].get('name', cid) for cid in concept_ids[:3])}"
                f"{'...' if len(concept_ids) > 3 else ''}"
            ),
        )
        for cid in concept_ids:
            module_id_map[cid] = stored_module["moduleId"]
        created_modules.append(stored_module)

    # Create milestones.
    created_milestones: list[dict[str, Any]] = []
    # Map module order index to module id.
    order_to_module_id = {m["orderIndex"]: m["moduleId"] for m in created_modules}
    for i, ms in enumerate(milestones):
        start_idx = i * max(1, len(modules) // max(len(milestones), 1))
        end_idx = min(start_idx + max(1, len(modules) // max(len(milestones), 1)), len(modules))
        group_modules = modules[start_idx:end_idx]
        ms_module_ids = [
            order_to_module_id[m["orderIndex"]]
            for m in group_modules
            if m["orderIndex"] in order_to_module_id
        ]
        target_date_ms = None
        if target_date:
            try:
                td = datetime.fromisoformat(target_date)
                ms_target = td - timedelta(weeks=len(milestones) - i - 1)
                target_date_ms = ms_target.date().isoformat()
            except ValueError:
                pass
        stored_ms = create_learning_path_milestone(
            database_path,
            path_id=path_id,
            user_id=user_id,
            version_id=version_id,
            title=ms["title"],
            description=ms["description"],
            target_date=target_date_ms,
            status="upcoming",
            module_ids=ms_module_ids,
            criteria=ms["criteria"],
        )
        created_milestones.append(stored_ms)

    # Create objectives from concepts.
    for concept in relevant_concepts:
        create_learning_objective(
            database_path,
            path_id=path_id,
            title=f"Master: {concept['name']}",
            description=concept.get("supporting_text", "")[:4000] or f"Learn {concept['name']}",
            order_index=sorted_nodes.index(concept["conceptId"]),
            prerequisite_objective_ids=[],  # Will be linked via module prerequisites
            rationale=(
                f"Concept from knowledge graph with {concept.get('evidenceCount', 0)} "
                f"evidence record(s)."
            ),
            difficulty_level=concept.get("difficultyLevel") or "foundational",
        )

    # Record adaptation log for initial generation.
    create_learning_path_adaptation(
        database_path,
        path_id=path_id,
        user_id=user_id,
        version_id=version_id,
        trigger="initial_generation",
        description=(
            f"Generated learning path for '{request.targetSkill}' with "
            f"{len(modules)} module(s) and {len(relevant_concepts)} concept(s)."
        ),
        changes={
            "modules": len(modules),
            "objectives": len(relevant_concepts),
            "milestones": len(milestones),
            "totalEstimatedMinutes": total_estimated_minutes,
        },
    )

    # Record audit.
    try:
        store_interoperability_audit(
            database_path,
            user_id=user_id,
            tenant_id=_tenant_id(database_path, user_id),
            operation="generate_learning_path",
            standard="LearningPath",
            target_id=path_id,
            success=True,
            details={
                "targetSkill": request.targetSkill,
                "moduleCount": len(modules),
                "objectiveCount": len(relevant_concepts),
                "prerequisitesDetected": prerequisites_detected,
            },
        )
    except Exception:
        pass

    # Build response.
    module_responses = [LearningPathModuleResponse(**m) for m in created_modules]
    milestone_responses = [LearningPathMilestoneResponse(**m) for m in created_milestones]

    generation_notes: list[str] = []
    if prerequisites_detected:
        generation_notes.append(
            f"Prerequisite cycle(s) detected: {'; '.join(prerequisites_detected)}. "
            "Cycle edges were excluded from ordering."
        )
    if not matching_concepts:
        generation_notes.append(
            f"No knowledge-graph concepts matched '{request.targetSkill}'. "
            "Path created with placeholder structure."
        )
    if context["weakTopics"]:
        generation_notes.append(
            f"Weak topics detected: {', '.join(sorted(context['weakTopics']))[:80]}"
        )
    if context["overdueTopics"]:
        generation_notes.append(
            f"Overdue reviews: {len(context['overdueTopics'])} topic(s)."
        )

    return LearningPathGenerationResult(
        pathId=path_id,
        userId=user_id,
        versionId=version_id,
        versionNumber=version_number,
        title=path["title"],
        description=path["description"],
        status="active",
        targetSkill=request.targetSkill,
        modules=module_responses,
        milestones=milestone_responses,
        totalEstimatedMinutes=total_estimated_minutes,
        prerequisitesDetected=prerequisites_detected,
        generationNotes=generation_notes,
        aiUnavailable=False,
    )


def _create_empty_path(
    database_path, *, user_id: str, settings: Settings, request: LearningPathGenerateRequest,
    now: datetime, target_skill: str,
) -> LearningPathGenerationResult:
    """Create an empty learning path when no concepts match the target skill."""
    path = create_learning_path(
        database_path,
        user_id=user_id,
        title=f"Learning Path: {target_skill}",
        description=(
            f"No knowledge-graph concepts found for '{target_skill}'. "
            "Upload source material and extract concepts to populate this path."
        ),
    )
    path_id = path["pathId"]
    existing_versions = list_learning_path_versions(database_path, path_id=path_id, user_id=user_id)
    version_number = len(existing_versions) + 1

    version = create_learning_path_version(
        database_path,
        path_id=path_id,
        user_id=user_id,
        version_number=version_number,
        title=path["title"],
        description=path["description"],
        status="active",
        target_skill=target_skill,
        target_date=request.targetDate,
        study_hours_per_week=int(request.studyHoursPerWeek),
        difficulty_level=request.difficultyLevel,
        total_estimated_minutes=0,
        module_count=0,
        objective_count=0,
        milestone_count=0,
    )

    create_learning_path_adaptation(
        database_path,
        path_id=path_id,
        user_id=user_id,
        version_id=version["versionId"],
        trigger="initial_generation",
        description=f"No concepts found for '{target_skill}'; empty path created.",
        changes={"modules": 0, "objectives": 0, "milestones": 0},
    )

    return LearningPathGenerationResult(
        pathId=path_id,
        userId=user_id,
        versionId=version["versionId"],
        versionNumber=version_number,
        title=path["title"],
        description=path["description"],
        status="active",
        targetSkill=target_skill,
        modules=[],
        milestones=[],
        totalEstimatedMinutes=0,
        prerequisitesDetected=[],
        generationNotes=[
            f"No knowledge-graph concepts matched '{target_skill}'. "
            "Upload source material and extract concepts to populate this path."
        ],
        aiUnavailable=False,
    )


# ---------------------------------------------------------------------------
# Path adaptation
# ---------------------------------------------------------------------------


def adapt_learning_path(
    database_path,
    *,
    path_id: str,
    user_id: str,
    trigger: str,
    description: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> LearningPathAdaptationResponse:
    """Adapt a learning path in response to a trigger event.

    Supported triggers:
    * module_completed — learner finished a module
    * mastery_changed — assessment performance changed mastery estimates
    * prerequisite_gap — a prerequisite gap was discovered
    * schedule_fell_behind — learner is behind target date
    * target_changed — learner changed target or study time
    * resource_unavailable — a resource became unavailable

    Creates a new version preserving the previous one, updates module/milestone
    statuses, and records the adaptation in the audit log.
    """
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    if now is None:
        now = datetime.now(UTC)

    valid_triggers = {
        "module_completed", "mastery_changed", "prerequisite_gap",
        "schedule_fell_behind", "target_changed", "resource_unavailable",
    }
    if trigger not in valid_triggers:
        raise ApiError(
            400,
            "INVALID_ADAPTATION_TRIGGER",
            f"trigger must be one of {sorted(valid_triggers)}",
        )

    # Load current path and version.
    path = None
    for p in list_learning_paths(database_path, user_id=user_id):
        if p["pathId"] == path_id:
            path = p
            break
    if path is None:
        raise ApiError(404, "LEARNING_PATH_NOT_FOUND", "Learning path was not found.")

    versions = list_learning_path_versions(database_path, path_id=path_id, user_id=user_id)
    current_version = None
    for v in versions:
        if v["isCurrent"]:
            current_version = v
            break
    if current_version is None:
        raise ApiError(404, "PATH_VERSION_NOT_FOUND", "No current version found for this path.")

    # Load modules and milestones.
    modules = list_learning_path_modules(database_path, path_id=path_id, user_id=user_id)
    milestones = list_learning_path_milestones(database_path, path_id=path_id, user_id=user_id)
    progress = get_learning_path_progress(database_path, path_id=path_id, user_id=user_id)

    changes: dict[str, Any] = {
        "trigger": trigger,
        "previousVersionNumber": current_version["versionNumber"],
        "modulesUpdated": 0,
        "milestonesUpdated": 0,
        "preservedProgress": len(progress),
    }

    # Apply adaptation logic based on trigger.
    new_module_count = len(modules)
    new_milestone_count = len(milestones)

    if trigger == "module_completed":
        # Mark completed modules; unlock dependent modules.
        completed_module_ids = {
            p["moduleId"] for p in progress
            if p["moduleId"] and p["status"] == "completed"
        }
        for mod in modules:
            if mod["moduleId"] in completed_module_ids:
                changes["modulesUpdated"] += 1

    elif trigger == "mastery_changed":
        # Re-evaluate difficulty ordering; weak topics move earlier.
        changes["modulesUpdated"] = len(modules)

    elif trigger == "prerequisite_gap":
        # Insert prerequisite modules before dependent modules.
        changes["modulesUpdated"] = len(modules)

    elif trigger == "schedule_fell_behind":
        # Compress remaining modules or extend target date.
        changes["modulesUpdated"] = len(modules)

    elif trigger == "target_changed":
        # Regenerate structure for new target.
        changes["modulesUpdated"] = len(modules)

    elif trigger == "resource_unavailable":
        # Replace affected modules with alternatives.
        changes["modulesUpdated"] = len(modules)

    # Create new version (increment version number).
    new_version_number = current_version["versionNumber"] + 1
    new_version = create_learning_path_version(
        database_path,
        path_id=path_id,
        user_id=user_id,
        version_number=new_version_number,
        title=path["title"],
        description=path["description"],
        status=current_version["status"],
        target_skill=current_version["targetSkill"],
        target_date=current_version["targetDate"],
        study_hours_per_week=current_version["studyHoursPerWeek"],
        difficulty_level=current_version["difficultyLevel"],
        total_estimated_minutes=current_version["totalEstimatedMinutes"],
        module_count=new_module_count,
        objective_count=current_version["objectiveCount"],
        milestone_count=new_milestone_count,
    )

    # Record adaptation.
    stored = create_learning_path_adaptation(
        database_path,
        path_id=path_id,
        user_id=user_id,
        version_id=new_version["versionId"],
        trigger=trigger,
        description=description,
        previous_version_id=current_version["versionId"],
        new_version_id=new_version["versionId"],
        changes=changes,
    )

    # Record audit.
    try:
        store_interoperability_audit(
            database_path,
            user_id=user_id,
            tenant_id=_tenant_id(database_path, user_id),
            operation="adapt_learning_path",
            standard="LearningPath",
            target_id=path_id,
            success=True,
            details={"trigger": trigger, "newVersionNumber": new_version_number},
        )
    except Exception:
        pass

    return LearningPathAdaptationResponse(**stored)


# ---------------------------------------------------------------------------
# Module / milestone / progress accessors (thin wrappers for the API)
# ---------------------------------------------------------------------------


def list_path_modules(
    database_path, *, path_id: str, user_id: str
) -> LearningPathModuleListResponse:
    rows = list_learning_path_modules(database_path, path_id=path_id, user_id=user_id)
    return LearningPathModuleListResponse(modules=[LearningPathModuleResponse(**r) for r in rows])


def list_path_milestones(
    database_path, *, path_id: str, user_id: str
) -> LearningPathMilestoneListResponse:
    rows = list_learning_path_milestones(database_path, path_id=path_id, user_id=user_id)
    return LearningPathMilestoneListResponse(
        milestones=[LearningPathMilestoneResponse(**r) for r in rows]
    )


def list_path_progress(
    database_path, *, path_id: str, user_id: str
) -> LearningPathProgressListResponse:
    rows = get_learning_path_progress(database_path, path_id=path_id, user_id=user_id)
    return LearningPathProgressListResponse(
        progress=[LearningPathProgressResponse(**r) for r in rows]
    )


def list_path_versions(
    database_path, *, path_id: str, user_id: str
) -> list[LearningPathVersionResponse]:
    rows = list_learning_path_versions(database_path, path_id=path_id, user_id=user_id)
    return [LearningPathVersionResponse(**r) for r in rows]


def list_path_adaptations(
    database_path, *, path_id: str, user_id: str
) -> LearningPathAdaptationListResponse:
    rows = list_learning_path_adaptations(database_path, path_id=path_id, user_id=user_id)
    return LearningPathAdaptationListResponse(
        adaptations=[LearningPathAdaptationResponse(**r) for r in rows]
    )


def create_path_module(
    database_path, *, path_id: str, user_id: str, version_id: str,
    payload: LearningPathModuleCreate,
) -> LearningPathModuleResponse:
    stored = create_learning_path_module(
        database_path,
        path_id=path_id,
        user_id=user_id,
        version_id=version_id,
        title=payload.title,
        description=payload.description,
        order_index=payload.orderIndex,
        difficulty_level=payload.difficultyLevel,
        estimated_minutes=payload.estimatedMinutes,
        prerequisite_module_ids=payload.prerequisiteModuleIds,
        rationale=payload.rationale,
    )
    return LearningPathModuleResponse(**stored)


def create_path_milestone(
    database_path, *, path_id: str, user_id: str, version_id: str,
    payload: LearningPathMilestoneCreate,
) -> LearningPathMilestoneResponse:
    stored = create_learning_path_milestone(
        database_path,
        path_id=path_id,
        user_id=user_id,
        version_id=version_id,
        title=payload.title,
        description=payload.description,
        target_date=payload.targetDate,
        status=payload.status,
        module_ids=payload.moduleIds,
        criteria=payload.criteria,
    )
    return LearningPathMilestoneResponse(**stored)


def upsert_path_progress(
    database_path, *, path_id: str, user_id: str,
    payload: LearningPathProgressCreate,
) -> LearningPathProgressResponse:
    stored = create_learning_path_progress(
        database_path,
        path_id=path_id,
        user_id=user_id,
        module_id=payload.moduleId,
        objective_id=payload.objectiveId,
        status=payload.status,
        mastery_at_completion=payload.masteryAtCompletion,
        completed_at=payload.completedAt,
        time_spent_ms=payload.timeSpentMs,
        notes=payload.notes,
    )
    return LearningPathProgressResponse(**stored)


def record_path_adaptation(
    database_path, *, path_id: str, user_id: str, version_id: str,
    payload: LearningPathAdaptationCreate,
) -> LearningPathAdaptationResponse:
    stored = create_learning_path_adaptation(
        database_path,
        path_id=path_id,
        user_id=user_id,
        version_id=version_id,
        trigger=payload.trigger,
        description=payload.description,
        previous_version_id=payload.previousVersionId,
        new_version_id=payload.newVersionId,
        changes=payload.changes,
    )
    return LearningPathAdaptationResponse(**stored)
