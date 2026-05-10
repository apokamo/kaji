---
id: local-p1-22
title: step runner が claude の result event 後 stdout 閉鎖待ちで step timeout する
state: open
slug: stream-terminal-event-break
labels:
- type:bug
created_at: '2026-05-10T11:04:43Z'
---
> [!NOTE]
> **Worktree**: `../kaji-fix-local-p1-22`
> **Branch**: `fix/local-p1-22`

## 概要

`kaji_harness/cli.py:stream_and_log` が Claude Code (stream-json) の `result` イベント受信後も stdout EOF を待ち続けるため、claude セッションが正常完了していてもステップが `default_timeout`（既定 1800s）まで blocking する。adapter に terminal event 検知 IF を追加し、stream loop 側で受信時に break + `process.terminate()` させることで解消する（案 B）。

## 目的

### Observed Behavior（OB）

Issue `local-pc5090-21` の dev workflow を `kaji run` で実行中、`final-check` ステップが `Step 'final-check' timed out after 1800s` で終了した。一方、artifact の `stdout.log` 末尾の `result` イベントは Claude セッションが 7 分で正常完了していたことを示している。

artifact: `.kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/`

```
$ ls -la .kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/
-rw-r--r-- 1 aki aki   6743  5月 10 17:17 console.log
-rw-r--r-- 1 aki aki 290036  5月 10 17:42 stdout.log
```

`stdout.log` の最終 `result` イベント（要約）:

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "duration_ms": 418868,
  "terminal_reason": "completed",
  "stop_reason": "end_turn",
  "session_id": "8540bd90-dece-407c-8fc9-b2d8bf8a82ea"
}
```

時系列:

| 時刻 | イベント |
|---|---|
| 17:10 | claude サブプロセス起動（`final-check` ステップ開始） |
| 17:17 | `result` event 出力（`duration_ms: 418868` ≈ 7 分、`terminal_reason: completed`）。`console.log` 最終書き込み |
| 17:17〜17:42 | stdout が閉じない 25 分間（`for line in process.stdout` が blocking） |
| 17:42 | `_kill_process` timer 発火 → SIGTERM → fd 閉鎖 → `StepTimeoutError` |

console.log には以下の痕跡があり、claude が `run_in_background: true` で `make check` を起動して polling loop で完了を待ったセッションだったことが分かる:

```
[tool] Bash $ cd /home/aki/dev/kaji/kaji-feat-local-pc5090-21 && source .venv/bin/activate && …
[tool] ScheduleWakeup
[tool] Bash $ while kill -0 $(pgrep -f "make check" 2>/dev/null) 2>/dev/null; do sleep 5; done…
```

推定原因（kaji 視点で対処すべき部分）: `for line in process.stdout` (`kaji_harness/cli.py:159`) は EOF まで blocking する設計であり、Claude セッションの完了シグナルを能動的に検知しない。child bash が stdout fd を保持したままだと、claude メインセッションが完了していても EOF が返らず、kaji 側のタイマーまで離脱できない。

### Expected Behavior（EB）

stream-json で `result`（terminal event）を受信した時点で、`stream_and_log` は for loop を break し、`process.terminate()` を発行して短い grace period (例: 5s) 内に EOF を取りに行く。grace period 内に exit しなければ kill する。これにより:

- 健全なセッションは `result` 受信直後に kaji 側でステップ完了として扱える
- child process leak のような claude CLI 側の挙動に kaji が引きずられない
- 既存のタイムアウト機構は最終ガードとして温存される

根拠:

- `kaji_harness/adapters.py` には既に `extract_session_id` / `extract_text` / `extract_cost` という event 内容を adapter で抽出する責務分離があり、`is_terminal_event(event)` を追加するのは設計上自然な拡張
- Claude Code stream-json の契約上、`result` イベントは session 終了マーカーであり、その後に意味のあるイベントは流れない
- `kaji_harness/cli.py:117-138` の `_execute_cli_once` には既に `_kill_process` ベースの強制終了経路があるため、break 後の terminate は既存パターンに沿う

### 再現手順（Steps to Reproduce）

確実な再現には claude 側で stdout fd を leak する条件が必要だが、観測された artifact から固定再現は可能:

1. 前提: kaji main repo (`/home/aki/dev/kaji/main`) に `.kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/stdout.log` が存在
2. 単体テストレベル: `stream_and_log` に「`result` event を出した後 `process.stdout` を close せずに sleep する fake subprocess」を流し込み、現状実装が timeout まで blocking することを確認
3. 観測: 修正前は `_kill_process` 経由でしか終了しない / 修正後は `result` 受信直後に break して `process.terminate()` で正常終了する

実環境での再現条件（参考、強制再現は不要）:
- claude 側で `run_in_background: true` の Bash を起動し、`make check` のような長時間プロセスを polling で待つようなセッション
- 当該セッション終了時点で background bash の子孫プロセスが stdout fd を保持

## 完了条件

- [ ] 設計書で根本原因（stdout EOF を待つ blocking read）と修正方針（terminal event 検知 → break → terminate）が特定されている
- [ ] `CLIEventAdapter` に `is_terminal_event(event: dict[str, Any]) -> bool` を追加し、Claude / Codex / Gemini 各 adapter で適切なイベント種別（claude: `type == "result"` 等）を判定している
- [ ] `stream_and_log` が terminal event 観測後に for loop を break し、`process.terminate()` → 短い grace period の `wait(timeout=...)` → 必要なら `kill()` の順で後始末する
- [ ] 再現テストが 1 本以上追加され、修正前は FAIL（`StepTimeoutError` または timeout）、修正後は PASS することを確認
- [ ] 既存の正常終了経路（subprocess が自発的に EOF を返すケース）でも従来と等価な挙動になることをテストで確認（regression なし）
- [ ] 既存の timeout（`step.timeout` / `default_timeout`）経路はそのまま残り、terminal event を見ない adapter（あるいは未対応 event）でも従来通り timeout で守られている
- [ ] `make check` 通過
- [ ] 影響モジュール（`tests/test_cli.py` 等の cli / adapter 関連テスト）が green

## 影響範囲（初期評価）

- 主修正: `kaji_harness/cli.py`（`stream_and_log` の break 条件と後始末）
- 主修正: `kaji_harness/adapters.py`（`CLIEventAdapter` ABC + `ClaudeAdapter` / `CodexAdapter` / `GeminiAdapter` の `is_terminal_event` 実装）
- テスト: `tests/` 配下の cli / adapters 関連、新規 terminal event テスト
- 影響するコマンド: `kaji run`（全 workflow 全 step）
- 深刻度: 軽微〜中（成果物には影響しないが、stdout fd を保持する子孫プロセスが発生したセッションごとに `default_timeout` 分の wall clock を浪費。今回は 1 step で 25 分 / 1 セッション）
- 回避策: ステップ単位で `step.timeout` を短く設定すれば最大ロスは抑制できるが、根本解決ではなく、健全な長時間ステップを誤って打ち切るリスクと表裏

## スコープ外

- claude CLI 側の background bash による stdout fd leak バグ修正（kaji の責務外、上流で別途対処）
- timeout 値の調整（症状ではなく原因に対処する Issue のため）
- Codex / Gemini で同種 leak が発生した場合の adapter 実装の網羅検証（terminal event 検知 IF を入れること自体は本 Issue で扱い、各 adapter の event 種別調査は実装フェーズで行う）

## 参考

- 観測 artifact: `/home/aki/dev/kaji/main/.kaji-artifacts/local-pc5090-21/runs/2605101619/final-check/{console.log,stdout.log}`
- 関連実装:
  - `kaji_harness/cli.py:117-138`（`_execute_cli_once`）
  - `kaji_harness/cli.py:141-216`（`stream_and_log`）
  - `kaji_harness/cli.py:219-226`（`_kill_process`）
  - `kaji_harness/adapters.py:1-176`（`CLIEventAdapter` と各 adapter 登録）
- 関連 Issue: `local-pc5090-21`（本問題に遭遇した dev workflow 実行）
- Claude Code stream-json 仕様: `result` event が `terminal_reason` を持つ session 終了マーカーである旨は artifact 側の event ペイロードで確認済み