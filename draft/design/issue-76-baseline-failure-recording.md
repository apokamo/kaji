# [設計] issue-implement で baseline failure を Issue コメントへ記録する

Issue: #76

## 概要

`issue-implement` スキルに baseline check ステップを追加し、実装開始前のテスト失敗を Issue コメントに記録する仕組みを組み込む。

## 背景・目的

#73 の workflow 実行で、implement 冒頭の `pytest` で既知失敗が検出されたが、以下が AI セッション記憶内でのみ管理されたため不安定になった:

- baseline failure と新規 regression の区別
- 途中再開時の「もともとの失敗」の共有
- 停止理由の一貫した説明

Issue コメントを source of truth にすることで、agent 再起動・モデル切替・人間介入をまたいでも前提が安定する。

## インターフェース

### 入力

既存の `issue-implement` と同一（変更なし）:

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |

### 出力

`issue-implement` の実行中に以下が追加される:

1. **Issue コメント**: baseline failure が存在する場合、所定フォーマットで投稿
2. **regression 判定基準**: 以降のテスト実行で baseline failure を除外した差分で合否判定

### 使用例

#### baseline failure あり の場合

```markdown
## Baseline Check 結果

### 実行環境

- **Commit**: abc1234
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
```

#### baseline failure なし の場合

Issue コメントは投稿しない（clean baseline は暗黙の前提として扱う）。

## 制約・前提条件

- 変更対象はスキル定義ファイル（`.claude/skills/*/SKILL.md`）のみ。Python コードの変更はない
- baseline check は `pytest` の実行結果をパースする手順（agent への指示）であり、自動化されたプログラムではない
- Issue コメントのフォーマットは、他スキル（review-code, verify-code）が参照できるよう一意に識別可能にする（`## Baseline Check 結果` ヘッダで識別）
- baseline failure の除外対象は pytest のみ。ruff / mypy は baseline failure の概念を適用しない（lint/型エラーは実装前に修正すべきため）

## 方針

### 1. `issue-implement` SKILL.md の変更

#### 1.1 Step 2.5: Baseline Check の挿入

現在の Step 2（設計書読み込み）と Step 3（Red Phase）の間に挿入する。

```
Step 1: Worktree 情報の取得（既存）
Step 2: 設計書の読み込み（既存）
Step 2.5: Baseline Check（新規）    ← ここ
Step 3: テスト実装 (Red Phase)（既存、番号繰り下げなし）
...
```

手順:

1. 実装前に `pytest` を実行する
2. 全パスの場合: baseline は clean。コメント不要。次ステップへ進む
3. FAILED / ERROR がある場合:
   a. 各失敗テストの `(nodeid, kind, error_type)` を記録する
   b. Issue コメントに所定フォーマットで投稿する（commit hash を含める）
   c. 継続可否を判断する（停止基準に該当するか）
   d. 継続する場合: 以降の regression 判定は baseline failure を除外して行う

**停止基準**:
- baseline failure が本 Issue の実装対象と同一モジュール/機能に影響する場合
- 失敗数が多く、regression の切り分けが困難な場合（目安: 10 件超）

#### 1.2 Step 4 (Green Phase) の pytest 合否条件の変更

現行の Step 4 は `pytest` 全パスを暗黙に期待している。baseline failure がある場合の条件を明示する。

**変更前** (現行):
```
テスト通過確認: pytest
```

**変更後**:
```
テスト通過確認: pytest を実行し、以下の基準で合否判定する。

- baseline failure コメントがない場合: 全テスト PASSED を期待（従来どおり）
- baseline failure コメントがある場合:
  1. FAILED/ERROR のテストを baseline failure 一覧と照合する
  2. 比較キー (nodeid, kind, error_type) が baseline と一致 → 既知（除外）
  3. 比較キーが不一致の新規 FAILED/ERROR → regression（修正が必要）
  4. baseline にあったが消えた（PASSED に変わった）→ 問題なし
```

#### 1.3 Step 7 (品質チェック) の条件変更

pre-commit チェックを 2 段階に分離する。`CLAUDE.md` の `&&` チェーンは baseline failure が残ると `pytest` の exit 非 0 でチェーン全体が失敗するため、`pytest` は個別実行にする。

**変更後**:
```
品質チェック（2 段階実行）:

7a. ruff check / ruff format / mypy: && チェーンで実行。exit 0 必須（baseline failure の概念を適用しない）
7b. pytest: 個別に実行し、以下の基準で合否判定する
  - Baseline Check コメントなし: exit 0 必須（従来どおり）
  - Baseline Check コメントあり: Step 4 と同じ regression 判定基準を適用
    - baseline failure のみ残っている → OK（コミット可）
    - 新規 FAILED/ERROR がある → NG（修正が必要）
```

> **重要**: `pytest` を `&&` チェーンに含めない理由は、baseline failure が存在すると `pytest` が非 0 で終了し、チェーン全体が失敗するため。個別実行することで exit code に関わらず出力を確認し、baseline 照合による合否判定が可能になる。

#### 1.4 Step 9 (Issue コメント) のテスト結果報告の変更

実装完了報告のテスト結果テーブルに baseline failure の情報を含める。

**変更後のテーブル例**:
```
| 項目 | 結果 |
|------|------|
| テスト総数 | XX |
| passed | XX |
| failed | XX (うち baseline: YY, regression: 0) |
| errors | XX (うち baseline: YY, regression: 0) |
| skipped | XX |
```

### 2. `issue-review-code` SKILL.md の変更

#### 2.1 Step 1.5（独立テスト実行）の合否判定条件の変更

現行ルール:
> 上記が exit 0 でなければ **Changes Requested**

**変更後**:
```
1. Issue コメントから最新の `## Baseline Check 結果` を検索する
   - gh issue view [issue-number] --comments で取得済みのコメントから探す
2. Lint / Format / 型チェック（exit 0 必須）:
   - ruff check && ruff format --check && mypy を && チェーンで実行
3. テスト実行（個別）:
   - pytest を && チェーンに含めず個別に実行する
   - 理由: baseline failure が残ると pytest が非 0 で終了し、チェーン全体が失敗するため
4. 合否判定:
   - baseline failure コメントがない場合:
     - 全コマンドが exit 0 でなければ Changes Requested（従来どおり）
   - baseline failure コメントがある場合:
     - ruff check / ruff format / mypy: exit 0 必須（変更なし）
     - pytest: FAILED/ERROR を baseline 一覧と照合する
       - 比較キー (nodeid, kind, error_type) が baseline と完全一致 → 除外
       - 不一致の新規 FAILED/ERROR → Changes Requested
       - baseline failure のみ残っている → テスト合否は OK とする
```

### 3. `issue-verify-code` SKILL.md の変更

Step 1（コンテキスト取得）に baseline failure コメントの参照を追加する。判定ロジックは `issue-review-code` と同一。

```
Issue コメントから最新の `## Baseline Check 結果` を確認し、
テスト実行時の regression 判定に使用する。
```

### 4. `kaji-run-verify` SKILL.md の変更

本スキルはワークフロー検証用であり、個別テストの baseline 管理は scope 外。変更不要。

### 5. Baseline コメントの選択規則

Issue に `## Baseline Check 結果` コメントが複数存在する場合（再実行時など）:

- **最新のコメントを正とする**: Issue コメントは時系列順に並ぶため、最後に投稿された `## Baseline Check 結果` が現在の baseline
- **理由**: baseline は「実装開始時点」のスナップショットであり、再実行時には環境が変わっている可能性がある。最新の baseline check が最も正確な状態を反映する
- **コメントに commit hash を含める**: どの時点のスナップショットかを明示し、古い baseline コメントとの区別を容易にする

### 6. Regression 比較キーの定義

| キー | 説明 | 例 |
|------|------|-----|
| `nodeid` | pytest の test node ID | `tests/test_foo.py::test_bar` |
| `kind` | 失敗種別 | `FAILED` or `ERROR` |
| `error_type` | 例外クラス名 | `AssertionError`, `ImportError` |

**比較ルール**:
- `(nodeid, kind, error_type)` の3タプルが baseline と完全一致 → 既知 failure（除外）
- いずれかが不一致 → regression（新規問題）

**エラーメッセージは比較対象に含めない理由**:
- メッセージは実行環境やデータにより微妙に変化しうる（パス、タイムスタンプ等）
- 誤検出が多くなり、agent の判断負荷が増える
- `error_type` の変化で原因の質的変化は十分に検出可能

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。
> AI はテストを省略する傾向があるため、設計段階で明確に定義し、省略の余地を排除する。
> 詳細は [テスト規約](../../../docs/dev/testing-convention.md) 参照。

### Small テスト

- baseline failure コメントのフォーマット検証:
  - 所定ヘッダ `## Baseline Check 結果` が含まれること
  - テスト失敗一覧テーブルが正しい Markdown 構文であること
  - 判定セクション（継続/停止）が含まれること
- ただし、スキル定義は Markdown テンプレートであり、実行時に agent が解釈する。Markdown テンプレート自体の構文チェックは手動レビューで代替する

### Medium テスト

- 変更後の SKILL.md を使用して、以下のシナリオで `issue-implement` を手動実行し、期待どおりの Issue コメントが投稿されることを確認:
  - **シナリオ 1**: baseline failure あり → コメント投稿される
  - **シナリオ 2**: baseline clean → コメント投稿されない
- `issue-review-code` が baseline failure コメントを参照し、既知失敗を regression から除外できることを確認

### Large テスト

- `kaji run workflows/feature-development.yaml` で E2E 実行し、baseline failure がある状態で implement → review-code のフローを通す
- baseline failure が Issue コメントに記録され、review-code がそれを参照して正しく判定できることを確認

### スキップするサイズ（該当する場合のみ）

- **Small**: 物理的に作成不可。変更対象は Python コードではなく Markdown のスキル定義ファイルのみであるため、pytest で自動検証する対象コードが存在しない。Markdown テンプレートの構文正確性は設計レビューおよび手動確認で担保する。

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更はない |
| docs/dev/development_workflow.md | あり | implement フェーズの手順に baseline check が追加されるため、フェーズ概要に言及が必要 |
| docs/dev/testing-convention.md | なし | テスト規約自体は変更しない |
| docs/cli-guides/ | なし | CLI 仕様変更はない |
| CLAUDE.md | なし | 規約変更はない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #76 本文 | `gh issue view 76` | 「AI の内部記憶だけでは再実行・別 agent・別モデルをまたいで前提が安定しないため、Issue コメントを source of truth にしたい」 |
| Issue #73 (背景事例) | `gh issue view 73` | #76 の動機となった事例。implement 冒頭の baseline pytest で既知失敗が観測され、以降の判断が揺れた |
| 現行 issue-implement SKILL.md | `.claude/skills/issue-implement/SKILL.md` | 現在の Step 3 で pytest を実行するが、baseline failure の記録・判定ルールは未定義 |
| 現行 issue-review-code SKILL.md | `.claude/skills/issue-review-code/SKILL.md` | Step 1.5 で独立テスト実行するが、baseline failure の除外ルールは未定義 |
| 現行 issue-verify-code SKILL.md | `.claude/skills/issue-verify-code/SKILL.md` | 修正確認時にテスト実行するが、baseline failure の参照ルールは未定義 |
| テスト規約 | `docs/dev/testing-convention.md` | テストサイズ定義（S/M/L）とスキップ判定基準 |
