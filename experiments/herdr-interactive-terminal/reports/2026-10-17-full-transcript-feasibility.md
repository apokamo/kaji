# Herdr backend の完全な端末ログ取得可否の調査

Issue: #476（親: #466）

## 結論

**判定: 条件付きで可能。** ただし Herdr の機能では実現できない。

- **Herdr の CLI / socket API では不可能。** 0.8.2 から最新 stable 0.9.5、preview（2026-10-14 build）まで、
  tmux `pipe-pane` に相当する raw byte の連続取得手段はない。`pane read` は再描画された snapshot で、
  返せるのは最大 1000 行（コードで固定）。`terminal session observe` は画面差分のストリームで、
  3000 行の出力から捕捉できたのは最後の 37 行だけだった。
- **kaji の wrapper 側で PTY recorder を挟めば可能。** Python 標準ライブラリだけで書いた試作 recorder を
  Herdr pane 内で検証した。Claude / Codex / Antigravity はどれも普段どおり操作でき、出力は byte 単位で
  欠落なく記録された。alternate screen の内容と終了コードも残った。util-linux への依存も OS ごとの分岐もない
  ので、ADR 007 v2 が `script(1)` を外した理由には当たらない。recorder を挟むと Herdr は agent を
  検出できなくなるが、Herdr 公式の `HERDR_AGENT=<agent>` ヒントを recorder に付ければ、3 agent とも
  状態遷移（idle / working / blocked / done）と履歴回収が直接起動と同じになる（0.8.2 で実測）。
- **スナップショット方式で失うものは、Issue の想定よりずっと大きい。** 原因は 2000 行の上限ではなく
  alternate screen にある。実環境の Claude（`tui: "fullscreen"`）と Codex（既定）は alternate screen で
  動くため、agent の終了後に `pane read` しても会話の痕跡は残らない。Claude で残ったのは
  `Resume this session with:` の 2 行だけだった。異常終了時の provider error 診断（#296 / #403）は、
  Herdr backend ではほぼ機能しない。
- **次の手:** 新しい Issue は起票しない。調査結果は #466 の子 Issue B（tmux の削除）で反映する。具体的には、
  docs と ADR 007 に書く snapshot の上限を実際の 1000 行に直し、alternate screen を使う agent では終了後に
  会話がほとんど残らないことを制限事項として明記する。PTY recorder は B に含めず、完全な transcript が
  必要になったときの選択肢として本報告書に残す。Herdr upstream への要望は既に出ている（Discussion #3643）
  ので、kaji は待たない。

## 調査環境

| 項目 | 値 |
|---|---|
| 日付 | 2026-10-17 |
| OS | Ubuntu 24.04.5 LTS on WSL2（Linux 6.18.33.2-microsoft-standard-WSL2, x86_64） |
| Herdr（実機） | client / server とも 0.8.2 stable, protocol 20（`herdr status`） |
| Herdr（最新） | stable 0.9.5（2026-10-14）, protocol 22。preview build `2026-10-14-fbd2ae23927f`（実機には入れず、docs / release notes / source で確認） |
| agent | Claude Code 2.1.302、codex-cli 0.163.0、Antigravity CLI（`agy`）1.1.16 |
| その他 | util-linux 2.39.3 `script`、Python 3.12.3 |
| kaji base | `0cbefb1`（v0.22.0） |

検証は `experiments/herdr-interactive-terminal/README.md` の safety boundary に従った。作業者自身の pane から
`pane split --no-focus` で probe pane を作り、その pane だけを操作して最後に close した。server の停止、
session の削除、他人の pane の操作はしていない。実 agent には trivial な prompt（`Reply with exactly
PROBE-OK ...`）だけを渡し、scratch directory で実行した。

## 前提の訂正（Issue 本文との差分）

| Issue 本文の記述 | 実際（一次情報） |
|---|---|
| `terminal.log` は「各ポーリング時と終了時に上書き」 | snapshot を取るのは終端イベントのときだけ（verdict 検知、shell 復帰の 3 回連続確認、timeout、launcher 失敗、Ctrl-C）。約 2 秒ごとの poll で読むのは `pane process-info` だけ（`kaji_harness/interactive_terminal_herdr.py:286-349`）。 |
| 「pane が突然終了した場合は直前のポーリング時点の画面しか残らない」 | agent の終了後も pane は shell に戻って生き続ける。snapshot は、shell-only を 3 回連続で観測した後（2 秒間隔なので、終了から約 4 秒以上経ってから）に取られる。そのため main screen の scrollback は残っている。失われるのは、alternate screen の内容、1000 行を超えた部分、`\e[3J` で消された部分。pane 自体が消えた場合は `terminal.log` が作られない（`transcript_available: false`）。 |
| `--lines 2000` | Herdr が `lines.min(1000)` で切り詰めるので、実効値は 1000 行（後述 Q1）。 |
| `transcript_truncated` は `null`（不明） | API は `truncated` を返している（実測では `true`）。kaji が text 出力の CLI を使い、`truncated=None` を固定で入れているだけ（`interactive_terminal_herdr.py:1049`）。 |
| recovery の snapshot が `terminal.log` を使う | `recovery/snapshot.py` が直接読むのは `pane-metadata.json` の `pane_id` と `result.json` の error 文字列。`terminal.log` の影響は、error 文字列に埋め込まれた診断を通じた間接的なものだけ（Q4 参照）。 |

## Q1. Herdr の CLI / socket API で pane の出力を連続して取得できるか

**できない（0.8.2〜0.9.5、preview も同じ）。**

### API surface（0.8.2 実機 schema）

`herdr api schema --json`（protocol 20）を走査した結果は次のとおり。

- request method は全 92 種。output に関係するのは `pane.read`、`pane.wait_for_output`、`agent.read`、
  `events.subscribe`、`events.wait` だけ。schema 全体に `raw`、`stream`、`pty`、`scrollback`、`pipe`
  といった語は出てこない（`bytes` は file frame 関連の 2 件だけ）。
- `ReadSource` は `visible`、`recent`、`recent_unwrapped`、`detection` の 4 つ。
- `events.subscribe` で購読できる output 系の型は `pane.output_matched` だけ。これは一致した行と
  snapshot（`PaneReadResult`）を返すもので、ストリームではない。
- `pane_output_changed` イベント（`events.wait` 用）が運ぶのは `{pane_id, workspace_id, revision}` だけで、
  本文は含まない。
- `pane_exited` は `{pane_id, workspace_id}` だけで、終了コードを持たない
  （<https://github.com/herdrdev/herdr/discussions/3051>）。

### 実測 1: `pane read` の上限

probe pane（幅 244 列、viewport 38 行）に 20,000 行を出力し、socket API の `pane.read` を直接呼んだ。

```text
recent_unwrapped  lines=500    truncated=True  returned=500   first=A019504
recent_unwrapped  lines=1000   truncated=True  returned=1000  first=A019004
recent_unwrapped  lines=2000   truncated=True  returned=1000  first=A019004
recent_unwrapped  lines=100000 truncated=True  returned=1000  first=A019004
recent_unwrapped  lines=None   truncated=True  returned=80    first=A019924
visible           lines=*      truncated=False returned=39
```

CLI の `herdr pane read --source recent-unwrapped --lines 2000`（および `--lines 5000`）も 999 行で止まった。
source でも上限を確認した。

- v0.8.2 と v0.9.5 の `src/app/api_helpers.rs:117` に `let line_limit = lines.map(|lines| lines.min(1000) as usize);`
  がある（<https://github.com/herdrdev/herdr/blob/v0.9.5/src/app/api_helpers.rs#L117>）。
- `lines` を省略したときの既定は 80 行
  （<https://herdr.dev/docs/cli-reference/>: "without it, reads default to 80 rows"）。

### 実測 2: scrollback の容量

- 設定は `[advanced] scrollback_limit_bytes = 10000000`（`herdr --default-config`、0.8.2）。
  "Maximum scrollback buffer size in bytes retained per pane terminal. Matches Ghostty's default
  scrollback-limit behavior."
- 幅 244 列の pane で 20,000 行を出したところ、`scroll.max_offset_from_bottom` は 4,524〜4,525 で頭打ちになった。
  1 行 7 文字（`B000001`）でも約 107 文字でも結果は同じだった。容量は文字数ではなく、cell grid の
  メモリ量で決まる。1 行あたり約 2.2 KB（幅 244 列）と推定され、狭い pane ほど行数は増える（推定）。
- ただし API で読めるのは、この scrollback のうち末尾 1000 行だけ。全体を見られるのは人間向けの
  `edit_scrollback`（`prefix+e`）だけ。

### 実測 3: alternate screen と `\e[3J`

| 操作 | `pane read` の結果 |
|---|---|
| main screen に 50 行 → `\e[?1049h` → alternate screen に 200 行 → 読む | 返ったのは alternate screen の viewport の 37 行だけ。`max_offset_from_bottom=0` |
| 上の状態から `\e[?1049l` で復帰 → 読む | 返ったのは main screen の 50 行だけ。alternate screen の 200 行は痕跡もない |
| 3000 行 → `\e[H\e[2J\e[3J` → 読む | scrollback が 0 になった（`max_offset_from_bottom=0`、旧出力は 0 行） |

同梱の `herdr --skill` にも次の記述がある。"Rows that leave the alternate screen do not enter Herdr's host
scrollback, so a larger line count cannot recover them."

補足: Herdr 0.8.2 / 0.9.5 には `src/server/alt_screen_read.rs` がある。ある条件下では、alternate screen
上の agent にマウスホイールのイベントを送って、agent 自身の履歴を最大 15 秒かけてスクロールしながら
読み集める。条件は、検出済みの agent が idle で、mouse reporting が有効で、`recent` 系の text read の
場合（`src/server/headless.rs` `alt_screen_read_spec`）。この経路は agent が生きている間しか働かず、
agent への入力を伴う。

実 agent で確認した結果は次のとおり。agent に 1〜150 を 1 行ずつ出力させ、idle になった後に読んだ
（Q2 の「`HERDR_AGENT` ヒントの検証」と同じ run）。

| agent | `visible` | `pane read --source recent-unwrapped --lines 1000`（kaji と同じ経路） | `agent read`（同じ指定） |
|---|---|---|---|
| Codex（alternate screen） | 29 | **149**（履歴回収が働いた） | 149 |
| Claude（`tui: fullscreen`） | 29〜30 | **30**（働かなかった） | 29〜30 |
| Antigravity（main screen） | 32 | 150（通常の scrollback） | 150 |

Claude で働かなかった理由は調べていない。0.9.1 の "Alternate-screen history reads preserve content across
changing headers and scrollbars"（#3979）で改善している可能性はあるが、0.9.x では検証していない。
いずれにしても、この経路が働くのは agent が生きていて idle のときだけで、異常終了後の snapshot には効かない。

### 実測 4: `terminal session observe`

0.8.2 にも、`herdr --help` に出ない `herdr terminal session observe <pane> --cols --rows` がある。
probe pane で observe を始めてから 3000 行を出力したところ、記録は次のようになった。

- frame は 4 個で、どれも `type=terminal.frame`（`bytes` は base64、`full`、`seq`、`width`、`height`）。
- 捕捉できた行は 37 行（`O002964`〜`O003000`）。残りの 2,963 行（98.8%）は捕捉できなかった。
- frame は `\e[?2026h ... \e[1;1H ...` の形の再描画、または差分セル（`\e[1;7H5\e[2;7H6...`）。

upstream の source でも、observe の frame は `render_terminal_virtual(runtime, area)` で再描画したもので、
PTY の byte ではない（<https://github.com/herdrdev/herdr/blob/v0.9.5/src/server/headless/render.rs>）。
また "an observer is disconnected if a socket write makes no progress for 30 seconds"（v0.9.1）とあり、
終了レコードなしに途切れることがある。

### 0.8.2 以降の変更（release notes）

| version | 関係する変更 |
|---|---|
| 0.9.0 | protocol 20 → 22。"Recent pane reads now include output that has not yet scrolled off the viewport"（#3444）。event subscription は過去分を replay せず、購読開始以降のイベントから届く（#1270） |
| 0.9.1 | 書き込みが 30 秒進まない observer を切断する（#3612）。"Alternate-screen history reads preserve content across changing headers and scrollbars"（#3979） |
| 0.9.2 | 処理が追いつかない subscriber には `events_lost` を返して切断する（#4178, #4225）。`keys.clear_pane` を追加（#4383） |
| 0.9.3〜0.9.5, preview | output の連続取得、raw output、録画、scrollback に関する変更はない |

出典: <https://github.com/herdrdev/herdr/releases>（v0.9.0〜v0.9.5）、<https://herdr.dev/llms.txt>
（"Current stable release: 0.9.5."）、<https://herdr.dev/llms-preview.txt>。

### upstream の要望状況

- Discussion #3643 "Live pane output streaming over the socket API (tmux pipe-pane equivalent)"
  （2026-09-05、maintainer の回答なし）。起票者の結論は "A true `pipe-pane`-style raw-byte tap is still
  genuinely missing from herdr" で、fork branch `tlamadon/herdr@feat/pane-stream-output` がある。
  <https://github.com/herdrdev/herdr/discussions/3643>
- Discussion #908 "Per-pane session recording + replay"（2026-07-01）。0.8.0 で、alternate screen の agent は
  `lines: 100000` を指定しても `viewport_rows` しか返らないと報告されている。
  <https://github.com/herdrdev/herdr/discussions/908>
- Discussion #4435 "Streaming pane output (content-change events)"。
  <https://github.com/herdrdev/herdr/discussions/4435>

## Q2. wrapper 側で backend に依存せずに取得できるか

**できる。Python 標準ライブラリの PTY recorder が有力。** 試作のソースは付録 A に載せた。

| 方式 | TUI の操作性 | Linux / macOS の移植性 | 判定 |
|---|---|---|---|
| `tee` / `exec > >(tee ...)` | **不可**。stdout が pipe になり、`[ -t 1 ]` が偽になる（実測 `stdout tty=n`）。TUI agent は TTY を前提にしている | 問題なし | 却下 |
| util-linux `script -q -f -e -c CMD LOG` | 可（実測: TTY、SIGWINCH、終了コードのすべて正常） | **不可**。BSD / macOS の `script` は `script [-aeFkqr] [-t time] [file [command ...]]` という別の構文。ADR 007 v2 で外した理由がそのまま残る | 却下（v1 の再来） |
| util-linux と BSD の `script -F` を wrapper で切り替える | 可（推定） | 分岐すれば可。ただし macOS 実機では未検証で、OS 分岐が復活する | 次善 |
| **kaji 所有の Python PTY recorder**（`pty.fork` + `select` + SIGWINCH を `TIOCSWINSZ` で転送、chunk ごとに append） | **可**（下の実測） | Python の `pty` / `tty` / `termios` / `fcntl` は Unix 共通の標準ライブラリ（<https://docs.python.org/3/library/pty.html>、Availability: Unix）。kaji は既に Python を必須にしているので、外部依存が増えず OS 分岐もない。macOS 実機は未検証 | **推奨** |

### 実測（Herdr 0.8.2 の pane 内、試作 `ptyrec.py`）

| 項目 | 結果 |
|---|---|
| TTY | 子プロセスから見て stdin と stdout はどちらも TTY。`stty size` は pane のサイズ（38×241）と一致 |
| SIGWINCH | `herdr pane resize` で 38→47→38 行と変えると、子プロセスに `WINCH size=47 241` と `WINCH size=38 241` が届いた（util-linux `script` も同じ結果） |
| 終了コード | 子プロセスが `exit 7` / `exit 3` で終わると、recorder の終了コードも 7 / 3 になった |
| 欠落 | 3000 行の一括出力、alternate screen の 200 行、`\e[3J` の後の出力が、ログにすべて残った（R 3000/3000、ALT 200/200） |
| Claude（haiku、`tui: fullscreen`） | TUI が通常どおり描画され、`/exit` の入力も受け付けた。ログは 8.6 KB で、`?1049h/l`、`?2026h` ×22、mouse mode ×9 を含み、`PROBE-OK` を記録していた |
| Codex（既定のモデル、effort low） | 同上。ログは 36 KB で、`?1049h/l`、`?2026h` ×151 を含み、`codex resume <uuid>` 行を記録していた |
| Antigravity | 同上（trust ダイアログの操作を含む）。ログは 15 KB で、`?1049h/l` ×1 とセッション中の `\e[3J` ×1 を含んでいた |
| **Herdr の agent 検出** | 直接起動では `agent=claude, agent_status=idle`、foreground は `claude`。recorder 経由では `agent=None, agent_status=unknown`、foreground は `python3`。Claude 用の Herdr integration hook（v8）を入れていても検出されなかった。**recorder に `HERDR_AGENT=claude` を付けると `agent=claude, agent_status=idle` に戻り、`herdr agent list` にも出た**（foreground は `python3` のまま）。agent の終了後は `agent=None` になった |

ログの形式は、tmux の `pipe-pane 'cat >> terminal.log'` と同じ raw PTY byte。kaji の
`extract_terminal_diagnostic`（ANSI を除去してから全文を走査）と `_CODEX_RESUME_RE` は、そのまま適用できる。

### `HERDR_AGENT` ヒントの検証（3 agent × 起動方法）

Herdr は pane の foreground process から agent を見つけ、画面を agent ごとの detection manifest と照合して
idle / working / blocked を判定する。recorder を挟むと foreground が `python3` になるので、そのままでは
検出されない。Herdr の docs は、この場合の対処として `HERDR_AGENT=<agent>` を示している。"a host-visible
wrapper can hide the real agent process from Herdr. Set `HERDR_AGENT=<agent>` on the wrapper command to tell
Herdr which existing agent screen manifest to use"（<https://herdr.dev/llms-full.txt> の agent detection の節）。
`<agent>` には manifest 名を使う。`server.agent_manifests` で確認した名前は `claude`、`codex`、`agy` で、
kaji の `antigravity` に対応するのは `agy`。

検証の手順。agent ごとに probe pane を 1 つ作り、`pane.get` を 0.3 秒間隔で記録しながら、次の順に操作した。

1. 起動する。
2. prompt 1（1〜150 を出力させる。tool は使わせない）を送る。
3. idle になったら履歴を読む。
4. prompt 2（`touch probe-approval.txt` の実行を依頼）を送る。承認を求める mode で起動したので blocked になる。
5. Escape で承認を拒否する。
6. 終了する。

承認を求める mode は、Claude が `--permission-mode default`、Codex が `-a on-request -s read-only`、
Antigravity が既定。承認はすべて拒否したので、ファイルは作られていない。

| agent | 起動方法 | 観測した状態遷移（秒） | 一致 |
|---|---|---|---|
| Claude | 直接 | idle 3.9 → working 4.8 → done 7.5 → working 11.4 → **blocked 13.2** → done 13.5 → idle 14.8 → 終了後 None | — |
| Claude | recorder + `HERDR_AGENT=claude` | idle 4.0 → working 5.2 → done 8.2 → idle 8.8 → working 11.8 → **blocked 13.0** → done 13.3 → 終了後 None | ✓ |
| Claude | recorder（ヒントなし） | 最後まで `agent=None`, `unknown` | ✗ |
| Codex | 直接 | idle 4.0 → working 5.2 → done 8.8 → working 14.1 → **blocked 17.4** → done 17.7 → 終了後 None | — |
| Codex | recorder + `HERDR_AGENT=codex` | idle 3.9 → working 5.1 → done 8.7 → working 13.9 → **blocked 17.2** → done 17.5 → 終了後 None | ✓ |
| Antigravity | 直接 | idle 3.9 → working 4.8 → done 12.0 → working 14.4 → **blocked 18.1** → done 18.4 → 終了後 None | — |
| Antigravity | recorder + `HERDR_AGENT=agy` | idle 4.0 → working 4.9 → done 14.2 → working 17.0 → **blocked 20.6** → done 20.9 → 終了後 None | ✓ |

Claude は、1 回目の run（直接、ヒントあり）でも同じ遷移だった。done と idle の違いは、Herdr の
"unseen background work" の扱いによるもので、起動方法の差ではない（docs: `done` は idle と同じ状態で、
未確認のまま完了したもの）。

付随して確認したこと。

- **履歴回収**（Q1 実測 3 の補足表）: どの agent でも、直接起動とヒント付きで取れた数が同じだった
  （Codex 149/149、Claude 30/30、Antigravity 150/150）。recorder はマウスホイールの入力もそのまま
  中継するので、Herdr の回収経路を妨げない。
- **integration hook の session 報告**: Claude の pane では、直接起動、ヒント付き、ヒントなしの
  いずれでも `agent_session`（`source=herdr:claude`、session UUID）が記録された。ヒントの有無で差はない。
  Codex と Antigravity の pane は、直接起動でも `agent_session` を持たなかった。
- **ヒントでは変わらないもの**: `herdr pane process-info` の foreground の名前は `python3` のまま。Herdr の
  agent 判定以外で process 名を見る仕組みには効かない。kaji の liveness 判定は pid しか見ないので影響しない。

### ADR 007 v2 との関係

v2 が `script(1)` を外したのは、util-linux と BSD の構文が違うため、macOS では transcript が取れず OS 分岐が
残るから（`docs/adr/007-interactive-terminal-runner.md` §「v1（kitty）の前提と、その崩壊」）。
v2 は pipe-pane を「terminal multiplexer が提供する、OS に依存しない記録」として採った。Herdr にはその代わりが
ないので、kaji が自分で PTY を中継するのが、移植性の点では v2 と同じ水準の解になる。

### 実装時のリスク（recorder を採るときに設計で決めること）

1. **Herdr の agent 検出**（実測）。recorder に `HERDR_AGENT=<manifest 名>`（`claude` / `codex` / `agy`）を
   付ければ、3 agent とも idle / working / blocked / done の遷移と履歴回収が直接起動と同じになる
   （上の「`HERDR_AGENT` ヒントの検証」）。ヒントを付け忘れると、人間向けの status 表示、通知、
   `herdr agent` 系での指名、alternate screen の履歴回収が失われる。kaji の liveness 判定は影響を受けない。
   実装では次の 3 点を守る。
   - kaji の agent 名を manifest 名へ写像する（`antigravity` → `agy`）。
   - ヒントは recorder のプロセスだけに付け、global に export しない（docs の注意事項）。
   - 検証済みなのは Herdr 0.8.2 だけ。0.9.x では実装時に回帰確認する。
   残る差は、`process-info` の foreground の名前が `python3` になることだけ。
2. **サイズ。** TUI の再描画で大きく膨らむ。既存コードのコメントでは、実障害の transcript は 50 MB 規模
   （`interactive_terminal.py:1021-1023`）。上限やローテーションを設けるかを決める。
3. **機密情報。** raw PTY には画面に出たものがすべて入る。tmux 時代の `terminal.log` と同じ扱いになる。
   README の "未 redact の agent transcript を保存しない" は experiments の成果物に対する規則で、
   runtime artifact の扱いは別に決める。
4. **macOS 実機での検証**と、PTY の EOF / EIO 処理の OS 差（Linux は EIO、BSD は EOF）。

## Q3. agent のセッションストアで代替できる範囲

| agent | ストア | 代替**できる** | 代替**できない** |
|---|---|---|---|
| Claude Code 2.1.302 | `~/.claude/projects/<enc-cwd>/<session-id>.jsonl`（既定で 30 日保持。`cleanupPeriodDays`） | 会話、thinking、tool_use / tool_result、Bash の stdout と stderr。セッション中の API error は `isApiErrorMessage` 付きの `assistant` レコードとして残る（`rate_limit`、`authentication_failed`、`server_error` を実データで確認）。kaji は `--session-id` で path を一意に特定できる | session ができる前の起動失敗、crash の stack trace（`--debug` 時のみ）、retry 中の banner の表示、失敗した Bash の出力の中間部分（約 10k 文字の head+tail だけ）、TUI の描画そのもの |
| Codex 0.163.0 | `~/.codex/sessions/YYYY/MM/DD/rollout-*-<uuid>.jsonl` | 会話、reasoning、tool call / output（`aggregated_output` は 64 KiB で中間を切り詰め）、turn の開始 / 完了 / 中断、token 数。kaji の resume ID の fallback にも使っている | **`Error` / `StreamError` / `Warning` / approval request / `ExecCommandOutputDelta` は保存されない**（`codex-rs/rollout/src/policy.rs` @ `rust-v0.163.0` の "Transient, non-durable events"。本調査で source を直接確認）。一部は `~/.codex/logs_2.sqlite`（tracing log）と `thread_history_1.sqlite` の `error_json` で補える |
| Antigravity 1.1.16 | `~/.gemini/antigravity-cli/conversations/<uuid>.db`、`brain/<uuid>/.system_generated/logs/transcript{,_full}.jsonl`、`log/cli-*.log` | step（入力、応答、コマンド、`ERROR_MESSAGE`）。ただし公式 docs に保存形式の記述はない（resume の docs だけがある: <https://antigravity.google/docs/cli/commands/resume>） | 非公開の形式なので変わりやすい。TUI の描画 |

まとめると、**会話と tool の内容はセッションストアで十分に追える。一方、kaji の異常終了診断が必要とする
「provider / transport の一時障害の表示」は、少なくとも Codex では保存されていない。** Claude は API error を
保存するが、起動前の失敗と crash は保存しない。どちらの場合も、TUI に出た文言をそのまま見るには terminal ログが
必要になる。

出典: <https://code.claude.com/docs/en/sessions#where-transcripts-are-stored>、
<https://code.claude.com/docs/en/tools-reference#output-limits>、<https://code.claude.com/docs/en/fullscreen>、
<https://developers.openai.com/codex/config-reference>、<https://developers.openai.com/codex/cli/reference>、
<https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/rollout/src/policy.rs>。ローカルストアは
構造（key 名と type 値）だけを確認し、本文は読んでいない。

## Q4. スナップショット方式で失われる情報

### agent ごとの描画モード（実測）

| agent | 描画 | 終了後の `pane read`（1000 行） |
|---|---|---|
| Claude（この環境の `~/.claude/settings.json` は `tui: "fullscreen"`） | alternate screen（`?1049h/l` を 1 回ずつ） | `Resume this session with:` / `claude --resume <id>` の 2 行だけ。prompt も応答も残らない |
| Codex（`tui.alternate_screen` の既定 `auto`） | alternate screen | `Token usage: ...` と `codex resume <uuid>` の案内だけ。会話は残らない |
| Antigravity | 本体は main screen（trust ダイアログだけ alternate screen） | 会話は残る。ただしセッション中に `\e[3J` を発行するので、それより前の scrollback は消える |

Claude の描画モードは利用者の設定次第。`tui: "default"` か `CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1` なら
main screen で描画される（<https://code.claude.com/docs/en/fullscreen>）。Codex は `--no-alt-screen` か
`tui.alternate_screen = "never"` で main screen になる。

### 失われる情報の具体例

1. **alternate screen で動く agent の異常終了**（Claude fullscreen、Codex）: 応答の途中で出た
   `rate limit`、`overloaded`、`stream disconnected` などの表示は、agent が終了した時点で画面ごと消える。
   約 4 秒後に取る snapshot には残らない。
2. **verdict を書いた時点の snapshot**（agent はまだ生きている）: Claude（fullscreen）は viewport の行数
   （実測 29〜38 行）しか取れない。Codex は、agent が idle なら Herdr の履歴回収で最大 1000 行取れる
   （実測 149/150。Q1 実測 3 の補足表）。ただし、verdict を書いた直後の agent が idle とは限らない。
3. **main screen で動く agent**: 末尾 1000 行より前は取れない。`\e[3J` が出ればその時点より前は消える
   （Antigravity で実際に発生）。
4. **pane 自体が消えた場合**（人間が close した、Herdr server が停止した）: `terminal.log` は作られない。
   recorder 方式なら、それまでに flush された分がディスクに残る。
5. **tmux の `pipe-pane` で残っていたもの**: alternate screen の再描画 byte を含む全出力。kaji の診断が
   全文を走査するのは、この形式を前提にしているから（`extract_terminal_diagnostic` の docstring）。

### kaji の 3 つの用途への影響

| 用途 | 実装 | 影響 |
|---|---|---|
| 異常終了時の診断 | `_terminal_exit_detail` / `read_terminal_diagnostic` → `result.json` の error 文字列、`pane-metadata.json` の `terminal_diagnostic` | **大きい。** alternate screen の agent では、provider error があっても `no_pattern` に分類され、tail は resume 案内になる。transient と sensitive の判定（#296）も入力を失い、recovery の分類が誤る可能性がある |
| Codex の resume ID の抽出 | `_extract_codex_session_id`（verdict 経路だけ） | **小さい。** verdict の時点では Codex がまだ alternate screen 上で生きているので、`terminal.log` に resume 行は出ていない。そのため常に session store の marker 照合へ fallback し、それで解決できる。異常終了の経路は設計上 `terminal.log` を読まない（#403、`interactive_terminal.py:1021`） |
| recovery の snapshot | `recovery/snapshot.py`（`pane-metadata.json` の `pane_id` と `result.json` の error） | **間接的。** `pane_id` は影響を受けない。error 文字列には 1 行目の診断結果がそのまま入るので、診断の劣化がそのまま recovery の判断材料の劣化になる |

## 比較した候補

| 候補 | 完全性 | TUI の操作性 | 移植性 | Herdr 機能との共存 | 実装コスト | 判定 |
|---|---|---|---|---|---|---|
| A. 現状のまま（recent-unwrapped の snapshot） | ✗（alternate screen で壊滅的） | ◎ | ◎ | ◎ | 0 | 異常終了診断の劣化を受け入れる場合 |
| B. snapshot を改善する（JSON で `truncated` を取る、1000 行に合わせる、終了前に複数回 read する） | △（1000 行と alternate screen の制約は残る） | ◎ | ◎ | ◎ | 小 | A に足す小修正 |
| C. agent を main screen で起動する（`CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1`、`codex --no-alt-screen`） | △（終了後も 1000 行は残る。`\e[3J` には弱い） | ○（利用者の描画の好みを上書きする） | ◎ | ◎ | 小 | 診断を安く回復する手段として有力。UX の判断は人間が行う |
| D. `pane read` を短い間隔で poll して差分を積み上げる | ✗（1000 行を超える burst、alternate screen、`\e[3J` を取りこぼす。observe の実測と同じ構造） | ◎ | ◎ | ◎ | 中 | 却下 |
| E. `terminal session observe` を記録する | ✗（実測 37/3000 行） | ◎ | ◎ | ◎ | 中 | 却下 |
| F. util-linux の `script` | ◎ | ◎ | ✗（macOS） | ○（推定: recorder と同じく `HERDR_AGENT` で回避できる） | 小 | 却下（macOS） |
| **G. kaji 所有の Python PTY recorder** | **◎（実測で欠落 0）** | **◎（実測）** | **○（macOS 実機は未検証）** | **○（`HERDR_AGENT` ヒントで、3 agent とも状態遷移と履歴回収が直接起動と一致することを実測。foreground の名前だけは `python3`）** | 中 | **採るならこれ（必要になったときに設計する）** |
| H. Herdr upstream に raw tap を実装してもらう | ◎（実現すれば） | ◎ | ◎ | ◎ | kaji 側は小 | 時期は不明（#3643 に回答なし）。待たない |
| I. agent のセッションストアだけで済ませる | △（会話は追える。Codex の error は保存されない） | ◎ | ◎ | ◎ | 小〜中 | G を補うものとして使う |

## 推奨する次のアクション

本調査は、#466（tmux 廃止）とその後の開発の判断材料を得るためのもの。新しい Issue は起票せず、
次のとおり扱う。

1. **#466 B（tmux の削除）で反映する。** B は本 Issue を待たないという決定は変わらない。B の docs と
   ADR 007 の更新で、次の 2 点を制限事項として明記する。
   - `terminal.log` の snapshot は最大 1000 行（#466 の決定にある「2000 行」は Herdr 側の上限で 1000 行に
     切り詰められる）。
   - Claude（`tui: "fullscreen"`）や Codex（既定）のように alternate screen を使う agent では、終了後の snapshot に
     会話がほとんど残らず、異常終了診断は `no_pattern` に劣化する。
2. **B で Herdr のコードに触れるなら、ついでに直す（任意）。** `_TRANSCRIPT_LINES = 2000` を実際の上限 1000 に
   合わせる。`transcript_truncated` は `null` ではなく推定値を残す。ADR v4 の決定は「CLI の argv だけを使い、
   raw socket client は使わない」なので、CLI の text 出力では `truncated` を得られない。1000 行に達したら
   `true` と推定する方法などを検討する。単独の Issue にする規模ではない。
3. **B の設計レビューで、snapshot を受け入れる決定を一度だけ確認する。** #466 の「rendered snapshot を受け入れる」
   という人間の決定は、2000 行の履歴が残る前提でされた。実際には alternate screen の agent ではほぼ何も残らない。
   それでも受け入れるかを確認し、変わらなければそのまま進める。
4. **完全な transcript が必要になったときの選択肢（今は着手しない）。**
   - G（wrapper 層の PTY recorder）: 欠落なく記録でき、`HERDR_AGENT=<manifest 名>` で Herdr の agent 検出も
     維持できる（0.8.2 で 3 agent とも実測）。設計で決めることは、既定で有効にするか opt-in にするか、
     Herdr の snapshot を別名で併用するか、サイズの上限、機密情報の扱い、macOS 実機での検証、ADR 007 の改訂。
   - C（main screen での起動）: コストは最も小さく、異常終了後の snapshot に会話と error 表示が残る。ただし
     利用者が選んだ fullscreen 描画を kaji が上書きすることになる。
5. **Herdr upstream:** 要望は Discussion #3643 に既にある。kaji から追加で要望を出すなら、同じ discussion に
   kaji の用途（異常終了診断）を補足するのにとどめる。kaji はそれを待たない。

## 副次的な発見（本 Issue の範囲外、記録のみ）

- Antigravity 1.1.16 は、終了時に `Resume with -c (or command below): agy --conversation=<uuid>` を表示する
  （実測）。kaji には「antigravity は公開 session ID を持たず、resume 非対応」という前提がある
  （`interactive_terminal.py` の `_resolve_abnormal_exit_session` のコメント、wrapper.sh の `antigravity does not support resume`）が、upstream ではもう成り立っていない可能性がある（<https://antigravity.google/docs/cli/commands/resume>）。
- `HerdrPaneRead.revision` は `pane get` の `revision` を使っているが、実測では出力によって値が変わらなかった
  （20,000 行を出力した後も `pane.read` は 0、`pane get` は 1）。出力の新しさを示す指標としては使えない。

## 再現手順

すべて Herdr 内の shell で実行する（`HERDR_ENV=1`、`HERDR_PANE_ID` は設定済み）。`$P` は自分で作った probe pane。

```bash
# probe pane の作成（終わったら close する）
P=$(herdr pane split "$HERDR_PANE_ID" --direction down --no-focus | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["pane"]["pane_id"])')

# Q1: 上限（CLI）
herdr pane run $P "clear; seq -f 'A%06g' 1 20000; echo FIN"
herdr pane wait-output $P --regex '^FIN$' --source recent --lines 5 --timeout 30000
herdr pane read $P --source recent-unwrapped --lines 2000 | wc -l   # => 999
herdr pane get $P   # .result.pane.scroll.max_offset_from_bottom

# Q1: alternate screen
herdr pane run $P "clear; seq -f 'M%04g' 1 50; printf '\033[?1049h'; seq -f 'ALT%04g' 1 300; sleep 3; printf '\033[?1049l'; echo EXITED"
herdr pane read $P --source recent-unwrapped --lines 1000 | grep -c '^ALT'   # 実行中は約 37、終了後は 0

# Q1: observe
timeout 12 herdr terminal session observe $P --cols 244 --rows 38 > observe.ndjson &
herdr pane run $P "seq -f 'O%06g observed' 1 3000"

# Q2: recorder（付録 A を ptyrec.py として保存）
herdr pane run $P "python3 -I ptyrec.py rec.log -- claude --model haiku 'Reply with exactly PROBE-OK'"
herdr pane get $P   # agent / agent_status を直接起動の場合と比べる

herdr pane close $P
```

socket API を直接呼ぶ場合は、`$HERDR_SOCKET_PATH` に改行区切りの JSON
（`{"id":"probe:1","method":"pane.read","params":{"pane_id":"...","source":"recent_unwrapped","lines":2000}}`）を
送ると、`result.read.truncated` を取得できる。

## 参照

- kaji: `kaji_harness/interactive_terminal_herdr.py:49`, `:286-349`, `:1029-1063`, `:1197-1247`;
  `kaji_harness/interactive_terminal.py:177-217`, `:1000-1094`; `kaji_harness/recovery/snapshot.py:286-314`;
  `kaji_harness/assets/interactive-terminal/wrapper.sh`; `docs/adr/007-interactive-terminal-runner.md`
- Herdr docs: <https://herdr.dev/llms.txt>, <https://herdr.dev/llms-preview.txt>,
  <https://herdr.dev/docs/socket-api/>, <https://herdr.dev/docs/cli-reference/>,
  <https://herdr.dev/docs/session-state/>
- Herdr source / releases: <https://github.com/herdrdev/herdr/releases>,
  <https://github.com/herdrdev/herdr/blob/v0.9.5/src/app/api_helpers.rs#L117>,
  <https://github.com/herdrdev/herdr/blob/v0.9.5/src/server/alt_screen_read.rs>,
  <https://github.com/herdrdev/herdr/blob/v0.9.5/src/server/headless/render.rs>
- Herdr discussions: #3643, #908, #4435, #3051（URL は本文中）
- 関連する報告: [Integration surface research](2026-08-20-integration-surfaces.md)、
  [Guarded live Herdr fake-agent smoke](2026-08-21-live-herdr-fake-agent.md)

## 付録 A: PTY recorder の試作

検証用の最小実装で、production の品質ではない。ログのローテーション、`EINTR` の網羅的な扱い、BSD での EOF
処理は、recorder を採るときに設計する。

```python
"""Prototype: backend-independent PTY transcript recorder (stdlib only, Linux/macOS).

Usage: python3 ptyrec.py <log_path> -- <argv...>
"""

import errno
import fcntl
import os
import pty
import select
import signal
import sys
import termios
import tty


def _winsize(fd: int) -> bytes:
    return fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)


def main() -> int:
    sep = sys.argv.index("--")
    log_path, argv = sys.argv[1], sys.argv[sep + 1 :]
    stdin_fd, stdout_fd = sys.stdin.fileno(), sys.stdout.fileno()
    pid, master = pty.fork()
    if pid == 0:
        os.execvp(argv[0], argv)
    if os.isatty(stdin_fd):
        fcntl.ioctl(master, termios.TIOCSWINSZ, _winsize(stdin_fd))

    def on_winch(_signum: int, _frame: object) -> None:
        try:
            fcntl.ioctl(master, termios.TIOCSWINSZ, _winsize(stdin_fd))
        except OSError:
            pass

    signal.signal(signal.SIGWINCH, on_winch)
    saved = termios.tcgetattr(stdin_fd) if os.isatty(stdin_fd) else None
    if saved is not None:
        tty.setraw(stdin_fd)
    log = open(log_path, "ab", buffering=0)
    try:
        fds = [master, stdin_fd]
        while True:
            try:
                ready, _, _ = select.select(fds, [], [])
            except InterruptedError:
                continue
            if master in ready:
                try:
                    data = os.read(master, 65536)
                except OSError as exc:  # EIO on Linux when the child side closes
                    if exc.errno != errno.EIO:
                        raise
                    data = b""
                if not data:
                    break
                log.write(data)
                os.write(stdout_fd, data)
            if stdin_fd in ready:
                data = os.read(stdin_fd, 65536)
                if not data:
                    fds.remove(stdin_fd)
                else:
                    os.write(master, data)
    finally:
        if saved is not None:
            termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, saved)
        log.close()
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    code = main()
    sys.exit(code if code >= 0 else 128 - code)
```
