# kaji「舵」

AI-driven software development workflow orchestrator. Claude Code / Codex / Gemini CLI のスキルをワークフロー YAML に従って実行する。

> **V7 (kaji_harness) が現在の正規エントリポイントです。** `legacy/` は V5/V6 の参照用アーカイブであり、サポート対象外です。

## アーキテクチャ概要

3層アーキテクチャでAIエージェントを制御:

```
┌─────────────────────────────────────────────┐
│  ハーネス (kaji_harness/)                     │
│  ワークフロー YAML を解釈し CLI を順次呼出  │
├─────────────────────────────────────────────┤
│  スキル (.claude/skills/, .agents/skills/)   │
│  実体は .claude、.agents は symlink         │
├─────────────────────────────────────────────┤
│  CLI (Claude Code / Codex / Gemini)          │
│  スキルをロードし PJ コンテキストで実行     │
└─────────────────────────────────────────────┘
```

詳細: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## セットアップ（開発者向け）

```bash
uv sync
source .venv/bin/activate
```

## 開発ワークフロー

Issue駆動のTDD開発フロー:

```
/issue-create → /issue-start → /issue-design → /issue-implement → /issue-pr → /issue-close
```

ワークフローガイド: [docs/dev/workflow_guide.md](docs/dev/workflow_guide.md)

## 最小導入

対象プロジェクトには `.kaji/config.toml` を配置する（コロケーテッドモデル）:

```toml
# .kaji/config.toml
[paths]
artifacts_dir = ".kaji/artifacts"    # 必須: アーティファクト保存先
skill_dir = ".claude/skills"         # 必須: スキルディレクトリ

[execution]
default_timeout = 1800  # 必須: タイムアウトのデフォルト値（秒）
```

skill の実体は `.claude/skills/` に置き、`.agents/skills/` はそれを参照する symlink として扱う。

```text
.claude/skills/
  implement/
    SKILL.md
  review-code/
    SKILL.md
  fix-code/
    SKILL.md
  verify-code/
    SKILL.md

.agents/skills/
  review-code -> ../../.claude/skills/review-code
  verify-code -> ../../.claude/skills/verify-code
```

最小の workflow は次のようになる。

```yaml
name: minimal-code-review
description: "最小のコードレビュー付きフロー"
execution_policy: auto

steps:
  - id: implement
    skill: implement
    agent: claude
    on:
      PASS: review-code
      ABORT: end

  - id: review-code
    skill: review-code
    agent: codex
    on:
      PASS: end
      RETRY: fix-code
      ABORT: end

  - id: fix-code
    skill: fix-code
    agent: claude
    on:
      PASS: verify-code
      ABORT: end

  - id: verify-code
    skill: verify-code
    agent: codex
    resume: review-code
    on:
      PASS: end
      RETRY: fix-code
      ABORT: end
```

`resume` は、同じ agent の前段ステップのコンテキストを引き継いで続きから実行するための指定。

## ワークフロー実行

```bash
# 最小 workflow を実行
kaji run workflows/minimal-code-review.yaml 57

# 途中から再開
kaji run workflows/minimal-code-review.yaml 57 --from fix-code

# 単一ステップ実行
kaji run workflows/minimal-code-review.yaml 57 --step review-code
```

詳細:
- [docs/dev/workflow-authoring.md](docs/dev/workflow-authoring.md)
- [docs/dev/skill-authoring.md](docs/dev/skill-authoring.md)

## 品質チェック

コミット前に必ず実行:

```bash
source .venv/bin/activate
make check                            # lint → format → typecheck → test
```

変更タイプに応じた追加検証:

```bash
make verify-docs                      # docs-only: リンク・参照整合チェック
make verify-packaging                 # packaging/metadata: 隔離環境で uv install + metadata 確認
```

個別ターゲット: `make lint` / `make format` / `make typecheck` / `make test`

## ドキュメント

| ドキュメント | 内容 |
|-------------|------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | V7 アーキテクチャ詳細 |
| [docs/adr/](docs/adr/) | アーキテクチャ決定記録 |
| [docs/dev/workflow_guide.md](docs/dev/workflow_guide.md) | ワークフローガイド |
| [docs/dev/testing-convention.md](docs/dev/testing-convention.md) | テスト規約 (S/M/L) |
| [docs/dev/workflow-authoring.md](docs/dev/workflow-authoring.md) | ワークフロー YAML 定義 |
| [docs/dev/skill-authoring.md](docs/dev/skill-authoring.md) | スキル作成ガイド |
| [docs/cli-guides/](docs/cli-guides/) | CLI ツールガイド (Claude/Codex/Gemini) |

## `legacy/` ディレクトリ

V5/V6 の旧コード・テスト・ドキュメントを参照用に保持。

```
legacy/
├── bugfix_agent/                  # V5/V6 パッケージ
├── bugfix_agent_orchestrator.py   # V5 エントリポイント
├── prompts/                       # V6 プロンプト
├── tests/                         # V5 テスト
├── docs/                          # V5 ドキュメント
├── config.toml                    # V5 設定
└── AGENT.md                       # V5 エージェント指示書
```

## License

Apache-2.0
