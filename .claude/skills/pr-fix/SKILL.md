---
description: PR 上のレビューコメントに基づきコード修正・コミット・レビュー返信を行う。
name: pr-fix
---

# PR Fix

PR 上のコードレビューコメントに基づき、修正対応を行う。
指摘を盲目的に受け入れるのではなく、技術的な妥当性を検討し、修正と反論を使い分ける。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| PR にレビューコメント（Changes Requested 等）が付いた後 | ✅ 必須 |
| PR 作成前（Issue ワークフロー内のレビュー） | ❌ `/issue-fix-code` を使用 |
| `provider.type='local'` 配下 | ❌ Step 0 で ABORT。代替は `/issue-review-code` 等 |

**ワークフロー内の位置**: i-pr → [PR review] → (**pr-fix** → pr-verify) → close

## 引数

```
$ARGUMENTS = <issue_id>
```

- Issue 番号を受け付ける（関連 PR を自動解決する）

### コンテキスト変数

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値、または `local-*`） |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`、local では bare ID） |
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

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 0: provider check

本 Skill は forge provider 専用。最初に `provider_type` を解決し、
`github` 以外なら **以降のステップに進まず ABORT verdict を出力して終了** する。

**手順**:

1. **`provider_type` の解決**（ハーネス注入 → 手動 fallback の優先順）:

   ```bash
   PROVIDER_TYPE="${provider_type:-$(kaji config provider-type 2>/dev/null || true)}"
   ```

   `|| true` は手動実行で `[provider]` 不在時に `kaji config provider-type` が
   exit 2 を返しても shell 全体を落とさないため。空文字に縮退する。

2. **判定と verdict 出力**:

   - `PROVIDER_TYPE` が `github` → Step 1 に進む
   - `PROVIDER_TYPE` が `local` → 以下の ABORT verdict を **そのまま stdout に
     出力**して以降のステップは実行しない:

     ```text
     ---VERDICT---
     status: ABORT
     reason: |
       pr-fix is a forge-only skill; provider.type='local'.
     evidence: |
       Pull request concept does not exist in local mode (bare provider).
     suggestion: |
       Use /issue-review-code, /issue-fix-code, /issue-verify-code instead.
     ---END_VERDICT---
     ```

   - `PROVIDER_TYPE` がそれ以外（空文字 / 不明値）→ 以下の ABORT verdict を
     出力して終了:

     ```text
     ---VERDICT---
     status: ABORT
     reason: |
       pr-fix could not resolve provider_type.
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

3. **レビューコメントの取得**:

   ```bash
   kaji pr view [pr_id] --comments
   kaji pr reviews [pr_id] --jq '.[] | {user: .user.login, state: .state, body: .body}'
   kaji pr review-comments [pr_id] --jq '.[] | {id: .id, path: .path, line: .line, body: .body, user: .user.login}'
   ```

   **注意**: inline comment の `id` を控えておく。Step 5 で thread 返信に使用する。

4. **現状把握**:
   指摘されている該当コード周辺を確認する。

### Step 2: 対応方針の検討

各指摘事項について **1つずつ** 検討する。

- **A: 対応する (Agree)**
  - 指摘が正しく、修正により品質・安全性が向上する場合
  - **改善提案の場合**: メリットが明確なら採用。大規模リファクタや高リスクなら見送り可

- **B: 対応しない/反論する (Disagree/Discuss)**
  - 指摘が誤解に基づいている場合
  - 修正による副作用やコストがメリットを上回る場合
  - CLAUDE.md の方針や既存の設計思想と矛盾する場合
  - **必須**: 反論する場合は明確な論理的根拠を用意する

### Step 3: 修正の実行

1. **コード修正**:
   採用した指摘事項に基づきコードを修正する。

2. **品質チェック（コミット前必須）**:

   ```bash
   cd [worktree_dir] && source .venv/bin/activate && make check
   ```

   **すべてパスするまでコミットしてはならない**。

### Step 4: コミット & プッシュ

```bash
cd [worktree_dir] && git add . && git commit -m "fix: address PR review feedback for [issue_ref]"
cd [worktree_dir] && git push
```

### Step 5: PR にレビュー返信

#### 5.1 インラインコメントへの thread 返信

Step 1 で取得した各 inline review comment に対し、thread 内で返信する。

```bash
kaji pr reply-to-comment [pr_id] --to [comment_id] --body "(対応内容または反論の要約)"
```

- **対応済みの指摘**: 修正内容を簡潔に説明し、コミットハッシュを添える
- **見送り/反論**: 理由と論理的根拠を明記する

#### 5.2 全体サマリーの投稿

top-level PR コメントとして全体サマリーを投稿する:

```bash
kaji pr comment [pr_id] --body-file - <<'EOF'
## レビュー指摘への対応報告

### 対応済み

- **(指摘内容の要約)**
  - 修正内容: (どう修正したか)

### 見送り・反論

- **(指摘内容の要約)**
  - 理由: (なぜ対応しなかったか。根拠となるロジック)

### 品質チェック

- `make check`: PASS

### 次のステップ

`/pr-verify [issue_id]` で修正確認をお願いします。
EOF
```

### Step 6: 完了報告

以下の形式で報告すること。

```
## PR レビュー対応完了

| 項目 | 値 |
|------|-----|
| PR | [pr_ref] |
| Issue | [issue_ref] |
| 対応済み | N 件 |
| 見送り | M 件 |

### 次のステップ

`/pr-verify [issue_id]` で修正確認を実施してください。
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること。

```
---VERDICT---
status: PASS
reason: |
  修正完了
evidence: |
  全指摘事項に対応済み、make check 通過
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 修正完了 |
| ABORT | 修正不可能 / Step 0 で provider mismatch |
