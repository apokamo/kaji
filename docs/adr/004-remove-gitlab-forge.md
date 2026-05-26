# ADR 004: GitLab forge 対応の完全撤去

## ステータス

承認 (2026-05-26)

## コンテキスト

kaji は v0.x 系で GitHub と GitLab の 2 forge に対応していた。``kaji issue`` / ``kaji pr`` の dispatcher は ``provider.type`` を見て ``GitHubProvider`` / ``GitLabProvider`` のどちらに振り分けるかを判定し、`kaji sync from-gitlab` で GitLab Issue を ``.kaji/cache/gl-*.json`` に取り込む経路、`provider.type='local'` 配下から ``gl:N`` で remote cache を参照する経路、`test-large-gitlab` Makefile target と `large_gitlab` pytest marker による GitLab E2E など、forge 抽象が複数モジュール / docs / skill にわたって浸透していた。

実運用は GitHub 単独（`apokamo/kaji` も GitHub 上に存在）であり、GitLab 経路の E2E (`test-large-gitlab`) は `KAJI_TEST_GITLAB_REPO=apokamo/kaji` を要求して日常的に skip されていた。Issue #184（GitHub primary 化）の `full-cycle` workflow は `design-review` cycle を 3 iteration 使い切って ABORT し、`.kaji-artifacts/184/runs/2605260240/run.log` の進捗ログ上、cycle 内で追加された設計契約はすべて「GitLab 経路を壊していないことを証明する」ための構造（baseline-aware 判定 / `test-large-gitlab` 二段構え検証契約 / `pipefail + PIPESTATUS` / `[provider.gitlab]` 温存条項）だった。

設計レビュー cycle 枯渇の根本原因は「GitLab 経路を温存したまま GitHub primary 化する」という命題そのものの内部矛盾であり、GitLab 抽象を背負い続けることで GitHub 経路の新機能追加・バグ修正にも非線形なコスト（baseline-aware 検証契約の伝播）が乗ることが判明した。

## 決定

GitLab forge 対応に関連するコード・テスト・docs・skill 記述を完全撤去し、kaji を GitHub 単独 forge 前提の単一スタックに収束させる（Issue #191）。

具体的には以下を撤去する:

- `kaji_harness/providers/gitlab.py`（GitLab provider 実装本体、859 行）
- `kaji_harness/providers/__init__.py` の dispatch から `provider.type == "gitlab"` 分岐
- `kaji_harness/sync.py` の `sync_from_gitlab()` および GitLab 専用 helper
- `kaji_harness/cli_main.py` の `_handle_issue_gitlab` / `_handle_pr_gitlab` / `_forward_to_glab`
- `kaji_harness/providers/local.py` の `view_cached_gitlab_issue` / `_list_cached_gitlab_issues` / `_cached_gitlab_issue_from_payload` および list 統合経路
- `kaji_harness/config.py` の `GitLabProviderConfig` dataclass
- `tests/test_large_gitlab/` ディレクトリ全体、`tests/test_providers_gitlab.py` / `tests/test_dispatcher_gitlab.py` / `tests/test_sync_from_gitlab.py`
- `Makefile` の `test-large-gitlab` target、`pyproject.toml` の `large_gitlab` marker
- `docs/cli-guides/gitlab-mode.md`、各種 docs / skill 内の GitLab 言及

外部観測可能な振る舞いの変更:

1. **公開機能の撤去**: `provider.type='gitlab'` / `kaji sync from-gitlab` / `gl:N` Issue 参照 / `large_gitlab` marker / `test-large-gitlab` Makefile target は `ValueError` または `unknown subcommand` で fail-fast する形に変わる
2. **永続 cache artifact の扱い**: 既存ユーザーの `.kaji/cache/gl-*.json` および `.sync-meta.json forge="gitlab"` は、`kaji_harness/sync.py` の `_detect_legacy_forge_cache()` が `sync_status` / `view_cached_*` / `list_issues` の cache 統合経路の冒頭で検出し、`SyncError` で fail-fast したうえで recovery 手順（`rm -f` と `kaji sync from-github`）を案内する

## 影響

- `kaji issue` / `kaji pr` の **GitHub 経路** は完全に IF 不変（subcommand / フラグ / 出力 / 終了コードすべて）
- LocalProvider の GitHub cache 経路（`view_cached_issue` / `_list_cached_github_issues`）は不変
- `kaji run` workflow 起動経路は不変。`requires_provider` の値域は `{"github", "local", "any"}` に縮退
- `.kaji/issues/` 配下の local Issue ストアは不変
- 既存の `.kaji/cache/gl-*.json` / `.sync-meta.json forge="gitlab"` を持つユーザーは `kaji sync status` 起動時に `SyncError` で停止し、recovery 手順に従って `rm -f` で legacy cache を削除する必要がある（無音で entry が消える silent regression を防止）

## 代替案と却下理由

- **両 forge 対応を維持**: 撤去判断の根拠（Issue #184 cycle 枯渇）と矛盾。GitHub 経路の新機能・バグ修正のたびに GitLab 互換性検証契約が浸透する非線形コストが残る
- **GitLab 経路を deprecation 期間付きで撤去**: kaji は v0.x 系で stable 宣言前。明示的 deprecation 期間を設けず、本 PR で即時 fail-fast 化する。cache artifact 移行ポリシーで recovery 手順を案内することで影響を緩和する

## 関連 Issue

- #184: GitHub primary 化（cycle 枯渇 ABORT。本撤去の動機根拠）
- #191: 本 ADR の起票根拠
