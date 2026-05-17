# Headless Terminal Spawn — 設計書

> **Status**: Draft (2026-05-14)
> **Goal**: kaji ハーネスから別ターミナルウィンドウを spawn し、その中で対話モードの `claude` / `codex` を実行 → 結果を sentinel + 出力ファイル経由で回収する PoC の検証
> **Motivation**: 現状の `claude -p`（ヘッドレス）は別料金 plan 扱い。対話モード起動なら既存 plan の範囲。ハーネスから対話 CLI を制御する方式を検証する

---

## 背景と前提

- 実行環境: WSL2（Ubuntu）+ Windows Terminal (`wt.exe`)
- サンドボックス: WSL2 自体が外部隔離層なので、`--dangerously-skip-permissions` 系を安全に使用可能
- 既存配置: kaji ハーネスは `kaji run` から workflow 経由で agent を起動 — 現状は `claude -p` を子プロセス化
- 対象 agent: `claude`（Claude Code CLI）と `codex`（Codex CLI）

## 課題

| # | 課題 | 解決方針 |
|---|---|---|
| C1 | 対話モードは stdout / exit code を直接奪えない | 出力をファイル化、ステータスを sentinel ファイルで表現する |
| C2 | 別ウィンドウのプロセスがいつ終わったか分からない | エージェントの shell tool に `touch sentinel` を実行させる |
| C3 | permission prompt で停止する | `--dangerously-skip-permissions` (claude) / `--dangerously-bypass-approvals-and-sandbox` (codex)。WSL2 サンドボックス前提 |
| C4 | 対話モードはユーザ入力待ちで自動終了しない | PoC では端末を開いたままにする。本実装では sentinel 後に `wt.exe` プロセス group を kill する |

## アーキテクチャ

```
┌─────────────────────────┐         ┌────────────────────────────┐
│   kaji harness (driver) │         │   spawned terminal (wt.exe)│
│                         │         │                            │
│  1. run_dir 作成        │ spawn   │   wsl bash -c 'wrapper.sh' │
│  2. prompt.txt 書き出し ├────────►│      └─ claude/codex ...   │
│  3. wt.exe new-window   │         │            └─ shell tool   │
│  4. sentinel を watch   │         │               └─ write     │
│  5. 出力読み込み        │◄────────┤                  output.txt│
│                         │ sentinel│                  sentinel  │
└─────────────────────────┘         └────────────────────────────┘
```

### run_dir レイアウト

```
/tmp/kaji-spawn/<run_id>/
├── prompt.txt        # driver が書き込む。本来のプロンプト + 末尾に完了指示
├── output.txt        # agent が shell tool 経由で書き込む（最終回答）
├── sentinel          # agent が最後に touch する。driver はこの出現を watch
├── status.json       # 完了情報（exit_code, duration, agent kind）
└── transcript.log    # wrapper.sh が `script` コマンドで保存する端末ログ（参考用）
```

### プロンプト末尾に付与する完了指示テンプレート

```
---
このタスクが完了したら、以下を **必ず** 実行してください:
1. あなたの最終回答（plain text）を `/tmp/kaji-spawn/<run_id>/output.txt` に書き込む
2. `/tmp/kaji-spawn/<run_id>/sentinel` を `touch` する
```

`claude` も `codex` も Bash / shell tool を持つので、この instruction で sentinel 制御が可能。

### Spawn コマンド（WSL2）

第一候補は Windows Terminal:

```bash
wt.exe -w new new-tab --title "kaji-spawn-<run_id>" \
    wsl.exe bash -lc "/path/to/wrapper.sh <run_dir> <agent>"
```

`new-window` は subcommand ではない（よくある誤解）。新規ウィンドウは `-w new` flag + `new-tab` subcommand で指定する。詳細は results.md 追補4 を参照。

claude code セッション内では sandbox 起因（推定）で window が開けないため、WSLg + Linux X terminal（xfce4-terminal 推奨 / mlterm 軽量代替）も実装する:

```bash
zutty -font DejaVuSansMono -title "kaji-spawn-<run_id>" \
    -e bash -lc "/path/to/wrapper.sh <run_dir> <agent>"
```

driver は `--terminal {wt,zutty}` で選択可能にする。通常 WSL シェルからは wt、claude code 等のサンドボックス内では zutty を使う。

- `new-window` は既存 wt セッションがあれば新規ウィンドウを開く（無ければ起動して開く）
- `wsl.exe bash -lc` でログインシェル経由 — PATH に `claude` / `codex` が乗る前提

### wrapper.sh の役割

1. `cd` to run_dir
2. trap で異常終了時にも sentinel + status を残す
3. `script -q -c "<agent invocation>" transcript.log` で端末ログを保存（任意）
4. agent コマンドを exec
5. agent が exit したら status.json を更新

最小実装は `script` を省き、agent を直接 exec する。

### Driver の役割

1. `run_id` 発行（UUID）
2. run_dir 作成、prompt.txt 書き出し（本体 + 完了指示 template の連結）
3. `wt.exe new-window ...` を `subprocess.Popen` で spawn
4. sentinel を `os.path.exists` + polling（PoC では 1s 間隔、本実装は inotify / watchfiles）
5. timeout（PoC: 5min）で abort
6. output.txt と status.json を読み込み、構造化して返す

## PoC 検証項目

| # | 項目 | 合格基準 |
|---|---|---|
| V1 | `wt.exe` 経由で別ウィンドウが開く | 視認 + `pgrep wt.exe` で確認 |
| V2 | spawn したウィンドウで `claude` が起動し対話プロンプトが応答する | output.txt に期待文字列が書かれる |
| V3 | sentinel ファイルが driver から検知できる | driver が sentinel を読んで loop 抜ける |
| V4 | output.txt の内容が driver で取得できる | 期待値「`HELLO_FROM_CLAUDE_<run_id>`」と一致 |
| V5 | 同じ仕組みで `codex` も成功する | output.txt に「`HELLO_FROM_CODEX_<run_id>`」 |
| V6 | エージェントが prompt の指示通り output と sentinel を書く頻度 | 1 試行ずつで成功率を観察（複数回まで） |

### 使用するテストプロンプト

```
次の動作確認タスクを実行してください:
あなたが受け取った run_id は <RUN_ID> です。
agent_kind は <AGENT> です。
1. /tmp/kaji-spawn/<RUN_ID>/output.txt に「HELLO_FROM_<AGENT>_<RUN_ID>」というテキストを書き込んでください
2. /tmp/kaji-spawn/<RUN_ID>/sentinel を touch してください
3. その後、画面に「done」と表示するだけで構いません
```

## 非対象（PoC では検証しない）

- ターミナル自動 close（spawn 側は手動でユーザが閉じる前提）
- 複数 agent 同時 spawn の競合
- ネットワーク遮断下の動作
- macOS / native Linux 環境（`wt.exe` 前提）
- 本実装での Stop hook / inotify への置き換え

## 想定リスクと割り切り

| リスク | 対応 |
|---|---|
| エージェントが instruction に従わない（sentinel を touch しない） | timeout で fail。PoC 段階で「指示の堅牢性」を見る |
| `wt.exe` のパスが PATH にない環境 | PoC は `/mnt/c/.../wt.exe` の絶対パスを想定（環境依存メモを残す）|
| prompt.txt が path 経由でなく引数になる際の quote 問題 | PoC は prompt をシェルエスケープして CLI 引数に渡す。長文は将来 stdin / `--system-prompt-file` へ |
| 別ウィンドウのプロセスを kill する方法 | PoC では手動 close。本実装は wt.exe の PID ではなく内部の `bash`/`claude` PID を `pgrep -f` で特定して SIGTERM |

## 次フェーズ（PoC 完了後）

- harness 側 `LocalAgentRunner` (仮) に組み込む I/F 設計
- Stop hook 連携（claude）/ `--output-last-message` 活用（codex）への置き換え検討
- timeout / kill / 残骸クリーンアップ policy
- 並列 run 時の run_dir 隔離 + リソース上限

---

## ファイル構成（PoC）

```
draft/lab/headless-terminal-spawn/
├── design.md           # 本書
├── spawn.py            # driver
├── wrapper.sh          # spawned terminal で実行するラッパ
├── prompt_template.txt # プロンプト本体 + 完了指示
└── results.md          # 検証結果
```
