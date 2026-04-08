# スキル横断ルール

スキル間の責務境界と共通ルールを定義する。

## 責務境界

### PR 作成関連

| 責務 | 担当スキル | やらないスキル |
|------|-----------|---------------|
| 品質チェック実行 | `i-dev-final-check` / `i-doc-final-check` | `i-pr` |
| 設計書アーカイブ | `i-dev-final-check` | `issue-close`, `i-pr` |
| エビデンス集約 | `i-dev-final-check` / `i-doc-final-check` | `i-pr` |
| コミット整理・プッシュ・PR 作成 | `i-pr` | `i-dev-final-check` |
| PR マージ | `issue-close` | `i-pr` |
| ブランチ削除 | `issue-close` | `i-pr` |
| worktree 削除 | `issue-close` | `i-pr` |

### レビュー関連

| 責務 | 担当スキル |
|------|-----------|
| 新規指摘 | `issue-review-design`, `issue-review-code`, `i-doc-review` |
| 修正確認のみ（新規指摘不可） | `issue-verify-design`, `issue-verify-code`, `i-doc-verify` |

## ワークフロー位置の統一表記

### feature-development

```
create → start → design → review-design → implement → review-code → doc-check → i-dev-final-check → i-pr → close
```

### docs-maintenance

```
start → i-doc-update → i-doc-review → (i-doc-fix → i-doc-verify) → i-doc-final-check → i-pr → close
```

## 共通参照ドキュメント

| 共通ルール | パス | 用途 |
|-----------|------|------|
| worktree パス解決 | `_shared/worktree-resolve.md` | Issue 本文から worktree パスを取得 |
| 無関係な問題の報告 | `_shared/report-unrelated-issues.md` | 作業中に発見した無関係な問題の報告手順 |
| 設計書の昇格 | `_shared/promote-design.md` | draft 設計書から恒久ドキュメントへの昇格手順 |
