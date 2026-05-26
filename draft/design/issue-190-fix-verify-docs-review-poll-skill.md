# [設計] verify-docs が code block 内の正規表現を Markdown link と誤検出する欠陥を修正

Issue: #190

## 概要

`scripts/check_doc_links.py` の link 抽出ロジックを修正し、Markdown の fenced code block（` ``` ` / ` ~~~ `）内およびインラインコード（`` ` ` ``）内の文字列を link 抽出対象から除外する。これにより `make verify-docs` が `.claude/skills/review-poll/SKILL.md:82` の sed 正規表現 `s#.*[:/]([^/]+)/[^/]+(\.git)?$#\1#` を broken link として誤検出する現象を解消する。

## 背景・目的

### Observed Behavior (OB)

現行 `main` (`2146e6d`) で `source .venv/bin/activate && make verify-docs` を実行すると以下が出力され、exit 2 で fail する:

```text
python3 scripts/check_doc_links.py docs/ README.md CLAUDE.md .claude/skills/
.claude/skills/review-poll/SKILL.md:82: broken link: [^/]+
make: *** [Makefile:33: verify-docs] エラー 1
```

該当行（`.claude/skills/review-poll/SKILL.md:82`）は fenced code block (` ```bash `) 内の sed コマンド:

```bash
OWNER=$(echo "$ORIGIN" | sed -E 's#.*[:/]([^/]+)/[^/]+(\.git)?$#\1#')
```

`scripts/check_doc_links.py` の `LINK_PATTERN = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+(?:\s+\"[^\"]*\")?)\)")` で対象行を `finditer` すると `full_match='[:/]([^/]+)'` / `target='[^/]+'` がマッチし（`[:/]` を `[text]` 部、`([^/]+)` を `(target)` 部として解釈）、スクリプトは `target='[^/]+'` をパス解決対象として扱い `broken link: [^/]+` を出力する。`scripts/check_doc_links.py:23` の `LINK_PATTERN` 定義は code block 文脈を考慮せず content 全体に `finditer` を当てているのが直接原因。

### Expected Behavior (EB)

Markdown のコードブロック（fenced ` ``` ` ブロック内、およびインラインコード `` ` `` 内）に出現する `[text](target)` 風の文字列は link 検査対象から除外され、実在する Markdown link 構文のみが検査される。これは CommonMark 仕様の以下定義に沿う:

- **Fenced code blocks** (CommonMark 0.31.2 § 4.5): 行頭 0–3 スペース + 3 個以上連続する `` ` `` または `~`（同一文字）で開閉し、内部の inline 構文（link / emphasis / 等）はパースされない。
- **Code spans** (CommonMark 0.31.2 § 6.1): backtick string で囲まれた範囲は literal text として扱われ、link 構文はマッチしない。

ただし本 Issue では **インデント済みコードブロック**（4 スペース / タブインデントによる code block）は対象外とする。理由: 本リポジトリの docs / skills は fenced code block を一貫使用しており、現実の偽陽性発生源は fenced / inline に限定される。

### EB の一次情報

- 仕様: `scripts/check_doc_links.py:22-23` の `LINK_PATTERN` 定義（`Matches [text](target) but NOT ![text](target)` コメント付き）が link 抽出の唯一の定義。
- CommonMark 0.31.2 仕様:
  - Fenced code blocks: https://spec.commonmark.org/0.31.2/#fenced-code-blocks
  - Code spans: https://spec.commonmark.org/0.31.2/#code-spans
- 既存テスト: `tests/test_check_doc_links.py` に link 抽出の振る舞いを規定するテスト群が存在。本修正で fenced / inline code 内の擬似 link を無視する回帰テストを追加する。

## 再現手順

1. 前提: `main` (`2146e6d`) または `fix/190` の修正前 commit の worktree、`source .venv/bin/activate` 済み
2. 実行:
   ```bash
   make verify-docs
   ```
3. 観測される出力（OB）:
   ```text
   .claude/skills/review-poll/SKILL.md:82: broken link: [^/]+
   make: *** [Makefile:33: verify-docs] エラー 1
   ```

## 根本原因（Root Cause）

`scripts/check_doc_links.py:93` の `LINK_PATTERN.finditer(content)` は Markdown ファイル全体を 1 つの文字列として走査し、Markdown のコンテキスト（fenced code block 内 / 通常段落内 / inline code 内）を区別しない。`LINK_PATTERN` 自体は `[text](target)` 構造を抽出する単純な regex で、code 文脈の inline 構文無効化を考慮する仕掛けが入っていない。

- **なぜ間違っているか**: CommonMark 仕様では fenced code block と code span の内部で link 構文はパースされない。link checker が link を抽出する以上、Markdown の構造に従い code 文脈を除外する責務がある。
- **いつから壊れているか**: `c2d4a66 docs: add docs-maintenance workflow and i-doc-* skills (#111)` で `scripts/check_doc_links.py` が追加された時点から、code 文脈除外ロジックは一度も実装されていない。`.claude/skills/review-poll/SKILL.md` の sed 正規表現が現実的な偽陽性源として顕在化したのは review-poll skill 追加以降。
- **同じ原因で他に壊れている箇所**: 現行 repo で `make verify-docs` が報告する偽陽性は `review-poll/SKILL.md:82` の 1 箇所のみ（実測）。ただし、今後 docs / skills に正規表現や Markdown link 風コードサンプルが追加された場合に同種の偽陽性が再発しうる構造的欠陥。本修正はこの再発リスクごと封じる。

## インターフェース

`scripts/check_doc_links.py` の **公開挙動**（CLI 仕様 / exit code / 出力 format）は不変:

- CLI 引数仕様: 不変（`<path>...`、引数なしで `docs/` を走査）
- exit code: 不変（0 = 全 link 有効、1 = broken link 検出、2 = エラー）
- エラー出力 format: 不変（`<file>:<line>: broken link: <target>` / `<file>:<line>: missing anchor '<frag>' in <path>` / `<file>:<line>: link resolves outside repository: <target>`）

**振る舞いの差分**:

- fenced code block (` ``` ` / ` ~~~ `) 内の `[text](target)` 風文字列: 抽出対象から除外（修正前は誤検出）
- インラインコード (`` ` ... ` ``) 内の `[text](target)` 風文字列: 抽出対象から除外（修正前は誤検出）
- 通常段落内の `[text](target)`: 従来どおり抽出・検証（不変）
- インデント済みコードブロック内: 従来どおり抽出・検証（スコープ外、不変）

## 変更スコープ

- 変更ファイル:
  - `scripts/check_doc_links.py` — code 文脈除外ヘルパーを追加し、`validate_all()` が link 抽出前にコンテンツを前処理するよう変更
  - `tests/test_check_doc_links.py` — fenced / inline code 内の擬似 link 抽出を回避することを検証する回帰テスト群を追加
- スコープ外:
  - インデント済みコードブロックの除外（理由は EB セクション参照）
  - CommonMark の info string や code block の特殊エッジケース（言語 hint・空行内包等）の構文解析。fenced 開閉判定のみに限定
  - `Makefile` の `verify-docs` ターゲット定義（修正不要）

## 方針（修正アプローチ）

### 1. code 文脈除外ヘルパー `_strip_code_segments(content: str) -> str` を追加

- 入力: Markdown content（生テキスト）
- 出力: fenced code block 内およびインラインコード内の文字を空白 `' '` で置換した同じ長さの文字列（改行は保持）
- 同じ長さを維持することで、`_index_to_line()` による既存の line 番号計算ロジック（`scripts/check_doc_links.py:205-211`）に影響を与えない（`finditer` の match position と content 内の文字位置の対応が保たれる）

#### Fenced code block の検出（CommonMark § 4.5 準拠の minimal 実装）

行単位走査:

- 行頭 0–3 スペース + 3 個以上連続する同一の `` ` `` または `~` を持つ行を fence 行として認識
- 開いている fence が無い状態で fence 行を検出 → open（fence 文字種と長さを記録）
- 開いている fence がある状態で **同じ文字種かつ同じ長さ以上** の fence 行を検出 → close
- 内側の `[`, `]`, `(`, `)` 等を空白化（fence 行自身は前後の link 構造を含まないので空白化対象に含めるか開閉対象にするかは実装詳細）
- info string（fence 開行末尾の言語名）は単純無視

擬似コード:

```python
def _strip_code_segments(content: str) -> str:
    out: list[str] = []
    fence_char: str | None = None
    fence_len = 0
    for line in content.split("\n"):
        m = FENCE_PATTERN.match(line)  # ^ {0,3}(`{3,}|~{3,})
        if fence_char is None:
            if m:
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                out.append(" " * len(line))  # fence 行自体も中和
            else:
                out.append(_strip_inline_code(line))  # inline code span 除外
        else:
            # 内部行: 全文字を空白化
            out.append(" " * len(line))
            if m and m.group(1)[0] == fence_char and len(m.group(1)) >= fence_len:
                fence_char = None
                fence_len = 0
    return "\n".join(out)
```

#### Inline code span の検出（CommonMark § 6.1 準拠の minimal 実装）

通常段落内では、`` ` `` の run（連続する N 個の backtick）に対し同じ長さ N の closing run までを code span として扱い、中身を空白化する:

```python
def _strip_inline_code(line: str) -> str:
    # Match opening backtick run of length N, content (any non-backtick or shorter run),
    # closing backtick run of same length N.
    return CODE_SPAN_PATTERN.sub(lambda m: " " * len(m.group(0)), line)
```

正規表現案: `` (`+)(?:(?!\1)[^`]|`(?!\1))*\1 `` 相当（CommonMark の simplified 版）。N=1 を中心とした実用 case をカバーすれば本 Issue の偽陽性は解消する。

### 2. `validate_all()` の改修

`scripts/check_doc_links.py:85-101` の `validate_all()` で、`LINK_PATTERN.finditer(content)` の前に `stripped = _strip_code_segments(content)` を実行し、stripped 側で `finditer` する。line 番号計算用の `lines` は元の `content.split("\n")` のままにする（stripped と元 content は文字数・改行位置が一致するため `_index_to_line()` は同じ結果を返す）。

### 3. 既存挙動の不変性確保

- 既存テスト（`tests/test_check_doc_links.py` の現行 30+ ケース）が全て green のままであること
- 特に `test_image_links_skipped` / `test_self_anchor` / `test_link_to_nonexistent_file` 等の link 検出の中核挙動が回帰しないこと

## テスト戦略

### 変更タイプ

実行時コード変更（`scripts/check_doc_links.py` のロジック変更）。`docs/dev/testing-convention.md` § 実行時の振る舞いを変える変更 に従い Small / Medium / Large の各観点を定義する。

### Small テスト（`tests/test_check_doc_links.py` のヘルパーレベル）

新規ヘルパー `_strip_code_segments` のロジックを単体で検証する。`_load_module()` 経由で import し、文字列 in / 文字列 out で振る舞いを assert する。

- **fenced code block 内の `[text](target)` 風文字列が空白化される**: 入力 `` "```\n[link](b.md)\n```\n" `` → 出力で `[link](b.md)` 部分が空白化されることを確認
- **インラインコード内の `[text](target)` 風文字列が空白化される**: 入力 `"text \`[link](b.md)\` text"` → 出力で `[link](b.md)` 部分が空白化される
- **通常段落の link は残る**: 入力 `"see [link](b.md) here"` → 出力でも `[link](b.md)` が残る
- **出力長と改行位置が入力と一致する**: line 番号計算の正確性を担保するため、`len(out) == len(input)` および `out.count("\n") == input.count("\n")` が成立
- **fence 開閉の文字種一致**: ` ``` ` で開いた block は ` ~~~ ` では閉じない（CommonMark 準拠）
- **fence 長さの一致**: 4 個 backtick で開いた block は 3 個 backtick では閉じない、5 個以上では閉じる
- **インデント済みコードブロックは対象外**: 4 スペースインデント行内の `[text](target)` 風文字列は **空白化されず** 抽出対象として残る（スコープ外を確認する negative test）

bug 規定（`design-by-type/bug.md` § 8）の **再現テスト**: 上記のうち「fenced code block 内の `[^/]+` 風文字クラスが空白化される」テストが、修正前 Red / 修正後 Green の regression test として機能する。

### Medium テスト（`tests/test_check_doc_links.py` の subprocess レベル）

`_run(tmp_path, ...)` 経由で CLI 全体の振る舞いを E2E に検証する。

- **fenced code block 内の正規表現は誤検出されない**: `.md` ファイルに ` ```bash\n... [^/]+ ... \n``` ` を書き、 `_run` で exit 0 / `All Markdown links valid` を確認
- **fenced code block 内の擬似 link `[link](missing.md)` は誤検出されない**: 同様に exit 0 を確認
- **インラインコード内の擬似 link `` `[link](missing.md)` `` は誤検出されない**: exit 0 を確認
- **fenced code block 外の broken link は引き続き検出される**: 同一ファイル内で code block 外に `[link](missing.md)` がある場合 exit 1 / stderr に `missing.md` を含む
- **fenced code block と通常段落の混在**: code block 内 fake link + code block 外 valid link → exit 0
- **review-poll/SKILL.md パターンの回帰テスト**: 実際の sed 正規表現 `s#.*[:/]([^/]+)/[^/]+(\.git)?$#\1#` を fenced bash block に含む `.md` を生成 → exit 0

### Large テスト（既存 `TestRealRepo` の拡張）

既存の `test_repo_docs_have_no_broken_links`（`tests/test_check_doc_links.py:334-352`）が実 repo の docs / skills 全体に対し E2E で `check_doc_links.py` を実行している。本修正により `.claude/skills/review-poll/SKILL.md:82` 由来の偽陽性が消え、当該テストが green になることを Large レベルの最終確認とする。新規 Large テストの追加は不要。

### 受け入れ判定

- 完了条件 1（`fix/190` HEAD 上で `make verify-docs` が exit 0）: Large テスト群（既存 `TestRealRepo`）の通過と等価
- 完了条件 2（fenced + inline code 内除外）: Small + Medium テストで検証
- 完了条件 3（回帰テスト追加）: 上記 Small / Medium の新規テスト
- 完了条件 4（`make check` green）: lint / format / typecheck / test 全通過

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 既存技術選定の延長で、新規 ADR 起票案件ではない |
| docs/ARCHITECTURE.md | なし | アーキテクチャ境界の変更なし |
| docs/dev/ | なし | docs 品質ゲートのワークフロー手順自体は不変 |
| docs/reference/ | なし | Python 規約変更なし |
| docs/cli-guides/ | なし | `make verify-docs` の CLI 仕様変更なし |
| CLAUDE.md | なし | プロジェクト規約変更なし |
| `scripts/check_doc_links.py` の docstring | あり（軽微） | 関数 docstring に fenced / inline code 除外仕様を追記する程度。新規 reference doc は不要 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `scripts/check_doc_links.py` (現行実装) | `scripts/check_doc_links.py:22-23, 85-101, 205-211` | `LINK_PATTERN` は code 文脈無視で content 全体に `finditer`。line 番号は `_index_to_line` が `match.start()` から計算するため、code 除外で文字数を変えないことが互換性要件 |
| `tests/test_check_doc_links.py` (既存テスト) | `tests/test_check_doc_links.py:50-323` | 既存テスト群の振る舞い（image link skip / external skip / anchor 検証等）が回帰しないことを担保する基準 |
| `.claude/skills/review-poll/SKILL.md` (偽陽性発生源) | `.claude/skills/review-poll/SKILL.md:79-84` | 現実の偽陽性パターン。fenced ` ```bash ` 内の `sed -E 's#.*[:/]([^/]+)/[^/]+(\.git)?$#\1#'` が誤検出される |
| CommonMark 仕様 — Fenced code blocks | https://spec.commonmark.org/0.31.2/#fenced-code-blocks | "A fenced code block ... Tildes and backticks cannot be mixed. ... A closing code fence ... whose opening fence was 3 backticks may not be closed by 4 backticks, but a closing fence with 4 backticks may close a 3-backtick opening." 本設計の fence 開閉ルール（同一文字種・close は open 以上の長さ）の根拠 |
| CommonMark 仕様 — Code spans | https://spec.commonmark.org/0.31.2/#code-spans | "A backtick string is a string of one or more backtick characters that is neither preceded nor followed by a backtick. A code span begins with a backtick string and ends with a backtick string of equal length." inline code span の長さ一致開閉ルールの根拠 |
| `docs/dev/testing-convention.md` § 実行時の振る舞いを変える変更 | `docs/dev/testing-convention.md:63-66` | 「設計書のテスト戦略には Small / Medium / Large の各観点を定義する」本設計のテスト戦略構成の根拠 |
| `c2d4a66` (LINK_PATTERN 導入 commit) | `c2d4a66 docs: add docs-maintenance workflow and i-doc-* skills (#111)` | `scripts/check_doc_links.py` 初導入時点から code 文脈除外ロジックは未実装。`git log --oneline -S "LINK_PATTERN" -- scripts/check_doc_links.py` で確認 |
