---
description: 設計書（draft/design/）に基づき、TDD（テスト駆動開発）アプローチを用いて機能を実装する。
name: issue-implement
---

# Issue Implement

承認された設計書を元に、テストコードの作成から実装を開始します。
**Test-Driven Development (TDD)** の原則に従い、「テスト作成 (Red) → 実装 (Green) → リファクタリング」のサイクルを回します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| 設計レビュー完了・承認後 | ✅ 必須 |
| 設計レビュー未完了 | ❌ 待機 |

**ワークフロー内の位置**: design → review-design → **implement** → review-code → doc-check → i-dev-final-check → i-pr → close

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

### Step 2.5: Baseline Check

実装開始前にテスト環境の状態を確認し、変更前から存在する失敗（baseline failure）を記録する。

1. **pytest を実行する**:
   ```bash
   cd [worktree-absolute-path] && source .venv/bin/activate && pytest
   ```

2. **全パスの場合**: baseline は clean。コメント不要。Step 3 へ進む。

3. **FAILED / ERROR がある場合**:
   a. 各失敗テストの `(nodeid, kind, error_type)` を記録する
   b. Issue コメントに以下のフォーマットで投稿する（commit hash を含める）:

   ````bash
   gh issue comment [issue-number] --body "$(cat <<'BASELINE_EOF'
   ## Baseline Check 結果

   ### 実行環境

   - **Commit**: [commit-hash]
   - **コマンド**: `pytest`

   ### Baseline Failure 一覧

   | nodeid | kind | error_type | 概要 |
   |--------|------|------------|------|
   | tests/test_foo.py::test_bar | FAILED | AssertionError | expected 1, got 2 |
   | tests/test_baz.py::test_qux | ERROR | ImportError | No module named 'xxx' |

   ### Regression 判定キー

   上記テーブルの `(nodeid, kind, error_type)` の3タプルを比較キーとする。
   以降の pytest 実行で:
   - 比較キーが一致する失敗 → baseline failure（既知）として除外
   - 比較キーが一致しない新規 FAILED/ERROR → regression

   ### 判定

   - **継続**: 上記は変更前から存在する失敗であり、本 Issue の対象外
   - **停止**: (該当する場合のみ記載)
   BASELINE_EOF
   )"
   ````

   c. **停止基準**に該当するか判断する:
      - baseline failure が本 Issue の実装対象と同一モジュール/機能に影響する場合
      - 失敗数が多く、regression の切り分けが困難な場合（目安: 10 件超）
   d. 継続する場合: 以降の regression 判定は baseline failure を除外して行う

> **Baseline コメントの選択規則**: Issue に `## Baseline Check 結果` コメントが複数存在する場合（再実行時など）、**最新のコメントを正とする**。各コメントに commit hash を含めることで、どの時点のスナップショットかを識別できる。

### Step 3: テスト実装 (Red Phase)

> **CRITICAL — 変更タイプに応じて妥当な検証を選ぶこと**
>
> 実行時コード変更では、都合よく S/M/L を減らさないこと。
> 一方で docs-only / metadata-only / packaging-only 変更に対し、
> 価値の低い恒久テストを機械的に追加してはならない。
>
> **禁止事項**:
> - ❌ 実行時コード変更なのに「実行時間が長い」を理由に M/L を省略する
> - ❌ 実行時コード変更なのに「Small で十分」と決め打ちする
> - ❌ docs-only / metadata-only / packaging-only 変更に無理やり S/M/L テストを新設する
> - ❌ `uv pip install -e .` など副作用のある検証を shared 環境へ常設する

設計書の「テスト戦略」セクションに基づき、変更タイプに応じた検証を実施する。

1. **テストファイルの特定/作成**:
   - 実行時コード変更: `tests/` 配下の適切な場所にテストファイルを作成または特定
   - docs-only / metadata-only / packaging-only: 変更固有検証のみで十分なら、新規テストは作成しない

2. **テストコード記述**:
   - 実行時コード変更: 設計書の「テスト戦略」をカバーするテストケースを書く
   - docs-only / metadata-only / packaging-only: 設計書に記載した変更固有検証を実施する

3. **失敗 / 回帰の確認**:
   - 実行時コード変更: Red Phase として失敗を確認する
   - docs-only / metadata-only / packaging-only: 新規テストを追加しない場合、既存テストに回帰がないかを確認するステップとして扱う
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

   pytest の合否判定基準:
   - **Baseline Check コメントがない場合**: 全テスト PASSED を期待（従来どおり）
   - **Baseline Check コメントがある場合**:
     1. FAILED/ERROR のテストを baseline failure 一覧と照合する
     2. 比較キー `(nodeid, kind, error_type)` が baseline と一致 → 既知（除外）
     3. 比較キーが不一致の新規 FAILED/ERROR → regression（修正が必要）
     4. baseline にあったが消えた（PASSED に変わった）→ 問題なし

### Step 5: リファクタリング

- コードの可読性を高める修正を行う
- テストが引き続きパスすることを確認

### Step 6: ドキュメント更新

設計書の「影響ドキュメント」セクションで「あり」のドキュメントを更新する。

### Step 7: 品質チェック（コミット前必須）

以下の 2 段階で実行すること。**すべての基準をクリアするまでコミットしてはならない**。

#### 7a. Lint / Format / 型チェック（exit 0 必須）

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && ruff check kaji_harness/ tests/ && ruff format kaji_harness/ tests/ && mypy kaji_harness/
```

ruff / mypy は全パス必須。baseline failure の概念を適用しない。

#### 7b. テスト実行

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && pytest
```

**`pytest` は `&&` チェーンに含めず、必ず個別に実行する。** baseline failure が残っていると exit 非 0 になるが、以下の基準で合否を判定する:

- **Baseline Check コメントがない場合**: 全テスト PASSED 必須（exit 0 でなければ NG）
- **Baseline Check コメントがある場合**: Step 4 と同じ regression 判定基準を適用する
  - FAILED/ERROR を baseline 一覧と照合し、比較キー `(nodeid, kind, error_type)` が全一致 → OK（コミット可）
  - 比較キーが不一致の新規 FAILED/ERROR が 1 件でもある → NG（修正が必要）

失敗した場合は原因を修正して再実行すること。

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

- **テスト / 検証**: `tests/test_xxx.py` に XX 件のケースを追加、または変更固有検証を実施
- **実装**: `src/xxx.py` に機能を実装

### テスト結果

```
(pytest の標準出力をそのまま貼り付け)
```

| 項目 | 結果 |
|------|------|
| テスト総数 | XX |
| passed | XX |
| failed | XX (うち baseline: YY, regression: 0) |
| errors | XX (うち baseline: YY, regression: 0) |
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

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

---VERDICT---
status: PASS
reason: |
  実装・テスト・品質チェック全パス
evidence: |
  pytest 全テストパス、ruff/mypy エラーなし
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 実装・テスト・品質チェック全パス |
| RETRY | テスト失敗等 |
| BACK | 設計に問題 |
| ABORT | 重大な問題 |
