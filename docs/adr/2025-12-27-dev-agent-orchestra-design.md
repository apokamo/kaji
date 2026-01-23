# ADR: dev-agent-orchestra 設計検討

> **Status**: Proposed
> **Date**: 2025-12-27
> **Deciders**: @apokamo
> **Related**: bugfix-v5 (`.claude/agents/bugfix-v5/`)

---

## 1. 背景

### 1.1 現状 (bugfix-v5)

bugfix-v5 は AI駆動のバグ修正ワークフロー自動化ツール:

- **9ステートのステートマシン**: INIT → INVESTIGATE → INVESTIGATE_REVIEW → DETAIL_DESIGN → DETAIL_DESIGN_REVIEW → IMPLEMENT → IMPLEMENT_REVIEW → PR_CREATE → COMPLETE
- **3つのAIエージェント**: Gemini(分析), Codex(レビュー), Claude(実装)
- **VERDICTプロトコル**: PASS / RETRY / BACK_DESIGN / ABORT
- **16回以上のE2Eテスト**で洗練されたアーキテクチャ

### 1.2 課題

bugfix-v5 は「バグ修正」に特化しているが、同様のワークフローは他の開発タスクにも適用可能:

- **設計機能**: DETAIL_DESIGN ↔ DETAIL_DESIGN_REVIEW のループのみ
- **実装機能**: IMPLEMENT ↔ IMPLEMENT_REVIEW のループのみ
- **将来**: リファクタリング、ドキュメント作成、コードレビューなど

---

## 2. 設計方針の検討

### 2.1 検討した選択肢

| 方針 | 概要 | メリット | デメリット |
|------|------|----------|------------|
| **1. サブワークフローモード追加** | ExecutionMode に DESIGN_LOOP / IMPLEMENT_LOOP 追加 | 既存コードへの影響最小 | 拡張性に限界 |
| **2. スタンドアロンエージェント** | design-agent / implement-agent を別途作成 | 責務分離が明確 | コード重複 |
| **3. 共通ライブラリ抽出** | core/ を抽出し、複数ワークフローで再利用 | 高い再利用性 | 大規模リファクタ |

### 2.2 決定

**方針3（共通ライブラリ抽出 + プラガブルワークフロー）を採用**

さらに以下を決定:
- bugfix-v5 は現状維持（壊さない）
- **v6 として新規リポジトリで開発**
- リポジトリ名: `dev-agent-orchestra`

### 2.3 決定理由

1. **リスク低減**: v5 は動作実績があり、破壊的変更を避ける
2. **設計の自由度**: 新規リポジトリで v5 の知見を活かした再設計が可能
3. **汎用性**: kamo2 以外のプロジェクトでも利用可能に
4. **依存の明確化**: kamo2 固有のコードと分離

---

## 3. アーキテクチャ設計

### 3.1 ディレクトリ構成

```
dev-agent-orchestra/
├── src/
│   ├── core/                       # 共通ライブラリ
│   │   ├── __init__.py
│   │   ├── tools/                  # AI CLI ラッパー
│   │   │   ├── __init__.py
│   │   │   ├── protocol.py         # AIToolProtocol
│   │   │   ├── gemini.py           # GeminiTool
│   │   │   ├── codex.py            # CodexTool
│   │   │   └── claude.py           # ClaudeTool
│   │   ├── verdict.py              # VERDICT パーサー
│   │   ├── state_machine.py        # 汎用ステートマシン基盤
│   │   ├── session.py              # SessionState
│   │   ├── config.py               # 設定読み込み
│   │   ├── context.py              # AgentContext
│   │   └── prompts.py              # load_prompt()
│   │
│   ├── workflows/
│   │   ├── __init__.py
│   │   ├── base.py                 # WorkflowBase 抽象クラス
│   │   │
│   │   ├── bugfix/                 # バグ修正ワークフロー
│   │   │   ├── __init__.py
│   │   │   ├── workflow.py         # BugfixWorkflow(WorkflowBase)
│   │   │   ├── states.py           # State enum (9ステート)
│   │   │   ├── handlers/
│   │   │   └── prompts/
│   │   │
│   │   ├── design/                 # 設計ワークフロー
│   │   │   ├── __init__.py
│   │   │   ├── workflow.py         # DesignWorkflow(WorkflowBase)
│   │   │   ├── states.py           # State enum (2ステート)
│   │   │   ├── handlers/
│   │   │   └── prompts/
│   │   │
│   │   └── implement/              # 実装ワークフロー
│   │       ├── __init__.py
│   │       ├── workflow.py         # ImplementWorkflow(WorkflowBase)
│   │       ├── states.py           # State enum (2ステート)
│   │       ├── handlers/
│   │       └── prompts/
│   │
│   ├── cli.py                      # 統一CLI
│   └── orchestrator.py             # 汎用オーケストレータ
│
├── tests/
├── docs/
├── pyproject.toml
├── config.toml
└── README.md
```

### 3.2 核となる抽象化

```python
# workflows/base.py
from abc import ABC, abstractmethod
from enum import Enum
from typing import Callable

StateHandler = Callable[["AgentContext", "SessionState"], Enum]

class WorkflowBase(ABC):
    """ワークフロー基底クラス"""

    @property
    @abstractmethod
    def name(self) -> str:
        """ワークフロー名 (例: 'bugfix', 'design', 'implement')"""
        ...

    @property
    @abstractmethod
    def states(self) -> type[Enum]:
        """ステート列挙型"""
        ...

    @property
    @abstractmethod
    def initial_state(self) -> Enum:
        """初期ステート"""
        ...

    @property
    @abstractmethod
    def terminal_states(self) -> set[Enum]:
        """終了ステート群"""
        ...

    @abstractmethod
    def get_handler(self, state: Enum) -> StateHandler:
        """ステートに対応するハンドラを取得"""
        ...

    @abstractmethod
    def get_next_state(self, current: Enum, verdict: "Verdict") -> Enum:
        """VERDICTに基づく次のステートを決定"""
        ...

    @abstractmethod
    def get_prompt_path(self, state: Enum) -> str:
        """ステートに対応するプロンプトファイルパス"""
        ...
```

### 3.3 CLI インターフェース

```bash
# バグ修正（既存互換）
dao bugfix --issue <url>

# 設計ループ
dao design --issue <url> --input requirements.md

# 実装ループ
dao implement --input design.md --workdir ./src

# ワークフロー一覧
dao --list-workflows
```

---

## 4. 開発環境

Git Worktree + Bare Repository パターンを採用。

詳細は [Git Worktree ガイド](../guides/git-worktree.md) を参照。

---

## 5. 開発計画

### Phase 1: 基盤構築
- [ ] GitHubリポジトリ作成
- [ ] プロジェクト構造作成
- [ ] core/ モジュール実装（v5からの移植）
- [ ] WorkflowBase 抽象クラス実装

### Phase 2: Design ワークフロー
- [ ] DesignWorkflow 実装
- [ ] 設計用プロンプト作成
- [ ] テスト作成
- [ ] CLI統合

### Phase 3: Implement ワークフロー
- [ ] ImplementWorkflow 実装
- [ ] 実装用プロンプト作成
- [ ] テスト作成

### Phase 4: Bugfix ワークフロー移植
- [ ] BugfixWorkflow 実装（v5互換）
- [ ] E2Eテスト移植

---

## 6. 参考資料

### 既存実装
- bugfix-v5: `.claude/agents/bugfix-v5/`
- ARCHITECTURE.md: `.claude/agents/bugfix-v5/docs/ARCHITECTURE.md`
