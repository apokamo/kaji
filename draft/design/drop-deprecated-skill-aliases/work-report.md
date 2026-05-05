# 作業報告: 互換用 Skill エイリアスの削除

| 項目 | 値 |
|------|-----|
| 設計書 | `draft/design/drop-deprecated-skill-aliases/design.md` |
| ブランチ | `refactor/drop-skill-aliases` |
| Worktree | `/home/aki/dev/kaji/kaji-refactor-drop-skill-aliases` |
| 起点コミット | `a6d03b5` (main) |
| 実施日 | 2026-05-05 |
| 報告者 | Claude (sonnet-4-7-1m, kaji harness 経由ではなく直接実施) |
| GitHub Issue / PR | **未起票・未作成**（GitHub アカウント停止のためローカル運用） |

## 概要

設計書 `draft/design/drop-deprecated-skill-aliases/design.md` に基づき、互換用 Skill エイリアス 2 件 (`issue-pr`, `issue-doc-check`) を削除し、参照する README / CLAUDE.md / docs を正本名 (`i-pr`, `i-dev-final-check`) に統一した。

設計書からの逸脱が **1 件** 発生した（テスト変更の追加。詳細は「設計書からの逸脱」セクション参照）。

## 実施内容

### 削除した実体・symlink

```
.claude/skills/issue-pr/             (dir, SKILL.md 89 行)
.claude/skills/issue-doc-check/      (dir, SKILL.md 104 行)
.agents/skills/issue-pr              (symlink → ../../.claude/skills/issue-pr)
.agents/skills/issue-doc-check       (symlink → ../../.claude/skills/issue-doc-check)
```

`git rm -r` で 1 コマンドで削除。

### 文言更新したドキュメント

| ファイル | 変更行 | 内容 |
|---------|--------|------|
| `README.md` | L39 | `/issue-pr` → `/i-pr` |
| `CLAUDE.md` | L136 | 「`/issue-pr`（`/i-pr` 経由）」→ 「`/i-pr`」 |
| `docs/ARCHITECTURE.md` | L43 | `/issue-pr` → `/i-pr` |
| `docs/ARCHITECTURE.md` | L60 | 「計 25 種」→ 「計 23 種」 |
| `docs/ARCHITECTURE.md` | L70-72 | Skill カテゴリ表から `issue-pr`（委譲ラッパー）と `issue-doc-check` を削除し、PR 行は `i-pr` のみ、その他 行は `kaji-run-verify` のみに |
| `docs/guides/git-worktree.md` | L169 | `/issue-pr` → `/i-pr` |
| `docs/guides/git-commit-flow.md` | L31, 86, 97, 113 | `/issue-pr` → `/i-pr` (4 箇所、`replace_all` で一括置換) |
| `draft/design/local-mode/design.md` | L18 | Skill 数 25→23、`gh` 直叩き計数 21→20、計測手法見直し方針を注記として追記 |
| `draft/design/local-mode/design.md` | L56 | 同上の数値更新と注記追記 |
| `draft/design/local-mode/design.md` | L1278 | Phase 2 行の「21 Skill」→ 「20 Skill」+「着手前に計測手法を見直し真値を確定」追記 |

### 設計書からの逸脱: テストファイル更新（1 件）

設計書「テスト戦略」セクションでは「実行時コード変更なし、Skill/docs/symlink の整理のみ」と宣言したが、`make check` 実行時に **既存テストが alias 名をハードコードしている**ことが判明した。

逸脱内容:

| ファイル | 変更内容 |
|---------|---------|
| `tests/test_skill_harness_adaptation.py:39-40` | `WORKFLOW_SKILLS` リストから `"issue-doc-check"`, `"issue-pr"` を削除 |
| `tests/test_skill_harness_adaptation.py:219-220` | `_SKILL_STATUSES` dict から `"issue-doc-check"`, `"issue-pr"` のエントリを削除 |

逸脱の正当化:
- これらのテストは parametrized list で skill 名を列挙しており、削除した SKILL.md を参照しようとして `FileNotFoundError` で 10 件失敗していた
- alias を削除した以上、これらの test parametrize entry も同期削除する必要がある（**設計書のスコープから外せなかったのは設計時の見落とし**）
- 変更内容は parametrize list からの **エントリ削除のみ** で、テストロジックの変更ではない
- 設計の趣旨（実行時コード変更ゼロ）は維持されている

別途検討した変更の見送り:
- `tests/test_verdict_e2e.py:43` にコメント「approximate output structure from the #73 issue-pr step」がある。これは過去の Issue #73 の文脈を示す **歴史的参照** であり、削除すると過去経緯が読み取れなくなる。設計の grep 対象 (`.claude .agents .kaji docs CLAUDE.md README.md`) にも `tests/` は含まれないため、修正対象外と判断
- `WORKFLOW_SKILLS` に `i-pr` / `i-dev-final-check` を追加しない（**スコープ外**）。これらの新正本が parametrize に未掲載なのは旧来からの test 側の負債で、本リファクタ起因ではない

### 検証結果

```
=== refs (want 0) ===
0
=== dangling symlinks (want 0) ===
0
=== skill count (want 23) ===
23
=== gh-direct count (want 20) ===
20
=== .agents skill count (want 20, mirrors .claude/skills) ===
20
```

| 検証 | コマンド | 結果 |
|------|---------|------|
| 参照漏れ | `grep -rn "issue-pr\|issue-doc-check" .claude .agents .kaji docs CLAUDE.md README.md \| wc -l` | 0 ✅ |
| dangling symlink | `find .agents -xtype l \| wc -l` | 0 ✅ |
| Skill 数 | `find .claude/skills -mindepth 1 -maxdepth 1 -type d \| grep -v _shared \| wc -l` | 23 ✅ |
| gh 直叩き計数 | `grep -l "gh issue\|gh pr\|gh api" .claude/skills/*/SKILL.md \| wc -l` | 20 ✅ |
| `.agents/skills` 同期 | `find .agents/skills -mindepth 1 -maxdepth 1 \| grep -v _shared \| wc -l` | 20 ✅（.claude/skills と一致、互換 alias 分が除外されている） |
| pytest | `make check`（lint + format + typecheck + pytest） | 660 passed, 1 skipped ✅ |
| markdown link | `make verify-docs` | All valid (71 files checked) ✅ |

## 設計書のリスク表との照合

| リスク | 想定対策 | 実際 |
|-------|---------|------|
| `/issue-pr` の打鍵習慣 | skill not found エラーで止まる | 未検証（runtime 起動は今回行わず）。設計の通り次回の手動起動時に判明する |
| README 等への言及漏れ | grep で網羅確認 | grep 実施、対象範囲（README.md 含む）で 0 行を確認 |
| `.agents/skills/` symlink 削除漏れ | `find -xtype l` で検出 | 0 行を確認、dangling symlink なし |
| `local-mode` 設計の母数乖離 | 同 PR 内で更新 | 当該設計書の 3 箇所（L18, L56, L1278）を更新済み |

新規発見リスク: なし（テストハードコードは想定外だったが、影響範囲は明確で対応も完了）

## オープン論点（リファクタ完了時点での残課題）

### 1. local-mode design の KPI 計測手法見直し（設計書のオープン論点 1）

事前検証で確認した通り、`grep -l "gh issue\|gh pr\|gh api" .claude/skills/*/SKILL.md` は文字列マッチであり、**verdict 例の prose にも hit する**。

- 削除対象 `issue-pr/SKILL.md` は本物の `gh` 呼び出しではなく verdict サンプルテキストの "push と gh pr create が成功した" に hit していた
- このため削除後の補正値は 21 → 20（設計書通り暫定値で更新済み）
- ただし、**残る 20 件のうちにも prose hit が含まれている可能性**があり、真の `gh` 直叩き Skill 数は 20 未満の可能性がある

`draft/design/local-mode/design.md` には「Phase 2 着手前に計測手法を見直し真値を確定」を本リファクタ完了時点で注記として追記済み。**local-mode 着手前に別タスクとして棚卸しが必要**。

### 2. `tests/test_skill_harness_adaptation.py` の `WORKFLOW_SKILLS` の coverage 抜け

本リファクタで alias を削除した結果、`WORKFLOW_SKILLS` 配列には現状以下のみが含まれる:
```
issue-design, issue-review-design, issue-fix-design, issue-verify-design,
issue-implement, issue-review-code, issue-fix-code, issue-verify-code,
issue-close
```

しかし実 workflow YAML (`.kaji/wf/*.yaml`) からは `i-pr`, `i-dev-final-check` も呼ばれている。これらは **parametrize に未掲載で SKILL.md 形式テストの対象外**。

- これは旧来からの test 側の負債で、本リファクタの起因ではない
- 別 Issue として扱う（本リファクタのスコープ外、報告書のオープン論点として記録）

### 3. `tests/test_verdict_e2e.py:43` の歴史的コメント

`# This is the approximate output structure from the #73 issue-pr step` は Issue #73 の作業文脈を示す歴史的記述。設計の grep 対象に `tests/` が含まれないことと、過去経緯の保全のため、修正せず保持。レビュー時に異論があれば対応。

## コミット予定

設計書通り、以下の 1 コミットで `refactor/drop-skill-aliases` ブランチに積む予定（**ユーザー承認待ち、本報告作成時点では未コミット**）:

```
refactor!: drop deprecated skill aliases (issue-pr, issue-doc-check)

BREAKING CHANGE: /issue-pr and /issue-doc-check slash commands are removed;
use /i-pr and /i-dev-final-check instead.
```

含める変更:
1. `.claude/skills/issue-pr/`, `.claude/skills/issue-doc-check/` 削除
2. `.agents/skills/issue-pr`, `.agents/skills/issue-doc-check` symlink 削除
3. README.md / CLAUDE.md / docs の文言更新（7 ファイル、12 箇所）
4. `draft/design/local-mode/design.md` の母数更新（3 箇所）
5. `tests/test_skill_harness_adaptation.py` の parametrize list 更新（2 箇所）
6. 本設計書 `draft/design/drop-deprecated-skill-aliases/design.md`
7. 本作業報告 `draft/design/drop-deprecated-skill-aliases/work-report.md`

## 完了条件チェック（設計書より）

- [x] `.claude/skills/issue-pr/` 削除完了
- [x] `.claude/skills/issue-doc-check/` 削除完了
- [x] `.agents/skills/issue-pr` symlink 削除完了
- [x] `.agents/skills/issue-doc-check` symlink 削除完了
- [x] `find .agents -xtype l` が 0 行（dangling symlink がない）
- [x] README.md, CLAUDE.md, docs 配下の `/issue-pr` `/issue-doc-check` 言及がすべて正本名に置換済み
- [x] `docs/ARCHITECTURE.md` の Skill 数記述が 23 に更新済み
- [x] `draft/design/local-mode/design.md` の母数記述（Skill 数・`gh` 直叩き数）が新しい値に整合
- [x] `grep -rn "issue-pr\|issue-doc-check" .claude .agents .kaji docs CLAUDE.md README.md` が 0 行
- [x] `make check` 通過（660 passed, 1 skipped）
- [ ] commit message が `refactor!:` プレフィックス + `BREAKING CHANGE:` フッタを含む（**コミット時に履行予定、未実施**）

## 次のアクション

1. ユーザーが本報告を確認
2. 問題なければ commit を承認 → `refactor!: drop deprecated skill aliases (issue-pr, issue-doc-check)` で 1 コミット
3. GitHub 復旧後、本ブランチを push して PR 化（または local-mode 移行後は kaji local provider で merge）
4. オープン論点 1（KPI 計測手法）と 2（test parametrize 抜け）は別タスクとして起票
