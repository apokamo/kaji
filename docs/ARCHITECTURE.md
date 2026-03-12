# Architecture: kaji_harness (V7)

**Version**: 7.0.0
**Last Updated**: 2026-03-09
**ADR**: [ADR 003: CLI スキルハーネスへの転換](adr/003-skill-harness-architecture.md)

---

## 概要

**kaji_harness** は、Claude Code / Codex / Gemini CLI のスキルをワークフロー YAML に従って実行する軽量ハーネス。

```
┌─────────────────────────────────────────────────┐
│  ハーネス (kaji_harness/)                         │
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
- **出力**: verdict ブロック（`---VERDICT---` / `---END_VERDICT---` で囲まれた YAML）。strict な出力契約であり、ハーネス側のフォールバックは後述の「Verdict 判定機構」を参照

参考: [スキル作成マニュアル](dev/skill-authoring.md)

### Layer 3: スキル本体

`.claude/skills/<name>/SKILL.md`（Claude Code 用）、`.agents/skills/<name>/SKILL.md`（Codex / Gemini 用）。CLI が `cwd=workdir` で実行する際にネイティブにロードされる。

---

## パッケージ構成

```
kaji_harness/
  __init__.py
  models.py       # データクラス: Workflow, Step, CycleDefinition, Verdict, CLIResult
  errors.py       # エラー階層 (12クラス)
  workflow.py     # YAML パーサ & バリデータ
  verdict.py      # Verdict パーサ (3段階フォールバック)
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
       │   └─ CLIEventAdapter           # stream-json → text/session_id/cost に変換
       │
       ├─ parse_verdict(output)         # 3段階フォールバックで verdict を抽出
       │   ├─ Step 1: Strict Parse     # 厳密な delimiter + YAML
       │   ├─ Step 2: Relaxed Parse    # 揺れ許容 delimiter + KV パターン
       │   └─ Step 3: AI Formatter     # エージェント再整形 → 再パース
       │
       ├─ state.record_step()           # 状態を永続化
       │
       └─ next_step = step.on[verdict]  # 遷移先を決定
```

---

## Verdict 判定機構

エージェント出力から verdict を抽出する 3 段階フォールバック。V5/V6 の運用知見に基づく設計であり、V7 で復元（#77）。

### フォールバック戦略

```
parse_verdict(output, valid_statuses, ai_formatter)
  │
  ├─ Step 1: Strict Parse
  │   └─ ---VERDICT--- / ---END_VERDICT--- 厳密一致 + YAML パース
  │
  ├─ Step 2a: Relaxed Delimiter + YAML
  │   └─ 大文字小文字・空白・アンダースコア揺れを許容した delimiter + YAML パース
  │
  ├─ Step 2b: Key-Value Pattern Extraction
  │   └─ delimiter なしの KV 形式（Result: PASS, Status: PASS, ステータス: PASS 等）
  │   └─ status は valid_statuses で動的に制約（誤検出防止）
  │   └─ reason + evidence が両方取れない場合は Step 3 へ
  │
  └─ Step 3: AI Formatter Retry（ai_formatter 提供時のみ）
      └─ raw output を 8000 文字に head+tail 切り詰め
      └─ エージェント CLI で正規フォーマットへ再整形
      └─ 再整形結果を Step 1 → 2a → 2b で再パース
      └─ 最大 2 回リトライ（各リトライでエージェント API コスト発生）
```

runner は step ごとに `create_verdict_formatter(agent, valid_statuses, model, workdir)` で formatter を生成し、`parse_verdict()` に渡す。formatter は同じエージェント CLI を plain text モードで起動する。

通常の well-formed な出力は Step 1-2 で処理され、Step 3 が起動されるのは delimiter / KV 回復でも扱えない場合に限られる。追加の API コストと遅延は常時ではなく、この最終手段に到達したときだけ発生する。

### 出力収集を含めた判定経路

Verdict 判定機構は parser 単体ではなく、`full_output` を組み立てる収集層まで含めて成立する。

- `stream_and_log()` は JSONL の decode に失敗した行も捨てず、plain text として `full_output` に保持する
- `CodexAdapter` は `agent_message` / `reasoning` に加えて `mcp_tool_call` の `result.content[].text` からもテキストを抽出する
- `parse_verdict()` は、この収集済み `full_output` を入力として初めて strict / relaxed / formatter retry を適用できる

この前提が必要なのは、Codex `mcp_tool_call` モードでは verdict が非 JSON テキストや `result.content` 側に現れ得るため。parser だけを強化しても、収集段階で verdict テキストを落とすと回復できない。

### parser と runner の責務分離

`parse_verdict()` の責務は、`output` から `Verdict` dataclass を抽出し妥当性を検証すること。`ABORT` も parse 可能な status の 1 つであり、parser 自体はワークフロー終了を決めない。

終了判定と遷移決定は runner 側の責務であり、`verdict.status` を `step.on` に当てて次ステップを選び、最終的に `ABORT` を workflow end status に反映する。

### Relaxed Parse の許容パターン

Step 2 で回復対象とする代表的な出力揺れの一覧。

**Delimiter 揺れ（Step 2a）:**

| パターン | 例 |
|----------|-----|
| アンダースコア → スペース | `---END VERDICT---`（#73 で発生） |
| 大文字小文字混在 | `---verdict---`, `---Verdict---` |
| 前後の余分な空白 | `--- VERDICT ---` |

**KV パターン揺れ（Step 2b）:**

| パターン | 例 |
|----------|-----|
| `Result:` / `Status:` | `- Result: PASS` |
| Markdown 太字 | `**Status**: PASS` |
| 等号区切り | `Status = PASS`, `Result = ABORT` |
| 日本語キー | `ステータス: PASS` |

### 失敗境界（フォールバック対象外）

以下はフォーマット揺れではなく意味的な誤りであり、フォールバックで回復すべきでない。

- **`InvalidVerdictValue`（未定義の status 値）**: 即失敗。全段階で共通。`valid_statuses` 以外の値は prompt 違反
- **`ABORT`/`BACK` verdict の suggestion 空欄**: 即失敗。次ステップへの情報が欠落しているため

### なぜ strict parse だけでは不十分か

以下は稀なエッジケースではなく通常運用で頻発する。strict parse のみでは workflow が不安定になる。

- `---END VERDICT---`（アンダースコア → スペース、#73 で発生）
- `Result: PASS` / `Status: PASS`（delimiter なしの KV 形式）
- verdict ブロック前後に思考トレース・ログが混入
- Codex `mcp_tool_call` モードでの非 JSON テキスト混在

V6→V7 移行時にこの仕組みを strict parse のみに単純化した結果、#73 で workflow 全体が停止した。この経緯が復元の直接的な理由。

### Troubleshooting

Verdict 解析に失敗した場合は、まず parser ではなく収集済み出力の欠落有無から確認する。

- `stdout.log`: 生の CLI 出力。verdict ブロックや `Result:` / `Status:`、非 JSON 行由来のテキストが実際に出ているかを見る
- `console.log`: adapter が decode / extract できたテキストと非 JSON 行の両方を含む人間可読出力。`full_output` と同等の内容
- `stderr.log`: CLI 自体のエラー出力。formatter subprocess や本体 CLI の失敗切り分けに使う
- `run.log`: workflow 全体の実行ログ。`VerdictNotFound` / `VerdictParseError` / `InvalidVerdictValue` のどれで落ちたかを確認する

---

## セッション管理と再開

**session-state.json** (`test-artifacts/<issue>/session-state.json`):

- issue 単位で1ファイル。クラッシュ後の再開基盤
- `session_id`: CLI resume 用のセッション ID をステップごとに保存
- `cycle_counts`: サイクルのイテレーション数
- `step_history`: ステップ実行履歴と verdict

**再開コマンド**:

```bash
kaji run workflows/feature-development.yaml 57 --from fix-code
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

- V5/V6 ファイルは `legacy/` に移動済み（#59 で実施）

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
