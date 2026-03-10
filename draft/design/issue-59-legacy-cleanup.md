# [設計] V5/V6旧ファイルをlegacy/に整理しV7基盤を明確化

Issue: #59

## 概要

V5/V6の旧ファイルを `legacy/` ディレクトリに移動し、`pyproject.toml`・`README.md`・`docs/ARCHITECTURE.md` をV7基盤に合わせて更新する。

## 背景・目的

V7（dao_harness）への移行が完了し、#57/#58でマージ済み。しかしリポジトリルートにV5/V6のコード・テスト・ドキュメントが混在しており、以下のリスクがある:

1. 新規参加者がV5コードを誤って修正・使用する
2. `pyproject.toml` が `bugfix_agent*` を含み、不要なパッケージが公開される
3. `README.md` がV5の内容で、プロジェクトの現状を反映していない

旧ファイルはgit履歴で完全に参照可能だが、`legacy/` に退避することでディレクトリを辿って参照できる利便性を維持する。

## インターフェース

### 入力

なし（リポジトリ内のファイル操作のみ）

### 出力

- `legacy/` ディレクトリに旧ファイルが移動された状態
- 更新された `pyproject.toml`、`README.md`、`docs/ARCHITECTURE.md`

### 使用例

```bash
# 移行後のディレクトリ構成確認
ls legacy/
# bugfix_agent/  bugfix_agent_orchestrator.py  config.toml  tests/  ...

# V7テストの実行（legacy移動後も変わらず動作）
pytest tests/

# V7の品質チェック
ruff check dao_harness/ tests/ && mypy dao_harness/
```

> **注意**: 現時点では `dao_harness` にCLIエントリポイントが存在しない（`__main__.py` 未作成、`pyproject.toml` の `[project.scripts]` 未定義）。CLIエントリポイントの追加はこのIssueのスコープ外であり、別途対応する。本Issueはファイル整理とドキュメント更新のみをスコープとする。

## 制約・前提条件

- `legacy/` 配下のファイルは**参照専用**。動作する必要はなく、import可能である必要もない
- `legacy/` 内のPythonファイルは `pyproject.toml` のパッケージに含めない
- V7コード (`dao_harness/`) および V7テスト (`tests/`) は `legacy/` 内のモジュールに一切依存してはならない
- `git mv` を使用してgit履歴の追跡性を維持する

## 方針

### Phase 1: V5/V6テストの仕分け

現在 `tests/` に V5テスト（`bugfix_agent` を import）と V7テスト（`dao_harness` を import）が混在している。移動前に仕分けが必要。

**V5テスト（`legacy/` へ移動）:**

| ファイル | 根拠 |
|---|---|
| `tests/test_prompts.py` | `from bugfix_agent.prompts import ...` |
| `tests/test_handlers.py` | `from bugfix_agent.agent_context import ...` |
| `tests/test_issue_provider.py` | `from bugfix_agent.agent_context import ...` |
| `tests/conftest.py` | `from bugfix_agent.agent_context import AgentContext` 等。V5フィクスチャ専用 |
| `tests/utils/providers.py` | `from bugfix_agent.providers import IssueProvider` |
| `tests/utils/context.py` | `from bugfix_agent.agent_context import AgentContext` |
| `tests/utils/__init__.py` | `from bugfix_agent.providers import ...` |

**V7テスト（`tests/` に残す）:**

| ファイル | 根拠 |
|---|---|
| `tests/test_adapters.py` | `dao_harness.adapters` のテスト |
| `tests/test_cli_args.py` | `dao_harness.cli` のテスト |
| `tests/test_cli_streaming_integration.py` | `dao_harness.cli` のテスト |
| `tests/test_cycle_limit.py` | `dao_harness.models` のテスト |
| `tests/test_e2e_cli.py` | `dao_harness.adapters` E2Eテスト |
| `tests/test_logging_integration.py` | `dao_harness.logger` のテスト |
| `tests/test_prompt_builder.py` | `dao_harness.prompt` のテスト |
| `tests/test_run_logger.py` | `dao_harness.logger` のテスト |
| `tests/test_session_state.py` | `dao_harness.state` のテスト |
| `tests/test_skill_validation.py` | `dao_harness.skill` のテスト |
| `tests/test_start_logic.py` | `dao_harness.runner` のテスト |
| `tests/test_state_persistence.py` | `dao_harness.state` のテスト |
| `tests/test_verdict_parser.py` | `dao_harness.verdict` のテスト |
| `tests/test_workflow_execution.py` | `dao_harness.runner` のテスト |
| `tests/test_workflow_parser.py` | `dao_harness.workflow` のテスト |
| `tests/test_workflow_validator.py` | `dao_harness.workflow` のテスト |

**conftest.py の扱い**: 現在の `tests/conftest.py` はV5フィクスチャ専用（`bugfix_agent` import）。V7テストはconftest.pyのフィクスチャを使用していないため、そのまま `legacy/` へ移動する。V7テストにconftest.pyが必要になった場合は別途作成する。

### Phase 2: ファイル移動

`git mv` で以下を `legacy/` に移動:

```
legacy/
├── bugfix_agent/                      # V5/V6パッケージ
├── bugfix_agent_orchestrator.py       # V5エントリポイント
├── test_bugfix_agent_orchestrator.py  # V5統合テスト（ルート直下）
├── prompts/                           # V6プロンプト
├── config.toml                        # V5設定
├── AGENT.md                           # V5エージェント指示書
├── tests/                             # V5テスト
│   ├── conftest.py
│   ├── test_prompts.py
│   ├── test_handlers.py
│   ├── test_issue_provider.py
│   └── utils/                         # V5テストユーティリティ
│       ├── __init__.py
│       ├── context.py
│       └── providers.py
└── docs/                              # V5ドキュメント
    ├── ARCHITECTURE.ja.md
    ├── E2E_TEST_FINDINGS.md
    └── TEST_DESIGN.md
```

### Phase 3: pyproject.toml 更新

- `include = ["bugfix_agent*", "dao_harness*"]` → `include = ["dao_harness*"]`
- V5関連コメント削除

### Phase 4: docs/ARCHITECTURE.md 更新

L159-163 の「V6 → V7 移行」セクションを、移動完了を反映した記述に更新。

### Phase 5: README.md 新規作成

V7 dao_harness ベースの内容で書き換え。以下を含む:
- プロジェクト概要（dao_harnessの役割）
- 3層アーキテクチャの簡潔な説明
- セットアップ手順
- CLIエントリポイントは未実装のため、起動方法セクションは設けない（別Issue対応）
- 開発ワークフロー（`/issue-create` ~ `/issue-close`）
- 品質チェックコマンド
- ドキュメントリンク一覧
- `legacy/` の説明（V5/V6参照用）
- README 冒頭に「V7 (dao_harness) が現在の正規エントリポイントであり、`legacy/` は参照専用で非サポート」を明記する
- README の詳細は `docs/ARCHITECTURE.md` および `docs/dev/*` に委譲し、過剰な詳細化を避ける

### Phase 6: 検証

- `ruff check dao_harness/ tests/ && ruff format --check dao_harness/ tests/ && mypy dao_harness/ && pytest` が全パス
- `dao_harness/` と `tests/` から `bugfix_agent` への import が存在しないことを grep で確認

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。

### Small テスト
- V7テスト（`tests/` に残るテスト全件）が全パスすること（純粋なロジック・バリデーション・マッピングの検証）
- V5テスト移動後に `pytest` のテスト収集でエラーが発生しないこと（import失敗等）
- `grep -r "from bugfix_agent\|import bugfix_agent" dao_harness/ tests/` が0件であること（依存隔離の検証）

### Medium テスト
- V7テストのうち、ファイルI/Oを伴うテスト（ワークフローYAML読み込み・状態永続化等）が正常パスすること（ファイルI/O結合の検証）
- `legacy/` ディレクトリ内のファイルが `pyproject.toml` のパッケージ探索対象に含まれないことを、setuptools の `find_packages` 結果で検証（パッケージ構成の結合検証）

### Large テスト
- サブプロセスで `pip install -e ".[dev]"` を実行し、インストール後に `import bugfix_agent` が `ModuleNotFoundError` になることを検証（パッケージ配布境界のE2E検証）
- サブプロセスで `pytest --collect-only tests/` を実行し、収集エラー（ImportError等）が0件であることを検証（テスト基盤のE2E疎通）

### スキップするサイズ（該当する場合のみ）
- なし

### 検証手順（テスト外）
以下はテストサイズ分類ではなく、Phase 6 の品質ゲートとして実施する静的解析:
- `ruff check dao_harness/ tests/ && ruff format --check dao_harness/ tests/`
- `mypy dao_harness/`

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | ADRは歴史記録。変更不要 |
| docs/ARCHITECTURE.md | あり | V6→V7移行セクションの記述更新 |
| docs/dev/ | なし | V7向けガイド、変更不要 |
| docs/cli-guides/ | なし | CLI仕様に変更なし |
| CLAUDE.md | なし | 規約変更なし |
| README.md | あり | V7ベースで全面書き換え |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| ADR-003: CLIスキルハーネスへの転換 | `docs/adr/003-skill-harness-architecture.md` | V6→V7移行の意思決定記録。「bugfix_agent/ は参照用アーカイブ。保守・機能追加の対象外」 |
| V7アーキテクチャ | `docs/ARCHITECTURE.md` | L161-163: 「V7安定後にbugfix_agent/を削除予定」→ 今回legacy/への移動で実施 |
| Issue #57 | `https://github.com/apokamo/dev-agent-orchestra/issues/57` | V7実装完了・マージ済みの記録 |
| V5テストのimport分析 | `grep -r "from bugfix_agent" tests/` | conftest.py, test_handlers.py, test_issue_provider.py, test_prompts.py, utils/ がV5依存。V7テストは全てdao_harness importのみ |
| pyproject.toml エントリポイント確認 | `pyproject.toml` L30付近 | `[project.scripts]` がコメントアウト済み。`dao` コマンドは未定義。CLIは `python -m dao_harness` 経由で起動 |
| テスト規約 | `docs/dev/testing-convention.md` | S=外部依存なし純粋ロジック、M=ファイルI/O・DB・内部サービス結合、L=実API・E2E・外部サービス疎通 |
