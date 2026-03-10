# [設計] V5/V6旧ファイルをlegacy/に整理しV7基盤を明確化

Issue: #59

## 概要

V5/V6の旧ファイルを `legacy/` ディレクトリに移動し、`pyproject.toml`・`README.md`・`docs/ARCHITECTURE.md` をV7基盤に合わせて更新する。

## 背景・目的

V7（dao_harness）への移行が完了し、#57/#58でマージ済み。しかしリポジトリルートにV5/V6のコード・テスト・ドキュメントが混在しており、以下のリスクがある:

1. 新規参加者がV5コードを誤って修正・使用する
2. `pyproject.toml` が `bugfix_agent*` を含み、不要なパッケージが公開される
3. `README.md` がV5の内容で、プロジェクトの現状を反映していない

旧ファイルは `git tag v6.0` およびgit履歴で完全に参照可能だが、`legacy/` に退避することでディレクトリを辿って参照できる利便性を維持する。

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
# bugfix_agent/  bugfix_agent_orchestrator.py  config.toml  ...

# V7のコマンドは変更なし
dao run workflows/feature-development.yaml 57
```

## 制約・前提条件

- `legacy/` 配下のファイルは保守対象外（参照のみ）
- `legacy/` 内のPythonファイルは `pyproject.toml` のパッケージに含めない
- V7テスト (`tests/`) は `legacy/` 内のモジュールに依存してはならない
- `git mv` を使用してgit履歴の追跡性を維持する

## 方針

### Phase 1: ファイル移動

`git mv` で以下を `legacy/` に移動:

```
legacy/
├── bugfix_agent/                      # V5/V6パッケージ
├── bugfix_agent_orchestrator.py       # V5エントリポイント
├── test_bugfix_agent_orchestrator.py  # V5統合テスト
├── test_prompts.py                    # V6プロンプトテスト (tests/ から移動)
├── prompts/                           # V6プロンプト
├── config.toml                        # V5設定
├── AGENT.md                           # V5エージェント指示書
└── docs/                              # V5ドキュメント
    ├── ARCHITECTURE.ja.md             # V5設計書
    ├── E2E_TEST_FINDINGS.md           # V5 E2Eレポート
    └── TEST_DESIGN.md                 # V5テスト設計
```

### Phase 2: pyproject.toml 更新

- `include = ["bugfix_agent*", "dao_harness*"]` → `include = ["dao_harness*"]`
- V5関連コメント削除

### Phase 3: docs/ARCHITECTURE.md 更新

L159-163 の「V6 → V7 移行」セクションを、移動完了を反映した記述に更新。

### Phase 4: README.md 新規作成

V7 dao_harness ベースの内容で書き換え。以下を含む:
- プロジェクト概要（dao_harnessの役割）
- 3層アーキテクチャの簡潔な説明
- セットアップ手順
- CLI コマンド (`dao run`)
- 開発ワークフロー（`/issue-create` ~ `/issue-close`）
- 品質チェックコマンド
- ドキュメントリンク一覧
- `legacy/` の説明（V5/V6参照用）

### Phase 5: 検証

- `ruff check dao_harness/ tests/ && ruff format --check dao_harness/ tests/ && mypy dao_harness/ && pytest` が全パス
- `legacy/` 配下のモジュールが V7 コードから import されていないことを grep で確認

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。

### Small テスト
- 既存の `tests/` 配下のテストが全パスすること（ファイル移動による回帰がないことの確認）
- `tests/test_prompts.py` 移動後に `pytest` のテスト収集でエラーが発生しないこと

### Medium テスト
- `pip install -e ".[dev]"` が `legacy/` 配下を含まずに正常完了すること
- `mypy dao_harness/` が `bugfix_agent` への参照なしでパスすること

### Large テスト
- `dao run --help` が正常動作すること（CLIエントリポイントの疎通）

### スキップするサイズ（該当する場合のみ）
- なし（全サイズ実施可能）

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
| git tag v6.0 | `git show v6.0` | V6時点のスナップショット。legacy/移動後も参照可能 |
