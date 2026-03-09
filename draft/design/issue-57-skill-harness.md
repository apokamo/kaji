# [設計] スキル実行ハーネスへのアーキテクチャ転換

Issue: #57

## 概要

現行の Python 状態マシンオーケストレータ（`bugfix_agent/`）を廃止し、Claude Code / Codex / Gemini CLI のスキルをワークフロー定義に従って実行する軽量ハーネスに転換する。

## 背景・目的

### 現行アーキテクチャの課題

1. **ネイティブ機能の再実装**: State machine、Verdict parsing、Tool abstraction、CLI streaming — これらは Claude Code / Codex のスキル機構がネイティブに提供する機能を Python（約2000 LOC）で再実装している
2. **PJ コンテキストの断絶**: 外部オーケストレータから各 PJ のドキュメント・コーディング規約・テスト規約を参照できない。PJ 内のスキルとして CLI が実行されれば、スキルが必要なタイミングで PJ のドキュメント（コーディング規約、テスト規約、設計テンプレート等）を Read ツールで段階的に読み込める。これは外部オーケストレータからは不可能であり、スキルが PJ のドキュメントツリーにアクセスできる位置で実行されることが前提となる
3. **保守コスト**: CLI のバージョンアップ（Claude v2.0→v2.1、Codex v0.63→v0.112、Gemini v0.18→v0.31）への追従が Tool abstraction 層で困難

### 新アーキテクチャの方針

> **設計根拠**: 本設計は Anthropic「[Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)」のベストプラクティスに基づく。
> ハーネスは遷移制御のみ、スキルが実作業、GitHub Issue が長期記憶 — この分離は同記事の「Initializer + Coding Agent」パターンおよび「構造化アーティファクトによるセッション間状態管理」と同じ設計思想。

- **スキルは CLI がネイティブにロード**: `.claude/skills/` や `.agents/skills/` に配置されたスキルは、各 CLI が `cwd=workdir` で実行される際に自動的にシステムコンテキストとして読み込まれる。**ハーネスはスキルファイルの内容を読み込まない**。ハーネスのプロンプトはスキル名を参照するだけで、スキル本体のロードは CLI に委譲する
- **段階的開示（Progressive Disclosure）**: CLAUDE.md / AGENTS.md はルーティング情報（スキル一覧、ワークフロー名、品質チェックコマンド等）のみに留め、軽量に保つ。コーディング規約・テスト規約・設計テンプレート等の詳細ルールは、各スキルが実行時に Read ツールで必要なドキュメントだけを読み込む。これによりコンテキストウィンドウを節約し、各ステップで関連情報のみがロードされる
- **ハーネスは「何をどの順で実行するか」だけを制御**: ワークフロー定義（YAML）を解釈し、CLI を外部呼び出し（方式 A）してスキルを順次実行する。1ステップ1関心事の分割により、エージェントが過大なタスクを一度に実行する傾向（over-ambitious execution）を構造的に防止する
- **3層の記憶構造**:

| 層 | 媒体 | 用途 | 寿命 |
|---|------|------|------|
| 短期 | CLI resume セッション | 同一 agent 内のコンテキスト継続 | セッション内 |
| 中期 | `progress.md` / `session-state.json` | ローカル状態確認・`--from` 再実行 | ワークフロー実行中 |
| 長期 | GitHub Issue（本文更新 + コメント） | agent 間・セッション間の知識共有 | 永続 |

  - **短期**: CLI の resume 機能に委譲。同一 agent 内はセッション継続、レビュー等は意図的にセッション切断
  - **中期**: ハーネスがステップ完了ごとに自動更新。人間の状態確認や異常終了後の再実行に使用
  - **長期**: スキルが Issue 本文更新 + コメントで作業成果・判定結果を記録。次セッション開始時にスキルが Issue を読んで現状を把握する（Anthropic の「progress file」パターンに相当）

- **異常終了時は `--from` で再実行**: ハーネスのクラッシュ時は SessionState（ステップ完了ごとに永続化）を基に、途中のステップから手動で再実行する。自動リカバリは実装しない
- **スキル・ワークフロー作成マニュアル**: AI がスキルやワークフロー定義を生成・最適化することを前提に、機械可読な仕様書として `docs/dev/` に配置する。CLAUDE.md / AGENTS.md にはマニュアルへのルーティングを記載しない（初期ロードの軽量化）。スキル作成・最適化を依頼する際に、作業指示でマニュアルパスを指定する

| マニュアル | 配置先 | 内容 |
|-----------|--------|------|
| スキル作成マニュアル | `docs/dev/skill-authoring.md` | スキルファイル構成、SKILL.md フォーマット、verdict 出力規約、GitHub Issue 活用規約（セクション管理ルール、レビュー系の追加ルール）、推奨パターン（Devil's Advocate プリアンブル、インクリメンタルコミット等） |
| ワークフロー定義マニュアル | `docs/dev/workflow-authoring.md` | YAML 構造、ステップフィールド定義、サイクル宣言、verdict 遷移設計、CLI フラグマッピング |

## インターフェース

### 入力

#### 1. ワークフロー定義ファイル（YAML）

```yaml
# workflows/feature-development.yaml
name: feature-development
description: "設計→レビュー→実装→レビュー→PR の標準フロー"
execution_policy: auto  # auto / sandbox / interactive

# サイクル宣言 — ループ構造を明示し、静的検証の対象にする
cycles:
  design-review:
    entry: review-design                   # サイクルの入口（初回レビュー）
    loop: [fix-design, verify-design]      # RETRY 時のループ（fix → verify → fix → ...）
    max_iterations: 3                      # fix→verify を1イテレーションとしてカウント
    on_exhaust: ABORT                      # 上限到達時の verdict

  code-review:
    entry: review-code
    loop: [fix-code, verify-code]
    max_iterations: 3
    on_exhaust: ABORT

# ステップ定義 — フラットで遷移が明示的
steps:
  - id: design
    skill: issue-design
    agent: claude
    model: sonnet
    effort: high
    max_turns: 50
    resume: null
    on:
      PASS: review-design         # → design-review サイクルへ
      ABORT: end

  - id: review-design
    skill: issue-review-design
    agent: codex
    effort: high
    resume: null                  # コンテキスト切断
    on:
      PASS: implement             # サイクル脱出
      RETRY: fix-design           # サイクル内ループへ
      ABORT: end

  - id: fix-design
    skill: issue-fix-design
    agent: claude
    effort: high
    resume: design                # design セッションを継続
    on:
      PASS: verify-design
      ABORT: end

  - id: verify-design
    skill: issue-verify-design
    agent: codex
    effort: high
    resume: null                  # コンテキスト切断
    on:
      PASS: implement             # サイクル脱出
      RETRY: fix-design           # ループ継続

  - id: implement
    skill: issue-implement
    agent: claude
    model: opus
    effort: high
    max_budget_usd: 5.0
    max_turns: 80
    resume: null
    on:
      PASS: review-code           # → code-review サイクルへ
      ABORT: end

  - id: review-code
    skill: issue-review-code
    agent: codex
    effort: high
    resume: null
    on:
      PASS: doc-check             # サイクル脱出
      RETRY: fix-code             # サイクル内ループへ
      BACK: design                # 設計フェーズへ差し戻し
      ABORT: end

  - id: fix-code
    skill: issue-fix-code
    agent: claude
    effort: high
    max_turns: 50
    resume: implement
    on:
      PASS: verify-code
      ABORT: end

  - id: verify-code
    skill: issue-verify-code
    agent: codex
    effort: high
    resume: null
    on:
      PASS: doc-check             # サイクル脱出
      RETRY: fix-code             # ループ継続

  - id: doc-check
    skill: issue-doc-check
    agent: claude
    resume: implement
    on:
      PASS: pr
      ABORT: end

  - id: pr
    skill: issue-pr
    agent: claude
    resume: implement
    on:
      PASS: end
      ABORT: end
```

#### ワークフロー定義の構造

| セクション | 役割 |
|-----------|------|
| `cycles` | ループ構造の宣言。どのステップがサイクルを構成するか、上限回数、上限到達時の挙動を定義 |
| `steps` | フラットなステップ定義。各ステップの遷移は `on` で明示的に記述 |

#### ステップのフィールド

| フィールド | 必須 | 型 | 説明 |
|-----------|------|-----|------|
| `id` | Yes | str | ステップ識別子（一意） |
| `skill` | Yes | str | 実行するスキル名 |
| `agent` | Yes | str | `claude` / `codex` / `gemini` |
| `model` | No | str | モデル指定。省略時はエージェントのデフォルト |
| `effort` | No | str | `low` / `medium` / `high`。省略時はエージェントのデフォルト |
| `max_budget_usd` | No | float | API費用上限。Claude Code のみ有効、他は無視 |
| `max_turns` | No | int | ツール呼び出し回数上限。Claude Code のみ有効、他は無視 |
| `timeout` | No | int | ステップのタイムアウト秒数。省略時はデフォルト（1800s） |
| `resume` | No | str\|null | セッション継続元の step id。null = 新規セッション |
| `on` | Yes | dict | verdict → 遷移先マッピング |

#### 実行ポリシー（execution_policy）

ワークフロー定義のトップレベルで指定。全ステップに適用される:

```yaml
execution_policy: auto  # auto / sandbox / interactive
```

| ポリシー | 説明 | Claude Code | Codex | Gemini |
|---------|------|-------------|-------|--------|
| `auto` | 全承認を自動化（デフォルト） | `--permission-mode bypassPermissions` | `--dangerously-bypass-approvals-and-sandbox` | `--approval-mode yolo` |
| `sandbox` | サンドボックス内で自動 | `--permission-mode default` | `-s workspace-write` | `-s` |
| `interactive` | 承認要求あり（デバッグ用） | *(フラグなし)* | *(フラグなし)* | *(フラグなし)* |

> **重要**: `auto` ポリシーは外部サンドボックス環境（コンテナ等）での実行を前提とする。
> Codex の `--dangerously-bypass-approvals-and-sandbox` は名前の通り危険なフラグであり、
> 信頼できない環境での使用は禁止。

#### CLI フラグマッピング

| フィールド | Claude Code | Codex | Gemini |
|-----------|-------------|-------|--------|
| `model` | `--model {v}` | `-m {v}` | `-m {v}` |
| `effort` | `--effort {v}` | `-c 'model_reasoning_effort="{v}"'` | *(無視)* |
| `max_budget_usd` | `--max-budget-usd {v}` | *(無視)* | *(無視)* |
| `max_turns` | `--max-turns {v}` | *(無視)* | *(無視)* |

#### サイクル宣言のフィールド

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `entry` | Yes | サイクルの入口ステップ（初回レビュー） |
| `loop` | Yes | RETRY 時に繰り返すステップの順序リスト |
| `max_iterations` | Yes | ループ上限（`loop` の全ステップ実行を1イテレーションとしてカウント） |
| `on_exhaust` | Yes | 上限到達時に発行する verdict（`ABORT` 推奨） |

#### 判定（verdict）の責務境界

| 責務 | 担当 | 説明 |
|------|------|------|
| verdict の**生成** | スキル | `---VERDICT---` ブロックを出力 |
| verdict の**パース** | ハーネス | テキストから status / reason / evidence / suggestion を抽出 |
| verdict に基づく**遷移** | ハーネス | `on` フィールドに従い次ステップを決定 |
| verdict の**意味定義** | ワークフロー定義 | `PASS`=次へ、`RETRY`=ループ、`BACK`=前フェーズへ戻る、`ABORT`=中断 |
| イテレーション**上限判定** | ハーネス | `cycles` の `max_iterations` と照合 |

#### 2. CLI 実行引数

```bash
dao run --workflow feature-development --issue 123 [--from design] [--step review-code]
```

| 引数 | 必須 | 説明 |
|------|------|------|
| `--workflow` | Yes | ワークフロー定義名（`workflows/` 内のファイル名） |
| `--issue` | Yes | GitHub Issue 番号 |
| `--from` | No | 指定ステップから開始（途中再開用） |
| `--step` | No | 単一ステップのみ実行 |
| `--dry-run` | No | 実行せずワークフローの遷移を表示 |

### 出力

- **セッション状態ファイル**: `test-artifacts/<issue>/session-state.json` — issue-scoped な stable state。セッション ID・verdict 履歴・サイクルカウント・`last_transition_verdict` を保持（ステップ完了ごとに更新）
- **進捗ファイル**: `test-artifacts/<issue>/progress.md` — 人間可読な進捗一覧（ステップ完了ごとに自動更新）
- **各実行の run ログ**: `test-artifacts/<issue>/runs/<timestamp>/` に JSONL 形式で保存
- **GitHub Issue コメント**: 各ステップ完了時にスキルが Issue にコメント（ハーネスではなくスキル側の責務）

### 使用例

```python
# プログラム的利用
from dao_harness import WorkflowRunner

runner = WorkflowRunner(
    workflow="feature-development",
    issue_number=123,
    workdir="/home/aki/dev/kamo2-feat-123",
)
result = runner.run()
print(result.final_verdict)  # PASS or ABORT
print(result.completed_steps)  # ["design", "review-design", "implement", ...]
```

## 制約・前提条件

### 技術的制約

1. **CLI の非インタラクティブモード依存**: Claude Code は `-p`、Codex は `exec`、Gemini は `-p` で非インタラクティブ実行
2. **セッション resume は同一 agent 内のみ**: Claude → Codex 間のセッション引き継ぎは不可能。agent を跨いだ `resume` 指定はワークフロー定義のバリデーション時にエラーとする
3. **JSON 出力の CLI 差異**: 各 CLI の JSON 出力フォーマットが異なる（後述の出力パーサで吸収）

| CLI | 非インタラクティブ | ストリーミング出力 | resume 時 JSON | セッション ID 取得 | 自動承認 |
|-----|-------------------|-------------------|---------------|-------------------|---------|
| Claude Code v2.1+ | `-p` | `--output-format stream-json --verbose` | 可 | `session_id` フィールド | `--permission-mode bypassPermissions` |
| Codex v0.112+ | `exec` | `--json`（デフォルトで JSONL ストリーム） | **可**（v0.112 で解消） | `thread.started` → `thread_id` | `--dangerously-bypass-approvals-and-sandbox` |
| Gemini v0.31+ | `-p` | `-o stream-json` | 可 | `init` → `session_id` | `--approval-mode yolo` |

4. **Gemini の `--allowed-tools` 非推奨**: v0.30.0+ では Policy Engine（TOML）が推奨。当面はレガシーの `--allowed-tools` も使用可能
5. **スキルの配置先が CLI により異なる**: 同一スキル内容（YAML frontmatter + Markdown）を、CLI ごとに異なるディレクトリに配置する必要がある

| CLI | スキル配置先 | ファイル |
|-----|------------|---------|
| Claude Code | `.claude/skills/<skill-name>/` | `SKILL.md` |
| Codex / Gemini | `.agents/skills/<skill-name>/` | `SKILL.md` |

ハーネスは CLI 起動前にスキルファイルの存在を検証する（pre-flight check）。**スキルの内容は読み込まない** — CLI が `cwd=workdir` で実行される際にネイティブにロードする:

```python
SKILL_DIRS = {
    "claude": ".claude/skills",
    "codex": ".agents/skills",
    "gemini": ".agents/skills",
}

def validate_skill_exists(skill_name: str, agent: str, workdir: Path) -> None:
    """CLI 起動前のスキル存在確認（pre-flight check）。
    スキル内容は CLI がネイティブにロードするため、ハーネスは読み込まない。"""
    base = workdir / SKILL_DIRS[agent] / skill_name / "SKILL.md"
    # パストラバーサル防御
    resolved = base.resolve()
    if not resolved.is_relative_to(workdir.resolve()):
        raise SecurityError(f"Skill path escapes workdir: {resolved}")
    if not resolved.exists():
        raise SkillNotFound(f"{base} not found")
```

6. **タイムアウト・プロセス管理**: 各ステップにタイムアウト（デフォルト 1800s）を設定。タイムアウト時はプロセスを SIGTERM → 猶予後 SIGKILL で強制終了。タイムアウト検出は `threading.Event` でメインスレッドに通知し、メインスレッドで `StepTimeoutError` を raise する
7. **verdict ブロック内は YAML 形式**: `yaml.safe_load()` でパース。`evidence` / `suggestion` の複数行記述（YAML block scalar `|`）に対応。PyYAML は既存依存（`pyyaml`）

### ビジネス制約

1. **既存のワークフロー互換性**: 現行の `/issue-create` → `/issue-close` のスキルチェーンを変更せずにハーネスで自動化できること
2. **段階的移行**: 既存の `bugfix_agent/` を即座に削除せず、新ハーネスと並行稼働できること

### エラー階層

現行オーケストレータの5種エラークラスを継承・再編:

```python
class HarnessError(Exception):
    """ハーネスの基底例外。"""

# --- ワークフロー定義エラー（起動時に検出） ---
class WorkflowValidationError(HarnessError):
    """ワークフロー YAML の静的検証エラー。"""

# --- スキル解決エラー ---
class SkillNotFound(HarnessError):
    """スキルファイルが見つからない。"""

class SecurityError(HarnessError):
    """パストラバーサル等のセキュリティ違反。"""

# --- CLI 実行エラー ---
class CLIExecutionError(HarnessError):
    """CLI プロセスが非ゼロ終了。"""
    def __init__(self, step_id: str, returncode: int, stderr: str): ...

class CLINotFoundError(HarnessError):
    """CLI コマンドが見つからない（FileNotFoundError をラップ）。"""

class StepTimeoutError(HarnessError):
    """ステップがタイムアウト。SIGTERM → SIGKILL 後に raise。"""
    def __init__(self, step_id: str, timeout: int): ...

# --- Verdict エラー ---
class VerdictNotFound(HarnessError):
    """出力に ---VERDICT--- ブロックがない。回復不能。"""

class VerdictParseError(HarnessError):
    """必須フィールド欠損。回復不能。"""

class InvalidVerdictValue(HarnessError):
    """on に未定義の status 値。プロンプト違反。回復不能・リトライしない。"""

# --- 遷移エラー ---
class InvalidTransition(HarnessError):
    """verdict.status に対応する遷移先が on に未定義。"""
```

> **現行からの継承**: `VerdictParseError`, `InvalidVerdictValueError` の回復不能セマンティクスを踏襲。
> 現行の `AgentAbortError`（ABORT verdict 時に raise）は廃止 — 新設計では ABORT は通常の遷移として `on` で処理する。
> 現行の `LoopLimitExceeded`（例外で中断）も廃止 — 新設計ではサイクル上限到達時に `on_exhaust` verdict を発行して遷移する。

### プロンプトプリアンブル（Devil's Advocate 等）

現行オーケストレータではレビュー系ステップに Devil's Advocate プリアンブルをハーネスが注入していたが、新設計ではスキル側の責務とする。レビュー品質を担保するプリアンブルのパターンは、スキル作成マニュアルに推奨事項として記載する。

## 方針

### 3層アーキテクチャ

```
┌──────────────────────────────────────────────────────────┐
│ Layer 1: ワークフロー定義 (YAML)                          │
│  ハーネスが解釈する。steps / transitions / conditions      │
│  agent・model・resume 指定                                │
├──────────────────────────────────────────────────────────┤
│ Layer 2: スキル入出力契約                                  │
│  ハーネスが注入（コンテキスト変数）・パース（verdict）する  │
│  Input:  コンテキスト変数 + スキル名参照（内容は CLI が読む）│
│  Output: verdict (PASS/RETRY/BACK/ABORT) + structured fields │
├──────────────────────────────────────────────────────────┤
│ Layer 3: スキル本体                                       │
│  PJ 固有。Claude Code / Codex / Gemini の形式で記述       │
│  .claude/skills/ or .agents/skills/ に配置                │
│  PJ ドキュメント参照は自由                                │
└──────────────────────────────────────────────────────────┘
```

### スキル入出力契約（Layer 2）

ハーネスとスキル間の入出力ルール。現行オーケストレータ（`bugfix_agent/`）の verdict プロトコルを継承・簡素化したもの。

#### 入力契約（ハーネス → スキル）

ハーネスがプロンプトに注入するコンテキスト変数:

| 変数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `issue_number` | int | Yes | GitHub Issue 番号 |
| `step_id` | str | Yes | 現在のステップ ID |
| `previous_verdict` | str | No | 現在の遷移を引き起こした verdict（reason + evidence + suggestion）。fix/verify 系ステップで注入 |
| `cycle_count` | int | No | 現在のサイクルイテレーション（1-indexed）。サイクル内ステップのみ |
| `max_iterations` | int | No | サイクルの上限回数。サイクル内ステップのみ |

> **現行からの継承**: `${loop_count}` / `${max_loop_count}` を `cycle_count` / `max_iterations` に改名。
> fix 系スキルが「何回目の修正か」を知ることで、Issue 本文の既存セクション削除→再追記を制御できる。
>
> **`previous_verdict` の参照元**: 同一ステップの過去履歴ではなく、`state.last_transition_verdict`
> （現在の遷移を引き起こした verdict）を参照する。これにより `review-design` → `fix-design` の遷移で
> レビュー指摘が正しく渡される。

#### 出力契約（スキル → ハーネス）

スキルは実行完了時に以下の verdict ブロックを出力に含めること。ブロック内部は **YAML 形式** で記述する（複数行 evidence/suggestion に対応するため）:

```
---VERDICT---
status: PASS | RETRY | BACK | ABORT
reason: "判定理由"
evidence: |
  具体的根拠（テスト結果、レビュー指摘、差分等）
  複数行記述可能（YAML block scalar）
suggestion: "次のアクション提案"
---END_VERDICT---
```

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `status` | Yes | ステップの `on` に定義された verdict 値のいずれか |
| `reason` | Yes | 判定理由（空文字不可） |
| `evidence` | Yes | 具体的な根拠。抽象表現禁止（「問題なし」ではなく「全12テスト PASS、カバレッジ 85%」） |
| `suggestion` | ABORT/BACK 時必須 | 中断・差し戻し時に次に何をすべきかの提案。PASS/RETRY 時は任意 |

> **verdict 値の意味**:
> - `PASS`: 成功。次のステップへ進む
> - `RETRY`: 軽微な問題。サイクル内で修正を繰り返す
> - `BACK`: 根本的な問題。前のフェーズ（例: 実装→設計）へ差し戻す
> - `ABORT`: 続行不能。ワークフローを中断する

> **現行からの継承**: 現行の `Result` / `Reason` / `Evidence` / `Suggestion` 4フィールド形式を踏襲。
> `Result` → `status` に改名（ワークフロー定義の `on` キーと直接対応させるため）。

#### エラー分類

| エラー | 回復可能 | 説明 |
|--------|---------|------|
| **verdict 未検出** | No | 出力に `---VERDICT---` ブロックがない。`VerdictNotFound` を raise |
| **status 値不正** | No | `on` に定義されていない status 値。`InvalidVerdictValue` を raise（プロンプト違反を示すため、リトライしない） |
| **フィールド欠損** | No | 必須フィールド（status, reason, evidence）が欠損。`VerdictParseError` を raise |

> **現行からの継承**: `InvalidVerdictValueError` は回復不能エラーとして即座に raise する設計を踏襲。
> 現行の AI Formatter Retry（3段フォールバック）はデリミタ形式の採用により廃止。

#### GitHub Issue 活用規約（長期記憶）

GitHub Issue はセッション間・agent 間の長期記憶として機能する。スキルは以下の規約に従って Issue を操作すること。

| 操作 | 担当 | タイミング | 方法 |
|------|------|-----------|------|
| **Issue 本文更新** | 作業系スキル | ステップ完了時 | `gh issue edit` で成果物セクションを追記 |
| **Issue コメント** | レビュー系スキル | verdict 確定時 | `gh issue comment` で verdict + チェックリストを投稿 |
| **Issue 読み込み** | 全スキル | セッション開始時 | `gh issue view` で現状を把握 |

**セクション管理ルール**:

- **cycle_count=1（初回）**: Issue 本文末尾にセクションを追記
- **cycle_count>=2（再実行）**: 既存の同名セクションを削除してから末尾に再追記
- スキルは `cycle_count` コンテキスト変数を参照して動作を切り替える

**レビュー系スキルの追加ルール**:

- PASS 時のみ Issue 本文にレビュー結果を反映
- RETRY/BACK 時はコメントのみ（本文は更新しない）

> 詳細なセクション構成・フォーマットはスキル作成マニュアルに記載。

### ハーネスのメインループ（疑似コード）

```python
def run_workflow(workflow: Workflow, issue: int, workdir: Path, verbose: bool = True):
    execution_policy = workflow.execution_policy or "auto"

    # 0. 全ステップのスキル存在を事前検証（pre-flight）
    for step in workflow.steps:
        validate_skill_exists(step.skill, step.agent, workdir)

    # 1. issue-scoped な状態をロードまたは新規作成
    state = SessionState.load_or_create(issue)

    # 2. run ログは実行ごとにタイムスタンプ別ディレクトリ
    run_dir = Path(f"test-artifacts/{issue}/runs/{datetime.now().strftime('%y%m%d%H%M')}")
    run_dir.mkdir(parents=True, exist_ok=True)
    logger = RunLogger(run_dir / "run.log")
    logger.log_workflow_start(issue, workflow.name)
    current_step = workflow.find_start_step()

    while current_step and current_step.id != "end":
        start_time = time.monotonic()
        cycle = workflow.find_cycle_for_step(current_step.id)

        # 3. サイクル上限チェック
        if cycle and state.cycle_iterations(cycle.name) >= cycle.max_iterations:
            verdict = Verdict(status=cycle.on_exhaust,
                              reason=f"Cycle '{cycle.name}' exhausted",
                              evidence=f"{cycle.max_iterations} iterations reached",
                              suggestion="手動で確認してください")
            cost = None
        else:
            # 4. コンテキスト変数のみを含むプロンプトを構築（スキル内容は CLI がロード）
            prompt = build_prompt(current_step, issue, state, workflow)

            # 5. CLI を実行（ストリーミング + リアルタイムログ）
            session_id = state.get_session_id(current_step.resume) if current_step.resume else None
            step_log_dir = run_dir / current_step.id
            step_log_dir.mkdir(parents=True, exist_ok=True)
            logger.log_step_start(current_step.id, current_step.agent,
                                  current_step.model, current_step.effort, session_id)

            result = execute_cli(
                step=current_step, prompt=prompt, workdir=workdir,
                session_id=session_id, log_dir=step_log_dir,
                execution_policy=execution_policy, verbose=verbose,
            )

            # 6. セッション ID を保存
            state.save_session_id(current_step.id, result.session_id)
            cost = result.cost

            # 7. verdict をパース（YAML 形式）
            verdict = parse_verdict(result.full_output,
                                    valid_statuses=set(current_step.on.keys()))

        # 8. ログ記録 + 状態更新（last_transition_verdict も保存）
        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.log_step_end(current_step.id, verdict, duration_ms, cost)
        state.record_step(current_step.id, verdict)

        if cycle and current_step.id == cycle.loop[-1] and verdict.status == "RETRY":
            state.increment_cycle(cycle.name)
            logger.log_cycle_iteration(cycle.name,
                                       state.cycle_iterations(cycle.name),
                                       cycle.max_iterations)

        # 9. 次のステップを決定
        next_step_id = current_step.on.get(verdict.status)
        if next_step_id is None:
            raise InvalidTransition(current_step.id, verdict.status)
        current_step = workflow.find_step(next_step_id)

    logger.log_workflow_end("COMPLETE", state.cycle_counts,
                           total_duration_ms=..., total_cost=..., error=None)
    return state
```

### CLI 実行の抽象化

全 CLI で JSONL ストリーミング出力を統一採用し、リアルタイムログ出力を実現する。

#### ストリーミング出力の統一

| CLI | フラグ | 形式 | 備考 |
|-----|--------|------|------|
| Claude Code | `--output-format stream-json --verbose` | JSONL | `--verbose` 必須 |
| Codex | `--json` | JSONL | デフォルトで逐次出力 |
| Gemini | `-o stream-json` | JSONL | — |

#### イベント構造の CLI 差分

全 CLI が JSONL を出力するが、イベントの JSON 構造は異なる。CLI 別アダプタで吸収:

| 情報 | Claude Code | Codex | Gemini |
|------|-------------|-------|--------|
| 初期化 | `{type:"system", subtype:"init", session_id}` | `{type:"thread.started", thread_id}` | `{type:"init", session_id}` |
| テキスト応答 | `{type:"assistant", message:{content:[{text}]}}` | `{type:"item.completed", item:{type:"agent_message", text}}` | `{type:"response", response:{content:[{text}]}}` |
| 完了 | `{type:"result", result, total_cost_usd}` | `{type:"turn.completed", usage:{input_tokens,...}}` | *(最終 response)* |
| セッションID | `session_id` | `thread_id` | `session_id` |
| コスト | `total_cost_usd` (USD) | `usage.input_tokens` (トークン数) | なし |

#### アーキテクチャ

```
                    ┌─────────────────────────────────┐
  Popen(stdout=PIPE)│    CLI プロセス (JSONL出力)       │
                    └──────────┬──────────────────────┘
                               │ 行単位読み取り
                    ┌──────────▼──────────────────────┐
                    │  StreamProcessor (共通)           │
                    │  ├─ raw ログ書き出し (即時flush)   │
                    │  ├─ CLI別アダプタでデコード        │
                    │  ├─ コンソールログ書き出し         │
                    │  └─ verbose: ターミナル出力        │
                    └──────────┬──────────────────────┘
                               │ 完了後
                    ┌──────────▼──────────────────────┐
                    │  CLIResult                       │
                    │  ├─ full_output: str (全テキスト)  │
                    │  ├─ session_id: str              │
                    │  ├─ cost: CostInfo | None        │
                    │  └─ stderr: str                  │
                    └─────────────────────────────────┘
```

#### CLI 別アダプタ（Protocol）

```python
class CLIEventAdapter(Protocol):
    """CLI 固有の JSONL イベント構造をデコードする。共通部分はない。"""

    def extract_session_id(self, event: dict) -> str | None:
        """初期化イベントからセッション ID を抽出。"""
        ...

    def extract_text(self, event: dict) -> str | None:
        """テキスト応答イベントから人間可読テキストを抽出。"""
        ...

    def extract_cost(self, event: dict) -> CostInfo | None:
        """完了イベントからコスト情報を抽出。"""
        ...


class ClaudeAdapter:
    def extract_session_id(self, event):
        if event.get("type") == "system" and event.get("subtype") == "init":
            return event.get("session_id")
        return None

    def extract_text(self, event):
        if event.get("type") == "assistant":
            content = event.get("message", {}).get("content", [])
            return "\n".join(c["text"] for c in content if c.get("type") == "text")
        if event.get("type") == "result":
            return event.get("result")
        return None

    def extract_cost(self, event):
        if event.get("type") == "result":
            return CostInfo(usd=event.get("total_cost_usd"))
        return None


class CodexAdapter:
    def extract_session_id(self, event):
        if event.get("type") == "thread.started":
            return event.get("thread_id")
        return None

    def extract_text(self, event):
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") in ("agent_message", "reasoning"):
                return item.get("text")
        return None

    def extract_cost(self, event):
        if event.get("type") == "turn.completed":
            usage = event.get("usage", {})
            return CostInfo(input_tokens=usage.get("input_tokens"),
                           output_tokens=usage.get("output_tokens"))
        return None


class GeminiAdapter:
    def extract_session_id(self, event):
        if event.get("type") == "init":
            return event.get("session_id")
        return None

    def extract_text(self, event):
        if event.get("type") == "response":
            content = event.get("response", {}).get("content", [])
            return "\n".join(c["text"] for c in content if c.get("type") == "text")
        return None

    def extract_cost(self, event):
        return None  # Gemini はコスト情報なし
```

#### ストリーミング実行（共通）

```python
ADAPTERS = {"claude": ClaudeAdapter(), "codex": CodexAdapter(), "gemini": GeminiAdapter()}

DEFAULT_TIMEOUT = 1800  # 30分

def execute_cli(step: Step, prompt: str, workdir: Path,
                session_id: str | None, log_dir: Path,
                execution_policy: str,
                verbose: bool = True) -> CLIResult:
    args = build_cli_args(step, prompt, workdir, session_id, execution_policy)
    adapter = ADAPTERS[step.agent]
    timeout = step.timeout or DEFAULT_TIMEOUT

    try:
        process = subprocess.Popen(args, stdout=PIPE, stderr=PIPE, text=True, cwd=workdir)
    except FileNotFoundError:
        raise CLINotFoundError(f"CLI '{args[0]}' not found. Is it installed?")

    # タイムアウト監視（shared Event でメインスレッドに通知）
    timed_out = threading.Event()
    timer = threading.Timer(timeout, _kill_process, args=[process, timed_out])
    timer.start()
    try:
        result = stream_and_log(process, adapter, step.id, log_dir, verbose)
        process.wait()
    finally:
        timer.cancel()

    # タイムアウト判定はメインスレッドで行う
    if timed_out.is_set():
        raise StepTimeoutError(step.id, timeout)
    if process.returncode != 0:
        raise CLIExecutionError(step.id, process.returncode, result.stderr)
    return result


def _kill_process(process: subprocess.Popen, timed_out: threading.Event):
    """タイムアウト時のプロセス強制終了。SIGTERM → 5秒猶予 → SIGKILL。
    例外は raise しない — shared Event でメインスレッドに通知。"""
    timed_out.set()
    process.terminate()  # SIGTERM
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()  # SIGKILL


def stream_and_log(process, adapter, step_id, log_dir, verbose) -> CLIResult:
    """行単位で読み取り、ログ書き出し・デコード・ターミナル表示を同時実行。"""
    session_id = None
    cost = None
    texts = []

    with open(log_dir / "stdout.log", "a") as f_raw, \
         open(log_dir / "console.log", "a") as f_con:

        for line in process.stdout:
            # 1. raw ログ（即時 flush — tail -f 対応）
            f_raw.write(line)
            f_raw.flush()

            # 2. JSON デコード → CLI別アダプタ
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            sid = adapter.extract_session_id(event)
            if sid:
                session_id = sid

            text = adapter.extract_text(event)
            if text:
                texts.append(text)
                f_con.write(text + "\n")
                f_con.flush()
                if verbose:
                    print(f"[{step_id}] {text}")

            c = adapter.extract_cost(event)
            if c:
                cost = c

    # stderr も保存
    stderr = process.stderr.read()
    if stderr:
        (log_dir / "stderr.log").write_text(stderr)

    return CLIResult(
        full_output="\n".join(texts),
        session_id=session_id,
        cost=cost,
        stderr=stderr,
    )
```

#### CLI 引数ビルダー

```python
def build_cli_args(step: Step, prompt: str, workdir: Path,
                   session_id: str | None,
                   execution_policy: str) -> list[str]:
    match step.agent:
        case "claude":
            return _build_claude_args(step, prompt, workdir, session_id, execution_policy)
        case "codex":
            return _build_codex_args(step, prompt, workdir, session_id, execution_policy)
        case "gemini":
            return _build_gemini_args(step, prompt, workdir, session_id, execution_policy)


def _build_claude_args(step, prompt, workdir, session_id, execution_policy):
    args = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    if step.model:           args += ["--model", step.model]
    if step.effort:          args += ["--effort", step.effort]
    if step.max_budget_usd:  args += ["--max-budget-usd", str(step.max_budget_usd)]
    if step.max_turns:       args += ["--max-turns", str(step.max_turns)]
    if session_id:           args += ["--resume", session_id]
    # execution_policy → 承認制御
    if execution_policy == "auto":
        args += ["--permission-mode", "bypassPermissions"]
    args.append(prompt)
    return args


def _build_codex_args(step, prompt, workdir, session_id, execution_policy):
    if session_id:
        args = ["codex", "exec", "resume", session_id, "--json"]
    else:
        args = ["codex", "exec", "--json", "-C", str(workdir)]
    if step.model:   args += ["-m", step.model]
    if step.effort:  args += ["-c", f'model_reasoning_effort="{step.effort}"']
    # max_budget_usd, max_turns: Codex 非対応 → 無視
    # execution_policy → 承認・サンドボックス制御
    match execution_policy:
        case "auto":
            args.append("--dangerously-bypass-approvals-and-sandbox")
        case "sandbox":
            args += ["-s", "workspace-write"]
        # interactive: フラグなし
    args.append(prompt)
    return args


def _build_gemini_args(step, prompt, workdir, session_id, execution_policy):
    args = ["gemini", "-p", "-o", "stream-json"]  # -p: 非インタラクティブモード
    if step.model:  args += ["-m", step.model]
    # effort, max_budget_usd, max_turns: Gemini 非対応 → 無視
    if session_id:  args += ["-r", session_id]
    # execution_policy → 承認制御
    match execution_policy:
        case "auto":
            args += ["--approval-mode", "yolo"]
        case "sandbox":
            args.append("-s")
        # interactive: フラグなし
    args.append(prompt)
    return args
```

### Verdict パース

パース戦略: デリミタで verdict ブロックを抽出し、内部を **YAML として解析** する。
これにより `evidence` や `suggestion` の複数行記述（YAML block scalar `|`）を安全に扱える:

```python
import re
import yaml
from dataclasses import dataclass

@dataclass
class Verdict:
    status: str          # PASS / RETRY / BACK / ABORT
    reason: str          # 判定理由
    evidence: str        # 具体的根拠（複数行可）
    suggestion: str      # 次のアクション提案（ABORT 時必須）

VERDICT_PATTERN = re.compile(
    r"---VERDICT---\s*\n(.*?)\n\s*---END_VERDICT---",
    re.DOTALL,
)

def parse_verdict(output: str, valid_statuses: set[str]) -> Verdict:
    """CLI 出力から verdict を抽出・検証する。

    Args:
        output: CLIResult.full_output（アダプタがデコード済みのテキスト）
        valid_statuses: ステップの on フィールドに定義された verdict 値の集合
    """
    # stream_and_log() がアダプタ経由でテキスト抽出済みなので、
    # デリミタ検索のみで十分（JSON/JSONL パースは不要）
    match = VERDICT_PATTERN.search(output)
    if not match:
        raise VerdictNotFound(output[-500:])

    verdict = _parse_fields(match.group(1))
    _validate(verdict, valid_statuses)
    return verdict

def _parse_fields(block: str) -> Verdict:
    """verdict ブロックを YAML として解析し、4フィールドを抽出。
    YAML block scalar (|) により evidence/suggestion の複数行記述に対応。"""
    try:
        fields = yaml.safe_load(block)
    except yaml.YAMLError as e:
        raise VerdictParseError(f"YAML parse error in verdict block: {e}")

    if not isinstance(fields, dict):
        raise VerdictParseError(f"Verdict block is not a YAML mapping: {type(fields)}")

    if "status" not in fields:
        raise VerdictParseError("Missing required field: status")
    if "reason" not in fields or not fields["reason"]:
        raise VerdictParseError("Missing required field: reason")
    if "evidence" not in fields or not fields["evidence"]:
        raise VerdictParseError("Missing required field: evidence")

    return Verdict(
        status=str(fields["status"]).strip(),
        reason=str(fields["reason"]).strip(),
        evidence=str(fields["evidence"]).strip(),
        suggestion=str(fields.get("suggestion", "")).strip(),
    )

def _validate(verdict: Verdict, valid_statuses: set[str]):
    """verdict 値の妥当性を検証。不正値は回復不能エラー。"""
    if verdict.status not in valid_statuses:
        raise InvalidVerdictValue(
            f"'{verdict.status}' not in {valid_statuses}. "
            "This indicates a prompt violation — do not retry."
        )
    if verdict.status in ("ABORT", "BACK") and not verdict.suggestion:
        raise VerdictParseError(f"{verdict.status} verdict requires non-empty suggestion")
```

> **verdict ブロック例（複数行 evidence）**:
> ```
> ---VERDICT---
> status: RETRY
> reason: "テスト不足と設計不整合を検出"
> evidence: |
>   1. test_workflow.py:L45 — Medium テスト未実装
>   2. session_state.py:L120 — load() の戻り型が設計と不一致
>   3. ruff check で 3 件の warning
> suggestion: "上記3点を修正し、品質チェックを再実行してください"
> ---END_VERDICT---
> ```

### ログ出力

#### ディレクトリ構造

```
test-artifacts/<issue>/
├── session-state.json         # issue-scoped stable state（再開の基盤）
├── progress.md                # 進捗一覧（人間向け、ステップ完了ごとに更新）
└── runs/
    └── <YYMMDDhhmm>/          # 各実行の run ログ
        ├── run.log            # ワークフロー層ログ（JSONL）
        ├── design/            # ステップ別ログ
        │   ├── stdout.log     # CLI 生出力（JSONL そのまま）
        │   ├── stderr.log     # CLI stderr
        │   └── console.log    # アダプタがデコード済みの人間可読テキスト
        ├── review-design/
        │   ├── stdout.log
        │   └── ...
        └── implement/
            └── ...
```

#### ワークフロー層ログ（run.log）

ハーネスのメインループが記録するイベント:

| event | payload | タイミング |
|-------|---------|-----------|
| `workflow_start` | issue, workflow, run_id | 実行開始 |
| `step_start` | step_id, agent, model, effort, session_id | ステップ開始 |
| `step_end` | step_id, verdict（4フィールド）, duration_ms, cost | ステップ完了 |
| `cycle_iteration` | cycle_name, iteration, max_iterations | サイクルカウント増加時 |
| `workflow_end` | status, cycle_counts, total_duration_ms, total_cost, error? | 実行終了 |

```python
@dataclass
class RunLogger:
    log_path: Path

    def _write(self, event: str, **kwargs):
        entry = {"ts": datetime.now(UTC).isoformat(), "event": event, **kwargs}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()

    def log_workflow_start(self, issue: int, workflow: str, run_id: str):
        self._write("workflow_start", issue=issue, workflow=workflow, run_id=run_id)

    def log_step_start(self, step_id: str, agent: str, model: str | None,
                       effort: str | None, session_id: str | None):
        self._write("step_start", step_id=step_id, agent=agent,
                    model=model, effort=effort, session_id=session_id)

    def log_step_end(self, step_id: str, verdict: Verdict,
                     duration_ms: int, cost: CostInfo | None):
        self._write("step_end", step_id=step_id,
                    verdict=asdict(verdict), duration_ms=duration_ms,
                    cost=asdict(cost) if cost else None)

    def log_cycle_iteration(self, cycle_name: str, iteration: int, max_iter: int):
        self._write("cycle_iteration", cycle_name=cycle_name,
                    iteration=iteration, max_iterations=max_iter)

    def log_workflow_end(self, status: str, cycle_counts: dict,
                        total_duration_ms: int, total_cost: float | None,
                        error: str | None = None):
        self._write("workflow_end", status=status, cycle_counts=cycle_counts,
                    total_duration_ms=total_duration_ms, total_cost=total_cost,
                    error=error)
```

> **現行からの継承**: `bugfix_agent/run_logger.py` の即時 flush + JSONL 形式を踏襲。
> イベント名を `state_enter`/`state_exit` → `step_start`/`step_end` に改名し、
> verdict 4フィールド・コスト情報・duration を追加。

### セッション状態管理

状態ファイルは **issue 単位で固定パス** に保存する。これにより `--from` 再開時にどの run の state を読むかが一意に決まる:

```
test-artifacts/<issue>/
├── session-state.json    # issue-scoped stable state（再開の基盤）
├── progress.md           # 人間可読な進捗一覧
└── runs/
    └── <YYMMDDhhmm>/     # 各実行の run ログ（タイムスタンプ別）
        ├── run.log
        └── <step_id>/
            ├── stdout.log
            ├── stderr.log
            └── console.log
```

```python
STATE_DIR = Path("test-artifacts")
STATE_FILE = "session-state.json"

@dataclass
class StepRecord:
    step_id: str
    verdict_status: str
    verdict_reason: str
    verdict_evidence: str
    verdict_suggestion: str
    timestamp: str  # ISO 8601（JSON シリアライズ可能な str）

@dataclass
class SessionState:
    issue_number: int
    sessions: dict[str, str]           # step_id → session_id
    step_history: list[StepRecord]     # 実行履歴
    cycle_counts: dict[str, int]       # cycle_name → iteration count
    last_completed_step: str | None    # 最後に完了したステップ ID（再実行用）
    last_transition_verdict: Verdict | None  # 現在の遷移を引き起こした verdict

    @classmethod
    def load_or_create(cls, issue: int) -> "SessionState":
        path = STATE_DIR / str(issue) / STATE_FILE
        if path.exists():
            data = json.loads(path.read_text())
            # step_history の rehydrate
            data["step_history"] = [StepRecord(**r) for r in data.get("step_history", [])]
            # last_transition_verdict の rehydrate
            ltv = data.pop("last_transition_verdict", None)
            if ltv:
                data["last_transition_verdict"] = Verdict(**ltv)
            return cls(**data)
        return cls(issue_number=issue, sessions={}, step_history=[],
                   cycle_counts={}, last_completed_step=None,
                   last_transition_verdict=None)

    @property
    def _state_dir(self) -> Path:
        return STATE_DIR / str(self.issue_number)

    def save_session_id(self, step_id: str, session_id: str):
        self.sessions[step_id] = session_id

    def get_session_id(self, resume_target: str | None) -> str | None:
        if resume_target is None:
            return None
        return self.sessions.get(resume_target)

    def cycle_iterations(self, cycle_name: str) -> int:
        return self.cycle_counts.get(cycle_name, 0)

    def increment_cycle(self, cycle_name: str):
        self.cycle_counts[cycle_name] = self.cycle_iterations(cycle_name) + 1

    def record_step(self, step_id: str, verdict: Verdict):
        self.step_history.append(StepRecord(
            step_id=step_id,
            verdict_status=verdict.status,
            verdict_reason=verdict.reason,
            verdict_evidence=verdict.evidence,
            verdict_suggestion=verdict.suggestion,
            timestamp=datetime.now(UTC).isoformat(),
        ))
        self.last_completed_step = step_id
        self.last_transition_verdict = verdict  # 次ステップに渡す
        self._persist()  # ステップ完了ごとに永続化

    def _persist(self):
        """JSON + progress.md に永続化。異常終了時の --from 再実行を可能にする。"""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        # 機械向け: session-state.json
        path = self._state_dir / STATE_FILE
        data = {
            "issue_number": self.issue_number,
            "sessions": self.sessions,
            "step_history": [asdict(r) for r in self.step_history],
            "cycle_counts": self.cycle_counts,
            "last_completed_step": self.last_completed_step,
            "last_transition_verdict": asdict(self.last_transition_verdict)
                                       if self.last_transition_verdict else None,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        # 人間向け: progress.md（状態確認ツール）
        self._write_progress_md()

    def _write_progress_md(self):
        """ステップ完了ごとに人間可読な進捗ファイルを更新。"""
        lines = [f"# Progress: Issue #{self.issue_number}\n"]
        for record in self.step_history:
            mark = "x" if record.verdict_status == "PASS" else " "
            lines.append(
                f"- [{mark}] {record.step_id}: {record.verdict_status}"
                f" — {record.verdict_reason}"
            )
        if self.cycle_counts:
            lines.append("\n## サイクル")
            for name, count in self.cycle_counts.items():
                lines.append(f"- {name}: {count} iterations")
        path = self._state_dir / "progress.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

### ワークフロー定義のバリデーション

ロード時に静的検証を行い、実行時エラーを防止:

```python
def validate_workflow(workflow: Workflow):
    errors = []

    # ステップレベルの検証
    for step in workflow.steps:
        # 1. resume 先が同一 agent であること
        if step.resume:
            target = workflow.find_step(step.resume)
            if target and target.agent != step.agent:
                errors.append(
                    f"Step '{step.id}' resumes '{step.resume}' but agents differ "
                    f"({step.agent} != {target.agent})"
                )

        # 2. on の遷移先が存在すること
        for verdict, next_id in step.on.items():
            if next_id != "end" and not workflow.find_step(next_id):
                errors.append(
                    f"Step '{step.id}' transitions to unknown step '{next_id}' on {verdict}"
                )

        # 3. verdict 値が有効であること
        valid_verdicts = {"PASS", "RETRY", "BACK", "ABORT"}
        for verdict in step.on:
            if verdict not in valid_verdicts:
                errors.append(f"Step '{step.id}' has invalid verdict '{verdict}'")

    # サイクルレベルの検証
    for cycle in workflow.cycles:
        # 4. entry ステップが存在すること
        if not workflow.find_step(cycle.entry):
            errors.append(f"Cycle '{cycle.name}' entry step '{cycle.entry}' not found")

        # 5. loop 内ステップが存在すること
        for step_id in cycle.loop:
            if not workflow.find_step(step_id):
                errors.append(f"Cycle '{cycle.name}' loop step '{step_id}' not found")

        # 6. loop 末尾ステップが RETRY 時に loop 先頭へ遷移すること
        last_step = workflow.find_step(cycle.loop[-1])
        if last_step and last_step.on.get("RETRY") != cycle.loop[0]:
            errors.append(
                f"Cycle '{cycle.name}' loop tail '{cycle.loop[-1]}' RETRY should "
                f"transition to loop head '{cycle.loop[0]}'"
            )

        # 7. entry/loop 内ステップが PASS 時にサイクル外へ遷移すること（脱出口の存在）
        all_cycle_steps = {cycle.entry} | set(cycle.loop)
        has_exit = False
        for step_id in all_cycle_steps:
            step = workflow.find_step(step_id)
            if step and step.on.get("PASS") not in all_cycle_steps:
                has_exit = True
                break
        if not has_exit:
            errors.append(f"Cycle '{cycle.name}' has no exit (PASS never leaves the cycle)")

        # 8. on_exhaust が有効な verdict であること
        if cycle.on_exhaust not in valid_verdicts:
            errors.append(f"Cycle '{cycle.name}' on_exhaust '{cycle.on_exhaust}' is invalid")

    if errors:
        raise WorkflowValidationError(errors)
```

### プロンプト構築

入力契約に基づき、ハーネスがコンテキスト変数と出力要件のみをプロンプトに注入する。
**スキル本体（SKILL.md）は CLI がネイティブにロードするため、ハーネスは内容を読み込まない**:

```python
def build_prompt(step: Step, issue: int, state: SessionState,
                 workflow: Workflow) -> str:
    # スキル存在確認（pre-flight — 内容は読まない）
    # validate_skill_exists() は run_workflow() 冒頭で全ステップ分を事前検証済み

    # 必須変数
    variables = {
        "issue_number": issue,
        "step_id": step.id,
    }

    # サイクル変数（サイクル内ステップのみ）
    cycle = workflow.find_cycle_for_step(step.id)
    if cycle:
        variables["cycle_count"] = state.cycle_iterations(cycle.name) + 1  # 1-indexed
        variables["max_iterations"] = cycle.max_iterations

    # 遷移元の verdict（fix/verify 系ステップ用）
    # ※ 同一 step の過去履歴ではなく、現在の遷移を引き起こした verdict を渡す
    if state.last_transition_verdict:
        v = state.last_transition_verdict
        variables["previous_verdict"] = f"reason: {v.reason}\nevidence: {v.evidence}\nsuggestion: {v.suggestion}"

    # 有効な verdict 値（スキルに許可された status を明示）
    valid_statuses = list(step.on.keys())

    header = "\n".join(f"- {k}: {v}" for k, v in variables.items())

    return f"""スキル `{step.skill}` を実行してください。

## セッション開始プロトコル
1. GitHub Issue #{issue} を読み、現在の進捗を把握する
2. git log --oneline -10 で最近の変更を確認する
3. 以下のコンテキスト変数を確認する
4. 上記を踏まえて、スキルの指示に従って作業を実行する

## コンテキスト変数
{header}

## 出力要件
実行完了後、以下の YAML 形式で verdict を出力してください:

---VERDICT---
status: {" | ".join(valid_statuses)}
reason: "判定理由"
evidence: |
  具体的根拠（複数行可。抽象表現禁止）
suggestion: "次のアクション提案"（ABORT/BACK時必須）
---END_VERDICT---
"""
```

> **スキル実行モデル**: ハーネスはスキル名を参照するだけで、CLI が `cwd=workdir` で実行される際に
> `.claude/skills/{skill_name}/SKILL.md`（Claude Code）または `.agents/skills/{skill_name}/SKILL.md`
> （Codex/Gemini）をプロジェクト設定として自動ロードする。ハーネスが skill loader / prompt assembler を
> 再実装することを明確に避ける。

### 既存コードからの流用

| 現行モジュール | 流用 | 変更点 |
|---------------|------|--------|
| `bugfix_agent/cli.py` | ○ ストリーミング実行 + `format_jsonl_line()` | `stream_and_log()` + CLI 別アダプタに再構成。行単位 flush・verbose 出力のパターンを継承 |
| `bugfix_agent/verdict.py` | △ パーサ + エラー分類 | 5段フォールバック → デリミタ+JSON/JSONL の2段に簡素化。4フィールド形式と `InvalidVerdictValue` 回復不能エラーを継承 |
| `bugfix_agent/run_logger.py` | ○ JSONL ログ | イベント名を step 系に改名、verdict 4フィールド・コスト・duration を追加 |
| `bugfix_agent/state.py` | × 削除 | SessionState に置換 |
| `bugfix_agent/handlers/` | × 削除 | スキルが担う |
| `bugfix_agent/tools/` | × 削除 | `build_*_args` 関数に置換 |
| `bugfix_agent/context.py` | × 削除 | スキル側の責務 |
| `bugfix_agent/prompts.py` | × 削除 | スキル側の責務 |
| `bugfix_agent/config.py` | △ 部分流用 | ワークフロー YAML ローダーに置換 |

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。

### Small テスト

- **ワークフロー YAML パーサ**: 正常系・異常系（不正な遷移、存在しないステップ参照、agent 不一致 resume）
- **ワークフローバリデータ**: 静的検証ルール（ステップ検証 + サイクル検証）のテスト。サイクルの entry/loop 存在確認、脱出口の有無、loop 末尾 RETRY 遷移先の整合性
- **Verdict パーサ**: デリミタ抽出、YAML 解析による4フィールド抽出（status/reason/evidence/suggestion）、複数行 evidence/suggestion（YAML block scalar `|`）の正常解析、verdict 未検出（VerdictNotFound）、status 値不正（InvalidVerdictValue — 回復不能）、必須フィールド欠損（VerdictParseError）、ABORT 時 suggestion 必須、不正 YAML 入力時のエラーハンドリング
- **CLI 別アダプタ**: 各アダプタ（Claude/Codex/Gemini）× extract_session_id / extract_text / extract_cost の組み合わせ。実際の CLI JSONL サンプルを入力としたデコード検証
- **CLI 引数ビルダー**: 各 agent（claude/codex/gemini）× 新規/resume × model/effort/max_budget_usd/max_turns/timeout × execution_policy（auto/sandbox/interactive）の組み合わせ。agent 非対応フィールドが無視されることの検証を含む。Gemini の `-p` フラグ付与、Codex の `--dangerously-bypass-approvals-and-sandbox` フラグ付与を検証
- **スキル存在検証**: agent 別ディレクトリ（`.claude/skills/` vs `.agents/skills/`）の正しい解決、存在しないスキル（SkillNotFound）、パストラバーサル防御（SecurityError）
- **セッション状態管理**: save/load（JSON rehydrate 含む）、サイクルイテレーションカウント、セッション ID 検索、`last_transition_verdict` の保存・復元
- **サイクル上限判定**: `max_iterations` 到達時の `on_exhaust` verdict 発行
- **プロンプト構築**: 変数展開、スキル内容の埋め込み

### Medium テスト

- **CLI ストリーミング統合テスト**: モック CLI プロセスが JSONL を逐次出力する環境で、`stream_and_log()` の即時 flush・アダプタデコード・ファイル出力を検証。タイムアウト時の `threading.Event` 通知・SIGTERM → SIGKILL 処理・メインスレッドでの `StepTimeoutError` raise、CLI 未インストール時の CLINotFoundError も検証
- **ワークフロー実行テスト**: モック CLI で完全なワークフロー（design → review → implement → ...）を実行し、状態遷移・リトライ・abort を検証
- **セッション状態永続化**: issue-scoped state の保存・読み込み・途中再開の統合テスト。`StepRecord` / `Verdict` の JSON rehydrate 検証、`--from` 再実行でのstate復元
- **ログ出力統合テスト**: run.log（ワークフロー層）と stdout.log / console.log（ステップ層）の書き込み・構造・即時 flush の検証

### Large テスト

- **実 CLI E2E テスト**: 実際の Claude Code / Codex CLI を使用して、単一ステップ（design のみ等）を実行し、verdict パースまでの一連の流れを検証
- **ワークフロー E2E テスト**: 簡易ワークフロー（2-3 ステップ）を実 CLI で実行し、セッション resume・verdict 遷移を検証
- **既存 PJ 互換テスト**: kamo2 の `.claude/skills/` を使用した実行テスト

### スキップするサイズ

なし。すべてのサイズのテストを実装する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | あり | 新アーキテクチャの ADR を追加 |
| docs/ARCHITECTURE.md | あり | ハーネスアーキテクチャの記載に全面改訂 |
| docs/dev/development_workflow.md | あり | ハーネス経由の自動実行フローを追記 |
| docs/dev/skill-authoring.md | あり | **新規作成**。スキル作成マニュアル（AI 参照前提） |
| docs/dev/workflow-authoring.md | あり | **新規作成**。ワークフロー定義マニュアル（AI 参照前提） |
| docs/cli-guides/ | なし | 今回の更新で最新化済み |
| CLAUDE.md | あり | Essential Commands セクションの更新 |
| pyproject.toml | あり | パッケージ名・エントリポイントの変更 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） | 検証方法 | 検証日 |
|--------|----------|-------------------|---------|--------|
| Claude Code CLI `--help` (v2.1.71) | ローカル実行結果 | `-p --output-format stream-json --verbose` で JSONL ストリーミング実行。`--model`, `--effort`, `--max-budget-usd`, `--max-turns`, `--permission-mode bypassPermissions` で制御。`stream-json` は `--verbose` 必須（なしだとエラー） | `claude --help` 実行 | 2026-03-09 |
| Codex CLI `exec --help` (v0.112.0) | ローカル実行結果 | resume 時に `--json`, `-m`, `--ephemeral`, `-o` が使用可能（v0.63.0 時点の制約が解消）。`-c key=value` で config.toml 値の CLI オーバーライドが可能（`-c 'model_reasoning_effort="high"'`）。`--dangerously-bypass-approvals-and-sandbox` で全承認・サンドボックスをバイパス。`-s workspace-write` でサンドボックス内自動実行。`--help` に「Use a dotted path (foo.bar.baz) to override nested values. The value portion is parsed as TOML.」と記載 | `codex exec --help` 実行 | 2026-03-09 |
| Codex `--json` ストリーミング動作 | ローカル実行結果 | `codex exec --json` が行単位で JSONL を逐次出力することを実機確認。イベント: `thread.started` → `item.completed` → `turn.completed`。`thread_id` でセッション ID 取得 | 実機テスト実行 | 2026-03-09 |
| Codex `model_reasoning_effort` 設定 | `~/.codex/config.toml` | `model_reasoning_effort = "high"` が config.toml で実際に使用されていることを確認。`-c` フラグでオーバーライド可能 | ファイル確認 + `--help` | 2026-03-09 |
| Gemini CLI `--help` (v0.31.0) | ローカル実行結果 | `-p` で非インタラクティブモード（headless）。`-o stream-json` で JSONL ストリーミング出力。`--approval-mode yolo` で全承認自動化。`--allowed-tools` は非推奨で Policy Engine（TOML）への移行が推奨 | `gemini --help` 実行 | 2026-03-09 |
| Gemini stream-json 動作 | ローカル実行結果 | `gemini -o stream-json` が行単位で JSONL を逐次出力することを実機確認。イベント: `init`（session_id 含む）→ `message` → `response` | 実機テスト実行 | 2026-03-09 |
| Claude Code CLI ガイド | `docs/cli-guides/claude-code-cli-guide.md` | stream-json イベント構造（system/init → assistant → result）、session_id 取得方法、`total_cost_usd` フィールド、`--verbose` 必須制約 | ドキュメント参照 | 2026-03-09 |
| Codex CLI ガイド | `docs/cli-guides/codex-cli-session-guide.md` | JSONL イベント構造（thread.started → item.completed → turn.completed）、resume 時の設定引き継ぎ、profile の `model_reasoning_effort` 設定例 | ドキュメント参照 | 2026-03-09 |
| Gemini CLI ガイド | `docs/cli-guides/gemini-cli-session-guide.md` | stream-json イベント構造（init → message → response）、Policy Engine 仕様、サンドボックス設定 | ドキュメント参照 | 2026-03-09 |
| スキルファイル形式 | `/home/aki/dev/kamo2/.claude/skills/`, `/home/aki/dev/kamo2/.agents/skills/` | 両ディレクトリに同一内容の `SKILL.md`（YAML frontmatter + Markdown）が配置されていることを実機確認。Claude Code は `.claude/skills/`、Codex/Gemini は `.agents/skills/` を参照 | `ls`, `head` で実機確認 | 2026-03-09 |
| 現行オーケストレータ | `bugfix_agent/` | verdict 4フィールド形式（`bugfix_agent/verdict.py`）、エラー階層（`bugfix_agent/errors.py`: VerdictParseError, InvalidVerdictValueError, AgentAbortError）、ストリーミング実行（`bugfix_agent/cli.py`: format_jsonl_line）、JSONL ログ（`bugfix_agent/run_logger.py`）、プロンプト構成（`prompts/_common.md`, `_review_preamble.md`, `_footer_verdict.md`）を調査し継承 | ソースコード読解 | 2026-03-09 |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L テストサイズ定義、スキップ判定基準 | ドキュメント参照 | — |
| 開発ワークフロー | `docs/dev/development_workflow.md` | 現行の 6 フェーズフロー定義、GitHub Issue 活用パターン（本文更新 + コメント） | ドキュメント参照 | — |
| Anthropic ハーネス設計 | [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | 「an initializer agent that sets up the environment on the first run, and a coding agent that is tasked with making incremental progress in every session, while leaving clear artifacts for the next session」— 2エージェントパターン、progress file、セッション開始プロトコル（pwd→git log→feature list→init.sh→E2E test）、1機能1セッション制約。JSON でのフィーチャーリスト管理（「the model is less likely to inappropriately change or overwrite JSON files compared to Markdown files」） | Web Fetch | 2026-03-09 |
| Agent Harness Infrastructure | [The Agent Harness: Why 2026 is About Infrastructure](https://www.hugo.im/posts/agent-harness-infrastructure) | 「The Agent Harness is the Operating System. The LLM is just the CPU.」— 3層分離（Framework/Runtime/Harness）、3層メモリモデル（Episodic/Semantic/Procedural）、Durable Execution パターン、「Build for Impermanence」原則 | Web Fetch | 2026-03-09 |
| Agent Harness 2026 | [The importance of Agent Harness in 2026](https://www.philschmid.de/agent-harness-2026) | 「Capabilities that required complex, hand-coded pipelines in 2024 are now handled by a single context-window prompt in 2026」— 軽量設計の根拠。Atomic Tool Design、実行トラジェクトリのデータ収集 | Web Fetch | 2026-03-09 |

> **本ハーネスで定義した仕様**: verdict デリミタ形式（`---VERDICT---` / `---END_VERDICT---`）、verdict 4フィールド（status/reason/evidence/suggestion）、セッション開始プロトコルは本設計で定義したものであり、外部一次情報源はない。現行オーケストレータの verdict プロトコルを継承・拡張した設計。
