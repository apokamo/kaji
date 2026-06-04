# ADR 007: interactive_terminal runner（kitty 上で通常 CLI を起動）の追加

## ステータス

承認 (2026-06-05)

## コンテキスト

kaji の harness は agent step を headless CLI として起動してきた（Claude Code は
`claude -p --output-format stream-json --verbose`、Codex は `codex exec --json`）。
この経路は harness が stdout / JSONL を直接読むことを前提にしており、通常の対話
サブスク枠での CLI 利用とは分離される可能性がある。サブスク範囲の通常 CLI 利用に
近い形で workflow を進めたい利用者にとって、headless 経路は最適ではない。

ADR 005（Issue #220 / PR #221）で **artifact `verdict.yaml` を primary とする verdict
解決**（`resolve_verdict()`）と **`runs/<run_id>/steps/<step_id>/attempt-NNN/` layout**
が実装され、stdout を直接読まずに step 完了を判定できる土台が既にある。この土台の上に、
別ターミナルで通常 CLI を起動する runner を追加できる状態になった（Issue #224）。

事前検証（PoC, `tmp/2026-06-kaji-subscription-runner/`）で、`kitty` 上に通常 `claude` /
`codex` を起動し、agent が attempt directory の `verdict.yaml` を書けば harness は
workflow を継続できること、verdict 検知後に `kitty` / `script` / agent process を marker
path で探索して cleanup できること、Codex は verdict 後すぐ close すると resume 行が
出ない場合があり session store からの fallback 抽出が要ること、を確認済み。

## 決定

repository config の `[execution] agent_runner`（または `kaji run --agent-runner`）で選べる
新しい runner backend `interactive_terminal` を追加する（Issue #224）。

- **terminal backend は `kitty` 単独**。`kitty` が PATH に無ければ step failure として
  fail-fast する。自動 fallback や他 terminal 探索（`wt.exe` / `tmux` / `wezterm` /
  `gnome-terminal`）は **行わない**。
- runner（`execute_interactive_terminal()`）は `kitty --title kaji-<agent>-<step> --hold
  <wrapper.sh> <9 args>` を `subprocess.Popen` で起動し、attempt directory の
  `verdict.yaml` を polling する。出現したら ADR 005 の artifact-primary 経路で verdict を
  読み、次 step へ進む。stdout は読まない（`CLIResult.full_output=""`）。
- `interactive_terminal_close_on_verdict = true`（既定）で verdict 検知後に terminal /
  wrapper / detached agent を best-effort cleanup する。`false` で terminal を残す
  （デバッグ用）。timeout 経路でも best-effort cleanup してから fail-loud する。
- session 継続は ADR の既存 session state を再利用する。Claude fresh は runner が UUID を
  生成して `--session-id` に渡し、同じ UUID を session state に保存する。Codex fresh は
  `terminal.log` の `codex resume <uuid>` を抽出し、取れなければ
  `CODEX_HOME/sessions/**/*.jsonl` → `~/.codex/sessions/**/*.jsonl` を marker 一致で fallback
  抽出する。
- transcript は **util-linux 互換の `script(1)` がある環境のみ** attempt directory の
  `terminal.log` に best-effort 記録する。`script --version` に `util-linux` を含まない
  （BSD / macOS native 等で GNU long option 非対応）か `script` 不在なら、wrapper は agent を
  直接起動して継続する（fail-soft。transcript 無し）。
- `agent_runner` 未指定時は既存の `headless` runner を使い、headless の CLI 引数 / stdout
  logging / verdict 解決挙動は変更しない（互換維持）。

## 影響

- `kaji_harness/config.py`: `ExecutionConfig` に `agent_runner` /
  `interactive_terminal_close_on_verdict` を追加し、`[execution]` の `config.local.toml`
  overlay（key 単位）を実装する。
- `kaji_harness/cli_main.py`: `kaji run` に `--agent-runner` /
  `--interactive-terminal-close-on-verdict` / `--no-...` を追加（precedence 1）。
- `kaji_harness/runner.py`: agent dispatch を `agent_runner` で分岐。
- `kaji_harness/interactive_terminal.py`（新規）+
  `kaji_harness/assets/interactive-terminal/wrapper.sh`（新規、package data として
  wheel/sdist へ同梱）。
- docs: 本 ADR、`docs/ARCHITECTURE.md` § runner backend dispatch、
  `docs/cli-guides/interactive-terminal-runner.md`（新規）、`github-mode.md` /
  `local-mode.md` の `[execution]` 例。
- workflow YAML / 遷移仕様 / verdict 解決経路（ADR 005）は不変。runner backend の選択は
  repository config の責務であり workflow step の責務にしない。

## 代替案と却下理由

| 代替案 | 却下理由 |
|--------|----------|
| handoff runner（prompt / path を表示して人間が手動でスキル実行） | 通常 CLI を順に手動実行すれば足り、runner 抽象と半自動状態管理が増えるだけ |
| terminal backend を `wt.exe` / `tmux` / `wezterm` / `gnome-terminal` まで抽象化 | macOS native を含む対象環境を最も単純にカバーできるのは `kitty` 単独。広い抽象を先に作る価値が薄い |
| headless runner を `interactive_terminal` で置換 | 既存 workflow / CI の互換が壊れる。headless は推奨外でも互換用として残す |
| Codex `reasoning.effort = minimal` を最小値にする | PoC で現 tool 構成（`image_gen` / `web_search`）と衝突。実用最小は `low`。effort は runner で second-guess せず pass-through し、最小値選択は workflow step / 手動検証側の責務とする |

## 参照

- ADR 005: artifact-primary verdict resolution（本 runner の前提）
- Issue #220 / PR #221: attempt layout / `verdict.yaml` primary
- Issue #224: 本 runner の実装
- `docs/cli-guides/interactive-terminal-runner.md`: 設定・手動検証手順
