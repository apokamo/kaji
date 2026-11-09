---
description: review-ready の RETRY 指摘に基づき Issue 本文を修正する。
name: issue-fix-ready
---

# Issue Fix Ready

`issue-review-ready` が RETRY を返した指摘事項に基づき、Issue 本文を修正する。
指摘を盲目的に受け入れるのではなく、妥当性を検討し、修正と反論を使い分ける。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-review-ready` で RETRY 後 | ✅ 必須 |
| PASS 済みの Issue | ❌ 不要 |

**ワークフロー内の位置**: create → review-ready → (**fix-ready** → review-ready) → start → ...

worktree 不要（メインリポジトリから実行可能）。

## 引数

```
$ARGUMENTS = <issue_id>
```

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_id` として使用。

`issue_ref` はハーネス経由ではプロンプトに自動注入される（`prompt.py` 側で provider 別に整形）。手動実行時は `issue_id` から `#<issue_id>` を導出する。

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: コンテキスト取得

1. **Issue 本文の取得**:
   ```bash
   kaji issue view [issue_id] --json title,body,labels --jq '{title: .title, body: .body, labels: [.labels[].name]}'
   ```

2. **レビュー指摘の取得（`step=review-ready` marker で特定する）**:

   review-ready の判定コメントは、1 行目の `<!-- kaji-verdict: step=review-ready status=<STATUS> -->`
   marker だけで特定する。見出しの文字列では探さない（見出しは PASS / RETRY / ABORT で共通であり、
   本文に引用されたコメントとも区別できないため）。手順は `kaji issue resolve-verdict` の
   `resolve_latest_verdict` と同じ停止契約にそろえる。

   - **候補**: 1 行目が `<!-- kaji-verdict: step=review-ready `（末尾の空白を含む）で始まるコメント。
     1 行目だけに合わせ（`split("\n")[0]`）、2 行目以降に引用された marker は拾わない。
     status の欠落・閉じの ` -->` の欠落・不正な meta があっても候補から外さない
   - **選択**: 候補のうち最新の 1 件だけを見る（最新の RETRY を探すのではない。後から出た PASS や
     不正な marker より前の古い RETRY を拾わないため）
   - **検証**: 最新候補の 1 行目全体を `kaji_harness/providers/markers.py` の `_VERDICT_MARKER_RE` と
     同じ文法で検証し、`parse_kaji_verdict_marker` と同じく meta キーの重複も不正とする。不正なら
     `malformed` とし、それより古い正常な marker には遡らない（fail-closed）

   取得・選択・検証・本文抽出は、1 回の `kaji issue view` の結果の中で行う（CLI を 2 回呼ぶと、
   その間に投稿されたコメントで検証対象と本文が食い違いうるため）。

   ```bash
   REVIEW_READY_JSON=$(set -o pipefail; kaji issue view [issue_id] --json comments | jq -c '
     [ .comments[]
       | select((.body | split("\n")[0]) | startswith("<!-- kaji-verdict: step=review-ready ")) ]
     | last
     | if . == null then {state: "not_found"}
       else (.body | split("\n")[0]) as $l
         | ($l | sub("^<!-- kaji-verdict: step=review-ready status=[A-Z0-9_]+"; "")
               | [scan(" (?<k>[a-z][a-z0-9_]*)=") | .[0]]) as $keys
         | if ($l | test("^<!-- kaji-verdict: step=review-ready status=(PASS|RETRY|ABORT|BACK|BACK_[A-Z0-9_]+)( [a-z][a-z0-9_]*=[A-Za-z0-9][A-Za-z0-9._/-]*)* -->$"))
              and (($keys | length) == ($keys | unique | length))
           then {state: "ok",
                 status: ($l | capture("status=(?<s>[A-Z0-9_]+)").s),
                 body: .body}
           else {state: "malformed", first_line: $l} end
       end
   ')
   REVIEW_READY_RC=$?
   if [ "$REVIEW_READY_RC" -ne 0 ] || [ -z "$REVIEW_READY_JSON" ] \
      || [ "$(printf '%s\n' "$REVIEW_READY_JSON" | wc -l)" -ne 1 ]; then
     REVIEW_READY_JSON="{\"state\":\"fetch_failed\",\"exit_code\":$REVIEW_READY_RC}"
   fi
   echo "$REVIEW_READY_JSON"
   ```

   取得・解析の成否は出力の有無ではなく終了コードで判定する。`set -o pipefail` により `kaji` と
   `jq` のどちらかが非 0 で終われば `REVIEW_READY_RC` が非 0 になる。そのときは、途中まで出力された
   JSON（例: `kaji` が正常な JSON を出したあと exit 1、jq が 1 件目を処理したあと 2 件目の不正入力で
   exit 5）が残っていても破棄し、`state: "fetch_failed"` に置き換える。出力が空、または 1 行の
   JSON でない場合も同じく `fetch_failed` とする。この判定ブロックを省略・改変して実行しない。

   結果に応じて次のように扱う。ABORT の場合は Issue 本文の修正・コメント投稿に進まず、理由を書いた
   `status: ABORT` の verdict ブロックを stdout に出して終了する（fail-loud）。

   | 結果 | 扱い |
   |------|------|
   | `state: "fetch_failed"` | ABORT。`kaji` / `jq` が非 0 で終了した、または出力が 1 行の JSON でない。コメント一覧を取得・解析できない。`exit_code` を理由に記載する（途中まで出力された内容は使わない） |
   | `state: "not_found"` | ABORT。`step=review-ready` marker 付きの判定コメントが無い（marker の無い旧形式の「## レディネスレビュー」コメントは入力にしない）。`/issue-review-ready [issue_id]` の再実行を案内する |
   | `state: "malformed"` | ABORT。最新の review-ready marker が不正。古い RETRY は使わない。`first_line` を理由に記載し、review-ready の再実行を案内する |
   | `state: "ok"` かつ `status` が `RETRY` 以外 | ABORT。直すべき RETRY が無い（最新の判定が PASS / ABORT 等） |
   | `state: "ok"` かつ `status` が `RETRY` | `body` の 2 行目以降から RETRY の指摘事項を抽出し、Step 2 へ進む |

### Step 2: 対応方針の検討

各指摘事項について **1つずつ** 検討する。

- **A: 修正する (Agree)**
  - 指摘が正しく、Issue 本文の改善につながる場合
  - 不足している 1 次情報を補う: 対象ファイルパス、`docs/` パス、関連 Issue/PR 番号、CLI 出力、ログ等
  - 主観的表現を客観的・検証可能な記述に置き換える

- **B: 反論する (Disagree)**
  - 指摘が誤解に基づいている場合
  - 現状の記述で review-ready の観点を実質的に満たしている場合
  - **必須**: 反論する場合は明確な論理的根拠を用意する

### Step 3: Issue 本文の修正

`kaji issue edit` で Issue 本文を更新する。

```bash
kaji issue edit [issue_id] --commit --body "[updated-body]"
```

**注意事項**:
- 既存の本文構造（概要・目的・完了条件）を維持する
- 指摘に対応する箇所のみ修正する。無関係なセクションは変更しない
- 1 次情報の追加では、推測で埋めず事実確認してから記載する

#### workflow 内判定可能性（観点 14）の修正

通常完了条件に workflow 外の確認が混在しているという指摘は、次の手順で修正する。

1. `docs/dev/workflow_completion_criteria.md` § workflow 内完了条件と事後確認の分離を読む
2. 各項目について、workflow を RETRY して環境非依存で同じ結果を得られるか判定する
3. No の項目だけを `## 完了条件` の末尾サブセクション
   `### ワークフロー完了後の確認項目` へ移す
4. Yes の項目が事後確認欄に誤って置かれていれば通常完了条件へ戻す
5. 事後確認がなければサブセクションを削除するか、チェックボックスではない `- なし` にする

項目の文言とチェック状態は移動時に維持する。通常完了条件と事後確認の両方へ複製しない。

### Step 4: Issue コメント投稿

修正内容と反論を Issue コメントに記録する。

**verdict マーカーの無条件付与（必須）**: 判定コメントには **常に** `--verdict-step fix-ready --verdict-status <STATUS>` を付ける。`<STATUS>` は本 skill が「Verdict 出力」で返す status（`PASS` / `ABORT`）に置き換える。CLI が body 1 行目に `<!-- kaji-verdict: step=fix-ready status=<STATUS> -->` を付け、`kaji issue resolve-verdict --step fix-ready` と runner の comment fallback はこの marker で発行元 step を特定する。PASS でも省略しない（条件付きの付与は禁止。[docs/dev/shared_skill_rules.md § verdict マーカー契約](../../../docs/dev/shared_skill_rules.md#verdict-マーカー契約producer-consumer)）。投稿前に判定を確定してから status を決める。

```bash
kaji issue comment [issue_id] --commit \
  --verdict-step fix-ready --verdict-status <STATUS> \
  --body-file - <<'EOF'
## レディネス指摘への対応報告

### 対応済み

- **[観点名]**: (指摘内容の要約)
  - 修正内容: (どう修正したか)

### 見送り・反論

- **[観点名]**: (指摘内容の要約)
  - 理由: (なぜ対応しなかったか。根拠となるロジック)

### 次のステップ

`/issue-review-ready [issue_id]` で再レビューを実施してください。
EOF
```

### Step 5: 完了報告

以下の形式で報告すること。

```
## レディネス指摘対応完了

| 項目 | 値 |
|------|-----|
| Issue | [issue_ref] |
| 対応済み | N 件 |
| 見送り | M 件 |

### 次のステップ

`/issue-review-ready [issue_id]` で再レビューを実施してください。
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること。

```
---VERDICT---
status: PASS
reason: |
  指摘対応完了
evidence: |
  全指摘事項に対応済み
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 修正完了 |
| ABORT | Issue 自体が不適切で修正不可能 |
