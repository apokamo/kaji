---
status: implemented
phase: 3d-preflight
parent: phase3d-preflight-design.md
created: 2026-05-06
branch: feat/local-phase3d-preflight
worktree: /home/aki/dev/kaji/kaji-feat-local-phase3d-preflight
---

# [実装報告] kaji local mode — Phase 3-d preflight hardening

GitHub が利用不可期間の作業のため、本ドキュメントは PR description / Issue
コメントの代替として残す。設計書 `phase3d-preflight-design.md` の「方針決定」
「実装範囲」「受け入れ条件」を正本とし、本書は実装結果と検証エビデンスを記録する。

## 1. 概要

Phase 3-c 完了後レビューで識別された 5 つの基盤リスクを、Phase 3-d 本体（`kaji
local init`、local workflow、Skill 5 変数移行）へ入る前に補正した。

| # | 補正項目 | 対象設計 §  |
|---|----------|------------|
| 1 | `kaji run` の canonical issue id 確定と state/log/prompt への一貫適用 | § 1 |
| 2 | local `--jq` を Python `jq` package へ移行（system jq 依存撤去） | § 2 |
| 3 | LocalProvider frontmatter を PyYAML 化 + 読み取り validation 強化 | § 3 |
| 4 | local `kaji issue create --slug` を optional 化（title 由来 fallback） | § 4 |
| 5 | local comment 書き込みを `O_CREAT \| O_EXCL` + 8 回 retry に変更 | § 5 |

### 作業ブランチ / Worktree

| 項目 | 値 |
|------|-----|
| ブランチ | `feat/local-phase3d-preflight` |
| 親ブランチ | `main` (`89edf27`) |
| worktree | `/home/aki/dev/kaji/kaji-feat-local-phase3d-preflight` |
| 報告書 | `draft/design/local-mode/phase3d-preflight-implementation-report.md` |

GitHub 不可期間のため、Issue 起票 / PR 作成は実施していない。本書を merge 元
worktree の `draft/design/local-mode/` に同梱しコミットすることで、将来 GitHub
復旧時に `phase3d-preflight-design.md` 上の TBD Issue へ紐付け直せる状態にした。

## 2. 変更ファイル

### コード変更

| ファイル | 変更点 |
|----------|--------|
| `pyproject.toml` | `dependencies` に `jq>=1.6` を追加 |
| `kaji_harness/cli_main.py` | `_apply_jq()` を Python `jq` package 実装へ書き換え、`_format_jq_results()` で `gh --jq` 互換 raw 出力を整形。`kaji issue create --slug` を optional 化（`required=True` → `default=None`）。`cmd_run()` の success summary を `runner.canonical_issue_ref` ベースに変更（fallback として raw `args.issue` の整形を残す） |
| `kaji_harness/providers/context.py` | `validate_branch_prefix()` helper を追加（`_mappings.LABEL_TO_PREFIX.values()` の集合に値域を制約） |
| `kaji_harness/providers/local.py` | `_serialize_frontmatter` / `_parse_frontmatter` を PyYAML 委譲へ書き換え、`_validate_issue_meta(strict_slug)` で `id` / `state` / `labels` / `slug` / `branch_prefix` の構造 validation を追加。`view_issue` / `edit_issue` / `close_issue` / `resolve_issue_context` から呼び出して fail-fast 化。`create_issue(slug=None)` を `derive_slug_from_title(title)` fallback 化。`comment_issue` を `_atomic_write_new()` + `MAX_COMMENT_WRITE_RETRIES = 8` の retry へ変更。新規 helper `_atomic_write_new()` は `os.open(O_CREAT \| O_EXCL \| O_WRONLY)` で exclusive create |
| `kaji_harness/runner.py` | `RunIssueContext` DTO を追加。`_resolve_run_issue_context()` を 1 度だけ呼び、`SessionState.load_or_create` / `run_dir` / `RunLogger.log_workflow_start` / `build_prompt` の `issue` 引数すべてに canonical id を渡すよう変更。`canonical_issue_id` / `canonical_issue_ref` フィールド (`init=False`) を追加。raw id ≠ canonical で旧 artifacts directory が残る場合に WARN を `_warn_legacy_artifacts()` で出力 |

### テスト変更

| ファイル | 変更点 |
|----------|--------|
| `tests/test_phase3d_preflight.py` | 新規 48 件（Small / Medium 混在）。canonical id / Python jq 出力 / PyYAML round-trip / slug fallback / comment retry の 5 領域を網羅 |
| `tests/test_providers_local.py` | `test_create_requires_slug` を `test_create_without_slug_derives_from_title` に置き換え（slug optional 化に対応） |
| `tests/test_phase3c_dispatcher.py` | `test_view_jq_unavailable_emits_runtime_error` を `test_view_jq_works_without_system_jq_binary` に置換（system jq 不在でも Python jq で動作する契約を構造的に検証）。既存 6 か所の `if not shutil.which("jq"): pytest.skip(...)` skip ガードを撤去（runtime dependency により常に動作） |
| `tests/test_cli_main.py` | `test_successful_run` で `mock_runner.return_value.canonical_issue_ref = None` を追加し、success summary fallback 経路を明示的にテスト |

### Docs 補正

| ファイル | 変更点 |
|----------|--------|
| `draft/design/local-mode/phase3c-implementation-report.md` | system jq 採用を Phase 3-d preflight で撤回したことを明記（CLI フラグ表 / `jq` 依存節 / 判断保留節の 3 か所に注記） |
| `draft/design/local-mode/phase3-design.md` | § slug の供給ルール の LocalProvider 節を「`--slug` は optional、未指定時は title から導出」に更新 |

## 3. 設計→実装の対応

### § 1 canonical issue id

設計書記載の `RunIssueContext` を完全準拠で導入。

```python
@dataclass(frozen=True)
class RunIssueContext:
    input_id: str
    canonical_id: str
    issue_ref: str
    issue_context: IssueContext | None
```

挙動表（設計書 § 1）と実装の対応:

| provider | 入力 | canonical_id | 実装場所 |
|----------|------|--------------|----------|
| 未設定 fallback | `42` | `42` | `_resolve_run_issue_context()` で `ctx is None` 経路 |
| github | `153` / `gh:153` | `153` | `IssueContext.issue_id` を採用 |
| local | `1` / `pc1-1` / `local-pc1-1` | `local-pc1-1` | `LocalProvider.resolve_issue_context()` 経由 |
| local | `gh:153` | reject | `_resolve_issue_context()` で `IssueContextResolutionError` |

`SessionState.load_or_create()` には常に canonical id を渡すよう統一。raw id 側に
旧 artifacts directory が残っている場合は WARN を 1 度出すが、自動 migration は
しない（設計書 § 1 既存 state / artifacts の扱い）。

### § 2 Python `jq` package

`pyproject.toml` の `dependencies` に `jq>=1.6` を追加。`uv sync` で `jq==1.11.0`
が入ることを確認。

`_apply_jq(json_text, expr)` は `jq.compile(expr).input_value(data).all()` で
評価し、結果配列を `_format_jq_results()` で `gh --jq` (= `jq -r`) 互換 raw
出力に整形する。出力契約（設計書 § 2）と実装の対応:

| jq 結果 | 実装の出力整形 |
|---------|----------------|
| string | 改行を含めてそのまま + 末尾 newline |
| number / bool | `decimal` / `true` / `false` + newline |
| null | 空行（newline のみ） |
| object / array | `json.dumps(..., separators=(",", ":"))` で compact JSON + newline |
| stream | 各 result を上記ルールで整形し `\n` 連結 |
| stream 内 null | 空行として混在（例: `1\n\n2\n`） |
| empty stream | 空文字列、exit 0 |
| syntax error / runtime error | `EXIT_RUNTIME_ERROR (3)`、stderr に jq 例外メッセージ |

`shutil.which("jq")` の runtime check は撤去。Python `jq` package import 失敗時
（runtime dependency 状態が壊れている場合）は exit 3 で明示エラー。

### § 3 PyYAML frontmatter

`_parse_frontmatter()` / `_serialize_frontmatter()` の 2 関数は名前を維持しつつ
実装を `yaml.safe_load()` / `yaml.safe_dump(allow_unicode=True, sort_keys=False)`
に置き換えた。frontmatter 抽出は既存 `_FRONTMATTER_RE` を再利用。

`_validate_issue_meta(meta, *, strict_slug: bool)` を新設し、設計書 § 3 の表に
従って validation:

| field | view_issue | resolve / write |
|-------|------------|-----------------|
| `id` | `local-<machine>-<n>` 一致 fail-fast | 同左 |
| `state` | `open` / `closed` 以外 fail-fast | 同左 |
| `labels` | list 以外 fail-fast | 同左 |
| `slug` | 不在許容、不正値は fail-fast | 不在 / 不正値とも fail-fast |
| `branch_prefix` | 不在許容、不正値は `validate_branch_prefix` で fail-fast | 同左 |
| その他 | 解釈せず保持 | 解釈せず保持 |

`branch_prefix` の値域は `_mappings.py:LABEL_TO_PREFIX` の values
（`feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `security`）
8 種に限定。任意 regex は採用せず、自由入力を provider 外へ漏らさない方針を維持。

byte-for-byte stability は要求せず、semantic round-trip（同じ field/value/body
を読めること）のみ保証する旨を実装コメントに記載。

### § 4 slug optional

`LocalProvider.create_issue(slug=None)` で `derive_slug_from_title(title)` を
呼ぶフォールバックに変更。`derive_slug_from_title` は既存の `providers/context.py`
にあり、空 slug 時は `"untitled"` を返す既存仕様で挙動を満たす。

CLI 側 `_local_issue_create()` の argparse で `--slug` は `required=True` →
`default=None` に変更。help を `"kebab-case slug (optional; derived from title
when omitted)"` に更新。

### § 5 comment write collision retry

`_atomic_write_new(path, content)` を新設。`os.open(path, O_CREAT | O_EXCL |
O_WRONLY, 0o644)` で exclusive create し、`os.write(fd, bytes)` 後 `os.close(fd)`。
既存 file がある場合は `FileExistsError` を投げる。

`comment_issue()` は `MAX_COMMENT_WRITE_RETRIES = 8` 回 loop で `_next_comment_seq()`
→ `_atomic_write_new()` を試行し、`FileExistsError` または `errno.EEXIST` の
`OSError` をキャッチして retry。8 回連続失敗で `LocalProviderError` を raise。

`_atomic_write()`（既存の `*.tmp` → `os.replace` 方式）は edit / close 用に残す。
issue.md の初回書き込みは counter lock + directory `mkdir` 衝突検出で守られる
ため変更しない。

## 4. 受け入れ条件の検証

### 機械検証（設計書 § 受け入れ条件）

| 条件 | 結果 |
|------|------|
| `ruff check` | PASS |
| `ruff format --check` | PASS |
| `mypy kaji_harness/` | PASS |
| `pytest` | 882 passed, 1 skipped（既存 skip 1 件は変更前後で不変） |
| `pyproject.toml` に `jq>=1.6` | PASS |
| `kaji issue view 1 --json body -q '.body'` が system jq 不在でも動作 | PASS（`test_view_jq_works_without_system_jq_binary`） |
| body 内 newline を保持して raw 出力 | PASS（`TestJqOutputFormatting::test_string_with_newline`） |
| jq empty stream は stdout なし exit 0 | PASS（`test_empty_stream_emits_no_output`） |
| jq syntax / runtime error は exit 3 | PASS（`TestApplyJqExitCodes` 3 件） |
| `kaji issue create --title "Hello World" --body "..."` が `local-pc1-1-hello-world` 形式 | PASS（`test_cli_create_without_slug_derives_from_title`） |
| `--title "!!!"` で `untitled` slug | PASS（`test_derive_slug_from_punctuation_only_returns_untitled` + `derive_slug_from_title` の既存仕様） |
| `--slug` 明示が優先される | PASS（`test_cli_create_with_explicit_slug_overrides_derived`） |
| `kaji run ... 1` / `pc1-1` / `local-pc1-1` が同じ `SessionState.issue_number == "local-pc1-1"` | PASS（`test_local_input_forms_resolve_to_same_canonical` parametrized 3 件 + `test_run_persists_state_under_canonical_dir`） |
| 旧 raw-id artifacts directory がある場合 canonical を使い WARN | PASS（`test_legacy_raw_artifacts_dir_emits_warning`） |
| `labels: [type:feature]` の inline list を読める | PASS（`test_inline_list_labels_round_trip`） |
| frontmatter semantic round-trip 検証 | PASS（quote / colon / null の 3 round-trip テスト） |
| comment 競合時に既存 file を上書きしない | PASS（`test_retries_on_existing_filename`） |
| comment 競合 retry 上限超で fail-fast | PASS（`test_fails_fast_after_retry_limit` + `test_max_retries_constant_is_eight`） |

### 手動確認

GitHub 利用不可のため tmp repo での end-to-end 手動確認は省略し、`pytest` の
medium 帯の integration テストに代替させた。代替の根拠:

- `test_cli_create_without_slug_derives_from_title` / `_with_explicit_slug_overrides_derived`
  は実 file I/O で `.kaji/issues/local-pc1-1-<slug>/issue.md` まで作成し
  directory 命名を assert する
- `test_run_persists_state_under_canonical_dir` は `WorkflowRunner.run()` を
  全行通し（CLI execute のみ mock）、`.kaji-artifacts/local-pc1-1/session-state.json`
  が canonical dir 配下にできることを構造的に検証
- error message の canonical id 解決失敗時表示は既存 `IssueContextResolutionError`
  の Phase 3-c テスト（`test_phase3c_runner.py::TestRunnerFailFastOnExplicitProvider`
  4 件）が引き続き PASS

`phase3c-implementation-report.md` への補正は § 5 docs 補正で完了済み（`§ jq 依存`
節 / `§ 4.3.1 末尾の判断保留節` / CLI フラグ表 の 3 か所）。

## 5. 設計判断の確認

設計書 § 判断済み論点 で固定された方針は実装でもそのまま採用。逸脱なし。

| 論点 | 設計書の判断 | 実装 |
|------|-------------|------|
| Python `jq` vs system `jq` | Python `jq` package を採用 | `jq>=1.6` を runtime dependency 化、`shutil.which("jq")` 撤去 |
| 既存 raw-id artifacts migration | 自動移動しない、WARN のみ | `_warn_legacy_artifacts()` で stderr 出力。`SessionState.load_or_create` に fallback 探索を入れない |
| frontmatter round-trip | semantic 等価のみ保証 | safe_dump の表記変化（quote / inline-list）を許容、テストは parsed value で比較 |
| `branch_prefix` 値域 | `_mappings.LABEL_TO_PREFIX.values()` に限定 | `validate_branch_prefix()` で frozenset 比較 |
| comment retry atomic 戦略 | `os.open(..., O_CREAT \| O_EXCL \| O_WRONLY)` | `_atomic_write_new()` で実装、`path.open("x")` は採用しない |
| retry 上限 | 8 | `MAX_COMMENT_WRITE_RETRIES = 8` を public 定数化（テストで参照） |

## 6. 既知のトレードオフ / 後続課題

### Phase 3-d 本体に持ち越し

設計書 § out-of-scope と一致。preflight では触らない。

| 項目 | 後続フェーズ |
|------|-------------|
| `kaji local init` 実装 | Phase 3-d |
| `.kaji/config.toml` への `[provider]` 追記 / `.gitignore` 更新 | Phase 3-d |
| `feature-development-local.yaml` 追加 | Phase 3-d |
| Skill の 5 変数移行 | Phase 3-d |
| `provider.type` 未設定 fallback 削除 | Phase 3-e |
| `kaji pr` の bare provider エラー化 | Phase 4 |
| `kaji sync from-github` | Phase 5 |

### 設計書未指定だが本実装で確定した点

- `_validate_issue_meta(strict_slug)` は `view_issue` でも呼ぶ。設計書 § 3 の
  表どおり、view 経路でも `id` / `state` / `labels` の壊れは検知する（slug 不在
  だけ許容）。これにより `local-pc1-9` のような directory に手書きした legacy
  frontmatter でも view まではでき、context 解決時に fail-fast する 2 段階構造
  になっている
- `_read_issue` で directory 名と frontmatter `id` の対応を強制
  （`dirname == issue_id or dirname.startswith(f"{issue_id}-")`）。これは設計書
  § 3 の「directory 名と `id` が対応している」を踏襲した強化
- comment retry の `_atomic_write_new()` は umask に依存せず `0o644` を明示。
  POSIX 環境で seq 衝突時の競合再試行は確認したが、Windows での `O_EXCL` 動作は
  既存の Windows 暫定（locking なし）と同じ扱いで本 preflight では特別扱いしない

### Python `jq` package のリスク

設計書 § 2 に記載されたとおり、Linux / macOS では wheel が存在し問題なし。
Windows install はオープンリスクとして残し、実際に詰まったら pure Python
alternative または system `jq` fallback を後続で検討する。本 preflight で
Windows 実機検証は行っていない（Phase 3 全体で Windows は locking 暫定扱い）。

## 7. Rollback 手順（参考）

設計書 § Rollback 方針に従い、各補正は独立して戻せる構造。Phase 3-d 着手前に
レビューで重大な問題が見つかった場合の手順:

| 変更 | rollback |
|------|----------|
| `jq` dependency | `pyproject.toml` から削除し `_apply_jq()` を Phase 3-c の `subprocess.run(["jq", "-r", ...])` 実装へ戻す |
| PyYAML frontmatter | `_serialize_frontmatter` / `_parse_frontmatter` / `_validate_issue_meta` / `_scalar` 旧実装を 89edf27 から復元 |
| slug optional | argparse `required=True` を戻し、`create_issue(slug=None)` の `ValueError` を復元 |
| comment retry | `comment_issue()` を旧 seq 採番 + `_atomic_write` 1 回書き込みへ戻す |
| canonical id | `WorkflowRunner.run()` の state / log / prompt 引数を `self.issue_number` へ戻す。`RunIssueContext` 削除 |

ただし rollback は local mode の実用性を下げるため、本 preflight は Phase 3-d
本体着手前に収束させる前提（設計書 § Rollback 末尾と一致）。

## 8. コミット粒度

設計書 § 実装順序に従い、provider 単体 → runner の順で進めた:

```
1. jq dependency 追加 + Python jq 化
2. PyYAML frontmatter 化 + read 時 validation
3. slug optional 化
4. comment write retry 実装
5. WorkflowRunner canonical id 化
6. テスト追加 + 既存テスト補正
7. docs 補正
```

実装中に新規発見の差分（`_validate_issue_meta` の strict_slug 切り替え、
directory 名 / id 対応の強制、`_atomic_write_new` の `os.open` 採用）も上記
6, 4 のステップに含めた。

GitHub 不可期間のため、本 worktree でのコミットは未確定（人間レビュー後に
`--no-ff` でマージする想定）。
