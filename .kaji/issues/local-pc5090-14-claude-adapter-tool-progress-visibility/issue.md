---
id: local-pc5090-14
title: kaji run の Claude ステップで進捗が見えず最終メッセージが二重表示される（ClaudeAdapter.extract_text 改修）
state: open
slug: claude-adapter-tool-progress-visibility
labels:
- type:bug
created_at: '2026-05-09T07:22:52Z'
---
> [!NOTE]
> **Worktree**: `../kaji-feat-local-pc5090-14`
> **Branch**: `feat/local-pc5090-14`
> **同時対応**: local-pc5090-13

## 概要

`kaji run` で `agent: claude` の step（design / fix-design / implement / fix-code 等）を実行すると `kaji_harness/adapters.py:29-37` `ClaudeAdapter.extract_text` の現実装に起因する 2 つの観測異常が同時に発生する。両方とも同一関数の責務（`assistant` content / `result` event のうちどれを extract するか）の見直しで解消するため、1 つのスコープに統合して扱う。

- **異常 A（ツール呼び出しの不可視化）**: ツール（Bash / Read / Edit / Write / Grep / TodoWrite / Skill / ToolSearch 等）の連続実行中、ターミナル出力が完全に止まる。最終 assistant text までの数十秒〜数分間、進捗が一切見えない。
- **異常 B（最終メッセージの二重表示）**: 最終アシスタントメッセージがターミナル / `console.log` / `full_output` の全てに同一内容で連続 2 回現れる。

## 目的

### Observed Behavior（OB）

#### 異常 A: ツール呼び出しの不可視化

`uv run kaji run .kaji/wf/feature-development-local.yaml local-pc5090-5` の `[design]` step は 16:08:47 〜 16:10:00 の 73 秒間走ったが、ターミナルには次の 2 行しか出力されなかった:

```
[2026-05-09T16:08:47] [design] 設計書ができたのでコミットして Issue にコメント。
[2026-05-09T16:10:00] [design] ## 設計書作成完了
```

実体ログ `.kaji-artifacts/local-pc5090-5/runs/2605091602/design/stdout.log`（580 KB）の生 JSONL を集計したところ、その 73 秒間に大量のツール呼び出しが発生していた:

| イベント / コンテンツ種別 | 件数 |
|---|---|
| `assistant` イベント | 58 |
| ├ `tool_use` ブロック | 50（Read 24 / Bash 17 / TodoWrite 5 / Skill 1 / ToolSearch 1 / Grep 1 / Write 1）|
| ├ `thinking` ブロック | 6（全て `thinking` フィールド長 0、Extended Thinking redacted 形式）|
| └ `text` ブロック | 2（初手 + 最終 verdict のみ）|

50 件の `tool_use` と 6 件の `thinking` はターミナルに 1 行も現れない。

#### 異常 B: 最終メッセージの二重表示

同一 step の verbose 出力末尾で、"## 設計書作成完了 ... ---END_VERDICT---" ブロックが完全に同一の内容で連続 2 回 print された:

```
[2026-05-09T16:10:00] [design] ## 設計書作成完了
... (約 30 行の同一ブロック) ...
---END_VERDICT---
[2026-05-09T16:10:00] [design] ## 設計書作成完了
... (まったく同じ約 30 行) ...
---END_VERDICT---
```

同じ stdout.log 末尾の生 JSONL を検査すると、最後の `assistant` イベントと、その直後の `result` イベント（`result` フィールド）に同一テキストが格納されている:

```
$ tail -n 5 stdout.log | python3 -c "import sys,json; [print(json.loads(l).get('type'), '|', ...) for l in sys.stdin]"
assistant | ## 設計書作成完了 ...
result    | ## 設計書作成完了 ...
```

### Expected Behavior（EB）

#### 異常 A について

Claude を直接 CLI 実行した場合と同様、ツール呼び出しがターミナルに逐次表示され、進捗が分かる。最低でも:
- ツール名（Bash / Read / Edit / Write / Grep / TodoWrite / Skill / ToolSearch 等）が逐次表示される
- ツールごとの主要入力（Bash → command の先頭、Read/Edit/Write → file_path、Grep → pattern、Skill → skill name、ToolSearch → query 等）が要約として 1 行に表示される

#### 異常 B について

最終アシスタントメッセージはターミナル / `console.log` / `full_output` のいずれにおいても 1 回だけ現れる。コスト情報（`total_cost_usd`）は引き続き `result` イベントから取得できる。

#### 共通の根拠

- `kaji_harness/adapters.py:29-37` `ClaudeAdapter.extract_text` の現実装:
  - `assistant` イベントの content から `type == "text"` のブロックしか抽出していない（→ tool_use / thinking が捨てられて異常 A 発生）
  - 加えて `result` イベントの `result` フィールドからもテキストを抽出している（→ 最終 assistant text と二重抽出されて異常 B 発生）
- `kaji_harness/adapters.py:55-71` `CodexAdapter.extract_text` は対比設計として:
  - `agent_message` / `reasoning` / `mcp_tool_call` を抽出（reasoning も含むため Codex 駆動 step は進捗が見える）
  - `result` 相当の `turn.completed` から **テキストは抽出していない**（コストのみ）
- `kaji_harness/adapters.py:98-102` `GeminiAdapter.extract_text` も `message` イベントのみを抽出し `result` イベントからはコストだけ取る対称設計
- すなわち Claude Adapter のみが (1) tool_use を捨てる、(2) result からも text を取る、という二重の非対称性を持つ

### 再現手順（Steps to Reproduce）

1. 前提: `agent: claude` を含む workflow（例: `.kaji/wf/feature-development-local.yaml` の `design` step）と着手済み Issue（例: `local-pc5090-5`、worktree 作成済み）。
2. `uv run kaji run .kaji/wf/feature-development-local.yaml local-pc5090-5 --step design`（または `--from design`）を実行。
3. 異常 A: 初回の assistant text 出力後、最終 assistant text 出力までの間、ターミナル出力が完全に停止する。`.kaji-artifacts/<issue>/runs/<run_id>/design/stdout.log` に多数の `tool_use` イベントが記録されていることを生 JSONL で確認できる。
4. 異常 B: ターミナル出力末尾（および `console.log` 末尾）に最終 assistant メッセージが連続 2 回現れる。同 stdout.log 末尾に同一テキストの `assistant` / `result` イベントが連続して存在することを確認できる。

## 完了条件

### 設計段階で確認

- [ ] `ClaudeAdapter.extract_text` で抽出する `assistant` content type の決定（最低でも `text` + `tool_use` を扱う方針が文書化されている）
- [ ] `tool_use` の表示形式（ツール名 + 主要入力の選び方 + 切り詰め長）が決定されている
- [ ] `thinking` ブロックの扱い（redacted 時の表示有無、内容ありの場合の表示形式）が決定されている
- [ ] `result` イベントからの text 抽出を廃止する方針が、コスト抽出 (`extract_cost`) との責務分離とともに文書化されている（`extract_cost` は `result` を引き続き処理）
- [ ] `full_output` に tool_use 表示行が混入することによる verdict parser への影響評価が記載され、必要なら parser 側の防御策が決定されている（例: `[tool] ` プレフィックスを VERDICT 行と誤認しないこと）
- [ ] CodexAdapter / GeminiAdapter で同種の進捗欠落・重複抽出が無いことの調査結果が設計書に記載されている

### 実装段階で確認

- [ ] 異常 A の再現テスト追加: `tool_use` を含む Claude JSONL ストリームに対し `full_output` / `console.log` にツール呼び出し情報が含まれることを assert
- [ ] 異常 B の再現テスト追加: `assistant` 直後の `result` イベントを含む JSONL ストリームに対し `full_output` 内の最終テキストが 1 回しか現れないことを assert
- [ ] 既存テストの期待値見直し:
  - `tests/test_adapters.py::test_extract_text_from_result_event`（result event から text が返る前提を反転または削除）
  - `tests/test_cli_streaming_integration.py::test_claude_streaming_extracts_session_and_text`（`"Done" in result.full_output` を反転）
- [ ] `total_cost_usd` の取得が `result` イベント経由で従来どおり機能することを既存テストまたは追加テストで確認
- [ ] `make check` 通過
- [ ] 73 秒の実例 step を再実行し、(1) ツール呼び出しが連続表示され、(2) 最終メッセージが 1 回しか出ないことを目視確認

## 影響範囲（初期評価）

- 影響するモジュール / コマンド:
  - `kaji_harness/adapters.py`（`ClaudeAdapter.extract_text`、`tool_use` レンダリング用ヘルパ）
  - 影響を受ける workflow step: `agent: claude` の全 step（design / fix-design / implement / fix-code / その他 claude 駆動 skill）
  - 副次的に verdict parser（`kaji_harness/verdict.py` 配下）への影響評価
- 深刻度: medium
  - 異常 A は UX 大（長時間沈黙はハング・エラーとの区別がつかず、Ctrl-C の判断材料を奪う）
  - 異常 B は表示の乱れ + verdict parser 入力（`full_output`）の重複による誤読リスク
- 回避策の有無: なし（生 JSONL の `stdout.log` を別端末で `tail -f` してパースすれば見える、という非現実的な回避策のみ）

## 参考

- 統合された Issue:
  - **local-pc5090-13**（duplicate close 済み）— 異常 B（最終メッセージ二重表示）単独。同一関数 `ClaudeAdapter.extract_text` を触るため当 Issue に統合した
- 関連コード:
  - `kaji_harness/adapters.py:29-37` `ClaudeAdapter.extract_text`（修正対象）
  - `kaji_harness/adapters.py:55-71` `CodexAdapter.extract_text`（対比対象）
  - `kaji_harness/adapters.py:98-102` `GeminiAdapter.extract_text`（対比対象）
  - `kaji_harness/cli.py:141-216` `stream_and_log`（adapter 戻り値の使われ方）
- 1 次情報（観測ログ）:
  - `.kaji-artifacts/local-pc5090-5/runs/2605091602/design/stdout.log`（580 KB, 73 秒分の生 JSONL）
  - assistant イベント分布: tool_use 50 / thinking 6 / text 2
  - 末尾に同一テキストの assistant / result イベントが連続して存在
- 関連ドキュメント:
  - `docs/dev/workflow_overview.md`（workflow 実行の前提）
  - 設計書配置予定: `draft/design/issue-local-pc5090-14-claude-adapter-tool-progress.md`