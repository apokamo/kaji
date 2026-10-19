# recorder の terminal.log と tmux pipe-pane の突き合わせ（Issue #515）

実施日: 2026-10-19（UTC 13:00 頃）。tmux backend を repo から削除する前（Issue #515 の commit C1）に実施した。

## 目的

Herdr backend に挟む PTY recorder（`kaji_harness/interactive_terminal_recorder.py`）が、tmux 時代の
`pipe-pane 'cat >> terminal.log'` と同じ raw byte を欠落なく記録することを、同じ agent 実行で確認する。

## 環境

| 項目 | 値 |
|---|---|
| OS | Linux 6.18.33.2-microsoft-standard-WSL2（WSL2） |
| tmux | 3.4（private server `tmux -L kaji515-cmp -f /dev/null`） |
| Python | 3.12.3 |
| Claude Code | 2.1.303（`--model haiku`） |
| recorder | Issue #515 C1 の `kaji_harness/interactive_terminal_recorder.py` |

## 手順

`experiments/herdr-interactive-terminal/scripts/compare_recorder_pipe_pane.py --out <dir> --claude` で実行した。

1. private tmux server に 160x48 の pane を作り、初期 command を
   `sh -c 'read _; exec <python> -I <recorder> rec.log -- <agent argv>'` にする。
2. `remain-on-exit on` と `pipe-pane -o 'cat >> pipe.log'` を設定してから Enter を送り、recorder を起動する。
3. 実行 A: 決定論的な fake TUI agent（3000 行の一括出力、`\e[?1049h` 〜 `\e[?1049l` の alternate screen 200 行、
   `\e[3J` 後の出力、provider error 文字列、exit 7）。
4. 実行 B: 実 Claude（`claude --model haiku "Reply with exactly PROBE-OK"`、その後 `/exit` を send-keys）。
5. pane の終了を待ち、`rec.log` が `pipe.log` の末尾と byte 一致するかを比較する（`cmp`）。

## 結果

| 実行 | 終了コード | `rec.log` | `pipe.log` | `pipe.log` だけにある byte | 末尾一致（`cmp`） | 状態ファイル |
|---|---|---|---|---|---|---|
| A: fake TUI | 7（recorder も 7） | 34866 byte | 34868 byte | 先頭の `\r\n`（`read _` への Enter の echo） | 一致 | `complete` |
| B: Claude haiku | 0 | 4164 byte | 4166 byte | 先頭の `\r\n`（同上） | 一致 | `complete` |

- 実行 A の `rec.log`: `BULK-` 3000 行、`ALT-` 200 行、`\e[3J` の後に `AFTER-CLEAR` と provider error 行が残った。
- 実行 B の `rec.log`: alternate screen（`\e[?1049h`）の内容と、Claude の応答 `PROBE-OK`、終了時の
  `claude --resume <id>` 行が残った。
- 判定基準（`rec.log` が `pipe.log` の末尾と byte 一致し、差分は recorder 起動前の入力 echo だけ）を両方の実行で満たした。
  不一致による再実行はしていない（1 回で合格）。

sha256（`rec.log`）:

- A: `7fadedb5b506685925be0816a73b3a4f97b5181472df82faf9e1aacbc46205ca`
- B: `fae7cb78bb02dad3a17b3f5835914a73ec3415c929cb2e5ef0a14dd0769c398c`

## 注意事項

- 実行 B の script は、応答待ちの判定で prompt 自体に含まれる `PROBE-OK` の echo に一致し、応答より前に `/exit` を
  送った。Claude は入力を受け付けたまま応答を出してから終了したので、記録には応答と終了の両方が含まれ、
  比較の判定には影響しない。
- 比較で用いたログは attempt の scratchpad に置き、repo には commit していない（会話内容と session ID を含むため）。
- 外側 pane の echo（`\r\n`）だけが差分になるのは、recorder が外側 TTY を raw mode にし、自分が記録した byte を
  そのまま外側へ mirror するため。
