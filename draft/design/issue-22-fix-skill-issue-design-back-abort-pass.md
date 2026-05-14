# [設計] issue-design skill: BACK 経由再起動時に ABORT せず PASS で復帰する

Issue: gl:22

## 概要

`.claude/skills/issue-design/SKILL.md` を改修し、`feature-development` workflow
で `review-code` が `BACK` verdict を返して `design` step に差し戻された再起動
シナリオで、設計再確認後に `PASS` を返して通常フロー（`review-design` →
`implement` → `review-code`）に復帰させる。現状は LLM が「implementation 済み」
を検出して自己判断で `ABORT` を返し、workflow 全体を停止させている。

## 背景・目的

### Observed Behavior (OB)

`feature-development.yaml` で `review-code` が hard gate（例: Pre-Handoff Review
見出し欠落）を理由に `BACK` を発行すると、workflow は仕様通り `design` step に
遷移する（`.kaji/wf/feature-development.yaml:79` の `BACK: design`）。このとき
`issue-design` skill を実行する LLM session は、

- `draft/design/issue-XX-*.md` が既に存在する
- `git log` 上に implementation commit がある
- 直前の Issue コメントに `review-code` の `BACK` verdict が存在する

という観測から「設計フェーズは完了済み、再生成するとスコープ違反になる」と
自己判断し、`ABORT` を返す。

GitLab issue gl:20（2026-05-13 17:35:58 のコメント、`Issue gl:22` 本文に引用）が
実観測ケース。

```
## /issue-design 再実行リクエスト確認 — 設計フェーズは既に完了済み

進捗確認の結果、本 Issue の設計フェーズは既に完了し、後続フェーズまで
進行しているため、設計を再実行せず ABORT する。

| design 作成 | ✅ commit 済み (160de97) |
| design review | ✅ Approved |
| implementation | ✅ commit 済み (8054520) |
| code review | ❌ BACK |
```

結果として `design` step は `ABORT` を返し、`feature-development.yaml:28`
の `ABORT: end` により workflow 全体が停止する。後続の `review-design` /
`implement` / `review-code` は実行されない。

### Expected Behavior (EB)

`BACK: design` 経路で再起動された `issue-design` skill は以下のフローを取る:

1. 既存設計書（`draft/design/issue-XX-*.md`）を `Read` で読む
2. Issue コメントから直近の `review-code` BACK verdict と指摘内容を取得する
3. 指摘を「設計起因」と「実装起因」に分類する
4. 設計起因の指摘がある場合: 設計書を該当箇所のみ最小修正 → `PASS`
5. 設計起因の指摘が無い場合: 設計書は未変更のまま「設計変更不要」コメントを
   Issue に投稿 → `PASS`
6. `ABORT` を返すのは「設計レビュー観点で根本的に修正不能」と判断できる場合
   に限定する

`PASS` で復帰すれば `feature-development.yaml:27` の `PASS: review-design` 遷移
が機能し、通常フロー（`review-design` → `implement` → `review-code`）が再開する。

### 根拠（一次情報）

- `.kaji/wf/feature-development.yaml:79` の `BACK: design` — `review-code` から
  `design` への戻し遷移は YAML 仕様として正当な経路
- `docs/dev/workflow-authoring.md:130` の verdict 定義 — `BACK: 差し戻し。前段
  ステップを再実行` であり、戻った先で skill が `ABORT` を返す挙動は意図と
  矛盾する
- `docs/dev/workflow_completion_criteria.md:24` — `BACK は原因に応じて design /
  implement / docs の前段に戻す`。戻された先で停止することは想定されない
- `docs/dev/development_workflow.md:42-43` のフローチャート — `i-dev-final-check`
  も `BACK: design` を発行しうる経路として明示

## 再現手順（Steps to Reproduce）

最小再現環境（既出: GitLab issue gl:20）:

1. `provider.type='gitlab'` 配下で `feature-development` workflow を起動
   （`kaji run .kaji/wf/feature-development.yaml <issue_id>`）
2. type:feature の Issue が `design` → `review-design` (PASS) → `implement`
   まで進む
3. `implement` step の Pre-Handoff Review 見出しが `review-code` hard gate
   と一致しない（例: 見出しを `## Pre-Handoff` のような短縮形にする）
4. `review-code` が hard gate 違反として `BACK` verdict を返す
5. workflow runtime は `feature-development.yaml:79` に従い `design` step を
   再実行する
6. `issue-design` skill が `git log` と Issue コメントから「implementation 済み
   + review-code BACK」を観測 → `ABORT` を返す
7. workflow runtime は `feature-development.yaml:28` の `ABORT: end` により
   全体停止

期待される修正後の挙動（同シナリオ）:

- 手順 6 で skill は ABORT せず、既存設計書を読み review-code BACK 指摘の
  分類に基づき設計書修正 or 設計変更不要コメントを残して `PASS` を返す
- 手順 7 は発生せず、workflow は `review-design` → `implement` → `review-code`
  と継続する

## 根本原因（Root Cause）

### 問題のあるロジックの所在

`.claude/skills/issue-design/SKILL.md`（現行 415 行）には **BACK 経由再起動の
ハンドリングが定義されていない**。Step 1（worktree resolve） → Step 1.5（type
判定） → Step 2（設計書作成）の順で「初回起動」を前提に進む。

### なぜ間違っているか

skill markdown が再入力を前提にしていないため、LLM session が

1. 既存設計書を上書きすると scope 違反（既存設計の throwaway）
2. 設計書を新規生成せず ABORT すると workflow の意図に反する

の二者択一を迫られたとき、安全側として `ABORT` を選んでしまう。BACK 経由
再起動という**正当な遷移パターン**に対する明示的な分岐が無いことが根本原因。

### いつから壊れているか

`feature-development.yaml` で `review-code` の `BACK: design` 遷移が導入された
時点から潜在的に存在。実観測は 2026-05-13（GitLab issue gl:20）。

### 同根の他壊れ箇所の調査

`feature-development.yaml` で `BACK: design` 遷移を持つ step:

- `review-code`（`feature-development.yaml:79`）
- `i-dev-final-check`（`development_workflow.md:43` のフローチャートに `BACK:
  design` → `d1` が明示）

`feature-development-light.yaml` / `feature-development-local.yaml` も同等構造
を持つ場合は同じ問題に該当する。ただし本 Issue の改修対象は **skill 側 1 ファイル
（`.claude/skills/issue-design/SKILL.md`）** のみで、workflow YAML 側の改修は
不要（YAML 仕様は正しく、skill 側が再起動を吸収すれば全 workflow で同じ挙動
になる）。

`issue-implement` skill が同様の `BACK: implement` 経路で再起動された場合の
挙動は別 Issue として `_shared/report-unrelated-issues.md` の手順で起票候補と
する（本 Issue のスコープ外）。

## インターフェース

`issue-design` skill の **挙動契約** を変更する。skill が読む入力と返す verdict
の I/O 自体は不変。

### 入力（不変）

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID |
| `issue_ref` | str | 人間可読の Issue 参照 |
| `step_id` | str | 現在のステップ ID（`design`） |

### 出力（不変）

| verdict | 意味 |
|---------|------|
| `PASS` | 設計書作成・修正・確認完了 |
| `ABORT` | 設計不可能な要件、または設計レビュー観点で根本的に修正不能 |

### 内部状態の検出（新規明文化）

skill 内部で以下を観測し、初回起動 / BACK 経由再起動を分岐する:

| 観測対象 | 取得方法 | 検出ルール |
|---------|---------|----------|
| 既存設計書 | `ls draft/design/issue-<issue_id>-*.md` | 1 件以上ヒット |
| implementation commit | `git log --oneline -- kaji_harness tests Makefile pyproject.toml` | 該当 commit が存在 |
| review-code BACK comment | `kaji issue view <issue_id> --comments` の最新 `### Verdict` 行 | `BACK` を含み戻し先が `design` |

3 観測すべて該当 → BACK 経由再起動と判定。

### 使用例（skill markdown レベルの擬似擬似コード）

```text
# Step 0 として追加（Step 1 の前に実行）

if 既存設計書 ∧ implementation commit ∧ review-code BACK comment:
    既存設計書を Read
    review-code BACK コメントを取得し設計起因 / 実装起因に分類
    if 設計起因の指摘あり:
        該当箇所のみ最小修正 → commit
    else:
        設計書未変更
        「設計変更不要」コメントを Issue に投稿
    PASS を返す（Step 2 以降の通常フローは実行しない）
else:
    通常フロー（Step 1 → Step 2 → ...）を実行
```

## 制約・前提条件

- 改修対象は `.claude/skills/issue-design/SKILL.md` のみ（Python ランタイム
  コードへの変更は含まない）
- skill の I/O 契約（入力変数 / 返す verdict 種別）は不変
- 既存設計書が無い初回起動時の挙動は完全に不変（後方互換）
- `BACK: design` を発行する step は workflow YAML 側で複数あり得る
  （`review-code` / `i-dev-final-check` 等）。skill 側はどの step からの BACK
  かを意識せず、`### Verdict` 行の `BACK` を検出すれば良い
- 設計起因 / 実装起因の分類は LLM の判断に委ねる（rule-based 自動分類は実装
  しない）が、判定根拠（どの指摘を設計起因と判定したか）はコメントに必ず
  含める

## 方針

`.claude/skills/issue-design/SKILL.md` に以下を追加・修正する。

### A. Step 0「BACK 経由再起動の検出と分岐」を Step 1 の前に挿入

Step 1（worktree resolve）の直前に Step 0 を挿入し、3 観測を行って初回起動 /
BACK 経由再起動を判定する。実行コマンドの例:

```bash
# 1. 既存設計書の有無
ls [worktree_dir]/draft/design/issue-[issue_id]-*.md 2>/dev/null

# 2. implementation commit の有無
git -C [worktree_dir] log --oneline -- kaji_harness tests Makefile pyproject.toml

# 3. 最新の review-code BACK verdict
kaji issue view [issue_id] --comments | grep -E '^(### Verdict|status:)' | tail -20
```

3 つすべて該当 → BACK 経由再起動として Step 0.5 に進む。
いずれか欠ける → 通常フロー（Step 1 以降）に進む。

### B. Step 0.5「BACK 経由再起動時の修正/再確認フロー」を新設

以下のサブステップを定義する:

1. 既存設計書を `Read`
2. `kaji issue view [issue_id] --comments` の最新 review-code BACK コメント
   から指摘リストを抽出
3. 各指摘を「設計起因」「実装起因」に分類:
   - 設計起因: 設計書の不備が原因の指摘（IF 設計の漏れ、テスト戦略の未定義、
     一次情報不足、影響ドキュメント漏れ等）
   - 実装起因: 設計は正しいが実装が逸脱した指摘（見出し表記、コード品質、
     テスト失敗等）
4. 分岐:
   - 設計起因の指摘がある場合 → 該当箇所のみ最小修正 → 設計書を再 commit →
     コメント（後述）→ `PASS`
   - 設計起因の指摘が無い場合 → 設計書未変更 → 「設計変更不要」コメント
     （後述）→ `PASS`

### C. 「設計変更不要」コメント書式を明文化

```markdown
## 設計再確認結果（BACK 経由再起動）

直近の review-code BACK verdict を確認しました。

### review-code BACK の指摘内容

- 指摘 1: ...
- 指摘 2: ...

### 設計起因 / 実装起因の分類

| 指摘 | 分類 | 根拠 |
|------|------|------|
| 指摘 1 | 実装起因 | 設計書 X 節で要件は明示済み。実装側の逸脱 |
| 指摘 2 | 実装起因 | ... |

### 判定

設計起因の指摘は無し → 設計書を変更せず PASS を返します。
後続フロー（review-design → implement）で実装を修正してください。
```

設計起因の指摘があり修正した場合は以下:

```markdown
## 設計再確認結果（BACK 経由再起動）

直近の review-code BACK verdict を確認し、設計書の以下箇所を修正しました。

### 修正箇所

- 設計書 X 節: ...
- 設計書 Y 節: ...

### review-code BACK 指摘の分類

| 指摘 | 分類 | 対応 |
|------|------|------|
| 指摘 1 | 設計起因 | 設計書 X 節を修正 |
| 指摘 2 | 実装起因 | 後続 implement フェーズで対応 |

### 判定

設計修正完了 → PASS。
```

`docs/dev/shared_skill_rules.md` § GitLab auto close keyword 回避規約に従い、
コメント本文では `指摘 N` / `Must Fix item N` 形式を用い、`Fix #N` / `Closes #N`
のような close keyword と `#数字` の隣接表記は使わない。

### D. ABORT 条件の限定を明文化

現行 SKILL.md の Step 5 直後にある verdict 選択表を以下に置き換える:

```markdown
| status | 条件 |
|--------|------|
| PASS | 設計書作成・コミット完了、または BACK 経由再起動時の設計再確認完了 |
| ABORT | 以下のいずれか:
          (a) Issue 要件が論理的に破綻しており、設計レベルで実現不能
          (b) BACK 経由再起動だが、指摘内容が設計レビュー観点で根本的に
              修正不能（例: 一次情報そのものが消失、要件の前提が崩壊） |
```

「implementation 済みを検出したから ABORT」という条件は明示的に **除外** する。

### E. 補足: 初回起動フローへの影響

Step 0 の追加は初回起動時にはノーオペ（3 観測のうち少なくとも 1 つが欠ける）
で素通りするだけなので、初回起動の挙動は完全に不変。既存テスト・既存
workflow への波及はない。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ

**instruction-only（`.claude/skills/issue-design/SKILL.md` 1 ファイルの markdown
更新）**。`docs/dev/testing-convention.md` のカテゴリでは **docs-only** に
分類される（Python 実行時コードの追加・変更を含まない）。

### 実行時コード変更の場合

該当なし。本改修は `kaji_harness/` 配下に一切手を入れない。

#### Small / Medium / Large テスト

いずれも追加しない。理由は次節（恒久テストを追加しない理由）に統一して記す。

### docs-only / instruction-only の場合

#### 変更固有検証

1. **`make verify-docs`** — `.claude/skills/issue-design/SKILL.md` 内の link
   切れがないこと、`docs/dev/shared_skill_rules.md` 等への相互参照が壊れて
   いないことを確認
2. **`make check`** — Python コード変更は無いが、ruff / mypy / pytest が
   regression を出さないこと（baseline-clean の維持確認）
3. **`make verify-skill`** が存在する場合は実行（現状 `Makefile` に該当
   ターゲットが無ければ skip。Issue 内で `make verify-skill` を新設しない）
4. **再現シナリオでの skill 出力期待値の手動検証**（下記「期待値」節）
5. **GitLab auto-close hazard pattern の grep**: `shared_skill_rules.md`
   § push / push 後の検証 の regex で `.claude/skills/issue-design/SKILL.md`
   差分を grep し hazard 表記が混入していないこと

#### 期待値（再現シナリオに対する skill 出力）

`Issue 完了条件「改修内容を検証する手順が設計書に記載されている（再現シナリオに
対する skill 出力の期待値）」` に対応:

| シナリオ | 期待 verdict | 期待 Issue コメント |
|---------|-------------|--------------------|
| 初回起動（設計書なし） | PASS | 「設計書作成完了」コメント（現行通り） |
| 初回起動だが既存設計書あり、implementation 無し、review-code BACK 無し | PASS | 「設計書作成完了」コメント（既存設計書は上書きしないか、最小差分での更新を選択）|
| BACK 経由再起動、設計起因の指摘あり | PASS | 「設計再確認結果（BACK 経由再起動）」コメント（修正箇所明示）|
| BACK 経由再起動、設計起因の指摘なし | PASS | 「設計再確認結果（BACK 経由再起動）」コメント（設計変更不要） |
| BACK 経由再起動、指摘が設計観点で根本修正不能 | ABORT | ABORT 理由を明示するコメント |

検証手順:

1. test fixture として `BACK 経由再起動シナリオ` を再現する Issue を local
   provider で起票し、設計書 / implementation commit / review-code BACK
   コメントを意図的に配置する
2. `/issue-design <test_issue_id>` を手動実行し、skill が PASS を返し
   「設計再確認結果」コメントを投稿することを確認する
3. local provider で再現できることを優先する（GitLab 実通信は不要）

#### 恒久テストを追加しない理由

`docs/dev/testing-convention.md` § docs-only / metadata-only / packaging-only
変更 の 4 条件に沿って:

1. **独自ロジックの追加・変更をほぼ含まない** — `.claude/skills/issue-design/SKILL.md`
   は LLM 向け instruction であり、Python 実行時ロジックは含まない。skill
   harness（`kaji_harness/`）に変更は無い
2. **想定される不具合パターンが既存テストまたは既存品質ゲートで捕捉済み** —
   skill harness 側の verdict 解釈・workflow 遷移ロジックは既存の workflow
   テスト（`tests/` 配下）でカバー済み。今回の改修は LLM 出力の内容を変える
   ものであり、Python ユニットテストで behavior を assert する手段がない
3. **新規テストを追加しても回帰検出情報がほとんど増えない** — SKILL.md の
   markdown 構造（例: `Step 0` 見出しの存在）を assert する snapshot test は
   書けるが、それは「指示書の文字列を assert する」だけで、LLM が実際に
   その指示に従って正しい分岐を取ることは保証されない。回帰検出としての
   情報量が低い
4. **テスト未追加の理由をレビュー可能な形で説明できる** — 本セクションが
   その説明にあたる

### `kaji-run-verify` での補強

設計書には `kaji-run-verify` skill による手動 workflow run 検証（再現シナリオで
の skill 出力確認）を **PR 前の検証作業** として記述するが、恒久 CI ジョブには
組み込まない（外部依存・実行時間の観点で `make check` のデフォルトには含めない）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規アーキテクチャ決定は伴わない（既存 workflow 仕様に skill を整合させる修正） |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/development_workflow.md | あり（軽微） | § Pre-Handoff Review の近傍に「BACK 経由再起動時の skill 挙動」への参照リンクを追加（必須ではない） |
| docs/dev/workflow_completion_criteria.md | あり（軽微） | § 判定原則「`BACK` は原因に応じて design / implement / docs の前段に戻す」が skill 側でどう吸収されるかへの参照を追加 |
| docs/dev/workflow-authoring.md | なし | verdict 定義は不変。skill 側の挙動規約は本 skill markdown に閉じる |
| docs/dev/shared_skill_rules.md | なし | GitLab auto-close 回避規約等の共通ルールは変更なし。新規 skill 規約は追加しない |
| docs/reference/python/ | なし | Python コード変更なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | なし | 規約変更なし |
| `.claude/skills/issue-design/SKILL.md` | **あり（主対象）** | Step 0 / 0.5 追加、verdict 選択表更新 |
| `.claude/skills/issue-implement/SKILL.md` | 検討対象 | 同様の `BACK: implement` 経路の扱い。本 Issue では別 Issue 化候補として `_shared/report-unrelated-issues.md` に従い起票する（本 Issue では変更しない） |
| `.claude/skills/issue-review-design/SKILL.md` | なし | review-design 側の rubric は不変。BACK 経由再起動で設計が小修正された場合も既存 rubric で再 review すれば良い |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `feature-development.yaml` (workflow definition) | `.kaji/wf/feature-development.yaml:79` | `review-code` の `on:` に `BACK: design` が定義されており、`review-code` から `design` への戻し遷移は YAML 仕様として正当 |
| `feature-development.yaml` (design step) | `.kaji/wf/feature-development.yaml:21-28` | `design` step の `on:` は `PASS: review-design` と `ABORT: end` のみ。`ABORT: end` のため skill が `ABORT` を返すと workflow 全体停止 |
| `workflow-authoring.md` (verdict 定義) | `docs/dev/workflow-authoring.md:122-131` | verdict `BACK = 差し戻し。前段ステップを再実行` という定義。前段が再実行で即 ABORT する挙動は本定義と矛盾 |
| `workflow_completion_criteria.md` (判定原則) | `docs/dev/workflow_completion_criteria.md:22-24` | `BACK は原因に応じて design / implement / docs の前段に戻す` という運用方針。戻された先で停止することは想定されない |
| `development_workflow.md` (フローチャート) | `docs/dev/development_workflow.md:43` | `i-dev-final-check` の `BACK: design` 遷移が明示。`review-code` 以外にも `design` に戻る経路がある |
| `shared_skill_rules.md` (GitLab auto-close 規約) | `docs/dev/shared_skill_rules.md:46-167` | コメント / commit body / MR description で `Clos(e[sd]?|ing)` / `Fix(e[sd]\|ing)?` / `Resolv(e[sd]?\|ing)` / `Implement(s\|ing\|ed)?` の直後 `#[0-9]` を書かない |
| `testing-convention.md` (docs-only 4 条件) | `docs/dev/testing-convention.md:62-68` | docs-only / metadata-only / packaging-only 変更で恒久テスト不要とできる 4 条件。本改修の test strategy 判断根拠 |
| GitLab issue gl:20 (実観測ケース) | Issue gl:22 本文に引用された 2026-05-13 17:35:58 のコメント | `issue-design` が implementation 済みを検出して ABORT した実例（design 作成 ✅ / design review ✅ / implementation ✅ / code review ❌ BACK） |
| `.claude/skills/issue-design/SKILL.md` (現行版) | `.claude/skills/issue-design/SKILL.md:1-415` | 改修対象。現行は Step 1 → 1.5 → 2 → 2.5 → 2.6 → 3 → 4 → 5 構成で BACK 経由再起動の分岐が未定義。verdict 選択表（行 411-414）も `PASS` / `ABORT` の 2 値のみ |
| `bug.md` (type:bug 設計指針) | `.claude/skills/_shared/design-by-type/bug.md` | OB / EB / 再現手順 / 根本原因 / 再現テスト を必須セクションとする規約。本設計書の構成根拠 |
