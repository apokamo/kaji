# [設計] local provider を削除する（第 2 段階）

Issue: #448

## 概要

v0.21.0 で非推奨にした local provider（`provider.type = "local"`）を、実装・local 専用 CLI・
local 専用 workflow・テスト・skill / docs の local 分岐・`.kaji/issues/` の local Issue 28 件ごと
削除する。`provider.type = "local"` は設定読み込み時に移行先を示すエラー（終了コード 2）にする。

> 実装時の確定事項: 削除済み provider の例外名は `ProviderTypeRemovedError` とする。
> Issue 完了条件の残存参照検索（`LocalProvider` 0 件）と両立させるための改名で、
> `ConfigLoadError` の継承・終了コード・表示内容は設計どおり。

## 背景・目的

- 本リポジトリは local provider を使っていない（`.kaji/config.toml` / `.kaji/config.local.toml` は
  どちらも `provider.type = "github"`）。それでも実装約 890 行（provider 本体）+ local 専用 CLI
  約 810 行、テスト 73 ファイル（2026-10-17 時点の grep。Issue 起票時は 68）、docs / skill 40 超の
  ファイルが local を前提に保守されている。
- 新機能の要件に「local provider ではどうするか」が入り込み、設計・レビューのコストが増えている
  （#434 では local の incident 起票経路を巡り design step が ABORT した）。
- #438 で「1 リリース警告してから削除する」と決め、非推奨化を含む v0.21.0 を公開済み。v0.22.0 は
  削除を含まずに公開済みのため、本削除は v0.23.0 に入る。

ユースケース:

1. kaji の保守者として、新機能の要件・設計・レビューを GitHub provider（と今後の GitLab 対応）だけで
   考えるために、local provider のコード・テスト・docs を持たずに済ませたい。
2. local provider を使っていた利用者として、アップグレード後に `provider.type = "local"` のまま
   kaji を実行したとき、黙って別の動作になるのではなく、移行先（GitHub provider）を示すエラーで
   止まってほしい。

代替案と不採用理由:

- **非推奨警告を出し続けて残す**: #438 の人間決定（P3: 1 リリース警告後に削除）に反する。
- **`provider.type = "local"` を黙って github にフォールバック**: Issue 本文の重要判断「黙って github に
  切り替えない」に反し、別 repo への誤投稿という不可逆事故を招く。
- **`kaji local` / `kaji sync` に「削除済み」専用エラーを残す**: ADR 008 決定 1（互換レイヤを書かない）に
  反する。argparse の未知サブコマンドエラー（exit 2）で十分に止まる。

## インターフェース

### 入力

| 入力 | 変更後の扱い |
|------|-------------|
| `.kaji/config.toml` / `.kaji/config.local.toml` の `[provider] type = "local"`（overlay 由来を含む） | `ConfigLoadError`。どの CLI でも設定読み込み時点で停止する |
| `[provider] type = "<github 以外>"`（例: `"gitlab"`） | 既存同様 `ConfigLoadError`。許容値の表示は `'github'` のみになる |
| `[provider.local]` テーブル（`type = "github"` と併存） | schema から削除。他の未知キーと同じく読み捨てる（専用処理を置かない） |
| `kaji local init` / `kaji sync from-github` / `kaji sync status` | サブコマンド登録を削除。argparse の `invalid choice` エラー（exit 2） |
| workflow YAML の `requires_provider: local` | `WorkflowValidationError`。許容値は `github` / `any` |
| Issue ID の local 形式（`local-<machine>-<n>` / `<machine>-<n>`） | `normalize_id` が `ValueError`（既存の「invalid issue id」経路）。`kaji run` / `kaji issue` / `kaji recover` は既存の exit code 契約で停止 |
| Issue ID `153` / `gh:153` | 変更なし（github provider の既存受理形式を維持） |
| `kaji issue ... --commit` / `kaji issue prepend-note --commit` | 変更なし（github 経路では従来どおり受理して無視）。後述「重要判断 provenance」参照 |

### 出力

- `provider.type = "local"` の場合の stderr（文言は two-way door。要素は固定）:

  ```text
  Error: Error loading <source-path>: provider.type = "local" is no longer supported:
  the local provider was removed after its deprecation in kaji v0.21.0.
  Set provider.type = "github" and [provider.github] repo = "<owner>/<repo>"
  (see docs/cli-guides/github-mode.md).
  ```

  - 必須要素: (1) local provider が削除されたこと、(2) 移行先 `provider.type = "github"`、
    (3) `<source-path>` は `type = "local"` を定義したファイル（overlay が定義していれば
    `.kaji/config.local.toml`、そうでなければ `.kaji/config.toml`）
  - 終了コード: 2。設定読み込みを前提にする全コマンド（`kaji run` / `kaji issue` / `kaji pr` /
    `kaji recover` / `kaji config *` / `kaji run-series` / `kaji validate-series`）は既存の
    `ConfigLoadError` → exit 2 経路（`EXIT_CONFIG_NOT_FOUND` / `EXIT_INVALID_INPUT`、いずれも値 2）に乗る。
    新しい exit code 定数は追加しない
  - `kaji validate <wf.yaml> [...]` も exit 2 で終わる。設定エラーは従来どおり当該ファイルの
    `✗ <path>` 行の下に表示し、残りのファイルの検証も続ける。最後に、1 件でも local provider の
    設定エラーがあれば `EXIT_CONFIG_NOT_FOUND`（2）を返す。この場合、他ファイルの検証失敗（exit 1）
    より 2 を優先する。local 以外の `ConfigLoadError` と workflow 検証失敗は、既存契約どおり exit 1 のまま
- 非推奨警告（`LOCAL_PROVIDER_DEPRECATION_WARNING` / `warn_local_provider_deprecated`）は出力されなくなる
- `kaji --help` に `local` / `sync` が出ない
- `kaji config provider-type` の出力値は `github` のみ
- 削除されるファイル・データ: 「方針」§ 削除対象を参照

### 使用例

```console
$ cat .kaji/config.local.toml
[provider]
type = "local"
$ kaji issue view 448; echo "exit=$?"
Error: Error loading /repo/.kaji/config.local.toml: provider.type = "local" is no longer supported: ...
exit=2

$ kaji local init; echo "exit=$?"
usage: kaji [-h] ...
kaji: error: argument command: invalid choice: 'local' (choose from ...)
exit=2

$ kaji validate .kaji/wf/custom/my-local.yaml; echo "exit=$?"   # requires_provider: local
✗ .kaji/wf/custom/my-local.yaml
  - 'requires_provider' must be one of ['any', 'github'], got 'local'
exit=1
```

```python
# 内部 API の使用例（変更後）
from kaji_harness.providers import normalize_id, resolve_main_worktree
from kaji_harness.errors import MainWorktreeResolutionError

normalize_id("153")      # -> "153"
normalize_id("gh:153")   # -> "153"
normalize_id("pc1-3")    # ValueError("invalid issue id 'pc1-3': expected '<number>' or 'gh:<number>'")

try:
    main_root = resolve_main_worktree(start_dir=repo_root, default_branch="main")
except MainWorktreeResolutionError:
    main_root = None  # best-effort 呼出側（artifacts / overlay divergence / baseline）
```

### エラー

| 失敗 | 例外 / 戻り値 | 終了コード |
|------|--------------|-----------|
| `provider.type = "local"` | `ProviderTypeRemovedError(source_path, ...)`（`ConfigLoadError` のサブクラス） | 2（`kaji validate` を含む全 CLI） |
| `requires_provider: local` | `WorkflowValidationError` | `kaji validate` 1 / `kaji run` 2（既存の定義エラー経路） |
| local 形式 Issue ID | `ValueError` → 各 CLI の既存ハンドリング | 既存契約どおり |
| main worktree 解決失敗 | `MainWorktreeResolutionError`（`LocalProviderError` の置換） | 呼出側はすべて best-effort で握る（既存挙動維持） |

## 制約・前提条件

- **スコープ境界（Issue 本文）**: `IssueProvider` Protocol と `GitHubProvider` は変更しない（docstring 中の
  local 言及の削除のみ可）。`.kaji/config.local.toml` overlay 機構は維持する（`[execution]` 等の上書きに
  使うため）。managed starter repository、旧 local branch / worktree の整理、無関係な bug 修正・refactor は
  含まない。
- **ADR 008**: 互換レイヤ・フォールバックを書かない。`provider.type = "local"` の named error は人間決定
  （Issue 本文 重要判断）であり、workflow.py の `_REMOVED_STEP_KEYS` と同じ「削除済み値を named error で
  止める」既存パターンに揃える。破壊的変更は CHANGELOG の BREAKING CHANGE に 3 要素で記載する。
- **ADR 009（private import 規約）**: `_worktree.py` は private module のまま。`resolve_main_worktree` は
  `kaji_harness.providers` facade 経由で公開を続ける。`tests/test_private_imports.py` /
  `tests/test_layer_imports.py` の fitness test を通す。
- **共有ロジックの存続**: 次は local 以外も使うため削除しない。
  - `kaji_harness/fsio.py`（`atomic_write`: baseline / review_poll_evidence / series / pytest plugin が使用）
  - `kaji_harness/providers/_worktree.py`（`resolve_main_worktree`: `artifacts.py`、
    `provider_overlay_divergence_warning`、`scripts/baseline_precheck.py` が使用）
  - `provider_overlay_divergence_warning` / `KAJI_PROVIDER_TYPE` による provider 一致検査（provider 種別に
    中立な仕組み。GitLab 対応で再び意味を持つ）
- 削除後に `LocalProviderError` の型は存在しない。`resolve_main_worktree` の失敗型は新設の
  `MainWorktreeResolutionError` とし、基底は `LocalProviderError` と同じ `RuntimeError` を維持する
  （`except RuntimeError` の到達範囲を変えない。`SyncError` を移設した #285 と同じ判断）。
- 依存: なし（新規ライブラリ追加なし）。

## 方針

### 削除対象（ファイル単位）

| 区分 | 対象 |
|------|------|
| provider 本体 | `kaji_harness/providers/local.py`、`_local_cache.py`、`_local_comments.py`、`_local_common.py`、`_local_store.py`、`cache_guard.py`（sync / LocalProvider だけが使う cache layout guard） |
| local 専用 CLI | `kaji_harness/local_init.py`、`kaji_harness/sync.py`、`kaji_harness/commands/sync.py` |
| local 専用 script | `scripts/migrate_comment_filenames_and_machine.py`（local comment ファイル名の移行 script） |
| workflow | `.kaji/wf/official/local/`（`dev-local.yaml` / `docs-local.yaml`） |
| docs | `docs/cli-guides/local-mode.md` / `local-mode.ja.md`、`docs/operations/local-mode-runbook.md` / `.ja.md`（local への退避手順で、provider 削除により手順自体が成立しない） |
| データ | `.kaji/issues/` 配下 28 件（人間の移行結果コメントで「全件移行しない」と確定済み） |
| テスト（ファイルごと削除） | local 専用機能だけを検証するもの: `test_providers_local.py`、`test_local_init.py`、`test_local_issue_commit_flag.py`、`test_local_provider_deprecation.py`、`test_local_provider_main_worktree.py`、`test_local_cli_large_local.py`、`test_sync_from_github.py`、`test_legacy_forge_cache_detection.py`、`test_migrate_comment_filenames.py`、`test_pr_bare_provider.py` |

### 変更対象（コード）

| モジュール | 変更 |
|-----------|------|
| `config.py` | 非推奨警告定数・helper を削除。`type == "local"` は `ProviderTypeRemovedError` で raise する。`LocalProviderConfig` と `ProviderConfig.local` を削除し、`ProviderConfig.type` を `Literal["github"]` にする。`_parse_provider` は `type == "local"` を named `ConfigLoadError`（source path は overlay 優先）にし、その他の非 github 値は `'github'` のみを許容値として示す。overlay の deep-1 merge 対象キーは `{"github"}` のみ。`[provider.local]` の解決コードを削除 |
| `errors.py` | `ProviderTypeRemovedError(ConfigLoadError)` を追加する。サブクラスなので、既存の `except ConfigLoadError` 経路（exit 2）はそのまま捕捉する。`SyncError` を削除。`MainWorktreeResolutionError(RuntimeError)` を追加。`ConfigNotFoundError` の案内から local-first 行と `local-mode.md` 参照を削除し、テンプレート参照先を `docs/cli-guides/github-mode.md` § 1.3 に変更 |
| `providers/__init__.py` | `LocalProvider` / `LocalProviderError` の import と `__all__` 登録、`get_provider` の local 分岐、`kaji local init` への案内を削除。`ResolvedId` / `ResolvedKind` を削除し、`normalize_id(raw: str) -> str` に単純化（`"<n>"` / `"gh:<n>"` → `"<n>"`、それ以外は `ValueError`）。`provider_overlay_divergence_warning` は `except MainWorktreeResolutionError` に変更し、WARN 文言から `kaji local init` を削除 |
| `providers/_worktree.py` | 例外を `MainWorktreeResolutionError` に置換し、エラー文言から `provider.type='local'` 前提の記述を削除。module docstring を「main worktree 解決（artifacts / overlay divergence / baseline 用）」に更新 |
| `providers/models.py` | `Comment.seq` / `Comment.machine_id`（local mode 固有と明記されたフィールド）を削除。docstring の local 例を削除 |
| `providers/base.py` / `context.py` / `markers.py` / `fsio.py` | docstring / コメントの local 言及のみ更新（Protocol シグネチャ・挙動は不変） |
| `workflow.py` / `models.py` | `VALID_REQUIRES_PROVIDER = {"github", "any"}`、`Workflow.requires_provider: Literal["github", "any"]`。`requires_provider: local` は既存の enum 検証で `WorkflowValidationError` になる（専用メッセージは置かない） |
| `runner.py` | `LocalProvider` import、`machine_id` 解決、`remote_cache` 拒否分岐、`isinstance(provider, LocalProvider)` 分岐を削除。`normalize_id` の新シグネチャに合わせる |
| `recovery/target.py` | `machine_id` 解決を削除し `normalize_id(issue_input)` を使う |
| `commands/main.py` / `commands/parser.py` | `sync` / `local` サブコマンドの登録と dispatch を削除。help 文言の local 言及（`provider-type` の値、Issue ID 例）を更新 |
| `commands/issue.py` | `_handle_issue_local`、`_local_issue_*`、`_resolve_local_id`、`resolve-verdict` / `prepend-note` / `context` の LocalProvider 分岐、`SyncError` 捕捉を削除。`--commit` の受理と github 経路での無視は維持し、コメント・help を「no-op（既存 skill 呼び出しとの互換のため受理）」に更新 |
| `commands/pr.py` | `LocalProvider` guard と `_PR_BARE_PROVIDER_ERROR` を削除 |
| `commands/recover.py` | `LocalProviderError` の捕捉を削除 |
| `commands/validate.py` | `cmd_validate` の per-file ループで `ProviderTypeRemovedError` を捕捉したら、既存どおり `_print_error` で表示して `failed` を加算する。あわせて `removed_local_provider` フラグを立てる。ループ終了後、フラグが立っていれば `EXIT_CONFIG_NOT_FOUND`（2）、そうでなく `failed > 0` なら `EXIT_VALIDATION_ERROR`（1）を返す。`_resolve_project_root_for_validate` の `ConfigLoadError` 再送出は現状のままでよい（サブクラスも同じループで捕捉される） |
| `artifacts.py` | `except MainWorktreeResolutionError` に変更 |
| `scripts/baseline_precheck.py` | `LocalProvider` 分岐（comment の local commit）を削除し、`except MainWorktreeResolutionError` に変更 |
| `errors.py` 以外の残存 local 文言 | `kaji_harness/` 全体で `kaji local` / `local-mode.md` / `provider.local` を案内する文字列を残さない |

local 言及が「local time」「attempt-local」「local branch」など provider と無関係な語の場合は変更しない。

### テストの扱い（73 ファイルの分類規則）

1. **ファイルごと削除**: 上表「テスト（ファイルごと削除）」の 10 ファイル。local 専用機能だけを検証し、
   github 側に移す検証観点を持たない。
2. **local ケースだけ削除**: github / local をパラメータ化しているテスト、local fixture を使う github 共通
   経路のテスト（例: `test_dispatcher.py`、`test_config.py`、`test_default_branch.py`、
   `test_git_remote_propagation.py`、`test_issue_context_cli.py`、`test_issue_prepend_note_cli.py`、
   `test_runner*.py`、`test_preflight.py`、`test_worktree_prefix.py`、`test_design_dir.py` 等）は local の
   パラメータ・ケースを削除し、github ケースを残す。github 側に同等ケースがない検証観点（例: provider 共通の
   `context` / `prepend-note` / `resolve-verdict` 経路）は github fixture へ移して観点を維持する。
3. **書き換え**: `test_provider_guard_large_local.py` は github ケース（`config provider-type`、
   `[provider]` 欠落、`requires_provider` 検証）を残し、local ケースを「local 設定 → exit 2」の Large 検証に
   置き換える。`test_resolve_main_worktree.py` は例外型を `MainWorktreeResolutionError` に、
   `test_providers_normalize_id.py` は新シグネチャに合わせる。`test_layer_imports.py` /
   `test_private_imports.py` は削除モジュールの登録・fixture 文字列を、存在するモジュール名に置き換える
   （fitness test の検出力は維持）。
4. **共有 fixture**: `tests/conftest.py` の `ensure_local_issue`、`_autocreate_local_issue_for_runner`、
   `_reset_local_provider_deprecation_flag`、`provider_type == "local"` 分岐、local 専用ファイル名の
   リストを削除する。
5. **無関係な一致**: `"local"` が provider と無関係な意味（`large_local` marker、`config.local.toml` 等）で
   現れるだけのファイルは変更しない。

### skill / docs の扱い

- **skill**（`.claude/skills/`。`.agents/skills/` は symlink のため実体は同一）: `provider_type == local`
  分岐、`provider=local の場合` 節（`issue-close` 等）、local Issue ID 形式の例、`local-mode.md` への案内を
  削除する。`--commit` の付与は残す（CLI 契約を変えないため）。
- **docs 削除**: 上表の 4 ファイル。
- **docs 更新**: `README.md` / `README.ja.md`（local provider 節を削除）、`llms.txt`、`AGENTS.md`
  （main 直コミット例外の `.kaji/issues/`）、`docs/README.md`、`docs/ARCHITECTURE.md`、
  `docs/reference/configuration.md` / `.ja.md`（`[provider.local]` 節・local overlay 節を削除し、
  `provider.type = "local"` が設定エラーになることを 1 文で記載）、`docs/cli-guides/github-mode.md` / `.ja.md`
  （§ 3 `kaji sync from-github` と local 復帰の記述を削除）、`failure-recovery`、`codex-cli-session-guide`、
  `docs/concepts/ai-driven-strategy`、`docs/dev/`（`workflow_guide.md`、`workflow-authoring.md` の
  `requires_provider` 値、`development_workflow.md`、`docs_maintenance_workflow.md`、`testing-convention.md`、
  `baseline-check.md`）、`docs/guides/`（`git-worktree.md`、`git-commit-flow.md` の `chore(local)`、
  `python-starter`、`typescript-starter`）、`docs/reference/python/naming-conventions.md` のディレクトリ例。
- **歴史的記録は書き換えない**: `CHANGELOG.md` の公開済みリリース節、`docs/adr/`、`draft/design/` の過去
  設計書。ただしリンク切れ（`make verify-docs` 失敗）を起こす箇所があればリンクのみ修正する。
- `.gitignore` の `.kaji/cache/` は残す（既存 clone に残る sync cache を untracked として露出させないため。
  保守コストを生まない 1 行）。

### CHANGELOG（Unreleased § BREAKING CHANGE）

ADR 008 の 3 要素を含める。

- **壊れる契約**: `provider.type = "local"`（overlay 含む）は設定エラー（exit 2）になる。`kaji local init` /
  `kaji sync from-github` / `kaji sync status` が無くなる。`.kaji/wf/official/local/` の workflow が無くなり、
  `requires_provider: local` は検証エラーになる。local 形式の Issue ID と `.kaji/issues/` は読まれなくなる。
  `[provider.local]` は無視される。
- **影響の判定方法**: `kaji config provider-type` が exit 2 で失敗するか、
  `grep -rnE 'type *= *"local"|requires_provider: *local' .kaji/` が一致するか。
- **適用指針**: アップグレード前に必要な `.kaji/issues/` の Issue を GitHub Issue へ移す。
  `provider.type = "github"` と `[provider.github] repo` を設定し、`.kaji/config.local.toml` の
  `type = "local"` を削除する。`requires_provider: local` の custom workflow は削除するか `github` に変える。
  未カスタマイズの starter は starter 追随版の再取得で足りる。カスタマイズ済み skill は本 Issue の PR の
  diff（local 分岐削除）を参照して移植する。

### 実装順序（推奨）

implement step の timeout（3600 秒）内で収まるよう、(1) コード削除・変更 → (2) テスト整理 →
`make check` → (3) workflow / `.kaji/issues/` 削除 → (4) skill / docs / CHANGELOG → `make verify-docs` の
順で commit を分ける。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| local provider の扱い | 削除する | 人間決定: [#438 provenance 記録](https://github.com/apokamo/kaji/issues/438#issuecomment-5914776187) P2。Issue #448 本文「重要判断」 | 削除対象をモジュール・CLI・script・workflow・docs・テスト単位に列挙 |
| 削除時期 | v0.23.0（非推奨化の次リリース。v0.22.0 は公開済み） | 人間決定: #438 provenance P3、Issue #448 本文 | なし（実装は Unreleased に入れる） |
| provider 抽象 | `IssueProvider` Protocol と `GitHubProvider` は変更しない | Issue #448 本文「スコープ境界」「重要判断」（#438 P2「gitlabは継続する」からの詳細化と明記済み） | Protocol メソッド（`is_readonly` を含む）・シグネチャは維持し、docstring の local 言及だけ更新 |
| 既存 local Issue 28 件 | 全件削除。移行なし | 人間決定: #438 provenance P4、[#448 移行結果コメント](https://github.com/apokamo/kaji/issues/448#issuecomment-6228378059)（28 件すべて移行しない） | `.kaji/issues/` を丸ごと削除 |
| `provider.type = "local"` の扱い | 設定読み込み時のエラー、終了コード 2、移行先を示す | Issue #448 本文 重要判断・完了条件（終了コード 2 は完了条件に明記） | `_parse_provider` で named `ConfigLoadError`。source path は overlay 優先。既存の `ConfigLoadError` → exit 2 経路に乗せ、新定数は作らない。文言要素を 3 点に固定 |
| エラー文言の細部 | 上記「出力」の例文 | AI の仮定（Issue 本文が「文言は設計で決める two-way door」と明記）。review-design / review-code で検査 | 必須要素のみ固定し、語句は実装で調整可 |
| `kaji validate` の終了コード | local provider の設定エラーは `kaji validate` でも exit 2。複数ファイルでは他ファイルの失敗（1）より 2 を優先する | 人間決定: Issue #448 完了条件第 2 項（`provider.type = "local"`、overlay 由来を含む、で設定を読み込むと終了コード 2）。コマンドの除外はない。review-design RD-1 の指摘で、初版の exit 1 解釈を撤回した | 専用サブクラス `ProviderTypeRemovedError` で local だけを判別する。local 以外の設定エラーと workflow 検証失敗の exit 1 契約は変えない（完了条件の対象外） |
| `workflow.requires_provider` の `"local"` | 値を削除し、`requires_provider: local` は `WorkflowValidationError` | Issue #448 完了条件が「設計で決め、設計書に記録する」と委任。AI の仮定。根拠: ADR 008（互換レイヤなし・fail-fast）、`requires_provider: local` の workflow は local provider なしに実行できない。review-design で検査 | 許容値 `{"github", "any"}`。専用メッセージは置かず既存 enum 検証のメッセージを使う |
| `[provider.local]` テーブル | schema から削除し、未知キーと同様に読み捨てる | AI の仮定。根拠: 既存 `_parse_provider` は `[provider]` 配下の未知キーを無視する。`type = "github"` と併存する `[provider.local]` は動作に影響しない残骸で、エラーにすると local を使っていない clone まで止まる。review-design で検査 | 専用の検出・警告コードを書かない |
| `kaji local` / `kaji sync` 実行時 | argparse の未知サブコマンドエラー（exit 2） | Issue #448 完了条件「`kaji --help` に出ず、実行すると引数エラーになる」 | 削除済み専用メッセージは置かない（ADR 008） |
| `--commit` フラグ | CLI は受理を続け github 経路で無視（現状維持）。skill からも削除しない | AI の仮定（two-way door）。根拠: `--commit` は 27 skill が付与する公開 CLI 契約で、削除は下流のカスタマイズ済み skill を gh の unknown flag で壊す別の BREAKING 判断になる。Issue の完了条件・スコープに含まれない。現状維持は後からいつでも削除できる。review-design で検査 | コメント・help を「no-op」に更新。削除は別 Issue 候補として完了報告に記載 |
| Issue ID 形式 | `"<n>"` / `"gh:<n>"` を維持し local 形式を拒否 | AI の仮定。根拠: `gh:<n>` は github provider でも現在受理する入力形式で、github 経路の契約を変えない（スコープ境界）。review-design で検査 | `normalize_id(raw) -> str` に単純化し `ResolvedId` を削除（内部 API） |
| `Comment.seq` / `machine_id` | 削除 | AI の仮定。根拠: フィールド定義に「local mode 固有」と明記され、GitHubProvider は値を設定しない。Protocol のメソッド・シグネチャは変わらない。review-design で検査 | provider 中立 DTO から local 固有フィールドのみ除去 |
| local-mode runbook | 削除 | AI の仮定。根拠: Issue 本文は「local provider・local 専用 CLI・local workflow の案内を削除」を完了条件とし、runbook は local へ退避する手順そのもの。review-design で検査 | ja / en 両方を削除し、リンク元を修正 |
| overlay divergence 検査 | 維持（文言から `kaji local init` を削除） | AI の仮定。根拠: Issue スコープ境界「overlay の仕組みは維持」、provider 種別に中立で GitLab 対応で再び意味を持つ。review-design で検査 | 例外型の置換のみ |

## テスト戦略

### 変更タイプ

実行時コード変更（設定検証・CLI サブコマンド・workflow 検証・ID 正規化の振る舞いが変わる）。

### Small テスト

- `KajiConfig` の provider 解析（`tests/test_config.py`）
  - tracked `type = "local"` → `ProviderTypeRemovedError`（`ConfigLoadError` のサブクラス）。`path` が tracked config、メッセージに「removed」相当と
    `provider.type = "github"` を含む
  - tracked `type = "github"` + overlay `type = "local"` → `ProviderTypeRemovedError`。`path` が overlay
  - `type = "gitlab"` → `ConfigLoadError`。許容値表示が `'github'` のみ
  - `type = "github"` + `[provider.local]` テーブル → 読み込み成功、`ProviderConfig` に `local` 属性がない
  - 非推奨警告が stderr に出ない（`capsys`）
- `workflow` 解析（`tests/test_workflow_requires_provider.py`）: `requires_provider: local` →
  `WorkflowValidationError`、`github` / `any` は受理
- `normalize_id`（`tests/test_providers_normalize_id.py`）: `"153"` / `"gh:153"` → `"153"`。
  `local-pc1-3` / `pc1-3` / `0` / `007` / `""` → `ValueError`
- `resolve_main_worktree`（`tests/test_resolve_main_worktree.py`）: git 不在・非 git・該当 worktree なしで
  `MainWorktreeResolutionError`（`RuntimeError` のサブクラス）を送出し、メッセージに `provider.type='local'`
  を含まない
- CLI parser（`tests/test_cli_main.py`）: `create_parser()` の subcommand choices に `local` / `sync` がない

### Medium テスト

- `main([...])` を in-process で呼び、tmp repo の local 設定に対する終了コードと stderr を検証
  （新規 `tests/test_local_provider_removal.py`。パラメータ化）
  - `issue view 1` / `pr list` / `config provider-type` / `run <wf> 1` → exit 2、stderr に移行先
  - overlay で `type = "local"` → exit 2、stderr に overlay path
  - `validate <wf>`（tracked local / overlay local）→ exit 2、stderr に `✗ <path>` と移行先メッセージ
  - `validate <ok.yaml> <bad.yaml>`（local 設定。`bad.yaml` は workflow 自体も不正）→ 両ファイルの結果を表示し exit 2（1 より 2 を優先）
  - `validate <wf>`（github 設定、`requires_provider: local`）→ exit 1（既存の検証失敗契約）
  - `local init` / `sync status` → `SystemExit(2)`
- 既存 github 経路の結合テスト（`test_dispatcher.py`、`test_issue_context_cli.py`、
  `test_issue_prepend_note_cli.py`、`test_resolve_verdict.py`、`test_runner*.py` 等）が local ケース削除後も
  github で同じ観点を検証していること（分類規則 2）
- `kaji validate` を `.kaji/wf/` 配下の全 workflow に適用して成功する（既存 `tests/workflows/` の不変条件
  テストで担保。削除した local workflow を参照するケースは除去）

### Large テスト

- `tests/test_provider_guard_large_local.py`（`large` + `large_local`）を書き換え、実 subprocess
  （`python -m kaji_harness.cli_main`）で検証する
  - local 設定の tmp repo で `issue view 1` / `pr list` / `config provider-type` / `validate <wf>` → exit 2、stderr に移行先
  - `kaji --help` の出力に `local` / `sync` が出ない、`kaji local init` が exit 2
  - 既存の github ケース（`config provider-type` = `github`、`[provider]` 欠落、`requires_provider` 検証）は維持
- 実 GitHub API 疎通（`large_forge`）は追加しない: 変更は設定検証と local 経路の削除で、GitHub API 呼び出し
  経路は変わらない。github 経路の回帰は既存 Small / Medium と既存 `large_forge` が担う。

### 変更固有の一時検証（恒久化しない）

- 残存参照: `grep -rnE 'LocalProvider|providers\.local|providers\._local_|local_init|kaji_harness\.sync' kaji_harness tests`
  が 0 件（完了条件）。削除モジュールが import できないことは、恒久テストにしても回帰検出情報が増えない
  （再追加はレビューで検出できる）ため grep で確認する
- `grep -rnE 'kaji local|kaji sync|local-mode|provider\.local|dev-local|docs-local' docs .claude/skills README.md README.ja.md llms.txt AGENTS.md`
  の残りが、歴史的記録（CHANGELOG 公開済み節・ADR・過去設計書）と削除を説明する文だけであること
- `make verify-docs` 通過、`kaji validate .kaji/wf/**/*.yaml` 全件成功、`make check` 通過

### 長時間 acceptance の配置

該当なし。nested `kaji run`、外部 agent を含む dogfood、fresh 環境での繰り返し acceptance を含まない。
最長の検証は `make check`（全 pytest）で、テスト削除により現状より短くなる。ただし implement step は
削除・修正ファイル数が多いため、「方針 § 実装順序」の順で commit を分ける。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし。既存 ADR は歴史的記録として書き換えない（リンク切れのみ修正） |
| docs/ARCHITECTURE.md | あり | provider 構成・モジュール一覧から local を削除 |
| docs/dev/ | あり | `workflow_guide.md`（dev-local / docs-local）、`workflow-authoring.md`（`requires_provider` 値）、`development_workflow.md`、`docs_maintenance_workflow.md`、`testing-convention.md`、`baseline-check.md` |
| docs/reference/ | あり | `configuration.md` / `.ja.md`（`[provider.local]`、`provider.type` 値）、`python/naming-conventions.md` |
| docs/cli-guides/ | あり | `local-mode.md` / `.ja.md` 削除、`github-mode.md` / `.ja.md`（`kaji sync` 節）、`failure-recovery`、`codex-cli-session-guide` |
| docs/operations/ | あり | `local-mode-runbook.md` / `.ja.md` 削除 |
| docs/guides/ / docs/concepts/ | あり | `git-worktree.md`、`git-commit-flow.md`、`python-starter`、`typescript-starter`、`ai-driven-strategy` |
| README.md / README.ja.md / llms.txt / docs/README.md | あり | local provider 節・索引の削除 |
| AGENTS.md / CLAUDE.md | あり（AGENTS.md） | main 直コミット例外の `.kaji/issues/` を削除。CLAUDE.md は影響なし |
| CHANGELOG.md | あり | Unreleased § BREAKING CHANGE（3 要素） |
| `.claude/skills/` | あり | `issue-close`、`i-pr`、`pr-fix`、`pr-verify`、`issue-start`、`i-dev-final-check`、`review` 等の local 分岐 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #448 本文 | https://github.com/apokamo/kaji/issues/448 | 完了条件（exit 2、`requires_provider` は設計で決定、grep 0 件、docs 削除、BREAKING 3 要素）、スコープ境界（`IssueProvider` / `GitHubProvider` / overlay 維持）、重要判断表 |
| #438 provenance 記録 | https://github.com/apokamo/kaji/issues/438#issuecomment-5914776187 | P2 local 削除・GitLab 継続、P3 1 リリース警告後に削除、P4 既存 Issue 削除 |
| #448 移行結果コメント | https://github.com/apokamo/kaji/issues/448#issuecomment-6228378059 | 「移行対象なし（28 件すべて移行しない）」 |
| v0.21.0 Release | https://github.com/apokamo/kaji/releases/tag/v0.21.0 | 非推奨化の公開。CHANGELOG `[0.21.0]` § Deprecated（「removal is planned for a subsequent release」） |
| ADR 008 | `docs/adr/008-no-backward-compat-layer.md` | 決定 1「後方互換レイヤを書かない」、決定 2「BREAKING に壊れる契約 / 影響の判定方法 / 適用指針の 3 要素」 |
| ADR 009 | `docs/adr/009-module-boundary-private-import.md` | private module は facade 経由で公開。fitness test が境界を強制 |
| 既存設定パーサ | `kaji_harness/config.py`（`_parse_provider`、`LocalProviderConfig`、非推奨警告） | overlay deep-1 merge、`machine_id` 由来 path を overlay 優先で選ぶ既存パターン、未知キーを無視する挙動 |
| 既存 exit code | `kaji_harness/commands/exit_codes.py`、`commands/run.py` / `issue.py` / `pr.py` / `config.py` / `validate.py` | `ConfigLoadError` は run で `EXIT_CONFIG_NOT_FOUND`(2)、issue / pr / config / recover / series で 2。validate は per-file 失敗として 1（local 設定エラーのみ本 Issue で 2 に変更） |
| 削除済み値の named error 前例 | `kaji_harness/workflow.py` `_REMOVED_STEP_KEYS` | 「過去に受理していたキーだけを named error で止める（ADR 008: fail-fast、互換層なし）」 |
| main worktree 解決の利用者 | `kaji_harness/artifacts.py`、`providers/__init__.py`、`scripts/baseline_precheck.py` | `resolve_main_worktree` は local 以外でも使うため `_worktree.py` を残し例外型だけ置換する |
| `SyncError` 移設の前例 | `kaji_harness/errors.py` `SyncError` docstring（#285） | 基底を `RuntimeError` のまま維持し `except RuntimeError` の到達範囲を変えない判断 |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L 判定基準、恒久テストと変更固有検証の切り分け、4 条件 |
| argparse | https://docs.python.org/3/library/argparse.html#exiting-methods | 「`ArgumentParser.error()` ... exits with status code 2」— 未知サブコマンドは exit 2 |
