---
description: イシュー完了時に使用。PRマージ・worktree削除・ブランチ安全削除・Issue の一時作業ディレクトリ削除を一括実行
name: issue-close
---

# Issue Close

イシュー対応完了後のクリーンアップを実行します。
PR マージ、worktree 削除、ブランチ削除、Issue クローズ、Issue の一時作業ディレクトリ削除を一括実行します。

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
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値） |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`） |
| `step_id` | str | 現在のステップ ID |

**provider 解決時に追加で注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `provider_type` | str | `github`（現行唯一の provider） |
| `default_branch` | str | ベースブランチ名（`main` 等）。branch 削除判定と `git pull` の引数に使用 |
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

`issue_ref` はハーネス経由ではプロンプトに自動注入される（`prompt.py` 側で provider 別に整形）。手動実行時は `issue_id` から `#<issue_id>` を導出する。

## 前提条件

- `/i-pr` でPRが作成済みであること
- Merge commit方式を使用（ブランチ履歴を保持）

## 実行手順

Step 1〜6 を順に実行する（`kaji pr merge` / worktree 削除 / branch 削除 /
`git pull` / `kaji issue close` / `tmp/kaji/[issue_id]/` 削除 / 報告）。

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
# 大文字小文字の揺れに依存しないよう小文字化して比較する
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
`/tmp/kaji-follow-up-body.md` を作る。テンプレートはファイル全体がそのまま follow-up 本文であり
（1 行目は `<!-- kaji-follow-up-parent: [parent_issue_ref] -->`）、placeholder 以外の置換・追記・削除をしない。
見出しや説明文、コードフェンスを本文に加えない。

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

# github provider では --commit は無視される（CLI 互換のため付与を維持）。
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
受け付ける版は `schema_version: 2`（`codex-review-state:v1` で承認した証跡、Issue #473）だけで、
`2` 以外は I1 違反（Issue #478）。
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
  def sha40: type == "string" and test("^[0-9a-f]{40}$");
  def chk($id; f): if (try f catch false) == true then empty else $id end;
  . as $e
  | def top_keys: keys_eq(["schema_version","kind","result","provider","repository","pull_request","reviewed_head","approval","checks","fetched_at","decided_at"])
      and .kind == "kaji.review-poll.approval" and .result == "PASS" and .provider == "github";
    def common_sections:
      (.repository | keys_eq(["owner","name"]))
      and (.pull_request | keys_eq(["number","url"]))
      and (.reviewed_head | keys_eq(["sha","committed_at"]))
      and (.approval.bot | keys_eq(["id","login"]))
      and (.approval.reaction | keys_eq(["id","content","created_at","api_path"]));
    def common_checks: [
      chk("I2";
        (.repository.owner | str1) and (.repository.name | str1)
        and (.pull_request.number | posint)
        and (.pull_request.url | type == "string"
             and startswith("https://github.com/")
             and endswith("/pull/\($e.pull_request.number)"))),
      chk("I3";
        (.reviewed_head.sha | sha40)
        and (.reviewed_head.committed_at | ists) and (.reviewed_head.committed_at | ts | . > 0)),
      chk("I4";
        .approval.bot.id == 199175422
        and (.approval.bot.login | type == "string" and startswith("chatgpt-codex-connector"))),
      chk("I5";
        (.approval.reaction.id | posint) and .approval.reaction.content == "+1"
        and (.approval.reaction.created_at | ists) and (.approval.reaction.created_at | ts | . > 0)
        and .approval.reaction.api_path
            == "repos/\($e.repository.owner)/\($e.repository.name)/issues/\($e.pull_request.number)/reactions"),
      chk("I9";
        (.fetched_at | ists) and (.decided_at | ists)
        and (.approval.reaction.created_at | ts) <= (.fetched_at | ts)
        and (.fetched_at | ts) <= (.decided_at | ts))
    ];
    def head_checks:
      [.checks.head_sha_at_start, .checks.head_sha_before_decision, .checks.head_sha_after_decision]
        | all(. == $e.reviewed_head.sha);
    def entry_ok: keys_eq(["commit_sha","status","has_any_findings"])
      and (.commit_sha == null or (.commit_sha | sha40))
      and (.status == null or (.status | str1))
      and (.has_any_findings == null or (.has_any_findings | type == "boolean"));
    (try .schema_version catch null) as $v
  | if $v == 2 then [
      chk("I1"; top_keys and .schema_version == 2),
      chk("I1.sections";
        common_sections
        and (.approval | keys_eq(["bot","reaction","review_state"]))
        and (.approval.review_state | keys_eq(["comment_id","url","comment_updated_at","state_version","code_review","security_review"]))
        and (.approval.review_state.code_review | entry_ok)
        and (.approval.review_state.security_review | entry_ok)
        and (.checks | keys_eq(["head_sha_at_start","head_sha_before_decision","head_sha_after_decision","current_head_bot_reviews"]))),
      common_checks[],
      chk("I6";
        (.approval.review_state.comment_id | posint)
        and (.approval.review_state.url | type == "string"
             and endswith("#issuecomment-\($e.approval.review_state.comment_id)"))
        and .approval.review_state.state_version == "v1"
        and (.approval.review_state.comment_updated_at | ists) and (.approval.review_state.comment_updated_at | ts | . > 0)
        and (.approval.review_state.code_review
             | .commit_sha == $e.reviewed_head.sha and .status == "completed" and .has_any_findings == false)
        and (.approval.review_state.security_review
             | .status == null
               or (.commit_sha != null and .commit_sha != $e.reviewed_head.sha)
               or (.status == "completed" and .has_any_findings == false
                   and .commit_sha == $e.reviewed_head.sha))),
      chk("I7";
        (.approval.reaction.created_at | ts) >= ((.approval.review_state.comment_updated_at | ts | sec) + 1000000)
        and (.approval.reaction.created_at | ts) >= (.reviewed_head.committed_at | ts)),
      chk("I8"; head_checks and .checks.current_head_bot_reviews == 0)
    ]
    else ["I1"] end
  | if length == 0 then "OK" else join(",") end
' "$EVIDENCE") || { echo "ABORT: review-poll evidence is unreadable or not valid JSON"; exit 1; }
[ "$VERIFIED" = "OK" ] || { echo "ABORT: review-poll evidence violates invariant(s): $VERIFIED"; exit 1; }
```

`ts` は小数秒を保持した epoch マイクロ秒を返す（`fromdateiso8601` は小数秒を受け付けないため、整数秒と小数部を別々に変換して合成する）。
`sec` は `floor(秒)` で、producer の `next_second_after` に対応する。floor を使うのは
`review_state.comment_updated_at` に対する比較（I7 の reaction）だけで、他の時刻比較（I7 の `committed_at`、I9）は小数秒まで比較する。
I7 は「reaction の秒が state を含む summary comment の更新秒より厳密に後」を要求する。
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

### Step 5.6: Issue の一時作業ディレクトリ削除

Step 5.5 で Issue をクローズできた場合だけ、その Issue の一時作業ディレクトリ
`<main checkout>/tmp/kaji/[issue_id]/`（runner が attempt ごとに作る `KAJI_TMP_DIR` の親。
正本: [`docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md) § attempt 固有の一時作業ディレクトリ）を削除する。
Step 5.5 までに ABORT した場合は実行しない（調査用に残す）。

- 基点は `git worktree list --porcelain` のうち `branch refs/heads/[default_branch]` を持つ checkout
  （ちょうど 1 件）。Step 2 の `$MAIN_REPO` は使わない（bare 構成では `.bare` になるため）
- harness 経由では、実行中の close step 自身の `$KAJI_TMP_DIR` と、Issue dir からそこへ至る親 dir を残し、
  それ以外を削除する。手動起動（`KAJI_TMP_DIR` 未設定）では Issue dir ごと削除する
- 安全条件を満たさない場合は何も削除しない: `issue_id` が数字以外、default branch の checkout を 1 件に
  特定できない、`tmp` / `tmp/kaji` / `tmp/kaji/[issue_id]` のいずれかが symlink または dir でない、
  `KAJI_TMP_DIR` が設定済みなのに解決できない・Issue dir 自身またはその祖先を指す、harness 経由なのに
  `KAJI_TMP_DIR` が未設定、削除を始める前に辿る dir を列挙できない（読取権限がない等）
- 一部を削除した後で辿る dir を列挙できなくなった場合は、その dir を一部削除失敗として報告する
- 削除の失敗・スキップで close 全体を **ABORT にしない**。結果を記録して Step 6 へ進む

ブロックの冒頭 3 行のプレースホルダーを実値に置き換えて実行する。`[close_via_harness]` は、プロンプトに
`[verdict_path]` がある（Step 2.5.1 と同じ判定）なら `1`、ない手動起動なら `0` にする。
cwd は main checkout など repo 内の既存 dir にする（Step 4 で削除した worktree 内では git が失敗し、
スキップになる）。本体は POSIX sh で実行する（GNU / BSD 両対応。`realpath` / `readlink -f` を使わない）。

```sh
# kaji-issue-tmp-cleanup
ISSUE_ID='[issue_id]' \
DEFAULT_BRANCH='[default_branch]' \
CLOSE_VIA_HARNESS='[close_via_harness]' \
sh -s <<'KAJI_TMP_CLEANUP_EOF'
cleanup() {
  case $ISSUE_ID in
    '' | *[!0123456789]*) RESULT="スキップ（issue_id が数字でない: $ISSUE_ID）"; return ;;
  esac
  case $CLOSE_VIA_HARNESS in
    0 | 1) ;;
    *) RESULT="スキップ（CLOSE_VIA_HARNESS が 0 / 1 でない）"; return ;;
  esac
  MAINS=$(git worktree list --porcelain 2>/dev/null | awk -v ref="branch refs/heads/$DEFAULT_BRANCH" '
    /^worktree / { wt = substr($0, 10) }
    $0 == ref { print wt }')
  case $MAINS in
    '') RESULT="スキップ（$DEFAULT_BRANCH の checkout が見つからない）"; return ;;
    *"$NL"*) RESULT="スキップ（$DEFAULT_BRANCH の checkout が複数ある）"; return ;;
  esac
  # 基点は default branch checkout の物理パスだけを信頼し、下の各段は実 dir であることを要求する
  BASE=$(CDPATH= cd -P -- "$MAINS" 2>/dev/null && pwd -P) || {
    RESULT="スキップ（main checkout を解決できない: $MAINS）"; return
  }
  TARGET=$BASE/tmp/kaji/$ISSUE_ID
  if [ ! -e "$TARGET" ] && [ ! -L "$TARGET" ]; then RESULT="対象なし"; return; fi
  for p in "$BASE/tmp" "$BASE/tmp/kaji" "$TARGET"; do
    if [ -L "$p" ] || [ ! -d "$p" ]; then
      RESULT="スキップ（symlink または dir でない: $p）"; return
    fi
  done
  TARGET_REAL=$(CDPATH= cd -P -- "$TARGET" 2>/dev/null && pwd -P)
  if [ "$TARGET_REAL" != "$TARGET" ]; then
    RESULT="スキップ（物理パスが一致しない: $TARGET）"; return
  fi
  KEEP=""
  if [ -n "${KAJI_TMP_DIR+set}" ]; then
    KEEP=$( [ -n "$KAJI_TMP_DIR" ] && CDPATH= cd -P -- "$KAJI_TMP_DIR" 2>/dev/null && pwd -P) || {
      RESULT="スキップ（KAJI_TMP_DIR を解決できない: $KAJI_TMP_DIR）"; return
    }
    case $TARGET/ in
      "${KEEP%/}"/*) RESULT="スキップ（KAJI_TMP_DIR が Issue dir 自身または祖先: $KEEP）"; return ;;
    esac
    case $KEEP in
      "$TARGET"/*) ;;
      *) KEEP="" ;;   # Issue dir の外なので影響しない
    esac
  elif [ "$CLOSE_VIA_HARNESS" = 1 ]; then
    RESULT="スキップ（harness 経由なのに KAJI_TMP_DIR が未設定）"; return
  fi
  # KEEP の祖先を Issue dir から 1 段ずつ辿り、各段で次の祖先以外の子を消す
  FAILED=""
  REMOVED=""
  DIR=$TARGET
  while :; do
    NEXT=""
    if [ -n "$KEEP" ]; then
      REST=${KEEP#"$DIR"/}
      NEXT=$DIR/${REST%%/*}
    fi
    # 読めない dir では glob が展開されず子が見えないため、列挙できることを先に確かめる
    if ! ls -A -- "$DIR" >/dev/null 2>&1; then
      if [ -z "$REMOVED" ]; then RESULT="スキップ（列挙できない: $DIR）"; return; fi
      FAILED="$FAILED${FAILED:+, }$DIR"
      break
    fi
    for child in "$DIR"/* "$DIR"/.[!.]* "$DIR"/..?*; do
      if [ ! -e "$child" ] && [ ! -L "$child" ]; then continue; fi
      [ "$child" = "$NEXT" ] && continue
      REMOVED=1
      rm -rf -- "$child" || FAILED="$FAILED${FAILED:+, }$child"
    done
    if [ -z "$NEXT" ] || [ "$NEXT" = "$KEEP" ]; then break; fi
    DIR=$NEXT
  done
  if [ -z "$KEEP" ] && [ -z "$FAILED" ]; then
    rmdir -- "$TARGET" || FAILED=$TARGET
  fi
  if [ -n "$FAILED" ]; then
    RESULT="一部削除失敗（要手動対応: $FAILED）"
  elif [ -n "$KEEP" ]; then
    RESULT="削除済み（実行中 attempt dir を保持: $KEEP）"
  else
    RESULT="削除済み"
  fi
}
NL='
'
RESULT=""
cleanup
printf 'TMP_CLEANUP_RESULT=%s\n' "$RESULT"
exit 0
KAJI_TMP_CLEANUP_EOF
```

> **結果を記録**: `tmp_cleanup_result` = 出力された `TMP_CLEANUP_RESULT=` の値。この値は Step 6 で使用する。
>
> | 値 | 意味 |
> |----|------|
> | `削除済み` | Issue dir を丸ごと削除した（手動起動、または `KAJI_TMP_DIR` が Issue dir の外） |
> | `削除済み（実行中 attempt dir を保持: <path>）` | 実行中 attempt dir とその親 dir 以外を削除した |
> | `対象なし` | `tmp/kaji/[issue_id]` が存在しない |
> | `スキップ（<理由>）` | 安全条件を満たさず何も削除していない |
> | `一部削除失敗（要手動対応: <path>）` | 一部の削除に失敗した、または一部を削除した後に列挙できない dir があった。記載の path を手動で確認する |
>
> どの値でも verdict は変えない（ブロックは常に exit 0）。

### Step 6: 完了報告

Step 3〜5.6 の結果を使って、**stdout への報告**と **Issue タイムラインへのコメント投稿**の両方を行う。

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
| 一時ディレクトリ | [tmp_cleanup_result] |
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
| 一時ディレクトリ | [tmp_cleanup_result] |
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

```
---VERDICT---
status: PASS
reason: |
  クローズ完了
evidence: |
  事後確認の移管要否を確認し、必要な follow-up Issue の作成または再利用と親マーカー追記を完了した。PR マージ・worktree 削除・main 最新化・Issue クローズ済み。一時ディレクトリ: [tmp_cleanup_result]
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | クローズ完了 |
| ABORT | review-poll 承認証跡の欠落・不変条件違反・現在の PR との不一致・`--match-head-commit` による merge 拒否、follow-up 作成・再利用・親マーカー追記の失敗（マーカー参照先が close 済みの場合を含む）、またはクローズ失敗（`kaji issue close` 失敗を含む） |

Step 5.6 の一時ディレクトリ削除の結果（スキップ・一部削除失敗を含む）は status に影響しない。
