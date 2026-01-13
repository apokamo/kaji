# Issue #19: AIToolProtocol定義 + ClaudeTool実装

## 概要

AIツール（Claude/Codex/Gemini）の共通インターフェース `AIToolProtocol` を定義し、`ClaudeTool` を実装する。

## 移植元

- **パス**: `/home/aki/claude/kamo2/.claude/agents/bugfix-v5`
- **対象ファイル**:
  - `bugfix_agent/tools/base.py` → `AIToolProtocol`, `MockTool`
  - `bugfix_agent/tools/claude.py` → `ClaudeTool`
  - `bugfix_agent/cli.py` → `run_cli_streaming()`, `format_jsonl_line()`
  - `bugfix_agent/context.py` → `build_context()`

## 設計方針

### 移植戦略

移植元の構造を尊重しつつ、このプロジェクトのアーキテクチャに適合させる:

1. **Protocol**: 既存の `protocol.py` をそのまま使用（移植元と同等）
2. **MockTool**: 移植元をそのまま移植
3. **ClaudeTool**: 移植元を簡略化（依存を減らす）
4. **CLI実行**: `run_cli_streaming()` を内部モジュールとして移植
5. **コンテキスト構築**: 簡易版で対応（将来拡張可能）

### スコープ

| 項目 | 対応 |
|------|------|
| AIToolProtocol | 既存維持 |
| MockTool | 移植 |
| ClaudeTool | 移植（簡略化） |
| run_cli_streaming | 移植 |
| build_context | 簡易版 |
| format_jsonl_line | 移植（Claude部分のみ） |

## ファイル構成

```
src/core/tools/
├── __init__.py       # 更新: MockTool, ClaudeTool 追加
├── protocol.py       # 変更なし（既存）
├── mock.py           # 新規: MockTool
├── claude.py         # 新規: ClaudeTool
└── _cli.py           # 新規: CLI実行ユーティリティ（内部用）
```

## インターフェース定義

### 1. AIToolProtocol (既存・変更なし)

```python
class AIToolProtocol(Protocol):
    """AI CLI ツールの統一インターフェース"""

    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        """AI ツールを実行する"""
        ...
```

### 2. MockTool (新規)

移植元: `bugfix_agent/tools/base.py`

```python
class MockTool:
    """テスト用モックツール

    予め設定した応答を順番に返す。セッション ID は自動生成。
    """

    def __init__(self, responses: list[str]):
        """
        Args:
            responses: 返す応答のリスト（順番に消費される）
        """
        self._responses = iter(responses)
        self._session_counter = 0

    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        """設定された応答を順番に返す"""
        del prompt, context, log_dir  # interface互換のため受け取るが使用しない
        response = next(self._responses, "MOCK_RESPONSE")
        self._session_counter += 1
        new_session = session_id or f"mock-session-{self._session_counter}"
        return (response, new_session)
```

### 3. ClaudeTool (新規)

移植元: `bugfix_agent/tools/claude.py`

```python
class ClaudeTool:
    """Claude Code CLI ラッパー"""

    def __init__(
        self,
        model: str = "sonnet",
        timeout: int = 600,
        permission_mode: str = "default",
        verbose: bool = True,
    ):
        """
        Args:
            model: モデル名 ("sonnet", "opus", "haiku")
            timeout: タイムアウト秒数
            permission_mode: 権限モード ("default", "bypassPermissions")
            verbose: 実行中の出力表示
        """
        self.model = model
        self.timeout = timeout
        self.permission_mode = permission_mode
        self.verbose = verbose

    def run(
        self,
        prompt: str,
        context: str | list[str] = "",
        session_id: str | None = None,
        log_dir: Path | None = None,
    ) -> tuple[str, str | None]:
        """Claude Code CLI を実行する

        処理フロー:
        1. コンテキストをプロンプトに結合
        2. CLI引数を構築
        3. run_cli_streaming() で実行
        4. 出力をパースして応答とセッションIDを抽出
        5. エラー時は ("ERROR", session_id) を返す
        """
        # コンテキスト結合
        context_str = _build_context(context)
        full_prompt = f"{prompt}\n\nContext:\n{context_str}" if context_str else prompt

        # CLI引数構築
        args = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
        if self.model:
            args += ["--model", self.model]
        if self.permission_mode != "default":
            args += ["--permission-mode", self.permission_mode]
        if session_id:
            args += ["-r", session_id]
        args.append("--dangerously-skip-permissions")
        args.append(full_prompt)

        # 実行
        try:
            stdout, stderr, returncode = run_cli_streaming(
                args,
                timeout=self.timeout if self.timeout > 0 else None,
                verbose=self.verbose,
                log_dir=log_dir,
                tool_name="claude",
            )
            if returncode != 0:
                return "ERROR", session_id
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return "ERROR", session_id

        # 出力パース
        return self._parse_json_output(stdout, session_id)

    def _parse_json_output(
        self, stdout: str, session_id: str | None
    ) -> tuple[str, str | None]:
        """CLIの出力からJSON部分を抽出してパースする

        stream-json形式（複数行JSON）から "type":"result" の行を探す。
        """
        for line in stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                if payload.get("type") == "result":
                    return self._extract_from_payload(payload, session_id)
            except json.JSONDecodeError:
                continue

        # パース失敗時は素の出力を返す
        return stdout.strip(), session_id

    def _extract_from_payload(
        self, payload: dict[str, Any], session_id: str | None
    ) -> tuple[str, str | None]:
        """パース済みペイロードから応答とセッションIDを抽出

        期待形式: {"type":"result","result":"text","session_id":"uuid"}
        """
        result = payload.get("result", "")
        new_session_id = payload.get("session_id") or session_id
        return result, new_session_id
```

#### CLI 実行コマンド

```bash
claude -p --output-format stream-json --verbose \
    --model <model> \
    -r <session_id>  # セッション継続時のみ
    --dangerously-skip-permissions
    "<prompt>"
```

#### 出力パース

stream-json形式の出力から結果を抽出:

```json
{"type":"result","result":"response text","session_id":"uuid"}
```

#### エラーハンドリング

| 状況 | 処理 |
|------|------|
| CLI未インストール | FileNotFoundError → ("ERROR", session_id) |
| タイムアウト | TimeoutExpired → ("ERROR", session_id) |
| 非ゼロ終了 | returncode != 0 → ("ERROR", session_id) |
| JSONパース失敗 | 素の出力を返す |

### 4. CLI実行ユーティリティ (_cli.py)

移植元: `bugfix_agent/cli.py`

```python
def run_cli_streaming(
    args: list[str],
    timeout: int | None = None,
    verbose: bool = True,
    env: dict[str, str] | None = None,
    log_dir: Path | None = None,
    tool_name: str | None = None,
) -> tuple[str, str, int]:
    """CLI をストリーミング実行

    処理フロー:
    1. subprocess.Popen でプロセス起動
    2. stdout をリアルタイムで読み取り（バッファリングも行う）
    3. verbose=True の場合、整形して表示
    4. タイムアウトは threading.Timer で実装
    5. log_dir 指定時は stdout.log, stderr.log を保存

    Args:
        args: コマンドと引数のリスト
        timeout: タイムアウト秒数（None で無制限）
        verbose: リアルタイム出力表示
        env: 環境変数（None で現在の環境を継承）
        log_dir: ログ保存ディレクトリ
        tool_name: ツール名（"claude"）。指定時は format_jsonl_line で整形

    Returns:
        (stdout, stderr, returncode)

    Raises:
        FileNotFoundError: コマンドが見つからない
        subprocess.TimeoutExpired: タイムアウト
    """
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    # タイムアウト用タイマー
    timeout_occurred = threading.Event()
    timer: threading.Timer | None = None

    def kill_on_timeout() -> None:
        timeout_occurred.set()
        process.kill()

    if timeout is not None:
        timer = threading.Timer(timeout, kill_on_timeout)
        timer.start()

    try:
        for line in process.stdout:
            stdout_lines.append(line)
            if verbose and tool_name:
                formatted = format_jsonl_line(line, tool_name)
                if formatted:
                    print(formatted, flush=True)
            elif verbose:
                print(line, end="", flush=True)

        for line in process.stderr:
            stderr_lines.append(line)

        returncode = process.wait()

        if timeout_occurred.is_set():
            raise subprocess.TimeoutExpired(args, timeout or 0)
    finally:
        if timer is not None:
            timer.cancel()

    return "".join(stdout_lines), "".join(stderr_lines), returncode


def format_jsonl_line(line: str, tool_name: str) -> str | None:
    """JSONL 行からコンテンツを抽出

    Args:
        line: JSONL 形式の1行
        tool_name: ツール名 ("claude")

    Returns:
        抽出したコンテンツ。抽出不可の場合は None
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        # JSON でない場合は、空でなければそのまま返す
        stripped = line.strip()
        return stripped if stripped else None

    if tool_name == "claude":
        msg_type = data.get("type")

        # result: 最終結果
        if msg_type == "result":
            result = data.get("result")
            return result if isinstance(result, str) and result else None

        # assistant: 応答メッセージ
        if msg_type == "assistant":
            message = data.get("message", {})
            if isinstance(message, dict):
                content = message.get("content", [])
                texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                return "\n".join(texts) if texts else None

        # system 等はスキップ
        return None

    return None
```

### 5. コンテキスト構築ユーティリティ

```python
def _build_context(context: str | list[str]) -> str:
    """コンテキストを文字列に変換

    Args:
        context: 文字列またはファイルパスのリスト

    Returns:
        結合されたコンテキスト文字列
    """
    if isinstance(context, str):
        return context
    if isinstance(context, list):
        return "\n".join(context)
    return ""
```

## 実装順序

1. `_cli.py`: CLI実行ユーティリティ
2. `mock.py`: MockTool
3. `claude.py`: ClaudeTool
4. `__init__.py`: エクスポート更新

## テスト方針

### MockTool テスト

```python
class TestMockTool:
    def test_returns_responses_in_order(self):
        """応答が順番に返される"""

    def test_returns_default_when_exhausted(self):
        """応答がなくなったらデフォルト値"""

    def test_generates_session_id(self):
        """セッションIDが自動生成される"""

    def test_preserves_existing_session_id(self):
        """既存セッションIDが維持される"""
```

### ClaudeTool テスト

```python
class TestClaudeTool:
    def test_builds_correct_command(self):
        """CLIコマンドが正しく構築される"""

    def test_parses_stream_json_output(self):
        """stream-json出力が正しくパースされる"""

    def test_handles_timeout(self):
        """タイムアウトが適切に処理される"""

    def test_handles_cli_not_found(self):
        """CLI未インストール時のエラー処理"""

    def test_handles_non_zero_exit(self):
        """非ゼロ終了コード時のエラー処理"""

    def test_includes_session_resume_flag(self):
        """セッション継続時に-rフラグが含まれる"""

    def test_includes_context_in_prompt(self):
        """コンテキストがプロンプトに含まれる"""
```

### run_cli_streaming テスト

```python
class TestRunCliStreaming:
    def test_captures_stdout(self):
        """標準出力がキャプチャされる"""

    def test_captures_stderr(self):
        """標準エラー出力がキャプチャされる"""

    def test_returns_exit_code(self):
        """終了コードが返される"""

    def test_raises_on_timeout(self):
        """タイムアウト時に例外が発生"""

    def test_raises_on_command_not_found(self):
        """コマンド未発見時に例外が発生"""
```

## 依存関係

- 外部: なし（標準ライブラリのみ）
- 内部: `src.core.config` (将来的に設定取得で使用可能)

## 参考資料

- [Claude Code CLI ガイド](../../docs/guides/claude-code-cli-guide.md) - CLI オプション、セッション管理、JSON出力形式の詳細

## 互換性

- 既存の `AIToolProtocol` は変更なし
- `run()` メソッドのシグネチャは移植元と完全互換
