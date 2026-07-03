# [設計] make verify-docs の対象に root AGENTS.md を追加する

Issue: #256

## 概要

`make verify-docs`（`scripts/check_doc_links.py` のラッパー）の検査対象に root `AGENTS.md` を
追加し、あわせて #243 で導入した暫定の個別チェック手順（`python3 scripts/check_doc_links.py AGENTS.md`）を
docs / skill から撤去する。実体は `Makefile` の `verify-docs` ターゲット 1 行への引数追加と、
2 箇所の docs/skill 記述の削除に閉じる。

## 背景・目的

#243 で `AGENTS.md` を常時適用ルールの正本として新設したが、当該 Issue は docs-only のため
`Makefile` 変更を範囲外とし、暫定手順として `i-doc-final-check/SKILL.md` と
`docs/dev/docs_maintenance_workflow.md` に `python3 scripts/check_doc_links.py AGENTS.md` の
個別チェックを追記していた。

現行の `verify-docs`（`Makefile:33`）の検査対象は
`docs/ README.md README.ja.md CLAUDE.md .claude/skills/` のみで root `AGENTS.md` を含まない。
このため `AGENTS.md` のリンク切れは通常の品質ゲート `make check`（→ `make verify-docs`）では
検出できず、保守者が暫定コマンドを手打ちする必要がある。

- **ユースケース**: kaji リポジトリで docs / skill を保守する開発者（および `make check` を回す
  CI / agent）が、`AGENTS.md` を編集して他 docs へのリンクを追加・変更したとき、リンク切れを
  暫定コマンドの記憶なしに `make verify-docs` で機械的に検出したい。
- **目的**: `AGENTS.md` を他 docs と同じ品質ゲート対象に昇格させ、暫定個別チェック手順を撤去して
  検出漏れと記憶コストを解消する。

## インターフェース

本 Issue は Python 公開 API を変更しない。変更するのは Make ターゲットの引数と docs/skill 記述。

### 入力

- `make verify-docs`（あるいは `make check` 経由）の呼び出し。追加引数・環境変数は増やさない。
- `scripts/check_doc_links.py` は既に複数のファイル/ディレクトリを位置引数として受け取る
  （暫定手順 `check_doc_links.py AGENTS.md` が単一ファイル引数で機能していた実績あり）。
  したがって既存引数リストへ `AGENTS.md` を追記するだけで挙動が拡張される。

### 出力

- `make verify-docs` の検査対象に root `AGENTS.md` が含まれ、`AGENTS.md` 内のリンク切れが
  あれば non-zero exit で検出される。リンクが健全なら従来どおり exit 0。
- 副作用: なし（リンク検査のみ。ファイル生成・書き換えなし）。

### 使用例

```bash
cd <worktree> && source .venv/bin/activate && make verify-docs
# → docs/ README.md README.ja.md CLAUDE.md .claude/skills/ AGENTS.md を一括検査
```

## 制約・前提条件

- `scripts/check_doc_links.py` 本体のロジックは変更しない（対象拡張は Makefile 引数のみ）。
- `verify-docs` の既存対象（`docs/` / `README*` / `CLAUDE.md` / `.claude/skills/`）は見直さない。
- `AGENTS.md` 自体の内容は変更しない。
- 変更種別は `chore`（`Makefile` 設定 + docs/skill 記述撤去）に閉じ、feat / fix を混在させない。
- 引数の並びは既存の慣習（ディレクトリと個別ファイルの混在列挙）に合わせ、末尾に `AGENTS.md` を追加する。

## 方針（Minimal How）

1. **`Makefile:33`**: `verify-docs` ターゲットの位置引数末尾に `AGENTS.md` を追加。

   ```make
   verify-docs:
   	python3 scripts/check_doc_links.py docs/ README.md README.ja.md CLAUDE.md .claude/skills/ AGENTS.md
   ```

2. **`.claude/skills/i-doc-final-check/SKILL.md`**: 暫定個別チェックの記述を撤去する。
   - `## 実施内容` 3 番目（56 行目）の「+ root `AGENTS.md` の個別チェック」を削除。
   - `## Step 3 詳細` のコードブロック（62-65 行目）から `python3 scripts/check_doc_links.py AGENTS.md`
     行を削除し、`make verify-docs` のみにする。
   - 暫定手順の注記ブロック（69-71 行目）を削除。`AGENTS.md` は `verify-docs` 対象に含まれる旨へ整合。
   - Verdict の evidence 例（103 行目）の「/ AGENTS.md 個別リンクチェック」文言を `make verify-docs` に一本化。

3. **`docs/dev/docs_maintenance_workflow.md:83`**: 「+ root `AGENTS.md` の個別チェック
   `python3 scripts/check_doc_links.py AGENTS.md`。現行 `verify-docs` 対象に root `AGENTS.md` が
   含まれないための暫定手順」の括弧内暫定説明を削除し、`make verify-docs` のみで整合確認する記述にする。

> `docs_maintenance_workflow.md:8` / `:79` の `AGENTS.md` 言及は「整理対象」「運用整合性の観点」として
> 列挙されているもので、暫定チェック手順ではない。撤去対象外。

## テスト戦略

### 変更タイプ

metadata-only（ビルド設定 = `Makefile` ターゲットの引数変更）+ docs-only（skill/docs の記述撤去）。
実行時 Python コード（`kaji_harness/`）の振る舞いは変更しない。

### 変更固有検証

- `make verify-docs` を実行し exit 0 を確認する（`AGENTS.md` 追加後もリンクが健全であること）。
- 対象拡張の実効性確認: `AGENTS.md` が実際に検査対象へ渡っていることを確認する。
  例として `AGENTS.md` に一時的な壊れリンクを挿入 → `make verify-docs` が non-zero で失敗することを
  確認 → 変更を破棄（恒久化しない、使い捨て検証）。あるいは検査対象の列挙が引数に含まれることを
  `make -n verify-docs`（dry-run）で目視確認する。
- `grep` で暫定手順文字列 `check_doc_links.py AGENTS.md` が
  `i-doc-final-check/SKILL.md` / `docs_maintenance_workflow.md` から消えていることを確認する。

### 恒久テストを追加しない理由（`docs/dev/testing-convention.md` の 4 条件）

1. 独自ロジックの追加・変更を含まない（`check_doc_links.py` 本体は不変、Makefile 引数追加のみ）。
2. 想定される不具合（`AGENTS.md` のリンク切れ）は、まさに本変更で `verify-docs` = 既存品質ゲートに
   取り込まれ、以後 `make check` / CI が恒久的に捕捉する。
3. Make ターゲット引数を検証する pytest を追加しても回帰検出情報はほとんど増えない
   （Makefile 引数は静的で、CI が実際に `make verify-docs` を実行することが最良の回帰シグナル）。
4. 以上の理由でテスト未追加はレビュー可能な形で説明できる。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | あり | `docs_maintenance_workflow.md` の暫定チェック手順を撤去（本 Issue の扱う内容そのもの） |
| docs/reference/ | なし | API 仕様・規約の変更なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| .claude/skills/ | あり | `i-doc-final-check/SKILL.md` の暫定チェック手順を撤去（本 Issue の扱う内容そのもの） |
| AGENTS.md / CLAUDE.md | なし | 内容変更しない（`AGENTS.md` は検査「対象」になるだけで記述は不変） |
| README* | なし | README には `verify-docs` の対象一覧を列挙していないため追従不要 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 現行 Makefile | `Makefile:32-33` | `verify-docs: python3 scripts/check_doc_links.py docs/ README.md README.ja.md CLAUDE.md .claude/skills/` — 現行対象に root `AGENTS.md` が無いことの一次証拠 |
| 暫定手順（skill） | `.claude/skills/i-doc-final-check/SKILL.md:62-71` | `python3 scripts/check_doc_links.py AGENTS.md` を「`verify-docs` 対象へ追加されるまでの暫定手順」と明記。撤去対象の一次証拠 |
| 暫定手順（docs） | `docs/dev/docs_maintenance_workflow.md:83` | 「現行 `verify-docs` 対象に root `AGENTS.md` が含まれないための暫定手順」と明記。撤去対象の一次証拠 |
| link checker が複数位置引数を取る実績 | 上記暫定手順 `check_doc_links.py AGENTS.md` | 単一ファイル引数で機能していた実績 → 既存引数リストへの追記で拡張可能なことの裏付け |
| テスト規約 | `docs/dev/testing-convention.md:52-76` | 「docs-only / metadata-only … 新規ロジックや持続的な回帰リスクがない限り機械的に S/M/L 全サイズを要求しない」— 恒久テスト省略判断の根拠 |
