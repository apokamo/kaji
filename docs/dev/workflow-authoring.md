# ワークフロー定義マニュアル

kaji_harness が読み込む YAML ワークフロー定義の書き方。

## 前提条件

ワークフローを実行するプロジェクトには `.kaji/config.toml` が必要です。これがプロジェクトルートのマーカーになります。

```toml
# .kaji/config.toml（最小構成）
[paths]
artifacts_dir = "~/.kaji/artifacts"   # 省略時のデフォルト値

[execution]
default_timeout = 1800               # 必須: タイムアウトのデフォルト値（秒）
```

## ファイル配置

```
workflows/
  feature-development.yaml
  bugfix.yaml
```

## 全体構造

```yaml
name: feature-development          # 必須: ワークフロー名
description: "設計→実装→PR フロー" # 必須: 説明
execution_policy: auto             # 必須: auto / sandbox / interactive
default_timeout: 600               # 省略可: ワークフロー全体のデフォルトタイムアウト（秒）
workdir: /home/user/project        # 省略可: 全ステップのデフォルト作業ディレクトリ

cycles:                            # 省略可: ループサイクル定義
  <cycle-name>:
    entry: <step-id>
    loop: [<step-id>, ...]
    max_iterations: 3
    on_exhaust: ABORT

steps:                             # 必須: ステップ一覧（上から順に実行）
  - id: <step-id>
    skill: <skill-name>
    agent: claude                  # claude / codex / gemini
    on:
      PASS: <next-step-id>
      RETRY: <step-id>
```

## ステップフィールド

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `id` | str | ✅ | ステップ ID。英数字とハイフン |
| `skill` | str | ✅ | スキル名（`<agent>.skills/<name>` のルックアップキー） |
| `agent` | str | ✅ | `claude` / `codex` / `gemini` |
| `on` | mapping | ✅ | verdict → next step ID のマッピング。非空必須 |
| `model` | str | — | モデル名（省略時は agent デフォルト） |
| `effort` | str | — | `low` / `medium` / `high`（agent がサポートする場合） |
| `max_budget_usd` | float | — | コスト上限（USD） |
| `max_turns` | int | — | ターン数上限 |
| `timeout` | int | — | タイムアウト（秒）。フォールバック: step.timeout → workflow.default_timeout → config.execution.default_timeout |
| `workdir` | str | — | 作業ディレクトリ（絶対パス）。フォールバック: step.workdir → workflow.workdir → project_root |
| `resume` | str | — | resume するステップ ID（同一 agent のセッション継続） |

### `on` マッピング

値は **ステップ ID** か **`end`** を指定する。

| verdict | 意味 |
|---------|------|
| `PASS` | 成功。次ステップへ進む |
| `RETRY` | 再試行。同ステップを再実行 |
| `BACK` | 差し戻し。前段ステップを再実行 |
| `ABORT` | 中断。ワークフロー全体を停止 |

`end` を値に指定するとワークフロー終了。

## サイクル定義

review → fix → verify のようなループを宣言する。

```yaml
cycles:
  code-review:
    entry: review-code      # サイクルへの入口ステップ
    loop:                   # RETRY 時のループステップ群
      - fix-code
      - verify-code
    max_iterations: 3       # fix→verify を 1 イテレーションとしてカウント
    on_exhaust: ABORT       # max_iterations 到達時に発行する verdict
```

**制約**: `loop` 末尾ステップの `on.RETRY` は `loop` 先頭ステップを指すこと。

## execution_policy

| 値 | 動作 |
|----|------|
| `auto` | 全 agent で承認・sandbox をバイパス（完全自動） |
| `sandbox` | sandbox 内で自動実行（ファイル書き込みを制限） |
| `interactive` | 承認フロー有効（人手確認あり） |

## resume（セッション継続）

同一 agent 内でコンテキストを引き継ぐ場合に指定する。

```yaml
steps:
  - id: design
    skill: design
    agent: claude
    on:
      PASS: implement

  - id: implement
    skill: implement
    agent: claude
    resume: design          # design ステップのセッションを継続
    on:
      PASS: end
```

`resume` 先ステップと `agent` が異なる場合はバリデーションエラー。

## 完全サンプル

```yaml
name: feature-development
description: "設計→実装→コードレビューの標準フロー"
execution_policy: auto

cycles:
  code-review:
    entry: review-code
    loop:
      - fix-code
      - verify-code
    max_iterations: 3
    on_exhaust: ABORT

steps:
  - id: design
    skill: issue-design
    agent: claude
    model: claude-sonnet-4-6
    effort: medium
    on:
      PASS: implement
      ABORT: end

  - id: implement
    skill: issue-implement
    agent: claude
    resume: design
    on:
      PASS: review-code
      ABORT: end

  - id: review-code
    skill: issue-review-code
    agent: codex
    on:
      PASS: doc-check
      RETRY: fix-code
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
      PASS: doc-check
      RETRY: fix-code
      ABORT: end

  - id: doc-check
    skill: issue-doc-check
    agent: claude
    on:
      PASS: final-check

  - id: final-check
    skill: i-dev-final-check
    agent: claude
    on:
      PASS: pr
      RETRY: final-check
      ABORT: end

  - id: pr
    skill: i-pr
    agent: claude
    model: sonnet
    on:
      PASS: end
      RETRY: pr
      ABORT: end
```

## 実行コマンド

```bash
# 通常実行（最初のステップから）
kaji run workflows/feature-development.yaml 57

# 途中から再開（--from で開始ステップ指定）
kaji run workflows/feature-development.yaml 57 --from fix-code

# 単発実行（1ステップのみ実行して終了）
kaji run workflows/feature-development.yaml 57 --step review-code

# config 探索の起点ディレクトリを指定（YAML workdir とは別物）
kaji run workflows/feature-development.yaml 57 --workdir ../kaji-feat-57

# エージェント出力のストリーミング表示を抑制
kaji run workflows/feature-development.yaml 57 --quiet
```

**注意**: `--from` と `--step` は排他オプションです（同時指定不可）。

### バリデーション

ワークフロー YAML を実行前に検証できます:

```bash
# 単一ファイルのバリデーション
kaji validate workflows/feature-development.yaml

# 複数ファイルの一括バリデーション
kaji validate workflows/*.yaml
```

**出力例**:

```
✓ workflows/feature-development.yaml
✗ workflows/bad.yaml
  - Step 'review' transitions to unknown step 'fix' on RETRY

Validation failed: 1 of 2 files had errors.
```

- 成功: `✓ <filename>` が stdout に出力、exit 0
- 失敗: `✗ <filename>` + エラー詳細が stderr に出力、exit 1
- 引数なし: argparse エラー、exit 2

### 終了コード

| 終了コード | 意味 |
|-----------|------|
| 0 | 正常終了 |
| 1 | ワークフロー ABORT または予期しないエラー |
| 2 | 定義エラー（YAML不正、スキル未検出、引数エラー、`.kaji/config.toml` 未検出等） |
| 3 | 実行時エラー（CLI実行失敗、タイムアウト、verdict解析失敗等） |

## 関連ドキュメント

- [スキル作成マニュアル](skill-authoring.md)
- [Architecture](../ARCHITECTURE.md)
