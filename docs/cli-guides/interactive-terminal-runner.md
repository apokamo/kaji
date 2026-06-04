# Interactive Terminal Runner

`kaji run` の agent step を、headless CLI ではなく `kitty` 上の通常 `claude` / `codex`
対話 CLI で実行する runner backend（Issue #224）。agent が attempt directory の
`verdict.yaml` を書いたら、kaji は artifact-primary 経路（[ADR 005](../adr/005-artifact-primary-verdict.md)）
で verdict を読み、次 step へ進む。

技術選定の経緯は [ADR 007](../adr/007-interactive-terminal-runner.md)、runner dispatch の
位置づけは [ARCHITECTURE](../ARCHITECTURE.md) § Runner backend dispatch を参照。

## いつ使うか

- 従量課金の headless 経路ではなく、通常コンソール利用に近い形で workflow を進めたいとき。
- step ごとに Claude / Codex の session を `--resume` / `codex resume` で引き継ぎたいとき。
- agent の最終状態を verdict 後も terminal に残して確認したいとき（`close_on_verdict = false`）。

`agent_runner` 未指定時は既存の `headless` runner が使われる。既存 workflow / CI の挙動は
変わらない。

## 前提

- `kitty` が PATH にあること。**無ければ step failure として fail-fast** する（自動 fallback /
  他 terminal 探索はしない）。
- `claude` / `codex` CLI が PATH にあること（runner が起動する agent）。
- transcript（`terminal.log`）は **util-linux 互換の `script(1)` がある環境のみ** best-effort で
  記録される。`script` 不在 / 非 util-linux（BSD・macOS native 等で GNU long option 非対応）の
  環境では transcript 無しで agent を直接起動して継続する。

## 設定

`[execution]` は **workflow YAML ではなく repository config** に書く。`kaji run` は `--workdir`
または現在の cwd から親方向へ `.kaji/config.toml` を探索し、その directory を repository root と
する。

tracked な既定値は `.kaji/config.toml`、個人環境だけで切り替える場合は gitignored の
`.kaji/config.local.toml` に書く。

```toml
# .kaji/config.toml または .kaji/config.local.toml
[execution]
default_timeout = 2400
agent_runner = "interactive_terminal"            # "headless"（既定） | "interactive_terminal"
interactive_terminal_close_on_verdict = true     # 既定 true
```

`ExecutionConfig` のフィールド:

| フィールド | 型 | 既定 | 説明 |
|-----------|-----|------|------|
| `default_timeout` | `int` | （必須） | step timeout 秒 |
| `agent_runner` | `"headless"` \| `"interactive_terminal"` | `"headless"` | runner backend |
| `interactive_terminal_close_on_verdict` | `bool` | `true` | verdict 検知後に terminal を閉じるか |

`agent_runner` が許可値以外なら **config load 時点で `ConfigLoadError`**（fail-fast）。

### overlay

`.kaji/config.local.toml` の `[execution]` は `[provider]` と同じく **top-level section 内の
key 単位** で `.kaji/config.toml` の同名 key を上書きする。例えば tracked が
`agent_runner = "headless"` でも、overlay に `agent_runner = "interactive_terminal"` だけ書けば
個人環境のみ interactive terminal に切り替えられる（`default_timeout` は tracked のまま）。

```toml
# .kaji/config.local.toml （gitignored, 個人環境）
[execution]
agent_runner = "interactive_terminal"
```

## CLI option（`kaji run`）

```bash
--agent-runner <headless|interactive-terminal>
--interactive-terminal-close-on-verdict
--no-interactive-terminal-close-on-verdict
```

- `--agent-runner interactive-terminal`: この実行だけ interactive terminal runner を使う
  （config value `interactive_terminal` に正規化。CLI 公開値は hyphen 区切りのみ）。
- `--agent-runner headless`: この実行だけ headless に戻す（config が interactive_terminal でも上書き）。
- `--interactive-terminal-close-on-verdict` / `--no-...`: この実行だけ close-on-verdict を上書き。
  どちらも未指定なら config 値を維持する。

### 設定の優先順位（固定）

1. `kaji run` CLI option
2. `.kaji/config.local.toml` の `[execution]`
3. `.kaji/config.toml` の `[execution]`
4. built-in default（`agent_runner = "headless"`, `interactive_terminal_close_on_verdict = true`）

### 使用例

```bash
# repository config で interactive_terminal を既定にして実行
kaji run .kaji/wf/feature-development.yaml 224

# この実行だけ interactive terminal + terminal を残す
kaji run .kaji/wf/feature-development.yaml 224 \
  --agent-runner interactive-terminal \
  --no-interactive-terminal-close-on-verdict

# この実行だけ headless に戻す
kaji run .kaji/wf/feature-development.yaml 224 --agent-runner headless
```

## 振る舞い

1. runner は `kitty --title kaji-<agent>-<step> --hold <wrapper.sh> <9 args>` を起動する。
2. wrapper は最初に `cd <workdir>`（trusted な project worktree。`/tmp` や attempt directory を
   cwd にしない）してから通常 `claude` / `codex` を起動する。
3. wrapper は prompt 全文を埋め込まず、agent に「`prompt.txt` を読み、`verdict.yaml` を pure YAML
   で書く」ことだけを指示する。
4. runner は `verdict.yaml` を polling し、出現したら artifact-primary 経路で verdict を解決して
   workflow を継続する。
5. `interactive_terminal_close_on_verdict = true` なら、verdict 検知後に terminal / wrapper /
   detached agent を best-effort cleanup する。timeout 経路でも cleanup してから fail-loud する。

### session 継続

- **Claude**: fresh run では runner が UUID を生成して `--session-id` に渡し、同じ UUID を
  session state に保存する。resume step では保存済み id を `--resume` に渡す。
- **Codex**: fresh run 後、runner は `terminal.log` の `codex resume <uuid>` を抽出する。取れない
  場合は `CODEX_HOME/sessions/**/*.jsonl` → `~/.codex/sessions/**/*.jsonl` を mtime 降順に走査し、
  当該 attempt の `prompt.txt` / `verdict.yaml` path を含む rollout file の UUID を採用する。
  resume step では `codex resume <uuid>` で起動する。

### effort の注意（Codex）

Codex の `reasoning.effort = minimal` は現 tool 構成（`image_gen` / `web_search`）と衝突するため、
実用最小値は `low`。runner / wrapper は effort を pass-through し、最小値の選択は workflow step /
手動検証側の責務とする。

## 手動検証手順（real kitty + real Claude / Codex）

> 自動テストは fake terminal の Large（`large_local`）で振る舞いを担保する。real `kitty` +
> real `claude` / `codex` は **意図的に自動化しない**（実 API 課金・対話 CLI の自動化困難）ため、
> 以下を手動で確認する。

検証は **project worktree 内**で行う。`/tmp` や attempt directory を cwd にしない。

検証 model / effort（安価なもの）:

- Claude: `haiku` / `low`
- Codex: `gpt-5.4-mini` / `low`

手順:

1. `.kaji/config.local.toml` に `[execution] agent_runner = "interactive_terminal"` を設定する
   （または `kaji run ... --agent-runner interactive-terminal`）。
2. 最小 workflow を `kaji run` で起動し、`kitty` が開いて通常 `claude` が起動することを確認する。
3. agent が `verdict.yaml` を書いたら kaji が次 step へ進むことを確認する（**Claude fresh**）。
4. resume step が同じ session id（`--resume <uuid>`）で起動されることを確認する（**Claude resume**）。
5. Codex でも同様に fresh が `verdict.yaml` を書き（**Codex fresh**）、resume が `codex resume` で
   起動される（**Codex resume**）ことを確認する。
6. `interactive_terminal_close_on_verdict = true` で verdict 後に terminal が残らないこと、
   `false`（`--no-interactive-terminal-close-on-verdict`）で terminal が残ることを確認する。
7. `terminal.log` が attempt directory に残ることを確認する。

### transcript（`terminal.log`）の OS 別挙動

- **Linux / util-linux `script(1)` 環境**: `terminal.log` に transcript（ANSI 制御文字を含むが
  検証用途には使える）が記録される。手順 7 の成功条件に含める。
- **macOS native（util-linux script 非対応）/ `script` 不在**: transcript は記録されない。
  wrapper は agent を直接起動して継続する。この環境では手順 7（`terminal.log` 取得）を成功条件に
  **含めない**。

## トラブルシュート

| 症状 | 原因 / 対処 |
|------|-------------|
| `CLI 'kitty' not found` で即終了 | `kitty` を PATH に入れるか `--agent-runner headless` で実行する |
| step が timeout する | agent が `verdict.yaml` を書いていない。prompt の verdict 書き出し指示と path を確認 |
| `terminal.log` が空 / 無い | util-linux `script(1)` が無い環境。transcript は best-effort（取得されないのは正常） |
| Codex resume が効かない | `terminal.log` に resume 行が出ず、session store fallback も marker 不一致。`CODEX_HOME` を確認 |
| CLI が trust / permission 確認で止まる | cwd が project 外（`/tmp` 等）。workdir を project worktree に固定する |
