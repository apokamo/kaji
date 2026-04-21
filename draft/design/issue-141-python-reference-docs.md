# [設計] Python 品質規約 6 本を docs/reference/python/ へ移植

Issue: #141

## 概要

kamo2 の `docs/reference/backend/` に整備された Python コード品質規約 6 本を、kaji 固有の実装・ツール・ドメインに適応させて `docs/reference/python/` へ移植する。

## 背景・目的

kaji には `docs/dev/testing-convention.md` と `docs/reference/testing-size-guide.md` があるが、Python コード本体（スタイル・命名・型ヒント・docstring・エラー処理・ロギング）の規約が未整備である。AI エージェントが一貫性のないコードを書く原因になっており、成文化による品質担保が目的。

### 期待読者と目的

6 本すべて **AI エージェント（Claude 等）が `kaji_harness/` コードを実装・修正する際に参照する規約** を主目的とする。人間開発者も参照するが、AI エージェントが機械的にルールを適用できる粒度での記述を優先する。

| ドキュメント | AI エージェントが使う場面 |
|---|---|
| python-style.md | フォーマット・インポート順・文字列クォート等の実装時チェック |
| naming-conventions.md | 変数名・関数名・kaji 固有語の一貫した命名 |
| type-hints.md | 型アノテーションパターン・dataclass の書き方 |
| docstring-style.md | Google style docstring の書き方・必須/省略可セクションの判断 |
| error-handling.md | 例外クラス選択・raise の場所・握り潰し禁止ルール |
| logging.md | RunLogger を呼ぶ場所・新規イベント追加時のフィールド設計 |

## インターフェース

### 入力

- 移植元: `/home/aki/dev/kamo2/docs/reference/backend/` 以下の 6 ファイル（レビュー可能な恒久パス）
- kaji 実装参照: `kaji_harness/errors.py`, `kaji_harness/logger.py`, `kaji_harness/models.py`

> **注記**: 作業途中の `/tmp/kaji-python-docs/` に存在するドラフト 2 本（`python-style.md` / `naming-conventions.md`）は一時作業物であり一次情報ではない。実装時に参照する場合は、kamo2 移植元と kaji 実装を正として手元でレビュー・補完すること。設計上の根拠には使用しない。

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

**python-style.md** （kamo2 移植元から新規適応）:
- 行長 100 文字（88 → 100 に変更）
- FastAPI / Decimal / 金融例を kaji 例（Step, Verdict, WorkflowRunner 等）に置換
- kaji 採用ツール（ruff / mypy / pytest）の説明に統一

**naming-conventions.md** （kamo2 移植元から新規適応）:
- kaji 採用語（step/verdict/cycle/workflow/skill）の統一規則を明記
- 金融ドメイン節・API/DB 節を削除

**type-hints.md** （新規適応）:
- Pydantic の `BaseModel` / `Field` を `@dataclass` に置換
- `Decimal` 型エイリアスを削除
- `from __future__ import annotations` を標準として冒頭に記載
- kaji_harness/models.py の実例を使用

**docstring-style.md** （新規適応）:
- ファイル名を `documentation.md` → `docstring-style.md` に変更
- スタイル: **Google style docstring を採用**（CLAUDE.md 既定の「Google docstrings」に準拠）
- 一次情報: Google Python Style Guide §3.8 (https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)
- Sphinx / mkdocstrings セクションを削除
- 金融ドメイン例を kaji 例（Step, Verdict, RunLogger）に差し替え
- PEP 257 は「モジュール冒頭 docstring 必須」「1行サマリー」等の一般規約として補足参照にとどめる

**error-handling.md** （新規適応）:
- `kaji_harness/errors.py` の HarnessError 階層（HarnessError → ConfigNotFoundError / WorkflowValidationError / VerdictNotFound 等）を基礎に記述
- FastAPI 例外ハンドラ節・HTTP ステータス対応節を削除
- CLI 出力エラー（CLIExecutionError）・verdict エラー（VerdictNotFound / VerdictParseError）の処理規約を追加

**logging.md** （新規適応）:
- **目的**: 現行 `kaji_harness/logger.py` の RunLogger JSONL 契約の文書化（一般的な Python logging 規約ではない）
- `RunLogger` は Python 標準 `logging` モジュールを使用しない。`_write()` で JSONL を直接書き出す専用実装であり、文書はこの契約を記述する
- 全イベント共通フィールド: `ts`（ISO 8601 UTC）, `event`（文字列）のみ
- イベント別フィールド（実装から導出）:
  - `workflow_start`: `issue` (int), `workflow` (str)
  - `step_start`: `step_id`, `agent`, `model`, `effort`, `session_id`（各 str | None）
  - `step_end`: `step_id` (str), `verdict` (dict), `duration_ms` (int), `cost` (dict | None)
  - `cycle_iteration`: `cycle_name` (str), `iteration` (int), `max_iterations` (int)
  - `workflow_end`: `status` (str), `cycle_counts` (dict), `total_duration_ms` (int), `total_cost` (float | None), `error` (str, 条件付き)
- kamo2 移植元の structlog 前提・株価関連フィールドは完全削除
- 規約として追加するのは「新規イベントを追加する際の命名・フィールド設計ガイドライン」に限定

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
| kamo2 移植元ドキュメント群 | `/home/aki/dev/kamo2/docs/reference/backend/` | 移植元 6 ファイル。kaji 環境で `ls` / `cat` で直接参照可能な恒久パス |
| kaji errors.py | `kaji_harness/errors.py` | HarnessError 階層（HarnessError → ConfigNotFoundError / VerdictNotFound 等）の実装。error-handling.md のソースオブトゥルース |
| kaji logger.py | `kaji_harness/logger.py` | RunLogger の JSONL 実装。logging.md のソースオブトゥルース。イベント定義・フィールド仕様はすべてここを参照 |
| kaji models.py | `kaji_harness/models.py` | dataclass 実例（Step, Verdict, CostInfo 等）。type-hints.md / naming-conventions.md の実例基礎 |
| pyproject.toml ruff 設定 | `pyproject.toml` L1〜（`[tool.ruff]` セクション） | `line-length = 100`, `target-version = "py311"` — スタイル規約の根拠 |
| PEP 484 — Type Hints | https://peps.python.org/pep-0484/ | Python 型ヒント仕様。type-hints.md の一次情報 |
| Google Python Style Guide §3.8 | https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings | Google style docstring の規約本体。docstring-style.md の主要一次情報（CLAUDE.md「Google docstrings」の根拠） |
| PEP 257 — Docstring Conventions | https://peps.python.org/pep-0257/ | docstring 一般規約（モジュール冒頭 docstring 必須・1行サマリー等）。Google style の補足参照 |
