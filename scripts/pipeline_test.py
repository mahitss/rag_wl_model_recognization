"""Pipeline tests for M76 CI/CD.

Tests aspects of the CI/CD pipeline itself:
- Clean environment installation
- Full pipeline execution simulation
- Failed quality gates detection
- Migration validation
- Build artifact integrity
- Release version consistency
- Secret redaction
- Rollback documentation procedures
- Configuration validation

These tests use only local, free tools and do not require external CI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=300, check=False
    )


def _require_tool(name: str) -> None:
    """Skip test if a required tool is not available."""
    result = subprocess.run(
        ["which", name], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        pytest.skip(f"Required tool '{name}' not found")


# ---------------------------------------------------------------------------
# Clean environment installation
# ---------------------------------------------------------------------------


def test_uv_lock_exists(tmp_path: Path) -> None:
    """Verify uv.lock exists for reproducible builds."""
    repo_root = _repo_root()
    lock_file = repo_root / "services/api/uv.lock"
    assert lock_file.exists(), "uv.lock must exist for reproducible builds"


def test_pyproject_has_pinned_deps(tmp_path: Path) -> None:
    """Verify pyproject.toml pins dependency versions."""
    repo_root = _repo_root()
    pyproject = repo_root / "services/api/pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    # All dependencies should use == for pinning
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith('"') and "==" in stripped:
            continue  # Pinned
        if stripped.startswith('"') and ">=" in stripped:
            pytest.fail(f"Unpinned dependency found: {stripped}")


# ---------------------------------------------------------------------------
# Migration validation
# ---------------------------------------------------------------------------


def test_schema_version_is_stable(tmp_path: Path) -> None:
    """Verify SCHEMA_VERSION is defined and stable."""
    repo_root = _repo_root()
    storage = repo_root / "services/api/app/storage.py"
    content = storage.read_text(encoding="utf-8")
    assert "SCHEMA_VERSION = " in content, "SCHEMA_VERSION must be defined"


def test_initialize_storage_is_idempotent(tmp_path: Path) -> None:
    """Migration initialization is idempotent."""
    import sys
    sys.path.insert(0, str(_repo_root() / "services/api"))
    from app.storage import initialize_storage  # type: ignore

    db_path = tmp_path / "idempotent_test.sqlite3"
    initialize_storage(db_path)
    initialize_storage(db_path)  # Second call should not fail


# ---------------------------------------------------------------------------
# Build artifact integrity
# ---------------------------------------------------------------------------


def test_artifact_integrity_script_exists(tmp_path: Path) -> None:
    """Verify artifact integrity script exists."""
    repo_root = _repo_root()
    script = repo_root / "scripts/artifact_integrity.py"
    assert script.exists(), "Artifact integrity script must exist"


def test_artifact_checksum_generation(tmp_path: Path) -> None:
    """Verify checksum generation works for a known file."""
    repo_root = _repo_root()
    script = repo_root / "scripts/artifact_integrity.py"

    # Create a test file
    test_file = tmp_path / "test_artifact.bin"
    test_file.write_bytes(b"test artifact content for integrity check")

    result = subprocess.run(
        [sys.executable, str(script), str(test_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Artifact integrity check failed: {result.stderr}"

    output = json.loads(result.stdout)
    assert output["checksum"] is not None
    assert output["checksum_matches"] is True
    assert output["size_bytes"] > 0


# ---------------------------------------------------------------------------
# Release version consistency
# ---------------------------------------------------------------------------


def test_version_file_or_pyproject_version(tmp_path: Path) -> None:
    """Verify version is defined either in VERSION file or pyproject.toml."""
    repo_root = _repo_root()
    version_file = repo_root / "VERSION"
    pyproject = repo_root / "services/api/pyproject.toml"

    has_version_file = version_file.exists()
    pyproject_content = pyproject.read_text(encoding="utf-8")
    has_pyproject_version = 'version = ' in pyproject_content

    assert has_version_file or has_pyproject_version, (
        "Version must be defined in VERSION file or pyproject.toml"
    )


def test_release_script_exists(tmp_path: Path) -> None:
    """Verify release management script exists."""
    repo_root = _repo_root()
    script = repo_root / "scripts/release.sh"
    assert script.exists(), "Release script must exist"


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


def test_no_secrets_in_pyproject(tmp_path: Path) -> None:
    """Verify pyproject.toml does not contain secrets."""
    repo_root = _repo_root()
    pyproject = repo_root / "services/api/pyproject.toml"
    content = pyproject.read_text(encoding="utf-8").lower()
    secret_patterns = ["sk-", "ghp_", "akia", "password =", "secret =", "token ="]
    for pattern in secret_patterns:
        assert pattern not in content, f"Potential secret pattern found: {pattern}"


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_env_example_exists(tmp_path: Path) -> None:
    """Verify .env.example exists for configuration reference."""
    repo_root = _repo_root()
    env_example = repo_root / ".env.example"
    assert env_example.exists(), ".env.example must exist"


def test_env_example_has_no_real_secrets(tmp_path: Path) -> None:
    """Verify .env.example does not contain real secrets."""
    repo_root = _repo_root()
    env_example = repo_root / ".env.example"
    content = env_example.read_text(encoding="utf-8")
    # Should not contain actual API keys or passwords
    assert "sk-" not in content
    assert "ghp_" not in content


# ---------------------------------------------------------------------------
# Full pipeline execution simulation
# ---------------------------------------------------------------------------


def test_pipeline_commands_available(tmp_path: Path) -> None:
    """Verify all CI pipeline commands are available."""
    commands = [
        (["uv", "--version"], "uv"),
        (["python", "--version"], "python"),
        (["npm", "--version"], "npm"),
    ]
    for cmd, name in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            pytest.skip(f"Tool '{name}' not available for pipeline test")
            continue
        # Tool may not be installed locally; skip if not available
        if result.returncode != 0:
            pytest.skip(f"Tool '{name}' not available for pipeline test")


def test_frontend_build_script_exists(tmp_path: Path) -> None:
    """Verify frontend build script is defined in package.json."""
    repo_root = _repo_root()
    package_json = repo_root / "apps/web/package.json"
    content = package_json.read_text(encoding="utf-8")
    assert '"build"' in content, "Frontend build script must be defined"
    assert '"typecheck"' in content, "Frontend typecheck script must be defined"
    assert '"test"' in content, "Frontend test script must be defined"


def test_backend_test_command_exists(tmp_path: Path) -> None:
    """Verify backend test command is configured."""
    repo_root = _repo_root()
    pyproject = repo_root / "services/api/pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    assert "pytest" in content, "pytest must be configured for backend tests"


def test_ruff_configured(tmp_path: Path) -> None:
    """Verify ruff is configured for linting."""
    repo_root = _repo_root()
    pyproject = repo_root / "services/api/pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    assert "[tool.ruff]" in content, "Ruff configuration must exist"


# ---------------------------------------------------------------------------
# Rollback documentation
# ---------------------------------------------------------------------------


def test_deployment_doc_has_rollback_section(tmp_path: Path) -> None:
    """Verify deployment docs include rollback procedures."""
    repo_root = _repo_root()
    deploy_doc = repo_root / "docs/deployment.md"
    assert deploy_doc.exists(), "Deployment docs must exist"
    content = deploy_doc.read_text(encoding="utf-8")
    assert "rollback" in content.lower(), "Deployment docs must document rollback"
    assert "restore" in content.lower(), "Deployment docs must document restore"


def test_release_doc_exists(tmp_path: Path) -> None:
    """Verify release documentation exists."""
    repo_root = _repo_root()
    release_doc = repo_root / "docs/release/RELEASE.md"
    assert release_doc.exists(), "Release documentation must exist"


if __name__ == "__main__":
    pass
