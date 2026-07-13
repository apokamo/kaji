# [設計] tests の import を commands/ 最終実体モジュールへ移行し cli_main シムを削除（R2）

Issue: #284

## 概要

tests の `kaji_harness.cli_main` 経由の import・module 参照・patch target を
`kaji_harness/commands/` 等の最終実体 module へ機械的に移行し、`cli_main.py` を
re-export shim から `main()` entrypoint 専用 module（`kaji_harness.cli_main:main` /
`python -m kaji_harness.cli_main` のみ担う）へ縮小する。#283（R1）〜#286（R4）シリーズの
最終 sub-issue であり、#285 の private import 検証と #286 の層方向 fitness test で
シリーズ末尾の回帰がないことを確認する。

## 背景・目的

### 現状の問題（観測可能な形）

2026-07-14 時点（worktree base = main `2cb61e4`、#286 merge 済み）の再計測値:

| 観測項目 | 計測値 | 再現コマンド |
|---------|--------|-------------|
| `cli_main.py` の行数（re-export shim） | 196 行 | `wc -l kaji_harness/cli_main.py` |
| tests の `from kaji_harness.cli_main import` 文 | 147 文 / 26 ファイル / 35 シンボル | `grep -rn 'from kaji_harness\.cli_main import' tests/ \| wc -l` + AST 集計（§ベースライン計測） |
| tests の module object import | 1 件（`tests/test_artifacts_dir.py:19` の `from kaji_harness import cli_main`） | `grep -rn 'from kaji_harness import cli_main' tests/` |
| tests の `kaji_harness.cli_main.*` patch target 文字列 | 68 件 / 6 ファイル（`subprocess.run` 36 + `shutil.which` 32） | `bash scripts/inventory_cli_main_patch_targets.sh` |
| 何らかの形で `cli_main` を参照する tests | 39 ファイル | `grep -rl 'kaji_harness\.cli_main\|from kaji_harness import cli_main' tests/ \| wc -l` |

shim が残る限り、新規 code/test が旧 module（`cli_main`）へ依存し続けられる。また
`tests/test_private_imports.py` の時限許容 allowlist（7 entry、#285 設計 §時限許容）は
「#284 の shim 削除で全 entry が stale 化して撤去が強制される」前提で設計されており、
本 Issue がその期限執行を担う。

**#283 patch target 対応表との照合**（完了条件 2）: #283 設計書 §patch 対応表の分類は
「維持 68 件（`subprocess.run` / `shutil.which` の属性 patch）/ 書換え 63 件（名前再束縛
patch）」。今回の再計測で `kaji_harness.cli_main.<symbol>` 形式の残存 patch target は
`subprocess.run` 36 件 + `shutil.which` 32 件 = 68 件のみであり、#283 で「維持」と分類
された属性 patch と完全一致する。名前再束縛 63 件は #283 で書換え済みで残存ゼロ。

### 改善指標（測定可能）

| 指標 | Before | After（目標） | 検証コマンド |
|------|--------|---------------|-------------|
| `cli_main.py` 行数 | 196 | entrypoint のみ（実測見込み ~15 行、目安 50 行以下） | `wc -l kaji_harness/cli_main.py` |
| tests の `from kaji_harness.cli_main import` 文 | 147 | 0 | ガード grep（§変更固有検証）が 0 行 |
| tests の `kaji_harness.cli_main.*` patch target | 68 | 0 | `bash scripts/inventory_cli_main_patch_targets.sh` が 0 件 |
| 時限許容 allowlist entry | 7 | 0 | `pytest tests/test_private_imports.py` PASS |
| `kaji_harness.cli_main:main` entrypoint | 動作 | 動作（不変） | `python -c "from kaji_harness.cli_main import main; assert callable(main)"` + 既存 `python -m` E2E |
| 全テスト / 層 fitness / private import 検証 | PASS | PASS（不変） | `make check` / `pytest tests/test_layer_imports.py tests/test_private_imports.py` |

## ベースライン計測

実装フェーズ冒頭で以下を再実行し、Issue コメントに記録する（完了条件 1）。

```bash
cd <worktree>
# (1) shim 行数
wc -l kaji_harness/cli_main.py                                        # 196
# (2) from-import 文数
grep -rn 'from kaji_harness\.cli_main import' tests/ | wc -l          # 147
# (3) module object import
grep -rn 'from kaji_harness import cli_main\|^import kaji_harness\.cli_main' tests/  # 1 件
# (4) patch target（R0 #282 の棚卸しスクリプト）
bash scripts/inventory_cli_main_patch_targets.sh                      # 68 件（36+32）
# (5) シンボル別内訳（AST。grep より正確）
python3 - <<'EOF'
import ast, pathlib, collections
syms = collections.Counter()
for p in pathlib.Path("tests").rglob("*.py"):
    for node in ast.walk(ast.parse(p.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module == "kaji_harness.cli_main":
            for a in node.names:
                syms[a.name] += 1
print(len(syms), "distinct symbols")   # 35
EOF
```

## インターフェース

### 公開 IF は不変（宣言）

- CLI のコマンド体系・出力・exit code は一切変更しない。
- console entrypoint `kaji = "kaji_harness.cli_main:main"`（`pyproject.toml:42`）は不変。
- `python -m kaji_harness.cli_main`（tests の E2E と `kaji_harness/recovery/handler.py:655`
  の child run argv が使用）は不変。
- test logic / assertion / fixture behavior は不変（参照先の書換えのみ）。

### 変更される内部 IF

- `kaji_harness.cli_main` の `__all__`（現 76 entry）を `["main"]` へ縮小する。
  旧 re-export シンボルの `from kaji_harness.cli_main import X` は **ImportError で
  fail-loud** になる（意図した破壊。対象は tests のみで、本 Issue 内で全件書換える）。
- 旧 patch target `kaji_harness.cli_main.subprocess.run` / `.shutil.which` は shim の
  stdlib 束縛削除により **AttributeError で fail-loud** になる（同上）。

### 使用例（Before / After）

```python
# Before（shim 経由）
from kaji_harness.cli_main import EXIT_RUNTIME_ERROR, _handle_pr

with patch("kaji_harness.cli_main.subprocess.run") as mock_run:
    ...

# After（最終実体 module + stdlib 直 patch）
from kaji_harness.commands.exit_codes import EXIT_RUNTIME_ERROR
from kaji_harness.commands.pr import _handle_pr

with patch("subprocess.run") as mock_run:
    ...
```

## 変更スコープ

| 区分 | ファイル | 変更内容 |
|------|---------|---------|
| tests | from-import を持つ 26 ファイル（§ベースライン計測 (5) で機械列挙） | import 行の書換えのみ（決定 D1） |
| tests | `tests/test_artifacts_dir.py` | module object import 1 件と `cli_main.main(` 呼び出し表記の書換え（D1） |
| tests | `tests/test_cli_main.py` / `tests/test_dispatcher.py` / `tests/test_pr_bare_provider.py` / `tests/test_skill_migration.py` / `tests/test_issue_context_cli.py` / `tests/test_issue_prepend_note_cli.py` | patch target 文字列 68 件の書換え（D2） |
| tests | `tests/test_private_imports.py` | 時限許容 allowlist 撤去 + 機構 unit test 3 本の合成定数化（D5。Issue 本文に限定例外として明文化済み） |
| tests | prose のみ（`tests/conftest.py:76` 等、事実と乖離した docstring / コメント） | パス表記の最小修正 |
| production | `kaji_harness/cli_main.py` | re-export・stdlib 束縛削除、entrypoint 専用へ縮小（D3）。**production 変更はこの 1 ファイルのみ** |
| docs | `docs/ARCHITECTURE.md` / `docs/adr/009-module-boundary-private-import.md` / `docs/dev/testing-convention.md` | §影響ドキュメント参照 |

対象外（Issue 本文と同一）: 実体 module（`commands/*` / `providers/*` 等）の責務移動、
test logic / assertion / fixture の変更、CLI コマンド体系・出力・exit code、feat / bug 修正。
段階分割は不要（commit A/B/C の順序制御で中間状態の green を維持する。§移行ステップ）。

## 制約・前提条件

- #286（blocked-by）は merge 済み（main `2cb61e4`）。移行先の正本は #286 完了時の
  設計書・実装（= 現 `cli_main.py` の import 節そのもの）。
- tests の変更は import 行・module 参照・patch target 文字列・（それに伴い事実と
  乖離する）prose 記述の機械的書換えに限定する。**限定例外**として
  `tests/test_private_imports.py` の時限許容 allowlist 撤去と、それに伴う allowlist
  機構 unit test 3 本の参照データの合成定数化のみを許す（Issue #284 本文の完了条件・
  対象スコープに 2026-07-14 追記済み。#285 設計 §時限許容が予告した終結処理）。
  test body の制御フロー・assertion・fixture behavior は変更しない（限定例外の
  3 本も参照定数の差し替えのみで assertion・制御フローは不変）。
- production 側の変更は `cli_main.py` の re-export 削除と entrypoint 維持に限定する。
- feat / bug 修正を混在させない。
- gl:21 の `subprocess.run` patch スコープ規則（`docs/dev/testing-convention.md`
  §`subprocess.run` patch スコープ）は変更しない。禁止/許可の区分は不変で、
  target 表記のみ更新する（決定 D2）。

## 方針

### 決定 D1: from-import の移行対応表（正本 = 現 shim の import 節）

`kaji_harness/cli_main.py:13-101` の import 節が「シンボル → 最終実体 module」の
対応を機械可読に定義している（#286 完了時実装と一致）。tests が import する
35 シンボルの書換え先:

| 移行先 module | シンボル（tests が import する 35 件） |
|---------------|--------------------------------------|
| `kaji_harness.commands.exit_codes` | `EXIT_OK` / `EXIT_DEFINITION_ERROR` / `EXIT_INVALID_INPUT` / `EXIT_RUNTIME_ERROR` |
| `kaji_harness.commands.parser` | `create_parser` / `_get_version` |
| `kaji_harness.commands.validate` | `cmd_validate` / `_resolve_project_root_for_validate` |
| `kaji_harness.commands.run` | `cmd_run` / `_apply_execution_overrides` |
| `kaji_harness.commands.pr` | `_PR_BARE_PROVIDER_ERROR` / `_PR_BUILTIN_SUBCOMMANDS` / `_detect_repo` / `_forward_to_gh` / `_gh_capture_value` / `_github_pr_review` / `_handle_pr` / `_has_approve_flag` / `_has_request_changes_flag` / `_is_ascii_decimal` / `_user_specified_repo` |
| `kaji_harness.commands.config` | `_load_config_for_dispatch` |
| `kaji_harness.commands.output` | `_apply_jq` / `_compose_json_and_jq` / `_format_jq_results` |
| `kaji_harness.commands.issue` | `_github_issue_comment_with_verdict` / `_handle_issue` / `_handle_issue_local` / `_has_verdict_flags` / `_local_issue_close` / `_local_issue_comment` / `_local_issue_create` / `_local_issue_edit` |
| `kaji_harness.commands.main` | `main` |
| `kaji_harness.providers.context` | `build_worktree_note_body` |

module object 参照 1 件（`tests/test_artifacts_dir.py` の `cli_main.main(...)`）は
`from kaji_harness.commands.main import main` + 呼び出し箇所の `cli_main.main(` →
`main(` に書換える。

### 決定 D2: 属性 patch 68 件は target 文字列のみ stdlib 直 patch へ書換える（site の検証機構は不変）

書換え規則: `"kaji_harness.cli_main.subprocess.run"` → `"subprocess.run"`、
`"kaji_harness.cli_main.shutil.which"` → `"shutil.which"` の **target 文字列置換のみ**。
各 site の `side_effect` / `return_value` / 併用 patch（`resolve_main_worktree` 局所 mock
等）/ assertion には一切触れない。対象 6 ファイル:
`test_cli_main.py`(35) / `test_dispatcher.py`(24) / `test_pr_bare_provider.py`(5) /
`test_skill_migration.py`(2) / `test_issue_context_cli.py`(1) / `test_issue_prepend_note_cli.py`(1)。

#### 68 site の機構分類（設計時点の全件棚卸し）

「68 件一律」ではなく、各 site が gl:21（`docs/dev/testing-convention.md`
§`subprocess.run` patch スコープ）の禁止事由 = **worktree 解決の git 経路まで盲目 stub
して暗黙の分岐依存を作ること** をどの機構で回避しているかを分類した:

| 機構 | 内容と規約適合の根拠 | site | 件数 |
|------|---------------------|------|-----:|
| (i) gh 転送境界の spy/stub | gh CLI への転送 argv / returncode の検証が試験目的そのもの。到達経路は `_load_config_for_dispatch()` → `get_provider()` → passthrough で `resolve_main_worktree()` を経由しない（経路順序は `tests/test_pr_bare_provider.py:118-123` のコメントが明文化）。`call_count` / `argv[0] == "gh"` の assertion が「gh 以外の subprocess 消費者に届いていない」ことを検証しており、暗黙の分岐依存は構造的に排除済み | `test_cli_main.py` 全 35 件（`TestGithubPrReviewHandler` / pr builtin 系。`:1065` / `:1701` のコメントが禁止対象回避を明文化）、`test_dispatcher.py` の github passthrough 群 18 件、`test_pr_bare_provider.py:134-153` の 4 件、`test_skill_migration.py:155,157` | 59 |
| (ii) 不呼出し検証 | fail-fast / provider ガードで `assert_not_called()`。subprocess は一度も実行されず、mock は「gh が呼ばれない」ことの検証装置。worktree 解決が絡む site は 系統 B（`resolve_main_worktree` 局所 mock、`test_pr_bare_provider.py:110`）または provider method の局所 mock（`test_issue_context_cli.py:295-299` / `test_issue_prepend_note_cli.py:195-203`）を併用済み | `test_dispatcher.py:179,704,723,847`、`test_pr_bare_provider.py:111`、`test_issue_context_cli.py:299`、`test_issue_prepend_note_cli.py:203` | 7 |
| (iii) 系統 A 維持の passthrough spy | `side_effect=real_run` で実 subprocess を素通しして git 経路（系統 A）を保全し、gh 不呼出しのみ spy 検証。gl:21 対応がコメントで明文化済み（`test_dispatcher.py:246-249`） | `test_dispatcher.py:253` | 1 |
| (iv) 選択的 side_effect | `shutil.which` の jq 可用性のみ固定（`name == "jq"` で分岐する side_effect）。subprocess.run には触れない | `test_dispatcher.py:572` | 1 |

つまり **既存 68 件はすべて gl:21 の系統 A/B・不呼出し検証・転送境界検証のいずれかの
公認形態を既に実装済み** であり（gl:21 以後に書かれた site は in-file コメントで系統
A/B を明示引用している）、「禁止対象を系統 A/B へ移す」べき site は存在しない。
target 文字列の置換は sys.modules singleton の同一属性への変異という semantics を
1 bit も変えないため（unittest.mock「Where to patch」）、各 site の機構・到達経路・
波及先も書換え前後で不変である。

**contingency**: 実装フェーズの行単位検証（下記対応表）で万一「worktree 解決経路に
届く盲目 stub」という真の規約違反 site が発見された場合も、target 文字列置換自体は
挙動保存のため実施し、fixture 再設計（系統 A/B への移設）は Issue 完了条件 4
（fixture behavior 不変）と衝突するため **別 Issue に分離して起票する**
（`_shared/report-unrelated-issues.md` の手順）。

#### 実装フェーズ成果物: 行単位対応表（レビュー Should Fix 採用）

commit A で、68 site 全件の「旧 target → 新 target / 所属 test / 機構分類 (i)〜(iv) /
到達経路」の行単位対応表を生成して Issue コメントへ記録する（#283 §patch 対応表の
照合を拡張。「0 件判定」だけでなく規約適合と fixture 不変を行単位でレビュー可能にする）。

#### stdlib 直 patch を書換え先に選ぶ根拠

1. **意味的完全同値**: この 68 件は sys.modules singleton（`subprocess` / `shutil`
   module object）の属性変異であり、prefix module は「その属性チェーンを辿れる」以上の
   意味を持たない。`patch("subprocess.run")` は同一 object の同一属性を変異させる。
2. **既例**: R0 characterization test（`tests/test_cli_main_characterization.py`
   docstring「分割耐性（R1-robust）方針」）が stdlib 側 patch を既に採用しており、
   「object identity 経由で解決されるため、対象関数が別 module へ移っても届く」ことを
   明文化済み。
3. **再発防止**: `kaji_harness.commands.pr.subprocess.run` 等、別 module の偶発的
   束縛に再結合すると「その module が束縛をやめた時点で壊れる」今回と同じ構造を
   再生産する。stdlib 直 patch は束縛位置に依存しない。また表記から偽の「局所化」の
   示唆が消え、global 波及という実態が可視化される（gl:21 の判断はもともと表記では
   なく到達経路で行う。§影響ドキュメントの testing-convention 更新で明文化）。

### 決定 D3: 縮小後の `cli_main.py`

```python
"""kaji console entrypoint（`kaji_harness.cli_main:main` / `python -m kaji_harness.cli_main`）。

実装の実体は kaji_harness.commands 配下（#283/#286 で分割・分離、#284 で shim 撤去）。
"""

from __future__ import annotations

import sys

from kaji_harness.commands.main import main

__all__ = ["main"]

if __name__ == "__main__":
    sys.exit(main())
```

- `from kaji_harness.commands.main import main` は public シンボルの下方向 import
  （shim 層 rank 4 → command 層 rank 3）であり、ADR 009 の層規則・private import
  規則のいずれにも抵触しない。absolute import は
  `docs/reference/python/python-style.md` §インポート順序「自プロジェクトの
  インポート（相対パス禁止）」に従う（現 shim の相対 import は #283 由来だが、
  縮小後の新規記述は規約準拠の absolute にする）。
- `import shutil` / `import subprocess` の互換束縛は削除する（D2 で参照ゼロ化済み）。

### 決定 D4: entrypoint として許可する tests 参照の一覧（完了条件 7）

書換え後に tests に残る `cli_main` 参照は **module 実行（`python -m
kaji_harness.cli_main`）のみ** とし、in-process の実装シンボル import は 0 件にする:

| ファイル | 参照形態 |
|---------|---------|
| `tests/test_cli_version.py:67` | `[sys.executable, "-m", "kaji_harness.cli_main", "--version"]` |
| `tests/test_cli_validate.py`（8 箇所） | 同上（`validate` E2E） |
| `tests/test_local_cli_large_local.py:21` | `_KAJI_CMD` 定数 |
| `tests/test_provider_guard_large_local.py:20` | `_KAJI_CMD` 定数 |
| `tests/test_migrate_comment_filenames.py:420` | module 実行 |
| `tests/test_cli_main.py:513` | `run --help` の module 実行 |
| `tests/test_recovery_e2e_large_local.py:84` | module 実行 |

これらは entrypoint の継続動作検証そのものであり、実装シンボル参照ではない。
このほか `tests/test_layer_imports.py` の module 分類 mapping key
`"kaji_harness.cli_main"`（決定 D6）と、履歴を記述する prose（docstring / コメント）は
参照 0 件の対象外（prose は現在形の事実と乖離する箇所のみ最小修正する。例:
`tests/conftest.py:76` の「`cmd_run()` が `kaji_harness/cli_main.py` で〜」）。

`main` の in-process 利用（10 ファイル）と `create_parser`（11 ファイル）も
`commands.main` / `commands.parser` へ移行する。console script の import 契約
`kaji_harness.cli_main:main` は「module import + 属性取得」で解決される（entry points
仕様）ため、縮小後 module が `main` を re-import している限り維持され、変更固有検証
（§テスト戦略）と既存 `python -m` E2E（`make check` 内の large_local）で担保する。

### 決定 D5: 時限許容 allowlist の撤去（#285 設計が予告した期限執行）

縮小後の `cli_main.py` は禁止 signature を 1 件も持たないため、
`tests/test_private_imports.py` の `TRANSITIONAL_ALLOWLIST`（7 entry）は全件 stale と
なり、検証器の厳密一致検査（`tests/test_private_imports.py:210-215`）が撤去を強制する:

- `TRANSITIONAL_ALLOWLIST` → 空の `frozenset()` にする（時限許容の終結をコメントで記録）。
- allowlist **機構**の unit test 3 本（`test_allowlist_filters_registered_forbidden_signature` /
  `test_unregistered_violation_in_shim_is_detected` / `test_stale_allowlist_entry_is_detected`）は
  module 定数 `TRANSITIONAL_ALLOWLIST` を直接参照しているため、**合成データの局所定数**
  （現 7 entry 相当の synthetic allowlist）を参照する形へ書換え、機構カバレッジを維持する。
  assertion の意味（filter / 新規検出 / stale 検出）は不変。

これは「機械的 import 書換え」を超える tests 変更であるため、Issue #284 本文の
完了条件・対象スコープに **限定例外として明文化した**（2026-07-14 追記済み。
完了条件「機械的書換えに限定（限定例外: …）」と対象スコープ
`tests/test_private_imports.py` の行）。根拠: #285 設計書 §時限許容と
`tests/test_private_imports.py:214-215` が「#284 時点で撤去が強制される」と事前に
設計した終結処理であり、完了条件（private import 検証の再実行 PASS）の前提条件で
ある。機構 unit test 3 本の変更は参照定数の差し替えのみで、assertion・制御フローは
不変（Issue 完了条件 4 の但し書きと整合）。

### 決定 D6: 層 mapping の `cli_main` entry は存続

`cli_main.py` は削除されず entrypoint として存続するため、
`tests/test_layer_imports.py:46` の `"kaji_harness.cli_main": "shim"` は維持する
（mapping 完全性検査が全 module の分類を要求する）。縮小後の唯一の runtime edge
`cli_main → commands.main` は rank 規則（4 → 3 の下方向）で許可済み。層ラベルの
改名（shim → entrypoint 等）は fitness test の再設計を伴うため本 Issue では行わない。

### 移行ステップ（コミット順序。各コミットで `make check` green を維持）

1. **再計測と照合の記録**: §ベースライン計測を再実行し、#283 対応表との照合結果と
   ともに Issue コメントへ記録（完了条件 1・2）。
2. **commit A（tests のみ）**: D1 の from-import / module 参照書換え + D2 の
   patch target 書換え。shim は存置したままなので新旧どちらの参照でも green。
   D2 の行単位対応表（旧 target → 新 target / 機構分類 / 到達経路の 68 行）を
   生成して Issue コメントへ記録する。
3. **commit B（production + fitness）**: D3 の `cli_main.py` 縮小と、D5 の allowlist
   撤去・機構 test の合成定数化を **同一コミット** で行う（allowlist は shim の
   statement と厳密一致検査のため分離すると中間状態で fail する）。事実と乖離する
   tests 内 prose の最小修正も含む。
4. **commit C（docs）**: §影響ドキュメントの更新。
5. **検証**: `make check` / `pytest tests/test_private_imports.py tests/test_layer_imports.py`
   / inventory script 0 件 / ガード grep 0 行 / entrypoint 確認（§テスト戦略）。

## テスト戦略

> 変更タイプ: **実行時コード変更**（`cli_main.py` の re-export・stdlib 束縛の削除は
> import 時挙動を変える）+ tests の機械的書換え。詳細は
> [テスト規約](../../docs/dev/testing-convention.md) 参照。

### 既存テストのカバレッジ評価（safety net）

- shim（`cli_main.py`）自体はロジック 0 行の re-export であり、実ロジックは
  `commands/*` にある。実ロジックのカバレッジは R0（#282）の characterization test と
  #283/#286 で評価・補強済みで、本 Issue ではロジックを一切動かさない。
- 追加の safety net 構築は不要。**全既存テストが書換え前後で同一 assertion のまま
  PASS すること**自体が振る舞い保存の証跡（テスト側は参照先の書換えのみで、検証内容
  が不変であることは diff が import 行・patch 文字列・prose、および限定例外の
  `tests/test_private_imports.py`（D5: allowlist 撤去 + 機構 test の参照定数差し替え）
  に限定されることで示す）。

### Small テスト

- `tests/test_private_imports.py`: 空 allowlist での厳密一致（禁止 signature 0 = allowlist 0）、
  allowlist 機構 unit test（合成定数化後も filter / 新規検出 / stale 検出の 3 観点を維持）。
- `tests/test_layer_imports.py`: 縮小後 `cli_main → commands.main` edge の許可判定
  （既存の synthetic ケースで担保済み）。

### Medium テスト

- `tests/test_private_imports.py` / `tests/test_layer_imports.py` の実ツリー走査
  fitness test（完了条件 9・10 の検証本体）。#285 / #286 で確立したコマンド:
  `source .venv/bin/activate && pytest tests/test_private_imports.py tests/test_layer_imports.py`
- in-process CLI テスト群（`test_cli_main.py` / `test_dispatcher.py` 等）が
  書換え後の参照先で全 PASS。

### Large テスト（large_local）

- `python -m kaji_harness.cli_main` を subprocess 実行する既存 E2E
  （`test_local_cli_large_local.py` / `test_provider_guard_large_local.py` /
  `test_recovery_e2e_large_local.py` 等、D4 の一覧）が entrypoint の継続動作を検証。
  実 API 疎通（large_forge）は本変更の影響面に含まれないため対象外。

### bridging test

- 新規追加しない。根拠: (1) 上記の通り全既存テスト（S/M/L）が同一 assertion で
  移行前後 PASS することが「同じ入力 → 同じ出力」の保証に相当する。(2) entrypoint
  維持は既存 large_local E2E が恒久的に検証している。

### 変更固有の一時検証（恒久テスト化しない）

```bash
# (a) 実装シンボル参照・patch target の残存ゼロ検査（完了条件 8 の再現可能コマンド）
grep -rnE "from kaji_harness\.cli_main import|from kaji_harness import cli_main|^import kaji_harness\.cli_main|['\"]kaji_harness\.cli_main\." tests/ kaji_harness/
# → 期待: 0 行（exit code 1）。`"-m", "kaji_harness.cli_main"`（末尾ドットなし）は意図的に非該当
# (b) patch target 棚卸し
bash scripts/inventory_cli_main_patch_targets.sh          # → 0 件
# (c) console script import 契約
python -c "from kaji_harness.cli_main import main; assert callable(main)"
# (d) module 実行契約
python -m kaji_harness.cli_main --version
```

恒久テストを追加しない理由（testing-convention の 4 条件）:

1. 独自ロジックの追加・変更を含まない（参照書換えと縮小のみ）。
2. 想定不具合は既存ゲートで捕捉済み: 縮小後 shim への新規 import は **シンボル不在の
   ImportError / AttributeError で構造的に即失敗**し、`kaji_harness` 側の再依存は
   `test_private_imports.py` の stale 検査 + 空 allowlist が恒久検出する。
3. (a)(b) を恒久化しても、上記の構造的失敗 + fitness test を超える回帰検出情報は増えない。
4. 本節がその説明（レビュー可能な形の記録）。副作用のある検証はなく隔離環境は不要。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | あり（ADR 009 更新） | `36` / `81-99` 行の「cli_main.py は #284 で削除」を完了形（shim 撤去・entrypoint 存続）へ更新し、時限許容 allowlist の終結を追記。決定内容は不変 |
| docs/ARCHITECTURE.md | あり | `109-110` の cli_main 説明（「互換 shim。最終削除は #284」）と `133` の層図を縮小後の実態へ更新 |
| docs/dev/testing-convention.md | あり | `134` 行の patch スコープ記述が `kaji_harness.cli_main.subprocess.run` を名指ししている。D2 の stdlib 直 patch 表記へ更新し、「属性 patch は表記（prefix module）に依らず sys.modules singleton へ global に波及する。禁止/許可の判定は表記ではなく到達経路（worktree 解決の git 経路に届くか）で行う」旨を明文化する（禁止/許可の区分・判断基準そのものは不変） |
| docs/dev/（その他） | なし | ワークフロー・開発手順に変更なし |
| docs/reference/ | なし | 規約変更なし（ADR 009 参照経由のみ） |
| docs/cli-guides/ | なし | CLI 仕様不変 |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| unittest.mock — Where to patch | https://docs.python.org/3/library/unittest.mock.html#where-to-patch | 「patch where the thing is looked up」— 名前再束縛 patch は lookup 場所依存で module 移動に脆い一方、`subprocess.run` の属性 patch は sys.modules singleton の属性変異で prefix に依存しない。D2 の stdlib 直 patch が既存 68 件と意味的同値である根拠 |
| Entry points specification | https://packaging.python.org/en/latest/specifications/entry-points/ | object reference は `importable.module:object.attr` 形式で「module を import し属性を取得」して解決される。縮小後 `cli_main` が `main` を re-import すれば `kaji_harness.cli_main:main` 契約は維持される（D3/D4 の根拠） |
| Python コマンドライン `-m` | https://docs.python.org/3/using/cmdline.html#cmdoption-m | `-m` は module を import し `__main__` として実行する。`if __name__ == "__main__"` block の維持で `python -m kaji_harness.cli_main` 互換が保たれる（D3 の根拠） |
| 現行 shim（対応表の正本） | `kaji_harness/cli_main.py:13-101` | #286 完了時点のシンボル → 最終 module 対応を import 節として機械可読に保持。D1 の移行対応表はここから機械的に導出 |
| #286 設計書（移行先の正本） | `draft/design/issue-286-refactor-cli-r4.md` §1 層の定義 / §2 責務分類 | 層対応表と command 層 66 関数の最終配置。`cli_main` は shim 層 rank 4（「#284 で削除」の予告は「shim としての削除」であり、本設計で entrypoint 存続として具体化） |
| #283 設計書 §patch 対応表 | `draft/design/issue-283-refactor-cli-main-py-kaji-harness-comman.md` | 「維持 68 件 / 書換え 63 件」の分類と根拠。今回の残存 68 件（36+32）が「維持」分類と一致することを照合済み（完了条件 2） |
| #285 設計書 §時限許容 | `draft/design/issue-285-refactor-private-import-r3.md` | transitional allowlist の statement 単位登録と「#284 時点で stale 化し撤去が強制される」設計。D5 の根拠 |
| stale 強制の実装 | `tests/test_private_imports.py:202-216` | 「allowlist entry に対応する statement が無い → fail（stale）」の厳密一致検査。D5 が撤去を伴う必然性の一次情報 |
| R1-robust patch 方針の既例 | `tests/test_cli_main_characterization.py:1-16`（docstring） | 「stdlib 側（`subprocess.run` / `shutil.which`）を patch する。object identity 経由で解決されるため、対象関数が別 module へ移っても届く」— D2 の repo 内既例 |
| gl:21 patch スコープ規則 | `docs/dev/testing-convention.md:132-145` + gl:21 設計書 `draft/design/issue-21-refactor-drop-test-compat-fallback-in-re.md` §制約・前提条件 | dispatch/provider 結合での盲目 stub 禁止と代替（系統 A: 実 git fixture / 系統 B: `resolve_main_worktree` 局所 mock）。D2 の site 機構分類 (i)〜(iv) の判定基準。既存 site の gl:21 対応は in-file コメント（`tests/test_dispatcher.py:246-249` / `tests/test_pr_bare_provider.py:104-108,118-123` / `tests/test_cli_main.py:1065,1701`）が明文化 |
| コーディング規約（import） | `docs/reference/python/python-style.md:62-64` | 「自プロジェクトのインポート（相対パス禁止）」— D3 の absolute import の根拠 |
| ADR 009 | `docs/adr/009-module-boundary-private-import.md` | 層規則（foundation→…→shim）と時限許容 allowlist 運用の正本。D3/D5/D6 の規約的根拠 |
