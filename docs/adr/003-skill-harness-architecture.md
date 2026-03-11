# ADR 003: Python 状態マシンから CLI スキルハーネスへの転換

**Status**: Accepted
**Date**: 2026-03-09
**Issue**: #57

## コンテキスト

現行の `bugfix_agent/`（V6）は Python 状態マシンオーケストレータで、約 2000 LOC。Claude / Codex / Gemini の CLI を外部から呼び出し、ステップ実行・verdict 解析・状態遷移を制御していた。

**課題**:

1. **PJ コンテキストの断絶**: 外部オーケストレータから各 PJ のドキュメント（コーディング規約、テスト規約、設計テンプレート）を参照できない。
2. **CLI ネイティブ機能の再実装**: Claude Code がネイティブに持つ機能（スキル実行、コンテキスト管理、ツール呼び出し）を Python で再実装しており、保守コストが高い。
3. **CLI バージョン追従コスト**: Claude v2.0→v2.1、Codex v0.63→v0.112、Gemini v0.18→v0.31 への追従が困難。

## 決定

V6 を廃止し、**CLI スキルハーネス（V7 = `kaji_harness/`）** へ移行する。

### 設計方針

**3層アーキテクチャ**:

| 層 | 責務 | 実体 |
|---|------|------|
| Layer 1: ワークフロー定義 | 何をどの順で実行するか | `workflows/*.yaml` |
| Layer 2: スキル入出力契約 | verdict 形式・コンテキスト変数 | `docs/dev/skill-authoring.md` |
| Layer 3: スキル本体 | 実作業のプロンプト | `.claude/skills/`, `.agents/skills/` |

**ハーネスの役割**: ワークフロー YAML を解釈し、CLI を外部呼び出ししてスキルを順次実行する。スキルのロードは CLI に委譲。ハーネスはスキルの中身を読まない。

**記憶構造**:

| 層 | 媒体 | 寿命 |
|---|------|------|
| 短期 | CLI resume セッション | セッション内 |
| 中期 | `session-state.json`, run ログ | ワークフロー実行中 |
| 長期 | GitHub Issue（本文・コメント） | 永続 |

**移行戦略**:

- `git tag v6.0` で V6 現状を保存。V7 実装中は `git show v6.0:<path>` で必要なノウハウを参照可能
- `bugfix_agent/` は移行期間中の**参照用アーカイブ**であり、保守・機能追加の対象外
- V7 実装完了後に `bugfix_agent/` を削除

## 根拠

- **CLI ネイティブ機能の活用**: スキルが `cwd=workdir` で実行されることで、PJ ドキュメントへのアクセスが可能になる。これは外部オーケストレータからは実現不可能。
- **軽量設計**: 「ハーネスは遷移制御のみ、スキルが実作業」という責務分離により、保守対象が大幅に縮小する（~2000 LOC → ~1300 LOC のハーネス本体 + PJ ごとのスキル）。
- **Anthropic ベストプラクティス準拠**: [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) の「Initializer + Coding Agent パターン」と同じ設計思想。

## 影響

- `kaji_harness/` パッケージを新設（`pyproject.toml` に登録）
- `CLAUDE.md` の品質チェックコマンドを `kaji_harness/` に更新
- `docs/dev/workflow-authoring.md`, `docs/dev/skill-authoring.md` を新設
- `docs/ARCHITECTURE.md` を V7 ハーネスアーキテクチャに改訂

## 代替案と却下理由

**LangChain / LangGraph 等のフレームワーク採用**: 外部依存が増え、CLI バージョン追従の問題が解消されない。PJ コンテキスト断絶問題も残る。

**V6 の段階的改修**: PJ コンテキスト断絶という根本課題は、外部オーケストレータのアーキテクチャを維持する限り解決できない。
