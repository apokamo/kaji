# [設計] GitLab forge 対応を完全撤去し GitHub 単独運用に切り替える

Issue: #191

## 概要

`kaji_harness/providers/gitlab.py`（859 行）と関連する provider dispatch / CLI passthrough / sync コマンド / cache reader / E2E テスト / 設計上の「GitLab 互換性検証」契約 / docs / skill 記述を完全撤去し、kaji を GitHub 単独 forge 前提の単一スタックに収束させる。LocalProvider は `kaji sync from-gitlab` 由来の cache 経路も同時に消す（呼出元が消えるため）。

本変更は **deprecated 機能の撤去を伴う refactor**（破壊的 cleanup）であり、外部観測可能な振る舞い変更を 2 種類含む:

1. **撤去される公開機能の外部挙動**: `provider.type='gitlab'` / `kaji sync from-gitlab` / `gl:N` Issue 参照 / `large_gitlab` marker / `test-large-gitlab` Makefile target は `ValueError` または `unknown subcommand` で fail-fast する形に変わる。これは「不変保証の対象外」であり、**意図された破壊的変更**として扱う
2. **永続 cache artifact の扱い**: 既存ユーザーの `.kaji/cache/gl-*.json` および `.sync-meta.json forge="gitlab"` は撤去後、LocalProvider 起動時に明示エラーで案内する（後述）

これらは [`refactor.md`](../../.claude/skills/_shared/design-by-type/refactor.md) § 「外部から観測可能な振る舞いを変えない」絶対要件からは **意図的に逸脱** する。逸脱を許容する根拠は本設計書 § 「振る舞い非変更保証のスコープ」で定義する。

**振る舞い非変更保証のスコープ**: 上記 2 種以外の経路 — すなわち **(a) GitHubProvider の挙動、(b) LocalProvider の GitHub cache / local-only 経路、(c) `kaji run` workflow runner、(d) `kaji issue` / `kaji pr` の GitHub passthrough、(e) `.kaji/issues/` local Issue ストア** — は IF / 挙動を完全に維持する。これらが現状実運用されている経路であり、refactor ガイドの「外部から観測可能な振る舞いを変えない」要件は **本スコープに限って** 適用する。

## 背景・目的

### 現状の問題（観測可能な形）

Issue #184（GitHub primary 化）が `full-cycle` workflow の `design-review` cycle を 3 iteration 使い切って ABORT した。`.kaji-artifacts/184/runs/2605260240/run.log` の `workflow_end status=ABORT, reason="Cycle 'design-review' exhausted"` と進捗ログ `.kaji-artifacts/184/progress.md` を突き合わせると、cycle 内で追加された設計契約はすべて「GitLab 経路を壊していないことを証明する」ための構造である:

- **baseline-aware 判定ルール**: HEAD と base commit `e1821f5` の failure set 機械照合
- **`test-large-gitlab` 二段構え検証**: 段 1（外部認証不要）+ 段 2（`KAJI_TEST_GITLAB_REPO=apokamo/kaji` 必須、skip 不可）
- **`[provider.gitlab]` 温存条項**: `git_remote = "gitlab"` を残し dispatch を維持
- **pipefail + PIPESTATUS による exit code 記録**: baseline 比較の厳密化

つまり cycle 枯渇の根本原因は「GitLab 経路を温存したまま GitHub primary 化する」という命題そのものの内部矛盾であり、本 Issue はその矛盾を「GitLab 経路撤去」によって解消する。

### ベースライン計測（refactor 必須項目）

2026-05-26 時点 `HEAD = 101bc01`（本 Issue の base commit、`main` 先端）での観測値。**探索条件は大小文字無視（`-i`）かつ `gitlab|glab|gl:` を含む**（SF-1 反映）:

| 計測 | 値 | 取得コマンド | 改修後の目標 |
|------|-----|---------------|-------------|
| `kaji_harness/providers/gitlab.py` 行数 | **859 行** | `wc -l kaji_harness/providers/gitlab.py` | 0 行 / ファイル不在 |
| `kaji_harness/` 内 `gitlab\|glab\|gl:` 参照（file 数、大小文字無視） | **16 ファイル** | `grep -rln -i "gitlab\|glab\|gl:" kaji_harness/ --include="*.py" \| wc -l` | 0 |
| 同（総ヒット数） | **451 件** | `grep -rcn -i "gitlab\|glab\|gl:" kaji_harness/ --include="*.py"` 合計 | 0 |
| `tests/` 内 `gitlab\|glab\|gl:` 参照 file 数（大小文字無視） | **約 40 ファイル**（[棚卸し表](#既存テストの棚卸しmf-2-対応)で詳細分類） | `grep -rln -i "gitlab\|glab\|gl:" tests/ --include="*.py" \| wc -l` | 0（historical comment は許容、後述） |
| `docs/` 内 GitLab 言及 markdown（大小文字無視） | **14 ファイル** | `grep -rln -i "gitlab\|glab" docs/ --include="*.md" \| wc -l` | 0（changelog 等の引用を除く） |
| `.claude/` 内 GitLab 言及 | **11 ファイル** | `grep -rln -i "gitlab\|glab" .claude/` | 0 |
| Makefile 内 GitLab 行 | **6 行**（`test-large-gitlab` target / help / `-m "not large_gitlab"`） | `grep -in "gitlab" Makefile` | 0 |
| `pyproject.toml [tool.pytest.ini_options].markers.large_gitlab` | 存在 | `grep -n "large_gitlab" pyproject.toml` | 削除 |
| `.kaji/config.toml [provider.gitlab]` | 存在（`type = "gitlab"` がデフォルト、`.kaji/config.local.toml` で `github` に override 済み） | `grep -in "gitlab" .kaji/config.toml` | 削除 |

> **Issue 本文と微差異の取扱い**: Issue 本文は「12 ファイル / 約 303 件」と記載するが、これは初版の Python lowercase grep 結果（11 files / 303 hits）に近い。SF-1 反映で大小文字無視 + `gl:` 追加にすると 16 files / 451 hits に増える。後者を **正式ベースライン** として採用し、Issue 本文の数値は historical reference として扱う。
>
> **historical comment の許容範囲**: 以下は撤去対象外として残置を許容する:
> - `CHANGELOG.md` 内の過去 release note（"GitLab provider support was removed in vX.Y" のような撤去アナウンス自体を含む）
> - `.kaji/issues/local-pc5090-*-gitlab-*` / `.kaji/issues/local-p1-*-gitlab-*` の local Issue ファイル本文（過去の作業履歴）
> - git commit message（既コミット履歴は不可変）
> - `.kaji/issues/*/comments/*.md` の過去コメント
>
> 上記以外で `gitlab|glab|gl:` が残ったら **regression として CR** 対象。再計測 grep は kaji_harness / tests / docs / .claude / Makefile / pyproject.toml / .kaji/config.toml に対してのみ評価する。

### 改善指標（測定可能な目標）

| 指標 | 現状 | 目標 |
|------|------|------|
| `kaji_harness/providers/gitlab.py` | 859 行 | 0 行（ファイル削除） |
| `kaji_harness/` 内 `gitlab\|glab\|gl:` 参照 file 数（大小文字無視） | 16 | 0 |
| 同 総ヒット数 | 451 | 0 |
| `tests/test_large_gitlab/` | 存在 | 削除 |
| Makefile `test-large-gitlab` target | 存在 | 削除（`make help` 出力からも消える） |
| Makefile `pytest -m "not large_gitlab"` exclude 句 | 存在 | 削除（exclude 不要、後述）|
| `docs/cli-guides/gitlab-mode.md` | 存在 | 削除、`docs/README.md` index からも除去 |
| 設計書テンプレ § GitLab 互換性検証 | 必須セクション | 撤去（refactor / feat Issue で baseline-aware 検証契約が再発しない） |
| `make check` の test 集合 | base で `pytest -m "not large_gitlab"`、`large_gitlab` を除外 | 改修後 `pytest`（exclude 句削除）。`large_gitlab` marker 自体が消えるため除外不要。test 集合は base と差分なし（`large_gitlab` テスト 0 件状態を維持） |
| `make test-large` | base で PASS（`large_local`） | PASS を維持（`tests/test_large_gitlab/` 削除後も `large_local` のみで構成） |

> **`make check` 集合の不変性（MF-3 対応）**: base commit `101bc01` の `Makefile:17-20` は既に `pytest -m "not large_gitlab"` で `large_gitlab` を除外している。改修後は `large_gitlab` marker 自体が `pyproject.toml` から消え、Makefile の exclude 句も削除される。両 base/HEAD で `make check` が実行する test 集合 = 「`large_gitlab` を含まない全 test」で一致する（HEAD では `tests/test_large_gitlab/` ディレクトリが存在しないため自動的に集合外）。したがって「GitLab E2E failure が `make check` から消失する」という初版表現は **誤り**。正しくは「`make check` の test 集合は base と HEAD で同一であり、振る舞い非変更を維持する」。

## 影響範囲（追加調査で発覚した scope）

Issue 本文の「対象スコープ」に加え、追加調査で以下が **同 PR スコープに含まれる必要がある** ことが判明した:

### LocalProvider 内の GitLab cache 経路（dead code 化）

`kaji_harness/providers/local.py` に `kaji sync from-gitlab` で生成された cache を読む API が存在:

- `_gitlab_cache_path(iid)` (`local.py:352`)
- `view_cached_gitlab_issue(iid)` (`local.py:794-816`)
- `_list_cached_gitlab_issues(state, labels)` (`local.py:818-`)
- `_cached_gitlab_issue_from_payload(payload)` (`local.py:1001-`)
- list 統合の append 経路 (`local.py:694-696`)

`kaji sync from-gitlab` 自体を撤去するため、これら API は dead code になる。LocalProvider 設計上「外部 forge cache の重ね合わせ」が機能のひとつだが、その唯一の供給源だった GitLab を消す以上、LocalProvider の cache 統合経路も削除する。GitHub cache 経路（`view_cached_github_issue` 等が存在する場合）は影響範囲外で温存（grep で確認: `kaji sync from-github` 系は `tests/test_sync_from_github.py` が存在し独立）。

### `gl:N` 形式参照の正規化経路

`kaji_harness/providers/__init__.py` の `normalize_id()` および `ResolvedKind = Literal["github", "local", "remote_cache", "gitlab"]` から `"gitlab"` を削除する。`gl:` prefix を持つ Issue 参照は invalid として reject する（または下位互換のため `IssueNotFoundError` 相当を返す）。

`.kaji/issues/local-pc5090-*-gitlab-*` のような **local Issue ファイル名** は historical record として残置（main 直コミット許容 path）。設計書本文・skill 本文の `gl:` 引用は historical note 化または削除。

### CLI subcommand

`kaji_harness/cli_main.py:`

- `kaji sync from-gitlab` subparser (`cli_main.py:103-` 周辺、`from .providers.gitlab import (...)`) を削除
- `_handle_issue_gitlab` / `_handle_pr_gitlab` dispatch (`cli_main.py:944` / `cli_main.py:1028`) を削除
- `_forward_to_glab` 関数 (`cli_main.py:1563-`) を削除
- `--print-provider-type` の help から `'gitlab'` 列挙を削除

### sync.py の GitLab 部分削除（SF-2 対応：方針確定）

`kaji_harness/sync.py` は GitHub と GitLab の両 sync を内包する。base commit `101bc01` の構成を確認:

- `sync_from_github()` (`sync.py:517-593`): GitHub Issue を `.kaji/cache/gh-{number}.json` に書く（**保持対象**）
- `sync_from_gitlab()` (`sync.py:287-477`): GitLab Issue を `.kaji/cache/gl-{iid}.json` に書く（**削除対象**）
- `sync_status()` (`sync.py:596-640`): `.sync-meta.json` の `forge` field を読み、`gh-*.json` と `gl-*.json` の合算件数を返す（**GitLab 経路削除のうえ保持**）
- `forge: str = "gitlab"` (`sync.py:266`) のデフォルト値（**削除対象** — `sync_from_gitlab()` 内のローカル默認値であり、`sync_from_github()` は `forge="github"` を hardcode 渡し）

確定方針: **sync.py は module として保持**。`sync_from_gitlab()` および GitLab 専用ヘルパ（`_gitlab_cache_path` / `_list_existing_gitlab_cached_numbers` / `_mark_cache_stale` の gl-* 経路等）を削除し、`sync_status()` の `gl_count = ...` 行と `if isinstance(forge, str) and forge == "gitlab"` 分岐を削除する。`sync_from_github()` と GitHub 部分は完全保持。

`.sync-meta.json` の既存 `forge="gitlab"` 値の扱いは [§ Cache artifact 移行ポリシー（MF-4 対応）](#cache-artifact-移行ポリシーmf-4-対応) を参照。

### Cache artifact 移行ポリシー（MF-4 対応）

撤去後、既存ユーザーの `.kaji/cache/` には GitLab forge 時代の artifact が残存しうる:

- `.kaji/cache/gl-*.json`: GitLab Issue cache（`kaji sync from-gitlab` で生成）
- `.kaji/cache/.sync-meta.json` の `forge` field が `"gitlab"`: GitLab に sync した meta record

これらの扱いを以下方針で定義する:

| Artifact | 撤去後の挙動 | 根拠 |
|----------|------------|------|
| `.sync-meta.json` の `forge == "gitlab"` | `kaji sync status` 起動時に **fail-fast**: `SyncError("legacy GitLab cache detected at <path>. GitLab forge support has been removed. Delete '.kaji/cache/' and re-run 'kaji sync from-github' to recover.")` を raise | `sync.py:633` の `forge == "gitlab"` 分岐を削除すると `forge=str(forge)` の generic 経路に落ち、無音で誤動作する可能性がある。明示エラーで利用者の判断を仰ぐ |
| `.kaji/cache/gl-*.json` ファイル本体 | LocalProvider は listing 対象外（`_list_cached_gitlab_issues` 削除済み）。ファイル削除はユーザーに委ねる（手動 `rm -f .kaji/cache/gl-*.json`） | 自動削除は data loss の可能性。fail-fast エラー文に削除コマンドを案内する |
| `.sync-meta.json` の `forge` が無い / `forge == "github"` / `forge is None` | 既存挙動を維持（保持対象） | `sync_from_github()` 経路は不変 |
| `kaji sync from-gitlab` invocation | `argparse` レベルで `unknown subcommand` exit code 2 | subparser 削除の自然な帰結 |

**fail-fast エラー文の正本**（実装時の文言）:

```
SyncError: legacy GitLab cache detected at <repo_root>/.kaji/cache/.sync-meta.json
(forge='gitlab'). GitLab forge support has been removed in this version of kaji.
To recover:
  1. Remove the legacy cache:
       rm -f <repo_root>/.kaji/cache/gl-*.json
       rm -f <repo_root>/.kaji/cache/.sync-meta.json
  2. Re-sync from GitHub:
       kaji sync from-github
```

**検証**: 上記 fail-fast 経路を Medium テストでカバーする（`tmp_path` に `.sync-meta.json forge="gitlab"` の cache を仕込んで `kaji sync status` 起動 → `SyncError` 確認）。詳細は § テスト戦略 § Medium。

### 設計上の「GitLab 互換性検証」契約

`.claude/skills/` 配下と `docs/dev/*.md` の以下記述を撤去:

- baseline-aware 判定ルール（HEAD vs base commit の failure set 機械照合プロトコル）
- pipefail + PIPESTATUS 記録手順
- `[provider.gitlab]` 温存・`git_remote = "gitlab"` 温存条項
- `test-large-gitlab` 二段構え検証契約
- 設計書テンプレ「§ GitLab 互換性検証」セクション

ただし、撤去が **設計レビュー rubric の質を下げない** ことを確認すること（例: 「振る舞い非変更の保証」の一般則は残し、GitLab 固有の検証手順だけを消す）。

## インターフェース

### 公開 IF（変更あり）

| IF | 現状 | 改修後 | 移行パス |
|----|------|--------|----------|
| `provider.type` 設定値 | `"github"` / `"local"` / `"gitlab"` | `"github"` / `"local"` | `provider.type = "gitlab"` は load 時に `ValueError` で reject（明示的 fail-fast） |
| `glab` CLI 依存 | 必要（gitlab provider 時） | 不要 | `glab` 未インストールでも `make check` PASS |
| `kaji sync from-gitlab` CLI | 存在 | 廃止 | `kaji sync from-gitlab` invocation は `argparse` レベルで `unknown subcommand` エラー |
| `gl:N` Issue 参照 | gitlab provider で valid / local provider で `remote_cache` | invalid（reject） | 既存 `.kaji/issues/local-pc5090-*` ファイル名は残置（historical）、新規参照は github numeric ID または local ID のみ |
| `ResolvedKind` Literal | `"github" \| "local" \| "remote_cache" \| "gitlab"` | `"github" \| "local" \| "remote_cache"` | 型変更だが `"gitlab"` を内部で生成する経路を全削除するため呼出側 type narrow 一致 |
| Makefile `test-large-gitlab` | 存在 | 削除 | `make help` から消える。CI も同 target を呼ばない |
| pytest marker `large_gitlab` | 存在 | 削除 | `pyproject.toml` から marker 削除、`make check` の `-m "not large_gitlab"` も `not large_forge` 等に変更不要（exclude 句ごと削除）|

### 公開 IF（不変宣言）

- `kaji issue` / `kaji pr` の **GitHub 経路** は完全に IF 不変。subcommand / フラグ / 出力 / 終了コードすべて
- LocalProvider の GitHub 経路 cache API（`view_cached_github_issue` 等）は不変
- `kaji run` workflow 起動経路は不変。`requires_provider` の `any` / `github` は維持
- `.kaji/issues/` 配下の local Issue ストア schema は不変

### 内部 IF

- `kaji_harness.providers.__init__` の dispatch 関数は `if config.provider.type == "github": ... if config.provider.type == "local": ...` の 2 分岐に縮退。`else: raise ValueError(...)` で `"gitlab"` を含む未知 type を reject
- `kaji_harness.providers.base.ReviewRequestProvider` 等の抽象 base は GitHub 経路のみが実装を提供する形に縮退（docstring から GitLab 言及を削除）

## 制約・前提条件

- **振る舞い非変更が絶対要件（スコープ限定、MF-1 対応）**: § 概要 § 振る舞い非変更保証のスコープ に列挙した範囲 — GitHubProvider / LocalProvider の GitHub cache・local-only 経路 / workflow runner / `kaji issue`/`kaji pr` の GitHub passthrough / `.kaji/issues/` local Issue ストア — に観測可能な差分を出さない。**GitLab 公開 IF（`provider.type='gitlab'` / `kaji sync from-gitlab` / `gl:N` / `large_gitlab` marker / `test-large-gitlab` target / `.sync-meta.json forge="gitlab"` 互換）は意図的に破壊的変更を行う**ことを明示
- **type:refactor を維持する根拠**: 本変更は (a) 構造再編（dispatch 分岐縮退、cache 経路統一）と (b) 公開機能撤去の両方を含む。type を `refactor` から `feature` 等に振り替える選択肢もあるが、変更の主成分は撤去であり新機能追加でない。`type:chore` も候補だが ベースライン計測・safety net 評価が `_shared/design-by-type/refactor.md` の指針と最も整合する。よって `type:refactor` を維持し、refactor ガイド「外部から観測可能な振る舞いを変えない」要件は **振る舞い非変更スコープに限って** 適用する形で逸脱を明示的に承認する。レビュワー判断で type の振替が妥当と判断される場合は `/issue-review-ready` への差し戻しを許容
- **Safety net**: § 「Safety net 評価（refactor 固有・MF-3 対応）」の保護対象 × 既存テスト対応表に従い、`pytest` 実行で baseline PASS / FAIL 集合を記録してから削除に入る。`pytest --collect-only` は coverage 評価ではないため使用しない
- **Scope 混在禁止**: 本 Issue では GitLab 撤去のみ。GitHub 経路の追加機能・バグ修正・docs 改善は同 PR に含めない（CLAUDE.md § Prohibitions と Issue 本文「対象スコープの明示」を遵守）
- **Docs と Code を同 PR**: docs だけ先に消すと「GitLab を案内するが実装は無い」、code だけ先に消すと「docs が嘘をつく」time window が発生する。同 PR スコープで両方更新
- **`.kaji/issues/` の historical record は残置**: `local-pc5090-*-gitlab-*` 系列の local Issue ファイルは過去の作業履歴。削除すると `kaji issue view local-pc5090-8` 等が壊れる
- **CHANGELOG / commit message 内の歴史的言及は対象外**: grep でのカウント時に `CHANGELOG.md` と `.kaji/issues/*/comments/*` は除外して評価
- **base commit は `101bc01` (`main` 先端) のみ**: 未マージ branch (#184 など) は safety net / baseline 根拠に使わない（MF-3 反映）

## 方針

### Before / After 構造

**Before**:

```
providers/
├── __init__.py       # dispatch: github | local | gitlab
├── base.py           # 抽象 base (GitLab MR 抽象に言及)
├── github.py
├── gitlab.py         # 859 行
├── local.py          # GitLab cache 経路を含む (view_cached_gitlab_issue 等)
├── models.py         # gl: 参照を含む docstring
└── _worktree.py

cli_main.py           # _handle_issue_gitlab / _handle_pr_gitlab / _forward_to_glab
sync.py               # sync_from_gitlab + forge="gitlab" デフォルト

tests/
├── test_providers_gitlab.py
├── test_dispatcher_gitlab.py
├── test_sync_from_gitlab.py
└── test_large_gitlab/   # E2E

Makefile              # test-large-gitlab target, `-m "not large_gitlab"`
pyproject.toml        # marker: large_gitlab
docs/cli-guides/gitlab-mode.md
docs/ + .claude/ の 14 + 11 ファイルが GitLab 言及
```

**After**:

```
providers/
├── __init__.py       # dispatch: github | local のみ
├── base.py           # GitLab 言及削除
├── github.py
├── local.py          # GitLab cache 経路削除
├── models.py         # gl: 参照削除
└── _worktree.py

cli_main.py           # GitHub / Local 経路のみ
sync.py               # GitHub-only または完全削除 (後述)

tests/
└── test_large_local/   # local provider E2E は維持

Makefile              # test-large-gitlab target なし
pyproject.toml        # marker: large_gitlab なし
docs/cli-guides/gitlab-mode.md  なし
```

### 移行ステップ（順序が重要）

Refactor の safety net 原則（[`_shared/design-by-type/refactor.md`](../../.claude/skills/_shared/design-by-type/refactor.md) § 7）に従い、**計測 → safety net → 改修 → 再計測** の順で進める。

#### Step 1: ベースライン計測の再現性確認

実装フェーズ冒頭で、本設計書「ベースライン計測」表の全コマンドを実行し、設計書記載値と一致することを確認する。差分があれば設計書を更新。

#### Step 2: Safety net 確認（既存テストでの振る舞い非変更保証）

§ 「Safety net 評価」で定義した保護対象 × 既存テスト対応表に従い:

1. `make test-small` / `make test-medium` / `make test-large-local` を base 状態で実行し PASS / FAIL 集合を baseline として記録
2. 対応表に列挙した保護対象テスト（`tests/test_providers_github.py` / `tests/test_providers_local.py` / `tests/test_sync_from_github.py` / `tests/test_runner.py` / `tests/test_workflow_execution.py` / `tests/test_workflow_provider_match.py` / `tests/test_workflow_requires_provider.py` / `tests/test_config.py` / `tests/test_provider_type.py` / `tests/test_git_remote_propagation.py` / `tests/test_providers_normalize_id.py` / `tests/test_dispatcher.py` / `tests/test_cli_main.py` / `tests/test_local_cli_large_local.py` / `tests/test_provider_guard_large_local.py`）が PASS であることを確認
3. base 状態でこれらが PASS しない（既存 failure がある）ことが判明したら、その内容を Issue コメントに記録し、Step 3 以降で「base failure と区別できる新規 failure を発生させていないか」の判定基準とする

> **重要**: bridging test の追加は「GitHub 経路の new behavior」ではなく「撤去された GitLab 公開 IF の fail-fast 化」 + 「legacy cache 検出の fail-fast 化」のみを許可する。新機能テストの混入は scope 違反（§ テスト戦略 § Small/Medium § 新規 bridging test 参照）。

#### Step 3: テストファイル削除と部分改修

§ 「既存テストの棚卸し（MF-2 対応）」のカテゴリ A/B に従い順次実施:

**A. ファイル全削除**:

1. `tests/test_large_gitlab/` ディレクトリごと
2. `tests/test_sync_from_gitlab.py`
3. `tests/test_providers_gitlab.py`
4. `tests/test_dispatcher_gitlab.py`

**B. ファイル内の GitLab 関連要素を選択削除**（棚卸し表のカテゴリ B 各行に従う）:

5. `tests/test_issue_context_cli.py`: `TestGitLab*` class 削除、`GitLabProviderError` import 削除
6. `tests/test_local_provider_main_worktree.py`: `GitLabProvider`/`GitLabProviderConfig` import 削除、`test_gitlab_provider_repo_root_unchanged` 削除
7. `tests/test_providers_normalize_id.py`: `TestGitLabProvider`/`TestGitLabRemoteCache` class 削除、`test_zero_rejected_gitlab` 削除
8. `tests/test_git_remote_propagation.py`: GitLab 関連 import / fixture / test 関数削除
9. `tests/test_runner_pr_context.py`: `GitLabProviderError` import 削除、関連 test 関数削除、`test_no_provider_type_gitlab_branching` の禁止パターン regex は **本テスト自身が `gitlab` リテラルを保護対象として参照する性質**のため、撤去後は assertion を「`provider.type == "gitlab"` 比較が `kaji_harness/` に **存在しないこと** を保証」と再解釈して維持。テスト自体は意味を持つ
10. `tests/test_workflow_provider_match.py`: `_PROVIDER_GITLAB` 定数および GitLab 関連 test 関数 4 本削除
11. `tests/test_config.py`: `provider.gitlab` schema 関連 test 削除（コメント `# gl:21:` は historical reference として残置）
12. `tests/test_dispatcher.py`: `test_explicit_type_gitlab_does_not_fallback_to_gh` 削除、`glab` 言及コメント修正

**C. marker 削除**:

13. `pyproject.toml [tool.pytest.ini_options].markers.large_gitlab` 行削除

**D. 中間検証**:

14. A-C 完了後 `make test-small` / `make test-medium` / `make test-large-local` を実行し、Step 2 で記録した baseline と比較。base failure を除き **新規 failure / collection error が無いこと** を確認

#### Step 4: コード削除

依存方向（callee → caller）の逆順、つまり caller 側から削除:

1. `kaji_harness/cli_main.py`:
   - `kaji sync from-gitlab` subparser 削除
   - `_handle_issue_gitlab` / `_handle_pr_gitlab` 分岐削除（dispatch を `if provider.type == "github": ...`「github」分岐＋ `local` 分岐に縮退）
   - `_forward_to_glab` 関数削除
   - `--print-provider-type` help 文字列から `'gitlab'` 削除
   - import 文 `from .providers.gitlab import (...)` 削除
2. `kaji_harness/sync.py`: `sync_from_gitlab()` および GitLab 専用ヘルパ削除（§ 「sync.py の GitLab 部分削除」確定方針）。`sync_status()` の `gl_count` 行と `forge == "gitlab"` 分岐は **legacy cache 検出 fail-fast 経路**に書き換え（§ 「Cache artifact 移行ポリシー」のエラー文を発行）。`sync_from_github()` は完全保持
3. `kaji_harness/providers/local.py`:
   - `_gitlab_cache_path` / `view_cached_gitlab_issue` / `_list_cached_gitlab_issues` / `_cached_gitlab_issue_from_payload`
   - list 統合経路 (`out.extend(self._list_cached_gitlab_issues(...))`)
   - 関連 docstring 言及
4. `kaji_harness/providers/__init__.py`:
   - `from .gitlab import GitLabProvider` import 削除
   - `__all__` から `"GitLabProvider"` 削除
   - dispatch `if config.provider.type == "gitlab"` 分岐削除
   - `ResolvedKind = Literal["github", "local", "remote_cache"]` に縮退
   - `normalize_id()` の `gl:` prefix 経路と `provider_name == "gitlab"` 経路を削除
   - docstring（"github / local / gitlab" のような列挙）から `gitlab` 言及削除
5. `kaji_harness/providers/models.py`: docstring の `gitlab` 言及削除（`pr_id` / `pr_ref` の例示など）
6. `kaji_harness/providers/base.py`: docstring の "GitLab MR を抽象化する" 等の言及削除
7. `kaji_harness/config.py`: `provider.gitlab` schema 削除（GitLabProviderConfig pydantic model 等）
8. `kaji_harness/errors.py` / `models.py` / `runner.py` / `workflow.py`: 残存 `gitlab`/`glab` 言及を grep で順次削除
9. **最後に** `kaji_harness/providers/gitlab.py` 自体を削除（caller がすべて消えてから）

#### Step 5: 設定ファイル更新

1. `.kaji/config.toml`:
   - `[provider]` `type = "gitlab"` → `type = "github"` に変更（または `.kaji/config.local.toml` で既に override 済みなので削除）
   - `[provider.gitlab]` セクション全体削除
2. `.kaji/wf/docs-maintenance.yaml`: docstring の `provider=gitlab` 言及を修正
3. `Makefile`: `test-large-gitlab` target / `.PHONY` / help / `-m "not large_gitlab"` 削除（pytest 既定実行から除外句が消える）

#### Step 6: docs 更新（同 PR スコープ）

14 ファイルを以下方針で:

- `docs/cli-guides/gitlab-mode.md`: ファイル削除
- `docs/README.md`: 上記 index 行削除
- `docs/cli-guides/github-mode.md`: GitLab との対比表現を削除
- `docs/cli-guides/local-mode.md`: `kaji sync from-gitlab` 言及を削除、`from-github` のみ残す
- `docs/dev/testing-convention.md`: `large_gitlab` marker テーブル削除、Makefile target 言及削除
- `docs/dev/shared_skill_rules.md` / `workflow_guide.md` / `development_workflow.md` / `workflow-authoring.md`: GitLab provider 言及を historical note 化、または削除
- `docs/operations/local-mode-runbook.md` / `docs/operations/release/runbook.md` / `docs/operations/release/admin-setup.md`: GitLab 言及削除
- `docs/reference/testing-size-guide.md`: Large 細分マーカーから `large_gitlab` 削除
- `docs/guides/git-worktree.md`: 必要なら GitLab 言及削除

#### Step 7: skill 更新

`.claude/` 配下 11 ファイルを GitHub 単独前提に書き換え。重点は以下:

- `pr-fix` / `pr-verify` / `i-pr` / `review` / `release` / `review-poll` / `review-cycle`: provider 分岐記述を GitHub 単独に縮退
- `issue-design` / `issue-implement`: 「GitLab 互換性検証」契約・baseline-aware 判定ルール・pipefail + PIPESTATUS 記述を削除
- `issue-close`: GitLab MR 経路の close 手順削除
- `.claude/agents/kaji-code-reviewer.md`: GitLab provider 言及削除

#### Step 8: CLAUDE.md 更新

- `## Git & GitLab` を `## Git` に rename
- `Forge: GitLab を正式採用` → `Forge: GitHub 単独` に変更
- `GitLab auto-close` → `GitHub auto-close`（同等機能）に変更
- `§ Prohibitions` の `§ Git & GitLab` 参照を `§ Git` に変更
- Release 行（146 行目）の `GitLab Release ページ作成` → `GitHub Release ページ作成`

#### Step 9: 再計測

ベースライン計測の全コマンドを再実行し、目標値に到達していることを確認（**SF-1 反映: 大小文字無視 + `gl:` を含む統一探索**）:

- `wc -l kaji_harness/providers/gitlab.py` → ファイル不在
- `grep -rln -i "gitlab\|glab\|gl:" kaji_harness/ --include="*.py" | wc -l` → 0
- `grep -rcn -i "gitlab\|glab\|gl:" kaji_harness/ --include="*.py"` 合計 → 0
- `find tests -name "*gitlab*"` → 空
- `rg -n -i "GitLabProvider|GitLabProviderError|GitLabProviderConfig|sync_from_gitlab" tests/ --type py` → 0（コード symbol のみ。historical comment は除外）
- `grep -rln -i "gitlab\|glab" docs/ --include="*.md" | wc -l` → 0
- `grep -rln -i "gitlab\|glab" .claude/ | wc -l` → 0
- `grep -in "gitlab" Makefile` → 0 件
- `grep -in "gitlab" pyproject.toml .kaji/config.toml` → 0 件

> CHANGELOG.md と `.kaji/issues/` 配下は historical record として再計測対象外（§ ベースライン計測 § historical comment の許容範囲 参照）。

#### Step 10: 品質ゲート

- `make check` PASS（ruff / mypy / pytest 全 green）。Step 2 で記録した base PASS/FAIL 集合と比較し、新規 failure / collection error が無いこと
- `make test-large` PASS（旧 `test-large-gitlab` を除く large テスト = `large_local` のみ）
- 任意で `make verify-docs` PASS（リンク切れ確認、特に `docs/cli-guides/gitlab-mode.md` への dead link が無いこと）

## 既存テストの棚卸し（MF-2 対応）

base commit `101bc01` 時点で `grep -rln -i "gitlab|glab|gl:" tests/ --include="*.py"` がヒットする tests/*.py を以下 3 カテゴリに分類し、改修方針を明示する。**ファイル名に `gitlab` を含まない直接依存テストも棚卸し対象**（MF-2 反映）。

### カテゴリ A: ファイルごと削除

`GitLabProvider` / `GitLabProviderError` / `GitLabProviderConfig` / `kaji sync from-gitlab` / `gl:N` 参照のテストで、GitLab 撤去とともに **テスト対象自体が消える** もの。

| ファイル | 削除根拠 |
|---------|---------|
| `tests/test_providers_gitlab.py` | `GitLabProvider` 単体テスト。本体削除に伴い無意味化 |
| `tests/test_dispatcher_gitlab.py` | dispatch の `type='gitlab'` 経路。経路自体が消える |
| `tests/test_sync_from_gitlab.py` | `sync_from_gitlab()` のテスト。関数が消える |
| `tests/test_large_gitlab/` ディレクトリ全体（7 ファイル: `conftest.py` / `test_issue_roundtrip.py` / `test_pr_review_contract.py` / `test_pr_roundtrip.py` / `test_pr_unsupported_sub.py` / `test_review_shape.py` / `test_sync_from_gitlab.py` / `test_workflow_e2e.py`） | GitLab E2E suite。実 API も provider も消える |

### カテゴリ B: ファイル内の GitLab 関連 test/class/method を選択削除（他は維持）

ファイル名に `gitlab` を含まないが、内部に GitLab 専用の test class / test method / fixture を持つ。**該当部分のみ削除し、GitHub / Local テストは温存**する。

| ファイル | 削除する要素 | 維持する要素（重要） |
|---------|------------|---------------------|
| `tests/test_issue_context_cli.py` (28 hits) | `TestGitLab*` class 内 `test_gitlab_provider_accepts_context_via_provider_method` / `test_gitlab_provider_accepts_gl_prefix` / `test_gitlab_provider_rejects_cross_provider_id` / `test_gitlab_provider_error_is_normalized_to_runtime_error` (`:143-269`)、`from kaji_harness.providers.gitlab import GitLabProviderError` import (`:252`) | GitHub provider / local provider 受理テスト |
| `tests/test_local_provider_main_worktree.py` (9 hits) | `from kaji_harness.providers.gitlab import GitLabProvider` import (`:289`)、`GitLabProviderConfig` import (`:25`)、`test_gitlab_provider_repo_root_unchanged` (`:288-304`) | gl:11 由来の LocalProvider main-worktree redirection テスト本体 |
| `tests/test_providers_normalize_id.py` (19 hits) | `class TestGitLabProvider` (`:77-99`) 全体、`class TestGitLabRemoteCache` (`:101-` 全体: `provider_name='local'` で `gl:N` を読む経路は撤去)、`test_zero_rejected_gitlab` (`:141-143`)、`gl:042`/`gl:0` 関連テスト | github / local の `normalize_id` 受理テスト |
| `tests/test_git_remote_propagation.py` (40 hits) | `GitLabProviderConfig` import (`:33`)、`GitLabProvider` import (`:43`)、`test_gitlab_provider_config_git_remote_default/explicit` (`:103-111`)、`test_gitlab_provider_git_remote_default/explicit` (`:139-147`)、`test_config_parses_provider_gitlab_git_remote` (`:200-`)、`test_get_provider_flows_git_remote_to_gitlab_provider` (`:334-349`)、関連 fixture | gl:6 由来の `git_remote` 伝播テストの GitHub 版・LocalProvider 版 |
| `tests/test_runner_pr_context.py` (21 hits) | `from kaji_harness.providers.gitlab import GitLabProviderError` import (`:29`)、`test_gitlab_provider_error_is_caught_and_warned` (`:71-`)、`test_gitlab_provider_error_warns_and_continues_without_pr_variables` (`:247-`)、関連 `provider.resolve_pr_context.side_effect = GitLabProviderError(...)` 部分 | GitHub provider / local provider 経由の PRContext 伝播テスト本体、および `test_no_provider_type_gitlab_branching` (`:284-301`) の禁止パターン regex は **GitLab 文字列を含むため一部修正が必要**（regex を `gitlab` → 残置可、なぜなら「分岐が**入っていない**ことを diff レベルで担保する」意図のため）。具体的には禁止パターンの retention 判断は後述 |
| `tests/test_workflow_provider_match.py` (14 hits) | `_PROVIDER_GITLAB` 定数 (`:30`)、`"gitlab": _PROVIDER_GITLAB` (`:56`)、`test_cmd_run_rejects_github_workflow_under_gitlab_provider` (`:128-`)、`test_cmd_run_rejects_gitlab_workflow_under_github_provider` (`:137-`)、`test_cmd_run_passes_gitlab_match` (`:146-`)、`test_cmd_run_any_passes_under_gitlab` (`:155-`) | `requires_provider` 突合の github / local 経路テスト |
| `tests/test_config.py` (9 hits) | `# gl:21:` コメント 9 箇所（コメントのみ、test 本体は不変）| `provider.gitlab` 関連 schema テストがあれば削除（要詳細確認）|
| `tests/test_dispatcher.py` (7 hits) | `test_explicit_type_gitlab_does_not_fallback_to_gh` (`:696`)、`--repository` 受理 test (`:932`) 内の `glab` 言及（コメント部分のみ）、`# gl:34` 等のコメント | dispatcher の github / local 経路テスト本体 |

### カテゴリ C: コメント / docstring の historical reference のみ修正

GitLab に関する実コードは含まないが、コメントや docstring 内で過去 Issue ID（`gl:21` / `gl:34` 等）や「github/gitlab provider テスト」の対比表現を含む。**コメント書換またはそのまま historical 残置**を選択。

| ファイル | 修正方針 |
|---------|---------|
| `tests/test_cli_main.py` / `tests/test_cli_streaming_integration.py` / `tests/test_cli_timestamp.py` / `tests/test_default_branch.py` / `tests/test_preflight.py` / `tests/test_prompt_builder.py` / `tests/test_provider_guard_large_local.py` / `tests/test_provider_overlay_divergence.py` / `tests/test_provider_type.py` / `tests/test_providers_github.py` / `tests/test_providers_local.py` / `tests/test_resolve_main_worktree.py` / `tests/test_runner.py` / `tests/test_runner_before.py` / `tests/test_skill_remote_placeholder.py` / `tests/test_skill_validation.py` / `tests/test_sync_from_github.py` / `tests/test_timeout_config.py` / `tests/test_verdict_e2e.py` / `tests/test_workdir_config.py` / `tests/test_workflow_execution.py` / `tests/test_workflow_requires_provider.py` / `tests/test_local_cli_large_local.py` / `tests/test_pr_bare_provider.py` | コメント内の `gl:N` Issue ID は **historical reference として残置許容**（過去 Issue の作業跡を示すため）。ただし「github/gitlab provider テスト」のような「現在も両方サポートしている」と読める表現は GitHub 単独前提に書き換え。docstring 中の `provider.type='gitlab'` 例示は削除 |

### 再計測と検出条件

撤去完了時の再計測は以下コマンドで:

```bash
# 1. kaji_harness/ 本体に GitLab 参照が残っていないこと（カテゴリ A/B の対象）
rg -n -i "gitlab|glab|gl:" kaji_harness/ --type py

# 2. tests/ で GitLab 参照が残っていないこと（カテゴリ A/B の対象、C は historical 許容）
rg -n -i "GitLabProvider|GitLabProviderError|GitLabProviderConfig|sync_from_gitlab|provider.type.*gitlab|normalize_id.*gitlab" tests/ --type py

# 3. docs / .claude / Makefile / pyproject.toml / .kaji/config.toml が clean
rg -n -i "gitlab|glab" docs/ .claude/ Makefile pyproject.toml .kaji/config.toml
```

検出 1 と 2 で **0 件**、検出 3 で `gitlab|glab` ヒット **0 件**（CHANGELOG.md と `.kaji/issues/` を除く）を改修完了条件とする。

カテゴリ C の `gl:N` historical comment は検出 1 / 2 の正規表現を「クラス名・関数名・実コード symbol」に絞ることで誤検出を回避する。

## テスト戦略

### 変更タイプ

**実行時コード変更（破壊的 cleanup を伴う refactor）**。コード削除に伴う dispatch 経路の縮退と公開 IF の fail-fast 化を含むため、新規 bridging test と既存テストの安全網確認が必要。

### Safety net 評価（refactor 固有・MF-3 対応）

[`refactor.md`](../../.claude/skills/_shared/design-by-type/refactor.md) § 7 の要請に従い、**振る舞い非変更スコープ（GitHub provider / LocalProvider GitHub 経路 / workflow runner / `kaji issue`/`kaji pr` GitHub passthrough）が既存テストで十分に保護されているか** を base commit `101bc01` のみを根拠に評価する。**未マージ #184 branch は safety net の根拠に使わない**（MF-3 反映）。

#### 保護対象 × 既存テスト対応表

| 保護対象（不変保証スコープ） | 守る既存テスト | カテゴリ |
|----------------------------|---------------|---------|
| GitHubProvider の Issue / PR / context 取得 | `tests/test_providers_github.py` / `tests/test_issue_context_cli.py`（GitHub 部） / `tests/test_runner_pr_context.py`（GitHub 部） | Small / Medium |
| LocalProvider の GitHub cache 読込 (`view_cached_github_issue` / `_list_cached_github_issues`) | `tests/test_providers_local.py` / `tests/test_sync_from_github.py` / `tests/test_local_cli_large_local.py` | Small / Medium / Large_local |
| LocalProvider の local-only 経路 (`kaji issue` local ID 系) | `tests/test_local_cli_large_local.py` / `tests/test_provider_guard_large_local.py` | Large_local |
| `kaji run` workflow runner | `tests/test_runner.py` / `tests/test_runner_before.py` / `tests/test_runner_pr_context.py`（GitHub 部） / `tests/test_workflow_execution.py` / `tests/test_workflow_requires_provider.py` / `tests/test_workflow_provider_match.py`（GitHub/Local 部） | Small / Medium |
| `kaji issue` / `kaji pr` の GitHub passthrough | `tests/test_dispatcher.py`（github 部） / `tests/test_cli_main.py` / `tests/test_pr_bare_provider.py` | Small / Medium |
| `normalize_id()` の github / local 経路 | `tests/test_providers_normalize_id.py`（GitHub/Local 部） | Small |
| `git_remote` 伝播の github / local 経路 | `tests/test_git_remote_propagation.py`（GitHub/Local 部） | Small |
| Config schema (`provider.github` / `provider.local`) | `tests/test_config.py`（GitHub/Local 部） / `tests/test_provider_type.py` | Small |
| `kaji sync from-github` / `kaji sync status` (GitHub 経路) | `tests/test_sync_from_github.py` | Small / Medium |

> **coverage 評価の意味付け**: 上表は test の **存在対応表** であり、defaultでは行カバレッジ計測（pytest-cov 等）を要求しない。`pytest --collect-only` も「test 発見手段」であり coverage ではない（MF-3 反映）。本対応表をもって「保護対象に対応する既存テストが存在する」safety net の根拠とし、削除に着手する。実装フェーズで `pytest tests/test_providers_github.py tests/test_providers_local.py ...` を Step 2（safety net 確認）として実行し、GitLab 撤去前に PASS を確認する。

#### Large テストの取扱い（MF-3 反映）

- `pyproject.toml:70` には `large_forge` marker（real GitHub API 用）が定義されているが、`grep -rln "large_forge" tests/` で参照されるのは marker 名前文字列の言及のみ。**実テスト 0 件**であり、GitHub 実 API E2E は base に存在しない
- したがって本 Issue では GitHub 実 API E2E の coverage 不足を refactor scope で解消しない（**scope 混在禁止**）。Large 戦略は `make test-large-local` の維持のみ
- 「Large が不要」な根拠: 撤去対象（GitLab 経路）は Large E2E で守られていたが、撤去対象そのものなので Large 不要。保護対象（GitHub provider）は Small / Medium で `tests/test_providers_github.py` 等にカバーされており、本 refactor の前後で実 API 経路に変更を入れないため、新規 Large は不要

### Small テスト

#### 既存テスト（流用、PASS を維持）

- `tests/test_providers_github.py`、`tests/test_providers_local.py`、`tests/test_providers_normalize_id.py`（GitHub/Local 部）、`tests/test_config.py`（GitHub/Local 部）、`tests/test_dispatcher.py`（github 部）、`tests/test_git_remote_propagation.py`（GitHub/Local 部）、`tests/test_workflow_provider_match.py`（GitHub/Local 部）、`tests/test_workflow_requires_provider.py`、`tests/test_provider_type.py`

#### 新規 bridging test（撤去後の挙動を固定）

撤去された公開 IF の fail-fast を保証する 3 本に限定:

1. **`provider.type='gitlab'` reject test**: `tests/test_config.py` に追加。`type = "gitlab"` を含む `.kaji/config.toml` を load → `ValueError` を確認
2. **`gl:N` reject test**: `tests/test_providers_normalize_id.py` に追加。`normalize_id("gl:42", provider_name="github", machine_id=None)` および `provider_name="local"` で `ValueError` を確認
3. **`kaji sync from-gitlab` unknown subcommand test**: `tests/test_cli_main.py` または `tests/test_sync_from_github.py` に追加。`kaji sync from-gitlab` invocation → exit code 2、stderr に subparser 既定エラー

> **bridging test の境界**: 上記 3 本は「撤去対象の fail-fast 化」を固定するのみで、GitHub 経路 / Local 経路の新規挙動テストは追加しない。新機能テストの混入は scope 違反。

### Medium テスト

#### 既存テスト（流用）

- `tests/test_local_cli_large_local.py`（small/medium 部分）、`tests/test_runner.py`、`tests/test_runner_before.py`、`tests/test_runner_pr_context.py`（GitHub 部）、`tests/test_provider_overlay_divergence.py`、`tests/test_workflow_execution.py`

#### 新規 bridging test（cache migration、MF-4 反映）

1. **legacy GitLab cache 検出 test**: `tests/test_sync_from_github.py` または `tests/test_providers_local.py` に追加。`tmp_path/.kaji/cache/.sync-meta.json` に `{"forge": "gitlab", ...}` を仕込んで `kaji sync status` 起動 → `SyncError` raise を確認、エラー文に recovery コマンド（`rm -f .kaji/cache/gl-*.json`）が含まれることを assert

### Large テスト

- **削除対象**: `tests/test_large_gitlab/` ディレクトリ全体（カテゴリ A）
- **既存テスト流用**: `tests/test_local_cli_large_local.py` / `tests/test_provider_guard_large_local.py`（`large_local` marker、`make test-large-local` 経路）
- **新規 Large テスト**: 不要（GitHub 実 API E2E は base に未存在、scope 混在禁止）

### 振る舞い非変更の保証（refactor 固有）

- safety net 評価対応表（上記）に列挙した既存テストが撤去前後で同一の PASS / FAIL 集合を保つことを Step 2（safety net 確認）と Step 9（再計測）で確認
- bridging test 4 本（Small 3 本 + Medium 1 本）は **撤去対象** に対する fail-fast 保証のみ。GitHub 経路 / LocalProvider GitHub 経路の挙動変更を意図的に避ける
- ベースライン計測値（Step 1）と再計測値（Step 9）の diff を Issue コメントに記録
- **`make check` 集合不変性**: base で `pytest -m "not large_gitlab"` で除外している test 集合と、HEAD で `pytest`（exclude 句削除）が実行する test 集合は、`large_gitlab` marker / `tests/test_large_gitlab/` がいずれも HEAD で消えるため同一になる。`make check` の PASS / FAIL 比較で振る舞い非変更を実証する

### `docs/dev/testing-convention.md` の 4 条件マッピング

本変更は実行時コード変更を含むため、「恒久テストを追加しない理由」を提示する義務はない（実行時コード変更時は Small/Medium/Large の検証観点定義が必要、本設計書で上記の通り定義済み）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | あり | `docs/adr/` に「GitLab forge 対応撤去」の ADR を 1 本追加（撤去理由 = Issue #184 cycle 枯渇の根本原因解消、再発防止のための forge 単一化方針） |
| `docs/ARCHITECTURE.md` | あり | provider 抽象から GitLab 分岐が消えることを反映（簡潔な diff、章追加なし） |
| `docs/dev/development_workflow.md` | あり | `make test-large-gitlab` 言及削除 |
| `docs/dev/testing-convention.md` | あり | `large_gitlab` marker 表行削除、関連リンク削除 |
| `docs/dev/shared_skill_rules.md` | あり | provider 切替の文脈で GitLab 言及があれば削除 |
| `docs/dev/workflow_guide.md` | あり | `requires_provider` 対応表から GitLab 列削除 |
| `docs/dev/workflow-authoring.md` | あり | provider 分岐記述の GitLab 例削除 |
| `docs/reference/testing-size-guide.md` | あり | Large 細分マーカーから `large_gitlab` 削除 |
| `docs/cli-guides/gitlab-mode.md` | あり | **ファイル削除** |
| `docs/cli-guides/github-mode.md` | あり | GitLab との対比表現削除 |
| `docs/cli-guides/local-mode.md` | あり | `kaji sync from-gitlab` 言及削除 |
| `docs/operations/local-mode-runbook.md` | あり | GitLab cache 経路言及削除 |
| `docs/operations/release/runbook.md` | あり | GitLab Release ページ言及削除（GitHub Release に統一） |
| `docs/operations/release/admin-setup.md` | あり | GitLab project 設定言及削除 |
| `docs/guides/git-worktree.md` | あり | GitLab worktree 例があれば削除 |
| `docs/README.md` | あり | `gitlab-mode.md` index 行削除 |
| `CLAUDE.md` | あり | `## Git & GitLab` を `## Git` に rename、GitLab 言及削除 |
| `.kaji/wf/docs-maintenance.yaml` | あり | docstring の `provider=gitlab` 言及修正 |
| `.claude/skills/` 配下 11 ファイル | あり | GitHub 単独前提に書き換え |
| `.claude/agents/kaji-code-reviewer.md` | あり | GitLab provider 言及削除 |
| `docs/reference/python/` | なし | コーディング規約への影響なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #191 本文 | <https://github.com/apokamo/kaji/issues/191> | 本設計の起票根拠。ベースライン計測値・完了条件・対象スコープを定義 |
| Issue #184 ABORT ログ | `.kaji-artifacts/184/runs/2605260240/run.log` | `workflow_end status=ABORT, reason="Cycle 'design-review' exhausted"`。**本撤去判断の動機根拠としてのみ参照**。#184 は未マージ branch（`a58b3a5` は `main = 101bc01` の祖先でない）であり、safety net / baseline の根拠としては使用しない（MF-3 反映） |
| Issue #184 進捗ログ | `.kaji-artifacts/184/progress.md` | cycle 内で追加された設計契約がすべて GitLab 経路温存に起因することを示す。同上の動機根拠としてのみ参照 |
| 関連設計書 | `draft/design/issue-184-forge-github-forge-skill-docs-github-pri.md` | 「GitLab 互換性検証」セクション。本 Issue で撤去対象 |
| GitLab provider 実装 | `kaji_harness/providers/gitlab.py:1-859` | 撤去対象本体 |
| dispatcher 実装 | `kaji_harness/providers/__init__.py:94-138` | dispatch 分岐削除箇所 |
| sync 実装 | `kaji_harness/sync.py:1-100, 266-321` | `sync_from_gitlab()` 撤去箇所、`forge="gitlab"` デフォルト変更箇所 |
| LocalProvider GitLab cache | `kaji_harness/providers/local.py:347-816` | GitLab cache 経路（dead code 化して削除） |
| CLI dispatch | `kaji_harness/cli_main.py:36, 92-110, 944, 1028, 1556-1574` | `kaji sync from-gitlab` / `_handle_*_gitlab` / `_forward_to_glab` |
| test marker | `pyproject.toml:71` | `large_gitlab` marker 定義 |
| Makefile target | `Makefile:2, 17-20, 34-41, 54-60` | `test-large-gitlab` target 周辺 |
| `.kaji/config.toml` | `.kaji/config.toml:14-16` | `type = "gitlab"` デフォルトと `[provider.gitlab]` セクション |
| Refactor 設計ガイド | `.claude/skills/_shared/design-by-type/refactor.md` | ベースライン計測・safety net・振る舞い非変更保証の手順論 |
| Testing 規約 | `docs/dev/testing-convention.md` | Small/Medium/Large の判定基準と恒久テスト要否 |
| CLAUDE.md 規約 | `CLAUDE.md:61-65, 91, 146` | 撤去対象の `## Git & GitLab` セクション本体 |
