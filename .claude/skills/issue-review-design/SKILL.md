---
description: 設計ドキュメントに対し、汎用的なソフトウェア設計原則に基づいてレビューを行う。
name: issue-review-design
---

# Issue Review Design

> **重要**: このスキルは実装/設計を行ったセッションとは **別のセッション** で実行することを推奨します。
> 同一セッションで実行すると、実装時のバイアスがレビュー判断に影響する可能性があります。

実装フェーズに入る前に、設計ドキュメントの品質を検証します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| 設計完了後、実装開始前 | ✅ 必須 |
| 仕様変更時の再レビュー | ⚠️ 推奨 |

**ワークフロー内の位置**: design → **review-design** → (fix → verify) → implement

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

1. **開発ワークフロー**: `docs/dev/development_workflow.md`
2. **テスト規約**: `docs/dev/testing-convention.md`

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。以降のステップではこのパスを使用する。

### Step 1.5: 設計書の読み込みと一次情報の確認（Gate Check）

1. **設計書の読み込み**:
   ```bash
   cat [worktree-absolute-path]/draft/design/issue-[number]-*.md
   ```

2. **一次情報の記載を確認**:

設計書に以下が明記されているか確認：

- [ ] **参照した一次情報の一覧**（公式ドキュメント、RFC、API仕様書、ライブラリのソースコード等）
- [ ] **各一次情報へのURL/パス**（検証可能な形式）

#### 一次情報がない場合 → 早期リターン

設計書に一次情報の記載がない、または不十分な場合は、**レビュー本体に入らず**以下のコメントを投稿して終了：

```bash
gh issue comment [issue-number] --body-file - <<'EOF'
# 設計レビュー：一次情報の記載が必要

## 指摘事項

設計書に**一次情報（Primary Sources）の記載がありません**。

設計レビューを行うには、以下を設計書に追記してください：

### 必要な情報

1. **参照した一次情報の一覧**
   - 公式ドキュメント、RFC、API仕様書、ライブラリのソースコード等
   - URLまたはファイルパスを明記

2. **一次情報から得た根拠**
   - 設計判断の裏付けとなる情報を引用または要約

### 例

\`\`\`markdown
## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|------------------------|
| Python公式ドキュメント | https://docs.python.org/... | 「〜を使用することで...」（該当箇所の引用） |
\`\`\`

## 判定

❌ **Changes Requested** - 一次情報を追記後、再度レビューを依頼してください。

### 次のステップ

`/issue-fix-design [issue-number]` で一次情報を追記
EOF
```

**この時点でレビュー終了。Step 2以降は実行しない。**

---

### Step 2: 設計レビュー（一次情報を参照）

一次情報が記載されている場合のみ、このステップに進みます。

**重要**: レビュー時は設計書の記述だけでなく、**一次情報を実際に参照**して整合性を確認してください。

#### レビュー基準

以下の汎用的な原則に基づいてレビューしてください。

1. **抽象化と責務の分離 (Abstraction & Scope)**:
   - **What & Why**: 「何を作るか」と「なぜ作るか」が明確か？
   - **No Implementation Details**: 特定の言語やライブラリの内部実装（How）に過度に踏み込んでいないか？（疑似コードはOK）
   - **Constraints**: システムの制約条件（性能、セキュリティ、依存関係）が明記されているか？

2. **インターフェース設計 (Interface Design)**:
   - **Usage Sample**: 利用者が実際に使用する際のコード例が含まれているか？
   - **Idiomatic**: そのインターフェースは、対象言語の慣習（Idioms）に適合しているか？
   - **Naming**: 直感的で意図が伝わる命名がなされているか？

3. **信頼性とエッジケース (Reliability)**:
   - **Source of Truth**: 一次情報の内容と設計が整合しているか？
   - **Error Handling**: 正常系だけでなく、異常系（エラー、境界値）の挙動が定義されているか？
   - **一次情報との乖離**: 一次情報に記載されているが設計で考慮されていない点はないか？

4. **検証可能性 (Testability)**:
   - テストケースの羅列ではなく、**「検証すべき観点」**が言語化されているか？
   - **変更タイプに応じたテスト戦略チェック（必須）**:
     - [ ] 変更タイプ（実行時コード変更 / docs-only / metadata-only / packaging-only）が明示されているか
     - [ ] 実行時コード変更なら Small / Medium / Large の検証観点が定義されているか
     - [ ] 恒久テストを追加しない場合、その理由が `docs/dev/testing-convention.md` の 4 条件に沿っているか
     - [ ] `pip install -e .` など副作用のある検証を行う場合、隔離方針が明記されているか

5. **影響ドキュメント**:
   - 「影響ドキュメント」セクションが存在し、影響範囲が適切に評価されているか？

### Step 3: レビュー結果のコメント

```bash
gh issue comment [issue-number] --body-file - <<'EOF'
# 設計レビュー結果

## 参照した一次情報

| 情報源 | 確認結果 |
|--------|----------|
| [URL] | ✅ 設計と整合 / ⚠️ 差異あり |

## 概要

(設計の明確さと、実装着手の可否判定)

## 指摘事項 (Must Fix)

- [ ] **項目**: 指摘内容
  - (要件の欠落、論理的な矛盾、不明確なインターフェースなど)

## 改善提案 (Should Fix)

- **項目**: 提案内容

## 判定

[ ] Approve (実装着手可)
[ ] Changes Requested (設計修正が必要)
EOF
```

### Step 4: 完了報告

```
## 設計レビュー完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| 判定 | Approve / Changes Requested |

### 次のステップ

- Approve: `/issue-implement [issue-number]` で実装を開始
- Changes Requested: `/issue-fix-design [issue-number]` で修正
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

---VERDICT---
status: PASS
reason: |
  設計は実装着手可能
evidence: |
  全レビュー基準を満たしている
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | Approve |
| RETRY | Changes Requested |
| ABORT | 重大な問題 |
