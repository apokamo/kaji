"""Custom exceptions for dao_harness."""


class HarnessError(Exception):
    """ハーネスの基底例外。"""


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


# --- スキル解決エラー ---
class SkillNotFound(HarnessError):
    """スキルファイルが見つからない。"""


class SecurityError(HarnessError):
    """パストラバーサル等のセキュリティ違反。"""


# --- CLI 実行エラー ---
class CLIExecutionError(HarnessError):
    """CLI プロセスが非ゼロ終了。"""

    def __init__(self, step_id: str, returncode: int, stderr: str):
        self.step_id = step_id
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"Step '{step_id}' CLI exited with code {returncode}: {stderr[:200]}")


class CLINotFoundError(HarnessError):
    """CLI コマンドが見つからない（FileNotFoundError をラップ）。"""


class StepTimeoutError(HarnessError):
    """ステップがタイムアウト。SIGTERM → SIGKILL 後に raise。"""

    def __init__(self, step_id: str, timeout: int):
        self.step_id = step_id
        self.timeout = timeout
        super().__init__(f"Step '{step_id}' timed out after {timeout}s")


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


class InvalidVerdictValue(HarnessError):
    """on に未定義の status 値。プロンプト違反。回復不能・リトライしない。"""


# --- 遷移エラー ---
class InvalidTransition(HarnessError):
    """verdict.status に対応する遷移先が on に未定義。"""

    def __init__(self, step_id: str, verdict_status: str):
        self.step_id = step_id
        self.verdict_status = verdict_status
        super().__init__(f"Step '{step_id}' has no transition for verdict '{verdict_status}'")
