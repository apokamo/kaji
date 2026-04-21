# エラーハンドリング規約

kaji における例外処理の統一規約。`kaji_harness/errors.py` の HarnessError 階層を基礎とする。

## 基本原則

1. **階層的例外クラスを使用する** — 全カスタム例外は `HarnessError` を継承する
2. **握り潰し禁止** — `except Exception: pass` は書かない
3. **情報を付与して raise する** — 文脈情報（step_id, path 等）を例外に含める
4. **適切な粒度で wrap する** — 外部例外は内部例外に変換して re-raise する

## HarnessError 階層

`kaji_harness/errors.py` に定義された例外クラス。新規コードでは必ずこの階層内の例外を使用する。

```
HarnessError                        # 基底例外
├── ConfigNotFoundError             # .kaji/config.toml が見つからない
├── ConfigLoadError                 # config の読み込み・検証失敗
├── WorkflowValidationError         # ワークフロー YAML の静的検証エラー
├── SkillNotFound                   # スキルファイルが見つからない
├── SecurityError                   # パストラバーサル等のセキュリティ違反
├── CLIExecutionError               # CLI プロセスが非ゼロ終了
├── CLINotFoundError                # CLI コマンドが見つからない
├── StepTimeoutError                # ステップがタイムアウト
├── WorkdirNotFoundError            # 指定された workdir が存在しない
├── MissingResumeSessionError       # resume 指定でセッション ID が見つからない
├── VerdictNotFound                 # 出力に ---VERDICT--- ブロックがない
├── VerdictParseError               # 必須フィールド欠損
├── InvalidVerdictValue             # on に未定義の status 値
└── InvalidTransition               # verdict.status に対応する遷移先がない
```

## 例外クラスの選択

実装時は以下の基準で例外クラスを選ぶ。

| 状況 | 使用する例外 |
|------|------------|
| 設定ファイルが見つからない | `ConfigNotFoundError` |
| YAML の構文・スキーマ違反 | `WorkflowValidationError` |
| スキルファイルが存在しない | `SkillNotFound` |
| CLI が exit 非ゼロで終了 | `CLIExecutionError` |
| CLI コマンドが見つからない | `CLINotFoundError` |
| ステップがタイムアウト | `StepTimeoutError` |
| verdict ブロックがない | `VerdictNotFound` |
| verdict フィールドが欠損 | `VerdictParseError` |
| 不正な status 値 | `InvalidVerdictValue` |
| 既存クラスで表現できない新規エラー | 新クラスを `HarnessError` から派生 |

## 例外処理パターン

### 基本パターン：wrap して re-raise

```python
def load_skill(skill_name: str, skills_dir: Path) -> Path:
    """スキルファイルのパスを解決する。"""
    skill_path = skills_dir / skill_name

    try:
        resolved = skill_path.resolve(strict=True)
    except FileNotFoundError:
        raise SkillNotFound(f"Skill not found: {skill_name}") from None

    # パストラバーサル検証
    if not str(resolved).startswith(str(skills_dir)):
        raise SecurityError(f"Path traversal detected: {skill_name}")

    return resolved
```

### CLI 実行エラーの処理

```python
def execute_cli(cmd: list[str], step_id: str, timeout: int) -> CLIResult:
    """CLI コマンドを実行する。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise CLINotFoundError(f"Command not found: {cmd[0]}") from None
    except subprocess.TimeoutExpired:
        raise StepTimeoutError(step_id, timeout)

    if proc.returncode != 0:
        raise CLIExecutionError(step_id, proc.returncode, proc.stderr)

    return CLIResult(full_output=proc.stdout, stderr=proc.stderr)
```

### Verdict エラーの処理

Verdict エラー（`VerdictNotFound` / `VerdictParseError` / `InvalidVerdictValue`）は回復不能なため、リトライせずに上位に伝播させる。

```python
def parse_verdict(output: str) -> Verdict:
    """出力から verdict を解析する。"""
    match = VERDICT_PATTERN.search(output)
    if match is None:
        raise VerdictNotFound("---VERDICT--- block not found in output")

    block = match.group(1)
    data = _parse_yaml_block(block)

    if "status" not in data:
        raise VerdictParseError("Required field 'status' missing from verdict")

    return Verdict(
        status=data["status"],
        reason=data.get("reason", ""),
        evidence=data.get("evidence", ""),
        suggestion=data.get("suggestion", ""),
    )
```

### 上位での集約

`WorkflowRunner.run()` 等の最上位では全 `HarnessError` を catch してログ出力し、再 raise する。

```python
def run(self, workflow: Workflow, issue: int) -> None:
    """ワークフローを実行する。"""
    self.logger.log_workflow_start(issue, workflow.name)
    try:
        self._run_steps(workflow, issue)
    except HarnessError as e:
        self.logger.log_workflow_end(
            status="error",
            cycle_counts={},
            total_duration_ms=0,
            total_cost=None,
            error=str(e),
        )
        raise
```

## 禁止パターン

### 握り潰し

```python
# ❌ 禁止
try:
    result = parse_verdict(output)
except Exception:
    pass  # 黙って無視

# ✅ 少なくともログを出力し、可能なら re-raise
try:
    result = parse_verdict(output)
except VerdictNotFound:
    self.logger.log_step_end(step.id, ...)
    raise
```

### 情報なし re-raise

```python
# ❌ 文脈情報が失われる
try:
    skill_path = resolve_skill(step.skill)
except FileNotFoundError:
    raise HarnessError("error")  # step_id もパスも不明

# ✅ 文脈を付与する
try:
    skill_path = resolve_skill(step.skill)
except FileNotFoundError:
    raise SkillNotFound(f"Skill '{step.skill}' not found for step '{step.id}'") from None
```

### 広すぎる catch

```python
# ❌ すべての例外を同一視
try:
    result = run_step(step)
except Exception as e:
    logger.error(str(e))

# ✅ 期待する例外のみを catch
try:
    result = run_step(step)
except StepTimeoutError:
    logger.warning(f"Step {step.id} timed out, proceeding to timeout handler")
    return _handle_timeout(step)
except CLIExecutionError as e:
    logger.error(f"Step {step.id} CLI failed: exit={e.returncode}")
    raise
```

## 新規例外クラスの追加

既存クラスで表現できない新規エラーが必要な場合、以下のルールで追加する。

1. `HarnessError` またはその適切なサブクラスから派生する
2. `__init__` で文脈情報（step_id / path 等）を属性として保持する
3. エラーメッセージは英語で記述する（日本語 UI はないため）

```python
class WorkdirNotFoundError(HarnessError):
    """ステップ実行時に指定された workdir が存在しない。"""

    def __init__(self, step_id: str, workdir: Path):
        self.step_id = step_id
        self.workdir = workdir
        super().__init__(f"Step '{step_id}' workdir does not exist: {workdir}")
```

## テストでのエラー検証

```python
import pytest
from kaji_harness.errors import VerdictNotFound, CLIExecutionError


def test_parse_verdict_missing_block() -> None:
    with pytest.raises(VerdictNotFound):
        parse_verdict("no verdict block here")


def test_cli_exit_nonzero() -> None:
    with pytest.raises(CLIExecutionError) as exc_info:
        execute_cli(["false"], step_id="test", timeout=10)
    assert exc_info.value.returncode == 1
    assert exc_info.value.step_id == "test"
```

## チェックリスト

### 実装時チェック

- [ ] カスタム例外は `HarnessError` 階層から派生しているか
- [ ] `except Exception: pass` がないか
- [ ] 外部例外を catch する場合、文脈情報を付与して re-raise しているか
- [ ] Verdict エラーをリトライしていないか（回復不能）
- [ ] 新規例外クラスに `step_id` / `path` 等の属性が含まれているか

### レビュー時チェック

- [ ] catch する例外の粒度が適切か（広すぎないか）
- [ ] エラーメッセージに十分な文脈情報があるか
- [ ] `from e` / `from None` の使い分けが適切か

## 関連ドキュメント

- [Python スタイル規約](./python-style.md) — 全般的なコーディング規約
- [ロギング](./logging.md) — エラー発生時のログ出力
- `kaji_harness/errors.py` — 例外クラスの実装
