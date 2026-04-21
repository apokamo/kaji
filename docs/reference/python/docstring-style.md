# docstring スタイル規約

kaji における docstring・コメントの記述規約。Google Style docstring を採用する。

一次情報: [Google Python Style Guide §3.8](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)

## 基本方針

- **Google Style 準拠**: CLAUDE.md 既定の「Google docstrings」に従う
- **型ヒントと重複しない**: 型情報は型アノテーションで表現し、docstring では意味・制約を記述する
- **AI エージェントが参照する規約**: grep しやすく、機械的に適用できる粒度で記述する
- **モジュール冒頭の docstring は必須**（PEP 257 準拠）

## モジュールの docstring

全モジュールに 1 行以上の docstring を付与する。

```python
"""Run logger for kaji_harness.

JSONL format execution logger with immediate flush.
"""
```

複数行が必要な場合は 1 行サマリー → 空行 → 詳細の順。

```python
"""Workflow execution engine for kaji_harness.

Reads a workflow YAML, resolves steps and cycles, and executes them
in order. Resumes from a specific step if --from is specified.
"""
```

## 関数・メソッドの docstring

### 基本構造

```python
def parse_verdict(output: str) -> Verdict:
    """ステップ出力から verdict ブロックを解析する。

    ---VERDICT--- / ---END_VERDICT--- の間のテキストを抽出し、
    status / reason / evidence / suggestion を返す。

    Args:
        output: ステップの標準出力全体。

    Returns:
        解析した Verdict オブジェクト。

    Raises:
        VerdictNotFound: ---VERDICT--- ブロックが存在しない場合。
        VerdictParseError: 必須フィールドが欠損している場合。
    """
    ...
```

### 各セクションのルール

| セクション | 必須 | 説明 |
|---------|------|------|
| 1 行サマリー | 必須 | 動詞で始める。末尾に `。` |
| 詳細説明 | 任意 | サマリーで伝わらない前提・挙動を記述 |
| `Args:` | 必須（引数があれば） | 各引数の意味・制約 |
| `Returns:` | 必須（None 以外を返す関数） | 戻り値の意味。型は型ヒントに任せる |
| `Raises:` | 必須（raise する例外がある場合） | どの例外をいつ raise するか |
| `Example:` | 任意 | 使用例（doctest 形式は不要） |
| `Note:` | 任意 | 注意事項・制約・非自明な挙動 |

### Args セクション

型情報は型ヒントで表現するため、Args では意味・制約を記述する。

```python
def run_step(
    step: Step,
    issue: int,
    workdir: Path,
    timeout: int = 300,
) -> Verdict:
    """ステップを実行し verdict を返す。

    Args:
        step: 実行対象のステップ定義。
        issue: GitHub Issue 番号。スキルへの引数として渡される。
        workdir: ステップを実行するディレクトリ。存在しない場合は raise。
        timeout: タイムアウト秒数。0 以下の場合は無制限。

    Returns:
        ステップ出力から解析した Verdict。

    Raises:
        WorkdirNotFoundError: workdir が存在しない場合。
        StepTimeoutError: timeout 秒を超えた場合。
        VerdictNotFound: 出力に ---VERDICT--- ブロックがない場合。
    """
```

引数が 1 つの場合でも Args セクションを付ける。

### Returns セクション

複数の値を返す場合は内容を明示する。

```python
def extract_session_id(output: str) -> str | None:
    """出力から Claude session ID を抽出する。

    Returns:
        session_id 文字列。出力に含まれない場合は None。
    """
```

### Raises セクション

`HarnessError` の具体的なサブクラスを記述する。`Exception` のような基底クラスは使わない。

```python
    Raises:
        ConfigNotFoundError: .kaji/config.toml が見つからない場合。
        ConfigLoadError: TOML の解析・検証に失敗した場合。
```

## クラスの docstring

クラス docstring は `__init__` に書かず、クラス本体に書く。`Attributes:` セクションで主要な属性を説明する。

```python
@dataclass
class RunLogger:
    """JSONL 形式の実行ログを出力するクラス。

    ファイルへの書き込みは即時 flush する（プロセスが途中終了しても
    ログが残るようにするため）。

    Attributes:
        log_path: JSONL ファイルの書き込み先パス。
    """

    log_path: Path
```

```python
@dataclass
class WorkflowRunner:
    """ワークフロー実行エンジン。

    Attributes:
        config: .kaji/config.toml の設定。
        logger: 実行ログの出力先。None の場合はログを出力しない。
    """

    config: KajiConfig
    logger: RunLogger | None = None
```

## 短い関数の docstring

5 行以下の関数は 1 行 docstring で十分。

```python
def is_terminal_status(status: str) -> bool:
    """status が終端（以降のステップなし）かどうかを返す。"""
    return status in ("END", "ABORT")
```

ただし `Raises` がある場合は複数行に展開する。

```python
def find_step(self, step_id: str) -> Step:
    """ID でステップを検索する。

    Raises:
        KeyError: step_id に対応するステップが存在しない場合。
    """
    ...
```

## インラインコメント

コードで表現できない「なぜ」のみ書く。「何をしているか」はコードと docstring で伝える。

```python
# SIGTERM 後に SIGKILL を送る（プロセスが SIGTERM を catch している場合への対処）
process.kill()

# step.on が空の場合は workflow 終了（None を返すことで呼び出し側が END と判断）
return None
```

一時的な制約は TODO + 理由で記録する。

```python
# TODO: claude-code が --no-cache をサポートしたら削除 (#123)
env.pop("CLAUDE_CODE_CACHE", None)
```

## 型情報と重複しない書き方

型ヒントで伝わる情報を Args に繰り返さない。

```python
# ❌ 型ヒントと重複
def get_step(step_id: str) -> Step | None:
    """ステップを取得する。

    Args:
        step_id (str): ステップ ID。  # 型ヒントで明らか
    Returns:
        Step | None: ステップまたは None。  # 型ヒントで明らか
    """

# ✅ 意味・制約のみ記述
def get_step(step_id: str) -> Step | None:
    """ステップを取得する。

    Args:
        step_id: workflow 内で一意なステップ ID。

    Returns:
        見つかった Step。存在しない場合は None（raise しない）。
    """
```

## チェックリスト

### 実装時チェック

- [ ] モジュール冒頭に docstring があるか
- [ ] 全クラス・全公開関数に docstring があるか
- [ ] 1 行サマリーが動詞で始まるか
- [ ] `Args:` / `Returns:` / `Raises:` が必要な場合に記載されているか
- [ ] 型情報の重複がないか（型は型ヒントに任せているか）

### レビュー時チェック

- [ ] docstring がコードの内容と一致しているか
- [ ] `Raises:` に具体的な例外クラスが記載されているか（`HarnessError` のサブクラス）
- [ ] 非自明な挙動・制約が `Note:` または本文に記載されているか

## 関連ドキュメント

- [Python スタイル規約](./python-style.md) — 全般的なコーディング規約
- [命名規則](./naming-conventions.md) — 関数・変数名の命名パターン
- [型ヒント](./type-hints.md) — 型アノテーションとの分担
