"""Custom exceptions for kaji_harness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class HarnessError(Exception):
    """ハーネスの基底例外。"""


@dataclass(frozen=True)
class SessionResolution:
    """異常終了経路で確定した session ID の解決結果（Issue #403）。

    ``session_id is None`` は「解決を試みたが、当該 attempt に一意対応する session が
    無かった」ことを表す確定値であり、呼び出し側の推測（resume 入力等）で埋めては
    ならない。解決自体を試みていない経路は、例外にこのオブジェクトを載せない
    （``session_resolution is None`` = 未試行）。

    Attributes:
        session_id: 当該 attempt の session として検証できた ID。検証できなければ None。
    """

    session_id: str | None


# --- main worktree 解決エラー ---
class MainWorktreeResolutionError(RuntimeError):
    """``resolve_main_worktree`` が main worktree を特定できない。

    Issue #448 で local provider の例外型を置き換えた。基底は ``RuntimeError`` を維持し、
    ``except RuntimeError`` の到達範囲を変えない（#285 の ``SyncError`` 移設と同じ判断）。
    """


# --- 設定エラー ---
class ConfigNotFoundError(HarnessError):
    """.kaji/config.toml が見つからない。"""

    def __init__(self, start_dir: Path):
        self.start_dir = start_dir
        super().__init__(
            f".kaji/config.toml not found. Searched from {self.start_dir} to /.\n\n"
            "`kaji issue` / `kaji pr` / `kaji run` require a kaji repository.\n"
            "First create `.kaji/config.toml` with `[paths]` and `[execution]`\n"
            "sections (template in `docs/cli-guides/github-mode.md` § 1.3),\n"
            "then add a `[provider]` section:\n"
            '  type = "github" + [provider.github] repo = "<owner>/<repo>"'
        )


class ConfigLoadError(HarnessError):
    """.kaji/config.toml の読み込み・検証エラー。"""

    def __init__(self, path: Path, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"Error loading {path}: {reason}")


class ProviderTypeRemovedError(ConfigLoadError):
    """``provider.type = "local"`` が設定された（local provider は削除済み）。

    ``ConfigLoadError`` のサブクラスなので、既存の ``except ConfigLoadError`` 経路
    （exit 2）がそのまま捕捉する。``kaji validate`` は本型だけを判別して exit 2 にする。
    """

    def __init__(self, path: Path):
        super().__init__(
            path,
            'provider.type = "local" is no longer supported: '
            "the local provider was removed after its deprecation in kaji v0.21.0. "
            'Set provider.type = "github" and [provider.github] repo = "<owner>/<repo>" '
            "(see docs/cli-guides/github-mode.md).",
        )


# --- ワークフロー定義エラー（起動時に検出） ---
class WorkflowValidationError(HarnessError):
    """ワークフロー YAML の静的検証エラー。"""

    def __init__(self, errors: list[str] | str):
        if isinstance(errors, list):
            self.errors = errors
            msg = f"{len(errors)} validation error(s): " + "; ".join(errors)
        else:
            self.errors = [errors]
            msg = errors
        super().__init__(msg)


class SeriesValidationError(HarnessError):
    """series YAML / state の検証エラー。"""

    def __init__(self, errors: list[str] | str):
        if isinstance(errors, list):
            self.errors = errors
        else:
            self.errors = [errors]
        super().__init__(
            f"{len(self.errors)} series validation error(s): " + "; ".join(self.errors)
        )


class SeriesInputError(HarnessError):
    """series の起動条件・resume 条件が満たされない。"""


class SeriesAbortedError(HarnessError):
    """member failure または外部状態不整合により series を停止した。"""


class SeriesRuntimeError(HarnessError):
    """series の child 起動・状態保存等で実行時エラーが発生した。"""


# --- スキル解決エラー ---
class SkillNotFound(HarnessError):
    """スキルファイルが見つからない。"""


class SecurityError(HarnessError):
    """パストラバーサル等のセキュリティ違反。"""


# --- CLI 実行エラー ---
class CLIExecutionError(HarnessError):
    """CLI プロセスが非ゼロ終了。

    Issue #403: interactive terminal の pane-dead 早期終了では、当該 attempt の
    session 解決結果を ``session_resolution`` として運ぶ。runner はこれを
    ``result.json`` の ``session_id`` へ写す。解決を試みない経路では ``None``
    のままにし、runner 側の既存 fallback を維持する。
    """

    def __init__(
        self,
        step_id: str,
        returncode: int,
        stderr: str,
        *,
        session_resolution: SessionResolution | None = None,
    ):
        self.step_id = step_id
        self.returncode = returncode
        self.stderr = stderr
        self.session_resolution = session_resolution
        super().__init__(f"Step '{step_id}' CLI exited with code {returncode}: {stderr[:200]}")


class CLINotFoundError(HarnessError):
    """CLI コマンドが見つからない（FileNotFoundError をラップ）。"""


class HerdrSessionRequiredError(CLINotFoundError):
    """Herdr backendをHerdr session外から起動した（Issue #322 / #396）。

    Herdr公式agent skillのcaller-context guardrailに対応する既知のユーザー前提エラーであり、
    failure triage の ``user_precondition_error`` を経て incident 記録の対象外になる。判定は
    ``run.log`` に載る **型名文字列** で行うため、クラス名は artifact 互換の契約面。

    基底は ``CLINotFoundError`` を維持する。runner の dispatch ``except`` タプル・
    cli 層の送出契約・exit code マッピングがいずれも ``CLINotFoundError`` を前提に
    しており、サブクラス化により fail-fast / retry / 終了コードの挙動が保存される。
    """


class ScriptExecutionError(HarnessError):
    """決定論 command の subprocess が非ゼロ終了。verdict 有無を問わず fail-loud。

    ``exec_script`` skill 経路（``execute_script``）と ``exec:`` step 経路
    （``execute_exec``）の双方で共有する（Issue #205）。``command_label`` は
    失敗 artifact 上の調査用ラベルで、``execute_script`` は module 名、
    ``execute_exec`` は ``" ".join(argv)`` を渡す。経路に依存しない中立表現に
    することで、どちらの dispatch の失敗かを誤解させない。

    メッセージには送出元が作った ``summary``（stderr の最後の失敗原因行を
    fingerprint と同じ正規化に通したもの）だけを入れる（Issue #516）。メッセージは
    ``result.json.error`` / incident の fingerprint に使われるため、原因と無関係な
    stderr 先頭を入れない。stderr 全文は ``stderr`` 属性と ``stderr.log`` に残る。
    正規化は application 層（``kaji_harness.recovery``）にあり、foundation の本 module
    からは使えないため、要約は送出元（``script_exec``）で作って渡す。
    """

    def __init__(
        self,
        step_id: str,
        command_label: str,
        returncode: int,
        stderr: str,
        *,
        summary: str,
    ):
        self.step_id = step_id
        self.command_label = command_label
        self.returncode = returncode
        self.stderr = stderr
        self.summary = summary
        super().__init__(
            f"Step '{step_id}' deterministic command '{command_label}' exited with "
            f"code {returncode}: {summary}"
        )


class SkillFrontmatterError(HarnessError):
    """SKILL.md frontmatter のパース / 検証エラー。"""

    def __init__(self, skill_name: str, reason: str):
        self.skill_name = skill_name
        self.reason = reason
        super().__init__(f"Skill '{skill_name}' frontmatter invalid: {reason}")


class StepTimeoutError(HarnessError):
    """ステップがタイムアウト。SIGTERM → SIGKILL 後に raise。

    Issue #222: kill 後に観測した ``process.returncode`` を ``returncode`` として
    運ぶ（best-effort）。timeout の kill は SIGTERM が初手のため通常 ``-15``、
    SIGKILL までエスカレートした場合は ``-9``。取得不能なら ``None``。
    runner はこの値から attempt result.json の ``exit_code`` / ``signal`` を導出する。

    Issue #403: interactive terminal の timeout では、当該 attempt の session 解決結果を
    ``session_resolution`` として同じ方式で運ぶ（解決を試みない経路では ``None``）。
    """

    def __init__(
        self,
        step_id: str,
        timeout: int,
        returncode: int | None = None,
        *,
        session_resolution: SessionResolution | None = None,
    ):
        self.step_id = step_id
        self.timeout = timeout
        self.returncode = returncode
        self.session_resolution = session_resolution
        super().__init__(f"Step '{step_id}' timed out after {timeout}s")


class WorkdirNotFoundError(HarnessError):
    """ステップ実行時に指定された workdir が存在しない。"""

    def __init__(self, step_id: str, workdir: Path):
        self.step_id = step_id
        self.workdir = workdir
        super().__init__(f"Step '{step_id}' workdir does not exist: {workdir}")


class TmpDirPreparationError(HarnessError):
    """attempt 固有の一時作業ディレクトリ（``tmp/kaji/...``）を用意できない。

    component 検査違反・``OSError``・境界不変条件違反を表す。番号の衝突
    （``FileExistsError``）は次の番号で再試行するためエラーにならない（Issue #508）。
    dispatch 前に送出され、dispatch は行われない。
    """

    def __init__(self, tmp_dir: Path, reason: str):
        self.tmp_dir = tmp_dir
        self.reason = reason
        super().__init__(f"Cannot prepare attempt tmp dir {tmp_dir}: {reason}")


class IssueContextResolutionError(HarnessError):
    """`provider.resolve_issue_context` が失敗した。

    Phase 3-c で導入、Phase 3-e で `[provider]` セクションが必須化されたため、
    Issue 解決失敗（Issue 不在 / ID 不正 等）は
    agent 起動前に常に fail-fast する。``cmd_run`` では `EXIT_RUNTIME_ERROR
    (= 3)` にマップされる。``[provider]`` 未設定 / 設定不整合の問題は
    ``ValueError`` として `cmd_run` 冒頭で `EXIT_INVALID_INPUT (= 2)` に
    正規化されるため、本例外には到達しない。
    """

    def __init__(self, issue_input: str, provider_type: str, cause: BaseException):
        self.issue_input = issue_input
        self.provider_type = provider_type
        self.cause = cause
        super().__init__(
            f"Failed to resolve IssueContext for {issue_input!r} under "
            f"provider.type={provider_type!r}: {type(cause).__name__}: {cause}"
        )


class RecoveryTargetError(HarnessError):
    """A requested recovery run is absent or not eligible for failure triage."""


class MissingResumeSessionError(HarnessError):
    """resume 指定ステップで継続元のセッション ID が見つからない。"""

    def __init__(self, step_id: str, resume_target: str):
        self.step_id = step_id
        self.resume_target = resume_target
        super().__init__(
            f"Step '{step_id}' requires resume from '{resume_target}' but no session ID found"
        )


# --- Verdict エラー ---
class VerdictNotFound(HarnessError):
    """出力に ---VERDICT--- ブロックがない。回復不能。"""


class VerdictParseError(HarnessError):
    """必須フィールド欠損。回復不能。"""


class VerdictMarkerResolutionError(HarnessError):
    """Issue comment verdict marker cannot be resolved safely."""


class VerdictMarkerNotFoundError(VerdictMarkerResolutionError):
    """No verdict marker exists for the requested step."""


class VerdictMarkerMalformedError(VerdictMarkerResolutionError):
    """The latest marker for the requested step is malformed."""


class VerdictMarkerMetaMissingError(VerdictMarkerResolutionError):
    """The latest marker lacks required metadata."""


class VerdictArtifactResolutionError(HarnessError):
    """Local ``verdict.yaml`` artifact fallback cannot produce a verdict safely."""


class VerdictArtifactNotFoundError(VerdictArtifactResolutionError):
    """No artifact verdict can be located for the requested step and run context."""


class VerdictArtifactUnusableError(VerdictArtifactResolutionError):
    """The latest attempt exists but its artifact is missing, corrupt, or contradictory."""


class InvalidVerdictValue(HarnessError):
    """on に未定義の status 値。プロンプト違反。回復不能・リトライしない。"""


# --- 遷移エラー ---
class InvalidTransition(HarnessError):
    """verdict.status に対応する遷移先が on に未定義。"""

    def __init__(self, step_id: str, verdict_status: str):
        self.step_id = step_id
        self.verdict_status = verdict_status
        super().__init__(f"Step '{step_id}' has no transition for verdict '{verdict_status}'")


# --- managed starter tracking エラー ---
class TrackingBodyError(HarnessError):
    """starter-sync tracking issue の本文が v1 schema に違反している（Issue #423）。

    ``kaji_harness.starter_tracking.parse_tracking_issue_body`` が raise する。
    呼び出し側の ``build_tracking_plan`` / ``build_task_plan`` はこれを捕捉し、
    観測矛盾として ``decision: ABORT`` の plan へ変換する（例外を上位へ伝播させない）。
    """
