"""Issue #397: ``[paths] design_dir`` で設計書 directory を設定可能にする。

設計書: ``draft/design/issue-397-feat-context-design-directory-design-pat.md``。
Small（純粋関数・入力モデル）/ Medium（config 読込・symlink・provider・CLI handler）/
Large（実 subprocess の ``kaji config design-dir`` / ``kaji issue context``）。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

import kaji_harness
from kaji_harness.commands.main import main
from kaji_harness.config import KajiConfig, PathsConfig, _DesignDirInput
from kaji_harness.design_dir import LEGACY_DESIGN_DIR, validate_design_dir
from kaji_harness.errors import ConfigLoadError
from kaji_harness.models import Step, Workflow
from kaji_harness.prompt import build_prompt
from kaji_harness.providers import GitHubProvider, LocalProvider, get_provider
from kaji_harness.providers.context import build_design_path, validate_slug
from kaji_harness.providers.models import Issue, Label
from kaji_harness.state import SessionState

# ============================================================
# Small: validate_design_dir / validate_slug / build_design_path / _DesignDirInput
# ============================================================


@pytest.mark.small
class TestValidateDesignDir:
    @pytest.mark.parametrize(
        "value",
        ["designs/issues", "draft/design", "docs/design", ".kaji/designs", "a_b/c-d.e", "d"],
    )
    def test_accepts_valid(self, value: str) -> None:
        validate_design_dir(value)

    @pytest.mark.parametrize(
        "value",
        [
            "/abs",  # V2
            "/srv/designs",  # V2
            "C:/x",  # V2 (drive)
            "//host/share",  # V2 (UNC)
            "~/x",  # V3
            "a b",  # V3
            "a\\b",  # V3
            "-x",  # V3 (option 化)
            "designs/$(id)",  # V3
            "designs/issues\n",  # V3 (fullmatch)
            "a//b",  # V4
            "a/",  # V4
            "",  # V4 (空 segment)
            ".",  # V5
            "./a",  # V5
            "../a",  # V5
            "a/../b",  # V5
            ".git",  # V6
            "a/.GIT",  # V6
        ],
    )
    def test_rejects_invalid_with_value_in_message(self, value: str) -> None:
        with pytest.raises(ValueError) as exc:
            validate_design_dir(value)
        assert repr(value) in str(exc.value) or value in str(exc.value)

    def test_legacy_constant(self) -> None:
        assert LEGACY_DESIGN_DIR == "draft/design"


@pytest.mark.small
class TestValidateSlugFullmatch:
    @pytest.mark.parametrize("slug", ["example", "a", "a" * 40, "a-b-c", "0abc"])
    def test_accepts(self, slug: str) -> None:
        validate_slug(slug)

    @pytest.mark.parametrize(
        "slug",
        ["example\n", "example\r\n", "exa\nmple", "example ", "ex\tample", "a" * 41, "", "-a"],
    )
    def test_rejects(self, slug: str) -> None:
        with pytest.raises(ValueError):
            validate_slug(slug)


@pytest.mark.small
class TestBuildDesignPath:
    def test_default_is_legacy(self) -> None:
        assert build_design_path("153", "auth") == "draft/design/issue-153-auth.md"
        assert build_design_path("153", "auth", "") == "draft/design/issue-153-auth.md"

    def test_custom_design_dir(self) -> None:
        assert (
            build_design_path("42", "example", "designs/issues")
            == "designs/issues/issue-42-example.md"
        )

    @pytest.mark.parametrize("slug", ["Bad_Slug", "../x", "", "example\n", "example\r\n"])
    def test_invalid_slug(self, slug: str) -> None:
        with pytest.raises(ValueError):
            build_design_path("42", slug, "designs/issues")

    @pytest.mark.parametrize("design_dir", ["../x", "/abs", "a//b", ".git", "a b"])
    def test_invalid_design_dir(self, design_dir: str) -> None:
        with pytest.raises(ValueError):
            build_design_path("42", "example", design_dir)


@pytest.mark.small
class TestDesignDirInput:
    def test_defaults_to_empty(self) -> None:
        assert _DesignDirInput().design_dir == ""

    def test_accepts_empty_and_valid(self) -> None:
        assert _DesignDirInput(design_dir="").design_dir == ""
        assert _DesignDirInput(design_dir="designs/issues").design_dir == "designs/issues"

    @pytest.mark.parametrize("value", [1, True, ["a"], {"a": 1}])
    def test_strict_rejects_non_str(self, value: object) -> None:
        with pytest.raises(ValidationError):
            _DesignDirInput.model_validate({"design_dir": value})

    def test_invalid_value_uses_validate_design_dir(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _DesignDirInput(design_dir="../x")
        assert ".." in str(exc.value)

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            _DesignDirInput.model_validate({"design_dir": "a", "other": 1})


@pytest.mark.small
def test_paths_config_default_design_dir() -> None:
    assert PathsConfig().design_dir == ""


@pytest.mark.small
def test_build_prompt_injects_design_path_verbatim() -> None:
    from tests.conftest import make_issue_context

    ctx = make_issue_context(slug="example")
    ctx = type(ctx)(**{**ctx.__dict__, "design_path": "designs/issues/issue-42-example.md"})
    step = Step(id="implement", skill="s", agent="claude", on={"PASS": "end"})
    wf = Workflow(
        name="w",
        description="d",
        execution_policy="sequential",
        steps=[step],
        cycles=[],
    )
    with patch.object(SessionState, "_persist"):
        state = SessionState(
            issue_number="42",
            artifacts_dir=Path("/tmp/fake-artifacts"),
            sessions={},
            step_history=[],
            cycle_counts={},
            last_completed_step=None,
            last_transition_verdict=None,
        )
    prompt = build_prompt(step, "42", state, wf, ctx)
    assert "designs/issues/issue-42-example.md" in prompt
    assert "draft/design" not in prompt


# ============================================================
# Medium helpers
# ============================================================


def _write_config(
    repo: Path,
    *,
    design_dir_line: str = "",
    provider: str = '[provider]\ntype = "github"\n\n[provider.github]\nrepo = "owner/name"\n',
    overlay: str = "",
) -> Path:
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir(parents=True, exist_ok=True)
    (kaji_dir / "config.toml").write_text(
        "[paths]\n"
        'artifacts_dir = ".kaji-artifacts"\n'
        'skill_dir = ".claude/skills"\n'
        f"{design_dir_line}\n"
        "\n[execution]\ndefault_timeout = 1800\n\n" + provider
    )
    if overlay:
        (kaji_dir / "config.local.toml").write_text(overlay)
    return repo


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = main(argv)
        except SystemExit as e:
            rc = int(e.code) if isinstance(e.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


# ============================================================
# Medium: config loading / Pydantic boundary / symlink (V7)
# ============================================================


@pytest.mark.medium
class TestConfigLoadDesignDir:
    def test_unset_is_empty(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo")
        assert KajiConfig.discover(start_dir=repo).paths.design_dir == ""

    def test_empty_string_is_unset(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo", design_dir_line='design_dir = ""')
        assert KajiConfig.discover(start_dir=repo).paths.design_dir == ""

    def test_configured(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo", design_dir_line='design_dir = "designs/issues"')
        assert KajiConfig.discover(start_dir=repo).paths.design_dir == "designs/issues"

    @pytest.mark.parametrize(
        "line",
        [
            "design_dir = 1",
            "design_dir = true",
            'design_dir = ["a"]',
            "design_dir = { a = 1 }",
            'design_dir = "/abs"',
            'design_dir = "C:/x"',
            'design_dir = "~/x"',
            'design_dir = "a b"',
            'design_dir = "a//b"',
            'design_dir = "a/"',
            'design_dir = "../x"',
            'design_dir = "."',
            'design_dir = ".git/x"',
            'design_dir = "designs/issues\\n"',
        ],
    )
    def test_invalid_raises_config_load_error(self, tmp_path: Path, line: str) -> None:
        repo = _write_config(tmp_path / "repo", design_dir_line=line)
        with pytest.raises(ConfigLoadError, match=r"paths\.design_dir"):
            KajiConfig.discover(start_dir=repo)

    def test_overlay_paths_ignored(self, tmp_path: Path) -> None:
        repo = _write_config(
            tmp_path / "repo",
            design_dir_line='design_dir = "designs/issues"',
            overlay='[paths]\ndesign_dir = "other/dir"\n',
        )
        assert KajiConfig.discover(start_dir=repo).paths.design_dir == "designs/issues"


@pytest.mark.medium
class TestConfigDesignDirSymlink:
    def test_symlink_loop_is_config_load_error(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo", design_dir_line='design_dir = "designs/issues"')
        os.symlink("designs", repo / "designs")
        with pytest.raises(ConfigLoadError, match=r"paths\.design_dir"):
            KajiConfig.discover(start_dir=repo)

    def test_broken_symlink_is_config_load_error(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo", design_dir_line='design_dir = "designs/issues"')
        os.symlink("missing", repo / "designs")
        with pytest.raises(ConfigLoadError, match=r"paths\.design_dir"):
            KajiConfig.discover(start_dir=repo)

    def test_symlink_escape_is_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        repo = _write_config(tmp_path / "repo", design_dir_line='design_dir = "designs/issues"')
        os.symlink(outside, repo / "designs")
        with pytest.raises(ConfigLoadError, match="inside the repository"):
            KajiConfig.discover(start_dir=repo)

    def test_symlink_inside_repo_is_allowed(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo", design_dir_line='design_dir = "designs/issues"')
        (repo / "real").mkdir()
        os.symlink(repo / "real", repo / "designs")
        assert KajiConfig.discover(start_dir=repo).paths.design_dir == "designs/issues"

    def test_nonexistent_dir_passes(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo", design_dir_line='design_dir = "designs/issues"')
        assert KajiConfig.discover(start_dir=repo).paths.design_dir == "designs/issues"

    def test_cli_exit_2_on_symlink_loop(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo", design_dir_line='design_dir = "designs/issues"')
        os.symlink("designs", repo / "designs")
        rc, stdout, stderr = _run_cli(["config", "design-dir", "--workdir", str(repo)])
        assert rc == 2
        assert stdout == ""
        assert "paths.design_dir" in stderr


# ============================================================
# Medium: providers / end-to-end / CLI handler
# ============================================================


def _stub_view_issue(provider: GitHubProvider, issue_id: str) -> Issue:
    return Issue(
        id="42",
        title="Example",
        body="",
        state="open",
        labels=[Label(name="type:feature")],
        comments=[],
    )


@pytest.mark.medium
class TestProviderDesignPath:
    def test_github_end_to_end_without_adapter(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo", design_dir_line='design_dir = "designs/issues"')
        config = KajiConfig.discover(start_dir=repo)
        provider = get_provider(config)
        assert isinstance(provider, GitHubProvider)
        with patch.object(GitHubProvider, "view_issue", _stub_view_issue):
            ctx = provider.resolve_issue_context("42")
        assert ctx.design_path == "designs/issues/issue-42-example.md"

        step = Step(id="implement", skill="s", agent="claude", on={"PASS": "end"})
        wf = Workflow(
            name="w", description="d", execution_policy="sequential", steps=[step], cycles=[]
        )
        with patch.object(SessionState, "_persist"):
            state = SessionState(
                issue_number="42",
                artifacts_dir=tmp_path / "artifacts",
                sessions={},
                step_history=[],
                cycle_counts={},
                last_completed_step=None,
                last_transition_verdict=None,
            )
        prompt = build_prompt(step, "42", state, wf, ctx)
        assert "designs/issues/issue-42-example.md" in prompt
        assert "draft/design" not in prompt

    def test_github_unset_is_legacy(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo")
        provider = get_provider(KajiConfig.discover(start_dir=repo))
        with patch.object(GitHubProvider, "view_issue", _stub_view_issue):
            ctx = provider.resolve_issue_context("42")
        assert ctx.design_path == "draft/design/issue-42-example.md"

    def test_local_matches_github_directory(self, tmp_path: Path) -> None:
        local_repo = tmp_path / "local"
        _git_init(local_repo)
        _write_config(
            local_repo,
            design_dir_line='design_dir = "designs/issues"',
            provider='[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n',
        )
        config = KajiConfig.discover(start_dir=local_repo)
        provider = get_provider(config)
        assert isinstance(provider, LocalProvider)
        issue = provider.create_issue(
            title="Example", body="b", labels=["type:feature"], slug="example"
        )
        ctx = provider.resolve_issue_context(issue.id)
        assert ctx.design_path == build_design_path(issue.id, "example", "designs/issues")

        gh_repo = _write_config(tmp_path / "gh", design_dir_line='design_dir = "designs/issues"')
        gh_provider = get_provider(KajiConfig.discover(start_dir=gh_repo))
        with patch.object(GitHubProvider, "view_issue", _stub_view_issue):
            gh_ctx = gh_provider.resolve_issue_context("42")
        assert Path(ctx.design_path).parent.as_posix() == Path(gh_ctx.design_path).parent.as_posix()

    def test_local_unset_is_legacy(self, tmp_path: Path) -> None:
        repo = tmp_path / "local"
        _git_init(repo)
        _write_config(
            repo,
            provider='[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n',
        )
        provider = get_provider(KajiConfig.discover(start_dir=repo))
        issue = provider.create_issue(title="x", body="b", labels=["type:feature"], slug="x")
        ctx = provider.resolve_issue_context(issue.id)
        assert ctx.design_path == f"draft/design/issue-{issue.id}-x.md"


@pytest.mark.medium
class TestConfigDesignDirCommand:
    def test_unset_prints_legacy(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo")
        rc, stdout, stderr = _run_cli(["config", "design-dir", "--workdir", str(repo)])
        assert rc == 0, stderr
        assert stdout == "draft/design\n"

    def test_configured_prints_value(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo", design_dir_line='design_dir = "designs/issues"')
        rc, stdout, stderr = _run_cli(["config", "design-dir", "--workdir", str(repo)])
        assert rc == 0, stderr
        assert stdout == "designs/issues\n"

    def test_missing_config_exits_2(self, outside_project_tmp_path: Path) -> None:
        rc, stdout, stderr = _run_cli(
            ["config", "design-dir", "--workdir", str(outside_project_tmp_path)]
        )
        assert rc == 2
        assert stdout == ""
        assert "Error:" in stderr

    def test_invalid_design_dir_exits_2(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo", design_dir_line='design_dir = "../x"')
        rc, stdout, stderr = _run_cli(["config", "design-dir", "--workdir", str(repo)])
        assert rc == 2
        assert stdout == ""
        assert "paths.design_dir" in stderr

    def test_invalid_workdir_exits_2(self, tmp_path: Path) -> None:
        rc, stdout, stderr = _run_cli(["config", "design-dir", "--workdir", str(tmp_path / "nope")])
        assert rc == 2
        assert "is not a valid directory" in stderr


# ============================================================
# Large (large_local): 実 subprocess
# ============================================================

_KAJI_CMD = [sys.executable, "-m", "kaji_harness.cli_main"]


def _run_kaji(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # subprocess は tmp repo を cwd にするため、テスト対象の checkout を明示的に import させる。
    package_root = str(Path(kaji_harness.__file__).resolve().parent.parent)
    env = {**os.environ, "PYTHONPATH": package_root}
    return subprocess.run(
        [*_KAJI_CMD, *args], cwd=repo, capture_output=True, text=True, timeout=60, env=env
    )


@pytest.mark.large
@pytest.mark.large_local
class TestDesignDirSubprocess:
    def test_config_design_dir_prints_configured_value(self, tmp_path: Path) -> None:
        repo = _write_config(tmp_path / "repo", design_dir_line='design_dir = "designs/issues"')
        result = _run_kaji(repo, "config", "design-dir", "--workdir", str(repo))
        assert result.returncode == 0, result.stderr
        assert result.stdout == "designs/issues\n"

    @pytest.mark.parametrize("kind", ["traversal", "loop"])
    def test_invalid_design_dir_exits_2_without_traceback(self, tmp_path: Path, kind: str) -> None:
        line = 'design_dir = "../x"' if kind == "traversal" else 'design_dir = "designs/issues"'
        repo = _write_config(tmp_path / "repo", design_dir_line=line)
        if kind == "loop":
            os.symlink("designs", repo / "designs")
        result = _run_kaji(repo, "config", "design-dir", "--workdir", str(repo))
        assert result.returncode == 2
        assert "paths.design_dir" in result.stderr
        assert "Traceback" not in result.stderr

    def test_local_issue_context_json_uses_design_dir(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        _git_init(repo)
        _write_config(
            repo,
            design_dir_line='design_dir = "designs/issues"',
            provider='[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n',
        )
        created = _run_kaji(
            repo, "issue", "create", "--title", "Example", "--body", "b", "--slug", "example"
        )
        assert created.returncode == 0, created.stderr
        ctx = _run_kaji(repo, "issue", "context", "local-pc1-1")
        assert ctx.returncode == 0, ctx.stderr
        payload = json.loads(ctx.stdout)
        assert payload["design_path"] == "designs/issues/issue-local-pc1-1-example.md"
