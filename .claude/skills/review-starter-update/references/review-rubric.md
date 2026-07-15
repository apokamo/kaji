# Starter update review rubric

この rubric は reviewer が一次情報から独立の変更一覧を作った後に読む。review は無修正で行う。

## Completeness

- 独立一覧と updater の一覧が全件対応し、各変更が一つの 3 区分と具体的根拠を持つ。
- BREAKING CHANGE、skills、workflows、config、docs に反映漏れがない。
- dependency source と lockfile が target kaji version に整合する。

## Excess and cleanliness

- package 更新で吸収される資産や kaji maintainer 専用資産を過剰コピーしていない。
- `update-starter` / `review-starter-update` / `release-starter` が template payload に混入していない。
- template 生成後の consumer に不要な release 運用情報や秘密情報がない。

## Evidence and identity

- quality gate は対象 repository の実体から解決され、結果が candidate SHA に対応する。
- target tag、base SHA、candidate SHA は実観測と一致する。
- N/A 候補は区分 (1) 空、未 push commit 0、base == candidate を満たす。

Must Fix が一つでもあれば PASS にしない。review 中にファイルを直さず、具体的な再実行先を示す。
