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

実例: `workflows/feature-development.yaml` の step 列は次のとおり（design / code 双方が review → fix → verify の cycle を持ち、`max_iterations` 超過で ABORT）。

```
design → review-design → (fix-design → verify-design) → implement
       → review-code → (fix-code → verify-code) → final-check → pr → end
```

PR 作成後の `pr-fix` / `pr-verify` は workflow YAML の自動 step ではなく、PR レビューが付いた後に手動起動するレビュー収束サイクルとして位置付ける（`/i-pr → [PR review] → /pr-fix → /pr-verify → /issue-close`）。

参考: [ワークフロー定義マニュアル](dev/workflow-authoring.md)

### Layer 2: スキル入出力契約

ハーネスとスキル間のインターフェース。

- **入力**: ハーネスがプロンプトに注入するコンテキスト変数（issue_id, issue_ref, step_id, previous_verdict など）
- **出力**: verdict ブロック（`---VERDICT---` / `---END_VERDICT---` で囲まれた YAML）。strict な出力契約であり、ハーネス側のフォールバックは後述の「Verdict 判定機構」を参照

参考: [スキル作成マニュアル](dev/skill-authoring.md)

### Layer 3: スキル本体

`.kaji/config.toml` の `paths.skill_dir` で設定されたカノニカルディレクトリ配下の `<name>/SKILL.md`。CLI が `cwd=workdir` で実行する際にネイティブにロードされる。他エージェント用ディレクトリ（例: `.agents/skills/`）はカノニカルディレクトリへのシンボリックリンクとして構成する。

kaji 標準スキルセット（`.claude/skills/` 実体、計 23 種）はライフサイクル順に次の category に整理される。

| Category | スキル |
|----------|--------|
| Lifecycle | `issue-create`, `issue-start`, `issue-close` |
| Ready gate（全 workflow 共通） | `issue-review-ready`, `issue-fix-ready` |
| Design cycle | `issue-design`, `issue-review-design`, `issue-fix-design`, `issue-verify-design` |
| Implement cycle | `issue-implement`, `issue-review-code`, `issue-fix-code`, `issue-verify-code` |
| Docs cycle | `i-doc-update`, `i-doc-review`, `i-doc-fix`, `i-doc-verify` |
| Final check | `i-dev-final-check`, `i-doc-final-check` |
| PR | `i-pr` |
| PR レビュー後サイクル | `pr-fix`, `pr-verify` |
| その他 | `kaji-run-verify` |

---

## パッケージ構成

```
kaji_harness/
  __init__.py
  config.py       # .kaji/config.toml 探索・パース (KajiConfig, PathsConfig, ExecutionConfig)
  models.py       # データクラス: Workflow, Step, CycleDefinition, Verdict, CLIResult
  errors.py       # エラー階層 (13クラス、ConfigNotFoundError 含む)
  workflow.py     # YAML パーサ & バリデータ
  verdict.py      # Verdict パーサ (3段階フォールバック)
  adapters.py     # CLI イベントアダプタ (Claude/Codex/Gemini)
  cli.py          # CLI 引数構築 & サブプロセス実行
  prompt.py       # プロンプトビルダー
  skill.py        # スキル存在確認 & パストラバーサル防御 (config.paths.skill_dir ベース)
  state.py        # セッション状態永続化 (artifacts_dir ベース)
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
       ├─ load_skill_metadata(step.skill)  # skill-step のみ。exec-step は skip
       │                                   # （frontmatter `exec_script` を解決）
       │
       ├─ if step.exec:                    # ── exec 経路（決定論的 dispatch・Issue #205） ──
       │   │
       │   ├─ execute_exec(argv=step.exec) # 任意 argv を shell=False で subprocess 実行
       │   │   └─ context env 注入 (KAJI_ISSUE_ID 等)
       │   │
       │   └─ parse_verdict(stdout, ai_formatter=None)  # AI fallback を使わない
       │
       │  elif metadata.exec_script:        # ── exec_script 経路（決定論的 dispatch） ──
       │   │
       │   ├─ execute_script(module=...)  # `python -m <module>` を subprocess 実行
       │   │   └─ context env 注入 (KAJI_ISSUE_ID 等)
       │   │
       │   └─ parse_verdict(stdout, ai_formatter=None)  # AI fallback を使わない
       │
       │  else:                            # ── agent 経路（既存・LLM dispatch） ──
       │   │
       │   ├─ build_prompt(step, state)   # コンテキスト変数を注入
       │   │
       │   ├─ if config.execution.agent_runner == "interactive_terminal":
       │   │     execute_interactive_terminal(step, prompt_path, verdict_path, ...)
       │   │       # tmux pane 上で通常 CLI を起動し verdict.yaml を polling（Issue #224 / #230）
       │   │   else:
       │   │     execute_cli(step, prompt)   # 既存 headless CLI をサブプロセスで実行
       │   │       └─ CLIEventAdapter         # stream-json → text/session_id/cost に変換
       │   │
       │   └─ parse_verdict(output)       # 3段階フォールバック（AI Formatter 含む）
       │
       ├─ logger.log_step_{start,end}(..., dispatch="exec"|"exec_script"|"agent")
       │
       ├─ state.record_step()             # 状態を永続化
       │
       └─ next_step = step.on[verdict]    # 遷移先を決定
```

決定論 dispatch は 2 経路ある。workflow.yaml の `exec:` step（任意 argv・Issue #205）と、
`exec_script` frontmatter を持つ skill（`python -m <module>`・Issue #204）。どちらも LLM を
起動せず subprocess で実行され、AI formatter fallback を呼ばない。RunLogger は agent / exec /
exec_script の 3 経路を `dispatch` field で区別する。詳細は
[ロギング規約](./reference/python/logging.md) を参照。

#### observability 2 層（機械可読ログ ↔ 起動コンソール progress）

kaji のログは責務の異なる 2 層に分かれる（Issue #235）。**機械可読ログ** は `RunLogger`
（`logger.py`）が `run.log` へ JSONL で書く実行記録で、プログラムが解析する正本。一方
**起動コンソール progress** は `console_log.py` が設定する stdlib `logging`（`kaji.*` 名前空間）で、
`kaji run` を叩いた起動コンソールに日時付き `[kaji]` 行（workflow / step / verdict / transition、
および exec step の `[review-poll]` heartbeat）を人間向けに時系列表示する（`INFO` 以下 stdout /
`WARNING` 以上 stderr、`--log-level` で閾値制御）。後者は `RunLogger` の JSONL 契約に影響しない
別系統であり、`run.log` には混入しない。

### Runner backend dispatch（headless ↔ interactive_terminal）

agent 経路の起動 backend は repository config の `[execution] agent_runner`（または
`kaji run --agent-runner`）で選ぶ（Issue #224）。

- **`headless`（既定）**: 従来どおり `execute_cli()` が `claude -p --output-format
  stream-json` / `codex exec --json` を起動し、stdout を読む。
- **`interactive_terminal`**: `execute_interactive_terminal()` が **tmux pane** 上で通常の
  対話 `claude` / `codex` を起動し（初回は `split-window -h` で origin の右、2枚目以降は右列内を
  `-v` で上下分割し、kaji 管理 agent pane を右列に最大2枚まで維持 / Issue #238）、
  stdout を読まずに attempt directory の `verdict.yaml` を polling する。完了判定は
  artifact-primary 経路（Issue #220）に完全に乗る。`tmux`（>= 3.1）/ `$TMUX` 不在は
  fail-fast、`interactive_terminal_close_on_verdict` で verdict 検知後に pane を `kill-pane`
  するか（best-effort cleanup）を制御する。transcript は `tmux pipe-pane` で `terminal.log` に
  常時記録され、`/proc` scan も util-linux `script(1)` 依存も無く Linux / macOS 同一に動く
  （tmux 単一 backend / Issue #230, [ADR 007](./adr/007-interactive-terminal-runner.md) v3）。

設定方法と手動検証手順は
[Interactive Terminal Runner ガイド](./cli-guides/interactive-terminal-runner.md) を参照。
技術選定の経緯は [ADR 007](./adr/007-interactive-terminal-runner.md)。

---

## Verdict 判定機構

### verdict source 解決順（artifact → comment → stdout）

Issue #220 以降、runner は各 step dispatch ごとに verdict を以下の順で解決する（`resolve_verdict()`）。

```
resolve_verdict(attempt_dir, full_output, valid_statuses, attempt_started_at, comment_loader, ai_formatter)
  │
  ├─ 1. artifact: attempt_dir/verdict.yaml が存在 → load_verdict_yaml（pure YAML）
  │       存在するが壊れている → fail-loud（comment / stdout へ落ちない）。source="artifact"
  │
  ├─ 2. comment: artifact 不在時のみ comment_loader() を遅延呼び出し
  │       created_at >= attempt_started_at の作業報告コメントのみを newest-first で走査し、
  │       末尾の ---VERDICT--- block を採用。source="comment"
  │       provider 取得失敗は WARN して stdout へ fallthrough
  │
  └─ 3. stdout: full_output に対する 3 段階フォールバック（後述）。source="stdout"
```

- **artifact primary**: 解決対象は常に「今 dispatch した attempt の dir」であり、`verdict.yaml` が存在すれば comment / stdout は **見ない**。stale comment が fresh artifact を上書きしない核心の不変条件。
- **comment fallback の attempt scoping**: `attempt_started_at`（dispatch 直前に harness が記録するローカル時刻）を下限に、`created_at >= attempt_started_at` のコメントのみ対象にする。retry / resume で当該 attempt が verdict を出さなかった場合に、前 attempt の作業報告コメントを誤採用しない。下限を満たすコメントが無ければ古いコメントを拾わず stdout / `VerdictNotFound` へ落とす（false verdict より解決失敗を優先する fail-safe）。
- **source != "artifact" の正規化保存**: comment / stdout で解決した場合、harness は同じ verdict を `attempt_dir/verdict.yaml` へ正規化保存する。未移行スキルが stdout しか出さなくても attempt 単位の `verdict.yaml` が必ず残る。解決経路は `run.log` の `verdict_source` イベントに記録される。
- **agent 側の書き込み順**: 上記は harness の解決順であり、agent / script の書き込み順とは別概念。interactive terminal runner では `verdict.yaml` の出現が次 step への完了トリガになるため、作業報告 Issue comment など当該 step の外部副作用を完了してから最後に `verdict.yaml` を保存する。

artifact / log の layout は attempt 単位（`runs/<run_id>/steps/<step_id>/attempt-NNN/`、詳細は § 実行アーティファクトの layout）。

### stdout フォールバック戦略

エージェント stdout から verdict を抽出する 3 段階フォールバック。V5/V6 の運用知見に基づく設計であり、V7 で復元（#77）。上記解決順の Step 3（stdout 経路）にあたる。

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
  └─ Step 3: AI Formatter Retry（ai_formatter 提供 **かつ** delimiter 抽出済み時のみ）
      └─ 入力ゲート: Step 1 / 2a で `---VERDICT---` delimiter（strict / relaxed）が
         一切抽出できなかった場合は Step 3 を起動せず `VerdictNotFound` を raise
         （AI が自然言語進捗報告から PASS を捏造する経路を構造的に閉じる。Issue #193）
      └─ raw output を 8000 文字に head+tail 切り詰め
      └─ エージェント CLI で正規フォーマットへ再整形
      └─ 再整形結果を Step 1 → 2a → 2b で再パース
      └─ 最大 2 回リトライ（各リトライでエージェント API コスト発生）
      └─ formatter が `---NO_VERDICT_FOUND---` sentinel を返したら即 `VerdictNotFound`
         （delimiter は存在するが内部が進捗報告のみ等のケース。リトライしない）
```

runner は step ごとに `create_verdict_formatter(agent, valid_statuses, model, workdir)` で formatter を生成し、`parse_verdict()` に渡す。formatter は同じエージェント CLI を plain text モードで起動する。

通常の well-formed な出力は Step 1-2 で処理され、Step 3 が起動されるのは delimiter / KV 回復でも扱えない場合に限られる。追加の API コストと遅延は常時ではなく、この最終手段に到達したときだけ発生する。

delimiter-presence-only gate により、verdict 不在の agent セッション（Issue #184 のように `ScheduleWakeup` 待ちでメインセッションが終了したケース等）は AI 捏造で穴埋めされず、`VerdictNotFound` が `HarnessError` 経由で `EXIT_RUNTIME_ERROR (= 3)` にマップされる。

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

**session-state.json** (`<artifacts_dir>/<issue>/session-state.json`):

- `artifacts_dir` は `.kaji/config.toml` の `paths.artifacts_dir`（必須設定項目）で決まる
- 相対パス指定の場合、`kaji run` は **main worktree（`provider.<type>.default_branch` を checkout している worktree）** 基準で解決する（Issue #177）。feature worktree 内で `kaji run` を実行しても artifacts は main worktree 配下に集約され、`/issue-close` で feature worktree を削除してもログが残る。絶対パス指定はそのまま。非 git / main worktree 未構成 / `provider` 未設定では legacy fallback として `repo_root` 基準に解決する
- 上記の main worktree 解決は `kaji run` 経路のみで発生する（`kaji validate` / `kaji issue` / `kaji pr` / `kaji sync` 等は従来通り `repo_root` 基準で `KajiConfig.artifacts_dir` を読む）
- issue 単位で1ファイル。クラッシュ後の再開基盤
- `session_id`: CLI resume 用のセッション ID をステップごとに保存
- `cycle_counts`: サイクルのイテレーション数
- `step_history`: ステップ実行履歴と verdict

**再開コマンド**:

```bash
kaji run workflows/feature-development.yaml 57 --from fix-code
```

`--from` で指定したステップから再開し、`session-state.json` の `session_id` を使って CLI セッションを復元する。

### 実行アーティファクトの layout

run / step / attempt の成果物は attempt 単位で分離される（Issue #220）。

```text
<artifacts_dir>/<issue>/
  session-state.json
  progress.md
  runs/<run_id>/
    run.log                       # workflow 全体ログ
    steps/<step_id>/
      attempt-001/
        prompt.txt                # agent step の build_prompt 結果（再現用）
        stdout.log / console.log / stderr.log
        terminal.log              # interactive_terminal runner の transcript（tmux pipe-pane で常時記録。Issue #224 / #230）
        pane-metadata.json        # interactive_terminal runner の pane 状態 snapshot（診断用。Issue #230）
        verdict.yaml              # resolve 後に harness が正規化保存
        result.json               # attempt 終了情報（Issue #222。下記参照）
      attempt-002/ ...            # cycle / retry / resume の再 dispatch ごとに採番
      latest -> attempt-002       # 最新 attempt への convenience symlink（best-effort）
```

- `run_id` は分（minute）精度（`%y%m%d%H%M`）。同一 step が同一 run 内で複数回 dispatch されても `attempt-NNN` で prompt / logs / verdict / result の対応が一意になる。
- `latest` symlink は人間 / 外部ツール向けの利便性。harness の verdict 解決は in-memory で保持した attempt path を使い `latest` に依存しない（symlink 非対応 FS でも壊れない）。
- 新規 run は新 layout を正とする。旧 `runs/<run_id>/<step_id>/`（attempt なしの flat 構造）が残っていても新 run はそれを温存したまま新 layout で完了する（migration は必須としない）。

#### `result.json`（attempt 終了情報, Issue #222）

各 attempt の終了情報を構造化保存する pure JSON（`kaji_harness/result.py` の `AttemptResult`）。dispatch を伴う step で正常終了・異常終了の両方に書かれる。143 / SIGTERM / timeout / interruption のような異常終了でも best-effort で `status` / `exit_code` / `signal` / `error` を残す（書き出し失敗は元例外を握り潰さない）。

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `step_id` | `str` | step ID |
| `attempt` | `int` | 1 始まりの attempt 番号 |
| `status` | `str` | 正常終了は解決済み verdict.status、異常終了は `"ABORT"` |
| `exit_code` | `int \| null` | subprocess の `returncode`（取得不能なら null） |
| `signal` | `str \| null` | `exit_code` から導出した signal 名（clean exit / signal 由来でなければ null） |
| `started_at` / `ended_at` | `str` | dispatch 直前 / 終了時の UTC ISO 8601 |
| `duration_ms` | `int` | `ended_at - started_at` の ms |
| `session_id` | `str \| null` | agent session id（exec / exec_script / 未取得は null） |
| `dispatch` | `str` | `"agent"` / `"exec_script"` / `"exec"`（exec-step・Issue #205） |
| `error` | `str \| null` | 異常終了時の例外クラス名 + 短いメッセージ（正常時 null） |

- 読み手は `result.json` の **欠落を許容**する（旧 run / best-effort 書き出し前に死んだ attempt）。verdict 解決（`resolve_verdict`）は `result.json` に依存しないため、旧 run に無くても影響しない（migration 不要）。
- `run.log` の `step_start` / `step_end` には `attempt` が付与され、`step_end` は `exit_code` / `signal` も持つ（異常終了経路でも合成 `ABORT` verdict で発火）。詳細は [`docs/reference/python/logging.md`](reference/python/logging.md)。

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
├── ConfigNotFoundError         # .kaji/config.toml が見つからない
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
| [ワークフローガイド](dev/workflow_guide.md) | ワークフロー選択基準・概要 |
| [Claude Code CLI ガイド](cli-guides/claude-code-cli-guide.md) | claude コマンド仕様 |
| [Codex CLI ガイド](cli-guides/codex-cli-session-guide.md) | codex コマンド仕様 |
| [Gemini CLI ガイド](cli-guides/gemini-cli-session-guide.md) | gemini コマンド仕様 |
