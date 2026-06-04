# [設計] workflow.yaml に `exec` step type を導入し LLM agent step と script step を schema レベルで分離する

Issue: #205

## 概要

`workflow.yaml` の step スキーマに新しいフィールド `exec:` を追加し、`skill:` と相互排他な「skill レイヤを介さず直接コマンドを subprocess 実行する step」を workflow.yaml 1 箇所で宣言できるようにする。これにより workflow 作成者は ad-hoc な決定論 step を skill ファイルを増やさずに書け、harness 運用者は `agent:` の有無で LLM コストが発生する step を一目で判別できる。

## 背景・目的

### ユースケース

1. **workflow 作成者**として、metrics 収集 / artifact dump / 外部 CLI 呼び出しのような ad-hoc な決定論 step を、SKILL.md ファイルを 1 枚も増やさずに workflow.yaml だけで宣言したい。
   ```yaml
   - id: collect-metrics
     exec: python -m kaji_harness.scripts.collect_metrics
     on: { PASS: next }
   ```
2. **harness 運用者**として、workflow.yaml を読むだけで「この step が LLM を呼ぶか / 単なる subprocess か」を判別したい。`exec:` step は `agent:` を持たない（持てない）ため、`agent:` の有無 = LLM コスト発生の有無、という不変条件が成立する。
3. **skill 開発者**として、純スクリプトに過ぎない skill（Issue #204 で `exec_script` frontmatter を持つもの）を `exec:` step に書き換えて skill ファイルそのものを削除する選択肢を持ちたい。

### 現状の問題

短期対応の Issue #204（PR #207, 2026-05-27 merge 済み）で skill frontmatter の `exec_script: <module>` を導入したが、これは「skill 単位での opt-in」であり以下の限界がある。

1. skill ファイルを必ず 1 枚作る必要がある（中身が `exec_script` 宣言のみでも）。
2. workflow.yaml だけ読んでも「LLM を呼ぶか / subprocess か」が判別できない。判別ロジックが skill frontmatter（`exec_script` の有無）に隠れる。
3. ad-hoc な script step にも skill ファイルが必要になる。

長期的には workflow.yaml に「LLM agent step」と「script step」を明示的に分離した方が、可読性・schema validation・型安全性・コスト管理の全てで利点がある。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| `exec_script` frontmatter のみで運用を続ける | ad-hoc step ごとに skill ファイルが必要になり、上記問題 1〜3 が残る。workflow.yaml 単独での可読性が上がらない |
| `skill:` に特殊値（例: `skill: exec`）を持たせて inline command を別キーで渡す | `skill` の意味を上書きする hack で schema が直感に反する。`agent` 有無での判別不変条件も成立しない |
| step 種別を `type: agent|script` の明示フィールドで切る | フィールドが 1 つ増える割に、`skill`/`exec` の排他で同じ情報が表現できる。冗長 |

採択: **`exec:` を `skill:` と相互排他な step フィールドとして追加する**。step 種別は「`skill:` を持つか `exec:` を持つか」で一意に決まり、追加の判別フィールドは要らない。

## インターフェース

### 入力（workflow.yaml の step スキーマ）

`exec` フィールドを step に追加する。YAML 表層では **文字列形式とリスト形式の両方**を受け付ける。

| 形式 | 例 | 解釈 |
|------|----|------|
| 文字列 | `exec: python -m kaji_harness.scripts.collect_metrics` | `shlex.split()`（POSIX）で argv に分解 |
| リスト | `exec: ["python", "-m", "kaji_harness.scripts.collect_metrics"]` | argv として直接使用 |

#### 排他・必須ルール

step は **`skill:` を持つ skill-step** か **`exec:` を持つ exec-step** のいずれか「ちょうど 1 つ」でなければならない。

| 条件 | 結果 |
|------|------|
| `skill:` も `exec:` も無い | `WorkflowValidationError`（"step must declare exactly one of 'skill' or 'exec'"） |
| `skill:` と `exec:` を同時指定 | `WorkflowValidationError`（相互排他違反） |
| `exec:` と `agent:` を同時指定 | `WorkflowValidationError`（exec-step は agent を持てない。完了条件で明示） |
| `exec:` と agent 専用フィールド（`model` / `effort` / `resume` / `inject_verdict` / `max_budget_usd`）を同時指定 | `WorkflowValidationError`（exec-step では無意味なフィールド） |
| `exec:` が空文字列 / 空リスト / 非文字列要素を含むリスト | `WorkflowValidationError`（argv が空 / 型不正） |

exec-step が許容するフィールド: `id` / `exec` / `timeout` / `workdir` / `on`。

#### Step dataclass の表現

`Step` dataclass に `exec: list[str] | None = None` フィールドを追加し、`skill: str` を `skill: str | None = None` へ緩める。

> **Issue 完了条件の型 `list[str] | str | None` からの意図的なずらし**: dataclass に保持する内部表現は **normalize 後の `list[str] | None`** とする。YAML 表層が受け付ける `str | list[str]` は parse 境界（`_parse_workflow`）で `shlex.split()` を通して `list[str]` に正規化する。理由は (a) subprocess は `shell=False` で起動するため最終的に argv が必須、(b) runner / 各 consumer が `str` と `list` の二形態を毎回分岐せずに済む単一表現、(c) 既存の parse 境界正規化（`workdir` を絶対パス str へ展開する処理）と一貫する。「外部入力は境界で検証・正規化する」という本リポジトリの方針に沿う判断。

> **フィールド名 `exec` について**: builtin 関数 `exec` と同名だが Python 3 では予約語ではなく、dataclass フィールド名 / キーワード引数として合法。本リポジトリの ruff lint select は `["E","F","I","W","B","UP"]` で flake8-builtins（`A` ルール群）を含まないため lint 違反にもならない。完了条件の文言（"`Step` dataclass に `exec` フィールド"）を尊重してフィールド名は `exec` とする。

### 出力・副作用

exec-step は既存の `exec_script` skill step と**同等の dispatch 副作用**を持つ。

- **subprocess 実行**: `shell=False` で argv を起動。cwd は workdir 解決規則（step.workdir → workflow.workdir → project_root）に従う。
- **context env 注入**: `exec_script` 経路と同じ `KAJI_*` 環境変数を注入する。特に `KAJI_VERDICT_PATH`（attempt の `verdict.yaml` パス）を渡し、script が artifact-primary 経路で verdict を書けるようにする。
  - 注入対象: `KAJI_ISSUE_ID` / `KAJI_ISSUE_REF` / `KAJI_STEP_ID` / `KAJI_WORKTREE_DIR` / `KAJI_BRANCH_NAME` / `KAJI_PROVIDER_TYPE` / `KAJI_DEFAULT_BRANCH` / `KAJI_GIT_REMOTE` / `KAJI_VERDICT_PATH`（PR context があれば `KAJI_PR_ID` / `KAJI_PR_REF`）。
- **artifact 保存**: attempt ディレクトリ配下に `stdout.log` / `console.log`（必要に応じて `stderr.log`）を保存。既存 `execute_script` と同方針。
- **verdict 解決**: `resolve_verdict` の artifact → comment → stdout 順。exec-step は決定論 step なので **AI formatter fallback を呼ばない**（`ai_formatter=None`）。fabrication 防止と決定論性維持のため、`exec_script` 経路と完全に同じ扱い。
- **fail-loud**: subprocess の exit code != 0 は `ScriptExecutionError` を送出し、runner が ABORT verdict を attempt に記録して元例外を re-raise する（`exec_script` と同じ正本契約。stdout の verdict 有無を問わない）。
- **ログ表現**: `log_step_start` で `agent` / `model` / `effort` を **null として記録**（LLM 非経路であることを明示）。`result.json` の `dispatch` フィールドは新値 `"exec"` を記録する（既存値: `"agent"` / `"exec_script"`）。cost / session_id は None。

### 使用例

```yaml
# workflow.yaml の一部
steps:
  - id: design
    skill: issue-design      # 従来通りの agent step（LLM 経路）
    agent: claude
    on: { PASS: collect-metrics, ABORT: end }

  - id: collect-metrics      # 新規: exec step（subprocess 経路、skill ファイル不要）
    exec: python -m kaji_harness.scripts.collect_metrics
    timeout: 120
    on: { PASS: end, ABORT: end }
```

```yaml
  - id: artifact-dump        # リスト形式（引数にスペースや特殊文字を含む場合に安全）
    exec: ["python", "-m", "kaji_harness.scripts.dump_artifacts", "--issue", "205"]
    on: { PASS: end }
```

### エラー

| 失敗 | 戻り / 例外 |
|------|-------------|
| parse / validate 時の排他・型違反 | `WorkflowValidationError`（`kaji validate` / `kaji run` 起動時に fail-fast） |
| subprocess の non-zero exit | `ScriptExecutionError` → runner が ABORT verdict を attempt に記録し re-raise |
| timeout 超過 | `StepTimeoutError` → 同上 |
| verdict 不在（stdout にも `verdict.yaml` にも verdict が無い） | `VerdictNotFound` → 同上（exec は formatter fallback を呼ばないため fabrication しない） |

## 制約・前提条件

- **依存**: 新規外部ライブラリは追加しない。標準ライブラリ `subprocess`（`shell=False`）・`shlex` のみ。
- **後方互換**: 既存の `skill:` step（agent 経路 / `exec_script` 経路の双方）は完全に従来通り動作する。`skill` を optional 化しても、`skill:` を持つ step の挙動は不変。
- **信頼境界**: `exec:` は argv を任意指定できる。workflow.yaml は **リポジトリ管理下の信頼された artifact**（Makefile / CI 設定と同格）であり、これを編集できる主体は既に任意の skill / agent を起動できる。したがって任意 argv の追加は信頼面を実質的に広げない。`shell=False` 固定によりシェルメタ文字の展開・injection は構造的に発生しない。`exec_script` の Python dotted-path 制限（`_EXEC_SCRIPT_RE`）は `python -m <module>` 形式専用の制約であり、汎用 argv を扱う `exec:` には適用しない（代わりに「非空 argv かつ全要素が非空 str」を検証する）。
- **Issue #204 との順序**: #204（`exec_script`）は merge 済み（PR #207）。本 Issue はその上に積む。両者の意味的重複の整理は本設計の「§ exec_script との関係整理」で行う。
- `kaji validate` は config 非依存。exec-step の排他・型検証は parse / validate 層で完結し、skill / provider 解決を必要としない。

## 変更スコープ

| ファイル | 変更内容 |
|----------|---------|
| `kaji_harness/models.py` | `Step` に `exec: list[str] | None` を追加。`skill: str` → `skill: str | None = None` |
| `kaji_harness/workflow.py` | `_parse_workflow`: `exec` の受理・正規化（str→argv）・排他検証。`_STEP_REQUIRED_KEYS` から `skill` を外し「exactly one of skill/exec」検証を追加。`validate_workflow`: 排他のミラー検証（parser を経由しない Workflow オブジェクト用） |
| `kaji_harness/script_exec.py` | subprocess 実行コアを private helper に抽出し、`execute_script`（既存、`python -m <module>`）と新規 `execute_exec`（argv 直指定）で共有 |
| `kaji_harness/runner.py` | Step 0 preflight: exec-step では skill 解決を skip。L2 preflight: agent 省略検証を exec-step では bypass。main loop: dispatch を 3 分岐（agent / exec_script / exec）に拡張。exec dispatch では context env + `execute_exec` を呼び、formatter=None で verdict 解決。dispatch_label に `"exec"` を追加 |
| `docs/dev/workflow-authoring.md` / `docs/dev/workflow_guide.md` | step フィールド表・必須条件・exec vs exec_script の使い分けを追記 |
| `tests/` | exec dispatch / verdict parse / 排他 validation / 後方互換の 4 観点を S/M(+L) で網羅 |

kaji は Python 単一スタック。backend / frontend の Scope 分岐はない。

## 方針（Minimal How）

### 1. models.py

`Step` に `exec: list[str] | None = None` を追加。`skill` を `skill: str | None = None` に緩める（`id` のみ必須、他は default 付き。dataclass のフィールド順序制約を満たす）。

### 2. workflow.py（parse / validate）

`_parse_workflow` の step ループ:

```python
raw_skill = step_data.get("skill")
raw_exec = step_data.get("exec")
raw_agent = step_data.get("agent")

# exactly one of skill / exec
if (raw_skill is None) == (raw_exec is None):
    raise WorkflowValidationError(
        f"Step '{sid}' must declare exactly one of 'skill' or 'exec'")

if raw_exec is not None:
    # exec-step: agent 専用フィールドの同時指定を拒否
    for forbidden in ("agent", "model", "effort", "resume", "inject_verdict", "max_budget_usd"):
        if forbidden in step_data:
            raise WorkflowValidationError(
                f"Step '{sid}' with 'exec' must not set '{forbidden}'")
    exec_argv = _normalize_exec(raw_exec, sid)  # str -> shlex.split, list -> そのまま
    # exec_argv: 非空 list[str]、全要素 非空 str を検証
else:
    exec_argv = None
    # 既存の skill-step 検証（_STEP_REQUIRED_KEYS から skill は外す）
```

`_normalize_exec(value, step_id)`:
- `str` → `shlex.split(value)`。空なら error。
- `list` → 全要素が非空 `str` であることを検証。空なら error。
- それ以外の型 → error。

`validate_workflow` に、上記排他（skill/exec の exactly-one、exec+agent 等）の **ミラー検証**を追加する（既存の timeout/workdir が parse と validate の双方で検証されるのと同じ defense-in-depth）。

### 3. script_exec.py（subprocess コアの共有）

既存 `execute_script` の subprocess 起動 / timeout / stdout・stderr ドレイン / ログ書き出し本体を private helper `_run_argv(*, step, args, env, workdir, log_dir, timeout, verbose, command_label)` に抽出する。

- `execute_script`: `args = [sys.executable, "-m", module]`、`command_label = module` で `_run_argv` を呼ぶ（外部挙動不変）。
- `execute_exec(*, step, argv, env, workdir, log_dir, timeout, verbose)`: `args = argv`、`command_label = " ".join(argv)` で `_run_argv` を呼ぶ。
- non-zero exit は `ScriptExecutionError(step.id, command_label, returncode, stderr)`。timeout は `StepTimeoutError`。

> これは新機能追加のための最小限の内部抽出であり、独立した refactor Issue ではない（subprocess/timeout/logging ロジックの重複を避けるための feat 内変更）。

### 4. runner.py（dispatch 3 分岐）

**Step 0 preflight**: step ループで exec-step は skill 解決を skip し、`skill_metadata[step.id] = None` を入れる。skill-step のみ `validate_skill_exists` / `load_skill_metadata` を呼び、既存の L2 検証（agent 省略は exec_script 必須）を適用する。

```python
for step in self.workflow.steps:
    if step.exec is not None:
        skill_metadata[step.id] = None      # exec dispatch
        continue
    validate_skill_exists(step.skill, ...)
    metadata = load_skill_metadata(step.skill, ...)
    skill_metadata[step.id] = metadata
    if step.agent is None and metadata.exec_script is None:
        raise WorkflowValidationError(...)   # 既存
    if metadata.exec_script is not None and (step.agent or step.model or step.effort):
        sys.stderr.write("WARNING: ... ignored")  # 既存
```

**main loop**: dispatch 種別を 3 値で確定する。

```python
step_metadata = skill_metadata[current_step.id]   # exec では None
if current_step.exec is not None:
    dispatch_kind = "exec"
elif step_metadata.exec_script is not None:
    dispatch_kind = "exec_script"
else:
    dispatch_kind = "agent"
is_script_like = dispatch_kind in ("exec", "exec_script")  # null agent fields / formatter=None / cost None を共有
dispatch_label = dispatch_kind
```

dispatch 本体:

```python
if dispatch_kind == "exec":
    context_env = { ...KAJI_* 一式..., "KAJI_VERDICT_PATH": str(verdict_yaml_path) }
    attempt_started_at = datetime.now(UTC)
    result = execute_exec(step=current_step, argv=current_step.exec,
                          env=context_env, workdir=effective_workdir,
                          log_dir=attempt_dir, timeout=resolved_timeout,
                          verbose=self.verbose)
elif dispatch_kind == "exec_script":
    ...既存...
else:
    ...既存 agent (execute_cli / execute_interactive_terminal)...
```

verdict 解決は `is_script_like` のとき `formatter = None`（AI formatter を呼ばない）。`log_step_start` の agent/model/effort は `is_script_like` のとき null。これらは既存の `is_exec_script` 分岐を `is_script_like` に一般化することで共有する。

## exec_script との関係整理（完了条件: 共存 / deprecate / 推奨用途）

**決定: 両者を共存させる。本 Issue では `exec_script` を deprecate しない。**

| 観点 | `exec:`（step フィールド・本 Issue） | `exec_script:`（skill frontmatter・#204） |
|------|----------------------------------------|---------------------------------------------|
| 宣言場所 | workflow.yaml の step | SKILL.md frontmatter（step は `skill:` で参照） |
| skill ファイル | 不要 | 必須 |
| 実行コマンド | 任意 argv（`shell=False`） | `python -m <module>` 固定（Python dotted-path に制限） |
| 再利用性 | workflow ローカル（その workflow 専用） | named skill として複数 workflow から共有可能 |
| ドキュメント面 | workflow.yaml のコメントのみ | SKILL.md に背景・使い方を記述できる |
| 可読性 | workflow.yaml 単独で subprocess と判別可 | skill frontmatter を見ないと判別不可 |

### 推奨用途（使い分けの境界）

- **`exec:` を推奨**: ad-hoc / inline / その workflow に閉じた決定論 step。metrics 収集・artifact dump・外部（非 Python）CLI 呼び出しなど、再利用も詳細ドキュメントも要らないもの。
- **`exec_script:` を推奨**: named・再利用・ドキュメント価値のある決定論 skill。典型例は `review-poll`（SKILL.md に挙動を詳述し、`full-cycle.yaml` / `full-cycle-xhigh.yaml` / `review-close.yaml` / `review-cycle.yaml` の 4 workflow から参照される）。Python module 限定の安全制約も活きる。

### 移行方針

純スクリプト skill を `exec:` step に書き換えて skill ファイルを削除する選択肢は提供する（ユースケース 3）。ただし **既存 `review-poll` の移行は本 Issue のスコープ外**。理由:

1. `review-poll` は 4 つの workflow から参照されており、移行は測定可能な改善指標を持たない内部構造変更 = refactor に該当し、本 feat Issue のスコープ境界（refactor は別 Issue）から外れる。
2. `exec_script` は #204/#207 で merge されたばかりで、`review-poll` が現役で使用中。deprecate は動作中コードを churn させるだけで定量的便益が無い。
3. `exec_script` は (a) Python module 限定の安全制約、(b) SKILL.md によるドキュメント面、という `exec:` が汎用性と引き換えに手放した価値を提供しており、両者は競合ではなく補完関係。

→ 必要なら別途 refactor Issue（`review-poll` 移行 or `exec_script` deprecation 検討）として追跡する。本 Issue では両機構を共存させ、使い分け境界を `docs/dev/workflow-authoring.md` に記録する。

## テスト戦略

> **CRITICAL**: 本変更は実行時の振る舞いを変えるコード変更（parser / validator / runner dispatch / subprocess 実行）。`docs/dev/testing-convention.md` のサイズ定義に従い Small / Medium / Large の観点を定義する。

### 変更タイプ

実行時コード変更（恒久回帰テストが原則必要）。

### Small テスト（外部依存なし・純ロジック / バリデーション）

- **parser 正規化**: `exec: "python -m foo"`（str）→ `["python", "-m", "foo"]` に shlex 分解される / `exec: ["python","-m","foo"]`（list）→ そのまま保持される。
- **排他検証**: `skill` も `exec` も無い → error / `skill` と `exec` 同時 → error / `exec` と `agent` 同時 → error / `exec` と `model`/`effort`/`resume`/`inject_verdict`/`max_budget_usd` 同時 → 各 error。
- **型・空検証**: `exec: ""` / `exec: []` / `exec: [123]`（非 str 要素）/ `exec: 42`（非 str/list）→ 各 error。
- **Step dataclass**: `exec` の default は None / `skill` のみの step が従来通り構築できる（後方互換）。
- **validate_workflow ミラー**: parser を経由せず手組みした `Workflow`（exec+skill 併存 / exec+agent 併存）で排他 error が出る。

### Medium テスト（subprocess / ファイル I/O / runner 結合）

- **`execute_exec` 実行**: 実 subprocess（例: `python -c "print('---VERDICT---'); ..."` 相当の最小 module/コマンド）を起動し、stdout が `stdout.log` / `console.log` に保存され `CLIResult.full_output` に蓄積される。
- **fail-loud parity**: exec の non-zero exit → `ScriptExecutionError`（stdout に verdict があっても）/ timeout → `StepTimeoutError`。
- **runner dispatch（exec）**: exec-step が `execute_exec` にルーティングされ、verdict が stdout の `---VERDICT---` から解決される。**AI formatter が呼ばれない**（決定論性）。
- **artifact-primary verdict**: exec script が `KAJI_VERDICT_PATH` に `verdict.yaml` を書くと artifact 経路で解決され、stdout より優先される。
- **副作用 parity**: exec-step で `log_step_start` の agent/model/effort が null、cost/session_id が None、`result.json` の `dispatch` が `"exec"`。
- **後方互換（回帰）**: 既存の agent step・`exec_script` step を含む workflow が従来通り dispatch される（dispatch 種別が混在しても破綻しない）。

### Large テスト（large_local: 実 subprocess・ネットワーク無し E2E）

- **`kaji run` E2E**: exec-step を 1 つ含む最小 workflow.yaml を実際に `kaji run` し、subprocess 起動 → verdict 解決 → attempt 配下の artifact レイアウト（`stdout.log` / `verdict.yaml` / `result.json`）が生成されることを確認する。`test_exec_script_subprocess_large.py` と同じ `large_local`（subprocess あり・network 無し）方針。
  - **追加する理由**: `exec` は新しい dispatch entrypoint であり、CLI wiring・config 探索・artifact レイアウトの結合は Medium の runner 単体テストでは完全には覆えない。`exec_script` 用 Large と同種の回帰シグナルを `exec` でも確保する。

### 省略判断

S/M/L いずれも省略しない。実行時コード変更であり、`testing-convention.md` の「省略してよい 4 条件」（独自ロジック無し・既存ゲートで捕捉済み・回帰情報が増えない・レビュー可能な省略理由）をいずれも満たさない（parser 正規化・排他検証・新 dispatch 経路は新規ロジックそのもの）。

## 影響ドキュメント

| ドキュメント | 影響 | 理由 |
|-------------|------|------|
| `docs/dev/workflow-authoring.md` | あり | step フィールド表に `exec` を追加。`skill`/`agent` の必須列を「skill/exec のどちらか必須」に修正。exec-step の宣言方法と exec vs exec_script の使い分け境界を追記（完了条件） |
| `docs/dev/workflow_guide.md` | あり | step 種別の説明に script step（exec）を追加（完了条件） |
| `docs/dev/skill-authoring.md` | あり（軽微） | `exec_script` 仕様の節に「inline 用途は workflow.yaml の `exec:` を検討」というクロスリファレンスを追加 |
| `docs/dev/workflow_overview.md` | 要評価 | agent step / script step の概念区分に触れている場合は追記 |
| `docs/ARCHITECTURE.md` | 要評価 | dispatch 経路（agent / exec_script / exec の 3 分岐）を図示・記述している箇所があれば更新 |
| `docs/adr/` | なし | 新規ライブラリ / プロトコルの技術選定は無い。exec_script との共存方針は本設計書 + workflow-authoring.md に記録するため ADR は必須としない（必要なら別途 short ADR を追加可能） |
| `CLAUDE.md` | なし | 新規 doc ファイル追加なし。既存 index で workflow authoring は参照済み |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Python `subprocess` security considerations | https://docs.python.org/3/library/subprocess.html#security-considerations | "If shell is True, ... it can be a security hazard if combined with untrusted input." → `exec:` は `shell=False` 固定で argv を渡し、シェル経由の injection を構造的に排除する設計根拠 |
| Python `shlex.split` | https://docs.python.org/3/library/shlex.html#shlex.split | "Split the string s using shell-like syntax." → 文字列形式 `exec:` を POSIX シェル風に argv へ分解する正規化の一次根拠 |
| Issue #204（`exec_script` frontmatter）| https://github.com/apokamo/kaji/issues/204 / PR #207（merge 済み）| `exec_script` の dispatch・verdict・fail-loud 契約。本 Issue はこの上に step レベルの `exec:` を積み、両者の関係を整理する前提 |
| 既存実装 `kaji_harness/script_exec.py` | `kaji_harness/script_exec.py`（in-repo）| subprocess 起動（`shell=False`）・timeout・stdout/stderr ドレイン・ログ書き出しの正本。`execute_exec` はこのコアを共有する |
| 既存実装 `kaji_harness/runner.py` | `kaji_harness/runner.py:510-703`（in-repo）| `is_exec_script` 分岐・context env 注入・`resolve_verdict(formatter=None)` の正本。exec dispatch はこれを 3 分岐へ一般化する |
| 既存実装 `kaji_harness/workflow.py` | `kaji_harness/workflow.py:56-185`（in-repo）| `_STEP_REQUIRED_KEYS` / step parse / 既存フィールド検証の正本。exec 排他・正規化を追加する場所 |
| 既存実装 `kaji_harness/models.py` | `kaji_harness/models.py:46-60`（in-repo）| `Step` dataclass。`exec` 追加と `skill` optional 化の対象 |
| 既存実装 `kaji_harness/skill.py` | `kaji_harness/skill.py:14-16`（in-repo）| `_EXEC_SCRIPT_RE`（Python dotted-path 制限）。`exec:` には適用しない（汎用 argv のため）という設計境界の根拠 |
| テスト規約 | `docs/dev/testing-convention.md`（in-repo）| S/M/L 定義と省略 4 条件。本設計のテスト戦略の根拠 |
