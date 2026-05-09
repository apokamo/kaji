---
id: local-pc5090-18
title: 'post-fix: dev workflow final-check に BACK transition 不足 (skill/YAML 不整合)'
state: closed
slug: post-fix-dev-workflow-final-check-back-t
labels:
- type:bug
created_at: '2026-05-09T13:35:15Z'
closed_at: '2026-05-09T13:35:22Z'
closed_by: pc5090
close_reason: skill/YAML 不整合を commit e7b9c96 で修正済み。記録目的のため即クローズ。
---
## ステータス

**誤対応・revert 済み** （2026-05-09）

## 経緯

local-pc5090-16 の作業中、fix-code 完了後に verify-code 未取得のまま `/i-dev-final-check local-pc5090-16` を手動実行した際、skill が `SKILL.md:104-106` の規定通り `BACK` を返したが、`feature-development-local.yaml` の `final-check` step に `BACK` transition が宣言されておらず harness が `InvalidVerdictValue` で reject した。

## 当初の対応（誤り）

`feature-development-local.yaml` の `final-check` step に `BACK: review-code` を追加（commit `e7b9c96`）。

## なぜ誤りだったか

論点を取り違えていた:

- 真の異常は **「fix-code 完了後 verify-code Approve 未取得のまま final-check に到達した」** こと（前段の遷移制御 or 手動操作のミス）
- final-check skill は異常を **検出した**（BACK 出力）。この検出自体は正しい働き
- しかし「どこに戻すべきか」の判断ロジック（cycle 全体を再実行 vs 単発で verify-code だけ呼ぶ）は **未実装**
- 戻し先を `review-code` にしても cycle 全体が再実行されるだけで、「verify-code Approve だけ取れば済む」状況には過剰
- → **「適切に戻せない」のだから ABORT で止めて人間に判断させるのが正しい設計**

YAML 側に BACK transition を追加する対応は、戻し先設計が定まっていない段階での先走りであり、structural な解決にはならない。

## revert

- revert commit: 2df4c13 (Revert "chore(wf): add BACK: review-code transition...")

## 真の対応（別 Issue で議論予定）

skill `i-dev-final-check`（および `i-doc-final-check`）の SKILL.md を「fix/verify 未経由 → ABORT」に変更すべきだが、これは「複数 step への戻しを判断する設計議論」と一体で扱う必要がある（過去議論あり）。本 Issue のスコープ外として保留し、別 Issue で議論する。

## 教訓

- skill が valid と documenting している status 値が workflow YAML の `on:` ブロックにない場合、**まず「skill が出力すべきでない状況ではないか」を疑う**
- YAML に transition を増やす前に、戻し先設計が完成しているかを確認する
- 「skill 仕様に書かれている = 正しい挙動」とは限らない。skill 仕様自体が未完成な可能性を考慮する
