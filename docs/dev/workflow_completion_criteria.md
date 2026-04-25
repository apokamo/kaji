# Workflow Completion Criteria

各フェーズで何を確認し、どこで全体確定するかの対応表。

## フェーズ別の確認項目

| 項目 | review-ready | design | review-design | implement | review-code | i-dev-final-check | i-doc-final-check |
|------|-------------|--------|---------------|-----------|-------------|-------------------|-------------------|
| Issue 本文の記述品質 | ✅ | - | - | - | - | - | - |
| テスト分類・実行面の記載 | - | ✅ | ✅ | - | - | ✅ | - |
| docs 影響評価 | - | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 実装・差分反映 | - | - | - | ✅ | ✅ | ✅ | - |
| 最終品質ゲート（`make check`） | - | - | - | 参考実施可 | 参考確認可 | ✅ | - |
| docs-only 最終整合確認（`make verify-docs`） | - | - | - | - | - | - | ✅ |
| Issue 完了条件の段階確認 | - | ✅ | ✅ | ✅ | ✅ | ✅（集約） | ✅（集約） |
| Issue 本文更新 | - | - | - | - | - | ✅ | ✅ |
| PR へ進める最終判定 | - | - | - | - | - | ✅ | ✅ |

## 判定原則

- 前段で確認できる項目を final-check に先送りしない
- final-check は前段の証跡を集約し、未充足なら `RETRY` または `BACK` を返す
- `RETRY` は final-check 文脈で閉じる軽微修正に限定する（自己ループ）
- `BACK` は原因に応じて design / implement / docs の前段に戻す

## 各ステップの証跡責務

各ステップは、自分の段階で確認可能な Issue 完了条件を確認し、後段が追跡可能な形で証跡を残す。

### 証跡の定義

**証跡（evidence）** とは、あるステップが Issue の完了条件を確認した事実を示す記録である。以下のいずれかの形式で残す。

| 証跡の形式 | 説明 | 例 |
|-----------|------|-----|
| Issue コメント | ステップ完了時に投稿する構造化コメント | 設計レビュー結果、実装完了報告、コードレビュー結果 |
| コミット内容 | Git に記録された成果物 | 設計書、テストコード、docs 更新 |
| コマンド出力 | Issue コメントに含まれる実行結果 | pytest 出力、`make check` の結果 |

### ステップ別の確認責務と証跡

| ステップ | Issue 完了条件のうち確認する範囲 | 確認の根拠 | 証跡の残し方 |
|----------|-------------------------------|-----------|-------------|
| `issue-review-ready` | Issue 本文の記述品質（構造・具体性・根拠・検証可能性・整合性・スコープ推定） | チェック観点 7 項目の充足 | Issue コメント（レディネスレビュー結果 + PASS/RETRY/ABORT 判定） |
| `issue-design` | 設計書で対応可能な条件（テスト方針、docs 影響評価、技術制約） | 設計書の各セクションが条件に対応 | 設計書コミット + Issue コメント（設計完了報告） |
| `issue-review-design` | 設計書が完了条件を充足できる構造か | 設計書の S/M/L 網羅性、一次情報整合、影響評価 | Issue コメント（レビュー結果 + Approve/CR 判定） |
| `issue-implement` | 実装・テストで対応可能な条件（実装完了、テスト通過、品質ゲート、docs 更新） | pytest 出力、`make check` 出力 | Issue コメント（実装完了報告 + テスト結果 + 品質チェック結果） |
| `issue-review-code` | 実装が設計と整合し、テスト・docs が揃っているか | 独立テスト実行、差分レビュー | Issue コメント（レビュー結果 + Approve/CR 判定） |
| `i-dev-final-check` | **全条件**（前段証跡の集約 + 未確認条件の最終確認） | 前段コメントの走査 + 最終品質ゲート実行 | Issue コメント（最終チェック結果）+ **Issue 本文更新** |
| `i-doc-final-check` | **docs-only 全条件**（docs 整合 + Issue 状態） | docs 差分 + リンクチェック | Issue コメント + **Issue 本文更新** |

### type 別に追加で確認する項目

Issue の `type:` ラベルに応じて、前段スキルが確認する完了条件の追加項目が変わる。final-check はこれらの type 別項目も集約して確認する。

| ステップ | feat 追加確認 | bug 追加確認 | refactor 追加確認 |
|----------|---------------|--------------|-------------------|
| `issue-design` | 使用例・エラー挙動が設計書に含まれている | OB / EB / 再現手順 / 根本原因が設計書に含まれている | ベースライン計測コマンド・改善指標が設計書に含まれている |
| `issue-implement` | 設計書のユースケースが受け入れテストでカバーされている | 再現テストが Red → Green に遷移したログが Issue コメントに含まれている | ベースライン値 / 改修後値が Issue コメントに含まれ、改善指標を達成 |
| `issue-review-code` | IF 契約（型・命名・戻り値・エラー）が設計書と一致 | 同根欠陥の波及修正が行われている / 再現テストの Red→Green 証跡が確認できる | 既存テスト全件 PASS（振る舞い非変更） / safety net テストの追加 |
| `i-dev-final-check` | 上記 3 段の type 別証跡を横断集約 | 同上 | 同上 |

**canonical 外 type（`type:test` / `type:chore` / `type:perf` / `type:security`）**: 上記 feat 列の追加確認を適用する（フォールバック規則）。

**type:docs の扱い**: dev workflow ではなく docs-only workflow（`/i-doc-*`）が担当する。dev workflow の各スキルでは対象外として処理を停止し、`/i-doc-update` に誘導する。

**fix/verify 系スキル（`issue-fix-*` / `issue-verify-*` / `pr-fix` / `pr-verify` / `i-doc-fix` / `i-doc-verify`）では type 別追加確認を行わない**。レビューサイクルの収束保証のため、新規指摘を行わないという原則に従う。

### docs-only / metadata-only / packaging-only の追加確認

`docs/dev/testing-convention.md` の 4 条件に基づき、恒久テストの追加が不要と判断できるケース（docs-only / metadata-only / packaging-only）では、final-check は以下を追加で確認する:

- 4 条件の充足が設計書または Issue コメントで明示されている
- 代替検証（`make verify-docs` / `make verify-packaging` / grep 検証等）の実行ログが残っている

### 前段証跡の確認方法

`i-dev-final-check` は以下の手順で前段の証跡を集約する。

1. `gh issue view [number] --comments` で Issue コメントを取得
2. 各ステップの完了報告コメントが存在するか確認
3. 各コメントの判定結果（Approve / CR）を確認
4. Issue 本文の完了条件リストと照合し、充足 / 未充足を判定

## Issue 本文更新プロトコル

### コメントと本文の役割分担

| 役割 | 格納先 | 説明 |
|------|--------|------|
| 各ステップの詳細報告 | Issue **コメント** | テスト結果、レビュー指摘、品質チェック出力など。ステップごとに投稿 |
| 完了条件の充足状態 | Issue **本文** | チェックボックスの更新で「どの条件が確認済みか」を表現 |
| 設計書アーカイブ | Issue **本文** | NOTE ブロック直下に `<details>` で添付（`/i-dev-final-check` Step 7.5） |

### 本文更新のタイミングと実行者

| タイミング | 実行者 | 更新内容 |
|-----------|--------|---------|
| `/issue-start` 時 | `issue-start` | 本文先頭の NOTE ブロックに Worktree / Branch を追記 |
| final-check PASS 時 | `i-dev-final-check` / `i-doc-final-check` | 完了条件のチェックボックスを `[x]` に更新、設計書を NOTE 直下に添付（dev のみ） |
| final-check BACK 時 | `i-dev-final-check` / `i-doc-final-check` | 本文更新なし（コメントで未充足条件と戻し先を明示） |
| final-check RETRY 時 | `i-dev-final-check` / `i-doc-final-check` | 本文更新なし（軽微修正後に再実行するため） |
| PR 作成時 | `i-pr` | NOTE ブロックに `PR: #NNN` を追記 |

### チェックボックス更新の方法

Issue 本文に `## 完了条件` セクションがあり、チェックボックス形式（`- [ ]`）で条件が列挙されている場合:

```bash
# 現在の本文を取得
gh issue view [number] --json body -q '.body' > /tmp/issue-body.md

# チェックボックスを更新（手動または sed）
# - [ ] 条件A → - [x] 条件A

# 更新を反映
gh issue edit [number] --body-file /tmp/issue-body.md
```

### 設計書の NOTE 直下添付

`/i-dev-final-check` の PASS 時に、設計書（`draft/design/issue-XXX-*.md`）を Issue 本文の NOTE ブロック直下に `<details>` タグで添付する。worktree 削除後も Issue から設計書を辿れるようにするため。

```markdown
> [!NOTE]
> **Worktree**: `../kaji-feat-123`
> **Branch**: `feat/123`
> **PR**: #456

<details>
<summary>設計書: issue-123-xxx.md</summary>

(設計書本文)

</details>

(元の Issue 本文)
```

### 本文にチェックボックスがない場合

完了条件が自由記述の場合は、final-check コメントに充足状態を一覧し、本文末尾に注記を追加する。

```markdown
> **Final Check**: YYYY-MM-DD に全完了条件の充足を確認。詳細は最終チェックコメント参照。
```

### BACK 時の本文更新

未充足条件がある場合、Issue 本文のチェックボックスは `[ ]` のまま残し、コメントで未充足理由と戻し先を明示する。

```markdown
## final-check 結果: BACK

### 未充足条件

- `- [ ] 条件X` — 理由: ○○が不足。戻し先: `issue-implement`
- `- [ ] 条件Y` — 理由: △△の整合が取れていない。戻し先: `issue-design`
```
