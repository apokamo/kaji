# [設計] GitLabProvider 実装 + config + dispatcher 拡張

Issue: local-pc5090-5
EPIC: local-pc5090-4 (GitLab 対応検証 EPIC)

## 概要

`provider.type='gitlab'` を kaji の正式 provider として認識させるための基盤実装。`IssueProvider` Protocol の 8 メソッドを `glab` CLI subprocess で実装し、`ProviderConfig` / `Workflow.requires_provider` の enum を拡張、`get_provider()` に GitLab 分岐を追加する。本 Issue 完了で `provider.type='gitlab'` 配下の dispatch が成立する（CRUD / IssueContext 解決まで）。`gl:N` ID 拡張 / `kaji issue` passthrough / PR (MR) 経路は子 Issue #2 以降の責務。

## 背景・目的

EPIC `local-pc5090-4` は GitLab 採用が確定した際に即時移行できる「準備状態」を先回りで作る作業 EPIC。本 Issue はその起点であり、後続 5 子 Issue（#2 `local-pc5090-6` / #3 `local-pc5090-7` / #4 `local-pc5090-8` / #5 `local-pc5090-9` / #6 `local-pc5090-10`）の実装前提となる基盤層を提供する。

### ユーザーストーリー

- **maintainer として**、`.kaji/config.toml` に `[provider]` `type = "gitlab"` と `[provider.gitlab]` `repo = "group/project"` を書くだけで `get_provider()` が `GitLabProvider` を返し、`IssueProvider` Protocol が満たされる状態にしたい。
- **maintainer として**、`requires_provider: gitlab` を持つ workflow YAML が `kaji validate` で正しく検査される状態にしたい（後続子 Issue が新規 workflow を追加する前提）。
- **maintainer として**、`glab` の context 状態（current project / current login）が何であっても、kaji が呼ぶ際は `provider.gitlab.repo` で対象を明示できる状態にしたい（暗黙依存禁止）。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| `python-gitlab` 等の API ライブラリを直接使う | 確定事項 #1 で `glab` CLI subprocess 方式が確定済（`GitHubProvider` の `gh` CLI と対称構造）。kaji 全体としての依存方針を一貫させる。API 直叩き fallback は持たない |
| `glab` の context（current project）に暗黙依存し `--repo` を省略 | 確定事項に基づく方針。複数 repo を持つ環境で誤発射事故のリスクが高い。`GitHubProvider._run_gh` も同じ理由で `--repo` 必須 |
| self-hosted GitLab インスタンスへ拡張 | 確定事項 #3 で「self-hosted 対応はしない」が確定。`gitlab.com` 前提で簡素化する |
| `glab issue list/view` の出力 parser を自作 | `glab` 自身は issue/view/list で構造化 JSON 出力を提供しない（`--output-format` は `details/ids/urls` のみ）。`glab api` 経由で REST を叩いて JSON を取得する方が安定 |

## インターフェース

### 1. `kaji_harness/providers/gitlab.py` 新規モジュール

#### `GitLabProvider` クラス

```python
@dataclass
class GitLabProvider:
    """``glab`` CLI を subprocess で叩く provider。

    Attributes:
        repo: ``group/project`` 形式（GitLab namespace path）。
            ``provider.gitlab.repo`` config 由来。``glab --repo`` に渡す
            ほか、``glab api projects/:id`` の URL encode 元としても使う。
        repo_root: 設計書 path / worktree path 計算用（GitHubProvider 同形）。
        default_branch: ``provider.gitlab.default_branch`` config 由来。
            ``IssueContext.default_branch`` の source。
    """
    repo: str
    repo_root: Path
    default_branch: str = "main"

    @property
    def is_readonly(self) -> bool: ...
```

`IssueProvider` Protocol（`kaji_harness/providers/base.py:16-83`）の 8 メソッドを実装する:

| メソッド | 引数 / 戻り値 | 実装方針 |
|---------|---------------|----------|
| `create_issue(title, body, labels, slug)` → `Issue` | GitHubProvider と同シグネチャ。`slug` は GitLab では title 由来で導出するため受け取るが採用しない（`del slug`） | `glab issue create --repo <repo> --title <title> --description <body> --label <l1,l2,...> --yes` |
| `view_issue(issue_id: str)` → `Issue` | `issue_id` は project-local IID の数値文字列 | `glab api projects/<url-encoded-repo>/issues/<iid>` で issue JSON、`/notes` で comments JSON、結果を `Issue` に詰める |
| `edit_issue(issue_id, title?, body?, add_labels?, remove_labels?)` → `Issue` | GitHubProvider と同シグネチャ | `glab issue update <iid> --repo <repo> [--title <t>] [--description <b>] [--label l1,l2] [--unlabel l1,l2]`、最後に `view_issue` で再取得 |
| `comment_issue(issue_id, body)` → `Comment` | GitHub と同様、created_at / author を返さない最小情報 | `glab issue note <iid> --repo <repo> --message <body>` |
| `close_issue(issue_id, reason?)` → `Issue` | `reason` は GitLab では受けないため `del reason` | `glab issue close <iid> --repo <repo>`、最後に `view_issue` |
| `list_issues(state, labels, limit)` → `list[Issue]` | state: `"open"` / `"closed"` / `"all"` | `glab api projects/<repo>/issues?state=opened\|closed\|all&labels=...&per_page=<limit>` |
| `list_labels()` → `list[Label]` | | `glab api projects/<repo>/labels?per_page=200` |
| `resolve_issue_context(issue_id)` → `IssueContext` | | `view_issue` を 1 度呼び、label / title から `IssueContext` を組み立てる（GitHub provider と同形）。`provider_type="gitlab"`、`issue_ref` は `gl:<iid>` 形式 |

#### 内部 helper（GitHubProvider と対称）

```python
class GitLabProviderError(RuntimeError): ...

def _run_glab(*args, capture=True) -> subprocess.CompletedProcess[str]:
    """glab を subprocess で起動。--repo を必ず明示している前提。
       glab 自体が PATH にない場合は GitLabProviderError("'glab' CLI not found in PATH...") を投げる。"""

def _glab_api_get(endpoint: str) -> object:
    """glab api <endpoint> を起動し JSON を parse して返す。
       endpoint には URL-encoded repo を含む project path を渡す。
       例: 'projects/group%2Fproject/issues/42'"""

@staticmethod
def _parse_issue_payload(payload: dict) -> Issue:
    """GitLab REST API の issue JSON を Issue に詰める。
       GitHub provider の _parse_issue_payload と類似だが GitLab 固有 field 名を吸収:
         - 'iid' → Issue.id（issue_iid を採用、global 'id' ではない）
         - 'state' は 'opened'/'closed' を 'open'/'closed' に正規化
         - 'labels' は string array（GitHub の {name,description,color} と異なる）
         - description → Issue.body"""

@staticmethod
def _parse_comments_payload(payload: list) -> list[Comment]:
    """GitLab notes API の JSON を Comment list に詰める。
       'system': true の note（state change 等の system note）は filter で除外する。"""
```

### 2. `kaji_harness/config.py` 拡張

#### 新規 dataclass

```python
@dataclass(frozen=True)
class GitLabProviderConfig:
    """``[provider.gitlab]`` セクション。

    Attributes:
        repo: ``group/project`` 形式（GitLab namespace path）。必須（空文字なら
            get_provider() で ValueError）。``glab --repo`` および
            ``glab api projects/<URL-encoded repo>`` に渡す。
        default_branch: ``main`` / ``master`` 等の既定 branch 名。
    """
    repo: str = ""
    default_branch: str = "main"
```

> **note**: `hostname` フィールドは持たない。確定事項 #3「self-hosted 非対応 / `gitlab.com` 前提」の論理的帰結。

#### `ProviderConfig` 拡張

```python
@dataclass(frozen=True)
class ProviderConfig:
    type: str  # "github" | "local" | "gitlab"
    local: LocalProviderConfig
    github: GitHubProviderConfig
    gitlab: GitLabProviderConfig  # 新規
```

#### `_parse_provider` 拡張

- `provider.type` の値域を `{"github", "local", "gitlab"}` に拡張
- `[provider.gitlab]` table を読み、`repo` (string optional) / `default_branch` (string default `"main"`) を `GitLabProviderConfig` に詰める
- overlay merge（`config.local.toml` 経由）も `gitlab` サブテーブルを deep-1 merge 対象に追加

### 3. `kaji_harness/providers/__init__.py` 拡張

#### `get_provider()`

```python
if config.provider.type == "gitlab":
    if not config.provider.gitlab.repo:
        raise ValueError(
            "provider.type='gitlab' requires provider.gitlab.repo "
            "(e.g. 'group/project')."
        )
    return GitLabProvider(
        repo=config.provider.gitlab.repo,
        repo_root=config.repo_root,
        default_branch=config.provider.gitlab.default_branch,
    )
```

#### `actual_provider_type()`

戻り値の docstring を `"github" | "local" | "gitlab"` に拡張（実体は `config.provider.type` をそのまま返すため runtime の挙動は変わらない）。

#### `__all__`

`GitLabProvider` を export 一覧に追加。

> **note**: `normalize_id()` の `gl:N` 拡張は子 Issue #2 (`local-pc5090-6`) のスコープ。本 Issue の `view_issue(issue_id)` は **数値文字列 IID をそのまま受け取る** 前提で実装する（GitHubProvider が `"153"` を受けるのと同じ）。`normalize_id` の dispatch は本 Issue では触らない。

### 4. `kaji_harness/models.py` / `kaji_harness/workflow.py` 拡張

#### `Workflow.requires_provider`

```python
# models.py
requires_provider: Literal["github", "local", "gitlab", "any"] = "any"

# workflow.py
VALID_REQUIRES_PROVIDER = {"github", "local", "gitlab", "any"}
```

`_validate_workflow_provider_match`（`cli_main.py:358-382`）は文字列比較なので enum 拡張のみで動く。

#### builtin workflow の `requires_provider` ポリシー

| ファイル | 既存 | 本 Issue での扱い |
|---------|------|-------------------|
| `feature-development.yaml` | `github` | **`github` 維持**（PR 作成 step `i-pr` は GitLab MR 経路未対応のため） |
| `feature-development-light.yaml` | `github` | `github` 維持 |
| `feature-development-local.yaml` | `local` | `local` 維持 |
| `implement-to-pr.yaml` | `github` | `github` 維持 |
| `design-only.yaml` | `any` | `any` 維持（design-only は forge 操作を含まない） |
| `docs-maintenance-local.yaml` | `local` | `local` 維持 |

> **判断**: 既存 builtin yaml は **本 Issue では追加・変更しない**。`provider.type='gitlab'` を要求する builtin workflow は子 Issue #2 以降で `kaji pr` MR エイリアス / docs と一緒に追加する。本 Issue 完了直後の `provider.type='gitlab'` 環境では「`kaji run feature-development.yaml ...` は exit 2 で停止する」が想定動作。設計書の整合先: workflow ↔ provider 整合 fail-fast（`docs/dev/development_workflow.md` § "workflow 起動時の provider 整合 fail-fast"）。

#### `IssueContext.provider_type`

`models.py` (`providers/models.py:101`) のコメント ```"github"`` / ``"local"``` を `"github" | "local" | "gitlab"` に拡張する。フィールド型は `str` のままなので runtime 変更はない（コメント / docstring のみ更新）。

#### `format_issue_ref` / 関連 helper

`kaji_harness/providers/context.py` の `format_issue_ref` は GitHub の `#N` / local の bare ID を分岐している。本 Issue では:

- `format_issue_ref` の signature は変更しない（後方互換）
- GitLabProvider 内部で `gl:<iid>` 形式の `issue_ref` を組み立てる（`f"gl:{issue.id}"`）
- 子 Issue #2 で `format_issue_ref` の中央集約が必要になれば追って refactor する

### 入力（config 例）

```toml
# .kaji/config.toml
[provider]
type = "gitlab"

[provider.gitlab]
repo = "owner-group/repo-name"
default_branch = "main"
```

### 出力（IssueContext 例）

`view_issue("42")` 経由で `resolve_issue_context("42")` を呼んだ結果:

```python
IssueContext(
    issue_id="42",
    issue_ref="gl:42",
    issue_input="42",
    slug="add-foo-bar",            # title から derive
    branch_prefix="feat",
    branch_name="feat/42",
    worktree_dir="/repo/../kaji-feat-42",
    design_path="draft/design/issue-42-add-foo-bar.md",
    provider_type="gitlab",
    branch_prefix_fallback=False,
    default_branch="main",
)
```

### 使用例

```python
from kaji_harness.config import KajiConfig
from kaji_harness.providers import get_provider

config = KajiConfig.discover()
provider = get_provider(config)  # GitLabProvider
issue = provider.view_issue("42")
print(issue.id, issue.title, issue.state)
ctx = provider.resolve_issue_context("42")
assert ctx.provider_type == "gitlab"
assert ctx.issue_ref == "gl:42"
```

### エラーケース

| 想定失敗 | 戻り値 / 例外 |
|---------|---------------|
| `glab` CLI 不在 | `GitLabProviderError("'glab' CLI not found in PATH. Install glab to use provider.type='gitlab'.")` |
| `glab` exit != 0 | `GitLabProviderError(f"glab failed (exit {rc}): {stderr or stdout}")` |
| `glab api` JSON parse 失敗 | `GitLabProviderError(f"glab returned invalid JSON: {exc}")` |
| `glab api` 認証エラー（401/403） | `glab` 自体が stderr に GitLab API エラーを出す → `GitLabProviderError` で wrap |
| `provider.gitlab.repo` 空 | `get_provider()` で `ValueError("provider.type='gitlab' requires provider.gitlab.repo (e.g. 'group/project').")` |
| `provider.type` が未知値 | `get_provider()` で既存 `ValueError(f"unknown provider.type: ...")` でカバー |
| Issue 不在（404） | `glab` exit != 0 → `GitLabProviderError`（404 専用例外は子 Issue #2 で必要なら追加） |

## 制約・前提条件

### 依存

- `glab` CLI が PATH に存在すること（確定事項 #1: kaji の依存として必須化、CI でも install 要求）
  - 認証は `glab auth login` 済み or `GITLAB_TOKEN` env で済ませる前提（OQ-1 で実装中決定可）
- `gitlab.com` 前提。self-hosted は非対応（確定事項 #3）
- `glab` バージョンは ローカル検証環境で `1.36.0` を確認。最低要求バージョンは「`glab issue create/update/note` および `glab api projects/:id/issues` が動作する」程度。`glab` 1.30+ 程度を想定し、Makefile / CI で具体最低バージョンを固定するかは子 Issue #6 (`local-pc5090-10`) 範囲

### 互換性保持

- `provider.type='github'` / `'local'` の既存挙動は変えない
- `[provider.gitlab]` 不在で `type != "gitlab"` の場合は `GitLabProviderConfig()`（空 default）が dataclass field に置かれるだけで実害なし
- 既存 builtin workflow の `requires_provider` 値は変更しない

### パフォーマンス

- `view_issue` は 2 回 API call を行う（issue 本体 + notes）。`gh issue view --json comments` が 1 回で済むのと比べて 1 往復多い。GitLab REST API の制約（issue endpoint には notes が含まれない）に従う。後続子 Issue で必要なら GraphQL 経路を検討
- `list_issues` は `per_page` を `limit` で渡し pagination は本 Issue では行わない（GitHubProvider と同等。`limit` 上限超は呼び出し側の責任）

### スコープ境界

#### IN（本 Issue で実装）

- `kaji_harness/providers/gitlab.py`（`GitLabProvider` + `GitLabProviderError`）
- `kaji_harness/config.py`（`GitLabProviderConfig` 新設、`ProviderConfig.gitlab` 追加、`_parse_provider` の `gitlab` 受理、overlay merge 対応）
- `kaji_harness/providers/__init__.py`（`get_provider` 分岐、`__all__`、`actual_provider_type` docstring）
- `kaji_harness/models.py` / `kaji_harness/workflow.py`（`Workflow.requires_provider` Literal 拡張、`VALID_REQUIRES_PROVIDER` 拡張）
- `kaji_harness/providers/models.py`（`IssueContext.provider_type` の docstring 拡張）
- 既存テスト fixture を壊さないため、新規 test ファイル `tests/test_providers_gitlab.py` 等を追加して enum 拡張 / dispatcher 経路 / mock 経由 CRUD を検証

#### OUT（後続子 Issue / EPIC OUT スコープ）

- `kaji issue` / `kaji pr` の CLI passthrough 拡張 → 子 Issue #2 (`local-pc5090-6`)
- `gl:N` ID 規約と `normalize_id` 拡張 → 子 Issue #2
- `resolve_pr_context` → 子 Issue #3 (`local-pc5090-7`)
- `kaji sync from-gitlab` / cache populate → 子 Issue #4 (`local-pc5090-8`)
- 実 GitLab 通信 E2E（`make test-large-gitlab`） → 子 Issue #6 (`local-pc5090-10`)
- `gitlab-mode.md` docs 新設 → 子 Issue #5 (`local-pc5090-9`)
- builtin workflow `feature-development-gitlab.yaml` 等の追加 → 子 Issue #2 以降

## 方針（Minimal How）

### glab CLI vs glab api ハイブリッド戦略

`glab` CLI は構造化 JSON 出力（`gh ... --json fields`）を持たない（`glab issue list --output-format` の値域は `details / ids / urls` のみ）。よって本 Issue では:

- **Mutating 系**（`create_issue` / `edit_issue` / `comment_issue` / `close_issue`）は `glab issue <sub>` を直接起動。終了コードのみ判定し、結果取得が必要なら `view_issue` を続けて呼ぶ（GitHub provider と同戦略）
- **Read 系**（`view_issue` / `list_issues` / `list_labels`）は `glab api projects/<url-encoded-repo>/...` を起動して REST JSON を取得。Python `urllib.parse.quote` で `repo` を URL encode（`/` → `%2F`）

これにより:

1. mutating ロジックは glab subcommand に委譲できる（label flag 等の解釈を再実装しない）
2. read 系は安定した REST JSON を直接 parse できる（CLI 出力 format の安定性に依存しない）
3. GitHub provider の構造（`_run_gh` / `_gh_json` / `_parse_issue_payload`）と 1:1 で対応する

### subprocess 起動方針

GitHubProvider と同パターンを踏襲する:

```python
def _run_glab(self, *args, capture=True):
    if shutil.which("glab") is None:
        raise GitLabProviderError("'glab' CLI not found in PATH. ...")
    cmd = ["glab", *args]
    return subprocess.run(cmd, check=False, capture_output=capture, text=True)
```

`--repo <repo>` は **mutating 系のみ呼出側で `args` に含める**（`glab issue create --repo ... --title ...`）。`glab api` 系は `--repo` を受けないため、URL に repo path を埋め込む形にする。

### URL encode の取り扱い

```python
from urllib.parse import quote
# "group/project" → "group%2Fproject"
encoded = quote(self.repo, safe="")
endpoint = f"projects/{encoded}/issues/{iid}"
```

`safe=""` を渡すことで `/` も encode する（GitLab REST API の `:id` placeholder は URL-encoded path を受け入れる）。

### state 値の正規化

GitLab の issue state は `"opened"` / `"closed"`。kaji の `Issue.state` は GitHub と揃えた `"open"` / `"closed"` を採用してきた。GitLabProvider 内部で `"opened"` → `"open"` に正規化する。`list_issues` の `state="open"` 引数は GitLab REST API の `?state=opened` に変換して渡す。

### labels の取り扱い

GitLab REST API の issue 応答での `labels` フィールドは `string[]`（label name array）。一方 `GET /projects/:id/labels` の応答は object array（`{name, description, color, ...}`）。`_parse_issue_payload` では `string[]` から `Label(name=...)` だけ詰める（GitHubProvider が string entries を受けるロジックと同形）。`list_labels()` は `_glab_api_get("projects/<repo>/labels")` の object array をそのまま `Label` に詰める。

### resolve_issue_context

GitHubProvider と同じ流れ:

1. `view_issue(issue_id)` で Issue を取得
2. `labels_to_branch_prefix(label_names)` で `branch_prefix` / `fallback` を導出
3. `derive_slug_from_title(issue.title)` で slug
4. `IssueContext(... provider_type="gitlab", issue_ref=f"gl:{issue.id}", ...)` を組み立てて返す

### `Workflow.requires_provider` の Literal 拡張

```python
# kaji_harness/models.py
requires_provider: Literal["github", "local", "gitlab", "any"] = "any"
# kaji_harness/workflow.py
VALID_REQUIRES_PROVIDER = {"github", "local", "gitlab", "any"}
```

`_validate_workflow_provider_match` は文字列比較のみなので変更不要。

### config overlay の merge

`_parse_provider` の deep-1 merge 対象キー（`{"github", "local"}`）に `"gitlab"` を追加する:

```python
if k in {"github", "local", "gitlab"} and isinstance(v, dict):
    base_sub = merged.get(k) or {}
    if not isinstance(base_sub, dict):
        base_sub = {}
    merged[k] = {**base_sub, **v}
```

## テスト戦略

### 変更タイプ
**実行時コード変更**（新規 provider モジュール / config 拡張 / dispatcher 分岐 / workflow enum 拡張）。

#### Small テスト（mock 完結 / 純粋ロジック）

- `GitLabProviderConfig` の field default 検証
- `ProviderConfig` に `gitlab` field が追加され、tracked TOML から正しくロードされる
- `_parse_provider` が `provider.type='gitlab'` + `[provider.gitlab] repo='g/p'` を `ProviderConfig(type='gitlab', gitlab=...)` に詰める
- `_parse_provider` が `provider.type` の未知値（`'foo'`）を `ConfigLoadError` で拒否する（既存挙動の回帰確認）
- `_parse_provider` の overlay merge が `[provider.gitlab]` も deep-1 merge できる
- `get_provider(config)` が `provider.type='gitlab'` で `GitLabProvider` を返す
- `get_provider(config)` が `provider.type='gitlab'` + `repo` 空で `ValueError` を投げる
- `Workflow.requires_provider='gitlab'` の YAML が `_parse_workflow` を通る
- `validate_workflow` が `requires_provider='unknown'` を errors に追加する
- `_parse_issue_payload` が GitLab 風 dict（`iid`/`description`/`state='opened'`/`labels=['a','b']`）を `Issue(id='42', body='...', state='open', labels=[Label('a'), Label('b')])` に正規化する
- `_parse_comments_payload` が `system: true` の note を除外する
- `GitLabProvider.resolve_issue_context` が（subprocess を mock した上で）`provider_type='gitlab'`、`issue_ref='gl:<iid>'` を返す
- `_run_glab` が `glab` 不在時に `GitLabProviderError` を投げる
- `_glab_api_get` の URL encode が `group/project` を `group%2Fproject` に変換していること

#### Medium テスト（subprocess 結合 / `_validate_workflow_provider_match` 経由）

- `subprocess.run` の monkeypatch で `glab` の標準的応答を模倣し、`view_issue` / `list_issues` / `list_labels` / `create_issue` / `edit_issue` / `comment_issue` / `close_issue` の round-trip を検証
- `_validate_workflow_provider_match` が `requires_provider='gitlab'` + `provider.type='gitlab'` で PASS、`provider.type='github'` で `EXIT_INVALID_INPUT` + stderr に切替手順を出すこと
- `_validate_workflow_provider_match` が `requires_provider='github'` + `provider.type='gitlab'` で同様に拒否
- `kaji validate` の CLI 経由（`tests/test_cli_validate.py` 流儀）で `requires_provider: gitlab` を含む workflow YAML が PASS
- `kaji run` の CLI 経由（既存 `test_phase4_workflow_provider_match.py` 流儀）で `provider.type='gitlab'` + `feature-development.yaml`（`requires_provider: github`）の組合せが exit 2 で停止する（builtin workflow 維持の方針確認）

#### Large テスト

- 本 Issue 範囲では **追加しない**。実 GitLab 通信 E2E（`make test-large-gitlab`）は子 Issue #6 (`local-pc5090-10`) の責務（EPIC OUT スコープ）。
- 不要理由（`testing-convention.md` 4 条件 vs 「子 Issue でカバー」）:
  - 1: 「独自ロジックの追加・変更をほぼ含まない」→ **満たさない**（新規ロジックあり）が、E2E は子 Issue #6 で恒久テストとして追加されるため重複を避ける
  - 2: 想定不具合パターンは Medium テスト（subprocess mock）と子 Issue #6 の Large テストで重複なくカバーされる
  - 3: 本 Issue で Large テストを追加しても、子 Issue #6 が同じ通信経路を恒久テスト化するため検出情報が増えない
  - 4: テスト未追加の理由は本設計書および子 Issue #6 の参照で説明可能

### baseline failure 既知事項

なし（本 Issue 起点）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | **なし**（本 Issue では） | 確定事項 #1〜#7 が EPIC 本文に固定済。新規 ADR を立てる必要は本 Issue にはない（kaji 全体の forge 戦略 ADR は別 Issue で検討） |
| `docs/ARCHITECTURE.md` | **なし** | provider 抽象自体は Phase 3 で確立済、新規 provider 追加だけならアーキテクチャ図に変更なし |
| `docs/dev/development_workflow.md` | **なし** | workflow 起動時の provider 整合 fail-fast の節は既存 `github`/`local` 説明のまま流用できる（`gitlab` 追加は値域拡張のみ） |
| `docs/dev/workflow_guide.md` | **あり**（小） | provider × workflow の対応表に `gitlab` 行を追加するか、子 Issue #5 (`local-pc5090-9`) で `gitlab-mode.md` 新設時に集中対応するか検討。**判断: 子 Issue #5 でまとめる**（本 Issue 単独で部分追加すると docs 整合性が崩れる） |
| `docs/cli-guides/` | **なし**（本 Issue では） | `gitlab-mode.md` の新設は子 Issue #5 の責務 |
| `docs/reference/python/` | **なし** | コーディング規約には影響しない |
| `CLAUDE.md` | **なし** | 規約は変わらない（builtin workflow ポリシーも変えない） |
| `kaji_harness/providers/__init__.py` の docstring | **あり**（コード内） | `provider.type` の値域に `gitlab` を追加する説明をコメント / docstring に反映 |

> **判断**: 本 Issue では **コード変更 + 既存コード内 docstring 更新のみ**。外部 docs の更新は子 Issue #5 (`local-pc5090-9`) に集中させる。これにより本 Issue の review-design / review-code が docs 整合性で棚上げされにくくする。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| **GitLab REST API: Issues** | https://docs.gitlab.com/ee/api/issues.html | `GET /projects/:id/issues/:issue_iid`（single issue）、`GET /projects/:id/issues`（list、`?state=opened\|closed\|all`、`?labels=`、`?per_page=`）。issue 応答の主要 field: `iid`（project-local）/ `id`（global）/ `title` / `description` / `state`（`"opened"` / `"closed"`）/ `labels: string[]`。**本設計の `view_issue` / `list_issues` / state 正規化の正本** |
| **GitLab REST API: Notes (Issues)** | https://docs.gitlab.com/ee/api/notes.html#issues | `GET /projects/:id/issues/:issue_iid/notes`。各 note の主要 field: `id` / `body` / `author.username` / `created_at` / `system`（state change 等の system note を区別）。**本設計の comment 取得 + system note 除外の正本** |
| **GitLab REST API: Labels** | https://docs.gitlab.com/ee/api/labels.html | `GET /projects/:id/labels`、`per_page` 上限 100（GitHub の 200 と異なる点に注意）。応答 field: `name` / `description` / `color`。**本設計の `list_labels` の正本** |
| **GitLab REST API: namespaced path encoding** | https://docs.gitlab.com/ee/api/rest/index.html#namespaced-paths | "URL-encoded path: `diaspora/diaspora` → `diaspora%2Fdiaspora`"。`:id` placeholder は URL-encoded path も numeric ID も受け付ける。**本設計の `urllib.parse.quote(repo, safe="")` の正本** |
| **glab CLI: issue subcommands** | https://gitlab.com/gitlab-org/cli/-/blob/main/docs/source/issue/index.md | `glab issue create --title --description --label --yes`、`glab issue update <iid> --title --description --label --unlabel`、`glab issue note <iid> --message`、`glab issue close <iid>`、すべて `--repo OWNER/REPO` を受ける。**本設計の mutating 系 subprocess 呼び出しの正本** |
| **glab CLI: api command** | https://gitlab.com/gitlab-org/cli/-/blob/main/docs/source/api.md | `glab api <endpoint>` は GitLab REST API v4 を起動し、JSON を stdout に出力。`:id` 等の placeholder を current repo から自動展開。**本設計の `_glab_api_get()` の正本**。なお placeholder 展開を回避し明示 path（`projects/<encoded>/issues/<iid>`）を渡す方針 |
| **glab issue list `--output-format`** | (`glab issue list --help` 出力をローカルで確認、glab 1.36.0) | `--output-format string   One of 'details', 'ids', or 'urls' (default "details")`。**JSON 出力をサポートしない**ため `glab api` 経由で REST を叩く必要があるという本設計の判断根拠 |
| **EPIC `local-pc5090-4` 確定事項 #1〜#7** | `.kaji/issues/local-pc5090-4-epic-gitlab-validation/issue.md` § 確定事項 | 本設計の前提として参照。特に #1（CLI subprocess 必須化）/ #3（`gitlab.com` 固定 / `gl:N` 規約）/ #5（本格実装）/ #7（PR / MR 互換 contract、本 Issue では PR 経路扱わない） |
| **OQ-2 決定文書** | `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md` | 本 Issue では PR / MR 経路を扱わないため直接の影響はないが、子 Issue #2 への申し送り事項を整理する際の参照点 |
| **既存 `IssueProvider` Protocol** | `kaji_harness/providers/base.py:16-89` | 本設計の 8 メソッド signature の正本 |
| **既存 `GitHubProvider`** | `kaji_harness/providers/github.py:1-305` | 本設計の構造的対称形（`_run_gh` / `_gh_json` / `_parse_issue_payload` / `resolve_issue_context`）の正本 |
| **既存 `_parse_provider` overlay merge** | `kaji_harness/config.py:145-258` | 本設計の `[provider.gitlab]` overlay merge の正本 |
| **既存 `_validate_workflow_provider_match`** | `kaji_harness/cli_main.py:358-382` | 本設計の `Workflow.requires_provider='gitlab'` 拡張時の挙動確認の正本 |
| **テスト規約** | `docs/dev/testing-convention.md` | Small / Medium / Large 区分の正本 |
