# Kitty interactive terminal PoC evidence

`interactive_terminal` runner の初期検討から kitty backend v1（Issue #224）までの PoC・設計・検証証跡を
保存する lab 領域。

Issue #230 で terminal backend は tmux 単一に置換されたが、kitty 版で確認した知見は ADR 007 v1、
artifact-primary verdict、runner IF、session id 回収、cleanup 方針の前提になっているため残す。

## 配置

| Path | 内容 |
|------|------|
| `raw/early-headless-terminal-spawn/` | `draft/lab/headless-terminal-spawn/` の原文コピー。terminal spawn + sentinel/output 回収の初期 PoC |
| `raw/subscription-runner-poc/tmp/` | `/home/aki/dev/kaji-poc-subscription-runner-real-cli/tmp/` の PoC 設計・結果・proposal |
| `raw/subscription-runner-poc/code/` | kitty PoC worktree から抽出した `interactive_terminal.py`、`wrapper.sh`、`test_interactive_terminal.py` |
| `raw/subscription-runner-poc/poc-smoke/` | kitty PoC worktree の smoke 実行成果物。Claude / Codex の `prompt.txt`、`terminal.log`、`verdict.yaml` など |
| `raw/design/issue-224-feat-interactive-terminal-runner.md` | kitty backend v1 の本実装設計書 |
| `raw/issue-224-artifacts/` | Issue #224 workflow の progress / run log / step artifacts |
| `raw/manual-kitty-cli-check/` | real kitty + real Claude/Codex の手動 CLI 確認ログ |

## 時系列

| フェーズ | 主なファイル | 結果 |
|----------|--------------|------|
| 初期 terminal spawn PoC | `raw/early-headless-terminal-spawn/` | WSL2 + terminal spawn で agent を別 terminal に起動し、sentinel + output file で完了と結果を回収できることを確認 |
| subscription runner PoC | `raw/subscription-runner-poc/tmp/` / `raw/subscription-runner-poc/code/` | 通常の Claude / Codex interactive CLI を別 terminal で起動し、`verdict.yaml` artifact を書かせる方向へ整理 |
| kitty backend v1 設計 | `raw/design/issue-224-feat-interactive-terminal-runner.md` | terminal backend を kitty 単一、verdict は artifact-primary、transcript は util-linux `script(1)` best-effort、cleanup は marker path + process scan として設計 |
| Issue #224 実装 workflow | `raw/issue-224-artifacts/` | 設計・実装・自動テストは通過。ただし real kitty + real Claude/Codex の手動検証が未実施として code review が RETRY になった |
| 手動 kitty CLI check | `raw/manual-kitty-cli-check/` | kitty 上で real Claude / Codex CLI が起動し、単純応答できることを確認。Codex resume id 出力も観測 |

## 確認できたこと

- 別 terminal 上の通常 Claude / Codex CLI でも、agent に artifact を書かせれば harness は stdout に依存せず workflow を進められる。
- `verdict.yaml` を primary source にする設計は interactive terminal runner と相性がよい。
- Claude / Codex の prompt 読み込み、artifact 書き込み、Codex session id 抽出は PoC で成立した。
- kitty 上の raw terminal transcript は `script(1)` で取得できる環境では診断に使える。
- 手動 check では kitty の `TERM=xterm-kitty` 上で Claude / Codex の通常 CLI 起動を確認できた。

## 問題として残ったこと

- kitty は GUI terminal 前提で、WSL2 / SSH / headless では並列可視性の前提が弱い。
- detached child を親子関係で追いにくく、cleanup に `/proc/*/cmdline` marker scan が必要になった。
- `/proc` scan は Linux 固有で macOS に移植できない。
- transcript は util-linux `script(1)` に依存し、BSD/macOS native `script` では同じ引数で動かない。
- backend を kitty / tmux 選択式にすると、protocol 抽象・実装・手動検証が二重化する。
- Issue #224 の自動テストは通ったが、real kitty + real Claude/Codex の手動検証が完了条件として残った。

## #224 で採用された設計

- `agent_runner = "interactive_terminal"` を追加する。
- CLI option は `--agent-runner interactive-terminal` を公開値にし、config value は `interactive_terminal` に正規化する。
- runner の公開 IF は `execute_interactive_terminal(step, prompt_path, verdict_path, workdir, timeout, session_id=None, close_on_verdict=True)` とする。
- wrapper は 9 引数契約にする。
  `agent prompt_path verdict_path terminal_log workdir resume_session_id launch_session_id model effort`
- transcript は wrapper 側で `script(1)` により `terminal.log` へ記録する。ただし util-linux 非互換なら fail-soft で直接起動する。
- close 時は marker path を含む process を探して kill する。
- Codex session id は `terminal.log` と `~/.codex/sessions/**/*.jsonl` の fallback から回収する。

## #230 で破棄または置換された設計

- terminal backend: kitty -> tmux。
- wrapper 引数: 9 引数 -> 8 引数（`terminal_log` を削除）。
- transcript: wrapper 側 `script(1)` -> runner 側 `tmux pipe-pane`。
- cleanup: `/proc` marker scan -> `tmux kill-pane`。
- terminal process handle: kitty process / child process tracking -> tmux pane id。
- backend 選択式は追加せず、tmux 単一に確定。

## 読み方

1. 初期発想を追うなら `raw/early-headless-terminal-spawn/design.md` と `results.md`。
2. kitty PoC の実装方針を見るなら `raw/subscription-runner-poc/tmp/interactive-terminal-poc-design.md` と `interactive-terminal-poc-result.md`。
3. 実装参照コードを見るなら `raw/subscription-runner-poc/code/interactive_terminal.py` と `wrapper.sh`。
4. #224 本実装の判断は `raw/design/issue-224-feat-interactive-terminal-runner.md`。
5. workflow 上の詰まりは `raw/issue-224-artifacts/progress.md`。
6. real CLI の terminal transcript は `raw/manual-kitty-cli-check/*.log`。

## 関連

- tmux 置換後の #230 証跡: `draft/lab/issue-230-poc/`
- ADR: `docs/adr/007-interactive-terminal-runner.md`

