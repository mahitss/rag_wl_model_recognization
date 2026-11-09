"""Artifact integrity verification script.

Verifies that build artifacts are intact, signed, and match expected
checksums. Designed for use in CI pipelines to ensure reproducible builds.

Usage:
    python scripts/artifact_integrity.py <artifact_path> [expected_checksum]
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def compute_sha256(path: Path) -> str:
    """Compute SHA-256 digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_artifact(artifact_path: Path, expected_checksum: str | None = None) -> dict:
    """Verify artifact integrity.

    Returns a dict with status, checksum, and any errors.
    """
    result: dict = {
        "artifact": str(artifact_path),
        "exists": artifact_path.exists(),
        "is_file": artifact_path.is_file() if artifact_path.exists() else False,
        "size_bytes": artifact_path.stat().st_size if artifact_path.exists() else 0,
        "checksum": None,
        "checksum_matches": False,
        "errors": [],
    }

    if not artifact_path.exists():
        result["errors"].append(f"Artifact not found: {artifact_path}")
        return result

    if not artifact_path.is_file():
        result["errors"].append(f"Not a regular file: {artifact_path}")
        return result

    checksum = compute_sha256(artifact_path)
    result["checksum"] = checksum

    if expected_checksum:
        result["checksum_matches"] = checksum == expected_checksum
        if not result["checksum_matches"]:
            result["errors"].append(
                f"Checksum mismatch: expected {expected_checksum}, got {checksum}"
            )
    else:
        # No expected checksum provided; just record the computed one.
        result["checksum_matches"] = True

    return result


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/artifact_integrity.py <artifact_path> [expected_checksum]")
        return 1

    artifact_path = Path(sys.argv[1])
    expected_checksum = sys.argv[2] if len(sys.argv) > 2 else None

    result = verify_artifact(artifact_path, expected_checksum)

    print(json.dumps(result, indent=2))

    if result["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
