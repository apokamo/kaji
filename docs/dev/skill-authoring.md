# スキル作成マニュアル

kaji_harness から呼び出されるスキルの書き方。

## スキルの役割

スキルは「1ステップの実作業」を担うプロンプト資産。ハーネスは何をどの順で実行するかを制御し、スキル本体は agent（Claude Code / Codex / Gemini）がネイティブにロードして実行する。

**ハーネスはスキルの中身を読まない**。スキルのロードは CLI に完全に委譲する。ただし、実行結果として出る `VERDICT` の出力契約には依存する。

## ファイル配置

スキルの実体は `.kaji/config.toml` の `paths.skill_dir` で指定されたカノニカルディレクトリに置く。他エージェント用ディレクトリ（例: `.agents/skills/`）はカノニカルディレクトリへのシンボリックリンクとして構成する。ハーネスは `skill_dir` と `skill` フィールドからパスを解決する（`agent` フィールドはパス解決に使用しない）。

```toml
# .kaji/config.toml
[paths]
skill_dir = ".claude/skills"   # 必須。カノニカルディレクトリ
```

```
.claude/skills/           # カノニカルディレクトリ（skill_dir で指定）
  issue-design
  issue-implement
  issue-review-code

.agents/skills/           # 他エージェント用 symlink
  issue-review-code -> ../../.claude/skills/issue-review-code
```

各スキルはディレクトリで、`SKILL.md` を含む。

## SKILL.md フォーマット

```markdown
---
name: issue-review-code
description: "コードレビューを実施し、verdict を返す"
---

# Issue Review Code

(スキルの説明とプロンプト本文)

## 出力フォーマット

必ず以下の形式で verdict を出力すること:

---VERDICT---
status: PASS
reason: |
  レビュー対象のコードは設計書との整合性・品質基準を満たしている。
evidence: |
  - テストカバレッジ: 87%（目標80%以上）
  - ruff / mypy: エラーなし
  - 設計書の全要件が実装されている
suggestion: ""
---END_VERDICT---
```

### exec_script: LLM 中継なしの deterministic skill

LLM の判断を必要としない決定論的 skill（GitHub API polling、固定アルゴリズム計算など）は、
frontmatter に `exec_script` フィールドを追加することで agent spawn を skip し、harness が
直接 `python -m <module>` として subprocess 実行する経路を選択できる。

```markdown
---
name: review-poll
description: codex auto-review polling
exec_script: kaji_harness.scripts.review_poll_entry
---
```

| フィールド | 型 | 必須 | 説明 |
|-----------|-----|------|------|
| `exec_script` | str | 任意 | Python module dotted path。`python -m <value>` で実行される |

**制約**:
- 値は Python identifier の `.` 区切り表記（`[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*`）のみ。
  違反は skill load 時に `SkillFrontmatterError` で fail-fast（path traversal / shell metachar を
  構文段階で遮断）。
- 設定された skill を呼ぶ step では workflow YAML の `agent` / `model` / `effort` が無視される
  （harness が WARN を出す）。step 側で `agent` を省略してよい。

**入力（harness が env として注入）**:

| env 変数 | 説明 |
|----------|------|
| `KAJI_ISSUE_ID` | canonical issue id |
| `KAJI_ISSUE_REF` | 人間可読の Issue 参照 |
| `KAJI_STEP_ID` | 実行中の step id |
| `KAJI_WORKTREE_DIR` | Issue worktree の絶対パス |
| `KAJI_BRANCH_NAME` | Issue branch 名 |
| `KAJI_PROVIDER_TYPE` | `github` / `local` |
| `KAJI_GIT_REMOTE` | git remote 名（既定 `origin`） |
| `KAJI_DEFAULT_BRANCH` | default branch 名 |
| `KAJI_PR_ID` | PR 解決済みなら数値文字列、未解決なら未注入 |
| `KAJI_PR_REF` | `#<n>` 形式の PR 参照 |

**出力契約**:
- verdict ブロックを stdout に出力する責務は script 側にある（既存 `kaji_harness.scripts.codex_review_poll.emit_verdict()` 同型）。
- exec_script 経路では **AI formatter fallback は呼ばれない**（fabrication 防止 + 決定論性維持）。
  delimiter 不在は `VerdictNotFound` で fail-loud。
- script は verdict を emit したら **必ず `return 0`** で終了する。ABORT / RETRY 等の業務失敗は
  verdict status で表現し、`sys.exit(1)` で表現してはならない。
- catastrophic 失敗（依存 CLI 不在、import エラー等）は raise させてよい。harness が
  `ScriptExecutionError` として ERROR 扱いする（non-zero exit は stdout の verdict 有無を
  問わず常に fail-loud）。

## verdict 出力規約

すべてのスキルは作業完了時に、以下の verdict を **3 経路** で残さなければならない（Issue #220）。

```
---VERDICT---
status: <PASS | RETRY | BACK | ABORT>
reason: |
  (1-2文で判断理由を要約)
evidence: |
  (判断の根拠となる具体的情報。テスト結果、レビュー指摘、差分など)
suggestion: |
  (ABORT/BACK 時は必須: 次のアクションの提案)
---END_VERDICT---
```

1. **artifact `verdict.yaml`（primary）**: コンテキスト変数 `verdict_path`（exec_script では env `KAJI_VERDICT_PATH`）が指す絶対パスへ、`status` / `reason` / `evidence` / `suggestion` の **pure YAML**（`---VERDICT---` delimiter なし）を保存する。
2. **作業報告 Issue comment 末尾（fallback）**: 作業報告コメントの末尾に、上記 `---VERDICT---` block をそのまま追記する。verdict 専用コメントを新設せず、既存の作業報告コメントの末尾に足すだけでよい。
3. **stdout（互換 fallback）**: 同じ `---VERDICT---` block を stdout にも出力する。

ハーネスはこの 3 経路を **artifact → comment → stdout** の順で解決する（詳細は [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) § Verdict 判定機構）。`verdict_path` への保存が primary 経路であり、`kaji issue comment --body` の引数や別コマンドの入力にだけ verdict を埋めても、artifact / 作業報告コメント末尾 / stdout のいずれにも残っていなければ判定されない。

`verdict.yaml` の例（pure YAML）:

```yaml
status: PASS
reason: 設計書と整合し品質基準を満たす
evidence: ruff / mypy / pytest すべて pass
suggestion: ""
```

**verdict 不在は fail-loud** (Issue #193 / #220): artifact `verdict.yaml` も、現在 attempt 以降の作業報告コメント末尾 block も、stdout の `---VERDICT---` delimiter も一切無いままセッションが終了した場合、ハーネスは AI formatter で穴埋めせず `VerdictNotFound` を `HarnessError` として raise する（`EXIT_RUNTIME_ERROR (= 3)` にマップ）。逆に `verdict.yaml` が「存在するが壊れている / 必須欠落 / invalid status」場合も fail-loud（comment / stdout へは fallthrough しない）。`ScheduleWakeup` 等で再起動を期待する場合でも、メインセッション終了時点で verdict を上記 3 経路に残す責務はスキル側にある。

> **stdout 経路の段階廃止方針**: stdout への verdict 出力は、未移行スキル・stdout ベースの既存テストとの互換のための fallback として当面残す。全スキルが `verdict.yaml` を書く運用に移行した後、stdout 経路の段階廃止を別 Issue で検討する。

### verdict の選択基準

| verdict | 使用条件 |
|---------|---------|
| `PASS` | 目標を達成し、次ステップへ進んでよい |
| `RETRY` | 同一ステップを再実行することで解決できる問題がある |
| `BACK` | 前段のステップを修正しなければ解決できない問題がある |
| `ABORT` | ワークフロー全体を停止すべき重大な問題がある |

**制約**:
- `ABORT` / `BACK` の場合、`suggestion` は必須（空文字不可）
- `evidence` は必須（空文字不可）
- `reason` は必須（空文字不可）
- `status` は上記4値のみ有効

### YAML block scalar の利用

`evidence` / `suggestion` に複数行を書く場合は YAML block scalar (`|`) を使用する。

```
---VERDICT---
status: RETRY
reason: テストが3件失敗している
evidence: |
  FAILED tests/test_workflow_parser.py::TestValidationErrors::test_empty_steps
  FAILED tests/test_cli_args.py::TestBuildClaudeArgs::test_basic_args
  FAILED tests/test_state_persistence.py::TestSessionState::test_load_or_create
suggestion: |
  失敗しているテストを修正してから再試行すること。
  特に workflow_parser のエラーは型チェックの問題と思われる。
---END_VERDICT---
```

## ハーネスが注入するコンテキスト変数

スキルのプロンプトには以下の変数が自動注入される。

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値または local ID。例: `"153"` / `"local-pc1-1"`） |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`、local では bare ID。例: `"#153"` / `"local-pc1-1"`） |
| `step_id` | str | 現在のステップ ID |
| `verdict_path` | str | 当該 attempt の `verdict.yaml` 絶対パス（Issue #220）。スキルはここへ pure YAML の verdict を保存する。exec_script 経路では env `KAJI_VERDICT_PATH` として注入される |
| `previous_verdict` | str | 前ステップの verdict 要約（resume ステップ等） |
| `cycle_count` | int | 現在のサイクルイテレーション（サイクル内ステップのみ） |
| `max_iterations` | int | サイクルの上限回数（サイクル内ステップのみ） |

`previous_verdict` は `resume` 指定ステップに注入される。`review-code` のように独立評価が必要なステップには注入されない。修正系スキルでは、詳細なレビュー内容は Issue コメントを正とし、`previous_verdict` は補助的な要約として扱う。

## GitHub Issue の活用

スキルは GitHub Issue を長期記憶として使う。

```bash
# 作業結果を Issue にコメント
kaji issue comment <issue_id> --body "..."

# Issue 本文を更新（状態の記録）
kaji issue edit <issue_id> --body "..."
```

**ルール**: レビュー系スキル（review-\*, verify-\*）は Issue にコメントで結果を記録する。実装系スキルは完了報告をコメントする。

## 推奨パターン

### Devil's Advocate プリアンブル（レビュー系）

レビュースキルには「批判的視点」を強制するプリアンブルを入れる。

```markdown
> **CRITICAL**: このレビューは改善提案ではなく、実装上の欠陥を発見することが目的。
> 「問題なさそう」と思った場合でも、境界条件・型不整合・エラー伝播の漏れを必ず確認すること。
```

### インクリメンタルコミット（実装系）

実装スキルは論理的な単位でコミットを分割する。

```bash
git add <files> && git commit -m "feat: implement X component"
git add <files> && git commit -m "test: add tests for X"
```

## 手動・ハーネス両立スキルの書き方

ワークフロー対象スキルは、ハーネス駆動と手動スラッシュコマンドの**両方で動作**するよう設計する。

### 入力セクション

`## 引数` の代わりに `## 入力` セクションを使い、両方の入力ソースを記載する。

```markdown
## 入力

### ハーネス経由（コンテキスト変数）

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値または local ID） |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`、local では bare ID） |
| `step_id` | str | 現在のステップ ID |

### 手動実行（スラッシュコマンド）

$ARGUMENTS = <issue_id>

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_id` として使用。

`issue_ref` はハーネス経由ではプロンプトに自動注入される（`prompt.py` 側で provider 別に整形）。手動実行時は `issue_id` から導出する: GitHub 数値 ID なら `#<issue_id>`、`local-*` 形式なら bare ID（`#` を付けない）。
```

**優先順位**: コンテキスト変数 > `$ARGUMENTS`。ハーネスが変数を注入している場合はそちらを使い、手動実行時は従来通り `$ARGUMENTS` から取得する。

### 手動専用スキル

`issue-create`、`issue-start` のようにワークフロー開始前のフェーズを担うスキルは、ハーネス駆動の対象外。verdict 出力は追加するが、入力は既存の `$ARGUMENTS` を維持する。

### fix スキルのレビュー結果取得

`issue-fix-code`、`issue-fix-design` では、ハーネス経由でも手動実行でも、Issue コメントをレビュー結果の正として取得する。`previous_verdict` が存在する場合は補助情報として使ってよい。

```markdown
### レビュー結果の取得

1. Issue コメントから最新のレビュー結果を取得する
2. コンテキスト変数 `previous_verdict` が存在する場合は補助情報として確認する
```

### 品質チェックコマンドの汎用化

スキル内で品質チェックコマンドを記述する場合、プロジェクト固有のパス（例: `bugfix_agent/`）をハードコードしない。代わりに CLAUDE.md を参照する形にする。

```markdown
**品質チェック（コミット前必須）**:

CLAUDE.md の「Pre-Commit (REQUIRED)」セクションに記載されたコマンドを実行すること。
```

## 関連ドキュメント

- [ワークフロー定義マニュアル](workflow-authoring.md)
- [テスト規約](testing-convention.md)
- [Architecture](../ARCHITECTURE.md)
