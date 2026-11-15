---
name: watch-triage
description: kaji watch が検知した停止 event（aborted / pane_idle / log_stall）の原因を読み、Issue に報告して保守者に回す判断用 skill。kaji watch が Herdr の watch-triage-<issue> タブで自動起動する。保守者が承認した復旧だけを実行する。
---

# Watch Triage

`kaji watch` が検知した event を 1 件だけ扱う。原因を調べて Issue に報告し、`kaji watch record` で
保守者に回し、pane を開いたまま保守者の指示を待つ。CLI と JSON の正本は
[`docs/cli-guides/watch.md`](../../../docs/cli-guides/watch.md)。

## 入力

```
/watch-triage <event-file>
```

`<event-file>` は `<artifacts>/_watch/events/` の下の event ファイル（schema v1）。

## 絶対規則

1. **agent の質問には答えない**。step の agent が質問で止まっていても、返答して作業を戻さない
   （答えて戻すのは #545 の範囲）。質問の内容と、
   [`critical-decision-checklist.md`](../_shared/critical-decision-checklist.md) の 3 分類による見立てを報告に含める。
2. **状態の判定は event の `status_document` を正とする**。artifact の本文は原因の調査のために読むだけにする。
3. **パスを組み立てない**。run の artifact は event の `artifacts.run_dir` / `artifacts.attempt_dir` だけを使う。
   `artifacts` が null なら `kaji status --json <issue>` の再取得だけで報告し、artifact が見つからなかったことを書く。
4. 失敗した step のやり直しは `kaji recover <workflow> <issue> --retry-failed-step` **だけ**を使い、
   保守者がこの pane で承認した場合に限る。
5. 下の禁止リストの操作は、保守者がこの pane で明示的に承認したとき（「この操作を実行してよい」という返答）だけ行う。

<!-- watch-triage-forbidden:start -->
| ID | 禁止操作 |
|---|---|
| merge | PR の merge |
| force-push | force push |
| rebase | rebase |
| close | Issue / PR の close |
| labels | ラベルの付与・削除 |
| issue-body | Issue 本文の編集 |
| kaji-state | kaji の state（run の artifact・series の state・watch の状態ファイル）の削除・書き換え |
| series-yaml | series YAML の作成・変更 |
| code | コードの修正・commit |
| model-switch | モデル・agent の切り替え |
<!-- watch-triage-forbidden:end -->

## 手順

### 1. 読む

- event ファイル（`kind` / `issue` / `run_id` / `step` / `attempt` / `failed_step` / `failure` / `pane` / `artifacts` / `status_document`）
- Issue 本文（特に `## 決定事項`）: `kaji issue view <issue> --json body -q .body`
- 最新の事実: `kaji status --json <issue>`
- `artifacts.attempt_dir` の下の `prompt.txt` とログ、`artifacts.run_dir` の下の `run.log`（あるものだけ）
- `kind` が `pane_idle` / `log_stall` のとき: `herdr pane read <pane.pane_id> --source recent --lines 200`

### 2. 原因を分類する

`failure.cause` / `failure.recoverability_hint`、ログの末尾、pane の表示から、次のどれかに分類する:
一過性（usage limit・ネットワーク等）/ agent の質問待ち / step の不具合 / 設計・要件の問題 / kaji 本体の欠陥 / 不明。

`pane_idle` で agent が作業中だった場合は `still-working`、`log_stall` で進んでいた場合は `not-stalled` を記録して
（手順 4 の `--outcome` を替える）、自分のタブを閉じて終了する。

### 3. 報告コメント

1 行目を `<!-- kaji-watch-triage: key=<event key> -->` にしたコメントを投稿し、URL を控える。
本文には、分類・根拠（ログの該当行）・推奨する復旧（コマンドを含む）・agent の質問があればその内容と 3 分類の見立てを書く。

```bash
kaji issue comment <issue> --body-file "[skill_tmp_dir]/watch-triage-report.md"
```

kaji 本体の欠陥と判断した場合は、incident Issue の下書きを `[skill_tmp_dir]/incident-draft.md` に作るだけで、起票しない。

### 4. 保守者に回す

```bash
kaji watch record --event <event-file> --outcome handoff --comment-url <url> --summary "<1 行の要約>"
```

通知はこの record が送る。pane を開いたまま、保守者の指示を待つ。

### 5. 承認された復旧だけを実行する

保守者が承認した場合だけ、次を実行する。

```bash
kaji recover <workflow> <issue> --retry-failed-step
kaji watch record --event <event-file> --outcome recovered --summary "<実行した復旧>"
kaji watch start
```

`kaji recover` が exit 2 / 3 / 4 で拒否した場合は、その `retry-gate:` 行を保守者に示して指示を待つ。
