---
description: PR レビュー修正が適切に行われたかを確認する。新規指摘は行わない（レビュー収束のため）。
name: pr-verify
---

# PR Verify

> **重要**: このスキルは修正を行ったセッションとは **別のセッション** で実行することを推奨します。
> 同一セッションで実行すると、修正時のバイアスが確認判断に影響する可能性があります。

PR レビュー修正後の確認を行う。

**重要**: このスキルは「指摘事項が適切に修正されたか」のみを確認する。
**新規の指摘は行わない**。これはレビューサイクルの収束を保証するためである。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/pr-fix` 後の修正確認 | ✅ 必須 |
| 新規レビューが必要な場合 | ❌ PR 上で直接レビューを実施 |

**ワークフロー内の位置**: i-pr → [PR review] → (pr-fix → **pr-verify**) → close

## 引数

```
$ARGUMENTS = <issue-number>
```

- Issue 番号を受け付ける（関連 PR を自動解決する）

### 解決ルール

コンテキスト変数 `issue_number` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_number` として使用。

## 前提知識の読み込み

変更対象に応じて、以下のドキュメントを Read ツールで読み込んでから作業を開始すること。

1. **テスト規約**: `docs/dev/testing-convention.md`
2. **コーディング規約**: `docs/reference/python/python-style.md`（型ヒント、docstring 等）
3. **エラーハンドリング**: `docs/reference/python/error-handling.md`

## verify と新規レビューの違い

| 項目 | 新規レビュー | verify |
|------|-------------|--------|
| 目的 | フルレビュー | 修正確認のみ |
| 新規指摘 | する | **しない** |
| 確認範囲 | コード全体 | 前回指摘箇所のみ |
| 使用タイミング | 初回レビュー | pr-fix 後 |

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: コンテキスト取得

1. **PR の特定**:
   Issue 番号から関連 PR を解決する。
   ```bash
   gh pr list --search "[issue-number]" --json number,title,headRefName --jq '.'
   ```
   見つからない場合は Issue 本文の `> **Branch**:` 行からブランチ名を取得し:
   ```bash
   gh pr list --head "[branch-name]" --json number,title --jq '.'
   ```

2. **Worktree パスの解決**:
   [_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、Worktree の絶対パスを取得。

3. **前回の指摘と対応報告の取得**:
   ```bash
   gh pr view [pr-number] --comments
   gh api repos/{owner}/{repo}/pulls/[pr-number]/reviews --jq '.[] | {user: .user.login, state: .state, body: .body}'
   gh api repos/{owner}/{repo}/pulls/[pr-number]/comments --jq '.[] | {path: .path, line: .line, body: .body, user: .user.login}'
   ```
   「レビュー指摘への対応報告」コメントを確認する。

4. **修正差分の確認**:
   ```bash
   cd [worktree-absolute-path] && git log --oneline -5
   cd [worktree-absolute-path] && git diff HEAD~1
   ```

### Step 2: 修正確認

#### 2.1 修正項目の確認

**確認すること:**
- 前回の指摘事項が適切に修正されているか
- 修正によるデグレードがないか

#### 2.2 反論（見送り項目）の検討

「見送り」または「反論」とされた項目について、以下の観点で **徹底的に検討** する:

1. **反論の論理的妥当性**
   - 根拠が明確か?
   - 論理に飛躍や矛盾がないか?

2. **技術的妥当性**
   - コードベースの一貫性を損なわないか?
   - 将来の保守性に問題はないか?

3. **トレードオフの評価**
   - 指摘を受け入れた場合のコスト/リスクは妥当か?
   - 代替案は検討されているか?

4. **判定**
   - **受け入れる**: 反論に納得 → 指摘を取り下げ
   - **再反論する**: 反論に問題あり → 理由を明記して再修正を求める
   - **一部受け入れ**: 部分的に納得 → 妥協点を提示

**重要**: 反論を無視してはならない。必ず検討結果と理由を回答すること。

#### 2.3 新規発見事項の記録（任意）

確認作業中に前回指摘以外の問題を発見した場合:

- **判定には含めない**（verify の収束保証のため）
- **報告は行う**（情報損失を防ぐため）
- **推奨対応を添える**（放置されないように）

### Step 3: 品質チェック

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && make check
```

### Step 4: 確認結果の投稿と PR レビュー状態の更新

判定結果に応じて、GitHub の正式なレビュー状態を更新する。

#### Approve の場合

```bash
gh pr review [pr-number] --approve --body-file - <<'EOF'
## PR レビュー修正確認結果

### 修正項目の確認

| 指摘項目 | 状態 | 理由・根拠 |
|----------|------|------------|
| (項目1) | ✅ OK | (なぜ OK と判断したか) |

### 反論への検討結果

| 見送り項目 | 検討結果 | 理由 |
|------------|----------|------|
| (項目A) | ✅ 受け入れ | (なぜ反論を受け入れるか) |

### 新規発見事項（参考情報）

> **注意**: 以下は今回の判定には影響しません。verify の対象は前回指摘事項のみです。

| 発見事項 | 重要度 | 推奨対応 |
|----------|--------|----------|
| (問題の概要) | 高/中/低 | 別 Issue 起票 / 次フェーズ / 将来検討 |

### 品質チェック

- `make check`: PASS
EOF
```

#### Changes Requested の場合

```bash
gh pr review [pr-number] --request-changes --body-file - <<'EOF'
## PR レビュー修正確認結果

### 修正項目の確認

| 指摘項目 | 状態 | 理由・根拠 |
|----------|------|------------|
| (項目1) | ✅ OK | (なぜ OK と判断したか) |
| (項目2) | ❌ 要再修正 | (なぜ NG か) |

### 反論への検討結果

| 見送り項目 | 検討結果 | 理由 |
|------------|----------|------|
| (項目B) | ❌ 再修正を求める | (なぜ受け入れないか) |
| (項目C) | ⚠️ 一部受け入れ | (妥協点) |

### 品質チェック

- `make check`: PASS / FAIL
EOF
```

### Step 5: 完了報告

以下の形式で報告すること。

```
## PR レビュー修正確認完了

| 項目 | 値 |
|------|-----|
| PR | #[pr-number] |
| Issue | #[issue-number] |
| 判定 | Approve / Changes Requested |

### 次のステップ

- Approve: `/issue-close [issue-number]` で PR マージ & クリーンアップ
- Changes Requested: `/pr-fix [issue-number]` で再修正
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること。

```
---VERDICT---
status: PASS
reason: |
  修正が適切に行われている
evidence: |
  全指摘事項の修正を確認、make check 通過
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | Approve |
| RETRY | 修正不十分 |
| ABORT | 重大な問題 |
