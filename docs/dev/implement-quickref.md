# Issue Implement Quick Reference

`/issue-implement` が開始時に読む、最小規律と正本へのポインタ。
規則の詳細・例外・閾値はここへ複製せず、必要になった時点で対応する正本セクションを読む。

## 開始時に保持する最小規律

- 承認済み設計書を契約とし、Red → Green → Refactor の順を崩さない。
- 設計書は解決した正確なパスから1回だけ Read し、shell 出力との二重取得をしない。
- 既読ファイルは対象セクションを部分 Read し、条件なしの全文再 Read をしない。
- Python コードを書く前に、AGENTS.md が要求する `docs/reference/python/*.md` をロードする。
- コミット前に ruff check、ruff format check、mypy、全 pytest の契約を満たし、結果を証跡に残す。
- Baseline Check と Pre-Handoff Review は省略しない。詳細手順は該当 Step まで遅延して読む。
- Issue scope 外の変更を混ぜず、外部入力は Pydantic で検証する。

## 状況 → 正本

| 状況 | 読む正本セクション | 読む時点 |
|------|--------------------|----------|
| worktree 解決 | `.claude/skills/_shared/worktree-resolve.md` | Step 1 |
| 既存失敗の記録・停止基準・regression 比較 | `.claude/skills/issue-implement/references/baseline-check.md` | Step 2.5 |
| type 別 TDD | `.claude/skills/_shared/implement-by-type/{feat,bug,refactor}.md` | type 確定後 |
| S/M/L、恒久テスト、変更固有検証 | `docs/dev/testing-convention.md` § テストサイズ定義 / § テスト戦略の原則 | テスト着手前 |
| Python の style・命名・型・docstring・例外・logging | `docs/reference/python/*.md` | Python コード着手前 |
| docs 更新要否 | `docs/dev/documentation_update_criteria.md` § 更新が必要になりやすいケース / § docs-only で止めるべきケース | docs 影響判断時 |
| 完了条件の段階証跡 | `docs/dev/workflow_completion_criteria.md` § 各ステップの証跡責務 | Step 7.5 |
| workflow の遷移・type 差分 | `docs/dev/development_workflow.md` § フロー / § type 別の差分 | 遷移判断が必要な時だけ |
| 無関係な問題 | `.claude/skills/_shared/report-unrelated-issues.md` | 発見時 |
| handoff 前 review | `.claude/skills/issue-implement/references/pre-handoff-review.md` | commit 後の Step 8.5 |
| 実装完了報告 | `.claude/skills/issue-implement/templates/implement-report.md` | Step 9 直前 |

## 部分 Read の手順

1. `rg -n '^#{1,3} <見出し>' <file>` で対象セクションの開始行を特定する。
2. 次の同レベル見出しまでを Read する。
3. 現セッションに本文がない、前回 Read 後に変更された、部分 Read だけでは判断不能、のいずれかの場合だけ全文を読む。

この quickref と正本の同期責務は [documentation_update_criteria.md](./documentation_update_criteria.md) § Quickref と正本の同期で定義する。
