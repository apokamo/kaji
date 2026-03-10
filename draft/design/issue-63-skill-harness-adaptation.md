# [設計] スキルをdaoハーネス適用PJ向けに修正 + ワークフローYAML作成

Issue: #63

## 概要

既存の `.claude/skills/` 内スキル（全13個）を、dao-harness ワークフローエンジンと手動スラッシュコマンドの**両方で動作**するように修正する。併せて `workflows/feature-development.yaml` を作成する。

**スコープ**:
- ワークフロー YAML は **claude-only**（全ステップの agent が `claude`）で作成する。codex / gemini 用スキル（`.agents/skills/`）の整備は別 Issue とする
- **ワークフロー対象スキル（11個）**: issue-design, issue-review-design, issue-fix-design, issue-verify-design, issue-implement, issue-review-code, issue-fix-code, issue-verify-code, issue-doc-check, issue-pr, issue-close
- **手動専用スキル（2個）**: issue-create, issue-start — ワークフロー開始前の準備フェーズであり、ハーネス駆動の対象外。verdict 出力のみ追加し、入力は既存の `$ARGUMENTS` を維持する

## 背景・目的

現在のスキルは手動実行（`/issue-implement 63`）専用に設計されている。dao-harness から駆動するには verdict ブロック出力とコンテキスト変数対応が必要だが、手動実行機能も維持したい。修正は「追加・拡張」を基本とし、既存機能を削除しない。

## インターフェース

### 入力

スキルは以下の2つの入力ソースを**両立サポート**する。

| ソース | 提供元 | 利用可能な変数 |
|--------|--------|---------------|
| コンテキスト変数 | dao-harness が自動注入 | `issue_number`, `step_id`, `previous_verdict`, `cycle_count`, `max_iterations` |
| `$ARGUMENTS` | Claude Code スラッシュコマンド | ユーザーが渡す引数文字列 |

**優先順位**: コンテキスト変数が存在すればそちらを使用。なければ `$ARGUMENTS` から取得。

### 出力

各スキルは既存の完了報告に加え、末尾に verdict ブロックを出力する。

```
---VERDICT---
status: <PASS | RETRY | BACK | ABORT>
reason: |
  (1-2文で判定理由)
evidence: |
  (具体的根拠)
suggestion: |
  (ABORT/BACK時は必須)
---END_VERDICT---
```

### 使用例

**手動実行（従来通り）:**
```bash
# Claude Code のスラッシュコマンドとして
/issue-implement 63
```

**ハーネス駆動:**
```bash
# dao CLI からワークフローとして
dao run workflows/feature-development.yaml 63
```

## 制約・前提条件

- スキルの作業内容（手順・レビュー基準・コミット規約・品質チェック等）は一切変更しない
- 既存の「次のステップ」セクションは残す（手動時に有用、ハーネスは verdict のみ参照）
- `$ARGUMENTS` セクションは残す（手動実行の入力手段）
- 品質チェックコマンドのハードコードパス `bugfix_agent/` は汎用化する

## 方針

### 1. ワークフロー対象スキル（11個）：入力セクションの拡張

既存の `## 引数` セクションを `## 入力` セクションに改名し、両方の入力ソースを記載。

**注意**: 手動専用スキル（issue-create, issue-start）は対象外。既存の `## 引数` セクションをそのまま維持する。

```markdown
## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |

**条件付きで注入される変数（該当スキルのみ記載）:**

| 変数 | 型 | 条件 | 説明 |
|------|-----|------|------|
| `previous_verdict` | str | resume 指定ステップのみ | 前ステップの verdict（reason/evidence/suggestion） |
| `cycle_count` | int | サイクル内ステップのみ | 現在のイテレーション番号 |
| `max_iterations` | int | サイクル内ステップのみ | サイクルの上限回数 |

### 手動実行（スラッシュコマンド）

$ARGUMENTS = <issue-number>

### 解決ルール

コンテキスト変数 `issue_number` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_number` として使用。
```

**スキルごとの入力変数一覧:**

入力セクションには、そのスキルで実際に利用する変数のみを記載する。

| スキル | 分類 | 常に注入 | 条件付き |
|--------|------|---------|---------|
| issue-create | 手動専用 | N/A（`$ARGUMENTS = <title> [type] [description]`） | - |
| issue-start | 手動専用 | N/A（`$ARGUMENTS = <issue-number> [prefix]`） | - |
| issue-design | ワークフロー対象 | `issue_number`, `step_id` | - |
| issue-review-design | ワークフロー対象 | `issue_number`, `step_id` | `cycle_count`, `max_iterations` |
| issue-fix-design | ワークフロー対象 | `issue_number`, `step_id` | `previous_verdict`, `cycle_count`, `max_iterations` |
| issue-verify-design | ワークフロー対象 | `issue_number`, `step_id` | `cycle_count`, `max_iterations` |
| issue-implement | ワークフロー対象 | `issue_number`, `step_id` | - |
| issue-review-code | ワークフロー対象 | `issue_number`, `step_id` | `cycle_count`, `max_iterations` |
| issue-fix-code | ワークフロー対象 | `issue_number`, `step_id` | `previous_verdict`, `cycle_count`, `max_iterations` |
| issue-verify-code | ワークフロー対象 | `issue_number`, `step_id` | `cycle_count`, `max_iterations` |
| issue-doc-check | ワークフロー対象 | `issue_number`, `step_id` | - |
| issue-pr | ワークフロー対象 | `issue_number`, `step_id` | - |
| issue-close | ワークフロー対象 | `issue_number`, `step_id` | - |

### 2. 全スキル：verdict ブロックの追加

全13スキル（ワークフロー対象11個 + 手動専用2個）に verdict 出力を追加。
既存の「完了報告」セクションの**後に** verdict 出力セクションを追加。

手動専用スキル（issue-create, issue-start）は verdict を出力するが、ワークフロー YAML には含めない。手動実行時の verdict は表示されるだけで、遷移制御には使われない。

スキルごとの verdict status マッピング:

| スキル | PASS | RETRY | BACK | ABORT |
|--------|------|-------|------|-------|
| issue-create | Issue 作成成功 | - | - | 作成失敗 |
| issue-start | Worktree 構築成功 | - | - | 構築失敗 |
| issue-design | 設計書作成・コミット完了 | - | - | 設計不可能な要件 |
| issue-review-design | Approve | RETRY（Changes Requested） | - | ABORT レベルの問題 |
| issue-fix-design | 修正完了 | - | - | 修正不可能 |
| issue-verify-design | Approve | RETRY（修正不十分） | - | ABORT レベルの問題 |
| issue-implement | 実装・テスト・品質チェック全パス | RETRY（テスト失敗等） | BACK（設計に問題） | ABORT レベルの問題 |
| issue-review-code | Approve | RETRY（Changes Requested） | BACK（設計に問題） | ABORT レベルの問題 |
| issue-fix-code | 修正完了 | - | - | 修正不可能 |
| issue-verify-code | Approve | RETRY（修正不十分） | - | ABORT レベルの問題 |
| issue-doc-check | チェック完了（更新有無問わず） | - | - | - |
| issue-pr | PR 作成成功 | RETRY（push 失敗等） | - | ABORT レベルの問題 |
| issue-close | クローズ完了 | RETRY（マージ失敗等） | - | ABORT レベルの問題 |

**注意**: `on` マッピングで使用しない status は、スキル内で「出力要件」として列挙しない。ワークフロー YAML の `on` と整合させる。

### 3. review/verify スキル：verdict status と判定の一貫した対応

レビュー系スキル（review-design, review-code, verify-design, verify-code）の既存判定を verdict status に対応付ける。**全レビュー系スキルで統一**する。

| 既存判定 | verdict status | 遷移先（cycle 内） | 説明 |
|---------|---------------|-------------------|------|
| Approve | PASS | cycle 外の次ステップへ | レビュー/検証合格 |
| Changes Requested | RETRY | fix ステップへ（cycle loop head） | 修正が必要 |

**サイクル内の遷移フロー:**

```
design-review サイクル:
  review-design → RETRY → fix-design → verify-design → RETRY → fix-design（loop）
                → PASS  → implement                   → PASS  → implement

code-review サイクル:
  review-code → RETRY → fix-code → verify-code → RETRY → fix-code（loop）
              → PASS  → doc-check              → PASS  → doc-check
```

review ステップは cycle の entry であり、RETRY 時に fix（loop head）へ遷移する。
verify ステップは cycle の loop tail であり、RETRY 時に fix（loop head）へ戻る。
いずれも PASS で cycle を抜けて次ステップへ進む。

### 4. fix スキル：`previous_verdict` のフォールバック対応

issue-fix-code, issue-fix-design の Step 1（コンテキスト取得）に以下を追加:

```markdown
### レビュー結果の取得

1. コンテキスト変数 `previous_verdict` が存在する場合はそれを確認（ハーネス経由）
2. 存在しない場合は Issue コメントから最新のレビュー結果を取得（手動実行時）
```

### 5. パス汎用化

`bugfix_agent/` のハードコードを以下に変更:

```markdown
**品質チェック（コミット前必須）**:

CLAUDE.md の「Pre-Commit (REQUIRED)」セクションに記載されたコマンドを実行すること。
```

これにより PJ ごとの CLAUDE.md に定義された品質チェックが自動的に適用される。

### 6. ワークフロー YAML 作成

`workflows/feature-development.yaml` を作成。

- **agent**: 全ステップ `claude`（claude-only ワークフロー）
- create / start はワークフロー外（手動で事前実行）
- design から close まで
- design-review サイクル（max 3）、code-review サイクル（max 3）
- review の RETRY → fix へ、verify の RETRY → fix へ（cycle loop）
- verify の PASS → cycle 外の次ステップへ

**`resume` 指定と `previous_verdict` の対応関係:**

fix ステップは直前の review/verify ステップのコンテキストを引き継ぐため、`resume` を指定する。これにより `previous_verdict` が自動注入される。

| fix ステップ | `resume` 値 | resume 対象の説明 | previous_verdict の内容 |
|-------------|------------|------------------|----------------------|
| fix-design | `review-design` | 初回は review-design のセッションを継続。2回目以降も同じ値（verify-design の verdict は `state.last_transition_verdict` 経由で渡る） | レビュー/検証の指摘事項 |
| fix-code | `review-code` | 初回は review-code のセッションを継続。2回目以降も同様 | レビュー/検証の指摘事項 |

**YAML での表現:**

`resume` は step ID（文字列）を指定する（`docs/dev/workflow-authoring.md` 準拠、`models.py: Step.resume: str | None`）。

```yaml
steps:
  # ... 前略 ...
  - id: fix-design
    skill: issue-fix-design
    agent: claude
    resume: review-design   # review-design ステップのセッションを継続
    on:
      PASS: verify-design
      ABORT: end
  - id: fix-code
    skill: issue-fix-code
    agent: claude
    resume: review-code     # review-code ステップのセッションを継続
    on:
      PASS: verify-code
      ABORT: end
```

`resume: <step-id>` 指定により、ハーネスは以下を行う:
1. 指定 step ID のセッション ID を `state.sessions` から取得し、CLI の `--resume` オプションに渡す（セッション継続）
2. `state.last_transition_verdict` を `previous_verdict` としてプロンプトに注入する（`dao_harness/prompt.py` 準拠）

**mixed-agent 対応（codex / gemini）**: 本 Issue のスコープ外。別 Issue で `.agents/skills/` にスキルを配置し、ワークフロー YAML の agent フィールドを変更する形で対応する。

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。

### Small テスト
- 修正後の各 SKILL.md に verdict 出力セクションが存在するか（文字列パターンマッチ）
- 入力セクションにコンテキスト変数と $ARGUMENTS の両方が記載されているか
- `bugfix_agent/` がハードコードされていないか
- fix スキルに `previous_verdict` フォールバック記述があるか
- ワークフロー YAML の構文妥当性（`dao_harness.workflow.load_workflow` でパース可能か）

### Medium テスト
- ワークフロー YAML のバリデーション（`dao_harness.workflow.validate_workflow` を通過するか）
- サイクル定義の整合性（entry / loop ステップが正しく参照されているか）
- 全ステップの `skill` がファイルシステム上に存在するか（`dao_harness.skill.validate_skill_exists`）
- fix ステップに `resume` が設定されているか（`previous_verdict` 注入の前提条件）
- 各スキルの SKILL.md verdict example がファイル読み込み → `parse_verdict()` で正常にパースできるか（ファイル I/O + verdict パーサー結合）
- ワークフロー全ステップの遷移先が到達可能か（step 存在確認 + transition 整合性）

### Large テスト
- ワークフロー YAML + 修正済みスキルで `dao run --step <step-id>` を単一ステップ実行し、verdict が正常に parse されるか

### スキップするサイズ
- **Large**: 物理的に作成不可。理由は以下の2点:
  1. `dao` CLI エントリポイントが未実装（`pyproject.toml` の `[project.scripts]` がコメントアウト状態）
  2. 単一ステップ実行は `WorkflowRunner` → `execute_cli()` → `subprocess.Popen(["claude", ...])` の経路を辿り、実際の AI エージェントプロセスの起動が必須。CI 環境でエージェントバイナリ + API キーを前提とするテストは構成できない

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/skill-authoring.md | あり | 手動・ハーネス両立スキルの書き方ガイドを追加（Phase 5） |
| docs/dev/development_workflow.md | なし | ワークフロー自体は変更なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| skill-authoring.md | `docs/dev/skill-authoring.md` | verdict 出力規約、コンテキスト変数仕様、SKILL.md フォーマットを定義。全スキルは verdict ブロック必須 |
| workflow-authoring.md | `docs/dev/workflow-authoring.md` | ワークフロー YAML の構造、step フィールド（`on` マッピング）、cycle 定義の仕様 |
| ARCHITECTURE.md | `docs/ARCHITECTURE.md` | V7 の 3層アーキテクチャ（Workflow YAML → Skill 契約 → Skill 実装）を定義。スキルは Layer 3 |
| ADR 001 | `docs/adr/001-review-cycle-pattern.md` | review と verify の区別、収束保証（verify は新規指摘禁止）、max 3 iterations |
| ADR 003 | `docs/adr/003-skill-harness-architecture.md` | V6→V7 移行決定。CLI skill harness + PJ skills の構成 |
| prompt.py | `dao_harness/prompt.py` | コンテキスト変数の注入ロジック。`issue_number`, `step_id`, `previous_verdict`, `cycle_count`, `max_iterations` |
| testing-convention.md | `docs/dev/testing-convention.md` | S/M/L テストサイズ定義、スキップ判定基準 |
