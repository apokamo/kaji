# Interactive Terminal Runner

Language: [English](interactive-terminal-runner.md) | 日本語

`kaji run` の agent step を、headless CLI ではなく **Herdr pane 上**の通常 `claude` / `codex` /
Antigravity（`agy`）対話 CLI で実行する runner backend（Issue #224 / #230 / #396 / #515）。agent が
attempt directory の `verdict.yaml` を書いたら、kaji は artifact-primary 経路
（[ADR 005](../adr/005-artifact-primary-verdict.md)）で verdict を読み、次 step へ進む。

Herdr が唯一の interactive terminal backend である。tmux backend は 0.23.0 で非推奨化した後、
Issue #515 で削除した（[Migrating from tmux](#migrating-from-tmux)）。kaji は release-matched Herdr CLI と
明示 pane ID を使い、Herdr pane 内で起動した `kaji run` の origin pane の横に agent pane を追加する。
ディスプレイ無し環境（WSL2 / SSH 先ホストで Herdr を起動する場合）でも `kaji run` の出力と agent を
同一画面で並列に見られる。pane 配置は **初回のみ origin pane の右、2枚目以降は右列内の上下分割**で、
kaji が作成した agent pane は右列に**最大2枚**まで残す（Issue #238）。

各 agent は kaji 所有の Python PTY recorder 経由で起動するため、`terminal.log` には agent が書いた
raw PTY byte（alternate screen の出力を含む）が残る（Issue #515。
[Terminal log（`terminal.log`）](#terminal-logterminallog) 参照）。

技術選定の経緯は [ADR 007](../adr/007-interactive-terminal-runner.md)、runner dispatch の
位置づけは [ARCHITECTURE](../ARCHITECTURE.md) § Runner backend dispatch を参照。

## いつ使うか

- 従量課金の headless 経路ではなく、通常コンソール利用に近い形で workflow を進めたいとき。
- ディスプレイ無し環境（WSL2 / SSH 先ホストの Herdr）でも `kaji run` 出力と agent を同一画面で並列に見たいとき。
- step ごとに Claude / Codex の session を `--resume` / `codex resume` で引き継ぎたいとき。
- `agy -i` で Antigravity の単発 step を実行しつつ、kaji の artifact 完了契約を使いたいとき
  （Antigravity は `resume:` 非対応）。
- verdict 後も agent の最終状態を pane で確認したいとき（`close_on_verdict = false`）。pane を残しても
  右列は最新2枚に保たれるため、step ごとに幅が縮み続けない。

`agent_runner` 未指定時は従来どおり `headless` runner が使われる。既存 workflow / CI の挙動は変わらない。

## Migrating from tmux

tmux backend は**削除済み**（Issue #515、親 Issue #466。0.23.0 で非推奨化、Issue #475）。interactive
terminal backend は Herdr のみで、headless runner は引き続き使える。

### 変わったこと

- `.kaji/config.toml` または `.kaji/config.local.toml` の `interactive_terminal_backend = "tmux"`
  （per-key merge 後）は config 読み込み時に `ConfigLoadError` になり、値を定義したファイルを示す。
  **headless 実行でも**失敗する。`--agent-runner headless` / `--interactive-terminal-backend herdr` では
  **救済できない**ため、ファイルを修正する必要がある。
- `--interactive-terminal-backend tmux` は移行案内付きの usage error（exit 2）で拒否される。その他の
  未知の値は argparse 標準の `invalid choice` になる。
- `agent_runner = "interactive_terminal"` で backend 未指定の場合の既定は `herdr` になったため、
  `kaji run` は Herdr pane 内で実行する必要がある。Herdr 外からは `HerdrSessionRequiredError` になり、
  headless への暗黙 fallback や terminal の自動検出は行わない。

### 影響を受けるかの確認

両方の config ファイルと script を検索する:

```bash
grep -nE 'interactive_terminal_backend|agent_runner' .kaji/config.toml .kaji/config.local.toml
grep -rnE -- '--interactive-terminal-backend|--agent-runner' <your-scripts>
```

検索は目安にすぎない（quote や動的に組み立てた引数は検出できない場合がある）。代表的な `kaji run` を
1 回実行すれば確定でき、影響を受ける config は読み込み時に修正すべきファイル名付きで失敗する。

### 移行方法

- **Herdr**: Herdr をインストールし（[前提](#前提)）、`interactive_terminal_backend = "herdr"` にする
  （またはキーを削除する）。`kaji run` は Herdr pane 内で起動する。
- **Headless**: `agent_runner = "headless"` にし（または `agent_runner` を削除し）、
  `interactive_terminal_backend = "tmux"` を削除する。terminal multiplexer は不要。
- どちらの場合も、tracked の `.kaji/config.toml` **と** gitignored の `.kaji/config.local.toml` の
  **両方**から `"tmux"` の値を削除・変更し、script から `--interactive-terminal-backend tmux` を外す。
  エラーが示すのは現在 merge で勝っているファイルなので、そのファイルだけ直すと次の実行でもう一方の
  ファイルの値が見える場合がある。

過去の tmux run の artifact は診断用に引き続き読める。`run.log` に残る過去の
`TmuxSessionRequiredError` は user precondition error として分類され、旧 tmux 形式の
`pane-metadata.json`（`pane_id: "%N"`）からも orphan pane ID を読める。

## 前提

- Herdr **>= 0.8.2** が PATH 上にあり、client/server protocol が互換であること。`HERDR_BIN_PATH` が
  実行可能ファイルを指す場合、kaji は PATH 探索より release-matched CLI を優先する。
- `kaji run` を Herdr 内で実行すること。`HERDR_ENV=1` と `HERDR_PANE_ID` が必須で、外部 caller は
  `HerdrSessionRequiredError` になり UI focus 中の session を操作しない。preflight は `herdr status` も
  実行し、`pane current --current` が `HERDR_PANE_ID` と完全一致することを要求する。この失敗は既知の
  利用者側前提条件エラーとして扱い、インシデント Issue を起票しない（triage コメントと run artifact は
  残る。[failure-recovery.ja.md](./failure-recovery.ja.md)）。
- 選択した `claude` / `codex` / `agy` CLI が PATH 上にあること。
- kaji はインストール済み CLI を argv list で呼び出し、JSON 応答の ID を parse し、pane ID を予測しない。
  raw socket client、plugin、Herdr agent integration は不要。
- 作成した pane には `kaji_origin` / `kaji_run` / `kaji_step` metadata token を付ける。cleanup は対象 pane を
  再読込し、現在の origin/run ownership を確認してから閉じる。
- wrapper は agent 起動前に `NO_COLOR` を unset し `COLORTERM=truecolor` を設定する。親 shell に
  `NO_COLOR=1` があっても、interactive terminal runner 内の agent は truecolor 出力を使える。
- PTY recorder は Python 標準ライブラリ（`pty` / `termios` など）だけを使い、kaji 自身の interpreter で
  isolated mode（`-I`）起動する。Unix 専用で、Linux（WSL2 を含む）で検証済み。macOS 実機での検証は別途。

## 設定

`[execution]` は workflow YAML ではなく **repository config** に置く。`.kaji/config.toml` の探索規則は
[設定リファレンス](../reference/configuration.md#discovery-rule) を参照。

tracked default は `.kaji/config.toml`、個人環境だけの切り替えは gitignored の `.kaji/config.local.toml` に書く。

```toml
# .kaji/config.toml または .kaji/config.local.toml
[execution]
default_timeout = 2400
agent_runner = "interactive_terminal"            # "headless"（既定）| "interactive_terminal"
interactive_terminal_backend = "herdr"           # "herdr" のみ（既定）。"tmux" はエラー
interactive_terminal_close_on_verdict = true     # 既定 true
```

`agent_runner` / `interactive_terminal_backend` が許容値以外の場合、config 読み込み時に `ConfigLoadError`
で fail-fast する。`[execution]` キー（型 / 既定値 / 検証）の網羅仕様は
[設定リファレンス](../reference/configuration.md#execution) を正とする。

### Antigravity の execution policy

runner は workflow の `execution_policy` を wrapper の第9引数として渡す。Antigravity case では次のように
写像する。

| policy | `agy -i` flag | permission の挙動 |
|--------|---------------|-------------------|
| `auto` | `--dangerously-skip-permissions` | tool request を自動承認 |
| `sandbox` | `--sandbox` | containment を有効化。TUI 承認は残る |
| `interactive` | なし | AGY 既定と TUI 承認 |

sandbox と permission は AGY の別の制御である。`sandbox` の写像は permission bypass flag を付けない。

### Codex の skill invocation

runner は wrapper の第10引数 `skill_invocation` を渡す。Codex では `$<skill> を実行してください。` 行
（`build_prompt` が `prompt.txt` 先頭に置くのと同じ行）、Claude と Antigravity では空文字列である。
空でない場合、wrapper は agent に渡す初期メッセージの先頭にこの行と空行を付ける。Codex は `$<skill>`
mention を `prompt.txt` 内ではなく user 入力でだけ解決するためである。空の場合、初期メッセージは変わらない。

### overlay

`.kaji/config.local.toml` の `[execution]` は `.kaji/config.toml` の同名キーを **key 単位**で上書きする
（[設定リファレンス](../reference/configuration.md#overlay-merge-rule)）。tracked config が
`agent_runner = "headless"` でも、overlay に1キー書くだけで個人環境だけ切り替えられ、`default_timeout` は
tracked config の値が残る。

```toml
# .kaji/config.local.toml （gitignored, 個人環境）
[execution]
agent_runner = "interactive_terminal"
```

## CLI option（`kaji run`）

```bash
--agent-runner <headless|interactive-terminal>
--interactive-terminal-backend <herdr>
--interactive-terminal-close-on-verdict
--no-interactive-terminal-close-on-verdict
```

- `--agent-runner interactive-terminal`: この実行だけ interactive terminal runner を使う（config 値
  `interactive_terminal` に正規化。公開 CLI 値はハイフン区切りのみ）。
- `--agent-runner headless`: config が interactive terminal でも、この実行だけ headless を使う。
- `--interactive-terminal-backend`: `herdr` のみ受理する。interactive runner を暗黙に有効化しない。
  `tmux` は移行案内付きで拒否される。
- `--interactive-terminal-close-on-verdict` / `--no-...`: この実行だけ close-on-verdict を上書きする。
  どちらも指定しなければ config 値を維持する。

### 設定の優先順位（固定）

1. `kaji run` CLI option
2. `.kaji/config.local.toml` `[execution]`
3. `.kaji/config.toml` `[execution]`
4. 組み込み既定（`agent_runner = "headless"`, `interactive_terminal_backend = "herdr"`,
   `interactive_terminal_close_on_verdict = true`）

どちらかの config ファイルに `"tmux"` があると、CLI option を適用する前の config 読み込みで失敗する。

### 使用例

```bash
# Herdr pane 内で実行する（HERDR_ENV / HERDR_PANE_ID が無いと fail-fast）

# repository config で interactive_terminal を既定にして実行
kaji run .kaji/wf/official/dev.yaml 224

# この実行だけ interactive terminal + pane を残す
kaji run .kaji/wf/official/dev.yaml 224 \
  --agent-runner interactive-terminal \
  --no-interactive-terminal-close-on-verdict

# この実行だけ headless に戻す（Herdr 不要）
kaji run .kaji/wf/official/dev.yaml 224 --agent-runner headless
```

## 起動コンソール progress（Issue #235）

interactive terminal runner では agent の作業が pane に出るため、`kaji run` を実行した元の pane（起動
コンソール）は無音に見えがちである。Issue #235 以降、harness は stdlib `logging` で時刻付きの `[kaji]`
progress を起動コンソールに出す。

```text
[2026-06-07T12:34:57] [kaji] workflow start: dev issue #224
[2026-06-07T12:34:57] [kaji] step start: design attempt-001 dispatch=agent agent=claude model=opus
[2026-06-07T12:35:02] [kaji] pane launched: step=design agent=claude pane=w1-3 timeout=1800s verdict=/.../steps/design/attempt-001/verdict.yaml
[2026-06-07T12:42:10] [kaji] verdict detected: design source=artifact status=PASS
[2026-06-07T12:42:10] [kaji] step end: design status=PASS duration=433000ms next=review-design
[2026-06-07T12:42:10] [kaji] workflow end: status=COMPLETE duration=...ms
```

- `INFO` 以下は stdout、`WARNING` 以上は stderr に出る。
- `--log-level {DEBUG,INFO,WARNING,ERROR}`（既定 `INFO`）で表示閾値を制御する。
- `--quiet` は agent / exec の stdout streaming（pane / exec relay）を抑止するが、`[kaji]` progress には
  影響しない（独立）。harness progress だけ抑えたい場合は `--log-level WARNING` を使う。
- `review-poll` などの決定論的 exec step は pane を開かない。polling 中は `POLL_INTERVAL_SEC`（10秒）ごとに
  経過秒、PR 番号、短縮 head、観測状態、timeout 残りを含む `[review-poll]` heartbeat を起動コンソールへ
  flush する。`run.log` を開かずに待機・停止・エラーを区別できる。

```bash
# harness progress を抑えて警告/エラーのみ表示
kaji run .kaji/wf/official/dev.yaml 224 --log-level WARNING
```

## 振る舞い

1. preflight は binary 解決前に `HERDR_ENV=1` と `HERDR_PANE_ID` を検証し、続いて Herdr >= 0.8.2 と
   `pane current --current` の完全一致を検証する。pane 作成前に、packaged wrapper、kaji の Python
   interpreter、recorder module が存在することも確認する。
2. kaji は token 所有の pane と origin の layout を読む。初回は右、以降は下方向に pane を開き、最大2枚を
   残す。prune は閉じる前に現在の origin/run token を再読込する。run token / layout が欠けた、または
   ownership が変わった stale 候補は skip し、警告を log に出して `kaji_agent_panes_skipped` に ID を
   記録する（以後の step をすべて止めない）。
3. split は明示 cwd と `--no-focus` を使う。kaji は応答から得た pane に marker を付け、attempt directory に
   private（`0700`）な `herdr-launcher.sh` を atomic に公開し、短い `<launcher path>` の child command を
   1 回だけ送る。shell は存命判定用に pane の shell process として残り、継承した PATH と長い argv は
   launcher 内に留まる。このため正しさは新しい shell の観測できない入力モードに依存しない。launcher は
   recorder を exec する前に private な start marker を atomic に公開し、kaji は固定上限内でそれを待つ。
   marker 前の shell-only 観測は agent 終了と数えない。launcher 作成、pane-run、起動確認の失敗では所有
   pane を cleanup して fail loud する。ownership marker の失敗では所有していない pane に触れない。
4. launcher は `HERDR_AGENT=<claude|codex|agy> <python> -I <recorder> <terminal.log> -- <wrapper> <10 引数>`
   を実行する。`HERDR_AGENT` は recorder プロセスだけに付け（kaji の `antigravity` は Herdr の `agy`
   manifest に写像）、Herdr の agent 検出（idle / working / blocked / done）を直接起動と同じにする。
   recorder は子の環境から `HERDR_AGENT` を取り除くため、nested kaji へ漏れない。
5. wrapper はまず `cd <workdir>`（trusted な project worktree。`/tmp` や attempt directory ではない）を
   行い、通常の `claude` / `codex` / `agy` を起動する（Codex には `--cd <workdir>` も渡す）。wrapper は
   prompt 全文を埋め込まず、`prompt.txt` を読み `verdict.yaml` に pure YAML を書くよう指示するだけである。
6. 完了トリガは `verdict.yaml` のみ。foreground process の観測は shell への早期復帰の検出にだけ使い、
   出力・status text・recorder の終了コードで step を完了させない。optional な process field の欠落・
   不正は unknown として扱い、shell 復帰の確定とは数えない。
7. verdict 後の cleanup は `interactive_terminal_close_on_verdict` に従い、失敗時の cleanup は ownership を
   確認する。close 失敗は警告として `close_error` に記録し、verdict、元の失敗 / timeout、解決済み session を
   置き換えない。早期終了と timeout の metadata には、recorder の `terminal.log` から作った構造化
   `terminal_diagnostic` も入る。
8. kaji pane での操作者の Ctrl-C（`KeyboardInterrupt`）では agent pane を残し、ownership を
   `pane-metadata.json` に記録して再送出する。metadata 保存に失敗しても元の割り込みが優先される。確認後に
   `herdr pane close <pane_id>` で手動で閉じる。agent pane 内で打った Ctrl-C は別経路で、recorder が
   `\x03` として agent の PTY へ転送し、agent は直接起動時と同じく SIGINT を受ける。

### Terminal log（`terminal.log`）

- **形式**: 子（wrapper → agent）が PTY に書いた raw byte をそのまま追記したもの。ANSI escape、alternate
  screen の内容（`\e[?1049h` 以降）、`\e[3J` 以降の出力を含む。旧 tmux `pipe-pane` の log と同じ形式で、
  wrapper の banner も記録される。
- **書き込み**: chunk ごとに pane へ mirror する前に unbuffered で追記する（`O_APPEND`）。recorder や pane が
  強制終了されても、それまでに読んだ chunk は残る。log は agent の起動前に開き、agent の exec 失敗も PTY
  経由で log に残る（終了コード 127）。
- **サイズ上限・ローテーションなし**。`close_on_verdict = false` では、step 後も agent が生きている間は
  追記が続く。
- **機密情報**: raw PTY の記録には agent が画面に出したものがすべて入る。ファイルは従来どおりプロセスの
  umask に従う。attempt directory の扱いに注意する。
- **記録状態**（`terminal.log.state`、内部契約）: recorder は agent 起動前に `recording`、子の EOF 後に
  `complete`、log 書き込み失敗時に `incomplete <ERRNO>`（例 `ENOSPC`）を書き、以後は記録を止めて pane の
  relay を続ける。状態を上書きできなければファイルを削除し、harness は `unknown` と報告する。残余リスク:
  上書きと削除の両方に失敗した場合だけ、byte が欠落しても `recording` が残る。

### Pane metadata（診断）

attempt directory の `pane-metadata.json` には ownership、配置診断（`layout_target_pane`、
`split_target_pane`、`split_direction`、`kaji_agent_panes_before`、`kaji_agent_panes_pruned`、
`kaji_agent_panes_skipped`）、transcript metadata を記録する。

| キー | 意味 |
|------|------|
| `transcript_kind` | `"raw_pty_recording"` |
| `transcript_available` | metadata 書き込み時点で `terminal.log` が通常ファイルとして存在するか |
| `transcript_recorder_state` | `"recording"` / `"complete"` / `"incomplete"` / `"unknown"`（状態ファイルが無い・読めない・不正） |
| `transcript_truncated` | `recording` / `complete` は `false`（既知の欠落なし）、`incomplete` は `true`、`unknown` は `null`。log が無ければ出力しない |
| `transcript_error` | `incomplete` のときだけ errno 名（例 `ENOSPC`）。transcript の内容は含めない |
| `transcript_revision` | 常に `null`（recorder に revision 概念はない）。log が無ければ出力しない |
| `transcript_bytes` | metadata 書き込み時点の `terminal.log` の byte 数。log が無ければ出力しない |

記録状態が `incomplete` / `unknown` のとき、異常終了の error detail に
`; transcript may be incomplete (recorder state: <state>)` を付ける。

### Codex / Claude Codeからkajiを起動

Herdr 内で動いている agent から repository skill `herdr-kaji-launch` を使う。Claude と Codex は
`.claude/skills/herdr-kaji-launch` と `.agents/skills/herdr-kaji-launch` symlink で正本の手順を共有する。
skill は release-matched の `herdr --skill` を読み、明示した caller pane から split し、cwd / focus を保ち、
kaji を通常の対話 command として実行する。Claude Code の `-p` / print mode や Herdr の `agent start` は
使わず、kaji pane は開いたまま残す。plugin は任意の人間向け起動 UX で、core 依存ではない。

### session 継続

- **Claude**: fresh 実行では UUID を生成して `--session-id` に渡し、同じ UUID を session state に保存する。
  resume step は保存した id を `--resume` に渡す。
- **Codex**: fresh 実行後、`terminal.log` から `codex resume <uuid>` を抽出する。取れなければ
  `CODEX_HOME/sessions/**/*.jsonl`、次に `~/.codex/sessions/**/*.jsonl` を mtime 降順で走査し、attempt の
  `prompt.txt` / `verdict.yaml` path を含む rollout file の UUID を採用する。resume step は
  `codex resume <uuid>` で起動する。収集の grace 待ちはなく、verdict 経路は `terminal.log` を 1 回読んでから
  session store に fallback する。
- **Antigravity**: 公開 session ID を取得しない。result は常に `session_id=None` で、
  workflow の `resume:` は起動前に拒否する。

#### 異常終了経路（timeout / pane-dead）の解決規則（Issue #403）

verdict なしで終わった attempt でも、その attempt に**検証可能に**結び付く session ID があれば
`result.json` に記録する（診断と手動 resume 判断用。自動 resume はしない）。規則は verdict 検知経路と
異なる。Herdr の shell 復帰の確定は `pane-dead` 行を使う。container pane は ownership 確認付き cleanup まで
残るが、起動した agent process は終了しているためである。

| Agent | Resume 入力 | 経路 | 解決 |
|---|---|---|---|
| codex | なし / あり | timeout / pane-dead | session store の rollout が exactly one 一致した場合だけ採用。0件 / 複数 / 読めない場合は `null`（親 id に fallback しない） |
| claude | なし | timeout | runner が発行した `--session-id` UUID |
| claude | なし | pane-dead | `null`（dead pane には起動失敗も含まれ、UUID が存在しない session を指しうる） |
| claude | あり | timeout / pane-dead | resume 入力の id（`--resume` は同じ session を継続する） |
| antigravity | — | すべて | `null` |

- codex の走査は `CODEX_HOME/sessions/**/*.jsonl` のうち **`prompt.txt` の mtime 以降**に更新されたものに
  限り、本文に attempt marker（`prompt.txt` / `verdict.yaml` の絶対 path）を含むものを数える。`resume:`
  step では親 rollout も marker を含むが、attempt 開始前に更新が止まっているため除外される。
- 異常終了経路は session ID のために `terminal.log` を読まない（timeout 時は Codex の resume 行がまだ出て
  おらず、transcript が非常に大きくなりうる）。grace 待ちも追加しない。
- **verdict 検知経路は既存規則を維持する**（mtime 降順の最初の一致）。Codex の `resume` は履歴を継承した
  新しい rollout を作るため、同じ marker が正当に複数一致しうる。ここで一意性を強制すると既存の
  `resume:` step が `MissingResumeSessionError` で退行する。

### effort の注意（Codex）

Codex の `reasoning.effort = minimal` は現在の tool 構成（`image_gen` / `web_search`）と衝突するため、
実用上の最小値は `low`。runner / wrapper は effort をそのまま渡す。最小値の選択は workflow step と
手動検証側の責務である。

## 手動検証手順（real Herdr + real Claude / Codex / Antigravity）

> 自動テストは、recorder の実 PTY 動作と launcher → recorder → wrapper の連鎖を fake agent で Large
> （`large_local`）、Herdr backend を fake `herdr` CLI で Medium として扱う。実 Herdr と実 `claude` /
> `codex` / `agy` の疎通は、Herdr pane と agent の認証が必要で API 課金も生じうるため、**意図的に
> 自動化していない**。次の手順で手動確認する。

検証は **project worktree 内**かつ **Herdr pane 内**で行う。`/tmp` や attempt directory を cwd にしない。

検証用モデル / effort（低コストの選択）:

- Claude: `haiku` / `low`
- Codex: `gpt-5.4-mini` / `low`
- Antigravity: 利用可能なモデル / `low`

手順:

1. Herdr を起動して pane を開く（ローカル、WSL2、または SSH 先のホスト）。
2. `.kaji/config.local.toml` に `[execution] agent_runner = "interactive_terminal"` を設定する
   （または `kaji run ... --agent-runner interactive-terminal`）。
3. 最小 workflow を `kaji run` で開始し、origin pane の右に pane が開いて通常の `claude` が起動することを
   確認する。
4. agent が `verdict.yaml` を書いた後に kaji が次 step へ進むことを確認する（**Claude fresh**）。
5. resume step が同じ session id（`--resume <uuid>`）で起動することを確認する（**Claude resume**）。
6. Codex でも同様に、fresh で `verdict.yaml` が書かれ（**Codex fresh**）、resume が `codex resume` で起動する
   こと（**Codex resume**）を確認する。
7. `interactive_terminal_close_on_verdict = true` では verdict 後に pane が閉じ、`false`
   （`--no-interactive-terminal-close-on-verdict`）では残ることを確認する。
8. attempt directory の `terminal.log` に会話（alternate screen の出力を含む）が残り、`pane-metadata.json` の
   `transcript_kind` が `"raw_pty_recording"` であることを確認する。
9. `--no-interactive-terminal-close-on-verdict` で **3 個以上の agent step** を連続実行し、右列の kaji 管理
   pane が**最大2枚**に保たれることを確認する。手動で作った無関係な pane が誤って閉じられないことも確認する。

結果には Herdr と agent の version を併記する。

## トラブルシュート

| 症状 | 原因 / 対処 |
|------|-------------|
| `config error (...): execution.interactive_terminal_backend = 'tmux' is no longer supported` | tmux backend は削除済み。示されたファイルを修正し、もう一方の config ファイルも確認する（[Migrating from tmux](#migrating-from-tmux)）。CLI option では上書きできない |
| `argument --interactive-terminal-backend: the tmux backend was removed`（exit 2） | `herdr` を指定するか `--agent-runner headless` を使う |
| `CLI 'herdr' not found` で即終了 | Herdr >= 0.8.2 をインストールするか headless runner を使う |
| `must run inside Herdr`（`HerdrSessionRequiredError`）で即終了 | Herdr pane 内で kaji を起動するか `agent_runner = "headless"` にする。外部から focus 中 session を操作することは意図的に拒否している |
| `HERDR_PANE_ID is not set` | caller の識別が明示されるよう、通常の Herdr pane で command を再実行する |
| `transcript may be incomplete (recorder state: ...)` | recorder が `terminal.log` の一部を書けなかった（`incomplete`。`transcript_error` を参照。例: disk full）か、状態を確認できなかった（`unknown`）。agent の実行自体には影響しない |
| step が timeout する | agent が `verdict.yaml` を書かなかった。prompt の verdict 書き込み指示と path を確認する |
| `Herdr pane returned to its shell before writing verdict.yaml` | agent の起動失敗または早期終了。error に添付された `terminal.log` の末尾を確認する |
| 色が出ない | wrapper は `NO_COLOR` を unset し `COLORTERM=truecolor` を設定する。端末の色対応と agent 側の設定も確認する |
| Codex resume が効かない | `terminal.log` に resume 行がなく、session store の fallback も marker に一致しなかった。`CODEX_HOME` を確認する |
| CLI が trust / permission の確認で止まる | cwd が project 外（`/tmp` など）。workdir を project worktree に固定する |
