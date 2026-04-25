---
description: 設計修正が適切に行われたかを確認する。新規指摘は行わない（レビュー収束のため）。
name: issue-verify-design
---

# Issue Verify Design

> **重要**: このスキルは設計/修正を行ったセッションとは **別のセッション** で実行することを推奨します。
> 同一セッションで実行すると、設計時のバイアスが確認判断に影響する可能性があります。

設計修正後の確認を行います。

**重要**: このコマンドは「指摘事項が適切に修正されたか」のみを確認します。
**新規の指摘は行いません**。これはレビューサイクルの収束を保証するためです。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-fix-design` 後の修正確認 | ✅ 必須 |
| 新規レビューが必要な場合 | ❌ `/issue-review-design` を使用 |

**ワークフロー内の位置**: design → review-design → (fix → **verify**) → implement

## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |

**条件付きで注入される変数:**

| 変数 | 型 | 条件 | 説明 |
|------|-----|------|------|
| `cycle_count` | int | サイクル内ステップのみ | 現在のイテレーション番号 |
| `max_iterations` | int | サイクル内ステップのみ | サイクルの上限回数 |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue-number>
```

### 解決ルール

コンテキスト変数 `issue_number` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_number` として使用。

## 前提知識の読み込み

以下のドキュメントを Read ツールで読み込んでから作業を開始すること。

1. **テスト規約**: `docs/dev/testing-convention.md`
2. **コーディング規約**: `docs/reference/python/python-style.md`
   - 必要に応じて `docs/reference/python/naming-conventions.md` /
     `type-hints.md` / `docstring-style.md` / `error-handling.md` /
     `logging.md` を追加読込
3. **開発ワークフロー**: `docs/dev/development_workflow.md`

## verify と review の違い

| 項目 | review | verify |
|------|--------|--------|
| 目的 | フルレビュー | 修正確認のみ |
| 新規指摘 | する | **しない** |
| 確認範囲 | 設計全体 | 前回指摘箇所のみ |
| 使用タイミング | 設計完了後 | fix 後 |

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: コンテキスト取得

1. [_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、Worktree の絶対パスを取得。

2. **前回の指摘内容を取得**:
   ```bash
   gh issue view [issue-number] --comments
   ```
   「設計レビュー結果」と「設計修正報告」を確認。

3. **現在の設計書を確認**:
   ```bash
   cat [worktree-absolute-path]/draft/design/issue-[number]-*.md
   ```

### Step 2: 修正確認

#### 2.1 修正項目の確認

**確認すること:**
- 前回の「指摘事項 (Must Fix)」が適切に修正されているか
- 一次情報の追記を求められた場合：
  - 「参照情報（Primary Sources）」セクションが追加されているか
  - URL/パスが検証可能な形式で記載されているか

**確認しないこと:**
- 一次情報の内容自体の妥当性（それは次の `/issue-review-design` で行う）

#### 2.2 反論（見送り項目）の検討

「見送り」または「議論」とされた項目について、以下の観点で**徹底的に検討**する：

1. **反論の論理的妥当性**
   - 根拠が明確か？（「〜だから」が説明されているか）
   - 論理に飛躍や矛盾がないか？

2. **トレードオフの評価**
   - 指摘を受け入れた場合のコスト/リスクは妥当か？
   - 代替案は検討されているか？

3. **判定**
   - **受け入れる**: 反論に納得できる → 指摘を取り下げ
   - **再反論する**: 反論に問題がある → 理由を明記して再度修正を求める
   - **一部受け入れ**: 部分的に納得 → 妥協点を提示

**重要**: 反論を無視してはならない。必ず検討結果と理由を回答すること。

#### 2.3 新規発見事項の記録（任意）

確認作業中に前回指摘以外の問題を発見した場合：

- **判定には含めない**（verify の収束保証のため）
- **報告は行う**（情報損失を防ぐため）
- **推奨対応を添える**（放置されないように）

### Step 3: 確認結果のコメント

```bash
gh issue comment [issue-number] --body-file - <<'EOF'
# 設計修正確認結果

## 修正項目の確認

| 指摘項目 | 状態 | 理由・根拠 |
|----------|------|------------|
| (項目1) | ✅ OK | (なぜOKと判断したか：修正内容が指摘意図を満たしている等) |
| (項目2) | ❌ 要再修正 | (なぜNGか：修正が不十分、意図と異なる等の具体的理由) |

## 反論への検討結果

| 見送り項目 | 検討結果 | 理由 |
|------------|----------|------|
| (項目A) | ✅ 受け入れ | (なぜ反論を受け入れるか：論理的に妥当、トレードオフが許容範囲等) |
| (項目B) | ❌ 再修正を求める | (なぜ受け入れないか：根拠が不十分、代替案がない等) |
| (項目C) | ⚠️ 一部受け入れ | (妥協点：〜については受け入れるが、〜は対応が必要) |

## 新規発見事項（参考情報）

> **注意**: 以下は今回の判定には影響しません。verify の対象は前回指摘事項のみです。

| 発見事項 | 重要度 | 推奨対応 |
|----------|--------|----------|
| (問題の概要) | 高/中/低 | 別Issue起票 / 次フェーズで対応 / 将来検討 |

- **高（ブロッカー級）**: 現Issueのスコープに含めるか検討。含める場合は `/issue-review-design` からやり直し
- **中（改善推奨）**: 別Issueを起票して追跡
- **低（軽微/好み）**: 記録のみ。対応は任意

## 判定

[ ] Approve (実装着手可)
[ ] Changes Requested (再修正が必要)

## 次のステップ

(Approve の場合)
\`/issue-implement [issue-number]\` で実装を開始してください。

(Changes Requested の場合)
\`/issue-fix-design [issue-number]\` で再度修正してください。
EOF
```

### Step 4: 完了報告

以下の形式で報告してください:

```
## 設計修正確認完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| 判定 | Approve / Changes Requested |

### 次のステップ

- Approve: `/issue-implement [issue-number]` で実装開始
- Changes Requested: `/issue-fix-design [issue-number]` で再修正
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

```
---VERDICT---
status: PASS
reason: |
  修正が適切に行われている
evidence: |
  全 Must Fix 項目の修正を確認
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | Approve |
| RETRY | 修正不十分 |
| ABORT | 重大な問題 |
