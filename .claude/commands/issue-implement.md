---
description: 設計書（draft/design/）に基づき、TDD（テスト駆動開発）アプローチを用いて機能を実装する。
---

# Issue Implement

承認された設計書を元に、テストコードの作成から実装を開始します。
**Test-Driven Development (TDD)** の原則に従い、「テスト作成 (Red) → 実装 (Green) → リファクタリング」のサイクルを回します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| 設計レビュー完了・承認後 | ✅ 必須 |
| 設計レビュー未完了 | ❌ 待機 |

## 引数

```
$ARGUMENTS = <issue-number>
```

- `issue-number` (必須): Issue番号

## 前提条件

- `/issue-start` が実行済みであること
- `/issue-design` で設計書が作成済みであること
- 設計レビューが完了・承認されていること

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

### Step 2: 設計書の読み込み

1. **設計書を読み込み**:
   ```bash
   cat draft/design/issue-[number]-*.md
   ```

2. **特に注目するセクション**:
   - 「インターフェース」: 実装すべきAPI
   - 「検証観点」: テストケースの元になる

### Step 3: テスト実装 (Red Phase)

1. **テストファイルの特定/作成**:
   - `tests/` 配下の適切な場所にテストファイルを作成または特定

2. **テストコード記述**:
   - 設計書の「検証観点」をカバーするテストケースを書く
   - この時点では実装がないため、テスト（またはインポート）は失敗する

3. **失敗の確認**:
   ```bash
   source .venv/bin/activate && pytest
   ```
   - 期待通りに失敗することを確認

### Step 4: 機能実装 (Green Phase)

1. **実装ファイルの編集**:
   - 設計書の「インターフェース定義」に従い、`src/` 配下のコードを実装
   - 必要なクラス、関数、定数を定義

2. **テスト通過確認**:
   ```bash
   source .venv/bin/activate && pytest
   ```
   - 全てパスすることを目指す

### Step 5: 品質確認 (Refactor & Lint)

1. **品質チェック実行**:
   ```bash
   source .venv/bin/activate
   ruff check src/ tests/
   ruff format src/ tests/
   mypy src/
   pytest
   ```

2. **リファクタリング**:
   - コードの可読性を高める修正を行う
   - テストが引き続きパスすることを確認

### Step 6: コミット

```bash
git add .
git commit -m "feat: implement [feature] for #[issue-number]"
```

### Step 7: Issueにコメント

実装完了をIssueにコメントします。

```bash
gh issue comment [issue-number] --body "$(cat <<'EOF'
## 実装完了報告 (TDD)

設計に基づき、TDDにて実装を行いました。

### 実施内容

- **テスト**: `tests/test_xxx.py` に XX 件のケースを追加 (Red → Green)
- **実装**: `src/xxx.py` に機能を実装

### 検証結果

- [x] テスト通過 (pytest)
- [x] Lint通過 (ruff check)
- [x] Format通過 (ruff format)
- [x] 型チェック通過 (mypy)

### 変更ファイル

- `src/xxx.py`: (変更内容)
- `tests/test_xxx.py`: (変更内容)

### 次のステップ

`/issue-review-code [issue-number]` によるコードレビューをお願いします。
EOF
)"
```

### Step 8: 完了報告

以下の形式で報告してください:

```
## 実装完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| テスト | XX 件追加 |
| 品質チェック | すべてパス |

### 次のステップ

`/issue-review-code [issue-number]` でコードレビューを実施してください。
```
