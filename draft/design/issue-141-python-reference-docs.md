# [設計] Python 品質規約 6 本を docs/reference/python/ へ移植

Issue: #141

## 概要

kamo2 の `docs/reference/backend/` に整備された Python コード品質規約 6 本を、kaji 固有の実装・ツール・ドメインに適応させて `docs/reference/python/` へ移植する。

## 背景・目的

kaji には `docs/dev/testing-convention.md` と `docs/reference/testing-size-guide.md` があるが、Python コード本体（スタイル・命名・型ヒント・docstring・エラー処理・ロギング）の規約が未整備である。AI エージェントが一貫性のないコードを書く原因になっており、成文化による品質担保が目的。

## インターフェース

### 入力

- 移植元: `/home/aki/dev/kamo2/docs/reference/backend/` 以下の 6 ファイル
- 退避済みドラフト: `/tmp/kaji-python-docs/` （`python-style.md`・`naming-conventions.md` の 2 本）
- kaji 実装参照: `kaji_harness/errors.py`, `kaji_harness/logger.py`, `kaji_harness/models.py`

### 出力

| # | ファイル | 移植元 |
|---|---------|--------|
| 1 | `docs/reference/python/python-style.md` | `python-style.md` |
| 2 | `docs/reference/python/naming-conventions.md` | `naming-conventions.md` |
| 3 | `docs/reference/python/type-hints.md` | `type-hints.md` |
| 4 | `docs/reference/python/docstring-style.md` | `documentation.md` |
| 5 | `docs/reference/python/error-handling.md` | `error-handling-conventions.md` |
| 6 | `docs/reference/python/logging.md` | `logging-conventions.md` |

追加更新:
- `docs/README.md` — Reference セクションに 6 本追記
- `CLAUDE.md` — Documentation 表に 6 本追記

### 使用例

AI エージェントが実装時に参照する:

```
kaji_harness/ のコードを書く際に docs/reference/python/naming-conventions.md を参照し、
動詞の使い分け・プライベート命名・kaji 固有語のルールを確認する。
```

## 制約・前提条件

- `docs/dev/testing-convention.md` / `docs/reference/testing-size-guide.md` は変更しない（スコープ外）
- `kaji_harness/errors.py` / `kaji_harness/logger.py` 実装コードは変更しない（規約策定のみ）
- kamo2 固有語（kamo2 / FastAPI / 金融 / Decimal / JPX / 株価）を残存させない
- Pydantic ではなく dataclass ベースで記述（kaji は dataclass のみ使用）
- 非同期（async/await）の記述を含めない（kaji は同期モデル）
- Python 3.11+ 記法で統一（`list[str]`、`X | None`、`from __future__ import annotations`）
- line-length は 100 文字（`pyproject.toml` の `[tool.ruff] line-length = 100` に準拠）
- コード例は `kaji_harness/` 実装からの実例に差し替える

## 方針

### 共通適応アルゴリズム

各ファイルに対して以下の順序で処理する:

1. **削除**: kamo2/FastAPI/金融/DB/Decimal/JPX/株価に関するセクション・例を除去
2. **置換**: kamo2 の例 → kaji 実例（WorkflowRunner, SessionState, Verdict, Step, RunLogger 等）
3. **追加**: kaji 固有概念（verdict, step_id, cycle, workflow, skill, harness）の命名規則・使用例
4. **検証**: `grep -iE "(kamo2|fastapi|金融|stock|JPX|株価|Decimal)" <file>` でゼロヒットを確認

### ファイル別の主な適応内容

**python-style.md** （退避済みドラフトを使用）:
- 行長 100 文字（88 → 100）
- FastAPI / Decimal / 金融例を kaji 例に置換済み
- 退避済みドラフトをベースにレビュー・補完する

**naming-conventions.md** （退避済みドラフトを使用）:
- kaji 採用語（step/verdict/cycle/workflow/skill）の統一規則を明記済み
- 退避済みドラフトをベースにレビュー・補完する

**type-hints.md** （新規適応）:
- Pydantic の `BaseModel` / `Field` を `@dataclass` に置換
- `Decimal` 型エイリアスを削除
- `from __future__ import annotations` を標準として冒頭に記載
- kaji_harness/models.py の実例を使用

**docstring-style.md** （新規適応）:
- ファイル名を `documentation.md` → `docstring-style.md` に変更
- Sphinx / mkdocstrings セクションを削除
- 金融ドメイン例を kaji 例（Step, Verdict, RunLogger）に差し替え

**error-handling.md** （新規適応）:
- `kaji_harness/errors.py` の HarnessError 階層（HarnessError → ConfigNotFoundError / WorkflowValidationError / VerdictNotFound 等）を基礎に記述
- FastAPI 例外ハンドラ節・HTTP ステータス対応節を削除
- CLI 出力エラー（CLIExecutionError）・verdict エラー（VerdictNotFound / VerdictParseError）の処理規約を追加

**logging.md** （新規適応）:
- `kaji_harness/logger.py` の RunLogger（JSONL 形式）と `_write()` パターンを基礎に記述
- structlog 前提を標準 `logging` モジュールに変更
- フィールド: `ts`, `event`, `step_id`, `verdict`, `cost`, `session_id` を標準フィールドとして定義
- 株価関連フィールドを kaji 固有フィールドに差し替え

### インデックス更新

- `docs/README.md`: Reference セクションに `docs/reference/python/` 以下 6 本を追記
- `CLAUDE.md`: Documentation 表に 6 本を追記

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ
- **docs-only**（実行時コード変更なし）

### 変更固有検証

1. **kamo2 固有語の残存チェック**:
   ```bash
   grep -rniE "(kamo2|fastapi|金融|stock|JPX|株価|Decimal)" docs/reference/python/
   ```
   ゼロヒットであることを確認

2. **リンク整合性確認**:
   ```bash
   make verify-docs
   ```
   `docs/reference/python/` 内の相互リンクおよび `docs/README.md` / `CLAUDE.md` からのリンクがすべて解決することを確認

3. **完了条件チェックリスト確認**: Issue の完了条件 6 項目をすべて満たすことを確認

### 恒久テストを追加しない理由

以下の 4 条件をすべて満たすため、新規の恒久回帰テストは追加しない:

1. 独自ロジックの追加・変更をほぼ含まない（Markdown ドキュメントの新規作成のみ）
2. 想定される不具合パターン（リンク切れ・不整合）は既存の `make verify-docs` で捕捉済み
3. 新規テストを追加しても回帰検出情報がほとんど増えない（規約文書の内容変化は自動検出不能）
4. テスト未追加の理由を上記のとおりレビュー可能な形で説明できる

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし（既存ツール構成の文書化） |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | なし | 開発ワークフロー・手順変更なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | あり | Documentation 表に 6 本追記（規約参照先として必要） |
| docs/README.md | あり | Reference セクションに 6 本追記 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| kamo2 移植元ドキュメント群 | `/home/aki/dev/kamo2/docs/reference/backend/` | 移植元 6 ファイル。kaji 環境で `ls` / `cat` で直接参照可能 |
| 退避済みドラフト | `/tmp/kaji-python-docs/` | `python-style.md` / `naming-conventions.md` の kaji 適応済みドラフト |
| kaji errors.py | `kaji_harness/errors.py` | HarnessError 階層の実装。error-handling.md の主要一次情報 |
| kaji logger.py | `kaji_harness/logger.py` | RunLogger の JSONL 実装。logging.md の主要一次情報 |
| kaji models.py | `kaji_harness/models.py` | dataclass 実例（Step, Verdict, CostInfo 等）。type-hints.md / naming-conventions.md の実例基礎 |
| pyproject.toml ruff 設定 | `pyproject.toml` L1〜（`[tool.ruff]` セクション） | `line-length = 100`, `target-version = "py311"` — スタイル規約の根拠 |
| PEP 484 — Type Hints | https://peps.python.org/pep-0484/ | Python 型ヒント仕様。type-hints.md の一次情報 |
| PEP 257 — Docstring Conventions | https://peps.python.org/pep-0257/ | Google スタイル docstring の根拠。docstring-style.md の一次情報 |
| Python logging HOWTO | https://docs.python.org/3/howto/logging.html | 標準 logging モジュール。logging.md の一次情報 |
