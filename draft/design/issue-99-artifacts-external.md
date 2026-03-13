# [設計] artifacts の出力先を worktree 外に移動する

Issue: #99

## 概要

`kaji run` の artifacts（実行ログ・セッション状態）のデフォルト出力先を `~/.kaji/artifacts/` に変更し、worktree 削除時にログが失われない構成にする。

## 背景・目的

現在 artifacts は worktree 内（`{repo_root}/.kaji-artifacts/`）に出力されるため、以下の問題がある:

1. **事後検証不能**: `issue-close` で worktree を削除すると過去の run ログが消失する
2. **後処理の競合**: worktree 削除後にハーネスが `run.log` を参照し ENOENT で異常終了する（旧 #96）
3. **worktree の使い捨て性が損なわれる**: artifacts が残っているために削除タイミングに制約が生じる

## インターフェース

### 入力

#### `config.toml` の `[paths]` セクション

```toml
[paths]
# 絶対パス指定（推奨）
artifacts_dir = "/home/user/.kaji/artifacts"

# または ~ 展開
artifacts_dir = "~/.kaji/artifacts"

# 相対パス（従来互換: repo_root からの相対）
artifacts_dir = ".kaji-artifacts"
```

- 未指定時のデフォルト: `~/.kaji/artifacts`
- `~` はランタイムで `Path.expanduser()` により展開

#### パス解決ルール

| 指定形式 | 解決方法 |
|---------|---------|
| `~/.kaji/artifacts` | `Path.expanduser()` → 絶対パス |
| `/abs/path` | そのまま使用 |
| `relative/path` | `repo_root / relative/path` |

### 出力

変更なし。ディレクトリ構造は現行と同一:

```
{artifacts_dir}/
└── {issue_number}/
    ├── session-state.json
    ├── progress.md
    └── runs/
        └── {YYMMDDhhmm}/
            ├── run.log
            └── {step_id}/
                ├── stdout.log
                ├── console.log
                └── stderr.log
```

### 使用例

```python
# config.toml なし（デフォルト）
# → ~/.kaji/artifacts/99/session-state.json

# config.toml: artifacts_dir = "~/.kaji/artifacts"
# → /home/user/.kaji/artifacts/99/session-state.json

# config.toml: artifacts_dir = ".kaji-artifacts"（従来互換）
# → {repo_root}/.kaji-artifacts/99/session-state.json
```

## 制約・前提条件

- 既存の `.kaji-artifacts/` ディレクトリのマイグレーションは行わない（手動移行）
- `~` 展開は Python の `Path.expanduser()` に依拠する
- `..` を含む相対パスは引き続き拒否する（repo_root エスケープ防止）
- artifacts_dir 配下のディレクトリ構造（issue 番号 / runs / etc.）は変更しない

## 方針

### 1. `PathsConfig` のデフォルト値変更

```python
@dataclass(frozen=True)
class PathsConfig:
    artifacts_dir: str = "~/.kaji/artifacts"  # 変更: ".kaji-artifacts" → "~/.kaji/artifacts"
```

### 2. パス解決ロジック（`KajiConfig.artifacts_dir` プロパティ）

```python
@property
def artifacts_dir(self) -> Path:
    expanded = Path(self.paths.artifacts_dir).expanduser()
    if expanded.is_absolute():
        return expanded
    return self.repo_root / self.paths.artifacts_dir
```

- `~` 付き → `expanduser()` で絶対パスに → そのまま返却
- 絶対パス → そのまま返却
- 相対パス → `repo_root / path`（従来動作）

### 3. バリデーション変更（`_validate_artifacts_dir`）

現在のバリデーション:
- 絶対パス → **拒否** ← これを撤廃
- `..` を含む → 拒否

変更後:
- `~` 展開後に絶対パスかどうかを判定
- 絶対パス → **許可**
- 相対パスで `..` を含む → 拒否（従来通り）

```python
@staticmethod
def _validate_artifacts_dir(config_path: Path, artifacts_dir: str) -> None:
    expanded = Path(artifacts_dir).expanduser()
    if expanded.is_absolute():
        return  # 絶対パスは無条件で許可
    # 相対パスの場合のみ .. チェック
    p = PurePosixPath(artifacts_dir)
    if ".." in p.parts:
        raise ConfigLoadError(...)
```

### 4. テスト修正

既存テストの期待値を新デフォルトに合わせて更新する。絶対パス拒否テストは「許可」テストに変更。

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。

### Small テスト

- `PathsConfig` のデフォルト値が `~/.kaji/artifacts` であること
- `KajiConfig.artifacts_dir` プロパティのパス解決ロジック:
  - `~` 付き文字列 → `expanduser()` で展開された絶対パス
  - 絶対パス文字列 → そのまま返却
  - 相対パス文字列 → `repo_root / path`
- `_validate_artifacts_dir`:
  - 絶対パス → エラーにならない
  - `~` 付きパス → エラーにならない
  - 相対パスで `..` → `ConfigLoadError`
  - 通常の相対パス → エラーにならない

### Medium テスト

- `config.toml` に絶対パスを指定 → `KajiConfig._load` が正しくパースし `artifacts_dir` プロパティが絶対パスを返す
- `config.toml` に `~` 付きパスを指定 → `expanduser()` 後のパスが返る
- `config.toml` に相対パスを指定 → 従来互換で `repo_root / path` が返る
- `config.toml` 未指定（デフォルト） → `~/.kaji/artifacts` が展開された絶対パスが返る
- `SessionState.load_or_create` + `_persist` が worktree 外のディレクトリに正しく書き込む
- `WorkflowRunner` が worktree 外の artifacts_dir にログを出力する
- CLI `cmd_run` がデフォルト config で `~/.kaji/artifacts` 相当のパスを `WorkflowRunner` に渡す

### Large テスト

- `kaji run` をサブプロセスで実行し、artifacts がデフォルトの `~/.kaji/artifacts/` 配下に生成されることを確認
- `kaji run --workdir` 指定時に config discovery + artifacts パス解決が正しく動作することを確認

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更はない |
| docs/dev/ | なし | ワークフロー・開発手順に変更はない |
| docs/cli-guides/ | なし | CLI インターフェースの変更はない |
| CLAUDE.md | なし | 規約変更はない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 現行 config 実装 | `kaji_harness/config.py` | `PathsConfig.artifacts_dir` のデフォルト値、`_validate_artifacts_dir` のバリデーションロジック、`KajiConfig.artifacts_dir` プロパティのパス解決を確認 |
| 現行 state 実装 | `kaji_harness/state.py` | `SessionState` が `artifacts_dir` を引数で受け取り、その配下に `session-state.json` / `progress.md` を書き込む構造を確認 |
| 現行 runner 実装 | `kaji_harness/runner.py:62-72` | `WorkflowRunner` が `artifacts_dir` を受け取り、`runs/{timestamp}` ディレクトリを作成してログを出力する構造を確認 |
| 現行 CLI 実装 | `kaji_harness/cli_main.py:208` | `config.artifacts_dir` を `WorkflowRunner` に渡す箇所を確認 |
| 既存テスト | `tests/test_config.py` | 絶対パス拒否テスト（L100-107）、デフォルト値テスト（L33-35, L156-164）が変更対象であることを確認 |
| Python Path.expanduser | `pathlib` 標準ライブラリ | `~` を `$HOME` に展開する。`HOME` 環境変数が未設定の場合は `pwd` モジュールにフォールバック |
| Issue #99 | GitHub Issue | 完了条件: (1) デフォルト出力先が `~/.kaji/artifacts/`、(2) 絶対パス指定可能、(3) worktree 削除でログが残る、(4) 後処理が壊れない |
