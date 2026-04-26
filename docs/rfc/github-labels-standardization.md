# RFC: GitHub ラベル標準化

## ステータス

採用（#154）— 定義・同期・運用ガイドのコード成果物は配置済み。旧ラベル削除と OPEN Issue 一括再ラベルは PR / 移行フェーズで実施する（後述「移行手順」参照）。

- 定義: [`.github/labels.yml`](../../.github/labels.yml)
- 同期: [`.github/workflows/labels-sync.yml`](../../.github/workflows/labels-sync.yml)
- 運用ガイド: [`docs/dev/labels.md`](../dev/labels.md)

## 概要

Conventional Commits 準拠の `type:*` 11 ラベル + 直交 meta 8 ラベル（合計 **19 ラベル**）を `.github/labels.yml` で宣言的に管理し、GitHub Actions（`labels-sync.yml`）で repo に同期する。配色は Catppuccin Mocha パレット。旧ラベル（`enhancement`, `bug`, `documentation`, `refactoring`, `e2e-test`）は移行フェーズで削除し、OPEN Issue を新ラベルへ一括再マッピングする。

## 背景

### 当初 RFC（2025）の方針と運用実態の乖離

当初 RFC は「`type:*` プレフィックス導入」「既存ラベルは残す」「共存期間: 無期限」「既存 Issue の再ラベルは行わない」を採用した。しかし運用実態は以下のとおり破綻していた:

| 観測事実 | 1 次情報 |
|---|---|
| `type:docs` 導入後も `documentation` との二重付与が継続 | #141, #149 |
| `type:refactor` 導入後も `refactoring` との二重付与が継続 | #147, #148, #149 |
| `/issue-create` スキルが `type:*` 前提で repo 側未整備 → 失敗 | #153 (`type:feature` not found) |
| 検索性の二重コスト | `bug` と `type:bug` を OR で引かなければ過去事例を網羅できない |

→ 共存戦略は「中途半端」の恒久化を招くため、**一括刷新**へ方針反転する。

### 当初提案の不足点

- Conventional Commits 標準（`feat, fix, docs, style, refactor, perf, test, build, ci, chore`）から `build` / `ci` を欠いていた
- release-please の changelog セクション（`build:` / `ci:`）と整合しない
- リリース作業（version bump、changelog 微修正、リリーススクリプト改修）の分類軸が不在
- SemVer 判定の最重要シグナルである breaking change ラベルが不在
- Dependabot 導入時の `dependencies` ラベルとの将来衝突が未考慮

## 提案（採用済み）

### type:* 11 ラベル（Conventional Commits 準拠）

| ラベル | 配色 (Catppuccin Mocha) | 用途 |
|--------|--------------------------|------|
| `type:feature` | `a6e3a1` Green | 新機能追加 |
| `type:bug` | `f38ba8` Red | 不具合・回帰の修正 |
| `type:refactor` | `89b4fa` Blue | 内部実装の改善（仕様変更なし） |
| `type:docs` | `b4befe` Lavender | ドキュメントの追加・更新 |
| `type:test` | `94e2d5` Teal | テストの追加・改善 |
| `type:chore` | `fab387` Peach | 雑務・依存の掃除など |
| `type:perf` | `f9e2af` Yellow | パフォーマンス改善 |
| `type:security` | `eba0ac` Maroon | 脆弱性対応・セキュリティ強化 |
| `type:release` | `cba6f7` Mauve | リリース作業（人間が起票するもの） |
| `type:build` | `f5e0dc` Rosewater | ビルドシステム・パッケージング |
| `type:ci` | `74c7ec` Sapphire | CI/CD・GitHub Actions |

`type:style` は採用しない（Python + ruff 自動整形環境では実用価値が低い）。

### meta 8 ラベル（type:* と直交）

| ラベル | 配色 | 用途 |
|--------|------|------|
| `breaking-change` | `f2cdcd` Flamingo | 破壊的変更（SemVer major bump 対象） |
| `dependencies` | `9399b2` Overlay2 | 依存関係更新（Dependabot 互換） |
| `good first issue` | `89dceb` Sky | 初学者向け |
| `help wanted` | `f5c2e7` Pink | 支援歓迎 |
| `question` | `cdd6f4` Text | 質問・サポート依頼 |
| `duplicate` | `6c7086` Overlay0 | 重複 |
| `invalid` | `585b70` Surface2 | 無効 |
| `wontfix` | `45475a` Surface1 | 対応しない |

### 移行マッピング（移行フェーズで実施）

| 旧ラベル | 新ラベル | 補足 |
|---|---|---|
| `enhancement` | `type:feature` | 新機能。文脈により `type:chore` 等に振り分けたものもある |
| `bug` | `type:bug` | |
| `documentation` | `type:docs` | |
| `refactoring` | `type:refactor` | |
| `e2e-test` | `type:test` | 統合（独立価値が低いため） |

OPEN Issue（実施時点で 9 件程度）は移行フェーズで手動再マッピングする。CLOSED Issue は scope 外（コスト > 効果）。

### 移行手順

PR マージ前後の移行作業は次の順序で実施する。証跡は PR コメント / Issue #154 に残す。

1. `gh label list --json name,color,description > labels-backup.json` で backup を取得し、PR に添付する
2. `gh workflow run labels-sync.yml -f dry_run=true` で差分を確認する
3. `gh workflow run labels-sync.yml -f dry_run=false` で 19 ラベルを repo に作成・更新する
4. OPEN Issue を移行マッピング表に従って手動再ラベル（`gh issue edit <n> --add-label <new> --remove-label <old>`）
5. 旧 5 ラベル（`enhancement`, `bug`, `documentation`, `refactoring`, `e2e-test`）を `gh label delete <name>` で削除する
6. self-validation: 本 RFC を実装した #154 のラベルから `enhancement` を剥がし、`type:chore` のみが残ることを確認する

### bot 所有ラベルの分離

release-please は `autorelease:pending` / `autorelease:tagged` / `autorelease:snapshot` / `autorelease:published` / `release-please:force-run` を **bot 自身が自動生成・管理** する。これらは bot のステートマシンであり、`labels.yml` の管理対象外（生成・削除に介入しない）。

`type:release` は **人間が起票するリリース関連 Issue / PR 専用**。release-please が作成する PR 自体には `type:release` を付与しない。

### `breaking-change` 採用根拠

SemVer において最重要のシグナル。release-please も `BREAKING CHANGE:` フッター / `!` で major bump を判定する。`type:*` と直交する単独ラベルとして導入。

### `dependencies` 先行作成根拠

kaji は現状 `.github/dependabot.yml` 未導入だが、将来導入時の互換性を確保するため `dependencies`（プレフィックス無し）を先行作成。Dependabot のデフォルト挙動と衝突せず、導入時は `labels: ["dependencies", "type:chore"]` の併用を運用ガイドで推奨する。

### 運用ポリシー

- **`type:*` の cardinality**: single-select（1 Issue / PR に 1 ラベルのみ）。dev workflow と各スキル（`issue-create` / `issue-review-ready` / `issue-review-code`）が single-select を前提に分岐するため、複数 `type:*` 付与は禁止する。`docs+test` のような混在は主目的で 1 つに集約し、破壊的変更は `type:*` + 直交 meta `breaking-change` を併用する
- **`type:release` / `type:build` / `type:ci` のスキル統合**: ラベルとしては定義するが、`/issue-create` の type 引数および [development_workflow.md](../dev/development_workflow.md) のスキル分岐表には現時点で含まれていない。これらの type に該当する Issue は `type:chore` 等で起票し、起票後に手動で付け替える。スキル側への正式組み込みは別 Issue で扱う
- **`type:security` の embargo 運用**: 公開済 CVE のみ。embargo 中は public Issue / PR に付与しない
- **配色アクセシビリティ**: `type:feature` (Green) と `type:bug` (Red) はテキストプレフィックスで補完するため colorblind 配慮は致命的でない
- **同期トリガー**: 手動 / `.github/labels.yml` への push / 週次 cron（drift 検知）

## bootstrap exception の記録

本 RFC を実装した #154 自体は、`type:*` 体系を新設する Issue であるため、`type:*` ラベル体系成立前は付与不能だった。実装途中で `type:chore` を bootstrap 用途として先行作成し、Issue に付与することで `issue-review-design` の cardinality チェックを通した。移行フェーズの self-validation で `enhancement` を剥がして `type:chore` のみが残ることを確認する（前述「移行手順」step 6）。

## 参照情報

- Conventional Commits 1.0.0: <https://www.conventionalcommits.org/en/v1.0.0/>
- release-please: <https://github.com/googleapis/release-please>
- Dependabot config: <https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuration-options-for-the-dependabot.yml-file>
- Catppuccin Mocha パレット: <https://catppuccin.com/palette> （MIT License、attribution 不要）
- 運用ガイド: [`docs/dev/labels.md`](../dev/labels.md)
