---
status: implemented
phase: 3e
parent: phase3e-design.md
created: 2026-05-06
---

# [実装報告] kaji local mode — Phase 3-e

ブランチ: `feat/local-phase3e`（`main` から分岐、未マージ）

本書は `phase3e-design.md` の各 commit を実装した記録である。GitHub が
利用不能なため、PR 作成に代わって本ファイルに作業内容詳細を記す。

## 概要

Phase 3-e で計画された 7 commit をすべて実装し、`make check` が緑の状態で
ブランチを残した。Phase 3-d までの「`[provider]` 未設定 → WARN + GitHub
fallback」を完全に削除し、`[provider]` セクションと `.kaji/config.toml`
の両方を必須化した。`provider.local.machine_id` の文法検証を config load
時点に統合し、Large-local subprocess E2E（外部通信なし）と CHANGELOG /
migration ガイドを整備した。

## 実装した commit 一覧

| # | hash | サマリ |
|---|------|--------|
| 1 | `4b7337e` | `chore(phase3e): register large_local / large_forge markers + test-large-local target` |
| 2 | `08c5c12` | `feat(phase3e): validate provider.local.machine_id at config load time` |
| 3 | `25cfbf4` | `test(phase3e): add Large-local subprocess E2E scaffolding` |
| 4 | `96568e2` | `feat(phase3e)!: fail-fast on missing [provider] / config.toml + IssueContext narrowing` |
| 5 | `0da4b76` | `test(phase3e): enable fail-fast subprocess Large-local cases` |
| 6 | `bc3c35f` | `docs(phase3e): add CHANGELOG.md (Keep a Changelog format)` |
| 7 | （本コミットで反映） | `docs(phase3e): migration section in local-mode guide + design refs` |

すべて `--no-ff` で main へマージできる粒度に保ち、各 commit 単独で
`make check` 緑（`make check` = ruff check / format / mypy strict /
pytest）を維持した。

## commit 1: pytest marker + Makefile

`pyproject.toml [tool.pytest.ini_options].markers` に `large_local` /
`large_forge` を追加。`Makefile` に `test-large-local` ターゲットを追加。

- `large_local`: subprocess を起動するが外部通信を伴わないテスト
- `large_forge`: 実 GitHub API へ通信するテスト（GitHub 復旧後に追加）
- `make test-large-local` で local subset のみ実行可能

既存テストへの影響なし。

## commit 2: `validate_machine_id` の config 統合

`kaji_harness/config.py:_parse_provider` に non-empty 時のみ `machine_id`
を `[a-z0-9]{1,16}` 検証する分岐を追加。違反は `ConfigLoadError` で
fail-fast。空文字（`type=github` + 空 `[provider.local]`）は skip。

新規 `tests/test_phase3e_config_validation.py` で:

- `pc1` accept
- `PC1` / `pc-1` / 17 文字 reject
- overlay 経由の invalid も reject
- `type=github` + 空 `[provider.local]` accept

の 6 ケースを Medium で網羅。

## commit 3: Large-local テスト雛形

`tests/test_phase3e_large_local.py` 新規。`fresh_repo` fixture で
minimum `.kaji/config.toml`（`paths` + `execution`、provider 無し）と
空 `.gitignore` を準備し、`subprocess` で `kaji local init` /
`kaji issue create / list / close` の 8 ケースを smoke テスト化。
fail-fast 系 5 ケースは `@pytest.mark.skip(reason="enable in commit 5")`
で保留（commit 4 で挙動が変わるまで実行しない）。

`pytestmark = [pytest.mark.large, pytest.mark.large_local]` で
`make test-large-local` から拾える。

## commit 4: fail-fast 本体（破壊的変更）

### `kaji_harness/providers/__init__.py`

- `_PROVIDER_FALLBACK_WARNED` / `_emit_provider_fallback_warning` を削除
- `get_provider(config)` の戻り型を `IssueProvider | None` →
  `IssueProvider` に narrowing
- `config.provider is None` で `ValueError` を raise（メッセージは
  `phase3e-design.md § 9` のドラフトをそのまま使用）

### `kaji_harness/cli_main.py`

- `_load_config_for_dispatch_or_none` → `_load_config_for_dispatch` に
  rename。戻り型 `KajiConfig | None` → `KajiConfig`、
  `ConfigNotFoundError` を catch せず propagate
- `_handle_issue` / `_handle_pr` で `(ConfigLoadError,
  ConfigNotFoundError)` を catch → exit 2、`get_provider(config)` の
  `ValueError` も exit 2 に正規化
- `cmd_run` 冒頭で `get_provider(config)` を呼んで早期 fail-fast。
  `ValueError` は `EXIT_INVALID_INPUT (= 2)` に正規化（§ 1.5 設計の通り、
  `IssueContextResolutionError` 経由 exit 3 に落ちないことを保証）

### `kaji_harness/runner.py`

- `_resolve_issue_context()` の戻り型を `IssueContext | None` →
  `IssueContext` に narrowing。`provider is None` 経路を削除し、
  `assert self.config.provider is not None` で type checker を満足
- `_resolve_run_issue_context()` から `ctx is not None` 分岐を削除し、
  常に `ctx.issue_id` / `ctx.issue_ref` を採用
- `RunIssueContext.issue_context: IssueContext` に narrowing
- `_format_issue_ref` import を削除（fallback で使っていた）

### `prompt.py` は touch しない

§ 2 の縮小判断（レビュー MF2）通り、`build_prompt` の signature と
内部 `if issue_context is not None:` 分岐は残置し、Phase 4 の
構造的削除候補に申し送る。

### 既存テスト書換

| ファイル | 内容 |
|----------|------|
| `tests/test_phase3c_dispatcher.py` | `test_no_provider_returns_none_with_warning` → `test_no_provider_raises_value_error`（`ValueError` raise を検証）。`test_no_provider_falls_back_to_gh_passthrough` → `test_no_provider_section_fails_fast_exit_2`。`test_no_provider_passthrough_does_not_inject_repo` → `test_no_provider_section_does_not_invoke_gh` |
| `tests/test_phase3c_runner.py` | `test_no_provider_returns_none_legacy_fallback` → `test_no_provider_raises_resolution_error` |
| `tests/test_phase3d_preflight.py` | `test_no_provider_fallback_uses_raw_input_as_canonical` → `test_no_provider_section_raises_resolution_error` |
| `tests/test_cli_main.py` | `test_existing_pr_view_falls_back_to_passthrough` → `test_existing_pr_view_fails_when_config_missing` |

`_PROVIDER_FALLBACK_WARNED` への参照はすべて削除。

### 既存テスト fixture の Phase 3-e 移行

旧 fixture は `[provider]` セクション無しの `.kaji/config.toml` を
作っていたため、Phase 3-e 後は `kaji run` が exit 2 で fail-fast する。
影響テスト群（`test_workflow_execution.py` / `test_runner_before.py` /
`test_workdir_config.py` / `test_skill_validation.py` /
`test_timeout_config.py` / `test_verdict_e2e.py` / `test_cli_timestamp.py`
/ `test_config.py` / `test_cli_main.py`）の fixture に
`[provider] type="local"` + `[provider.local] machine_id="pc1"` を
一括追加した。

`test_workflow_execution.py:_make_runner` 内では `_ensure_local_issue`
ヘルパで対象 Issue dir を pre-create。同等のヘルパを `tests/conftest.py`
で `ensure_local_issue` として export し、`autouse` fixture で
`WorkflowRunner.__post_init__` をパッチして runner E2E テスト群が
透過的に Issue dir を持てるようにした。

`autouse` fixture は `_AUTOCREATE_OPT_OUT_FILES`
（`test_phase3c_runner.py` / `test_phase3d_preflight.py` /
`test_phase3e_large_local.py`）を opt-out して、fail-fast を直接
検証するテストの挙動を壊さない。

subprocess 起動の E2E（`test_config.py` / `test_verdict_e2e.py` /
`test_cli_timestamp.py` / `test_cli_main.py:TestCLILarge`）は
autouse が効かないため、各テストで `ensure_local_issue` を明示呼び出し
+ `arts_dir / "999"` → `arts_dir / "local-pc1-999"` などの canonical id
への書換を行った。

## commit 5: Large-local fail-fast 解除

`tests/test_phase3e_large_local.py` の `@pytest.mark.skip(...)` を 5
ケース削除。subprocess `kaji issue` / `kaji pr` / `kaji run` が
`[provider]` 不在 / `.kaji/config.toml` 不在のいずれでも exit 2 で
fail-fast し、`stderr` に該当メッセージを含むことを検証する。

`make test-large-local`: 13 件すべて緑。

## commit 6: CHANGELOG.md

ルート `CHANGELOG.md` を Keep a Changelog 形式で新規作成。

- `[Unreleased]` セクションに Phase 3-e の `BREAKING CHANGE`、
  Migration、exit-code 契約を記載
- `[Phase 3]` セクションに 3-a 〜 3-e の累積 Added / Changed / Removed
  をまとめ、復旧後のリリースノートのソースとして使える状態にした

## commit 7: migration docs + 設計書補正

- `docs/cli-guides/local-mode.md` に § 9 Phase 3-e migration を追加
  （GitHub 運用継続 / local-first 切替 / config.toml 不在 /
  machine_id 手書き 4 パスをカバー）
- `phase3-design.md § 4 PR-3e` の項目に「実装完了 — 2026-05-06、
  `feat/local-phase3e` ブランチ。`phase3e-design.md` および
  `phase3e-implementation-report.md` を参照」を追記

## 受け入れ条件のチェック

### 機械検証

- [x] `make check` 緑（commit 1-7 各時点で確認済）
- [x] `make test-small` / `make test-medium` 緑
- [x] `make test-large-local` 緑（13 件）
- [x] `make test-large` 緑（large_local を含む拡大）
- [x] `mypy kaji_harness/` strict 緑（`Success: no issues found in
  22 source files`）
- [x] `kaji_harness.providers.get_provider` が `config.provider is
  None` で `ValueError` を raise、戻り型 `IssueProvider`
- [x] `_PROVIDER_FALLBACK_WARNED` / `_emit_provider_fallback_warning`
  完全削除（`grep -r _PROVIDER_FALLBACK_WARNED kaji_harness/ tests/`
  → 0 件）
- [x] `_load_config_for_dispatch` の戻り型 `KajiConfig`、
  `ConfigNotFoundError` を catch しない
- [x] `cmd_run` 冒頭で `get_provider(config)` を呼んで `ValueError` を
  exit 2 に正規化
- [x] `RunIssueContext.issue_context` 型が `IssueContext`（Optional 解除）
- [x] `prompt.build_prompt` signature **不変**（§ 2 縮小判断）
- [x] `KajiConfig.discover()` が `provider.local.machine_id = "PC1"`
  overlay に対し `ConfigLoadError`
- [x] `pyproject.toml` の `markers` に `large_local` / `large_forge`
- [x] `Makefile` に `test-large-local`
- [x] `CHANGELOG.md` がルートに存在し `### BREAKING CHANGE` を含む
- [x] subprocess: `kaji issue view 1` `[provider]` 不在 → exit 2、
  stderr に `[provider] section is required`
- [x] subprocess: `kaji issue view 1` `.kaji/config.toml` 不在 →
  exit 2、stderr に `.kaji/config.toml not found`
- [x] subprocess: `kaji run feature-development-local.yaml 1`
  `[provider]` 不在 → exit 2（HarnessError 経由 exit 3 ではない）
- [x] subprocess: `kaji run` `.kaji/config.toml` 不在 → exit 2

### 手動確認（GitHub 不在環境のため可能な範囲）

- [x] dev repo の `.kaji/config.toml` には Phase 3-d で commit 済の
  `[provider.type = "github"]` + `[provider.github] repo`
  + `default_branch = "main"` がそのまま残存（`git diff main --
  .kaji/config.toml .gitignore` の差分ゼロを確認）
- [x] tmp repo `kaji local init` → `kaji issue create` →
  `kaji issue list` → `kaji issue close` を Large-local で完走
- [x] `make test-large-local` が外部通信なしで完走

### ドキュメント

- [x] `CHANGELOG.md` `[Unreleased]` に BREAKING CHANGE / Migration /
  Added / Changed / Removed
- [x] `docs/cli-guides/local-mode.md` § 9 Phase 3-e migration
- [x] `phase3-design.md § 4 PR-3e` 達成記録追記
- [x] `phase3e-implementation-report.md` 作成（本書）

## 残課題（次フェーズへの申し送り）

- **Phase 4**: `kaji pr` の bare provider エラー化、`pr_id` / `pr_ref`
  の prompt 注入変数化、`pr-fix` / `pr-verify` skill の provider 分岐
- **Phase 4 候補**: `build_prompt` の `IssueContext` required 化と
  内部 `if issue_context is not None:` 分岐の構造的削除（共通 fixture
  整備が前提）
- **Phase 5**: `kaji sync from-github` / cache 整備 / BCP runbook /
  `make test-large-forge` の新設（実 GitHub API 通信あり）
- **後続オープン論点**: Windows 本格対応（`portalocker` 化等）、
  `provider.local.default_branch` の git ref-format validation を
  config load 時に統合するか、`provider.github.repo` の owner/name
  regex validation、`kaji local init --force` 上書き flag

## 工数実績

設計工数見積: 2.5 日（phase3e-design.md § 工数見積）。

実装は 1 セッション内で完了したため、設計通り PR-3e のスコープが妥当
だったと判断する。最も重い commit は 4（fail-fast 本体 + 既存 fixture
書換）で、次点が autouse fixture 設計（既存テストの透過的移行）。

## design.md / phase3-design.md との整合

本実装は phase3e-design.md の判断済み論点（§ 9 文面、§ 1.5 exit code
正規化、§ 2 IssueContext narrowing 縮小、§ 3 machine_id 統合、§ 4
Large-local 範囲、§ 5 CHANGELOG 配置、§ 7 commit 順序）をすべて踏襲し、
新たな判断は加えていない。設計書と本実装に乖離がある場合はコードを
正本とする（phase3e-design.md § 9 の方針）。
