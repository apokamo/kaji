# Starter change classification guide

この reference は `update-starter` が開始点と target の一次情報を取得した後に読む。

## 調査集合

- kaji Release 間の CHANGELOG 全項目、commit、changed path を照合し、漏れのない一覧を作る。
- BREAKING CHANGE、skills、workflows、config、docs、dependency source、lockfile を個別に確認する。
- 開始点は最新の公開済み starter Release tag のみ。pin や任意 commit を fallback にしない。

## 3 区分

| 区分 | 判定 | 必須 evidence |
|---|---|---|
| (1) starter へ反映 | template から生成される repository の挙動・運用資産が変わる | 対象 path、移植方法、candidate 上の確認 |
| (2) package 更新で吸収 | kaji package 内だけで完結し template asset は不変 | package 境界と dependency / lock 整合 |
| (3) starter には不要 | kaji maintainer 専用、内部 CI、starter に過剰な資産 | 不要理由。無理由の N/A 禁止 |

同じ変更を複数区分へ重複させず、全調査項目に一つの区分を割り当てる。maintainer 専用の
starter 3 skill は常に区分 (3)。dependency pin / lockfile の更新だけで分類を終了しない。

## Candidate report

区分表に target tag、remote main の base SHA、local main HEAD の candidate SHA、実行した quality gate と
結果を付ける。N/A 候補は区分 (1) が空、未 push commit 0、base SHA == candidate SHA をすべて満たす。
