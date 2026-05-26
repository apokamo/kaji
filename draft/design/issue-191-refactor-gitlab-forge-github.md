# [設計] GitLab forge 対応を完全撤去し GitHub 単独運用に切り替える

Issue: #191

## 概要

`kaji_harness/providers/gitlab.py`（859 行）と関連する provider dispatch / CLI passthrough / sync コマンド / cache schema / E2E テスト / 設計上の「GitLab 互換性検証」契約 / docs / skill 記述を完全撤去し、kaji を GitHub 単独 forge 前提の単一スタックに収束させる。LocalProvider は `kaji sync from-gitlab` 由来の cache 経路も同時に消す（呼出元が消えるため）。**振る舞い変更ではなく forge 多重対応の撤去** であり、GitHub provider と LocalProvider の GitHub 経路は IF 不変を維持する。

## 背景・目的

### 現状の問題（観測可能な形）

Issue #184（GitHub primary 化）が `full-cycle` workflow の `design-review` cycle を 3 iteration 使い切って ABORT した。`.kaji-artifacts/184/runs/2605260240/run.log` の `workflow_end status=ABORT, reason="Cycle 'design-review' exhausted"` と進捗ログ `.kaji-artifacts/184/progress.md` を突き合わせると、cycle 内で追加された設計契約はすべて「GitLab 経路を壊していないことを証明する」ための構造である:

- **baseline-aware 判定ルール**: HEAD と base commit `e1821f5` の failure set 機械照合
- **`test-large-gitlab` 二段構え検証**: 段 1（外部認証不要）+ 段 2（`KAJI_TEST_GITLAB_REPO=apokamo/kaji` 必須、skip 不可）
- **`[provider.gitlab]` 温存条項**: `git_remote = "gitlab"` を残し dispatch を維持
- **pipefail + PIPESTATUS による exit code 記録**: baseline 比較の厳密化

つまり cycle 枯渇の根本原因は「GitLab 経路を温存したまま GitHub primary 化する」という命題そのものの内部矛盾であり、本 Issue はその矛盾を「GitLab 経路撤去」によって解消する。

### ベースライン計測（refactor 必須項目）

2026-05-26 時点 `HEAD = 101bc01` での観測値:

| 計測 | 値 | 取得コマンド | 改修後の目標 |
|------|-----|---------------|-------------|
| `kaji_harness/providers/gitlab.py` 行数 | **859 行** | `wc -l kaji_harness/providers/gitlab.py` | 0 行 / ファイル不在 |
| Python 内 `gitlab`/`glab` 参照（file 数） | **11 ファイル** | `grep -rl "gitlab\|glab" kaji_harness/ --include="*.py" \| wc -l` | 0 |
| Python 内 `gitlab`/`glab` 参照（総ヒット数） | **303 件** | `grep -rc "gitlab\|glab" kaji_harness/ --include="*.py"` 合計 | 0 |
| `tests/` 内 GitLab 専用 path | `test_dispatcher_gitlab.py` / `test_providers_gitlab.py` / `test_sync_from_gitlab.py` / `test_large_gitlab/` ディレクトリ | `find tests -name "*gitlab*"` | 削除 |
| `docs/` 内 GitLab 言及 markdown | **14 ファイル** | `grep -rl "gitlab\|GitLab\|glab" docs/ --include="*.md" \| wc -l` | 0（changelog 等の引用を除く） |
| `.claude/` 内 GitLab 言及 | **11 ファイル** | `grep -rl "gitlab\|GitLab\|glab" .claude/` | 0 |
| Makefile 内 GitLab 行 | **6 行**（`test-large-gitlab` target / help / `-m "not large_gitlab"`） | `grep -n "gitlab" Makefile` | 0 |
| `pyproject.toml [tool.pytest.ini_options].markers.large_gitlab` | 存在 | `grep -n "large_gitlab" pyproject.toml` | 削除 |
| `.kaji/config.toml [provider.gitlab]` | 存在（`type = "gitlab"` がデフォルト） | `grep -n "gitlab" .kaji/config.toml` | 削除 |

> **Issue 本文と微差異**: Issue は「12 ファイル」「約 303 件」と記載するが実測 11 ファイル / 303 件。Issue 起票時の counting と本 Issue の検証時点（HEAD 同じ）が一致しなかった原因は `kaji_harness/providers/base.py` の docstring 「`gitlab` 互換性」言及を含めるか否かと推測される。実測値（11/303）を採用する。base.py の該当 docstring 行は撤去対象に含める。

### 改善指標（測定可能な目標）

| 指標 | 現状 | 目標 |
|------|------|------|
| `kaji_harness/providers/gitlab.py` | 859 行 | 0 行（ファイル削除） |
| Python 内 `gitlab`/`glab` 参照 file 数 | 11 | 0 |
| `tests/test_large_gitlab/` | 存在 | 削除 |
| Makefile `test-large-gitlab` target | 存在 | 削除（`make help` 出力からも消える） |
| `docs/cli-guides/gitlab-mode.md` | 存在 | 削除、`docs/README.md` index からも除去 |
| 設計書テンプレ § GitLab 互換性検証 | 必須セクション | 撤去（refactor / feat Issue で baseline-aware 検証契約が再発しない） |
| `make check` | PASS（GitHub 経路） | PASS を維持（振る舞い非変更の証明） |
| `make test-large` | PASS | PASS を維持（旧 `test-large-gitlab` を除く） |

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

### sync.py のデフォルト forge

`kaji_harness/sync.py` の `forge: str = "gitlab"` (`sync.py:266`) などのデフォルト値を `"github"` に変更、ないし sync.py 自体が GitLab しか扱わないなら module ごと削除（後述）。

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

- **振る舞い非変更が絶対要件**: GitHub provider の API レスポンス・LocalProvider の GitHub cache 経路・workflow 実行結果に観測可能な差分を出さない
- **Safety net**: 既存テスト suite で GitHub 経路と LocalProvider 経路のカバレッジが十分であることを `pytest --collect-only` レベルで確認してから削除に入る
- **Scope 混在禁止**: 本 Issue では GitLab 撤去のみ。GitHub 経路の追加機能・バグ修正・docs 改善は同 PR に含めない（CLAUDE.md § Prohibitions と Issue 本文「対象スコープの明示」を遵守）
- **Docs と Code を同 PR**: docs だけ先に消すと「GitLab を案内するが実装は無い」、code だけ先に消すと「docs が嘘をつく」time window が発生する。同 PR スコープで両方更新
- **`.kaji/issues/` の historical record は残置**: `local-pc5090-*-gitlab-*` 系列の local Issue ファイルは過去の作業履歴。削除すると `kaji issue view local-pc5090-8` 等が壊れる
- **CHANGELOG / commit message 内の歴史的言及は対象外**: grep でのカウント時に `CHANGELOG.md` と `.kaji/issues/*/comments/*` は除外して評価

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

- `make test-small` / `make test-medium` / `make test-large-local` の baseline 実行（PASS であること、または baseline failure を記録）
- GitHub 経路を網羅するテスト一覧を `pytest --collect-only tests/test_*github*.py tests/test_local_*.py` で抽出
- 抽出結果から「GitHub provider と LocalProvider の GitHub 経路の振る舞いが既存テストで十分カバーされているか」を判定。不足があれば **削除前に bridging test を追加**

> **重要**: bridging test の追加は「GitHub 経路の new behavior」ではなく「GitLab 撤去後に GitHub 経路の既存挙動が変わっていないことを保証するための既存挙動テスト」のみを許可する。新機能テストの混入は scope 違反。

#### Step 3: テストファイル削除

依存関係の少ない順に削除:

1. `tests/test_large_gitlab/` ディレクトリごと
2. `tests/test_sync_from_gitlab.py`
3. `tests/test_providers_gitlab.py`
4. `tests/test_dispatcher_gitlab.py`
5. `pyproject.toml [tool.pytest.ini_options].markers.large_gitlab` 行削除
6. 削除後 `make test-small` / `make test-medium` / `make test-large-local` PASS 再確認

#### Step 4: コード削除

依存方向（callee → caller）の逆順、つまり caller 側から削除:

1. `kaji_harness/cli_main.py`:
   - `kaji sync from-gitlab` subparser 削除
   - `_handle_issue_gitlab` / `_handle_pr_gitlab` 分岐削除（dispatch を `if provider.type == "github": ...`「github」分岐＋ `local` 分岐に縮退）
   - `_forward_to_glab` 関数削除
   - `--print-provider-type` help 文字列から `'gitlab'` 削除
   - import 文 `from .providers.gitlab import (...)` 削除
2. `kaji_harness/sync.py`: `sync_from_gitlab` および GitLab 専用ヘルパ削除。GitLab 専用なら module ごと削除し `cli_main.py` の `kaji sync` 親 subparser も評価
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

ベースライン計測の全コマンドを再実行し、目標値に到達していることを確認:

- `wc -l kaji_harness/providers/gitlab.py` → ファイル不在
- `grep -rl "gitlab\|glab" kaji_harness/ --include="*.py"` → 0
- `grep -rc "gitlab\|glab" kaji_harness/ --include="*.py"` 合計 → 0
- `find tests -name "*gitlab*"` → 空
- `grep -rl "gitlab\|GitLab\|glab" docs/ --include="*.md"` → 0
- `grep -rl "gitlab\|GitLab\|glab" .claude/` → 0
- `grep -n "gitlab" Makefile` → 0 件

#### Step 10: 品質ゲート

- `make check` PASS（ruff / mypy / pytest 全 green）
- `make test-large` PASS（旧 `test-large-gitlab` を除く large テスト）
- 任意で `make verify-docs` PASS（リンク切れ確認、特に `docs/cli-guides/gitlab-mode.md` への dead link が無いこと）

## テスト戦略

### 変更タイプ

**実行時コード変更（refactor / 削除）**。コード削除に伴う dispatch 経路の縮退があるため、新規 bridging test と既存テストの安全網確認が必要。

### Small テスト

- **既存テストの流用**: `tests/test_providers.py` / `tests/test_config.py` / `tests/test_workflow_provider_match.py` 等の dispatch unit test を実行し、GitLab 経路削除後も github / local のみで PASS することを確認
- **新規 bridging test**: `tests/test_config.py` に「`provider.type = "gitlab"` を含む `.kaji/config.toml` を load すると `ValueError` で reject される」ことを保証する unit test を 1 本追加（後方互換性の明示的拒絶）
- **正規化経路の縮退確認**: `tests/test_normalize_id.py` 相当（存在しなければ追加）で `gl:42` 参照が `ValueError` で reject されることを保証
- **不要な新規テスト**: GitHub 経路自体の挙動を変えないため、GitHub 経路の新規 small test は追加しない

### Medium テスト

- **既存テストの流用**: `tests/test_local_cli_*.py` / `tests/test_runner.py` / `tests/test_provider_overlay_divergence.py` を実行し、LocalProvider の GitHub cache 経路と workflow runner が GitLab 経路削除後も PASS することを確認
- **新規 bridging test**: 不要（LocalProvider の GitHub cache 経路は既存テストでカバー済み。GitLab cache 経路は dead code として削除するため新規テスト追加不要）

### Large テスト

- **既存テストの流用**: `make test-large-local` を実行し PASS を維持
- **削除対象**: `tests/test_large_gitlab/` ディレクトリごと削除
- **新規 large テスト**: 不要（GitHub primary 化 Issue #184 で `make test-large` の GitHub 経路カバレッジは既に確立済み）

### 振る舞い非変更の保証（refactor 固有）

- bridging test は「`gl:42` の reject」「`provider.type='gitlab'` の reject」「`kaji sync from-gitlab` の unknown subcommand」の 3 点に限定し、GitHub 経路 / LocalProvider GitHub 経路の挙動変更を意図的に避ける
- ベースライン計測値（Step 1）と再計測値（Step 9）の diff を Issue コメントに記録（refactor の「測定可能な改善」エビデンス）
- Issue #184 baseline failure 10 件（GitLab E2E 由来）が `make check` から消失することは「regression ではなく削除対象の消失」として記録

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
| Issue #184 ABORT ログ | `.kaji-artifacts/184/runs/2605260240/run.log` | `workflow_end status=ABORT, reason="Cycle 'design-review' exhausted"`。本撤去判断の根拠 |
| Issue #184 進捗ログ | `.kaji-artifacts/184/progress.md` | cycle 内で追加された設計契約がすべて GitLab 経路温存に起因することを示す |
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
