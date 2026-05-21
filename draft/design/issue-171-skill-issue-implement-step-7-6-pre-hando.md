# [設計] issue-implement Step 7.6 Pre-Handoff Review の入力契約と手順順序の整合

Issue: #171

## 概要

`/issue-implement` SKILL.md の **Step 7.6 Pre-Handoff Review** が、実行手順上 Step 8（コミット）より前に配置されているのに、入力としてコミット後にしか確定しない値（`git diff main...HEAD` の差分・対象 commit hash）を要求している順序矛盾を修正する。Pre-Handoff Review をコミット後（`Step 8.5`）へ移動して手順番号と実行順を一致させ、併せて verdict ループと PHR_COUNT カウンタの pre-existing な不整合（ループ試行が Issue コメントに記録されずカウントできない）を、各試行ごとの PHR 証跡投稿（Step 8.5.5 新設）として一意に再定義する。

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

「いつから壊れているか」: gl:9 の PHR 導入時点から。「同根の他箇所」: `/issue-implement` SKILL.md 全体を確認した結果、コミット前ステップがコミット後の値を要求する順序矛盾は Step 7.6 ↔ Step 8 の 1 箇所のみ（Step 7.5 完了条件確認は Step 9 のコメントを参照するのみで矛盾なし）。ただし同じく gl:9 で導入された **verdict ループと PHR_COUNT カウンタの記述には別種の pre-existing 不整合がある**（ループ表は Step 9 を経由しないのに PHR_COUNT 説明は「7.6 実行のたびに Step 9 コメントが増える」と前提する）。この不整合は本 Issue 完了条件 3「ループ手順が破綻しない」と直結するため、順序修正と同一スコープで併せて解消する（詳細は「方針」2）。

### 採用方針: 案 A（Step 7.6 をコミット後へ移動）

Issue 本文の案 A を採用する。案 B（入力契約を未コミット作業ツリー差分へ変更）は以下の副作用があるため不採用:

- **入力契約の弱体化**: `kaji-code-reviewer.md` § 入力（入力契約の SoT）は「対象 commit hash: prompt 冒頭または header で指定」を **必須入力**として定義している。案 B はコミット前に PHR を走らせるため、この必須入力を撤廃せざるを得ず、SoT 契約を実装フェーズ専用に弱める。
- **出力契約の不整合**: `kaji-code-reviewer.md` § 出力形式は出力ブロック先頭に `- **対象 commit**: <git-sha>` を必須項目として持つ。案 B ではコミット前に対象 commit hash が存在しないため、この出力フィールドが埋められず出力契約も崩れる。
- `/issue-review-code` Step 1（行 85）は `git diff main...HEAD`（コミット済み差分）でレビューする。案 B は実装時 PHR とレビュー時で「レビュー対象差分の意味」を分岐させ、概念的不整合を生む。

案 A は PHR をコミット済み差分に対して実施するため、`kaji-code-reviewer.md` の入力契約（§ 入力）・出力契約（§ 出力形式）を **いずれも一切変更せず**そのまま有効化でき、`/issue-review-code` 側との整合も保たれる。

## インターフェース

skill / agent markdown の手順記述変更であり、Python の公開 API・CLI・YAML スキーマの変更は無い。「インターフェース」= 手順番号体系と入力契約。

### 入力（変更なし）

`kaji-code-reviewer` の入力契約（`.claude/agents/kaji-code-reviewer.md` § 入力）は変更しない。`## Diff` = `git diff main...HEAD` の全文、`対象 commit hash` を引き続き要求する。案 A ではこれらが Step 8（コミット）後に取得されるため、契約は満たされる。

### 出力

Pre-Handoff Review の出力フォーマット（`## Pre-Handoff Review` セクション、4 観点判定、verdict）自体は変更しない。

ただし **PHR 出力の Issue 投稿経路を変更する**（後述「方針」1・5 で詳述）。変更前は Step 9（実装完了報告）が PHR ブロックを内包して投稿していたが、変更後は Step 8.5 の各実行ごとに専用の `## Pre-Handoff Review` コメントを直接投稿する。これにより、verdict ループの試行回数が Issue コメント上に 1 試行 1 コメントで永続記録され、PHR_COUNT が試行回数の正しいカウンタとして機能する（Must Fix 1 への対応）。

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

### 1. `.claude/skills/issue-implement/SKILL.md`（移動・改番）

- Step 7.6 ブロック（行 270〜404、子ステップ 7.6.1〜7.6.4 と出力フォーマット含む）を Step 8（コミット）の **後ろ**へ移動。
- 見出しを改番: `Step 7.6` → `Step 8.5`、`Step 7.6.1`〜`7.6.4` → `Step 8.5.1`〜`8.5.4`。本文中の自己参照「本 Step 7.6」も `本 Step 8.5` へ統一。
- Step 7.5 末尾（行 268）の「Step 9 の Issue コメント」参照は不変（Step 9 番号据え置きのため）。

### 2. verdict ループと PHR_COUNT カウンタの再定義（Must Fix 1 対応）

現行 SKILL.md には pre-existing な矛盾がある: verdict ループ表（行 343〜345）は「`With fixes` → Step 7a/7b 再実行 → 本 Step 7.6 を再実行」とし Step 9 を経由しないが、PHR_COUNT 説明（行 350）は「本 Step 7.6 を実行するたびに Step 9 で Issue コメントが追加される」ことを前提にする。ループが Step 9 を経由しない以上、各ループ試行は Issue コメントを増やさず、PHR_COUNT は試行回数を数えられない。案 A の並べ替えはこの矛盾を温存できない（完了条件 3「ループ手順が破綻しない」を満たせない）ため、ループと証跡投稿経路を一意に再定義する。

採用する一意なモデル:

- **Step 8.5.5「Pre-Handoff Review 証跡投稿」を新設**: Step 8.5 を実行するたび（＝各ループ試行ごと）に、その試行の `## Pre-Handoff Review` ブロック（経路 / 起動 agent / 対象 commit / 4 観点 / 指摘事項 / verdict）を **専用の Issue コメントとして即座に投稿**する。PHR 出力の一次投稿経路を Step 8.5 に一元化する。
- **PHR_COUNT は Step 8.5.5 が投稿した `## Pre-Handoff Review` コメント数を数える**。これにより 1 試行 1 コメントで永続記録され、within-run のループ試行回数を正しく永続カウントできる（session-local カウンタを使わない gl:9 の意図を維持）。
- **verdict ループ表**（現行 行 343〜345）を変更後順序へ整合:
  - `Yes` → 「handoff 可。**Step 9（実装完了報告）へ進む**」（コミットは Step 8 で完了済み）。
  - `With fixes` / `No` → 「main session が指摘事項を反映 → Step 7a / 7b を再実行 → **`git commit --amend --no-edit` で実装コミットを更新** → 本 Step 8.5 を再実行（＝再び Step 8.5.5 で証跡コメントを投稿）」。
    - `--amend` を採る理由: 実装を単一の実装コミット（`type:bug` Issue なら `fix:`、`type:feature` なら `feat:` 等、Issue type に対応する prefix）に保ち、ループのたびに `git diff main...HEAD` と `対象 commit hash` が「修正反映後の完全な現在状態」を表すようにする。fixup コミット連鎖でも機能はするが履歴を汚す。未 push branch なので amend は安全。
- **ループ制限判定**: Step 8.5.5 で証跡コメントを投稿した直後に `PHR_COUNT` を再取得する。`verdict ≠ Yes` かつ `PHR_COUNT ≥ 3` → ループ制限到達。さらに繰り返さず、申し送りコメント（現行 行 358 相当）を含めて Step 9 へ進む。`verdict ≠ Yes` かつ `PHR_COUNT < 3` → Step 7a/7b へ戻りループ継続。現行の「次回 Step 9 投稿後の見込み件数 = PHR_COUNT + 1」という *見込み* 計算は、Step 8.5.5 が投稿後カウントへ変わるため「投稿済み実数 PHR_COUNT」での判定に置き換える。
- **Step 9（実装完了報告）の `Pre-Handoff Review 結果` セクション**（現行 行 455〜457）: PHR ブロックの *一次投稿* は Step 8.5.5 が担うため、Step 9 は PHR を再投稿しない。同セクションは「最新の `## Pre-Handoff Review` コメントを参照（投稿数 PHR_COUNT と最終 verdict を要約）」する記述に変更する。`/issue-review-code` Step 1.4 の hard gate は `## Pre-Handoff Review` セクションの存在を grep で確認するが、Step 8.5.5 が投稿する専用コメントがこれを満たすため gate は引き続き成立する。

### 3. `.claude/agents/kaji-code-reviewer.md`

- 行 37 の SoT 注記「`Step 7.6.2 経路 A` の prompt template」を `Step 8.5.2 経路 A` へ改番。入力契約（§ 入力）・出力契約（§ 出力形式）の内容は変更しない（完了条件 2: SKILL.md テンプレートとの整合は、双方が同一契約をミラーする状態を維持）。

### 4. `/issue-review-code` SKILL.md

- 行 91・101 の「`/issue-implement` Step 7.6」を `Step 8.5` へ改番。Step 1.4 の hard gate ロジック（`## Pre-Handoff Review` セクション数・`経路` 行の検出）は不変。Step 8.5.5 が専用コメントで `## Pre-Handoff Review` を投稿するため、hard gate の検出対象は従来どおり存在する。

### 5. 影響 docs の step 番号追従

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
| verdict ループ整合 | `With fixes` / `No` 経路を机上トレースし、(1) 各 Step 8.5 試行ごとに Step 8.5.5 が `## Pre-Handoff Review` コメントを投稿し PHR_COUNT が試行回数と一致すること、(2) `git commit --amend` 後に `git diff main...HEAD` が修正反映済み・`対象 commit hash` が更新されること、(3) `PHR_COUNT ≥ 3` でループ制限が発火し Step 9 へ抜けることを確認する（完了条件 3）。 |
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
| .claude/skills/issue-implement/SKILL.md | あり | 本変更の主対象（Step 7.6→8.5 改番・移動、Step 8.5.5 PHR 証跡投稿の新設、verdict ループと PHR_COUNT の再定義、Step 9 の PHR セクションを参照化） |
| .claude/skills/issue-review-code/SKILL.md | あり | Step 1.4 の「Step 7.6」参照を「Step 8.5」へ改番 |
| .claude/agents/kaji-code-reviewer.md | あり | § 入力 SoT 注記の step 番号参照を改番 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| issue-implement SKILL.md（現行） | `.claude/skills/issue-implement/SKILL.md` 行 270〜411 | Step 7.6（PHR）が Step 8（コミット）より前に配置され、Step 7.6.2 経路 A が `git diff main...HEAD` と `対象 commit hash` を入力要求する記述。defect の所在。 |
| kaji-code-reviewer 入力契約 SoT | `.claude/agents/kaji-code-reviewer.md` 行 35〜47 | 「`## Diff`: `git diff main...HEAD` の全文」「対象 commit hash: prompt 冒頭または header で指定」。本契約はコミット済み実装を前提とする。 |
| issue-review-code Step 1 / 1.4 | `.claude/skills/issue-review-code/SKILL.md` 行 83〜114 | レビュー本体は `git diff main...HEAD`（コミット済み差分）で実施（行 85）。Step 1.4 の PHR hard gate（行 89〜102）は `## Pre-Handoff Review` セクション数と `経路` 行の存在のみを確認する。行 112〜114 の「commit hash で識別」は `## Baseline Check 結果` コメントの選択規則であり、PHR コメント識別の規則ではない（本設計はこの読み取りを訂正済み）。 |
| kaji-code-reviewer 出力契約 | `.claude/agents/kaji-code-reviewer.md` § 出力形式（行 88〜129） | 出力ブロック先頭に `- **対象 commit**: <git-sha>` を必須項目として持つ。案 B 不採用の根拠（コミット前は対象 commit hash が存在せず出力契約が崩れる）。 |
| bug 設計ガイド | `.claude/skills/_shared/design-by-type/bug.md` § 8 | 「修正前に Red になる再現テスト（regression test）を必ず 1 本以上定義」。本ルールがランタイムコード bug 前提であることを踏まえ、instruction-only 変更での省略を testing-convention に基づき正当化。 |
| testing-convention | `docs/dev/testing-convention.md` 行 113〜131 | 「物理的に作成不可 — サンドボックス未提供、対象インターフェース自体が存在しない」を恒久テスト省略の正当理由として列挙。 |
| PHR 導入コミット | `git show 585789b`（`feat: add pre-handoff review with kaji-code-reviewer subagent for gl:9`） | Pre-Handoff Review の初出。導入時に Step 7.6 と Step 8 の順序が入力契約と突き合わされなかったことが根本原因。 |
