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

```
## Baseline Check 結果

### 実行コマンド

pytest (at commit abc1234)

### Baseline Failure 一覧

| テスト | エラー種別 | 概要 |
|--------|-----------|------|
| tests/test_foo.py::test_bar | FAILED | AssertionError: ... |
| tests/test_baz.py::test_qux | ERROR | ImportError: ... |

### 判定

- **継続**: 上記は変更前から存在する失敗であり、本 Issue の対象外
- **Regression 判定基準**: 上記以外の新規 FAILED/ERROR が発生した場合を regression とする

### 停止する場合の基準

以下に該当する場合は実装を開始せず、Issue コメントに理由を記録して停止する:

- baseline failure が本 Issue の実装対象と同一モジュール/機能に影響する場合
- 失敗数が多く、regression の切り分けが困難な場合
```

#### baseline failure なし の場合

Issue コメントは投稿しない（clean baseline は暗黙の前提として扱う）。

## 制約・前提条件

- 変更対象はスキル定義ファイル（`.claude/skills/*/SKILL.md`）のみ。Python コードの変更はない
- baseline check は `pytest` の実行結果をパースする手順（agent への指示）であり、自動化されたプログラムではない
- Issue コメントのフォーマットは、他スキル（review-code, verify-code）が参照できるよう一意に識別可能にする（`## Baseline Check 結果` ヘッダで識別）

## 方針

### 1. `issue-implement` SKILL.md の変更

現在の Step 2（設計書読み込み）と Step 3（Red Phase）の間に、新しい **Step 2.5: Baseline Check** を挿入する。

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
   a. 失敗テスト一覧を記録する
   b. Issue コメントに所定フォーマットで投稿する
   c. 継続可否を判断する（停止基準に該当するか）
   d. 継続する場合: 以降の regression 判定は baseline failure を除外して行う

### 2. `issue-review-code` SKILL.md の変更

Step 1.5（独立テスト実行）に、baseline failure コメントの参照手順を追加する。

- Issue コメントから `## Baseline Check 結果` を検索し、baseline failure が記録されている場合はそのテストを regression 判定から除外する
- baseline failure コメントがない場合は、全テスト合格を期待する（従来どおり）

### 3. `issue-verify-code` SKILL.md の変更

Step 1（コンテキスト取得）に同様の参照手順を追加する。

### 4. `kaji-run-verify` SKILL.md の変更

本スキルはワークフロー検証用であり、個別テストの baseline 管理は scope 外。変更不要。

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
