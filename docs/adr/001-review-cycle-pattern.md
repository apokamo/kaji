# ADR-001: Review-Fix-Verify サイクルパターン

## Status

Accepted

## Context

dev-agent-orchestraのワークフローにおいて、レビューサイクルが収束しない問題が発生していた。

### 問題

`review → fix → review` のループでは:

1. 毎回フルレビューを実行するため、新規指摘が発生し続ける
2. 些末な指摘の繰り返しでサイクルが収束しない
3. ワークフローが破綻するリスクがある

### 背景

Issue #6でClaude Codeスキルを導入した際、jquantsワークフローの `review → fix → verify` パターンを採用し、効果が確認された。

## Decision

レビューサイクルに `verify` ステートを導入する。

### review vs verify の違い

| 観点 | review | verify |
|------|--------|--------|
| 目的 | フルレビュー | 修正確認のみ |
| 新規指摘 | あり | なし |
| 収束保証 | なし | あり |

### ステート遷移

```
REVIEW ──(PASS)──> COMPLETE
   │
   └──(RETRY)──> FIX ──(always)──> VERIFY
                                      │
                        ┌─────────────┴─────────────┐
                        │                           │
                      PASS                        RETRY
                        │                           │
                        v                           v
                    COMPLETE                       FIX
```

### 収束保証のメカニズム

1. `verify` は「指摘事項が修正されたか」のみを確認
2. 新規指摘を追加しないことでループを収束させる
3. このルールはプロンプト設計で担保（AIへの明示的指示）

### VERDICTプロトコルとの関係

VERDICTプロトコル自体は変更しない（後方互換性維持）:

```
PASS / RETRY / BACK_DESIGN / ABORT
```

`review` と `verify` の区別はステート遷移ロジックで行う。同じ `RETRY` でも、どのステートから発行されたかで遷移先が変わる。

## Consequences

### Positive

- レビューサイクルの収束が保証される
- ワークフローの予測可能性が向上
- 既存のVERDICTプロトコルとの互換性維持

### Negative

- ステート数の増加（各ワークフローに FIX / VERIFY を追加）
- プロンプト設計への依存（コードでの強制は困難）

### Risks

- AIが `verify` で新規指摘を追加してしまう可能性
  - 対策: プロンプトで明示的に禁止、ループカウンタによる強制終了

## References

- Issue #6: Issue駆動開発ワークフローの導入
- docs/DEVELOPMENT_WORKFLOW.md
