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

- 一次根拠: `/home/aki/dev/kaji/main/.kaji-artifacts/196/runs/2605272327/run.log` 最終 event
  - `workflow_end status=ERROR error=VerdictNotFound: No verdict delimiter found in output. Step 3 (AI formatter) skipped to prevent fabrication. ... polling を起動しました。完了通知を待ちます。`
- 該当 stdout: `/home/aki/dev/kaji/main/.kaji-artifacts/196/runs/2605272327/review-poll/stdout.log`

> **artifact パスについて (SF-1 対応)**: 上記 `.kaji-artifacts/...` は main worktree (`/home/aki/dev/kaji/main`) に存在する artifact であり、feature worktree (`/home/aki/dev/kaji/kaji-feat-204`) からは参照できない。本設計書中の全 `.kaji-artifacts/196/runs/2605272327/...` 参照は `/home/aki/dev/kaji/main/.kaji-artifacts/196/runs/2605272327/...` を指す。

LLM 判断を必要としない決定論的 step に agent を必須とする構造は (a) 単一障害点、(b) 不要な LLM コスト、(c) 実行時間ブレ の 3 重損失を生む。

### 代替案と不採用理由

| 案 | 内容 | 不採用理由 |
|----|------|-----------|
| A. workflow.yaml に新 step type `exec:` を導入 | step スキーマに `type: exec` を増やす | 別 Issue（Issue 本文「将来の発展: C 案」）として起票予定。skill 単位の再利用ができず、workflow ごとに重複定義が増える |
| B. agent prompt に「`background=False` 強制」追記 | LLM 側に regulate を依存 | 単一障害点（LLM 側の遵守失敗で再発）。不要なコスト・時間は解消しない |
| **C. skill frontmatter に `exec_script` 追加（本案）** | skill 定義側で dispatch 方法を宣言 | skill 単位で再利用可能、後方互換、決定論的 |

C 案を採用する根拠: 「LLM 介在の有無」は **skill の性質**（review-poll は仕様上 LLM 不要、issue-design は仕様上 LLM 必須）であり、workflow ごとに毎回宣言するより skill 定義側に持つ方が DRY。

### Issue 完了条件との差分（MF-1 対応）

Issue #204 完了条件は `exec_script: kaji_harness.scripts.codex_review_poll` を指定するが、本設計では `kaji_harness.scripts.review_poll_entry`（新規 entry module）を指定する方針に **変更** する。

**変更理由**:
- `kaji_harness/scripts/codex_review_poll.py:251-292` の `main()` は `--pr` / `--owner` / `--repo` / `--head-sha` / `--head-committed-at` の 5 個を必須 argparse 引数として要求する。
- `exec_script` 契約は「module 名のみを harness 側から指定し、subprocess に context は env で渡す」というシンプルな I/F に統一する（複雑な argv 構築 hook を harness 側に持たせない決定）。
- したがって `codex_review_poll` を直接 `exec_script` に指定すると必須 argv 不足で `SystemExit(2)` となり機能しない。
- env→argv 変換と PR 解決（現 `SKILL.md` Step 1〜3 の bash 処理）を担う薄い entry module を新設するのが最小変更で、`codex_review_poll.py` 本体（polling ロジック / argparse 契約）を一切変更せずに済む（Issue スコープ境界「polling ロジックには変更を加えない」遵守）。

**Issue 本文の更新**: 設計修正コミット後、Issue #204 の完了条件該当行を以下に書き換える:

> - [ ] `review-poll` skill を `exec_script: kaji_harness.scripts.review_poll_entry` に切り替え（新設の entry module が env→argv 変換と PR 解決を担当）、SKILL.md から bash wrapper 手順を削除

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

`agent` 未指定 step が `exec_script` を持たない skill を参照していたら、**runner preflight (L2)** で `WorkflowValidationError`（fail-fast）。skill metadata に依存する判定のため L1 (YAML schema) では検出せず、後述 § validate_workflow の L1/L2/L3 責務表に従う。

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
| subprocess が **non-zero exit**（stdout の verdict 有無を問わず） | `ScriptExecutionError` を raise（stderr を detail に含める）。**verdict があっても採用しない**（MF-2 対応） |
| skill が `agent` 未指定なのに `exec_script` も持たない | **runner preflight (L2)** で `WorkflowValidationError`（skill metadata 依存のため L1 schema では検出不可。`kaji validate` (L3) で skill_dir が解決できれば同等 check を任意実施。SF-2 対応、後述 § validate_workflow の L1/L2/L3 責務表が正本） |

#### exit code と verdict の優先順位（MF-2 対応・正本）

`exec_script` 経路の契約は次の 1 行に集約する:

> **deterministic script は verdict を emit したら必ず `return 0` で終了する。non-zero exit は常に harness 側で fail-loud（`ScriptExecutionError`）として扱う。**

**根拠**:
- `kaji_harness/scripts/codex_review_poll.py:287-288` の `main()` は ABORT 含む全 verdict 状態で `sys.stdout.write(emit_verdict(...))` 後に `return 0` する設計であり、これが本機構の reference 実装である。FAIL 状態は exit code ではなく verdict status (`ABORT`) で表現する。
- 前回設計で参照した `kaji_harness/cli.py:152-178` の「terminal-event 観測後の non-zero 容認」は **agent CLI が adapter terminal event を出した後に harness が SIGTERM を送る可能性がある場合の救済** であり、Python subprocess には適用できない（一次情報の文脈逸脱）。本設計からこの根拠を撤回する。
- exit code 0 を強制することで、cleanup 失敗 / flush 失敗 / dependency error 等の予期せぬ non-zero を fail-loud で検知でき、PASS verdict の隠蔽事故を防ぐ。

**script 著者への影響**:
- ABORT / RETRY / BACK_FALLBACK 等の業務上の失敗は **必ず verdict status で表現** すること。`sys.exit(1)` で表現してはならない。
- catastrophic 失敗（例: `gh` CLI 不在、依存 import error）は raise させてよい。harness が `ScriptExecutionError` として ERROR 扱いする。

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
| `tests/test_skill_metadata.py` | 新規 | frontmatter parse / identifier validation (Small) |
| `tests/test_script_exec.py` | 新規 | `subprocess.Popen` を patch して dispatch ロジック検証 (Small) |
| `tests/test_review_poll_entry.py` | 新規 | entry module の env validation / argv 委譲を mock で検証 (Small) |
| `tests/test_models.py` | 拡張 | `Step(agent=None)` 構築可能性 (Small) |
| `tests/test_runner_exec_script_dispatch.py` | 新規 | `WorkflowRunner.run()` から dispatch 分岐と fail-fast を検証 (Medium、`execute_script` は patch) |
| `tests/test_workflow_agent_optional.py` | 新規 | YAML schema レベルの agent 省略許容 (Medium) |
| `tests/test_exec_script_subprocess_large.py` | 新規 | 実 Python subprocess 起動による E2E 確認 (Large + large_local) |
| `tests/fixtures/skills/dummy-script/SKILL.md` | 新規 | dispatch / large テスト用 fixture |
| `tests/fixtures/scripts/dummy_pass.py` 他 | 新規 | large テスト用 dummy script 群（pass / abort_zero / nonzero / no_verdict） |

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
    # exit code != 0 → 常に ScriptExecutionError（stdout の verdict 有無は問わない、MF-2 対応）
    # exit code == 0 → CLIResult を返却（verdict parse は呼び出し側）
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

### 4. validate_workflow の skill 整合チェック（SF-2 対応）

検証は **2 層 + 1 任意層** で発火させ、どの層でどの check が走るかを以下のように一意に定義する。

| 層 | 場所 | 行う check | skill metadata 依存 |
|----|------|-----------|---------------------|
| L1: YAML schema 検証 | `kaji_harness/workflow.py:_load_step` | `id` / `skill` 必須、`agent` は **任意**（型のみ `str | None`） | なし |
| L2: runner preflight | `kaji_harness/runner.py:run()` Step 0 | 全 step に対し `validate_skill_exists` + `load_skill_metadata` を呼び、`step.agent is None and metadata.exec_script is None` を `WorkflowValidationError` で fail-fast | あり |
| L3: `kaji validate` CLI（任意） | `kaji_harness/cli.py:validate` | `paths.skill_dir` が解決できる場合のみ L2 と同等の skill 整合 check を実施。解決不能なら警告のみ出して skip | 条件付きあり |

`Step.agent` の型緩和（`str` → `str | None`）に伴い `_STEP_REQUIRED_KEYS` から `agent` を除外するのは L1。`agent` 省略の妥当性は skill metadata なしには判断できないため L2 で確定する。`model` / `effort` は `exec_script` 経路では「存在しても無視（warning ログ）」とし、ここも L2 で検証する（L1 では存在チェックしない）。

**`kaji validate` の挙動明示**: skill metadata 解決不能（`skill_dir` 未設定や CWD 外実行時）の場合、`exec_script` の整合 check は skip し、validation は YAML schema レベル（L1）のみで通過させる。CI で確実に検証したい場合は `kaji run` の preflight (L2) に任せる。

### 5. review_poll_entry（責務境界の正本）

新設 entry module の責務は **「env から argv への変換と PR 解決」のみ**。polling 本体は `codex_review_poll.main()` に委譲し、本 module からは触れない。

**受け取る env（harness 注入）**:

| env 変数 | 必須 | 用途 |
|----------|------|------|
| `KAJI_ISSUE_ID` | ✅ | `kaji pr list --search` の検索キー |
| `KAJI_PROVIDER_TYPE` | ✅ | `"github"` 以外は ABORT verdict + return 0 |
| `KAJI_GIT_REMOTE` | ✅ | `git remote get-url` で owner/repo 解決 |
| `KAJI_WORKTREE_DIR` | ✅ | `git remote get-url` 実行 cwd |
| `KAJI_PR_ID` | 任意 | harness 側で解決済みの場合のみ。未設定なら `kaji pr list --search` で取得 |

**verdict 出力（ABORT 経路）**:

| 失敗シナリオ | verdict status | exit code |
|-------------|--------------|-----------|
| `KAJI_PROVIDER_TYPE != "github"` | `ABORT`（reason: provider mismatch） | 0 |
| `KAJI_ISSUE_ID` から PR が解決できない（`kaji pr list` 0 件 or null） | `ABORT`（reason: PR not resolved） | 0 |
| `headRefOid` が空 / null（再取得後も） | `ABORT`（reason: head_sha unavailable） | 0 |
| `commits[-1].committedDate` が空 | `ABORT`（reason: head committed_at unavailable） | 0 |
| `git remote get-url` 失敗（owner/repo 解決不能） | `ABORT`（reason: remote url parse failure） | 0 |
| `gh` CLI 不在 / 通信不能で `subprocess.CalledProcessError` | raise（harness が `ScriptExecutionError` で ERROR 扱い） | non-zero |

**argv 委譲契約**:

```python
def main() -> int:
    # 1. env validate（上記の ABORT 表に従う）
    # 2. PR / owner / repo / head_sha / head_committed_at を gh CLI 経由で解決
    # 3. codex_review_poll.main([
    #        "--pr", str(pr_id),
    #        "--owner", owner,
    #        "--repo", repo,
    #        "--head-sha", head_sha,
    #        "--head-committed-at", head_committed_at,
    #    ])
    # 4. その return code をそのまま return（codex_review_poll の契約上 0 のはず）
```

**polling 本体との責務境界**:

| 責務 | 担当 |
|------|------|
| env → argv 変換 | `review_poll_entry` |
| PR / owner / repo / head 情報の解決 | `review_poll_entry` |
| GitHub API polling / reactions・reviews 判定 / timeout / verdict 出力 | `codex_review_poll`（無変更） |
| stdout への `---VERDICT---` ブロック書き込み | `codex_review_poll.emit_verdict()`（無変更） |

`codex_review_poll.main()` は既に `argv: list[str] | None` を受け取る設計なので、本 entry 経由でも `codex_review_poll.py` 本体への変更は不要。

## テスト戦略

### 変更タイプ
- 実行時コード変更（harness dispatch レイヤと skill loader の拡張、entry module 新設）

### サイズ分類の整理（MF-4 対応）

`docs/reference/testing-size-guide.md:24-30,68-76` に従い、**実 Python subprocess 起動を含むテストは `large` + `large_local` マーカー** を付ける。前回設計で Medium に分類していた `test_runner_exec_script.py` の一部は実 subprocess を含むため Large に移す。一方、dispatch ロジックを `subprocess.Popen` をモック / patch で完結させて検証する Medium テストは別ファイルに分離する。

### Small テスト（モック完結 / 純粋ロジック）

- `tests/test_skill_metadata.py`（新規）
  - frontmatter なし → `SkillMetadata(exec_script=None)`
  - frontmatter あり / `exec_script` なし → `exec_script=None`
  - `exec_script: kaji_harness.scripts.review_poll_entry` → 正常に取得
  - `exec_script: ../../etc/passwd` / `exec_script: /abs/path` / `exec_script: foo; rm -rf /` → `SkillFrontmatterError`
  - SKILL.md 不在 → 既存 `SkillNotFound` を継続
- `tests/test_script_exec.py`（新規、`subprocess.Popen` を patch）
  - argv 構築: `[sys.executable, "-m", "<module>"]` の形であることを確認
  - shell=False の確認（`shell` キーワードが明示渡されないこと、または `False`）
  - env 引数に `KAJI_ISSUE_ID` 等の context env が merge されていること（既存 `os.environ` を base とすること）
  - mock の returncode=0 / stdout に verdict あり → `CLIResult` 正常返却
  - mock の returncode=0 / stdout 空 → `CLIResult.full_output == ""`
  - mock の returncode=1（stdout の verdict 有無を問わず）→ `ScriptExecutionError` raise（MF-2 整合）
  - timeout 発生 → `StepTimeoutError`
- `tests/test_models.py`（拡張）
  - `Step(agent=None)` が dataclass として構築可能
- `tests/test_review_poll_entry.py`（新規、MF-3 対応、`gh` CLI / `codex_review_poll.main` を patch）
  - `KAJI_PROVIDER_TYPE != "github"` → stdout に ABORT verdict（reason: provider mismatch）、`return 0`
  - `kaji pr list --search` が 0 件 → ABORT verdict（reason: PR not resolved）、`return 0`
  - `headRefOid` 空 / null → ABORT verdict（reason: head_sha unavailable）、`return 0`
  - `commits[-1].committedDate` 空 → ABORT verdict（reason: head committed_at unavailable）、`return 0`
  - `git remote get-url` が SSH / HTTPS 両形式で owner/repo を正しく抽出できること（remote URL parse の境界ケース）
  - 正常系: env 完備 → `codex_review_poll.main(argv)` が `--pr <pr_id> --owner <o> --repo <r> --head-sha <sha> --head-committed-at <ts>` の argv で呼ばれることを mock の `call_args` で検証
  - `gh` CLI が `CalledProcessError` を投げた場合 → entry module が握り潰さず raise すること（harness で `ScriptExecutionError` として捕捉される前提）

### Medium テスト（runner + ファイル fixture、subprocess は mock）

- `tests/test_runner_exec_script_dispatch.py`（新規）
  - dummy skill（`tests/fixtures/skills/dummy-script/SKILL.md` に `exec_script: dummy.module`）を `tmp_path` 配下に作り、`WorkflowRunner.run()` を回す。`execute_script` 関数自体を patch して呼出引数（step, module, env, workdir, log_dir, timeout）を検証
  - PASS verdict 返却の mock → state.last_transition_verdict.status == "PASS"
  - VerdictNotFound 経路: mock が空 stdout を返す → AI formatter が呼ばれない（`assert_not_called`）
  - `cost` / `session_id` が None で記録されること
  - `agent` 未指定 step + exec_script skill → dispatch 分岐に入ること
  - `agent` 未指定 step + exec_script なし skill → `WorkflowValidationError` で fail-fast（L2 検証、SF-2 対応）
- `tests/test_workflow_agent_optional.py`（新規）
  - YAML で agent を省略した step を持つ workflow が `load_workflow_from_str` で parse できる（L1 検証）
  - `validate_workflow` 単体（skill 非依存）は agent 省略を許容（L1 検証）

### Large テスト（`large` + `large_local`、実 subprocess を起動）

- `tests/test_exec_script_subprocess_large.py`（新規、`@pytest.mark.large` + `@pytest.mark.large_local`）
  - `tests/fixtures/scripts/dummy_pass.py`（stdout に PASS verdict + return 0）を実 `python -m` で起動 → `execute_script` が `CLIResult` を返し、verdict parse が PASS を得ること
  - `tests/fixtures/scripts/dummy_abort_zero.py`（ABORT verdict + return 0）→ ABORT として記録（業務失敗は verdict で表現される MF-2 契約の確認）
  - `tests/fixtures/scripts/dummy_nonzero.py`（PASS verdict を出すが `sys.exit(1)`）→ `ScriptExecutionError` raise（MF-2: non-zero は verdict 有無を問わず fail-loud）
  - `tests/fixtures/scripts/dummy_no_verdict.py`（stdout 空 + return 0）→ `VerdictNotFound`、AI formatter は呼ばれない
  - env 注入の実 subprocess 確認: `KAJI_ISSUE_ID=204` を渡し、dummy script が `os.environ["KAJI_ISSUE_ID"]` を verdict.evidence に echo した内容を assert

### Large 不要の判断（変更タイプ補足）

- `review-poll` 自体の polling ロジック（GitHub API 疎通）は `codex_review_poll.py` 既存テスト群でカバー済みであり、本 Issue では追加しない。
- 実 GitHub API 疎通（`large_forge`）も本 Issue では追加しない（dispatch レイヤと entry module の責務に閉じる）。

## 影響ドキュメント

| ドキュメント | 影響 | 理由 |
|-------------|------|------|
| `docs/dev/skill-authoring.md` | **あり** | `exec_script` frontmatter 仕様を追記（決定論的 skill の書き方、env 変数仕様、verdict 出力責務） |
| `docs/dev/workflow-authoring.md` | **あり** | `agent` 任意化条件（exec_script skill のみ）と validation 動作を追記 |
| `docs/ARCHITECTURE.md` | **あり** | WorkflowRunner シーケンス図に exec_script / agent の分岐と RunLogger の `dispatch` field を追記（dispatch レイヤ拡張の図解化） |
| `docs/adr/` | なし | 既存方針（skill = 1 step の作業資産、verdict は stdout 経由）の延長であり、新規 ADR 不要 |
| `docs/dev/development_workflow.md` | なし | 開発ワークフローのフロー定義は不変 |
| `docs/reference/python/logging.md` | **あり** | `step_start` / `step_end` の `agent` を nullable 化、`dispatch` field（`"agent"` / `"exec_script"`）を追記。exec_script 経路では `agent`/`model`/`effort` が常に null である旨を明記 |
| `docs/reference/python/` (logging.md 以外) | なし | コーディング規約 / 命名 / 型 hint に影響なし |
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
| 失敗判定の terminal-event 優先（**撤回**） | `kaji_harness/cli.py:152-178` | agent CLI が adapter terminal event を出した後に harness が SIGTERM を送る場合の救済であり、Python subprocess の一般契約には適用しない。前回設計でこの根拠を流用していたが MF-2 で撤回し、`codex_review_poll.main()` の「verdict 後 return 0」契約に基準を切り替えた |
| codex_review_poll の return code 契約（**新規根拠**） | `kaji_harness/scripts/codex_review_poll.py:287-288` | `sys.stdout.write(emit_verdict(...))` 後に `return 0` する。ABORT 等の業務失敗も verdict status で表現し exit code には載せない。これが exec_script 経路の reference 契約 |
| Test size guide (subprocess) | `docs/reference/testing-size-guide.md:24-30,68-76` | 実 CLI / subprocess 実行は `large`、ネットワーク無しは `large_local` マーカー。MF-4 対応で本設計のテスト分類根拠とする |
| Skill frontmatter 既存慣例 | `.claude/skills/review-poll/SKILL.md:1-4` | 既に `---\nname: ...\ndescription: ...\n---` の YAML frontmatter が使われており、`exec_script` を同形式で追加することで一貫性を保てる |
| Verdict 出力契約 | `docs/dev/skill-authoring.md:61-81` | 「stdout にそのまま verdict を出力する責務は skill 側」「verdict 不在は fail-loud」の正本 |
| テスト規約 | `docs/dev/testing-convention.md:50-75` | 実行時コード変更は S/M/L 観点を検討、Large 省略は 4 条件根拠の明示 |
| Python style | `docs/reference/python/python-style.md` | snake_case / type hints / Google docstring の準拠先 |
