---
description: 第2層。収束した調査 artifact を正本に、インシデントイシューへ最終提案コメント（可読サマリ・調査結論・対応策・意味的類似の指摘・統合提案・処遇メニュー・モデルメタデータ）を投稿する。ラベル操作・クローズ・起票は行わない（実行は人間）。
name: incident-report
---

# Incident Report（最終提案）

収束した調査 artifact を正本として、インシデントイシューへ**最終提案コメント**を投稿する。
review PASS（初回受理）と verify PASS（修正後受理）の 2 経路がここに合流する。

**全終端は「提案」**（#303 決定 D）: ラベル付与・除去、クローズ / reopen、バグイシュー化、統合の実行は
**一切行わない**。それらは人間の処遇判断。

**ワークフロー内の位置**: review/verify（PASS）→ **report** → end

## 入力

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 対象インシデントイシュー ID |
| `issue_ref` | str | 人間可読の Issue 参照 |
| `step_id` | str | 現在のステップ ID |

手動実行時は `$ARGUMENTS` 第 1 トークンを `issue_id` とする。

## 共通ルール

`.claude/skills/incident-investigate/SKILL.md` § 全 incident-* skill 共通ルールに従う。

## 実行手順

### Step 1: 調査 artifact の読み込み

artifact root を解決する（共通ルール参照）:

```bash
ART="$(kaji config artifacts-dir)"
```

`$ART/[issue_id]/investigation/report.md`（収束済み）を正本として読み込む。

### Step 2: 最終提案コメントの構成

以下を含む最終提案コメントを構成する（Issue 完了条件の「可読サマリ・意味的類似の指摘・統合提案」に対応）。

1. **可読サマリ**: 障害の 2〜3 文の平易な要約（テンプレート生成の起票本文より高い可読性）。
2. **調査結論**: conclusion（6 値）＋確度＋根拠 citation の要約。
3. **対応策の提案**: 緩和策・恒久対策（バグイシュー化する場合の本文ドラフトを含む。実行は人間）。
4. **意味的類似インシデントの指摘**: 第1層のあいまい照合候補を意味レベルで再評価し、文字列類似では
   拾えない類似も指摘する（#303 決定 F）。
5. **統合提案**: conclusion が `duplicate` の場合、統合先イシューと根拠（実行は人間）。
6. **処遇メニュー**: conclusion → 推奨ラベル・後続アクションの対応表（チェックボックス形式。人間が
   そのまま実行判断できる形。`docs/dev/incident-labels.md` の対応表と整合させる）。
7. **モデルメタデータ**: 提案役 / 査読役モデル・査読経路・縮退の有無。

処遇メニューの対応表（conclusion → 推奨ラベル・後続アクション。実行は人間）:

| conclusion | 推奨 status ラベル | 推奨 classification ラベル | 後続アクション（人間が実行） |
|------------|--------------------|----------------------------|------------------------------|
| `internal-bug` | `incident:mitigated` 等 | `incident:cause:internal` | バグイシュー化ドラフトを起票、緩和/恒久対策の判断 |
| `upstream` | `incident:mitigated` 等 | `incident:cause:upstream` | 上流 issue への報告 / watch、回避策の適用 |
| `environment` | `incident:mitigated` 等 | `incident:cause:environment` | 環境修正、運用手順の更新 |
| `transient` | （第1層が自動付与済みの場合あり） | `incident:cause:transient` | 頻度を監視。頻発なら昇格判断 |
| `duplicate` | （統合先に集約） | 統合先に準ずる | 統合先への集約（実行は人間） |
| `INCONCLUSIVE` | `incident:investigating` 維持 | 付与しない | 不足証拠の収集後に再調査 |

> `risk-accepted` は人間専用語彙であり、本コメントの出力語彙に含めない。

### Step 3: 最終提案コメントの投稿

verdict マーカーを無条件付与する。auto-close hazard 回避規約に従う（提案文面で「バグイシュー化」等の
名詞表現を使い、ハザードパターンと issue 番号の連接を避ける）。

```bash
kaji issue comment [issue_id] --commit \
  --verdict-step report --verdict-status <STATUS> \
  --body-file <最終提案 markdown>
```

**ラベル操作・クローズ・イシュー起票は一切行わない。** PASS: end。

## Verdict 出力

---VERDICT---
status: PASS
reason: |
  最終提案コメントを投稿した（可読サマリ・調査結論・対応策・類似・統合提案・処遇メニュー）
evidence: |
  conclusion=<値>、処遇メニュー・モデルメタデータを含む最終提案を投稿
suggestion: |
  人間がラベル遷移・クローズ・バグイシュー化・統合の実行を判断する
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 最終提案コメントを投稿した |
| ABORT | 前提崩壊（対象が非インシデント / artifact 不在等） |
