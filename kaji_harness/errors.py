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


# --- cache 同期エラー ---
class SyncError(RuntimeError):
    """``kaji sync`` 固有のエラー（config 不在 / gh CLI 不在 / API 失敗等）。

    Issue #285 で ``sync.py`` から foundation 層へ移設した。``providers.cache_guard``
    （下位層）が raise し ``sync`` / ``commands`` （上位層）が catch するため、
    どちらにも属さない ``errors`` に置く。

    基底は ``RuntimeError`` を維持する。``HarnessError`` に付け替えると
    ``except RuntimeError`` の到達範囲が変わり振る舞い変更になるため、基底の統一は
    本 Issue の scope 外（`draft/design/issue-285-refactor-private-import-r3.md`
    § 制約・前提条件）。
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
            "sections (template in `docs/cli-guides/local-mode.md` § 2),\n"
            "then add a `[provider]` section:\n"
            '  - For GitHub:    type = "github" + [provider.github] repo = "<owner>/<repo>"\n'
            '  - For local-first: type = "local"  (then run `kaji local init`\n'
            "                    to write the gitignored machine_id overlay)."
        )


class ConfigLoadError(HarnessError):
    """.kaji/config.toml の読み込み・検証エラー。"""

    def __init__(self, path: Path, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"Error loading {path}: {reason}")


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


class TmuxSessionRequiredError(CLINotFoundError):
    """interactive terminal runner を tmux セッション外から起動した（Issue #322）。

    調査を要さない既知のユーザー前提エラーであり、failure triage の
    ``user_precondition_error`` を経て incident 記録の対象外になる。判定は
    ``run.log`` に載る **型名文字列** で行うため、クラス名は artifact 互換の契約面。
    改名すると過去 run の再 triage で分類が変わる。

    基底は ``CLINotFoundError`` を維持する。runner の dispatch ``except`` タプル・
    cli 層の送出契約・exit code マッピングがいずれも ``CLINotFoundError`` を前提に
    しており、サブクラス化により fail-fast / retry / 終了コードの挙動が保存される。
    """


class HerdrSessionRequiredError(CLINotFoundError):
    """Herdr backendをHerdr session外から起動した。

    Herdr公式agent skillのcaller-context guardrailに対応する既知のユーザー前提エラー。
    ``TmuxSessionRequiredError`` と同様にartifactへ残る型名は互換契約として扱う。
    """


class ScriptExecutionError(HarnessError):
    """決定論 command の subprocess が非ゼロ終了。verdict 有無を問わず fail-loud。

    ``exec_script`` skill 経路（``execute_script``）と ``exec:`` step 経路
    （``execute_exec``）の双方で共有する（Issue #205）。``command_label`` は
    失敗 artifact 上の調査用ラベルで、``execute_script`` は module 名、
    ``execute_exec`` は ``" ".join(argv)`` を渡す。経路に依存しない中立表現に
    することで、どちらの dispatch の失敗かを誤解させない。
    """

    def __init__(self, step_id: str, command_label: str, returncode: int, stderr: str):
        self.step_id = step_id
        self.command_label = command_label
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"Step '{step_id}' deterministic command '{command_label}' exited with "
            f"code {returncode}: {stderr[:200]}"
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


class IssueContextResolutionError(HarnessError):
    """`provider.resolve_issue_context` が失敗した。

    Phase 3-c で導入、Phase 3-e で `[provider]` セクションが必須化されたため、
    Issue 解決失敗（machine_id 不在 / Issue dir 不在 / cache 不整合 等）は
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


class InvalidVerdictValue(HarnessError):
    """on に未定義の status 値。プロンプト違反。回復不能・リトライしない。"""


# --- 遷移エラー ---
class InvalidTransition(HarnessError):
    """verdict.status に対応する遷移先が on に未定義。"""

    def __init__(self, step_id: str, verdict_status: str):
        self.step_id = step_id
        self.verdict_status = verdict_status
        super().__init__(f"Step '{step_id}' has no transition for verdict '{verdict_status}'")
