"""Run WonderLearn's offline regression suite and synthetic retrieval benchmark."""

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "services" / "api"
DATASET = ROOT / "evaluations" / "semantic" / "retrieval_eval.json"
OUTPUT = ROOT / "evaluations" / "generated"
sys.path.insert(0, str(API))


def run(command: list[str], *, env: dict[str, str], cwd: Path = ROOT) -> dict:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "command": command,
        "exitCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def test_counts(output: str) -> dict[str, int]:
    counts = {name: 0 for name in ("passed", "failed", "skipped", "errors")}
    summary = re.findall(r"(\d+) (passed|failed|skipped|error|errors)", output)
    for number, name in summary:
        key = "errors" if name in {"error", "errors"} else name
        counts[key] += int(number)
    return counts


def version(command: str) -> str | None:
    arguments = (
        ["cmd.exe", "/d", "/c", command, "--version"]
        if os.name == "nt" and command in {"npm", "npx"}
        else [command, "--version"]
    )
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return (result.stdout or result.stderr).strip() if result.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-model",
        action="store_true",
        help="Also run local semantic/hybrid retrieval using only the already-cached model.",
    )
    parser.add_argument(
        "--include-model-tests",
        action="store_true",
        help="Explicitly enable tests marked integration that require a locally cached model.",
    )
    parser.add_argument(
        "--quality",
        action="store_true",
        help="Also run the M18 real-world quality evaluation (lexical-only by default; use --with-model for semantic/hybrid).",
    )
    args = parser.parse_args()
    env = os.environ.copy()
    env.pop("WL_RUN_MODEL_INTEGRATION", None)
    if args.include_model_tests:
        env["WL_RUN_MODEL_INTEGRATION"] = "1"

    test_command = [sys.executable, "-m", "pytest", "-q"]
    tests = run(test_command, env=env)
    retrieval_command = [
        sys.executable,
        str(ROOT / "evaluations" / "semantic" / "evaluate_retrieval.py"),
    ]
    if not args.with_model:
        retrieval_command.append("--lexical-only")
    retrieval = run(retrieval_command, env=env)
    retrieval_report = None
    if retrieval["exitCode"] == 0:
        try:
            retrieval_report = json.loads(retrieval["stdout"])
        except json.JSONDecodeError:
            retrieval["exitCode"] = 1
            retrieval["stderr"] += "\nRetrieval evaluator did not emit valid JSON."

    quality_report = None
    if args.quality:
        quality_command = [
            sys.executable,
            str(ROOT / "evaluations" / "realworld" / "evaluate_quality.py"),
        ]
        if not args.with_model:
            quality_command.append("--lexical-only")
        quality = run(quality_command, env=env)
        if quality["exitCode"] == 0:
            try:
                quality_report = json.loads(quality["stdout"])
            except json.JSONDecodeError:
                quality["exitCode"] = 1
                quality["stderr"] += "\nQuality evaluator did not emit valid JSON."
    else:
        quality = None

    try:
        from app.config import get_settings

        settings = get_settings()
        tutor = {
            "configured": settings.tutor_configured,
            "provider": "local_loopback_openai_compatible"
            if settings.tutor_configured
            else "unconfigured",
            "modelId": settings.tutor_model_id if settings.tutor_configured else None,
            "qualityEvaluated": False,
            "contractCoverage": [
                "retrieved evidence citation allow-list",
                "model abstention format",
                "missing evidence and provider unavailable",
                "malformed model responses",
                "quiz schema, citation, and lexical support validation",
                "deterministic multiple-choice and ambiguous short-answer scoring",
            ],
            "note": "Tutor/quiz safety contracts are covered by deterministic regression tests; no generated-answer quality score is claimed.",
        }
    except (OSError, ValidationError) as exc:
        tutor = {"configurationReadable": False, "errorType": type(exc).__name__}

    commit = run(["git", "rev-parse", "HEAD"], env=env)
    try:
        import importlib.metadata
        import sqlite3

        package_versions = {
            name: importlib.metadata.version(name)
            for name in ("fastapi", "pytest", "ruff", "fastembed", "onnxruntime")
            if _is_installed(name)
        }
        sqlite_version = sqlite3.sqlite_version
    except OSError:
        package_versions = {}
        sqlite_version = None

    frontend_versions = {}
    frontend_package_file = ROOT / "apps" / "web" / "package.json"
    if frontend_package_file.is_file():
        frontend_package = json.loads(frontend_package_file.read_text(encoding="utf-8"))
        frontend_versions = {
            package: frontend_package.get(section, {}).get(package)
            for section, package in (
                ("dependencies", "react"),
                ("dependencies", "react-dom"),
                ("devDependencies", "typescript"),
                ("devDependencies", "vite"),
                ("devDependencies", "vitest"),
            )
        }
    report = {
        "reportVersion": "1",
        "generatedAtUtc": datetime.now(UTC).isoformat(timespec="seconds"),
        "sourceCommit": commit["stdout"].strip() if commit["exitCode"] == 0 else None,
        "entryCommand": "uv run --locked --project services/api --extra dev --extra semantic python evaluations/run_suite.py",
        "entryArguments": sys.argv[1:],
        "testEnvironment": {
            "WL_RUN_MODEL_INTEGRATION": env.get("WL_RUN_MODEL_INTEGRATION", "unset")
        },
        "dataset": {
            "path": "evaluations/semantic/retrieval_eval.json",
            "version": retrieval_report.get("datasetVersion")
            if retrieval_report
            else None,
            "kind": retrieval_report.get("datasetKind")
            if retrieval_report
            else "synthetic",
            "sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
            "queries": len(json.loads(DATASET.read_text(encoding="utf-8"))["queries"]),
        },
        "runtime": {
            "python": platform.python_version(),
            "sqlite": sqlite_version,
            "platform": platform.platform(),
            "node": version("node"),
            "npm": version("npm"),
            "packages": package_versions,
            "frontendPackages": frontend_versions,
        },
        "tutorQuizEvaluation": tutor,
        "commands": {
            "backendTests": {
                "command": tests["command"],
                "exitCode": tests["exitCode"],
                "counts": test_counts(tests["stdout"] + tests["stderr"]),
                "summary": (tests["stdout"] + tests["stderr"])[-8000:],
            },
            "retrieval": {
                "command": retrieval["command"],
                "exitCode": retrieval["exitCode"],
                "report": retrieval_report,
                "stderr": retrieval["stderr"][-4000:],
            },
            "quality": (
                {
                    "command": quality["command"],
                    "exitCode": quality["exitCode"],
                    "report": quality_report,
                    "stderr": quality["stderr"][-4000:],
                }
                if quality is not None
                else None
            ),
        },
        "workflowRegression": {
            "coverage": "The backend suite includes an API journey for PDF ingestion, retrieval, diagram evidence, tutor-backed quiz generation, answer submission, study session, study coach, analytics, and source deletion.",
            "simulatedProvider": "The workflow regression stubs the loopback model response; it does not measure model answer quality.",
        },
        "interpretation": "Synthetic and deterministic regression results are kept separate from real-world evaluation. No quality threshold, semantic improvement, or learning outcome is inferred.",
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT / "latest.json"
    markdown_path = OUTPUT / "latest.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    print(f"Evaluation JSON: {json_path.relative_to(ROOT)}")
    print(f"Evaluation report: {markdown_path.relative_to(ROOT)}")
    print(markdown_path.read_text(encoding="utf-8"))
    return 0 if tests["exitCode"] == 0 and retrieval["exitCode"] == 0 else 1


def _is_installed(package: str) -> bool:
    import importlib.metadata

    try:
        importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def _markdown(report: dict) -> str:
    tests = report["commands"]["backendTests"]
    retrieval = report["commands"]["retrieval"]
    lines = [
        "# WonderLearn evaluation report",
        "",
        f"- Generated: {report['generatedAtUtc']}",
        f"- Commit: `{report['sourceCommit'] or 'unavailable'}`",
        f"- Dataset: `{report['dataset']['version'] or 'unavailable'}` ({report['dataset']['kind']}), SHA-256 `{report['dataset']['sha256']}`",
        f"- Backend tests: exit {tests['exitCode']}; {tests['counts']}",
        f"- Retrieval evaluation: exit {retrieval['exitCode']}",
        f"- Tutor configured: {report['tutorQuizEvaluation'].get('configured', 'unknown')}; answer quality scored: no",
        "",
        "## Retrieval metrics",
        "",
    ]
    metrics = retrieval.get("report")
    if metrics:
        lines.append(
            f"Evaluation mode: `{metrics['evaluationMode']}`. Synthetic smoke set only."
        )
        lines.append("")
        lines.append(
            "| Mode | Recall@5 | Precision@5 | MRR | Citation validity | Unsupported abstention recall | Answerable evidence coverage |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for mode, values in metrics["metrics"].items():
            lines.append(
                f"| {mode} | {values['recallAt5']:.3f} | {values['precisionAt5']:.3f} | "
                f"{values['meanReciprocalRank']:.3f} | {values['citationValidityRate']} | "
                f"{values['unanswerableAbstentionRecall']} | {values['answerableEvidenceCoverage']} |"
            )
    else:
        lines.append(
            "No retrieval metrics were produced; inspect the JSON command error details."
        )
    lines += ["", report["interpretation"], ""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
