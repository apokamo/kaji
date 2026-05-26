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

- **Fenced code blocks** (CommonMark 0.31.2 § 4.5): 行頭 0–3 スペース + 3 個以上連続する `` ` `` または `~`（同一文字）で開閉し、内部の inline 構文（link / emphasis / 等）はパースされない。**list item (CommonMark 0.31.2 § 5.2) 内に置かれた fenced code block** も同様に内部内容は link parse 対象外となる。本 Issue ではこのケース（list item 直下に置かれた fenced block）を契約に含める。
- **Code spans** (CommonMark 0.31.2 § 6.1): backtick string で囲まれた範囲は literal text として扱われ、link 構文はマッチしない。

ただし本 Issue では以下のケースを **scope-out** とする:

- **インデント済みコードブロック**（4 スペース / タブインデントによる code block）。理由: 本リポジトリの docs / skills は fenced code block を一貫使用しており、現実の偽陽性発生源は fenced / inline に限定される。
- **block quote (CommonMark § 5.1) 内に置かれた fenced code block**。実在例: `.claude/skills/i-pr/SKILL.md:225-239`（`> ```text` で始まる ABORT verdict サンプル）が 1 箇所存在するが、内部内容に `[text](target)` 構造を含まないため現実の偽陽性源ではない。実在例の有無は `rg -n '^[ \t]{0,3}>[ \t]*\`{3,}' docs/ .claude/skills/ README.md CLAUDE.md` で確認可能。block quote 内に link 風文字列を含む fenced block を追加した場合は別 Issue として扱う。
- **多層ネスト container 内 fenced code block**（例: list item の中の list item の中の fenced block、`^[ \t]{2,}[-*+][ \t]+\`{3,}` 相当）。実在例なし（`rg -n '^[ \t]{2,}[-*+][ \t]+\`{3,}' docs/ .claude/skills/ README.md CLAUDE.md` で 0 件）。実装の複雑性に対し検証可能な現実 case が無いため scope-out。
- **list item 内の fenced block で開いた後、closing fence が無いまま list item 境界（ブランク行 + dedent）で暗黙的に閉じるケース**。CommonMark 仕様では list item が終わるとその内部の fenced block も暗黙的に終了するが、本設計では「closing fence の明示出現」だけを fence 終了条件とする（後述「fence 終了の単純化規則」参照）。本リポジトリ内に該当する暗黙終了パターンの実在例は無いため、簡素化を優先する。

実装は上記 scope-out ケースを「fenced code block ではない」と分類するか、最初の opening fence を検出した後に**通常の closing fence 出現まで継続**する単純規則で扱う。これは現実の偽陽性が発生しない（実在例なし）ため安全。

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
  - block quote 内 fenced block / 多層ネスト container 内 fenced block の除外（理由は EB § scope-out 参照。本リポジトリ内に偽陽性となる実在例が無い）
  - list item 境界（ブランク行 + dedent）による fenced block の暗黙終了処理。明示的な closing fence のみで fence を閉じる単純規則を採用（理由は EB § scope-out 参照）
  - CommonMark の info string や code block の特殊エッジケース（言語 hint・空行内包等）の構文解析。fenced 開閉判定のみに限定
  - `Makefile` の `verify-docs` ターゲット定義（修正不要）

## 方針（修正アプローチ）

### 1. code 文脈除外ヘルパー `_strip_code_segments(content: str) -> str` を追加

- 入力: Markdown content（生テキスト）
- 出力: fenced code block 内およびインラインコード内の文字を空白 `' '` で置換した同じ長さの文字列（改行は保持）
- 同じ長さを維持することで、`_index_to_line()` による既存の line 番号計算ロジック（`scripts/check_doc_links.py:205-211`）に影響を与えない（`finditer` の match position と content 内の文字位置の対応が保たれる）

#### Fenced code block の検出（CommonMark § 4.5 / § 5.2 準拠）

行単位走査で fenced code block 領域を特定する。CommonMark 0.31.2 § 4.5 の以下規定に従い、加えて § 5.2 (List items) で規定される **list item 直下に置かれた fenced block** を契約に含める:

- **top-level opening fence**: 行頭 0–3 スペース + 3 個以上連続する同一の `` ` `` または `~`。同行の残部は info string として無視
- **list-item-nested opening fence**: 行頭が list marker prefix（`^ {0,3}(?:[-*+]|\d{1,9}[.)])[ \t]+`）であり、その後ろが 0–3 スペース + 3 個以上連続する同一の `` ` `` または `~` の場合、これも opening fence と扱う。container_prefix_len = list marker prefix の文字長 を記録
- **closing fence**: opening と **同じ文字種** かつ **opening と同じ以上の長さ** で、fence 文字以降は **spaces / tabs のみ**（info string を持たない）。閉じる際の leading whitespace は 0–3 スペース、または list-item-nested fence の場合は container_prefix_len 以上の leading whitespace（空白のみ）から始まることを許容する
- info string に backtick を含むことは禁止（opening 行末の残部に `` ` `` が含まれる場合は info string として無効 → そもそも opening fence ではない）

> CommonMark 0.31.2 § 4.5: "The closing code fence must be the same type as the opening (backticks or tildes), and must have at least as many backticks or tildes as the opening fence. ... The closing code fence may be indented up to three spaces, and may be followed only by spaces, which are ignored."

> CommonMark 0.31.2 § 5.2 (要旨): list item の content は marker + space の文字幅でインデントされ、その内部に置かれた fenced code block は通常の fenced code block と同様に振る舞う。

**fence 終了の単純化規則**: 本実装では list item 境界（ブランク行 + dedent）による fenced block の暗黙終了は扱わず、明示的な closing fence の出現のみで fence を閉じる。理由は EB § scope-out 参照。CommonMark 厳密には list item を抜けた行で fenced block も暗黙終了するが、本リポジトリ内に該当パターンの実在例が無いため簡素化を優先する。

擬似コード:

```python
# Top-level: 行頭 0-3 sp + `{3,} or ~{3,}
_FENCE_OPEN_BT = re.compile(r"^ {0,3}(`{3,})([^`]*)$")
_FENCE_OPEN_TILDE = re.compile(r"^ {0,3}(~{3,})(.*)$")
# List-item-nested: list marker prefix + 0-3 sp + `{3,} or ~{3,}
# List marker は unordered (-, *, +) または ordered (1-9 digits + . または ))
_LIST_MARKER_PREFIX = re.compile(r"^( {0,3}(?:[-*+]|\d{1,9}[.)])[ \t]+)")

def _try_open_fence(line: str) -> tuple[str, int, int] | None:
    """Return (fence_char, fence_len, container_prefix_len) if `line` opens a fence."""
    # Top-level
    m = _FENCE_OPEN_BT.match(line) or _FENCE_OPEN_TILDE.match(line)
    if m:
        return m.group(1)[0], len(m.group(1)), 0
    # List-item-nested: marker prefix + fence
    lm = _LIST_MARKER_PREFIX.match(line)
    if lm:
        prefix_len = len(lm.group(1))
        rest = line[prefix_len:]
        m = _FENCE_OPEN_BT.match(rest) or _FENCE_OPEN_TILDE.match(rest)
        if m:
            return m.group(1)[0], len(m.group(1)), prefix_len
    return None

def _is_closing_fence(
    line: str, fence_char: str, fence_len: int, container_prefix_len: int
) -> bool:
    """closing fence は以下のいずれか:
    - top-level: leading 0-3 sp + same char + same length+ + spaces/tabs only
    - container-nested: leading が **正確に** container_prefix_len spaces で
      始まり、その後 0-3 sp + same char + same length+ + spaces/tabs only

    container_prefix_len + 0-3 を超える leading indent は indented code 扱い
    となり close しない（CommonMark §4.5 + §5.2 準拠）。
    """
    # Top-level close: 0-3 sp + fence
    if re.match(
        rf"^ {{0,3}}({re.escape(fence_char)}{{{fence_len},}})[ \t]*$", line
    ):
        return True
    # Container-nested close: 正確に container_prefix_len spaces + 0-3 sp + fence
    if container_prefix_len > 0:
        pattern = (
            rf"^ {{{container_prefix_len}}} {{0,3}}"
            rf"({re.escape(fence_char)}{{{fence_len},}})[ \t]*$"
        )
        if re.match(pattern, line):
            return True
    return False

def _strip_code_segments(content: str) -> str:
    lines = content.split("\n")
    # Pass 1: fenced code block 領域をマスク（位置保持）
    out_lines: list[str] = []
    fence_char: str | None = None
    fence_len = 0
    container_prefix_len = 0
    for line in lines:
        if fence_char is None:
            opened = _try_open_fence(line)
            if opened is not None:
                fence_char, fence_len, container_prefix_len = opened
                out_lines.append(" " * len(line))  # opening fence 行も中和
            else:
                out_lines.append(line)  # fenced 外: そのまま（inline code は Pass 2 で）
        else:
            out_lines.append(" " * len(line))  # 内部行は全空白化
            if _is_closing_fence(line, fence_char, fence_len, container_prefix_len):
                fence_char = None
                fence_len = 0
                container_prefix_len = 0
    # Pass 2: fenced を除外した content 全体に対し inline code span を空白化
    masked = "\n".join(out_lines)
    return _strip_inline_code_spans(masked)
```

**動作例（reviewer の追加プローブ）**:

入力:
```
- ```bash
  [fake](missing.md)
  ```
```

処理:
1. line 1 `- \`\`\`bash`: `_try_open_fence` が list-item-nested として match → `(fence_char='`', fence_len=3, container_prefix_len=2)`、line 1 を空白化
2. line 2 `  [fake](missing.md)`: in-fence → 空白化
3. line 3 `  \`\`\``: `_is_closing_fence` が container-nested close として match（leading 2 spaces ≥ container_prefix_len=2 + 0-3 sp + 3 backticks）→ fence 終了

結果: line 2 の `[fake](missing.md)` は `LINK_PATTERN.finditer` の対象から除外され、誤検出されない。

#### Inline code span の検出（CommonMark § 6.1 準拠 / 複数行対応）

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

### 2. `validate_all()` の改修

`scripts/check_doc_links.py:85-101` の `validate_all()` で、`LINK_PATTERN.finditer(content)` の前に `stripped = _strip_code_segments(content)` を実行し、stripped 側で `finditer` する。line 番号計算用の `lines` は元の `content.split("\n")` のままにする（stripped と元 content は文字数・改行位置が一致するため `_index_to_line()` は同じ結果を返す）。

### 位置保持の不変条件（実装契約）

`_strip_code_segments` は以下を満たすことが、`_index_to_line()` 互換性の前提:

- `len(_strip_code_segments(c)) == len(c)`
- すべての `i` で `c[i] == "\n"` ⇔ `_strip_code_segments(c)[i] == "\n"`（改行位置完全一致）

この 2 条件は Small テストで明示的に検証する。

### 3. 既存挙動の不変性確保

- 既存テスト（`tests/test_check_doc_links.py` の現行 30+ ケース）が全て green のままであること
- 特に `test_image_links_skipped` / `test_self_anchor` / `test_link_to_nonexistent_file` 等の link 検出の中核挙動が回帰しないこと

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
- **list-item-nested fenced block の認識** (今回の MF-1 対応): 入力 `` "- ```bash\n  [fake](missing.md)\n  ```\n" `` → 全 3 行が空白化される（`[fake](missing.md)` 部分が link 抽出対象から除外される）
- **list-item-nested fenced block: ordered list marker でも動作**: 入力 `` "1. ```text\n   [fake](missing.md)\n   ```\n" `` でも同様に空白化
- **list-item-nested fenced block: 異なる marker (`*`, `+`) でも動作**: `* ` / `+ ` プレフィックスでも同じく opening fence と認識
- **list-item-nested closing fence の indent 範囲**: 開行 `- ```bash` (container_prefix_len=2) の後、leading 空白が **正確に 2 spaces + 0–3 sp** の範囲（2〜5 spaces）の closing fence 行は close。例: `  ` ``` `` (2 sp) は close、`   ` ``` `` (3 sp) も close、`     ` ``` `` (5 sp) も close、`      ` ``` `` (6 sp) は **close しない**（indented code 範囲）。また ` ``` ` (top-level 0 sp) も close する（CommonMark の top-level close 互換性）
- **list-item-nested の scope-out 確認** (block quote): 入力 `` "> ```text\n> [fake](missing.md)\n> ```\n" `` は **block quote 内 fenced block として認識せず**、`[fake](missing.md)` が抽出対象に残る（scope-out 動作の negative test）

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
- **list-item-nested fenced block の subprocess 回帰テスト** (今回の MF-1 対応 / Red 証跡用): `` "- ```bash\n  [fake](missing.md)\n  ```\n" `` を含む `.md` を `_run` 経由で検査 → 修正前は `returncode=1` + stderr に `broken link: missing.md` を含む（**OB 同等の偽陽性を再現する Red**）、修正後は `returncode=0` + stdout に `All Markdown links valid` を含む Green
- **list-item-nested + 同一ファイル内の正当な link**: list-item-nested fenced block 内に fake link を含み、同ファイルの段落部分には実在 link を持つケース → exit 0（fenced 内は除外、段落内は検証通過）

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
| `scripts/check_doc_links.py` の docstring | あり（軽微） | 関数 docstring に fenced / inline code 除外仕様を追記する程度。新規 reference doc は不要 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `scripts/check_doc_links.py` (現行実装) | `scripts/check_doc_links.py:22-23, 85-101, 205-211` | `LINK_PATTERN` は code 文脈無視で content 全体に `finditer`。line 番号は `_index_to_line` が `match.start()` から計算するため、code 除外で文字数を変えないことが互換性要件 |
| `tests/test_check_doc_links.py` (既存テスト) | `tests/test_check_doc_links.py:50-323` | 既存テスト群の振る舞い（image link skip / external skip / anchor 検証等）が回帰しないことを担保する基準 |
| `.claude/skills/review-poll/SKILL.md` (偽陽性発生源) | `.claude/skills/review-poll/SKILL.md:79-84` | 現実の偽陽性パターン。fenced ` ```bash ` 内の `sed -E 's#.*[:/]([^/]+)/[^/]+(\.git)?$#\1#'` が誤検出される |
| CommonMark 仕様 — Fenced code blocks | https://spec.commonmark.org/0.31.2/#fenced-code-blocks | "A fenced code block ... Tildes and backticks cannot be mixed. ... A closing code fence ... whose opening fence was 3 backticks may not be closed by 4 backticks, but a closing fence with 4 backticks may close a 3-backtick opening." 本設計の fence 開閉ルール（同一文字種・close は open 以上の長さ）の根拠 |
| CommonMark 仕様 — Code spans | https://spec.commonmark.org/0.31.2/#code-spans | "A backtick string is a string of one or more backtick characters that is neither preceded nor followed by a backtick. A code span begins with a backtick string and ends with a backtick string of equal length." inline code span の長さ一致開閉ルールの根拠 |
| CommonMark 仕様 — List items | https://spec.commonmark.org/0.31.2/#list-items | § 5.2: list item の content indentation は marker + space の幅で定まり、その内部に fenced code block を含む場合は通常の fenced block と同様に振る舞う。本設計が list-item-nested fenced block を契約に含める根拠 |
| 偽陽性源の実在確認 (block quote 内 fenced block) | `.claude/skills/i-pr/SKILL.md:225-239` (`rg -n '^[ \t]{0,3}>[ \t]*\`{3,}' ...` で確認) | block quote 内 fenced block の実在例は 1 箇所のみで、内部に link 風文字列を含まない。よって block quote scope-out は現実の偽陽性を発生させない |
| 偽陽性源の実在確認 (多層ネスト) | `rg -n '^[ \t]{2,}[-*+][ \t]+\`{3,}' docs/ .claude/skills/ README.md CLAUDE.md` で 0 件 | 多層ネスト container 内 fenced block の実在例なし。scope-out は安全 |
| `docs/dev/testing-convention.md` § 実行時の振る舞いを変える変更 | `docs/dev/testing-convention.md:63-66` | 「設計書のテスト戦略には Small / Medium / Large の各観点を定義する」本設計のテスト戦略構成の根拠 |
| `c2d4a66` (LINK_PATTERN 導入 commit) | `c2d4a66 docs: add docs-maintenance workflow and i-doc-* skills (#111)` | `scripts/check_doc_links.py` 初導入時点から code 文脈除外ロジックは未実装。`git log --oneline -S "LINK_PATTERN" -- scripts/check_doc_links.py` で確認 |
