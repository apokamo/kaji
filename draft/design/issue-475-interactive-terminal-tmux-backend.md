# [設計] interactive terminal の tmux backend 使用時に非推奨警告を出す

Issue: #475

## 概要

`kaji run` の起動時に、tmux backend が実際に使われる場合と、tmux 削除後にエラーになる設定・CLI 指定が見つかった場合に、
tmux backend の廃止予定・移行先・修正すべき設定の場所・移行ガイドを示す警告を stderr へ 1 プロセス 1 回だけ出す。
実行結果・終了コード・stdout・artifact は変えない。

## 背景・目的

親 Issue #466 の `## 決定事項`（grill-me Q3、メンテナ回答「A」）で、tmux backend の削除（子 Issue B）の前に予告リリースを挟み、
「tmux backend が使われたとき（明示指定でも既定値でも）に stderr へ非推奨の警告を出し、CHANGELOG の Deprecated と
Release notes で告知する」と決まった。

現行の既定値は `interactive_terminal_backend = "tmux"`（`kaji_harness/config.py:58`）であり、
`agent_runner = "interactive_terminal"` で backend を書いていない利用者は、気づかないまま tmux を使っている。
Release notes を読まない利用者が削除前に気づける手段は実行時の警告しかない。

また #466「初回削除リリースの設定契約」では、削除後は config に明示された `tmux` が headless 実行でもエラーになり、
config の検証が CLI override より先に行われるため `--agent-runner headless` などでは救済できない。したがって
interactive で実行していない利用者でも、per-key マージ後の config 値が明示的な `tmux` なら削除後に影響を受ける。

### ユースケース

- **tmux 利用者として**、削除リリースより前に `kaji run` の実行時に、tmux の廃止予定・移行先（Herdr または headless）・
  修正すべき設定ファイルを知りたい。そうすれば Herdr の導入、起動手順の変更、設定の修正を計画的に進められる。
- **config に `tmux` を書いたまま headless で使っている利用者として**、削除後に config 読み込みがエラーになる前に、
  どのファイルの値を消せばよいかを知りたい。
- **headless だけを使い backend を設定していない利用者として**、関係のない警告に煩わされたくない。

### 代替案と不採用理由

| 案 | 不採用理由 |
|----|-----------|
| config 読み込み（`KajiConfig._load`）で警告する（#438 の local provider と同じ位置） | `KajiConfig.discover()` は `kaji issue` / `kaji pr` / `kaji config` / `baseline_precheck` など全 command が呼ぶ。workflow 内の agent は step ごとに `kaji issue ...` を多数呼び、プロセスごとに警告が出て agent の作業ログを汚す（module flag は subprocess を跨いで重複を抑止できない）。`[execution]` を消費するのは `kaji run` だけであり、利用者は移行前に必ず `kaji run` で警告を見る。加えて `artifacts.py` の設計方針（「`KajiConfig.discover()` には副作用を入れず」、`kaji_harness/artifacts.py:8`）に反する |
| runner の interactive dispatch（`runner.py:569-582`）で警告する | 実効 runner が headless のときに出せず、「config に明示 `tmux`、runner は headless」の利用者（削除後にエラーになる）を取りこぼす。step ごとに通過するため 1 回化の flag も別途必要 |
| Python 標準の `warnings.warn(DeprecationWarning)` | 既定の warning filter では `__main__` 以外の DeprecationWarning が表示されず、利用者に届かない。前例（#381 / #438 / #473）は `print(..., file=sys.stderr)` 系で統一されている |

## インターフェース

### 入力

警告の判定は次の 3 つの入力だけで決まる。

| 入力 | 型 | 意味 |
|------|----|------|
| config 解決値 `C` | `ExecutionConfig`（CLI override 適用**前**） | tracked `.kaji/config.toml` と `.kaji/config.local.toml` の `[execution]` を per-key マージした結果。`interactive_terminal_backend` の値と、そのキーを定義したファイル（下記の新フィールド） |
| 実効値 `E` | `ExecutionConfig`（`_apply_execution_overrides` 適用**後**） | 実際に runner へ渡る `agent_runner` と `interactive_terminal_backend` |
| CLI 指定 `F` | `str \| None` | `kaji run --interactive-terminal-backend` の値（未指定は `None`） |

`ExecutionConfig` に 1 フィールドを追加する:

| フィールド | 型 | 既定値 | 意味 |
|-----------|----|--------|------|
| `interactive_terminal_backend_source` | `Path \| None` | `None` | per-key マージ後の `interactive_terminal_backend` を定義したファイル。`_parse_execution` 内の既存 `source()` と同じ解決（overlay にキーがあれば `config.local.toml`、なければ tracked）。どちらのファイルにもキーが無い（built-in default）場合は `None`。CLI override では更新しない（config 解決の記録であり実効値の出所ではない） |

### 判定規則

警告を出す条件は次の (a)〜(c) のいずれかが真であること（OR）。

| ID | 条件 | 対応する完了条件 |
|----|------|------------------|
| (a) | `E.agent_runner == "interactive_terminal"` かつ `E.interactive_terminal_backend == "tmux"` | 実効 backend が tmux で interactive 実行する（明示指定でも既定値でも） |
| (b) | `C.interactive_terminal_backend_source is not None` かつ `C.interactive_terminal_backend == "tmux"` | per-key マージ後の config 値が、どちらかのファイルで明示的に定義された `"tmux"`（runner・CLI override に関係なく） |
| (c) | `F == "tmux"` | CLI で `--interactive-terminal-backend tmux` を指定 |

上記がすべて偽なら警告しない。完了条件の非発生条件との対応:

- runner が headless で backend をどこにも明示していない → (a) は runner で偽、(b) は source `None` で偽、(c) は `F is None` で偽
- 実効 backend が `herdr` で、マージ後の config 値と CLI のどちらも `tmux` ではない → (a) は backend で偽、(b) は値で偽、(c) は偽。
  tracked の `tmux` が `config.local.toml` の `herdr` で上書きされる場合、マージ後の値は `herdr`（source は `config.local.toml`）なので (b) は偽

判定表（代表ケース。テストの観点表を兼ねる）:

| # | tracked | local | CLI runner | CLI backend | 実効 runner / backend | 警告 | 成立条件 / 警告に載る出所 |
|---|---------|-------|------------|-------------|------------------------|:----:|---------------------------|
| 1 | runner 未指定、backend 未指定 | なし | なし | なし | headless / tmux | しない | — |
| 2 | `agent_runner = "interactive_terminal"`、backend 未指定 | なし | なし | なし | interactive / tmux | する | (a)。出所 = built-in default |
| 3 | backend `tmux` | なし | なし | なし | headless / tmux | する | (b)。出所 = tracked |
| 4 | backend `tmux` | backend `herdr` | なし | なし | headless / herdr | しない | — |
| 5 | backend `tmux` | backend `herdr` | interactive | なし | interactive / herdr | しない | — |
| 6 | backend `herdr` | backend `tmux` | なし | なし | headless / tmux | する | (b)。出所 = `config.local.toml` |
| 7 | backend `tmux` | なし | interactive | `herdr` | interactive / herdr | する | (b)。出所 = tracked（削除後は config エラーになり CLI で救済できない） |
| 8 | 未指定 | なし | なし | `tmux` | headless / tmux | する | (c)。出所 = CLI |
| 9 | 未指定 | なし | interactive | `tmux` | interactive / tmux | する | (a)+(c)。出所 = CLI |
| 10 | backend `tmux` | なし | interactive | `tmux` | interactive / tmux | する | (a)+(b)+(c)。出所 = tracked と CLI の両方を 1 通の警告に列挙 |
| 11 | `agent_runner = "interactive_terminal"`、backend `herdr` | なし | なし | なし | interactive / herdr | しない | — |
| 12 | 未指定 | なし | interactive | `herdr` | interactive / herdr | しない | — |

### 出力

- **副作用**: 条件成立時、stderr に警告を 1 行（`print(..., file=sys.stderr)`）出す。1 プロセスで 2 回目以降の判定成立では出さない
- **stdout / 終了コード / `verdict.yaml` / run artifact**: 変更しない。runner へ渡る `KajiConfig` も変えない
  （新フィールドは runner・logger・state のいずれからも参照・直列化されない。`dataclasses.asdict` 等で `ExecutionConfig` を
  直列化する箇所が無いことを `grep -rn "asdict" kaji_harness` で確認済み）
- **警告文の構成**（英語。既存の CLI 出力と揃える）。先頭の固定句 `WARNING: the tmux interactive terminal backend is deprecated`
  は CHANGELOG の「影響の判定方法」で grep 対象にするため、変更しない契約とする。

```text
WARNING: the tmux interactive terminal backend is deprecated and will be removed in a subsequent kaji release. tmux is selected by: <出所1>; <出所2>. Migrate to Herdr (interactive_terminal_backend = "herdr", run kaji inside a Herdr pane) or to the headless runner (agent_runner = "headless"). <追加文>See docs/cli-guides/interactive-terminal-runner.md#tmux-backend-deprecation
```

| 要素 | 内容 | 完了条件との対応 |
|------|------|------------------|
| 廃止予定と削除 | `is deprecated and will be removed in a subsequent kaji release`（削除 version は #466 で未固定のため version 番号は書かない） | 廃止予定で後続リリースで削除 |
| 出所（該当するものをすべて、この順で列挙） | (b) 成立時: `interactive_terminal_backend = "tmux" in <source のパス>`。(c) 成立時: `--interactive-terminal-backend tmux`。(a) のみが成立し、config 明示も CLI 指定も無い時: `the built-in default (interactive_terminal_backend is not set)` | 定義ファイル（`source()` と同じ解決） |
| 移行先 | Herdr（`interactive_terminal_backend = "herdr"`）と headless（`agent_runner = "headless"`） | 移行先 |
| 追加文 | (b) 成立時のみ `Remove or change the value in that file: after removal, an explicit "tmux" in config fails config loading even with --agent-runner headless or --interactive-terminal-backend herdr. `。built-in default が出所の時のみ `After removal, the default backend becomes Herdr. ` | 削除後の契約（#466） |
| docs の場所 | `docs/cli-guides/interactive-terminal-runner.md#tmux-backend-deprecation`（新設節。日本語版は同ガイドの `.ja.md` に対応節を置く） | 移行方法の docs の場所 |

ファイルパスは `source()` が返す `Path` をそのまま表示する（`ConfigLoadError` が定義元ファイルを示すのと同じ表示）。

### 使用例

```python
# kaji_harness/commands/run.py（cmd_run 内、疑似コード）
config = KajiConfig.discover(start_dir=start_dir)          # C: CLI override 前
effective = _apply_execution_overrides(config, args)        # E: CLI override 後
_emit_tmux_backend_deprecation_warning(
    configured=config.execution,
    effective=effective.execution,
    cli_backend=getattr(args, "interactive_terminal_backend", None),
)                                                           # 条件成立時のみ stderr に 1 回
config = effective
# 以降（get_provider → workflow load → WorkflowRunner）は現行どおり
```

利用者から見た例（tracked config に `interactive_terminal_backend = "tmux"`、headless 実行）:

```text
$ kaji run .kaji/wf/official/dev.yaml 123
WARNING: the tmux interactive terminal backend is deprecated and will be removed in a subsequent kaji release. tmux is selected by: interactive_terminal_backend = "tmux" in /path/repo/.kaji/config.toml. Migrate to Herdr (...) or to the headless runner (...). Remove or change the value in that file: ... See docs/cli-guides/interactive-terminal-runner.md#tmux-backend-deprecation
（以降、現行と同じ出力）
```

### エラー

警告処理は新しい失敗経路を持たない。

- config 読み込みが `ConfigLoadError` / `ConfigNotFoundError` で失敗した場合は、警告判定に到達せず現行どおり終了する
- 警告判定は純粋関数（入力 3 つから `str | None`）で、I/O を行わない。stderr への書き込み失敗は現行の他の `print(..., file=sys.stderr)` と同じ扱い（特別な握りつぶしはしない）

## 制約・前提条件

- **挙動不変**: 警告以外の挙動（tmux / Herdr / headless の実行結果、終了コード、stdout、`verdict.yaml`、run artifact）を変えない。
  `_apply_execution_overrides` の戻り値と、`WorkflowRunner` に渡る config は変更しない
- **stdout 不使用**: skill / 自動化は stdout を解析するため、警告は stderr のみ
- **per-key precedence 維持**: `_parse_execution` のマージ・検証ロジックと既存エラーメッセージは変えない。新フィールドの記録だけを足す
- **出す場所は `kaji run` のみ**: `kaji run-series` / failure triage の recovery child / `kaji recover` が起動する child は
  それぞれ独立した `kaji run` プロセスなので、各プロセスで 1 回ずつ出る（「1 プロセス 1 回」の範囲内）
- **スコープ外**（Issue スコープ境界）: tmux backend の削除・拒否、既定値の変更、`interactive_terminal.py` /
  `interactive_terminal_herdr.py` の内部整理（#474）、Herdr missing CLI 文言（`interactive_terminal_herdr.py:492`）の変更、
  環境の自動検出、公開中の managed starter の更新
- **依存**: 新規ライブラリなし。`kaji_harness/config.py`（`ExecutionConfig` / `_parse_execution`）と
  `kaji_harness/commands/run.py`（`cmd_run` / `_apply_execution_overrides`）に閉じる
- **#474 との関係**: #474 の interactive terminal 共用処理分離は、基点 `a2bf9a2` に含まれる `f05ccbb` で完了済み。
  本 Issue は分離済みの構成を前提とし、`interactive_terminal*.py` の内部構成は変更しない

## 変更スコープ

| ファイル | 変更 |
|----------|------|
| `kaji_harness/config.py` | `ExecutionConfig.interactive_terminal_backend_source` を追加。`_parse_execution` でキーがマージ結果にあれば `source("interactive_terminal_backend")`、無ければ `None` を設定。警告文を組み立てる純粋関数 `tmux_backend_deprecation_warning(configured, effective, cli_backend) -> str \| None` と固定句定数を追加 |
| `kaji_harness/commands/run.py` | `_emit_tmux_backend_deprecation_warning(...)` と module-level の 1 回化 flag を追加し、`cmd_run` の `_apply_execution_overrides` 直後で呼ぶ |
| `tests/test_tmux_backend_deprecation.py`（新規） | Small / Medium / Large の新規テスト（下記テスト戦略） |
| `tests/test_config.py` | `TestExecutionOverlay` に source 記録の Medium テストを追加（既存テストは変更しない） |
| docs（日英） | 影響ドキュメント参照 |
| `CHANGELOG.md` | `## [Unreleased]` の `### Deprecated` に下記下書きを記載 |

## 方針

1. **config 解決の記録**（`config.py`）: `_parse_execution` の既存 `merged` / `source()` を使い、
   `"interactive_terminal_backend" in merged` なら `source(...)` を、そうでなければ `None` を
   `ExecutionConfig(interactive_terminal_backend_source=...)` に渡す。検証ロジック・エラー文言は触らない
2. **判定と文言**（`config.py` の純粋関数）: 判定規則 (a)〜(c) を評価し、成立した出所を「config 明示 → CLI → built-in default」
   の順で集め、警告文を返す。不成立なら `None`。#466 の削除後契約に属する知識（config 明示 `tmux` は削除後エラー）を
   config の近くに置き、子 Issue B が同じ場所でエラーへ置き換えられるようにする
3. **出力と 1 回化**（`commands/run.py`）: `_emit_tmux_backend_deprecation_warning` は純粋関数の結果が `None` でなく、
   かつ module-level flag が未設定のときだけ `print(message, file=sys.stderr)` して flag を立てる（#438 の
   `warn_local_provider_deprecated` と同じ形。`git show 0ab4f19:kaji_harness/config.py` の `:29-39`）。
   既存の `_emit_provider_overlay_divergence_warning`（`commands/config.py:21`）と同じく `_emit_*` 命名にする
4. **呼び出し位置**: `cmd_run` で config 読み込み成功・`_apply_execution_overrides` 適用の直後、`get_provider` の前。
   以降の provider / workflow 検証が失敗しても、警告は既に出ている（利用者は失敗した実行でも気づける）

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 予告リリースを挟み、tmux 使用時に stderr 警告を出す | 本 Issue で警告を実装し、次のマイナーで予告。削除は B | 人間決定: #466 `## 決定事項`「人間決定」表（grill-me Q3 へのメンテナ回答「A」）、[grill-me provenance](https://github.com/apokamo/kaji/issues/466#issuecomment-6228216748) | 判定入力を C / E / F の 3 つに分解し、判定規則 (a)〜(c) と判定表 12 ケースへ具体化 |
| 発生条件に「マージ後の config 値が明示 `tmux`（runner 不問）」「CLI `tmux`」を含め、上書き済みの tracked `tmux` は除外 | 判定規則 (b) / (c)、判定表 #4 / #5 | Issue #475 本文の完了条件・重要判断表（readiness PASS 済み）。Issue 上は AI の詳細化で、根拠は #466「初回削除リリースの設定契約」（per-key precedence 維持、明示 `tmux` は headless でもエラー）。本設計レビューで再検査 | 「明示」を「`[execution]` マージ結果にキーが存在する」と定義し、定義ファイルを既存 `source()` で解決 |
| headless で backend 未明示の利用者には警告しない | 判定表 #1 | Issue #475 完了条件（非発生条件）。根拠は同上（削除後も影響なし） | (a)〜(c) がすべて偽になることで表現（特別扱いの分岐を作らない） |
| 警告の出す場所を `kaji run` だけに限定する | `cmd_run` の CLI override 適用直後。他 command では出さない | **AI の仮定**。Issue #475 重要判断表で「`kaji run` 以外のコマンドでも出すかは設計で確定する」と設計へ委任されている。根拠: `[execution]` の消費者は `kaji run` のみ、config 読み込みは workflow 内 agent の `kaji issue` 等でも多数走り警告が agent ログを汚す、`artifacts.py:8` の「discover に副作用を入れない」方針。検査先: `issue-review-design` | 代替案表に不採用理由を記載。series / recovery child は別プロセスとして各 1 回 |
| 警告の形式（stderr、`print`、1 プロセス 1 回、module-level flag） | #438 と同形 | **AI の仮定**（#466 `## 決定事項`「AI の仮定」表: 警告形式は `inject_verdict` の前例に合わせる）。根拠: #438（`0ab4f19`）と #473 の stderr 警告、CHANGELOG 0.21.0 / Unreleased の Deprecated。検査先: `issue-review-design` | 判定（純粋関数）と出力（flag 付き emitter）を分離し、Small で判定、Medium/Large で出力を検証できる形にした |
| 警告文の文言と固定句 | 上記「警告文の構成」 | **AI の仮定**。根拠: Issue #475 完了条件「警告文に含める」4 要素、#438 の文言（`WARNING: ... deprecated and will be removed ... Migrate to ... See docs/...`）。検査先: `issue-review-design` / `issue-review-code` | 出所の列挙順、(b) 時の「削除後は CLI で救済できない」追加文、default 時の「削除後は Herdr が既定」追加文を追加 |
| `ExecutionConfig` へ source フィールドを追加 | `interactive_terminal_backend_source: Path \| None = None` | **AI の仮定**（内部データ構造。two-way door）。根拠: 既存テストに `ExecutionConfig` の等価比較・直列化が無い（`grep` で確認）、既定値付きのため既存の構築箇所は無変更。検査先: `issue-review-code` | CLI override では更新しない（config 解決の記録）と定義 |
| CHANGELOG `[Unreleased]` への Deprecated 記載 | 実装時に下書きを `### Deprecated` へ記載 | **AI の仮定**。Issue の完了条件は「設計書に下書きとして残す」だが、#473 / #448 が `[Unreleased]` へ直接記載しており、`release` skill は `[Unreleased]` を新 version へ移す運用（`.claude/skills/release/SKILL.md` Step 3）。検査先: `issue-review-design` | 下書きは本設計書に残し、同文を CHANGELOG へ転記 |
| 設計書のファイル名 | `draft/design/issue-475-interactive-terminal-tmux-backend.md` | **AI の仮定**。Issue 完了条件の例示名は `issue-475-tmux-backend-deprecation-warning.md` だが、harness が後続 step に注入する `design_path` は本ファイル名（Issue タイトル由来）。後続 step の参照先と一致させることを優先した。検査先: `issue-review-design` | `issue-[id]-*.md` の glob 規約には両方とも合致 |

one-way door の未決は無い。公開 CLI の stderr に警告を足すこと自体は #466 の人間決定であり、終了コード・stdout・設定契約は変えない。

## テスト戦略

### 変更タイプ

- 実行時コード変更（`kaji run` の stderr 出力と `ExecutionConfig` の新フィールド）

### 実行時コード変更の場合

全テストで、1 回化 flag を autouse fixture（`monkeypatch.setattr`）で毎テスト未設定に戻す（#438 のテストと同じ形）。

#### Small テスト

- **判定規則の網羅**: 純粋関数 `tmux_backend_deprecation_warning` に、判定表 12 ケースに相当する C / E / F を直接与え、
  警告の有無と、警告に載る出所（tracked パス / local パス / CLI / built-in default）を検証する。
  特に #4 / #5（上書き済み tracked `tmux`）と #7（config 明示 `tmux` + CLI `herdr`）を独立ケースにする
- **警告文の必須要素**: 固定句 `WARNING: the tmux interactive terminal backend is deprecated`、`will be removed`、
  `herdr`、`headless`、docs パス `docs/cli-guides/interactive-terminal-runner.md#tmux-backend-deprecation` を含むこと。
  (b) 成立時は source のパスと「after removal ... fails config loading」の追加文を含み、default のみの時は含まないこと
- **複数出所の集約**: #10 で 1 通の警告に tracked と CLI の両方が順序どおり載ること
- **1 プロセス 1 回**: `_emit_tmux_backend_deprecation_warning` を条件成立の入力で 2 回呼び、`capsys` の stderr に固定句が 1 回、
  stdout が空であること。条件不成立の入力では何も出ず flag も立たないこと（後続の成立時に出る）

#### Medium テスト

- **source 記録（`tests/test_config.py`、実ファイル I/O）**: `KajiConfig._load` で、tracked のみ定義 → tracked パス、
  local のみ定義 → local パス、両方定義 → local パス（値は local の値）、どちらも未定義 → `None` を検証する。
  既存 `test_overlay_overrides_interactive_terminal_backend` は変更しない
- **`cmd_run` 結合（実 config ファイル + argparse）**: `create_parser()` で組み立てた引数と、tmp dir に書いた tracked /
  local config（`get_provider` を通すため `[provider]` に `type = "github"` と `repo` を置く）で `cmd_run` を in-process
  実行し、存在しない workflow path で `EXIT_DEFINITION_ERROR` に到達させる（`get_provider` は `GitHubProvider` を構築する
  だけで `gh` を実行しない: `kaji_harness/providers/__init__.py:95-106`）。
  判定表の代表ケース（#1, #2, #3, #4, #6, #7, #8, #12）で stderr の警告有無を検証し、終了コードと stdout が
  警告の有無で変わらないことを検証する。`cmd_run` を同一プロセスで 2 回呼んで警告が 1 回であることも検証する
- **成功経路の挙動不変**: 最小 workflow YAML と、`WorkflowRunner` を差し替えた stub（受け取った config を記録し成功 state を返す）で
  `cmd_run` を実行し、tmux 警告が出るケースでも終了コード 0 と stdout の完了行が変わらないこと、stub が受け取った
  `config` が同じ config と引数に `_apply_execution_overrides` を直接適用した結果と等しいこと（新フィールドを含め、
  警告処理が runner へ渡る config を変えないこと）を検証する
- **隔離**: `cmd_run` は provider 解決と overlay divergence 検査で git を呼ぶため、workflow 内実行で `tmp_path` が repo 内に
  なる問題（`docs/dev/testing-convention.md` § workflow 内実行での `tmp_path`）を避け、`outside_project_tmp_path` に
  `git init` した repo を使う。`subprocess.run` の盲目 patch は使わない

#### Large テスト

- **実 CLI の stderr 契約（`large` + `large_local`）**: `git init` した一時 repo に config を置き、
  `sys.executable -m kaji_harness.cli_main run <存在しない workflow> 1` を subprocess で実行する（#438 の
  `test_cli_local_provider_warns_on_stderr_only` と同じ起動方法。ネットワーク・`gh` 呼び出しに到達する前に終了する）。
  config 明示 `tmux` では stderr に固定句がちょうど 1 回出ること、config 明示 `herdr` では出ないこと、
  両者で終了コードと stdout が一致することを検証する
- 実 tmux / 実 Herdr / 実 agent の起動は不要: 警告は runner 起動前に確定し、runner 側の経路は変更しない。
  既存の interactive dispatch / tmux / Herdr テスト（`tests/test_runner_interactive_dispatch.py`、
  `tests/test_interactive_terminal_*.py`）が assertion を変えずに通ることを `make check` で確認する

### 長時間 acceptance の配置

該当なし。nested `kaji run` の full workflow、外部 agent を含む dogfood、fresh 環境の反復 acceptance は無い。
Large テストは早期終了する `kaji run` の subprocess（1 回あたり数秒、timeout 30 秒）で、implement step の timeout の
25% を大きく下回る。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/cli-guides/interactive-terminal-runner.md` / `.ja.md` | あり | 新設節「tmux backend deprecation」（anchor `#tmux-backend-deprecation`、警告文が参照）: 廃止予定と削除時期の扱い、影響を受けるかの判定（判定規則 (a)〜(c) の利用者向け表現）、警告例、移行方法（Herdr / headless、tracked config と `config.local.toml` の両方から明示 `tmux` を消す）。`### tmux` 前提条件節・Configuration 例のコメント・Fixed precedence の既定値記述に非推奨の注記を付ける |
| `docs/reference/configuration.md` / `.ja.md` | あり | `[execution]` 表の `interactive_terminal_backend` 行と説明段落に、`tmux` が非推奨であること、移行先、警告の発生条件、ガイドへのリンクを記載。設定例のコメント（`configuration.md:300`）に非推奨を明記。触れる行の Source 列の行番号を現行に合わせる |
| `docs/guides/python-starter.md` / `.ja.md` | あり | § 2.5 Interactive terminal runner (tmux) と Troubleshooting 行に、tmux backend が非推奨であること、移行先（Herdr / headless）、修正すべき設定の場所（`.kaji/config.toml` と `.kaji/config.local.toml` の `[execution]`）とガイドへのリンクを記載 |
| `README.md` / `README.ja.md` | あり（最小） | 「tmux interactive terminal runner」節に非推奨の 1〜2 文とガイドへのリンクを追加（#438 も README に非推奨を記載した前例）。節全体の書き換えは B |
| `CHANGELOG.md` | あり | `[Unreleased]` `### Deprecated` に下記下書きを記載 |
| `llms.txt` | なし | 機能概要の索引であり、非推奨の告知先ではない。tmux 記述の除去は B（#466 子 Issue B の受け入れ条件に `llms.txt` が含まれる） |
| `docs/adr/` | なし | 新しい技術選定・方針変更は無い（方針は #466 で決定済み）。ADR 007 の更新は B の受け入れ条件 |
| `docs/ARCHITECTURE.md` | なし | モジュール構成は変わらない |
| `docs/dev/` | なし | 開発ワークフロー・テスト規約は変わらない |
| `AGENTS.md` / `CLAUDE.md` | なし | 規約変更なし |
| managed starter（公開 repo） | なし（本 Issue） | スコープ外。予告リリース後の starter-sync で追跡（Issue の事後確認項目） |

## CHANGELOG Deprecated / Release notes 下書き

予告リリース（次のマイナー）の CHANGELOG `### Deprecated` と Release notes に載せる内容。#473 の Deprecated 項目と同じく
「壊れる契約 / 影響の判定方法 / 移行方法」の 3 要素で書く。

```markdown
- **The tmux backend of the interactive terminal runner** is deprecated and
  will be removed in a subsequent release (#475, parent #466). Herdr becomes
  the only interactive terminal backend; the headless runner stays. In this
  release tmux still works unchanged. `kaji run` now prints a deprecation
  warning to stderr once per process when tmux is used by the interactive
  runner (explicitly or as the default), when the merged `[execution]` config
  explicitly sets `interactive_terminal_backend = "tmux"`, or when
  `--interactive-terminal-backend tmux` is given. stdout, exit codes, and run
  artifacts are unchanged.
  - **Broken contract**: after removal, `interactive_terminal_backend = "tmux"`
    in `.kaji/config.toml` or `.kaji/config.local.toml` (after the per-key
    overlay merge) fails config loading even for headless runs, and
    `--agent-runner headless` / `--interactive-terminal-backend herdr` cannot
    bypass it. `--interactive-terminal-backend tmux` is rejected. With
    `agent_runner = "interactive_terminal"` and no backend set, the default
    becomes Herdr, so `kaji run` must run inside a Herdr pane.
  - **How to check whether you are affected**: look for
    `WARNING: the tmux interactive terminal backend is deprecated` in the
    stderr of `kaji run`, or run
    `grep -nE 'interactive_terminal_backend *= *"tmux"|agent_runner *= *"interactive_terminal"' .kaji/config.toml .kaji/config.local.toml`
    and check scripts for `--interactive-terminal-backend tmux` and
    `--agent-runner interactive-terminal`. These searches only identify
    candidates: quoting, spacing, or dynamically built arguments may hide
    matches. Use the warning from a representative `kaji run` that reaches
    config loading and CLI override application to confirm its effective settings.
  - **How to migrate**: either switch to Herdr (install Herdr, set
    `interactive_terminal_backend = "herdr"`, and run `kaji run` inside a
    Herdr pane) or use the headless runner (`agent_runner = "headless"`). In
    both cases remove `interactive_terminal_backend = "tmux"` from both
    `.kaji/config.toml` and `.kaji/config.local.toml`. See
    `docs/cli-guides/interactive-terminal-runner.md#tmux-backend-deprecation`.
```

Release notes（GitHub Release ページ）には、上記を要約した次の段落を `Deprecated` 見出しの下に置く:

> The tmux backend of the interactive terminal runner is deprecated and will be removed in a subsequent release; Herdr becomes the
> only interactive backend and headless stays. `kaji run` warns on stderr when tmux is used or explicitly configured. To migrate,
> switch to Herdr (`interactive_terminal_backend = "herdr"`, run inside Herdr) or headless (`agent_runner = "headless"`), and remove
> `interactive_terminal_backend = "tmux"` from `.kaji/config.toml` and `.kaji/config.local.toml`. See the Interactive Terminal Runner guide.

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 親 Issue #466「決定事項」 | https://github.com/apokamo/kaji/issues/466 | 「予告リリースを挟む。次のマイナーで、tmux backend が使われたとき（明示指定でも既定値でも）に stderr へ非推奨の警告を出し、CHANGELOG の Deprecated と Release notes で告知する」（grill-me Q3 への回答「A」） |
| 親 Issue #466「初回削除リリースの設定契約」 | https://github.com/apokamo/kaji/issues/466 | 「backend 明示 `tmux` → 廃止理由と移行方法を示してエラー」「config と `config.local.toml` の per-key precedence を維持し、エラーは実際に値を定義したファイルを示す」「旧 config の `tmux` 値は headless の実行時でも黙って無視しない」「`--agent-runner headless` や `--interactive-terminal-backend herdr` だけでは救済できない」 |
| #466 grill-me provenance コメント | https://github.com/apokamo/kaji/issues/466#issuecomment-6228216748 | 上記人間決定の経緯 |
| Issue #475 本文（完了条件・重要判断表） | https://github.com/apokamo/kaji/issues/475 | 発生条件 3 件・非発生条件 2 件・警告文の必須要素 4 件。「`kaji run` 以外のコマンドでも出すかは設計で確定する」 |
| `ExecutionConfig` と既定値 | `kaji_harness/config.py:48-61` | `interactive_terminal_backend: Literal["tmux", "herdr"] = "tmux"` |
| `[execution]` の per-key マージと `source()` | `kaji_harness/config.py:345-379` | overlay の `[execution]` を tracked に `merged.update` し、`source(key)` は overlay にキーがあれば `config.local.toml`、無ければ tracked を返す |
| backend の検証 | `kaji_harness/config.py:408-420` | マージ後の値を `{"tmux", "herdr"}` で検証し、エラーは `source("interactive_terminal_backend")` を示す |
| overlay による上書きの既存テスト | `tests/test_config.py:1448-1456` | tracked `tmux` + local `herdr` → 実効値 `herdr` |
| CLI override の適用 | `kaji_harness/commands/run.py:39-86`、`:137-148` | config 読み込み後に `_apply_execution_overrides` で `--interactive-terminal-backend` を上書き（precedence 1） |
| CLI option 定義 | `kaji_harness/commands/parser.py:152-157` | `--interactive-terminal-backend`、`choices=["tmux", "herdr"]`、`default=None` |
| interactive dispatch | `kaji_harness/runner.py:569-582` | `agent_runner == "interactive_terminal"` のときのみ backend を `execute_interactive_terminal` に渡す |
| `discover()` に副作用を入れない方針 | `kaji_harness/artifacts.py:1-11` | 「`KajiConfig.discover()` には副作用を入れず、`cli_main._cmd_run` のみが…」 |
| 既存 stderr 警告の emitter | `kaji_harness/commands/config.py:21-30` | `_emit_provider_overlay_divergence_warning`: 「exit code・標準出力には影響しない」stderr WARN |
| #438 local provider 警告（歴史的前例） | `git show 0ab4f19:kaji_harness/config.py`（`:20-39`）、`git show 0ab4f19:tests/test_local_provider_deprecation.py` | module-level flag で 1 プロセス 1 回、`print(..., file=sys.stderr)`、「stdout は skill が解析するため使わない」。Large は `sys.executable -m kaji_harness.cli_main` を subprocess 実行し stderr の出現回数を検証。#448（`46475e8`）で削除済み |
| CHANGELOG 0.21.0 Deprecated / Unreleased Deprecated（#473） | `CHANGELOG.md`（`## [0.21.0]` の `### Deprecated`、`## [Unreleased]` の `### Deprecated`） | 「prints a deprecation warning to stderr (once per process) … stdout and exit codes are unchanged」。#473 は Broken contract / How to check / How to migrate の 3 要素 |
| release skill の CHANGELOG 運用 | `.claude/skills/release/SKILL.md` Step 3 | `## [Unreleased]` 配下の既存記述を新 version セクションへ移動する |
| Python warnings の既定 filter | https://docs.python.org/3/library/warnings.html#default-warning-filter | 既定では `DeprecationWarning` は `__main__` で発生したもの以外無視される（`warnings.warn` を不採用とした根拠） |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L 定義、workflow 内の `tmp_path` と `outside_project_tmp_path`、`subprocess.run` patch スコープ |
