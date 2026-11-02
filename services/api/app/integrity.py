"""Read-only database integrity audit for M50.

Reports structural/orphan findings without modifying the database.
Intended for tests, ops tooling, and the recovery runbook.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


def _fetch_count(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    try:
        row = connection.execute(sql, params).fetchone()
        return int(row[0]) if row is not None else 0
    except sqlite3.Error:
        return -1


def audit_database(database_path: Path) -> dict[str, Any]:
    """Run read-only integrity checks against an existing SQLite database.

    Never modifies the database. Returns {"ok": bool, "findings": [...]}.
    """
    findings: list[dict[str, Any]] = []
    ok = True

    def record(name: str, passed: bool, detail: Any = None, severity: str = "medium") -> None:
        nonlocal ok
        if not passed:
            ok = False
        findings.append(
            {
                "check": name,
                "ok": bool(passed),
                "severity": severity,
                "detail": detail,
            }
        )

    with closing(sqlite3.connect(str(database_path))) as connection:
        connection.row_factory = sqlite3.Row

        # 1. Structural integrity
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        record(
            "quick_check",
            integrity is not None and integrity[0] == "ok",
            integrity[0] if integrity else None,
            "critical",
        )

        # 2. Referential integrity
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        record("foreign_key_check", len(violations) == 0, f"{len(violations)} violations", "high")

        # 3. FTS orphan rows (chunks_fts is manually synced, not FK-linked)
        count = _fetch_count(
            connection,
            "SELECT COUNT(*) FROM chunks_fts f WHERE NOT EXISTS ("
            "SELECT 1 FROM chunks c WHERE c.chunk_id = f.chunk_id)",
        )
        record("chunks_fts_orphans", count == 0, f"{count} orphaned FTS rows", "high")

        # 4. Embedding orphan rows
        count = _fetch_count(
            connection,
            "SELECT COUNT(*) FROM chunk_embeddings e WHERE NOT EXISTS ("
            "SELECT 1 FROM chunks c WHERE c.chunk_id = e.chunk_id)",
        )
        record("chunk_embeddings_orphans", count == 0, f"{count} orphaned embedding rows", "high")

        # 5. Quiz citation orphans (chunk or question missing)
        count = _fetch_count(
            connection,
            "SELECT COUNT(*) FROM quiz_citations qc WHERE NOT EXISTS ("
            "SELECT 1 FROM chunks c WHERE c.chunk_id = qc.citation_id) OR NOT EXISTS ("
            "SELECT 1 FROM quiz_questions qq WHERE qq.question_id = qc.question_id)",
        )
        record("quiz_citations_orphans", count == 0, f"{count} orphaned quiz citations", "medium")

        # 6. Image region citation orphans
        count = _fetch_count(
            connection,
            "SELECT COUNT(*) FROM image_region_citations irc WHERE NOT EXISTS ("
            "SELECT 1 FROM chunks c WHERE c.chunk_id = irc.citation_id)",
        )
        record(
            "image_region_citations_orphans",
            count == 0,
            f"{count} orphaned image citations",
            "medium",
        )

        # 7. content_resources referencing deleted documents (JSON-aware)
        dangling = _dangling_content_resource_refs(connection)
        record(
            "content_resources_dangling_refs", dangling == 0, f"{dangling} dangling refs", "medium"
        )

        # 8. Knowledge concepts without evidence or relationships
        count = _fetch_count(
            connection,
            "SELECT COUNT(*) FROM knowledge_concepts kc WHERE NOT EXISTS ("
            "SELECT 1 FROM knowledge_concept_evidence e "
            "WHERE e.concept_id = kc.concept_id) AND NOT EXISTS ("
            "SELECT 1 FROM knowledge_relationships r "
            "WHERE r.source_concept_id = kc.concept_id OR "
            "r.target_concept_id = kc.concept_id)",
        )
        record("knowledge_concepts_orphans", count == 0, f"{count} orphaned concepts", "low")

        # 9. documents.chunk_count drift vs actual chunk count
        count = _fetch_count(
            connection,
            "SELECT COUNT(*) FROM documents d WHERE d.chunk_count <> ("
            "SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.document_id)",
        )
        record(
            "chunk_count_drift",
            count == 0,
            f"{count} documents with mismatched chunk_count",
            "medium",
        )

        # 10. chunk user_id vs parent document user_id mismatch
        count = _fetch_count(
            connection,
            "SELECT COUNT(*) FROM chunks c JOIN documents d ON d.document_id = c.document_id "
            "WHERE (c.user_id IS NULL) <> (d.user_id IS NULL) OR c.user_id <> d.user_id",
        )
        record(
            "chunk_user_id_mismatch",
            count == 0,
            f"{count} chunks with mismatched user_id",
            "medium",
        )

    return {"ok": ok, "findings": findings}


def _dangling_content_resource_refs(connection: sqlite3.Connection) -> int:
    """Count content_resources whose source_refs_json points at a deleted document."""
    try:
        return _fetch_count(
            connection,
            "SELECT COUNT(DISTINCT cr.resource_id) FROM content_resources cr "
            "JOIN json_each(cr.source_refs_json) je "
            "WHERE json_type(je.value) = 'object' "
            "AND json_extract(je.value, '$.documentId') IS NOT NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM documents d "
            "WHERE d.document_id = json_extract(je.value, '$.documentId'))",
        )
    except sqlite3.Error:
        # JSON1 unavailable: fall back to parsing source_refs_json in Python.
        dangling = 0
        rows = connection.execute(
            "SELECT resource_id, source_refs_json FROM content_resources "
            "WHERE source_refs_json LIKE '%documentId%'"
        ).fetchall()
        for _resource_id, refs_json in rows:
            try:
                refs = json.loads(refs_json or "[]")
            except (ValueError, TypeError):
                dangling += 1
                continue
            candidates = refs if isinstance(refs, list) else [refs]
            for ref in candidates:
                doc_id = ref.get("documentId") if isinstance(ref, dict) else None
                if (
                    doc_id
                    and not connection.execute(
                        "SELECT 1 FROM documents WHERE document_id = ?", (doc_id,)
                    ).fetchone()
                ):
                    dangling += 1
                    break
        return dangling


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m app.integrity <database_path>", file=sys.stderr)
        return 2
    report = audit_database(Path(argv[0]))
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
