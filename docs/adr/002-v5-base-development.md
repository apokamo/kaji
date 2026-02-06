# ADR-002: V5ベース開発への移行

## Status

Accepted

## Context

### 問題

新規アーキテクチャ（src/core）へのV5機能移植を進めていたが、1ヶ月以上経過しても完了の見通しが立たず、その間エージェントが使用不能な状態が継続していた。

### 移植の進捗状況

| 項目 | 状態 |
|------|------|
| prompts | 5/14 移行済み |
| CLI ガイド | 1/3 移行済み |
| オーケストレータ本体 | 未移行 |
| bugfix ワークフロー | **動作しない** |

### V5（bugfix-v5）の状態

- 9ステートのステートマシンが完全に動作
- テスト、ドキュメント、設計書が揃っている
- Claude/Codex/Gemini の3エージェント対応済み

### src/core のリファクタリング成果

- RunLogger: JSONL実行ログ（#29/#44で移植済み）
- config.py: pydantic-settings統合
- state.py: SessionState API拡張

これらは部分的な改善であり、V5を動作させるには不十分だった。

## Decision

**V5オリジナルをdaoリポジトリに直接コピーし、メインの開発対象とする。**

- src/core/, src/bugfix_agent/, src/workflows/, docs/ を削除
- V5（bugfix_agent/, prompts/, docs/, tests/）を丸ごとコピー
- 改善（RunLogger統合等）はV5が動作する状態を維持しながら後続PRで実施

### 採用理由

1. **動作優先**: 使えないツールを改善し続けることに価値はない
2. **完成品の活用**: V5は動作する完成品であり、移植コストに見合わない
3. **段階的改善**: git historyにsrc/coreの成果は残っており、必要時に取り出せる

### 却下した選択肢

| 選択肢 | 却下理由 |
|--------|---------|
| 移植を継続 | 1ヶ月以上進捗なし、完了見通し不明 |
| src/coreと共存 | 二重管理による混乱リスク |

## Consequences

### Positive

- エージェントが即座に使用可能になる
- 単一のコードベースで管理が簡潔
- V5のドキュメント・テストがそのまま利用可能

### Negative

- src/coreのリファクタリング成果（pydantic-settings等）を一旦失う
- RunLogger統合を再度行う必要がある

### Risks

- V5のディレクトリ構造前提のパス（config.py等）の修正が必要 → 対応済み
