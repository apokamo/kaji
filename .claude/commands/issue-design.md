---
description: Issue要件に基づき、draft/design/に設計書を作成する。worktree内での作業が前提。
---

# Issue Design

指定されたIssueに基づき、設計書（Markdown）を作成します。
設計書は `draft/design/` に作成され、PR作成時にPR本文へ転記されます。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| Issue着手後、実装前 | ✅ 必須 |
| worktreeが存在しない | ❌ 先に `/issue-start` を実行 |

## 引数

```
$ARGUMENTS = <issue-number>
```

- `issue-number` (必須): Issue番号

## 前提条件

- `/issue-start` が実行済みであること
- Issue本文にWorktree情報が記載されていること

## 設計書ルール

| ルール | 説明 |
|--------|------|
| **What & Constraint** | 入力/出力と制約のみ |
| **Minimal How** | 実装詳細は方針のみ。疑似コードはOK |
| **API仕様** | 公式リンク参照（コピペ禁止） |
| **Test Strategy** | ID羅列ではなく検証観点を言語化 |

## 実行手順

### Step 1: Worktree情報の取得と移動

1. **Issue本文からWorktree情報を取得**:
   ```bash
   gh issue view [issue-number] --json body -q '.body'
   ```

2. **Worktreeパスを抽出**:
   - `> **Worktree**: \`../[prefix]-[number]\`` の形式で記載されている
   - 正規表現等でパスを抽出

3. **Worktreeへ移動**:
   ```bash
   cd [worktree-path]
   ```

4. **存在しない場合はエラー**:
   - `/issue-start [issue-number]` を先に実行するよう案内して終了

### Step 2: 設計書の作成

1. **ディレクトリ作成**:
   ```bash
   mkdir -p draft/design
   ```

2. **ファイル名決定**:
   - `draft/design/issue-[number]-[short-name].md`
   - 例: `draft/design/issue-6-workflow.md`

3. **設計書テンプレート**:

```markdown
# [設計] タイトル

Issue: #[issue-number]

## 概要

(何を実現するか、1-2文で)

## 背景・目的

(なぜこの変更が必要か)

## インターフェース

### 入力

(引数、パラメータ、設定など)

### 出力

(戻り値、副作用、生成物など)

### 使用例

\`\`\`python
# ユーザーコード例
\`\`\`

## 制約・前提条件

- (技術的制約)
- (ビジネス制約)
- (依存関係)

## 方針

(実装の大まかな方針。疑似コードOK)

## 検証観点

- (正常系: どのような動作を確認するか)
- (異常系: どのようなエラーケースを確認するか)
- (境界値: どのような境界条件を確認するか)

## 参考

- [公式ドキュメント](URL)
```

### Step 3: コミット

```bash
git add draft/design/
git commit -m "docs: add design for #[issue-number]"
```

### Step 4: Issueにコメント

設計完了をIssueにコメントします。

```bash
gh issue comment [issue-number] --body "$(cat <<'EOF'
## 設計書作成完了

設計書を作成しました。

### 成果物

- **ファイル**: `draft/design/issue-[number]-xxx.md`

### 設計の要点

1. **What**: (何を実現するか)
2. **Why**: (なぜこの設計か)
3. **Constraints**: (主な制約)

### 検証観点

- (主要な検証ポイント)

### 次のステップ

`/issue-review-design [issue-number]` でレビューをお願いします。
EOF
)"
```

### Step 5: 完了報告

以下の形式で報告してください:

```
## 設計書作成完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| 設計書 | draft/design/issue-[number]-xxx.md |
| コミット | [commit-hash] |

### 次のステップ

`/issue-review-design [issue-number]` でレビューを実施してください。
```
