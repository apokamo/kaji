---
description: 実装完了後の成果物に対し、設計整合性とコード品質の観点から厳格なレビューを実施する
name: issue-review-code
---

# Issue Review Code

> **重要**: このスキルは実装/設計を行ったセッションとは **別のセッション** で実行することを推奨します。
> 同一セッションで実行すると、実装時のバイアスがレビュー判断に影響する可能性があります。

実装コードに対して、設計書を基に厳格なコードレビューを実施します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-implement` 完了後 | ✅ 必須 |
| 実装途中 | ⚠️ 任意（中間レビューとして） |

**ワークフロー内の位置**: implement → **review-code** → (fix → verify) → doc-check → pr → close

## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |

**条件付きで注入される変数:**

| 変数 | 型 | 条件 | 説明 |
|------|-----|------|------|
| `cycle_count` | int | サイクル内ステップのみ | 現在のイテレーション番号 |
| `max_iterations` | int | サイクル内ステップのみ | サイクルの上限回数 |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue-number>
```

### 解決ルール

コンテキスト変数 `issue_number` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_number` として使用。

## 前提知識の読み込み

以下のドキュメントを Read ツールで読み込んでから作業を開始すること。

1. **開発ワークフロー**: `docs/dev/workflow_feature_development.md`
2. **テスト規約**: `docs/dev/testing-convention.md`

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: コンテキストの取得

1. [_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、Worktree の絶対パスを取得。

2. **設計情報の取得**:
   ```bash
   cat [worktree-absolute-path]/draft/design/issue-[number]-*.md
   ```

3. **実装サマリーの取得**:
   ```bash
   gh issue view [issue-number] --comments
   ```
   直近の「実装完了報告」を確認。

4. **実装差分の取得**:
   ```bash
   cd [worktree-absolute-path] && git diff main...HEAD
   ```

### Step 1.5: 独立テスト実行（必須）

レビュワー自身が独立した環境でテストを実行し、結果を確認する。
実装者の報告だけに依存せず、テスト結果を独自に検証することが目的。

1. **Baseline Check コメントの確認**:
   Issue コメント（Step 1.3 で取得済み）から最新の `## Baseline Check 結果` を検索する。

2. **Lint / Format / 型チェック（exit 0 必須）**:
   ```bash
   cd [worktree-absolute-path] && source .venv/bin/activate && ruff check kaji_harness/ tests/ && ruff format --check kaji_harness/ tests/ && mypy kaji_harness/
   ```

3. **テスト実行（個別）**:
   ```bash
   cd [worktree-absolute-path] && source .venv/bin/activate && pytest
   ```
   **`pytest` は `&&` チェーンに含めず、必ず個別に実行する。** baseline failure が残っていると exit 非 0 になるため、チェーンに含めると後続の判定に到達できない。

4. **合否判定**:
   - **Baseline Check コメントがない場合**:
     - 全コマンドが exit 0 でなければ **Changes Requested**（従来どおり）
   - **Baseline Check コメントがある場合**:
     - ruff check / ruff format / mypy: exit 0 必須（変更なし）
     - pytest: FAILED/ERROR を baseline 一覧と照合する
       - 比較キー `(nodeid, kind, error_type)` が baseline と完全一致 → 除外
       - 不一致の新規 FAILED/ERROR → **Changes Requested**
       - baseline failure のみ残っている → テスト合否は OK とする

5. テスト総数、passed/failed/errors/skipped を記録しておく

### Step 2: コードレビューの実施

1. **設計との整合性**:
   - 設計書の要件を完全に満たしているか？
   - 勝手な仕様変更や、未実装の機能はないか？

2. **安全性と堅牢性**:
   - エラーハンドリングは適切か？（握りつぶし、汎用Exceptionの禁止）
   - 境界値（Boundary Value）やNull安全性の考慮はあるか？

3. **コード品質**:
   - 型ヒントは具体的か？ (`Any` の乱用禁止)
   - 命名は適切で説明的か？
   - CLAUDE.md のコーディング規約に準拠しているか？

4. **テスト**:
   - 追加された機能に対するテストは十分か？
   - 設計書の「テスト戦略」と実装テストが対応しているか？
   - **変更タイプに応じた検証チェック（必須）**:
     - [ ] 実行時コード変更なら、設計書で定義した Small / Medium / Large が実装・PASSED か
     - [ ] docs-only / metadata-only / packaging-only 変更なら、設計書で定義した変更固有検証が実施済みか
     - [ ] 恒久テストを追加しない理由が `docs/dev/testing-convention.md` と矛盾していないか
     - [ ] pytest 出力が Issue コメントに含まれているか
   - テスト / 検証未実施の場合: 設計レビューで承認済みでない限り **Changes Requested**
   - pytest 出力がない場合は **Changes Requested**

### Step 3: レビュー結果のコメント投稿

```bash
gh issue comment [issue-number] --body "$(cat <<'EOF'
# コードレビュー結果

## 概要

(一言で言うとどうだったか)

## 指摘事項 (Must Fix)

- [ ] **ファイル名:行数**: 具体的な指摘内容
- [ ] ...

## 改善提案 (Should Fix)

- **ファイル名**: より良い実装パターンの提案

## 良い点

- (特筆すべき良い実装があれば記載)

## 判定

[ ] Approve (修正なしでマージ可)
[ ] Changes Requested (要修正)
EOF
)"
```

### Step 4: 完了報告

```
## コードレビュー完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| 判定 | Approve / Changes Requested |
| Must Fix | N 件 |
| Should Fix | M 件 |

### 次のステップ

- Approve: `/issue-doc-check [issue-number]` でドキュメントチェック
- Changes Requested: `/issue-fix-code [issue-number]` で修正
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

---VERDICT---
status: PASS
reason: |
  コード品質基準を満たしている
evidence: |
  設計整合性・テストカバレッジ・品質チェックすべて合格
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | Approve |
| RETRY | Changes Requested |
| BACK | 設計に問題 |
| ABORT | 重大な問題 |
