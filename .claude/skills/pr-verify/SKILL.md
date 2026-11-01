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
| `provider.type='github'` 配下 | ✅ 受理（gh CLI 経由） |

**ワークフロー内の位置**: i-pr → [PR review] → (pr-fix → **pr-verify**) → close

## 引数

```
$ARGUMENTS = <issue_id>
```

- Issue 番号を受け付ける（関連 PR を自動解決する）

### コンテキスト変数

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値） |
| `issue_ref` | str | 人間可読の Issue 参照 |
| `provider_type` | str | `github`（現行唯一の provider）。Step 0 のガード判定に使用 |

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_id` として使用。

`issue_ref` はハーネス経由ではプロンプトに自動注入される（`prompt.py` 側で provider 別に整形）。手動実行時は `issue_id` から `#<issue_id>` を導出する。

`pr_id` / `pr_ref` はハーネス経由ではプロンプトに自動注入される（`runner.py` の `_resolve_pr_context_safe` が `GitHubProvider.resolve_pr_context()` 経由でブランチから PR を逆引きして展開する）。手動実行時、および auto-resolve が失敗した（branch 未 push / PR 未作成）場合は Step 1 で fallback として `kaji pr list --head` から取得する。`pr_ref` は `gh:<pr_id>` 形式で組み立てる（`kaji_harness/providers/models.py` `PRContext` 準拠）。

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
   - `PROVIDER_TYPE` がそれ以外（空文字 / 不明値）→ 以下の ABORT verdict を
     出力して終了:

     ```text
     ---VERDICT---
     status: ABORT
     reason: |
       pr-verify could not resolve provider_type.
     evidence: |
       provider_type was not injected and `kaji config provider-type` failed
       (likely missing `[provider]` section in .kaji/config.toml).
     suggestion: |
       Add `[provider]` to .kaji/config.toml. See docs/cli-guides/github-mode.md.
     ---END_VERDICT---
     ```

> **重要**: ABORT verdict は **shell の `exit` に任せず agent 自身が stdout に
> 出力する**こと。workflow runner はその verdict を読み取って `on: ABORT: end`
> で workflow を終わらせる。

### Step 1: コンテキスト取得

1. **PR の特定**:
   `pr_id` / `pr_ref` はハーネス注入時にプロンプトへ展開済み（`{{pr_id}}` / `{{pr_ref}}`）。手動実行、または auto-resolve が失敗した場合のみ fallback として Issue 本文の `> **Branch**:` 行からブランチ名を取得し:

   ```bash
   PR_JSON=$(kaji pr list --head "[branch_name]" --json number,title --jq '.[0]')
   pr_id=$(echo "$PR_JSON" | jq -r '.number')
   pr_ref="gh:${pr_id}"
   ```

2. **Worktree パスの解決**:
   [_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、Worktree の絶対パスを取得。

3. **前回の指摘と対応報告の取得**:

   ```bash
   kaji pr view [pr_id] --comments
   kaji pr reviews [pr_id] --jq '.[] | {user: .user.login, state: .state, body: .body}'
   kaji pr review-comments [pr_id] --jq '.[] | {id: .id, in_reply_to_id: .in_reply_to_id, path: .path, line: .line, body: .body, user: .user.login}'
   ```

   「レビュー指摘への対応報告」コメントを確認する。インラインの指摘は `id` と `in_reply_to_id` を
   控えておく（Step 3.5 でどの指摘がどのスレッドに属するかを対応付けるため）。

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

### Step 3.5: 確認済みスレッドの解決

Step 2 の判定に従い、確認済みのインライン指摘のレビュースレッドを
`kaji pr resolve-thread` で解決する。`resolveReviewThread` はコメント単体ではなく
**スレッド全体**を解決するため、判定単位は指摘、副作用の単位はスレッドとし、
指摘ごとの判定をスレッドごとに集約してから実行する。

**集約手順**:

1. 今回の verify 対象の既出指摘のうち、インラインレビューコメントに対応するものを列挙する。
   インラインでない指摘（PR 会話欄のコメント・review 本文）はスレッドが存在しないため対象外。
2. 各指摘を、そのコメントが属するスレッドの **根コメント ID** でグループ化する
   （Step 1-3 で控えた `in_reply_to_id` が null ならその `id`、そうでなければ `in_reply_to_id`）。
3. グループ内の **すべての** 指摘が「✅ OK（解消）」（Step 2.1）または「受け入れる」（Step 2.2）なら、
   そのスレッドを解決対象とする。
4. グループ内に 1 件でも「❌ 要再修正」「再反論する」「一部受け入れ」が残るなら、そのスレッドは
   resolve を実行せず **開いたまま** にする（未解消の指摘まで閉じないため）。

- verify で新規指摘を追加しない原則は変えない。集約の母集団は既出指摘だけである。
- レビュアー（Codex bot / `/review` / 人間）で区別しない。
- 判定全体が Approve でも Changes Requested でも、解決対象のスレッドは解決する（集約はスレッドごと）。

**判定例**:

| ケース | 既出指摘と判定 | resolve の実行 |
|-------|---------------|----------------|
| 同一スレッドで混在 | スレッド T の根の指摘 A = 解消、T の返信の指摘 B = 要再修正 | なし（T は開いたまま。表に「未解決のまま: B 要再修正」） |
| 同一スレッドで全件 OK | T の A = 解消、B = 反論受理 | T の根コメント ID で 1 回 |
| 別スレッドで混在 | スレッド T1 の A = 解消、スレッド T2 の B = 要再修正 | T1 の根コメント ID で 1 回だけ。T2 は開いたまま |
| 一部受け入れ・再反論 | T の A = 一部受け入れ（または再反論） | なし |

**実行**: 解決対象のスレッドごとに **1 回だけ**、根コメント ID で実行する。

```bash
if out=$(kaji pr resolve-thread [pr_id] --comment [root_comment_id] 2>"$err_file"); then
  echo "[root_comment_id]: $(echo "$out" | jq -r .result)"   # resolved / already_resolved
else
  echo "[root_comment_id]: 失敗 (rc=$?): $(head -n 3 "$err_file")"
fi
```

- 成功時の stdout は JSON 1 行（`result` が `resolved` = 今回解決 / `already_resolved` = 解決済みで書き込みなし）。
- 失敗時（rc≠0。2 = コメントが PR のスレッドに無い、3 = 権限不足・API 失敗等）: verdict は変えない。
  根コメント ID と stderr の要旨を控え、Step 4 の表と Step 5 の完了報告に転記する。再試行やループはしない。

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

### レビュースレッドの解決

| 根 comment ID | 含まれる指摘と判定 | 結果 |
|---------------|--------------------|------|
| (ID) | (項目1: ✅ OK) | resolved / already_resolved / 失敗: (理由) / 未解決のまま: (残っている指摘と判定) |

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

### レビュースレッドの解決

| 根 comment ID | 含まれる指摘と判定 | 結果 |
|---------------|--------------------|------|
| (ID) | (項目1: ✅ OK) | resolved / already_resolved / 失敗: (理由) |
| (ID) | (項目2: ❌ 要再修正) | 未解決のまま: (残っている指摘と判定) |

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

### 解決できなかったスレッド

| 根 comment ID | 理由 |
|---------------|------|
| (ID) | (resolve-thread の失敗理由。stderr の要旨) |

（失敗が無ければ「なし」と書く。開いたままにしたスレッドはここに含めない）

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

スレッド解決の成否は status に影響しない。status は修正確認の結果だけで決める
（解決できなかったスレッドは Step 4 の確認結果コメントと Step 5 の完了報告に残し、workflow は進める）。
