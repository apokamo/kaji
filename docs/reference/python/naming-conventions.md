# 命名規則

kaji における統一的な命名規則。Python コード・ファイル・設定項目をカバーする。

## 基本原則

- **明確性**: 名前から目的・役割が即座に理解できる
- **一貫性**: プロジェクト全体で統一したパターンを使用する
- **検索性**: grep / IDE 検索で容易に発見できる（略語・独自語を避ける）
- **簡潔性**: 必要十分な長さ。ただし省略しすぎて意味が失われるより冗長な方を取る

## Python コード命名

### 基本パターン

| 対象 | 規則 | 例 | 備考 |
|------|------|-----|------|
| 変数 | `snake_case` | `step_id`, `session_state` | 名詞・名詞句 |
| 関数 | `snake_case` | `execute_step()`, `parse_verdict()` | 動詞で開始 |
| クラス | `PascalCase` | `WorkflowRunner`, `SessionState` | 名詞・名詞句 |
| 定数 | `UPPER_SNAKE_CASE` | `DEFAULT_TIMEOUT`, `MAX_RETRIES` | モジュールレベルのみ |
| モジュール | `snake_case` | `workflow.py`, `verdict.py` | 短く・明確に |
| パッケージ | `snake_case` | `kaji_harness/` | ハイフン不可 |

### プライベート・保護

```python
# モジュール内プライベート（外部から使わない）
def _internal_helper() -> None:
    ...

_INTERNAL_CONSTANT = 42

# クラス内プライベート（サブクラスは触ってよい）
class RunLogger:
    def _write(self, event: str, **kwargs: Any) -> None:
        ...
```

名前マングリング（`__double_leading`）は禁止。テストやモックが著しく困難になる。

### 動詞の使い分け

Python 標準ライブラリ・広く採用されている命名慣習に合わせる。

#### データ取得

```python
def get_step(step_id: str) -> Step | None:                # ID 指定で単一取得（失敗時 None）
def find_cycle_for_step(step_id: str) -> Cycle | None:    # 条件検索
def load_workflow(path: Path) -> Workflow:                # ファイルから読み込み
```

#### データ作成・処理

```python
def create_session(step_id: str) -> SessionState:         # 新規作成
def run_step(step: Step, issue: int) -> Verdict:          # 実行・処理（副作用あり）
def execute_cli(cmd: list[str]) -> CLIResult:             # 外部プロセス実行
def build_command(step: Step, issue: int) -> list[str]:   # オブジェクト構築（副作用なし）
```

#### 解析・変換

```python
def parse_verdict(output: str) -> Verdict:                # テキストから構造体へ
def extract_session_id(output: str) -> str | None:        # 特定情報の抽出
def format_step_summary(step: Step) -> str:               # 表示用文字列へ
```

#### 判定・検証

```python
def is_terminal_status(status: str) -> bool:              # 状態判定（bool）
def validate_workflow(workflow: Workflow) -> None:         # 検証（失敗時 raise）
def has_cycle(workflow: Workflow, step_id: str) -> bool:  # 存在確認
```

## kaji 固有語の統一

kaji ドメインで使用する語彙は以下に統一する。AI エージェントが一貫して使用すること。

| 概念 | 統一語 | 禁止・非推奨 |
|------|--------|------------|
| ワークフロー実行単位 | `step` | task, job, operation |
| ステップ判定結果 | `verdict` | result, outcome, decision |
| 繰り返し実行 | `cycle` | loop, iteration（変数名としての `iteration` は可） |
| スキルファイル | `skill` | script, prompt, template |
| 実行エンジン | `harness` | runner（クラス名 `WorkflowRunner` は可） |
| ワークフロー定義 | `workflow` | pipeline, process |
| セッション状態 | `session_state` | context, state（単独の `state` は可） |

### 使用例

```python
# step 関連
step_id: str
current_step: Step
find_step(step_id: str) -> Step | None

# verdict 関連
verdict: Verdict
parse_verdict(output: str) -> Verdict
verdict_status: str  # "PASS", "FAIL", "ABORT" 等

# cycle 関連
cycle: CycleDefinition
cycle_count: int
max_iterations: int  # cycle 内の反復回数は iterations が自然

# skill 関連
skill_path: Path
resolve_skill(name: str) -> Path

# workflow 関連
workflow: Workflow
workflow_name: str
load_workflow(path: Path) -> Workflow
```

## ファイル・ディレクトリ命名

### kaji_harness/ モジュール構造

```
kaji_harness/
├── __init__.py
├── cli.py              # CLI エントリポイント
├── runner.py           # WorkflowRunner 実装
├── workflow.py         # Workflow 解析・検証
├── executor.py         # Step 実行ロジック
├── verdict.py          # Verdict 解析
├── logger.py           # RunLogger 実装
├── models.py           # dataclass 定義
├── errors.py           # 例外クラス定義
└── config.py           # 設定読み込み
```

### テストファイル

テストファイルは `tests/test_[module].py` の形式。

```
tests/
├── test_runner.py
├── test_workflow.py
├── test_verdict.py
└── conftest.py
```

### ワークフロー・スキルファイル

```
workflows/
├── feature_development.yaml
└── bugfix.yaml

.claude/skills/
├── issue-start/
│   └── ...
└── issue-implement/
    └── ...
```

## 略語・頭字語

### 推奨される略語

```python
id          # identifier
cli         # command-line interface
yaml        # YAML Ain't Markup Language
cfg         # config（設定オブジェクトの変数名のみ）
```

### 避けるべき略語

```python
# ❌ 理解困難な略語
mgr         # manager → manager のまま
proc        # process → process のまま
tmp         # temporary → temp または temporary
wf          # workflow → workflow のまま
```

## チェックリスト

### 実装時チェック

- [ ] 名前から目的・役割が明確か
- [ ] kaji 固有語が統一されているか（上記テーブル参照）
- [ ] 動詞の使い分けが一貫しているか（`get_` / `find_` / `parse_` 等）
- [ ] プライベートメソッドが `_` 始まりか
- [ ] 略語が推奨リスト内か

### レビュー時チェック

- [ ] 同じ概念に対して異なる名前を使っていないか
- [ ] grep・IDE 検索で発見しやすいか
- [ ] kaji 固有語が統一されているか

## 関連ドキュメント

- [Python スタイル規約](./python-style.md) — 基本的なコーディングスタイル
- [型ヒント](./type-hints.md) — 型名・変数名の型注釈
- [docstring スタイル](./docstring-style.md) — docstring での命名記述
