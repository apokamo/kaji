# スキル作成マニュアル

dao_harness から呼び出されるスキルの書き方。

## スキルの役割

スキルは「1ステップの実作業」を担うプロンプト資産。ハーネスは何をどの順で実行するかを制御し、スキル本体は agent（Claude Code / Codex / Gemini）がネイティブにロードして実行する。

**ハーネスはスキルの中身を読まない**。スキルのロードは CLI に完全に委譲する。

## ファイル配置

スキルは agent ごとに異なるディレクトリに配置する。ハーネスは step の `agent` フィールドと `skill` フィールドからパスを解決する。

```
.claude/skills/           # Claude Code 用
  issue-design            # ← skill: issue-design, agent: claude
  issue-implement
  issue-review-code

.agents/skills/           # Codex / Gemini 用
  issue-review-code       # ← skill: issue-review-code, agent: codex/gemini
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

## verdict 出力規約

すべてのスキルは最終出力として以下の形式の verdict ブロックを含めなければならない。

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
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |
| `previous_verdict` | str | 前ステップの verdict（resume ステップのみ） |
| `cycle_count` | int | 現在のサイクルイテレーション（サイクル内ステップのみ） |
| `max_iterations` | int | サイクルの上限回数（サイクル内ステップのみ） |

`previous_verdict` は `resume` 指定ステップ（文脈継続ステップ）にのみ注入される。`review-code` のように独立評価が必要なステップには注入されない。

## GitHub Issue の活用

スキルは GitHub Issue を長期記憶として使う。

```bash
# 作業結果を Issue にコメント
gh issue comment <issue_number> --body "..."

# Issue 本文を更新（状態の記録）
gh issue edit <issue_number> --body "..."
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

## 関連ドキュメント

- [ワークフロー定義マニュアル](workflow-authoring.md)
- [テスト規約](testing-convention.md)
- [Architecture](../ARCHITECTURE.md)
