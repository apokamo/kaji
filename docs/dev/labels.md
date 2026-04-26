# GitHub ラベル運用ガイド

kaji の GitHub Issue / PR ラベルは [`.github/labels.yml`](../../.github/labels.yml) で宣言的に管理し、[`.github/workflows/labels-sync.yml`](../../.github/workflows/labels-sync.yml) で repo に同期する。本ガイドは日常運用とトラブル対応の手順をまとめる。

設計根拠: [RFC: GitHub ラベル標準化](../rfc/github-labels-standardization.md)

## ラベル一覧

### type:* (11) — Conventional Commits 準拠

Issue / PR の主分類。原則 1 つだが、`docs+test` のような重複は許可（後述）。

| ラベル | Conventional Commits | 用途 |
|--------|----------------------|------|
| `type:feature` | `feat` | 新機能追加 |
| `type:bug` | `fix` | 不具合・回帰の修正 |
| `type:refactor` | `refactor` | 内部実装の改善（仕様変更なし） |
| `type:docs` | `docs` | ドキュメント |
| `type:test` | `test` | テストの追加・改善 |
| `type:chore` | `chore` | 雑務・依存の掃除 |
| `type:perf` | `perf` | パフォーマンス改善 |
| `type:security` | — | セキュリティ修正（公開済 CVE のみ） |
| `type:release` | — | リリース作業（人間起票） |
| `type:build` | `build` | ビルドシステム・パッケージング |
| `type:ci` | `ci` | CI/CD |

### meta (8) — type:* と直交

| ラベル | 用途 |
|--------|------|
| `breaking-change` | 破壊的変更（SemVer major bump 対象） |
| `dependencies` | 依存関係更新（Dependabot 互換） |
| `good first issue` | 初学者向け |
| `help wanted` | 外部コントリビューション歓迎 |
| `question` | 質問・サポート依頼 |
| `duplicate` | 重複 |
| `invalid` | 無効 |
| `wontfix` | 対応しない |

## 追加・変更手順

1. `.github/labels.yml` を編集（追加 / `name` 以外の更新）
2. ローカルで機械的妥当性を確認:

   ```bash
   pytest tests/test_labels_yml.py
   ```

3. PR を作成し main にマージ
4. `push` トリガーで `labels-sync.yml` が自動実行され、追加・更新が repo に反映される

> **`name` の変更は破壊的**: GitHub のラベル ID は `name` で同定される。`name` を変えると新規作成 + 古い名前は孤児化する。リネームは旧ラベル削除と既存 Issue/PR の付け替えを伴うため、別 Issue で計画的に実施する。

## 削除手順（手作業）

`labels-sync.yml` は **追加と更新のみ**。削除は誤削除リスクのため自動化していない。`labels.yml` から削除した上で:

```bash
gh label list --json name,color,description > labels-backup-$(date +%Y%m%d).json
gh label delete <name>
```

実行前に backup を取得し、PR 等で証跡を残すこと。

## 緊急時の手動 sync

```bash
# dry-run（差分確認のみ）
gh workflow run labels-sync.yml -f dry_run=true

# 本番実行
gh workflow run labels-sync.yml -f dry_run=false
```

## 配色: Catppuccin Mocha パレット

[Catppuccin Mocha](https://catppuccin.com/palette)（MIT License、hex 利用に attribution 不要）を採用。配色衝突を避けるためのポリシー:

- `type:feature` = Green / `type:bug` = Red は意味どおり。colorblind 配慮はテキストの `type:` プレフィックスで補完
- 警告系は `type:security` (Maroon) と `breaking-change` (Flamingo) で差別化
- meta 系のグレーは `duplicate` (Overlay0) → `invalid` (Surface2) → `wontfix` (Surface1) と段階的に暗くする

## bot 所有ラベルとの境界

[release-please](https://github.com/googleapis/release-please) は以下のラベルを **bot 自身が自動生成・管理** する。`labels.yml` の管理対象外（生成・削除に介入しない）:

- `autorelease: pending`
- `autorelease: tagged`
- `autorelease: snapshot`
- `autorelease: published`
- `release-please: force-run`

これらは bot のステートマシンであり、人間が `labels.yml` で管理しようとすると bot との競合状態が発生する。

`type:release` は **人間が起票するリリース関連 Issue / PR 専用**。release-please が作成する PR 自体には `type:release` を付けない。

## Dependabot 導入時の設定

将来 `.github/dependabot.yml` を導入する際は、`dependencies` ラベルが既に `labels.yml` で管理されているため、Dependabot のデフォルト自動生成と衝突しない。`dependabot.yml` 側で:

```yaml
updates:
  - package-ecosystem: "pip"
    directory: "/"
    labels:
      - "dependencies"
      - "type:chore"
```

の併用を推奨する（Dependabot 自体の導入は別 Issue）。

## 複数 `type:*` 付与ポリシー

許可する。ただし主たる 1 つを推奨。例:

- `docs+test` 同時更新 → 主が docs なら `type:docs`、テストの整備が主目的なら `type:test`
- `feature` で `breaking-change` を伴う場合 → `type:feature` + `breaking-change`（meta の併用）

## `type:security` の embargo 運用

- **公開済 CVE のみ** に付与する
- **embargo 中の脆弱性** は public Issue / PR に `type:security` を付けない（情報漏洩防止）
- 内部対応は private security advisory または別の private repo で進め、公開時に `type:security` を付与する

## drift 検知（週次 cron）

`labels-sync.yml` は毎週月曜 09:00 JST（UTC 0:00）に自動再同期する。手動で GitHub UI からラベルを編集した場合、次の cron 実行で `labels.yml` の定義に巻き戻る。手動編集は drift とみなして避けること。

## 参照

- 設計 RFC: [github-labels-standardization](../rfc/github-labels-standardization.md)
- 移植元 (kamo2): フル 66 ラベル体系 + automation/metrics 拡張
- Conventional Commits: <https://www.conventionalcommits.org/en/v1.0.0/>
