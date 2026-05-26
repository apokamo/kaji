# [設計] GitLab forge 対応を完全撤去し GitHub 単独運用に切り替える

Issue: #191

## 概要

`kaji_harness/providers/gitlab.py`（859 行）と関連する provider dispatch / CLI passthrough / sync コマンド / cache reader / E2E テスト / 設計上の「GitLab 互換性検証」契約 / docs / skill 記述を完全撤去し、kaji を GitHub 単独 forge 前提の単一スタックに収束させる。LocalProvider は `kaji sync from-gitlab` 由来の cache 経路も同時に消す（呼出元が消えるため）。

本変更は **deprecated 機能の完全撤去**（破壊的 cleanup）であり、外部観測可能な振る舞い変更を 2 種類含む:

1. **撤去される公開機能の外部挙動**: `provider.type='gitlab'` / `kaji sync from-gitlab` / `gl:N` Issue 参照 / `large_gitlab` marker / `test-large-gitlab` Makefile target は `ValueError` または `unknown subcommand` で fail-fast する形に変わる
2. **永続 cache artifact の扱い**: 既存ユーザーの `.kaji/cache/gl-*.json` および `.sync-meta.json forge="gitlab"` は撤去後、**(a) meta の `forge == "gitlab"` または (b) `gl-*.json` の存在のいずれか**を検出した時点で `kaji sync status` / `view_cached_*` / list 経路から明示エラー (`SyncError`) を raise する。meta 単独残存・`gl-*.json` 単独残存も両方カバーする（後述 § Cache artifact 移行ポリシー）

> **type ラベルについて**: 本 Issue は `type:chore`（review-ready PASS 済み）。`type:chore` は canonical 外 type で `feat.md` フォールバック適用となり、refactor ガイドの「外部から観測可能な振る舞いを変えない」絶対要件には縛られない（`docs/dev/development_workflow.md:78-79`）。撤去対象 (GitLab 公開 IF) の fail-fast 化は意図された破壊的変更として正規承認される。なお過去の design cycle round 2 までは `type:refactor` 前提で設計を組み、relabel を要求する fix-design verdict を経て review-ready 再実行 PASS に至った経緯がある（履歴参考: round 2 fix-design 報告 / verify-design 報告 Issue コメント）。

**振る舞い非変更保証のスコープ（GitLab 撤去対象外を保護）**: 上記の撤去対象以外の経路 — すなわち **(a) GitHubProvider の挙動、(b) LocalProvider の GitHub cache / local-only 経路、(c) `kaji run` workflow runner、(d) `kaji issue` / `kaji pr` の GitHub passthrough、(e) `.kaji/issues/` local Issue ストア** — は IF / 挙動を完全に維持する。本 PR で保護されるべき design contract。

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
| `kaji_harness/` 内 `gitlab\|glab\|gl:` 参照（file 数、大小文字無視） | **16 ファイル** | `grep -rln -Ei "gitlab\|glab\|gl:" kaji_harness/ --include="*.py" \| wc -l` | 1（`kaji_harness/sync.py` のみ。legacy cache migration detector に限定。後述「許容除外規則」参照） |
| 同（総ヒット数） | **451 件** | `grep -rcn -Ei "gitlab\|glab\|gl:" kaji_harness/ --include="*.py"` 合計 | ≤ 8（`sync.py` 内 migration detector + recovery エラー文に限定） |
| `tests/` 内 `gitlab\|glab\|gl:` 参照 file 数（大小文字無視） | **約 40 ファイル**（[棚卸し表](#既存テストの棚卸しmf-2-対応)で詳細分類） | `grep -rln -Ei "gitlab\|glab\|gl:" tests/ --include="*.py" \| wc -l` | 0（historical comment は許容、後述） |
| `docs/` 内 GitLab 言及 markdown（大小文字無視） | **14 ファイル** | `grep -rln -Ei "gitlab\|glab" docs/ --include="*.md" \| wc -l` | 1（`docs/adr/` 配下の撤去 ADR 1 本に限定。後述「許容除外規則」参照） |
| `.claude/` 内 GitLab 言及 | **11 ファイル** | `grep -rln -Ei "gitlab\|glab" .claude/` | 0 |
| Makefile 内 GitLab 行 | **6 行**（`test-large-gitlab` target / help / `-m "not large_gitlab"`） | `grep -in "gitlab" Makefile` | 0 |
| `pyproject.toml [tool.pytest.ini_options].markers.large_gitlab` | 存在 | `grep -n "large_gitlab" pyproject.toml` | 削除 |
| `.kaji/config.toml [provider.gitlab]` | 存在（`type = "gitlab"` がデフォルト、`.kaji/config.local.toml` で `github` に override 済み） | `grep -in "gitlab" .kaji/config.toml` | 削除 |

> **Issue 本文と微差異の取扱い**: Issue 本文は「12 ファイル / 約 303 件」と記載するが、これは初版の Python lowercase grep 結果（11 files / 303 hits）に近い。SF-1 反映で大小文字無視 + `gl:` 追加にすると 16 files / 451 hits に増える。後者を **正式ベースライン** として採用し、Issue 本文の数値は historical reference として扱う。
>
#### 許容除外規則（再計測時の明示許容範囲、MF-1 / MF-3 round 3 反映）

「kaji_harness/ で 0 件」絶対条件と「legacy cache migration を維持」「撤去 ADR を残す」要件の両立のため、以下を **再計測時の明示許容範囲** として定義する。許容範囲を超えるヒットは regression として CR 対象。

**1. リポジトリ全体で常に許容（historical record）**:

- `CHANGELOG.md`（撤去アナウンス自体を含む過去 release note）
- `.kaji/issues/local-pc5090-*-gitlab-*` / `.kaji/issues/local-p1-*-gitlab-*` の local Issue ファイル本文（過去の作業履歴）
- `.kaji/issues/*/comments/*.md` の過去コメント
- git commit message（既コミット履歴は不可変）

**2. `kaji_harness/sync.py` 内の legacy cache migration detector に限定許容**（MF-1 round 3）:

`kaji sync status` 起動時の legacy GitLab cache 検出 fail-fast は、**`kaji_harness/sync.py` 内の単一関数 `_detect_legacy_forge_cache(cache_dir)`（実装時に命名確定、機能は § Cache artifact 移行ポリシー 参照）と、そこから raise する `SyncError` メッセージ文字列**に閉じる。それ以外の `kaji_harness/` ファイルでは **0 件**。

許容ヒットの厳密内訳（実装時に diff レベルで担保。**合計 8 hits 以内**）:

| 出現位置 | リテラル | ヒット数 |
|---------|---------|---------|
| `_detect_legacy_forge_cache()` 関数定義 + caller | 関数名 `_detect_legacy_forge_cache` | 2 hits（def 行 + caller 1 箇所） |
| `forge == "gitlab"` 比較リテラル | `"gitlab"` | 1 hit |
| `SyncError` メッセージ本体 | `"legacy GitLab cache"` / `"GitLab forge support"` / `"gl-*.json"` / `"gl-"` glob 等 | ≤ 5 hits（recovery 文中で複数出現可） |

**実装時の検証 regex**:

> **regex 表記の統一（MF round 4 反映）**: 以下の検証コマンドは全て `grep -E`（ERE: extended regex）または `rg`（ripgrep, 既定で regex）を使用する。基本 grep（BRE）では `|` がリテラル扱いで alternation にならないため、表記を `grep -E` に固定して可搬性と挙動を一意化する。

```bash
# (a) sync.py 以外で active 残存が無いこと
grep -rln -Ei "gitlab|glab|gl:" kaji_harness/ --include="*.py" | grep -v "^kaji_harness/sync.py$" | wc -l   # → 0

# (b) sync.py 内のヒット総数が 8 以内かつ migration detector に閉じること
grep -cn -Ei "gitlab|glab" kaji_harness/sync.py                              # → ≤ 8
grep -n "_detect_legacy_forge_cache" kaji_harness/sync.py | wc -l           # → ≥ 2（定義 + caller）

# (c) detector 関数の外（module top-level / 他関数）に GitLab リテラルが漏れていないこと
#     → review-code で diff レベルに目視確認（機械判定困難なため設計レビュー観点で担保）
```

**3. `docs/adr/` 配下の撤去 ADR に限定許容**（MF-3 round 3）:

GitLab forge 対応撤去の意思決定を記録する ADR（§ 影響ドキュメント `docs/adr/` 参照）はファイル名と本文に `GitLab` を含む。これを除外する再計測 regex を以下に固定:

```bash
# docs/ 全体から docs/adr/ を除外して再計測
grep -rln -Ei "gitlab|glab" docs/ --include="*.md" | grep -v "^docs/adr/" | wc -l   # → 0
```

`docs/adr/` 配下は ADR 仕様上 immutable record のため、撤去 ADR 1 本（仮称 `docs/adr/NNNN-remove-gitlab-forge.md`、ファイル命名は実装時確定）にのみ GitLab 言及が許容される。それ以外の `docs/adr/` ファイルへの GitLab 追加は scope 違反として CR 対象。

**4. 上記以外で `gitlab|glab|gl:` が残ったら regression として CR 対象**。

再計測 grep の評価対象は kaji_harness / tests / docs / .claude / Makefile / pyproject.toml / .kaji/config.toml。許容除外規則 1〜3 を機械的に適用したうえで判定する。

### 改善指標（測定可能な目標）

| 指標 | 現状 | 目標 |
|------|------|------|
| `kaji_harness/providers/gitlab.py` | 859 行 | 0 行（ファイル削除） |
| `kaji_harness/` 内 `gitlab\|glab\|gl:` 参照 file 数（大小文字無視） | 16 | 1（`sync.py` の migration detector のみ。許容除外規則 § 2 参照） |
| 同 総ヒット数 | 451 | ≤ 8（`sync.py` 内 detector + recovery エラー文に限定。許容除外規則 § 2 参照） |
| `tests/test_large_gitlab/` | 存在 | 削除 |
| Makefile `test-large-gitlab` target | 存在 | 削除（`make help` 出力からも消える） |
| Makefile `pytest -m "not large_gitlab"` exclude 句 | 存在 | 削除（exclude 不要、後述）|
| `docs/cli-guides/gitlab-mode.md` | 存在 | 削除、`docs/README.md` index からも除去 |
| 設計書テンプレ § GitLab 互換性検証 | 必須セクション | 撤去（refactor / feat Issue で baseline-aware 検証契約が再発しない） |
| `make check` の test 集合 | base で `pytest -m "not large_gitlab"`、`large_gitlab` のみ除外。`tests/test_providers_gitlab.py` / `tests/test_sync_from_gitlab.py` / `tests/test_dispatcher_gitlab.py` 等は **実行対象** | 改修後 `pytest`（exclude 句削除）。HEAD では撤去対象テストが消えるため、test 集合は **base の strict subset**: `(base 集合) \ (撤去対象テスト)` |
| `make test-large` | base で PASS（`large_local` のみ。`large_gitlab` は除外句で除外）| PASS を維持（`tests/test_large_gitlab/` 削除後も `large_local` のみで構成） |

> **`make check` 集合の strict subset 関係（MF-3 round 2 訂正）**: 前 round で「base と HEAD で `make check` の test 集合は同一」と記載したが **誤り**。base の `make check` (`Makefile:17-20`) は `pytest -m "not large_gitlab"` で除外するのは `large_gitlab` marker を持つテストのみであり、`tests/test_providers_gitlab.py` / `tests/test_sync_from_gitlab.py` / `tests/test_dispatcher_gitlab.py`（およびカテゴリ B で削除する各 test 関数群）は marker を持たないため **base の `make check` で実行されている**。HEAD ではこれらが削除されるため、HEAD の `make check` 集合は base の strict subset (`base_set \ removed_set`) になる。
>
> したがって振る舞い非変更の根拠は「集合同一」ではなく「**`base_set \ removed_set` は保護対象 (GitHubProvider / LocalProvider) のテストを全て含む**」という形に再定義する。`removed_set` は § 既存テストの棚卸し のカテゴリ A + カテゴリ B の selective 削除部分から成り、その全てが「撤去対象 (GitLab provider) のテスト」または「GitLab fixture/import に依存して collection が失敗するテスト」であって保護対象のテストではない。これにより削除は振る舞い非変更スコープを破らない。
>
> **検証方法**: Step 2（safety net 確認）で `pytest --collect-only > /tmp/base.txt` を base で取得し、HEAD でも `pytest --collect-only > /tmp/head.txt` を取得して `comm -23 <(sort /tmp/base.txt) <(sort /tmp/head.txt)` の差分が **すべて GitLab 関連テスト（カテゴリ A/B の対象）であること** を確認。`comm -13` 側（HEAD 新規）は bridging test 4 本のみのはず。

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

これらの扱いを以下方針で定義する。**legacy cache 検出は `_detect_legacy_forge_cache(cache_dir)` 単一関数に集約**し、`kaji sync status` / `view_cached_*_issue` / list 統合経路の **すべての entry point** から先頭で呼び出して fail-fast する。検出条件は (a) `.sync-meta.json` の `forge == "gitlab"` または (b) `cache_dir/gl-*.json` の存在 のいずれかで成立する **OR 結合**（MF-2 round 3 反映、meta 不在 + `gl-*.json` 単独残存ケースを明示処理）:

| Artifact 状態 | 検出条件 (a)/(b) | 撤去後の挙動 | 根拠 |
|--------------|------------------|------------|------|
| `.sync-meta.json forge == "gitlab"` + `gl-*.json` あり | (a) AND (b) | `kaji sync status` / `view_cached_*` / list 起動時に **fail-fast**: 後述「fail-fast エラー文の正本」の `SyncError` を raise | meta + 本体ともに残存。最も典型的な legacy 状態 |
| `.sync-meta.json forge == "gitlab"` + `gl-*.json` 無し | (a) のみ | **fail-fast**（同上、recovery 文中の `rm -f gl-*.json` 行は no-op だが表示は維持） | meta のみ残るケース（手動で `gl-*.json` だけ削除済）。`sync_status()` の status 集計が `forge='gitlab'` を含む状態で動作するのは混乱の元 |
| `.sync-meta.json` 不在 OR `forge != "gitlab"` + `gl-*.json` あり | (b) のみ | **fail-fast**（同上、recovery 文中の `rm -f .sync-meta.json` 行は条件付き表示） | meta 削除済 or 手動コピー / 旧状態。base の `sync.py:607-615` と `providers/local.py:694-698,818-881` では `gl-*.json` を集計・list 表示していたため、撤去後に無言で entry が消えるのは silent regression。明示エラーで recovery を促す（MF-2 round 3 の核心修正） |
| `.sync-meta.json` 不在 OR `forge in {"github", None}` + `gl-*.json` 無し | (a) も (b) も不成立 | 既存挙動を維持（保持対象） | `sync_from_github()` 経路は完全不変 |
| `kaji sync from-gitlab` invocation | — | `argparse` レベルで `unknown subcommand` exit code 2 | subparser 削除の自然な帰結 |

**呼び出し箇所**（fail-fast を発火させる entry point の最小集合）:

| 関数 | 検出のタイミング |
|------|----------------|
| `kaji_harness.sync.sync_status()` | 関数冒頭、`gh-*.json` 集計の **前** |
| `kaji_harness.providers.local.LocalProvider.view_cached_*_issue()` | 関数冒頭、cache 読込の **前**（LocalProvider 側からは `kaji_harness.sync._detect_legacy_forge_cache()` を import して呼ぶ。重複実装は避ける）|
| `kaji_harness.providers.local.LocalProvider.list_issues()`（cache 統合経路） | list 構築の **前** |

**fail-fast エラー文の正本**（実装時の文言、condition 別に表示分岐）:

```
SyncError: legacy GitLab cache detected at <repo_root>/.kaji/cache/
  - .sync-meta.json forge='gitlab'   ← (a) 検出時のみ表示
  - gl-*.json files: <count> file(s)  ← (b) 検出時のみ表示

GitLab forge support has been removed in this version of kaji.
To recover:
  1. Remove the legacy cache:
       rm -f <repo_root>/.kaji/cache/gl-*.json        ← (b) または unconditional
       rm -f <repo_root>/.kaji/cache/.sync-meta.json   ← (a) または unconditional
  2. Re-sync from GitHub:
       kaji sync from-github
```

> **エラー文の許容ヒット計上**: 上記 "GitLab" / "gitlab" / "gl-" の出現は § ベースライン計測 § 許容除外規則 § 2 で定義した「`kaji_harness/sync.py` 内 ≤ 8 hits」に含まれる。LocalProvider から呼ぶ場合も detector / エラー文は `sync.py` に閉じ、`local.py` 側には import 文と関数呼び出ししか書かない（`local.py` には GitLab リテラルを残さない）。

**検証**: 上記 fail-fast 経路を Medium テストの **3 ケース** でカバーする（MF-2 round 3 反映）:

1. (a) AND (b): `tmp_path/.kaji/cache/.sync-meta.json` に `{"forge": "gitlab", ...}` を仕込み + `gl-42.json` 配置 → `kaji sync status` で `SyncError`、recovery 文に両 rm 行が出ること
2. (a) のみ: meta のみ仕込み、`gl-*.json` 無し → `SyncError`、recovery 文に meta 削除行が出ること
3. (b) のみ（MF-2 round 3 の新規ケース）: meta 不在、`gl-42.json` だけ配置 → `kaji sync status` で `SyncError`、recovery 文に `gl-*.json` 削除行が出ること。**この状態で `view_cached_*` / list を呼んでも同様に `SyncError` raise**（regression test として 1 ケース追加）

詳細は § テスト戦略 § Medium。

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

## type ラベル変遷の経緯（履歴）

本設計書は当初 `type:refactor` 前提で起草されたが、Issue 完了条件が「`gitlab.py` が削除されている」等の **削除を絶対要求** していたため、refactor ガイドの「外部から観測可能な振る舞いを変えない」要件と論理レベルで両立しないことが design-review round 2 で判明した。fix-design round 2 verdict / verify-design round 2 verdict（`/issue-verify-design 191` round 2）を経て、Issue は `type:refactor` → `type:chore` に relabel されたうえで `/issue-review-ready 191` 再実行 PASS に到達している。

`type:chore` は canonical 外 type のため `_shared/design-by-type/feat.md` フォールバック適用となり、refactor ガイドの非変更要件には縛られない（`docs/dev/development_workflow.md:78-79`）。撤去対象 (GitLab 公開 IF) の fail-fast 化は意図された破壊的変更として正規承認される。

本設計書本文（インターフェース・テスト戦略・移行ステップ・既存テスト棚卸し・safety net 評価表）の technical content は relabel 前後で不変である。

## 制約・前提条件

- **Issue type は `type:chore`**（review-ready PASS 済み）。canonical 外 type のため `feat.md` フォールバックを適用し、refactor ガイドの「外部から観測可能な振る舞いを変えない」要件は適用されない。撤去対象 (GitLab 公開 IF) の fail-fast 化は意図された破壊的変更として正規承認される
- **振る舞い非変更スコープ（GitLab 撤去対象外を保護）**: § 概要 § 振る舞い非変更保証のスコープ に列挙した範囲 — GitHubProvider / LocalProvider の GitHub cache・local-only 経路 / workflow runner / `kaji issue`/`kaji pr` の GitHub passthrough / `.kaji/issues/` local Issue ストア — に観測可能な差分を出さない。本 PR で保護される design contract
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
sync.py               # GitHub-only + legacy cache migration detector (保持、§ Cache artifact 移行ポリシー参照)

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
13. `tests/test_provider_overlay_divergence.py`（MF-2 round 2 追加）: `GitLabProviderConfig` import 削除、divergence test の対比値を `gitlab` → `local` に振り替え、fixture を `[provider.gitlab]` → `[provider.local]` に書換。`gitlab` × `github` の overlay divergence が `local` × `github` で再現できなければ Category A に降格（実装時判断）
14. `tests/test_sync_from_github.py`（MF-2 round 2 追加）: `GitLabProviderConfig` import 削除、fixture から `gitlab=GitLabProviderConfig()` 引数除去

**C. marker 削除**:

15. `pyproject.toml [tool.pytest.ini_options].markers.large_gitlab` 行削除

**D. 中間検証**:

16. A-C 完了後 `make test-small` / `make test-medium` / `make test-large-local` を実行し、Step 2 で記録した baseline と比較。base failure を除き **新規 failure / collection error が無いこと** を確認

#### Step 4: コード削除

依存方向（callee → caller）の逆順、つまり caller 側から削除:

1. `kaji_harness/cli_main.py`:
   - `kaji sync from-gitlab` subparser 削除
   - `_handle_issue_gitlab` / `_handle_pr_gitlab` 分岐削除（dispatch を `if provider.type == "github": ...`「github」分岐＋ `local` 分岐に縮退）
   - `_forward_to_glab` 関数削除
   - `--print-provider-type` help 文字列から `'gitlab'` 削除
   - import 文 `from .providers.gitlab import (...)` 削除
2. `kaji_harness/sync.py`: `sync_from_gitlab()` および GitLab 専用ヘルパ（`_gitlab_cache_path` / `_list_existing_gitlab_cached_numbers` / `_mark_cache_stale` の gl-* 経路等）を削除。`sync_status()` の `gl_count` 行と既存の `forge == "gitlab"` 分岐は **`_detect_legacy_forge_cache(cache_dir)` 単一関数** に書き換え、`sync_status()` 関数冒頭から呼び出して fail-fast（§ 「Cache artifact 移行ポリシー」の表 4 ケース・エラー文正本を実装）。`sync_from_github()` は完全保持。許容ヒット範囲（≤ 8）は § ベースライン計測 § 許容除外規則 § 2 を遵守
3. `kaji_harness/providers/local.py`:
   - `_gitlab_cache_path` / `view_cached_gitlab_issue` / `_list_cached_gitlab_issues` / `_cached_gitlab_issue_from_payload` を削除
   - list 統合経路 (`out.extend(self._list_cached_gitlab_issues(...))`) を削除
   - 関連 docstring 言及を削除
   - `view_cached_*_issue()` 系および `list_issues()` の cache 統合経路冒頭に `kaji_harness.sync._detect_legacy_forge_cache(self.cache_dir)` を呼ぶ guard を追加（MF-2 round 3、`gl-*.json` 単独残存ケースで silent regression を防ぐ）。**detector は sync.py に閉じ、local.py 側には GitLab リテラルを残さない**（import 文と関数呼び出しのみ）
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

ベースライン計測の全コマンドを再実行し、§ ベースライン計測 § 許容除外規則（MF-1 / MF-3 round 3）に従って目標値に到達していることを確認:

- `wc -l kaji_harness/providers/gitlab.py` → ファイル不在
- `grep -rln -Ei "gitlab\|glab\|gl:" kaji_harness/ --include="*.py" | grep -v "^kaji_harness/sync.py$" | wc -l` → **0**（sync.py を除外して評価。許容除外規則 § 2）
- `grep -cn -Ei "gitlab\|glab" kaji_harness/sync.py` → **≤ 8**（migration detector + recovery エラー文）
- `grep -n "_detect_legacy_forge_cache" kaji_harness/sync.py | wc -l` → **≥ 2**（定義 + caller、detector 関数が実在することの確認）
- `find tests -name "*gitlab*"` → 空
- `rg -n -i "GitLabProvider|GitLabProviderError|GitLabProviderConfig|sync_from_gitlab" tests/ --type py` → 0（コード symbol のみ。historical comment は除外）
- `grep -rln -Ei "gitlab\|glab" docs/ --include="*.md" | grep -v "^docs/adr/" | wc -l` → **0**（撤去 ADR を除外して評価。許容除外規則 § 3）
- `ls docs/adr/*remove-gitlab*.md` → **1 件**（撤去 ADR が新規追加されていること）
- `grep -rln -Ei "gitlab\|glab" .claude/ | wc -l` → 0
- `grep -in "gitlab" Makefile` → 0 件
- `grep -in "gitlab" pyproject.toml .kaji/config.toml` → 0 件

> CHANGELOG.md / `.kaji/issues/` / `kaji_harness/sync.py` 内 migration detector / `docs/adr/` 撤去 ADR は historical record / migration / 意思決定記録として再計測対象外（§ ベースライン計測 § 許容除外規則 1〜3 参照）。

#### Step 10: 品質ゲート

- `make check` PASS（ruff / mypy / pytest 全 green）。Step 2 で記録した base PASS/FAIL 集合と比較し、新規 failure / collection error が無いこと
- `make test-large` PASS（旧 `test-large-gitlab` を除く large テスト = `large_local` のみ）
- 任意で `make verify-docs` PASS（リンク切れ確認、特に `docs/cli-guides/gitlab-mode.md` への dead link が無いこと）

## 既存テストの棚卸し（MF-2 対応）

base commit `101bc01` 時点で `grep -rln -Ei "gitlab|glab|gl:" tests/ --include="*.py"` がヒットする tests/*.py を以下 3 カテゴリに分類し、改修方針を明示する。**ファイル名に `gitlab` を含まない直接依存テストも棚卸し対象**（MF-2 反映）。

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
| `tests/test_provider_overlay_divergence.py` (20+ hits) | **active 依存（MF-2 round 2 補完）**: `GitLabProviderConfig` import (`:22`)、`gitlab=GitLabProviderConfig(repo="group/project")` fixture (`:65`)、`lambda _p: "gitlab"` overlay 値 (`:184`)、`assert "gitlab" in warning` (`:191,322`)、`assert "'gitlab'" in captured.err` (`:372,393,409`)、`overlay.write_text('[provider]\ntype = "gitlab"\n')` (`:213,293`)、`[provider.gitlab]` fixture (`:271`)、Medium tests `:233-440` の `type=github / overlay=gitlab` divergence 再現環境。これらは「github と gitlab の 2 値が divergence の対比対象として **本質的に存在**する」前提のテストで、gitlab 単独削除では成立しない | gl:28 overlay divergence 検知ロジック自体（`provider_overlay_divergence_warning` 関数） |
| `tests/test_sync_from_github.py` (4 hits) | **active 依存（MF-2 round 2 補完）**: `GitLabProviderConfig` import (`:20`)、fixture 内 `gitlab=GitLabProviderConfig()` 引数 (`:60`) — `Config.provider` の構築時 default arg として参照されるため、`GitLabProviderConfig` 型削除に合わせて import 削除 + fixture から `gitlab=...` 引数除去 | `sync_from_github()` テスト本体 |

> **カテゴリ B の改修方針（更新）**: `tests/test_provider_overlay_divergence.py` の divergence test は **「2 つの異なる provider type が overlay 差分を引き起こすケース」を検知することが本質**であり、対比対象を `gitlab` から **別の有効 type（`local`）** に振り替えることで test 意図を維持する。具体的には:
>
> - 上記表の各 `"gitlab"` リテラルを `"local"` に書き換え
> - `gitlab=GitLabProviderConfig(...)` fixture を `local=LocalProviderConfig(machine_id="...")` に置換
> - `[provider.gitlab]` セクション fixture を `[provider.local]` に置換
> - これにより gl:28 が保護する「overlay 差分の沈黙的 provider 取り違え」検知ロジックは GitHub × Local の対比で温存される
>
> ただし「`github` と `local` の対比は overlay divergence の本質テストとして有効か」は実装フェーズで verify-design / review-code 観点で再確認する。**もし `github` × `local` の対比では gl:28 が保護する OB を再現できない**（例: `LocalProviderConfig` の overlay 経路が異なる）と判明したら、divergence test の意義そのものが GitLab 撤去で失われたと判定し、該当 test を「Category A（ファイル全削除）」に再分類する逃げ道を許容する。この判断は実装時の挙動確認結果に依拠する。

### カテゴリ C: コメント / docstring の historical reference のみ修正

GitLab に関する実コードは含まないが、コメントや docstring 内で過去 Issue ID（`gl:21` / `gl:34` 等）や「github/gitlab provider テスト」の対比表現を含む。**コメント書換またはそのまま historical 残置**を選択。

| ファイル | 修正方針 |
|---------|---------|
| `tests/test_cli_main.py` / `tests/test_cli_streaming_integration.py` / `tests/test_cli_timestamp.py` / `tests/test_default_branch.py` / `tests/test_preflight.py` / `tests/test_prompt_builder.py` / `tests/test_provider_guard_large_local.py` / `tests/test_provider_type.py` / `tests/test_providers_github.py` / `tests/test_providers_local.py` / `tests/test_resolve_main_worktree.py` / `tests/test_runner.py` / `tests/test_runner_before.py` / `tests/test_skill_remote_placeholder.py` / `tests/test_skill_validation.py` / `tests/test_timeout_config.py` / `tests/test_verdict_e2e.py` / `tests/test_workdir_config.py` / `tests/test_workflow_execution.py` / `tests/test_workflow_requires_provider.py` / `tests/test_local_cli_large_local.py` / `tests/test_pr_bare_provider.py` | コメント内の `gl:N` Issue ID は **historical reference として残置許容**（過去 Issue の作業跡を示すため）。ただし「github/gitlab provider テスト」のような「現在も両方サポートしている」と読める表現は GitHub 単独前提に書き換え。docstring 中の `provider.type='gitlab'` 例示は削除 |

> **MF-2 round 2 反映**: `tests/test_provider_overlay_divergence.py` および `tests/test_sync_from_github.py` を Category C（コメントのみ）から **Category B（active 依存）** に再分類した。base での `grep -i "gitlab"` ヒットだけでは "active vs comment-only" を区別できなかったため、各ファイルの該当行を実検査して fixture / assertion / lambda / overlay literal の用途を確認したうえで分類しなおした。同様の見落としを避けるため、再計測コマンドに「コード symbol regex（`GitLabProvider|GitLabProviderError|GitLabProviderConfig|sync_from_gitlab`）」を含め、historical comment と active 依存の区別を機械的に行う方針は維持する。

### 再計測と検出条件

撤去完了時の再計測は以下コマンドで（§ ベースライン計測 § 許容除外規則と一貫）:

```bash
# 1. kaji_harness/ 本体に GitLab 参照が残っていないこと（カテゴリ A/B の対象）
#    sync.py の legacy cache migration detector は許容（規則 § 2）
rg -n -i "gitlab|glab|gl:" kaji_harness/ --type py | rg -v "^kaji_harness/sync.py:"   # → 0 行

# 2. tests/ で GitLab 参照が残っていないこと（カテゴリ A/B の対象、C は historical 許容）
rg -n -i "GitLabProvider|GitLabProviderError|GitLabProviderConfig|sync_from_gitlab|provider.type.*gitlab|normalize_id.*gitlab" tests/ --type py   # → 0 行

# 3. docs / .claude / Makefile / pyproject.toml / .kaji/config.toml が clean
#    docs/adr/ の撤去 ADR は許容（規則 § 3）
rg -ln -i "gitlab|glab" docs/ --type md | rg -v "^docs/adr/"   # → 0 行
rg -n -i "gitlab|glab" .claude/ Makefile pyproject.toml .kaji/config.toml   # → 0 行
```

検出 1 / 2 / 3 すべてで **0 行**を改修完了条件とする。`kaji_harness/sync.py` 内 hits（≤ 8）と `docs/adr/*remove-gitlab*.md`（1 ファイル）は別途許容範囲内であることを規則 § 2 / § 3 の検証 regex で確認する。

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

#### 新規 bridging test（cache migration、MF-4 / MF-2 round 3 反映）

`_detect_legacy_forge_cache()` の 3 ケース parametrize で legacy cache 検出 fail-fast をカバーする（`tests/test_sync_from_github.py` または新規 `tests/test_legacy_forge_cache_detection.py` に追加）。

1. **case (a) AND (b): meta + gl-*.json 両方残存**: `tmp_path/.kaji/cache/.sync-meta.json` に `{"forge": "gitlab", ...}` + `gl-42.json` を配置 → `kaji sync status` で `SyncError` raise を assert、recovery 文に `rm -f .kaji/cache/gl-*.json` と `rm -f .kaji/cache/.sync-meta.json` の **両方** が含まれることを確認
2. **case (a) のみ: meta 単独残存**: meta のみ配置（`gl-*.json` 無し）→ `SyncError` raise、recovery 文に meta 削除行が含まれることを確認
3. **case (b) のみ: gl-*.json 単独残存（MF-2 round 3 新規）**: meta 不在、`gl-42.json` だけ配置 → `kaji sync status` で `SyncError` raise、recovery 文に `gl-*.json` 削除行が含まれることを確認。**追加で `view_cached_*` / list 経路からも同じ `SyncError` が raise されることを 1 ケース確認**（base の `providers/local.py:694-698,818-881` で list 表示されていた entry が無言で消えない silent regression 防止）

すべてのケースで raise される `SyncError` メッセージは `"legacy GitLab cache"` を必ず含むことを共通 assert で確認する（MF-1 の `kaji_harness/sync.py` 許容ヒット範囲内）。

### Large テスト

- **削除対象**: `tests/test_large_gitlab/` ディレクトリ全体（カテゴリ A）
- **既存テスト流用**: `tests/test_local_cli_large_local.py` / `tests/test_provider_guard_large_local.py`（`large_local` marker、`make test-large-local` 経路）
- **新規 Large テスト**: 不要（GitHub 実 API E2E は base に未存在、scope 混在禁止）

### 振る舞い非変更の保証（refactor 固有）

- safety net 評価対応表（上記）に列挙した既存テストが撤去前後で同一の PASS / FAIL 集合を保つことを Step 2（safety net 確認）と Step 9（再計測）で確認
- bridging test 4 本（Small 3 本 + Medium 1 本）は **撤去対象** に対する fail-fast 保証のみ。GitHub 経路 / LocalProvider GitHub 経路の挙動変更を意図的に避ける
- ベースライン計測値（Step 1）と再計測値（Step 9）の diff を Issue コメントに記録
- **`make check` 集合の strict subset 関係**（MF-3 round 2 訂正）: HEAD の `make check` は base の strict subset (`base_set \ removed_set`)。`removed_set` は GitLab 撤去対象テスト（カテゴリ A 全件 + カテゴリ B selective 削除部分）のみで、保護対象テストを含まない。Step 2 で `pytest --collect-only` を base / HEAD で取得して diff を確認し、`removed_set` が想定通りであることを実証する。`base_set \ removed_set` の PASS / FAIL は base の同集合と同一であることをもって振る舞い非変更（スコープ限定）を実証する

### `docs/dev/testing-convention.md` の 4 条件マッピング

本変更は実行時コード変更を含むため、「恒久テストを追加しない理由」を提示する義務はない（実行時コード変更時は Small/Medium/Large の検証観点定義が必要、本設計書で上記の通り定義済み）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | あり（新規 ADR 1 本追加） | 「GitLab forge 対応撤去」の ADR を 1 本追加（仮称 `docs/adr/NNNN-remove-gitlab-forge.md`、番号は実装時に既存 ADR の最大番号 + 1）。撤去理由 = Issue #184 cycle 枯渇の根本原因解消、再発防止のための forge 単一化方針。**本 ADR ファイル本文には `GitLab` 言及を残す**（§ ベースライン計測 § 許容除外規則 § 3 により再計測対象外）|
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
| sync 実装 | `kaji_harness/sync.py:1-100, 266-321, 596-645` | `sync_from_gitlab()` 撤去箇所、`forge="gitlab"` デフォルト変更箇所、`sync_status()` の `forge == "gitlab"` 分岐（legacy cache migration detector への書き換え対象） |
| LocalProvider GitLab cache | `kaji_harness/providers/local.py:347-816` | GitLab cache 経路（dead code 化して削除） |
| CLI dispatch | `kaji_harness/cli_main.py:36, 92-110, 944, 1028, 1556-1574` | `kaji sync from-gitlab` / `_handle_*_gitlab` / `_forward_to_glab` |
| test marker | `pyproject.toml:71` | `large_gitlab` marker 定義 |
| Makefile target | `Makefile:2, 17-20, 34-41, 54-60` | `test-large-gitlab` target 周辺 |
| `.kaji/config.toml` | `.kaji/config.toml:14-16` | `type = "gitlab"` デフォルトと `[provider.gitlab]` セクション |
| Refactor 設計ガイド | `.claude/skills/_shared/design-by-type/refactor.md` | ベースライン計測・safety net・振る舞い非変更保証の手順論 |
| Testing 規約 | `docs/dev/testing-convention.md` | Small/Medium/Large の判定基準と恒久テスト要否 |
| CLAUDE.md 規約 | `CLAUDE.md:61-65, 91, 146` | 撤去対象の `## Git & GitLab` セクション本体 |
