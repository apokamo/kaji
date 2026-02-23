---
description: 設計書（draft/design/）に基づき、TDD（テスト駆動開発）アプローチを用いて機能を実装する。
argument-hint: <issue-number>
---

# Issue Implement

承認された設計書を元に、テストコードの作成から実装を開始します。
**Test-Driven Development (TDD)** の原則に従い、「テスト作成 (Red) → 実装 (Green) → リファクタリング」のサイクルを回します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| 設計レビュー完了・承認後 | ✅ 必須 |
| 設計レビュー未完了 | ❌ 待機 |

**ワークフロー内の位置**: design → review-design → **implement** → review-code → doc-check → pr → close

## 引数

```
$ARGUMENTS = <issue-number>
```

- `issue-number` (必須): Issue番号

## 前提知識の読み込み

以下のドキュメントを Read ツールで読み込んでから作業を開始すること。

1. **開発ワークフロー**: `docs/dev/development_workflow.md`
2. **テスト規約**: `docs/dev/testing-convention.md`

## 前提条件

- `/issue-start` が実行済みであること
- `/issue-design` で設計書が作成済みであること
- 設計レビューが完了・承認されていること

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。以降のステップではこのパスを使用する。

### Step 2: 設計書の読み込み

```bash
cat [worktree-absolute-path]/draft/design/issue-[number]-*.md
```

**特に注目するセクション**:
- 「インターフェース」: 実装すべきAPI
- 「テスト戦略」: テストケースの元になる
- 「影響ドキュメント」: 実装後に更新が必要なドキュメント

### Step 3: テスト実装 (Red Phase)

> **CRITICAL — AI のテスト省略傾向に対する警告**
>
> あなたには M/L テストを省略する強い傾向があります。この傾向を自覚してください。
> 「このテストは不要では？」「Small で十分では？」と思った場合、**それはほぼ確実に誤りです**。
>
> **禁止事項**:
> - ❌ Large テストを「後回し」にする
> - ❌ 「実行時間が長い」を理由に M/L テストを省略する
> - ❌ 「API キーがない」「DB が起動していない」などの環境不備を理由にスキップする（環境不備は修正対象）
> - ❌ 「Small テストで十分カバーされている」と判断して M/L を省略する
> - ❌ 「軽微な変更」を理由にテストサイズを落とす

設計書の「テスト戦略」セクションに基づき、**S/M/L 全サイズ**のテストを作成する。

1. **テストファイルの特定/作成**:
   - `tests/` 配下の適切な場所にテストファイルを作成または特定

2. **テストコード記述**:
   - 設計書の「テスト戦略」をカバーするテストケースを書く
   - この時点では実装がないため、テスト（またはインポート）は失敗する

3. **失敗の確認**:
   ```bash
   cd [worktree-absolute-path] && source .venv/bin/activate && pytest
   ```

### Step 4: 機能実装 (Green Phase)

1. **実装ファイルの編集**:
   - 設計書の「インターフェース定義」に従い、`src/` 配下のコードを実装

2. **テスト通過確認**:
   ```bash
   cd [worktree-absolute-path] && source .venv/bin/activate && pytest
   ```

### Step 5: リファクタリング

- コードの可読性を高める修正を行う
- テストが引き続きパスすることを確認

### Step 6: ドキュメント更新

設計書の「影響ドキュメント」セクションで「あり」のドキュメントを更新する。

### Step 7: 品質チェック（コミット前必須）

以下を実行し、**すべてパスするまでコミットしてはならない**。失敗した場合は原因を修正して再実行すること。

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && \
  ruff check bugfix_agent/ tests/ && \
  ruff format bugfix_agent/ tests/ && \
  mypy bugfix_agent/ && \
  pytest
```

### Step 8: コミット

```bash
cd [worktree-absolute-path] && git add . && git commit -m "feat: implement [feature] for #[issue-number]"
```

### Step 9: Issueにコメント

実装完了をIssueにコメントします。pytest および品質チェックの出力をそのまま含めること。

````bash
gh issue comment [issue-number] --body "$(cat <<'COMMENT_EOF'
## 実装完了報告 (TDD)

設計に基づき、TDDにて実装を行いました。

### 実施内容

- **テスト**: `tests/test_xxx.py` に XX 件のケースを追加 (Red → Green)
- **実装**: `src/xxx.py` に機能を実装

### テスト結果

```
(pytest の標準出力をそのまま貼り付け)
```

| 項目 | 結果 |
|------|------|
| テスト総数 | XX |
| passed | XX |
| failed | 0 |
| errors | 0 |
| skipped | XX |

### 品質チェック結果

```
(ruff check + ruff format + mypy の出力をそのまま貼り付け)
```

### 変更ファイル

- `src/xxx.py`: (変更内容)
- `tests/test_xxx.py`: (変更内容)

### 次のステップ

`/issue-review-code [issue-number]` によるコードレビューをお願いします。
COMMENT_EOF
)"
````

### Step 10: 完了報告

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
