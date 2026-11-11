# Run status（`kaji status`）

[English](run-status.md)（英語正本）

`kaji status` は run と series の状態をローカルの artifact から出力する。監視ツールは kaji の内部ファイル
（`run.log`・`session-state.json`・`process.json`・`_series/*/state.json` など）を読む代わりにこれを呼ぶ。
JSON 出力（`--json`）は `schema_version` を持つ公開の契約で、既定の表の出力は人向け（契約にしない）。

```bash
kaji status 540 541                 # 表
kaji status --json 540 541          # JSON（schema v1）
kaji status --json --series lane-a  # series の状態と member の Issue ごとの項目
```

`kaji status` は読み取り専用。ファイル（artifact・worktree・git index）に書き込まず、GitHub API を呼ばず
（provider を構築しない）、Herdr も呼ばない。git は artifact の場所を `kaji run` と同じ規則で解決する
`git worktree list --porcelain` だけを呼ぶ（相対の `paths.artifacts_dir` は main worktree 基準のため、
feature worktree から実行しても同じ artifact を読む）。

## exit code

| code | 意味 |
|------|------|
| 0 | 出力した。run の失敗、artifact の破損・欠落、series の `state.json` の欠落も、出力の中で表し exit 0 |
| 2 | 引数の誤り（`<issue>` と `--series` の両方・どちらもなし、`<number>` / `gh:<number>` 以外の Issue ID、`^[a-z0-9][a-z0-9-]{0,63}$` に合わない series id）、または `.kaji/config.toml` が無い |
| 3 | 予期しないエラー |

## schema の互換性

`schema_version` は整数。任意の項目の追加では上げない（利用側は知らない項目を無視する）。項目の削除・
名前や型の変更・`null` の意味の変更・enum の値の追加と変更で上げる。出す version は常に 1 つで、
古い version を選んで出す手段は無い。

## JSON（schema v1）

項目・型・`null` の意味の正本は英語版の表（[run-status.md](run-status.md)）。要点:

- top-level: `schema_version`（1）/ `generated_at` / `series`（`--series` のときだけ object）/ `issues[]`
  （引数順。`--series` では member 順）
- `series`: `id` / `status`（`running` / `stopped` / `completed` \| null）/ `stop_reason` / `updated_at` /
  `members[]`（`issue` / `workflow` / `status`（`pending` / `running` / `interrupted` / `completed` /
  `failed`）/ `child_pid` / `run_id` / `exit_code` / `gate` / `started_at` / `finished_at`）/ `read_errors[]`。
  フィールド単位で検証し、通らないフィールドだけ null にして `read_errors` に入れる
- `IssueStatus`: `issue` / `latest_run`（RunStatus \| null）/ `any_run_alive` / `launching` / `read_errors[]`
  （`{"path", "error": "missing" | "unreadable" | "invalid", "message"}`、path は artifacts dir 相対）
- `RunStatus`: `run_id` / `workflow` / `started_at` / `process`（`pid` / `alive` / `basis`）/ `termination` /
  `end_status` / `ended_at` / `last_step`（`id` / `attempt` / `agent` / `session_id`）/ `failed_step` /
  `failure`（`cause` / `source` / `recoverability_hint`）/ `worktree_dir` / `branch_name` / `pane` / `recovery`

最新の run は `<issue>/runs/` の run_id の文字列順で最大のもの。呼び出し側の時計とは比較しない。

`termination`:

| run.log の `workflow_end` | `process.alive` | `termination` |
|---------------------------|-----------------|---------------|
| あり | 任意（true は終了後の triage・待機中） | `ended` |
| なし（run.log 可読） | true | `running` |
| なし（run.log 可読） | false | `vanished`（kill / OOM 等の推定） |
| なし（run.log 可読） | null | `unknown` |
| run.log 不可読 | true | `running` |
| run.log 不可読 | false / null | null（`read_errors` に記録） |

`vanished` の run は `failure.cause = "process_vanished"`（source `external`、hint `unknown`）で、
`failed_step` は開始して終了していない最後の step。

`last_step.session_id` はその run・その attempt の artifact だけから取る（attempt の `result.json` の
`session_id` → `resume:` step の `step_start.session_id` → null）。Issue 単位の `session-state.json` の
sessions は別の run の ID を含みうるため使わない。

`process.basis` の値（`no_record` / `record_unreadable` / `other_host` / `rebooted` / `pid_absent` /
`zombie` / `pid_verified` / `pid_reused` / `unverifiable` / `finished_by_log`）の意味は英語版の表を参照。

## 生存の記録

`kaji run` は provider で Issue を解決する前に launch record（`<artifacts>/<issue>/launches/run-<pid>-<ticks>.json`）
を書き、run dir ができたら `run_id` を設定し、終了時に削除する。`runs/<run_id>/process.json` も書き、こちらは
残す。`kaji recover --retry-failed-step` と `kaji run-series` は child の `kaji run` を起動する前に
`launcher-<pid>-<ticks>.json` を書く。kill されたプロセスが残した記録は、生存判定で「生きていない」と
判定されるため無害。`kaji run` は最初の launch record を書けないと、Issue を解決する前に exit 3 で止まる。
artifact をホスト間で共有する構成は対象外（別ホストの記録は `other_host` = 判定不能になる）。
