# [設計] 長時間 acceptance を step deadline へ整合させ、期限前 checkpoint を可能にする

Issue: #421

## 概要

agent step の hard deadline が runner（headless / tmux / Herdr）内部だけで計算され、prompt と
skill から実行期限を確認できない。attempt 開始時に deadline を **1 回だけ** 計算し、同じ値を
prompt のコンテキスト変数（timeout 秒数・UTC 絶対 deadline）と runner の hard deadline に渡す。
あわせて `issue-design` / `issue-review-design` に長時間 acceptance の配置規則を、
`issue-implement` に残時間確認と期限前 checkpoint（既存 `RETRY`）の規則を追加する。

## 背景・目的

### Observed Behavior（OB）

Incident #393 の 2 occurrence（一次証拠: 原因調査コメント
https://github.com/apokamo/kaji/issues/393#issuecomment-5372780233 ）:

```text
run 260730222002: step=implement attempt=2 runner duration=6002075ms timeout=6000s
                  pane_dead=0 pane_current_command=node session error event=0
                  verdict.yaml=absent  StepTimeoutError: Step 'implement' timed out after 6000s
run 260731015910: step=implement attempt=1 runner duration=6002101ms timeout=6000s
                  pane_dead=0 pane_current_command=node session error event=0
                  verdict.yaml=absent  StepTimeoutError: Step 'implement' timed out after 6000s
```

両 session は timeout の 32 秒 / 21 秒前まで tool call を続け、nested `make dogfood-local`
（外部 agent を含む full lifecycle）を 10 回 / 3 回実行していた。正常に前進している
session が、残時間を知らないまま hard deadline で強制終了された。

現行 main（ffc90bb）のコード上の観測:

| 箇所 | 観測 |
|------|------|
| `kaji_harness/prompt.py:13` `build_prompt()` | 引数・コンテキスト変数に timeout / deadline がない |
| `kaji_harness/runner.py:554` | `build_prompt()` へ timeout を渡していない |
| `kaji_harness/runner.py:571` / `:587` | interactive には `timeout=settings.timeout`、headless には `default_timeout` を渡し、各 runner が独自に deadline を計算 |
| `kaji_harness/interactive_terminal.py:460` | pane launch・`pipe-pane` 完了 **後** に `deadline = time.monotonic() + timeout` |
| `kaji_harness/interactive_terminal_herdr.py:237` | launcher 起動待ち（最大 `_HERDR_LAUNCHER_START_TIMEOUT_SECONDS`）**後** に同様の計算 |
| `kaji_harness/cli.py:235` | `Popen` 後に `threading.Timer(timeout, ...)`。transient retry（`_MAX_RETRIES=3`、`_BASE_DELAY=30s` の指数 backoff）ごとに timer を **full timeout で再始動** |
| `.claude/skills/issue-implement/SKILL.md` | TDD・`make check`・Pre-Handoff Review を要求するが、残時間確認・期限前 checkpoint の規則がない |
| `.claude/skills/issue-design` / `issue-review-design` | 長時間 acceptance を単一 step attempt に置く場合の bounded 根拠・分離規則がない |

### Expected Behavior（EB）

Issue #421 本文「Expected Behavior」を正とする:

1. nested full lifecycle・外部 agent を含む dogfood・複数回の fresh acceptance など、1 attempt の
   timeout 内へ決定論的に収まらない受入処理は、設計段階で独立 acceptance step または別 workflow へ
   分離される
2. agent step の prompt から、その attempt に適用される timeout 秒数と UTC 絶対 deadline を確認できる
3. hard deadline の計算と prompt 表示の deadline は、同じ attempt 開始時刻を基準にする
4. `issue-implement` は長時間 command / nested acceptance / full quality gate の開始前と主要フェーズ
   境界で残時間を確認する
5. deadline 内に完了できない場合、外部副作用と作業状態を整合させ、既存 `RETRY` で次 attempt に
   引き継ぐ
6. hard timeout、verdict-last、cycle 上限、既存 workflow YAML の timeout 値は維持する。timeout の
   自動延長・無制限 retry は導入しない

根拠: `docs/adr/005-artifact-primary-verdict.md`（verdict-last: 外部副作用完了後に最後に
`verdict.yaml` を保存）、`docs/dev/workflow-authoring.md`（timeout 解決順・self-RETRY step の cycle
所属制約）、Incident #393 調査コメント（恒久対策候補 1・2、「単純な timeout 延長を恒久策にしない」）。

## 再現手順（Steps to Reproduce）

1. `Step(id="implement", skill="issue-implement", agent="codex", timeout=6000, on={"PASS": "end", "RETRY": "implement", "ABORT": "end"})` を構築する
2. 現行 main の `build_prompt(step, ..., verdict_path=...)` で prompt を生成する
3. `## コンテキスト変数` を確認すると `step_id` / `verdict_path` はあるが、timeout 秒数・deadline が
   ない（OB）。→ 恒久回帰テスト（後述 Small）で FAIL を確認できる
4. interactive runner で verdict を書かずに deadline まで pane を生存させると、進捗によらず
   `StepTimeoutError` になる。実世界ログは Incident #393 の 2 occurrence

## 根本原因（Root Cause）

1. **時間予算の情報が harness 内に閉じている**: deadline は runner の実装詳細として各 backend が
   個別に計算し（`interactive_terminal.py:460` は #224 `9c31e6a`、`interactive_terminal_herdr.py:237`
   は #396 `8ede4e9` で導入。headless timer は初期実装以来）、prompt 生成（`build_prompt`）へは一度も
   渡されていない。agent は残時間を観測できず、「deadline 内に終わらないので区切る」判断が原理的に
   できない
2. **deadline の起点が backend ごとに異なる**: interactive は pane / launcher 起動後、headless は
   `Popen` 後かつ transient retry ごとに再始動。単に prompt へ `now + timeout` を表示するだけでは
   表示値と実 deadline が一致しない（EB 3 を満たせない）。単一計算元が必要
3. **長時間 acceptance の配置規則がない**: #391 の設計は nested full workflow の反復を `implement`
   1 attempt 内に置いたが、設計・設計レビューの rubric に bounded 根拠を問う観点がなかった
4. **skill に期限前 checkpoint の規則がない**: `issue-implement` の RETRY 条件は「テスト失敗等」のみで、
   時間切れ見込み時に進捗を引き継ぐ手順がない

**同根の他箇所調査**: agent prompt 生成経路は `runner.py::_StepExecutor._dispatch()` の 1 箇所に集約
され、headless（`execute_cli`）/ tmux（`execute_interactive_terminal`）/ Herdr
（`execute_interactive_terminal_herdr`、tmux 入口から委譲）の 3 backend すべてが同じ prompt を受け取る。
よって prompt 側の修正は 1 箇所で 3 経路に及ぶ。hard deadline 側は 3 backend それぞれに独自計算が
あり、3 箇所すべてを単一計算元へ寄せる必要がある。exec / exec_script step は prompt を持たず agent の
判断を伴わないため同根ではない（スコープ外。後述）。

## インターフェース

### 新規: `AttemptDeadline`（内部 API）

新モジュール `kaji_harness/deadline.py`（runner / cli / interactive_terminal* のいずれからも循環なく
import できる葉モジュール）に置く。

```python
@dataclass(frozen=True)
class AttemptDeadline:
    timeout_seconds: int          # 解決済み step timeout（_ExecutionSettings.timeout）
    started_at: datetime          # attempt 開始の UTC wall clock（tz-aware）
    started_monotonic: float      # 同時刻の time.monotonic()

    @classmethod
    def start(cls, timeout_seconds: int) -> AttemptDeadline: ...
        # datetime.now(UTC) と time.monotonic() を連続取得して生成（唯一の生成経路）

    @property
    def deadline_monotonic(self) -> float: ...   # started_monotonic + timeout_seconds（hard deadline）
    @property
    def deadline_at(self) -> datetime: ...       # started_at + timeout_seconds（表示用）
    def remaining(self) -> float: ...            # max(0.0, deadline_monotonic - time.monotonic())
```

### 変更: `build_prompt()`

```python
def build_prompt(..., verdict_path: str | None = None,
                 attempt_deadline: AttemptDeadline | None = None) -> str
```

`attempt_deadline` が渡された場合のみ、コンテキスト変数と `## 実行期限` 節を出力する
（`verdict_path` と同じ optional パターン。直接呼出し・既存テストの互換を保つ）。runner は agent step で
常に渡す。

追加するコンテキスト変数:

| 変数 | 例 | 定義 |
|------|----|------|
| `step_timeout_seconds` | `6000` | `AttemptDeadline.timeout_seconds` |
| `attempt_started_at_utc` | `2026-10-03T04:27:28Z` | `started_at` を秒未満切り捨て、ISO 8601 UTC（`Z` 表記） |
| `attempt_deadline_utc` | `2026-10-03T06:07:28Z` | `deadline_at` を秒未満切り捨て、同形式 |

秒未満切り捨てにより、表示 deadline は実 deadline 以前（保守側）になる。

追加する `## 実行期限` 節（`## 出力要件` の前。文言は実装で調整可、要件は以下）:

- この attempt の hard deadline は `attempt_deadline_utc`（UTC）で、延長されない。到達時点で
  `verdict_path` が未保存なら harness が session を終了し `StepTimeoutError` として記録する
- 長時間処理の開始前と主要フェーズ境界で `date -u +%Y-%m-%dT%H:%M:%SZ` により残時間を確認し、
  処理・外部副作用・verdict 保存まで deadline 内に完了できない処理を開始しない
- 期限内に完了できない場合の checkpoint 手順と status は skill の規則に従い、上記 status 候補
  （`step.on` のキー）以外を出力しない

RETRY の意味は step ごとに異なる（`implement` は自己ループ、`review-design` は `fix-design` 行き）
ため、共通 prompt では RETRY の意味づけをしない。意味づけは skill（本 Issue では `issue-implement`）が持つ。

### 変更: runner backend へ hard deadline を渡す

3 backend の入口に keyword-only 引数 `deadline_monotonic: float | None = None` を追加する。
`None` の場合は従来どおり各 backend が `time.monotonic() + timeout` を計算する（直接呼出し・既存テストの
互換）。runner は agent step で常に `attempt_deadline.deadline_monotonic` を渡す。

| backend | 変更 |
|---------|------|
| `execute_interactive_terminal`（tmux） | `deadline = deadline_monotonic if not None else time.monotonic() + timeout`。Herdr 委譲時にそのまま転送 |
| `execute_interactive_terminal_herdr` | 同上 |
| `execute_cli` / `_execute_cli_once`（headless） | timer 秒数を `max(0.0, deadline_monotonic - time.monotonic())` とする。transient retry は「backoff 待機後の残時間 > 0」の場合のみ行い、残時間がなければ元の `CLIExecutionError` を再送出する（retry しない） |

`timeout` 引数・`StepTimeoutError(step.id, timeout, ...)` の `timeout` 値（設定秒数）・console の
`pane launched ... timeout=%ds` 表示は変えない。

### 変更: `runner.py::_StepExecutor._dispatch()`（agent 分岐）

```text
deadline = AttemptDeadline.start(settings.timeout)
prompt = build_prompt(..., verdict_path=..., attempt_deadline=deadline)
write prompt.txt
attempt_started_at_ref.append(deadline.started_at)   # result.json started_at と同じ起点
execute_*(..., timeout=settings.timeout, deadline_monotonic=deadline.deadline_monotonic)
```

これにより「prompt 表示」「hard deadline」「result.json の `started_at`」が同じ 1 回の時刻取得に揃う
（完了条件 1 の単一計算元 = `AttemptDeadline.start()`、呼出し元 = `_dispatch()`）。

### 出力（副作用）

- `prompt.txt` に 3 変数と `## 実行期限` 節が増える
- interactive backend の hard deadline の起点が「pane / launcher 起動後」から「attempt 開始（prompt
  生成直前）」へ前倒しになる（差は起動所要時間＝通常数秒、Herdr launcher 待ちを含め最大数十秒）
- headless の transient retry は attempt deadline の残時間内に限られる（従来は retry ごとに full timeout）

### 使用例（agent 側から見た prompt 抜粋）

```text
## コンテキスト変数
- step_id: implement
- verdict_path: /…/steps/implement/attempt-001/verdict.yaml
- step_timeout_seconds: 6000
- attempt_started_at_utc: 2026-10-03T04:27:28Z
- attempt_deadline_utc: 2026-10-03T06:07:28Z

## 実行期限
この attempt の hard deadline は 2026-10-03T06:07:28Z（UTC）です。…
```

## 変更スコープ

| 種別 | ファイル |
|------|----------|
| 実装 | `kaji_harness/deadline.py`（新規）、`kaji_harness/prompt.py`、`kaji_harness/runner.py`、`kaji_harness/cli.py`、`kaji_harness/interactive_terminal.py`、`kaji_harness/interactive_terminal_herdr.py` |
| テスト | `tests/test_attempt_deadline.py`（新規）、`tests/test_attempt_deadline_e2e_large_local.py`（新規）、`tests/test_prompt_builder.py`、`tests/test_runner_interactive_dispatch.py`、`tests/test_interactive_terminal.py`、`tests/test_interactive_terminal_herdr.py`、`tests/test_cli_streaming_integration.py` |
| skill | `.claude/skills/issue-design/SKILL.md`、`.claude/skills/issue-review-design/SKILL.md`、`.claude/skills/issue-implement/SKILL.md`（`.agents/skills/*` は `.claude/skills/*` への symlink のため実体は 1 箇所） |
| docs | 「影響ドキュメント」参照 |

**スコープ外**:

- exec / exec_script step: agent 判断を伴わず prompt もない。timeout 挙動は不変
- workflow YAML の `timeout` 値・新 YAML field・cycle `max_iterations`: 不変（完了条件・Issue 重要判断）
- #393 処遇提案 3（timeout 時 session ID 保存）は #403 で対応済み。本 Issue は退行させないことのみ担保
- #393 処遇提案 4（KeyboardInterrupt 時の COMPLETE 記録）: 別 bug
- #391 由来の dogfood を実際に独立 step へ移す workflow 変更: 本 Issue は規則の追加まで

## 方針

### A. harness（runner / prompt / backend）

最小侵襲で「単一計算元を作って配る」だけにとどめる。deadline の判定ロジック（polling ループ、
timer kill、timeout 例外、#403 の session 解決）は変えず、起点となる値の供給元だけを差し替える。
`deadline_monotonic=None` の fallback を残すことで、backend の単体テスト（`time.monotonic` を
`side_effect=[0.0, 1.0]` で差し替える既存テスト等）の呼出し回数・挙動を変えない。

wall clock と monotonic の関係: hard deadline は従来どおり monotonic で判定する。表示 deadline は
同時刻に取得した wall clock から算出するため起点は同一だが、実行中に wall clock が monotonic から
ずれると（#393 では wall clock が約 10% 速く進んだ）agent が `date -u` で見る残時間は推定値になる。
速くずれる場合は早めに checkpoint する保守側、遅くずれる場合の誤差は skill 側の予備時間（後述 C）で
吸収する。この限界は docs に明記する。

### B. 設計段階の配置規則（`issue-design` / `issue-review-design`）

`issue-design` の設計書テンプレート「テスト戦略」に **「長時間 acceptance の配置」** 小節を追加する。

- **対象**: nested full workflow（kaji run の入れ子実行）、外部 agent を含む dogfood、修正ごとに
  fresh 環境で繰り返す acceptance、その他 1 回の所要見積が対象 step timeout の 25% を超える検証
- **要求**（対象がある場合、どちらか一方を必須）:
  - (a) **bounded 根拠**: 実行回数の上限 × 1 回の所要見積（根拠: 過去 run の実測など）＋予備時間
    ≤ 配置先 step の timeout であることを数値で示す。失敗→修正→再実行の回数上限を含める
  - (b) **分離**: 独立 acceptance step、別 workflow、または別 Issue へ分離し、分離先を明記する
- 分離に必要な workflow 変更が Issue のスコープ外になる場合は、設計 agent が選ばず
  `critical-decision-checklist.md` の「スコープ変更」軸として `ABORT` し、人間に分離方式を確認する
- 対象がない場合は「該当なし」と根拠を記載する（節の省略禁止）

`issue-review-design` の「レビュー基準 4. 検証可能性」に同観点のチェックを追加し、対象があるのに
(a)(b) のいずれもない設計は Changes Requested（`RETRY`）とする。

### C. 実装段階の残時間確認と checkpoint（`issue-implement`）

`issue-implement` に以下を追加する（新 Step「実行期限の確認」を Step 1 の直後に置き、各 Step から参照）。

1. **開始時**: コンテキスト変数 `attempt_deadline_utc` / `step_timeout_seconds` を記録する。
   変数がない（手動実行等）場合は本規則を適用しない
2. **前 attempt の checkpoint の引継ぎ**: body 1 行目に `<!-- kaji-verdict: step=implement status=RETRY -->`
   を持つ直近コメント（checkpoint コメント）があり、それ以降に implement の PASS / BACK 等がなければ、
   その「残作業」から再開する（implement の self-RETRY は `resume:` を持たず fresh session のため、
   引継ぎ情報は Issue コメントと worktree が唯一の経路）
3. **予備時間** R = max(300 秒, `step_timeout_seconds` の 10%)。commit・Issue コメント・verdict 保存に
   充てる
4. **確認点**: (i) 全 pytest / `make check` / nested workflow / 外部 agent 呼出しなど長時間処理の開始前、
   (ii) Step 3〜8.5 の各境界。`date -u` で残時間を算出し、「見積所要時間 + R > 残時間」なら新たな
   長時間処理を開始せず checkpoint へ移る。見積は同 session / Issue コメント上の実測を優先し、
   なければ保守的に見積もる
5. **設計との関係**: 設計書が長時間 acceptance を implement に置きながら bounded 根拠を持たない場合、
   それは設計起因として `BACK` を返す（`implement` の `BACK: design` は全 workflow に存在）
6. **checkpoint 手順**（verdict-last を維持）:
   - `make check` が通る単位の変更は通常どおり commit する。通らない WIP は commit せず worktree に
     残す（AGENTS.md の「コード変更 commit 前に `make check` 必須」を破らない）
   - checkpoint コメントを `kaji issue comment [issue_id] --verdict-step implement --verdict-status RETRY`
     で投稿する。内容: 完了済み作業（commit SHA）、未 commit 差分の概要、実行中だった検証と結果、
     残作業と次 attempt の開始手順、確認時の残時間
   - その後に verdict（`status: RETRY`、`reason` に期限前 checkpoint である旨）を stdout と
     `verdict_path` へ保存する
7. **status 制約**: `RETRY` は prompt の status 候補に含まれる場合のみ使う。含まれない場合は checkpoint
   コメントを投稿した上で `ABORT`（suggestion に `kaji run … --from implement` での再開を記載）とし、
   存在しない status は出力しない
8. **cycle 上限**: checkpoint の `RETRY` も `implementation` cycle のカウント対象であり、上限到達時は
   runner が `on_exhaust: ABORT` で停止する。上限回避のために `PASS` 等へ status を偽らない。
   `cycle_count` == `max_iterations` の attempt でも同手順で `RETRY` を返す（再開は人間が
   `--reset-cycle` で判断）

### D. docs

`docs/dev/workflow-authoring.md` に「timeout と attempt deadline」節を追加し（起点・hard deadline・
延長なし・headless retry の上限・wall clock 推定の限界・長時間 acceptance の配置規則へのリンク）、
`docs/dev/skill-authoring.md` のコンテキスト変数表と skill 作成指針に 3 変数と checkpoint 規則を追記する。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| hard timeout の扱い | hard timeout・cycle 上限・YAML timeout 値を維持し、可視化と期限前 checkpoint で対処 | Issue 本文「重要判断」表（AI の仮定と明記、review-ready PASS で再検査済み）＋ EB 6 ＋ #393 調査「単純な timeout 延長を恒久策にしない」。review-design で再検査 | 判定ロジックは不変、起点の供給元のみ差し替え |
| 公開 workflow schema | 新 YAML field を追加せず `Step.timeout` 解決値から内部変数を導出 | Issue 本文「重要判断」表（AI の仮定）。review-design で再検査 | 変数源を `_ExecutionSettings.timeout`（step → workflow → config の解決済み値）に固定 |
| 単一計算元 | `AttemptDeadline.start()` を `_dispatch()` で 1 回呼び、prompt・hard deadline・`result.json.started_at` に配る | 完了条件 1・3 と EB 3 が要求。実装位置は AI の詳細化。review-design / review-code で検査 | 葉モジュール化、`deadline_monotonic=None` fallback、表示は秒未満切り捨て |
| interactive deadline 起点の前倒し | pane / launcher 起動後 → attempt 開始へ（数秒〜数十秒短くなる） | AI の仮定。EB 3「同じ attempt 開始時刻を基準」を満たすには起点統一が必須で、timeout 値自体は不変。可逆。review-design で検査 | console の `timeout=%ds` 表示と例外の timeout 値は設定秒数のまま |
| headless transient retry の上限 | retry は attempt deadline の残時間内に限る | AI の仮定。従来は retry ごとに full timeout で、表示 deadline と実 deadline が乖離し「無制限ではないが timeout の最大 4 倍超」になる。EB 3・6 との整合を優先。内部挙動で可逆。review-design で検査 | 残時間 0 なら元の `CLIExecutionError` を再送出し、エラー分類を変えない |
| prompt 変数名・形式 | `step_timeout_seconds` / `attempt_started_at_utc` / `attempt_deadline_utc`、ISO 8601 UTC `Z`、秒切捨て | AI の仮定。内部 prompt 契約で skill 文言と同時に変更可能（two-way door）。review-design で検査 | 既存変数の snake_case 慣習に合わせる |
| acceptance 分離規則 | 対象検証は bounded 根拠 (a) か分離 (b) を必須。スコープ外 workflow 変更は ABORT で人間確認 | Issue 本文「重要判断」表（AI の仮定）＋ #393 調査の恒久対策候補 1 ＋ EB 1。review-design で検査 | 対象の定義（25% 閾値を含む）、(a) の数値要件、review-design の RETRY 条件 |
| checkpoint の適用範囲 | `issue-implement` と共通 prompt。他 skill への個別規則追加はしない | Issue 本文「重要判断」表（AI の仮定）。review-code で検査 | 共通 prompt は RETRY の意味づけをせず skill に委ねる |
| checkpoint の手順・予備時間 | R = max(300s, 10%)、WIP は make check 不通過なら commit しない、RETRY 不可時は ABORT | AI の仮定。AGENTS.md の commit 前 `make check` 契約と ADR 005 verdict-last を破らない範囲で具体化。skill 文言で可逆。review-design / review-code で検査 | checkpoint コメントの記載項目、次 attempt の引継ぎ検出（verdict marker） |
| source of truth | #393 実障害調査、現行 main の runner / prompt 実装、ADR 005・workflow-authoring の既存契約 | Issue 本文「重要判断」表。相互矛盾なしを本設計の調査でも確認 | — |

one-way door の未決: なし。公開 CLI / workflow YAML schema / 永続化形式は変更せず、prompt 変数と skill
文言は内部契約として後段で安く直せる。

## テスト戦略

### 変更タイプ

実行時コード変更（harness）＋ instruction-only（skill / docs）。

### 実行時コード変更

#### 再現テスト（bug 必須）

`tests/test_prompt_builder.py` に、`attempt_deadline` を渡した `build_prompt()` の出力が
`step_timeout_seconds: 6000` と `attempt_deadline_utc: <started_at + 6000s>` を含むことを assert する
テストを追加する。修正前は `build_prompt()` が `attempt_deadline` 引数を持たず FAIL（TypeError）し、
修正後 PASS する。実世界 OB は #393 のログが別途裏付ける。

#### Small テスト

- `AttemptDeadline`: `deadline_monotonic == started_monotonic + timeout`、`deadline_at == started_at + timeout`、
  `remaining()` が 0 未満にならない、`start()` の `started_at` が tz-aware UTC（`time.monotonic` /
  `datetime.now` を差し替えて固定値で検証）
- `build_prompt()`:
  - 3 変数の値と形式（秒未満切捨て、`Z` 表記）、`## 実行期限` 節の存在
  - `attempt_deadline=None` では 3 変数・節を出さない（直接呼出し互換）
  - 節が status 候補外の status を勧めない: `on` に `RETRY` を持たない step の prompt に `RETRY` の
    文字列が現れない（status 候補行・期限節とも）
- 境界: timeout が小さい値（例 1 秒）でも deadline 算出・表示が破綻しない

#### Medium テスト

- **runner の単一時間基準**（`tests/test_runner_interactive_dispatch.py`）: `kaji_harness.deadline` の
  `time.monotonic` / `datetime.now` を固定値に差し替え、runner を実行し、
  (1) backend へ渡った `deadline_monotonic == 固定 monotonic + timeout`、
  (2) `prompt.txt` の `attempt_deadline_utc == 固定 now + timeout`、
  (3) `result.json` の `started_at == 固定 now` を、tmux / Herdr / headless の 3 経路で確認する
  （headless は `execute_cli` mock の kwargs で確認）。timeout は step.timeout 指定時と
  workflow / config fallback 時の両方で解決値が使われること
- **backend が渡された deadline を使う**:
  - tmux / Herdr: `deadline_monotonic` を過去値で渡すと polling せず `StepTimeoutError` になり、
    #403 の `session_resolution`（timeout 経路）が従来どおり付与される
  - `deadline_monotonic=None` で既存テスト（`time.monotonic` side_effect 固定）が無変更で通る
- **headless retry**（`tests/test_cli_streaming_integration.py`）: transient error 後、残時間が backoff
  以下なら retry せず元の `CLIExecutionError` を送出する。残時間が十分なら従来どおり retry し成功する。
  `deadline_monotonic` 指定時に timer が残時間で kill し `StepTimeoutError(timeout=設定秒数)` になる
- **#403 非退行**: 既存の timeout session ID / `result.json` 異常終了記録テスト
  （`test_runner_interactive_dispatch.py` の resolution 系、`test_interactive_terminal*.py` の timeout 系、
  `test_verdict_artifact_runner.py`）が green のまま

#### Large テスト（`large` + `large_local`、ネットワーク疎通なし）

#407 の `tests/test_tmp_dir_e2e_large_local.py` と同じ構成（実 `kaji run` + PATH 上の fake agent、
隔離した実 tmux server）で、新規 `tests/test_attempt_deadline_e2e_large_local.py` を追加する。

- **headless・表示値の到達**: 実 `kaji run` + fake `claude`（headless）。fake agent が argv の prompt から
  `step_timeout_seconds` / `attempt_deadline_utc` を読み取って記録し、PASS verdict を返す。記録値が
  step の timeout と一致し、`attempt_deadline_utc == result.json の started_at（秒切捨て）+ timeout` であること
- **headless・hard deadline の一致**: 同構成で step `timeout` を数秒にし、fake agent は verdict を出さず
  sleep する。`StepTimeoutError` が `result.json` に異常終了として記録され、kill 時刻が表示 deadline
  から許容誤差（timer 精度・プロセス終了待ち分）内であること
- **tmux・hard deadline の一致**: 隔離 tmux server 上で `execute_interactive_terminal` を実行し、fake agent
  は verdict を出さず sleep。`deadline_monotonic` に attempt 開始基準の値を渡したとき、pane 起動所要時間を
  加算せずにその時刻で `StepTimeoutError` になること

Herdr の実起動は含めない。理由は #407 と同じく、実 Herdr pane 内からの起動を要し CI / 本 test 環境では
物理的に作成できないため（testing-convention「物理的に作成不可」）。Herdr 側の差分は deadline の供給元
だけで、Medium で `deadline_monotonic` の転送（tmux 入口 → Herdr）と過去 deadline での即時
`StepTimeoutError` を検証する。

### instruction-only（skill / docs）

#### 変更固有検証

- `make verify-docs`（リンク・参照整合）
- skill 文言の検査: `issue-implement` に (i) 確認点、(ii) 予備時間、(iii) checkpoint 手順、
  (iv) RETRY 不可時の扱い、(v) cycle 上限の非迂回が記載され、`issue-design` テンプレートと
  `issue-review-design` 基準 4 に長時間 acceptance 観点があることを review-code で目視確認する
- 既存の skill 構造テスト（`tests/test_skill_migration.py` 等、`make check` に含まれる）が green

#### 恒久テストを追加しない理由（skill 文言部分）

`docs/dev/testing-convention.md`「docs-only / metadata-only / packaging-only 変更」の 4 条件に沿う:

1. 独自ロジックなし: skill / docs の文言は agent の判断手順で、harness の実行時ロジックを含まない
2. 既存ゲートで捕捉: リンク切れは `make verify-docs`、skill 構造は既存 skill テスト、runner が依存する
   機械可読契約（verdict marker、status 候補）は既存テストと上記 Small テスト（status 候補外を勧めない）
   が捕捉する
3. 回帰情報が増えない: 文言の存在を grep する恒久テストは表現の調整を妨げるだけで、規則が機能するかは
   agent の振る舞いでしか判定できない
4. 説明可能: 本節と review-code での目視確認項目（上記 (i)〜(v)）として記録する

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない。ADR 005 の verdict-last を維持（変更しない） |
| docs/ARCHITECTURE.md | なし | 変数一覧は「など」で例示のみ、dispatch 構造（`build_prompt` → `execute_*`）は不変 |
| docs/dev/workflow-authoring.md | あり | 「timeout と attempt deadline」節を追加（起点・hard deadline・延長なし・headless retry 上限・wall clock 推定の限界・長時間 acceptance 配置規則） |
| docs/dev/skill-authoring.md | あり | コンテキスト変数表に 3 変数、skill 作成指針に期限前 checkpoint 規則（status 候補制約・cycle 非迂回） |
| docs/dev/shared_skill_rules.md | あり | 「実行期限と checkpoint（共通）」の短節を追加し skill-authoring へ誘導 |
| docs/dev/development_workflow.md / workflow_guide.md | なし | フェーズ構成・遷移は不変 |
| docs/cli-guides/interactive-terminal-runner.ja.md | あり | timeout の起点が attempt 開始であること、トラブルシュート「step が timeout する」に `attempt_deadline_utc` 確認を追記 |
| docs/reference/configuration.md | なし | timeout 解決順・設定項目は不変 |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| .claude/skills/issue-design, issue-review-design, issue-implement | あり | 方針 B・C（skill 実体。`.agents/skills` は symlink） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Incident #393 原因調査 | https://github.com/apokamo/kaji/issues/393#issuecomment-5372780233 | 2 occurrence とも pane 生存・error 0・verdict 不在で 6002075ms / 6002101ms。恒久対策候補 1「full dogfood を独立 acceptance step へ分離」、2「deadline 残量を agent へ渡し、一定残量で commit/comment の上で正規 RETRY」、5「単純な timeout 延長だけは恒久策にしない」。wall clock が monotonic より約 10% 速く進んだ観測 |
| Incident #393 occurrence 1 / 2 | https://github.com/apokamo/kaji/issues/393#issuecomment-5133438440 / https://github.com/apokamo/kaji/issues/393#issuecomment-5134968724 | 実障害ログ（再現テストの実ログ代替根拠） |
| ADR 005 | `docs/adr/005-artifact-primary-verdict.md` | 「interactive terminal runner では `verdict.yaml` の出現が完了トリガになるため、agent は外部副作用を完了してから最後に artifact を保存する」→ checkpoint でも comment → verdict の順を維持 |
| workflow-authoring | `docs/dev/workflow-authoring.md:263`, `:348-357` | timeout 解決順 step → workflow → config。self-RETRY step は cycle 所属必須で runner は cycle 経由でのみ RETRY 上限を enforce → checkpoint RETRY も上限対象 |
| 現行 prompt 生成 | `kaji_harness/prompt.py:13-118` | 変数は issue / step / verdict_path / cycle / previous_verdict のみ。status 候補は `step.on` のキー |
| 現行 dispatch | `kaji_harness/runner.py:460-590` | `settings.timeout` 解決、agent 分岐で prompt 生成後に `attempt_started_at` 記録、backend へ timeout を個別に渡す |
| 現行 hard deadline | `kaji_harness/interactive_terminal.py:460`, `kaji_harness/interactive_terminal_herdr.py:237`, `kaji_harness/cli.py:220-287` | 各 backend が起動後に独自計算。headless は retry ごとに timer 再始動 |
| #403 timeout session 解決 | `kaji_harness/interactive_terminal.py:545-560`, `docs/cli-guides/interactive-terminal-runner.ja.md:290-310` | timeout 経路で kill 前に 1 回 session 解決 → 非退行の検証対象 |
| workflow YAML の implement 定義 | `.kaji/wf/official/dev.yaml`, `.kaji/wf/official/local/dev-local.yaml`, `.kaji/wf/custom/dev/dev-thorough*.yaml` | 全 workflow の `implement` が `RETRY: implement` / `BACK: design` を持ち `implementation` cycle（max 3, ABORT）に所属。`timeout: 6000` は dev-thorough-codex / fable |
| AGENTS.md | `AGENTS.md` | 「コード変更を含む commit の前に `make check` を必ず通す」→ WIP の扱い |
| 重要判断チェックリスト | `.claude/skills/_shared/critical-decision-checklist.md` | 「スコープ変更」は one-way door 軸 → 分離に Issue 外の workflow 変更が要る場合は ABORT |
| Python datetime | https://docs.python.org/3/library/datetime.html#datetime.datetime.isoformat | tz-aware UTC の ISO 8601 表記（`timespec="seconds"`）。`Z` 表記は `strftime("%Y-%m-%dT%H:%M:%SZ")` で生成 |
| Python time.monotonic | https://docs.python.org/3/library/time.html#time.monotonic | 「The clock is not affected by system clock updates」→ hard deadline は monotonic で判定し、表示用 wall clock とは同時刻取得で起点のみ揃える |
