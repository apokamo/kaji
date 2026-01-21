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
| `handle_design` | `DesignState.DESIGN_REVIEW` | 常にレビューへ遷移 |
| `handle_design_review` | `DesignState.COMPLETE` or `DesignState.DESIGN` | PASS→完了, RETRY→再設計 |

### 使用例

```python
# Workflow内部での呼び出し（既存のworkflow.pyから）
next_state = self._handle_design(ctx, session)
# -> DesignState.DESIGN_REVIEW

next_state = self._handle_design_review(ctx, session)
# -> DesignState.COMPLETE (PASS) or DesignState.DESIGN (RETRY)
```

## 制約・前提条件

- AgentContext.issue_provider.issue_url が有効なGitHub Issue URL
- AgentContext.analyzer/reviewer が AIToolProtocol を実装
- プロンプトファイルが所定パスに存在

## 方針

### 1. ハンドラ実装パターン（v5準拠）

```python
def _handle_design(self, ctx: AgentContext, session: SessionState) -> Enum:
    # 1. artifacts ディレクトリ確保
    artifacts_dir = ctx.ensure_artifacts_dir("design")

    # 2. プロンプト読み込み（テンプレート変数展開）
    prompt = load_prompt("design", issue_url=ctx.issue_provider.issue_url, ...)

    # 3. AI呼び出し（会話継続のためsession_id使用）
    session_id = session.get_conversation_id("design")
    result, new_session_id = ctx.analyzer.run(
        prompt=prompt,
        context=ctx.issue_provider.issue_url,
        session_id=session_id,
        log_dir=artifacts_dir,
    )

    # 4. session_id更新
    if new_session_id:
        session.set_conversation_id("design", new_session_id)

    # 5. ループカウンタ・完了状態更新
    session.increment_loop("design")

    return DesignState.DESIGN_REVIEW
```

```python
def _handle_design_review(self, ctx: AgentContext, session: SessionState) -> Enum:
    # 1. artifacts ディレクトリ確保
    log_dir = ctx.ensure_artifacts_dir("design_review")

    # 2. プロンプト読み込み
    prompt = load_prompt("design_review", issue_url=ctx.issue_provider.issue_url)

    # 3. AI呼び出し（レビューは会話継続不要）
    decision, _ = ctx.reviewer.run(
        prompt=prompt,
        context=ctx.issue_provider.issue_url,
        log_dir=log_dir,
    )

    # 4. VERDICT解析（AI Formatter付き）
    ai_formatter = create_ai_formatter(ctx.reviewer, context=..., log_dir=...)
    verdict = parse_verdict(decision, ai_formatter=ai_formatter)

    # 5. ABORT処理
    handle_abort_verdict(verdict, decision)

    # 6. 状態遷移
    if verdict == Verdict.RETRY:
        return DesignState.DESIGN

    session.mark_completed("design_review")
    return DesignState.COMPLETE
```

### 2. プロンプトローダー

`src/core/prompts.py` に `load_prompt()` を新規作成:

```python
def load_prompt(name: str, **kwargs) -> str:
    """プロンプトファイルを読み込み、テンプレート変数を展開"""
    path = Path(__file__).parent.parent / "workflows" / "design" / "prompts" / f"{name}.md"
    template = path.read_text()
    return template.format(**kwargs)  # または string.Template
```

### 3. プロンプトファイル配置

```
src/workflows/design/prompts/
├── design.md           # v5 detail_design.md ベース
└── design_review.md    # v5 detail_design_review.md ベース
```

### 4. エラーハンドリング

| エラー | 対応 |
|--------|------|
| AI呼び出し失敗 | AIToolError を上位に伝播（Circuit Breaker対応は Phase 3） |
| VERDICT解析失敗 | VerdictParseError を上位に伝播 |
| ABORT | AgentAbortError を上位に伝播 |

## 検証観点

### 正常系

- DESIGN → analyzer呼び出し → session_id保存 → DESIGN_REVIEW遷移
- DESIGN_REVIEW (PASS) → reviewer呼び出し → COMPLETE遷移
- DESIGN_REVIEW (RETRY) → DESIGN遷移（ループ）

### 異常系

- AI呼び出し失敗時にAIToolError送出
- VERDICT解析失敗時にVerdictParseError送出
- ABORT時にAgentAbortError送出

### 境界値

- 複数回のRETRYループ（loop_counter動作確認）
- session_idが初回None、2回目以降は継続

## 参考

- [v5 handlers/design.py](/home/aki/claude/kamo2/.claude/agents/bugfix-v5/bugfix_agent/handlers/design.py)
- [v5 detail_design.md](/home/aki/claude/kamo2/.claude/agents/bugfix-v5/prompts/detail_design.md)
- [v5 detail_design_review.md](/home/aki/claude/kamo2/.claude/agents/bugfix-v5/prompts/detail_design_review.md)
