---
description: イシュー完了時に使用。PRマージ・worktree削除・ブランチ安全削除を一括実行
name: issue-close
---

# Issue Close

イシュー対応完了後のクリーンアップを実行します。
PR マージ、worktree 削除、ブランチ削除、Issue クローズを一括実行します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| PRがApproveされマージ可能 | ✅ 使用 |
| PRレビュー待ち | ❌ 待機 |
| 作業途中 | ❌ 不要 |

**ワークフロー内の位置**: implement → review-code → i-dev-final-check → i-pr → **close**

## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値または local ID） |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`、local では bare ID） |
| `step_id` | str | 現在のステップ ID |

**provider 解決時に追加で注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `provider_type` | str | `github` / `local` のいずれか。本 Skill の経路分岐に使用 |
| `default_branch` | str | ベースブランチ名（`main` 等）。local 経路で merge / push の引数に使用 |
| `branch_name` | str | フィーチャーブランチ名（`feat/<id>` 等） |
| `worktree_dir` | str | worktree 絶対パス |
| `verdict_path` | str | harness 経由でのみ注入される、この step の `verdict.yaml` 絶対パス。Step 2.5（review-poll 承認証跡の照合）の適用判定に使う |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue_id>
```

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_id` として使用。

`issue_ref` はハーネス経由ではプロンプトに自動注入される（`prompt.py` 側で provider 別に整形）。手動実行時は `issue_id` から導出する: GitHub 数値 ID なら `#<issue_id>`、`local-*` 形式なら bare ID（`#` を付けない）。

## 前提条件

- `/i-pr` でPRが作成済みであること
- Merge commit方式を使用（ブランチ履歴を保持）

## 実行手順

`[provider_type]` に応じて手順が分岐する。

- `[provider_type]` が `github`（または未注入の legacy 環境）→ 既存の
  Step 1〜6 を順に実行する（`kaji pr merge` / worktree 削除 / branch 削除 /
  `git pull` / `kaji issue close` / 報告）。
- `[provider_type]` が `local` → 後述の **provider=local の場合** セクションへ
  ジャンプし、design.md § local mode における /issue-close の手順 (6 step) を
  実行する。github 用の Step 1〜6 は実行しない（PR 概念が無いため）。

## 共通: ワークフロー完了後の確認項目の移管

親 Issue を close する直前に、provider に関係なく本手順を実行する。
正本は
[`docs/dev/workflow_completion_criteria.md`](../../../docs/dev/workflow_completion_criteria.md)
§ follow-up Issue への移管、本文 template は
[`templates/follow-up-issue.md`](templates/follow-up-issue.md) とする。

### 1. 未完了項目の抽出

親本文の `## 完了条件` 内にある末尾サブセクション
`### ワークフロー完了後の確認項目` だけを対象にし、次の `## ` 見出しまたは本文末尾までから
`- [ ]` の項目を抽出する。

| 状態 | 処理 |
|------|------|
| セクションなし | follow-up なしで close 手順を続行 |
| `- なし`、または未チェック項目 0 件 | follow-up なしで close 手順を続行 |
| 未チェック項目 1 件以上 | 以降の検索・作成・マーカー追記を実行 |
| 見出し重複、チェックリストとして解釈不能 | 項目喪失を避けるため `ABORT` |

`i-dev-final-check` / `i-doc-final-check` が同サブセクションを更新対象外としているため、
ここに残る `[ ]` は未完了のまま follow-up へ移す。`[x]` は移さない。

### 2. 親マーカーと既存 Issue の確認

follow-up のタイトルは次の完全一致形式に固定する。

```text
[follow-up] [parent_title] ([issue_ref])
```

まず親本文に次のマーカーがあるか確認する。

```text
<!-- kaji-follow-up-issue: [follow_up_issue_id] -->
```

- マーカーが 1 件ある場合: 値を `FOLLOW_UP_ID` に設定して `kaji issue view` で確認し、
  次の 3 点をすべて満たすときだけ再利用する
  1. `state` が `open` である
  2. title が完全一致形式である
  3. 本文に `<!-- kaji-follow-up-parent: [issue_ref] -->` が 1 件ある
- マーカーが複数、値が空、参照先が存在しない場合: `ABORT`
- マーカーの参照先が close 済みの場合: `ABORT`。未チェック項目が親に残っているのに
  追跡先が閉じている状態であり、close 済み follow-up を再利用すると追跡が失われる。
  同名タイトルの open Issue 検索へフォールバックしてもマーカーとの不整合が残るため、
  自動復旧はしない。人間が (a) 完了済みの項目を親本文で `[x]` に更新する、
  (b) follow-up Issue を reopen する、(c) 親のマーカー行を削除して再作成させる、
  のいずれかを選んだうえで再実行する
- マーカーがない場合: open Issue を完全一致タイトルで検索する

```bash
FOLLOW_UP_STATE=$(kaji issue view "$FOLLOW_UP_ID" --json state -q '.state')
# GitHub は OPEN/CLOSED、local は open/closed を返すため小文字化して比較する
case "$(printf '%s' "$FOLLOW_UP_STATE" | tr '[:upper:]' '[:lower:]')" in
  open) : ;;
  *)
    echo "ABORT: marked follow-up Issue $FOLLOW_UP_ID is not open (state=$FOLLOW_UP_STATE)"
    exit 1
    ;;
esac
```

```bash
PARENT_TITLE=$(kaji issue view [issue_id] --json title -q '.title')
FOLLOW_UP_TITLE="[follow-up] $PARENT_TITLE ([issue_ref])"
kaji issue list --state open --limit 1000 --json number,title > /tmp/kaji-open-issues.json
jq -r --arg title "$FOLLOW_UP_TITLE" \
  '.[] | select(.title == $title) | .number' \
  /tmp/kaji-open-issues.json > /tmp/kaji-follow-up-candidates.txt
```

| 完全一致件数 | 処理 |
|-------------|------|
| 0 | 新規作成 |
| 1 | 候補の `.number` を `FOLLOW_UP_ID` に設定し、既存 open Issue を再利用 |
| 2 以上 | 対象を一意に決められないため `ABORT` |

作成後にマーカー追記だけが失敗した場合も、再実行時は完全一致検索で作成済み Issue を
再利用する。これにより重複起票を防ぐ。

### 3. follow-up Issue の作成

完全一致候補がない場合だけ、`templates/follow-up-issue.md` を読み、以下を実値へ置換して
`/tmp/kaji-follow-up-body.md` を作る。

- `[parent_issue_ref]` / `[parent_issue_title]`
- `[unchecked_items]`: 抽出した未チェック項目だけ
- `[reference_docs]`: 親本文の `docs/...` 参照。なければ正本
  `docs/dev/workflow_completion_criteria.md`

```bash
CREATE_OUTPUT=$(kaji issue create \
  --title "$FOLLOW_UP_TITLE" \
  --body-file /tmp/kaji-follow-up-body.md) || {
    echo "ABORT: follow-up Issue creation failed"
    exit 1
  }
FOLLOW_UP_ID=${CREATE_OUTPUT##*/}

# local provider では create した issue.md を atomic commit する。
# github provider では --commit が除去されるため同じコマンドを使える。
kaji issue edit "$FOLLOW_UP_ID" --commit \
  --body-file /tmp/kaji-follow-up-body.md || {
    echo "ABORT: follow-up Issue persistence failed"
    exit 1
  }
```

### 4. 親本文へのマーカー追記

親本文に有効なマーカーがなかった場合だけ、新規作成・既存検索で得た Issue ID の
マーカーを親本文末尾へ 1 件追記する。Step 2 で有効なマーカーを確認済みの場合は追記を
スキップし、既存の 1 件を維持する。

```bash
CURRENT_BODY=$(kaji issue view [issue_id] --json body -q '.body')
{
  printf '%s\n\n' "$CURRENT_BODY"
  printf '<!-- kaji-follow-up-issue: %s -->\n' "$FOLLOW_UP_ID"
} > /tmp/kaji-parent-body-with-follow-up.md

kaji issue edit [issue_id] --commit \
  --body-file /tmp/kaji-parent-body-with-follow-up.md || {
    echo "ABORT: parent follow-up marker update failed; parent Issue remains open"
    exit 1
  }
```

追記後、`kaji issue view` で同じマーカーが 1 件だけ存在することを確認する。作成・再利用、
マーカー追記、追記後確認のいずれかが失敗した場合は `ABORT` とし、親 Issue の close を
実行しない。

> **結果を記録**: `follow_up_result` = 「対象なし」/「作成: [follow_up_issue_id]」/
> 「既存再利用: [follow_up_issue_id]」。完了報告に含める。

### provider=github の場合

### Step 1: Worktree情報の取得

Issue本文からWorktree情報を取得します:

```bash
kaji issue view [issue_id] --json body -q '.body'
```

以下の情報を抽出:
- `> **Worktree**: \`../kaji-[prefix]-[issue_id]\`` → worktree パス
- `> **Branch**: \`[prefix]/[issue_id]\`` → ブランチ名

### Step 2: メインリポジトリのパスを特定

`git worktree list` の最初の行が常に main worktree（bare repository のルート）を示す:

```bash
MAIN_REPO=$(git worktree list | head -1 | awk '{print $1}')
```

> **注意**: `git rev-parse --show-toplevel` は現在の worktree のルートを返すため、
> worktree 内から実行すると main repo を取得できない。必ず `git worktree list` を使うこと。

worktree 内にいる場合は main repo に移動:

```bash
cd "$MAIN_REPO"
```

### Step 2.5: review-poll 承認証跡の照合

review-poll が PASS を返した run では、merge の前に「どの PR のどの full SHA が承認されたか」を
構造化証跡と照合する。**Issue の verdict marker の有無は、この Step のどの段階でも判定に使わない**
（review-poll は Issue コメントを投稿しない）。

#### 2.5.1 適用判定

プロンプトに `[verdict_path]` がある場合に限り、次を実行する。`[verdict_path]` がない手動起動は
照合対象外（人間が承認者）で、Step 3 へ進む。

```bash
RESOLVED=$(kaji issue resolve-verdict [issue_id] --step review-poll --current-verdict-path [verdict_path])
RESOLVE_EXIT=$?
```

| 結果 | 扱い |
|------|------|
| exit 0 かつ `.source == "artifact"` かつ `.status == "PASS"` | **照合対象**。2.5.2 へ |
| exit 0 で `.source == "artifact"` かつ `.status` が PASS 以外 | 照合対象外。承認は `review` / `pr-verify` 経路。Step 3 へ |
| exit 4（現在の run と recovery 復旧元に review-poll の attempt がない） | 照合対象外。Step 3 へ |
| exit 0 で `source` key がない（marker で解決された） | 矛盾。**ABORT** |
| 上記以外の非 0（5 / 7 など） | **ABORT**（fail-closed） |

exit code で分岐し、exit 0 の出力だけを `jq` で分類する（`marker` は review-poll が marker を投稿しない前提と
矛盾するので ABORT、`skip` は Step 3 へ、`verify` だけが照合対象）。

```bash
REVIEW_POLL_VERDICT_PATH=""
case "$RESOLVE_EXIT" in
  0)
    MODE=$(printf '%s' "$RESOLVED" | jq -r '
      if has("source") | not then "marker"
      elif .source == "artifact" and .status == "PASS" then "verify"
      else "skip" end') || { echo "ABORT: cannot classify resolve-verdict output"; exit 1; }
    REVIEW_POLL_VERDICT_PATH=$(printf '%s' "$RESOLVED" | jq -r '.verdict_path // empty')
    ;;
  4) MODE=skip ;;   # review-poll の attempt なし（人間の再開判断などは既存運用の範囲）
  *) echo "ABORT: kaji issue resolve-verdict failed (exit $RESOLVE_EXIT)"; exit 1 ;;
esac
```

`MODE` に応じた分岐（bash の `case` で明示する）:

```bash
case "$MODE" in
  marker) echo "ABORT: review-poll verdict resolved from an Issue marker (unexpected)"; exit 1 ;;
  skip)   : ;;   # 照合対象外。2.5.2〜2.5.5 を実行せず Step 3 へ進む
  verify) : ;;   # 2.5.2 以降を実行する
  *)      echo "ABORT: unexpected resolve-verdict classification: $MODE"; exit 1 ;;
esac
```

以降の 2.5.2〜2.5.5 は **`MODE=verify` のときだけ** 実行する。`skip` のときは何もせず Step 3 へ進む。

#### 2.5.2 証跡の所在（`MODE=verify` のみ）

```bash
EVIDENCE="$(dirname "$REVIEW_POLL_VERDICT_PATH")/review-poll-evidence.json"
test -f "$EVIDENCE" || { echo "ABORT: review-poll evidence not found: $EVIDENCE"; exit 1; }
```

ファイルがなければ旧形式の artifact とみなして **ABORT** する。PR の現状から証跡を組み立てない（捏造しない）。
suggestion は「`kaji run <workflow> [issue_id] --from review-poll` で再確認する」。

#### 2.5.3 証跡の完全検証（`MODE=verify` のみ）

不変条件 I1〜I9（正本: `docs/ARCHITECTURE.md` の「review-poll の承認証跡」節、
producer は `kaji_harness/review_poll_evidence.py`）を 1 本の jq プログラムで検証する。
出力は、すべて満たせば `OK` の 1 語、違反があれば違反した不変条件 ID のカンマ区切りになる。
`OK` 以外（jq エラーや JSON 破損を含む）はすべて **ABORT** とし、違反した ID を報告する。

```bash
VERIFIED=$(jq -r '
  def whole: sub("\\.[0-9]+Z$"; "Z");
  def ists: type == "string"
    and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?Z$")
    and (whole as $w | ($w | fromdateiso8601 | todateiso8601) == $w);
  def ts: (whole | fromdateiso8601) * 1000000
    + ((capture("\\.(?<f>[0-9]+)Z$") // {f: "0"}) | .f + "000000" | .[0:6] | tonumber);
  def sec: (. / 1000000 | floor) * 1000000;
  def keys_eq($k): type == "object" and ((keys | sort) == ($k | sort));
  def posint: type == "number" and . == floor and . > 0;
  def str1: type == "string" and length > 0;
  def chk($id; f): if (try f catch false) == true then empty else $id end;
  . as $e
  | [
      chk("I1"; keys_eq(["schema_version","kind","result","provider","repository","pull_request","reviewed_head","approval","checks","fetched_at","decided_at"])
        and .schema_version == 1 and .kind == "kaji.review-poll.approval"
        and .result == "PASS" and .provider == "github"),
      chk("I1.sections";
        (.repository | keys_eq(["owner","name"]))
        and (.pull_request | keys_eq(["number","url"]))
        and (.reviewed_head | keys_eq(["sha","committed_at"]))
        and (.approval | keys_eq(["bot","reaction","review_summary"]))
        and (.approval.bot | keys_eq(["id","login"]))
        and (.approval.reaction | keys_eq(["id","content","created_at","api_path"]))
        and (.approval.review_summary | keys_eq(["comment_id","url","commit_short_sha","status","completed_at","comment_updated_at"]))
        and (.checks | keys_eq(["head_sha_at_start","head_sha_before_decision","head_sha_after_decision","short_sha_matching_pr_commits","current_head_bot_reviews"]))),
      chk("I2";
        (.repository.owner | str1) and (.repository.name | str1)
        and (.pull_request.number | posint)
        and (.pull_request.url | type == "string"
             and startswith("https://github.com/")
             and endswith("/pull/\($e.pull_request.number)"))),
      chk("I3";
        (.reviewed_head.sha | type == "string" and test("^[0-9a-f]{40}$"))
        and (.reviewed_head.committed_at | ists) and (.reviewed_head.committed_at | ts | . > 0)),
      chk("I4";
        .approval.bot.id == 199175422
        and (.approval.bot.login | type == "string" and startswith("chatgpt-codex-connector"))),
      chk("I5";
        (.approval.reaction.id | posint) and .approval.reaction.content == "+1"
        and (.approval.reaction.created_at | ists) and (.approval.reaction.created_at | ts | . > 0)
        and .approval.reaction.api_path
            == "repos/\($e.repository.owner)/\($e.repository.name)/issues/\($e.pull_request.number)/reactions"),
      chk("I6";
        (.approval.review_summary.comment_id | posint)
        and (.approval.review_summary.url | type == "string"
             and endswith("#issuecomment-\($e.approval.review_summary.comment_id)"))
        and .approval.review_summary.status == "Completed"
        and (.approval.review_summary.commit_short_sha | type == "string" and test("^[0-9a-f]{7,40}$"))
        and ($e.reviewed_head.sha | startswith($e.approval.review_summary.commit_short_sha))
        and (.approval.review_summary.completed_at | ists) and (.approval.review_summary.completed_at | ts | . > 0)
        and (.approval.review_summary.comment_updated_at | ists) and (.approval.review_summary.comment_updated_at | ts | . > 0)),
      chk("I7";
        (.approval.reaction.created_at | ts) >= ((.approval.review_summary.completed_at | ts | sec) + 1000000)
        and (.approval.reaction.created_at | ts) >= (.reviewed_head.committed_at | ts)
        and (.approval.review_summary.comment_updated_at | ts) >= (.approval.review_summary.completed_at | ts | sec)),
      chk("I8";
        ([.checks.head_sha_at_start, .checks.head_sha_before_decision, .checks.head_sha_after_decision]
          | all(. == $e.reviewed_head.sha))
        and .checks.short_sha_matching_pr_commits == 1 and .checks.current_head_bot_reviews == 0),
      chk("I9";
        (.fetched_at | ists) and (.decided_at | ists)
        and (.approval.reaction.created_at | ts) <= (.fetched_at | ts)
        and (.fetched_at | ts) <= (.decided_at | ts))
    ]
  | if length == 0 then "OK" else join(",") end
' "$EVIDENCE") || { echo "ABORT: review-poll evidence is unreadable or not valid JSON"; exit 1; }
[ "$VERIFIED" = "OK" ] || { echo "ABORT: review-poll evidence violates invariant(s): $VERIFIED"; exit 1; }
```

`ts` は小数秒を保持した epoch マイクロ秒を返す（`fromdateiso8601` は小数秒を受け付けないため、整数秒と小数部を別々に変換して合成する）。
`sec` は `floor(秒)` で、producer の `next_second_after` / `replace(microsecond=0)` に対応する。floor を使うのは
`completed_at` に対する比較（I7 の reaction・`comment_updated_at`）だけで、他の時刻比較（I7 の `committed_at`、I9）は小数秒まで比較する。
I7 は「reaction の秒が完了秒より厳密に後」であることを要求する。
`ists` は形式に加えて暦日・時分秒の実在を検証する（`fromdateiso8601 | todateiso8601` が元の秒部分に往復一致しなければ不正。`2026-02-30` 等を拒否する）。

#### 2.5.4 現在の PR との照合（`MODE=verify` のみ）

```bash
PR_NOW=$(kaji pr view [branch_name] --json number,headRefOid,url,state) \
  || { echo "ABORT: kaji pr view failed"; exit 1; }
jq -e -n --argjson pr "$PR_NOW" --slurpfile ev "$EVIDENCE" '
  $ev[0] as $e
  | $pr.number == $e.pull_request.number
    and $pr.url == $e.pull_request.url
    and $pr.headRefOid == $e.reviewed_head.sha
    and $pr.state == "OPEN"' >/dev/null \
  || { echo "ABORT: current PR does not match the approved evidence (number/url/head/state)"; exit 1; }
REVIEWED_SHA=$(jq -r '.reviewed_head.sha' "$EVIDENCE")
```

`url` の一致で owner / repo / 番号の照合を兼ねる。食い違えば **ABORT**。

#### 2.5.5 停止条件

2.5.1〜2.5.4 のいずれかで停止した場合、承認証跡を推測で補ったり、PR の現状から作り直したりしない。
`$REVIEWED_SHA` が設定された場合だけ、Step 3 は `--match-head-commit` 付きで実行する。

### Step 3: PRのマージ

Step 2.5 で照合対象になった場合（`$REVIEWED_SHA` が設定されている場合）:

```bash
kaji pr merge [branch_name] --match-head-commit "$REVIEWED_SHA"
```

照合の後に head が動いても GitHub が merge を拒否する。拒否された場合は **ABORT**（merge を再試行しない）。

照合対象外の場合（`review` / `pr-verify` 経路、`[verdict_path]` なしの手動起動）:

```bash
kaji pr merge [branch_name]
```

マージコミットを作成してブランチ履歴を保持する。ブランチ削除は worktree 削除後に Step 4.5 で行う。

> **結果を記録**: `pr_merge_result` = 「マージ済み」。この値は Step 6 で使用する。

### Step 4: worktree削除

```bash
git worktree remove "$MAIN_REPO/../kaji-[prefix]-[issue_id]"
```

> `$MAIN_REPO` は Step 2 で取得済み。

> **結果を記録**: `worktree_result` = 「削除済み」。この値は Step 6 で使用する。

### Step 4.5: ブランチ削除

worktree 削除後にローカル・リモートブランチを削除する。
`git fetch [git_remote]` で `[git_remote]/[default_branch]` を最新化してから、`merge-base --is-ancestor` でマージ済み判定を行い、安全に削除する。
ローカル削除とリモート削除は独立して実行し、片方の失敗がもう片方をブロックしない。
ブランチが既に存在しない場合はスキップする。

```bash
# 1. fetch して [git_remote]/[default_branch] を更新
git fetch [git_remote]

# 2. ローカルブランチ削除: 存在確認 → マージ済み判定 → 安全な -D
if git show-ref --verify --quiet refs/heads/[branch_name]; then
    if git merge-base --is-ancestor [branch_name] [git_remote]/[default_branch]; then
        git branch -D [branch_name]
    else
        echo "WARNING: branch not merged into [git_remote]/[default_branch], skipping local delete"
    fi
fi

# 3. リモートブランチ削除（ローカル削除の成否に依存しない）
git ls-remote --exit-code --heads [git_remote] [branch_name] >/dev/null 2>&1
LS_EXIT=$?
if [ "$LS_EXIT" -eq 0 ]; then
    if ! git push [git_remote] --delete [branch_name]; then
        echo "ERROR: git push [git_remote] --delete failed"
        exit 1
    fi
elif [ "$LS_EXIT" -eq 2 ]; then
    echo "INFO: remote branch already deleted"
else
    echo "ERROR: git ls-remote failed (exit $LS_EXIT)"
    exit 1
fi

# 4. stale remote-tracking ref を掃除
git fetch --prune [git_remote]
```

> **結果を記録**:
> - `local_branch_result` = 「削除済み」/「未存在を確認」/「未マージのためスキップ」
> - `remote_branch_result` = 「削除済み」/「未存在を確認」/「削除失敗（要手動対応）」
>
> これらの値は Step 6 で使用する。

### Step 5: mainを最新化

```bash
git pull [git_remote] [default_branch]
```

> **結果を記録**: `pull_result` = 「最新化済み」。この値は Step 6 で使用する。

### Step 5.4: ワークフロー完了後の確認項目を移管

「共通: ワークフロー完了後の確認項目の移管」を実行する。失敗時は `ABORT` とし、
Step 5.5 へ進まない。

### Step 5.5: Issue クローズ

```bash
kaji issue close [issue_id] --reason completed
```

> **結果を記録**: `close_result` = 「クローズ済み」/「クローズ失敗（要手動対応）」。この値は Step 6 で使用する。
>
> **重要**: `kaji issue close` が失敗した場合は verdict を **ABORT** にすること。Issue が未クローズのまま残ることは許容しない。

### Step 6: 完了報告

Step 3〜5.5 の結果を使って、**stdout への報告**と **Issue タイムラインへのコメント投稿**の両方を行う。

#### 6a. Issue コメント投稿

各ステップで記録した結果変数を使い、コメント内容を動的に組み立てて投稿する:

```bash
kaji issue comment [issue_id] --commit --body-file - <<'COMMENT_EOF'
## Issue クローズ完了

| 項目 | 状態 |
|------|------|
| PR | [pr_merge_result] |
| worktree | [worktree_result] |
| ローカルブランチ | [local_branch_result] |
| リモートブランチ | [remote_branch_result] |
| main | [pull_result] |
| follow-up Issue | [follow_up_result] |
| Issue | [close_result] |
COMMENT_EOF

```

> `[pr_merge_result]` 等のプレースホルダーは、実際の実行結果に置き換えること。ハードコードしない。

#### 6b. stdout 報告

以下の形式で報告してください:

```
## Issue クローズ完了

| 項目 | 状態 |
|------|------|
| Issue | [issue_ref] |
| PR | [pr_merge_result] |
| worktree | [worktree_result] |
| ローカルブランチ | [local_branch_result] |
| リモートブランチ | [remote_branch_result] |
| main | [pull_result] |
| follow-up Issue | [follow_up_result] |
| Issue 状態 | [close_result] |
```

### provider=local の場合

`[provider_type]` が `local` のとき、PR 概念が無いため
design.md § local mode における /issue-close の手順 (6 step) を実行する。

> **重要 (worktree 運用)**: bare repository + worktree パターンでは
> `[default_branch]` は **feature worktree とは別の worktree**（通常 main repo 側）
> で checkout されている。そのため merge / close commit は **base worktree 側
> で実行**し、feature worktree (`[worktree_dir]`) はその後で削除する。
> feature worktree 内で `git switch [default_branch]` を実行しても、別 worktree
> がそのブランチを保持しているため Git に拒否される。

#### Step 1: Preflight check（feature worktree で確認）

```bash
cd [worktree_dir]
test -z "$(git status --porcelain)" || { echo "ABORT: uncommitted changes in [worktree_dir]"; exit 1; }
git rev-parse --abbrev-ref HEAD | grep -qE "^[a-z]+/local-[a-z0-9]+-[0-9]+(-[a-z0-9-]+)?$" || { echo "ABORT: not on feature branch"; exit 1; }
```

未コミット変更 / feature ブランチ外なら ABORT。

#### Step 2: Base worktree を特定し、base branch を最新化

`git worktree list --porcelain` から `[default_branch]` を checkout している
worktree を抽出する。見つからなければ user が手動で base 側を準備する必要が
あるため ABORT。

```bash
# [default_branch] を checkout している worktree を取得
BASE_WT=$(git worktree list --porcelain | awk -v b="[default_branch]" '
    /^worktree / { wt=$2 }
    $0 == "branch refs/heads/" b { print wt; exit }
')
test -n "$BASE_WT" || { echo "ABORT: no worktree has [default_branch] checked out. Run 'git worktree add <path> [default_branch]' or 'git switch [default_branch]' in your main checkout first."; exit 1; }

cd "$BASE_WT"

# Step 2.1: 3 段ガードによる救済 commit 判定（Issue local-p1-16 B）
#
# 標準動線（各 skill での `kaji issue {comment,edit} --commit`）が機能していれば
# base worktree は clean。蓄積が残っている場合は LocalProvider 永続化由来の path
# のみ救済し、それ以外は ABORT する。
#
# 救済対象 (LocalProvider 命名規則):
#   - .kaji/issues/<issue_id>-<slug>/issue.md
#   - .kaji/issues/<issue_id>-<slug>/comments/<4桁seq>-<machine_id>.md
# 出典: kaji_harness/providers/local.py:586 / providers/context.py:17 / providers/local.py:36
DIRTY=$(git status --porcelain)
if [ -n "$DIRTY" ]; then
    # 条件 1: dirty path がすべて [issue_id] の永続化 whitelist に一致するか検査
    ISSUE_DIR_RE='^\.kaji/issues/[issue_id]-[a-z0-9-]+/(issue\.md|comments/[0-9]{4}-[a-z0-9]{1,16}\.md)$'
    UNRELATED=$(printf '%s\n' "$DIRTY" | awk -v re="$ISSUE_DIR_RE" '
        {
            # rename / copy ("R  old -> new") は救済対象外として ABORT に倒す
            if (match($0, /->/)) { print; next }
            # 先頭 3 文字 (status + space) を除いた残りを path として扱う
            path = substr($0, 4)
            # quoted path (path に空白等あり) は対応外として ABORT
            if (substr(path, 1, 1) == "\"") { print; next }
            if (path !~ re) { print }
        }
    ')
    if [ -n "$UNRELATED" ]; then
        echo "ABORT: dirty files outside LocalProvider persistence whitelist in base worktree $BASE_WT:"
        printf '%s\n' "$UNRELATED"
        echo "  Allowed pattern: $ISSUE_DIR_RE"
        exit 1
    fi
    # 条件 2: whitelist 命名規則の glob で限定 add + atomic commit
    #   - `comments/` ディレクトリ全体を add してはならない (note.txt 等を巻き込む)
    #   - `git commit --only` で他の staged change を HEAD に混入させない
    git add \
        ".kaji/issues/[issue_id]-"*"/issue.md" \
        ".kaji/issues/[issue_id]-"*"/comments/"[0-9][0-9][0-9][0-9]-*.md \
        2>/dev/null || true
    git commit --only \
        -m "chore(local): salvage uncommitted issue files for [issue_ref]" \
        -- \
        ".kaji/issues/[issue_id]-"*"/issue.md" \
        ".kaji/issues/[issue_id]-"*"/comments/"[0-9][0-9][0-9][0-9]-*.md \
        || { echo "ABORT: salvage commit failed"; exit 1; }
    # 条件 3: 救済後の残差を再検証 (rename/copy/rm 等の取りこぼしを検出)
    test -z "$(git status --porcelain)" || {
        echo "ABORT: residual dirty files after salvage commit in base worktree $BASE_WT:"
        git status --porcelain
        exit 1
    }
fi

# remote 設定がある場合のみ fetch + ff-only merge。
# fetch 失敗 (network 断 / 認証エラー / suspended account 等) は WARNING で skip し、
# local-only で close を完結させる (Step 6 の push も同様に warning で続行する設計と整合)。
# `kaji run` 非対話モードでは AskUserQuestion 経由のリカバリ不可のため、
# deterministic に local fallback すること。手動 push は remote 復旧後に実施。
if git remote get-url [git_remote] >/dev/null 2>&1; then
    if git fetch [git_remote] [default_branch] 2>&1; then
        git merge --ff-only "[git_remote]/[default_branch]" || { echo "ABORT: ff-only merge failed in base worktree"; exit 1; }
    else
        echo "WARNING: git fetch [git_remote] [default_branch] failed; proceeding with local-only close (manual push needed after remote recovery)"
    fi
fi
```

ABORT 条件:
- fast-forward できない (ローカル [default_branch] が [git_remote]/[default_branch] から分岐) → resolve 後に再実行
- base worktree 側に LocalProvider 永続化 whitelist 外の dirty file が残存 → 手動コミット後に再実行

WARNING 継続条件:
- `git fetch` 失敗 (remote 到達不可 / 認証失敗) → local merge は実行、push は Step 6 で warning skip

標準動線で各 skill が `kaji issue {comment,edit} --commit` を使っていれば、ここまで到達した時点で
base worktree は clean のはず。救済 commit は標準動線が機能しなかった場合の安全装置として残す。

#### Step 3: Merge 実行（base worktree 上で）

```bash
git merge --no-ff --no-edit [branch_name] || { echo "ABORT: merge conflict, resolve manually in $BASE_WT then retry"; exit 1; }
```

衝突したら ABORT。Issue は open のまま、user が手動 resolve した後で再実行する。

#### Step 4: 事後確認の移管 + Issue frontmatter 更新 + commit（base worktree 上で）

最初に「共通: ワークフロー完了後の確認項目の移管」を実行する。follow-up 作成または
親マーカー追記に失敗した場合は `ABORT` とし、親 Issue を close しない。

移管成功後、親 Issue を close する。

```bash
kaji issue close [issue_id] --reason completed
git add .kaji/issues/[issue_id]-*/issue.md
git commit -m "chore(issue): close [issue_ref]" || { echo "ABORT: commit failed"; exit 1; }
```

`--reason completed` は明示で書く（`LocalProvider.close_issue` の default も
`completed` だが、Skill markdown 上で明示することで読み手の予期外を減らす）。

**Step 4 完了で Issue close は確定**。以降の失敗は警告のみ。

#### Step 5: Cleanup（base worktree から feature worktree を削除）

base worktree に居る状態で feature worktree を削除する。`cwd == 削除対象` を
回避するため、Step 2 の `cd "$BASE_WT"` は維持したまま実行する。

```bash
git worktree remove [worktree_dir] || echo "WARNING: worktree remove failed for [worktree_dir]; manual cleanup needed"
git branch -d [branch_name] || echo "WARNING: branch delete failed for [branch_name]; manual cleanup needed"
```

#### Step 6: Push（remote 設定がある場合、base worktree から）

```bash
if git remote get-url [git_remote] >/dev/null 2>&1; then
    git push [git_remote] [default_branch] || echo "WARNING: push failed; manual push needed"
fi
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

```
---VERDICT---
status: PASS
reason: |
  クローズ完了
evidence: |
  事後確認の移管要否を確認し、必要な follow-up Issue の作成または再利用と親マーカー追記を完了した。PR マージ・worktree 削除・main 最新化・Issue クローズ済み
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | クローズ完了 |
| ABORT | review-poll 承認証跡の欠落・不変条件違反・現在の PR との不一致・`--match-head-commit` による merge 拒否、follow-up 作成・再利用・親マーカー追記の失敗（マーカー参照先が close 済みの場合を含む）、またはクローズ失敗（`kaji issue close` 失敗を含む / local merge 衝突） |
