---
description: 第2層。査読 RETRY の指摘に対し、追加調査・再実験・artifact 修正、または技術的根拠を示した反論で対応する。調査セッションを継続（resume）し、指摘ごとの対応表をコメント投稿する。scope 拡大はしない。
name: incident-fix
---

# Incident Fix（修正）

直近の査読 RETRY コメントの指摘に対応し、調査 artifact を更新する。`incident.yaml` では
`resume: investigate`（調査セッションを継続）＋ `inject_verdict: true`（査読要約の注入）で起動される。

**収束保証**: 指摘対応以外の scope 拡大（新しい調査論点の追加）は行わない。

**ワークフロー内の位置**: review/verify（RETRY）→ **fix** → verify

## 入力

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 対象インシデントイシュー ID |
| `issue_ref` | str | 人間可読の Issue 参照 |
| `step_id` | str | 現在のステップ ID |
| `cycle_count` / `max_iterations` | int | サイクル内ステップのため注入される |

`inject_verdict: true` により直近査読 verdict の要約が prompt に注入される。手動実行時は
`$ARGUMENTS` 第 1 トークンを `issue_id` とする。

## 共通ルール

`.claude/skills/incident-investigate/SKILL.md` § 全 incident-* skill 共通ルールに従う。

## 実行手順

### Step 1: 指摘の抽出

直近の査読 RETRY コメント（verdict マーカー `step=review` または `step=verify` の最新）から
指摘リスト（`指摘 N`）を抽出する:

```bash
kaji issue view [issue_id] --comments
```

### Step 2: 指摘への対応

各指摘に対し、以下のいずれかで対応する。

- **追加調査・再実験・artifact 修正**: 不足していた citation の追加、再現実験の再試行、
  棄却仮説・不足証拠の補強、conclusion の見直し。使い捨て検証環境を使う場合は調査後に破棄する。
- **技術的根拠を示した反論**: 指摘が不当と判断する場合、根拠（citation / 再現結果）を添えて反論する。

調査 artifact（`.kaji-artifacts/[issue_id]/investigation/report.md`）を更新する。**新しい調査論点の
追加（scope 拡大）はしない**。

### Step 3: 対応表のコメント投稿

指摘ごとの対応表（対応 / 反論 / 一部対応）をコメント投稿する。指摘参照は `指摘 N` 形式
（auto-close hazard 回避）。verdict マーカーを無条件付与する。

```bash
kaji issue comment [issue_id] --commit \
  --verdict-step fix --verdict-status <STATUS> \
  --body-file <対応報告 markdown>
```

## Verdict 出力

---VERDICT---
status: PASS
reason: |
  査読指摘に対応し、調査 artifact を更新した
evidence: |
  指摘 1..N それぞれに対応 / 反論を記録。追加 citation・再現結果を artifact に反映
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 全指摘に対応 / 反論した（verify で確認へ） |
| ABORT | 前提崩壊（対象が非インシデント等） |
