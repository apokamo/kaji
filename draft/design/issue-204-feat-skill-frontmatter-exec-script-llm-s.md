# [設計] skill frontmatter `exec_script` による LLM 中継なし script step

Issue: #204

## 概要

`kaji_harness` の skill 実行レイヤに `exec_script: <python.module.path>` frontmatter を追加し、LLM の判断を必要としない決定論的 skill を agent spawn なしで直接 `python -m <module>` として実行できるようにする。検証 lane として `review-poll` skill を本機構へ移行する。

## 背景・目的

### ユースケース

- **skill 開発者**として、決定論的に script だけで完結する skill を書くために、SKILL.md の frontmatter に `exec_script: <module>` を宣言するだけで agent spawn を skip させたい。
- **workflow 作成者**として、`review-poll` のような純スクリプト型 step を LLM 経由の中継ロスなく実行したい。
- **harness 運用者**として、verdict を subprocess の stdout から直接 parse し、agent の誤動作（background 起動・ターン早期終了）に起因する `VerdictNotFound` ERROR を排除したい。

### 現状の問題（直接の発端）

`review-poll` skill は実質「`python -m kaji_harness.scripts.codex_review_poll` を起動して stdout の verdict を中継するだけ」の薄い bash wrapper だが、現状の `Step` は `agent: str` を必須とする（`kaji_harness/models.py:46`）。このため polling の中継のためだけに sonnet エージェントを起動しており、agent が polling script を background 起動して即ターン終了すると stdout に `---VERDICT---` ブロックが乗らず、harness の fail-safe（`VerdictNotFound`、Issue #193 の fabrication 防止経路、`kaji_harness/verdict.py:294-298`）で `workflow_end status=ERROR` となる。

- 一次根拠: `.kaji-artifacts/196/runs/2605272327/run.log` 最終 event
  - `workflow_end status=ERROR error=VerdictNotFound: No verdict delimiter found in output. Step 3 (AI formatter) skipped to prevent fabrication. ... polling を起動しました。完了通知を待ちます。`
- 該当 stdout: `.kaji-artifacts/196/runs/2605272327/review-poll/stdout.log`

LLM 判断を必要としない決定論的 step に agent を必須とする構造は (a) 単一障害点、(b) 不要な LLM コスト、(c) 実行時間ブレ の 3 重損失を生む。

### 代替案と不採用理由

| 案 | 内容 | 不採用理由 |
|----|------|-----------|
| A. workflow.yaml に新 step type `exec:` を導入 | step スキーマに `type: exec` を増やす | 別 Issue（Issue 本文「将来の発展: C 案」）として起票予定。skill 単位の再利用ができず、workflow ごとに重複定義が増える |
| B. agent prompt に「`background=False` 強制」追記 | LLM 側に regulate を依存 | 単一障害点（LLM 側の遵守失敗で再発）。不要なコスト・時間は解消しない |
| **C. skill frontmatter に `exec_script` 追加（本案）** | skill 定義側で dispatch 方法を宣言 | skill 単位で再利用可能、後方互換、決定論的 |

C 案を採用する根拠: 「LLM 介在の有無」は **skill の性質**（review-poll は仕様上 LLM 不要、issue-design は仕様上 LLM 必須）であり、workflow ごとに毎回宣言するより skill 定義側に持つ方が DRY。

## インターフェース

### 入力

#### SKILL.md frontmatter（拡張）

```yaml
---
name: review-poll
description: codex auto-review polling
exec_script: kaji_harness.scripts.review_poll_entry
---
```

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `name` | str | 既存 | skill 名 |
| `description` | str | 既存 | skill 説明 |
| `exec_script` | str | 任意（新規） | Python module path。`python -m <value>` で実行される。設定時、step の `agent` / `model` / `effort` は無視される |

`exec_script` の値の制約:
- Python identifier の `.` 区切り表記（`[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*`）
- 上記正規表現に違反する値（path traversal `..`、絶対パス `/`、shell metachar 等）は `SkillFrontmatterError` で reject

#### workflow YAML（互換変更）

`Step.agent` を `str | None` に緩和する。`exec_script` を持つ skill を呼び出す step では `agent` / `model` / `effort` を省略できる。

```yaml
# Before
- id: review-poll
  skill: review-poll
  agent: claude
  model: sonnet
  effort: medium
  on:
    PASS: close
    RETRY: pr-fix
    BACK_FALLBACK: review
    ABORT: end

# After
- id: review-poll
  skill: review-poll
  on:
    PASS: close
    RETRY: pr-fix
    BACK_FALLBACK: review
    ABORT: end
```

`agent` 未指定 step が `exec_script` を持たない skill を参照していたら、`workflow load` 時に `WorkflowValidationError`（fail-fast）。

### 出力

subprocess の stdout に `---VERDICT---` ブロックを出力する責務は **script 側にある**（既存 `codex_review_poll.py` の `emit_verdict()` と同型）。harness は subprocess の stdout を従来の `parse_verdict()` に渡す。

副作用:
- `run.log` に `step_start` / `step_end` event は従来通り出る。`agent` / `model` / `effort` は null（exec_script 経路を識別できるよう、event に `dispatch: "exec_script"` フィールドを追加する）
- `<run_dir>/<step_id>/stdout.log` / `stderr.log` を従来通り書き出す
- `cost` は null（LLM 起動なし）

### 使用例

```python
# kaji_harness/runner.py 内（疑似コード）
metadata = load_skill_metadata(step.skill, project_root, skill_dir)
if metadata.exec_script is not None:
    result = execute_script(
        step=step,
        module=metadata.exec_script,
        context=context_env,  # KAJI_ISSUE_ID, KAJI_PR_ID 等を env として渡す
        workdir=effective_workdir,
        log_dir=step_log_dir,
        timeout=resolved_timeout,
    )
else:
    result = execute_cli(step=step, prompt=prompt, ...)  # 既存経路

verdict = parse_verdict(
    result.full_output,
    valid_statuses=valid,
    ai_formatter=None if metadata.exec_script else formatter,  # exec_script は fail-loud
)
```

### エラー

| 失敗シナリオ | 挙動 |
|-------------|------|
| `exec_script` の値が identifier 規約違反（`..` / `/` / shell metachar） | `SkillFrontmatterError`（skill load 時、`validate_skill_exists` 拡張位置で fail-fast） |
| `python -m <module>` が `ModuleNotFoundError` で exit | `CLIExecutionError` 同型の `ScriptExecutionError` を raise（returncode + stderr を保持） |
| subprocess timeout | 既存 `StepTimeoutError` を流用 |
| subprocess は exit 0 だが stdout に verdict delimiter なし | `VerdictNotFound`（既存）。`exec_script` 経路では **AI formatter fallback を呼ばない**（fabrication 防止と決定論性の維持） |
| subprocess が non-zero exit、かつ stdout に有効な verdict あり | verdict を採用（既存 `_execute_cli_once` の terminal-event優先と同精神。script 側が「FAIL 状態を verdict で表現」できるようにする） |
| subprocess が non-zero exit、かつ stdout に verdict なし | `ScriptExecutionError` を raise（stderr を detail に含める） |
| skill が `agent` 未指定なのに `exec_script` も持たない | workflow load 時に `WorkflowValidationError` |

## 制約・前提条件

- **互換性**: `exec_script` 不在の skill は従来通り agent 経由で動作。既存 workflow / skill に変更不要。
- **依存**: subprocess 実行は `python` 単体で完結する。`python` パスは `sys.executable` を使用（`.venv/bin/python` 等を継承）。
- **セキュリティ**: `exec_script` の値は Python identifier 形式に正規表現で限定し、shell injection / path traversal を構文段階で遮断。`subprocess.run([...], shell=False)` で実行。
- **タイムアウト**: 既存の解決順序を流用（`step.timeout` → `workflow.default_timeout` → `config.execution.default_timeout`）。
- **stdout サイズ**: 既存 `execute_cli` 同様、stream 読み取り（`subprocess.Popen` + line iteration）で大量出力 OOM を防ぐ。
- **`review-poll` 自身の polling ロジック非変更**: `kaji_harness/scripts/codex_review_poll.py` の `classify` / `run_polling` / `emit_verdict` / timeout 定数は変更しない。新 entry module `kaji_harness/scripts/review_poll_entry.py` を追加し、env→argv 変換のみ担当させる。

## 変更スコープ

| 対象 | 種別 | 変更内容 |
|------|------|----------|
| `kaji_harness/models.py` | 変更 | `Step.agent: str` → `Step.agent: str | None` |
| `kaji_harness/skill.py` | 変更 | `load_skill_metadata()` 関数追加（frontmatter YAML 解析）。`SkillMetadata` dataclass 追加。`SkillFrontmatterError` を `errors.py` に追加 |
| `kaji_harness/script_exec.py` | 新規 | `execute_script()` 関数（subprocess dispatch、env 注入、stdout streaming、log 書き出し） |
| `kaji_harness/runner.py` | 変更 | `run()` の dispatch 分岐: skill metadata の `exec_script` 有無で `execute_cli` / `execute_script` を切替 |
| `kaji_harness/workflow.py` | 変更 | `_STEP_REQUIRED_KEYS` から `agent` を除外し、`validate_workflow()` で skill metadata と組み合わせた fail-fast を追加 |
| `kaji_harness/logger.py` | 変更 | `log_step_start` / `log_step_end` に `dispatch` field（`"agent"` / `"exec_script"`）を追加 |
| `kaji_harness/scripts/review_poll_entry.py` | 新規 | env (`KAJI_ISSUE_ID` / `KAJI_PR_ID` / `KAJI_GIT_REMOTE` / `KAJI_WORKTREE_DIR`) から PR 情報を resolve し `codex_review_poll.main()` を呼ぶ |
| `.claude/skills/review-poll/SKILL.md` | 変更 | frontmatter に `exec_script` 追加。Step 0〜4 の bash wrapper 説明を削除し、env 仕様を記述 |
| `.kaji/wf/full-cycle.yaml` | 変更 | `review-poll` step から `agent` / `model` / `effort` を削除 |
| `docs/dev/skill-authoring.md` | 変更 | `exec_script` frontmatter 仕様を追記 |
| `docs/dev/workflow-authoring.md` | 変更 | `agent` 任意化条件（exec_script skill のみ）を追記 |
| `tests/` | 新規 | 後述 |

`kaji_harness/scripts/codex_review_poll.py` は **本 Issue では変更しない**（Issue 本文「polling ロジックには変更を加えない」遵守）。

## 方針（Minimal How）

### 1. SkillMetadata と frontmatter parser

```python
# kaji_harness/skill.py
@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    exec_script: str | None  # Python module path or None

_EXEC_SCRIPT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

def load_skill_metadata(skill_name, workdir, skill_dir) -> SkillMetadata:
    # 1. validate_skill_exists で path safety check（既存）
    # 2. SKILL.md 冒頭の `---\n...\n---` ブロックを抽出
    # 3. yaml.safe_load で dict 化
    # 4. exec_script があれば _EXEC_SCRIPT_RE で validate
    # 5. SkillMetadata を返す
```

frontmatter parser は `re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)` で先頭ブロックを切り出す（既存 markdown frontmatter 慣例と一致）。

### 2. execute_script

```python
# kaji_harness/script_exec.py
def execute_script(
    *, step, module, env, workdir, log_dir, timeout
) -> CLIResult:
    args = [sys.executable, "-m", module]
    proc = subprocess.Popen(args, stdout=PIPE, stderr=PIPE, text=True,
                            cwd=workdir, env={**os.environ, **env})
    # 既存 _execute_cli_once と同型のタイマー・streaming・log 書き出し
    # stdout を逐次 stdout.log と console.log に書き出しつつ full_output に蓄積
    # 終了後、CLIResult(full_output=..., session_id=None, cost=None, stderr=...) を返す
    # exit code != 0 + stdout に verdict なし → ScriptExecutionError
    # exit code != 0 + stdout に verdict あり → CLIResult をそのまま返す
```

### 3. Runner dispatch 分岐

`runner.py:run()` の step ループ内、`execute_cli` 呼び出し直前で:

```python
metadata = load_skill_metadata(current_step.skill, self.project_root, self.config.paths.skill_dir)

if metadata.exec_script is not None:
    context_env = {
        "KAJI_ISSUE_ID": run_ctx.canonical_id,
        "KAJI_ISSUE_REF": run_ctx.issue_ref,
        "KAJI_STEP_ID": current_step.id,
        "KAJI_WORKTREE_DIR": issue_context.worktree_dir,
        "KAJI_BRANCH_NAME": issue_context.branch_name,
        "KAJI_PROVIDER_TYPE": issue_context.provider_type,
        "KAJI_GIT_REMOTE": issue_context.git_remote,
        "KAJI_DEFAULT_BRANCH": issue_context.default_branch,
    }
    if pr_context is not None:
        context_env["KAJI_PR_ID"] = str(pr_context.pr_id)
        context_env["KAJI_PR_REF"] = pr_context.pr_ref
    result = execute_script(
        step=current_step, module=metadata.exec_script,
        env=context_env, workdir=effective_workdir,
        log_dir=step_log_dir, timeout=resolved_timeout,
    )
    verdict = parse_verdict(result.full_output, valid_statuses=valid, ai_formatter=None)
else:
    # 既存 execute_cli + parse_verdict（AI formatter 付き）
```

prompt 構築（`build_prompt`）は exec_script 経路では呼ばない。

### 4. validate_workflow の skill 整合チェック

現状 `validate_workflow(workflow)` は workflow 単体を見る。`runner.py:run()` Step 0 では `validate_skill_exists` を全 step に対し呼ぶ。同じループ内で `load_skill_metadata` を呼び、`step.agent is None and metadata.exec_script is None` を `WorkflowValidationError` として fail-fast する。

`kaji validate` CLI は config 非依存だが、`paths.skill_dir` が解決できる場合のみ整合 check を行う（既存の `validate_skill_exists` 呼び出し範囲に合わせる）。

### 5. review_poll_entry

```python
# kaji_harness/scripts/review_poll_entry.py
def main() -> int:
    issue_id = os.environ["KAJI_ISSUE_ID"]
    provider_type = os.environ.get("KAJI_PROVIDER_TYPE", "")
    if provider_type != "github":
        sys.stdout.write(_abort_verdict("requires provider.type='github'"))
        return 0
    # gh CLI で PR / owner / repo / head_sha / head_committed_at を解決
    # （現在 SKILL.md の Step 1〜3 が bash で行っている処理を Python で実装）
    # その後 codex_review_poll.main([...]) を呼ぶ
```

`codex_review_poll.main()` は既に `argv: list[str] | None` を受け取る設計なので、`main(["--pr", ..., "--owner", ..., "--repo", ..., "--head-sha", ..., "--head-committed-at", ...])` で呼べる。`codex_review_poll.py` 本体への変更は不要。

## テスト戦略

> **CRITICAL**: 変更タイプ = 実行時コード変更。Small / Medium で網羅し、Large は不要。

### 変更タイプ
- 実行時コード変更（harness dispatch レイヤと skill loader の拡張）

### Small テスト

- `tests/test_skill_metadata.py`（新規）
  - frontmatter なし → `SkillMetadata(exec_script=None)`
  - frontmatter あり / `exec_script` なし → `exec_script=None`
  - `exec_script: kaji_harness.scripts.review_poll_entry` → 正常に取得
  - `exec_script: ../../etc/passwd` / `exec_script: /abs/path` / `exec_script: foo; rm -rf /` → `SkillFrontmatterError`
  - SKILL.md 不在 → 既存 `SkillNotFound` を継続
- `tests/test_script_exec.py`（新規）
  - exit 0 + verdict ブロック含む stdout → `CLIResult` 正常返却
  - exit 0 + stdout 空 → `CLIResult.full_output == ""`（verdict parse 側で fail-loud）
  - exit non-zero + stdout に verdict あり → `CLIResult` を返却（terminal-event 優先と同型）
  - exit non-zero + stdout に verdict なし → `ScriptExecutionError`
  - env 変数注入: `subprocess.Popen` への env 引数に `KAJI_ISSUE_ID` 等が含まれていること
  - shell=False の確認（`shell` キーワードが明示渡されないこと、または `False`）
  - `sys.executable` を起動コマンドの先頭に使うこと
- `tests/test_models.py`（拡張）
  - `Step(agent=None)` が dataclass として構築可能

### Medium テスト

- `tests/test_runner_exec_script.py`（新規）
  - dummy skill（`tests/fixtures/skills/dummy-script/SKILL.md` に `exec_script: tests.fixtures.scripts.dummy_pass` 等）と dummy module を用意し、`WorkflowRunner.run()` を回す
  - skill が PASS verdict を stdout に出す → state.last_transition_verdict.status == "PASS"
  - skill が exit 1 で ABORT verdict を出す → ABORT として記録される（exit code に依らず verdict 優先）
  - skill が stdout を一切出さない → `VerdictNotFound`（AI formatter は呼ばれない）
  - `cost` / `session_id` は None で記録される
  - `agent` 未指定 step + exec_script skill → 正常実行
  - `agent` 未指定 step + exec_script なし skill → `WorkflowValidationError`
- `tests/test_workflow_agent_optional.py`（新規）
  - YAML で agent を省略した step を持つ workflow が `load_workflow_from_str` で parse できる
  - `validate_workflow` 単体（skill 非依存）は agent 省略を許容
  - runner 経由で skill metadata と組み合わせた fail-fast が機能する

### Large テスト

- 不要。理由:
  - `review-poll` skill 自体の polling ロジックは `codex_review_poll.py` 既存テスト（`tests/test_codex_review_poll.py` 系）でカバー済み
  - 本 Issue の変更点は dispatch レイヤと frontmatter parser であり、実 API 疎通の追加価値がない
  - `docs/dev/testing-convention.md` の 4 条件のうち「想定される不具合パターンが既存テストで捕捉済み」「新規テストを追加しても回帰検出情報がほとんど増えない」を満たす

## 影響ドキュメント

| ドキュメント | 影響 | 理由 |
|-------------|------|------|
| `docs/dev/skill-authoring.md` | **あり** | `exec_script` frontmatter 仕様を追記（決定論的 skill の書き方、env 変数仕様、verdict 出力責務） |
| `docs/dev/workflow-authoring.md` | **あり** | `agent` 任意化条件（exec_script skill のみ）と validation 動作を追記 |
| `docs/ARCHITECTURE.md` | なし | dispatch レイヤの拡張だが、verdict 判定機構の根本構造は不変 |
| `docs/adr/` | なし | 既存方針（skill = 1 step の作業資産、verdict は stdout 経由）の延長であり、新規 ADR 不要 |
| `docs/dev/development_workflow.md` | なし | 開発ワークフローのフロー定義は不変 |
| `docs/reference/python/` | なし | コーディング規約 / 命名 / 型 hint に影響なし |
| `docs/cli-guides/` | なし | `kaji run` / `kaji validate` の CLI シグネチャ不変 |
| `CLAUDE.md` | なし | プロジェクト規約に影響なし |
| `.claude/skills/review-poll/SKILL.md` | **あり** | コード変更そのものではないが、frontmatter / Step 構成を同 PR で更新 |
| `.kaji/wf/full-cycle.yaml` | **あり** | `review-poll` step から `agent` / `model` / `effort` 削除 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #196 review-poll ERROR 一次根拠 | `.kaji-artifacts/196/runs/2605272327/run.log` | `workflow_end status=ERROR error=VerdictNotFound: No verdict delimiter found in output. Step 3 (AI formatter) skipped to prevent fabrication. ... polling を起動しました。完了通知を待ちます。` — agent が polling を background 起動して即ターン終了したことを示す |
| 該当 agent 出力 | `.kaji-artifacts/196/runs/2605272327/review-poll/stdout.log` | review-poll step が verdict ブロックを stdout に乗せず終了したことの裏付け |
| 既存 skill 実装 | `.claude/skills/review-poll/SKILL.md` | Step 0〜4 が PR 情報解決 + `python -m kaji_harness.scripts.codex_review_poll` 起動の bash wrapper であること |
| 既存 polling 本体 | `kaji_harness/scripts/codex_review_poll.py:251-292` | `main(argv: list[str] | None)` が完備しており、引数を整えれば直接呼出し可能 |
| Step 必須フィールド | `kaji_harness/models.py:46` | `agent: str` が必須として宣言されており、これが exec_script 経路では緩和される対象 |
| Step required keys | `kaji_harness/workflow.py:56` | `_STEP_REQUIRED_KEYS = ("id", "skill", "agent")` — agent 緩和に伴い改修対象 |
| Verdict fail-loud 機構 | `kaji_harness/verdict.py:289-298` | Issue #193 由来の「delimiter なしで AI formatter を起動しない」契約。exec_script 経路でもこの fail-loud を継承する根拠 |
| 失敗判定の terminal-event 優先 | `kaji_harness/cli.py:152-178` | exit code 単独で失敗判定しない既存契約。exec_script 経路でも「verdict あれば exit code に関わらず採用」を継承する根拠 |
| Skill frontmatter 既存慣例 | `.claude/skills/review-poll/SKILL.md:1-4` | 既に `---\nname: ...\ndescription: ...\n---` の YAML frontmatter が使われており、`exec_script` を同形式で追加することで一貫性を保てる |
| Verdict 出力契約 | `docs/dev/skill-authoring.md:61-81` | 「stdout にそのまま verdict を出力する責務は skill 側」「verdict 不在は fail-loud」の正本 |
| テスト規約 | `docs/dev/testing-convention.md:50-75` | 実行時コード変更は S/M/L 観点を検討、Large 省略は 4 条件根拠の明示 |
| Python style | `docs/reference/python/python-style.md` | snake_case / type hints / Google docstring の準拠先 |
