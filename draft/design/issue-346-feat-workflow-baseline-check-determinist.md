# [設計] Baseline Check の deterministic exec step 化と既知 failure ポリシー一元化

Issue: #346

## 概要

4 つの開発 workflow（`dev.yaml` / `dev-thorough.yaml` / `dev-thorough-fable.yaml` /
`dev-local.yaml`）に、変更前 pytest baseline を取得・機械判定する deterministic precheck step
（exec_script skill `baseline-precheck`）を追加する。この step は設計書のレビュー
（`review-design` または `verify-design`）が PASS した直後、`implement` の開始前に実行する。
現在 5 つの skill に分散している既知 failure の停止基準・3タプル比較・コミット可否判定を、
単一の構造化 artifact と正本ポリシー（`docs/dev/baseline-check.md`）へ一元化する。

## 背景・目的

### ユーザーストーリー

- **workflow 運用者**として、実装開始前の baseline 取得・構造化・機械判定を決定論 step で
  一度だけ実行し、後段の implement / review / fix / verify / final-check が同じ構造化結果を
  参照できるようにしたい。既知 failure と変更内容の意味的な関連性判断は implement agent に残す。
- **実装 agent**として、pytest baseline のパース・Issue コメント生成・最新コメント選択を毎回
  推論せず、実装と変更固有テストへコンテキストを使いたい。
- **レビュー担当者**として、clean / known_failures / blocked / invalid の状態と測定 commit を
  機械的に識別し、新規 regression だけを一貫して判定したい。

### 現状の問題（Issue #346 より）

- baseline clean 時は Issue コメントが残らず、「未実行」と「実行済み clean」をコメント有無で
  区別できない。
- 停止基準・最新コメント選択・3タプル比較・コミット可否が `issue-implement` /
  `issue-review-code` / `issue-fix-code` / `issue-verify-code` / `i-dev-final-check` に分散し、
  各 agent が毎回再解釈している。
- #323 run `260714213832` と #341 run `260716004739` の比較では、agent 外へ移せる機械処理を
  品質を落とさず移す余地がある（calls -10.3% の一方で output / wall time は増）。

### 代替案と不採用理由

Issue 本文「代替案と不採用理由」の 4 案（現行維持 / 常時停止 / 常時許可 / 公開 CLI 新設）は
人間により不採用が決定済み。本設計はこれを変更しない。設計レベルの追加代替案:

- **`exec:` step で argv を直接書く**: 4 つの workflow variant に同一 argv が重複し、
  ポリシー説明を YAML コメントに分散させることになる。`docs/dev/workflow-authoring.md`
  § 使い分けの「named・再利用・ドキュメント価値のある決定論処理は `exec_script:` を推奨」
  に従い、exec_script skill とする。
- **`kaji` CLI subcommand（`kaji baseline` 等）の新設**: 公開 CLI 契約の追加になるため
  Issue のスコープ境界に抵触する。不採用。
- **設計書から変更スコープを自動抽出して同一領域停止も完全機械化**: 設計書は散文であり
  決定的パースは保証できず、`issue-design` テンプレート変更は Issue の対象領域外。
  同一領域判定の **基準と評価関数** は一元化しつつ、scope パスの特定（意味判断）は
  現行どおり implement agent の入力とする（現行意味論の維持）。

## インターフェース

### 1. 新規 exec_script skill: `baseline-precheck`

- `.claude/skills/baseline-precheck/SKILL.md`
  - frontmatter: `exec_script: kaji_harness.scripts.baseline_precheck`
  - `review-poll` と同じ deterministic skill パターン（agent 起動なし）
- workflow step 宣言（`dev.yaml` / `dev-thorough.yaml` / `dev-thorough-fable.yaml` /
  `dev-local.yaml` 共通）:

```yaml
  - id: baseline
    skill: baseline-precheck
    timeout: 1800
    on:
      PASS: implement
      ABORT: end
```

- 配線変更: `review-design.on.PASS: implement → baseline`、
  `verify-design.on.PASS: implement → baseline`。他の遷移
  （`implement.on.RETRY: implement`、`review-code.on.BACK_IMPLEMENT: implement` 等）は不変。
  これにより「設計書のレビューが PASS した直後、implement 開始前に 1 回だけ」実行され、
  BACK_IMPLEMENT では再実行されない。`BACK: design` / `BACK_DESIGN: design` で design へ
  戻った場合は design 承認後に baseline step が再実行されるが、実装 commit が存在する場合は
  再測定せず既存 artifact を再利用する（§ measure の前提条件と再測定ガード。変更後の
  baseline 上書きを fail-closed に拒否する）。

- `baseline` step 自体は agent を起動せず、pytest の実行、plugin report のパース、artifact / コメント /
  verdict の生成までを entrypoint が行う。`dev-local.yaml` でも同じ step を配線し、コメントは
  active provider の `comment_issue` を通して local issue store へ記録する。

### 2. 内部 entrypoint: `kaji_harness/scripts/baseline_precheck.py`

3 モードを持つ。ロジック本体は `kaji_harness/baseline.py`（純粋ロジック、Small テスト対象）に
置き、scripts 側は env/argv shim とする（`codex_review_poll` / `review_poll_entry` と同じ分離）。

#### 入力（共通）

| 入力 | 形式 | 必須 | 説明 |
|------|------|------|------|
| `KAJI_WORKTREE_DIR` | env（`--worktree DIR` で override 可） | ✅ | 測定対象 worktree の絶対パス |
| `KAJI_ISSUE_ID` | env（`--issue ID` で override 可） | measure 時 ✅ | Issue コメント投稿先 |
| `KAJI_VERDICT_PATH` | env | 任意 | あれば pure YAML verdict を保存（exec_script 経路で harness が注入） |

pytest は `[worktree_dir]/.venv/bin/python -m pytest -p kaji_harness.pytest_baseline_plugin`
として subprocess 実行する（entrypoint 自身の interpreter に依存しない）。`shell=False`、
cwd は worktree。plugin は env `KAJI_BASELINE_REPORT_PATH`（entrypoint が
`<artifact_dir>/report-<mode>.json` を注入）へ機械可読 report を atomic write する。

#### pytest report の lossless 取得契約（内部 plugin）

JUnit XML（`--junitxml`）は**不採用**とする。pytest 9.0.2 の `_pytest/junitxml.py` は
xunit2 の testcase に `classname` / `name` しか残さず（raw nodeid は `mangle_test_address`
で不可逆に変換される）、`failure` / `error` 要素に exception `type` 属性を付けない
（`_add_simple` は message のみ）。message も bare assert では `assert ...`、setup error では
`failed on setup with ...` となり、先頭から例外クラス名を復元できない。公式 docs も
raw nodeid / exception type の可逆性を保証しない（Primary Sources 参照）。

現行 3 タプル `(nodeid, kind, error_type)` の raw 値を損失なく得るため、内部 pytest plugin
`kaji_harness/pytest_baseline_plugin.py`（新規）を `-p` で読み込む:

- `pytest_runtest_makereport` hookwrapper で `call.excinfo.typename`（例: `AssertionError`。
  bare assert も excinfo 上は `AssertionError`）を report へ付与し、
  `pytest_runtest_logreport` で `(nodeid, when, outcome, error_type)` を収集する
- `pytest_collectreport` で collection error（import error 等）を収集する
- kind の写像（現行意味論の維持）: call phase の failure → `FAILED`、setup / teardown の
  error および collection error → `ERROR`
- report は JSON 1 ファイルへ atomic write。plugin は kaji_harness の内部モジュールであり、
  worktree `.venv` は main `.venv` への symlink のため import 可能（公開契約の追加なし）
- entrypoint は report JSON を正本として 3 タプルを構成する。pytest の text 出力・JUnit XML
  はパースしない

#### mode 1: measure（デフォルト。workflow step として実行）

- **出力 1 — 構造化 artifact（正本）**:
  `[worktree_dir]/.kaji-artifacts/baseline/baseline.json` へ atomic write（tmp + `os.replace`）。
  `.kaji-artifacts/` は gitignore 済みのため git status を汚さず、worktree 削除とともに
  自然消滅する（branch スコープの寿命）。
- **出力 2 — Issue コメント**: `status != clean` の場合のみ、artifact から決定的に生成した
  `## Baseline Check 結果` コメントを provider 層経由で投稿する。現行規約どおり verdict
  marker は付けない（判定コメントではなく証跡コメント）。投稿契約は
  `kaji issue comment --commit` と同一とする: GitHub provider は API 投稿のみで完結し、
  **local provider は `comment_issue()` の後に `commit_issue_change()` まで完了**して
  comment file を main worktree へ atomic commit する（`git commit --only`。
  `LocalProvider.comment_issue()` 単独では commit されず main worktree が dirty になる
  ことが既存契約で固定されているため。正本: `kaji_harness/commands/issue.py` の
  `--commit` 経路、`tests/test_local_issue_commit_flag.py`）。
- **出力 3 — verdict**: `KAJI_VERDICT_PATH` への pure YAML と stdout の
  `---VERDICT---` block。exec_script 経路は AI formatter fallback を呼ばない既存契約に乗る。
- **出力順序と失敗時の契約**: ① artifact atomic write → ② Issue コメント投稿（非 clean 時
  のみ。local は commit まで）→ ③ verdict 書き出し、の順で行う。② が失敗した場合
  （投稿失敗・commit 失敗）は verdict を書かず exit 非 0 で終了する（harness が
  `ScriptExecutionError` として fail-loud に停止。書き込み済み artifact は次回 measure の
  再測定ガード通過時に atomic overwrite されるため無害）。step 再実行での再投稿による
  コメント重複は許容する（コメントは証跡専用で consumer が読まない。正本は artifact のみ）。

artifact schema（Pydantic v2 model で読み書きとも検証）:

```json
{
  "schema_version": 1,
  "issue_id": "346",
  "branch": "feat/346",
  "measured_commit": "<HEAD の SHA>",
  "measured_at": "2026-07-16T03:00:00+00:00",
  "pytest_exit_code": 1,
  "summary": {"collected": 106, "passed": 100, "failed": 2, "errors": 1, "skipped": 3},
  "status": "known_failures",
  "stop_reason": null,
  "failures": [
    {"nodeid": "tests/test_foo.py::test_bar", "kind": "FAILED",
     "error_type": "AssertionError", "message_head": "expected 1, got 2"}
  ]
}
```

status 分類と verdict への写像:

| 条件 | status | stop_reason | verdict |
|------|--------|-------------|---------|
| exit 0 かつ FAILED / ERROR 0 件 | `clean` | — | PASS |
| exit 1 かつ failure 件数 1〜10 | `known_failures` | — | PASS |
| exit 1 かつ failure 件数 > 10 | `blocked` | `mass_failures` | ABORT |
| report 整合性違反（exit 0 なのに failure > 0 件 / exit 1 なのに failure 0 件）、report 欠損・パース不能、`.venv` の pytest 起動不能 | `invalid` | 事由文字列 | ABORT |
| **0 / 1 以外のすべての exit code**（2 / 3 / 4 / 5 / 6 / 将来追加・未知値・signal 由来の負値を含む） | `invalid` | `unexpected_exit_code:<code>` | ABORT |

exit code の分類は既知値の列挙ではなく **fail-closed の catch-all** とする。pytest 公式の
最新 stable docs は exit code 6（`--maxwarnings` 上限超過）を定義する一方、installed
pytest 9.0.2 の `ExitCode` enum は 0〜5 のみであり、`pyproject.toml` L32 の
`pytest>=8.0.0`（上限なし）は lock 更新で新しい code の到達を許す。0 / 1 以外は理由を
問わず `invalid` / ABORT に落とし、silent な誤分類を排除する。

`known_failures` の PASS は、baseline の取得と機械分類が正常に完了したことだけを表す。
その failure が今回の変更対象へ意味的に影響しないことを承認する verdict ではない。

#### deterministic step と LLM の責務境界

| 責務 | 実行主体 | 理由 |
|------|----------|------|
| 測定 commit の取得、pytest 実行、plugin report の収集・パース | deterministic entrypoint | 同じ入力から同じ構造化結果を再現できる機械処理 |
| failure 件数と exit code による status 分類 | deterministic entrypoint | 明示した規則で機械判定できる |
| artifact / コメント / PASS・ABORT verdict の生成 | deterministic entrypoint | agent formatter と自由形式出力への依存を除く |
| baseline と最終 pytest の 3 タプル比較 | deterministic entrypoint | 集合比較として機械判定できる |
| 設計書から変更対象 scope を特定する | `issue-implement` agent | 散文の設計意図を決定的には抽出できない |
| 少数の既知 failure と変更機能の意味的な関連性を判断する | `issue-implement` agent | パス一致だけでは間接依存・横断機能を判定できない |

baseline step 内では `make check` を実行せず、構造化結果を得るため pytest を直接実行する。
`make check` と review / final-check の独立品質確認は実装後の品質ゲートとして従来どおり残す。

閾値 10 は現行 `baseline-check.md`「目安: 10件超」の定数化
（`kaji_harness/baseline.py` のモジュール定数）。pytest exit code の意味は公式
リファレンス（Primary Sources 参照）に従うが、分類は上表の catch-all を正とする
（例: exit 5 = 収集 0 件は baseline を定義できず `invalid`）。

#### measure の前提条件と再測定ガード（fail-closed）

baseline は「実装変更前」の snapshot である（Issue #346「変更前 pytest baseline」、現行
`baseline-check.md`「実装開始前」の人間決定）。変更後の再測定で regression を baseline へ
取り込み `--compare` を無効化する経路を塞ぐため、measure は次の順で判定する:

1. **実装 commit の有無**: `git log [default_branch]..HEAD -- ':(exclude)draft/design/'` が
   非空（実装 commit あり）の場合、measure は**再測定しない**:
   - 既存 artifact があり、その `measured_commit` が HEAD の ancestor → 既存 artifact を
     再利用し、その status に対応する verdict を返す（evidence に `reused` を明記。
     上書きなし）
   - 既存 artifact が無い、または `measured_commit` が ancestor でない（rebase / amend 後）→
     `invalid` / ABORT（stop_reason: `baseline_unrecoverable_post_implement`）。suggestion に
     人間の選択肢（実装 commit を別 branch へ退避して再測定する / baseline なしの扱いを
     人間が判断する）を列挙する
2. **working tree の clean 検査**: 再測定する場合、`git status --porcelain` が非空なら
   `invalid` / ABORT（stop_reason: `dirty_worktree`）。gitignore 済みの `.kaji-artifacts/` は
   porcelain に現れないため誤検知しない
3. 上記を通過した場合のみ pytest を実行し、artifact を atomic overwrite する

この規則により「**measure が書く artifact は常に、実装 commit を含まない clean tree で
測定されたもの**」という不変条件が成立し、「最後に書かれた artifact が唯一の正本」が
安全になる。`measured_commit` の ancestor 検査（stale 検出）だけでは再測定直後の同一
commit を排除できないため、書き込み側で fail-closed にする。再開シナリオごとの挙動:

| シナリオ | 状態 | 挙動 |
|----------|------|------|
| 初回実行 | artifact なし・実装 commit なし | 測定して保存 |
| crash retry / design 修正後の再承認 | artifact あり・実装 commit なし | 再測定して atomic overwrite（設計変更を反映した新鮮な baseline） |
| BACK: design 後の再承認（実装済み） | artifact あり・実装 commit あり・ancestor | 既存 artifact を再利用（再測定・上書きなし） |
| artifact 欠損 + 実装済み | artifact なし・実装 commit あり | ABORT（変更前 baseline は再構築不能。人間判断） |
| 履歴書き換え後 | artifact あり・ancestor でない | ABORT（`baseline_unrecoverable_post_implement`） |

#### mode 2: `--evaluate --scope <path> [--scope <path> ...]`

implement agent が `known_failures` 時の開始直後に呼ぶ、同一領域停止判定の評価関数。
scope パス（設計書「変更スコープ」から agent が特定した対象ファイル / ディレクトリ）を
入力に取り、stdout へ JSON 1 オブジェクトを出力する:

```json
{"verdict": "ok", "stop": false, "overlapping": [],
 "baseline_status": "known_failures", "measured_commit": "<sha>"}
```

- 重複判定: failure の nodeid ファイルパスが scope パスと完全一致、または scope が
  ディレクトリとして前方一致 → `overlapping` に列挙し `stop: true`。
- 判定はこの関数が唯一の機械的正本。agent は `stop: true` なら ABORT する。
  `stop: false` でも意味的な追加根拠があれば agent は停止してよい（現行意味論。
  その場合は根拠をコメントに記録する）。

#### mode 3: `--compare`

implement Step 7b / fix-code / verify-code / review-code / final-check が pytest の
regression 判定に使う。artifact を読み、pytest を再実行して 3タプル
`(nodeid, kind, error_type)` を比較し、stdout へ JSON 1 オブジェクトを出力する:

```json
{"verdict": "ok", "regressions": [], "matched_baseline": ["..."], "resolved": [],
 "current_exit_code": 1, "measured_commit": "<sha>", "current_commit": "<sha>"}
```

- `verdict`: `ok`（regression 0 件）/ `regression` / `stale_baseline` / `missing_baseline`
- **stale 判定**: `measured_commit` が現在 HEAD の ancestor でない（`git merge-base
  --is-ancestor`）→ `stale_baseline`。artifact 不在 → `missing_baseline`。いずれも
  agent 側ポリシーは「比較不能。baseline step の再実行（`kaji run ... --from baseline`）を
  suggestion に含めて停止」とする。silent fallthrough はしない。この再実行が変更後
  baseline を作ることはない — measure 側の再測定ガード（§ measure の前提条件と再測定
  ガード）により、実装 commit がある状態では reuse または ABORT に落ちる。

#### エラー・exit code 契約

- ポリシー上の結論（clean / known_failures / blocked / invalid / regression / stale）は
  **すべて exit 0** で、artifact / verdict / JSON に構造化して返す
  （`review_poll_entry` の「ABORT verdict を emit して return 0」パターン）。
- exit 非 0 は harness が `ScriptExecutionError` として fail-loud に扱う catastrophic 経路
  （git / worktree 不在、artifact 書き込み不能等）に限定する。

### 使用例

```bash
# workflow（harness が自動実行。agent 起動なし）
#   review-design PASS → baseline → implement

# implement agent: known_failures 時の停止判定（設計書の変更スコープを入力）
cd [worktree_dir] && .venv/bin/python -m kaji_harness.scripts.baseline_precheck \
  --evaluate --scope kaji_harness/runner.py --scope tests/test_runner.py
# → {"verdict": "ok", "stop": false, ...}

# implement Step 7b / review-code / verify-code / final-check: regression 比較
cd [worktree_dir] && .venv/bin/python -m kaji_harness.scripts.baseline_precheck --compare
# → {"verdict": "ok", "regressions": [], ...}

# 手動 /issue-implement 起動などで artifact が無い場合の再測定（env なしでも可）
cd [worktree_dir] && .venv/bin/python -m kaji_harness.scripts.baseline_precheck \
  --worktree "$(pwd)" --issue 346
```

## 制約・前提条件

- **公開契約を追加しない**: 新しい `kaji` subcommand / workflow schema フィールドは作らない。
  既存の exec_script dispatch（`python -m <module>`、`sys.executable` 実行、KAJI_* env、
  artifact-primary verdict、AI formatter fallback なし）にそのまま乗る。
- **現行意味論の維持**（人間決定）: 「対象外かつ切り分け可能なら継続」「最終 FAILED / ERROR が
  baseline 3タプルと一致し、新規 regression 0 件、非 pytest 品質ゲート全 PASS の場合だけ
  コミット可」を変えない。
- worktree の `.venv` は `issue-start` が main の `.venv` への symlink として作成済み
  （`issue-start/SKILL.md` Step 2.5）。したがって `[worktree]/.venv/bin/python` で
  pytest と `kaji_harness` の双方が利用できる。本機能は main へ merge されて初めて
  workflow から利用可能になる（harness 変更の通常制約）。
- `.kaji-artifacts/` は gitignore 済み。artifact を commit しない。
- exec-step / exec_script step は `agent` を持たない = LLM コスト 0 の不変条件を保つ。
- Pre-Handoff Review・review-code / final-check の独立品質確認は削除・軽量化しない
  （Issue スコープ境界）。

## 変更スコープ

| 領域 | ファイル | 変更 |
|------|----------|------|
| harness | `kaji_harness/baseline.py`（新規） | plugin report パース、status 分類（fail-closed catch-all）、3タプル比較、overlap 評価、再測定ガード判定、artifact model（Pydantic） |
| harness | `kaji_harness/pytest_baseline_plugin.py`（新規） | pytest hook（makereport hookwrapper / logreport / collectreport）で raw nodeid・phase・exception typename を収集し JSON report を atomic write する内部 plugin |
| harness | `kaji_harness/scripts/baseline_precheck.py`（新規） | env/argv shim、pytest subprocess（`-p` plugin 読み込み）、verdict / コメント出力（local は comment commit まで） |
| skill | `.claude/skills/baseline-precheck/SKILL.md`（新規） | exec_script frontmatter + 単体起動契約 |
| workflow | `.kaji/wf/dev.yaml` / `dev-thorough.yaml` / `dev-thorough-fable.yaml` / `dev-local.yaml` | `baseline` step 挿入、`review-design` / `verify-design` の PASS 遷移の付け替え |
| skill | `.claude/skills/issue-implement/`（SKILL.md、`references/baseline-check.md` 削除） | Step 2.5 を「artifact 確認 + evaluate」へ、Step 4 / 7b を `--compare` へ |
| skill | `issue-review-code` / `issue-fix-code` / `issue-verify-code` / `i-dev-final-check` の SKILL.md | コメント検索・独自 3タプル再パースを artifact + `--compare` 参照へ置換 |
| docs | `docs/dev/baseline-check.md`（新規・正本）ほか影響ドキュメント表参照 | ポリシー一元化 |
| tests | `tests/test_baseline.py`（新規）ほか | テスト戦略参照 |

## 方針

### データフロー

1. `review-design`（または `verify-design`）PASS → harness が `baseline` step を実行。
2. entrypoint が再測定ガードを判定 → 通過時のみ worktree で pytest（内部 plugin report）を
   実行 → `baseline.json` を atomic write → 非 clean なら active provider（GitHub / local）へ
   Issue コメント投稿（local は comment file の atomic commit まで）→ verdict（PASS / ABORT）を
   artifact 経路で返す（順序と失敗時契約は § mode 1 の「出力順序と失敗時の契約」）。
3. `implement` agent は開始時に artifact を Read。`clean` なら従来どおり `make check` 一本。
   `known_failures` なら `--evaluate` で停止判定 → 継続時は Step 7a（非 pytest ゲート）+
   `--compare`（7b 相当）でコミット可否を機械判定。
4. review-code / fix-code / verify-code / final-check は各自の独立検証の pytest 部分で
   `--compare` を使い、コメント有無判定・最新コメント選択・3タプル手動比較を廃止する。

### 正本の一元化

- **判定ロジックの正本**: `kaji_harness/baseline.py`（分類・比較・overlap 評価。ADR 008
  決定 3 の「cross-skill 契約は CLI/harness 層」に従う）。
- **ポリシー散文の正本**: `docs/dev/baseline-check.md`（新規）。停止基準、コミット可否、
  artifact schema、正本選択・stale 規則を 1 箇所に記載し、各 SKILL.md と
  `testing-convention.md` は参照のみ持つ。
  `.claude/skills/issue-implement/references/baseline-check.md` は削除する。

### 正本選択・重複実行・再開の規則

- artifact は固定パス 1 ファイルで、measure の再実行は再測定ガード通過時のみ
  atomic overwrite（§ measure の前提条件と再測定ガード）。**最後に書かれた artifact が
  唯一の正本**であり、複数コメントからの最新選択規則は廃止する（コメントは人間向け証跡に
  格下げ。consumer が読むことを禁止）。ガードの不変条件により、正本は常に「実装 commit を
  含まない clean tree で測定された変更前 baseline」である。
- 実装 commit を含む HEAD・dirty tree での再測定は fail-closed に拒否される
  （reuse または ABORT。再開シナリオ別の挙動は同節の表を正とする）。
- 「未実行」= artifact 不在、「実行済み clean」= `status: clean` の artifact 存在。
  コメント有無では判定しない。
- 再開・resume 時の stale 検出は `--compare` / `--evaluate` が
  `measured_commit` の ancestor 検査で機械判定する（前節）。

### 品質ゲート・review 責務の非劣化（#323 / #341 比較への対応）

- agent から**削除**されるのは、baseline pytest の実行・出力パース・コメント生成・
  最新コメント選択・3タプル手動比較という機械処理のみ。
- review-code / verify-code / final-check が自セッションで品質ゲートを独立実行する責務、
  Pre-Handoff Review、レビュー観点は一切変更しない。#341 で確認された品質水準
  （review-code RETRY 0 / BACK 0、`make check` PASS）の担保構造を維持したまま、
  #324 の狙いである implement セッションの call / context 削減を進める。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 既知 failure の扱い | 現行意味論を維持（対象外かつ切り分け可能なら継続。最終3タプル一致・regression 0・非 pytest ゲート PASS でのみコミット可） | Issue #346 重要判断表（人間決定） | status 4 値（clean / known_failures / blocked / invalid）と `--compare` verdict への機械的写像を定義 |
| 実行経路 | agent を介さない deterministic step。既存 exec / exec_script dispatch を使う | Issue #346 重要判断表（人間決定）。「内部 entrypoint の配置は設計で決定」と委任済み | exec_script skill `baseline-precheck` + `kaji_harness/scripts/baseline_precheck.py`（shim）+ `kaji_harness/baseline.py`（ロジック）。`review-poll` の既存分離パターンを踏襲 |
| 公開契約 | 新しい公開 CLI / workflow schema を追加しない | Issue #346 重要判断表（人間決定） | `kaji baseline` subcommand 案を不採用とし `python -m` 内部 entrypoint に限定 |
| review 責務 | review-code / final-check の独立品質確認を維持 | Issue #346 重要判断表（人間決定） | 各 skill から置換するのは比較・パース手段のみと明記（§ 品質ゲート非劣化） |
| 対象 workflow | `dev.yaml` / `dev-thorough.yaml` / `dev-thorough-fable.yaml` / `dev-local.yaml` の全 4 dev variant | Issue #346 重要判断表（2026-07-16 の人間決定） | 4 本すべてで design review PASS → baseline → implement を配線。local provider のコメント記録も対象化 |
| LLM との責務境界 | 測定・構造化・機械分類・比較は deterministic step、既知 failure と変更内容の意味的関連判断は implement agent | Issue #346 重要判断表（2026-07-16 の人間決定） | `known_failures` の PASS の意味、`--evaluate --scope`、意味判断による追加停止を分離して明記 |
| blocked 閾値 | failure > 10 件で blocked | 現行 `baseline-check.md`「目安: 10件超」（人間承認済みの現行契約） | モジュール定数化。境界（10 / 11）を Small テストで固定 |
| 同一領域停止の実行点 | 判定基準・評価関数は正本に一元化し、scope パスの特定と意味的関連判断は implement agent の入力とする | Issue #346 の LLM 責務境界（人間決定）。根拠: 設計書は散文で決定的パース不能、パス一致だけでは間接依存を判定不能 | `--evaluate --scope` の JSON 契約と overlap 規則（完全一致 + ディレクトリ前方一致）を定義し、agent の追加停止責務を維持 |
| artifact の置き場所と寿命 | `[worktree]/.kaji-artifacts/baseline/baseline.json`、worktree と同寿命 | AI の仮定。根拠: gitignore 済み・consumer 全員が worktree を解決済み・branch スコープの寿命が baseline の意味と一致。検査先: review-design | atomic write、schema_version 付き Pydantic model |
| pytest 結果の取得方法 | 内部 pytest plugin（`-p kaji_harness.pytest_baseline_plugin`）で raw `(nodeid, kind, error_type)` を lossless に収集。JUnit XML は不採用 | AI の仮定。根拠: pytest 9.0.2 `_pytest/junitxml.py` は raw nodeid（`mangle_test_address` で不可逆変換）と exception `type` を保持しない（review-design RETRY 指摘 1 で確定）。検査先: verify-design / review-code | hookwrapper で `excinfo.typename` を取得、collection error を ERROR に写像、JSON report を atomic write。round-trip テストを定義 |
| 再測定の安全性 | 実装 commit を含む HEAD / dirty tree では measure を fail-closed（reuse または ABORT）とし、変更後 baseline の上書きを拒否 | Issue #346「変更前 pytest baseline」・現行 baseline-check.md「実装開始前」（人間決定）の詳細化。review-design RETRY 指摘 2 | 前提条件 2 件（実装 commit 検査・clean tree 検査）、再開シナリオ 5 種の挙動表、measure の不変条件を定義 |
| local コメントの永続化 | `kaji issue comment --commit` と同一契約（`comment_issue()` + `commit_issue_change()` の atomic commit） | 既存 CLI 契約（`commands/issue.py` `--commit` 経路、`test_local_issue_commit_flag.py`）の適用。review-design RETRY 指摘 3 | 出力順序（artifact → コメント → verdict）、失敗時 fail-loud（verdict 未生成で exit 非 0）、retry 重複許容を定義 |
| exit code 分類 | 0 / 1 以外は未知値を含め catch-all で `invalid` / ABORT（fail-closed） | pytest 公式 docs（exit code 6 を定義）+ `pyproject.toml` L32 `pytest>=8.0.0`（上限なし）。review-design RETRY 指摘 4 | stop_reason `unexpected_exit_code:<code>`、exit 6 / 未知値の Small テストを定義 |
| Issue コメント規則 | 非 clean のみ投稿、verdict marker なし、正本は artifact | 現行契約（baseline-check.md「clean はコメントせず」「marker を付けない」）の維持 + AI の詳細化。検査先: review-design | コメントを証跡専用に格下げし consumer の参照を禁止 |

one-way door の未決: なし（公開契約・データ永続 schema・運用手順の不可逆変更を含まない。
artifact は worktree 寿命の内部ファイルで `schema_version` により後方互換なしで進化可能）。

## テスト戦略

### 変更タイプ

実行時コード変更（harness 新モジュール + workflow 配線）+ skill / docs 更新の複合。

### Small テスト（`tests/test_baseline.py`）

- plugin report のパースと kind 写像: call phase failure → FAILED、setup / teardown error・
  collection error → ERROR、`excinfo.typename` が error_type に反映されること
- status 分類: clean / known_failures / blocked の境界（failure 10 件 → known_failures、
  11 件 → blocked）/ invalid の catch-all（exit 6・未知値 42 等・signal 由来の負値が
  `unexpected_exit_code:<code>` になること、exit 0 かつ failure > 0 / exit 1 かつ
  failure 0 件の整合性違反）
- 3タプル比較: 完全一致（matched）/ 新規（regression）/ 解消（resolved）/
  同一 nodeid で error_type が変わったケースは regression 扱い
- overlap 評価（**同一領域 failure シナリオ**）: scope 完全一致 / ディレクトリ前方一致で
  `stop: true`、非重複で `stop: false`
- artifact model: schema roundtrip、必須フィールド欠損・不正値の Pydantic ValidationError
- stale 判定・再測定ガードの判定分岐: ancestor 検査、実装 commit あり + ancestor artifact →
  reuse / artifact なし → ABORT / dirty tree → ABORT（git 呼び出しは
  `testing-convention.md` の patch スコープ表に従い、helper 自身の unit test としてのみ
  `subprocess.run` を mock）

### Medium テスト

- artifact の atomic write / 再読込（tmp ディレクトリの実ファイル I/O）
- `KAJI_VERDICT_PATH` への pure YAML verdict 書き出し
- workflow YAML 検証: 4 variant の `load_workflow` + `validate_workflow` が通ること、
  step graph が `review-design --PASS--> baseline --PASS--> implement` かつ
  `verify-design --PASS--> baseline` であること、`baseline` step が agent を持たないこと
- provider 差分: 非 clean のコメントが provider abstraction を通り、local provider では fixture の
  local issue store に保存されること。GitHub provider は境界 spy で argv / body を検証する
- **local コメントの atomic commit 契約**: fixture の main / feature worktree 構成で
  feature worktree からコメント投稿経路を実行し、comment file の commit が main worktree の
  HEAD に着地し（`git commit --only` 契約）、main / feature 双方の working tree が clean で
  あること
- **再測定ガード（実 git fixture）**: 実装 commit を積んだ fixture repo で measure が
  既存 artifact を reuse して上書きしないこと、artifact 欠損時・非 ancestor 時に ABORT
  すること、dirty tree で ABORT すること

### Large テスト（`@pytest.mark.large` + `@pytest.mark.large_local`）

- fixture ミニプロジェクト（実 git repo + 数件のテスト）に対する entrypoint E2E:
  - **clean**: 全 PASS → `status: clean` / verdict PASS / コメント投稿なし
  - **継続可能な既知 failure**: 少数 failure → `known_failures` / PASS / コメント生成
  - **大量 failure**: 閾値超 → `blocked` / ABORT
  - **最終 regression**: baseline 後に新規失敗を注入して `--compare` → `regression`
  - **再開時の stale baseline**: baseline 後に履歴を書き換え（amend）て `--compare` →
    `stale_baseline`
  - **変更後再測定の拒否**: baseline 後に実装 commit を注入して measure を再実行 →
    既存 artifact が reuse され上書きされないこと。artifact 削除後の再実行 → ABORT
    （`baseline_unrecoverable_post_implement`）
- **plugin round-trip（実 pytest 実行）**: parametrized test（角括弧・引用符を含む nodeid）、
  class 配下の test method、setup / teardown error、collection error（import error）、
  bare assert（`assert False`）を含む fixture テスト群を実行し、report の raw nodeid と
  error_type（`AssertionError` / `TypeError` / `CollectError` 等）が損失なく現行 3 タプルへ
  復元できること

実 pytest subprocess を伴うため large_local（subprocess あり / ネットワーク無し）とする。
Issue コメント投稿は GitHub provider では provider 層境界で spy し、実 GitHub API へは出さない。
local provider は fixture の local issue store へ実際に書き込み、再読込できることを検証する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/dev/baseline-check.md` | あり（新規） | 停止基準・コミット可否・artifact schema・正本選択規則のポリシー正本 |
| `docs/dev/development_workflow.md` | あり | フロー図・フェーズ表への `baseline` step 追加、Pre-Handoff 節の baseline 記述更新 |
| `docs/dev/implement-quickref.md` | あり | 「状況 → 正本」表の `references/baseline-check.md` 行を新正本へ差し替え |
| `docs/dev/testing-convention.md` | あり | テスト実行マトリクス「baseline failure がある場合」の参照先を新正本へ |
| `docs/dev/workflow_guide.md` | あり | dev workflow の step 一覧・説明に `baseline` を追記 |
| `docs/dev/workflow_completion_criteria.md` | あり | implement / final-check の証跡責務に artifact 参照を反映 |
| `docs/dev/workflow-authoring.md` | なし | schema 変更なし（既存 exec_script 仕様のまま） |
| `docs/dev/skill-authoring.md` | なし | exec_script 仕様は不変 |
| `docs/adr/` | なし | 新しい技術選定なし（既存 exec_script 機構 + pytest 標準の plugin hook API。外部ライブラリ追加なし。cross-skill 契約を harness 層に置く方針は ADR 008 で決定済み） |
| `docs/ARCHITECTURE.md` | 要確認 | `kaji_harness/baseline.py` 追加がモジュール一覧に載る構成なら追記 |
| `AGENTS.md` / `CLAUDE.md` | なし | 常時適用ルール・人間起動 skill 表に変更なし（baseline は harness 内部 step） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠(引用/要約) |
|--------|----------|-------------------|
| Issue #346 本文 | GitHub #346 | スコープ境界・完了条件・重要判断表（人間決定の正本） |
| Epic #324 | GitHub #324 | 「baseline check の exec step 化」方針、品質非劣化の到達状態 |
| workflow 運用ガイド | `docs/dev/workflow_guide.md` | `dev-local.yaml` を local provider 用の正式な dev workflow と定義 |
| local dev workflow | `.kaji/wf/dev-local.yaml` | `review-design` / `verify-design` の PASS から共有 `issue-implement` へ遷移する現行 graph |
| 現行 baseline 手順 | `.claude/skills/issue-implement/references/baseline-check.md` | 「(nodeid, kind, error_type) を比較キー」「目安: 10件超」「最新コメントを正」— 維持すべき現行意味論の正本 |
| implement 手順 | `.claude/skills/issue-implement/SKILL.md` Step 2.5 / 7 | baseline 分岐時の 7a / 7b 分離実行、コミット可否条件 |
| exec-step / exec_script 契約 | `docs/dev/workflow-authoring.md` § exec-step | 「verdict 解決: artifact → comment → stdout。AI formatter fallback を呼ばない」「exit code != 0 は ScriptExecutionError」 |
| exec_script skill 仕様 | `docs/dev/skill-authoring.md` § exec_script | frontmatter 契約、KAJI_* env 一覧、`KAJI_VERDICT_PATH` artifact-primary |
| dispatch 実装 | `kaji_harness/script_exec.py` | `sys.executable -m <module>`、`shell=False`、fail-loud 契約 |
| 先行 deterministic skill | `kaji_harness/scripts/review_poll_entry.py` | 「ABORT verdict を stdout に emit して return 0」パターン、env 検証の作法 |
| harness env 注入 | `kaji_harness/runner.py` `_build_context_env` | 注入される KAJI_* の全集合（KAJI_WORKTREE_DIR / KAJI_VERDICT_PATH 等） |
| pytest exit codes | https://docs.pytest.org/en/stable/reference/exit-codes.html | 0=全 PASS、1=テスト失敗、2=中断、3=内部エラー、4=usage error、5=収集 0 件。最新 stable は 6（warnings 上限超過）も定義 → catch-all 分類の根拠 |
| installed pytest の ExitCode | `.venv/lib/python*/site-packages/_pytest/config/__init__.py`（pytest 9.0.2） | `ExitCode` enum は 0〜5 のみ（実測）。docs との差が未知 exit code 到達の実例 → fail-closed の根拠 |
| pytest junitxml docs + 実装 | https://docs.pytest.org/en/stable/how-to/output.html#creating-junitxml-format-files / `.venv/lib/python*/site-packages/_pytest/junitxml.py`（9.0.2） | `record_testreport` は xunit2 testcase に `classname` / `name` のみ設定（raw nodeid は `mangle_test_address` で不可逆変換）、`_add_simple` は failure / error に message のみで `type` 属性なし → JUnit XML 不採用の根拠 |
| pytest hook reference | https://docs.pytest.org/en/stable/reference/reference.html#hooks | `pytest_runtest_makereport`（hookwrapper で `call.excinfo` にアクセス可）/ `pytest_runtest_logreport` / `pytest_collectreport` — 内部 plugin の収集点 |
| local provider comment 契約 | `kaji_harness/providers/local.py` / `kaji_harness/commands/issue.py` / `tests/test_local_issue_commit_flag.py` | `comment_issue()` は comment file を書くのみ。`--commit` 経路だけが `commit_issue_change()`（`git commit --only`）で atomic commit する — baseline コメント投稿契約の正本 |
| pytest 依存宣言 | `pyproject.toml` L32 | `pytest>=8.0.0`（上限なし）→ lock 更新で未知 exit code に到達しうる（fail-closed 分類の根拠） |
| ADR 008 | `docs/adr/008-no-backward-compat-layer.md` | 「cross-skill 契約は SKILL.md 散文ではなく CLI/harness 層に置く」（決定 3）。後方互換 fallback を残さない方針 |
| 品質比較 artifact | #323 run `260714213832` / #341 run `260716004739` | 施策前後の calls / output / 品質指標（review-code RETRY・BACK 0 件、make check PASS） |
| worktree venv 構成 | `.claude/skills/issue-start/SKILL.md` Step 2.5 | worktree `.venv` は main `.venv` への symlink（`kaji_harness` importable の根拠） |
