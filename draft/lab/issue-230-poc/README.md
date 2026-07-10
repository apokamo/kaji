# Issue #230 PoC evidence

Issue #230（`interactive_terminal` runner の terminal backend を kitty から tmux 単一へ置換）の
PoC・検討・実行証跡を保存するための lab 領域。

`defaft/lab` という依頼文の表記は、既存ディレクトリ構成に合わせて `draft/lab` として扱った。

## 目的

- #230 の設計判断に使った PoC 証跡を、`.kaji` / `.kaji-artifacts` の揮発的な場所だけに置かない。
- 実施した内容、検討中に出た矛盾、レビュー指摘、最終結果を失わない。
- raw evidence を残したうえで、後から読み返すための入口を用意する。

## 配置

| Path | 内容 |
|------|------|
| `raw/kaji-issues/` | PoC 用ローカル Issue 本文とコメント。local-p1-25 から local-p1-28 までを原文コピー |
| `raw/kaji-artifacts/230/` | #230 本番 workflow の run log、各 step attempt、verdict、prompt、result |
| `raw/design/issue-230-feat-harness-interactive-terminal-runner.md` | #230 の最終設計書コピー |

## PoC 一覧

| ID | 目的 | 主な結果 |
|----|------|----------|
| `local-p1-25` | 既存想定の interactive terminal runner で real Claude / Codex CLI が prompt を読み artifact verdict を書けるか検証 | 当初は kitty 前提かつ現行 worktree に runner/config が存在しない前提不整合が発覚。後続 PoC で対象を整理するきっかけになった |
| `local-p1-26` | 最小の足し算 CLI を題材に workflow を軽量検証 | PoC 用の小さな実装課題として、設計から実装までの検証対象を単純化した |
| `local-p1-27` | tmux lifecycle を手動 real-agent で確認する PoC02 | `kaji_addition_poc02.py` とテストを使い、real-agent workflow が小さな実装課題を完走できることを確認 |
| `local-p1-28` | clean な未実装状態から tmux lifecycle workflow を再検証する PoC03 | design -> implement -> review-code まで実施。Pre-Handoff Review 証跡不足による BACK と、その後の PASS も記録 |
| `230` | PoC 結果を反映した本番 Issue | tmux backend 置換を実装し、PR #231 で merge、Issue #230 close まで完了 |

## #230 に反映された主な判断

### 採用

- terminal backend は tmux 単一にする。
- `tmux split-window -d -h -P -F '#{pane_id}' -t "$TMUX_PANE"` で右 pane を作る。
- transcript は wrapper 側の `script(1)` ではなく runner 側の `tmux pipe-pane -o` で取る。
- cleanup は `/proc` scan ではなく `tmux kill-pane` に寄せる。
- `$TMUX` / `$TMUX_PANE` / `tmux >= 3.0` を fail-fast で検査する。
- `verdict.yaml` 出現を runner の終了トリガにする。
- `close_on_verdict=false` では `remain-on-exit on` と `kill-pane` 抑止を契約にする。
- `pane_dead` は verdict 検知時点の実値 snapshot として記録し、最終的な `[dead]` pane 状態とは分けて扱う。

### 不採用またはスコープ外

- kitty backend の維持。
- tmux / kitty の backend 選択式 config。
- `$TMUX` が無い場合の暗黙 fallback。
- `completion_barrier=agent_exit`、`post_verdict_timeout`、`agent-exited.json` など PoC 用の調査 scaffolding。
- wrapper 側での transcript 取得。
- pane 再利用。

## 重要な検討結果

`review-design` で、`close_on_verdict=false` 時に metadata へ `pane_dead=1` を必ず保存する要件は、
「終了トリガは `verdict.yaml` 出現のみで agent exit を待たない」という契約と矛盾すると指摘された。

最終設計では次のように分離して解消した。

- metadata: verdict 検知時点の `#{pane_dead}` 実値 snapshot。通常は `0`。
- 最終 pane 状態: `remain-on-exit on` により agent 自然終了後に `[dead]` として残る状態。runner 戻り時点では観測しない。

この判断は `raw/kaji-artifacts/230/progress.md`、`raw/kaji-artifacts/230/runs/2606070240/run.log`、
および `raw/design/issue-230-feat-harness-interactive-terminal-runner.md` に残っている。

## 本番 #230 の結果

- workflow は close まで完了。
- PR #231 を作成し、merge 済み。
- 実装後の `make check` は PASS。
- PR review の P2 指摘「pipe-pane 失敗時に verdict が既にある場合も失敗扱いになる race」に対応済み。
- 最終的に Issue #230 は completed として close。

## 読み方

1. まずこの README で全体像を掴む。
2. PoC の経緯を追う場合は `raw/kaji-issues/local-p1-25...28/issue.md` と `comments/` を読む。
3. #230 本番 workflow の時系列は `raw/kaji-artifacts/230/progress.md` と `raw/kaji-artifacts/230/runs/2606070240/run.log` を読む。
4. 最終設計の仕様は `raw/design/issue-230-feat-harness-interactive-terminal-runner.md` を読む。

## 関連 PoC

- kitty backend v1 とその前身 PoC: `draft/lab/kitty-interactive-terminal-poc/`
