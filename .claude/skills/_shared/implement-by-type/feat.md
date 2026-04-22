# Implement by Type: feat（新機能の実装）

`type:feature` の Issue を `/issue-implement` で実装する際の手順。

## feat 実装の性格

- 設計書の **IF 定義** と **ユースケース** を契約として実装する。
- 標準 TDD サイクル（Red → Green → Refactor）を素直に適用する。
- 新規コードが中心。既存コードへの侵襲は最小に保つ。

## 手順

### Step F1: 設計書の IF と使用例を再確認

- 設計書「インターフェース」「使用例」から、**呼び出し側が書くコード**を特定
- テストは使用例のパターンを最初の対象にする（ユーザー視点で書くテストが最も回帰価値が高い）

### Step F2: Small テストから書き始める（Red）

- バリデーション・マッピング・純粋ロジック単位で失敗するテストを書く
- `@pytest.mark.small` を付与
- 実行して期待通りに赤くなることを確認（ImportError や assert 失敗）

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && pytest tests/<path> -v
```

### Step F3: 最小実装で Green

- テストを通す最小コードを書く
- この段階で将来の拡張を考えた抽象化は入れない（YAGNI）

### Step F4: Medium テスト（ファイル I/O / サブプロセス / 内部サービス結合）

設計書で Medium が定義されている場合:

- tmp_path fixture を使ったファイル I/O テスト、サブプロセス起動を含む結合テスト、CLI 呼び出しの往復テスト等
- `@pytest.mark.medium` を付与
- 統合テストで mock するくらいなら Large に上げる（mock と実挙動の乖離が回帰を隠すため）

### Step F5: Large テスト（実 API / E2E）

設計書で Large が定義されている場合:

- 実 API 疎通や外部サービス接続、CLI を実際に起動する E2E を含む
- `@pytest.mark.large` を付与
- skip 禁止（API キー不在や環境未整備は環境側の問題として修正対象）

### Step F6: Refactor

- テストが green であることを確認しつつ、命名改善・重複除去・型補強
- 過剰な抽象化は避ける（似た 3 行は抽象化より素直さを優先）

### Step F7: docs 更新

設計書「影響ドキュメント」で「あり」のドキュメントを更新:

- `docs/cli-guides/` — 新規 CLI 仕様
- `docs/reference/python/` — コーディング規約への影響
- `docs/dev/` — 開発ワークフロー・テスト規約への影響
- `docs/adr/` — 技術選定（新ライブラリ採用等）
- `CLAUDE.md` — プロジェクト規約や必読ドキュメントの更新

### Step F8: 品質ゲート

`make check` を実行し、ruff / mypy / pytest がすべて green になることを確認する（[CLAUDE.md](../../../../CLAUDE.md) の Pre-Commit 要件）。

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && make check
```

## コミット前チェックリスト

- [ ] 使用例どおりの呼び出しでテストが通る
- [ ] 設計書のユースケースが E2E テスト等でカバーされている（Large 必要なケース）
- [ ] 新規ライブラリを追加したなら `pyproject.toml` に登録済み（手動 `pip install` 禁止、`uv sync` で同期）
- [ ] `any` / `Any` を使っていない（必要な場合は理由を明記）
- [ ] ログ・エラーメッセージが [docs/reference/python/logging.md](../../../../docs/reference/python/logging.md) / [docs/reference/python/error-handling.md](../../../../docs/reference/python/error-handling.md) に沿っている
