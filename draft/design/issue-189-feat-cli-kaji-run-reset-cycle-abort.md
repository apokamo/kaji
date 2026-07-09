# [設計] `kaji run --reset-cycle`: cycle exhaust (ABORT) 後の正規復旧経路

Issue: #189

## 概要

`kaji run` に `--reset-cycle` フラグを追加し、`--from <step>` と併用したときに、その step が属する cycle の `cycle_counts` だけを `0` に戻してから workflow を開始する。`on_exhaust: ABORT` で止まった workflow を、`session-state.json` の手動編集なしで再開できるようにする。

## 背景・目的

### 現状の問題（一次情報で確認）

`runner.py:552` の exhaust 判定は、step を dispatch する前に `state.cycle_iterations(cycle.name) >= cycle.max_iterations` を評価する。`cycle_counts` は `session-state.json` に永続化され、`--from` は開始 step を選ぶだけで counts に触れない。したがって exhaust 済みの state に対して `--from <cycle 内 step>` で再開すると、同じ step で再び合成 ABORT verdict が発行される。

実測（`.kaji-artifacts/184/`、Issue #184 の踏み台）:

- `session-state.json.bak`: `cycle_counts = {"ready-review": 3}` / `last_completed_step = "review-ready"`
- `runs/2605260005/run.log`: `Cycle 'ready-review' exhausted` を記録

現状の唯一の回避策は `session-state.json` の手動編集（`.bak` はその編集前バックアップ）であり、`cycle_counts` というフィールド名と履歴整合性を知る人間しか復旧できない。`kaji run --help` にも docs にも復旧手順が存在しない。

### ユーザーストーリー

- **maintainer として**、AI レビュアーが 3 連続 RETRY で cycle を exhaust させた後、Issue 本文や設計書を直したうえで `kaji run <wf> <issue> --from <step> --reset-cycle` 一発で再開したい。`session-state.json` を手で編集したくない。
- **kaji 利用者として**、`kaji run --help` と docs から復旧手順を発見したい。ソース読解を強いられたくない。
- **workflow 作者として**、`on_exhaust: ABORT` が「詰み」ではなく「人間介入ポイント」だと言い切れる状態にしたい。`max_iterations` を保守的に大きくして AI に粘らせる圧力を消したい。

### 代替案と不採用理由

| 案 | 不採用理由 |
|----|-----------|
| `--reset-cycle <cycle-name>` に値を取らせる | cycle 名は workflow YAML の `cycles:` セクションキー（`ready-review` 等）で、CLI 出力・ログの主役ではない。利用者は「どの step から再開したいか」は知っているが「その step がどの cycle 名に属するか」は知らない。`--from` から `find_cycle_for_step()` で導出できるため、値を要求するのは UX 上の劣化 |
| ABORT 時に自動で counts をリセット | `max_iterations` は「人間の介入を強制する」ための仕組み。自動リセットは無限 RETRY ループの穴になり、`on_exhaust` の意味を無効化する |
| docs に手動編集手順だけ書く | JSON の手編集は失敗しやすく（`step_history` / `last_completed_step` の整合も要る）、公式 UX として不適 |
| `cycle_counts` 全体を `{}` に wipe | 手動 workaround の実態だが、無関係な cycle の履歴まで消す。復旧対象の cycle だけを戻すほうが副作用が小さい |

## インターフェース

### 入力

**CLI（`kaji run`）**

| 引数 | 型 | 既定値 | 説明 |
|------|-----|--------|------|
| `--reset-cycle` | flag（`action="store_true"`、値なし） | `False` | `--from <step>` が属する cycle の反復回数を `0` に戻してから実行を開始する |

依存・排他規則:

- `--reset-cycle` は `--from` を **必須の相棒** とする（`--from` なしの単独指定はエラー）
- `--step` は既存規則により `--from` と排他。よって `--reset-cycle --step X` は「`--from` がない」として同じエラーに落ちる（追加の排他規則は不要）
- `--before` との併用は許容（`--from A --reset-cycle --before B` は「A の cycle を戻し、B の手前まで回す」）

**Python API（`WorkflowRunner`）**

| フィールド | 型 | 既定値 |
|-----------|-----|--------|
| `reset_cycle` | `bool` | `False` |

**`SessionState` の新 API**

```python
def reset_cycle(self, cycle_name: str) -> None:
    """指定 cycle の反復回数を 0 に戻し、即時永続化する。"""
```

### 出力

| 種別 | 内容 |
|------|------|
| 副作用 | `<artifacts_dir>/<canonical_id>/session-state.json` の `cycle_counts[<cycle>]` が `0` になる。`progress.md` も再生成される（`_persist()` 経由） |
| ログ | `run.log` に `cycle_reset` イベント（cycle 名・リセット前の値）。コンソールに `cycle reset: <cycle> (was N)` |
| 標準出力 | 変更なし（verdict 契約は不変） |
| 終了コード | 正常時は従来通り。誤用時は `EXIT_DEFINITION_ERROR`（`2`） |

`cycle_counts` **以外**（`step_history` / `last_completed_step` / `last_transition_verdict` / `sessions`）は書き換えない（→ 「制約・前提条件」参照）。

### 使用例

```bash
# ready-review cycle が 3 回 RETRY して ABORT した後、Issue 本文を修正してから再開する
kaji run .kaji/wf/dev.yaml 184 --from review-ready --reset-cycle

# cycle 内の loop step から再開してもよい（find_cycle_for_step は entry と loop の両方に一致する）
kaji run .kaji/wf/dev.yaml 184 --from fix-ready --reset-cycle

# --before との併用: code-review cycle を戻し、final-check の手前で停止する
kaji run .kaji/wf/dev.yaml 189 --from review-code --reset-cycle --before final-check
```

### エラー

| 条件 | 検出層 | 挙動 |
|------|--------|------|
| `--reset-cycle` を `--from` なしで指定 | `cmd_run`（config 探索より前） | stderr に `Error: --reset-cycle requires --from <step>` / exit `2` |
| `--from <step>` の step が workflow に存在しない | `runner.run()`（既存経路） | 既存の `WorkflowValidationError: Step '<step>' not found` / exit `2` |
| `--from <step>` の step がどの cycle にも属さない（linear step、または `cycles:` を持たない workflow） | `runner.run()` | `WorkflowValidationError: Step '<step>' does not belong to any cycle (--reset-cycle)` / exit `2` |
| `session-state.json` が存在しない（初回実行） | — | エラーにしない。`load_or_create()` が新規 state を作り、`cycle_counts[<cycle>] = 0` を書くだけ（実質 no-op） |

`--reset-cycle` が誤用エラーになるケースでは、**state を一切書き換えずに** 終了する（検証は state 書き込みより前に済ませる）。

## 制約・前提条件

- **reset の実行位置は `runner.run()` 内でなければならない**。`session-state.json` のパスは `<artifacts_dir>/<canonical_id>/` で決まり、`canonical_id` は `runner.run()` 内の `_resolve_run_issue_context()`（`runner.py:391`）で確定する。`cmd_run` の時点で持っているのは正規化前の生入力（`pc1-1` / `local-pc1-1` / `gh:N` などの表記ゆれ）なので、そこで state を触ると別ファイルを書きうる。よって **フラグ検証は `cmd_run`、state 変更は `runner.run()`** に分離する。
- **リセット対象は `--from` の step が属する cycle 1 個のみ**。他 cycle の counts は保持する。exhaust 判定は step ごとに `find_cycle_for_step(current_step.id)` で cycle を引くため、再開点の cycle さえ戻せば即時 ABORT は解消する。
- **`step_history` / `last_transition_verdict` は書き換えない**。手動 workaround は履歴末尾の ABORT 3 件を除去していたが、それは不要かつ有害（実行履歴の改竄）。理由: (a) 最初の step が dispatch されれば `record_step()` が `last_transition_verdict` を上書きする、(b) dispatch 前に `--before` barrier で止まる経路は `runner.py:909` が既に stale verdict を `None` に落として `cmd_run` の誤 ABORT 報告を抑止している。
- リセットは即時永続化する（`increment_cycle()` と同じ契約）。reset 直後にクラッシュしても、再実行時の意図（counts=0 から再開）と一致する。
- `--reset-cycle` は既定 `False`。既存の呼び出し・workflow YAML・state ファイル形式に変更はない（後方互換）。
- 依存追加なし（`argparse` / 既存 `kaji_harness` モジュールのみ）。

## 変更スコープ

| ファイル | 変更内容 |
|---------|---------|
| `kaji_harness/cli_main.py` | `_register_run()` に `--reset-cycle` 追加。`cmd_run()` に `--from` 依存ガード追加。`WorkflowRunner(...)` へ `reset_cycle=args.reset_cycle` を渡す |
| `kaji_harness/runner.py` | `WorkflowRunner.reset_cycle: bool = False` フィールド。`run()` の開始 step 決定後・メインループ前に reset 処理 |
| `kaji_harness/state.py` | `SessionState.reset_cycle()` を追加 |
| `kaji_harness/logger.py` | `log_cycle_reset()` を追加 |
| `tests/test_runner_reset_cycle.py` | 新規（`tests/test_runner_before.py` と同型） |
| docs | 「影響ドキュメント」参照 |

`kaji_harness/models.py` / `kaji_harness/workflow.py` は **読み取りのみ**（`find_cycle_for_step()` を使う）で変更しない。

## 方針

### データフロー

```
cmd_run(args)
  ├─ [新規ガード] args.reset_cycle and not args.from_step → stderr + exit 2
  ├─ config 探索 / provider 検証 / load_workflow  （既存・順序不変）
  └─ WorkflowRunner(..., reset_cycle=args.reset_cycle).run()
         ├─ skill preflight / validate_workflow            （既存）
         ├─ canonical_id 確定 → SessionState.load_or_create（既存, runner.py:402）
         ├─ run_dir / RunLogger 作成                        （既存, runner.py:450）
         ├─ 開始 step 決定（--step / --from / 先頭）        （既存, runner.py:454-464）
         ├─ [新規] _apply_cycle_reset(state, logger)
         └─ メインループ（exhaust 判定は runner.py:552）    （既存）
```

reset を「開始 step 決定の直後」に置く理由: `--from` step の存在検証（`Step '<x>' not found`）が既に済んでおり、かつ exhaust 判定（メインループ冒頭）より前だから。

### 疑似コード

```python
# runner.py
def _apply_cycle_reset(self, state: SessionState, logger: RunLogger) -> None:
    """--reset-cycle: from_step が属する cycle の反復回数を 0 に戻す。"""
    if not self.reset_cycle:
        return
    if not self.from_step:
        # CLI 層で弾いているが、プログラム的利用のための防御
        raise WorkflowValidationError("--reset-cycle requires --from <step>")
    cycle = self.workflow.find_cycle_for_step(self.from_step)
    if cycle is None:
        raise WorkflowValidationError(
            f"Step '{self.from_step}' does not belong to any cycle (--reset-cycle)"
        )
    previous = state.cycle_iterations(cycle.name)
    state.reset_cycle(cycle.name)
    logger.log_cycle_reset(cycle.name, previous)
    _console.info("cycle reset: %s (was %d)", cycle.name, previous)
```

```python
# state.py
def reset_cycle(self, cycle_name: str) -> None:
    """サイクルのイテレーション回数を 0 に戻し、即時永続化する。"""
    self.cycle_counts[cycle_name] = 0
    self._persist()
```

キー削除ではなく `0` 代入とする。`cycle_iterations()` は `.get(name, 0)` なので観測上は等価だが、`0` を残すほうが冪等で、`progress.md` の「サイクル」節と `log_workflow_end(cycle_counts)` に「リセット済み」という事実が現れる。

### 命名

`--reset-cycle`（単数）は「`--from` が指す 1 つの cycle を戻す」という意味論を表す。`--reset-cycles`（複数）は全 cycle wipe を連想させるため採らない。

## テスト戦略

### 変更タイプ

**実行時コード変更**（CLI 引数解析の分岐追加 + `session-state.json` への新しい書き込み経路）。

### Small テスト

外部依存なし。`create_parser()` / `cmd_run()` の早期ガード / `SessionState` の純ロジックを検証する。

- `--reset-cycle` が `args.reset_cycle` に parse され、未指定時の既定が `False` であること
- `kaji run --help` の出力（`parser.format_help()`）に `--reset-cycle` とその説明が含まれること（Issue 完了条件「help 出力に説明が含まれる」の直接検証）
- `--reset-cycle` を `--from` なしで渡すと `cmd_run()` が `EXIT_DEFINITION_ERROR`（`2`）を返し、stderr に `--reset-cycle requires --from` を出すこと。かつこのガードが config 探索より前に効く（不正な `--workdir` でも同じエラーになる）こと
- `--reset-cycle --step <x>`（`--from` なし）が同じガードに落ちること
- `SessionState.reset_cycle()`: `3 → 0` になる / 冪等（2 回呼んでも `0`）/ 他 cycle の counts を変えない / 未知の cycle 名でも例外を投げない

### Medium テスト

ファイル I/O（`session-state.json`）・`git init` した一時リポジトリ・`RunLogger` との結合を含む。`tests/test_runner_before.py` の fixture 構成（`_make_config` / `_make_runner`、dispatch を patch）を再利用する。

- **回帰の再現（対照群）**: `cycle_counts = {"rev": 3}` を書き込んだ `session-state.json` を用意し、`--from review`（`--reset-cycle` なし）で `run()` すると step が dispatch されず `Cycle 'rev' exhausted` の ABORT verdict で終わること。＝ Issue が報告した現象がテストとして固定される
- **実験群（本機能）**: 同じ state に対し `--from review --reset-cycle` で `run()` すると、ABORT せず `review` step が dispatch され、workflow が続行すること
- **state の事後条件**: 実行後の `session-state.json` で対象 cycle が `0` から数え直されていること、および**他 cycle の counts が保存されていること**（`{"rev": 3, "other": 2}` → `other` は `2` のまま）
- **cycle 外 step の誤用検知**: linear step（cycle に属さない step）を `--from` に与えて `--reset-cycle` すると `WorkflowValidationError` が送出され、`cmd_run()` 経由では exit `2` になること。かつ `session-state.json` の `cycle_counts` が**書き換わっていない**こと
- **`--from` 未指定での runner 直呼び**: `WorkflowRunner(reset_cycle=True, from_step=None).run()` が `WorkflowValidationError` になること（API 層の防御）
- **ログ証跡**: `run.log` に `cycle_reset` イベント（cycle 名・リセット前の値）が 1 件記録されること

Issue 完了条件の「E2E テスト」は、この Medium 群のうち「exhaust 済み state → `--from ... --reset-cycle` → 続行」を `cmd_run()` から駆動するケースが担う（agent dispatch のみ patch し、argparse → config → workflow load → runner → state 永続化までを実経路で通す）。

### Large テスト

**追加しない。** `docs/dev/testing-convention.md` の判定基準（「外部 API / 実サービス疎通あり → Large」）に照らして、本変更は Large に分類される依存を一切増やさないため。

- 新設する処理は「argparse のフラグ 1 個」「ローカル JSON への `0` 書き込み」「ログ 1 行」のみで、外部 API・実サービス・ネットワークへの新規疎通は発生しない
- `kaji run` のプロセス境界そのもの（実 CLI 起動）は既存の `tests/test_e2e_cli.py` / `large_local` 群が既に担保しており、本フラグを Large 化しても新しい回帰シグナルは増えない

「Small で十分」「実行時間が長い」といった理由での省略ではない点を明記する。上記 Medium 群が実行経路（`cmd_run` → `runner.run()` → `SessionState._persist()`）を実ファイル込みで通す。

### 品質ゲート

`source .venv/bin/activate && make check`（`ruff check` → `ruff format --check` → `mypy` → `pytest` 全体）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 新規ライブラリ採用・アーキテクチャ決定を伴わない。既存の cycle / state 機構に flag を 1 つ足すだけ |
| `docs/ARCHITECTURE.md` | **あり** | 「セッション管理と再開」節（`ARCHITECTURE.md:316-334`）。`cycle_counts` が永続化される事実は書かれているが、`--from` 単独では exhaust から復帰できないことが書かれておらず、「`--from` で再開できる」と読める。`--reset-cycle` の説明を追加し、誤読箇所を修正する |
| `docs/dev/workflow-authoring.md` | **あり** | 「実行コマンド」節（`:401` 付近）に例を追加。`--from` / `--step` / `--before` の比較表（`:428-434`）に `--reset-cycle` の行を追加。`on_exhaust` の説明（`:254` / `:267` 付近）に「ABORT からの復旧は `--from <step> --reset-cycle`」を追記 |
| `docs/dev/workflow_guide.md` | **あり** | 「途中開始・途中終了・単発実行」節（`:70` 以降）に cycle exhaust 復旧レシピを追加 |
| `docs/dev/` その他 | なし | ワークフロー定義・テスト規約そのものは不変 |
| `docs/reference/python/` | なし | コーディング規約に影響しない |
| `docs/cli-guides/github-mode.md` / `local-mode.md` | **なし** | 両ガイドは provider 固有の前提（`gh` / `glab` 認証、`.kaji/config.local.toml` overlay、ID 文法、provider ごとの `kaji issue` / `kaji pr` 挙動）を扱う。`--reset-cycle` は `session-state.json` というローカル成果物のみを操作し、provider に依存しない挙動を持つため、両ガイドに同じ手順を二重掲載すると単一情報源が壊れる。復旧手順の正本は `docs/dev/workflow_guide.md` に置く。※ 両ガイドの `kaji run` 記述（`local-mode.md:143` 等）は workflow 起動例であって flag 一覧ではなく、`--from` の誤読を招く記述も含まないため修正不要 |
| `README.md` / `llms.txt` | なし | `--from` の記述（`README.md:228` / `llms.txt:75`）は「途中のステップから再開する」例示に留まり、cycle exhaust 後の復旧可否には言及していない。誤読リスクなし |
| `AGENTS.md` / `CLAUDE.md` | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Python 公式ドキュメント `argparse` — `required` | https://docs.python.org/3/library/argparse.html#required | `required` は「その option 自体を必須にする」ためのもの。「他の option が指定されているときだけ必須」を宣言する機能はない。→ `--reset-cycle` の `--from` 依存は宣言的に書けず、`cmd_run` の手続き的ガードで実装する |
| Python 公式ドキュメント `argparse` — Mutual exclusion | https://docs.python.org/3/library/argparse.html#mutual-exclusion | `add_mutually_exclusive_group()` は「グループ内の引数のうち 1 つだけが存在すること」を保証する。表現できるのは排他であって依存ではない。→ 既存の `--from` / `--step` 排他が `cmd_run` の手書き分岐（`cli_main.py:385-399`）で実装されている理由と一致し、本 flag も同じ層に置くのが一貫する |
| Python 公式ドキュメント `argparse` — `action="store_true"` | https://docs.python.org/3/library/argparse.html#action | `store_true` は「値を取らない真偽フラグ」で既定 `False`。→ `--reset-cycle` を値なしフラグとする実装根拠（`--reset-cycle <cycle-name>` を採らない） |
| `kaji_harness/runner.py:552` | worktree 内ソース | `if cycle and state.cycle_iterations(cycle.name) >= cycle.max_iterations:` — exhaust 判定が dispatch より前、かつ step ごとに評価される。`cycle_counts` を 0 に戻せば同一 run 内で即時 ABORT は起きない |
| `kaji_harness/runner.py:391, 402` | worktree 内ソース | `run_ctx = self._resolve_run_issue_context()` → `SessionState.load_or_create(run_ctx.canonical_id, self.artifacts_dir)`。state ファイルの位置は canonical id に依存する。→ reset を `cmd_run`（生入力しか持たない）で行ってはならない根拠 |
| `kaji_harness/runner.py:909` | worktree 内ソース | `if barrier_hit and not step_dispatched and state.last_transition_verdict is not None: state.last_transition_verdict = None` — dispatch 前 barrier での stale verdict は既に抑止済み。→ `--reset-cycle` が `last_transition_verdict` を触る必要がない根拠 |
| `kaji_harness/models.py:107-112` | worktree 内ソース | `find_cycle_for_step()` は `step_id in cycle.loop or step_id == cycle.entry` で一致判定し、非該当は `None`。→ entry / loop いずれの step からも復旧でき、linear step は `None` で誤用検知できる |
| `kaji_harness/state.py:103-110` | worktree 内ソース | `cycle_iterations()` は `cycle_counts.get(name, 0)`、`increment_cycle()` は代入後に `_persist()`。→ `reset_cycle()` を「`0` 代入 + `_persist()`」として対称に置く根拠 |
| `kaji_harness/cli_main.py:50-56, 385-399` | worktree 内ソース | `EXIT_DEFINITION_ERROR = 2`。既存の相互排他違反は stderr 出力 + `EXIT_DEFINITION_ERROR` で返す。→ `--reset-cycle` 誤用も同じ終了コード契約に揃える |
| `.kaji-artifacts/184/session-state.json.bak` | main worktree 内成果物 | 手動編集前の実測値: `cycle_counts = {"ready-review": 3}` / `last_completed_step = "review-ready"`。→ 本 Issue が解く failure mode の実在証明 |
| `.kaji-artifacts/184/runs/2605260005/run.log` | main worktree 内成果物 | `Cycle 'ready-review' exhausted` を記録。`--from` での再実行が即 ABORT した証跡 |
| `.kaji/wf/dev.yaml:9-44` | worktree 内 workflow 定義 | 7 つの cycle がすべて `max_iterations: 3` / `on_exhaust: ABORT`。`ready-review` は `entry: review-ready` / `loop: [fix-ready, review-ready]`。→ 使用例の妥当性根拠 |
| `docs/dev/testing-convention.md`（判定基準・省略理由） | worktree 内 docs | 「外部 API / 実サービス疎通あり → Large」「DB / ファイル / 内部サービス結合あり → Medium」。および不正当な省略理由の一覧。→ Large 省略を「外部依存を増やさないため分類上 Large に該当しない」として正当化し、「Small で十分」型の省略を避ける根拠 |
| `tests/test_runner_before.py` | worktree 内テスト | `--before`（同種の `kaji run` フラグ追加）の既存テスト構成。Small: argparse / 排他、Medium: `_make_config` + `git init` + dispatch patch で `run()` を駆動。→ 本 Issue のテスト構成の先例 |
