"""Tests for deterministic pytest baseline collection and comparison."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from kaji_harness.baseline import (
    BaselineArtifact,
    BaselineFailure,
    BaselineSummary,
    classify_baseline,
    compare_failures,
    evaluate_scope,
    load_artifact,
    save_artifact,
)
from kaji_harness.providers.github import GitHubProvider
from kaji_harness.providers.local import LocalProvider
from kaji_harness.scripts import baseline_precheck
from kaji_harness.workflow import load_workflow, validate_workflow

REPO_ROOT = Path(__file__).resolve().parent.parent
# official のみを対象とする（custom variant の routing は kaji の pytest で保証しない。
# 所有権境界の意図的トレードオフ: Issue #352）。
OFFICIAL_DEV_WORKFLOWS = (
    "dev.yaml",
    "local/dev-local.yaml",
)


def _git_run(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_baseline_repo(tmp_path: Path, *, failure_count: int = 0) -> Path:
    project = tmp_path / "baseline-project"
    project.mkdir()
    os.symlink(REPO_ROOT / ".venv", project / ".venv")
    os.symlink(REPO_ROOT / "kaji_harness", project / "kaji_harness")
    (project / ".gitignore").write_text(".kaji-artifacts/\n", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
        encoding="utf-8",
    )
    tests_dir = project / "tests"
    tests_dir.mkdir()
    test_source = (
        "def test_value():\n    assert True\n"
        if failure_count == 0
        else "\n".join(
            f"def test_failure_{index}():\n    assert False" for index in range(failure_count)
        )
        + "\n"
    )
    (tests_dir / "test_value.py").write_text(test_source, encoding="utf-8")
    _git_run(project, "init", "-b", "main")
    _git_run(project, "config", "user.email", "baseline@example.invalid")
    _git_run(project, "config", "user.name", "Baseline Test")
    _git_run(project, "add", ".")
    _git_run(project, "commit", "-m", "chore: initialize fixture")
    _git_run(project, "switch", "-c", "feat/346")
    return project


def _failure(
    nodeid: str = "tests/test_example.py::test_value",
    kind: str = "FAILED",
    error_type: str = "AssertionError",
) -> BaselineFailure:
    return BaselineFailure(
        nodeid=nodeid,
        kind=kind,
        error_type=error_type,
        message_head="expected value",
    )


def _summary(*, failed: int = 0, errors: int = 0) -> BaselineSummary:
    return BaselineSummary(
        collected=max(10, failed + errors),
        passed=max(0, 10 - failed - errors),
        failed=failed,
        errors=errors,
        skipped=0,
    )


def _artifact(*, failures: list[BaselineFailure] | None = None) -> BaselineArtifact:
    records = failures or []
    status, stop_reason = classify_baseline(
        pytest_exit_code=1 if records else 0,
        summary=_summary(failed=len(records)),
        failures=records,
    )
    return BaselineArtifact(
        schema_version=1,
        issue_id="346",
        branch="feat/346",
        measured_commit="abc123",
        measured_at="2026-07-16T03:00:00+00:00",
        pytest_exit_code=1 if records else 0,
        summary=_summary(failed=len(records)),
        status=status,
        stop_reason=stop_reason,
        failures=records,
    )


@pytest.mark.small
class TestClassifyBaseline:
    @pytest.mark.parametrize(
        ("exit_code", "count", "expected_status", "expected_reason"),
        [
            (0, 0, "clean", None),
            (1, 1, "known_failures", None),
            (1, 10, "known_failures", None),
            (1, 11, "blocked", "mass_failures"),
            (6, 0, "invalid", "unexpected_exit_code:6"),
            (42, 0, "invalid", "unexpected_exit_code:42"),
            (-9, 0, "invalid", "unexpected_exit_code:-9"),
        ],
    )
    def test_status_mapping(
        self,
        exit_code: int,
        count: int,
        expected_status: str,
        expected_reason: str | None,
    ) -> None:
        failures = [_failure(nodeid=f"tests/test_x.py::test_{index}") for index in range(count)]
        status, reason = classify_baseline(
            pytest_exit_code=exit_code,
            summary=_summary(failed=count),
            failures=failures,
        )
        assert status == expected_status
        assert reason == expected_reason

    @pytest.mark.parametrize(
        ("exit_code", "summary", "failures"),
        [
            (0, _summary(failed=1), [_failure()]),
            (1, _summary(), []),
            (1, _summary(failed=1), []),
        ],
    )
    def test_inconsistent_report_is_invalid(
        self,
        exit_code: int,
        summary: BaselineSummary,
        failures: list[BaselineFailure],
    ) -> None:
        status, reason = classify_baseline(exit_code, summary, failures)
        assert status == "invalid"
        assert reason == "inconsistent_report"


@pytest.mark.small
class TestFailureComparison:
    def test_matches_regressions_and_resolved(self) -> None:
        baseline = [
            _failure(),
            _failure("tests/test_old.py::test_fixed", error_type="TypeError"),
        ]
        current = [
            _failure(),
            _failure("tests/test_new.py::test_regression"),
        ]
        comparison = compare_failures(baseline, current)
        assert comparison.matched_baseline == [baseline[0].key]
        assert comparison.regressions == [current[1].key]
        assert comparison.resolved == [baseline[1].key]

    def test_changed_error_type_is_regression_and_resolution(self) -> None:
        baseline = [_failure(error_type="AssertionError")]
        current = [_failure(error_type="TypeError")]
        comparison = compare_failures(baseline, current)
        assert comparison.regressions == [current[0].key]
        assert comparison.resolved == [baseline[0].key]


@pytest.mark.small
class TestScopeEvaluation:
    @pytest.mark.parametrize(
        "scope",
        ["tests/test_example.py", "tests"],
    )
    def test_exact_or_directory_overlap_stops(self, scope: str) -> None:
        result = evaluate_scope(_artifact(failures=[_failure()]), [scope])
        assert result.stop is True
        assert result.overlapping == ["tests/test_example.py::test_value"]

    def test_unrelated_scope_continues(self) -> None:
        result = evaluate_scope(_artifact(failures=[_failure()]), ["kaji_harness/baseline.py"])
        assert result.stop is False
        assert result.overlapping == []


@pytest.mark.medium
class TestBaselineArtifact:
    def test_atomic_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline" / "baseline.json"
        expected = _artifact(failures=[_failure()])
        save_artifact(path, expected)
        assert load_artifact(path) == expected
        assert not path.with_suffix(".json.tmp").exists()

    def test_schema_rejects_missing_and_unknown_fields(self) -> None:
        payload = _artifact().model_dump(mode="json")
        payload.pop("measured_commit")
        payload["unknown"] = True
        with pytest.raises(ValidationError):
            BaselineArtifact.model_validate(payload)


@pytest.mark.medium
def test_dev_workflows_route_design_approval_through_agentless_baseline() -> None:
    for relative_path in OFFICIAL_DEV_WORKFLOWS:
        workflow = load_workflow(REPO_ROOT / ".kaji" / "wf" / "official" / relative_path)
        validate_workflow(workflow)
        baseline = workflow.find_step("baseline")
        assert baseline is not None
        assert baseline.skill == "baseline-precheck"
        assert baseline.agent is None
        assert baseline.timeout == 1800
        assert baseline.on == {"PASS": "implement", "ABORT": "end"}
        assert workflow.find_step("review-design").on["PASS"] == "baseline"  # type: ignore[union-attr]
        assert workflow.find_step("verify-design").on["PASS"] == "baseline"  # type: ignore[union-attr]


@pytest.mark.large
@pytest.mark.large_local
def test_entrypoint_measure_compare_and_post_implementation_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _init_baseline_repo(tmp_path)
    verdict_path = tmp_path / "verdict.yaml"
    monkeypatch.setenv("KAJI_WORKTREE_DIR", str(project))
    monkeypatch.setenv("KAJI_ISSUE_ID", "346")
    monkeypatch.setenv("KAJI_BRANCH_NAME", "feat/346")
    monkeypatch.setenv("KAJI_DEFAULT_BRANCH", "main")
    monkeypatch.setenv("KAJI_VERDICT_PATH", str(verdict_path))

    assert baseline_precheck.main([]) == 0
    artifact_path = project / ".kaji-artifacts" / "baseline" / "baseline.json"
    artifact = load_artifact(artifact_path)
    assert artifact.status == "clean"
    assert artifact.measured_commit == _git_run(project, "rev-parse", "HEAD")
    assert yaml.safe_load(verdict_path.read_text(encoding="utf-8"))["status"] == "PASS"
    capsys.readouterr()

    (project / "tests" / "test_value.py").write_text(
        "def test_value():\n    assert False\n",
        encoding="utf-8",
    )
    assert baseline_precheck.main(["--compare"]) == 0
    compare_stdout = capsys.readouterr().out.splitlines()
    assert len(compare_stdout) == 1
    comparison = json.loads(compare_stdout[0])
    assert comparison["verdict"] == "regression"
    assert comparison["regressions"] == [
        "tests/test_value.py::test_value | FAILED | AssertionError"
    ]

    _git_run(project, "add", "tests/test_value.py")
    _git_run(project, "commit", "-m", "feat: introduce implementation change")
    original_text = artifact_path.read_text(encoding="utf-8")
    assert baseline_precheck.main([]) == 0
    assert artifact_path.read_text(encoding="utf-8") == original_text
    reused_verdict = yaml.safe_load(verdict_path.read_text(encoding="utf-8"))
    assert reused_verdict["status"] == "PASS"
    assert "reused=true" in reused_verdict["evidence"]

    artifact_path.unlink()
    assert baseline_precheck.main([]) == 0
    unrecoverable = yaml.safe_load(verdict_path.read_text(encoding="utf-8"))
    assert unrecoverable["status"] == "ABORT"
    assert unrecoverable["reason"] == "baseline_unrecoverable_post_implement"


@pytest.mark.large
@pytest.mark.large_local
@pytest.mark.parametrize(
    ("failure_count", "expected_status", "expected_verdict"),
    [(1, "known_failures", "PASS"), (11, "blocked", "ABORT")],
)
def test_entrypoint_classifies_non_clean_baseline_and_posts_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_count: int,
    expected_status: str,
    expected_verdict: str,
) -> None:
    project = _init_baseline_repo(tmp_path, failure_count=failure_count)
    verdict_path = tmp_path / "verdict.yaml"
    posted: list[BaselineArtifact] = []
    monkeypatch.setenv("KAJI_WORKTREE_DIR", str(project))
    monkeypatch.setenv("KAJI_ISSUE_ID", "346")
    monkeypatch.setenv("KAJI_BRANCH_NAME", "feat/346")
    monkeypatch.setenv("KAJI_DEFAULT_BRANCH", "main")
    monkeypatch.setenv("KAJI_VERDICT_PATH", str(verdict_path))
    monkeypatch.setattr(
        baseline_precheck,
        "_post_comment",
        lambda _worktree, _issue_id, artifact, _default_branch: posted.append(artifact),
    )

    assert baseline_precheck.main([]) == 0
    artifact = load_artifact(project / ".kaji-artifacts" / "baseline" / "baseline.json")
    assert artifact.status == expected_status
    assert len(artifact.failures) == failure_count
    assert posted == [artifact]
    verdict = yaml.safe_load(verdict_path.read_text(encoding="utf-8"))
    assert verdict["status"] == expected_verdict


@pytest.mark.large
@pytest.mark.large_local
def test_entrypoint_maps_pytest_launch_failure_to_invalid_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _init_baseline_repo(tmp_path)
    verdict_path = tmp_path / "verdict.yaml"
    original_run = subprocess.run

    def launch_spy(command: list[str], *args: object, **kwargs: object) -> object:
        if command[0] == str(project / ".venv" / "bin" / "python"):
            raise OSError("pytest executable unavailable")
        return original_run(command, *args, **kwargs)

    monkeypatch.setenv("KAJI_WORKTREE_DIR", str(project))
    monkeypatch.setenv("KAJI_ISSUE_ID", "346")
    monkeypatch.setenv("KAJI_BRANCH_NAME", "feat/346")
    monkeypatch.setenv("KAJI_DEFAULT_BRANCH", "main")
    monkeypatch.setenv("KAJI_VERDICT_PATH", str(verdict_path))
    monkeypatch.setattr(baseline_precheck.subprocess, "run", launch_spy)
    monkeypatch.setattr(baseline_precheck, "_post_comment", lambda *_: None)

    assert baseline_precheck.main([]) == 0
    artifact = load_artifact(project / ".kaji-artifacts" / "baseline" / "baseline.json")
    assert artifact.status == "invalid"
    assert artifact.pytest_exit_code == 127
    assert artifact.stop_reason == "report_missing_or_invalid"
    verdict = yaml.safe_load(verdict_path.read_text(encoding="utf-8"))
    assert verdict["status"] == "ABORT"


@pytest.mark.medium
def test_non_clean_github_comment_uses_provider_boundary_spy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GitHubProvider(repo="owner/repo", repo_root=tmp_path)
    calls: list[tuple[str, ...]] = []

    def gh_spy(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="https://github.example/comment/1\n",
            stderr="",
        )

    monkeypatch.setattr(baseline_precheck, "_resolve_run_config", lambda *_: object())
    monkeypatch.setattr(baseline_precheck, "_assert_provider_matches_run", lambda _: None)
    monkeypatch.setattr(baseline_precheck, "get_provider", lambda _: provider)
    monkeypatch.setattr(provider, "_run_gh", gh_spy)
    artifact = _artifact(failures=[_failure()])

    baseline_precheck._post_comment(tmp_path, "346", artifact, "main")

    assert len(calls) == 1
    assert calls[0][:6] == ("issue", "comment", "346", "--repo", "owner/repo", "--body")
    body = calls[0][6]
    assert body.startswith("## Baseline Check 結果\n")
    assert "`known_failures`" in body
    assert artifact.measured_commit in body


@pytest.mark.large
@pytest.mark.large_local
def test_compare_detects_stale_baseline_after_history_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _init_baseline_repo(tmp_path)
    monkeypatch.setenv("KAJI_WORKTREE_DIR", str(project))
    monkeypatch.setenv("KAJI_ISSUE_ID", "346")
    monkeypatch.setenv("KAJI_BRANCH_NAME", "feat/346")
    monkeypatch.setenv("KAJI_DEFAULT_BRANCH", "main")
    monkeypatch.delenv("KAJI_VERDICT_PATH", raising=False)
    assert baseline_precheck.main([]) == 0
    capsys.readouterr()

    (project / "README.md").write_text("history rewrite\n", encoding="utf-8")
    _git_run(project, "add", "README.md")
    _git_run(project, "commit", "--amend", "--no-edit")
    assert baseline_precheck.main(["--compare"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"verdict": "stale_baseline", "regressions": []}


@pytest.mark.medium
def test_measure_refuses_dirty_worktree_without_writing_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _init_baseline_repo(tmp_path)
    (project / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    verdict_path = tmp_path / "verdict.yaml"
    monkeypatch.setenv("KAJI_WORKTREE_DIR", str(project))
    monkeypatch.setenv("KAJI_ISSUE_ID", "346")
    monkeypatch.setenv("KAJI_BRANCH_NAME", "feat/346")
    monkeypatch.setenv("KAJI_DEFAULT_BRANCH", "main")
    monkeypatch.setenv("KAJI_VERDICT_PATH", str(verdict_path))
    assert baseline_precheck.main([]) == 0
    verdict = yaml.safe_load(verdict_path.read_text(encoding="utf-8"))
    assert verdict["status"] == "ABORT"
    assert verdict["reason"] == "dirty_worktree"
    assert not (project / ".kaji-artifacts" / "baseline" / "baseline.json").exists()


@pytest.mark.medium
def test_non_clean_local_comment_is_committed_to_provider_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    _git_run(main_repo, "init", "-b", "main")
    _git_run(main_repo, "config", "user.email", "baseline@example.invalid")
    _git_run(main_repo, "config", "user.name", "Baseline Test")
    provider = LocalProvider(repo_root=main_repo, machine_id="pc1")
    issue = provider.create_issue(
        title="baseline",
        body="body",
        slug="baseline",
        labels=["type:feature"],
    )
    _git_run(main_repo, "add", ".kaji")
    _git_run(main_repo, "commit", "-m", "chore: seed issue")
    feature_worktree = tmp_path / "feature"
    _git_run(
        main_repo,
        "worktree",
        "add",
        "-b",
        "feat/local-comment",
        str(feature_worktree),
    )
    monkeypatch.setattr(baseline_precheck, "_resolve_run_config", lambda *_: object())
    monkeypatch.setattr(baseline_precheck, "_assert_provider_matches_run", lambda _: None)
    monkeypatch.setattr(baseline_precheck, "get_provider", lambda _: provider)

    baseline_precheck._post_comment(
        feature_worktree,
        issue.id,
        _artifact(failures=[_failure()]),
        "main",
    )

    assert _git_run(main_repo, "status", "--porcelain") == ""
    assert _git_run(feature_worktree, "status", "--porcelain") == ""
    assert _git_run(main_repo, "log", "-1", "--format=%s") == (
        f"chore(local): comment for {issue.id}"
    )
    comments = provider.list_issue_comments_all(issue.id)
    assert len(comments) == 1
    assert comments[0].body.startswith("## Baseline Check 結果")


def _write_overlay_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Build a main worktree whose local-only overlay overrides tracked github config."""
    main_repo = tmp_path / "main"
    (main_repo / ".kaji").mkdir(parents=True)
    _git_run(main_repo, "init", "-b", "main")
    _git_run(main_repo, "config", "user.email", "baseline@example.invalid")
    _git_run(main_repo, "config", "user.name", "Baseline Test")
    (main_repo / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "owner/repo"\n',
        encoding="utf-8",
    )
    # gitignored overlay: `git worktree add` never copies it into a feature worktree.
    (main_repo / ".kaji" / "config.local.toml").write_text(
        '[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n',
        encoding="utf-8",
    )
    (main_repo / ".gitignore").write_text(".kaji/config.local.toml\n", encoding="utf-8")
    _git_run(main_repo, "add", ".")
    _git_run(main_repo, "commit", "-m", "chore: seed config")
    feature_worktree = tmp_path / "feature"
    _git_run(main_repo, "worktree", "add", "-b", "feat/overlay", str(feature_worktree))
    return main_repo, feature_worktree


@pytest.mark.medium
def test_run_config_resolves_main_worktree_overlay_from_feature_worktree(
    tmp_path: Path,
) -> None:
    main_repo, feature_worktree = _write_overlay_repo(tmp_path)
    assert not (feature_worktree / ".kaji" / "config.local.toml").exists()

    config = baseline_precheck._resolve_run_config(feature_worktree, "main")

    assert config.repo_root == main_repo.resolve()
    assert config.provider is not None
    # Discovering from the feature worktree would silently resolve 'github' and post
    # baseline evidence to the wrong provider.
    assert config.provider.type == "local"


@pytest.mark.medium
def test_run_config_falls_back_to_worktree_when_main_is_unresolvable(
    tmp_path: Path,
) -> None:
    standalone = tmp_path / "standalone"
    (standalone / ".kaji").mkdir(parents=True)
    (standalone / ".kaji" / "config.toml").write_text(
        '[paths]\nartifacts_dir = ".kaji-artifacts"\nskill_dir = ".claude/skills"\n\n'
        "[execution]\ndefault_timeout = 1800\n\n"
        '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "owner/repo"\n',
        encoding="utf-8",
    )

    config = baseline_precheck._resolve_run_config(standalone, "main")

    assert config.repo_root == standalone.resolve()
    assert config.provider is not None
    assert config.provider.type == "github"


@pytest.mark.medium
def test_post_comment_fails_loud_when_provider_diverges_from_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_repo, feature_worktree = _write_overlay_repo(tmp_path)
    (main_repo / ".kaji" / "config.local.toml").unlink()
    monkeypatch.setenv("KAJI_PROVIDER_TYPE", "local")
    posted: list[object] = []
    monkeypatch.setattr(
        baseline_precheck,
        "get_provider",
        lambda _: posted.append("provider built"),
    )

    with pytest.raises(ValueError, match="provider mismatch"):
        baseline_precheck._post_comment(
            feature_worktree,
            "local-1",
            _artifact(failures=[_failure()]),
            "main",
        )

    assert posted == []


@pytest.mark.large
@pytest.mark.large_local
def test_pytest_plugin_preserves_nodeids_phases_and_exception_types(tmp_path: Path) -> None:
    project = tmp_path / "project"
    tests_dir = project / "tests"
    tests_dir.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
        encoding="utf-8",
    )
    (tests_dir / "test_cases.py").write_text(
        """import pytest

@pytest.mark.parametrize("value", [pytest.param(1, id='quoted[\"value\"]')])
def test_bare_assert(value):
    assert value == 2

class TestSetup:
    def setup_method(self):
        raise TypeError("setup failed")

    def test_method(self):
        pass

@pytest.fixture
def teardown_failure():
    yield
    raise RuntimeError("teardown failed")

def test_teardown_error(teardown_failure):
    pass

def test_chained_exception():
    try:
        raise ValueError("inner")
    except ValueError as error:
        raise RuntimeError("outer") from error
""",
        encoding="utf-8",
    )
    (tests_dir / "test_collection_error.py").write_text(
        "import module_that_does_not_exist\n",
        encoding="utf-8",
    )
    report_path = project / "report.json"
    env = os.environ.copy()
    env["KAJI_BASELINE_REPORT_PATH"] = str(report_path)
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) + (
        os.pathsep + current_pythonpath if current_pythonpath else ""
    )
    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "python"),
            "-m",
            "pytest",
            "-p",
            "kaji_harness.pytest_baseline_plugin",
            "-n",
            "0",
            "-q",
            "tests/test_cases.py",
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert report_path.exists(), result.stdout + result.stderr
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    failures = {
        (entry["nodeid"], entry["kind"], entry["error_type"]) for entry in payload["failures"]
    }
    assert (
        'tests/test_cases.py::test_bare_assert[quoted["value"]]',
        "FAILED",
        "AssertionError",
    ) in failures
    assert ("tests/test_cases.py::TestSetup::test_method", "ERROR", "TypeError") in failures
    assert (
        "tests/test_cases.py::test_teardown_error",
        "ERROR",
        "RuntimeError",
    ) in failures
    assert (
        "tests/test_cases.py::test_chained_exception",
        "FAILED",
        "RuntimeError",
    ) in failures
    collection_report_path = project / "collection-report.json"
    env["KAJI_BASELINE_REPORT_PATH"] = str(collection_report_path)
    collection_result = subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "python"),
            "-m",
            "pytest",
            "-p",
            "kaji_harness.pytest_baseline_plugin",
            "-n",
            "0",
            "-q",
            "tests/test_collection_error.py",
        ],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert collection_result.returncode != 0
    collection_payload = json.loads(collection_report_path.read_text(encoding="utf-8"))
    collection = [
        (entry["nodeid"], entry["kind"], entry["error_type"])
        for entry in collection_payload["failures"]
    ]
    assert collection == [("tests/test_collection_error.py", "ERROR", "ModuleNotFoundError")]
