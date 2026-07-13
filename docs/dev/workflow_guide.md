# ワークフローガイド

ワークフローの選択基準と各ワークフローへのポインター。
ワークフロー全体の概要は [workflow_overview.md](workflow_overview.md) を参照。

## 通常運用 workflow（5 本）

通常運用で使う workflow は GitHub provider 3 本 + local provider 緊急 fallback 2 本の
計 5 本に固定する。failure triage 第2層の `incident.yaml`（手動起動・調査専用。§ 第2層: 調査・提案）は
通常運用の 5 本には含めない別系統の workflow。

| ファイル | provider | 用途 |
|----------|----------|------|
| `.kaji/wf/dev.yaml` | github | 標準 dev workflow（設計 → 実装 → レビュー → PR → review-poll → close） |
| `.kaji/wf/dev-thorough.yaml` | github | 丁寧版 dev workflow（同じ骨格でモデル / effort を厚めに） |
| `.kaji/wf/docs.yaml` | github | docs-only workflow |
| `.kaji/wf/dev-local.yaml` | local | GitHub 障害時・緊急時の fallback dev workflow |
| `.kaji/wf/docs-local.yaml` | local | GitHub 障害時・緊急時の fallback docs-only workflow |

- 各 YAML の `name:` はファイル名から `.yaml` を除いた値と一致する。
- local 2 本は GitHub 前提 step（`i-pr` / `review-poll` / PR review）を持たず、最終 step は
  `issue-close`（local merge `--no-ff` + frontmatter 更新）。通常時は GitHub 3 本を使い、
  GitHub 障害時・緊急時の fallback として local 2 本を使う。

## ワークフロー選択表

| 作業種類 | 通常時（GitHub 正常） | 緊急時（GitHub 障害・不通） |
|----------|------------------------|------------------------------|
| 機能追加・バグ修正・リファクタ | `dev.yaml` | `dev-local.yaml` |
| 丁寧に進めたいコード変更 | `dev-thorough.yaml` | `dev-local.yaml`（thorough の local 版は持たない） |
| スキルファイルの改善 | `dev.yaml` | `dev-local.yaml` |
| ドキュメント修正のみ | `docs.yaml` | `docs-local.yaml` |
| 既存 PR の review 収束のみ | `dev.yaml --from review-poll [--before close]` | （PR concept なし。local では非対象） |

判断に迷うケースは [workflow_overview.md](workflow_overview.md) の判断テーブルを参照。

## 複数 Issue の sequential series

順序が確定した複数 Issue を前段完了後に一件ずつ進める場合は、単一 Issue workflow を
変更せず上位の series runner を使う。定義は `.kaji/series/<id>.yaml` に置く。

```yaml
id: maintenance-2026-07
strategy: sequential
members:
  - issue: 310
    workflow: .kaji/wf/dev.yaml
  - issue: 311
    workflow: .kaji/wf/docs.yaml
on_failure: stop
```

```bash
kaji validate-series .kaji/series/maintenance-2026-07.yaml
kaji run-series .kaji/series/maintenance-2026-07.yaml --dry-run
kaji run-series .kaji/series/maintenance-2026-07.yaml
kaji run-series .kaji/series/maintenance-2026-07.yaml --resume
```

`parent_issue` は任意のトレーサビリティ情報で、実行意味論を変えない。member は YAML の
順序どおりに起動され、child `kaji run` の exit 0 と GitHub Issue の
`closed/completed` が揃った場合だけ次へ進む。失敗・close reason 不一致・外部状態の巻き戻りは
後続を起動せず停止する。`--dry-run` は provider、state、lock、member workflow に触れない。

定義作成には `/series-create` を使う。skill は Issue の単一 `type:` label と各 workflow の
`description` から標準 `dev.yaml` / `docs.yaml` を一意選択し、thorough / fable 等の variant
は member 単位 `--workflow` override がある場合だけ採用する。生成後は validation と dry-run
まで行い、本実行は開始しない。

## provider × workflow の対応表

各 builtin workflow が要求する provider type。`kaji run` 起動時に
`config.provider.type` と突合し、不整合を exit 2 で fail-fast する。

| Workflow | `requires_provider` | 末尾 step | 備考 |
|----------|---------------------|-----------|------|
| `dev.yaml` | `github` | `issue-close` | forge 必須。review-poll → close まで内包 |
| `dev-thorough.yaml` | `github` | `issue-close` | forge 必須。丁寧版 |
| `docs.yaml` | `github` | `issue-close` | forge 必須。docs-only |
| `dev-local.yaml` | `local` | `issue-close` | local merge (`--no-ff`) 前提。GitHub 前提 step を持たない |
| `docs-local.yaml` | `local` | `issue-close` | docs-only / local。GitHub 前提 step を持たない |
| `incident.yaml` | `github` | `report` | 通常運用ではない failure triage 第2層（手動起動）。調査 → 査読 → 修正 → 確認 → 最終提案。終端は「提案」で close step を持たない（§ 第2層: 調査・提案） |

custom workflow への `requires_provider` 追加は推奨（[workflow-authoring.md](workflow-authoring.md)
§ `requires_provider` 参照）。

## PR レビュー後フェーズ（途中起動）

dev / dev-thorough / docs の 3 本は review 軸（`review-poll` step +
`pr-review` cycle: `entry: review-poll` / `loop: [pr-fix, pr-verify]` /
`max_iterations: 3` / `on_exhaust: ABORT`）を内包している。PR review の収束だけを
回したい場合は専用 workflow を増やさず、`dev.yaml` を `--from review-poll` で途中起動する。
review 以降の step 列は dev / dev-thorough / docs で同一のため、canonical には `dev.yaml`
を使えばよい。

| 用途 | コマンド | close 実行 |
|------|---------|-----------|
| review → 修正 → 確認ループのみ（close 手前で停止） | `kaji run .kaji/wf/dev.yaml <id> --from review-poll --before close` | ❌（手動 `/issue-close`） |
| review から close まで全自動 | `kaji run .kaji/wf/dev.yaml <id> --from review-poll` | ✅（自動） |
| PR 作成で停止（review に入らない） | `kaji run .kaji/wf/dev.yaml <id> --before review-poll` | ❌ |
| `/review-cycle <id>` | review → 修正 → 確認ループを 1 コマンドで回す slash command wrapper（内部で `dev.yaml --from review-poll --before close` を起動）。終了後に `/issue-close` 案内を出力 | ❌（手動） |

> **review-poll の前提**: dev / dev-thorough / docs は `chatgpt-codex-connector[bot]` (id `199175422`) の auto-review が走っている GitHub 環境を前提に設計されている。`requires_provider: github` 固定で、local 環境では workflow load 時に exit 2 する。auto-review がクレジット不足等で走らない場合は、`review-poll` が `NO_REACTION_TIMEOUT_SEC` (60s) 経過で `BACK_FALLBACK` を返し、既存 `review` skill (codex agent による能動レビュー) に fallback する。詳細は [`.claude/skills/review-poll/SKILL.md`](../../.claude/skills/review-poll/SKILL.md) を参照。

## 途中開始・途中終了・単発実行（`--from` / `--before` / `--step` / `--reset-cycle`）

途中開始・途中終了・単発実行は専用 workflow YAML を増やさず、`kaji run` の flag で行う。

```bash
# 通常運用（GitHub）
kaji run .kaji/wf/dev.yaml 247              # 標準 dev
kaji run .kaji/wf/dev-thorough.yaml 247     # 丁寧版
kaji run .kaji/wf/docs.yaml 247             # docs-only

# 緊急時 fallback（GitHub 障害・不通時）
kaji run .kaji/wf/dev-local.yaml 247
kaji run .kaji/wf/docs-local.yaml 247

# 途中開始・途中終了・単発実行
kaji run .kaji/wf/dev.yaml 247 --from review-poll               # PR review から close まで（旧 review-close 相当）
kaji run .kaji/wf/dev.yaml 247 --from review-poll --before close # PR review ループのみ・close 手前で停止（旧 review-cycle 相当）
kaji run .kaji/wf/dev.yaml 247 --step review-code               # 単発実行
kaji run .kaji/wf/dev.yaml 247 --before review-poll             # PR 作成で停止
```

`--from` / `--step` / `--before` の意味論は
[workflow-authoring.md § 実行コマンド](workflow-authoring.md) を参照。

### cycle exhaust (`on_exhaust: ABORT`) からの復旧（Issue #189）

AI レビュアーが 3 連続 RETRY を返すなどして cycle が `max_iterations` に達すると、
`on_exhaust: ABORT` で run が停止する。`cycle_counts` は `session-state.json` に
永続化されるため、`--from` だけで再実行しても同じ cycle が即座に再度 exhaust する
（`session-state.json` の手動編集なしに復旧する手段がなかった問題）。

Issue 本文や設計書を修正した上で、`--from <cycle 内 step>` に `--reset-cycle` を
併用すると、その cycle の iteration count だけを `0` に戻して再開できる:

```bash
# ready-review cycle が exhaust した後、Issue 本文を修正してから再開する
kaji run .kaji/wf/dev.yaml 184 --from review-ready --reset-cycle
```

`--reset-cycle` は `--from` を必須の相棒とする（単独指定はエラー）。`--from` の
step が cycle に属さない場合（linear step）も誤用としてエラーになる。詳細な意味論は
[workflow-authoring.md § `--reset-cycle` の意味論](workflow-authoring.md) を参照。

## failure triage と自動再開（Issue #288）

`kaji run` が `ERROR`、または triage 対象の `ABORT` で終了すると、run artifact を根拠に原因を
機械分類し、証跡を固定する **failure triage** が走る。失敗対処は 2 層に分かれる。

| 層 | 対象 | 時間スケール | 挙動 |
|----|------|-------------|------|
| attempt retry | 1 step dispatch 内の transient CLI failure | 数十秒〜数分 | `execute_cli()` が in-process で最大 3 回リトライ |
| run recovery | workflow process の `ERROR` / triage 対象 `ABORT` 終端 | 固定 10 分ウェイト + 新規 `kaji run` | 本節の handler。**1 recovery chain につき 1 回だけ** |

### triage が残すもの

- Issue コメント: 機械生成の triage report（原因・根拠・次アクション。LLM は使わない）
- `runs/<run_id>/recovery.json`: 判定結果（`decision` / `classification` / `resume_command` 等）
- `run.log`: `failure_event` / `recovery_decision` / `recovery_scheduled` / `recovery_attempt_start` / `recovery_attempt_end`
- stderr: 既存の `Error:` / `Workflow aborted:` 表示の直後に数行のサマリ

triage は default 有効（`[execution] failure_triage = true`）。証跡を残すだけで destructive な
操作は行わない。無効化は `--no-failure-triage`。

### 第1層: インシデント検知・集約（Issue #304）

triage コメント投稿の**直後**に、同じ失敗を「識別署名」で照合してインシデントイシューに
集約する第1層が走る（完全純コード・LLM なし・fail-open）。triage が「1 回の失敗の証跡」を
残すのに対し、第1層は「同一障害の再発を 1 本のイシューに束ね、回数を自動で数える」層。

- **識別署名** = `(failure_cause, exception_type, 正規化エラー指紋)`。run_id / タイムスタンプ /
  絶対パス / issue 参照 / 可変 tail（`Last N chars:` 以降）などの occurrence 固有値は正規化で
  除去し、HTTP status / exit code / errno などの識別的数値は allowlist で保持する。指紋 hash は
  **redaction 後**のテキストから生成する（secrets を marker 経由で漏らさない）。
- **照合と起票**（GitHub provider のみ。他 provider はローカル記録のみで起票 no-op）:
  `incident` ラベルで全件検索し、identity marker を厳格 parse して署名同値を探す。
  - open 一致 → occurrence コメントを追記（回数 +1）
  - closed かつ `incident:cause:transient` 一致 → reopen せず occurrence 追記
  - closed かつ人間 resolve 済み一致 → 新規起票し旧イシューへリンク（リグレッション検知）
  - 一致なし → `incident` + `incident:investigating` で新規起票し、初回 occurrence コメントを投稿
- **再発回数**は可変カウンタを持たず、イシュー全コメント中の occurrence marker の
  **ユニーク `run_id` 件数**から導出する。crash window（remote 投稿成功 → ローカル保存前に中断）で
  同一 run のコメントが二重投稿されても回数は汚れない（at-least-once + 読み取り時 dedupe）。
- **transient 即クローズ**: `--auto-recover` の child run が `COMPLETE`（自己回復）し、かつ
  この run が起票したインシデントなら、`incident:cause:transient` を付与して即クローズする。
- **fail-open**: 起票・照合の失敗は triage コメント生成・recovery 判断・exit code を一切変えない。
  失敗しても `<artifacts_dir>/incidents/occurrences.jsonl` にローカル記録が残り、次回の同一署名
  失敗時に backfill で自然回復する。
- **無効化**: 第1層は failure triage の内部ステップであり、`--no-failure-triage`
  （`[execution] failure_triage = false`）で triage ごと無効になる。「全失敗を例外なく記録」は
  triage が有効な失敗に対する契約。
- ラベル 2 軸の意味と遷移意図は [incident-labels.md](./incident-labels.md) を参照。

### 第2層: 調査・提案（Issue #305）

第1層が起票したインシデントイシューを入力に、**原因調査 → 査読 → 修正 → 確認 → 最終提案**の
レビュー収束サイクルを回す第2層。第1層が完全純コード（LLM なし）なのに対し、第2層は LLM の付加価値
（可読サマリ・意味的類似の指摘・統合提案）を担う。**手動起動・人間ゲート**であり、自動起動・自動昇格は
しない（EPIC #303「自動化への移行条件」が未解消のため）。

- **起動**: `/incident-cycle <incident_issue_id>`（slash wrapper）または
  `kaji run .kaji/wf/incident.yaml <incident_issue_id>`。`requires_provider: github`。
- **workflow**: `.kaji/wf/incident.yaml`。step 構成は investigate（調査・提案役 opus）→
  review（実行型査読役 subagent。提案役と別モデル sonnet）→ cycle `incident-review`
  （`loop: [fix, verify]` / `max_iterations: 3` / `on_exhaust: ABORT`）→ report（最終提案）。
- **調査結論とレビュー verdict は別軸**（#303 決定 D）: 調査結論は
  `internal-bug` / `upstream` / `environment` / `transient` / `duplicate` / `INCONCLUSIVE` の 6 値。
  レビュー verdict（`PASS` / `RETRY` / `ABORT`）は調査品質のみを判定する。証拠不足のときは無理な断定を
  せず `INCONCLUSIVE`（棄却済み仮説＋不足証拠）を返し、記述が十分ならレビュー verdict は PASS になり得る。
- **受理基準は実証**（#303 決定 A）: 断定には実再現または実障害ログの引用（citation）が必須。
  査読役は反証義務＋一次情報の独立検証（ログ再読・再現の再実行・独立検索）を課され、`gh` 書き込み系・
  push・issue 操作は指示レベルで禁止される（機械的強制はスコープ外）。
- **全終端は「提案」**: ラベル遷移・クローズ・バグイシュー化・統合の**実行は人間**。conclusion →
  推奨ラベル・後続アクションの処遇メニューは [incident-labels.md](./incident-labels.md) § 調査フローと処遇判断（第2層）を参照。
- **cycle exhaust からの復旧**: 査読 cycle が `max_iterations`（3）到達で ABORT した場合は、人間が
  artifact を確認してから `kaji run .kaji/wf/incident.yaml <id> --from review --reset-cycle` で再開する。
- skill 群: `incident-investigate` / `incident-review` / `incident-fix` / `incident-verify` /
  `incident-report` / `incident-cycle`、実行型査読役 agent `kaji-incident-reviewer`。

### 自動再開（opt-in）

自動再開は default 無効（`[execution] auto_recover = false`）。`--auto-recover` で有効にすると、
`decision: resume` の場合のみ **固定 10 分ウェイト後に child run を 1 回だけ**起動する。

```bash
kaji run .kaji/wf/dev.yaml 288                 # triage のみ（default）
kaji run .kaji/wf/dev.yaml 288 --auto-recover  # 復旧可能なら 10 分後に 1 回だけ自動再開
```

- **budget は recovery chain 単位で 1**。自動再開で作られた child run が再び失敗しても、
  「別 run_id だからもう 1 回」は成立しない（`recovery-chain.json` の有無で機械的に決まる）。
  手動で起動した独立 run は新しい chain の root になり、budget は復活する。
- **10 分ウェイトの理由**: attempt retry が既にバックオフ込みで諦めた直後に即時再開すると、
  同じ API / agent 障害を踏んで唯一の budget を消費しやすい。triage コメントはウェイト開始
  **前**に投稿され、`resume_scheduled_at` で再開予定時刻が Issue 上に残る。
- **並行実行は常に新しい run が優先される**。child 起動直前に `runs/` を再走査し、自 run より
  新しい run dir があれば起動を中止して `decision: cancelled_newer_run_detected` に更新する
  （再チェックと起動の間に手動 run が割り込む数百 ms の race は既知の許容範囲。child 側も
  同一 Issue の state を追記型で扱うため破壊はしない）。
- ウェイト中に中断（SIGINT）した場合は `decision: cancelled_interrupted` で停止する。

### 自動再開しないケース

以下は再開せず、triage コメントに次アクションを残して停止する。

- 正規の `ABORT` verdict（agent の安全停止・手動確認要求）→ `comment_only`
- cycle exhaust → `not_resumable`。`--reset-cycle` は **自動付与しない**（安全弁の自動解除は
  無制限 retry の実質的迂回になるため、手動の次アクション候補として提示するに留める）
- config / workflow 定義 / workdir / resume session の不備 → `not_resumable`
- worktree 不在 / branch 不一致 / provider 解決失敗 / auth・secret・permission 形跡 /
  副作用 step（`issue-start` / `i-pr` / `issue-close`）→ `not_resumable`
- 既に自動再開済みの chain → `exhausted`
- artifact と runner event の決定論的矛盾 → `bug_issue_created`（`type:bug` の Issue を起票）
- triage コメントの投稿に失敗した場合も自動再開しない（handler が必要操作を完遂できていないため）

`resume:` step が失敗した場合は、異常セッションを引き継がず **session 生成元 step へ巻き戻して**
再開する（`discarded_resume_session: true`）。

### 失敗 artifact からの手動 triage（`kaji recover`）

```bash
kaji recover .kaji/wf/dev.yaml 288                      # 最新 run を対象に triage を再実行
kaji recover .kaji/wf/dev.yaml 288 --run-id 260710120000
```

対象 run に `workflow_end`（status `ERROR` / `ABORT`）が無い場合は、実行中 run への誤介入を
防ぐため exit 2 で停止する。triage が完了すれば decision にかかわらず exit 0。

CLI 仕様の詳細は [Failure Triage / Recovery CLI](../cli-guides/failure-recovery.ja.md) を参照。

## runner backend（headless / interactive-terminal）

agent step を headless CLI で起動するか tmux pane 上の対話 CLI で起動するかは
**workflow YAML ではなく repository config の `[execution].agent_runner`** で選ぶ。
workflow YAML は runner backend を固定しない。

- 通常運用: repository config の `[execution].agent_runner` で選択する。config TOML では
  アンダースコア表記 `agent_runner = "interactive_terminal"` を使う。
- 一時 override: `kaji run ... --agent-runner interactive-terminal` でその実行だけ上書きする。
  CLI override ではハイフン表記を使う（`headless` / `interactive-terminal`）。
- `claude -p` は headless runner の実装詳細であり、workflow 選択基準には含めない。

```bash
# config TOML（通常運用 / 恒久設定）
#   [execution]
#   agent_runner = "interactive_terminal"   # アンダースコア

# CLI override（この実行だけ一時上書き）
kaji run .kaji/wf/dev.yaml 247 --agent-runner interactive-terminal   # ハイフン
kaji run .kaji/wf/dev.yaml 247 --agent-runner headless
```

詳細は [Interactive Terminal Runner ガイド](../cli-guides/interactive-terminal-runner.md) を参照。

## dev / dev-thorough

コード変更を伴う Issue のワークフロー。設計 → 設計レビュー → 実装 → コードレビュー →
最終チェック → PR → review-poll → close。`dev-thorough` は同じ骨格をモデル / effort を
厚めにした丁寧版。

各 hand-off 直前（`design → review-design` / `implement → review-code`）には **pre-handoff review** が挟まる（capability-based: Claude Code は `kaji-code-reviewer` subagent、Codex / Gemini は main-session self-check）。詳細は [development_workflow.md § Pre-Handoff Review](development_workflow.md#prehandoff-review) を参照。

詳細: [development_workflow.md](development_workflow.md)

## docs

ドキュメント修正のみの Issue のワークフロー。コード・設定・テストは変更せず、現行実装との整合性を監査しながら docs を更新する。

詳細: [docs_maintenance_workflow.md](docs_maintenance_workflow.md)

## step 種別（agent step / script step）

workflow.yaml の step は、実行経路で 2 種類に分かれる。

| 種別 | 宣言 | 実行経路 | LLM コスト |
|------|------|---------|-----------|
| **agent step** | `skill:` + `agent:` | skill を LLM agent で実行 | 発生する |
| **script step (exec)** | `exec:`（`skill:` と相互排他） | 宣言した command を直接 subprocess 実行（決定論） | 発生しない |

- `exec:` step は skill ファイルを増やさず、その workflow に閉じた決定論処理（metrics 収集・
  artifact dump・外部 CLI 呼び出し等）を workflow.yaml 1 箇所で宣言できる（Issue #205）。
- exec-step は `agent:` を持てないため、**`agent:` の有無 = LLM コスト発生の有無** という
  不変条件が workflow.yaml 単独で成立する。
- 宣言方法・規約・`exec:` vs `exec_script:`（skill frontmatter）の使い分けは
  [workflow-authoring.md § exec-step（script step）](workflow-authoring.md) を参照。

## 関連ドキュメント

- [完了条件](workflow_completion_criteria.md) — フェーズ別の完了条件チェックリスト
- [スキル横断ルール](shared_skill_rules.md) — スキル間の責務境界
- [ドキュメント更新基準](documentation_update_criteria.md) — ドキュメント更新要否の判断
