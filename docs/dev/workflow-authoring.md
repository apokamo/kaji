# ワークフロー定義マニュアル

kaji_harness が読み込む YAML ワークフロー定義の書き方。

## 前提条件

ワークフローを実行するプロジェクトには `.kaji/config.toml` が必要です。これがプロジェクトルートのマーカーになります。

```toml
# .kaji/config.toml（最小構成）
[paths]
artifacts_dir = ".kaji/artifacts"     # 必須: アーティファクト保存先
skill_dir = ".claude/skills"          # 必須: スキルディレクトリ

[execution]
default_timeout = 1800               # 必須: タイムアウトのデフォルト値（秒）
```

> **`artifacts_dir` の解決基準** (Issue #177): 相対パス指定の場合、`kaji run` は main worktree（`provider.<type>.default_branch` を checkout している worktree）基準で解決する。feature worktree 内で `kaji run` を実行しても artifacts/log は main worktree 配下に集約され、`git worktree remove` でログが消えない。絶対パス指定はそのまま。

> **runner backend は repository config の責務** (Issue #224): agent step を headless CLI で起動するか `kitty` 上の対話 CLI で起動するかは `[execution] agent_runner`（または `kaji run --agent-runner`）で選ぶ。これは **repository config / 実行時 option の責務**であり、workflow YAML の step 定義には書かない。step は backend 非依存に保つ。詳細は [Interactive Terminal Runner ガイド](../cli-guides/interactive-terminal-runner.md)。

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
requires_provider: github          # 省略可: github / local / any（default: any）

cycles:                            # 省略可: ループサイクル定義
  <cycle-name>:
    entry: <step-id>
    loop: [<step-id>, ...]
    max_iterations: 3
    on_exhaust: ABORT

steps:                             # 必須: ステップ一覧（上から順に実行）
  - id: <step-id>
    skill: <skill-name>
    agent: claude                  # claude / codex / gemini（exec_script skill のみ省略可）
    on:
      PASS: <next-step-id>
      RETRY: <step-id>
```

### `agent` の省略条件 (exec_script skill)

skill の SKILL.md frontmatter に `exec_script` が宣言されている場合、その skill を呼ぶ step
では `agent` / `model` / `effort` を省略できる。harness は agent を起動せず、`exec_script` に
指定された Python module を ``python -m <module>`` として直接 subprocess 実行する
(`docs/dev/skill-authoring.md` § exec_script 参照)。

```yaml
# OK: review-poll skill (frontmatter に exec_script を持つ) は agent 省略可
- id: review-poll
  skill: review-poll
  on:
    PASS: close
    RETRY: pr-fix
    BACK_FALLBACK: review
    ABORT: end
```

検証層 (`docs/dev/skill-authoring.md` の exec_script 仕様と整合):

| 層 | 場所 | 内容 |
|----|------|------|
| L1: YAML schema | `kaji_harness/workflow.py` | `agent` は任意。型のみ `str | None` で検証 |
| L2: runner preflight | `kaji_harness/runner.py:run()` 起動時 | 全 step に対し skill metadata を解決し、`agent is None` かつ `exec_script` も無いケースを `WorkflowValidationError` で fail-fast。`exec_script` 経路では `agent` / `model` / `effort` が指定されても WARN ログを出して無視する |
| L3: `kaji validate` CLI | `kaji_harness/cli_main.py:validate` | `paths.skill_dir` が解決できる場合のみ L2 と同等の skill 整合 check を実施。解決不能なら skip |


### `requires_provider`

Phase 4 で導入。workflow が要求する provider を宣言する。`kaji run` 起動時に
`config.provider.type` と突合し、不整合を **exit 2** で fail-fast する。

| 値 | 意味 |
|----|------|
| `github` | `i-pr` / `pr-fix` / `pr-verify` / `kaji pr ...` 等の forge 機能を GitHub 経路で必要とする |
| `local` | bare provider 限定の workflow（最終 step が `issue-close` 等） |
| `any` | provider 中立。設計のみ / Issue 操作のみで forge を呼ばない |

未指定時は `any`（既存 workflow の破壊を避けるため）。

**custom workflow への migration 推奨**:

- forge 機能（`i-pr` / `pr-*` Skill / `kaji pr ...`）を含む custom workflow には
  `requires_provider: github` を追加することで早期 fail-fast の保護を opt-in できる
- bare 機能のみで構成される custom workflow には `requires_provider: local`
  を追加できる
- 上記対応をしない custom workflow は本ガードの保護対象外であり、従来通り
  workflow 終盤で停止する挙動になる（ガードは fail-fast による早期検知のみで、
  workflow の意味的な動作は変わらない）

`kaji validate` は `requires_provider` の **値の妥当性**（enum）を検証するが、
`config.provider.type` との突合は `kaji run` でのみ行う（`kaji validate` は
config 非依存のため）。

## ステップフィールド

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `id` | str | ✅ | ステップ ID。英数字とハイフン |
| `skill` | str | ✅ | スキル名（`<agent>.skills/<name>` のルックアップキー） |
| `agent` | str | ✅ | `claude` / `codex` / `gemini` |
| `on` | mapping | ✅ | verdict → next step ID のマッピング。非空必須 |
| `model` | str | — | モデル名（省略時は agent デフォルト） |
| `effort` | str | — | エージェント別の許容値で書く。後述「effort 値」参照 |
| `max_budget_usd` | float | — | コスト上限（USD） |
| `timeout` | int | — | タイムアウト（秒）。フォールバック: step.timeout → workflow.default_timeout → config.execution.default_timeout |
| `workdir` | str | — | 作業ディレクトリ（絶対パス）。フォールバック: step.workdir → workflow.workdir → project_root |
| `resume` | str | — | resume するステップ ID（同一 agent のセッション継続） |

### effort 値

エージェント別に **小文字** の許容値が異なる。`workflow.py` の YAML parse 時に
runtime validator が agent 別 allowed values で reject するため、本仕様から
外れる値（特に大文字 `High` / `xHigh`）を書くと `WorkflowValidationError` が
発生する。

| agent | 許容値（小文字） | 一次情報 |
|-------|-----------------|----------|
| `claude` | `low`, `medium`, `high`, `xhigh`, `max` | `claude --help` の `--effort` 列挙 |
| `codex` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh` | codex error message: `expected one of \`none\`, \`minimal\`, \`low\`, \`medium\`, \`high\`, \`xhigh\` in \`model_reasoning_effort\`` |
| その他 (`gemini` 等) | 検証スキップ（passthrough） | allowed values 辞書未登録のため |

> **罠（UI 表示と YAML 値の差異）**: claude / codex どちらの対話 UI も effort
> を **大文字**（`Low` / `Medium` / `High` / `Extra high`）で表示する。
> しかし CLI に渡す内部値は **小文字**。UI 表示をそのまま YAML にコピーすると
> codex 側で `unknown variant 'High'` で workflow が ERROR 停止する（claude 側は
> 暗黙の lowercase 化で通る場合があるが、許容値の方針として統一する）。

採択方針 (Issue local-pc5090-16): **agent 別 allowed values 辞書** を `workflow.py`
の module-level 定数として保持し、`step.agent` でルックアップして reject する。
共通 subset (`low/medium/high/xhigh`) のみで縛らない（claude `max` / codex
`none/minimal` を将来も使えるようにするため）。新 agent 追加時は
`_AGENT_EFFORT_ALLOWED` 辞書に 1 行加える。

### `on` マッピング

値は **ステップ ID** か **`end`** を指定する。

| verdict | 意味 |
|---------|------|
| `PASS` | 成功。次ステップへ進む |
| `RETRY` | 再試行。同ステップを再実行 |
| `BACK` | 差し戻し。前段ステップを再実行 |
| `ABORT` | 中断。ワークフロー全体を停止 |

`end` を値に指定するとワークフロー終了。

#### `BACK_*` プレフィックス拡張

差し戻し先を root-cause 別に解決したい場合、`BACK_*`（suffix 1 文字以上）の形式で追加 verdict を
定義できる。例: `BACK_DESIGN: design` / `BACK_IMPLEMENT: implement`。

- 標準 status は引き続き `PASS / RETRY / BACK / ABORT` の 4 種。`BACK_*` は **拡張点**
- `BACK_*` の suffix は **uppercase 英数字 + アンダースコア (`[A-Z0-9_]+`)** に限定する。
  relaxed verdict parser (`kaji_harness/verdict.py:_parse_relaxed_fields`) が status を
  `.upper()` で正規化するため、lowercase / mixed-case を許すと validator は通るが parser fallback
  経路で別 status に変換され `InvalidVerdictValue` を起こす
- `BACK_*` は `validate_workflow` が形式的に受理する。suffix の意味は workflow 設計者が定義する
- `BACK_` 単独（suffix 空）や `BACK_design` のような lowercase / mixed-case suffix は不正で `validate_workflow` が弾く
- `BACK_*` も `BACK` / `ABORT` と同様に verdict の `suggestion` フィールド必須
- 共通 skill を複数 workflow で使う場合、各 workflow の `step.on` キーは prompt 経由で skill に注入される
  （`prompt.py:75, 92-97` の `valid_statuses = list(step.on.keys())`）。skill 側はこの
  prompt 内 valid status を権威として扱い、YAML が許可していない status は返さない
- `cycle.on_exhaust` も同じ判定関数を共有する（`BACK_DESIGN` 等を指定可能）

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

**必須フィールド**（未設定時はバリデーションエラー）。

| 値 | 動作 |
|----|------|
| `auto` | 全 agent で承認・sandbox をバイパス（完全自動） |
| `sandbox` | sandbox 内で自動実行（ファイル書き込みを制限） |
| `interactive` | 承認フロー有効（人手確認あり） |

### エージェント別 CLI フラグ

| policy | Claude | Codex | Gemini |
|--------|--------|-------|--------|
| `auto` | `--permission-mode bypassPermissions` | `--dangerously-bypass-approvals-and-sandbox` | `--approval-mode yolo` |
| `sandbox` | （フラグなし） | `-s workspace-write` | `-s` |
| `interactive` | （フラグなし） | （フラグなし） | （フラグなし） |

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
      PASS: final-check
      RETRY: fix-code
      BACK: design
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
      PASS: final-check
      RETRY: fix-code
      ABORT: end

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

# 指定ステップを dispatch する直前で停止（exclusive barrier。指定ステップは実行されない）
kaji run workflows/feature-development.yaml 57 --before implement

# `--from` と組み合わせ: 修正ループだけ回す（A から B の手前まで）
kaji run workflows/feature-development.yaml 57 --from fix-design --before implement

# config 探索の起点ディレクトリを指定（YAML workdir とは別物）
kaji run workflows/feature-development.yaml 57 --workdir ../kaji-feat-57

# エージェント出力のストリーミング表示を抑制
kaji run workflows/feature-development.yaml 57 --quiet
```

**注意**: `--from` と `--step` は排他オプションです（同時指定不可）。`--step` と `--before` も排他です。

### `--from` / `--step` / `--before` の使い分け

| フラグ | 意味 | 指定 step は実行される？ | ループ動作 |
|--------|------|--------------------------|-----------|
| `--from <step>` | 開始点を指定（指定 step から実行） | ✅ 実行される | 通常通り |
| `--step <step>` | 単発実行（1 step だけ実行して終了） | ✅ 実行される | しない |
| `--before <step>` | 停止点を指定（dispatch 直前で停止） | ❌ 実行されない | barrier に到達するまで通常通り |

**`--before` の意味論**:

- 「次に dispatch しようとしている step ID」が `--before` 値と一致した瞬間に停止する exclusive barrier。
- ループ（`verify ⇄ fix` 等）は PASS が出るまで何周でも回り、PASS 後の遷移先が barrier 値と一致したら停止する。
- 分岐により barrier に到達せず workflow が自然完了した場合は WARN ログ（`stop point '<X>' was never reached; workflow completed naturally`）を stderr に出力し、終了コード 0 で終わる。
- `--before end` は許容（デフォルト動作と等価）。
- workflow に存在しない step ID を指定すると起動時に定義エラー（exit 2）。

**`resume:` と組み合わせる際の注意**: `resume:` を持つ step を barrier の直後に配置している workflow で、`--before` で止めた後に `--from` で再開すると、前段 agent の context が `SessionState` 経由で引き継がれるため、barrier を挟むタイミング次第で context 整合が崩れるケースがあります。barrier は「人間レビュー用の停止点」として、context 継続を要さない位置に置いてください。

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
