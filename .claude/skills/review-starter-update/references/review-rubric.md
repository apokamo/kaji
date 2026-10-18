# Starter update review rubric

この rubric は reviewer が一次情報から独立の変更一覧を作った後に読む。review は無修正で行う。

## Completeness

- 独立一覧と updater の一覧が全件対応し、各変更が一つの 3 区分と具体的根拠を持つ。
- BREAKING CHANGE、skills、workflows、config、docs に反映漏れがない。
- dependency source と lockfile が target kaji version に整合する。
- 独立に再構成した starter-fix Issue（open・`starter-fix` label・本文の `starter_repo:` 行が
  完全一致）が全件区分表にある。区分 (1) は Issue 番号・対象 path・candidate 上で確認した完了条件を
  持ち、区分 (3)（反映済み）は開始点に含まれている根拠を持つ。

## Excess and cleanliness

- package 更新で吸収される資産や kaji maintainer 専用資産を過剰コピーしていない。
- kaji Release 間差分に根拠がない区分 (1) の変更は、上記の starter-fix Issue に対応する場合だけ
  許容する。対応する Issue がない変更、または Issue の対象 path を超える変更は過剰コピーとする。
- `update-starter` / `review-starter-update` / `release-starter` が template payload に混入していない。
- template 生成後の consumer に不要な release 運用情報や秘密情報がない。

## Evidence and identity

- quality gate は対象 repository の実体から解決され、結果が candidate SHA に対応する。
- target tag、base SHA、candidate SHA は実観測と一致する。
- N/A 候補は区分 (1) 空、未 push commit 0、base == candidate を満たす。

Must Fix が一つでもあれば PASS にしない。review 中にファイルを直さず、具体的な再実行先を示す。
