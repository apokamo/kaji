# [設計] Baseline Check の deterministic exec step 化と既知 failure ポリシー一元化

Issue: #346

## 概要

`.kaji/wf/dev*.yaml` の設計承認後・`implement` 前に、変更前 pytest baseline を agent を介さず
取得・判定する deterministic precheck step（exec_script skill `baseline-precheck`）を追加する。
現在 5 つの skill に分散している既知 failure の停止基準・3タプル比較・コミット可否判定を、
単一の構造化 artifact と正本ポリシー（`docs/dev/baseline-check.md`）へ一元化する。

## 背景・目的

### ユーザーストーリー

- **workflow 運用者**として、実装開始前の baseline 取得・停止判定を決定論 step で一度だけ
  実行し、後段の implement / review / fix / verify / final-check が同じ構造化結果を参照できる
  ようにしたい。
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

- **`exec:` step で argv を直接書く**: 3 つの workflow variant に同一 argv が重複し、
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
- workflow step 宣言（`dev.yaml` / `dev-thorough.yaml` / `dev-thorough-fable.yaml` 共通）:

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
  これにより「設計承認後・implement 前に 1 回だけ」実行され、implement のリトライ・
  BACK_IMPLEMENT では再実行されない。`BACK: design` / `BACK_DESIGN: design` で design へ
  戻った場合は design 承認後に再実行され、baseline が再測定される（stale 防止）。

### 2. 内部 entrypoint: `kaji_harness/scripts/baseline_precheck.py`

3 モードを持つ。ロジック本体は `kaji_harness/baseline.py`（純粋ロジック、Small テスト対象）に
置き、scripts 側は env/argv shim とする（`codex_review_poll` / `review_poll_entry` と同じ分離）。

#### 入力（共通）

| 入力 | 形式 | 必須 | 説明 |
|------|------|------|------|
| `KAJI_WORKTREE_DIR` | env（`--worktree DIR` で override 可） | ✅ | 測定対象 worktree の絶対パス |
| `KAJI_ISSUE_ID` | env（`--issue ID` で override 可） | measure 時 ✅ | Issue コメント投稿先 |
| `KAJI_VERDICT_PATH` | env | 任意 | あれば pure YAML verdict を保存（exec_script 経路で harness が注入） |

pytest は `[worktree_dir]/.venv/bin/python -m pytest --junitxml=<artifact_dir>/junit-<mode>.xml`
として subprocess 実行する（entrypoint 自身の interpreter に依存しない）。
`shell=False`、cwd は worktree。

#### mode 1: measure（デフォルト。workflow step として実行）

- **出力 1 — 構造化 artifact（正本）**:
  `[worktree_dir]/.kaji-artifacts/baseline/baseline.json` へ atomic write（tmp + `os.replace`）。
  `.kaji-artifacts/` は gitignore 済みのため git status を汚さず、worktree 削除とともに
  自然消滅する（branch スコープの寿命）。
- **出力 2 — Issue コメント**: `status != clean` の場合のみ、artifact から決定的に生成した
  `## Baseline Check 結果` コメントを provider 層（`comment_issue`）経由で投稿する。
  現行規約どおり verdict marker は付けない（判定コメントではなく証跡コメント）。
- **出力 3 — verdict**: `KAJI_VERDICT_PATH` への pure YAML と stdout の
  `---VERDICT---` block。exec_script 経路は AI formatter fallback を呼ばない既存契約に乗る。

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
| pytest exit 0（FAILED / ERROR 0 件） | `clean` | — | PASS |
| exit 1 かつ failure 件数 1〜10 | `known_failures` | — | PASS |
| exit 1 かつ failure 件数 > 10 | `blocked` | `mass_failures` | ABORT |
| exit 2 / 3 / 4 / 5、junitxml 欠損・パース不能、exit 1 なのに failure 0 件、`.venv` の pytest 起動不能 | `invalid` | 事由文字列 | ABORT |

閾値 10 は現行 `baseline-check.md`「目安: 10件超」の定数化
（`kaji_harness/baseline.py` のモジュール定数）。pytest exit code の意味は公式
リファレンス（Primary Sources 参照）に従う。exit 5（no tests collected）は baseline を
定義できないため `invalid` とする。

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
  suggestion に含めて停止」とする。silent fallthrough はしない。

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
| harness | `kaji_harness/baseline.py`（新規） | junitxml パース、status 分類、3タプル比較、overlap 評価、artifact model（Pydantic） |
| harness | `kaji_harness/scripts/baseline_precheck.py`（新規） | env/argv shim、pytest subprocess、verdict / コメント出力 |
| skill | `.claude/skills/baseline-precheck/SKILL.md`（新規） | exec_script frontmatter + 単体起動契約 |
| workflow | `.kaji/wf/dev.yaml` / `dev-thorough.yaml` / `dev-thorough-fable.yaml` | `baseline` step 挿入、design 承認 PASS 遷移の付け替え |
| skill | `.claude/skills/issue-implement/`（SKILL.md、`references/baseline-check.md` 削除） | Step 2.5 を「artifact 確認 + evaluate」へ、Step 4 / 7b を `--compare` へ |
| skill | `issue-review-code` / `issue-fix-code` / `issue-verify-code` / `i-dev-final-check` の SKILL.md | コメント検索・独自 3タプル再パースを artifact + `--compare` 参照へ置換 |
| docs | `docs/dev/baseline-check.md`（新規・正本）ほか影響ドキュメント表参照 | ポリシー一元化 |
| tests | `tests/test_baseline.py`（新規）ほか | テスト戦略参照 |

## 方針

### データフロー

1. `review-design`（または `verify-design`）PASS → harness が `baseline` step を実行。
2. entrypoint が worktree で pytest（junitxml）を実行 → `baseline.json` を atomic write →
   非 clean なら Issue コメント投稿 → verdict（PASS / ABORT）を artifact 経路で返す。
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

- artifact は固定パス 1 ファイルで、measure の再実行は atomic overwrite。
  **最後に書かれた artifact が唯一の正本**であり、複数コメントからの最新選択規則は廃止する
  （コメントは人間向け証跡に格下げ。consumer が読むことを禁止）。
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
| blocked 閾値 | failure > 10 件で blocked | 現行 `baseline-check.md`「目安: 10件超」（人間承認済みの現行契約） | モジュール定数化。境界（10 / 11）を Small テストで固定 |
| 同一領域停止の実行点 | 判定基準・評価関数は正本に一元化し、scope パスの特定は implement agent の入力とする | AI の仮定。根拠: 設計書は散文で決定的パース不能、`issue-design` は対象領域外、現行も agent 判断であり意味論維持になる。検査先: review-design | `--evaluate --scope` の JSON 契約と overlap 規則（完全一致 + ディレクトリ前方一致）を定義 |
| artifact の置き場所と寿命 | `[worktree]/.kaji-artifacts/baseline/baseline.json`、worktree と同寿命 | AI の仮定。根拠: gitignore 済み・consumer 全員が worktree を解決済み・branch スコープの寿命が baseline の意味と一致。検査先: review-design | atomic write、schema_version 付き Pydantic model |
| pytest 結果の取得方法 | `--junitxml` を正とし text 出力をパースしない | AI の仮定。根拠: junitxml は pytest 標準の機械可読出力で、summary 行の text パースより安定。検査先: review-design | kind（FAILED / ERROR）と error_type（message 先頭の例外クラス名）の抽出規則を定義 |
| Issue コメント規則 | 非 clean のみ投稿、verdict marker なし、正本は artifact | 現行契約（baseline-check.md「clean はコメントせず」「marker を付けない」）の維持 + AI の詳細化。検査先: review-design | コメントを証跡専用に格下げし consumer の参照を禁止 |

one-way door の未決: なし（公開契約・データ永続 schema・運用手順の不可逆変更を含まない。
artifact は worktree 寿命の内部ファイルで `schema_version` により後方互換なしで進化可能）。

## テスト戦略

### 変更タイプ

実行時コード変更（harness 新モジュール + workflow 配線）+ skill / docs 更新の複合。

### Small テスト（`tests/test_baseline.py`）

- junitxml パース: FAILED / ERROR の kind 分類、error_type 抽出（`AssertionError: ...` /
  型情報なし message / 空 message のフォールバック）
- status 分類: clean / known_failures / blocked の境界（failure 10 件 → known_failures、
  11 件 → blocked）/ invalid（exit 2・3・4・5、exit 1 かつ failure 0 件の不整合）
- 3タプル比較: 完全一致（matched）/ 新規（regression）/ 解消（resolved）/
  同一 nodeid で error_type が変わったケースは regression 扱い
- overlap 評価（**同一領域 failure シナリオ**）: scope 完全一致 / ディレクトリ前方一致で
  `stop: true`、非重複で `stop: false`
- artifact model: schema roundtrip、必須フィールド欠損・不正値の Pydantic ValidationError
- stale 判定ロジック: ancestor 検査の分岐（git 呼び出しは `testing-convention.md` の
  patch スコープ表に従い、helper 自身の unit test としてのみ `subprocess.run` を mock）

### Medium テスト

- artifact の atomic write / 再読込（tmp ディレクトリの実ファイル I/O）
- `KAJI_VERDICT_PATH` への pure YAML verdict 書き出し
- workflow YAML 検証: 3 variant の `load_workflow` + `validate_workflow` が通ること、
  step graph が `review-design --PASS--> baseline --PASS--> implement` かつ
  `verify-design --PASS--> baseline` であること、`baseline` step が agent を持たないこと

### Large テスト（`@pytest.mark.large` + `@pytest.mark.large_local`）

- fixture ミニプロジェクト（実 git repo + 数件のテスト）に対する entrypoint E2E:
  - **clean**: 全 PASS → `status: clean` / verdict PASS / コメント投稿なし
  - **継続可能な既知 failure**: 少数 failure → `known_failures` / PASS / コメント生成
  - **大量 failure**: 閾値超 → `blocked` / ABORT
  - **最終 regression**: baseline 後に新規失敗を注入して `--compare` → `regression`
  - **再開時の stale baseline**: baseline 後に履歴を書き換え（amend）て `--compare` →
    `stale_baseline`

実 pytest subprocess を伴うため large_local（subprocess あり / ネットワーク無し）とする。
Issue コメント投稿は provider 層境界で spy し、実 GitHub API へは出さない。

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
| `docs/adr/` | なし | 新しい技術選定なし（既存 exec_script 機構 + pytest 標準 junitxml。cross-skill 契約を harness 層に置く方針は ADR 008 で決定済み） |
| `docs/ARCHITECTURE.md` | 要確認 | `kaji_harness/baseline.py` 追加がモジュール一覧に載る構成なら追記 |
| `AGENTS.md` / `CLAUDE.md` | なし | 常時適用ルール・人間起動 skill 表に変更なし（baseline は harness 内部 step） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠(引用/要約) |
|--------|----------|-------------------|
| Issue #346 本文 | GitHub #346 | スコープ境界・完了条件・重要判断表（人間決定の正本） |
| Epic #324 | GitHub #324 | 「baseline check の exec step 化」方針、品質非劣化の到達状態 |
| 現行 baseline 手順 | `.claude/skills/issue-implement/references/baseline-check.md` | 「(nodeid, kind, error_type) を比較キー」「目安: 10件超」「最新コメントを正」— 維持すべき現行意味論の正本 |
| implement 手順 | `.claude/skills/issue-implement/SKILL.md` Step 2.5 / 7 | baseline 分岐時の 7a / 7b 分離実行、コミット可否条件 |
| exec-step / exec_script 契約 | `docs/dev/workflow-authoring.md` § exec-step | 「verdict 解決: artifact → comment → stdout。AI formatter fallback を呼ばない」「exit code != 0 は ScriptExecutionError」 |
| exec_script skill 仕様 | `docs/dev/skill-authoring.md` § exec_script | frontmatter 契約、KAJI_* env 一覧、`KAJI_VERDICT_PATH` artifact-primary |
| dispatch 実装 | `kaji_harness/script_exec.py` | `sys.executable -m <module>`、`shell=False`、fail-loud 契約 |
| 先行 deterministic skill | `kaji_harness/scripts/review_poll_entry.py` | 「ABORT verdict を stdout に emit して return 0」パターン、env 検証の作法 |
| harness env 注入 | `kaji_harness/runner.py` `_build_context_env` | 注入される KAJI_* の全集合（KAJI_WORKTREE_DIR / KAJI_VERDICT_PATH 等） |
| pytest exit codes | https://docs.pytest.org/en/stable/reference/exit-codes.html | 0=全 PASS、1=テスト失敗、2=中断、3=内部エラー、4=usage error、5=収集 0 件 |
| pytest junitxml | https://docs.pytest.org/en/stable/how-to/output.html#creating-junitxml-format-files | `--junitxml` は標準の機械可読レポート出力 |
| ADR 008 | `docs/adr/008-no-backward-compat-layer.md` | 「cross-skill 契約は SKILL.md 散文ではなく CLI/harness 層に置く」（決定 3）。後方互換 fallback を残さない方針 |
| 品質比較 artifact | #323 run `260714213832` / #341 run `260716004739` | 施策前後の calls / output / 品質指標（review-code RETRY・BACK 0 件、make check PASS） |
| worktree venv 構成 | `.claude/skills/issue-start/SKILL.md` Step 2.5 | worktree `.venv` は main `.venv` への symlink（`kaji_harness` importable の根拠） |
