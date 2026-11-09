"""Secret detection tests.

Verifies that no secrets, API keys, tokens, or credentials are committed
to the repository. These tests scan tracked files for common secret
patterns and fail if any are found.

These tests complement CI secret scanning (trufflehog) by providing
fast, deterministic local checks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Patterns that indicate potential secrets/commit credentials.
SECRET_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"][^'\"\\s]{8,}"), "API key"),
    (re.compile(r"(?i)(secret[_-]?key|secretkey)\s*[:=]\s*['\"][^'\"\\s]{8,}"), "Secret key"),
    (re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"\\s]{8,}"), "Password"),
    (re.compile(r"(?i)(token|access[_-]?token)\s*[:=]\s*['\"][^'\"\\s]{16,}"), "Access token"),
    (re.compile(r"(?i)(sk-[a-zA-Z0-9]{20,})"), "OpenAI-style key"),
    (re.compile(r"(?i)(ghp_[a-zA-Z0-9]{20,})"), "GitHub token"),
    (re.compile(r"(?i)(AKIA[0-9A-Z]{16})"), "AWS access key"),
    (re.compile(r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----)"), "Private key"),
    (re.compile(r"(?i)(docker[_-]?hub[_-]?token|docker[_-]?password)\s*[:=]"), "Docker token"),
]

# Lines that are NOT secrets (GitHub Actions references, etc.)
NOT_SECRET_PATTERNS = [
    re.compile(r"\$\{\{ secrets\."),  # GitHub Actions secret reference
    re.compile(r"\$\{\{ github\."),    # GitHub Actions context
]

# Files and directories to exclude from scanning.
EXCLUDE_PATTERNS = [
    ".git/",
    ".env",
    ".env.example",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    "node_modules/",
    ".venv/",
    "venv/",
    "dist/",
    "build/",
    "*.pyc",
    "*.pyo",
    "*.egg-info/",
    "graphify-out/",
]


def _should_exclude(file_path: Path, root: Path) -> bool:
    relative = file_path.relative_to(root).as_posix()
    for pattern in EXCLUDE_PATTERNS:
        if pattern in relative:
            return True
    return False


def _scan_file_for_secrets(file_path: Path) -> list[tuple[int, str, str]]:
    """Scan a single file for secret patterns."""
    findings: list[tuple[int, str, str]] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return findings

    for line_num, line in enumerate(content.splitlines(), 1):
        # Skip lines that are GitHub Actions secret references
        if any(p.search(line) for p in NOT_SECRET_PATTERNS):
            continue
        for pattern, name in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append((line_num, name, line.strip()))
    return findings


def _collect_repo_files(root: Path) -> list[Path]:
    """Collect all text files in the repo, excluding known non-secret files."""
    files: list[Path] = []
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        if _should_exclude(file_path, root):
            continue
        # Skip binary files and large files
        if file_path.stat().st_size > 1_000_000:
            continue
        try:
            file_path.read_bytes()[:1]
        except OSError:
            continue
        files.append(file_path)
    return files


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_no_secrets_in_repository(repo_root: Path) -> None:
    """Fail if any committed file contains potential secrets."""
    files = _collect_repo_files(repo_root)
    all_findings: list[tuple[str, int, str, str]] = []  # (file, line, pattern, content)

    for file_path in files:
        findings = _scan_file_for_secrets(file_path)
        for line_num, pattern_name, line_content in findings:
            all_findings.append(
                (str(file_path.relative_to(repo_root)), line_num, pattern_name, line_content)
            )

    assert not all_findings, (
        f"Found {len(all_findings)} potential secret(s) in repository:\n"
        + "\n".join(
            f"  {file}:{line} [{pattern}]: {content[:80]}"
            for file, line, pattern, content in all_findings
        )
    )


def test_env_file_is_gitignored(repo_root: Path) -> None:
    """Verify .env is listed in .gitignore so secrets are not committed."""
    gitignore = repo_root / ".gitignore"
    assert gitignore.exists(), ".gitignore must exist"
    content = gitignore.read_text(encoding="utf-8")
    assert ".env" in content, ".env must be in .gitignore"
    # .env.example should be allowed (template only)
    assert "!.env.example" in content or ".env" in content


if __name__ == "__main__":
    pass
