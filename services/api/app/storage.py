"""Minimal SQLite availability checks; no application data schema is defined in M0."""

import sqlite3
from contextlib import closing
from pathlib import Path


def initialize_storage(database_path: Path) -> None:
    """Create the local database file so startup exercises the configured storage path."""
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path, timeout=2)) as connection:
        connection.execute("PRAGMA user_version")


def check_storage(database_path: Path) -> dict[str, bool]:
    """Check that SQLite opens the configured DB, passes integrity check, and has FTS5."""
    status = {"sqlite": False, "sqlite_fts5": False}
    try:
        database_uri = f"{database_path.resolve().as_uri()}?mode=rw"
        with closing(sqlite3.connect(database_uri, uri=True, timeout=2)) as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            status["sqlite"] = integrity == ("ok",)
            connection.execute("CREATE VIRTUAL TABLE temp.readiness_fts USING fts5(content)")
            connection.execute("DROP TABLE temp.readiness_fts")
            status["sqlite_fts5"] = True
    except (OSError, sqlite3.Error):
        return status
    return status
