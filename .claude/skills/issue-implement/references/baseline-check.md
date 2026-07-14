# Baseline Check

この reference は `/issue-implement` Step 2.5 でのみ読む。責務・停止基準を変更せず、開始時の固定読込から分離するための手順書である。

## 実行

実装開始前に変更前の全 pytest を実行する。

```bash
cd [worktree_dir] && source .venv/bin/activate && pytest
```

- 全パスなら baseline は clean。コメントせず次へ進む。
- FAILED / ERROR があれば、各失敗の `(nodeid, kind, error_type)` を記録する。

## 失敗時の Issue コメント

````bash
kaji issue comment [issue_id] --commit --body "$(cat <<'BASELINE_EOF'
## Baseline Check 結果

### 実行環境

- **Commit**: [commit-hash]
- **コマンド**: `pytest`

### Baseline Failure 一覧

| nodeid | kind | error_type | 概要 |
|--------|------|------------|------|
| tests/test_foo.py::test_bar | FAILED | AssertionError | expected 1, got 2 |
| tests/test_baz.py::test_qux | ERROR | ImportError | No module named 'xxx' |

### Regression 判定キー

上記の `(nodeid, kind, error_type)` を比較キーとする。

- 3タプル一致: baseline failure
- 不一致の新規 FAILED / ERROR: regression
- baseline にあった失敗が PASSED: 問題なし

### 判定

- **継続**: 変更前から存在し、本 Issue の対象外
- **停止**: (該当時のみ理由を記載)
BASELINE_EOF
)"
````

Baseline コメントには verdict marker を付けない。

## 停止基準

次のいずれかなら実装を止める。

- baseline failure が本 Issue の実装対象と同一モジュール・機能に影響する
- 失敗が多く regression の切り分けが困難（目安: 10件超）

継続時は以降の pytest で3タプルを比較し、新規 regression だけを修正対象とする。

## 複数コメントの選択

`## Baseline Check 結果` が複数ある場合は最新コメントを正とする。各コメントの commit hash で測定時点を識別する。
