# [設計] interactive_terminal runner の terminal backend を tmux 単一に実装置換する

Issue: #230

## 概要

`interactive_terminal` runner の terminal backend を **`kitty` から `tmux` 単一へ実装置換**する。
runner の公開インターフェース（`execute_interactive_terminal()` の呼び出し契約）・verdict 解決経路
（ADR 005 artifact-primary）・session 継続・config（`agent_runner` 等）は不変に保ち、pane の起動／
存命判定／cleanup／transcript の実体だけを tmux コマンドへ差し替える。あわせて関連ドキュメントを
tmux 前提に更新する。

## 背景・目的

### 現状の問題

現行 runner（v1, `kitty` 固定, `kaji_harness/interactive_terminal.py`）は 2 つの OS 依存を抱える:

1. **cleanup**: `kitty` の detached child を親子で追えず、`/proc/*/cmdline` を marker scan して
   killpg する（`_kill_processes_matching` / `_signal_matches` / `_pid_exists`, 約 70 行）。
   macOS に `/proc` が無く **Linux 専用**。
2. **transcript**: wrapper が util-linux `script(1)` 依存。macOS native `script` は BSD 構文で
   GNU long option 非対応のため OS 分岐（fail-soft で transcript 無し）が残る。

加えて `kitty` は GUI ウィンドウ前提で、WSL2 / SSH / headless では並列可視性が得られない。

### 到達したい状態

`kaji run` を tmux session 内で起動し、runner が `tmux split-window` で現ウィンドウに pane を
追加して agent を起動する。これにより:

- ディスプレイ無し環境（WSL2 / SSH / headless）でも `kaji run` 出力と agent を同一画面で
  並列に見られる。
- cleanup は `tmux kill-pane`、transcript は `tmux pipe-pane` に置換され、`/proc` scan と
  `script(1)` の OS 分岐が**両方消える**（Linux / macOS 同一実装）。

### ユースケース

- **kaji 利用者**として、ディスプレイ無し環境でも `kaji run` と agent を同一画面で並列に見たいので、
  `kaji run` を tmux 内で起動し、runner に `tmux split-window -h` で右に pane を追加させて実行したい。
- **メンテナ**として、`/proc` scan と `script(1)` の OS 分岐を消して Linux / macOS 同一実装にしたいので、
  cleanup を `kill-pane`、transcript を `pipe-pane` に置換したい。

### 代替案と不採用理由（ADR 007 v2 で確定済み・要約）

| 代替案 | 不採用理由 |
|--------|-----------|
| `kitty` 単一を維持（v1） | 並列可視性が `tmux split-window` でディスプレイ無しに再現でき、kitty 固有の優位が消えた。kitty は `/proc` scan と `script(1)` の 2 OS 依存を残す |
| `tmux` / `kitty` を config で選べる選択式 | protocol 抽象 + 2 実装 + 手動検証 2 backend 分のコストが乗る。対象環境（WSL2 / SSH / headless / macOS）に対し kitty の固有ニッチは薄い |
| `$TMUX` 無し時に headless 自動 fallback | 暗黙の backend 切替はデバッグを難しくする。明示 fail-fast（「tmux 内で実行して」）にし、戻すなら `--agent-runner headless` |
| pane を step 間で使い回す | session 継続（`--resume` / `codex resume`）は新 process 起動前提で pane 再利用と相性が悪い。step 単位 split → kill が単純 |

詳細根拠は ADR 007 v2（`docs/adr/007-interactive-terminal-runner.md`）§ 代替案と却下理由。

## インターフェース

### 入力

#### `execute_interactive_terminal()`（公開シグネチャは現行を維持）

runner.py dispatch（`kaji_harness/runner.py:673-684`）からの呼び出し契約は **不変**:

| 引数 | 型 | 説明 |
|------|-----|------|
| `step` | `Step` | `step.agent` は `claude` / `codex`。`step.model` / `step.effort` を pass-through |
| `prompt_path` | `Path` | attempt の `prompt.txt`（絶対パス）。`prompt_path.parent` が attempt directory |
| `verdict_path` | `Path` | agent が書く `verdict.yaml` の絶対パス。**終了トリガ** |
| `workdir` | `Path` | trusted project worktree。cwd / Codex `--cd`。**headless runner と同一の `effective_workdir`** |
| `timeout` | `int` | `verdict.yaml` 出現を待つ秒数 |
| `session_id` | `str \| None` | resume 対象 session id（`None` → fresh） |
| `close_on_verdict` | `bool` | verdict 検知後に pane を kill するか（既定 `True`） |

> dispatch 側の呼び出しは現行のまま（`workdir=effective_workdir` を含め変更しない）。本 Issue は
> backend 実装置換であり、公開シグネチャの破壊的変更は行わない。`verbose` / `completion_barrier`
> 等の追加引数を導入する場合も既定値を持たせ、既存呼び出しを壊さないこと（§ 方針「スコープ決定」参照）。

#### wrapper.sh の引数契約（**9 → 8 へ変更**）

transcript を runner 側 `pipe-pane` に移すため、wrapper から `terminal_log` 引数を削除する:

```
agent prompt_path verdict_path workdir resume_session_id launch_session_id model effort
```

（v1 は `agent prompt_path verdict_path terminal_log workdir ...` の 9 引数。位置 4 の `terminal_log` を除去）

#### 環境前提（runner 起動時に検査）

- `$TMUX` が設定されていること（tmux session 内で `kaji run` していること）。
- `$TMUX_PANE`（split の `-t` ターゲット）が設定されていること。
- PATH に `tmux`（`>= 3.0`）と、起動対象の `claude` / `codex` があること。

### 出力

- **戻り値**: `CLIResult(full_output="", session_id=<解決済み id or None>)`。stdout は読まない
  （artifact-primary）。`full_output` は常に空。
- **副作用 / 生成物**（attempt directory `prompt_path.parent` 配下）:
  - `terminal.log` — `tmux pipe-pane -o` による pane 出力の transcript（OS 分岐なしで常に記録）。
  - pane lifecycle metadata（`#{pane_dead}` 等を含む JSON）— 完了条件「`#{pane_dead}=1` metadata 保存」
    の証跡。診断用に attempt directory へ書き出す。
  - tmux pane の生成（`split-window`）と、`close_on_verdict=true` 時の破棄（`kill-pane`）。
- **session state への保存**: 解決した session_id を `state.save_session_id()`（dispatch 側、現行不変）。

### 使用例

利用者は config で backend を選ぶだけ（runner API の直接呼び出しは harness 内部のみ）:

```toml
# .kaji/config.toml または .kaji/config.local.toml
[execution]
agent_runner = "interactive_terminal"            # "headless"（既定） | "interactive_terminal"
interactive_terminal_close_on_verdict = true     # 既定 true
```

```bash
# 必ず tmux session 内で実行する（$TMUX が無いと fail-fast）
tmux new-session            # もしくは既存 tmux session 内
kaji run .kaji/wf/feature-development.yaml 230 --agent-runner interactive-terminal
# → runner が現ウィンドウの右に pane を split して claude/codex を起動し、
#   verdict.yaml 出現で次 step へ進む
```

### エラー

| 失敗 | 例外 | 文言の要点 |
|------|------|-----------|
| `tmux` が PATH に無い | `CLINotFoundError` | "tmux not found. Install tmux or use agent_runner='headless'." |
| `$TMUX` 未設定 | `CLINotFoundError` | "requires tmux. Run `kaji run` inside tmux ..."（fail-fast。自動 fallback しない） |
| `$TMUX_PANE` 未設定 | `CLINotFoundError` | "TMUX_PANE is not set; cannot target the current tmux pane." |
| `tmux -V` < 3.0 | `CLINotFoundError` | "requires tmux >= 3.0, got <version>" |
| `split-window` が pane id を返さない / 非ゼロ終了 | `CLIExecutionError` | tmux stderr / "did not return a pane id" |
| pane が verdict 前に dead（`#{pane_dead}=1` / lookup 失敗） | `CLIExecutionError` | `terminal.log` tail を添えて fail-loud |
| `timeout` 経過しても `verdict.yaml` が出現しない | `StepTimeoutError` | best-effort `kill-pane` 後に raise |
| `step.agent` 不正 / `prompt.txt` 不在 | `ValueError` / `FileNotFoundError` | 現行と同一 |

## 制約・前提条件

- **公開契約の不変**: dispatch 呼び出し（`runner.py`）・config（`ExecutionConfig.agent_runner` /
  `interactive_terminal_close_on_verdict`）・CLI option（`--agent-runner` /
  `--interactive-terminal-close-on-verdict`）・verdict 解決経路（ADR 005）・session 継続ロジックは
  **変更しない**。
- **`tmux >= 3.0` を要求**: pane option（`set-option -p` による per-pane `remain-on-exit`）が
  tmux 3.0 で追加されたため（§ 参照情報）。`#{pane_dead}` / `split-window -P -F` も併用する。
- **`-h` で右分割**: `tmux split-window` の既定は下分割。ユーザー視点で「右」に出すため `-h`（横分割）を
  指定する。
- **終了トリガは `verdict.yaml` の出現のみ**: agent プロセスの自然終了は待たない
  （`completion_barrier=verdict` 既定）。verdict 時点で agent CLI は生存（`#{pane_dead}=0`）している。
- **kill のタイミングは契約にしない**: verdict 検知後の `kill-pane` は **best-effort cleanup** であり、
  「poll≤Ns で kill」のレイテンシ要件を設けない。次 step 開始後の kill も許容。Codex fresh で
  session id 未解決時の回収 grace（≤5s）を挟むことも許容。
- **依存**: 標準ライブラリのみ（`subprocess` で tmux 呼び出し、`shlex` で shell-quote、`re` /
  `uuid` / `os` / `shutil` / `time` / `pathlib`）。新規サードパーティ依存は追加しない。
- **OUT（本 Issue で扱わない）**: backend 選択式 config、`$TMUX` 無し時 fallback、`agent_runner` /
  config key 追加、verdict 解決・session 継続の変更、skill/workflow の worktree 選択ロジック
  （runner は与えられた `effective_workdir` を忠実に使うのみ）、implement step の timeout 手当て、
  full macOS 実機検証。

## 変更スコープ

| ファイル | 変更 |
|---------|------|
| `kaji_harness/interactive_terminal.py` | terminal backend を kitty → tmux に置換。`_build_kitty_argv` → tmux `split-window` argv 構築。`process.poll()` 存命判定 → pane id + `#{pane_dead}`。`_kill_processes_matching` / `_signal_matches` / `_pid_exists`（/proc scan）を削除し `kill-pane` に置換。`$TMUX` / `$TMUX_PANE` / `tmux -V` 検査を追加。transcript を `pipe-pane` に移管。Codex session id 抽出・session 継続は不変 |
| `kaji_harness/assets/interactive-terminal/wrapper.sh` | `script(1)` 経路（`run_with_transcript`）を削除。引数を 9 → 8（`terminal_log` 除去）。`cd "$workdir"` → 通常 `claude` / `codex` 起動（Codex `--cd "$workdir"`）に専念。transcript は runner 側 pipe-pane が担当 |
| `tests/test_interactive_terminal.py` | kitty 前提のテストを tmux 前提へ書き換え（§ テスト戦略） |
| `kaji_harness/runner.py` | dispatch 呼び出しは原則不変。MF3 回帰 guard 用に `effective_workdir` passthrough を確認するテスト経路のみ追加（コメントの "kitty" 表記更新は任意） |
| `kaji_harness/config.py` / `kaji_harness/cli_main.py` | **不変**（config key / CLI option は変えない） |
| docs（§ 影響ドキュメント） | kitty → tmux 前提へ更新 |

## 方針

### backend 機構（Minimal How）

検証済み PoC（worktree `kaji-poc-tmux-lifecycle-227`、real-agent 検証で実現性確認済み）を参照実装と
する。中核フロー:

1. **前段検査**: `shutil.which("tmux")` → `$TMUX` / `$TMUX_PANE` → `tmux -V` で `>= 3.0` を検査
   （いずれも満たさなければ fail-fast）。
2. **pane 起動**: `_build_tmux_split_argv` が次を生成し、`subprocess.run` で pane id を回収する:
   ```
   tmux split-window -d -h -P -F '#{pane_id}' -t "$TMUX_PANE" <wrapper.sh + 8 args(shlex.join)>
   ```
   - `-d` でフォーカスを奪わない。**`-h` でユーザー視点の右に追加**（MF2）。`-P -F '#{pane_id}'` で
     生成 pane の id を stdout に出させ、以後のライフサイクルハンドルにする。pane id が `%` で
     始まらなければ fail-loud。
3. **transcript**: `tmux pipe-pane -o -t %id 'cat >> <attempt>/terminal.log'`。OS 分岐なし。
4. **polling ループ**（`_VERDICT_POLL_INTERVAL_SECONDS` 間隔、`deadline = now + timeout`）:
   - `verdict_path.is_file()` なら verdict 検知。pane metadata（`#{pane_dead}` 等）を保存し、
     Codex かつ session_id 未解決なら resume id を回収（grace ≤5s）。`close_on_verdict=true` なら
     `kill-pane`（best-effort）。`CLIResult(full_output="", session_id=...)` を返す。
   - `_pane_dead(tmux, %id)`（`tmux display-message -p -t %id '#{pane_dead}'`、**lookup 失敗も
     dead 扱い**）が True なら、verdict 前 pane 死として `terminal.log` tail を添えて
     `CLIExecutionError`。
   - timeout 到達 → best-effort `kill-pane` 後に `StepTimeoutError`。
5. **`close_on_verdict=false`**: polling 前に `tmux set-option -p -t %id remain-on-exit on` を設定し、
   verdict 後も `kill-pane` しない。pane は `[dead]`（`#{pane_dead}=1`）として残る。metadata に
   `#{pane_dead}=1` を記録する。

存命判定の主経路は `#{pane_dead}`。`list-panes` は cleanup 後の pane 消滅確認（テスト／検証で
「kill-pane 後に当該 pane が list-panes に現れない」ことの assert）に使う。

### PoC からの差分（実装で必ず反映する 3 点）

1. **MF2: `-h` 追加**。PoC の `_build_tmux_split_argv` は `split-window -d -P -F '#{pane_id}' -t ...`
   で `-h` を含まない。`-d` の直後に `-h` を加え、argv exact-assert テストも `-h` 込みへ更新する。
2. **wrapper 引数 9 → 8**。`terminal_log` を除去し、transcript は runner の pipe-pane に一本化する。
3. **MF3 回帰 guard**。`effective_workdir` は既に dispatch 側で両 runner（interactive / headless）に
   同一変数として渡されている（`runner.py:678` / `runner.py:689`）。新規実装は不要。回帰テストを
   1 本追加し、interactive 分岐が headless と同じ `effective_workdir` を渡すことを固定する。

### スコープ決定（PoC の investigation scaffolding の扱い）

PoC は実現性検証のために本 Issue の契約を超える診断機構を含む。本 Issue の contract（**終了トリガ＝
verdict 出現の単一経路**）を維持するため、以下を方針として定める:

| PoC 要素 | 本 Issue での扱い | 理由 |
|---------|------------------|------|
| pane lifecycle metadata（`#{pane_dead}` 等の JSON 書き出し） | **採用** | 完了条件「`#{pane_dead}=1` metadata 保存」を直接満たす診断証跡。低リスク |
| `completion_barrier=agent_exit` 経路 / `post_verdict_timeout` grace / `agent-exited.json` | **不採用（contract 外）** | 契約は verdict-trigger 単一。`agent_exit` barrier は別経路で、ADR でも本経路には grace 非適用と明記。wrapper は exit code 捕捉せず agent を直接 `exec` してよい |
| `pane-capture.log`（`capture-pane`） | **任意（採用可・低優先）** | デバッグ補助。契約には不要。残す場合も runtime gate にしない |
| `verbose` / progress print | **任意** | 採用する場合は既存 `--quiet` / `self.verbose` に従わせる。契約ではない |

> `completion_barrier` を引数として残す場合は既定 `"verdict"` 固定とし、`agent_exit` 経路は実装しない
> （contract を verdict-trigger 単一に保つ）。dispatch（runner.py）は `completion_barrier` を渡さない
> ので既定で verdict-trigger になる。

### 削除する OS 依存コード

- `_kill_processes_matching` / `_signal_matches` / `_pid_exists`（/proc scan、約 70 行）→ `kill-pane`。
- wrapper の `run_with_transcript`（util-linux `script(1)` 判定と BSD/macOS fallback）→ pipe-pane。
- `_build_kitty_argv` / `kitty` PATH 検査 → tmux split argv / `tmux -V` 検査。

## テスト戦略

> **CRITICAL**: 本変更は **実行時の振る舞いを変えるコード変更**（runner backend 置換）であり、
> Small / Medium / Large の各観点を定義する。サイズ省略は行わない。

### 変更タイプ

実行時コード変更（runner backend 実装置換）。

### Small テスト（外部依存なし・純粋ロジック / argv / バリデーション）

- **argv 構築（MF2）**: `_build_tmux_split_argv` の prefix が
  `["tmux", "split-window", "-d", "-h", "-P", "-F", "#{pane_id}", "-t", <target_pane>]` であることを
  **exact assert**。`-h` の存在と位置を固定する（PoC の `argv[:8]` assert を `-h` 込みへ更新）。
  末尾 1 要素が shlex 化された wrapper コマンドで、wrapper path / agent / prompt / verdict / model /
  effort を含むことを確認。
- **fail-fast バリデーション**: `tmux` 不在（`shutil.which` → None）→ `CLINotFoundError`。`$TMUX`
  未設定 → `CLINotFoundError("inside tmux"...)`。`$TMUX_PANE` 未設定 → `CLINotFoundError`。
  `tmux -V` が `tmux 2.9` → `CLINotFoundError("tmux >= 3.0")`。`step.agent` 不正 → `ValueError`。
  `prompt.txt` 不在 → `FileNotFoundError`。
- **`#{pane_dead}` マッピング**: `display-message` の stdout が `"1"` → dead True、`"0"` → False、
  返り値非ゼロ（pane lookup 失敗）→ dead True。

### Medium テスト（subprocess を fake tmux に差し替え・file I/O 結合）

- **pane lifecycle**（`subprocess.run` を fake tmux に patch、`verdict.yaml` を test 側で生成）:
  - verdict 出現 + `close_on_verdict=true` → `kill-pane -t %id` が呼ばれ、`CLIResult(full_output="",
    session_id=...)` を返す。
  - `close_on_verdict=false` → `set-option -p -t %id remain-on-exit on` が呼ばれ、`kill-pane` は
    呼ばれない。metadata に `#{pane_dead}=1` が保存される。
  - verdict 前に `#{pane_dead}=1` → `CLIExecutionError`（`terminal.log` tail を含む fail-loud）。
  - `timeout` 経過 → best-effort `kill-pane` の後に `StepTimeoutError`。
  - transcript: `pipe-pane -o -t %id 'cat >> .../terminal.log'` が呼ばれる。
  - Codex session id: `terminal.log` の `codex resume <uuid>` 抽出、未出力時の store fallback、
    grace（≤5s）経路（現行テストを移植）。
- **dispatch 回帰 guard（MF3）**: runner dispatch で `agent_runner="interactive_terminal"` のとき
  `execute_interactive_terminal(workdir=...)` に渡る値が headless の `execute_cli(workdir=...)` と
  同一の `effective_workdir` であることを固定する（backend 切替で workdir 解決が分岐しないこと）。

### Large テスト（`large_local`: 実 tmux + fake `claude`/`codex` bin、ネットワーク無し）

- **wrapper + 実 tmux の結合**（PoC の `TestInteractiveTerminalWrapper` 相当）: PATH に
  verdict.yaml を書く fake `claude` / `codex` を置き、実 tmux pane（3.0+）で wrapper を起動して
  end-to-end を確認する。観点: pane 生成 → `pipe-pane` で `terminal.log` 記録 → verdict 検知 →
  `kill-pane` 後に `list-panes` が当該 pane を返さない → session 継続。実 tmux が前提のため
  `large_local` マーカーで分離する。
- **省略しない根拠**: 実行時コード変更につき Large 観点を定義する。ただし **real `claude` / `codex`
  ライブ疎通（実 API 課金・対話 CLI 自動化困難）は既存方針どおり意図的に手動**
  （`docs/cli-guides/interactive-terminal-runner.md` § 手動検証手順）とし、自動 Large は fake bin +
  実 tmux でカバーする。これは `testing-convention.md` の「物理的に作成不可（実 API の自動化困難）」に
  当たる正当な省略であり、fake bin による回帰テストで振る舞いを担保する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/007-interactive-terminal-runner.md | なし | 本 Issue が実装する決定そのもの（v2 承認済み, commit 552247a〜）。追記不要 |
| docs/cli-guides/interactive-terminal-runner.md | **あり** | 前提を kitty→tmux / `$TMUX` 必須 / `tmux >= 3.0` / 右 pane へ更新。transcript を「pipe-pane で常時記録（util-linux script 分岐を削除）」に書き換え。トラブルシュート更新 |
| docs/ARCHITECTURE.md | **あり** | § Runner backend dispatch（L151-167）と dispatch ツリー（L129-133 の "kitty" 記述）、L335 の terminal.log 注記を tmux 前提へ更新 |
| docs/cli-guides/github-mode.md | **あり** | L73 "`kitty` 上で…" を tmux 前提へ更新 |
| docs/cli-guides/local-mode.md | **あり** | L45 "`kitty` 上で…" を tmux 前提へ更新 |
| docs/reference/python/ | なし | コーディング規約・型の変更なし |
| docs/dev/ | なし | ワークフロー・テスト規約の変更なし |
| CLAUDE.md | なし | 規約・必読 doc 一覧の変更なし（Interactive Terminal Runner のリンク先は不変） |

> kitty 由来残骸の最終確認は実装時に
> `rg -n "kitty|script\(1\)|_build_kitty|/proc|kill_processes|signal_matches|pid_exists" kaji_harness tests docs`
> で行い、履歴説明の意図的残置（ADR 改訂履歴等）を除き tmux 前提へ揃える。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| ADR 007 v2（承認済み・本 Issue が実装する決定） | `docs/adr/007-interactive-terminal-runner.md` | 「terminal backend は tmux 単独」「`split-window -d -h -P -F '#{pane_id}' -t "$TMUX_PANE"` で pane id を回収」「終了トリガは verdict.yaml の出現」「kill-pane は best-effort cleanup でレイテンシ契約を持たない」「`close_on_verdict=false` は `set-option -p remain-on-exit on`」「transcript は `pipe-pane`」を決定として明記 |
| tmux man page（公式） | https://man7.org/linux/man-pages/man1/tmux.1.html | `split-window [-bdfhIvPZ]`（`-d` フォーカス維持 / `-h` 横分割 / `-P -F` で pane format 出力）、`pipe-pane [-IOo]`（`-o` で toggle 出力パイプ）、`kill-pane`、`capture-pane`、`display-message -p`、`set-option -p`（pane option）、format `#{pane_dead}` / `#{pane_dead_status}` / `#{pane_dead_signal}`、`remain-on-exit [on \| off \| failed]` を定義。local tmux 3.4 の man で当該 flag を実在確認済み |
| tmux CHANGES（公式・version 要件の裏付け） | https://github.com/tmux/tmux/blob/master/CHANGES | 「CHANGES FROM 2.9 TO 3.0: Add pane options, set with `set-option -p` … Pane options inherit from window options …」。per-pane `remain-on-exit`（`set-option -p`）が **tmux 3.0 で追加**されたことが、設計の `tmux >= 3.0` 要件の一次根拠 |
| 現行 runner / wrapper（置換対象の実体） | `kaji_harness/interactive_terminal.py` / `kaji_harness/assets/interactive-terminal/wrapper.sh` | 削除対象の `_kill_processes_matching` 等（/proc scan）と `run_with_transcript`（script(1)）の現状、および維持する Codex session id 抽出ロジックの所在 |
| PoC 参照実装（検証済み・実装の下地） | 局所 worktree `kaji-poc-tmux-lifecycle-227`（branch `poc/tmux-lifecycle-227`、作業ツリー） | tmux 機構の動作確認済み下地。**注**: 当該 worktree はローカルかつ未コミットのためレビュー時にアクセスできない可能性がある。本設計の各決定は上記 ADR / tmux man / CHANGES（いずれも公開・コミット済み）で独立に裏付けており、PoC は実装者の参照便宜に留める |
| real-agent 検証レポート | `tmp/2026-06-tmux-lifecycle-real-agent-verification/report.md`（ローカル / gitignored） | 検証結論（終了トリガ＝verdict 出現 / 右 pane / best-effort cleanup）は **コミット済みの ADR 007 v2 に反映済み**。レポート自体は gitignored でレビュー不可のため、一次根拠としては ADR を参照する |
