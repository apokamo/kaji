# [設計] GitLabProvider.resolve_pr_context + prompt 注入経路

Issue: local-pc5090-7
EPIC: local-pc5090-4 (GitLab 対応検証 EPIC)

## 概要

GitLab branch 名から MR を逆引きする `GitLabProvider.resolve_pr_context(branch_name)` を実装し、`kaji_harness/prompt.py` に provider 共通の PR コンテキスト注入経路を追加する。これにより `provider.type='gitlab'` 配下でも `pr_id`（project-local `merge_request_iid`）/ `pr_ref`（`gl:<iid>`）が skill プロンプトに自動注入される状態を作る。skill 側のプロンプト経路には GitHub/GitLab 分岐を入れない。

## 背景・目的

EPIC `local-pc5090-4` 連番 #3。bucket Issue `local-pc5090-1` の Phase 4 申し送り「`<Provider>.resolve_pr_context(branch_name)` 実装 + `pr_id` / `pr_ref` の `prompt.py` 自動注入」を GitLab 側で先取り実装する。現状、`pr-fix` / `pr-verify` / `i-pr` の SKILL.md は冒頭で「`pr_id` はプロンプトに自動注入されない」と明記し、Step 1 で `kaji pr list --search [issue_id]` から取得する暫定運用を採っている (`.claude/skills/pr-fix/SKILL.md:44`、同 `pr-verify/SKILL.md:49`、`i-pr/SKILL.md:53`)。本 Issue 完了時点で「自動注入経路」が成立する状態を作り、skill 側の暫定運用記述削除（子 Issue `local-pc5090-9`）に渡す。

### ユーザーストーリー

- **kaji ユーザーとして**、`provider.type='gitlab'` の workflow で `/pr-fix` / `/pr-verify` / `/i-pr` の skill が起動したとき、現在の branch から MR が自動検出され、`pr_id` / `pr_ref` がプロンプトに既に入っている状態にしたい（GitHub mode と同じ前提に揃う）。
- **maintainer として**、`prompt.py` の注入経路に GitHub/GitLab 分岐を入れたくない。Provider Protocol の 1 メソッド呼び出しで完結する設計にしたい（skill 側は将来も provider 中立を保つ）。
- **maintainer として**、PR/MR が存在しない（branch が未 push / MR 未作成）状況でも `kaji run` を中断させず、`pr_id` / `pr_ref` 不在のまま skill を起動できるようにしたい（skill 側の暫定運用記述削除前後の互換性）。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| `prompt.py` 内で `provider.type` を `if/elif` 分岐し、GitLab のときだけ `glab mr list` を直接呼ぶ | Issue 完了条件「skill のプロンプト注入経路に GitHub/GitLab 分岐が入っていない（diff 確認）」に違反。`prompt.py` を provider 中立に保つには `IssueProvider` Protocol 経由の polymorphic 呼び出しが必須 |
| `resolve_pr_context` を `IssueContext` 解決と同時に行い、`IssueContext` に `pr_id` / `pr_ref` を埋め込む | `IssueContext` は **Issue 解決時点（`kaji run` 起動直後）** で確定する設計。PR は Issue 着手後の任意のタイミングで作られるため、`IssueContext` に同居させると「Issue は解決できたが PR がまだない」というありふれた状態で fail-fast せざるを得なくなる。`PRContext` を別 DTO とし、`prompt.py` 内で **conditional に** マージするほうが状態モデルとして自然 |
| MR 逆引きのキーに「現在 checkout 中の branch（`git rev-parse --abbrev-ref HEAD`）」を使う | runner は worktree 外（`project_root` = bare repository / main worktree）で起動されることが多く、`git rev-parse` を runner cwd で取ると意図しない branch を見る危険がある。`IssueContext.branch_name` を一次情報とすれば、Issue 起票時点で確定した期待 branch を使えて副作用がない（`branch_name` は `IssueContext` 解決時に provider が `<prefix>/<id>` で組み立てる確定値） |
| GitLab `resolve_pr_context` は本 Issue で実装するが、`prompt.py` への注入は子 Issue #5 (`local-pc5090-9`) で行う | 本 Issue の完了条件に「`kaji_harness/prompt.py` が `provider.type='gitlab'` 配下で `pr_id` / `pr_ref` を自動注入する」が明記されており、注入経路まで本 Issue 内で完結させる |
| GitHub provider にも同時に `resolve_pr_context` を実装する | 本 Issue は **GitLab 側で先取り実装** が目的（EPIC 本文）。GitHub 側実装は bucket `local-pc5090-1` の Phase 4 申し送り（`<Provider>` 表記）に従い、forge 採用時に別 Issue で扱う。本 Issue 内では Protocol に default 実装を置き、GitHub provider は default を継承して `None` を返す（後述「GitHub / Local provider の取り扱い」） |

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

        Returns:
            PRContext: branch に対応する open な PR/MR が一意に存在する場合。
            None: PR/MR が存在しない場合（branch 未 push / MR 未作成 / draft 等で
                探索結果が 0 件）。fail-fast せず None を返す責務。

        Raises:
            Provider 固有のエラー: 複数該当（運用上の異常）/ CLI / API 失敗時。
        """
        ...
```

Protocol で `runtime_checkable` を維持するため、Protocol 自体には default 実装を置けない。**default 動作（常に `None` 返す）は `prompt.py` 側で `getattr(provider, "resolve_pr_context", None)` 経由で吸収**するか、各 provider の base class で実装する。本 Issue では後者を採らず、**`prompt.py` 側で `hasattr` ガード**を採用する（理由は § 方針）。

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

### `prompt.py` の注入経路

```python
# kaji_harness/prompt.py
def build_prompt(
    step: Step,
    issue: str,
    state: SessionState,
    workflow: Workflow,
    issue_context: IssueContext,
    pr_context: PRContext | None = None,  # 新規 keyword 引数
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

`_resolve_pr_context_safe` の責務:

1. `getattr(provider, "resolve_pr_context", None)` で method 存在チェック
2. method が無い provider（GitHub / Local 現状）→ `None` 返却で no-op
3. method 呼び出し中の例外（GitLab API エラー等）は **WARN ログを残して None 返却**（`pr_id` 注入失敗で workflow 全体を止めない）
4. 戻り値が `PRContext` なら そのまま返す

### 使用例

```python
# 例 1: provider.type='gitlab' で feat/local-pc5090-7 が MR !42 にひもづく
provider = GitLabProvider(repo="aki/kaji", repo_root=Path("/repo"))
ctx = provider.resolve_pr_context("feat/local-pc5090-7")
assert ctx == PRContext(pr_id="42", pr_ref="gl:42")

# 例 2: branch 未 push / MR 未作成
ctx = provider.resolve_pr_context("feat/local-pc5090-7")
assert ctx is None  # GitLabProviderError を None に翻訳

# 例 3: prompt 経路（呼び出し側は provider type を知らない）
pr_ctx = _resolve_pr_context_safe(provider, "feat/local-pc5090-7")
prompt = build_prompt(step, issue_id, state, workflow,
                     issue_context=ictx, pr_context=pr_ctx)
# prompt 文字列内に "- pr_id: 42" / "- pr_ref: gl:42" が含まれる
```

### エラー挙動

| シナリオ | 挙動 |
|---|---|
| GitLab に MR が存在しない | `resolve_pr_context` は `None` 返却（`prompt.py` は `pr_*` 変数を注入しない） |
| GitLab API / `glab` 失敗 | runner 側で `WARN` ログを残し `None` 扱い（workflow は継続。skill 側で `pr_id` を必要とする場合は skill が暫定運用記述に従って自前解決する） |
| GitLab で同一 source_branch に複数の open MR | `GitLabProviderError` を runner で捕捉し WARN + `None`（複数該当は運用上の異常だが、agent 起動を止めるほどではない判断。原因調査は skill / user に委ねる） |
| GitHub / Local provider | Protocol method 未実装。`hasattr` ガードで `None` 扱い（既存 skill は暫定運用記述に従って `kaji pr list --search` で自前解決） |

## 制約・前提条件

- 既存 `GitLabProvider.resolve_mr_iid_from_branch` (`kaji_harness/providers/gitlab.py:404-437`) を再利用する。subprocess 起動 / API 呼び出しの 2 度 hit を避ける
- `IssueContext.branch_name` は `<branch_prefix>/<issue_id>` の確定値（`build_branch_name` 経由）。runner は `git rev-parse` 等で actual branch を再取得しない
- `prompt.py` の signature 変更は keyword-only 引数追加 + default `None` で backward compatible（既存の test / 呼び出し側を破壊しない）
- 本 Issue では skill SKILL.md の暫定運用記述削除は行わない（OUT スコープ。子 Issue `local-pc5090-9`）
- GitHub provider にも `resolve_pr_context` を実装するのは本 Issue の OUT スコープ。bucket `local-pc5090-1` の Phase 4 申し送り（`<Provider>` 表記）に従い、forge 採用時に別 Issue で扱う

## 変更スコープ

| ファイル | 変更内容 |
|---|---|
| `kaji_harness/providers/models.py` | `PRContext` dataclass 新規追加 |
| `kaji_harness/providers/base.py` | `IssueProvider` Protocol に `resolve_pr_context` メソッド追加 |
| `kaji_harness/providers/__init__.py` | `PRContext` を re-export（`from .providers import PRContext` を成立させる） |
| `kaji_harness/providers/gitlab.py` | `GitLabProvider.resolve_pr_context` 実装 |
| `kaji_harness/prompt.py` | `build_prompt` に `pr_context` keyword 引数追加 + variables 拡張 |
| `kaji_harness/runner.py` | main loop の build_prompt 呼び出し前に provider から PR 解決 + 例外吸収 |
| `tests/test_providers_gitlab.py` | `resolve_pr_context` の Small テスト追加（一意 / 該当なし / 複数該当 / API エラー） |
| `tests/test_prompt.py`（無ければ新規） | `pr_context` 注入時 / 非注入時 の variables 出力テスト |
| `tests/test_phase3c_runner.py` または新規 `tests/test_runner_pr_context.py` | runner の PR 解決経路の Medium テスト（mock provider 経由で round-trip） |

## 方針

### Step 1: `PRContext` 追加

`kaji_harness/providers/models.py` に新 dataclass を追加。`@dataclass(frozen=True)` で immutable とし、`Issue` / `IssueContext` と同じ規約に揃える。

### Step 2: Protocol 拡張

`kaji_harness/providers/base.py` の `IssueProvider` Protocol に `resolve_pr_context(branch_name) -> PRContext | None` を追加。Protocol method には実装を書かないため、未実装 provider（GitHub / Local）は `hasattr` ガードで `prompt.py` 側が default `None` 動作にする（§ 「GitHub / Local provider の取り扱い」参照）。

### Step 3: `GitLabProvider.resolve_pr_context` 実装

```python
def resolve_pr_context(self, branch_name: str) -> PRContext | None:
    try:
        iid = self.resolve_mr_iid_from_branch(branch_name)
    except GitLabProviderError as exc:
        if "no open merge request" in str(exc):
            return None
        raise  # 複数該当 / API エラーは raise 継続
    return PRContext(pr_id=iid, pr_ref=f"gl:{iid}")
```

「該当なし」の判定は既存 `resolve_mr_iid_from_branch` のメッセージ文字列マッチに依存する。脆さを下げるため、本実装と同時に `resolve_mr_iid_from_branch` 側に `_NO_MR_FOUND_MSG` 等の module-level 定数を切り出し、両側でそれを参照する。

### Step 4: `prompt.py` 拡張

`build_prompt` の signature に `pr_context: PRContext | None = None` を keyword-only 引数として追加。`variables` 辞書組み立て後、`pr_context is not None` なら `pr_id` / `pr_ref` を追記。`header` の組み立てには既存 `"\n".join(f"- {k}: {v}" for k, v in variables.items())` がそのまま使えるため追加加工は不要。

### Step 5: `runner.py` 統合

main loop（`runner.py:255-289`）の `build_prompt` 呼び出し前に小さな helper を呼び出す:

```python
def _resolve_pr_context_safe(self, provider: IssueProvider, branch_name: str) -> PRContext | None:
    """provider から PRContext を解決。method 不在 / 例外は WARN + None。"""
    method = getattr(provider, "resolve_pr_context", None)
    if not callable(method):
        return None
    try:
        return method(branch_name)
    except Exception as exc:
        sys.stderr.write(
            f"WARNING: resolve_pr_context for branch {branch_name!r} failed: {exc}\n"
            f"  pr_id / pr_ref will not be auto-injected; skill must resolve manually.\n"
        )
        return None
```

毎 step 呼び出すか、`run_ctx` 解決時に 1 度だけ呼ぶかは設計判断。**毎 step 呼び出す**を採用する理由: PR は workflow 実行中（具体的には `i-pr` step）に新規作成されるため、step ごとに最新状態を見るほうが自然。subprocess hit のコストは 1 step あたり 1 回の `glab api` であり、step 全体のコスト（agent 起動）に比べれば無視できる。

### Step 6: テスト

§ テスト戦略 参照。

### GitHub / Local provider の取り扱い

- 本 Issue では GitHub / Local provider に `resolve_pr_context` 実装を追加しない（OUT スコープ）
- `prompt.py` 側は `getattr(provider, "resolve_pr_context", None)` の callable チェックではなく、**Protocol Optional method の呼び出しパターン**を採る。runtime には method 不在で `AttributeError` を発生させず `None` 扱いになる
- mypy 上の整合: Protocol に method を追加すると GitHub / Local が Protocol 不充足になる。これを避けるため、**Protocol の method 定義は `def resolve_pr_context(...) -> PRContext | None: ...` を default no-op として書ける形にせず**、Protocol 上は宣言のみとする。各 provider class が Protocol を `implements` 宣言していない場合（duck typing）、mypy は静的検査をスキップする。`runtime_checkable` Protocol の `isinstance` チェックは「指定された全 method が存在するか」のみ見るため、GitHub / Local が `resolve_pr_context` を持たないと `isinstance(provider, IssueProvider)` が False になる **副作用がある**

> **設計上の選択**: 副作用回避のため、本 Issue では Protocol を変更せず **`prompt.py` / `runner.py` 側でのみ `getattr` ガード**を入れる。Protocol 拡張は GitHub 側 `resolve_pr_context` 実装と同じタイミング（forge 採用後の別 Issue）で行う。

→ § Protocol 拡張は **「将来の意図表明」** として基本設計を残しつつ、本 Issue の実装ファイルからは Protocol 変更を除外する。`base.py` への変更は **コメントによる将来予定の追記のみ**（実 method 追加は行わない）。

## テスト戦略

### 変更タイプ

実行時コード変更（provider method 追加 + prompt / runner 拡張）。

### Small テスト

- **`PRContext` dataclass**: frozen / equality / repr の基本確認（dataclass 標準動作なので minimal 1 case）
- **`GitLabProvider.resolve_pr_context`**:
  - 一意 MR が存在 → `PRContext(pr_id="42", pr_ref="gl:42")` を返す
  - 該当 0 件（`resolve_mr_iid_from_branch` の "no open merge request" メッセージ） → `None` を返す
  - 複数該当 / API エラー → `GitLabProviderError` がそのまま raise される（runner 側で吸収する責務）
- **`build_prompt` の `pr_context` 引数**:
  - `pr_context=None` → variables に `pr_id` / `pr_ref` が含まれない
  - `pr_context=PRContext(pr_id="42", pr_ref="gl:42")` → variables に `pr_id="42"` / `pr_ref="gl:42"` が含まれ、`header` 文字列にも反映される
  - 既存の no-arg 呼び出し（`pr_context` 省略）が default で動く（backward compatibility 確認）
- **`_resolve_pr_context_safe` helper**:
  - method 不在の mock provider → `None` 返却 + 警告なし
  - method が `PRContext` を返す mock provider → `PRContext` 返却
  - method が例外を raise する mock provider → `None` 返却 + stderr に WARN 出力

### Medium テスト

- **runner の PR 解決経路 round-trip**:
  - mock GitLab provider（`resolve_mr_iid_from_branch` を mock）+ in-memory workflow + 1-step config で `WorkflowRunner.run` を呼ぶ
  - `build_prompt` に `pr_context` が正しく渡り、agent 呼び出し時の prompt 文字列に `pr_id: 42` / `pr_ref: gl:42` が含まれる
  - mock provider が `None` を返した場合、prompt に `pr_id` / `pr_ref` が含まれない
- **provider.type 分岐の不在 確認**:
  - `prompt.py` / `runner.py` の build_prompt 呼び出し path 周辺の diff に `provider.type == "gitlab"` 等の文字列分岐が **含まれない** ことを test or grep で確認（完了条件「skill のプロンプト注入経路に GitHub/GitLab 分岐が入っていない」）

### Large テスト

- **本 Issue では追加しない**。
- **理由**: GitLab E2E 群は子 Issue `local-pc5090-10` (`make test-large-gitlab`) に集約される設計（EPIC `local-pc5090-4` § 子 Issue 構成 #6）。`make test-large-gitlab` の項目に「branch → MR 自動逆引き → prompt 注入の E2E」を追加するのが妥当だが、それ自体は子 Issue #6 の責務で本 Issue で先取り実装しない（`docs/dev/testing-convention.md` の「省略してよい理由」: 別 Issue で同等以上のカバレッジを取る計画があり、本 Issue 単独で重複 Large テストを書いても保守コストの増加に対してリターンが薄い）
- **代替**: 本 Issue 内では Medium テスト（mock provider 経由の runner round-trip）で「runner → provider → prompt」の経路接続を検証し、E2E は子 Issue #6 に任せる

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新技術選定なし。既存 GitLab provider 上の機能追加 |
| docs/ARCHITECTURE.md | なし | provider 内の method 追加 + prompt の variable 追加にとどまる |
| docs/dev/ | なし | ワークフロー / 開発手順への影響なし。Provider Protocol の意図表明コメント追加のみ |
| docs/reference/ | なし | コーディング規約への影響なし |
| docs/cli-guides/ | なし | CLI 仕様の追加なし。`pr_id` / `pr_ref` は skill prompt 内部の variable で CLI ではない |
| CLAUDE.md | なし | プロジェクト規約への影響なし |
| `.claude/skills/{pr-fix,pr-verify,i-pr}/SKILL.md` の暫定運用記述 | なし（本 Issue では） | 子 Issue `local-pc5090-9` で扱う（Issue 本文 OUT 明記） |

> **副次的な docs 影響**: 子 Issue `local-pc5090-9` で skill SKILL.md の暫定運用記述削除を行う際、本 Issue で実装した「自動注入経路」を前提として doc を書き換える（GitHub / GitLab 両 provider で `pr_id` / `pr_ref` が prompt に注入される、と記述する）。本 Issue では skill 修正を行わないため、`local-pc5090-9` 側で「GitLab は注入される / GitHub は注入されない」状態を doc に反映する責務を負う。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| EPIC `local-pc5090-4` 本文 § 確定事項 #7 | 本 EPIC Issue 本文 | 「MR の `resolved` 状態 / approval state 等の GitLab 固有情報は provider 内部で保持、外向きには GitHub 互換 shape を返す。skill 側には GitHub/GitLab 分岐を入れない」を本 Issue の interface 設計の根拠とする |
| `kaji-pr-mr-bridge.md` § 設計原則 | `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md` (本 worktree 内) | 「skill 側 contract は GitHub 互換 subset を正本」「GitLab 固有差分は provider 内部で吸収」「skill 側に GitHub/GitLab 分岐を入れない」を `pr_ref` の `gl:<iid>` 命名規約の根拠とする |
| 既存 `IssueProvider` Protocol | `kaji_harness/providers/base.py:16-88` | `resolve_issue_context` の interface を `resolve_pr_context` のテンプレートとする（戻り値 DTO + 単一 method） |
| 既存 `GitHubProvider.resolve_issue_context` | `kaji_harness/providers/github.py:281-304` | provider が IssueContext を組み立てる責務範囲（`issue_id` / `issue_ref` / `branch_name` 等の確定）を `PRContext` にも踏襲する |
| 既存 `GitLabProvider.resolve_mr_iid_from_branch` | `kaji_harness/providers/gitlab.py:404-437` | branch → IID 逆引きの実装。本 Issue では再利用する（subprocess hit を 2 度行わない）。複数該当 / 該当なしのエラーメッセージ仕様を引用する |
| 既存 `prompt.py` build_prompt | `kaji_harness/prompt.py:13-89` | `IssueContext` から variables を組み立てる既存パターン。`PRContext` を同じ仕組みで追加する |
| 既存 `runner.py` build_prompt 呼び出し | `kaji_harness/runner.py:277-283` | main loop 内で step 単位に build_prompt を呼ぶ既存構造。本 Issue で `pr_context` 解決を直前に挿入する |
| 暫定運用記述（差し替え対象） | `.claude/skills/pr-fix/SKILL.md:44`、`pr-verify/SKILL.md:49`、`i-pr/SKILL.md:53` | 「`pr_id` はハーネス経由では現時点ではプロンプトに自動注入されない」を本 Issue 完了で「自動注入される」状態に変える根拠 |
| bucket Phase 4 申し送り | `.kaji/issues/local-pc5090-1-forge-bucket-forge-pr-context-gitlabprov/issue.md` § Phase 4 申し送り | 「`<Provider>.resolve_pr_context(branch_name)` 実装」「`pr_id` / `pr_ref` の prompt.py 自動注入」を本 Issue が GitLab 側で先取り実装する根拠 |
