# Claude Code CLI ガイド

## 概要

このガイドは、kaji workflow の実行や連携で使う Claude Code CLI の操作面をまとめたものです。

検証環境:

| 項目 | 値 |
|------|----|
| CLI | Claude Code `2.1.202` |
| 検証日 | 2026-07-08 |
| 検証方法 | `claude --version`、`claude --help`、一部サブコマンドの `--help` 出力 |

ローカルに入っている CLI の正本はコマンドラインです。Claude Code を更新した後は
`claude --help` を再確認してください。

公式ドキュメント:

- <https://docs.anthropic.com/claude-code>

## 1. コマンド形

Claude Code はデフォルトでインタラクティブセッションを開始します。

```bash
claude [options] [prompt]
```

非インタラクティブな出力には print mode を使います。

```bash
claude -p "Summarize this repository"
claude --print --output-format json "Return a short status report"
```

インストール済み CLI には以下のサブコマンドがあります。

| コマンド | 用途 |
|----------|------|
| `claude agents` | バックグラウンドエージェントを管理する |
| `claude auth` | 認証を管理する |
| `claude auto-mode` | auto mode classifier の設定を確認する |
| `claude doctor` | auto-updater と実行環境の健全性を確認する |
| `claude gateway` | enterprise auth/telemetry gateway を実行する |
| `claude install` | native build をインストールする |
| `claude mcp` | MCP サーバーを設定・管理する |
| `claude plugin` / `claude plugins` | プラグインを管理する |
| `claude project` | Claude Code の project state を管理する |
| `claude setup-token` | 長期認証トークンを設定する |
| `claude ultrareview` | cloud-hosted multi-agent code review を実行する |
| `claude update` / `claude upgrade` | 更新を確認してインストールする |

## 2. 主要オプション

| オプション | 短縮形 | 備考 |
|------------|--------|------|
| `--print` | `-p` | 応答を出力して終了する。スクリプトやパイプ向け。 |
| `--model <model>` | | モデル alias または full model name。help の例には `fable`、`opus`、`sonnet`、`claude-fable-5` などが含まれる。 |
| `--output-format <format>` | | print mode の出力形式: `text`、`json`、`stream-json`。 |
| `--input-format <format>` | | print mode の入力形式: `text` または `stream-json`。 |
| `--session-id <uuid>` | | 指定 UUID のセッションを開始または使用する。 |
| `--continue` | `-c` | 現在のディレクトリの最新 conversation を継続する。 |
| `--resume [value]` | `-r` | session ID で再開、または任意の検索語で picker を開く。 |
| `--fork-session` | | resume / continue 時に新しい session ID へ分岐する。 |
| `--from-pr [value]` | | PR 番号、URL、または picker 検索語で PR 紐づきセッションを再開する。 |
| `--name <name>` | `-n` | セッションの表示名を設定する。 |
| `--add-dir <directories...>` | | 追加ディレクトリへのアクセスを許可する。 |
| `--system-prompt <prompt>` | | system prompt を置き換える。 |
| `--append-system-prompt <prompt>` | | default system prompt に追記する。 |
| `--settings <file-or-json>` | | JSON ファイルまたは JSON 文字列から settings を読み込む。 |
| `--setting-sources <sources>` | | settings source を選ぶ: `user`、`project`、`local`。 |
| `--debug [filter]` | `-d` | 任意の filter 付きで debug 出力を有効化する。 |
| `--debug-file <path>` | | debug log をファイルに書き出す。 |
| `--verbose` | | 設定済み verbose mode を上書きする。 |
| `--version` | `-v` | バージョンを表示する。 |
| `--help` | `-h` | help を表示する。 |

system prompt はファイルからも読み込めます。

```bash
claude --system-prompt-file ./system.txt "Task"
claude --append-system-prompt-file ./rules.txt "Task"
```

## 3. Print Mode と構造化出力

print mode は連携用途で扱いやすい実行経路です。

```bash
claude -p "Explain the current branch"
```

デフォルトは text 出力です。JSON 出力では最後に単一の result object が返ります。

```bash
claude -p --output-format json "Return a one sentence summary"
```

streaming output は JSONL 形式の event を出力します。

```bash
claude -p --output-format stream-json --verbose "Analyze this change"
```

realtime input pipeline では stream input と stream output を組み合わせます。

```bash
producer | claude -p \
  --input-format stream-json \
  --output-format stream-json \
  "Process these events"
```

print mode で有用なオプション:

| オプション | 備考 |
|------------|------|
| `--json-schema <schema>` | JSON Schema で構造化出力を検証する。 |
| `--max-budget-usd <amount>` | API 利用額の上限に達したら停止する。 |
| `--fallback-model <model>` | primary model が overload または unavailable の場合に fallback model を使う。 |
| `--no-session-persistence` | セッションを保存しない。`--print` でのみ動作する。 |
| `--include-partial-messages` | `--print --output-format stream-json` で partial chunk を含める。 |
| `--include-hook-events` | `--output-format stream-json` で hook lifecycle event を含める。 |
| `--prompt-suggestions [value]` | print / SDK mode で予測 next prompt を出力する。 |
| `--replay-user-messages` | stream input / stream output で user message を stdout に再出力する。 |

## 4. セッション

Claude Code のセッションは再開できます。

```bash
claude -p "Start a task"
claude -p -c "Continue the latest task"
claude -p -r "$SESSION_ID" "Continue this session"
claude -p -r "$SESSION_ID" --fork-session "Explore another path"
claude --from-pr 123 "Resume the PR-linked session"
```

`--session-id <uuid>` には有効な UUID だけを渡します。

再利用しない一時的な automation では以下を使います。

```bash
claude -p --no-session-persistence "One-off check"
```

## 5. Permissions と Tool Control

tool availability と permission behavior は別々に制御します。

| オプション | 用途 |
|------------|------|
| `--tools <tools...>` | 利用可能な built-in tool を制限する。`""` で全 tool 無効、`default` で全 built-in tool を使用、または `Bash,Edit,Read` のような名前を指定する。 |
| `--allowedTools` / `--allowed-tools <tools...>` | prompt なしで許可する tool または tool pattern を指定する。 |
| `--disallowedTools` / `--disallowed-tools <tools...>` | 拒否する tool または tool pattern を指定する。 |
| `--permission-mode <mode>` | permission mode を設定する。選択肢は `acceptEdits`、`auto`、`bypassPermissions`、`manual`、`dontAsk`、`plan`。 |
| `--dangerously-skip-permissions` | permission check を bypass する alias。隔離環境だけで使う。 |
| `--allow-dangerously-skip-permissions` | bypass mode を利用可能にするが、default では有効化しない。 |
| `--permission-prompt-tool <tool>` | permission prompt を MCP tool に委譲する。 |

例:

```bash
claude -p \
  --allowedTools "Read" "Grep" "Glob" \
  "Inspect the documentation"

claude -p \
  --tools "Read,Grep,Glob" \
  --permission-mode dontAsk \
  "Report on this repository without editing files"
```

permission bypass 系オプションは、container、VM、または破棄可能な sandbox の外では危険です。

## 6. Configuration、Safety、Environment Modes

Claude Code は project、local、user settings を読み込めます。

```bash
claude --setting-sources user,project,local
claude --settings ./settings.json "Task"
claude --settings '{"permissions":{"allow":["Read"]}}' "Task"
```

troubleshooting や context 削減に使う mode:

| オプション | 用途 |
|------------|------|
| `--safe-mode` | CLAUDE.md、skills、plugins、hooks、MCP servers、custom commands、themes、keybindings などの customization を無効化して起動する。 |
| `--bare` | explicit-context automation 向けの minimal mode。hooks、LSP、plugin sync、attribution、auto-memory、background prefetch、keychain read、CLAUDE.md auto-discovery を skip する。 |
| `--exclude-dynamic-system-prompt-sections` | machine-specific な default-prompt section を first user message に移し、prompt-cache reuse を改善する。 |
| `--ax-screen-reader` | screen-reader friendly な出力にする。 |

`--bare` では `--system-prompt`、`--append-system-prompt`、`--add-dir`、
`--mcp-config`、`--settings`、`--agents`、`--plugin-dir` などで明示的に context を渡します。

## 7. Agents と Background Work

現在のセッションの main agent を選ぶには `--agent` を使います。

```bash
claude --agent reviewer "Review this change"
```

セッション単位の custom agent 定義には `--agents` を使います。

```bash
claude --agents '{
  "reviewer": {
    "description": "Reviews code",
    "prompt": "You are a careful code reviewer."
  }
}' "Review the current branch"
```

background agent としてセッションを開始します。

```bash
claude --background "Investigate flaky tests"
```

background agent を管理します。

```bash
claude agents
claude agents --json
claude agents --json --all
```

主な `claude agents` オプション:

| オプション | 用途 |
|------------|------|
| `--cwd <path>` | 指定 path 配下で開始した background session だけを表示する。 |
| `--json` | active session を JSON として出力して終了する。 |
| `--all` | `--json` と併用し、completed session も含める。 |
| `--model <model>` | agent view から dispatch する session の default model。 |
| `--agent <agent>` | dispatch session の default agent。 |
| `--permission-mode <mode>` | dispatch session の default permission mode。 |
| `--add-dir <directory>` | dispatch session に追加で許可する directory。 |

## 8. Worktrees と Terminal Layout

Claude Code はセッション用の git worktree を作成できます。

```bash
claude -w feature-name "Work in an isolated worktree"
claude --worktree feature-name "Work in an isolated worktree"
```

`--worktree` と一緒に `--tmux` を使うと tmux session を作成します。

```bash
claude --worktree feature-name --tmux "Start work"
claude --worktree feature-name --tmux=classic "Start work"
```

`claude --help` は網羅的ではありません。CLI reference は引き続き、agent team の表示モード
（`in-process`、`auto`、`tmux`、`iterm2`）を選ぶための `--teammate-mode` を文書化しています。
一方、`--tmux` は worktree 用の tmux session を作成するための option です。

## 9. MCP Servers

MCP servers は `claude mcp` で管理します。

```bash
claude mcp list
claude mcp get <name>
claude mcp remove <name>
```

command line、JSON、Claude Desktop から server を追加できます。

```bash
claude mcp add my-server -- my-command --some-flag arg1
claude mcp add --transport http sentry https://mcp.sentry.dev/mcp
claude mcp add-json my-server '{"type":"stdio","command":"my-command"}'
claude mcp add-from-claude-desktop
```

authentication と project choices:

```bash
claude mcp login <name>
claude mcp logout <name>
claude mcp reset-project-choices
claude mcp serve
```

セッション単位の MCP 制御:

| オプション | 用途 |
|------------|------|
| `--mcp-config <configs...>` | JSON ファイルまたは JSON 文字列から MCP server 定義を読み込む。 |
| `--strict-mcp-config` | `--mcp-config` 以外の MCP server を無視する。 |

## 10. Plugins

Plugins は `claude plugin` または `claude plugins` で管理します。

```bash
claude plugin list
claude plugin install <plugin>
claude plugin enable <plugin>
claude plugin disable <plugin>
claude plugin update <plugin>
claude plugin uninstall <plugin>
```

開発と確認用のコマンド:

```bash
claude plugin init my-plugin
claude plugin validate ./my-plugin
claude plugin details my-plugin
claude plugin eval ./my-plugin
claude plugin tag ./my-plugin
```

セッション単位の plugin options:

| オプション | 用途 |
|------------|------|
| `--plugin-dir <path>` | この session で plugin directory または zip を読み込む。repeatable。 |
| `--plugin-url <url>` | この session で plugin zip を取得する。repeatable。 |

## 11. Authentication、Installation、Health Checks

authentication:

```bash
claude auth login
claude auth logout
claude auth status
```

installation と updates:

```bash
claude install
claude install latest
claude install stable
claude install <version>
claude update
```

health check:

```bash
claude doctor
```

`claude doctor` は `.mcp.json` の stdio server を spawn する場合があります。信頼できる
directory でだけ実行してください。

## 12. kaji Integration Notes

kaji では通常、Claude Code は手動ではなく workflow runner から起動されます。主な注意点は以下です。

- kaji workflow runner に合わせて print mode または interactive terminal mode を使う。
- working directory と worktree boundary を明示する。
- workflow completion signal には `verdict.yaml` などの artifact-backed verdict を優先する。
- runner environment が破棄可能でない限り、広い permission bypass は避ける。
- workflow に MCP、plugins、settings が必要な場合は repository configuration または runner command で明示する。

agent step を手動で debug するときは以下から始めます。

```bash
claude --version
claude --help
claude -p --output-format json "Return a JSON status summary"
```

## 13. Codex CLI との比較

| 機能 | Claude Code | Codex CLI |
|------|-------------|-----------|
| 非インタラクティブ実行 | `claude -p` | `codex exec` |
| 最新セッションの再開 | `claude -c` | `codex exec resume --last` |
| ID 指定再開 | `claude -r <id>` | `codex exec resume <id>` |
| JSON result | `--output-format json` | `--json` |
| streaming result | `--output-format stream-json` | JSONL event stream |
| model selection | `--model` | `-m` |
| 追加 working directories | `--add-dir` | working directory selection は `-C` |
| tool control | `--tools`、`--allowedTools`、`--disallowedTools` | sandbox と MCP configuration |
| background agents | `claude agents`、`--background` | 同等機能なし |
| worktree creation | `--worktree` / `-w` | 同等機能なし |

## 14. Troubleshooting

### Stream JSON Requires Verbose Output

stream JSON output で verbose mode が必要というエラーが出る場合は `--verbose` を追加します。

```bash
claude -p --output-format stream-json --verbose "Question"
```

### Session Resume Uses the Wrong Context

明示的な session ID を使うか、一時的な non-persistent session を開始します。

```bash
claude -p -r "$SESSION_ID" "Continue"
claude -p --no-session-persistence "Run once"
```

### Configuration Appears Broken

safe mode または bare mode を使い、Claude Code 本体と project customization を切り分けます。

```bash
claude --safe-mode
claude --bare --add-dir . "Inspect this repository"
```

### Permission Prompts Block Automation

利用可能 tool を絞り、task に合う permission mode を選びます。

```bash
claude -p \
  --tools "Read,Grep,Glob" \
  --permission-mode dontAsk \
  "Read-only documentation review"
```

## 15. Verification Log

| 領域 | 情報源 | 検証 |
|------|--------|------|
| Version | `claude --version` | 2026-07-08 に `2.1.202 (Claude Code)` を返すことを確認。 |
| Top-level options and commands | `claude --help` | 2026-07-08 に確認。 |
| Authentication commands | `claude auth --help` | 2026-07-08 に確認。 |
| Background agents | `claude agents --help` | 2026-07-08 に確認。 |
| MCP commands | `claude mcp --help` | 2026-07-08 に確認。 |
| Plugin commands | `claude plugin --help` | 2026-07-08 に確認。 |
| Install command | `claude install --help` | 2026-07-08 に確認。 |
| Doctor command | `claude doctor --help` | 2026-07-08 に確認。 |
