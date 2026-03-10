---
description: Issue要件に基づき、draft/design/に設計書を作成する。worktree内での作業が前提。
name: issue-design
---

# Issue Design

指定されたIssueに基づき、設計書（Markdown）を作成します。
設計書は `draft/design/` に作成され、Issue Close 時に Issue 本文へアーカイブされます。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| Issue着手後、実装前 | ✅ 必須 |
| worktreeが存在しない | ❌ 先に `/issue-start` を実行 |

**ワークフロー内の位置**: create → start → **design** → review-design → implement → review-code → doc-check → pr → close

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
- Issue本文にWorktree情報が記載されていること

## 設計書ルール

| ルール | 説明 |
|--------|------|
| **What & Constraint** | 入力/出力と制約のみ |
| **Minimal How** | 実装詳細は方針のみ。疑似コードはOK |
| **Primary Sources** | 一次情報（公式ドキュメント等）のURL/パスを必ず記載 |
| **API仕様** | 公式リンク参照（コピペ禁止） |
| **Test Strategy** | ID羅列ではなく検証観点を言語化 |

### 一次情報のアクセス可能性ルール

> **重要**: レビュワー（agent）がアクセスできない一次情報は使用できません。

| 情報の種類 | 対応方法 |
|------------|----------|
| 公開URL | そのまま記載（推奨） |
| ログイン必須/有償 | ローカルにダウンロードしてリポジトリに配置、または該当箇所を引用 |
| 社内限定/NDA | 使用不可。公開版ドキュメントを探すか、該当箇所のスクリーンショット・引用で代替 |

設計レビュー時にアクセス不可の一次情報があると、レビューが中断されます。

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。以降のステップではこのパスを使用する。

### Step 2: 設計書の作成

1. **ディレクトリ作成**（絶対パスを使用）:
   ```bash
   mkdir -p [worktree-absolute-path]/draft/design
   ```

2. **ファイル名決定**:
   - `draft/design/issue-[number]-[short-name].md`
   - 例: `draft/design/issue-42-workflow.md`

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

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。
> AI はテストを省略する傾向があるため、設計段階で明確に定義し、省略の余地を排除する。
> 詳細は [テスト規約](../../../docs/dev/testing-convention.md) 参照。

### Small テスト
- (検証対象を列挙: 単体ロジック、バリデーション、マッピング等)

### Medium テスト
- (検証対象を列挙: DB連携、内部サービス結合等)

### Large テスト
- (検証対象を列挙: 実API疎通、E2Eデータフロー等)

### スキップするサイズ（該当する場合のみ）
- サイズ: (物理的に作成不可な理由を明記。「実行時間」「環境依存」は不正当な理由)

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | あり/なし | (新しい技術選定がある場合) |
| docs/ARCHITECTURE.md | あり/なし | (アーキテクチャ変更がある場合) |
| docs/dev/ | あり/なし | (ワークフロー・開発手順変更がある場合) |
| docs/cli-guides/ | あり/なし | (CLI仕様変更がある場合) |
| CLAUDE.md | あり/なし | (規約変更がある場合) |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| (公式ドキュメント名) | (URL) | (設計判断の裏付けとなる引用または要約) |

> **重要**: 設計判断の根拠となる一次情報を必ず記載してください。
> - URLだけでなく、**根拠（引用/要約）** も記載必須
> - レビュー時に一次情報の記載がない場合、設計レビューは中断されます
```

### Step 3: コミット

```bash
cd [worktree-absolute-path] && git add draft/design/ && git commit -m "docs: add design for #[issue-number]"
```

### Step 4: Issueにコメント

設計完了をIssueにコメントします。

```bash
gh issue comment [issue-number] --body-file - <<'EOF'
## 設計書作成完了

設計書を作成しました。

### 成果物

- **ファイル**: `draft/design/issue-[number]-xxx.md`

### 設計の要点

1. **What**: (何を実現するか)
2. **Why**: (なぜこの設計か)
3. **Constraints**: (主な制約)

### テスト戦略

- (主要な検証ポイント)

### 次のステップ

`/issue-review-design [issue-number]` でレビューをお願いします。
EOF
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
