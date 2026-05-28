# [設計] issue-review-code Step 1.4 hard gate の BACK semantic 衝突を BACK_IMPLEMENT で解消する

Issue: #192

## 概要

`issue-review-code` skill の Step 1.4 hard gate（Pre-Handoff Review 証跡欠落検出）が発行する `BACK` verdict が、workflow YAML の `review-code` routing（`BACK: design`）に従って approve 済み設計を再起動し ABORT を誘発する。Step 1.4 の意図（`/issue-implement` 再実行）と routing semantic（design 差し戻し）が衝突しているため、Step 1.4 専用の差し戻し verdict として `BACK_IMPLEMENT`（→ implement）を導入し、bare `BACK`（→ design）と意味を分離する。

## 背景・目的

### Observed Behavior（OB）

Issue #184（`type:refactor`）の `full-cycle.yaml` 実行で `implement → review-code → design (ABORT)` の遷移が発生し workflow が中断した。

一次情報（実世界障害ログ）: `/home/aki/dev/kaji/main/.kaji-artifacts/184/runs/2605260007/run.log`（artifact は main worktree 配下に集約されるため、fix-192 worktree からは絶対パスで参照する。Issue #177 で `.kaji-artifacts/` を main worktree 集約とした方針による）

```
{"event": "step_end", "step_id": "review-code", "verdict": {"status": "BACK",
 "reason": "実装 handoff に必須の Pre-Handoff Review 証跡が Issue 184 に存在せず..."}}
{"event": "step_start", "step_id": "design", ...}
{"event": "step_end", "step_id": "design", "verdict": {"status": "ABORT",
 "reason": "Design は既に cycle 2 で Approve 済み（comment #12）かつ implementation が進行中..."}}
{"event": "workflow_end", "status": "ABORT", ...}
```

review-code agent は `.claude/skills/issue-review-code/SKILL.md:100`（Step 1.4 hard gate）の指示どおり、`PHR_COUNT=0` を検出して `BACK` を発行し、suggestion に「`/issue-implement 184` の Step 8.5 を完了し... 再実行すること」と記載した。しかし workflow は `BACK: design` routing に従い design step を起動し、design skill が「approve 済み設計の上書き＝scope 違反」として ABORT した。

### Expected Behavior（EB）

Pre-Handoff Review 証跡欠落は「implement Step 8.5 未実施」であり、対処は `/issue-implement` の再実行である。approve 済み設計を再起動すべきではない。Step 1.4 が発火したときの差し戻し先は **implement step** でなければならない。

根拠: Issue #192 本文 EB（`kaji issue view 192`）、および Step 1.4 自身の suggestion 文言（run.log 内）が「`/issue-implement` 再実行」を明示している。

### 再現手順（steps-to-reproduce）

1. `full-cycle.yaml`（または `feature-development*.yaml`）の実装フェーズで、`## Pre-Handoff Review` コメントを Issue に投稿せずに implement step が PASS で完了する状況を作る（#184 では起点が別バグ #193 = AI formatter による PASS 捏造だった。#193 は `kaji_harness/verdict.py:294` の delimiter-presence-only gate で既修正）
2. 直後の review-code step が Step 1.4 hard gate で `PHR_COUNT=0` を検出
3. skill が `BACK` verdict を出力
4. workflow が `review-code` の `on.BACK: design` routing に従い design step を起動
5. design skill が「approve 済み設計を上書きする scope 違反」として ABORT
6. `workflow_end status=ABORT`

実世界障害ログ（上記 run.log）が OB を直接示すため、bug.md の escape clause により、この実ログを **実装前 Red 証跡の代替**として扱う（恒久回帰テスト自体は省略しない。後述「テスト戦略」参照）。

### 根本原因（Root Cause）

`.claude/skills/issue-review-code/SKILL.md` 内で `BACK` の意味が 2 通りに多重定義されている:

| 出典 | BACK の意味 | 意図された対処 |
|------|-------------|----------------|
| `SKILL.md:100`（Step 1.4 hard gate） | implement Step 8.5 未実施 | `/issue-implement` 再実行 |
| `SKILL.md:317`（verdict 凡例 `BACK | 設計に問題`） | 設計に問題 | design 差し戻し |

workflow routing（全 `*.yaml` の `review-code.on.BACK: design`）は凡例 line 317 の semantic に従う設計のため、**実際に唯一 BACK を発行する Step 1.4** の意図と routing が乖離する。すなわち「1 つの verdict 種別 (`BACK`) に 2 つの異なる差し戻し意味を多重化した」ことが根本原因である。

**いつから**: review-code に Pre-Handoff Review hard gate（Step 1.4）が導入された時点（`SKILL.md:104` の "gl:9" 由来）から。verdict 凡例の `BACK = 設計に問題` は hard gate 導入以前からの定義であり、新設の Step 1.4 が既存 `BACK` を流用したことで衝突が生じた。

**同根の他箇所の調査**（完了条件「同根の他の skill に同様の semantic 衝突がないか」）:

| skill | BACK 発行箇所 | routing | 衝突有無 |
|-------|---------------|---------|----------|
| `issue-review-design` | **なし**（verdict 凡例は `PASS`/`RETRY`/`ABORT` のみ、`SKILL.md:325-331`）。hard gate も BACK を発行しない | review-design の `on:` に BACK key なし（`feature-development.yaml:35-36` 等） | **なし** |
| `issue-implement` | 凡例 `BACK | 設計に問題`（`SKILL.md:532`）。Pre-Handoff ループ制限到達時は BACK を **自分で発行せず** review-code 側に委ねる（`SKILL.md:358, 368`） | `implement.on.BACK: design` | **なし**（BACK の意味は単一＝設計に問題で routing と一致） |
| `issue-design` | BACK を発行しない（fail-loud は ABORT、`SKILL.md` Step 1.6） | — | **なし** |
| `issue-review-code` | Step 1.4 hard gate（`SKILL.md:100`）が唯一の BACK 発行点 | `review-code.on.BACK: design` | **あり（本 Issue 対象）** |

結論: semantic 衝突は `issue-review-code` Step 1.4 のみ。他 skill は BACK の意味が単一であるか BACK を発行しないため波及しない。

**workflow ごとの壊れ方の差**:

| workflow | `review-code.on` の BACK 系 key | Step 1.4 BACK 発火時の挙動 |
|----------|-------------------------------|---------------------------|
| `.kaji/wf/feature-development.yaml` / `-light` / `-local` / `full-cycle.yaml` | `BACK: design` | design 再起動 → ABORT（OB そのもの） |
| `workflows/feature-development.yaml`（legacy） | `BACK: design`（L76） | builtin と同一。design 再起動 → ABORT |
| `.kaji/wf/implement-to-pr.yaml` | **BACK key 自体が無い**（`on:` は PASS/RETRY/ABORT のみ、L32-35） | `BACK` が injected valid_statuses に含まれず、skill が BACK を返すと `InvalidVerdictValue`（`verdict.py:208`）でクラッシュ |

全系統とも Step 1.4 に対して壊れているため、修正は `issue-review-code` を採用する **全 6 workflow（builtin 5 + legacy 1）**を対象とする。

**legacy workflow の扱い（MF-1 対応）**: `workflows/feature-development.yaml` は builtin `.kaji/wf/feature-development.yaml` と並列で `tests/test_feature_development_workflow.py:23-27` の structural parity 検証対象として repo 上に現存する。同ファイルの `review-code` step（L68-77）も `skill: issue-review-code` を持ち、現状 `BACK: design` のみで `BACK_IMPLEMENT` を欠くため、builtin と同じ修正が必須。legacy を漏らすと旧 workflow パスで Step 1.4 `BACK_IMPLEMENT` 発火時に `InvalidVerdictValue` を残す。

## インターフェース

bug 修正のため公開 IF（CLI・Python API）は不変。変更は **skill 指示（markdown）と workflow routing（YAML）の semantic 契約**に限定する。

### 変更前 → 変更後

**1. `issue-review-code/SKILL.md` Step 1.4（L98-105）の verdict 種別**

- 変更前: `PHR_COUNT == 0` または `PHR_ROUTE_COUNT == 0` → **BACK**
- 変更後: 同条件 → **BACK_IMPLEMENT**（suggestion は従来どおり「`/issue-implement` を再実行し Step 8.5 を完了してから再 review」）

**2. `issue-review-code/SKILL.md` verdict 凡例（L313-318）**

- 変更前: `BACK | 設計に問題`
- 変更後: 2 行に分離
  - `BACK | コードレビューで設計レベルの問題を発見（→ design）。**ただし当該 workflow の review-code.on に BACK key がある場合のみ発行可**`
  - `BACK_IMPLEMENT | Step 1.4 hard gate 発火（Pre-Handoff Review 証跡欠落）。implement Step 8.5 未実施（→ implement）`
- prompt 内 valid_statuses（`prompt.py:75` の `step.on.keys()`）を権威とする旨を明記し、YAML が許可しない status は返さないルール（`workflow-authoring.md:175-177`）を参照させる

**3. 全 workflow YAML の `review-code.on` マッピングに `BACK_IMPLEMENT: implement` を追加**

| ファイル | 現状 | 追加 |
|----------|------|------|
| `.kaji/wf/feature-development.yaml` | `BACK: design` | `BACK_IMPLEMENT: implement` |
| `.kaji/wf/feature-development-light.yaml` | `BACK: design` | `BACK_IMPLEMENT: implement` |
| `.kaji/wf/feature-development-local.yaml` | `BACK: design` | `BACK_IMPLEMENT: implement` |
| `.kaji/wf/full-cycle.yaml` | `BACK: design` | `BACK_IMPLEMENT: implement` |
| `.kaji/wf/implement-to-pr.yaml` | （BACK 系なし） | `BACK_IMPLEMENT: implement` |
| `workflows/feature-development.yaml`（legacy） | `BACK: design`（L76） | `BACK_IMPLEMENT: implement` |

- `feature-development*` / `full-cycle` は bare `BACK: design` を**残す**（コードレビューで真の設計問題を見つけた場合の正当な差し戻し経路として温存）
- `implement-to-pr.yaml` は design step を持たない（前提が verify-design PASS 済み）ため bare `BACK: design` は追加しない。BACK_IMPLEMENT のみ追加する

### 使用例

workflow 実行時、review-code が Step 1.4 で証跡欠落を検出した場合:

```text
# review-code agent 出力（変更後）
---VERDICT---
status: BACK_IMPLEMENT
reason: "Pre-Handoff Review 証跡が Issue に存在しない（PHR_COUNT=0）"
evidence: "kaji issue view <id> --comments で '## Pre-Handoff Review' 見出し 0 件"
suggestion: "/issue-implement <id> を再実行し Step 8.5 を完了してから review-code に渡すこと"
---END_VERDICT---

# runner routing: review-code.on.BACK_IMPLEMENT → implement step を再実行
```

## 制約・前提条件

- `BACK_*` プレフィックス拡張は engine 側で既にサポート済み（`verdict.py:213` の `status.startswith("BACK")`、`workflow.py` の `validate_workflow`）。**runtime Python の変更は不要**
- `BACK_*` の suffix は uppercase 英数字 + アンダースコア限定（`workflow-authoring.md:168`）。`BACK_IMPLEMENT` は適合
- `BACK_*` は `suggestion` 必須（`verdict.py:213`）。Step 1.4 は既に suggestion を出力しているため充足
- 先例: `i-dev-final-check` は既に `BACK_DESIGN: design` / `BACK_IMPLEMENT: implement` を使用（`feature-development.yaml:110-111`）。本設計はこの確立済みパターンに整合させる
- skill は prompt 注入の valid_statuses（`step.on.keys()`）を権威とする。各 workflow が `BACK_IMPLEMENT` を `on:` に持つことで初めて skill が当該 status を発行できる。**全 review-code 採用 workflow への追加が漏れると、漏れた workflow で `InvalidVerdictValue` が発生する**ため、builtin 5 + legacy 1 = 全 6 ファイルへの追加が必須制約。legacy `workflows/feature-development.yaml` も `tests/test_feature_development_workflow.py` の検証対象として現存するため漏らさない
- リファクタ混在禁止: 本 Issue は BACK semantic 衝突の解消に限定。review-code の他観点・他 skill の改修は混ぜない

## 方針

最小侵襲で「verdict 種別の分離」を行う。runtime コードには触れず、(1) skill 指示の verdict 種別変更、(2) workflow routing への BACK_IMPLEMENT 追加、(3) 不変条件を守る回帰テスト、の 3 点に閉じる。

1. `issue-review-code/SKILL.md`:
   - Step 1.4 の判定結論を `BACK` → `BACK_IMPLEMENT` に書き換え
   - verdict 凡例（status の選択基準テーブル）に `BACK_IMPLEMENT` 行を追加し、`BACK` と意味を分離。valid_statuses 権威ルールへの参照を追記
2. workflow YAML 6 ファイル（builtin 5 + legacy `workflows/feature-development.yaml`）の `review-code.on` に `BACK_IMPLEMENT: implement` を追加
3. workflow routing 不変条件テストを追加（後述。新規ファイルを作成し、builtin / legacy 双方を列挙対象とする）
4. docs 整合確認・更新（`docs/dev/workflow_guide.md` 等に review-code の verdict→routing 記述があれば追従）

### verdict 種別選択の代替案比較（完了条件「Step 1.4 の verdict 種別の見直し方針が決定されている」）

| 案 | routing 先 | 評価 |
|----|-----------|------|
| **BACK_IMPLEMENT（採択）** | implement | EB（`/issue-implement` 再実行）と一致。先例（final-check）あり。bare BACK の design 用途を温存でき root-cause 別差し戻しが明確 |
| ABORT 化 | workflow 停止 | 人手介入前提。EB は「再実行で回復可能」を示すため過剰。自動 workflow の自己修復性を損なう |
| RETRY 化 | fix-code（cycle loop） | fix-code は「レビュー指摘のコード修正」であり Step 8.5（Pre-Handoff Review 生成）の再実行ではない。差し戻し先が誤り |
| bare BACK のまま + routing を `BACK: implement` に変更 | implement | bare BACK の「設計に問題」用途を潰す。多重定義の解消にならず、将来コードレビューで設計問題を見つけた際の経路が消える |

→ **BACK_IMPLEMENT** を採択。

### ループ終端性に関する考察

`BACK_IMPLEMENT` は cycle `code-review`（`entry: review-code`, `loop: [fix-code, verify-code]`）の **loop 外**にある implement step へ遷移する。これは既存 `BACK: design`（同じく loop 外の design へ遷移）と同一のルーティング機構であり、本修正がループ機構を新たに変えるわけではない。

implement ↔ review-code を往復し続ける理論的リスクは、implement が永続的に `PHR_COUNT=0` を生む場合のみ生じる。その永続失敗は #193（AI formatter による PASS 捏造）が起点であり、#193 は `verdict.py:294` の delimiter-presence-only gate で既修正。修正後の implement は Step 8.5 を実施して PASS するか、verdict 不成立で fail-loud（`VerdictNotFound`）して workflow ERROR 停止するため、終端性が担保される。

**残存リスク（設計判断として明記）**: cycle 外遷移には cycle の `max_iterations` 上限が効かない。これは `BACK: design` でも同様の既存特性であり本 Issue で新規導入される問題ではない。global なステップ往復上限が必要なら別 Issue とする（本 Issue の scope 外）。

## テスト戦略

### 変更タイプ

instruction（skill markdown）+ config（workflow YAML）変更 + 不変条件を守る回帰テスト（実行時 Python 実装の変更はなし）。

bug 固有ルール（再現テスト必須）に従う。SKILL.md は実行可能コードでないため markdown の挙動を直接 unit test できないが、**バグを構造的に不可能にする不変条件**を workflow YAML に対して検証する。これが「`PHR_COUNT=0` 状態における review-code の verdict semantics 検証手段」（完了条件）に相当する。

#### Small テスト（採用）

**前提の訂正（MF-2 対応）**: 当初案が前提とした `tests/workflows/test_builtin_workflows.py` は repo に**存在しない**。既存の workflow 構造テストは `tests/test_feature_development_workflow.py` であり、同テストは `FEATURE_WORKFLOW_PATHS`（legacy `workflows/feature-development.yaml` + builtin `.kaji/wf/feature-development.yaml`）を `pytest.mark.parametrize` で並列検証する parity 方式を採る（`tests/test_feature_development_workflow.py:23-27`）。

新規ファイル `tests/workflows/test_review_code_routing.py` を作成し、以下を追加する。**列挙対象は `.kaji/wf/*.yaml` に加えて legacy `workflows/feature-development.yaml` を必ず含める**（MF-2: builtin だけを glob する案では legacy 漏れを検出できない）。

1. **review-code の BACK_IMPLEMENT routing 不変条件**:
   - 列挙対象 = `glob(".kaji/wf/*.yaml")` ∪ `glob("workflows/*.yaml")`（legacy ディレクトリを明示的に含める）。各ファイルを `load_workflow` でロード
   - `skill == "issue-review-code"` の step を持つ workflow について、その step の `on` マッピングに `BACK_IMPLEMENT` key が存在することを assert
   - `BACK_IMPLEMENT` の routing 先 step ID が、`skill == "issue-implement"` の step を指すことを assert
   - これにより「Step 1.4 の差し戻し先が design ではなく implement である」「routing 先が実在する」を builtin / legacy 双方について機械的に保証

2. **（補強）bare BACK の routing 先健全性**（任意）:
   - review-code の `on` に bare `BACK` key がある場合、その先が `skill == "issue-design"` の step であること（design なし workflow に design 差し戻しが定義されていないこと）を確認

Red→Green の確認: 修正前は review-code step（builtin / legacy 双方）に `BACK_IMPLEMENT` が無いためテスト 1 が FAIL（OB に対応）、全 6 workflow へ追加後 PASS（EB に対応）。とくに legacy `workflows/feature-development.yaml` を列挙対象に含めることで、MF-1 で指摘された legacy 漏れもこのテストが検出する。OB を直接示す実ログ（run.log）が存在するため、bug.md escape clause により実装前合成 Red は実ログで代替し、恒久回帰テスト（修正後 Green）は本テストで担保する。

サイズ根拠: YAML パース + assertion のみ。外部プロセス・DB・ネットワーク無し。`docs/dev/testing-convention.md` のサイズ定義（純粋ロジック → Small）に従い `@pytest.mark.small` を付与する。

#### Medium テスト

不要。本変更は DB 連携・サブプロセス結合・内部サービス結合を伴わない。routing 解決の結合は runner の既存テスト（`tests/test_runner_back_routing.py` の `_resolve_next_step` ベース）が `BACK_*` 解決を既にカバーしており、本 Issue は routing 先の宣言（YAML）を正すのみで runner ロジックを変えないため Medium 追加は過剰。

#### Large テスト

不要。実 API 疎通・E2E ワークフロー実行を伴う検証は、CI コスト・実行時間に見合わず、上記 Small 不変条件テストで OB→EB の本質（差し戻し先 = implement）が検証できる。`docs/dev/testing-convention.md` のサイズ選択方針（根本原因の層に合わせる）に照らし、根本原因は YAML routing 宣言の層にあるため Small で十分。

### 既存テストのデグレ確認

- `tests/test_feature_development_workflow.py`（legacy / builtin parity）: BACK_IMPLEMENT 追加後も `validate_workflow` / `kaji validate` を通ること
- 新規 `tests/workflows/test_review_code_routing.py`: builtin / legacy 双方で BACK_IMPLEMENT routing 不変条件を満たすこと
- `tests/test_runner_back_routing.py`: BACK_* 解決の既存挙動が不変であること
- `make check`（ruff / mypy / pytest 全件）green

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし。`BACK_*` 拡張は既存決定 |
| docs/ARCHITECTURE.md | なし | アーキテクチャ不変 |
| docs/dev/workflow_guide.md | あり | review-code の verdict→routing 表に `BACK_IMPLEMENT` を反映（記述がある場合）。Issue 完了条件で明示された更新対象 |
| docs/dev/development_workflow.md | 要確認 | review-code routing の記述があれば追従（現状 mermaid は review-code BACK を明示していないため影響軽微の見込み） |
| docs/dev/workflow-authoring.md | なし | `BACK_*` 拡張仕様（L162-178）は既に正しく記載。追記不要 |
| docs/reference/ | なし | API 仕様・規約変更なし |
| docs/cli-guides/ | なし | CLI 仕様不変 |
| CLAUDE.md | なし | 規約変更なし |
| `.claude/skills/issue-review-code/SKILL.md` | あり（本体修正対象） | Step 1.4 + verdict 凡例 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #192 本文 | `kaji issue view 192`（GitHub） | OB/EB・根本原因テーブル（`SKILL.md:100` vs `:315` の BACK 多重定義）・完了条件の一次情報 |
| 実世界障害ログ | `/home/aki/dev/kaji/main/.kaji-artifacts/184/runs/2605260007/run.log`（main worktree 集約、絶対パス） | OB を直接示す。review-code が BACK → design 起動 → ABORT の event 列。bug.md escape clause の実ログ代替根拠 |
| review-code skill | `.claude/skills/issue-review-code/SKILL.md:98-105`（Step 1.4）, `:313-318`（verdict 凡例） | BACK 発行点と凡例の多重定義箇所。唯一の BACK 発行点が Step 1.4 であることの確認 |
| review-design skill | `.claude/skills/issue-review-design/SKILL.md:325-331` | verdict 凡例が PASS/RETRY/ABORT のみ＝BACK 非発行。同根衝突なしの根拠 |
| issue-implement skill | `.claude/skills/issue-implement/SKILL.md:358, 368, 532` | Pre-Handoff ループ制限到達時に BACK を自発行せず review-code に委ねる。BACK=設計問題の単一定義。同根衝突なしの根拠 |
| workflow routing | `.kaji/wf/feature-development.yaml:76-80`（review-code.on）, `:110-111`（final-check の BACK_IMPLEMENT 先例）, `implement-to-pr.yaml:32-35`（BACK key 欠落） | 各 workflow の routing 現状と先例 |
| legacy workflow | `workflows/feature-development.yaml:68-77`（review-code step、現状 `BACK: design` のみ） | MF-1 で指摘された修正対象の legacy workflow。builtin と同一の semantic 衝突を持つ |
| legacy/builtin parity test | `tests/test_feature_development_workflow.py:23-27`（`FEATURE_WORKFLOW_PATHS` で legacy + builtin を parametrize） | legacy workflow が repo 上の検証対象として現存する根拠。MF-2 の「`test_builtin_workflows.py` は不在」訂正の根拠 |
| BACK_* 拡張仕様 | `docs/dev/workflow-authoring.md:162-178` | `BACK_*` の文法（uppercase suffix）・valid_statuses 権威ルール（skill は prompt 注入の status を権威とする）・suggestion 必須 |
| verdict engine | `kaji_harness/verdict.py:206-214`（`_validate`） | `status.startswith("BACK")` で BACK_* を統一扱い、suggestion 必須を engine が強制。runtime 変更不要の根拠 |
| prompt 注入 | `kaji_harness/prompt.py:75, 93`（`valid_statuses = list(step.on.keys())`） | workflow の `on` key が skill prompt の valid status になる機構。全 workflow への追加が必須である根拠 |
| #193（前提修正） | `kaji_harness/verdict.py:289-298`（delimiter-presence-only gate） | implement 永続失敗の起点（AI formatter PASS 捏造）が既修正であり、BACK_IMPLEMENT ループの終端性を担保する根拠 |
