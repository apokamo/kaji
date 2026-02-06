# Bugfix Agent v5 - テスト詳細設計書 (リバースエンジニアリング)

**作成日**: 2025-12-09
**バージョン**: 1.0
**目的**: 現在のテスト方式を文書化し、本番環境との差異を特定する

---

## 1. テスト構成概要

### 1.1 テストファイル一覧

| ファイル | サイズ | テスト数 | 目的 |
|---------|--------|---------|------|
| `test_bugfix_agent_orchestrator.py` | ~100KB | 160+ | オーケストレーター本体のユニットテスト |
| `tests/test_issue_provider.py` | ~5KB | 11 | IssueProvider 単体テスト |
| `test_prompts.py` | ~6KB | 少数 | プロンプトファイルの検証 |
| `tests/conftest.py` | ~2KB | - | pytest fixtures (mock_issue_provider 等) |

### 1.2 テストカテゴリ

```
test_bugfix_agent_orchestrator.py
├── Tool Wrapper Tests (Phase 0)
│   ├── MockTool Tests (4件)
│   ├── GeminiTool Tests (12件)
│   ├── CodexTool Tests (10件)
│   └── ClaudeTool Tests (12件)
├── State/Context Tests
│   ├── AgentContext Tests (4件)
│   ├── SessionState Tests (2件)
│   └── Factory Function Tests (6件)
├── IssueProvider Tests (tests/test_issue_provider.py)
│   ├── MockIssueProvider Tests (8件)
│   └── Handler Integration Tests (3件)
├── CLI Parsing Tests (Phase 2)
│   ├── parse_args Tests (12件)
│   ├── ExecutionMode Tests
│   └── ExecutionConfig Tests
├── State Handler Tests (Phase 3)
│   ├── handle_init (3件)
│   ├── handle_investigate (2件)
│   ├── handle_investigate_review (2件)
│   ├── handle_detail_design (2件)
│   ├── handle_detail_design_review (2件)
│   ├── handle_implement (2件)
│   ├── handle_implement_review (3件)
│   ├── handle_qa (2件)
│   ├── handle_qa_review (4件)
│   └── handle_pr_create (1件)
├── Edge Case Tests
│   ├── Loop Counter Tests (3件)
│   ├── Session Management Tests (2件)
│   └── State Transition Tests (2件)
├── Error Handling Tests
│   ├── ToolError Tests (6件)
│   ├── check_tool_result Tests (2件)
│   └── LoopLimitExceeded Tests (1件)
├── Logging Tests
│   ├── RunLogger Tests (3件)
│   └── Log Format Tests (4件)
├── Integration Tests
│   ├── run() SINGLE mode (1件)
│   └── run() FROM_END mode (1件)
└── Smoke Tests (CI skip) - 4件
    ├── test_smoke_gemini_tool_real_cli
    ├── test_smoke_codex_tool_real_cli
    ├── test_smoke_claude_tool_real_cli
    └── test_smoke_full_orchestrator_run
```

---

## 2. テスト方式

### 2.1 モックベーステスト

大部分のテストは `MockTool` と `monkeypatch` を使用してCLI呼び出しをモック化:

```python
# パターン1: MockTool による完全モック
ctx = create_test_context(
    analyzer_responses=["analyzer response"],
    reviewer_responses=["reviewer response"],
    implementer_responses=["implementer response"],
    issue_url=config.issue_url,
)

# パターン2: monkeypatch による部分モック
def fake_streaming(args, **kwargs):
    return (stdout, stderr, returncode)
monkeypatch.setattr(mod, "_run_cli_streaming", fake_streaming)
```

### 2.2 テストコンテキストファクトリ

`create_test_context()` 関数で統一的なテストコンテキストを生成:
- `analyzer_responses`: Gemini用レスポンス (INIT, INVESTIGATE, DETAIL_DESIGN)
- `reviewer_responses`: Codex用レスポンス (*_REVIEW)
- `implementer_responses`: Claude用レスポンス (IMPLEMENT, PR_CREATE)
- `issue_provider`: IssueProvider インスタンス（省略時は `_SimpleTestIssueProvider` を自動生成）

### 2.3 IssueProvider 抽象化によるローカルテスト

GitHub API 依存を排除するため、`IssueProvider` 抽象化を導入:

```python
# tests/utils/providers.py
class MockIssueProvider(IssueProvider):
    """テスト用 IssueProvider"""

    def __init__(self, initial_body: str = "", issue_number: int = 999):
        self._comments: list[str] = []
        ...

    # アサーション用ヘルパー
    @property
    def comments(self) -> list[str]: ...
    @property
    def last_comment(self) -> str | None: ...
    @property
    def comment_count(self) -> int: ...
    def has_comment_containing(self, text: str) -> bool: ...
    def clear(self) -> None: ...
```

#### 使用例

```python
# tests/conftest.py にフィクスチャとして定義済み
@pytest.fixture
def mock_issue_provider():
    return MockIssueProvider(initial_body="# Test Issue")

def test_handler_posts_comment(mock_issue_provider):
    ctx = AgentContext(
        reviewer=MockTool(["## VERDICT\n- Result: PASS"]),
        issue_provider=mock_issue_provider,
        ...
    )
    handle_init(ctx, state)

    assert mock_issue_provider.comment_count == 1
    assert "PASS" in mock_issue_provider.last_comment
```

#### テスト範囲

| テスト種別 | GitHub API | 対象 |
|-----------|------------|------|
| Unit/Handler Tests | ❌ 不要（MockIssueProvider使用） | MockTool + MockIssueProvider |
| E2E Tests (Real AI) | ✅ 必要（GitHubIssueProvider使用） | 実際のCLIツール + GitHub Issue |

### 2.4 Smokeテスト

実際のCLIツールを呼び出すテスト (CIではスキップ):

```python
@pytest.mark.skip(reason="Requires actual CLI tools")
def test_smoke_gemini_tool_real_cli():
    tool = mod.GeminiTool(model="auto")
    response, session_id = tool.run("What is 2+2?")
    assert response != "ERROR"
```

### 2.5 CodexTool JSON パース仕様

#### 設計意図

CodexTool は Codex CLI の出力をパースする際、JSONパースに失敗した行も収集する。
これは `mcp_tool_call` モードで VERDICT がプレーンテキストとして出力されるケースに対応するため。

#### 動作仕様

| シナリオ | 期待動作 | 根拠 |
|---------|---------|------|
| 有効なJSON行 | パースして `agent_message.text` を抽出 | 通常フロー |
| 無効なJSON行 | **スキップせず収集** | E2E Test 7-10 で確定した仕様 |
| 混合出力 | 有効メッセージ + 非JSON行を `\n\n` で結合して返却 | mcp_tool_call モード対応 |

#### 下流処理との関係

収集された出力は `parse_verdict()` に渡され、VERDICT Result を抽出します:

1. **Step 1 (Strict Parse)**: `re.search(r"Result:\s*(\w+)", text)` でマッチ → Enum変換で検証
   - 有効値 (PASS/RETRY/BACK_DESIGN/ABORT): 成功
   - 不正値 (PENDING/WAITING等): `InvalidVerdictValueError` を即座に発生（**フォールバック対象外**）
   - マッチなし: Step 2へ

2. **Step 2 (Relaxed Parse)**: 複数パターンで探索（Status:, **Status**:, ステータス: 等）

3. **Step 3 (AI Formatter)**: AI で整形してリトライ（最大2回）

**重要**: `Result:` 行が違う場所（例: `gh comment` 引数）に出力されるとStep 1/2で失敗し、Step 3のAI Formatterが必要になります。ノイズ（非JSON行）の混入自体はVERDICT抽出に影響しません。詳細は ARCHITECTURE.md Section 10 を参照。

#### テスト方針

テストでは完全一致で検証し、「意図したノイズ」と「意図しないノイズ」を区別する：

- ✅ `assert response == "INVALID JSON LINE\n\nok"` （厳密）
- ❌ `assert "ok" in response` （リグレッション見逃しリスク）

> **参照**: `E2E_TEST_FINDINGS.md` セクション 3.1, 4.1

---

## 3. テストフィクスチャ構造

### 3.1 E2Eテストフィクスチャ

```
test-fixtures/
└── bugfix-agent-e2e/
    ├── L1-simple/
    │   └── 001-type-error/
    │       ├── src/
    │       │   └── calculator.py
    │       ├── tests/
    │       │   └── test_calculator.py
    │       ├── test-artifacts/
    │       │   ├── logs/pytest/test.log
    │       │   ├── coverage/{html,xml,json}/
    │       │   └── bugfix-agent/
    │       └── pytest_output.txt
    ├── L2-medium/ (未実装)
    └── L3-complex/ (未実装)
```

### 3.2 L1-001 フィクスチャ詳細

**目的**: シンプルな型エラーを修正する基本テストケース

**テスト内容**:
- 6件のテストケース (全てPASS)
- Python 3.13.7 + pytest-8.4.2
- カバレッジ計測対象: `apps/` モジュール

---

## 4. 本番環境との差異分析

### 4.1 特定された差異

| 項目 | テスト環境 | 本番環境 | 影響 |
|------|-----------|---------|------|
| CLI呼び出し | MockTool でモック化 | 実際のgemini/codex/claude CLI | 出力フォーマットの差異 |
| GitHub Issue | MockIssueProvider でモック化 | GitHubIssueProvider (実API) | ✅ 解決済み (Issue #284) |
| 作業ディレクトリ | tmp_path使用 | リポジトリルート | パス解決の差異 |
| カバレッジ計測 | 無効 (`No data was collected`) | 有効 | カバレッジ閾値判定 |
| タイムアウト | なし/短い | config.toml設定値 | 長時間タスクの挙動 |

### 4.2 カバレッジ問題の詳細

L1-001フィクスチャで発生している問題:

```
CoverageWarning: Module apps was never imported. (module-not-imported)
CoverageWarning: No data was collected. (no-data-collected)
ERROR: Coverage failure: total of 0 is less than fail-under=40
```

**原因**:
- `pyproject.toml`のカバレッジ設定が `apps/` をターゲットにしている
- フィクスチャの `src/` ディレクトリはカバレッジ対象外

### 4.3 E2Eテストランナーの状態

**注意**: `e2e_test_runner.py` は現在リポジトリに存在しない

- 以前のE2Eテストで使用されていたが、削除またはコミットされていない
- `test-artifacts/e2e/` にログが残存 (Dec 6-8のテスト実行結果)

---

## 5. テスト実行方法

### 5.1 ユニットテスト実行

```bash
# 全テスト実行
cd .claude/agents/bugfix-v5
source /home/aki/claude/kamo2/.venv/bin/activate
pytest test_bugfix_agent_orchestrator.py -v

# 特定カテゴリのみ
pytest test_bugfix_agent_orchestrator.py -v -k "gemini"
pytest test_bugfix_agent_orchestrator.py -v -k "handler"

# Smokeテスト含む (実CLI必要)
pytest test_bugfix_agent_orchestrator.py -v --run-slow
```

### 5.2 カバレッジ付き実行

```bash
pytest test_bugfix_agent_orchestrator.py --cov=bugfix_agent_orchestrator --cov-report=html
```

---

## 6. 改善提案

### 6.1 短期改善 (すぐに対応可能)

1. **カバレッジ設定の分離**
   - フィクスチャ専用の `pytest.ini` または `pyproject.toml` を作成
   - `--no-cov` オプションのデフォルト化

2. **E2Eテストランナーの復元または再実装**
   - 現在のワークフローに合わせた設計
   - GitHub Issue作成/クローズの自動化

3. **モックレスポンスの充実**
   - 実際のCLI出力に基づくモックデータ
   - エッジケース（タイムアウト、レート制限）のテスト

### 6.2 中期改善 (設計変更を伴う)

1. **統合テスト環境の構築**
   - Dockerコンテナでの隔離テスト
   - APIキー不要のローカルLLMモック

2. **テストフィクスチャの拡充**
   - L2-medium: 複数ファイル修正
   - L3-complex: リファクタリング・設計変更

3. **CI/CD統合**
   - GitHub Actionsでのテスト自動実行
   - Smokeテストの条件付き実行

---

## 7. 関連ドキュメント

- `ARCHITECTURE.md`: システム設計
- `config.toml`: 設定ファイル
- `prompts/`: 各ステートのプロンプト定義

---

## 8. 変更履歴

| 日付 | バージョン | 変更内容 |
|------|-----------|---------|
| 2025-12-09 | 1.0 | 初版作成 (リバースエンジニアリング) |
| 2025-12-15 | 1.1 | IssueProvider 抽象化追加 (Issue #284) |
