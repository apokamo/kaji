# [設計] inject_verdict 廃止予告 warning と予告リリース

Issue: #381

## 概要

workflow step の `inject_verdict` に対し、workflow file 単位で集約した deprecation warning を
`kaji validate` / `kaji run` の事前検証（preflight）から stderr に **1 行** 出力する。
`inject_verdict` の受理と `previous_verdict` 注入の挙動は一切変更せず、docs / CHANGELOG に
廃止予定・移行方法・影響判定方法を記載して予告 release へ引き渡す。

## 背景・目的

### ユースケース

下流 repository（`kamo2` / `tsuchi` / starter 系の 34 workflow file・105 指定）の workflow 保守者
として、`inject_verdict` が engine から削除される前に **どの file のどの step が対象か** と
**どう書き換えるか** を把握するために、日常的に実行する `kaji validate` / `kaji run` の時点で
警告を受け取りたい。

### 現状の問題

- parser は `inject_verdict` を通常の step field として受理するため（`kaji_harness/workflow.py:202`）、
  利用者は将来の削除を実行前に認識できない。
- parser は未知 step key を包括的に拒否しないため、警告期間なしに削除すると旧 YAML は
  silent ignore され、`previous_verdict` 注入だけが消える silent regression になる（#310 現状ベースライン）。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| parse 境界（`_parse_workflow`）で stderr 出力 | parse を純粋な変換から副作用付きへ変える。`load_workflow` を呼ぶすべての経路（tests / `kaji recover` / 将来の consumer）が無条件に出力し、集約単位が「file」ではなく「load 呼び出し回数」に依存する |
| L1 validation error にする | #310 決定 2「まず warning を追加し、挙動を維持した廃止予告 release」に反する。削除は #383 の責務 |
| `warnings.warn(DeprecationWarning)` | Python の既定フィルタでは `__main__` 以外で非表示。完了条件「stderr に warning を表示」を実行環境に依存せず満たせない |
| step ごとに 1 行出力 | Issue 完了条件「file 内の指定件数にかかわらず stderr の warning は 1 行のみ」に反する |

採用案は preflight (L2/L3) 層の既存 `WorkflowPreflightResult.warnings`（"Non-fatal compatibility
warnings"、`kaji_harness/preflight.py:31`）に載せる方式。run 経路は既に
`kaji_harness/runner.py:810-811` で warnings を stderr へ出力しており、warning の出力先・集約単位
（preflight 1 回 = workflow 1 つ）が既存契約と一致する。

## インターフェース

### 入力

- **CLI 引数**: 変更なし。新規オプションを追加しない。
- **workflow YAML schema**: 変更なし。`inject_verdict: bool`（default `false`）を引き続き受理する。
- warning の判定入力は `Workflow.steps` の `Step.inject_verdict is True` のみ。値が `false` の
  明示指定は warning 対象としない（挙動に影響せず、削除時も silent regression を生まないため）。

### 出力

| 経路 | 出力 | 変更点 |
|------|------|--------|
| `preflight_workflow()` | `WorkflowPreflightResult.warnings` の **先頭** に deprecation warning を最大 1 件追加 | 新規 |
| `kaji run`（runner の run 前 preflight） | 既存経路（`runner.py:810`）が stderr へ 1 行出力 | コード変更なし |
| `kaji validate` | preflight の warnings を stderr へ `⚠ {path}: {warning}` 形式で出力 | 新規（現状 warnings は破棄されている） |

- **exit code**: 不変。warning のみなら `kaji validate` は `EXIT_OK`(0)、`kaji run` は実行を継続する。
- **stdout**: 不変（`✓ {path}` のまま）。機械可読な stdout 契約を汚さない。
- **副作用**: なし（file 書き込み・network なし）。

warning 文言（**改行を含まない 1 行**、既存 warning と同じく英語 / `WARNING: ` prefix）:

```text
WARNING: 'inject_verdict' is deprecated and will be removed in the next minor release
(apokamo/kaji#310); step(s) fix-design, fix-code: remove the field and use
'resume: <step-id>' for same-agent session continuation, or read the prior verdict with
'kaji issue resolve-verdict <issue-id> --step <step-id>'.
```

（上記は紙面の都合で折り返しているが、実出力は 1 行。）Issue 完了条件の 4 要素を満たす:
`inject_verdict` の識別 / 削除予定（the next minor release）/ 移行方法（`resume` または
`kaji issue resolve-verdict`）/ `apokamo/kaji#310`。加えて対象 step ID を列挙し、1 行のまま
file 内の全該当箇所を特定できるようにする。

### 使用例

```console
$ kaji validate .kaji/wf/custom/dev-opus.yaml
⚠ .kaji/wf/custom/dev-opus.yaml: WARNING: 'inject_verdict' is deprecated and will be removed in the next minor release (apokamo/kaji#310); step(s) fix-design, fix-code: ...
✓ .kaji/wf/custom/dev-opus.yaml
$ echo $?
0
```

```python
# 内部 API（preflight の呼び出し側から見た契約）
result = preflight_workflow(workflow, project_root=root, skill_dir=".claude/skills")
assert result.errors == []
assert len(result.warnings) == 1          # inject_verdict が 2 件でも 1 件
assert "inject_verdict" in result.warnings[0]
```

### エラー

| 入力 | 挙動 | 変更 |
|------|------|------|
| `inject_verdict: "yes"`（非 bool） | L1 `WorkflowValidationError`（`workflow.py:203`）。warning は出ない（parse で落ちるため preflight に到達しない） | なし |
| exec-step + `inject_verdict` | L1 error（`_EXEC_FORBIDDEN_KEYS`）。同上 | なし |
| `inject_verdict` を含む workflow が他の L2/L3 error も持つ | error（exit 1）と warning が両方 stderr に出る。error による exit code が優先 | 新規（warning 行が増える） |
| `kaji validate` が `.kaji/config.toml` を発見できない | preflight に到達しないため warning は出ない。既存どおり config error で exit 1 | なし（既知の境界。後述） |

## 制約・前提条件

- **挙動維持**: `kaji_harness/prompt.py:75-80` の `(step.resume or step.inject_verdict)` 条件、
  `Step.inject_verdict` field、parse / exec 排他検証を変更しない（#310 決定 2・Issue #381 スコープ境界）。
- **1 行制約**: warning 文字列に改行を含めない。Issue 完了条件のテストが stderr 行数 1 を検証する。
- **version 非ハードコード**: 文言に `v0.18.0` 等を埋め込まない（#310「version 番号は release 時点の
  version 正本に従う」）。予告 release を実行中の利用者から見て "the next minor release" が
  完全削除 release（#383）を正しく指す。
- **ADR 008**: 後方互換レイヤではない。warning は互換コードではなく予告であり、削除は #383 で
  BREAKING として実施する。
- **依存追加なし**: 標準ライブラリ + 既存 module のみ。
- **層方向**: `preflight.py` は `models` / `workflow` / `skill` にのみ依存する現状を維持する
  （`tests/test_layer_imports.py` / ADR 009）。
- kaji 本体の `.kaji/wf/**` に `inject_verdict` は 0 件（`commit 6f9b4e6` で削除済み）であり、
  `make validate-workflows` / 自 repo の run に新しい stderr noise は発生しない。

## 変更スコープ

| 対象 | 変更 |
|------|------|
| `kaji_harness/preflight.py` | 非公開 helper と定数を追加し、`preflight_workflow()` の warnings 初期化を差し替える |
| `kaji_harness/commands/validate.py` | preflight の warnings を stderr へ出力する `_print_warnings()` を追加し `cmd_validate` から呼ぶ |
| `tests/test_workflow_preflight.py` | Small（集約・文言・順序・0 件） |
| `tests/test_cli_validate.py` | Medium（stderr 1 行の fixture 検証）/ Large（実 CLI subprocess） |
| `tests/test_runner.py`（既存 file に追加） | Medium（run 事前検証経路の stderr 1 行） |
| `docs/dev/workflow-authoring.md` | `inject_verdict` の非推奨記載と移行方法 |
| `CHANGELOG.md` | `## [Unreleased]` に `### Deprecated` エントリ |

**非対象（変更しない）**: `kaji_harness/workflow.py` / `models.py` / `prompt.py` / `runner.py` /
`commands/run.py`。既存 workflow YAML、`docs/adr/011`・`docs/reference/python/type-hints.md` の
例示記述（過去の決定記録・型記法の例であり runtime 契約ではない。#383 の削除時に扱う）。

## 方針

### 1. preflight での集約（`kaji_harness/preflight.py`）

```python
_INJECT_VERDICT_DEPRECATION = (
    "WARNING: 'inject_verdict' is deprecated and will be removed in the next minor "
    "release (apokamo/kaji#310); step(s) {steps}: remove the field and use "
    "'resume: <step-id>' for same-agent session continuation, or read the prior "
    "verdict with 'kaji issue resolve-verdict <issue-id> --step <step-id>'."
)


def _deprecated_field_warnings(workflow: Workflow) -> list[str]:
    """廃止予定 field の警告を workflow 単位で 1 件に集約する。"""
    step_ids = [step.id for step in workflow.steps if step.inject_verdict]
    if not step_ids:
        return []
    return [_INJECT_VERDICT_DEPRECATION.format(steps=", ".join(step_ids))]


def preflight_workflow(...) -> WorkflowPreflightResult:
    ...
    warnings: list[str] = _deprecated_field_warnings(workflow)   # 既存は `= []`
    ...                                                          # 以降の append は不変
```

- 集約単位は「`preflight_workflow()` 1 回 = workflow file 1 つ」。step 走査は 1 回、
  出力は最大 1 件。step ID は YAML 出現順（決定的）。
- workflow レベルの警告なので step レベル警告（exec_script）より前に置く。既存
  `result.warnings == [...]` の完全一致テストは `inject_verdict` を含まないため影響しない。
- helper は非公開にする。#383 で field ごと削除する一時的な仕組みであり、公開 API を増やすと
  削除時の BREAKING 面が広がるため。テストは公開契約
  （`WorkflowPreflightResult.warnings`）越しに行う。

### 2. `kaji validate` での出力（`kaji_harness/commands/validate.py`）

```python
result = preflight_workflow(...)
_print_warnings(path, result.warnings)   # errors 判定より前。error 時も warning を残す
if result.errors:
    _print_error(path, result.errors)
    failed += 1
    continue
_print_success(path)


def _print_warnings(path: Path, warnings: list[str]) -> None:
    """Print non-fatal warnings to stderr, one line each."""
    for warning in warnings:
        print(f"⚠ {path}: {warning}", file=sys.stderr)
```

- `✓` / `✗` と同じ記号語彙に `⚠` を追加する。複数 file を 1 コマンドで検証しても
  行内に path を含むため対応関係が一意で、かつ 1 warning = 1 行を保てる（`⚠ {path}` の
  ヘッダ行 + bullet の 2 行形式は完了条件の「1 行」を壊すため採らない）。
- 副作用として既存の exec_script warning も `kaji validate` で可視化される。現状 validate は
  preflight の warnings を破棄しており、これは warning 機構の取りこぼしである。

### 3. `kaji run` 経路

コード変更なし。`WorkflowRunner.run()` → `_collect_skill_metadata()`（run あたり 1 回）が
`preflight_workflow()` を呼び、`runner.py:810-811` が warnings を stderr へ書く。

**カバレッジ境界（記録）**: `kaji recover` の親プロセス（`commands/recover.py:63`）は
`load_workflow` のみで preflight を通らないため warning を出さない。実行は子 `kaji run` が
行い、そこで 1 行出力される。Issue の完了条件は `kaji validate` / `kaji run` を対象としており、
この境界は仕様の欠落ではない。

### 4. docs / CHANGELOG

- `docs/dev/workflow-authoring.md`
  - 「ステップフィールド」表に `inject_verdict` 行を追加し **⚠️ 非推奨** と削除予定を明示する
    （現状この表に `inject_verdict` の記載自体がなく、非推奨を書く場所が存在しない）。
  - 「resume（セッション継続）」節の直後に `### inject_verdict（非推奨）` を追加し、
    廃止理由・削除時期・移行手順（`resume:` / `kaji issue resolve-verdict`）・検出コマンドを書く。
- `CHANGELOG.md` の `## [Unreleased]` に `### Deprecated`（Keep a Changelog 1.1.0 の区分）を追加し、
  ADR 008 の BREAKING 3 要素を先行案内する:
  - **壊れる契約**: 次々回 minor（#383）で `inject_verdict` が削除され、指定した step の
    `previous_verdict` 注入が失われる（削除後は明示的な migration error になる）。
  - **影響の判定方法**: `rg -n 'inject_verdict' .kaji/wf/`（0 件なら影響なし）。
  - **適用指針**: `resume: <step-id>`（同一 agent のセッション継続）へ置換する。cross-agent 等で
    `resume` が使えない step は skill 側で `kaji issue resolve-verdict <issue-id> --step <step-id>`
    により直前 verdict を取得する。参照: #310 / #381。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 廃止方式 | warning 予告 release → 14 日以上 → 完全削除の 3 段階 | #310 本文「決定事項」1〜5（2026-07-25 の人間決定） | 本設計は第 1 段階のみを扱い、削除・migration error は #383 に残す |
| 予告段階の互換性 | `inject_verdict` の受理と `previous_verdict` 注入を維持 | #310 決定 2 / Issue #381「スコープ境界」（人間決定） | `workflow.py` / `models.py` / `prompt.py` を変更対象から除外し、既存の `tests/test_prompt_builder.py` §11〜14 と `tests/test_skill_harness_adaptation.py` を挙動維持の回帰砦として据え置く |
| warning の集約単位 | workflow file 単位・1 行 | Issue #381 完了条件（人間決定。readiness review の指摘 1 で定量化済み） | 「preflight 1 回 = workflow 1 つ」へ写像し、step ID 列挙で 1 行のまま全該当箇所を可視化 |
| warning の実装配置 | L1 parse ではなく preflight (L2/L3) の `warnings` に載せる | AI の仮定。根拠: `WorkflowPreflightResult.warnings` が "Non-fatal compatibility warnings" として既存（`preflight.py:31`）で、run 経路は既に stderr 出力済み（`runner.py:810`）。配置替えは局所的で後段で安く直せる two-way door。検査先: review-design / review-code | 非公開 helper `_deprecated_field_warnings()` として実装し、公開 API を増やさない |
| `kaji validate` が warnings を出力していない現状 | validate でも stderr に出力する | AI の仮定。根拠: Issue 完了条件「`kaji validate` が stderr に warning を表示」を満たすには必須。既存 exec_script warning も可視化される副作用がある。検査先: review-code（既存 validate テストの stderr 前提が壊れないこと） | 書式 `⚠ {path}: {warning}`。error 判定より前に出力し、error 併発時も警告が残る |
| warning 文言の version 表記 | "the next minor release"（番号を書かない） | #310「version 番号は release 時点の version 正本に従う」（人間決定） | 予告 release 利用者視点で次の minor = 完全削除 release を正しく指すことを確認済み |
| 移行方法の内容 | `resume:` を第一手段、cross-agent 等は `kaji issue resolve-verdict` | #310「維持: `resume` 経由の `previous_verdict` 注入」および判断履歴 2026-07-16（`73c7abf` で resolver 追加）（人間決定 + 既存契約） | 1 行に収まる語順へ圧縮し、docs / CHANGELOG に詳細を置く |
| 出力書式 `⚠ {path}: {warning}` | 1 行に path を含める | AI の仮定。根拠: 既存の `✓` / `✗` 記号語彙との一貫性。ヘッダ + bullet の 2 行形式は完了条件「1 行」を壊す。検査先: review-code | 複数 file 検証時の対応付けを行内で解決 |
| 予告 release の version | release 時点の正本に従う（想定 v0.18.0） | #310「release 計画」（人間決定） | 本 Issue では CHANGELOG `## [Unreleased]` までを成果物とし、version 確定は `/release` に委ねる |

未決の one-way door はない。公開契約に触れる判断（stderr 出力の追加・文言）はいずれも #310 /
#381 の人間決定の範囲内で、CHANGELOG での伝達手段（ADR 008）が用意されている。

## テスト戦略

### 変更タイプ

実行時コード変更（preflight の戻り値と `kaji validate` の stderr 出力が変わる）+ docs 変更。

### 実行時コード変更の場合

#### Small テスト

`tests/test_workflow_preflight.py` に追加。`Workflow` / `Step` を直接構築し、skill 検証は既存の
注入 seam（`skill_exists_validator` / `skill_metadata_loader`）で差し替えて I/O なしで検証する。

- **集約**: `inject_verdict: true` の step が 2 件ある workflow → `warnings` の deprecation 件数は 1。
- **文言の必須要素**: 生成文字列に `inject_verdict` / `next minor release` / `apokamo/kaji#310` /
  `resume` / `kaji issue resolve-verdict` が含まれる（完了条件 3 の観点を機械検証する）。
- **1 行性**: 生成文字列が `\n` を含まない。
- **対象の特定**: 該当 step ID が両方とも文字列に含まれ、非該当 step ID は含まれない。
- **境界**: 該当 0 件（未指定 / `false` 明示）→ deprecation warning なし（既存 `warnings == []` 契約の維持）。
- **順序**: exec_script warning と併存する場合、deprecation warning が先頭。

#### Medium テスト

実 file I/O と CLI 関数を通す。

- `tests/test_cli_validate.py`: `inject_verdict: true` を **2 件** 含む有効な workflow fixture を
  tmp_path に書き `cmd_validate` を実行 → exit 0、`capsys` の **stderr 行数がちょうど 1**、
  その 1 行に path と `inject_verdict` を含む、stdout に `✓`（Issue 完了条件 4・5 の直接検証）。
- `tests/test_cli_validate.py`: `inject_verdict` を含まない workflow → stderr が空（回帰防止）。
- `tests/test_runner.py`: 同 fixture で `WorkflowRunner` を構築し `_collect_skill_metadata()` を
  実行 → stderr 行数がちょうど 1（`kaji run` の事前検証経路の検証。完了条件 2）。
- **挙動維持**: 既存 `tests/test_prompt_builder.py` §11〜14（`inject_verdict=True` で
  `previous_verdict` が注入され、`False` では注入されない）と
  `tests/test_skill_harness_adaptation.py`（fixture YAML の parse 結果）を **無変更で通す**。
  これらは本変更が挙動を変えていないことの証跡であり、修正が必要になった時点で設計違反を意味する。

#### Large テスト

- `tests/test_cli_validate.py` に `@pytest.mark.large` / `@pytest.mark.large_local`（subprocess あり /
  network なし）で 1 本追加。`python -m kaji_harness.cli_main validate <fixture>` を実行し、
  returncode 0 かつ stderr の行数が 1 であることを検証する。
  **恒久化する理由**: 本 Issue の成果物は「下流利用者が実 CLI の stderr で警告を受け取れること」
  という外部契約であり、in-process 呼び出しでは entry point 経由の出力経路（stream の取り違え・
  出力抑止）を保護できない。既存の `TestCLIValidateLarge` と同型で追加コストは小さい。
- 実 API 疎通（`large_forge`）は不要。本変更は provider / network に一切触れない。

### docs 変更分

`make verify-docs`（`scripts/check_doc_links.py`）でリンク整合を確認する。docs 単体の恒久テストは
追加しない（`docs/dev/testing-convention.md` の 4 条件: 独自ロジックなし / リンク切れは既存
verify-docs で捕捉 / 新規テストで増える回帰シグナルがない / 理由を本節に記録）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/dev/workflow-authoring.md` | **あり** | ステップフィールド表に `inject_verdict`（非推奨）を追加し、移行方法・削除予定・検出コマンドを記載する（完了条件） |
| `CHANGELOG.md` | **あり** | `## [Unreleased]` の `### Deprecated` に廃止予定・移行方法・ADR 008 の 3 要素を記載する。GitHub Release notes の正本（`.claude/skills/release/SKILL.md` Step 3） |
| `docs/adr/` | なし | 新規の技術選定はない。ADR 008（互換レイヤ非提供）/ ADR 011（overlay）の既存決定に従うのみ。ADR 011:171 の field 列挙は過去の決定記録であり書き換えない |
| `docs/ARCHITECTURE.md` | なし | module 構成・層方向を変更しない |
| `docs/dev/` その他 | なし | workflow / テスト規約そのものは不変 |
| `docs/reference/` | なし | `python/type-hints.md:92` の `inject_verdict: bool = False` は型記法の例示であり CLI / schema 契約ではない。#383 の削除時に扱う |
| `docs/cli-guides/` | なし | `kaji validate` / `kaji run` の CLI 仕様（引数・exit code）は不変。同ディレクトリに両コマンドの正本 guide はない |
| `AGENTS.md` / `CLAUDE.md` | なし | 開発規約に変更なし |
| `.claude/skills/issue-fix-code` / `issue-fix-design` / `incident-fix` の SKILL.md | なし（本 Issue では） | `inject_verdict` に言及するが、これらは kaji 本体 workflow が `resume` を併用しており挙動は変わらない。skill 文言の更新は下流移行（#382）/ 完全削除（#383）の scope |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| EPIC #310 本文「決定事項」 | https://github.com/apokamo/kaji/issues/310 | 「まず warning を追加し、挙動を維持した廃止予告 release を直ちに公開する」「予告 release の公開から 14 日以上経過し、下流移行が完了した後に engine から完全削除」「旧キーは silent ignore せず明示的 validation error」 |
| EPIC #310「現状ベースライン」「release 計画」 | 同上 | 下流 4 repo / 34 file / 105 件、built-in 使用 0 件。「version 番号は release 時点の version 正本に従う。固定する契約は『別 release』『予告 release 公開から 14 日以上』『移行完了後』」 |
| Issue #381 本文「完了条件」 | https://github.com/apokamo/kaji/issues/381 | 「集約単位は workflow file 単位。…指定件数にかかわらず、stderr に出力される deprecation warning は 1 行のみ」「warning は `inject_verdict`、削除予定の次回 minor release、移行方法、#310 を識別できる内容を含む」 |
| `docs/adr/008-no-backward-compat-layer.md` | `docs/adr/008-no-backward-compat-layer.md` | 決定 2: BREAKING は「壊れる契約 / 影響の判定方法 / 適用指針」の 3 要素を CHANGELOG / Release notes に必ず記載する |
| preflight の warnings 契約 | `kaji_harness/preflight.py:23-37`, `:66-108` | `WorkflowPreflightResult.warnings` は "Non-fatal compatibility warnings"。step ごとの exec_script warning が既にこの経路で集約されている |
| run 経路の warning 出力 | `kaji_harness/runner.py:801-814` | `_collect_skill_metadata()` が run あたり 1 回 `preflight_workflow()` を呼び、`sys.stderr.write(f"{warning}\n")` で出力する |
| validate 経路の現状 | `kaji_harness/commands/validate.py:51-96` | preflight の戻り値のうち `errors` のみを参照し、`warnings` を出力していない |
| 維持対象の注入挙動 | `kaji_harness/prompt.py:75-80` | `if (step.resume or step.inject_verdict) and state.last_transition_verdict:` で `previous_verdict` を prompt 変数へ注入 |
| parse 時の受理・型検証 | `kaji_harness/workflow.py:63`, `:202-207` | exec-step での禁止フィールド、非 bool の `WorkflowValidationError` |
| 移行先 CLI | `kaji_harness/commands/issue.py:153`, `:274` / `.claude/skills/release-starter/SKILL.md:14` | `kaji issue resolve-verdict <id> --step <step>` が対象 step の最新 verdict marker を決定的に解決する |
| release notes の生成元 | `.claude/skills/release/SKILL.md` Step 3 | `CHANGELOG.md` の `## [Unreleased]` を新 version セクションへ移し、BREAKING 3 要素の充足を確認する |
| Keep a Changelog 1.1.0 | https://keepachangelog.com/en/1.1.0/ | 「Deprecated for soon-to-be removed features」— 削除予定機能の区分は `### Deprecated` |
| テスト規約 | `docs/dev/testing-convention.md` | 実行時コード変更は S/M/L の観点を定義。省略時は 4 条件を明記。`large_local` = subprocess あり / network なし |
