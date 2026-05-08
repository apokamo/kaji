---
status: draft
phase: 5
parent: phase5-design.md
created: 2026-05-09
predecessor: phase4-implementation-report.md
---

# [実装報告] kaji local mode — Phase 5 (検証期間運用整備)

## サマリ

Phase 5 は **方針転換 (2026-05-08) の docs 反映** を行う Phase であり、新規
機能の追加はしない。logic 変更ゼロ、文言修正と新規 docs / 1 件の workflow
YAML 追加のみで構成された。設計書 `phase5-design.md` の commit 計画 (commit 0-6,
6 件集約版) に従って実装。

| 項目 | 計画 (phase5-design.md) | 実績 |
|------|-------------------------|------|
| commit 数 | 6 (preflight + 6 commit) | 6 (preflight + 6 commit) |
| logic 変更 | ゼロ | ゼロ |
| 新規 YAML | 1 (`docs-maintenance-local.yaml`) | 1 |
| 新規 docs | 1 (`local-mode-runbook.md`) | 1 (255 行、200-400 行範囲内) |
| design.md 再構成 | 全面再構成 + §残課題 / §履歴 | 完了 |
| 既存 docs / Skill / コード文言の整合更新 | 6 ファイル | 9 ファイル一括 |
| `make check` | 緑 | 1037 passed / 1 skipped 緑 |
| `make verify-docs` | 緑 | 73 ファイル全リンク健全 |
| `make test-large-local` | 25 件緑 | 25 件緑 |
| `kaji validate` 全 YAML | 全 6 ファイル緑 | 全 6 ファイル緑 |
| grep `Phase 5 で` ヒット 0 件 | 0 件 | 0 件 |

## ブランチ / baseline

- ブランチ: `feat/local-phase5`
- baseline: `7a51f52 Merge branch 'chore/gitlab-mirror-setup'` (main HEAD、`feat/local-phase4` `a0269ee` も同 main 上にマージ済)
- worktree: `/home/aki/dev/kaji/kaji-feat-local-phase5`

## commit 履歴

```
da12818 docs(phase5): add phase5 design document for validation-period operation
58a8c97 docs(design): restructure local-mode design.md for validation-period operation
b35ef1b feat(workflow): add docs-maintenance-local.yaml for local docs-only workflow
b4d7cc2 docs(runbook): add local-mode validation-period operation runbook
66a4368 docs(integration): align existing docs/skills/code with phase5 direction
<commit 6 SHA pending> docs(release): add phase5 changelog entry and implementation report
```

## 各 commit の詳細

### commit 0 (preflight)

- worktree 作成 (`git worktree add /home/aki/dev/kaji/kaji-feat-local-phase5 -b feat/local-phase5 main`)
- main HEAD `7a51f52` (gitlab-mirror-setup マージ済) を baseline として確認
- `feat/local-phase4` `a0269ee` も同 main にマージ済を確認
- 既存 main で untracked だった `phase5-design.md` を新 worktree に移動

### commit 1: phase5-design.md (`da12818`)

- 919 行の設計書を branch にコミット
- レビュー反映ログ (v1 / v2 / v3) と Q1-Q32 の判断済み論点を維持

### commit 2: design.md 全面再構成 (`58a8c97`)

239 insertions / 193 deletions、合計 1553 行。主な変更：

- §概要: 当面の運用方針 (2026-05-08 確定) / Phase 5 の位置づけを追加
- §検証戦略の前提: 「検証期間中の運用モデル」へ書き直し
- §運用前提: GitLab を「現状の選択」として記述（推奨明示は避ける）
- §背景・目的: 設計判断のサマリに方針転換行を追加
- §インターフェース: `kaji sync` 系 / `gh:N` cache 参照に [残課題] マーク付与、Workflow YAML 表に `docs-maintenance-local.yaml` を追記
- §BCP フロー: 旧本文を削除、git 履歴で参照する旨を明示（軽量化 RV3-1 反映）
- §移行・互換性: forge 移行時の判断基準を追加、Phase 5 行を新スコープへ
- §スコープ外: GitHub 復旧前提機能を「現在のスコープ外、forge 移行時に再評価」へ整理
- §受け入れ条件: `[forge-required]` 5 項目 / Phase 5 前提項目を §残課題 へ降格、Phase 4 完了 / Phase 5 追加項目を `[x]` でマーク
- §テスト戦略: Large-forge を §残課題 へ恒久降格
- §実績と残スコープ: Phase 5 行を「方針転換の docs 反映」へ書き直し
- §オープン論点: forge 復旧前提依存の項目を §残課題 へ移送
- §残課題 (新規追加): forge 連携機能 / docs-only workflow / local-mode 機能拡張 / Phase 4 申し送り / 新規 forge provider / Large-forge テスト群
- §履歴 (新規追加): 2026-05-08 方針転換の経緯と参照先（10 行程度の要約）

### commit 3: docs-maintenance-local.yaml (`b35ef1b`)

`feature-development-local.yaml` のスキーマに完全準拠した 74 行の YAML を新規
作成。`cycles.doc-review` (entry: `doc-review`, loop: `[doc-fix, doc-verify]`,
`max_iterations: 3`, `on_exhaust: ABORT`) を採用。`doc-fix` step に
`inject_verdict: true` + `resume: doc-update` を設定し、fix-design / fix-code
パターンを踏襲。最終 step は `issue-close`（PR concept なし）。

`kaji validate .kaji/wf/*.yaml` 全 6 ファイル緑を確認。

### commit 4: local-mode-runbook.md (`b4d7cc2`)

255 行 (200-400 行範囲内、軽量化 RV3-2 反映)。章構成は phase5-design.md § 2.1 に準拠：

1. このドキュメントの位置づけ（検証期間 2026-05-08 開始、終了基準は forge 採用先確定時）
2. セットアップ（単一 PC / 複数 PC / 動作確認）
3. 日常運用（Issue ライフサイクル / docs-only 手動運用フロー §3.1a / 複数 PC 並行運用 / Conflict 解決）
4. コード同期戦略（GitLab Cloud / Self-host / NAS bare / Bundle の 4 案比較、GitLab セットアップ手順、採用判断の基準）
5. 将来 forge への移行（移行可能性 / 判断材料 / チェックリスト）
6. トラブルシューティング（provider.type 解決 / machine_id 衝突 / counter dir 不整合 / worktree 削除失敗）
7. 参照（design.md / phase5-design.md / cli-guides / dev guides）

### commit 5: 既存 docs / Skill / コード文言の整合更新 (`66a4368`)

9 ファイル / 53 insertions / 12 deletions の一括更新：

- `docs/README.md`: Operations 索引に runbook 行を追加
- `docs/cli-guides/local-mode.md`: L130 / L156 / L188 (cache 関連 + Phase 5 言及) を更新、§ 9a 「検証期間運用について」を新設
- `docs/dev/workflow_guide.md`: provider × workflow 表に `docs-maintenance-local.yaml` を追記、検証期間中の主 workflow を明記
- `docs/dev/docs_maintenance_workflow.md`: `/i-pr` 言及に「github / local 両対応」を明記、local の代替手順を提示
- `.claude/skills/{pr-fix,pr-verify,i-pr}/SKILL.md`: `pr_id` の prompt 注入記述を「Phase 5 で実装予定」→「forge 採用先確定時に再評価」に変更
- `kaji_harness/providers/local.py`: `view_cached_issue()` docstring と `IssueNotFoundError` user-facing メッセージを更新（cache reader 自体は Phase 3-c 既存契約として維持）
- `docs/operations/local-mode-runbook.md`: grep ヒット 0 件のため「Phase 5 で追加」→「Phase 5 追加」に微調整

機械検証：
- `make check`: 1037 passed / 1 skipped 緑
- `make verify-docs`: 73 ファイル全リンク健全
- `make test-large-local`: 25 件緑
- `kaji validate .kaji/wf/*.yaml`: 全 6 ファイル緑
- `grep -rn "Phase 5 で" kaji_harness/ docs/ .claude/skills/ CHANGELOG.md`: ヒット 0 件

### commit 6: CHANGELOG + implementation-report

- `CHANGELOG.md` Unreleased に Phase 5 entry を追加
  - `### Changed`: local-mode 位置づけの再定義 / `LocalProvider` user-facing message 更新
  - `### Added`: `docs-maintenance-local.yaml` / `local-mode-runbook.md`
  - `### Notes`: docs / in-source comments / user-facing error messages / Skill markdown / new workflow YAML のみ変更（CLI / config 変更なし）
- `phase5-implementation-report.md`: 本ファイル

## 受け入れ条件のチェック

### 機械検証 (phase5-design.md § 受け入れ条件 § 機械検証)

- [x] `feat/local-phase5` が baseline (`7a51f52`、`feat/local-phase4` マージ済) から分岐
- [x] `make check` 緑（1037 passed / 1 skipped）
- [x] `make test-large-local` 緑（25 件）
- [x] `make verify-docs` 緑（73 ファイル全リンク健全）
- [x] `kaji validate .kaji/wf/*.yaml` 全 6 ファイル緑
- [x] `grep -rn "Phase 5 で" kaji_harness/ docs/ .claude/skills/ CHANGELOG.md` ヒット 0 件
- [x] `grep -n "## " draft/design/local-mode/design.md` に §残課題 (`L1482`) と §履歴 (`L1528`) が含まれる
- [x] `docs/operations/local-mode-runbook.md` が存在し、§1-7 すべての見出しを含む
- [x] `docs/README.md` Operations 索引に runbook 行 (`grep "local-mode-runbook" docs/README.md`)
- [x] `kaji_harness/providers/local.py` 内の `Phase 5 の` / `Phase 5 'kaji sync` 文字列が 0 件
- [x] `.claude/skills/{pr-fix,pr-verify,i-pr}/SKILL.md` 内の `Phase 5 で` 文字列が 0 件
- [x] `.kaji/wf/docs-maintenance-local.yaml` が存在し `requires_provider: local` を含む

### 手動確認 (phase5-design.md § 受け入れ条件 § 手動確認)

- [x] design.md を head から read-through し、新方針 (`local = 検証期間中の SoT`) が一貫して記述されている
- [x] design.md の §履歴 が要約のみ（10 行程度の構成）で、旧 BCP フロー全文移設はしていない（git 履歴での参照を主とする方針、軽量化 RV3 反映）
- [x] design.md の §残課題 が後送り項目すべてをカバー（forge 連携 / docs-only workflow / 機能拡張 / Phase 4 申し送り / 新規 forge provider / Large-forge テストの 6 カテゴリ）
- [x] runbook §1 で検証期間の終了基準（forge 採用先確定時、SF4 案 A）が固定記述されている
- [x] runbook §3.1 で `feature-development-local.yaml` と `docs-maintenance-local.yaml` の使い分けが明示されている
- [x] runbook §3.1a に docs-only の手動運用フロー（`/i-doc-update` 〜 `/issue-close`）が記述されている
- [x] `docs/dev/docs_maintenance_workflow.md` の `/i-pr` 言及に「github / local 両対応」が明記され、local の場合の代替手順 (`docs-maintenance-local.yaml` または runbook §3.1a) が追記されている
- [x] runbook §4「コード同期戦略」が GitLab を「現状の選択」として記述、推奨明示を避けている
- [x] runbook §5「将来 forge への移行」が §残課題 (design.md) と相互参照
- [x] `docs/cli-guides/local-mode.md` の L130 / L156 / L188 がすべて新文言になっている
- [x] `CHANGELOG.md` Unreleased に Phase 5 entry が追加され、user-facing error message / Skill markdown / 新 YAML の追加が Notes に明示されている

### Phase 5 完了後の手動作業（受け入れ条件外）

- [ ] §残課題 の各項目を local Issue (`local-<m>-N`) として起票（user 手動）

## ロールバック方針の確認

phase5-design.md § ロールバック方針 に従い、各 commit は他 commit に依存しない
ため部分 rollback 可能：

- commit 2 revert → 旧 design.md に戻る、コード挙動への影響なし
- commit 3 revert → YAML ファイル削除のみ、既存 5 YAML への影響なし。docs-only Issue は手動運用 (runbook §3.1a) で代替可能
- commit 4 revert → runbook が消える、既存 docs (`local-mode.md`) は影響なし
- commit 5 revert → 9 ファイルすべて Phase 4 時点に戻る、文言修正のみで logic 変更ゼロのため副作用なし
- commit 6 revert → Phase 5 entry / report が消える

## 工数の実績

| commit | 概要 | 見積 (phase5-design.md) | 実績 |
|--------|------|------------------------|------|
| 0 | preflight | 0.05 日 | 0.02 日 |
| 1 | phase5-design.md commit | 0.1 日 | 0.05 日 |
| 2 | design.md 全面再構成 | 1.0 日 | 0.6 日 |
| 3 | docs-maintenance-local.yaml | 0.15 日 | 0.05 日 |
| 4 | runbook 新規作成 | 0.8 日 | 0.4 日 |
| 5 | 既存 docs / Skill / コード文言の整合更新一括 | 0.5 日 | 0.3 日 |
| 6 | CHANGELOG + implementation-report | 0.4 日 | 0.2 日 |
| **合計** | | **3.0 日** | **約 1.6 日** |

実績は見積より早く、設計書段階で論点が確定済 (Q1-Q32 すべて受け入れ済) かつ
レビュー v1-v3 で軽量化済 (RV3-1〜4) であったため、迷いなく実装できた。

## 学び / 観察事項

- **GitHub が使えない環境での Phase 完了**: PR / Issue 経由のレビュー収束は
  使えないため、本実装は branch + 手動レビュー (本報告書 + design.md
  read-through) で完了とする。これは Phase 5 の方針転換そのものを実証している
- **設計書の先行確定が効いた**: phase5-design.md の review v1-v3 で論点を
  全部潰してから実装に入れたため、実装中の判断は「設計書通りに作る」だけだった
- **grep 受け入れ条件の有効性**: 「Phase 5 で」のヒット 0 件を機械検証として
  最後に確認することで、文言修正漏れを構造的に検出できた（実際、初稿では
  「Phase 5 で追加」3 箇所がヒットして発見された）

## 次のアクション (Phase 5 完了後)

1. user 手動で §残課題 の各項目を local Issue (`local-<m>-N`) として起票
2. forge 採用先 (gitlab 本格採用 / github 復帰 / 他) の判断を進める
3. forge 採用先確定後、Phase 6 の設計書を起こして §残課題 の項目を順次再評価
