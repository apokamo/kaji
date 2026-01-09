# Architecture

## Overview

dev-agent-orchestra は、プラガブルなワークフローアーキテクチャを採用しています。

## Directory Structure

```
src/
├── __init__.py
├── cli.py                  # 統一CLI
├── orchestrator.py         # 汎用オーケストレータ (TODO)
│
├── core/                   # 共通ライブラリ
│   ├── __init__.py
│   ├── verdict.py          # VERDICT パーサー
│   ├── config.py           # 設定読み込み (TODO)
│   ├── session.py          # セッション管理 (TODO)
│   └── tools/
│       ├── __init__.py
│       ├── protocol.py     # AIToolProtocol
│       ├── claude.py       # ClaudeTool (TODO)
│       ├── codex.py        # CodexTool (TODO)
│       └── gemini.py       # GeminiTool (TODO)
│
└── workflows/
    ├── __init__.py
    ├── base.py             # WorkflowBase 抽象クラス
    │
    ├── design/             # 設計ワークフロー
    │   ├── __init__.py
    │   ├── states.py
    │   ├── workflow.py
    │   ├── handlers/
    │   └── prompts/
    │
    ├── implement/          # 実装ワークフロー (TODO)
    │   └── ...
    │
    └── bugfix/             # バグ修正ワークフロー (TODO)
        └── ...
```

## Core Components

### WorkflowBase

すべてのワークフローの基底クラス。以下を定義:

- `name`: ワークフロー名
- `states`: ステート列挙型
- `initial_state`: 初期ステート
- `terminal_states`: 終了ステート群
- `get_handler()`: ステートハンドラ取得
- `get_next_state()`: 次ステート決定
- `get_prompt_path()`: プロンプトファイルパス

### VERDICT Protocol

すべてのAIエージェントが出力する統一フォーマット:

```markdown
## VERDICT
- Result: PASS | RETRY | BACK_DESIGN | ABORT
- Reason: <判定理由>
- Evidence: <証拠>
- Suggestion: <次のアクション提案>
```

### AIToolProtocol

AIツール（Claude, Codex, Gemini）の共通インターフェース:

```python
class AIToolProtocol(Protocol):
    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]: ...
```

## Workflows

すべてのワークフローは `review → fix → verify` パターンを採用（[ADR-001](adr/001-review-cycle-pattern.md)）。

### Design Workflow

```
DESIGN ──(always)──> DESIGN_REVIEW
                          │
                    ┌─────┴─────┐
                    │           │
                  PASS        RETRY
                    │           │
                    v           v
                COMPLETE    DESIGN_FIX
                                │
                           (always)
                                v
                         DESIGN_VERIFY
                                │
                    ┌───────────┴───────────┐
                    │                       │
                  PASS                    RETRY
                    │                       │
                    v                       v
                COMPLETE                DESIGN_FIX
```

### Implement Workflow (TODO)

```
IMPLEMENT ──(always)──> IMPLEMENT_REVIEW
                              │
                    ┌─────────┼─────────┐
                    │         │         │
                  PASS      RETRY   BACK_DESIGN
                    │         │         │
                    v         v         v
                COMPLETE  IMPLEMENT_FIX  (external)
                              │
                         (always)
                              v
                       IMPLEMENT_VERIFY
                              │
                    ┌─────────┴─────────┐
                    │                   │
                  PASS                RETRY
                    │                   │
                    v                   v
                COMPLETE            IMPLEMENT_FIX
```

### Bugfix Workflow (TODO)

9ステートのフルワークフロー。bugfix-v5 から移植予定。
`review → fix → verify` パターンを適用予定。

## Related

- [DEVELOPMENT_WORKFLOW.md](DEVELOPMENT_WORKFLOW.md) - Claude Code スキルによる開発ワークフロー（`draft/design/` パターン等）
