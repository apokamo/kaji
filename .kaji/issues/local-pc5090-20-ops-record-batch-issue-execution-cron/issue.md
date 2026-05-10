---
id: local-pc5090-20
title: 'ops: 連続イシュー実行予約 (2026-05-10) — CronCreate + workflow 自動化の知見記録'
state: closed
slug: ops-record-batch-issue-execution-cron
labels:
- type:docs
created_at: '2026-05-10T05:11:16Z'
closed_at: '2026-05-10T05:11:25Z'
closed_by: pc5090
close_reason: completed
---
> [!NOTE]
> **記録 issue**: 2026-05-10 に実施した「6 完了後に 7→8→9→10 を CronCreate で 90 分後自動起動」実験の作業ログと知見。即クローズ。

## 背景 / 目的

- 利用者が `kaji run` 連続実行を非対話で予約したい（ユースケース: 6 を人間で完了 → 90 分後に 7-10 を自動連続実行 → 翌朝結果確認）
- 「Claude Code のスケジューリング機構で何ができるか」「workflow 自動連続実行のハマりどころ」を実証

## 実行サマリ

| 段階 | 結果 |
|---|---|
| Cron 予約 (02:40 JST one-shot) | 成功（job ID `815354f3` / durable=false / session-only） |
| local-pc5090-6 sanity check | PASS（state=closed 確認済み） |
| local-pc5090-7 (feature) | ⚠️ 実装 PASS → close で **構造的 abort** → skill 修正後再実行で完走 |
| local-pc5090-8 (feature) | ✅ 完走 |
| local-pc5090-9 (docs-only) | ✅ 完走（docs-maintenance-local workflow） |
| local-pc5090-10 (test) | ✅ 完走 |

最終状態: main HEAD = `d190464`、すべての feature worktree / branch は削除済み。push のみ remote 復旧待ち（GitHub account suspended）。

## 採用した予約機構

### CronCreate one-shot vs ScheduleWakeup (/loop)

| 機構 | 最大遅延 | 用途 |
|---|---|---|
| ScheduleWakeup (`/loop`) | **3600s = 60 分** | 短時間ポーリング、cache 内（300s 未満が望ましい） |
| CronCreate one-shot | 任意の絶対時刻 | 1 時間超の予約 |

90 分予約は ScheduleWakeup の上限超過のため CronCreate を採用。durable オプション:
- `durable=false` (今回採用): セッション in-memory、Claude セッションが閉じると消失
- `durable=true`: `.claude/scheduled_tasks.json` に永続化、別セッション復帰時にも fire

**今回 durable=false を選んだ理由**: ユーザがセッションを開いたまま待機する判断。セッション運用の柔軟性を優先。

### Cron 式の組み立て

```
40 2 10 5 *
↑  ↑ ↑  ↑ ↑
|  | |  | └─ DoW (any)
|  | |  └─── Month (May)
|  | └────── Day-of-Month (10)
|  └──────── Hour (2)
└─────────── Minute (40)
```

`recurring=false` で 1 回 fire 後 auto-delete。`:00` / `:30` を避ける推奨は守った（`:40`）。

## 構造的失敗 1: Bash exit chain による誤報

### 症状

`run_in_background` で投げた `kaji run` が `task-notification` で `exit code 0` と通知されたが、実体は exit 1 だった。

### 原因

```bash
uv run kaji run ... 2>&1; echo "EXIT_CODE=$?"
```

`;` 連結のため最終 exit code は `echo` の 0。task-notification は最終 exit のみ参照するため誤報。

### 検出

`tail` で出力末尾を確認したところ `EXIT_CODE=1` と `Workflow aborted` が記録されていた。

### 教訓

**自動化スクリプトで実 exit code を捕捉する場合は以下のいずれかを使う**:

```bash
# パターン A: status をファイルに保存
uv run kaji run ... 2>&1; echo $? > /tmp/kaji-exit
# パターン B: && / || で chain（成功時のみ後続実行）
uv run kaji run ... 2>&1 && echo "PASS"
# パターン C: pipefail を使う（pipe している場合）
set -o pipefail; uv run kaji run ... 2>&1 | tee log
# パターン D: 単純に echo を付けない（task-notification は本来の exit を返す）
uv run kaji run ...
```

今回の 8/9/10 では **パターン D**（echo を付けない）を採用し、誤報なく完走した。

## 構造的失敗 2: issue-close skill のオフライン非対応

### 症状

local-pc5090-7 の `issue-close` skill (provider=local) で `git fetch origin main` が exit 128 (GitHub account suspended)。skill には fetch 失敗時の fallback が未定義 → Claude が `AskUserQuestion` で「local-only 継続 / abort」を確認しようとした → `kaji run` 非対話モードで `AskUserQuestion` が発火不可 → workflow exit 1。

### 原因の本質

**skill 設計が `kaji run` 非対話モードを十分考慮していなかった**。skill は本来 deterministic に動作すべきだが、remote 障害時の分岐が AskUserQuestion 経由に倒れていた。

### 修正 (commit `14dc29e`)

`.claude/skills/issue-close/SKILL.md` Step 2 (provider=local):

```bash
# Before
if git remote get-url origin >/dev/null 2>&1; then
    git fetch origin [default_branch]
    git merge --ff-only "origin/[default_branch]" || { echo "ABORT: ..."; exit 1; }
fi

# After
if git remote get-url origin >/dev/null 2>&1; then
    if git fetch origin [default_branch] 2>&1; then
        git merge --ff-only "origin/[default_branch]" || { echo "ABORT: ff-only merge failed in base worktree"; exit 1; }
    else
        echo "WARNING: git fetch origin [default_branch] failed; proceeding with local-only close (manual push needed after remote recovery)"
    fi
fi
```

Step 6 の push と同じ「WARNING で skip + local 完結」パターンに揃えた。ABORT 条件は ff-only merge 失敗 / dirty file 残存に限定。

### 教訓

**`kaji run` で実行される skill は AskUserQuestion 経由の対話に依存しないこと**。fallback 経路は WARNING / ABORT のいずれかで deterministic に決定する。

### 関連未対応箇所

- `.claude/skills/issue-close/SKILL.md` Step 4.5 (provider=github/gitlab) も `git fetch origin` を実行している。GitHub 復旧見込みなしの方針下では provider=github を当面使わないため未着手。gitlab 経路で同等問題が出る場合は同パターンで修正可能。

## 構造的事象 3: コメントファイル連番衝突（再現性あり）

### 症状

7/8/9/10 の close 時、3 件すべてで `kaji run` 中の `git merge --no-ff feat/local-pc5090-N` がコメントファイル連番（`.kaji/issues/.../comments/000N-*.md`）の add/add 衝突を起こした。

| Issue | 衝突連番 | リネーム後 |
|---|---|---|
| 8 | 0004-0007 | 0009-0012 |
| 9 | 0004-0005 | 0007-0008 |
| 10 | 0002-0004 | 0008-0010 |

### 原因（推定 / 静的調査ベース）

LocalProvider の comment seq 採番は worktree-local の最大値 +1。並行コミット（feature 側で `kaji issue comment` を打ちながら、main 側でも別 issue 関連のコメントが追加される）下では seq が独立進行し、merge 時に同じ番号で別ファイルが追加される race。

### 暫定対応（close skill 内で都度実施）

衝突発生 → feature 側コメントを最大値以降にリネームコミット → 再マージ。close skill が手動で対処していた。

### 改善候補（次の検討対象、本 issue では未着手）

1. LocalProvider seq 採番を「ベース main の最大値 + feature 内 commit 数」にする
2. seq 衝突時の自動リネームを skill 化（現状は claude が都度判断している）
3. seq を timestamp/UUID ベースに変更（互換性影響大）

優先度: 連続実行予約をルーチン化するなら 2 の skill 化が最低限欲しい（claude の判断ばらつきを除去できる）。

## CronCreate prompt 設計の知見

### 自己完結性の重要性

cron fire 時、prompt は**コンテキスト無しの fresh state**で実行される。今回の prompt が機能した理由:

- 実行手順を明示（`/issue-start` → `kaji run`）
- 4 issue それぞれに該当 workflow を明記（特に 9 が docs-only である点）
- ガードレール明記（main 直コミット禁止 / pre-commit checks スキップ禁止）
- エラー時の挙動を明示（中断 / 静的調査のみ / 後続 skip）
- 完了時のサマリ形式を指定

### Sanity check 設計

「6 完了待ち」を最初は最大 60 分ポーリングで設計したが、ユーザが事前に 6 完了を確認していたため `state: closed` の単発確認に簡略化した。一般化すると:

- **完了が事前確認されている** → 単発 sanity check で十分
- **完了タイミングが不確実** → ポーリング（cron 起床後、別の遅延実行で再起動するパターン）

## 派生コミット

| commit | 種別 | 経緯 |
|---|---|---|
| `14dc29e` | fix(skill) | issue-close オフライン fallback |
| `125935c` | chore | `.gitignore` に `.claude/*.lock` 追加（issue-7 close 中の派生対応） |
| `bb8920d` | chore(local) | local-pc5090-19 記録 issue（skill 修正の audit trail） |

## 次回連続実行予約のためのチェックリスト

- [ ] 予約時刻の確定（CronCreate / ScheduleWakeup どちらが適切か判断）
- [ ] durable の選択（セッション維持できる前提か / 別セッション復帰前提か）
- [ ] 実行前提の sanity check 設計（前段 issue の state 確認等）
- [ ] 各 issue の workflow 確認（type:feature / type:docs / type:test の使い分け）
- [ ] Bash exit code 捕捉の正しい方法（`; echo` を使わない）
- [ ] 中断条件と継続条件の明示（exit code != 0 で全停止 / 静的調査のみ等）
- [ ] サマリ出力フォーマットの指定（最終結果のみ報告等）

## 関連 issue / commit

- local-pc5090-19: skill 修正の audit trail
- commit `14dc29e`: skill 修正本体
- 本 issue: 連続実行予約全体の知見記録

