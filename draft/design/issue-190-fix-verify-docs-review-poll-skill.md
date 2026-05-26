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

Markdown のコードブロック（fenced ` ``` ` ブロック内、およびインラインコード `` ` `` 内）に出現する `[text](target)` 風の文字列は link 検査対象から除外され、実在する Markdown link 構文のみが検査される。これは CommonMark 0.31.2 仕様の以下定義に沿う:

- **Fenced code blocks** (CommonMark 0.31.2 § 4.5): 行頭 0–3 スペース + 3 個以上連続する `` ` `` または `~`（同一文字）で開閉。内部の inline 構文（link / emphasis / 等）はパースされない。本 Issue では **CommonMark §5.2 List items の content indentation 内に置かれた fenced code block / §5.1 Block quote 内に置かれた fenced code block / 多層 container 内の fenced code block** を含む、CommonMark 仕様上 fenced code block と認識されるケース全般を除外対象とする。
- **Code spans** (CommonMark 0.31.2 § 6.1): backtick string で囲まれた範囲は literal text として扱われ、link 構文はマッチしない。複数行に跨る code span（CommonMark §6.1 "Line endings are treated like spaces" の規定）も対象。

#### Scope-out

- **インデント済みコードブロック** (CommonMark 0.31.2 § 4.4、4 スペース / タブインデントによる code block)。理由: 本リポジトリの docs / skills は fenced code block を一貫使用しており、現実の偽陽性発生源は fenced / inline に限定される。これは Issue #190 本文 § スコープ外 で明示された唯一の scope-out。

#### Soundness 要件（link checker としての健全性）

link checker の役割は **broken link を見逃さない**こと（false-negative の最小化）。fenced code block 除外は false-positive を減らすが、副作用で false-negative を増やしてはならない。具体的な要件:

- **未閉鎖 fenced block の安全な扱い**: opening fence のみで matching closing fence が存在しないまま EOF / containing block 終端に到達する場合、CommonMark §4.5 は「文書終端まで fenced block 継続」と規定するが、本実装ではそのような未閉鎖 fence の領域を **mask しない**（= 抽出対象として残す）。理由: 未閉鎖 fence は markdown source の typo / 編集途中の状態であることが多く、それ以降の段落にある broken link が silent に隠されると link checker としての価値が損なわれる。spec-pure CommonMark 挙動より safety を優先する。
- **未閉鎖 fence の検出条件 (round 6 MF-1 対応)**: closing fence の有無は **markdown-it-py がパース時に既に決定済み**であり、本実装は raw 行ベースの pattern match で再判定しない。具体的には `fence` token の `tok.map` (line span) と `tok.content` (中身の行数) の整合性比較で判定する:
  - 閉鎖済み: `span_lines == content_lines + 2`（open + content + close の 3 種類が積算される）
  - 未閉鎖: `span_lines == content_lines + 1`（open + content のみ）
  - ここで `span_lines = tok.map[1] - tok.map[0]`、`content_lines = tok.content.count("\n")`。markdown-it-py の content は必ず末尾 `\n` で正規化されているため、改行カウントが content 行数と一致する。
  - 判定不一致時（防御的フォールバック）は「未閉鎖」として扱う（link checker の soundness を優先）。

#### 公開挙動の不変性

- CLI 仕様 / exit code / 出力 format は不変（既存 § インターフェース 参照）
- 既存テストの振る舞いは regression なし

### EB の一次情報

- 仕様: `scripts/check_doc_links.py:22-23` の `LINK_PATTERN` 定義（`Matches [text](target) but NOT ![text](target)` コメント付き）が link 抽出の唯一の定義。
- CommonMark 0.31.2 仕様:
  - Fenced code blocks: https://spec.commonmark.org/0.31.2/#fenced-code-blocks
  - Code spans: https://spec.commonmark.org/0.31.2/#code-spans
  - List items: https://spec.commonmark.org/0.31.2/#list-items（content indentation の挙動、Example 263 で list item content + fenced block の組合せ動作を規定）
  - Block quotes: https://spec.commonmark.org/0.31.2/#block-quotes
- 実在する偽陽性パターン（修正前 `make verify-docs` で fail する場所）: `.claude/skills/review-poll/SKILL.md:82` の sed 正規表現 1 箇所のみ。
- 実在する CommonMark container-nested fenced block の例:
  - `.claude/skills/review/SKILL.md:96-106`, `.claude/skills/pr-verify/SKILL.md:97-123`, `.claude/skills/pr-fix/SKILL.md:84-110`, `.claude/skills/i-pr/SKILL.md:95-121` — ordered list item の content indentation 内に `     ```text ... ` ``` `` 形式の fenced block。内部内容は VERDICT block / コマンド例で、現状は `[text](target)` 構造を含まないため修正前でも偽陽性は発生していない。設計上は CommonMark §5.2 準拠で除外対象。
  - `.claude/skills/i-pr/SKILL.md:225-239` — block quote 内の fenced block (`> ```text ... > ` ``` ``)。内部に link 風文字列なし。CommonMark §5.1 + §4.5 で fenced と認識される。
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
  - `scripts/check_doc_links.py` — code 文脈除外ヘルパーを `markdown-it-py` ベースに置換し、`validate_all()` が link 抽出前にコンテンツを前処理するよう変更
  - `tests/test_check_doc_links.py` — CommonMark container 内 fenced block / 未閉鎖 fence 安全条件 / block quote 内 fenced block の各回帰テスト群を追加
  - `pyproject.toml` — `[dependency-groups].dev` に `markdown-it-py>=3.0` を追加（dev-only dependency。`scripts/check_doc_links.py` は `make verify-docs` 用の dev tool で、runtime / wheel には含まれない）
  - `uv.lock` — `uv sync` による自動更新
- スコープ外:
  - インデント済みコードブロック (CommonMark §4.4) の除外（理由は EB § scope-out 参照。Issue 本文の明示 scope-out）
  - inline code の解析を markdown-it-py に切り替えること。inline code span は既存の content 全体走査 regex (`_strip_inline_code_spans`) を継続利用する（複数行 code span 対応済み、回帰なし）
  - `Makefile` の `verify-docs` ターゲット定義（修正不要）
  - 既存 link 検証ロジック（anchor / repo 外 / image link skip 等）の挙動変更

## 方針（修正アプローチ）

### アーキテクチャ方針: regex から CommonMark parser ベースへの切り替え

これまでの round 1 / round 2 は regex ベースの fence 検出で各 container ケースに対応していたが、CommonMark §5.2 (Example 263) のような **content indentation で開始する fenced block** や、§5.1 block quote の中の fenced block、多層 container 内 fenced block を行単位 regex で正確に扱うのは原理的に困難で、毎回パッチを当てる形になり review cycle が収束しない。さらに「明示 closing fence のみで終了」とする以前の単純化規則は、未閉鎖 fence によって以降の通常段落の broken link を silent に隠す可能性があり、link checker の **soundness（false-negative の最小化）** を損なう。

本 round では実装基盤を **`markdown-it-py`（純 Python 製の CommonMark 0.31 準拠 parser）** に切り替え、token-level の line range 情報を用いて fenced code block の領域を mask する。これにより:

- list item / block quote / 多層 container 内の fenced block を正確に検出（CommonMark spec 準拠）
- inline code spans の解析は既存 regex を継続利用（複数行対応の `_strip_inline_code_spans` は round 2 で検証済み、parser に置換するメリット薄）
- 未閉鎖 fence は **mask 対象から除外** することで soundness を保証

### 1. dev dependency 追加

`pyproject.toml` の `[dependency-groups].dev` に追加:

```toml
[dependency-groups]
dev = [
    # ... existing entries ...
    "markdown-it-py>=3.0",
]
```

`scripts/check_doc_links.py` は `make verify-docs` 用の dev-only tool で、runtime（`kaji_harness/`）の依存関係には含まれない。`pyproject.toml` の `[project].dependencies` には追加しない。

### 2. fenced code block 検出ヘルパーの置換 (round 6: content/span 整合性ベース)

#### 設計変更の経緯 (round 5 → round 6 / MF-1 対応)

round 5 までは raw 行ベース pattern `^[ \t>]*<fence>{n,}[ \t]*$` で closing fence を判定していたが、**top-level fence の closing が 4 sp 以上字下げされた場合** (CommonMark §4.5 「closing fence は 0–3 sp までしか indent できない」に違反する形) を **明示 close と誤認** する false-positive が判明した (review-code MF-1 probe)。

```python
# round 5 までの誤判定 (MF-1 probe)
source = "```bash\n[real-broken](missing.md)\n    ```\n"
# markdown-it-py は spec 準拠で「unclosed」と扱う:
#   tok.map=[0, 3], tok.content='[real-broken](missing.md)\n    ```\n'
# しかし round 5 までの _is_explicit_closing_fence('    ```', '`', 3) は True を返し、
# 結果として soundness guard が機能せず block を mask → broken link を silent に隠す
```

container 文脈 (list / blockquote / 複合) と top-level の closing indent 制約を行ベース regex で区別するのは原理的に困難である。`[ \t>]*` を緩めれば top-level の 4 sp 制約違反を見逃し、厳しくすれば container 内 fence を誤判定する。

#### 解決方針: markdown-it-py の content/span に依拠する

closing fence の有無判定は **markdown-it-py がパース時に既に CommonMark 準拠で決定済み** であり、再判定は不要である。`fence` token の `tok.map` (line span) と `tok.content` (中身の改行数) の整合性比較のみで判定する:

| 状態 | span_lines | 関係式 |
|------|------------|--------|
| closed (open + content + close) | content_lines + 2 | `span_lines == content_lines + 2` |
| unclosed (open + content のみ) | content_lines + 1 | `span_lines == content_lines + 1` |

`span_lines = tok.map[1] - tok.map[0]`、`content_lines = tok.content.count("\n")`。markdown-it-py は `tok.content` を末尾 `\n` で正規化するため改行カウント = 内部行数となる。

実機検証 (worktree 上での Python probe):

| ケース | tok.map | tok.content | span | content_\\n | 判定 |
|--------|---------|-------------|------|------------|------|
| top-level closed (`\`\`\`bash\n[link](b.md)\n\`\`\`\n`) | `[0, 3]` | `'[link](b.md)\n'` | 3 | 1 | 3 == 1+2 → closed |
| **top-level 4-sp pseudo-close** (MF-1 probe) | `[0, 3]` | `'[real-broken](missing.md)\n    \`\`\`\n'` | 3 | 2 | 3 == 2+1 → **unclosed** |
| container 複合 closed (`1.  > \`\`\`text...`) | `[0, 3]` | `'[fake](missing.md)\n'` | 3 | 1 | 3 == 1+2 → closed |
| EOF まで unclosed | `[0, 4]` | `'...\n\n[real-broken](missing.md)\n'` | 4 | 3 | 4 == 3+1 → unclosed |
| empty closed (`\`\`\`\n\`\`\`\n`) | `[0, 2]` | `''` | 2 | 0 | 2 == 0+2 → closed |
| opening only (`\`\`\`\n`) | `[0, 1]` | `''` | 1 | 0 | 1 == 0+1 → unclosed |

これにより container-prefix の有無に関わらず container 階層は markdown-it-py 側で正しく解析され、本実装は「parser の出した結果を整合性チェック」する責務に集中する。

#### ヘルパー設計（round 6 最終形態）

```python
from markdown_it import MarkdownIt
from markdown_it.token import Token

_MD_PARSER = MarkdownIt("commonmark", {"html": False})

def _fence_has_explicit_closing(tok: "Token") -> bool:
    """Determine whether a markdown-it-py `fence` token has an explicit
    closing fence in the source.

    Rationale: markdown-it-py performs full CommonMark parsing including
    container-aware closing fence detection (§4.5 indent rules within
    list / block quote contexts). Re-deriving "is this a closing fence?"
    from the raw last line of `tok.map` either over-accepts (top-level
    4-sp pseudo-close treated as close, breaking MF-1) or under-accepts
    (legitimate container-nested close rejected). Instead, leverage the
    invariant that markdown-it-py's `tok.content` contains content lines
    only and is `\\n`-terminated:

      span_lines == content_lines + 2  → closed (open + content + close)
      span_lines == content_lines + 1  → unclosed (open + content only)

    For defensive fallthrough (unexpected span/content mismatch — should
    not happen with markdown-it-py >=3.0 commonmark mode), treat as
    unclosed so the link checker keeps scanning (soundness > spec
    purity).
    """
    if tok.map is None:
        return False
    span_lines = tok.map[1] - tok.map[0]
    content_lines = tok.content.count("\n")
    return span_lines == content_lines + 2


def _collect_fenced_block_line_ranges(content: str) -> list[tuple[int, int]]:
    """Return list of (start_line, end_line) (both 0-indexed, end exclusive)
    for fenced code blocks that have an explicit closing fence.

    Unclosed fenced blocks (no closing fence before EOF / containing block end)
    are EXCLUDED — their content is left visible to the link checker so that
    real broken links after an accidentally-unclosed fence are not silently
    swallowed (link checker soundness).

    Indented code blocks (CommonMark §4.4, token type "code_block") are also
    excluded per Issue #190 scope-out.
    """
    tokens = _MD_PARSER.parse(content)
    ranges: list[tuple[int, int]] = []
    for tok in tokens:
        # markdown-it-py token types:
        #   - "fence"        : fenced code block (§4.5)
        #   - "code_block"   : indented code block (§4.4)  ← scope-out
        # Only "fence" is considered for masking.
        if tok.type != "fence" or tok.map is None:
            continue
        if not _fence_has_explicit_closing(tok):
            continue  # unclosed fence → skip masking (soundness guard)
        start, end = tok.map  # [start, end) 0-indexed line range
        ranges.append((start, end))
    return ranges


def _strip_code_segments(content: str) -> str:
    """Blank out fenced code blocks and inline code spans for link extraction.

    Returns a string of the same length as ``content`` where characters inside
    masked regions are replaced with spaces (newlines preserved). Indented
    code blocks (§4.4) and unclosed fenced blocks are NOT masked, by design.
    """
    lines = content.split("\n")
    ranges = _collect_fenced_block_line_ranges(content)
    mask_line = [False] * len(lines)
    for start, end in ranges:
        for i in range(start, min(end, len(lines))):
            mask_line[i] = True
    out_lines = [
        " " * len(lines[i]) if mask_line[i] else lines[i]
        for i in range(len(lines))
    ]
    masked = "\n".join(out_lines)
    # Inline code spans (CommonMark §6.1) handled by content-wide regex
    # (multi-line spans supported). Unchanged from round 1/2.
    return _strip_inline_code_spans(masked)
```

#### 動作例

**ケース 1: top-level fenced block (`.claude/skills/review-poll/SKILL.md:82` 由来の OB)**

```
```bash
OWNER=$(echo "$ORIGIN" | sed -E 's#.*[:/]([^/]+)/[^/]+(\.git)?$#\1#')
```
```
→ markdown-it-py が `fence` token を出力、`map=[0, 3)`、`markup="```"`。末尾行 `` ``` `` は closing fence なので mask 対象。内部の `[^/]+` は除外される。

**ケース 2: reviewer round 2 probe (`- \`\`\`bash` の list item 直下 fence)**

```
- ```bash
  [fake](missing.md)
  ```
```
→ markdown-it-py が list_item の content に `fence` token を出力、`map=[0, 3)`。末尾 `  ` ``` `` が closing fence と認識される（markdown-it 側で container indent 処理済み）。`[fake](missing.md)` は除外。

**ケース 3: §5.2 Example 263 (content indentation で開始する fence)**

```
1.  text

    ```
    [fake](missing.md)
    ```
```
→ markdown-it-py が ordered list_item の content に `fence` token を出力、`map=[2, 5)`。`[fake](missing.md)` は除外。

**ケース 4: block quote 内 fence**

```
> ```text
> [fake](missing.md)
> ```
```
→ markdown-it-py が blockquote の content に `fence` token を出力（`map=[0,3)`, `markup="```"`, `tok.content='[fake](missing.md)\n'`）。`span_lines=3, content_lines=1` → `3 == 1+2` → **closed** 判定 → mask 適用。`[fake](missing.md)` は除外。

**ケース 4b: 多層ネスト block quote 内 fence**

```
> > ```text
> > [fake](missing.md)
> > ```
```
→ markdown-it-py が多層 blockquote の content に `fence` token を出力（`map=[0,3)`, `tok.content='[fake](missing.md)\n'`）。`span_lines=3, content_lines=1` → `3 == 1+2` → **closed** 判定 → mask 適用。

**ケース 4c: list item content indent + block quote の複合 container** (reviewer round 4 probe)

```
1.  > ```text
    > [fake](missing.md)
    > ```
```
→ markdown-it-py が ordered list item の content (indent 4) 内の block quote に fenced block を認識し、`fence` token を `map=[0,3)`、`tok.content='[fake](missing.md)\n'` で出力。`span_lines=3, content_lines=1` → `3 == 1+2` → **closed** 判定 → mask 適用。`[fake](missing.md)` は除外。

> **設計の不変条件 (round 6)**: container 階層（list / blockquote / 多層ネスト / 複合）の closing fence 解析は **markdown-it-py が CommonMark 準拠で完全に処理済み** であり、本実装は `tok.map` と `tok.content` の整合性比較のみで「parser が closed と扱った fence は実装も closed と扱う」契約を維持する。raw 行ベースの `[ \t>]*` プレフィックス吸収パターン (`_is_explicit_closing_fence`) は不要となり廃止する（round 5 までの実装からの主要変更点）。

**ケース 4d: round 6 MF-1 probe — top-level 4-sp pseudo-close**

```
```bash
[real-broken](missing.md)
    ```
```
→ markdown-it-py は CommonMark §4.5「top-level closing fence は 0–3 sp までしか indent できない」に従い、`    \`\`\`` を closing fence と認識しない。`fence` token は `map=[0,3)`、`tok.content='[real-broken](missing.md)\n    \`\`\`\n'` を返す（pseudo-close 行を内部 content として保持）。`span_lines=3, content_lines=2` → `3 == 2+1` → **unclosed** 判定 → mask せず。`[real-broken](missing.md)` は link 検査対象として残り、broken link が報告される。round 5 までの実装ではここで false-positive close が起き silent に隠れていたため、本ケースが round 6 設計の存在理由となる。

**ケース 5: 未閉鎖 fence (soundness guard)**

```
```bash
something incomplete

[real-broken-link](missing.md)
```
（ファイル末尾、closing fence なし）
→ markdown-it-py は `fence` token を出力するが、`tok.content` は EOF までの全行を含む。`span_lines == content_lines + 1` で **unclosed** 判定 → mask しない。`[real-broken-link](missing.md)` は link 検査対象として残り、broken link が報告される（false-negative 回避）。

### 3. `validate_all()` の改修

`scripts/check_doc_links.py:85-101` の `validate_all()` で、`LINK_PATTERN.finditer(content)` の前に `stripped = _strip_code_segments(content)` を実行し、stripped 側で `finditer` する。line 番号計算用の `lines` は元の `content.split("\n")` のままにする（stripped と元 content は文字数・改行位置が一致するため `_index_to_line()` は同じ結果を返す）。

### 4. 位置保持の不変条件（実装契約）

`_strip_code_segments` は以下を満たすことが、`_index_to_line()` 互換性の前提:

- `len(_strip_code_segments(c)) == len(c)`
- すべての `i` で `c[i] == "\n"` ⇔ `_strip_code_segments(c)[i] == "\n"`（改行位置完全一致）

この 2 条件は Small テストで明示的に検証する。markdown-it-py の `map` は line 単位の範囲を返すため、行単位で `" " * len(line)` 置換しても改行位置・総文字数は保たれる。

### 5. 既存挙動の不変性確保

- 既存テスト（`tests/test_check_doc_links.py` の現行 76+ ケース）が全て green のままであること
- 特に `test_image_links_skipped` / `test_self_anchor` / `test_link_to_nonexistent_file` 等の link 検出の中核挙動が回帰しないこと
- round 1 / round 2 で追加した Small / Medium テストも全て green を維持

### 6. Inline code span の検出（CommonMark § 6.1 準拠 / 複数行対応）

`_strip_inline_code_spans` は round 1/2 から継続利用する（markdown-it-py に置換せず regex のまま）。理由: 複数行 code span を含む既存仕様は round 1/2 の Small テストで検証済みで、parser 置換のメリット薄。

CommonMark 0.31.2 § 6.1 の以下規定に厳密に従う:

- code span は **同じ長さ N の backtick string** で開閉する
- **line ending は内部に許容される**（"Line endings are treated like spaces."）→ **行単位処理ではなく content 全体に対する処理が必須**
- 開閉のため、Pass 2 は Pass 1 でマスク済みの全 content（複数行を含む単一文字列）に対し regex を適用する

擬似コード（content 全体走査による複数行対応）:

```python
# N 個 backtick 開 → 同 N 個 backtick 閉 まで（内部に \n を許容、ただし short run は許容）
# CommonMark の "backtick string" は前後が backtick でない連続列。lookaround で境界を確保。
CODE_SPAN_PATTERN = re.compile(
    r"(?<!`)(`+)(?!`)"            # opening run (length N), not preceded/followed by `
    r"(?:(?!\1)[^`]|`+(?!\1))*?"  # content: non-backtick or backtick-run of length != N
    r"(?<!`)\1(?!`)",             # closing run of same length N
    re.DOTALL,                    # `.` を使わないが、内部の改行を許容するために DOTALL
)

def _strip_inline_code_spans(text: str) -> str:
    def _blank(m: re.Match[str]) -> str:
        # 改行は保持し、それ以外を空白化（line 番号互換性のため）
        return "".join(ch if ch == "\n" else " " for ch in m.group(0))
    return CODE_SPAN_PATTERN.sub(_blank, text)
```

> `_blank` は改行を `\n` のまま残し、それ以外を空白化することで、複数行 code span を空白化しても `_index_to_line()` が元 content と同じ line 番号を返すよう位置を保つ。

## テスト戦略

### 変更タイプ

実行時コード変更（`scripts/check_doc_links.py` のロジック変更）。`docs/dev/testing-convention.md` § 実行時の振る舞いを変える変更 に従い Small / Medium / Large の各観点を定義する。

### Small テスト（`tests/test_check_doc_links.py` のヘルパーレベル）

新規ヘルパー `_strip_code_segments` のロジックを単体で検証する。`_load_module()` 経由で import し、文字列 in / 文字列 out で振る舞いを assert する。

#### Fenced code block

- **fenced code block 内の `[text](target)` 風文字列が空白化される**: 入力 `` "```\n[link](b.md)\n```\n" `` → 出力で `[link](b.md)` 部分が空白化されることを確認
- **fence 開閉の文字種一致**: ` ``` ` で開いた block は ` ~~~ ` では閉じない（CommonMark 準拠）
- **fence 長さの一致**: 4 個 backtick で開いた block は 3 個 backtick では閉じない、5 個以上では閉じる
- **closing fence は info string を持てない**: 開行 ` ```bash ` の後、内部行 ` ``` aaa ` は closing fence と扱われず内部行のまま（前回 Must Fix 2 対応の回帰テスト）。次の正しい ` ``` ` のみが close する
- **closing fence は spaces/tabs のみ後続可**: ` ```   ` (trailing spaces) は close、` ```\t ` も close、`` ```x `` は close しない
- **インデント済みコードブロックは対象外**: 4 スペースインデント行内の `[text](target)` 風文字列は **空白化されず** 抽出対象として残る（スコープ外を確認する negative test）
- **list-item-nested fenced block の認識** (reviewer round 2 probe): 入力 `` "- ```bash\n  [fake](missing.md)\n  ```\n" `` → 全 3 行が空白化される（`[fake](missing.md)` 部分が link 抽出対象から除外される）
- **list-item-nested fenced block: ordered list marker でも動作**: 入力 `` "1. ```text\n   [fake](missing.md)\n   ```\n" `` でも同様に空白化
- **list-item-nested fenced block: 異なる marker (`*`, `+`) でも動作**: `* ` / `+ ` プレフィックスでも同じく opening fence と認識
- **CommonMark §5.2 Example 263: content indentation で開始する fence** (今回の MF-1 対応): 入力 `` "1.  text\n\n    ```\n    [fake](missing.md)\n    ```\n" `` → fence は markdown-it-py によって list_item の content として認識され、内部 `[fake](missing.md)` が空白化される
- **block quote 内 fenced block** (今回の MF-2 + MF-3 対応): 入力 `` "> ```text\n> [fake](missing.md)\n> ```\n" `` → markdown-it-py が blockquote の content として認識、内部 `[fake](missing.md)` が空白化される
- **多層ネスト container 内 fenced block**: 入力 `` "- outer\n  - inner\n\n    ```\n    [fake](missing.md)\n    ```\n" `` → 内部の `[fake](missing.md)` が空白化される
- **未閉鎖 fence の soundness guard** (今回の Point 2 対応): 入力 `` "```bash\n\n[real-broken](missing.md)\n" `` (EOF まで closing fence なし) → markdown-it-py は fence token を出力するが、`_fence_has_explicit_closing` が `span_lines == content_lines + 1` (open + content のみ) として **unclosed** と判定し mask 対象から除外する。`[real-broken](missing.md)` は **空白化されない**（link 抽出対象として残る）。これは link checker の false-negative 回避の核心契約。
- **closing fence が EOF と一致する場合は閉鎖と認める**: 入力 `` "```bash\nx\n```" `` (末尾改行なし) → markdown-it-py が closing fence を認識し `span_lines == content_lines + 2` で **closed** 判定、内部 mask される
- **round 6 MF-1 probe: top-level 4-sp pseudo-close** (今回の MF-1 対応 / 最重要回帰テスト): 入力 `` "```bash\n[real-broken](missing.md)\n    ```\n" `` (top-level で 4 sp 字下げされた pseudo-close を含む) → markdown-it-py は CommonMark §4.5 準拠で「unclosed」と扱い `tok.content` に `    \`\`\`\n` を含む。`span_lines=3, content_lines=2` → `3 == 2+1` → **unclosed** 判定 → mask 対象外。`[real-broken](missing.md)` は link 抽出対象として残る (`_strip_code_segments` 出力に `[real-broken](missing.md)` がそのまま残ることを assert)。round 5 までの実装ではこの入力で false-positive close 判定が起きていたため、本テストが round 6 設計の核心契約を担保する
- **`_fence_has_explicit_closing` 単体 True 系**: markdown-it-py の `Token` を直接組み立てる、または `_MD_PARSER.parse(source)` で得た `fence` token に対し、以下のいずれの container 文脈でも `_fence_has_explicit_closing(tok) == True` を返すこと（container 階層判定は markdown-it-py に委ねるため、本テストは「parser が closed と扱った fence は実装も closed と扱う」整合性を担保する）
  - top-level: `` "```\nx\n```\n" ``
  - top-level (no trailing newline): `` "```\nx\n```" ``
  - top-level closed empty: `` "```\n```\n" ``
  - block quote 1 段: `"> ```\n> x\n> ```\n"`
  - block quote 多層: `"> > ```\n> > x\n> > ```\n"`
  - list item content indent (Example 263): `"1.  text\n\n    ```\n    x\n    ```\n"`
  - 複合 container (list + block quote): `"1.  > ```\n    > x\n    > ```\n"`
- **`_fence_has_explicit_closing` 単体 False 系（unclosed）**: 以下が False (= unclosed) を返すこと
  - top-level opening only: `` "```\n" `` → `span=1, content=0` → 1 == 0+1 → False
  - top-level + content (no close): `` "```\nx\ny\n" `` → unclosed
  - **round 5 → 6 切替えで挙動が変わる top-level 4-sp pseudo-close**: `` "```bash\n[real-broken](missing.md)\n    ```\n" `` → round 6 では False（unclosed）。round 5 の `_is_explicit_closing_fence` 単体 True とは挙動が異なるため、回帰防止の起点として明示的に assert する
  - block quote opening only: `"> ```\n> x\n"` → unclosed
  - `tok.map is None` の防御パス: defensive fallback として False
- **scope-out 確認: インデント済みコードブロック (§4.4)**: 4 スペースインデント行内の `[text](target)` 風文字列は **空白化されず** 抽出対象として残る（Issue 本文 § スコープ外 の確認 negative test）

> **round 5 → round 6 のテスト整理**: round 5 までは `_is_explicit_closing_fence(line, fence_char, fence_len)` の line 単体 True/False を直接テストしていた。round 6 では line ベース判定を廃止し、`Token` 単位の content/span 比較に変えるため、Small テストの一部（line 単体 True/False 系）は **`_fence_has_explicit_closing(tok)` の Token in / bool out テスト** に書き換える。Issue を跨いだ既存契約（container 文脈での closing 認識）の意図は変わらないので、各 container ケースの input source は round 5 までの想定と同等のものを採用し、`_MD_PARSER.parse()` 経由で得た fence token を assert 対象とする。

#### Inline code span

- **インラインコード内の `[text](target)` 風文字列が空白化される**: 入力 `"text \`[link](b.md)\` text"` → 出力で `[link](b.md)` 部分が空白化される
- **複数行 code span 内の擬似 link が空白化される** (Must Fix 3 対応): 入力 `` "see `[link]\n(b.md)` here" `` → 内部の `[link]\n(b.md)` 部分が空白化される（改行は `\n` のまま保持）
- **同長 backtick run でのみ閉じる**: ` ``code with ` single`` ` のように内部に短い run を含む二重 backtick span を正しく検出
- **不揃いな run は code span にならない**: `` `abc`` `` のような不一致は span として消費されず、`[...](...)` がそのまま残る
- **通常段落の link は残る**: 入力 `"see [link](b.md) here"` → 出力でも `[link](b.md)` が残る

#### 位置保持の不変条件（実装契約の明示検証）

- **出力長が入力長と完全一致**: 任意の入力 `c` に対し `len(_strip_code_segments(c)) == len(c)`
- **改行位置が完全一致**: 任意の入力 `c` に対し、すべての `i` で `c[i] == "\n"` ⇔ `out[i] == "\n"`（複数行 code span を含むケースで特に重要）

bug 規定（`design-by-type/bug.md` § 8）の **再現テスト** および Red 証跡の取得経路（前回 MF-2 対応）:

- **修正前 Red の取得経路**: Medium 層の subprocess テスト（特に「review-poll/SKILL.md パターンの回帰テスト」および「list-item-nested fenced block の subprocess 回帰テスト」）で、**CLI 経由の `broken link: ...` 出力 + `returncode=1`** を Red 証跡として取得すること。Small テストの `AttributeError: module has no attribute '_strip_code_segments'` のような import / collection error は、OB の `broken link` 偽陽性出力を再現していないため Red 証跡として **使用不可**。実装着手時は (a) 新規 Medium テストを追加して修正前 commit で Red を確認 → (b) 実装 → (c) 同じ Medium テストで Green を確認、の順序を踏むこと
- **修正後 Green の取得経路**: 同じ Medium テスト群が全て exit 0 / `All Markdown links valid` を返すこと、加えて Large テスト `test_repo_verify_docs_args_have_no_broken_links` が exit 0 を返すこと
- 上記 Red→Green ログは `/issue-implement` 完了報告に貼付し、`/issue-review-code` が独立検証で再現できる形式（テスト名 + 出力抜粋 + returncode）で記録する

### Medium テスト（`tests/test_check_doc_links.py` の subprocess レベル）

`_run(tmp_path, ...)` 経由で CLI 全体の振る舞いを E2E に検証する。

- **fenced code block 内の正規表現は誤検出されない**: `.md` ファイルに ` ```bash\n... [^/]+ ... \n``` ` を書き、 `_run` で exit 0 / `All Markdown links valid` を確認
- **fenced code block 内の擬似 link `[link](missing.md)` は誤検出されない**: 同様に exit 0 を確認
- **インラインコード内の擬似 link `` `[link](missing.md)` `` は誤検出されない**: exit 0 を確認
- **複数行 code span 内の擬似 link は誤検出されない** (Must Fix 3 対応): `` `[link]\n(missing.md)` `` を含む `.md` で exit 0
- **closing fence の info string 偽陽性回避** (前回 Must Fix 2 対応): ` ```bash ` で開いた block の内部行に ` ``` aaa ` がある場合、これを close と扱わず block 継続。block 内の `[link](missing.md)` が誤検出されないことを exit 0 で確認
- **fenced code block 外の broken link は引き続き検出される**: 同一ファイル内で code block 外に `[link](missing.md)` がある場合 exit 1 / stderr に `missing.md` を含む
- **fenced code block と通常段落の混在**: code block 内 fake link + code block 外 valid link → exit 0
- **review-poll/SKILL.md パターンの回帰テスト**: 実際の sed 正規表現 `s#.*[:/]([^/]+)/[^/]+(\.git)?$#\1#` を fenced bash block に含む `.md` を生成 → exit 0
- **list-item-nested fenced block の subprocess 回帰テスト** (reviewer round 2 probe / Red 証跡用): `` "- ```bash\n  [fake](missing.md)\n  ```\n" `` を含む `.md` を `_run` 経由で検査 → 修正前は `returncode=1` + stderr に `broken link: missing.md` を含む（**OB 同等の偽陽性を再現する Red**）、修正後は `returncode=0` + stdout に `All Markdown links valid` を含む Green
- **CommonMark §5.2 Example 263 の subprocess 回帰テスト** (今回の MF-1 対応 / Red 証跡用): `` "1.  text\n\n    ```\n    [fake](missing.md)\n    ```\n" `` を含む `.md` を `_run` 経由で検査 → 修正前は `returncode=1` + stderr に `broken link: missing.md`、修正後は `returncode=0`
- **block quote 内 fenced block の subprocess 回帰テスト** (round 3 MF-2 + MF-3 対応): `` "> ```text\n> [fake](missing.md)\n> ```\n" `` を含む `.md` を `_run` 経由で検査 → 修正前は `returncode=1`、修正後は `returncode=0`
- **list item content indent + block quote の複合 container** (round 5 reviewer probe 対応): `` "1.  > ```text\n    > [fake](missing.md)\n    > ```\n" `` を含む `.md` を `_run` 経由で検査 → 修正前は `returncode=1`、修正後は `returncode=0`。round 6 では line ベースの `_is_explicit_closing_fence` 判定を廃止したため、本ケースの単体検証は **Small テストの `_fence_has_explicit_closing` 複合 container True 系** に移行し、subprocess 側は CLI 全体の振る舞いのみを assert する
- **round 6 MF-1 probe: top-level 4-sp pseudo-close の subprocess 回帰テスト** (今回の MF-1 対応 / 最重要): `` "```bash\n[real-broken](missing.md)\n    ```\n" `` (top-level fence + 4 sp 字下げ pseudo-close) を含む `.md` を `_run` 経由で検査 → **修正後も `returncode=1` を返し、`broken link: missing.md` を stderr に出力する**。round 5 までの実装ではこの入力で `[real-broken](missing.md)` が silent に隠れて exit 0 になるため、本ケースが round 5 と round 6 を分ける subprocess Red 証跡となる
- **未閉鎖 fence の soundness 回帰テスト** (今回の Point 2 対応 / 最重要): `` "Intro.\n\n```bash\nsome code\n\n[real-broken](missing.md)\n" `` (closing fence なし) を含む `.md` を `_run` 経由で検査 → **修正後も `returncode=1` を返し、`broken link: missing.md` を stderr に出力する**（false-negative を防ぐ safety guard の動作確認）
- **未閉鎖 fence + 既存通常段落 broken link の coexistence**: 同一ファイルに「未閉鎖 fence の前にある通常段落 broken link」と「未閉鎖 fence 後の段落 broken link」を持つ → 両方が報告される (`returncode=1`)
- **list-item-nested + 同一ファイル内の正当な link**: list-item-nested fenced block 内に fake link を含み、同ファイルの段落部分には実在 link を持つケース → exit 0（fenced 内は除外、段落内は検証通過）
- **多層ネスト container 内 fenced block の subprocess 回帰テスト**: `- outer\n  - inner\n\n    \`\`\`\n    [fake](missing.md)\n    \`\`\`` → `returncode=0`

### Large テスト（`make verify-docs` と一致する全引数 E2E）

既存 `TestRealRepo`（`tests/test_check_doc_links.py:334-352`）は引数なしで `docs/` を、`README.md` 単独引数で `README.md` を検証するのみで、**`Makefile:32-33` の `verify-docs` ターゲットが対象とする `.claude/skills/` を含まない**。本 Issue の偽陽性源 `.claude/skills/review-poll/SKILL.md:82` を実 repo 状態で検証するには、`make verify-docs` と同一引数での Large テストが必須。

新規 Large テスト（`tests/test_check_doc_links.py:TestRealRepo` に追加）:

```python
def test_repo_verify_docs_args_have_no_broken_links(self) -> None:
    """E2E: check_doc_links.py with the same arguments as `make verify-docs`.

    Covers Issue #190: ensure .claude/skills/ paths (e.g. review-poll/SKILL.md
    fenced code block regex) do not produce false positives.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "docs/", "README.md", "CLAUDE.md", ".claude/skills/"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, f"Broken links:\n{result.stderr}"
```

引数列は `Makefile:33` のレシピと完全一致させる。既存 `test_repo_docs_have_no_broken_links` / `test_repo_readme_has_no_broken_links` は重複しないため残置する（より狭い範囲の回帰検出として有用）。

### 受け入れ判定

- 完了条件 1（`fix/190` HEAD 上で `make verify-docs` が exit 0）: 新規 Large テスト `test_repo_verify_docs_args_have_no_broken_links` の通過と等価
- 完了条件 2（fenced + inline code 内除外）: Small + Medium テストで検証
- 完了条件 3（回帰テスト追加）: 上記 Small / Medium の新規テスト（Must Fix 2 / Must Fix 3 対応の回帰テストを含む）
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
| `scripts/check_doc_links.py` の docstring | あり（軽微） | 関数 docstring に CommonMark parser ベースの fenced 除外仕様および未閉鎖 fence safety guard を追記する程度。新規 reference doc は不要 |
| `pyproject.toml` | あり | `[dependency-groups].dev` に `markdown-it-py>=3.0` を追加。`[project].dependencies` (runtime) には追加しない |
| `uv.lock` | あり（自動更新） | `uv sync` 実行により markdown-it-py + 推移依存（`mdurl` 等）が lockfile に追加される |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `scripts/check_doc_links.py` (現行実装) | `scripts/check_doc_links.py:22-23, 85-101, 205-211` | `LINK_PATTERN` は code 文脈無視で content 全体に `finditer`。line 番号は `_index_to_line` が `match.start()` から計算するため、code 除外で文字数を変えないことが互換性要件 |
| `tests/test_check_doc_links.py` (既存テスト) | `tests/test_check_doc_links.py:50-323` | 既存テスト群の振る舞い（image link skip / external skip / anchor 検証等）が回帰しないことを担保する基準 |
| `.claude/skills/review-poll/SKILL.md` (偽陽性発生源) | `.claude/skills/review-poll/SKILL.md:79-84` | 現実の偽陽性パターン。fenced ` ```bash ` 内の `sed -E 's#.*[:/]([^/]+)/[^/]+(\.git)?$#\1#'` が誤検出される |
| CommonMark 仕様 — Fenced code blocks | https://spec.commonmark.org/0.31.2/#fenced-code-blocks | "A fenced code block ... Tildes and backticks cannot be mixed. ... A closing code fence ... whose opening fence was 3 backticks may not be closed by 4 backticks, but a closing fence with 4 backticks may close a 3-backtick opening." 本設計の fence 開閉ルール（同一文字種・close は open 以上の長さ）の根拠 |
| CommonMark 仕様 — Code spans | https://spec.commonmark.org/0.31.2/#code-spans | "A backtick string is a string of one or more backtick characters that is neither preceded nor followed by a backtick. A code span begins with a backtick string and ends with a backtick string of equal length." inline code span の長さ一致開閉ルールの根拠 |
| CommonMark 仕様 — List items | https://spec.commonmark.org/0.31.2/#list-items | § 5.2 + Example 263: list item の content indentation は marker + space の幅で定まり、その内部に fenced code block を含む場合は通常の fenced block と同様に振る舞う。本設計が list_item / content indentation fence を含む全 container ケースを契約に含める根拠 |
| CommonMark 仕様 — Block quotes | https://spec.commonmark.org/0.31.2/#block-quotes | § 5.1: block quote (`>` プレフィックス) は container block であり、その content 内の fenced code block も §4.5 ルールに従う。本設計が block quote 内 fence を mask 対象に含める根拠 |
| markdown-it-py (CommonMark parser) | https://github.com/executablebooks/markdown-it-py / https://markdown-it-py.readthedocs.io/ | "A Python port of markdown-it ... 100% CommonMark spec coverage." token-level の `map` (line range) 情報を持ち、container 内 fenced block の line range を CommonMark 準拠で取得可能。本設計が regex から markdown-it-py への切り替えを採用する根拠 |
| markdown-it-py token API | https://markdown-it-py.readthedocs.io/en/latest/architecture.html#tokens | Token.type (`fence` / `code_block` / `inline` / ...) / Token.map (`[start_line, end_line)` 0-indexed) / Token.markup (opening fence string). 本設計の `_collect_fenced_block_line_ranges()` 実装根拠 |
| 偽陽性源の実在確認 (block quote 内 fenced block) | `.claude/skills/i-pr/SKILL.md:225-239` | block quote 内 fenced block の実在例。本設計では markdown-it-py で正しく mask 対象として扱う |
| 偽陽性源の実在確認 (content indentation fence in list) | `.claude/skills/{review,pr-verify,pr-fix,i-pr}/SKILL.md` | ordered list item の content indentation 内に置かれた `     ```text ...` 形式の fenced block が複数存在（内部に `[text](target)` 構造は無いため修正前でも偽陽性は発生していないが、CommonMark 上は fenced block と認識される） |
| `docs/dev/testing-convention.md` § 実行時の振る舞いを変える変更 | `docs/dev/testing-convention.md:63-66` | 「設計書のテスト戦略には Small / Medium / Large の各観点を定義する」本設計のテスト戦略構成の根拠 |
| `c2d4a66` (LINK_PATTERN 導入 commit) | `c2d4a66 docs: add docs-maintenance workflow and i-doc-* skills (#111)` | `scripts/check_doc_links.py` 初導入時点から code 文脈除外ロジックは未実装。`git log --oneline -S "LINK_PATTERN" -- scripts/check_doc_links.py` で確認 |
