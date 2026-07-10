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
| `runs/<run_id>/recovery.json` | `RecoveryDecision`（`schema_version: 1`）。decision 更新のたびに上書き |
| `runs/<run_id>/recovery-chain.json` | `{root_run_id, parent_run_id}`。recovery child run が起動直後に書く |
| `runs/<run_id>/run.log` | `failure_event` / `recovery_decision` / `recovery_scheduled` / `recovery_attempt_start` / `recovery_attempt_end` |
| Issue コメント | 機械生成 triage report（child 起動前）と、自動再開した場合の結果報告 follow-up。kaji-verdict マーカーは付けない（step verdict ではないため） |
| stderr | 既存の終端表示の直後に出る `--- failure triage ---` の数行サマリ |

stderr サマリの `comment:` 行は `Comment.ref` をそのまま表示する。GitHub provider では作成コメント
URL、local provider では comment file の repo-root 相対パス、取得不能時は `n/a`。

## 関連ドキュメント

- [workflow guide](../dev/workflow_guide.md) — 運用ルール、自動再開しないケース
- [configuration reference](../reference/configuration.md) — `[execution] failure_triage` / `auto_recover`
- [ARCHITECTURE](../ARCHITECTURE.md) — recovery layer と `kaji_harness/recovery/` package
