# Implement by Type: refactor（リファクタリングの実装）

`type:refactor` の Issue を `/issue-implement` で実装する際の手順。

## refactor 実装の性格

- 外部から観測可能な振る舞いを**変えない**ことが絶対要件。
- TDD の通常サイクルではなく、「ベースライン計測 → safety net 確保 → 改修 → 再計測」の 4 段階。
- 新規テストは原則 bridging（振る舞い保持の保証）のみ。

## 手順

### Step R1: ベースライン計測

設計書「ベースライン計測」セクションに記載のコマンドを実行し、現状値を記録:

- テストカバレッジ（対象モジュール）
- 静的解析の警告数 / 循環依存数
- ベンチマーク（パフォーマンス refactor の場合）
- コード行数 / 複雑度

記録は Issue コメントの「実装完了報告」に含めるため、標準出力をそのまま保管する:

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && <計測コマンド> | tee /tmp/baseline-<issue>.txt
```

### Step R2: safety net（既存テストの強化）

対象モジュールの既存テストカバレッジを評価し、不足があれば**先に**テストを足す:

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && pytest --cov=<module> tests/<path>
```

- カバーされていない分岐 / エッジケースに対してテストを追加
- この段階のテストは「現行の（リファクタ前の）振る舞い」を固定する目的。改修後も同じ結果が出ることを保証する bridge になる
- テストが green であることを確認してから Step R3 に進む

### Step R3: 改修実施

- 設計書「方針」の Before / After に従って段階的に置換
- 各ステップごとにテスト実行して green を維持（小さくコミットする）
- 振る舞い変更に見える diff が出たら一時停止し、設計書に戻って意図確認

### Step R4: 振る舞い非変更の検証

改修後に再度テスト全体を実行:

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && pytest tests/<path>
```

- 既存テスト + Step R2 で追加した safety net が全件 PASS
- 1 件でも FAIL した場合、それは振る舞い変更の混入 → 原因を特定して直す

### Step R5: 改善指標の再計測

Step R1 と同じコマンドで改修後の値を計測:

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && <計測コマンド> | tee /tmp/after-<issue>.txt
```

- 設計書「改善指標」の目標値を達成しているか確認
- 達成できていない場合は設計書に戻って方針見直し（勝手に追加の変更を混ぜない）

### Step R6: docs 更新

- 内部構造の ADR が設計書で指定されていれば追記（`docs/adr/`）
- 開発ワークフロー・テスト規約に影響がある場合は `docs/dev/` を更新
- 公開 IF 不変なので外部向けドキュメントは原則更新不要
- 不可避な IF 変更がある場合のみ `docs/cli-guides/` 等を更新

### Step R7: 品質ゲート

`make check` を実行し、ruff / mypy / pytest がすべて green になることを確認する。

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && make check
```

## コミット前チェックリスト

- [ ] Step R1 / R5 のベースライン比較（改善指標の達成）を Issue コメントに貼れる形で保管済み
- [ ] 既存テストがすべて PASS（FAIL = 振る舞い変更の混入）
- [ ] Step R2 で追加した safety net テストが green
- [ ] 公開 IF に変更がない（あるなら設計書で承認済みの範囲内か確認）
- [ ] ついでのバグ修正や feat を混ぜていない（混ぜるなら別 Issue に切り出す）
- [ ] `git diff --stat` の変更範囲が設計書の Scope と一致
