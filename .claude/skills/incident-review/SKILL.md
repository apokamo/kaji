---
description: 第2層。調査 artifact に対する実行型査読。使い捨て検証環境を準備し kaji-incident-reviewer subagent を起動、反証義務・独立検証・受理基準（実証）で判定して査読結果コメントと verdict を発行する。査読結論とレビュー verdict は別軸。
name: incident-review
---

# Incident Review（査読）

調査 artifact に対する**実行型査読**。main session は環境準備・転記・verdict 発行のみを担い、
検証本体は `kaji-incident-reviewer` subagent が使い捨て検証環境の中で行う。

**判定軸の分離（#303 決定 D）**: 査読の評価対象は **調査品質のみ**（受理基準の充足・反証への耐性・
記述の充足）であり、conclusion の値そのものではない。「結論は `INCONCLUSIVE` だが棄却仮説・不足証拠・
再現結果の記述が十分なので verdict は PASS」を明示的に許可する。

**ワークフロー内の位置**: investigate → **review** →（PASS: report / RETRY: fix）

## 入力

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 対象インシデントイシュー ID |
| `issue_ref` | str | 人間可読の Issue 参照 |
| `step_id` | str | 現在のステップ ID |
| `cycle_count` / `max_iterations` | int | サイクル内ステップのため注入される |

手動実行時は `$ARGUMENTS` 第 1 トークンを `issue_id` とする。

## 共通ルール

`.claude/skills/incident-investigate/SKILL.md` § 全 incident-* skill 共通ルールに従う
（`worktree_dir` 非参照 / verdict 3 経路 / foreground + timeout / 副作用禁止 / ログ sanitize /
auto-close hazard 回避）。

## 受理基準（§ 別軸設計。#303 決定 A / D）

- conclusion が `internal-bug` / `upstream` / `environment` / `transient` / `duplicate` の場合:
  **実再現、または実障害ログの引用（`<run_id>:<ファイル>` 付き citation）が必須**。欠けば RETRY。
- conclusion が `INCONCLUSIVE` の場合: 棄却済み仮説（反証根拠つき）・不足証拠の列挙・
  試行した再現の記録が必須。欠けば RETRY。充足していれば PASS（結論が `INCONCLUSIVE` であること自体は
  減点しない）。
- `risk-accepted` は人間専用語彙。査読結果の語彙に含めない。

## 実行手順

### Step 0: 前提ガード

調査 artifact（`.kaji-artifacts/[issue_id]/investigation/report.md`）と直近の調査報告コメントが
存在することを確認する。対象が非インシデント（`incident` ラベルなし / identity marker なし）と判明した
場合は ABORT。

### Step 1: 入力の収集

1. 調査 artifact 全文・インシデントイシュー本文・直近の調査報告コメントを読む。
2. artifact のメタデータから調査対象 run_id 一覧・提案役モデルを取得する。

### Step 2: 使い捨て検証環境の準備

```bash
# 例: main の HEAD を detach した一時 worktree。査読後に破棄する。
git worktree add --detach /tmp/kaji-incident-review-[issue_id] HEAD
```

隔離 venv または scratch dir でも可。**main checkout を検証で汚さない**ことが要件。

### Step 3: 査読役の起動（capability-based fallback）

`docs/dev/development_workflow.md` § Pre-Handoff Review と同型の分岐で査読役を起動する。

- **経路 A（subagent）**: Agent tool で `subagent_type: "kaji-incident-reviewer"` を起動する。
  prompt に「対象イシュー番号・本文/コメント要約・調査 artifact 全文・検証環境パス・調査対象 run_id
  一覧・提案役モデル」を渡す（入力契約の SoT は `.claude/agents/kaji-incident-reviewer.md` § 入力）。
- **経路 B（self-review, fallback）**: Agent tool が使えない runtime / 起動失敗時は、main session が
  `.claude/agents/kaji-incident-reviewer.md` の rubric を自セッションで適用する。この場合は**縮退**。

### Step 4: 報告の受領と環境破棄

subagent の報告（反証の試行・独立検証・再現の再実行・独立検索・受理可否の推奨
`accept` / `needs-fix` / `reject`・**自己申告モデル ID**）を受領する。使い捨て検証環境を破棄する:

```bash
git worktree remove --force /tmp/kaji-incident-review-[issue_id]
```

### Step 5: モデルメタデータの追記（#303 決定 B）

調査 artifact の**メタデータセクションのみ**に、`査読役モデル`（subagent の自己申告値、取得できなければ
frontmatter の設定値）・`査読経路`（`subagent` / `main-session`）・`モデル縮退`（提案役モデルと査読役
モデルが同一なら「あり」）を追記する。**調査本文は変更しない**。縮退時は verdict の evidence にも明記する。

### Step 6: 査読結果コメントの投稿と verdict 発行

受理基準（§ 受理基準）と subagent の推奨を突き合わせ、査読結果コメント（verdict マーカー付き）を投稿する。

```bash
kaji issue comment [issue_id] --commit \
  --verdict-step review --verdict-status <STATUS> \
  --body-file <査読結果 markdown>
```

verdict:

| 推奨 / 状態 | verdict | 遷移 |
|-------------|---------|------|
| 受理基準を満たす（`accept`） | PASS | report |
| 指摘あり・修正で収束可（`needs-fix` / `reject`） | RETRY | fix |
| 前提崩壊（対象が非インシデント等） | ABORT | end |

査読結果コメントには指摘を `指摘 N` 形式で列挙する（fix が参照する）。

## Verdict 出力

---VERDICT---
status: PASS
reason: |
  受理基準を満たす調査品質を確認した
evidence: |
  実証（再現 or 実障害ログ引用）あり / INCONCLUSIVE の記述充足。査読経路=<subagent|main-session>、縮退=<あり|なし>
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 受理基準を満たす（調査品質が十分。conclusion 値は問わない） |
| RETRY | 指摘あり。fix で修正して再確認 |
| ABORT | 前提崩壊（対象が非インシデント等） |
