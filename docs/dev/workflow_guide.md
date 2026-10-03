# ワークフローガイド

ワークフローの選択基準と各ワークフローへのポインター。
ワークフロー全体の概要は [workflow_overview.md](workflow_overview.md) を参照。

## 通常運用 workflow（5 本）

workflow YAML は所有権で 2 系統に分かれる。契約の正本は
[workflow-authoring.md](workflow-authoring.md) § ファイル配置。

### official（kaji 公式提供・更新・テスト対象）

| ファイル | provider | 用途 |
|----------|----------|------|
| `.kaji/wf/official/dev.yaml` | github | 標準 dev workflow（設計 → 実装 → レビュー → PR → review-poll → close） |
| `.kaji/wf/official/docs.yaml` | github | docs-only workflow |
| `.kaji/wf/official/local/dev-local.yaml` | local | GitHub 障害時・緊急時の fallback dev workflow |
| `.kaji/wf/official/local/docs-local.yaml` | local | GitHub 障害時・緊急時の fallback docs-only workflow |
| `.kaji/wf/official/incident.yaml` | github | failure triage 第2層（手動起動・調査専用。§ 第2層: 調査・提案）。通常運用には含めない |

### custom（このリポジトリ固有・利用者管理）

kaji の pytest 回帰対象外。`make validate-workflows` の L1/L2/L3 静的検証のみが掛かる。

| ファイル | provider | 用途 |
|----------|----------|------|
| `.kaji/wf/custom/dev/dev-thorough.yaml` | github | 丁寧版 dev workflow（dev.yaml と同じ骨格でモデル / effort を厚めに） |
| `.kaji/wf/custom/dev/dev-thorough-fable.yaml` | github | dev-thorough の fable モデル variant |
| `.kaji/wf/custom/dev/dev-small.yaml` | github | 設計判断済み小修正向けの軽量 dev workflow（試験導入。§ dev-small） |
| `.kaji/wf/custom/docs/docs-codex.yaml` | github | docs workflow の codex variant |
| `.kaji/wf/custom/docs/docs-fable.yaml` | github | docs workflow の fable variant |
| `.kaji/wf/custom/docs/docs-thorough-codex.yaml` | github | 丁寧版 docs workflow の codex variant |
| `.kaji/wf/custom/operations/starter-sync.yaml` | github | managed starter 同期（`update-starter` → `review-starter-update` → `release-starter`）。通常運用には含めない（手動起動） |

- 各 YAML の `name:` はファイル名から `.yaml` を除いた値と一致する。
- `official/local/` の 2 本は GitHub 前提 step（`i-pr` / `review-poll` / PR review）を持たず、
  最終 step は `issue-close`（local merge `--no-ff` + frontmatter 更新）。通常時は GitHub provider
  の workflow を使い、GitHub 障害時・緊急時の fallback として local 2 本を使う。

## ワークフロー選択表

| 作業種類 | 通常時（GitHub 正常） | 緊急時（GitHub 障害・不通） |
|----------|------------------------|------------------------------|
| 機能追加・バグ修正・リファクタ | `official/dev.yaml` | `official/local/dev-local.yaml` |
| 丁寧に進めたいコード変更 | `custom/dev/dev-thorough.yaml`（このリポジトリ固有の custom variant） | `official/local/dev-local.yaml`（thorough の local 版は持たない） |
| 設計判断が済んだ小修正（明示選択） | `custom/dev/dev-small.yaml`（試験導入の custom variant。§ dev-small の適用条件） | `official/local/dev-local.yaml`（small の local 版は持たない） |
| スキルファイルの改善 | `official/dev.yaml` | `official/local/dev-local.yaml` |
| ドキュメント修正のみ | `official/docs.yaml` | `official/local/docs-local.yaml` |
| 既存 PR の review 収束のみ | `official/dev.yaml --from review-poll [--before close]` | （PR concept なし。local では非対象） |

判断に迷うケースは [workflow_overview.md](workflow_overview.md) の判断テーブルを参照。

## 複数 Issue の sequential series

順序が確定した複数 Issue を前段完了後に一件ずつ進める場合は、単一 Issue workflow を
変更せず上位の series runner を使う。定義は `.kaji/series/<id>.yaml` に置く。

```yaml
id: maintenance-2026-07
strategy: sequential
members:
  - issue: 310
    workflow: .kaji/wf/official/dev.yaml
  - issue: 311
    workflow: .kaji/wf/official/docs.yaml
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
後続を起動せず停止する。

`validate-series` と `--dry-run` は、現在の plan が参照する全 member workflow を読み取り、
L1（parse/schema）/ L2（step・cycle の参照整合）/ L3（skill metadata）を完全検証する。
通常実行も開始時に同じ検証を再実行し、過去の dry-run 成功を信用しない。invalid member が
1 件でもあれば、member subprocess、series state、lock を作成せず停止する。`--dry-run` は
provider API、Issue、artifact、state、lock、member workflow 実行への副作用を持たない。

定義作成には `/series-create` を使う。skill は Issue の単一 `type:` label と各 workflow の
`description` から標準 `dev.yaml` / `docs.yaml` を一意選択し、thorough / fable 等の variant
は member 単位 `--workflow` override がある場合だけ採用する。生成後は validation と dry-run
まで行い、本実行は開始しない。

## provider × workflow の対応表

各 builtin workflow が要求する provider type。`kaji run` 起動時に
`config.provider.type` と突合し、不整合を exit 2 で fail-fast する。

| Workflow | 所有区分 | `requires_provider` | 末尾 step | 備考 |
|----------|----------|---------------------|-----------|------|
| `official/dev.yaml` | official | `github` | `issue-close` | forge 必須。review-poll → close まで内包 |
| `official/docs.yaml` | official | `github` | `issue-close` | forge 必須。docs-only |
| `official/local/dev-local.yaml` | official | `local` | `issue-close` | local merge (`--no-ff`) 前提。GitHub 前提 step を持たない |
| `official/local/docs-local.yaml` | official | `local` | `issue-close` | docs-only / local。GitHub 前提 step を持たない |
| `official/incident.yaml` | official | `github` | `report` | 通常運用ではない failure triage 第2層（手動起動）。調査 → 査読 → 修正 → 確認 → 最終提案。終端は「提案」で close step を持たない（§ 第2層: 調査・提案） |
| `custom/dev/dev-thorough.yaml` | custom | `github` | `issue-close` | forge 必須。丁寧版。kaji の pytest 回帰対象外 |
| `custom/dev/dev-small.yaml` | custom | `github` | `issue-close` | forge 必須。設計判断済み小修正向け（試験導入）。kaji の pytest 回帰対象外 |
| `custom/operations/starter-sync.yaml` | custom | `github` | `release-starter` | 通常運用ではない managed starter 同期（手動起動）。終端は publish で close step を持たない。kaji の pytest 回帰対象外 |

custom workflow への `requires_provider` 追加は推奨（[workflow-authoring.md](workflow-authoring.md)
§ `requires_provider` 参照）。

## PR レビュー後フェーズ（途中起動）

official の `dev` / `docs` と custom の `dev-thorough` 系・`dev-small` は review 軸（`review-poll` step +
PR review cycle: `entry: review-poll` / `loop: [pr-fix, pr-verify]` /
`max_iterations: 3` / `on_exhaust: ABORT`。`dev-small` の cycle 名は `small-pr-review`）を内包している。PR review の収束だけを
回したい場合は専用 workflow を増やさず、`official/dev.yaml` を `--from review-poll` で
途中起動する。review 以降の step 列はこれらで同一のため、canonical には
`official/dev.yaml` を使えばよい。

| 用途 | コマンド | close 実行 |
|------|---------|-----------|
| review → 修正 → 確認ループのみ（close 手前で停止） | `kaji run .kaji/wf/official/dev.yaml <id> --from review-poll --before close` | ❌（手動 `/issue-close`） |
| review から close まで全自動 | `kaji run .kaji/wf/official/dev.yaml <id> --from review-poll` | ✅（自動） |
| PR 作成で停止（review に入らない） | `kaji run .kaji/wf/official/dev.yaml <id> --before review-poll` | ❌ |
| `/review-cycle <id>` | review → 修正 → 確認ループを 1 コマンドで回す slash command wrapper（内部で `dev.yaml --from review-poll --before close` を起動）。終了後に `/issue-close` 案内を出力 | ❌（手動） |

> **review-poll の前提**: `official/dev.yaml` / `official/docs.yaml` と custom の dev-thorough 系・dev-small は `chatgpt-codex-connector[bot]` (id `199175422`) の auto-review が走っている GitHub 環境を前提に設計されている。`requires_provider: github` 固定で、local 環境では workflow load 時に exit 2 する。auto-review がクレジット不足等で走らない場合は、`review-poll` が `NO_REACTION_TIMEOUT_SEC` (60s) 経過で `BACK_FALLBACK` を返し、既存 `review` skill (codex agent による能動レビュー) に fallback する。review-poll の `PASS` は、bot の review summary comment（現在 head で `Completed`）と、その完了後の `+1` を組み合わせた場合だけで、`+1` 単独では承認にならない。`PASS` 時は PR 番号・40 桁の head SHA・承認シグナルの ID を持つ構造化証跡 `review-poll-evidence.json` を step attempt に保存し、`issue-close` が現在の PR と照合したうえで `--match-head-commit` 付きで merge する。詳細は [`.claude/skills/review-poll/SKILL.md`](../../.claude/skills/review-poll/SKILL.md) と [`.claude/skills/issue-close/SKILL.md`](../../.claude/skills/issue-close/SKILL.md) を参照。

## 途中開始・途中終了・単発実行（`--from` / `--before` / `--step` / `--reset-cycle`）

途中開始・途中終了・単発実行は専用 workflow YAML を増やさず、`kaji run` の flag で行う。

```bash
# 通常運用（GitHub）
kaji run .kaji/wf/official/dev.yaml 247              # 標準 dev
kaji run .kaji/wf/custom/dev/dev-thorough.yaml 247     # 丁寧版
kaji run .kaji/wf/custom/dev/dev-small.yaml 247        # 設計判断済み小修正（試験導入。§ dev-small）
kaji run .kaji/wf/official/docs.yaml 247             # docs-only

# 緊急時 fallback（GitHub 障害・不通時）
kaji run .kaji/wf/official/local/dev-local.yaml 247
kaji run .kaji/wf/official/local/docs-local.yaml 247

# 途中開始・途中終了・単発実行
kaji run .kaji/wf/official/dev.yaml 247 --from review-poll               # PR review から close まで（旧 review-close 相当）
kaji run .kaji/wf/official/dev.yaml 247 --from review-poll --before close # PR review ループのみ・close 手前で停止（旧 review-cycle 相当）
kaji run .kaji/wf/official/dev.yaml 247 --step review-code               # 単発実行
kaji run .kaji/wf/official/dev.yaml 247 --before review-poll             # PR 作成で停止
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
kaji run .kaji/wf/official/dev.yaml 184 --from review-ready --reset-cycle
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
- **記録の対象外**（Issue #322 / #403 / #405）: 分類が `user_precondition_error` /
  `user_interrupted` / `agent_declared_abort` / `cycle_exhausted` の失敗は incident 記録を
  行わない（新規起票・occurrence コメント・`incidents/occurrences.jsonl` 追記のいずれもしない）。
  triage コメント・run artifact・console のエラー表示は従来どおり維持し、抑止した事実と理由は
  `run.log` の `incident_suppressed` event で監査できる。該当するのは「interactive terminal
  runner を tmux セッション外から起動した」（`TmuxSessionRequiredError`）、「利用者が Ctrl-C で
  run を中断した」（`failure_event.kind = "interrupted"`）、「agent が正規の ABORT verdict を
  返した」（`failure_event.kind = "agent_abort"`）、「cycle が `max_iterations` に到達した」
  （`failure_event.kind = "cycle_exhausted"`）の 4 ケース。前 2 者は調査を要さない既知の
  ユーザー起因の終了、後 2 者は契約上の正常終端であり、かつ例外を伴わないため識別署名が
  cause ごとの定数へ退化する（除外しないと無関係な安全停止が 1 incident へ誤って集約される）。
  tmux 未インストール・tmux バージョン不足・`TMUX_PANE` 欠落・その他の `CLINotFoundError` は
  従来どおり incident 記録の対象。
- ラベル 2 軸の意味と遷移意図は [incident-labels.md](./incident-labels.md) を参照。

### 第2層: 調査・提案（Issue #305）

第1層が起票したインシデントイシューを入力に、**原因調査 → 査読 → 修正 → 確認 → 最終提案**の
レビュー収束サイクルを回す第2層。第1層が完全純コード（LLM なし）なのに対し、第2層は LLM の付加価値
（可読サマリ・意味的類似の指摘・統合提案）を担う。**手動起動・人間ゲート**であり、自動起動・自動昇格は
しない（EPIC #303「自動化への移行条件」が未解消のため）。

- **起動**: `/incident-cycle <incident_issue_id>`（slash wrapper）または
  `kaji run .kaji/wf/official/incident.yaml <incident_issue_id>`。`requires_provider: github`。
- **workflow**: `.kaji/wf/official/incident.yaml`。step 構成は investigate（調査・提案役 opus）→
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
  artifact を確認してから `kaji run .kaji/wf/official/incident.yaml <id> --from review --reset-cycle` で再開する。
- skill 群: `incident-investigate` / `incident-review` / `incident-fix` / `incident-verify` /
  `incident-report` / `incident-cycle`、実行型査読役 agent `kaji-incident-reviewer`。

### 自動再開（opt-in）

自動再開は default 無効（`[execution] auto_recover = false`）。`--auto-recover` で有効にすると、
`decision: resume` の場合のみ **固定 10 分ウェイト後に child run を 1 回だけ**起動する。

```bash
kaji run .kaji/wf/official/dev.yaml 288                 # triage のみ（default）
kaji run .kaji/wf/official/dev.yaml 288 --auto-recover  # 復旧可能なら 10 分後に 1 回だけ自動再開
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
  副作用 skill（`issue-start` / `i-pr` / `issue-close`。これらを実行する step は
  step ID に依存せず対象になり、原因分類・`auto_recover` の設定より優先して常に
  `not_resumable` になる）→ `not_resumable`
- 既に自動再開済みの chain → `exhausted`
- artifact と runner event の決定論的矛盾 → `bug_issue_created`（`type:bug` の Issue を起票）
- triage コメントの投稿に失敗した場合も自動再開しない（handler が必要操作を完遂できていないため）

`resume:` step が失敗した場合は、異常セッションを引き継がず **session 生成元 step へ巻き戻して**
再開する（`discarded_resume_session: true`）。

### 失敗 artifact からの手動 triage（`kaji recover`）

```bash
kaji recover .kaji/wf/official/dev.yaml 288                      # 最新 run を対象に triage を再実行
kaji recover .kaji/wf/official/dev.yaml 288 --run-id 260710120000
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
kaji run .kaji/wf/official/dev.yaml 247 --agent-runner interactive-terminal   # ハイフン
kaji run .kaji/wf/official/dev.yaml 247 --agent-runner headless
```

詳細は [Interactive Terminal Runner ガイド](../cli-guides/interactive-terminal-runner.md) を参照。

## dev（official）/ dev-thorough（custom）

コード変更を伴う Issue のワークフロー。設計 → 設計レビュー → deterministic baseline → 実装 → コードレビュー →
最終チェック → PR → review-poll → close。`custom/dev/dev-thorough.yaml` は同じ骨格を
モデル / effort を厚めにした、このリポジトリ固有の custom variant（kaji の pytest 回帰対象外）。

`official/dev.yaml` / `official/local/dev-local.yaml` と custom の dev-thorough 系は
設計承認直後に共通の
`baseline-precheck` script step を 1 回実行する。artifact と既知 failure ポリシーは
[baseline-check.md](baseline-check.md) を参照する。

各 hand-off 直前（`design → review-design` / `implement → review-code`）には **pre-handoff review** が挟まる（capability-based: Claude Code は `kaji-code-reviewer` subagent、Codex / Antigravity は main-session self-check）。詳細は [development_workflow.md § Pre-Handoff Review](development_workflow.md#prehandoff-review) を参照。

詳細: [development_workflow.md](development_workflow.md)

## dev-small（custom・試験導入）

`custom/dev/dev-small.yaml` は、期待動作と修正範囲が Issue で確定し、大きな設計判断が残らない小修正を
「方針確認・実装・検証」と「独立レビュー・最終確認」の 2 工程で進める軽量 dev workflow。
起動者が明示的に選ぶ（series 自動選択・自動切替の対象外）。このリポジトリ固有の試験導入物で、
kaji の pytest 回帰対象外・starter 非公開。

```text
review-ready → start → baseline（script step）→ change → review-change → pr → review-poll → close
                                  修正ループ: review-change ─RETRY→ fix-change → verify-change ─RETRY→ fix-change
```

| step | skill | 役割 |
|------|-------|------|
| `change` / `fix-change` | `issue-small-change-execute` | 短い方針 → baseline scope 評価 → 実装・テスト・docs → 差分確認 → commit 前の必須検証 → commit → 報告 |
| `review-change` / `verify-change` | `issue-small-change-review` | 実装 session と別 context で実 diff と受け入れ条件を確認、自身で品質検証、完了条件の `[x]` 更新、PR 前提確認、判定 |

- 設計書・設計レビュー・Pre-Handoff Review・final-check の独立工程を持たない。要件の正本は Issue 本文と
  人間の決定事項で、設計書は要求しない。方針は変更前に報告へ記録するが、承認待ち工程にはしない。
- readiness・worktree・baseline・独立レビュー・PR review・close は維持する。通常成功経路の agent 起動は
  6 回（`official/dev.yaml` は 9 回）。起動回数の差を token 削減率とはみなさない。
- 独立レビューの条件は「実装した session と同じ context でレビューしない」ことだけで、review 系 step は
  `resume:` を持たない別 session で起動する。agent / model の別指定は条件にしない。
- 標準 dev の Pre-Handoff Review が見る Scope 混在・auto-close 規約は、`review-change` の観点に集約する。
- baseline は start の直後に 1 回測定する（[baseline-check.md](baseline-check.md)）。`change` は編集前に確定した
  変更対象 path、`review-change` は実 diff の path を `--evaluate --scope` に渡し、既存の停止基準を適用する。
  commit 前（`change` / `fix-change`）とレビュー側（`review-change` / `verify-change`）の双方が品質検証を実行する:
  `clean` は `make check`、`known_failures` は非 pytest gate 全 PASS と `--compare` の `verdict: ok` かつ `regressions: []`。
- 完了条件のチェックボックスは `review-change` / `verify-change` の PASS 時に確認済み項目だけ `[x]` にする。
  `### ワークフロー完了後の確認項目` は `issue-close` の follow-up 移管対象として残す。
- PR 作成・PR review・close は標準 dev と同じ `i-pr` / `review-poll`（fallback `review`）/ `pr-fix` / `pr-verify` /
  `issue-close` を使う。`review` は dev-small の review PASS marker がある場合に限り、設計書の代わりに Issue の
  決定事項・完了条件で評価する。

### dev-small の適用条件

選択は差分の行数・ファイル数ではなく、残っている設計判断の大きさで行う。次をすべて満たす変更が候補。

- 期待動作・対象・受け入れ条件が Issue で明確
- 大きな設計判断が残らない（公開互換性・権限境界・データ移行・再開処理・状態永続化・merge 条件等の判断を伴わない）
- 影響範囲を説明できる
- 既存検証または局所的な回帰テストで確認できる
- 通常の revert で戻せる
- `### ワークフロー完了後の確認項目` を除く完了条件に、dev-small で満たせない項目がない

「dev-small で満たせない項目」は、充足の証拠または充足範囲の定義が、dev-small にない工程の成果物
（設計書・設計レビュー結果・Pre-Handoff Review 報告・final-check 報告）を前提とする完了条件を指す。
issue-create テンプレート（`issue-feat.md`）由来の「設計書作成」「テスト作成（設計書のテスト戦略に従い S/M/L を網羅）」や、
「影響ドキュメントの更新（設計書『影響ドキュメント』セクション参照）」のように範囲を設計書で定める項目が該当する。
「`make check` 通過」「〜のテストを追加」「特定 docs の同期」など、実 diff・dev-small 自身の検証で照合できる項目は該当しない。
判定に迷う項目は ABORT 側に倒す。

| 区分 | 具体例 |
|------|--------|
| dev-small 候補 | 原因と期待動作が明確な局所的 bug 修正（例: 特定入力での誤ったエラーメッセージ）、テスト修正（例: 環境依存で不安定な assertion の修正）、意味が限定された機械的変更（例: 設定値・定数の置換、決定済みの rename） |
| 標準 dev | 調査後に要件・構成を決める機能開発、公開 CLI・永続化 schema の互換性判断、権限境界、データ移行、再開処理・状態永続化・merge 条件などの設計判断を伴う変更 |
| docs workflow | docs-only の変更（軽量化目的で type を付け替えない） |

### 起動・レビュー前停止・再開

```bash
# 起動（起動者が明示選択）
kaji run .kaji/wf/custom/dev/dev-small.yaml 431

# 独立レビューの前で止める → 人間が差分を確認 → 独立レビュー・最終確認から通常再開
kaji run .kaji/wf/custom/dev/dev-small.yaml 431 --before review-change
kaji run .kaji/wf/custom/dev/dev-small.yaml 431 --from review-change
```

- `--before review-change` での停止は Issue 完了やレビュー承認を意味しない。再開は `--from review-change` で行う。
- 人間レビューを理由に `--from pr` で `review-change` を飛ばす運用、および人間のレビュー結果の自動取り込みは対象外
  （`review-change` は品質検証・完了条件照合・PR 前提確認も担うため）。
- 停止中に commit を追加した場合、`review-change` は HEAD の全体をレビューしたうえで報告 SHA との不一致を指摘して
  RETRY し、`fix-change` が追加差分を確認・検証・報告する。未 commit のまま残した変更は、`review-change` が
  path と原因を報告して RETRY し（品質検証は未実施と記録）、`fix-change` が報告に載った path だけを引き継いで commit する。

### 適用外 ABORT とやり直し

途中で適用外と判明した場合、`change` / `fix-change` / `review-change` / `verify-change` は理由と未完了事項を Issue に
残して ABORT する。worktree・branch・未 commit 変更は保全し、session-state の編集・label 変更・他 workflow の起動はしない。
標準 dev への引継ぎや途中からの再開は仕様に含めない。再実行が必要なら、人間が判断したうえで worktree を含めて初めからやり直す。

1. ABORT 報告を確認し、再実行するか（標準 dev か、原因を解消した dev-small か）を人間が判断する
2. 残す必要のある作業を確認したうえで、既存 worktree / branch を削除する
   （[git-worktree.md § Worktree の削除](../guides/git-worktree.md)）
3. `--from` なしで起動する

```bash
kaji run .kaji/wf/official/dev.yaml 431              # 標準 dev で初めから
kaji run .kaji/wf/custom/dev/dev-small.yaml 431      # 原因を解消して dev-small で初めから
```

完了条件に dev-small で満たせない項目が残っている不整合（§ dev-small の適用条件）は、次のとおり検出して ABORT する。

- `change` は実装前の適用判定（Step 2）で検出し、方針・編集・commit に進まず ABORT する
- `review-change` / `verify-change` は、他の blocking finding の有無に関係なく RETRY より優先して ABORT する
  （同時に見つかった finding は ABORT 報告に併記する）。`verify-change` の確認範囲の限定はこの検査に適用しない
- いずれも Issue 本文の項目を書き換え・削除・チェックしない。人間が、該当項目を dev-small に合わせて本文から変更するか、
  標準 dev に切り替えるかを決める

`session-state.json` の cycle 消費回数は Issue 単位で持ち越される。dev-small の cycle 名（`small-*`）は `official/dev.yaml` と
重ならないため、標準 dev でのやり直しには影響しない。dev-small 自体を再実行した場合は exhaust 済み cycle が入口で再び停止する。
その解除は人間判断で § cycle exhaust からの復旧 の `--reset-cycle` 手順に従う。

### 効果評価

試験導入の効果は、比較可能な小修正を数件試し、モデル・effort・取得可能な cache 条件を添えて所要時間・取得できる token・
差し戻し・重複確認を記録して判断する。未取得値は未取得とし、起動回数の削減や固定の削減率を token 削減の根拠にしない。
継続・見直し・official 化は試験結果から別途判断する。

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
