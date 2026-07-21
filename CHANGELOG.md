# Changelog

All notable changes to kaji are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### BREAKING CHANGE

- **Broken contract**: workflow YAML no longer lives directly under `.kaji/wf/`.
  Every path that names a workflow file — `kaji run` / `kaji validate` /
  `kaji recover` arguments, `.kaji/series/*.yaml` `workflow:` entries, scripts,
  and CI steps — now fails with "File not found" (#352). Workflow YAML is split
  by ownership: `.kaji/wf/official/**` is provided, updated, and
  regression-tested by kaji; `.kaji/wf/custom/**` is owned by the repository.
  No alias, symlink, or fallback for the old paths is provided
  (`docs/adr/008-no-backward-compat-layer.md`).
  - **How to check whether you are affected**: run
    `rg -n '\.kaji/wf/[a-z0-9-]+\.yaml' .` in your repository. Zero hits means
    you are not affected.
  - **How to migrate**: move each workflow to its new location and update every
    hit.

    | Old | New | Ownership |
    |---|---|---|
    | `.kaji/wf/dev.yaml` | `.kaji/wf/official/dev.yaml` | official |
    | `.kaji/wf/docs.yaml` | `.kaji/wf/official/docs.yaml` | official |
    | `.kaji/wf/incident.yaml` | `.kaji/wf/official/incident.yaml` | official |
    | `.kaji/wf/dev-local.yaml` | `.kaji/wf/official/local/dev-local.yaml` | official |
    | `.kaji/wf/docs-local.yaml` | `.kaji/wf/official/local/docs-local.yaml` | official |
    | agent / model / effort variants | `.kaji/wf/custom/<category>/<name>.yaml` | custom |

    If you never customized a shipped workflow, re-copying the new layout is
    enough. Do not edit `official/**` in place — copy it into `custom/**`,
    change `name:` to match the new filename stem, then run `kaji validate`.
    The canonical contract is `docs/dev/workflow-authoring.md` § ファイル配置.
  - **What kaji stops guaranteeing for `custom/**`**: kaji's pytest inventory,
    contract, and structural/routing invariants now target `.kaji/wf/official/**`
    only. Tracked custom YAML is still statically validated (L1 parse/schema,
    L2 workflow reference integrity, L3 skill metadata) by
    `make validate-workflows`, but the following *behavioral* regressions are no
    longer detected by kaji's test suite for workflows you move to `custom/**`:
    the `review-poll` exec argv (`["kaji", "pr", "review-poll"]`), the baseline
    step's agentless configuration and `{"PASS": "implement", "ABORT": "end"}`
    routing, the `description` series auto-selection contract, `review-code`
    routing, self-RETRY cycle membership, and workflow set inventory. This is a
    deliberate trade-off that prioritizes the ownership boundary.
  - **Managed starter**: the starter repository is not changed by this release.
    It follows via the post-release starter-sync process
    (`docs/operations/release/starter-sync-runbook.md`).

## [0.16.1] - 2026-07-20

### Fixed

- Validate the documented types for workflow `name`, `description`, and
  `execution_policy`, plus step `skill`, `model`, and `max_budget_usd`, at the
  YAML parse boundary. Malformed values now raise field-specific
  `WorkflowValidationError` messages instead of leaking `TypeError` or being
  silently accepted (#360).
- Reject an unrepresentable `max_budget_usd` (for example a 1000-digit YAML
  integer) with a field-specific `WorkflowValidationError` instead of letting a
  raw `OverflowError` escape `kaji validate` as a traceback (#360).

## [0.16.0] - 2026-07-18

This release tightens workflow validation and execution preflight, adds a
deterministic test baseline gate, and introduces the managed-starter release
workflow. It also strengthens the human-decision and post-workflow handoff
contracts used by the development lifecycle.

### BREAKING CHANGE

- **Broken contract**: `kaji_harness.recovery.NON_RESUMABLE_STEPS` is removed and
  renamed to `NON_RESUMABLE_SKILLS` (#349). Code that imports the old name now
  fails with `ImportError`. `kaji` CLI execution/arguments/exit codes,
  `recovery.json` schema, and workflow YAML schema are all unchanged.
  - **How to check whether you are affected**: run
    `grep -rn 'NON_RESUMABLE_STEPS' .` in your repository. Zero hits means you
    are not affected.
  - **How to migrate**: replace each hit with `NON_RESUMABLE_SKILLS`. The value
    and meaning are unchanged (`frozenset({"issue-start", "i-pr", "issue-close"})`);
    the rename reflects that recovery now resolves the denylist against
    `Step.skill` instead of the workflow step ID. kaji ships no backward
    compatibility layer (ADR 008); see #349 for the full contract change.

### Added

- Add a deterministic baseline precheck before implementation in all four
  development workflow variants. The check records structured pytest evidence,
  distinguishes known failures from regressions, and prevents implementation
  from starting when the baseline is invalid or blocked (#346).
- Add `update-starter`, `review-starter-update`, and `release-starter` skills,
  deterministic starter release-plan support, and the canonical runbook for
  independently synchronizing and releasing managed starter repositories
  (#341).
- Tighten workflow validation by checking supported agent names, requiring an
  `on.PASS` transition, and rejecting steps that are unreachable from the first
  workflow step (#339).
- Add the explicit `grill-me` interview skill for resolving important decisions
  before Issue creation and recording their provenance in the Issue (#329).
- Add a standalone workflow token-usage measurement tool and baseline, with
  aggregation by workflow, step, agent, model, and effort (#325).

### Fixed

- Resolve the non-resumable recovery gate by `Step.skill` rather than workflow
  step ID, so irreversible skills cannot be resumed automatically through an
  aliased step (#349).
- Apply the shared L1/L2/L3 workflow preflight to `validate`, `run`, `recover`,
  and every member of a series before execution; aggregate validation failures
  and reject stale or invalid series plans before launching work (#338).
- Reject duplicate workflow step IDs before later definitions can be silently
  shadowed during transition, resume, or cycle validation (#355).
- Reject non-string step IDs, resume and cycle references, and verdict keys at
  the parse boundary with `WorkflowValidationError` instead of leaking raw type
  errors or accepting invalid values (#357).

### Docs

- Preserve human-approved critical decisions across review and design, stop on
  unresolved one-way doors, and record decision provenance in the Issue (#328).
- Separate checks that can only happen after merge, deployment, or an external
  response from workflow completion criteria, and create idempotent follow-up
  Issues for unfinished post-workflow checks (#327).
- Reorganize `issue-implement` around progressive disclosure, a compact
  implementation quick reference, and delayed loading of handoff material
  (#326).

### Internal

- Update GitHub Actions dependencies to Node 24-native major versions and pin
  the affected action references (#336).
- Update workflow agent/model variants and add plans for sequential Issue
  series.

## [0.15.0] - 2026-07-14

This release adds a sequential series runner that drives an ordered list of
Issues through their workflows, and completes the decomposition of the
monolithic `cli_main.py` into a layered `commands/` package with enforced module
boundaries. The `cli_main` compatibility shim is removed in the process.

### BREAKING CHANGE

- **Broken contract**: the `kaji_harness/cli_main.py` re-export compatibility shim
  is removed (#284). Code that imports or monkeypatches command handlers, helpers,
  or `subprocess` through `kaji_harness.cli_main` now fails with `ImportError` or
  `AttributeError`. The console entry point (`kaji = kaji_harness.cli_main:main`)
  is unchanged, so running the `kaji` CLI as a command is unaffected.
  - **How to check whether you are affected**: run
    `grep -rn 'kaji_harness\.cli_main\.\|from kaji_harness\.cli_main import' .`
    in your repository. Zero hits means you are not affected.
  - **How to migrate**: point each hit at the concrete module under
    `kaji_harness/commands/` (`commands.run`, `commands.issue`, `commands.pr`,
    `commands.config`, and so on). Test patch targets move the same way:
    `kaji_harness.cli_main.subprocess.run` becomes
    `kaji_harness.commands.<module>.subprocess.run`. kaji ships no backward
    compatibility layer (ADR 008); see #283 / #284, `docs/ARCHITECTURE.md`, and
    `docs/dev/testing-convention.md` for the full boundary description.

### Added

- A sequential series runner that drives an explicitly ordered list of Issues
  through their workflows: `kaji validate-series` and `kaji run-series` (with
  `--dry-run` and `--resume`), plus a `/series-create` skill that generates a
  validated series definition without starting execution. The runner advances to
  the next member only after the preceding workflow exits successfully and its
  Issue is closed with reason `completed` (#313).

### Fixed

- Exclude interactive-runner launch failures that occur outside a tmux session
  from automatic incident recording; these are missing preconditions rather than
  failures worth filing repeatedly (#322).
- Preserve degenerate cache entries in `list_issues` so that cache rebuilds no
  longer drop entries (#323).
- Apply the closed-issue preflight to resumed series members as well, so already
  closed Issues are not re-run (#313).

### Docs

- Added a README section comparing kaji with other agent orchestration tools
  (#314).
- Aligned `/i-pr` and the shared skill rules with the finalized closing-keyword
  policy, and distinguished the linked-PR path from the commit-message path in
  the github-mode and commit-flow guides (#318).

### Internal

- Mechanically split `cli_main.py` into the `kaji_harness/commands/` package
  (#283).
- Removed cross-module imports of private symbols and made private imports through
  sub-package facades a forbidden pattern (#285, ADR 009).
- Moved domain logic out of the CLI layer (local issue commits, recovery target
  selection, worktree note composition, verdict marker resolution) into the
  provider and service layers, and enforced runtime layer import direction with a
  fitness test (#286).
- Split runner and local provider responsibilities (#323).
- Migrated test imports to the concrete modules under `commands/`, removing the
  last dependencies on the `cli_main` shim (#284).
- Updated workflow agent and model assignments (dev-thorough-fable, docs-fable).

## [0.14.0] - 2026-07-13

This release adds two-layer incident management, automated workflow failure
triage and one-shot recovery, and explicit recovery from exhausted workflow
cycles. It also improves provider-error handling, run artifact isolation, and
Codex tool-result readability.

### Added

- Incident detection and aggregation based on redacted failure signatures.
  New incidents open GitHub Issues, recurring occurrences append comments, and
  transient incidents can be closed automatically; local fail-open occurrence
  records and an incident label taxonomy are included (#304).
- A second-layer incident investigation workflow with investigate, review, fix,
  verify, and report stages, structured evidence artifacts, and a convergent
  review cycle (#305).
- Workflow failure triage for ERROR and eligible ABORT outcomes, including
  structured reports, recovery-chain budgeting, Issue comments, and an optional
  one-shot automatic resume after transient failures (#288).
- `kaji run --reset-cycle`, used with `--from <step>`, as the supported way to
  reset an exhausted target cycle before resuming a workflow (#189).
- `kaji config artifacts-dir` so incident workflows resolve their shared
  artifact root from the main worktree instead of the current feature worktree
  (#305).

### Fixed

- Treat model-capacity errors embedded in interactive-terminal transcripts as
  transient recovery candidates while preserving authentication and permission
  markers for the sensitive-failure safety gate (#296).
- Normalize YAML-forbidden control characters at the verdict parsing boundary,
  record sanitization findings without leaking raw control characters, and
  persist sanitized verdict artifacts (#298).
- Decode doubly encoded Unicode escapes in Codex MCP tool results for human
  readable console output while retaining raw logs and safely handling control
  characters and lone surrogates (#137).
- Allocate unique, second-resolution run directories with deterministic numeric
  suffixes, preventing rapid reruns from sharing logs and artifacts (#292).
- Retry Claude Extended Thinking block-mutation responses through the existing
  transient CLI retry path (#213).
- Resolve recovery child workflow paths before changing working directory,
  enforce provider requirements during recovery, and strengthen recovery budget
  and state validation (#288).

### Docs

- Added incident operations, failure recovery, cycle-reset, logging, workflow,
  and configuration guidance together with the corresponding design records
  (#137, #189, #288, #296, #298, #304, #305).
- Added the `epic` metadata label and documented handling for Epic parent Issues
  (#287).

### Internal

- Expanded `cli_main` characterization coverage and added a reproducible patch
  target inventory ahead of its planned decomposition (#282).
- Updated Codex workflow model assignments and tuned implement / fix-code
  timeouts for thorough workflows.
- Removed redundant verdict injection from fix steps that already use Issue
  comment fallback.

## [0.13.0] - 2026-07-09

This release publishes kaji as an installable PyPI package, adds the Trusted
Publisher release pipeline, and includes workflow reliability fixes and the
documentation translation refresh since 0.12.1.

### BREAKING CHANGE

- **壊れる契約**: `issue-design` Step 1.6 の BACK 経由再起動検出は、判定コメントの
  見出し・checkbox 表現（`[x] Changes Requested / BACK` / `| 判定 |` テーブル / 判定
  見出しゲート）を **読まなくなった**。検出対象は `kaji issue comment
  --verdict-step/--verdict-status` が付与する body 1 行目マーカー
  `<!-- kaji-verdict: step=<step> status=<STATUS> -->` のみ。マーカーを付与しない判定
  コメントは BACK 再入として検出されず、「設計書あり + 設計後コミットあり + マーカー
  なし」の曖昧状態は上書きに進まず ABORT で停止する（#261）。
  - **影響の判定方法**: 下流 repo で
    `grep -rn 'Changes Requested / BACK' .claude/skills/` がヒットする場合、旧 consumer を
    保持しており更新が必要。producer 側は
    `grep -rln 'kaji issue comment' .claude/skills/issue-review-code .claude/skills/i-dev-final-check`
    等で判定コメント投稿箇所を特定する。
  - **適用指針**: 未カスタマイズなら該当 SKILL.md の再コピー + kaji 本体更新で完結。
    カスタマイズ済み repo は、producer 側は判定コメント投稿コマンドへ
    `--verdict-step <step> --verdict-status <STATUS>` を付与（呼び出し 1 行の差し替え）、
    consumer 側は本 PR の `issue-design` Step 1.6 diff を自版へ移植する（上流 PR: #261）。

### Added

- PyPI publish pipeline: `.github/workflows/publish-pypi.yml` now builds
  distributions on GitHub Release `published`, runs strict metadata checks,
  smoke-tests the wheel entry point through `uv tool install`, and publishes via
  PyPI Trusted Publisher using the `pypi` GitHub environment (#270).
- PyPI package metadata and install documentation. `pyproject.toml` now carries
  project URLs plus author / maintainer metadata, and the READMEs document
  `uv tool install kaji` as the primary install path (#270).
- `kaji issue comment` に verdict マーカー付与機能（`--verdict-step` /
  `--verdict-status`）を追加。CLI が comment body 1 行目に決定的な HTML マーカー
  `<!-- kaji-verdict: step=<step> status=<STATUS> -->` を埋め込み、cross-skill 契約
  （BACK 再入検出）を CLI 層に固定する。github / local 両 provider で同一の振る舞い、
  語彙検証は fail-loud（#261）。
- Python starter repository guide and links from the top-level documentation
  indexes (#242).

### Fixed

- Fixed the `issue-design` BACK detection command, local-provider self-RETRY
  cycle membership, and the mutating `make check` format gate. `make check` now
  uses format checks, while mutating formatting is explicit through `make fmt`
  (#259).
- BACK 判定コメントの producer / consumer 契約不一致を恒久修正。旧 consumer regex
  （`[x] Changes Requested / BACK`）は producer が一度も出力しておらず、BACK 経由の
  design 再入が常に「初回起動」と誤判定され既存設計書を上書きしうる欠陥だった。
  producer は全判定コメントで verdict マーカーを無条件付与し、consumer はマーカーのみを
  参照する（#261）。
- Fixed the publish workflow action pinning and wheel smoke-test install syntax
  before the first PyPI release (#270).

### Docs

- Reworked the public README workflow diagram and terminal demo assets so the
  repository front page reflects the current GitHub workflow (#269, #277, #279).
- Switched the configuration reference and several user-facing docs to the
  English-canonical + `.ja.md` companion policy, with updated indexes and
  language-switcher links (#264, #265, #266, #268, #272).
- Added and refined configuration reference documentation, including standard
  GitHub operation examples and README config snippets (#253).
- Added root `AGENTS.md`, reduced `CLAUDE.md` to Claude-specific notes, and
  included `AGENTS.md` in `make verify-docs` (#243, #256).
- ADR 008「後方互換レイヤを提供しない（BREAKING 明示ポリシー）」を追加。付随して
  `shared_skill_rules.md` / `skill-authoring.md` / `release` skill / cli-guides に契約と
  BREAKING 3 要素要件を反映（#261）。

## [0.12.1] - 2026-06-24

Maintenance release. No external API or runtime behavior changes: internal
cleanup of the `review-poll` CLI dispatch, workflow inventory consolidation,
and documentation.

### Changed

- Split `review-poll` out of the shared `kaji pr` builtin dispatch into its own
  branch in `_handle_pr`, removing the `del repo_override` workaround and
  documenting in code that it intentionally accepts no repo argument (repo is
  resolved from `KAJI_GIT_REMOTE` / the git remote, not a CLI flag) (#246).
- Consolidated the tracked workflows into the five GitHub/local everyday
  workflows (#247).

### Fixed

- Dispatch the public `review-poll` step through the `kaji` CLI entry point.
- Restored test isolation so the console progress logging test no longer
  pollutes the kaji root logger (#250).

### Docs

- Refreshed README for the public launch and added design notes for #246 /
  #247 / #250.

## [0.12.0] - 2026-06-08

This release adds the interactive terminal runner path for subscription CLI
usage, introduces direct `exec` workflow steps, and improves workflow
observability through structured verdict artifacts, attempt results, and
console progress logging.

### Added

- `interactive_terminal` agent runner. `kaji run` can now launch normal
  Claude/Codex CLI sessions in tmux-backed panes and resolve step completion
  from artifact-primary `verdict.yaml` files (#224, #230).
- `exec` workflow step type. Workflow YAML can now declare deterministic
  subprocess steps directly, separate from LLM agent steps and `exec_script`
  skill wrappers (#205).
- Artifact-primary verdict resolution and attempt-scoped log layout. Verdicts
  are read from `verdict.yaml` first, then issue comments and stdout fallback;
  step logs now live under `attempt-NNN/` directories (#220).
- Per-attempt `result.json` artifacts and attempt-aware `run.log` events for
  exit status, signal, timing, and session metadata (#222).
- `kaji issue prepend-note`, a deterministic helper used by `/issue-start` to
  prepend worktree notes without model-dependent markdown formatting drift
  (#200).
- `full-cycle-xhigh` workflow variant (#220).

### Changed

- The interactive terminal runner now uses tmux as the single backend, replacing
  the earlier kitty/proc-scan prototype with Linux/macOS-aligned tmux pane
  management (#230).
- tmux agent panes are kept to a right-column maximum of two managed panes,
  pruning older kaji-created panes while preserving user-created panes (#238).
- `review-poll` builtin workflow steps now use the `exec` step type directly,
  removing ignored agent fields and the extra skill dispatch layer (#234).
- Interactive terminal pane launch progress now includes `step`, `agent`, and
  `timeout` fields in INFO logs for easier run tracking (#232).

### Fixed

- Fixed `/issue-start` note insertion so the blockquote/body blank line is
  preserved through a deterministic Python path instead of heredoc reproduction
  by an agent (#200).
- Fixed attempt result persistence around verdict parse errors, same-second
  issue comments, terminal success cleanup, and voluntary process exits (#220,
  #222).
- Fixed interactive terminal edge cases including packaged wrapper discovery,
  early terminal exit detection, truecolor environment setup, and non-fatal
  tmux `pipe-pane` failures after a verdict is already present (#224, #230).
- Fixed verdict artifact ordering relative to issue comments (#220).
- Added review-poll heartbeat output during API retry and eyes-grace sleeps
  (#235).

### Docs

- Added ADRs for artifact-primary verdicts, attempt result JSON, and the
  interactive terminal runner (#220, #222, #224).
- Added and updated CLI/developer documentation for interactive terminal
  execution, workflow authoring, skill authoring, logging, and provider-specific
  guides (#205, #224, #230, #238).
- Archived interactive terminal PoC evidence and design records for the tmux
  backend work (#224, #230).

## [0.11.2] - 2026-06-01

### Fixed

- resumed workflow が Issue の現在ラベルから毎回 `worktree_dir` / `branch_name`
  を再合成していたため、`review-ready` 等で `type:*` ラベルが workflow 途中に
  追加されると `exec_script` step（例: `review-poll`）の cwd が `issue-start`
  で作成された worktree から乖離し `FileNotFoundError` で workflow が ERROR
  終了していた問題を修正。`SessionState` に「初めて physical に存在を確認した
  worktree/branch」を構造化保存し、以降の run/step ではそちらを `IssueContext`
  の正本として override する。旧 kaji 版で作られた `session-state.json` に対しては
  `git worktree list --porcelain` から backfill で救済する (#218)。
- `review_poll_entry` が `KAJI_WORKTREE_DIR` 不存在時に Python traceback で
  死んでいた問題を修正。存在検査を追加し ABORT verdict として診断可能にする (#218)。

### Internal

- `kaji_harness/worktree_discovery.py` を追加: `discover_existing_worktree()` /
  `AmbiguousWorktreeError`。known prefix + 規約準拠 basename + physical
  existence の 3 条件 AND で SessionState backfill 候補を発見する (#218)。
- `SessionState` に `worktree_dir` / `branch_name` の Optional フィールドと
  `capture_worktree()` 冪等 helper を追加。既存 state JSON は新規 key 不在でも
  load 可能（後方互換） (#218)。
- `release` skill を `.agents/skills` からも参照できるよう symlink を追加。

## [0.11.1] - 2026-05-31

`paths.worktree_prefix` config option を追加し、consumer プロジェクトの
worktree ディレクトリ prefix が `kaji-` 以外の場合に `build_worktree_dir()`
が FileNotFoundError を起こす問題を修正したパッチリリース。

### Fixed

- `build_worktree_dir()` が worktree prefix を `kaji-` でハードコードしていた
  問題を修正。`[paths].worktree_prefix` config option として外部化し、未設定時は
  従来どおり `kaji-` prefix にフォールバックして後方互換を維持 (#215, #216)。

### Internal

- `PathsConfig` に `worktree_prefix: str = ""` フィールドを追加。空文字は未設定を
  意味し、`build_worktree_dir()` 内で `"kaji"` にフォールバックする（区切りの
  ハイフンは連結側が付与）。非空値は安全な単一パスセグメントとして validation 済み (#215)。
- local provider M-1/M-2 worktree_prefix plumbing テストを追加 (#215)。

## [0.11.0] - 2026-05-29

GitLab forge 対応を撤去し GitHub 単独運用へ回帰したリリース。あわせて
skill frontmatter の `exec_script` による LLM 中継なし script step、codex
auto-review polling 用の `review-poll` skill、final-check / review-code の
BACK verdict 分割など workflow 周りの改善を含む。

### Added

- `exec_script` skill frontmatter — LLM 中継を挟まず deterministic に script
  step を dispatch する仕組み (#204)。
- `review-poll` skill — codex auto-review (chatgpt-codex-connector[bot]) の
  reactions / reviews を polling し PASS / RETRY / BACK_FALLBACK を判定する
  (#182)。
- final-check の `BACK` verdict を `BACK_DESIGN` / `BACK_IMPLEMENT` に分割し、
  差し戻し先（design / implement）を verdict 自体で表現できるようにした
  (#158)。
- github PR auto-injection と `kaji sync from-github`（gl:34）。

### Changed

- GitLab forge 対応を完全撤去し GitHub 単独運用に切り替え。`glab` 依存・
  GitLab provider・関連 workflow の `requires_provider: gitlab` を削除
  (#191)。
- release-please 資産を削除し、release 運用を `/release` skill 単独に統一
  (#195)。
- `Step.max_turns` を廃止し、CLI guide を追従更新 (#167)。
- forge workflow の `requires_provider` を `any` に緩和 (#169)。

### Fixed

- `issue-review-code` Step 1.4 hard gate の `BACK` が approve 済み設計を
  再起動して ABORT する意味衝突を、専用 verdict `BACK_IMPLEMENT` の導入で
  解消 (#192)。
- `verify-docs` がコードブロック内の正規表現を broken link と誤検出する
  欠陥を、escape-aware scanner + markdown-it-py への切り替えで修正 (#190)。
- verdict 不在時に AI formatter が PASS を捏造する問題を、delimiter 存在を
  gate にすることで抑止 (#193)。
- Codex の stream-level error event を recoverable として扱い、失敗判定から
  除外 (#196)。
- GitHub self-PR で `kaji pr review --request-changes` / `--approve` を
  marker comment fallback で成立させる (#199, #186)。
- `review-poll` の `exec_script` が `--jq` スカラー出力を JSON parse して
  失敗する問題を、生文字列解決で修正 (#209)。
- `artifacts_dir` を main worktree 基準で解決 (#177)。
- `_forward_to_gh` / `_forward_to_glab` が inline `--repo` / `-R` 形式を
  検出できない欠陥を修正 (#172)。
- `issue-implement` Step 7.6 Pre-Handoff Review の順序矛盾を修正（commit
  step の後段へ移動）(#171)。
- linked worktree での provider overlay 乖離を WARN するよう修正（gl:28）。

### Docs

- bug 証跡 rubric に、実世界障害ログによる実装前 Red 代替の escape clause を
  追加 (#211)。

## [0.10.1] - 2026-05-18

### Fixed

- `kaji run` の terminal step 後始末で、kaji 自身が撃った
  `process.terminate()` 起因の returncode を失敗判定から除外。Claude Code
  CLI は SIGTERM を trap し shell 慣例の正値 143 で exit するため、成功した
  terminal success ステップが誤って `CLIExecutionError` 化される不具合を
  修正 (gl:25)。

### Changed

- GitLab を tracked repository の既定 forge に昇格。`.kaji/config.toml` の
  `provider.type` を `github` → `gitlab`、builtin workflow
  (`implement-to-pr.yaml` / `feature-development-light.yaml`) の
  `requires_provider` を `gitlab` に変更。CLAUDE.md の forge ガイダンスも
  GitLab 前提に更新。

### Internal

- 開発フェーズ番号ベース命名の 15 テストファイル（`test_phaseXX*.py`）を
  ドメインベース命名に正規化し、命名規約を `testing-convention.md` に明文化。
  テストロジック・テスト ID・公開 IF は不変 (gl:30)。

## [0.10.0] - 2026-05-17

This release makes a **multi-provider architecture** the backbone of
kaji. Issue / PR operations now route through a `Provider` abstraction
with GitHub, GitLab, and local-filesystem backends, and a `[provider]`
section is now mandatory in `.kaji/config.toml`. It also adds a GitLab
provider (`provider.type='gitlab'`) and a `review-cycle` workflow.

### BREAKING CHANGE

- `[provider]` section is now **required** in `.kaji/config.toml`.
  `kaji issue` / `kaji pr` / `kaji run` exit 2 with a setup message if it
  is missing. Previously, missing `[provider]` triggered a one-time WARN
  and fell back to GitHub passthrough.
- `.kaji/config.toml` itself is now required to invoke `kaji issue` /
  `kaji pr`. The legacy passthrough that forwarded these commands to
  `gh` outside of a kaji repository has been removed.
- `provider.local.machine_id` is now validated at config load time
  (must match `[a-z0-9]{1,16}`). Hand-edited `config.local.toml` with
  invalid values (`PC1`, `pc-1`, 17+ characters, etc.) now fails fast
  with `ConfigLoadError` instead of crashing later in `kaji issue` /
  `kaji run`.
- `kaji pr ...` (including `pr create` / `pr list` / `pr review-comments`
  / `pr reviews` / `pr reply-to-comment`) now exits 2 with a `forge-only`
  error message when run under `provider.type='local'`. Previously, the
  call was passed through to `gh pr` even in local mode, risking
  accidental PR creation against the GitHub remote.
- `kaji run` now validates that `workflow.requires_provider` matches
  `config.provider.type` before dispatching the runner. Mismatches exit 2
  with a switching guide (e.g. running `feature-development.yaml` under
  `provider.type='local'` exits 2 instead of stopping mid-workflow at the
  `i-pr` step).
- `prompt.build_prompt(...)` requires `issue_context: IssueContext` (no
  longer Optional). All callers must pass the resolved `IssueContext`
  from `WorkflowRunner._resolve_run_issue_context()`. The internal
  `if issue_context is not None:` fallback paths have been removed.

### Added

#### Provider abstraction & local mode

- `IssueProvider` Protocol + `IssueContext` providing 9 context variables
  (`issue_id`, `issue_ref`, `issue_input`, `branch_prefix`,
  `branch_name`, `worktree_dir`, `design_path`, `provider_type`,
  `default_branch`; `step_id` continues to come from the step
  definition).
- `LocalProvider` for GitHub-independent issue management
  (`.kaji/issues/<id>-<slug>/issue.md`, file-based CRUD, POSIX flock for
  the ID counter, atomic frontmatter writes via `os.replace`).
- `kaji local init` CLI (overlay-only: writes `.kaji/config.local.toml`,
  never the tracked `.kaji/config.toml`; hostname-based machine_id
  candidate; `.gitignore` integration).
- `kaji config provider-type` — read-only subcommand that prints the
  resolved provider type (`github` / `local` / `gitlab`) on stdout.
- `Workflow.requires_provider` field (`"github"` / `"local"` /
  `"gitlab"` / `"any"`, default `"any"`). Declares which provider type a
  workflow expects; builtin `.kaji/wf/*.yaml` declare it explicitly.
- `feature-development-local.yaml` workflow (final step is `issue-close`
  instead of `i-pr`; no PR concept under local mode) and
  `docs-maintenance-local.yaml` (lets `type:docs` issues run under
  `provider.type='local'` without hitting the bare-provider PR guard).
- `kaji_harness/providers/_mappings.py` `LABEL_TO_PREFIX` table —
  canonical source of `type:* label → branch_prefix` mapping.
- ID normalization across `local-<machine>-<n>` / `<machine>-<n>` /
  numeric / `gh:N` / `gl:N` forms.
- Step 0 provider-check guard in the `pr-fix` / `pr-verify` / `i-pr`
  skills — forge-only skills ABORT under `provider.type='local'` with
  guidance toward the bare-mode alternatives.
- `docs/operations/local-mode-runbook.md` — operations runbook covering
  single-PC / multi-PC setup, the daily Issue lifecycle, code
  synchronisation strategy, forge migration judgement criteria, and
  troubleshooting.

#### GitLab provider

- `GitLabProvider` — `provider.type='gitlab'` backed by the `glab` CLI
  (mutating ops) and `glab api` (reads). 8-method `IssueProvider`
  implementation with `GitLabProviderConfig` and config-overlay support.
- `kaji issue` / `kaji pr` GitLab passthrough with a `gl:N` issue-id
  form. Skill-facing args stay GitHub-shaped (`--body`, `edit`,
  `comment`, `--base`, `--head`); the dispatcher rewrites them to `glab`
  equivalents. Unsupported subcommands are rejected with exit 2 instead
  of being silently passed through.
- `GitLabProvider.resolve_pr_context()` + `PRContext` dataclass —
  resolves the MR for the current branch and injects `pr_id` / `pr_ref`
  into skill prompts.
- `kaji sync from-gitlab` / `kaji sync status` — fetch GitLab issues into
  a local read cache with an all-or-nothing 3-phase contract
  (fetch → stale check → atomic write) and paginated retrieval.

#### Workflows & skills

- `review-cycle.yaml` / `review-close.yaml` workflows and the
  `/review-cycle` skill — drive the `review → pr-fix ⇄ pr-verify` loop
  (and optionally `issue-close`) with a single command.

### Changed

- `kaji issue` / `kaji pr` dispatch now routes through the
  `get_provider()` factory; `--repo` is auto-injected when
  `[provider.github] repo` is configured.
- `cmd_run` validates the provider configuration before constructing the
  runner; `[provider]` misconfiguration is reported as exit 2 and no
  longer surfaces as an `IssueContextResolutionError` at exit 3.
- `LocalProvider.close_issue(reason=None)` now writes
  `close_reason: "completed"` (was an empty string), aligning with the
  GitHub Issue API default.
- Repositioned local-mode from "BCP for GitHub outage" to "primary SoT
  during validation period"; GitHub recovery is no longer a precondition
  for the project.

### Fixed

- `kaji run` step が CLI セッションの terminal event（Claude/Gemini
  `type:"result"` / Codex `turn.completed` / `turn.failed`）受信後も
  stdout EOF を待ち続けて `default_timeout` まで blocking する不具合を
  修正。`CLIEventAdapter` に `is_terminal_event` / `is_terminal_failure`
  を追加し、stream loop が terminal event 観測時に break して
  `terminate -> wait(5) -> kill` で後始末する。timer は最終ガードとして
  温存し、`terminal_seen` 観測時は `timer.cancel()` 先行で grace wait
  中の race を構造的に排除。Claude/Gemini の failure terminal は
  `is_terminal_failure` で `CLIExecutionError` に伝搬する。
- `kaji issue create/edit/comment` now accept `--body-file` (and `-` for
  stdin) under the GitLab provider; the flag is expanded to `--body`
  before reaching `glab`, restoring contract parity with the GitHub and
  local providers.

### Removed

- WARN-then-fallback path for a missing `[provider]` section (now
  fail-fast — see BREAKING CHANGE above).
- Legacy `kaji issue` / `kaji pr` passthrough outside kaji repositories
  (now fail-fast — see BREAKING CHANGE above).

### Internal

- Migrated the skill suite from kamo2: `_shared/` rewrite, `docs/dev`
  workflow-doc renames, lifecycle / readiness / PR-gate skills, and the
  `i-pr` / `i-dev-final-check` / `i-doc-final-check` skills.
- Local-mode Phase 1/2 scaffolding: `kaji issue` / `kaji pr` wrappers,
  str-typed issue ids, and `kaji pr review-comments` / `reviews` /
  `reply-to-comment`.
- Hardening: `resolve_main_worktree()` fail-fast, `LocalProvider`
  `repo_root` pinned to the main worktree, and `large_local` subprocess
  E2E fixtures (pytest markers `large_local` / `large_forge`, target
  `make test-large-local`).
- `CodexAdapter` `command_execution` / `file_change` / `web_search`
  rendering was merged and then reverted within this release window — no
  net change in 0.10.0.

### Migration

For existing GitHub-based usage, add to `.kaji/config.toml`:

    [provider]
    type = "github"

    [provider.github]
    repo = "<owner>/<repo>"

For local-first usage, run `kaji local init` (creates
`.kaji/config.local.toml` overlay). See `docs/cli-guides/local-mode.md`.

For **custom workflow YAMLs** that include forge-only skills (`i-pr` /
`pr-fix` / `pr-verify` / direct `kaji pr` invocations), add to opt into
the new fail-fast guard:

    requires_provider: github

The default value `any` keeps existing custom workflows running, but the
guard will not catch provider mismatches until the field is set. See
`docs/dev/workflow-authoring.md` for details.

The exit-code contract is now:

- Configuration / provider setup problems → exit 2
  (`EXIT_INVALID_INPUT` / `EXIT_CONFIG_NOT_FOUND`)
- Issue resolution problems (missing local issue dir, agent CLI not
  found, runtime exceptions) → exit 3 (`EXIT_RUNTIME_ERROR`)
