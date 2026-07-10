# Phase 5 完了サマリ

> Phase 5 (kaji local mode 検証期間運用整備) の **マージ完了記録**。
> GitHub アカウントが使えない環境のため PR を出せず、PR description 相当の
> サマリを MD として保持する。
>
> 詳細な commit-by-commit の実装報告は `phase5-implementation-report.md`、
> 設計書は `phase5-design.md` を参照。

## マージ記録

- **branch**: `feat/local-phase5`（マージ後削除済）
- **baseline**: `7a51f52 Merge branch 'chore/gitlab-mirror-setup'` (main HEAD)
- **merge commit**: `b570882 Merge branch 'feat/local-phase5'` (`--no-ff`)
- **gitlab push**: `b570882..d73bb4c main -> main`（Issue 起票 commit `d73bb4c` 含む）
- **commit 数**: 7 (preflight 含めると 8 段階。最終 commit はレビュー v1 Must Fix 反映)

## 実装の趣旨

Phase 5 は **方針転換 (2026-05-08) の docs 反映**を行う Phase。GitHub
復旧前提を放棄し、検証期間中は local-mode を SoT として運用する方針へ
転換した。本 Phase は logic 変更ゼロで、docs 中心 + 新 workflow YAML 1 件
+ 文言修正のみで構成される。

設計書: `draft/design/local-mode/phase5-design.md` (919 行、review v1-v3 反映済)

## commit 履歴

```
3d1ed07 fix(phase5):       review v1 must-fix feedback (verdict / BACK / kaji run paths)
5ebc87d docs(release):     phase5 changelog entry and implementation report
66a4368 docs(integration): align existing docs/skills/code with phase5 direction
b4d7cc2 docs(runbook):     local-mode validation-period operation runbook
b35ef1b feat(workflow):    docs-maintenance-local.yaml for local docs-only workflow
58a8c97 docs(design):      restructure local-mode design.md
da12818 docs(phase5):      phase5 design document
```

## diff サマリ

```
12 files changed, 1539 insertions(+), 204 deletions(-)
```

| ファイル | 変更 |
|---------|------|
| `draft/design/local-mode/phase5-design.md` | 新規 (919 行) |
| `draft/design/local-mode/design.md` | 全面再構成 (+239 / -193) |
| `draft/design/local-mode/phase5-implementation-report.md` | 新規 (208 行) |
| `.kaji/wf/docs-maintenance-local.yaml` | 新規 (74 行) |
| `docs/operations/local-mode-runbook.md` | 新規 (255 行) |
| `docs/README.md` | Operations 索引に runbook 追加 |
| `docs/cli-guides/local-mode.md` | L130 / L156 / L188 修正 + § 9a 新設 |
| `docs/dev/workflow_guide.md` | provider × workflow 表に追記 |
| `docs/dev/docs_maintenance_workflow.md` | `/i-pr` に github / local 両対応を明記 |
| `.claude/skills/{pr-fix,pr-verify,i-pr}/SKILL.md` | `pr_id` 注入記述を更新 |
| `kaji_harness/providers/local.py` | `view_cached_issue()` docstring + user-facing message 更新 |
| `CHANGELOG.md` | Unreleased に Phase 5 entry 追加 |

## 機械検証結果

| 検証項目 | 結果 |
|---------|------|
| `make check` | 1037 passed / 1 skipped 緑 |
| `make verify-docs` | 緑（73 ファイル全リンク健全） |
| `make test-large-local` | 25 件緑 |
| `kaji validate .kaji/wf/*.yaml` | 全 6 ファイル緑 |
| `grep -rn "Phase 5 で" kaji_harness/ docs/ .claude/skills/ CHANGELOG.md` | ヒット 0 件 |

## 実施済の merge コマンド（参考）

kaji の運用規約 (`docs/guides/git-commit-flow.md`) に従い `--no-ff` で merge：

```bash
git switch main
git merge --no-ff feat/local-phase5     # → b570882
git push gitlab main                    # 検証期間中のバックアップ先
git worktree remove /home/aki/dev/kaji/kaji-feat-local-phase5
git branch -d feat/local-phase5
```

## ロールバック方針（マージ済の今は parent commit から個別 revert）

各 commit は他に依存しないため部分 rollback 可能：

| 層 | commit | 影響 |
|----|--------|------|
| 設計書 | `58a8c97` | revert で旧 design.md に戻る、コード挙動への影響なし |
| 新 YAML | `b35ef1b` | YAML 削除のみ、既存 5 YAML への影響なし |
| runbook | `b4d7cc2` | runbook が消える、既存 docs は影響なし |
| 整合更新 | `66a4368` | 9 ファイルすべて Phase 4 時点に戻る、副作用なし |
| release | `5ebc87d` | Phase 5 entry / report が消える |
| review fix | `3d1ed07` | doc-verify resume / final-check BACK / kaji run path 修正の取り消し |

## Phase 5 完了後の手動作業（実施済）

- [x] §残課題 を local Issue 化（`d73bb4c chore(local): ...`）:
  - `local-pc5090-1`: forge 採用先確定後タスク bucket（A 集約）
  - `local-pc5090-2`: `kaji issue edit --add-frontmatter` 実装（B 個別）
  - `local-pc5090-3`: `kaji issue list` の filter / sort 強化（B 個別）
- [x] machine_id `pc5090` で main worktree の `.kaji/config.local.toml` を生成
  （`kaji local init --machine-id pc5090 --default-branch main`）
- [x] gitlab に push 完了（`b570882..d73bb4c main -> main`）

## 次のアクション（時間軸長め）

- 検証期間運用を実際に回して「複数 PC で gitlab に push して同期する」フローの実績を積む
- forge 採用判断（gitlab 本格採用 / github 復帰 / 他）を別途進める
- forge 採用先確定時、`local-pc5090-1` を分解して個別 Issue 化、その後 Phase 6 を起票

## 参照

- 設計書: `draft/design/local-mode/phase5-design.md`
- 詳細実装報告: `draft/design/local-mode/phase5-implementation-report.md`
- 再構成された設計書: `draft/design/local-mode/design.md`（特に §残課題 / §履歴）
- 新規 runbook: `docs/operations/local-mode-runbook.md`
- 新規 workflow YAML: `.kaji/wf/docs-maintenance-local.yaml`
- CHANGELOG: `CHANGELOG.md` Unreleased § Phase 5
