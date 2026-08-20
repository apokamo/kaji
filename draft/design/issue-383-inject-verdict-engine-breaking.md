# [設計] inject_verdict を engine から削除し BREAKING リリースを行う

Issue: #383

## 概要

workflow step フィールド `inject_verdict` を engine（`models.py` / `workflow.py` /
`prompt.py` / `preflight.py`）と公開 workflow schema から削除し、当該キーを含む stale YAML を
**L1 parse 時の明示的な migration error** で停止させる。`resume` 経由の `previous_verdict`
注入は不変。

## 背景・目的

### 現状の問題（観測可能な形）

`grep -rn "inject_verdict"` を worktree `/home/aki/dev/kaji/kaji-refactor-383` の
`main` (eb859c8) 相当ツリーで実行した実測値:

| 領域 | 出現数 / ファイル | 内訳 |
|------|------------------|------|
| `kaji_harness/` | 20 occurrences / 4 files | `models.py`(4) / `workflow.py`(10) / `prompt.py`(2) / `preflight.py`(4) |
| `tests/` | 82 occurrences / 10 files | fixture 1 + 9 テストファイル |
| `docs/` `.claude/` | 6 files | `workflow-authoring.md` / `type-hints.md` / `adr/011` / skill 3本 |
| `.kaji/wf/**` | 0 | 自リポジトリの runtime workflow は移行済み |

実行 capability が残っている具体箇所（削除前の正本）:

- `kaji_harness/models.py:68-71` — `Step.inject_verdict` と廃止予告用の
  `Step.inject_verdict_declared`
- `kaji_harness/workflow.py:63` — `_EXEC_FORBIDDEN_KEYS` に `"inject_verdict"`
- `kaji_harness/workflow.py:202-208, 310-311` — YAML 表層値の bool 型検証と `Step` への格納
- `kaji_harness/workflow.py:528-529` — exec-step 排他の L2 検証
- `kaji_harness/prompt.py:76` — `if (step.resume or step.inject_verdict) and
  state.last_transition_verdict:` の注入条件
- `kaji_harness/preflight.py:22-38` — `_INJECT_VERDICT_DEPRECATION` と
  `_deprecated_field_warnings()`（#381 の廃止予告 warning）

さらに現行 parser は未知 step key を包括的には拒否しない（`_STEP_REQUIRED_KEYS = ("id",)`
のみを必須とし、未知キーは無視される: `kaji_harness/workflow.py:59, 145-160`）。したがって
フィールド認識だけを削除すると、`inject_verdict: true` を書いた stale YAML が
**validation 成功のまま設定だけ無視される**（silent regression）。

### 改善指標（測定可能）

> **文字列 `inject_verdict` は engine から完全には消えない。** 削除後も
> `_REMOVED_STEP_KEYS` の key と migration guidance がキー名を**意図的に保持する**
> （保持しなければ stale YAML を named rejection できない）。したがって指標は
> 「文字列の全消滅」ではなく **capability 参照の消滅（G1a）** と
> **残存が named rejection 由来だけであること（G1b）** に分離する。

| # | 指標 | 測定コマンド | ベースライン → 目標値 |
|---|------|--------------|----------------------|
| G1a | engine から実行 capability の参照が消えている | `grep -rnE 'step\.inject_verdict\|inject_verdict_declared\|raw_inject_verdict\|_INJECT_VERDICT_DEPRECATION\|_deprecated_field_warnings\|inject_verdict: bool\|inject_verdict=' kaji_harness/ \| wc -l` | 16 → **0** |
| G1b | 残存文字列が named rejection 由来だけである | `grep -rln "inject_verdict" kaji_harness/` | 4 files → **`kaji_harness/workflow.py` のみ**。かつ `grep -n "inject_verdict" kaji_harness/workflow.py` の全ヒットが `_REMOVED_STEP_KEYS` 定義ブロック（dict key・guidance 文字列・直上コメント）に収まる |
| G2 | 機能テストが削除後契約へ置換されている | (a) `grep -rnE 'step\.inject_verdict\|inject_verdict_declared\|inject_verdict=' tests/ \| wc -l` (b) `grep -rln "inject_verdict" tests/` | (a) 23 → **0**（属性・コンストラクタ引数の参照が消える） (b) 10 files → 削除後契約テスト（stale YAML 拒否）と fixture 以外に残らない |
| G3 | 公開 schema / 運用 docs から消えている | `grep -rn "inject_verdict" docs/dev/ docs/reference/ .claude/skills/` | 「削除済み」を説明する記述のみ（フィールド仕様としての記載は 0） |
| G4-cli | stale YAML が CLI で silent に通らない | `kaji validate` / `kaji run` / `kaji recover` に stale YAML を投入 | 3 経路とも **非 0 exit**（validate=1、run/recover=`EXIT_DEFINITION_ERROR`）+ stderr に migration error |
| G4-lib | stale YAML が library 経路で silent に通らない | series member 検証（`load_series`）に stale YAML を投入 | **`SeriesValidationError` 送出**（CLI exit code ではなく例外が観測境界）。`preflight_workflow_path()` は `errors` 非空 / `workflow=None` |
| G5 | `resume` 注入が不変 | `pytest tests/test_prompt_builder.py` の resume 系テスト | 変更なしで green |
| G6 | 品質ゲート | `source .venv/bin/activate && make check` | PASS |

G1a の識別子リストは「削除対象の実体」（`models.py:68,71` の 2 属性 / `workflow.py:202-208,310-311,528` の parse・格納・L2 参照 / `prompt.py:76` の注入条件 / `preflight.py:23-38,85` の warning producer）を網羅する。実装後にこの grep が 1 件でも残れば削除漏れである。

### ベースライン計測

実装フェーズ冒頭で以下を再計測し、改修後と比較する（誰が実行しても同じ値が出る形）。

```bash
cd /home/aki/dev/kaji/kaji-refactor-383
CAP='step\.inject_verdict|inject_verdict_declared|raw_inject_verdict|_INJECT_VERDICT_DEPRECATION|_deprecated_field_warnings|inject_verdict: bool|inject_verdict='

# G1a: engine の capability 参照（0 になるべき本体）
grep -rnE "$CAP" kaji_harness/ | wc -l            # 20260820 実測: 16 → 0

# G1b: engine の残存文字列（named rejection のみが残る）
grep -rln "inject_verdict" kaji_harness/          # 実測: 4 files → workflow.py のみ
grep -n  "inject_verdict" kaji_harness/workflow.py  # 実測: 10 行 → _REMOVED_STEP_KEYS 定義ブロックのみ

# G2: テストの capability 参照 / ファイル分布
grep -rnE 'step\.inject_verdict|inject_verdict_declared|inject_verdict=' tests/ | wc -l  # 実測: 23 → 0
grep -rln "inject_verdict" tests/                 # 実測: 10 files → 拒否テスト + fixture 以外は 0

# G3 / 参考
grep -rln "inject_verdict" docs/ .claude/         # 実測: 6 files → 削除済み記述のみ
grep -rn  "inject_verdict" .kaji/wf/ | wc -l      # 実測: 0 → 0（不変）

# pytest ベースライン（baseline-precheck step の構造化 artifact を正本とする）
source .venv/bin/activate && make check
```

`grep -rn "inject_verdict" kaji_harness/ | wc -l`（実測 20）は G1b の粗い上位集合であり、
**0 を目標値にしない**。20 のうち 16 が capability 参照（G1a で 0 になる）、残りは
コメント・エラーメッセージ・`_EXEC_FORBIDDEN_KEYS` のキー名であり、削除後は
`_REMOVED_STEP_KEYS` 由来の数行に置き換わる。

pytest の baseline failure 判定は workflow の `baseline-precheck` step が生成する構造化
artifact（`docs/dev/baseline-check.md`）を正本とし、本設計では数値を固定しない。

## インターフェース

### 入力

workflow YAML の step mapping。**変更点は step の受理キー集合のみ**。

| キー | 変更前 | 変更後 |
|------|--------|--------|
| `inject_verdict` | bool（省略時 false）として受理。宣言時は preflight が warning 1 行 | **受理しない。宣言されていれば L1 parse で `WorkflowValidationError`** |
| `resume` | str \| None。同一 agent の session 継続 | 不変 |
| その他すべて | — | 不変 |

### 出力

1. **`Step` dataclass**: `inject_verdict` / `inject_verdict_declared` の 2 属性が消滅する。
   これは内部 API だが、`Step(...)` を直接構築するテスト・script に影響する。
2. **prompt 変数 `previous_verdict`**: 注入条件が `step.resume and
   state.last_transition_verdict` のみになる。resume step の生成 prompt は**バイト単位で不変**。
3. **エラー出力**: stale YAML に対して 1 行の migration error。経路別の観測形は次のとおり。

| 経路 | 呼び出し箇所 | 観測される結果 |
|------|-------------|----------------|
| `kaji validate <f>` | `commands/validate.py:62` | exit 1、stderr に `✗` + migration error |
| `kaji run <f> <issue>` | `commands/run.py:171` | `EXIT_DEFINITION_ERROR`、stderr に `Error: ...` |
| `kaji recover <f> <issue>` | `commands/recover.py:63` | `EXIT_DEFINITION_ERROR`、stderr に `Error: ...` |
| series member 検証 | `series/loader.py:60` → `preflight_workflow_path`（`preflight.py:147`） | `SeriesValidationError`（`members.<i>.workflow is invalid (...): <migration error>`） |
| `preflight_workflow_path()` | `preflight.py:147-155` | `WorkflowPreflightResult(workflow=None, errors=[migration error])` |

4. **preflight warning**: `inject_verdict` 由来の deprecation warning が消える。
   `exec_script` skill の `agent` / `model` / `effort` 無視 warning は不変。

### 使用例

```yaml
# Before（削除済み・エラーになる）
steps:
  - id: fix-code
    skill: issue-fix-code
    agent: claude
    inject_verdict: true
    on: { PASS: end }

# After（同一 agent の session 継続）
steps:
  - id: fix-code
    skill: issue-fix-code
    agent: claude
    resume: review-code
    on: { PASS: end }
```

```console
$ kaji validate .kaji/wf/custom/stale.yaml
✗ .kaji/wf/custom/stale.yaml
  - Step 'fix-code': 'inject_verdict' was removed from the workflow step schema
    (apokamo/kaji#310, #383); remove the field and use 'resume: <step-id>' for
    same-agent session continuation, or read the prior verdict with
    'kaji issue resolve-verdict <issue-id> --step <step-id>'
$ echo $?
1
```

（上記メッセージは実装時に 1 行文字列として構成する。表示上の折返しであり改行は含まない。）

## 制約・前提条件

- **開始条件**: Issue #383「開始条件の充足証跡」（2026-08-20 再確認）が満たされている。
  予告 release v0.18.0 は `2026-07-25T03:28:19Z` 公開、最短削除日時
  `2026-08-08T03:28:19Z` を今日（2026-08-20）は超過。下流 `apokamo/kamo2` の
  runtime workflow 23本・managed starter 2本ともに `inject_verdict` 0 件。
- **ADR 008（後方互換レイヤ禁止）**: silent ignore / フォールバック / バージョン分岐を
  実装しない。stale YAML は fail-fast させる。
- **scope 混在禁止**（Issue 本文「混在禁止」）:
  - `resume` 経由の注入は廃止しない
  - verdict artifact / marker / comment fallback の優先順は変更しない
  - parser の unknown-key 方針は**全面変更しない**。今回追加するのは
    「削除済みキーの named rejection」のみで、未知キー一般は従来どおり無視される
- **CHANGELOG の不変区間**: `## [0.18.0]` 以下の released section は履歴記録であり編集しない
  （Keep a Changelog）。本 workflow で触るのは `## [Unreleased]` のみ。
- **`draft/design/**` の既存設計書**（10 files が `inject_verdict` に言及）は過去の意思決定
  記録であり改変しない。
- **`docs/adr/011-workflow-overlay-single-layer.md:171`** は承認済み ADR の記録であり、
  かつ同段落が「対象集合の正本は `kaji_harness/models.py`」と明記しているため編集しない
  （Issue の対象スコープにも含まれない）。
- **version 番号を engine のエラーメッセージに埋め込まない**。release version は release 時点の
  正本に従う（Issue「重要判断」表）ため、engine 側は Issue 番号と CHANGELOG を参照させる。

## 方針

### 削除の配置（Where）

migration error を **L1 parser（`workflow.py::_parse_workflow`）** に置く。

理由:

1. `load_workflow` は全 4 経路（validate / run / recover / series）が必ず通る唯一の共通関門で
   ある（`commands/validate.py:62` / `commands/run.py:171` / `commands/recover.py:63` /
   `preflight.py:147`）。L2（`validate_workflow`）や preflight に置くと、`load_workflow` の
   戻り値だけを使う経路が生まれた際に漏れる。
2. 既存の同種検証（`_EXEC_FORBIDDEN_KEYS` による named key rejection、`inject_verdict` の
   bool 型検証）が既に parser 層にある。同じ層に置くことで L1 の責務境界が乱れない。
3. #381 で series / recover は「warning 非対象経路」（`tests/test_series_io.py:81`,
   `tests/test_recovery_cli.py:446` が現在その契約を固定している）だったが、削除後は
   これらも**必ず fail する**必要がある。parser 配置はこれを構造的に保証する。

### 実装ステップ（順序）

```
1. safety net 確認 — resume 注入テスト・fixture cycle テストが現状 green（変更対象外）
2. parser: 削除済みキー拒否を追加（Red→Green）
3. models.py: Step.inject_verdict / inject_verdict_declared を削除
4. workflow.py: _EXEC_FORBIDDEN_KEYS から除外 + 表層値検証 + Step 格納 + L2 排他検証を削除
5. prompt.py: 注入条件を step.resume のみへ
6. preflight.py: _INJECT_VERDICT_DEPRECATION / _deprecated_field_warnings() 削除
7. tests: 機能テスト削除 → 削除後契約テストへ置換（4 経路 + 単体）
8. fixtures/test_workflow.yaml: inject_verdict 行を削除
9. docs / skills 更新、CHANGELOG [Unreleased] に BREAKING 追加
10. make check
```

### 疑似コード（parser）

```python
# kaji_harness/workflow.py（module level）
# 削除済み step キー → 移行手順。unknown-key 一般の拒否ではなく、
# 過去に受理していたキーだけを named error で止める（ADR 008: fail-fast、互換層なし）。
_REMOVED_STEP_KEYS: dict[str, str] = {
    "inject_verdict": (
        "'inject_verdict' was removed from the workflow step schema "
        "(apokamo/kaji#310, #383); remove the field and use 'resume: <step-id>' "
        "for same-agent session continuation, or read the prior verdict with "
        "'kaji issue resolve-verdict <issue-id> --step <step-id>'"
    ),
}

# _parse_workflow() の step ループ内、sid 確定直後 / skill-exec 排他検証の前
for removed_key, guidance in _REMOVED_STEP_KEYS.items():
    if removed_key in step_data:
        # step ID は利用者入力。repr() で 1 行にエスケープする（#381 と同じ理由）。
        raise WorkflowValidationError(f"Step {sid!r}: {guidance}")
```

**配置順の根拠**: `sid` 確定直後に置くことで、stale YAML が同時に別の不備（`on` 欠落等）を
持つ場合でも「まず移行せよ」という最も行動可能な診断が先に出る。exec-step + `inject_verdict`
の組み合わせも、`_EXEC_FORBIDDEN_KEYS` の汎用メッセージ（`must not set 'inject_verdict'`）
ではなく移行手順つきメッセージになる。これに伴い `_EXEC_FORBIDDEN_KEYS` から
`"inject_verdict"` を除く（残すと到達不能な dead branch になる）。

### 疑似コード（prompt）

```python
# kaji_harness/prompt.py:75-79
# 遷移元の verdict（resume ステップ）
if step.resume and state.last_transition_verdict:
    ...  # 中身は不変
```

### docs / skill の更新方針

| 対象 | 変更内容 |
|------|----------|
| `docs/dev/workflow-authoring.md:180` | exec-step の禁止フィールド列挙から `inject_verdict` を除く |
| `docs/dev/workflow-authoring.md:261` | step フィールド表の行を削除 |
| `docs/dev/workflow-authoring.md:412-448` | 「`inject_verdict`（非推奨）」節を「削除済みフィールド」節へ置換。フィールド仕様は書かず、(a) 削除済みであること (b) migration error が出ること (c) `resume` / `kaji issue resolve-verdict` への移行手順 (d) CHANGELOG BREAKING への参照、のみを残す |
| `docs/reference/python/type-hints.md:92` | `Step` dataclass のサンプルから `inject_verdict: bool = False` を削除（実コードのミラーであるため） |
| `docs/dev/skill-authoring.md:219-223` | **変更不要**。既に「`previous_verdict` は `resume` 指定ステップに注入される」と削除後契約どおり記述されている。実装フェーズで再確認のみ行う |
| `.claude/skills/issue-fix-code/SKILL.md:36`, `issue-fix-design/SKILL.md:35` | `resume` または `inject_verdict: true` 指定ステップ → `resume` 指定ステップ |
| `.claude/skills/incident-fix/SKILL.md:9,24` | `resume: investigate`（＋ `inject_verdict: true`）の記述から後者を削除。`.kaji/wf/official/incident.yaml:43` は既に `resume: investigate` のみであり、SKILL.md 側が実装と乖離している状態を同時に解消する |
| `.claude/skills/i-doc-fix/SKILL.md` | `inject_verdict` の記載なし（`previous_verdict` のみ）。**変更不要**、再確認のみ |

### CHANGELOG（`## [Unreleased]` に追加）

ADR 008 決定 2 の 3 要素を満たす BREAKING エントリを追加する。

- **壊れる契約**: workflow YAML step フィールド `inject_verdict` を engine と公開 schema から
  削除。当該キーを含む workflow は読み込み時に validation error で停止する
  （旧挙動: warning 付きで前 step の verdict を prompt へ注入）
- **影響の判定方法**: `rg -n 'inject_verdict' .kaji/wf/` が 1 件以上ヒットすれば影響あり。
  併せて `kaji validate .kaji/wf/**/*.yaml` が該当ファイルで error になる
- **適用指針**: 未カスタマイズなら managed starter の該当 workflow を再コピー。カスタマイズ済みなら
  該当 step から `inject_verdict` 行を削除し、前 step の verdict が必要な step は
  `resume: <step-id>`（同一 agent session 継続）へ移行するか、
  `kaji issue resolve-verdict <issue-id> --step <step-id>` で明示取得する。上流の変更点は
  Issue #383 とその PR を参照

GitHub Release notes への転記は `/release` Step 7 の責務であり、本 workflow の成果物ではない
（Issue「ワークフロー完了後の確認項目」）。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 完全削除するか | warning release 後に engine から削除する | Issue #383「重要判断」表 → #310 の 2026-07-25 方針（人間決定） | 削除対象を `models.py` / `workflow.py` / `prompt.py` / `preflight.py` の 6 箇所へ特定 |
| 実施時期 | 予告 release 公開から 14 日後以降 | Issue #383「開始条件の充足証跡」（人間決定・一次情報検証済み） | 追加判断なし。着手可否のゲートとしてのみ使用 |
| stale YAML の扱い | silent ignore せず明示的 migration error | Issue #383「重要判断」表（人間決定）。「エラー実装の配置は設計で詳細化可」と明記 | 配置を **L1 parser の named removed-key rejection** に決定。理由は 4 経路共通関門であること（`load_workflow` の呼び出し 4 箇所を実測） |
| release 形態 | BREAKING として次回 minor release | Issue #383「重要判断」表（人間決定）。version は release 時点の正本に従う | CHANGELOG `## [Unreleased]` にのみ BREAKING を追加。released section は編集しない |
| `resume` 注入の維持 | 維持する | Issue #383「混在禁止」（人間決定） | `prompt.py:76` の条件式から `or step.inject_verdict` のみを外す最小差分に限定 |
| unknown-key 方針 | 全面変更しない | Issue #383「混在禁止」（人間決定） | 削除済みキーのみを列挙する `_REMOVED_STEP_KEYS` 辞書とし、未知キー一般の挙動は不変に保つ |
| エラーメッセージに version 番号を含めるか | 含めない（Issue 番号と CHANGELOG を参照させる） | **AI の仮定**。根拠: Issue「重要判断」表が「version は release 時点の正本に従う」としており、実装時点で v0.19.0 と断定するとメッセージが事実と乖離しうる。検査先: `/issue-review-design` および `/i-dev-final-check` の docs 整合確認 | メッセージ本文は `(apokamo/kaji#310, #383)` の参照のみを持つ |
| `_EXEC_FORBIDDEN_KEYS` から `inject_verdict` を除くか | 除く | **AI の仮定**。根拠: removed-key 検査が先行するため残すと到達不能な dead branch になる（ADR 008「死んだ読み取り器を増やさない」）。検査先: `/issue-review-code` と `tests/test_exec_step_parser.py` の置換テスト | exec-step の該当 param を「移行エラー」テストへ移す |
| ADR 011 / released CHANGELOG / `draft/design/**` の `inject_verdict` 言及 | 編集しない | **AI の仮定**。根拠: いずれも過去の意思決定・履歴記録であり、ADR 011 は当該段落自身が「正本は `models.py`」と宣言している。Issue の対象スコープにも含まれない。検査先: `/issue-review-design` の影響ドキュメント確認 | 影響ドキュメント表に「あり/なし」と理由を明記 |
| `docs/dev/skill-authoring.md` の扱い | 変更不要（再確認のみ） | **AI の仮定**。根拠: 219-223 行が既に「`resume` 指定ステップに注入される」と削除後契約どおり。検査先: `/i-dev-final-check` の docs 整合 | Issue 対象スコープに挙がっているため「確認済み・差分なし」を証跡として残す |
| `incident-fix` SKILL.md の乖離解消 | 同 PR で直す | **AI の仮定**。根拠: SKILL.md が `inject_verdict: true` 起動と書いているが `incident.yaml:43` は `resume: investigate` のみで、削除により記述が完全な誤りになる。Issue 対象スコープに当該ファイルが明記されている。検査先: `/issue-review-code` | `inject_verdict` の言及のみを削除し、resume の説明は維持（scope 拡大しない） |

one-way door の未決は無し。公開 workflow schema の破壊的変更という最大の one-way door は、
Issue #383 と #310 で人間が決定済み（予告 release・14 日待機・移行完了の 3 条件つき）。

## テスト戦略

### 変更タイプ

実行時コード変更（parser / prompt builder / preflight の振る舞いが変わる）。加えて docs 更新を含む。

### 振る舞い非変更の保証（refactor 固有）

「削除された機能以外は不変」であることを、既存テストを**無改変で green に保つ**ことで示す。

- `tests/test_prompt_builder.py` の `resume` 系テスト（`previous_verdict` 注入）— 無改変で green
- `tests/test_skill_harness_adaptation.py` の cycle / transition テスト — fixture から
  `inject_verdict` 行を削るだけで無改変 green（当該行は cycle 定義に無関係）
- `tests/test_workflow_parser.py` の `inject_verdict_declared` 系 2 テスト以外 — 無改変 green
- `tests/test_exec_step_parser.py` の他 5 forbidden key param — 無改変 green

bridging test の新規追加は不要。`resume` 注入は既存 Small テストが入出力を固定しており、
削除差分がそこに触れないことをテストの無改変性そのものが示す。

#### Small テスト

- parser: skill-step に `inject_verdict: true` → `WorkflowValidationError`。message に
  `inject_verdict` / `resume` / `kaji issue resolve-verdict` / `#383` を含む
- parser: `inject_verdict: false` の明示指定でも同じく error（「値ではなくキーの存在」で判定）
- parser: 非 bool 値（例 `inject_verdict: "yes"`）でも型エラーではなく migration error になる
  （旧 `'inject_verdict' must be a boolean` パスの消滅を固定）
- parser: exec-step + `inject_verdict` → `must not set` ではなく migration error
- parser: 改行 / U+2028 を含む step ID でも error message が 1 行（`repr()` エスケープ、
  #381 の Must Fix と同じ不変条件）
- parser: `inject_verdict` を含まない workflow は従来どおり成功（回帰防止）
- parser: 未知キー一般（例 `bogus_key: 1`）は従来どおり**無視される**（unknown-key 方針
  非変更の固定）
- prompt: `resume` あり + `last_transition_verdict` あり → `previous_verdict` 注入（既存）
- prompt: `resume` なし → 注入されない（旧 `inject_verdict=True` 経路の消滅）
- preflight: `inject_verdict` を持たない workflow の `warnings` が空、`exec_script`
  warning は従来どおり出る（deprecation 消滅と既存 warning 存続の同時固定）

#### Medium テスト

4 entry path すべてで stale YAML が止まることを固定する。観測境界は経路種別で異なる:
CLI 3 経路は **非 0 exit code + stderr**（G4-cli）、series は **例外送出**（G4-lib）。

| 経路 | 配置先 | 期待 |
|------|--------|------|
| `cmd_validate` | `tests/test_cli_validate.py` | exit 1、stderr に migration error |
| `cmd_recover` | `tests/test_recovery_cli.py`（既存の `..._emits_no_deprecation_warning` を置換） | `EXIT_DEFINITION_ERROR`、`RECOVERY_FILE` 未生成 |
| series load | `tests/test_series_io.py`（既存の `..._emits_no_deprecation_warning` を置換） | `SeriesValidationError`、message に `members.0.workflow is invalid` と migration error |
| `cmd_run` | `tests/test_workflow_provider_match.py`（既存 cmd_run driver を再利用） | `EXIT_DEFINITION_ERROR` |

`tests/test_runner.py` の `TestCollectSkillMetadataDeprecationWarning` は削除する
（stale YAML は `_collect_skill_metadata()` 到達前の `load_workflow` で止まるため、当該層に
残す契約が存在しない）。

#### Large テスト

- `large_local`: **別プロセスの module CLI invocation**（`subprocess.run([sys.executable, "-m",
  "kaji_harness.cli_main", "validate", ...])`）で stale YAML → returncode 1 + stderr に
  migration error。ここで保証する境界は「`sys.argv` 解析 → exit code → stderr の
  プロセス境界越しの観測」であり、**配布済み `kaji` entry point（console_script）の疎通では
  ない**。既存 `tests/test_cli_validate.py` の large_local テストと同じ起動形式に揃える。
  配布 wheel からの `kaji` entry point 疎通は Issue #383「ワークフロー完了後の確認項目」
  （公開済み wheel での smoke test）が担い、本 workflow の恒久テストでは扱わない
- **既存 large_local の retarget（削除しない）**:
  `test_kaji_validate_inject_verdict_warning_path_with_newline_stays_single_line` が守っている
  「改行を含む path でも warning が 1 行」という `_print_warnings` の repr エスケープ契約
  （`commands/validate.py:100-113`、#381 Must Fix）は `inject_verdict` とは独立の不変条件である。
  warning producer を `exec_script` skill warning へ差し替えて**テストを存続させる**。
  単純削除すると #381 で入れた回帰防止が失われる
- 実 API 疎通（`large_forge`）は不要。本変更は GitHub / PyPI へアクセスしない
  （`docs/dev/testing-convention.md` の判定基準: 外部 API 疎通なし）

#### docs 変更分の検証

- `make verify-docs`（リンク・参照整合）。`workflow-authoring.md` の節見出し変更が
  他 docs からのアンカー参照を壊していないかを含む

### 恒久テストを追加しない領域

`CHANGELOG.md` の BREAKING 記述は文章であり、`docs/dev/testing-convention.md` の 4 条件
（独自ロジックなし / 既存ゲートで捕捉 / 追加しても回帰情報が増えない / 理由を説明可能）を
満たすため恒久テストを追加しない。ADR 008 3 要素の充足確認は `/release` skill Step 3 と
`/i-dev-final-check` が担う。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 新規技術選定なし。ADR 008 の既存方針の適用であり改訂不要。ADR 011 の field 列挙は承認済み記録かつ「正本は `models.py`」と自己宣言しているため編集しない |
| `docs/ARCHITECTURE.md` | なし | 52 行の `previous_verdict` 記述は注入変数の例示であり、変数自体は存続する |
| `docs/dev/workflow-authoring.md` | **あり** | 公開 workflow schema の正本。フィールド表・exec-step 禁止列挙・非推奨節を更新 |
| `docs/dev/skill-authoring.md` | なし（再確認） | 219-223 行は既に `resume` のみを注入条件として記述済み |
| `docs/reference/python/type-hints.md` | **あり** | `Step` dataclass サンプルが実コードのミラー |
| `docs/cli-guides/` | なし | CLI の引数・exit code 契約は不変（既存 `EXIT_DEFINITION_ERROR` / exit 1 を再利用） |
| `AGENTS.md` / `CLAUDE.md` | なし | 規約変更なし |
| `CHANGELOG.md` | **あり** | `## [Unreleased]` に BREAKING セクションを追加 |
| `.claude/skills/issue-fix-code`, `issue-fix-design`, `incident-fix` | **あり** | `previous_verdict` の注入条件記述が誤りになる |
| `.claude/skills/i-doc-fix`, `i-doc-verify` | なし（再確認） | `previous_verdict` のみ言及し `inject_verdict` に触れていない |
| `.kaji/wf/**` | なし | 自リポジトリの workflow は `inject_verdict` 0 件（実測） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #383 本文 | https://github.com/apokamo/kaji/issues/383 | 人間決定の正本。「stale YAML: silent ignoreせず明示的migration error」「エラー実装の配置は設計で詳細化可」「混在禁止: resume 経由の注入廃止を含めない／parser unknown-key 方針の全面変更を含めない」 |
| 親 EPIC #310 | https://github.com/apokamo/kaji/issues/310 | 段階廃止方針（予告 release → 14 日 → 完全削除）と release gate の正本 |
| 廃止予告 Issue #381 | https://github.com/apokamo/kaji/issues/381 | 予告 release の実装。warning 1 行集約と step ID / path の `repr()` エスケープ契約の由来 |
| 下流移行 Issue #382 | https://github.com/apokamo/kaji/issues/382 | 下流 runtime workflow の `inject_verdict` 0 件化の完了証跡 |
| Release v0.18.0 | https://github.com/apokamo/kaji/releases/tag/v0.18.0 | 予告 release の公開日時 `2026-07-25T03:28:19Z`。14 日ゲートの起点 |
| ADR 008 | `docs/adr/008-no-backward-compat-layer.md` | 決定 1「後方互換レイヤを書かない」、決定 2「BREAKING は壊れる契約・影響の判定方法・適用指針の 3 要素を CHANGELOG に明記」、帰結「検出不能時は上書きせず fail-safe」 |
| CHANGELOG 0.18.0 Deprecated | `CHANGELOG.md:47-65` | 予告時に下流へ約束した削除後契約: 「the field will become an explicit migration error instead of being silently ignored」「replace `inject_verdict: true` with `resume: <step-id>`」。本設計のエラーメッセージ文言はこの約束と一致させる |
| workflow parser 実装 | `kaji_harness/workflow.py:59, 63, 145-160, 202-208, 310-311, 528-529` | 削除対象と `_STEP_REQUIRED_KEYS = ("id",)` のみが必須＝未知キーは無視される現行仕様 |
| prompt builder 実装 | `kaji_harness/prompt.py:75-79` | 注入条件 `(step.resume or step.inject_verdict) and state.last_transition_verdict` |
| preflight 実装 | `kaji_harness/preflight.py:22-38, 130-155` | 削除対象の warning producer と、series が使う `preflight_workflow_path` の L1 失敗ハンドリング |
| `load_workflow` 呼び出し 4 経路 | `kaji_harness/commands/validate.py:62`, `commands/run.py:171`, `commands/recover.py:63`, `preflight.py:147` | parser 配置が全経路を覆うことの根拠（実測: `grep -rn "load_workflow(" kaji_harness/`） |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L 判定基準（外部 API 疎通なし → Large は `large_local` に限る）、恒久テスト省略の 4 条件 |
| refactor 設計指針 | `.claude/skills/_shared/design-by-type/refactor.md` | ベースライン計測コマンドの明示、改善指標の測定可能性、公開 IF 変更時の移行パス列挙 |
| Keep a Changelog 1.1.0 | https://keepachangelog.com/en/1.1.0/ | released section を書き換えず `Unreleased` に追記する運用の根拠 |
| Semantic Versioning 2.0.0 | https://semver.org/spec/v2.0.0.html | 破壊的変更を伴う version bump 方針（0.x の minor bump）の根拠 |
