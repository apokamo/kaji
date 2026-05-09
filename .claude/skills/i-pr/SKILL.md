---
description: workflow 共通の PR 作成スキル。worktree 解決、未コミット確認、push、kaji pr create のみを担当する。
name: i-pr
---

# I PR

workflow 共通の PR 作成スキル。
workflow 固有の完了判定は持たず、PR 作成そのものに責務を限定する。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `i-dev-final-check` 完了後 | ✅ 必須 |
| `i-doc-final-check` 完了後 | ✅ 必須 |
| `provider.type='local'` 配下 | ❌ Step 0 で ABORT。代替は `/issue-close`（local merge） |
| `provider.type='gitlab'` 配下 | ✅ 受理（`kaji pr` の写像層が glab 命令を吸収） |

## このスキルがやらないこと

- 品質チェック（`make check`）の実行 → `i-dev-final-check` / `i-doc-final-check` の責務
- 設計書アーカイブ → `i-dev-final-check` の責務
- エビデンス集約 → `i-dev-final-check` / `i-doc-final-check` の責務
- PR マージ・ブランチ削除 → `issue-close` の責務

## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値または local ID） |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`、local では bare ID） |
| `step_id` | str | 現在のステップ ID |
| `provider_type` | str | `github` / `gitlab` / `local` のいずれか。Step 0 のガード判定に使用 |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue_id>
```

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_id` として使用。

`issue_ref` はハーネス経由ではプロンプトに自動注入される（`prompt.py` 側で provider 別に整形）。手動実行時は `issue_id` から導出する: GitHub 数値 ID なら `#<issue_id>`、`local-*` 形式なら bare ID（`#` を付けない）。

`pr_id` / `pr_ref` / `pr_url` は本 Skill の Step 4 で `kaji pr create` の出力から確定する。プロンプトへの自動注入は現時点では行わない（forge 採用先確定時に再評価、`draft/design/local-mode/design.md` §残課題 参照）。

## 前提知識の読み込み

1. [docs/dev/workflow_overview.md](../../../docs/dev/workflow_overview.md)
2. [docs/dev/shared_skill_rules.md](../../../docs/dev/shared_skill_rules.md)
3. `docs/guides/git-commit-flow.md`（kaji の Conventional Commits 運用、`--no-ff` merge 規約）

## 前提条件

- `/issue-start` が実行済みであること
- `git absorb` がインストール済みであること（任意）

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 0: provider check

本 Skill は forge provider 専用。最初に `provider_type` を解決し、
`github` / `gitlab` 以外なら **以降のステップに進まず ABORT verdict を出力して終了** する。

**手順**:

1. **`provider_type` の解決**（ハーネス注入 → 手動 fallback の優先順）:

   ```bash
   PROVIDER_TYPE="${provider_type:-$(kaji config provider-type 2>/dev/null || true)}"
   ```

   `|| true` は手動実行で `[provider]` 不在時に `kaji config provider-type` が
   exit 2 を返しても shell 全体を落とさないため。空文字に縮退する。

2. **判定と verdict 出力**:

   - `PROVIDER_TYPE` が `github` / `gitlab` → Step 1 に進む（`kaji pr` の
     写像層が forge 別の引数差異を吸収するため、本 Skill は両 forge で同じ
     コマンドを使う）
   - `PROVIDER_TYPE` が `local` → 以下の ABORT verdict を **そのまま stdout に
     出力**して以降のステップは実行しない:

     ```text
     ---VERDICT---
     status: ABORT
     reason: |
       i-pr is a forge-only skill; provider.type='local'.
     evidence: |
       Pull request concept does not exist in local mode (bare provider).
     suggestion: |
       Use /issue-close for local merge instead.
     ---END_VERDICT---
     ```

   - `PROVIDER_TYPE` がそれ以外（空文字 / 不明値）→ 以下の ABORT verdict を
     出力して終了:

     ```text
     ---VERDICT---
     status: ABORT
     reason: |
       i-pr could not resolve provider_type.
     evidence: |
       provider_type was not injected and `kaji config provider-type` failed
       (likely missing `[provider]` section in .kaji/config.toml).
     suggestion: |
       Add `[provider]` to .kaji/config.toml. See docs/cli-guides/local-mode.md.
     ---END_VERDICT---
     ```

> **重要**: ABORT verdict は **shell の `exit` に任せず agent 自身が stdout に
> 出力する**こと。workflow runner はその verdict を読み取って `on: ABORT: end`
> で workflow を終わらせる。
>
> **補足**: workflow 経由で起動された場合は、`kaji run` の `requires_provider`
> 検証（Phase 4 commit 4）で先に止まるはずだが、user が `provider=local` 配下で
> `/i-pr` を直接呼ぶケースに備えて Skill 層でも止める（3 層ガードの冗長性）。

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。以降のステップではこのパスを使用する。

また、Issue 本文から `> **Branch**: \`[prefix]/[issue_id]\`` を抽出して prefix を取得する。

### Step 2: 未コミットの変更確認

```bash
cd [worktree_dir] && git status
```

未コミットの変更がある場合は先にコミットしてください。
workflow 固有の docs 同梱判定は `i-dev-final-check` / `i-doc-final-check` 側の責務とする。

### Step 3: コミット履歴の整理

```bash
cd [worktree_dir] && git absorb --and-rebase
```

fixup対象がない場合は何も起きません（正常）。
`git absorb` がインストールされていない場合はスキップ。

### Step 4: プッシュとPR作成

```bash
cd [worktree_dir] && git push -u origin HEAD
```

```bash
# stderr は捨てず stdout と分離して取得する（失敗を握りつぶさないため）。
# ``2>&1 | tail -1`` 形式は pipeline exit status が tail 側で 0 になるため使わない。
pr_output=$(cd [worktree_dir] && kaji pr create --base main --title "[prefix]: タイトル ([issue_ref])" --body "$(cat <<'EOF'
## Summary

(Issueの概要を1-2文で)

Closes [issue_ref]

## Changes

- (主な変更点)

## Documentation

- (ドキュメントの更新内容。設計書昇格 / 既存 docs 更新 / なし)

## Test Plan

- [x] 既存テストがパス
- [ ] 新規テストを追加（該当する場合）
- [ ] 手動検証: (必要な場合)
EOF
)")
pr_create_rc=$?

# 成功時のみ最終行を取り出す（失敗時の stdout を URL として誤認しないため）。
pr_url=""
if [ "$pr_create_rc" -eq 0 ]; then
    pr_url=$(printf '%s\n' "$pr_output" | tail -n1)
fi

# 失敗 / 形式不一致のいずれかなら abort_reason に診断を入れて分岐する。
# pr_id / pr_ref は abort_reason が空のときだけ確定する（壊れた値を作らない）。
abort_reason=""
if [ "$pr_create_rc" -ne 0 ]; then
    abort_reason="kaji pr create exited with rc=$pr_create_rc"
elif ! printf '%s' "$pr_url" | grep -Eq '^https://github\.com/[^/]+/[^/]+/pull/[1-9][0-9]*$|^https://gitlab\.com/[^/]+/[^/]+(/[^/]+)*/-/merge_requests/[1-9][0-9]*$'; then
    abort_reason="kaji pr create last line is not a PR/MR URL: '$pr_url'"
fi

if [ -n "$abort_reason" ]; then
    # 失敗時: 診断を stderr に出し、Step 5 / Step 6 には進まない。
    # 後述「失敗時の処理」の ABORT verdict をそのまま stdout に出力して終了する。
    printf 'ERROR: %s\n' "$abort_reason" >&2
    printf '%s\n' "$pr_output" >&2
    # ここから先（pr_id 確定 / Step 5 / Step 6）には進まないこと。
else
    # 成功時のみ pr_id / pr_ref を確定する。
    pr_id="${pr_url##*/}"   # "42"
    pr_ref="#${pr_id}"      # "#42"
fi
```

> **失敗時の処理**: `abort_reason` が非空（= `pr_create_rc` 非 0、または
> `pr_url` が `https://github.com/.../pull/<N>` 形式に一致しない）の場合、
> **以降のステップ（Step 5: Issue 本文への PR 番号追記、Step 6: 完了報告）には
> 進まない**こと。`pr_id` / `pr_ref` は確定しておらず、未確定値で Issue 本文を
> 上書きすると壊れた `**PR**:` 行を残す事故になる。
>
> その場合は以下の ABORT verdict を **そのまま stdout に出力して** 終了する:
>
> ```text
> ---VERDICT---
> status: ABORT
> reason: |
>   kaji pr create failed or returned an unexpected output.
> evidence: |
>   abort_reason="$abort_reason"
>   pr_create_rc=$pr_create_rc
>   (full output captured in pr_output / stderr above)
> suggestion: |
>   Re-run after addressing the underlying error (auth / rate limit /
>   pre-existing PR for the branch). Check `gh auth status` (github)
>   or `glab auth status` (gitlab) and `kaji pr list --head <branch>`.
> ---END_VERDICT---
> ```

> **重要**: PR body に `Closes [issue_ref]` を必ず含めること。これにより GitHub の Development sidebar に正式リンクが作成される。

> **マージ規約**: kaji の merge 規約は `--no-ff` only（squash merge 禁止）。マージ自体は `/issue-close` の責務だが、PR タイトルとコミットは Conventional Commits に従うこと（`docs/guides/git-commit-flow.md` 参照）。

### Step 5: Issue 本文に PR 番号を追記

PR 作成後、Issue 本文のメタ情報（NOTE ブロック）に PR 番号を追加:

```bash
CURRENT_BODY=$(kaji issue view [issue_id] --json body -q '.body')
# **Branch** 行の後に **PR**: [pr_ref] を追加した本文を作成して更新
kaji issue edit [issue_id] --commit --body "..."
```

### Step 6: 完了報告

以下の形式で報告してください:

```
## PR作成完了

| 項目 | 値 |
|------|-----|
| Issue | [issue_ref] |
| PR | [pr_ref] |
| URL | [pr_url] |
| コミット整理 | git absorb 実行済み / スキップ |

### 次のステップ

PRのマージ準備ができたら `/issue-close [issue_id]` を実行してください。
```

## 非責務

- dev / docs-only の個別ルール判定
- docs 昇格や docs 同梱の妥当性判定
- final-check 実行済みかどうかの代行判断
- マージ実行（`/issue-close` の責務）

## Verdict 出力

```text
---VERDICT---
status: PASS
reason: |
  PR 作成を完了した
evidence: |
  push と kaji pr create が成功した
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | PR 作成成功 |
| RETRY | 再試行で解決可能な失敗 |
| ABORT | 継続不能な失敗 / Step 0 で provider mismatch |
