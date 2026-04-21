# 型ヒント

kaji における型ヒント（Type Hints）の規約。Python 3.11+ を前提とする。

## 基本原則

- **全関数・メソッドに型ヒント必須**（引数・戻り値ともに）
- **`Any` 型の使用を避ける**（本当に不可避の場合はコメントで理由を記載）
- **`from __future__ import annotations` を全モジュールに付与する**
- **mypy が通ることを品質基準とする**（`make check` で確認）

## 基本的な型ヒント

### プリミティブ型

Python 3.11+ 記法を標準とする。`typing.Optional` / `typing.Union` は使わない。

```python
from __future__ import annotations

# プリミティブ型
step_id: str = "review"
timeout: int = 300
cost_usd: float = 0.05
is_quiet: bool = False

# None を許可する型（X | None を使用）
session_id: str | None = None
model: str | None = None
```

### コレクション型

```python
from __future__ import annotations

# Python 3.9+ 組み込み型を使用（typing.List 等は使わない）
step_ids: list[str] = ["design", "implement"]
transitions: dict[str, str] = {"PASS": "next", "FAIL": "fix"}
unique_agents: set[str] = {"claude", "codex"}

# タプル（固定長）
point: tuple[int, int] = (1, 2)

# より抽象的な型（読み取り専用の場合）
from collections.abc import Sequence, Mapping

def process_steps(steps: Sequence[Step]) -> None: ...  # list, tuple 両方受け入れ
def read_config(cfg: Mapping[str, str]) -> None: ...   # dict の読み取り専用版
```

### Any 型

```python
from __future__ import annotations
from typing import Any

# ❌ 避けるべき
def _write(self, event: str, **kwargs: Any) -> None: ...  # kwargs は Any が不可避

# ✅ 不可避な場合はコメントで理由を記載
def _write(self, event: str, **kwargs: Any) -> None:
    # JSONL の値型は多様（str/int/float/None/dict）のため Any
    entry: dict[str, Any] = {"event": event, **kwargs}
```

## dataclass パターン

kaji は Pydantic を使用しない。データクラスには `@dataclass` を使用する。

### 基本 dataclass

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Step:
    """ワークフロー内の 1 ステップ定義。"""

    id: str
    skill: str
    agent: str
    model: str | None = None
    effort: str | None = None
    max_budget_usd: float | None = None
    max_turns: int | None = None
    timeout: int | None = None
    workdir: str | None = None
    resume: str | None = None
    inject_verdict: bool = False
    on: dict[str, str] = field(default_factory=dict)
```

### mutable default は field(default_factory=...) を使う

```python
# ❌ NG: mutable default は共有される
@dataclass
class Workflow:
    steps: list[Step] = []  # mypy / dataclass が警告

# ✅ OK
@dataclass
class Workflow:
    steps: list[Step] = field(default_factory=list)
```

### dataclass の比較・ハッシュ

```python
from dataclasses import dataclass

@dataclass(eq=True, frozen=True)  # イミュータブルにする場合
class VerdictKey:
    step_id: str
    status: str
```

## Union 型・Literal 型

```python
from __future__ import annotations
from typing import Literal

# Literal: 特定の値のみ許可
VerdictStatus = Literal["PASS", "FAIL", "ABORT", "RETRY", "BACK"]

def next_step(status: VerdictStatus, on: dict[str, str]) -> str | None:
    return on.get(status)

# Union: 複数の型を許可（Python 3.10+ の | 記法）
StepRef = str | Step  # 名前または Step オブジェクト
```

## Callable 型

```python
from __future__ import annotations
from collections.abc import Callable

# コールバック型
StepHook = Callable[[Step, Verdict], None]

def run_with_hook(step: Step, hook: StepHook | None = None) -> Verdict:
    verdict = _execute(step)
    if hook is not None:
        hook(step, verdict)
    return verdict
```

## TypeAlias（Python 3.10+）

複雑な型を分かりやすくする。

```python
from __future__ import annotations
from typing import TypeAlias

# 型エイリアス定義
StepId: TypeAlias = str
VerdictStatus: TypeAlias = str
TransitionMap: TypeAlias = dict[str, str]  # {verdict_status: next_step_id}

def find_transition(step_id: StepId, status: VerdictStatus, on: TransitionMap) -> StepId | None:
    return on.get(status)
```

## Protocol（構造的サブタイピング）

```python
from __future__ import annotations
from typing import Protocol


class Runnable(Protocol):
    """実行可能なオブジェクトのプロトコル。"""

    def run(self, issue: int) -> None: ...


class WorkflowRunner:
    def run(self, issue: int) -> None:
        ...  # Runnable に適合
```

## Generic 型

```python
from __future__ import annotations
from typing import Generic, TypeVar

T = TypeVar("T")


class Result(Generic[T]):
    """成功・失敗を表す汎用型。"""

    def __init__(self, value: T | None, error: Exception | None = None) -> None:
        self._value = value
        self._error = error

    @property
    def ok(self) -> bool:
        return self._error is None

    def unwrap(self) -> T:
        if self._error is not None:
            raise self._error
        assert self._value is not None
        return self._value
```

## mypy 設定

kaji の `pyproject.toml` で設定済み。追加の設定が必要な場合のみ変更する。

```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_ignores = true
```

厳格モードを有効化しているため、以下が暗黙的に適用される:

- `disallow_untyped_defs = true` — 型ヒントのない関数定義を禁止
- `disallow_any_generics = true` — `list` / `dict` をパラメータなしで使用禁止
- `warn_redundant_casts = true` — 冗長な `cast()` に警告

## よくある型エラーと対処法

### None チェックの漏れ

```python
# ❌ mypy エラー: session_id が str | None なのに None チェックなし
def resume_from(session_id: str | None) -> str:
    return session_id.upper()  # error: Item "None" of "str | None" has no attribute "upper"

# ✅ 明示的な None チェック
def resume_from(session_id: str | None) -> str:
    if session_id is None:
        return ""
    return session_id.upper()
```

### dict の値型

```python
# ❌ 型が曖昧
def get_field(cfg: dict, key: str):
    return cfg.get(key)

# ✅ 具体的な型
def get_field(cfg: dict[str, str], key: str) -> str | None:
    return cfg.get(key)
```

### cast() の使用

外部ライブラリ等で型が `Any` になる場合は `cast()` で型を明示する。ただし乱用しない。

```python
from typing import cast
import yaml

raw = yaml.safe_load(text)
data = cast(dict[str, Any], raw)  # yaml.safe_load の戻り値は Any
```

## チェックリスト

### 実装時チェック

- [ ] `from __future__ import annotations` がモジュール先頭にあるか
- [ ] 全ての関数に引数・戻り値の型ヒントがあるか
- [ ] `Optional[X]` ではなく `X | None` を使用しているか
- [ ] `list[X]` / `dict[K, V]` 等の組み込み記法を使用しているか（`typing.List` 等を使っていないか）
- [ ] mutable default に `field(default_factory=...)` を使用しているか
- [ ] `mypy` がエラーなしで通るか（`make check`）

## 関連ドキュメント

- [Python スタイル規約](./python-style.md) — 基本的なコーディング規約
- [命名規則](./naming-conventions.md) — 型名・変数名の命名パターン
- [docstring スタイル](./docstring-style.md) — docstring での型情報記述
