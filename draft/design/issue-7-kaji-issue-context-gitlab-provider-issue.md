# [設計] kaji issue context を GitLab provider で受理する

Issue: gl:7

## 概要

`kaji issue context <id>` が `provider.type='gitlab'` 配下で明示拒否されている
gate を撤去し、GitHub provider と同等の `IssueContext` JSON を返せるようにする。

## 背景・目的

### Observed Behavior (OB)

`provider.type='gitlab'` を有効化した repo で `kaji issue context` を呼ぶと、
`kaji_harness/cli_main.py:822-832` の dispatcher gate により `EXIT_INVALID_INPUT (=2)`
が返り、結果取得不能。

```
$ kaji issue context 6 -q '.branch_prefix'
Error: 'kaji issue context' is not supported under provider.type='gitlab'.
GitLab support requires normalize_id() and dispatcher extension
(tracked separately). Use provider.type='local' or 'github'.
$ echo $?
2
```

### Expected Behavior (EB)

`provider.type='gitlab'` 配下でも `kaji issue context <id>` が `IssueContext`
JSON を stdout に書き、exit 0 で終了する。`--json FIELDS` / `-q EXPR` flag は
local / github 経路と同じく機能する。

EB の根拠:

- `kaji_harness/providers/gitlab.py:577-595` で `GitLabProvider.resolve_issue_context()`
  が既に GitHub 互換 shape (`IssueContext` dataclass) で実装済み。`view_issue` で
  ラベルを取り、`labels_to_branch_prefix()` で prefix を決め、`build_branch_name()` /
  `build_worktree_dir()` で値を組み立てる経路は完成している。
- `kaji_harness/providers/__init__.py:198-207` で `normalize_id()` が `gl:N` /
  数値を `provider_name='gitlab'` で受理し `ResolvedId(kind='gitlab', value=N)`
  を返す経路も実装済み。
- 不足は `_handle_issue` dispatcher の **拒否 gate** と `_handle_issue_context`
  の **provider 別 ID 正規化分岐** のみ。

### Root Cause

`kaji_harness/cli_main.py:822-832` の `if isinstance(provider, GitLabProvider):`
block が前提条件 (`normalize_id` / `resolve_issue_context` 未実装) の作業中保護
として置かれた。前提条件は別 issue（追跡 issue は未起票 = 本 issue が初出）で
解消され、現在は本 gate のみが阻害要因。

加えて `_handle_issue_context` (`cli_main.py:1011-1065`) の provider 分岐は
local / github の 2 系統のみ。gitlab 経路は `normalize_id(provider_name='gitlab')`
で `gl:N` / 数値を value に正規化する分岐を追加する必要がある。

壊れた時期: GitLab provider 拒否 gate 導入時 (`local-p1-4` epic の早期 phase。
`kaji issue context` 機能自体は `local-p1-17` で導入)。

同じ原因で他に壊れている箇所はないか:

- `_handle_issue_local` (`cli_main.py:1071-`) は context sub を保険として `_handle_issue_context`
  に委譲しており、GitLab 経路では `_handle_issue` 側 gate に先に当たるため再現は当該 1 経路のみ。
- `kaji issue context` 以外の GitLab sub (view / create / edit / comment / close / list)
  は `_handle_issue_gitlab` 経由で機能している (`tests/test_phase4_dispatcher_gitlab.py` で
  検証済み)。

## 再現手順

### 前提

- `provider.type='gitlab'` + `provider.gitlab.repo='<group>/<project>'` を持つ
  `.kaji/config.local.toml`。
- 対象 GitLab project に open issue が 1 件存在 (例: gl:6)。

### 操作

```bash
$ kaji issue context 6
```

### 観測 (OB)

```
Error: 'kaji issue context' is not supported under provider.type='gitlab'. ...
$ echo $?
2
```

## インターフェース

### 入力

- positional: `issue_id` — `gl:N` または bare `N` の数値文字列
- optional: `--json <FIELDS>` / `--jq <EXPR>` / `-q <EXPR>` (local / github 経路と同形)

### 出力

stdout に `IssueContext` の dataclass を JSON 化したオブジェクト:

```json
{
  "issue_id": "6",
  "issue_ref": "gl:6",
  "issue_input": "6",
  "slug": "...",
  "branch_prefix": "fix",
  "branch_name": "fix/6",
  "worktree_dir": "/abs/path/../kaji-fix-6",
  "design_path": "draft/design/issue-6-....md",
  "provider_type": "gitlab",
  "branch_prefix_fallback": false,
  "default_branch": "main"
}
```

exit code:

- `0` — 正常
- `2` (`EXIT_INVALID_INPUT`) — ID 正規化失敗 (`gh:N` などの cross-provider 参照を含む)
- `1` (`EXIT_RUNTIME_ERROR`) — `glab` 不在 / API 失敗 / Issue not found

shape / exit code 規約は `_handle_issue_context` (`cli_main.py:1011-1065`) の
github / local 経路と同一。本変更で互換 surface 拡張のみ。

### 使用例

```bash
$ kaji issue context 6 -q '.branch_prefix'
"fix"
$ kaji issue context gl:6 --json branch_name,worktree_dir
{"branch_name": "fix/6", "worktree_dir": "/abs/.../kaji-fix-6"}
```

## 制約・前提条件

- `glab` CLI が PATH に存在し認証済 (`GitLabProvider` の前提と同一)。
- GitLab project 設定で issue ラベル `type:*` が付与済 (label 不在時は `chore` fallback)。
- self-hosted GitLab は非対応 (`provider.gitlab.repo` 経由で `gitlab.com` 固定。
  `GitLabProvider` の確定事項 #3)。
- 既存 github / local 経路の挙動は維持 (regression 禁止)。

## 変更スコープ

| File | 変更内容 |
|------|----------|
| `kaji_harness/cli_main.py:822-832` | GitLab 拒否 gate 削除。`if args and args[0] == "context":` 直下の `isinstance(provider, GitLabProvider)` ブロックを撤去し、`_handle_issue_context(provider, args[1:])` に直接委譲。 |
| `kaji_harness/cli_main.py:1011-1065` (`_handle_issue_context`) | provider 別 ID 正規化分岐に `isinstance(provider, GitLabProvider)` ケースを追加。`normalize_id(ns.issue_id, provider_name='gitlab', machine_id=None)` で `rid.value` を取得して `provider.resolve_issue_context()` に渡す。例外捕捉 (`except`) 節に `GitLabProviderError` を追加 (`GitHubProviderError` と同等に `EXIT_RUNTIME_ERROR` へ正規化)。 |
| `tests/test_issue_context_cli.py:137-155` | 既存の `test_gitlab_provider_rejected` を **削除** または rename して、GitLab provider が受理されることを assert する Small テストに置換。 |

新規追加コード:

- gitlab 用の dispatcher 分岐 (`_handle_issue` 側) は **削除のみ** (新規行不要)。
- `_handle_issue_context` 側は数行の elif 分岐 + import (`GitLabProvider` /
  `GitLabProviderError`) を 1 行追加程度。

## 方針

最小侵襲。`GitLabProvider.resolve_issue_context()` と `normalize_id` の `gl:N`
受理は既に正本側で完成しているため、dispatcher の 2 箇所を解放するだけで動く。
本 issue ではリファクタ / 新規 IF 設計を混ぜない。

擬似コード:

```python
# _handle_issue (cli_main.py:822 付近)
if args and args[0] == "context":
    # gate 削除: GitLab 受理
    return _handle_issue_context(provider, args[1:])

# _handle_issue_context (cli_main.py:1031 付近)
if isinstance(provider, LocalProvider):
    rid_or_rc = _resolve_local_id(provider, ns.issue_id, write=False)
    ...
elif isinstance(provider, GitLabProvider):
    try:
        rid = normalize_id(ns.issue_id, provider_name="gitlab", machine_id=None)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    issue_id_value = rid.value
else:
    # 既存 GitHub 経路
    rid = normalize_id(ns.issue_id, provider_name="github", machine_id=None)
    ...

try:
    ctx = provider.resolve_issue_context(issue_id_value)
except IssueNotFoundError as exc: ...
except (GitHubProviderError, GitLabProviderError) as exc:
    sys.stderr.write(f"Error: {exc}\n")
    return EXIT_RUNTIME_ERROR
```

## テスト戦略

### 変更タイプ

実行時コード変更 (dispatcher path の解放 + 分岐追加)。

bug 固有ルール: 修正前に Red になる再現テストを Small で 1 本以上必須。

### Small テスト

`tests/test_issue_context_cli.py` に追加:

1. **再現テスト (Red→Green)**: `provider.type='gitlab'` を持つ tmp repo で
   `_handle_issue(["context", "6"])` を呼び、`GitLabProvider._glab_api_get`
   を mock した上で:
   - exit 0 を assert
   - stdout に `"provider_type": "gitlab"`、`"issue_ref": "gl:6"`、
     `"branch_name": "fix/6"` (label `type:bug` を返す mock の場合) が含まれることを assert
   既存の `test_gitlab_provider_rejected` (`tests/test_issue_context_cli.py:137-155`)
   は本 PR で **削除 or 反転** する (拒否 → 受理に契約反転)。
2. **`gl:N` prefix 受理**: 同上経路で `["context", "gl:6"]` 入力でも同じ shape
   を返すことを assert。
3. **数値 / `gl:N` 以外の cross-provider 入力拒否**: `["context", "gh:6"]` が
   exit 2 + stderr に `gh:` 文言 (normalize_id 既存挙動の再確認) を返すことを assert。
4. **`GitLabProviderError` → `EXIT_RUNTIME_ERROR`**: mock で `_glab_api_get`
   が `GitLabProviderError("...")` を raise する場合、exit 1 + stderr に
   エラー文言が出ることを assert。

実装層が Small で十分カバーできる根拠:

- `_handle_issue` / `_handle_issue_context` は subprocess を伴わない関数。
  `GitLabProvider._glab_api_get` を patch すれば外部 I/O なしで挙動を観測できる。

### Medium テスト

`tests/test_phase4_dispatcher_gitlab.py` の dispatcher テスト群に
`context` sub を加える形で、`_handle_issue` 全体 (config 読込 → provider 解決
→ context sub dispatch) を end-to-end で 1 ケース確認する (subprocess は mock 経由)。

- 構造的 regression (config → provider → dispatcher → response) を Small より
  上のレイヤで補強する目的。

### Large テスト

`tests/test_large_gitlab/test_issue_roundtrip.py` 群に追加 (現状 `test_large_gitlab`
は `make test-large-gitlab` で実 GitLab を叩く `medium`+`large` セット):

1. 実 GitLab issue 1 件 (smoke 用 fixture) に対し `kaji issue context <iid>`
   を subprocess 経由で呼び、exit 0 + 期待 shape (provider_type=gitlab /
   issue_ref=gl:N / branch_prefix が label に応じた値) を assert。

`make test-large-gitlab` の存在: `Makefile` 参照 (本 issue では既存 target を
使う前提、Makefile 変更なし)。

### 検証手順 (smoke 含む)

1. `make test-small` PASS
2. `make test-medium` PASS
3. `make test-large-gitlab` PASS (実 GitLab 疎通)
4. 手動 smoke: gl:6 の worktree で `kaji issue context 6 -q '.branch_prefix'` が
   `bug` または該当 prefix を返し exit 0 を確認。`/issue-start gl:6` で worktree
   構築まで完走することを確認 (本 issue の上流ブロッカー解消の最終証跡)。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 既存設計 (provider 抽象) の中で完結。新たな技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし (dispatcher gate 撤去のみ) |
| docs/dev/ | なし | workflow 文書の変更なし。`/issue-start` skill 側も既に gitlab 対応想定 |
| docs/reference/ | なし | API 仕様変更なし (shape は GitHub 経路と同一) |
| docs/cli-guides/gitlab-mode.md | **あり** | 「未対応経路の記載なし」が現状 (Issue 本文の「関連 docs」参照)。`kaji issue context` が gitlab で動作する旨を追記、もしくは未対応リストから除外する必要があるか確認。**実装段階で当該 doc を読み、現状未対応注記があれば外す** |
| CLAUDE.md | なし | プロジェクト規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 既存実装: GitLab provider | `kaji_harness/providers/gitlab.py:577-595` | `resolve_issue_context()` が `IssueContext` を GitHub 互換 shape で返す経路は既に完成。本 issue では呼び出し側を解放するのみ |
| 既存実装: normalize_id の `gl:N` 受理 | `kaji_harness/providers/__init__.py:155-207` | `_GL_PREFIX_RE` と provider_name='gitlab' 分岐により `gl:N` / 数値が `ResolvedId(kind='gitlab', value=N)` に正規化される |
| 阻害コード | `kaji_harness/cli_main.py:822-832` | `isinstance(provider, GitLabProvider)` で `EXIT_INVALID_INPUT` を返す gate。本 issue で撤去対象 |
| 既存テスト (反転対象) | `tests/test_issue_context_cli.py:137-155` | `test_gitlab_provider_rejected` が現状の拒否契約を assert している。本 PR で契約反転に伴い削除 or rename |
| 既存 dispatcher テスト | `tests/test_phase4_dispatcher_gitlab.py` | `kaji issue` の GitLab 経路 (view/create/...) の既存テストパターン。context sub の Medium テスト追加先 |
| 既存 Large テスト | `tests/test_large_gitlab/` | 実 GitLab 疎通テストの集約場所 (`test_issue_roundtrip.py` 等) |
| testing 規約 | `docs/dev/testing-convention.md` | Small / Medium / Large の境界定義。本設計の test サイズ判断の準拠先 |
| Issue 本文 | gl:7 | OB / EB / 完了条件の正本 |
