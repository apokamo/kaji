---
description: ワークフローを手動実行して検証し、失敗時は継続せず原因を調査して Issue に記録する。成功時も気づきや詰まりどころを Issue に記録する。
name: kaji-run-verify
---

# Kaji Run Verify

`kaji run` によるワークフローの手動検証を標準化します。
検証目的の実行であり、エラー発生時はその時点で停止し、原因調査と Issue コメントを優先します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| ワークフロー変更後の実機検証 | ✅ 必須 |
| 「`kaji run workflows/... <issue>` を実行して、結果を Issue に残して」と依頼されたとき | ✅ 推奨 |
| 通常の feature 開発をそのまま進めたいだけ | ⚠️ 任意 |
| 単に YAML の静的検証だけしたい | ❌ `kaji validate` のみで十分 |

## 入力

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <workflow-path> <issue-number> [kaji run options...]
```

- `workflow-path` (必須): 例 `workflows/feature-development.yaml`
- `issue-number` (必須): GitHub Issue 番号
- `kaji run options...` (任意): `--from` / `--step` / `--workdir` / `--quiet` などをそのまま後続に渡す

### 例

```bash
/kaji-run-verify workflows/feature-development.yaml 73
/kaji-run-verify workflows/feature-development.yaml 73 --from fix-code
/kaji-run-verify workflows/feature-development.yaml 73 --workdir ../kaji-feat-73
```

## 前提知識の読み込み

以下を必要に応じて参照すること。

1. **ワークフロー定義**: `docs/dev/workflow-authoring.md`
2. **スキル作成規約**: `docs/dev/skill-authoring.md`
3. **共通 Worktree 解決**: `../_shared/worktree-resolve.md`

## 共通ルール

- エラーが発生したら、追加の `kaji run` 再実行で先に進めない。まず原因調査と Issue コメントを完了させる。
- 長時間実行中の状態確認は、毎回細かくポーリングせず **2 分程度の間隔** を目安にしてよい。新規出力がなくても `kaji run` プロセス自体が継続している限り、即座に停止扱いしない。
- review / fix / verify サイクル中の差し戻しや、agent が自力で修正可能な一時的失敗は、workflow 全体の失敗とみなして過剰に停止しない。`kaji run` 自体の終了、明確なハング、人手介入必須の異常が確認できた時点で停止判断を行う。
- 成功時も `気になった点` と `詰まった点` を記録する。なければ `特になし` と明記する。
- Issue コメントには生ログ全文を貼らず、要点と必要な抜粋だけを載せる。
- 原因を断定できない場合は、**確定事項** と **仮説** を分けて書く。

## 実行手順

### Step 1: 引数の解析

`$ARGUMENTS` から以下を取得する。

1. `workflow_path`
2. `issue_number`
3. `extra_args` (`kaji run` にそのまま渡す残りの引数)

`workflow_path` はメインリポジトリ基準の相対パスとして解決し、ファイルが存在することを確認する。

### Step 2: Worktree の解決（`--workdir` 未指定時のみ）

`extra_args` に `--workdir` が含まれていない場合のみ、
[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順で Issue 本文から Worktree を解決する。

- 解決できた場合: `kaji run` に `--workdir [resolved-path]` を追加する
- 解決できない場合: `--workdir` なしで続行してよい。ただし Issue コメントにその旨を明記する

### Step 3: 事前検証

まず `kaji validate` を実行する。

```bash
cd [main-repo-absolute-path] && source .venv/bin/activate && kaji validate [workflow-path]
```

- exit 0: 次へ進む
- exit 1 以上: **ここで停止**
  - YAML 定義エラーまたは引数エラーとして扱う
  - `kaji run` は実行しない
  - 標準出力 / 標準エラーの要点を控える

### Step 4: ワークフロー実行

ログを保存しながら `kaji run` を実行する。

```bash
LOG_FILE=$(mktemp)
cd [main-repo-absolute-path] && source .venv/bin/activate && \
  kaji run [workflow-path] [issue-number] [extra_args...] 2>&1 | tee "$LOG_FILE"
RUN_EXIT=${PIPESTATUS[0]}
```

### Step 5: 判定と調査

#### 5.1 成功時（exit 0）

- 実行コマンド
- 使用した `workdir`
- 実行したオプション
- ログの要点
- 気になった点 / 詰まった点
- 次回向けノウハウ

を整理して Issue にコメントする。

#### 5.2 失敗時（exit 非 0）

**その場で停止し、継続実行しないこと。**

最低限、以下を調査する。

1. **どこで止まったか**
   - 最後に開始した step / 成功した step / 失敗した step
2. **終了コードの種別**
   - `1`: ワークフロー ABORT または予期しないエラー
   - `2`: 定義エラー（YAML 不正、スキル未検出、引数エラー等）
   - `3`: 実行時エラー（CLI 実行失敗、タイムアウト、verdict 解析失敗等）
3. **関連ファイル**
   - 該当 workflow YAML
   - 失敗 step が指している SKILL.md
   - 必要なら Issue 本文、worktree 状態、対象ログ
4. **原因の整理**
   - 確定事項
   - 有力な原因仮説
   - 追加で必要なアクション

必要に応じて以下の調査を行う。

```bash
sed -n '1,220p' [workflow-path]
gh issue view [issue-number] --comments
git worktree list
```

### Step 6: Issue コメント

成功・失敗のどちらでも、Issue に必ずコメントする。
`gh issue comment --body-file - <<'EOF'` を使い、以下のテンプレートをベースに記録すること。

#### 成功テンプレート

````bash
gh issue comment [issue-number] --body-file - <<'EOF'
# ワークフロー実行検証結果

## 実行コマンド

```bash
kaji validate [workflow-path]
kaji run [workflow-path] [issue-number] [extra_args...]
```

## 結果

| 項目 | 値 |
|------|-----|
| Workflow | `[workflow-path]` |
| Issue | #[issue-number] |
| Exit Code | 0 |
| Workdir | `[resolved-or-explicit-workdir]` |
| Validation | PASS |

## ログ要点

```text
(主要な出力を 10-30 行程度で抜粋)
```

## 気になった点 / 詰まった点

- (なければ `特になし`)

## 次回向けノウハウ

- (なければ `特になし`)
EOF
````

#### 失敗テンプレート

````bash
gh issue comment [issue-number] --body-file - <<'EOF'
# ワークフロー実行検証結果

## 実行コマンド

```bash
kaji validate [workflow-path]
kaji run [workflow-path] [issue-number] [extra_args...]
```

## 失敗概要

| 項目 | 値 |
|------|-----|
| Workflow | `[workflow-path]` |
| Issue | #[issue-number] |
| Exit Code | `[exit-code]` |
| 停止位置 | `[step-or-phase]` |
| Workdir | `[resolved-or-explicit-workdir]` |
| Validation | PASS / FAIL |

## 原因調査

### 確定事項

- ...

### 原因仮説

- ...

### 追加で確認したコマンド

- `...`

## ログ抜粋

```text
(失敗原因が分かる範囲の抜粋)
```

## 気になった点 / 詰まった点

- ...

## 次回向けノウハウ

- ...

## 提案アクション

- ...
EOF
````

## 完了報告

成功時:

```
## ワークフロー実行検証完了

| 項目 | 値 |
|------|-----|
| Workflow | [workflow-path] |
| Issue | #[issue-number] |
| 判定 | PASS |

Issue に検証結果とノウハウをコメント済み。
```

失敗時:

```
## ワークフロー実行検証中断

| 項目 | 値 |
|------|-----|
| Workflow | [workflow-path] |
| Issue | #[issue-number] |
| 判定 | ABORT |

Issue に失敗原因の調査結果をコメント済み。
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

---VERDICT---
status: PASS
reason: |
  ワークフローの手動検証が完了し、Issue に結果を記録した
evidence: |
  kaji validate と kaji run が完了し、ログ要点・気づき・ノウハウを Issue にコメントした
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | `kaji validate` と `kaji run` が成功し、Issue コメントまで完了 |
| ABORT | バリデーション失敗、実行失敗、前提不足、または原因調査が必要 |
