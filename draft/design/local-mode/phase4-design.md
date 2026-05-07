---
status: draft
phase: 4
parent: design.md
created: 2026-05-07
revised: 2026-05-07 (review v1 + v2)
predecessor: phase3e-design.md
---

# [設計] kaji local mode — Phase 4 (PR provider 整理)

## レビュー反映ログ

### 2026-05-07 review v1（Changes Requested）

| # | 区分 | 指摘 | 対応 |
|---|------|------|------|
| MF1 | Must Fix | Phase 3-e merge 前提が「前提」記述だけで受け入れ条件に無い | 受け入れ条件 § 機械検証 に preflight 3 項目を追加。実装順序に commit 0 を追加 |
| MF2 | Must Fix | Skill Step 0 ガードが `[provider_type]` 未注入時 ABORT で GitHub 手動実行を破壊 | § 方針決定 2 を全面書換。`kaji config provider-type` (read-only CLI) を新設し Skill から provider 解決可能に。判断済み論点 #11 に経緯を記録 |
| MF3 | Must Fix | `cmd_run` の `config.provider.type` 直接参照は mypy strict で落ちる | `actual_provider_type(config)` narrowing helper を `kaji_harness/providers/__init__.py` に追加。判断済み論点 #12 に経緯を記録 |
| SF1 | Should Fix | `kaji pr create` の出力からの `pr_id` 抽出方法が暗黙的 | § 方針決定 4 に i-pr / pr-fix / pr-verify それぞれの `pr_id` / `pr_ref` / `pr_url` 確定手順を bash で明示。判断済み論点 #14 に経緯を記録 |
| SF2 | Should Fix | `requires_provider: any` default で custom workflow が無防備 | § 方針決定 5 の設計判断に「保護対象範囲」サブセクションを追加。docs migration 推奨も影響ドキュメント節に明記。判断済み論点 #13 に経緯を記録 |
| SF3 | Should Fix | autouse fixture は build_prompt 直接呼び出しを救えない | § 方針決定 6 の「既存テストの影響」セクションを書換。autouse fixture 楽観論を撤回し、`make_issue_context` factory + 個別呼び出し更新の手順を明示 |

`make check` / `make test-large-local` は本レビュー反映後の実装段階で
緑であることを確認する（設計書段階では code 変更なし）。

### 2026-05-07 review v2（OK + 1 nit）

| # | 区分 | 指摘 | 対応 |
|---|------|------|------|
| N1 | nit | `cmd_run` 例示の `provider = get_provider(config)` は副作用のみ目的のため未使用変数になる | 例示コードを `get_provider(config)`（戻り値を捨てる形）に修正。新セクション § 実装上の注意 を追加し、判定基準・関数別の整理表・Skill bash の `set -e` 対策を明文化（実装時の参照点を中央集約） |

Phase 3-e で `provider` の解決と Issue 系の context 注入は fail-fast 化された。
Phase 4 は **PR (forge 機能) を bare provider 配下で誤起動させない**ための
3 層ガード（CLI / Skill / Workflow）と、Phase 3-e から申し送られた
`build_prompt` の Optional 解除を一括で扱う。

> **前提**: 本設計は `feat/local-phase3e` が `main` にマージされた状態を
> baseline とする。マージ前に着手する場合は、Phase 3-e の rebase / merge を
> 先に完了させる。

## 概要

Phase 4 で確定する変更は次の 5 項目。すべて「forge 専用機能 (`kaji pr` /
`pr-*` Skill / `i-pr` Skill) を `provider.type='local'` 配下で誤起動させない」
という単一の関心事に収束する。多重ガードにより、CLI 直叩き / Skill 手動実行
/ workflow 経由のいずれの起動経路でも fail-fast する。

| # | 変更 | 層 |
|---|------|----|
| 1 | `kaji pr ...` を `provider.type='local'` 配下で exit 2 + 代替手順案内 | CLI |
| 2 | `kaji config provider-type` (read-only) を新設し、Skill から provider 解決可能に | CLI |
| 3 | `pr-fix` / `pr-verify` / `i-pr` Skill 冒頭に `provider_type` ガードを追加（手動実行時は `kaji config provider-type` で解決） | Skill |
| 4 | Skill 内 `[pr-number]` → `[pr_id]` / `[pr_ref]` placeholder 統一 + 静的検証 | Skill |
| 5 | Workflow YAML に `requires_provider: github | local | any` を追加し `kaji run` 起動時に整合検証 | Workflow |
| 6 | `build_prompt(..., issue_context: IssueContext)` の required 化 + 内部 `if issue_context is not None:` 分岐を構造的削除 | runner |

`pr_id` / `pr_ref` の **prompt.py 経由の context 注入は本 Phase ではやらない**
（理由は § 方針決定 4 を参照）。手動実行 fallback ルール（GitHub 数値 ID なら
`#<pr_id>`、それ以外は bare）を Skill 入力表に明文化するに留め、provider 経由
の自動解決は Phase 5（`kaji sync` と PR 検索を含む forge 通信のまとまり）で扱う。

## 背景・目的

### Phase 3-e 完了時点の状態

- `get_provider(config)` は `provider is None` で `ValueError` を raise し、
  `IssueProvider` を必ず返す（Optional 解除済）
- `_load_config_for_dispatch()` は `ConfigNotFoundError` を含めて propagate し、
  `_handle_issue` / `_handle_pr` で exit 2 に正規化される
- `cmd_run` 冒頭で `get_provider(config)` を呼び、`ValueError` は exit 2 に
  正規化される（`IssueContextResolutionError` 経由 exit 3 に落ちない）
- `WorkflowRunner._resolve_issue_context()` は `IssueContext` を必ず返す
  （`IssueContext | None` 解除済、Phase 3-e commit 4 で narrowing 完了）
- ただし `prompt.build_prompt(..., issue_context: IssueContext | None = None)` は
  Phase 3-e § 2 で縮小判断され、Optional のまま残置された（Phase 4 申し送り）

### Phase 4 が解く問題

| 問題 | 現状 | Phase 4 後 |
|------|------|-----------|
| `provider.type='local'` で `kaji pr create` を打つと、現在の `_handle_pr` は `repo_override = None` のまま `gh pr create` に passthrough する。`gh` 認証があれば current dir の `git remote` を勝手に推論して GitHub に PR を作りに行く可能性がある（`feat/local-phase3e/cli_main.py:601-643`） | passthrough（事故 PR の余地） | exit 2、代替手順を stderr に出力 |
| `/pr-fix` / `/pr-verify` を手動で `provider.type='local'` 配下で呼ぶと、Skill 内部で `kaji pr list --search` 等が CLI 層エラーで止まるが、エラー文面が「forge 専用機能だから」を伝えない | CLI エラー（理由不明瞭） | Skill 冒頭で `provider_type` を見て早期 ABORT、代替案提示 |
| `i-pr` Skill が `provider.type='local'` で起動すると `kaji pr create` で停止するが、workflow YAML（`feature-development-local.yaml`）では `i-pr` を使わない別フローに分けてある。**しかし user が誤って `feature-development.yaml` を `provider.type='local'` 配下で起動した場合、`i-pr` step まで進んでから停止する**（途中まで実行コストを消費する） | step 9 で停止 | `kaji run` 起動時に workflow ↔ provider 整合検証で fail-fast |
| `[pr-number]` という hyphen 形式 placeholder が Skill 内に残存（Phase 3-d で Group A/B/C 統一の際に Group C で見送られたもの。phase3d-design.md § 1）。`pr_id` / `pr_ref` への命名整合が未完了 | hyphen 形式が残存 | underscore 形式に統一 + Phase 3-d 静的検証に追加 |
| `prompt.py` が `IssueContext is None` 経路を持つため、Phase 3-e で削除された fallback の残骸 if 分岐が残る。`runner._resolve_issue_context()` は `IssueContext` を必ず返すのに、`build_prompt` 側だけ Optional のまま | if 分岐残存 | Optional 解除 + 互換分岐削除 |

### Phase 4 が解かない問題（後続）

- `pr_id` / `pr_ref` の prompt.py 経由の自動注入。現行 workflow に
  `pr-fix` / `pr-verify` が含まれないため context 注入経路を作っても発火しない。
  `kaji sync from-github` 経由で PR メタデータを cache する Phase 5 と一体で
  整理する方が、`GitHubProvider` の forge 通信箇所が 1 箇所に集まる
- `kaji pr list --search` の bare provider エラー化に伴う **ローカル grep**
  （`provider=local` 配下で「Issue 番号から関連 PR を解決する」概念自体が無い
  ため代替なし）。Skill 側で `provider=local` の場合は PR 概念を skip する設計
  で十分（後述 § 方針決定 2）
- `legacy WARN fallback` の完全廃止。Phase 3-e で `_PROVIDER_FALLBACK_WARNED`
  は削除済。本 Phase でこれ以上触らない

## スコープ

### in-scope

- `kaji_harness/cli_main.py` — `_handle_pr` に `provider.type=='local'` 分岐を
  追加し、`_PR_BUILTIN_SUBCOMMANDS` も含めて exit 2 + 代替手順 stderr 出力
- `kaji_harness/cli_main.py:cmd_run` — `_validate_workflow_provider_match()` を
  `get_provider(config)` 直後に追加し、workflow YAML の `requires_provider` と
  `config.provider.type` の不整合を exit 2 で fail-fast
- `kaji_harness/workflow.py` — `Workflow` dataclass に
  `requires_provider: Literal["github", "local", "any"] = "any"` を追加し、
  `_parse_workflow` / `validate_workflow` で文法検証
- `kaji_harness/models.py:Workflow` — `requires_provider` フィールドを追加
- `kaji_harness/prompt.py:build_prompt` — `issue_context: IssueContext` の
  required 化 + 内部 `if issue_context is not None:` 分岐の構造的削除
- `kaji_harness/runner.py:RunIssueContext` — `issue_context` の Optional は
  Phase 3-e で既に解除済（再確認のみ）
- `.claude/skills/pr-fix/SKILL.md` / `pr-verify/SKILL.md` — 冒頭に
  `provider_type` ガード step を追加し、`provider_type='local'` で明示 ABORT。
  `[pr-number]` を `[pr_id]` / `[pr_ref]` に置換
- `.claude/skills/i-pr/SKILL.md` — 同様の `provider_type` ガードと placeholder
  置換（i-pr は workflow からも呼ばれるため、Skill ガードに加えて workflow
  整合検証で重ねて止める）
- `.kaji/wf/feature-development.yaml` / `feature-development-light.yaml` /
  `implement-to-pr.yaml` — `requires_provider: github` を追加
- `.kaji/wf/feature-development-local.yaml` — `requires_provider: local` を追加
- `.kaji/wf/design-only.yaml` — `requires_provider: any`（明示）を追加
- `tests/test_phase3d_skills.py` — `FORBIDDEN_PATTERNS` に `[pr-number]` /
  `<pr-number>` を追加
- `tests/test_phase4_*.py` — 新規追加（後述 § テスト戦略）

### out-of-scope

- `pr_id` / `pr_ref` の `prompt.py` 経由の context 注入（Phase 5）
- `GitHubProvider.resolve_pr_context(branch_name)` の実装（Phase 5）
- `kaji pr create` / `kaji pr merge` 等の forge 経路 logic 変更（現行
  passthrough を維持）
- `pr-fix` / `pr-verify` を `provider=local` 配下で **代替動作させる**ような
  ロジック（design.md L972-996 で「PR 概念は無い」と明示済。Skill ガードで
  ABORT させる方針を維持）
- workflow YAML スキーマの全面リファクタ（`requires_provider` 1 フィールド
  追加のみ。それ以外のスキーマには触らない）
- 既存 `RunIssueContext` の API 変更（Phase 3-e で確定済の DTO を維持）

## 方針決定

### 1. `kaji pr` の bare provider エラー化（CLI 層）

`_handle_pr(raw_args)` を以下のように拡張する:

```python
def _handle_pr(raw_args: list[str]) -> int:
    try:
        config = _load_config_for_dispatch()
    except (ConfigLoadError, ConfigNotFoundError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    try:
        provider = get_provider(config)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT

    if isinstance(provider, LocalProvider):
        sys.stderr.write(_PR_BARE_PROVIDER_ERROR)
        return EXIT_INVALID_INPUT

    # 既存の forge 経路（_PR_BUILTIN_SUBCOMMANDS / passthrough）はそのまま
    ...
```

`_PR_BARE_PROVIDER_ERROR` の文面（design.md L1194-1197 を踏襲）:

```
Error: 'kaji pr' is a forge-only command and cannot run under provider.type='local'.
Pull request concept does not exist in local mode (bare provider). Use git/issue
operations directly:

  - Code review:        /issue-review-code, /issue-fix-code, /issue-verify-code
  - Merge + close:      /issue-close (executes 'git merge --no-ff' + frontmatter update)
  - Branch listing:     git branch --list 'feat/local-*'

To switch back to GitHub mode (e.g. after the outage), edit
.kaji/config.local.toml and set [provider] type = "github" (or remove the
overlay so the tracked .kaji/config.toml takes effect).
```

**設計判断**:

- `provider.type=='github'` 配下では現行の `repo_override` 注入と
  passthrough を維持。本 Phase は「bare に来た PR コマンドを止める」だけ
- `_PR_BUILTIN_SUBCOMMANDS`（`review-comments` / `reviews` /
  `reply-to-comment`）も同じガードでまとめて止める。これらは GitHub API 直叩き
  であり、cache 経由でも代替できないため
- exit code は `EXIT_INVALID_INPUT (= 2)` に統一。`EXIT_RUNTIME_ERROR (= 3)`
  にはしない（user の **入力意図ミス**であり、ランタイム障害ではない）
- `--help` を expected として通すかは検討対象だが、本 Phase は「`kaji pr`
  の `--help` も bare では止める」とする（forge 機能の help を見せると
  「使えるかも」という誤解を生む。`kaji pr` の help は GitHub mode に
  切り替えてから読めば良い）

### 2. `kaji config provider-type` (read-only CLI 新設)

Skill が手動実行時にも provider type を解決できるよう、副作用のない
read-only サブコマンドを追加する。

```
kaji config provider-type
```

**仕様**:

- 出力（stdout、改行 1 つ）: `github` / `local` のいずれか
- exit code: 0（成功） / 2（config 不在 or 不正）
- 副作用なし。tracked `.kaji/config.toml` + overlay `.kaji/config.local.toml`
  を read-only で merge し、`config.provider.type` を返すだけ
- `KajiConfig.discover(start_dir=Path.cwd())` を経由するため、`_handle_pr` /
  `_handle_issue` / `cmd_run` と同じ config resolution path を共有する
  （別経路の bug 混入を防ぐ）

**Skill からの呼び出し例**:

```bash
PROVIDER_TYPE=$(kaji config provider-type 2>/dev/null || echo "")
case "$PROVIDER_TYPE" in
    github) : ;; # 続行
    local)  echo "ABORT: provider=local"; exit 1 ;;
    *)      echo "ABORT: provider unresolved"; exit 1 ;;
esac
```

**設計判断**:

- 既存代替案を棄却した理由:
  - **TOML 直 grep** — `.kaji/config.toml` の `[provider] type = "..."` を
    grep で取るのは overlay (`config.local.toml`) を考慮しないため誤動作
  - **python one-liner** — `python -c "from kaji_harness.config import ..."`
    は venv activation 状態に依存し脆い
  - **既存コマンドへの flag 追加** — `kaji local init --check` 等は
    semantics を変えるため避ける
- `kaji config` という新サブコマンドグループを作る理由: 将来 `kaji config
  doctor` / `kaji config show` 等を追加する余地を残す（design.md L1505 の
  オープン論点）
- read-only 保証: `KajiConfig.discover()` は副作用なし。CLI 実装は I/O
  しないため race condition なし
- Phase 3-e で fail-fast 化された `provider` 必須化と整合: `[provider]` 不在
  なら `KajiConfig.discover()` が成功し、その後 `config.provider is None` で
  exit 2 + 「`[provider]` セクションが必要」を stderr に出す（Phase 3-e の
  メッセージを再利用）

### 3. Skill 冒頭の provider_type ガード

`pr-fix` / `pr-verify` / `i-pr` の Skill markdown 冒頭（「## 入力」直後の
「## 前提条件」もしくは「### Step 0: provider check」）に以下を挿入する:

```markdown
### Step 0: provider check

本 Skill は forge provider 専用。`provider_type` を解決し、`github` 以外
なら ABORT する。

**provider_type の解決順序**:

1. ハーネス経由で `[provider_type]` が注入されている → そのまま使う
2. 未注入（手動実行）→ `kaji config provider-type` を呼んで解決する:

   ```bash
   PROVIDER_TYPE="${provider_type:-$(kaji config provider-type 2>/dev/null)}"
   ```

**判定**:

- `github` → 通常通り Step 1 以降へ進む
- `local` → 以下のメッセージを stdout に出力し、verdict を **ABORT**:

  ```
  pr-fix is a forge-only skill and cannot run under provider.type='local'.
  Pull request concept does not exist in local mode. Use the design / code
  review skills directly:
    /issue-review-code, /issue-fix-code, /issue-verify-code
  ```

- 解決失敗（`kaji config provider-type` が exit 2）→ 「`.kaji/config.toml`
  の `[provider]` を確認してください」とガイドして verdict ABORT
```

**設計判断**:

- 3 層ガード（CLI / Skill / Workflow）の **Skill 層** として位置づける。
  CLI 層で止まらない経路（Skill が直接 git / file 操作を行う場合）も
  fail-safe にする
- 手動実行（`/pr-fix <issue_id>` 直接起動）でも GitHub mode を破壊しない
  よう、`kaji config provider-type` で provider を解決する経路を持つ。
  「未注入 = ABORT」の素朴な実装は GitHub mode の手動実行も止めてしまう
  ため避ける（レビュー MF2 反映）
- `i-pr` は workflow にも含まれるため Workflow 整合検証（§ 5）で先に
  止まるはずだが、user が `provider=local` 配下で `/i-pr` を直接呼ぶ
  ケースに備えて Skill 層でも止める
- ABORT verdict を返す理由: workflow 経由で呼ばれた場合、`on: ABORT: end`
  で workflow を終わらせるため。RETRY や PASS では cycle を回してしまう

### 4. `[pr-number]` → `[pr_id]` / `[pr_ref]` placeholder 統一

Phase 3-d Group C で見送られた hyphen 形式を underscore 形式に統一する。

| 出現箇所 | 現状 | 変更後 |
|----------|------|--------|
| `pr-fix/SKILL.md:66-68, 116, 127, 159` | `[pr-number]` | コマンド引数は `[pr_id]`、表示は `[pr_ref]`（`#<pr_id>`） |
| `pr-verify/SKILL.md:80-82, 144, 176, 208` | `[pr-number]` | 同上 |
| `i-pr/SKILL.md:133, 147` | `[pr-number]` | 同上 |

**`pr_id` / `pr_ref` / `pr_url` の確定方法**（i-pr / pr-fix / pr-verify
の各 Skill に明記する。レビュー SF1 反映）:

`i-pr` Skill の Step 4（PR 作成）と Step 5（Issue 本文への PR 番号追記）の
間に PR 識別子を確定する手順を明示する:

```bash
# Step 4 実行
PR_URL=$(cd [worktree_dir] && kaji pr create --base main --title "..." --body "..." 2>&1 | tail -1)
# kaji pr create は最後の行に PR URL を返す:
# https://github.com/<owner>/<repo>/pull/<N>

# pr_id / pr_ref / pr_url を確定
pr_url="$PR_URL"
pr_id=$(echo "$pr_url" | sed 's|.*/||')      # "42"
pr_ref="#${pr_id}"                            # "#42"

# Step 5 へ pr_id / pr_ref / pr_url を持ち越す
```

`pr-fix` / `pr-verify` Skill の Step 1（PR 特定）では既存の
`kaji pr list --search "[issue_id]" --json number,title,headRefName --jq '.'`
の出力から `pr_id = .[0].number` を抽出する手順を明示する:

```bash
PR_JSON=$(kaji pr list --search "[issue_id]" --json number,title,headRefName --jq '.[0]')
pr_id=$(echo "$PR_JSON" | jq -r '.number')
pr_ref="#${pr_id}"
```

**手動実行 fallback ルール**（Skill 入力表に追加する文言、現行の `issue_id`
/ `issue_ref` 解決ルールに倣う）:

> `pr_id` はハーネス経由では Phase 4 時点ではプロンプトに自動注入されない
> （Phase 5 で GitHubProvider が解決して prompt 注入する予定）。
> Phase 4 時点では Skill 内で `kaji pr list --search` 等で取得して
> `[pr_id]` の値を確定する。`pr_ref` は `pr_id` から導出する: GitHub
> 数値 ID なら `#<pr_id>`、それ以外は bare ID。

**`tests/test_phase3d_skills.py:FORBIDDEN_PATTERNS` への追加**:

```python
FORBIDDEN_PATTERNS = [
    # 既存（Phase 3-d）
    r"\[worktree-absolute-path\]", r"\[branch-name\]",
    r"\[design-path\]", r"\[issue-input\]",
    r"<worktree-absolute-path>", r"<branch-name>",
    r"<design-path>", r"<issue-input>",
    # Phase 4 で追加
    r"\[pr-number\]",
    r"<pr-number>",
]
```

**設計判断**:

- `pr_id` / `pr_ref` を **prompt 注入変数として確立する**のではなく、
  「Skill 内 placeholder の命名規約」として確立する。Phase 5 で prompt 経由
  の自動注入が入った時に同じ identifier を再利用できる
- `[pr-url]` は表示用のため Phase 4 では触らない（forge 専用情報、
  bare では存在しないため）

### 5. Workflow YAML ↔ provider type の整合検証

`.kaji/wf/*.yaml` のトップレベルに `requires_provider` を追加する。

```yaml
name: feature-development
description: |
  ...
execution_policy: auto
requires_provider: github   # ← 新規追加。default は "any"
cycles: ...
steps: ...
```

**スキーマ**:

```python
# kaji_harness/models.py
@dataclass
class Workflow:
    name: str
    description: str
    execution_policy: str
    steps: list[Step]
    cycles: list[CycleDefinition] = field(default_factory=list)
    default_timeout: int | None = None
    workdir: str | None = None
    requires_provider: Literal["github", "local", "any"] = "any"  # ← 新規
```

**parser**:

```python
# kaji_harness/workflow.py:_parse_workflow
raw_requires_provider = data.get("requires_provider", "any")
if not isinstance(raw_requires_provider, str):
    raise WorkflowValidationError(
        f"'requires_provider' must be a string, got {type(raw_requires_provider).__name__}"
    )
if raw_requires_provider not in {"github", "local", "any"}:
    raise WorkflowValidationError(
        f"'requires_provider' must be one of 'github', 'local', 'any', "
        f"got {raw_requires_provider!r}"
    )
```

**`cmd_run` での整合検証**（narrowing helper を経由する。レビュー MF3 反映）:

```python
# kaji_harness/providers/__init__.py に追加
def actual_provider_type(config: KajiConfig) -> str:
    """`get_provider(config)` が成功した後に provider.type を取り出す helper。

    Phase 3-e 以降、`get_provider(config)` が成功すれば `config.provider`
    は必ず非 None。本 helper は型 narrowing と「provider 確定後に呼ぶ」契約を
    呼出側に強制する役割を持つ。
    """
    if config.provider is None:
        # 防御的: get_provider() を経由せず本 helper を呼んだ場合のガード
        raise ValueError(
            "actual_provider_type() called before get_provider(); "
            "config.provider is None"
        )
    return config.provider.type
```

```python
# kaji_harness/cli_main.py:cmd_run
# (existing) workflow load + config load の後
try:
    # `provider` インスタンス自体は cmd_run で使わない。fail-fast 検証
    # （ValueError raise）の副作用のみが目的のため、戻り値は捨てる。
    # `provider = get_provider(config)` と書くと ruff F841 / mypy unused
    # variable で落ちる（実装注意 § 参照）。
    get_provider(config)
except ValueError as exc:
    print(f"Error: {exc}", file=sys.stderr)
    return EXIT_INVALID_INPUT

if workflow.requires_provider != "any":
    actual = actual_provider_type(config)  # narrowing helper
    if workflow.requires_provider != actual:
        print(
            f"Error: workflow '{workflow.name}' requires provider.type="
            f"'{workflow.requires_provider}' but current config has "
            f"provider.type='{actual}'.\n"
            f"  - To run this workflow, switch provider in .kaji/config.local.toml.\n"
            f"  - To use the current provider, choose a workflow with "
            f"requires_provider='{actual}' or 'any'.",
            file=sys.stderr,
        )
        return EXIT_INVALID_INPUT
```

**narrowing 設計の根拠**:

- mypy strict では `config.provider` が `ProviderConfig | None` 型のため、
  `get_provider()` 成功直後でも `config.provider.type` の直接参照は型エラー
  になる
- Phase 3-e 実装は `assert config.provider is not None  # for type checker`
  を runner.py / cli_main.py で使っているが、本 Phase で増える参照箇所
  （`cmd_run` / `_handle_pr` / `_validate_workflow_provider_match`）に
  毎回 assert を散らすと **「provider 確定後にのみ呼べる」契約**が読み取り
  にくくなる
- `actual_provider_type(config)` helper に集約することで、呼出側が
  「`get_provider()` を先に呼ぶ」契約を関数呼び出しの並びとして表現できる。
  defensive な ValueError も中央集約される

**workflow YAML への適用**:

| ファイル | requires_provider | 理由 |
|----------|-------------------|------|
| `feature-development.yaml` | `github` | 末尾 step が `i-pr`（forge 必須） |
| `feature-development-light.yaml` | `github` | 末尾 step が `i-pr` |
| `feature-development-local.yaml` | `local` | 末尾 step が `issue-close`（local merge 前提） |
| `implement-to-pr.yaml` | `github` | 末尾 step が `i-pr` |
| `design-only.yaml` | `any` | PR / merge を含まないため、どの provider でも動く |

**設計判断**:

- `requires_provider` は workflow の **「forge 機能を必要とするか」** を
  宣言するメタデータ。Skill レベルでなく workflow レベルで宣言する理由:
  - Skill は workflow 構成によって forge を使うとも限らない
    （`/issue-design` 単独なら provider 中立）
  - workflow は **step 列の組み合わせ** として forge 依存性を持つため、
    workflow 単位で宣言する方が semantics が一致する
- `any` を default にする理由: 既存 workflow（`design-only.yaml` 等）の
  破壊を避ける。`requires_provider` フィールド自体を optional にし、
  未指定なら `any` 扱い
- `any` default の **保護対象範囲**（レビュー SF2 反映）:
  - **builtin workflow**（`.kaji/wf/*.yaml`）は本 Phase で全 5 ファイルに
    `requires_provider` を明示する。これにより builtin workflow について
    は不整合 fail-fast が確実に発火する
  - **user の custom workflow** は default `any` のため fail-fast 対象外。
    user は自分の workflow に `requires_provider: github` 等を追加する
    ことで保護を opt-in できる
  - migration 推奨: `docs/dev/workflow-authoring.md` に「forge 機能を含む
    custom workflow には `requires_provider: github` を追加してください」
    と明記
  - 強制 default を `any` 以外（例: `github`）にすると、user の既存
    workflow が突然 fail-fast し始めるため不採用
- `kaji validate <workflow>` でも整合検証を行う **べきか**:
  - `kaji validate` は config を読み込まないため、workflow 単独の文法検証
    のみに留める。`requires_provider` の **値の妥当性**（enum）は検証する
    が、`config.provider.type` との突合は `kaji run` でのみ行う
- 不整合検出時の exit code: `EXIT_INVALID_INPUT (= 2)`。user の入力意図と
  config の不整合であり、定義エラーではない

### 6. `build_prompt` の `IssueContext` required 化

Phase 3-e § 2 で縮小判断され Phase 4 申し送りされた項目。

**変更内容**:

```python
# kaji_harness/prompt.py（変更後）
def build_prompt(
    step: Step,
    issue: str,
    state: SessionState,
    workflow: Workflow,
    issue_context: IssueContext,  # required 化
) -> str:
    """ステップ実行用のプロンプトを構築する。

    Phase 4 で `issue_context` を required 化。Phase 3-e で provider が
    必ず解決される設計に変わったため、`build_prompt` 呼び出し時点で
    `IssueContext` は確定している（runner.py:_resolve_run_issue_context）。
    """
    issue_id = issue_context.issue_id
    issue_ref = issue_context.issue_ref
    variables: dict[str, object] = {
        "issue_id": issue_id,
        "issue_ref": issue_ref,
        "step_id": step.id,
        "issue_input": issue_context.issue_input,
        "branch_prefix": issue_context.branch_prefix,
        "branch_name": issue_context.branch_name,
        "worktree_dir": issue_context.worktree_dir,
        "design_path": issue_context.design_path,
        "provider_type": issue_context.provider_type,
        "default_branch": issue_context.default_branch,
    }
    # ... 以下、cycle / verdict 注入は変更なし
```

**呼び出し側の更新**:

- `runner.py:run()` の `build_prompt(...)` 呼び出しは **既に
  `issue_context=run_ctx.issue_context` を渡している**。`run_ctx.issue_context`
  は Phase 3-e で `IssueContext`（Optional 解除）になっているため、追加変更
  不要
- `state.py:_format_issue_ref` への import が不要になる（`build_prompt` 内
  で `issue` 文字列から ref を導出する fallback 経路が消えるため）

**既存テストの影響**（レビュー SF3 反映）:

`build_prompt` を直接呼んでいるテスト関数は **個別に `issue_context=...`
引数を追加する必要がある**。autouse fixture は Phase 3-e で
`WorkflowRunner` 経由テストの Issue dir pre-create を担うものであり、
`build_prompt` の引数追加は救えない（autouse は関数引数の挿入はできない）。

具体的な作業:

1. `tests/conftest.py` に `make_issue_context(...)` factory を追加
   （local / github 両 provider 用）。signature 例:

   ```python
   def make_issue_context(
       *,
       provider_type: str = "github",
       issue_id: str = "153",
       slug: str = "test",
       branch_prefix: str = "feat",
       repo_root: Path | None = None,
       default_branch: str = "main",
   ) -> IssueContext: ...
   ```

2. `build_prompt(...)` を呼んでいるテストを grep で列挙し、各呼び出しに
   `issue_context=make_issue_context(...)` を追加。grep 想定対象:
   `tests/test_prompt*.py` / `tests/test_workflow_execution.py` /
   `tests/test_runner_*.py` 等
3. fixture / parametrize で provider 別に build_prompt 出力を検証している
   テストは `make_issue_context(provider_type=...)` で揃える

**設計判断**:

- `IssueContext | None` の互換分岐削除は **コード単純化**（Phase 3-e §
  2 の継続）。`prompt.py` 内に `issue_context is not None:` ブロックが 2 箇所
  残存しているため、片付けることで cognitive load を下げる
- 既存 fixture 一括更新は Phase 3-e で確立した `autouse` パターンを再利用。
  `_AUTOCREATE_OPT_OUT_FILES` リストに新規ファイルを追加する形でカスタマイズ
  可能にする
- 別案として「`IssueContext` factory を `prompt.py` 内に持ち、`issue` 文字列
  だけ渡せば組み立てる」も検討したが、provider への依存を `prompt.py` に
  作ることになるため棄却（責務分離が崩れる）

## インターフェース

### CLI surface

| 経路 | 変更前 | 変更後 |
|------|--------|--------|
| `kaji pr <any>` (provider=local) | `gh pr` passthrough（事故 PR の余地） | exit 2 + 代替手順 stderr |
| `kaji pr <any>` (provider=github) | 現行通り passthrough + `--repo` 強制注入 | 変更なし |
| `kaji pr review-comments <id>` (provider=local) | builtin handler 経由で `gh api` 呼び出し | exit 2 + 代替手順 stderr |
| `kaji run <workflow> <issue>` (workflow.requires_provider != config.provider.type) | workflow 終盤で stop | 起動直後に exit 2 + 切替手順 stderr |
| `kaji validate <workflow>` | `requires_provider` 未認識 | enum 値（`github` / `local` / `any`）として検証 |

### 内部 API の signature 変化

```python
# kaji_harness/prompt.py
- def build_prompt(step, issue, state, workflow, issue_context: IssueContext | None = None) -> str
+ def build_prompt(step, issue, state, workflow, issue_context: IssueContext) -> str

# kaji_harness/models.py:Workflow
+ requires_provider: Literal["github", "local", "any"] = "any"

# kaji_harness/providers/__init__.py
+ def actual_provider_type(config: KajiConfig) -> str  # narrowing helper

# kaji_harness/cli_main.py
+ _PR_BARE_PROVIDER_ERROR: str = "..."  # module-level constant
+ def _validate_workflow_provider_match(workflow: Workflow, config: KajiConfig) -> int  # 0 if ok, EXIT_INVALID_INPUT otherwise
+ def _register_config(subparsers): ...  # `kaji config` サブコマンドグループ
+ def cmd_config_provider_type(args): ...  # `kaji config provider-type` ハンドラ
```

### Workflow YAML スキーマの追加

```yaml
# .kaji/wf/*.yaml
name: <string>                          # 既存
description: <string>                   # 既存
execution_policy: auto | sandbox | interactive  # 既存
requires_provider: github | local | any  # ← 追加（optional、default "any"）
default_timeout: <int>                  # 既存
workdir: <abs path>                     # 既存
cycles: ...                             # 既存
steps: ...                              # 既存
```

### Skill markdown の追加 step

```markdown
### Step 0: provider check (Phase 4 で追加)

`[provider_type]` が `github` 以外の場合は本 Skill を実行せず ABORT する。
（pr-fix / pr-verify / i-pr の冒頭に挿入）
```

## 実装範囲

### 変更対象ファイル

| ファイル | 変更概要 | サイズ目安 |
|----------|----------|-----------|
| `kaji_harness/cli_main.py` | `_PR_BARE_PROVIDER_ERROR` 追加、`_handle_pr` に local 分岐、`cmd_run` に `_validate_workflow_provider_match` 呼び出し追加、`kaji config provider-type` サブコマンド追加 | +80 行 |
| `kaji_harness/providers/__init__.py` | `actual_provider_type()` narrowing helper 追加 | +15 行 |
| `kaji_harness/models.py` | `Workflow.requires_provider` 追加 | +1 行 |
| `kaji_harness/workflow.py` | `_parse_workflow` で `requires_provider` 文法検証、`validate_workflow` で enum 検証 | +20 行 |
| `kaji_harness/prompt.py` | `issue_context` required 化、内部 if 分岐削除、`_format_issue_ref` import 削除 | -15 行（純減） |
| `kaji_harness/runner.py` | コメント更新のみ（Phase 3-e で narrowing 完了済） | 0 行 |
| `.claude/skills/pr-fix/SKILL.md` | Step 0 追加、`[pr-number]` → `[pr_id]` / `[pr_ref]` 置換、入力表に `provider_type` 追加 | +20 / -10 行 |
| `.claude/skills/pr-verify/SKILL.md` | 同上 | +20 / -10 行 |
| `.claude/skills/i-pr/SKILL.md` | 同上（i-pr は workflow からも呼ばれるため、Skill ガードと workflow 整合検証で重ねて止める） | +20 / -10 行 |
| `.kaji/wf/feature-development.yaml` | `requires_provider: github` 追加 | +1 行 |
| `.kaji/wf/feature-development-light.yaml` | `requires_provider: github` 追加 | +1 行 |
| `.kaji/wf/feature-development-local.yaml` | `requires_provider: local` 追加 | +1 行 |
| `.kaji/wf/implement-to-pr.yaml` | `requires_provider: github` 追加 | +1 行 |
| `.kaji/wf/design-only.yaml` | `requires_provider: any` 追加（明示） | +1 行 |
| `tests/test_phase3d_skills.py` | `FORBIDDEN_PATTERNS` に `[pr-number]` / `<pr-number>` 追加 | +2 行 |
| `tests/test_phase4_pr_bare_provider.py` | 新規（後述） | +120 行 |
| `tests/test_phase4_workflow_provider_match.py` | 新規（後述） | +80 行 |
| `tests/test_phase4_build_prompt_required.py` | 新規（後述） | +60 行 |
| `tests/test_phase4_skill_provider_guard.py` | 新規（Medium 静的検証） | +40 行 |
| `tests/test_phase4_large_local.py` | 新規（Large-local subprocess） | +100 行 |
| `tests/conftest.py` | `make_issue_context` ヘルパ追加 | +20 行 |
| 既存 fixture の更新 | `tests/test_prompt*.py` ほか `build_prompt` を呼ぶテストで `issue_context` 引数を required 化（autouse fixture 経由でカバー可能なものは触らない） | ~10 ファイル × 数行 |

### 実装順序（commit 粒度）

**preflight (commit 0)** — `feat/local-phase3e` が `main` にマージ済みで
あることを確認する。未マージなら本 Phase 4 着手を中断し、Phase 3-e の
merge を先に完了させる（受け入れ条件 § 機械検証 参照）。

1. **commit 1** — `actual_provider_type()` narrowing helper 追加
   + `kaji config provider-type` サブコマンド追加（read-only）+ Small/Medium テスト
2. **commit 2** — `Workflow.requires_provider` 追加（model + parser + validator）
   + Small/Medium テスト
3. **commit 3** — `.kaji/wf/*.yaml` への `requires_provider` 追加
   （commit 2 の検証ロジックが先に通っていることが前提）
4. **commit 4** — `cmd_run` に `_validate_workflow_provider_match` 追加
   + Medium テスト
5. **commit 5** — `_handle_pr` の bare provider エラー化
   （`_PR_BARE_PROVIDER_ERROR` 定数追加 + 分岐）+ Small/Medium テスト
6. **commit 6** — Skill markdown 更新（pr-fix / pr-verify / i-pr）+ Step 0
   ガード追加（`kaji config provider-type` 経由で provider 解決）+
   placeholder 置換（`[pr-number]` → `[pr_id]` / `[pr_ref]`）+
   `pr_id` / `pr_ref` / `pr_url` 確定手順の明記 + 入力表更新
7. **commit 7** — `tests/test_phase3d_skills.py:FORBIDDEN_PATTERNS` 拡張
   + 新規 `test_phase4_skill_provider_guard.py`（Step 0 ガード文言の存在検証）
8. **commit 8** — `build_prompt` の `issue_context` required 化 +
   `tests/conftest.py:make_issue_context` factory 追加 + `build_prompt` 直接
   呼び出しテストの一括引数追加
9. **commit 9** — Large-local E2E（`test_phase4_large_local.py`）で
   3 層ガード（CLI / Skill / Workflow）を subprocess で確認
10. **commit 10** — CHANGELOG 更新（[Unreleased] に Phase 4 BREAKING CHANGE
    と Migration を追記）+ docs（`docs/cli-guides/local-mode.md`、
    `docs/dev/workflow-authoring.md`）更新

各 commit は `make check` 緑を維持。commit 5 で `make test-large-local`
（既存）も緑であることを確認。

## テスト戦略

`docs/dev/testing-convention.md` および `docs/reference/testing-size-guide.md`
に従う（Small / Medium / Large）。

### Small

| 対象 | 例 |
|------|------|
| `_parse_workflow` の `requires_provider` 文法検証 | `"github"` accept、`"local"` accept、`"any"` accept、`"x"` reject、int reject、未指定で `"any"` default |
| `validate_workflow` の `requires_provider` enum 検証 | 同上、enum 値以外を reject |
| `build_prompt` の required 化 | `IssueContext` 渡し有り 1 経路のみ。Optional 経路の if 分岐が削除されていることを test で確認（mypy strict + 関数 signature 検査） |
| `_PR_BARE_PROVIDER_ERROR` 文面 | 「forge-only」「local mode」「/issue-review-code」等の必須キーワードを部分一致で検証 |
| `actual_provider_type(config)` helper | provider 確定状態で正常値を返す、`config.provider is None` で `ValueError` を raise |

### Medium

| 対象 | 例 |
|------|------|
| `_handle_pr` provider=local | `CliRunner` で `kaji pr create` / `kaji pr list` / `kaji pr review-comments 1` 等 5 サブコマンドが exit 2 + stderr に代替手順を含むことを確認。`gh` への subprocess 呼び出しが発火しないことを mock で確認 |
| `_handle_pr` provider=github | 既存挙動が壊れないことを回帰確認（current dir に config を置き、mock した `gh` への引数組み立てを検証） |
| `cmd_run` workflow=feature-development.yaml + provider=local | exit 2 + stderr に「workflow ... requires provider.type='github'」を含む |
| `cmd_run` workflow=feature-development-local.yaml + provider=github | exit 2 + 対称ケース |
| `cmd_run` workflow=design-only.yaml + provider=local | 通過（`requires_provider: any`） |
| `cmd_run` workflow=design-only.yaml + provider=github | 通過 |
| Skill placeholder 静的検証 | `tests/test_phase3d_skills.py:FORBIDDEN_PATTERNS` 拡張版で `[pr-number]` が全 Skill から消えていることを確認 |
| Skill provider_type ガード文言 | pr-fix / pr-verify / i-pr の SKILL.md に `[provider_type]` への分岐句が含まれることを文字列検索で検証 |
| `tests/conftest.py:make_issue_context` | local / github 各 1 件で IssueContext を構築し、`build_prompt` に渡せることを確認 |
| `kaji config provider-type` (CliRunner) | provider=github / local の各 config で `github` / `local` を 1 行で出力、`[provider]` 不在で exit 2、`.kaji/config.toml` 不在で exit 2 |
| Skill Step 0 ガード（手動実行 fallback） | `provider_type` 環境変数 / 引数が未注入で `kaji config provider-type` 経由で解決される code path を Skill 文面の grep で確認 |

### Large-local（subprocess、外部通信なし）

`tests/test_phase4_large_local.py` を新規追加。`pyproject.toml [markers]` に
既存登録済の `large_local` を使用。

| 対象 | 例 |
|------|------|
| subprocess `kaji pr create` (provider=local) | exit 2、stderr に「forge-only」を含む |
| subprocess `kaji pr review-comments 1` (provider=local) | exit 2 |
| subprocess `kaji run feature-development.yaml local-pc1-1` (provider=local) | exit 2 + 「workflow ... requires provider.type='github'」 |
| subprocess `kaji run feature-development-local.yaml 1` (provider=github) | exit 2 + 対称ケース |
| subprocess `kaji run feature-development-local.yaml local-pc1-1` (provider=local) | 起動成功（全 step 完走は別 fixture が必要なため、`--before design` で起動だけ確認） |
| subprocess `kaji validate feature-development.yaml` | `requires_provider: github` を含む YAML を pass、不正 enum 値の YAML を reject |
| subprocess `kaji config provider-type` (provider=github) | stdout = `github\n`、exit 0 |
| subprocess `kaji config provider-type` (provider=local) | stdout = `local\n`、exit 0 |
| subprocess `kaji config provider-type` (`[provider]` 不在) | exit 2 + Phase 3-e の fail-fast メッセージ |

### 既存テストの維持

- `test_phase3d_skills.py:test_no_legacy_placeholders_remain_in_skill_md`:
  `FORBIDDEN_PATTERNS` 拡張後も既存 Skill 群で violation 0 件
- `test_phase3e_large_local.py`: Phase 3-e で確立した fail-fast 系がそのまま緑
- `test_workflow_*.py`: workflow ロード後の `requires_provider` default が
  `"any"` のため、既存 workflow YAML 修正前でも通る（commit 1 → 2 の順序で
  実装すれば回帰なし）

## 影響ドキュメント

- `docs/cli-guides/local-mode.md` § 「kaji pr の挙動」を新設し、
  `provider=local` 配下では bare-provider エラー停止する旨を明記。
  `pr-fix` / `pr-verify` / `i-pr` Skill の手動実行も同様
- `docs/dev/workflow-authoring.md` に `requires_provider` フィールドの
  説明を追加（enum 値、default、`kaji run` での整合検証）。**custom workflow
  への migration 推奨**を明記する（レビュー SF2 反映）:
  - default `any` のため既存 custom workflow は突然 fail-fast しない
  - forge 機能（`i-pr` / `pr-*` Skill / `kaji pr ...`）を含む custom workflow
    には `requires_provider: github` を追加することで早期 fail-fast の保護を
    opt-in できる
  - bare 機能のみで構成される custom workflow には `requires_provider: local`
    を追加できる
  - 上記対応をしない custom workflow は本 Phase の保護対象外であり、従来通り
    workflow 終盤で停止する挙動になる
- `docs/dev/workflow_guide.md` に provider × workflow の対応表を追加
  （feature-development → github、feature-development-local → local、
  design-only → any）
- `docs/dev/development_workflow.md` に「workflow 起動時の provider 整合
  fail-fast」のセクションを追加
- `CHANGELOG.md` の `[Unreleased]` に以下を追記:
  - `### BREAKING CHANGE`: `kaji pr ...` が provider=local 配下で exit 2
    する。`build_prompt` の `issue_context` が required になる。Workflow
    YAML に `requires_provider` 整合検証が追加される
  - `### Added`: `Workflow.requires_provider` フィールド、Skill Step 0
    provider ガード
  - `### Migration`: 既存 workflow YAML への `requires_provider` 追加
    （任意。default は `any`）

## 受け入れ条件

### 機械検証

- [ ] **Preflight (commit 0)**: `git branch --merged main | grep
  feat/local-phase3e` がヒットする（Phase 3-e merge 済み確認、レビュー
  MF1 反映）。未マージなら本 Phase の着手を中断
- [ ] **Preflight**: main の `kaji_harness/runner.py:RunIssueContext.issue_context`
  型注釈が `IssueContext`（Optional 解除済）であることを `grep` で確認
- [ ] **Preflight**: main の `kaji_harness/providers/__init__.py:get_provider`
  の戻り型が `IssueProvider`（Optional 解除済）であることを `grep` で確認
- [ ] `make check` 緑（commit 1-10 各時点）
- [ ] `make test-small` / `make test-medium` 緑
- [ ] `make test-large-local` 緑（既存 13 件 + Phase 4 追加分すべて）
- [ ] `make test-large` 緑
- [ ] `mypy kaji_harness/` strict 緑
- [ ] `Workflow.requires_provider` のデフォルトが `"any"` で、未指定の
  workflow が壊れない（既存 `_parse_workflow` テストが回帰しない）
- [ ] `_parse_workflow` が `requires_provider: foo` を `WorkflowValidationError`
  で拒否
- [ ] `_handle_pr` が `provider.type='local'` 時に exit 2 を返し、`gh` への
  `subprocess.run` が呼ばれない（mock で確認）
- [ ] `_handle_pr` の `provider.type='github'` 経路は変化なし（mock した
  `gh` 引数組み立てが Phase 3-e と bit-exact）
- [ ] `cmd_run` が workflow.requires_provider と config.provider.type の
  不整合を exit 2 + stderr で報告（CliRunner）
- [ ] `cmd_run` が `requires_provider: any` の workflow を両 provider で
  受理する
- [ ] `prompt.build_prompt` の signature が `issue_context: IssueContext`
  （Optional 解除）に変わっている（mypy + 関数 inspect で確認）
- [ ] `prompt.py` 内に `if issue_context is not None:` 分岐が 0 件
- [ ] `tests/test_phase3d_skills.py:FORBIDDEN_PATTERNS` に `[pr-number]` /
  `<pr-number>` が含まれ、全 Skill markdown で violation 0 件
- [ ] pr-fix / pr-verify / i-pr の SKILL.md に `[provider_type]` ガード句が
  含まれる（grep で確認）
- [ ] subprocess: `kaji pr create -t x -b y` (provider=local) → exit 2、
  stderr に `forge-only` / `provider.type='local'` / `/issue-review-code`
- [ ] subprocess: `kaji run feature-development.yaml local-pc1-1` (provider=local)
  → exit 2、stderr に `requires provider.type='github'`
- [ ] subprocess: `kaji run feature-development-local.yaml 1` (provider=github)
  → exit 2、対称ケース
- [ ] subprocess: `kaji config provider-type` (provider=github) → stdout
  = `github\n`、exit 0
- [ ] subprocess: `kaji config provider-type` (provider=local) → stdout
  = `local\n`、exit 0
- [ ] Skill 文面検証: pr-fix / pr-verify / i-pr の SKILL.md に
  `kaji config provider-type` の呼び出し fallback が含まれる（grep で確認）

### 手動確認

- [ ] dev repo の `.kaji/config.toml`（provider=github 固定）で
  `make test-large-local` が通り、provider=local の subprocess case が
  config overlay 経由で正しく切り替わる
- [ ] dev repo で `/issue-review-code` 等の代替 Skill 案内が user に伝わる
  文面になっていることを目視確認
- [ ] `kaji validate .kaji/wf/*.yaml` が全 5 ファイルで通る

### ドキュメント

- [ ] `CHANGELOG.md` `[Unreleased]` に Phase 4 の BREAKING CHANGE / Added /
  Migration
- [ ] `docs/cli-guides/local-mode.md` に `kaji pr` 挙動セクション
- [ ] `docs/dev/workflow-authoring.md` に `requires_provider` 説明
- [ ] `docs/dev/workflow_guide.md` に provider × workflow 対応表
- [ ] `phase4-implementation-report.md` を作成（実装後）

## Rollback 方針

Phase 4 は破壊的変更を 3 つ含む（CLI 層 / Skill 層 / Workflow 層）。
各層は独立してロールバック可能な commit 粒度で分割している。

- **CLI 層 rollback** — commit 4 (`_handle_pr` bare エラー化) を revert
  すると、`kaji pr` が provider=local 配下で再び passthrough する（Phase
  3-e の挙動に戻る）。事故 PR の余地は残るが、user は `gh` 認証を解除する
  ことで回避可能
- **Workflow 層 rollback** — commit 1-3 を revert すると `requires_provider`
  フィールドが消え、workflow ↔ provider 整合検証が無効化される。Skill 層
  ガード（commit 5）は単独で機能するため、最低限の保護は残る
- **Skill 層 rollback** — commit 5-6 を revert すると Step 0 ガードが消え、
  `[pr-number]` placeholder が復活する。CLI / Workflow 層ガードが残るため、
  workflow 経由の起動は依然として fail-fast する
- **prompt 層 rollback** — commit 7 を revert すると `build_prompt` の
  Optional 互換が復活する。本変更は code 整理のみで semantic 変化なし

3 層のうち **少なくとも 1 層が機能していれば**、provider=local 配下での
forge 機能誤起動は防げる設計。冗長性により安全側に倒せる。

## 実装上の注意

### `get_provider(config)` の戻り値を捨てる場合

`get_provider(config)` は副作用（`config.provider is None` / 設定不整合で
`ValueError` raise）と返り値の両方を持つ。Phase 4 では呼出側が **副作用のみ**
を必要とする箇所がある（`cmd_run` の fail-fast 検証など）。この場合は
**戻り値を変数に束縛せず捨てる**:

```python
# OK — 副作用のみ目的（ruff F841 / mypy unused variable に当たらない）
get_provider(config)

# NG — `provider` を以降で参照しないと未使用変数として lint で落ちる
provider = get_provider(config)  # ← `provider` 未使用なら ruff F841
```

戻り値を `LocalProvider` / `GitHubProvider` の分岐に使う場合（`_handle_pr` /
`_handle_issue` など）は通常通り `provider = get_provider(config)` で束縛する。

判定基準: 同じスコープ内で `provider` 変数を **1 度でも参照するか**。参照
しないなら捨てる。`_ = get_provider(config)` の wildcard 束縛は推奨しない
（`# noqa` / `_ = ` のいずれも noise）。

### `provider` 変数を持つかどうかの整理表

| 関数 | 戻り値の用途 | 推奨形式 |
|------|-------------|---------|
| `cmd_run` | fail-fast 検証のみ | `get_provider(config)` |
| `_handle_pr` | `isinstance(provider, LocalProvider)` で分岐 | `provider = get_provider(config)` |
| `_handle_issue` | 同上 | `provider = get_provider(config)` |
| `runner._resolve_issue_context` | provider 経由で `resolve_issue_context()` 呼出 | `provider = get_provider(config)` |
| `cmd_config_provider_type` | `actual_provider_type(config)` を直接呼ぶため不要 | `get_provider(config)` (副作用のみ) |

### Skill bash one-liner における `provider_type` 解決

`Step 0: provider check` の bash snippet で `${provider_type:-$(kaji config
provider-type)}` を使う際、`kaji config provider-type` の exit code が
非 0 の場合 `set -e` が有効だと Skill 全体が ABORT 前に exit してしまう。
これを避けるため、Skill 内では:

```bash
# `2>/dev/null` で stderr を抑制し、exit 2 でも空文字を変数に入れる
PROVIDER_TYPE="${provider_type:-$(kaji config provider-type 2>/dev/null || true)}"
```

の形を採用し、空文字 / 不明値の場合に Step 0 内で明示的に ABORT を返す。
`|| true` で exit code を握りつぶし、後段の `case "$PROVIDER_TYPE" in` で
判定する。

## 判断済み論点

| # | 論点 | 判断 | 理由 |
|---|------|------|------|
| 1 | `pr_id` / `pr_ref` の prompt.py 注入を Phase 4 でやるか | **やらない** | workflow に pr-* skill が含まれないため発火経路が無い。Phase 5（`kaji sync` + GitHubProvider PR 解決）と一体で扱う方が結合度が下がる |
| 2 | `_PR_BUILTIN_SUBCOMMANDS` も同じガードで止めるか | **止める** | `gh api` 直叩きであり cache 経由でも代替できない。3 層ガードの一貫性も保つ |
| 3 | `kaji pr --help` を bare で通すか | **止める** | forge 機能の help を見せると「使えるかも」という誤解を生む |
| 4 | exit code は 2 / 3 のどちらか | **2 (`EXIT_INVALID_INPUT`)** | user の入力意図ミスであり、ランタイム障害ではない。Phase 3-e の `cmd_run` 正規化方針と一貫 |
| 5 | `requires_provider` の default | **`"any"`** | 既存 workflow（`design-only.yaml` 等）の破壊を避ける。明示宣言を強制すると migration burden が増える |
| 6 | `requires_provider` enum 値 | **`github` / `local` / `any` の 3 値** | 将来 `gitlab` 等を追加したくなった場合に備え、`"forge"` という一括値も検討したが、forge ごとに API spec が違うため明示列挙の方が安全。enum 拡張は別 ADR |
| 7 | `kaji validate` で workflow ↔ provider 突合検証するか | **`kaji run` のみ** | `kaji validate` は config 非依存で workflow 単独検証。`requires_provider` の **値の妥当性** は validate するが、突合は run でのみ行う |
| 8 | `build_prompt` Optional 解除のタイミング | **Phase 4 で実施** | Phase 3-e § 2 で申し送られた既知項目。fixture 整備のコストはかかるが、Phase 4 の他項目（IssueContext 経由の variable 注入）と作業範囲が重なる |
| 9 | Skill Step 0 ガードの verdict は ABORT / RETRY / PASS どれか | **ABORT** | workflow `on: ABORT: end` で workflow を終わらせるため。RETRY / PASS は cycle を回してしまう |
| 10 | i-pr Skill のガードは Workflow 整合検証だけで十分か | **Skill 層も追加** | user が `provider=local` で `/i-pr` を直接呼ぶケースがあり得る（手動 invocation）。3 層ガードの冗長性を保つ |
| 11 | Skill 手動実行時の `provider_type` 解決方法（レビュー MF2） | **`kaji config provider-type` 新設 (read-only)** | TOML 直 grep は overlay 非対応で誤動作、python one-liner は venv 依存で脆い。新規 read-only CLI は副作用ゼロかつ既存 config resolution と同じ path を共有 |
| 12 | `config.provider` narrowing 方法（レビュー MF3） | **`actual_provider_type(config)` helper 集約** | `assert config.provider is not None` を散らすと「provider 確定後に呼ぶ」契約が読みにくい。helper で defensive validation も中央集約 |
| 13 | `requires_provider: any` default の保護範囲（レビュー SF2） | **builtin workflow のみ Phase 4 で保護、custom は opt-in 推奨** | 強制 default を `github` 等にすると user の既存 workflow が突然 fail-fast する。docs で migration 推奨 |
| 14 | `pr_id` / `pr_ref` / `pr_url` の Skill 内確定方法（レビュー SF1） | **i-pr Step 4 で `kaji pr create` の出力末尾 URL を sed 抽出、pr-fix/verify では `kaji pr list --search` の `.[0].number` を抽出** | Phase 5 で prompt 注入が入るまでの暫定。手順を Skill に明示することで暗黙挙動依存を排除 |

## オープンな論点

- `requires_provider` enum 拡張ポリシー。Phase 5 以降に `gitlab` /
  `forgejo` を追加した場合、`forge` という meta enum を入れるか、各 forge
  名を列挙するか。**現時点の判断**: 列挙する（API spec 差を吸収しやすい）
- `kaji pr` の bare-provider エラー文面に **`gh` 認証無効化手順**も含める
  か。事故 PR を防ぐため `gh auth logout` 案内を入れる選択肢があるが、user
  の他リポジトリ運用を壊す可能性があるため見送り候補
- `tests/conftest.py:make_issue_context` ヘルパの命名と公開範囲。`tests/`
  配下のヘルパとして閉じるか、`kaji_harness.providers.testing` のような
  module を作るか。後者は本番コードに test 専用の依存を入れることになる
  ため見送り候補
- Phase 3-e で `_PROVIDER_FALLBACK_WARNED` を削除した際、`prompt.py` の
  Optional 経路が不整合のまま残った。Phase 5 以降で同様の「片付け漏れ」が
  発生しないよう、Phase 完了時の chk リストに「`Optional` / `None`-check
  の残存を grep」を追加するか
- `i-pr` Skill の Step 0 ガードと workflow `requires_provider: github` で
  二重に止めることが過剰か。**現時点の判断**: 冗長性を取る（手動 invocation
  vs workflow invocation で経路が違うため、両方ガードが妥当）

## 工数見積

設計工数: 0.5 日（本書） + レビュー反映 0.2 日 = **0.7 日**。

実装工数: **3.0 日** 想定（レビュー反映で commit 1 と SF 対応分が増えた
ぶん 0.5 日上振れ）。

| commit | 工数 | 主な作業 |
|--------|------|----------|
| 0 (preflight) | 0.05 日 | Phase 3-e merge 確認（grep / git command） |
| 1 | 0.4 日 | `actual_provider_type()` helper + `kaji config provider-type` サブコマンド + Small/Medium テスト |
| 2 | 0.25 日 | `Workflow.requires_provider` model + parser + validator + Small テスト |
| 3 | 0.1 日 | `.kaji/wf/*.yaml` 5 ファイルへの 1 行追加 |
| 4 | 0.25 日 | `cmd_run` 整合検証（`actual_provider_type` 使用）+ Medium テスト |
| 5 | 0.5 日 | `_handle_pr` bare エラー化 + Medium テスト + 既存 mock パターンの再利用 |
| 6 | 0.6 日 | Skill 3 ファイル更新（Step 0 ガード + placeholder 置換 + `pr_id` 確定手順 + 入力表更新） |
| 7 | 0.1 日 | Skill 静的検証拡張 |
| 8 | 0.5 日 | `build_prompt` Optional 解除 + `make_issue_context` factory + `build_prompt` 直接呼び出しテストの個別更新 |
| 9 | 0.25 日 | Large-local subprocess E2E |
| 10 | 0.1 日 | CHANGELOG + docs（custom workflow migration 推奨を含む） |

最も重い commit は **6（Skill 更新）**。3 ファイル × 3 種の作業（Step 0
ガード / placeholder 置換 / `pr_id` 確定手順）に加え、文面の質を担保する
ため目視レビューに時間を割く。次点が **8（fixture 個別更新、レビュー SF3
反映で autouse fixture 楽観論を撤回）**と **5（CLI 分岐）**。

## 参照情報（Primary Sources）

- `draft/design/local-mode/design.md`
  - L1185-1187: workflow ↔ provider type 整合検証は Phase 4 検討事項
  - L1189-1197: `pr-fix` / `pr-verify` の bare provider エラー文面例
  - L972-1010: local mode における /issue-close の手順（PR 概念無し）
  - L1496: Phase 4 で扱う論点（CLI / Skill / Workflow の 3 層ガード）
- `draft/design/local-mode/phase3e-design.md`
  - § 2: `IssueContext is None` 経路の削除（Phase 4 へ申し送り）
- `draft/design/local-mode/phase3e-implementation-report.md`
  - L237-240: 「Phase 4 候補」として `build_prompt` required 化と prompt
    内 if 分岐の構造的削除
  - L235-237: Phase 4 メイン項目（`kaji pr` の bare エラー化、`pr_id` /
    `pr_ref` 注入、`pr-fix` / `pr-verify` の provider 分岐）
- `kaji_harness/cli_main.py:589-643` (feat/local-phase3e): `_handle_pr` の
  Phase 3-e 状態（`provider.type='local'` 配下を passthrough する）
- `kaji_harness/prompt.py:13-53`: `build_prompt` の Optional 経路
- `kaji_harness/runner.py:147-175`: `RunIssueContext` の構築（Phase 3-e で
  Optional 解除済）
- `tests/test_phase3d_skills.py:17-28`: `FORBIDDEN_PATTERNS` の現状
- `.claude/skills/pr-fix/SKILL.md`、`pr-verify/SKILL.md`、`i-pr/SKILL.md`:
  `[pr-number]` placeholder の出現箇所
- `.kaji/wf/feature-development*.yaml`、`design-only.yaml`、`implement-to-pr.yaml`:
  workflow YAML の現状

## 完了条件の段階確認

| 段階 | 確認内容 |
|------|----------|
| commit 0 完了 | Phase 3-e merge 確認（main に `feat/local-phase3e` の 9 commit が含まれる） |
| commit 1 完了 | `kaji config provider-type` が provider 別に正常応答、`actual_provider_type()` helper が mypy strict で通る |
| commit 2-3 完了 | `make test-small` / `make test-medium` 緑、既存 workflow YAML が新フィールド有無に関わらず load 可能 |
| commit 4 完了 | `cmd_run` 不整合検出が CliRunner で確認できる |
| commit 5 完了 | `_handle_pr` bare エラー化が Medium / `make test-large-local` で確認できる |
| commit 6-7 完了 | Skill 静的検証 + Step 0 ガード文言（`kaji config provider-type` 経由 fallback を含む）が grep / Medium テストで確認できる |
| commit 8 完了 | `build_prompt` Optional 解除後も `make check` 緑、`make_issue_context` factory が公開されている |
| commit 9 完了 | subprocess E2E で 3 層ガードすべてが exit 2 を返す。手動実行 fallback（`provider_type` 未注入 + `kaji config provider-type` 解決）も subprocess で確認 |
| commit 10 完了 | CHANGELOG / docs 更新（custom workflow migration 推奨含む）、`phase4-implementation-report.md` の skeleton を作成 |
| Phase 4 全完了 | 3 層ガードの相互独立性を Rollback 方針通りに確認（commit 2-4 単独 revert で Skill 層が機能、commit 6-7 単独 revert で Workflow 層が機能） |
