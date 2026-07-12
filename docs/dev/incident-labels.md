# incident ラベル運用ガイド（2 軸）

failure triage の第1層（インシデント検知・集約層。Issue #304 / EPIC #303）が扱う
`incident` ラベル体系の意味と遷移意図をまとめる。ラベルの宣言的定義は
[`.github/labels.yml`](../../.github/labels.yml)、一般的なラベル運用は
[labels.md](./labels.md) を参照。第1層の動作は
[workflow_guide.md](./workflow_guide.md) § 第1層: インシデント検知・集約。

## 2 軸の構成

incident ラベルは **種別キー 1 つ**と、直交する **2 軸**からなる。

- **種別キー** `incident`: 第1層が起票したインシデントイシューであることを示す検索キー。
  第1層はこのラベルで全件検索して照合する。起票時に必ず付与する。
- **status 軸**: インシデントの対応状況。
- **classification 軸**: インシデントの原因分類。

## ラベル一覧と遷移意図

| ラベル | 軸 | 付与者 | 意味 / 遷移意図 |
|--------|-----|--------|-----------------|
| `incident` | 種別キー | 第1層（起票時に必ず） | インシデントイシュー本体。検索・照合のキー |
| `incident:investigating` | status | 第1層（起票時の初期値） | 調査中。第1層が起票時に自動付与する初期状態 |
| `incident:mitigated` | status | 人間 | 暫定緩和済み（恒久対処は未完）。第2層以降の判断で人間が遷移 |
| `incident:resolved` | status | 人間 | 恒久解決済み。人間が付与。以後の同一署名一致はリグレッションとして扱う |
| `incident:cause:internal` | classification | 人間 | kaji 内部起因。第2層の調査結論を受けて人間が付与 |
| `incident:cause:upstream` | classification | 人間 | 上流（外部 CLI / API）起因。人間が付与 |
| `incident:cause:environment` | classification | 人間 | 実行環境起因。人間が付与 |
| `incident:cause:transient` | classification | **第1層が自動付与** | 一過性。auto-resume 自己回復時に第1層が付与し即クローズ |

## 自動付与の範囲（第1層がラベルに触れる箇所）

第1層が**自動で**ラベルを操作するのは次の 2 箇所のみ。それ以外の遷移は人間が行う。

1. **起票時**: `incident` + `incident:investigating` を付与する。
2. **transient 即クローズ時**: `--auto-recover` の child run が `COMPLETE`（自己回復）し、かつ
   この run が起票したインシデントに対して、`incident:cause:transient` を付与し
   `incident:investigating` を外してクローズする。

status 軸の `mitigated` / `resolved` と、classification 軸の `internal` / `upstream` /
`environment` は**すべて人間が付与する**。第1層はこれらを自動で付けない。

## 照合規則との関係

ラベルは照合（`plan_incident_action`）の分岐条件になる。

| 一致した既存インシデントの状態 | 第1層のアクション |
|--------------------------------|-------------------|
| open | occurrence コメントを追記（回数 +1） |
| closed かつ `incident:cause:transient` あり | reopen せず occurrence コメントを追記 |
| closed かつ `incident:cause:transient` なし（人間 resolve 済み） | 新規起票し旧イシューへリンク（リグレッション検知） |
| 一致なし | 新規起票 |

- `incident:cause:transient` が付いた closed インシデント（第1層の自動クローズ分）は、以後も
  closed のまま occurrence が追記され、頻発パターンの昇格判断材料になる。
- 人間が `incident:resolved` 相当でクローズしたインシデントに同一署名が再来した場合は、
  reopen せず新規起票して旧イシューへリンクする（resolve 済みを蒸し返さず、リグレッションを
  独立に追跡する）。

## 遷移の機械強制はしない

status 軸・classification 軸の遷移順序（例: investigating → mitigated → resolved）は
**機械的に強制しない**。第1層は初期状態と transient 自動クローズだけを担い、以降の運用判断
（原因分類・緩和・解決の宣言）は人間と第2層（調査・提案。Issue #305）に委ねる。

## 調査フローと処遇判断（第2層・Issue #305）

第2層（インシデント原因調査・対応策提案。手動起動）は、第1層が起票したインシデントイシューを入力に
**調査 → 査読 → 修正 → 確認 → 最終提案**のレビュー収束サイクルを回す。詳細は
[workflow_guide.md](./workflow_guide.md) § 第2層: 調査・提案。

- **起動**: `/incident-cycle <incident_issue_id>`（手動のみ）。
- **出力**: 調査結論（conclusion）＋対応策＋処遇メニューを含む最終提案コメント。**ラベル遷移・
  クローズ・バグイシュー化・統合の実行は行わない**（すべて人間の処遇判断）。
- **調査結論とレビュー verdict は別軸**（EPIC #303 決定 D）: 証拠不足のときは `INCONCLUSIVE`
  （棄却済み仮説＋不足証拠）を返し、記述が十分ならレビュー品質としては PASS になり得る。

### conclusion → 推奨ラベル・後続アクション（処遇メニュー）

最終提案コメントが提示する対応表。**実行はすべて人間**。第2層はラベルに触れない。

| 調査結論（conclusion） | 推奨 classification ラベル | status 軸の目安 | 後続アクション（人間が実行） |
|------------------------|----------------------------|-----------------|------------------------------|
| `internal-bug` | `incident:cause:internal` | `incident:mitigated` → `incident:resolved` | バグイシュー化ドラフトの起票、緩和策 / 恒久対策の判断 |
| `upstream` | `incident:cause:upstream` | `incident:mitigated` 等 | 上流 issue への報告 / watch、回避策の適用 |
| `environment` | `incident:cause:environment` | `incident:mitigated` 等 | 実行環境の修正、運用手順の更新 |
| `transient` | `incident:cause:transient`（第1層が自動付与済みの場合あり） | closed 維持が多い | 再発頻度を監視し、頻発なら昇格判断 |
| `duplicate` | 統合先に準ずる | 統合先に集約 | 統合先イシューへの集約（実行は人間） |
| `INCONCLUSIVE` | 付与しない | `incident:investigating` 維持 | 不足証拠を収集後に再調査 |

- `incident:cause:*` の付与、`incident:mitigated` / `incident:resolved` への遷移は、第2層の提案を
  受けて**人間が付与する**（第1層の transient 自動付与を除く）。第2層はラベルを自動で操作しない。
- `risk-accepted` は人間専用の処遇語彙であり、第2層エージェントの出力（conclusion / 提案文面）には
  現れない。リスク受容の宣言は人間が行う。

## 関連ドキュメント

- [labels.md](./labels.md) — GitHub ラベル全体の運用ガイド
- [workflow_guide.md](./workflow_guide.md) § failure triage と自動再開 / 第1層
- [failure-recovery.ja.md](../cli-guides/failure-recovery.ja.md) — triage / recovery CLI リファレンス
