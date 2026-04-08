# ワークフロー概要

Issue の種類に応じて適切なワークフローを選択するエントリポイント。

## ワークフロー選択

```
Issue の種類を確認
  ├── コード変更を伴う → feature-development ワークフロー
  └── ドキュメントのみ → docs-maintenance ワークフロー
```

## feature-development ワークフロー

コード変更を伴う Issue（機能追加・バグ修正・リファクタ・スキル改善等）のワークフロー。

```
/issue-create → /issue-start → /issue-design → /issue-review-design
→ /issue-implement → /issue-review-code → /issue-doc-check
→ /i-dev-final-check → /i-pr → /issue-close
```

詳細: [workflow_feature_development.md](workflow_feature_development.md)

## docs-maintenance ワークフロー

ドキュメント修正のみの Issue のワークフロー。コード・設定・テストは変更しない。

```
/issue-start → /i-doc-update → /i-doc-review → (/i-doc-fix → /i-doc-verify)
→ /i-doc-final-check → /i-pr → /issue-close
```

詳細: [workflow_docs_maintenance.md](workflow_docs_maintenance.md)

## 判断に迷うケース

| ケース | 選択 | 理由 |
|--------|------|------|
| スキルファイル（.claude/skills/）の変更 | feature-development | スキルはプロセス定義であり開発ワークフロー変更に該当 |
| CLAUDE.md の変更のみ | docs-maintenance | 設定・規約の変更であり docs-only |
| テスト追加のみ（実装変更なし） | feature-development | テストコードの変更 |
| CI/CD 設定の変更 | feature-development | インフラ変更 |
