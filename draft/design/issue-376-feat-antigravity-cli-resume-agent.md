# [設計] Antigravity CLI を resume 非対応 agent として追加する

Issue: #376

## 概要

`kaji_harness/` に公開 agent 名 `antigravity`（実行 binary: `agy`）を追加し、headless（`agy -p`）と
interactive terminal（`agy -i`）の単発 workflow step 実行を限定正式対応する。未対応 capability
（`resume:` / session ID / JSONL progress / token・cost）は agent capability registry と workflow
validation で実行前に決定的に拒否し、実装・tests・利用者向け docs を同一 Issue 内で一体更新する。

## 背景・目的

Google は個人 / free tier を Gemini CLI から Antigravity CLI へ移行した（2026-06-18、
https://github.com/google-gemini/gemini-cli/discussions/27274 ）。しかし kaji は `agy` を agent
として実行できない。AGY v1.1.6 は headless / interactive を提供する一方、新規 conversation ID や
JSONL event を公開 stdout contract として提供しないため、既存 Gemini adapter の置換や `resume:`
の黙認では安全で検証可能な対応にならない（#267 実機調査）。

**ユーザーストーリー**: kaji 利用者として、Google の現行 agent CLI である Antigravity CLI を
headless または interactive terminal から単発 workflow step として実行し、未対応の `resume:` は
実行前に明確な validation error として検出したい。これにより、対応範囲を誤認せずに AGY を
workflow へ組み込める。

**代替案と不採用理由**（#267 調査で棄却済み）:

- Gemini adapter の CLI 名置換 / alias: 出力契約（JSONL vs plain text）と session contract が別物。
- `--log-file` から conversation UUID を scraping して `resume:` 対応: 非公開ログ形式への依存で、
  無告知の形式変更・並列実行・ログ欠落で壊れるため正式契約に採用しない。
- `resume:` の黙殺（実行時 `MissingResumeSessionError` へ遅延）: 対応範囲の誤認を招く。
  validation で fail-fast する。

## インターフェース

### 入力

**workflow YAML（利用者契約）**:

| フィールド | 型 | antigravity での扱い |
|-----------|-----|---------------------|
| `agent: antigravity` | str | 有効な agent として受理（`kaji validate` / run 前 preflight 双方） |
| `model` | str（任意） | `--model <値>` として passthrough（値の列挙は AGY 側に委譲、検証しない） |
| `effort` | str（任意） | 許容値 `low` / `medium` / `high`。範囲外は parse 時エラー（既存 `_AGENT_EFFORT_ALLOWED` 方式） |
| `resume: <step>` | str | **validation error**。エラーメッセージに step 名・agent 名・未対応 capability（`resume`）を含める |
| `timeout` / `workdir` / `inject_verdict` | — | agent 非依存の既存契約のまま |
| `max_budget_usd` | float | claude 専用 flag のため無視（codex / gemini と同じ既存挙動。本 Issue では変更しない） |

**workflow `execution_policy` → AGY 引数 mapping（headless / interactive 共通表）**:

| execution_policy | headless argv flag | interactive argv flag | 意味 |
|------------------|-------------------|----------------------|------|
| `auto` | `--dangerously-skip-permissions` | `--dangerously-skip-permissions` | tool 実行を自動承認（claude の `--permission-mode bypassPermissions` 相当）。headless / interactive とも agent は承認待ちで停止しない |
| `sandbox` | `--sandbox` | `--sandbox` | containment のみ。permission（approval）は AGY default のまま。headless では soft-deny され得る。interactive では TUI approval で人間が承認できる |
| `interactive` | （flag なし） | （flag なし） | AGY default。headless では対話承認できないため tool は soft-deny され得る（下記「制約」参照）。interactive では TUI approval |

sandbox と permission は AGY では別軸の概念であり（https://antigravity.google/docs/cli/permissions
の permission engine 定義と、`agy --help` の `--sandbox` / `--dangerously-skip-permissions` が独立
flag であること）、`sandbox` policy に承認 bypass を混ぜない。これは codex の `sandbox` →
`-s workspace-write`（bypass なし）と同じ構造。headless / interactive で **flag mapping を同一**に
することで、policy の意味が runner backend によって変わらないことを契約とする。interactive で
`sandbox` / `interactive` policy の approval が TUI に出た場合は人間が pane 上で承認する
（#267 capability 表「tool 実行: TUI approval または自動承認 policy」のとおり）。

**headless argv（`_build_antigravity_args` が構築）**:

```
agy -p <prompt> [--model <m>] [--effort <low|medium|high>] [<policy flag>]
```

- `-p` は prompt 文字列を引数に取る（gemini 型。claude の flag 型 `-p` とは異なる）
- workdir は `subprocess.Popen(cwd=workdir)` で与える（claude と同様。専用 flag 不要）
- `session_id` は validation により常に `None`。防御として `session_id` 非 None は assert で拒否

**interactive terminal（wrapper.sh の antigravity case）**:

```
agy [<policy flag>] [--model <m>] [--effort <e>] -i <initial_prompt>
```

- `<policy flag>` は上記 mapping 表の interactive 列（`auto` → `--dangerously-skip-permissions`、
  `sandbox` → `--sandbox`、`interactive` → なし）
- `execution_policy` は runner → `execute_interactive_terminal` → wrapper 第 9 位置引数として
  伝播する（下記「方針 4」）。wrapper で antigravity case のみが消費し、既存 claude / codex case の
  argv は変更しない（両者の policy mapping 見直しは本 Issue の scope 外）
- wrapper 第 5 引数 `resume_session_id` が非空なら `exit 2`（fail-loud。validation 上到達しない防御）
- `launch_session_id` は claude 専用のまま（antigravity では常に空）

### 出力

**headless（`CLIResult` 契約）**:

| フィールド | antigravity での値 |
|-----------|-------------------|
| `full_output` | stdout の plain text 全行。行の保持契約: 各行は**行終端（`\n` / `\r\n`）のみを除去**し、先頭・末尾の空白と空行を含め原文のまま保持して `"\n".join` する。JSON として parse 可能な行も加工せず text として収集する（既存の非 JSON 行経路が行う `strip()` + 空行 skip は plain-text mode では適用しない） |
| `session_id` | 常に `None`（新規 conversation UUID は公開 stdout contract に存在しない） |
| `cost` | 常に `None`（token / cost metadata は取得不能） |
| `terminal_seen` / `terminal_failure` | 常に `False`（terminal event 契約なし） |
| `exit_code` / `stderr` | process の実値。失敗判定は「terminal event なし」経路の既存規約（exit != 0 → `CLIExecutionError`、detail に stderr） |

**interactive terminal**: 既存契約を維持。完了トリガは `verdict.yaml` の出現のみ。
`CLIResult(full_output="", session_id=None)` を返す（codex 型の session ID 抽出は行わない）。

**verdict 解決**: headless は plain stdout（`---VERDICT---` block）→ AI formatter fallback、
interactive は `verdict.yaml`。AI formatter は `agy -p <formatter_prompt> [--model <m>]` で起動する
（gemini case と同型。整形タスクは tool 不要のため permission flag は付けない）。

**runner の state**: `result.session_id` が `None` のため session 保存は発生しない（既存の
`if result.session_id:` ガードで自然に成立。runner 変更不要）。

### 使用例

```yaml
# 許可: 単発 step
execution_policy: auto
steps:
  - id: investigate
    skill: some-skill
    agent: antigravity
    model: gemini-3-pro
    effort: high
    on:
      PASS: end
```

```yaml
# 拒否: resume 指定
steps:
  - id: implement
    skill: issue-implement
    agent: antigravity
    resume: design      # ← validation error
```

期待エラー（`kaji validate` / `kaji run` preflight 共通）:

```
Step 'implement' uses agent 'antigravity' which does not support 'resume'
(agent 'antigravity' capabilities do not include: resume)
```

（文言は実装時に調整可。**step 名・agent 名・未対応 capability 名の 3 要素を含む**ことが契約）

### エラー

| 事象 | 挙動 |
|------|------|
| `agy` 未インストール | `CLINotFoundError`（既存共通経路） |
| `agy` が非 0 exit | `CLIExecutionError(step_id, returncode, stderr)`。stderr が transient pattern に一致すれば既存 backoff retry |
| headless soft-deny（tool 承認要求 → 拒否、exit 0） | CLI 層は成功扱い（AGY の仕様どおり exit 0）。出力に verdict block がなければ既存の verdict 解決機構（`VerdictNotFound` → RETRY 系）が fail-loud する。kaji は special-case しない |
| `resume:` 指定 | 上記 validation error（実行前）。`MissingResumeSessionError` へ到達させない |
| timeout | `StepTimeoutError`（既存共通経路） |

## 制約・前提条件

- 一次検証環境は AGY v1.1.6（#267 実機調査）。JSON / stream JSON 出力 option なし、新規
  conversation UUID は stdout / stderr に出ない。`--log-file` は診断用で公開契約ではない
- AGY v1.1.3+ は headless で対話承認が必要な tool を soft-deny し、必要な allow rule を stderr に
  出す。**tool が拒否され回答が生成されなくても exit 0 になり得る**（#267 実機確認）。この挙動は
  kaji 側で検出・救済せず、docs と capability matrix に仕様として明記する
- sandbox（containment）と permission（tool approval）は別軸。既存 Gemini の引数を機械的に
  移植しない
- interactive terminal の既存契約（tmux pane lifecycle・`verdict.yaml` 完了トリガ・timeout・
  terminal.log・pane-metadata.json）は変更しない
- Gemini CLI 関連コード・tests・docs の削除は #377 の scope。本 Issue では gemini の挙動を
  一切変更しない（`GeminiAdapter` / `_build_gemini_args` / docs の gemini 記載は現状維持）
- AGY session ID を非公開 log・conversation DB・内部 storage から抽出する実装は scope 外
- module 境界は ADR 009 に従う（package 跨ぎ private import 禁止、層方向は
  `tests/test_layer_imports.py` が機械検証）

## 変更スコープ

| ファイル | 変更内容 |
|---------|---------|
| `kaji_harness/agents.py`（新規） | agent capability registry（下記「方針」） |
| `kaji_harness/adapters.py` | `AntigravityAdapter` 追加、`ADAPTERS["antigravity"]`、protocol へ `parses_stdout_as_jsonl()` 追加 |
| `kaji_harness/cli.py` | `_build_antigravity_args` 追加、`build_cli_args` dispatch、`stream_and_log` の plain-text 分岐 |
| `kaji_harness/workflow.py` | `VALID_AGENTS` / effort 許容値を registry 参照へ、`validate_workflow` に resume capability 検査追加 |
| `kaji_harness/interactive_terminal.py` | 対応 agent 判定を registry 参照へ（`antigravity` 追加、gemini は従来どおり非対応）。`execute_interactive_terminal` / `_launch_pane` に `execution_policy` パラメータを追加し wrapper 第 9 位置引数として伝播 |
| `kaji_harness/assets/interactive-terminal/wrapper.sh` | 第 9 位置引数 `execution_policy`（省略時空文字）を追加。`antigravity` case 追加（policy 3 分岐の flag 付与、resume 引数は exit 2）。claude / codex case の argv は不変 |
| `kaji_harness/verdict.py` | `_build_formatter_cli_args` に `antigravity` case、docstring の agent 列挙更新 |
| `kaji_harness/runner.py` | `execute_interactive_terminal` 呼び出しに `execution_policy=self.workflow.execution_policy` を追加（headless 経路と同じ値の受け渡しのみ） |
| `tests/test_layer_imports.py` | `MODULE_LAYERS` に `"kaji_harness.agents": "foundation"` を追加（未分類 module は `layer_of()` が `ValueError` で fail-loud し `make check` を通過できないため必須） |
| `tests/`（その他） | 下記「テスト戦略」 |
| docs 群 | 下記「影響ドキュメント」 |

`kaji_harness/runner.py` の変更は上記の引数追加 1 点に限定する。session 保存・resume 解決・
formatter 生成は既存ガードで antigravity に自然対応することを実装時に確認する。それ以外の差分が
必要になった場合は設計逸脱として review-code で検査する。

## 方針

### 1. agent capability registry（一元管理）

#267 の設計要請「agent capability を辞書・型などで一元管理し、個別 validator に文字列条件を
散在させない」に従い、新規 module `kaji_harness/agents.py`（foundation 層、stdlib のみに依存）に
frozen dataclass の registry を置く。

```python
@dataclass(frozen=True)
class AgentCapabilities:
    binary: str                      # 表示・診断用の実行 binary 名
    supports_resume: bool            # False → validate_workflow が resume: を拒否
    supports_interactive_terminal: bool
    emits_jsonl: bool                # False → stream_and_log が全行を plain text 扱い
    effort_allowed: frozenset[str] | None  # None → 検証 skip（従来の辞書未登録と同義）

AGENT_CAPABILITIES: dict[str, AgentCapabilities] = {
    "claude":      AgentCapabilities("claude", True,  True,  True,  frozenset({"low","medium","high","xhigh","max"})),
    "codex":       AgentCapabilities("codex",  True,  True,  True,  frozenset({"none","minimal","low","medium","high","xhigh"})),
    "gemini":      AgentCapabilities("gemini", True,  False, True,  None),
    "antigravity": AgentCapabilities("agy",    False, True,  False, frozenset({"low","medium","high"})),
}
```

- `workflow.py` の `VALID_AGENTS` と `_AGENT_EFFORT_ALLOWED` は registry から導出（現行挙動を
  完全維持: gemini の effort 検証 skip を含む）
- `interactive_terminal.py` の `{"claude", "codex"}` hardcode を
  `supports_interactive_terminal` 参照へ置換（gemini の interactive 非対応という現行挙動を
  registry に明文化する。gemini 自体の削除は #377）
- 将来 AGY が公開 machine-readable session contract を提供した場合、`supports_resume` を
  True にして headless 引数と session 抽出を実装する拡張点になる

### 2. validation（resume 拒否）

`validate_workflow`（`kaji_harness/workflow.py`）の per-step 検証に capability 検査を追加する。
呼び出し経路は `commands/validate.py`（`kaji validate`）と `preflight.py` →
`runner.py`（`kaji run` の実行前 preflight）の双方が `validate_workflow` を共有しているため、
**1 箇所の追加で両経路が同時に満たされる**。既存の「resume 先の存在・同一 agent」検査
（`on` 不正時の分岐にも重複して存在する）とは独立に、`on` の妥当性に関わらず必ず実行される
位置に置く。

### 3. headless 実行（plain-text stream mode）

`CLIEventAdapter` protocol に `parses_stdout_as_jsonl() -> bool` を追加する。
`stream_and_log` は adapter がこれを `False` と返す場合、行ごとの `json.loads` を行わず
**全行を text として収集**する（session / cost / terminal event 検出なし）。

理由: 現行実装は「JSON parse 失敗行のみ text 収集」のため、AGY の plain stdout に偶然 JSON として
parse 可能な行（例: agent が JSON snippet を回答した場合）が含まれると、`AntigravityAdapter` の
extract 系が全て `None` を返して **その行が `full_output` から欠落**する。plain-text mode は
この silent data loss を構造的に排除する。

`AntigravityAdapter` は protocol 充足のため extract 系は常に `None` / terminal 系は常に `False` を
返す（plain-text mode では呼ばれない）。失敗判定は `terminal_seen=False` 経路の既存規約
（exit code / stderr）に委ねる。

### 4. interactive terminal（execution_policy の伝播）

現行の interactive 経路は `execution_policy` を受け取らず、wrapper が claude / codex に常時
承認 bypass flag を付けている。antigravity では #267 の決定（interactive でも permission /
sandbox を別軸で mapping する）に従い、policy を argv まで伝播する:

```
runner._dispatch (workflow.execution_policy)
  → execute_interactive_terminal(..., execution_policy=...)   # 新パラメータ
  → _launch_pane(..., execution_policy=...)
  → wrapper.sh 第 9 位置引数（省略時空文字 = 既存呼び出しとの互換）
  → antigravity case が mapping 表どおりに flag を付与（claude / codex case は不変）
```

`wrapper.sh` の `antigravity` case は既存 claude / codex case と同じ `printf %q` quoting 規約で
argv を組み立てる。`verdict.yaml` polling・pane lifecycle・timeout は共通実装のまま変更しない。
claude / codex の wrapper argv（常時 bypass）は現状維持とし、両 agent の policy mapping 見直しが
必要なら別 Issue とする（本 Issue の scope 境界）。

### 5. データフロー（headless）

```
workflow YAML → load_workflow (parse: effort 検証)
             → validate_workflow (agent 名 / resume capability)  ← kaji validate / run preflight 共通
             → runner._execute_step → execute_cli
             → _build_antigravity_args → Popen(agy -p ...)
             → stream_and_log (plain-text mode) → CLIResult(full_output, session_id=None, cost=None)
             → resolve_verdict (stdout block → AI formatter[agy -p] fallback)
```

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| support level | resume 非対応を明示した限定正式対応 | #267 maintainer 承認コメント（2026-07-24「確定した重要判断」表）・#376 本文「重要判断」表（人間決定） | capability registry の `supports_resume=False` として型で固定 |
| 公開 agent 名 / binary | `antigravity` / `agy` | #267 検討コメントへの maintainer 承認・#376 本文（人間決定） | `AGENT_CAPABILITIES` の key と `binary` フィールドに反映 |
| runner | headless (`agy -p`) と interactive terminal (`agy -i`) の双方 | #267 maintainer 承認コメント（人間決定） | argv 構築・wrapper case・対応 agent 判定の 3 箇所を特定 |
| resume | 非対応。YAML validation で拒否し黙って無視しない | #267 maintainer 確認「yamlのvalidationでチェック」+ 承認（人間決定） | `validate_workflow` への 1 箇所追加で `kaji validate` / run preflight 両経路を担保（呼び出し経路を実装調査で確認済み） |
| completion contract | headless は plain stdout / exit、interactive は `verdict.yaml` | #267 調査結果 + maintainer 承認（人間決定。「詳細な内部構造は設計で決定」） | plain-text stream mode（`parses_stdout_as_jsonl()=False`）で JSON 風行の欠落を防止。soft-deny は special-case せず verdict 解決層で fail-loud |
| session ID / JSONL / token・cost 非取得 | result・tests・docs で一貫して非対応 | #267 決定事項・#376 完了条件（人間決定） | `CLIResult` の `session_id=None` / `cost=None` / `terminal_seen=False` を契約化し tests で固定 |
| docs 一体更新 | 実装・tests・利用者向け docs を同一 Issue/PR で整合 | #267 maintainer コメント「ドキュメントとプログラムは一体」（人間決定） | 影響ドキュメント表に列挙 |
| effort 許容値 | `low` / `medium` / `high` | ローカル実機 `agy --help`（v1.1.6、2026-07-24 取得。Primary Sources に出力を引用）の `--effort ... (low\|medium\|high)` と #267 実機確認表で確認済み | `effort_allowed=frozenset({"low","medium","high"})` |
| permission / sandbox mapping | headless / interactive とも同一 flag mapping（`auto` → `--dangerously-skip-permissions` / `sandbox` → `--sandbox` / `interactive` → flag なし）。`sandbox` に承認 bypass を混ぜない | 人間決定: #267 capability 表「sandbox: 対応（permission と別途設計）」「tool 実行: TUI approval または自動承認 policy」（interactive を含む双方の mapping 設計・検証の要求）。詳細化は AI: 3 policy への具体割当。根拠: AGY 公式 permissions docs の別軸定義 + `agy --help` で両 flag が独立に存在 + codex の `sandbox` mapping（bypass なし）と構造一致。検査先: 実装時の wrapper / argv tests（Small・Medium）、および実機 smoke（事後確認項目） | headless / interactive 共通 mapping 表と runner → wrapper の `execution_policy` 伝播経路（方針 4）に反映。soft-deny の可能性（headless の非 auto policy）を docs 明記で補完 |
| capability registry の配置 | 新規 `kaji_harness/agents.py`（foundation 層、stdlib のみに依存） | AI の仮定。根拠: workflow / cli / interactive_terminal / verdict の複数 module が参照するため foundation 層に置き循環を回避（ADR 009 の層規約）。検査先: `tests/test_layer_imports.py` の `MODULE_LAYERS` へ `"kaji_harness.agents": "foundation"` を追加（未分類は fail-loud）した上で、foundation の内部 import 禁止検査（`test_runtime_imports_follow_layer_direction`）が機械検証する | registry 構造とフィールドを定義。layer mapping 追加を変更スコープに明記 |
| Antigravity guide は英語のみ新設 | `.ja.md` は作らない | AI の仮定。根拠: #376 完了条件が「英語正本として追加」のみを要求。既存 guide の ja 対訳は #264（docs 英語化 EPIC）系の慣行だが本 Issue の完了条件外。検査先: review-design（scope 判断として） | docs 索引には英語版のみ登録 |
| `max_budget_usd` の扱い | antigravity では無視（既存 codex / gemini と同挙動） | AI の仮定。根拠: 本 Issue の fail-fast 要求は `resume:` のみが対象（#376 完了条件）。codex / gemini も現状 silent ignore であり、agent 別の budget capability 検証は本 Issue の scope 外。検査先: review-design | 変更なし（現状維持）を明記 |

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。
> 詳細は [テスト規約](../../docs/dev/testing-convention.md) 参照。

### 変更タイプ

実行時コード変更（agent 実行・validation・interactive terminal の振る舞い追加）+ 利用者向け docs 更新。

### Small テスト

外部依存なしの純粋ロジック検証:

- **capability registry**: `AGENT_CAPABILITIES` の 4 agent 定義、`VALID_AGENTS` / effort 許容値の
  導出が現行値と一致すること（claude / codex / gemini の後方互換固定）
- **validation**: `agent: antigravity` + `resume:` が step 名・agent 名・capability 名を含む
  エラーになること（`on` 不正の step でも検出されること）。`resume:` なし step が PASS すること。
  `agent: gemini` + `resume:` は従来どおり許容されること（#377 まで非破壊）
- **effort 検証**: `antigravity` の `low`/`medium`/`high` 受理、`xhigh` 等の拒否（parse 時エラー）
- **argv 構築**: `_build_antigravity_args` の全分岐（model / effort 有無 × execution_policy 3 値、
  prompt の位置、`session_id` 非 None の assert）
- **formatter argv**: `_build_formatter_cli_args("antigravity", ...)` が `agy -p <prompt>`
  （+ `--model`）を返すこと
- **adapter**: `AntigravityAdapter` の extract 系が `None` / terminal 系が `False` /
  `parses_stdout_as_jsonl()` が `False` を返すこと（既存 3 adapter は `True` を返すこと）
- **layer 分類**: `MODULE_LAYERS` に `kaji_harness.agents` が foundation として分類され、
  既存の layer fitness test（未分類 fail-loud・foundation の内部 import 禁止・stale entry 検査）が
  `make check` で通ること

### Medium テスト

subprocess・ファイル I/O 結合（fake `agy` 実行ファイルを PATH に置く既存 fixture パターン）:

- **plain stdout 取り込み**: fake agy が plain text（**JSON として parse 可能な行・空行・
  先頭 / 末尾に空白を持つ行を含む**）を出力 → `full_output` に全行が「行終端のみ除去・原文保持」
  契約どおり欠落なく収集され、`session_id=None` / `cost=None` / `terminal_seen=False` であること
- **失敗判定**: fake agy が stderr + 非 0 exit → `CLIExecutionError`（detail に stderr）。
  transient pattern を含む stderr で backoff retry が動くこと
- **soft-deny 契約**: fake agy が exit 0 + 空 stdout → CLI 層は例外なく `CLIResult` を返し、
  verdict 解決層で `VerdictNotFound` 系の fail-loud になること（黙って PASS しない）
- **interactive terminal**: tmux mock / 既存 test seam で `agent: antigravity` の pane 起動 argv
  （wrapper への引数列に `execution_policy` が第 9 位置引数として渡ること）と `verdict.yaml` 検知
  → `session_id=None` の返却。gemini が引き続き `ValueError` になること。runner →
  `execute_interactive_terminal` へ `workflow.execution_policy` が渡ることの dispatch 検証
- **wrapper.sh**: bash 直接実行で antigravity case の exec コマンド文字列（quoting 含む）を
  **execution_policy 3 分岐（auto / sandbox / interactive）すべて**について検証し、
  `resume_session_id` 非空時の `exit 2`、および policy 引数省略時（空文字）に claude / codex case の
  argv が従来と不変であることを検証（既存 wrapper テストの方式に従う）

### Large テスト

- **`large_local`（subprocess あり / ネットワークなし）**: stub `agy` 実行ファイルを用いた
  workflow E2E — `agent: antigravity` の 1 step workflow を `kaji run` 相当で回し、plain stdout の
  verdict block から PASS 解決まで到達すること。`kaji validate` CLI に resume 付き YAML を与えて
  exit 非 0 + エラーメッセージ 3 要素を検証すること
- **実 AGY 疎通（認証必要）**: 恒久 CI テストにはしない。認証済み環境が必要なため Issue の
  「ワークフロー完了後の確認項目」（headless / tmux interactive の smoke test）として人間が実施
  する。これは testing-convention の「物理的に作成不可（認証 backend 必須）」に該当し、
  代替として stub binary による large_local E2E で kaji 側の契約全体を検証する

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/cli-guides/antigravity-cli-session-guide.md`（新規・英語正本） | あり | `agy -p` / `agy -i`、permission と sandbox の別軸説明、resume 非対応と validation 拒否例、capability matrix、soft-deny 挙動、`--log-file` は非公開契約である旨 |
| `README.md` / `README.ja.md` | あり | 対応 agent 列挙・prerequisites・support matrix に Antigravity（resume 非対応の注記付き）を追加 |
| `llms.txt` | あり | 対応 agent 列挙と guide リンク追加（「Do not infer support…」行の agent 列挙更新） |
| `docs/ARCHITECTURE.md` | あり | adapter 一覧・agent×機能 capability 表（既存 522 行付近）・ドキュメント表への guide 追加 |
| `docs/README.md` | あり | CLI ガイド索引に Antigravity guide を追加 |
| `docs/dev/workflow-authoring.md` | あり | effort 値表に `antigravity` 行追加、agent 関連記述の更新 |
| `docs/reference/configuration.md` / `.ja.md` | あり | `agent_runner` / execution_policy 節に antigravity の対応範囲注記（該当節がある場合のみ） |
| `docs/cli-guides/interactive-terminal-runner.md` / `.ja.md` | あり | 対応 agent（claude / codex / antigravity）と PATH 前提、antigravity の execution_policy → interactive argv mapping（wrapper 第 9 引数の伝播）の更新 |
| `docs/adr/` | なし | 新規技術選定なし（capability registry は既存層規約 ADR 009 の範囲内の module 追加） |
| `AGENTS.md` / `CLAUDE.md` | なし | プロジェクト規約に変更なし |

Gemini 記載の削除・legacy 表記変更は行わない（#377 の scope）。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| ローカル実機 `agy --help` / `agy --version` | v1.1.6、2026-07-24 取得（下記に出力抜粋を引用。レビュワーはローカルの `agy` で再実行検証可能） | `-p/--print`（"Run a single prompt non-interactively and print the response"）、`-i/--prompt-interactive`、`--conversation`（"Resume a previous conversation by ID"）、`-c/--continue`、`--model`、`--effort ... (low\|medium\|high)`、`--sandbox`（"Run in a sandbox with terminal restrictions enabled"）、`--dangerously-skip-permissions`（"Auto-approve all tool permission requests without prompting"）、`--log-file` を確認。JSON / stream 出力 flag は flag 一覧に存在しない → argv mapping・effort 許容値・plain-text stream mode の直接根拠 |
| Antigravity CLI: Using | https://antigravity.google/docs/cli/using | 現行 version を v1.1.6 と表示。設定 override として `--sandbox` / `--dangerously-skip-permissions` に言及（`-p` / `-i` の説明はこのページにはない。flag の根拠は上記 `agy --help`） |
| Antigravity CLI: Install & auth | https://antigravity.google/docs/cli/install | native binary `agy`（macOS/Linux/Windows）。認証は対話 setup が必要 |
| Antigravity CLI: Conversations | https://antigravity.google/docs/cli/conversations | conversation は workspace-scoped で `-c` / `--continue` により再開可能。新規 conversation ID の公開取得手段は記載なし → resume 非対応判断の根拠（`--conversation <uuid>` flag 自体の根拠は上記 `agy --help` と #267 実機確認） |
| Antigravity CLI: Permissions | https://antigravity.google/docs/cli/permissions | permission engine は `deny > ask > allow` で、sandbox と permission（approval）は別概念 → policy mapping で両者を混ぜない根拠（headless soft-deny / exit 0 の挙動はこのページには記載がなく、#267 実機確認を根拠とする） |
| Antigravity CLI: Reference | https://antigravity.google/docs/cli/reference | TUI slash commands / keybindings / settings の reference（process 起動 flag の網羅表ではないため、flag 存否の根拠には使用しない。interactive TUI の操作体系の参照用） |
| Gemini CLI 移行告知 | https://github.com/google-gemini/gemini-cli/discussions/27274 | 2026-06-18 以降、個人 / free tier は Antigravity CLI へ移行 → 本機能の背景 |
| 親 Issue #267 本文・コメント | https://github.com/apokamo/kaji/issues/267 | 実機確認表（`agy --version` 1.1.6、`agy -p` の回答は plain text stdout、成功時 exit 0 / v1.1.3+ は server-side failure を stderr + non-zero で返す、headless soft-deny 時に回答なしでも exit 0 になり得る、新規 conversation UUID は stdout/stderr に出ない）と maintainer 承認済み決定事項（source of truth。interactive を含む permission / sandbox mapping の設計・検証要求を含む） |
| 現行実装 | `kaji_harness/cli.py` / `adapters.py` / `workflow.py` / `interactive_terminal.py` / `verdict.py` / `runner.py` / `preflight.py` / `assets/interactive-terminal/wrapper.sh` | argv 構築・adapter protocol・validation 経路（`validate_workflow` が `commands/validate.py` と `preflight.py` 経由 `runner.py` の双方から呼ばれる）・wrapper 契約・formatter 生成の各拡張点 |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L 分類・large_local マーカー・省略正当化 4 条件 |
| ADR 009 | `docs/adr/009-module-boundary-private-import.md` | 新 module `agents.py` の層配置と import 方向の制約 |
| layer fitness test | `tests/test_layer_imports.py` | `MODULE_LAYERS` は module を明示列挙し、未分類 module は `layer_of()` が `ValueError` で fail-loud する。foundation は内部 module import 禁止 → `agents.py` の分類追加が必須である根拠 |

### 実機出力の引用: `agy --help`（v1.1.6、2026-07-24 取得）

設計判断に用いた flag の該当行のみ抜粋（全文はローカルの `agy --help` で再取得可能）:

```
  --conversation                  Resume a previous conversation by ID
  -c                              Short alias for --continue
  --continue                      Continue the most recent conversation
  --dangerously-skip-permissions  Auto-approve all tool permission requests without prompting
  --effort                        Reasoning effort for the current CLI session (low|medium|high)
  -i                              Short alias for --prompt-interactive
  --log-file                      Override CLI log file path
  --model                         Model for the current CLI session
  -p                              Short alias for --print
  --print                         Run a single prompt non-interactively and print the response
  --prompt-interactive            Run an initial prompt interactively and continue the session
  --sandbox                       Run in a sandbox with terminal restrictions enabled
```

flag 一覧（全文）に JSON / stream 出力を指定する flag は存在しない。
