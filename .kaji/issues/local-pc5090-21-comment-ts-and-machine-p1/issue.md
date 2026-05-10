---
id: local-pc5090-21
title: 'feat: comment ファイル名 timestamp 化 + machine_id pc5090 → p1 への統合移行'
state: open
slug: comment-ts-and-machine-p1
labels:
- type:feature
created_at: '2026-05-10T07:09:36Z'
---
## 概要

LocalProvider のコメントファイル命名規則を `<seq>-<machine_id>.md` (例: `0001-pc5090.md`) から **compact ISO 8601 timestamp** ベース (`<YYYYMMDDTHHMMSSZ>-<machine_id>.md`) に変更し、同時に machine_id を `pc5090` から `p1` に短縮する。

## 背景

### 課題 1: comment seq 連番衝突 (race)

local-pc5090-7/8/9/10 の連続自動実行 (CronCreate one-shot, 2026-05-10) で、3/3 の close 時に `.kaji/issues/*/comments/000N-*.md` の add/add 衝突が発生し、close skill が手動でリナンバリングして merge する事象が再現した。

原因: `LocalProvider._next_comment_seq` が **worktree-local の最大 seq + 1** で採番し、別 worktree (main / feature) の存在を考慮しない設計。並行コミット下で seq が独立進行し、merge 時に衝突する。

詳細は `local-pc5090-20` (記録 issue) 参照。

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

## 完了条件

- [ ] `_next_comment_seq` が削除されている
- [ ] `add_comment` が `<timestamp>-<machine_id>.md` 形式で書き込む
- [ ] `_read_comments` が新形式を解釈できる（旧形式 fallback は移行 commit 後に削除）
- [ ] 142 コメントファイルすべてが新形式 + `-p1.md` に rename されている
- [ ] 20 issue ディレクトリすべてが `local-p1-N-*` に rename されている
- [ ] `.kaji/counters/p1.txt` が存在し、中身は `20`
- [ ] `.kaji/config.local.toml` の machine_id が `p1`
- [ ] `grep -rn pc5090 .kaji/counters .kaji/config.local.toml kaji_harness/providers/local.py | wc -l == 0`
- [ ] `make check` 緑 (ruff / format / mypy / pytest)
- [ ] 移行後に `kaji issue create` で `local-p1-21` が採番されることを smoke test で確認

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

- local-pc5090-19: skill 修正 (issue-close offline fallback) audit trail
- local-pc5090-20: 連続実行予約全体の知見記録 (本 issue 起票の動機)
- commit `14dc29e`: 関連 skill 修正

## 注意事項

- 本 issue 着手中は **他 worktree での `kaji issue comment` 並行実行を控える**（migration race 回避）
- E 実装と machine rename を分離せず、1 つの issue / branch / 一連の commit で完結させる（中途半端な状態を残さない）
