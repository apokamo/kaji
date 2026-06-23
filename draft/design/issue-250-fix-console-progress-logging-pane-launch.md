# [設計] console progress logging テストの kaji root logger 汚染による pane launched テスト flaky 化の修正

Issue: #250

## 概要

console progress logging を検証するテスト（`TestExecStartProgress` / `TestConsoleProgress`）の teardown が `logging.getLogger("kaji")` の `propagate` / `level` を復元しないため、同一 pytest worker 内の後続テスト `test_pane_launched_progress_includes_step_agent_timeout` が `caplog` でログを捕捉できず flaky に FAILED する。teardown を `handlers` / `level` / `propagate` の保存復元へ修正し、global state 漏れを止める。

## 背景・目的

### Observed Behavior（OB）

`pytest`（xdist 並列, `-n auto`）実行時に、以下 1 件が稀に FAILED する（Issue #247 Baseline Check コメントの実ログ: https://github.com/apokamo/kaji/issues/247#issuecomment-4770061887）。

```text
FAILED tests/test_interactive_terminal.py::TestRunnerPaneLifecycle::test_pane_launched_progress_includes_step_agent_timeout
AssertionError: assert 0 == 1
```

xdist の worker 間競合ではなく、同一 pytest process 内の logging global state 汚染として deterministic に再現できる。`configure_console_logging()` を呼ぶ progress logging テストを前置すると、対象テストが `assert 0 == 1`（`len([]) == 0`）で FAIL する。

```text
tests/test_script_exec.py .
tests/test_interactive_terminal.py F
E   assert 0 == 1
E    +  where 0 = len([])
```

汚染後の `kaji` logger 状態（Issue 本文の追加観測）:

```text
after_teardown_pattern {'level': 20, 'propagate': False, 'handlers': 0, 'kaji_handlers': 0}
```

### Expected Behavior（EB）

`test_pane_launched_progress_includes_step_agent_timeout` は、同一 pytest worker 内で他の console progress logging テストが先に実行されても PASS する。

期待される契約:

- `execute_interactive_terminal()` は pane 起動成功直後に `kaji.interactive_terminal` logger へ `pane launched: step=... agent=... pane=... timeout=...s verdict=...` を INFO 出力する（`kaji_harness/interactive_terminal.py:281-288`、`_console = logging.getLogger("kaji.interactive_terminal")` は同 `:47`）。
- 対象テストは `caplog.at_level("INFO", logger="kaji.interactive_terminal")` でこの 1 レコードを捕捉できる（`tests/test_interactive_terminal.py:443-479`）。
- console progress logging を検証するテストは、`logging.getLogger("kaji")` の `handlers` / `level` / `propagate` をテスト前状態へ復元し、後続テストへ global state を漏らさない（`tests/test_console_log.py::_clean_root` `:18-33` が既に確立している isolation 契約）。

### なぜ caplog が伝播停止で捕捉に失敗するか

pytest の `caplog` は capture handler を **root logger** に張り、各 logger から伝播してきたレコードを捕捉する。`kaji.interactive_terminal` のレコードは `kaji.interactive_terminal` → `kaji` → root と伝播して初めて caplog に届く。`kaji` logger に `propagate=False` が残ると伝播が `kaji` で止まり、root の capture handler に届かないため `caplog.records` が空になる。これが OB の `len([]) == 0` の機序であり、追加観測 `'propagate': False` と一致する。

## 再現手順（steps-to-reproduce）

1. `/home/aki/dev/kaji/main` で `.venv` を有効化する。
2. 対象テスト単体が PASS することを確認する。

```bash
source .venv/bin/activate
pytest -n0 tests/test_interactive_terminal.py::TestRunnerPaneLifecycle::test_pane_launched_progress_includes_step_agent_timeout -q
# -> 1 passed
```

3. `configure_console_logging()` を呼ぶテストを前置して実行する。

```bash
source .venv/bin/activate
pytest -n0 \
  tests/test_script_exec.py::TestExecStartProgress::test_exec_start_logged_with_full_argv \
  tests/test_interactive_terminal.py::TestRunnerPaneLifecycle::test_pane_launched_progress_includes_step_agent_timeout -q
# -> FAILED ... assert 0 == 1 (len([]) == 0)
```

4. `tests/test_workflow_execution.py::TestConsoleProgress::test_progress_lines_routed_to_stdout` を前置しても同じ失敗を再現する。

## 根本原因（Root Cause）

### なぜ壊れているか

`configure_console_logging()` は本番 console progress 用に `kaji` root logger を以下へ変更する（`kaji_harness/console_log.py:38-40`、本番用途としては妥当）。

```python
root = logging.getLogger(ROOT_LOGGER_NAME)  # "kaji"
root.setLevel(level)        # default INFO (20)
root.propagate = False
```

一方、progress logging テストの teardown は `_kaji` handler の除去のみで、`level` / `propagate` を復元しない。

- `tests/test_script_exec.py::TestExecStartProgress._teardown`（`:303-310`）
- `tests/test_workflow_execution.py::TestConsoleProgress._teardown`（`:462-469`）

```python
def _teardown(self) -> None:
    root = logging.getLogger(ROOT_LOGGER_NAME)
    for h in [h for h in root.handlers if getattr(h, "_kaji", False)]:
        root.removeHandler(h)
    # level / propagate を復元していない -> propagate=False が漏れる
```

そのため同一 process 内の後続テストでは `kaji` logger が `propagate=False` のまま残り、`caplog` 依存の `test_pane_launched_progress_includes_step_agent_timeout` が伝播停止により捕捉に失敗する。

### いつから壊れているか

- `f9033b8 feat: 起動コンソールに harness progress と review-poll heartbeat を表示する`: console progress logging と `TestExecStartProgress` / `TestConsoleProgress`（不完全 teardown）を追加。
- `520267d feat: pane launched INFO progress に step/agent/timeout を追加する for #232`: `caplog` 依存の対象 pane launched 検証テストを追加。両者が同一 worker に並ぶと汚染が顕在化する。

### 同根の他箇所（`configure_console_logging()` 利用全数調査）

`rg -n "configure_console_logging\(" tests kaji_harness` の結果を全件分類した。

| 箇所 | 種別 | isolation | 判定 |
|------|------|-----------|------|
| `kaji_harness/console_log.py:27` | 定義 | — | 対象外 |
| `kaji_harness/cli_main.py:382` | 本番呼び出し | 本番は復元不要（プロセス起動時設定） | 対象外 |
| `tests/test_console_log.py:41,54,70,81,92,106,107` | テスト | `_clean_root` fixture（`:18-33`）で `handlers`/`level`/`propagate` を保存復元 | 汚染なし（正） |
| `tests/test_script_exec.py:301`（`TestExecStartProgress`） | テスト | `_teardown` が level/propagate 未復元 | **汚染源 #1（修正対象）** |
| `tests/test_workflow_execution.py:460`（`TestConsoleProgress`） | テスト | `_teardown` が level/propagate 未復元 | **汚染源 #2（修正対象）** |

結論: 汚染源は #1 / #2 の 2 箇所のみ。他に追加の汚染源は存在しない。`test_console_log.py` は `_clean_root` で既にガード済みで汚染しない。

## インターフェース

bug 修正であり、本番 IF（`configure_console_logging()` のシグネチャ・挙動、`pane launched` ログ書式）は一切変更しない。変更対象はテストの isolation 機構のみ。後方互換性への影響なし。

### 入力 / 出力

- 入力: なし（テスト内部の fixture 機構の変更）。
- 出力（副作用）: progress logging テストの teardown 後、`logging.getLogger("kaji")` の `handlers` / `level` / `propagate` がテスト前状態へ復元される。

## 制約・前提条件

- `tests/test_console_log.py::_clean_root`（`:18-33`）の保存復元セマンティクスを isolation 契約の基準とする（save handlers/level/propagate → remove all → yield → remove all → restore handlers/level/propagate）。
- 対象テスト側（`test_interactive_terminal.py`）で `propagate=True` を上書きする対処は採用しない。汚染源を残したまま症状を隠すだけで、他の `kaji.*` logging テストへ同根リスクが残るため（Issue 修正方針）。
- bug 修正にリファクタを混在させない。`test_console_log.py::_clean_root`（既に正しく動作し汚染源ではない）の改変は本 Issue のスコープ外とする。
- shared `.venv` を汚染する副作用テストは追加しない（`docs/dev/testing-convention.md` `uv pip install -e .` の扱い）。

## 方針

### 採用案: 共有 isolation fixture を `tests/conftest.py` に集約し、両汚染源で使用する

`_clean_root` と同等セマンティクスの fixture を `tests/conftest.py`（既存の「Shared test helpers across kaji_harness test suite」）へ 1 つ追加し、`TestExecStartProgress` / `TestConsoleProgress` の壊れた `_teardown` / `try-finally` 手動パターンを置き換える。isolation 契約を 3 箇所に重複させず単一の情報源（SSOT）に集約する。

擬似コード（fixture）:

```python
# tests/conftest.py
@pytest.fixture
def clean_kaji_console_root():
    """kaji root logger の handlers/level/propagate を保存し、テスト後に復元する。"""
    root = logging.getLogger("kaji")
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_propagate = root.propagate
    for h in list(root.handlers):
        root.removeHandler(h)
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)
    root.propagate = saved_propagate
```

各テストの変更イメージ（`TestExecStartProgress` / `TestConsoleProgress`）:

```python
def test_exec_start_logged_with_full_argv(self, clean_kaji_console_root, tmp_path, capsys):
    configure_console_logging(logging.INFO)   # 旧 self._configure() 相当
    # ... 検証 ...
    # try/finally と self._teardown() は削除（fixture が復元を保証）
```

これにより `propagate` / `level` / `handlers` がテスト後に必ず復元され、後続の `caplog` 依存テストへの汚染が消える。

なお、保存復元の core ロジック（remove all → restore handlers/level/propagate）は、後述の Small 回帰テストからも in-test で参照できるよう、fixture 内のローカル helper か小さなモジュールレベル関数へ切り出す。fixture はその helper を yield 前後で呼び出す薄いラッパとし、回帰テストは同じ helper を直接呼んで復元後の状態を検証する。これにより「fixture の復元挙動」と「回帰テストが検証する復元挙動」が同一実装を共有し乖離しない。

### 代替案と不採用理由

- **代替 A（クラスごとに個別 fixture を複製）**: 各テストファイルに `_clean_root` 相当を複製。isolation 契約が 3 ファイルに重複（既存 `_clean_root` を含め 3 重）し、将来の契約変更時に乖離リスク。SSOT の観点で採用案に劣る。
- **代替 B（`_configure`/`_teardown` 手動パターンを維持し、`_configure` で state を `self` に保存・`_teardown` で復元）**: 最小侵襲だが壊れた手動 try/finally 構造を温存し、`_clean_root` という既存の正しいパターンと別系統の保存復元コードを残す。可読性・一貫性で採用案に劣る。
- **不採用（対象テスト側で `propagate=True` 上書き）**: 症状隠蔽。汚染源が残るため恒久解にならない（Issue 修正方針で明示的に回避）。

## テスト戦略

> 変更タイプ: **実行時の振る舞いを変えるコード変更**（テストコードの isolation 機構の修正）。本番ロジックは不変だが、テスト global state の復元という振る舞いを変えるため、恒久回帰テストを定義する。

### bug 固有: 実装前 Red 証跡

Issue 本文 OB と Issue #247 Baseline Check コメントに、`assert 0 == 1`（`len([]) == 0`）の実 pytest 失敗ログが存在する（再現コマンドつき）。`docs/dev/testing-convention` 経由の bug ガイド escape clause により、この実ログを **実装前 Red 証跡の代替**として扱う。下記の恒久回帰テスト（修正後 Green）は省略しない。

### Small テスト

- **isolation 契約の恒久回帰テスト**: 共有 fixture の復元セマンティクスを、テスト順序に依存しない単一テスト内で deterministic に検証する。具体的には 1 テスト内で、(1) `kaji` logger の pre-state を保存、(2) `configure_console_logging(INFO)`（= `propagate=False` を誘発）、(3) fixture と同一の復元処理を適用、(4) 復元後に `caplog.at_level("INFO", logger="kaji.interactive_terminal")` で `kaji.interactive_terminal` への INFO レコードが捕捉できることを assert する。これは OB（伝播停止で捕捉失敗）の EB 側（復元後は捕捉成功）を、worker 内テスト順序に依存せず固定する。
  - 補強として、復元後の `logging.getLogger("kaji")` の `propagate` / `level` が pre-state と一致することも assert する。
- 既存 `tests/test_console_log.py` の routing / formatter / 冪等性テストが green を維持する（`_clean_root` 契約への回帰がないこと）。

### Medium テスト

- `TestConsoleProgress`（`@pytest.mark.medium`、runner 結合）が共有 fixture 適用後も green を維持し、teardown 後に `kaji` logger state を漏らさないことを、後段の再現コマンド（下記 Large 相当の順序依存検証）で確認する。新規の Medium 専用テストは追加しない（結合点は既存テストで網羅済み、条件 2/3 充足）。

### Large テスト

- 実 API / E2E 疎通は本変更に無関係のため新規追加なし（`docs/dev/testing-convention.md` 4 条件: 独自ロジック追加なし・既存ゲートで捕捉・回帰情報が増えない・理由明記）。
- ただし **順序依存の統合確認**として、汚染源テスト → 対象テストを `-n0` 連結実行する以下を回帰確認コマンドとして固定する（Issue 完了条件と一致）。これは subprocess を伴わない in-process のテスト連結であり、Large マーカーは付与しない（pytest CLI レベルの検証手段）。

```bash
pytest -n0 \
  tests/test_script_exec.py::TestExecStartProgress::test_exec_start_logged_with_full_argv \
  tests/test_interactive_terminal.py::TestRunnerPaneLifecycle::test_pane_launched_progress_includes_step_agent_timeout -q

pytest -n0 \
  tests/test_workflow_execution.py::TestConsoleProgress::test_progress_lines_routed_to_stdout \
  tests/test_interactive_terminal.py::TestRunnerPaneLifecycle::test_pane_launched_progress_includes_step_agent_timeout -q

pytest -n0 tests/test_console_log.py \
  tests/test_script_exec.py::TestExecStartProgress \
  tests/test_workflow_execution.py::TestConsoleProgress \
  tests/test_interactive_terminal.py::TestRunnerPaneLifecycle::test_pane_launched_progress_includes_step_agent_timeout -q
```

修正前は第 1 / 第 2 コマンドが `assert 0 == 1` で FAIL、修正後は全て PASS することを検証の中核とする。最後に `make check` 全体が green であることを確認する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ不変 |
| docs/dev/ | なし（原則） | テスト isolation の規約化が必要と判断した場合のみ `docs/dev/testing-convention.md` に「`kaji` logger を変更するテストは handlers/level/propagate を復元する」旨を追記検討。本 Issue では実装中に判断 |
| docs/reference/ | なし | 公開 API / 規約変更なし |
| docs/cli-guides/ | なし | CLI 仕様不変 |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 既存 isolation 契約 | `tests/test_console_log.py:18-33`（`_clean_root`） | `saved = list(root.handlers); saved_level = root.level; saved_propagate = root.propagate` を保存し、yield 後に handler 除去 → 復元 → `root.setLevel(saved_level); root.propagate = saved_propagate`。本設計の保存復元セマンティクスの基準 |
| 汚染源の本番設定 | `kaji_harness/console_log.py:38-40` | `root.setLevel(level); root.propagate = False`。本番では妥当だが、テスト teardown が未復元だと後続へ漏れる |
| 不完全 teardown #1 | `tests/test_script_exec.py:303-310` | `_kaji` handler のみ除去し level/propagate を復元しない |
| 不完全 teardown #2 | `tests/test_workflow_execution.py:462-469` | 同上 |
| pane launched ログ実装 | `kaji_harness/interactive_terminal.py:47,281-288` | `_console = logging.getLogger("kaji.interactive_terminal")` へ `pane launched: step=%s agent=%s pane=%s timeout=%ds verdict=%s` を INFO 出力。EB の契約元 |
| 対象 caplog 検証テスト | `tests/test_interactive_terminal.py:443-479` | `caplog.at_level("INFO", logger="kaji.interactive_terminal")` で 1 レコードを捕捉、`assert len(launched) == 1`。伝播停止で捕捉失敗するのが OB |
| Python logging 伝播仕様 | https://docs.python.org/3/library/logging.html#logging.Logger.propagate | "If this attribute evaluates to true, events logged to this logger will be passed to the handlers of higher level (ancestor) loggers ... If false ... handlers of ancestor loggers are not [called]." `kaji.propagate=False` だと root の caplog handler へ届かないことの一次根拠 |
| pytest caplog 仕様 | https://docs.pytest.org/en/stable/how-to/logging.html#caplog-fixture | caplog は伝播してきたレコードを捕捉する。root への伝播が前提であり、中間 logger で propagate を止めると捕捉できない |
| bug 設計ガイド | `.claude/skills/_shared/design-by-type/bug.md:62-74` | bug は再現テスト必須。escape clause により実世界障害ログを実装前 Red 証跡の代替にできる（恒久回帰テスト自体は省略不可） |
| テスト規約 | `docs/dev/testing-convention.md:52-76` | 変更タイプ別の恒久回帰テスト要否・4 条件。本変更はテスト isolation の振る舞い変更のため Small 回帰を定義 |
