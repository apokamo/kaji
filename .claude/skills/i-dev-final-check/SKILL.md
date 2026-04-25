---
description: dev workflow 向けの最終チェック。PR 前に品質ゲート、docs 整合、設計書昇格、Issue 更新をまとめて確認する。
name: i-dev-final-check
---

# I Dev Final Check

dev workflow の PR 前最終ゲート。
前段で作られた証跡を集約し、必要なら docs 更新や設計書昇格を行ったうえで、PR に進めるか判定する。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-review-code` または `/issue-verify-code` で Approve 後 | ✅ 必須 |
| dev workflow の PR 作成前 | ✅ 必須 |

**ワークフロー内の位置**: implement → review-code → **i-dev-final-check** → i-pr → close

## 前提知識の読み込み

1. [docs/dev/development_workflow.md](../../../docs/dev/development_workflow.md)
2. [docs/dev/workflow_completion_criteria.md](../../../docs/dev/workflow_completion_criteria.md)
3. [docs/dev/documentation_update_criteria.md](../../../docs/dev/documentation_update_criteria.md)
4. [docs/dev/shared_skill_rules.md](../../../docs/dev/shared_skill_rules.md)
5. `docs/README.md`
6. [_shared/promote-design.md](../_shared/promote-design.md)

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実施内容

1. worktree と branch を解決する
2. 前段の証跡を集約し、Issue 完了条件との照合を行う
3. 設計書の「影響ドキュメント」と実差分を確認する
4. 品質ゲートを実行する（後述 Step 4 詳細）
5. docs 更新の最終確認を行い、必要なら修正する
6. 設計書昇格判定 → 必要なら昇格、または既存 docs 更新の有無を確認する
7. Issue 本文の完了条件を照合し、充足状態を更新する
7.5. 設計書を Issue 本文の NOTE ブロック直下に添付する
8. Issue に最終チェック結果をコメントする

## Step 2 詳細: 前段証跡の集約と完了条件照合

### 2-1. 前段コメントの走査

```bash
gh issue view [issue-number] --comments
```

以下の完了報告コメントが存在するか確認する:

| ステップ | 期待するコメント | 必須の内容 |
|----------|----------------|-----------|
| `issue-design` | 「設計書作成完了」 | 設計書パス、テスト戦略、影響ドキュメント |
| `issue-review-design` | 「設計レビュー結果」 | Approve / Changes Requested 判定 |
| `issue-fix-design` → `issue-verify-design` | （経由した場合のみ）「修正確認結果」 | Approve 判定 |
| `issue-implement` | 「実装完了報告」 | pytest 出力（S/M/L 結果）、品質チェック結果 |
| `issue-review-code` | 「コードレビュー結果」 | Approve / Changes Requested 判定、独立テスト実行結果 |
| `issue-fix-code` → `issue-verify-code` | （経由した場合のみ）「修正確認結果」 | Approve 判定 |

> **fix/verify サイクルの扱い**: 実 workflow では `issue-review-*` が Changes Requested を返した場合、
> `issue-fix-*` → `issue-verify-*` を経由して再度 Approve を得てから final-check に到達する。
> コメント履歴に過去の Changes Requested が残るのは正常な状態であり、**最新の判定結果**（verify の Approve）を
> 権威ある判定として採用する。過去の Changes Requested は「解決済みの指摘」として無視してよい。

### 2-2. 完了条件との照合

Issue 本文の `## 完了条件` セクション（チェックボックス形式）を取得し、各条件について:

1. **どの前段で確認されたか** を特定する
2. **確認の根拠** を前段コメントから抽出する（最新のコメントを優先）
3. **未確認の条件** があれば、この final-check で確認するか、前段への差し戻しが必要かを判断する

### 2-3. 前段証跡が不足している場合

- 前段コメントが欠落 → `BACK`（該当ステップへ戻す）
- 最新の判定結果が Changes Requested のまま（fix/verify を経由していない）→ `BACK`（fix/verify が未完了）
- 最新の判定結果は Approve だが完了条件に未充足あり → この final-check で対応可能なら `RETRY`、不可能なら `BACK`

## Step 4 詳細: 品質ゲートの実行

kaji は Python 単一スタックのため、以下の 1 本に統一する。

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && make check
```

`make check` は ruff / format / mypy / pytest を一括で実行する（CLAUDE.md「Pre-Commit (REQUIRED)」と同一）。

特定マーカーや変更タイプ固有の検証が必要な場合は、設計書「テスト戦略」に従い追加実行する:

| 変更タイプ | 追加検証 |
|-----------|----------|
| docs-only | `make verify-docs` |
| metadata-only / packaging-only | `make verify-packaging` |
| 通常 | 追加なし（`make check` で十分） |

> **baseline failure の扱い**: `pytest` 部分は baseline failure を考慮し、
> 比較キー `(nodeid, kind, error_type)` が baseline と一致する失敗は除外、
> 不一致の新規 FAILED/ERROR が 1 件でもあれば NG として扱う。

## Step 6 詳細: 設計書昇格判定

[_shared/promote-design.md](../_shared/promote-design.md) の手順に従い、
`draft/design/issue-[number]-*.md` を恒久ドキュメントへ昇格するか、既存 docs に統合するかを判定する。

判定軸:

- 新規機能・新規 ADR 相当の決定 → 恒久 docs（`docs/adr/` ほか）へ昇格
- 既存 docs の更新で吸収可能 → 既存 docs を更新（昇格しない）
- 設計の決定が draft 段階のまま留めるべき軽微な変更 → 昇格不要

## Step 7 詳細: Issue 本文の完了条件更新

### PASS の場合

Issue 本文のチェックボックスを `[x]` に更新する。

```bash
# 本文を取得
gh issue view [issue-number] --json body -q '.body' > /tmp/issue-body.md

# チェックボックスを更新（確認済み条件を [x] に変更）
# 例: sed -i 's/- \[ \] 条件A/- [x] 条件A/' /tmp/issue-body.md

# 更新を反映
gh issue edit [issue-number] --body-file /tmp/issue-body.md
```

### BACK の場合

チェックボックスは `[ ]` のまま残す。コメントで未充足条件と戻し先を明示する。

### RETRY の場合

本文更新は行わない（軽微修正後に再実行するため）。

## Step 7.5 詳細: 設計書の Issue 本文添付

Step 7（完了条件更新）の後、Step 8（最終チェックコメント）の前に、設計書を Issue 本文の NOTE ブロック直下に添付する。

### 7.5-1. 冪等性チェック

```bash
gh issue view [issue-number] --json body -q '.body' | grep -q '^## 設計書'
```

既に `## 設計書` セクションが存在する場合はスキップする（位置の移動はしない）。

### 7.5-2. 添付対象の決定

| 条件 | 添付対象 |
|------|----------|
| Step 6 で恒久 docs へ昇格を実施した | 昇格後の確定版（`docs/...` 配下） |
| 昇格対象外 | `draft/design/` 版 |

### 7.5-3. 添付位置の判定（NOTE 直下挿入ルール）

Issue 本文を行単位で走査し、以下のルールで挿入位置を決定する:

| ケース | 挿入位置 |
|--------|----------|
| NOTE ブロックが1つ存在する（標準） | NOTE ブロック終端の次の空行の後 |
| NOTE ブロックが複数存在する | **最初の** NOTE ブロック終端の次の空行の後 |
| NOTE ブロックが存在しない（古い Issue） | 本文先頭 |
| 既に `## 設計書` が別位置に存在する | **スキップ**（7.5-1 で検出済み） |

> NOTE ブロックの終端判定: `> [!NOTE]` から始まり、`> ` プレフィックスの連続行が途切れた最初の空行。

### 7.5-4. 添付フォーマット

**昇格済みの場合:**
```markdown
## 設計書

恒久ドキュメントとして昇格済み: [`docs/...`](https://github.com/apokamo/kaji/tree/main/docs/...)
```

**未昇格の場合:**
```markdown
## 設計書

<details>
<summary>クリックして展開</summary>

(設計書全文)

</details>
```

### 7.5-5. Issue 本文への挿入

```bash
# 本文を取得
BODY=$(gh issue view [issue-number] --json body -q '.body')

# NOTE ブロック終端位置を検出し、その次の空行の後に挿入
# new_body = body[:insert_at] + 設計書セクション + body[insert_at:]
gh issue edit [issue-number] --body-file /tmp/issue-body-updated.md
```

### 7.5-6. フォールバック

本文サイズ上限超過等で `gh issue edit` に失敗した場合:

1. Issue **コメント**に設計書全文を投稿する
2. 本文には `## 設計書` セクションとコメントへのリンクのみを追記する
3. フォールバック発生時も **PASS 扱い**（設計書自体は参照可能であり、添付位置の問題に過ぎないため）

## Step 8 詳細: 最終チェックコメントのテンプレート

```bash
gh issue comment [issue-number] --body-file - <<'EOF'
## 最終チェック結果

### 前段証跡の確認

| ステップ | コメント有無 | 最新判定 |
|----------|------------|---------|
| issue-design | ✅ | 設計書作成済み |
| issue-review-design | ✅ | Approve |
| (fix-design → verify-design) | (経由した場合) | (Approve) |
| issue-implement | ✅ | テスト全件 PASSED（または baseline 一致） |
| issue-review-code | ✅ | Approve |
| (fix-code → verify-code) | (経由した場合) | (Approve) |

### 完了条件の充足状態

| 条件 | 充足 | 確認元 |
|------|------|--------|
| (条件1) | ✅ / ❌ | (どのステップ/コメントで確認) |
| (条件2) | ✅ / ❌ | (どのステップ/コメントで確認) |

### 品質ゲート

| ゲート | 結果 | 備考 |
|--------|------|------|
| `make check` | PASS / FAIL | ruff / format / mypy / pytest 一括 |
| 変更タイプ固有検証 | PASS / FAIL / N/A | 例: `make verify-docs` / `make verify-packaging` |

### docs 整合

- 設計書昇格: 実施 (`docs/...`) / 不要
- docs 更新: 実施 / 不要

### Issue 本文更新

- チェックボックス更新: 実施 / 不要
- 設計書添付: 実施 / スキップ（既存） / フォールバック（コメント投稿）

### 判定

PASS / RETRY / BACK
EOF
```

## Verdict 出力

```text
---VERDICT---
status: PASS
reason: |
  dev workflow の最終チェックを完了し、PR に進める状態を確認した
evidence: |
  前段証跡を集約し、全完了条件の充足を確認した。Issue 本文のチェックボックスを更新済み
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 全完了条件が充足し、Issue 本文更新済み |
| RETRY | final-check 文脈で閉じる軽微修正が必要 |
| BACK | 前段ステップへ戻す必要がある（未充足条件と戻し先を verdict に明示） |
| ABORT | 重大な前提不整合 |
