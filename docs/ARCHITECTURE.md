# Architecture: dao_harness (V7)

**Version**: 7.0.0
**Last Updated**: 2026-03-09
**ADR**: [ADR 003: CLI スキルハーネスへの転換](adr/003-skill-harness-architecture.md)

---

## 概要

**dao_harness** は、Claude Code / Codex / Gemini CLI のスキルをワークフロー YAML に従って実行する軽量ハーネス。

```
┌─────────────────────────────────────────────────┐
│  ハーネス (dao_harness/)                         │
│  ワークフロー YAML を解釈し、CLI を順次呼び出す  │
├─────────────────────────────────────────────────┤
│  スキル (.claude/skills/, .agents/skills/)       │
│  各ステップの実作業プロンプト。CLI がロード      │
├─────────────────────────────────────────────────┤
│  CLI (Claude Code / Codex / Gemini)              │
│  スキルをロードし、PJ コンテキストで実行         │
└─────────────────────────────────────────────────┘
```

**設計原則**: ハーネスは「何をどの順で実行するか」だけを制御する。スキルのロード・PJ ドキュメント参照・ツール呼び出しは CLI に委譲。

---

## 3層アーキテクチャ

### Layer 1: ワークフロー定義（YAML）

`workflows/*.yaml` に宣言的に記述。ステップ・遷移条件・サイクル・execution policy を定義する。

参考: [ワークフロー定義マニュアル](dev/workflow-authoring.md)

### Layer 2: スキル入出力契約

ハーネスとスキル間のインターフェース。

- **入力**: ハーネスがプロンプトに注入するコンテキスト変数（issue_number, step_id, previous_verdict など）
- **出力**: verdict ブロック（`---VERDICT---` / `---END_VERDICT---` で囲まれた YAML）

参考: [スキル作成マニュアル](dev/skill-authoring.md)

### Layer 3: スキル本体

`.claude/skills/<name>/SKILL.md`（Claude Code 用）、`.agents/skills/<name>/SKILL.md`（Codex / Gemini 用）。CLI が `cwd=workdir` で実行する際にネイティブにロードされる。

---

## パッケージ構成

```
dao_harness/
  __init__.py
  models.py       # データクラス: Workflow, Step, CycleDefinition, Verdict, CLIResult
  errors.py       # エラー階層 (12クラス)
  workflow.py     # YAML パーサ & バリデータ
  verdict.py      # Verdict Protocol パーサ
  adapters.py     # CLI イベントアダプタ (Claude/Codex/Gemini)
  cli.py          # CLI 引数構築 & サブプロセス実行
  prompt.py       # プロンプトビルダー
  skill.py        # スキル存在確認 & パストラバーサル防御
  state.py        # セッション状態永続化
  logger.py       # JSONL 構造化ログ
  runner.py       # WorkflowRunner (自動遷移・サイクル管理)
```

---

## データフロー

```
WorkflowRunner.run()
  │
  ├─ validate_workflow(workflow)         # 静的バリデーション
  │
  ├─ SessionState.load_or_create()      # issue-scoped な状態をロード
  │
  └─ while current_step != "end":
       │
       ├─ build_prompt(step, state)     # コンテキスト変数を注入
       │
       ├─ execute_cli(step, prompt)     # CLI をサブプロセスで実行
       │   └─ CLIEventAdapter           # stream-json → Verdict/session_id/cost に変換
       │
       ├─ parse_verdict(output)         # ---VERDICT--- ブロックを抽出
       │
       ├─ state.record_step()           # 状態を永続化
       │
       └─ next_step = step.on[verdict]  # 遷移先を決定
```

---

## セッション管理と再開

**session-state.json** (`test-artifacts/<issue>/session-state.json`):

- issue 単位で1ファイル。クラッシュ後の再開基盤
- `session_id`: CLI resume 用のセッション ID をステップごとに保存
- `cycle_counts`: サイクルのイテレーション数
- `step_history`: ステップ実行履歴と verdict

**再開コマンド**:

```bash
dao run workflows/feature-development.yaml 57 --from fix-code
```

`--from` で指定したステップから再開し、`session-state.json` の `session_id` を使って CLI セッションを復元する。

---

## CLI 対応マトリクス

| 機能 | Claude Code | Codex | Gemini |
|------|-------------|-------|--------|
| 非インタラクティブ実行 | `-p` | `exec --json` | `-p` |
| ストリーミング | `--output-format stream-json --verbose` | `--json` | `-o stream-json` |
| セッション resume | `--resume <session_id>` | `resume <thread_id>` | `--resume <session_id>` |
| 承認バイパス (auto) | `--permission-mode bypassPermissions` | `--dangerously-bypass-approvals-and-sandbox` | `--approval-mode yolo` |
| モデル指定 | `--model` | `-m` | `--model` |

---

## エラー階層

```
HarnessError
├── WorkflowValidationError    # YAML 定義エラー
├── MissingResumeSessionError  # resume 先セッションなし
├── InvalidTransition          # 未定義の verdict 遷移
├── VerdictNotFound            # verdict ブロックなし
├── VerdictParseError          # verdict YAML 解析エラー
├── InvalidVerdictValue        # 未定義の verdict 値
├── CLINotFoundError           # CLI コマンドが見つからない
├── CLIExecutionError          # CLI 実行エラー
├── StepTimeoutError           # タイムアウト
├── SkillNotFoundError         # スキルファイルなし
├── PathTraversalError         # パストラバーサル防御
└── CycleLimitExhausted        # サイクル上限到達
```

---

## 記憶構造

| 層 | 媒体 | 用途 | 寿命 |
|---|------|------|------|
| 短期 | CLI resume セッション | 同一 agent 内のコンテキスト継続 | セッション内 |
| 中期 | `session-state.json`, run ログ | 状態確認・`--from` 再実行 | ワークフロー実行中 |
| 長期 | GitHub Issue（本文・コメント） | agent 間・セッション間の知識共有 | 永続 |

---

## V6 → V7 移行

- `git tag v6.0` で旧実装（`bugfix_agent/`）を保存済み
- `bugfix_agent/` は**参照用アーカイブ**。保守・機能追加の対象外
- V7 安定後に `bugfix_agent/` を削除予定

---

## 関連ドキュメント

| ドキュメント | 内容 |
|-------------|------|
| [ADR 003](adr/003-skill-harness-architecture.md) | アーキテクチャ決定記録 |
| [ワークフロー定義マニュアル](dev/workflow-authoring.md) | YAML 定義の書き方 |
| [スキル作成マニュアル](dev/skill-authoring.md) | スキルの書き方・verdict 規約 |
| [テスト規約](dev/testing-convention.md) | S/M/L テストサイズ定義 |
| [開発ワークフロー](dev/development_workflow.md) | issue → PR までのフロー |
| [Claude Code CLI ガイド](cli-guides/claude-code-cli-guide.md) | claude コマンド仕様 |
| [Codex CLI ガイド](cli-guides/codex-cli-session-guide.md) | codex コマンド仕様 |
| [Gemini CLI ガイド](cli-guides/gemini-cli-session-guide.md) | gemini コマンド仕様 |
