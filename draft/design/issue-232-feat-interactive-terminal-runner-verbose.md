# [設計] interactive_terminal runner の pane 起動 INFO progress に step/agent/timeout/verdict path を追加する

Issue: #232

## 概要

`interactive_terminal` runner が pane 起動成功直後に出す `[kaji] pane launched: ...` INFO progress に、不足している `step id` / `agent 名` / `timeout 秒数` を追記し、親コンソール 1 行で「どの step の agent が、どの pane で、どれだけの timeout を待っているか」を追跡できるようにする小さな feature。

## 背景・目的

### ユースケース

- **運用者として**、複数 pane が並走する interactive terminal セッションで、親コンソールだけで進捗を把握するために、`step start` → `pane launched` → `step end` の流れの中で「起動した agent 名・timeout・verdict 待機対応」を 1 行で確認したい。

interactive terminal runner では agent 出力が tmux pane 側へ出るため、親コンソール（`kaji run` を叩いた origin pane）には harness 自身の progress しか残らない。#235 で `step start` / `step end` と `pane launched` が導入されたが、現行の `pane launched` 行は `pane id` と `verdict path` しか持たない（`kaji_harness/interactive_terminal.py` の `_console.info("pane launched: %s pane=%s verdict=%s", step.id, pane_id, verdict_path)`）。

そのため親コンソール側では次が欠落している。

- 起動した agent 名（`claude` / `codex`）
- timeout 秒数（その pane の verdict 待機がいつ打ち切られるか）
- step との対応関係（`pane launched` 行単体では step id がラベル無しの第 1 引数に埋もれており、`step=` キーが無い）

本 Issue は **既存の `pane launched` INFO progress に情報を足すだけ** に閉じる。新しい CLI option / logger / stdout print は追加しない。

### 代替案と不採用理由

- **verdict detected 行や別 logger を新設して情報を載せる**: スコープ外。`pane launched` 行に集約する方が、`step start`（agent/model）→ `pane launched`（pane/timeout/verdict）→ `step end`（status/duration）という既存の 3 行構成と整合し、追加表面積が最小。Issue のスコープ境界でも明示的に除外されている。
- **`pane launched` 行を構造化ログ（JSON）化する**: #235 が確立した「日時付き `[kaji]` human-readable progress」という既存フォーマットを壊す。既存行と表記体系を揃える `key=value` 追記が後方互換かつ最小。

## インターフェース

### 入力

本変更は新規の公開 IF を追加しない。`execute_interactive_terminal()` のシグネチャは不変で、進捗行の生成に使う値はすべて呼び出し時点で確定済み。

- `step.id`（既出・既に第 1 引数として出力中）
- `step.agent`（`execute_interactive_terminal` 冒頭で `claude` / `codex` のいずれかに validation 済み。`None` や未対応値はここに到達しない）
- `pane_id`（`_launch_pane` の戻り値 `_PaneLaunch.pane_id`、既出）
- `timeout: int`（`execute_interactive_terminal` の既存引数。単位は秒）
- `verdict_path: Path`（既存引数、既出）

### 出力

副作用は **`kaji.interactive_terminal` logger への INFO レコード 1 件の文言変更のみ**。

変更前（現行）:

```text
[2026-06-07T12:35:02] [kaji] pane launched: design pane=%12 verdict=/.../steps/design/attempt-001/verdict.yaml
```

変更後（本 Issue）:

```text
[2026-06-07T12:35:02] [kaji] pane launched: step=design agent=claude pane=%12 timeout=1800s verdict=/.../steps/design/attempt-001/verdict.yaml
```

- フィールド順は `step` → `agent` → `pane` → `timeout` → `verdict`（Issue「到達したい状態」の例と一致）。
- 全フィールドを `key=value` 形式に統一する（現行は先頭 step id だけラベル無しだったのを `step=` 付きに揃える）。
- `timeout` は `=<int>s` 形式（秒サフィックス付き）。`step end` 行の `duration=...ms` と単位サフィックス方針を揃える。

### 使用例

呼び出し側のコードは変わらない。runner 内部の 1 行のみ変わる。

```python
# kaji_harness/interactive_terminal.py（_launch_pane 成功直後）
pane_id = launch.pane_id
_console.info(
    "pane launched: step=%s agent=%s pane=%s timeout=%ds verdict=%s",
    step.id,
    step.agent,
    pane_id,
    timeout,
    verdict_path,
)
```

`logging` の遅延 `%` フォーマット（引数を渡し、f-string で組み立てない）を維持する（`docs/reference/python/logging.md` 準拠）。

### エラー

新たな失敗経路は無い。`step.agent` は本行到達前に validation 済みのため `None` 混入はなく、format 引数はすべて非 optional。logger 呼び出し自体は例外を投げない。

## 制約・前提条件

- 依存追加なし。stdlib `logging` のみ。
- `pane launched` 行は **pane 起動成功直後・`pipe-pane` 前** に出る現行の出力位置を変更しない（情報の追加のみ）。
- `--quiet` / `verbose` / `--log-level` の意味は変更しない。interactive runner には headless の `verbose` 概念を持ち込まない（Issue 方針どおり）。
- 後方互換: `pane launched` 行を機械パースしている既存コード/テストは存在しない（`grep` で `tests/` 内に `pane launched` 参照なしを確認済み）。docs の表示例のみが固定値を持つ。

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/interactive_terminal.py` | `pane launched` INFO 行の format 文字列と引数に `step=` / `agent=` / `timeout=Ns` を追加 |
| `tests/test_interactive_terminal.py` | `pane launched` progress の内容を `caplog` で固定する回帰テストを追加 |
| `docs/cli-guides/interactive-terminal-runner.md` | 起動コンソール progress の表示例（line 132 付近）を新フォーマットへ更新 |

スコープ外（Issue のスコープ境界に従う）: `step start` / `step end` の新規実装、`--quiet` の意味変更、`verbose` 伝播、`--log-level` 拡張、verdict wait heartbeat、verdict detected 行拡張、completion barrier、#238 の pane 配置再実装。

## 方針

`execute_interactive_terminal()` 内の唯一の `pane launched` 出力箇所（`_console.info(...)`）を、format 文字列と渡す引数だけ差し替える。出力タイミング・logger・log level（INFO）は不変。`timeout` と `step.agent` は同関数のローカルスコープに既に存在するため、追加の引数受け渡しや関数シグネチャ変更は不要。

## テスト戦略

### 変更タイプ

実行時コード変更（progress logging の出力文言を変える runtime 挙動変更）。

#### Small テスト

- **新規回帰テスト**: `tests/test_interactive_terminal.py` の `TestRunnerPaneLifecycle` に、`pane launched` progress の内容を固定するテストを 1 件追加する。検証観点:
  - `caplog`（logger `kaji.interactive_terminal`, level INFO）に出る `pane launched` レコードが `step=<step.id>` / `agent=<step.agent>` / `pane=<pane_id>` / `timeout=<timeout>s` / `verdict=<verdict_path>` の全フィールドを含むこと。
  - フィールド順が `step → agent → pane → timeout → verdict` であること（部分文字列 or 正規表現で固定）。
  - 既存の `_make_fake_tmux` / `subprocess.run` patch fixture を流用し、tmux 実体に依存しない（外部依存なし＝Small）。
- 既存の `TestRunnerPaneLifecycle` テスト群（verdict kill / metadata / 戻り値）は本変更で挙動が変わらないため、回帰確認として実行する。

#### Medium テスト

- 不要。本変更はファイル I/O / 内部サービス結合を新たに伴わない。pane 起動・transcript pipe・verdict polling の結合経路は既存 Medium 相当テスト（`_make_fake_tmux` ベース）で既にカバーされており、本変更は同経路に流れる 1 行の文言のみを変える。`docs/dev/testing-convention.md` の 4 条件（独自ロジック追加なし／不具合パターンは Small + 既存ゲートで捕捉／新規 Medium で回帰情報が増えない／本節が理由）を満たす。

#### Large テスト

- 不要。実 tmux / 実 agent CLI 疎通は本変更の検証に寄与しない（変わるのは logger 文言のみで、tmux 実挙動には非依存）。`docs/dev/testing-convention.md` の 4 条件を満たす（実 API 疎通ロジックの追加なし／文言は Small で完全固定可能／Large で増える回帰情報なし／本節が理由）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | なし | ワークフロー・テスト規約変更なし |
| docs/reference/ | なし | API 仕様・規約変更なし（logging 規約は既存どおり遵守） |
| docs/cli-guides/ | **あり** | `interactive-terminal-runner.md` の起動コンソール progress 表示例を新フォーマットへ更新（完了条件で要求） |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 現行実装の `pane launched` 出力 | `kaji_harness/interactive_terminal.py`（`execute_interactive_terminal` 内 `_console.info("pane launched: %s pane=%s verdict=%s", step.id, pane_id, verdict_path)`） | 変更対象の唯一の出力箇所。`step.agent` は同関数冒頭で `claude` / `codex` に validation 済み、`timeout` は引数として在席。追加情報はすべて当該スコープ内で取得可能 |
| #235 progress logging の表示例 | `docs/cli-guides/interactive-terminal-runner.md` 「起動コンソール progress（Issue #235）」節（line 129–145） | `step start` / `pane launched` / `step end` の既存 3 行フォーマットと `key=value` 表記体系。本変更が後方互換に追記すべき書式の根拠 |
| logging 規約（遅延フォーマット） | `docs/reference/python/logging.md` | logger 呼び出しは `%` プレースホルダで引数を渡す（f-string で事前組み立てしない）。新 format 文字列もこの規約に従う |
| テスト規約（変更タイプ別検証） | `docs/dev/testing-convention.md` § 変更タイプごとの期待値 / § 省略してよい理由 | 実行時コード変更は原則 Small で検証観点を定義。Medium / Large 省略の 4 条件の根拠 |
