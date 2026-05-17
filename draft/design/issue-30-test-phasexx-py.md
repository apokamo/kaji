# [設計] test_phaseXX_*.py のフェーズ番号ベース命名をドメインベース命名に正規化する

Issue: gl:30

## 概要

`tests/` 配下の `test_phaseXX_*.py` 形式 15 ファイルを `git mv` でドメインベース命名に正規化し、機能依存する参照（`conftest.py` のファイル名リスト・docstring 相互参照）を追従更新し、テストファイル命名規約を `docs/dev/testing-convention.md` に明文化する。テストロジック・テスト ID は一切変更しない（振る舞い非変更）。

## 背景・目的

### 現状の問題（測定可能な形で）

`tests/` 61 エントリ中、開発フェーズ番号をファイル名に含むテストは 15 ファイル:

```
test_phase3c_dispatcher.py            test_phase4_dispatcher_gitlab.py
test_phase3c_runner.py                test_phase4_large_local.py
test_phase3d_default_branch.py        test_phase4_pr_bare_provider.py
test_phase3d_preflight.py             test_phase4_provider_type.py
test_phase3d_skills.py                test_phase4_skill_provider_guard.py
test_phase3e_config_validation.py     test_phase4_workflow_provider_match.py
test_phase3e_large_local.py           test_phase4_workflow_requires_provider.py
test_skill_phase2b_migration.py
```

問題点:

- 「Phase 3-e」「Phase 4」「Phase 2-B」は開発プロジェクトの内部マイルストーン番号であり、テスト対象の振る舞い・ドメインを表していない。フェーズ完了後はファイル名としての情報量がゼロになる時間的アーティファクト。
- 発見性の喪失: `test_phase4_provider_type.py` という名前から機能名で到達できない（`test_provider_type.py` なら自明）。
- ドメインの分散: dispatcher のテストが `test_phase3c_dispatcher.py` と `test_phase4_dispatcher_gitlab.py` にフェーズ軸で離散している。
- `docs/dev/testing-convention.md` にテストファイル命名規約が存在しない（`grep -niE 'naming|file name|ファイル名' docs/dev/testing-convention.md` で該当なし）。
- 大半のファイルは既にドメイン接尾辞（`_dispatcher` / `_runner` / `_provider_type` 等）を持ち、`phaseXX_` プレフィックスは無意味な前置詞として残っているだけ。

### Issue 確認コマンドの事実補正

Issue 本文の確認コマンド「`ls tests/test_phase*.py`（現状 15 件）」は、glob 上は **14 件** にしかヒットしない。15 番目の `test_skill_phase2b_migration.py` は `test_skill_` プレフィックスで始まるため `test_phase*` glob の対象外。本設計では Issue が列挙する 15 ファイルすべてを対象とし、`test_skill_phase2b_migration.py` を含める（改善指標の数値根拠を下記で再定義）。

### ベースライン計測

改修前の状態。実装フェーズ冒頭で再計測し、改修後と突き合わせる:

| 指標 | 計測コマンド | 改修前の値 |
|------|-------------|-----------|
| フェーズ番号付きテストファイル数（glob） | `ls tests/test_phase*.py \| wc -l` | 14 |
| フェーズ番号付きテストファイル数（列挙対象全体） | 上記 + `ls tests/test_skill_phase2b_migration.py` | 15 |
| テスト総数（`large_gitlab` 除く） | `pytest -m "not large_gitlab" --co -q \| tail -1` | 改修前に記録 |
| `large_local` テスト数 | `pytest -m large_local --co -q \| tail -1` | 改修前に記録 |
| `make check` 結果 | `make check` | 改修前に PASS を確認（gl:29 マージ後の green ベースライン） |

### 改善指標（測定可能な目標）

- `ls tests/test_phase*.py` の出力が 0 件（14 ファイルのリネーム完了）。
- `test_skill_phase2b_migration.py` を含む 15 ファイルすべてが `phaseXX` を含まないドメイン名に変わる。
- 改修前後でテスト総数・テスト ID・PASS/FAIL 内訳が完全一致（振る舞い非変更の証明）。
- `docs/dev/testing-convention.md` に「テストファイルは対象モジュール/ドメイン名で命名し、開発フェーズ番号を含めない」旨の規約が追記される。
- `make check` 通過。

## インターフェース

### 入力

なし（CLI / API の入力変更なし）。本変更はファイルシステム上のテストファイル名と、それを参照する文字列定数・docstring・ドキュメントの変更のみ。

### 出力

| 種別 | 変更内容 |
|------|---------|
| `tests/` 配下のファイル名 | 15 ファイルを `git mv` でリネーム（下記対応表） |
| `tests/conftest.py` | `_AUTOCREATE_OPT_OUT_FILES`（99-101 行）の 3 ファイル名文字列を新名へ更新 |
| docstring 相互参照 | リネーム対象ファイル内・`kaji_harness/providers/local.py` の docstring 中のファイルパス参照を新名へ更新 |
| `docs/dev/testing-convention.md` | テストファイル命名規約セクションを追記 |

公開 IF（CLI / Python API / pytest marker / テスト ID）は **不変**。

### 使用例

```bash
# 改修後の発見性: 機能名でテストファイルに到達できる
ls tests/test_dispatcher.py tests/test_provider_type.py

# フェーズ番号付きファイルが残っていないことの確認
ls tests/test_phase*.py        # → No such file
```

### 新旧名対応表（完了条件: 15 ファイルの対応表）

命名原則: **`phaseXX_` / `phase2b_` のフェーズ番号トークンを除去し、残るドメイン接尾辞をそのままファイル名にする。除去後に (a) 既存ファイルと衝突する、または (b) ドメインが曖昧になる場合のみドメイン名を補う。** `_large_local` サフィックスは pytest marker `large_local` と対応する種別マーカーであり除去しない（Issue 注意点 1）。

| # | 旧名 | 新名 | テスト対象ドメイン | 命名根拠 |
|---|------|------|-------------------|---------|
| 1 | `test_phase3c_dispatcher.py` | `test_dispatcher.py` | `cli_main` の `kaji issue` / `kaji pr` dispatch・config parsing | `phase3c_` 除去のみ |
| 2 | `test_phase3c_runner.py` | `test_runner.py` | `WorkflowRunner` + `prompt.build_prompt` | `phase3c_` 除去のみ。既存 `test_runner_before.py` / `test_runner_pr_context.py` とは別名（衝突なし） |
| 3 | `test_phase3d_default_branch.py` | `test_default_branch.py` | `default_branch` placeholder の provider 別供給 | `phase3d_` 除去のみ |
| 4 | `test_phase3d_preflight.py` | `test_preflight.py` | preflight 5 基盤補正（canonical id / jq / frontmatter / slug / comment retry） | `phase3d_` 除去のみ |
| 5 | `test_phase3d_skills.py` | `test_skill_placeholders.py` | Skill markdown の forbidden placeholder 静的検証 | `phase3d_` 除去後の `skills` は曖昧（kaji に `skills` モジュールは無い）。中身は placeholder 検証なのでドメイン名を補正（条件 b） |
| 6 | `test_phase3e_config_validation.py` | `test_config_validation.py` | config load 時の `machine_id` 検証 | `phase3e_` 除去のみ。既存 `test_config.py` とは別名（衝突なし） |
| 7 | `test_phase3e_large_local.py` | `test_local_cli_large_local.py` | `kaji local init` / `kaji issue` の subprocess E2E | 単純除去だと `test_large_local.py` で #9 と衝突（条件 a）。ドメイン（local CLI E2E）を補い `_large_local` 種別サフィックスは維持 |
| 8 | `test_phase4_dispatcher_gitlab.py` | `test_dispatcher_gitlab.py` | `kaji issue` / `kaji pr` の GitLab dispatcher | `phase4_` 除去のみ |
| 9 | `test_phase4_large_local.py` | `test_provider_guard_large_local.py` | 3 層（CLI / Skill / Workflow）bare-provider ガードの subprocess E2E | 単純除去だと #7 と衝突（条件 a）。ドメイン（provider guard E2E）を補い `_large_local` 種別サフィックスは維持 |
| 10 | `test_phase4_pr_bare_provider.py` | `test_pr_bare_provider.py` | `_handle_pr` の bare provider エラー化 | `phase4_` 除去のみ |
| 11 | `test_phase4_provider_type.py` | `test_provider_type.py` | `actual_provider_type` helper + `kaji config provider-type` | `phase4_` 除去のみ。既存 `test_providers_*.py`（複数形・providers パッケージのテスト）とは別名（衝突なし） |
| 12 | `test_phase4_skill_provider_guard.py` | `test_skill_provider_guard.py` | forge 専用 Skill の Step 0 ガード文言静的検証 | `phase4_` 除去のみ |
| 13 | `test_phase4_workflow_provider_match.py` | `test_workflow_provider_match.py` | `cmd_run` の workflow ↔ provider 整合検証 | `phase4_` 除去のみ |
| 14 | `test_phase4_workflow_requires_provider.py` | `test_workflow_requires_provider.py` | `Workflow.requires_provider` field 文法検証 | `phase4_` 除去のみ |
| 15 | `test_skill_phase2b_migration.py` | `test_skill_migration.py` | Skill markdown の `gh` 直呼び / legacy placeholder 移行の静的検証 | `phase2b_` トークンのみ除去。`test_skill_` プレフィックスは保持 |

> **Issue 注意点 2 への回答（large_local 2 ファイルの方針）**: `test_phase3e_large_local.py` / `test_phase4_large_local.py` は `_large_local` という**テスト種別**で束ねられているが、両者とも明確なドメインを持つ（前者 = local CLI の E2E、後者 = 3 層 provider guard の E2E）。「種別維持」案（両方 `test_large_local.py`）はファイル名衝突を起こすため採用不可。**ドメイン分割**を採用し、`<ドメイン>_large_local.py` 形式で命名する。`_large_local` 種別サフィックスは marker `large_local`（`pytest -m large_local` / `make test-large-local`）との対応で残す。

## 制約・前提条件

- **技術的制約**: `git mv` を使用しリネーム履歴を Git に追跡させる（`git log --follow` で旧名まで辿れる）。テストファイルの中身（テスト関数・assertion・fixture・marker）は変更しない。
- **公開 IF 不変**: テスト ID（`tests/<file>::<class>::<func>`）のうち変わるのはファイル名部分のみ。テスト関数名・クラス名は不変。
- **混在禁止**（Issue スコープ準拠）: テストロジックの変更・テスト追加/削除・本番ロジック変更を混ぜない。`kaji_harness/providers/local.py` の変更は docstring コメント 1 行のファイルパス参照更新のみで、実行時の振る舞いに影響しない。
- **前提**: テスト失敗修正 Issue gl:29 がマージ済み（`git log` 上 `480a059` 系で確認済）。RED 状態でのリネームを避け、振る舞い非変更を green ベースラインで検証する。
- **依存関係**: 本変更は他の進行中 worktree（`feat/135` / `feat/153` 等）と `tests/` のファイル名空間で競合しうる。マージ順序によっては rebase 衝突が起きるが、`git mv` のリネームは追従しやすい。

## 方針

### 追従更新が必要な参照（調査結果）

`grep -rnE 'test_phase[0-9]|test_skill_phase2b'` をリポジトリ全体（`draft/` 除く）に実施した結果、追従更新が必要な参照は以下に限られる:

| 参照箇所 | 種別 | 追従要否 | 理由 |
|---------|------|---------|------|
| `tests/conftest.py:99-101` `_AUTOCREATE_OPT_OUT_FILES` | **機能依存**（実行時にファイル名文字列と `request.node.fspath` を照合） | **必須** | `test_phase3c_runner.py` / `test_phase3d_preflight.py` / `test_phase3e_large_local.py` の 3 件。未更新だと autouse fixture の opt-out が壊れ、対象テストが `IssueContextResolutionError` で fail する |
| `tests/test_phase3c_dispatcher.py:5` docstring | docstring 内パス参照 | 必須 | `tests/test_phase3c_runner.py` への相互参照 |
| `tests/test_phase3c_runner.py:5` docstring | docstring 内パス参照 | 必須 | `tests/test_phase3c_dispatcher.py` への相互参照 |
| `kaji_harness/providers/local.py:773` docstring | docstring 内パス参照（コメント） | 必須 | `tests/test_phase3c_dispatcher.py:329-365` への参照。実行時の振る舞いには無関係 |
| `docs/` 配下 | — | **不要** | `grep` 結果 0 件。docs/ に `test_phase` 参照は存在しない |
| `.claude/` 配下 | — | 不要 | `grep` 結果 0 件 |
| `draft/design/**` 配下 | 過去 Issue の設計書内のテキスト参照 | **不要**（後述） | 完了済み Issue の歴史的記録 |

> **行番号の扱い**: 上記行番号（`conftest.py:99-101` 等）は本設計時点の値。実装フェーズで `git mv` 前に再 grep して現在値を確定する。

### draft/design/ 内参照を追従しない判断

`draft/design/**` に `test_phaseXX` を参照する箇所が約 80 行存在する（`issue-21` / `issue-24` / `issue-29` / `issue-7` / `issue-6` / `issue-11` / `local-pc5090-*` / `phase3c〜4-implementation-report` / `phase3c〜5-design` 等）。これらは **追従更新しない**。根拠:

1. **歴史的記録**: `draft/design/` 配下は完了済み Issue の設計書・実装レポート。当該設計書が書かれた時点のファイル名で事実を記述しており、後から書き換えると「その時点の事実」という記録が歪む。とりわけ gl:30 の前提である gl:29 の設計書（`issue-29-large-local-subprocess-11-git-tmp-path-l.md`）の作業記録を改変するのは不適切。
2. **完了条件の文言**: Issue 完了条件は「import 参照・**docs 内**パス参照が追従更新されている」。`docs/` 配下の参照は 0 件であり、`draft/` は `docs/` ではない。
3. **リンクチェック対象外**: `make verify-docs`（`Makefile:44`）は `python3 scripts/check_doc_links.py docs/ README.md CLAUDE.md .claude/skills/` を実行する。`draft/` はスコープ外のため、`draft/design/issue-local-pc5090-10-test-large-gitlab-e2e.md:434` の markdown リンク形式 `[tests/test_phase3e_large_local.py](../../tests/test_phase3e_large_local.py)` が旧名のまま残ってもリンクチェックは PASS する。
4. **Git で追跡可能**: `git mv` によるリネーム履歴があるため、旧名は `git log --follow` で辿れる。

この判断は設計レビューで再検討されうる論点として明示する。もしレビューで「draft も追従すべき」と判断された場合、追加コストは grep 置換のみで小さい。

### 移行ステップ（実装フェーズ向け）

```
1. ベースライン計測を記録（テスト総数 / large_local 数 / make check 結果）
2. 15 ファイルを `git mv` でリネーム（対応表どおり）
3. tests/conftest.py の _AUTOCREATE_OPT_OUT_FILES 3 件を新名へ更新
4. docstring 相互参照（test_dispatcher.py / test_runner.py / local.py）を新名へ更新
5. docs/dev/testing-convention.md に命名規約セクションを追記
6. 再計測: pytest 全体 + pytest -m large_local を実行し、改修前と件数・ID・結果が一致することを確認
7. ls tests/test_phase*.py が 0 件であることを確認
8. make check 通過を確認
```

`git mv` → 参照更新 → 規約追記は 1 コミットにまとめる（リネームと参照追従が分離すると中間状態でテストが壊れるため不可分）。

### testing-convention.md への規約追記方針

`docs/dev/testing-convention.md` に「テストファイル命名規約」セクションを新設し、以下を明文化する:

- テストファイルは対象モジュール / ドメイン名で命名する（`test_<domain>.py`）。
- 開発フェーズ番号・マイルストーン番号（`phaseXX` 等の時間的アーティファクト）をファイル名に含めない。
- テスト種別マーカー（`_large_local` 等、pytest marker と対応するサフィックス）は命名に含めてよい。

追記位置は既存セクション構成（「テストサイズ定義」「テスト戦略の原則」等）と整合する箇所を実装時に判断する。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義する。

### 変更タイプ

**テストファイルのリネーム（実行時の振る舞いを変えない refactor）**。`docs/dev/testing-convention.md` の 4 分類（実行時コード変更 / docs-only / metadata-only / packaging-only）のいずれにも厳密には該当しないが、性格は最も docs-only / refactor に近い:

- テストロジック（テスト関数・assertion・fixture・marker）は 1 行も変更しない。
- 本番コード（`kaji_harness/`）の変更は `local.py:773` の docstring コメント 1 行のみで、実行時の振る舞いに無関係。
- 検証の主眼は「振る舞い非変更（テスト総数・ID・結果が改修前後で一致）」。

### refactor 固有: 振る舞い非変更の保証（bridging）

`_shared/design-by-type/refactor.md` § 7 に従い、振る舞い非変更を**既存テスト全体**で担保する。bridging test は既存テストスイートそのもの:

- **改修前ベースライン**: `pytest -m "not large_gitlab" --co -q` でテスト総数を、`pytest -m large_local --co -q` で large_local テスト数を記録。`make check` の PASS を確認。
- **改修後**: 同コマンドを再実行し、テスト総数・テスト ID（ファイル名部分以外）・PASS/FAIL 内訳が改修前と完全一致することを確認。`large_local` ファイル（#7 / #9）が対象に含まれるため `pytest -m large_local` の実行は**必須**（`make check` のデフォルトには含まれるが、リネーム対象ファイルなので明示的に確認する）。
- リネーム漏れ・`conftest.py` 参照不整合は pytest のテスト収集失敗・対象テストの fail として顕在化する。

### Small / Medium / Large テスト

#### Small テスト
- 新規追加なし。リネーム対象ファイル内の既存 Small テストがファイル名変更後もそのまま PASS することを確認する（bridging）。

#### Medium テスト
- 新規追加なし。`conftest.py` の `_AUTOCREATE_OPT_OUT_FILES` 更新の正否は、opt-out 対象テスト（`test_runner.py` / `test_preflight.py` / `test_local_cli_large_local.py` の各 fail-fast 検証ケース）が改修後も PASS することで検証される（bridging）。

#### Large テスト
- 新規追加なし。`large_local` マーカー付き 2 ファイル（#7 / #9）が改修後も `pytest -m large_local` で全 PASS することを確認する（bridging）。

### 恒久回帰テストを追加しない理由

`docs/dev/testing-convention.md` § docs-only / metadata-only / packaging-only 変更の 4 条件に沿って:

1. **独自ロジックの追加・変更をほぼ含まない**: 変更はファイル名・文字列定数・docstring・ドキュメントのみ。実行時ロジックの新規追加なし。
2. **想定不具合パターンが既存ゲートで捕捉済み**: リネーム漏れ → `ls tests/test_phase*.py` の残存 / pytest 収集結果の差分で検知。`conftest.py` 参照不整合 → opt-out 対象テストの fail で検知。docstring 参照切れ → レビューで検知（`docs/` 外なので verify-docs 対象外だが、実行時の振る舞いに無関係）。
3. **新規テストの回帰検出情報の増分が小さい**: 「`test_phaseXX` 命名が再発したら fail する静的テスト」を追加することは技術的に可能だが、Issue スコープは「リネーム + 規約追記」であり「混在禁止: テスト追加/削除を混ぜない」と明記されている。完了条件にも命名 enforcement テストは含まれない。回帰防止は `docs/dev/testing-convention.md` の規約明文化で担保し、自動 enforcement テストは別 Issue 候補とする。
4. **理由をレビュー可能な形で説明**: 本セクション。

### 変更固有検証

- `make check`（`ruff check` → `ruff format` → `mypy` → `pytest -m "not large_gitlab"`）通過。
- `pytest -m large_local` 全 PASS（リネーム対象 2 ファイルを含む）。
- `make verify-docs` 通過（`testing-convention.md` 追記後のリンク健全性確認。`docs/` 配下にファイル名参照は無いためリンク切れリスクは低いが、規約セクション内に追記したリンクの健全性を確認する）。
- `ls tests/test_phase*.py` の出力が 0 件。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 新しい技術選定なし。命名規約はアーキテクチャ決定ではなく開発規約 |
| `docs/ARCHITECTURE.md` | なし | アーキテクチャ構造の変更なし |
| `docs/dev/testing-convention.md` | **あり** | テストファイル命名規約セクションを新設（本 Issue スコープ。完了条件） |
| `docs/dev/` その他 | なし | `grep` 結果 0 件。`development_workflow.md` 等にテストファイル名のハードコード参照なし |
| `docs/reference/` | なし | `grep` 結果 0 件。`testing-size-guide.md` 等にファイル名参照なし |
| `docs/cli-guides/` | なし | CLI 仕様変更なし |
| `CLAUDE.md` | なし | `grep` 結果 0 件。Documentation Index に testing-convention.md は既登録済 |
| `draft/design/**` | なし（意図的に不追従） | 過去 Issue の歴史的記録。本設計「方針」§ draft/design/ 内参照を追従しない判断 を参照 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue gl:30 本文 | GitLab Issue #30 | 対象 15 ファイルの列挙、改善指標、完了条件、注意点 1（`_large_local` サフィックス維持）・注意点 2（large_local 2 ファイルの種別/ドメイン判断）、混在禁止スコープ、gl:29 前提の指定元 |
| refactor 設計ガイド | `.claude/skills/_shared/design-by-type/refactor.md` | 「外部から観測可能な振る舞いを変えないことが絶対要件」「測定可能な改善指標」「ベースライン計測」「振る舞い非変更を既存テストで担保（bridging）」「既存テストで十分な場合は新規不要、エビデンスを書く」（§ 7） |
| テスト規約 | `docs/dev/testing-convention.md` | 変更タイプ別の恒久テスト要否判断、docs-only/metadata-only/packaging-only で新規テスト不要とする 4 条件、テスト戦略セクションの書き方 |
| `tests/conftest.py:98-118` | リポジトリ内（worktree `refactor/30`） | `_AUTOCREATE_OPT_OUT_FILES` がファイル名文字列で `request.node.fspath` と照合する機能依存。リネーム時の必須追従対象の根拠 |
| 各リネーム対象ファイルの docstring | `tests/test_phase3c_dispatcher.py:1-15` / `test_phase3c_runner.py:1-8` 他 | 各ファイルのテスト対象ドメインの一次根拠（対応表の「テスト対象ドメイン」列の出典） |
| `kaji_harness/providers/local.py:773` | リポジトリ内 | docstring コメント中の `tests/test_phase3c_dispatcher.py:329-365` 参照。追従対象 |
| `Makefile:43-44` | リポジトリ内 | `verify-docs` ターゲット定義 `python3 scripts/check_doc_links.py docs/ README.md CLAUDE.md .claude/skills/`。`draft/` がリンクチェック対象外であることの根拠 |
| `grep -rnE 'test_phase[0-9]\|test_skill_phase2b'` 結果 | 本設計時の調査ログ | `docs/` / `.claude/` 配下に参照 0 件、追従必須は `conftest.py` + docstring 数件、`draft/design/**` に約 80 行（不追従判断の対象）であることの根拠 |
