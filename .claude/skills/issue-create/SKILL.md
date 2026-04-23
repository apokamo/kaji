---
description: Issue作成とラベル付与を行う。開発ワークフローの起点。review-ready 7観点を満たす本文生成を誘導する。
name: issue-create
---

# Issue Create

GitHub Issue を作成し、適切なラベルを付与する。

本スキルは `issue-review-ready`（`.claude/skills/issue-review-ready/SKILL.md` のチェック観点1〜7）で RETRY にならない水準の本文を生成することを目標とする。単なるテンプレ埋めではなく、「何を」「どこに」「なぜ」「何が満たされれば完了か」「その根拠」を明示できるまで対話で不足を補う。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| 新機能・バグ修正・リファクタの着手前 | ✅ 必須 |
| 既存Issueがある場合 | ❌ 不要 |

**ワークフロー内の位置**: **create** → review-ready → start → ...

## 引数

```
$ARGUMENTS = <title> [type] [description]
```

- `title` (必須): Issue タイトル
- `type` (任意): `feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `perf` / `security`（デフォルト: feat）
- `description` (任意): 詳細説明。省略時は対話で収集。

## type → ラベル マッピング

> **参照**: `docs/rfc/github-labels-standardization.md`（GitHub Labels 標準化ドキュメント）

| type | ラベル | 用途 |
|------|--------|------|
| `feat` | `type:feature` | 新機能追加 |
| `fix` | `type:bug` | バグ修正 |
| `refactor` | `type:refactor` | リファクタリング |
| `docs` | `type:docs` | ドキュメント |
| `test` | `type:test` | テスト追加・改善 |
| `chore` | `type:chore` | 雑務・依存の掃除 |
| `perf` | `type:perf` | パフォーマンス改善 |
| `security` | `type:security` | セキュリティ対応 |

## 実行手順

### Step 1: 引数の解析

`$ARGUMENTS` から `title`, `type`, `description` を取得する。

- `type` が未指定なら `feat` をデフォルトとする
- `description` が未指定または情報不足なら、ユーザーに対話で詳細を確認する

### Step 2: review-ready 7観点に沿った素材収集

本文起草の前に、以下が揃っているかを確認する。不足している場合は **対話で補う**、またはコマンド実行・ファイル閲覧で事実を確認する。推測で埋めない。

| # | 観点（review-ready）| 収集する情報 |
|---|--------------------|--------------|
| 1 | 構造の完備 | 概要・目的・完了条件の3セクションを埋める材料 |
| 2 | 概要の具体性 | 「何を」「どこに」: 対象モジュール / ディレクトリ / ファイルパス、対象ドキュメントパス |
| 3 | 目的の根拠 | 背景・動機: 既存コード/ドキュメント/運用で困っている具体的事象 |
| 4 | 完了条件の検証可能性 | 客観的に判定可能な条件（CLI 実行結果、ファイル存在、テスト通過、docs 整合等） |
| 5 | 1次情報の明示 | 外部 URL、`docs/` パス、関連 Issue/PR 番号、ログ、CLI 出力のいずれか |
| 6 | 記述間の整合性 | 概要・目的・完了条件が互いに矛盾しないこと |
| 7 | 作業スコープの推定可能性 | dev: 対象ファイル/ディレクトリ/技術スタック。docs-only: 対象ドキュメントパスまたは領域 |

**補強のヒント**:
- 「〜したい」で止まる動機は、**どの現状のどこが問題か**を 1 文追加する
- 「改善する」「最適化する」等の主観表現は、計測可能な判定条件に置き換える（例: 「実行時間 X 秒以内」「Y が表示される」「`make check` 通過」）
- 「らしい」「はず」など推測を断定調に書かない。確認できないものは Issue に書かずに事実確認してから書く

### Step 3: Issue 本文の作成（type 別テンプレートを Read して適用）

Step 1 で確定した `type` に応じて、以下のテンプレートファイルを Read ツールで読み込み、その本文構造に従って Issue 本文を組み立てる。

| type | テンプレートファイル |
|------|----------------------|
| `feat` | `.claude/skills/issue-create/templates/issue-feat.md` |
| `fix` | `.claude/skills/issue-create/templates/issue-bug.md` |
| `refactor` | `.claude/skills/issue-create/templates/issue-refactor.md` |
| `docs` | `.claude/skills/issue-create/templates/issue-docs.md` |

**canonical 外 type のフォールバック**: `test` / `chore` / `perf` / `security` を受け取った場合は `issue-feat.md` を使用する（dispatch 方式の制約）。

> **dispatch 方式**: パターン A（Read ツールによる静的選択読み）を採用。SKILL.md は薄く保ち、テンプレート本文は外部ファイルで管理する。

各テンプレートには「本文の雛形」に加えて、type 特有のチェックポイント（例: feat のユースケース、bug の OB/EB、refactor の測定指標、docs の対象パス）が記載されている。テンプレート末尾の「チェックポイント」を対話で必ず確認してから本文を確定する。

**共通の追記ルール**（全 type 共通）:
- 設計書の配置先が明らかなら `draft/design/issue-<number>-<slug>.md` を参考欄に予告してもよい
- 推測や「〜らしい」を事実として書かない
- 対話で集めた 1 次情報（URL / docs パス / Issue 番号 / ログ）は参考欄に必ず明記する

### Step 4: Issue 作成とラベル付与

```bash
gh issue create --title "[title]" --body "[body]" --label "[label]"
```

### Step 5: 完了報告

以下の形式で報告すること。

```
## Issue 作成完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| タイトル | [title] |
| Type | [type] |
| ラベル | [label] |
| URL | [issue-url] |

### 次のステップ

作業を開始するには `/issue-review-ready [issue-number]` でレディネスレビューを実行してください。
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること。

```
---VERDICT---
status: PASS
reason: |
  Issue 作成成功
evidence: |
  Issue #XX を作成、ラベル付与済み
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | Issue 作成成功 |
| ABORT | 作成失敗、または review-ready の観点に必要な素材が対話でも揃わない |
