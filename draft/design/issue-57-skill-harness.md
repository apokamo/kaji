# [設計] スキル実行ハーネスへのアーキテクチャ転換

Issue: #57

## 概要

現行の Python 状態マシンオーケストレータ（`bugfix_agent/`）を廃止し、Claude Code / Codex / Gemini CLI のスキルをワークフロー定義に従って実行する軽量ハーネスに転換する。

## 背景・目的

### 現行アーキテクチャの課題

1. **ネイティブ機能の再実装**: State machine、Verdict parsing、Tool abstraction、CLI streaming — これらは Claude Code / Codex のスキル機構がネイティブに提供する機能を Python（約2000 LOC）で再実装している
2. **PJ コンテキストの断絶**: 外部オーケストレータから各 PJ のドキュメント・コーディング規約・テスト規約を参照できない。CLI が対象 PJ の worktree 内で実行されれば、CLAUDE.md / AGENTS.md / `.claude/skills/` 等が自然に読み込まれる
3. **保守コスト**: CLI のバージョンアップ（Claude v2.0→v2.1、Codex v0.63→v0.112、Gemini v0.18→v0.31）への追従が Tool abstraction 層で困難

### 新アーキテクチャの方針

- **スキル本体は各 PJ に配置**: `.claude/skills/` や `.agents/skills/` に Claude Code / Codex 形式で配置。PJ 固有のドキュメント参照はスキル内で自由に行える
- **ハーネスは「何をどの順で実行するか」だけを制御**: ワークフロー定義（YAML）を解釈し、CLI を外部呼び出し（方式 A）してスキルを順次実行する
- **コンテキスト管理は CLI の resume 機能に委譲**: 同一 agent 内はセッション継続、レビュー等は意図的にセッション切断
- **長期記憶は GitHub Issue**: agent 間・セッション間の情報共有は従来通り Issue に書く

## インターフェース

### 入力

#### 1. ワークフロー定義ファイル（YAML）

```yaml
# workflows/feature-development.yaml
name: feature-development
description: "設計→レビュー→実装→レビュー→PR の標準フロー"

steps:
  - id: design
    skill: issue-design
    agent: claude
    model: sonnet
    resume: null
    on:
      PASS: review-design
      ABORT: end

  - id: review-design
    skill: issue-review-design
    agent: codex
    model: null
    resume: null          # コンテキスト切断
    on:
      PASS: implement
      RETRY: fix-design
      ABORT: end

  - id: fix-design
    skill: issue-fix-design
    agent: claude
    resume: design        # design セッションを継続
    on:
      PASS: verify-design
      ABORT: end

  - id: verify-design
    skill: issue-verify-design
    agent: codex
    resume: null          # コンテキスト切断
    on:
      PASS: implement
      RETRY: fix-design
    max_retries: 3

  - id: implement
    skill: issue-implement
    agent: claude
    model: opus
    resume: null
    on:
      PASS: review-code
      ABORT: end

  - id: review-code
    skill: issue-review-code
    agent: codex
    resume: null
    on:
      PASS: doc-check
      RETRY: fix-code
      ABORT: end

  - id: fix-code
    skill: issue-fix-code
    agent: claude
    resume: implement
    on:
      PASS: verify-code
      ABORT: end

  - id: verify-code
    skill: issue-verify-code
    agent: codex
    resume: null
    on:
      PASS: doc-check
      RETRY: fix-code
    max_retries: 3

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

- **各ステップの実行ログ**: `test-artifacts/<issue>/<timestamp>/` に JSONL 形式で保存
- **セッション状態ファイル**: `test-artifacts/<issue>/session-state.json` — 各ステップのセッション ID、verdict 履歴を保持
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

1. **CLI の非インタラクティブモード依存**: Claude Code は `-p`、Codex は `exec`、Gemini は位置引数で非インタラクティブ実行
2. **セッション resume は同一 agent 内のみ**: Claude → Codex 間のセッション引き継ぎは不可能。agent を跨いだ `resume` 指定はワークフロー定義のバリデーション時にエラーとする
3. **JSON 出力の CLI 差異**: 各 CLI の JSON 出力フォーマットが異なる（後述の出力パーサで吸収）

| CLI | 非インタラクティブ | JSON 出力 | resume 時 JSON | セッション ID 取得 |
|-----|-------------------|-----------|---------------|-------------------|
| Claude Code v2.1+ | `-p` | `--output-format json` | 可 | `session_id` フィールド |
| Codex v0.112+ | `exec` | `--json` | **可**（v0.112 で解消） | `thread.started` → `thread_id` |
| Gemini v0.31+ | 位置引数 | `-o json` / `-o stream-json` | 可 | `init` → `session_id` |

4. **Gemini の `--allowed-tools` 非推奨**: v0.30.0+ では Policy Engine（TOML）が推奨。当面はレガシーの `--allowed-tools` も使用可能
5. **スキルの配置はハーネス管轄外**: 各 PJ の `.claude/skills/` や `.agents/skills/` に配置済みであることを前提とする

### ビジネス制約

1. **既存のワークフロー互換性**: 現行の `/issue-create` → `/issue-close` のスキルチェーンを変更せずにハーネスで自動化できること
2. **段階的移行**: 既存の `bugfix_agent/` を即座に削除せず、新ハーネスと並行稼働できること

## 方針

### 3層アーキテクチャ

```
┌──────────────────────────────────────────────────────────┐
│ Layer 1: ワークフロー定義 (YAML)                          │
│  ハーネスが解釈する。steps / transitions / conditions      │
│  agent・model・resume 指定                                │
├──────────────────────────────────────────────────────────┤
│ Layer 2: スキル入出力契約                                  │
│  ハーネスが注入（入力変数）・パース（verdict）する          │
│  Input:  テンプレート変数展開                              │
│  Output: verdict (PASS/RETRY/BACK/ABORT) + reason         │
├──────────────────────────────────────────────────────────┤
│ Layer 3: スキル本体                                       │
│  PJ 固有。Claude Code / Codex / Gemini の形式で記述       │
│  .claude/skills/ or .agents/skills/ に配置                │
│  PJ ドキュメント参照は自由                                │
└──────────────────────────────────────────────────────────┘
```

### ハーネスのメインループ（疑似コード）

```python
def run_workflow(workflow: Workflow, issue: int, workdir: Path):
    state = SessionState.load_or_create(issue)
    current_step = workflow.find_start_step()

    while current_step and current_step.id != "end":
        # 1. リトライ上限チェック
        if state.retry_count(current_step.id) >= current_step.max_retries:
            raise LoopLimitExceeded(current_step.id)

        # 2. スキル内容を読み込み、入力変数を展開
        prompt = build_prompt(current_step, issue, state)

        # 3. CLI を実行
        session_id = state.get_session_id(current_step.resume) if current_step.resume else None
        result = execute_cli(
            agent=current_step.agent,
            model=current_step.model,
            prompt=prompt,
            workdir=workdir,
            session_id=session_id,
        )

        # 4. セッション ID を保存
        state.save_session_id(current_step.id, result.session_id)

        # 5. verdict をパース
        verdict = parse_verdict(result.output)

        # 6. ログ記録
        state.record_step(current_step.id, verdict)

        # 7. 次のステップを決定
        next_step_id = current_step.on.get(verdict.status)
        if next_step_id is None:
            raise InvalidTransition(current_step.id, verdict.status)
        current_step = workflow.find_step(next_step_id)

    return state
```

### CLI 実行の抽象化

```python
def execute_cli(agent: str, model: str | None, prompt: str,
                workdir: Path, session_id: str | None) -> CLIResult:
    match agent:
        case "claude":
            args = build_claude_args(model, prompt, workdir, session_id)
        case "codex":
            args = build_codex_args(model, prompt, workdir, session_id)
        case "gemini":
            args = build_gemini_args(model, prompt, workdir, session_id)

    process = subprocess.run(args, capture_output=True, text=True, cwd=workdir)
    return parse_cli_output(agent, process.stdout, process.stderr)


def build_claude_args(model, prompt, workdir, session_id):
    args = ["claude", "-p", "--output-format", "json"]
    if model:
        args += ["--model", model]
    if session_id:
        args += ["--resume", session_id]
    args += ["--dangerously-skip-permissions"]  # ハーネスはサンドボックス環境で実行前提
    args.append(prompt)
    return args


def build_codex_args(model, prompt, workdir, session_id):
    if session_id:
        args = ["codex", "exec", "resume", session_id, "--json"]
    else:
        args = ["codex", "exec", "--json", "-C", str(workdir), "-s", "workspace-write"]
    if model:
        args += ["-m", model]
    args.append(prompt)
    return args


def build_gemini_args(model, prompt, workdir, session_id):
    args = ["gemini", "-o", "stream-json"]
    if model:
        args += ["-m", model]
    if session_id:
        args += ["-r", session_id]
    args += ["--allowed-tools", "run_shell_command"]  # TODO: Policy Engine 移行
    args.append(prompt)
    return args
```

### Verdict パース

スキルが出力に含めるべき verdict フォーマット:

```
---VERDICT---
status: PASS
reason: "設計レビュー完了、指摘事項なし"
---END_VERDICT---
```

パース戦略:

```python
import re
import json

VERDICT_PATTERN = re.compile(
    r"---VERDICT---\s*\n(.*?)\n\s*---END_VERDICT---",
    re.DOTALL,
)

def parse_verdict(output: str) -> Verdict:
    # Strategy 1: デリミタ付き verdict（推奨）
    match = VERDICT_PATTERN.search(output)
    if match:
        return parse_verdict_block(match.group(1))

    # Strategy 2: JSON 出力の result フィールドから抽出
    # Claude Code の --output-format json は result フィールドにテキストを含む
    try:
        data = json.loads(output)
        if "result" in data:
            match = VERDICT_PATTERN.search(data["result"])
            if match:
                return parse_verdict_block(match.group(1))
    except json.JSONDecodeError:
        pass

    # Strategy 3: JSONL（Codex / Gemini）の最終メッセージから抽出
    for line in reversed(output.strip().splitlines()):
        try:
            event = json.loads(line)
            text = extract_text_from_event(event)
            if text:
                match = VERDICT_PATTERN.search(text)
                if match:
                    return parse_verdict_block(match.group(1))
        except json.JSONDecodeError:
            continue

    raise VerdictNotFound(output[-500:])
```

### セッション状態管理

```python
@dataclass
class SessionState:
    issue_number: int
    sessions: dict[str, str]           # step_id → session_id
    step_history: list[StepRecord]     # 実行履歴
    retry_counts: dict[str, int]       # step_id → retry count

    def save_session_id(self, step_id: str, session_id: str):
        self.sessions[step_id] = session_id

    def get_session_id(self, resume_target: str | None) -> str | None:
        if resume_target is None:
            return None
        return self.sessions.get(resume_target)

    def retry_count(self, step_id: str) -> int:
        return self.retry_counts.get(step_id, 0)

    def record_step(self, step_id: str, verdict: Verdict):
        self.step_history.append(StepRecord(
            step_id=step_id,
            verdict=verdict,
            timestamp=datetime.now(),
        ))
        if verdict.status == "RETRY":
            self.retry_counts[step_id] = self.retry_count(step_id) + 1
        else:
            self.retry_counts[step_id] = 0
```

### ワークフロー定義のバリデーション

ロード時に静的検証を行い、実行時エラーを防止:

```python
def validate_workflow(workflow: Workflow):
    errors = []

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

    if errors:
        raise WorkflowValidationError(errors)
```

### プロンプト構築

ハーネスがスキル実行時にプロンプトに注入する変数:

```python
def build_prompt(step: Step, issue: int, state: SessionState) -> str:
    # スキルファイルの読み込み
    skill_content = load_skill(step.skill)

    # 入力変数の構築
    variables = {
        "issue_number": issue,
        "step_id": step.id,
    }

    # 前回の verdict がある場合（fix 系ステップ用）
    last_record = state.last_record_for(step.id)
    if last_record:
        variables["previous_verdict"] = last_record.verdict.reason

    # ヘッダーとして注入
    header = "\n".join(f"- {k}: {v}" for k, v in variables.items())

    return f"""以下のスキルを実行してください。

## コンテキスト変数
{header}

## スキル内容
{skill_content}

## 出力要件
実行完了後、以下の形式で verdict を出力してください:

---VERDICT---
status: PASS | RETRY | BACK | ABORT
reason: "(判定理由)"
---END_VERDICT---
"""
```

### 既存コードからの流用

| 現行モジュール | 流用 | 変更点 |
|---------------|------|--------|
| `bugfix_agent/cli.py` | ○ CLI ストリーミング実行 | agent 別の引数構築に簡素化 |
| `bugfix_agent/verdict.py` | △ パーサのみ | 5段フォールバック → デリミタ+JSONの2段に簡素化 |
| `bugfix_agent/run_logger.py` | ○ JSONL ログ | そのまま流用 |
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
- **ワークフローバリデータ**: 静的検証ルールのテスト
- **Verdict パーサ**: デリミタ形式、JSON 内埋め込み、JSONL 内埋め込み、verdict 未検出
- **CLI 引数ビルダー**: 各 agent（claude/codex/gemini）× 新規/resume × model 指定あり/なし の組み合わせ
- **セッション状態管理**: save/load、リトライカウント、セッション ID 検索
- **プロンプト構築**: 変数展開、スキル内容の埋め込み

### Medium テスト

- **CLI 実行統合テスト**: モック CLI プロセスを使用し、stdout/stderr の解析、セッション ID 抽出、エラーハンドリングをテスト
- **ワークフロー実行テスト**: モック CLI で完全なワークフロー（design → review → implement → ...）を実行し、状態遷移・リトライ・abort を検証
- **セッション状態永続化**: ファイル保存・読み込み・途中再開の統合テスト
- **ログ出力**: JSONL ログファイルの書き込みと構造の検証

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
| docs/cli-guides/ | なし | 今回の更新で最新化済み |
| CLAUDE.md | あり | Essential Commands セクションの更新 |
| pyproject.toml | あり | パッケージ名・エントリポイントの変更 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Claude Code CLI `--help` (v2.1.71) | ローカル実行結果 | `-p --output-format json --resume <id>` で非インタラクティブ実行+セッション継続が可能。`--model`, `--effort`, `--max-budget-usd` でモデル・コスト制御 |
| Codex CLI `exec resume --help` (v0.112.0) | ローカル実行結果 | resume 時に `--json`, `-m`, `--ephemeral`, `-o` が使用可能（v0.63.0 時点の制約が解消） |
| Gemini CLI `--help` (v0.31.0) | ローカル実行結果 | `-o stream-json` + `--allowed-tools run_shell_command` で非インタラクティブ実行。`--allowed-tools` は非推奨で Policy Engine への移行が推奨 |
| Claude Code CLI ガイド | `docs/cli-guides/claude-code-cli-guide.md` | セッション管理、JSON 出力フォーマット、サブエージェント機能の詳細 |
| Codex CLI ガイド | `docs/cli-guides/codex-cli-session-guide.md` | セッション管理、JSONL 出力フォーマット、resume 時の設定引き継ぎ動作 |
| Gemini CLI ガイド | `docs/cli-guides/gemini-cli-session-guide.md` | セッション管理、stream-json 出力、Policy Engine、サンドボックス |
| kamo2 スキル群 | `/home/aki/dev/kamo2/.claude/skills/` | 実運用中のスキル実装。17スキル、ワークフロー lifecycle パターンの実例 |
| 現行オーケストレータ | `bugfix_agent/` | 流用可能なコード（cli.py, verdict.py, run_logger.py）の特定 |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L テストサイズ定義、スキップ判定基準 |
| 開発ワークフロー | `docs/dev/development_workflow.md` | 現行の 6 フェーズフロー定義 |
