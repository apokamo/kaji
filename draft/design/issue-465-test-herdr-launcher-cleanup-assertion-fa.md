# [設計] Herdr launcher cleanup テストが長い一時パスで失敗する問題の修正

Issue: #465

## 概要

`tests/test_interactive_terminal_herdr.py::TestHerdrCommandContract::test_launcher_cleanup_failure_does_not_replace_creation_error`
が、例外の表示用メッセージ（200 文字に切り詰め済み）を `pytest.raises(..., match="publish failed")` で照合しているため、
一時パスが長い環境では元の原因文言が切り詰めで消え、失敗する。検査を切り詰めの影響を受けない `CLIExecutionError.stderr`
属性と例外連鎖へ移し、テストの意図（cleanup エラーが creation エラーを置換しない）を一時パス長に依存せず検証する。

## 背景・目的

### Observed Behavior (OB)

workflow が注入する attempt 固有の長い TMPDIR（例: `/home/aki/dev/kaji/main/tmp/kaji/457/261003160329/pr-verify/attempt-001`）
の下で `make check` を実行すると、対象テスト 1 件だけが次のように失敗する（Issue 本文および PR #460 修正確認コメントの実ログ）。

```text
E   AssertionError: Regex pattern did not match.
E     Expected regex: 'publish failed'
E     Actual message: "Step 'interactive_terminal' CLI exited with code 1: Herdr launcher creation failed for /home/aki/dev/kaji/main/tmp/kaji/457/261003160329/pr-verify/attempt-001/pytest-of-aki/pytest-0/popen-gw2/test_launcher_cleanup_failure_0/herdr-launcher.sh: publish f"
1 failed, 3551 passed, 10 skipped
```

短い一時パス（`TMPDIR=/tmp`）では同ファイル 94 件がすべて通過する。結果として、本テストとは無関係な PR の
review / verify step が `make check` 失敗で阻害される（PR #463 / PR #460 で発生）。

### Expected Behavior (EB)

- 一時パスの長さに関係なく、launcher の publish（`os.replace`）失敗後に cleanup（`Path.unlink`）も失敗した場合、
  送出される `CLIExecutionError` が元の creation エラー原因（`publish failed`）を保持していることをテストで検証でき、通過する。
- 根拠（1 次情報）: 同クラスの隣接テスト `test_launcher_creation_wraps_filesystem_failure`
  （`tests/test_interactive_terminal_herdr.py:238`、検査は 249-250 行）は既に `exc_info.value.stderr` で原因とパスを検査しており、
  表示メッセージの切り詰めに依存していない。
- 公開エラー表示（`CLIExecutionError.__str__` の `stderr[:200]` 切り詰め）は互換性維持のため変更しない（Issue 本文 EB の明示決定）。

### 目的

attempt 固有の長い TMPDIR 下でも `make check` が本件起因で失敗しない状態にし、回帰検証の意図を表示用切り詰め長に依存しない形で維持する。

## 再現手順

1. 前提: main `c7d0378` 以降（現 worktree の基点 `9dbe1b2` を含む）で `.venv` 作成済み。
2. 操作:
   ```bash
   source .venv/bin/activate
   pytest -q -o addopts='' \
     --basetemp=<200 文字前後に達する長いディレクトリ> \
     tests/test_interactive_terminal_herdr.py::TestHerdrCommandContract::test_launcher_cleanup_failure_does_not_replace_creation_error
   ```
3. 観測: `AssertionError: Regex pattern did not match. Expected regex: 'publish failed'`。Actual message がパス途中で途切れる。
4. 比較: `TMPDIR=/tmp TMP=/tmp TEMP=/tmp pytest tests/test_interactive_terminal_herdr.py -q` は 94 passed。

失敗条件は「`Step 'interactive_terminal' CLI exited with code 1: ` 接頭辞 + `Herdr launcher creation failed for ` +
launcher の絶対パス + `: publish failed`」のうち、`stderr` 部分（`Herdr launcher creation failed for <path>: publish failed`）が
200 文字を超え、`publish failed` の一部が `stderr[:200]` の外へ押し出されることである。`stderr` 自体の固定部は
`"Herdr launcher creation failed for "`（35 文字）+ `": publish failed"`（16 文字）= 51 文字なので、launcher 絶対パスが
およそ 150 文字以上になると再現する。

## 根本原因（Root Cause）

- **なぜ間違っているか**: プロダクションコードは正しい。`_materialize_herdr_launcher`
  （`kaji_harness/interactive_terminal_herdr.py:918`）は `except OSError as error:` で cleanup の `OSError` を握り（957-960 行）、
  元の `error` を `stderr` 文字列と `raise ... from error` の両方に保持している（961-966 行）。一方 `CLIExecutionError.__init__`
  （`kaji_harness/errors.py:141`）は `self.stderr` に全文を保持しつつ、`str(exc)` に使う基底メッセージだけ `stderr[:200]` に切り詰める。
  テストは `pytest.raises(match=...)` を使っており、`match` は `str(exc)`（= 切り詰め後）に対する `re.search` である。
  照合対象の `publish failed` が **パスの後ろ** にあるため、パス長が表示長を超えると照合対象が消える。
  つまり「検査している属性（表示用・切り詰めあり）」と「保証したい性質（原因の保持）」が一致していないテスト側の欠陥。
- **いつから**: テストは `fc800ab`（fix: dispatch Herdr wrapper through short launcher）で導入。導入時から潜在しており、
  workflow の attempt 固有 TMPDIR（`tmp/kaji/<issue>/<run>/<step>/attempt-NNN`）+ pytest-xdist の
  `pytest-of-<user>/pytest-N/popen-gwN/<test名先頭30字>N/` が重なってパスが 150 文字を超えるようになった時点で顕在化した。
- **同根の他箇所調査**:
  - `tests/test_interactive_terminal_herdr.py` 内の `pytest.raises(CLIExecutionError, match=...)` 全 22 箇所を確認。
    `stderr` に一時パスを含むメッセージは `Herdr launcher creation failed for {launcher_path}: {error}`（965 行）と
    `Herdr launcher start confirmation timed out for pane {pane_id} after ...`（1022 行）だが、他テストの `match` はいずれも
    パスより **前** にある固定文言（`Herdr launcher creation failed` / `launcher creation failed` / `start confirmation timed out`）を
    照合しており、切り詰めの影響を受けない。パスより後ろの文言を `match` するのは対象テストのみ。
  - リポジトリ全体: 現実の workflow TMPDIR より意図的に長い `--basetemp`
    （`$TMPDIR/long-basetemp-probe-for-issue-465-same-root-cause-survey-extra-padding-directory`、TMPDIR は
    `/home/aki/dev/kaji/main/tmp/kaji/465/261004211446/design/attempt-001`）で全テストを実行した結果、
    `2 failed, 3592 passed, 10 skipped`。失敗は対象テストと、次の 1 件のみ。
  - **同類型の潜在箇所（本 Issue のスコープ外）**: `TestHerdrCommandContract::test_launcher_moves_long_payload_out_of_pane_command`
    （`tests/test_interactive_terminal_herdr.py:204`）の `assert len(pane_command) < 200`（224 行）。`pane_command` は
    `shlex.quote(str(launcher_path))` であり `tmp_path` の長さに比例するため、パス長依存という点で同類型だが、
    原因は表示切り詰めではなく「テスト側の固定上限 200」であり別の assert である。現実の workflow TMPDIR
    （`/home/aki/dev/kaji/main/tmp/kaji/<issue>/<12 桁 run>/<step>/attempt-001` + xdist の
    `pytest-of-aki/pytest-0/popen-gwNN/<30 字>0/`）で試算すると `pane_command` は 173〜187 文字（step 名 `design` 〜
    `incident-investigate`）で、上限 200 に対し 13〜27 文字の余裕があり通過する（PR #460 の実ログでも失敗は対象テストのみ）。
    Issue #465 は対象を本テスト 1 件と明示しているため、本設計では修正範囲に含めず、設計完了コメントで報告して
    取り込み / 別 Issue 化の判断を人間に委ねる（スコープ変更は設計 agent が決めない）。

## インターフェース

テストのみの修正であり、プロダクションコードの IF は変更しない。

### 入力

- 変更対象: `tests/test_interactive_terminal_herdr.py` の `test_launcher_cleanup_failure_does_not_replace_creation_error` 1 関数

### 出力

- 変更前: `pytest.raises(CLIExecutionError, match="publish failed")`（表示用メッセージ照合）
- 変更後: 表示長に依存しない属性で検査する（下記「方針」）

### 使用例

```python
with (
    patch("kaji_harness.interactive_terminal_herdr.os.replace", side_effect=OSError("publish failed")),
    patch.object(Path, "unlink", side_effect=OSError("cleanup failed")) as unlink,
    pytest.raises(CLIExecutionError, match="Herdr launcher creation failed") as exc_info,
):
    _materialize_herdr_launcher(launcher_path, "/wrapper codex")

unlink.assert_called_once()                       # cleanup 失敗経路を実際に通ったこと
assert "publish failed" in exc_info.value.stderr  # 元の creation 原因を保持
assert "cleanup failed" not in exc_info.value.stderr  # cleanup エラーで置換されていない
assert isinstance(exc_info.value.__cause__, OSError)
assert str(exc_info.value.__cause__) == "publish failed"  # 例外連鎖も元の原因
```

## 変更スコープ

- `tests/test_interactive_terminal_herdr.py` のみ（1 テスト関数）
- `kaji_harness/` 配下は変更しない

## 制約・前提条件

- 公開エラー表示（`CLIExecutionError` の `str()` の 200 文字切り詰め）は変更しない（Issue 本文 EB）
- `_materialize_herdr_launcher` の挙動は変更しない（現行実装は EB を満たしている）
- テストは既存どおり `@pytest.mark.small`（`TestHerdrCommandContract` クラス単位）。外部依存なし・mock 完結
- 隣接テスト `test_launcher_creation_wraps_filesystem_failure` の検査方式（`exc_info.value.stderr`）に揃える

## 方針

1. `pytest.raises` の `match` を、パスより前に位置する固定文言 `"Herdr launcher creation failed"` に変更する
   （型と経路の確認のみ。隣接テストと同じ）。`as exc_info` で例外を捕捉する。
2. `patch.object(Path, "unlink", ...)` に `as unlink` を付け、`unlink.assert_called_once()` で cleanup 失敗経路を実際に通過したことを確認する。
   これにより「cleanup が呼ばれず元エラーがそのまま出た」ケースで偽陽性的に通過することを防ぎ、テスト名の意図
   （cleanup 失敗が creation エラーを置換しない）を明示的に検証する。
3. 原因保持を、切り詰めのない 2 系統で検査する:
   - `exc_info.value.stderr` に `"publish failed"` を含み、`"cleanup failed"` を含まない
   - `exc_info.value.__cause__` が `OSError("publish failed")`（`raise ... from error` の連鎖が cleanup エラーに置換されていない）
4. プロダクションコード・`CLIExecutionError` には触れない。リファクタ混在はしない。

`Path.unlink` は `patch.object(Path, ...)` でクラス属性を差し替えるため、pytest の tmp_path 後処理より前に with ブロックを抜ける
現行構造（patch は with 内に限定）を維持する。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 修正対象をテストに限定し、公開エラー表示を維持する | `CLIExecutionError` の `stderr[:200]` 切り詰めは変更しない | 人間決定: Issue #465 本文 EB「公開エラー表示（`CLIExecutionError` の `str()` の 200 文字切り詰め）は互換性維持のため変更しない」、および本文末尾「公開エラー表示の互換性を維持してテストを修正する方法を優先して検討する」 | 変更ファイルを `tests/test_interactive_terminal_herdr.py` の 1 関数に限定 |
| 検査方式 | `exc_info.value.stderr` 属性で原因を検査 | 人間決定: Issue #465 完了条件 2「回帰検証が例外メッセージの表示用切り詰め長に依存しない（例: stderr属性を検査）」と EB の隣接テスト根拠 | `match` はパスより前の固定文言へ変更。`stderr` 検査に加え `"cleanup failed"` 非含有を確認 |
| 例外連鎖（`__cause__`）と `unlink` 呼び出しも検査する | 追加 assert として含める | AI の仮定: テスト名の意図（cleanup が creation エラーを置換しない）を `stderr` 文字列だけより直接的に表す。現行実装（`raise ... from error`、`temporary_path.unlink(missing_ok=True)`）で成立することをコードで確認済み。誤りでも test-only で安く直せる two-way door。検査先: review-design / review-code | `unlink.assert_called_once()`、`__cause__` の型と文言 |
| Red 証跡の扱い | 実ログを実装前 Red 代替とする | 既存契約: `.claude/skills/_shared/design-by-type/bug.md` § テスト戦略 escape clause。Issue 本文・PR #460 修正確認コメントの実ログは OB を直接示し、修正後テストはその OB に対応する EB を検証する（issue-review-ready でも admissible と確認済み） | 実装時に長い basetemp で修正前 FAIL / 修正後 PASS を併せて記録する |

| 同類型の潜在箇所（`test_launcher_moves_long_payload_out_of_pane_command` の `len < 200`）を修正範囲に含めない | 本 Issue では修正せず、設計完了コメントで報告する | 人間決定: Issue #465 の対象は本テスト 1 件（概要・OB・完了条件 1-2）。完了条件 3（workflow TMPDIR 下で `make check` 通過）は現実の TMPDIR 試算（173〜187 文字 < 200）と PR #460 実ログで当該テストが通過していることから本修正のみで充足する。取り込みはスコープ変更にあたるため AI は選ばない | 長い basetemp による全テスト調査結果と試算値を「根本原因」に記録 |

one-way door の未決: 該当なし（公開表示を変えない方針は人間決定済み。変更はテスト 1 関数で可逆。同類型箇所の扱いは
スコープを広げない側を選び、人間へ報告するのみ）。

## テスト戦略

### 変更タイプ

- テストコードのみの変更（`kaji_harness/` の実行時振る舞いは不変）。`testing-convention.md` の 4 分類
  （実行時コード変更 / docs-only / metadata-only / packaging-only）のいずれにも直接は該当しないが、変更対象が
  恒久回帰テストそのものであるため、「実行時コード変更」節に準じて Small / Medium / Large の観点を定義する。

### 実行時コード変更の場合

#### Small テスト

- 修正後の `test_launcher_cleanup_failure_does_not_replace_creation_error`（既存 `@pytest.mark.small` クラス内）が恒久回帰テスト。検証観点:
  - publish 失敗 + cleanup 失敗の組み合わせで `CLIExecutionError` が送出される
  - cleanup（`Path.unlink`）が実際に 1 回呼ばれ、失敗した経路を通る
  - `stderr` 属性に元の原因 `publish failed` が残り、`cleanup failed` に置換されていない
  - `__cause__` が元の `OSError("publish failed")`
  - 上記が一時パス長に依存しない
- 実装前 Red: Issue 本文と PR #460 修正確認コメントの実ログ（`Expected regex: 'publish failed'` 不一致）を代替とする
  （bug.md escape clause）。加えて実装時に、修正前テストを長い `--basetemp` で実行して FAIL を再確認する。
- 修正後 Green の確認（変更固有検証）:
  1. 長い `--basetemp`（launcher パスが 200 文字を超える長さ）で対象テストを単体実行し PASS
  2. 短い一時パス（`TMPDIR=/tmp`）で `tests/test_interactive_terminal_herdr.py` 全体が PASS
  3. 回帰検出力の確認（一時的な mutation、コミットしない）: `_materialize_herdr_launcher` の cleanup 例外処理を
     「cleanup の `OSError` をそのまま送出する」形に一時変更すると、修正後テストが FAIL することを確認し、元に戻す
- workflow の attempt 固有 TMPDIR 下で `source .venv/bin/activate && make check` が通過する（完了条件 3）。

#### Medium テスト

- 追加不要。`testing-convention.md` の 4 条件に沿った根拠:
  1. 独自ロジックの追加・変更なし（`kaji_harness/` 不変、assert の対象属性を変えるのみ）
  2. 想定不具合（cleanup エラーによる原因置換）は修正後の Small テストで捕捉する。launcher の実ファイル書き込み・
     パーミッション・atomic publish は既存の `test_launcher_moves_long_payload_out_of_pane_command` /
     `test_launcher_is_published_atomically` が `tmp_path` 上で検証済み
  3. `os.replace` と `Path.unlink` の同時失敗は実ファイルシステムで決定的に起こせず、mock 注入以外の再現手段がないため、
     Medium を追加しても回帰検出情報は増えない
  4. 上記理由を本節に記録する

#### Large テスト

- 追加不要。外部 API / 実 Herdr との疎通は本修正に関係せず（プロダクションコード不変）、上記 4 条件の 1・4 を同様に満たす。
  既存の Herdr large テストの挙動にも影響しない。

### 長時間 acceptance の配置

該当なし。検証は対象テスト単体実行（約 0.15 秒）、同ファイル実行（数秒）、`make check`（実測 約 2〜3 分）で、
いずれも implement step の timeout の 25% を下回る。nested workflow や外部 agent を伴う acceptance はない。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 技術選定の変更なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | なし | ワークフロー・開発手順の変更なし（テスト規約にも OB を正とする記述はない） |
| docs/reference/ | なし | API・規約の変更なし（`CLIExecutionError` の表示仕様は不変） |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #465 本文・コメント | https://github.com/apokamo/kaji/issues/465 | OB 実ログ（`Expected regex: 'publish failed'` 不一致、パス途中で切り詰め）、EB（公開表示不変、stderr 属性で検査）、完了条件 3 項目 |
| `CLIExecutionError` | `kaji_harness/errors.py:120-141` | `self.stderr = stderr` で全文保持、`super().__init__(f"... {stderr[:200]}")` で表示のみ切り詰め |
| `_materialize_herdr_launcher` | `kaji_harness/interactive_terminal_herdr.py:918-967` | `except OSError as error:` 内で `temporary_path.unlink(missing_ok=True)` の `OSError` を `pass`、元 `error` を stderr と `from error` に保持 |
| 対象テスト / 隣接テスト | `tests/test_interactive_terminal_herdr.py:238-264` | 隣接テストは `exc_info.value.stderr` で原因検査（249-250 行）、対象テストは `match="publish failed"`（262 行） |
| pytest `pytest.raises` | https://docs.pytest.org/en/stable/reference/reference.html#pytest.raises | `match`: "a regex ... tested against the string representation of the exception ... using `re.search()`"。表示用 `str(exc)` が照合対象であることの根拠 |
| Python `unittest.mock` | https://docs.python.org/3/library/unittest.mock.html#unittest.mock.Mock.assert_called_once | `assert_called_once()`: mock がちょうど 1 回呼ばれたことを assert |
| Python 例外連鎖 | https://docs.python.org/3/reference/simple_stmts.html#the-raise-statement | `raise ... from` で `__cause__` に元例外が設定される |
| bug 設計ガイド escape clause | `.claude/skills/_shared/design-by-type/bug.md` § 8 | OB を直接示す実ログは実装前 Red 証跡の代替にできる。恒久回帰テスト自体は必須 |
| テスト規約 | `docs/dev/testing-convention.md` | サイズ判定（mock 完結 → Small）、Medium / Large 省略理由の要件 |
| 導入コミット | `git show fc800ab` | 対象テストの導入元 |
