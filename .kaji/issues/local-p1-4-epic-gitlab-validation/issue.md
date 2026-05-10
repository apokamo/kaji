---
id: local-p1-4
title: 'EPIC: GitLab 対応検証（採用未確定 / 検証用）'
state: closed
slug: epic-gitlab-validation
labels:
- type:meta
- scope:gitlab-validation
created_at: '2026-05-09T02:51:54Z'
closed_at: '2026-05-09T06:26:37Z'
closed_by: pc5090
close_reason: completed
---
## 概要

GitLab を採用可能な状態を早期に整備するため、`GitLabProvider` を本格実装する先回り作業 EPIC。あわせて、local-mode の運用パターン（cache / sync / PR context 注入）を実 forge 連携を通じて検証する。

> **注意**: 本 EPIC は GitLab 採用を決定したものではなく、採用判断のための材料収集が目的でもない。GitLab 採用が決まった際に即座に移行できる「準備状態」を先回りで作っておく実装作業として位置付ける。

## 目的

- GitLab を採用可能な状態を早期に整備する（採用判断時の移行コストを最小化）
- local-mode の運用パターン（cache populate / sync / PR context 注入）を実 forge 越しに検証する

## ユーザーストーリー

- **maintainer として**、GitLab 移行が必要になった際に provider 実装を ad-hoc で書き起こす作業を避け、即座に運用に入れる状態にしておきたい
- **maintainer として**、local-mode の cache / sync 経路を実 forge 接続で動作させ、現状設計の弱点を事前に潰したい
- **kaji ユーザーとして**、forge を切り替えた際にも `kaji issue` / `kaji pr` のコマンド体験が変わらないことを確認したい

## 背景

GitHub 復旧前提を放棄した Phase 5 方針転換 (2026-05-08) 以降、`local-p1-1`（forge 採用先確定後タスク bucket）に GitLab 対応を含む forge 連携項目が集約されている。bucket は forge 採用が確定するまで個別 Issue 化されない設計。

本 EPIC は bucket の forge 連携項目のうち GitLab 関連を **採用判断とは独立に先取り実装** する。GitLab 採用が確定した時点で、bucket 解体時に本 EPIC の成果物を「再活用」し、bucket 残務（sync の `local-to-gitlab-plan` / Issue 一括転記支援等）のみを切り出して個別 Issue 化する。

> **ADR-004 (EPIC orchestration) に関する注記**: 本 EPIC は ADR-004 Accept 前の暫定 tracking Issue であり、`kaji run-epic` runner はまだ存在しない。子 Issue の依存解決と実行は手動で `kaji run` を回す前提。

## 本 EPIC の直接作業

本 Issue 自体は **tracking 兼 計画ドキュメント** であり、直接の実装作業を持たない。本 Issue で行うのは以下のみ:

- Open Questions の決着文書整備（`draft/lab/gitlab-validation/` 配下に OQ ごとの決定文書を配置。例: OQ-2 → `kaji-pr-mr-bridge.md`）
- 子 Issue の起票
- 子 Issue 進捗の集約と完了判定
- 確定事項の更新（実装中に方針変更が生じた場合）

実装作業はすべて子 Issue（後述）が担う。

## 確定事項（決定済み）

検討段階で決着した方針を以下に固定する。実装時は本セクションを参照する。

| # | 項目 | 決定内容 |
|---|---|---|
| 1 | CLI 採用 | `glab` CLI subprocess 方式（`GitHubProvider` の `gh` CLI と対称構造）。**kaji の依存として必須化** し、CI でも `glab` install を要求する。API 直叩き fallback は持たない |
| 2 | PR / MR 命名 | `kaji pr` を MR にエイリアス（skill 互換性最大化のため） |
| 3 | ID prefix 規約 | `gl:N` 形式で確定。**self-hosted（複数 GitLab インスタンス）対応はしない**。`normalize_id` は gitlab.com 前提で簡素化 |
| 4 | 認証 | git remote: ssh（既存登録済み鍵を再利用）/ GitLab API: token（格納方針は OQ-1 で決定） |
| 5 | 実装品質 | **本番使用用に本格実装**。検証用の throwaway 実装にはしない |
| 6 | `from-github` 対称性 | `kaji sync from-github` も Issue 化（後送り deferred）。今回 EPIC では実装しない |
| 7 | MR discussion / PR review comment 差分吸収 | GitLab 固有 shape は `GitLabProvider` 内で解釈し、既存 skill が期待する GitHub 互換 shape に変換する。**外向き contract は GitHub 互換、内部では GitLab の対応情報（`discussion_id` / `note_id` / `resolved` / `position`）を失わずに保持** する。`kaji pr review-comments` の出力 contract は GitHub 互換 subset を正本とし、`reply-to-comment` 相当では **GitLab 側で復元可能な provider-local ID 形式** を設計する（GitHub comment id をそのまま使うのではない）。skill 側には GitHub/GitLab 分岐を入れない |

## 子 Issue が扱う範囲

本 EPIC が束ねる実装範囲は以下。各項目は子 Issue として分割起票される（後述「子 Issue 構成」参照）。

- `GitLabProvider` 実装（`IssueProvider` Protocol 8 メソッド全実装）
- `ProviderConfig.type` の `gitlab` 追加 + `[provider.gitlab]` config セクション + `get_provider` 分岐
- `Workflow.requires_provider` enum への `gitlab` 追加 + builtin workflow の対応
- `normalize_id` の `gl:N` 拡張（`N` は project-local IID）
- `kaji issue` / `kaji pr` passthrough の gitlab 対応（`kaji pr` は MR エイリアス）
- `GitLabProvider.resolve_pr_context` + prompt 注入経路（Phase 4 申し送り分の GitLab 版）
- `kaji pr review-comments` / `reply-to-comment` の GitHub 互換 contract 実装（確定事項 #7）
- `kaji sync from-gitlab` + `sync status` + `.kaji/cache/` 自動 populate
- `docs/cli-guides/gitlab-mode.md` 新設 + 残存 gh 直接記述の forge-neutral 化
- `make test-large-gitlab` + provider=gitlab E2E（`make check` から分離）

## 本 EPIC では扱わない範囲

| 項目 | 理由 / 取扱い |
|---|---|
| `runbook v2` への書き換え | GitLab 採用が確定した時点で別途 |
| `local-p1-1` bucket の解体 | GitLab 採用が確定した時点で別途 |
| 既存 local Issue (`local-pcXXXX-N`) の GitLab 転記 | GitLab 関連作業完了後に必要性を判断 |
| `kaji sync local-to-gitlab-plan` | bucket 残務として残す |
| Issue 一括転記支援 | bucket 残務として残す |
| `.github/workflows/` → `.gitlab-ci.yml` 移植 | 子 Issue として起票するが **実施は後送り**（GitLab 採用確定後） |
| `kaji sync from-github` 実装 | 子 Issue として起票するが **実施は後送り**（GitHub 復帰判断後） |

## 子 Issue 構成（計 8 本）

### 実装系（6 本）

| Issue ID | タイトル | 概要 | 依存 |
|---|---|---|---|
| `local-p1-5` | GitLabProvider 実装 + config + dispatcher 拡張 | `IssueProvider` Protocol 8 メソッドを `glab` CLI subprocess で実装。`ProviderConfig.type` への `gitlab` 追加、`[provider.gitlab]` config dataclass（`repo = "group/project"` 必須 / `default_branch` 必須 / `hostname` は持たず `gitlab.com` 固定 — 確定事項 #3 の論理的帰結）、`get_provider()` の `GitLabProvider` 分岐、`Workflow.requires_provider` enum への `gitlab` 追加、builtin workflow の `requires_provider` 設計、`kaji validate` / workflow provider match のテスト更新を含む。`glab` context への暗黙依存はせず、`--repo` 相当の明示指定で動作させる | — |
| `local-p1-6` | `kaji issue` / `kaji pr` passthrough gitlab 対応 + ID 規約 (`gl:N`) 拡張 | `normalize_id` に `gl:N` パターン追加。**`gl:N` の `N` は project-local IID（`issue_iid` / `merge_request_iid`）を指す**。`provider.gitlab.repo` で対象 project を特定し IID で issue / MR を解決する。表示用 `issue_ref` は `gl:<iid>` 形式で統一。`cli_main.py` の dispatcher 拡張。`kaji pr` を MR にエイリアス。**`kaji pr review-comments` / `reply-to-comment` は確定事項 #7 の互換 contract で実装**（GitHub 互換 subset を正本、provider-local ID 形式で reply 復元、skill 側に GitHub/GitLab 分岐を持ち込まない） | `local-p1-5` |
| `local-p1-7` | `GitLabProvider.resolve_pr_context` + prompt 注入経路 | branch 名から MR を逆引きし `pr_id`（project-local `merge_request_iid`） / `pr_ref` を `prompt.py` に注入。MR の `resolved` 状態など GitLab 固有情報は確定事項 #7 に従い provider 内部で保持し、外向きには GitHub 互換 shape を返す | `local-p1-5` |
| `local-p1-8` | `kaji sync from-gitlab` + `sync status` + cache 自動 populate | `.kaji/cache/` を GitLab Issue から populate。atomic write、`kaji issue list` の local + cache 統合表示 | `local-p1-5` |
| `local-p1-9` | docs: `gitlab-mode.md` 新設 + gh 直接記述の forge-neutral 化 | `docs/cli-guides/gitlab-mode.md` 新設、`i-pr/SKILL.md:234` 等の残存 gh 直接記述を抽象化。`make test-large-gitlab` の env / glab auth / 検証用 project 前提も本 docs に明記 | `local-p1-5〜8` 並走 |
| `local-p1-10` | `make test-large-gitlab` + provider=gitlab E2E | E2E workflow 完走、issue/mr ラウンドトリップ、sync 通信、MR review-comments 疎通。**`make check` のデフォルト実行から除外し独立ターゲット化**。必要な env（`GITLAB_TOKEN` 等） / `glab auth` / 検証用 project の前提を Makefile および `docs/cli-guides/gitlab-mode.md` に明記 | `local-p1-5〜8` |

### 後送り (deferred) 系（2 本）

| Issue ID | タイトル | 概要 | trigger |
|---|---|---|---|
| `local-p1-11` | `[deferred]` CI 資産の GitLab 移植（release-please / labels-sync） | `release-please.yml` → release-cli or semantic-release / `labels-sync.yml` → GitLab Labels API 直叩き | GitLab 採用確定後 |
| `local-p1-12` | `[deferred]` `kaji sync from-github` 実装（GitHubProvider 対称化） | `from-gitlab` (`local-p1-8`) と対称構造で GitHub 側にも cache populate 機能。`local-p1-1` bucket からの先取り | GitHub 復帰判断後 |

> **`local-p1-11` / `local-p1-12` の運用ルール**: 両 Issue は計画記録ドキュメントとして open 維持される **deferred Issue** であり、**`/issue-review-ready` ゲート適用対象外** とする。trigger 発火時（GitLab 採用確定 / GitHub 復帰判断）に本 Issue を起点として **着手 Issue を新規起票** し、そちらで通常の review-ready → start → 実装フローを通す。本 Issue 自体は trigger 発火時に historical reference として close されるか、再活用されるかは状況に応じて判断する。

### 連番 ↔ 実 Issue ID 対応表

| 連番 | 実 Issue ID | 種別 | 状態 |
|---|---|---|---|
| #1 | `local-p1-5` | 実装 | open |
| #2 | `local-p1-6` | 実装 | open |
| #3 | `local-p1-7` | 実装 | open |
| #4 | `local-p1-8` | 実装 | open |
| #5 | `local-p1-9` | docs | open |
| #6 | `local-p1-10` | test | open |
| #7 | `local-p1-11` | deferred (CI 移植) | open |
| #8 | `local-p1-12` | deferred (from-github) | open |

## 進め方

1. **本 EPIC のレビューと確定**（現在）— 進め方 / スコープ / 子 Issue 構成のレビュー
2. **検討メモ整備** — `draft/lab/gitlab-validation/` 配下に OQ ごとの決定文書を配置し、後述の Open Questions のうち「子 Issue 起票前確定必須」を決着させる（命名はトピックに沿わせる。例: OQ-2 → `kaji-pr-mr-bridge.md`）
3. **review-ready ゲート通過** — `/issue-review-ready` 7 観点を満たした時点で「EPIC 確定」とみなす
4. **子 Issue 8 本起票** — 本 EPIC 本文の構成に従って `kaji issue create`。起票後、連番対応表を更新し、本文中の `#1`〜`#8` を実 ID へ置換
5. **実装** — 子 Issue `local-p1-5` → `local-pc5090-{6/7/8}` 並列（5 完了後）→ `local-p1-9` / `local-p1-10` 並走 → 検証
6. **完了レポート** — `provider.type='gitlab'` で 1 本の workflow が完走したら EPIC 完了。実装結果記録を `draft/lab/gitlab-validation/report.md` に残す

## Open Questions（決定保留）

EPIC 確定までに、`draft/lab/gitlab-validation/` 配下の OQ ごとの決定文書で決着させる。**確定タイミング** 列で、子 Issue 起票前に確定が必須か / 実装中に決定可かを区別する。**決定文書（決着済み）** 列に記録先 file 名を記す。

| # | 項目 | 候補 / 検討観点 | 確定タイミング | 決定文書 |
|---|---|---|---|---|
| OQ-1 | GitLab API token の格納方針 | (a) `GITLAB_TOKEN` env / (b) `~/.config/glab-cli/config.yml` (`glab auth login` 経由) / (c) `gh auth status` 同様に kaji が認証状態を検証 | **実装中に決定可**（子 Issue `local-p1-5` 内で確定すればよい） | 未決（決定時 `gitlab-token-storage.md` 等を作成） |
| OQ-2 | `kaji pr` MR エイリアスの実装範囲 | (a) URL / 番号系のみ / (b) 全 subcommand 対応 / (c) `glab mr` 引数体系吸収検証 / **(d) skill が使う sub に限定 + contract 統一** | **子 Issue 起票前に確定必須**（`local-p1-6` のスコープに直結） | **決着済み**: `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md`（(d) を採用） |
| OQ-3 | ラベル運用 | `.github/labels.yml` 由来の type:* / meta ラベル群を GitLab project でも宣言的に再現するか、検証期間中は GitLab デフォルトラベルで運用するか | **実装中に決定可**（GitLab project の運用次第） | 未決（決定時 `gitlab-label-policy.md` 等を作成） |

## 完了条件（本 EPIC の直接作業に対応）

本 EPIC は **「tracking 兼 計画ドキュメント」** であり、直接作業は計画 + 子 Issue 起票に限定される（§ 本 EPIC の直接作業）。完了条件もそれに揃える。実装の進捗は各子 Issue 個別の完了条件で追跡する。

- [x] **子 Issue 起票前ゲート**: 起票前確定必須 OQ（OQ-2: `kaji pr` MR エイリアス範囲）が決着済み（決定文書: `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md`）
- [x] 子 Issue 8 本起票完了、連番 ↔ 実 Issue ID 対応表が埋まっている
- [x] 子 Issue `local-p1-11` / `local-p1-12`（deferred）が起票済み + 運用ルール明記
- [x] 確定事項 #1〜#7 が本文に固定されている

> **実装フェーズの進捗追跡**: `provider.type='gitlab'` の workflow 完走 / `docs/cli-guides/gitlab-mode.md` の setup 手順記述 / `make test-large-gitlab` 緑 / 実装結果記録 (`draft/lab/gitlab-validation/report.md`) は **各子 Issue (`local-p1-5`〜`local-p1-10`) の完了条件で個別追跡** する。本 EPIC は計画完了で close し、実装系子 Issue の追跡は `scope:gitlab-validation` ラベルおよび `kaji issue list` のフィルタ経由に移行する。

## 不要になる条件

- GitLab 採用準備自体が中止された場合
  → `closed --reason not-planned` でクローズし、子 Issue は個別に判断（多くは not-planned）

## 参照

- bucket Issue: `local-p1-1`（forge 採用先確定後タスク bucket）
- 設計書: `draft/design/local-mode/design.md` § 残課題
- 検証期間運用 runbook: `docs/operations/local-mode-runbook.md` § 5「将来 forge への移行」
- GitLab mirror セットアップログ: `draft/lab/gitlab/setup-log.md`
- ADR-001 (Revised) / ADR-004: `draft/lab/adr-001-rev1.md` / `draft/lab/adr-004-epic-orchestration.md`
- IssueProvider Protocol: `kaji_harness/providers/base.py:16-83`
- 既存 GitHubProvider: `kaji_harness/providers/github.py`
- 既存 normalize_id: `kaji_harness/providers/_mappings.py`
