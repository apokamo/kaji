# [設計] recovery の再開禁止判定を step ID ではなく Step.skill で行う

Issue: #349

## 概要

`kaji_harness/recovery` の再開禁止 denylist が skill 名（`issue-start` / `i-pr` / `issue-close`）を保持しながら、比較対象に workflow の step ID（`start` / `pr` / `close`）を使っているため、標準 workflow の副作用 step が一度も再開禁止と判定されない。denylist を `NON_RESUMABLE_SKILLS` として skill 名で一元管理し、失敗 step と実際の再開先を `Step.skill` に解決して判定する。

## 背景・目的

### Observed Behavior（OB）

Issue #342 の source run `260715021013`（Issue #328 / workflow `.kaji/wf/docs-codex.yaml`）では、`pr` step が `StepTimeoutError` で失敗した。`pr` は skill `i-pr` を実行する外部副作用 step だが、recovery は再開禁止 gate で停止せず、再開可能候補を生成した。

一次証拠 `/home/aki/dev/kaji/main/.kaji-artifacts/328/runs/260715021013/recovery.json`（本設計作成時に実物を確認）:

```json
{
  "recoverable": true,
  "decision": "comment_only",
  "classification": { "cause": "dispatch_failure", "recoverability_hint": "candidate" },
  "failed_step": "pr",
  "resume_from": "pr",
  "resume_command": "kaji run .../docs-codex.yaml 328 --from pr --recovery-root 260715021013 --recovery-parent 260715021013",
  "reason": "auto recovery is disabled; resume command is offered as a manual next action",
  "auto_recovery_attempted": false
}
```

`decision: comment_only` かつ `recoverable: true` は「`auto_recover=false` だったから child が起動しなかった」だけの結果であり、`evidence` には `non_resumable_step` gate が 1 件も現れていない。つまり `auto_recover=true` かつ他 gate 非該当なら、同じ `pr` step が `decision: resume` に到達しうる。

### Expected Behavior（EB）

`docs/dev/workflow_guide.md:261`（§ 自動再開しないケース）の既存契約:

> - worktree 不在 / branch 不一致 / provider 解決失敗 / auth・secret・permission 形跡 /
>   副作用 step（`issue-start` / `i-pr` / `issue-close`）→ `not_resumable`

契約は skill 名で副作用 step を定義している。したがって、これらの skill を実行する step は **workflow 上の step ID に依存せず** 再開禁止でなければならない。

次のどちらかが再開禁止 skill なら、recovery の行動決定を必ず停止する。

- 失敗した step の `Step.skill`
- `resume:` 適用後に実際の再開先となる step の `Step.skill`

判定結果は、失敗原因の candidate / noncandidate、`auto_recover` の設定、workflow / provider / variant にかかわらず統一する。

```text
decision = not_resumable
recoverable = false
resume_command = null
resume_scheduled_at = null
```

### 再現手順（Steps to Reproduce）

実 GitHub API / 実 child run を使わず、プロセス内で決定的に再現できる最小手順（Issue § 再現手順に対応）。

1. **前提**: tracked built-in workflow の `.kaji/wf/docs.yaml`（または `dev.yaml`）をロードする。いずれも `id: pr` / `skill: i-pr` の step を含む（OB run が使った `docs-codex.yaml` は `fix/349` に存在しない未追跡ファイルのため、再現手順の入力には tracked workflow を使う。step ID / skill の構造は同一）
2. **入力**: `failed_step="pr"`、`failure_event(kind="dispatch_exception", step_id="pr", exception_type="StepTimeoutError")`、`attempt_error="StepTimeoutError: ..."` の failure snapshot を構築する。他の safety gate（worktree / branch / provider / sensitive / artifact / newer run）はすべて非該当にする
3. **実行**: `plan_recovery(snapshot=..., classification=classify_failure(snapshot), workflow=<上記 workflow>, auto_recover=True, now=...)` を呼ぶ
4. **観測（OB）**: `decision == "resume"` に到達し、`resume_command` / `resume_scheduled_at` が非 null になる。`evidence` に `non_resumable_step` gate 行が現れない
5. **期待（EB）**: `decision == "not_resumable"` / `recoverable is False` / `resume_command is None` / `resume_scheduled_at is None`
6. **同型の再現**: `start`→`issue-start`、`close`→`issue-close`、および標準と異なる step ID（例 `publish`→`i-pr`）でも同じく gate が不発になる

`auto_recover=False`（既定）では手順 3 の結果が `decision: comment_only` / `recoverable: true` / `resume_command` 非 null になる。これは OB run `260715021013` の `recovery.json`（workflow_path = 未追跡の `docs-codex.yaml`）と一致する。`docs.yaml` と `docs-codex.yaml` は `id: pr` / `skill: i-pr` の構造が同一であり、gate の判定入力は `Step.skill` のみに依存するため、tracked workflow による再現は OB と等価である。

### 根本原因（Root Cause）

**なぜ壊れているか**: 定義と比較の単位がずれている。

`kaji_harness/recovery/models.py:30`（本設計作成時の実コード）:

```python
#: irreversible / 外部公開系の副作用を持つ step。自動再開の対象にしない。
NON_RESUMABLE_STEPS = frozenset({"issue-start", "i-pr", "issue-close"})
```

集合の要素は **skill 名** だが、`kaji_harness/recovery/handler.py:145` は **step ID** と比較している。

```python
elif snapshot.failed_step in NON_RESUMABLE_STEPS or resume_from in NON_RESUMABLE_STEPS:
    gates.append("non_resumable_step")
```

`snapshot.failed_step` は `run.log` の `failure_event.step_id` 由来の step ID、`resume_from` は `_resume_point()` が返す `step.resume` または `step.id`（いずれも step ID）である。

**実 workflow での対応**: `fix/349` worktree で **tracked な** built-in workflow（`git -C /home/aki/dev/kaji/kaji-fix-349 ls-files '.kaji/wf/*.yaml'` = 9 ファイル）を全件ロードして確認した。

| workflow（tracked 9 ファイル） | 副作用 step の id | skill |
|---|---|---|
| `dev.yaml` / `dev-thorough.yaml` / `dev-thorough-fable.yaml` / `docs.yaml` / `docs-fable.yaml` / `docs-thorough-codex.yaml`（6 ファイル） | `start` / `pr` / `close` | `issue-start` / `i-pr` / `issue-close` |
| `dev-local.yaml` / `docs-local.yaml`（2 ファイル） | `close` | `issue-close` |
| `incident.yaml`（1 ファイル） | （該当 step なし） | — |

skill 別の出現数は `issue-start` = 6、`i-pr` = 6、`issue-close` = 8（副作用 step を持つ workflow は 8 ファイル）。

**step ID が skill 名と一致する step は tracked built-in workflow に 1 つも存在しない**。したがって gate は全 workflow・全 provider で恒常的に不発である。

> **inventory の一次情報境界（committed source と working-tree artifact の分離）**: OB run
> `260715021013` の `workflow_path` は `.kaji/wf/docs-codex.yaml` だが、このファイルは
> `main` の working tree にのみ存在する **未追跡ファイル**（`git -C .../main status --short` で
> `?? .kaji/wf/docs-codex.yaml`）であり、`fix/349` worktree には存在しない。したがって
> `docs-codex.yaml` は **障害 run の入力 artifact としては有効な一次情報**だが、
> **built-in workflow inventory の一次情報にはできない**。本設計では両者を分離して扱い、
> inventory の件数・テスト対象範囲はすべて tracked 9 ファイルを基準にする。未追跡 workflow を
> 含む任意の step ID alias（例 `publish` → `i-pr`）は、実 YAML ではなく合成 workflow を使った
> 一般則の検証で担保する（§ テスト戦略）。

**いつから壊れているか**: Issue #288（recovery 導入）で `NON_RESUMABLE_STEPS` が skill 名で定義された時点から。`draft/design/issue-288-*.md:413` は「副作用 step の denylist」として skill 名を列挙しており、定義時から命名と比較対象が乖離していた。

**既知だが未修正だった証跡**: `draft/design/issue-296-fix-interactive-terminal-model-capacity.md:168` に次の記述がある。

> `start` step の id は `.kaji/wf/dev-thorough.yaml` で `start`。`NON_RESUMABLE_STEPS = {"issue-start", "i-pr", "issue-close"}` に `start` は含まれないため、candidate 化後の `start` は `non_resumable_step` gate で止まらず、他 gate 通過時に resume 予約される。

**なぜテストが検出できなかったか**: `tests/test_recovery_plan.py:218` の `test_non_resumable_step_denylist_gate` は、fixture workflow（同ファイル `_workflow()`）に `Step(id="i-pr", skill="i-pr", ...)` / `Step(id="issue-start", skill="issue-start", ...)` という **step ID = skill 名の架空 step** を定義し、`failed_step="i-pr"` を与えている。実 workflow の `id: pr` と構造が異なるため、テストは通るが実挙動を保証していない。

**同根の他の壊れ箇所の調査**: `NON_RESUMABLE_STEPS` の全参照を検索した結果、比較箇所は `handler.py:145` の 1 か所のみ（他は `models.py` の定義、`__init__.py` の re-export、`tests/test_recovery_models.py:45` の定数値 assertion、および過去設計書の記述）。skill 名と step ID を取り違える同型の欠陥は recovery 配下に他に存在しない。

### 副次的な原因: gate の適用順序

`plan_recovery()` の現行判定順（`handler.py:171-181` の docstring と実装）は次のとおり。

1. `kaji_bug_suspected` → `bug_issue_created`
2. `recoverability_hint != candidate` → cause 別の `comment_only` / `not_resumable`
3. budget guard → `exhausted`
4. safety gate → `not_resumable`
5. `auto_recover` 無効 → `comment_only`（`resume_command` は提示）
6. → `resume`

副作用 skill の判定は 4 に埋もれている。この順序では、`decision` が失敗原因（1・2）や budget（3）に先に確定するため、「副作用 skill は原因に依存せず常に再開禁止」という不変条件をコード構造として保証できない。実際、OB の run は 5 に到達して `recoverable: true` + `resume_command` を出している。

## インターフェース

bug 修正のため公開 CLI / workflow YAML schema / `recovery.json` schema は変更しない。変更するのは `kaji_harness.recovery` package が公開する定数名と、内部純関数の判定順のみ。

### 入力

`plan_recovery()` の signature は不変。

```python
def plan_recovery(
    *,
    snapshot: FailureSnapshot,
    classification: FailureClassification,
    workflow: Workflow,      # ← 既に受け取っている。step ID → Step.skill 解決に使う
    workflow_path: Path,
    issue_id: str,
    auto_recover: bool,
    now: datetime,
) -> RecoveryDecision: ...
```

`workflow` は既存引数であり、新規引数の追加は不要。

### 出力

#### 定数の改名（変更前 → 変更後）

| 変更前 | 変更後 |
|---|---|
| `kaji_harness.recovery.models.NON_RESUMABLE_STEPS` | `kaji_harness.recovery.models.NON_RESUMABLE_SKILLS` |

値は `frozenset({"issue-start", "i-pr", "issue-close"})` のまま。`kaji_harness/recovery/__init__.py` の import / `__all__` も同時に改名する。

#### 後方互換性の評価: 公開シンボルの BREAKING 削除（ADR 008 / ADR 009）

`NON_RESUMABLE_STEPS` は `kaji_harness/recovery/__init__.py:79-140` の `__all__` に列挙されている。ADR 009 決定 3 は次を正本とする。

> `__all__` は PEP 8 に基づく「public API の宣言」という規約上の意思表示であり…

したがって本改名は **public API の破壊的変更**である。「harness 内部 package だから安定契約ではない」という理由で影響を閉じることはできない。

**alias を残さない根拠**: ADR 008 決定 1「**後方互換レイヤを書かない。** 旧フォーマット読み取り・フォールバック・バージョン分岐を実装しない。レビュー（人間・agent とも）は互換フォールバックの追加を指摘・要求しない」。これは既存の承認済み方針であり、AI の裁量判断ではない。

**alias を書かない代わりに必須となる手続き**: ADR 008 決定 2 は、破壊的変更を CHANGELOG / GitHub Release notes の BREAKING セクションで明示し、次の 3 要素を必ず記載するよう要求する（`.claude/skills/release/SKILL.md:145-157` の `/release` Step 3 がこの 3 要素充足を検査する）。本 Issue の実装では `CHANGELOG.md` の `## [Unreleased]` に `### BREAKING CHANGE` エントリを追加し、3 要素を次の内容で満たす。

| ADR 008 決定 2 の要素 | 本変更での記載内容 |
|---|---|
| **壊れる契約** | `kaji_harness.recovery.NON_RESUMABLE_STEPS` を import しているコードが `ImportError` になる（#349）。`kaji` CLI の実行・引数・exit code、`recovery.json` schema、workflow YAML schema は不変であり、CLI として使う分には影響しない |
| **影響の判定方法** | `grep -rn 'NON_RESUMABLE_STEPS' .` を下流 repo で実行する。0 件なら影響なし |
| **適用指針** | 各ヒットを `NON_RESUMABLE_SKILLS` に置換する。値（`frozenset({"issue-start", "i-pr", "issue-close"})`）と意味は不変で、判定単位が step ID から `Step.skill` へ是正されたことに伴う改名である。kaji は後方互換レイヤを提供しない（ADR 008）。契約変更点の詳細は #349 と本 PR の diff を参照 |

**commit / label**: `/release` skill は commit message の `BREAKING CHANGE:` footer または `<type>!:` 形式を major bump 判定に使う（`.claude/skills/release/SKILL.md:111`）。実装 commit には `BREAKING CHANGE:` footer を付ける。また Issue #349 には直交 meta label `breaking-change`（`docs/dev/labels.md:34` = 「破壊的変更（SemVer major bump 対象）」）を付与する。`type:bug` は単一のまま維持され、`breaking-change` は `type:*` と直交する meta label のため type cardinality には影響しない（`docs/dev/labels.md:154`）。

**repo 内の参照**: `handler.py` / `__init__.py` / `tests/test_recovery_models.py` の 3 箇所のみ（`grep -rn NON_RESUMABLE` で確認）。すべて本 Issue で更新する。

#### `RecoveryDecision` の値（副作用 skill 該当時）

schema・フィールドは不変。値のみ次に固定する。

| フィールド | 値 |
|---|---|
| `decision` | `"not_resumable"` |
| `recoverable` | `false` |
| `resume_from` / `resume_mode` / `resume_command` / `resume_scheduled_at` | `null` |
| `classification` | 分類結果をそのまま保持（診断情報） |
| `reason` | 該当 step ID と skill 名を含む固定文 |
| `evidence` | `snapshot.evidence` + `safety gate: non_resumable_skill (...)` 行 |

### 使用例

```python
# workflow: .kaji/wf/docs.yaml（tracked built-in。id: pr / skill: i-pr）
# snapshot: failed_step="pr", dispatch_exception(StepTimeoutError) → hint=candidate
decision = plan_recovery(
    snapshot=snapshot,
    classification=classification,
    workflow=workflow,
    workflow_path=Path(".kaji/wf/docs.yaml"),
    issue_id="328",
    auto_recover=True,          # ← true でも結果は変わらない
    now=datetime.now(UTC),
)

assert decision.decision == "not_resumable"
assert decision.recoverable is False
assert decision.resume_command is None
assert decision.resume_scheduled_at is None
assert "pr" in decision.reason and "i-pr" in decision.reason
```

## 制約・前提条件

- `Step.skill` は `str | None`（`kaji_harness/models.py:59`）。exec-step（`baseline` 等）は `skill=None` を持つため、`None` を denylist 照合に渡しても該当しない実装にする
- `Workflow.find_step()`（`kaji_harness/models.py:96`）は未知 step ID に `None` を返す。skill 解決は `None` 安全でなければならない
- `_resume_point()` は未知 step で `(None, False)` を返す。この場合 skill 解決不能 → 副作用 gate は不発 → 既存 `unknown_failed_step` gate が `not_resumable` に落とす（現行動作維持）
- `resume:` の指す step は `validate_workflow()`（`kaji_harness/workflow.py:468-486`）が存在を検証済み。`resume:` 先が実在しない workflow は load 時点で弾かれるため、gate 側で再検証しない
- `plan_recovery()` は純関数（fs / provider / subprocess に触れない）。この性質を維持する
- workflow YAML schema に `side_effect: true` 等の属性を追加しない（Issue 設計・実装方針 7）
- 副作用 skill でない通常 step の既存挙動（one-shot recovery / 原因分類 / budget / 既存 safety gate）を変えない

## 方針

### 1. denylist を skill 名で一元管理する

`models.py`:

```python
#: irreversible / 外部公開系の副作用を持つ skill。これを実行する step は step ID に
#: 依存せず自動再開・手動 resume 提示の対象にしない。
NON_RESUMABLE_SKILLS = frozenset({"issue-start", "i-pr", "issue-close"})
```

### 2. step ID → `Step.skill` 解決と gate 判定（純関数）

`handler.py`:

```python
def _step_skill(workflow: Workflow, step_id: str | None) -> str | None:
    if not step_id:
        return None
    step = workflow.find_step(step_id)
    return step.skill if step is not None else None


def _non_resumable_skill_hits(
    workflow: Workflow, failed_step: str | None, resume_from: str | None
) -> list[str]:
    """failed step と実再開先のうち、再開禁止 skill を実行するものを列挙する。"""
    targets = [("failed_step", failed_step)]
    if resume_from is not None and resume_from != failed_step:
        targets.append(("resume_from", resume_from))   # 同一 step の二重計上を避ける
    hits = []
    for label, step_id in targets:
        skill = _step_skill(workflow, step_id)
        if skill in NON_RESUMABLE_SKILLS:
            hits.append(f"non_resumable_skill ({label}={step_id}, skill={skill})")
    return hits
```

`_safety_gates()` からは旧 `non_resumable_step` 分岐を削除する（gate 0 へ昇格するため。両方に残すと二重判定になる）。`unknown_failed_step` gate は `_safety_gates()` に残す。

### 3. 副作用 skill gate を最優先に置く

`plan_recovery()` の判定順を次に変更する。

```python
resume_from, discarded = _resume_point(workflow, snapshot.failed_step)   # 冒頭へ移動

# 0. 副作用 skill gate（原因分類・budget・auto_recover より前）
hits = _non_resumable_skill_hits(workflow, snapshot.failed_step, resume_from)
if hits:
    return build(
        "not_resumable",
        recoverable=False,
        reason=f"non-resumable skill step; auto recovery and manual resume are blocked: "
               f"{', '.join(hits)}",
        extra_evidence=[f"safety gate: {hit}" for hit in hits],
    )
    # resume_from を build に渡さない → resume_from / resume_mode / resume_command /
    # resume_scheduled_at はすべて None（build のデフォルト）

# 1. kaji_bug_suspected → bug_issue_created
# 2. recoverability_hint != candidate → comment_only / not_resumable
# 3. budget guard → exhausted
# 4. 残りの safety gate → not_resumable
# 5. auto_recover 無効 → comment_only
# 6. → resume
```

`_resume_point()` の呼び出しを冒頭へ移すだけで、1〜6 の内部ロジックは現行のまま維持する（`discarded` は 5・6 でのみ使用）。docstring の判定順記述も更新する。

### 4. gate 0 が最優先であることの帰結（意図的）

Issue 本文「重要判断: safety gate の優先順位」の「原因分類は診断情報として保持するが、recovery action の決定では副作用 skill gate を最優先し、常に `not_resumable` とする」に従い、**`kaji_bug_suspected` も gate 0 の下流に置く**。したがって副作用 step で harness の決定論的矛盾が起きた場合、`decision` は `bug_issue_created` ではなく `not_resumable` になり、bug issue は起票されない。

この帰結が診断能力の欠落にならない根拠:

- `classification.cause = "kaji_bug_suspected"` と矛盾の `evidence` は `recovery.json` にそのまま残る（`build()` が `snapshot.evidence` を含める）
- `RecoveryHandler.run()`（`handler.py:360-364`）は decision 値にかかわらず triage コメント投稿と `_record_incident()` を実行する。incident 記録の抑止対象は `INCIDENT_EXEMPT_CAUSES = {"user_precondition_error"}` のみ（`models.py:82`）であり、`kaji_bug_suspected` は除外されない。GitHub provider では incident issue が起票・追記される
- したがって失われるのは「`type:bug` issue の自動起票」1 経路のみで、人間が矛盾を検知する経路は triage コメント・incident issue・`recovery.json` に残る

### 5. 修正後の OB run の再判定

OB run が実際に使った workflow（`.kaji/wf/docs-codex.yaml`。main の未追跡ファイル）と `failed_step="pr"` / `StepTimeoutError` の入力は、gate 0 で `_step_skill(workflow, "pr") == "i-pr" ∈ NON_RESUMABLE_SKILLS` にヒットし、原因分類（`dispatch_failure` / `candidate`）と `auto_recover` の値を問わず `not_resumable` / `recoverable=false` / `resume_command=null` になる。判定は `Step.skill` のみに依存するため、この結論は workflow が tracked か未追跡かに影響されない（tracked な `docs.yaml` / `dev.yaml` でも同一）。

## 変更スコープ

| ファイル | 変更内容 |
|---|---|
| `kaji_harness/recovery/models.py` | `NON_RESUMABLE_STEPS` → `NON_RESUMABLE_SKILLS` 改名 + docstring 更新 |
| `kaji_harness/recovery/__init__.py` | import / `__all__` の改名 |
| `kaji_harness/recovery/handler.py` | `_step_skill()` / `_non_resumable_skill_hits()` 追加、`_safety_gates()` から旧分岐削除、`plan_recovery()` の gate 0 追加と docstring 更新 |
| `tests/test_recovery_models.py` | 定数名・値 assertion の更新 |
| `tests/test_recovery_plan.py` | **Small**。架空 step ID fixture の是正、step ID ≠ skill のパラメータ化、`resume:` 双方向、原因横断、`auto_recover` 双方向（合成 workflow のみ。ファイル I/O なし） |
| `tests/test_recovery_workflow_inventory.py`（新規） | **Medium**。実 `.kaji/wf/*.yaml` を glob + `load_workflow()` して tracked built-in を検査（ファイル I/O を伴うため Medium） |
| `tests/test_recovery_handler.py` | **Medium**。mock child launcher で child 非起動・artifact・run log を検証 |
| `docs/dev/workflow_guide.md` | § 自動再開しないケースの文言を skill 基準に明確化 |
| `CHANGELOG.md` | `## [Unreleased]` に `### BREAKING CHANGE` を追加し、ADR 008 決定 2 の 3 要素を記載（§ インターフェースの表の内容） |
| Issue #349 の label | 直交 meta label `breaking-change` を付与（`type:bug` は単一のまま維持） |

実装 commit には `BREAKING CHANGE:` footer を付ける（`/release` の major bump 判定入力）。

リファクタは混在させない。`plan_recovery()` の 1〜6 の内部ロジック、`classify.py`、`snapshot.py`、`report.py`、incident 系は触らない。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 再開禁止の識別単位 | step ID ではなく `Step.skill` を使用し、`issue-start` / `i-pr` / `issue-close` を `NON_RESUMABLE_SKILLS` で中央管理する | Issue #349 本文 § 重要判断「再開禁止の識別単位」（人間決定）。契約側の出典は `docs/dev/workflow_guide.md:261` | `_step_skill()` による `find_step().skill` 解決、`Step.skill = None`（exec-step）と未知 step の `None` 安全化 |
| safety gate の優先順位 | 原因分類は診断情報として保持し、recovery action 決定では副作用 skill gate を最優先して常に `not_resumable` | Issue #349 本文 § 重要判断「safety gate の優先順位」（人間決定）。根拠は「副作用が途中まで実行された可能性を recovery engine から否定できない」 | gate 0 を `kaji_bug_suspected` 分岐より上に置く帰結（bug issue 非起票）を § 方針 4 で明示し、incident 記録経路（`models.py:82` の `INCIDENT_EXEMPT_CAUSES` に非含有）で診断が残ることを確認 |
| `resume:` の判定対象 | failed step と実際の再開先の両方を `Step.skill` で検査し、片方でも再開禁止 skill なら停止 | Issue #349 本文 § 重要判断「`resume:` の判定対象」および EB の組み合わせ表（人間決定） | `_resume_point()` 呼び出しを `plan_recovery()` 冒頭へ移動し、`failed_step == resume_from` の二重計上を回避する列挙規則を定義 |
| workflow / provider の対象範囲 | built-in の全 workflow / provider / variant と、同じ skill を使うユーザー定義 workflow を対象 | Issue #349 本文 § 重要判断「workflow/provider の対象範囲」（人間決定） | 判定を `Step.skill` のみに依存させ、YAML ファイル名・`requires_provider` を参照しない。実 YAML ロードテストで全 variant を機械検査 |
| workflow schema | `side_effect: true` を追加せず skill denylist を正本とする | Issue #349 本文 § 重要判断「workflow schema」および § スコープ外（人間決定） | YAML parser / `validate_workflow` を変更対象から除外 |
| テスト規模 | unit test・実 YAML load・mock launcher の integration test で保証し、外部 API / 実 child run の E2E は行わない | Issue #349 本文 § 重要判断「テスト規模」および § テスト方針（人間決定） | Small/Medium への割付とサイズ根拠を § テスト戦略で確定 |
| 旧定数名 `NON_RESUMABLE_STEPS` の alias | alias を残さず改名のみ。代わりに CHANGELOG へ BREAKING 3 要素を明示する | **ADR 008 決定 1**「後方互換レイヤを書かない。…レビューは互換フォールバックの追加を指摘・要求しない」（承認済み方針。AI 裁量ではない）。改名自体の出典は Issue § 設計・実装方針 1。公開性の判定は **ADR 009 決定 3**「`__all__` は public API の宣言」 | ADR 008 決定 2 の 3 要素（壊れる契約 / 影響の判定方法 = `grep -rn 'NON_RESUMABLE_STEPS' .` / 適用指針 = `NON_RESUMABLE_SKILLS` へ置換）を § インターフェースの表で確定。`CHANGELOG.md` の `### BREAKING CHANGE` 追加、実装 commit の `BREAKING CHANGE:` footer、Issue への `breaking-change` meta label 付与を変更スコープに追加 |
| gate 名と evidence 書式 | gate 名を `non_resumable_skill` とし、`(failed_step=pr, skill=i-pr)` 形式で step ID と skill を併記 | AI の仮定。根拠: 完了条件「判定理由または evidence から該当 step ID と skill 名を確認できる」を満たす最小形式。既存 gate 文字列の書式（`branch_mismatch (worktree=..., state=...)`、`handler.py:135-137`）に合わせた。旧 `non_resumable_step` から改名するのは、判定単位が skill であることを evidence 上でも一致させるため。検査先: review-design / review-code | `reason` にも hits を含め、`evidence` は既存 gate と同じ `safety gate: <gate>` prefix で追加 |

## テスト戦略

### 変更タイプ

実行時コード変更（条件分岐の追加と判定順の変更）。恒久回帰テストが必要。

### Small テスト

対象: `plan_recovery()` の純ロジック（`tests/test_recovery_plan.py`）。**合成 `Workflow` オブジェクトのみを入力とし、fs / provider / subprocess に触れないため Small**（`docs/dev/testing-convention.md` § 判定基準「それ以外（純粋関数・モック完結）→ Small」）。実 YAML のロードは本節に含めず Medium に置く。

**実装前 Red の一次証拠**: 本 Issue は `.kaji-artifacts/328/runs/260715021013/recovery.json`（`decision: comment_only` / `recoverable: true` / `resume_command` 非 null / `evidence` に gate 行なし）という実世界障害 artifact を持つ。ただし bug 型の再現テスト必須ルール（`_shared/design-by-type/bug.md` § 8）に従い、escape clause には依存せず **実装前に Red になる再現テストを恒久回帰テストとして先に書く**。fixture の架空 step ID（`Step(id="i-pr")` / `Step(id="issue-start")`）を実 workflow と同じ `id: pr` / `id: start` に是正すると、現行実装では gate が不発になり Red が観測できる。

検証観点:

1. **step ID と skill 名が異なる組み合わせ**（パラメータ化）: `start`→`issue-start` / `pr`→`i-pr` / `close`→`issue-close` / `publish`→`i-pr`（標準と異なる step ID）。各ケースで `decision == "not_resumable"` / `recoverable is False` / `resume_command is None` / `resume_scheduled_at is None` / `resume_from is None`
2. **evidence と reason の証跡**: 該当 step ID と skill 名の双方が `reason` または `evidence` に含まれる
3. **`auto_recover` 非依存**: 同一入力を `auto_recover=True` / `False` で流し、上記 4 値が一致する
4. **原因非依存**: candidate 系（`StepTimeoutError` の `dispatch_exception` / `VerdictNotFound` の `verdict_exception`）と noncandidate 系（`agent_abort` / `cycle_exhausted`）、および `kaji_bug_suspected`（`attempt_result_present=False` 等で矛盾を作る）を副作用 step に与え、すべて `not_resumable` になる。`kaji_bug_suspected` では `bug_issue_created` にならないこと、かつ `classification.cause` が診断情報として保持されることを併せて検証（§ 方針 4 の帰結の固定）
5. **`resume:` の 3 組み合わせ**（Issue EB の表と 1:1）:

   | 失敗した step | 実際の再開先 | 期待 |
   |---|---|---|
   | 安全な skill（`fix-pr-meta` → `issue-fix-code`、`resume: pr`） | 再開禁止 skill（`pr` → `i-pr`） | `not_resumable`。evidence に `resume_from=pr, skill=i-pr` |
   | 再開禁止 skill（`pr` → `i-pr`、`resume: implement`） | 安全な skill（`implement`） | `not_resumable`。evidence に `failed_step=pr, skill=i-pr` |
   | 安全な skill（`verify-code`、`resume: review-code`） | 安全な skill | 既存判定継続（`resume` / `comment_only` に到達） |

6. **境界・現行動作維持**: 未知 step ID（`unknown_failed_step` gate で `not_resumable`、evidence が `non_resumable_skill` ではないこと）、`Step.skill is None` の exec-step（gate 0 で誤ヒットしない）、`failed_step == resume_from` のとき hits が 1 件に重複排除される
7. **任意の step ID alias（一般則）**: 標準と異なる step ID から再開禁止 skill を参照するケース（`publish` → `i-pr` 等）は、実 YAML ではなく **合成 workflow** で検証する。判定は `Step.skill` のみに依存し YAML ファイル名に依存しないという一般則をここで固定するため、tracked inventory の内容に左右されない

### Medium テスト

実 YAML の読み込みと run artifact I/O を伴う検査。`docs/dev/testing-convention.md` § 判定基準「DB / ファイル / 内部サービス結合あり → Medium」に従い、いずれも `@pytest.mark.medium` とする。

> **サイズ分類の根拠**: 既存の `tests/workflows/test_review_code_routing.py` / `tests/test_dev_workflow.py` は実 `.kaji/wf/*.yaml` を `load_workflow()` で読みながら `@pytest.mark.small` を付けているが、これは規約を上書きする根拠にならない（規約の正本は `testing-convention.md`）。既存の同型不整合は本 Issue の scope 外として **#352** で分離追跡する。本 Issue の新規テストは規約どおり Medium に置く。

#### (a) workflow inventory 検査（新規 `tests/test_recovery_workflow_inventory.py`）

対象: tracked built-in workflow に含まれる再開禁止 skill の step。実 `.kaji/wf/*.yaml` を glob し `load_workflow()` で読むためファイル I/O を伴う = Medium。

検証観点:

1. `Step.skill in NON_RESUMABLE_SKILLS` の全 step について、`plan_recovery()` が `decision == "not_resumable"` / `recoverable is False` / `resume_command is None` / `resume_scheduled_at is None` を返す（`auto_recover=True` で実行）
2. dev / docs / local / thorough / codex / fable の各 variant と provider 差分を同一規則で網羅する（判定は `Step.skill` のみに依存し、ファイル名・`requires_provider` を参照しないことの機械的確認）
3. **vacuous pass 防止（強化）**: 対象 step が全体で 1 件以上あることに加え、**denylist の各 skill が最低 1 件ずつ検出される**ことを assert する。本設計作成時の tracked inventory は `issue-start` = 6 / `i-pr` = 6 / `issue-close` = 8 であり、いずれも 1 件以上を満たす。これにより一部 skill の YAML からの消失や typo を個別に検知できる（review-design § 改善提案に対応）
4. 対象範囲は `git ls-files` 相当の tracked built-in（本設計作成時 9 ファイル、うち副作用 step を持つのは 8 ファイル。`incident.yaml` は該当 step なし）。glob が未追跡の作業ファイル（例: `main` にのみある `docs-codex.yaml`）を拾った場合も、判定規則は `Step.skill` に依存するため結果は変わらない

#### (b) handler 結合（`tests/test_recovery_handler.py`、既存 `pytestmark = pytest.mark.medium`）

対象: `RecoveryHandler.run()` の orchestrator 結合。`tmp_path` 上の run artifact 読み書きと `run.log` 出力を伴うため Medium。child launcher と provider は既存 fixture と同じく注入 mock（実 child run / 実 GitHub API は使わない）。

検証観点:

1. `auto_recover=True` かつ副作用 step 失敗で **child launcher が呼ばれない**（`launched` リストが空）
2. `recovery.json` が `decision: "not_resumable"` / `recoverable: false` / `resume_command: null` / `resume_scheduled_at: null` で書き出される
3. `run.log` に recovery の `schedule` / `attempt start` / `attempt end` event が記録されない
4. 既存の budget 消費が発生しない（`auto_recovery_attempted: false` / `auto_recovery_attempt_no: 0`）

### Large テスト

新規追加しない。

理由: 判定は純関数と mock launcher でプロセス内に決定的に閉じており、実 GitHub API / 実 child run による再現は Issue § スコープ外（「実 GitHub API や実 child run を使う end-to-end 再現試験」）で人間が明示的に除外している。recovery 経路の E2E 保証は既存 `tests/test_recovery_e2e_large_local.py`（`large` / `large_local`）が担い、本変更で失われない。これは「実行時間が長い」「API キーがない」といった不正当な省略理由（`docs/dev/testing-convention.md` § 省略してはいけない理由）には該当しない。

### 回帰

- 副作用 skill でない通常 step の `resume` / `comment_only` / budget / cycle / 未知 step / 既存 safety gate（`worktree_unavailable` / `branch_mismatch` / `provider_unavailable` / `sensitive_failure_pattern` / `artifact_unreadable` / `newer_run_detected`）を検証する既存テストを変更せずに維持する
- 最後に recovery 関連テスト一式と `make check` を実行する

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| **CHANGELOG.md** | **あり** | `NON_RESUMABLE_STEPS` は `__all__` 掲載の public API（ADR 009 決定 3）であり、その削除は破壊的変更。ADR 008 決定 2 により `## [Unreleased]` へ `### BREAKING CHANGE` を追加し、3 要素（壊れる契約 / 影響の判定方法 `grep -rn 'NON_RESUMABLE_STEPS' .` / 適用指針 `NON_RESUMABLE_SKILLS` へ置換）を記載する。記載形式は #284（`cli_main` shim 削除）の既存エントリに倣う |
| docs/adr/ | なし | 新しい技術選定・アーキテクチャ決定を伴わない。既存契約（ADR 008 / ADR 009）の実装不整合の是正であり、ADR 自体の改訂は不要（ADR 008 § 帰結「外部公開 API の提供など前提が変わった場合は本 ADR を改訂する」に該当しない） |
| docs/ARCHITECTURE.md | なし | recovery package の層構成・責務は不変 |
| docs/dev/workflow_guide.md | **あり** | `:261` の「副作用 step（`issue-start` / `i-pr` / `issue-close`）→ `not_resumable`」は skill 名を「step」と表記している。契約の意味は変えず、「これらの skill を実行する step（step ID は `start` / `pr` / `close` 等）」と判定単位を明記し、原因分類・`auto_recover` に優先することを追記する |
| docs/cli-guides/failure-recovery.ja.md | なし | 副作用 step / denylist への言及なし（本設計作成時に grep で確認）。CLI 仕様・exit code は不変 |
| docs/dev/testing-convention.md | なし | テスト規約自体の変更なし |
| docs/reference/ | なし | Python 規約・API 仕様の変更なし |
| AGENTS.md / CLAUDE.md | なし | 開発規約の変更なし |
| draft/design/issue-288-*.md / issue-296-*.md | なし | 過去の設計書は当時の記録として保持する（歴史的 artifact を遡及改変しない） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 既存契約 | `docs/dev/workflow_guide.md:252-264` § 自動再開しないケース | 「副作用 step（`issue-start` / `i-pr` / `issue-close`）→ `not_resumable`」。契約が skill 名で副作用 step を定義している = EB の一次根拠 |
| 現行実装（denylist 定義） | `kaji_harness/recovery/models.py:29-30` | `#: irreversible / 外部公開系の副作用を持つ step。` + `NON_RESUMABLE_STEPS = frozenset({"issue-start", "i-pr", "issue-close"})`。集合の要素が skill 名である事実 |
| 現行実装（比較箇所） | `kaji_harness/recovery/handler.py:145` | `elif snapshot.failed_step in NON_RESUMABLE_STEPS or resume_from in NON_RESUMABLE_STEPS:`。step ID と skill 名集合を比較している事実 = 根本原因 |
| 現行実装（判定順） | `kaji_harness/recovery/handler.py:171-181, 214-296` | `plan_recovery()` docstring と実装。cause → budget → safety gate → auto_recover の順で、副作用判定が 4 番目である事実 |
| 現行実装（再開点） | `kaji_harness/recovery/handler.py:107-122` `_resume_point()` | `step.resume` があればそれを、無ければ `step.id` を返す。未知 step は `(None, False)`。返り値がすべて step ID である事実 |
| 現行実装（incident 抑止範囲） | `kaji_harness/recovery/models.py:82` / `handler.py:360-364, 475` | `INCIDENT_EXEMPT_CAUSES = frozenset({"user_precondition_error"})`。`run()` が decision 値によらず `_record_incident()` を実行する = § 方針 4 の診断維持根拠 |
| データモデル | `kaji_harness/models.py:46-101` | `Step.id: str` / `Step.skill: str | None` / `Workflow.find_step(step_id) -> Step | None`。skill 解決の IF と `None` 安全化の必要性 |
| resume 検証 | `kaji_harness/workflow.py:468-486` | `validate_workflow` が `Step.resume` 先の存在と agent 一致を検証する = gate 側で再検証不要の根拠 |
| 障害の一次証拠 | `/home/aki/dev/kaji/main/.kaji-artifacts/328/runs/260715021013/recovery.json` | `"decision": "comment_only"` / `"recoverable": true` / `"failed_step": "pr"` / `"resume_command": "kaji run ... --from pr ..."`、`evidence` に `non_resumable_step` gate 行なし = OB の一次証拠 |
| 実 workflow 定義（tracked inventory） | `git -C /home/aki/dev/kaji/kaji-fix-349 ls-files '.kaji/wf/*.yaml'` → 9 ファイル。全件ロードして確認 | 副作用 step の id は `start` / `pr` / `close`、skill は `issue-start`(6) / `i-pr`(6) / `issue-close`(8)。step ID = skill 名の step は 1 つも存在しない = gate 恒常不発の根拠。副作用 step を持つのは 8 ファイル（`incident.yaml` は該当なし） |
| inventory の境界（未追跡ファイル） | `git -C /home/aki/dev/kaji/main status --short .kaji/wf/` → `?? .kaji/wf/docs-codex.yaml`。`fix/349` には不在 | OB run の `workflow_path` である `docs-codex.yaml` は main の未追跡ファイル = 障害 run の入力 artifact としては有効だが built-in inventory の一次情報にはできない根拠 |
| 後方互換ポリシー | `docs/adr/008-no-backward-compat-layer.md` 決定 1・2 / § 帰結 | 決定 1「後方互換レイヤを書かない。…レビューは互換フォールバックの追加を指摘・要求しない」= alias 不保持の出典（AI 裁量ではない）。決定 2「破壊的変更は CHANGELOG / GitHub Release notes の BREAKING セクションで明示し、次の 3 要素を必ず記載する: 壊れる契約 / 影響の判定方法 / 適用指針」= CHANGELOG 記載義務の出典 |
| public API の定義 | `docs/adr/009-module-boundary-private-import.md` 決定 3 | 「`__all__` は PEP 8 に基づく『public API の宣言』という規約上の意思表示」= `NON_RESUMABLE_STEPS` 削除が BREAKING である根拠 |
| 現行の公開宣言 | `kaji_harness/recovery/__init__.py:79-140`（`__all__`） | `"NON_RESUMABLE_STEPS"` が `__all__` に現に列挙されている事実 |
| BREAKING 記載の運用と先例 | `.claude/skills/release/SKILL.md:111, 145-157` / `CHANGELOG.md`（0.15.0 § BREAKING CHANGE, #284） | `/release` Step 3 が 3 要素充足を検査する。commit の `BREAKING CHANGE:` footer / `<type>!:` が major bump 判定入力。#284 の `cli_main` shim 削除エントリが 3 要素の記載形式の先例 |
| label 運用 | `docs/dev/labels.md:34, 154` | `breaking-change` = 「破壊的変更（SemVer major bump 対象）」。`type:*` と直交する meta label として併用する（type cardinality に影響しない）根拠 |
| 既知だが未修正の記録 | `draft/design/issue-296-fix-interactive-terminal-model-capacity.md:168` | 「`NON_RESUMABLE_STEPS` に `start` は含まれないため、`non_resumable_step` gate で止まらず、他 gate 通過時に resume 予約される」 |
| 検出できなかった既存テスト | `tests/test_recovery_plan.py:26-49, 218-228` | fixture が `Step(id="i-pr", skill="i-pr")` / `Step(id="issue-start", skill="issue-start")` という架空 step を定義し、`failed_step="i-pr"` を与えている = テストが実 workflow 構造と乖離していた根拠 |
| 導入時設計 | `draft/design/issue-288-feat-workflow-failure-triage-1-recovery.md:413, 433` | 「副作用 step の denylist: `NON_RESUMABLE_STEPS = {"issue-start", "i-pr", "issue-close"}`」= 定義時から命名と比較対象が乖離していた根拠 |
| テスト規約 | `docs/dev/testing-convention.md` § 判定基準 / § 省略してよい理由 | 「外部 API / 実サービス疎通あり → Large、DB / ファイル / 内部サービス結合あり → Medium、それ以外 → Small」。Small/Medium 割付と Large 省略根拠の判定基準 |
| bug 設計規約 | `.claude/skills/_shared/design-by-type/bug.md` § 8 | 「修正前に Red になる再現テストを必ず 1 本以上定義する。省略不可」。escape clause に依存せず再現テストを先行させる根拠 |
| 既存のサイズ分類不整合（scope 外） | `tests/workflows/test_review_code_routing.py:44-64` / `tests/test_dev_workflow.py:37-45` / Issue #352 | 実 `.kaji/wf/*.yaml` を `load_workflow()` で読みながら `@pytest.mark.small` を付けている既存テスト。規約（`testing-convention.md`）の正本性を上書きしないため、新規テストの Small 割付根拠には**しない**。既存不整合は #352 で分離追跡し、本 Issue では新規テストを規約どおり Medium に置く |
| 判断分類の正本 | `.claude/skills/_shared/critical-decision-checklist.md` § 重要判断の 3 分類 | 可逆性で分類し、AI の仮定には根拠と後段の検査先を記録する |
