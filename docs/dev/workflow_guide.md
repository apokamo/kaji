# ワークフローガイド

ワークフローの選択基準と各ワークフローへのポインター。
ワークフロー全体の概要は [workflow_overview.md](workflow_overview.md) を参照。

## 通常運用 workflow（5 本）

通常運用で使う workflow は GitHub provider 3 本 + local provider 緊急 fallback 2 本の
計 5 本に固定する。

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

## 途中開始・途中終了・単発実行（`--from` / `--before` / `--step`）

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
