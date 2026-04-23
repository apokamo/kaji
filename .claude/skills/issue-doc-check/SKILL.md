---
description: PR前の品質ゲートとして、コード変更に伴うドキュメント影響を網羅チェックする
name: issue-doc-check
---

# Issue Doc Check

コードレビュー Approve 後、PR 作成前に実行する品質ゲート。
コード変更に伴うドキュメントの影響を網羅的にチェックし、必要に応じて更新します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-review-code` または `/issue-verify-code` で Approve 後 | ✅ 必須 |
| PR作成前の最終確認として | ✅ 推奨 |

**ワークフロー内の位置**: implement → review-code → **doc-check** → pr → close

## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue-number>
```

### 解決ルール

コンテキスト変数 `issue_number` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_number` として使用。

## 前提知識の読み込み

以下のドキュメントを Read ツールで読み込んでから作業を開始すること。

1. **開発ワークフロー**: `docs/dev/development_workflow.md`

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。

### Step 2: 影響ドキュメントの確認

1. **設計書の「影響ドキュメント」セクションを確認**:
   ```bash
   cat [worktree-absolute-path]/draft/design/issue-[number]-*.md
   ```

2. **変更ファイルの確認**:
   ```bash
   cd [worktree-absolute-path] && git diff main...HEAD --name-only
   ```

### Step 3: チェックリストの実行

| 確認項目 | 判定基準 |
|----------|---------|
| ADR が必要か | 新しい技術選定・アーキテクチャ変更の有無 |
| ARCHITECTURE.md の更新 | システム構成・コンポーネント構造の変更 |
| docs/dev/ の更新 | ワークフロー・開発手順・テスト規約の変更 |
| docs/cli-guides/ の更新 | CLI仕様・コマンドの変更 |
| CLAUDE.md の更新 | 新しいコマンド・規約・禁止事項の追加 |

### Step 4: ドキュメント更新の実施

更新が必要なドキュメントがあれば修正・コミット：

```bash
cd [worktree-absolute-path] && git add docs/ CLAUDE.md && git commit -m "docs: update documentation for #[issue-number]"
```

### Step 5: スキップ条件

以下の場合、ドキュメント更新は不要：

- **バグ修正**: 既存の動作を修正するだけで設計変更なし
- **軽微なリファクタ**: 内部実装の改善で外部仕様・構造に影響なし
- **テスト追加**: テストコードのみの変更

### Step 6: Issue にコメント

**更新を行った場合:**

```bash
gh issue comment [issue-number] --body "$(cat <<'EOF'
## ドキュメントチェック完了

チェックリストに基づき、以下のドキュメントを更新しました。

### 更新内容

- `docs/xxx`: (更新内容の概要)

### 次のステップ

`/i-dev-final-check [issue-number]` で最終チェックを実施してください。
EOF
)"
```

**更新不要の場合:**

```bash
gh issue comment [issue-number] --body "$(cat <<'EOF'
## ドキュメントチェック完了

チェックリストを確認した結果、関連ドキュメントの更新は不要でした。

**理由**: (バグ修正のみ / 内部実装の改善のみ / 等)

### 次のステップ

`/i-dev-final-check [issue-number]` で最終チェックを実施してください。
EOF
)"
```

### Step 7: 完了報告

```
## ドキュメントチェック完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| 更新 | あり / なし |
| 対象 | (更新したドキュメント / -) |

### 次のステップ

`/i-dev-final-check [issue-number]` で最終チェックを実施してください。
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

---VERDICT---
status: PASS
reason: |
  ドキュメントチェック完了
evidence: |
  影響ドキュメントの確認・更新済み
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | チェック完了 |
