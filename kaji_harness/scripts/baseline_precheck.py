"""Deterministic entrypoint for pytest baseline measurement and comparison."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from kaji_harness.baseline import (
    BASELINE_SCHEMA_VERSION,
    BaselineArtifact,
    BaselineSummary,
    classify_baseline,
    compare_failures,
    evaluate_scope,
    load_artifact,
    load_plugin_report,
    save_artifact,
)
from kaji_harness.config import KajiConfig
from kaji_harness.fsio import atomic_write
from kaji_harness.providers import LocalProvider, get_provider, normalize_id

ARTIFACT_RELATIVE_PATH = Path(".kaji-artifacts/baseline/baseline.json")
REPORT_DIRECTORY = Path(".kaji-artifacts/baseline")


def _git(worktree: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run one git command in the selected worktree."""
    return subprocess.run(
        ["git", *args],
        cwd=worktree,
        check=check,
        capture_output=True,
        text=True,
    )


def _has_implementation_commit(worktree: Path, default_branch: str) -> bool:
    """Return whether HEAD contains a non-design commit beyond the default branch."""
    result = _git(
        worktree,
        "log",
        "--format=%H",
        f"{default_branch}..HEAD",
        "--",
        ".",
        ":(exclude)draft/design/**",
    )
    return bool(result.stdout.strip())


def _is_ancestor(worktree: Path, ancestor: str) -> bool:
    """Return whether one commit is an ancestor of worktree HEAD."""
    result = _git(worktree, "merge-base", "--is-ancestor", ancestor, "HEAD", check=False)
    if result.returncode not in (0, 1):
        result.check_returncode()
    return result.returncode == 0


def _is_dirty(worktree: Path) -> bool:
    """Return whether tracked or untracked worktree files are present."""
    return bool(_git(worktree, "status", "--porcelain").stdout.strip())


def _run_pytest(worktree: Path, report_path: Path) -> int:
    """Run pytest with the lossless plugin and return its exact exit code."""
    report_path.unlink(missing_ok=True)
    env = os.environ.copy()
    env["KAJI_BASELINE_REPORT_PATH"] = str(report_path)
    try:
        result = subprocess.run(
            [
                str(worktree / ".venv" / "bin" / "python"),
                "-m",
                "pytest",
                "-p",
                "kaji_harness.pytest_baseline_plugin",
                "-n",
                "0",
            ],
            cwd=worktree,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        sys.stderr.write(f"pytest launch failed: {type(exc).__name__}: {exc}\n")
        return 127
    if result.stdout:
        sys.stderr.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def _artifact_path(worktree: Path) -> Path:
    """Return the fixed branch-scoped artifact path."""
    return worktree / ARTIFACT_RELATIVE_PATH


def _build_invalid_artifact(
    *,
    issue_id: str,
    branch: str,
    measured_commit: str,
    exit_code: int,
    stop_reason: str,
) -> BaselineArtifact:
    """Build a validated invalid artifact for a completed measurement attempt."""
    return BaselineArtifact(
        schema_version=BASELINE_SCHEMA_VERSION,
        issue_id=issue_id,
        branch=branch,
        measured_commit=measured_commit,
        measured_at=datetime.now(UTC),
        pytest_exit_code=exit_code,
        summary=BaselineSummary(collected=0, passed=0, failed=0, errors=0, skipped=0),
        status="invalid",
        stop_reason=stop_reason,
        failures=[],
    )


def _format_comment(artifact: BaselineArtifact) -> str:
    """Render a deterministic evidence comment from one artifact."""
    lines = [
        "## Baseline Check 結果",
        "",
        "### 実行環境",
        "",
        f"- **Commit**: `{artifact.measured_commit}`",
        "- **コマンド**: `pytest`",
        f"- **Status**: `{artifact.status}`",
        f"- **pytest exit code**: `{artifact.pytest_exit_code}`",
        "",
        "### Baseline Failure 一覧",
        "",
        "| nodeid | kind | error_type | 概要 |",
        "|--------|------|------------|------|",
    ]
    for failure in artifact.failures:
        message = failure.message_head.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{failure.nodeid}` | {failure.kind} | {failure.error_type} | {message} |")
    if not artifact.failures:
        lines.append("| — | — | — | failure report なし |")
    lines.extend(
        [
            "",
            "### Regression 判定キー",
            "",
            "artifact の `(nodeid, kind, error_type)` を比較キーとする。",
        ]
    )
    if artifact.stop_reason:
        lines.extend(["", f"- **停止理由**: `{artifact.stop_reason}`"])
    return "\n".join(lines) + "\n"


def _post_comment(worktree: Path, issue_id: str, artifact: BaselineArtifact) -> None:
    """Post baseline evidence through the active provider and commit local comments."""
    config = KajiConfig.discover(worktree)
    provider = get_provider(config)
    comment = provider.comment_issue(issue_id, _format_comment(artifact))
    if isinstance(provider, LocalProvider):
        resolved = normalize_id(
            issue_id,
            provider_name="local",
            machine_id=provider.machine_id,
        )
        provider.commit_issue_change(
            resolved,
            "comment",
            [Path("comments") / f"{comment.seq}-{comment.machine_id}.md"],
        )


def _verdict_fields(
    artifact: BaselineArtifact,
    *,
    reused: bool = False,
) -> dict[str, str]:
    """Map artifact status to the workflow verdict contract."""
    status = "PASS" if artifact.status in {"clean", "known_failures"} else "ABORT"
    evidence = (
        f"baseline_status={artifact.status}; measured_commit={artifact.measured_commit}; "
        f"pytest_exit_code={artifact.pytest_exit_code}; failures={len(artifact.failures)}; "
        f"reused={str(reused).lower()}"
    )
    suggestion = ""
    if status == "ABORT":
        suggestion = (
            "stop_reason を解消して baseline step を再実行する。実装 commit 後に artifact が"
            "失われた場合は、commit を別 branch へ退避して再測定するか、人間が baseline なしの"
            "扱いを判断する。"
        )
    return {
        "status": status,
        "reason": f"deterministic baseline classification: {artifact.status}",
        "evidence": evidence,
        "suggestion": suggestion,
    }


def _emit_verdict(fields: dict[str, str], verdict_path: Path | None) -> None:
    """Write artifact-primary pure YAML, then emit the compatible stdout block."""
    pure_yaml = yaml.safe_dump(fields, allow_unicode=True, sort_keys=False)
    if verdict_path is not None:
        atomic_write(verdict_path, pure_yaml)
    sys.stdout.write("---VERDICT---\n" + pure_yaml + "---END_VERDICT---\n")


def _emit_guard_abort(reason: str, evidence: str, verdict_path: Path | None) -> int:
    """Emit a policy ABORT without creating or overwriting the baseline artifact."""
    _emit_verdict(
        {
            "status": "ABORT",
            "reason": reason,
            "evidence": evidence,
            "suggestion": (
                "working tree を clean にして再実行する。実装 commit 後に変更前 baseline が"
                "復元不能なら、commit を退避して再測定するか人間判断を行う。"
            ),
        },
        verdict_path,
    )
    return 0


def _measure(
    *,
    worktree: Path,
    issue_id: str,
    branch: str,
    default_branch: str,
    verdict_path: Path | None,
) -> int:
    """Measure or safely reuse the pre-implementation baseline."""
    artifact_path = _artifact_path(worktree)
    if _has_implementation_commit(worktree, default_branch):
        try:
            artifact = load_artifact(artifact_path)
        except (FileNotFoundError, ValidationError, ValueError):
            return _emit_guard_abort(
                "baseline_unrecoverable_post_implement",
                "implementation commit exists and no valid baseline artifact can be reused",
                verdict_path,
            )
        if not _is_ancestor(worktree, artifact.measured_commit):
            return _emit_guard_abort(
                "baseline_unrecoverable_post_implement",
                "artifact measured_commit is not an ancestor of current HEAD",
                verdict_path,
            )
        if artifact.status != "clean":
            _post_comment(worktree, issue_id, artifact)
        _emit_verdict(_verdict_fields(artifact, reused=True), verdict_path)
        return 0

    if _is_dirty(worktree):
        return _emit_guard_abort(
            "dirty_worktree",
            "git status --porcelain returned implementation-uncommitted paths",
            verdict_path,
        )

    measured_commit = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    report_path = worktree / REPORT_DIRECTORY / "report-measure.json"
    exit_code = _run_pytest(worktree, report_path)
    try:
        report = load_plugin_report(report_path)
    except (FileNotFoundError, ValidationError, ValueError):
        artifact = _build_invalid_artifact(
            issue_id=issue_id,
            branch=branch,
            measured_commit=measured_commit,
            exit_code=exit_code,
            stop_reason="report_missing_or_invalid",
        )
    else:
        status, stop_reason = classify_baseline(exit_code, report.summary, report.failures)
        artifact = BaselineArtifact(
            schema_version=BASELINE_SCHEMA_VERSION,
            issue_id=issue_id,
            branch=branch,
            measured_commit=measured_commit,
            measured_at=datetime.now(UTC),
            pytest_exit_code=exit_code,
            summary=report.summary,
            status=status,
            stop_reason=stop_reason,
            failures=report.failures,
        )
    save_artifact(artifact_path, artifact)
    if artifact.status != "clean":
        _post_comment(worktree, issue_id, artifact)
    _emit_verdict(_verdict_fields(artifact), verdict_path)
    return 0


def _evaluate(worktree: Path, scopes: list[str]) -> int:
    """Print the deterministic same-area evaluation as one JSON object."""
    artifact_path = _artifact_path(worktree)
    try:
        artifact = load_artifact(artifact_path)
    except (FileNotFoundError, ValidationError, ValueError):
        print(json.dumps({"verdict": "missing_baseline", "stop": True, "overlapping": []}))
        return 0
    if not _is_ancestor(worktree, artifact.measured_commit):
        print(json.dumps({"verdict": "stale_baseline", "stop": True, "overlapping": []}))
        return 0
    print(evaluate_scope(artifact, scopes).model_dump_json())
    return 0


def _compare(worktree: Path) -> int:
    """Run current pytest and print a deterministic regression comparison."""
    artifact_path = _artifact_path(worktree)
    try:
        artifact = load_artifact(artifact_path)
    except (FileNotFoundError, ValidationError, ValueError):
        print(json.dumps({"verdict": "missing_baseline", "regressions": []}))
        return 0
    if not _is_ancestor(worktree, artifact.measured_commit):
        print(json.dumps({"verdict": "stale_baseline", "regressions": []}))
        return 0
    report_path = worktree / REPORT_DIRECTORY / "report-compare.json"
    exit_code = _run_pytest(worktree, report_path)
    try:
        report = load_plugin_report(report_path)
    except (FileNotFoundError, ValidationError, ValueError):
        print(
            json.dumps(
                {
                    "verdict": "regression",
                    "regressions": ["pytest report missing or invalid"],
                    "current_exit_code": exit_code,
                }
            )
        )
        return 0
    comparison = compare_failures(artifact.failures, report.failures)
    payload = comparison.model_dump(mode="json")
    current_status, current_stop_reason = classify_baseline(
        exit_code,
        report.summary,
        report.failures,
    )
    if current_status in {"blocked", "invalid"}:
        payload["verdict"] = "regression"
        payload["regressions"] = [
            *payload["regressions"],
            current_stop_reason or current_status,
        ]
    payload.update(
        {
            "current_exit_code": exit_code,
            "measured_commit": artifact.measured_commit,
            "current_commit": _git(worktree, "rev-parse", "HEAD").stdout.strip(),
        }
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse mode and environment-overridable entrypoint arguments."""
    parser = argparse.ArgumentParser(prog="baseline-precheck")
    parser.add_argument("--worktree", default=os.environ.get("KAJI_WORKTREE_DIR"))
    parser.add_argument("--issue", default=os.environ.get("KAJI_ISSUE_ID"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--evaluate", action="store_true")
    modes.add_argument("--compare", action="store_true")
    parser.add_argument("--scope", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Dispatch measure, evaluate, or compare mode."""
    args = _parse_args(argv)
    if not args.worktree:
        raise ValueError("KAJI_WORKTREE_DIR or --worktree is required")
    worktree = Path(args.worktree).resolve(strict=True)
    if args.evaluate:
        if not args.scope:
            raise ValueError("--evaluate requires at least one --scope")
        return _evaluate(worktree, list(args.scope))
    if args.compare:
        return _compare(worktree)
    if not args.issue:
        raise ValueError("KAJI_ISSUE_ID or --issue is required in measure mode")
    verdict_path_raw = os.environ.get("KAJI_VERDICT_PATH")
    verdict_path = Path(verdict_path_raw) if verdict_path_raw else None
    branch = (
        os.environ.get("KAJI_BRANCH_NAME")
        or _git(worktree, "branch", "--show-current").stdout.strip()
    )
    default_branch = os.environ.get("KAJI_DEFAULT_BRANCH", "main")
    return _measure(
        worktree=worktree,
        issue_id=str(args.issue),
        branch=branch,
        default_branch=default_branch,
        verdict_path=verdict_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
