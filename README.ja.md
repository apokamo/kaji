# kaji

言語: [English](README.md) | 日本語

[![Release](https://img.shields.io/github/v/release/apokamo/kaji?include_prereleases)](https://github.com/apokamo/kaji/releases)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](pyproject.toml)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue)](LICENSE)

Claude Code、Codex、Gemini CLIのための closed-loop agentic development。

kajiは、Issueを起点に、設計 -> 実装 -> レビュー -> 修正 -> 検証 -> PR までを
再開可能なワークフローとして実行するAIエージェントオーケストレータです。
human-in-the-loop の判断ポイントと、`verdict.yaml` などのartifact-backed verdictにより、
AIエージェントの作業をブラックボックスにせず運用できます。

> `kaji` は日本語の「舵」です。人間が方向を決め、エージェントが作業を進めます。

## なぜkajiか

AI coding agentは強力ですが、一発プロンプトだけでは開発プロセスを統制しにくいです。
設計するタイミング、レビューするタイミング、修正するタイミング、止めるタイミング、
そして人間が判断すべきタイミングを、agentの外側で管理する必要があります。

kajiはその層を提供します。

- 開発プロセスをworkflow YAMLとして定義する
- 各stepをClaude Code、Codex、Gemini CLIへ割り当てる
- 無限チャットではなく、上限付きの review -> fix -> verify ループにする
- 判断結果を構造化されたverdict artifactとして残す
- 中断した作業を特定stepから再開する
- 重要な判断ポイントは人間に残す

Beyond vibe coding: kajiはAI支援開発に、loop、log、quality gateを与えます。

## 仕組み

```mermaid
flowchart TB
  Issue["Issue"] --> Ready["Ready gate"]
  Ready --> Design["Design"]
  Design --> DesignReview{"Design review"}
  DesignReview -- "PASS" --> Implement["Implement"]
  DesignReview -- "RETRY" --> FixDesign["Fix design"]
  FixDesign --> VerifyDesign["Verify design"]
  VerifyDesign --> DesignReview

  Implement --> CodeReview{"Code review"}
  CodeReview -- "PASS" --> FinalCheck["Final check"]
  CodeReview -- "RETRY" --> FixCode["Fix code"]
  FixCode --> VerifyCode["Verify code"]
  VerifyCode --> CodeReview
  CodeReview -- "BACK" --> Design

  FinalCheck --> PR["Pull request"]
```

各agent stepはverdictを返します。

| Verdict | 意味 |
|---------|------|
| `PASS` | 次のstepへ進む |
| `RETRY` | 現在の問題を修正し、再検証する |
| `BACK` | 設計や実装など、前のフェーズへ戻る |
| `ABORT` | 理由を明示してworkflowを停止する |

harnessは `verdict.yaml` などの構造化出力を読み、attempt artifactを記録しながら、
各verdictから次のworkflow stepを決定論的に進めます。

## 主な機能

- **Multi-agent workflow orchestration**: 1つのworkflow定義からClaude Code、Codex、Gemini CLIを呼び分ける
- **Closed review loops**: review -> fix -> verify の閉じたサイクルでレビュー指摘を収束させる
- **Interactive tmux runner**: 通常のCLI agentをtmux paneで起動し、kajiがartifact-backed verdictを監視する
- **Headless runner**: CI的な非対話実行に向いた既存のheadless経路も維持する
- **Deterministic exec steps**: LLMが不要なstepはsubprocessとして直接実行する
- **Artifact-primary verdicts**: `verdict.yaml` を優先し、必要に応じてIssue commentやstdoutへfallbackする
- **Issue and PR lifecycle**: GitHub Issue、branch、PR、review、closeの流れを扱う
- **TDD and docs-as-code**: 実装、レビュー、テスト、ドキュメント更新を同じプロセスに載せる

## 拡張性

kajiは現在、Claude Code、Codex、Gemini CLIを中心に対応しています。runnerとworkflow modelは、
実際の需要があるcoding-agent CLIを追加できるように設計しています。

このループに組み込みたい別のcoding agentがあれば、ぜひIssueで教えてください。
どのようなworkflowで使いたいかも含めてリクエストしてもらえると助かります。

## Quick start

### 前提

- Python 3.11以上
- `uv`
- 使用したいagentのClaude Code、Codex、Gemini CLI
- GitHub Issue / PR連携を使う場合は認証済みの `gh`
- interactive terminal runnerを使う場合は `tmux` 3.1以上
- 対象リポジトリに `.claude/skills/` 配下のkaji skillがあること

### kajiをインストールする

配布がリポジトリベースの間は、Gitからインストールします。

```bash
uv tool install git+https://github.com/apokamo/kaji.git
kaji --help
```

将来PyPI公開する場合は、次の形に差し替えられます。

```bash
uv tool install kaji
```

### 対象リポジトリを設定する

kajiを実行したいリポジトリに `.kaji/config.toml` を追加します。
下の例で使う `.kaji/wf/dev.yaml` は、PR作成、PR review polling、Issue closeまで扱う
GitHub前提のworkflowです。

```toml
[paths]
artifacts_dir = ".kaji-artifacts"
skill_dir = ".claude/skills"
worktree_prefix = "kaji"

[execution]
default_timeout = 1800
agent_runner = "headless"
interactive_terminal_close_on_verdict = true

[provider]
type = "github"

[provider.github]
repo = "<owner>/<name>"
default_branch = "main"
git_remote = "origin"
```

`.kaji/config.toml` の全設定項目、overlay、利用可能なkeyの詳細は
[設定リファレンス](docs/reference/configuration.md)（英語正本、[日本語版](docs/reference/configuration.ja.md)）を参照してください。

GitHubを使わないlocal issue storageの場合は、local provider configにし、
gitignoredなmachine overlayを作成します。

```toml
[provider]
type = "local"
```

```bash
kaji local init
```

`kaji local init` は現在のmachine用の `.kaji/config.local.toml` を作成します。
trackedなbase configを置き換えるものではありません。local modeでは
`.kaji/wf/dev-local.yaml` などのlocal専用workflowを使います。local providerの
セットアップは [Local Mode CLI Guide](docs/cli-guides/local-mode.md) を参照してください。

skillは `.claude/skills/` に配置します。他agent向けのskill directoryは、
同じcanonical skill fileへのsymlinkとして構成できます。

### workflowを実行する

workflow fileは各リポジトリの `.kaji/wf/` から実行します。このリポジトリでは現在、
GitHub前提のworkflow setとして `.kaji/wf/dev.yaml`、`.kaji/wf/dev-thorough.yaml`、
`.kaji/wf/docs.yaml` を置いています。これらのworkflowを設定済みの新規Python
プロジェクトを始めるには、
[kaji-starter-python](https://github.com/apokamo/kaji-starter-python)
template repositoryからリポジトリを作成し、
[Python Starterガイド](docs/guides/python-starter.ja.md)に従ってください。

`dev.yaml` の例は、GitHub Issueが存在し、必要なskillがあり、選択するagent CLIが使え、
`/issue-create` が完了していることを前提にします。`issue-start` はworkflow内で実行します。

workflowを実行:

```bash
kaji run .kaji/wf/dev.yaml <issue-id>
```

特定stepから再開:

```bash
kaji run .kaji/wf/dev.yaml <issue-id> --from fix-code
```

単一stepだけ実行:

```bash
kaji run .kaji/wf/dev.yaml <issue-id> --step review-code
```

### kaji自体を開発する

別リポジトリでkajiを使うのではなく、kaji自体を開発する場合だけ、この手順を使います。

```bash
git clone https://github.com/apokamo/kaji.git
cd kaji
uv sync
source .venv/bin/activate
kaji --help
```

## tmux interactive terminal runner

headless runnerではなく、通常のClaude CodeやCodex CLI sessionをtmux pane内で起動したい場合に使います。

```toml
[execution]
default_timeout = 2400
agent_runner = "interactive_terminal"
interactive_terminal_close_on_verdict = true
```

tmux session内で実行します。

```bash
tmux new-session
kaji run .kaji/wf/dev.yaml <issue-id> --agent-runner interactive-terminal
```

runnerは管理対象paneを開き、terminal transcriptを記録し、`verdict.yaml` を待ってworkflowを進めます。
ライブ観察、subscription CLI利用、agent挙動のデバッグに向いています。

詳細:
[Interactive Terminal Runner](docs/cli-guides/interactive-terminal-runner.md)

## workflow例

```yaml
name: minimal-code-review
description: "Bounded implement -> review -> fix -> verify loop"
execution_policy: auto

cycles:
  code-review:
    entry: review-code
    loop: [fix-code, verify-code]
    max_iterations: 3
    on_exhaust: ABORT

steps:
  - id: implement
    skill: issue-implement
    agent: claude
    on:
      PASS: review-code
      ABORT: end

  - id: review-code
    skill: issue-review-code
    agent: codex
    on:
      PASS: end
      RETRY: fix-code
      BACK_IMPLEMENT: implement
      ABORT: end

  - id: fix-code
    skill: issue-fix-code
    agent: claude
    on:
      PASS: verify-code
      ABORT: end

  - id: verify-code
    skill: issue-verify-code
    agent: codex
    resume: review-code
    on:
      PASS: end
      RETRY: fix-code
      ABORT: end
```

review loopの上限は `cycles.code-review.max_iterations` で指定します。上のskill名はkaji標準skill setの
実名に合わせています。対象リポジトリ側に対応するskill fileが必要です。
`model` と `effort` はYAML schema上は任意なので、この短い例では省略しています。
実運用workflowではpinすることが多いです。

`resume` は、runnerが対応している場合に、同じagentの前回sessionから続行するための指定です。

## ドキュメント

| Topic | Link |
|-------|------|
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Workflow overview | [docs/dev/workflow_overview.md](docs/dev/workflow_overview.md) |
| Workflow authoring | [docs/dev/workflow-authoring.md](docs/dev/workflow-authoring.md) |
| Skill authoring | [docs/dev/skill-authoring.md](docs/dev/skill-authoring.md) |
| Interactive terminal runner | [docs/cli-guides/interactive-terminal-runner.md](docs/cli-guides/interactive-terminal-runner.md) |
| AI-driven development strategy | [docs/concepts/ai-driven-strategy.md](docs/concepts/ai-driven-strategy.md) |
| CLI guides | [docs/cli-guides/](docs/cli-guides/) |

## AIが読みやすいドキュメント

AI assistantやクローラ向けに、[llms.txt](llms.txt) に重要docs、主要コマンド、
workflow概念への短い索引を置いています。

## ステータス

現在の公開バージョンは v0.12.0 です。kajiはactive development中であり、
ユーザー向けのサポート対象エントリポイントは `kaji` CLIです。

`legacy/` directoryは過去実装の参照用であり、現在のサポート対象runtimeには含めません。

## 開発

```bash
source .venv/bin/activate
make check
```

個別target:

```bash
make lint
make format
make typecheck
make test
make verify-docs
make verify-packaging
```

## License

Apache-2.0
