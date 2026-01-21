# [設計] DesignWorkflow ハンドラ実装

Issue: #28

## 概要

DesignWorkflowの`handle_design`と`handle_design_review`を実装し、v5からプロンプトを移植する。

## 背景・目的

- Phase 1で整備したcore基盤（AgentContext, AIToolProtocol, Verdict, SessionState）を活用
- v5の実績あるハンドラパターンをdaoに適用
- DesignWorkflowを動作可能な状態にする

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

    # 3. プロンプト読み込み（テンプレート変数展開）
    prompt = load_prompt(
        self.get_prompt_path(DesignState.DESIGN),
        issue_url=ctx.issue_provider.issue_url,
        issue_body=ctx.issue_provider.get_issue_body(),
    )

    # 4. AI呼び出し（会話継続のためrole名でsession_id管理）
    session_id = session.get_conversation_id("analyzer")  # ロール名で管理
    result, new_session_id = ctx.analyzer.run(
        prompt=prompt,
        context=ctx.issue_provider.issue_url,
        session_id=session_id,
        log_dir=artifacts_dir,
    )

    # 5. 証跡保存
    save_artifact(artifacts_dir, "prompt.md", prompt)
    save_artifact(artifacts_dir, "response.md", result)

    # 6. session_id更新（ロール名で保持）
    if new_session_id:
        session.set_conversation_id("analyzer", new_session_id)

    # 7. ループカウンタ更新
    session.increment_loop("design")

    # 8. DESIGNハンドラは常にPASS（レビューへ遷移）
    return Verdict.PASS
```

```python
def _handle_design_review(self, ctx: AgentContext, session: SessionState) -> Verdict:
    # 1. artifacts ディレクトリ確保
    log_dir = ctx.ensure_artifacts_dir("design_review")

    # 2. プロンプト読み込み
    prompt = load_prompt(
        self.get_prompt_path(DesignState.DESIGN_REVIEW),
        issue_url=ctx.issue_provider.issue_url,
    )

    # 3. AI呼び出し（レビューは新規会話）
    decision, _ = ctx.reviewer.run(
        prompt=prompt,
        context=ctx.issue_provider.issue_url,
        log_dir=log_dir,
    )

    # 4. 証跡保存
    save_artifact(log_dir, "prompt.md", prompt)
    save_artifact(log_dir, "response.md", decision)

    # 5. VERDICT解析（AI Formatter付き）
    ai_formatter = create_ai_formatter(ctx.reviewer, context="", log_dir=log_dir)
    verdict = parse_verdict(decision, ai_formatter=ai_formatter, max_retries=2)

    # 6. 証跡にVERDICT追記
    save_artifact(log_dir, "verdict.txt", verdict.value)

    # 7. ABORT処理（例外送出）
    handle_abort_verdict(verdict, decision)

    # 8. BACK_DESIGN処理（本ワークフローでは RETRY と同じ扱い）
    if verdict == Verdict.BACK_DESIGN:
        # DesignWorkflowでは BACK_DESIGN は RETRY 相当
        # Implement/BugfixWorkflowでは外部への遷移シグナル
        return Verdict.RETRY

    # 9. 完了マーク（PASSの場合）
    if verdict == Verdict.PASS:
        session.mark_completed("design_review")
        session.reset_loop("design")  # 次のワークフロー用にリセット

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


class PromptLoadError(Exception):
    """Raised when prompt file cannot be loaded or formatted."""
    pass


def load_prompt(relative_path: str, **kwargs: str) -> str:
    """Load prompt file and substitute template variables.

    Uses string.Template which safely handles missing keys and
    allows literal ${...} by using $$.

    Args:
        relative_path: Relative path from src/ directory
        **kwargs: Template variables to substitute

    Returns:
        Formatted prompt text

    Raises:
        PromptLoadError: If file not found or template substitution fails
    """
    src_dir = Path(__file__).parent.parent
    path = src_dir / relative_path

    if not path.exists():
        raise PromptLoadError(f"Prompt file not found: {path}")

    try:
        template_text = path.read_text(encoding="utf-8")
        template = Template(template_text)
        # safe_substitute: 未定義変数は ${varname} のまま残す
        return template.safe_substitute(**kwargs)
    except Exception as e:
        raise PromptLoadError(f"Failed to process prompt {path}: {e}") from e


# プロンプト内で使用可能な変数をドキュメント化
PROMPT_VARIABLES = {
    "design": ["issue_url", "issue_body"],
    "design_review": ["issue_url"],
}


def validate_prompt_variables(prompt_name: str, provided: dict[str, str]) -> list[str]:
    """Validate that all required variables are provided.

    Returns:
        List of missing variable names (empty if all provided)
    """
    required = PROMPT_VARIABLES.get(prompt_name, [])
    return [var for var in required if var not in provided]
```

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

    Args:
        artifacts_dir: Directory containing log file
        event_type: Type of event (e.g., "ai_call", "verdict")
        data: Event data dictionary
    """
    import json
    log_path = artifacts_dir / "events.jsonl"
    event = {
        "timestamp": datetime.now().isoformat(),
        "type": event_type,
        **data,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
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

**確認点**: `dao design --input requirements.md` の扱い

**設計**:
```python
# CLI側で入力ファイルを処理
if args.input:
    requirements_content = Path(args.input).read_text()
    # Issue本文に追記、または artifacts にコピー
    ctx.issue_provider.update_body(
        ctx.issue_provider.get_issue_body() + "\n\n## Requirements\n" + requirements_content
    )
```

**方針**: 入力ファイルの内容はIssue本文に追記される。プロンプトは `${issue_body}` 経由でアクセス。

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
| `load_prompt` | ファイル存在、変数展開、エスケープ、ファイル不存在 |
| `save_artifact` | 通常保存、追記モード、ディレクトリ不存在 |
| `LoopLimitExceededError` | ループ上限超過検出 |
| `handle_design` | 正常フロー、ループ上限、AI呼び出し失敗 |
| `handle_design_review` | PASS/RETRY/BACK_DESIGN/ABORT各パターン |

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
