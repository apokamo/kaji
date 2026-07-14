# Failure Triage / Recovery CLI

Language: [English](failure-recovery.md) | 日本語

failure triage / 自動再開レイヤ（Issue #288）の CLI リファレンス。`provider.type = "github"` /
`"local"` の双方に適用され、triage コメントは有効な provider 経由で投稿される。

運用ルール（何が再開可能で何がそうでないか、なぜウェイトを置くのか）は
[workflow guide](../dev/workflow_guide.md) § failure triage と自動再開、config key は
[configuration reference](../reference/configuration.md) § `[execution]` を参照。

## いつ走るか

`kaji run` の workflow process が `ERROR`、または triage 対象の `ABORT` で終了したときに、原因を
分類して証跡を残す。**run directory 作成前の失敗**（config 探索 / workflow validation /
`IssueContext` 解決）は triage しない。根拠となる artifact が存在せず、根拠なしの Issue コメントは
何も投稿しないより悪いためである。

層は 2 つあり、責務は重ならない。

| 層 | 対象 | 時間スケール | 実装 |
|----|------|-------------|------|
| attempt retry | 1 step dispatch 内の transient CLI failure | 数十秒〜数分、in-process | `execute_cli()` |
| run recovery | workflow process の `ERROR` / triage 対象 `ABORT` 終端 | 固定 10 分ウェイト + 新規 `kaji run` | 本ドキュメント |

### Interactive terminal: transcript に埋もれた transient provider error（Issue #296）

`agent_runner = "interactive_terminal"` で tmux pane が `verdict.yaml` を書かずに終了した場合、
kaji は `terminal.log` の transcript **全文**（末尾 2000 文字に限らない）を走査し、既知の
transient pattern（`"at capacity"` / `"rate limit"` / `"overloaded"` など、`execute_cli()` と
同一の pattern list）を検出する。TUI redraw により transcript は旧来の tail window よりはるかに
大きくなり得るため、1行の provider エラーが深く埋もれる。全文走査でなければ、一時的な capacity
エラーを非復旧対象と誤分類してしまう。

`CLIExecutionError` / `result.json.error` に載せるのは一致した pattern **literal のみ**（例:
`"...transient provider error detected (pattern: 'at capacity')"`）で、transcript の部分文字列は
一切載せない。これにより、同一物理行にある無関係なテキスト（`Token usage:` テレメトリ等）が
classifier / sensitive gate の入力に混入せず、credential-leak gate を誤って発火させない。
ANSI 除去済みの人間向け抜粋・末尾は `pane-metadata.json` の `terminal_diagnostic` キー
（`kind`: `provider_error` / `no_pattern` / `no_log` / `empty`）にのみ保存され、classifier は
これを読まない。

結果として得られる `dispatch_failure` 分類と `--auto-recover` の挙動（candidate → 10 分後に
resume、または auto recovery 無効時は `comment_only`）は、他の transient dispatch failure と
同じルールに従う（以下参照）。

## `kaji run` の option

| flag | default | 意味 |
|------|---------|------|
| `--failure-triage` / `--no-failure-triage` | config（`true`） | 分類・triage コメント投稿・`recovery.json` / `run.log` 記録・stderr サマリ |
| `--auto-recover` / `--no-auto-recover` | config（`false`） | `decision: resume` のとき child run を 1 recovery chain につき 1 回起動する |
| `--recovery-root <run_id>` | — | recovery chain の root run_id（通常は handler が付与する） |
| `--recovery-parent <run_id>` | — | 直接の親 run_id。`--recovery-root` が必須で、単独指定は exit 2 |

precedence は `--agent-runner` と同じ（CLI flag > `.kaji/config.local.toml` > `.kaji/config.toml`）。
`--no-failure-triage` は `auto_recover` も強制的に無効化する（child run を起動する handler 自体が
走らないため）。

```bash
# 1. 通常運用（triage 有効・自動再開 無効が default）
kaji run .kaji/wf/dev.yaml 288
# → ERROR 終了時: Issue に triage コメント、recovery.json 保存、stderr にサマリ。exit 3

# 2. 自動再開を opt-in
kaji run .kaji/wf/dev.yaml 288 --auto-recover
# → decision: resume なら 10 分後に child run を起動。親の exit code は child のもの

# 3. handler が内部で起動する child run（運用者は通常直接叩かない）
kaji run .kaji/wf/dev.yaml 288 --from review-code \
  --recovery-root 260710120000 --recovery-parent 260710120000
```

## `kaji recover`

既に失敗した run の artifact に対して同じ handler を起動する。調査、provider 障害後の triage
レポート再生成、事後的な再開の opt-in に使う。

```
kaji recover <workflow.yaml> <issue> [--run-id <run_id>] [--auto-recover] [--workdir <dir>]
```

- `--run-id` 省略時は `<artifacts_dir>/<issue>/runs/` の最新 run を対象とする。
- 対象 run に `workflow_end` event が無ければ exit 2 で拒否する（実行中 run への誤介入防止）。
- 対象 run の終了 status が `ERROR` / `ABORT` 以外の場合も exit 2。
- `<workflow.yaml>` は再開点の解決と resume command の構築に使う。対象 run と同じ workflow を
  指定する責務は運用者側にある（workflow path は `recovery.json` に記録される）。
- 同じ run に対して再実行しても、既に自動再開を実行した run は `decision: exhausted` になる
  （budget は 1 recovery chain につき 1 回。`recovery.json` と child run dir が判定入力）。
- 本機能の導入**前**に生成された run（`run.log` の `workflow_start` に `schema_version` が無い）
  では、`failure_event` の不在を harness の矛盾と見なさない。bug issue は起票されない。

```bash
kaji recover .kaji/wf/dev.yaml 288
kaji recover .kaji/wf/dev.yaml 288 --run-id 260710120000
```

## exit code

既存の map（`0 = OK` / `1 = ABORT` / `2 = 定義エラー` / `3 = ランタイムエラー`）は不変。

| 状況 | exit code |
|------|-----------|
| `kaji run` で triage のみ（child run 未起動） | 元の失敗の exit code |
| `kaji run` が child run を起動した | child の exit code（chain の最終結果） |
| `kaji recover` で triage 完了（decision 問わず） | `0` |
| `kaji recover` で対象 run 不在 / 進行中 run / flag 不整合 / `requires_provider` 不一致 | `2` |
| `kaji recover` で handler 内部エラー | `3` |

## artifact

| パス | 内容 |
|------|------|
| `runs/<run_id>/recovery.json` | `RecoveryDecision`（`schema_version: 1`）。decision 更新のたびに上書き。第1層の `incident_ref` / `incident_action` / `incident_transient_closed` を含む（Issue #304）。incident 記録を抑止した場合は `incident_suppressed` / `incident_suppression_reason`（Issue #322） |
| `runs/<run_id>/recovery-chain.json` | `{root_run_id, parent_run_id}`。recovery child run が起動直後に書く |
| `runs/<run_id>/run.log` | `failure_event` / `recovery_decision` / `recovery_scheduled` / `recovery_attempt_start` / `recovery_attempt_end` / `incident_recorded` / `incident_recording_failed` / `incident_suppressed` |
| `incidents/occurrences.jsonl` | 第1層のローカル occurrence 記録（`<artifacts_dir>` 直下・append-only）。**incident 記録の対象外**（下記）を除く全 provider・全失敗で必ず 1 行追記。GitHub 起票の成否と無関係（fail-open の受け皿・backfill 元） |
| Issue コメント | 機械生成 triage report（child 起動前）と、自動再開した場合の結果報告 follow-up。第1層のインシデントイシュー本文 / occurrence コメント。kaji-verdict マーカーは付けない（step verdict ではないため） |
| stderr | 既存の終端表示の直後に出る `--- failure triage ---` の数行サマリ |

第1層（インシデント検知・集約）の詳細は [workflow guide](../dev/workflow_guide.md) § 第1層 と
[incident-labels.md](../dev/incident-labels.md) を参照。`incidents/occurrences.jsonl` は triage が
有効な失敗に対して必ず生成され、GitHub provider では加えてインシデントイシューへ集約される。

### incident 記録の対象外（Issue #322）

分類が `user_precondition_error` の失敗だけは、第1層の記録経路に一切入らない。新規起票も
occurrence コメントも `incidents/occurrences.jsonl` への追記も行わない。調査を要さない既知の
ユーザー前提エラーであり、incident 一覧に載せると障害の信号が薄まるため。

現時点で該当するのは `TmuxSessionRequiredError`（interactive terminal runner を tmux セッション
外から起動した）1 ケースのみ。判定は run.log の `failure_event.exception_type` の型名で行い、
エラーメッセージの文字列一致には依存しない。tmux 未インストール・tmux バージョン不足・
`TMUX_PANE` 欠落・その他の `CLINotFoundError` は従来どおり incident 記録の対象。

抑止した場合も、console のエラー表示・run artifact・発生元 Issue への triage コメントは
維持される。抑止の事実と理由は `run.log` の `incident_suppressed` event（`cause` /
`exception_type` / `failed_step` / `reason`）と `recovery.json` の `incident_suppressed` /
`incident_suppression_reason` から確認できる。

stderr サマリの `comment:` 行は `Comment.ref` をそのまま表示する。GitHub provider では作成コメント
URL、local provider では comment file の repo-root 相対パス、取得不能時は `n/a`。

## 関連ドキュメント

- [workflow guide](../dev/workflow_guide.md) — 運用ルール、自動再開しないケース
- [configuration reference](../reference/configuration.md) — `[execution] failure_triage` / `auto_recover`
- [ARCHITECTURE](../ARCHITECTURE.md) — recovery layer と `kaji_harness/recovery/` package
