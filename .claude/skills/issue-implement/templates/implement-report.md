# Implementation Report Template

この template は `/issue-implement` Step 9 の直前にだけ読む。

## Issue コメント

`<STATUS>` はこの skill が返す正式 status と一致させる。verdict marker は常に付ける。

````bash
kaji issue comment [issue_id] --commit \
  --verdict-step implement --verdict-status <STATUS> \
  --body "$(cat <<'COMMENT_EOF'
## 実装完了報告 (TDD)

設計に基づき、TDD で実装しました。

### 実施内容

- **テスト / 検証**: (追加・更新したテストまたは変更固有検証)
- **実装**: (実装内容)

### テスト結果

```
(Step 7 で保持した pytest の出力をそのまま貼り付け。`make check` 経路ではその出力の pytest 部分)
```

| 項目 | 結果 |
|------|------|
| テスト総数 | XX |
| passed | XX |
| failed | XX (baseline: YY, regression: 0) |
| errors | XX (baseline: YY, regression: 0) |
| skipped | XX |
| Small テスト | XX passed |
| Medium テスト | XX passed |
| Large テスト | XX passed |

### 品質チェック結果

```
(Step 7 で保持した ruff check / ruff format --check / mypy の出力をそのまま貼り付け。`make check` 経路ではその出力の該当部分)
```

### 変更ファイル

- `path/to/file`: (変更内容)

### Pre-Handoff Review 結果

- Pre-Handoff Review コメント数 (`PHR_COUNT`): XX
- 最終 verdict: Yes / With fixes / No
- 最新コメント: (リンクまたは投稿日時)

### 完了条件の段階確認

- [ ] (条件1): ✅ 実装で対応済み / ❌ 未充足
- [ ] (条件2): ✅ テストで確認 / ❌ 未充足
- (未確認条件): final-check で確認予定

### 次のステップ

`/issue-review-code [issue_id]` でコードレビューを実施してください。

---VERDICT---
status: <STATUS>
reason: "(判定理由)"
evidence: |
  (pytest、ruff/format/mypy、Pre-Handoff Review の具体的証跡)
suggestion: "(BACK/ABORT 時の次アクション。不要なら空文字)"
---END_VERDICT---
COMMENT_EOF
)"
````

Baseline Check と Pre-Handoff Review の専用コメントには verdict marker を付けない。Pre-Handoff Review 本文は再投稿せず、最新コメントを参照・要約する。

## セッション完了報告

```markdown
## 実装完了

| 項目 | 値 |
|------|-----|
| Issue | [issue_ref] |
| テスト | XX 件追加・更新 |
| 品質チェック | すべてパス |

### 次のステップ

`/issue-review-code [issue_id]` でコードレビューを実施してください。
```
