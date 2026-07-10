---
status: implemented
phase: 3d
parent: phase3d-design.md
created: 2026-05-06
branch: feat/local-phase3d
---

# [実装報告] kaji local mode — Phase 3-d: local init + workflow + Skill 5 変数移行

`phase3d-design.md` の 9 項目スコープを `feat/local-phase3d` ブランチで実装した
報告。GitHub が利用不可のため、本書を Issue / PR の代替トレースとして残す。

## 結果サマリ

- **ブランチ**: `feat/local-phase3d`（main から派生）
- **コミット数**: 12（design の 9 commit 計画 + lint fixup 1 + 実装報告 1 + レビュー反映 1）
- **`make check`**: 全段緑（ruff check / ruff format / mypy / pytest）
- **テスト**: 956 passed, 1 skipped
- **新規テスト**: 50+ 件（test_phase3d_default_branch / test_local_init / test_phase3d_skills + 既存ファイル拡張 + レビュー反映で追加した default_branch validation）

## 実装サマリ（コミット順）

| # | commit | 範囲 |
|---|--------|------|
| 1 | `8d7b551 feat(workflow)` | `.kaji/wf/feature-development-local.yaml` 追加（`kaji validate` 通過確認）|
| 2 | `e9d8cce feat(providers)` | `IssueContext.default_branch` / `GitHubProviderConfig.default_branch` 追加 + Small/Medium テスト 12 件 |
| 3 | `69a7c8c feat(cli)` | `kaji local init` CLI 実装（overlay-only 上書き仕様、Medium テスト 13 件）|
| 4 | `d3d2b1c feat(prompt)` | `prompt.build_prompt` に `[provider_type]` / `[default_branch]` 注入 + Small テスト 2 件 |
| 5 | `5494c47 refactor(skills)` | Group A: issue-* / i-dev-final-check / _shared 13 ファイルの placeholder 統一 |
| 6 | `9b2e84e refactor(skills)` | Group B: i-doc-* 5 ファイルの placeholder 統一 |
| 7 | `96e0a9c refactor(skills)` | Group C: i-pr / pr-* 3 ファイルの placeholder 統一 + 静的検証テスト追加 |
| 8 | `86bb04c feat(close)` | issue-close SKILL.md provider 分岐 + 6-step 完全反映 + `LocalProvider.close_issue` default `"completed"` |
| 9 | `820984f chore(repo)` | dev repo dogfooding（`.kaji/config.toml`、`.gitignore`、`docs/cli-guides/local-mode.md`、cross-reference 注記）|
| + | `4aae90d chore(phase3d)` | mypy + ruff format lint fixup |

## 主要な決定の反映

### `kaji local init` の overlay-only 上書き仕様（phase3d-design.md § 3）

active provider 値（`type` / `machine_id` / `default_branch`）はすべて
`.kaji/config.local.toml` (gitignored) に書き、tracked `.kaji/config.toml`
は touch しない。

- `phase3-design.md` § `kaji local init` 仕様 Step 4-5 を上書き
- `phase3-design.md` 該当節に cross-reference 注記を追記済み
- dev repo の `.kaji/config.toml` は repository default（`type = "github"`）を
  commit しており、各 user は `kaji local init` で overlay を作って切替可能

### machine_id 解決順（phase3d-design.md § 6）

1. `--machine-id <name>` 明示（`validate_machine_id` で `[a-z0-9]{1,16}` 検証）
2. `socket.gethostname()` を sanitize（lowercase / 英数字のみ / 16 文字切り詰め）
3. `pc1` / `pc2` / … fallback（既存 `.kaji/issues/local-*` と衝突しない最小 N）

すべての経路で `validate_machine_id` を explicit に呼び、文法違反は exit 2。

### `[provider_type]` / `[default_branch]` placeholder（phase3d-design.md § 2）

- `IssueContext.default_branch: str = "main"` を追加
- `GitHubProviderConfig.default_branch: str = "main"` を追加
- LocalProvider / GitHubProvider の `resolve_issue_context` で値を流す
- `prompt.build_prompt` で 2 placeholder を Skill prompt に注入
- `IssueContext is None` の fallback 経路では新規 2 変数も注入しない（互換）

### Skill 21 ファイルの placeholder 統一（phase3d-design.md § 1）

| 旧 | 新 |
|----|----|
| `[worktree-absolute-path]` | `[worktree_dir]` |
| `[branch-name]` | `[branch_name]` |
| `[design-path]` | `[design_path]` |
| `[issue-input]` | `[issue_input]` |

`tests/test_phase3d_skills.py` で hyphen / 山括弧 8 パターンの forbidden list を
全 Skill markdown に対し grep 検証。新規混入も回帰検知できる。

### `issue-close` Skill の provider 分岐（phase3d-design.md § 7）

- `[provider_type]` で github / local 経路を分岐
- local 経路は design.md L972-996 の 6 step を完全反映:
  1. Preflight check（uncommitted / branch / base 確認）
  2. Base branch 最新化（`git fetch` + `merge --ff-only`）
  3. Merge 実行（`git merge --no-ff --no-edit`）
  4. Issue frontmatter 更新 + commit（`kaji issue close [issue_id] --reason completed`）
  5. Cleanup（`git worktree remove` → `git branch -d`）
  6. Push（remote ありなら `git push origin [default_branch]`）
- Step 4 完了で close 確定、Step 5/6 失敗は警告のみ
- Skill markdown 内で `--reason completed` を明示

### `LocalProvider.close_issue` default 修正

- `meta["close_reason"] = reason or ""` → `reason if reason else "completed"`
- design.md L985 の `close_reason: completed` 仕様、状態遷移図 L1011-L1015、
  GitHub Issue API default と整合
- 明示値 (`not-planned` 等) はそのまま保持
- 既存テスト `test_close_without_reason_persists_empty` を
  `test_close_without_reason_defaults_to_completed` にリネームし、
  空文字 / 明示値の挙動も追加で検証

## 受け入れ条件チェック（phase3d-design.md § 受け入れ条件）

### 機械検証

- [x] `ruff check kaji_harness/ tests/` PASS
- [x] `ruff format --check kaji_harness/ tests/` PASS
- [x] `mypy kaji_harness/` PASS
- [x] `pytest` PASS（925 passed, 1 skipped）
- [x] `kaji validate .kaji/wf/feature-development-local.yaml` 0 件警告で通過
- [x] `kaji local init` が `.kaji/config.local.toml` を生成し、`.kaji/config.toml`
      は touch しない、`.gitignore` に行を追記（Medium テストで構造的に確認）
- [x] `kaji local init --machine-id PC1` / `pc-1` が exit 2
- [x] `kaji local init` 2 回目実行が exit 3 で abort
- [x] `kaji local init --default-branch develop` が overlay に反映
- [x] `grep -rE '\[worktree-absolute-path\]|\[branch-name\]|\[design-path\]|\[issue-input\]' .claude/skills/` が 0 件
- [x] 山括弧形式も 0 件（`tests/test_phase3d_skills.py` で回帰検知）
- [x] `prompt.py` が `IssueContext` 由来で `provider_type` / `default_branch` を注入
- [x] `IssueContext.default_branch` が provider 別 source から供給される
- [x] `issue-close` SKILL.md が 6-step / `--reason completed` を含む（静的検証）
- [x] `LocalProvider.close_issue(reason=None)` が `close_reason: "completed"`
- [x] `LocalProvider.close_issue(reason="not-planned")` が明示値を保持
- [x] dev repo `.kaji/config.toml` に `[provider.type = "github"]` + repo + default_branch が存在
- [x] dev repo の `provider.github.repo` が `apokamo/kaji`（`git remote get-url origin` と一致）
- [x] `.gitignore` に `.kaji/config.local.toml` 行が含まれる
- [x] Phase 3-c / preflight の既存テスト全件 PASS

### 手動確認（buildout 制約により未実施）

- [ ] dev repo で `kaji local init` → `kaji issue create` → `kaji run` smoke
- [ ] `.kaji/config.local.toml` 削除で github 経路復帰

→ `make check` の自動テスト範囲で同等の構造的保証は得ているが、subprocess
レベルの smoke は **Phase 3-e Large-local** で扱う。

### ドキュメント

- [x] `docs/cli-guides/local-mode.md` ドラフト作成（インストール / init 上書き仕様 /
      provider 切替 / Issue / Workflow / ID 文法 / 6-step / レイアウト / 制限）
- [x] `phase3-design.md` § `kaji local init` 仕様 に cross-reference 注記
- [x] `phase3d-implementation-report.md`（本書）作成
- [ ] CHANGELOG エントリは GitHub 復旧後に PR description / リリースノートで反映

## 既存テストへの影響

| テスト | 影響 | 対応 |
|--------|------|------|
| `tests/test_cli_main.py::test_existing_pr_view_falls_back_to_passthrough` | dev repo dogfooding で `provider.github.repo` が configured されたため `--repo` 注入が起きる | `_load_config_for_dispatch_or_none` を mock で None 返却にし、legacy passthrough 経路を独立に検証する |
| `tests/test_providers_local.py::test_close_without_reason_persists_empty` | default が `""` → `"completed"` に変更 | テスト名・assertion を更新し、追加で `reason=""` / `reason="not-planned"` の挙動を検証 |

それ以外の Phase 3-c / preflight の既存テストはすべて touch なしで PASS。

## ロールバック方針（phase3d-design.md § Rollback）

各 commit は独立に revert 可能。Phase 3-e fail-fast 化は本ブランチ merge 後に
着手するため、commit 9（dogfooding）が main に入っていることが前提。

## 次フェーズへの申し送り

- **Phase 3-e**: `provider.type` 未設定 fallback の削除 + fail-fast 化、
  Large-local E2E、`config.py` 側の `validate_machine_id` 統合
- **Phase 4**: `kaji pr` の bare provider エラー化、`pr_id` / `pr_ref` の
  prompt 注入、`PullRequestProvider` / `ReviewRequestProvider` の上位概念
- **Phase 5**: `kaji sync from-github` / cache 整備 / BCP runbook

`phase3d-design.md` § 4 の commit 順序（副作用小から大へ）を遵守。Phase 3-e
直前で dev repo dogfooding 済の状態を維持する。

## レビュー反映（commit `0d81577`）

初回実装後のレビューで指摘された 3 件の Must Fix を以下のように対応した。

### 1. `issue-close` /local 経路の worktree 運用修正

- **指摘**: feature worktree 内で `git switch [default_branch]` していたが、
  bare + worktree 構成では base branch が別 worktree で checkout 済みのため
  Git に拒否される。さらに自分自身の worktree を `git worktree remove` する
  構造になっていた。
- **対応**: `git worktree list --porcelain` で `[default_branch]` を checkout
  している base worktree を抽出し、merge / close commit / cleanup / push を
  base 側で実行する手順に書き換えた。feature worktree (`[worktree_dir]`) は
  base 側から `git worktree remove` で削除する。
- **テスト追従**: `tests/test_phase3d_skills.py` の static keyword を
  "Base branch" → "Base worktree" に更新。

### 2. `--default-branch` 値の validation

- **指摘**: `bad"branch` / 改行 / 制御文字 などが overlay TOML に直書きされ、
  次回 config load が `ConfigLoadError` になる事故を構造で防いでいなかった。
- **対応**: `local_init.py` に `validate_default_branch()` を新設。git の
  `check-ref-format` の保守的サブセット（`[A-Za-z0-9._/-]`、長さ 255、
  `'.'` / `'..'` / `'.lock'` / leading `'-'` 等の禁止）を適用し、不正値は
  `cmd_local_init` 入口で exit 2 にして overlay を生成しない。
- **テスト**: Small parametrized（accept 7 件 / reject 19 件）+ Medium 5 件
  を追加。

### 3. docs の `kaji issue create` 例に `--body` 追加

- **指摘**: `kaji_harness/cli_main.py:950` で `--body` / `--body-file` が必須
  だが、ガイドの例にどちらも無く user が最初の smoke で詰まる。
- **対応**: `docs/cli-guides/local-mode.md` の例に `--body` 版と
  `--body-file` 版の両方を提示。

### Hygiene について

レビューで指摘された未追跡ファイル（`.kaji/wf/design-only.yaml` 等、`actionlint`、
`draft/lab/`）は Phase 3-d 開始時点（main HEAD）から既に untracked として
存在していたものであり、本 Phase の作業対象外。Phase 3-d スコープに
含めず、別途整理する位置づけとする。

## 参照

- `draft/design/local-mode/phase3d-design.md` — 本 Phase の正本
- `draft/design/local-mode/phase3-design.md` — Phase 3 全体の親設計
- `draft/design/local-mode/design.md` — local mode の全体仕様（特に L972-996）
- `draft/design/local-mode/phase3d-preflight-implementation-report.md` —
  preflight 完了点（canonical id / PyYAML / slug optional / O_EXCL comment）
