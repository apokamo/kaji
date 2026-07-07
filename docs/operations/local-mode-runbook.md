# Local Mode 運用 Runbook（GitHub 障害時・緊急時 fallback）

通常運用は GitHub provider（`.kaji/wf/dev.yaml` / `dev-thorough.yaml` / `docs.yaml`）で
行い、GitHub 障害・不通時の緊急 fallback として kaji local-mode へ切り替えるための実用 runbook。
複数 PC・コード同期戦略・forge 復帰判断までを 1 ファイルで提供する。

## 1. このドキュメントの位置づけ

- **通常運用**: GitHub provider が SoT。Issue / PR / review は GitHub 上で回す
  （`.kaji/wf/dev.yaml` / `dev-thorough.yaml` / `docs.yaml`）。
- **本 runbook の対象**: GitHub 障害・不通・レート制限などで GitHub 運用を継続できない
  ときに、local-mode へ一時退避して開発を止めないための **緊急時 fallback 手順**。
- **復帰**: GitHub が復旧したら通常運用（GitHub）へ戻す。fallback 中に蓄積した
  local Issue / commit を GitHub 側へどう反映するかは user が判断する。

参照: `draft/design/local-mode/design.md` § 概要 / § 検証戦略の前提 / § 残課題。

## 2. セットアップ

### 2.1 単一 PC セットアップ

リポジトリ root で overlay を生成する。**tracked `.kaji/config.toml`
（`type = "github"`）は書き換えない** — 個人環境の provider 切替が repo 全体に
commit されるのを防ぐため、切替は gitignored overlay で行う:

```bash
kaji local init
```

- `.kaji/config.local.toml`（gitignored）に `[provider] type = "local"` と
  `machine_id` / `default_branch` が書き込まれる
- `machine_id` は `[a-z0-9]{1,16}`（ハイフン禁止）。明示する場合は
  `--machine-id pc1` 等を渡す
- `.gitignore` の `.kaji/config.local.toml` 行は `kaji local init` が追記する
  （`.kaji/counters/` 行は手動で確認する）
- config 不備があれば `kaji issue` / `kaji pr` / `kaji run` が exit 2 で停止し、
  エラーメッセージが修正内容を案内する

生成される overlay の内容・machine_id の解決順は
[Local Mode CLI Guide](../cli-guides/local-mode.md) § 2 を参照。

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

緊急時 fallback 中の Issue は **type ラベル** に応じて以下の local workflow を使い分ける：

| type | workflow YAML | 使用 Skill 系列 |
|------|--------------|----------------|
| type:feature | `dev-local.yaml` | issue-design / issue-implement / issue-review-* / issue-close |
| type:docs | `docs-local.yaml` | i-doc-update / i-doc-review / i-doc-fix / i-doc-verify / i-doc-final-check / issue-close |
| github 用 (`dev.yaml` 等) | — | **GitHub 障害・不通時は使用しない**（forge 通信を伴うため。復旧後の通常運用で使う） |

呼び出し例:

```bash
# 事前手動実行
/issue-create   # Issue 起票 (Skill)
/issue-start    # worktree 作成 (Skill)

# 自動連続実行（kaji run はファイルパス必須。basename 探索はしない）
kaji run .kaji/wf/dev-local.yaml local-pc1-1
# または
kaji run .kaji/wf/docs-local.yaml   local-pc1-2
```

### 3.1a docs-only Issue の手動運用（`kaji run` を使わない場合）

`docs-local.yaml` を使わず、Skill 単位で手動実行する代替手順：

1. `/i-doc-update [issue_id]`
2. `/i-doc-review [issue_id]`
3. RETRY なら `/i-doc-fix [issue_id]` → `/i-doc-verify [issue_id]` を収束まで繰り返す
4. `/i-doc-final-check [issue_id]`
5. `/issue-close [issue_id]`

> `/i-pr` は **使用しない**。local (bare) provider は PR 概念を持たないため、
> `kaji pr create` は bare-provider ガードで exit 2 となる
> （[Local Mode CLI Guide](../cli-guides/local-mode.md) § 8 参照）。

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

## 4. コード同期

fallback 中も git 自体は通常どおり使える。コード同期は従来どおり
`git push origin main`（GitHub への push まで不通の場合は、復旧後に push する）。
LAN bare repo 等の代替 remote が必要になるほどの長期障害は本 runbook の
スコープ外として別途判断する。

## 5. GitHub 運用への復帰

GitHub が復旧したら通常運用へ戻す。GitHub provider のセットアップ / 認証は
[GitHub Mode CLI Guide](../cli-guides/github-mode.md) を参照。

1. `.kaji/config.local.toml` の overlay を削除する（または `[provider]` の
   `type = "github"` へ書き換える）
2. `kaji config provider-type` が `github` を返すことを確認する
3. fallback 中に蓄積した local Issue（`local-<m>-<n>`）の扱いを決める:
   GitHub Issue へ手動転記して local 側を close する（自動転記は無い）か、
   local Issue のまま `dev-local.yaml` / `docs-local.yaml` で完結させる
4. fallback 期間中の commit / branch は git remote に保持されているため、
   復帰後も履歴は失われない
5. GitHub Issue の read-only 参照が必要な場合は `kaji sync from-github` で
   cache を更新する（`gh:N` 参照、[Local Mode CLI Guide](../cli-guides/local-mode.md) § 10）

## 6. トラブルシューティング

### 6.1 「provider.type が解決できない」エラー

- `[provider] section is required in .kaji/config.toml.` — tracked / overlay の
  いずれにも `[provider]` セクションが無い。local fallback へ切り替えるなら
  `kaji local init` で overlay を生成する
- `Error loading <path>: provider.type is required (string)` — `[provider]`
  セクションはあるが `type` が無い / 不正な値。overlay で切り替えている場合は
  `.kaji/config.local.toml` 側の `[provider]` ブロックを確認する

legacy passthrough（config 無しで `gh` へ素通り）は存在しないため、
`type` は必ず明示する必要がある。

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
