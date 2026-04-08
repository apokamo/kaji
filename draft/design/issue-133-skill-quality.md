# [設計] スキルファイルの品質改善

Issue: #133

## 概要

別プロジェクトで実施されたスキル改善を kaji に移植する。スキルファイル（Markdown）の新規作成・既存改善と、スキルが参照するドキュメントの新規作成・更新を一体で行う。

## 背景・目的

現行スキルには以下の課題がある:

1. **品質ゲートの不在**: PR 前に全フェーズのエビデンスを集約・検証するステップがない
2. **ブランチ削除の安全性**: `issue-close` が merge 確認なしにブランチ削除を実行
3. **責務の曖昧さ**: `issue-pr` の責務範囲（何をしないか）が不明確。設計書アーカイブが `issue-close` に集中
4. **ラベル体系の非標準化**: `type:` プレフィックスなし、`security` タイプ未対応
5. **参照ドキュメントの欠如**: ワークフロー概要・完了条件・ドキュメント更新基準等のガイドが存在しない

## インターフェース

### 入力

なし（スキルファイルとドキュメントの変更のみ）

### 出力

| カテゴリ | 成果物 |
|----------|--------|
| 新規スキル | `i-dev-final-check`, `i-doc-final-check`, `i-pr`, `_shared/promote-design.md` |
| 改善スキル | `issue-implement`, `issue-close`, `issue-create`, `issue-pr`, `issue-design`, `i-doc-review`, `i-doc-verify` |
| 新規ドキュメント | `docs/dev/` 4件、`docs/concepts/` 2件、`docs/rfc/` 1件、`docs/reference/` 1件、`docs/README.md` |
| 更新ドキュメント | `docs/dev/testing-convention.md`, `docs/dev/workflow_guide.md`, `CLAUDE.md` |

### 使用例

```bash
# 改善後のワークフロー（dev）
/issue-create → /issue-start → /issue-design → /issue-review-design
→ /issue-implement → /issue-review-code → /issue-doc-check
→ /i-dev-final-check → /i-pr → /issue-close

# 改善後のワークフロー（docs-only）
... → /i-doc-review → /i-doc-fix → /i-doc-verify
→ /i-doc-final-check → /i-pr → /issue-close
```

## 制約・前提条件

- kaji_harness/ 配下のコード変更はなし（スコープ外）
- テスト追加なし（スコープ外）
- 既存 Issue の再ラベルは行わない（新規 Issue のみ対象）
- 移植元スキルは別プロジェクトにあり直接参照不可 → Issue #133 本文の改善一覧・責務境界テーブル・コミット戦略を「何を作るか」の正本とする。一方、kaji 固有の適合判断（既存スキルとの差分、kaji に不要な機能の除外、既存 workflow docs との整合）は現行 kaji リポジトリの一次情報で確定する
- Scope 概念（backend/frontend/fullstack）は kaji では不要（Python 単体）
- secrets シンボリックリンクは kaji では不要

## 方針

### 1. 新規スキル作成

#### `i-dev-final-check`
- PR 前の包括的品質ゲートとして新規作成
- 全ステップのエビデンス集約、品質チェック（`make check`）実行
- 設計書の Issue 本文添付を `issue-close` から移譲（`## 設計書` セクション存在チェックで冪等性確保）
- 添付失敗時は Issue コメントに投稿 + 本文にリンク

#### `i-doc-final-check`
- docs-only ワークフローの PR 前最終チェック
- リンク整合性（`make verify-docs`）・完了条件の検証
- 設計書アーカイブは行わない（docs-only では設計書が存在しないため）

#### `i-pr`
- 責務を明確化した PR 作成スキル（何をしないかを明記）
- PR テンプレートに Documentation セクションを追加
- `issue-pr` はこの `i-pr` への委譲ラッパーに変更

#### `_shared/promote-design.md`
- draft 設計書から恒久ドキュメント（`docs/adr/` 等）への昇格手順を共通化

### 2. 既存スキル改善

#### `issue-implement`（CRITICAL）
- テスト判断の禁止事項を明記（AI のテスト省略傾向への対策を強化）
- lint と test のステップを分離（失敗原因の特定を容易に）
- 注: ベースラインチェック（Step 2.5）は既に実装済み

#### `issue-close`
- ブランチ削除を安全化: `git merge-base --is-ancestor` でマージ確認後に削除
- local/remote 独立処理、stale ref cleanup
- 設計書アーカイブ責務を `i-dev-final-check` に移譲（Step 3 の設計書保存ロジックを削除）

#### `issue-create`
- `security` タイプを追加
- ラベルプレフィックス標準化: `enhancement` → `type:feature`, `bug` → `type:bug` 等
- 既存ラベルとの共存（既存ラベルは削除しない）

#### `issue-design`
- 完了条件の段階確認（Step 2.5）を追加: テンプレート各セクションの記載有無を段階的に検証
- 冒頭説明の更新: 「Issue Close 時に Issue 本文へアーカイブ」→「`i-dev-final-check` 時に Issue 本文へアーカイブ」に修正（責務移譲の反映）

#### `issue-pr`
- `i-pr` への委譲ラッパー化（既存インターフェースは維持）

#### `i-doc-review` / `i-doc-verify`
- ワークフロー位置の更新: `i-doc-final-check` → `i-pr` ゲートの追加を反映

### 3. 設計書アーカイブ責務の移譲

| 項目 | `i-dev-final-check` | `issue-close` |
|------|---------------------|---------------|
| 設計書の Issue 本文添付 | 実行する | 実行しない |
| 二重添付の防止 | `## 設計書` 存在チェック（冪等） | — |
| 添付失敗時フォールバック | コメントに投稿 + 本文リンク | — |
| PR マージ・worktree 削除 | 実行しない | 実行する |

### 4. ドキュメント新規作成

| ドキュメント | 概要 |
|-------------|------|
| `docs/dev/workflow_overview.md` | ワークフロー選択エントリポイント（dev / docs-only 分岐） |
| `docs/dev/workflow_completion_criteria.md` | フェーズ別完了条件チェックリスト |
| `docs/dev/documentation_update_criteria.md` | ドキュメント更新要否の判断フレームワーク |
| `docs/dev/shared_skill_rules.md` | スキル横断の責務境界定義 |
| `docs/concepts/ai-driven-strategy.md` | AI 駆動開発戦略の明文化 |
| `docs/concepts/ai-docs-management.md` | AI ドキュメント管理方針 |
| `docs/rfc/github-labels-standardization.md` | GitHub ラベル標準化方針 |
| `docs/reference/testing-size-guide.md` | テストサイズ境界ケース判断ガイド |
| `docs/README.md` | Diataxis フレームワークに基づくドキュメント索引 |

### 5. 既存ドキュメント更新

| ドキュメント | 更新内容 |
|-------------|----------|
| `docs/dev/workflow_feature_development.md` | `/issue-close` の責務記述を「PRマージ + worktree削除」に縮小（設計書アーカイブ責務を削除）。フロー図に `/i-dev-final-check` を追加。設計書保存場所の `Close時` → `i-dev-final-check時` に更新 |
| `docs/dev/workflow_docs_maintenance.md` | フロー概要に `/i-doc-final-check` ステップを追加（`i-doc-verify` → `i-doc-final-check` → `i-pr`） |
| `docs/dev/testing-convention.md` | テスト実行マトリクス（いつ何を実行するか）セクション追加 |
| `docs/dev/workflow_guide.md` | `workflow_overview.md` との役割分担整理 |
| `CLAUDE.md` | ドキュメント参照テーブルに新規ドキュメント追加 |

### 6. コミット戦略

Issue 本文のコミット戦略に従い、論理単位で分割:

1. `issue-implement` テスト判断の禁止事項明記 + lint/test ステップ分離（ベースラインチェックは既存のため対象外）
2. `issue-close` ブランチ削除安全化 + 設計書アーカイブ責務削除
3. `i-dev-final-check` / `i-doc-final-check` 新規追加
4. `_shared/promote-design.md` 新規追加
5. `i-pr` 新規追加 + `issue-pr` ラッパー化
6. その他スキル改善（issue-create, issue-design, i-doc-review, i-doc-verify）
7. 参照ドキュメント新規作成・更新（`docs/dev/workflow_feature_development.md`, `docs/dev/workflow_docs_maintenance.md` の正本更新を含む）
8. コンセプト・運用ドキュメント新規作成

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ
- **skills + docs の運用変更**（kaji_harness のコード変更なし）

### 変更固有検証

- `make verify-docs` — 全ドキュメントのリンク整合性チェック
- 全スキルのドキュメント参照パスが実在ファイルを指していることを手動確認
- workflow / skill / `CLAUDE.md` 間のワークフロー位置記述の整合性確認
- 全スキルの「ワークフロー内の位置」記述が統一されていることを確認

### 恒久テストを追加しない理由

1. **独自ロジックの追加・変更なし**: kaji_harness/ 配下のコード変更がなく、Markdown ファイルの新規作成・更新のみ
2. **既存ゲートで捕捉可能**: `make verify-docs` でリンク切れ・参照不整合を検出可能
3. **回帰検出情報の増加なし**: スキルファイルは Claude Code が実行時に解釈するものであり、pytest による自動テストの対象外
4. **理由の明示**: スキル（Markdown）はプログラムコードではなく AI エージェントへの指示書であり、実行時の振る舞いを変えるコード変更に該当しない

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | あり | workflow_overview.md 等の新規作成、workflow_feature_development.md・workflow_docs_maintenance.md の正本更新、既存ドキュメント更新 |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | あり | ドキュメント参照テーブルに新規ドキュメント追加 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #133 本文 | `gh issue view 133` | 改善一覧・コミット戦略・完了条件の正本。移植元の仕様記述を含む |
| 現行 issue-implement | `.claude/skills/issue-implement/SKILL.md` | ベースラインチェック（Step 2.5）が既に実装済みであることを確認。追加改善箇所: テスト判断の禁止事項明記、lint/test ステップ分離 |
| 現行 issue-close | `.claude/skills/issue-close/SKILL.md` | 設計書アーカイブ（Step 3）と `--delete-branch` によるブランチ削除が現行。安全化対象を特定 |
| 現行 issue-create | `.claude/skills/issue-create/SKILL.md` | type→label マッピングが feat/fix/refactor/docs/test/chore/perf の 7 種。security 未対応、プレフィックスなし |
| 現行 issue-design | `.claude/skills/issue-design/SKILL.md` | テスト戦略セクションと影響ドキュメントテーブルは存在するが、完了条件の段階確認ステップは未実装 |
| 現行 issue-pr | `.claude/skills/issue-pr/SKILL.md` | git absorb → push → PR 作成の責務。i-pr への委譲前の現状 |
| 現行 i-doc-review / i-doc-verify | `.claude/skills/i-doc-review/SKILL.md`, `.claude/skills/i-doc-verify/SKILL.md` | ワークフロー位置に `i-doc-final-check` が未記載 |
| 開発ワークフロー | `docs/dev/workflow_feature_development.md` | スキルのワークフロー位置記述の正本。`/issue-close` の責務・設計書保存場所の記述を更新対象 |
| docs メンテナンスワークフロー | `docs/dev/workflow_docs_maintenance.md` | docs-only workflow の正本。フロー概要に `i-doc-final-check` を追加対象 |
| テスト規約 | `docs/dev/testing-convention.md` | テスト実行マトリクス追加先。恒久テスト不要の 4 条件を参照 |
| ワークフローガイド | `docs/dev/workflow_guide.md` | `workflow_overview.md` との役割分担整理対象 |
