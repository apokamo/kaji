---
status: draft
phase: 4
parent: phase4-design.md
created: 2026-05-07
revised: 2026-05-08 (review fix: MF1, MF2, SF1, SF2)
---

# [実装報告] kaji local mode — Phase 4 (PR provider 整理)

設計書: [phase4-design.md](./phase4-design.md)

実装ブランチ: `feat/local-phase4`（worktree:
`/home/aki/dev/kaji/kaji-feat-local-phase4`）。

GitHub が利用できない環境のため、PR は作成せず本ファイルで作業内容を報告する。

## 結果サマリー

| 項目 | 状態 |
|------|------|
| 全 commit 完了 | ✅ 12 commit（design 通り 10 + レビュー反映 2 / preflight commit 0 を含めれば 13 単位） |
| `make check`（最終 commit 時点） | ✅ |
| `make test-large-local` | ✅ 25 件 pass |
| 全テスト（small + medium + large） | ✅ 1037 passed, 1 skipped |
| 設計書からの逸脱 | 後述「設計書との差分」を参照 |
| Phase 4 レビュー対応 | ✅ Must Fix 2 / Should Fix 2 すべて反映（後述「Phase 4 レビュー反映」を参照） |

## commit 一覧

| # | hash | 種別 | 概要 |
|---|------|------|------|
| 0 | — | preflight | Phase 3-e merge 確認（`git branch --merged main` で `feat/local-phase3e` がヒット、`runner.RunIssueContext.issue_context` の Optional 解除 / `get_provider` の戻り型 `IssueProvider` 解除を grep 確認） |
| 1 | `de662d2` | feat | `actual_provider_type()` narrowing helper + `kaji config provider-type` サブコマンド（read-only）+ Small/Medium テスト 9 件 |
| 2 | `4e9ce07` | feat | `Workflow.requires_provider: Literal["github", "local", "any"] = "any"` 追加。`_parse_workflow` / `validate_workflow` に enum 検証 + Small テスト 8 件 |
| 3 | `c4256eb` | feat | `.kaji/wf/*.yaml` 全 5 ファイルに `requires_provider` 明示。`feature-development.yaml` / `feature-development-light.yaml` / `implement-to-pr.yaml` / `design-only.yaml` をこの commit で tracked 化（従来は個人 worktree のみに存在していた） |
| 4 | `99a7ecc` | feat | `cmd_run` に `_validate_workflow_provider_match` を追加。`actual_provider_type()` helper 経由で type narrowing。Medium テスト 5 件 |
| 5 | `4533db8` | feat | `_handle_pr` に `isinstance(provider, LocalProvider)` 分岐追加。`_PR_BARE_PROVIDER_ERROR` 定数も新設。`_PR_BUILTIN_SUBCOMMANDS` も同じガードで止める。Small/Medium テスト 11 件 |
| 6 | `f94b731` | feat | pr-fix / pr-verify / i-pr の SKILL.md に Step 0 provider check を追加。`[pr-number]` → `[pr_id]` / `[pr_ref]` 置換、`pr_id` / `pr_ref` / `pr_url` 確定手順を明記、コンテキスト変数表に `provider_type` を追加 |
| 7 | `e794111` | test | `test_phase3d_skills.py:FORBIDDEN_PATTERNS` に `[pr-number]` / `<pr-number>` を追加。新規 `test_phase4_skill_provider_guard.py` で Step 0 ガード文言・`kaji config provider-type` 経由 fallback・underscore placeholder の存在を grep 検証（21 件） |
| 8 | `c59fb8c` | refactor | `prompt.build_prompt` の `issue_context: IssueContext | None = None` を required 化。`if issue_context is not None:` 分岐を構造的削除。`tests/conftest.py:make_issue_context` factory を追加し、`tests/test_prompt_builder.py` / `test_phase3c_runner.py` / `test_verdict_integration.py` の直接呼び出しを一括更新 |
| 9 | `bb153fa` | test | 新規 `test_phase4_large_local.py`（11 件）で 3 層ガード（CLI / Workflow / `kaji config provider-type` / `kaji validate` enum）を実 subprocess で確認 |
| 10 | `83a29e2` | docs | `CHANGELOG.md`（BREAKING CHANGE 3 件 / Added 3 件 / Migration）、`docs/cli-guides/local-mode.md`（`kaji pr` の挙動）、`docs/dev/workflow-authoring.md`（`requires_provider`）、`docs/dev/workflow_guide.md`（provider × workflow 対応表）、`docs/dev/development_workflow.md`（workflow 起動時の provider 整合 fail-fast）を更新 |
| 11 | `b33c35a` | fix | レビュー反映（SF1, SF2）: `_handle_pr` docstring から「``--help`` を含む」を撤回し argparse 上位の挙動を Note で説明。pr-fix / pr-verify / i-pr の Step 0 を verdict 一体化に書き換え（bash 内 `exit 0` を撤去） |
| 12 | `d4a6bc1` | fix | レビュー反映（MF1）: i-pr Step 4 の ``2>&1 \| tail -1`` を ``pr_output=$(...)`` + exit code 保持 + URL regex 検証に書き換え。失敗時 ABORT verdict 出力手順も明記 |

差分統計（main..feat/local-phase4、レビュー反映後）:

```
31 files changed (incl. design / report tracking), +2057 / -137
```

## 機械検証結果

phase4-design.md § 受け入れ条件 § 機械検証 を順番に確認した結果:

| 項目 | 結果 |
|------|------|
| Phase 3-e merge 済み（`git branch --merged main \| grep feat/local-phase3e`） | ✅ |
| `RunIssueContext.issue_context` 型注釈が `IssueContext`（Optional 解除） | ✅ `runner.py:52` |
| `get_provider(config)` 戻り型が `IssueProvider`（Optional 解除） | ✅ `providers/__init__.py:63` |
| `make check` 緑（commit 10 時点） | ✅ ruff / mypy / pytest 全 pass |
| `make test-small` / `make test-medium` 緑 | ✅ |
| `make test-large-local` 緑（既存 13 件 + Phase 4 追加 11 件 = 24 件、ただし `test_phase3e_large_local.py` も Phase 3-e 配下で集計するため最終 25 件） | ✅ 25 passed |
| `make test-large` 緑 | ✅ 全テストで 1037 passed, 1 skipped |
| `mypy kaji_harness/` strict 緑 | ✅ |
| `Workflow.requires_provider` の default が `"any"` で既存 workflow が壊れない | ✅（`test_phase4_workflow_requires_provider.py:test_requires_provider_defaults_to_any`） |
| `_parse_workflow` が `requires_provider: foo` を `WorkflowValidationError` で拒否 | ✅ |
| `_handle_pr` が `provider.type='local'` で exit 2 + `gh` subprocess を呼ばない | ✅ (`test_phase4_pr_bare_provider.py:test_pr_local_provider_blocks_all_subcommands`) |
| `_handle_pr` の `provider.type='github'` 経路は変化なし（mock で bit-exact） | ✅（`test_pr_github_passthrough_invokes_gh_with_repo_injection` / `test_pr_github_pr_create_forwarded`） |
| `cmd_run` が workflow.requires_provider と config.provider.type の不整合を exit 2 で報告 | ✅ |
| `cmd_run` が `requires_provider: any` を両 provider で受理 | ✅ |
| `prompt.build_prompt` の signature が `issue_context: IssueContext`（Optional 解除） | ✅（`prompt.py:13-21`） |
| `prompt.py` 内に `if issue_context is not None:` が 0 件 | ✅（`grep "issue_context is not None" kaji_harness/prompt.py` ヒット 0） |
| `test_phase3d_skills.py:FORBIDDEN_PATTERNS` に `[pr-number]` / `<pr-number>` を含み violation 0 件 | ✅ |
| pr-fix / pr-verify / i-pr の SKILL.md に `[provider_type]` ガード句が含まれる | ✅ |
| subprocess: `kaji pr create -t x -b y` (provider=local) → exit 2 + `forge-only` / `provider.type='local'` / `/issue-review-code` | ✅ (`test_pr_create_under_local_exits_2`) |
| subprocess: `kaji run feature-development.yaml local-pc1-1` (provider=local) → exit 2 + `requires provider.type='github'` | ✅ (`test_run_github_workflow_under_local_exits_2`) |
| subprocess: `kaji run feature-development-local.yaml 1` (provider=github) → exit 2 + 対称ケース | ✅ (`test_run_local_workflow_under_github_exits_2`) |
| subprocess: `kaji config provider-type` (provider=github) → stdout = `github\n`, exit 0 | ✅ |
| subprocess: `kaji config provider-type` (provider=local) → stdout = `local\n`, exit 0 | ✅ |
| Skill 文面検証: pr-fix / pr-verify / i-pr に `kaji config provider-type` の fallback が含まれる | ✅ (`test_phase4_skill_provider_guard.py:test_skill_has_kaji_config_provider_type_fallback`) |

### 手動確認

| 項目 | 結果 |
|------|------|
| dev repo の `.kaji/config.toml`（provider=github 固定）で `make test-large-local` が通る | ✅ 25 passed |
| `kaji validate .kaji/wf/*.yaml` が全 5 ファイルで通る | ✅（commit 3 完了直後に確認） |

## 設計書との差分

### 1. Skill Step 0 ガードのフォーマット選択

設計書 § 3 では verdict ABORT を返すための bash サンプルとして `exit 0` を
使う想定だったが、Skill 群のフォーマットを既存（PASS / RETRY / ABORT verdict
を出して終了する）に揃えた。case 文の中で実行を打ち切る `exit 0` は残しつつ、
verdict ブロックは markdown の通常解説として明示する形にした（agent が判定を
読み取る経路を変えないため）。

### 2. `kaji pr --help` の取り扱い

設計書 § 1 § 設計判断 では「`kaji pr --help` も bare では止める」としていたが、
既存の argparse 構成（top-level parser が `--help` を消費してしまう）の制約で
`--help` は `_handle_pr` まで到達せず top-level の
`unrecognized arguments: --help` で先に止まる。これは GitHub mode でも同様
の挙動であり、設計の「forge 機能の help を見せない」という要件は満たすため、
本 Phase では追加対応しない（`test_pr_local_provider_blocks_all_subcommands`
の `--help` ケースを `pr` no-args ケースに置換）。

### 3. `i-pr` Skill の `[pr_id]` placeholder

設計書 § 4 では i-pr / pr-fix / pr-verify すべてに `[pr_id]` / `[pr_ref]` を
入れる方針だったが、i-pr は **新規 PR を作成する Skill** であり PR 識別子は
Step 4 内で `kaji pr create` の出力から **shell 変数として** 確定する流れに
変更した。Skill markdown 上の placeholder 表記としては `[pr_ref]` / `[pr_url]`
のみが残り、`[pr_id]` は bash 変数 `pr_id` として参照される（`test_phase4_skill_provider_guard.py`
は i-pr についてのみ `[pr_id]` を必須としない形に調整した）。

### 4. `build_prompt` の `issue` 引数の扱い

設計書 § 6 では `issue` 引数の扱いに明確な記載が無かった。実装では
**signature 上は残す**（呼出側互換のため）が、注入される値は
`issue_context.issue_id` / `issue_context.issue_ref` を採用するように変更し、
関数冒頭で `del issue` して未使用変数 lint を回避した（実装注意 § と整合）。

## 主要ファイルの差分

### 新規追加

- `kaji_harness/providers/__init__.py:actual_provider_type()` — narrowing helper
- `kaji_harness/cli_main.py:_register_config()` / `cmd_config_provider_type()` — `kaji config provider-type` サブコマンド
- `kaji_harness/cli_main.py:_PR_BARE_PROVIDER_ERROR` / `_validate_workflow_provider_match()` — Phase 4 ガード本体
- `tests/conftest.py:make_issue_context()` — `build_prompt` 直接呼び出しテスト向け factory
- `tests/test_phase4_provider_type.py`（9 件）
- `tests/test_phase4_workflow_requires_provider.py`（8 件）
- `tests/test_phase4_workflow_provider_match.py`（5 件）
- `tests/test_phase4_pr_bare_provider.py`（11 件）
- `tests/test_phase4_skill_provider_guard.py`（21 件）
- `tests/test_phase4_large_local.py`（11 件）
- `.kaji/wf/feature-development.yaml` / `feature-development-light.yaml` / `design-only.yaml` / `implement-to-pr.yaml`（commit 3 で tracked 化、`requires_provider` 明示）

### 既存変更

- `kaji_harness/models.py` — `Workflow.requires_provider` フィールド追加（+1 行）
- `kaji_harness/workflow.py` — `_parse_workflow` / `validate_workflow` に enum 検証（+20 行）
- `kaji_harness/cli_main.py` — `cmd_run` に整合検証挿入、`_handle_pr` に bare provider 分岐、`config` サブコマンドグループ追加（+127 行）
- `kaji_harness/prompt.py` — `issue_context` required 化、内部 if 分岐削除（-15 行ネット）
- `.claude/skills/{pr-fix,pr-verify,i-pr}/SKILL.md` — Step 0 ガード追加 + placeholder 統一
- `tests/test_phase3c_runner.py` — Optional 経路テスト 2 件を Phase 4 仕様向けに差し替え
- `tests/test_prompt_builder.py` — 全 `build_prompt` 呼び出しに `issue_context=` 追加
- `tests/test_verdict_integration.py` — 1 箇所の直接呼び出しを更新

## 3 層ガードの相互独立性確認

phase4-design.md § Rollback 方針通りの独立性をチェックした:

| 層 | 該当 commit | 単独 revert で他層が機能するか |
|----|-------------|--------------------------------|
| CLI（`_handle_pr` bare error） | `4533db8` | ✅ commit 5 を revert しても commit 4 (workflow) / commit 6 (skill) で止まる |
| Workflow（`requires_provider` 整合検証） | `4e9ce07` / `c4256eb` / `99a7ecc` | ✅ commit 2-4 を revert しても commit 5 (CLI) / commit 6 (skill) で止まる |
| Skill（Step 0 ガード） | `f94b731` | ✅ commit 6 を revert しても commit 4 (workflow) / commit 5 (CLI) で止まる |

各層の rollback は他層の機能性を毀損しない（少なくとも 1 層が機能していれば
事故 PR は防げる）。

## Phase 4 レビュー反映（2026-05-08）

レビューで指摘された Must Fix 2 件 / Should Fix 2 件をすべて受け入れて修正し、
追加 commit `b33c35a`（SF1+SF2）/ `d4a6bc1`（MF1）/ 本ファイル追加 commit
（MF2）として branch に積んだ。

| 指摘 | 区分 | 対応概要 | 該当 commit |
|------|------|----------|-------------|
| i-pr の `PR_URL=$(... 2>&1 \| tail -1)` が `kaji pr create` の失敗を握りつぶす | MF | `pr_output=$(...)` で exit code を保持、URL regex で末尾行を検証、失敗時は ABORT verdict を出して Step 5 / Step 6 へ進まない手順を明記 | `d4a6bc1` |
| phase4-design.md / phase4-implementation-report.md が feat/local-phase4 worktree に存在せずレビュー証跡として不整合 | MF | 両ファイルを branch に追加（既存 Phase 1-3e と同じ tracked 運用に揃える）。`docs/dev/development_workflow.md:151` の「設計書は worktree 内」前提とも整合 | （本コミット） |
| `_handle_pr` docstring が「``--help`` を含む」と書いているが実装では argparse 上位で先に止まり `_handle_pr` に届かない | SF | docstring を実装挙動に合わせ「``_PR_BUILTIN_SUBCOMMANDS`` も同じガードで止める」+ ``--help`` の既存挙動を Note 化。実装側の挙動は変えない（``--help`` を bare で見せない設計要件は満たす） | `b33c35a` |
| pr-fix / pr-verify / i-pr の Step 0 が bash 内 `exit 0` と別 verdict ブロックに分離していて実行手順として曖昧 | SF | 3 Skill すべてで Step 0 を verdict 一体化に書き換え（bash の `exit 0` を撤去、各分岐ごとに出力する verdict 全文を text ブロックで明示）。「shell の exit に任せず agent 自身が stdout に出力する」注記も追加 | `b33c35a` |

レビュー反映後の機械検証もすべて緑（make check / pytest 1037 passed,
1 skipped / make test-large-local 25 passed / make verify-docs / kaji
validate `.kaji/wf/*.yaml`）。

## オープン論点と次フェーズへの申し送り

phase4-design.md § オープンな論点 で挙げた項目について、本実装では追加検討を
していない。以下は Phase 5 以降への申し送り:

1. **`pr_id` / `pr_ref` の prompt.py 経由自動注入**（Phase 5）
   - 現行は Skill 内 `kaji pr list --search` で取得する暫定運用
   - `kaji sync from-github` + `GitHubProvider.resolve_pr_context(branch_name)`
     と一体で整理する方が結合度が下がる（設計書 § 1 「out-of-scope」と整合）

2. **`requires_provider` enum の拡張**
   - GitLab / Forgejo / 他 forge を追加する場合の policy は本 Phase で確定せず、
     enum 列挙を維持（`forge` という meta enum は API spec 差を吸収しづらい
     ため棄却）

3. **`tests/conftest.py:make_issue_context` の公開範囲**
   - 現状は `tests/` 配下に閉じている。本番コードに test 専用依存を入れない
     という方針を維持（`kaji_harness.providers.testing` module は作らない）

4. **Phase 完了時の chk リスト**
   - 設計書 § オープンな論点 で「Optional / None-check の残存を grep する」
     提案あり。Phase 5 着手時に preflight で確認する運用を検討する

## 工数記録

| commit | 設計予定 | 実績 | 差異 |
|--------|----------|------|------|
| 1 | 0.4 日 | 0.3 日 | -0.1（既存 config helper を流用） |
| 2 | 0.25 日 | 0.2 日 | -0.05 |
| 3 | 0.1 日 | 0.1 日 | 0 |
| 4 | 0.25 日 | 0.2 日 | -0.05（narrowing helper を commit 1 で先出ししていたため） |
| 5 | 0.5 日 | 0.4 日 | -0.1（既存 mock パターンを流用） |
| 6 | 0.6 日 | 0.5 日 | -0.1（3 ファイルとも同じ Step 0 テンプレートで揃えたため） |
| 7 | 0.1 日 | 0.15 日 | +0.05（i-pr の `[pr_id]` 例外で test 調整） |
| 8 | 0.5 日 | 0.4 日 | -0.1（autouse fixture を撤回した分の節約） |
| 9 | 0.25 日 | 0.2 日 | -0.05 |
| 10 | 0.1 日 | 0.15 日 | +0.05（CHANGELOG の Migration 章で custom workflow 推奨を厚めに記述） |
| **合計** | **3.05 日** | **2.6 日** | -0.45 日 |

設計フェーズで判断済み論点 14 件を先に整理していた効果が大きく、実装中に
方針判断で詰まる箇所はほぼ無かった。

## 次のステップ

- 本ブランチ `feat/local-phase4` を `main` にマージ（`--no-ff`）
- マージ後、`docs/dev/workflow-authoring.md` § `requires_provider` を踏まえて
  custom workflow（user 個人の `.kaji/wf/*.yaml`）に手動で
  `requires_provider` を追加するか確認する
- Phase 5 設計（`kaji sync from-github` + GitHubProvider PR 解決 + prompt
  経由の `pr_id` / `pr_ref` 注入）の起点にする
