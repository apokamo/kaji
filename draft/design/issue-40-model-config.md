# [設計] モデル設定: config.toml のモデル/ツール設定移植

Issue: #40

## 概要

v5 の config.toml にあるモデル設定機能を dao に移植し、ツール別のモデル指定を可能にする。

## 背景・目的

現状:
- `src/bugfix_agent/tools/claude.py` は `get_config_value()` で config.toml 対応済み
- `src/core/tools/claude.py` はコンストラクタ引数でハードコード、config.toml 未対応
- config.toml ファイル自体がプロジェクトに存在しない
- ステート別設定（`[states.XXX]`）は未実装

目的:
- config.toml でツール別のデフォルト設定を一元管理
- 将来的なステート別設定の基盤を整備

## インターフェース

### 入力

**config.toml（プロジェクトルート）**:
```toml
[tools.claude]
model = "opus"
permission_mode = "default"
timeout = 1800

[tools.codex]
model = "gpt-5.1-codex-max"
sandbox = "workspace-write"
timeout = 900

[tools.gemini]
model = "gemini-2.5-pro"
timeout = 900

# ステート別設定（Phase 2 以降）
# [states.INIT]
# agent = "claude"
# model = "opus"
```

### 出力

- `ClaudeTool()` 初期化時に config.toml からデフォルト値を読み込む
- 明示的なコンストラクタ引数 > config.toml > ハードコードデフォルト の優先順位

### 使用例

```python
# 1. config.toml のデフォルト値を使用
tool = ClaudeTool()
assert tool.model == "opus"  # config.toml から

# 2. コンストラクタ引数で上書き
tool = ClaudeTool(model="sonnet")
assert tool.model == "sonnet"  # 引数優先

# 3. config.toml がない場合はハードコードデフォルト
# (config.toml を削除した状態)
tool = ClaudeTool()
assert tool.model == "sonnet"  # ハードコードデフォルト
```

## 制約・前提条件

- **config.toml 探索パス**: 既存の `find_config_file()` ロジックを使用
- **後方互換性**: コンストラクタ引数は引き続き動作する
- **ステート別設定は Phase 2**: 本 Issue では `[tools.XXX]` セクションのみ対応

## 対象範囲

### ツール実装状況

| ツール | src/core/tools/ | src/bugfix_agent/tools/ | 本 Issue 対応 |
|--------|-----------------|-------------------------|---------------|
| claude | `claude.py` 存在 | `claude.py` 存在（対応済み） | **対象** |
| codex | 未実装 | 未実装 | スコープ外 |
| gemini | 未実装 | 未実装 | スコープ外 |

**注記**: `src/core/tools/` には現時点で `claude.py` のみ実装。codex/gemini は将来の拡張時に同様のパターンで対応。

### 設定キーの適用対象

| 設定キー | claude | codex | gemini | 備考 |
|----------|--------|-------|--------|------|
| `model` | ✅ | ✅ | ✅ | 全ツール共通 |
| `timeout` | ✅ | ✅ | ✅ | 全ツール共通 |
| `permission_mode` | ✅ | - | - | Claude CLI 固有 |
| `sandbox` | - | ✅ | - | Codex CLI 固有 |

**未対応時の扱い**: 設定キーが存在しない場合、各ツールのハードコードデフォルト値を使用。

## 方針

### アーキテクチャ: 依存関係の整理

```
src/core/config.py          ← config.toml 読み込み機能（移動先）
    ↑
src/core/tools/claude.py    ← core 層ツール

src/bugfix_agent/config.py  ← core/config.py を import（wrapper）
    ↑
src/bugfix_agent/tools/     ← bugfix_agent 層ツール（既存）
```

**原則**: core 層は上位レイヤ（bugfix_agent）に依存しない。

### Phase 1: config.toml 作成と設定読み込み共通化

#### 1.1 config.toml をプロジェクトルートに作成
- v5 の `[tools.XXX]` セクションを踏襲
- `[states.XXX]` はコメントアウトで将来対応を示唆

#### 1.2 config.toml 探索優先順位

以下の順序で config.toml を探索し、最初に見つかったものを使用:

1. **環境変数**: `DAO_CONFIG` (core) / `BUGFIX_AGENT_CONFIG` (bugfix_agent)
2. **ワーキングディレクトリ**: `./config.toml`
3. **Git リポジトリルート**: リポジトリルートの `config.toml`
4. **ユーザー設定ディレクトリ**: `~/.config/dao/config.toml` (core) / `~/.config/bugfix-agent/config.toml` (bugfix_agent)

**フォールバック**: いずれも見つからない場合、各ツールのハードコードデフォルト値を使用。

#### 1.3 core/config への集約と互換性維持の方式

**方式: パラメータ化された共通関数 + レイヤ固有ラッパー**

```python
# src/core/config.py（共通ロジック）
def find_config_file(
    env_var: str = "DAO_CONFIG",
    user_config_dir: str = "~/.config/dao"
) -> Path | None:
    """汎用の config.toml 探索関数

    Args:
        env_var: 設定ファイルパスを指定する環境変数名
        user_config_dir: ユーザー設定ディレクトリのパス

    Returns:
        見つかった config.toml のパス、または None
    """
    # 探索ロジック（env_var → CWD → git root → user_config_dir）


# src/bugfix_agent/config.py（レイヤ固有ラッパー）
from src.core.config import find_config_file as _find_config_file

def find_config_file() -> Path | None:
    """bugfix_agent 固有の設定ファイル探索

    bugfix_agent 固有の環境変数名とユーザー設定パスを使用。
    """
    return _find_config_file(
        env_var="BUGFIX_AGENT_CONFIG",
        user_config_dir="~/.config/bugfix-agent"
    )
```

**責務分離**:
- `src/core/config.py`: 探索ロジックの共通実装（パラメータ化）
- `src/bugfix_agent/config.py`: bugfix_agent 固有の設定（環境変数名、ユーザー設定パス）を維持

**後方互換性**:
- `src/bugfix_agent/config.py` の既存 API シグネチャは維持
- `BUGFIX_AGENT_CONFIG` 環境変数、`~/.config/bugfix-agent/` パスは引き続き動作

#### 1.4 エラーハンドリング

| エラー種別 | 例外 | タイミング | 動作 |
|-----------|------|-----------|------|
| TOML 構文エラー | `tomllib.TOMLDecodeError` | `load_config()` 呼び出し時 | 即座に例外を raise（フォールバックなし） |
| ファイル読み込みエラー | `OSError` | `load_config()` 呼び出し時 | 即座に例外を raise |
| 設定ファイル未発見 | なし（None 返却） | `find_config_file()` 呼び出し時 | ハードコードデフォルトにフォールバック |
| 設定キー未存在 | なし（デフォルト返却） | `get_config_value()` 呼び出し時 | 引数で指定したデフォルト値を返却 |

**エラーメッセージ例**:
```
TOMLDecodeError: Invalid TOML in /path/to/config.toml at line 5:
  Expected '=' after key name, found ':'
```

#### 1.5 `src/core/config.py` に設定読み込み機能を追加
- `find_config_file()` を汎用化して移動
- `load_config()` を移動
- `get_config_value()` を移動
- 環境変数プレフィックスは `DAO_` を使用（core 層の既存規約）

#### 1.6 `src/bugfix_agent/config.py` のリファクタリング
- `src/core/config.py` の汎用関数を import
- bugfix_agent 固有パラメータを渡すラッパー関数を定義
- 後方互換性のため既存 API シグネチャは維持（re-export）
- `BUGFIX_AGENT_` プレフィックスの Settings は維持

#### 1.7 `src/core/tools/claude.py` の修正
- `src/core/config.py` から `get_config_value()` を import
- コンストラクタで config.toml を参照するよう変更

### Phase 2: ステート別設定

ステート遷移時に `[states.XXX]` の設定に基づきツール/モデルを切り替える。

#### 2.1 ステート設定のデータモデル

```python
# src/core/config.py に追加

@dataclass
class StateConfig:
    """ステート別設定

    Attributes:
        agent: 使用するツール名 ("claude" | "codex" | "gemini")
        model: モデル名（省略時は [tools.{agent}].model を継承）
        timeout: タイムアウト秒数（省略時は [tools.{agent}].timeout を継承）
    """
    agent: str
    model: str | None = None
    timeout: int | None = None


def get_state_config(state_name: str) -> StateConfig | None:
    """ステート設定を取得

    Args:
        state_name: ステート名（例: "INIT", "INVESTIGATE"）

    Returns:
        StateConfig if [states.{state_name}] exists, else None

    Note:
        model/timeout が未指定の場合、対応する [tools.{agent}] から継承される。
        継承ロジックは呼び出し側（create_tool_for_state）で実装。
    """
```

#### 2.2 設定継承ルール

```
[states.INIT]           → StateConfig(agent="claude", model=None, timeout=None)
    ↓ 継承
[tools.claude]          → model="opus", timeout=1800
    ↓ 最終値
agent=claude, model=opus, timeout=1800
```

優先順位:
1. `[states.XXX].model` / `[states.XXX].timeout` （明示指定）
2. `[tools.{agent}].model` / `[tools.{agent}].timeout` （継承）
3. ツールのハードコードデフォルト

#### 2.3 ツール生成関数

```python
# src/bugfix_agent/tool_factory.py（新規）

def create_tool_for_state(state_name: str) -> AIToolProtocol:
    """ステート設定に基づいてツールを生成

    Args:
        state_name: ステート名（例: "INIT", "INVESTIGATE"）

    Returns:
        設定に基づいて初期化された AIToolProtocol 実装

    Raises:
        ValueError: 不明なエージェント名の場合

    Example:
        # config.toml:
        # [states.INIT]
        # agent = "claude"
        # model = "opus"

        tool = create_tool_for_state("INIT")
        assert isinstance(tool, ClaudeTool)
        assert tool.model == "opus"
    """
```

#### 2.4 ワークフローへの統合

現在の AgentContext は初期化時に固定のツールを受け取るが、
ステート別設定では各ステート遷移時にツールを切り替える必要がある。

**方針 A: 遅延評価型（推奨）**
```python
@dataclass
class AgentContext:
    # 既存フィールドは維持
    analyzer: AIToolProtocol
    reviewer: AIToolProtocol
    implementer: AIToolProtocol

    # ステート別設定を有効化するフラグ
    use_state_config: bool = False

    def get_tool_for_state(self, state_name: str) -> AIToolProtocol:
        """ステートに応じたツールを取得

        use_state_config=True の場合:
            config.toml の [states.{state_name}] からツール生成
        use_state_config=False の場合:
            既存ロジック（analyzer/reviewer/implementer を使用）
        """
```

**利点**:
- 既存の DI パターンを維持（テスト容易性）
- ステート設定がない場合のフォールバックが明確
- 段階的移行が可能

**方針 B: 毎回生成型**
```python
# オーケストレータのメインループ内
while current != State.COMPLETE:
    tool = create_tool_for_state(current.name)
    handler(ctx_with_tool, session_state)
```

**欠点**: 既存のハンドラシグネチャとの互換性が低い

#### 2.5 config.toml への追加

```toml
# ステート別設定
[states.INIT]
agent = "claude"
model = "opus"

[states.INVESTIGATE]
agent = "claude"
model = "opus"
timeout = 600

[states.INVESTIGATE_REVIEW]
agent = "codex"
model = "gpt-5.2"
timeout = 300

[states.DETAIL_DESIGN]
agent = "claude"
model = "opus"
timeout = 900

[states.DETAIL_DESIGN_REVIEW]
agent = "codex"
model = "gpt-5.2"

[states.IMPLEMENT]
agent = "claude"
model = "opus"
timeout = 1800

[states.IMPLEMENT_REVIEW]
agent = "codex"
model = "gpt-5.2"
timeout = 600

[states.PR_CREATE]
agent = "claude"
model = "sonnet"
```

#### 2.6 実装ステップ

1. **`StateConfig` データクラスと `get_state_config()` を追加**
   - `src/core/config.py` に実装
   - 継承ロジックは含めない（純粋な設定読み込み）

2. **`create_tool_for_state()` を実装**
   - `src/bugfix_agent/tool_factory.py` を新規作成
   - 継承ロジックをここで実装
   - ClaudeTool / CodexTool / GeminiTool の生成

3. **`AgentContext.get_tool_for_state()` を追加**
   - `use_state_config` フラグで既存動作と切り替え
   - 既存テストへの影響を最小化

4. **オーケストレータを修正**
   - メインループでステート別ツール取得を呼び出し
   - `run_bugfix_workflow` をネイティブ実装に置き換え（オプション）

#### 2.7 スコープ

| 項目 | Phase 2 対象 | 備考 |
|------|-------------|------|
| `get_state_config()` | ✅ | config.toml 読み込み |
| `create_tool_for_state()` | ✅ | ツール生成ファクトリ |
| AgentContext 拡張 | ✅ | `get_tool_for_state()` 追加 |
| オーケストレータ修正 | ⚠️ 部分的 | 既存 v5 呼び出しを維持可能 |
| codex/gemini ツール実装 | ❌ | 別 Issue で対応 |

## 検証観点

### Phase 1: ツール設定

#### 正常系
- config.toml が存在する場合、ツール設定が正しく読み込まれる
- コンストラクタ引数が config.toml より優先される
- config.toml がない場合、ハードコードデフォルトが使用される
- `src/bugfix_agent/config.py` の既存 API が引き続き動作する

#### 異常系
- config.toml の構文エラー時にわかりやすいエラーメッセージ
- 存在しないキーへのアクセスでデフォルト値が返る

#### 境界値
- 空の config.toml（ファイル存在、内容なし）
- `[tools]` セクションのみ存在（`[tools.claude]` なし）

#### 依存関係
- `src/core/tools/claude.py` が `src/bugfix_agent/` に依存しないこと
- `src/bugfix_agent/config.py` が `src/core/config.py` を正しく import すること

### Phase 2: ステート別設定

#### 正常系
- `[states.XXX]` が存在する場合、`get_state_config()` が StateConfig を返す
- `[states.XXX]` が存在しない場合、`get_state_config()` が None を返す
- `create_tool_for_state()` が正しいツールを正しいモデルで生成する
- model/timeout 未指定時に `[tools.{agent}]` から継承される
- `AgentContext.get_tool_for_state()` がステート設定に基づいてツールを返す
- `use_state_config=False` の場合、既存ロジック（固定ツール）が動作する

#### 異常系
- 不明なエージェント名で ValueError
- `[states.XXX].agent` 未指定で適切なエラー

#### 境界値
- `[states.XXX]` に agent のみ指定（model/timeout なし）→ 継承テスト
- `[states.XXX]` が空セクション（`[states.INIT]` のみ、キーなし）
- `[tools.{agent}]` も存在しない場合 → ハードコードデフォルト

#### 統合
- ワークフロー実行中にステート遷移ごとにツールが切り替わる
- 既存テストが `use_state_config=False` で引き続きパスする

## 参考

- v5 config.toml: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/config.toml`
- v5 config.py: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5/bugfix_agent/config.py`
- 既存実装: `src/bugfix_agent/config.py` - `get_config_value()`, `find_config_file()`
