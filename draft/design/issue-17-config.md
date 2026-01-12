# [設計] config.py pydantic-settings移行

Issue: #17

## 概要

pydantic-settings を使用した型安全な設定管理モジュールを新規作成する。

## 背景・目的

現状、設定管理の仕組みが存在しない。今後 AIToolProtocol（Claude/Codex/Gemini）の API キーや各種設定を管理する必要があるため、以下を実現する:

- 型安全な設定管理
- `.env` ファイルによるオーバーライド対応
- 起動時バリデーション（Fail Fast）

## インターフェース

### 入力

環境変数または `.env` ファイルから読み込む:

| 変数名 | 型 | 必須 | デフォルト | 説明 |
|--------|-----|------|------------|------|
| `DAO_LOG_LEVEL` | str | No | `INFO` | ログレベル |
| `DAO_ARTIFACTS_DIR` | Path | No | `./artifacts` | 成果物出力先 |

※ API キー等は Phase 1 #2 (AIToolProtocol) で追加予定

### 出力

```python
from src.core.config import settings

settings.log_level      # -> "INFO"
settings.artifacts_dir  # -> Path("./artifacts")
```

### 使用例

```python
from src.core.config import settings

# 設定値の参照
print(settings.log_level)

# バリデーション済みなので安全に使用可能
output_path = settings.artifacts_dir / "output.json"
```

## 制約・前提条件

- Python 3.11+
- pydantic-settings >= 2.0
- `.env` ファイルは任意（環境変数でも設定可能）
- シングルトンパターン（`settings` インスタンスをモジュールレベルで公開）

## 方針

```
src/core/config.py
├── class Settings(BaseSettings)  # pydantic-settings
│   ├── log_level: str = "INFO"
│   ├── artifacts_dir: Path = Path("./artifacts")
│   └── model_config = SettingsConfigDict(env_prefix="DAO_", ...)
│
└── settings = Settings()  # シングルトンインスタンス
```

### SettingsConfigDict 設定

```python
model_config = SettingsConfigDict(
    env_prefix="DAO_",
    env_file=".env",
    env_file_encoding="utf-8",
    case_sensitive=False,
)
```

## 検証観点

### 正常系
- デフォルト値で Settings インスタンスが生成できること
- 環境変数から値が読み込まれること
- `.env` ファイルから値が読み込まれること
- 環境変数が `.env` より優先されること

### 異常系
- 不正な型の値でバリデーションエラーになること
- 将来的に必須項目が未設定の場合エラーになること

### 境界値
- 空文字列の扱い
- Path の相対パス/絶対パス

## 参考

- [pydantic-settings 公式ドキュメント](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
