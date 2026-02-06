# Common Prompt Elements

このファイルは全ステートで共有される共通要素を定義します。
各プロンプトファイルの先頭に自動的に挿入されます。

---

## VERDICT 出力形式

REVIEWステート（INIT含む）は、必ず以下の形式でVERDICTを出力してください。

```markdown
## VERDICT
- Result: PASS | RETRY | BACK_DESIGN | ABORT
- Reason: <判定理由>
- Evidence: <判定根拠>
- Suggestion: <次のアクション提案>（ABORT時は必須）
```

### VERDICT キーワード定義

| VERDICT | 意味 | 使用可能ステート |
|---------|------|-----------------|
| `PASS` | 成功・次ステートへ進行 | 全REVIEWステート |
| `RETRY` | 同ステート再実行（軽微な問題） | INVESTIGATE_REVIEW, DETAIL_DESIGN_REVIEW, IMPLEMENT_REVIEW |
| `BACK_DESIGN` | 設計見直しが必要 | IMPLEMENT_REVIEW のみ |
| `ABORT` | 続行不能・即座に終了 | 全ステート（緊急時） |

### 注意事項

1. **必ず `## VERDICT` セクションを含めること**
2. **Result 行は1行で、キーワードのみを記載**
3. **ABORT時は Suggestion に具体的な対処法を記載**
4. **作業ステート（INVESTIGATE, DETAIL_DESIGN, IMPLEMENT, PR_CREATE）はVERDICTを出力しない**
5. **【重要】最終応答は必ず VERDICT ブロックで終了すること**
   - GitHub への投稿内容と同一の VERDICT を stdout にも出力する
   - VERDICT ブロックの後に追加の文章・サマリーを付けない
   - 「タスク完了報告」のみの応答は禁止

---

## Issue 更新ルール

Issue本文への追記ルール：

1. **初回（Loop=1）**: セクションを Issue 本文の末尾に追記
2. **2回目以降（Loop>=2）**: 既存の該当セクションを削除し、新しい内容を末尾に追記

例:

- 初回: Output を Issue 本文の末尾に追記
- 2回目以降: 既存セクションを削除し、新しい内容を末尾に追記

コマンド例: `gh issue edit <issue_number> --body "<updated_body>"`

---

## 証跡保存ルール

- 証跡ファイルは artifacts_dir に保存
- ファイル名は内容がわかる命名にする
- 本文には証跡ファイルへの参照を含める

例:
```markdown
### INVESTIGATE / 再現手順
1. pytest実行 (証跡: pytest_output.txt)
2. エラーログ確認 (証跡: error_log.txt)
```
