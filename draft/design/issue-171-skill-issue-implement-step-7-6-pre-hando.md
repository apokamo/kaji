# [設計] issue-implement Step 7.6 Pre-Handoff Review の入力契約と手順順序の整合

Issue: #171

## 概要

`/issue-implement` SKILL.md の **Step 7.6 Pre-Handoff Review** が、実行手順上 Step 8（コミット）より前に配置されているのに、入力としてコミット後にしか確定しない値（`git diff main...HEAD` の差分・対象 commit hash）を要求している順序矛盾を修正する。Pre-Handoff Review をコミット後へ移動し、手順番号と実行順を一致させる。

## 背景・目的

### Observed Behavior (OB)

Step 7.6 時点（Step 8 のコミット未実行）では、feature branch の HEAD が分岐元 `main` と同一コミットを指す。Issue 本文「現状の挙動」に記載のとおり、実装差分を未コミットのまま実行すると:

```
$ git status --short
 M f.txt
$ git diff main...HEAD --stat
(出力なし = 空。実装差分が diff に現れない)
$ git log main..HEAD --oneline
(出力なし = 対象 commit hash が存在しない)
```

Step 7.6.2 経路 A の入力契約（`.claude/agents/kaji-code-reviewer.md` § 入力 が SoT）は `## Diff` = `git diff main...HEAD` の全文と `対象 commit hash` を要求するが、Step 7.6 時点ではどちらも取得できない。

### Expected Behavior (EB)

Step 7.6 の入力契約と手順順序が整合し、手順番号どおりに辿るだけで Pre-Handoff Review に正しい diff / commit hash を渡せること。Issue 本文「完了条件」3 項目を満たす。

### 根本原因 (Root Cause)

Pre-Handoff Review は `585789b feat: add pre-handoff review with kaji-code-reviewer subagent for gl:9` で導入された。導入時、「handoff 直前（`/issue-review-code` への進行前）に検査する」という *位置的* 意図を満たすため Step 7.5 と Step 8 の間に挿入されたが、その入力契約は `kaji-code-reviewer` の SoT が前提とする「完成・コミット済みの実装」（`git diff main...HEAD` + commit hash）に対して定義されていた。**「handoff 直前」という位置要件と「コミット済み状態を入力に取る」という契約要件が、Step 8（コミット）が Step 7.6 の後にある事実と突き合わされないまま固定された**ことが原因。

「いつから壊れているか」: gl:9 の PHR 導入時点から。「同根の他箇所」: `/issue-implement` SKILL.md 全体を確認した結果、コミット前ステップがコミット後の値を要求する順序矛盾は Step 7.6 ↔ Step 8 の 1 箇所のみ（Step 7.5 完了条件確認・PHR_COUNT カウンタは Step 9 のコメントを参照するのみで矛盾なし）。

### 採用方針: 案 A（Step 7.6 をコミット後へ移動）

Issue 本文の案 A を採用する。案 B（入力契約を未コミット作業ツリー差分へ変更）は以下の副作用があるため不採用:

- `kaji-code-reviewer` の出力 `対象 commit` フィールドがコミット前には埋められない。`/issue-review-code` Step 1.5 は複数 Pre-Handoff Review コメントを **commit hash で識別**して最新を正とするため、commit hash 要求の撤廃は下流の識別機構を壊す。
- `/issue-review-code` Step 1（行 85）は `git diff main...HEAD`（コミット済み差分）でレビューする。案 B は実装時 PHR とレビュー時で「レビュー対象差分の意味」を分岐させ、概念的不整合を生む。

案 A は PHR をコミット済み差分に対して実施するため、`kaji-code-reviewer.md` の入力契約（§ 入力）を **一切変更せず**そのまま有効化でき、`/issue-review-code` 側との整合も保たれる。

## インターフェース

skill / agent markdown の手順記述変更であり、Python の公開 API・CLI・YAML スキーマの変更は無い。「インターフェース」= 手順番号体系と入力契約。

### 入力（変更なし）

`kaji-code-reviewer` の入力契約（`.claude/agents/kaji-code-reviewer.md` § 入力）は変更しない。`## Diff` = `git diff main...HEAD` の全文、`対象 commit hash` を引き続き要求する。案 A ではこれらが Step 8（コミット）後に取得されるため、契約は満たされる。

### 出力（変更なし）

Pre-Handoff Review の出力フォーマット（`## Pre-Handoff Review` セクション、4 観点判定、verdict）は変更しない。

### 変更前 / 変更後の手順順序

| | 変更前 | 変更後 |
|---|--------|--------|
| 手順 | Step 7（品質）→ 7.5（完了条件）→ **7.6（PHR）**→ 8（commit）→ 9（comment） | Step 7（品質）→ 7.5（完了条件）→ 8（commit）→ **8.5（PHR）**→ 9（comment） |

Step 7.6 とその子ステップ 7.6.1〜7.6.4 を **Step 8.5 / 8.5.1〜8.5.4** へ改番し、物理位置を Step 8 と Step 9 の間へ移動する。Step 9 の番号は不変。

> **改番する理由**: 本 Issue の defect は「手順番号の順序 ≠ 実行順序」である。物理位置だけ移して `Step 7.6` の番号を残すと、文書を順に辿る読者（AI agent）に対して「Step 7.6 が Step 8 の後にある」という同型の混乱を残す。番号は実行順を表さねばならない。

### 使用例

```text
（運用者 / AI agent が /issue-implement を手順番号どおりに辿る）
Step 7   品質チェック（ruff / mypy / pytest）
Step 7.5 完了条件の段階確認
Step 8   git add . && git commit       ← コミットがここで確定
Step 8.5 Pre-Handoff Review            ← git diff main...HEAD / commit hash が取得可能
Step 9   Issue へ実装完了報告コメント
```

## 制約・前提条件

- 変更対象は instruction document（`.claude/skills/` / `.claude/agents/` / `docs/`）のみ。`kaji_harness/` 等の Python ランタイムコードは変更しない。
- `kaji-code-reviewer.md` § 入力 は入力契約の SoT。本変更では契約の *内容* を変えないため、SoT 更新は step 番号参照の追従のみ。
- verdict ループ（`With fixes` / `No`）の手順が変更後の順序でも破綻しないこと（Issue 完了条件 3）。
- worktree は未 push の feature branch であり、コミットの `--amend` は共有履歴を書き換えない（安全）。

## 方針

### 1. `.claude/skills/issue-implement/SKILL.md`

- Step 7.6 ブロック（行 270〜404、子ステップ 7.6.1〜7.6.4 と出力フォーマット含む）を Step 8（コミット）の **後ろ**へ移動。
- 見出しを改番: `Step 7.6` → `Step 8.5`、`Step 7.6.1`〜`7.6.4` → `Step 8.5.1`〜`8.5.4`。本文中の自己参照「本 Step 7.6」も `本 Step 8.5` へ統一。
- verdict ループ表（現行 行 343〜345）を変更後順序へ整合:
  - `Yes` → 「handoff 可。**Step 9（Issue コメント）へ進む**」（コミットは Step 8 で完了済み）。
  - `With fixes` / `No` → 「main session が指摘事項を反映 → Step 7a / 7b を再実行 → **`git commit --amend --no-edit` で実装コミットを更新** → 本 Step 8.5 を再実行（ループ）」。
    - `--amend` を採る理由: 実装を単一の `feat:` コミットに保ち、ループのたびに `git diff main...HEAD` と `対象 commit hash` が「修正反映後の完全な現在状態」を表すようにする。fixup コミット連鎖でも機能はするが履歴を汚す。未 push branch なので amend は安全。
- PHR_COUNT カウンタ説明（現行 行 350・356）の「本 Step 7.6」を `本 Step 8.5` へ改番。カウンタ機構自体（Step 9 投稿コメント数を数える）は案 A の並べ替えに影響されないため論理変更なし。ループは Step 8.5 内で完結し、Step 9 はループ終了後に 1 回コメントを投稿する（=`/issue-implement` の呼び出し単位で 1 件）という現行解釈を維持。
- Step 7.5 末尾（行 268）の「Step 9 の Issue コメント」参照は不変（Step 9 番号据え置きのため）。

### 2. `.claude/agents/kaji-code-reviewer.md`

- 行 37 の SoT 注記「`Step 7.6.2 経路 A` の prompt template」を `Step 8.5.2 経路 A` へ改番。入力契約の内容は変更しない（完了条件 2: SKILL.md テンプレートとの整合は、双方が同一契約をミラーする状態を維持）。

### 3. `/issue-review-code` SKILL.md

- 行 91・101 の「`/issue-implement` Step 7.6」を `Step 8.5` へ改番。Step 1.4 の hard gate ロジック（`## Pre-Handoff Review` セクション数の検出）は不変。

### 4. 影響 docs の step 番号追従

- `docs/dev/workflow_completion_criteria.md` 行 78、`docs/dev/shared_skill_rules.md` 行 123、`docs/dev/development_workflow.md` 行 111 の「Step 7.6」記述を `Step 8.5` へ改番。

## テスト戦略

> **CRITICAL**: 変更タイプに応じた検証方針を定義する。

### 変更タイプ

instruction / docs-only 変更（`.claude/skills/` / `.claude/agents/` / `docs/` の markdown のみ。`kaji_harness/` 等のランタイムコード変更なし）。

### 恒久回帰テスト（pytest）を追加しない理由

`type:bug` の再現テスト必須ルール（`design-by-type/bug.md` § 8）は **ランタイムコードの bug** を対象とする。本 bug は「AI agent が skill markdown を手順番号順に辿ると必要入力が取得できない」という instruction document の順序・契約の不整合であり、`docs/dev/testing-convention.md` § 省略してよい理由「物理的に作成不可 — 対象インターフェース自体が存在しない」に該当する。skill markdown の手順順序を assert する Python 実行面は存在しない。同 § 4 条件:

1. 独自ロジックの追加・変更を含まない（markdown 手順の並べ替え・改番のみ）。
2. 想定不具合パターン（PHR コメント欠落）は `/issue-review-code` Step 1.4 の hard gate（`PHR_COUNT` / `PHR_ROUTE_COUNT` チェック）が既に捕捉する。
3. 新規 pytest を追加しても回帰検出情報は増えない（markdown を解析対象とする恒久テストは存在せず、追加は過剰）。
4. 本セクションが理由をレビュー可能な形で説明している。

### 変更固有検証

| 検証 | 内容 |
|------|------|
| 手順再トレース | 改訂後 SKILL.md を Issue 本文「再現手順」どおりに辿り、Step 8（コミット）実行後に Step 8.5 へ進む順序で `git diff main...HEAD` と `git rev-parse HEAD` が非空になることを確認する。 |
| verdict ループ整合 | `With fixes` / `No` 経路を机上トレースし、`git commit --amend` 後に `git diff main...HEAD` が修正反映済み・`対象 commit hash` が更新される（破綻しない）ことを確認する（完了条件 3）。 |
| 契約ミラー整合 | `kaji-code-reviewer.md` § 入力 と SKILL.md Step 8.5.2 経路 A テンプレートの各セクション記述・step 番号参照が一致することを目視照合する（完了条件 2）。 |
| `make verify-docs` | docs / skill markdown のリンク整合チェック。 |
| dogfood 検証 | 次回実 `/issue-implement` 実行が最終的な動作検証（`kaji-run-verify` の運用に準ずる）。本設計では恒久テスト代替としては扱わず、実運用での確認位置づけ。 |

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | あり | `workflow_completion_criteria.md` / `shared_skill_rules.md` / `development_workflow.md` の「Step 7.6」記述を「Step 8.5」へ改番 |
| docs/reference/ | なし | API 仕様・規約変更なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | なし | 規約変更なし |
| .claude/skills/issue-implement/SKILL.md | あり | 本変更の主対象（Step 7.6→8.5 改番・移動・loop 整合） |
| .claude/skills/issue-review-code/SKILL.md | あり | Step 1.4 の「Step 7.6」参照を「Step 8.5」へ改番 |
| .claude/agents/kaji-code-reviewer.md | あり | § 入力 SoT 注記の step 番号参照を改番 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| issue-implement SKILL.md（現行） | `.claude/skills/issue-implement/SKILL.md` 行 270〜411 | Step 7.6（PHR）が Step 8（コミット）より前に配置され、Step 7.6.2 経路 A が `git diff main...HEAD` と `対象 commit hash` を入力要求する記述。defect の所在。 |
| kaji-code-reviewer 入力契約 SoT | `.claude/agents/kaji-code-reviewer.md` 行 35〜47 | 「`## Diff`: `git diff main...HEAD` の全文」「対象 commit hash: prompt 冒頭または header で指定」。本契約はコミット済み実装を前提とする。 |
| issue-review-code Step 1 / 1.4 | `.claude/skills/issue-review-code/SKILL.md` 行 83〜114 | レビューは `git diff main...HEAD`（コミット済み差分）で実施。複数 PHR コメントは「commit hash で識別」して最新を正とする。案 B が下流の commit hash 識別を壊す根拠。 |
| bug 設計ガイド | `.claude/skills/_shared/design-by-type/bug.md` § 8 | 「修正前に Red になる再現テスト（regression test）を必ず 1 本以上定義」。本ルールがランタイムコード bug 前提であることを踏まえ、instruction-only 変更での省略を testing-convention に基づき正当化。 |
| testing-convention | `docs/dev/testing-convention.md` 行 113〜131 | 「物理的に作成不可 — サンドボックス未提供、対象インターフェース自体が存在しない」を恒久テスト省略の正当理由として列挙。 |
| PHR 導入コミット | `git show 585789b`（`feat: add pre-handoff review with kaji-code-reviewer subagent for gl:9`） | Pre-Handoff Review の初出。導入時に Step 7.6 と Step 8 の順序が入力契約と突き合わされなかったことが根本原因。 |
