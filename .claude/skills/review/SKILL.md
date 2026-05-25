---
description: PR/MR に対し初回コードレビューを実施し、Approve / Changes Requested を投稿する。新規レビュー専用（修正確認は /pr-verify）。`review-close` / `review-cycle` workflow では `review-poll` の `BACK_FALLBACK` を受けた fallback step として呼び出される。
name: review
---

# Review

PR/MR に対する **初回コードレビュー** を実施するスキル。
設計書整合 / コード品質 / テスト証跡 / docs 更新を観点に評価し、`kaji pr review` で正式な
Approve / Changes Requested を投稿する。

> **重要**: このスキルは **新規レビュー** 専用。修正確認（`/pr-fix` 後の収束確認）は
> `/pr-verify` を使うこと。両者を混同するとレビューサイクルが収束しなくなる。

## いつ使うか

| タイミング | このスキル |
|-----------|-----------|
| PR/MR 作成直後の初回レビュー（GitLab / 単体起動 / `review-poll` の `BACK_FALLBACK` 後の fallback） | ✅ 必須 |
| `/pr-fix` 後の修正確認 | ❌ `/pr-verify` を使う |
| `provider.type='github'` で codex auto-review が走る環境 | ⚠️ workflow からは `review-poll` 経由で fallback 用途のみ |
| `provider.type='github'` で codex auto-review が走らない環境（クレジット不足等） | ✅ `review-poll` → `BACK_FALLBACK` → 本 skill |
| `provider.type='gitlab'` 配下 | ✅ 受理（`kaji pr` の写像層が `glab` 命令を吸収） |
| `provider.type='local'` 配下 | ❌ Step 0 で ABORT。代替は `/issue-review-code` |

**ワークフロー内の位置**: i-pr → [PR 作成] → **review** → (pr-fix → pr-verify) → close

## 入力

### ハーネス経由（コンテキスト変数）

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID |
| `issue_ref` | str | 人間可読の Issue 参照 |
| `provider_type` | str | `github` / `gitlab` / `local` のいずれか。Step 0 のガード判定に使用 |
| `step_id` | str | 現在のステップ ID |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue_id>
```

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用。なければ `$ARGUMENTS` の第 1 引数を
`issue_id` として使用。`pr_id` は Step 1 で `kaji pr list --search` から逆引きする
（pr-verify と同じ規約）。

## 前提知識の読み込み

以下のドキュメントを Read ツールで読み込んでから作業を開始すること。

1. **開発ワークフロー**: `docs/dev/development_workflow.md`
2. **テスト規約**: `docs/dev/testing-convention.md`
3. **Python スタイル**: `docs/reference/python/python-style.md`

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 必須情報源 / 任意情報源

初回レビュー判定（Approve / Changes Requested）を出すために **必ず読まなければならない情報源**:

| Step | 情報源 | 必須/任意 |
|------|--------|-----------|
| Step 3a | `kaji pr view [pr_id]` — PR タイトル / 説明 / 状態 / linked Issue | **必須** |
| Step 3b | `git diff [git_remote]/[default_branch]...HEAD` — レビュー対象の差分本体 | **必須** |
| Step 3c | `kaji pr review-comments [pr_id]` — 既存 inline 指摘（重複指摘回避） | **必須** |
| Step 3d | `kaji pr reviews [pr_id]` — 過去の Approve / Changes Requested 履歴 | 任意 |
| Step 3e | `kaji pr view [pr_id] --comments` — 非 inline の議論経緯 | 任意 |
| Step 4  | `make check` — 品質ゲート（失敗時は Changes Requested 必至） | **必須** |

**Step 3a / 3b / 3c / 4 を欠いた状態でのレビュー判定は許可しない**。Step 3d / 3e は補助情報で、
レビュー対象の主たる入力ではない。

## 実行手順

### Step 0: provider check

本 Skill は forge provider 専用。最初に `provider_type` を解決し、`github` / `gitlab` 以外なら
**以降のステップに進まず ABORT verdict を出力して終了** する。

1. **`provider_type` の解決**（ハーネス注入 → 手動 fallback の優先順）:

   ```bash
   PROVIDER_TYPE="${provider_type:-$(kaji config provider-type 2>/dev/null || true)}"
   ```

2. **判定と verdict 出力**:

   - `PROVIDER_TYPE` が `github` / `gitlab` → Step 1 に進む
   - `PROVIDER_TYPE` が `local` → 以下を stdout に出力して終了:

     ```text
     ---VERDICT---
     status: ABORT
     reason: |
       review is forge-only and cannot run under provider.type='local'.
     evidence: |
       Pull request concept does not exist in local mode (bare provider).
     suggestion: |
       Use /issue-review-code instead.
     ---END_VERDICT---
     ```

   - `PROVIDER_TYPE` がそれ以外 → ABORT verdict（reason: `review could not resolve
     provider_type`、suggestion: `Add [provider] to .kaji/config.toml.`）を出力して終了

### Step 1: PR の解決

Issue 番号から関連 PR を解決し、`pr_id` / `pr_ref` を確定する（`pr-verify` Step 1 と同型）。

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

両方の経路で PR が見つからない場合は ABORT verdict を出して終了する
（`reason`: `no open PR/MR found for issue [issue_ref]`、
`suggestion`: `Create a PR via /i-pr [issue_id] first.`）。

### Step 2: Worktree パスの解決

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、Worktree の絶対パスを取得。
以降の `git diff` / `make check` は `[worktree_dir]` で実行する。

### Step 3: レビュー入力の収集

#### 3a. PR 概要取得（必須）

```bash
kaji pr view [pr_id]
```

タイトル / 説明 / 状態 / linked Issue を読み、PR の目的・スコープを把握する。

#### 3b. 差分本体取得（必須）

```bash
cd [worktree_dir] && git fetch [git_remote] [default_branch]
cd [worktree_dir] && git diff [git_remote]/[default_branch]...HEAD
```

レビュー対象の全コード差分。仕様判定の主入力。差分が大きい場合は主要ファイルを
個別に確認する。

#### 3c. 既存 inline 指摘の取得（必須）

```bash
kaji pr review-comments [pr_id]
```

既に他のレビュアー / セッションが付けた inline 指摘を確認し、**同じ指摘を重複して
書かない**。新規レビューであっても他者の既存指摘を上書きしないこと。

#### 3d. 既存 review state（任意）

```bash
kaji pr reviews [pr_id]
```

過去の Approve / Changes Requested 履歴。逆転判定や重複 Approve を避けるための補助情報。

#### 3e. top-level コメント（任意）

```bash
kaji pr view [pr_id] --comments
```

非 inline の議論経緯。初回レビューでは差分 (3b) が情報の中心だが、議論で確定済みの
方針があれば従う。

### Step 4: 品質チェック

```bash
cd [worktree_dir] && source .venv/bin/activate && make check
```

`make check` が失敗した場合は、原則として **Changes Requested**。失敗内容をレビュー本文に
含める。

### Step 5: レビュー観点評価

`issue-review-code` のレビュー観点を引き継ぐ:

1. **設計書整合性**: `draft/design/issue-[issue_id]-*.md` と差分が一致しているか
2. **コード品質**: 命名 / 責務分離 / 重複排除 / エラーハンドリング
3. **テスト証跡**: 設計書「テスト戦略」で定義した検証が実施されたか
4. **docs 更新**: 設計書「影響ドキュメント」の「あり」項目が更新されたか
5. **Scope 混在**: 設計書のスコープ境界を逸脱した変更がないか

各観点について ✅ / ⚠️ / ❌ で判定し、❌ または ⚠️ がある項目を Must Fix / Should Fix として
列挙する。

### Step 6: 正式 review 投稿

判定結果に応じて、`kaji pr review` で正式な review state を更新する。

#### Approve の場合

```bash
kaji pr review [pr_id] --approve --body-file - <<'EOF'
## 初回コードレビュー結果

### 参照した一次情報

| 情報源 | 確認結果 |
|--------|----------|
| `kaji pr view [pr_id]` | ✅ PR 目的 / スコープ確認 |
| `git diff [git_remote]/[default_branch]...HEAD` | ✅ 差分全量確認 |
| `kaji pr review-comments [pr_id]` | ✅ 既存 inline 指摘なし / 重複なし |
| `make check` | ✅ PASS |

### 観点評価

| 観点 | 判定 | 根拠 |
|------|------|------|
| 設計書整合 | ✅ | (引用) |
| コード品質 | ✅ | (引用) |
| テスト証跡 | ✅ | (引用) |
| docs 更新 | ✅ | (引用) |
| Scope 混在 | ✅ | (引用) |

### 判定

[x] Approve
[ ] Changes Requested

### 次のステップ

`/issue-close [issue_id]` で PR マージ & クリーンアップ。
EOF
```

#### Changes Requested の場合

```bash
kaji pr review [pr_id] --request-changes --body-file - <<'EOF'
## 初回コードレビュー結果

### 参照した一次情報

| 情報源 | 確認結果 |
|--------|----------|
| `kaji pr view [pr_id]` | ✅ |
| `git diff [git_remote]/[default_branch]...HEAD` | ✅ |
| `kaji pr review-comments [pr_id]` | ✅ |
| `make check` | ✅ / ❌ |

### 観点評価

| 観点 | 判定 | 根拠 |
|------|------|------|
| 設計書整合 | ✅ / ⚠️ / ❌ | (引用) |
| コード品質 | ✅ / ⚠️ / ❌ | (引用) |
| テスト証跡 | ✅ / ⚠️ / ❌ | (引用) |
| docs 更新 | ✅ / ⚠️ / ❌ | (引用) |
| Scope 混在 | ✅ / ⚠️ / ❌ | (引用) |

### 指摘事項 (Must Fix)

- [ ] **point 1**: (具体的修正内容) — 根拠: (一次情報)

### 改善提案 (Should Fix)

- **point N**: (具体的修正内容)

### 判定

[ ] Approve
[x] Changes Requested

### 次のステップ

`/pr-fix [issue_id]` で修正対応をお願いします。
EOF
```

> **規約遵守**: GitLab auto-close hazard pattern（`Clos(e[sd]?|ing)` / `Fix(e[sd]|ing)?` /
> `Resolv(e[sd]?|ing)` / `Implement(s|ing|ed)?` の直後 `#[0-9]`）を本文に書かない。
> 指摘参照は `指摘 N` / `point N` / `Must Fix item N` で統一する
> （[`docs/dev/shared_skill_rules.md`](../../../docs/dev/shared_skill_rules.md) § GitLab auto close keyword 回避規約）。

### Step 7: 完了報告

```
## 初回コードレビュー完了

| 項目 | 値 |
|------|-----|
| PR | [pr_ref] |
| Issue | [issue_ref] |
| 判定 | Approve / Changes Requested |

### 次のステップ

- Approve: `/issue-close [issue_id]` で PR マージ & クリーンアップ
- Changes Requested: `/pr-fix [issue_id]` で修正対応
```

## Verdict 出力

実行完了後、以下の形式で verdict を **stdout に** 出力すること。

```
---VERDICT---
status: PASS
reason: |
  PR の初回レビューを Approve として投稿
evidence: |
  全観点 ✅、make check PASS、kaji pr review --approve 投稿済み
suggestion: |
  /issue-close [issue_id] でマージへ
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | Approve を投稿 |
| RETRY | Changes Requested を投稿（pr-fix へ進む合図） |
| ABORT | Step 0 で provider mismatch / Step 1 で PR が見つからない等の致命的問題 |
