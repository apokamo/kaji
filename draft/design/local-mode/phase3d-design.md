---
status: draft
phase: 3d
parent: phase3-design.md
created: 2026-05-06
---

# [設計] kaji local mode — Phase 3-d: local init + workflow + Skill 5 変数移行

Issue: TBD（local-mode buildout 期間中の handoff 設計。GitHub 復旧後に該当 Issue へ紐付ける）

## 概要

Phase 3-c および Phase 3-d preflight を経た provider 抽象 / `IssueContext` 注入 / canonical issue id / PyYAML frontmatter / slug optional / O_EXCL comment write の上に、local mode を **主運用可能** にする残作業をまとめる。

本書は他担当者が実装する前提の handoff 文書である。実装担当者は本書の「方針決定」「実装範囲」「受け入れ条件」を正本として扱い、`phase3-design.md` の Phase 3-d 該当箇所と矛盾する場合は本書の判断を優先する（preflight で前提が動いているため）。本書は phase3-design.md を **置き換えない**。phase3-design.md の `kaji local init` 仕様（§ `kaji local init` 仕様）と `feature-development-local.yaml` 全文（§ `feature-development-local.yaml` step 構成）は本書でも正本として参照する。

## 背景・目的

### Phase 3-c + preflight 完了時点の状態

実装済み（参照: `phase3c-implementation-report.md`, `phase3d-preflight-implementation-report.md`）:

- `kaji_harness/providers/` の `IssueProvider` / `IssueContext` 抽象、`LocalProvider` / `GitHubProvider` 実装、`get_provider()` factory（`provider.type` 未設定時は WARN + legacy fallback）
- `kaji issue` dispatcher の provider 切替、`kaji run` の `IssueContext` 解決と prompt 注入経路（5 変数を `IssueContext` 由来で渡す）
- `WorkflowRunner._resolve_run_issue_context()` による canonical issue id 確定、SessionState / RunLogger / build_prompt の canonical id 統一
- LocalProvider の PyYAML frontmatter / `validate_branch_prefix` / `derive_slug_from_title` / O_EXCL comment write
- Python `jq` package による `--jq` 実装（system jq 非依存）

未着手（本 Phase 3-d で実装）:

| 項目 | 影響 |
|------|------|
| `kaji local init` CLI が無い | local mode 初期化が user 手作業（machine_id / config.local.toml / .gitignore を 3 箇所同期）、初期化ミスでの id 採番衝突や machine_id 漏洩リスクが残る |
| `feature-development-local.yaml` が `.kaji/wf/` に無い | `kaji run` で local-first 開発を `--workflow` 単発起動できず、Skill 個別起動運用に依存する |
| Skill markdown 21 ファイルが旧 placeholder（`[worktree-absolute-path]` / `[branch-name]` / `[design-path]` / `[issue-input]` 等）のまま | prompt.py が注入する 5 変数（`[worktree_dir]` / `[branch_name]` / `[design_path]` / `[issue_input]` / `[branch_prefix]`）と命名がずれ、注入が機能しない箇所が残る |
| `issue-close` Skill が provider 分岐を持たない | provider=local では `kaji pr merge` ではなく `git merge --no-ff` + frontmatter close が要るが Skill は GitHub 前提のまま |
| dev repo `.kaji/config.toml` に `[provider]` が無い | Phase 3-e の fail-fast 化を発動した瞬間に dev repo 自身が壊れる（preflight 段階では fallback で生き残っている） |
| `.gitignore` に `.kaji/config.local.toml` が無い | local config の `machine_id` 等が git に紛れ込むリスクが構造化されていない |

### Phase 3-d が解かない問題（後続）

| 項目 | Phase |
|------|-------|
| `provider.type` 未設定 fallback の削除 + fail-fast 化 | 3-e |
| `kaji pr` の bare provider エラー化、`pr-fix` / `pr-verify` Skill の provider 分岐 | 4 |
| `pr_id` / `pr_ref` の prompt 注入変数化 | 4 |
| `kaji sync from-github` / cache 整備 | 5 |
| BCP runbook | 5 |

## スコープ

### in-scope

1. **`kaji local init` CLI 実装** — phase3-design.md § `kaji local init` 仕様 をベースに、本書 § 3 で **Step 4-5 の生成位置を上書き**（active provider 値を `.kaji/config.local.toml` 側に書く）。`validate_machine_id` を explicit に呼ぶ
2. **`.kaji/wf/feature-development-local.yaml` 追加** — phase3-design.md § `feature-development-local.yaml` step 構成 の YAML をそのまま採用し、`kaji validate` 通過を確認する
3. **`IssueContext` / `GitHubProviderConfig` 拡張** — `IssueContext.default_branch: str = "main"` 追加、`GitHubProviderConfig.default_branch: str = "main"` 追加、LocalProvider/GitHubProvider の `resolve_issue_context` で値を流す
4. **Skill markdown 21 ファイルの placeholder 統一** — 旧 hyphen 形式 `[worktree-absolute-path]` 系を、prompt.py の注入名と一致する `[worktree_dir]` / `[branch_name]` / `[design_path]` / `[issue_input]` / `[branch_prefix]` に置換する
5. **`prompt.py` への placeholder 追加** — `[provider_type]` および `[default_branch]` の 2 変数を `IssueContext` 由来で Skill prompt に注入する
6. **`issue-close` Skill の provider 分岐** — Skill markdown を `provider=local` / `provider=github` 両対応に書き換え。local 経路は **design.md L972-996 の 6-step（preflight + base 更新 + merge + frontmatter commit + cleanup + push）を完全反映**。Step 4 では `kaji issue close [issue_id] --reason completed` を明示 + `LocalProvider.close_issue` の default を `"completed"` に修正
7. **dev repo 自身の dogfooding 整備** — `.kaji/config.toml` に committed `[provider.type = "github"]` + `[provider.github] repo = "apokamo/kaji"` + `default_branch = "main"` を追記、`.kaji/config.local.toml` overlay で buildout 中は `local` を試せる状態を作る、`.gitignore` に `.kaji/config.local.toml` を追加する
8. **Small / Medium テスト整備** — 上記 7 項目の検証
9. **影響ドキュメント補正** — phase3-design.md（§ Step 4-5 の cross-reference 注記、オープン論点更新）/ docs/cli-guides/local-mode.md（新規）への反映

### out-of-scope

| 項目 | 後続 |
|------|------|
| `provider.type` 未設定時の WARN+fallback の削除 | Phase 3-e |
| Large-local E2E（subprocess 起動 + multi-PC simulation） | Phase 3-e（ロールアウト戦略の出口要件側へ寄せる。本書では smoke 範囲のみ要求） |
| `i-pr` / `pr-fix` / `pr-verify` の provider 分岐実装 | Phase 4（本 Phase では placeholder 統一のみ） |
| Skill markdown の旧 mapping table（`issue-create/SKILL.md:37-44` の `branch_prefix` ⇔ label 表）削除 | 後続。本書では `_mappings.LABEL_TO_PREFIX` が正本である旨を Skill 側に注記するだけ |
| `kaji local init --force` 上書き flag | phase3-design.md § オープン論点。Phase 3-d では持たない |

## 方針決定

### 1. Skill 5 変数 placeholder 統一の正本マッピング

#### 現状（grep 確定 2026-05-06）

`grep -rln '\[branch-name\]\|\[worktree-absolute-path\]\|\[design-path\]\|<branch-name>\|<worktree-absolute-path>\|<design-path>\|\[issue-input\]\|<issue-input>\|\[branch_prefix\]' .claude/skills/` で 21 ファイル。山括弧形式（`<branch-name>` 等）は **0 件**（preflight 後の確認）。よって本 Phase の置換は hyphen 形式のみを対象とする。`<issue_id>` / `<issue_ref>` の山括弧形式は `$ARGUMENTS = <issue_id>` のような **メタ説明文**であり、置換対象ではない（Phase 2-B から保持）。

#### 置換ルール（hyphen → underscore 化）

| 旧 placeholder | 新 placeholder（prompt.py 注入名と一致） | 出現ファイル数 |
|----------------|------------------------------------------|---------------|
| `[worktree-absolute-path]` | `[worktree_dir]` | 多数（13 ファイル超） |
| `[branch-name]` | `[branch_name]` | 主に `issue-close` / `pr-*` |
| `[design-path]` | `[design_path]` | 主に `issue-design` / `issue-implement` |
| `[issue-input]` | `[issue_input]` | （現状未確認、念のため対象） |
| `[branch_prefix]` | （変更なし。既に正規形） | 既に統一済 |

`[branch_prefix]` は既に `prompt.py` の注入名と一致しているため touch しない。grep の検出対象に入れているのは、新規記述で誤って `[branch-prefix]` 形式が混入した場合に検出できるよう **回帰検知用**として維持する。

#### Group 分割（21 ファイル → 3 commit）

レビュー単位を整え、誤置換時の影響面を限定するため、commit を 3 グループに分ける。

**Group A: issue-* 系（13 ファイル）**

```
.claude/skills/_shared/implement-by-type/{bug,feat,refactor}.md
.claude/skills/issue-design/SKILL.md
.claude/skills/issue-implement/SKILL.md
.claude/skills/issue-close/SKILL.md
.claude/skills/issue-fix-code/SKILL.md
.claude/skills/issue-fix-design/SKILL.md
.claude/skills/issue-review-code/SKILL.md
.claude/skills/issue-review-design/SKILL.md
.claude/skills/issue-verify-code/SKILL.md
.claude/skills/issue-verify-design/SKILL.md
.claude/skills/i-dev-final-check/SKILL.md
```

`feature-development-local.yaml` で実際に呼ばれる skill 群。最優先で 5 変数化する。

**Group B: i-doc-* 系（5 ファイル）**

```
.claude/skills/i-doc-final-check/SKILL.md
.claude/skills/i-doc-fix/SKILL.md
.claude/skills/i-doc-review/SKILL.md
.claude/skills/i-doc-update/SKILL.md
.claude/skills/i-doc-verify/SKILL.md
```

docs-only workflow 用。Phase 3-d で同等改修するが、Group A とは別 commit。

**Group C: PR / pr-* 系（3 ファイル）**

```
.claude/skills/i-pr/SKILL.md
.claude/skills/pr-fix/SKILL.md
.claude/skills/pr-verify/SKILL.md
```

placeholder 置換 **のみ** 行う。`provider=local` 時の動作変更（bare provider エラー化）は Phase 4 範囲。本 Phase では命名整合だけ取り、Phase 4 で provider 分岐を素直に追加できる土台を作る。

#### 静的検証

Phase 2-B 同様、Medium テストで以下を保証する。山括弧形式は現状 grep 0 件だが、新規混入の回帰検知のため forbidden list に含める。

```python
# tests/test_phase3d_skills.py（仮）
def test_no_legacy_placeholders_remain() -> None:
    forbidden = [
        # hyphen 形式（現状残存）
        r"\[worktree-absolute-path\]",
        r"\[branch-name\]",
        r"\[design-path\]",
        r"\[issue-input\]",
        # 山括弧形式（現状 0 件、回帰検知用）
        r"<worktree-absolute-path>",
        r"<branch-name>",
        r"<design-path>",
        r"<issue-input>",
    ]
    for path in pathlib.Path(".claude/skills").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert not re.search(pattern, text), f"{path}: {pattern}"
```

`<issue_id>` / `<issue_ref>` の山括弧形式は **メタ説明文**（`$ARGUMENTS = <issue_id>` 等の構文表記）として保持する。これらは forbidden 対象に含めない（Phase 2-B から踏襲）。

### 2. `provider_type` / `default_branch` placeholder の追加

#### 問題

`issue-close` Skill は GitHub 前提で `kaji pr merge [branch-name]` を呼ぶ。provider=local では PR 概念が無く、design.md L972-996 の 6-step 手順（preflight + base 更新 + merge + frontmatter commit + cleanup + push）に切り替わる。Skill markdown を 2 ファイルに分けると workflow yaml の `skill: issue-close` が provider 別になり、両 yaml の保守負担が増える。

加えて、design.md L975 で `provider.local.default_branch` を base branch として使用する仕様、phase3-design.md `kaji local init --default-branch` 引数、`config.py` の `LocalProviderConfig.default_branch` field がすべて存在する。`issue-close` Skill が `main` 固定にすると `--default-branch develop` で初期化した repo で merge 先が壊れる。

#### 決定

以下 2 つの placeholder を追加する。

1. **`[provider_type]`**: `IssueContext.provider_type` を `prompt.py` で `[provider_type]` として注入。`issue-close` で provider 経路分岐に使う。`IssueContext.provider_type` field は preflight 時点で既に存在（`kaji_harness/providers/models.py:96`）
2. **`[default_branch]`**: 新たに `IssueContext.default_branch: str` field を追加し、`prompt.py` で `[default_branch]` として注入。`issue-close` で `git switch [default_branch]` / `git merge --ff-only [remote]/[default_branch]` / `git push [remote] [default_branch]` の引数に使う

```python
# kaji_harness/providers/models.py (IssueContext を拡張)
@dataclass(frozen=True)
class IssueContext:
    issue_id: str
    issue_ref: str
    issue_input: str
    slug: str
    branch_prefix: str
    branch_prefix_fallback: bool = False
    branch_name: str = ""
    worktree_dir: str = ""
    design_path: str = ""
    provider_type: str = ""
    default_branch: str = "main"  # 追加
```

```python
# kaji_harness/prompt.py（変更点）
if issue_context is not None:
    variables["issue_input"] = issue_context.issue_input
    variables["branch_prefix"] = issue_context.branch_prefix
    variables["branch_name"] = issue_context.branch_name
    variables["worktree_dir"] = issue_context.worktree_dir
    variables["design_path"] = issue_context.design_path
    variables["provider_type"] = issue_context.provider_type  # 追加
    variables["default_branch"] = issue_context.default_branch  # 追加
```

#### `default_branch` の供給ルール（provider 別）

| provider | `IssueContext.default_branch` の source |
|----------|----------------------------------------|
| local | `LocalProvider.default_branch`（`config.provider.local.default_branch` 経由で初期化済、existing field） |
| github | `config.provider.github.default_branch`（**Phase 3-d で新規追加**、default `"main"`） |

`GitHubProviderConfig.default_branch: str = "main"` の追加は本書 § 3 の「`provider.github.default_branch` config field の追加」と一体実装する。`gh repo view --json defaultBranchRef` の subprocess 呼び出しは buildout 中疎通不可のため採用しない。

#### Skill markdown 側の分岐パターン

`issue-close` の最小例（実際の 6-step 完全版は § 7）:

```markdown
### provider=local の場合

`[provider_type]` が `local` のとき、PR 概念は無いため design.md § local mode における /issue-close の手順 に従う:

```bash
cd [worktree_dir]
git switch [default_branch] && git merge --ff-only origin/[default_branch]
git merge --no-ff --no-edit [branch_name]
kaji issue close [issue_id]   # frontmatter 更新を担当
git add .kaji/issues/[issue_id]-*/issue.md
git commit -m "chore(issue): close [issue_ref]"
```
```

エージェント（claude / codex）は `[provider_type]` 値で分岐を選ぶ。

#### 影響範囲

- `provider_type` / `default_branch` の 2 placeholder 追加は新規のため、Phase 3-d で touch する Skill 以外への影響はない
- 実際に分岐を **使う** Skill は `issue-close` のみ。`i-pr` / `pr-fix` / `pr-verify` の bare provider エラー化（Phase 4）でも同じ placeholder を再利用可能
- `IssueContext` field 追加は dataclass の後方互換 default 値（`""` / `"main"`）でカバー。Phase 3-c の既存テストへの影響は fixture 拡張で吸収

### 3. `kaji local init` 仕様の上書き — active provider は overlay 側に書く

#### 問題

phase3-design.md § `kaji local init` 仕様 Step 4 は `.kaji/config.toml` に `type = "local"` + `[provider.local] default_branch` を追記し、Step 5 は `.kaji/config.local.toml` に `[provider.local] machine_id` のみを書く構成になっている。この構成には以下の問題がある。

- **個人選択の commit 化**: `provider.type` は user / machine 個人の選択（buildout 中だけ local、復旧後は github 等）であり、tracked file に commit されるとチーム / 復旧後の運用と相性が悪い
- **dev repo の運用矛盾**: kaji リポジトリは長期 GitHub 主運用。Step 4 通りに `type = "local"` を commit するとリポジトリの default 挙動が local に固定され、GitHub 復旧後の「単に gh で issue 操作する」を阻害する
- **clone 時の壊れた default**: 他 user が clone した瞬間 `type = "local"` が effective になるが、その user の `machine_id` は未設定なので `kaji issue` がエラーで止まる

#### 決定（phase3-design.md Step 4-5 を上書き）

**active provider 値（特に `provider.type`、`provider.local.machine_id`、`provider.local.default_branch`）は必ず `.kaji/config.local.toml` (gitignored) 側に書く。tracked `.kaji/config.toml` は repository default 値（`provider.github.repo` 等のチーム共有情報）のみを保持する。**

`kaji local init` の挙動を以下に上書きする（phase3-design.md § `kaji local init` 仕様 Step 4-5 を本書で置換）:

| ステップ | phase3-design.md 記述 | **本書での上書き** |
|----------|----------------------|--------------------|
| Step 4 | `.kaji/config.toml` に `type = "local"` + `[provider.local] default_branch` を追記 | `.kaji/config.toml` には **何も書かない**。既存 `[provider]` セクションがあっても触らない（`--force` は持たない） |
| Step 5 | `.kaji/config.local.toml` に `[provider.local] machine_id` のみ | `.kaji/config.local.toml` に `[provider] type = "local"` + `[provider.local] machine_id = "..."` + `[provider.local] default_branch = "<--default-branch or main>"` を書く |

`[provider]` overlay は Phase 3-c で section 全体上書きが効くため、overlay 側で `type` を含む全 active 値を持てる。

#### 生成される overlay 例

```toml
# .kaji/config.local.toml （gitignored、kaji local init で生成）
[provider]
type = "local"

[provider.local]
machine_id = "pc1"
default_branch = "main"
```

#### dev repo の `.kaji/config.toml` (committed)

```toml
[paths]
artifacts_dir = ".kaji-artifacts"
skill_dir = ".claude/skills"

[execution]
default_timeout = 1800

[provider]
type = "github"

[provider.github]
repo = "apokamo/kaji"
default_branch = "main"
```

`type = "github"` は kaji リポジトリの **repository default**（誰が clone しても初期挙動として GitHub 運用が想定される）を commit したもの。各 user は `kaji local init` で overlay を作って local 試運用に切り替える。GitHub 復旧後は overlay を消すか上書きすれば github 運用へ戻る。

#### 根拠

- 個人選択（provider 切替、machine_id）と repository default を構造で分離すると、clone / dogfooding / 復旧の各局面で挙動が破綻しない
- `.kaji/config.local.toml` を消せば自動で `.kaji/config.toml` の default に戻るため、復旧経路が常に存在する
- Phase 3-e fail-fast 化のとき、`.kaji/config.toml` に `[provider.type = "github"]` が commit されていれば dev repo は破壊されない
- 親設計 Step 4 の「`.kaji/config.toml` に追記」は当初 BCP 短期 fallback を想定しており、長期 local-first / GitLab 移行可能性が前提に変わった本 Phase（phase3-design.md § 1 案 D 採用）と整合しない。本書で上書きすることで矛盾を解消する

#### `provider.github.default_branch` config field の追加

dev repo の `.kaji/config.toml` 例で `[provider.github] default_branch = "main"` を書いている。この field は phase3-design.md には未記載で、Phase 3-d preflight でも追加されていない。

**Phase 3-d で `kaji_harness/config.py` の `GitHubProviderConfig` に `default_branch: str = "main"` を追加する。**

理由:
- `IssueContext.default_branch` を Must Fix #4 で追加するため、GitHubProvider 側にも source が要る
- buildout 中は `gh repo view --json defaultBranchRef` の subprocess 呼び出しが疎通不可。config 由来が安全
- `provider.local.default_branch` が既に config field なので、対称的な構造になる

未設定時 default は `"main"`。fail-fast はしない（Phase 3-e で全 config field を fail-fast 化するか個別判断）。

#### Phase 3-d で touch する範囲

- `kaji_harness/local_init.py`（新規）: 上書き仕様で TOML 出力
- `kaji_harness/config.py`: `GitHubProviderConfig.default_branch: str = "main"` を追加
- `phase3-design.md` § `kaji local init` 仕様 Step 4-5: 本書で上書きされたことの cross-reference 注記を追記
- `docs/cli-guides/local-mode.md`（新規）: 上記の up-to-date 仕様を user 向けに記載

### 4. PR-3d 内 commit 順序

#### 設計判断

phase3-design.md Step 14-16 は dev repo config → Skill → workflow の順を示すが、preflight が canonical id / PyYAML / slug optional を確定させた今、より安全な順序は **副作用の小さいものから順に**:

| commit | 範囲 | 副作用 | rollback コスト |
|--------|------|--------|----------------|
| 1 | `.kaji/wf/feature-development-local.yaml` 追加 | 既存 workflow に影響なし。`kaji validate` のみ | revert で完全戻し |
| 2 | `IssueContext.default_branch` 追加 + `GitHubProviderConfig.default_branch` 追加 + LocalProvider/GitHubProvider の `resolve_issue_context` 拡張 + Small テスト | dataclass field 追加（後方互換 default 値あり）、既存 IssueContext consumer は touch 不要 | revert で完全戻し |
| 3 | `kaji local init` CLI 実装（本書 § 3 の overlay 仕様）+ `validate_machine_id` 呼び出し + Medium テスト | 新規サブコマンド。既存 `kaji issue` / `kaji run` には触れない | revert で完全戻し |
| 4 | `prompt.py` に `provider_type` / `default_branch` 注入追加 + Small テスト | 既存 5 変数の挙動は不変、新規 2 変数追加のみ | revert で完全戻し |
| 5 | Skill Group A 5 変数化（13 ファイル） | dev repo 内の AI 起動経路に影響。`prompt.py` が既に IssueContext を流すので静的差分のみ | grep ベース静的検証 + revert |
| 6 | Skill Group B 5 変数化（5 ファイル） | docs-only workflow に影響 | 同上 |
| 7 | Skill Group C 5 変数化（3 ファイル） | PR 系 Skill 命名整合のみ。動作変更なし | 同上 |
| 8 | `issue-close` への provider 分岐 + design.md L972-996 の 6-step 反映 | `provider=github` 経路の挙動は不変、新たに local 6-step 分岐を **追加** するのみ | revert で local 経路喪失 |
| 9 | dev repo `.kaji/config.toml` に `[provider.type = "github"]` + `[provider.github] repo` + `default_branch` 追記、`.gitignore` 更新 | dogfooding 開始 | revert で legacy fallback 復活 |

commit 1-4 は contract 整備、commit 5-8 は Skill markdown、commit 9 は dogfooding に分けることで、各 commit のレビュー焦点を保つ。

#### make check ゲート

各 commit の境界で `make check` 緑を維持する。Skill markdown 変更は Python テスト挙動を変えないため、`pytest` は Group A→B→C のいずれの commit でも緑のままのはず。`prompt.py` 拡張（commit 4）と IssueContext 拡張（commit 2）は既存テストの fixture 拡張が必要。

### 5. `feature-development-local.yaml` の確定（phase3-design.md からの差分なし）

phase3-design.md § `feature-development-local.yaml` step 構成 で完全な YAML が決定済。本 Phase では **そのまま採用**する。本書での追記は以下のみ:

- 配置先: `.kaji/wf/feature-development-local.yaml`（既存 4 つの yaml と同位置）
- workflow name: `feature-development-local`（YAML 内 `name` と一致）
- `kaji validate .kaji/wf/feature-development-local.yaml` が通ることを Medium テストで確認

`final-check: PASS → close` の差分が、既存 `feature-development.yaml` との唯一の構造差分。

### 6. `kaji local init` の preflight 整合（phase3-design.md からの差分のみ）

phase3-design.md § `kaji local init` 仕様 を正本として実装する。preflight 後に確認すべき差分:

#### `socket.gethostname()` sanitize の確定

phase3-design.md L385 の sanitize 規則を本書で具体化:

```python
import socket, re
raw = socket.gethostname()
candidate = re.sub(r"[^a-z0-9]", "", raw.lower())[:16]
# 空文字なら次の pcN fallback へ
```

`[a-z0-9]{1,16}` regex（`validate_machine_id` 相当）に通れば採用。落ちれば `pc1` / `pc2` / … に fallback。

#### machine_id validation の所在

`kaji_harness/providers/local.py:59` に `validate_machine_id(machine_id: str) -> None` が **既に存在し、`[a-z0-9]{1,16}` の regex 検証を担う**。一方 `kaji_harness/config.py:219-221` の ProviderConfig 解析は `isinstance(machine_id, str)` のみで regex 検証を行わない（preflight 時点までの仕様）。

**Phase 3-d では `kaji local init` から `validate_machine_id` を explicit に呼ぶ。** config 再ロードによる暗黙検証に頼らない。

```python
# kaji_harness/local_init.py
from kaji_harness.providers.local import validate_machine_id

def _resolve_machine_id(args, existing_ids: set[str]) -> str:
    if args.machine_id is not None:
        validate_machine_id(args.machine_id)  # ValueError → exit 2
        return args.machine_id
    # ... fallback to socket.gethostname() sanitize / pcN
```

呼び出しタイミング:
- `--machine-id` 明示時: 値の採用前
- hostname sanitize 経由の候補: sanitize 結果を採用前（sanitize 出力が `[a-z0-9]{0,16}` を満たすかは sanitize アルゴリズムで保証されるが、空文字回避と `pcN` fallback の境界で念のため呼ぶ）
- `pcN` fallback: `pc1` 〜 `pcN` も `validate_machine_id` を通せば万一の regex 変更にも追随

`validate_machine_id` を `providers/context.py` へ移すか否かは Phase 3-d スコープ外（重複定義を避けるため `providers/local.py` 留置のまま再 export 不要）。`kaji_harness/local_init.py` からは `from kaji_harness.providers.local import validate_machine_id` で参照する。

#### config.py への validation 統合は別 Issue

`config.py` の ProviderConfig 解析で `validate_machine_id` を呼ばせる改修は、Phase 3-e の fail-fast 化と同時に検討する（`provider.local.machine_id` の regex 違反を config load 時点で fail-fast にするか、それとも各使用箇所での validation に留めるかの方針判断を伴うため）。本 Phase では `kaji local init` 経路の validation のみ確定する。

#### 既存 Issue dir からの machine_id 抽出（重複検知用）

phase3-design.md L381 の「他 PC の machine_id 一覧」を抽出する glob:

```python
import re
artifacts_dir = pathlib.Path(".kaji/issues")
ids = set()
for d in artifacts_dir.glob("local-*"):
    m = re.match(r"^local-([a-z0-9]+)-\d+(?:-.*)?$", d.name)
    if m:
        ids.add(m.group(1))
```

このロジックは `kaji local init` 専用で、provider にも runner にも露出しない（責務漏れ防止）。

#### exit code

phase3-design.md L405-408 の exit code を遵守:

| 状況 | exit |
|------|------|
| 正常完了 | 0 |
| `--machine-id` 不正 | 2 |
| 既存 `.kaji/config.local.toml` あり | 3 |

それ以外（`--default-branch` 未指定で main 採用、interactive 入力での候補選択）は exit 0。

### 7. `issue-close` Skill provider 分岐の実装範囲

#### 現状

`.claude/skills/issue-close/SKILL.md` は L85 で `kaji pr merge [branch-name]` を呼ぶ前提。L114-128 で local branch / remote branch の削除を行う。`provider=local` 時はそもそも `kaji pr` が動かないので（Phase 4 で error 化されるが Phase 3-d 時点では undefined behavior）、Skill 単体では機能しない。

#### 採用する正本: design.md L972-996

`provider=local` 時の `/issue-close` 手順は **design.md § local mode における /issue-close の手順 (L972-996) 全 6 step を完全反映する**。本書で短縮しない。

design.md の 6 step（要約は本書、正本は design.md）:

| Step | 内容 | 失敗時 |
|------|------|--------|
| 1. Preflight check | `git status --porcelain` 空、HEAD が `<type>-local-<machine>-<n>-<slug>` ブランチ、base branch が local 存在 | ABORT、原因表示 |
| 2. Base branch 最新化 | remote あれば `git fetch [remote] [default_branch]`、`git switch [default_branch] && git merge --ff-only [remote]/[default_branch]` | fast-forward 失敗 → ABORT |
| 3. Merge 実行 | `git merge --no-ff --no-edit [branch_name]` | 衝突 → ABORT、Issue は open のまま、手動 resolve 依頼 |
| 4. Issue frontmatter 更新 + commit | `kaji issue close [issue_id] --reason completed` で frontmatter (`state: closed` / `closed_at` / `close_reason: completed` / `closed_by`) 更新、`git add .kaji/issues/[issue_id]-*/issue.md && git commit -m "chore(issue): close [issue_ref]"` | commit 失敗 → ABORT、Issue は open のまま |
| 5. Cleanup | `git worktree remove [worktree_dir]` → `git branch -d [branch_name]`（順序必須） | 警告のみ、Issue は既に closed |
| 6. Push | `git push [remote] [default_branch]`（remote あれば） | 警告のみ、Issue 状態は確定 |

**Step 4 までで Issue close は確定**。Step 5/6 の失敗は警告に留め、user が手動回復できる状態にする（design.md L996）。

#### Phase 3-d での Skill markdown 改修範囲

`.claude/skills/issue-close/SKILL.md` に以下を実施する。

1. **placeholder 統一**: 既存 `[branch-name]` を `[branch_name]` に置換（Group A の一部として commit 4 で実施）
2. **provider 分岐セクション追加**:
   - `[provider_type]` が `github` の場合: 既存 `kaji pr merge [branch_name]` 経路を保持
   - `[provider_type]` が `local` の場合: design.md L972-996 の 6 step を Skill markdown 内に手順として展開
3. **6 step の Skill markdown 表現**: 各 step で具体的な bash コマンドと失敗時の挙動を記載

具体的な markdown ドラフト（実装担当者が SKILL.md に挿入する内容）:

````markdown
## マージ・クローズ手順

`[provider_type]` に応じて手順が異なる。

### provider=github の場合

```bash
kaji pr merge [branch_name]
```

その後、ローカル/remote branch 削除（既存セクション）に進む。

### provider=local の場合

design.md § local mode における /issue-close の手順 (6 step) を実行する。

#### Step 1: Preflight check

```bash
cd [worktree_dir]
test -z "$(git status --porcelain)" || { echo "ABORT: uncommitted changes"; exit 1; }
git rev-parse --abbrev-ref HEAD | grep -qE "^[a-z]+/local-[a-z0-9]+-[0-9]+(-[a-z0-9-]+)?$" || { echo "ABORT: not on feature branch"; exit 1; }
git rev-parse --verify [default_branch] >/dev/null 2>&1 || { echo "ABORT: base branch [default_branch] missing locally"; exit 1; }
```

#### Step 2: Base branch 最新化

```bash
# remote 設定がある場合のみ fetch + ff-only merge
if git remote get-url origin >/dev/null 2>&1; then
    git fetch origin [default_branch]
    git switch [default_branch]
    git merge --ff-only origin/[default_branch] || { echo "ABORT: ff-only merge failed"; exit 1; }
else
    git switch [default_branch]
fi
```

#### Step 3: Merge 実行

```bash
git merge --no-ff --no-edit [branch_name] || { echo "ABORT: merge conflict, resolve manually"; exit 1; }
```

#### Step 4: Issue frontmatter 更新 + commit

```bash
kaji issue close [issue_id] --reason completed   # frontmatter (state, closed_at, close_reason, closed_by) を更新
git add .kaji/issues/[issue_id]-*/issue.md
git commit -m "chore(issue): close [issue_ref]" || { echo "ABORT: commit failed"; exit 1; }
```

`--reason completed` は明示で書く。`LocalProvider.close_issue()` の default は本 Phase で `"completed"` に変更する（後述 § 7 sub-section「`LocalProvider.close_issue` の default 修正」）が、Skill markdown 上で何が書かれるかが読み手に明確になるよう reason は省略しない。

**Step 4 完了で Issue close は確定**。以降の失敗は警告のみ。

#### Step 5: Cleanup（worktree → branch の順序必須）

```bash
git worktree remove [worktree_dir] || echo "WARNING: worktree remove failed, manual cleanup needed"
git branch -d [branch_name] || echo "WARNING: branch delete failed, manual cleanup needed"
```

#### Step 6: Push（remote 設定がある場合）

```bash
if git remote get-url origin >/dev/null 2>&1; then
    git push origin [default_branch] || echo "WARNING: push failed, manual push needed"
fi
```
````

`[default_branch]` は本書 § 2 で追加する placeholder。`[issue_id]` / `[issue_ref]` / `[branch_name]` / `[worktree_dir]` は既存。

#### `kaji issue close` の責務

Step 4 の `kaji issue close [issue_id] --reason completed` は LocalProvider が frontmatter 更新を担当する。preflight 時点の実装（`kaji_harness/providers/local.py:611-625`）は `state: closed` / `closed_at: <ISO8601>` / `closed_by: <machine_id>` を書き、`close_reason` は `--reason` 引数の値（None なら空文字）を書く。具体的なフィールド一覧は preflight report § 4 の close path を参照。

#### `LocalProvider.close_issue` の default 修正（Phase 3-d で実施）

##### 問題

現行 `LocalProvider.close_issue(issue_id, reason: str | None = None)` の L623 は `meta["close_reason"] = reason or ""` であり、`--reason` 未指定時に `close_reason: ""` が書かれる。一方、design.md L985 は `close_reason: completed` を要求しており、状態遷移図 L1011-L1015 でも `closed_completed` が `kaji issue close --reason completed`、`closed_not_planned` が `kaji issue close --reason not-planned` と明示されている。default 空文字は設計仕様違反であり、CLI 単体使用時の罠でもある。

##### 決定

**Phase 3-d で `LocalProvider.close_issue` の default を `"completed"` に変更する。**

```python
# kaji_harness/providers/local.py:611-623（変更点）
def close_issue(self, issue_id: str, reason: str | None = None) -> Issue:
    ...
    meta["state"] = "closed"
    meta["closed_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta["closed_by"] = self.machine_id
    meta["close_reason"] = reason if reason else "completed"   # 旧: reason or ""
    _atomic_write(issue_path, self._build_issue_md(meta, current_body))
    return self._read_issue(issue_dir)
```

`reason=""` の明示空文字も `"completed"` に置き換える（実用的な差はないが、空文字 frontmatter を生まないことを構造で保証する）。`reason="not-planned"` 等の明示値は変更せずそのまま記録する。

##### `--reason` 未指定時の CLI 挙動

`kaji_harness/cli_main.py:999` (`p.add_argument("--reason", default=None)`) は touch しない。argparse default は `None` のまま、LocalProvider 内で `None`/空 → `"completed"` 変換を担う。CLI surface としては `--reason` を省略できる挙動を維持する（互換性）。

##### 影響範囲

- `tests/test_providers_local.py` / `tests/test_phase3d_preflight.py` の close path test で `close_reason == ""` を期待しているケースがあれば、`"completed"` 期待に書き換える
- design.md L985 と整合する
- GitHub Issue モデル（API default `completed`）と整合する
- `kaji issue close [issue_id]` 単体呼び出しが有意な default 値を生成する

#### 既存 cleanup セクションとの関係

既存 SKILL.md L114-128 は `git branch -D [branch_name]` / `git push origin --delete [branch_name]` を行っている。これは GitHub PR merge 後の cleanup ロジック。local 経路の Step 5/6 とは別物として共存させる（github 経路でも cleanup は必要）。github 経路は既存セクションを保持、local 経路は新規 6-step セクションを追加。

#### 検証

- **静的検証（Medium）**: SKILL.md に `[provider_type]` 分岐 / `[default_branch]` / Step 1-6 のキーワード（`Preflight check`、`Base branch`、`merge --no-ff`、`kaji issue close`、`git worktree remove`）がすべて含まれることを grep で確認
- **frontmatter 更新（Medium）**: `kaji issue close local-pc1-1` を CliRunner で起動し、frontmatter `state: closed` / `closed_at` / `close_reason` / `closed_by` が書き込まれることを確認
- **実 git merge subprocess（Large-local）**: Phase 3-e 範囲（本 Phase では smoke のみ）

## インターフェース

### 新規 CLI

```
kaji local init [--machine-id <name>] [--default-branch <branch>] [--non-interactive]
```

詳細は phase3-design.md § `kaji local init` 仕様。

### 既存 CLI（変更なし）

`kaji issue` / `kaji run` / `kaji pr` / `kaji validate` の surface は本 Phase で変えない。

### 内部 API

#### `IssueContext` 拡張（Phase 3-d）

```python
# kaji_harness/providers/models.py
@dataclass(frozen=True)
class IssueContext:
    issue_id: str
    issue_ref: str
    issue_input: str
    slug: str
    branch_prefix: str
    branch_prefix_fallback: bool = False
    branch_name: str = ""
    worktree_dir: str = ""
    design_path: str = ""
    provider_type: str = ""           # 既存（preflight 時点）
    default_branch: str = "main"      # 追加
```

LocalProvider の `resolve_issue_context` は `self.default_branch` を流す。GitHubProvider の `resolve_issue_context` は本書 § 3 で追加する `config.provider.github.default_branch` を流す。

#### `GitHubProviderConfig` 拡張（Phase 3-d）

```python
# kaji_harness/config.py
@dataclass
class GitHubProviderConfig:
    repo: str = ""
    default_branch: str = "main"  # 追加
```

#### `prompt.build_prompt` 拡張

```python
# 既存（preflight 時点）
variables = {
    "issue_id": ...,
    "issue_ref": ...,
    "issue_input": ...,        # IssueContext あれば
    "branch_prefix": ...,
    "branch_name": ...,
    "worktree_dir": ...,
    "design_path": ...,
}

# Phase 3-d で追加
variables["provider_type"] = issue_context.provider_type
variables["default_branch"] = issue_context.default_branch
```

`IssueContext is None` の場合（fallback 経路）は新規 2 変数も注入しない。Skill markdown 側は注入されない placeholder を生のまま残す（Phase 2-B から踏襲）。

#### `validate_machine_id` の参照先

`kaji_harness/providers/local.py:59` に既存（preflight 時点で実装済）。Phase 3-d では新規追加せず、`kaji_harness/local_init.py` から:

```python
from kaji_harness.providers.local import validate_machine_id
```

で参照する。`config.py` への validation 統合は Phase 3-e の fail-fast 化と一体検討する（本書 § 6 参照）。

## 実装範囲

### 変更対象ファイル

| ファイル | 変更 | commit 番号 |
|----------|------|------------|
| `.kaji/wf/feature-development-local.yaml` | 新規。phase3-design.md § `feature-development-local.yaml` step 構成 を全文採用 | 1 |
| `kaji_harness/providers/models.py` | `IssueContext.default_branch: str = "main"` 追加 | 2 |
| `kaji_harness/config.py` | `GitHubProviderConfig.default_branch: str = "main"` 追加 | 2 |
| `kaji_harness/providers/local.py` | `resolve_issue_context` で `self.default_branch` を `IssueContext` に流す | 2 |
| `kaji_harness/providers/github.py` | `resolve_issue_context` で `config.provider.github.default_branch` を `IssueContext` に流す | 2 |
| `tests/test_phase3d_default_branch.py`（新規） | `IssueContext.default_branch` の provider 別供給を Small + Medium で検証 | 2 |
| `kaji_harness/cli_main.py` | `local init` サブコマンドの argparse + dispatcher 追加 | 3 |
| `kaji_harness/local_init.py`（新規） | `kaji local init` 本体（本書 § 3 の overlay 仕様で TOML 出力、§ 6 の `validate_machine_id` 呼び出し、machine_id 候補生成、`.gitignore` 確認） | 3 |
| `tests/test_local_init.py`（新規） | `kaji local init` Medium テスト（CliRunner + tmp_path、`--machine-id PC1` exit 2、既存 config.local.toml exit 3、overlay 内容検証） | 3 |
| `kaji_harness/prompt.py` | `provider_type` / `default_branch` placeholder 注入 | 4 |
| `tests/test_prompt.py` | `provider_type` / `default_branch` 注入の Small テスト追加 | 4 |
| `.claude/skills/_shared/implement-by-type/{bug,feat,refactor}.md` | placeholder hyphen→underscore 化 | 5 |
| `.claude/skills/issue-{design,implement,close,fix-code,fix-design,review-code,review-design,verify-code,verify-design}/SKILL.md` | placeholder 統一 | 5 |
| `.claude/skills/i-dev-final-check/SKILL.md` | placeholder 統一 | 5 |
| `.claude/skills/i-doc-{final-check,fix,review,update,verify}/SKILL.md` | placeholder 統一 | 6 |
| `.claude/skills/i-pr/SKILL.md`、`pr-{fix,verify}/SKILL.md` | placeholder 統一のみ。動作変更は Phase 4 | 7 |
| `tests/test_phase3d_skills.py`（新規） | 旧 placeholder 残存検出（hyphen + 山括弧）の Medium テスト | 5-7 |
| `.claude/skills/issue-close/SKILL.md` | provider 分岐セクション追加 + design.md L972-996 の 6-step を local 経路として記述、Step 4 で `--reason completed` 明示 | 8 |
| `kaji_harness/providers/local.py` | `close_issue()` の `meta["close_reason"]` default を `"completed"` に変更（L623） | 8 |
| `tests/test_providers_local.py` / `tests/test_phase3d_preflight.py` | `close_reason == ""` 期待を `"completed"` 期待に書き換え（該当ケースがある場合） | 8 |
| `tests/test_phase3d_skills.py` | provider 分岐記述の静的検証（6-step キーワード grep + `--reason completed` 明示）追加 | 8 |
| `.kaji/config.toml` | `[provider.type = "github"]` + `[provider.github] repo = "apokamo/kaji"` + `default_branch = "main"` 追加 | 9 |
| `.gitignore` | `.kaji/config.local.toml` 行追加 | 9 |
| `draft/design/local-mode/phase3-design.md` | Phase 3-d 範囲完了の反映、§ `kaji local init` 仕様 Step 4-5 が本書で上書きされた旨を cross-reference として注記、オープン論点更新 | 9 |
| `docs/cli-guides/local-mode.md`（新規） | `kaji local init` 上書き仕様 / provider 切替（overlay 経由）/ config schema / ID 文法 / local workflow の最小ガイド | 9 |

### 実装順序

phase3-design.md Step 14-16 ではなく、本書 § 4 の commit 1-9 を採用する。理由は副作用の小さい変更から順次入れることで、各 commit の make check 緑を独立に維持できるため。

## テスト戦略

### 変更タイプ

実行時コード変更 + Skill markdown 変更 + workflow YAML 追加。

### Small テスト

- `provider_type` placeholder 注入の round-trip（`prompt.py` が `IssueContext.provider_type == "local"` / `"github"` を `[provider_type]` に流す）
- `default_branch` placeholder 注入の round-trip（local provider は `provider.local.default_branch`、github provider は `provider.github.default_branch` を `[default_branch]` に流す）
- `IssueContext.default_branch` field の default 値（未指定時 `"main"`）
- `GitHubProviderConfig.default_branch` の TOML parse（未設定時 `"main"`、明示時 `"develop"` 等）
- `validate_machine_id` の境界確認（`providers/local.py:59` 既存実装）（`pc1` ✓、`PC1` ✗、`pc-1` ✗、17 文字 ✗、空 ✗、`a` ✓）
- `socket.gethostname()` sanitize の純粋関数（大文字 → 小文字、記号除去、16 文字切り詰め、空文字 → 空文字）

### Medium テスト

- **`kaji local init`** の正常系（fresh repo で成功 + machine_id 候補がホスト名 sanitize 済 + `.kaji/config.local.toml` に `[provider] type = "local"` + `[provider.local] machine_id` + `[provider.local] default_branch` が書かれる + `.gitignore` 追記 + `.kaji/config.toml` は touch されない）
- **`kaji local init`** の overlay 仕様確認（生成された `.kaji/config.local.toml` を kaji が読んで provider=local に切り替わること）
- **`kaji local init --machine-id PC1`** が exit 2（`validate_machine_id` 経由）
- **`kaji local init --machine-id pc-1`** が exit 2（hyphen 拒否）
- **`kaji local init`** 2 回目実行が exit 3 で abort、既存 `.kaji/config.local.toml` を上書きしない
- **`kaji local init`** の machine_id 重複警告（既存 `local-pc1-3` ディレクトリがある状態で `--machine-id pc1` 指定 → stderr に WARN、exit 0）
- **`kaji local init --default-branch develop`** が overlay の `[provider.local] default_branch = "develop"` に書かれる
- **`kaji local init`** の `--non-interactive` で stdin を読まないこと
- **`feature-development-local.yaml`** が `kaji validate` を通る
- **Skill 静的検証**（旧 placeholder ゼロ件 hyphen + 山括弧両形式、`provider_type` を使う Skill が分岐セクションを持つこと、`issue-close` SKILL.md が design.md L972-996 の 6-step キーワード `Preflight check` / `Base branch` / `merge --no-ff` / `kaji issue close` / `git worktree remove` を含むこと）
- **`issue-close` の frontmatter 更新** — `kaji issue close local-pc1-1` で frontmatter `state: closed` / `closed_at` / `close_reason: completed` / `closed_by: pc1` が書かれること（**`--reason` 未指定時の default が `"completed"` に変わったことを構造で確認**）
- **`LocalProvider.close_issue(reason="not-planned")`** が `close_reason: "not-planned"` を書き、明示値が default に上書きされないこと
- **`LocalProvider.close_issue(reason=None)` / `reason=""`** が `close_reason: "completed"` を書くこと
- **`IssueContext.default_branch` の provider 別供給** — LocalProvider は `provider.local.default_branch`、GitHubProvider は `provider.github.default_branch` から流すこと
- **dev repo dogfooding**（`.kaji/config.toml` に `[provider.type = "github"]` + `[provider.github] repo = "apokamo/kaji"` + `default_branch = "main"` がある状態で provider 解決が GitHubProvider に解決され、`.kaji/config.local.toml` overlay があれば LocalProvider に切り替わること）

### Large（Phase 3-d では smoke のみ、本格は Phase 3-e）

- `kaji local init` → `kaji issue create` → `kaji issue list` を fresh tmp repo で subprocess 実行。issue が `local-<machine>-1-<slug>/` に作られることを確認
- `feature-development-local.yaml` の **kaji run smoke**（agent は mock または最初の design step のみ起動）

E2E full workflow（design → implement → close）の subprocess 完走は **Phase 3-e の Large-local** で実施する。本 Phase では smoke 範囲。

### 既存テストの維持

- preflight で追加された `tests/test_phase3d_preflight.py` の 48 件全パス
- Phase 3-c の dispatcher テスト全パス
- Skill 静的検証は新規 `tests/test_phase3d_skills.py` に追加（Phase 2-B の grep 検証と同形式）

## 影響ドキュメント

| ドキュメント | 影響 | 対応 |
|--------------|------|------|
| `draft/design/local-mode/phase3-design.md` | 一部 | Phase 3-d 範囲完了の反映、オープン論点（`--force` flag、`default_branch` placeholder 化）の追記 |
| `draft/design/local-mode/design.md` | なし | 親設計の方針はそのまま |
| `docs/cli-guides/local-mode.md` | 新規 | `kaji local init`、`provider` config schema、ID 文法（`local-<machine>-<n>`）、local workflow の最小ガイド |
| `docs/dev/workflow_guide.md` | 軽微 | local workflow の存在を index に追加 |
| `docs/CHANGELOG`（または PR description） | あり | `kaji local init` 追加、`feature-development-local.yaml` 追加、Skill placeholder 統一の user-visible change を記録 |
| `.claude/skills/_shared/implement-by-type/*.md`（mapping table） | 確認のみ | `_mappings.LABEL_TO_PREFIX` が正本であることを skill 側 markdown に注記する（既存表を消さない） |

`docs/cli-guides/local-mode.md` のドラフトは本 Phase で **最小**（インストール + 初期化 + workflow 起動 + ID 形式）を書く。Large-local テストや Phase 3-e fail-fast の説明は Phase 3-e に持ち越す。

## 受け入れ条件

### 機械検証

- [ ] `ruff check kaji_harness/ tests/` PASS
- [ ] `ruff format --check kaji_harness/ tests/` PASS
- [ ] `mypy kaji_harness/` PASS
- [ ] `pytest` PASS
- [ ] `kaji validate .kaji/wf/feature-development-local.yaml` 0 件警告で通過
- [ ] `kaji local init` が fresh tmp repo で `.kaji/config.local.toml` を生成（`[provider] type = "local"` + `[provider.local] machine_id` + `default_branch` 全 3 値が含まれる）し、`.gitignore` に `.kaji/config.local.toml` 行を追記、`.kaji/config.toml` は touch されない
- [ ] `kaji local init --machine-id PC1` が exit 2（`validate_machine_id` 経由で大文字拒否）
- [ ] `kaji local init --machine-id pc-1` が exit 2（hyphen 拒否）
- [ ] `kaji local init` 2 回目実行が exit 3 で abort し、既存 `.kaji/config.local.toml` を上書きしない
- [ ] `kaji local init --default-branch develop` で overlay に `[provider.local] default_branch = "develop"` が書かれる
- [ ] `grep -rE '\[worktree-absolute-path\]|\[branch-name\]|\[design-path\]|\[issue-input\]' .claude/skills/` が 0 件
- [ ] `grep -rE '<worktree-absolute-path>|<branch-name>|<design-path>|<issue-input>' .claude/skills/` が 0 件（回帰検知）
- [ ] `prompt.py` が `IssueContext` 由来で `provider_type` および `default_branch` を Skill prompt に注入する
- [ ] `IssueContext.default_branch` が LocalProvider 経路で `provider.local.default_branch`、GitHubProvider 経路で `provider.github.default_branch` から供給される
- [ ] `.claude/skills/issue-close/SKILL.md` に `[provider_type]` 分岐、`[default_branch]`、design.md L972-996 の 6-step（`Preflight check` / `Base branch` / `merge --no-ff` / `kaji issue close [issue_id] --reason completed` / `git worktree remove` / `git push origin [default_branch]`）が含まれる
- [ ] `LocalProvider.close_issue(reason=None)` が `close_reason: "completed"` を書く（default 修正の確認）
- [ ] `LocalProvider.close_issue(reason="not-planned")` は `close_reason: "not-planned"` を書く（明示値の優先）
- [ ] dev repo `.kaji/config.toml` に `[provider.type = "github"]` + `[provider.github] repo = "apokamo/kaji"` + `default_branch = "main"` が存在
- [ ] dev repo の `.kaji/config.toml` の `provider.github.repo` 値が `git remote get-url origin` から導出した `apokamo/kaji` と一致
- [ ] `git ls-files .kaji/config.local.toml` が空（gitignored 確認）
- [ ] `.gitignore` に `.kaji/config.local.toml` 行が含まれる
- [ ] Phase 3-c の `tests/test_phase3c_*.py` 全件 PASS
- [ ] preflight の `tests/test_phase3d_preflight.py` 全件 PASS

### 手動確認

- [ ] dev repo で `kaji local init` → `.kaji/config.local.toml` 生成 → `kaji issue create --title "Phase 3-d smoke"` → `.kaji/issues/local-<machine>-1-phase-3-d-smoke/issue.md` が作られる
- [ ] 上記 issue に対し `kaji run .kaji/wf/feature-development-local.yaml local-<machine>-1` を起動できる（design step が走り出す段階で stop してよい）
- [ ] `.kaji/config.local.toml` を削除すると dev repo は `[provider.type = "github"]` 経路に戻る（overlay 仕様の往復確認）
- [ ] `issue-close` Skill markdown を読んだ AI が provider=local 経路で design.md L972-996 の 6-step（preflight → base 更新 → merge → frontmatter commit → cleanup → push）を踏む判断ができる（実行は手動でも skill prompt の判断ロジックは markdown 上で完結している）

### ドキュメント

- [ ] `docs/cli-guides/local-mode.md` ドラフト作成（最小: install / init 上書き仕様 / config schema / overlay 仕様 / workflow / ID 文法）
- [ ] `phase3-design.md` § `kaji local init` 仕様 Step 4-5 に「本書の仕様は `phase3d-design.md` § 3 で上書きされた」cross-reference を追記
- [ ] `phase3-design.md` § オープンな論点 から `[default_branch]` placeholder 化を削除（本 Phase で実装された）し、Phase 3-e で扱う `config.py` 側 fail-fast validation を新規 open 論点として追記
- [ ] CHANGELOG エントリまたは PR description で user-visible change を列挙（`kaji local init` 上書き仕様、新規 placeholder 2 件、`GitHubProviderConfig.default_branch` 追加）

## Rollback 方針

| 変更 | rollback |
|------|----------|
| `feature-development-local.yaml` 追加 | ファイル削除のみ |
| `IssueContext.default_branch` / `GitHubProviderConfig.default_branch` 追加 | dataclass field の default 値が後方互換のため revert で完全戻し可能 |
| `kaji local init` CLI | dispatcher と `local_init.py` を revert |
| `prompt.py` の `provider_type` / `default_branch` 注入 | 2 行追加なので revert で済む。Skill markdown 側に未注入 placeholder が残るだけ |
| Skill placeholder 統一 | `git revert` で旧 hyphen 形式に戻る。grep 検証も復元する必要あり |
| `issue-close` 6-step 反映 + `close_issue` default `"completed"` 化 | revert で github 単一経路と空文字 default に戻る。local mode で実用不可になるが Phase 3-e まで運用 OK |
| dev repo `.kaji/config.toml` の `[provider]` 追記 | revert で legacy fallback 復活。Phase 3-e fail-fast 直前にもう一度入れ直す必要あり |
| `.gitignore` 追記 | revert |

各 commit 単位で revert 可能な粒度を維持する。Phase 3-e の fail-fast 化に進む前に dev repo dogfooding の commit 9 が main に入っていることが必要条件。

## 判断済み論点

実装担当者が再判断しなくてよい論点を明示する。

| 論点 | 判断 | 根拠 |
|------|------|------|
| Skill placeholder の hyphen vs underscore | underscore に統一（`[branch_name]` 等） | Phase 2-B の `[issue_id]` / `[issue_ref]` と整合、Python identifier として自然 |
| 山括弧形式 `<branch-name>` の置換 | 対象ゼロ件、touch 不要。forbidden list と回帰検知のため grep には残す | preflight 後の grep で 0 件確認 |
| 21 ファイル一括 commit vs グループ分割 | 3 グループ（A: issue-* / B: i-doc-* / C: pr-*）に分割 | レビュー単位の保全、誤置換時の影響面限定 |
| `provider_type` 注入の場所 | `prompt.py` で `IssueContext.provider_type` を flatten | `IssueContext` field は preflight 時点で存在、Skill 改修は markdown 側のみで完結 |
| `[default_branch]` placeholder 化 | **本 Phase で `IssueContext.default_branch` 追加 + `prompt.py` 注入を実装**（前版「main 固定」判断を撤回） | `provider.local.default_branch` config / `kaji local init --default-branch` / `LocalProvider.default_branch` が既に存在し、Skill 側 `main` 固定では `--default-branch develop` 指定時に merge 先が壊れる |
| `provider.github.default_branch` config field 追加 | 本 Phase で追加（default `"main"`） | `IssueContext.default_branch` を GitHub 経路でも供給する必要があり、`gh repo view` subprocess は buildout 中疎通不可で採用不可 |
| `issue-close` Skill の分岐実装 | 単一 markdown 内で `[provider_type]` 分岐 + design.md L972-996 の 6-step を local 経路として完全反映 | workflow yaml の `skill: issue-close` を 1 名で維持、provider 別 yaml 分岐を作らない、design.md の安全条件（preflight / base 更新 / cleanup 順序 / push）を欠落させない |
| `LocalProvider.close_issue` の default reason | **本 Phase で `"completed"` に変更**（旧: `reason or ""` で空文字）、Skill markdown でも `--reason completed` を明示 | design.md L985 / 状態遷移図 L1011-L1015 が `close_reason: completed` を要求、CLI 単体使用時の罠（空文字書き込み）を構造で解消、GitHub Issue API default と整合 |
| `kaji local init` の生成位置 | **active provider 値（`type` / `machine_id` / `default_branch`）はすべて `.kaji/config.local.toml` (gitignored) に書く**。phase3-design.md § Step 4-5 を本書 § 3 で上書き | 個人選択を tracked file に commit しない、clone 時の壊れた default を回避、復旧経路を常に保つ |
| dev repo の commit 値 | `.kaji/config.toml` に `[provider.type = "github"]` + `[provider.github] repo = "apokamo/kaji"` + `default_branch = "main"`、active local 試運用は overlay | GitHub 復旧後の通常運用継続を阻害しない、buildout 中も `kaji local init` 1 コマンドで overlay 生成 |
| `validate_machine_id` の所在 | 既存 `providers/local.py:59` を `kaji local init` から explicit に呼ぶ。`config.py` への統合は Phase 3-e で別途検討 | regex 検証は preflight 時点で実装済、`config.py:219-221` は isinstance のみで Phase 3-d 時点では unchanged のまま運用、二重 validation の追加コストを避ける |
| Skill mapping table の削除 | 削除しない、`_mappings.py` が正本である注記のみ | 既存 markdown 上の human-readable table を docs として温存 |
| commit 順序 | 副作用小から大へ（workflow yaml → IssueContext / config 拡張 → CLI → prompt → Skill A/B/C → close 6-step → dev config） | 各 commit で make check 緑、独立 revert |
| Large-local E2E の本 Phase 担当 | smoke のみ（init + create + run 起動）、full E2E は Phase 3-e | Phase 3-d スコープ膨張の防止、Phase 3-e fail-fast の入口要件として配置 |

## 参照情報（Primary Sources）

| 情報源 | パス / URL | 根拠 |
|--------|------------|------|
| Phase 3 設計 | `draft/design/local-mode/phase3-design.md` | `kaji local init` 仕様（§ `kaji local init` 仕様、Step 4-5 は本書 § 3 で上書き）、`feature-development-local.yaml` 全文（§ `feature-development-local.yaml` step 構成）、Skill 21 ファイル特定（§ Skill 改修対象範囲）、PR-3d 出口要件（§ ロールアウト戦略） |
| 親設計（local /issue-close 6-step） | `draft/design/local-mode/design.md` L972-996 | local mode の `/issue-close` 6 step（preflight / base 更新 / merge / frontmatter commit / cleanup / push）の正本仕様 |
| 親設計（状態遷移） | `draft/design/local-mode/design.md` L985, L1011-L1017 | `close_reason: completed` / `not-planned` の 2 値仕様。`LocalProvider.close_issue` default を `"completed"` に修正する根拠 |
| 現行 close 実装 | `kaji_harness/providers/local.py:611-625` | preflight 時点の `close_issue` 実装（L623 `meta["close_reason"] = reason or ""` の空文字 default が design.md と矛盾） |
| Phase 3-d preflight 設計 | `draft/design/local-mode/phase3d-preflight-design.md` | canonical id / PyYAML / slug optional / O_EXCL comment / Python jq の決定根拠 |
| Phase 3-d preflight 実装報告 | `draft/design/local-mode/phase3d-preflight-implementation-report.md` | 実装済 helper（`validate_branch_prefix`, `derive_slug_from_title`, `RunIssueContext`, `validate_machine_id`）の所在と署名 |
| Phase 3-c 実装報告 | `draft/design/local-mode/phase3c-implementation-report.md` | dispatcher 切替、`get_provider` factory、`prompt.build_prompt` シグネチャ、config overlay（`[provider]` section 全体上書き） |
| Phase 2-B 実装報告 | `draft/design/local-mode/phase2b-implementation-report.md` | `[issue_id]` / `[issue_ref]` placeholder 体系、Skill 静的検証 grep の前例 |
| 現行 prompt | `kaji_harness/prompt.py` | 現行注入変数 7 個（issue_id / issue_ref / 5 変数）、`IssueContext is None` fallback |
| 現行 IssueContext | `kaji_harness/providers/models.py:96` | `provider_type: str` field（既存）、`default_branch` field は本 Phase で追加 |
| 現行 LocalProvider | `kaji_harness/providers/local.py:59` (`validate_machine_id`)、L312 (`default_branch`) | preflight 後の frontmatter / slug / comment 実装、machine_id 検証、default_branch field |
| 現行 GitHubProvider | `kaji_harness/providers/github.py` | `[provider.github] repo` の使用箇所 |
| 現行 config | `kaji_harness/config.py:35-36` (`LocalProviderConfig`)、L219-225 (validation) | `LocalProviderConfig.machine_id` / `default_branch` 既存、`isinstance` のみで regex 検証なし |
| 現行 dev repo config | `.kaji/config.toml` | `[provider]` セクション未追加 |
| 現行 .gitignore | `.gitignore` | `.kaji/config.local.toml` 行未追加 |
| 現行 origin URL | `git remote get-url origin` 出力 | `https://github.com/apokamo/kaji.git`（実装担当者は本書 commit 9 時点で再確認） |
| Skill 群 | `.claude/skills/` | 21 ファイルの placeholder 分布（grep 確定済、山括弧形式は 0 件） |

## 完了条件の段階確認

- [x] Phase 3-c + preflight の到達点を本書 § 背景・目的 に明示した
- [x] Phase 3-d で実装する 9 項目を § スコープ in-scope に列挙した
- [x] Skill 21 ファイルの placeholder 統一を 3 グループ × 旧→新 mapping table で確定した
- [x] `provider_type` / `default_branch` placeholder 注入の最小変更経路を § 方針決定 § 2 で示した
- [x] `kaji local init` の生成位置を本書 § 3 で上書きし、active provider 値は overlay 側に書く正本を確定した
- [x] dev repo dogfooding 戦略を `.kaji/config.toml` commit (`type = "github"`) + `.kaji/config.local.toml` overlay (`type = "local"`) で固定した
- [x] PR-3d 内の commit 順序を 9 commit に分割し、副作用小から大へ並べた
- [x] `kaji local init` の preflight 整合点（`socket.gethostname` sanitize、`validate_machine_id` の所在、glob による machine_id 抽出）を確定した
- [x] `issue-close` Skill の local 経路を design.md L972-996 の 6 step（preflight / base 更新 / merge / frontmatter commit / cleanup / push）に完全反映する markdown ドラフトを示した
- [x] Step 4 で `kaji issue close [issue_id] --reason completed` を明示し、`LocalProvider.close_issue` の default reason を `"completed"` に変更する設計を確定した
- [x] `IssueContext.default_branch` 追加と `GitHubProviderConfig.default_branch` 追加を本書で確定した
- [x] 受け入れ条件を機械検証 / 手動確認 / ドキュメントの 3 区分で列挙した
- [x] 判断済み論点を 13 項目で明示し、実装担当者の再判断負荷を軽減した
- [x] 参照情報を Primary Source として 14 項目列挙した
