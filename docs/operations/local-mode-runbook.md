# Local Mode 検証期間運用 Runbook

検証期間中に kaji local-mode を SoT として運用するための実用 v1 runbook。
複数 PC・コード同期戦略・forge 移行判断までを 1 ファイルで提供する。

## 1. このドキュメントの位置づけ

- **検証期間の開始**: 2026-05-08（Phase 5 着手時点）
- **検証期間の終了基準**: forge 採用先（github 復帰 / 他）が
  確定した時点。期間 KPI（日数 / 件数）は設定しない。user の forge 採用判断に
  同期する
- **位置づけ**: 当初の BCP runbook（GitHub 停止からの復旧手順）ではなく、
  **検証期間中の local-mode SoT 運用 runbook**

検証期間終了時、本 runbook と `draft/design/local-mode/design.md` は forge
採用先に応じて再構成する（v2 として書き直す前提）。

参照: `draft/design/local-mode/design.md` § 概要 / § 検証戦略の前提 / § 残課題。

## 2. セットアップ

### 2.1 単一 PC セットアップ

```toml
# .kaji/config.toml （tracked）
[provider]
type = "local"

[provider.local]
default_branch = "main"
```

```toml
# .kaji/config.local.toml （gitignored）
[provider.local]
machine_id = "pc1"
```

- `machine_id` は `[a-z0-9]{1,16}`（ハイフン禁止）。`pc1` / `mac1` /
  `desktop` / `home` / `office` 等
- `.gitignore` に `.kaji/config.local.toml` と `.kaji/counters/` が登録されて
  いることを確認（`kaji local init` が初期化する）
- 既存設定がある場合、`kaji issue` / `kaji pr` / `kaji run` 実行時に config
  不備があれば exit 2 で停止し、エラーメッセージで「何を、どこに、どう書くか」
  を案内する

### 2.2 複数 PC セットアップ

各 PC で **異なる `machine_id`** を必ず設定する。`.kaji/config.local.toml`
は gitignored なので、PC ごとの設定は git に流れない。

| PC | machine_id 例 | counter dir |
|----|--------------|-------------|
| pc1（メイン） | `pc1` | `.kaji/counters/pc1.txt` |
| pc2（ノート） | `pc2` | `.kaji/counters/pc2.txt` |
| mac1（外出先） | `mac1` | `.kaji/counters/mac1.txt` |

- counter dir は PC ごとに独立（gitignored）。git pull 時に他 PC の counter
  と衝突しない
- 既存 repo を別 PC に clone した場合、`.kaji/config.local.toml` を作成し
  `machine_id` を新値で設定する。counter は不在でも `next_local_id()` が
  既存 `.kaji/issues/local-<machine>-*` の最大値から自動補正する

### 2.3 セットアップ後の動作確認

```bash
kaji config provider-type      # → local
kaji issue list --state open   # （まだ何もなければ空、エラーが出なければ OK）
```

## 3. 日常運用

### 3.1 Issue ライフサイクル（/issue-create → /issue-close）

検証期間中の Issue は **type ラベル** に応じて以下の workflow を使い分ける：

| type | workflow YAML | 使用 Skill 系列 |
|------|--------------|----------------|
| type:feature | `feature-development-local.yaml` | issue-design / issue-implement / issue-review-* / issue-close |
| type:docs | `docs-maintenance-local.yaml` (Phase 5 追加) | i-doc-update / i-doc-review / i-doc-fix / i-doc-verify / i-doc-final-check / issue-close |
| github 用 (`feature-development.yaml` 等) | — | **検証期間中は使用しない**（forge 通信を行わない方針） |

呼び出し例:

```bash
# 事前手動実行
/issue-create   # Issue 起票 (Skill)
/issue-start    # worktree 作成 (Skill)

# 自動連続実行（kaji run はファイルパス必須。basename 探索はしない）
kaji run .kaji/wf/feature-development-local.yaml local-pc1-1
# または
kaji run .kaji/wf/docs-maintenance-local.yaml   local-pc1-2
```

### 3.1a docs-only Issue の手動運用（`kaji run` を使わない場合）

`docs-maintenance-local.yaml` を使わず、Skill 単位で手動実行する代替手順：

1. `/i-doc-update [issue_id]`
2. `/i-doc-review [issue_id]`
3. RETRY なら `/i-doc-fix [issue_id]` → `/i-doc-verify [issue_id]` を収束まで繰り返す
4. `/i-doc-final-check [issue_id]`
5. `/issue-close [issue_id]`

> `/i-pr` は **使用しない**。検証期間中は forge 通信を行わない方針のため、
> `kaji pr create` は Phase 4 の bare-provider ガードで exit 2 となる。

### 3.2 複数 PC 並行運用

- 各 PC は自分の `local-<machine>-<n>` 番号空間のみ採番（machine prefix で
  物理分離されているため衝突は構造的に発生しない）
- 1 サイクル: `git pull` → 作業 → commit → `git push`
- Issue / counter / config の git tracked 状態確認:
  - tracked: `.kaji/issues/`, `.kaji/config.toml`
  - gitignored: `.kaji/config.local.toml`, `.kaji/counters/`

### 3.3 Conflict 解決

| ケース | 対処 |
|--------|------|
| 同一 Issue を複数 PC で編集 | git の通常 merge conflict として手動解決 |
| counter の不整合（fresh clone / cleanup 後）| `next_local_id()` が `.kaji/issues/local-<machine>-*` の最大値から自動補正するため、特別対処不要 |
| duplicate issue dir 検出時 | `resolve_issue_dir` が glob で重複検出してエラー停止する。手動で重複 dir を削除（merge 事故由来が多い）|

## 4. コード同期戦略

**現状**: GitHub に push（バックアップ兼ねる）。

### 4.1 採用候補の比較

| 案 | 特徴 | 適用シナリオ |
|----|------|-------------|
| GitHub Cloud | 無償枠で private repo / branch 保護 | **現状の選択** |
| Self-host (Gitea / Forgejo) | フルコントロール、保守必要 | 長期運用が固まった後の選択肢 |
| LAN bare repo (NAS) | オフライン可、PC 間限定 | 外部送信を避けたい場合 |
| Bundle ファイル USB 持ち回り | git remote 不要 | 単一 PC + 偶発的バックアップ |

### 4.2 GitHub セットアップ手順

GitHub に push するための一般的な手順を以下に示す。詳細は GitHub 公式ドキュメントを参照すること。

1. GitHub に private repo を作成（例: `apokamo/kaji`）
2. SSH 鍵を GitHub アカウントに登録
3. 既存 kaji repo に GitHub remote を追加:
   ```bash
   git remote add origin git@github.com:<user>/<repo>.git
   ```
4. 初回 push:
   ```bash
   git push -u origin main
   ```
5. 以降は通常運用で `git push origin main`

### 4.3 採用判断の基準

| 観点 | 評価ポイント |
|------|-------------|
| 容量 | repo + LFS 想定容量、無償枠の上限 |
| 認証 | SSH / HTTPS / 2FA / SAML 等 |
| 可用性 | SLA、地域別レイテンシ、ダウンタイム傾向 |
| 価格 | 無償枠の制限、有償プラン費用 |
| 移行コスト | repo 移行、Issue 転記、CI 設定 |

## 5. 将来 forge への移行

> GitHub を primary forge として採用する場合のセットアップ / 認証 /
> `provider.type='github'` 起動手順は
> [GitHub Mode CLI Guide](../cli-guides/github-mode.md) を参照。本節は
> 移行判断材料に focus する。

### 5.1 移行可能性の維持

- local-mode の Issue は `local-<m>-<n>` で SoT として保持される
- forge に移行する場合、local Issue を **手動転記** する必要がある（自動転記は
  §残課題）
- 検証期間中の commit / branch は git remote で保持されているため、forge 切替
  後も履歴は失われない

### 5.2 移行時の判断材料

- 過去 Issue の扱い（転記 / 凍結のまま / 部分転記）
- 移行時に必要な kaji 実装は `draft/design/local-mode/design.md` § 残課題 を
  参照。`kaji sync from-github` / PR context 注入 等は実装済み

### 5.3 移行のチェックリスト

```
[ ] forge 採用先を決定
[ ] 採用先で repo 作成、git remote 設定
[ ] 過去 local Issue の扱いを決定（転記対象を選別）
[ ] design.md / runbook v2 を新方針で書き直し
[ ] 必要な kaji 実装を §残課題 から起票
[ ] 検証期間終了の宣言（CHANGELOG に記録）
```

## 6. トラブルシューティング

### 6.1 「provider.type が解決できない」エラー

```
ERROR: provider.type is not configured.
Add the following to .kaji/config.toml:

  [provider]
  type = "local"
```

`.kaji/config.toml` に `[provider]` セクションが無い、または `type` が
不正な値。Phase 3-e 以降は legacy passthrough を廃止したため、必ず
`type = "local"` を明示する必要がある。

### 6.2 machine_id 衝突

同じ `machine_id` を 2 PC で使うと `local-<machine>-<n>` の番号空間が重複
する。発生時の対処：

1. 重複した dir をどちらか片方の PC で `git mv` で改名（例:
   `local-pc1-3-foo` → `local-pc1-99-foo`）
2. `.kaji/config.local.toml` の `machine_id` を再設定し直す（既存 dir の
   `machine` 部は手動で改名する必要がある）
3. counter ファイルを必要なら手動で再採番

### 6.3 counter / dir 不整合

`make clean` 等で `.kaji/counters/` を消した場合、次回 `kaji issue create` で
`next_local_id()` が `.kaji/issues/local-<machine>-*` の最大値を見て自動
補正する。手動対処は不要。

### 6.4 worktree 削除失敗

`/issue-close` で worktree 削除に失敗した場合、Issue 状態は closed として
確定する（cleanup 失敗時も Issue 状態は確定する設計）。手動 cleanup：

```bash
git worktree list   # 残存 worktree 確認
git worktree remove <path>
git branch -d <branch>
```

## 7. 参照

- 設計書: `draft/design/local-mode/design.md`（特に § 残課題 / § 履歴）
- Phase 5 設計書: `draft/design/local-mode/phase5-design.md`
- CLI Guide: `docs/cli-guides/local-mode.md`
- Workflow Guide: `docs/dev/workflow_guide.md`
- Workflow Authoring: `docs/dev/workflow-authoring.md`
- Skill Authoring: `docs/dev/skill-authoring.md`
