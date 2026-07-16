# Deterministic Baseline Check

dev workflow の実装前 pytest baseline、既知 failure の停止基準、最終 regression 比較の正本。
判定ロジックの正本は `kaji_harness/baseline.py`、実行 entrypoint は
`kaji_harness.scripts.baseline_precheck` とする。

## workflow 上の位置

4 つの dev workflow は `review-design` または `verify-design` の PASS 後に agentless な
`baseline` step を実行し、PASS の場合だけ `implement` へ進む。

```text
review-design / verify-design --PASS--> baseline --PASS--> implement
                                      `--ABORT--> end
```

baseline step は全 pytest を一度実行し、固定 path
`[worktree]/.kaji-artifacts/baseline/baseline.json` へ atomic write する。
「未実行」は artifact 不在、「実行済み clean」は `status: clean` であり、コメント有無では
判定しない。

## artifact schema

artifact は Pydantic v2 で読み書きの両方を検証する。`schema_version: 1`、Issue / branch、
`measured_commit`、UTC の `measured_at`、pytest exit code、summary、status、stop_reason、
failure 一覧を持つ。failure の regression 比較キーは
`(nodeid, kind, error_type)`。`kind` は call failure が `FAILED`、setup / teardown / collection
error が `ERROR` となる。

status は次の 4 値だけを取る。

| 条件 | status | verdict |
|------|--------|---------|
| exit 0、failure 0 | `clean` | PASS |
| exit 1、failure 1〜10 | `known_failures` | PASS |
| exit 1、failure 11 以上 | `blocked` (`mass_failures`) | ABORT |
| report 不整合・欠損、または exit 0/1 以外 | `invalid` | ABORT |

exit code は fail-closed とし、2〜6、未知値、signal 由来の負値をすべて
`unexpected_exit_code:<code>` の `invalid` にする。

## pytest report の取得

JUnit XML や pytest text はパースしない。内部 plugin
`kaji_harness/pytest_baseline_plugin.py` が runtest / collection hook の
`ExceptionInfo` から raw nodeid、phase、例外型を収集し、JSON report を atomic write する。
entrypoint は worktree の `.venv/bin/python` を使い、shell を介さず pytest を起動する。

## implement 開始時

`issue-implement` は artifact を検証し、`measured_commit` が HEAD の ancestor であることを
確認する。`known_failures` の場合、設計書の変更 scope を次の評価関数へ渡す。

```bash
python -m kaji_harness.scripts.baseline_precheck \
  --evaluate --scope kaji_harness/example.py --scope tests/test_example.py
```

failure nodeid の file path が scope と完全一致、または scope directory の配下なら
`stop: true`。`stop: false` でも間接依存など意味的に同一機能へ影響する場合は agent が停止する。
件数・path overlap は deterministic entrypoint、意味的関連性は agent の責務とする。

## コミット前と後段 review

artifact が `clean` の場合は通常どおり `make check` を使う。`known_failures` の場合は
非 pytest gate を全対象へ実行した上で、pytest を次の比較へ置換する。

```bash
python -m kaji_harness.scripts.baseline_precheck --compare
```

コミットを許可するのは、ruff / format / mypy が全 PASS し、`--compare` が
`verdict: ok`、`regressions: []` を返す場合だけ。`issue-implement` / `issue-review-code` /
`issue-fix-code` / `issue-verify-code` / `i-dev-final-check` は同じ artifact と比較関数を使い、
Issue コメントの検索、pytest text の再パース、手動 3 タプル比較を行わない。

## 再測定・resume

measure が artifact を書けるのは、非 design の実装 commit がなく、working tree が clean な
場合だけ。実装 commit がある場合は再測定しない。

- valid artifact の `measured_commit` が HEAD の ancestor: artifact をそのまま reuse
- artifact 不在・不正・非 ancestor: `baseline_unrecoverable_post_implement` で ABORT
- 実装前でも dirty tree: `dirty_worktree` で ABORT

これにより、artifact は常に「実装 commit を含まない clean tree で測定された変更前 baseline」
となる。コメントは `status != clean` の人間向け証跡であり、consumer は読まない。local provider
ではコメントファイルの `commit_issue_change()` 完了後にのみ verdict を保存する。
