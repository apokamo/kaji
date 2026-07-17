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

上記は workflow 実行に必要な最小構成。`.kaji/config.toml` / overlay の全 section / key 仕様
（型 / 既定 / 検証規則 / overlay merge）の正本は
[設定リファレンス](../reference/configuration.md) を参照。

> **`artifacts_dir` の解決基準** (Issue #177): 相対パス指定の場合、`kaji run` は main worktree（`provider.<type>.default_branch` を checkout している worktree）基準で解決する。feature worktree 内で `kaji run` を実行しても artifacts/log は main worktree 配下に集約され、`git worktree remove` でログが消えない。絶対パス指定はそのまま。

> **runner backend は repository config の責務** (Issue #224 / #230): agent step を headless CLI で起動するか tmux pane 上の対話 CLI で起動するかは `[execution] agent_runner`（または `kaji run --agent-runner`）で選ぶ。これは **repository config / 実行時 option の責務**であり、workflow YAML の step 定義には書かない。step は backend 非依存に保つ。詳細は [Interactive Terminal Runner ガイド](../cli-guides/interactive-terminal-runner.md)。

## ファイル配置

```
.kaji/wf/
  dev.yaml
  docs.yaml
```

## 全体構造

```yaml
name: dev                          # 必須: ワークフロー名
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
# OK: exec_script frontmatter を持つ skill は agent 省略可
- id: summarize-artifacts
  skill: summarize-artifacts
  on:
    PASS: close
    ABORT: end
```

`review-poll` は exec_script skill として単体起動・再利用も可能だが、builtin workflow
（`dev` / `dev-thorough` / `docs`）では `exec: [kaji, pr, review-poll]` で installed
`kaji` CLI 経由（`kaji pr review-poll` → `review_poll_entry`）に dispatch する。
builtin workflow の dispatch 正本は workflow YAML 側の `exec` step である。

検証層 (`docs/dev/skill-authoring.md` の exec_script 仕様と整合):

| 層 | 場所 | 内容 |
|----|------|------|
| L1: YAML parse/schema | `kaji_harness/workflow.py` | YAML parse、型、必須、排他、許容値を検証 |
| L2: workflow 参照整合 | `kaji_harness/workflow.py` | step ID の一意性、step/cycle の遷移先、resume、verdict、到達可能性など workflow 内の参照を検証 |
| L3: skill metadata | `kaji_harness/preflight.py` | skill の存在・frontmatter と `agent` 省略条件を検証。`exec_script` と agent/model/effort の併記は warning |

`preflight_workflow_path()` が L1→L2→L3、`preflight_workflow()` がロード済み workflow の
L2→L3 を共通実装する。`kaji validate`、`kaji run`、`kaji recover`、series の各入口はこの
preflight を使用するため、入口ごとに検証規則を重複実装しない。


### exec-step（script step）

step は `skill` の代わりに `exec` を持つことができる。`exec:` step は **skill レイヤを
介さず、宣言された command を直接 subprocess 実行する** 決定論 step である（Issue #205）。
ad-hoc な metrics 収集 / artifact dump / 外部 CLI 呼び出しのような「その workflow に閉じた
決定論処理」を、SKILL.md ファイルを 1 枚も増やさずに workflow.yaml だけで宣言できる。

```yaml
# 文字列形式（shlex.split で argv に分解）
- id: collect-metrics
  exec: python -m kaji_harness.scripts.collect_metrics
  timeout: 120
  on: { PASS: end, ABORT: end }

# リスト形式（引数にスペースや特殊文字を含む場合に安全）
- id: artifact-dump
  exec: ["python", "-m", "kaji_harness.scripts.dump_artifacts", "--issue", "205"]
  on: { PASS: end }
```

exec-step の規約:

- **相互排他**: `skill` と同時指定不可。step は skill-step か exec-step のいずれか 1 つ。
- **agent 専用フィールド禁止**: `agent` / `model` / `effort` / `resume` / `inject_verdict` /
  `max_budget_usd` は指定できない（exec-step は LLM を呼ばないため無意味）。指定すると
  `WorkflowValidationError`。許容するのは `id` / `exec` / `timeout` / `workdir` / `on`。
- **可読性の不変条件**: exec-step は `agent` を持てないため、**`agent` の有無 = LLM コスト発生の
  有無** という不変条件が workflow.yaml 単独で成立する。
- **実行**: `shell=False` で argv を起動（シェルメタ文字の展開・injection は構造的に発生しない）。
  cwd は workdir 解決規則（step.workdir → workflow.workdir → project_root）に従う。
- **context env**: `exec_script` skill と同じ `KAJI_*` 環境変数（`KAJI_ISSUE_ID` /
  `KAJI_STEP_ID` / `KAJI_WORKTREE_DIR` / `KAJI_VERDICT_PATH` 等）を注入する。script は
  `KAJI_VERDICT_PATH` に `verdict.yaml` を書く artifact-primary 経路で完了判定できる。
- **verdict 解決**: artifact → comment → stdout の順。決定論 step のため **AI formatter
  fallback を呼ばない**（fabrication 防止）。
- **fail-loud**: subprocess の exit code != 0 は `ScriptExecutionError`、timeout 超過は
  `StepTimeoutError`。runner が ABORT verdict を attempt に記録して元例外を re-raise する
  （stdout の verdict 有無を問わない）。
- **検証層**: 排他・型・必須は parse（`load_workflow`）と `validate_workflow` の双方で
  fail-fast。`kaji validate` / runner preflight は exec-step の skill 解決を skip する
  （skill ファイル不要のため）。

#### `exec:` と `exec_script:` の使い分け

`exec_script:`（SKILL.md frontmatter・Issue #204）と `exec:`（step フィールド・本機能）は
**共存** する。どちらも決定論 subprocess dispatch だが用途が異なる。

| 観点 | `exec:`（step フィールド） | `exec_script:`（skill frontmatter） |
|------|----------------------------|-------------------------------------|
| 宣言場所 | workflow.yaml の step | SKILL.md frontmatter（step は `skill:` で参照） |
| skill ファイル | 不要 | 必須 |
| 実行コマンド | 任意 argv（`shell=False`） | `python -m <module>` 固定（Python dotted-path 制限） |
| 再利用性 | workflow ローカル（その workflow 専用） | named skill として複数 workflow から共有可能 |
| ドキュメント面 | workflow.yaml のコメントのみ | SKILL.md に背景・使い方を記述できる |

- **`exec:` を推奨**: ad-hoc / inline / その workflow に閉じた決定論 step。metrics 収集・
  artifact dump・外部（非 Python）CLI 呼び出しなど、再利用も詳細ドキュメントも要らないもの。
- **`exec_script:` を推奨**: named・再利用・ドキュメント価値のある決定論 skill。Python module
  限定の安全制約も活きる。例外として、`review-poll` は SKILL.md に単体起動・再利用契約を
  残しつつ、builtin workflow では `exec` step で entry module を直接起動する。


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
| `id` | str | ✅ | ステップ ID。英数字とハイフン。workflow 内で一意でなければならない（重複は `WorkflowValidationError`） |
| `skill` | str | △ | スキル名（`<agent>.skills/<name>` のルックアップキー）。`exec` と相互排他（後述「skill-step と exec-step」） |
| `exec` | str \| list[str] | △ | 直接実行する command/argv。`skill` と相互排他。後述「exec-step（script step）」 |
| `agent` | str | △ | `claude` / `codex` / `gemini`。skill-step（非 exec_script）で必須。exec-step / exec_script skill では指定不可 / 省略可 |
| `on` | mapping | ✅ | verdict → next step ID のマッピング。非空必須 |
| `model` | str | — | モデル名（省略時は agent デフォルト）。exec-step では指定不可 |
| `effort` | str | — | エージェント別の許容値で書く。後述「effort 値」参照。exec-step では指定不可 |
| `max_budget_usd` | float | — | コスト上限（USD）。exec-step では指定不可 |
| `timeout` | int | — | タイムアウト（秒）。フォールバック: step.timeout → workflow.default_timeout → config.execution.default_timeout |
| `workdir` | str | — | 作業ディレクトリ（絶対パス）。フォールバック: step.workdir → workflow.workdir → project_root |
| `resume` | str | — | resume するステップ ID（同一 agent のセッション継続）。exec-step では指定不可 |

> **`skill` / `exec` の `△`（どちらか 1 つ必須）**: step は `skill` を持つ **skill-step** か
> `exec` を持つ **exec-step** の **ちょうど 1 つ** でなければならない。両方指定 / 両方欠落は
> `WorkflowValidationError`（parse / `validate_workflow` の双方で fail-fast）。

`agent` を指定する場合は `claude` / `codex` / `gemini` のいずれかでなければならない。
model 名は agent ごとに変化するため enum 検証の対象外とする。

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

全 step で `PASS` キーが必須。値は **ステップ ID** か **`end`** を指定する。
agent-backed skill、exec_script skill、direct exec のいずれも成功時に `PASS` を生成し得るため、
step 種別による例外はない。

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

**制約**: `on.RETRY` が自ステップ自身を指す **self-RETRY step**（例: `implement` /
`final-check` の 1-step ループ）は、必ずいずれかの cycle の `loop` に所属し、かつ
その step が `loop` 末尾（1-step cycle なら唯一の要素）でなければならない。runner は
cycle 経由でのみ RETRY 上限を enforce する（increment 条件は
「step が `cycle.loop[-1]` かつ verdict が `RETRY`」）ため、cycle 未所属の self-RETRY
step は `max_iterations` が効かず無限ループしうる。cycle に入れない場合は、その
self-RETRY edge が dead（producer skill が RETRY を返さない）であることを確認して
edge を除去する。この不変条件は `tests/workflows/test_self_retry_cycle_membership.py`
で機械的に検証している。

**`on_exhaust: ABORT` からの復旧**（Issue #189）: `max_iterations` に達すると
`on_exhaust` の verdict（多くは `ABORT`）で run が停止する。`session-state.json` の
`cycle_counts` は run を跨いで永続化されるため、`--from` だけで再実行しても同じ
cycle が即座に再度 exhaust する。人間が Issue 本文や設計書を修正した上で
`kaji run <wf> <issue> --from <cycle 内 step> --reset-cycle` を指定すると、対象
cycle の `cycle_counts` だけを `0` に戻してから再開できる（詳細は「実行コマンド」
節の `--reset-cycle` を参照）。

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
name: dev
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
kaji run .kaji/wf/dev.yaml 57

# 途中から再開（--from で開始ステップ指定）
kaji run .kaji/wf/dev.yaml 57 --from fix-code

# 単発実行（1ステップのみ実行して終了）
kaji run .kaji/wf/dev.yaml 57 --step review-code

# 指定ステップを dispatch する直前で停止（exclusive barrier。指定ステップは実行されない）
kaji run .kaji/wf/dev.yaml 57 --before implement

# `--from` と組み合わせ: 修正ループだけ回す（A から B の手前まで）
kaji run .kaji/wf/dev.yaml 57 --from fix-design --before implement

# cycle exhaust (on_exhaust: ABORT) 後の復旧: 対象 cycle の iteration count を 0 に戻して再開
kaji run .kaji/wf/dev.yaml 57 --from review-code --reset-cycle

# config 探索の起点ディレクトリを指定（YAML workdir とは別物）
kaji run .kaji/wf/dev.yaml 57 --workdir ../kaji-feat-57

# エージェント出力のストリーミング表示を抑制
kaji run .kaji/wf/dev.yaml 57 --quiet
```

**注意**: `--from` と `--step` は排他オプションです（同時指定不可）。`--step` と `--before` も排他です。`--reset-cycle` は `--from` を必須の相棒とし、`--from` なしで指定すると定義エラー（exit 2）になります。

### `--from` / `--step` / `--before` / `--reset-cycle` の使い分け

| フラグ | 意味 | 指定 step は実行される？ | ループ動作 |
|--------|------|--------------------------|-----------|
| `--from <step>` | 開始点を指定（指定 step から実行） | ✅ 実行される | 通常通り |
| `--step <step>` | 単発実行（1 step だけ実行して終了） | ✅ 実行される | しない |
| `--before <step>` | 停止点を指定（dispatch 直前で停止） | ❌ 実行されない | barrier に到達するまで通常通り |
| `--reset-cycle`（`--from` 併用必須） | `--from` の step が属する cycle の `cycle_counts` を `0` に戻してから実行 | `--from` の意味論に従う | リセット後の cycle は通常通り再カウント |

`--from` / `--step` で指定できるのは、workflow の先頭 step を root として `on` 遷移を
辿った canonical graph 上の到達可能 step だけである。部分実行専用の孤立 step を追加 root
として宣言する用途には使えない。

**`--reset-cycle` の意味論**:

- `--from <step>` が指す step の属する cycle を `workflow.find_cycle_for_step()` で解決し、workflow 起動前にその cycle の `cycle_counts[<cycle>]` だけを `0` に戻す。他 cycle の counts / `step_history` / `last_transition_verdict` は変更しない。
- `--from` の step が cycle に属さない linear step の場合は誤用として定義エラー（exit 2）。
- 誤用（`--from` 未指定 / cycle 外 step 指定）の場合、`session-state.json` は一切書き換えられない。

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
kaji validate .kaji/wf/dev.yaml

# 複数ファイルの一括バリデーション
kaji validate .kaji/wf/*.yaml
```

`kaji validate` は YAML の parse/schema だけでなく、step/cycle の参照整合と、設定された
`paths.skill_dir` に対する skill の存在・frontmatter まで L1/L2/L3 を一括検証する。
静的検証では agent enum、全 step の `on.PASS`、先頭 step からの到達可能性も確認する。
cycle の `entry` / `loop` と step の `resume` は実行遷移ではないため、到達可能性の edge には
数えない。違反はすべて `WorkflowValidationError` として集約される。

**出力例**:

```
✓ .kaji/wf/dev.yaml
✗ .kaji/wf/bad.yaml
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
