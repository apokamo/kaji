---
description: 設計書と関連ドキュメント（ADR、architecture.md等）の整合性を確認・更新する
---

# Issue Sync Docs

コードレビュー完了後、PR作成前に設計書と関連ドキュメントの整合性を確認・更新します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-review-code` または `/issue-verify-code` で Approve 後 | ✅ 必須 |
| PR作成前の最終確認として | ✅ 推奨 |
| コードレビュー未完了 | ❌ 待機 |

## 引数

```
$ARGUMENTS = <issue-number>
```

- `issue-number` (必須): Issue番号

## 前提条件

- `/issue-start` が実行済みであること
- コードレビューが完了していること（推奨）

## 実行手順

### Step 1: Worktree情報の取得と移動

1. **Issue本文からWorktree情報を取得**:
   ```bash
   gh issue view [issue-number] --json body -q '.body'
   ```

2. **Worktreeパスを抽出して移動**:
   ```bash
   cd [worktree-path]
   ```

3. **存在しない場合はエラー**:
   - `/issue-start [issue-number]` を先に実行するよう案内

### Step 2: 設計書の確認

1. **設計書を読み込み**:
   ```bash
   cat draft/design/issue-[number]-*.md
   ```

2. **設計書の内容を分析**:
   - 技術選定・アーキテクチャ決定の有無
   - システム構成の変更の有無
   - 新しい手順・ワークフローの有無

### Step 3: ドキュメント更新チェックリストの確認

以下のチェックリストを確認し、該当項目があれば対応するドキュメントを更新してください。

```markdown
## ドキュメント更新チェックリスト

### ADR (Architecture Decision Record)
- [ ] 新しい技術選定・ライブラリ採用の決定があるか？
- [ ] 既存のアーキテクチャパターンを変更する決定があるか？
- [ ] 重要なトレードオフを伴う設計判断があるか？

→ 該当あり: `docs/adr/` に新規 ADR を作成

### architecture.md
- [ ] システム構成・コンポーネント構造に変更があるか？
- [ ] 新しいモジュール・レイヤーを追加したか？
- [ ] 依存関係に変更があるか？

→ 該当あり: `docs/architecture.md` を更新

### guides/
- [ ] 開発者向けの新しい手順・ワークフローがあるか？
- [ ] 既存の運用手順に変更があるか？
- [ ] 新しいツール・コマンドの使用方法があるか？

→ 該当あり: `docs/guides/` に追加または更新
```

### Step 4: ドキュメント更新の実施

チェックリストで該当項目がある場合:

1. **対象ドキュメントの更新**:
   - ADR: `docs/adr/NNNN-title.md` を作成
   - architecture.md: 該当セクションを更新
   - guides/: 関連ガイドを追加・更新

2. **コミット**:
   ```bash
   git add docs/
   git commit -m "docs: update documentation for #[issue-number]"
   ```

### Step 5: スキップ条件の確認

以下の場合、ドキュメント更新は不要です:

- **バグ修正**: 既存の動作を修正するだけで設計変更なし
- **軽微なリファクタ**: 内部実装の改善で外部仕様・構造に影響なし
- **テスト追加**: テストコードのみの変更
- **ドキュメント修正**: typo修正等の軽微な変更

### Step 6: Issueにコメント

**更新を行った場合:**

```bash
gh issue comment [issue-number] --body "$(cat <<'EOF'
## ドキュメント同期完了

チェックリストに基づき、以下のドキュメントを更新しました。

### 更新内容

- `docs/xxx`: (更新内容の概要)

### 次のステップ

`/issue-pr [issue-number]` でPRを作成してください。
EOF
)"
```

**更新不要の場合:**

```bash
gh issue comment [issue-number] --body "$(cat <<'EOF'
## ドキュメント同期確認完了

チェックリストを確認した結果、関連ドキュメントの更新は不要でした。

**理由**: (バグ修正のみ / 内部実装の改善のみ / 等)

### 次のステップ

`/issue-pr [issue-number]` でPRを作成してください。
EOF
)"
```

### Step 7: 完了報告

以下の形式で報告してください:

```
## ドキュメント同期完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| 更新 | あり / なし |
| 対象 | (更新したドキュメント / -) |

### 次のステップ

`/issue-pr [issue-number]` でPRを作成してください。
```
