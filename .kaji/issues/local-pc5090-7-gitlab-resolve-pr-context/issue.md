---
id: local-pc5090-7
title: GitLabProvider.resolve_pr_context + prompt 注入経路
state: open
slug: gitlab-resolve-pr-context
labels:
- type:feature
- scope:gitlab-validation
created_at: '2026-05-09T06:02:09Z'
---
## 設計書

<details>
<summary>クリックして展開</summary>

# [設計] GitLabProvider.resolve_pr_context + prompt 注入経路

Issue: local-pc5090-7
EPIC: local-pc5090-4 (GitLab 対応検証 EPIC)

## 概要

GitLab branch 名から MR を逆引きする `GitLabProvider.resolve_pr_context(branch_name)` を実装し、`kaji_harness/prompt.py` に provider 共通の PR コンテキスト注入経路を追加する。これにより `provider.type='gitlab'` 配下でも `pr_id`（project-local `merge_request_iid`）/ `pr_ref`（`gl:<iid>`）が skill プロンプトに自動注入される状態を作る。`prompt.py` / `runner.py` には provider.type 分岐を入れず、`IssueProvider` Protocol の polymorphic 呼び出しで完結させる。

> **本 Issue 完了で skill 動作が変わる範囲**: `/i-pr` のみ。`/pr-fix` / `/pr-verify` は現状 `provider.type='gitlab'` を Step 0 で ABORT する（`.claude/skills/pr-fix/SKILL.md:62-92` / `pr-verify/SKILL.md:74-104`）ため、本 Issue の自動注入経路の恩恵を即座に受けるのは `gitlab` を許可している `/i-pr`（`.claude/skills/i-pr/SKILL.md:74-90`）のみ。`/pr-fix` / `/pr-verify` の `gitlab` 許可解禁は子 Issue `local-pc5090-9`（docs / skill 整理）の責務（後述「skill 側の対応範囲」）。

## 背景・目的

EPIC `local-pc5090-4` 連番 #3。bucket Issue `local-pc5090-1` の Phase 4 申し送り「`<Provider>.resolve_pr_context(branch_name)` 実装 + `pr_id` / `pr_ref` の `prompt.py` 自動注入」を GitLab 側で先取り実装する。本 Issue 完了時点で「自動注入経路」が成立する状態を作り、子 Issue `local-pc5090-9` での skill SKILL.md 暫定運用記述削除 + provider check 解禁に渡す。

### ユーザーストーリー

- **kaji ユーザー（`provider.type='gitlab'`）として**、`/i-pr` skill が起動したとき、現在の branch から MR が自動検出され、`pr_id` / `pr_ref` がプロンプトに既に入っている状態にしたい（GitHub mode と同じ前提に揃う）。
- **maintainer として**、`prompt.py` / `runner.py` の注入経路に GitHub/GitLab 分岐を入れたくない。`IssueProvider` Protocol の 1 メソッド呼び出しで完結する設計にしたい（skill 側および harness 側を将来も provider 中立に保つ）。
- **maintainer として**、PR/MR が存在しない（branch 未 push / MR 未作成）状況でも `kaji run` を中断させず、`pr_id` / `pr_ref` 不在のまま skill を起動できるようにしたい（PR 作成前 step / docs-only step での実行性確保）。
- **maintainer として**、子 Issue `local-pc5090-9` で `/pr-fix` / `/pr-verify` の `gitlab` 許可解禁を行う際に、本 Issue で確立した `IssueProvider.resolve_pr_context` Protocol method を「契約」として依存できる状態にしたい。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| `prompt.py` 内で `provider.type` を `if/elif` 分岐し、GitLab のときだけ `glab mr list` を直接呼ぶ | Issue 完了条件「skill のプロンプトの注入経路に GitHub/GitLab 分岐が入っていない（diff 確認）」に違反。`prompt.py` を provider 中立に保つには `IssueProvider` Protocol 経由の polymorphic 呼び出しが必須 |
| `resolve_pr_context` を `IssueContext` 解決と同時に行い、`IssueContext` に `pr_id` / `pr_ref` を埋め込む | `IssueContext` は **Issue 解決時点（`kaji run` 起動直後）** で確定する設計。PR は Issue 着手後（具体的には `i-pr` step 実行後）の任意のタイミングで作られるため、`IssueContext` に同居させると「Issue は解決できたが PR がまだない」というありふれた状態で fail-fast せざるを得なくなる。`PRContext` を別 DTO とし、`prompt.py` 内で **conditional に** マージするほうが状態モデルとして自然 |
| MR 逆引きのキーに「現在 checkout 中の branch（`git rev-parse --abbrev-ref HEAD`）」を使う | runner は worktree 外（`project_root` = bare repository / main worktree）で起動されることが多く、`git rev-parse` を runner cwd で取ると意図しない branch を見る危険がある。`IssueContext.branch_name` を一次情報とすれば、Issue 起票時点で確定した期待 branch を使えて副作用がない（`branch_name` は `IssueContext` 解決時に provider が `<prefix>/<id>` で組み立てる確定値） |
| GitLab `resolve_pr_context` は本 Issue で実装するが、`prompt.py` への注入は子 Issue `local-pc5090-9` で行う | 本 Issue の完了条件に「`kaji_harness/prompt.py` が `provider.type='gitlab'` 配下で `pr_id` / `pr_ref` を自動注入する」が明記されており、注入経路まで本 Issue 内で完結させる |
| `IssueProvider` Protocol を変更せず、`prompt.py` 側で `getattr` ガードを入れる（前 revision の方針） | 設計の自己整合性を欠く。前半（インターフェース / Step 2）で Protocol 拡張する前提を書きながら、後半「GitHub / Local provider の取り扱い」で「Protocol を変更しない」と結論する矛盾があった。実コードでは `isinstance(provider, IssueProvider)` 呼び出しが存在しない（`grep` 結果: `kaji_harness/cli_main.py:972` の型注釈 `provider: IssueProvider` が唯一の利用箇所）ため、`@runtime_checkable` の `isinstance` 副作用への懸念は **本コードベースでは杞憂**。Protocol を拡張し、GitHub / Local provider に no-op `return None` を実装する設計に統一する（後述「Protocol 拡張と no-op 実装」） |
| `_resolve_pr_context_safe` helper で `except Exception` で全例外を WARN + None に落とす（前 revision の方針） | `docs/reference/python/error-handling.md` § 基本原則 1「広すぎる catch を避ける（`except Exception: pass` 禁止）」に違反。実装バグ・契約逸脱（`GitLabProviderError` 以外の AttributeError / TypeError 等）まで隠してしまい、デバッグ困難になる。**`GitLabProviderError` のみ catch** し、それ以外は raise 継続する（後述「例外吸収方針」） |

## インターフェース

### 新規データ型

```python
# kaji_harness/providers/models.py に追加
@dataclass(frozen=True)
class PRContext:
    """Skill 注入用の PR コンテキスト変数。

    `IssueContext` と分離している理由は § 代替案を参照。`prompt.py` は
    `provider.resolve_pr_context(branch_name)` の戻り値が `None` でない場合に限り
    `pr_id` / `pr_ref` を variables に追加する。

    Attributes:
        pr_id: provider 内部 ID。github なら ``"42"``、gitlab なら project-local
            ``merge_request_iid`` の文字列（``"42"``）。
        pr_ref: 人間可読参照。github なら ``"#42"``、gitlab なら ``"gl:42"``
            （`kaji-pr-mr-bridge.md` § 設計原則 1 に準拠）。
    """
    pr_id: str
    pr_ref: str
```

### Protocol 拡張

```python
# kaji_harness/providers/base.py に追加
@runtime_checkable
class IssueProvider(Protocol):
    # ... 既存 8 メソッドはそのまま ...

    def resolve_pr_context(self, branch_name: str) -> PRContext | None:
        """branch 名から PR/MR を逆引きし `PRContext` を返す。

        本 Issue では GitLab 側で実装。GitHub / Local provider は同じ Issue 内で
        ``return None`` の no-op を実装する（後述「Protocol 拡張と no-op 実装」）。
        GitHub の本実装は forge 採用後の別 Issue で扱う（bucket
        ``local-pc5090-1`` Phase 4 申し送り）。

        Returns:
            PRContext: branch に対応する open な PR/MR が一意に存在する場合。
            None: PR/MR が存在しない場合（branch 未 push / MR 未作成 / draft 等で
                探索結果が 0 件）。fail-fast せず None を返す責務。

        Raises:
            Provider 固有のエラー: 複数該当（運用上の異常）/ CLI / API 失敗時。
        """
        ...
```

### `GitLabProvider.resolve_pr_context` 仕様

```python
def resolve_pr_context(self, branch_name: str) -> PRContext | None:
    """branch から MR を逆引きする。

    内部実装: 既存 `resolve_mr_iid_from_branch` を呼ぶ。
    `GitLabProviderError` の中で「該当なし」だけを `None` に翻訳し、
    複数該当 / API エラーは raise を継承させる。
    """
```

- 入力: `branch_name`（例: `"feat/local-pc5090-7"`）
- 出力（成功 / 一意）: `PRContext(pr_id=iid, pr_ref=f"gl:{iid}")`
- 出力（該当 0 件）: `None`
- 例外（複数該当 / API エラー / `glab` 未インストール）: `GitLabProviderError`

### `GitHubProvider.resolve_pr_context` 仕様（no-op）

```python
def resolve_pr_context(self, branch_name: str) -> PRContext | None:
    """no-op 実装。本実装は forge 採用後の別 Issue で扱う。

    本 Issue では Protocol 整合のため ``return None`` のみ。GitHub 側で本実装
    すると ``gh pr list --head <branch>`` 等の subprocess hit が増えるが、
    skill 側暫定運用記述（`kaji pr list --search`）は本 Issue では削除しない
    （子 Issue ``local-pc5090-9`` の OUT スコープ）ため、no-op で十分。
    """
    del branch_name
    return None
```

### `LocalProvider.resolve_pr_context` 仕様（no-op）

```python
def resolve_pr_context(self, branch_name: str) -> PRContext | None:
    """no-op 実装。local mode に PR 概念は存在しない。

    `provider.type='local'` 配下では `/i-pr` / `/pr-fix` / `/pr-verify` が
    Step 0 で ABORT するため、本 method が呼ばれた時点で何かが間違っている
    が、Protocol 整合のため None を返す（防御的）。
    """
    del branch_name
    return None
```

### `prompt.py` の注入経路

```python
# kaji_harness/prompt.py
def build_prompt(
    step: Step,
    issue: str,
    state: SessionState,
    workflow: Workflow,
    issue_context: IssueContext,
    pr_context: PRContext | None = None,  # 新規 keyword 引数（default None）
) -> str:
    ...
    variables: dict[str, object] = {
        "issue_id": issue_context.issue_id,
        # ... 既存 9 変数 ...
    }
    if pr_context is not None:
        variables["pr_id"] = pr_context.pr_id
        variables["pr_ref"] = pr_context.pr_ref
    ...
```

`runner.py` 側で各 step の build_prompt 呼び出し直前に provider から `PRContext` を解決:

```python
# kaji_harness/runner.py 内（main loop 内、build_prompt 呼び出し前）
pr_context = self._resolve_pr_context_safe(provider, issue_context.branch_name)
prompt = build_prompt(
    current_step, run_ctx.canonical_id, state, self.workflow,
    issue_context=issue_context, pr_context=pr_context,
)
```

### `_resolve_pr_context_safe` の例外吸収方針

```python
def _resolve_pr_context_safe(
    self, provider: IssueProvider, branch_name: str
) -> PRContext | None:
    """provider から PRContext を解決。known provider error のみ WARN + None。

    catch する範囲は ``GitLabProviderError`` のみ（``GitHubProvider`` の本実装後
    は ``GitHubProviderError`` も追加）。それ以外（``AttributeError`` /
    ``TypeError`` 等の実装バグ、``KeyboardInterrupt`` 等の signal 系）は
    raise を継承する。``docs/reference/python/error-handling.md`` § 基本原則 1
    「握り潰し禁止」「広すぎる catch を避ける」遵守。
    """
    from .providers.gitlab import GitLabProviderError

    try:
        return provider.resolve_pr_context(branch_name)
    except GitLabProviderError as exc:
        sys.stderr.write(
            f"WARNING: resolve_pr_context for branch {branch_name!r} failed: {exc}\n"
            f"  pr_id / pr_ref will not be auto-injected; skill must resolve manually.\n"
        )
        return None
```

### 使用例

```python
# 例 1: provider.type='gitlab' で feat/local-pc5090-7 が MR !42 にひもづく
provider = GitLabProvider(repo="aki/kaji", repo_root=Path("/repo"))
ctx = provider.resolve_pr_context("feat/local-pc5090-7")
assert ctx == PRContext(pr_id="42", pr_ref="gl:42")

# 例 2: branch 未 push / MR 未作成
ctx = provider.resolve_pr_context("feat/local-pc5090-7")
assert ctx is None  # GitLabProviderError("no open merge request") を None に翻訳

# 例 3: GitHub / Local provider（no-op）
provider = GitHubProvider(repo="aki/kaji", repo_root=Path("/repo"))
assert provider.resolve_pr_context("feat/153") is None

# 例 4: prompt 経路（呼び出し側は provider type を知らない）
pr_ctx = self._resolve_pr_context_safe(provider, "feat/local-pc5090-7")
prompt = build_prompt(step, issue_id, state, workflow,
                     issue_context=ictx, pr_context=pr_ctx)
# prompt 文字列内に "- pr_id: 42" / "- pr_ref: gl:42" が含まれる（GitLab 一意 MR）
# または含まれない（None / no-op の場合）
```

### エラー挙動

| シナリオ | 挙動 |
|---|---|
| GitLab に MR が存在しない | `resolve_pr_context` は `None` 返却（`prompt.py` は `pr_*` 変数を注入しない） |
| GitLab `glab` CLI 失敗 / 同一 source_branch に複数 open MR | `GitLabProviderError` を runner の `_resolve_pr_context_safe` で捕捉し WARN + `None`（workflow 継続。`/i-pr` 等の skill 側に PR 解決を委ねる） |
| GitHub / Local provider | no-op 実装が常に `None` 返却（既存 skill は SKILL.md 暫定運用記述に従い `kaji pr list --search` で自前解決。本 Issue では skill 側変更しない） |
| `resolve_pr_context` 内の予期しない例外（`AttributeError` / `TypeError` 等の実装バグ） | `_resolve_pr_context_safe` は catch しない。raise が伝播し runner の通常エラーハンドリング経路に乗る（`docs/reference/python/error-handling.md` § 基本原則 1 遵守） |

## 制約・前提条件

- 既存 `GitLabProvider.resolve_mr_iid_from_branch` (`kaji_harness/providers/gitlab.py:404-437`) を再利用する。subprocess 起動 / API 呼び出しの 2 度 hit を避ける
- `IssueContext.branch_name` は `<branch_prefix>/<issue_id>` の確定値（`build_branch_name` 経由）。runner は `git rev-parse` 等で actual branch を再取得しない
- `prompt.py` の signature 変更は keyword-only 引数追加 + default `None` で backward compatible（既存の test / 呼び出し側を破壊しない）
- 本 Issue では skill SKILL.md の暫定運用記述削除と `/pr-fix` / `/pr-verify` の `gitlab` 許可解禁は行わない（OUT スコープ。子 Issue `local-pc5090-9`）
- GitHub provider にも Protocol 整合のため `resolve_pr_context` を no-op で実装するが、本実装（`gh pr list --head <branch>` 等）は本 Issue の OUT スコープ。bucket `local-pc5090-1` Phase 4 申し送り（`<Provider>` 表記）に従い、forge 採用時に別 Issue で扱う
- `IssueProvider` Protocol への method 追加は **本 Issue 内で完結**（前 revision の「Protocol 変更しない」方針は Must Fix 2 で撤回）。`isinstance(provider, IssueProvider)` 呼び出しが実コードに存在しないこと（`grep` 確認済）を根拠に `@runtime_checkable` の副作用懸念を解消する

## skill 側の対応範囲

本 Issue 完了時点で `pr_id` / `pr_ref` の自動注入が実際に効く skill / 効かない skill を明示する。

| skill | 現状の Step 0 provider check | 本 Issue 完了時点の動作 | 子 Issue `local-pc5090-9` で必要な変更 |
|-------|--------------------------------|--------------------------|--------------------------------------|
| `/i-pr` | `github` / `gitlab` 許可（`SKILL.md:74-90`） | **GitLab で `pr_id` / `pr_ref` 自動注入が効く**。ただし `/i-pr` は PR 新規作成 skill のため `pr_id` 注入は補助的（Step 4 で `kaji pr create` 出力から確定する経路が一次。事前注入は冗長だが害はない） | SKILL.md L53 の暫定運用記述（「現時点では行わない」）を「自動注入される」に書き換え |
| `/pr-fix` | `github` のみ許可、`gitlab` で ABORT（`SKILL.md:62-92`） | **GitLab では Step 0 で ABORT され、自動注入は効かない**。注入経路は成立しているが skill 側で受け取る前に終了する | (a) Step 0 を `github` / `gitlab` 許可に変更、(b) SKILL.md L44 暫定運用記述削除 |
| `/pr-verify` | `github` のみ許可、`gitlab` で ABORT（`SKILL.md:74-104`） | 同上 | 同上（SKILL.md L49 暫定運用記述削除） |

> **設計含意**: 本 Issue は **harness 側のインフラ整備**として完結し、skill 側の `gitlab` 許可解禁は子 Issue `local-pc5090-9` の責務として明確に分離する。本 Issue の Issue 本文「skill のプロンプト注入経路に GitHub/GitLab 分岐が入っていない（diff 確認）」は **harness 側 (`prompt.py` / `runner.py`) の diff** に対する要件であり、skill SKILL.md の更新タイミングはここで議論しない（skill 側 diff は子 Issue `local-pc5090-9` のレビュー対象）。

## 変更スコープ

| ファイル | 変更内容 |
|---|---|
| `kaji_harness/providers/models.py` | `PRContext` dataclass 新規追加 |
| `kaji_harness/providers/base.py` | `IssueProvider` Protocol に `resolve_pr_context` メソッド追加 |
| `kaji_harness/providers/__init__.py` | `PRContext` を re-export（`from .providers import PRContext` を成立させる） |
| `kaji_harness/providers/gitlab.py` | `GitLabProvider.resolve_pr_context` 本実装 |
| `kaji_harness/providers/github.py` | `GitHubProvider.resolve_pr_context` no-op 実装（`return None`） |
| `kaji_harness/providers/local.py` | `LocalProvider.resolve_pr_context` no-op 実装（`return None`） |
| `kaji_harness/prompt.py` | `build_prompt` に `pr_context` keyword 引数追加 + variables 拡張 |
| `kaji_harness/runner.py` | `_resolve_pr_context_safe` helper 追加 + main loop での呼び出し |
| `tests/test_providers_gitlab.py` | `resolve_pr_context` の Small テスト追加（一意 / 該当なし / 複数該当 / API エラー） |
| `tests/test_providers_github.py` | `GitHubProvider.resolve_pr_context` no-op テスト（1 case） |
| `tests/test_providers_local.py` | `LocalProvider.resolve_pr_context` no-op テスト（1 case） |
| `tests/test_prompt.py`（無ければ新規） | `pr_context` 注入時 / 非注入時の variables 出力テスト |
| `tests/test_runner_pr_context.py`（新規） | runner の PR 解決経路の Medium テスト + `_resolve_pr_context_safe` の例外吸収範囲テスト |

## 方針

### Protocol 拡張と no-op 実装

`IssueProvider` Protocol に `resolve_pr_context` を追加し、全 provider に実装を持たせる。GitHub / Local は no-op (`return None`) を実装し、GitLab のみ本実装する。

**`@runtime_checkable` の副作用に関する確認結果**:

```
$ grep -rn "isinstance.*IssueProvider" kaji_harness/ tests/
(no results)
$ grep -rn "IssueProvider" kaji_harness/
kaji_harness/providers/base.py:16: class IssueProvider(Protocol):
kaji_harness/cli_main.py:972: def _handle_issue_context(provider: IssueProvider, rest: list[str]) -> int:
```

`isinstance(provider, IssueProvider)` 呼び出しは存在せず、Protocol 利用は型注釈のみ。よって Protocol method 追加で `isinstance` チェック結果が変わる懸念は本コードベースでは発生しない。

### Step 1: `PRContext` 追加

`kaji_harness/providers/models.py` に新 dataclass を追加。`@dataclass(frozen=True)` で immutable とし、`Issue` / `IssueContext` と同じ規約に揃える。`kaji_harness/providers/__init__.py` の `__all__` に `PRContext` を追加。

### Step 2: Protocol 拡張

`kaji_harness/providers/base.py` の `IssueProvider` Protocol に `resolve_pr_context(branch_name: str) -> PRContext | None` を追加。Protocol method の本体は `...`（既存メソッドと同様）。

### Step 3: GitLab 本実装

```python
# kaji_harness/providers/gitlab.py 内に追加
_NO_MR_FOUND_MSG_PREFIX = "no open merge request found for source branch"


class GitLabProvider:
    # ...
    def resolve_pr_context(self, branch_name: str) -> PRContext | None:
        try:
            iid = self.resolve_mr_iid_from_branch(branch_name)
        except GitLabProviderError as exc:
            if str(exc).startswith(_NO_MR_FOUND_MSG_PREFIX):
                return None
            raise  # 複数該当 / API エラーは raise 継続（runner で WARN + None に変換）
        return PRContext(pr_id=iid, pr_ref=f"gl:{iid}")
```

「該当なし」の判定は既存 `resolve_mr_iid_from_branch` のメッセージ文字列マッチに依存する。脆さを下げるため、本実装と同時に `resolve_mr_iid_from_branch` 側のエラーメッセージを `_NO_MR_FOUND_MSG_PREFIX` 定数 + branch 名 補完の形に書き換え、両側でそれを参照する。

### Step 4: GitHub / Local の no-op 実装

両 provider に同一形のメソッドを追加（§ インターフェース参照）。`del branch_name` で unused 警告を抑制。

### Step 5: `prompt.py` 拡張

`build_prompt` の signature に `pr_context: PRContext | None = None` を keyword 引数として追加（default あり = backward compatible）。`variables` 辞書組み立て後、`pr_context is not None` なら `pr_id` / `pr_ref` を追記。`header` の組み立てには既存 `"\n".join(f"- {k}: {v}" for k, v in variables.items())` がそのまま使える。

### Step 6: `runner.py` 統合

main loop（`runner.py:255-289`）の `build_prompt` 呼び出し前に `_resolve_pr_context_safe` helper を呼ぶ。helper は `GitLabProviderError` のみ catch（§ 「`_resolve_pr_context_safe` の例外吸収方針」参照）。

毎 step 呼び出すか、`run_ctx` 解決時に 1 度だけ呼ぶかは設計判断。**毎 step 呼び出す**を採用する理由: PR は workflow 実行中（具体的には `i-pr` step）に新規作成されるため、step ごとに最新状態を見るほうが自然。subprocess hit のコストは 1 step あたり 1 回の `glab api` であり、step 全体のコスト（agent 起動）に比べれば無視できる。

### Step 7: テスト

§ テスト戦略 参照。

## テスト戦略

### 変更タイプ

実行時コード変更（provider method 追加 + prompt / runner 拡張）。

### Small テスト

- **`PRContext` dataclass**: frozen / equality / repr の基本確認（dataclass 標準動作なので minimal 1 case）
- **`GitLabProvider.resolve_pr_context`**:
  - 一意 MR が存在 → `PRContext(pr_id="42", pr_ref="gl:42")` を返す
  - 該当 0 件（`resolve_mr_iid_from_branch` の `_NO_MR_FOUND_MSG_PREFIX` メッセージ） → `None` を返す
  - 複数該当 / API エラー → `GitLabProviderError` がそのまま raise される（runner 側で吸収する責務）
- **`GitHubProvider.resolve_pr_context`** (no-op): branch 名に関わらず `None` 返却（1 case）
- **`LocalProvider.resolve_pr_context`** (no-op): branch 名に関わらず `None` 返却（1 case）
- **`build_prompt` の `pr_context` 引数**:
  - `pr_context=None` → variables に `pr_id` / `pr_ref` が含まれない
  - `pr_context=PRContext(pr_id="42", pr_ref="gl:42")` → variables に `pr_id="42"` / `pr_ref="gl:42"` が含まれ、`header` 文字列にも反映される
  - 既存の no-arg 呼び出し（`pr_context` 省略）が default で動く（backward compatibility 確認）

### Medium テスト（新規 `tests/test_runner_pr_context.py`）

- **runner の PR 解決経路 round-trip**:
  - mock GitLab provider（`resolve_mr_iid_from_branch` を mock）+ in-memory workflow + 1-step config で `WorkflowRunner.run` を呼ぶ
  - `build_prompt` に `pr_context` が正しく渡り、agent 呼び出し時の prompt 文字列に `pr_id: 42` / `pr_ref: gl:42` が含まれる
  - mock provider が `None` を返した場合、prompt に `pr_id` / `pr_ref` が含まれない
- **`_resolve_pr_context_safe` の例外吸収範囲**:
  - mock provider が `GitLabProviderError("API down")` を raise → `None` 返却 + stderr に WARN 出力 + workflow 継続
  - mock provider が `AttributeError("bug")` を raise → catch せず raise 継続（`pytest.raises(AttributeError)`）
  - mock provider が `KeyboardInterrupt` を raise → catch せず raise 継続（`pytest.raises(KeyboardInterrupt)`）
- **provider.type 分岐の不在 確認**:
  - `kaji_harness/prompt.py` / `kaji_harness/runner.py` 全体を grep し、`provider.type == "gitlab"` / `provider_type == "gitlab"` / `isinstance(.*GitLabProvider)` の文字列が **含まれない** ことを test で assert（完了条件「skill のプロンプトの注入経路に GitHub/GitLab 分岐が入っていない」）

### Large テスト

- **本 Issue では追加しない**。
- **理由**: GitLab E2E 群は子 Issue `local-pc5090-10` (`make test-large-gitlab`) に集約される設計（EPIC `local-pc5090-4` § 子 Issue 構成 #6）。`make test-large-gitlab` の項目に「branch → MR 自動逆引き → prompt 注入の E2E」を追加するのが妥当だが、それ自体は子 Issue #6 の責務で本 Issue で先取り実装しない（`docs/dev/testing-convention.md` の「省略してよい理由」: 別 Issue で同等以上のカバレッジを取る計画があり、本 Issue 単独で重複 Large テストを書いても保守コストの増加に対してリターンが薄い）
- **代替**: 本 Issue 内では Medium テスト（mock provider 経由の runner round-trip）で「runner → provider → prompt」の経路接続を検証し、E2E は子 Issue #6 に任せる

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新技術選定なし。既存 GitLab provider 上の機能追加 |
| docs/ARCHITECTURE.md | なし | provider 内の method 追加 + prompt の variable 追加にとどまる |
| docs/dev/ | なし | ワークフロー / 開発手順への影響なし |
| docs/reference/ | なし | コーディング規約への影響なし |
| docs/cli-guides/ | なし | CLI 仕様の追加なし。`pr_id` / `pr_ref` は skill prompt 内部の variable で CLI ではない |
| CLAUDE.md | なし | プロジェクト規約への影響なし |
| `.claude/skills/{pr-fix,pr-verify,i-pr}/SKILL.md` の暫定運用記述 / provider check | なし（本 Issue では） | 子 Issue `local-pc5090-9` で扱う（Issue 本文 OUT 明記）。詳細は § skill 側の対応範囲 |

> **副次的な docs / skill 影響**: 子 Issue `local-pc5090-9` で skill SKILL.md の暫定運用記述削除 + `/pr-fix` / `/pr-verify` の `gitlab` 許可解禁を行う際、本 Issue で実装した「自動注入経路」と「`IssueProvider.resolve_pr_context` Protocol method」を前提として doc / skill を書き換える。本 Issue では skill 修正を行わないため、`local-pc5090-9` 側で「GitLab は本実装で注入される / GitHub は no-op で注入されない」状態を doc / skill に反映する責務を負う。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| EPIC `local-pc5090-4` 本文 § 確定事項 #7 | 本 EPIC Issue 本文（`kaji issue view local-pc5090-4`） | 「MR の `resolved` 状態 / approval state 等の GitLab 固有情報は provider 内部で保持、外向きには GitHub 互換 shape を返す。skill 側には GitHub/GitLab 分岐を入れない」を本 Issue の interface 設計の根拠とする |
| `kaji-pr-mr-bridge.md` § 設計原則 | `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md` (本 worktree 内) | 「skill 側 contract は GitHub 互換 subset を正本」「GitLab 固有差分は provider 内部で吸収」「skill 側に GitHub/GitLab 分岐を入れない」を `pr_ref` の `gl:<iid>` 命名規約の根拠とする |
| 既存 `IssueProvider` Protocol | `kaji_harness/providers/base.py:16-88` | `resolve_issue_context` の interface を `resolve_pr_context` のテンプレートとする（戻り値 DTO + 単一 method）。Protocol 拡張による no-op 実装パターンの根拠 |
| `IssueProvider` の利用箇所調査 | `grep -rn "IssueProvider" kaji_harness/ tests/` 結果 | `isinstance(provider, IssueProvider)` 呼び出しが存在せず、`cli_main.py:972` の型注釈 `provider: IssueProvider` のみ。`@runtime_checkable` の `isinstance` 副作用懸念が本コードベースでは発生しないことを確認 |
| 既存 `GitHubProvider.resolve_issue_context` | `kaji_harness/providers/github.py:281-304` | provider が IssueContext を組み立てる責務範囲（`issue_id` / `issue_ref` / `branch_name` 等の確定）を `PRContext` にも踏襲する |
| 既存 `GitLabProvider.resolve_mr_iid_from_branch` | `kaji_harness/providers/gitlab.py:404-437` | branch → IID 逆引きの実装。本 Issue では再利用する（subprocess hit を 2 度行わない）。複数該当 / 該当なしのエラーメッセージ仕様を引用する。`_NO_MR_FOUND_MSG_PREFIX` 定数化で結合度を明示する |
| 既存 `prompt.py` build_prompt | `kaji_harness/prompt.py:13-89` | `IssueContext` から variables を組み立てる既存パターン。`PRContext` を同じ仕組みで追加する |
| 既存 `runner.py` build_prompt 呼び出し | `kaji_harness/runner.py:277-283` | main loop 内で step 単位に build_prompt を呼ぶ既存構造。本 Issue で `pr_context` 解決を直前に挿入する |
| エラーハンドリング規約 § 基本原則 | `docs/reference/python/error-handling.md:6-10` | 「握り潰し禁止 — `except Exception: pass` は書かない」「適切な粒度で wrap する」を `_resolve_pr_context_safe` の `GitLabProviderError` 限定 catch の根拠とする |
| 暫定運用記述（差し替え対象、本 Issue は記述削除を扱わない） | `.claude/skills/pr-fix/SKILL.md:44`、`pr-verify/SKILL.md:49`、`i-pr/SKILL.md:53` | 「`pr_id` はハーネス経由では現時点ではプロンプトに自動注入されない」を本 Issue 完了で「自動注入される」状態に変える根拠（記述削除自体は子 Issue `local-pc5090-9`） |
| skill provider check 現状 | `.claude/skills/pr-fix/SKILL.md:62-92`、`pr-verify/SKILL.md:74-104`、`i-pr/SKILL.md:74-90` | `/pr-fix` / `/pr-verify` は `gitlab` で ABORT、`/i-pr` のみ `gitlab` 許可。本 Issue 完了で「自動注入が実際に効く skill = `/i-pr` のみ」と明確化する根拠。`/pr-fix` / `/pr-verify` の `gitlab` 許可解禁は子 Issue `local-pc5090-9` の責務 |
| bucket Phase 4 申し送り | `.kaji/issues/local-pc5090-1-forge-bucket-forge-pr-context-gitlabprov/issue.md` § Phase 4 申し送り | 「`<Provider>.resolve_pr_context(branch_name)` 実装」「`pr_id` / `pr_ref` の prompt.py 自動注入」を本 Issue が GitLab 側で先取り実装する根拠。GitHub 側本実装は別 Issue で扱う根拠 |


</details>

## 概要

branch 名から GitLab MR を逆引きし、`pr_id`（project-local `merge_request_iid`） / `pr_ref` を `prompt.py` に注入する経路を実装する。GitLab 固有の `resolved` 状態などは確定事項 #7 に従い provider 内部で保持する。

## 目的

- skill（`pr-fix` / `pr-verify` / `i-pr`）が forge 切替時にも同じ前提で動作するよう、PR/MR コンテキストの自動注入を GitLab でも実現する
- `local-pc5090-1` bucket の Phase 4 申し送り「`<Provider>.resolve_pr_context(branch_name)`」を GitLab 側で先取り実装する

## ユーザーストーリー

- kaji ユーザーとして、`/pr-fix` 等の skill が起動したとき、現在の branch から MR を自動検出して `pr_id` / `pr_ref` がプロンプトに自動注入されてほしい
- maintainer として、`pr-fix/SKILL.md` / `pr-verify/SKILL.md` の暫定運用記述（`kaji pr list --search` で取得）を本注入経路に切替できる状態にしたい

## スコープ

### IN

- `GitLabProvider.resolve_pr_context(branch_name) -> PRContext | IssueContext` 実装:
  - `glab mr list --source-branch <branch>` 等で MR を逆引き（`--repo` 明示指定）
  - `pr_id` = project-local `merge_request_iid`
  - `pr_ref` は `gl:<iid>` 形式で統一（`kaji-pr-mr-bridge.md` 準拠）
- MR の `resolved` 状態 / approval state 等の GitLab 固有情報は **確定事項 #7 に従い provider 内部で保持**、外向きには GitHub 互換 shape を返す
- `kaji_harness/prompt.py` の prompt 注入経路:
  - `provider.type='gitlab'` 配下でも `pr_id` / `pr_ref` が自動注入される統合
  - 既存 GitHub 経路と同じ public interface を維持（skill 修正不要）

### OUT

- skill 側の暫定運用記述削除 → 子 Issue #5 で扱う（docs / skill 整理）
- 実 GitLab 通信 E2E → 子 Issue #6

## 完了条件

- [x] `GitLabProvider.resolve_pr_context(branch)` が MR を IID で返す
- [x] `kaji_harness/prompt.py` が `provider.type='gitlab'` 配下で `pr_id` / `pr_ref` を自動注入する
- [x] Medium テストで branch → MR 逆引きの round-trip が緑（mock 経由）
- [x] skill のプロンプト注入経路に GitHub/GitLab 分岐が入っていない（diff 確認）
- [x] `make check` 緑

## 依存

- 子 Issue #1（`GitLabProvider` 実装）— 完了必須

## 参照

- 確定事項 #7: 本 EPIC 本文
- 既存 GitHub 経路: `kaji_harness/providers/github.py:281` `resolve_issue_context` ほか
- prompt 注入: `kaji_harness/prompt.py`
- bucket 由来の Phase 4 申し送り: `local-pc5090-1`
- OQ-2 決定文書: `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md`

