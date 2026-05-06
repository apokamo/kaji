---
status: draft
phase: 3e
parent: phase3-design.md
created: 2026-05-06
---

# [設計] kaji local mode — Phase 3-e: fail-fast 化 + Large-local + CHANGELOG

Issue: TBD（local-mode buildout 期間中の handoff 設計。GitHub 復旧後に該当 Issue へ紐付ける）

## 概要

Phase 3-a 〜 Phase 3-d で、provider 抽象 / `IssueContext` / `LocalProvider` / `kaji
local init` / `feature-development-local.yaml` / Skill 5 変数化 / dev repo
dogfooding が main に揃った。残されているのは **「`[provider]` 未設定 + WARN
fallback」を削除して破壊的変更を発動する** ことと、**Large-local subprocess
E2E** で local mode が end-to-end 動作することを構造で確認すること、そして
**CHANGELOG / migration guide** で外部告知できる状態を作ることの 3 点である。

本書は他担当者が実装する handoff 文書である。実装担当者は本書の「方針決定」
「実装範囲」「受け入れ条件」を正本として扱い、`phase3-design.md` § 4
ロールアウト戦略および `phase3d-implementation-report.md` § 次フェーズへの
申し送り と矛盾する場合は本書の判断を優先する（Phase 3-d 完了で前提が一段
進んでいるため）。本書は phase3-design.md / design.md を **置き換えない**。

## 背景・目的

### Phase 3-d 完了時点の状態

実装済み（参照: `phase3c-implementation-report.md` / `phase3d-preflight-implementation-report.md`
/ `phase3d-implementation-report.md`）:

- `kaji_harness/providers/` の `IssueProvider` / `IssueContext` 抽象、`LocalProvider`
  / `GitHubProvider` 実装、`get_provider()` factory（`provider.type` 未設定時は
  **WARN + None 返却**）
- `kaji issue` dispatcher（`_handle_issue` / `_handle_pr`）、`_load_config_for_dispatch_or_none`
  経由の config 解決と provider 切替
- `WorkflowRunner._resolve_run_issue_context()` による canonical issue id 確定、
  `IssueContext` 由来の prompt 注入経路（5 + 2 = 7 変数 + `issue_id` / `issue_ref`）
- `kaji local init` CLI（overlay-only 上書き仕様、`validate_machine_id` /
  `validate_default_branch` を explicit に呼ぶ）
- `.kaji/wf/feature-development-local.yaml`、Skill 21 ファイルの 5 変数化、
  `issue-close` の provider 分岐 + design.md L972-996 6-step 反映
- dev repo `.kaji/config.toml` に `[provider.type = "github"]` + `[provider.github]
  repo = "apokamo/kaji"` + `default_branch = "main"`、`.gitignore` に
  `.kaji/config.local.toml`、`docs/cli-guides/local-mode.md`

未着手（本 Phase 3-e で実装）:

| 項目 | 影響 |
|------|------|
| `provider.type` 未設定 fallback（WARN + GitHub 透過）が残存 | `[provider]` セクション必須化が未発動。design.md L1283 / phase3-design.md § 4 PR-3e の破壊的変更が宙に浮いている |
| `ConfigNotFoundError` 経路の legacy passthrough が残存 | リポジトリ外（`.kaji/config.toml` 不在）で `kaji issue view 1` が黙って `gh` へ転送される。`[provider]` 必須化と整合しない |
| `provider.local.machine_id` の regex 検証が config load 時点で走らない | `.kaji/config.local.toml` を user が手書きした場合、`PC1` / `pc-1` / 17 文字超 等の不正値が `kaji local init` を経由しないで紛れ込み、後段の `normalize_id` で初めて発覚する |
| `IssueContext is None` の互換ブランチが prompt.py / runner.py に残存 | fallback 削除後は到達不能になるが、型 (`IssueContext \| None`) が緩いまま |
| Large-local subprocess E2E が未整備 | `kaji local init` → `kaji issue create` → `kaji issue list` の subprocess 動作確認が手動。phase3d-implementation-report § 手動確認 で「Phase 3-e Large-local で扱う」と申し送られている |
| CHANGELOG が無い | 破壊的変更の外部告知経路が無い。phase3-design.md § 4 / phase3d-implementation-report § 136 で Phase 3-e 持ち越しと明示 |
| `make test-large-local` ターゲット未整備 | 既存 `make test-large` は外部通信あり（`gh` 経由）と外部通信なし（local subprocess）が混在、CI 上で前者を skip しつつ後者を回すことができない |

### Phase 3-e が解かない問題（後続）

| 項目 | Phase |
|------|-------|
| `kaji pr` の bare provider エラー化、`pr-fix` / `pr-verify` Skill の provider 分岐 | 4 |
| `pr_id` / `pr_ref` の prompt 注入変数化 | 4 |
| `kaji sync from-github` / cache 整備 / BCP runbook | 5 |
| Windows の本格対応（`portalocker` 化等） | 後続オープン論点 |
| Large-forge（実 GitHub 通信あり）E2E | GitHub 復旧後（`make test-large-forge` 新設） |

## スコープ

### in-scope

1. **`get_provider()` の fail-fast 化** — `config.provider is None` で `ValueError`
   を raise（旧: WARN + None）。WARN 関数（`_emit_provider_fallback_warning` /
   `_PROVIDER_FALLBACK_WARNED`）を削除し、戻り型を `IssueProvider | None` →
   `IssueProvider` に narrowing する。
2. **`cli_main._handle_issue` / `_handle_pr` の fallback 削除** — `config is None`
   分岐 / `config.provider is None` 分岐を削除し、いずれの経路も「config + provider
   両方が必須」を front door で要求する。
3. **`_load_config_for_dispatch_or_none` の fail-fast 化** — `ConfigNotFoundError`
   も exit 2（リポジトリ外で `kaji issue` を使う legacy passthrough を廃止）。
   関数名は `_load_config_for_dispatch`（`or_none` 削除）に rename。
4. **`runner._resolve_issue_context` の None 経路削除** — fallback 削除後
   `provider is None` は到達不能のため type narrowing し、`RunIssueContext.issue_context`
   field を `IssueContext | None` → `IssueContext` に変更。
5. **`prompt.build_prompt` の `IssueContext` 必須化** — 引数を Optional から必須に
   変更し、`if issue_context is not None:` ブランチを削除する。
6. **`config.py` への `validate_machine_id` 統合** — `_parse_provider` で
   `local_cfg.machine_id` が non-empty な場合に `validate_machine_id` を呼ぶ
   （`PC1` / `pc-1` / 17 文字 / 制御文字 等は `ConfigLoadError` で停止）。
7. **`make test-large-local` ターゲット追加** — pytest marker `large_local` を
   新設し、subprocess E2E を独立にカテゴライズする。Makefile に
   `test-large-local: pytest -m large_local` を追加。
8. **Large-local subprocess E2E（`tests/test_phase3e_large_local.py`）追加** —
   fresh tmp repo に対し subprocess `kaji local init` / `kaji issue create` /
   `kaji issue list` / `kaji validate .kaji/wf/feature-development-local.yaml`
   を起動し、生成物（`.kaji/config.local.toml` / `.kaji/issues/local-<m>-1-<slug>/`）
   と exit code を確認する。実 agent CLI 起動（`claude` / `codex`）は本 Phase
   外（API コスト発生のため、Mock adapter は Medium 範囲で別途扱う）。
9. **CHANGELOG.md 新規作成** — Keep a Changelog 形式でルート `CHANGELOG.md` を作成し、
   Phase 3-a 〜 Phase 3-e の累積差分（Added / Changed / Removed / **BREAKING**）を
   記録する。GitHub 復旧後はリリースノートのソースとして利用できる状態にする。
10. **migration guide 充実** — `docs/cli-guides/local-mode.md` に「Phase 3-e
    fail-fast 化に伴う既存 repo の移行手順」セクションを追加する。dev repo は
    Phase 3-d で対応済のため、本 guide は **外部 user 向け** の補足。
11. **既存テストの整理** — `_PROVIDER_FALLBACK_WARNED` を参照する 3 箇所
    （`test_phase3c_dispatcher.py:111` / `test_phase3d_preflight.py:103` /
    `test_phase3c_runner.py:234`）と、`test_existing_pr_view_falls_back_to_passthrough`
    （`test_cli_main.py:769`）を fail-fast 検証に書き換える。
12. **影響ドキュメント補正** — `phase3-design.md` § 4 PR-3e の達成記録、
    `design.md` § 移行・互換性 の Phase 3-e リリース確定情報、`docs/cli-guides/local-mode.md`
    の migration セクション。

### out-of-scope

| 項目 | 後続 |
|------|------|
| 実 agent CLI（`claude` / `codex` / `gemini`）を起動する E2E | 別途、buildout 中はコスト判断、復旧後の `make test-large-forge` で扱う |
| `kaji pr` の bare provider エラー化 | Phase 4 |
| Windows 本格対応 | 後続オープン論点（`portalocker` 化 vs atomic create+rename） |
| `kaji local init --force` 上書き flag | phase3-design.md § オープン論点 |
| `provider.github.repo` の formal validation（owner/name regex）| 必要なら Phase 4 で別途 |
| 残存 オープンな論点（slug 同梱 worktree、`branch_prefix` mapping の user 設定可能化、cache 由来 Issue の close 挙動 等）| Phase 4-5 ないし別 ADR |

## 方針決定

### 0. レビュー反映（v2）の経緯

初回設計（v1）に対するレビューで以下 5 点の修正を受けた。本書はその反映後の v2:

| # | 指摘 | 反映 |
|---|------|------|
| MF1 | `kaji run` で `[provider]` 不在 → 設計上 exit 2 と書いたが、実際は `IssueContextResolutionError` が `HarnessError` 経由で `EXIT_RUNTIME_ERROR=3` に落ちる | § 1.5 で `cmd_run` 冒頭の早期 fail-fast を追加し、exit 2 に正規化する |
| MF2 | `build_prompt` を required 化すると 20+ 件のテスト書換が必要、設計の対象ファイル列挙が `test_prompt.py`（実在しない）になっていた | § 2 で **設計を縮小**: signature の Optional は維持し、runner / RunIssueContext のみ narrowing する。prompt.py 内部の `if issue_context is not None:` は Phase 4 削除候補に申し送り |
| MF3 | Large-local の `kaji validate` テストは `.kaji/config.toml` + skill ディレクトリを要求するが `kaji local init` は overlay しか作らない | § 4 で `kaji validate` を Large-local から削除（Phase 3-d Medium で済）、minimum `.kaji/config.toml` fixture セットアップ手順を明示 |
| SF4 | CHANGELOG 草案で「7 prompt variables」と書きつつ 9 個列挙していた | § 5 で「9 context variables」に修正 |
| SF5 | commit 4 と commit 5 で「旧 fallback 検証テスト書換」の責務が重複 | § 7 で commit 4 にテスト書換すべて集約、commit 5 は Large-local enable のみに整理 |

### 1. fallback 削除の境界（`provider` 未設定 / config 不在の両方を fail-fast）

#### 問題

Phase 3-c で導入された fallback 経路は 2 系統存在する。

| 経路 | 条件 | 現挙動 |
|------|------|--------|
| (a) `[provider]` セクション不在 | `KajiConfig.provider is None` | `get_provider()` が WARN を 1 度出して `None` を返し、CLI 層で legacy `gh` 透過 |
| (b) `.kaji/config.toml` 自体が不在 | `ConfigNotFoundError` | `_load_config_for_dispatch_or_none` が `None` を返し、CLI 層で legacy `gh` 透過（リポジトリ外で `kaji issue view 1` が `gh` に転送される） |

phase3-design.md § 4 / design.md L1283 が明示的に対象としているのは **(a)** の
削除である。一方 **(b)** は phase3-design.md では言及されていないが、phase3-c
review #3 で「壊れた config は exit 2、`ConfigNotFoundError` のみ legacy 経路」
として残った経緯がある。

#### 決定

**(a) (b) の両方を fail-fast 化する。**

(b) を残すと「`[provider]` 必須化」と「リポジトリ外で `gh` 透過」が両立し、
規範が不整合になる。kaji の利用想定は `.kaji/config.toml` を持つリポジトリ内
での開発であり、リポジトリ外で `gh` ラッパとして使いたい user は `gh` を直接
使えば済む。Phase 3-e の主要破壊は (a) なので、(b) を同時に整理する方が
CHANGELOG / migration が一回にまとまる。

#### 具体的挙動

| 入力 | 旧（Phase 3-d） | 新（Phase 3-e） |
|------|-----------------|------------------|
| `.kaji/config.toml` 不在 | WARN なし、legacy passthrough | exit 2、メッセージ「`.kaji/config.toml` not found. Run `kaji local init` (local) or add `[provider]` section (github)」 |
| `[provider]` セクション不在 | WARN + legacy passthrough | exit 2、メッセージ「`[provider]` section is required. Add the following to `.kaji/config.toml`: ...」（旧 WARN メッセージとほぼ同じ本文を error として出す） |
| `[provider.type = "github"]` + `[provider.github]` 不在 | exit 2（既存） | exit 2（変更なし） |
| `[provider.type = "local"]` + `machine_id` 不在 | exit 2（既存） | exit 2（変更なし） |
| `[provider.type = "local"]` + `machine_id = "PC1"` | `kaji issue` で normalize_id 時に exit 2 | **config load 時点で exit 2**（§ 3 で実装） |

#### dev repo への影響

dev repo `.kaji/config.toml` は Phase 3-d で `[provider.type = "github"]` +
`[provider.github] repo = "apokamo/kaji"` + `default_branch = "main"` を commit
済 (commit 9 / `820984f`)。本 Phase の fail-fast 化を入れても dev repo 自身は
壊れない（PR-3d で先回り済の窓に入る）。

#### 関数名 rename

- `_load_config_for_dispatch_or_none` → `_load_config_for_dispatch`
  - 戻り型 `KajiConfig | None` → `KajiConfig`
  - `ConfigNotFoundError` を catch せず `ConfigLoadError` と同様 propagate
  - 呼出側（`_handle_issue` / `_handle_pr`）は `try / except (ConfigNotFoundError,
    ConfigLoadError)` で sys.stderr + exit 2 に統一

#### `get_provider` の戻り型 narrowing

```python
# 旧
def get_provider(config: KajiConfig) -> IssueProvider | None: ...

# 新
def get_provider(config: KajiConfig) -> IssueProvider:
    if config.provider is None:
        raise ValueError(
            "[provider] section is required in .kaji/config.toml.\n"
            "  For GitHub: add\n"
            "    [provider]\n"
            "    type = \"github\"\n\n"
            "    [provider.github]\n"
            "    repo = \"<owner>/<repo>\"\n"
            "  For local-first: run `kaji local init`."
        )
    ...
```

### 1.5. `cmd_run` の exit code 正規化（レビュー MF1 反映）

#### 問題

`kaji run` 経路では `WorkflowRunner._resolve_run_issue_context()` 内部で
`get_provider(config)` を呼ぶ。fail-fast 化後、`config.provider is None` で
`get_provider` が `ValueError` を raise すると、現状の `runner.py:92-100` で
**`IssueContextResolutionError` に wrap される**:

```python
# kaji_harness/runner.py（現行）
try:
    provider = get_provider(self.config)
except ValueError as exc:
    raise IssueContextResolutionError(
        issue_input=self.issue_number,
        provider_type=(self.config.provider.type if self.config.provider else "unset"),
        cause=exc,
    ) from exc
```

`IssueContextResolutionError` は `HarnessError` のサブクラス
（`kaji_harness/errors.py:86`）。`cmd_run` の例外処理は:

```python
# kaji_harness/cli_main.py:294
except HarnessError as e:
    print(f"Error: {e}", file=sys.stderr)
    return EXIT_RUNTIME_ERROR   # = 3
```

`kaji issue` / `kaji pr` の dispatcher は exit 2、`kaji run` は exit 3 という
**exit code 契約が割れる**。CHANGELOG / migration guide で「exit 2」と書くと
事実と合わない。

#### 決定

**`cmd_run` 冒頭で `get_provider(config)` を呼んで早期 fail-fast し、
`ValueError` は exit 2（`EXIT_INVALID_INPUT`）に正規化する。**

```python
# kaji_harness/cli_main.py:cmd_run（変更点）
try:
    config = KajiConfig.discover(start_dir=start_dir)
except ConfigNotFoundError as e:
    print(f"Error: {e}", file=sys.stderr)
    return EXIT_CONFIG_NOT_FOUND   # = 2（既存）
except ConfigLoadError as e:
    print(f"Error: {e}", file=sys.stderr)
    return EXIT_CONFIG_NOT_FOUND   # = 2（既存）

# 追加（Phase 3-e）: provider config を runner に入る前に validate
try:
    get_provider(config)   # 戻り値は捨てる、validation 目的
except ValueError as e:
    print(f"Error: {e}", file=sys.stderr)
    return EXIT_INVALID_INPUT   # = 2
```

`get_provider` を 2 度呼ぶ（cmd_run 冒頭 + runner 内部）コストは無視できる
（kaji run 起動時の 1 度のみ、provider 構築は file I/O ほぼなし）。

#### exit code 契約の確定

| 経路 | 入力 | exit |
|------|------|------|
| `kaji issue view 1` | `[provider]` 不在 | 2（`EXIT_INVALID_INPUT`） |
| `kaji issue view 1` | `.kaji/config.toml` 不在 | 2 |
| `kaji pr view 153` | `[provider]` 不在 | 2 |
| `kaji pr view 153` | `.kaji/config.toml` 不在 | 2 |
| `kaji run wf.yaml 1` | `[provider]` 不在 | 2（cmd_run 早期 fail-fast 経由） |
| `kaji run wf.yaml 1` | `.kaji/config.toml` 不在 | 2（既存 `EXIT_CONFIG_NOT_FOUND`） |
| `kaji run wf.yaml 1` | provider はあるが Issue 解決失敗 | 3（`IssueContextResolutionError` → `EXIT_RUNTIME_ERROR`、既存挙動） |
| `kaji local init --machine-id PC1` | regex 違反 | 2 |
| `kaji local init` 二重実行 | 既存 overlay | 3（既存） |

**「config / provider 設定の問題は exit 2、Issue 解決の問題は exit 3」** が
本 Phase で確定する契約。

#### Large-local テストでの確認

§ 4 の Large-local テストに以下を追加する:

- subprocess `kaji run feature-development-local.yaml 1` を `[provider]` 不在
  の tmp repo に対して実行 → exit 2、stderr に新メッセージ
- subprocess `kaji run feature-development-local.yaml 1` を `.kaji/config.toml`
  不在 cwd で実行 → exit 2

### 2. `IssueContext is None` 経路の削除（レビュー MF2 反映で縮小）

#### 問題

`prompt.build_prompt(issue_context=None)` と `runner.RunIssueContext.issue_context: IssueContext | None`
は Phase 3-c 互換のため Optional のまま残っている。fail-fast 化後は本番経路では
到達不能。

#### 決定（v1 → v2 で縮小）

レビュー MF2 の指摘で実装影響面を再検証した結果:

- `tests/test_prompt_builder.py`: 15 件の `build_prompt(...)` 呼び出しが
  `issue_context` 引数なし
- `tests/test_verdict_integration.py:404`: 1 件
- `tests/test_phase3c_runner.py`: 4 件（うち legacy fallback 検証 1-2 件、
  IssueContext 注入を直接検証する 2-3 件）

`build_prompt` を required 化すると上記すべてに `issue_context=<fixture>` を
追加する必要があり、Phase 3-e 主旨（fallback 削除 + Large-local + CHANGELOG）
の 1.5 〜 2 倍の作業量になる。**本 Phase ではこの構造的削除を見送る**。

採用する narrowing は以下に縮小する:

- `runner._resolve_issue_context() -> IssueContext` — None を返さず、provider
  不在は早期 raise（§ 1.5 で `cmd_run` 側に正規化）
- `runner.RunIssueContext.issue_context: IssueContext` — Optional 削除
- `prompt.build_prompt(..., issue_context: IssueContext | None = None)` — **signature
  変更しない**（後方互換維持）
- `prompt.py` 内 `if issue_context is not None:` ブランチも **残す**（Phase 4
  以降の削除候補として申し送り）

この縮小により:

- `test_prompt_builder.py` / `test_verdict_integration.py`: touch 不要
- `test_phase3c_runner.py`: legacy fallback 検証ケース（None 経由）だけ書換、
  IssueContext 注入の直接呼び出しは touch 不要

#### 申し送り（Phase 4 / 別 Issue）

- `build_prompt` の signature を required 化し、`if issue_context is not None:`
  ブランチを削除する
- 同 Phase で test fixtures（共通 IssueContext factory）を整備
- 上記によって prompt 内の `issue_id` / `issue_ref` が常に context 由来になり、
  args 由来の上書きパスが消える（コード簡素化）

#### narrowing の妥当性

`build_prompt` を呼ぶ本番経路（`runner.py` 内 `WorkflowRunner.run`）は preflight
+ `_resolve_run_issue_context` を経由しており、Phase 3-e 後は必ず `IssueContext`
を渡している。本番経路では None 経由に到達しないため、構造的削除を Phase 4 に
持ち越しても挙動上のリスクは無い。レガシーテストのために signature を緩める
形が残るが、これは構造の歪みであり functional な問題ではない。

### 3. `validate_machine_id` の `config.py` 統合

#### 問題

phase3d-design.md § 6 で「`config.py` への validation 統合は Phase 3-e の
fail-fast 化と同時に検討する」と申し送られた。現状 (`config.py:226-228`) は
`isinstance(machine_id, str)` のみで `[a-z0-9]{1,16}` 検証なし。`kaji local init`
を経由しない手書き `.kaji/config.local.toml` で `machine_id = "PC1"` 等が紛れる
と、`kaji issue create` が動いた瞬間に `normalize_id` 内 regex で初めて発覚し、
trace が分かりにくい。

#### 決定

**Phase 3-e で `config.py._parse_provider` から `validate_machine_id` を呼ぶ。**

```python
# kaji_harness/config.py の _parse_provider 内（変更点）
machine_id = local_raw.get("machine_id", "") or ""
if not isinstance(machine_id, str):
    raise ConfigLoadError(path, "provider.local.machine_id must be a string")
if machine_id:  # 空文字は許容（type=github + provider.local 空で stub する場合）
    from .providers.local import validate_machine_id
    try:
        validate_machine_id(machine_id)
    except ValueError as e:
        raise ConfigLoadError(
            path,
            f"provider.local.machine_id {machine_id!r} is invalid: {e}. "
            f"Must match [a-z0-9]{{1,16}}.",
        ) from e
```

#### 空文字許容の扱い

`machine_id = ""` は config schema 上の default で、`type = "github"` + 空の
`[provider.local]` が現れる正常ケース（dev repo は `[provider.local]` セクション
自体を持たないが、overlay 不在時は default が適用される）。空文字を validate
すると false positive になるため、**空文字は skip / non-empty のみ validate**。

`type = "local"` + `machine_id = ""` は既存の `get_provider()` で「`machine_id`
required」エラーが上がるため、二重 validation にならない。

#### `default_branch` の validation 取扱

`local_init.py:validate_default_branch` は git の `check-ref-format` 保守的
サブセットで、`kaji local init` 経路でのみ呼ばれる。config load 時点での
統合は **本 Phase ではしない**（phase3d レビュー反映 commit `0d81577` のスコープ
維持）。理由は:

- `default_branch` が壊れていても影響は merge 引数の生成のみ（即死しない）
- 既存 `.kaji/config.toml` で `default_branch = "main"` が普通に commit
  されているため、config load 時の validation は false positive になりにくいが、
  一旦保守的に保留
- 必要なら Phase 3-e の判断済み論点で「本 Phase は machine_id のみ統合、
  default_branch は将来検討」と明示する

### 4. Large-local テストの範囲

#### 問題

phase3-design.md § テスト戦略 / phase3d-implementation-report § 手動確認 で
「Large-local subprocess E2E は Phase 3-e で実施」と申し送られている。一方、
実 agent CLI（`claude` / `codex` / `gemini`）を起動する full E2E（design →
implement → close）は API コスト発生のため、buildout 中の自動化は避けたい。

#### 決定

**Phase 3-e の Large-local は「kaji CLI subprocess の起動 + filesystem 確認」
に限定する。** agent CLI を起動する E2E は別途扱う（CI 外、開発者手動 / 復旧後）。

#### tmp repo セットアップの正本（レビュー MF3 反映）

`kaji local init` は **overlay (`.kaji/config.local.toml`) しか作らない**。
tracked `.kaji/config.toml`（`[paths]` / `[execution]` 含む）も skill ディレクトリ
も自前で作らない。Large-local テストは fresh tmp_path に対し、以下の fixture
セットアップを行う:

```python
# tests/test_phase3e_large_local.py（fixture）
@pytest.fixture
def fresh_repo(tmp_path: Path) -> Path:
    """`kaji local init` 直前の fresh tmp repo を作る。
    
    - .kaji/config.toml を minimum 構成（paths + execution、provider 無し）で生成
    - .gitignore は空（kaji local init が追記する経路を確認するため）
    - skill ディレクトリは作らない（kaji validate を本テストでは要求しない）
    - git init は行わない（kaji local init / kaji issue は git repo 不要）
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    kaji_dir = repo / ".kaji"
    kaji_dir.mkdir()
    (kaji_dir / "config.toml").write_text(
        "[paths]\n"
        'artifacts_dir = ".kaji-artifacts"\n'
        'skill_dir = ".claude/skills"\n'
        "\n"
        "[execution]\n"
        "default_timeout = 1800\n"
    )
    (repo / ".gitignore").write_text("")
    return repo
```

`[provider]` 不在 case はこの `config.toml` をそのまま使う。`kaji local init`
を流して overlay を作る case は fixture の後で subprocess を起動する。
`config.toml` 自体不在 case は `fresh_repo` ではなく裸の `tmp_path` を使う。

#### 具体的テスト項目（`tests/test_phase3e_large_local.py`、新規）

| ケース | fixture | 動作 |
|--------|---------|------|
| `kaji local init` smoke | `fresh_repo` | subprocess `kaji local init --machine-id pc1 --non-interactive` を起動。`.kaji/config.local.toml` 内容、`.gitignore` の追記、exit 0 を確認 |
| `kaji local init` 再実行 | `fresh_repo` + 既存 overlay | exit 3 を subprocess で確認 |
| `kaji local init --machine-id PC1` | `fresh_repo` | exit 2（validate_machine_id 経由） |
| `kaji local init --machine-id pc-1` | `fresh_repo` | exit 2（hyphen 拒否） |
| `kaji local init --default-branch develop` | `fresh_repo` | overlay に `default_branch = "develop"` |
| `kaji issue create` smoke | `fresh_repo` + `local init` | subprocess `kaji issue create --title "phase 3e smoke" --body "x"`。`.kaji/issues/local-pc1-1-phase-3e-smoke/issue.md` の frontmatter（`state: open` / `slug: phase-3e-smoke`）を確認 |
| `kaji issue list` smoke | 上記 + create | subprocess の stdout に `local-pc1-1` が含まれること |
| `kaji issue close` smoke | 上記 + create | subprocess `kaji issue close local-pc1-1` 後、frontmatter `state: closed` / `close_reason: completed` |
| fail-fast: `[provider]` 不在 で `kaji issue view 1` | `fresh_repo`（init せず）| exit 2、stderr に「[provider] section is required」 |
| fail-fast: `[provider]` 不在 で `kaji pr view 153` | `fresh_repo`（init せず）| exit 2、stderr に「[provider] section is required」 |
| fail-fast: `[provider]` 不在 で `kaji run feature-development-local.yaml 1` | `fresh_repo`（init せず）| **exit 2**（§ 1.5 cmd_run 早期 fail-fast 経由）、stderr に「[provider] section is required」 |
| fail-fast: `.kaji/config.toml` 不在 で `kaji issue view 1` | 裸の `tmp_path` | exit 2、stderr に「.kaji/config.toml not found」 |
| fail-fast: `.kaji/config.toml` 不在 で `kaji run feature-development-local.yaml 1` | 裸の `tmp_path` | exit 2（既存 `EXIT_CONFIG_NOT_FOUND` 経路）、stderr に config not found |

**`kaji validate` は Large-local から削除する**。理由:

- `kaji validate` は `KajiConfig.discover()` + `validate_skill_exists()` を要求し、
  tmp repo に `.claude/skills/` 全体（21+ Skill ファイル + サブディレクトリ）の
  fixture コピーが必要になる
- 同等の workflow YAML 検証は Phase 3-d で `tests/test_local_init.py` および
  `tests/test_phase3d_default_branch.py` 周辺の Medium テスト（dev repo の実
  yaml に対する `kaji validate`）で済んでいる
- Large-local の主目的は「kaji CLI subprocess + filesystem の基本経路が動く」
  確認であり、workflow validation は Medium で十分

`feature-development-local.yaml` の存在自体は Phase 3-d で `make check`
（`pytest` 内 Medium）で担保されている。

#### marker 追加と Makefile

```python
# tests/test_phase3e_large_local.py（先頭）
import pytest

pytestmark = [pytest.mark.large, pytest.mark.large_local]
```

```makefile
# Makefile（追加）
test-large-local:
	pytest -m large_local
```

`large_local` は `large` の subset（design.md § 検証戦略 § Large の分割と整合）。
`make test-large` は両方走る、`make test-large-local` は外部通信なし subset
のみ走る、`make test-large-forge` は将来 (Phase 5+) 追加。

#### `pyproject.toml` への marker 登録

```toml
# pyproject.toml [tool.pytest.ini_options] に追加
markers = [
    ...
    "large_local: Large tests with subprocess but no external network (Phase 3-e+)",
    "large_forge: Large tests requiring real GitHub API (post-recovery)",
]
```

### 5. CHANGELOG.md の配置

#### 問題

リポジトリに CHANGELOG が無い。Phase 3 の累積（3-a / 3-b / 3-c / 3-d / 3-e）で
段階的に積み上げた変更を、外部告知できる形でまとめる必要がある（phase3-design.md
§ 4 migration guide 文面 ドラフト 参照）。

#### 決定

**ルート `CHANGELOG.md` を Phase 3-e で新規作成する。** Keep a Changelog 形式。

理由:

- 標準的配置（GitHub releases / `gh release create --notes-file CHANGELOG.md`
  と相性良い）
- Phase 3-d で `docs/cli-guides/local-mode.md` を作ったが、CHANGELOG は
  リリース履歴の正本でありガイドとは別物
- 復旧後はリリースノートのソースとして直接使える

#### 内容（Phase 3-e 時点）

```markdown
# Changelog

All notable changes to kaji are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### BREAKING CHANGE

- `[provider]` section is now **required** in `.kaji/config.toml`.
  `kaji issue` / `kaji pr` / `kaji run` will exit 2 with a setup message
  if it is missing. Previously, missing `[provider]` triggered a one-time
  WARN and fell back to GitHub passthrough.
- `.kaji/config.toml` itself is now required to invoke `kaji issue` /
  `kaji pr`. The legacy passthrough that forwarded these commands to `gh`
  outside of a kaji repository has been removed.
- `provider.local.machine_id` is now validated at config load time
  (must match `[a-z0-9]{1,16}`). Hand-edited `config.local.toml` with
  invalid values (`PC1`, `pc-1`, etc.) now fails fast with
  `ConfigLoadError` instead of crashing later in `kaji issue` /
  `kaji run`.

### Migration

For existing GitHub-based usage, add to `.kaji/config.toml`:

    [provider]
    type = "github"

    [provider.github]
    repo = "<owner>/<repo>"

For local-first usage, run `kaji local init` (creates
`.kaji/config.local.toml` overlay). See
`docs/cli-guides/local-mode.md`.

## [Phase 3] — kaji local mode

### Added

- LocalProvider for GitHub-independent issue management
  (`.kaji/issues/<id>-<slug>/issue.md`, file-based CRUD, POSIX flock for
  ID counter, atomic frontmatter writes via `os.replace`)
- `IssueProvider` Protocol + `IssueContext` (provides 9 context variables
  via `IssueContext`: `issue_id` / `issue_ref` / `issue_input` /
  `branch_prefix` / `branch_name` / `worktree_dir` / `design_path` /
  `provider_type` / `default_branch`; plus `step_id` from the step
  definition itself)
- `kaji local init` CLI (overlay-only: writes
  `.kaji/config.local.toml`, never tracked `.kaji/config.toml`;
  hostname-based machine_id candidate, `.gitignore` integration)
- `feature-development-local.yaml` workflow (final step is
  `issue-close` instead of `i-pr`, no PR concept under local mode)
- `kaji_harness/providers/_mappings.py` `LABEL_TO_PREFIX` table (canonical
  source of `type:* label → branch_prefix` mapping; `.claude/skills/`
  markdown becomes documentation only)
- ID normalization across `local-<machine>-<n>` / `<machine>-<n>` /
  numeric / `gh:N` forms (read-only `gh:N` for cached GitHub issues
  under local mode)
- Skill markdown placeholder unification: `[branch-name]` →
  `[branch_name]`, `[worktree-absolute-path]` → `[worktree_dir]`,
  `[design-path]` → `[design_path]`, `[issue-input]` → `[issue_input]`
  (21 skill files; legacy hyphen / angle-bracket forms grep-asserted to
  zero)
- `issue-close` skill provider branching: `provider=local` follows the
  6-step base-worktree merge flow defined in design.md L972-996
- pytest markers `large_local` / `large_forge` for subprocess E2E
  categorization

### Changed

- `kaji issue` / `kaji pr` dispatcher routes through `get_provider()`
  factory; `--repo` is auto-injected when `[provider.github] repo` is
  configured
- `LocalProvider.close_issue(reason=None)` now writes
  `close_reason: "completed"` (was empty string), aligning with
  design.md L985 and GitHub Issue API default

### Removed

- WARN-then-fallback path for missing `[provider]` section (now
  fail-fast, see BREAKING)
- Legacy `kaji issue` / `kaji pr` passthrough outside kaji repositories
  (now fail-fast, see BREAKING)
```

`[Unreleased]` は Phase 3-e merge を release タグでくくる時点で日付付きの
セクションにする。本 Phase ではドラフト状態で main に commit する。

#### 配置場所

ルート `CHANGELOG.md`。`docs/CHANGELOG.md` ではなくルート配置にする理由は
GitHub releases / 各種リリースツール（`gh release create --notes-file`）の
慣習による。

### 6. dev repo dogfooding の確認のみ

Phase 3-d で `.kaji/config.toml` に `[provider]` 追記、`.gitignore` 整備が
完了している。Phase 3-e で touch しない。**実装担当者は本 Phase の commit を
入れる前に `git diff main -- .kaji/config.toml .gitignore` で何も差分が
無いことを確認する**（dev repo を破壊する commit が紛れ込んでいないことの
保険）。

### 7. PR-3e 内 commit 順序

| commit | 範囲 | 副作用 | rollback コスト |
|--------|------|--------|----------------|
| 1 | `pyproject.toml` に marker `large_local` / `large_forge` 登録 + `Makefile` に `test-large-local` 追加 | 既存テストへの影響なし | revert で完全戻し |
| 2 | `config.py._parse_provider` で `validate_machine_id` を non-empty 時に呼ぶ + Small/Medium テスト | invalid machine_id を持つ既存 config はエラーになるが、dev repo は該当しない | revert で完全戻し |
| 3 | `tests/test_phase3e_large_local.py` 追加（fixture + 非 fail-fast 系テスト：`kaji local init` smoke / `kaji issue create/list/close`）。fail-fast 系ケースは `pytest.skip("enable in commit 5")` で保留 | CI 上 `make test-large` 経路に新規 | revert で削除 |
| 4 | **fail-fast 本体 + 既存テスト書換すべて**: `get_provider()` fail-fast 化（戻り型 narrowing、WARN 関数削除）+ `cmd_run` 冒頭の `get_provider` 早期呼び出しで exit 2 正規化（§ 1.5）+ `_load_config_for_dispatch` rename + dispatcher の None 分岐削除 + `runner._resolve_issue_context` の None 経路削除 + `runner.RunIssueContext.issue_context` を required に + **既存 fallback 検証テストを fail-fast 検証に書換**（`test_phase3c_dispatcher.py` / `test_phase3c_runner.py` / `test_phase3d_preflight.py` / `test_cli_main.py`）+ `_PROVIDER_FALLBACK_WARNED` 参照の削除 | **破壊的変更の本体**。`make check` 緑を維持するためテスト書換は同 commit 内に必須 | revert で全戻し（dev repo は無事） |
| 5 | `tests/test_phase3e_large_local.py` の fail-fast 系ケースを `pytest.skip` 解除（commit 4 で挙動が変わったため真値を期待できる） | Large-local テスト一式が enable | revert で skip 復活 |
| 6 | `CHANGELOG.md` 新規作成（Keep a Changelog 形式、Phase 3 累積差分） | docs 追加のみ | revert |
| 7 | `docs/cli-guides/local-mode.md` に migration セクション追加 + `phase3-design.md` § 4 PR-3e の達成記録、§ オープンな論点 から本 Phase 解決済項目を移動 + `design.md` § 移行・互換性 の Phase 3-e リリース確定情報 | docs 更新のみ | revert |

#### commit 4 / 5 の責務分離（レビュー SF5 反映）

v1 では commit 4 / 5 で「旧 fallback 検証テストの書換」が両方に書かれていた
責務重複を整理した:

- **commit 4**: 実装変更 + 既存テストの書換 **すべて**（make check を緑に保つため）
- **commit 5**: Large-local テストの skip 解除 **のみ**

commit 4 で既存テスト書換を分離すると、その時点で `make check` が落ちる。
fail-fast 化と既存テストは「片方だけ」の状態を作れないため、同 commit 必須。

commit 1-3 は contract / インフラ整備（破壊なし）、commit 4 で破壊的変更
発動 + 既存テスト追従、commit 5 で Large-local enable、commit 6-7 で告知
整備。各 commit で `make check` 緑を維持。

#### make check ゲート

- commit 1: `make check` 緑（marker 登録は既存テストに影響しない）
- commit 2: `make check` 緑（dev repo の `.kaji/config.toml` には
  `provider.local` セクションが無いか空のため、validation 統合は noop）
- commit 3: `make check` 緑（新規 large_local テスト群は `pytest -m large`
  で初めて拾われる。`make test` 既定動作には影響なし。fail-fast 系は
  `pytest.skip("enable in commit 5")` で保留）
- commit 4: `make check` 緑（fallback 関連既存テストを **同 commit 内で**
  書換。実装変更とテスト書換は分離不可能 — 別コミットにすると間で
  `make check` が落ちる）
- commit 5: `make check` 緑（large_local 全有効化、subprocess テストは
  default で `make test` から除外されるが、`make test-large-local` は緑）
- commit 6: `make check` 緑（docs 追加のみ）
- commit 7: `make check` 緑（docs 追加のみ）

各 commit 単独で revert 可能。dev repo 自身が壊れるリスクは PR-3d で先回り済。

### 8. legacy passthrough テストの扱い

`tests/test_cli_main.py:769 test_existing_pr_view_falls_back_to_passthrough`
は config 不在を mock で `None` 返却にして「`gh pr view 153 --comments` に
素通る」を検証していた。fail-fast 化後は同入力で exit 2 になる。

#### 決定

- 関数名を `test_existing_pr_view_fails_when_config_missing` に rename
- assertion を「subprocess.run が呼ばれず exit 2」「stderr に config.toml
  not found メッセージ」に書換

### 9. error message 文面の最終確定

fail-fast 化に伴う error message は phase3-design.md L246-275 の migration
ドラフトを **そのまま** 採用する。Phase 3-e で文面を再設計しない（user の
学習コスト最小化のため、ドラフト → 確定で文面は変えない）。

```
$ kaji issue view 1   # `[provider]` 不在
Error: [provider] section is required in .kaji/config.toml.

You must add the following to your .kaji/config.toml:

    [provider]
    type = "github"

    [provider.github]
    repo = "<owner>/<repo>"

For GitHub-independent (local-first) development, run `kaji local init`.
See `docs/cli-guides/local-mode.md`.
```

```
$ kaji issue view 1   # config.toml 不在
Error: .kaji/config.toml not found.

`kaji issue` / `kaji pr` require a kaji repository.
- For local-first: run `kaji local init` in your repo root.
- For GitHub: create `.kaji/config.toml` with [provider] section
  (see `docs/cli-guides/local-mode.md`).
```

文言は実装担当者がコード内に書き下した時点で確定。本書とコードの diff が
出る場合、コードを正本とする（本書はドラフト）。

## インターフェース

### CLI surface（変更なし）

`kaji local init` / `kaji issue` / `kaji pr` / `kaji run` / `kaji validate`
の引数仕様は本 Phase で変えない。挙動が変わるのは「`[provider]` 未設定」
「config.toml 不在」の error 化のみ。

### 内部 API の signature 変化

```python
# 旧（Phase 3-d）
def get_provider(config: KajiConfig) -> IssueProvider | None: ...
def _load_config_for_dispatch_or_none() -> KajiConfig | None: ...
def build_prompt(step, issue, state, workflow, issue_context: IssueContext | None = None) -> str: ...

# 新（Phase 3-e）
def get_provider(config: KajiConfig) -> IssueProvider: ...   # raises ValueError on no provider
def _load_config_for_dispatch() -> KajiConfig: ...           # raises ConfigNotFoundError / ConfigLoadError
def build_prompt(step, issue, state, workflow, issue_context: IssueContext | None = None) -> str: ...
                                                            # 変更なし（§ 2 の縮小判断、Phase 4 持ち越し）
```

```python
# kaji_harness/runner.py
@dataclass(frozen=True)
class RunIssueContext:
    input_id: str
    canonical_id: str
    issue_ref: str
    issue_context: IssueContext   # was IssueContext | None
```

### `pyproject.toml` markers

```toml
[tool.pytest.ini_options]
markers = [
    "small: Pure logic / no I/O",
    "medium: With file I/O / CliRunner",
    "large: With subprocess",
    "large_local: Subprocess but no external network (Phase 3-e+)",
    "large_forge: Real GitHub API (post-recovery)",
]
```

### `Makefile`

```makefile
test-large-local:
	pytest -m large_local
```

## 実装範囲

### 変更対象ファイル

| ファイル | 変更 | commit |
|----------|------|--------|
| `pyproject.toml` | pytest marker `large_local` / `large_forge` 追加 | 1 |
| `Makefile` | `test-large-local` ターゲット追加 | 1 |
| `kaji_harness/config.py` | `_parse_provider` 内で `machine_id` non-empty 時に `validate_machine_id` を呼ぶ | 2 |
| `tests/test_config_provider.py`（新規 or 既存拡張） | machine_id 文法違反の `ConfigLoadError` を Small + Medium で検証 | 2 |
| `tests/test_phase3e_large_local.py`（新規） | `fresh_repo` fixture（minimum config + 空 .gitignore）+ `kaji local init` / `kaji issue create/list/close` の subprocess テスト。fail-fast 系は `pytest.skip("enable in commit 5")` で保留（commit 4 までは旧挙動のため真値が異なる） | 3 |
| `kaji_harness/providers/__init__.py` | `_PROVIDER_FALLBACK_WARNED` / `_emit_provider_fallback_warning` 削除、`get_provider()` 戻り型 narrowing、`config.provider is None` で raise | 4 |
| `kaji_harness/cli_main.py` | `_load_config_for_dispatch_or_none` rename → `_load_config_for_dispatch`、`ConfigNotFoundError` propagate、`config is None` / `config.provider is None` 分岐削除、**`cmd_run` 冒頭で `get_provider(config)` を validation 目的で呼んで `ValueError` → `EXIT_INVALID_INPUT` に正規化（§ 1.5）** | 4 |
| `kaji_harness/runner.py` | `_resolve_issue_context` の `provider is None` 経路削除（必ず raise）、`RunIssueContext.issue_context` 型を Optional から required に narrowing | 4 |
| `kaji_harness/prompt.py` | **本 Phase では touch しない**（signature の Optional は Phase 4 持ち越し、§ 2 の縮小判断） | — |
| `tests/test_phase3c_dispatcher.py` | `test_no_provider_returns_none_with_warning` を `test_no_provider_raises_configerror` に書換、`_PROVIDER_FALLBACK_WARNED` 参照削除 | 4 |
| `tests/test_phase3c_runner.py` | `_PROVIDER_FALLBACK_WARNED` 参照削除、`provider is None` / `RunIssueContext.issue_context is None` を前提とした legacy 検証ケースのみ fail-fast 検証に置換。**`build_prompt(...)` を直接呼ぶ 4 件は touch 不要**（signature 変えないため） | 4 |
| `tests/test_phase3d_preflight.py` | `_PROVIDER_FALLBACK_WARNED` 参照削除（fail-fast 後は不要） | 4 |
| `tests/test_cli_main.py` | `test_existing_pr_view_falls_back_to_passthrough` を `test_existing_pr_view_fails_when_config_missing` に書換 | 4 |
| `tests/test_prompt_builder.py` | **touch 不要**（§ 2 の縮小判断、`build_prompt` signature 不変） | — |
| `tests/test_verdict_integration.py` | **touch 不要**（同上） | — |
| `tests/test_phase3e_large_local.py` | commit 3 で skip した fail-fast 系を `pytest.skip` 解除 | 5 |
| `CHANGELOG.md`（新規） | Keep a Changelog 形式、Phase 3 累積 + Phase 3-e BREAKING | 6 |
| `docs/cli-guides/local-mode.md` | migration セクション追加（fail-fast 化に伴う既存 user 移行手順） | 7 |
| `draft/design/local-mode/phase3-design.md` | § 4 ロールアウト戦略 PR-3e の達成記録、§ オープンな論点 から「`config.py` 側 fail-fast validation（machine_id）」を移動。`[default_branch]` placeholder 化は Phase 3-d で既に解決済 | 7 |
| `draft/design/local-mode/design.md` | § 移行・互換性 に「Phase 3-e リリースで fail-fast 化が確定した」旨の追記 | 7 |
| `draft/design/local-mode/phase3e-implementation-report.md`（新規） | 本 Phase 完了時に作成。Phase 3-d 報告と同形式 | 7 後 |

### 実装順序

§ 7 commit 1-7 を採用。副作用の小さい順（marker → validation 統合 → Large-local
雛形 → fail-fast 本体 → 既存テスト書換 → CHANGELOG → migration docs）。

## テスト戦略

### 変更タイプ

実行時コード変更（fail-fast）+ pytest 設定変更 + Large-local subprocess E2E
新規 + CHANGELOG / migration docs。

### Small テスト

- `validate_machine_id` を `_parse_provider` から呼ぶ路の round-trip
  （`pc1` accept / `PC1` reject / `pc-1` reject / 17 文字 reject / 空文字 accept）
- `get_provider(config)` が `config.provider is None` で `ValueError`、
  WARN を出さないこと（旧 WARN 関数が削除されている）
- `runner.RunIssueContext.issue_context` の型が required（dataclass field の
  default 値が無い）

### Medium テスト

- `KajiConfig.discover()` が `config.local.toml` に `machine_id = "PC1"` を
  含む場合 `ConfigLoadError` で停止
- CliRunner: `[provider]` 不在の `.kaji/config.toml` に対して `kaji issue
  view 1` が exit 2 + stderr に新メッセージ
- CliRunner: `.kaji/config.toml` 自体不在の repo で `kaji issue view 1` が
  exit 2 + stderr に「config.toml not found」
- CliRunner: `kaji pr view 153` が同様の fail-fast
- CliRunner: dev repo（`[provider.type = "github"]` あり）では `kaji issue
  view 153` が `--repo apokamo/kaji` を注入した `gh issue view` に転送
- CliRunner: `kaji run wf.yaml 1` が `[provider]` 不在 config で
  **exit 2**（§ 1.5 cmd_run 早期 fail-fast 経由、`HarnessError` で exit 3 に
  落ちないこと）
- CliRunner: `kaji run wf.yaml 1` が `.kaji/config.toml` 不在で exit 2
  （既存 `EXIT_CONFIG_NOT_FOUND` 経路）
- `runner.WorkflowRunner._resolve_issue_context()` が provider 不在 config に
  対して `IssueContextResolutionError` を raise（None を返さない）
- `_load_config_for_dispatch` が `ConfigNotFoundError` / `ConfigLoadError` を
  catch せず propagate し、呼出側 dispatcher で exit 2 になること
- 既存テスト `test_existing_pr_view_*` が新挙動に追従

### Large-local テスト（`tests/test_phase3e_large_local.py`、新規）

詳細は § 4。subprocess 起動でかつ外部通信なし。`fresh_repo` fixture で
minimum `.kaji/config.toml`（paths + execution、provider なし）を準備する。
**`kaji validate` は本テストから除外**（§ 4 参照、Phase 3-d Medium で済）。

- `kaji local init` smoke（`fresh_repo`、exit 0、`.kaji/config.local.toml`
  生成物 / `.gitignore` 追記の確認）
- `kaji local init` 二重実行 exit 3
- `kaji local init --machine-id PC1` exit 2（validate_machine_id）
- `kaji local init --machine-id pc-1` exit 2（hyphen 拒否）
- `kaji local init --default-branch develop` overlay 内容確認
- `kaji issue create` 後 frontmatter slug / branch_prefix / state 確認
- `kaji issue list` stdout に local id 出現
- `kaji issue close` 後 `close_reason: completed`
- fail-fast: `[provider]` 不在で subprocess `kaji issue view 1` exit 2
- fail-fast: `[provider]` 不在で subprocess `kaji pr view 153` exit 2
- fail-fast: `[provider]` 不在で subprocess `kaji run wf.yaml 1` **exit 2**
  （HarnessError 経由で exit 3 に落ちないこと、§ 1.5 の正規化が効いている確認）
- fail-fast: `.kaji/config.toml` 不在で subprocess `kaji issue view 1` exit 2
- fail-fast: `.kaji/config.toml` 不在で subprocess `kaji run wf.yaml 1` exit 2

### 既存テストの維持

- Phase 3-c `tests/test_phase3c_*.py`: fallback 経路テストは書換、それ以外維持
- Phase 3-d preflight `tests/test_phase3d_preflight.py`: `_PROVIDER_FALLBACK_WARNED`
  参照のみ削除、それ以外維持
- Phase 3-d `tests/test_phase3d_default_branch.py` / `tests/test_phase3d_skills.py`
  / `tests/test_local_init.py`: 全て維持

## 影響ドキュメント

| ドキュメント | 影響 | 対応 |
|--------------|------|------|
| `CHANGELOG.md` | 新規 | Keep a Changelog 形式、Phase 3 累積 + Phase 3-e BREAKING |
| `docs/cli-guides/local-mode.md` | 軽微 | migration セクション（既存 user の Phase 3-e 移行手順）を末尾に追加 |
| `draft/design/local-mode/phase3-design.md` | 軽微 | § 4 ロールアウト戦略 PR-3e に「達成」マーク、§ オープンな論点 から本 Phase で解決した項目を削除 |
| `draft/design/local-mode/design.md` | 軽微 | § 移行・互換性 に「Phase 3-e で fail-fast 化が確定」旨の追記 |
| `draft/design/local-mode/phase3e-implementation-report.md` | 新規 | 実装後に作成 |
| 既存 `phase3d-implementation-report.md` の「次フェーズへの申し送り」 | 確認のみ | Phase 3-e 完了で「fail-fast / Large-local / config validation 統合」の 3 項目が解消 |

## 受け入れ条件

### 機械検証

- [ ] `make check` PASS（commit 1-7 各時点で）
- [ ] `make test-small` PASS
- [ ] `make test-medium` PASS
- [ ] `make test-large-local` PASS（新規 ターゲット、subprocess E2E）
- [ ] `make test-large` PASS（既存、`large_local` を含むため拡大）
- [ ] `mypy kaji_harness/` PASS
- [ ] `kaji_harness.providers.get_provider(config)` が `config.provider is None`
      で `ValueError` を raise（戻り型 `IssueProvider`、Optional 解除）
- [ ] `_PROVIDER_FALLBACK_WARNED` / `_emit_provider_fallback_warning` が
      `kaji_harness.providers.__init__` から完全に削除されている
- [ ] `kaji_harness.cli_main._load_config_for_dispatch` の戻り型が `KajiConfig`
      （Optional 解除）、`ConfigNotFoundError` を catch しない
- [ ] `kaji_harness.cli_main.cmd_run` 冒頭で `get_provider(config)` を呼び、
      `ValueError` を `EXIT_INVALID_INPUT` に正規化している（§ 1.5）
- [ ] `kaji_harness.runner.RunIssueContext.issue_context` の型が `IssueContext`
      （Optional 解除）
- [ ] `kaji_harness.prompt.build_prompt` の signature は **変更されていない**
      （§ 2 縮小判断、Phase 4 持ち越し）
- [ ] `KajiConfig.discover()` が `provider.local.machine_id = "PC1"` の overlay
      に対し `ConfigLoadError` を raise
- [ ] `pyproject.toml` に `large_local` / `large_forge` marker が登録されている
- [ ] `Makefile` に `test-large-local` ターゲットが存在
- [ ] `CHANGELOG.md` がリポジトリ root に存在し、`### BREAKING CHANGE` セクションを
      含む（`[provider]` 必須化、`.kaji/config.toml` 必須化、`machine_id` validation 統合）
- [ ] subprocess: `kaji issue view 1` が `[provider]` 不在 config に対し
      exit 2、stderr に「[provider] section is required」
- [ ] subprocess: `kaji issue view 1` が `.kaji/config.toml` 不在 cwd で
      exit 2、stderr に「.kaji/config.toml not found」
- [ ] subprocess: `kaji run feature-development-local.yaml 1` が `[provider]`
      不在 config で **exit 2**（§ 1.5 cmd_run 早期 fail-fast、HarnessError
      経由 exit 3 ではないこと）
- [ ] subprocess: `kaji run` が `.kaji/config.toml` 不在で exit 2
- [ ] dev repo（`apokamo/kaji` 自身）の `kaji issue view <既存 number>` が
      Phase 3-e の commit 群を全て載せた状態で従来通り `gh` に転送される
      （Phase 3-d で `[provider]` を commit 済のため壊れない）

### 手動確認

- [ ] dev repo で `kaji issue view 1` が従来通り動作（破壊が来ていないことの保険）
- [ ] tmp 用空ディレクトリで `kaji issue view 1` が新エラーメッセージで停止
- [ ] `kaji local init` でセットアップした overlay-only の repo で `kaji issue
      create` / `kaji issue list` / `kaji issue close` が完走
- [ ] `make test-large-local` が外部通信なしで完走（CI の network 制限環境で
      実行できることを確認）
- [ ] `CHANGELOG.md` の文面が migration の手順をそのまま実行できる粒度になっている
      （文面の通りに `.kaji/config.toml` を編集すれば既存 GitHub 運用に戻せる）

### ドキュメント

- [ ] `CHANGELOG.md` が `## [Unreleased]` セクションを持ち、Phase 3-e の
      BREAKING CHANGE / Migration / Added / Changed / Removed を整理
- [ ] `docs/cli-guides/local-mode.md` 末尾に「Phase 3-e migration」セクション
      が存在し、既存 GitHub user / 既存 local user 双方の手順をカバー
- [ ] `draft/design/local-mode/phase3-design.md` § 4 ロールアウト戦略 PR-3e に
      実装済の記録、§ オープンな論点 から `validate_machine_id` config 統合 /
      `[default_branch]` placeholder 化 を削除
- [ ] `draft/design/local-mode/design.md` § 移行・互換性 に Phase 3-e で
      fail-fast 化が確定した旨の追記
- [ ] `draft/design/local-mode/phase3e-implementation-report.md` が作成され、
      commit 1-7 と受け入れ条件チェックがレコードされている

## Rollback 方針

| 変更 | rollback |
|------|----------|
| commit 1: pytest marker / Makefile | revert で完全戻し（既存テストへの影響なし） |
| commit 2: `validate_machine_id` config 統合 | revert で完全戻し。validation が config load から外れるが既存挙動に戻るのみ |
| commit 3: Large-local テスト雛形 | revert で削除 |
| commit 4: fail-fast 本体 + 既存テスト書換 | revert で fallback 復活。dev repo は無事だが、Phase 3-e 全体が振り出しに戻る |
| commit 5: Large-local fail-fast ケース enable | revert で skipif 復活 |
| commit 6: CHANGELOG | revert で削除 |
| commit 7: docs / 影響設計書補正 | revert で削除 |

各 commit を独立 revert 可能な粒度に保つ。Phase 3-d の dogfooding commit
（`820984f`）が main に入っていることが本 Phase の前提。Phase 3-e merge 後に
dev repo を破壊する revert を要求された場合は、commit 4-5 を一括 revert する
（fallback が復活して legacy 経路に戻る）。

## 判断済み論点

実装担当者が再判断しなくてよい論点を明示する。

| 論点 | 判断 | 根拠 |
|------|------|------|
| `[provider]` 未設定 fallback の削除タイミング | 本 Phase で確定 | phase3-design.md § 4 PR-3e、design.md L1283 |
| `ConfigNotFoundError` 経路の legacy passthrough | 同時に削除 | 「`[provider]` 必須化」と「リポジトリ外で `gh` 透過」が両立すると規範が不整合になる、CHANGELOG / migration が一回にまとまる |
| `_load_config_for_dispatch_or_none` の rename | `_load_config_for_dispatch` に変更 | `_or_none` 接尾辞は意味的不整合（戻り型 narrowing 後）、API 命名の clarity |
| `IssueContext is None` 互換ブランチの扱い | `runner.RunIssueContext.issue_context` の Optional **のみ** 削除。`prompt.build_prompt` の signature と内部分岐は **本 Phase では touch しない**（Phase 4 申し送り）| レビュー MF2 反映。required 化すると `test_prompt_builder.py` 15 件 / `test_verdict_integration.py` 1 件 / `test_phase3c_runner.py` 4 件で test fixture 整備が必要、Phase 3-e 主旨を阻害する。本番経路は preflight 経由で必ず IssueContext を渡すため挙動上のリスクなし |
| `kaji run` の exit code 正規化 | `cmd_run` 冒頭で `get_provider(config)` を呼び `ValueError` を `EXIT_INVALID_INPUT` に正規化 | レビュー MF1 反映。`IssueContextResolutionError`（HarnessError 派生）が `EXIT_RUNTIME_ERROR=3` に落ちることを避け、「config / provider 設定の問題は exit 2、Issue 解決の問題は exit 3」契約を確定 |
| Large-local の `kaji validate` テスト | **削除**。Phase 3-d で `tests/test_local_init.py` 周辺の Medium テストで dev repo の実 yaml に対し済 | レビュー MF3 反映。`kaji validate` は config + skill ディレクトリ全体の fixture コピーが必要で、Large-local の主目的（CLI subprocess + filesystem 動作確認）から外れる |
| Large-local の tmp repo セットアップ | `fresh_repo` fixture で minimum `.kaji/config.toml`（paths + execution、provider なし）+ 空 `.gitignore` を準備 | `kaji local init` は overlay しか作らないため、subprocess 起動前に tracked config と `.gitignore` の土台を fixture で準備する必要がある |
| `validate_machine_id` の config.py 統合 | 本 Phase で実施 | phase3d-design.md § 6 で「Phase 3-e の fail-fast 化と一体検討」と明示、手書き overlay の罠を構造で防ぐ |
| `validate_default_branch` の config.py 統合 | 本 Phase では実施しない | 影響が即死しない、既存 config への false positive リスク、保守的に保留 |
| `validate_machine_id` を空文字に対しては skip | non-empty 時のみ呼ぶ | `type = "github"` + 空 `[provider.local]` が正常ケース、二重 validation を回避 |
| Large-local の範囲 | kaji CLI subprocess + filesystem 確認のみ、agent CLI 起動は別 | API コスト発生、buildout 中の自動化を避ける、`make test-large-forge` で別途扱う |
| Large-local marker | `large_local` を新設、`large` の subset | design.md § 検証戦略 § Large の分割と整合、`make test-large-local` で外部通信なし subset を独立に回せる |
| CHANGELOG の配置 | ルート `CHANGELOG.md` 新規作成、Keep a Changelog 形式 | GitHub releases / `gh release create --notes-file` の慣習、`docs/CHANGELOG.md` ではなくルートが標準 |
| CHANGELOG のスコープ | Phase 3 累積（3-a / 3-b / 3-c / 3-d / 3-e）+ Phase 3-e BREAKING | リポジトリに既存 CHANGELOG が無い、Phase 3 を 1 リリース単位でくくる |
| error message 文面 | phase3-design.md L246-275 のドラフトをそのまま採用 | user の学習コスト最小化、ドラフト → 確定で文面を変えない |
| dev repo dogfooding 確認 | commit 入れる前に `git diff main -- .kaji/config.toml .gitignore` で差分ゼロ確認のみ、touch しない | Phase 3-d で commit 済（`820984f`）、本 Phase で再 touch すると revert 単位が壊れる |
| commit 順序 | 副作用小から大へ（marker → validation → Large 雛形 → fail-fast 本体 → Large enable → CHANGELOG → docs） | 各 commit で `make check` 緑、独立 revert |
| 実 agent CLI E2E（design → implement → close） | 本 Phase out-of-scope、Phase 3-d の手動確認に対応 | API コスト、buildout 中の自動化を避ける |

## オープンな論点

本 Phase で持ち越す論点（Phase 4-5 もしくは別 ADR で扱う）:

- `provider.local.default_branch` の git ref-format validation を config load
  時に統合するか（本 Phase は machine_id のみ統合）
- `provider.github.repo` の owner/name regex validation を config load 時に
  統合するか
- `make test-large-forge` 新設のタイミング（GitHub 復旧 + Phase 5 の `kaji
  sync from-github` 実装と一体）
- `CHANGELOG.md` のリリースタグ運用（本 Phase は `[Unreleased]` のまま、
  Phase 4 / 5 のどこかで `[v0.x.x]` の git tag を切るタイミング）
- `IssueContext` を flatten 後 `issue_id` / `issue_ref` の `prompt.py` 内
  上書きを「常に context 由来」に正規化することの追加変更（本 Phase では
  既存挙動を維持し、互換性のみ削除）
- `kaji issue` を kaji リポジトリ外で `gh` ラッパとして使いたい user の
  alternative path（本 Phase で legacy passthrough を削除する判断、user 側は
  `gh` 直接利用に戻す）
- **`build_prompt` の `IssueContext` required 化**（レビュー MF2 で本 Phase
  から外した分）。Phase 4 で `test_prompt_builder.py` / `test_verdict_integration.py`
  / `test_phase3c_runner.py` の `build_prompt` 呼び出し 20+ 件に共通 fixture
  経由で `issue_context` を渡し、signature の Optional と prompt.py 内部の
  `if issue_context is not None:` ブランチを構造的に削除する

## 工数見積

| Step 群 | 内容 | 見積 |
|---------|------|------|
| commit 1 | pytest marker / Makefile | 0.1 日 |
| commit 2 | `validate_machine_id` config 統合 + テスト | 0.25 日 |
| commit 3 | Large-local テスト雛形（fixture + 非 fail-fast 系、fail-fast 系は skip） | 0.5 日 |
| commit 4 | fail-fast 本体（providers/cli_main/runner の touch + cmd_run 早期 fail-fast + 既存テスト書換 4 ファイル）| 0.6 日 |
| commit 5 | Large-local fail-fast 系 skip 解除 | 0.1 日 |
| commit 6 | `CHANGELOG.md` 新規作成 | 0.25 日 |
| commit 7 | migration docs + 影響設計書補正 | 0.25 日 |
| 予備 | レビュー対応 / 追加バグ修正 | 0.5 日 |
| **合計** | | **2.5 日** |

phase3-design.md § 工数見積（Step 18 = 0.15 日 + Step 19 = 0.75 日 + Step 20
= 0.25 日 = 1.15 日）に対し、Phase 3-d で持ち越されたデバッグ余地（Large-local
の fail-fast 前後の二段構成、CHANGELOG 形式整備、既存テスト書換の影響面）
を見込んで 2.5 日に修正。Phase 3-d の 6.15 日見積より大幅に小さく、本 Phase で
Phase 3 全体（3-a / 3-b / 3-c / 3-d / 3-e）を完了する。

## 参照情報（Primary Sources）

| 情報源 | パス / URL | 根拠 |
|--------|------------|------|
| Phase 3 設計 | `draft/design/local-mode/phase3-design.md` | § 4 ロールアウト戦略 PR-3e、§ 受け入れ条件、§ オープンな論点 |
| Phase 3-d 設計 | `draft/design/local-mode/phase3d-design.md` | § 6 `validate_machine_id` 所在 / Phase 3-e 統合の申し送り、§ 判断済み論点 |
| Phase 3-d 実装報告 | `draft/design/local-mode/phase3d-implementation-report.md` | § 次フェーズへの申し送り（fail-fast / Large-local / config validation 統合） |
| Phase 3-c 実装報告 | `draft/design/local-mode/phase3c-implementation-report.md` | dispatcher 切替、`get_provider` factory、`_load_config_for_dispatch_or_none` の review #3 経緯 |
| 親設計（移行・互換性） | `draft/design/local-mode/design.md` L1279-1346 | 既存 config の移行手順、Phase 3 リリース時の fail-fast 化、CHANGELOG / release notes での明示告知 |
| 親設計（検証戦略） | `draft/design/local-mode/design.md` L1433-1476 | Large-local / Large-forge の分割、`make test-large-forge` 新設の方針 |
| 現行 fallback コード | `kaji_harness/providers/__init__.py:38-79` | `_PROVIDER_FALLBACK_WARNED` / `_emit_provider_fallback_warning` / `get_provider` 中の None 返却 |
| 現行 dispatcher | `kaji_harness/cli_main.py:589-679` | `_handle_pr` / `_handle_issue` の `config is None` / `config.provider is None` 分岐 |
| 現行 dispatcher helper | `kaji_harness/cli_main.py:629-642` | `_load_config_for_dispatch_or_none` の `ConfigNotFoundError → None` 経路 |
| 現行 runner | `kaji_harness/runner.py:35-145` | `RunIssueContext.issue_context: IssueContext \| None`、`_resolve_issue_context` の `provider is None` 経路 |
| 現行 prompt | `kaji_harness/prompt.py` | `build_prompt(issue_context: IssueContext \| None = None)` の Optional |
| 現行 config parser | `kaji_harness/config.py:145-235` | `_parse_provider`、`machine_id` の `isinstance` のみ検証 |
| 現行 `validate_machine_id` | `kaji_harness/providers/local.py:59` | `[a-z0-9]{1,16}` regex 実装 |
| dev repo config | `.kaji/config.toml` | Phase 3-d で `[provider.type = "github"]` + `[provider.github] repo = "apokamo/kaji"` + `default_branch = "main"` 追記済 |
| dev repo .gitignore | `.gitignore:58` | `.kaji/config.local.toml` 追記済 |
| local-mode guide | `docs/cli-guides/local-mode.md` | Phase 3-d で新規作成、Phase 3-e で migration セクション追加 |
| pytest 設定 | `pyproject.toml [tool.pytest.ini_options]` | marker 登録、`large_local` / `large_forge` の追加 |
| Makefile | `Makefile` | `test-large-local` ターゲット追加 |
| 既存 large テスト | `tests/test_e2e_cli.py` | `@pytest.mark.large` の前例（real CLI 起動、skipif `_cli_available`） |
| 既存 fallback テスト | `tests/test_phase3c_dispatcher.py:105-118` / `tests/test_cli_main.py:769-787` / `tests/test_phase3c_runner.py:234` / `tests/test_phase3d_preflight.py:103` | 書換対象 |

## 完了条件の段階確認

- [x] Phase 3-d までの到達点を本書 § 背景・目的 に明示した
- [x] Phase 3-e で実装する 12 項目を § スコープ in-scope に列挙した
- [x] fallback 削除の境界（`provider` 未設定 / config 不在の両方）を § 1 で確定した
- [x] `IssueContext is None` 互換ブランチの削除を § 2 で確定した
- [x] `validate_machine_id` の config.py 統合を § 3 で確定した
- [x] Large-local テストの範囲（kaji CLI subprocess + filesystem 確認のみ）と
      marker 戦略を § 4 で確定した
- [x] CHANGELOG.md の配置（ルート、Keep a Changelog）と内容ドラフトを § 5 で確定した
- [x] dev repo dogfooding は touch せず確認のみ、を § 6 で固定した
- [x] PR-3e 内の commit 順序を 7 commit に分割し、副作用小から大へ並べた
- [x] error message 文面を phase3-design.md L246-275 ドラフトに固定した
- [x] legacy passthrough テスト（`test_existing_pr_view_falls_back_to_passthrough`）の
      書換を § 8 で確定した
- [x] 受け入れ条件を機械検証 / 手動確認 / ドキュメントの 3 区分で列挙した
- [x] 判断済み論点を 17 項目で明示し、実装担当者の再判断負荷を軽減した
- [x] 参照情報を Primary Source として 17 項目列挙した
- [x] 工数見積を 2.55 日とし、Phase 3 全体を本 Phase で完了させる位置づけを明示した
- [x] レビュー v2 反映: cmd_run の exit code 正規化（MF1）、build_prompt
      signature 維持の縮小判断（MF2）、Large-local fixture と `kaji validate`
      除外（MF3）、9 context variables への修正（SF4）、commit 4/5 責務分離
      （SF5）を § 0 〜 § 7 に反映した
