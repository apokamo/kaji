# [設計] interactive_terminal runner の追加

Issue: #224

## 概要

`kaji run` の agent step に `interactive_terminal` runner を追加する。通常の `claude` / `codex`
対話 CLI を `kitty` 上で起動し、agent が attempt directory の `verdict.yaml` を書いたら
artifact-primary 経路（Issue #220）で verdict を読んで workflow を継続する。runner 未指定の
既存 workflow は実装前と同じ headless runner で動く。

## 背景・目的

### ユースケース

- **サブスク利用者として**、従量課金の headless 経路ではなく通常コンソール利用に近い形で
  workflow を進めたいので、repository config または `kaji run` の CLI option で
  `agent_runner = "interactive_terminal"` を選び、`kitty` 上の通常 `claude` / `codex` で
  各 step を実行したい。
- **同利用者として**、step ごとに session を引き継ぎたいので、Claude / Codex の session id が
  保存され、後続 step が `--resume` / `codex resume` で同じ session を再開できるようにしたい。
- **デバッグ時には**、verdict 検知後も terminal を残して agent の最終状態を確認したいので、
  `interactive_terminal_close_on_verdict = false`（または `--no-interactive-terminal-close-on-verdict`）
  を指定したい。

### 現状の問題

現行 `kaji` は agent step を headless CLI として起動している（`claude -p --output-format
stream-json --verbose` / `codex exec --json`）。この経路は harness が stdout / JSONL を
直接読むことを前提にしており、通常の対話サブスク枠と分離される可能性がある。

Issue #220 / PR #221 で **artifact `verdict.yaml` を primary とする verdict 解決**（`resolve_verdict()`）
と **`runs/<run_id>/steps/<step_id>/attempt-NNN/` layout** が実装済みのため、stdout を直接
読まずに完了判定できる土台は既にある。本 Issue はこの土台の上に、別ターミナルで通常 CLI を
起動する `interactive_terminal` runner を追加する。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| handoff runner（prompt/path を表示して人間が手動でスキル実行） | 通常 CLI を順に手動実行すれば足り、runner 抽象と半自動状態管理が増えるだけ（proposal § なぜ handoff runner を作らないか） |
| terminal backend を `wt.exe` / `tmux` / `wezterm` / `gnome-terminal` まで抽象化 | macOS native を含む対象環境を最も単純にカバーできるのは `kitty` 単独。広い抽象を先に作る価値が薄い（proposal § シンプル化の経緯） |
| headless runner を `interactive_terminal` で置換 | 既存 workflow / CI の互換が壊れる。headless は推奨外でも互換用として残す（Issue スコープ境界） |

## インターフェース

### 入力（設定 / CLI）

#### repository config（`.kaji/config.toml` / `.kaji/config.local.toml`）

`[execution]` セクションに 2 つの key を追加する。`[execution]` は workflow YAML の step 定義
ではなく **repository config** に書く。`kaji run` は `--workdir` または cwd から親方向へ
`.kaji/config.toml` を探索し、その directory を repository root とする（既存 `KajiConfig.discover()`）。

```toml
# .kaji/config.toml または .kaji/config.local.toml
[execution]
default_timeout = 2400
agent_runner = "interactive_terminal"            # "headless"（既定） | "interactive_terminal"
interactive_terminal_close_on_verdict = true     # 既定 true
```

`ExecutionConfig`（`kaji_harness/config.py`）に追加するフィールド:

| フィールド | 型 | 既定 | 説明 |
|-----------|-----|------|------|
| `default_timeout` | `int` | （既存・必須） | 既存どおり |
| `agent_runner` | `Literal["headless", "interactive_terminal"]` | `"headless"` | runner backend |
| `interactive_terminal_close_on_verdict` | `bool` | `True` | verdict 検知後に terminal を閉じるか |

#### CLI option（`kaji run`）

```bash
--agent-runner <headless|interactive-terminal>
--interactive-terminal-close-on-verdict
--no-interactive-terminal-close-on-verdict
```

- `--agent-runner interactive-terminal` は config value `interactive_terminal` に正規化する
  （CLI 公開値は hyphen 区切りの `interactive-terminal` のみ。`interactive_terminal` を CLI で
  受け付けるかは実装で `interactive-terminal` 単独に固定し、underscore 形式は公開しない）。
- `--interactive-terminal-close-on-verdict` / `--no-...` は close-on-verdict の真偽を上書きする。
- いずれの option も未指定なら config 値を維持する（「未指定」と「明示 false」を区別するため、
  close-on-verdict 対は argparse 上 `default=None` の三状態で持つ）。

#### 設定の優先順位（固定）

1. `kaji run` CLI option
2. `.kaji/config.local.toml` の `[execution]`
3. `.kaji/config.toml` の `[execution]`
4. built-in default: `agent_runner = "headless"`, `interactive_terminal_close_on_verdict = true`

`.kaji/config.local.toml` の `[execution]` overlay は **top-level section 内の key 単位** で
`.kaji/config.toml` の同名 key を上書きする（既存 `[provider]` overlay と同じ merge 粒度）。

### 出力（副作用・生成物）

- **attempt directory**（`runs/<run_id>/steps/<step_id>/attempt-NNN/`、既存採番）に:
  - `prompt.txt`（既存。runner dispatch 前に書き込み済み）
  - `verdict.yaml`（agent が書く。primary verdict source）
  - `terminal.log`（新規。`script(1)` による terminal transcript。`script(1)` 不在時は未生成）
- **session state**（`session-state.json`、既存）に step 用 session id を保存（既存
  `state.save_session_id()` をそのまま使用）。
- runner の戻り値は既存 `CLIResult`（`full_output=""`, `session_id=<解決 id or None>`）。
  以降の verdict 解決・session 保存・attempt 終了処理は既存経路を通る。

### 使用例

```bash
# repository config で interactive_terminal を既定にして実行
kaji run .kaji/wf/feature-development.yaml 224

# この実行だけ interactive terminal + terminal を残す
kaji run .kaji/wf/feature-development.yaml 224 \
  --agent-runner interactive-terminal \
  --no-interactive-terminal-close-on-verdict

# この実行だけ headless に戻す（config が interactive_terminal でも上書き）
kaji run .kaji/wf/feature-development.yaml 224 --agent-runner headless
```

runner 本体（新規 `kaji_harness/interactive_terminal.py`）の公開 IF:

```python
def execute_interactive_terminal(
    *,
    step: Step,
    prompt_path: Path,
    verdict_path: Path,
    workdir: Path,
    timeout: int,
    session_id: str | None = None,
    close_on_verdict: bool = True,
) -> CLIResult:
    """kitty 上で通常 CLI を起動し verdict.yaml を待つ。

    Returns:
        CLIResult(full_output="", session_id=<解決した session id or None>)

    Raises:
        CLINotFoundError: kitty が PATH に無い / kitty 起動に失敗した。
        StepTimeoutError: timeout までに verdict.yaml が出現しなかった。
        ValueError / FileNotFoundError: step.agent 不正 / prompt.txt 不在。
    """
```

### エラー時の挙動

| 失敗 | 挙動 |
|------|------|
| `kitty` が PATH に無い | `CLINotFoundError` で fail-fast（fallback / terminal 探索はしない） |
| `agent_runner` が許可値以外 | config load 時点で `ConfigLoadError`（dispatch 前に止める） |
| `step.agent` が `claude` / `codex` 以外 | `ValueError`（runner 入口で弾く） |
| timeout までに `verdict.yaml` 不在 | `StepTimeoutError` → best-effort cleanup 後 re-raise |
| cleanup 失敗（verdict は PASS 済み） | verdict を優先し workflow 成功扱い、cleanup 失敗は log に残す |
| cleanup 失敗（timeout 経路） | step failure として fail-loud（cleanup 失敗で成功化しない） |

## 制約・前提条件

- **依存**: 外部コマンド `kitty`（terminal）、`script(1)`（transcript、任意）、`claude` / `codex`
  （agent CLI）。`kitty` 不在は fail-fast。`script(1)` は **util-linux 互換のみ** transcript に使い、
  不在時・非 util-linux（BSD/macOS native 等で GNU long option 非対応）時は transcript 無しで agent を
  直接起動して継続する（transcript は best-effort。Linux/util-linux 環境のみ取得）。`script` が PATH に
  存在するだけでは util-linux 互換とみなさず、`script --version` の `util-linux` 表記で判定する
  （§ Wrapper 契約）。
- **cwd 制約**: wrapper / agent の cwd・`--cd` は trusted な project worktree（`kaji run --workdir`
  が指す worktree、または config discovery で得た repository root）に固定する。`/tmp` や attempt
  directory を cwd にすると CLI の trust / permission 確認で停止しうる（PoC 確認済み）。
- **Codex effort**: `reasoning.effort = minimal` は現 tool 構成（`image_gen` / `web_search`）と
  衝突するため、Codex の実用最小 effort は `low`。runner / wrapper は effort を **pass-through** し、
  最小値の選択は workflow step / 手動検証側の責務とする（docs に明記。詳細は § 方針 末尾）。
- **互換性**: `agent_runner` 未指定時は headless runner を使い、headless の CLI 引数 / stdout logging /
  verdict 解決挙動を変更しない。
- **session 保存**: 既存 `SessionState.save_session_id()` / `get_session_id()` を再利用し、
  session state schema は変更しない。
- **verdict 解決**: 既存 `resolve_verdict()`（artifact → comment → stdout）を変更しない。
  interactive runner は stdout を持たない（`full_output=""`）が、artifact-primary 経路が
  `verdict.yaml` を読むため成立する。

## 変更スコープ

| ファイル | 変更 | 内容 |
|----------|------|------|
| `kaji_harness/config.py` | 改修 | `ExecutionConfig` に 2 フィールド追加 / `[execution]` overlay + 検証 |
| `kaji_harness/cli_main.py` | 改修 | `kaji run` に 3 CLI option 追加 / `cmd_run` で config override |
| `kaji_harness/runner.py` | 改修 | agent dispatch を `agent_runner` で分岐 |
| `kaji_harness/interactive_terminal.py` | 新規 | `execute_interactive_terminal()` + cleanup / session 抽出 |
| `assets/interactive-terminal/wrapper.sh` | 新規 | `kitty` 上で `claude` / `codex` を起動する wrapper |
| `kaji_harness/cli.py` | **変更なし** | headless の `execute_cli` は制約上不変（下記スコープ注記） |
| `kaji_harness/verdict.py` | **変更なし** | `resolve_verdict()` の artifact-primary 経路を再利用 |
| `kaji_harness/state.py` | **変更なし** | 既存 session state 管理を再利用 |
| `tests/test_config.py` | 改修 | `[execution]` フィールド / overlay / 検証のテスト |
| `tests/test_runner.py`（または既存 dispatch テスト） | 改修 | runner dispatch 分岐のテスト |
| `tests/test_interactive_terminal.py` | 新規 | runner / wrapper / resume / close / session fallback / timeout |
| docs（§ 影響ドキュメント） | 改修 / 新規 | config / CLI option / 手動検証手順 |

> **スコープ注記（Issue ファイルリストとの差分・要レビュー判断）**:
> 1. Issue のファイルリストは `kaji_harness/cli.py` を挙げるが、`kaji run` の argparse 定義と
>    `cmd_run` は実際には `kaji_harness/cli_main.py` にある。新 CLI option はそちらに追加する。
>    `cli.py` の `execute_cli`（headless 起動経路）は「既存 headless runner の CLI 起動引数変更」が
>    スコープ外のため **変更しない**。Issue のリストはあくまで変更候補の上限集合であり、本設計は
>    その範囲内で `cli.py` を据え置き `cli_main.py` を改修する判断とする。
> 2. Issue は `verdict.py` / `state.py` も候補に挙げるが、artifact-primary verdict 解決（#220）と
>    session state 管理は既存実装を **そのまま再利用** でき、変更不要と判断した。最小変更で
>    機能を成立させる設計意図。レビューで「ここは変えるべき」という指摘があれば再検討する。

## 方針（Minimal How）

### データフロー

```text
cmd_run (cli_main.py)
  1. KajiConfig.discover(start_dir)           # [execution] を overlay 込みで load
  2. CLI option override を config.execution に適用（dataclasses.replace）
  3. WorkflowRunner(config=...).run()

WorkflowRunner._run_step (runner.py, 既存 agent dispatch 箇所 L638 付近)
  4. attempt_dir 採番 → prompt.txt 書き込み（既存）
  5. if config.execution.agent_runner == "interactive_terminal":
         result = execute_interactive_terminal(
             step=current_step,
             prompt_path=attempt_dir / "prompt.txt",
             verdict_path=verdict_yaml_path,
             workdir=effective_workdir,
             timeout=resolved_timeout,
             session_id=session_id,
             close_on_verdict=config.execution.interactive_terminal_close_on_verdict,
         )
     else:
         result = execute_cli(...)            # 既存 headless 経路（不変）
  6. state.save_session_id(step.id, result.session_id)   # 既存
  7. resolve_verdict(attempt_dir=..., full_output=result.full_output, ...)  # 既存

execute_interactive_terminal (interactive_terminal.py, 新規)
  a. step.agent ∈ {claude, codex} を検証、prompt.txt 存在確認
  b. kitty を shutil.which で解決（無ければ CLINotFoundError）
  c. wrapper.sh path を解決（package root / assets/interactive-terminal/wrapper.sh）
  d. terminal_log = attempt_dir / "terminal.log"
  e. launch_session_id = uuid4()（Claude fresh のみ） / ""（resume / codex）
  f. argv = [kitty, --title, "kaji-<agent>-<step_id>", --hold, wrapper, <9 args>]
  g. subprocess.Popen(argv, cwd=workdir, start_new_session=True)
  h. verdict.yaml を polling（deadline = now + timeout, sleep ~2s）
  i. 出現したら session id を解決（claude: launch/resume id / codex: terminal.log → session store）
  j. close_on_verdict なら terminal / wrapper / detached agent を best-effort cleanup
  k. return CLIResult(full_output="", session_id=...)
  l. timeout 到達 → best-effort cleanup → StepTimeoutError
```

### 新規モジュール・関数の責務（名前のみ）

`kaji_harness/interactive_terminal.py`:

- `execute_interactive_terminal(...)` — runner 本体（上記 IF）。
- `_extract_codex_session_id(terminal_log, *, prompt_path, verdict_path)` — terminal.log の
  `codex resume <uuid>` を抽出し、無ければ session store fallback。
- `_extract_codex_session_id_from_store(*, prompt_path, verdict_path)` —
  `CODEX_HOME/sessions/**/*.jsonl` → `~/.codex/sessions/**/*.jsonl` を mtime 降順に走査し、
  jsonl 本文に当該 attempt の `prompt_path` / `verdict_path` marker を含む rollout file の
  UUID を返す。
- `_close_terminal(process, *, markers)` / `_kill_processes_matching(markers)` — verdict / timeout
  時の cleanup（process group SIGTERM→SIGKILL + `/proc/<pid>/cmdline` に marker path を含む
  detached process group の終了）。

> 実装の構造は参照用 PoC（`/home/aki/dev/kaji-poc-subscription-runner-real-cli/
> kaji_harness/interactive_terminal.py`）を一次情報とする（§ 参照情報に該当箇所を引用）。
> PoC は main に merge せず、本設計に沿って通常 workflow で作り直す。

### Runner Dispatch 契約

- `headless`: 既存 `execute_cli(...)` を呼ぶ。引数・stdout / log 処理を変更しない。
- `interactive_terminal`: 新規 `execute_interactive_terminal(...)` を呼ぶ。
- `agent_runner` が上記以外: **config load 時点**で `ConfigLoadError`（runner dispatch までに到達しない）。

### Wrapper 契約（`assets/interactive-terminal/wrapper.sh`）

引数順を固定する（9 個）:

```bash
wrapper.sh <agent> <prompt_path> <verdict_path> <terminal_log_path> \
           <workdir> <resume_session_id> <launch_session_id> <model> <effort>
```

- `<resume_session_id>`: resume する id。空文字なら fresh。
- `<launch_session_id>`: Claude fresh 用に runner が生成した UUID。Codex では空文字。
- 最初に `cd "$workdir"`（attempt directory や `/tmp` を cwd にしない）。
- prompt 全文は埋め込まず、次の指示文を agent に渡す:

  ```text
  Read the full task prompt from: <prompt_path>
  Carry out the requested workflow step in this existing workspace: <workdir>
  When the step is complete, write only a pure YAML verdict file to this exact path: <verdict_path>
  Do not wrap the YAML in Markdown. Use the valid status values described in the prompt.
  ```

- `script(1)` は **util-linux 互換のときだけ** transcript に使う。判定は
  `command -v script` で存在確認した上で `script --version` 出力に `util-linux` を含むか確認する
  （util-linux は `script from util-linux X.Y` を出力。BSD/macOS native の `script` は GNU long option
  `--quiet/--flush/--command` を持たず `--version` も非対応のため不一致になる）。
  - util-linux 互換: `script --quiet --flush --command <cmd> <terminal_log_path>` 経由で agent を起動。
  - 非互換 / 不在: agent を直接起動し、`transcript unavailable`（util-linux script 不在）を
    runner log（stderr）に残す。BSD long option を当てて起動前に失敗することは **しない**（fail-soft）。
  - これにより、`script` は存在するが GNU long option 非対応の環境（macOS native 等）でも
    wrapper は agent 起動へ進む。

### Agent Command 契約（Issue 本文どおり）

```bash
# Claude fresh
claude --dangerously-skip-permissions --model <model> --effort <effort> --session-id <launch_session_id> <prompt>
# Claude resume
claude --dangerously-skip-permissions --model <model> --effort <effort> --resume <resume_session_id> <prompt>
# Codex fresh
codex --cd <workdir> --dangerously-bypass-approvals-and-sandbox --model <model> --config 'model_reasoning_effort="<effort>"' <prompt>
# Codex resume
codex resume --cd <workdir> --dangerously-bypass-approvals-and-sandbox --model <model> --config 'model_reasoning_effort="<effort>"' <resume_session_id> <prompt>
```

model / effort が空のときは該当 option を付けない（PoC wrapper の `printf %q` による条件組み立て）。

### Session ID 契約

- **Claude**: fresh は runner が UUID を生成 → wrapper の `<launch_session_id>` に渡し、同じ UUID を
  session state に保存。resume は session state の id を `<resume_session_id>` に渡す。
- **Codex**: fresh 後、runner は `terminal.log` から `codex resume <uuid>` を抽出。取れなければ
  `CODEX_HOME/sessions/**/*.jsonl` → `~/.codex/sessions/**/*.jsonl` を fallback 走査し、jsonl 本文に
  当該 attempt の `prompt_path` / `verdict_path` を含む file の UUID を採用。resume は session state の
  id を `codex resume ... <resume_session_id> <prompt>` に渡す。

### Cleanup 契約

- `Popen` の process group を終了対象にする（`start_new_session=True` で取得）。
- detached 対策として、`prompt_path` / `verdict_path` / `terminal_log_path` を
  `/proc/<pid>/cmdline` の marker として探索し、該当 process group も終了対象にする。
- cleanup 失敗は workflow を成功扱いにしない。ただし verdict が既に PASS なら verdict を優先し、
  cleanup 失敗は log に残す（verdict 検知後の cleanup 失敗で step を失敗化しない）。timeout 経路では
  step failure として fail-loud。

### effort pass-through の判断（要レビュー）

Codex の `minimal` 禁止は runner のハード検証にせず、effort を pass-through し、最小値（Codex は `low`）の
選択を workflow step / 手動検証側の責務として docs に明記する方針とする。理由: effort は workflow step
解決値であり、runner backend が second-guess すると workflow 契約の責務分離を崩す。PoC も pass-through。
（ハード検証にすべきという反論があればレビューで再検討する。）

## テスト戦略

### 変更タイプ

**実行時コード変更**（config 解析 / CLI option / runner dispatch / 新規 runner / wrapper shell）。
恒久回帰テストを追加する。

### Small テスト

- **config**（`tests/test_config.py`）:
  - `[execution]` に `agent_runner` / `interactive_terminal_close_on_verdict` を書いたとき、
    `ExecutionConfig` に正しく反映される。
  - 両 key 未指定時の built-in default（`headless` / `True`）。
  - `agent_runner` 許可値以外（例 `"foo"`）で `ConfigLoadError`（fail-fast）。
  - `interactive_terminal_close_on_verdict` の型不正（非 bool）で `ConfigLoadError`。
  - overlay: `.kaji/config.local.toml` の `[execution]` key が `.kaji/config.toml` を上書き
    （key 単位 merge。`default_timeout` は tracked、runner key は overlay のみのケースを含む）。
- **CLI 正規化 / override**（`tests/test_config.py` または CLI テスト）:
  - `--agent-runner interactive-terminal` → config value `interactive_terminal` に正規化。
  - CLI option が `.kaji/config.local.toml` / `.kaji/config.toml` より優先（precedence 1）。
  - close-on-verdict の三状態（未指定で config 維持 / `--...` で true / `--no-...` で false）。
- **runner builder / 入口検証**（`tests/test_interactive_terminal.py`）:
  - kitty argv builder が期待 argv（`--title kaji-<agent>-<step>` / `--hold` / wrapper / 9 引数の順）を生成。
  - `kitty` 不在（`shutil.which` → None）で `CLINotFoundError`（fail-fast）。
  - `launch_session_id` 生成規則（Claude fresh のみ UUID / resume・codex は空）。
  - Codex `terminal.log` からの `codex resume <uuid>` 抽出（正規表現）。
  - Codex session store fallback: fake `CODEX_HOME` 配下に marker を含む rollout jsonl を置き、
    UUID を補完できる / marker 不一致なら採用しない。

### Medium テスト

- **runner dispatch**（`tests/test_runner.py` または既存 dispatch テスト）:
  - `agent_runner = "interactive_terminal"` で `execute_interactive_terminal` に routing される
    （monkeypatch / fake）。`agent_runner` 未指定（headless）で `execute_cli` に routing され、
    既存挙動が壊れない。
- **runner ふるまい**（`tests/test_interactive_terminal.py`、fake terminal command を使用）:
  - `verdict.yaml` 出現で `CLIResult` を返し、後続 verdict 解決へ繋がる。
  - `close_on_verdict = true` で cleanup が呼ばれ、`false` で呼ばれない。
  - timeout 時に `StepTimeoutError` + cleanup が呼ばれる。
- **wrapper shell**（`tests/test_interactive_terminal.py`、PATH に fake `claude`/`codex`/`script` を置く）:
  - `bash -n wrapper.sh`（構文）。
  - wrapper が `cd "$workdir"` してから agent を起動する（fake agent が cwd を記録）。
  - 引数順が Wrapper 契約と一致する（fake agent が argv を記録）。
  - Claude fresh / resume・Codex fresh / resume の command line が Agent Command 契約と一致。
  - transcript 分岐を 3 ケース検証する:
    - util-linux 互換 `script`（fake `script` が `--version` で `util-linux` を出力）がある場合、
      `terminal.log` path 経由で起動される。
    - `script` 不在の場合、agent を直接起動し transcript unavailable warning を出す。
    - **`script` は存在するが util-linux 非互換**（fake `script` が `--version` で util-linux を出さず、
      `--quiet/--flush/--command` を渡すと非 0 終了する）の場合、wrapper が long option で fail せず
      agent を直接起動する（fail-soft。fake agent が起動されたことを記録）。

### Large テスト（`large_local`）

- fake terminal command（`kitty` の代役）が `assets/interactive-terminal/wrapper.sh` と同じ引数順で
  起動され、wrapper 経由で `verdict.yaml` を作り、runner が verdict を解決して継続する E2E。
  ネットワーク無し・subprocess ありのため `@pytest.mark.large_local`。
- real `kitty` + real `claude` / `codex` を使うテストは **自動化しない**（手動検証で担保。§ 手動検証手順）。

省略するもの: real Claude / Codex を CI で実行するテストはスコープ外（Issue スコープ境界）。これは
「物理的に作成不可 / 環境依存」ではなく **意図的なスコープ外**（実 API 課金・対話 CLI の自動化困難）で、
代わりに fake terminal の Large + 手動検証で振る舞いを担保する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/007-interactive-terminal-runner.md`（新規） | あり | runner backend 追加・`kitty` 単独採用・handoff runner / 他 terminal 不採用という技術選定を永続化（feat の技術選定 ADR。proposal の rationale は gitignored tmp/ にしか無いため repo に残す） |
| `docs/ARCHITECTURE.md` | あり | runner dispatch（`execute_cli` ↔ `execute_interactive_terminal`）と `terminal.log` artifact を追記 |
| `docs/cli-guides/interactive-terminal-runner.md`（新規） | あり | `[execution]` 設定・CLI option・real kitty + real Claude/Codex の手動検証手順をまとめる（provider 非依存のため dedicated guide）。`terminal.log` transcript は util-linux/Linux 環境のみの best-effort であり、macOS native では取得されない（手動検証で transcript 有無を OS 別に明記し、macOS では transcript 取得を成功条件に含めない）旨を記載 |
| `docs/cli-guides/github-mode.md` / `docs/cli-guides/local-mode.md` | あり | § config.toml の `[execution]` 例に `agent_runner` / `close_on_verdict` を追記し、新 guide へ相互リンク |
| `docs/dev/workflow-authoring.md` | あり（小） | runner backend 選択は repository config（`[execution]`）であり workflow YAML step 責務ではない旨を補足 |
| `CLAUDE.md` | あり（小） | Documentation Index 表に新 CLI guide / ADR 行を追加 |
| `docs/reference/python/` | なし | 命名・型・docstring 規約に変更なし |
| `docs/dev/testing-convention.md` | なし | 既存規約に従うのみ |

## 参照情報（Primary Sources）

> **アクセス可能性の注記**: 一次情報のうち `tmp/2026-06-kaji-subscription-runner/*.md` と PoC worktree は
> **gitignored（`tmp/`）/ 別 worktree** であり feat-224 worktree には tracked されない。レビュワーが
> repo 内ファイルだけでも判断できるよう、設計判断の根拠となる該当箇所を下表に引用する。絶対パスは同一
> マシン上では読めるが、引用を一次の根拠とする。

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| subscription-runner 改修案 | `/home/aki/dev/kaji/tmp/2026-06-kaji-subscription-runner/subscription-runner-proposal.md` | 「`kitty` が見つからない場合は fail-fast する。自動 fallback や terminal 探索は行わない」「未指定時 default: `agent_runner = "headless"`, `interactive_terminal_close_on_verdict = true`」「`reasoning.effort = minimal` は現在の Codex tool 構成で `image_gen` / `web_search` と衝突したため、Codex の実運用上の最小 effort は `low`」 |
| interactive_terminal PoC 結果 | `/home/aki/dev/kaji/tmp/2026-06-kaji-subscription-runner/interactive-terminal-poc-result.md` | 「agent が `verdict.yaml` を書けば、harness は workflow を継続できる」「verdict 検知後、`kitty` だけでなく `script` / agent process も marker path で探索して cleanup できる」「Codex は…verdict 後すぐ close すると resume line が出ない場合があるため、`~/.codex/sessions/**/*.jsonl` から session ID を fallback 抽出する必要がある」。実 CLI 起動コマンド（Claude/Codex fresh/resume）と通し検証 PASS を確認済み |
| PoC runner 実装（参照） | `/home/aki/dev/kaji-poc-subscription-runner-real-cli/kaji_harness/interactive_terminal.py` | `execute_interactive_terminal(*, step, prompt_path, verdict_path, workdir, timeout, session_id=None, close_on_verdict=True) -> CLIResult` の構造、`_CODEX_RESUME_RE` / `_CODEX_SESSION_FILE_RE`、`start_new_session=True` + `os.killpg` + `/proc/<pid>/cmdline` marker cleanup を一次情報として踏襲 |
| PoC wrapper（参照） | `/home/aki/dev/kaji-poc-subscription-runner-real-cli/assets/interactive-terminal/wrapper.sh` | 9 引数の順、`cd "$workdir"`、`script --quiet --flush --command ... <terminal_log>`、Claude/Codex の fresh/resume 分岐の `printf %q` 組み立て。**ただし PoC の `run_with_transcript` は `command -v script` の有無のみで分岐**しており、BSD/macOS native の `script` を弾けない。本設計はここを意図的に分岐し、`script --version` の util-linux 判定 + 非互換時 fail-soft 直接起動を追加する（§ Wrapper 契約 / 制約・前提条件） |
| ADR 005（repo 内） | `docs/adr/005-artifact-primary-verdict.md` | 「verdict の受け渡しを artifact `verdict.yaml`（primary）→ … の順で解決」「次の interactive terminal runner は stdout を直接読む必要がない」— 本 runner が stdout を持たずとも成立する前提 |
| 現行 runner（repo 内） | `kaji_harness/runner.py` L638 付近 | agent dispatch の `execute_cli(...)` 呼び出し箇所。ここを `agent_runner` で分岐させる統合点。session 保存（L650-651）・`resolve_verdict()`（L671）は既存経路を再利用 |
| 現行 config（repo 内） | `kaji_harness/config.py` L27-31, L143-160, L173-313 | `ExecutionConfig` の現フィールドと `[execution]` 必須検証、`[provider]` overlay（key 単位 merge）の既存実装。`[execution]` overlay はこの provider overlay と同じ粒度で実装する |
| Issue #220 / PR #221 | GitHub Issue #220 / PR #221 | artifact-primary verdict resolution と attempt layout の前提（本 Issue の土台） |
