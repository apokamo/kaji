---
status: implemented
phase: 3c
parent: phase3-design.md
created: 2026-05-06
revised: 2026-05-06
branch: feat/local-phase3c
---

# [実装報告] kaji local mode — Phase 3-c: dispatcher 切替 + IssueContext 注入

> **Revision 2026-05-06 (#1)** — 初版に対するレビュー（Must / Should 4 項目）
> を反映。`normalize_id` 経由の id 解決、Skill 利用 CLI フラグの全受理、
> config 例外の fail-fast 化、`config.local.toml` overlay の `[provider]`
> 全体対応を加えた。テストも 15 → 36 件に拡張。
>
> **Revision 2026-05-06 (#2)** — 続くレビューで「`-q '.body'` が
> quote 付き出力で `CURRENT_BODY=$(...)` 系を壊す」と指摘された。
> `_apply_jq` を `jq -r` 採用に修正（`gh --jq` の raw 出力互換）。
> 既存テストを exact match 化、subshell 取り込みの round-trip テストも
> 追加。テスト 36 → 39 件、`pytest` 全体は 817 → 820 passed。詳細は § 11。
>
> **Revision 2026-05-06 (#3)** — さらに 3 項目が指摘された。(1) `runner.py`
> の context 解決が `normalize_id` を経由せず `kaji run ... 1` /
> `kaji run ... pc1-1` で 5 変数注入が壊れていた。(2) 明示 provider 設定
> 下でも IO エラー等を WARN + legacy fallback で握りつぶしていた。
> (3) GitHubProvider 経路で `[provider.github] repo` を捨てて gh の cwd
> 推論に委ねていた。(1) は normalize_id 経由化、(2) は
> `IssueContextResolutionError` で fail-fast、(3) は `_forward_to_gh` /
> `_detect_repo` に override を追加して `--repo` を強制注入。テスト 39 →
> 53 件、`pytest` 全体は 820 → 834 passed。詳細は § 11。

phase3-design.md § 4 ロールアウト戦略 PR-3c に対応する実装報告。GitHub
利用不能環境のため PR は作成せず、本書で作業内容と検証結果を記録する。

## 1. 作業ブランチ

- `feat/local-phase3c` （`main` 5bbe0a5 から分岐）
- worktree: `/home/aki/dev/kaji/kaji-feat-local-phase3c`

## 2. 完了した範囲（phase3-design.md Step 11–13）

| Step | 内容 | 状態 |
|------|------|------|
| 11 | `cli_main.py` を `get_provider` 経由に切替（fallback あり） | 完 |
| 12 | `prompt.py` を `IssueContext` 注入へ切替（5 変数） | 完 |
| 13 | dev repo 動作確認用の test 整備 + `make check` 緑 | 完 |

PR-3c の出口要件（design.md L613）「fail-fast 未発動、dev repo 動作確認」
を満たす。`provider.type` 未設定の dev repo（kaji 本体）でも従来通り動作
する fallback 経路を残している。Phase 3-e で fallback 削除予定。

## 3. 変更ファイル（rev 2026-05-06）

```
kaji_harness/cli_main.py           +312 / -1   ※ _handle_issue / Local CRUD dispatcher
                                                normalize_id 経由 id 解決 + 全フラグ受理
                                                + jq subprocess + fail-fast 例外
kaji_harness/config.py             +120 / -3   ※ ProviderConfig parse +
                                                [provider] 全体 overlay
kaji_harness/prompt.py             +23  / -2   ※ build_prompt(issue_context=...)
kaji_harness/providers/__init__.py +76  / -1   ※ get_provider() factory + WARN
kaji_harness/runner.py             +40  / -3   ※ IssueContext 解決と build_prompt 連携
tests/test_phase3c_dispatcher.py   +565 (新規)  ※ 36 medium/small tests
                                                （初版 15 → +21 件 review 対応）
```

## 4. 設計上の決定（phase3-design.md からの差分）

### 4.1 `get_provider` の戻り値を `IssueProvider | None` にする

phase3-design.md L296-310 の擬似コードでは `get_provider(config)` は
provider を返す前提だが、`provider.type` 未設定時に GitHub fallback
provider を返すと `repo` 不在で `gh --repo ""` の broken な引数列に
なる。

採用方針: `get_provider` は `provider` 未設定時に `None` を返し、WARN
を 1 度だけ stderr に出す。呼び出し側（`_handle_issue` / `_resolve_issue_context`）
は `None` を Phase 2-B 互換の legacy 経路に解釈する。

`_emit_provider_fallback_warning` は process-wide flag（`_PROVIDER_FALLBACK_WARNED`）
で 1 度きりに抑制する。テストではフラグを reset して挙動を検証する。

### 4.2 `_handle_issue` の dispatch 構造（rev で fail-fast 化）

```
kaji issue <args>
  └→ _load_config_for_dispatch_or_none()
       ├─ ConfigNotFoundError → None（legacy fallback OK）
       └─ ConfigLoadError     → exit 2（壊れた TOML / 未知 type）  ← review #3
  └→ provider 設定済 →
       ├─ get_provider() raise ValueError → exit 2（machine_id 不在等） ← review #3
       ├─ LocalProvider                    → _handle_issue_local(provider, args)
       └─ GitHubProvider                   → _forward_to_gh("issue", args)
  └→ provider 未設定 → WARN + _forward_to_gh("issue", args)
```

旧版の `_try_load_config_for_dispatch` は `ConfigLoadError` を握りつぶして
gh fallback していたため、`type=gitlab` / 壊れた TOML が silent に GitHub
に流れる契約違反があった。`get_provider` の `ValueError`（machine_id /
repo 不在）も catch されず CLI が traceback していた。両方とも user-facing
error で exit 2 を返すよう修正（テスト
`TestDispatcherFailFastOnConfig::test_invalid_provider_type_yields_exit_2_not_silent_fallback`
で構造的に検証）。

`kaji pr` は本 PR では provider 切替を行わない（Phase 4 で `pr-fix` /
`pr-verify` の bare-provider 化と一体実施するため、phase3-design.md
§ scope 参照）。

### 4.3 `prompt.build_prompt` のシグネチャ拡張

```python
def build_prompt(
    step, issue, state, workflow,
    issue_context: IssueContext | None = None,
) -> str:
```

`issue_context` が None の場合は Phase 2-B 互換の 2 変数（`issue_id` /
`issue_ref`）のみ注入。指定された場合は追加で 5 変数（`issue_input` /
`branch_prefix` / `branch_name` / `worktree_dir` / `design_path`）を
注入する。phase3-design.md § 1 で確定した「9 変数体系のうち Issue 系
7 変数」に対応。

`issue_context` 指定時は `issue_id` / `issue_ref` も `IssueContext`
由来の値を採用する（local の machine_id 補完を尊重）。

### 4.3.1 `_handle_issue_local` の id 解決と CLI フラグ（rev で大幅拡張）

初版では `provider.view_issue(ns.issue_id)` に user 入力を直接渡していたが、
review #1 で「`153` / `pc1-3` / `gh:153` を扱う設計契約に違反」と指摘された
ため全面改修した。

**id 解決経路**（`_resolve_local_id`）:

```python
rid = normalize_id(raw, provider_name="local", machine_id=provider.machine_id)
# kind == "local"        → provider.view_issue(rid.value)
# kind == "remote_cache" → provider.view_cached_issue(rid.value)（read-only）
# write 系 + remote_cache → 'cannot modify, gh:N is read-only' で exit 2
```

| 入力形式 | 解決先 | write 可否 |
|---------|--------|-----------|
| `153` | `local-pc1-153`（machine_id 補完） | 〇 |
| `pc1-3` | `local-pc1-3` | 〇 |
| `local-pc1-3` | そのまま | 〇 |
| `gh:153` | cache JSON `.kaji/cache/issues/153.json` | × （read-only） |
| `Bogus-ID` | `ValueError` → exit 2 | — |

`view` で `gh:N` を呼ぶと `IssueNotFoundError`（cache 不在）または
read-only Issue を返す。`edit` / `comment` / `close` で `gh:N` を呼ぶと
明示エラーで exit 2。

**Skill 利用フラグ**（review #2 対応、phase3-design.md L573 「Skill が現在
使っているフラグはすべて受理」契約）:

| フラグ | 対象 sub | 動作 |
|--------|---------|------|
| `--json FIELDS` | view / list | gh 互換の JSON 投影（dict / list 形式） |
| `--jq EXPR` / `-q EXPR` | view / list | system `jq` への subprocess pipe（**Phase 3-d preflight § 2 で Python `jq` package へ移行**） |
| `--comments` | view（plain） | 本文に続いてコメントを `---` 区切りで連結 |
| `--body STRING` | create / edit / comment | 直値 |
| `--body-file PATH` | create / edit / comment | ファイル読み込み（`-` で stdin） |
| `--label LABEL` | create | 追加可（複数指定対応） |
| `--add-label / --remove-label` | edit | 既存 mapping への delta |
| `--state` / `--label` / `--limit` | list | gh 互換 |

**`jq` 依存**（Phase 3-c 時点）: Python パッケージ依存にせず、system tool として
`shutil.which("jq")` で検出 + 不在時 exit 3（明示メッセージ付き）。`pyproject.toml`
には記載しない（Skill が依存する jq 評価は GitHub provider 側でも `gh --jq`
経由で system jq に依存するため対称）。

> **Phase 3-d preflight § 2 で撤回（2026-05-06）**: 上記の system `jq` 採用は、
> local mode の自己完結性を弱める（fresh install で `kaji issue view ... -q '.body'`
> が動かない）ため Phase 3-d preflight で破棄し、PyPI `jq` package を runtime
> dependency へ追加した。詳細は `phase3d-preflight-design.md` および
> `phase3d-preflight-implementation-report.md` を参照。

**`jq -r` 採用（rev #2 で修正）**: `gh --jq` は内部で gojq を使い、
結果が string なら raw 出力（`jq -r` 相当）。Skill 群は
`CURRENT_BODY=$(kaji issue view N --json body -q '.body')` で shell 変数
に raw 値を期待するため、`jq` 単体では `"body text"` のような quote 付き
出力で下流が壊れる。`["jq", "-r", expr]` で起動することで以下を成立させる:

| 入力 | 期待挙動（gh 互換） | rev #2 後の検証 |
|------|--------------------|----------------|
| `.body`（string） | `body text\n`（raw） | `captured.out == "body text\\n"` exact match |
| `[.labels[].name]`（array） | JSON pretty | `json.loads(captured.out) == [...]` |
| `.labels[].name`（string stream） | 各行 raw | `splitlines() == ["a", "b"]` |
| 実 subshell ``$(... -q '.body')`` | quote 無し | subprocess + `rstrip("\n")` で round-trip 検証 |

### 4.4 `WorkflowRunner._resolve_issue_context` の fail-fast vs fallback

phase3-design.md L322 は「IO エラーで失敗した場合は kaji run 自体を
fail-fast。半端な `IssueContext` で Skill を起動しない」と規定するが、
これは PR-3e（fail-fast 化）以降の振る舞い。PR-3c の段階では既存の
dev repo / 既存テスト互換を保つため、解決失敗時は WARN を出して
`None` を返す（→ Skill は Phase 2-B 互換の 2 変数で動作する）。

`_resolve_issue_context` 内で例外を `Exception` で広く捕まえているのは
fallback 段階の意図的な広範囲トラップ。PR-3e で `try/except` を外し、
`get_provider` の None 戻りも禁止することで fail-fast 化する。

### 4.5 `[provider]` config の optional 化と overlay（rev で範囲拡張）

`KajiConfig._parse_provider`:

- `[provider]` セクションが tracked / overlay の双方に不在 → `provider=None`
  （Phase 3-c の fallback 経路）
- `[provider] type = "github"` → `[provider.github] repo` 必須（`get_provider` 側で fail-fast）
- `[provider] type = "local"` → `[provider.local] machine_id` 必須（`get_provider` 側で fail-fast）
- `[provider] type` が `github` / `local` 以外 → `ConfigLoadError`（fail-fast）

`config.local.toml` overlay（review #4 対応で範囲を拡張）:

- 同階層 `.kaji/config.local.toml` の **`[provider]` 全体**（`type` /
  `[provider.github]` / `[provider.local]`）を tracked と deep-1 merge する
- tracked が `[provider] type = "github"` でも overlay で `type = "local"` に
  切替可能（個人 PC で local 主運用、CI / 共有環境で github を維持）
- tracked に `[provider]` が無くても overlay 単独で導入可能（ロールアウト過渡
  期の dogfooding に有効）
- `.gitignore` に `.kaji/config.local.toml` を追加する作業は phase3-design.md
  Step 14 の `kaji local init` 実装と一体で PR-3d に持ち越し（本 PR では
  config 解析側のみ）

## 5. テスト整備

### 5.1 新規 `tests/test_phase3c_dispatcher.py`（rev 後 36 件）

**`TestProviderConfigParsing` (medium, 4 件)**
- `[provider]` 不在 → `cfg.provider is None`
- `[provider] type = github` の正常 parse
- `config.local.toml` overlay で machine_id を上書き
- `type = "gitlab"` を `ConfigLoadError` で reject

**`TestGetProviderRouting` (medium, 5 件)**
- `provider=None` → WARN + `None` 返却
- `type=github` → `GitHubProvider` 返却（repo 注入確認）
- `type=local` → `LocalProvider` 返却（machine_id 注入確認）
- `type=local` で machine_id 不在 → `ValueError`
- `type=github` で repo 不在 → `ValueError`

**`TestHandleIssueDispatch` (medium, 4 件)**
- provider 未設定の repo で `kaji issue view 42` → `gh issue view 42` に
  passthrough（subprocess.run 引数を検証）
- `type=github` でも passthrough を維持（phase3-design.md L611 互換性）
- `type=local` で `kaji issue view local-pc1-1` → gh を呼ばずに
  LocalProvider 経由で本文を出力（subprocess.run 未呼出を assert）
- `type=local` で `kaji issue create --slug ... --label ...` →
  `local-pc1-1` を採番、`kaji issue close local-pc1-1` で state=closed に

**`TestPromptIssueContextInjection` (small, 2 件)**
- `issue_context=None` → 2 変数のみ注入、5 変数は不在
- `issue_context=IssueContext(...)` → 7 変数すべて注入

**`TestLocalDispatcherIdNormalization` (medium, 7 件) — rev 追加**
- `view 1` が `local-pc1-1` に解決される（machine_id 補完）
- `view pc1-1`（短縮形）→ 解決
- `view local-pc1-1` → 解決
- `view gh:153` cache 不在 → exit 3 + 「no cached issue」
- `view gh:153` cache 投入後 → read-only に表示
- `edit/close/comment gh:153` → exit 2 + 「read-only」
- `view Bogus-ID` → exit 2 + 「invalid issue id」

**`TestLocalDispatcherFlags` (medium, 8 件) — rev 追加**
- `view --json body -q '.body'` （Skill `i-doc-final-check` 等が利用）
- `view --jq '.title'` 単独 → full Issue JSON を入力に評価
- `view --jq` で jq 不在 → exit 3 + ガイダンス（pyproject 非依存契約の検証）
- `view --comments` で本文 + コメント連結出力
- `create --body-file path.md` でファイル本文
- `comment --body-file -` で stdin 本文
- `--body` と `--body-file` の同時指定 → exit 2 + 「mutually exclusive」
- `list --json labels --jq '.[0].labels[0].name'`（Skill `issue-implement` 風）

**`TestDispatcherFailFastOnConfig` (medium, 4 件) — rev 追加**
- `type=gitlab` → exit 2 + gh subprocess 不発呼（silent fallback しない）
- 壊れた TOML → exit 2
- `type=local` で `machine_id` 不在 → exit 2（traceback ではない）
- `type=github` で `repo` 不在 → exit 2

**`TestConfigLocalOverlayProviderType` (medium, 2 件) — rev 追加**
- tracked が `type=github` でも overlay で `type=local` に切替できる
- tracked に `[provider]` 不在でも overlay 単独で導入できる

### 5.2 既存テストの維持

PR-3a/3b で導入された 781 件 + 1 skip は全て緑のまま。`prompt.py` /
`runner.py` の変更は既定値 `issue_context=None` で挙動不変なので、
`test_prompt_builder.py` / `test_workflow_execution.py` 等は無修正で
パスする。

### 5.3 `make check` 結果

**初版**:
```
ruff check ............ PASS
mypy kaji_harness/ .... Success
pytest ................ 796 passed, 1 skipped
```

**rev 2026-05-06**（review 対応後）:
```
ruff check ............ PASS
mypy kaji_harness/ .... Success
pytest ................ 817 passed, 1 skipped (60.93s)
                        ※ test_phase3c_dispatcher.py が 15 → 36 件に拡張
```

## 6. 受け入れ条件チェック（phase3-design.md § 受け入れ条件）

### 機械検証可能（PR-3c 範囲のみ）

- [x] `make check` が PR-3c 時点で緑（796 passed）
- [x] CliRunner 相当（`_handle_issue` 直叩き）で provider=github / 未設定 fallback / local の各経路が動作
- [x] `prompt.py` が `IssueContext` 由来で 5 変数を注入する（`issue_context` 指定時）
- [x] `mypy kaji_harness/` 緑（21 files）

### 手動確認（PR-3c 範囲）

- [x] dev repo （本 worktree）で `make check` パス
- 残り（`provider.type` 未設定で `kaji issue view 1` のエラーメッセージ確認、
  Windows 警告、`gh:153` cache 経路、`is_readonly` エラー等）は PR-3d / 3e
  のスコープ。本 PR では実装範囲外

## 7. PR-3d 以降への申し送り

| 項目 | PR | 備考 |
|------|-----|------|
| `kaji local init` 実装 | 3d | `config.local.toml` 作成 + machine_id 候補生成 + `.gitignore` 確認 |
| `feature-development-local.yaml` 追加 | 3d | 本 PR では yaml 未追加（既存 `.kaji/wf/feature-development.yaml` も触らず） |
| Skill markdown の 5 変数移行 | 3d | 21 ファイル の placeholder 置換 |
| dev repo の `.kaji/config.toml` に `[provider]` 追記 | 3d | dogfooding |
| fail-fast 化（fallback 削除） | 3e | `_emit_provider_fallback_warning` / `_resolve_issue_context` の except を削除 |
| Large-local テスト（subprocess E2E） | PR-3e | feature-development-local.yaml の存在前提 |
| `kaji pr` の bare-provider 切替 | Phase 4 | PR/MR 概念抽象化と一体 |

## 8. 既知の差分・トレードオフ

1. **WARN の冪等性**: `_PROVIDER_FALLBACK_WARNED` は process-wide。サブ
   コマンドを連続して `python -m` で叩くと毎回 WARN が出る一方、`kaji
   issue view 42 && kaji issue list` を 1 process 内で連鎖しても 1 度の
   WARN で済む。pytest 並列実行（xdist）では worker 単位なので相互
   汚染しない。

2. **`_handle_issue_local` のサブセット**: `view --json` の field 投影は
   `gh issue view --json` と完全互換ではない（最低限 `number` / `title` /
   `body` / `state` / `labels` / `comments` のみ）。Skill が要求する
   field は Phase 2-B 完了時点で上記 6 つに収束しているので不足しないが、
   将来 Skill が追加 field を要求する場合は LocalProvider 出力 dict に
   足す必要がある。

3. **`runner._resolve_issue_context` の except 範囲**: PR-3c では `Exception`
   で広く捕まえている。fail-fast 化を行う PR-3e でこの except 自体を
   削除し、半端な context での Skill 起動を構造的に防ぐ。

## 9. 完了確認

- ブランチ: `feat/local-phase3c`
- コミット: 未実施（user 確認後にコミット予定）
- 検証: `make check` 緑、phase3c 専用 test 15 件 緑
- 報告: 本書（`draft/design/local-mode/phase3c-implementation-report.md`）

## 10. 参考

- 親設計書: `draft/design/local-mode/phase3-design.md`
- design.md L226-244 ロールアウト戦略 PR-3c
- phase3-design.md § 1 IssueContext 経由の 5 変数供給
- phase3-design.md § 4 fail-fast ロールアウト戦略
- Phase 2-B 報告: `draft/design/local-mode/phase2b-implementation-report.md`

## 11. 改訂履歴

### 2026-05-06 rev — レビュー指摘 4 項目を反映

レビューで「Changes Requested」判定だった以下 4 項目を本 rev で対応:

| # | 指摘 | 対応 | 検証 |
|---|------|------|------|
| Must 1 | `_handle_issue_local` が `normalize_id` を未使用、`153` / `pc1-3` / `gh:153` が壊れる | `_resolve_local_id` ヘルパ導入、view/edit/comment/close 全経路で normalize_id 経由に統一。`gh:N` は `view_cached_issue` で read-only 表示、write 系で拒否 | `TestLocalDispatcherIdNormalization` (7 件) |
| Must 2 | Skill が依存する `--jq` / `-q` / `--comments` / `--body-file` 未受理 | view/list で `--jq`/`-q`、view で `--comments`、create/edit/comment で `--body-file`（`-` で stdin）。jq は subprocess に委譲、不在は exit 3 で明示エラー | `TestLocalDispatcherFlags` (8 件) |
| Must 3 | `_try_load_config_for_dispatch` が `ConfigLoadError` を握りつぶし、`get_provider` の `ValueError` で traceback | `_load_config_for_dispatch_or_none` は `ConfigNotFoundError` のみ None で fallback、`ConfigLoadError` は raise。`_handle_issue` で `get_provider` の `ValueError` を catch して exit 2 | `TestDispatcherFailFastOnConfig` (4 件) |
| Should 4 | `config.local.toml` overlay が `[provider.local]` のみで `[provider]` 全体を引き受けられない | `_parse_provider` を deep-1 merge へ拡張。`type` / `[provider.github]` / `[provider.local]` 全体を overlay 可能。tracked 不在でも overlay 単独で導入可 | `TestConfigLocalOverlayProviderType` (2 件) |

**追加したテスト**: 21 件（`test_phase3c_dispatcher.py` 15 → 36）。`pytest`
全体は 796 → 817 passed。

**判断保留**（Phase 3-c 時点）: レビュー指摘の `pyproject.toml` への jq 依存追加は
対応せず、runtime の `shutil.which("jq")` 検出 + 不在時 exit 3 で吸収する選択を
採った。理由は (1) jq は Python パッケージではないため `pyproject.toml` の依存に
書く先がない、(2) GitHub provider 経路でも `gh --jq` 経由で system jq が
要るため対称性が保てる、(3) installer ガイドへの記載で十分な体感品質。
本判断は本書 § 4.3.1 末尾でも明記。

> **Phase 3-d preflight § 2 で撤回（2026-05-06）**:
> - PyPI に Python binding `jq` が存在することを再確認（前提 (1) 誤り）
> - GitHub provider と local provider の要求水準は対称ではなく、local mode は
>   GitHub 非依存 / local-first を目的にしているため、追加 OS package install を
>   必須にすると自己完結性が下がる（前提 (2) 不適切）
> - Skill 中核の `kaji issue view ... -q '.body'` が fresh install で動かない
>   ため (3) は user 体験として許容できない
>
> 以上により、Phase 3-d preflight で `jq>=1.6` を runtime dependency として
> 追加し、`_apply_jq` を Python `jq` package 実装に置き換えた。詳細は
> `phase3d-preflight-design.md § 2` を参照。

### 2026-05-06 rev #2 — `jq -r` 採用と exact-match テスト

レビューで「`-q '.body'` が quote 付き出力で
`CURRENT_BODY=$(kaji issue view ... --json body -q '.body')` 系を壊す。
テストも `"body text" in captured.out` で通ってしまうので exact match で
`body text\n` を確認する必要がある」と指摘された。

| 項目 | 内容 |
|------|------|
| 根本原因 | `_apply_jq` が `["jq", expr]` で起動し、string 結果が `"body text"` のように quote されていた。`gh --jq` は gojq + raw output で `body text` を返す |
| 修正 | `["jq", "-r", expr]` に変更（`cli_main.py:_apply_jq`）。`-r` は string 結果のみ raw 化、array / object は変わらず JSON のまま（gh と同挙動） |
| テスト 1 | `test_view_json_with_jq_emits_raw_string` を新設、`captured.out == "body text\\n"` で exact match。`'"' not in captured.out` で regression guard |
| テスト 2 | `test_view_jq_array_result_keeps_json_format` で array は JSON のまま `json.loads` で構造比較 |
| テスト 3 | `test_view_jq_string_stream_emits_raw_lines` で `.labels[].name` の stream を `splitlines()` 一致で検証 |
| テスト 4 | `test_view_jq_shell_capture_round_trip` で実 subprocess + `stdout.rstrip("\\n")` 経由で `$()` 取り込み相当を再現し、`captured == "body text"`（改行剥ぎ後 quote 無し）を assert |
| 結果 | テスト 36 → 39 件。`pytest` 全体は 817 → 820 passed |

### 2026-05-06 rev #3 — runner の normalize_id 経由化 / 明示 provider の fail-fast / `--repo` 強制注入

レビューで以下 3 項目が Must Fix として指摘された:

#### (1) `runner._resolve_issue_context` が `normalize_id` を未経由

**問題**: `kaji issue view 1` は動くのに `kaji run ... 1` / `kaji run ... pc1-1`
では `provider.resolve_issue_context(self.issue_number)` に user 入力をその
まま渡していたため、`LocalProvider._resolve_issue_dir` の正規表現マッチで
失敗 → fallback で context 注入が壊れていた。Phase 3-c の主目的（local
workflow で 5 変数を Skill に注入する）が機能しない直撃のバグ。

**修正**:

```python
# kaji_harness/runner.py:_resolve_issue_context
provider_type = self.config.provider.type
machine_id = (
    self.config.provider.local.machine_id if provider_type == "local" else None
)
rid = normalize_id(self.issue_number, provider_name=provider_type, machine_id=machine_id)
if rid.kind == "remote_cache":
    raise IssueContextResolutionError(...)  # gh:N に kaji run は意味矛盾
return provider.resolve_issue_context(rid.value)  # provider 内部 ID で resolve
```

**検証**: `TestRunnerIssueContextNormalization::test_local_id_forms_resolve_to_same_context`
を `parametrize(["1", "pc1-1", "local-pc1-1"])` で書き、3 形式すべてが
同一の `IssueContext`（`issue_id="local-pc1-1"`、`branch_prefix="feat"` 等）に
解決されることを assert。

#### (2) 明示 provider 設定下でも legacy 2 変数 fallback

**問題**: 旧実装は `provider.resolve_issue_context(...)` を `except Exception`
で広く捕まえ、WARN を出して `None` を返していた。これにより
`provider.type='local'` 設定下で machine_id 不在 / Issue 不在 / frontmatter
不備が起きても 2 変数 fallback で agent が起動し、後続の Skill が壊れる
構造的リスク。

**修正**: `kaji_harness/errors.py` に
`IssueContextResolutionError(HarnessError)` を新設。runner では明示 provider
設定下の解決失敗を全て raise し、`cmd_run` の `except HarnessError → exit 3`
で agent 起動前に終了させる。fallback（`return None`）は `[provider]` 未設定
の互換経路のみ。

**検証**: `TestRunnerFailFastOnExplicitProvider` の 4 件で以下を確認:
- 存在しない issue id（`9999`）→ raise
- 文法違反（`Bogus-ID`）→ raise (`match="invalid issue id"`)
- `gh:153` を local 配下で → raise (`match="read-only"`)
- machine_id 不在 → raise

#### (3) `[provider.github] repo` が gh に伝搬していない

**問題**: GitHubProvider 経路で `provider = get_provider(config)` を構築した
直後に provider と repo を捨て、`_forward_to_gh("issue", raw_args)` で
`gh issue ...` を起動していた。`gh` は `git remote` から repo を auto-detect
するため、worktree が他 fork を指している / git remote が default repo と異
なる場合、`config.toml` で明示した `repo` を無視して別所に書き込む構造
バグ。

**修正**:

| 関数 | 変更 |
|------|------|
| `_forward_to_gh(group, raw_args, *, repo=None)` | `repo` が指定され、user 入力に `--repo`/`-R` が無いとき末尾に `--repo <owner/name>` を追加 |
| `_detect_repo(*, override=None)` | override 指定時はそれを返し auto-detect しない |
| `_handle_issue` | GitHub 経路で `repo=config.provider.github.repo` を渡す |
| `_handle_pr` | config 読み込み + `repo_override` を builtin / passthrough 両方に伝搬。`type='github'` で `repo` 不在は exit 2 |
| `_dispatch_pr_builtin` / `_forward_pr_*` | `repo_override` を末端まで貫通 |

**user 入力の尊重**: user が `--repo X` を明示した場合は config 由来の二重
注入をしない（`if repo and "--repo" not in args and "-R" not in args` で
guard）。

**検証**: `TestForwardToGhRepoInjection` の 5 件で以下を確認:
- `kaji issue view 42` → `gh ... --repo kamo/kaji` が末尾に注入される
- user `--repo X` を渡したら user 値を尊重、`--repo` は 1 個のみ
- `[provider]` 未設定の legacy 経路では `--repo` を注入しない（既存挙動維持）
- `kaji pr view 153` も同様に `--repo` 注入
- `kaji pr review-comments 153` の builtin が `_detect_repo(override="kamo/kaji")`
  で呼ばれることを spy で確認、生成 cmd が `gh api repos/kamo/kaji/pulls/153/comments`
  になる

#### 結果（rev #3）

- テスト 39 → 53 件（+14）。新クラス: `TestRunnerIssueContextNormalization`
  (4)、`TestRunnerFailFastOnExplicitProvider` (4)、`TestForwardToGhRepoInjection` (5)
  + parametrize 1 件
- `pytest` 全体は 820 → 834 passed
- 新エラー型: `IssueContextResolutionError(HarnessError)` → exit 3
- 影響を受ける関数 / シグネチャ拡張: `_forward_to_gh`、`_detect_repo`、
  `_dispatch_pr_builtin`、`_forward_pr_review_comments`、`_forward_pr_reviews`、
  `_forward_pr_api_list`、`_forward_pr_reply_to_comment`、`_handle_pr`
