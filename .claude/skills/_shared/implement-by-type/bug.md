# Implement by Type: bug（バグ修正の実装）

`type:bug` の Issue を `/issue-implement` で修正する際の手順。

## bug 実装の性格

- feat の TDD に対し、**再現テスト先行**で進める。
- 「Red = 再現テストが OB を再現して失敗」「Green = 再現テストが EB に合致」。
- テストが green に変わった瞬間が修正完了の一次的定義（ただし副次的回帰の確認も必須）。

## 手順

### Step B1: 再現テストの作成（Red）

1. 設計書「再現手順」「OB / EB」セクションを精読
2. 最も近い既存テストファイルを特定（なければ新規作成）
3. OB を assert する形でテストを書く
   - 最終形（EB）で書いて、実装前は FAIL することを確認する（推奨）
   - または「修正前の OB」を assert するテストを書き、Green フェーズで EB に書き換える
4. テストサイズは根本原因の層に合わせる（[docs/dev/testing-convention.md](../../../../docs/dev/testing-convention.md) に従う）:
   - 純粋ロジック → `@pytest.mark.small`
   - ファイル I/O / サブプロセス / 内部サービス結合 → `@pytest.mark.medium`
   - 外部 API / E2E → `@pytest.mark.large`
5. 実行して FAIL を確認:
   ```bash
   cd [worktree-absolute-path] && source .venv/bin/activate && pytest tests/<path> -k <test_name> -v
   ```
   - 誤って PASS してしまう場合: 再現条件が違う → 設計書「再現手順」に戻る

### Step B2: 根本原因箇所の修正（Green）

- 設計書「根本原因」に記載された箇所を編集
- 最小侵襲（リファクタ混在禁止）
- 関連するエッジケースに同じ欠陥がないか周辺を点検

### Step B3: 再現テストの Green 確認

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && pytest tests/<path> -v
```

- 再現テストが PASS することを確認
- 同時に**既存の関連テストが壊れていない**ことも確認（影響モジュール全体を流す）

### Step B4: 回帰防止テストの追加（任意だが推奨）

再現テストが単一ケースのみの場合、**変種**も追加すると将来のデグレ検知力が上がる:

- 境界値（off-by-one、空入力、null）
- 関連する入力パターン

### Step B5: docs 更新

- `docs/cli-guides/`, `docs/reference/python/`, `docs/dev/` に OB が「正しい挙動」として書かれていた場合、正しい EB に修正
- `CHANGELOG` / リリースノートへの記載（kaji 現行では未運用のため原則不要）

### Step B6: 品質ゲート

`make check` を実行し、ruff / mypy / pytest がすべて green になることを確認する。

```bash
cd [worktree-absolute-path] && source .venv/bin/activate && make check
```

## コミット前チェックリスト

- [ ] 再現テストが Red → Green に遷移したログが手元にある（コピー用）
- [ ] 設計書「再現手順」と実装テストの assert が一致
- [ ] 影響モジュール全体のテストを流し、他テストが壊れていない
- [ ] リファクタが混入していない（`git diff` で無関係な変更がないか確認）
- [ ] 設計書「根本原因」に挙げた**他の壊れ箇所**も同時に直したか（同根の見落としを防ぐ）
