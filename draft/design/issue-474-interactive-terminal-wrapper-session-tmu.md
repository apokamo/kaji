# [設計] interactive terminal の wrapper・session・診断処理を tmux 実装から分離する

Issue: #474

## 概要

`kaji_harness/interactive_terminal.py` に同居する「backend dispatch」「tmux 実装」「tmux / Herdr 共用 helper」を
3 つのモジュールへ分離し、Herdr 実装が tmux 実装を含むモジュールに依存しない import 境界を作る。
外部挙動（tmux / Herdr / headless）と公開入口 `execute_interactive_terminal` の契約は変えない。

## 背景・目的

#466（tmux サポート廃止・Herdr 一本化 epic）の子 Issue A。現状は以下の結合があり、子 Issue B で tmux 実装を
削除すると Herdr と共用処理が壊れる。

- `kaji_harness/interactive_terminal_herdr.py:33-41` が共用 helper 7 件
  （`_build_wrapper_command` / `_extract_codex_session_id` / `_resolve_abnormal_exit_session` /
  `_terminal_exit_detail` / `_wrapper_path` / `_wrapper_skill_invocation` / `read_terminal_diagnostic`）を
  tmux 実装と同じ `kaji_harness/interactive_terminal.py` から import している
- `execute_interactive_terminal`（`interactive_terminal.py:350`）が dispatch と tmux 本体を兼ね、#463 以降は
  `deadline_monotonic` を Herdr へ転送する役割（同 `:400-412`）も持つ
- `tests/test_interactive_terminal.py`（104 tests / 23 class）に tmux 固有テストと共用処理の回帰テストが混在する
- recovery / 環境変数 / large_local テストも共用 helper を tmux 実装のモジュールから import している

**ユースケース**:

- 保守者として、子 Issue B で tmux を削除するときに、`kaji_harness/interactive_terminal_tmux.py` と
  `tests/test_interactive_terminal_tmux.py`、および名前で区切られた数個の tmux class を削除するだけで
  Herdr と共用処理を壊さずに済むようにしたい
- 保守者として、分離前後で #403 / #407 / #415 / #421 の回帰テストが assertion を弱めずに通ることを確認したい

**代替案と不採用理由**:

| 代替案 | 不採用理由 |
|--------|-----------|
| (A1) 共用 helper だけを新モジュールへ移し、tmux 実装は `interactive_terminal.py` に残す | B で `interactive_terminal.py` の大半を行単位で削る作業になり、「ファイル単位で削除できる境界」にならない。dispatch と tmux 本体の兼務も残る |
| (A2) `kaji_harness/interactive_terminal/` を package 化する | `kaji_harness.interactive_terminal` が module から package に変わり、private import の package 境界（ADR 009 / `tests/test_private_imports.py`）を跨ぐため helper の public 化や facade が必要になる。A の「内部整理」の範囲を超える |
| (A3) `interactive_terminal.py` から旧 private 名を re-export して test import を無変更にする | dispatch モジュールが tmux 実装を名前で抱え続け、B で re-export 一覧の削除も必要になる。境界の目的に反する |
| (A4) 共通 Protocol / ABC で backend を抽象化 | Issue のスコープ境界・完了条件で禁止（#466「子 Issue A の受け入れ条件」） |

## インターフェース

### 入力

公開入口は不変。

```python
def execute_interactive_terminal(
    *,
    step: Step,
    prompt_path: Path,
    verdict_path: Path,
    workdir: Path,
    timeout: int,
    session_id: str | None = None,
    backend: Literal["tmux", "herdr"] = "tmux",
    close_on_verdict: bool = True,
    execution_policy: str = "auto",
    env: Mapping[str, str] | None = None,
    deadline_monotonic: float | None = None,
) -> CLIResult: ...
```

- import path: `kaji_harness.interactive_terminal`（不変）
- 引数名・順序・keyword-only・既定値（`backend="tmux"` を含む）: 不変
- 新設する tmux 側入口 `execute_interactive_terminal_tmux(...)` は、上記から `backend` を除いた同一 keyword 引数を受ける
  （Herdr 側 `execute_interactive_terminal_herdr` と同形）

### 出力

- 戻り値: `CLIResult(full_output="", session_id=...)`（不変）
- 送出例外: `CLINotFoundError` / `TmuxSessionRequiredError` / `HerdrSessionRequiredError` / `CLIExecutionError` /
  `StepTimeoutError` / `ValueError` / `FileNotFoundError`（型・送出条件・メッセージ文字列とも不変）
- 副作用: `terminal.log` / `pane-metadata.json` / herdr launcher 等の生成物、`kaji.interactive_terminal` logger への
  progress 出力（logger 名も不変）

### 使用例

呼び出し側（runner / experiments スクリプト）は無変更。

```python
from kaji_harness.interactive_terminal import execute_interactive_terminal

result = execute_interactive_terminal(
    step=step,
    prompt_path=prompt_path,
    verdict_path=verdict_path,
    workdir=workdir,
    timeout=timeout,
    backend=config.execution.interactive_terminal_backend,
    deadline_monotonic=deadline_monotonic,
)
```

Herdr 実装の import（分離後）:

```python
# kaji_harness/interactive_terminal_herdr.py
from .interactive_terminal_common import (
    _build_wrapper_command,
    _extract_codex_session_id,
    _resolve_abnormal_exit_session,
    _terminal_exit_detail,
    _wrapper_path,
    _wrapper_skill_invocation,
    read_terminal_diagnostic,
)
```

### エラー

新しい失敗モードは追加しない。backend 値が `"herdr"` 以外のとき tmux 経路へ進む現行挙動（`else` 分岐）もそのまま維持する。

## 制約・前提条件

- **外部挙動不変**: tmux / Herdr / headless の挙動、例外の型・メッセージ、生成 artifact、logger 名を変えない
- **#421 の deadline 契約不変**: 起動前の期限切れで pane を起動しない、待ちが attempt 期限で打ち切られる、timeout 時の
  session 解決、の各経路を tmux / Herdr 両方で維持する。`deadline_monotonic` は dispatch から両 backend 入口へ
  これまでどおり keyword でそのまま渡す
- **抽象を作らない**: `typing.Protocol` / ABC / 汎用 terminal abstraction を新設しない
- **private import 規約**: 新設モジュールはすべて `kaji_harness` 直下（同一 package）に置く。同一 package 内の
  private import は ADR 009 で許容（`docs/reference/python/python-style.md` § モジュール境界と private import）。
  `_` 始まりの helper を public 化しない
- **layer 規約**: 新設 2 モジュールを `tests/test_layer_imports.py` の `MODULE_LAYERS` に `application` として登録する
  （分類の完全性 fitness test が要求する）
- **テスト件数・assertion 不変**: 既存テストを削除しない。`pytest --collect-only -q` の件数は着手前（2026-10-17 の
  worktree `chore/474` で 3783）を下回らない。assertion を弱めない
- **スコープ外**: tmux 削除、既定 backend の変更、Herdr missing CLI の「tmux に切り替える」案内、利用者向け docs、
  `TmuxSessionRequiredError` / config / CLI parser / recovery classifier の tmux 関連記述、不具合修正

## 変更スコープ

| ファイル | 変更 |
|----------|------|
| `kaji_harness/interactive_terminal_common.py` | **新設**。共用 helper と診断型を移す |
| `kaji_harness/interactive_terminal_tmux.py` | **新設**。tmux 実装を移す（`execute_interactive_terminal_tmux` を入口とする） |
| `kaji_harness/interactive_terminal.py` | dispatch のみに縮小（公開入口の signature・docstring は維持） |
| `kaji_harness/interactive_terminal_herdr.py` | 共用 helper の import 元を `interactive_terminal_common` に付け替えるのみ |
| `kaji_harness/runner.py` | 変更なし（`from .interactive_terminal import execute_interactive_terminal` のまま） |
| `experiments/herdr-interactive-terminal/scripts/*.py`（3 件） | 変更なし |
| `tests/test_interactive_terminal.py` → `tests/test_interactive_terminal_tmux.py` | `git mv`。共用 class を抜き、import / patch 対象を付け替える |
| `tests/test_interactive_terminal_common.py` | **新設**。共用 class の移設先 + 直接 helper テスト + import 境界テスト |
| `tests/test_attempt_deadline_e2e_large_local.py` / `tests/test_tmp_dir_e2e_large_local.py` | 実 tmux fixture と tmux E2E を tmux class に囲う。import 付け替え |
| `tests/test_runner_interactive_dispatch.py` | tmux 固有の 1 test を tmux class に移し、patch 対象を付け替える |
| `tests/test_recovery_classify.py` / `tests/test_recovery_handler.py` / `tests/test_tmp_env_propagation.py` | import 元を `interactive_terminal_common` に付け替えるのみ |
| `tests/test_layer_imports.py` | `MODULE_LAYERS` に新設 2 モジュールを追加 |
| `docs/ARCHITECTURE.md` | § Runner backend dispatch にモジュール構成を追記 |

## 方針

### 1. モジュール分割

依存方向（分離後）:

```text
runner.py ──▶ interactive_terminal.py (dispatch)
                 ├──▶ interactive_terminal_tmux.py ──┐
                 └──▶ interactive_terminal_herdr.py ─┴──▶ interactive_terminal_common.py
```

`interactive_terminal_common.py` は他の 3 モジュールを import しない。`interactive_terminal_herdr.py` は
`interactive_terminal.py` と `interactive_terminal_tmux.py` を import しない。

シンボルの配置（現 `interactive_terminal.py` からの移動先。名前・本体・docstring は変えない）:

| 移動先 | シンボル |
|--------|----------|
| `interactive_terminal_common.py` | `TerminalDiagnostic`, `_strip_ansi`, `extract_terminal_diagnostic`, `read_terminal_diagnostic`, `_terminal_exit_detail`, `_wrapper_path`, `_wrapper_skill_invocation`, `_build_wrapper_command`, `_resolve_abnormal_exit_session`, `_unique_codex_session_id_from_store`, `_extract_codex_session_id`, `_extract_codex_session_id_from_store`, `_codex_home` と、これらだけが使う定数（`_CODEX_RESUME_RE`, `_CODEX_SESSION_FILE_RE`, `_CODEX_SESSION_SCAN_LIMIT`, `_TERMINAL_LOG_TAIL_CHARS`, `_DIAGNOSTIC_EXCERPT_RADIUS`, ANSI / 制御文字 regex 4 件） |
| `interactive_terminal_tmux.py` | `execute_interactive_terminal_tmux`（現 `execute_interactive_terminal` の tmux 分岐本体）, `KajiAgentPane`, `_PaneLaunch`, `_build_tmux_split_argv`, `_resolve_tmux`, `_resolve_target_pane`, `_validate_tmux_version`, `_launch_pane`, `_parse_kaji_pane_marker`, `_list_kaji_agent_panes`, `_prune_kaji_agent_panes`, `_set_kaji_agent_pane_marker`, `_pipe_pane`, `_set_remain_on_exit`, `_pane_dead`, `_kill_pane`, `_write_pane_metadata`, `_tmux_version_text`, `_wait_for_pane_exit_or_session_id` と tmux 専用定数（`_MIN_TMUX_VERSION`, `_KAJI_PANE_OPTION`, `_MAX_VISIBLE_AGENT_PANES`, `_SESSION_ID_GRACE_SECONDS`, `_VERDICT_POLL_INTERVAL_SECONDS`）、`_console = logging.getLogger("kaji.interactive_terminal")` |
| `interactive_terminal.py` | `execute_interactive_terminal`（dispatch のみ） |

補足:

- `_wait_for_pane_exit_or_session_id` は `_pane_dead`（tmux）を呼ぶため tmux 側に置く
- `_terminal_exit_detail` の既定 `prefix="tmux pane exited before writing verdict.yaml"` は挙動不変のため A では変えない。
  既存テスト（`TestSensitiveSafeFocusedMessage` / `TestFalsePositiveBoundary` / recovery テスト）が既定文言を assert しており、
  文言の扱いは B で決める（境界表に記載）
- tmux 実装の docstring（現モジュール docstring の tmux 説明部分）は `interactive_terminal_tmux.py` へ移し、
  `interactive_terminal.py` の module docstring は dispatch の説明に縮める
- 移動時に関数本体のロジック・呼び出し順・`time.monotonic()` / `time.sleep()` の呼び出し回数を変えない
  （既存テストが `side_effect=[...]` で呼び出し回数を固定しているため）

### 2. dispatch

```python
# kaji_harness/interactive_terminal.py（疑似コード）
from . import interactive_terminal_herdr, interactive_terminal_tmux

def execute_interactive_terminal(*, step, ..., backend="tmux", ..., deadline_monotonic=None) -> CLIResult:
    """（現 docstring を維持）"""
    if backend == "herdr":
        return interactive_terminal_herdr.execute_interactive_terminal_herdr(
            step=step, ..., deadline_monotonic=deadline_monotonic,
        )
    return interactive_terminal_tmux.execute_interactive_terminal_tmux(
        step=step, ..., deadline_monotonic=deadline_monotonic,
    )
```

- 現在の関数内 deferred import（`from .interactive_terminal_herdr import ...`）は、herdr→dispatch の循環を避けるためのもので、
  分離後は循環が消える。python-style の「deferred import は依存の向きが誤っている兆候」に従い top-level import にする
- backend 入口は **モジュール属性経由**で呼ぶ。`tests/test_interactive_terminal_herdr.py::TestTmuxEntryForwardsDeadlineToHerdr` は
  `kaji_harness.interactive_terminal_herdr.execute_interactive_terminal_herdr` を patch しており、名前を dispatch 側へ束縛すると
  この patch が効かなくなるため
- 入力検証（agent / capability / prompt 存在）は現行どおり backend 側に置く（tmux 側は `execute_interactive_terminal_tmux` 冒頭、
  Herdr 側は既存 `_validate_step`）。検証順序を変えない

### 3. テストの再配置

#### 3.1 `tests/test_interactive_terminal.py` の class 振り分け

`git mv tests/test_interactive_terminal.py tests/test_interactive_terminal_tmux.py` で履歴を保ち、共用 class を新ファイルへ切り出す。

| class（現行） | tests | 移動先 | 理由 |
|---------------|------:|--------|------|
| `TestBuildTmuxSplitArgv` | 4 | tmux | tmux argv |
| `TestRunnerEntryValidation` | 7 | tmux | tmux preflight（which / `$TMUX` / version）経由 |
| `TestPaneDeadMapping` | 3 | tmux | `_pane_dead` |
| `TestSessionIdLaunch` | 4 | tmux | tmux runner 経由 |
| `TestRunnerPaneLifecycle` | 8 | tmux | tmux runner 経由 |
| `TestCodexSessionIdExtraction` | 3 | tmux | fake tmux runner 経由の結合テスト |
| `TestAbnormalExitSessionResolution` | 14 | tmux | fake tmux runner 経由の結合テスト（#403） |
| `TestInteractiveTerminalInterrupt` | 2 | tmux | tmux runner 経由（#403） |
| `TestKajiPaneMarker` / `TestListKajiAgentPanes` / `TestPruneKajiAgentPanes` / `TestRightColumnPanePlacement` | 5 / 3 / 2 / 7 | tmux | pane marker / 配置 |
| `TestPaneExitStatusDistinguishable` | 4 | tmux | tmux runner 経由 |
| `TestInteractiveTerminalEndToEnd` | 2 | tmux | 実 tmux E2E |
| `TestTmuxAttemptDeadline` | 3 | tmux | #421 の tmux deadline |
| `TestResolveAbnormalExitSessionPureBranches` | 5 | common | 共用 helper 直接 |
| `TestWrapperSkillInvocationArgument` | 3 | common | `_build_wrapper_command` / `_wrapper_skill_invocation` 直接 |
| `TestInteractiveTerminalWrapper` | 9 | common | `wrapper.sh` 契約（tmux 非依存） |
| `TestExtractTerminalDiagnostic` / `TestSensitiveSafeFocusedMessage` / `TestSensitiveMarkerCoexistsWithTransient` / `TestFalsePositiveBoundary` / `TestReadTerminalDiagnostic` | 5 / 3 / 3 / 2 / 3 | common | 診断（provider error / sensitive marker） |

合計: tmux 71 / common 33（現行 104 と一致）。両ファイルで使う module-level helper（`_step`、`WRAPPER`、診断用 transcript
生成関数など）は、使う側のファイルにそれぞれ置く。tmux テストファイルから common テストファイル（またはその逆）を import しない
（B で tmux ファイルを削除したときに common 側が壊れない向きにするため。重複は数行の helper に限られる）。

#### 3.2 patch / import 対象の付け替え

| 現行 | 付け替え後 |
|------|-----------|
| `patch("kaji_harness.interactive_terminal.shutil.which", ...)`（29 箇所） | `kaji_harness.interactive_terminal_tmux.shutil.which` |
| `patch("kaji_harness.interactive_terminal.time.monotonic" / ".time.sleep", ...)` | `kaji_harness.interactive_terminal_tmux.time.*` |
| `uuid.uuid4` の patch | `kaji_harness.interactive_terminal_tmux.uuid.uuid4` |
| `patch("kaji_harness.interactive_terminal._write_pane_metadata", ...)` | `kaji_harness.interactive_terminal_tmux._write_pane_metadata`（名前束縛先が変わるため必須） |
| tmux 固有シンボルの `from kaji_harness.interactive_terminal import ...` | `from kaji_harness.interactive_terminal_tmux import ...` |
| 共用シンボルの `from kaji_harness.interactive_terminal import ...`（recovery 2 件、`test_tmp_env_propagation.py`、`test_tmp_dir_e2e_large_local.py`、common テスト） | `from kaji_harness.interactive_terminal_common import ...` |
| `from kaji_harness.interactive_terminal import execute_interactive_terminal` | 変更なし |

付け替えは patch の表記変更のみで、patch の適用範囲（どの実行経路まで stub するか）は変えない。対象テストの実行経路は worktree 解決に届かないため、`docs/dev/testing-convention.md` § `subprocess.run` / `shutil.which` patch スコープの許可区分も変わらない。

`shutil` / `time` / `uuid` の patch は module object の属性差し替えであり、参照経路のモジュール名が変わっても効果は同じ。
ただし dispatch 縮小後の `interactive_terminal.py` はこれらを import しないため、旧経路のままでは `AttributeError` になる。
付け替えは「tmux 実装が使うものを tmux モジュール経由で patch する」意図を明示するためでもある。

#### 3.3 tmux 固有テストの class 化（他ファイル）

B で削除できる単位にするため、以下を名前で区切られた class に入れる（テスト関数名・本体・assertion は変えない）。

| ファイル | 対象 | 新 class |
|----------|------|----------|
| `tests/test_attempt_deadline_e2e_large_local.py` | fixture `isolated_tmux` + `test_tmux_times_out_at_attempt_deadline_not_pane_launch_plus_timeout` | `TestTmuxAttemptDeadlineE2E`（fixture は class 内 method fixture） |
| `tests/test_tmp_dir_e2e_large_local.py` | fixture `isolated_tmux` + `test_tmux_pane_receives_attempt_tmp_env_over_server_environment` | `TestTmuxTmpEnvE2E`（同上） |
| `tests/test_runner_interactive_dispatch.py` | `TestRunnerBackendDispatch::test_tmux_session_required_type_name_reaches_run_log` | `TestTmuxRunnerDispatch`（同ファイル内） |

node id は変わるが収集件数は変わらない。

#### 3.4 共用処理の直接テスト追加（common）

現行の Codex session store 照合（#403 の一意性規則）と `terminal.log` からの session 抽出は、fake tmux runner 経由の
`TestAbnormalExitSessionResolution` / `TestCodexSessionIdExtraction` でしか検証されていない（Herdr テストは
`CODEX_HOME` を空にする用途のみ）。これらは tmux 側に残るため、B で削除すると共用 helper の回帰テストが失われる。
そこで A で、同じ観点を helper 直接呼び出しで検証するテストを `tests/test_interactive_terminal_common.py` に **追加** する
（既存の tmux 経由テストは削除・弱化しない）。

### 4. import 境界の機械検査

`tests/test_interactive_terminal_common.py` に AST ベースの Small テストを置く（`tests/test_layer_imports.py` と同様に
`ast` で runtime import edge を抽出。関数内 deferred import も edge に含め、`TYPE_CHECKING` guard 内は除外）。

- `kaji_harness/interactive_terminal_herdr.py` は `kaji_harness.interactive_terminal` / `kaji_harness.interactive_terminal_tmux` を import しない
- `kaji_harness/interactive_terminal_common.py` は `kaji_harness.interactive_terminal` / `_tmux` / `_herdr` を import しない
- `kaji_harness/interactive_terminal_common.py` は `subprocess` を import しない（terminal / process 操作を持たないことの検査）
- Herdr が使う共用 helper 7 件がすべて `interactive_terminal_common` に定義されている（`hasattr` で検査）

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| A/B 分割と順序 | 共用処理の分離（A）を先に行い、tmux 削除は B | Issue #474 本文「重要判断」表 1 行目。#466「採用する進め方」2 と「実施順序」（ユーザー承認は #466 本文に記録） | B で削除する単位を「境界表」として固定 |
| A の範囲 | 挙動維持の内部整理。抽象・Protocol を作らない | Issue #474 本文「スコープ境界」「重要判断」2 行目、#466「子 Issue A の受け入れ条件」 | 代替案 A4 を不採用と明記。例外・logger 名・既定 prefix まで不変と具体化 |
| #463 の deadline 経路を含める | dispatch での `deadline_monotonic` 転送と #421 回帰テストの配置を対象に含める | Issue #474 本文「重要判断」3 行目（起票時のユーザー指示） | dispatch が両入口へ keyword でそのまま渡す。tmux 側 deadline テスト（Medium / Large）の移設先と class 化を確定 |
| 公開入口の維持 | `execute_interactive_terminal` の import path・引数・既定値・戻り値・例外を維持 | Issue #474 本文「重要判断」4 行目、#466「public runner 呼び出しと既存 tmux／Herdr の挙動を維持する」 | runner / experiments を無変更とし、dispatch の docstring も維持 |
| モジュール名と配置（3 分割、flat module） | `interactive_terminal_common.py` / `interactive_terminal_tmux.py` を新設、`interactive_terminal.py` は dispatch のみ | **AI の仮定**（two-way door）。根拠: B をファイル単位削除にできる、package 化（A2）は ADR 009 の package 境界を跨ぐ。Issue 本文で「AI が設計で決める」と明記。検査先: `/issue-review-design` | シンボル単位の配置表（§ 方針 1） |
| 旧 private 名の re-export をしない | dispatch モジュールは `execute_interactive_terminal` 以外を提供しない。テスト import を付け替える | **AI の仮定**（two-way door）。根拠: Issue 完了条件が維持を求める公開契約は `execute_interactive_terminal` のみ。リポジトリ内で他シンボルを `kaji_harness.interactive_terminal` から import するのはテストだけ（grep 確認済み）。検査先: `/issue-review-design`、`make check` | 付け替え表（§ 方針 3.2） |
| dispatch の deferred import 解消とモジュール属性呼び出し | top-level `from . import interactive_terminal_herdr, interactive_terminal_tmux` とし属性経由で呼ぶ | **AI の仮定**（two-way door）。根拠: python-style § モジュール境界と private import、既存 patch（`TestTmuxEntryForwardsDeadlineToHerdr`）の維持。検査先: `/issue-review-design`、既存テスト | § 方針 2 |
| テストファイルの分け方 | `git mv` で tmux ファイル化し、共用 class を新ファイルへ。他ファイルの tmux 項目は class 化 | **AI の仮定**（two-way door）。Issue 本文で「AI が設計で決める」と明記。検査先: `/issue-review-design`、収集件数比較 | § 方針 3.1 / 3.3 の振り分け表 |
| 共用 helper の直接テスト追加 | Codex session store / terminal.log 抽出を helper 直接で検証するテストを追加 | **AI の仮定**（two-way door）。根拠: 完了条件「tmux 固有のテストを B で削除できる単位にする」と「共用処理のテストを分けて配置」を同時に満たすには、tmux runner 経由でしか検証されない共用ロジックの回帰観点を tmux 非依存に複製する必要がある。検査先: `/issue-review-design` / `/issue-review-code` | § 方針 3.4、§ テスト戦略 |
| `_terminal_exit_detail` の既定 prefix（"tmux pane exited ..."） | A では変えない | Issue #474 本文「スコープ境界」（挙動を変えない） | B の判断対象として境界表に記載 |
| 設計書ファイル名 | workflow が注入した `design_path`（`draft/design/issue-474-interactive-terminal-wrapper-session-tmu.md`）を使う | **AI の仮定**（two-way door）。Issue 完了条件の例示名は `issue-474-interactive-terminal-common-split.md` だが、後続 `issue-implement` は `design_path` を最優先で解決する（`.claude/skills/issue-implement/SKILL.md` Step 2）。ファイル名は内容契約ではなく rename で安く直せる。検査先: `/issue-review-design`、`/i-dev-final-check` | 完了条件の「設計書作成」は本ファイルで充足とする |
| #466 の重要判断 3 件 | 前提にしない | Issue #474 本文「重要判断」最終行 | 本設計は B の判断（SSH 対応、rendered snapshot、告知）に一切依存しない |

one-way door の未決: **該当なし**。公開契約は Issue で維持が決定済みで、残る判断はすべて内部配置（rename / 移動で安く戻せる）。

## B で削除する対象と維持する対象の境界表

| 区分 | 対象 | B での扱い |
|------|------|-----------|
| 削除 | `kaji_harness/interactive_terminal_tmux.py`（ファイル全体） | 削除 |
| 削除 | `tests/test_interactive_terminal_tmux.py`（ファイル全体。71 tests、実 tmux E2E `TestInteractiveTerminalEndToEnd` を含む） | 削除 |
| 削除 | `tests/test_attempt_deadline_e2e_large_local.py::TestTmuxAttemptDeadlineE2E`（class 全体、`isolated_tmux` fixture を含む） | 削除 |
| 削除 | `tests/test_tmp_dir_e2e_large_local.py::TestTmuxTmpEnvE2E`（class 全体、`isolated_tmux` fixture を含む） | 削除 |
| 削除 | `tests/test_runner_interactive_dispatch.py::TestTmuxRunnerDispatch`（class 全体） | 削除 |
| 編集 | `kaji_harness/interactive_terminal.py` の tmux 分岐・既定値 `backend="tmux"`・`from . import interactive_terminal_tmux` | B が既定 backend と分岐を決めて編集 |
| 編集 | `tests/test_layer_imports.py` の `kaji_harness.interactive_terminal_tmux` 行 | 削除 |
| 編集 | backend を parametrize / 併記する dual テスト（`tests/test_tmp_env_propagation.py::TestRunnerDispatchWiring::test_interactive_agent_receives_four_variables[tmux]`、`tests/test_runner_interactive_dispatch.py` の `backend` 振り分けテスト） | tmux の parametrize 値・分岐を削除 |
| B が判断 | `_terminal_exit_detail` の既定 prefix 文言と、それを assert する common / recovery テスト | 文言変更の要否を B で決める |
| B が判断 | `kaji_harness/errors.py::TmuxSessionRequiredError`、`config.py` / `commands/parser.py` / `recovery/classify.py` / `runner.py` の tmux 関連記述、Herdr missing CLI の tmux 案内、利用者向け docs | B のスコープ（本 Issue 対象外） |
| 維持 | `kaji_harness/interactive_terminal_common.py`、`kaji_harness/interactive_terminal_herdr.py`、`execute_interactive_terminal`（公開入口） | そのまま |
| 維持 | `tests/test_interactive_terminal_common.py`、`tests/test_interactive_terminal_herdr.py`（`TestTmuxEntryForwardsDeadlineToHerdr` は dispatch→Herdr 転送テストで tmux 非依存）、recovery / 環境変数テスト、headless の deadline E2E | そのまま |
| 維持 | `kaji_harness/assets/interactive-terminal/wrapper.sh`（Herdr も使用） | そのまま |

## テスト戦略

### 変更タイプ

- 実行時コード変更（モジュール再配置。外部挙動は不変）

### 実行時コード変更の場合

主な検証は「既存テストを assertion 不変のまま移設して全件通す」こと。新規追加は import 境界と、tmux 経由でしか検証されていない
共用ロジックの直接テストに限る。

#### Small テスト

- **import 境界**（新規、`tests/test_interactive_terminal_common.py`）: § 方針 4 の 4 観点。Herdr が tmux 含有モジュールを import しないこと
  （完了条件 1）と、共用処理が tmux 非依存モジュールにあること（完了条件 2）を AST / `hasattr` で検査する
- **layer 分類の完全性**（既存、`tests/test_layer_imports.py`）: 新設 2 モジュールが `MODULE_LAYERS` に登録され、runtime import
  方向違反がないこと
- **private import 規約**（既存、`tests/test_private_imports.py`）: 新設モジュールが同一 package に置かれ、禁止 signature が増えないこと
- **共用 helper の純粋分岐**（既存移設）: `TestResolveAbnormalExitSessionPureBranches`、`TestWrapperSkillInvocationArgument`、
  診断系 5 class（provider error / sensitive marker / false positive / no_log / empty）
- **dispatch の deadline 転送**（既存）: `TestTmuxEntryForwardsDeadlineToHerdr` が無変更で通ること（モジュール属性経由呼び出しの検証を兼ねる）

#### Medium テスト

- **共用 helper の直接テスト**（新規、`tests/test_interactive_terminal_common.py`、tmp_path に `CODEX_HOME` を作るファイル I/O）:
  - `_resolve_abnormal_exit_session("codex", ...)` / `_unique_codex_session_id_from_store`: marker 一致がちょうど 1 件で UUID、2 件で None、
    0 件で None、`sessions/` 不在で None、`prompt.txt` より古い rollout を除外（#403 の一意性規則）
  - `_extract_codex_session_id`: `terminal.log` に resume 行が複数あれば末尾を採用、resume 行がなければ store の marker 一致 rollout を採用、
    `terminal.log` 不在でも store を参照
- **wrapper 契約**（既存移設）: `TestInteractiveTerminalWrapper`（`wrapper.sh` を fake agent で実行する 9 tests）
- **tmux runner 結合**（既存、tmux ファイル）: fake tmux による lifecycle / #403 session 解決 / 中断 / 配置 / pane exit / #421 deadline
  （`TestTmuxAttemptDeadline`: 起動前期限切れで pane を起動しない、待ちが attempt 期限で打ち切られる、timeout 時の session 解決）
- **Herdr runner 結合**（既存、無変更）: `tests/test_interactive_terminal_herdr.py` の #415 launcher 一度起動・start marker、#421 deadline
  （`TestLauncherStartWaitAttemptDeadline` / `TestExecuteHerdrAttemptDeadline`）
- **runner 配線**（既存）: `tests/test_runner_interactive_dispatch.py`、`tests/test_tmp_env_propagation.py`（#407）、recovery 2 ファイル

#### Large テスト

- 新規追加なし。既存の実 tmux / 実 subprocess の large_local テスト（`TestInteractiveTerminalEndToEnd`、`TestTmuxAttemptDeadlineE2E`、
  `TestTmuxTmpEnvE2E`、`test_herdr_launcher_applies_attempt_tmp_env`、headless deadline E2E）を移設・class 化のうえ実行し、
  分離後も実 tmux 経路が動くことを確認する（ローカルに tmux >= 3.1 がない環境では既存の skip 条件に従う）
- 新規 Large を追加しない理由: 本変更は内部配置のみで外部 I/F・外部サービス疎通が変わらず、実 tmux / 実 Herdr 経路は既存 Large が
  そのまま網羅する（`docs/dev/testing-convention.md` § 省略してよい理由「既存ゲートで不具合パターンを捕捉できる」）。Large そのものは省略せず、既存 Large を移設後に実行する

#### 件数・assertion の検証手順（implement / review-code で実施）

- 着手前と後で `pytest --collect-only -q | tail -1` を比較し、件数が 3783 以上であること（追加分だけ増える）
- 移設テストの assertion 不変: `git diff -M main -- tests/` で移設 class の本体差分が import / patch 対象文字列の付け替えと
  class 化のインデントに限られることを確認する
- `make check`

### 長時間 acceptance の配置

- 該当なし。nested `kaji run`、外部 agent dogfood、fresh 環境の反復 acceptance はない。最長の検証は既存 large_local テスト
  （実 tmux E2E は数秒〜十数秒、deadline E2E は 4 秒打ち切り）と `make check` で、implement step の timeout の 25% を超えない

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし。ADR 007 の `interactive_terminal.py` 記述は決定時点の記録であり書き換えない。ADR 009 の規約内で配置する |
| docs/ARCHITECTURE.md | あり | § Runner backend dispatch に、dispatch（`interactive_terminal.py`）/ tmux（`interactive_terminal_tmux.py`）/ Herdr（`interactive_terminal_herdr.py`）/ 共用（`interactive_terminal_common.py`）のモジュール構成と依存方向を 1 段落追記する |
| docs/dev/ | なし | ワークフロー・開発手順に変更なし |
| docs/reference/ | なし | 設定値・API 仕様に変更なし（`interactive_terminal_backend` の既定・列挙は不変） |
| docs/cli-guides/ | なし | CLI 仕様に変更なし |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| 利用者向け docs（README / interactive guide 等） | なし | Issue のスコープ境界により B の範囲 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #474 本文 | https://github.com/apokamo/kaji/issues/474 | 完了条件（import 境界、公開入口不変、deadline 転送、テスト件数・assertion 不変、B 削除単位と境界表、Protocol 不新設）とスコープ境界の正本 |
| 親 Issue #466 | https://github.com/apokamo/kaji/issues/466 | 「子 Issue A の受け入れ条件」: 共用処理を先に分離し、public runner 呼び出しと既存 tmux／Herdr の挙動を維持する |
| 現行 dispatch + tmux 実装 | `kaji_harness/interactive_terminal.py`（`:350` 公開入口、`:402-416` herdr への `deadline_monotonic` 転送、`:945` `_wait_for_pane_exit_or_session_id` が `_pane_dead` を使用） | シンボル配置表の根拠。どの helper が tmux 呼び出しを含むかを本体で確認 |
| Herdr 実装の共用 import | `kaji_harness/interactive_terminal_herdr.py:33-41` | Herdr が依存する共用 helper 7 件 |
| 公開入口の呼び出し元 | `kaji_harness/runner.py:37`、`experiments/herdr-interactive-terminal/scripts/run_fake_herdr_smoke.py:10` / `run_live_herdr_fake_agent.py:11` / `run_live_herdr_real_agents.py:13` | すべて `from kaji_harness.interactive_terminal import execute_interactive_terminal` のみ → 無変更で済む |
| private import 規約 | `docs/reference/python/python-style.md` § モジュール境界と private import、`docs/adr/009-module-boundary-private-import.md`、`tests/test_private_imports.py` | 同一 package 内の private import は許容、package を跨ぐと禁止。deferred import は依存方向の誤りの兆候 → flat module 採用と dispatch の top-level import の根拠 |
| layer fitness test | `tests/test_layer_imports.py:19-59`（`MODULE_LAYERS`） | 全モジュールの分類登録が必要 → 新設 2 モジュールの登録 |
| テスト規約 | `docs/dev/testing-convention.md`（サイズ定義、テスト要否） | ファイル I/O は Medium、純粋ロジックは Small。新規 Large を追加しない判断の根拠 |
| unittest.mock.patch の target 解決 | https://docs.python.org/3/library/unittest.mock.html#where-to-patch | 「patch はオブジェクトが *参照される* 名前空間で行う」。`_write_pane_metadata` など名前束縛先が変わる対象は付け替え必須。`shutil.which` 等は module object の属性差し替えのため経路名に依らず効果が同じ |
| 既存テスト構成 | `tests/test_interactive_terminal.py`（23 class / 104 tests）、`tests/test_attempt_deadline_e2e_large_local.py:190-250`、`tests/test_tmp_dir_e2e_large_local.py:253-323`、`tests/test_runner_interactive_dispatch.py:135-165`、`tests/test_interactive_terminal_herdr.py:2289-2308` | § 方針 3 の振り分け表・class 化対象の根拠（AST で class ごとに tests 数と tmux 依存を集計） |
| 着手前テスト収集件数 | worktree `chore/474`（main `1c76d20`）で `pytest --collect-only -q` → 3783 | 件数比較のベースライン |
