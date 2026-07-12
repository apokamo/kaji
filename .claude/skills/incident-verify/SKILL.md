---
description: 第2層。査読で挙がった既出指摘が解消されたかのみを確認する。新規指摘は行わない（レビュー収束のため）。全指摘解消なら PASS（report へ）、未解消ありなら RETRY（fix へ）。
name: incident-verify
---

# Incident Verify（確認）

修正後の調査 artifact について、**既出指摘が解消されたかのみ**を確認する。

> **重要**: このスキルは「指摘が適切に解消されたか」のみを確認する。**新規の指摘は行わない**。
> これはレビューサイクルの収束を保証するため（`issue-verify-code` / `issue-verify-design` と同じ収束規則）。

**ワークフロー内の位置**: fix → **verify** →（PASS: report / RETRY: fix）

## 入力

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 対象インシデントイシュー ID |
| `issue_ref` | str | 人間可読の Issue 参照 |
| `step_id` | str | 現在のステップ ID |
| `cycle_count` / `max_iterations` | int | サイクル内ステップのため注入される |

手動実行時は `$ARGUMENTS` 第 1 トークンを `issue_id` とする。

## 共通ルール

`.claude/skills/incident-investigate/SKILL.md` § 全 incident-* skill 共通ルールに従う。

## verify と review の違い

| 項目 | review | verify |
|------|--------|--------|
| 目的 | 反証・独立検証を伴うフル査読 | 既出指摘の解消確認のみ |
| 新規指摘 | する | **しない** |
| 確認範囲 | 調査 artifact 全体 | 前回指摘箇所のみ |

## 実行手順

### Step 1: 指摘と対応の取得

```bash
kaji issue view [issue_id] --comments
```

直近の査読結果コメント（`指摘 N`）と、直近の fix 対応表を突き合わせる。

### Step 2: 解消確認

指摘 N ごとに「解消 / 未解消 / 反論受理」を判定する。

- **解消**: 修正内容が指摘意図を満たす（追加 citation・再現結果が受理基準を満たす等）。
- **未解消**: 修正が不十分 / 意図と異なる。
- **反論受理**: fix の反論が技術的に妥当（根拠が明確・論理に飛躍なし）→ 指摘取り下げ。

**新規指摘はしない**。確認中に前回指摘以外の問題を見つけた場合は、判定に含めず参考情報として記録する
（`_shared/report-unrelated-issues.md`）。

### Step 3: 確認結果のコメント投稿

チェックリスト形式（指摘 N ごと）でコメント投稿する。verdict マーカーを無条件付与する。

```bash
kaji issue comment [issue_id] --commit \
  --verdict-step verify --verdict-status <STATUS> \
  --body-file <確認結果 markdown>
```

## Verdict 出力

---VERDICT---
status: PASS
reason: |
  全指摘の解消 / 反論受理を確認した
evidence: |
  指摘 1..N をチェックリストで確認。未解消 0 件
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 全指摘が解消 / 反論受理（report へ） |
| RETRY | 未解消の指摘あり（fix へ戻す） |
| ABORT | 前提崩壊（対象が非インシデント等） |
