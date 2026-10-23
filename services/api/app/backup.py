"""Create, validate, restore, and retain private SQLite backup archives."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import Settings
from app.storage import SCHEMA_VERSION

FORMAT_VERSION = 1
DATABASE_MEMBER = "database.sqlite3"
MANIFEST_MEMBER = "manifest.json"
BACKUP_NAME = re.compile(r"^wonderlearn-\d{8}T\d{6}Z-[0-9a-f]{32}\.wlbackup$")


class BackupError(Exception):
    """A backup archive or operation failed a safety check."""


def _sqlite_uri(path: Path, mode: str) -> str:
    return f"{path.resolve().as_uri()}?mode={mode}"


def _set_private_file(path: Path) -> None:
    if os.name == "nt":
        _set_windows_acl(path, is_directory=False)
    else:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _set_private_directory(path: Path) -> None:
    if os.name == "nt":
        _set_windows_acl(path, is_directory=True)
    else:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


@lru_cache(maxsize=1)
def _windows_user_sid() -> str:
    try:
        result = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            text=True,
        )
        fields = next(csv.reader([result.stdout.strip()]))
    except (OSError, subprocess.CalledProcessError, StopIteration, csv.Error) as exc:
        raise BackupError(
            "Could not determine the current Windows account for backup ACLs"
        ) from exc
    if len(fields) < 2 or not re.fullmatch(r"S-1-[0-9-]+", fields[1]):
        raise BackupError("Windows did not return a valid account SID for backup ACLs")
    return fields[1]


def _set_windows_acl(path: Path, *, is_directory: bool) -> None:
    grant = f"*{_windows_user_sid()}:"
    if is_directory:
        grant += "(OI)(CI)F"
    else:
        grant += "F"
    try:
        result = subprocess.run(
            ["icacls.exe", str(path), "/inheritance:r", "/grant:r", grant],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise BackupError("Could not restrict backup permissions using icacls") from exc
    if result.returncode != 0:
        raise BackupError("Could not restrict backup permissions using icacls")


def _ensure_private_directory(directory: Path) -> Path:
    if directory.is_symlink():
        raise BackupError("Backup location must not be a symbolic link")
    directory.mkdir(parents=True, exist_ok=True)
    resolved = directory.resolve()
    if not resolved.is_dir():
        raise BackupError("Backup location must be a directory")
    _set_private_directory(resolved)
    return resolved


def _integrity_check(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA quick_check").fetchone()
    if result is None or result[0] != "ok":
        raise BackupError("SQLite integrity check failed")
    violations = connection.execute("PRAGMA foreign_key_check").fetchone()
    if violations is not None:
        raise BackupError("SQLite foreign-key integrity check failed")


def _schema_version(connection: sqlite3.Connection) -> int:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version < 0 or version > SCHEMA_VERSION:
        raise BackupError(
            f"Database schema version {version} is not supported by this application "
            f"(maximum supported: {SCHEMA_VERSION})"
        )
    return version


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _create_snapshot(database_path: Path, snapshot_path: Path) -> tuple[int, int, str]:
    if not database_path.is_file():
        raise BackupError(f"Database file does not exist: {database_path}")
    try:
        with closing(sqlite3.connect(_sqlite_uri(database_path, "ro"), uri=True)) as source:
            _integrity_check(source)
            with closing(sqlite3.connect(snapshot_path)) as destination:
                source.backup(destination)
                if destination.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_sessions'"
                ).fetchone():
                    destination.execute("DELETE FROM user_sessions")
                destination.commit()
                _integrity_check(destination)
                version = _schema_version(destination)
        _set_private_file(snapshot_path)
    except sqlite3.Error as exc:
        raise BackupError("Could not create a consistent SQLite snapshot") from exc
    return version, snapshot_path.stat().st_size, _sha256_file(snapshot_path)


def create_backup(database_path: Path, backup_directory: Path) -> dict[str, Any]:
    """Create an atomic archive using SQLite's consistent online backup API."""
    directory = _ensure_private_directory(backup_directory)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_name = f"wonderlearn-{timestamp}-{uuid.uuid4().hex}.wlbackup"
    archive_path = directory / archive_name

    with tempfile.TemporaryDirectory(prefix=".wonderlearn-backup-") as temporary:
        _set_private_directory(Path(temporary))
        snapshot_path = Path(temporary) / DATABASE_MEMBER
        version, size, digest = _create_snapshot(database_path, snapshot_path)
        manifest = {
            "format_version": FORMAT_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "schema_version": version,
            "database": {
                "name": DATABASE_MEMBER,
                "size_bytes": size,
                "sha256": digest,
            },
            "excluded_tables": ["user_sessions"],
        }
        temporary_archive = directory / f".{archive_name}.{uuid.uuid4().hex}.tmp"
        try:
            with zipfile.ZipFile(
                temporary_archive, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.write(snapshot_path, DATABASE_MEMBER)
                archive.writestr(
                    MANIFEST_MEMBER,
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                )
            _set_private_file(temporary_archive)
            try:
                os.link(temporary_archive, archive_path)
            except OSError:
                shutil.copy2(temporary_archive, archive_path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise BackupError("Could not write the backup archive") from exc
        finally:
            temporary_archive.unlink(missing_ok=True)

    return {
        "path": archive_path,
        "created_at": manifest["created_at"],
        "database_size_bytes": size,
        "database_sha256": digest,
        "schema_version": version,
    }


def _read_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    names = archive.namelist()
    if len(names) != 2 or set(names) != {DATABASE_MEMBER, MANIFEST_MEMBER}:
        raise BackupError("Backup archive has missing or unexpected files")
    try:
        raw = archive.read(MANIFEST_MEMBER)
        if len(raw) > 64 * 1024:
            raise BackupError("Backup manifest is too large")
        manifest = json.loads(raw)
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise BackupError("Backup manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise BackupError("Backup manifest is invalid")
    database = manifest.get("database")
    if (
        manifest.get("format_version") != FORMAT_VERSION
        or manifest.get("schema_version") not in range(0, SCHEMA_VERSION + 1)
        or not isinstance(database, dict)
        or database.get("name") != DATABASE_MEMBER
        or not isinstance(database.get("size_bytes"), int)
        or not isinstance(database.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", database["sha256"])
    ):
        raise BackupError("Backup format or schema version is unsupported")
    return manifest


def _extract_and_check(archive_path: Path, destination: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise BackupError(f"Backup archive is corrupt: {corrupt_member}")
            manifest = _read_manifest(archive)
            database_info = manifest["database"]
            database_member = archive.getinfo(DATABASE_MEMBER)
            if database_member.file_size != database_info["size_bytes"]:
                raise BackupError("Backup database size does not match its manifest")
            with archive.open(DATABASE_MEMBER) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise BackupError("Could not read the backup archive") from exc

    if destination.stat().st_size != manifest["database"]["size_bytes"]:
        raise BackupError("Backup database size does not match its manifest")
    if _sha256_file(destination) != manifest["database"]["sha256"]:
        raise BackupError("Backup database checksum does not match its manifest")
    try:
        with closing(sqlite3.connect(_sqlite_uri(destination, "ro"), uri=True)) as connection:
            _integrity_check(connection)
            actual_version = _schema_version(connection)
            if actual_version != manifest["schema_version"]:
                raise BackupError("Backup schema version does not match its manifest")
    except sqlite3.Error as exc:
        raise BackupError("Backup does not contain a valid SQLite database") from exc
    return manifest


def validate_backup(backup_path: Path) -> dict[str, Any]:
    """Validate archive members, checksums, schema compatibility, and SQLite integrity."""
    if not backup_path.is_file():
        raise BackupError(f"Backup file does not exist: {backup_path}")
    with tempfile.TemporaryDirectory(prefix="wonderlearn-validate-") as temporary:
        _set_private_directory(Path(temporary))
        database_path = Path(temporary) / DATABASE_MEMBER
        manifest = _extract_and_check(backup_path, database_path)
    return {
        "valid": True,
        "created_at": manifest.get("created_at"),
        "schema_version": manifest["schema_version"],
        "database_size_bytes": manifest["database"]["size_bytes"],
        "database_sha256": manifest["database"]["sha256"],
    }


def restore_backup(
    backup_path: Path, database_path: Path, *, dry_run: bool = False
) -> dict[str, Any]:
    """Restore only to a new database path; never replace an existing/live database."""
    target = database_path.absolute()
    if target.exists() or target.is_symlink():
        raise BackupError("Restore destination already exists; refusing to overwrite it")
    parent = target.parent
    if dry_run:
        with tempfile.TemporaryDirectory(prefix="wonderlearn-restore-") as temporary:
            _set_private_directory(Path(temporary))
            restored_database = Path(temporary) / DATABASE_MEMBER
            manifest = _extract_and_check(backup_path, restored_database)
        return {
            "valid": True,
            "restored": False,
            "destination": target,
            "schema_version": manifest["schema_version"],
        }

    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".wonderlearn-restore-", dir=parent) as temporary:
        _set_private_directory(Path(temporary))
        restored_database = Path(temporary) / DATABASE_MEMBER
        manifest = _extract_and_check(backup_path, restored_database)
        _set_private_file(restored_database)
        try:
            os.link(restored_database, target)
        except FileExistsError as exc:
            raise BackupError(
                "Restore destination appeared during validation; refusing to overwrite it"
            ) from exc
        except OSError:
            shutil.copy2(restored_database, target)

    return {
        "valid": True,
        "restored": True,
        "destination": target,
        "schema_version": manifest["schema_version"],
    }


def prune_backups(backup_directory: Path, *, keep_last: int) -> list[Path]:
    """Remove only this tool's direct-child archives, preserving the newest N."""
    if keep_last <= 0:
        raise ValueError("keep_last must be positive")
    if backup_directory.is_symlink():
        raise BackupError("Backup location must not be a symbolic link")
    directory = backup_directory.resolve()
    if not directory.is_dir():
        raise BackupError(f"Backup directory does not exist: {directory}")

    candidates = sorted(
        (
            entry
            for entry in directory.iterdir()
            if BACKUP_NAME.fullmatch(entry.name) and not entry.is_symlink() and entry.is_file()
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    removed = candidates[keep_last:]
    for path in removed:
        path.unlink()
    return removed


def _settings() -> Settings:
    return Settings()


def main(argv: list[str] | None = None) -> int:
    settings = _settings()
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    create_parser = commands.add_parser("create", help="create a consistent database backup")
    create_parser.add_argument("--database", type=Path, default=settings.database_path)
    create_parser.add_argument("--backup-dir", type=Path, default=settings.backup_directory)
    create_parser.add_argument(
        "--retention-count", type=int, default=settings.backup_retention_count
    )

    validate_parser = commands.add_parser("validate", help="validate a backup archive")
    validate_parser.add_argument("backup_file", type=Path)

    restore_parser = commands.add_parser("restore", help="restore to a new database file")
    restore_parser.add_argument("backup_file", type=Path)
    restore_parser.add_argument("--database", type=Path, required=True)
    restore_parser.add_argument("--dry-run", action="store_true")

    prune_parser = commands.add_parser("prune", help="apply backup retention")
    prune_parser.add_argument("--backup-dir", type=Path, default=settings.backup_directory)
    prune_parser.add_argument(
        "--retention-count", type=int, default=settings.backup_retention_count
    )
    args = parser.parse_args(argv)

    try:
        if args.command in {"create", "prune"} and args.retention_count <= 0:
            raise ValueError("retention count must be positive")
        if args.command == "create":
            result = create_backup(args.database, args.backup_dir)
            removed = prune_backups(args.backup_dir, keep_last=args.retention_count)
            result["removed_backups"] = [path.name for path in removed]
        elif args.command == "validate":
            result = validate_backup(args.backup_file)
        elif args.command == "restore":
            result = restore_backup(args.backup_file, args.database, dry_run=args.dry_run)
        else:
            removed = prune_backups(args.backup_dir, keep_last=args.retention_count)
            result = {"removed_backups": [path.name for path in removed]}
    except (BackupError, OSError, ValueError) as exc:
        print(f"backup operation failed: {exc}", file=sys.stderr)
        return 2

    serializable = {
        key: str(value) if isinstance(value, Path) else value for key, value in result.items()
    }
    print(json.dumps(serializable, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
