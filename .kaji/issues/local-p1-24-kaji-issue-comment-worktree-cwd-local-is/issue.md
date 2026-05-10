---
id: local-p1-24
title: kaji issue comment が worktree cwd で local issue コメントを feature branch に misroute
  する
state: open
slug: kaji-issue-comment-worktree-cwd-local-is
labels:
- type:bug
- priority:p1
- area:harness
- area:cli
created_at: '2026-05-10T15:36:18Z'
---
## 概要

`kaji issue comment` を worktree (feature branch checkout) 内 cwd から実行すると、local issue (`local-*`) のコメントファイルが main ではなく **feature branch に commit され、main から見えなくなる**。CLAUDE.md の「`chore(local)` は main 直コミット」規約をツール側で守れていない。

## 目的

### Observed Behavior（OB）

worktree 内 cwd で `kaji issue comment local-XXX --commit` を実行すると:

- コメントファイル (`.kaji/issues/<slug>/comments/<ts>-pN.md`) が **cwd の git repo (= feature worktree)** に書き出される
- `chore(local): comment for local-XXX` の commit が **feature branch** に積まれる
- main worktree のファイルツリーには反映されず、main HEAD も進まない
- 結果、main で `kaji issue view local-XXX --comments` してもそのコメントは見えない

実例（`local-p1-23` で再現済み）:

```
# verify-code skill が worktree /home/aki/dev/kaji/kaji-fix-local-p1-23 内で実行
$ kaji issue comment local-p1-23 --commit --body-file ...
# → commit e286c13 が fix/local-p1-23 ブランチに積まれる
# → main 側の comments/ には 20260510T150532Z-p1.md が存在しない
# → 直後の /i-dev-final-check が「verify-code 未実施」と誤判定して BACK で停止
# → 復旧として c60ff6e で main に cherry-pick が必要だった
```

main 側コメント履歴の欠落（修正前）:

```
20260510T150200Z-p1.md  ← fix-code
(20260510T150532Z-p1.md が抜ける ← verify-code が feature branch に misroute)
20260510T150741Z-p1.md  ← final-check（前段証跡欠落で BACK 判定）
```

### Expected Behavior（EB）

local issue (`local-*`) のコメントは、cwd に関係なく **bare repo の primary worktree (= main worktree)** のファイルツリーに書き出され、**main ブランチ**に commit される。

根拠（1次情報）:
- `CLAUDE.md` § Git & GitHub「main 直コミット許容: `chore(local)`: kaji local Issue ファイル (`.kaji/issues/`) の追加・更新（`kaji issue create/edit/comment/close` の永続化）」
- 既存の他 skill (design / fix-code / final-check 等) のコメントは実際 main に積まれており、規約と一致した運用が成立している（今回の verify-code が逸脱）

GitHub-backed issue（数値 ID）は外部 API 呼び出しのため本問題と無関係（既存挙動維持）。

### 再現手順（Steps to Reproduce）

1. **前提**: bare repo + worktree 構成。main 上に `local-XXX` の local issue が存在。feature worktree（例: `kaji-fix-XXX`, branch `fix/XXX`）が checkout 済み。
2. **操作**: feature worktree に `cd` した状態で `kaji issue comment local-XXX --commit --body "test"` を実行。
3. **観測**:
   - feature worktree の `.kaji/issues/<slug>/comments/` に新規ファイルが作成される
   - `git -C <feature-worktree> log -1` に `chore(local): comment for local-XXX` が積まれる
   - `git -C <main-worktree> log -1` には反映されず、main の `comments/` にも該当ファイルなし

## 完了条件

- [ ] 設計書で根本原因が特定されている（cwd 依存で git repo を解決している箇所、および main worktree path の解決方法）
- [ ] `kaji issue comment local-XXX --commit` が worktree 内 cwd で実行されても、main worktree のファイルツリーにコメントが書き出され、main ブランチに commit される
- [ ] 再現テスト: worktree 内 cwd から `kaji issue comment` を実行 → main 側の `comments/` にファイルが追加され、main HEAD が進むことを assert（修正前は FAIL、修正後は PASS）
- [ ] GitHub-backed issue（数値 ID）のコメント挙動に変更がないことの回帰テスト
- [ ] 同根調査: `kaji issue create` / `kaji issue edit` / `kaji issue close` も同様の cwd 依存問題を抱えていないか調査し、設計書に結果を記載。問題があれば本 Issue で同時修正
- [ ] 関連 skill SKILL.md に「commit 先の cwd を意識する必要なし」を反映、または既存に冗長な指示があれば削除
- [ ] `make check` 通過

## 影響範囲（初期評価）

- 影響する CLI: `kaji issue comment`（local issue 経路）。同根の可能性ありとして `kaji issue create` / `edit` / `close` も要調査
- 影響する skill: worktree 内で動く全 skill（issue-design / issue-implement / issue-fix-code / issue-verify-code / issue-review-* など）
- 深刻度: **ワークフロー収束を破壊する** — final-check が前段判定の存在を確認できず BACK で停止する。人間の介入（本件のような cherry-pick 復旧）が必須となり、自動化されたワークフローが詰まる
- 回避策: skill 側で `cd <main-worktree>` してから `kaji issue comment` を呼ぶ（対症療法。スキルごとに同じ注意が必要で再発リスクが高い）

## 参考

- 再現 Issue: `local-p1-23`（glab --hostname flag incompat）
- misroute された commit: `e286c13` on `fix/local-p1-23` ブランチ（コメントファイル `20260510T150532Z-p1.md`）
- 復旧 commit: `c60ff6e` on main（cherry-pick）
- 規約根拠: `CLAUDE.md` § Git & GitHub / § Prohibitions
- 影響を受けた skill: `.claude/skills/issue-verify-code/SKILL.md` Step 3
- 関連スキル群: `.claude/skills/issue-{design,implement,fix-code,review-code,verify-design,verify-code}/SKILL.md`
