from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI


def collect_frontend_api_paths(frontend_root: Path) -> set[str]:
    """Collect /api/v1 paths referenced by the React frontend fetch calls."""
    pattern = re.compile(r'fetch\s*\(\s*["\'](/api/v1/[^"\']+)["\']')
    paths: set[str] = set()
    for file_path in frontend_root.rglob("*.ts"):
        text = file_path.read_text(encoding="utf-8")
        paths.update(pattern.findall(text))
    for file_path in frontend_root.rglob("*.tsx"):
        text = file_path.read_text(encoding="utf-8")
        paths.update(pattern.findall(text))
    return paths


def collect_openapi_api_paths(spec: dict[str, Any]) -> set[str]:
    return {path for path in spec.get("paths", {}) if path.startswith("/api/v1")}


def validate_api_contract(app: FastAPI, frontend_root: Path | None = None) -> dict[str, list[str]]:
    frontend_root = frontend_root or (Path(__file__).resolve().parents[3] / "apps" / "web" / "src")
    spec = app.openapi()
    openapi_paths = collect_openapi_api_paths(spec)
    frontend_paths = collect_frontend_api_paths(frontend_root)
    required_paths = {
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/register",
        "/api/v1/auth/me",
        "/api/v1/auth/csrf-token",
        "/api/v1/health",
        "/api/v1/ready",
        "/api/v1/documents",
        "/api/v1/images",
        "/api/v1/diagrams",
        "/api/v1/progress",
        "/api/v1/learning-analytics",
        "/api/v1/study-coach/plan",
        "/api/v1/knowledge-graph",
        "/api/v1/quizzes",
    }
    missing_from_backend = sorted(frontend_paths - openapi_paths)
    missing_required_paths = sorted(required_paths - openapi_paths)
    return {
        "missing_from_backend": missing_from_backend,
        "missing_required_paths": missing_required_paths,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the app's FastAPI OpenAPI contract against the frontend fetch routes.")
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1], help="Path to the API project root.")
    args = parser.parse_args()

    repo_root = args.project.resolve().parents[1]
    sys.path.insert(0, str(args.project))
    from app.main import app

    frontend_root = repo_root / "apps" / "web" / "src"
    result = validate_api_contract(app, frontend_root)
    missing = result["missing_from_backend"] + result["missing_required_paths"]
    if missing:
        print("API contract validation failed.")
        for value in missing:
            print(f" - {value}")
        return 1
    openapi_paths = collect_openapi_api_paths(app.openapi())
    print(f"API contract validation passed: {len(openapi_paths)} OpenAPI routes and {len(collect_frontend_api_paths(frontend_root))} frontend fetch routes are aligned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
