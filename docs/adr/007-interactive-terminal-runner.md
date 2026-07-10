# ADR 007: interactive_terminal runner（terminal backend は tmux）

## ステータス

承認 (2026-06-07) — Issue #229 / #227 の real-agent PoC 検証（WSL2 / tmux 3.4 / claude haiku + codex gpt-5.4-mini）で
terminal backend = `tmux` 単一の実現性を確定し、本版を承認に確定した。検証詳細は
`tmp/2026-06-tmux-lifecycle-real-agent-verification/report.md`。
旧: 改訂提案 (2026-06-06) — Issue #229 の PoC 検証後に承認へ確定する。
旧: 承認 (2026-06-05) — terminal backend を `kitty` 単一とする初版決定（本 ADR で supersede）。

### 承認時の確認事項（2026-06-07）

- **close_on_verdict=true の pane 終了**: real-agent live 観測で挙動を確定。終了トリガは
  `verdict.yaml` の出現であり agent プロセスの自然終了ではない（verdict 時点で agent CLI は
  `pane_dead=0`＝生存）。`completion_barrier=verdict` 既定のため `post_verdict_timeout` の 30s grace
  は適用されない。**`kill-pane` は best-effort cleanup でありレイテンシ契約を持たない** — verdict
  検知後に pane を killする（観測上は ~1s 以内だが、次 step 開始後に killしても問題なく、速さに
  利得はない）。Codex fresh で session id 未解決時の session-id 回収 grace（≤5s）を挟むことも許容。
  検証は `#{pane_dead}` / `list-panes` の高頻度サンプリングで実施。
- **実行 pane の配置**: ユーザー視点で**右**に追加する（下記スコープ参照）。Issue #238 で、初回のみ
  origin pane の右、2枚目以降は右列内の上下分割とし、kaji が作成した agent pane は右列に最大2枚まで
  に制限する配置へ更新した（§ 改訂履歴 v3）。
- **macOS**: tmux 版が WSL で安定動作することをもって当面の確認とし、full macOS 実機検証は
  WSL 安定後に別 Issue で実施する（deferred）。

## 改訂履歴

| 版 | 日付 | 決定 | 備考 |
|----|------|------|------|
| v1 | 2026-06-05 | terminal backend = `kitty` 単一 | GUI ウィンドウを spawn して並列可視性を得る前提。`/proc` cmdline scan による cleanup、util-linux `script(1)` による transcript |
| v2 | 2026-06-06 | terminal backend = `tmux` 単一 | 「kaji を tmux 内で起動 → runner が `split-window` で pane 追加」で**並列可視性をディスプレイ無しで再現できる**と判明し、v1 の前提が崩れたため改訂 |
| v3（本版） | 2026-06-08 | pane 配置を「初回右・以後右列内・最大2枚」に変更、最小 tmux を 3.1 に引き上げ | v2 の「毎 step で現在 pane の右に追加」は `close_on_verdict=false` で pane を残すと横幅が step ごとに狭くなる。Issue #238 で、初回のみ origin の右、2枚目以降は右列内の上下分割、右列の kaji 管理 pane を最大2枚に制限する配置へ更新。pane を kaji marker（pane user option）で識別するため最小 tmux を 3.1 に引き上げ |

v2 / v3 は v1 の **runner 抽象・verdict 解決経路・session 継続方針を維持**し、terminal backend の実体だけを
`kitty` → `tmux` に差し替える。`agent_runner = "interactive_terminal"` という設定面・workflow 契約・
ADR 005 artifact-primary verdict 解決は不変。v3 は v2 の pane 配置契約のみを更新し、その他の決定は維持する。

## コンテキスト

kaji の harness は agent step を headless CLI として起動してきた（Claude Code は
`claude -p --output-format stream-json --verbose`、Codex は `codex exec --json`）。
この経路は harness が stdout / JSONL を直接読むことを前提にしており、通常の対話
サブスク枠での CLI 利用とは分離される可能性がある。サブスク範囲の通常 CLI 利用に
近い形で workflow を進めたい利用者にとって、headless 経路は最適ではない。

ADR 005（Issue #220 / PR #221）で **artifact `verdict.yaml` を primary とする verdict
解決**（`resolve_verdict()`）と **`runs/<run_id>/steps/<step_id>/attempt-NNN/` layout**
が実装され、stdout を直接読まずに step 完了を判定できる土台が既にある。この土台の上に、
別 pane で通常 CLI を起動する runner を追加できる状態になった（Issue #224）。

### v1（kitty）の前提と、その崩壊

v1 は「agent を **別 OS ウィンドウ**に出して、`kaji run` の端末と並列に見せる」ことを
terminal backend の主目的に置き、それを最も単純に満たすのは `kitty` 単独だと判断した。
この前提は次の2つの OS 依存を実装に持ち込んでいた:

1. **cleanup**: `kitty` が detached child を spawn し親子関係で追えないため、`/proc/*/cmdline`
   を marker 文字列で scan して killpg する経路（`_kill_processes_matching` 約55行）。
   **macOS に `/proc` が無い**ため Linux 専用。
2. **transcript**: util-linux `script(1)` 依存。macOS native `script` は BSD 構文で GNU long
   option 非対応のため、doc 上 macOS では transcript 無し（OS 分岐が残る）。

その後の検討で、**`kaji run` を `tmux` の中で起動していれば、runner は GUI ウィンドウを
spawn する代わりに `tmux split-window` で現在ウィンドウに pane を追加でき、`kaji` の出力と
agent の様子を同一画面で並列に見せられる**ことが分かった。これは v1 が `kitty` でしか
得られないとした「並列可視性」を**ディスプレイ無し（WSL2 / SSH / headless 可）で再現**する。

さらに backend を `tmux` にすると、上記2つの OS 依存が**両方消える**:

- cleanup → pane id をハンドルに `tmux kill-pane -t %id` 一発。`/proc` scan 不要。
- transcript → `tmux pipe-pane` で記録。`script(1)` の util-linux 判定と Linux/BSD 分岐が消える。

結果、v1 が「macOS を含む対象環境を最も単純にカバーできるのは kitty」とした根拠は**反転**し、
`tmux` の方が移植性が高く（Linux / macOS 同一実装）、cleanup / transcript のコードも短くなる。

## 決定

repository config の `[execution] agent_runner`（または `kaji run --agent-runner`）で選べる
runner backend `interactive_terminal` の terminal backend を **`tmux` 単一**とする（Issue #229）。

- **terminal backend は `tmux` 単独**。`kitty` その他の terminal は使わない。広い backend 抽象
  （`wt.exe` / `wezterm` / `gnome-terminal` / `kitty` への選択式）は **作らない**（§ 代替案）。
- runner（`execute_interactive_terminal()`）は **`kaji run` が tmux session 内で起動されている
  こと（`$TMUX` が設定されていること）を前提**とする。`$TMUX` が無ければ step failure として
  **fail-fast**（「`tmux` 内で `kaji run` を実行してください」）。自動 fallback はしない。
- runner は現在ウィンドウに pane を作って wrapper を起動する:
  `tmux split-window -d <-h|-v> -P -F '#{pane_id}' -t <split target> <wrapper.sh> <args>` で **pane id を
  回収**し、それを以後のライフサイクルのハンドルにする。`-d` でフォーカスを奪わない。
  **配置（v3 / Issue #238）**: kaji が作成した agent pane が右列に0枚なら origin pane を `-h` で右に分割し
  右列を作る。1枚あれば右列の pane を `-v` で上下分割して2枚目を作る。2枚以上ある場合は、`pane_top`
  昇順で最古（上側）の管理対象 pane を `kill-pane` してから残った最新 pane を `-v` で分割し、右列を
  常に最新2枚相当に保つ。これにより `close_on_verdict=false` で pane を残しても横幅が step ごとに
  狭くならない。
- **kaji 管理 pane の識別**: 作成直後の pane に pane user option `@kaji_interactive_terminal`
  （値 `origin=<origin pane id>`）を `set-option -p` で付与する。launch 前に `list-panes` で同一 window
  の pane を列挙し、marker が origin 一致する pane だけを管理対象として prune 候補にする。marker 設定に
  失敗した場合は作成済み pane を best-effort `kill-pane` してから `CLIExecutionError` で fail-loud する
  （識別不能な pane を残さない）。`list-panes` / pane lookup が失敗した場合も `CLIExecutionError` で
  fail-loud する（壊れた tmux state で誤 cleanup するより停止を優先）。最古判定は wall-clock ではなく
  `pane_top` 昇順で行う。ユーザーが手動で agent pane を active にした後にその pane が prune 対象に
  なった場合、active pane が tmux 通常挙動で移ることは許容する（「自動作成時に origin からフォーカスを
  奪わない」ことのみを契約とし、ユーザー操作後の active 復帰は範囲外）。
- 完了判定は ADR 005 の artifact-primary 経路。runner は attempt directory の `verdict.yaml` を
  polling し、出現したら verdict を読んで次 step へ進む。stdout は読まない
  （`CLIResult.full_output=""`）。pane の存命は `#{pane_dead}` / `list-panes` で確認し、verdict
  前に pane が死んでいれば fail-loud する。
- `interactive_terminal_close_on_verdict = true`（既定）で verdict 検知後に `tmux kill-pane -t
  %id` で best-effort cleanup する。**終了トリガは `verdict.yaml` の出現**であり agent プロセスの
  自然終了は待たない（`completion_barrier=verdict` 既定。`post_verdict_timeout` の 30s grace は
  `completion_barrier=agent_exit` 専用で本経路では適用されない）。**kill のタイミングは契約では
  ない** — verdict 検知後の cleanup であって、次 step 開始後に killしても問題なく、kill を急ぐ
  利得は無い。したがって Codex fresh で session id 未解決時に session-id 回収 grace（≤5s）を挟む
  ことも許容し、「poll≤Ns で kill」のようなレイテンシ契約は設けない。`false` なら
  `set-option -p -t %id remain-on-exit on` で pane を `[dead]`（`#{pane_dead}=1`）表示のまま残す
  （デバッグ用）。timeout 経路でも best-effort cleanup してから fail-loud する。
- **pane の寿命は step 単位**。step ごとに split-window で pane を作り、verdict / timeout で
  kill する（v1 の「step ごとに1ウィンドウ」と同じ寿命）。pane の使い回しはしない。
- transcript は `tmux pipe-pane -o -t %id 'cat >> terminal.log'` で attempt directory の
  `terminal.log` に記録する。`script(1)` 依存と OS 分岐は廃止。Linux / macOS で同一挙動。
- session 継続は v1 の既存 session state を再利用する。Claude fresh は runner が UUID を生成して
  `--session-id` に渡し、同じ UUID を session state に保存する。Codex fresh は `terminal.log` の
  `codex resume <uuid>` を抽出し、取れなければ `CODEX_HOME/sessions/**/*.jsonl` →
  `~/.codex/sessions/**/*.jsonl` を marker 一致で fallback 抽出する（v1 と同一）。
- 起動時に **`tmux -V` で最低バージョン（v3 / Issue #238 以降は `tmux >= 3.1`）を検査**し、満たさなければ
  fail-fast する。`#{pane_dead}` / `split-window -P -F` は 3.0 を要求し、v3 で導入する pane user option
  marker（`set-option -p @...`）は tmux 3.1 を要求するため、最小を 3.1 へ引き上げた。`kitty` の PATH
  検査はこのバージョン検査に置換される。
- `agent_runner` 未指定時は既存の `headless` runner を使い、headless の CLI 引数 / stdout logging
  / verdict 解決挙動は変更しない（互換維持）。

## 影響

- `kaji_harness/interactive_terminal.py`: terminal backend を `kitty` から `tmux` に置換。
  - `_build_kitty_argv` → `tmux split-window` argv 構築に置換。
  - `process.poll()` ベースの存命判定 → pane id + `#{pane_dead}` / `list-panes` に置換。
  - `_kill_processes_matching` / `_signal_matches` / `_pid_exists`（`/proc` scan、約70行）を
    **削除**し、`tmux kill-pane` に置換。
  - `$TMUX` 検査 + `tmux -V` バージョン検査を起動前段に追加。
  - verdict polling / Codex session id 抽出 / session 継続ロジックは**不変**。
- `kaji_harness/assets/interactive-terminal/wrapper.sh`: `script(1)` 経路を廃止し、transcript は
  runner 側 `tmux pipe-pane` に移す。wrapper は `cd <workdir>` → 通常 `claude` / `codex` 起動に
  専念する。
- `kaji_harness/config.py`: `ExecutionConfig` の `agent_runner` /
  `interactive_terminal_close_on_verdict` は不変（backend 切替の config key は**追加しない**）。
- `kaji_harness/cli_main.py`: `kaji run` の `--agent-runner` /
  `--interactive-terminal-close-on-verdict` / `--no-...` は不変。
- docs: 本 ADR、`docs/ARCHITECTURE.md` § runner backend dispatch、
  `docs/cli-guides/interactive-terminal-runner.md`（前提を `kitty`→`tmux` / `$TMUX` 必須 /
  `tmux >= 3.1`（v3）に更新、配置契約と右列最大2枚、トラブルシュート更新）、`github-mode.md` /
  `local-mode.md` の `[execution]`
  例（更新があれば）。
- workflow YAML / 遷移仕様 / verdict 解決経路（ADR 005）は不変。runner backend の選択は repository
  config の責務であり workflow step の責務にしない。

## 代替案と却下理由

| 代替案 | 却下理由 |
|--------|----------|
| terminal backend を `kitty` 単一に保つ（v1 決定の維持） | 並列可視性が `tmux split-window` でディスプレイ無しに再現でき、`kitty` 固有の優位が消えた。`kitty` は `/proc` scan（macOS 不可）と util-linux `script(1)`（macOS 分岐）の2つの OS 依存を残す。`tmux` 単一の方が移植性が高くコードも短い |
| `tmux` / `kitty` を config で選べる選択式（`interactive_terminal_backend`） | 「選べる」は本質的に機能追加で、protocol 抽象 + 2 実装 + 手動検証 2 backend 分のコストが乗る。`kitty` に残る固有ニッチは「GUI デスクトップで tmux を一切使いたくない人」のみで、対象環境（WSL2 / SSH / headless / macOS）に対し価値が薄い。将来必要になればその時 protocol を足す |
| `wt.exe` / `wezterm` / `gnome-terminal` まで含む広い terminal 抽象 | v1 同様に却下。広い抽象を先に作る価値が薄い。単一の最良 backend に収束させる |
| `$TMUX` 無し時に `tmux new-session -d` で detached session を作る | pane が即座に見えず `tmux attach` が要るため「並列可視性」の利点が失われ、detached モデルに退化する。`$TMUX` 必須 + fail-fast の方が単純で意図が明確 |
| `$TMUX` 無し時に headless へ自動 fallback する | 暗黙の backend 切替はデバッグを難しくする。明示の fail-fast（「tmux 内で実行して」）にし、headless に戻したいときは `--agent-runner headless` を使う |
| pane を step 間で使い回す | session 継続（`--resume` / `codex resume`）は新 process 起動が前提で、pane 再利用と相性が悪い。step ごとに split-window → kill-pane が単純で寿命も明確 |
| headless runner を `interactive_terminal` で置換 | 既存 workflow / CI の互換が壊れる。headless は推奨外でも互換用として残す |
| Codex `reasoning.effort = minimal` を最小値にする | PoC で現 tool 構成（`image_gen` / `web_search`）と衝突。実用最小は `low`。effort は runner で second-guess せず pass-through し、最小値選択は workflow step / 手動検証側の責務とする |

## 参照

- ADR 005: artifact-primary verdict resolution（本 runner の前提）
- Issue #220 / PR #221: attempt layout / `verdict.yaml` primary
- Issue #224: interactive_terminal runner（v1, kitty backend）の実装
- Issue #229: terminal backend を tmux に改訂（v2 の PoC 検証 + 実装）
- Issue #238: pane 配置を「初回右・以後右列内・最大2枚」に変更し最小 tmux を 3.1 に引き上げ（v3）
- `docs/cli-guides/interactive-terminal-runner.md`: 設定・手動検証手順
