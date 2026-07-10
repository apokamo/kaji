---
status: draft
phase: 3d-preflight
parent: phase3-design.md
created: 2026-05-06
---

# [設計] kaji local mode — Phase 3-d preflight hardening

Issue: TBD（local-mode buildout 期間中の補正設計。GitHub 復旧後に該当 Issue へ紐付ける）

## 概要

Phase 3-c 完了後レビューで見えた provider / local mode の契約不整合を、Phase 3-d
本体（`kaji local init`、local workflow、Skill 5 変数移行）へ入る前に補正する。

本設計は他の担当者が実装する前提の handoff 文書である。実装担当者は本書の
「方針決定」「実装範囲」「受け入れ条件」を正本として扱い、既存 Phase 3 設計書と
矛盾する場合は本書の Phase 3-c 後補正を優先する。

## 背景・目的

Phase 3-c までに以下は完了している。

- `kaji_harness/providers/` package と `IssueProvider` / `IssueContext` の導入
- `LocalProvider` / `GitHubProvider` の Issue CRUD と context 解決
- `kaji issue` dispatcher の provider 切替
- `kaji run` で `IssueContext` を解決し、`issue_input` / `branch_prefix` /
  `branch_name` / `worktree_dir` / `design_path` を prompt に注入する経路

一方で、Phase 3-d / 3-e に進む前に潰すべき土台リスクが残っている。

| リスク | 影響 |
|--------|------|
| `kaji run ... 1` と `kaji run ... local-pc1-1` が state/artifacts 上で別物になる | 同一 Issue の session resume / progress が分裂する |
| local `--jq` が system `jq` に依存している | fresh install で Skill の中核コマンドが失敗する |
| frontmatter parser が自前簡易実装 | 人間編集した YAML や inline list / null が壊れる |
| `kaji issue create --slug` 必須 | local-first の Issue 起票が Skill / user に余計な責務を持たせる |
| comment seq が lock されていない | 同一 machine の並列 comment で上書きが起きうる |

この補正を Phase 3-d 本体に混ぜると、`kaji local init` / Skill 移行 / local workflow
追加と同時に基盤契約も動き、レビューの焦点がぼやける。したがって、Phase 3-d
preflight として先に小さく切る。

## スコープ

### in-scope

1. `kaji run` の canonical issue id 確定と state/log/prompt への一貫適用
2. local provider の `--jq` 実装を package dependency ベースに戻す
3. LocalProvider frontmatter の parser / serializer を PyYAML ベースへ移行
4. local `kaji issue create` の `--slug` を optional 化し、未指定時は title 由来で生成
5. local comment 書き込みの上書き競合を防ぐ
6. 上記に対する Small / Medium テストの追加
7. Phase 3-c 実装報告と Phase 3 設計書の該当箇所の補正

### out-of-scope

| 項目 | 後続 |
|------|------|
| `kaji local init` 実装 | Phase 3-d |
| `.kaji/config.toml` への `[provider]` 追記 / `.gitignore` 更新 | Phase 3-d |
| `feature-development-local.yaml` 追加 | Phase 3-d |
| Skill の 5 変数移行 | Phase 3-d |
| `provider.type` 未設定 fallback の削除 | Phase 3-e |
| `kaji pr` の bare provider エラー化 | Phase 4 |
| `kaji sync from-github` | Phase 5 |

## 方針決定

### 1. canonical issue id は `kaji run` 起動時に確定する

#### 問題

`WorkflowRunner._resolve_issue_context()` は `normalize_id()` を通して `1` /
`pc1-1` / `local-pc1-1` を同一 `IssueContext` に解決する。一方で
`SessionState.load_or_create(self.issue_number, ...)`、run log path、完了メッセージは
正規化前の入力値を使っている。

このため provider=local で以下が分裂する。

```bash
kaji run .kaji/wf/feature-development-local.yaml 1
kaji run .kaji/wf/feature-development-local.yaml local-pc1-1
```

両者は同じ Issue を指すが、`.kaji-artifacts/1/` と
`.kaji-artifacts/local-pc1-1/` の別 state を使ってしまう。

#### 決定

`WorkflowRunner` は run 開始時に `RunIssueContext`（仮称）を 1 度だけ解決し、以降の
state / run log / prompt / success summary は canonical id を使う。

```python
@dataclass(frozen=True)
class RunIssueContext:
    input_id: str
    canonical_id: str
    issue_ref: str
    issue_context: IssueContext | None
```

挙動:

| provider | 入力 | canonical_id | issue_context |
|----------|------|--------------|---------------|
| 未設定 fallback | `42` | `42` | `None` |
| github | `153` | `153` | GitHub `IssueContext` |
| github | `gh:153` | `153` | GitHub `IssueContext` |
| local | `1` | `local-<machine_id>-1` | Local `IssueContext` |
| local | `pc1-1` | `local-pc1-1` | Local `IssueContext` |
| local | `local-pc1-1` | `local-pc1-1` | Local `IssueContext` |
| local | `gh:153` | reject | `kaji run` 対象として不可 |

`WorkflowRunner.issue_number` は後方互換名として残してよいが、内部では
`canonical_issue_id` を導入する。新規コードでは `issue_number` を増やさない。

#### 実装方針

- `WorkflowRunner.run()` の early phase で `_resolve_run_issue_context()` を呼ぶ
- `SessionState.load_or_create()` には canonical id を渡す
- `run_dir` は `artifacts_dir / canonical_id / "runs" / ...`
- `RunLogger.log_workflow_start()` も canonical id を渡す
- `build_prompt()` の `issue` 引数も canonical id を渡す
- `WorkflowRunner.canonical_issue_id: str | None` と `WorkflowRunner.canonical_issue_ref:
  str | None` を追加し、`run()` 起動後に外部から参照可能にする
- `cmd_run()` の成功表示は `runner.canonical_issue_ref` を使う。`None` の場合のみ
  legacy fallback として raw `args.issue` を整形する

`[provider]` 未設定 fallback の場合だけ、現在の Phase 2-B 互換動作として入力値を
canonical id とする。

#### 既存 state / artifacts の扱い

preflight 適用前に `kaji run ... 1` を実行済みの場合、既存 state は
`<artifacts_dir>/1/` に存在しうる。preflight 後の canonical path は
`<artifacts_dir>/local-pc1-1/` になるため、その state は自動では読まれない。

本 preflight では **自動 migration はしない**。理由は、raw id と canonical id の対応は
provider config / machine_id に依存し、agent が暗黙に move / copy すると別 Issue の state を
混ぜる事故が起きうるため。

代わりに以下を実装する。

- `raw_issue_id != canonical_id`
- `<artifacts_dir>/<raw_issue_id>/` が存在する

この条件を満たす場合、起動時に WARN を出す。

```text
WARNING: legacy artifact directory exists for raw issue id '1':
  .kaji-artifacts/1
This run will use canonical issue id 'local-pc1-1':
  .kaji-artifacts/local-pc1-1
If you need to resume the old session, move the directory manually after
confirming it belongs to the same issue.
```

`SessionState.load_or_create()` に fallback 探索は入れない。migration helper が必要になった場合は
Phase 3-d 後に別 Issue とする。

### 2. local `--jq` は Python package dependency で実装する

#### 問題

Phase 3-c の `_apply_jq()` は system `jq` バイナリに委譲している。これは
fresh install で保証されない。`pyproject.toml` の runtime dependency にも
記録されないため、local mode の中核 CLI が環境依存になる。

既存 Skill は以下の形に依存している。

```bash
kaji issue view [issue_id] --json body -q '.body'
```

このコマンドが fresh install で動かないと、local mode の主要 workflow は成立しない。

#### 決定

Phase 3-c rev #2 の system `jq` 採用判断を維持せず、PyPI `jq` package を runtime
dependency に追加する。
LocalProvider の `--jq` / `-q` は Python `jq` package で評価し、`gh --jq` 互換の
raw output を post-processing で再現する。

```toml
[project]
dependencies = [
    "pyyaml>=6.0",
    "jq>=1.6",
]
```

system `jq` バイナリは不要にする。`shutil.which("jq")` による runtime check も撤去する。

#### Phase 3-c rev #2 への回答

Phase 3-c rev #2 は「GitHub provider は `gh --jq` に依存し、local provider は system
`jq` に依存するので対称」と説明していた。この対称性は、**forge provider と bare provider の
要求水準が異なる**ため採用しない。

- `provider=github` の user は GitHub CLI を使うため、`gh` は機能そのものの前提である
- `provider=local` は GitHub 非依存 / local-first を目的にしているため、追加の OS package
  install を必須にすると自己完結性が下がる
- `jq` を Python dependency に入れれば `uv sync` / package install の責務に含められる
- system `jq` 不在環境でも Skill の中核である `kaji issue view ... -q '.body'` が動作する

ただし PyPI `jq` にも C extension / wheel availability のリスクはある。Phase 3 では Linux /
macOS を主要対象、Windows は既に local mode の locking を暫定扱いとしているため、このリスクは
許容する。Windows install が実際に詰まる場合は、後続で pure Python alternative または
system `jq` fallback を検討する。

#### 出力契約

`gh --jq` 互換 subset として以下を保証する。

| jq 結果 | 出力 |
|---------|------|
| string | raw string + newline |
| string 内の改行 | 文字列中の改行をそのまま出力し、最後に newline を 1 つ追加 |
| number | decimal string + newline |
| boolean | `true` / `false` + newline |
| null | 空行 |
| object / array | compact JSON + newline |
| stream | 各結果を newline 区切り |
| stream 内の null | 空行として出力（例: `1`, `null`, `2` → `1\n\n2\n`） |
| empty stream | stdout なし、exit 0 |

`jq.compile(expr).input_value(data).all()` の結果配列に対して整形する。

syntax error / runtime error は user 入力の jq 式または jq 実行時の問題として
`EXIT_RUNTIME_ERROR`（3）を返す。stderr には jq package 由来の例外メッセージを
user-facing に整形して出す。

`jq -r` を bit-exact 正本として扱う。Python `jq` package と `jq -r` の差異が見つかった場合は、
Skill が依存する式（`.body`, `.title`, `.labels[].name`, object projection）を優先して
互換 post-processing を追加する。

#### 不採用案

| 案 | 不採用理由 |
|----|------------|
| system `jq` 継続 | runtime dependency として表現できず、fresh install で壊れる |
| `gh --jq` を local でも呼ぶ | local provider から GitHub CLI 依存を排除する目的に反する |
| 独自 jq subset parser | jq 式の互換性を自前で抱えるべきではない |

### 3. frontmatter は PyYAML で扱う

#### 問題

`LocalProvider` は自前の `_parse_frontmatter()` / `_serialize_frontmatter()` を持つ。
現状の parser は serializer が出す限定 YAML には概ね対応するが、以下に弱い。

- `labels: [type:feature, area:harness]` の inline list
- `closed_at: null`
- 人間編集で入る quote / colon / nested scalar
- 将来の `assignees`, `created_by`, `updated_at`, `migrated_to` 等

`pyyaml>=6.0` はすでに runtime dependency にあるため、自前 parser を持つ理由は弱い。

#### 決定

frontmatter parser / serializer は PyYAML に移行する。

実装方針:

- frontmatter の抽出は既存 `_FRONTMATTER_RE` 相当で `---` block と body を分ける
- YAML 部分は `yaml.safe_load()` を使う
- `safe_load()` の戻り値が `None` の場合は `{}`
- 戻り値が mapping 以外なら `LocalProviderError`
- serializer は `yaml.safe_dump(..., allow_unicode=True, sort_keys=False)` を使う
- body は frontmatter 後にそのまま保持する

`_serialize_frontmatter()` / `_parse_frontmatter()` の関数名はテスト互換のため残してもよい。
ただし実装は PyYAML に委譲する。

#### 読み取り時 validation

LocalProvider は人間編集された file を読むため、create 時だけでなく read / context 解決時も
validation する。

必須:

- `id` が `local-<machine>-<n>` に一致する
- directory 名と `id` が対応している
- `state` は `open` / `closed`
- `slug` がある場合は `validate_slug(slug)` を通る
- `branch_prefix` がある場合は branch 名として許容する文字だけに制限する

`resolve_issue_context()` では `slug` が必須。`view_issue()` は legacy / migration 用に
slug 不在でも表示可能としてよい。

#### legacy frontmatter / validation 方針

読み取り対象の frontmatter は人間編集されるため、全フィールドを一律 strict にしない。
Phase 3-d preflight での validation は以下に限定する。

| field | `view_issue()` | `resolve_issue_context()` / write 系 | 理由 |
|-------|----------------|--------------------------------------|------|
| `id` | 不正なら fail-fast | 不正なら fail-fast | Issue identity の正本 |
| `state` | `open` / `closed` 以外は fail-fast | 同左 | list / close の分岐に使う |
| `labels` | `list[str]` / `list[dict]` 以外は fail-fast | 同左 | jq 互換出力に必要 |
| `slug` | 不在は許容、不正値は fail-fast | 不在 / 不正値とも fail-fast | context / design_path に必要 |
| `branch_prefix` | 不在は許容、不正値は fail-fast | 同左 | branch_name に使う |
| その他 | 解釈せず保持 | 解釈せず保持 | `migrated_to` 等の user 拡張を許容 |

`branch_prefix` は `_mappings.py:LABEL_TO_PREFIX` の values
（`feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `security`）に限定する。
任意 regex は採用しない。理由は、`branch_prefix` は kaji の workflow type と branch 命名規約の
一部であり、自由入力にすると path / branch policy を provider 外へ漏らすため。custom label 体系を
許可する場合は、後続で mapping の config 化を検討する。

PyYAML 移行後は byte-for-byte round-trip を保証しない。保証するのは semantic round-trip
（同じ field/value/body を読めること）である。safe_dump による quote / inline-list の表記変化は
許容する。

### 4. local `kaji issue create --slug` は optional にする

#### 問題

Phase 3-c では local `kaji issue create` に `--slug` が必須である。一方、既存
`issue-create` Skill は GitHub 互換の以下の形で起票する。

```bash
kaji issue create --title "[title]" --body "[body]" --label "[label]"
```

Phase 3-d で Skill を直すとしても、slug を user / Skill の必須入力にする必要性は低い。
GitHubProvider は title から slug を導出しているため、local でも同じ fallback を持つ方が
provider 中立性が高い。

#### 決定

local `kaji issue create` は `--slug` を optional override にする。

| 入力 | slug |
|------|------|
| `--slug foo-bar` 指定 | `foo-bar` を validate して使用 |
| `--slug` 未指定 | `derive_slug_from_title(title)` を使用 |
| title 由来 slug が空 | `untitled` |

同じ machine_id で同名 slug が複数あっても、directory は `<id>-<slug>` なので衝突しない。

#### 互換性

`--slug` 指定済の Phase 3-c テストはそのまま通す。新たに未指定のテストを追加する。
`LocalProvider.create_issue(slug=None)` は title から導出するよう変更する。

### 5. comment 書き込みは上書きしない filename 戦略にする

#### 問題

現状の comment filename は `comments/<seq>-<machine>.md` であり、`seq` は
既存ファイルの max + 1 で決める。comment 書き込み時に lock が無いため、同一 machine
上で並列 comment が走ると両方が同じ filename を選ぶ可能性がある。

#### 候補

| 案 | 内容 | 評価 |
|----|------|------|
| A. issue 単位 lock | `_next_comment_seq()` と write を lock で囲む | 既存 filename 維持、実装中 |
| B. timestamp + random suffix | `20260506T120000Z-pc1-<rand>.md` | 上書きしにくいが既存 seq 契約を捨てる |
| C. `open(..., "x")` retry | seq を試し、存在したら再読込して retry | lock なしで安全、既存 filename 維持 |

#### 決定

案 C を採用する。理由は、既存の `0001-pc1.md` 形式を維持しつつ、同一 process /
別 process の競合でも上書きしないため。

retry 上限は `MAX_COMMENT_WRITE_RETRIES = 8` とする。8 回連続で競合した場合は
`LocalProviderError` で fail-fast する。

実装方針:

```python
for _ in range(MAX_COMMENT_WRITE_RETRIES):
    seq = self._next_comment_seq(issue_dir)
    path = cdir / f"{seq}-{self.machine_id}.md"
    try:
        _atomic_write_new(path, content)  # existing path なら FileExistsError
        return comment
    except FileExistsError:
        continue
raise LocalProviderError("failed to allocate unique comment filename ...")
```

`_atomic_write_new()` は `os.open(path, O_CREAT | O_EXCL | O_WRONLY)` で最終ファイルを
exclusive に作り、bytes を write して close する。`path.open("x")` は buffering / kill 時の
0 byte file 懸念があるため採用しない。

既存 `_atomic_write()` は edit / close の上書き用として残す。`create_issue()` の `issue.md`
初回書き込みは counter lock と issue directory の `mkdir` 衝突検出で守られるため、本 preflight では
変更しない。

## インターフェース

### CLI

変更される user-facing CLI:

```bash
# before: --slug required
kaji issue create --title TEXT (--body TEXT | --body-file PATH) [--label LABEL]... [--slug SLUG]
```

`--slug` 未指定時の挙動が追加されるだけで、既存の指定済呼び出しは互換。

`kaji issue view/list --jq/-q` の CLI surface は変えない。内部実装だけを system `jq`
から Python package に置き換える。

### Python internal

追加または変更予定:

```python
@dataclass(frozen=True)
class RunIssueContext:
    input_id: str
    canonical_id: str
    issue_ref: str
    issue_context: IssueContext | None
```

`WorkflowRunner` 内部に閉じた DTO としてよい。public API として export しない。

`LocalProvider.create_issue()` は以下のように変更する。

```python
def create_issue(
    self,
    *,
    title: str,
    body: str,
    labels: list[str] | None = None,
    slug: str | None = None,
) -> Issue:
    ...
```

signature は既にこの形なので、`slug is None` の挙動だけ変更する。

## 実装範囲

### 変更対象ファイル

| ファイル | 変更 |
|----------|------|
| `pyproject.toml` | `jq>=1.6` を runtime dependency に追加 |
| `kaji_harness/cli_main.py` | `_apply_jq()` を Python `jq` package 実装へ変更、local create の `--slug required` を解除、success summary を canonical ref 表示に変更 |
| `kaji_harness/runner.py` | canonical issue id 解決を state / log / prompt に適用、`canonical_issue_id` / `canonical_issue_ref` を post-run 参照可能にする |
| `kaji_harness/state.py` | 必要なら `_format_issue_ref` の provider/context 側 helper との重複を整理。少なくとも canonical id が渡る前提のテストを追加 |
| `kaji_harness/providers/local.py` | PyYAML frontmatter、slug default、comment write retry |
| `kaji_harness/providers/context.py` | 必要なら branch_prefix validation helper を追加 |
| `tests/test_phase3c_runner.py` | canonical id と state path の回帰テスト |
| `tests/test_phase3c_dispatcher.py` | local create slug optional、Python jq output の回帰テスト |
| `tests/test_providers_local.py` | PyYAML frontmatter round-trip、comment collision retry |
| `draft/design/local-mode/phase3c-implementation-report.md` | system jq 採用の記述を補正 |
| `draft/design/local-mode/phase3-design.md` | preflight 補正後の方針を反映 |
| `CHANGELOG.md` または PR description | runtime dependency と local create slug optional の user-visible change を記録 |

### 実装順序

1. `jq` dependency 追加と `_apply_jq()` の Python package 化
2. frontmatter parser / serializer の PyYAML 化
3. slug optional 化
4. comment write collision 対策
5. `WorkflowRunner` canonical id 化
6. docs / implementation report 補正
7. `make check`

順序の理由: `jq` / frontmatter / slug / comment は provider 単体で閉じており、
最後に runner の state path 変更を入れるとテスト失敗範囲を切り分けやすい。

## テスト戦略

### 変更タイプ

実行時コード変更。

### Small テスト

- `gh_compatible_jq` helper（名称は実装担当者に任せる）の出力整形
  - string raw
  - string 内 newline
  - number / bool / null
  - object / array compact JSON
  - stream newline 区切り
  - stream 内 null
  - empty stream
  - syntax error / runtime error → exit 3 相当
- `derive_slug_from_title()` を local create 未指定 slug でも使うこと
- title 由来 slug が空になる場合は `untitled`
- branch_prefix validation helper の許容 / 拒否パターン

### Medium テスト

- provider=local で `kaji issue create --title ... --body ...` が `--slug` なしで
  `.kaji/issues/local-pc1-1-<derived>/issue.md` を作る
- `labels: [type:feature, area:harness]` / `closed_at: null` / quote を含む title が
  PyYAML parser で round-trip する
- `resolve_issue_context()` が invalid slug / invalid branch_prefix を fail-fast する
- `comment_issue()` が既存 `0001-pc1.md` と競合した場合に `0002-pc1.md` へ retry する
- comment filename の競合が 8 回続いた場合に `LocalProviderError` で停止する
- `kaji run ... 1` と `kaji run ... local-pc1-1` が同じ artifacts directory /
  `SessionState.issue_number` を使う
- `kaji run ... pc1-1` も同じ canonical id に解決される
- raw id 側の legacy artifacts directory が存在すると WARN を出す
- PATH から system `jq` を見えなくしても local `--jq` が Python package 経由で動く

### Large テスト

本 preflight では必須にしない。理由:

- 外部 API 疎通は含まない
- `feature-development-local.yaml` がまだ Phase 3-d scope
- full local workflow E2E は Phase 3-d / 3-e の Large-local で実施する

ただし、既存 large test がこの変更で壊れないことは `pytest` 全体で確認する。

## 影響ドキュメント

| ドキュメント | 影響 | 理由 |
|--------------|------|------|
| `draft/design/local-mode/phase3-design.md` | あり | `jq` 実装、slug optional、canonical id 方針を反映 |
| `draft/design/local-mode/design.md` | あり | 親設計の `jq` dependency / frontmatter / slug の記述と整合確認 |
| `draft/design/local-mode/phase3c-implementation-report.md` | あり | system jq 採用の判断保留を撤回または補正 |
| `docs/cli-guides/local-mode.md` | 後続 | Phase 3-d で新規作成時に `--slug` optional と jq dependency を記載 |
| `docs/ARCHITECTURE.md` | なし | provider abstraction の大枠は変えない |
| `docs/dev/testing-convention.md` | なし | テスト分類の変更なし |
| `.claude/skills/` | 後続 | Phase 3-d の 5 変数移行で対応。本 preflight では触らない |

## 受け入れ条件

### 機械検証

- [ ] `ruff check kaji_harness/ tests/` PASS
- [ ] `ruff format --check kaji_harness/ tests/` PASS
- [ ] `mypy kaji_harness/` PASS
- [ ] `pytest` PASS
- [ ] `pyproject.toml` に `jq>=1.6` が runtime dependency として存在
- [ ] `kaji issue view 1 --json body -q '.body'` が system `jq` 不在でも動作する
- [ ] `kaji issue view 1 --json body -q '.body'` が body 内 newline を保持して raw 出力する
- [ ] jq empty stream は stdout なし exit 0、jq syntax/runtime error は exit 3
- [ ] `kaji issue create --title "Hello World" --body "..."` が `local-pc1-1-hello-world`
  形式の issue directory を作る
- [ ] `kaji issue create --title "!!!" --body "..."` が `untitled` slug を使う
- [ ] `kaji issue create --title "Hello" --slug custom --body "..."` が explicit slug を優先する
- [ ] `kaji run ... 1` / `kaji run ... pc1-1` / `kaji run ... local-pc1-1` が同じ
  `SessionState.issue_number == "local-pc1-1"` になる
- [ ] raw id 側に既存 artifacts directory がある場合、canonical path を使いつつ WARN を出す
- [ ] inline list frontmatter (`labels: [type:feature]`) を読める
- [ ] frontmatter の byte-for-byte stability ではなく semantic round-trip を検証する
- [ ] comment filename 競合時に既存 file を上書きしない
- [ ] comment filename 競合が retry 上限を超えると fail-fast する

### 手動確認

- [ ] `provider=local` の tmp repo で issue create / view / comment / close が通る
- [ ] `kaji run` の error message が canonical id 解決失敗時に user 入力と provider type を示す
- [ ] `phase3c-implementation-report.md` に残る system jq 前提の記述が補正されている

## Rollback 方針

この preflight は Phase 3-d 前の補正なので、rollback は比較的容易である。

| 変更 | rollback |
|------|----------|
| `jq` dependency | `pyproject.toml` と `_apply_jq()` を Phase 3-c 実装へ戻す |
| PyYAML frontmatter | `_parse_frontmatter()` / `_serialize_frontmatter()` を旧実装へ戻す |
| slug optional | `argparse required=True` と `LocalProvider.create_issue()` の `slug is None` error を戻す |
| comment retry | `comment_issue()` を旧 seq 書き込みへ戻す |
| canonical id | `WorkflowRunner.run()` の state/load/log 引数を旧 `self.issue_number` へ戻す |

ただし rollback は local mode の実用性を下げるため、Phase 3-d へ進む前に本 preflight を
収束させることを優先する。

## 判断済み論点

レビュー時に争点になりやすい論点を、実装前に再判断しなくてよいよう明示する。

| 論点 | 判断 |
|------|------|
| Python `jq` package vs system `jq` | Python `jq` package を採用する。Phase 3-c rev #2 の system `jq` 採用は local mode の自己完結性を弱めるため、本 preflight で上書きする |
| 既存 raw-id artifacts migration | 自動 migration しない。WARN のみ出し、必要なら user が手動移動する |
| frontmatter round-trip | byte-for-byte stability は要求しない。semantic round-trip を保証する |
| explicit `branch_prefix` | `_mappings.py` の既知 prefix values に限定する。任意 regex は採用しない |
| comment retry atomic 戦略 | `os.open(..., O_CREAT | O_EXCL | O_WRONLY)` を採用し、`path.open("x")` は採用しない |
| retry 上限 | `MAX_COMMENT_WRITE_RETRIES = 8` |

## 参照情報（Primary Sources）

| 情報源 | パス / URL | 根拠 |
|--------|------------|------|
| Phase 3 設計 | `draft/design/local-mode/phase3-design.md` | `IssueContext` 採用、Phase 3-d / 3-e rollout、local workflow 前倒し方針 |
| 親設計 | `draft/design/local-mode/design.md` | local provider の CLI 契約、`--jq` は Python `jq` package で gh raw output 互換にする方針 |
| Phase 3-c 実装報告 | `draft/design/local-mode/phase3c-implementation-report.md` | dispatcher 切替、`IssueContext` 注入、system jq 採用の経緯、既知トレードオフ |
| 現行 runner | `kaji_harness/runner.py` | `SessionState.load_or_create(self.issue_number, ...)` と `issue_context` 解決の順序 |
| 現行 state | `kaji_harness/state.py` | `SessionState` の artifacts directory と `issue_number` 永続化 |
| 現行 local provider | `kaji_harness/providers/local.py` | 自前 frontmatter parser、slug 必須、comment seq の実装 |
| 現行 CLI dispatcher | `kaji_harness/cli_main.py` | `_apply_jq()` の system jq 委譲、local create argparse |
| Python dependency | `pyproject.toml` | `pyyaml>=6.0` は既存 runtime dependency、`jq` は未追加 |
| PyYAML documentation | https://pyyaml.org/wiki/PyYAMLDocumentation | `safe_load` / `safe_dump` を使った YAML parsing / emitting の一次情報 |
| jq PyPI package | https://pypi.org/project/jq/ | Python binding として `jq.compile(...).input_value(...).all()` を使える runtime dependency 候補 |

## 完了条件の段階確認

- [x] Phase 3-c レビューで見えた 5 論点を設計対象として明示した
- [x] 他担当者が実装可能な変更対象ファイルと順序を明示した
- [x] 実行時コード変更として Small / Medium / Large の検証観点を分けた
- [x] Phase 3-d / 3-e / Phase 4 とのスコープ境界を明示した
