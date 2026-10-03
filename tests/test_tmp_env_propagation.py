"""Issue #407: attempt tmp dir の 4 変数が各 dispatch 経路へ伝播することを検証する。

- small: ``_build_wrapper_command`` の ``env`` 前置
- medium: ``execute_cli`` / ``execute_exec`` の実 subprocess、``WorkflowRunner`` の dispatch 配線
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from kaji_harness.cli import execute_cli
from kaji_harness.config import KajiConfig
from kaji_harness.errors import TmpDirPreparationError
from kaji_harness.interactive_terminal import _build_wrapper_command
from kaji_harness.models import CLIResult, Step, Workflow
from kaji_harness.runner import WorkflowRunner, build_tmp_env
from kaji_harness.script_exec import execute_exec
from kaji_harness.skill import SkillMetadata

_TMP_VARS = ("KAJI_TMP_DIR", "TMPDIR", "TMP", "TEMP")
_PASS_YAML = "status: PASS\nreason: ok\nevidence: e\nsuggestion: ''\n"
_WRAPPER_KWARGS: dict[str, Any] = {
    "agent": "claude",
    "prompt_path": Path("/a/prompt.txt"),
    "verdict_path": Path("/a/verdict.yaml"),
    "workdir": Path("/w"),
    "resume_session_id": "",
    "launch_session_id": "sid",
    "model": "m",
    "effort": "e",
    "execution_policy": "auto",
}


@pytest.mark.small
class TestBuildWrapperCommandEnv:
    def test_env_none_matches_legacy_output(self) -> None:
        wrapper = Path("/pkg/wrapper.sh")
        without = _build_wrapper_command(wrapper, **_WRAPPER_KWARGS)
        explicit_none = _build_wrapper_command(wrapper, env=None, **_WRAPPER_KWARGS)
        assert without == explicit_none
        assert shlex.split(without)[0] == "/pkg/wrapper.sh"
        assert len(shlex.split(without)) == 11  # wrapper + 10 positional args

    def test_env_prefix_precedes_wrapper_and_its_args(self) -> None:
        env = build_tmp_env(Path("/p/tmp/kaji/1/r/s/attempt-001"))
        command = _build_wrapper_command(Path("/pkg/wrapper.sh"), env=env, **_WRAPPER_KWARGS)
        argv = shlex.split(command)
        assert argv[0] == "env"
        assert argv[1:5] == [f"{k}=/p/tmp/kaji/1/r/s/attempt-001" for k in _TMP_VARS]
        assert argv[5] == "/pkg/wrapper.sh"
        legacy = shlex.split(_build_wrapper_command(Path("/pkg/wrapper.sh"), **_WRAPPER_KWARGS))
        assert argv[5:] == legacy

    def test_env_value_with_space_round_trips(self) -> None:
        env = build_tmp_env(Path("/p q/tmp/kaji/x"))
        command = _build_wrapper_command(Path("/pkg/wrapper.sh"), env=env, **_WRAPPER_KWARGS)
        argv = shlex.split(command)
        assert argv[1] == "KAJI_TMP_DIR=/p q/tmp/kaji/x"


_FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys, tempfile

record = {k: os.environ.get(k) for k in ("KAJI_TMP_DIR", "TMPDIR", "TMP", "TEMP", "KAJI_ISSUE_ID")}
record["gettempdir"] = tempfile.gettempdir()
with tempfile.NamedTemporaryFile(delete=False) as handle:
    record["tempfile"] = handle.name
with open(os.environ["FAKE_CLAUDE_OUT"], "w", encoding="utf-8") as out:
    json.dump(record, out)
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}))
print(json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.0}))
"""


def _install_fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(_FAKE_CLAUDE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "fake-claude-out.json"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_OUT", str(out))
    return out


def _execute_fake_cli(log_dir: Path, **kwargs: Any) -> CLIResult:
    return execute_cli(
        step=Step(id="design", skill="x", agent="claude"),
        prompt="p",
        workdir=log_dir.parent,
        session_id=None,
        log_dir=log_dir,
        execution_policy="auto",
        verbose=False,
        default_timeout=30,
        **kwargs,
    )


@pytest.mark.medium
class TestExecuteCliEnv:
    def test_env_overrides_inherited_temp_variables(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = _install_fake_claude(tmp_path, monkeypatch)
        attempt_tmp = tmp_path / "attempt-tmp"
        attempt_tmp.mkdir()
        for var in ("TMPDIR", "TMP", "TEMP"):
            monkeypatch.setenv(var, str(tmp_path / "parent"))

        _execute_fake_cli(tmp_path / "logs", env=build_tmp_env(attempt_tmp))

        record = json.loads(out.read_text())
        for var in _TMP_VARS:
            assert record[var] == str(attempt_tmp)
        assert record["gettempdir"] == str(attempt_tmp)
        assert Path(record["tempfile"]).parent == attempt_tmp

    def test_env_none_inherits_parent_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = _install_fake_claude(tmp_path, monkeypatch)
        parent = tmp_path / "parent"
        parent.mkdir()
        monkeypatch.setenv("TMPDIR", str(parent))
        monkeypatch.delenv("KAJI_TMP_DIR", raising=False)

        _execute_fake_cli(tmp_path / "logs")

        record = json.loads(out.read_text())
        assert record["TMPDIR"] == str(parent)
        assert record["KAJI_TMP_DIR"] is None

    def test_transient_retry_reuses_same_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempt_tmp = tmp_path / "attempt-tmp"
        attempt_tmp.mkdir()
        env = build_tmp_env(attempt_tmp)
        seen: list[dict[str, str]] = []

        def fake_once(*args: Any, **kwargs: Any) -> CLIResult:
            seen.append(dict(kwargs["env"]))
            if len(seen) == 1:
                from kaji_harness.errors import CLIExecutionError

                raise CLIExecutionError("design", 1, "rate limit")
            return CLIResult(full_output="")

        monkeypatch.setattr("kaji_harness.cli.time.sleep", lambda _s: None)
        with patch("kaji_harness.cli._execute_cli_once", side_effect=fake_once):
            _execute_fake_cli(tmp_path / "logs", env=env)

        assert seen == [env, env]


@pytest.mark.medium
class TestExecuteExecEnv:
    def test_child_tempfile_api_uses_kaji_tmp_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempt_tmp = tmp_path / "attempt-tmp"
        attempt_tmp.mkdir()
        for var in ("TMPDIR", "TMP", "TEMP"):
            monkeypatch.setenv(var, str(tmp_path / "parent"))
        script = (
            "import json, os, tempfile\n"
            "with tempfile.NamedTemporaryFile() as f:\n"
            "    print(json.dumps({'tmp': tempfile.gettempdir(), 'file': f.name,"
            " 'vars': [os.environ[k] for k in ('KAJI_TMP_DIR','TMPDIR','TMP','TEMP')]}))\n"
        )

        result = execute_exec(
            step=Step(id="s", exec=[sys.executable, "-c", script]),
            argv=[sys.executable, "-c", script],
            env=build_tmp_env(attempt_tmp),
            workdir=tmp_path,
            log_dir=tmp_path / "logs",
            timeout=30,
            verbose=False,
        )

        data = json.loads(result.full_output.strip().splitlines()[-1])
        assert data["tmp"] == str(attempt_tmp)
        assert Path(data["file"]).parent == attempt_tmp
        assert data["vars"] == [str(attempt_tmp)] * 4


def _make_config(tmp_path: Path, *, execution_extra: str = "") -> KajiConfig:
    kaji_dir = tmp_path / ".kaji"
    kaji_dir.mkdir(exist_ok=True)
    cfg = kaji_dir / "config.toml"
    cfg.write_text(
        '[paths]\nskill_dir = ".claude/skills"\nartifacts_dir = ".kaji/artifacts"\n\n'
        f"[execution]\ndefault_timeout = 60\n{execution_extra}\n\n"
        '[provider]\ntype = "local"\n\n'
        '[provider.local]\nmachine_id = "pc1"\ndefault_branch = "main"\n'
    )
    if not (tmp_path / ".git").exists():
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(tmp_path)], check=True)
    return KajiConfig._load(cfg)


def _make_runner(config: KajiConfig, tmp_path: Path, step: Step) -> WorkflowRunner:
    workflow = Workflow(name="t", description="", execution_policy="auto", steps=[step])
    return WorkflowRunner(
        workflow=workflow,
        issue_number=99,
        project_root=tmp_path,
        artifacts_dir=tmp_path / ".kaji-artifacts",
        config=config,
    )


def _assert_attempt_env(env: dict[str, str], project_root: Path, step_id: str) -> None:
    values = {env[name] for name in _TMP_VARS}
    assert len(values) == 1
    value = values.pop()
    path = Path(value)
    assert path.is_absolute()
    assert path.is_dir(), "tmp dir must exist at dispatch time"
    assert path.is_relative_to(project_root.resolve() / "tmp")
    assert path.parent.name == step_id
    assert path.name == "attempt-001"
    assert os.path.normpath(value) == value


@pytest.mark.medium
class TestRunnerDispatchWiring:
    def test_exec_step_receives_four_variables(self, tmp_path: Path) -> None:
        step = Step(id="collect", exec=["true"], on={"PASS": "end"})
        runner = _make_runner(_make_config(tmp_path), tmp_path, step)
        captured: dict[str, Any] = {}

        def fake_exec(**kwargs: Any) -> CLIResult:
            captured.update(kwargs)
            Path(kwargs["env"]["KAJI_VERDICT_PATH"]).write_text(_PASS_YAML)
            return CLIResult(full_output="")

        with patch("kaji_harness.runner.execute_exec", side_effect=fake_exec):
            runner.run()

        env = captured["env"]
        _assert_attempt_env(env, tmp_path, "collect")
        assert env["KAJI_STEP_ID"] == "collect"  # 既存 context env は維持される

    def test_exec_script_step_receives_four_variables(self, tmp_path: Path) -> None:
        step = Step(id="poll", skill="rp", on={"PASS": "end"})
        runner = _make_runner(_make_config(tmp_path), tmp_path, step)
        captured: dict[str, Any] = {}

        def fake_script(**kwargs: Any) -> CLIResult:
            captured.update(kwargs)
            Path(kwargs["env"]["KAJI_VERDICT_PATH"]).write_text(_PASS_YAML)
            return CLIResult(full_output="")

        meta = SkillMetadata(name="rp", description="", exec_script="m")
        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=meta),
            patch("kaji_harness.runner.execute_script", side_effect=fake_script),
        ):
            runner.run()

        _assert_attempt_env(captured["env"], tmp_path, "poll")

    def test_headless_agent_receives_only_four_variables(self, tmp_path: Path) -> None:
        step = Step(id="design", skill="plain", agent="claude", on={"PASS": "end"})
        runner = _make_runner(_make_config(tmp_path), tmp_path, step)
        captured: dict[str, Any] = {}

        def fake_cli(**kwargs: Any) -> CLIResult:
            captured.update(kwargs)
            (kwargs["log_dir"] / "verdict.yaml").write_text(_PASS_YAML)
            return CLIResult(full_output="")

        meta = SkillMetadata(name="plain", description="", exec_script=None)
        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=meta),
            patch("kaji_harness.runner.execute_cli", side_effect=fake_cli),
        ):
            runner.run()

        env = captured["env"]
        assert set(env) == set(_TMP_VARS)
        _assert_attempt_env(env, tmp_path, "design")

    @pytest.mark.parametrize("backend", ["tmux", "herdr"])
    def test_interactive_agent_receives_four_variables(self, tmp_path: Path, backend: str) -> None:
        config = _make_config(
            tmp_path,
            execution_extra=(
                f'agent_runner = "interactive_terminal"\ninteractive_terminal_backend = "{backend}"'
            ),
        )
        step = Step(id="design", skill="plain", agent="claude", on={"PASS": "end"})
        runner = _make_runner(config, tmp_path, step)
        captured: dict[str, Any] = {}

        def fake_interactive(**kwargs: Any) -> CLIResult:
            captured.update(kwargs)
            kwargs["verdict_path"].write_text(_PASS_YAML)
            return CLIResult(full_output="")

        meta = SkillMetadata(name="plain", description="", exec_script=None)
        with (
            patch("kaji_harness.runner.validate_skill_exists"),
            patch("kaji_harness.runner.load_skill_metadata", return_value=meta),
            patch("kaji_harness.runner.execute_interactive_terminal", side_effect=fake_interactive),
        ):
            runner.run()

        assert set(captured["env"]) == set(_TMP_VARS)
        _assert_attempt_env(captured["env"], tmp_path, "design")

    def test_prepare_failure_prevents_dispatch(self, tmp_path: Path) -> None:
        (tmp_path / "tmp").write_text("blocking regular file")
        step = Step(id="collect", exec=["true"], on={"PASS": "end"})
        runner = _make_runner(_make_config(tmp_path), tmp_path, step)

        with patch("kaji_harness.runner.execute_exec") as mock_exec:
            with pytest.raises(TmpDirPreparationError):
                runner.run()

        mock_exec.assert_not_called()

    @pytest.mark.parametrize("bad_id", ["../outside", "a/../design", "sub/step"])
    def test_invalid_step_id_rejected_before_any_directory_is_created(
        self, tmp_path: Path, bad_id: str
    ) -> None:
        step = Step(id=bad_id, exec=["true"], on={"PASS": "end"})
        runner = _make_runner(_make_config(tmp_path), tmp_path, step)
        artifacts_dir = tmp_path / ".kaji-artifacts"

        with patch("kaji_harness.runner.execute_exec") as mock_exec:
            with pytest.raises(TmpDirPreparationError):
                runner.run()

        mock_exec.assert_not_called()
        assert not (tmp_path / "tmp").exists()
        assert not list(artifacts_dir.rglob("steps/*")), "attempt dir must not be allocated"
        assert not list(artifacts_dir.rglob(Path(bad_id).name)), "no component may be created"

    def test_absolute_step_id_does_not_create_directories_outside_artifacts(
        self, tmp_path: Path
    ) -> None:
        outside = tmp_path / "abs-outside"
        step = Step(id=str(outside), exec=["true"], on={"PASS": "end"})
        runner = _make_runner(_make_config(tmp_path), tmp_path, step)

        with patch("kaji_harness.runner.execute_exec") as mock_exec:
            with pytest.raises(TmpDirPreparationError):
                runner.run()

        mock_exec.assert_not_called()
        assert not outside.exists()
        assert not (tmp_path / "tmp").exists()

    def test_each_attempt_gets_distinct_directory(self, tmp_path: Path) -> None:
        step = Step(id="collect", exec=["true"], on={"RETRY": "collect", "PASS": "end"})
        runner = _make_runner(_make_config(tmp_path), tmp_path, step)
        seen: list[str] = []

        def fake_exec(**kwargs: Any) -> CLIResult:
            seen.append(kwargs["env"]["KAJI_TMP_DIR"])
            status = "RETRY" if len(seen) == 1 else "PASS"
            Path(kwargs["env"]["KAJI_VERDICT_PATH"]).write_text(
                f"status: {status}\nreason: r\nevidence: e\nsuggestion: ''\n"
            )
            return CLIResult(full_output="")

        with patch("kaji_harness.runner.execute_exec", side_effect=fake_exec):
            runner.run()

        assert len(seen) == 2
        assert seen[0] != seen[1]
        assert seen[0].endswith("attempt-001")
        assert seen[1].endswith("attempt-002")
        assert tempfile.gettempdir() != seen[0]  # test プロセス自体の env は変更されない
