---
description: Issue要件に基づき、draft/design/に設計書を作成する。worktree内での作業が前提。
name: issue-design
---

# Issue Design

指定されたIssueに基づき、設計書（Markdown）を作成します。
設計書は `draft/design/` に作成され、`i-dev-final-check` 時に Issue 本文へアーカイブされます。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| Issue着手後、実装前 | ✅ 必須 |
| worktreeが存在しない | ❌ 先に `/issue-start` を実行 |

**ワークフロー内の位置**: create → start → **design** → review-design → implement → review-code → doc-check → i-dev-final-check → i-pr → close

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

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。
> 実行時コード変更では Small / Medium / Large の観点を定義し、
> docs-only / metadata-only / packaging-only 変更では変更固有検証と
> 恒久テストを追加しない理由を明記する。
> 詳細は [テスト規約](../../../docs/dev/testing-convention.md) 参照。

### 変更タイプ
- (実行時コード変更 / docs-only / metadata-only / packaging-only)

### 実行時コード変更の場合

#### Small テスト
- (検証対象を列挙: 単体ロジック、バリデーション、マッピング等)

#### Medium テスト
- (検証対象を列挙: DB連携、内部サービス結合等)

#### Large テスト
- (検証対象を列挙: 実API疎通、E2Eデータフロー等)

### docs-only / metadata-only / packaging-only の場合

#### 変更固有検証
- (例: link check、隔離環境での `pip install -e .`、`importlib.metadata` 確認)

#### 恒久テストを追加しない理由
- (テスト規約の 4 条件に沿って記載)

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

### Step 2.5: 完了条件の段階確認

設計書の各セクションが記載されているか段階的に確認する:

1. **必須セクションの存在確認**:
   - [ ] 概要
   - [ ] 背景・目的
   - [ ] インターフェース（入力・出力）
   - [ ] 制約・前提条件
   - [ ] 方針
   - [ ] テスト戦略（変更タイプに応じたセクション）
   - [ ] 影響ドキュメント
   - [ ] 参照情報（Primary Sources）

2. **内容の妥当性確認**:
   - テスト戦略が変更タイプに対して妥当か
   - Primary Sources に根拠が記載されているか
   - 影響ドキュメントが網羅的か

不足がある場合は設計書を補完してからコミットする。

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

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

---VERDICT---
status: PASS
reason: |
  設計書作成・コミット完了
evidence: |
  draft/design/issue-XX-*.md を作成
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 設計書作成・コミット完了 |
| ABORT | 設計不可能な要件 |
