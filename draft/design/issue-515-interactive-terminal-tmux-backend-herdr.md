# [設計] interactive terminal の tmux backend を削除し、Herdr backend に PTY recorder による完全な端末ログ記録を追加する

Issue: #515（親: #466）

## 概要

`interactive_terminal` runner から tmux backend を削除して Herdr を唯一の terminal backend にし、
同じ変更で Herdr backend の agent 起動経路に kaji 所有の Python PTY recorder を挟んで、
`terminal.log` に agent の端末出力を raw PTY byte で欠落なく記録する。

## 背景・目的

- tmux / Herdr の 2 backend で pane 操作・ownership・配置・存命判定・cleanup を二重保守している
  （`kaji_harness/interactive_terminal_tmux.py` 760 行、`interactive_terminal_herdr.py` 1247 行）。
  v0.23.0（2026-10-17 公開）で tmux backend を非推奨化し、後続リリースでの削除を告知済み
  （`CHANGELOG.md` 0.23.0 `### Deprecated`）。
- Herdr backend の `terminal.log` は `herdr pane read --source recent-unwrapped` の rendered snapshot で、
  実効 1000 行に切り詰められ、alternate screen で動く Claude（fullscreen）/ Codex（既定）では終了後に
  会話がほぼ残らない。異常終了時の provider error 診断（#296 / #403）は実質 `no_pattern` に劣化する
  （#476 報告書 Q1・Q4）。tmux を削除すると raw byte を記録する唯一の経路（`pipe-pane`）が消えるため、
  削除と同時に Herdr 側で同等の記録を用意する（#466「2026-10-19 の追加決定」）。

### ユースケース

- **保守者**として、interactive 実行の機能追加・障害修正・検証を Herdr だけに集中するために、
  tmux 固有の実装とテストを削除したい。
- **障害調査者**として、Herdr で実行した agent が異常終了したときに provider error を判別するために、
  `terminal.log` に端末出力の完全な記録を残したい。
- **既存の tmux 利用者**として、どの設定を直せば Herdr または headless へ移行できるかを判断するために、
  エラーメッセージと利用ガイドで移行方法を知りたい。
- **障害調査者**として、過去の tmux run の artifact を再調査するときに分類の意味が変わらないように、
  旧型名の分類と旧 pane metadata の読み取りを維持したい。

### 代替案と不採用理由（記録方式）

#476 報告書「比較した候補」A〜I を前提とする。人間決定（#466 2026-10-19）で G（kaji 所有の Python
PTY recorder）の実装が確定しているため、ここでは G の中での設計選択だけを比較する。

| 選択 | 採否 | 理由 |
|---|---|---|
| recorder を wrapper.sh の **外側**（launcher → recorder → wrapper.sh → agent）に置く | **採用** | wrapper.sh の 10 引数契約・分岐を変えずに済む。`HERDR_AGENT` を recorder プロセスだけに付ける要件を launcher の `env` prefix 1 箇所で満たせる。wrapper の banner（agent / workdir / verdict_path 等）も記録され、診断材料が増える |
| recorder を wrapper.sh の `run_agent()` の中（`exec python recorder -- bash -c ...`）に置く | 不採用 | wrapper の argv 契約に terminal.log path と python path の 2 引数を足す必要がある。3 agent 分岐すべてに recorder の知識が入る |
| recorder を `assets/interactive-terminal/` の package-data スクリプトにする | 不採用 | ruff / mypy / pytest の import 対象にならず、Small テストで関数単位に検証しにくい |
| Herdr の rendered snapshot を別名ファイルで併存させる | 不採用 | 正本が 2 つになり、診断がどちらを読むかの分岐が生じる。alternate screen では snapshot に情報がほぼない（#476 Q4）。recorder 起動失敗時の補助としての価値は、recorder が起動前に log を開き、子 exec 失敗も PTY 経由で log に残す設計で代替する |

## インターフェース

### 入力

#### 設定（`.kaji/config.toml` / `.kaji/config.local.toml` の `[execution]`）

| キー | 型 | 受理値 | 既定 | 変更点 |
|---|---|---|---|---|
| `agent_runner` | str | `"headless"` / `"interactive_terminal"` | `"headless"` | 変更なし |
| `interactive_terminal_backend` | str | **`"herdr"` のみ** | **`"herdr"`**（旧既定 `"tmux"`） | `"tmux"` は廃止エラー、その他の文字列・非文字列は入力検証エラー |
| `interactive_terminal_close_on_verdict` | bool | — | `true` | 変更なし |

per-key precedence（`config.local.toml` が tracked config を上書き）は維持する。

#### CLI（`kaji run`）

| option | 受理値 | 変更点 |
|---|---|---|
| `--agent-runner` | `headless` / `interactive_terminal` | 変更なし |
| `--interactive-terminal-backend` | **`herdr` のみ** | `tmux` は移行案内付きの usage error、その他は argparse 標準の `invalid choice` |

新しい設定キー・CLI option は追加しない。

#### recorder（kaji 内部契約。利用者向けではない）

```text
<python> -I <kaji_harness/interactive_terminal_recorder.py の絶対 path> <terminal_log> -- <argv...>
```

- `<python>`: kaji 自身の `sys.executable`（絶対 path）。recorder は stdlib のみに依存し、`-I` で
  起動するため、pane 側の `PYTHONPATH` / user site / cwd の module に影響されない。
- `<terminal_log>`: attempt directory の `terminal.log`（絶対 path）。
- `<argv...>`: 子プロセスとして PTY 上で `execvp` する argv（実運用では packaged `wrapper.sh` と 10 引数）。
- 環境変数 `HERDR_AGENT`: launcher が recorder プロセスにだけ付ける。recorder は子の環境から取り除いてから exec する。

### 出力

#### `terminal.log`（attempt directory）

- 形式: 子プロセス（wrapper.sh → agent）が PTY に書いた **raw byte をそのまま追記**したもの。
  ANSI escape、alternate screen の内容（`\e[?1049h` 以降）、`\e[3J` 以降の出力を含む。
  tmux 時代の `pipe-pane 'cat >> terminal.log'` と同じ形式。
- 書き込み: chunk ごとに unbuffered で追記（`O_APPEND`）。recorder / pane が途中で強制終了されても、
  それまでに読んだ chunk は残る。
- サイズ上限・ローテーション: なし（tmux 時代と同じ）。
- permission: tmux 時代と同じ（プロセスの umask に従う）。
- `close_on_verdict = false` で pane を残した場合、agent が生きている間は step 完了後も追記が続く
  （tmux 時代の `pipe-pane` と同じ）。

#### `pane-metadata.json` の transcript metadata（Herdr）

| キー | 旧値（snapshot） | 新値（recorder） |
|---|---|---|
| `transcript_kind` | `"rendered_recent_unwrapped_snapshot"` | `"raw_pty_recording"` |
| `transcript_available` | snapshot 取得成否 | metadata 書き込み時点で `terminal.log` が通常ファイルとして存在するか |
| `transcript_truncated` | `null`（不明固定） | `false`（recorder は上限を持たず切り詰めない）。`transcript_available` が `false` のときは出力しない |
| `transcript_revision` | Herdr pane の `revision` | `null`（recorder には revision 概念がない。キーは schema 安定のため残す）。`transcript_available` が `false` のときは出力しない |
| `transcript_bytes`（新設） | — | metadata 書き込み時点の `terminal.log` の byte 数。`transcript_available` が `false` のときは出力しない |

transcript metadata は metadata を書くすべての経路（marker 失敗を除く）で出力する。`terminal_diagnostic`
を付ける経路（launcher 失敗、shell 復帰、timeout）は現状を維持する。marker 設定失敗の経路は wrapper を
起動していないので transcript metadata を出力しない（現状どおり `pane_read` 相当なし）。

#### エラー（新設・変更）

| 条件 | 例外 / 終了 | メッセージ要件 |
|---|---|---|
| config に `interactive_terminal_backend = "tmux"`（tracked / local のどちらでも、runner が headless でも） | `ConfigLoadError(<値を定義したファイル>, ...)` | 「tmux backend は削除された（0.23.0 で非推奨）」こと、`"herdr"` に変更して Herdr pane 内で `kaji run` する／キーを削除して `agent_runner = "headless"` にする、の 2 つの移行先、「`config.toml` と `config.local.toml` の両方から tmux の値を削除・変更する」こと、CLI override では救済できないこと、移行ガイドの anchor |
| config の backend が `"tmux"` / `"herdr"` 以外の文字列 | `ConfigLoadError(<ファイル>, ...)` | `must be 'herdr'`（既存書式に合わせる） |
| config の backend が非文字列 | `ConfigLoadError(<ファイル>, ...)` | 既存どおり型名を示す |
| CLI `--interactive-terminal-backend tmux` | argparse usage error（exit 2） | tmux backend は削除された、`herdr` を指定するか `--agent-runner headless` を使う、の案内 |
| CLI に unknown 値 | argparse 標準の `invalid choice`（exit 2） | 既存挙動 |
| Herdr 外（`HERDR_ENV != "1"` / `HERDR_PANE_ID` なし）から interactive 実行 | `HerdrSessionRequiredError`（既存） | Herdr pane 内で `kaji run` する、または `agent_runner = "headless"` を使う案内を追加。tmux は案内しない |
| `herdr` CLI が見つからない | `CLINotFoundError`（既存） | 「Install Herdr (>= 0.8.2) or use the headless runner」。`interactive_terminal_backend='tmux'` の案内を削除 |
| recorder の Python（`sys.executable`）または recorder module が存在しない | `FileNotFoundError`（wrapper 欠落と同じ扱い、pane 起動前） | 欠落 path を示す |

### 使用例

```toml
# .kaji/config.toml — 廃止後の interactive 設定（backend は省略しても herdr）
[execution]
agent_runner = "interactive_terminal"
interactive_terminal_backend = "herdr"
```

```text
# 文言は例（実装で既存のエラー書式に合わせる）
$ kaji run .kaji/wf/official/dev.yaml 515      # Herdr pane の外で実行
HerdrSessionRequiredError: ... run kaji inside a Herdr pane, or set agent_runner = "headless" ...

# config.local.toml に旧値が残っている場合（runner に関係なく config 読み込み時に失敗）
config error (/repo/.kaji/config.local.toml): execution.interactive_terminal_backend = 'tmux' is no longer
supported: the tmux backend was removed (deprecated in 0.23.0). Set it to "herdr" and run kaji inside a
Herdr pane, or remove the key and use agent_runner = "headless". Remove or change "tmux" in both
.kaji/config.toml and .kaji/config.local.toml; --agent-runner / --interactive-terminal-backend cannot
override it. See docs/cli-guides/interactive-terminal-runner.md#migrating-from-tmux
```

```python
# Herdr backend 内部での launcher command 組み立て（疑似コード）
command = shlex.join([
    *env_prefix,                       # Issue #407 の attempt env（既存）
    f"HERDR_AGENT={herdr_agent_name}",  # claude / codex / agy
    sys.executable, "-I", str(recorder_path), str(terminal_log), "--",
    str(wrapper), agent, prompt_path, verdict_path, workdir, ...,  # 既存 10 引数
])
```

## 制約・前提条件

- Herdr の最低 version は 0.8.2 のまま。`HERDR_AGENT` ヒントの効果は 0.8.2 で 3 agent とも実測済み
  （#476 報告書「`HERDR_AGENT` ヒントの検証」）。0.9.x での回帰確認は merge 後の実機確認で行う。
- Herdr の agent manifest 名は `claude` / `codex` / `agy`。kaji の `antigravity` は `agy` に写像する。
- recorder は Python 標準ライブラリ（`pty` / `tty` / `termios` / `fcntl` / `select` / `signal` / `os`）
  だけを使う。これらは Unix 共通（<https://docs.python.org/3/library/pty.html> Availability: Unix）。
  `requires-python >= 3.11` で `-I` は `-E -P -s` を含む。kaji の module を import しない。
- PTY の child 側 close は Linux では master の `read()` が `EIO`、BSD / macOS では `b""`（EOF）になる。
  両方を EOF として扱う。macOS 実機での検証は別途（ADR 007「macOS: deferred」の前例）。
- 正常完了の判定は引き続き filesystem の `verdict.yaml` のみ。recorder の終了コード、Herdr の agent 状態、
  出力 pattern を完了判定に使わない。
- pane の存命判定は `herdr pane process-info` の foreground pid が shell pid 以外を含むか、で変わらない。
  recorder 挿入後は foreground が `python3`（recorder）になるが、agent が終了すれば recorder も終了して
  shell-only に戻るため、判定ロジックは変更不要（#476 報告書「ヒントでは変わらないもの」）。
- 過去 artifact 互換: `recovery/classify.py` の `_USER_PRECONDITION_EXCEPTIONS` は型名の**文字列**で
  照合し、`recovery/snapshot.py` の `_orphan_pane_id` は backend に依存せず `pane-metadata.json` の
  `pane_id` だけを読む。どちらも `interactive_terminal_tmux` を import しない。
- 公開済み managed starter の追随、CHANGELOG・過去の設計書・PoC・検証記録からの tmux 文字列削除、
  Claude Code の `--tmux` 等の upstream 機能の説明、設定キー / CLI option 自体の撤去、headless の
  挙動変更、#422 は本 Issue に含めない（Issue「スコープ境界」）。

### 変更スコープ（モジュール・ファイル）

| 区分 | 対象 | 変更 |
|---|---|---|
| 削除 | `kaji_harness/interactive_terminal_tmux.py` | ファイル削除 |
| 新設 | `kaji_harness/interactive_terminal_recorder.py` | PTY recorder（stdlib のみ） |
| 変更 | `kaji_harness/interactive_terminal.py` | tmux import と分岐を削除し、Herdr を直接呼ぶ。`backend` 引数を削除 |
| 変更 | `kaji_harness/interactive_terminal_herdr.py` | recorder を挟む command 組み立て、`HERDR_AGENT` 写像、snapshot 取得（`_read_herdr_pane` / `_capture_herdr_snapshot` / `HerdrPaneRead` / `_TRANSCRIPT_LINES`）の削除、metadata の transcript フィールド変更、エラー文言 |
| 変更 | `kaji_harness/interactive_terminal_common.py` | `_terminal_exit_detail` の tmux 既定 prefix を廃止（prefix 必須化）、tmux 前提の docstring 更新 |
| 変更 | `kaji_harness/assets/interactive-terminal/wrapper.sh` | `pipe-pane` 前提のコメントを recorder 前提に更新（挙動は不変） |
| 変更 | `kaji_harness/config.py` | `Literal["herdr"]`・既定 `"herdr"`・tmux 廃止エラー。`tmux_backend_deprecation_warning` / `TMUX_BACKEND_DEPRECATION_PREFIX` / `_TMUX_BACKEND_DEPRECATION_DOCS` と、警告専用だった `interactive_terminal_backend_source` を削除 |
| 変更 | `kaji_harness/commands/parser.py` | `--interactive-terminal-backend` の受理値を `herdr` のみにし、`tmux` に移行案内付きエラー |
| 変更 | `kaji_harness/commands/run.py` | 非推奨警告の emitter・once-per-process global・import を削除 |
| 変更 | `kaji_harness/runner.py` | `backend=` 引数の受け渡しを削除。tmux 前提のコメント（`:1301` 付近）を更新 |
| 変更 | `kaji_harness/errors.py` | `TmuxSessionRequiredError` クラスを削除（送出元がなくなる）。`HerdrSessionRequiredError` の docstring から tmux 参照を除く |
| 維持 | `kaji_harness/recovery/classify.py` | `"TmuxSessionRequiredError"` 文字列を維持し、「過去 run の artifact 互換のための型名」であるコメントを付ける |
| 維持 | `kaji_harness/recovery/snapshot.py` | 変更なし（backend 非依存） |
| 変更 | `.kaji/config.toml` | backend のコメントを `"herdr"` のみに更新 |
| テスト | 「テスト戦略」参照 | tmux 固有テストの削除、共通テストの Herdr 化、recorder テスト新設 |
| docs | 「影響ドキュメント」参照 | |

## 方針

### 1. 設定・CLI の契約

- `KajiConfig._parse_execution`: `merged.get("interactive_terminal_backend", "herdr")`。型検査（既存）→
  `"tmux"` なら廃止専用の `ConfigLoadError(source(key), <移行メッセージ>)` → `"herdr"` 以外なら
  `must be 'herdr'` の `ConfigLoadError`。検証は runner 値に関係なく config 読み込み時に行う
  （headless でも黙って無視しない）。`source(key)` は per-key merge 後に値を定義したファイルを返す既存
  helper を使う。
- 廃止メッセージは `config.py` のモジュール定数 1 箇所で組み立て、config エラーと CLI エラーで
  移行先の文言（herdr / headless / docs anchor）を共有する。
- `parser.py`: `choices=["herdr"]` に `type=` の検証関数を組み合わせ、`"tmux"` は
  `argparse.ArgumentTypeError(<移行メッセージ>)`、それ以外は値をそのまま返して choices 判定に委ねる。
  argparse の usage error（exit 2）として出る。
- `run.py`: `_apply_execution_overrides` はそのまま（`herdr` しか来ない）。非推奨警告の emitter・
  global・import を削除する。

### 2. dispatch

- `interactive_terminal.execute_interactive_terminal` は public entry point として残し、
  `interactive_terminal_herdr.execute_interactive_terminal_herdr` を直接呼ぶ。`backend` 引数は
  単一値しか取らないため削除し、`runner.py` の受け渡しも削除する。docstring の Raises を Herdr の
  契約（`HerdrSessionRequiredError` / `CLINotFoundError` / `CLIExecutionError` / `StepTimeoutError` 等）
  に書き換える。

### 3. PTY recorder（`interactive_terminal_recorder.py`）

責務と主要な関数（名前は実装時に調整可）:

| 名前 | 責務 |
|---|---|
| `main(argv) -> int` | 引数解析（`<log> -- <argv...>`）、log を開く、子の起動、relay、終了コードの返却 |
| `_read_master(fd) -> bytes` | master の read。`OSError(EIO)` と `b""` を同じく EOF（`b""`）として返し、それ以外の `OSError` は送出する |
| `_child_exec(argv)` | 子側: 環境から `HERDR_AGENT` を除き `os.execvp`。失敗時は stderr（PTY）に理由を書いて `os._exit(127)` |
| `_exit_code(status) -> int` | `os.waitstatus_to_exitcode` の結果を shell 慣習（signal は `128 + signum`）に変換 |

処理の流れ:

1. 引数を検証し、`terminal_log` を `O_WRONLY | O_CREAT | O_APPEND` で**子の起動前に**開く。開けなければ
   stderr に理由を出して exit 2（agent を起動しない）。
2. `pty.fork()`。子は `_child_exec`。親は stdin が TTY なら winsize を master に複製し
   （`TIOCGWINSZ` → `TIOCSWINSZ`）、SIGWINCH handler で以後も転送し、stdin を raw mode にする。
3. `select` で master と stdin を待つ。master の chunk は **log へ先に全量書き込み**、次に stdout へ全量
   書き込む（partial write をループで処理）。stdin の chunk は master へ転送。stdin の EOF 後は stdin を
   監視対象から外す。`EINTR` は PEP 475 の自動再試行に任せ、`InterruptedError` は再試行する。
4. `_read_master` が EOF を返したら loop を抜け、`finally` で terminal 設定を復元し log を閉じ、
   `waitpid` の結果を `_exit_code` で返す。
5. 失敗の扱い（診断が本処理を壊さない）:
   - log 書き込みの `OSError`（disk full 等）: stderr に 1 回だけ警告し、以後は記録を止めて relay を続ける。
   - stdout 書き込みの `OSError`（pane が閉じられた等）: mirror を止め、子の EOF まで log 記録を続ける。
   - SIGHUP / SIGTERM には独自 handler を付けない（既定動作で終了。それまでの chunk は log に残っている。
     master が閉じると kernel が子の session に SIGHUP を送る）。

`HERDR_AGENT` を recorder のプロセスだけに付ける要件は、「launcher の `env` prefix で recorder に付与」＋
「recorder が子の環境から削除」で満たす。Herdr は pane の foreground（= recorder）の環境を見て manifest を
選ぶ（#476 報告書、Herdr docs の agent detection 節）ため、子から消しても検出には影響しない。nested kaji
が子の中で起動する Herdr pane へヒントが漏れることも防ぐ。

### 4. Herdr backend の変更

- preflight（pane 起動前）: wrapper の存在確認に加えて `sys.executable` と recorder module の path が
  ファイルとして存在することを確認する。
- `_HERDR_AGENT_MANIFESTS = {"claude": "claude", "codex": "codex", "antigravity": "agy"}`。未知の agent は
  既存の `_validate_step` で弾かれるため写像漏れは起きないが、写像を引けない場合は `ValueError`。
- launcher に渡す command を「attempt env prefix → `HERDR_AGENT=<manifest>` → `sys.executable -I <recorder>
  <terminal_log> --` → 既存 wrapper argv」の順で組み立てる。common の `_build_wrapper_command` の
  wrapper 10 引数の順序・quoting は変えない（recorder 部分は Herdr 側で前置する）。
- 終端イベント（verdict 検知、shell 復帰、timeout、launcher 失敗、Ctrl-C）で行っていた
  `_capture_herdr_snapshot` を削除する。terminal.log は recorder が書くので kaji 側は書かない。
  verdict 検知時の Codex session 抽出（`_extract_codex_session_id(terminal_log, ...)`）、
  異常終了時の `_terminal_exit_detail(terminal_log, prefix=...)`、metadata の `terminal_diagnostic` は
  recorder の log をそのまま読む。
- shell 復帰時の prefix から「rendered snapshot may be incomplete」を削除する
  （例: `"Herdr pane returned to its shell before writing verdict.yaml"`）。
- `_write_herdr_metadata` は `pane_read` 引数の代わりに `terminal_log` を受け、上記「出力」の
  transcript metadata を計算する。`terminal_diagnostic` を付けるかどうかは別の bool 引数で現状の経路と
  一致させる。
- 変更しないもの: caller-context guard、version 検査、split / 配置 / prune、ownership token、launcher の
  一度だけの起動と start marker、`close_on_verdict`、cleanup 失敗が元の verdict / 例外を置き換えない契約、
  Ctrl-C 時の pane 保持、attempt deadline。

### 5. tmux backend の削除と過去 artifact 互換

- `interactive_terminal_tmux.py` を削除する（executable 探索、version 検査、session 解決、split、pane
  marker、list / prune、pipe-pane、存命判定、kill、tmux metadata 作成をすべて含む）。
- `TmuxSessionRequiredError` クラスを削除する。`recovery/classify.py` は型名文字列を維持し、過去 run の
  `run.log` の `exception_type` が `user_precondition_error` に分類され incident 記録から除外される挙動を
  保つ（`tests/test_recovery_classify.py` / `test_recovery_incident_handler.py` で既存の検証を維持）。
- `recovery/snapshot.py` の `_orphan_pane_id` は backend 非依存で、旧 tmux の `pane-metadata.json`
  （`pane_id: "%N"`）もそのまま読める。新たな読み取り処理は追加しない。

### 6. 実装順序（tmux 削除前の突き合わせを含む）

implement step 内で次の順に進め、各段階を commit する。

1. recorder module と Small / Medium テスト。
2. **tmux 削除前の突き合わせ**（下記「テスト戦略 > 変更固有検証」）。結果を
   `experiments/herdr-interactive-terminal/reports/<日付>-recorder-vs-pipe-pane.md` に記録し、比較に使った
   script を `experiments/herdr-interactive-terminal/scripts/` に置いて commit する。
3. Herdr backend への組み込み、snapshot 削除、metadata 変更。
4. 設定・CLI の契約変更、非推奨警告の削除。
5. tmux backend・tmux 固有テストの削除。
6. docs、ADR 007、CHANGELOG。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| tmux backend の廃止時期 | 予告リリース v0.23.0 の次のマイナー以降の BREAKING リリースで削除 | 人間決定: #466「決定事項」（grill-me Q3「A」）、#515「重要判断」表 | 本 Issue では version 番号を決めない（release 時）。CHANGELOG `[Unreleased]` に BREAKING として記載 |
| 設定・CLI の契約 | キーと option を残し `herdr` のみ受理、`tmux` は理由と移行方法付きエラー、unknown / 型不正は入力検証エラー、Herdr 外は `HerdrSessionRequiredError`、headless でも tmux 設定を無視しない | 人間決定: #466「初回削除リリースの設定契約」 | config は読み込み時に検証（runner 非依存）。CLI は argparse の `type=` で tmux 専用メッセージ、unknown は標準 `invalid choice`（exit 2）。メッセージは config.py の 1 箇所で組み立て共有 |
| SSH / 画面のない環境 | WSL2 と、SSH 先ホストで Herdr を起動する場合をサポート対象。SSH は merge 後に実機 1 回確認 | 人間決定: #466 決定事項（grill-me Q1「A で」） | コード上の分岐は追加しない。merge 後の確認項目として扱う |
| 完全な端末ログ | PTY recorder を本 Issue で実装する | 人間決定: #466「2026-10-19 の追加決定」（「PTY recorder の実装をこのイシューに含める」「過剰分割は不要」） | 配置（launcher → recorder → wrapper）、module 名、引数形式、失敗時の挙動を具体化 |
| 過去 artifact の互換 | 新しい tmux 操作を削除し、型名による分類と旧 metadata の読み取りを維持 | 人間決定: #466「採用する進め方」5。ADR 008 との整合は AI の仮定（#466 1 回目の AI 仮定表）で、検査先は本設計レビュー | `TmuxSessionRequiredError` クラスは削除し、classify の型名文字列だけ残す。snapshot.py は backend 非依存のため無変更。新しい互換読み取り処理を書かないので ADR 008 決定 1 に抵触しない |
| recorder は Herdr のみに適用 | Herdr の launch 経路だけに挟む | AI の仮定（#466 追加決定の AI 仮定表を継承。tmux は削除されるため論点は消滅）。検査先: 本設計レビュー | tmux は同じ Issue で削除されるので二重記録の懸念はない |
| `terminal.log` の形式と snapshot の扱い | `terminal.log` は raw PTY byte のみ。Herdr rendered snapshot の取得は削除し、別名でも残さない | AI の仮定（#466 は「snapshot を残すかどうかは設計で決める」と設計へ委任）。根拠: 正本を 1 つにする、alternate screen では snapshot の情報がほぼない（#476 Q4）、recorder は子起動前に log を開くため起動失敗も log に残る。検査先: 本設計レビュー、review-code | metadata の `transcript_kind` で記録方式を区別（`raw_pty_recording`）。`_read_herdr_pane` 等を削除 |
| 新しい設定キー / CLI option | 追加しない | AI の仮定（#466 追加決定の AI 仮定表）。検査先: 本設計レビュー | recorder は常に有効（opt-in / opt-out の切り替えなし）。必要になった場合は公開契約の変更として停止する |
| サイズ上限・機密情報 | 上限なし、ローテーションなし。permission と扱いは tmux 時代の `terminal.log` と同じ | AI の仮定（#466 追加決定の AI 仮定表。tmux 時代の 50 MB 規模の前例）。検査先: 本設計レビュー | ファイルは umask に従う。docs に「raw PTY には画面に出たものがすべて入る」ことを明記 |
| EIO / EOF と macOS | Linux の EIO と BSD の EOF を両方 EOF として扱い unit test で検証。macOS 実機は別途 | AI の仮定（#466 追加決定の AI 仮定表、ADR 007「macOS: deferred」）。検査先: 本設計レビュー、review-code | `_read_master` に集約して Small テストで両方を検証 |
| `HERDR_AGENT` の付け方 | recorder プロセスだけに付け、子からは削除。`antigravity` → `agy` | AI の仮定（#476 報告書「実装時のリスク」1、#466 追加決定の AI 仮定表）。検査先: 本設計レビュー、review-code | launcher の env prefix で付与し、recorder が子 exec 前に `HERDR_AGENT` を除去。Medium テストで子の環境に無いことを検証 |
| tmux 削除前の突き合わせ | 同じ agent 実行で recorder の log と tmux `pipe-pane` の log を比較し、欠落がないことを記録 | 人間決定由来の完了条件: #515「Herdr backend の PTY recorder」（#466 追加決定の AI 仮定を readiness で Issue に固定） | private tmux server で、決定論的な fake TUI agent と実 Claude の 2 実行を比較。判定基準と記録先を定義（テスト戦略） |
| `interactive_terminal` の `backend` 引数・`interactive_terminal_backend_source` の削除 | 単一値しか取らない引数と、非推奨警告専用だった field を削除 | AI の仮定（ADR 008「互換レイヤを書かない」、grep で利用箇所が警告とその field 設定だけであることを確認）。検査先: 本設計レビュー、review-code | 設定キー自体は人間決定どおり残す。エラーのファイル特定は既存の `source(key)` を使うため field は不要 |
| `transcript_revision` の扱い | キーを残して `null` | AI の仮定（完了条件「kind / availability / revision / truncation が recorder の記録を正しく表す」に対し、recorder に revision 概念がないことを `null` で表す）。検査先: 本設計レビュー | `transcript_bytes` を追加して記録量を示す |

one-way door の未決はない。公開契約（設定・CLI・エラー）、廃止時期、記録方式の採否、互換範囲はすべて
人間決定の範囲内で詳細化した。残りは誤っても review / PR review で安く直せる two-way door であり、
AI の仮定として上表に検査先を記録した。

## テスト戦略

### 変更タイプ

実行時コード変更（設定・CLI 契約の破壊的変更、backend 削除、recorder の新設）。

### Small テスト

- **設定の契約**（`tests/test_config.py`）: backend 未指定 → `"herdr"`。明示 `"herdr"` → `"herdr"`。
  tracked config の `"tmux"` → `ConfigLoadError` の path が `config.toml`、local overlay の `"tmux"` →
  path が `config.local.toml`（tracked が `"herdr"` でも local の tmux で失敗）。`agent_runner = "headless"`
  でも `"tmux"` は失敗。メッセージに herdr / headless / 両ファイルの修正 / docs anchor が含まれる。
  unknown 文字列・非文字列は既存の入力検証エラー。既存の tmux を使った overlay / source テスト
  （`:1335`, `:1452-1499` 付近）はこの観点に書き換える。
- **CLI の契約**（`tests/test_run_execution_overrides.py` ほか parser テスト）: `--interactive-terminal-backend
  herdr` を受理。`tmux` は exit 2 で移行案内を stderr に出す。unknown は `invalid choice`。
- **非推奨警告の削除**: `tests/test_tmux_backend_deprecation.py` を削除。stderr に deprecation 警告が
  出ないことは Medium の CLI 経路で確認する。
- **recorder の純関数**（新規 `tests/test_interactive_terminal_recorder.py`）: `_read_master` が
  `OSError(EIO)` を EOF に、`b""` を EOF に、`EBADF` 等は送出（Linux / BSD 両方の終了を fake read で検証）。
  `_exit_code` が正常終了コードと signal 終了（`128 + signum`）を変換。引数解析（`--` 欠落・argv 空は exit 2）。
- **Herdr command 組み立て**（`tests/test_interactive_terminal_herdr.py`）: launcher command が
  env prefix → `HERDR_AGENT=<manifest>` → `sys.executable -I <recorder> <terminal_log> --` → wrapper
  10 引数の順になる。claude / codex / antigravity が `claude` / `codex` / `agy` に写像される。
  wrapper 10 引数の順序・quoting は既存テストどおり。
- **metadata**: `transcript_kind == "raw_pty_recording"`、log 有無で `transcript_available`、
  `transcript_truncated is False`、`transcript_revision is None`、`transcript_bytes` が log サイズと一致。
  log がない場合に truncated / revision / bytes を出さない。既存の `transcript_revision == 9` 等の検証は置き換える。
- **エラー文言**: missing CLI と caller-context のメッセージに `tmux` が含まれず、headless / Herdr pane の
  案内を含む。`_terminal_exit_detail` の既定 prefix 廃止に合わせて common テストの期待文字列
  （`:495`, `:576` 付近）を更新。
- **import 境界**（`tests/test_interactive_terminal_common.py`、`tests/test_layer_imports.py`）:
  `interactive_terminal_tmux` を layer 表から外し、recorder を追加する。recorder が kaji の module を
  import しない（stdlib のみ）ことを import scanner で検証する（`-I` で path 起動するため）。
  scanner の sample 入力に使っている `interactive_terminal_tmux` 文字列は任意の module 名に置き換える。

### Medium テスト

- **recorder を実 PTY 上で subprocess 起動**（新規、`tests/test_interactive_terminal_recorder.py`）。
  test が `pty.openpty()` で作った slave を recorder の stdin / stdout にし、子に決定論的な fake TUI agent
  （python script）を渡す。
  - fake agent が出す 3000 行の一括出力、`\e[?1049h` 〜 `\e[?1049l` の alternate screen 200 行、
    `\e[3J` 後の出力が、`terminal.log` に **byte 列として期待値どおり**すべて残る。
  - 子の終了コード 7 が recorder の終了コードになる。signal 終了は `128 + signum`。
  - master 側から書いた入力が子の stdin に届く（stdin relay）。
  - recorder の環境に `HERDR_AGENT=codex` を付けて起動し、子が自分の環境に `HERDR_AGENT` が無いことを出力する。
  - recorder の stdin が TTY でない場合でも記録・終了コードが正しい（raw mode / winsize をスキップ）。
  - SIGWINCH: test 側 PTY の winsize を変えて recorder に SIGWINCH を送ると、子が新しいサイズを観測する。
  - 子 exec 失敗（存在しない command）→ 理由が log に残り、終了コード 127。
  - log を開けない path → 子を起動せず exit 2。
- **診断との結合**: fake agent が alternate screen 内で canonical transient pattern（例: `overloaded` 系の
  既存 pattern）を出して非ゼロ終了 → recorder の log に対し `read_terminal_diagnostic` が
  `provider_error` を返し、`_terminal_exit_detail` が pattern literal を含む。sensitive marker が同居する
  場合の併記も確認する（#296 の契約を recorder の記録で再検証）。
- **Herdr backend（fake herdr CLI、既存 `tests/test_interactive_terminal_herdr.py`）**: fresh / resume、
  timeout、早期終了（shell 復帰）、Ctrl-C、`close_on_verdict` true / false、pane 保持・prune、
  ownership-safe cleanup、launcher の一度だけの起動と start marker、cleanup 失敗が元の verdict / 例外を
  置き換えない、の既存テストを維持する。snapshot の `pane read` 呼び出しを前提にした fake 応答と
  assertion を削除し、代わりに「`pane read` を呼ばない」「異常終了の detail と `terminal_diagnostic` が
  recorder 側の `terminal.log`（test が事前に書いた raw byte）から作られる」ことを検証する。
  `TestTmuxEntryForwardsDeadlineToHerdr` は名前を実態に合わせる。
- **runner dispatch**（`tests/test_runner_interactive_dispatch.py`）: `interactive_terminal` 設定で Herdr
  runner が呼ばれる（`captured["backend"] == "tmux"` を置き換え）。`TestTmuxRunnerDispatch` を削除
  （Herdr 版の型名 → run.log の検証 `:168-177` は維持）。`test_both_backends_receive_identical_effective_workdir`
  は Herdr と headless の比較に書き換え、`TestAttemptDeadlineSingleClock` の parametrize から tmux を外す。
- **環境変数伝播**（`tests/test_tmp_env_propagation.py`）: parametrize から tmux を外し、Herdr で 4 変数が
  渡ることを維持する。
- **CLI 経路**: `kaji run` 相当の command 経路で、`herdr` 設定時に deprecation 警告が stderr に出ないこと、
  tracked / local の `tmux` 設定でファイル名付きのエラーになること。
- **recovery 互換**（既存維持）: `test_recovery_classify.py` の `TmuxSessionRequiredError` 型名分類、
  `test_recovery_incident_handler.py` の incident 抑止、`test_recovery_plan.py` / `test_recovery_signature.py` /
  `test_recovery_models.py` の過去 artifact 前提のテストを残す。旧 tmux 形式の `pane-metadata.json`
  （`pane_id: "%3"`, `tmux_version` 等）から `_orphan_pane_id` が pane ID を返すテストがなければ追加する。

### Large テスト

- **launcher → recorder → wrapper → fake agent の実 subprocess**（`tests/test_tmp_dir_e2e_large_local.py` の
  `test_herdr_launcher_applies_attempt_tmp_env` を拡張、`large` + `large_local`）: 実際に生成された
  `herdr-launcher.sh` を sh で起動し、PATH 上の fake `claude` が attempt TMP 環境を観測して verdict を書く。
  `terminal.log` に wrapper の banner と fake agent の出力（alternate screen を含む）が raw byte で残り、
  fake agent の環境に `HERDR_AGENT` が無いことを確認する。実 Herdr は使わない（CI で再現できる構成）。
- **attempt deadline E2E**（`tests/test_attempt_deadline_e2e_large_local.py`）: tmux クラスを削除し、Herdr /
  headless の deadline E2E を維持する。
- 削除: `tests/test_interactive_terminal_tmux.py`（private tmux server の real-server クラスを含む）、
  `test_tmp_dir_e2e_large_local.py::TestTmuxTmpEnvE2E`、
  `test_attempt_deadline_e2e_large_local.py::TestTmuxAttemptDeadlineE2E`、
  `tests/test_tmux_backend_deprecation.py`。残るテストが tmux のインストールを要求しないことは、
  `rg -n "tmux" tests/` の残存が recovery 互換・拒否テスト・文字列データだけであることと、
  `PATH` から tmux を外した環境で `make check` が通ることで確認する。
- 実 Herdr・実 agent の E2E は恒久テストにしない（Herdr pane 内の実行と各 agent の認証が必要で CI で
  再現できない）。#476 で 0.8.2 の実測があり、merge 後の確認項目（Herdr / agent version 明記の実機証跡、
  3 agent の recorder 記録、SSH 先ホスト）で確認する。

### 変更固有検証（恒久テストにしないもの）

- **tmux 削除前の突き合わせ**（実装順序 2。tmux が repo から消える前に実施）:
  1. private tmux server（`tmux -L kaji515-cmp -f /dev/null`）に pane を作り、`sh -c 'read _; exec <python> -I
     <recorder> rec.log -- <agent argv>'` を初期 command にする。`pipe-pane -o 'cat >> pipe.log'` を付けてから
     Enter を送り、recorder を起動する。
  2. 実行 A: 決定論的な fake TUI agent（3000 行、alternate screen 200 行、`\e[3J`、provider error 文字列、exit 7）。
  3. 実行 B: 実 Claude（`--model haiku`、`Reply with exactly PROBE-OK`、応答後 `/exit` を send-keys）。
  4. 判定基準: `rec.log` が `pipe.log` の末尾と byte 一致し、差分は recorder 起動前の入力 echo だけである。
     実行 A では各マーカー行の数（3000 / 200 / `\e[3J` 後）が期待値と一致する。
  5. 記録: tmux / Python / Claude の version、コマンド、`cmp` 結果、byte 数を
     `experiments/herdr-interactive-terminal/reports/<日付>-recorder-vs-pipe-pane.md` に残し、implement の
     Issue コメントに要約を載せる。不一致なら recorder を修正して 1 回だけ再実行する（上限 2 回。2 回目も不一致なら implement は `BACK`）。
- `make check`、`make verify-docs`、`make verify-packaging`（隔離環境で recorder module が wheel に含まれ、
  path 起動できることを確認）。
- `rg -n tmux` の残存参照を「旧設定の拒否」「過去 artifact の互換」「履歴」「upstream の機能説明」に
  理由付きで分類した表を final-check の Issue コメントに残す（ゼロ件を完了条件にしない）。

### 長時間 acceptance の配置

対象あり: tmux 削除前の突き合わせの実行 B は外部 agent（実 Claude）を含むため、(a) bounded 根拠で
`implement` step（timeout T = 3600 秒）に置く。nested full workflow、Herdr 上の 3 agent・nested kaji・SSH の
実機確認は workflow 内に置かない（Issue の「ワークフロー完了後の確認項目」）。

- 1 回の比較の所要見積: 実行 A（fake TUI agent、決定論的）約 30 秒 ＋ 実行 B（Claude haiku の起動・1 prompt・
  `/exit`）約 120 秒 ＋ tmux server 準備・`cmp`・後片付け約 30 秒 = 約 180 秒。根拠: #476 の実測で Claude の起動から
  2 回の prompt 応答と承認拒否までが約 15 秒（報告書「`HERDR_AGENT` ヒントの検証」の表）。余裕を見て 8 倍にした。
  1 回の所要は T の 25%（900 秒）未満。
- 実行回数の上限: 2 回（初回 ＋ recorder 修正後の再実行 1 回）。2 回目も不一致なら implement は修正を続けず
  `BACK`（設計見直し）を返す。acceptance 合計 360 秒。
- attempt 全体: 実装・失敗修正 1800 秒 ＋ acceptance 以外の検証（対象 pytest の反復、`make check` 2 回、
  `make verify-docs` / `make verify-packaging`、Pre-Handoff Review）720 秒 ＋ acceptance 360 秒 ＋ 報告・commit・
  verdict 保存 180 秒 ＋ 予備時間 R = min(max(300, 0.1 × 3600), 0.25 × 3600) = 360 秒 = **3420 秒 ≤ 3600 秒**。
- 突き合わせの結果は実装順序 2 の時点で report を commit するため、implement が後段で `RETRY` されても
  再実行は不要（tmux 削除後は再実行できないので、report が唯一の証跡になる）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/007-interactive-terminal-runner.md` | あり | 改訂履歴に v5（Issue #515）: tmux backend 削除、Herdr 唯一、recorder による raw 記録。ステータスと「決定」を Herdr 前提に更新し、v2〜v4 の tmux 決定を supersede（履歴は残す）。snapshot の記述を recorder に置き換え、サイズ・機密情報・macOS deferred を明記 |
| `docs/adr/008-no-backward-compat-layer.md` | なし | 決定は変えない。互換の扱いは本設計と ADR 007 で説明 |
| `docs/ARCHITECTURE.md` | あり | interactive terminal の構成図・説明（`:196,236-252,479,515` 付近）を Herdr + recorder に |
| `README.md` / `README.ja.md` | あり | 「tmux interactive terminal runner」節を Herdr 前提に。移行手順への導線 |
| `llms.txt` | あり | `:25`, `:48` の backend 説明 |
| `docs/reference/configuration.md` / `.ja.md` | あり | backend の受理値 `herdr` のみ、既定 `herdr`、`tmux` はエラー、両ファイルの修正 |
| `docs/cli-guides/interactive-terminal-runner.md` / `.ja.md` | あり | 全面改訂。`#tmux-backend-deprecation` を移行手順の節（`#migrating-from-tmux`）に置き換え、tracked config と `config.local.toml` の両方から `tmux` を削除・変更することを明記。`terminal.log` の記録方式・サイズ・機密情報、metadata の新フィールド |
| `docs/cli-guides/failure-recovery.md` / `.ja.md` | あり | `:28` の pane 終了説明を Herdr に。`:156` の `TmuxSessionRequiredError` は過去 run の型名として残す旨 |
| `docs/cli-guides/github-mode.md` / `.ja.md` | あり | `:86,99` |
| `docs/cli-guides/antigravity-cli-session-guide.md` | あり | `:15,91,95` |
| `docs/cli-guides/claude-code-cli-guide.md` / `.ja.md` | なし | upstream の `--tmux` 等の説明（スコープ外） |
| `docs/guides/python-starter.md` / `.ja.md` | あり | `:84,130-144,320` 付近の tmux 前提の手順 |
| `docs/dev/workflow-authoring.md` | あり | `:25,369,372` |
| `docs/dev/workflow_guide.md` | あり | `:213,219` の tmux エラー説明を Herdr に（`TmuxSessionRequiredError` は過去 run の型名として注記）、`:305` |
| `docs/dev/incident-labels.md` | あり | `:93` の例を Herdr セッション必須に（過去の型名も分類される旨） |
| `docs/reference/`（python 規約） | なし | 規約変更なし |
| `CHANGELOG.md` | あり | `[Unreleased]` `### BREAKING CHANGE` に ADR 008 の 3 要素（壊れる契約: 既定 backend と tmux の受理、影響の確認: `rg -n 'interactive_terminal_backend' .kaji/` と CLI 呼び出しの grep、移行: herdr か headless、両ファイルの修正、Herdr pane 内での実行）。`### Added` 相当で recorder による raw `terminal.log` |
| `.kaji/config.toml` | あり | コメント更新 |
| `AGENTS.md` / `CLAUDE.md` | なし | 規約変更なし |
| `experiments/herdr-interactive-terminal/` | あり（追加のみ） | 突き合わせの報告書と script を追加。既存報告書は履歴として変更しない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #515 本文 | https://github.com/apokamo/kaji/issues/515 | 完了条件（設定・CLI の契約、tmux 削除、recorder、Herdr 既存契約、docs）と「重要判断」表 |
| 親 Issue #466「決定事項」「2026-10-19 の追加決定」「初回削除リリースの設定契約」 | https://github.com/apokamo/kaji/issues/466 | 設定入力ごとの廃止後挙動、PTY recorder を #515 に含める人間決定、AI の仮定表（Herdr のみ適用、raw byte、設定キー追加なし、上限なし、EIO/EOF、`HERDR_AGENT`、tmux との突き合わせ） |
| #476 調査報告 | `experiments/herdr-interactive-terminal/reports/2026-10-17-full-transcript-feasibility.md` | `pane read` の実効上限 1000 行、alternate screen で終了後に会話が残らない（Q1・Q4）。Python PTY recorder が 3 agent で欠落なく記録し終了コードを保持（Q2 実測）。`HERDR_AGENT=claude/codex/agy` で状態遷移が直接起動と一致。「ヒントは recorder のプロセスだけに付け、global に export しない」。Linux は EIO、BSD は EOF。付録 A の試作 |
| Herdr docs（agent detection） | https://herdr.dev/llms-full.txt | "a host-visible wrapper can hide the real agent process from Herdr. Set `HERDR_AGENT=<agent>` on the wrapper command to tell Herdr which existing agent screen manifest to use"（#476 報告書 Q2 に引用） |
| Herdr CLI reference | https://herdr.dev/docs/cli-reference/ | `pane read` の行数指定（"without it, reads default to 80 rows"）。snapshot 方式の制約の根拠 |
| Herdr source（read 上限） | https://github.com/herdrdev/herdr/blob/v0.9.5/src/app/api_helpers.rs#L117 | `lines.min(1000)` による切り詰め |
| Python `pty` | https://docs.python.org/3/library/pty.html | `pty.fork()` の仕様、Availability: Unix |
| Python コマンドライン `-I` | https://docs.python.org/3/using/cmdline.html#cmdoption-I | isolated mode は `-E -P -s` を含み、script の directory と user site を `sys.path` に入れない |
| Python `os.waitstatus_to_exitcode` | https://docs.python.org/3/library/os.html#os.waitstatus_to_exitcode | signal 終了は負値を返す（`128 + signum` への変換の根拠） |
| PEP 475 | https://peps.python.org/pep-0475/ | `EINTR` で中断された system call の自動再試行 |
| ADR 007 | `docs/adr/007-interactive-terminal-runner.md` | v2 の `pipe-pane` raw 記録、`script(1)` を外した理由（OS 分岐）、v4 の Herdr 決定（CLI argv のみ、caller-context guard、launcher、ownership） |
| ADR 008 | `docs/adr/008-no-backward-compat-layer.md` | 互換レイヤを書かない、BREAKING の 3 要素、fail-safe は対象外 |
| 現行コード | `kaji_harness/config.py:58,472-484,510-514`、`commands/parser.py:152-158`、`commands/run.py:38-61,176-180`、`interactive_terminal.py`、`interactive_terminal_herdr.py:49,178,286-399,478-512,1029-1063,1197-1247`、`interactive_terminal_common.py:104-145,317-330`、`errors.py:157-176`、`recovery/classify.py:35`、`recovery/snapshot.py:286-314` | 既定 tmux、choices、非推奨警告、snapshot 取得、metadata、診断の読み取り、型名文字列による分類、backend 非依存の orphan pane ID 読み取り |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L の定義、`large_local` marker、恒久テストと変更固有検証の切り分け |
