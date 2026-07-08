# Codex CLI セッションガイド

## 概要

このガイドは、kaji workflow の実行や連携で重要になる Codex CLI の挙動をまとめたものです。
対象はセッション開始、作業の再開、機械可読出力の取得、安全な automation flag の選び方です。

検証環境:

| 項目 | 値 |
|------|----|
| CLI | OpenAI Codex CLI `0.142.5` |
| 検証日 | 2026-07-08 |
| 検証方法 | `codex --version`、`codex --help`、一部サブコマンドの `--help` 出力、現行 OpenAI Codex manual |

ローカル挙動の正本はインストール済みのコマンドラインです。Codex CLI を更新した後は
`codex --help` を再確認してください。

公式ドキュメント:

- <https://developers.openai.com/codex/cli/reference/>
- <https://developers.openai.com/codex/cli/features/>
- <https://developers.openai.com/codex/noninteractive/>
- <https://developers.openai.com/codex/models/>

## 1. コマンド形

Codex はサブコマンドなしで起動すると interactive terminal UI を開始します。

```bash
codex [OPTIONS] [PROMPT]
```

非 interactive automation には `codex exec` を使います。

```bash
codex exec [OPTIONS] [PROMPT]
```

インストール済み CLI は、セッション関連で以下のコマンドを提供しています。

| コマンド | エイリアス | 用途 |
|----------|------------|------|
| `codex exec` | `codex e` | Codex を非 interactive に実行する。 |
| `codex exec resume` | | ID または `--last` で過去の非 interactive セッションを再開する。 |
| `codex resume` | | ID、picker、または `--last` で過去の interactive セッションを再開する。 |
| `codex fork` | | 過去の interactive セッションを新しい thread に fork する。 |
| `codex review` | | 非 interactive code review を実行する。 |
| `codex exec review` | | `exec` コマンド経路で code review を実行する。 |
| `codex apply` | `codex a` | Codex agent が生成した最新 diff を local tree に適用する。 |
| `codex cloud` | | Codex Cloud task を参照・管理する。 |
| `codex mcp` | | MCP server を管理する。 |
| `codex plugin` | | plugin と plugin marketplace を管理する。 |
| `codex features` | | feature flag を確認・永続化する。 |
| `codex doctor` | | local installation、config、auth、runtime の健全性を診断する。 |
| `codex sandbox` | | Codex 提供 sandbox 内でコマンドを実行する。 |
| `codex archive` / `codex unarchive` | | 保存済み interactive セッションを非表示化または復元する。 |
| `codex delete` | | 保存済み interactive セッションを完全に削除する。 |

## 2. 主要オプション

共通 runtime option:

| オプション | 短縮形 | 備考 |
|------------|--------|------|
| `--model <model>` | `-m` | セッションまたは実行で使う model を選ぶ。 |
| `--cd <dir>` | `-C` | agent の working directory を設定する。 |
| `--sandbox <mode>` | `-s` | `read-only`、`workspace-write`、`danger-full-access` のいずれか。 |
| `--ask-for-approval <policy>` | `-a` | `untrusted`、`on-request`、`never` のいずれか。`on-failure` は deprecated。 |
| `--add-dir <dir>` | | main workspace に加えて別の writable root を追加する。 |
| `--config <key=value>` | `-c` | 1 回の invocation だけ config 値を上書きする。値は可能なら TOML として parse される。 |
| `--enable <feature>` / `--disable <feature>` | | 1 回の invocation だけ feature flag を上書きする。 |
| `--strict-config` | | config にこの CLI version が認識しない field がある場合に失敗する。 |
| `--image <file>` | `-i` | prompt に 1 つ以上の画像を添付する。 |
| `--profile <name>` | `-p` | base user config に `$CODEX_HOME/<name>.config.toml` を重ねる。 |
| `--oss` | | open-source provider を使う。 |
| `--local-provider <provider>` | | local model 使用時に `lmstudio` または `ollama` を選ぶ。 |
| `--search` | | その実行で live web search を有効化する。 |
| `--no-alt-screen` | | alternate screen ではなく inline で interactive TUI を実行する。 |
| `--dangerously-bypass-approvals-and-sandbox` | | approval と sandbox を無効化する。外部 sandbox 内だけで使う。 |
| `--dangerously-bypass-hook-trust` | | この invocation で persisted hook trust なしに enabled hook を実行する。 |

`codex exec` には automation 向けの flag があります。

| オプション | 備考 |
|------------|------|
| `--json` | JSON Lines event stream を stdout に出力する。 |
| `--output-last-message <file>` / `-o <file>` | agent の最終 message をファイルへ書き出す。 |
| `--output-schema <file>` | 最終 response が JSON Schema に一致することを要求する。 |
| `--ephemeral` | session file を disk に永続化しない。 |
| `--skip-git-repo-check` | Git repository 外での実行を許可する。 |
| `--ignore-user-config` | `$CODEX_HOME/config.toml` を読み込まない。auth は引き続き `CODEX_HOME` を使う。 |
| `--ignore-rules` | user / project の execpolicy `.rules` file を読み込まない。 |
| `--color <always|never|auto>` | ANSI color output を制御する。 |

`0.142.5` では、`codex exec resume --help` にも `--json`、
`--output-last-message`、`--output-schema` が表示されます。したがって、新規実行と
resume した非 interactive 実行で同じ structured-output 経路を使えます。

## 3. セッション

### 3.1 非 Interactive セッションを開始する

```bash
codex exec \
  --model gpt-5.5 \
  --sandbox workspace-write \
  --ask-for-approval never \
  "Summarize the current repository"
```

script など unattended の非 interactive 実行では `--ask-for-approval never` を使います。
`on-request` は human が approval prompt に応答できる supervised local session に限定します。

prompt argument を省略した場合、または prompt に `-` を指定した場合、`codex exec` は
stdin から instruction を読みます。stdin が pipe され、かつ prompt argument もある場合、
Codex は prompt を instruction として扱い、stdin を追加 context として添付します。

### 3.2 非 Interactive セッションを再開する

```bash
codex exec resume --last "Continue from the previous findings"
codex exec resume "$SESSION_ID" "Run the next verification step"
```

parallel agent workflow では明示的な session ID を使ってください。`--last` は単一 local
thread では便利ですが、複数の Codex session が active または直近に完了している場合、意図しない
run を選ぶ可能性があります。

`codex exec resume --all` は session 一覧時の current-working-directory filter を無効化します。

### 3.3 Interactive セッションを再開または Fork する

```bash
codex resume
codex resume --last
codex resume "$SESSION_ID" "Pick up the refactor"

codex fork --last "Explore an alternative implementation"
codex fork "$SESSION_ID"
```

`codex resume` は保存済み transcript を再度開きます。`codex fork` は元の transcript を保持し、
そこから新しい thread を開始します。

### 3.4 セッションを Archive、Delete、Restore する

```bash
codex archive "$SESSION_ID"
codex unarchive "$SESSION_ID"
codex delete "$SESSION_ID"
```

active list から隠したいだけなら archive を使います。transcript を削除すべき場合だけ delete します。

## 4. 非 Interactive 出力

`--json` なしの `codex exec` は progress を stderr に stream し、agent の最終 message だけを
stdout に出力します。これにより shell pipeline は単純になります。

```bash
codex exec "Generate release notes for the last 10 commits" > release-notes.md
```

harness が structured progress を必要とする場合は `--json` を使います。

```bash
codex exec --json "Summarize the repository" | jq
```

documented JSONL event stream には以下が含まれます。

| Event | 意味 |
|-------|------|
| `thread.started` | session 開始。`thread_id` を含む。 |
| `turn.started` | model turn の開始。 |
| `item.started` / `item.completed` | command execution、MCP call、file change、web search、reasoning、plan update、agent message などの work item。 |
| `turn.completed` | turn 完了。usage field を含む。 |
| `turn.failed` | turn 失敗。 |
| `error` | error event。 |

JSONL output から session ID を抽出します。

```bash
SESSION_ID=$(
  codex exec --json "Start the investigation" |
    jq -r 'select(.type == "thread.started") | .thread_id'
)
```

通常 stream を受け取りつつ最終 message も書き出します。

```bash
codex exec \
  --json \
  --output-last-message result.md \
  "Inspect this change and summarize the result"
```

構造化された最終 response を要求します。

```bash
codex exec \
  --output-schema ./schema.json \
  --output-last-message ./result.json \
  "Extract the requested fields"
```

## 5. よく使うパターン

### 5.1 単一 Agent の継続

```bash
codex exec \
  --sandbox workspace-write \
  --ask-for-approval never \
  "Start the documentation audit"

codex exec resume --last "Apply the next documentation update"
```

### 5.2 Parallel Agents

```bash
SESSION_REVIEW=$(
  codex exec --json -C /path/to/project \
    --sandbox workspace-write \
    "Review the current diff" |
    jq -r 'select(.type == "thread.started") | .thread_id'
)

SESSION_DOCS=$(
  codex exec --json -C /path/to/project \
    --sandbox workspace-write \
    --search \
    "Check whether the docs are current" |
    jq -r 'select(.type == "thread.started") | .thread_id'
)

codex exec resume "$SESSION_REVIEW" "Report the top risks"
codex exec resume "$SESSION_DOCS" "Update only the stale documentation"
```

### 5.3 Prompt Plus Stdin

```bash
git diff --stat |
  codex exec "Summarize the scope of this change for a pull request"
```

### 5.4 Ephemeral Automation

```bash
codex exec \
  --ephemeral \
  --sandbox read-only \
  "Triage this repository and suggest the next three checks"
```

後で resume しない session には `--ephemeral` を使います。

## 6. Permissions と Sandbox

task に必要な最小権限を使います。

| Mode | 使う場面 |
|------|----------|
| `--sandbox read-only` | inspection、review、planning。 |
| `--sandbox workspace-write --ask-for-approval never` | failure を prompt ではなく agent に返すべき、scripted non-interactive の local coding または docs 作業。 |
| `--sandbox workspace-write --ask-for-approval on-request` | human が approval prompt に応答できる supervised interactive または local run。 |
| `--sandbox danger-full-access --ask-for-approval never` | 外部隔離された container、VM、CI runner 内だけ。 |

workflow が本当に別の writable root を必要とする場合は、`workspace-write` を `--add-dir` で拡張できます。

```bash
codex exec \
  --sandbox workspace-write \
  --add-dir ../shared-docs \
  "Update cross-repository references"
```

通常の workstation で bypass flag を便利 shortcut として使うのは避けてください。agentic shell
execution を reviewable にする guardrail が外れます。

interactive TUI 内では、session を再起動せずに `/permissions` で permission behavior を調整できます。

## 7. Web Search と Models

Codex には first-party web search tool があります。設定によっては cached search が default で
利用できます。最新情報が必要な task では `--search` を使います。

```bash
codex exec --search "Check the current upstream release notes"
```

model は時間とともに変わります。現行 OpenAI Codex manual は、多くの Codex 作業では
`gpt-5.5`、軽めの task で高速・低コストを優先する場合は `gpt-5.4-mini` を推奨しています。
model を選ぶには `codex --model <model>`、`codex exec --model <model>`、または TUI 内の
`/model` を使い、Codex 更新後は公式 model documentation を再確認してください。

```bash
codex --model gpt-5.5
codex exec --model gpt-5.5 "Review this branch"
```

## 8. MCP、Plugins、Feature Flags

MCP server は `codex mcp` で管理します。

```bash
codex mcp list
codex mcp add context7 -- npx -y @upstash/context7-mcp
codex mcp get context7
codex mcp remove context7
```

MCP configuration は `~/.codex/config.toml` などの Codex config file に置きます。
trusted project では project-scoped `.codex/config.toml` も使えます。

plugin は `codex plugin` で管理します。

```bash
codex plugin list
codex plugin add <plugin-name>
codex plugin remove <plugin-name>
codex plugin marketplace list
```

feature flag は `codex features` で確認します。

```bash
codex features list
codex features enable <feature-name>
codex features disable <feature-name>
```

feature、plugin、MCP の availability は、インストール済み Codex version、account、workspace policy、
local config によって異なる場合があります。

## 9. Codex Cloud

インストール済み `0.142.5` CLI では `codex cloud` は experimental です。

```bash
codex cloud
codex cloud list --limit 10
codex cloud list --json --env "$ENV_ID"
codex cloud exec --env "$ENV_ID" --attempts 3 "Investigate this bug"
codex cloud status "$TASK_ID"
codex cloud diff "$TASK_ID"
codex cloud apply "$TASK_ID"
```

`codex cloud exec` には `--env <ENV_ID>` が必要です。`0.142.5` では、cloud task の Git branch を
選ぶ `--branch <BRANCH>` も受け付けます。

`codex apply <TASK_ID>` または `codex cloud apply <TASK_ID>` は、task を確認し、現在の local tree に
diff を適用して安全だと判断した後だけ使ってください。

## 10. Troubleshooting

### 10.1 `--last` が違う Session を再開する

script や parallel workflow では明示的な session ID を使います。`--last` は、recent session ordering
が明らかな単一 local thread に限定します。

### 10.2 Git Repository Check が失敗する

Codex は通常の local work では Git repository 内での実行を要求します。automation target が意図的に
Git 外にある場合は以下を使います。

```bash
codex exec --skip-git-repo-check "Inspect this directory"
```

周辺環境が controlled な場合だけ使ってください。

### 10.3 Upgrade 後の Config Drift

以下を実行します。

```bash
codex doctor
codex --strict-config --help
```

その後、新しい CLI version が受け付けない config key を削除または更新します。

### 10.4 Automation Auth

API key を使う one-off automation では、key を Codex process だけに渡します。

```bash
CODEX_API_KEY="$CODEX_API_KEY" codex exec --json "Triage this change"
```

同じ job environment 内の無関係な setup command、test、dependency hook、repository-controlled script に
API key を晒さないでください。

### 10.5 古い Flag 例

古い guide では `--experimental-json` や `--full-auto` のような broad shortcut が使われていました。
現在の例では `--json` と明示的な sandbox / approval flag を使います。

## 11. 参考情報と検証状況

| 情報 | Source | Verification |
|------|--------|--------------|
| installed version | `codex --version` | local command が `codex-cli 0.142.5` を返した。 |
| top-level commands / global flags | `codex --help` | 2026-07-08 に local command で確認。 |
| non-interactive options | `codex exec --help` | 2026-07-08 に local command で確認。 |
| resume options | `codex exec resume --help`、`codex resume --help`、`codex fork --help` | 2026-07-08 に local command で確認。 |
| cloud options | `codex cloud --help`、`codex cloud exec --help`、`codex cloud list --help` | 2026-07-08 に local command で確認。 |
| MCP、plugin、feature commands | `codex mcp --help`、`codex plugin --help`、`codex features --help` | 2026-07-08 に local command で確認。 |
| JSONL event model、sandbox semantics、model guidance、slash commands | current OpenAI Codex manual | 2026-07-08 に取得。 |

## 変更履歴

| 日付 | 変更 |
|------|------|
| 2025-11-27 | Codex CLI v0.63.0 向け日本語 guide の初版。 |
| 2025-12-02 | 旧 `--json` resume 制約に関する note を追加。 |
| 2026-03-09 | v0.112.0 時点の commands / features に更新。 |
| 2026-05-23 | v0.124.0 に更新し、`--experimental-json` を `--json` に置換。 |
| 2026-07-08 | Codex CLI `0.142.5` 向けに英語正本 guide として全面改訂。stale な model、command、session note を整理・更新。 |
