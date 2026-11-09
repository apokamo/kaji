---
description: 実装前 pytest baseline を deterministic に測定・構造化し、workflow verdict を返す。
name: baseline-precheck
exec_script: kaji_harness.scripts.baseline_precheck
---

# Baseline Precheck

設計レビュー PASS 後、実装開始前に agent を起動せず pytest baseline を一度測定する。

## 入力

- `KAJI_WORKTREE_DIR`: 測定対象 worktree
- `KAJI_ISSUE_ID`: 証跡コメントの投稿先
- `KAJI_BRANCH_NAME`: artifact に記録する branch
- `KAJI_GIT_REMOTE` / `KAJI_DEFAULT_BRANCH`: 実装 commit 判定の基点 `<KAJI_GIT_REMOTE>/<KAJI_DEFAULT_BRANCH>`
  （remote 側の default branch。ローカルの default branch は使わない）
- `KAJI_VERDICT_PATH`: pure YAML verdict の保存先

## 出力

構造化 artifact の正本は
`[worktree]/.kaji-artifacts/baseline/baseline.json`。`clean` / `known_failures` は
PASS、`blocked` / `invalid` は ABORT を返す。基点 ref が解決できない場合は artifact を作らず
`baseline_base_ref_unresolvable` で ABORT する。詳細ポリシーは
[`docs/dev/baseline-check.md`](../../../docs/dev/baseline-check.md) を参照する。
