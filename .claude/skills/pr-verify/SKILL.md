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
| `provider.type='local'` 配下 | ❌ Step 0 で ABORT。代替は `/issue-verify-code` |

**ワークフロー内の位置**: i-pr → [PR review] → (pr-fix → **pr-verify**) → close

## 引数

```
$ARGUMENTS = <issue_id>
```

- Issue 番号を受け付ける（関連 PR を自動解決する）

### コンテキスト変数

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値、または `local-*`） |
| `issue_ref` | str | 人間可読の Issue 参照 |
| `provider_type` | str | `github` または `local`。Step 0 のガード判定に使用 |

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_id` として使用。

`issue_ref` はハーネス経由ではプロンプトに自動注入される（`prompt.py` 側で provider 別に整形）。手動実行時は `issue_id` から導出する: GitHub 数値 ID なら `#<issue_id>`、`local-*` 形式なら bare ID（`#` を付けない）。

`pr_id` はハーネス経由では Phase 4 時点ではプロンプトに自動注入されない（Phase 5 で GitHubProvider が解決して prompt 注入する予定）。Phase 4 時点では Step 1 内で `kaji pr list --search` から取得して確定する。`pr_ref` は `pr_id` から導出する: GitHub 数値 ID なら `#<pr_id>`、それ以外は bare ID。

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

### Step 0: provider check

本 Skill は forge provider 専用。`provider_type` を解決し、`github` 以外なら ABORT する。

**provider_type の解決順序**:

1. ハーネス経由で `[provider_type]` が注入されていればそれを使用
2. 未注入（手動実行）の場合は `kaji config provider-type` を呼んで解決:

   ```bash
   PROVIDER_TYPE="${provider_type:-$(kaji config provider-type 2>/dev/null || true)}"
   ```

   `|| true` で exit code を握りつぶし、空文字 / 不明値の場合は次の判定で明示的に ABORT する。

**判定**:

```bash
case "$PROVIDER_TYPE" in
    github) : ;;  # 続行
    local)
        cat <<'MSG'
pr-verify is a forge-only skill and cannot run under provider.type='local'.
Pull request concept does not exist in local mode. Use the verify skill
for code review directly:
  /issue-verify-code
MSG
        exit 0
        ;;
    *)
        echo "ABORT: provider_type unresolved. Check .kaji/config.toml has [provider]."
        exit 0
        ;;
esac
```

`provider_type` が `github` 以外の場合は以下の verdict で **ABORT** すること:

```text
---VERDICT---
status: ABORT
reason: |
  pr-verify is a forge-only skill; current provider.type is not 'github'.
evidence: |
  PROVIDER_TYPE="$PROVIDER_TYPE"（local mode では PR 概念が無い）
suggestion: |
  Use /issue-verify-code instead.
---END_VERDICT---
```

### Step 1: コンテキスト取得

1. **PR の特定**:
   Issue 番号から関連 PR を解決し、`pr_id` / `pr_ref` を確定する。

   ```bash
   PR_JSON=$(kaji pr list --search "[issue_id]" --json number,title,headRefName --jq '.[0]')
   pr_id=$(echo "$PR_JSON" | jq -r '.number')
   pr_ref="#${pr_id}"
   ```

   見つからない場合は Issue 本文の `> **Branch**:` 行からブランチ名を取得し:

   ```bash
   PR_JSON=$(kaji pr list --head "[branch_name]" --json number,title --jq '.[0]')
   pr_id=$(echo "$PR_JSON" | jq -r '.number')
   pr_ref="#${pr_id}"
   ```

2. **Worktree パスの解決**:
   [_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、Worktree の絶対パスを取得。

3. **前回の指摘と対応報告の取得**:

   ```bash
   kaji pr view [pr_id] --comments
   kaji pr reviews [pr_id] --jq '.[] | {user: .user.login, state: .state, body: .body}'
   kaji pr review-comments [pr_id] --jq '.[] | {path: .path, line: .line, body: .body, user: .user.login}'
   ```

   「レビュー指摘への対応報告」コメントを確認する。

4. **修正差分の確認**:

   ```bash
   cd [worktree_dir] && git log --oneline -5
   cd [worktree_dir] && git diff HEAD~1
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
- **推奨対応を添える**(放置されないように)

### Step 3: 品質チェック

```bash
cd [worktree_dir] && source .venv/bin/activate && make check
```

### Step 4: 確認結果の投稿と PR レビュー状態の更新

判定結果に応じて、GitHub の正式なレビュー状態を更新する。

#### Approve の場合

```bash
kaji pr review [pr_id] --approve --body-file - <<'EOF'
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
kaji pr review [pr_id] --request-changes --body-file - <<'EOF'
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
| PR | [pr_ref] |
| Issue | [issue_ref] |
| 判定 | Approve / Changes Requested |

### 次のステップ

- Approve: `/issue-close [issue_id]` で PR マージ & クリーンアップ
- Changes Requested: `/pr-fix [issue_id]` で再修正
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
| ABORT | 重大な問題 / Step 0 で provider mismatch |
