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

# 旧形式 comment の読み取り（移行 commit より前 / 21 配下では fallback parser が活きる）
issue = provider.view_issue("local-pc5090-21")
# 21/comments/0001-pc5090.md は seq="0001", machine_id="pc5090" として読める
```

### エラー

| ケース | 振る舞い |
|--------|---------|
| 同秒衝突（同一 machine、同一 timestamp string） | `_atomic_write_new` が `FileExistsError` → timestamp を `+1s` して retry。`MAX_COMMENT_WRITE_RETRIES`（既存値 8）まで繰り返す |
| 8 retry 全敗 | `LocalProviderError(f"failed to allocate unique comment filename ... after {N} retries")`。既存メッセージのまま timestamp を含めた diagnostic 文に更新 |
| 旧形式 comment の parser fallback 削除後に旧形式 file が混入 | `_read_comments` は当該 file を skip せず、エラーで早期失敗（fail-fast）させる。移行完了後は混入そのものが事故扱いだから |
| 移行スクリプトの timestamp 衝突（同一 issue dir 内で同一秒）| 既存 142 件は seq 順を保つため、frontmatter `created_at` を**そのまま filename に転写**するのではなく、**入力順に 1 秒ずつ加算する deterministic 変換**を採用（§ 移行スクリプト方針） |

## 制約・前提条件

- LocalProvider 以外の provider（github / gitlab）には影響を与えない
- comment 並び順は **filename ASCII sort** に依存する（`_read_comments` の `sorted(cdir.iterdir())`）。timestamp は固定長 16 文字なので lexical sort = chronological sort
- 移行は **本 PR 内で完結** させる（中途半端な mixed-format 状態を main に残さない）
- **21 (本 issue) の dir / branch / worktree path は移行対象外** として `local-pc5090-21` のまま残す。理由: 移行作業中の worktree path / branch name 整合性を保つ
- migration commits の途中で別 worktree から `kaji issue *` を実行しないことを運用合意とする（mixed-format race 回避）

### 21 を例外扱いする境界条件

- `21/comments/0001-pc5090.md` は新形式 parser の fallback で読める必要がある
- fallback は migration commit 5（`refactor: drop legacy parser`）で削除されるが、その時点で `21/comments/` に**新規 comment を書かない運用**であれば fallback 削除後も存在する旧 file は **read 不可** になる
- → fallback 削除タイミングは「21 完了 (close) 後」が安全だが、**本 PR は 21 close 前に merge する** ため、fallback は **保持したまま merge** する選択肢も検討
- **採用方針**: fallback parser は **保持して merge** し、22 番以降の運用が `p1` 系で安定したのち、別 issue で削除する。本 PR で削除すると 21 の comment が読めなくなるため

→ 完了条件 §「`_read_comments` が新形式を解釈できる（旧形式 fallback は移行 commit 後に削除）」の **後段（fallback 削除）は本 issue から外し、別 issue 化** する旨、Issue 本文の「OUT」または別 issue 切り出しを review-design 段階で擦り合わせる。

## 変更スコープ

### 影響モジュール

| ファイル | 変更内容 |
|---------|---------|
| `kaji_harness/providers/local.py` | `_next_comment_seq` 削除、`comment_issue` の filename 生成変更、`_read_comments` の parser を timestamp 形式 + 旧形式 fallback に拡張、retry ロジック調整 |
| `kaji_harness/providers/models.py` | `Comment.seq` の docstring を更新（timestamp 形式も許容） |
| `kaji_harness/cli_main.py` | docstring / help 文の `<seq>` 表記を変更（line 1288 周辺）。format 文 `f"{seq}-{machine}"` は無変更 |
| `.kaji/config.local.toml` | `machine_id = "p1"` |
| `.kaji/counters/pc5090.txt` → `.kaji/counters/p1.txt` | rename + 中身そのまま (`21`) |
| `.kaji/issues/local-pc5090-{1..20}-*/` | `local-p1-{1..20}-*/` へ rename |
| `.kaji/issues/local-pc5090-{1..20}-*/comments/*.md` | timestamp 形式 + `-p1.md` へ rename + frontmatter 無変更を保証 |
| `.kaji/issues/local-pc5090-{1..20}-*/issue.md` | frontmatter `id:` 書き換え + 本文 cross-ref 書き換え |
| `tests/test_providers_local.py` | comment filename 形式に依存する fixture / assertion を新形式に更新 |
| `.claude/skills/issue-close/SKILL.md` | comment seq renumber 関連記述を新形式に整合 |
| `docs/cli-guides/local-mode.md`, `docs/operations/local-mode-runbook.md` | 例示 (pc5090 → p1) |
| `scripts/migrate_comment_filenames_and_machine.py` | 新規作成（移行スクリプト本体） |

### 移行対象外（leave）

- commit history 中の `pc5090` 文字列（rewrite 不実施）
- `draft/design/issue-local-pc5090-N-*.md` ファイル名 / 本文（時点記録として正確性保持）
- 既存 comment frontmatter の `author: pc5090` / 既存 issue の `closed_by: pc5090`（時点記録）
- `21/` dir 配下（dir 名・comment filename ともに `pc5090` のまま）

## 方針（Minimal How）

### 1. LocalProvider コード変更（commit 1）

```python
# local.py 内の擬似コード（変更箇所のみ）

def _read_comments(self, issue_dir: Path) -> list[Comment]:
    cdir = issue_dir / "comments"
    if not cdir.is_dir():
        return []
    result = []
    for path in sorted(cdir.iterdir()):
        if path.suffix != ".md":
            continue
        stem = path.stem
        # 新形式: <YYYYMMDDTHHMMSSZ>-<machine>
        if _TIMESTAMP_FILENAME_RE.match(stem):
            ts, _, machine = stem.partition("-")
            seq_value = ts
        # 旧形式 fallback: <NNNN>-<machine>
        elif _LEGACY_FILENAME_RE.match(stem):
            seq_value, _, machine = stem.partition("-")
        else:
            # 解釈不能 → fail-fast
            raise LocalProviderError(f"unrecognized comment filename: {path}")
        meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        result.append(Comment(
            author=str(meta.get("author", "")),
            body=body,
            created_at=str(meta.get("created_at", "")),
            seq=seq_value,
            machine_id=machine,
        ))
    return result

def comment_issue(self, issue_id: str, body: str) -> Comment:
    # ... 既存の validate / mkdir 部分は無変更 ...
    created_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    base_dt = datetime.now(UTC)  # filename 用の独立 timestamp
    last_attempted = ""
    for attempt in range(MAX_COMMENT_WRITE_RETRIES):
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
        return Comment(author=self.machine_id, body=body, created_at=created_at,
                       seq=ts, machine_id=self.machine_id)
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

1. `.kaji/issues/local-pc5090-{1..20}-*/comments/*.md` を発見
2. 各ファイルの frontmatter から `created_at` を読み出し、compact ISO 8601 に変換
3. 同一 issue dir 内で同一 timestamp が衝突した場合、**入力順 (元の seq 昇順) に 1 秒ずつ加算** して重複解消
4. `git mv <old> <new>`（new = `<ts>-p1.md`）
5. issue dir 名を `git mv` で `local-p1-N-<slug>/` に rename
6. 各 issue.md の frontmatter `id:` を `local-pc5090-N` → `local-p1-N` に sed 書き換え
7. 各 issue.md の **本文中の cross-ref**（`local-pc5090-N` → `local-p1-N`、N=1〜20 のみ。**21 は据置**）を sed 書き換え
8. counter file rename（`pc5090.txt` → `p1.txt`、中身 `21` 維持）
9. **最後に** `.kaji/config.local.toml` の `machine_id = "p1"` を書き換え

#### 21 の境界保証

- スクリプト内で `local-pc5090-21-*` は処理対象外として **明示的に除外**（glob で `[1-9]-*` `1[0-9]-*` `20-*` のみ拾う、または除外リストで `21` を skip）
- cross-ref 書き換えで `local-pc5090-21` の文字列は **書き換えない**（21 自身を指す参照 / commit message 内の参照を保護）

### 3. config 変更タイミング（commit 3）

`.kaji/config.local.toml` の `machine_id = "p1"` 変更は **必ず移行 commit 群の最後** に置く。事前変更すると `_resolve_issue_dir` が `local-p1-N-*` を glob して既存の `local-pc5090-N-*` を見つけられず、**全 issue が kaji から不可視**になる（counter 不整合 + mixed-format 混入）。

### 4. commit 構成

Issue 本文の commit 構成案を踏襲。本設計書では以下を明示:

```
1. feat(local): switch comment filename to compact ISO 8601 timestamp
   - LocalProvider._read_comments / comment_issue 改修
   - _next_comment_seq 削除、Comment.seq docstring 更新
   - 旧形式 fallback parser を併存
   - tests/test_providers_local.py 新形式対応

2. chore(migration): rename comment files to timestamp format (1..20)
   - 142 ファイルの git mv（21 は据置）

3. chore(config): rename counter and switch machine_id to p1
   - .kaji/counters/pc5090.txt → p1.txt
   - .kaji/config.local.toml の machine_id = "p1"

4. chore(migration): rename issue dirs and cross-refs to p1 (1..20)
   - 20 issue dir の git mv
   - issue.md frontmatter id 書き換え
   - issue.md 本文 cross-ref 書き換え（21 references は保護）

5. chore: update example references in skills / docs / kaji_harness docstrings
   - issue-close skill / cli-guides / runbook / cli_main.py docstring 等

6. test: update fixtures depending on user-specific machine_id (if any)
```

**fallback parser 削除 commit は本 PR には含めない**（§ 21 を例外扱いする境界条件 参照）。別 issue で 21 close 後に実施。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義する。
> 詳細は [testing-convention.md](../../docs/dev/testing-convention.md) 参照。

### 変更タイプ

**実行時コード変更**（LocalProvider のロジック変更）+ **データ移行**（既存 .kaji/issues/ の rename）。

### Small テスト（単体ロジック）

`tests/test_providers_local.py` に以下を追加:

- `_read_comments`: 新形式 file (`20260510T142536Z-p1.md`) を読み取って `Comment(seq="20260510T142536Z", machine_id="p1")` を返す
- `_read_comments`: 旧形式 file (`0001-pc5090.md`) を読み取って `Comment(seq="0001", machine_id="pc5090")` を返す（fallback 動作）
- `_read_comments`: 同一 dir に新旧 mixed の場合、ASCII sort 順で読み出す（`0001-pc5090.md` < `20260510...` の lexical 関係を確認）
- `_read_comments`: 規約外の filename（例: `foo-bar.md`）で `LocalProviderError` を raise
- `comment_issue`: 新規投稿で `<ts>-<machine>.md` 形式の file が atomic 生成され、Comment.seq が timestamp、machine_id が config 値となる
- `comment_issue`: 同秒衝突（既存 file がある）で 1 秒繰り上げ retry が動作する（mock で時刻固定）
- `comment_issue`: 8 retry 全敗で `LocalProviderError` を raise（既存テストの error message を新形式に合わせて更新）
- `_next_comment_seq` 削除に伴い、当該テストを削除

### Medium テスト（ファイル I/O 結合）

`tests/test_providers_local.py` に以下を追加:

- end-to-end: tmp_path 上に LocalProvider を構築 → comment_issue 連続呼び出し → file system 上で N 個の `<ts>-<m>.md` が生成されることを確認
- migration script の dry-run: fixture (旧形式 dir 1 つ + 旧形式 comment 3 個) に対し、rename plan が deterministic に生成されることを assert（実 rename はせず plan を返す mode）
- counter file rename: pc5090.txt → p1.txt の中身保全と、新規 `comment_issue` 後の counter 不変を確認
- `cli_main.py:1305` の `--commit` path: comment 投稿 → 生成 path が新形式で git commit に含まれる（subprocess.run で `git status` を確認）

### Large テスト

**追加しない**。理由:

1. 本変更は LocalProvider 内部の filename / parser の変更であり、外部 API（GitHub / GitLab）疎通を伴わない
2. CLI E2E 観点は既存の `tests/test_cli_main.py` / `tests/test_local_issue_commit_flag.py` が新形式 fixture に追従すれば吸収可能（追加テストは Small / Medium で十分）
3. `make test-large-local` の subprocess CLI tests は Phase 3-d 既存の comment write カバレッジで再現可能

### 移行検証（変更固有検証、恒久化しない）

以下は本 PR でのみ実行し、恒久 test には**含めない**:

- `find .kaji/issues -name "comments" -type d -exec ls {} \; | grep -E '^[0-9]{4}-pc5090\.md$' | wc -l == 1`（21 配下の `0001-pc5090.md` のみが残ること）
- `find .kaji/issues -name "*.md" -path "*/comments/*" | grep -v "^.kaji/issues/local-pc5090-21" | grep -E '\d{4}-pc5090\.md$' | wc -l == 0`（21 以外に旧形式が残らないこと）
- `grep -rn pc5090 .kaji/counters .kaji/config.local.toml kaji_harness/providers/local.py | wc -l == 0`
- `kaji issue list` が 1〜20 + 21 を全件表示する smoke test
- `kaji issue create --title "smoke"` で `local-p1-22` が採番されることの smoke test
- `make check` 緑

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
| testing-convention | `docs/dev/testing-convention.md` § テスト戦略の原則 | 「実行時の振る舞いを変えるコード変更 → 影響範囲に応じて Small / Medium / Large を設計」に従い、Large は省略理由を明記する形を採用 |
| feat 設計テンプレ | `.claude/skills/_shared/design-by-type/feat.md` | type=feature の必須セクション（IF / 使用例 / エラー / 代替案）の準拠元 |
