---
description: 設計書（draft/design/）に基づき、TDD（テスト駆動開発）アプローチを用いて機能を実装する。
name: issue-implement
---

# Issue Implement

承認された設計書を契約として、Red → Green → Refactor で実装する。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| 設計レビュー完了・承認後 | ✅ 必須 |
| 設計レビュー未完了 | ❌ 待機 |

**ワークフロー内の位置**: design → review-design → **implement** → review-code → i-dev-final-check → i-pr → close

## 入力

### ハーネス経由（コンテキスト変数）

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値または local ID） |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`、local では bare ID） |
| `step_id` | str | 現在のステップ ID |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue_id>
```

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用し、なければ `$ARGUMENTS` の第1引数を使う。
手動実行時の `issue_ref` は、数値なら `#<issue_id>`、`local-*` なら bare ID とする。

## 読み込み方針（段階的開示）

開始時に [implement-quickref.md](../../../docs/dev/implement-quickref.md) **だけ**を Read する。
正本 docs・reference・template は quickref の対応表と各 Step の指示に従い、必要になった時点で読む。

- 既読ファイルの全文再 Read はしない。必要箇所を `rg -n '^##? '` 等で特定し、対象セクションだけを読む。
- 全文再 Read を許すのは、(a) 現セッションに本文がない、(b) 前回 Read 後にファイルが変更された、(c) 対象範囲だけでは判断不能、のいずれかに限る。理由を作業メモに残す。
- 同じ内容を shell の `cat` と Read ツールの両方で取得しない。
- 規則本文は正本 docs を優先する。quickref と本 skill は規則の要約・読込タイミング・手順だけを持つ。

## 前提条件

- `/issue-start` 実行済み
- `/issue-design` で設計書作成済み
- 設計レビュー承認済み

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 無関係な問題の報告ルール

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) に従って絶対パスを解決し、以降はそのパスを使う。

### Step 2: 設計書を解決して1回だけ読む

1. コンテキストに `design_path` があれば `[worktree_dir]/[design_path]` を採用する。
2. なければ `draft/design/issue-[issue_id]-*.md` をファイル名だけ列挙し、候補を解決する。本文を `cat` しない。
3. 候補が0件または複数で一意に決められなければ、実装を開始せず設計フェーズへ戻す。
4. 解決した設計書を Read で **1回だけ**読み、「インターフェース」「テスト戦略」「影響ドキュメント」を作業契約とする。

BACK 等で再入し、設計書が前回 Read 後に変わっている場合は、まず diff と変更セクションだけを読む。全文再 Read は変更範囲から判断できない場合だけ行う。

### Step 2.5: Baseline Check

この時点で初めて [references/baseline-check.md](references/baseline-check.md) を Read し、記載手順を省略せず実行する。
変更前 pytest の FAILED / ERROR を記録し、停止基準・最新コメント選択・regression 比較キーを同 reference の契約どおり扱う。

### Step 2.6: type 判定と type 別ガイド

```bash
kaji issue view [issue_id] --json labels --jq '[.labels[].name] | map(select(startswith("type:")))'
```

| 判定 | 処理 |
|------|------|
| type ラベルが0件または複数 | 実装せず `/issue-review-ready` へ差し戻して `ABORT` |
| `type:docs` | `/i-doc-update` へ誘導して `ABORT` |
| `type:feature` | `_shared/implement-by-type/feat.md` をこの時点で Read |
| `type:bug` | `_shared/implement-by-type/bug.md` をこの時点で Read |
| `type:refactor` | `_shared/implement-by-type/refactor.md` をこの時点で Read |
| その他の `type:*` | feat のガイドをフォールバックとして Read |

type 別ガイドが Step 3〜5 の具体化を担い、本 skill の記述は共通の枠組みとする。

### Step 2.7: 実装直前に必要な正本を読む

quickref の「状況 → 正本」表に従い、まず必要なセクションを特定して部分 Read する。

- テスト実装前: `docs/dev/testing-convention.md` の「テストサイズ定義」「テスト戦略の原則」と設計で該当する節
- **Python コードを書く前**: AGENTS.md の契約に従い、`docs/reference/python/*.md` をすべてロードする。既に同一内容を読んだファイルは再 Read しない
- docs 更新判断時: `docs/dev/documentation_update_criteria.md` の該当節
- 完了条件確認時: `docs/dev/workflow_completion_criteria.md` の該当節
- workflow 全体や戻り先が不明な場合だけ: `docs/dev/development_workflow.md` の該当節

### Step 3: テスト実装（Red）

設計書「テスト戦略」と type 別ガイドに従う。

- 実行時コード変更は、設計されたテストを先に書き、期待どおり失敗することを確認する。
- docs-only / metadata-only / packaging-only は、設計された変更固有検証で十分なら恒久テストを機械的に追加しない。
- テストサイズはリソース制約で決め、`small` / `medium` / `large` marker を付ける。
- 設計で必要としたテストの省略、設計で不要としたテストの独自追加、環境不備を理由とする skip は禁止する。

```bash
cd [worktree_dir] && source .venv/bin/activate && pytest
```

### Step 4: 機能実装（Green）

設計書のインターフェースに従い、最小実装でテストを通す。

```bash
cd [worktree_dir] && source .venv/bin/activate && pytest
```

Baseline Check コメントがある場合は `(nodeid, kind, error_type)` を照合し、新規 FAILED / ERROR を regression として修正する。

### Step 5: Refactor

可読性と構造を改善し、テストが引き続き通ることを確認する。type 別ガイドの追加測定・safety net 契約を維持する。

### Step 6: docs 更新

設計書「影響ドキュメント」で「あり」の文書を更新する。判断境界が必要な時だけ `documentation_update_criteria.md` の該当節を読む。

### Step 7: 品質チェック（コミット前必須）

AGENTS.md の pre-commit 契約を通し、出力を Step 9 の証跡用に保持する。

Baseline Check が clean なら `source .venv/bin/activate && make check` を実行し、exit 0 と全出力を記録する。以下の 7a / 7b はその内訳であり、同じコマンドを重複実行しない。

Baseline failure を記録して継続している場合だけ、pytest の既知失敗を分類できるよう 7a / 7b に分離して実行する。この例外でも `make check` の全対象（`kaji_harness/ tests/ experiments/` の ruff / format、`kaji_harness/` の mypy、全 pytest）を省略しない。

#### 7a. Lint / Format / 型チェック（exit 0 必須）

```bash
cd [worktree_dir] && source .venv/bin/activate && ruff check kaji_harness/ tests/ experiments/ && ruff format --check kaji_harness/ tests/ experiments/ && mypy kaji_harness/
```

全項目必須。`ruff format --check` は非破壊 gate とし、修正には `make fmt` を使って再実行する。

#### 7b. pytest（個別実行）

```bash
cd [worktree_dir] && source .venv/bin/activate && pytest
```

`pytest` は baseline failure 判定のため `&&` チェーンへ含めない。

- Baseline Check コメントなし: 全テスト PASSED / exit 0 必須
- コメントあり: FAILED / ERROR が記録済み3タプルと全一致し、新規 regression が0件であること

上記 7a / 7b は baseline failure がある場合に限り、`make check` の ruff / format / mypy / pytest 契約を同じ対象へ分離実行したもの。いずれかが基準を満たさなければコミットしない。

### Step 7.5: 完了条件の段階確認

Issue 本文 `## 完了条件` のうち、実装・テスト・docs で確認できる条件を照合する。判断に必要な場合だけ `workflow_completion_criteria.md` の「各ステップの証跡責務」周辺を部分 Read し、結果を Step 9 に含める。

### Step 8: コミット

```bash
cd [worktree_dir] && git add <issue-scope-files> && git commit -m "<type-prefix>: implement <feature> for [issue_ref]"
```

prefix は Issue type に合わせ、無関係な変更を stage しない。

### Step 8.5: Pre-Handoff Review（MANDATORY）

コミット後、この時点で初めて [references/pre-handoff-review.md](references/pre-handoff-review.md) を Read し、capability 分岐、入力、rubric、verdict loop、Issue コメント投稿、回数制限を記載どおり実行する。

この自己評価は `Yes` / `No` / `With fixes` だけを返し、workflow の正式 verdict は発行しない。責務の削除・省略、Issue コメント証跡の省略は禁止する。

### Step 9: 実装完了報告

Pre-Handoff Review が完了した後、この時点で初めて [templates/implement-report.md](templates/implement-report.md) を Read する。保持した pytest / ruff / format / mypy の出力、変更ファイル、Pre-Handoff Review、完了条件を埋めて投稿する。

実装完了報告には `--verdict-step implement --verdict-status <STATUS>` を **常に**付ける。Baseline / Pre-Handoff の証跡コメントには付けない。

### Step 10: 完了報告

Issue、テスト件数、品質チェック結果、次の `/issue-review-code [issue_id]` を簡潔に報告する。

## Verdict 出力

作業報告 Issue コメント末尾、stdout、最後に `verdict_path` の pure YAML へ同じ内容を残す。

```text
---VERDICT---
status: PASS
reason: |
  実装・テスト・品質チェック全パス
evidence: |
  pytest 全テストパス、ruff/format/mypy エラーなし、Pre-Handoff Review 完了
suggestion: |
---END_VERDICT---
```

| status | 条件 |
|--------|------|
| PASS | 実装・テスト・品質チェック全パス |
| RETRY | テスト失敗等 |
| BACK | 設計に問題 |
| ABORT | type ラベル不正、`type:docs` 等の重大な前提違反 |
