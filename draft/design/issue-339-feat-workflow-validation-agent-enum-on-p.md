# [設計] workflow validation ルールの厳密化（agent enum / on.PASS 必須 / dead step 検出）

Issue: #339

## 概要

`kaji_harness/workflow.py` の `validate_workflow()` に静的検証ルールを 3 つ追加し、
workflow YAML の typo（`agent: cladue`）・成功遷移の書き漏れ（`on.PASS` 欠落）・
書き換え残り（到達不能 step）を実行前に `WorkflowValidationError` として検出する。

## 背景・目的

kaji の workflow は 1 本の完走に数十分〜1 時間超かかるため、「実行してみるまで
分からない」定義エラーの損失が大きい（#338 の実障害では member 完走 76 分後に
次 member の定義エラーで停止した）。#338 で L1/L2/L3 の共通 preflight
（`kaji_harness/preflight.py`）が全入口（`kaji run` / `kaji validate` / series /
recovery）へ適用されたので、本 Issue は preflight が実行する検証ルール自体の穴を塞ぐ。

### ユーザーストーリー

- **workflow 作者として**、`.kaji/wf/*.yaml` を書き換えたとき typo や遷移の書き漏れを
  `kaji validate` の即時実行で検出したい。実行して数十分待ってから知りたくない。
- **series 運用者として**、複数 member を直列実行する前に全 member の workflow 定義が
  構造的に健全であることを確認したい。
- **kaji のメンテナとして**、agent 追加時の検証テーブル登録漏れが「静かに検証 skip」
  ではなく「明示的なエラー」として現れてほしい。

### 現状の穴（一次情報）

1. **agent 名が未検証**: `workflow.py:69` の `_AGENT_EFFORT_ALLOWED` は effort 許容値
   テーブルであり、コメントに「辞書未登録の agent (gemini 等) は validation skip」と
   明記されている。`agent: cladue` は validation を素通りし、実行時に `cli.py:143-151`
   の `match step.agent` ディスパッチ（`case _: raise ValueError`）まで壊れない。
2. **`on.PASS` 欠落が未検証**: `validate_workflow()`（`workflow.py:464-500`）は
   `on` の非空 dict・遷移先の存在・verdict 名は検証するが「PASS の行き先があるか」は
   見ない。欠落は実行時の `InvalidTransition`（`runner.py` docstring）まで検出されない。
3. **到達不能 step が未検証**: `on` の遷移を書き換えた結果どこからも参照されなくなった
   step が validation を素通りする。

## インターフェース

### 入力

- `validate_workflow(workflow: Workflow) -> None`（`workflow.py:357`）。
  **シグネチャは変更しない**。検証ルールの追加のみ。
- 新モジュール定数 `VALID_AGENTS: frozenset[str] = frozenset({"claude", "codex", "gemini"})`
  を `_AGENT_EFFORT_ALLOWED`（`workflow.py:69`）の近傍に置く。enum の正本は
  `cli.py:143-151` の runtime dispatch と `adapters.py:386` の `ADAPTERS` キー集合。

### 出力

- 違反があれば既存パターン通り `errors: list[str]` に集約し、末尾で
  `WorkflowValidationError(errors)` を送出（`workflow.py:567-568`）。
  **1 回の validate で全違反をまとめて報告**する既存挙動を維持する。
- エラーメッセージ形式（テストで固定する契約。既存メッセージの文体に合わせる）:

| ルール | メッセージ形式 |
|--------|----------------|
| 1. agent enum | `Step '<id>' has unknown agent '<agent>' (allowed: ['claude', 'codex', 'gemini'])` |
| 2. on.PASS 必須 | `Step '<id>' 'on' must define a 'PASS' transition` |
| 3. dead step | `Step '<id>' is not reachable from the first step '<root_id>'` |

### 使用例

```console
$ kaji validate .kaji/wf/broken.yaml
✗ .kaji/wf/broken.yaml
  - Step 'implement' has unknown agent 'cladue' (allowed: ['claude', 'codex', 'gemini'])
  - Step 'review' 'on' must define a 'PASS' transition
  - Step 'old-check' is not reachable from the first step 'design'
$ echo $?
1
```

（`kaji validate` の出力枠組み・exit code は #338 で確立済み。本 Issue はエラー行が
増えるだけで CLI の入出力契約は変更しない）

### エラーケースの挙動

- 3 ルールはいずれも error（`WorkflowValidationError`）。warning にしない（人間決定）。
- 既存エラーとの併発時も全件を 1 つの例外に集約（既存挙動）。
- `on` が非空 dict でない step（既存エラー `invalid_on_step_ids` 該当）は、ルール 2 の
  PASS チェックを行わない（既存エラーで報告済み。二重報告を避ける）。ルール 3 の
  到達可能性 traversal ではその step の出エッジを持たないものとして扱う。
- ルール 2/3 は既存ルール「遷移先が存在すること」（`workflow.py:491-495`）と独立に
  動く。未知 step への遷移エッジは traversal で無視する（存在エラーは既存ルールが報告）。

## 制約・前提条件

- **#338 完了が前提**（完了済み: commit 742fd46）。全入口が `preflight.py:73` 経由で
  `validate_workflow()` を呼ぶため、ルール追加は `validate_workflow()` のみでよく、
  呼び出し口の変更は本 Issue に含めない。
- **agent 省略（`agent: None`）は本ルールの対象外**: exec_script skill の agent 省略
  可否は L3 preflight（`preflight.py:90-95`）の責務。ルール 1 は
  `step.agent is not None` の場合のみ enum 照合する。exec-step の agent 指定は
  既存ルール（`_EXEC_FORBIDDEN_KEYS` / `workflow.py:423-424`）が既に拒否する。
- **model 名は検証しない**（人間決定・スコープ境界）。
- **effort 検証は現状維持**: `_AGENT_EFFORT_ALLOWED` に gemini は未登録のままとし、
  gemini の effort 値は引き続き検証 skip される。ルール 1 が塞ぐのは「typo agent が
  effort 検証ごと素通りする」穴であり、gemini の effort enum 追加は CLI 仕様の
  一次情報確認を要する別作業（スコープ外）。
- **互換性**: 到達不能 step を含む workflow は今後 validate で落ちる。これは意図した
  validation 強化であり、テストと docs に明記する（Issue 本文「リスク・懸念」）。

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/workflow.py` | `VALID_AGENTS` 定数追加、`validate_workflow()` に 3 ルール追加 |
| `tests/test_workflow_validator.py` | 3 ルールの Small テスト追加 |
| `tests/test_cli_validate.py` | 新ルール違反 YAML の CLI 経由（M/L）テスト追加 |
| `docs/dev/workflow-authoring.md` | validation ルール 3 件の追記、`--from` / `--step` 意味論の明記 |
| `.kaji/wf/*.yaml` | 設計時分析では修正不要（後述）。実装時再検証で fallout があれば最小整合修正 |

`kaji_harness/runner.py` / `cli_main.py`（`--from` / `--step` 処理）は**変更しない**
（理由は「方針」参照）。

## 方針

### ルール 1: agent enum（`validate_workflow()` の step ループ内）

```
if step.agent is not None and step.agent not in VALID_AGENTS:
    errors.append(...)
```

### ルール 2: on.PASS 必須（同 step ループ内）

`on` が有効（非空 dict）な step に対してのみ:

```
if "PASS" not in step.on:
    errors.append(...)
```

agent-backed skill / exec_script skill / direct exec の区別なく全 step に適用する
（人間決定）。step 種別による分岐はそもそも書かない。

### ルール 3: dead step 検出（step ループの後、workflow レベル）

```
root = steps[0]                          # 唯一の canonical root（人間決定）
reachable = BFS/DFS(root, edges = 各 step の on の値のうち "end" と未知 step を除く)
              ※ invalid_on_step_ids の step は出エッジなしとして扱う
for step in steps:                       # 宣言順に報告（決定的な出力順）
    if step.id not in reachable:
        errors.append(...)
```

- cycle 定義（`entry` / `loop`）は step への参照であって遷移エッジではないため、
  到達可能性に寄与させない。`resume` も session 継続の参照であり同様。
- `workflow.steps` が空の場合は既存エラー（`workflow.py:412-413`）に任せ、
  traversal は行わない。

### `--from` / `--step` に runner 変更が不要な理由

人間決定は「`--from` / `--step` は canonical graph 上の到達可能 step だけを対象とし、
孤立 step を追加 root として扱わない」。#338 により `kaji run`（`--from` / `--step`
含む）は実行前に必ず preflight を通るため、**dead step を含む workflow はそもそも
実行に入れない**。preflight を通過した workflow では全 step が到達可能であり、
`runner.py:864-872` の既存 step 存在チェックだけで人間決定の意味論が透過的に満たされる。
よって runner 側の到達可能性チェック追加は不要（この透過的保証をテストで固定する）。

### 既存 workflow への fallout（設計時分析）

repository 管理下の `.kaji/wf/*.yaml` 全 9 本（feat/339 分岐時点の main 追跡分:
dev / dev-local / dev-thorough / dev-thorough-fable / docs / docs-fable / docs-local /
docs-thorough-codex / incident）に対し、本設計の 3 ルールを試作スクリプトで静的適用した。

**結果: 9 本すべて違反ゼロ（agent enum / on.PASS / dead step とも該当なし）。**

- Issue 本文の「現在 10 本」との差分: 起票時点の main 作業ツリーには未コミットの
  `docs-codex.yaml`（untracked）が存在しており、これを含めた計数と推定される。
  実装時は実装時点の repository 管理下全件で `kaji validate` を再実行し、結果
  （落ちた場合は「最小整合修正 / 無関係な既存 bug / ルール過剰」の一件ずつの判定）を
  PR 本文に記録する（完了条件）。
- fallout ゼロのため、現時点で最小整合修正・別 Issue 分離の対象はない。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| dead step の severity | warning ではなく `WorkflowValidationError` | Issue コメント「人間決定: validation ルール境界」項 1（2026-07-16） | エラーメッセージ形式と既存 errors 集約への合流を定義 |
| 到達可能性の root | 先頭 step を唯一の canonical root | 同コメント項 2 | traversal のエッジ定義（`on` の値のみ。cycle / resume は非エッジ、"end" と未知 step は除外） |
| `--from` / `--step` の意味 | canonical graph 上の到達可能 step のみ対象。孤立 step を追加 root にしない | 同コメント項 3 + 補足コメント「`--step` の扱い」（2026-07-16） | **AI の詳細化**: #338 の全入口 preflight により dead step ゼロが実行前に保証されるため runner 変更不要と判断。review-design / review-code で検査 |
| `on.PASS` の対象 | agent-backed skill / exec_script skill / direct exec を含む全 step | 同コメント項 4 | `on` が非空 dict でない step は既存エラーに委ね PASS チェックを skip（二重報告回避）。**AI の仮定**、review-design で検査 |
| 既存 workflow の fallout | 最小整合修正は #339 に含め、無関係 bug は別 Issue 分離 | 同コメント項 5・6 | 設計時静的分析で 9 本 fallout ゼロを確認。実装時に `kaji validate` 全件再実行し PR 本文へ記録 |
| agent enum | `{claude, codex, gemini}` を許可。model 名は検証しない | 同コメント項 7 + Issue 本文スコープ境界 | `VALID_AGENTS` 定数の配置（`_AGENT_EFFORT_ALLOWED` 近傍）と、`ADAPTERS` キー集合との drift 防止 fitness test を定義 |
| ルール追加位置 | `validate_workflow()` のみ（`_parse_workflow()` には重複追加しない） | Issue 本文概要「`validate_workflow()` に静的検証ルールを 3 つ追加」（人間決定）。全入口が preflight 経由で同関数を呼ぶ事実（`preflight.py:73`）が補強 | **AI の詳細化**: parse 時重複は不要と判断。review-design で検査 |
| agent 省略の扱い | `agent: None` は enum 対象外（L3 preflight の責務） | 既存契約: `preflight.py:90-95` と `docs/dev/workflow-authoring.md` §「`agent` の省略条件」 | ルール 1 の条件を `step.agent is not None` に限定 |
| gemini の effort | `_AGENT_EFFORT_ALLOWED` へ追加しない（検証 skip 継続） | Issue 本文スコープ境界「effort の検証ルールは現状維持」（人間決定） | ルール 1 導入後も gemini の effort が未検証で残ることを明文化（将来の別 Issue 候補） |
| エラーメッセージ文言 | 上表の 3 形式 | **AI の仮定**。既存メッセージ（`Step '<id>' ...`）の文体踏襲が根拠。review-design / review-code で検査 | テストで文言を固定する契約として定義 |

## テスト戦略

> 変更タイプ: **実行時コード変更**（`validate_workflow()` のロジック追加）

### Small テスト（`tests/test_workflow_validator.py` へ追加）

外部依存なしの純粋バリデーション検証。

- **ルール 1 正常系**: `claude` / `codex` / `gemini` の各 agent が通る。
  `agent: None` の skill step（exec_script 想定）が enum エラーにならない
- **ルール 1 異常系**: `agent: cladue`（typo）/ 大文字 `Claude` / 未知 agent が
  所定メッセージでエラーになる
- **ルール 2 正常系**: 全 step に `on.PASS` がある workflow が通る
- **ルール 2 異常系**: skill step / exec step それぞれで `on` に `PASS` が無い場合に
  エラー。`on` が空 / 非 dict の step では既存エラーのみで PASS エラーが重複しないこと
- **ルール 3 正常系**: 先頭 step から `on` 遷移で全 step に到達できる workflow
  （分岐・cycle 構造を含む）が通る。単一 step workflow が通る
- **ルール 3 異常系**: 孤立 step / 遷移書き換えで切り離された step 群が宣言順に
  エラーになる。cycle の `entry` / `loop` 参照だけでは到達扱いにならない。
  `resume` 参照だけでは到達扱いにならない。未知 step への遷移と dead step の併発時に
  両方のエラーが集約される
- **複合**: 3 ルール同時違反の workflow で全エラーが 1 例外に集約される
- **drift 防止 fitness**: `VALID_AGENTS == set(ADAPTERS)`（`adapters.py:386`）を
  固定し、agent 追加時の登録漏れを「明示的なエラー」にする（Issue のメンテナ・ストーリー）

### Medium テスト（`tests/test_cli_validate.py` へ追加）

ファイル I/O + preflight 結合（in-process CLI 呼び出し）。

- 新ルール違反の YAML fixture が `kaji validate` で ✗ / exit code 非 0 になり、
  エラーメッセージが出力に含まれる
- repository 管理下 `.kaji/wf/*.yaml` 全件が `kaji validate` で ✓ になる
  （fallout ゼロの恒久保証。既存に同種テストがあれば拡張で対応）

### Large テスト

- `tests/test_cli_validate.py` は S/M/L 構成を持つ（同ファイル docstring）ため、
  既存の large 系（subprocess での実 CLI 起動）に新ルール違反 fixture の
  1 ケースを追加する（`@pytest.mark.large_local`。ネットワーク不要）
- 実 GitHub API / 実 agent CLI 疎通（`large_forge` 等）は不要:
  本変更は agent プロセス起動前の純ロジック検証であり、外部サービスとの結合面を
  持たない。validate の CLI 入出力契約は #338 の既存 Large が回帰網として保護済み

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/dev/workflow-authoring.md | **あり** | L1 validation ルールに 3 件追記。`--from` / `--step` の説明（部分実行オプション表）へ「canonical graph 上の到達可能 step のみ対象」を明記。agent 表（`claude` / `codex` / `gemini`）は既存記載と整合済みのため enum 化の事実のみ追記。`workflow.py:69` コメントの「docs に同じ表を保持する」宣言と同期 |
| docs/adr/ | なし | 新しい技術選定なし（既存 validation 機構への enum / graph ルール追加） |
| docs/ARCHITECTURE.md | なし | 層構造・モジュール境界の変更なし |
| docs/dev/（その他） | なし | 開発手順・テスト規約の変更なし |
| docs/reference/ | なし | Python 規約・API 仕様の変更なし |
| docs/cli-guides/ | なし | `kaji validate` の CLI 入出力契約（引数・exit code）は不変。エラー行が増えるのみ |
| AGENTS.md / CLAUDE.md | なし | プロジェクト規約の変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| effort テーブルと skip 挙動 | `kaji_harness/workflow.py:63-72` | コメント「辞書未登録の agent (gemini 等) は validation skip」— ルール 1 の必要性の根拠 |
| ルール追加先 | `kaji_harness/workflow.py:357-568` | `validate_workflow()`。errors 集約と `WorkflowValidationError(errors)` 送出の既存パターン |
| 現行 `on` 検証 | `kaji_harness/workflow.py:464-500` | 非空 dict・遷移先存在・verdict 名は検証するが PASS の有無は見ない — ルール 2 の根拠 |
| agent 実行時ディスパッチ | `kaji_harness/cli.py:143-151` | `match step.agent: case "claude" / "codex" / "gemini" / case _: raise ValueError` — enum `{claude, codex, gemini}` の正本 |
| adapter レジストリ | `kaji_harness/adapters.py:386-390` | `ADAPTERS` キーが `claude` / `codex` / `gemini` — drift 防止 fitness test の照合先 |
| #338 共通 preflight | `kaji_harness/preflight.py:73, 90-95` | 全入口が `validate_workflow()` を実行。agent 省略条件は L3 の責務 — ルール 1 の対象限定と「runner 変更不要」の根拠 |
| `--from` / `--step` 現行実装 | `kaji_harness/runner.py:864-872` | step 存在チェックのみ。preflight 通過後は全 step 到達可能のため変更不要 |
| agent / effort / L1-L3 の docs 正本 | `docs/dev/workflow-authoring.md`（step field 表 / §effort 値 / §Validation の L1-L3 表 / 部分実行オプション表） | agent 許容値 `claude / codex / gemini` の docs 側記載。更新対象の特定 |
| 人間決定 | Issue #339 本文「重要判断」+ コメント「人間決定: validation ルール境界」「人間決定の補足: `--step` の扱い」（apokamo, 2026-07-16） | 3 ルールの severity / root / 対象範囲 / fallout 処遇 / enum 値の決定 |
| 先行 Issue | #338（commit 742fd46 で完了） | validation 入口統一。本 Issue の前提 |
