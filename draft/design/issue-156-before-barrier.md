# [設計] `kaji run --before <step>` barrier オプションの追加

Issue: #156

## 概要

`kaji run` に `--before <step>` フラグを追加する。指定したステップを **dispatch する直前で停止** する exclusive barrier として動作させ、「設計フェーズだけ実行して止める」「PR 作成前で止める」など、多段ワークフローを途中で意図的に停止する運用を CLI 一発で実現する。

## 背景・目的

### ユーザーストーリー

- **[Workflow 実行者] として**、コストの大きい後続ステップに進む前に人間レビューを挟みたい。`kaji run feature-development.yaml 156 --before implement` のように barrier を指定して停止できるようにしたい。
- **[Workflow 作者] として**、特殊な YAML 構文を追加することなく、CLI フラグだけで停止点を制御したい（workflow 側の変更不要）。
- **[Maintainer] として**、ループ・分岐を含む遷移グラフでも意味論が一意に決まる barrier 機構を持ちたい。

### 現状の問題

1. `kaji run` の停止制御は `--from`（開始点）と `--step`（単一実行）のみで、「ここまで実行して止める」が表現できない。
2. 多段 workflow（design → implement → pr 等）で人間レビュー点を作るには `--step` を連打するしかなく、`on:` 遷移を手で追う必要がある。
3. CLI として「開始点指定」と「停止点指定」が非対称。

### 代替案と不採用理由

Issue 本文「検討経緯」に詳細記載済み。要約:

| 案 | 不採用理由 |
|----|-----------|
| `--to <step>` (inclusive) | ループ内ステップ指定時に「何回目で止めるか」が一意に決まらず DAG で破綻 |
| `--until <step>` (exclusive 同義) | 命名が inclusive 寄りに誤読される (\"work until Friday\") |
| `--through` / `--to` を sugar 併設 | 線形専用例外を作ると DAG の説明が破綻 |
| YAML で `stoppable: true` 宣言 | 過剰設計。CLI 側で十分表現でき、作者に余計な負担を強いる |

採用案 `--before` は「dispatch 直前で停止」の実装語彙と一致し、`--from A --before B` が前置詞ペアとして自然に読める。

## インターフェース

### 入力

CLI フラグを 1 つ追加:

| フラグ | 型 | 必須 | 説明 |
|--------|-----|------|------|
| `--before <step>` | str | 任意 | 指定 step ID を **dispatch する直前で停止** する exclusive barrier。指定 step は実行されない |

組み合わせルール:

- `--from A --before B` ✅ 両立。「A から B の手前まで」
- `--step X --before Y` ❌ `--step` と `--before` は相互排他（`--step` 自体が単発実行であり barrier と意味が衝突）
- `--before <存在しない step>` ❌ 起動時に WorkflowValidationError（exit code: 既存の DEFINITION_ERROR と同じ）
- `--before end` ✅ 許容（デフォルト動作と等価。ユーザーが明示したい場合のため）

### 出力

| 観測点 | 動作 |
|--------|------|
| barrier ヒット時の stdout/log | `INFO: stopped before dispatching '<step>' (--before barrier)` を RunLogger に出力 |
| barrier 未到達で workflow 完了時 | stderr に WARN `stop point '<X>' was never reached; workflow completed naturally` を出力 |
| 終了コード（barrier ヒット） | `0`（正常完了扱い） |
| 終了コード（barrier 未到達で完了） | `0`（正常完了扱い、WARN 付き） |
| 終了コード（不正値） | 既存 `EXIT_DEFINITION_ERROR` |
| `SessionState` への影響 | barrier 直前ステップまでの verdict は通常通り記録される。barrier 自体の verdict は記録しない（dispatch していないため） |

### 使用例

```bash
# 設計フェーズだけ実行して止める
kaji run workflows/feature-development.yaml 156 --before implement

# 修正ループだけ回す（A から B 手前まで）
kaji run workflows/feature-development.yaml 156 --from fix-design --before implement

# 不正値: 即エラー
kaji run workflows/feature-development.yaml 156 --before nonexistent-step
# → Error: Step 'nonexistent-step' not found in workflow (exit 2)
```

### エラー

| ケース | 例外 / 戻り値 |
|--------|---------------|
| `--before` 値が workflow に存在しない step | 起動時に `WorkflowValidationError` → `EXIT_DEFINITION_ERROR` |
| `--step` と `--before` 同時指定 | `cmd_run` の冒頭で argparse 後の相互排他チェックで弾く（`--from` と `--step` の既存ガードと同位置に追加） |

## 制約・前提条件

- 実装は `kaji_harness/cli_main.py`（フラグ追加 / 検証）と `kaji_harness/runner.py`（barrier チェック）に閉じる。
- workflow YAML スキーマは変更しない（`models.py` / `workflow.py` への影響なし）。
- 既存の `--from` / `--step` の挙動を破壊しない（後方互換）。
- barrier チェックは「次に dispatch しようとしている step ID」と `--before` 値の比較 1 箇所で完結させる（複数チェックポイントを設けない）。
- `resume:` を持つステップを barrier の直後に配置する workflow では、barrier 後に `--from` で再開すると agent context 整合が壊れうる（本 Issue のスコープ外。docs に注意書きを記載）。

## 変更スコープ

| 種別 | ファイル | 内容 |
|------|---------|------|
| 変更 | `kaji_harness/cli_main.py` | `--before` フラグ追加、`--step` との相互排他チェック追加、`WorkflowRunner` への引き渡し |
| 変更 | `kaji_harness/runner.py` | `WorkflowRunner` に `before_step: str | None` フィールド追加。dispatch 直前 barrier チェック追加。未到達検知用フラグと WARN ログ |
| 変更 | `kaji_harness/logger.py` | barrier ヒット / 未到達ログ用メソッド（最小限。既存の info/warning で済むなら追加不要） |
| 追加 | `tests/test_runner_before.py`（仮） | barrier 動作の Small / Medium テスト |
| 変更 | `README.md` | ワークフロー実行セクションに `--before` 例を追加 |
| 変更 | `docs/dev/workflow-authoring.md` または新規 `docs/cli-guides/run-options.md` | `--before` 仕様、`--from` / `--step` / `--before` 比較表、`resume:` 注意点 |

## 方針（Minimal How）

### CLI レイヤー（`cli_main.py`）

```python
# _register_run に追加
p.add_argument(
    "--before",
    dest="before_step",
    help="Stop just before dispatching <step> (exclusive barrier).",
)

# cmd_run の相互排他チェック拡張
if args.single_step and args.before_step:
    print("Error: --step and --before are mutually exclusive", file=sys.stderr)
    return EXIT_DEFINITION_ERROR

# WorkflowRunner 構築時に before_step=args.before_step を渡す
```

`--before` 値の存在検証は **Runner 起動時に既存 `validate_workflow` 後に実行**（workflow 構造を一度ロード済みの状態で `find_step` するのが最も自然）。

### Runner レイヤー（`runner.py`）

`WorkflowRunner` に以下を追加:

```python
@dataclass
class WorkflowRunner:
    ...
    before_step: str | None = None
```

run() 冒頭の起動時検証（Step 0〜1 の間）:

```python
if self.before_step and self.before_step != "end":
    if not self.workflow.find_step(self.before_step):
        raise WorkflowValidationError(f"Step '{self.before_step}' not found")
```

メインループ末尾、「次のステップを決定」ブロックを以下に変更:

```python
# 次のステップを決定
if self.single_step:
    break

next_step_id = current_step.on.get(verdict.status)
if next_step_id is None:
    raise InvalidTransition(current_step.id, verdict.status)

# barrier チェック（dispatch 直前で停止）
if self.before_step and next_step_id == self.before_step:
    logger.log_barrier_hit(self.before_step)  # or logger.info(...)
    barrier_hit = True
    break

current_step = self.workflow.find_step(next_step_id)
```

未到達検知:

```python
# ループ脱出後（current_step が "end" or None）
if self.before_step and not barrier_hit and self.before_step != "end":
    logger.log_barrier_missed(self.before_step)
    # stderr WARN
```

### barrier ヒット時の verdict / state 取り扱い

- barrier 直前ステップ（= 現 `current_step`）の verdict は通常通り `state.record_step` 済み。
- barrier 自体は dispatch しないので state には何も追加しない。
- `--from` で再開した場合、`SessionState` は issue 単位で永続化されているため、barrier 後のステップから自然に続行可能（既存挙動）。

### `--step` との関係

`--step` は単発実行で `current_step.on` 遷移を見ずに break する既存実装。barrier チェックは「次の遷移先を決めた後」で行うため、`--step` 経路には barrier チェックが届かない（=相互排他で十分）。

## テスト戦略

### 変更タイプ
実行時コード変更（CLI フラグ追加 + Runner ループ分岐）。

### Small テスト

- `WorkflowRunner` を直接初期化し、モック Workflow + モック step 実行で barrier ロジックを検証
  - 線形 `A → B → C` で `before_step="C"` → A, B のみ実行、C 直前で break する
  - 分岐 workflow で barrier 指定 step に到達しないルートを通った場合、未到達 WARN フラグが立つ
  - `before_step="end"` 指定時は通常完了と同等
- CLI 引数パースの相互排他: `--step X --before Y` が `EXIT_DEFINITION_ERROR` を返す
- 不正値: workflow に存在しない step ID 指定で `WorkflowValidationError`

### Medium テスト

- 実 workflow YAML をロードし、CLI 実行ハーネス層を経由して barrier が機能することを確認
  - ループを含む `verify ⇄ fix` workflow で `--before next-step` → ループは PASS まで回り、PASS 後の next-step 直前で停止
  - `--from A --before B` の合成
- agent CLI 自体はモック / fake fixture を使用（実 LLM 呼び出しは行わない）

### Large テスト

- 不要。barrier は外部 API を介さない純粋な遷移制御で、Small / Medium で観点を網羅できる。
- testing-convention.md の 4 条件のうち「既存ゲートで不具合パターンを捕捉できる」「物理的に作成不可な対象ではない」については、E2E に近い検証は Medium で十分カバー可能（外部 API を呼ばないため Large 化する必然性なし）と判断。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新ライブラリ採用や外部依存追加なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ層の追加・変更なし |
| docs/dev/workflow-authoring.md | あり | `--before` 仕様 + `--from` / `--step` / `--before` 比較表 + `resume:` 注意点 |
| docs/cli-guides/ | あり（候補） | 既存ファイル構成に応じて、`run` サブコマンド仕様としてここに書くのが適切なら本ファイルを優先 |
| docs/reference/ | なし | API 仕様・規約変更なし |
| README.md | あり | ワークフロー実行セクションに `--before` 例を追記 |
| CLAUDE.md | なし | 規約変更なし |

実装段階で `docs/cli-guides/` に既存の run コマンド解説ファイルがあればそちらを正本とし、`workflow-authoring.md` からはリンクを張る方針で確認する。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 現状 `kaji run` オプション | `README.md:117-128` | 「途中から再開」「単一ステップ実行」のみ存在、停止点指定は未提供。本機能の対称性議論の出発点 |
| 多段 workflow 実例 | `workflows/feature-development.yaml` | design → implement → pr の構造があり、設計フェーズ後の停止ニーズが現実に存在 |
| Runner の遷移評価ロジック | `kaji_harness/runner.py:196-203` | 「next_step_id を `current_step.on.get(verdict.status)` で決定 → `find_step` で current 更新」の 1 箇所が dispatch ポイント。barrier はこの直後に挿入する |
| CLI 引数定義 | `kaji_harness/cli_main.py:55-68` | 既存 `--from` / `--step` の登録形式と相互排他ガード位置（L171-177）が、本 Issue で追加するコードのテンプレート |
| 遷移セマンティクス | `docs/dev/workflow-authoring.md` | `on: PASS/RETRY/ABORT` の遷移グラフ仕様。barrier の意味論（exclusive）が DAG で一意に定まることの根拠 |
| Python argparse 相互排他 | https://docs.python.org/3/library/argparse.html#mutual-exclusion | 公式仕様。今回は既存パターン（手書き if 文での排他）に合わせるため `add_mutually_exclusive_group` は使わない |
| テスト規約 | `docs/dev/testing-convention.md` | Small / Medium / Large の判定基準。本機能は外部 API 非依存のため Large 不要の根拠 |
