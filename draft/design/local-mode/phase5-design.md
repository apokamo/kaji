---
status: draft
phase: 5
parent: design.md
created: 2026-05-08
revised: 2026-05-08 (review v1: MF1-6 / SF1-4 受け入れ; review v2: RV2-1 / RV2-2 受け入れ; review v3: RV3 軽量化 4 件受け入れ)
predecessor: phase4-design.md
---

# [設計] kaji local mode — Phase 5 (検証期間運用整備)

## レビュー反映ログ

### 2026-05-08 review v3（軽量化推奨 → 受け入れ）

| # | 区分 | 指摘 | 対応 |
|---|------|------|------|
| RV3-1 | 軽量化推奨 | §履歴 に旧 §BCP フロー全文 (110 行) を移設するのは将来 design.md を肥大化させる。要約 + 参照先で十分 | §履歴 を 10 行程度の要約版に変更。旧 §BCP フロー / §概要 / §検証戦略 の本文は git 履歴で参照可能と明記 |
| RV3-2 | 軽量化推奨 | runbook 想定ボリュームが大きすぎる (600-900 行)。検証期間 v1 は実用手順中心で短く | total 200-400 行を目安に軽量化。比較は簡潔、設計判断の解説は最小限。検証期間後 v2 で書き直す前提 |
| RV3-3 | 軽量化推奨 | 新 YAML 名 `feature-development-docs-local.yaml` は既存命名 (`docs_maintenance_workflow.md` / `i-doc-*` Skill) と整合しない。`docs-maintenance-local.yaml` の方が責務に合致 | 全 31 箇所を `docs-maintenance-local.yaml` に置換 |
| RV3-4 | 軽量化推奨 | 14 commit 分割は docs 中心 Phase としては運用コストが高い。5-7 commit に集約 | 6 commit に集約（design / YAML / runbook / 既存 docs Skill コード integration / release）。受け入れ条件 / 完了条件 / ロールバック方針 / 影響ドキュメント / 工数見積の commit 番号も新番号に揃える |

### 2026-05-08 review v2（Changes Requested → 受け入れ）

| # | 区分 | 指摘 | 対応 |
|---|------|------|------|
| RV2-1 | 要再修正 | `docs/dev/docs_maintenance_workflow.md` の扱いが in-scope / 変更対象ファイル / commit 10 / 影響ドキュメント / 受け入れ条件で「同梱確定」と「軽微更新検討」が混在 | すべての箇所を **Phase 5 同梱確定**（Q31）に揃える。commit 10 内で github / local 両対応を明記し代替手順を追記 |
| RV2-2 | 要再修正 | `docs-maintenance-local.yaml` の YAML 骨格が現行 parser スキーマ（`cycles` + `on:`）に不整合。`when:` キーは存在しない | 既存 `feature-development-local.yaml` を読み取って完全準拠の骨格に修正 (Q32)。`cycles.doc-review` + 各 step の `on: PASS/RETRY/BACK/ABORT` を採用。`inject_verdict: true` / `resume: doc-update` も既存パターン踏襲 |

### 2026-05-08 review v1（Changes Requested → 受け入れ）

| # | 区分 | 指摘 | 対応 |
|---|------|------|------|
| MF1 | Must Fix | Skill 内 (`pr-fix:44` / `pr-verify:49` / `i-pr:52`) に Phase 5 言及残存 | in-scope に Skill 3 ファイル追加 (commit 11)。grep 受け入れ条件に `.claude/skills/` 追加 |
| MF2 | Must Fix | 検証期間中の docs-only workflow が `/i-pr` で詰む | **案 C**: `docs-maintenance-local.yaml` 新規 (軽量化後 RV3-4 で commit 3) + runbook §3.1a 手動運用フロー併記 |
| MF3 | Must Fix | `kaji issue view gh:N` cache reader は既実装、残課題は cache 自動 populate のみ | §残課題 / LocalProvider 文言修正で表現を分離（cache reader 既存契約維持） |
| MF4 | Must Fix | `make verify-docs` は draft/ 非対象 | **draft/ は POC 領域のため verify 対象外**（user 判断 Q22）。受け入れ条件から「設計書リンク健全性自動検証」を削除、手動レビューに変更 |
| MF5 | Must Fix | 新 runbook が `docs/README.md` Operations 索引から孤児 | 影響ドキュメントに `docs/README.md` 追加、commit 8 で索引行追加 |
| MF6 | Must Fix | 47e9f69 が baseline `a0269ee` から見えない | **マージ確認済**: main HEAD は `7a51f52 Merge branch 'chore/gitlab-mirror-setup'`。ただし 47e9f69 (`draft/lab/gitlab/setup-log.md`) は **ただのセッション作業記録**であり Phase 5 では取り込まない（user 判断 Q23 改）。runbook §4 は 47e9f69 に依存せず一般的な手順として独立執筆する |
| SF1 | Should Fix | 「Only docs and in-source comments」が不正確 | CHANGELOG Notes 文面を「docs / in-source comments / user-facing error messages / Skill markdown / new workflow YAML」へ拡張 |
| SF2 | Should Fix | `refactor(local)` より `docs(local)` が自然 | 実装順序 commit 12 の type を `docs(local)` に変更 |
| SF3 | Should Fix | `docs/cli-guides/local-mode.md` L130 / L156 周辺の cache 前提記述も新方針に合わせる | 修正対象を L188 単独 → L130 / L156 / L188 の 3 箇所統合修正に拡大 |
| SF4 | Should Fix | 「runbook §1 執筆時に user 確認」を実装前に決める | **案 A**: forge 採用先（gitlab 本格採用 / github 復帰 / 他）が確定した時点で検証期間終了。期間 KPI は設定しない。runbook §1 で固定記述 |

`make check` / `make test-large-local` / `kaji validate` は本レビュー反映後の
実装段階で緑であることを確認する（設計書段階では code 変更なし）。

## 方針転換のサマリ

Phase 5 着手前の事前確認で、local-mode の位置づけが当初設計から変わった。
本 Phase はこの方針転換を**設計書・ドキュメント・コード comment** に反映し、
**local-mode を検証期間中の SoT として安定運用するための docs 整備**に
スコープを絞る。新規 CLI / Skill 追加は行わない。

| 項目 | 当初設計（Phase 1-4） | 新方針（Phase 5 以降） |
|------|----------------------|----------------------|
| local-mode の位置づけ | GitHub 停止時の **BCP**（一時退避） | **検証期間中の SoT**（一定期間の本運用） |
| GitHub 復旧見込み | 復旧待ち（時期未定） | **復旧前提を放棄**。今後の forge は gitlab か github を別途検討 |
| `github = SoT` 前提 | 通常時は github が SoT、local は cache 経由で read-only | 検証期間中は **local が SoT**。forge は git remote としてのみ使用（現状 gitlab） |
| `kaji sync` 系の優先度 | Phase 5 で実装、復旧後に実通信検証 | **残課題に降格**。forge 移行先が確定するまで実装しない |
| `.kaji/cache/` の必要性 | GitHub snapshot を保持 | **不要**（検証期間中は GitHub 通信を行わない） |
| BCP runbook | GitHub 停止からの復旧手順 | **検証期間運用 runbook**。将来 forge 移行時の判断基準を含む |
| Issue 番号空間 | github = SoT、local = `local-<m>-<n>` 補助 | local = `local-<m>-<n>` 主、forge 移行時に転記計画 |

ソース管理は当面 gitlab に push（バックアップ目的、急ぎ採用）。gitlab の本格採用判断は別途。
本 Phase は gitlab を**現状の選択**として記述するに留め、推奨明示はしない。

## 概要

Phase 4 で 3 層ガード（CLI / Workflow / Skill）が完成し、`provider.type='local'`
配下での forge 機能誤起動は構造的に防がれた。Phase 5 は次の 3 軸で
**方針転換を docs に反映**する。

| 軸 | 作業 | 対象 |
|----|------|------|
| 1 | 設計書本体の全面再構成 | `draft/design/local-mode/design.md` |
| 2 | 検証期間運用 runbook の新規作成 | `docs/operations/local-mode-runbook.md` |
| 3 | 既存 docs / コード comment の整合更新 | `docs/cli-guides/local-mode.md`, `docs/dev/workflow_guide.md`, `CHANGELOG.md`, `kaji_harness/providers/local.py` |

新規機能の実装は **docs-only 用 local workflow YAML 1 件**（`.kaji/wf/docs-maintenance-local.yaml`、後述 § 方針決定 6）に限る。コード変更は **`LocalProvider` の Phase 5 言及 2 箇所**と **`.claude/skills/{pr-fix,pr-verify,i-pr}/SKILL.md` の Phase 5 言及 3 箇所** の文言修正のみ。残りは設計書再構成 + runbook 作成 + 整合更新で構成される。

> **前提**: 本設計は `chore/gitlab-mirror-setup` が `main` にマージ済み (`7a51f52`) を baseline とする。
> `feat/local-phase4` (`a0269ee`) も同 main 上にマージ済。Phase 5 着手前の preflight で確認済み。
> なお 47e9f69 が追加した `draft/lab/gitlab/setup-log.md` は **GitLab ミラー初期化セッションの
> 作業記録** にすぎないため、Phase 5 のいかなる成果物にも取り込まない（user 判断、後述 Q23 改）。

## 背景・目的

### Phase 4 完了時点の状態

- 3 層ガード（CLI bare-provider error / Workflow `requires_provider` 整合検証 / Skill Step 0 provider check）が完成
- `kaji pr ...` は `provider.type='local'` で exit 2 + 代替手順を案内
- `Workflow.requires_provider: Literal["github", "local", "any"]` でワークフロー × provider 整合を fail-fast
- `prompt.build_prompt(..., issue_context: IssueContext)` の Optional 解除完了
- `kaji config provider-type` (read-only) を Skill 手動実行向けに新設済
- 残された Phase 5 申し送り: `kaji sync from-github` 等の cache 系 / `--add-frontmatter` / PR context 注入

### Phase 4 完了後に user 方針が変化

- 2026-05-08 時点で **GitHub アカウント復旧の見込みなし**と判断
- 当面 (期間未定) は **local-mode を SoT とする検証運用** へ移行
- 検証期間後の運用先は gitlab か github を別途検討（gitlab は現状バックアップ目的で push 中）
- 「GitHub 復旧時の cache 同期 / 転記支援」を前提とする機能群は **本 Phase ではすべて実装せず、forge 移行先が決まった時点で再評価**

### Phase 5 が解く問題

| 問題 | 現状 | Phase 5 後 |
|------|------|-----------|
| 設計書 (`design.md`) が「github = SoT」を恒久前提として書かれており、user の現実運用と乖離 | github 復旧前提の記述が散在（§概要 / §検証戦略 / §BCP フロー） | 「検証期間中は local が SoT」を前提とする全面再構成。旧記述は §履歴 として保持 |
| 検証期間中の運用 runbook が存在しない（新規 PC を加える / conflict を解く / forge 移行を判断する手順が docs にまとまっていない） | docs/operations/ 配下に local-mode runbook なし | `docs/operations/local-mode-runbook.md` を新規作成。`docs/README.md` Operations 索引にも追加 |
| コード comment / Skill / docs に「Phase 5 で実装予定」の文言が残り、user / AI が「すぐ実装される」と誤読する余地 | `LocalProvider.py:712,723`、`.claude/skills/pr-fix/SKILL.md:44`、`pr-verify/SKILL.md:49`、`i-pr/SKILL.md:52`、`docs/cli-guides/local-mode.md:188` に Phase 5 言及 | 全箇所を「残課題として別途追跡。forge 移行時に再評価」へ書き換え |
| 検証期間中に **docs-only workflow が動かない** | `feature-development-local.yaml` は `type:feature` 系のみ。`type:docs` の Issue は既存 `docs_maintenance_workflow.md` のフロー上 `/i-pr` に進むため、Phase 4 の bare-provider ガードで詰む | `.kaji/wf/docs-maintenance-local.yaml` を新規追加（i-doc-* 系 + `/issue-close` 終端）。runbook §3 にも手動運用フロー（`kaji run` を使わない場合の手順）を併記 |
| `kaji sync` 系 / `--add-frontmatter` 等の後送り項目が散在管理（design.md オープン論点 / Phase 4 申し送り / 受け入れ条件未完了 等） | 複数箇所に分散 | design.md 内 §残課題 章に集約。後で local Issue 化 (`local-<m>-N`) する |

### Phase 5 が解かない問題（残課題リストに集約）

以下は Phase 5 で実装せず、§残課題 に集約して forge 移行時 / 必要時に再評価する：

- `kaji sync from-github` / `kaji sync status` / `kaji sync local-to-github-plan`（**cache 自動 populate**）
- `.kaji/cache/` の自動初期化（注: cache reader 自体は既実装、`view_cached_issue()` 経由で manual JSON 投入運用は動作中）
- `kaji issue list` の local + cache 統合表示
- `kaji issue edit --add-frontmatter`
- PR context 注入 (`GitHubProvider.resolve_pr_context()` + `pr_id` / `pr_ref` の prompt 自動注入)
- forge 移行時の Issue 一括転記支援（local → gitlab/github）
- gitlab 本格採用時の `GitLabProvider` 実装

> **既存契約として維持される機能**: `kaji issue view gh:N` の cache reader（`LocalProvider.view_cached_issue()` / `cli_main.py:remote_cache` 分岐 / `tests/test_phase3c_dispatcher.py:329-365`）は **Phase 3-c で実装済みのため引き続き利用可能**。残課題は cache を自動 populate する `kaji sync from-github` のみ。検証期間中に手動で `.kaji/cache/issues/<n>.json` を投入する運用は技術的には可能だが、forge 通信を行わない方針 (Q3) のため非推奨。

## スコープ

### in-scope

- `draft/design/local-mode/design.md` — **全面再構成**（後述 § 方針決定 1）
  - §概要 / §検証戦略の前提 / §BCP フロー の書き換え
  - 旧記述を §履歴 として保持（Q11 反映）
  - Phase 表 (L1488) の Phase 5 行を新スコープで書き換え
  - 受け入れ条件から GitHub 系を残課題に切り出し
  - §スコープ外 を新方針反映（GitHub 復旧前提機能を恒久 out-of-scope に）
  - **§残課題 章を新規追加**（Q18 案 B）
- `docs/operations/local-mode-runbook.md` — **新規作成（実用 v1、200-400 行軽量版）**（後述 § 方針決定 2）
- `docs/README.md` — Operations 索引 (L47-50) に新 runbook 行を追加
- `docs/cli-guides/local-mode.md` — Phase 5 言及の文言修正（L130 / L156 / L188 の cache 関連記述を含む）と新方針への追記
- `docs/dev/workflow_guide.md` — local-mode 主体運用への記述追加（default 化はしない）。`docs-maintenance-local.yaml` の存在も追記
- `docs/dev/docs_maintenance_workflow.md` — **Phase 5 同梱確定**（user 判断 Q31）。L31 `/i-pr` 言及に対し「github / local 両対応」を明記し、local の場合は `docs-maintenance-local.yaml` または手動運用 (runbook §3.1a) を使う旨を追記
- `CHANGELOG.md` — Unreleased に Phase 5 entry 追加（方針転換の記録）
- `kaji_harness/providers/local.py:712,723` — Phase 5 言及 comment / **user-facing error message** の文言修正
- `.claude/skills/pr-fix/SKILL.md:44` — Phase 5 言及の文言修正（「Phase 5 で GitHubProvider が解決して prompt 注入する予定」→「forge 採用先確定時に再評価」相当）
- `.claude/skills/pr-verify/SKILL.md:49` — 同上
- `.claude/skills/i-pr/SKILL.md:52` — 同上
- `.kaji/wf/docs-maintenance-local.yaml` — **新規作成**（後述 § 方針決定 6）。docs-only workflow を local provider 配下で完走可能にする

### out-of-scope

- 新規 CLI 追加（`kaji sync` / `--add-frontmatter` 等は §残課題）
- 新規 Skill 追加 / 既存 Skill の logic 変更（Skill markdown の文言修正のみは in-scope）
- 既存 Workflow YAML の変更（`docs-maintenance-local.yaml` の **新規追加** は in-scope。既存 5 YAML への変更はしない）
- `LocalProvider` / `GitHubProvider` の logic 変更（comment / user-facing error message の文言修正のみ）
- `IssueProvider` Protocol の API 変更
- gitlab 本格採用の意思決定（runbook では「現状の選択」とのみ記述）
- `GitLabProvider` 実装（残課題）
- `.kaji/cache/` の物理ディレクトリ作成（不要）。ただし **既存の `view_cached_issue()` 経路は変更せず**、cache reader 契約は維持する
- 既存 Phase 1-4 の implementation report 修正（履歴として現状維持）
- local issue を作成して残課題を Issue 化する作業（**Phase 5 完了後に手動で実施**、本 Phase は §残課題 への列挙まで）
- `draft/design/` 配下のリンク健全性自動検証（user 判断 Q22: draft/ は POC 用領域のため verify 対象外）

## 方針決定

### 1. `design.md` の全面再構成（Q11 採用案）

**書き換え方針**: 新方針 (`local = 検証期間中の SoT`) を本文の正本とする。
旧記述 (`github = SoT, local = BCP`) は **§履歴 章** として末尾近くに保持し、
設計判断の経緯を追跡可能にする。Q11「全面再構成 + 必要があれば旧章を残す」+ RV3-1
（軽量化）を踏まえ、**§履歴 は要約のみ（10 行程度）に留め、旧 §BCP フロー / §概要 /
§検証戦略 の本文は git 履歴で参照可能とする**（全文移設はしない）。

#### 1.1 章構成の差分

| 章番号 | 現行 design.md | 再構成後 |
|--------|---------------|---------|
| 1 | Primary Sources | Primary Sources（更新: 2026-05-08 方針転換を追記） |
| 2 | 概要 | **概要（書き換え）**: local = 検証期間中の SoT。forge 移行は別途検討 |
| 3 | 検証戦略の前提（buildout 期間中の temporal inversion） | **検証戦略の前提（書き換え）**: 検証期間中は実 forge 通信を行わない。`[buildout-ok]` / `[forge-required]` タグの扱いを「forge 移行時に再評価」へ変更 |
| 4 | 運用前提 | 運用前提（更新: gitlab を「現状の選択」として記述） |
| 5 | 背景・目的 | **背景・目的（更新）**: 「ユーザーストーリー」は維持、「設計判断のサマリ」に方針転換行を追加 |
| 6 | インターフェース | インターフェース（軽微更新: `kaji sync` 行に「残課題」マークを付与） |
| 7 | 詳細設計 | 詳細設計（基本維持。`gh:N` cache 参照の節に「残課題」マークを付与） |
| 8 | EPIC orchestration との将来連携 | EPIC orchestration との将来連携（変更なし） |
| 9 | 移行・互換性 | 移行・互換性（更新: 「forge 移行時の判断基準」を追加） |
| 10 | スコープ外 | **スコープ外（更新）**: GitHub 復旧前提機能を「現在のスコープ外、forge 移行時に再評価」へ |
| 11 | 受け入れ条件 | **受け入れ条件（更新）**: GitHub 系項目を §残課題 に切り出し、`[forge-required]` タグは「forge 移行時に再評価」へ降格 |
| 12 | テスト戦略 | テスト戦略（更新: Large-forge は forge 移行時の追跡項目へ降格） |
| 13 | 実績と残スコープ（Phase 表） | **実績と残スコープ（書き換え）**: Phase 5 行を新スコープで書き直し。Phase 6 以降は forge 移行時に検討 |
| 14 | オープンな論点 | オープンな論点（更新: 不要になった項目を削除、§残課題 へ移動） |
| 15 (新) | — | **§残課題（新規追加）**: 後送り全項目を集約 |
| 16 (新) | — | **§履歴（新規追加）**: 方針転換の経緯と参照先（要約のみ、10 行程度）。旧 BCP フロー / 旧 §概要 / 旧 §検証戦略 の全文は **git 履歴で参照可能**なため移設しない（軽量化） |

#### 1.2 §概要（新本文ドラフト）

```markdown
## 概要

kaji を **GitHub に依存せずローカルファイルだけで全 workflow を完結**できる
ようにする。`kaji_harness/providers/` に provider 抽象層を導入し、
`provider: local | github` を config で切り替え可能にする。Skill からの
`gh` 直接呼び出しを廃し、`kaji` CLI の薄いラッパー (`kaji issue ...` /
`kaji pr ...`) 経由に統一する。Issue は `.kaji/issues/local-<machine>-<n>-<slug>/issue.md`
（directory-per-issue、frontmatter + body）として表現する。

### 当面の運用方針（2026-05-08 確定）

GitHub アカウント復旧の見込みが立たないため、**当面 (期間未定) は local-mode を
SoT として運用する**。検証期間中は：

- 全 Issue は `local-<machine>-<n>` として local mode で管理
- ソース管理は git remote に push（現状 gitlab、急ぎバックアップ目的で採用）
- forge 機能（PR / inline review）は使用しない。code review は
  `/issue-review-design` / `/issue-review-code` Skill で代替

検証期間後の運用先（gitlab 本格採用 / github 復帰 / 他 forge）は別途判断する。
`local-mode を恒久 SoT とする決定はしていない` 点に注意。本設計は
**検証期間中の安定運用** と **forge 移行時の判断材料** を提供することを目的とする。

### Phase 5 の位置づけ

Phase 1-4 で local-mode の機能実装は完了している。Phase 5 は **方針転換の docs
反映**を行う Phase であり、新規機能の追加はしない。
（後述 § 実績と残スコープ 参照）
```

#### 1.3 §残課題（新規追加章のドラフト）

```markdown
## 残課題

以下は Phase 5 着手前の方針転換により後送りとなった項目。**forge 移行先が
確定した時点 / 必要が生じた時点で再評価**し、必要なものから順次実装する。
本リストは設計書内に集約しておき、Phase 5 完了後に local Issue (`local-<m>-N`)
化して個別追跡する。

### forge 連携機能（forge 移行先確定後に再設計）

- `kaji sync from-github` / `kaji sync status` / `kaji sync local-to-github-plan`（cache 自動 populate）
- `.kaji/cache/` の自動初期化と atomic write（atomic rename 方針は当初設計を維持）
- `kaji issue list` の local + cache 統合表示（cache 不在の現状では local 単体表示で運用）
- forge 移行時の Issue 一括転記支援（local → gitlab/github）

> 注: `kaji issue view gh:N` の cache reader 経路（`view_cached_issue()`）は Phase 3-c で実装済。残課題は cache 自動 populate のみ。

### docs-only workflow 関連（Phase 5 の MF2 派生スコープ拡張で対応済み）

- 検証期間中に `docs-maintenance-local.yaml` を新規作成し、docs-only Issue を local provider 配下で完走可能にする（本 Phase の方針決定 6 で対応）
- 既存 `docs_maintenance_workflow.md` の文言は「github / local 両対応」に整合更新する

### local-mode 機能拡張

- `kaji issue edit --add-frontmatter KEY=VALUE`（reserved key 保護 + 文法検証）
  - 当初用途は `migrated_to=<gh-number>` だったが、forge 移行が後送りになったため
    優先度は低下。user 拡張 metadata 機構として将来必要になった時点で実装
- `kaji issue list` の filter / sort 強化（state / label / assignee / created_at）

### Phase 4 申し送り

- PR context 注入: `GitHubProvider.resolve_pr_context(branch_name)` + `pr_id` / `pr_ref` の prompt.py 自動注入
  - 現行 Skill は `kaji pr list --search` で取得する暫定運用
  - forge 移行先確定後に GitHubProvider / GitLabProvider のいずれで実装するか判断

### 新規 forge provider

- `GitLabProvider` 実装（gitlab 本格採用が決まった場合）
- `requires_provider` enum の `gitlab` 追加 / Workflow YAML スキーマ拡張
```

#### 1.4 §履歴（新規追加章のドラフト）

```markdown
## 履歴

### 2026-05-08 方針転換: GitHub 復旧前提の放棄

Phase 4 完了時点で GitHub アカウント復旧の見込みが立たず、user 判断により
local-mode を **検証期間中の SoT** として運用する方針に転換した。本転換に伴い、
当初設計の以下の章は履歴として保持するが、**現行の運用前提ではない**点に注意：

- 旧 §BCP フロー（GitHub 停止検知 → local 切替 → 復旧時 sync）
- 旧 §概要 の「github = SoT、local = BCP」記述
- 旧 §検証戦略の前提 の「buildout 期間中の temporal inversion」（時限的記述）

これらは「将来 forge を再採用する時の参考設計」として残す。実際の forge 移行時は、
**移行先（gitlab か github か他）に応じた再設計が必要**。

#### 旧 §BCP フローの参照方法

旧設計（github = SoT、local = BCP）の本文は git 履歴で参照可能：

```bash
git show <Phase 4 マージ前 commit>:draft/design/local-mode/design.md | sed -n '1038,1147p'
```

旧 §概要 / §検証戦略の前提 / §BCP フロー はそれぞれ design.md の旧 L28-50 /
L52-72 / L1038-1147 にあった。設計判断の経緯は本 §履歴 と Phase 1-4 の
implementation report、および phase5-design.md（本ファイル）で追跡可能。
```

### 2. `docs/operations/local-mode-runbook.md` の新規作成

#### 2.1 章構成

```markdown
# Local Mode 検証期間運用 Runbook

## 1. このドキュメントの位置づけ
- 検証期間の開始: 2026-05-08（Phase 5 着手時点）
- **検証期間の終了基準**: forge 採用先（gitlab 本格採用 / github 復帰 / 他）が確定した時点（SF4 案 A）
  - 期間 (KPI) の数値設定はしない。user の forge 採用判断に同期する
  - 終了時、本 runbook と `design.md` は forge 採用先に応じて再構成する
- 当初の BCP runbook ではなく、検証期間中の SoT 運用 runbook である旨

## 2. セットアップ
### 2.1 単一 PC セットアップ
- `.kaji/config.toml` / `.kaji/config.local.toml` の最小構成
- machine_id 命名規則（`[a-z0-9]{1,16}`）
- `.gitignore` 追加項目の確認

### 2.2 複数 PC セットアップ
- 各 PC で異なる machine_id を必ず設定
- counter ディレクトリは PC ごとに独立（gitignored）
- 既存 repo を別 PC に clone した場合のチェックリスト

## 3. 日常運用
### 3.1 Issue ライフサイクル（/issue-create → /issue-close）
- type:feature 系 workflow は `feature-development-local.yaml`
- type:docs 系 workflow は `docs-maintenance-local.yaml` (Phase 5 で新設)
- github 用 (`feature-development.yaml` / `feature-development-light.yaml` / `implement-to-pr.yaml`) は検証期間中は使用しない

### 3.1a docs-only Issue の手動運用（`kaji run` を使わない場合）
`docs-maintenance-local.yaml` を使わず、`/i-doc-update` 〜 `/issue-close` を Skill 単位で手動実行する代替手順:

1. `/i-doc-update [issue_id]`
2. `/i-doc-review [issue_id]`
3. (RETRY なら) `/i-doc-fix [issue_id]` → `/i-doc-verify [issue_id]` を収束まで繰り返す
4. `/i-doc-final-check [issue_id]`
5. `/issue-close [issue_id]` （`/i-pr` は **使用しない**。bare-provider error で停止する）

### 3.2 複数 PC 並行運用
- 各 PC は自分の `local-<machine>-<n>` 番号空間のみ採番
- pull / push の手順
- Issue / counter / config の git tracked 状態確認

### 3.3 Conflict 解決
- 同一 Issue を複数 PC で編集した場合（git の通常 merge conflict）
- counter の不整合（fresh clone / cleanup 後の `next_local_id()` 自動補正）
- duplicate issue dir 検出時の手動解決

## 4. コード同期戦略
**現状**: gitlab に push（バックアップ兼ねる、急ぎ採用）。本格採用は今後検討。

### 4.1 採用候補の比較
| 案 | 特徴 | 適用シナリオ |
|----|------|-------------|
| GitLab Cloud | 無償枠で private repo / branch 保護 | **現状の選択**（バックアップ目的） |
| Self-host (Gitea / Forgejo) | フルコントロール、保守必要 | 長期運用が固まった後の選択肢 |
| LAN bare repo (NAS) | オフライン可、PC 間限定 | 外部送信を避けたい場合 |
| Bundle ファイル USB 持ち回り | git remote 不要 | 単一 PC + 偶発的バックアップ |

### 4.2 GitLab セットアップ手順（現状の選択）
- 一般的な GitLab プロジェクト作成 → SSH 鍵登録 → remote 追加 → 初回 push の手順
- runbook の独立記述として完結させる（draft/lab/gitlab/setup-log.md は user の作業記録であり、本 runbook は参照しない）

### 4.3 採用判断の基準
- 検証期間中は gitlab 継続で十分
- 本格採用 / 切替の判断材料（容量 / 認証 / 可用性 / 価格）

## 5. 将来 forge への移行
### 5.1 移行可能性の維持
- local-mode の Issue は `local-<m>-<n>` で SoT として保持される
- forge に移行する場合、local Issue を forge Issue に **手動転記**する必要がある
- 自動転記は §残課題（`kaji sync local-to-github-plan` 等）として未実装

### 5.2 移行時の判断材料
- forge 採用先 (gitlab 本格採用 / github 復帰 / 他)
- 過去 Issue の扱い (転記 / 凍結のまま / 部分転記)
- 移行時に必要な kaji 実装（残課題リスト参照）

## 6. トラブルシューティング
### 6.1 「provider.type が解決できない」エラー
### 6.2 machine_id 衝突
### 6.3 counter / dir 不整合
### 6.4 worktree 削除失敗

## 7. 参照
- 設計書: `draft/design/local-mode/design.md`
- CLI Guide: `docs/cli-guides/local-mode.md`
- Workflow Guide: `docs/dev/workflow_guide.md`
```

#### 2.2 想定ボリューム（軽量化）

検証期間 v1 runbook は **実用手順中心**。total 200〜400 行を目安に短く保つ。
各章は「最低限必要な手順 + 注意点」のみ。設計判断の解説や複数案の比較記述は
最小限にする：

- §4 採用候補比較表は維持（4 案: GitLab Cloud / self-host / NAS bare / bundle）
  だが各案 1〜2 行に圧縮
- §6 トラブルシューティングは Phase 1-4 で実際に発生した issue の対処を
  簡潔に列挙（解説不要）
- 検証期間後の forge 採用先確定時に runbook v2 として書き直す前提なので、
  v1 は完璧を目指さない

#### 2.3 既存 docs との非重複

- `docs/cli-guides/local-mode.md` は **CLI 参照**（`kaji issue` 等の使い方）。runbook は **運用判断**（いつ / なぜ / どのように複数 PC を回すか）に集中
- `docs/dev/workflow_guide.md` は **workflow 設計**。runbook は **実運用**

### 3. 既存 docs / コード comment / Skill / user-facing error message の整合更新

#### 3.1 `docs/cli-guides/local-mode.md`

cache 関連記述を 3 箇所統合修正：

- **L130**（ID 文法表）: 「`gh:153` GitHub cache 由来の read-only 参照（`.kaji/cache/issues/153.json` 必要）」 → 「`gh:153` GitHub cache 由来の read-only 参照。**検証期間中は cache 自動 populate 未実装のため、必要時のみ手動で JSON 投入**」
- **L156**（ファイルレイアウト中の cache）: 「`└── cache/issues/<n>.json    (GitHub の read-only キャッシュ)`」 → 「`└── cache/issues/<n>.json    (GitHub の read-only キャッシュ。検証期間中は手動投入)`」
- **L188** (既知の制限): 「`kaji sync from-github` は Phase 5 で実装予定」→ **「`kaji sync from-github` は残課題（forge 採用先確定時に再評価）」** に書き換え
- 末尾に「§ 検証期間運用について」節を新設し、`docs/operations/local-mode-runbook.md` へのリンクを記載

#### 3.2 `docs/dev/workflow_guide.md`

- 現状 48 行と短い。local-mode workflow (`feature-development-local.yaml`) と **新設の `docs-maintenance-local.yaml`** が **当面の主 workflow** である旨を 1 段落で追記
- provider 切替の手順は既存の `docs/cli-guides/local-mode.md` を参照する形に統一

#### 3.2a `docs/README.md`

Operations 索引 (L47-50) に新 runbook 行を追加：

```markdown
| [Local Mode 検証期間運用 Runbook](operations/local-mode-runbook.md) | 検証期間中の local-mode SoT 運用、複数 PC、コード同期戦略、forge 移行判断 |
```

#### 3.3 `CHANGELOG.md`

`Unreleased` 配下に Phase 5 entry を追加：

```markdown
### Changed

- **Phase 5**: Repositioned local-mode from "BCP for GitHub outage"
  to "primary SoT during validation period". GitHub recovery is no longer
  a precondition for the project. See
  `draft/design/local-mode/design.md` (re-organized) and the new
  `docs/operations/local-mode-runbook.md` for the validation-period
  operating model.
- **Phase 5**: Updated `LocalProvider` error messages and CLI guide to
  reframe `kaji sync from-github` and related cache features as
  "remaining tasks (re-evaluated when forge migration target is
  decided)" rather than "to be implemented in Phase 5".

### Notes

- No public CLI / config changes in Phase 5. Updates are limited to
  docs, in-source comments / docstrings, user-facing error messages,
  Skill markdown wording, and one new workflow YAML
  (`.kaji/wf/docs-maintenance-local.yaml`) that mirrors
  `feature-development-local.yaml` for type:docs Issues.
```

#### 3.4 `kaji_harness/providers/local.py`

L712, L723 の Phase 5 言及を書き換え。**`view_cached_issue()` メソッド本体の logic は変更しない**（既存契約を維持）：

**L712 (docstring)**:
```python
# Before
Phase 5 の `kaji sync from-github` 未実装のため、buildout 中は user
が手動で `.kaji/cache/issues/<n>.json` を投入する想定（design.md L177-181）。

# After
cache 自動 populate (`kaji sync from-github`) は残課題（forge 採用先確定時に再評価、
`design.md` §残課題 参照）。2026-05-08 方針転換以降、検証期間中は forge 通信を行わない
方針のため、本メソッドが呼ばれるのは user が手動で JSON を投入した場合に限られる。
cache reader 自体は既存契約として維持される（Phase 3-c で実装、
`tests/test_phase3c_dispatcher.py:329-365` で検証済）。
```

**L723 (user-facing エラーメッセージ)**:
```python
# Before
f"Phase 5 'kaji sync from-github' will populate this; until then, "
f"populate it manually or avoid 'gh:' references."

# After
f"Cache population (`kaji sync from-github`) is a remaining task, "
f"re-evaluated when the forge migration target is decided. "
f"During the local-mode validation period, manual `gh:` references "
f"are not recommended (see docs/operations/local-mode-runbook.md). "
f"If the JSON is intentionally pre-populated, ensure the file path "
f"matches the cache layout."
```

### 4. 残課題の集約（Q18 案 B）

design.md の §残課題 章にすべての後送り項目を列挙する（前述 § 1.3）。
Phase 5 merge 後、user 判断で local Issue (`local-<m>-N`) 化する。Phase 5
本体では Issue 化作業は行わない（手動作業として明示的に分離）。

### 5. Skill markdown と user-facing error message の文言修正

`.claude/skills/{pr-fix,pr-verify,i-pr}/SKILL.md` の Phase 5 言及（`pr_id`
prompt 注入の予告）を統一フレーズへ書き換える。Step 1 の `kaji pr list --search`
ベースの取得手順自体は変更しない（forge 採用先が確定するまで現行運用を維持）。

**Before（pr-fix:44 / pr-verify:49 共通）**:

```
`pr_id` はハーネス経由では Phase 4 時点ではプロンプトに自動注入されない（Phase 5 で
GitHubProvider が解決して prompt 注入する予定）。
```

**After**:

```
`pr_id` はハーネス経由では現時点ではプロンプトに自動注入されない（forge 採用先確定時に
再評価、`draft/design/local-mode/design.md` §残課題 参照）。
```

i-pr:52 も同等の文体で修正する。

### 6. `docs-maintenance-local.yaml` の新規追加（MF2 案 C）

検証期間中の docs-only Issue を local provider 配下で完走可能にする。既存
`feature-development-local.yaml` (`/issue-design` → … → `/issue-close`) と
同じ構造で、step 系列のみ docs 用 Skill に置き換える。`/i-pr` step は持たない。

**期待される YAML 骨格** (実装時に既存 `feature-development-local.yaml` を base に作成。`cycles` + `on` 形式で既存 parser スキーマに完全準拠):

```yaml
name: docs-maintenance-local
description: |
  provider=local 用の docs-only 開発 workflow。
  type:docs Issue を local provider 配下で完走させる。
  issue-create / issue-start は事前に手動実行済みであることが前提。
  PR concept を持たないため、最終 step は issue-close
  （local merge + frontmatter 更新）。
execution_policy: auto
requires_provider: local

cycles:
  doc-review:
    entry: doc-review
    loop: [doc-fix, doc-verify]
    max_iterations: 3
    on_exhaust: ABORT

steps:
  - id: doc-update
    skill: i-doc-update
    agent: claude
    model: opus
    effort: medium
    on:
      PASS: doc-review
      ABORT: end

  - id: doc-review
    skill: i-doc-review
    agent: codex
    model: gpt-5.4
    effort: medium
    on:
      PASS: final-check
      RETRY: doc-fix
      ABORT: end

  - id: doc-fix
    skill: i-doc-fix
    agent: claude
    model: opus
    inject_verdict: true
    resume: doc-update
    on:
      PASS: doc-verify
      ABORT: end

  - id: doc-verify
    skill: i-doc-verify
    agent: codex
    model: gpt-5.4
    effort: medium
    on:
      PASS: final-check
      RETRY: doc-fix
      ABORT: end

  - id: final-check
    skill: i-doc-final-check
    agent: claude
    model: opus
    on:
      PASS: close
      RETRY: final-check
      ABORT: end

  - id: close
    skill: issue-close
    agent: claude
    model: sonnet
    on:
      PASS: end
      RETRY: close
      ABORT: end
```

**スキーマ準拠の根拠** (既存 `feature-development-local.yaml` を読み取って確認):

- top-level: `name` / `description` / `execution_policy` / `requires_provider` / `cycles` / `steps`
- `cycles.<name>`: `entry` (cycle 起点 step id) / `loop` (反復対象 step id 配列) / `max_iterations` (整数) / `on_exhaust` (`ABORT` 等)
- `steps[].on`: `PASS` / `RETRY` / `BACK` / `ABORT` の遷移先 step id（`end` は workflow 終端の予約 id）
- `inject_verdict: true` は fix 系で前 step の verdict を prompt 注入（`fix-code` パターンを踏襲）
- `resume: <step_id>` は fix → verify 完了後に再 dispatch する起点（`fix-design` の `resume: design` パターンを踏襲。doc-fix は doc-update を起点に再開する想定）

`when:` キーは現行スキーマに**存在しない**ため使用しない。step 遷移は `on:` のみで表現する。

**設計判断**:

- **新規 logic は持たない**: 既存 `feature-development-local.yaml` のスキーマと
  step 解決ロジックをそのまま使う。`requires_provider: local` も既存運用通り
- **既存 `docs_maintenance_workflow.md` は文言更新のみ**: 「github / local
  両対応」と明記し、local の場合は本 YAML を使う旨を追記
- **github 用 docs workflow は本 Phase では新設しない**: 検証期間中は github 経路を
  使わないため。forge 採用先決定時に必要なら追加
- 既存 fix/verify step の `inject_verdict` / `resume` 設定は `feature-development-local.yaml`
  の fix-design / fix-code パターンを踏襲（doc-fix は `inject_verdict: true` + `resume: doc-update`）

### 7. コード変更の最小化

本 Phase は **logic 変更ゼロ** を原則とする。修正は以下のみ：

- `LocalProvider` の docstring 1 箇所 + user-facing error message 1 箇所の文言修正
- Skill markdown の文言修正 (3 ファイル × 1 行ずつ)
- workflow YAML 1 ファイルの新規追加（既存スキーマに完全準拠）

これにより：

- 既存テスト (`tests/test_*.py`) の修正は **不要**
- `make check` / `make test-large-local` は文言修正 + 新 YAML 追加のみで pass する想定
- 新 YAML は `kaji validate` で構文検証
- rollback は文字列 revert + YAML ファイル削除で完結

## 詳細設計

### 5.1 design.md 再構成の作業手順

1. §概要 / §検証戦略の前提 を § 1.2 のドラフトベースで書き換え
2. §運用前提 / §背景・目的 / §設計判断のサマリ に方針転換行を追加
3. §インターフェース / §詳細設計 内の `kaji sync` / `gh:N` / `cache` 言及に「残課題」マークを付与
4. §スコープ外 を更新: 「GitHub 停止の自動検知＋自動切替」等の項目はそのまま維持。新規追加: 「forge 通信機能（sync / cache / PR context）は本 Phase ではすべて残課題」
5. §受け入れ条件 を整理: `[forge-required]` タグの 5 項目を §残課題 へ移動。`[buildout-ok]` タグは「検証期間中の必須項目」へ意味を更新
6. §実績と残スコープ (Phase 表) の Phase 5 行を新スコープで書き直し
7. §オープンな論点 を整理: forge 復旧前提のもの (cache サイズ管理 / sync 自動実行ポリシー 等) を §残課題 へ移動
8. §残課題 を新規追加（§ 1.3 のドラフト）
9. §履歴 を新規追加（§ 1.4 のドラフト、要約のみ。旧 BCP フロー本文の移設はしない、軽量化 RV3-1）

### 5.2 runbook 執筆の作業手順

1. 章立てを § 2.1 に従って markdown skeleton として配置
2. § 1 で検証期間の開始日 (2026-05-08) と終了基準（forge 採用先決定時、SF4 案 A）を固定記述
3. § 2 (セットアップ) を `docs/cli-guides/local-mode.md` の重複を避けながら執筆
4. § 3 (日常運用) は既存 Skill / workflow 名を実際に参照する手順形式。**§ 3.1 で `feature-development-local.yaml` と `docs-maintenance-local.yaml` の使い分けを明示**、§ 3.1a で docs-only の手動運用フローも併記
5. § 4 (コード同期戦略) は gitlab を実例として、一般的な GitLab セットアップ手順を runbook 独立記述として執筆（既存 commit 47e9f69 のセットアップログは user の作業記録のため取り込まない、Q23 改）
6. § 5 (forge 移行) は §残課題 (design.md) と相互参照
7. § 6 (トラブルシューティング) は Phase 1-4 の implementation report で実際に発生した issue を抜粋

### 5.3 既存 docs / コード comment / Skill の更新作業

- `docs/cli-guides/local-mode.md`: 機械的な文言修正 3 箇所 (L130 / L156 / L188) + runbook へのリンク section 追加 (5 行程度)
- `docs/dev/workflow_guide.md`: 1 段落追記 (5-10 行)。新 YAML への言及を含む
- `docs/dev/docs_maintenance_workflow.md`: L31 `/i-pr` 言及に「github / local 両対応」を明記し、local の場合の代替手順（`docs-maintenance-local.yaml` または runbook §3.1a の手動運用）を追記 (5-10 行)
- `docs/README.md`: Operations 索引に新 runbook 行を 1 行追加
- `CHANGELOG.md`: Unreleased 配下に Phase 5 entry 追加（10-15 行）
- `kaji_harness/providers/local.py`: docstring 1 箇所 + user-facing error message 1 箇所の文言修正
- `.claude/skills/pr-fix/SKILL.md` / `pr-verify/SKILL.md` / `i-pr/SKILL.md`: 各ファイル 1 行の文言修正

## テスト戦略

### コード変更が文言修正のみ + 新規 YAML 1 件のため、テスト追加は限定的

| 検証項目 | 手段 |
|---------|------|
| `LocalProvider` 文言修正後も既存 logic が同一 | `make test-large-local` (25 件) が無修正で pass する |
| `docs-maintenance-local.yaml` の構文 | `kaji validate .kaji/wf/docs-maintenance-local.yaml` |
| 既存 5 YAML との `requires_provider` 整合 | `kaji validate .kaji/wf/*.yaml` (前回 Phase 4 commit 3 で確認済) |
| **本番 docs / runbook の** markdown リンク健全性 | `make verify-docs` (`docs/ README.md CLAUDE.md .claude/skills/` を対象。Q22: `draft/design/` は POC 領域のため対象外) |
| markdown lint (見出し階層 / コードブロック等) | ruff/markdownlint は対象外。手動レビュー |
| `Phase 5` 言及の grep 検証 | 末尾 commit 前に `grep -rn "Phase 5 で" kaji_harness/ docs/ .claude/skills/ CHANGELOG.md` で残存ゼロを確認 |

### 受け入れ後の確認項目

- `make check` 緑（既存テスト全 pass）
- `make test-large-local` 緑
- `make verify-docs` 緑（既存対象範囲のみ）
- `kaji validate .kaji/wf/*.yaml` 全 6 ファイル緑
- `grep -rn "Phase 5 で" kaji_harness/ docs/ .claude/skills/ CHANGELOG.md` ヒット 0 件
- design.md / runbook を head から read-through し、章間の論理矛盾なし
- `draft/design/local-mode/phase5-design.md` および新規 design.md 章は手動レビュー（リンク自動検証なし、Q22 受け入れ）

## 実装範囲

### 変更対象ファイル

| ファイル | 種別 | 概要 |
|---------|------|------|
| `draft/design/local-mode/design.md` | 全面再構成 | §概要 / §検証戦略 / §BCP / §残課題（新）/ §履歴（新） |
| `docs/operations/local-mode-runbook.md` | 新規 | 検証期間運用 runbook（実用 v1、200-400 行軽量版）|
| `docs/README.md` | 軽微更新 | Operations 索引 (L47-50) に runbook 行 1 行追加 |
| `docs/cli-guides/local-mode.md` | 更新 | L130 / L156 / L188 cache 関連記述修正 + runbook リンク section 追加 |
| `docs/dev/workflow_guide.md` | 軽微追記 | local-mode 主体運用の段落追記 + 新 docs-local YAML への言及 |
| `docs/dev/docs_maintenance_workflow.md` | 更新 | L31 `/i-pr` 言及に「github / local 両対応」明記 + local の場合の代替手順（新 YAML / 手動運用）を追記 |
| `CHANGELOG.md` | 追記 | Unreleased に Phase 5 entry |
| `kaji_harness/providers/local.py` | 文言修正 | L712 docstring / L723 user-facing エラーメッセージ |
| `.claude/skills/pr-fix/SKILL.md` | 文言修正 | L44 Phase 5 言及 |
| `.claude/skills/pr-verify/SKILL.md` | 文言修正 | L49 Phase 5 言及 |
| `.claude/skills/i-pr/SKILL.md` | 文言修正 | L52 Phase 5 言及 |
| `.kaji/wf/docs-maintenance-local.yaml` | 新規 | docs-only Issue を local provider 配下で完走させる workflow |
| `draft/design/local-mode/phase5-design.md` | 新規 | 本ファイル（Phase 5 設計書）|
| `draft/design/local-mode/phase5-implementation-report.md` | 新規 | Phase 5 完了時に追加 |

### 実装順序（commit 粒度、軽量化版）

docs 中心 Phase のため commit を 6 件に集約（user 軽量化指示 RV3 反映、14 → 6）。

| # | commit | 種別 | 概要 | 依存 |
|---|--------|------|------|------|
| 0 | preflight | — | baseline `7a51f52` (main HEAD) 確認 / `feat/local-phase4` (`a0269ee`) main マージ確認 / worktree 作成 (`kaji-feat-local-phase5`) | — |
| 1 | docs(phase5) | docs | 本設計書 (`phase5-design.md`) を branch に commit | 0 |
| 2 | docs(design) | docs | `design.md` 全面再構成（§概要 / §検証戦略 / §運用前提 / §背景・目的 / §インターフェース / §スコープ外 / §受け入れ条件 / §オープン論点 / Phase 表 を更新 + §残課題 章 / §履歴 章を新規追加。§履歴 は要約のみ、旧 BCP フロー全文は移設しない） | 1 |
| 3 | feat(workflow) | feat | `.kaji/wf/docs-maintenance-local.yaml` 新規作成 + `kaji validate` 緑確認 | 2 |
| 4 | docs(runbook) | docs | `docs/operations/local-mode-runbook.md` 新規作成（§1-7 一括、200-400 行軽量版） | 3 |
| 5 | docs(integration) | docs | 既存 docs / Skill / コード文言の整合更新を一括: `docs/README.md` 索引追加 / `docs/cli-guides/local-mode.md` L130 / L156 / L188 修正 + runbook リンク / `docs/dev/workflow_guide.md` 追記 / `docs/dev/docs_maintenance_workflow.md` L31 両対応明記 / `.claude/skills/{pr-fix,pr-verify,i-pr}/SKILL.md` 文言修正 / `kaji_harness/providers/local.py:712,723` 文言修正 | 4 |
| 6 | docs(release) | docs | `CHANGELOG.md` Unreleased に Phase 5 entry 追加 + `phase5-implementation-report.md` 追加 | 5 |

各 commit の境界：
- commit 2 で design.md 全面再構成を 1 commit に集約。docs 中心 + コード変更ゼロのため大きな diff でも review しやすい
- commit 3 (feat workflow YAML) は独立させ、`kaji validate` 緑確認を分離
- commit 4 で runbook 全章を 1 commit。執筆中は draft として並行進行
- commit 5 で「既存 docs / Skill / コード文言の整合更新」を 6 ファイル一括。文言修正のみで論理依存なし。type は `docs(integration)` を採用（SF2 と整合）
- commit 6 で release 関連 (CHANGELOG + report) を一括

## 受け入れ条件

### 機械検証

- [ ] `feat/local-phase5` が baseline (main HEAD `7a51f52`、`feat/local-phase4` マージ済) から分岐している
- [ ] `make check` 緑（commit 5 以降）
- [ ] `make test-large-local` 緑（25 件、commit 5 以降）
- [ ] `make verify-docs` 緑（commit 5 以降。対象範囲: `docs/ README.md CLAUDE.md .claude/skills/`、`draft/` は対象外）
- [ ] `kaji validate .kaji/wf/*.yaml` 全 6 ファイル緑（commit 3 以降。新 YAML を含む）
- [ ] `grep -rn "Phase 5 で" kaji_harness/ docs/ .claude/skills/ CHANGELOG.md` ヒット 0 件
- [ ] `grep -n "## " draft/design/local-mode/design.md` に §残課題 と §履歴 が含まれる
- [ ] `docs/operations/local-mode-runbook.md` が存在し、§1-7 すべての見出しを含む
- [ ] `docs/README.md` Operations 索引に runbook 行が含まれる (`grep "local-mode-runbook" docs/README.md`)
- [ ] `kaji_harness/providers/local.py` 内の `Phase 5 の` / `Phase 5 'kaji sync` 文字列が 0 件
- [ ] `.claude/skills/{pr-fix,pr-verify,i-pr}/SKILL.md` 内の `Phase 5 で` 文字列が 0 件
- [ ] `.kaji/wf/docs-maintenance-local.yaml` が存在し `requires_provider: local` を含む

### 手動確認

- [ ] design.md を head から read-through し、新方針 (`local = 検証期間中の SoT`) が一貫して記述されている
- [ ] design.md の §履歴 が要約のみ（10 行程度）で構成され、旧 BCP フロー全文移設はしていない（git 履歴での参照を主にする方針、軽量化 RV3）
- [ ] design.md の §残課題 が後送り項目すべてをカバーしている（§ 1.3 ドラフトの 7 項目以上、cache reader 既実装の注記を含む）
- [ ] runbook の § 1 で検証期間の終了基準（forge 採用先確定時、SF4 案 A）が固定記述されている
- [ ] runbook の § 3.1 で `feature-development-local.yaml` と `docs-maintenance-local.yaml` の使い分けが明示されている
- [ ] runbook の § 3.1a に docs-only の手動運用フロー（`/i-doc-update` 〜 `/issue-close`）が記述されている
- [ ] `docs/dev/docs_maintenance_workflow.md` の `/i-pr` 言及に「github / local 両対応」が明記され、local の場合の代替手順 (`docs-maintenance-local.yaml` または runbook §3.1a) が追記されている
- [ ] runbook の § 4 「コード同期戦略」が gitlab を「現状の選択」として記述し、推奨明示を避けている
- [ ] runbook の § 5 「将来 forge への移行」が §残課題 (design.md) と相互参照している
- [ ] `docs/cli-guides/local-mode.md` の L130 / L156 / L188 がすべて新文言になっている
- [ ] `CHANGELOG.md` Unreleased に Phase 5 entry が追加され、user-facing error message / Skill markdown / 新 YAML の追加が Notes に明示されている
- [ ] `draft/design/local-mode/phase5-design.md` および新規 design.md 章の markdown リンクを手動レビュー（`make verify-docs` の対象外、Q22）

### Phase 5 完了後の手動作業（受け入れ条件外）

- [ ] §残課題 の各項目を local Issue (`local-<m>-N`) として起票（user 手動）

## ロールバック方針

本 Phase は **logic 変更ゼロ + docs 中心** のため、rollback は単純：

| 層 | 該当 commit | rollback 影響 |
|----|------------|--------------|
| 設計書 | commit 2 | revert で旧 design.md に戻る。コード挙動への影響なし |
| 新 YAML | commit 3 | YAML ファイル削除で完結。既存 5 YAML への影響なし。docs-only Issue は手動運用 (runbook §3.1a) で代替可能 |
| runbook | commit 4 | revert で runbook が消える。既存 docs (`local-mode.md`) は影響なし |
| 既存 docs / Skill / コード文言の整合 | commit 5 | revert で 6 ファイルすべて Phase 4 時点に戻る。文言修正のみで logic 変更ゼロのため副作用なし |
| CHANGELOG + report | commit 6 | revert で Phase 5 entry / report が消える |

各 commit は他 commit に依存しない（runbook と design.md は内容的にリンクするが、
片方だけ revert しても build error は発生しない）。**部分 rollback 可能**。
新 YAML (commit 3) は user が docs-only workflow を実際に使い始めた後で revert すると
既存 Issue の workflow 解決が壊れるため、後発 commit から revert する場合は注意。

## 影響ドキュメント

| ドキュメント | 影響 | 対応 |
|------------|------|------|
| `draft/design/local-mode/design.md` | 全面再構成 | commit 2 |
| `.kaji/wf/docs-maintenance-local.yaml` | 新規 | commit 3 |
| `docs/operations/local-mode-runbook.md` | 新規 | commit 4 |
| `docs/README.md` | Operations 索引追加 | commit 5 |
| `docs/cli-guides/local-mode.md` | L130 / L156 / L188 修正 + リンク追加 | commit 5 |
| `docs/dev/workflow_guide.md` | 段落追記 + 新 YAML 言及 | commit 5 |
| `docs/dev/docs_maintenance_workflow.md` | L31 `/i-pr` 言及に「github / local 両対応」明記 + local の場合の代替手順 | commit 5 |
| `.claude/skills/pr-fix/SKILL.md` | L44 文言修正 | commit 5 |
| `.claude/skills/pr-verify/SKILL.md` | L49 文言修正 | commit 5 |
| `.claude/skills/i-pr/SKILL.md` | L52 文言修正 | commit 5 |
| `kaji_harness/providers/local.py` | L712 / L723 文言修正 | commit 5 |
| `CHANGELOG.md` | Unreleased entry 追加 | commit 6 |
| `phase5-implementation-report.md` | 新規 | commit 6 |
| `CLAUDE.md` | 影響なし（local-mode の運用方針は docs 側に集約） | — |
| `docs/dev/development_workflow.md` | 影響なし（Phase 4 で更新済の `requires_provider` 記述を維持） | — |
| `docs/dev/workflow-authoring.md` | 影響なし（Phase 4 で更新済。新 YAML は既存スキーマに準拠） | — |

## 判断済み論点

Phase 5 着手前の対話 (Q1-Q20) で確定済の論点を記録する：

| # | 論点 | 確定 | 反映先 |
|---|------|------|--------|
| Q1 | サブフェーズ分割 | 1 Phase で完結（細分化なし）| 本設計書 § 概要 |
| Q2 | GitHub 復旧見込み | 復旧前提を放棄、forge 移行先は別途検討 | § 方針転換のサマリ |
| Q3 | GitHub 関連タスクの扱い | すべて残課題に降格 | § 残課題 (design.md) |
| Q4 | runbook の分量 | フル作成（あと送り回避）→ 軽量化後 RV3-2 で実用 v1（200-400 行）に縮小 | § 方針決定 2 |
| Q5 | cache atomic 書き込み | 案 A (Issue 単位 atomic rename) | § 残課題 (forge 移行時に再評価) |
| Q6 | list 統合表示 order | 案 B (セクション分け) → cache 不在で不要に | § 残課題 |
| Q7 | `--add-frontmatter` value 型 | string 固定。デメリット説明は残課題に残し、必要時に docs 化 | § 残課題 |
| Q8 | cache invalidation policy | 案 A+B (明示再 sync + warning) | § 残課題 (forge 移行時に再評価) |
| Q9 | PR context 解決方法 | 残課題に降格 | § 残課題 |
| Q10 | preflight commit | 実施。`feat/local-phase4` main マージ済 (`a0269ee`) 確認済 | 実装順序 commit 0 |
| Q11 | design.md 再構成方針 | 全面再構成 + §履歴 章で旧記述保持 | § 方針決定 1 |
| Q12 | gitlab 推奨明示 | 「現状の選択」として事実記述、推奨明示は避ける | § 方針決定 2 / runbook §4 |
| Q13 | `--add-frontmatter` の Phase 5 残置 | 残課題に切り出し | § 残課題 |
| Q14 | `kaji sync` の CLI 削除 | 実装ゼロのため削除作業不要 | § 概要 |
| Q15 | provider=local default 化 | しない | § 方針決定 3 / workflow_guide.md |
| Q16 | hostname fallback | しない（fail-fast 維持）| 設計変更なし |
| Q17 | 設計書ファイル構成 | 案 A (`phase5-design.md` + design.md 直接書き換え) | § 方針決定 1 |
| Q18 | 残課題の保存場所 | 案 B (design.md 内 §残課題)、後で local Issue 化 | § 方針決定 1 / § 残課題 |
| Q19 | コード同期戦略章のスコープ | 案 B (具体的 setup + フロー、gitlab 中立記述) | runbook §4 |
| Q20 | preflight | OK (`a0269ee Merge branch 'feat/local-phase4'` + `7a51f52 Merge branch 'chore/gitlab-mirror-setup'` 両方マージ済 main HEAD) | commit 0 |
| Q21 (MF1) | Skill 内 Phase 5 言及の更新 | 受け入れ。`pr-fix:44` / `pr-verify:49` / `i-pr:52` を in-scope に追加し commit 5 で対応（軽量化後 RV3-4）。grep 受け入れ条件にも `.claude/skills/` を含める | § 方針決定 5 / 受け入れ条件 |
| Q22 (MF4) | draft/ のリンク健全性検証 | **`draft/` は POC 領域のため verify 対象外**（user 判断）。`make verify-docs` の対象範囲は既存通り (`docs/ README.md CLAUDE.md .claude/skills/`)。設計書のリンクは手動レビューで担保 | § テスト戦略 / 受け入れ条件 |
| Q23 (MF6) | 47e9f69 の参照方法 | `chore/gitlab-mirror-setup` は main にマージ済 (`7a51f52`)。ただし `draft/lab/gitlab/setup-log.md` は **user のセッション作業記録**であり、Phase 5 のいかなる成果物 (runbook / design.md / docs) にも**取り込まない**。runbook §4 は一般的手順として独立執筆 | § 概要 / § 方針決定 2 / § Primary Sources |
| Q24 (MF2) | docs-only workflow の扱い | **案 C 採用**: `docs-maintenance-local.yaml` を新規作成（軽量化後 RV3-4 で commit 3）+ runbook §3.1a に手動運用フローを併記 | § 方針決定 6 / runbook §3.1 / §3.1a |
| Q25 (MF3) | gh:N cache reader 既実装の表現分離 | 受け入れ。残課題は cache **自動 populate** (`kaji sync from-github`) のみ。cache reader (`view_cached_issue`) は既存契約として維持 | § 1.3 §残課題 / § 3.4 LocalProvider 文言修正 |
| Q26 (MF5) | docs/README.md Operations 索引 | 受け入れ。commit 5 で索引追加（軽量化後 RV3-4）| § 影響ドキュメント / § 3.2a |
| Q27 (SF1) | 「Only docs and in-source comments」表現 | 受け入れ。「docs / in-source comments / user-facing error messages / Skill markdown / new workflow YAML」へ拡張 | CHANGELOG entry 文面 |
| Q28 (SF2) | commit type | 受け入れ。`refactor(local)` → `docs(local)`（軽量化後 RV3-4 で commit 5 の `docs(integration)` に統合） | 実装順序 commit 5 |
| Q29 (SF3) | docs/cli-guides/local-mode.md の修正範囲 | L188 だけでなく L130 / L156 も統合修正 | § 3.1 |
| Q30 (SF4) | 検証期間終了基準 | **案 A 採用**: forge 採用先（gitlab 本格採用 / github 復帰 / 他）が確定した時点で検証期間終了。期間 KPI は設定しない | runbook §1 |
| Q23 改 | 47e9f69 / `draft/lab/gitlab/setup-log.md` の取り扱い | **取り込まない**（user 明示判断、初回 MF6 回答時に「これはただの作業記録」と既に明言）。runbook §4 は 47e9f69 に依存せず、一般的な GitLab セットアップ手順として独立執筆 | § 概要 / § 方針決定 2 / § Primary Sources |
| Q31 (再レビュー) | `docs/dev/docs_maintenance_workflow.md` の Phase 5 同梱判断 | **Phase 5 同梱確定**（user 判断）。L31 `/i-pr` 言及に github / local 両対応を明記、local の代替手順を追記。軽量化後 RV3-4 で commit 5 の `docs(integration)` に統合 | in-scope / 変更対象ファイル / commit 5 / 影響ドキュメント / 受け入れ条件 で一貫 |
| Q32 (再レビュー) | 新 YAML 骨格のスキーマ準拠 | 既存 `feature-development-local.yaml` を読み取り、`cycles` + `on:` 形式に統一。`when:` キーは現行 parser が受け付けないため使用しない。`inject_verdict: true` / `resume:` は fix-design / fix-code パターンを踏襲 | § 方針決定 6 |

## オープンな論点

- `CHANGELOG.md` の Phase 5 entry を `Changed` のみに置くか、`Notes` 節を新設するか。Keep a Changelog の慣例的には `Notes` は標準カテゴリではない。`Changed` 一本化が無難
- ~~design.md §履歴 に旧 §BCP フローを移設する際、commit 履歴で追跡可能なため敢えて全文保持が冗長か~~ → **review v3 (RV3-1) で確定**: §履歴 は要約のみとし、旧本文は git 履歴で参照する方針に決定済み

## 解消済みの論点（設計書段階で確定）

| 論点 | 確定内容 |
|------|---------|
| 47e9f69 の取り扱い | `draft/lab/gitlab/setup-log.md` は **user のセッション作業記録**であり、Phase 5 のいかなる成果物にも **取り込まない**（user 明示判断）。runbook §4 は 47e9f69 に依存せず、一般的な GitLab セットアップ手順として独立執筆する |
| `docs/dev/docs_maintenance_workflow.md` の更新 | **Phase 5 同梱確定**（user 判断）。L31 `/i-pr` 言及に「github / local 両対応」明記 + local の代替手順（`docs-maintenance-local.yaml` または手動運用 runbook §3.1a）を追記。軽量化後 RV3-4 で commit 5 `docs(integration)` に統合 |
| §残課題 の local Issue 化タイミング | **Phase 5 完了後に user 手動実施**（受け入れ条件外、§ Phase 5 完了後の手動作業 に明示済） |

## 工数見積

| commit | 概要 | 見積 |
|--------|------|------|
| 0 | preflight | 0.05 日 |
| 1 | phase5-design.md commit | 0.1 日 |
| 2 | design.md 全面再構成（§残課題 / §履歴 追加 + 全章整合更新、§履歴 は要約のみ） | 1.0 日 |
| 3 | docs-maintenance-local.yaml 新規 | 0.15 日 |
| 4 | runbook 新規作成（§1-7 一括、200-400 行軽量版） | 0.8 日 |
| 5 | 既存 docs / Skill / コード文言の整合更新一括（6 ファイル） | 0.5 日 |
| 6 | CHANGELOG + implementation report | 0.4 日 |
| **合計** | | **3.0 日** |

Phase 4 (実績 2.6 日) と同等。レビュー v1 / v2 でスコープが拡張した分の docs 物量
増加と、軽量化 (RV3) による §履歴 縮約 / runbook 200-400 行 / commit 集約 (14 → 6) の
削減が概ね相殺。

## 完了条件の段階確認

| 段階 | 確認内容 |
|------|---------|
| commit 1 完了 | 本設計書 (review 反映版) が branch に存在、reviewer がレビュー可能 |
| commit 2 完了 | design.md の再構成が完了。§残課題 / §履歴（要約のみ）を含み、新方針が一貫 |
| commit 3 完了 | `docs-maintenance-local.yaml` が `kaji validate` で緑 |
| commit 4 完了 | runbook が全 7 章完成（200-400 行軽量版、§3.1a 手動運用フロー / §1 検証期間終了基準を含む）|
| commit 5 完了 | 既存 docs / Skill / コード文言の整合更新完了。`make check` / `make test-large-local` / `make verify-docs` 緑 |
| commit 6 完了 | CHANGELOG + implementation report 完成、Phase 5 完了。user に local Issue 化を引き継ぎ |

## 参照情報（Primary Sources）

| カテゴリ | パス / コマンド | 参照目的 |
|---------|----------------|---------|
| Phase 5 baseline | main HEAD `7a51f52 Merge branch 'chore/gitlab-mirror-setup'` | 本 Phase の出発点。`feat/local-phase4` (`a0269ee`) も同 main 上 |
| 既存 design.md | `draft/design/local-mode/design.md` (1507 行) | 全面再構成の対象。§BCP フロー (L1038-1147) は §履歴 に移設せず、要約と git 履歴参照で代替（RV3-1）|
| 既存 runbook 不在 | `ls docs/operations/` → `release/` のみ | runbook を新規作成する根拠 |
| 旧 Phase 5 言及 (LocalProvider) | `kaji_harness/providers/local.py:712,723` | docstring + user-facing error message 修正対象 |
| 旧 Phase 5 言及 (Skill) | `.claude/skills/pr-fix/SKILL.md:44`, `pr-verify/SKILL.md:49`, `i-pr/SKILL.md:52` | Skill markdown 文言修正対象（MF1）|
| 旧 Phase 5 言及 (docs) | `docs/cli-guides/local-mode.md:130, 156, 188` | cache 関連 + Phase 5 言及修正対象（SF3）|
| docs/README Operations 索引 | `docs/README.md:47` | 新 runbook 行追加対象（MF5）|
| docs-only 詰まりの根拠 | `.claude/skills/issue-design/SKILL.md:112`, `issue-implement/SKILL.md:149` (type:docs ABORT), `docs/dev/docs_maintenance_workflow.md:31` (`/i-pr` 参照) | MF2 問題の発生源。新 YAML 必要性の根拠 |
| 既存 cache reader 実装 | `kaji_harness/cli_main.py:1000`, `kaji_harness/providers/local.py:708`, `tests/test_phase3c_dispatcher.py:329-365` | `view_cached_issue()` 既存契約の根拠（MF3）|
| `make verify-docs` 対象 | `Makefile:32-33` | `docs/ README.md CLAUDE.md .claude/skills/` のみ。draft/ 非対象（Q22）|
| gitlab セットアップ作業記録（**取り込まない**） | `47e9f69 chore(gitlab): document GitLab mirror setup procedure` / `draft/lab/gitlab/setup-log.md` | user のセッション作業記録のため Phase 5 では参照・転記しない。runbook §4 は 47e9f69 に依存しない独立記述（Q23 改） |
| Phase 4 設計書 | `draft/design/local-mode/phase4-design.md` (1059 行) | 設計書フォーマット参照 |
| Phase 4 完了状態 | `phase4-implementation-report.md` | 申し送り (`pr_id` / `pr_ref` 注入) を §残課題 に集約する根拠 |
| 既存 workflow YAML | `.kaji/wf/feature-development-local.yaml` | 新 `docs-maintenance-local.yaml` の base |
| 全 user 対話 | 本設計書の Q1-Q30 | 判断済み論点として記録（review 反映含む） |
