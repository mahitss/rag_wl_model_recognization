"""SQLite schema, readiness checks, and transactional document persistence."""
# ruff: noqa: E501

import hashlib
import json
import math
import secrets
import sqlite3
import struct
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.learning_analytics import _parse_datetime, build_analytics

SCHEMA_VERSION = 17


def _user_scoped_id(prefix: str, identifier: str, user_id: str | None) -> str:
    if user_id is None:
        return identifier
    digest = hashlib.sha256(f"{user_id}\x1f{identifier}".encode()).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _database_uri(database_path: Path, *, mode: str = "rw") -> str:
    return f"{database_path.resolve().as_uri()}?mode={mode}"


def connect_database(database_path: Path) -> sqlite3.Connection:
    """Open the existing database without creating it and enable referential integrity.

    WAL mode allows concurrent readers and a single writer without blocking,
    and `normal` synchronous mode trades a small durability window for much
    faster writes.  Both are safe for the single-file SQLite deployment model.
    """
    connection = sqlite3.connect(_database_uri(database_path), uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


# ---------------------------------------------------------------------------
# User management (M23: authentication & user isolation)
# ---------------------------------------------------------------------------


def _ensure_column(connection, table_name: str, column_name: str, column_definition: str) -> None:
    """Add a column to a table only if it doesn't already exist."""
    existing = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not any(row[1] == column_name for row in existing):
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")


def create_user(
    database_path: Path,
    username: str,
    password_hash: str,
    email: str | None = None,
    *,
    is_admin: bool = False,
) -> str:
    """Create a new user and return the user_id."""

    user_id = f"user_{secrets.token_urlsafe(16)}"
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        with connection:
            connection.execute(
                """INSERT INTO users(
                       user_id, username, email, password_hash, is_admin, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    username,
                    email or f"{user_id}@users.wonderlearn.invalid",
                    password_hash,
                    int(is_admin),
                    datetime.now(UTC).isoformat(),
                ),
            )
    return user_id


def get_user_by_id(database_path: Path, user_id: str) -> dict[str, Any] | None:
    """Return a user record by user_id, or None if not found."""
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            "SELECT user_id, username, email, password_hash, is_admin, created_at "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "email": row["email"],
        "password_hash": row["password_hash"],
        "is_admin": bool(row["is_admin"]),
        "created_at": row["created_at"],
    }


def get_user_by_username(database_path: Path, username: str) -> dict[str, Any] | None:
    """Return a user record by username, or None if not found."""
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            "SELECT user_id, username, email, password_hash, is_admin, created_at "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if row is None:
        return None
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "email": row["email"],
        "password_hash": row["password_hash"],
        "is_admin": bool(row["is_admin"]),
        "created_at": row["created_at"],
    }


def mark_user_admin(database_path: Path, user_id: str, *, is_admin: bool) -> None:
    """Persist the account's admin flag without trusting client-supplied role state."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                "UPDATE users SET is_admin = ? WHERE user_id = ?",
                (int(is_admin), user_id),
            )


def create_feedback_item(
    database_path: Path,
    *,
    user_id: str,
    category: str,
    title: str,
    description: str,
    status: str = "new",
) -> dict[str, Any]:
    """Create a feedback item and return its stored record."""
    feedback_id = f"feedback_{secrets.token_urlsafe(12)}"
    created_at = datetime.now(UTC).isoformat()
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO feedback_items(
                    feedback_id, user_id, category, title, description, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    user_id,
                    category,
                    title,
                    description,
                    status,
                    created_at,
                    created_at,
                ),
            )
    return get_feedback_item(database_path, feedback_id) or {
        "feedback_id": feedback_id,
        "user_id": user_id,
        "category": category,
        "title": title,
        "description": description,
        "status": status,
        "created_at": created_at,
        "updated_at": created_at,
        "username": None,
    }


def get_feedback_item(database_path: Path, feedback_id: str) -> dict[str, Any] | None:
    """Return one feedback item, including the submitter's username if available."""
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            """
            SELECT f.feedback_id, f.user_id, f.category, f.title, f.description,
                   f.status, f.resolution_notes, f.improvement_task_id,
                   f.created_at, f.updated_at, u.username
            FROM feedback_items AS f
            LEFT JOIN users AS u ON u.user_id = f.user_id
            WHERE f.feedback_id = ?
            """,
            (feedback_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "feedback_id": row["feedback_id"],
        "user_id": row["user_id"],
        "category": row["category"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "resolution_notes": row["resolution_notes"],
        "improvement_task_id": row["improvement_task_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "username": row["username"],
    }


def list_feedback_for_user(database_path: Path, user_id: str) -> list[dict[str, Any]]:
    """Return all feedback submitted by a user ordered by the newest update first."""
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT f.feedback_id, f.user_id, f.category, f.title, f.description,
                   f.status, f.resolution_notes, f.improvement_task_id,
                   f.created_at, f.updated_at, u.username
            FROM feedback_items AS f
            LEFT JOIN users AS u ON u.user_id = f.user_id
            WHERE f.user_id = ?
            ORDER BY f.updated_at DESC, f.created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "feedback_id": row["feedback_id"],
            "user_id": row["user_id"],
            "category": row["category"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "resolution_notes": row["resolution_notes"],
            "improvement_task_id": row["improvement_task_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "username": row["username"],
        }
        for row in rows
    ]


def update_feedback_item(
    database_path: Path,
    *,
    feedback_id: str,
    user_id: str | None = None,
    category: str | None = None,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Update a feedback item, enforcing ownership unless the caller is an admin."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            existing = connection.execute(
                "SELECT feedback_id, user_id, category, title, description, status FROM feedback_items WHERE feedback_id = ?",
                (feedback_id,),
            ).fetchone()
            if existing is None:
                return None
            if user_id is not None and existing["user_id"] != user_id:
                raise PermissionError("You do not own this feedback item.")

            values: list[Any] = []
            updates: list[str] = []
            if category is not None:
                updates.append("category = ?")
                values.append(category)
            if title is not None:
                updates.append("title = ?")
                values.append(title)
            if description is not None:
                updates.append("description = ?")
                values.append(description)
            if status is not None:
                updates.append("status = ?")
                values.append(status)

            if not updates:
                return get_feedback_item(database_path, feedback_id)

            updates.append("updated_at = ?")
            values.extend([datetime.now(UTC).isoformat()])
            values.extend([feedback_id])
            connection.execute(
                f"UPDATE feedback_items SET {', '.join(updates)} WHERE feedback_id = ?",
                tuple(values),
            )

    return get_feedback_item(database_path, feedback_id)


def _compute_priority_score(
    *,
    severity: int,
    impact: int,
    report_count: int,
    reproducibility: int,
    workflow_frequency: int,
    implementation_effort: int,
) -> float:
    """Compute a transparent priority score for an improvement task.

    The score blends measured data with administrator estimates.  The only
    measured value is ``report_count`` (the number of distinct feedback items
    linked to the task).  All other factors are administrator estimates on a
    1–5 scale and are never treated as observed facts.

    Weights:
        severity x20, impact x15, report_count x10 (measured),
        reproducibility x10, workflow_frequency x10,
        implementation_effort x5 (higher effort lowers the score).

    Result range is approximately 0 to 150.  Higher means higher priority.
    """
    return (
        severity * 20
        + impact * 15
        + report_count * 10
        + reproducibility * 10
        + workflow_frequency * 10
        - implementation_effort * 5
    )


def create_improvement_task(
    database_path: Path,
    *,
    user_id: str,
    title: str,
    description: str,
    status: str = "proposed",
    severity: int = 3,
    impact: int = 3,
    reproducibility: int = 3,
    workflow_frequency: int = 3,
    implementation_effort: int = 3,
    regression_test: str | None = None,
    feedback_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Create an improvement task and optionally link feedback items to it.

    Duplicate titles (case-insensitive) are rejected to prevent accidental
    duplicate tasks.  ``feedback_ids`` are stored in the junction table and the
    measured ``report_count`` is derived from them.
    """
    task_id = f"task_{secrets.token_urlsafe(12)}"
    now = datetime.now(UTC).isoformat()
    title = title.strip()
    description = description.strip()
    feedback_ids = feedback_ids or []

    with closing(connect_database(database_path)) as connection:
        with connection:
            existing = connection.execute(
                "SELECT 1 FROM improvement_tasks WHERE lower(title) = lower(?) LIMIT 1",
                (title,),
            ).fetchone()
            if existing is not None:
                raise ValueError("An improvement task with this title already exists.")

            report_count = len(feedback_ids)
            priority_score = _compute_priority_score(
                severity=severity,
                impact=impact,
                report_count=report_count,
                reproducibility=reproducibility,
                workflow_frequency=workflow_frequency,
                implementation_effort=implementation_effort,
            )
            factors = {
                "severity": severity,
                "impact": impact,
                "report_count": report_count,
                "reproducibility": reproducibility,
                "workflow_frequency": workflow_frequency,
                "implementation_effort": implementation_effort,
                "measured": ["report_count"],
                "estimated": [
                    "severity",
                    "impact",
                    "reproducibility",
                    "workflow_frequency",
                    "implementation_effort",
                ],
            }
            connection.execute(
                """
                INSERT INTO improvement_tasks(
                    task_id, title, description, status, priority_score,
                    priority_factors_json, severity, impact, report_count,
                    reproducibility, workflow_frequency, implementation_effort,
                    regression_test, implemented, implemented_at, user_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    title,
                    description,
                    status,
                    round(priority_score, 2),
                    json.dumps(factors, separators=(",", ":"), sort_keys=True),
                    severity,
                    impact,
                    report_count,
                    reproducibility,
                    workflow_frequency,
                    implementation_effort,
                    regression_test,
                    0,
                    None,
                    user_id,
                    now,
                    now,
                ),
            )
            for feedback_id in feedback_ids:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO improvement_task_feedback(
                        improvement_task_id, feedback_id
                    ) VALUES (?, ?)
                    """,
                    (task_id, feedback_id),
                )
                connection.execute(
                    "UPDATE feedback_items SET improvement_task_id = ? WHERE feedback_id = ?",
                    (task_id, feedback_id),
                )

    return get_improvement_task(database_path, task_id)


def get_improvement_task(database_path: Path, task_id: str) -> dict[str, Any] | None:
    """Return one improvement task with its linked feedback IDs."""
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            """
            SELECT t.task_id, t.title, t.description, t.status, t.priority_score,
                   t.priority_factors_json, t.severity, t.impact, t.report_count,
                   t.reproducibility, t.workflow_frequency, t.implementation_effort,
                   t.regression_test, t.implemented, t.implemented_at, t.user_id,
                   t.created_at, t.updated_at, u.username
            FROM improvement_tasks AS t
            LEFT JOIN users AS u ON u.user_id = t.user_id
            WHERE t.task_id = ?
            """,
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        linked = connection.execute(
            "SELECT feedback_id FROM improvement_task_feedback WHERE improvement_task_id = ? ORDER BY feedback_id",
            (task_id,),
        ).fetchall()
    return {
        "task_id": row["task_id"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "priority_score": row["priority_score"],
        "priority_factors": json.loads(row["priority_factors_json"] or "{}"),
        "severity": row["severity"],
        "impact": row["impact"],
        "report_count": row["report_count"],
        "reproducibility": row["reproducibility"],
        "workflow_frequency": row["workflow_frequency"],
        "implementation_effort": row["implementation_effort"],
        "regression_test": row["regression_test"],
        "implemented": bool(row["implemented"]),
        "implemented_at": row["implemented_at"],
        "user_id": row["user_id"],
        "username": row["username"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "feedback_ids": [r["feedback_id"] for r in linked],
    }


def list_improvement_tasks(
    database_path: Path,
    *,
    status: str | None = None,
    user_id: str | None = None,
    sort_by: str = "priority",
    sort_order: str = "desc",
) -> list[dict[str, Any]]:
    """List improvement tasks with optional status/user filters and sorting."""
    allowed_sort = {"priority": "t.priority_score", "recency": "t.created_at"}
    sort_column = allowed_sort.get(sort_by, "t.priority_score")
    order = "ASC" if sort_order == "asc" else "DESC"

    query = """
        SELECT t.task_id, t.title, t.description, t.status, t.priority_score,
               t.priority_factors_json, t.severity, t.impact, t.report_count,
               t.reproducibility, t.workflow_frequency, t.implementation_effort,
               t.regression_test, t.implemented, t.implemented_at, t.user_id,
               t.created_at, t.updated_at, u.username
        FROM improvement_tasks AS t
        LEFT JOIN users AS u ON u.user_id = t.user_id
        WHERE 1 = 1
    """
    params: list[Any] = []
    if status is not None:
        query += " AND t.status = ?"
        params.append(status)
    if user_id is not None:
        query += " AND t.user_id = ?"
        params.append(user_id)
    query += f" ORDER BY {sort_column} {order}"

    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(query, params).fetchall()
        # Fetch all linked feedback IDs in one query while the connection is open.
        task_ids = [row["task_id"] for row in rows]
        linked_map: dict[str, list[str]] = {}
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            linked_rows = connection.execute(
                f"SELECT improvement_task_id, feedback_id FROM improvement_task_feedback "
                f"WHERE improvement_task_id IN ({placeholders}) "
                f"ORDER BY improvement_task_id, feedback_id",
                task_ids,
            ).fetchall()
            for lr in linked_rows:
                linked_map.setdefault(lr["improvement_task_id"], []).append(lr["feedback_id"])
    results = []
    for row in rows:
        results.append(
            {
                "task_id": row["task_id"],
                "title": row["title"],
                "description": row["description"],
                "status": row["status"],
                "priority_score": row["priority_score"],
                "priority_factors": json.loads(row["priority_factors_json"] or "{}"),
                "severity": row["severity"],
                "impact": row["impact"],
                "report_count": row["report_count"],
                "reproducibility": row["reproducibility"],
                "workflow_frequency": row["workflow_frequency"],
                "implementation_effort": row["implementation_effort"],
                "regression_test": row["regression_test"],
                "implemented": bool(row["implemented"]),
                "implemented_at": row["implemented_at"],
                "user_id": row["user_id"],
                "username": row["username"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "feedback_ids": linked_map.get(row["task_id"], []),
            }
        )
    return results


def update_improvement_task(
    database_path: Path,
    *,
    task_id: str,
    user_id: str | None = None,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    severity: int | None = None,
    impact: int | None = None,
    reproducibility: int | None = None,
    workflow_frequency: int | None = None,
    implementation_effort: int | None = None,
    regression_test: str | None = None,
    implemented: bool | None = None,
) -> dict[str, Any] | None:
    """Update an improvement task, recomputing priority when factors change."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            existing = connection.execute(
                "SELECT * FROM improvement_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if existing is None:
                return None
            if user_id is not None and existing["user_id"] != user_id:
                raise PermissionError("You do not own this improvement task.")

            updates: list[str] = []
            values: list[Any] = []
            if title is not None:
                updates.append("title = ?")
                values.append(title.strip())
            if description is not None:
                updates.append("description = ?")
                values.append(description.strip())
            if status is not None:
                updates.append("status = ?")
                values.append(status)
            if severity is not None:
                updates.append("severity = ?")
                values.append(severity)
            if impact is not None:
                updates.append("impact = ?")
                values.append(impact)
            if reproducibility is not None:
                updates.append("reproducibility = ?")
                values.append(reproducibility)
            if workflow_frequency is not None:
                updates.append("workflow_frequency = ?")
                values.append(workflow_frequency)
            if implementation_effort is not None:
                updates.append("implementation_effort = ?")
                values.append(implementation_effort)
            if regression_test is not None:
                updates.append("regression_test = ?")
                values.append(regression_test.strip() if regression_test else None)
            if implemented is not None:
                updates.append("implemented = ?")
                values.append(1 if implemented else 0)
                if implemented:
                    updates.append("implemented_at = ?")
                    values.append(datetime.now(UTC).isoformat())
                else:
                    updates.append("implemented_at = ?")
                    values.append(None)

            # Recompute priority score if any factor changed.
            factor_changed = any(
                f is not None
                for f in (
                    severity,
                    impact,
                    reproducibility,
                    workflow_frequency,
                    implementation_effort,
                )
            )
            if factor_changed:
                new_severity = severity if severity is not None else existing["severity"]
                new_impact = impact if impact is not None else existing["impact"]
                new_reproducibility = (
                    reproducibility if reproducibility is not None else existing["reproducibility"]
                )
                new_workflow_frequency = (
                    workflow_frequency
                    if workflow_frequency is not None
                    else existing["workflow_frequency"]
                )
                new_effort = (
                    implementation_effort
                    if implementation_effort is not None
                    else existing["implementation_effort"]
                )
                new_score = _compute_priority_score(
                    severity=new_severity,
                    impact=new_impact,
                    report_count=existing["report_count"],
                    reproducibility=new_reproducibility,
                    workflow_frequency=new_workflow_frequency,
                    implementation_effort=new_effort,
                )
                updates.append("priority_score = ?")
                values.append(round(new_score, 2))
                factors = {
                    "severity": new_severity,
                    "impact": new_impact,
                    "report_count": existing["report_count"],
                    "reproducibility": new_reproducibility,
                    "workflow_frequency": new_workflow_frequency,
                    "implementation_effort": new_effort,
                    "measured": ["report_count"],
                    "estimated": [
                        "severity",
                        "impact",
                        "reproducibility",
                        "workflow_frequency",
                        "implementation_effort",
                    ],
                }
                updates.append("priority_factors_json = ?")
                values.append(json.dumps(factors, separators=(",", ":"), sort_keys=True))

            if not updates:
                return get_improvement_task(database_path, task_id)

            updates.append("updated_at = ?")
            values.append(datetime.now(UTC).isoformat())
            values.append(task_id)
            connection.execute(
                f"UPDATE improvement_tasks SET {', '.join(updates)} WHERE task_id = ?",
                tuple(values),
            )

    return get_improvement_task(database_path, task_id)


def link_feedback_to_task(
    database_path: Path, *, task_id: str, feedback_id: str
) -> dict[str, Any] | None:
    """Link a feedback item to an improvement task and recompute report_count."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            task = connection.execute(
                "SELECT * FROM improvement_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if task is None:
                return None
            feedback = connection.execute(
                "SELECT * FROM feedback_items WHERE feedback_id = ?", (feedback_id,)
            ).fetchone()
            if feedback is None:
                return None
            connection.execute(
                """
                INSERT OR IGNORE INTO improvement_task_feedback(
                    improvement_task_id, feedback_id
                ) VALUES (?, ?)
                """,
                (task_id, feedback_id),
            )
            connection.execute(
                "UPDATE feedback_items SET improvement_task_id = ? WHERE feedback_id = ?",
                (task_id, feedback_id),
            )
            row = connection.execute(
                "SELECT COUNT(*) AS c FROM improvement_task_feedback WHERE improvement_task_id = ?",
                (task_id,),
            ).fetchone()
            new_count = int(row["c"])
            new_score = _compute_priority_score(
                severity=task["severity"],
                impact=task["impact"],
                report_count=new_count,
                reproducibility=task["reproducibility"],
                workflow_frequency=task["workflow_frequency"],
                implementation_effort=task["implementation_effort"],
            )
            factors = {
                "severity": task["severity"],
                "impact": task["impact"],
                "report_count": new_count,
                "reproducibility": task["reproducibility"],
                "workflow_frequency": task["workflow_frequency"],
                "implementation_effort": task["implementation_effort"],
                "measured": ["report_count"],
                "estimated": [
                    "severity",
                    "impact",
                    "reproducibility",
                    "workflow_frequency",
                    "implementation_effort",
                ],
            }
            connection.execute(
                "UPDATE improvement_tasks SET report_count = ?, priority_score = ?, priority_factors_json = ?, updated_at = ? WHERE task_id = ?",
                (
                    new_count,
                    round(new_score, 2),
                    json.dumps(factors, separators=(",", ":"), sort_keys=True),
                    datetime.now(UTC).isoformat(),
                    task_id,
                ),
            )

    return get_improvement_task(database_path, task_id)


def unlink_feedback_from_task(
    database_path: Path, *, task_id: str, feedback_id: str
) -> dict[str, Any] | None:
    """Unlink a feedback item from an improvement task and recompute report_count."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            task = connection.execute(
                "SELECT * FROM improvement_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if task is None:
                return None
            connection.execute(
                "DELETE FROM improvement_task_feedback WHERE improvement_task_id = ? AND feedback_id = ?",
                (task_id, feedback_id),
            )
            connection.execute(
                "UPDATE feedback_items SET improvement_task_id = NULL WHERE feedback_id = ? AND improvement_task_id = ?",
                (feedback_id, task_id),
            )
            row = connection.execute(
                "SELECT COUNT(*) AS c FROM improvement_task_feedback WHERE improvement_task_id = ?",
                (task_id,),
            ).fetchone()
            new_count = int(row["c"])
            new_score = _compute_priority_score(
                severity=task["severity"],
                impact=task["impact"],
                report_count=new_count,
                reproducibility=task["reproducibility"],
                workflow_frequency=task["workflow_frequency"],
                implementation_effort=task["implementation_effort"],
            )
            factors = {
                "severity": task["severity"],
                "impact": task["impact"],
                "report_count": new_count,
                "reproducibility": task["reproducibility"],
                "workflow_frequency": task["workflow_frequency"],
                "implementation_effort": task["implementation_effort"],
                "measured": ["report_count"],
                "estimated": [
                    "severity",
                    "impact",
                    "reproducibility",
                    "workflow_frequency",
                    "implementation_effort",
                ],
            }
            connection.execute(
                "UPDATE improvement_tasks SET report_count = ?, priority_score = ?, priority_factors_json = ?, updated_at = ? WHERE task_id = ?",
                (
                    new_count,
                    round(new_score, 2),
                    json.dumps(factors, separators=(",", ":"), sort_keys=True),
                    datetime.now(UTC).isoformat(),
                    task_id,
                ),
            )

    return get_improvement_task(database_path, task_id)


def list_feedback_for_admin(
    database_path: Path,
    *,
    category: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort_by: str = "recency",
    sort_order: str = "desc",
) -> list[dict[str, Any]]:
    """List all feedback for administrator review with filtering and sorting.

    Measured counts are derived from stored rows only.  No administrator
    estimates are included in this list view.
    """
    allowed_sort = {"recency": "f.updated_at", "created": "f.created_at"}
    sort_column = allowed_sort.get(sort_by, "f.updated_at")
    order = "ASC" if sort_order == "asc" else "DESC"

    query = """
        SELECT f.feedback_id, f.user_id, f.category, f.title, f.description,
               f.status, f.resolution_notes, f.improvement_task_id,
               f.created_at, f.updated_at, u.username
        FROM feedback_items AS f
        LEFT JOIN users AS u ON u.user_id = f.user_id
        WHERE 1 = 1
    """
    params: list[Any] = []
    if category is not None:
        query += " AND f.category = ?"
        params.append(category)
    if status is not None:
        query += " AND f.status = ?"
        params.append(status)
    if date_from is not None:
        query += " AND f.created_at >= ?"
        params.append(date_from)
    if date_to is not None:
        query += " AND f.created_at <= ?"
        params.append(date_to)
    query += f" ORDER BY {sort_column} {order}"

    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(query, params).fetchall()
    return [
        {
            "feedback_id": row["feedback_id"],
            "user_id": row["user_id"],
            "category": row["category"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "resolution_notes": row["resolution_notes"],
            "improvement_task_id": row["improvement_task_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "username": row["username"],
        }
        for row in rows
    ]


def update_feedback_item_admin(
    database_path: Path,
    *,
    feedback_id: str,
    category: str | None = None,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    resolution_notes: str | None = None,
    improvement_task_id: str | None = None,
) -> dict[str, Any] | None:
    """Update a feedback item as an administrator (no ownership check).

    Only administrators may call this.  Resolution notes and improvement task
    links are admin-only fields.
    """
    with closing(connect_database(database_path)) as connection:
        with connection:
            existing = connection.execute(
                "SELECT feedback_id, user_id FROM feedback_items WHERE feedback_id = ?",
                (feedback_id,),
            ).fetchone()
            if existing is None:
                return None

            updates: list[str] = []
            values: list[Any] = []
            if category is not None:
                updates.append("category = ?")
                values.append(category)
            if title is not None:
                updates.append("title = ?")
                values.append(title.strip())
            if description is not None:
                updates.append("description = ?")
                values.append(description.strip())
            if status is not None:
                updates.append("status = ?")
                values.append(status)
            if resolution_notes is not None:
                updates.append("resolution_notes = ?")
                values.append(resolution_notes.strip() if resolution_notes else None)
            if improvement_task_id is not None:
                updates.append("improvement_task_id = ?")
                values.append(improvement_task_id)

            if not updates:
                return get_feedback_item(database_path, feedback_id)

            updates.append("updated_at = ?")
            values.append(datetime.now(UTC).isoformat())
            values.append(feedback_id)
            connection.execute(
                f"UPDATE feedback_items SET {', '.join(updates)} WHERE feedback_id = ?",
                tuple(values),
            )

    return get_feedback_item(database_path, feedback_id)


def get_user_by_session(database_path: Path, session_token: str) -> dict[str, Any] | None:
    """Return the user associated with a valid session token, or None.

    Expired sessions are deleted lazily.
    """
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        row = connection.execute(
            """SELECT u.user_id, u.username, u.email, u.password_hash, u.is_admin, u.created_at
               FROM users u
               JOIN user_sessions s ON s.user_id = u.user_id
               WHERE s.session_id = ?""",
            (session_token,),
        ).fetchone()
        if row is None:
            return None
        expires_at = connection.execute(
            "SELECT expires_at FROM user_sessions WHERE session_id = ?",
            (session_token,),
        ).fetchone()
        if expires_at is None:
            return None
        expires_at_dt = datetime.fromisoformat(expires_at["expires_at"])
        if expires_at_dt.tzinfo is None:
            expires_at_dt = expires_at_dt.replace(tzinfo=UTC)
        if expires_at_dt <= datetime.now(UTC):
            connection.execute(
                "DELETE FROM user_sessions WHERE session_id = ?",
                (session_token,),
            )
            connection.commit()
            return None
    return dict(row) if row else None


def destroy_session(database_path: Path, session_token: str) -> None:
    """Remove a session from the store (no-op if absent)."""
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        with connection:
            connection.execute("DELETE FROM user_sessions WHERE session_id = ?", (session_token,))


def cleanup_expired_sessions(database_path: Path) -> int:
    """Delete all expired sessions and return the number removed."""
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        with connection:
            cursor = connection.execute(
                "DELETE FROM user_sessions WHERE expires_at <= ?",
                (datetime.now(UTC).isoformat(),),
            )
            return cursor.rowcount


def _sanitize_product_analytics_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only bounded numeric operational metadata; discard all free-form values."""
    if not isinstance(metadata, dict):
        return {}
    allowed = {"byte_size", "count", "duration_ms"}
    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        if (
            key in allowed
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and 0 <= value <= 1_000_000_000
        ):
            sanitized[key] = value
    return sanitized


def record_product_event(
    database_path: Path,
    *,
    user_id: str | None,
    event_name: str,
    feature: str,
    result: str,
    category: str = "feature",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist a minimal, privacy-safe event for user-specific adoption metrics."""
    supported_events = {
        "document_upload_completed": ("documents", "success"),
        "document_upload_failed": ("documents", "failure"),
        "image_upload_completed": ("images", "success"),
        "image_upload_failed": ("images", "failure"),
        "tutor_request_completed": ("tutor", "success"),
        "tutor_request_abstained": ("tutor", "abstained"),
        "tutor_request_failed": ("tutor", "failure"),
        "quiz_started": ("quizzes", "started"),
        "quiz_completed": ("quizzes", "completed"),
        "quiz_failed": ("quizzes", "failure"),
        "study_session_completed": ("study_sessions", "completed"),
        "study_session_failed": ("study_sessions", "failure"),
        "study_coach_recommendation_accepted": ("study_coach", "accepted"),
        "study_coach_recommendation_dismissed": ("study_coach", "dismissed"),
    }
    if supported_events.get(event_name) != (feature, result):
        raise ValueError("Unsupported product analytics event")
    if category not in {"feature", "workflow", "error", "recovery"}:
        raise ValueError("Unsupported analytics category")
    payload = _sanitize_product_analytics_metadata(metadata)
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO product_events(
                    event_id, user_id, event_name, feature, result, category, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"event_{secrets.token_urlsafe(16)}",
                    user_id,
                    event_name,
                    feature,
                    result,
                    category,
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )


def prune_product_events(database_path: Path, *, retention_days: int) -> int:
    """Delete expired product analytics rows while preserving the user-owned structure."""
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        with connection:
            cursor = connection.execute(
                "DELETE FROM product_events WHERE created_at <= ?",
                (cutoff,),
            )
            return cursor.rowcount


def get_product_analytics_snapshot(
    database_path: Path, *, user_id: str, window_days: int = 30
) -> dict[str, Any]:
    """Return per-user aggregated product analytics without exposing raw content."""
    window_days = max(1, window_days)
    cutoff = (datetime.now(UTC) - timedelta(days=window_days)).isoformat()
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT feature, event_name, result, COUNT(*) AS event_count
            FROM product_events
            WHERE user_id = ? AND created_at >= ?
            GROUP BY feature, event_name, result
            ORDER BY feature ASC, event_name ASC
            """,
            (user_id, cutoff),
        ).fetchall()
        daily_rows = connection.execute(
            """
            SELECT substr(created_at, 1, 10) AS event_date,
                   COUNT(*) AS event_count,
                   SUM(CASE WHEN result IN ('success', 'accepted', 'completed') THEN 1 ELSE 0 END) AS success_count,
                   SUM(CASE WHEN result = 'failure' THEN 1 ELSE 0 END) AS failure_count
            FROM product_events
            WHERE user_id = ? AND created_at >= ?
            GROUP BY substr(created_at, 1, 10)
            ORDER BY event_date ASC
            """,
            (user_id, cutoff),
        ).fetchall()
    feature_totals: dict[str, dict[str, int]] = {}
    successful_events = 0
    failed_events = 0
    for row in rows:
        feature = row["feature"]
        count = int(row["event_count"])
        payload = feature_totals.setdefault(
            feature, {"event_count": 0, "positive_count": 0, "negative_count": 0}
        )
        payload["event_count"] += count
        if row["result"] in {"success", "accepted", "completed"}:
            payload["positive_count"] += count
        elif row["result"] in {"failure", "dismissed"}:
            payload["negative_count"] += count
        if row["result"] in {"success", "accepted", "completed"}:
            successful_events += count
        elif row["result"] == "failure":
            failed_events += count
    features = []
    for feature_name, counts in sorted(feature_totals.items()):
        total = int(counts["event_count"])
        positive_count = int(counts["positive_count"])
        outcome_count = positive_count + int(counts["negative_count"])
        features.append(
            {
                "feature": feature_name,
                "event_count": total,
                "positive_outcome_rate": (
                    round(positive_count / outcome_count, 4) if outcome_count else None
                ),
                "positive_outcomes": positive_count,
            }
        )
    trend = [
        {
            "date": row["event_date"],
            "event_count": int(row["event_count"]),
            "success_count": int(row["success_count"] or 0),
            "failure_count": int(row["failure_count"] or 0),
        }
        for row in daily_rows
    ]
    total_events = sum(item["event_count"] for item in features)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "enabled": True,
        "window_days": window_days,
        "totals": {
            "event_count": total_events,
            "success_count": successful_events,
            "failure_count": failed_events,
            "feature_count": len(features),
        },
        "features": features,
        "trends": trend,
        "privacy": {
            "retention_days": window_days,
            "raw_content": "not_collected",
            "identifiers": "user_id_only_for_personalized_views",
        },
    }


def initialize_storage(database_path: Path) -> None:
    """Create the local database and apply idempotent, data-preserving migrations."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path, timeout=5)) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        with connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("Database schema is newer than this application")

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
                    page_count INTEGER NOT NULL CHECK (page_count > 0),
                    chunk_count INTEGER NOT NULL CHECK (chunk_count > 0),
                    created_at TEXT NOT NULL,
                    parser_version TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL CHECK (page_number > 0),
                    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
                    char_start INTEGER NOT NULL CHECK (char_start >= 0),
                    char_end INTEGER NOT NULL CHECK (char_end > char_start),
                    text TEXT NOT NULL CHECK (length(text) > 0),
                    UNIQUE (document_id, page_number, chunk_index)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_document_page "
                "ON chunks(document_id, page_number, chunk_index)"
            )
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                USING fts5(chunk_id UNINDEXED, text, tokenize='unicode61')
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chunk_embeddings (
                    chunk_id TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    model_id TEXT NOT NULL,
                    model_revision TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
                    vector BLOB NOT NULL,
                    CHECK (length(vector) = dimensions * 4)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_config "
                "ON chunk_embeddings(config_hash, chunk_id)"
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quiz_sets (
                    quiz_id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quiz_questions (
                    question_id TEXT PRIMARY KEY,
                    quiz_id TEXT NOT NULL REFERENCES quiz_sets(quiz_id) ON DELETE CASCADE,
                    position INTEGER NOT NULL CHECK (position >= 0),
                    question_type TEXT NOT NULL CHECK (question_type IN (
                        'multiple_choice', 'short_answer'
                    )),
                    prompt TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    answer_key TEXT NOT NULL,
                    accepted_answers_json TEXT NOT NULL,
                    explanation TEXT NOT NULL,
                    UNIQUE (quiz_id, position)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quiz_citations (
                    question_id TEXT NOT NULL REFERENCES quiz_questions(
                        question_id
                    ) ON DELETE CASCADE,
                    citation_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    position INTEGER NOT NULL CHECK (position >= 0),
                    retrieval_json TEXT NOT NULL,
                    PRIMARY KEY (question_id, citation_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quiz_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    question_id TEXT NOT NULL REFERENCES quiz_questions(
                        question_id
                    ) ON DELETE CASCADE,
                    outcome TEXT NOT NULL CHECK (outcome IN ('correct', 'incorrect', 'ambiguous')),
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_concepts (
                    concept_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    aliases_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_concept_evidence (
                    concept_id TEXT NOT NULL REFERENCES knowledge_concepts(concept_id)
                        ON DELETE CASCADE,
                    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL CHECK (page_number > 0),
                    supporting_text TEXT NOT NULL CHECK (length(supporting_text) > 0),
                    PRIMARY KEY (concept_id, chunk_id, supporting_text)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_relationships (
                    relationship_id TEXT PRIMARY KEY,
                    source_concept_id TEXT NOT NULL REFERENCES knowledge_concepts(concept_id)
                        ON DELETE CASCADE,
                    target_concept_id TEXT NOT NULL REFERENCES knowledge_concepts(concept_id)
                        ON DELETE CASCADE,
                    relation TEXT NOT NULL,
                    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL CHECK (page_number > 0),
                    supporting_text TEXT NOT NULL CHECK (length(supporting_text) > 0),
                    status TEXT NOT NULL CHECK (status IN ('candidate', 'human_verified')),
                    UNIQUE(source_concept_id, target_concept_id, relation, chunk_id,
                           supporting_text)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS study_sessions (
                    session_id TEXT PRIMARY KEY,
                    topic_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_study_sessions_completed "
                "ON study_sessions(completed_at DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS topic_review_items (
                    question_id TEXT PRIMARY KEY REFERENCES quiz_questions(question_id)
                        ON DELETE CASCADE,
                    topic_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    last_attempt_id TEXT NOT NULL REFERENCES quiz_attempts(attempt_id)
                        ON DELETE CASCADE,
                    last_outcome TEXT NOT NULL CHECK (last_outcome IN (
                        'correct', 'incorrect', 'ambiguous'
                    )),
                    attempted_at TEXT NOT NULL,
                    due_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_topic_review_due "
                "ON topic_review_items(due_at, topic_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_relationship_source "
                "ON knowledge_relationships(source_concept_id, target_concept_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS study_coach_recommendations (
                    recommendation_id TEXT PRIMARY KEY,
                    plan_date TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    title TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    reasons_json TEXT NOT NULL,
                    graph_concept_id TEXT,
                    related_concept_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'planned', 'accepted', 'dismissed', 'completed'
                    )),
                    estimated_minutes INTEGER NOT NULL CHECK (estimated_minutes BETWEEN 5 AND 180),
                    updated_at TEXT NOT NULL,
                    UNIQUE(plan_date, topic_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_study_coach_date "
                "ON study_coach_recommendations(plan_date, priority DESC)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_images (
                    image_id TEXT PRIMARY KEY,
                    display_filename TEXT NOT NULL,
                    content_type TEXT NOT NULL CHECK (content_type IN ('image/png', 'image/jpeg')),
                    width INTEGER NOT NULL CHECK (width > 0),
                    height INTEGER NOT NULL CHECK (height > 0),
                    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    ocr_status TEXT NOT NULL CHECK (ocr_status IN (
                        'available', 'empty', 'unavailable', 'failed'
                    )),
                    extracted_text TEXT,
                    image_bytes BLOB NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS image_regions (
                    region_id TEXT PRIMARY KEY,
                    image_id TEXT NOT NULL REFERENCES learning_images(image_id) ON DELETE CASCADE,
                    x REAL NOT NULL CHECK (x >= 0 AND x < 1),
                    y REAL NOT NULL CHECK (y >= 0 AND y < 1),
                    width REAL NOT NULL CHECK (width > 0 AND width <= 1 AND x + width <= 1),
                    height REAL NOT NULL CHECK (height > 0 AND height <= 1 AND y + height <= 1),
                    ocr_status TEXT NOT NULL CHECK (ocr_status IN (
                        'available', 'empty', 'unavailable', 'failed'
                    )),
                    extracted_text TEXT,
                    diagram_id TEXT,
                    concept_id TEXT,
                    concept_name TEXT,
                    created_at TEXT NOT NULL,
                    CHECK ((diagram_id IS NULL) = (concept_id IS NULL)),
                    UNIQUE(image_id, region_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_image_regions_image "
                "ON image_regions(image_id, created_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS image_region_citations (
                    region_id TEXT NOT NULL REFERENCES image_regions(region_id) ON DELETE CASCADE,
                    citation_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                    position INTEGER NOT NULL CHECK (position >= 0),
                    retrieval_json TEXT NOT NULL,
                    PRIMARY KEY (region_id, citation_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quiz_questions_quiz_position "
                "ON quiz_questions(quiz_id, position)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quiz_attempts_question_time "
                "ON quiz_attempts(question_id, created_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_created_at "
                "ON documents(created_at DESC, document_id ASC)"
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS remove_quiz_question_without_citations
                AFTER DELETE ON quiz_citations
                WHEN NOT EXISTS (
                    SELECT 1 FROM quiz_citations WHERE question_id = OLD.question_id
                )
                BEGIN
                    DELETE FROM quiz_questions WHERE question_id = OLD.question_id;
                END
                """
            )
            # --- M23: Authentication & user isolation ---
            # Add user_id columns to all user-owned tables (nullable for
            # backward compatibility with pre-M23 data).  Existing rows keep
            # user_id = NULL (orphaned/legacy); new writes require auth and
            # set user_id from the session.
            _ensure_column(
                connection,
                "documents",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "quiz_sets",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "quiz_attempts",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "study_sessions",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "learning_images",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "knowledge_concepts",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "study_coach_recommendations",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            # Users table
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            _ensure_column(
                connection,
                "users",
                "is_admin",
                "is_admin INTEGER NOT NULL DEFAULT 0",
            )
            # User sessions table
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id)
                        ON DELETE CASCADE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at "
                "ON user_sessions(expires_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS product_events (
                    event_id TEXT PRIMARY KEY,
                    user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE,
                    event_name TEXT NOT NULL,
                    feature TEXT NOT NULL,
                    result TEXT NOT NULL,
                    category TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_items (
                    feedback_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    category TEXT NOT NULL CHECK (category IN ('bug', 'usability', 'feature_request', 'other')),
                    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
                    description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 4000),
                    status TEXT NOT NULL CHECK (status IN ('new', 'reviewing', 'planned', 'resolved', 'withdrawn')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_items_user_id ON feedback_items(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_items_status ON feedback_items(status)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_product_events_user_id ON product_events(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_product_events_created_at ON product_events(created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_product_events_feature ON product_events(feature)"
            )
            # Indexes on user_id columns for query performance
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quiz_sets_user_id ON quiz_sets(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quiz_attempts_user_id ON quiz_attempts(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_study_sessions_user_id ON study_sessions(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_learning_images_user_id ON learning_images(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_concepts_user_id "
                "ON knowledge_concepts(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_study_coach_recs_user_id "
                "ON study_coach_recommendations(user_id)"
            )
            # Additional user_id columns on inherited tables for simpler queries
            _ensure_column(
                connection,
                "chunks",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "quiz_questions",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "quiz_citations",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "image_regions",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "image_region_citations",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "knowledge_concept_evidence",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "knowledge_relationships",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "chunk_embeddings",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            _ensure_column(
                connection,
                "topic_review_items",
                "user_id",
                "user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE",
            )
            # Additional indexes
            connection.execute("CREATE INDEX IF NOT EXISTS idx_chunks_user_id ON chunks(user_id)")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quiz_questions_user_id ON quiz_questions(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_quiz_citations_user_id ON quiz_citations(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_image_regions_user_id ON image_regions(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_image_region_citations_user_id "
                "ON image_region_citations(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_concept_evidence_user_id "
                "ON knowledge_concept_evidence(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_knowledge_relationships_user_id "
                "ON knowledge_relationships(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_user_id "
                "ON chunk_embeddings(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_topic_review_items_user_id "
                "ON topic_review_items(user_id)"
            )

            if version < 9:
                connection.execute(
                    """
                    CREATE TABLE documents_m23 (
                        document_id TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        byte_size INTEGER NOT NULL CHECK (byte_size > 0),
                        page_count INTEGER NOT NULL CHECK (page_count > 0),
                        chunk_count INTEGER NOT NULL CHECK (chunk_count > 0),
                        created_at TEXT NOT NULL,
                        parser_version TEXT NOT NULL,
                        user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE,
                        UNIQUE(user_id, sha256)
                    )
                    """
                )
                connection.execute(
                    """INSERT INTO documents_m23(
                           document_id, display_name, sha256, byte_size, page_count,
                           chunk_count, created_at, parser_version, user_id
                       )
                       SELECT document_id, display_name, sha256, byte_size, page_count,
                              chunk_count, created_at, parser_version, user_id
                       FROM documents"""
                )
                connection.execute("DROP TABLE documents")
                connection.execute("ALTER TABLE documents_m23 RENAME TO documents")
                connection.execute(
                    """
                    CREATE TABLE study_coach_recommendations_m23 (
                        recommendation_id TEXT PRIMARY KEY,
                        plan_date TEXT NOT NULL,
                        topic_id TEXT NOT NULL,
                        topic TEXT NOT NULL,
                        title TEXT NOT NULL,
                        priority INTEGER NOT NULL,
                        reasons_json TEXT NOT NULL,
                        graph_concept_id TEXT,
                        related_concept_ids_json TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN (
                            'planned', 'accepted', 'dismissed', 'completed'
                        )),
                        estimated_minutes INTEGER NOT NULL
                            CHECK (estimated_minutes BETWEEN 5 AND 180),
                        updated_at TEXT NOT NULL,
                        user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE,
                        UNIQUE(plan_date, topic_id, user_id)
                    )
                    """
                )
                connection.execute(
                    """INSERT INTO study_coach_recommendations_m23(
                           recommendation_id, plan_date, topic_id, topic, title, priority,
                           reasons_json, graph_concept_id, related_concept_ids_json,
                           status, estimated_minutes, updated_at, user_id
                       )
                       SELECT recommendation_id, plan_date, topic_id, topic, title, priority,
                              reasons_json, graph_concept_id, related_concept_ids_json,
                              status, estimated_minutes, updated_at, user_id
                       FROM study_coach_recommendations"""
                )
                connection.execute("DROP TABLE study_coach_recommendations")
                connection.execute(
                    "ALTER TABLE study_coach_recommendations_m23 "
                    "RENAME TO study_coach_recommendations"
                )
                connection.execute(
                    "UPDATE knowledge_concepts SET normalized_name = "
                    "user_id || char(31) || normalized_name "
                    "WHERE user_id IS NOT NULL"
                )
                legacy_sessions = connection.execute(
                    "SELECT session_id FROM user_sessions"
                ).fetchall()
                for row in legacy_sessions:
                    digest = hashlib.sha256(row[0].encode("utf-8")).hexdigest()
                    connection.execute(
                        "UPDATE user_sessions SET session_id=? WHERE session_id=?",
                        (digest, row[0]),
                    )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_documents_created "
                    "ON documents(created_at DESC, document_id ASC)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_study_coach_date "
                    "ON study_coach_recommendations(plan_date, priority DESC)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_study_coach_recs_user_id "
                    "ON study_coach_recommendations(user_id)"
                )

            if version < 10:
                owner_relations = (
                    ("chunks", "documents", "document_id", "document_id"),
                    ("quiz_questions", "quiz_sets", "quiz_id", "quiz_id"),
                    ("quiz_attempts", "quiz_questions", "question_id", "question_id"),
                    ("quiz_citations", "quiz_questions", "question_id", "question_id"),
                    ("image_regions", "learning_images", "image_id", "image_id"),
                    ("image_region_citations", "image_regions", "region_id", "region_id"),
                    (
                        "knowledge_concept_evidence",
                        "knowledge_concepts",
                        "concept_id",
                        "concept_id",
                    ),
                    (
                        "knowledge_relationships",
                        "knowledge_concepts",
                        "source_concept_id",
                        "concept_id",
                    ),
                    ("chunk_embeddings", "chunks", "chunk_id", "chunk_id"),
                    ("topic_review_items", "quiz_questions", "question_id", "question_id"),
                )
                for child, parent, child_key, parent_key in owner_relations:
                    mismatch = connection.execute(
                        f"""SELECT 1 FROM {child} AS child
                            JOIN {parent} AS parent
                              ON parent.{parent_key}=child.{child_key}
                            WHERE child.user_id IS NOT NULL
                              AND parent.user_id IS NOT NULL
                              AND child.user_id != parent.user_id
                            LIMIT 1"""
                    ).fetchone()
                    if mismatch is not None:
                        raise RuntimeError(
                            f"Cannot safely migrate conflicting ownership in {child}"
                        )
                    connection.execute(
                        f"""UPDATE {child}
                            SET user_id=(
                                SELECT parent.user_id FROM {parent} AS parent
                                WHERE parent.{parent_key}={child}.{child_key}
                            )
                            WHERE user_id IS NULL AND EXISTS (
                                SELECT 1 FROM {parent} AS parent
                                WHERE parent.{parent_key}={child}.{child_key}
                                  AND parent.user_id IS NOT NULL
                            )"""
                    )

                cross_owner_checks = (
                    """SELECT 1 FROM quiz_citations AS citation
                       JOIN chunks AS chunk ON chunk.chunk_id=citation.citation_id
                       WHERE citation.user_id IS NOT chunk.user_id LIMIT 1""",
                    """SELECT 1 FROM image_region_citations AS citation
                       JOIN chunks AS chunk ON chunk.chunk_id=citation.citation_id
                       WHERE citation.user_id IS NOT chunk.user_id LIMIT 1""",
                    """SELECT 1 FROM knowledge_concept_evidence AS evidence
                       JOIN chunks AS chunk ON chunk.chunk_id=evidence.chunk_id
                       WHERE evidence.user_id IS NOT chunk.user_id LIMIT 1""",
                    """SELECT 1 FROM knowledge_relationships AS relationship
                       JOIN knowledge_concepts AS target
                         ON target.concept_id=relationship.target_concept_id
                       JOIN chunks AS chunk ON chunk.chunk_id=relationship.chunk_id
                       WHERE relationship.user_id IS NOT target.user_id
                          OR relationship.user_id IS NOT chunk.user_id LIMIT 1""",
                    """SELECT 1 FROM topic_review_items AS review
                       JOIN quiz_attempts AS attempt
                         ON attempt.attempt_id=review.last_attempt_id
                       WHERE review.user_id IS NOT attempt.user_id LIMIT 1""",
                )
                if any(connection.execute(query).fetchone() for query in cross_owner_checks):
                    raise RuntimeError("Cannot safely migrate resources with conflicting ownership")

            if version < 13:
                # M32: Feedback analysis and product improvement loop.
                # Create improvement_tasks and its junction table first so the
                # feedback_items recreation can reference them safely.
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS improvement_tasks (
                        task_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
                        description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 4000),
                        status TEXT NOT NULL CHECK (status IN ('proposed', 'in_progress', 'resolved', 'declined')),
                        priority_score REAL NOT NULL DEFAULT 0,
                        priority_factors_json TEXT NOT NULL DEFAULT '{}',
                        severity INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 5),
                        impact INTEGER NOT NULL CHECK (impact BETWEEN 1 AND 5),
                        report_count INTEGER NOT NULL DEFAULT 0,
                        reproducibility INTEGER NOT NULL CHECK (reproducibility BETWEEN 1 AND 5),
                        workflow_frequency INTEGER NOT NULL CHECK (workflow_frequency BETWEEN 1 AND 5),
                        implementation_effort INTEGER NOT NULL CHECK (implementation_effort BETWEEN 1 AND 5),
                        regression_test TEXT,
                        implemented INTEGER NOT NULL DEFAULT 0 CHECK (implemented IN (0, 1)),
                        implemented_at TEXT,
                        user_id TEXT REFERENCES users(user_id) ON DELETE CASCADE,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_improvement_tasks_user_id "
                    "ON improvement_tasks(user_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_improvement_tasks_status "
                    "ON improvement_tasks(status)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_improvement_tasks_priority "
                    "ON improvement_tasks(priority_score DESC)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS improvement_task_feedback (
                        improvement_task_id TEXT NOT NULL
                            REFERENCES improvement_tasks(task_id) ON DELETE CASCADE,
                        feedback_id TEXT NOT NULL
                            REFERENCES feedback_items(feedback_id) ON DELETE CASCADE,
                        PRIMARY KEY (improvement_task_id, feedback_id)
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_improvement_task_feedback_feedback "
                    "ON improvement_task_feedback(feedback_id)"
                )
                # Recreate feedback_items with expanded status CHECK and new columns.
                connection.execute(
                    """
                    CREATE TABLE feedback_items_m32 (
                        feedback_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        category TEXT NOT NULL CHECK (category IN ('bug', 'usability', 'feature_request', 'other')),
                        title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
                        description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 4000),
                        status TEXT NOT NULL CHECK (status IN ('new', 'reviewing', 'planned', 'in_progress', 'resolved', 'withdrawn', 'declined')),
                        resolution_notes TEXT,
                        improvement_task_id TEXT REFERENCES improvement_tasks(task_id),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO feedback_items_m32(
                        feedback_id, user_id, category, title, description, status,
                        resolution_notes, improvement_task_id, created_at, updated_at
                    )
                    SELECT feedback_id, user_id, category, title, description, status,
                        NULL, NULL, created_at, updated_at
                    FROM feedback_items
                    """
                )
                connection.execute("DROP TABLE feedback_items")
                connection.execute("ALTER TABLE feedback_items_m32 RENAME TO feedback_items")
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_feedback_items_user_id ON feedback_items(user_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_feedback_items_status ON feedback_items(status)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_feedback_items_improvement_task "
                    "ON feedback_items(improvement_task_id)"
                )

            if version < 14:
                # M34: Personalized learning paths and adaptive difficulty.
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS learning_paths (
                        path_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
                        description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 4000),
                        status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'completed', 'archived')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_learning_paths_user_id ON learning_paths(user_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_learning_paths_status ON learning_paths(status)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS learning_objectives (
                        objective_id TEXT PRIMARY KEY,
                        path_id TEXT NOT NULL REFERENCES learning_paths(path_id) ON DELETE CASCADE,
                        title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
                        description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 4000),
                        status TEXT NOT NULL CHECK (status IN ('not_started', 'in_progress', 'completed', 'skipped')),
                        order_index INTEGER NOT NULL CHECK (order_index >= 0),
                        source_document_id TEXT REFERENCES documents(document_id) ON DELETE SET NULL,
                        source_chunk_id TEXT REFERENCES chunks(chunk_id) ON DELETE SET NULL,
                        source_passage TEXT,
                        prerequisite_objective_ids TEXT NOT NULL DEFAULT '[]',
                        rationale TEXT,
                        difficulty_level TEXT CHECK (difficulty_level IN ('foundational', 'building', 'practice', 'advanced')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_learning_objectives_path_id ON learning_objectives(path_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_learning_objectives_status ON learning_objectives(status)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS objective_evidence_links (
                        objective_id TEXT NOT NULL REFERENCES learning_objectives(objective_id) ON DELETE CASCADE,
                        citation_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                        position INTEGER NOT NULL CHECK (position >= 0),
                        PRIMARY KEY (objective_id, citation_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS objective_activity_links (
                        objective_id TEXT NOT NULL REFERENCES learning_objectives(objective_id) ON DELETE CASCADE,
                        activity_type TEXT NOT NULL CHECK (activity_type IN ('quiz_attempt', 'study_session')),
                        activity_id TEXT NOT NULL,
                        PRIMARY KEY (objective_id, activity_type, activity_id)
                    )
                    """
                )

            if version < 15:
                # M36: Shared study spaces (private collaboration workspaces).
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS study_spaces (
                        space_id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
                        description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 4000),
                        learning_objective TEXT,
                        status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_study_spaces_owner_id "
                    "ON study_spaces(owner_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_study_spaces_status "
                    "ON study_spaces(status)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workspace_members (
                        space_id TEXT NOT NULL REFERENCES study_spaces(space_id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        role TEXT NOT NULL CHECK (role IN ('owner', 'member')),
                        joined_at TEXT NOT NULL,
                        PRIMARY KEY (space_id, user_id)
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_workspace_members_user_id "
                    "ON workspace_members(user_id)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workspace_documents (
                        space_id TEXT NOT NULL REFERENCES study_spaces(space_id) ON DELETE CASCADE,
                        document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                        shared_by TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        shared_at TEXT NOT NULL,
                        PRIMARY KEY (space_id, document_id)
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_workspace_documents_document_id "
                    "ON workspace_documents(document_id)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workspace_discussions (
                        discussion_id TEXT PRIMARY KEY,
                        space_id TEXT NOT NULL REFERENCES study_spaces(space_id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 4000),
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_workspace_discussions_space_id "
                    "ON workspace_discussions(space_id, created_at DESC)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workspace_discussion_replies (
                        reply_id TEXT PRIMARY KEY,
                        discussion_id TEXT NOT NULL REFERENCES workspace_discussions(discussion_id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 4000),
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_workspace_discussion_replies_discussion_id "
                    "ON workspace_discussion_replies(discussion_id, created_at)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workspace_quizzes (
                        space_id TEXT NOT NULL REFERENCES study_spaces(space_id) ON DELETE CASCADE,
                        quiz_id TEXT NOT NULL REFERENCES quiz_sets(quiz_id) ON DELETE CASCADE,
                        shared_by TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        shared_at TEXT NOT NULL,
                        PRIMARY KEY (space_id, quiz_id)
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_workspace_quizzes_quiz_id "
                    "ON workspace_quizzes(quiz_id)"
                )

            if version < 16:
                # M36: Optional learning objective on shared study spaces.
                _ensure_column(
                    connection,
                    "study_spaces",
                    "learning_objective",
                    "learning_objective TEXT",
                )

            if version < 17:
                # M38: AI Learning Content Studio - generated learning resources.
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS content_resources (
                        resource_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
                        description TEXT,
                        format TEXT NOT NULL CHECK (format IN (
                            'notes', 'summary', 'flashcards', 'questions',
                            'revision_sheet', 'concept_outline'
                        )),
                        detail_level TEXT NOT NULL CHECK (detail_level IN (
                            'brief', 'standard', 'comprehensive'
                        )),
                        status TEXT NOT NULL CHECK (status IN ('draft', 'reviewed', 'archived')),
                        source_type TEXT NOT NULL CHECK (source_type IN ('document', 'workspace')),
                        source_refs_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        reviewed_at TEXT,
                        reviewed_by TEXT
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_content_resources_user_id "
                    "ON content_resources(user_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_content_resources_status "
                    "ON content_resources(status)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_content_resources_created_at "
                    "ON content_resources(created_at DESC)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS content_resource_sections (
                        section_id TEXT PRIMARY KEY,
                        resource_id TEXT NOT NULL REFERENCES content_resources(resource_id)
                            ON DELETE CASCADE,
                        section_type TEXT NOT NULL CHECK (section_type IN (
                            'heading', 'body', 'flashcard', 'question', 'summary'
                        )),
                        section_title TEXT NOT NULL CHECK (length(section_title) BETWEEN 1 AND 200),
                        content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 10000),
                        citations_json TEXT NOT NULL DEFAULT '[]',
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_content_resource_sections_resource_id "
                    "ON content_resource_sections(resource_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_content_resource_sections_version "
                    "ON content_resource_sections(resource_id, version)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS content_resource_revisions (
                        revision_id TEXT PRIMARY KEY,
                        resource_id TEXT NOT NULL REFERENCES content_resources(resource_id)
                            ON DELETE CASCADE,
                        section_id TEXT NOT NULL REFERENCES content_resource_sections(section_id)
                            ON DELETE CASCADE,
                        content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 10000),
                        citations_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_content_resource_revisions_section_id "
                    "ON content_resource_revisions(section_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_content_resource_revisions_created_at "
                    "ON content_resource_revisions(created_at DESC)"
                )

            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.execute("PRAGMA foreign_keys = ON")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("Database migration produced invalid foreign-key references")


def check_storage(database_path: Path) -> dict[str, bool]:
    """Check that the configured existing database passes integrity and supports FTS5."""
    status = {"sqlite": False, "sqlite_fts5": False}
    try:
        with closing(connect_database(database_path)) as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            status["sqlite"] = integrity is not None and integrity[0] == "ok"
            connection.execute("CREATE VIRTUAL TABLE temp.readiness_fts USING fts5(content)")
            connection.execute("DROP TABLE temp.readiness_fts")
            status["sqlite_fts5"] = True
    except (OSError, sqlite3.Error):
        return status
    return status


def store_document(
    database_path: Path,
    metadata: dict[str, Any],
    chunks: list[dict[str, Any]],
    user_id: str | None = None,
) -> str | None:
    """Insert a document and all text/FTS rows atomically; return a duplicate's ID if present."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            if user_id is None:
                existing = connection.execute(
                    "SELECT document_id FROM documents WHERE sha256=? AND user_id IS NULL",
                    (metadata["sha256"],),
                ).fetchone()
            else:
                existing = connection.execute(
                    "SELECT document_id FROM documents WHERE sha256=? AND user_id=?",
                    (metadata["sha256"], user_id),
                ).fetchone()
            if existing:
                return str(existing["document_id"])

            connection.execute(
                """
                INSERT INTO documents (
                    document_id, display_name, sha256, byte_size, page_count,
                    chunk_count, created_at, parser_version, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metadata["document_id"],
                    metadata["display_name"],
                    metadata["sha256"],
                    metadata["byte_size"],
                    metadata["page_count"],
                    metadata["chunk_count"],
                    metadata["created_at"],
                    metadata["parser_version"],
                    user_id,
                ),
            )
            if chunks:
                connection.executemany(
                    """
                    INSERT INTO chunks (
                        chunk_id, document_id, page_number, chunk_index,
                        char_start, char_end, text, user_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            chunk["chunk_id"],
                            metadata["document_id"],
                            chunk["page_number"],
                            chunk["chunk_index"],
                            chunk["char_start"],
                            chunk["char_end"],
                            chunk["text"],
                            user_id,
                        )
                        for chunk in chunks
                    ],
                )
                connection.executemany(
                    "INSERT INTO chunks_fts(chunk_id, text) VALUES (?, ?)",
                    [(chunk["chunk_id"], chunk["text"]) for chunk in chunks],
                )
    return None


def list_documents(database_path: Path, user_id: str | None = None) -> list[dict[str, Any]]:
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            rows = connection.execute(
                """
                SELECT document_id, display_name, sha256, byte_size, page_count,
                       chunk_count, created_at, parser_version
                FROM documents WHERE user_id = ?
                ORDER BY created_at DESC, document_id ASC
                """,
                (user_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT document_id, display_name, sha256, byte_size, page_count,
                       chunk_count, created_at, parser_version
                FROM documents WHERE user_id IS NULL
                ORDER BY created_at DESC, document_id ASC
                """
            ).fetchall()
    return [dict(row) for row in rows]


def get_document(
    database_path: Path, document_id: str, user_id: str | None = None
) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            row = connection.execute(
                """
                SELECT document_id, display_name, sha256, byte_size, page_count,
                       chunk_count, created_at, parser_version
                FROM documents WHERE document_id = ? AND user_id = ?
                """,
                (document_id, user_id),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT document_id, display_name, sha256, byte_size, page_count,
                       chunk_count, created_at, parser_version
                FROM documents WHERE document_id = ? AND user_id IS NULL
                """,
                (document_id,),
            ).fetchone()
    return dict(row) if row else None


def delete_document(database_path: Path, document_id: str, user_id: str | None = None) -> bool:
    with closing(connect_database(database_path)) as connection:
        with connection:
            if user_id is not None:
                owned = connection.execute(
                    "SELECT 1 FROM documents WHERE document_id=? AND user_id=?",
                    (document_id, user_id),
                ).fetchone()
            else:
                owned = connection.execute(
                    "SELECT 1 FROM documents WHERE document_id=? AND user_id IS NULL",
                    (document_id,),
                ).fetchone()
            if owned is None:
                return False
            chunk_ids = connection.execute(
                "SELECT chunk_id FROM chunks WHERE document_id = ?",
                (document_id,),
            ).fetchall()
            connection.execute("DELETE FROM documents WHERE document_id=?", (document_id,))
            connection.executemany(
                "DELETE FROM chunks_fts WHERE chunk_id = ?",
                [(row["chunk_id"],) for row in chunk_ids],
            )
            if user_id is not None:
                connection.execute(
                    "DELETE FROM knowledge_concepts WHERE user_id=? AND NOT EXISTS ("
                    "SELECT 1 FROM knowledge_concept_evidence e "
                    "WHERE e.concept_id=knowledge_concepts.concept_id"
                    ") AND NOT EXISTS (SELECT 1 FROM knowledge_relationships r "
                    "WHERE r.source_concept_id=knowledge_concepts.concept_id "
                    "OR r.target_concept_id=knowledge_concepts.concept_id)",
                    (user_id,),
                )
            else:
                connection.execute(
                    "DELETE FROM knowledge_concepts WHERE user_id IS NULL AND NOT EXISTS ("
                    "SELECT 1 FROM knowledge_concept_evidence e "
                    "WHERE e.concept_id=knowledge_concepts.concept_id"
                    ") AND NOT EXISTS (SELECT 1 FROM knowledge_relationships r "
                    "WHERE r.source_concept_id=knowledge_concepts.concept_id "
                    "OR r.target_concept_id=knowledge_concepts.concept_id)"
                )
            return True


def _image_metadata(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "imageId": row["image_id"],
        "displayFilename": row["display_filename"],
        "contentType": row["content_type"],
        "width": row["width"],
        "height": row["height"],
        "byteSize": row["byte_size"],
        "sha256": row["sha256"],
        "createdAt": row["created_at"],
        "ocrStatus": row["ocr_status"],
        "extractedText": row["extracted_text"],
    }


def store_learning_image(
    database_path: Path,
    metadata: dict[str, Any],
    image_bytes: bytes,
    user_id: str | None = None,
) -> None:
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                """INSERT INTO learning_images(
                       image_id, display_filename, content_type, width, height, byte_size,
                       sha256, created_at, ocr_status, extracted_text, image_bytes,
                       user_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    metadata["imageId"],
                    metadata["displayFilename"],
                    metadata["contentType"],
                    metadata["width"],
                    metadata["height"],
                    len(image_bytes),
                    metadata["sha256"],
                    metadata["createdAt"],
                    metadata["ocrStatus"],
                    metadata["extractedText"],
                    image_bytes,
                    user_id,
                ),
            )


def list_learning_images(database_path: Path, user_id: str | None = None) -> list[dict[str, Any]]:
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            rows = connection.execute(
                """SELECT image_id, display_filename, content_type, width, height, byte_size,
                          sha256, created_at, ocr_status, NULL AS extracted_text
                   FROM learning_images WHERE user_id = ?
                   ORDER BY created_at DESC, image_id""",
                (user_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT image_id, display_filename, content_type, width, height, byte_size,
                          sha256, created_at, ocr_status, NULL AS extracted_text
                   FROM learning_images WHERE user_id IS NULL
                   ORDER BY created_at DESC, image_id"""
            ).fetchall()
    return [_image_metadata(row) for row in rows]


def get_learning_image(
    database_path: Path, image_id: str, user_id: str | None = None
) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            row = connection.execute(
                "SELECT * FROM learning_images WHERE image_id=? AND user_id=?",
                (image_id, user_id),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM learning_images WHERE image_id=? AND user_id IS NULL",
                (image_id,),
            ).fetchone()
    if row is None:
        return None
    return {**_image_metadata(row), "imageBytes": row["image_bytes"]}


def create_learning_image_region(
    database_path: Path, region: dict[str, Any], user_id: str | None = None
) -> None:
    with closing(connect_database(database_path)) as connection:
        with connection:
            image = connection.execute(
                "SELECT 1 FROM learning_images WHERE image_id=? AND user_id IS ?",
                (region["imageId"], user_id),
            ).fetchone()
            if image is None:
                raise ValueError("Image is not owned by the requested user")
            connection.execute(
                """INSERT INTO image_regions(
                       region_id, image_id, x, y, width, height, ocr_status, extracted_text,
                       diagram_id, concept_id, concept_name, created_at, user_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    region["regionId"],
                    region["imageId"],
                    region["geometry"]["x"],
                    region["geometry"]["y"],
                    region["geometry"]["width"],
                    region["geometry"]["height"],
                    region["ocrStatus"],
                    region["extractedText"],
                    region["diagramId"],
                    region["conceptId"],
                    region["conceptName"],
                    region["createdAt"],
                    user_id,
                ),
            )


def get_learning_image_region(
    database_path: Path, image_id: str, region_id: str, user_id: str | None = None
) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            row = connection.execute(
                """SELECT region_id, image_id, x, y, width, height, ocr_status, extracted_text,
                          diagram_id, concept_id, concept_name, created_at
                   FROM image_regions WHERE image_id=? AND region_id=? AND user_id=?""",
                (image_id, region_id, user_id),
            ).fetchone()
        else:
            row = connection.execute(
                """SELECT region_id, image_id, x, y, width, height, ocr_status, extracted_text,
                          diagram_id, concept_id, concept_name, created_at
                   FROM image_regions WHERE image_id=? AND region_id=? AND user_id IS NULL""",
                (image_id, region_id),
            ).fetchone()
    if row is None:
        return None
    return {
        "regionId": row["region_id"],
        "imageId": row["image_id"],
        "geometry": {key: row[key] for key in ("x", "y", "width", "height")},
        "ocrStatus": row["ocr_status"],
        "extractedText": row["extracted_text"],
        "diagramId": row["diagram_id"],
        "conceptId": row["concept_id"],
        "conceptName": row["concept_name"],
        "createdAt": row["created_at"],
    }


def replace_image_region_citations(
    database_path: Path,
    region_id: str,
    citations: list[dict[str, Any]],
    user_id: str | None = None,
) -> None:

    with closing(connect_database(database_path)) as connection:
        with connection:
            region = connection.execute(
                "SELECT 1 FROM image_regions WHERE region_id=? AND user_id IS ?",
                (region_id, user_id),
            ).fetchone()
            if region is None:
                raise ValueError("Image region is not owned by the requested user")
            for citation in citations:
                chunk = connection.execute(
                    """SELECT 1 FROM chunks c JOIN documents d
                         ON d.document_id=c.document_id
                       WHERE c.chunk_id=? AND c.user_id IS ? AND d.user_id IS ?""",
                    (citation["citationId"], user_id, user_id),
                ).fetchone()
                if chunk is None:
                    raise ValueError("Image citation is not owned by the requested user")
            if user_id is not None:
                connection.execute(
                    "DELETE FROM image_region_citations WHERE region_id=? AND user_id=?",
                    (region_id, user_id),
                )
            else:
                connection.execute(
                    "DELETE FROM image_region_citations WHERE region_id=? AND user_id IS NULL",
                    (region_id,),
                )
            connection.executemany(
                """INSERT INTO image_region_citations(
                       region_id, citation_id, position, retrieval_json, user_id
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        region_id,
                        item["citationId"],
                        index,
                        json.dumps(item["retrieval"]),
                        user_id,
                    )
                    for index, item in enumerate(citations)
                ],
            )


def get_learning_image_detail(
    database_path: Path, image_id: str, user_id: str | None = None
) -> dict[str, Any] | None:

    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            image = connection.execute(
                """SELECT image_id, display_filename, content_type, width, height, byte_size,
                          sha256, created_at, ocr_status, extracted_text
                   FROM learning_images WHERE image_id=? AND user_id=?""",
                (image_id, user_id),
            ).fetchone()
        else:
            image = connection.execute(
                """SELECT image_id, display_filename, content_type, width, height, byte_size,
                          sha256, created_at, ocr_status, extracted_text
                   FROM learning_images WHERE image_id=? AND user_id IS NULL""",
                (image_id,),
            ).fetchone()
        if image is None:
            return None
        if user_id is not None:
            regions = connection.execute(
                """SELECT region_id, image_id, x, y, width, height, ocr_status, extracted_text,
                          diagram_id, concept_id, concept_name, created_at
                   FROM image_regions WHERE image_id=? AND user_id=? ORDER BY created_at, region_id""",
                (image_id, user_id),
            ).fetchall()
        else:
            regions = connection.execute(
                """SELECT region_id, image_id, x, y, width, height, ocr_status, extracted_text,
                          diagram_id, concept_id, concept_name, created_at
                   FROM image_regions WHERE image_id=? AND user_id IS NULL ORDER BY created_at, region_id""",
                (image_id,),
            ).fetchall()
        result = _image_metadata(image)
        result["regions"] = []
        for row in regions:
            if user_id is not None:
                citations = connection.execute(
                    """SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
                              c.chunk_index, c.char_start, c.char_end, c.text,
                              irc.retrieval_json
                       FROM image_region_citations irc
                       JOIN chunks c ON c.chunk_id=irc.citation_id
                       JOIN documents d ON d.document_id=c.document_id
                       WHERE irc.region_id=? AND irc.user_id=? AND c.user_id=?
                         AND d.user_id=? ORDER BY irc.position""",
                    (row["region_id"], user_id, user_id, user_id),
                ).fetchall()
            else:
                citations = connection.execute(
                    """SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
                              c.chunk_index, c.char_start, c.char_end, c.text,
                              irc.retrieval_json
                       FROM image_region_citations irc
                       JOIN chunks c ON c.chunk_id=irc.citation_id
                       JOIN documents d ON d.document_id=c.document_id
                       WHERE irc.region_id=? AND irc.user_id IS NULL
                         AND c.user_id IS NULL AND d.user_id IS NULL ORDER BY irc.position""",
                    (row["region_id"],),
                ).fetchall()
            result["regions"].append(
                {
                    "regionId": row["region_id"],
                    "geometry": {key: row[key] for key in ("x", "y", "width", "height")},
                    "ocrStatus": row["ocr_status"],
                    "extractedText": row["extracted_text"],
                    "diagramId": row["diagram_id"],
                    "conceptId": row["concept_id"],
                    "conceptName": row["concept_name"],
                    "citations": [
                        {
                            "citationId": item["chunk_id"],
                            "documentId": item["document_id"],
                            "displayFilename": item["display_name"],
                            "pageNumber": item["page_number"],
                            "chunkIndex": item["chunk_index"],
                            "charStart": item["char_start"],
                            "charEnd": item["char_end"],
                            "text": item["text"],
                            "retrieval": json.loads(item["retrieval_json"]),
                        }
                        for item in citations
                    ],
                }
            )
    return result


def delete_learning_image(database_path: Path, image_id: str, user_id: str | None = None) -> bool:
    with closing(connect_database(database_path)) as connection:
        with connection:
            if user_id is not None:
                result = connection.execute(
                    "DELETE FROM learning_images WHERE image_id=? AND user_id=?",
                    (image_id, user_id),
                )
            else:
                result = connection.execute(
                    "DELETE FROM learning_images WHERE image_id=? AND user_id IS NULL",
                    (image_id,),
                )
    return result.rowcount > 0


def knowledge_graph_chunks(
    database_path: Path,
    *,
    document_ids: list[str] | None,
    limit: int,
    after_chunk_id: str | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    sql = """SELECT c.chunk_id, c.document_id, d.display_name, c.page_number, c.text
             FROM chunks c JOIN documents d ON d.document_id=c.document_id"""
    parameters: list[Any] = []
    conditions = []
    if after_chunk_id is not None:
        conditions.append("c.chunk_id > ?")
        parameters.append(after_chunk_id)
    if document_ids is not None:
        if not document_ids:
            return []
        conditions.append("c.document_id IN (" + ",".join("?" for _ in document_ids) + ")")
        parameters.extend(document_ids)
    if user_id is not None:
        conditions.append("c.user_id = ?")
        parameters.append(user_id)
    else:
        conditions.append("c.user_id IS NULL")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY c.chunk_id LIMIT ?"
    parameters.append(limit)
    with closing(connect_database(database_path)) as connection:
        return [dict(row) for row in connection.execute(sql, parameters)]


def store_knowledge_extraction(
    database_path: Path,
    chunk: dict[str, Any],
    extraction: dict[str, Any],
    user_id: str | None = None,
) -> None:
    """Atomically store concepts and evidence-backed candidate relations for one chunk."""

    with closing(connect_database(database_path)) as connection:
        with connection:
            owned_chunk = connection.execute(
                """SELECT 1 FROM chunks c JOIN documents d
                     ON d.document_id=c.document_id
                   WHERE c.chunk_id=? AND c.user_id IS ? AND d.user_id IS ?""",
                (chunk["chunk_id"], user_id, user_id),
            ).fetchone()
            if owned_chunk is None:
                raise ValueError("Knowledge graph source is not owned by the requested user")
            resolved: dict[str, str] = {}
            for concept in extraction["concepts"]:
                scoped_name = (
                    concept["normalized_name"]
                    if user_id is None
                    else f"{user_id}\x1f{concept['normalized_name']}"
                )
                row = connection.execute(
                    "SELECT concept_id, aliases_json FROM knowledge_concepts "
                    "WHERE normalized_name=? AND user_id IS ?",
                    (scoped_name, user_id),
                ).fetchone()
                if row is None:
                    concept_id = _user_scoped_id("kg", concept["concept_id"], user_id)
                    connection.execute(
                        "INSERT INTO knowledge_concepts("
                        "concept_id, canonical_name, normalized_name, aliases_json, user_id) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            concept_id,
                            concept["name"],
                            scoped_name,
                            json.dumps(concept["aliases"]),
                            user_id,
                        ),
                    )
                else:
                    concept_id = row["concept_id"]
                    aliases = list(
                        dict.fromkeys(json.loads(row["aliases_json"]) + concept["aliases"])
                    )
                    connection.execute(
                        "UPDATE knowledge_concepts SET aliases_json=? WHERE concept_id=?",
                        (json.dumps(aliases), concept_id),
                    )
                resolved[concept["normalized_name"]] = concept_id
                connection.execute(
                    "INSERT OR IGNORE INTO knowledge_concept_evidence("
                    "concept_id, chunk_id, page_number, supporting_text, user_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        concept_id,
                        chunk["chunk_id"],
                        chunk["page_number"],
                        concept["supporting_text"],
                        user_id,
                    ),
                )
            for relation in extraction["relationships"]:
                source_id = resolved[relation["source_normalized"]]
                target_id = resolved[relation["target_normalized"]]
                relationship_id = _user_scoped_id("rel", relation["relationship_id"], user_id)
                connection.execute(
                    """INSERT OR IGNORE INTO knowledge_relationships(
                       relationship_id, source_concept_id, target_concept_id, relation,
                       chunk_id, page_number, supporting_text, status, user_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate', ?)""",
                    (
                        relationship_id,
                        source_id,
                        target_id,
                        relation["relation"],
                        chunk["chunk_id"],
                        chunk["page_number"],
                        relation["supporting_text"],
                        user_id,
                    ),
                )


def list_knowledge_concepts(
    database_path: Path, user_id: str | None = None
) -> list[dict[str, Any]]:
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            rows = connection.execute(
                """SELECT c.concept_id, c.canonical_name, c.aliases_json,
                          count(DISTINCT e.chunk_id) AS evidence_count,
                          count(DISTINCT r.relationship_id) AS relationship_count
                   FROM knowledge_concepts c
                   LEFT JOIN knowledge_concept_evidence e ON e.concept_id=c.concept_id
                        AND e.user_id=c.user_id
                   LEFT JOIN knowledge_relationships r ON r.source_concept_id=c.concept_id
                        AND r.user_id=c.user_id
                        OR (r.target_concept_id=c.concept_id AND r.user_id=c.user_id)
                   WHERE c.user_id = ?
                   GROUP BY c.concept_id ORDER BY c.canonical_name COLLATE NOCASE""",
                (user_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT c.concept_id, c.canonical_name, c.aliases_json,
                          count(DISTINCT e.chunk_id) AS evidence_count,
                          count(DISTINCT r.relationship_id) AS relationship_count
                   FROM knowledge_concepts c
                   LEFT JOIN knowledge_concept_evidence e ON e.concept_id=c.concept_id
                        AND e.user_id IS NULL
                   LEFT JOIN knowledge_relationships r ON r.source_concept_id=c.concept_id
                        AND r.user_id IS NULL
                        OR (r.target_concept_id=c.concept_id AND r.user_id IS NULL)
                   WHERE c.user_id IS NULL
                   GROUP BY c.concept_id ORDER BY c.canonical_name COLLATE NOCASE"""
            ).fetchall()

    return [
        {
            "conceptId": row["concept_id"],
            "name": row["canonical_name"],
            "aliases": json.loads(row["aliases_json"]),
            "evidenceCount": row["evidence_count"],
            "relationshipCount": row["relationship_count"],
        }
        for row in rows
    ]


def get_knowledge_concept(
    database_path: Path, concept_id: str, user_id: str | None = None
) -> dict[str, Any] | None:

    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            concept = connection.execute(
                "SELECT * FROM knowledge_concepts WHERE concept_id=? AND user_id=?",
                (concept_id, user_id),
            ).fetchone()
        else:
            concept = connection.execute(
                "SELECT * FROM knowledge_concepts WHERE concept_id=? AND user_id IS NULL",
                (concept_id,),
            ).fetchone()
        if concept is None:
            return None
        if user_id is not None:
            evidence = connection.execute(
                """SELECT e.chunk_id, c.document_id, d.display_name, e.page_number,
                          e.supporting_text
                     FROM knowledge_concept_evidence e JOIN chunks c ON c.chunk_id=e.chunk_id
                     JOIN documents d ON d.document_id=c.document_id
                     WHERE e.concept_id=? AND e.user_id=?
                     ORDER BY c.document_id, e.page_number, e.chunk_id""",
                (concept_id, user_id),
            ).fetchall()
            relationships = connection.execute(
                """SELECT r.relationship_id, r.source_concept_id, s.canonical_name AS source_name,
                          r.target_concept_id, t.canonical_name AS target_name, r.relation,
                          r.chunk_id, c.document_id, d.display_name, r.page_number,
                          r.supporting_text, r.status
                     FROM knowledge_relationships r
                     JOIN knowledge_concepts s ON s.concept_id=r.source_concept_id
                     JOIN knowledge_concepts t ON t.concept_id=r.target_concept_id
                     JOIN chunks c ON c.chunk_id=r.chunk_id
                     JOIN documents d ON d.document_id=c.document_id
                     WHERE (r.source_concept_id=? OR r.target_concept_id=?)
                       AND r.user_id=?
                     ORDER BY r.source_concept_id, r.target_concept_id, r.relation, r.relationship_id""",
                (concept_id, concept_id, user_id),
            ).fetchall()
        else:
            evidence = connection.execute(
                """SELECT e.chunk_id, c.document_id, d.display_name, e.page_number,
                          e.supporting_text
                     FROM knowledge_concept_evidence e JOIN chunks c ON c.chunk_id=e.chunk_id
                     JOIN documents d ON d.document_id=c.document_id
                     WHERE e.concept_id=? AND e.user_id IS NULL
                     ORDER BY c.document_id, e.page_number, e.chunk_id""",
                (concept_id,),
            ).fetchall()
            relationships = connection.execute(
                """SELECT r.relationship_id, r.source_concept_id, s.canonical_name AS source_name,
                          r.target_concept_id, t.canonical_name AS target_name, r.relation,
                          r.chunk_id, c.document_id, d.display_name, r.page_number,
                          r.supporting_text, r.status
                     FROM knowledge_relationships r
                     JOIN knowledge_concepts s ON s.concept_id=r.source_concept_id
                     JOIN knowledge_concepts t ON t.concept_id=r.target_concept_id
                     JOIN chunks c ON c.chunk_id=r.chunk_id
                     JOIN documents d ON d.document_id=c.document_id
                     WHERE r.source_concept_id=? OR r.target_concept_id=?
                       AND r.user_id IS NULL
                     ORDER BY r.source_concept_id, r.target_concept_id, r.relation, r.relationship_id""",
                (concept_id, concept_id),
            ).fetchall()
    return {
        "conceptId": concept["concept_id"],
        "name": concept["canonical_name"],
        "aliases": json.loads(concept["aliases_json"]),
        "evidence": [
            {
                "chunkId": row["chunk_id"],
                "documentId": row["document_id"],
                "displayFilename": row["display_name"],
                "pageNumber": row["page_number"],
                "supportingText": row["supporting_text"],
            }
            for row in evidence
        ],
        "relationships": [
            {
                "relationshipId": row["relationship_id"],
                "sourceConceptId": row["source_concept_id"],
                "sourceName": row["source_name"],
                "targetConceptId": row["target_concept_id"],
                "targetName": row["target_name"],
                "relation": row["relation"],
                "chunkId": row["chunk_id"],
                "documentId": row["document_id"],
                "displayFilename": row["display_name"],
                "pageNumber": row["page_number"],
                "supportingText": row["supporting_text"],
                "status": row["status"],
            }
            for row in relationships
        ],
    }


def verify_knowledge_relationship(
    database_path: Path, relationship_id: str, user_id: str | None = None
) -> bool:
    with closing(connect_database(database_path)) as connection:
        with connection:
            if user_id is not None:
                connection.execute(
                    "UPDATE knowledge_relationships SET status='human_verified' "
                    "WHERE relationship_id=? AND status='candidate' AND user_id=?",
                    (relationship_id, user_id),
                )
                row = connection.execute(
                    "SELECT status FROM knowledge_relationships "
                    "WHERE relationship_id=? AND user_id=?",
                    (relationship_id, user_id),
                ).fetchone()
            else:
                connection.execute(
                    "UPDATE knowledge_relationships SET status='human_verified' "
                    "WHERE relationship_id=? AND status='candidate' AND user_id IS NULL",
                    (relationship_id,),
                )
                row = connection.execute(
                    "SELECT status FROM knowledge_relationships "
                    "WHERE relationship_id=? AND user_id IS NULL",
                    (relationship_id,),
                ).fetchone()
    return row is not None and row["status"] == "human_verified"


def search_chunks(
    database_path: Path,
    fts_query: str,
    document_ids: list[str] | None,
    limit: int,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
               c.chunk_index, c.char_start, c.char_end, c.text,
               bm25(chunks_fts) AS rank
        FROM chunks_fts
        JOIN chunks AS c ON c.chunk_id = chunks_fts.chunk_id
        JOIN documents AS d ON d.document_id = c.document_id
        WHERE chunks_fts MATCH ?
    """
    parameters: list[Any] = [fts_query]
    if document_ids is not None:
        placeholders = ",".join("?" for _ in document_ids)
        sql += f" AND c.document_id IN ({placeholders})"
        parameters.extend(document_ids)
    if user_id is not None:
        sql += " AND c.user_id = ?"
        parameters.append(user_id)
    else:
        sql += " AND c.user_id IS NULL"
    sql += " ORDER BY rank ASC, c.document_id ASC, c.page_number ASC, c.chunk_index ASC LIMIT ?"
    parameters.append(limit)

    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(sql, parameters).fetchall()
    return [dict(row) for row in rows]


def create_quiz_set(database_path: Path, quiz: dict[str, Any], user_id: str | None = None) -> None:
    """Persist generated questions and foreign-key citations atomically."""

    with closing(connect_database(database_path)) as connection:
        with connection:
            for question in quiz["questions"]:
                for citation in question["citations"]:
                    owned_chunk = connection.execute(
                        """SELECT 1 FROM chunks c JOIN documents d
                             ON d.document_id=c.document_id
                           WHERE c.chunk_id=? AND c.user_id IS ? AND d.user_id IS ?""",
                        (citation["citationId"], user_id, user_id),
                    ).fetchone()
                    if owned_chunk is None:
                        raise ValueError("Quiz citation is not owned by the requested user")
            connection.execute(
                "INSERT INTO quiz_sets(quiz_id, topic_id, topic, created_at, user_id) VALUES (?, ?, ?, ?, ?)",
                (quiz["quiz_id"], quiz["topic_id"], quiz["topic"], quiz["created_at"], user_id),
            )
            for position, question in enumerate(quiz["questions"]):
                connection.execute(
                    """
                    INSERT INTO quiz_questions(
                        question_id, quiz_id, position, question_type, prompt,
                        options_json, answer_key, accepted_answers_json, explanation,
                        user_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        question["question_id"],
                        quiz["quiz_id"],
                        position,
                        question["question_type"],
                        question["prompt"],
                        json.dumps(question["options"]),
                        question["answer_key"],
                        json.dumps(question["accepted_answers"]),
                        question["explanation"],
                        user_id,
                    ),
                )
                for citation_position, citation in enumerate(question["citations"]):
                    connection.execute(
                        """
                        INSERT INTO quiz_citations(
                            question_id, citation_id, position, retrieval_json, user_id
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            question["question_id"],
                            citation["citationId"],
                            citation_position,
                            json.dumps(citation["retrieval"]),
                            user_id,
                        ),
                    )


def get_quiz_set(
    database_path: Path,
    quiz_id: str,
    *,
    review_incorrect: bool = False,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    """Load a quiz with current source provenance and latest attempt outcomes."""

    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            quiz = connection.execute(
                "SELECT quiz_id, topic_id, topic, created_at FROM quiz_sets WHERE quiz_id = ? AND user_id = ?",
                (quiz_id, user_id),
            ).fetchone()
        else:
            quiz = connection.execute(
                "SELECT quiz_id, topic_id, topic, created_at FROM quiz_sets WHERE quiz_id = ? AND user_id IS NULL",
                (quiz_id,),
            ).fetchone()
        if quiz is None:
            return None
        if user_id is not None:
            rows = connection.execute(
                """
                SELECT q.*, latest.outcome AS latest_outcome,
                       (SELECT count(*) FROM quiz_attempts a
                        WHERE a.question_id=q.question_id AND a.user_id=?) AS attempt_count
                FROM quiz_questions q
                LEFT JOIN (
                    SELECT question_id, outcome,
                           row_number() OVER (
                               PARTITION BY question_id
                               ORDER BY created_at DESC, rowid DESC
                           ) AS rn
                    FROM quiz_attempts
                    WHERE user_id = ?
                ) latest ON latest.question_id=q.question_id AND latest.rn=1
                WHERE q.quiz_id=? AND q.user_id=?
                ORDER BY q.position
                """,
                (user_id, user_id, quiz_id, user_id),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT q.*, latest.outcome AS latest_outcome,
                       (SELECT count(*) FROM quiz_attempts a
                        WHERE a.question_id=q.question_id) AS attempt_count
                FROM quiz_questions q
                LEFT JOIN (
                    SELECT question_id, outcome,
                           row_number() OVER (
                               PARTITION BY question_id
                               ORDER BY created_at DESC, rowid DESC
                           ) AS rn
                    FROM quiz_attempts
                ) latest ON latest.question_id=q.question_id AND latest.rn=1
                WHERE q.quiz_id=? AND q.user_id IS NULL
                ORDER BY q.position
                """,
                (quiz_id,),
            ).fetchall()
        questions = []
        for row in rows:
            if review_incorrect and row["latest_outcome"] not in {"incorrect", "ambiguous"}:
                continue
            if user_id is None:
                citation_rows = connection.execute(
                    """SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
                              c.chunk_index, c.char_start, c.char_end, c.text,
                              qc.retrieval_json
                       FROM quiz_citations qc
                       JOIN chunks c ON c.chunk_id=qc.citation_id
                       JOIN documents d ON d.document_id=c.document_id
                       WHERE qc.question_id=? AND qc.user_id IS NULL
                         AND c.user_id IS NULL AND d.user_id IS NULL
                       ORDER BY qc.position""",
                    (row["question_id"],),
                ).fetchall()
            else:
                citation_rows = connection.execute(
                    """SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
                              c.chunk_index, c.char_start, c.char_end, c.text,
                              qc.retrieval_json
                       FROM quiz_citations qc
                       JOIN chunks c ON c.chunk_id=qc.citation_id
                       JOIN documents d ON d.document_id=c.document_id
                       WHERE qc.question_id=? AND qc.user_id=?
                         AND c.user_id=? AND d.user_id=?
                       ORDER BY qc.position""",
                    (row["question_id"], user_id, user_id, user_id),
                ).fetchall()
            citations = [
                {
                    "citationId": citation["chunk_id"],
                    "documentId": citation["document_id"],
                    "displayFilename": citation["display_name"],
                    "pageNumber": citation["page_number"],
                    "chunkIndex": citation["chunk_index"],
                    "charStart": citation["char_start"],
                    "charEnd": citation["char_end"],
                    "text": citation["text"],
                    "retrieval": json.loads(citation["retrieval_json"]),
                }
                for citation in citation_rows
            ]
            if not citations:
                continue
            has_attempt = row["latest_outcome"] is not None
            questions.append(
                {
                    "questionId": row["question_id"],
                    "questionType": row["question_type"],
                    "prompt": row["prompt"],
                    "options": json.loads(row["options_json"]),
                    "explanation": row["explanation"] if has_attempt else None,
                    "citations": citations if has_attempt else [],
                    "latestOutcome": row["latest_outcome"],
                    "attemptCount": row["attempt_count"],
                }
            )
        return {
            "quizId": quiz["quiz_id"],
            "topicId": quiz["topic_id"],
            "topic": quiz["topic"],
            "createdAt": quiz["created_at"],
            "questions": questions,
        }


def get_quiz_question(
    database_path: Path, question_id: str, user_id: str | None = None
) -> dict[str, Any] | None:

    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            row = connection.execute(
                "SELECT * FROM quiz_questions WHERE question_id=? AND user_id=?",
                (question_id, user_id),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM quiz_questions WHERE question_id=? AND user_id IS NULL",
                (question_id,),
            ).fetchone()
    if row is None:
        return None
    return {
        "question_id": row["question_id"],
        "quiz_id": row["quiz_id"],
        "question_type": row["question_type"],
        "options": json.loads(row["options_json"]),
        "answer_key": row["answer_key"],
        "accepted_answers": json.loads(row["accepted_answers_json"]),
        "explanation": row["explanation"],
    }


def record_quiz_attempt(
    database_path: Path,
    *,
    attempt_id: str,
    question_id: str,
    outcome: str,
    created_at: str,
    user_id: str | None = None,
) -> None:
    with closing(connect_database(database_path)) as connection:
        with connection:
            question = connection.execute(
                """SELECT s.topic_id, s.topic FROM quiz_questions q
                   JOIN quiz_sets s ON s.quiz_id=q.quiz_id
                   WHERE q.question_id=? AND q.user_id IS ? AND s.user_id IS ?""",
                (question_id, user_id, user_id),
            ).fetchone()
            if question is None:
                raise ValueError("Quiz question is not owned by the requested user")
            connection.execute(
                """
                INSERT INTO quiz_attempts(attempt_id, question_id, outcome, created_at, user_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (attempt_id, question_id, outcome, created_at, user_id),
            )
            from datetime import UTC, datetime, timedelta

            interval_days = {"incorrect": 1, "ambiguous": 1, "correct": 7}[outcome]
            attempted_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if attempted_at.tzinfo is None:
                attempted_at = attempted_at.replace(tzinfo=UTC)
            due_at = (attempted_at + timedelta(days=interval_days)).astimezone(UTC).isoformat()
            connection.execute(
                """INSERT INTO topic_review_items(
                           question_id, topic_id, topic, last_attempt_id, last_outcome,
                           attempted_at, due_at, user_id
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(question_id) DO UPDATE SET
                           topic_id=excluded.topic_id, topic=excluded.topic,
                           last_attempt_id=excluded.last_attempt_id,
                           last_outcome=excluded.last_outcome,
                           attempted_at=excluded.attempted_at, due_at=excluded.due_at""",
                (
                    question_id,
                    question["topic_id"],
                    question["topic"],
                    attempt_id,
                    outcome,
                    created_at,
                    due_at,
                    user_id,
                ),
            )


def start_study_session(
    database_path: Path,
    *,
    session_id: str,
    topic_id: str,
    topic: str,
    started_at: str,
    user_id: str | None = None,
) -> dict[str, Any]:
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                "INSERT INTO study_sessions("
                "session_id, topic_id, topic, started_at, user_id) VALUES (?, ?, ?, ?, ?)",
                (session_id, topic_id, topic, started_at, user_id),
            )
    return {
        "sessionId": session_id,
        "topicId": topic_id,
        "topic": topic,
        "startedAt": started_at,
        "completedAt": None,
    }


def complete_study_session(
    database_path: Path,
    *,
    session_id: str,
    completed_at: str,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        with connection:
            if user_id is not None:
                connection.execute(
                    "UPDATE study_sessions SET completed_at=? "
                    "WHERE session_id=? AND completed_at IS NULL AND user_id=?",
                    (completed_at, session_id, user_id),
                )
            else:
                connection.execute(
                    "UPDATE study_sessions SET completed_at=? "
                    "WHERE session_id=? AND completed_at IS NULL AND user_id IS NULL",
                    (completed_at, session_id),
                )
            row = connection.execute(
                "SELECT session_id, topic_id, topic, started_at, completed_at "
                "FROM study_sessions WHERE session_id=? AND user_id IS ?",
                (session_id, user_id),
            ).fetchone()
    if row is None:
        return None
    return {
        "sessionId": row["session_id"],
        "topicId": row["topic_id"],
        "topic": row["topic"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
    }


def get_learning_analytics_data(database_path: Path, user_id: str | None = None) -> dict[str, Any]:

    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            attempts = [
                dict(row)
                for row in connection.execute(
                    """SELECT a.rowid AS sequence, a.attempt_id AS attemptId,
                              a.question_id AS questionId,
                              a.outcome, a.created_at AS createdAt, q.prompt,
                              s.topic_id AS topicId, s.topic
                       FROM quiz_attempts a JOIN quiz_questions q ON q.question_id=a.question_id
                       JOIN quiz_sets s ON s.quiz_id=q.quiz_id
                       WHERE a.user_id = ? AND q.user_id = ? AND s.user_id = ?
                       ORDER BY a.created_at DESC, a.rowid DESC""",
                    (user_id, user_id, user_id),
                )
            ]
            sessions = [
                {
                    "sessionId": row["session_id"],
                    "topicId": row["topic_id"],
                    "topic": row["topic"],
                    "startedAt": row["started_at"],
                    "completedAt": row["completed_at"],
                }
                for row in connection.execute(
                    "SELECT * FROM study_sessions WHERE user_id=? ORDER BY started_at DESC, rowid DESC",
                    (user_id,),
                )
            ]
            reviews = [
                {
                    "questionId": row["question_id"],
                    "topicId": row["topic_id"],
                    "topic": row["topic"],
                    "outcome": row["last_outcome"],
                    "attemptedAt": row["attempted_at"],
                    "dueAt": row["due_at"],
                }
                for row in connection.execute(
                    "SELECT * FROM topic_review_items WHERE user_id=? ORDER BY due_at, question_id",
                    (user_id,),
                )
            ]
            source_rows = connection.execute(
                """SELECT s.topic_id, qc.citation_id AS chunk_id,
                          MIN(qc.retrieval_json) AS retrieval_json, c.document_id,
                          d.display_name, c.page_number, c.chunk_index, c.char_start,
                          c.char_end, c.text
                 FROM quiz_sets s JOIN quiz_questions q ON q.quiz_id=s.quiz_id
                 JOIN quiz_citations qc ON qc.question_id=q.question_id
                 JOIN chunks c ON c.chunk_id=qc.citation_id
                 JOIN documents d ON d.document_id=c.document_id
                 WHERE s.user_id=? AND q.user_id=? AND qc.user_id=?
                   AND c.user_id=? AND d.user_id=?
                 GROUP BY s.topic_id, qc.citation_id
                 ORDER BY s.topic_id, c.document_id, c.page_number, c.chunk_index""",
                (user_id, user_id, user_id, user_id, user_id),
            ).fetchall()
            concepts = [
                {
                    "conceptId": row["concept_id"],
                    "name": row["canonical_name"],
                    "aliases": json.loads(row["aliases_json"]),
                }
                for row in connection.execute(
                    "SELECT concept_id, canonical_name, aliases_json FROM knowledge_concepts WHERE user_id=?",
                    (user_id,),
                )
            ]
            relationships = [
                {
                    "relationshipId": row["relationship_id"],
                    "sourceConceptId": row["source_concept_id"],
                    "targetConceptId": row["target_concept_id"],
                    "relation": row["relation"],
                    "status": row["status"],
                }
                for row in connection.execute(
                    """SELECT relationship_id, source_concept_id, target_concept_id, relation, status
                       FROM knowledge_relationships WHERE user_id=? ORDER BY relationship_id""",
                    (user_id,),
                )
            ]
        else:
            attempts = [
                dict(row)
                for row in connection.execute(
                    """SELECT a.rowid AS sequence, a.attempt_id AS attemptId,
                              a.question_id AS questionId,
                              a.outcome, a.created_at AS createdAt, q.prompt,
                              s.topic_id AS topicId, s.topic
                       FROM quiz_attempts a JOIN quiz_questions q ON q.question_id=a.question_id
                       JOIN quiz_sets s ON s.quiz_id=q.quiz_id
                       WHERE a.user_id IS NULL AND s.user_id IS NULL
                       ORDER BY a.created_at DESC, a.rowid DESC"""
                )
            ]
            sessions = [
                {
                    "sessionId": row["session_id"],
                    "topicId": row["topic_id"],
                    "topic": row["topic"],
                    "startedAt": row["started_at"],
                    "completedAt": row["completed_at"],
                }
                for row in connection.execute(
                    "SELECT * FROM study_sessions WHERE user_id IS NULL ORDER BY started_at DESC, rowid DESC"
                )
            ]
            reviews = [
                {
                    "questionId": row["question_id"],
                    "topicId": row["topic_id"],
                    "topic": row["topic"],
                    "outcome": row["last_outcome"],
                    "attemptedAt": row["attempted_at"],
                    "dueAt": row["due_at"],
                }
                for row in connection.execute(
                    "SELECT * FROM topic_review_items WHERE user_id IS NULL ORDER BY due_at, question_id"
                )
            ]
            source_rows = connection.execute(
                """SELECT s.topic_id, qc.citation_id AS chunk_id,
                          MIN(qc.retrieval_json) AS retrieval_json, c.document_id,
                          d.display_name, c.page_number, c.chunk_index, c.char_start,
                          c.char_end, c.text
                   FROM quiz_sets s JOIN quiz_questions q ON q.quiz_id=s.quiz_id
                   JOIN quiz_citations qc ON qc.question_id=q.question_id
                   JOIN chunks c ON c.chunk_id=qc.citation_id
                   JOIN documents d ON d.document_id=c.document_id
                   WHERE s.user_id IS NULL AND q.user_id IS NULL
                     AND qc.user_id IS NULL AND c.user_id IS NULL AND d.user_id IS NULL
                   GROUP BY s.topic_id, qc.citation_id
                   ORDER BY s.topic_id, c.document_id, c.page_number, c.chunk_index"""
            ).fetchall()
            concepts = [
                {
                    "conceptId": row["concept_id"],
                    "name": row["canonical_name"],
                    "aliases": json.loads(row["aliases_json"]),
                }
                for row in connection.execute(
                    "SELECT concept_id, canonical_name, aliases_json FROM knowledge_concepts WHERE user_id IS NULL"
                )
            ]
            relationships = [
                {
                    "relationshipId": row["relationship_id"],
                    "sourceConceptId": row["source_concept_id"],
                    "targetConceptId": row["target_concept_id"],
                    "relation": row["relation"],
                    "status": row["status"],
                }
                for row in connection.execute(
                    """SELECT relationship_id, source_concept_id, target_concept_id, relation, status
                           FROM knowledge_relationships WHERE user_id IS NULL ORDER BY relationship_id"""
                )
            ]

        topic_sources: dict[str, list[dict[str, Any]]] = {}
        for row in source_rows:
            citations = topic_sources.setdefault(row["topic_id"], [])
            if len(citations) >= 5:
                continue
            citations.append(
                {
                    "citationId": row["chunk_id"],
                    "documentId": row["document_id"],
                    "displayFilename": row["display_name"],
                    "pageNumber": row["page_number"],
                    "chunkIndex": row["chunk_index"],
                    "charStart": row["char_start"],
                    "charEnd": row["char_end"],
                    "text": row["text"],
                    "retrieval": json.loads(row["retrieval_json"]),
                }
            )
        topics = []
        seen_topic_ids = set()
        for review in reviews:
            if review["topicId"] not in seen_topic_ids:
                seen_topic_ids.add(review["topicId"])
                topics.append({"topicId": review["topicId"], "topic": review["topic"]})
        return {
            "attempts": attempts,
            "sessions": sessions,
            "reviews": reviews,
            "topicSources": topic_sources,
            "topics": topics,
            "concepts": concepts,
            "relationships": relationships,
        }


def get_or_create_study_coach_plan(
    database_path: Path,
    *,
    plan_date: str,
    recommendations: list[dict[str, Any]],
    updated_at: str,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Persist the deterministic daily plan once and preserve learner decisions."""

    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.executemany(
                """INSERT OR IGNORE INTO study_coach_recommendations(
                       recommendation_id, plan_date, topic_id, topic, title, priority,
                       reasons_json, graph_concept_id, related_concept_ids_json, status,
                       estimated_minutes, updated_at, user_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?)""",
                [
                    (
                        item["recommendationId"],
                        plan_date,
                        item["topicId"],
                        item["topic"],
                        item["title"],
                        item["priority"],
                        json.dumps(item["reasons"]),
                        item["graphConceptId"],
                        json.dumps(item["relatedConceptIds"]),
                        item["estimatedMinutes"],
                        updated_at,
                        user_id,
                    )
                    for item in recommendations
                ],
            )
        if user_id is not None:
            rows = connection.execute(
                """SELECT * FROM study_coach_recommendations WHERE plan_date=? AND user_id=?
                   ORDER BY priority DESC, topic COLLATE NOCASE, recommendation_id""",
                (plan_date, user_id),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT * FROM study_coach_recommendations WHERE plan_date=? AND user_id IS NULL
                   ORDER BY priority DESC, topic COLLATE NOCASE, recommendation_id""",
                (plan_date,),
            ).fetchall()
    return [
        {
            "recommendationId": row["recommendation_id"],
            "planDate": row["plan_date"],
            "topicId": row["topic_id"],
            "topic": row["topic"],
            "title": row["title"],
            "priority": row["priority"],
            "reasons": json.loads(row["reasons_json"]),
            "graphConceptId": row["graph_concept_id"],
            "relatedConceptIds": json.loads(row["related_concept_ids_json"]),
            "status": row["status"],
            "estimatedMinutes": row["estimated_minutes"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def update_study_coach_recommendation(
    database_path: Path,
    *,
    recommendation_id: str,
    action: str,
    updated_at: str,
    title: str | None = None,
    estimated_minutes: int | None = None,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    """Apply an explicit learner decision to one persisted plan item."""

    with closing(connect_database(database_path)) as connection:
        with connection:
            row = connection.execute(
                "SELECT * FROM study_coach_recommendations "
                "WHERE recommendation_id=? AND user_id IS ?",
                (recommendation_id, user_id),
            ).fetchone()
            if row is None:
                return None
            status = {
                "accept": "accepted",
                "edit": "accepted",
                "dismiss": "dismissed",
                "complete": "completed",
            }[action]
            connection.execute(
                """UPDATE study_coach_recommendations
                   SET status=?, title=?, estimated_minutes=?, updated_at=?
                   WHERE recommendation_id=? AND user_id IS ? """,
                (
                    status,
                    title if title is not None else row["title"],
                    estimated_minutes
                    if estimated_minutes is not None
                    else row["estimated_minutes"],
                    updated_at,
                    recommendation_id,
                    user_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM study_coach_recommendations "
                "WHERE recommendation_id=? AND user_id IS ?",
                (recommendation_id, user_id),
            ).fetchone()
    if updated is None:
        return None
    return {
        "recommendationId": updated["recommendation_id"],
        "planDate": updated["plan_date"],
        "topicId": updated["topic_id"],
        "topic": updated["topic"],
        "title": updated["title"],
        "priority": updated["priority"],
        "reasons": json.loads(updated["reasons_json"]),
        "graphConceptId": updated["graph_concept_id"],
        "relatedConceptIds": json.loads(updated["related_concept_ids_json"]),
        "status": updated["status"],
        "estimatedMinutes": updated["estimated_minutes"],
        "updatedAt": updated["updated_at"],
    }


def get_learning_progress(database_path: Path, user_id: str | None = None) -> dict[str, Any]:
    """Return local topic mastery based on each question's latest attempt and recent history."""
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            topic_rows = connection.execute(
                """
                WITH latest AS (
                    SELECT a.question_id, a.outcome,
                           row_number() OVER (
                               PARTITION BY a.question_id
                               ORDER BY a.created_at DESC, a.rowid DESC
                           ) AS rn
                    FROM quiz_attempts a
                    WHERE a.user_id = ?
                ), counts AS (
                    SELECT s.topic_id, s.topic, count(DISTINCT q.question_id) AS attempted_questions,
                           sum(CASE WHEN latest.outcome='correct' THEN 1 ELSE 0 END)
                               AS correct_questions,
                           sum(CASE WHEN latest.outcome='incorrect' THEN 1 ELSE 0 END)
                               AS incorrect_questions,
                           sum(CASE WHEN latest.outcome='ambiguous' THEN 1 ELSE 0 END)
                               AS ambiguous_questions
                    FROM quiz_sets s
                    JOIN quiz_questions q ON q.quiz_id=s.quiz_id
                    JOIN latest ON latest.question_id=q.question_id AND latest.rn=1
                    WHERE s.user_id = ? AND q.user_id = ?
                    GROUP BY s.topic_id, s.topic
                )
                SELECT *, round(1.0 * correct_questions / attempted_questions, 3) AS accuracy
                FROM counts ORDER BY accuracy ASC, topic COLLATE NOCASE
                """,
                (user_id, user_id, user_id),
            ).fetchall()
            attempt_total = int(
                connection.execute(
                    "SELECT count(*) FROM quiz_attempts WHERE user_id = ?", (user_id,)
                ).fetchone()[0]
            )
            recent_rows = connection.execute(
                """
                SELECT a.attempt_id, a.outcome, a.created_at, q.prompt, s.topic_id, s.topic
                FROM quiz_attempts a
                JOIN quiz_questions q ON q.question_id=a.question_id
                JOIN quiz_sets s ON s.quiz_id=q.quiz_id
                WHERE a.user_id = ? AND q.user_id = ? AND s.user_id = ?
                ORDER BY a.created_at DESC, a.rowid DESC LIMIT 100
                """,
                (user_id, user_id, user_id),
            ).fetchall()
        else:
            topic_rows = connection.execute(
                """
                WITH latest AS (
                    SELECT a.question_id, a.outcome,
                           row_number() OVER (
                               PARTITION BY a.question_id
                               ORDER BY a.created_at DESC, a.rowid DESC
                           ) AS rn
                    FROM quiz_attempts a
                    WHERE a.user_id IS NULL
                ), counts AS (
                    SELECT s.topic_id, s.topic, count(DISTINCT q.question_id) AS attempted_questions,
                           sum(CASE WHEN latest.outcome='correct' THEN 1 ELSE 0 END)
                               AS correct_questions,
                           sum(CASE WHEN latest.outcome='incorrect' THEN 1 ELSE 0 END)
                               AS incorrect_questions,
                           sum(CASE WHEN latest.outcome='ambiguous' THEN 1 ELSE 0 END)
                               AS ambiguous_questions
                    FROM quiz_sets s
                    JOIN quiz_questions q ON q.quiz_id=s.quiz_id
                    JOIN latest ON latest.question_id=q.question_id AND latest.rn=1
                    GROUP BY s.topic_id, s.topic
                )
                SELECT *, round(1.0 * correct_questions / attempted_questions, 3) AS accuracy
                FROM counts ORDER BY accuracy ASC, topic COLLATE NOCASE
                """
            ).fetchall()
            attempt_total = int(
                connection.execute(
                    "SELECT count(*) FROM quiz_attempts WHERE user_id IS NULL"
                ).fetchone()[0]
            )
            recent_rows = connection.execute(
                """
                SELECT a.attempt_id, a.outcome, a.created_at, q.prompt, s.topic_id, s.topic
                FROM quiz_attempts a
                JOIN quiz_questions q ON q.question_id=a.question_id
                JOIN quiz_sets s ON s.quiz_id=q.quiz_id
                WHERE a.user_id IS NULL AND q.user_id IS NULL AND s.user_id IS NULL
                ORDER BY a.created_at DESC, a.rowid DESC LIMIT 100
                """
            ).fetchall()
    topics = [
        {
            "topicId": row["topic_id"],
            "topic": row["topic"],
            "attemptedQuestions": row["attempted_questions"],
            "correctQuestions": row["correct_questions"],
            "incorrectQuestions": row["incorrect_questions"],
            "ambiguousQuestions": row["ambiguous_questions"],
            "accuracy": row["accuracy"],
            "needsReview": row["incorrect_questions"] > 0 or row["ambiguous_questions"] > 0,
        }
        for row in topic_rows
    ]
    return {
        "topics": topics,
        "totalAttempts": attempt_total,
        "recentAttempts": [dict(row) for row in recent_rows],
    }


def fetch_chunk_batch(
    database_path: Path,
    *,
    after_chunk_id: str | None,
    limit: int,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read a bounded chunk batch for incremental embedding indexing."""
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            rows = connection.execute(
                """
                SELECT c.chunk_id, c.text, e.model_id, e.model_revision, e.config_hash,
                       e.dimensions, e.vector
                FROM chunks AS c
                LEFT JOIN chunk_embeddings AS e ON e.chunk_id = c.chunk_id
                    AND e.user_id IS c.user_id
                WHERE (? IS NULL OR c.chunk_id > ?) AND c.user_id = ?
                ORDER BY c.chunk_id ASC
                LIMIT ?
                """,
                (after_chunk_id, after_chunk_id, user_id, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT c.chunk_id, c.text, e.model_id, e.model_revision, e.config_hash,
                       e.dimensions, e.vector
                FROM chunks AS c
                LEFT JOIN chunk_embeddings AS e ON e.chunk_id = c.chunk_id
                    AND e.user_id IS NULL
                WHERE (? IS NULL OR c.chunk_id > ?) AND c.user_id IS NULL
                ORDER BY c.chunk_id ASC
                LIMIT ?
                """,
                (after_chunk_id, after_chunk_id, limit),
            ).fetchall()
    return [dict(row) for row in rows]


def store_chunk_embeddings(
    database_path: Path,
    entries: list[dict[str, Any]],
    user_id: str | None = None,
) -> None:
    """Atomically upsert a batch of embeddings without touching source chunks or FTS."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            for entry in entries:
                if user_id is None:
                    chunk = connection.execute(
                        "SELECT 1 FROM chunks WHERE chunk_id=? AND user_id IS NULL",
                        (entry["chunk_id"],),
                    ).fetchone()
                else:
                    chunk = connection.execute(
                        "SELECT 1 FROM chunks WHERE chunk_id=? AND user_id=?",
                        (entry["chunk_id"], user_id),
                    ).fetchone()
                if chunk is None:
                    raise ValueError("Embedding chunk is not owned by the requested user")
            connection.executemany(
                """
                INSERT INTO chunk_embeddings(
                    chunk_id, model_id, model_revision, config_hash, dimensions, vector,
                    user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    model_id=excluded.model_id,
                    model_revision=excluded.model_revision,
                    config_hash=excluded.config_hash,
                    dimensions=excluded.dimensions,
                    vector=excluded.vector,
                    user_id=excluded.user_id
                """,
                [
                    (
                        entry["chunk_id"],
                        entry["model_id"],
                        entry["model_revision"],
                        entry["config_hash"],
                        entry["dimensions"],
                        entry["vector"],
                        user_id,
                    )
                    for entry in entries
                ],
            )


def is_valid_normalized_vector_blob(vector_bytes: bytes | None, dimensions: int) -> bool:
    """Return whether a stored vector has the expected float32 shape and unit norm."""
    if not isinstance(vector_bytes, (bytes, bytearray, memoryview)):
        return False
    try:
        if len(vector_bytes) != dimensions * 4:
            return False
        vector = struct.unpack(f"<{dimensions}f", vector_bytes)
    except (TypeError, struct.error):
        return False
    norm = math.hypot(*vector)
    return all(math.isfinite(value) for value in vector) and math.isclose(norm, 1.0, abs_tol=1e-3)


def embedding_index_counts(
    database_path: Path,
    *,
    config_hash: str,
    dimensions: int,
    user_id: str | None = None,
) -> dict[str, int]:
    """Return total, current, stale, and missing vector counts for one config."""
    with closing(connect_database(database_path)) as connection:
        if user_id is not None:
            total = int(
                connection.execute(
                    "SELECT count(*) FROM chunks WHERE user_id = ?", (user_id,)
                ).fetchone()[0]
            )
            vectors = connection.execute(
                "SELECT config_hash, dimensions, vector FROM chunk_embeddings WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        else:
            total = int(
                connection.execute("SELECT count(*) FROM chunks WHERE user_id IS NULL").fetchone()[
                    0
                ]
            )
            vectors = connection.execute(
                "SELECT config_hash, dimensions, vector FROM chunk_embeddings WHERE user_id IS NULL"
            ).fetchall()
    indexed = len(vectors)
    current = 0
    for row in vectors:
        vector_bytes = row["vector"]
        if (
            row["config_hash"] != config_hash
            or row["dimensions"] != dimensions
            or not is_valid_normalized_vector_blob(vector_bytes, dimensions)
        ):
            continue
        current += 1
    return {
        "total_chunks": total,
        "indexed_chunks": indexed,
        "current_chunks": current,
        "stale_chunks": indexed - current,
        "missing_chunks": total - current,
    }


def search_semantic_chunks(
    database_path: Path,
    *,
    config_hash: str,
    dimensions: int,
    query_vector: tuple[float, ...],
    limit: int,
    document_ids: list[str] | None = None,
    user_id: str | None = None,
) -> list[dict[str, Any]]:
    """Exact cosine search over normalized vectors.

    Two-phase execution: phase 1 fetches only chunk ids and vectors and keeps a
    bounded heap of the top-k candidates.  Phase 2 fetches full chunk details
    only for the selected candidates, avoiding materializing every row's text
    and vector in Python for large indexes.
    """
    import heapq

    detail_sql = """
        SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
               c.chunk_index, c.char_start, c.char_end, c.text
        FROM chunks AS c
        JOIN documents AS d ON d.document_id = c.document_id
        WHERE c.chunk_id IN ({placeholders})
    """

    with closing(connect_database(database_path)) as connection:
        # Phase 1: compute top-k similarities over id+vector pairs only.
        heap: list[tuple[float, str]] = []
        params = [config_hash, dimensions, dimensions * 4]
        vector_sql = """
            SELECT e.chunk_id, e.vector
            FROM chunk_embeddings AS e
            JOIN chunks AS c ON c.chunk_id = e.chunk_id
            WHERE e.config_hash = ? AND e.dimensions = ? AND length(e.vector) = ?
        """
        if document_ids is not None:
            if not document_ids:
                return []
            placeholders = ",".join("?" for _ in document_ids)
            vector_sql += f" AND c.document_id IN ({placeholders})"
            params.extend(document_ids)
        if user_id is not None:
            vector_sql += " AND c.user_id = ? AND e.user_id = ?"
            params.extend((user_id, user_id))
        else:
            vector_sql += " AND c.user_id IS NULL AND e.user_id IS NULL"

        for chunk_id, vector_bytes in connection.execute(vector_sql, params):
            if not is_valid_normalized_vector_blob(vector_bytes, dimensions):
                continue
            vector = struct.unpack(f"<{dimensions}f", vector_bytes)
            similarity = math.fsum(a * b for a, b in zip(query_vector, vector, strict=True))
            key = str(chunk_id)
            heapq.heappush(heap, (similarity, key))
            if len(heap) > limit:
                heapq.heappop(heap)

        top_ids = [key for _, key in sorted(heap, key=lambda v: (-v[0], v[1]))]
        if not top_ids:
            return []

        # Phase 2: fetch full details only for the selected chunk ids.
        placeholders = ",".join("?" for _ in top_ids)
        detail_sql = detail_sql.format(placeholders=placeholders)
        if user_id is not None:
            rows = connection.execute(
                detail_sql + " AND c.user_id = ? AND d.user_id = ?",
                [*top_ids, user_id, user_id],
            ).fetchall()
        else:
            rows = connection.execute(
                detail_sql + " AND c.user_id IS NULL AND d.user_id IS NULL", top_ids
            ).fetchall()

    # Attach similarities and return in ranked order.
    similarity_by_id = {key: sim for sim, key in heap}
    results = []
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        item["similarity"] = similarity_by_id[str(item["chunk_id"])]
        results.append(item)
    results.sort(key=lambda value: (-value["similarity"], str(value["chunk_id"])))
    return results


# ---------------------------------------------------------------------------
# M34: Personalized learning paths and adaptive difficulty
# ---------------------------------------------------------------------------


def _difficulty_level_from_mastery(mastery: float | None) -> str:
    """Map a mastery score to a deterministic difficulty level.

    Mastery is None (no history) or below 0.40 -> foundational.
    0.40 <= mastery < 0.60 -> building.
    0.60 <= mastery < 0.80 -> practice.
    mastery >= 0.80 -> advanced.
    """
    if mastery is None or mastery < 0.40:
        return "foundational"
    if mastery < 0.60:
        return "building"
    if mastery < 0.80:
        return "practice"
    return "advanced"


def create_learning_path(
    database_path: Path,
    *,
    user_id: str,
    title: str,
    description: str,
) -> dict[str, Any]:
    """Create a new learning path for ``user_id``.

    Returns the stored path record.  Title uniqueness is not enforced; callers
    may create multiple paths with the same title.
    """
    path_id = f"path_{secrets.token_urlsafe(12)}"
    now = datetime.now(UTC).isoformat()
    title = title.strip()
    description = description.strip()
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO learning_paths(
                    path_id, user_id, title, description, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    path_id,
                    user_id,
                    title,
                    description,
                    "active",
                    now,
                    now,
                ),
            )
    return get_learning_path(database_path, path_id)


def get_learning_path(database_path: Path, path_id: str) -> dict[str, Any] | None:
    """Return one learning path, or None if not found."""
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            "SELECT path_id, user_id, title, description, status, created_at, updated_at "
            "FROM learning_paths WHERE path_id = ?",
            (path_id,),
        ).fetchone()
        if row is None:
            return None
    return {
        "pathId": row["path_id"],
        "userId": row["user_id"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def list_learning_paths(
    database_path: Path,
    *,
    user_id: str,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List learning paths for ``user_id`` with optional status filter."""
    query = """
        SELECT path_id, user_id, title, description, status, created_at, updated_at
        FROM learning_paths
        WHERE user_id = ?
    """
    params: list[Any] = [user_id]
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY updated_at DESC, created_at DESC"
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(query, params).fetchall()
    return [
        {
            "pathId": row["path_id"],
            "userId": row["user_id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def update_learning_path(
    database_path: Path,
    *,
    path_id: str,
    user_id: str | None = None,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Update a learning path, enforcing ownership when ``user_id`` is given."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            existing = connection.execute(
                "SELECT path_id, user_id FROM learning_paths WHERE path_id = ?",
                (path_id,),
            ).fetchone()
            if existing is None:
                return None
            if user_id is not None and existing["user_id"] != user_id:
                raise PermissionError("You do not own this learning path.")

            updates: list[str] = []
            values: list[Any] = []
            if title is not None:
                updates.append("title = ?")
                values.append(title.strip())
            if description is not None:
                updates.append("description = ?")
                values.append(description.strip())
            if status is not None:
                updates.append("status = ?")
                values.append(status)

            if not updates:
                return get_learning_path(database_path, path_id)

            updates.append("updated_at = ?")
            values.append(datetime.now(UTC).isoformat())
            values.append(path_id)
            connection.execute(
                f"UPDATE learning_paths SET {', '.join(updates)} WHERE path_id = ?",
                tuple(values),
            )

    return get_learning_path(database_path, path_id)


def delete_learning_path(
    database_path: Path,
    *,
    path_id: str,
    user_id: str | None = None,
) -> bool:
    """Delete a learning path and its objectives.  Returns True if deleted."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            existing = connection.execute(
                "SELECT user_id FROM learning_paths WHERE path_id = ?",
                (path_id,),
            ).fetchone()
            if existing is None:
                return False
            if user_id is not None and existing["user_id"] != user_id:
                raise PermissionError("You do not own this learning path.")
            connection.execute("DELETE FROM learning_paths WHERE path_id = ?", (path_id,))
    return True


def create_learning_objective(
    database_path: Path,
    *,
    path_id: str,
    title: str,
    description: str,
    order_index: int,
    source_document_id: str | None = None,
    source_chunk_id: str | None = None,
    source_passage: str | None = None,
    prerequisite_objective_ids: list[str] | None = None,
    rationale: str | None = None,
    difficulty_level: str = "foundational",
) -> dict[str, Any] | None:
    """Create a learning objective under ``path_id``.

    Returns the stored objective record, or None if the path does not exist.
    Prerequisite objective IDs must belong to the same path.
    """
    objective_id = f"obj_{secrets.token_urlsafe(12)}"
    now = datetime.now(UTC).isoformat()
    title = title.strip()
    description = description.strip()
    prerequisite_objective_ids = prerequisite_objective_ids or []
    with closing(connect_database(database_path)) as connection:
        with connection:
            path = connection.execute(
                "SELECT 1 FROM learning_paths WHERE path_id = ?",
                (path_id,),
            ).fetchone()
            if path is None:
                return None
            # Verify prerequisite objectives belong to the same path.
            if prerequisite_objective_ids:
                placeholders = ",".join("?" for _ in prerequisite_objective_ids)
                prereqs = connection.execute(
                    f"SELECT objective_id FROM learning_objectives "
                    f"WHERE objective_id IN ({placeholders}) AND path_id = ?",
                    [*prerequisite_objective_ids, path_id],
                ).fetchall()
                found = {row["objective_id"] for row in prereqs}
                missing = set(prerequisite_objective_ids) - found
                if missing:
                    raise ValueError(
                        "Prerequisite objectives not found in this path: "
                        + ", ".join(sorted(missing))
                    )
            connection.execute(
                """
                INSERT INTO learning_objectives(
                    objective_id, path_id, title, description, status, order_index,
                    source_document_id, source_chunk_id, source_passage,
                    prerequisite_objective_ids, rationale, difficulty_level,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    objective_id,
                    path_id,
                    title,
                    description,
                    "not_started",
                    order_index,
                    source_document_id,
                    source_chunk_id,
                    source_passage,
                    json.dumps(prerequisite_objective_ids, separators=(",", ":")),
                    rationale,
                    difficulty_level,
                    now,
                    now,
                ),
            )
    return get_learning_objective(database_path, objective_id)


def get_learning_objective(database_path: Path, objective_id: str) -> dict[str, Any] | None:
    """Return one learning objective with its evidence links and activity links."""
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            """
            SELECT objective_id, path_id, title, description, status, order_index,
                   source_document_id, source_chunk_id, source_passage,
                   prerequisite_objective_ids, rationale, difficulty_level,
                   created_at, updated_at
            FROM learning_objectives WHERE objective_id = ?
            """,
            (objective_id,),
        ).fetchone()
        if row is None:
            return None
        evidence = connection.execute(
            "SELECT citation_id, position FROM objective_evidence_links "
            "WHERE objective_id = ? ORDER BY position",
            (objective_id,),
        ).fetchall()
        activities = connection.execute(
            "SELECT activity_type, activity_id FROM objective_activity_links "
            "WHERE objective_id = ?",
            (objective_id,),
        ).fetchall()
    return {
        "objectiveId": row["objective_id"],
        "pathId": row["path_id"],
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "orderIndex": row["order_index"],
        "sourceDocumentId": row["source_document_id"],
        "sourceChunkId": row["source_chunk_id"],
        "sourcePassage": row["source_passage"],
        "prerequisiteObjectiveIds": json.loads(row["prerequisite_objective_ids"] or "[]"),
        "rationale": row["rationale"],
        "difficultyLevel": row["difficulty_level"],
        "evidenceIds": [e["citation_id"] for e in evidence],
        "activityLinks": [
            {"activityType": a["activity_type"], "activityId": a["activity_id"]} for a in activities
        ],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def list_learning_objectives(
    database_path: Path,
    *,
    path_id: str,
) -> list[dict[str, Any]]:
    """List objectives for a path ordered by order_index, then created_at."""
    with closing(connect_database(database_path)) as connection:
        path = connection.execute(
            "SELECT 1 FROM learning_paths WHERE path_id = ?",
            (path_id,),
        ).fetchone()
        if path is None:
            return []
        rows = connection.execute(
            """
            SELECT objective_id, path_id, title, description, status, order_index,
                   source_document_id, source_chunk_id, source_passage,
                   prerequisite_objective_ids, rationale, difficulty_level,
                   created_at, updated_at
            FROM learning_objectives
            WHERE path_id = ?
            ORDER BY order_index ASC, created_at ASC
            """,
            (path_id,),
        ).fetchall()
        # Fetch all evidence links while connection is open.
        objective_ids = [row["objective_id"] for row in rows]
        evidence_map: dict[str, list[str]] = {}
        activities_map: dict[str, list[dict[str, str]]] = {}
        if objective_ids:
            placeholders = ",".join("?" for _ in objective_ids)
            evidence_rows = connection.execute(
                f"SELECT objective_id, citation_id FROM objective_evidence_links "
                f"WHERE objective_id IN ({placeholders}) "
                f"ORDER BY objective_id, position",
                objective_ids,
            ).fetchall()
            for er in evidence_rows:
                evidence_map.setdefault(er["objective_id"], []).append(er["citation_id"])
            activity_rows = connection.execute(
                f"SELECT objective_id, activity_type, activity_id FROM objective_activity_links "
                f"WHERE objective_id IN ({placeholders}) "
                f"ORDER BY objective_id, activity_type, activity_id",
                objective_ids,
            ).fetchall()
            for ar in activity_rows:
                activities_map.setdefault(ar["objective_id"], []).append(
                    {"activityType": ar["activity_type"], "activityId": ar["activity_id"]}
                )
        results = []
        for row in rows:
            evidence_map_for_row = evidence_map.get(row["objective_id"], [])
            activities_map_for_row = activities_map.get(row["objective_id"], [])
            results.append(
                {
                    "objectiveId": row["objective_id"],
                    "pathId": row["path_id"],
                    "title": row["title"],
                    "description": row["description"],
                    "status": row["status"],
                    "orderIndex": row["order_index"],
                    "sourceDocumentId": row["source_document_id"],
                    "sourceChunkId": row["source_chunk_id"],
                    "sourcePassage": row["source_passage"],
                    "prerequisiteObjectiveIds": json.loads(
                        row["prerequisite_objective_ids"] or "[]"
                    ),
                    "rationale": row["rationale"],
                    "difficultyLevel": row["difficulty_level"],
                    "evidenceIds": evidence_map_for_row,
                    "activityLinks": activities_map_for_row,
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                }
            )
        return results


def update_learning_objective(
    database_path: Path,
    *,
    objective_id: str,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    order_index: int | None = None,
    rationale: str | None = None,
    difficulty_level: str | None = None,
) -> dict[str, Any] | None:
    """Update a learning objective's mutable fields."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            existing = connection.execute(
                "SELECT objective_id FROM learning_objectives WHERE objective_id = ?",
                (objective_id,),
            ).fetchone()
            if existing is None:
                return None
            updates: list[str] = []
            values: list[Any] = []
            if title is not None:
                updates.append("title = ?")
                values.append(title.strip())
            if description is not None:
                updates.append("description = ?")
                values.append(description.strip())
            if status is not None:
                updates.append("status = ?")
                values.append(status)
            if order_index is not None:
                updates.append("order_index = ?")
                values.append(order_index)
            if rationale is not None:
                updates.append("rationale = ?")
                values.append(rationale.strip() if rationale else None)
            if difficulty_level is not None:
                updates.append("difficulty_level = ?")
                values.append(difficulty_level)

            if not updates:
                return get_learning_objective(database_path, objective_id)

            updates.append("updated_at = ?")
            values.append(datetime.now(UTC).isoformat())
            values.append(objective_id)
            connection.execute(
                f"UPDATE learning_objectives SET {', '.join(updates)} WHERE objective_id = ?",
                tuple(values),
            )

    return get_learning_objective(database_path, objective_id)


def delete_learning_objective(
    database_path: Path,
    *,
    objective_id: str,
) -> bool:
    """Delete a learning objective and its evidence/activity links."""
    with closing(connect_database(database_path)) as connection:
        with connection:
            existing = connection.execute(
                "SELECT 1 FROM learning_objectives WHERE objective_id = ?",
                (objective_id,),
            ).fetchone()
            if existing is None:
                return False
            connection.execute(
                "DELETE FROM learning_objectives WHERE objective_id = ?",
                (objective_id,),
            )
    return True


def link_objective_evidence(
    database_path: Path,
    *,
    objective_id: str,
    citation_ids: list[str],
) -> dict[str, Any] | None:
    """Replace the evidence links for an objective with ``citation_ids``.

    Citations are ordered as supplied.  Returns the updated objective or None.
    """
    with closing(connect_database(database_path)) as connection:
        with connection:
            exists = connection.execute(
                "SELECT 1 FROM learning_objectives WHERE objective_id = ?",
                (objective_id,),
            ).fetchone()
            if exists is None:
                return None
            connection.execute(
                "DELETE FROM objective_evidence_links WHERE objective_id = ?",
                (objective_id,),
            )
            for position, citation_id in enumerate(citation_ids):
                connection.execute(
                    """
                    INSERT INTO objective_evidence_links(objective_id, citation_id, position)
                    VALUES (?, ?, ?)
                    """,
                    (objective_id, citation_id, position),
                )
    return get_learning_objective(database_path, objective_id)


def link_objective_activity(
    database_path: Path,
    *,
    objective_id: str,
    activity_type: str,
    activity_id: str,
) -> dict[str, Any] | None:
    """Link an objective to a quiz attempt or study session activity."""
    if activity_type not in {"quiz_attempt", "study_session"}:
        raise ValueError("activity_type must be 'quiz_attempt' or 'study_session'")
    with closing(connect_database(database_path)) as connection:
        with connection:
            exists = connection.execute(
                "SELECT 1 FROM learning_objectives WHERE objective_id = ?",
                (objective_id,),
            ).fetchone()
            if exists is None:
                return None
            connection.execute(
                """
                INSERT OR IGNORE INTO objective_activity_links(
                    objective_id, activity_type, activity_id
                ) VALUES (?, ?, ?)
                """,
                (objective_id, activity_type, activity_id),
            )
    return get_learning_objective(database_path, objective_id)


def compute_adaptive_recommendations(
    database_path: Path,
    *,
    user_id: str,
    now: datetime,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Compute deterministic adaptive learning recommendations for ``user_id``.

    Recommendations are derived exclusively from persisted learner data:
    quiz attempts, study sessions, topic reviews, knowledge graph concepts,
    and document/chunk evidence.  No external model or AI service is used.

    Each recommendation includes:
        - topicId / topic: the topic to work on
        - difficultyLevel: foundational / building / practice / advanced
        - priorityScore: higher = more urgent
        - reasons: human-readable, data-grounded explanations
        - citations: current source passages for the topic
        - citationsCount: number of available source passages
        - activitySuggestion: the type of activity to do next
        - measuredFields: which inputs come from stored data
        - estimatedFields: which inputs are derived heuristics
    """
    current = now.astimezone(UTC)
    today = current.date().isoformat()

    attempts = _get_user_quiz_attempts(database_path, user_id)
    sessions = _get_user_study_sessions(database_path, user_id)
    reviews = _get_user_topic_reviews(database_path, user_id)
    topic_sources = _get_user_topic_sources(database_path, user_id)
    concepts = list_knowledge_concepts(database_path, user_id=user_id)

    analytics = build_analytics(
        attempts=attempts,
        sessions=sessions,
        reviews=reviews,
        topic_sources=topic_sources,
        concepts=concepts,
        now=current,
    )

    # Build a map of latest outcome per topic.
    latest_by_topic: dict[str, str] = {}
    for attempt in attempts:
        topic_id = attempt["topicId"]
        previous = latest_by_topic.get(topic_id)
        attempted_at = attempt["createdAt"]
        previous_at = previous["createdAt"] if previous else None
        if previous is None or attempted_at > previous_at:
            latest_by_topic[topic_id] = attempt

    # Count recent activity (last 30 days).
    thirty_days_ago = current - timedelta(days=30)
    recent_activity_by_topic: dict[str, int] = {}
    for attempt in attempts:
        if _parse_datetime(attempt["createdAt"]) >= thirty_days_ago:
            recent_activity_by_topic[attempt["topicId"]] = (
                recent_activity_by_topic.get(attempt["topicId"], 0) + 1
            )
    for session in sessions:
        if session["completedAt"] and _parse_datetime(session["completedAt"]) >= thirty_days_ago:
            recent_activity_by_topic[session["topicId"]] = (
                recent_activity_by_topic.get(session["topicId"], 0) + 1
            )

    sessions_today_by_topic: dict[str, int] = {}
    for session in sessions:
        completed_at = session.get("completedAt")
        if completed_at and completed_at[:10] == today:
            sessions_today_by_topic[session["topicId"]] = (
                sessions_today_by_topic.get(session["topicId"], 0) + 1
            )

    recommendations: list[dict[str, Any]] = []
    for topic in analytics.get("topics", []):
        topic_id = topic["topicId"]
        topic_name = topic["topic"]
        citations = topic_sources.get(topic_id, [])
        # Skip topics without any source passages — cannot ground activities.
        if not citations:
            continue

        mastery = topic.get("mastery")
        weak = topic.get("weak", False)
        overdue = int(topic.get("overdueReviewCount", 0))
        incorrect = int(topic.get("incorrectQuestions", 0))
        ambiguous = int(topic.get("ambiguousQuestions", 0))
        recent_count = recent_activity_by_topic.get(topic_id, 0)
        today_sessions = sessions_today_by_topic.get(topic_id, 0)

        score = 0
        reasons: list[str] = []
        measured: list[str] = []
        estimated: list[str] = []

        # Overdue reviews — strongest signal, fully measured.
        if overdue:
            score += overdue * 100
            reasons.append(f"{overdue} review question(s) for this topic are due or overdue.")
            measured.append("overdueReviewCount")

        # Weak topic — measured via mastery calculation.
        if weak and mastery is not None:
            score += 60
            reasons.append(
                f"Recency-weighted quiz score is {mastery:.0%}, below the "
                f"{0.60:.0%} review-prompt threshold."
            )
            measured.append("mastery")

        # Recent incorrect/ambiguous outcomes — measured.
        if incorrect:
            score += min(incorrect, 3) * 15
            reasons.append(f"Your latest results include {incorrect} incorrect question(s).")
            measured.append("incorrectQuestions")
        if ambiguous:
            score += min(ambiguous, 3) * 10
            reasons.append(f"Your latest results include {ambiguous} uncertain question(s).")
            measured.append("ambiguousQuestions")

        # Recency — measured heuristic.
        if recent_count == 0:
            score += 20
            reasons.append("No recent activity on this topic (30 days).")
            measured.append("recentActivityCount")

        # High mastery — de-prioritize, suggest advanced practice.
        if mastery is not None and mastery >= 0.80:
            score -= 20
            reasons.append(f"Strong mastery ({mastery:.0%}); consider advanced practice.")
            measured.append("mastery")

        # Today's completed sessions — de-prioritize.
        if today_sessions:
            score -= min(today_sessions, 2) * 10
            reasons.append(f"You completed {today_sessions} study session(s) on this topic today.")
            measured.append("sessionsToday")

        # Difficulty level from mastery — derived heuristic.
        difficulty = _difficulty_level_from_mastery(mastery)
        estimated.append("difficultyLevel")

        if mastery is None:
            reasons.append("No quiz history yet; starting with foundational practice.")
            estimated.append("mastery")

        if not reasons:
            reasons.append("You have stored quiz and source material for this topic.")

        recommendations.append(
            {
                "topicId": topic_id,
                "topic": topic_name,
                "difficultyLevel": difficulty,
                "priorityScore": round(score, 2),
                "reasons": reasons,
                "citations": citations,
                "citationsCount": len(citations),
                "activitySuggestion": _activity_suggestion(difficulty, weak, overdue),
                "mastery": mastery,
                "measuredFields": measured,
                "estimatedFields": estimated,
            }
        )

    recommendations.sort(key=lambda item: (-item["priorityScore"], item["topic"].casefold()))
    return recommendations[: max(0, limit)]


def _activity_suggestion(
    difficulty: str,
    weak: bool,
    overdue: int,
) -> str:
    """Return a deterministic activity suggestion from difficulty and state."""
    if overdue > 0:
        return "review_overdue"
    if weak:
        return "foundational_practice"
    if difficulty == "advanced":
        return "challenge_quiz"
    if difficulty == "practice":
        return "mixed_quiz"
    if difficulty == "building":
        return "targeted_quiz"
    return "introductory_quiz"


def _get_user_quiz_attempts(database_path: Path, user_id: str) -> list[dict[str, Any]]:
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT a.attempt_id, a.question_id, a.outcome, a.created_at,
                   qs.topic_id, qs.topic, q.position, q.prompt
            FROM quiz_attempts AS a
            JOIN quiz_questions AS q ON q.question_id = a.question_id
            JOIN quiz_sets AS qs ON qs.quiz_id = q.quiz_id
            WHERE a.user_id = ?
            ORDER BY a.created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "attemptId": row["attempt_id"],
            "questionId": row["question_id"],
            "outcome": row["outcome"],
            "createdAt": row["created_at"],
            "topicId": row["topic_id"],
            "topic": row["topic"],
            "position": row["position"],
            "prompt": row["prompt"],
        }
        for row in rows
    ]


def _get_user_study_sessions(database_path: Path, user_id: str) -> list[dict[str, Any]]:
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT session_id, topic_id, topic, started_at, completed_at
            FROM study_sessions
            WHERE user_id = ?
            ORDER BY started_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "sessionId": row["session_id"],
            "topicId": row["topic_id"],
            "topic": row["topic"],
            "startedAt": row["started_at"],
            "completedAt": row["completed_at"],
        }
        for row in rows
    ]


def _get_user_topic_reviews(database_path: Path, user_id: str) -> list[dict[str, Any]]:
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT r.question_id, r.topic_id, r.topic, r.last_outcome, r.attempted_at,
                   r.due_at
            FROM topic_review_items AS r
            JOIN quiz_questions AS q ON q.question_id = r.question_id
            WHERE q.user_id = ?
            ORDER BY r.due_at ASC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "questionId": row["question_id"],
            "topicId": row["topic_id"],
            "topic": row["topic"],
            "lastOutcome": row["last_outcome"],
            "attemptedAt": row["attempted_at"],
            "dueAt": row["due_at"],
            "status": "completed",
        }
        for row in rows
    ]


def _get_user_topic_sources(database_path: Path, user_id: str) -> dict[str, list[dict[str, Any]]]:
    """Return topicId -> list of citation records from stored quiz citations."""
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT qs.topic_id, c.citation_id, ch.document_id,
                   d.display_name, ch.page_number, ch.chunk_index, ch.char_start,
                   ch.char_end, ch.text, c.retrieval_json
            FROM quiz_citations AS c
            JOIN quiz_questions AS q ON q.question_id = c.question_id
            JOIN quiz_sets AS qs ON qs.quiz_id = q.quiz_id
            JOIN chunks AS ch ON ch.chunk_id = c.citation_id
            JOIN documents AS d ON d.document_id = ch.document_id
            WHERE q.user_id = ?
            ORDER BY qs.topic_id, c.position
            """,
            (user_id,),
        ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        topic_id = row["topic_id"]
        retrieval_json = row["retrieval_json"]
        try:
            retrieval = json.loads(retrieval_json) if retrieval_json else {}
        except (json.JSONDecodeError, TypeError):
            retrieval = {}
        result.setdefault(topic_id, []).append(
            {
                "citationId": row["citation_id"],
                "documentId": row["document_id"],
                "displayFilename": row["display_name"],
                "pageNumber": row["page_number"],
                "chunkIndex": row["chunk_index"],
                "charStart": row["char_start"],
                "charEnd": row["char_end"],
                "text": row["text"],
                "retrieval": retrieval,
            }
        )
    return result


# ---------------------------------------------------------------------------
# M36: Shared study spaces (private collaboration workspaces).
# ---------------------------------------------------------------------------

def _workspace_exists(database_path: Path, space_id: str) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        return connection.execute(
            "SELECT space_id, owner_id, title, description, learning_objective, status, created_at, updated_at "
            "FROM study_spaces WHERE space_id = ?",
            (space_id,),
        ).fetchone()


def _workspace_member_row(database_path: Path, space_id: str, user_id: str) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            "SELECT space_id, user_id, role, joined_at FROM workspace_members WHERE space_id = ? AND user_id = ?",
            (space_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def _is_workspace_member(database_path: Path, space_id: str, user_id: str) -> bool:
    return _workspace_member_row(database_path, space_id, user_id) is not None


def _is_workspace_owner(database_path: Path, space_id: str, user_id: str) -> bool:
    row = _workspace_member_row(database_path, space_id, user_id)
    return row is not None and row["role"] == "owner"


def create_study_space(
    database_path: Path,
    *,
    user_id: str,
    title: str,
    description: str,
    learning_objective: str | None = None,
) -> dict[str, Any]:
    """Create a study space owned by ``user_id`` and register them as owner member."""

    space_id = f"space_{secrets.token_urlsafe(12)}"
    now = datetime.now(UTC).isoformat()
    title = title.strip()
    description = description.strip()
    learning_objective = (learning_objective or "").strip() or None
    if learning_objective is not None and len(learning_objective) > 1000:
        raise ValueError("Learning objective must not exceed 1000 characters.")
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO study_spaces(
                    space_id, owner_id, title, description, learning_objective, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (space_id, user_id, title, description, learning_objective, now, now),
            )
            connection.execute(
                """
                INSERT INTO workspace_members(space_id, user_id, role, joined_at)
                VALUES (?, ?, 'owner', ?)
                """,
                (space_id, user_id, now),
            )
    return {
        "spaceId": space_id,
        "ownerId": user_id,
        "title": title,
        "description": description,
        "learningObjective": learning_objective,
        "status": "active",
        "memberCount": 1,
        "createdAt": now,
        "updatedAt": now,
        "userRole": "owner",
    }


def list_study_spaces(database_path: Path, *, user_id: str, status: str | None = None) -> list[dict[str, Any]]:
    """List study spaces the user owns or is a member of, newest first."""
    query = """
        SELECT s.space_id, s.owner_id, s.title, s.description, s.learning_objective,
               s.status, s.created_at, s.updated_at,
               m.role AS user_role,
               (SELECT COUNT(*) FROM workspace_members wm WHERE wm.space_id = s.space_id) AS member_count
        FROM study_spaces AS s
        JOIN workspace_members AS m ON m.space_id = s.space_id AND m.user_id = ?
    """
    params: list[Any] = [user_id]
    if status is not None:
        query += " AND s.status = ?"
        params.append(status)
    query += " ORDER BY s.updated_at DESC, s.created_at DESC"
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(query, params).fetchall()
    return [_space_row_to_public(row) for row in rows]


def _space_row_to_public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "spaceId": row["space_id"],
        "ownerId": row["owner_id"],
        "title": row["title"],
        "description": row["description"],
        "learningObjective": row["learning_objective"],
        "status": row["status"],
        "memberCount": row["member_count"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "userRole": row["user_role"],
    }


def get_study_space(database_path: Path, *, space_id: str, user_id: str) -> dict[str, Any] | None:
    """Return a study space if the user is a member; None otherwise."""
    row = _workspace_exists(database_path, space_id)
    if row is None:
        return None
    if not _is_workspace_member(database_path, space_id, user_id):
        return None
    return {
        "spaceId": row["space_id"],
        "ownerId": row["owner_id"],
        "title": row["title"],
        "description": row["description"],
        "learningObjective": row["learning_objective"],
        "status": row["status"],
        "memberCount": 0,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "userRole": _workspace_member_row(database_path, space_id, user_id)["role"],
    }


def update_study_space(
    database_path: Path,
    *,
    space_id: str,
    user_id: str,
    title: str | None = None,
    description: str | None = None,
    learning_objective: str | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Update mutable study-space fields. Only the owner may update."""
    if not _is_workspace_owner(database_path, space_id, user_id):
        return None
    if _workspace_exists(database_path, space_id) is None:
        return None
    updates: list[str] = []
    values: list[Any] = []
    if title is not None:
        title = title.strip()
        if not title:
            raise ValueError("Title must not be blank.")
        updates.append("title = ?")
        values.append(title)
    if description is not None:
        description = description.strip()
        if not description:
            raise ValueError("Description must not be blank.")
        updates.append("description = ?")
        values.append(description)
    if learning_objective is not None:
        learning_objective = learning_objective.strip() or None
        if learning_objective is not None and len(learning_objective) > 1000:
            raise ValueError("Learning objective must not exceed 1000 characters.")
        updates.append("learning_objective = ?")
        values.append(learning_objective)
    if status is not None:
        if status not in {"active", "archived"}:
            raise ValueError("Status must be 'active' or 'archived'.")
        updates.append("status = ?")
        values.append(status)
    if not updates:
        return get_study_space(database_path, space_id=space_id, user_id=user_id)
    updates.append("updated_at = ?")
    values.append(datetime.now(UTC).isoformat())
    values.append(space_id)
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                f"UPDATE study_spaces SET {', '.join(updates)} WHERE space_id = ?",
                tuple(values),
            )
    return get_study_space(database_path, space_id=space_id, user_id=user_id)


def delete_study_space(database_path: Path, *, space_id: str, user_id: str) -> bool:
    """Delete a study space. Only the owner may delete; cascades members/docs/quizzes/discussions."""
    if not _is_workspace_owner(database_path, space_id, user_id):
        return False
    row = _workspace_exists(database_path, space_id)
    if row is None:
        return False
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute("DELETE FROM study_spaces WHERE space_id = ?", (space_id,))
    return True


def list_workspace_members(database_path: Path, *, space_id: str, user_id: str) -> list[dict[str, Any]] | None:
    """List members of a space if the caller is a member; None if unauthorized."""
    if not _is_workspace_member(database_path, space_id, user_id):
        return None
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT m.user_id, m.role, m.joined_at, u.username
            FROM workspace_members AS m
            JOIN users AS u ON u.user_id = m.user_id
            WHERE m.space_id = ?
            ORDER BY m.role DESC, u.username COLLATE NOCASE
            """,
            (space_id,),
        ).fetchall()
    return [
        {
            "userId": row["user_id"],
            "username": row["username"],
            "role": row["role"],
            "joinedAt": row["joined_at"],
        }
        for row in rows
    ]


def add_workspace_member(
    database_path: Path,
    *,
    space_id: str,
    inviter_user_id: str,
    target_username: str,
) -> dict[str, Any] | None:
    """Invite a user to a workspace by username. Only the owner may invite."""
    if not _is_workspace_owner(database_path, space_id, inviter_user_id):
        return None
    if _workspace_exists(database_path, space_id) is None:
        return None
    target_username = target_username.strip()
    with closing(connect_database(database_path)) as connection:
        target = connection.execute(
            "SELECT user_id, username FROM users WHERE username = ?", (target_username,)
        ).fetchone()
        if target is None:
            return None
        if _is_workspace_member(database_path, space_id, target["user_id"]):
            return None
        now = datetime.now(UTC).isoformat()
        with connection:
            connection.execute(
                """
                INSERT INTO workspace_members(space_id, user_id, role, joined_at)
                VALUES (?, ?, 'member', ?)
                """,
                (space_id, target["user_id"], now),
            )
    return {
        "userId": target["user_id"],
        "username": target["username"],
        "role": "member",
        "joinedAt": now,
    }


def remove_workspace_member(
    database_path: Path,
    *,
    space_id: str,
    actor_user_id: str,
    target_user_id: str,
) -> bool:
    """Remove a member from a space. Only the owner may remove; owner cannot remove themselves."""
    if not _is_workspace_owner(database_path, space_id, actor_user_id):
        return False
    if actor_user_id == target_user_id:
        return False
    if _workspace_exists(database_path, space_id) is None:
        return False
    if not _is_workspace_member(database_path, space_id, target_user_id):
        return False
    row = _workspace_member_row(database_path, space_id, target_user_id)
    if row is not None and row["role"] == "owner":
        return False
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                "DELETE FROM workspace_members WHERE space_id = ? AND user_id = ?",
                (space_id, target_user_id),
            )
    return True


def share_document_with_workspace(
    database_path: Path,
    *,
    space_id: str,
    actor_user_id: str,
    document_id: str,
) -> dict[str, Any] | None:
    """Share an owned document with a workspace. Only the owner/members of the space may share."""
    if not _is_workspace_member(database_path, space_id, actor_user_id):
        return None
    if _workspace_exists(database_path, space_id) is None:
        return None
    with closing(connect_database(database_path)) as connection:
        doc = connection.execute(
            "SELECT document_id, display_name, sha256, byte_size, page_count, chunk_count, created_at, parser_version, user_id FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if doc is None:
            return None
        if doc["user_id"] != actor_user_id and doc["user_id"] is not None:
            return None
        existing = connection.execute(
            "SELECT 1 FROM workspace_documents WHERE space_id = ? AND document_id = ?",
            (space_id, document_id),
        ).fetchone()
        if existing is not None:
            return None
        now = datetime.now(UTC).isoformat()
        with connection:
            connection.execute(
                """
                INSERT INTO workspace_documents(space_id, document_id, shared_by, shared_at)
                VALUES (?, ?, ?, ?)
                """,
                (space_id, document_id, actor_user_id, now),
            )
    return {
        "documentId": doc["document_id"],
        "displayFilename": doc["display_name"],
        "sha256": doc["sha256"],
        "byteSize": doc["byte_size"],
        "pageCount": doc["page_count"],
        "chunkCount": doc["chunk_count"],
        "createdAt": doc["created_at"],
        "parserVersion": doc["parser_version"],
        "sharedAt": now,
        "sharedBy": actor_user_id,
        "sharedByUsername": None,
    }


def list_workspace_documents(
    database_path: Path, *, space_id: str, user_id: str
) -> list[dict[str, Any]] | None:
    """List documents shared with a workspace if the caller is a member; None if unauthorized."""
    if not _is_workspace_member(database_path, space_id, user_id):
        return None
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT wd.document_id, d.display_name, d.sha256, d.byte_size, d.page_count,
                   d.chunk_count, d.created_at, d.parser_version, wd.shared_at, wd.shared_by, u.username
            FROM workspace_documents AS wd
            JOIN documents AS d ON d.document_id = wd.document_id
            JOIN users AS u ON u.user_id = wd.shared_by
            WHERE wd.space_id = ?
            ORDER BY wd.shared_at DESC
            """,
            (space_id,),
        ).fetchall()
    return [
        {
            "documentId": row["document_id"],
            "displayFilename": row["display_name"],
            "sha256": row["sha256"],
            "byteSize": row["byte_size"],
            "pageCount": row["page_count"],
            "chunkCount": row["chunk_count"],
            "createdAt": row["created_at"],
            "parserVersion": row["parser_version"],
            "sharedAt": row["shared_at"],
            "sharedBy": row["shared_by"],
            "sharedByUsername": row["username"],
        }
        for row in rows
    ]


def remove_workspace_document(
    database_path: Path, *, space_id: str, actor_user_id: str, document_id: str
) -> bool:
    """Unshare a document from a workspace. Only the sharer or space owner may unshare."""
    if not _is_workspace_member(database_path, space_id, actor_user_id):
        return False
    if _workspace_exists(database_path, space_id) is None:
        return False
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            "SELECT shared_by FROM workspace_documents WHERE space_id = ? AND document_id = ?",
            (space_id, document_id),
        ).fetchone()
        if row is None:
            return False
        is_owner = _is_workspace_owner(database_path, space_id, actor_user_id)
        if not is_owner and row["shared_by"] != actor_user_id:
            return False
        with connection:
            connection.execute(
                "DELETE FROM workspace_documents WHERE space_id = ? AND document_id = ?",
                (space_id, document_id),
            )
    return True


def share_quiz_with_workspace(
    database_path: Path,
    *,
    space_id: str,
    actor_user_id: str,
    quiz_id: str,
) -> dict[str, Any] | None:
    """Share an owned quiz with a workspace. Only space members may share."""
    if not _is_workspace_member(database_path, space_id, actor_user_id):
        return None
    if _workspace_exists(database_path, space_id) is None:
        return None
    with closing(connect_database(database_path)) as connection:
        quiz = connection.execute(
            "SELECT quiz_id, user_id FROM quiz_sets WHERE quiz_id = ?",
            (quiz_id,),
        ).fetchone()
        if quiz is None:
            return None
        if quiz["user_id"] != actor_user_id and quiz["user_id"] is not None:
            return None
        existing = connection.execute(
            "SELECT 1 FROM workspace_quizzes WHERE space_id = ? AND quiz_id = ?",
            (space_id, quiz_id),
        ).fetchone()
        if existing is not None:
            return None
        now = datetime.now(UTC).isoformat()
        with connection:
            connection.execute(
                """
                INSERT INTO workspace_quizzes(space_id, quiz_id, shared_by, shared_at)
                VALUES (?, ?, ?, ?)
                """,
                (space_id, quiz_id, actor_user_id, now),
            )
    return {
        "spaceId": space_id,
        "quizId": quiz_id,
        "sharedBy": actor_user_id,
        "sharedAt": now,
    }


def list_workspace_quizzes(
    database_path: Path, *, space_id: str, user_id: str
) -> list[dict[str, Any]] | None:
    """List quizzes shared with a workspace if the caller is a member; None if unauthorized."""
    if not _is_workspace_member(database_path, space_id, user_id):
        return None
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT wq.quiz_id, qs.topic_id, qs.topic, qs.created_at, wq.shared_at, wq.shared_by, u.username
            FROM workspace_quizzes AS wq
            JOIN quiz_sets AS qs ON qs.quiz_id = wq.quiz_id
            JOIN users AS u ON u.user_id = wq.shared_by
            WHERE wq.space_id = ?
            ORDER BY wq.shared_at DESC
            """,
            (space_id,),
        ).fetchall()
    return [
        {
            "quizId": row["quiz_id"],
            "topicId": row["topic_id"],
            "topic": row["topic"],
            "createdAt": row["created_at"],
            "sharedAt": row["shared_at"],
            "sharedBy": row["shared_by"],
            "sharedByUsername": row["username"],
        }
        for row in rows
    ]


def create_workspace_discussion(
    database_path: Path,
    *,
    space_id: str,
    user_id: str,
    content: str,
) -> dict[str, Any] | None:
    """Create a discussion thread in a workspace. Only members may create."""
    if not _is_workspace_member(database_path, space_id, user_id):
        return None
    if _workspace_exists(database_path, space_id) is None:
        return None

    content = content.strip()
    if not content:
        raise ValueError("Discussion content must not be blank.")
    if len(content) > 4000:
        raise ValueError("Discussion content must not exceed 4000 characters.")
    discussion_id = f"disc_{secrets.token_urlsafe(12)}"
    now = datetime.now(UTC).isoformat()
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO workspace_discussions(discussion_id, space_id, user_id, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (discussion_id, space_id, user_id, content, now),
            )
    return {
        "discussionId": discussion_id,
        "spaceId": space_id,
        "userId": user_id,
        "content": content,
        "createdAt": now,
    }


def list_workspace_discussions(
    database_path: Path, *, space_id: str, user_id: str
) -> list[dict[str, Any]] | None:
    """List discussion threads in a workspace if the caller is a member; None if unauthorized."""
    if not _is_workspace_member(database_path, space_id, user_id):
        return None
    if _workspace_exists(database_path, space_id) is None:
        return None
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT d.discussion_id, d.space_id, d.user_id, d.content, d.created_at, u.username
            FROM workspace_discussions AS d
            JOIN users AS u ON u.user_id = d.user_id
            WHERE d.space_id = ?
            ORDER BY d.created_at DESC
            """,
            (space_id,),
        ).fetchall()
    return [
        {
            "discussionId": row["discussion_id"],
            "spaceId": row["space_id"],
            "userId": row["user_id"],
            "username": row["username"],
            "content": row["content"],
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def create_workspace_discussion_reply(
    database_path: Path,
    *,
    space_id: str,
    discussion_id: str,
    user_id: str,
    content: str,
) -> dict[str, Any] | None:
    """Reply to a discussion thread. Only members may reply."""
    if not _is_workspace_member(database_path, space_id, user_id):
        return None
    if _workspace_exists(database_path, space_id) is None:
        return None

    content = content.strip()
    if not content:
        raise ValueError("Reply content must not be blank.")
    if len(content) > 4000:
        raise ValueError("Reply content must not exceed 4000 characters.")
    reply_id = f"reply_{secrets.token_urlsafe(12)}"
    now = datetime.now(UTC).isoformat()
    with closing(connect_database(database_path)) as connection:
        with connection:
            discussion = connection.execute(
                "SELECT discussion_id FROM workspace_discussions WHERE discussion_id = ? AND space_id = ?",
                (discussion_id, space_id),
            ).fetchone()
            if discussion is None:
                return None
            connection.execute(
                """
                INSERT INTO workspace_discussion_replies(reply_id, discussion_id, user_id, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (reply_id, discussion_id, user_id, content, now),
            )
    return {
        "replyId": reply_id,
        "discussionId": discussion_id,
        "userId": user_id,
        "content": content,
        "createdAt": now,
    }


def list_workspace_discussion_replies(
    database_path: Path, *, space_id: str, discussion_id: str, user_id: str
) -> list[dict[str, Any]] | None:
    """List replies to a discussion if the caller is a member; None if unauthorized."""
    if not _is_workspace_member(database_path, space_id, user_id):
        return None
    if _workspace_exists(database_path, space_id) is None:
        return None
    with closing(connect_database(database_path)) as connection:
        discussion = connection.execute(
            "SELECT discussion_id FROM workspace_discussions WHERE discussion_id = ? AND space_id = ?",
            (discussion_id, space_id),
        ).fetchone()
        if discussion is None:
            return None
        rows = connection.execute(
            """
            SELECT r.reply_id, r.discussion_id, r.user_id, r.content, r.created_at, u.username
            FROM workspace_discussion_replies AS r
            JOIN users AS u ON u.user_id = r.user_id
            WHERE r.discussion_id = ?
            ORDER BY r.created_at ASC
            """,
            (discussion_id,),
        ).fetchall()
    return [
        {
            "replyId": row["reply_id"],
            "discussionId": row["discussion_id"],
            "userId": row["user_id"],
            "username": row["username"],
            "content": row["content"],
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# M38: AI Learning Content Studio storage functions.
# ---------------------------------------------------------------------------

def _resource_exists(database_path: Path, resource_id: str) -> dict[str, Any] | None:
    with closing(connect_database(database_path)) as connection:
        return connection.execute(
            "SELECT resource_id, user_id, title, description, format, detail_level, status, "
            "source_type, source_refs_json, created_at, updated_at, reviewed_at, reviewed_by "
            "FROM content_resources WHERE resource_id = ?",
            (resource_id,),
        ).fetchone()


def _is_resource_owner(database_path: Path, resource_id: str, user_id: str) -> bool:
    row = _resource_exists(database_path, resource_id)
    return row is not None and row["user_id"] == user_id


def _verify_source_chunks(
    database_path: Path,
    citation_ids: list[str],
    user_id: str | None,
) -> list[dict[str, Any]]:
    """Return chunk records for citation ids, enforcing user ownership."""
    if not citation_ids:
        return []
    placeholders = ",".join("?" for _ in citation_ids)
    parameters: list[Any] = list(citation_ids)
    if user_id is not None:
        sql = f"""
            SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
                   c.chunk_index, c.char_start, c.char_end, c.text
            FROM chunks AS c
            JOIN documents AS d ON d.document_id = c.document_id
            WHERE c.chunk_id IN ({placeholders}) AND c.user_id = ? AND d.user_id = ?
        """
        parameters.extend([user_id, user_id])
    else:
        sql = f"""
            SELECT c.chunk_id, c.document_id, d.display_name, c.page_number,
                   c.chunk_index, c.char_start, c.char_end, c.text
            FROM chunks AS c
            JOIN documents AS d ON d.document_id = c.document_id
            WHERE c.chunk_id IN ({placeholders}) AND c.user_id IS NULL AND d.user_id IS NULL
        """
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(sql, parameters).fetchall()
    return [dict(row) for row in rows]


def create_content_resource(
    database_path: Path,
    *,
    user_id: str,
    title: str,
    description: str | None,
    format: str,
    detail_level: str,
    source_type: str,
    source_refs: list[dict[str, Any]],
    status: str = "draft",
) -> dict[str, Any]:
    """Create a new AI-generated content resource owned by ``user_id``."""

    resource_id = f"content_{secrets.token_urlsafe(12)}"
    now = datetime.now(UTC).isoformat()
    title = title.strip()
    description = (description or "").strip() or None
    source_refs_json = json.dumps(source_refs, separators=(",", ":"), sort_keys=True)
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO content_resources(
                    resource_id, user_id, title, description, format, detail_level,
                    status, source_type, source_refs_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resource_id,
                    user_id,
                    title,
                    description,
                    format,
                    detail_level,
                    status,
                    source_type,
                    source_refs_json,
                    now,
                    now,
                ),
            )
    return {
        "resourceId": resource_id,
        "userId": user_id,
        "title": title,
        "description": description,
        "format": format,
        "detailLevel": detail_level,
        "status": status,
        "sourceType": source_type,
        "sourceRefs": source_refs,
        "createdAt": now,
        "updatedAt": now,
        "reviewedAt": None,
        "reviewedBy": None,
    }


def list_content_resources(
    database_path: Path, *, user_id: str, status: str | None = None
) -> list[dict[str, Any]]:
    """List content resources owned by the user, newest first."""
    query = """
        SELECT resource_id, user_id, title, description, format, detail_level, status,
               source_type, source_refs_json, created_at, updated_at, reviewed_at, reviewed_by
        FROM content_resources WHERE user_id = ?
    """
    params: list[Any] = [user_id]
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY updated_at DESC, created_at DESC"
    with closing(connect_database(database_path)) as connection:
        rows = connection.execute(query, params).fetchall()
    return [_resource_row_to_public(row) for row in rows]


def _resource_row_to_public(row: sqlite3.Row) -> dict[str, Any]:

    return {
        "resourceId": row["resource_id"],
        "userId": row["user_id"],
        "title": row["title"],
        "description": row["description"],
        "format": row["format"],
        "detailLevel": row["detail_level"],
        "status": row["status"],
        "sourceType": row["source_type"],
        "sourceRefs": json.loads(row["source_refs_json"] or "[]"),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "reviewedAt": row["reviewed_at"],
        "reviewedBy": row["reviewed_by"],
    }


def get_content_resource(
    database_path: Path, *, resource_id: str, user_id: str
) -> dict[str, Any] | None:
    """Return a content resource with its sections if the user owns it."""
    if not _is_resource_owner(database_path, resource_id, user_id):
        return None
    row = _resource_exists(database_path, resource_id)
    if row is None:
        return None
    with closing(connect_database(database_path)) as connection:
        section_rows = connection.execute(
            """
            SELECT section_id, resource_id, section_type, section_title, content,
                   citations_json, version, created_at
            FROM content_resource_sections
            WHERE resource_id = ?
            ORDER BY created_at ASC, section_id ASC
            """,
            (resource_id,),
        ).fetchall()
    sections = [_section_row_to_public(row) for row in section_rows]
    public = _resource_row_to_public(row)
    public["sections"] = sections
    return public


def _section_row_to_public(row: sqlite3.Row) -> dict[str, Any]:

    return {
        "sectionId": row["section_id"],
        "resourceId": row["resource_id"],
        "sectionType": row["section_type"],
        "sectionTitle": row["section_title"],
        "content": row["content"],
        "citations": json.loads(row["citations_json"] or "[]"),
        "version": row["version"],
        "createdAt": row["created_at"],
    }


def update_content_resource(
    database_path: Path,
    *,
    resource_id: str,
    user_id: str,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Update mutable content resource fields. Only the owner may update."""
    if not _is_resource_owner(database_path, resource_id, user_id):
        return None
    if _resource_exists(database_path, resource_id) is None:
        return None
    updates: list[str] = []
    values: list[Any] = []
    if title is not None:
        title = title.strip()
        if not title:
            raise ValueError("Title must not be blank.")
        updates.append("title = ?")
        values.append(title)
    if description is not None:
        description = description.strip() or None
        updates.append("description = ?")
        values.append(description)
    if status is not None:
        if status not in {"draft", "reviewed", "archived"}:
            raise ValueError("Status must be 'draft', 'reviewed', or 'archived'.")
        updates.append("status = ?")
        values.append(status)
        if status == "reviewed":
            updates.append("reviewed_at = ?")
            values.append(datetime.now(UTC).isoformat())
            updates.append("reviewed_by = ?")
            values.append(user_id)
    if not updates:
        return get_content_resource(database_path, resource_id=resource_id, user_id=user_id)
    updates.append("updated_at = ?")
    values.append(datetime.now(UTC).isoformat())
    values.append(resource_id)
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                f"UPDATE content_resources SET {', '.join(updates)} WHERE resource_id = ?",
                tuple(values),
            )
    return get_content_resource(database_path, resource_id=resource_id, user_id=user_id)


def delete_content_resource(
    database_path: Path, *, resource_id: str, user_id: str
) -> bool:
    """Delete a content resource, its sections, and revisions. Owner only."""
    if not _is_resource_owner(database_path, resource_id, user_id):
        return False
    if _resource_exists(database_path, resource_id) is None:
        return False
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                "DELETE FROM content_resources WHERE resource_id = ?", (resource_id,)
            )
    return True


def create_content_section(
    database_path: Path,
    *,
    resource_id: str,
    user_id: str,
    section_type: str,
    section_title: str,
    content: str,
    citations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Create a new section for a content resource. Owner only."""
    if not _is_resource_owner(database_path, resource_id, user_id):
        return None
    if _resource_exists(database_path, resource_id) is None:
        return None

    section_id = f"section_{secrets.token_urlsafe(12)}"
    now = datetime.now(UTC).isoformat()
    section_title = section_title.strip()
    content = content.strip()
    if not section_title:
        raise ValueError("Section title must not be blank.")
    if not content:
        raise ValueError("Section content must not be blank.")
    citations_json = json.dumps(citations, separators=(",", ":"), sort_keys=True)
    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO content_resource_sections(
                    section_id, resource_id, section_type, section_title,
                    content, citations_json, version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    section_id,
                    resource_id,
                    section_type,
                    section_title,
                    content,
                    citations_json,
                    now,
                ),
            )
    return {
        "sectionId": section_id,
        "resourceId": resource_id,
        "sectionType": section_type,
        "sectionTitle": section_title,
        "content": content,
        "citations": citations,
        "version": 1,
        "createdAt": now,
    }


def update_content_section(
    database_path: Path,
    *,
    resource_id: str,
    section_id: str,
    user_id: str,
    section_title: str | None = None,
    content: str | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Edit a section's mutable fields. Only the resource owner may edit."""
    if not _is_resource_owner(database_path, resource_id, user_id):
        return None
    with closing(connect_database(database_path)) as connection:
        section = connection.execute(
            "SELECT section_id, version FROM content_resource_sections WHERE section_id = ? AND resource_id = ?",
            (section_id, resource_id),
        ).fetchone()
        if section is None:
            return None
        updates: list[str] = []
        values: list[Any] = []
        if section_title is not None:
            section_title = section_title.strip()
            if not section_title:
                raise ValueError("Section title must not be blank.")
            updates.append("section_title = ?")
            values.append(section_title)
        if content is not None:
            content = content.strip()
            if not content:
                raise ValueError("Section content must not be blank.")
            updates.append("content = ?")
            values.append(content)
        if citations is not None:

            updates.append("citations_json = ?")
            values.append(json.dumps(citations, separators=(",", ":"), sort_keys=True))
        if not updates:
            return get_content_section(database_path, resource_id, section_id, user_id)
        new_version = int(section["version"]) + 1
        updates.append("version = ?")
        values.append(new_version)
        updates.append("created_at = ?")
        values.append(datetime.now(UTC).isoformat())
        values.append(section_id)
        with connection:
            connection.execute(
                f"UPDATE content_resource_sections SET {', '.join(updates)} WHERE section_id = ?",
                tuple(values),
            )
            connection.execute(
                """
                INSERT INTO content_resource_revisions(
                    revision_id, resource_id, section_id, content, citations_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"rev_{secrets.token_urlsafe(12)}",
                    resource_id,
                    section_id,
                    content if content is not None
                    else connection.execute(
                        "SELECT content FROM content_resource_sections WHERE section_id = ?",
                        (section_id,),
                    ).fetchone()["content"],
                    json.dumps(citations, separators=(",", ":"), sort_keys=True)
                    if citations is not None
                    else connection.execute(
                        "SELECT citations_json FROM content_resource_sections WHERE section_id = ?",
                        (section_id,),
                    ).fetchone()["citations_json"],
                    datetime.now(UTC).isoformat(),
                ),
            )
    return get_content_section(database_path, resource_id, section_id, user_id)


def regenerate_content_section(
    database_path: Path,
    *,
    resource_id: str,
    section_id: str,
    user_id: str,
    section_type: str | None = None,
    section_title: str | None = None,
    content: str | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Regenerate a section's content, preserving the previous version as a revision."""
    if not _is_resource_owner(database_path, resource_id, user_id):
        return None
    with closing(connect_database(database_path)) as connection:
        section = connection.execute(
            "SELECT content, citations_json, version FROM content_resource_sections WHERE section_id = ? AND resource_id = ?",
            (section_id, resource_id),
        ).fetchone()
        if section is None:
            return None
        # Archive current content as a revision before overwriting.

        now = datetime.now(UTC).isoformat()
        new_version = int(section["version"]) + 1
        updates: list[str] = []
        values: list[Any] = []
        if content is not None:
            content = content.strip()
            if not content:
                raise ValueError("Section content must not be blank.")
            updates.append("content = ?")
            values.append(content)
        if citations is not None:
            updates.append("citations_json = ?")
            values.append(json.dumps(citations, separators=(",", ":"), sort_keys=True))
        if section_title is not None:
            section_title = section_title.strip()
            if not section_title:
                raise ValueError("Section title must not be blank.")
            updates.append("section_title = ?")
            values.append(section_title)
        if section_type is not None:
            updates.append("section_type = ?")
            values.append(section_type)
        if not updates:
            return get_content_section(database_path, resource_id, section_id, user_id)
        updates.append("version = ?")
        values.append(new_version)
        updates.append("created_at = ?")
        values.append(now)
        values.append(section_id)
        with connection:
            connection.execute(
                """
                INSERT INTO content_resource_revisions(
                    revision_id, resource_id, section_id, content, citations_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"rev_{secrets.token_urlsafe(12)}",
                    resource_id,
                    section_id,
                    section["content"],
                    section["citations_json"],
                    now,
                ),
            )
            connection.execute(
                f"UPDATE content_resource_sections SET {', '.join(updates)} WHERE section_id = ?",
                tuple(values),
            )
    return get_content_section(database_path, resource_id, section_id, user_id)


def get_content_section(
    database_path: Path, resource_id: str, section_id: str, user_id: str
) -> dict[str, Any] | None:
    """Return one section if the user owns the parent resource."""
    if not _is_resource_owner(database_path, resource_id, user_id):
        return None

    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            """
            SELECT section_id, resource_id, section_type, section_title, content,
                   citations_json, version, created_at
            FROM content_resource_sections
            WHERE section_id = ? AND resource_id = ?
            """,
            (section_id, resource_id),
        ).fetchone()
    if row is None:
        return None
    return _section_row_to_public(row)


def list_content_revisions(
    database_path: Path, *, resource_id: str, section_id: str, user_id: str
) -> list[dict[str, Any]] | None:
    """List revisions for a section if the user owns the parent resource."""
    if not _is_resource_owner(database_path, resource_id, user_id):
        return None

    with closing(connect_database(database_path)) as connection:
        section = connection.execute(
            "SELECT 1 FROM content_resource_sections WHERE section_id = ? AND resource_id = ?",
            (section_id, resource_id),
        ).fetchone()
        if section is None:
            return None
        rows = connection.execute(
            """
            SELECT revision_id, resource_id, section_id, content, citations_json, created_at
            FROM content_resource_revisions
            WHERE section_id = ?
            ORDER BY created_at DESC
            """,
            (section_id,),
        ).fetchall()
    return [
        {
            "revisionId": row["revision_id"],
            "resourceId": row["resource_id"],
            "sectionId": row["section_id"],
            "content": row["content"],
            "citations": json.loads(row["citations_json"] or "[]"),
            "createdAt": row["created_at"],
        }
        for row in rows
    ]
