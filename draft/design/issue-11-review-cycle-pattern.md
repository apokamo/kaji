# [設計] レビューサイクル設計パターンのdao本体への反映

Issue: #11

## 概要

Issue #6で導入したClaude Codeスキルの `review → fix → verify` パターンを、dev-agent-orchestraのPythonコード設計（ステートマシン）に反映する。

## 背景・目的

### 現状の課題

1. **収束しないレビューサイクル**: 現在の `review → fix → review` ループでは、毎回フルレビューを実行するため新規指摘が発生し続け、サイクルが収束しない
2. **VERDICTプロトコルの限界**: PASS/RETRY/BACK_DESIGN/ABORTの4値では、「修正確認のみ」を表現できない
3. **設計書ライフサイクルの未定義**: `draft/design/` パターンがワークフローに組み込まれていない

### 解決策

jquantsワークフローおよびIssue #6の実践から得られた `verify` ステートを導入し、収束を保証する。

## インターフェース

### 入力

#### ステートマシン定義（states.py）

```python
class DesignState(Enum):
    DESIGN = auto()
    DESIGN_REVIEW = auto()
    DESIGN_FIX = auto()       # 新規追加
    DESIGN_VERIFY = auto()    # 新規追加
    COMPLETE = auto()

class ImplementState(Enum):
    IMPLEMENT = auto()
    IMPLEMENT_REVIEW = auto()
    IMPLEMENT_FIX = auto()    # 新規追加
    IMPLEMENT_VERIFY = auto() # 新規追加
    COMPLETE = auto()
```

#### VERDICTプロトコル拡張

```python
class Verdict(Enum):
    PASS = "PASS"
    RETRY = "RETRY"
    BACK_DESIGN = "BACK_DESIGN"
    ABORT = "ABORT"
```

VERDICTプロトコル自体は変更しない。ステート遷移ロジックで `review` と `verify` を区別する。

### 出力

#### ステート遷移図（Design Workflow）

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

#### ステート遷移図（Implement Workflow）

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

### 使用例

```python
# Design Workflow の状態遷移
workflow = DesignWorkflow()

# フルレビュー後の遷移
next_state = workflow.get_next_state(
    DesignState.DESIGN_REVIEW,
    Verdict.RETRY
)
assert next_state == DesignState.DESIGN_FIX

# verify後の遷移（修正OK）
next_state = workflow.get_next_state(
    DesignState.DESIGN_VERIFY,
    Verdict.PASS
)
assert next_state == DesignState.COMPLETE

# verify後の遷移（修正不十分）
next_state = workflow.get_next_state(
    DesignState.DESIGN_VERIFY,
    Verdict.RETRY
)
assert next_state == DesignState.DESIGN_FIX
```

## 制約・前提条件

### 技術的制約

- 既存の `WorkflowBase` 抽象クラスを破壊しない
- VERDICTプロトコルの構文は変更しない（後方互換性）
- ステート追加は既存テストをパスする形で行う

### ビジネス制約

- verifyステートは「新規指摘を追加しない」という運用ルールで収束を保証
- このルールはプロンプト設計で担保する（コードでの強制は困難）

### 依存関係

- `src/core/verdict.py`: 変更なし
- `src/workflows/base.py`: 変更なし
- `src/workflows/design/states.py`: ステート追加
- `src/workflows/implement/`: 新規実装（現在TODO）

## 方針

### Phase 1: ADR作成

`docs/adr/001-review-cycle-pattern.md` を作成し、以下を文書化:

1. `review` vs `verify` の違い
   - review: フルレビュー、新規指摘あり
   - verify: 修正確認のみ、新規指摘なし（収束保証）
2. 収束保証のメカニズム
3. VERDICTプロトコルとの関係

### Phase 2: architecture.md更新

`docs/architecture.md` のワークフロー図を更新:

- Design Workflowに `DESIGN_FIX` / `DESIGN_VERIFY` を追加
- Implement Workflowに `IMPLEMENT_FIX` / `IMPLEMENT_VERIFY` を追加

### Phase 3: ステートマシン実装

1. `src/workflows/design/states.py` にステート追加
2. `src/workflows/design/workflow.py` に遷移ロジック追加
3. 対応するハンドラーの実装（handlers/）

### Phase 4: プロンプト設計

`prompts/` ディレクトリに以下を作成:

- `design_fix.md`: 指摘事項に対する修正プロンプト
- `design_verify.md`: 修正確認専用プロンプト（新規指摘禁止を明示）
- `implement_fix.md`: 同様
- `implement_verify.md`: 同様

### Phase 5: draft/design/ パターンの組み込み（任意）

設計書ライフサイクルをドキュメント化:

```
draft/design/ → PR本文転記 → draft/削除 → (必要に応じて) docs/design/
```

## 検証観点

### 正常系

- フルレビュー（DESIGN_REVIEW）でRETRY判定 → DESIGN_FIXへ遷移
- DESIGN_FIXからDESIGN_VERIFYへ自動遷移
- DESIGN_VERIFYでPASS判定 → COMPLETEへ遷移
- DESIGN_VERIFYでRETRY判定 → DESIGN_FIXへ戻る（verify-fixループ）
- Implement Workflowでも同様の遷移が機能する

### 異常系

- DESIGN_REVIEWでABORT判定 → ワークフロー中断
- IMPLEMENT_REVIEWでBACK_DESIGN判定 → Design Workflowへ戻る
- 不正なVerdict値 → `InvalidVerdictValueError` 送出

### 収束性

- verify → fix → verify ループが3回以内に収束するかのシミュレーション
- 無限ループ防止のガード（ループカウンタによる強制ABORT）

### 後方互換性

- 既存の `parse_verdict()` が新ステート導入後も正常動作
- 既存のDesignWorkflowテストがパス

## 参考

- [Issue #6: Issue駆動開発ワークフローの導入](https://github.com/apokamo/dev-agent-orchestra/issues/6)
- [docs/DEVELOPMENT_WORKFLOW.md](../docs/DEVELOPMENT_WORKFLOW.md)
- [docs/architecture.md](../docs/architecture.md)
