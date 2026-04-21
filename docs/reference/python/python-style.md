# Python スタイル規約

kaji における Python コードのスタイル規約。PEP 8 を基本とし、プロジェクト固有の要件を追加する。

## 基本方針

- **明示性 > 暗黙性 > 簡潔性** の優先順位
- **型安全性**を重視（型ヒントは必須。詳細は [type-hints.md](./type-hints.md)）
- **AI 協調開発**を意識した、grep しやすい明確な規約
- **ruff / mypy / pytest による自動チェックで担保**（手動では守れない規約は書かない）

手動フォーマットや個別のスタイル議論は許容しない。すべて `make check` 経由で機械的に判定する。

## フォーマット規則

ruff が自動適用する。以下は規約としての意図を説明するもので、手で整える必要はない。

### インデント・行長

- インデント: スペース 4 つ（タブ禁止）
- 行長: 100 文字（`pyproject.toml` の `[tool.ruff] line-length = 100`）
- 文字列のエスケープを避けるため、ダブルクォート `"` を優先

```python
def execute_step(
    step: Step,
    state: SessionState,
    config: KajiConfig,
) -> Verdict:
    """ステップを実行し verdict を返す。"""
    ...
```

長い文字列は括弧で囲んで連結する。`+` 連結や改行文字の埋め込みは避ける。

```python
message = (
    f"Step '{step.id}' failed after {duration_ms}ms: "
    f"exit code {returncode}, stderr: {stderr[:200]}"
)
```

### インポート順序

ruff の `I` ルールで自動整列される。手動で整える必要はないが、規約上は以下の 3 分類。

```python
"""モジュール docstring（必須）."""

from __future__ import annotations

# 1. 標準ライブラリ（アルファベット順）
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 2. サードパーティライブラリ（アルファベット順）
import yaml

# 3. 自プロジェクトのインポート（相対パス禁止）
from kaji_harness.errors import HarnessError, VerdictNotFound
from kaji_harness.models import Step, Verdict

# 4. モジュールレベル定数
DEFAULT_TIMEOUT = 300
MAX_RETRY_COUNT = 3
```

`from __future__ import annotations` は全モジュールに付与する。前方参照エラーを防ぎ、型アノテーションの評価を遅延させる。

## 命名規則

詳細は [naming-conventions.md](./naming-conventions.md) を参照。基本パターンのみ掲載する。

| 対象 | 規則 | 例 |
|------|------|-----|
| 変数・関数 | `snake_case` | `step_id`, `parse_verdict()` |
| クラス | `PascalCase` | `WorkflowRunner`, `SessionState` |
| 定数 | `UPPER_SNAKE_CASE` | `DEFAULT_TIMEOUT` |
| モジュール | `snake_case` | `workflow.py`, `runner.py` |

## コメント・文字列

コメントはコードで表現できない「なぜ」のみ書く。動作の説明はコードと docstring で行う。

```python
# SIGTERM 後に SIGKILL を送る（プロセスが catch している場合への対処）
process.kill()
```

一時的な制限は TODO とともに理由・期限を明記する。

```python
# TODO: claude-code が --no-cache をサポートしたら削除
if not use_cache:
    env["DISABLE_CACHE"] = "1"
```

## 関数・クラス設計

### 単一責任

1 つの関数は 1 つのことだけ行う。引数が 5 個を超えたら dataclass にまとめる。

```python
@dataclass
class StepRunConfig:
    step: Step
    issue: int
    workdir: Path
    timeout: int = 300
    quiet: bool = False

def run_step(config: StepRunConfig) -> Verdict:
    """ステップを実行し verdict を返す。"""
    ...
```

### 継承よりコンポジション

```python
@dataclass
class WorkflowRunner:
    """ワークフロー実行エンジン。"""

    config: KajiConfig
    logger: RunLogger

    def run(self, workflow: Workflow, issue: int) -> None:
        """ワークフローを実行する。"""
        self.logger.log_workflow_start(issue, workflow.name)
        ...
```

## エラーハンドリング

詳細は [error-handling.md](./error-handling.md) を参照。概要のみ掲載する。

- カスタム例外は `HarnessError` 階層を使用する
- 例外の握り潰しは禁止
- 上位概念の例外で catch して下位概念に wrap する

```python
try:
    result = _call_cli(step)
except FileNotFoundError as e:
    raise CLINotFoundError(f"CLI not found: {step.agent}") from e
```

## ロギング

詳細は [logging.md](./logging.md) を参照。`RunLogger` 経由でのみ JSONL ログを出力する。標準 `logging` モジュールの直接使用は避ける。

## チェックリスト

### コーディング完了時

- [ ] `from __future__ import annotations` が先頭にあるか
- [ ] 全ての関数・クラスに型ヒントが付与されているか
- [ ] docstring が Google style で記述されているか（詳細: [docstring-style.md](./docstring-style.md)）
- [ ] 例外処理が適切に実装されているか
- [ ] `make check` が全パスするか

### コードレビュー時

- [ ] [命名規則](./naming-conventions.md) に従っているか
- [ ] 関数の責任が明確で単一か
- [ ] エラーハンドリングが網羅されているか
- [ ] `Any` 型の使用が最小限か

## 関連ドキュメント

- [型ヒント](./type-hints.md) — 型注釈の詳細ガイド
- [命名規則](./naming-conventions.md) — より詳細な命名パターン
- [docstring スタイル](./docstring-style.md) — docstring の書き方
- [エラーハンドリング](./error-handling.md) — 例外処理の実装パターン
- [ロギング](./logging.md) — RunLogger の使い方
