---
status: draft
phase: 3
parent: design.md
created: 2026-05-05
---

# [設計] kaji local mode — Phase 3: LocalProvider 実装

本書は `draft/design/local-mode/design.md` の Phase 3 範囲を補完する。`Provider` 抽象 / `LocalProvider` の責務 / ID 採番アルゴリズム / `normalize_id` / config schema 等の詳細決定は **design.md を正本**とし、本書では **design.md L918 等で「Phase 3 開始時に決定」と先送りされた論点** と、**実装ステップ分解・コミット粒度・ロールアウト戦略** に集中する。

## Primary Sources（一次情報）

| 種別 | 参照 | 役割 |
|------|------|------|
| 親設計書 | `draft/design/local-mode/design.md` | provider 抽象 / ID 採番 / config / file layout / BCP の詳細決定。本書は重複させない |
| Phase 1 実装 | `draft/design/local-mode/phase1-implementation-report.md` | `kaji issue/pr` ラッパー + `kaji run` の issue 引数 str 化（完了済） |
| Phase 2 設計 | `draft/design/local-mode/phase2-design.md` | Skill 改修の正本マッピング、9 変数体系のうち `issue_id` / `issue_ref` の 2 変数のみ Phase 2 で完成（残 7 を Phase 3+ に延期） |
| Phase 2-B 実装報告 | `draft/design/local-mode/phase2b-implementation-report.md` | § 4.4 で Phase 3 に持ち越された残 7 変数の整理。`issue_id` / `issue_ref` 注入経路（`kaji_harness/prompt.py`）の前例 |
| 既存実装 | `kaji_harness/{config.py, prompt.py, cli_main.py}` | Phase 1-2 で構築済の wrapper / context 注入経路。本 Phase はここに `providers/` package を追加 |
| 既存規約 | `.claude/skills/issue-start/SKILL.md:32-33` | branch / worktree 命名規約（`<prefix>/<id>` / `kaji-<prefix>-<id>`）の正本 |
| testing 規約 | `docs/reference/testing-size-guide.md` | Small / Medium / Large の境界。CliRunner = Medium、subprocess = Large |

## 概要

Phase 3 は本機能の **end-to-end 動作開始点**である。Phase 1-2 で整えた `kaji issue/pr` wrapper と Skill 改修の上に `LocalProvider` を載せ、`provider=local` で `/issue-design` 〜 `/issue-close` を完走可能にする。同時に `provider.type` の fail-fast 化（破壊的変更）を発動する。

**本 Phase で完了させる項目**:

1. `kaji_harness/providers/` package 新設（`base.py` / `models.py` / `github.py` / `local.py` / `__init__.py`）
2. `LocalProvider` 実装（CRUD + `is_readonly`、cache reader、glob による Issue dir 解決）
3. ID 採番ロジック（`next_local_id` + POSIX flock + machine_id 検証）
4. `normalize_id` および `ResolvedId` 導入
5. `config.py` に `[provider]` section の必須化と fail-fast 化を追加
6. `.gitignore` に `.kaji/config.local.toml` を追加
7. **`IssueContext` 導入と 5 変数（`issue_input` / `branch_prefix` / `branch_name` / `worktree_dir` / `design_path`）の provider 経由注入**
8. `feature-development-local.yaml` workflow 追加（local mode を主運用可能にする）
9. `kaji local init` 導入（local mode 初期化、machine_id 候補生成、`.gitignore` 整備）
10. Windows 暫定挙動（platform 検出 + warning）
11. CHANGELOG / migration guide ドラフト作成
12. Small / Medium / Large-local テストの整備

**out-of-scope**（Phase 4-5 へ）:

- `pr-*` Skill の bare provider エラー化（`pr_id` / `pr_ref` 含む）
- `kaji sync from-github` / `kaji sync local-to-github-plan`
- BCP runbook
- Windows の本格対応（`portalocker` 化など）

## 背景・目的

### Phase 2-B 完了時点の状況

- `kaji issue` / `kaji pr` ラッパーが `gh` 互換で動作（Phase 1）
- 全 Skill の `gh` → `kaji` 置換完了（Phase 2-A）
- `issue_id` / `issue_ref` 2 変数の `prompt.py` 経由注入完了（Phase 2-B）
- 残 7 変数（`issue_input` / `branch_prefix` / `branch_name` / `worktree_dir` / `design_path` / `pr_id` / `pr_ref`）は未着手で Skill 自前計算が温存されている
- `.kaji/config.toml` には `[provider]` セクションが**未追加**。`kaji issue` / `kaji pr` は config なしで `gh` を呼ぶ暫定挙動で動作している

### Phase 3 が解くべき問題

| 問題 | 影響 | Phase 3 での扱い |
|------|------|-----------------|
| `LocalProvider` が存在しない | `provider=local` を選択不能 → BCP 機能ゼロ | 本 Phase の主目的 |
| ID 採番アルゴリズム未実装 | local Issue 作成不能 | flock + counter + 既存 dir max 統合 |
| `normalize_id` 未実装 | CLI 層が `local-pc1-1` / `gh:153` を受理できない | `providers/__init__.py` で実装 |
| `[provider]` config の fail-fast 化未実施 | 暗黙挙動（gh fallback）が残存し、user の設定意図が曖昧 | 破壊的変更として有効化、migration guide で告知 |
| `.kaji/config.local.toml` が gitignored でない | machine_id 誤コミットの構造的リスク | `.gitignore` 追加 |
| 5 変数の供給経路未決定 | Skill 自前計算が provider 知識（local prefix 等）を持つ歪み | 本 Phase で `IssueContext` 経由に統一 |
| local mode 用 workflow が存在しない | 長期 local-first 運用で Skill 個別起動に依存する | `feature-development-local.yaml` を本 Phase に前倒し |
| local mode 初期化が手順依存 | machine_id 重複、config.local.toml 未 ignore、初期設定漏れが起きる | `kaji local init` で初期化手順を CLI 化 |
| dev repo（`kaji/main`）自身の `.kaji/config.toml` が現在 `[provider]` セクションを持たない | 本 Phase 完了直後に自分の repo が壊れる | ロールアウト戦略で別途扱う（後述 § ロールアウト戦略） |

### Phase 3 が解かない問題（後続スコープ）

- **PR 概念の bare provider エラー化** — `pr-fix` / `pr-verify` Skill が `provider=local` で skip / error する仕組みは Phase 4 でまとめて。`pr_id` / `pr_ref` の 2 変数化と一体実施
- **`kaji sync` 系** — Phase 5（GitHub 復旧後の実用価値）
- **既存 GitHub Issue（`#1` 〜 `#170` 程度）の参照** — Phase 3 開始時点では `.kaji/cache/issues/*.json` は **空**（Phase 5 の `kaji sync from-github` 未実装、かつ GitHub API へアクセス不能）。本 Phase 完了後も `kaji issue view gh:153` は cache 不在として明示エラーになる。既存 Issue の本文を Phase 3 期間中に参照する必要がある場合は、user が手動で当該 JSON を `.kaji/cache/issues/N.json` に投入する運用とし、Phase 3 自体に sync 経路は追加しない。この前提は本 Phase の `IssueContext` 実装でも踏襲する（GitHubProvider は cache 不在時に明示エラー、推測 fallback は持たない）

## スコープ

### in-scope

| 項目 | 内容 |
|------|------|
| `providers/` package 新設 | `base.py` / `models.py` / `github.py` / `local.py` / `__init__.py` |
| Provider Protocol | `IssueProvider` + `IssueContext` 解決。`PullRequestProvider` / `ReviewRequestProvider` は Phase 4 |
| GitHubProvider | 既存 `kaji issue/pr` 実装の集約。挙動は不変、構造のみ provider 化 |
| LocalProvider | CRUD（create / view / edit / comment / close / list）+ `view_json` / `list_json` + `is_readonly` + cache reader |
| ID 採番 | `next_local_id`（counter + 既存 dir max + flock） |
| machine_id 検証 | `[a-z0-9]{1,16}` 文法 + `kaji local init` / `kaji config set machine_id` 経路 |
| `normalize_id` | `ResolvedId` 導入、CLI 層の分岐起点 |
| `resolve_issue_dir` | glob ベース、重複検出エラー |
| config fail-fast | `provider.type` / `provider.local.machine_id` / `provider.local.default_branch` / `provider.github.repo` の必須化 |
| エラーメッセージ | design.md L406-435 の仕様（書くべき場所と内容を完全提示） |
| `.gitignore` 更新 | `.kaji/config.local.toml` 追加 |
| IssueContext | `issue_input` / `branch_prefix` / `branch_name` / `worktree_dir` / `design_path` を provider 経由で正本化 |
| `kaji local init` | local mode 初期化、machine_id 候補生成、重複警告、config.local.toml 作成、`.gitignore` 確認 |
| `feature-development-local.yaml` | `provider=local` で日常開発を回す workflow。PR 作成 step は含めず、local close までを対象 |
| Windows 暫定挙動 | platform 検出 + 警告メッセージ + flock skip |
| Small / Medium テスト | design.md § テスト戦略の該当範囲 |
| Large-local テスト | subprocess 起動の E2E + multi-PC simulation |
| CHANGELOG / migration guide ドラフト | dev repo 自身の移行手順を含む |

### out-of-scope

| 項目 | Phase |
|------|-------|
| `pr-fix` / `pr-verify` の bare provider エラー化 | 4 |
| `pr_id` / `pr_ref` の 2 変数化 | 4 |
| `kaji sync from-github` | 5 |
| `kaji sync local-to-github-plan` | 5 |
| BCP runbook | 5 |
| `portalocker` 等での Windows 本対応 | 後続（オープン論点） |
| `kaji issue rename` 等の slug 操作 CLI | 後続（オープン論点） |
| EPIC orchestration の `_epics/` 連携 | loose coupling、別 ADR |

## 未決事項の決定

design.md が「Phase 3 開始時に決定」と明示している項目を本節で確定する。

### 1. 5 変数（`issue_input` / `branch_prefix` / `branch_name` / `worktree_dir` / `design_path`）の正本化方針

#### 候補

design.md L918 が提示する 3 案に、長期 local-first / GitLab 移行を前提にした案 D を追加する：

| 案 | 説明 | 工数 | リスク |
|----|------|------|--------|
| A. **Skill 内計算継続** | Skill 自前で `prefix` / `slug` を持ち、`issue_id` から合成。`prompt.py` は `issue_id` / `issue_ref` のみ供給（現状維持） | 小 | provider 知識（local prefix の文字列形式）が Skill 側に分散する |
| B. **`prompt.py` で全変数を生成し供給** | `ResolvedId` + workflow 文脈から 5 変数を組み立てて Skill に注入 | 中-大 | `prefix` / `slug` の供給源（workflow YAML / Issue frontmatter / CLI 引数）が必要で、現状の Workflow / Step モデルに穴がある |
| C. **Issue frontmatter から読み出し** | `.kaji/issues/<id>-<slug>/issue.md` の frontmatter に `prefix` を持たせ、`prompt.py` がそれを読んで注入 | 中 | `provider=github` 時は cache JSON に `prefix` が無く、両系統で生成ロジックが分岐 |
| D. **`IssueContext` 経由で provider が供給** | Provider または harness が provider 非依存の `IssueContext` を作り、`prompt.py` は `issue_input` / `branch_prefix` / `branch_name` / `worktree_dir` / `design_path` を注入する。local は issue frontmatter、GitHub は label / cache、将来 GitLab は GitLab provider から同じ context を返す | 中-大 | Phase 3 の実装範囲が拡大する。`IssueContext` schema、GitHub cache への不足情報補完、既存 Skill の変数移行を同時に設計する必要がある |

#### 決定: **D**

GitHub 復旧が長期化し、local mode を数週間以上の主運用として使う可能性が高い。また、GitLab 移行を現実的な選択肢として残す必要がある。この前提では A + 限定 B は採用しない。Phase 3 では **案 D: `IssueContext` 経由で provider が供給** を採用する。

**判断理由**:

- Skill 内計算を温存すると、`branch_prefix` / `branch_name` / `worktree_dir` / `design_path` の命名規則が Skill 群へ分散する。短期 BCP では許容できるが、長期 local-first 運用では provider 追加・命名変更・GitLab 移行時の修正面が広がる
- `prompt.py` が全変数を直接生成する B 案は、`prefix` / `slug` の取得責務を `prompt.py` に寄せすぎる。`prompt.py` は injection 層に留め、provider / harness 側で `IssueContext` を解決する方が責務境界が明確になる
- C 案の frontmatter 正本化は local には自然だが、GitHub / GitLab では Issue label、remote API、cache など別の正本が存在する。frontmatter そのものではなく `IssueContext` を正本 interface にすると、provider ごとの差異を吸収できる
- GitLab では GitHub Issue / PR と同じ番号・label・merge request の扱いにならない可能性がある。Skill へ文字列計算を残すより、`GitLabProvider` が同じ `IssueContext` contract を満たす方が移行コストを抑えられる

**設計変更**:

1. `kaji_harness/providers/models.py` に `IssueContext` を追加する
   - `issue_id: str`
   - `issue_ref: str`
   - `issue_input: str`
   - `slug: str` — Issue ディレクトリ末尾 / `design_path` / 将来の slug 同梱 worktree 命名で使用
   - `branch_prefix: str`
   - `branch_prefix_fallback: bool = False` — `type:*` label 不在で `chore` fallback された場合に True。呼び出し側が warning 表示等の判断に使用
   - `branch_name: str`
   - `worktree_dir: str`
   - `design_path: str`
   - `provider_type: str`
2. `IssueProvider` に `resolve_issue_context(issue_id: str) -> IssueContext` を追加する
3. `prompt.py` は `IssueContext` を受け取り、Skill へ 5 変数を注入する。`prompt.py` 自体では provider 固有の label / slug 解決を行わない
4. `LocalProvider` は `.kaji/issues/<id>-<slug>/issue.md` frontmatter から `branch_prefix` / `slug` を読む。不足時は fail-fast し、修正すべき frontmatter を明示する
5. `GitHubProvider` は `kaji issue view --json labels,title,number` 相当の情報、または `.kaji/cache/issues/*.json` から `IssueContext` を組み立てる。`branch_prefix` は既存 Skill の type label 推論規則と同じ mapping を provider 側へ移す
6. 将来の `GitLabProvider` は GitLab Issue / label / MR の差異を `IssueContext` へ変換する。Skill markdown は GitHub / GitLab / local の違いを知らない
7. Skill markdown 内の `[issue-input]` / `<issue_input>` / branch / worktree / design path 生成箇所は、Phase 3 の同一変更群で `IssueContext` 由来の context 変数へ寄せる

**スコープ調整**:

- `feature-development-local.yaml` は Phase 4 から Phase 3 へ前倒しする。長期 local-first 運用では Skill 個別起動を日常運用の前提にしない
- `kaji local init` を Phase 3 に含める。`provider.type` / `provider.local.machine_id` / `provider.local.default_branch` / `.gitignore` / local artifacts directory をまとめて初期化する
- machine_id は user 完全責務ではなく、初期化時に候補生成と重複警告を行う。完全な分散一意性までは保証しないが、同一 repo 内での明らかな重複は検知対象にする
- `github_cache` など GitHub 固有名の internal kind は、将来 GitLab cache を受け入れられるよう `remote_cache` / `external_cache` 等へ寄せる
- `PullRequestProvider` は GitHub PR 固有の抽象にしない。GitLab merge request を扱えるよう、Phase 4 の PR/MR 設計時に `ReviewRequestProvider` 等の上位概念を検討する

**Phase 4 への申し送り**: `pr_id` / `pr_ref` は Phase 4 で扱う。ただし、Issue 系の 5 変数は Phase 3 で `IssueContext` 化を完了し、PR/MR 設計へ持ち越さない。

### 2. POSIX flock の例外設計

`fcntl.flock(fd, fcntl.LOCK_EX)` は Linux/macOS で advisory lock を取る。本 Phase での例外設計：

| 状況 | 挙動 |
|------|------|
| 通常取得成功 | カウンタ更新 → `next_local_id` 計算 → Issue dir 作成 → release |
| 別プロセスが lock 中 | `LOCK_EX` で **block して待機**（タイムアウトなし）。CLI は通常完了する |
| `fcntl.flock` 自体が `OSError` | エラーメッセージで停止: `"flock unavailable on this filesystem (NFS / FUSE?). Set provider.local.machine_id to a unique value per process and retry."` |
| stale lock（プロセス kill 残り） | advisory lock は fd 解放で自動解除されるため stale は構造的に発生しない（kernel 管理） |
| Windows | `import fcntl` 自体が失敗するので platform 検出で skip（後述 § 3） |

**実装方針**:

- `LocalProvider.create_issue` 内で context manager を使う:
  ```python
  with _counter_lock(counter_path):
      n = next_local_id(...)
  ```
- timeout は導入しない（個人 4 PC 想定、deadlock リスクは極小）。導入するなら `portalocker` 移行と一体（オープン論点）
- lock ファイルは counter ファイル自身を流用（別ファイルにすると stale 残存リスクが発生）

### 3. Windows 暫定挙動

design.md L691 の「警告して続行」を具体化する。

**検出ロジック**:

```python
import sys
if sys.platform == "win32":
    # flock を skip。多重起動時の race は user 責務
    ...
```

**警告メッセージ**（`LocalProvider` 初期化時に 1 度のみ stderr へ）:

```
WARNING: kaji local mode is running on Windows without process-level locking.
If you launch multiple kaji processes simultaneously on this PC, ID
collisions are possible. As a single user with serial workflow this is
typically safe. Full Windows support is tracked as a future work item.
```

**警告抑制**: 環境変数 `KAJI_SUPPRESS_WIN_WARNING=1` で suppress 可能。CI / dotfiles で 1 度承認したら毎回出さない選択肢を残す。

**範囲**: 警告のみ。`next_local_id` 自体は呼ぶが flock 部分を no-op にする。同一 PC で 2 並列起動した場合は両者が同じ `n` を採番し、Issue dir 作成（mkdir）の段で **片方が `FileExistsError`** で落ちる（最低限の事故防止）。

### 4. fail-fast ロールアウト戦略

`provider.type` 必須化は dev repo（`kaji/main`）自身に最初に当たる。dev repo の `.kaji/config.toml` に現時点で `[provider]` セクションは存在しない。

**ロールアウト順序**:

1. **PR-3a**: `providers/` package 追加 + `ResolvedId` / `IssueContext` / `IssueProvider.resolve_issue_context` の骨格。**この時点では `cli_main.py` の dispatcher を新 provider 経路に切り替えない**（既存の直接 `gh` 呼び出しを維持）。Small テストはここで緑化
2. **PR-3b**: `LocalProvider` / `GitHubProvider` 実装 + `normalize_id` + `remote_cache` reader。Medium テストはここで緑化
3. **PR-3c**: `cli_main.py` を `get_provider(config)` 経由に切り替え、`prompt.py` を `IssueContext` 注入へ切り替える。**ただし `provider.type` 未設定時は WARN を出して `github` に fallback**（Phase 1-2 暫定挙動の継続）
4. **PR-3d**: `kaji local init` + `feature-development-local.yaml` + Skill の 5 変数利用への移行 + dev repo の `.kaji/config.toml` / `.gitignore` dogfooding
5. **PR-3e**: fail-fast 化を有効化（fallback 削除）。CHANGELOG / migration guide ドラフトを同梱（**実装完了 — 2026-05-06、`feat/local-phase3e` ブランチ。`phase3e-design.md` および `phase3e-implementation-report.md` を参照**）

**根拠**:

- PR-3a で provider 抽象と `IssueContext` contract を先に固定する
- PR-3b で provider 実装を入れても dispatcher 未切替なので既存 user / dev repo は壊れない
- PR-3c で本番経路に乗せても fallback で既存 user / dev repo は壊れない
- PR-3d で local-first の実運用経路を dogfooding し、fail-fast 前に dev repo 自身を新 schema に移行する
- PR-3e で破壊的変更を発動。CHANGELOG で明示告知

各 PR は `make check` 緑を維持する。PR-3e 直前で `.kaji/config.toml` が既に新 schema を持ち、local workflow が動作する状態を作ることで、自分自身の repo が壊れる窓を作らない。

**migration guide 文面（CHANGELOG ドラフト）**:

```markdown
## [Unreleased] kaji local mode Phase 3

### BREAKING CHANGE

`kaji issue` / `kaji pr` are no longer fall-back to the `gh` CLI when
`[provider]` is not configured. You must add the following to your
`.kaji/config.toml`:

    [provider]
    type = "github"

    [provider.github]
    repo = "<owner>/<repo>"

For GitHub-independent (local-first) development, run `kaji local init`
and see `docs/cli-guides/local-mode.md`. Local mode is a primary mode of
operation, not just a BCP fallback.

### Added

- LocalProvider for GitHub-independent issue management
- IssueContext-based context injection for local-first workflows
- `kaji local init` for local mode setup
- `feature-development-local.yaml` for GitHub-independent development
- ID normalization across `local-<machine>-<n>`, `<machine>-<n>`, numeric, and `gh:N` forms
- Fail-fast config validation with actionable error messages
- `.kaji/config.local.toml` (machine-specific, gitignored)
```

## 詳細設計（design.md からの差分のみ）

design.md に詳述された範囲は再記述しない。本 Phase 実装で**追加で固める**項目のみを記載する。

### `kaji_harness/providers/` のファイル粒度

| ファイル | 主な内容 | 行数目安 |
|----------|---------|---------|
| `__init__.py` | `get_provider(config) -> IssueProvider`、`normalize_id` / `ResolvedId`、`remote_cache` kind | ~140 |
| `base.py` | `IssueProvider` Protocol（`resolve_issue_context` 含む。PR/MR 系 protocol は Phase 4） | ~80 |
| `models.py` | `Issue` / `Comment` / `Label` / `IssueContext` dataclass | ~120 |
| `github.py` | `GitHubProvider`（既存 `cli_main.py` の `gh` 呼び出しを集約、label から `IssueContext` を解決） | ~320 |
| `local.py` | `LocalProvider` + `next_local_id` + `_counter_lock` + `resolve_issue_dir` + remote cache reader + `IssueContext` 解決 | ~480 |

PR/MR 系の Protocol 定義は Phase 4 で `base.py` に追加する。Phase 3 では GitHub PR 固有の `PullRequestProvider` を先に固定せず、GitLab merge request を含められる `ReviewRequestProvider` 等の上位概念を Phase 4 で検討する。

### `cli_main.py` の dispatcher 変更点

Phase 1-2 では `kaji issue view N` 等が `subprocess.run(["gh", "issue", "view", N, ...])` を直接呼んでいる。Phase 3 では:

```python
def issue_view(ctx, issue_id, ...):
    config = ctx.obj["config"]
    provider = get_provider(config)
    rid = normalize_id(issue_id, provider_name=config.provider.type, machine_id=config.provider.local.machine_id)
    if rid.kind == "remote_cache":
        # remote cache reader 経路（local provider 配下、read-only）
        ...
    else:
        provider.view(rid.value)
```

**注意**: PR-3b の段階では `provider.type` 未設定 → WARN + `github` fallback。`get_provider` 内に fallback ロジックを集約する（CLI 層は分岐を持たない）。

`prompt.py` は CLI dispatcher とは別に `provider.resolve_issue_context(issue_id)` を呼び、返却された `IssueContext` を context 変数として注入する。`prompt.py` では provider 固有の label / slug / cache 解決を行わない。

### `IssueContext` の解決タイミングと cache 戦略

`provider.resolve_issue_context(issue_id)` の呼び出し方針:

- **呼ぶ頻度**: `kaji run` プロセス起動時に **1 度だけ** resolve し、以降の Skill 起動はすべて同じ `IssueContext` インスタンスを参照する。`prompt.py` 内部で in-memory cache（`functools.lru_cache` 相当）を持つ
- **cache 範囲**: プロセス境界。`kaji run` を再起動すれば再 resolve される
- **状態変化への対応**: workflow 実行中に Issue label が外部から変更されても `IssueContext` は固定。これは「同一 workflow 実行内では `branch_name` / `worktree_dir` が安定すべき」という運用要件と整合する。次回 `kaji run` で反映
- **GitHubProvider の cost**: 1 回の `gh issue view --json labels,title,number` を kaji run 起動時に走らせる。Phase 1-2 では Skill が個別に `gh issue view` を呼んでいたため、むしろ呼び出し回数は減る
- **失敗時**: `resolve_issue_context` が IO エラーで失敗した場合は kaji run 自体を fail-fast。半端な `IssueContext` で Skill を起動しない

### `branch_prefix` mapping の正本化

`branch_prefix`（`feat` / `fix` / `docs` 等）は GitHub Issue label から導出する必要がある。**正本マッピングは `.claude/skills/issue-create/SKILL.md:37-44` に既に存在する**ため、Phase 3 ではこの table を Python module へ移植し、Skill 側の table と二重管理にならない構造を採る。

**現行の正本（`.claude/skills/issue-create/SKILL.md:37-44`、全 8 種）**:

| `branch_prefix` | GitHub label |
|----------------|--------------|
| `feat` | `type:feature` |
| `fix` | `type:bug` |
| `refactor` | `type:refactor` |
| `docs` | `type:docs` |
| `test` | `type:test` |
| `chore` | `type:chore` |
| `perf` | `type:perf` |
| `security` | `type:security` |

**Phase 3 での扱い**:

- 配置場所: `kaji_harness/providers/_mappings.py` に `LABEL_TO_PREFIX: dict[str, str]` として hard-code（config 化はしない。理由: Skill / kaji 規約の一部であり、user 設定対象ではない）
- 既存 Skill の table（`issue-create/SKILL.md:37-44` および `issue-create/templates/issue-*.md` の冒頭表記）は **正本性を Python module へ移譲**し、Skill markdown 上はドキュメントとして残す。整合性は Phase 3 のテストで grep ベース検証する
- 複数 type label が付いた Issue（`type:feature` と `type:enhancement` 同居等）の優先順位: **`LABEL_TO_PREFIX` の dict 順を優先順位とみなし、最初に match した label を採用**。tie-break は決定的（dict は Python 3.7+ で挿入順保持）
- `type:*` label が一つも無い場合: **`branch_prefix=chore` を fallback** として返し、warning を stderr に出す（fail-fast にしない理由: 既存 Issue が label 不備でも CRUD 操作は阻害したくない）。fallback 時は `IssueContext.branch_prefix_fallback: bool = True` field で呼び出し側が把握可能

### `slug` の供給ルール

`slug` は `branch_name` / `worktree_dir` / `design_path` の合成に必要。provider 別に供給ルールを定める:

**LocalProvider**:

- `kaji issue create --slug <slug>` は **optional**（Phase 3-d preflight § 4 で必須から変更）。未指定時は GitHubProvider と同じく title を sanitize して slug を導出する。`derive_slug_from_title()` の結果が空（記号のみ等）になる場合は `untitled` を使用
- frontmatter `slug` field に保存。以降は `IssueContext.slug` として frontmatter から read
- 文字制約: `^[a-z0-9][a-z0-9-]{0,39}$`（lowercase + 数字 + hyphen、先頭は英数字、最大 40 文字）。違反は `kaji issue create` 時点で fail-fast
- ディレクトリ名: `.kaji/issues/local-<machine>-<n>-<slug>/`（design.md L446 と整合）

**GitHubProvider**:

- 既存 GitHub Issue は frontmatter を持たないため、cache JSON または直接 `gh issue view --json title` から **title を sanitize して slug を導出**する
- 導出ルール: title を lowercase 化 → 英数字以外を `-` に置換 → 連続 hyphen を圧縮 → 先頭末尾 hyphen 除去 → 40 文字に切り詰め
- 導出した slug は `IssueContext.slug` に格納するが、副作用を持たない（cache JSON には書き戻さない。Phase 5 の `kaji sync` で write-back 経路を別途検討）
- ただし既存 worktree が `.../kaji-<prefix>-<id>/` 形式（slug を含まない）で運用されている。Phase 3 では slug 未使用形式と slug 込み形式の両対応とし、**`worktree_dir` 生成は provider 別**: GitHub では `kaji-<prefix>-<id>` のまま（Phase 2-B 互換）、local では `kaji-<prefix>-<id>-<slug>` を採用候補とするが、**Phase 3 では LocalProvider も `kaji-<prefix>-<id>` 固定**として既存規約と統一する。slug を含めた worktree 命名は将来の改修候補（オープン論点）

### `kaji local init` 仕様

> **Cross-reference (Phase 3-d)**: 本節の Step 4-5 は `phase3d-design.md § 3` で
> **上書き**された。実装では active provider 値（`type` / `machine_id` /
> `default_branch`）をすべて `.kaji/config.local.toml` (gitignored) に書く。
> 本節のテキストは設計史としてそのまま残す。

local mode 初期化を user 手作業から CLI 化する。長期 local-first 運用で初期セットアップミス（machine_id 未設定、`.gitignore` 漏れ、config 不整合）を構造で防ぐ。

**コマンド**:

```
kaji local init [--machine-id <name>] [--default-branch <branch>] [--non-interactive]
```

**実行フロー**:

1. **既存設定の検査**:
   - `.kaji/config.toml` を読み、`[provider]` セクション有無を確認
   - `.kaji/config.local.toml` を読み、`provider.local.machine_id` 既存値を確認
   - 既存の `.kaji/issues/local-*-*` ディレクトリを glob し、**他 PC の machine_id 一覧**を抽出（重複検知用）
2. **machine_id 候補生成**:
   - `--machine-id` 明示時: その値を採用（`[a-z0-9]{1,16}` 検証は通す）
   - 未指定 + non-interactive: 以下の deterministic な順序で決定（フローは必ず成功する）:
     1. `socket.gethostname()` を sanitize（lowercase 化、英数字以外除去、16 文字切り詰め）。空でなく、かつ Step 1 で抽出した既存 machine_id 一覧と衝突しなければ採用
     2. 空または衝突する場合は `pc1` / `pc2` / … を順に試し、既存一覧と衝突しない最小 n を採用（`pcN` の N は 1〜∞ の整数。理論上ほぼ即決まる）
   - 未指定 + interactive: 上記 2 候補を提示して user 入力を促す
3. **重複警告**:
   - Step 1 で抽出した machine_id 一覧と候補が衝突する場合、stderr に warning 出力（fatal にはしない、user の意図的な選択を阻害しないため）
   - 例: `WARNING: machine_id 'pc1' is already used by existing issues local-pc1-3, local-pc1-7. Continue only if you intend to share the namespace.`
4. **`.kaji/config.toml` 作成 / 更新**:
   - `[provider]` セクション不在なら追記（`type = "local"`、`[provider.local] default_branch = "<--default-branch or main>"`）
   - 既存セクションがある場合は **既存値を尊重し追記しない**。Phase 3 では上書き flag を持たず、既存設定の修正は user が手動で行う（`--force` 等の上書き flag は将来の検討事項としてオープン論点に追加）
5. **`.kaji/config.local.toml` 作成**:
   - 既存ファイルがある場合は abort し、user に手動マージを促す（破壊しない）
   - 不在なら新規作成: `[provider.local] machine_id = "<決定値>"`
6. **`.gitignore` 確認**:
   - `.kaji/config.local.toml` 行が無ければ追記
   - 既に存在すれば no-op
7. **完了出力**:
   - 設定された machine_id / default_branch / 確認した既存 Issue 数を summary 出力
   - exit code 0

**例外**:

- `--machine-id` 値が `[a-z0-9]{1,16}` 違反 → exit 2 + メッセージ
- `.kaji/config.local.toml` 既存（重複初期化） → exit 3 + 手動マージガイド
- non-interactive で hostname sanitize が空文字でも Step 2.2 の `pcN` fallback で必ず決まるため、機械的失敗 case は無い（exit code は 0）

**対話モードの扱い**:

- `sys.stdin.isatty()` で判定。non-tty なら `--non-interactive` 同等動作
- `--non-interactive` 明示時は stdin を読まない（CI / automation 用途）

### `feature-development-local.yaml` step 構成

design.md L1146-1156 の表を Phase 3 で確定する。step 列挙:

**方針**: 既存 `.kaji/wf/feature-development.yaml` をベースに、最終 `pr` step を `close` step に差し替える。`name` / `execution_policy` / `cycles` / 各 step の `agent` / `model` / `on` などは現行 schema（`kaji_harness/workflow.py:46,68,196`）に準拠して既存 yaml と完全同型を維持する。

```yaml
# .kaji/wf/feature-development-local.yaml
name: feature-development-local
description: |
  provider=local 用の開発 workflow。
  issue-create / issue-start は事前に手動実行済みであることが前提。
  PR concept を持たないため、最終 step は issue-close
  （local merge + frontmatter 更新。design.md § local mode における /issue-close の手順）。
execution_policy: auto

cycles:
  design-review:
    entry: review-design
    loop: [fix-design, verify-design]
    max_iterations: 3
    on_exhaust: ABORT
  code-review:
    entry: review-code
    loop: [fix-code, verify-code]
    max_iterations: 3
    on_exhaust: ABORT

steps:
  # design 〜 final-check は既存 feature-development.yaml と完全同一。差分は最終 step のみ
  - id: design
    skill: issue-design
    agent: claude
    model: opus
    effort: medium
    on:
      PASS: review-design
      ABORT: end

  - id: review-design
    skill: issue-review-design
    agent: codex
    model: gpt-5.4
    effort: medium
    on:
      PASS: implement
      RETRY: fix-design
      ABORT: end

  - id: fix-design
    skill: issue-fix-design
    agent: claude
    model: opus
    resume: design
    on:
      PASS: verify-design
      ABORT: end

  - id: verify-design
    skill: issue-verify-design
    agent: codex
    model: gpt-5.4
    effort: medium
    on:
      PASS: implement
      RETRY: fix-design
      ABORT: end

  - id: implement
    skill: issue-implement
    agent: claude
    model: opus
    on:
      PASS: review-code
      RETRY: implement
      BACK: design
      ABORT: end

  - id: review-code
    skill: issue-review-code
    agent: codex
    model: gpt-5.4
    effort: medium
    on:
      PASS: final-check
      RETRY: fix-code
      BACK: design
      ABORT: end

  - id: fix-code
    skill: issue-fix-code
    agent: claude
    model: opus
    inject_verdict: true
    on:
      PASS: verify-code
      ABORT: end

  - id: verify-code
    skill: issue-verify-code
    agent: codex
    model: gpt-5.4
    effort: medium
    on:
      PASS: final-check
      RETRY: fix-code
      ABORT: end

  - id: final-check
    skill: i-dev-final-check
    agent: claude
    model: opus
    on:
      PASS: close       # ← 既存 yaml では PASS: pr。ここが local 差分
      RETRY: final-check
      ABORT: end

  # ↓↓↓ 既存 yaml の `pr` step（i-pr）を以下の `close` step（issue-close）に差し替え
  - id: close
    skill: issue-close
    agent: claude
    model: sonnet
    on:
      PASS: end
      RETRY: close
      ABORT: end
```

**既存 `feature-development.yaml` との差分**:

- 最終 step `pr`（`skill: i-pr`）を `close`（`skill: issue-close`）に差し替え。`final-check` の `PASS:` 遷移先も `pr` → `close` に変更
- それ以外の step（design / review-design / fix-design / verify-design / implement / review-code / fix-code / verify-code / final-check）は **既存 yaml と完全同一**
- `cycles` 定義も同一。学習コスト最小化のため既存構造を尊重
- `issue-close` Skill 側で provider=local 時の動作（`git merge --no-ff` + frontmatter 更新）を実装する。これは Phase 3 の Skill 改修範囲（後述 § Skill 改修対象範囲）に含める

**Skill 側で必要な対応**:

- `issue-close` Skill は provider=local の場合、design.md § local mode における `/issue-close` の手順 に従い `git merge --no-ff` + frontmatter 更新を行う。これは Phase 3 の Skill markdown 改修範囲に含める（後述 § Skill 改修対象範囲）

### Skill 改修対象範囲

Phase 2-B 後の現状で、本 Phase で touch すべき Skill markdown を grep で確定:

```
$ grep -rl '\[branch-name\]\|\[worktree-absolute-path\]\|\[design-path\]\|<branch-name>\|<worktree-absolute-path>\|<design-path>\|\[issue-input\]\|<issue-input>\|\[branch_prefix\]' .claude/skills/
```

**該当 21 ファイル**（2026-05-05 時点）:

- `.claude/skills/_shared/implement-by-type/{bug,feat,refactor}.md`
- `.claude/skills/i-dev-final-check/SKILL.md`
- `.claude/skills/i-doc-{final-check,fix,review,update,verify}/SKILL.md`
- `.claude/skills/i-pr/SKILL.md`
- `.claude/skills/issue-{close,design,fix-code,fix-design,implement,review-code,review-design,verify-code,verify-design}/SKILL.md`
- `.claude/skills/pr-{fix,verify}/SKILL.md`

**改修方針**:

- 5 placeholder（`[branch-name]` / `[worktree-absolute-path]` / `[design-path]` / `[issue-input]` / `[branch_prefix]`）を `[branch_name]` / `[worktree_dir]` / `[design_path]` / `[issue_input]` / `[branch_prefix]` に統一（Phase 2-B の `[issue_id]` / `[issue_ref]` と同形式）
- 山括弧形式（`<branch-name>` 等）も併せて検出・置換（phase2b-implementation-report.md § 9 で確立した方針）
- `pr-{fix,verify}` / `i-pr` は Phase 4 で provider 別エラー化するため、本 Phase では placeholder 置換のみ実施し、`provider=local` 時の動作変更は加えない
- 既存 mapping table（`issue-create/SKILL.md:37-44` および `templates/`）は markdown ドキュメントとして残し、Python module（`_mappings.py`）が正本であることを明記

**Step 表との対応**: 上記 21 ファイルの改修は Step 15「Skill markdown の 5 変数利用へ移行」に集約される。1-2 コミットの内訳目安: 1 コミット目で `_shared/` + `issue-*` 系（13 ファイル）、2 コミット目で `i-doc-*` + `i-dev-*` + `i-pr` + `pr-*`（8 ファイル）。

### Issue ファイル / コメントファイルの atomic 書き込み

design.md L932 で「cache の atomic rename」は明記されているが、`.kaji/issues/*` 側の atomic 性は触れていない。Phase 3 で次の方針を採る:

- `issue.md` / `comments/<seq>-<machine>.md` 書き込みは `*.tmp` → `os.replace` で atomic 化
- 部分書き込みが残らないため、git の add/commit が壊れた中間状態を取り込まない
- counter 書き込みも同様

実装は `local.py` 内に `_atomic_write(path, content)` ヘルパを置く。

### コメント seq 採番

design.md L931 でコメントファイル名は `<seq>-<machine>.md`。seq の採番ロジックを定める:

- 採番対象は単一 Issue の `comments/` 配下
- 既存ファイルから `^([0-9]+)-` を抽出した max + 1
- machine prefix が違っても seq 空間は共有（`0001-pc1.md` と `0002-pc2.md` のように交互採番）
- 同一 PC 内の同一 Issue へ並列 `kaji issue comment` するケースは flock しない（実用上ほぼ発生せず、衝突したら git merge で解決）

## 実装ステップ分解（PR 粒度とコミット境界）

| Step | 内容 | コミット粒度 | テスト |
|------|------|-------------|--------|
| 1 | `providers/{__init__,base,models}.py` 雛形 + `IssueProvider` Protocol + `IssueContext` / `models.Issue` 等 | 1 コミット | mypy 通過 |
| 2 | `normalize_id` + `ResolvedId` 実装 | 1 コミット | Small (provider × 入力の組合せ全網羅) |
| 3 | `IssueContext` 解決 contract 実装（`_mappings.py` の `LABEL_TO_PREFIX`、worktree/design path builder、`remote_cache` kind） | 1-2 コミット | Small (context builder 全パターン) |
| — | **PR-3a 完了**（contract 確定、provider 実装はゼロ） | — | `make check` + Small 緑 |
| 4 | `GitHubProvider` 実装（既存 `cli_main.py` の `gh` 呼び出しを移植、label から `IssueContext` 解決） | 2-3 コミット（CRUD / JSON / comments / context で分割） | Small (subprocess mock) |
| 5 | `LocalProvider` 実装 — Issue CRUD + frontmatter parse/serialize + atomic write + `IssueContext` 解決 + slug 検証 | 3-4 コミット | Medium (実 file I/O) |
| 6 | `kaji local init` 実装（config.local.toml 作成、machine_id 候補生成、重複警告、`.gitignore` 確認） | 1-2 コミット | Medium (CliRunner + tmp repo) |
| 7 | `next_local_id` + `_counter_lock` + machine_id 検証 | 1 コミット | Medium (実 fcntl 並列、Windows skip) |
| 8 | `resolve_issue_dir` (glob, 重複検出) | 1 コミット | Medium |
| 9 | remote cache reader（`.kaji/cache/issues/N.json` の view 整形）+ `is_readonly` | 1 コミット | Medium |
| 10 | **PR-3b 完了**（provider 実装は揃うが dispatcher は未切替） | — | `make check` 緑 |
| 11 | `cli_main.py` を `get_provider` 経由に切替（fallback あり） | 1 コミット | Medium (CliRunner、provider=github / 未設定 / local の各経路) |
| 12 | `prompt.py` を `IssueContext` 注入へ切替（5 変数すべて） | 1 コミット | Medium |
| 13 | **PR-3c 完了**（fail-fast 未発動、dev repo 動作確認） | — | dev repo で `kaji issue view <既存>` が動く |
| 14 | `.kaji/config.toml` に `[provider]` 追記 + `.gitignore` 更新 | 1 コミット（dev repo の dogfooding） | — |
| 15 | Skill markdown の 5 変数利用へ移行（branch/worktree/design path 自前計算を削減） | 1-2 コミット | Medium (Skill 静的検証) |
| 16 | `feature-development-local.yaml` 追加 | 1 コミット | Medium (workflow validate) |
| 17 | **PR-3d 完了** | — | local workflow smoke |
| 18 | `get_provider` の fallback を削除、fail-fast 化 | 1 コミット | Medium (config 不足時のエラーメッセージ検証) |
| 19 | Large-local テスト追加（subprocess E2E + multi-PC simulation） | 1-2 コミット | Large-local |
| 20 | CHANGELOG + `docs/cli-guides/local-mode.md` ドラフト | 1 コミット | doc lint |
| 21 | **PR-3e 完了** | — | 全 size の test 緑 |

**PR 単位**: 5 PR（3a / 3b / 3c / 3d / 3e）に分割。各 PR で `make check` 緑を maintain。Phase 2 と同様、`--no-ff` で main へ merge。

## テスト戦略（Phase 3 範囲）

design.md § テスト戦略の項目を Phase 3 で実装するものに限定して列挙する。

### Small（Phase 3 で完成）

- `normalize_id` 全パターン（github 数値 / `gh:N` / local-<m>-<n> / <m>-<n> / 数値 + machine_id / `gh:N` remote cache / 不正入力 / machine_id 欠落）
- `IssueContext` builder 全パターン（GitHub label mapping / local frontmatter / remote cache / fallback 不可時の fail-fast）
- `branch_prefix` から `branch_name` / `worktree_dir` / `design_path` を生成する純粋関数
- `next_local_id` のロジック（counter 不在 / counter < dir max / counter > dir max）
- frontmatter parse / serialize round-trip
- string ↔ object label 双方向変換
- machine_id 文法検証（`[a-z0-9]{1,16}` 境界 / ハイフン拒否 / 大文字拒否）
- `GitHubProvider` の subprocess mock pass-through
- config merge 順序（env > config.local.toml > config.toml > global）

### Medium（Phase 3 で完成）

- `LocalProvider` の Issue CRUD 全経路（実 file I/O、tmp_path）
- flock 並列実行（multiprocessing で 2 プロセス、counter 一意性、Windows は skip）
- atomic rename（`*.tmp` 残存ゼロ、書き込み中の crash で部分ファイル残らない）
- `resolve_issue_dir` の正常 / 重複 / 不在
- remote cache reader の view 整形（GitHub API JSON → 表示）
- `kaji local init` の正常系 / 既存 config 保持 / machine_id 候補生成 / 重複警告 / `.gitignore` 追記
- CliRunner による provider 切替（github / local / 未設定 fallback / 未設定 fail-fast）
- `prompt.py` の `IssueContext` 注入（5 変数すべて、provider 固有ロジックを持たないこと）
- Skill markdown の 5 変数利用への移行検証（legacy branch/worktree/design path 自前計算の残存検出）
- `feature-development-local.yaml` の `kaji validate`
- config fail-fast エラーメッセージの内容検証（書くべきファイルと内容が含まれること）
- `.gitignore` 検証（tmp repo 初期化 → `.kaji/config.local.toml` が ignored）

### Large-local（Phase 3 で完成、本機能初の E2E）

- `provider=local` で `feature-development-local.yaml` を使い、`/issue-create` → `/issue-design` → `/issue-implement` → `/issue-close` 相当を実 kaji subprocess で完走
- 2 worktree + 別 machine_id で `kaji issue create` を subprocess 起動 → git merge で同期
- `kaji local init` → `kaji issue create` → local workflow 実行までを fresh tmp repo で検証する
- 注: Skill 側に PR 概念が残っているステップ（`i-pr` 等）は Phase 4 で provider 別エラー化するため、`feature-development-local.yaml` には含めない

### 既存テストの維持

- Phase 1-2 の test を 1 件も deprecate しない。`provider=github` の経路は `GitHubProvider` 経由に切り替わるが挙動は不変
- `prompt.py` の `issue_id` / `issue_ref` 注入テストは `IssueContext` 注入へ拡張し、既存 2 変数の挙動を維持する

## リスク

| リスク | 影響 | 緩和 |
|--------|------|------|
| dev repo 自身が PR-3e で壊れる | 自分が dogfooding 中の repo で kaji が動かなくなる | PR-3d で先に config 追記と local workflow dogfooding を完了し、PR-3e 直前に手動確認 |
| `IssueContext` 化で既存 Skill が破壊 | Phase 2-B 完了直後の安定状態を崩す | `issue_id` / `issue_ref` の挙動を維持したまま 5 変数を追加し、Skill 静的検証で legacy 計算の残存を検出 |
| `branch_prefix` mapping が Skill 既存推論とずれる | branch / worktree / design path が既存運用と不一致になる | 既存 Skill の type label 推論規則を provider 側の mapping に移植し、mapping の Small test を追加 |
| flock の挙動が NFS / FUSE で異なる | 個人運用では稀だが、user 環境で counter が壊れる | エラーメッセージで `OSError` 検出時に明示的に user 通知。本格対応は portalocker でオープン論点 |
| Large-local テストが flaky | CI 不安定化 | subprocess 起動は serial 実行、tmp_path 分離。flake が出たら fixture を見直し |
| machine_id 衝突（同名 2 PC） | local Issue 番号空間が破壊 | `kaji local init` で候補生成と同一 repo 内の重複警告を行う。完全な分散一意性は保証しない |

## 受け入れ条件

### 機械検証可能

- [ ] `make check` が緑（PR-3a / 3b / 3c / 3d / 3e 各時点で）
- [ ] `make test-small` で `normalize_id` / `IssueContext` / `next_local_id` の全パターン緑
- [ ] `make test-medium` で `LocalProvider` CRUD + flock + `kaji local init` + CliRunner + Skill 静的検証の全経路緑
- [ ] `make test-large` で `provider=local` + `feature-development-local.yaml` の subprocess E2E 完走
- [ ] `mypy kaji_harness/providers/` が strict mode で緑
- [ ] `git ls-files .kaji/config.local.toml` が空（gitignored 確認）
- [ ] dev repo の `.kaji/config.toml` に `[provider]` セクションが存在
- [ ] `provider=github` + `gh` CLI 利用可能な環境（CI または GitHub 復旧後）で `kaji issue view <既存 GitHub 番号>` が PR-3e 後も動作。**buildout 中（GitHub 利用不能）の検証は subprocess mock で `GitHubProvider` の pass-through 契約のみ確認**（実通信は復旧後の Large-forge へ持ち越し、design.md L1446-1459 の方針）
- [ ] `kaji issue create` が provider=local config の下で `local-<machine>-1` を作成
- [ ] `prompt.py` が `IssueContext` 由来で `issue_input` / `branch_prefix` / `branch_name` / `worktree_dir` / `design_path` を注入する
- [ ] `feature-development-local.yaml` が `kaji validate` を通過する

### 手動確認

- [ ] dev repo で `kaji local init` → `kaji issue create` → `kaji issue list` → `feature-development-local.yaml` 実行 → `kaji issue close` が完走
- [ ] `provider.type` 未設定状態で `kaji issue view 1` を呼ぶとエラーメッセージが「どのファイルに何を書くか」を示す
- [ ] Windows 環境（あれば）で警告が 1 度のみ出て、CLI が動作する
- [ ] `kaji issue view gh:153` が cache から read-only で表示される（cache が未整備なら明示エラー）
- [ ] `kaji issue edit gh:153 --body ...` が `is_readonly` エラーで停止する
- [ ] `kaji local init` が既存 `.kaji/config.local.toml` を不用意に上書きせず、machine_id 重複候補を警告する

### ドキュメント

- [ ] `draft/design/local-mode/phase3-implementation-report.md` を作成（Phase 2-B と同形式）
- [ ] `docs/cli-guides/local-mode.md` ドラフト（最小: `kaji local init`、provider 切替手順、config schema、ID 文法、local workflow）
- [ ] CHANGELOG エントリ（破壊的変更の告知 + migration 手順）
- [ ] design.md L918 の先送り宣言を「Phase 3 で `IssueContext` 経由の 5 変数注入を完成」に更新

## 段階リリース戦略

PR 5 段の正本は § 未決事項の決定 § 4 fail-fast ロールアウト戦略 を参照。本節はリスク観点での補足表のみ示す。

| PR | 範囲（要約） | 既存挙動への影響 | 戻せるか |
|----|------|-----------------|---------|
| 3a | `providers/` 構造 + `IssueContext` contract 追加（dispatcher 未切替） | ゼロ | revert で完全戻し可能 |
| 3b | `LocalProvider` / `GitHubProvider` 実装（dispatcher 未切替） | ゼロ | revert で完全戻し可能 |
| 3c | dispatcher + `prompt.py` を provider / `IssueContext` 経由に切替（fallback あり） | 内部経路のみ変更、外部挙動不変 | revert 可能、ただし test の変更を伴う |
| 3d | `kaji local init` + local workflow + dev repo の config 追記 + .gitignore + Skill 変数移行 | dev repo に config と local workflow が追加される | revert 可能、ただし dogfooding 状態に影響 |
| 3e | fail-fast 発動 | **破壊的変更**。`provider.type` 未設定の repo は kaji が動かなくなる | revert で fallback 復活、ただし dogfooding 中なので極力避ける |

**3a → 3b → 3c → 3d → 3e** の順序を厳守。3d を 3e より前に置くことで dev repo 自身が PR-3e 直後に壊れる窓を作らない。各 PR ごとに `make check` 緑、独立に main へ `--no-ff` merge。

## オープンな論点

本 Phase で持ち越す論点（Phase 4-5 もしくは別 ADR で扱う）:

- flock の代替（`portalocker` 化 vs atomic create+rename）
- `kaji issue rename <id> <new-slug>` を入れるか（slug 変更を git mv に委ねるか）
- cache 由来 Issue の close 状態を local mode 中に変更不能にする UI 文言（read-only エラー時のガイダンス具体化）
- Phase 4 の PR/MR 抽象名（GitHub PR 固有の `PullRequestProvider` にするか、GitLab MR を含めた `ReviewRequestProvider` にするか）
- `IssueContext` に将来 GitLab 固有の情報（project path、IID など）を追加する場合の互換性ルール
- `worktree_dir` / `branch_name` への slug 同梱の是非（Phase 3 では `kaji-<prefix>-<id>` 固定、将来 `kaji-<prefix>-<id>-<slug>` への移行余地）
- `branch_prefix` mapping（`_mappings.py`）の user 設定可能化（現状は kaji 規約で固定。custom label 体系を user が持ち込んだ場合の拡張パス）
- `kaji local init` の `--force` flag 追加（既存 `[provider]` セクションや `.kaji/config.local.toml` を上書き再生成する用途。Phase 3 では持たないが、運用が安定した後の利便性向上として検討）

## 工数見積

| Step 群 | 内容 | 見積 |
|---------|------|------|
| Step 1-3 | Protocol + models + normalize_id + IssueContext contract | 0.5 日 |
| Step 4 | GitHubProvider 移植 + IssueContext 解決 | 0.75 日 |
| Step 5 / 7-9 | LocalProvider 本体 + flock + glob 解決 + remote cache reader | 1.25 日 |
| Step 6 | `kaji local init` | 0.5 日 |
| Step 11-12 | dispatcher 切替 + prompt.py IssueContext 注入 | 0.5 日 |
| Step 14-16 | dev repo dogfooding + Skill 変数移行 + local workflow | 0.75 日 |
| Step 18 | fail-fast 化 | 0.15 日 |
| Step 19 | Large-local テスト | 0.75 日 |
| Step 20 | CHANGELOG + cli-guide ドラフト | 0.25 日 |
| 予備 | レビュー対応 / バグ修正 | 0.75 日 |
| **合計** | | **6.15 日** |

design.md L1484 の `Phase 3 = 2 日` 見積より大きいが、**案 D 採用により IssueContext 化、local workflow、`kaji local init`、Large-local テスト整備、段階リリース 5 PR** を本 Phase に組み入れた結果。GitHub 復旧待ちが長期化し、GitLab 移行可能性も残す前提では、短期 BCP として小さく作るより Phase 3 で local-first 基盤へ投資する判断を採る。

## 参考

- design.md § 検証戦略の前提（buildout 期間中の temporal inversion）
- phase2-design.md § 段階リリース戦略（PR 2-A / 2-B 分割の前例）
- phase2b-implementation-report.md § 9（Skill 静的検証 grep の再帰化）
