# Interactive Terminal Runner

Language: [English](interactive-terminal-runner.md) | 日本語

`kaji run` のagent stepを、headless CLIではなく **tmuxまたはHerdr pane上**の通常`claude` / `codex` /
Antigravity（`agy`）
対話 CLI で実行する runner backend（Issue #224 / tmux 化は #230）。agent が attempt directory の
`verdict.yaml` を書いたら、kaji は artifact-primary 経路（[ADR 005](../adr/005-artifact-primary-verdict.md)）
で verdict を読み、次 step へ進む。

`interactive_terminal_backend = "tmux"`が既定。`"herdr"`を明示するとrelease-matched Herdr CLIと
明示pane IDを使う。どちらも選択したterminal session内で`kaji run`を起動し、runnerがpaneを
追加して agent を起動するため、ディスプレイ無し環境（WSL2 / SSH / headless）でも `kaji run` の出力と
agent を同一画面で並列に見られる。pane 配置は **初回のみ origin pane の右、2枚目以降は右列内の上下
分割**で、kaji が作成した agent pane は右列に**最大2枚**まで残す（Issue #238）。cleanup は
`tmux kill-pane`、transcript は `tmux pipe-pane` で行い、`/proc` scan も util-linux `script(1)` 依存も
無いため Linux / macOS で同一に動く。Herdr backendの`terminal.log`は
`recent-unwrapped`のrendered snapshotで、tmux raw transcriptと同等とは扱わない。

技術選定の経緯は [ADR 007](../adr/007-interactive-terminal-runner.md)、runner dispatch の
位置づけは [ARCHITECTURE](../ARCHITECTURE.md) § Runner backend dispatch を参照。

## いつ使うか

- 従量課金の headless 経路ではなく、通常コンソール利用に近い形で workflow を進めたいとき。
- ディスプレイ無し環境（WSL2 / SSH / headless）でも `kaji run` 出力と agent を同一画面で並列に見たいとき。
- step ごとに Claude / Codex の session を `--resume` / `codex resume` で引き継ぎたいとき。
- `agy -i` で Antigravity の単発 step を実行しつつ、kaji の artifact 完了契約を使いたいとき
  （Antigravity は `resume:` 非対応）。
- agent の最終状態を verdict 後も pane に残して確認したいとき（`close_on_verdict = false`）。pane を
  残しても右列は最新2枚相当に保たれ、横幅が step ごとに狭くならない。

`agent_runner` 未指定時は既存の `headless` runner が使われる。既存 workflow / CI の挙動は
変わらない。

## 前提

terminal backendは明示選択する。自動判定・暗黙fallbackは行わない。

### tmux

- **tmux session 内で `kaji run` していること**。runner は `$TMUX` を検査し、未設定なら step
  failure として **fail-fast** する（自動 fallback / 他 terminal 探索はしない）。tmux 外で使いたい
  場合は `--agent-runner headless` に戻す。この失敗は既知のユーザー前提エラーとして扱われ、
  インシデントイシューには起票されない（triage コメントと run artifact は従来どおり残る。
  Issue #322 / [failure-recovery.ja.md](./failure-recovery.ja.md) § incident 記録の対象外）。
- `$TMUX_PANE`（split の `-t` ターゲット）が設定されていること（tmux pane 内なら自動で入る）。
- `tmux`（**>= 3.1**）が PATH にあること。kaji 管理 pane を識別する pane user option
  （`set-option -p @kaji_interactive_terminal`）が tmux 3.1 で追加されたため（Issue #238 で 3.0 から
  引き上げ）。無ければ / 古ければ fail-fast。
- 選択した `claude` / `codex` / `agy` CLI が PATH にあること。
- transcript（`terminal.log`）は `tmux pipe-pane` で **常時記録**される（OS 分岐なし）。
- wrapper は agent 起動前に `NO_COLOR` を unset し、`COLORTERM=truecolor` を設定する。
  親 shell に `NO_COLOR=1` があっても、interactive terminal runner 内の agent は
  truecolor 表示を使える。

### Herdr

- Herdr **>= 0.8.2**がPATHにあり、client/server protocolがcompatibleであること。
  `HERDR_BIN_PATH`が実行可能なCLIを指す場合は、PATH探索よりrelease-matchedなそのCLIを優先する。
- Herdr内で`kaji run`すること。`HERDR_ENV=1`と`HERDR_PANE_ID`を必須とし、外側からfocused
  sessionを操作しない。不足時は`HerdrSessionRequiredError`でfail-fastする。preflightは
  `herdr status`も実行し、`pane current --current`が`HERDR_PANE_ID`とexact一致することを要求する。
- kajiはinstalled CLIをargvで呼び、JSON response由来pane IDだけを使う。raw socket client、plugin、
  Herdr agent integrationは必須ではない。
- 作成paneへ`kaji_origin` / `kaji_run` / `kaji_step` tokenを付け、close前にorigin/run一致を再確認する。
- `terminal.log`はrendered snapshot。kind / availability / revisionを`pane-metadata.json`へ保存する。
  Herdr 0.8.2の`pane read` CLIはstructured truncation flagを含まないplain textを返すため、
  `transcript_truncated`は推測せず`null`（unknown）とする。

## 設定

`[execution]` は **workflow YAML ではなく repository config** に書く（`.kaji/config.toml` の
探索ルールは [設定リファレンス](../reference/configuration.md#discovery-rule) 参照）。

tracked な既定値は `.kaji/config.toml`、個人環境だけで切り替える場合は gitignored の
`.kaji/config.local.toml` に書く。

```toml
# .kaji/config.toml または .kaji/config.local.toml
[execution]
default_timeout = 2400
agent_runner = "interactive_terminal"            # "headless"（既定） | "interactive_terminal"
interactive_terminal_backend = "tmux"            # "tmux"（既定） | "herdr"
interactive_terminal_close_on_verdict = true     # 既定 true
```

### Antigravity の execution policy

runner は workflow の `execution_policy` を wrapper 第9引数へ渡し、Antigravity では次のように写像する。

| policy | `agy -i` flag | permission の挙動 |
|--------|---------------|-------------------|
| `auto` | `--dangerously-skip-permissions` | tool request を自動承認 |
| `sandbox` | `--sandbox` | containment を有効化し、TUI approval は維持 |
| `interactive` | なし | AGY default と TUI approval |

AGY では sandbox と permission は別軸であり、`sandbox` に permission bypass を加えない。

`agent_runner` が許可値以外なら **config load 時点で `ConfigLoadError`**（fail-fast）。
`[execution]` 各 key の網羅的な仕様（型 / 既定 / 検証）の正本は
[設定リファレンス](../reference/configuration.md#execution) を参照。

### overlay

`.kaji/config.local.toml` の `[execution]` は **key 単位**で `.kaji/config.toml` の同名 key を
上書きする（[設定リファレンス](../reference/configuration.md#overlay-merge-rule) 参照）。tracked が
`agent_runner = "headless"` でも、overlay に 1 key 書くだけで個人環境のみ切り替えられる
（`default_timeout` は tracked のまま）。

```toml
# .kaji/config.local.toml （gitignored, 個人環境）
[execution]
agent_runner = "interactive_terminal"
```

## CLI option（`kaji run`）

```bash
--agent-runner <headless|interactive-terminal>
--interactive-terminal-backend <tmux|herdr>
--interactive-terminal-close-on-verdict
--no-interactive-terminal-close-on-verdict
```

- `--agent-runner interactive-terminal`: この実行だけ interactive terminal runner を使う
  （config value `interactive_terminal` に正規化。CLI 公開値は hyphen 区切りのみ）。
- `--agent-runner headless`: この実行だけ headless に戻す（config が interactive_terminal でも上書き）。
- `--interactive-terminal-backend`: この実行だけpane実装を選ぶ。interactive runner自体を暗黙に有効化しない。
- `--interactive-terminal-close-on-verdict` / `--no-...`: この実行だけ close-on-verdict を上書き。
  どちらも未指定なら config 値を維持する。

### 設定の優先順位（固定）

1. `kaji run` CLI option
2. `.kaji/config.local.toml` の `[execution]`
3. `.kaji/config.toml` の `[execution]`
4. built-in default（`agent_runner = "headless"`, `interactive_terminal_backend = "tmux"`,
   `interactive_terminal_close_on_verdict = true`）

### 使用例

```bash
# 必ず tmux session 内で実行する（$TMUX が無いと fail-fast）
tmux new-session            # もしくは既存 tmux session 内

# repository config で interactive_terminal を既定にして実行
kaji run .kaji/wf/official/dev.yaml 224

# この実行だけ interactive terminal + pane を残す
kaji run .kaji/wf/official/dev.yaml 224 \
  --agent-runner interactive-terminal \
  --no-interactive-terminal-close-on-verdict

# Herdr内からHerdr backendを使う
kaji run .kaji/wf/official/dev.yaml 396 \
  --agent-runner interactive-terminal \
  --interactive-terminal-backend herdr

# この実行だけ headless に戻す（tmux 不要）
kaji run .kaji/wf/official/dev.yaml 224 --agent-runner headless
```

## 起動コンソール progress（Issue #235）

interactive terminal runner では agent の作業内容は pane 側に表示されるため、起動コンソール
（`kaji run` を叩いた元の pane）には harness 自身の進行が見えにくい。Issue #235 で、harness は
起動コンソールへ日時付き `[kaji]` progress を stdlib `logging` 経由で出力する。

```text
[2026-06-07T12:34:57] [kaji] workflow start: dev issue #224
[2026-06-07T12:34:57] [kaji] step start: design attempt-001 dispatch=agent agent=claude model=opus
[2026-06-07T12:35:02] [kaji] pane launched: step=design agent=claude pane=%12 timeout=1800s verdict=/.../steps/design/attempt-001/verdict.yaml
[2026-06-07T12:42:10] [kaji] verdict detected: design source=artifact status=PASS
[2026-06-07T12:42:10] [kaji] step end: design status=PASS duration=433000ms next=review-design
[2026-06-07T12:42:10] [kaji] workflow end: status=COMPLETE duration=...ms
```

- `INFO` 以下は stdout、`WARNING` 以上は stderr に出る。
- `--log-level {DEBUG,INFO,WARNING,ERROR}`（default `INFO`）で表示閾値を制御する。
- `--quiet` は agent/exec の stdout streaming（pane や exec 中継）を抑制するが、`[kaji]` progress
  には影響しない（両者は独立）。harness progress だけ抑えたい場合は `--log-level WARNING` を使う。
- `review-poll` のような deterministic exec step は pane を開かない代わり、polling 中に
  `POLL_INTERVAL_SEC`（10s）ごとの `[review-poll]` heartbeat を起動コンソールへ flush 出力する
  （経過秒・PR 番号・head 短縮・観測中 state・timeout 残を含む）。これにより待機中 / 停止中 /
  エラーを `run.log` を開かずに切り分けられる。

```bash
# harness progress を抑えて警告/エラーのみ表示
kaji run .kaji/wf/official/dev.yaml 224 --log-level WARNING
```

## 振る舞い

以下の番号付きlifecycleはtmux実装。Herdrもwrapper / verdict / session state契約を共有し、
backend固有差分だけを後段に示す。

1. runner は launch 前に `tmux list-panes` で同一 window の kaji 管理 agent pane（pane user option
   `@kaji_interactive_terminal` が `origin=<origin pane>` 一致）を列挙し、配置を決める（Issue #238）:
   - 管理対象0枚: origin pane を `tmux split-window -d -h -t "$TMUX_PANE"` で右に分割し、右列を作る。
   - 管理対象1枚: その agent pane を `-d -v` で上下分割し、右列の2枚目を作る。
   - 管理対象2枚以上: `pane_top` 昇順で最古（上側）の管理対象 pane から順に、残り 1 枚になるまで
     `kill-pane` してから、残った最新 pane を `-d -v` で分割する。これで右列は常に最新2枚相当に
     保たれ、横幅が step ごとに狭くならない。

   いずれも `-d` でフォーカスを奪わず、`-P -F '#{pane_id}'` で生成された pane id を回収して以降の
   ライフサイクルハンドルにする。作成直後の pane には `tmux set-option -p -t <pane> @kaji_interactive_terminal
   origin=<origin pane>` で marker を付与し、kaji が作成した pane だけを後続の prune 対象にする
   （ユーザーが手動で作った pane や別 origin の pane は誤って閉じない）。marker 設定に失敗した場合は
   作成済み pane を best-effort `kill-pane` してから fail-loud する。`list-panes` / pane lookup が
   失敗した場合も fail-loud する（壊れた tmux state での誤 cleanup を避ける）。ユーザーが手動で agent
   pane を active にした後にその pane が prune された場合、active pane が tmux 通常挙動で移ることは
   許容する（自動作成時に origin からフォーカスを奪わないことのみが契約）。
2. runner は `tmux pipe-pane -o -t %id 'cat >> terminal.log'` で pane 出力を attempt directory の
   `terminal.log` に記録する。
3. wrapper は最初に `cd <workdir>`（trusted な project worktree。`/tmp` や attempt directory を
   cwd にしない）してから通常 `claude` / `codex` / `agy` を起動する
   （Codex には `--cd <workdir>` も渡す）。
4. wrapper は prompt 全文を埋め込まず、agent に「`prompt.txt` を読み、`verdict.yaml` を pure YAML
   で書く」ことだけを指示する。
5. runner は `verdict.yaml` を polling し、出現したら artifact-primary 経路で verdict を解決して
   workflow を継続する。**終了トリガは `verdict.yaml` の出現のみ**で、agent プロセスの自然終了は
   待たない。pane の存命判定は `#{pane_dead}`（pane lookup 失敗も dead 扱い）で行う。
6. `interactive_terminal_close_on_verdict = true` なら、verdict 検知後に `tmux kill-pane` で pane を
   **best-effort cleanup** する。これは cleanup であり「poll≤Ns で kill」のようなレイテンシ契約は持たない
   （次 step 開始後の kill も許容）。timeout 経路でも best-effort `kill-pane` してから fail-loud する。
   ただし **利用者の Ctrl-C（`KeyboardInterrupt`）では pane を kill しない**（Issue #403）。
   中断直前の agent 状態を目視・回収できるよう pane を残し、`pane-metadata.json` に
   `pane_id` を記録して `KeyboardInterrupt` を再送出する。孤児 pane は手動で
   `tmux kill-pane -t <pane_id>` する。
7. `interactive_terminal_close_on_verdict = false` なら、polling 前に
   `tmux set-option -p -t %id remain-on-exit on` を設定し、verdict 後に `kill-pane` しない。pane は
   agent 自然終了後も `[dead]`（`#{pane_dead}=1`）として残り、ユーザーが後から内容を確認できる。次 step
   の launch 時にその pane は kaji marker 付き pane として検出され、右列が最大2枚になるよう最古 pane が
   prune される（直近の agent step と1つ前を見比べられる）。

> **pane metadata（診断用）**: runner は verdict 検知時点の `#{pane_dead}` 等を
> attempt directory の `pane-metadata.json` に snapshot 記録する。verdict-trigger 契約のもとでは
> verdict 時点の agent CLI は生存しているため、この snapshot は通常 `#{pane_dead}=0` になる。
> `[dead]` への遷移は `remain-on-exit on` が保証する最終状態で、metadata snapshot とは別物。
> Issue #238 以降は配置診断として `layout_target_pane` / `split_target_pane` / `split_direction`
> （`horizontal` / `vertical`）/ `kaji_agent_panes_before` / `kaji_agent_panes_pruned` も記録する。

### Herdrの振る舞い

1. binary解決前に`HERDR_ENV=1`と`HERDR_PANE_ID`を検証し、その後Herdr >= 0.8.2と`pane current --current`のexact一致をpreflightする。
2. token所有paneとorigin layoutを読み、初回は右、以後は下へsplitし最大2枚に保つ。prune前に
   current origin/run tokenを再取得する。run token / layout欠落またはownership変化がある古い候補は
   stepを止めずskipし、warningと`kaji_agent_panes_skipped`へpane IDを残す。
3. 明示cwdと`--no-focus`でsplitし、response由来paneをmarker付与する。続いてattempt directoryへ
   private（mode `0700`）な`herdr-launcher.sh`をatomic publishし、paneには短い
   `<launcher path>`という短いchild commandだけを一度送る。shell processはliveness判定のためpaneに残し、
   継承PATHと長いwrapper argvはlauncher内に置くため、fresh shellの観測不能なinput modeへ正しさを
   依存させない。launcherはwrapper起動前にprivateなstart markerをatomic publishし、kajiはboundedに
   markerを待つ。marker前のshell-only観測はagent終了として数えない。launcher作成、pane run、または
   start確認失敗時はownership確認済みpaneを
   cleanupしてfail-loudする。ownership marker失敗時はunowned paneを閉じない。
4. 完了triggerは`verdict.yaml`のみ。foreground processは早期shell復帰の診断にだけ使い、
   output/status文字列ではstepを完了しない。optional process fieldの欠落・型不正はunknownとして扱い、
   shell復帰確認へ加算しない。
5. verdict / pane run失敗 / 早期終了 / timeout時にrendered snapshotをbest-effort保存する。cleanupは
   ownershipを再確認する。close失敗はwarningと`close_error`へ記録し、verdict、元の失敗/timeout、
   解決済みsessionを置換しない。早期終了metadataには構造化`terminal_diagnostic`も残し、失敗詳細には
   rendered snapshotが不完全な可能性を明記する。
6. 利用者のCtrl-Cではagent paneを閉じず、ownershipとrendered snapshotを`pane-metadata.json`へ
   記録して`KeyboardInterrupt`を再送出する。snapshot保存に失敗しても元の中断を優先する。
   状態確認後、孤児paneは`herdr pane close <pane_id>`で手動cleanupする。

### Codex / Claude Codeからkajiを起動

Herdr内agentからrepository skill `herdr-kaji-launch`を使う。Claudeは
`.claude/skills/herdr-kaji-launch`、Codexは`.agents/skills/herdr-kaji-launch` symlinkから同じ正本を読む。
skillはrelease-matched `herdr --skill`を読み、explicit caller paneからcwd/focusを保ってsplitし、
kajiを通常interactive commandとして起動する。Claude Code `-p` / print modeとHerdr `agent start`は使わず、
kaji paneは対話用に残す。pluginは任意の人間向けlauncher UXでありcore依存ではない。

### session 継続

- **Claude**: fresh run では runner が UUID を生成して `--session-id` に渡し、同じ UUID を
  session state に保存する。resume step では保存済み id を `--resume` に渡す。
- **Codex**: fresh run 後、runner は `terminal.log` の `codex resume <uuid>` を抽出する。取れない
  場合は `CODEX_HOME/sessions/**/*.jsonl` → `~/.codex/sessions/**/*.jsonl` を mtime 降順に走査し、
  当該 attempt の `prompt.txt` / `verdict.yaml` path を含む rollout file の UUID を採用する。
  resume step では `codex resume <uuid>` で起動する。tmux backendはverdict検知後に回収grace（≤5s）と
  cleanup後の再走査を行いうる。Herdr backendは各verdictへ最大5秒を追加しないため、意図的にrendered
  snapshot 1回とsession store fallbackだけを使う。実Herdr 0.8.2のCodex runではこの経路で解決済み。
- **Antigravity**: 公開 session ID を取得しない。result は常に `session_id=None` で、
  workflow の `resume:` は起動前に拒否する。

#### 異常終了経路（timeout / pane-dead）の解決規則（Issue #403）

verdict を得ずに終わった attempt でも、当該 attempt と**検証可能に**対応付く session ID を
`result.json` の `session_id` に残す（診断・人手の再開判断のためであり、自動 resume は行わない）。
verdict 検出経路とは規則が異なる。この規則は両terminal backendに適用する。Herdrで確認済みの
shell復帰は、container pane自体はownership確認後のcleanupまで残るが、起動したagent processは
終了済みなので表の`pane-dead`経路として扱う。

| agent | resume 入力 | 経路 | 解決結果 |
|---|---|---|---|
| codex | なし / あり | timeout / pane-dead | session store の一意一致 1 件のみ採用。0 件 / 複数 / 読取失敗は `null`（親 ID へ fallback しない） |
| claude | なし | timeout | runner 採番の `--session-id` UUID |
| claude | なし | pane-dead | `null`（起動失敗を含み、UUID に対応する session が実在しない可能性がある） |
| claude | あり | timeout / pane-dead | resume 入力の ID（`--resume` は同一 session を継続する） |
| antigravity | — | 全経路 | `null` |

- codex の照合は `CODEX_HOME/sessions/**/*.jsonl` のうち **`prompt.txt` の mtime 以降に更新された**
  rollout に限り、marker（attempt の `prompt.txt` / `verdict.yaml` の絶対 path）を含むものを数える。
  `resume:` step では親 rollout も marker を含むが、attempt 開始前に更新が止まっているため除外される。
- 異常終了経路は `terminal.log` を読まない（timeout 時は Codex の resume 行がまだ出ておらず、
  transcript が巨大になりうるため）。grace wait も置かない。
- **verdict 検出経路はこの一意性規則を適用しない**（従来どおり mtime 降順の最初の一致を採る）。
  codex の resume は履歴を引き継ぐ新規 rollout を作るため同一 marker が正当に複数一致しえ、
  verdict 経路に一意性を強制すると既存の `resume:` step が `MissingResumeSessionError` で退行する。

### effort の注意（Codex）

Codex の `reasoning.effort = minimal` は現 tool 構成（`image_gen` / `web_search`）と衝突するため、
実用最小値は `low`。runner / wrapper は effort を pass-through し、最小値の選択は workflow step /
手動検証側の責務とする。

## 手動検証手順（real tmux + real Claude / Codex / Antigravity）

> 自動テストは fake bin + 実 tmux の Large（`large_local`）と fake tmux の Medium で振る舞いを
> 担保する。real `claude` / `codex` / `agy` のライブ疎通は **意図的に自動化しない**（実 API 課金・対話 CLI の
> 自動化困難）ため、以下を手動で確認する。

検証は **project worktree 内**かつ **tmux session 内**で行う。`/tmp` や attempt directory を cwd に
しない。

検証 model / effort（安価なもの）:

- Claude: `haiku` / `low`
- Codex: `gpt-5.4-mini` / `low`
- Antigravity: 利用可能な model / `low`

手順:

1. `tmux` session を起動する（`tmux new-session`、または既存 session 内）。
2. `.kaji/config.local.toml` に `[execution] agent_runner = "interactive_terminal"` を設定する
   （または `kaji run ... --agent-runner interactive-terminal`）。
3. 最小 workflow を `kaji run` で起動し、**現ウィンドウの右に pane が開いて**通常 `claude` が起動する
   ことを確認する。
4. agent が `verdict.yaml` を書いたら kaji が次 step へ進むことを確認する（**Claude fresh**）。
5. resume step が同じ session id（`--resume <uuid>`）で起動されることを確認する（**Claude resume**）。
6. Codex でも同様に fresh が `verdict.yaml` を書き（**Codex fresh**）、resume が `codex resume` で
   起動される（**Codex resume**）ことを確認する。
7. `interactive_terminal_close_on_verdict = true` で verdict 後に pane が消える（`kill-pane`）こと、
   `false`（`--no-interactive-terminal-close-on-verdict`）で `[dead]` pane が残ることを確認する。
8. `terminal.log` が attempt directory に記録されることを確認する（OS を問わず常時記録）。
9. `--no-interactive-terminal-close-on-verdict` で **agent step を3回以上**連続実行し、右列の kaji 管理
   pane が**最大2枚**に保たれること、origin pane と右列 agent pane の `pane_width` が連続作成後も横方向に
   狭くならない（毎 step で縮み続けない）ことを確認する。手動で作った別 pane が誤って閉じられないことも
   合わせて確認する。

## トラブルシュート

| 症状 | 原因 / 対処 |
|------|-------------|
| `CLI 'tmux' not found` で即終了 | `tmux` を PATH に入れるか `--agent-runner headless` で実行する |
| `requires tmux. Run kaji run inside tmux` で即終了 | tmux session の外で実行している。`tmux new-session` 内で再実行するか `--agent-runner headless` |
| `requires tmux >= 3.1` で即終了 | tmux が古い。3.1 以上へ更新する（kaji marker の pane user option `set-option -p` が 3.1、`#{pane_dead}` / `split-window -P -F` が 3.0 を要求） |
| `TMUX_PANE is not set` で即終了 | tmux pane 内で実行していない。通常の tmux session なら自動設定される |
| `CLI 'herdr' not found` で即終了 | Herdr >= 0.8.2をinstallするかtmux backendを選ぶ |
| `must run inside Herdr` で即終了 | Herdr pane内で再実行する。外側からfocused sessionは操作しない |
| `HERDR_PANE_ID is not set` | caller identityを得られる通常のHerdr pane内で再起動する |
| Herdr `terminal.log`の過去行が足りない | raw transcriptではなくrendered snapshot。Herdr 0.8.2で`transcript_truncated=null`ならplain-text CLIが省略有無を公開していない。revisionはsnapshot識別に使い、完全性の証明にはしない |
| step が timeout する | agent が `verdict.yaml` を書いていない。prompt の verdict 書き出し指示と path を確認 |
| pane が verdict 前に消える / `tmux pane exited before writing verdict.yaml` | agent が起動失敗。`terminal.log` の tail（エラー文面に添付）を確認 |
| 色が出ない | wrapper は `NO_COLOR` unset / `COLORTERM=truecolor` を設定する。端末側の color support と agent 側設定も確認 |
| Codex resume が効かない | `terminal.log` に resume 行が出ず、session store fallback も marker 不一致。`CODEX_HOME` を確認 |
| CLI が trust / permission 確認で止まる | cwd が project 外（`/tmp` 等）。workdir を project worktree に固定する |
