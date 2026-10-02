# [設計] `[paths] design_dir` で設計書 directory を設定可能にし、`design_path` の固定 draft 規約を解消する

Issue: #397

## 概要

`.kaji/config.toml` の `[paths] design_dir`（repository 相対 directory）で設計書の置き場所を設定可能にし、
GitHub / Local 両 provider が `IssueContext.design_path = <design_dir>/issue-<id>-<slug>.md` を同一規約で注入する。
未設定時は legacy default `draft/design` を維持する。

## 背景・目的

### 現状

- `kaji_harness/providers/context.py::build_design_path()` は provider に関係なく
  `f"draft/design/issue-{issue_id}-{slug}.md"` を固定生成する（同ファイル L97-103）。
- 呼び出し元は `GitHubProvider.resolve_issue_context()`（`providers/github.py:510`）と
  `LocalProvider.resolve_issue_context()`（`providers/local.py:292`）の 2 箇所のみで、どちらも引数に
  config 由来の値を渡していない。
- `kaji_harness/config.py::PathsConfig`（L44-49）は `artifacts_dir` / `skill_dir` / `worktree_prefix` のみ。
- `prompt.py:56` は `issue_context.design_path` をそのまま `design_path` コンテキスト変数として注入する。

### ユースケース

> **利用側リポジトリの maintainer** として、設計書を Git に commit して後続 AI が過去の実装判断を検索・参照できる
> 恒久履歴（例: `designs/issues/`）として運用するために、`.kaji/config.toml` に
> `[paths] design_dir = "designs/issues"` と書くだけで、Kaji が注入する `design_path` がその directory を指すようにしたい。

一次情報: [apokamo/fullstack-agent-template#42](https://github.com/apokamo/fullstack-agent-template/issues/42) の
「実装設計」節は正本を `designs/issues/issue-42-kaji-skill-worktree-bootstrap.md` とし、Kaji 0.18.0 の固定注入値を
repository 側 adapter（legacy path resolver）で変換して運用している。設定可能化により adapter を除去できる。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| 利用側で adapter（legacy path resolver）を維持する | 現状そのもの。注入値と正本 path が乖離し、skill ごとに変換を書く必要がある（Issue 目的に反する） |
| default を `designs/issues` に変更する | Issue「範囲外」に「default 値の切替（将来の別 Issue で判断）」と明記。既存利用者の in-flight 設計書を壊す |
| `design_path` の template 文字列（`{dir}/issue-{id}-{slug}.md` 全体）を設定可能にする | Issue 提案 2 は「設定済み directory、Issue ID、sanitized slug から path を組み立てる」と directory のみを可変点に指定。ファイル名規約まで開放すると validation 面が増える |
| `config.local.toml` overlay で `design_dir` を上書き可能にする | 既存 `[paths]` は overlay 対象外（`docs/reference/configuration.md` § Overlay merge rule）。設計書の置き場所は repository 単位の規約であり、user ごとに変わると履歴が分散する |

## インターフェース

### 入力

#### 設定キー `[paths] design_dir`

| 項目 | 内容 |
|------|------|
| 位置 | tracked `.kaji/config.toml` の `[paths]` テーブル（overlay `config.local.toml` の `[paths]` は従来どおり無視） |
| 型 | `str` |
| 必須 | 任意 |
| loader default | `""`（未設定） |
| 実効 fallback | `"draft/design"`（legacy default。`build_design_path` が `design_dir or LEGACY_DESIGN_DIR` で解決） |
| 形式 | repository root からの相対 POSIX path。`/` 区切りの 1 個以上の segment |

**validation（違反は `ConfigLoadError`、config 読込時に fail-fast）**:

| # | 規則 | 拒否例 | 理由 |
|---|------|--------|------|
| V1 | 文字列であること | `design_dir = 1` | 型契約 |
| V2 | 絶対 path でないこと（先頭 `/`、Windows drive / UNC を含む） | `/srv/designs`, `C:/designs`, `//host/share` | repository 外 escape |
| V3 | 各 segment が `[A-Za-z0-9._][A-Za-z0-9._-]*` に一致 | `~/designs`, `my designs`, `a\b`, `-x/designs`, `designs/$(id)` | `design_path` は skill 内の shell command（`git add [design_path]` 等）にそのまま展開されるため、空白・shell metachar・option 化（先頭 `-`）・`~` 展開を構文的に排除する |
| V4 | 空 segment を含まないこと | `designs//issues`, `designs/issues/`, `/designs` | 正規形を 1 つに固定し、`design_path` 比較を文字列一致で行えるようにする |
| V5 | `.` / `..` segment を含まないこと | `../designs`, `designs/../..`, `./designs`, `.` | traversal と非正規形の排除。repository root 自体（`.`）も設計書 directory として認めない |
| V6 | `.git` segment を含まないこと（大文字小文字無視） | `.git/designs`, `x/.GIT` | git 管理領域への書込み防止 |
| V7 | `design_dir` の既存 prefix を symlink 解決した実体が `repo_root.resolve()` 配下であり、かつ解決自体が成功すること | repo 内 symlink `designs -> /tmp/outside` を経由する `designs/issues`、symlink loop `designs -> designs` | lexical 検査を通過する symlink 経由の repository 外 escape、および解決不能な path |

**入力境界（Pydantic）**: AGENTS.md「外部入力は Pydantic で検証する」と同ファイルの既存パターン
（`_IncidentSection` → `IncidentConfig` の詰め替え、`ValidationError` → `ConfigLoadError` 変換。`config.py` L117-166 / L269-292）に合わせ、
`design_dir` の生値は strict な Pydantic 入力モデル `_DesignDirInput`
（`model_config = ConfigDict(extra="forbid", frozen=True, strict=True)`、field `design_dir: str = ""`）で検証する。

- `[paths]` に `design_dir` キーが無い → モデルを通さず `""`（未設定 = legacy fallback）
- `design_dir = ""`（明示空文字）→ 未設定と同じ扱い（`""`、legacy fallback）。`worktree_prefix` の空文字 = 未設定と同じ規則
- `design_dir = 1` / `true` / `["a"]` / `{ a = 1 }` → strict mode により型エラー（int / bool / list / table の暗黙変換をしない）
- 非空文字列 → `field_validator` が純粋関数 `validate_design_dir(value)`（V2〜V6、違反は `ValueError`）を呼ぶ
- `ValidationError` は `_parse_incident` と同形式で `ConfigLoadError(path, "paths.design_dir: <msg>")` に変換する
- 既存 `artifacts_dir` / `skill_dir` / `worktree_prefix` の手動検査は本 Issue では刷新しない（範囲外。`design_dir` の新規入力のみ Pydantic 境界に載せる）

**V7（filesystem 検査）** は filesystem を参照するため Pydantic モデルには入れず、loader（`KajiConfig._load`）で
Pydantic 検証の後に行う。Python のバージョン差（3.13 未満は `resolve(strict=False)` でも symlink loop で
`RuntimeError`、3.13 以降は loop を非存在扱いで黙って通す）に依存しないよう、次の決定的手順をとる:

1. `design_dir` の segment を先頭から順に `repo_root` に連結し、`os.path.lexists()` が偽になった最初の component で打ち切る
   （以降は未作成 directory。lexical 検査 V2〜V6 済みのため連結のみで安全）
2. 最後に存在した prefix を `Path.resolve(strict=True)` で解決する。`RuntimeError`（3.13 未満の loop）・`OSError`
   （3.13 以降の `ELOOP`、権限不足、壊れた symlink の `FileNotFoundError` を含む）は
   `ConfigLoadError(path, "paths.design_dir: cannot resolve '<prefix>': <exc>")` に変換する
3. 解決結果が `repo_root.resolve()` の配下（`Path.is_relative_to`）でなければ
   `ConfigLoadError(path, "paths.design_dir must stay inside the repository (resolves to <resolved>)")`
4. prefix が 1 つも存在しない（未作成 directory）場合は V7 を通過する

#### `build_design_path()` の引数

```python
def build_design_path(issue_id: str, slug: str, design_dir: str = "") -> str: ...
```

| 引数 | 型 | 内容 |
|------|----|------|
| `issue_id` | `str` | 正規化済み Issue ID（`"42"` / `"local-pc1-3"`） |
| `slug` | `str` | sanitized slug。`validate_slug()` で**全体一致**検証（違反は `ValueError`。§ slug 検証の厳密化） |

#### slug 検証の厳密化

現行 `validate_slug` は `_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")` を `.match()` で評価しており、
`$` が末尾改行の直前にも一致するため `validate_slug("example\n")` が成功する（review-design で Python 3.12.3 実測）。
このまま `build_design_path` に流すと改行入りの `design_path` が生成され、「不正 slug を validation error にする」要件を満たさない。

- `validate_slug` を `_SLUG_RE.fullmatch(slug)`（正規表現から `^` / `$` を除いた `[a-z0-9][a-z0-9-]{0,39}`）に変更し、
  文字列全体の一致を要求する。末尾 LF・CRLF・その他の制御文字・空白はすべて拒否される
- `validate_slug` は Local provider の frontmatter 検証（`providers/_local_common.py:117`、`providers/local.py:136`）とも共有する。
  変更は拒否側への厳密化のみで、従来も意図上不正だった値（末尾改行付き slug）を拒否するだけなので、
  正規の slug（`derive_slug_from_title` の出力・`kaji issue create` が生成する slug）の挙動は変わらない
- 同じ理由で `validate_design_dir` の segment 判定も `fullmatch` を用いる（疑似コードどおり）
| `design_dir` | `str` | `[paths].design_dir`。`""` は legacy default `draft/design` に解決。非空なら `validate_design_dir()` で再検証 |

### 出力

| 出力 | 内容 |
|------|------|
| `build_design_path()` 戻り値 | `f"{design_dir or 'draft/design'}/issue-{issue_id}-{slug}.md"`（repository 相対 POSIX path 文字列） |
| `IssueContext.design_path` | 上記。GitHub / Local provider とも `get_provider(config)` で渡された同一 `design_dir` を使う |
| prompt コンテキスト変数 `design_path` | `prompt.py` 経由で上記値がそのまま注入される（`prompt.py` 変更なし） |
| `kaji issue context <id>` JSON の `design_path` | 同上（既存 CLI が `IssueContext` を出力するため自動追随） |
| `kaji config design-dir`（新規 read-only subcommand） | 実効 design dir を stdout に 1 行（`draft/design\n` / `designs/issues\n`）。exit 0。config 不在・不正は stderr 診断 + exit 2（`kaji config artifacts-dir` と同一契約） |

### エラー

| 状況 | 挙動 |
|------|------|
| `design_dir` が V1〜V7 に違反（型違反・symlink loop 等の解決失敗を含む） | `KajiConfig._load` が `ConfigLoadError` を送出（`ValidationError` / `RuntimeError` / `OSError` は送出前に変換し、生の例外を漏らさない）。`kaji run` / `kaji issue` / `kaji config *` は既存経路どおり exit 2 + stderr にキー名と違反値を表示 |
| `build_design_path` に不正 slug（例: `"Bad_Slug"`, `"../x"`, `""`, `"example\n"`, `"example\r\n"`） | `ValueError`（`validate_slug` のメッセージ）。GitHub は title から導出した slug、Local は frontmatter strict 検証済み slug を渡すため通常経路では到達しない（defense in depth） |
| `build_design_path` に不正 `design_dir`（provider を直接構築した場合） | `ValueError`（`validate_design_dir` のメッセージ） |

### 使用例

```toml
# .kaji/config.toml（利用側リポジトリ）
[paths]
artifacts_dir = ".kaji-artifacts"
skill_dir = ".claude/skills"
design_dir = "designs/issues"   # 未設定なら legacy default "draft/design"
```

```python
config = KajiConfig.discover(start_dir=repo)
provider = get_provider(config)           # design_dir=config.paths.design_dir を受け取る
ctx = provider.resolve_issue_context("42")  # title "Example"
assert ctx.design_path == "designs/issues/issue-42-example.md"

build_design_path("42", "example")                     # "draft/design/issue-42-example.md"（legacy）
build_design_path("42", "example", "designs/issues")   # "designs/issues/issue-42-example.md"
build_design_path("42", "Bad_Slug", "designs/issues")  # ValueError
```

```console
$ kaji config design-dir
designs/issues
```

## 制約・前提条件

- **後方互換**: `design_dir` 未設定の repository（kaji 自身を含む）では `design_path` の生成値・
  `kaji issue context` 出力・prompt 注入値が現行と完全一致すること。
- **provider 一致**: `design_dir` は `get_provider()` が `config.paths.design_dir` を両 provider に渡す 1 経路のみで供給する。
  provider 構築箇所は `get_provider()` のみ（`grep "GitHubProvider(\|LocalProvider(" kaji_harness` で確認済み）。
- **overlay 非対象**: `[paths]` は tracked `.kaji/config.toml` のみから構築する既存規則を維持する。
- **依存方向**: `validate_design_dir` と `LEGACY_DESIGN_DIR` は `kaji_harness/config.py` に置き、
  `providers/context.py` はそれを import する（`config.py` は `errors` 以外を import しないため循環しない。
  逆方向の `config → providers` は `providers/__init__` が github/local 等を読み込むため避ける）。
- **Kaji は設計書ファイル自体を作成・移動しない**: Kaji core の責務は path 値の算出・検証・注入まで。
  設計書の作成・commit は skill 側の責務（既存どおり）。
- **issue_id の path 安全性**は既存の上流検証（GitHub は `gh` JSON の数値 ID、Local は `validate_issue_meta`
  の `expected_id` 照合）に依存し、本 Issue では追加しない。
- **local provider は非推奨化済み（#438）だが現行実装が存在する**ため、GitHub と同等に対応・検証する。
- Python 規約: `docs/reference/python/` に従う（型ヒント必須、`from __future__ import annotations`、Google style docstring）。

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/config.py` | `LEGACY_DESIGN_DIR` 定数、`validate_design_dir()`、`PathsConfig.design_dir: str = ""`、入力モデル `_DesignDirInput`（strict Pydantic）、`_load` での V1〜V7 検証と例外変換 |
| `kaji_harness/providers/context.py`（slug） | `validate_slug` を `fullmatch` に変更（末尾改行等の拒否） |
| `kaji_harness/providers/context.py` | `build_design_path(issue_id, slug, design_dir="")`：legacy fallback・slug / design_dir 検証。module docstring / 関数 docstring の固定 path 表現を除去 |
| `kaji_harness/providers/github.py` / `local.py` | dataclass field `design_dir: str = ""` を追加し `build_design_path` に渡す。docstring に由来を記載 |
| `kaji_harness/providers/__init__.py` | `get_provider()` で両 provider に `design_dir=config.paths.design_dir` を渡す |
| `kaji_harness/providers/models.py` | `IssueContext.design_path` docstring を「`[paths].design_dir`（未設定時 legacy default `draft/design`）配下の `issue-<id>-<slug>.md`」に変更 |
| `kaji_harness/commands/config.py` / `commands/parser.py` / dispatch | `kaji config design-dir` read-only subcommand |
| docs（§ 影響ドキュメント） | configuration reference、CLI guide の config 例、skill authoring のコンテキスト変数表、AI docs management の設計書 lifecycle |
| `.kaji/config.toml` | `[paths]` にコメント行で `design_dir`（未設定 = legacy default）を明示 |
| tests | § テスト戦略 |

**範囲外（Issue 本文「範囲外」に準拠 + 設計で確認した境界）**:

- 設計全文の Issue 本文 mirror、`kaji issue edit --body-file` 契約の変更、既存 `draft/design/` 配下の移動、default 切替。
- **kaji 自身の `.claude/skills/` 内の `draft/design/` 記述の置換**: kaji repository は `design_dir` 未設定（legacy default）で
  運用を続けるため、現行 skill の記述は kaji 自身にとって正しい。Issue 提案 7 は「workflow/skill authoring **docs**」の
  固定 path 表現除去を対象としており、skill 本文の全面置換は含まない。starter の skill への反映は post-release の
  starter sync（`/update-starter`）の責務。
- 外部 starter repository（`kaji-starter-python` / `kaji-starter-typescript`）の `.kaji/config.toml` の直接編集：
  別 repository であり、Release 後の starter sync 手順（`docs/operations/release/starter-sync-runbook.md`）で追随する。
  本 workflow では kaji 側の config 例（configuration reference / CLI guides）に `designs/issues` の選択例を載せる。

## 方針

### データフロー

```
.kaji/config.toml [paths].design_dir
  └─ KajiConfig._load: _DesignDirInput (strict Pydantic; V1 + validate_design_dir V2-V6) → symlink 検査 (V7, 例外は ConfigLoadError 化) → PathsConfig.design_dir
       └─ get_provider(config): GitHubProvider(design_dir=...) / LocalProvider(design_dir=...)
            └─ resolve_issue_context(): build_design_path(id, slug, self.design_dir)
                 └─ IssueContext.design_path ──→ prompt.py の design_path / `kaji issue context` JSON
KajiConfig ──→ `kaji config design-dir`: config.paths.design_dir or LEGACY_DESIGN_DIR を出力
```

### 新規・変更シンボルの責務

| シンボル | 責務 |
|----------|------|
| `config.LEGACY_DESIGN_DIR = "draft/design"` | legacy default の単一情報源。`build_design_path` と `kaji config design-dir` が参照 |
| `config.validate_design_dir(value: str) -> None` | V1 以外の lexical 規則（V2〜V6）を判定する純粋関数。違反は `ValueError` |
| `_DesignDirInput`（Pydantic, strict） | `design_dir` 生値の入力境界。型検査（V1）と `validate_design_dir` 呼び出し（V2〜V6） |
| `KajiConfig._parse_design_dir(config_path, paths_data, repo_root) -> str` | キー有無・空文字の扱い、`_DesignDirInput` 検証、`ValidationError` → `ConfigLoadError` 変換、V7 検査（`RuntimeError` / `OSError` → `ConfigLoadError`） |
| `PathsConfig.design_dir` | loader が採用した生値（未設定 `""`）。`worktree_prefix` と同じ「config default と実効 fallback の分離」パターン |
| `build_design_path(issue_id, slug, design_dir="")` | slug 検証 → design_dir 検証 → fallback 解決 → 連結 |
| `cmd_config_design_dir(args)` | `kaji config artifacts-dir` と同一の `--workdir` / exit code 契約で実効値を出力 |

`build_worktree_dir` / `worktree_prefix` と同じ「loader は生値、builder が fallback」の既存パターンに合わせる
（`docs/reference/configuration.md` § `[paths]` の `worktree_prefix` 注記と同型で文書化できる）。

### 疑似コード

```python
_DESIGN_DIR_SEGMENT_RE = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._-]*")

def validate_design_dir(value: str) -> None:
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive:
        raise ValueError(...)
    for seg in value.split("/"):
        if seg == "": raise ValueError(...)            # V4
        if seg in {".", ".."}: raise ValueError(...)   # V5
        if seg.lower() == ".git": raise ValueError(...) # V6
        if not _DESIGN_DIR_SEGMENT_RE.fullmatch(seg): raise ValueError(...)  # V3

def build_design_path(issue_id: str, slug: str, design_dir: str = "") -> str:
    validate_slug(slug)          # fullmatch: "example\n" も拒否
    if design_dir:
        validate_design_dir(design_dir)
    return f"{design_dir or LEGACY_DESIGN_DIR}/issue-{issue_id}-{slug}.md"
```

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 設定キーの位置と名前 | `[paths] design_dir`（repository 相対 directory） | Issue 本文「提案」1・「概要」（人間決定） | 型 `str`、tracked config のみ（overlay 非対象）、正規形を POSIX `/` 区切りに固定 |
| 未設定時の挙動 | legacy default `draft/design` を維持し、legacy であることを文書化 | Issue 本文「提案」4・「範囲外」（default 切替は別 Issue）（人間決定） | loader default `""` + builder fallback（`worktree_prefix` と同型）。定数 `LEGACY_DESIGN_DIR` を単一情報源化 |
| path の組み立て規則 | `<design_dir>/issue-<id>-<slug>.md`（directory のみ可変） | Issue 本文「提案」2（人間決定） | ファイル名部分は現行規約を据え置き |
| 拒否対象 | 絶対 path / `..` / repository 外 escape / 不正 slug | Issue 本文「提案」3・「完了条件」4（人間決定） | V1〜V7 に分解。V3（segment 文字種）・V4（空 segment）・V6（`.git`）・V7（symlink escape）は「repository 外 escape 拒否」と shell 展開安全性の実装上の詳細化。**V3/V4/V6 の厳しさは AI の仮定**（根拠: `design_path` は skill 内 shell command に未 quote で展開されうる。緩和は後方互換に追加できる two-way door）。検査先: review-design / review-code |
| `design_dir` の入力検証境界 | strict Pydantic 入力モデル `_DesignDirInput` で検証し `ConfigLoadError` に変換 | `AGENTS.md`「外部入力は Pydantic で検証する」（人間が定めた repository 規約）+ 既存 `_IncidentSection` パターン（既存契約） | 新規キーのみ対象（既存 `[paths]` キーの刷新は範囲外）。キー無し / 空文字は legacy fallback、非 str は strict で拒否 |
| 不正 slug の判定方法 | `validate_slug` を `fullmatch` に厳密化（末尾改行・制御文字を拒否） | Issue 本文「完了条件」4「不正 slug が validation error になる」（人間決定） | 共有先（Local frontmatter 検証）への影響は拒否側の厳密化のみ。review-design の実測（`"example\n"` が通る）に基づく |
| V7 の解決失敗時の挙動 | symlink loop・壊れた symlink・権限不足を `ConfigLoadError`（CLI では exit 2）に収束 | Issue 本文「提案」3「repository 外 escape を拒否」（人間決定）+ 既存 CLI の exit 2 契約 | Python バージョン差を避けるため既存 prefix に `resolve(strict=True)` を使う決定的手順。**手順の具体形は AI の詳細化**。検査先: review-code |
| 両 provider への供給経路 | `get_provider()` から同一値を両 provider に渡す | Issue 本文「提案」6・「完了条件」3（人間決定） | dataclass field `design_dir` を追加。provider 構築箇所が `get_provider()` のみであることを grep で確認 |
| config inspection の具体形 | 既存 `kaji config`（parser の help: "Read-only config inspection commands"）に `design-dir` subcommand を追加し、`kaji issue context` の `design_path` も自動追随 | Issue 本文「スコープ/範囲内」の「config inspection … の更新」と「完了条件」5（人間決定: config inspection に反映する）。**具体形（新 subcommand を足すこと）は AI の仮定**（根拠: 既存 `provider-type` / `artifacts-dir` と同型の read-only 追加で、release 前なら削除・改名が安価な two-way door）。検査先: review-design |
| starter / config example の扱い | kaji 側 config 例（configuration reference・CLI guides）に `designs/issues` の選択例をコメントで載せる。外部 starter repository は Release 後の starter sync で追随 | Issue 本文「提案」5・「範囲内」（人間決定: example で選択可能にする）。**外部 repository を本 workflow で直接変更しない判断は AI の仮定**（根拠: starter は別 repository で、`/update-starter` → `/release-starter` の管理手順が存在する）。検査先: review-design / i-dev-final-check |
| docs の固定 path 表現除去範囲 | `IssueContext.design_path` docstring、`context.py` docstring、skill authoring docs のコンテキスト変数表、AI docs management の lifecycle 表 | Issue 本文「提案」7・「範囲内」（人間決定） | kaji 自身の `.claude/skills/` 本文は legacy default 運用のため対象外と明記（Issue 提案 7 は docs が対象）。**この境界は AI の解釈**。検査先: review-design |
| Issue 本文 mirror / `issue edit` 契約 / 既存設計書移動 | 実施しない | Issue 本文「範囲外」（人間決定） | — |

one-way door の未決: なし。公開 config キー名・default・拒否対象はすべて Issue 本文で人間が決定済み。
新 CLI subcommand は read-only の追加で、release 前に review で安価に撤回できる。source of truth（Issue 本文）間の矛盾なし。

## テスト戦略

### 変更タイプ

実行時コード変更（config 読込・path 生成・provider context・CLI subcommand の追加）。

### Small テスト（`tests/test_providers_context.py` / `tests/test_config.py` 等）

- `validate_design_dir`: 許容例（`designs/issues`, `draft/design`, `docs/design`, `.kaji/designs`, `a_b/c-d.e`）が例外なし。
  拒否例を V2〜V6 の規則ごとに最低 1 件ずつ（`/abs`, `C:/x`, `//host/share`, `~/x`, `a b`, `a\b`, `-x`, `a//b`, `a/`,
  `.`, `./a`, `../a`, `a/../b`, `.git`, `a/.GIT`）が `ValueError` かつメッセージに違反値を含む。
- `build_design_path`: `design_dir` 省略 / `""` で legacy（`draft/design/issue-153-auth.md`。既存テスト維持）、
  `"designs/issues"` で `designs/issues/issue-42-example.md`、不正 slug（`"Bad_Slug"`, `"../x"`, `""`）と
  不正 `design_dir` で `ValueError`。
- `validate_slug` の全体一致: `"example\n"`, `"example\r\n"`, `"exa\nmple"`, `"example "`, `"ex\tample"` が `ValueError`、
  正規 slug（`"example"`, `"a"`, 40 文字境界）は従来どおり通過、41 文字は拒否。
- `_DesignDirInput`: strict で int / bool / list を拒否、`""` を受理、非空の不正値で `validate_design_dir` 由来のエラー。
- `PathsConfig()` の `design_dir` default が `""`。
- `prompt.build_prompt` が `IssueContext.design_path` を加工せず注入すること（既存テストで担保済みのため、
  `designs/issues/...` 値での 1 ケースのみ追加）。

### Medium テスト（tmp_path 上の config / git fixture）

- **config 読込**: `design_dir = "designs/issues"` → `paths.design_dir == "designs/issues"`；未設定 → `""`；
  非文字列・V2〜V6 違反 → `ConfigLoadError`（メッセージに `paths.design_dir`）；overlay の `[paths] design_dir` は無視される。
- **入力境界（Pydantic）**: `design_dir` に int / bool / 配列 / inline table → `ConfigLoadError`（メッセージに `paths.design_dir`）。
  キー無しと `design_dir = ""` はどちらも `paths.design_dir == ""` で legacy fallback になる。
  `design_dir = "designs/issues\n"`（TOML の `"\n"` エスケープ）→ `ConfigLoadError`。
- **symlink loop（V7 解決失敗）**: tmp repo に `designs -> designs` を作り `design_dir = "designs/issues"` で
  `ConfigLoadError`（生の `RuntimeError` / `OSError` が漏れないこと。Python 3.11〜3.13+ のどれでも同結果になることを
  決定的手順の `strict=True` で保証）。壊れた symlink（`designs -> missing`）も同様に `ConfigLoadError`。
  同 config で `kaji config design-dir` の CLI handler が exit 2 + stderr に `paths.design_dir` を出す。
- **symlink escape（V7）**: tmp repo 内に `designs -> <tmp外 directory>` を作り `design_dir = "designs/issues"` で
  `ConfigLoadError`。repo 内を指す symlink は許容。
- **adapter 不要の end-to-end（完了条件 1・6）**: `[paths] design_dir = "designs/issues"` を持つ tmp_path の
  `.kaji/config.toml`（`provider.type = "github"`）から `KajiConfig.discover` → `get_provider` →
  `resolve_issue_context("42")` → `build_prompt` を通し、`design_path` が `designs/issues/issue-42-example.md`、
  prompt 内に `draft/design` が現れないことを確認。`gh` 境界は `GitHubProvider.view_issue` の stub（worktree 解決に
  届かない経路のため `testing-convention.md` § patch スコープの許可範囲）で title `"Example"` を返す。
- **provider 一致（完了条件 3）**: 同一 `design_dir` の config で `provider.type = "local"`（実 git repo fixture、
  `resolve_main_worktree` は実 git で解決）から local Issue を作成し `resolve_issue_context` →
  `design_path == build_design_path(id, slug, "designs/issues")`。GitHub 側と `PurePosixPath(design_path).parent` が
  一致することを assert。
- **後方互換（完了条件 2）**: `design_dir` 未設定 config で両 provider の `design_path` が `draft/design/issue-<id>-<slug>.md`
  （既存 `test_providers_github.py:159` / `test_providers_local.py:449` を維持し、`get_provider` 経由の 1 ケースを追加）。
- **`kaji config design-dir`**: `_run_cli` 相当で未設定 → `draft/design\n`、設定済み → `designs/issues\n`、
  config 不在・不正 `design_dir` → exit 2 + stderr。

### Large テスト（`large_local`、ネットワークなし subprocess）

- 実 subprocess の `kaji config design-dir --workdir <tmp repo>` が設定値を出力し、不正値（`../x`、symlink loop `designs -> designs`）で
  exit 2 + traceback なしの stderr 診断になること
  （CLI 登録・dispatch・exit code の結線確認）。
- local provider の tmp repo で `kaji issue create` → `kaji issue context <id>` を実 subprocess で実行し、
  JSON の `design_path` が `designs/issues/issue-<id>-<slug>.md` であること（config → provider → CLI 出力の E2E）。
- **実 GitHub API（`large_forge`）は追加しない**: 本変更は `gh` 呼び出しの引数・出力解析を変えず、`design_path` は
  `gh` 応答（title）取得後の純粋計算でのみ決まる。`gh` 境界は既存 `large_forge` テストで担保済みで、
  GitHub 経路の path 計算は Medium の stub E2E で config 読込から prompt 生成まで通して検証する
  （`testing-convention.md` § 正当化できる理由「既存ゲートで不具合パターンを捕捉できる」）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新ライブラリ・技術選定なし。既存 `[paths]` パターンの拡張 |
| docs/ARCHITECTURE.md | なし | モジュール構成・責務分担は不変（`design_path` への言及なし） |
| `docs/reference/configuration.md` / `.ja.md` | **あり** | `[paths]` 表に `design_dir` 行（型・default・validation・source）、「config default と実効 fallback（legacy `draft/design`）」注記、**security boundary**（V1〜V7）、**migration 方法**（設定追加 → 既存設計書の `git mv` 判断 → in-flight Issue の扱い → legacy adapter 除去 → skill が `[design_path]` / `kaji config design-dir` を参照すること）、Overlay merge rule の `[paths]` キー列挙、Example にコメント例 `# design_dir = "designs/issues"` |
| `docs/cli-guides/github-mode.md` / `.ja.md`, `local-mode.md` / `.ja.md` | **あり** | config 例の `[paths]` に `# design_dir = "designs/issues"` 選択例を追加（詳細は configuration reference へ誘導） |
| `docs/dev/skill-authoring.md` | **あり** | 「ハーネスが注入するコンテキスト変数」表に `design_path`（`[paths].design_dir` 配下、未設定時 legacy `draft/design`）を追加し、skill は固定 path ではなく `[design_path]` を参照すべきことを記載 |
| `docs/dev/workflow-authoring.md` | なし | `design_path` / `draft/design` の固定 path 表現を含まない（grep で確認） |
| `docs/concepts/ai-docs-management.md` / `.ja.md` | **あり** | 設計書 lifecycle 表の「In progress」location を `<design_dir>/issue-XXX-*.md`（default `draft/design`）に変更 |
| docs/cli-guides/（`kaji config` の CLI 説明） | なし | `kaji config` subcommand 一覧を持つ guide は存在しない（grep 確認: `local-mode.md` / `local-mode-runbook.md` は `provider-type` の使用例のみ）。`kaji config design-dir` は configuration reference の `design_dir` 節に記載する |
| `docs/dev/workflow_completion_criteria.md` / `shared_skill_rules.md` | なし | kaji 自身の運用記述（legacy default 運用）であり、固定値として正しい |
| `README.md` / `README.ja.md` | なし | 最小 config 例。キー詳細は configuration reference に集約する方針（同 reference § Overview） |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| `CHANGELOG.md` | なし（本 workflow） | `[Unreleased]` は `/release` skill が生成する運用 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 現行の固定実装 | `kaji_harness/providers/context.py` L97-103 | `return f"draft/design/issue-{issue_id}-{slug}.md"` — provider に関係なく固定 |
| provider からの呼出し | `kaji_harness/providers/github.py:510`, `kaji_harness/providers/local.py:292` | 両方とも `build_design_path(issue_id, slug)` で config 値を渡していない |
| provider 構築箇所 | `kaji_harness/providers/__init__.py` L102-135 `get_provider()` | 両 provider へ `worktree_prefix=config.paths.worktree_prefix` を渡す既存パターン（`design_dir` も同経路で供給） |
| 設定定義 | `kaji_harness/config.py` L44-49 `PathsConfig`、L209-243 `_load`、L555-609 validators | `design_dir` 不在。`artifacts_dir` は `..` 拒否、`skill_dir` は absolute / `..` 拒否、`worktree_prefix` は safe segment 正規表現 — validation 実装パターンの根拠 |
| prompt 注入 | `kaji_harness/prompt.py:56` | `"design_path": issue_context.design_path` をそのまま注入 |
| config inspection の既存面 | `kaji_harness/commands/parser.py` L98-130、`kaji_harness/commands/config.py` `cmd_config_artifacts_dir` | `kaji config` は "Read-only config inspection commands"。`--workdir` + exit 0/2 契約 |
| config 仕様の正本 | `docs/reference/configuration.md` § `[paths]` / § Overlay merge rule | `[paths]` は overlay 非対象。`worktree_prefix` の「config default vs effective fallback」記述パターン |
| テスト規約 | `docs/dev/testing-convention.md` § テストサイズ定義 / § `subprocess.run` patch スコープ | GitHub passthrough の stub 許可条件、worktree 解決に届く経路は実 git fixture |
| 利用側ユースケース | https://github.com/apokamo/fullstack-agent-template/issues/42 | 「実装設計」節: 正本 `designs/issues/issue-42-kaji-skill-worktree-bootstrap.md`。Kaji 0.18.0 の注入値を repository adapter で変換している |
| Python `pathlib` | https://docs.python.org/3/library/pathlib.html#pathlib.Path.resolve | `PurePosixPath.is_absolute()` / `PureWindowsPath.drive` による OS 非依存の lexical 判定。`resolve()` は「If a path resolution loop is encountered, RuntimeError is raised」（3.13 で変更: strict=False では loop を非存在扱い、strict=True では OSError）→ V7 は既存 prefix に `strict=True` を使い両例外を変換 |
| Python `re` | https://docs.python.org/3/library/re.html#re.fullmatch | `$` は「the end of the string or just before the newline at the end of the string」に一致。`fullmatch` は文字列全体の一致を要求 → slug / segment 判定は `fullmatch` |
| Pydantic strict mode | https://docs.pydantic.dev/latest/concepts/strict_mode/ | strict では型の暗黙変換を行わない（int → str 等を拒否）。既存 `_IncidentSection` と同じ `ConfigDict(strict=True)` を採用 |
| 外部入力の検証規約 | `AGENTS.md` Always-Apply Rules | 「外部入力は Pydantic で検証する」 |
| starter sync 手順 | `docs/operations/release/starter-sync-runbook.md` | 外部 starter repository は Release 後に追随する管理手順 |
