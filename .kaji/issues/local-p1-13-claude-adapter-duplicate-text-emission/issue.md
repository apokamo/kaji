---
id: local-p1-13
title: kaji run の Claude ステップで最終アシスタントメッセージが verbose 出力に 2 回現れる
state: closed
slug: claude-adapter-duplicate-text-emission
labels:
- type:bug
- duplicate
created_at: '2026-05-09T07:19:24Z'
closed_at: '2026-05-09T07:28:24Z'
closed_by: pc5090
close_reason: duplicate of local-p1-14
---
> [!NOTE]
> **同時対応**: local-p1-14 の worktree (`../kaji-feat-local-p1-14` / `feat/local-p1-14`) で対応

## 概要

`kaji run` で `agent: claude` を使う step（design / fix-design / implement / fix-code 等）の verbose 出力において、最終アシスタントメッセージがターミナル・`console.log`・`full_output` の全てに同一内容で連続 2 回現れる。

## 目的

### Observed Behavior（OB）

`uv run kaji run .kaji/wf/feature-development-local.yaml local-p1-5` 実行時、`[design]` step の最終出力 "## 設計書作成完了 ... ---END_VERDICT---" ブロックがそのまま 2 回表示される。

```
[2026-05-09T16:10:00] [design] ## 設計書作成完了
... (約 30 行の同一ブロック) ...
---END_VERDICT---
[2026-05-09T16:10:00] [design] ## 設計書作成完了
... (まったく同じ約 30 行) ...
---END_VERDICT---
```

実体ログ `.kaji-artifacts/local-p1-5/runs/2605091602/design/stdout.log` 末尾の生 JSONL を確認したところ、最後の `assistant` イベントと、その直後の `result` イベント（`result` フィールド）に同一テキストが格納されていた。

```
$ tail -n 5 .kaji-artifacts/local-p1-5/runs/2605091602/design/stdout.log | python3 -c "..."
user |
assistant |
user |
assistant | ## 設計書作成完了 ...
result    | ## 設計書作成完了 ...
```

### Expected Behavior（EB）

最終アシスタントメッセージはターミナル / `console.log` / `full_output` のいずれにおいても 1 回だけ現れる。コスト情報（`total_cost_usd`）は引き続き `result` イベントから取得できる。

根拠:
- CodexAdapter / GeminiAdapter は単一イベント源（`item.completed` / `message`）からのみテキストを抽出しており、Claude Adapter のみが二重抽出する非対称設計になっている（`kaji_harness/adapters.py:55-71`, `:98-102`）。
- Claude Code stream-json の仕様では、`result` イベントの `result` フィールドは最終 assistant メッセージの再掲であり、純粋な追加情報ではない。

### 再現手順（Steps to Reproduce）

1. 前提: `agent: claude` を含む workflow（例: `.kaji/wf/feature-development-local.yaml` の `design` step）と着手済み Issue（例: `local-p1-5`、worktree 作成済み）。
2. `uv run kaji run .kaji/wf/feature-development-local.yaml local-p1-5 --step design`（または `--from design`）を実行。
3. ターミナル出力に "## 設計書作成完了" ブロックが連続 2 回表示される。同様に `.kaji-artifacts/<issue>/runs/<run_id>/design/console.log` にも同一テキストが 2 ブロック書き込まれる。

## 完了条件

- [ ] 設計書で根本原因が特定されている（`ClaudeAdapter.extract_text` が `assistant` と `result` の両方からテキストを返す実装の妥当性を検討した上で、修正方針を決定）
- [ ] 再現テストが 1 本以上追加され、修正前は FAIL、修正後は PASS する（`assistant` イベント + `result` イベントの両方を含む JSONL ストリームに対して、`full_output` 内の最終テキストが 1 回しか現れないことを assert）
- [ ] 既存テスト `tests/test_adapters.py::test_extract_text_from_result_event` および `tests/test_cli_streaming_integration.py::test_claude_streaming_extracts_session_and_text`（"Done" の `full_output` 含有 assert）の期待値見直しが完了
- [ ] `total_cost_usd` の取得が `result` イベント経由で従来どおり機能していることを既存テストまたは追加テストで確認
- [ ] 同根の他の壊れ箇所の調査結果が設計書に記載されている（CodexAdapter / GeminiAdapter で同様の二重抽出が無いことの確認結果含む）
- [ ] `make check` 通過

## 影響範囲（初期評価）

- 影響するモジュール / コマンド:
  - `kaji_harness/adapters.py`（`ClaudeAdapter.extract_text`）
  - `kaji_harness/cli.py`（`stream_and_log` の出力経路）
  - 影響を受ける workflow step: `agent: claude` の全 step（design / fix-design / implement / fix-code / 全ての claude 駆動 skill）
- 深刻度: 表示の乱れ（軽微）。ただし `full_output` がそのまま verdict parse 入力となるため、verdict ブロックが 2 回出現することで 3-stage fallback parser の挙動を分かりにくくする副作用、および console.log を AI レビュー材料にする運用での誤読リスクあり。
- 回避策の有無: なし（verbose=False でターミナル出力は抑止できるが、`console.log` と `full_output` の二重化は残る）。

## 参考

- 関連コード:
  - `kaji_harness/adapters.py:29-37`（`ClaudeAdapter.extract_text` — `assistant` と `result` の両分岐）
  - `kaji_harness/adapters.py:55-71`（`CodexAdapter.extract_text` — 単一イベント源）
  - `kaji_harness/adapters.py:98-102`（`GeminiAdapter.extract_text` — 単一イベント源）
  - `kaji_harness/cli.py:181-187`（`stream_and_log` が extract_text の戻り値を `texts.append` + `print`）
- 既存テスト:
  - `tests/test_adapters.py:65-68` `test_extract_text_from_result_event`（`result` イベントから text が返ることを期待する既存 assert）
  - `tests/test_cli_streaming_integration.py:48,63` (`"Done" in result.full_output` を assert している既存テスト)
- 1 次情報: `.kaji-artifacts/local-p1-5/runs/2605091602/design/stdout.log` 末尾の生 JSONL 内に同一テキストの `assistant` と `result` イベントが連続して存在することを確認済み。
- 関連ドキュメント: `docs/dev/workflow_overview.md`（workflow 実行の前提）。