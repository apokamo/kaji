# [設計] DesignWorkflow ハンドラ実装

Issue: #28

## 概要

DesignWorkflowの`handle_design`と`handle_design_review`を実装し、v5からプロンプトを移植する。

## 背景・目的

- Phase 1で整備したcore基盤（AgentContext, AIToolProtocol, Verdict, SessionState）を活用
- v5の実績あるハンドラパターンをdaoに適用
- DesignWorkflowを動作可能な状態にする

## SessionState 拡張

### 追加インターフェース

現行の `SessionState` に以下のメソッドを追加する。

```python
@dataclass
class SessionState:
    # 既存フィールド
    completed_states: list[str] = field(default_factory=list)
    loop_counters: dict[str, int] = field(default_factory=dict)
    active_conversations: dict[str, str | None] = field(default_factory=dict)
    max_loop_count: int = 3

    # 新規フィールド: ハンドラ間のコンテキスト共有用
    _context: dict[str, Any] = field(default_factory=dict)

    def set_context(self, key: str, value: Any) -> None:
        """Set a context value for cross-handler communication.

        Args:
            key: Context key (e.g., "design_output", "requirements_content").
            value: Any serializable value.

        Example:
            session.set_context("design_output", result)
        """
        self._context[key] = value

    def get_context(self, key: str, default: Any = None) -> Any:
        """Get a context value.

        Args:
            key: Context key.
            default: Default value if key not found.

        Returns:
            Stored value or default.

        Example:
            design_output = session.get_context("design_output", "")
        """
        return self._context.get(key, default)

    def clear_context(self, key: str) -> None:
        """Remove a context value.

        Args:
            key: Context key to remove.
        """
        self._context.pop(key, None)
```

### artifacts パスについて

**重要**: `artifacts_base_dir` は SessionState ではなく **AgentContext** が管理する。

| コンポーネント | 責務 |
|--------------|------|
| `AgentContext.artifacts_dir` | 実行単位の artifacts パス（`{base}/{issue_number}/{timestamp}`） |
| `AgentContext.ensure_artifacts_dir(state)` | ステート別ディレクトリ作成 |
| `SessionState._context` | ハンドラ間の値受け渡し（パス、出力テキスト等） |

設計書内で使用していた `session.artifacts_base_dir` は削除し、`ctx.ensure_artifacts_dir()` に統一する。

## インターフェース

### 入力

| ハンドラ | 引数 | 説明 |
|---------|------|------|
| `handle_design` | `ctx: AgentContext, session: SessionState` | 設計作成 |
| `handle_design_review` | `ctx: AgentContext, session: SessionState` | 設計レビュー |

### 出力

| ハンドラ | 戻り値 | 説明 |
|---------|--------|------|
| `handle_design` | `Verdict.PASS` | 常にPASS（レビュー判定はget_next_stateが行う） |
| `handle_design_review` | `Verdict` | PASS/RETRY/BACK_DESIGN/ABORT |

**修正点**: ハンドラは `Verdict` を返し、`WorkflowBase.get_next_state()` が `Verdict` から次ステートを決定する。これにより設計書とWorkflowBaseの契約が一致。

### 使用例

```python
# Orchestrator内部での呼び出し
verdict = handler(ctx, session)  # -> Verdict.PASS or Verdict.RETRY etc.
next_state = workflow.get_next_state(current_state, verdict)
```

## 制約・前提条件

- AgentContext.issue_provider.issue_url が有効なGitHub Issue URL
- AgentContext.analyzer/reviewer が AIToolProtocol を実装
- プロンプトファイルが所定パスに存在

## 方針

### 1. ハンドラ実装パターン（v5準拠 + 修正）

```python
def _handle_design(self, ctx: AgentContext, session: SessionState) -> Verdict:
    # 1. ループ上限チェック（RETRY無限ループ防止）
    if session.is_loop_exceeded("design"):
        raise LoopLimitExceededError(
            state="design",
            count=session.loop_counters.get("design", 0),
            max_count=session.max_loop_count,
        )

    # 2. artifacts ディレクトリ確保
    artifacts_dir = ctx.ensure_artifacts_dir("design")

    # 3. イベントログ: ハンドラ開始
    save_jsonl_log(artifacts_dir, "handler_start", {
        "handler": "design",
        "loop_count": session.loop_counters.get("design", 0),
    })

    # 4. 要求入力ファイルがあれば読み込み（CLIから設定済み）
    requirements_content = session.get_context("requirements_content", "")

    # 5. プロンプト読み込み（テンプレート変数展開 + 必須変数バリデーション）
    prompt_vars = {
        "issue_url": ctx.issue_provider.issue_url,
        "issue_body": ctx.issue_provider.get_issue_body(),
        "requirements": requirements_content,
    }
    prompt = load_prompt(
        self.get_prompt_path(DesignState.DESIGN),
        required_vars=["issue_url", "issue_body"],  # バリデーション対象
        **prompt_vars,
    )

    # 6. イベントログ: AI呼び出し前
    save_jsonl_log(artifacts_dir, "ai_call_start", {
        "role": "analyzer",
        "prompt_length": len(prompt),
    })

    # 7. AI呼び出し（会話継続のためrole名でsession_id管理）
    session_id = session.get_conversation_id("analyzer")  # ロール名で管理
    result, new_session_id = ctx.analyzer.run(
        prompt=prompt,
        context=ctx.issue_provider.issue_url,
        session_id=session_id,
        log_dir=artifacts_dir,
    )

    # 8. イベントログ: AI呼び出し後
    save_jsonl_log(artifacts_dir, "ai_call_end", {
        "role": "analyzer",
        "response_length": len(result),
        "session_id": new_session_id,
    })

    # 9. 証跡保存
    save_artifact(artifacts_dir, "prompt.md", prompt)
    save_artifact(artifacts_dir, "response.md", result)

    # 10. 設計出力をSessionStateに保存（レビューで参照）
    session.set_context("design_output", result)
    session.set_context("design_output_path", str(artifacts_dir / "response.md"))

    # 11. session_id更新（ロール名で保持）
    if new_session_id:
        session.set_conversation_id("analyzer", new_session_id)

    # 12. ループカウンタ更新
    session.increment_loop("design")

    # 13. イベントログ: ハンドラ終了
    save_jsonl_log(artifacts_dir, "handler_end", {
        "handler": "design",
        "verdict": "PASS",
    })

    # 14. DESIGNハンドラは常にPASS（レビューへ遷移）
    return Verdict.PASS
```

```python
def _handle_design_review(self, ctx: AgentContext, session: SessionState) -> Verdict:
    # 1. artifacts ディレクトリ確保
    log_dir = ctx.ensure_artifacts_dir("design_review")

    # 2. イベントログ: ハンドラ開始
    save_jsonl_log(log_dir, "handler_start", {
        "handler": "design_review",
    })

    # 3. 設計成果物を取得（DESIGNハンドラが保存したもの）
    design_output = session.get_context("design_output", "")
    design_output_path = session.get_context("design_output_path", "")

    if not design_output:
        raise PromptLoadError("Design output not found in session. Run DESIGN first.")

    # 4. プロンプト読み込み（設計成果物を含む）
    prompt = load_prompt(
        self.get_prompt_path(DesignState.DESIGN_REVIEW),
        required_vars=["issue_url", "design_output"],  # バリデーション対象
        issue_url=ctx.issue_provider.issue_url,
        design_output=design_output,
        design_output_path=design_output_path,
    )

    # 5. イベントログ: AI呼び出し前
    save_jsonl_log(log_dir, "ai_call_start", {
        "role": "reviewer",
        "prompt_length": len(prompt),
        "design_output_length": len(design_output),
    })

    # 6. AI呼び出し（レビューは新規会話）
    decision, _ = ctx.reviewer.run(
        prompt=prompt,
        context=ctx.issue_provider.issue_url,
        log_dir=log_dir,
    )

    # 7. イベントログ: AI呼び出し後
    save_jsonl_log(log_dir, "ai_call_end", {
        "role": "reviewer",
        "response_length": len(decision),
    })

    # 8. 証跡保存
    save_artifact(log_dir, "prompt.md", prompt)
    save_artifact(log_dir, "response.md", decision)

    # 9. VERDICT解析（AI Formatter付き）
    ai_formatter = create_ai_formatter(ctx.reviewer, context="", log_dir=log_dir)

    # 10. イベントログ: VERDICT解析開始
    save_jsonl_log(log_dir, "verdict_parse_start", {
        "raw_response_length": len(decision),
    })

    verdict = parse_verdict(decision, ai_formatter=ai_formatter, max_retries=2)

    # 11. 証跡にVERDICT追記
    save_artifact(log_dir, "verdict.txt", verdict.value)

    # 12. イベントログ: VERDICT確定（元のverdictを保持）
    original_verdict = verdict
    save_jsonl_log(log_dir, "verdict_determined", {
        "verdict": verdict.value,
        "original_verdict": original_verdict.value,  # 変換前の値も記録
    })

    # 13. ABORT処理（例外送出）
    handle_abort_verdict(verdict, decision)

    # 14. BACK_DESIGN処理（本ワークフローでは RETRY と同じ扱い）
    if verdict == Verdict.BACK_DESIGN:
        # イベントログ: BACK_DESIGN→RETRY変換を記録
        save_jsonl_log(log_dir, "verdict_converted", {
            "original": "BACK_DESIGN",
            "converted_to": "RETRY",
            "reason": "DesignWorkflow treats BACK_DESIGN as RETRY",
        })
        # DesignWorkflowでは BACK_DESIGN は RETRY 相当
        # Implement/BugfixWorkflowでは外部への遷移シグナル
        verdict = Verdict.RETRY

    # 15. 完了マーク（PASSの場合）
    if verdict == Verdict.PASS:
        session.mark_completed("design_review")
        session.reset_loop("design")  # 次のワークフロー用にリセット

    # 16. イベントログ: ハンドラ終了
    save_jsonl_log(log_dir, "handler_end", {
        "handler": "design_review",
        "original_verdict": original_verdict.value,
        "final_verdict": verdict.value,
    })

    return verdict
```

### 2. Verdict契約の明確化

**修正点**: ハンドラは直接次ステートを返さず、`Verdict`を返す。

| コンポーネント | 責務 |
|--------------|------|
| ハンドラ | AI呼び出し、VERDICT解析、`Verdict`を返す |
| `WorkflowBase.get_next_state()` | `Verdict`から次ステートを決定 |
| Orchestrator | ハンドラ呼び出し→get_next_state→状態遷移 |

**DESIGNハンドラの特殊ケース**: 設計生成自体は常に成功（VERDICTなし）。`Verdict.PASS`を返してget_next_stateに判定を委譲。

### 3. ループ制御

**修正点**: ループ上限チェックと例外送出を追加。

```python
# src/core/errors.py に追加
class LoopLimitExceededError(Exception):
    """Raised when loop count exceeds the maximum."""
    def __init__(self, state: str, count: int, max_count: int) -> None:
        self.state = state
        self.count = count
        self.max_count = max_count
        super().__init__(f"Loop limit exceeded for {state}: {count} >= {max_count}")
```

**フロー**:
1. `handle_design` 開始時に `is_loop_exceeded()` チェック
2. 超過時は `LoopLimitExceededError` 送出
3. Orchestrator がキャッチして Issue に報告、ワークフロー終了

### 4. conversation_id のロール名管理

**修正点**: ステート名ではなくロール名でconversation_idを管理。

| キー | 用途 |
|-----|------|
| `"analyzer"` | 設計生成の会話継続 |
| `"reviewer"` | レビューの会話（通常は新規） |
| `"implementer"` | 実装の会話継続（Implement/Bugfix用） |

**理由**: 同一ロールが複数ステートで呼ばれる場合（例: analyzerがDESIGNとDESIGN_FIXで使われる）に会話を継続可能。

### 5. プロンプトローダー

**修正点**: str.formatの危険性を考慮し、string.Templateを使用。

```python
# src/core/prompts.py
from pathlib import Path
from string import Template
import re
import logging

logger = logging.getLogger(__name__)


class PromptLoadError(Exception):
    """Raised when prompt file cannot be loaded or formatted."""
    pass


def extract_template_variables(template_text: str) -> set[str]:
    """Extract all variable names from a string.Template text.

    Args:
        template_text: Template text with ${var} placeholders.

    Returns:
        Set of variable names found in the template.

    Example:
        >>> extract_template_variables("Hello ${name}, your ${item} is ready")
        {'name', 'item'}
    """
    # Match ${identifier} but not $${escaped}
    pattern = r'(?<!\$)\$\{(\w+)\}'
    return set(re.findall(pattern, template_text))


def load_prompt(
    relative_path: str,
    *,
    required_vars: list[str] | None = None,
    **kwargs: str,
) -> str:
    """Load prompt file and substitute template variables.

    Uses string.Template which safely handles missing keys and
    allows literal ${...} by using $$.

    Args:
        relative_path: Relative path from src/ directory
        required_vars: List of variable names that MUST be provided.
                      If any are missing, raises PromptLoadError.
                      If None, auto-detects from template.
        **kwargs: Template variables to substitute

    Returns:
        Formatted prompt text

    Raises:
        PromptLoadError: If file not found, required vars missing,
                        or template substitution fails
    """
    src_dir = Path(__file__).parent.parent
    path = src_dir / relative_path

    if not path.exists():
        raise PromptLoadError(f"Prompt file not found: {path}")

    try:
        template_text = path.read_text(encoding="utf-8")
    except Exception as e:
        raise PromptLoadError(f"Failed to read prompt {path}: {e}") from e

    # 必須変数のバリデーション
    if required_vars:
        missing = [var for var in required_vars if var not in kwargs or not kwargs[var]]
        if missing:
            raise PromptLoadError(
                f"Missing required prompt variables for {relative_path}: {missing}"
            )

    # テンプレート内の変数を静的解析して警告
    template_vars = extract_template_variables(template_text)
    provided_vars = set(kwargs.keys())
    undefined_vars = template_vars - provided_vars
    if undefined_vars:
        logger.warning(
            f"Template {relative_path} has undefined variables: {undefined_vars}. "
            "These will remain as ${var} in the output."
        )

    try:
        template = Template(template_text)
        # safe_substitute: 未定義変数は ${varname} のまま残す
        return template.safe_substitute(**kwargs)
    except Exception as e:
        raise PromptLoadError(f"Failed to process prompt {path}: {e}") from e


# プロンプト内で使用可能な変数をドキュメント化
# テンプレートファイル変更時はこのリストも更新すること
PROMPT_VARIABLES = {
    "design": {
        "required": ["issue_url", "issue_body"],
        "optional": ["requirements"],  # --input 経由で提供される場合
        "description": {
            "issue_url": "GitHub Issue URL",
            "issue_body": "Issue本文（Markdown）",
            "requirements": "追加要件ファイルの内容（オプション、空文字列がデフォルト）",
        },
    },
    "design_review": {
        "required": ["issue_url", "design_output"],
        "optional": ["design_output_path"],
        "description": {
            "issue_url": "GitHub Issue URL",
            "design_output": "設計ドキュメントの内容（または先頭サマリ）",
            "design_output_path": "設計ドキュメントのファイルパス",
        },
    },
}
```

**オプション変数の扱い**:
- `requirements` は常に渡す（未提供の場合は空文字列）
- テンプレート側で `${requirements}` が空の場合は何も表示されない
- これにより「プレースホルダが残る」問題を回避

```python
# handle_design 内
prompt_vars = {
    "issue_url": ctx.issue_provider.issue_url,
    "issue_body": ctx.issue_provider.get_issue_body(),
    "requirements": session.get_context("requirements_content", ""),  # 空文字列がデフォルト
}
```

**テンプレート変数の静的解析**:
- `extract_template_variables()` でテンプレート内の `${var}` を自動検出
- 提供されていない変数は警告ログを出力（処理は継続）
- 必須変数が欠落した場合は `PromptLoadError` を送出

**静的解析の実行タイミング**:
| タイミング | 動作 | 失敗時 |
|-----------|------|-------|
| 実行時（`load_prompt` 内） | 未定義変数を警告ログ出力 | 処理継続（警告のみ） |
| CI（Phase 3以降で追加） | テンプレートと `PROMPT_VARIABLES` の整合性チェック | テスト失敗 |

**Phase 2での運用**:
- 実行時の警告ログで開発者が気づく
- テンプレート変更時は手動で `PROMPT_VARIABLES` を更新
- 漏れがあっても `safe_substitute` により致命的エラーにはならない

**テンプレートファイル管理**:
- 各テンプレートの先頭にコメントで変数リストを記載（自己文書化）
- `PROMPT_VARIABLES` でコード側からも参照可能
- テンプレート変更時は両方を更新するルール

### 6. プロンプトファイル配置とエスケープ規則

```
src/workflows/design/prompts/
├── design.md           # v5 detail_design.md ベース
└── design_review.md    # v5 detail_design_review.md ベース
```

**エスケープ規則**:
- `${variable}` → テンプレート変数（展開される）
- `$${literal}` → リテラル `${literal}`（展開されない）
- `$` 単体 → そのまま

### 6.1 design_output の長大化対策

**問題**: 設計出力全文をプロンプトにインライン展開すると、トークン数が膨大になる可能性がある。

**対策**:

```python
# src/core/prompts.py

# 設計出力の最大文字数（超過時はサマリ化）
MAX_INLINE_CONTENT_LENGTH = 10_000


def summarize_for_prompt(content: str, max_length: int = MAX_INLINE_CONTENT_LENGTH) -> str:
    """Summarize long content for prompt inclusion.

    Args:
        content: Original content.
        max_length: Maximum length before summarization.

    Returns:
        Original content if under limit, otherwise summarized version.
    """
    if len(content) <= max_length:
        return content

    # 先頭と末尾を抽出してサマリ化
    head_length = max_length // 2 - 50
    tail_length = max_length // 2 - 50

    return (
        content[:head_length]
        + "\n\n... [中略: 全文は ${design_output_path} を参照] ...\n\n"
        + content[-tail_length:]
    )
```

**使用箇所**:
```python
# handle_design_review 内
design_output = session.get_context("design_output", "")
design_output_path = session.get_context("design_output_path", "")

# プロンプトには要約版を使用
design_output_for_prompt = summarize_for_prompt(design_output)

prompt = load_prompt(
    self.get_prompt_path(DesignState.DESIGN_REVIEW),
    required_vars=["issue_url", "design_output"],
    issue_url=ctx.issue_provider.issue_url,
    design_output=design_output_for_prompt,  # 要約版
    design_output_path=design_output_path,   # フルパス（参照用）
)
```

**プロンプト側の対応**:
- `design_review.md` テンプレートに「全文は ${design_output_path} を参照してください」の文言を追加
- レビューアは必要に応じてファイルを直接参照

### 7. 監査ログ（証跡保存）

**修正点**: 全呼び出しの証跡を保存。

```python
# src/core/artifacts.py
from pathlib import Path
from datetime import datetime


def save_artifact(
    artifacts_dir: Path,
    filename: str,
    content: str,
    *,
    append: bool = False,
) -> Path:
    """Save artifact to the specified directory.

    Args:
        artifacts_dir: Directory to save to (must exist)
        filename: Name of the artifact file
        content: Content to save
        append: If True, append to existing file

    Returns:
        Path to the saved file
    """
    filepath = artifacts_dir / filename
    mode = "a" if append else "w"
    filepath.write_text(content, encoding="utf-8")
    return filepath


def save_jsonl_log(
    artifacts_dir: Path,
    event_type: str,
    data: dict,
) -> None:
    """Append event to JSONL log file.

    Best-effort logging: IO failures are logged as warnings but do not
    stop workflow execution.

    Args:
        artifacts_dir: Directory containing log file
        event_type: Type of event (e.g., "ai_call", "verdict")
        data: Event data dictionary
    """
    import json
    import sys

    log_path = artifacts_dir / "events.jsonl"
    event = {
        "timestamp": datetime.now().isoformat(),
        "type": event_type,
        **data,
    }
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        # ログ失敗はワークフローを止めない（best-effort）
        print(f"Warning: Failed to write event log: {e}", file=sys.stderr)
```

**保存される証跡**:
- `prompt.md` - 実行したプロンプト
- `response.md` - AI応答
- `verdict.txt` - 解析されたVERDICT値
- `events.jsonl` - 全イベントのタイムスタンプ付きログ

### 8. アーキテクチャ整合性

**確認点**: architecture.md の4ステートパターン vs 本設計の2ステートパターン

**結論**: Phase 2では2ステート（DESIGN ⇄ DESIGN_REVIEW）で実装。理由:
1. MVP（最小実装）としてまず動作を確認
2. FIX/VERIFYはPhase 3以降で拡張
3. architecture.md は将来像を示しており、段階的に実装

**対応**: 設計書に明記し、architecture.md にTODOコメントを追加予定。

### 9. CLI/入力との接続

**確認点**: `dao design --issue <url>` の扱い

**CLI仕様変更**: 現行の `design` サブコマンドに `--issue` 引数を追加する。

```python
# src/cli.py の design_parser 変更
design_parser = subparsers.add_parser("design", help="Design workflow")
design_parser.add_argument("--issue", "-I", required=True, help="GitHub issue URL")
design_parser.add_argument("--input", "-i", help="Optional input requirements file")
design_parser.add_argument("--output", "-o", help="Output design file")
```

**変更理由**:
- DesignWorkflow は Issue 起点で動作する（IssueProvider が必要）
- `--issue` は必須、`--input` はオプション（追加要件がある場合のみ）
- `bugfix` サブコマンドとの整合性を確保

**修正**: Issue本文への追記はリスキー（再実行で重複、他ワークフローと競合）。artifacts保存方式に変更。

**設計**:
```python
# src/cli/commands/design.py
def setup_workflow_context(
    args: argparse.Namespace,
    ctx: AgentContext,
    session: SessionState,
) -> None:
    """CLI引数からワークフロー実行コンテキストを設定.

    Args:
        args: CLI引数（args.input が入力ファイルパス）
        ctx: AgentContext（artifacts パス管理）
        session: SessionState（コンテキスト共有）

    Raises:
        FileNotFoundError: 入力ファイルが存在しない場合
    """
    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        requirements_content = input_path.read_text(encoding="utf-8")

        # SessionStateに保存（ハンドラからアクセス可能）
        session.set_context("requirements_content", requirements_content)
        session.set_context("requirements_path", str(input_path.absolute()))

        # artifacts にコピー（証跡として保存）
        # AgentContext.ensure_artifacts_dir を使用
        input_dir = ctx.ensure_artifacts_dir("input")
        (input_dir / "requirements.md").write_text(
            requirements_content, encoding="utf-8"
        )
```

**呼び出しタイミング**: CLI の `design` コマンドハンドラ内で、Orchestrator 起動前に呼び出す。

```python
# src/cli/commands/design.py
def run_design_command(args: argparse.Namespace) -> int:
    # 1. AgentContext 作成（--issue 引数から）
    ctx = create_context(args.issue)  # args.issue = GitHub Issue URL

    # 2. SessionState 作成
    session = SessionState()

    # 3. ワークフローコンテキスト設定（--input 処理）
    setup_workflow_context(args, ctx, session)

    # 4. Orchestrator 起動
    orchestrator = Orchestrator(DesignWorkflow(), ctx, session)
    return orchestrator.run()
```

**フロー**:
1. CLI: `create_context()` で AgentContext 作成
2. CLI: `SessionState()` 作成
3. CLI: `setup_workflow_context()` で `--input` ファイルを読み込み
   - `session.set_context()` でハンドラに共有
   - `ctx.ensure_artifacts_dir("input")` で証跡保存
4. Orchestrator 起動
5. ハンドラ: `session.get_context("requirements_content")` で取得
6. プロンプト: `${requirements}` 変数で内容にアクセス

**メリット**:
- Issue本文を汚さない
- 再実行しても重複しない
- 他ワークフローと競合しない
- 証跡がartifactsに残る
- `ctx.ensure_artifacts_dir()` により既存APIと整合

### 10. エラーハンドリング階層

| エラー | 発生箇所 | 対応 |
|--------|---------|------|
| `AIToolError` | analyzer/reviewer.run() | Orchestratorでキャッチ → Issue報告 → 終了 |
| `VerdictParseError` | parse_verdict() | Orchestratorでキャッチ → Issue報告 → 終了 |
| `AgentAbortError` | handle_abort_verdict() | Orchestratorでキャッチ → Issue報告 → 終了 |
| `LoopLimitExceededError` | handle_design() | Orchestratorでキャッチ → Issue報告 → 終了 |
| `PromptLoadError` | load_prompt() | Orchestratorでキャッチ → Issue報告 → 終了 |

**Orchestratorの責務**: 全例外をキャッチし、Issue へエラー報告コメントを投稿。

### 11. AI Formatter の改善

**指摘**: reviewer が reviewer 自身でフォーマットするリスク

**対応**:
1. max_retries=2 で制限（無限ループ防止）
2. フォーマット結果をログに保存（デバッグ用）
3. 将来的には軽量モデル（claude-haiku）を使用可能

```python
ai_formatter = create_ai_formatter(
    ctx.reviewer,  # 現時点では同じツール
    context="",
    log_dir=log_dir,
)
```

**注**: Phase 2ではリスクを許容。Phase 3以降でフォーマッタ専用ツール検討。

## テスト戦略

### ユニットテスト

| 対象 | テストケース |
|------|------------|
| `load_prompt` | ファイル存在、変数展開、エスケープ、ファイル不存在、**必須変数欠落でPromptLoadError**、**未定義変数の警告ログ** |
| `extract_template_variables` | 変数抽出、エスケープ済み変数の除外 |
| `summarize_for_prompt` | 短いコンテンツはそのまま、長いコンテンツは先頭・末尾抽出 |
| `save_artifact` | 通常保存、追記モード、ディレクトリ不存在 |
| `save_jsonl_log` | イベント追記、タイムスタンプ付与、複数イベント、**IO失敗時の警告出力と処理継続** |
| `LoopLimitExceededError` | ループ上限超過検出 |
| `SessionState.set_context/get_context` | 値の設定・取得、デフォルト値、clear_context |
| `handle_design` | 正常フロー、ループ上限、AI呼び出し失敗、**設計出力のSessionState保存** |
| `handle_design_review` | PASS/RETRY/BACK_DESIGN/ABORT各パターン、**設計成果物が無い場合のエラー**、**BACK_DESIGN→RETRY変換のログ記録**、**長大な設計出力のサマリ化** |
| `setup_workflow_context` | 入力ファイル読み込み、**ctx.ensure_artifacts_dir経由のartifacts保存**、ファイル不存在エラー |

### 統合テスト（MockTool使用）

```python
def test_design_review_retry_flow():
    """DESIGN → DESIGN_REVIEW(RETRY) → DESIGN → DESIGN_REVIEW(PASS) → COMPLETE"""
    mock_reviewer = MockTool(responses=[
        "## VERDICT\n- Result: RETRY\n- Reason: Not complete",
        "## VERDICT\n- Result: PASS\n- Reason: LGTM",
    ])
    # ...
```

### E2Eテスト

`pytest tests/workflows/design/` で実行。実際のCLI経由でフロー検証。

## 検証観点

### 正常系

- DESIGN → Verdict.PASS → DESIGN_REVIEW遷移
- DESIGN_REVIEW (PASS) → Verdict.PASS → COMPLETE遷移
- DESIGN_REVIEW (RETRY) → Verdict.RETRY → DESIGN遷移（ループ）

### 異常系

- ループ上限超過時に LoopLimitExceededError 送出
- AI呼び出し失敗時に AIToolError 送出
- VERDICT解析失敗時に VerdictParseError 送出
- ABORT時に AgentAbortError 送出

### 境界値

- 複数回のRETRYループ（loop_counter動作確認）
- session_idが初回None、2回目以降は継続
- max_loop_count=3 でちょうど3回目の呼び出し

## 参考

- [v5 handlers/design.py](/home/aki/claude/kamo2/.claude/agents/bugfix-v5/bugfix_agent/handlers/design.py)
- [v5 detail_design.md](/home/aki/claude/kamo2/.claude/agents/bugfix-v5/prompts/detail_design.md)
- [v5 detail_design_review.md](/home/aki/claude/kamo2/.claude/agents/bugfix-v5/prompts/detail_design_review.md)
