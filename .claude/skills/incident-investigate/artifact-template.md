# インシデント調査報告: #<incident_issue_id>

<!--
調査 artifact テンプレート（kaji 第2層。Issue #305 / EPIC #303 決定 A・B・D）。
- 必須セクション見出し（メタデータ / 可読サマリ / 結論 / 根拠 / 棄却済み仮説 / 不足証拠）と
  conclusion 6 値の語彙はテストで固定される（tests/workflows/test_incident_workflow.py）。
- conclusion（調査結論）とレビュー verdict（PASS/RETRY/ABORT）は別軸（#303 決定 D）。
  結論が INCONCLUSIVE でも記述が充足していればレビュー verdict は PASS になり得る。
- `risk-accepted` は人間専用語彙。本 artifact の出力語彙に含めない。
- ログ引用は sanitize 済み（トークン・資格情報・秘匿 URL をマスク）であること。
-->

## メタデータ

- 対象インシデントイシュー: #<incident_issue_id>
- 調査対象 run_id 一覧: <run_id, ...>
- 再発回数 N: <occurrence marker のユニーク run_id 件数>
- 提案役モデル: <investigate step の実行中モデル ID>
- 査読役モデル: <incident-review が追記>
- 査読経路: <subagent | main-session（incident-review が追記）>
- モデル縮退: <あり / なし>（理由: <縮退の経緯。提案役と査読役が同一モデルなら「あり」>）
- モデル値の情報源: <self-reported | configured>

## 可読サマリ

（2〜3 文。人間向けの平易な要約。テンプレート生成の起票本文より高い可読性を提供する）

## 結論

- conclusion: `internal-bug` | `upstream` | `environment` | `transient` | `duplicate` | `INCONCLUSIVE`
- 確度: <高 / 中 / 低>
- 断定に用いた実証（再現 or 実障害ログ引用）への参照: <§ 根拠（citation）の該当項目 / § 調査手順の実施記録⑤>

> **結論を断定できないことは失敗ではない。** 証拠が不足する場合は `INCONCLUSIVE` を選び、
> § 棄却済み仮説と § 不足証拠を充足させること（記述が十分ならレビュー verdict は PASS になり得る）。

## 根拠（citation）

（実障害ログの引用。各引用に出典 `<run_id>:<ファイル>` を付す。sanitize 済みであること）

- 引用 1（出典 `<run_id>:<path>`）: ...
- 引用 2（出典 `<run_id>:<path>`）: ...

## 調査手順の実施記録

（#301 の手順①〜⑥。⑤ 再現実験は「実施不能」の場合も理由を記録する）

- ① 一時的か判定: <再発回数 N・auto-resume 自己回復の有無・発生間隔からの transient 評価>
- ② 識別署名の確認: <identity marker / fingerprint block が障害実体と整合するか。過剰統合の検出を含む>
- ③ バージョンの時系列: <発生 run 前後の CLI / 依存バージョン変化と発生時期の相関>
- ④ 上流既知不具合と照合: <WebSearch / WebFetch による独立検索の結果と出典 URL>
- ⑤ 再現実験: <使い捨て環境での最小再現の結果（成功 / 失敗 / 実施不能の理由）>
- ⑥ 内部／外部要因の切り分け: <①〜⑤から conclusion への到達過程、または INCONCLUSIVE 判断>

## 棄却済み仮説

| 仮説 | 反証根拠（citation） |
|------|---------------------|
| <仮説 1> | <反証根拠と出典 `<run_id>:<path>`> |

## 意味的類似インシデント

（第1層のあいまい候補の再評価＋独自に発見した類似。`duplicate` 統合提案の根拠。実行は人間）

- 候補 1（#<id>）: <署名一致でないが意味的に類似する根拠 / 非類似の根拠>

## 対応策の提案

（緩和策 / 恒久対策 / バグイシュー化ドラフト。すべて提案であり実行は人間）

- 緩和策: ...
- 恒久対策: ...
- バグイシュー化ドラフト（起票する場合の本文案。auto-close hazard を含めない）: ...

## 不足証拠（INCONCLUSIVE 時は必須）

（何が得られれば断定できるかの列挙。conclusion が INCONCLUSIVE 以外なら「該当なし」と明記可）

- 不足 1: ...
