---
id: local-pc5090-21
title: 'feat: comment ファイル名 timestamp 化 + machine_id pc5090 → p1 への統合移行'
state: closed
slug: comment-ts-and-machine-p1
labels:
- type:feature
created_at: '2026-05-10T07:09:36Z'
closed_at: '2026-05-10T11:03:29Z'
closed_by: p1
close_reason: completed
---
> [!NOTE]
> **Worktree**: `../kaji-feat-local-pc5090-21`
> **Branch**: `feat/local-pc5090-21`

## 設計書

<details>
<summary>クリックして展開</summary>

# [設計] comment ファイル名 timestamp 化 + machine_id pc5090 → p1 への統合移行

Issue: local-pc5090-21

## 概要

LocalProvider の comment ファイル命名規則を `<seq>-<machine_id>.md` から **compact ISO 8601 timestamp** ベース (`<YYYYMMDDTHHMMSSZ>-<machine_id>.md`) に切り替え、同一 PR 内で全 142 comment + 20 issue dir を `pc5090` → `p1` に rename する。worktree 間の seq 衝突を原理的に消滅させ、入力負荷を 4 文字短縮する。

## 背景・目的

### 課題 1: comment seq 衝突 (worktree 間 race)

`LocalProvider._next_comment_seq` は **当該 worktree 内の `comments/` の最大 seq + 1** で採番する（local.py:484-493）。別 worktree（main / feature）に存在する comment を見ないため、2 つの worktree が同時に同じ Issue へ comment すると、merge 時に `0001-pc5090.md` の add/add 衝突が発生する。

実例として 2026-05-10 の CronCreate one-shot 連続実行（local-pc5090-7/8/9/10）の close フェーズで再現済み（`local-pc5090-20` に記録、commit `877fa84` で手動 renumber して merge）。

### 課題 2: machine_id `pc5090` の入力負荷

issue ID (`local-pc5090-N`)、comment filename (`-pc5090.md`)、CLI 出力（`[author @ ts]`）、worktree path、branch name で 6 文字の machine_id を毎回読み書きしている。`p1` (2 文字) に短縮することで日常入力 / レビュー文字列を 4 文字 × 多箇所削減する。

### ユーザーストーリー

- **maintainer として**、別 worktree で並行投稿した comment が merge 時に seq 衝突を起こさない状態にしたい（Why: 現状は手動 renumber が発生する）。
- **maintainer として**、`local-p1-N` の短い ID で issue を参照したい（Why: cron batch / interactive 双方で入力コストが高い）。
- **maintainer として**、過去 commit history の `pc5090` references は時点記録として保持したい（Why: history rewrite はリスクと監査追跡コストに見合わない）。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| seq 採番だけ修正（machine_id は据置） | 入力負荷の課題が残る。同 PR 内で機械的 rewrite するのが最少手数 |
| timestamp + 別途 lock file | local mode は worktree 越境 lock を持たない設計。timestamp + 同秒 retry で十分 |
| 旧形式 fallback を恒久残置 | 移行後は不要。fallback は同 PR 内で削除し読み取りパスを単純化 |
| machine_id を `m1` 等中立名 | `p1` (= "pc 5090 1 号機") として運用上のセマンティクスを保つほうが障害分析時に有利 |

## インターフェース

### 入力

`LocalProvider` の public API（`comment_issue` / `view_issue` 等）の **シグネチャは無変更**。動作（書き込み・読み込み）の内部規約のみ変更する。

| 項目 | 旧 | 新 |
|------|-----|-----|
| comment filename | `<NNNN>-<machine>.md`（NNNN = 4桁 zero-pad seq） | `<YYYYMMDDTHHMMSSZ>-<machine>.md`（compact ISO 8601 UTC） |
| machine_id (`.kaji/config.local.toml`) | `pc5090` | `p1` |
| issue_dir 名 | `local-pc5090-N-<slug>/` (1〜20) | `local-p1-N-<slug>/` (1〜20)。**21 は据置** |
| issue.md frontmatter `id:` | `local-pc5090-N` | `local-p1-N`（21 を除く） |
| counter file | `.kaji/counters/pc5090.txt`（中身 `21`） | `.kaji/counters/p1.txt`（中身 `21`、旧 file 削除） |
| 21 配下の既存 comment | `0001-pc5090.md` / `0002-pc5090.md`（旧 seq 形式） | `<ts>-pc5090.md`（新形式 timestamp + machine 部分は `pc5090` を維持。**dir / branch path と同様に時点記録として machine_id 部分を保護**） |

#### timestamp_compact のフォーマット

```python
datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")   # 例: "20260510T142536Z"
```

- 秒精度。同秒 collision 時は `_atomic_write_new` の `FileExistsError` を retry 契機にし、2 回目以降の retry では `+1s` ずつ繰り上げて再生成する（実装詳細は § 方針）。
- ms / ns 精度化は本 issue では行わない（現状 0 件、過剰実装回避）。

### 出力

#### CLI 出力（`kaji issue comment`）

stdout に書き出される `<seq>-<machine>` 行（`cli_main.py:1302`）は形式が変わる:

```
# 旧
0042-pc5090

# 新
20260510T142536Z-p1
```

- `--commit` 経路（`cli_main.py:1305`）の commit 対象 path も同形式: `comments/20260510T142536Z-p1.md`
- 表示系（`[author @ created_at]` フォーマット、`createdAt` JSON キー）は **無変更**

#### Comment dataclass (`kaji_harness/providers/models.py:24-35`)

`seq: str` フィールドの取り扱いを 2 案で検討。**A 案（後方互換重視）を採用** する:

| 案 | 概要 | 採否 |
|----|------|------|
| **A: `seq` を保持し、新形式では timestamp 文字列を入れる** | `Comment(seq="20260510T142536Z", machine_id="p1")`。`cli_main.py:1302` / `1305` の format `f"{seq}-{machine}"` がそのまま動作 | ✅ **採用**（最小差分） |
| B: `seq` 廃止し新規フィールド `filename_stem` を追加 | より明確だが call site 全件改修が必要 | 不採用 |

A 案下では「`seq` フィールドの値の意味」が広がる（旧: `0042` / 新: `20260510T142536Z`）。docstring を更新して両形式を許容する旨明記する。

### 使用例

```python
# 通常の comment 投稿（外部から見た振る舞いは無変更）
provider = LocalProvider(...)  # config.local.toml で machine_id="p1"
comment = provider.comment_issue("local-p1-22", "review feedback")
# -> .kaji/issues/local-p1-22-*/comments/20260510T142536Z-p1.md が atomic 作成
print(f"{comment.seq}-{comment.machine_id}")  # "20260510T142536Z-p1"

# 21 配下の既存 comment 読み取り（移行後）
issue = provider.view_issue("local-pc5090-21")
# 21/comments/<ts>-pc5090.md は seq=<ts>, machine_id="pc5090" として読める
# (machine 部分が pc5090 でも、新形式 timestamp parser で解釈可能)

# config 切替（migration commit 群の最後）以降、21 への新規 comment 投稿は
# self.machine_id="p1" となり <ts>-p1.md で書かれる。21 配下に pc5090 / p1 の
# machine_id が混在することを許容する（前者は migration 前の時点記録、
# 後者は config 切替後の新規投稿）。完了条件は「**移行時点で存在した** 21 配下
# コメントの machine 部分が pc5090 のまま保持されている」を指す。
```

### エラー

| ケース | 振る舞い |
|--------|---------|
| 同秒衝突（同一 machine、同一 timestamp string） | `_atomic_write_new` が `FileExistsError` → filename 用 timestamp を `+1s` して retry。`MAX_COMMENT_WRITE_RETRIES`（既存値 8）まで繰り返す。**filename の timestamp は uniqueness 用、ordering の正本は frontmatter `created_at`**（§ 制約 / § 方針 (1) 参照）。同秒衝突時に filename と `created_at` が乖離しても ordering には影響しない |
| 8 retry 全敗 | `LocalProviderError(f"failed to allocate unique comment filename ... after {N} retries")`。既存メッセージのまま timestamp を含めた diagnostic 文に更新 |
| `_read_comments` で既知形式（timestamp / 旧 seq）にマッチしない filename | `LocalProviderError` で fail-fast。skip しない |
| 移行スクリプトの timestamp 衝突（同一 issue dir 内で同一秒）| 既存 142 件は seq 順を保つため、frontmatter `created_at` を**そのまま filename に転写**するのではなく、**入力順に 1 秒ずつ加算する deterministic 変換**を採用（§ 移行スクリプト方針）。frontmatter `created_at` 自体は無変更 = ordering の正本も無変更 |

## 制約・前提条件

- LocalProvider 以外の provider（github / gitlab）には影響を与えない
- **comment ordering の正本は frontmatter `created_at`**（Issue 目的「comment フィルター順序の正本を `created_at` に一本化」に従う）
  - `_read_comments` は frontmatter `created_at` をパースして dataclass に詰めた後、**`Comment.created_at` で stable sort** する
  - 同 `created_at` 内のタイブレーカーは filename ASCII sort（決定性確保のため）
  - filename の timestamp は **uniqueness 用** であり、衝突時 +1s 加算で `created_at` と乖離しても ordering には波及しない
  - 旧形式 (`<seq>-pc5090.md`) で frontmatter `created_at` 不在のレガシーケースは想定しない（既存 142 件 + 21 の 2 件すべてに `created_at` 存在を移行スクリプトで前提検証 = preflight assertion）
- 移行は **本 PR 内で完結** させる（中途半端な mixed-format 状態を main に残さない）
- **21 (本 issue) の dir / branch / worktree path は移行対象外** として `local-pc5090-21` のまま残す。理由: 移行作業中の worktree path / branch name 整合性を保つ
- migration commits の途中で別 worktree から `kaji issue *` を実行しないことを運用合意とする（mixed-format race 回避）

### 21 配下の comment に対する統一ルール（review-design Must Fix 1 反映）

| 観点 | ルール |
|------|--------|
| 既存 comment の filename スキーマ | migration script で **新形式 timestamp に rename** する（1〜20 と同様） |
| 既存 comment の machine 部分 | `pc5090` を **維持** する（時点記録として保護。dir / branch / worktree path と同等の扱い） |
| 既存 comment frontmatter `author` / `created_at` | 無変更（時点記録） |
| config 切替後（migration 群の最後）の 21 への新規 comment | `self.machine_id="p1"` で投稿される → `<ts>-p1.md`。21 配下に pc5090 / p1 が混在することを **許容**。完了条件は「**移行時点で存在した** 21 配下コメントの machine 部分が pc5090」を指す |
| `_read_comments` の解釈 | 新形式 timestamp parser で `<ts>-pc5090.md` / `<ts>-p1.md` の両方を解釈する（machine 部分は値として読み取るだけで、parser 側で固定値を期待しない） |
| 旧 seq 形式 (`0001-pc5090.md`) の fallback parser | **本 PR で削除** する。migration script が 21 既存 2 件も含めて全件を新形式 timestamp に rename するため、移行完了後の repo に旧 seq 形式は残らない（fallback は不要） |

→ Issue 本文の完了条件「`_read_comments` が新形式を解釈できる（旧形式 fallback は移行 commit 後に削除）」と整合。前回設計の「fallback 保持で merge」案は **撤回** し、Issue 完了条件を満たす形に統一する。

## 変更スコープ

### 影響モジュール

| ファイル | 変更内容 |
|---------|---------|
| `kaji_harness/providers/local.py` | `_next_comment_seq` 削除、`comment_issue` の filename 生成変更、`_read_comments` の parser を **timestamp 形式単独** に切替（旧 seq 形式 fallback は導入せず、未知形式は fail-fast）、`Comment.created_at` を ordering の正本として stable sort、retry ロジック調整 |
| `kaji_harness/providers/models.py` | `Comment.seq` の docstring を更新（timestamp 形式も許容） |
| `kaji_harness/cli_main.py` | docstring / help 文の `<seq>` 表記を変更（line 1288 周辺）。format 文 `f"{seq}-{machine}"` は無変更 |
| `.kaji/config.local.toml` | `machine_id = "p1"` |
| `.kaji/counters/pc5090.txt` → `.kaji/counters/p1.txt` | rename + 中身そのまま (`21`) |
| `.kaji/issues/local-pc5090-{1..20}-*/` | `local-p1-{1..20}-*/` へ rename |
| `.kaji/issues/local-pc5090-{1..20}-*/comments/*.md` | timestamp 形式 + `-p1.md` へ rename + frontmatter 無変更を保証 |
| `.kaji/issues/local-pc5090-21-*/comments/*.md` | timestamp 形式 + `-pc5090.md` へ rename（machine 部分は維持、frontmatter 無変更）|
| `.kaji/issues/local-pc5090-{1..20}-*/issue.md` | frontmatter `id:` 書き換え + 本文 cross-ref 書き換え |
| `tests/test_providers_local.py` | comment filename 形式に依存する fixture / assertion を新形式に更新 |
| `.claude/skills/issue-close/SKILL.md` | comment seq renumber 関連記述を新形式に整合 |
| `docs/cli-guides/local-mode.md`, `docs/operations/local-mode-runbook.md` | 例示 (pc5090 → p1) |
| `scripts/migrate_comment_filenames_and_machine.py` | 新規作成（移行スクリプト本体） |

### 移行対象外（leave）

- commit history 中の `pc5090` 文字列（rewrite 不実施）
- `draft/design/issue-local-pc5090-N-*.md` ファイル名 / 本文（時点記録として正確性保持）
- 既存 comment frontmatter の `author: pc5090` / 既存 issue の `closed_by: pc5090`（時点記録）
- `21/` dir 名（`local-pc5090-21-<slug>/`）と branch / worktree path
- 21 配下 comment の **machine 部分** (`-pc5090.md`)。filename スキーマ（seq → timestamp）は揃えるが、machine 部分は時点記録として保護

## 方針（Minimal How）

### 1. LocalProvider コード変更（commit 1）

```python
# local.py 内の擬似コード（変更箇所のみ）

# 新形式 filename: <YYYYMMDDTHHMMSSZ>-<machine>
# - timestamp 部: 16 文字固定 (8 digit date + "T" + 6 digit time + "Z")
# - machine 部: [a-z0-9]{1,16} (validate_machine_id と同じ正規表現)
_COMMENT_FILENAME_RE = re.compile(
    r"^(?P<ts>\d{8}T\d{6}Z)-(?P<machine>[a-z0-9]{1,16})$"
)

def _read_comments(self, issue_dir: Path) -> list[Comment]:
    cdir = issue_dir / "comments"
    if not cdir.is_dir():
        return []
    result: list[Comment] = []
    for path in cdir.iterdir():
        if path.suffix != ".md":
            continue
        m = _COMMENT_FILENAME_RE.match(path.stem)
        if m is None:
            # 旧 seq 形式 / 規約外 → fail-fast (migration 完了済み前提)
            raise LocalProviderError(f"unrecognized comment filename: {path}")
        ts, machine = m["ts"], m["machine"]
        meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        created_at = str(meta.get("created_at", "") or "")
        if not created_at:
            # ordering の正本が欠落 → fail-fast
            raise LocalProviderError(f"missing 'created_at' in {path}")
        result.append(Comment(
            author=str(meta.get("author", "") or ""),
            body=body,
            created_at=created_at,
            seq=ts,             # filename uniqueness 値（参考用、CLI 表示互換）
            machine_id=machine,
        ))
    # ordering の正本は frontmatter created_at。同 created_at は filename
    # （= seq フィールド）でタイブレーカー。Python sort は stable なので
    # (created_at, seq) の lexicographic 比較で決定的順序が得られる
    result.sort(key=lambda c: (c.created_at, c.seq))
    return result

def comment_issue(self, issue_id: str, body: str) -> Comment:
    # ... 既存の validate / mkdir 部分は無変更 ...
    created_at_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    base_dt = datetime.now(UTC)  # filename 用の独立 timestamp（衝突 retry で +1s）
    last_attempted = ""
    for attempt in range(MAX_COMMENT_WRITE_RETRIES):
        # filename の timestamp は uniqueness 用。+1s 加算で created_at と
        # 乖離しても、ordering は frontmatter created_at で決まるため波及しない
        ts = (base_dt + timedelta(seconds=attempt)).strftime("%Y%m%dT%H%M%SZ")
        last_attempted = ts
        path = cdir / f"{ts}-{self.machine_id}.md"
        try:
            _atomic_write_new(path, content)
        except FileExistsError:
            continue
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                continue
            raise
        return Comment(
            author=self.machine_id, body=body, created_at=created_at_str,
            seq=ts, machine_id=self.machine_id,
        )
    raise LocalProviderError(
        f"failed to allocate unique comment filename in {cdir} after "
        f"{MAX_COMMENT_WRITE_RETRIES} retries (last attempted ts={last_attempted!r})"
    )

# _next_comment_seq は削除
```

### 2. 移行スクリプト方針（commit 2 / 4）

#### 設計原則

- **deterministic** — 入力（既存 142 ファイルの frontmatter `created_at`）に対して常に同じ rename 結果を生成
- **idempotent** — 途中失敗時に再実行しても同結果。実装は dry-run option を持つ
- **git-aware** — `git mv` を使い、git history に rename を記録する（log --follow で追跡可能）

#### 処理ステップ（一回の Python スクリプト実行）

1. **preflight assertion**: `.kaji/issues/local-pc5090-{1..20,21}-*/comments/*.md` 全件で frontmatter `created_at` が存在することを検証（欠落は abort、人手 resolve）。ordering の正本が欠落した状態で migration を進めないため
2. `.kaji/issues/local-pc5090-{1..20}-*/comments/*.md` を発見し、各ファイルの `created_at` を compact ISO 8601 に変換
3. 同一 issue dir 内で同一 timestamp が衝突した場合、**入力順 (元の seq 昇順) に 1 秒ずつ加算** して filename を重複解消（**frontmatter `created_at` は無変更** = ordering の正本不変）
4. `git mv <old> <new>`（new = `<ts>-p1.md`、1〜20 配下）
5. **21 配下の comment** (`0001-pc5090.md` / `0002-pc5090.md` / 以降の追加分) を同様に処理: `git mv <old> <ts>-pc5090.md`（machine 部分は **`pc5090` を維持**、frontmatter `created_at` 不変、衝突 +1s ロジックは共通）
6. issue dir 名を `git mv` で `local-p1-N-<slug>/` に rename（**1〜20 のみ。21 は据置**）
7. 各 issue.md の frontmatter `id:` を `local-pc5090-N` → `local-p1-N` に sed 書き換え（1〜20 のみ）
8. 各 issue.md の **本文中の cross-ref**（`local-pc5090-N` → `local-p1-N`、N=1〜20 のみ。**21 は据置**）を sed 書き換え
9. counter file rename（`pc5090.txt` → `p1.txt`、中身 `21` 維持）
10. **最後に** `.kaji/config.local.toml` の `machine_id = "p1"` を書き換え

#### 21 の境界保証（統一ルール反映）

| 対象 | 動作 |
|------|------|
| `local-pc5090-21-<slug>/` (dir 名) | **据置** |
| `21/comments/*.md` (filename スキーマ) | **新形式 timestamp に rename** |
| `21/comments/*.md` (machine 部分) | **`pc5090` を維持** (`<ts>-pc5090.md`) |
| `21/comments/*.md` (frontmatter `author` / `created_at`) | **無変更** |
| `21/issue.md` (frontmatter `id:`) | **据置**（`local-pc5090-21`） |
| `21/issue.md` 本文中の `local-pc5090-21` 自己参照 | **据置** |
| 1〜20 issue.md 本文中の `local-pc5090-21` への cross-ref | **据置**（21 を指すため） |
| migration 後・config 切替後の 21 への新規 comment | `<ts>-p1.md` で書き込み（許容） |

- 実装上の glob: 1〜20 の `git mv <dir>` は名前列挙（`local-pc5090-1-*` 〜 `local-pc5090-20-*`）で確実に 21 を除外
- cross-ref sed: regex は `local-pc5090-(?:[1-9]|1[0-9]|20)\b` のように **N=1〜20 のみマッチ** させ、`local-pc5090-21` を保護

### 3. config 変更タイミング（commit 3）

`.kaji/config.local.toml` の `machine_id = "p1"` 変更は **必ず移行 commit 群の最後** に置く。事前変更すると `_resolve_issue_dir` が `local-p1-N-*` を glob して既存の `local-pc5090-N-*` を見つけられず、**全 issue が kaji から不可視**になる（counter 不整合 + mixed-format 混入）。

### 4. commit 構成

Issue 本文の commit 構成案を踏襲。本設計書では以下を明示:

```
1. feat(local): switch comment filename to compact ISO 8601 timestamp
   - LocalProvider._read_comments / comment_issue 改修
   - _next_comment_seq 削除、Comment.seq docstring 更新
   - parser は新形式 timestamp 単独（旧 seq 形式 fallback 導入なし、未知形式は fail-fast）
   - ordering を frontmatter created_at で stable sort に切替
   - tests/test_providers_local.py を新形式に更新（旧 seq 形式 fixture は migration commit で消える前提）

2. chore(migration): rename comment files to timestamp format (1..20 + 21)
   - 1〜20 配下: <ts>-p1.md にrename (~142 件)
   - 21 配下: <ts>-pc5090.md にrename (machine 部分維持、~2 件)
   - frontmatter は無変更

3. chore(config): rename counter and switch machine_id to p1
   - .kaji/counters/pc5090.txt → p1.txt
   - .kaji/config.local.toml の machine_id = "p1"
   - **このタイミングで commit 1 の新 parser が config の機械的読込と整合**

4. chore(migration): rename issue dirs and cross-refs to p1 (1..20)
   - 20 issue dir の git mv（21 は据置）
   - issue.md frontmatter id 書き換え（1〜20 のみ）
   - issue.md 本文 cross-ref 書き換え（regex で N=1〜20 のみマッチ、21 references は保護）

5. chore: update example references in skills / docs / kaji_harness docstrings
   - issue-close skill / cli-guides / runbook / cli_main.py docstring 等

6. test: update fixtures depending on user-specific machine_id (if any)
```

**重要**: commit 1 で parser を新形式単独に切り替え、commit 2 で全 file を新形式に rename する。commit 1 と commit 2 の間（中間状態）では migration script 内部でのみ操作し、`kaji issue *` を実行しないことが必須運用制約。**本 PR merge 時に旧 seq 形式 file は repo 上に存在しない**（→ fallback 不要、Issue 完了条件に整合）。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義する。
> 詳細は [testing-convention.md](../../docs/dev/testing-convention.md) 参照。

### 変更タイプ

**実行時コード変更**（LocalProvider のロジック変更）+ **データ移行**（既存 .kaji/issues/ の rename）。

> **テストサイズ判定の根拠**: [`docs/reference/testing-size-guide.md` § subprocess / コマンド実行](../../docs/reference/testing-size-guide.md) に従い、`subprocess.run` で実 CLI / git を実行する検証は **Large (`large_local` マーカー)** に分類する。`tmp_path` でのファイル I/O 結合は Medium、純粋なロジック / 文字列操作は Small。

### Small テスト（単体ロジック、外部依存なし）

`tests/test_providers_local.py` に以下を追加:

- `_read_comments`: 新形式 file (`20260510T142536Z-p1.md`) を読み取って `Comment(seq="20260510T142536Z", machine_id="p1", created_at="...")` を返す
- `_read_comments`: machine 部分が `pc5090` の新形式 file (`20260510T142536Z-pc5090.md`) も解釈できる（21 配下の rename 結果）
- `_read_comments`: 旧 seq 形式 (`0001-pc5090.md`) や規約外 filename (`foo-bar.md`) で `LocalProviderError` を raise（fail-fast、fallback なし）
- `_read_comments`: frontmatter `created_at` 欠落で `LocalProviderError` を raise（ordering 正本欠落の fail-fast）
- `_read_comments`: **ordering** が frontmatter `created_at` 順になる（filename ASCII 順とずらした fixture で検証）
- `_read_comments`: 同 `created_at` 内では filename タイブレーカーで決定的順序になる
- `comment_issue`: 同秒衝突（既存 file がある）で filename timestamp が +1s 加算される。frontmatter `created_at` は **base_dt** の値で固定（ordering 用）— mock で時刻固定して両者の値が一致 / 乖離するケースを assert
- `comment_issue`: 8 retry 全敗で `LocalProviderError`（error message に最後の attempted ts が含まれる）
- `_next_comment_seq` テストを削除（メソッドそのもの削除）
- `_COMMENT_FILENAME_RE` 単独テスト: `YYYYMMDDTHHMMSSZ-<machine>` の正例 / 反例（短い ts、不正 machine 等）

### Medium テスト（ファイル I/O 結合、subprocess なし）

`tests/test_providers_local.py` に以下を追加:

- `tmp_path` 上に LocalProvider を構築 → `comment_issue` 連続呼び出し → file system 上で `<ts>-<m>.md` が生成され、`view_issue().comments` が `created_at` 順で返ることを確認
- migration script の **dry-run plan 生成** (subprocess なし、純 Python 関数): fixture (旧 seq 形式 dir 1 つ + 旧 seq 形式 comment 3 個 + 同秒衝突ケース) に対し、rename plan が deterministic に生成されることを assert
- migration script の **実 rename** (`os.rename` ベースの dry-run=False mode、`git mv` ではなく `Path.rename`): tmp_path に fixture を組み立てて rename → 新 filename が生成され、frontmatter が無変更であることを assert
- counter file rename ロジック (`pc5090.txt` → `p1.txt`): 中身保全と新規 `comment_issue` 後の counter 不変を確認
- `_resolve_issue_dir` が migration 完了後の `local-p1-N-*` glob で正しく解決すること（fixture を直接組んで確認）

### Large テスト（subprocess あり、外部 API なし → `large_local` マーカー）

`tests/test_providers_local.py` または `tests/test_local_issue_commit_flag.py` に以下を追加（`@pytest.mark.large` + `@pytest.mark.large_local`）:

- `kaji issue comment` CLI を `subprocess.run` で実行 → 生成された comment file が新形式 (`<ts>-<machine>.md`) であること、stdout に `<ts>-<machine>` が出力されることを assert（`cli_main.py:1302` の format 整合）
- `kaji issue comment --commit` を `subprocess.run` で実行 → `git status` を `subprocess.run` で確認し、新形式 path が staging されていること（`cli_main.py:1305` 経路）
- migration script を `subprocess.run [..., "scripts/migrate_..."]` で実行（tmp_path に組んだ fixture 上で）→ 142+α file が新形式に rename され、`local-pc5090-21-*` dir / `21/comments/*-pc5090.md` machine 部分が保護されていることを assert
- `kaji issue list` smoke: subprocess 実行で migration 後の全 issue が正常に列挙されること

**実 GitHub / GitLab API 疎通は不要** のため `large_forge` / `large_gitlab` マーカーは付与しない。`make test-large-local` で実行範囲がカバーされる。

### 移行検証（変更固有検証、恒久化しない）

以下は本 PR の作業者が一度だけ実行し、恒久 test には**含めない**:

- `find .kaji/issues -path "*/comments/*.md" | grep -E '/[0-9]{4}-[a-z0-9]+\.md$' | wc -l == 0`（旧 seq 形式が repo 全体に残らないこと、21 含む全件が新形式に揃ったこと）
- `find .kaji/issues -path "*/comments/*-pc5090.md" | grep -v "^.kaji/issues/local-pc5090-21-" | wc -l == 0`（21 以外に `-pc5090.md` machine の comment が残らないこと）
- `find .kaji/issues/local-pc5090-21-*/comments -name '*-pc5090.md' | wc -l >= 2`（21 配下に `-pc5090.md` machine の comment が 2 件以上残ること）
- `grep -rn pc5090 .kaji/counters .kaji/config.local.toml kaji_harness/providers/local.py | wc -l == 0`（コード / config / counter から `pc5090` 残存が消えていること）
- `kaji issue list` が 1〜20 + 21 を全件表示する smoke（手動）
- `kaji issue create --title "smoke"` で `local-p1-22` が採番されることの smoke（手動）
- `make check` 緑（恒久ゲート、最終確認用）

これらの検証を Large テストに恒久化しない理由（`testing-convention.md` 4 条件）:

1. 本 PR 固有のデータ移行検証であり、移行完了後は再実行する状況がない（独自ロジックの恒久実行に該当しない）
2. 想定不具合パターン（旧 seq 残存・21 保護違反・コード内 pc5090 残存）は Large `large_local` の migration script subprocess テストでカバー済み
3. 1 回限りの確認を恒久化しても回帰検出情報が増えない（migration は冪等だが状況変化なし）
4. 上記理由を本セクションでレビュー可能な形で説明済み

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 新規技術選定なし。filename convention 内部変更のみ |
| `docs/ARCHITECTURE.md` | なし | アーキテクチャレベルの変更なし |
| `docs/dev/` | なし | workflow / testing-convention 改訂なし |
| `docs/reference/` | なし | API 仕様変更なし（Comment.seq の docstring は実装内に閉じる） |
| `docs/cli-guides/local-mode.md` | あり | 例示の `pc5090` → `p1` への置換、comment filename 例の更新 |
| `docs/operations/local-mode-runbook.md` | あり | 同上 |
| `CLAUDE.md` | なし | プロジェクト規約の変更なし |
| `.claude/skills/issue-close/SKILL.md` | あり | comment seq renumber に関する記述があれば新形式に整合（または記述削除） |
| `.claude/skills/issue-start/SKILL.md` | あり（minor） | machine_id を含む例示があれば置換 |
| `draft/design/local-mode/design.md` | なし | 時点記録として保持（leave） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| LocalProvider 既存実装 | `kaji_harness/providers/local.py:461-493` | `_read_comments` / `_next_comment_seq` の現行ロジック。worktree-local の最大 seq + 1 採番が衝突原因であることを実装で確認 |
| comment write retry | `kaji_harness/providers/local.py:564-620` | `MAX_COMMENT_WRITE_RETRIES=8` の既存 retry ループ。timestamp +1s retry の継承元 |
| Comment dataclass | `kaji_harness/providers/models.py:24-35` | `seq: str = ""` フィールドの存在と「local 固有、GitHub では空」のコメント。新形式で値の意味を拡張する根拠 |
| CLI seq 表示 | `kaji_harness/cli_main.py:1302, 1305` | stdout / commit path の `f"{seq}-{machine}"` フォーマット。Comment.seq に timestamp 文字列を入れれば format 自体は無変更で動作 |
| 衝突実例 (記録) | `.kaji/issues/local-pc5090-20-*/issue.md` | local-pc5090-7/8/9/10 連続実行で 0001-0003 の add/add 衝突が発生し、commit 877fa84 で手動 renumber して merge した記録 |
| 衝突 commit 実例 | git log: `877fa84 chore(local): renumber conflicting comment files for local-pc5090-10` | 手動 renumber が必要だった事実の git history 上の証跡 |
| ISO 8601 compact format | RFC 3339 / ISO 8601 (basic format): `YYYYMMDDTHHMMSSZ` | UTC 秒精度の固定長表現。Python: `datetime.strftime("%Y%m%dT%H%M%SZ")`。lexical sort = chronological sort が成立 |
| Python datetime strftime | https://docs.python.org/3/library/datetime.html#strftime-strptime-behavior | `%Y%m%dT%H%M%SZ` ディレクティブの公式仕様。"Z" は literal 文字（UTC indicator として規約上使用） |
| testing-convention | `docs/dev/testing-convention.md` § テスト戦略の原則 | 「実行時の振る舞いを変えるコード変更 → 影響範囲に応じて Small / Medium / Large を設計」に従い、Small / Medium / Large の各サイズで観点を定義する。変更固有検証の恒久テスト不要は 4 条件で justify |
| testing-size-guide | `docs/reference/testing-size-guide.md` § subprocess / コマンド実行 | 「実際の CLI コマンドを subprocess で実行 → Large」の規約。subprocess 検証を Large (`large_local` マーカー) に分類する根拠 |
| Large 細分マーカー | `docs/reference/testing-size-guide.md` § Large の細分マーカー | `large_local`: subprocess あり / 外部ネットワーク無し（kaji 自身の CLI 等）。本 PR の subprocess テストはこれに該当 |
| feat 設計テンプレ | `.claude/skills/_shared/design-by-type/feat.md` | type=feature の必須セクション（IF / 使用例 / エラー / 代替案）の準拠元 |

</details>


## 概要

LocalProvider のコメントファイル命名規則を `<seq>-<machine_id>.md` (例: `0001-pc5090.md`) から **compact ISO 8601 timestamp** ベース (`<YYYYMMDDTHHMMSSZ>-<machine_id>.md`) に変更し、同時に machine_id を `pc5090` から `p1` に短縮する。

## 背景

### 課題 1: comment seq 連番衝突 (race)

local-p1-7/8/9/10 の連続自動実行 (CronCreate one-shot, 2026-05-10) で、3/3 の close 時に `.kaji/issues/*/comments/000N-*.md` の add/add 衝突が発生し、close skill が手動でリナンバリングして merge する事象が再現した。

原因: `LocalProvider._next_comment_seq` が **worktree-local の最大 seq + 1** で採番し、別 worktree (main / feature) の存在を考慮しない設計。並行コミット下で seq が独立進行し、merge 時に衝突する。

詳細は `local-p1-20` (記録 issue) 参照。

### 課題 2: machine_id `pc5090` の入力負荷

`local-pc5090-N` 形式の issue 参照、コメントファイル名 (`-pc5090.md`)、author 表示で 6 文字を毎回入力 / 表示。`p1` (2 文字) に短縮することで入力削減と可読性向上。

## 目的

1. seq 衝突を **原理的に消滅** させる（worktree 間 race の根本解消）
2. comment フィルター順序の正本を `created_at` (frontmatter 既存) に一本化
3. `_next_comment_seq` 採番ロジックの削除によるコード単純化
4. machine_id 短縮で日常入力 / 表示を 4 文字短縮
5. 既存 142 コメントファイル / 20 issue ディレクトリの**移行を同一 PR 内で完了**

## ユーザーストーリー

- maintainer として、`kaji issue comment` を別 worktree で並行投稿しても merge で seq 衝突が起きない状態にしたい
- maintainer として、`local-p1-N` の短い ID で issue 参照したい
- maintainer として、過去の commit history (pc5090 references) は時点記録として保持したい

## スコープ

### IN

#### コード変更 (kaji_harness/providers/local.py)

- `_read_comments`: ファイル名 parser を新形式 (`<timestamp>-<machine_id>.md`) に対応
  - 移行期の安全装置として旧形式 (`<seq>-<machine_id>.md`) も accept する fallback を併存（同 PR 内の rename commit 後に削除）
- `add_comment`: ファイル名生成を `f"{seq}-{machine_id}.md"` から `f"{timestamp_compact}-{machine_id}.md"` に変更
  - timestamp_compact = `datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")`
- `_next_comment_seq`: **削除**
- `_atomic_write_new` retry: 同 machine 同秒衝突は ms 精度化 or retry suffix で対応（実装時判断、現状未発生のため最小実装で可）
- `Comment` dataclass の `seq` フィールド: 廃止 or `created_at` から導出（cli_main.py の表示影響を確認した上で判断）

#### 移行スクリプト (scripts/migrate_comment_filenames_and_machine.py)

- 全 142 コメントファイルを `git mv` で rename:
  - 旧: `.kaji/issues/local-pc5090-N-*/comments/0001-pc5090.md`
  - 新: `.kaji/issues/local-p1-N-*/comments/20260509T061152Z-p1.md`
- 全 20 issue dir を `git mv` で rename: `local-pc5090-N-*` → `local-p1-N-*`
- counter file rename: `.kaji/counters/pc5090.txt` → `p1.txt`（中身そのまま、次は local-p1-21）
- 全 issue.md frontmatter `id:` 書き換え: `local-pc5090-N` → `local-p1-N`
- 全 issue.md 本文の cross-ref 書き換え: `local-pc5090-N` → `local-p1-N`
- 衝突検出 (同 timestamp + 同 machine_id) は abort、人間判断で resolve

#### 設定変更 (.kaji/config.local.toml)

- `machine_id = "pc5090"` → `machine_id = "p1"`

#### 説明系参照の更新

- `kaji_harness/cli_main.py` / `config.py` / `adapters.py` / `sync.py` 内の docstring / コメント例（pc5090 → p1）
- `.claude/skills/` 配下 2 ファイルの例示（pc5090 → p1）
- `docs/cli-guides/` 配下 2 ファイル

#### Skill 修正

- `.claude/skills/issue-close/SKILL.md`: comment seq renumber 関連記述（あれば）削除、新形式に整合する記述に更新

#### Test 修正

- `tests/test_providers_local.py` 等の **test fixture が functional に依存するもの** を新形式に更新
- machine_id を fixture として hardcoded する箇所は test ごとに「意図的に specific か / user-config 依存か」を判定し、後者のみ更新（`pc5090` → `p1` or 中立的な `m1`）

### OUT

- **commit message 内の pc5090 references**: leave (history rewrite 不実施)
- **`draft/design/` 配下のファイル名 / 本文の `local-pc5090-N` references**: leave (時点記録として正確性保持)
- **旧コメントの `author: pc5090` / 旧 issue の `closed_by: pc5090`**: leave (時点記録)
- **GitHub remote への push**: GitHub account suspended 解除後に手動実施
- ms 精度化 / retry suffix の本格実装: 現状同秒衝突未発生のため、実装は最小化 or 後 issue
- comment の symlink / alias 経由互換: 不要（移行で完結）

## 移行制約 / 境界条件

### 移行対象の境界

- **移行対象**: `local-p1-1` 〜 `local-p1-20`（20 件）
- **対象外**: `local-pc5090-21`（本 issue 自身、移行境界の歴史的記録として pc5090 namespace に残す）
- 影響: worktree path (`kaji-feat-local-pc5090-21`) / branch name (`feat/local-pc5090-21`) も pc5090 のまま保持され、workflow runner / git 側の path 整合性が崩れない

### Counter 初期値

- `.kaji/counters/p1.txt` 初期値: `21`（次の新規 issue は `local-p1-22`）
- `.kaji/counters/pc5090.txt`: 移行完了後に **削除** する（残置しても害はないが、旧 namespace への誤書き込み防止のため明示削除）

### Config 変更タイミング

- `.kaji/config.local.toml` の `machine_id = "p1"` 変更は **移行スクリプト実行 commit 群の最後** に行う
- **事前変更は禁止**: machine_id を p1 に変えた瞬間、`_resolve_issue_dir` が `local-p1-N-*` を glob して既存の `local-pc5090-N-*` を見つけられず、**既存 issue 全件が kaji から不可視** になる
- 副作用: counter 不整合（`p1.txt` 不在のため新規 issue が `local-p1-1` から採番されて衝突） / mixed-format コメント混入

### 移行中の運用制約

- migration commits の途中で別 worktree から `kaji issue *` を実行しない（mixed-format race 回避）
- 本 issue (21) 用の worktree / branch 名（`kaji-feat-local-pc5090-21` / `feat/local-pc5090-21`）は **変更しない**（移行対象外なので git 内部整合性が保たれる）

## 完了条件

- [x] `_next_comment_seq` が削除されている
- [x] `add_comment` が `<timestamp>-<machine_id>.md` 形式で書き込む
- [x] `_read_comments` が新形式を解釈できる（旧形式 fallback は移行 commit 後に削除）
- [x] 1〜20 の issue ディレクトリ (20 件) が `local-p1-N-*` に rename されている
- [x] **21 (本 issue) は `local-pc5090-21-*` のまま保持されている**（移行境界の歴史的記録）
- [x] 142 コメントファイル (1〜20 配下) が新形式 + `-p1.md` に rename されている
- [x] 21 配下のコメント（本 issue 自身のもの）は新形式 + `-pc5090.md` のまま保持されている
- [x] `.kaji/counters/p1.txt` が存在し、中身は `21`（次の新規 issue は local-p1-22）
- [x] `.kaji/counters/pc5090.txt` が **削除** されている
- [x] `.kaji/config.local.toml` の machine_id が `p1`（**migration commit 群の最後**で変更）
- [x] `grep -rn pc5090 .kaji/counters .kaji/config.local.toml kaji_harness/providers/local.py | wc -l == 0`
- [x] `make check` 緑 (ruff / format / mypy / pytest)
- [x] 移行後に `kaji issue create` で `local-p1-22` が採番されることを smoke test で確認

## 影響範囲

| 領域 | 影響 |
|---|---|
| LocalProvider 機能 | comment write / read / 採番ロジック |
| 既存データ | 142 コメント + 20 issue dir + counter file |
| CLI 出力 | `[author @ created_at]` 表示は無変更（cli_main.py:1162） |
| JSON 出力 | `createdAt` キーは無変更（cli_main.py:990） |
| Skill | issue-close の renumber 記述 削除 |
| Docs | cli-guides 2 件、設計書は leave |
| Tests | 一部 fixture 更新、構造的テストは新形式対応 |
| Git history | 無変更 (rewrite 不実施) |
| 他 provider (github / gitlab) | 無影響 (read 経路独立) |

## リスクと軽減策

| リスク | 軽減策 |
|---|---|
| 移行漏れ (pc5090 残存) | 完了条件の grep チェック、許容 leave 範囲を docs に明示 |
| test fixture が壊れる | TDD: test を先に新形式に更新、red → green → refactor |
| 旧形式 parser fallback の削除タイミング誤り | 同一 PR 内で rename commit 直後に fallback を削除する commit を作る |
| Counter 採番ずれ | `.kaji/counters/p1.txt` の中身を pc5090.txt から継承（移行スクリプトで保証） |
| issue cross-ref の漏れ | 移行スクリプトで `local-pc5090-N` → `local-p1-N` を全 issue.md 本文に sed 適用 + 結果を git diff で目視 |
| 進行中作業との race | この issue は他の `kaji issue comment` 並行実行を控える運用合意下で進める |
| 同 machine 同秒衝突 (将来) | 現状 0 件、ms 精度化 / retry suffix は別 issue で対応可 |

## 移行 commit 構成案

```
1. feat(local): switch comment filename to compact ISO 8601 timestamp
   - LocalProvider 修正、新旧両形式 parser 対応 (fallback は一時的)
   - tests 新形式対応

2. chore(migration): rename comment files to timestamp format
   - 142 ファイルの rename + frontmatter 無変更を確認

3. chore(config): change machine_id from pc5090 to p1
   - .kaji/config.local.toml 1 行変更
   - .kaji/counters/pc5090.txt → p1.txt rename

4. chore(migration): rename issue dirs and cross-refs to p1
   - 20 issue dir rename
   - frontmatter id 書き換え
   - body 内 cross-ref 書き換え

5. refactor(local): drop legacy NNNN-machine.md parser fallback
   - 移行完了確認後、旧形式 parser を削除

6. chore: update example references in skills / docs / kaji_harness
   - 説明系参照の更新

7. test: update fixtures depending on user-specific machine_id (if any)
```

## 関連 issue / commit

- local-p1-19: skill 修正 (issue-close offline fallback) audit trail
- local-p1-20: 連続実行予約全体の知見記録 (本 issue 起票の動機)
- commit `14dc29e`: 関連 skill 修正

## 注意事項

- 本 issue 着手中は **他 worktree での `kaji issue comment` 並行実行を控える**（migration race 回避）
- E 実装と machine rename を分離せず、1 つの issue / branch / 一連の commit で完結させる（中途半端な状態を残さない）
