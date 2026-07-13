# [設計] CLI 層からドメインロジックを分離（R4）

Issue: #286

## 概要

#283 で分割された command 層（`kaji_harness/commands/`）の全トップレベル関数 66 件を
CLI / application / domain / provider の責務へ分類し、CLI 許容責務（引数解釈・入力読取・
application 呼出し・出力整形・exit code 決定）に該当しない 5 シンボルを適切な層へ移す。
シンボル移動に着手する前に、ADR 009 決定 1（層依存方向の固定）を強制する層方向
fitness test を Red→Green で確立する。

## 背景・目的

### 現状の問題（観測可能な形）

1. **層方向の強制装置が無い**: 現行 `tests/test_private_imports.py` は ADR 009 決定 2
   （private import の 3 分類）のみを検査し、public シンボル経由の層逆依存を検出しない。
   再現例（#291 中間コードレビュー指摘の正本）:

   ```python
   classify_source("from ..sync import sync_from_github\n",
                   "kaji_harness.providers.local", PACKAGES)  # => [] （検出されない）
   ```

   R3（#285）が解消した provider→application 逆依存と同型の違反を、public シンボル経由で
   再導入しても `make check` は素通りする。R4 はシンボルを層・module 間で移動するため、
   この検出穴を放置したまま移動に着手すると回帰リスクが検査されない。

2. **command 層への責務混在**: `kaji_harness/commands/` 2,558 行・66 トップレベル関数に、
   CLI 固有処理と混在して以下が存在する:
   - 外部 I/O に依存しない規則（`build_worktree_note_body` の本文合成、
     `_resolve_verdict_marker` の marker 規則）→ command 層を import しないとテストできない
   - provider 永続化境界（`_commit_local_issue_change` の git atomic commit）
     → さらに `commands/issue.py:283` が `provider._resolve_issue_dir(...)` という
     private 属性アクセスで LocalProvider 内部に依存している（import AST 検査の対象外）
   - application orchestration（`_resolve_recover_issue_context` の provider 種別考慮
     IssueContext 解決、`_resolve_target_run_dir` の run 選択・終端状態検証）

3. **stale な deferred import**: `commands/output.py:127` の
   `from ..providers.models import Issue  # local import to avoid cycle` は、#283 分割後は
   循環が存在しない（`providers.models` は commands へ依存しない）ため回避理由が消滅している。
   ADR 009 は deferred import を「依存の向きが誤っている兆候」として扱う。

### 改善指標

| 指標 | 現状 | ゴール | 検証手段 |
|------|------|--------|---------|
| 層方向 fitness test | 無し（層逆依存は検査されない） | `tests/test_layer_imports.py` が PASS | `make check` |
| command 層関数の責務分類 | 未分類 | 66 関数全件に分類と残置/移動理由（本設計書 § 方針） | 設計書レビュー |
| CLI 残置関数の責務説明可能性 | 旧本文は境界を先決め | 残置 61 件が許容責務 5 種のいずれかへ対応付く | 本設計書の分類表 |
| 移動シンボルの独立テスト可能性 | commands import が必須 | 移動 5 シンボルのテストが `kaji_harness.commands` / `cli_main` を import しない | grep + pytest |
| 禁止 private import 残差 | 0（allowlist 8 entry 厳密一致） | 0 を維持（allowlist は実態に追随して更新） | `pytest tests/test_private_imports.py` |
| CLI 挙動 | 2,365 collected テストが担保 | command 体系・出力・exit code 不変（既存テスト全 PASS） | `make check` |

## ベースライン計測

改修前（branch `refactor/286` 分岐点 = main `a80b88c`）の計測値。実装フェーズ冒頭で再計測し、
完了時に同一コマンドで再々計測して #291 へ記録する。

| 項目 | コマンド | 計測値（2026-07-13） |
|------|---------|----------------------|
| commands/ 行数 | `wc -l kaji_harness/commands/*.py` | 合計 2,558 行 |
| トップレベル関数数 | 下記 AST スクリプト | 66 関数（config 4 / issue 16 / main 1 / output 6 / parser 10 / pr 16 / recover 3 / run 4 / sync 2 / validate 4） |
| private import fitness | `python -m pytest tests/test_private_imports.py -q` | 19 passed |
| テスト総数 | `python -m pytest --collect-only -q` | 2,365 tests collected |
| runtime 層逆依存 | 下記 AST スクリプト（手動棚卸し） | 0 件（TYPE_CHECKING 内の上向き依存が 1 件のみ: `providers/__init__.py:25` → `config`） |

関数棚卸しスクリプト（誰が実行しても同じ値が出る）:

```bash
python - <<'EOF'
import ast, pathlib
total = 0
for p in sorted(pathlib.Path("kaji_harness/commands").glob("*.py")):
    tree = ast.parse(p.read_text())
    names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    total += len(names)
    print(f"{p.name}: {len(names)} {names}")
print(f"total: {total}")
EOF
```

## インターフェース

### 公開 IF は不変

- CLI command 体系・stdout/stderr 出力・exit code は**一切変更しない**
- console entry point `kaji`（`pyproject.toml` → `cli_main:main`）は不変
- provider 公開抽象（`IssueProvider` の abstract surface）は再設計しない（Issue 対象外）
- 内部 module の import path 変更は公開契約外であり、ADR 008 決定 2 の CHANGELOG
  BREAKING エントリの対象外（ADR 009 帰結に既記載）

### 内部 IF の変更（移動シンボル）

| Before | After | 備考 |
|--------|-------|------|
| `commands.issue.build_worktree_note_body` | `providers.context.build_worktree_note_body` | シグネチャ不変 |
| `commands.issue._resolve_verdict_marker` | `providers.markers.resolve_verdict_marker` | public 昇格。シグネチャ不変 |
| `commands.issue._commit_local_issue_change` | `LocalProvider.commit_issue_change(rid, action, paths)` メソッド | `provider=` 引数が `self` になる。`_resolve_issue_dir` の外部 private 属性アクセスがメソッド内部へ吸収される |
| `commands.recover._resolve_recover_issue_context` | `recovery.target.resolve_recover_issue_context` | 新設 module。シグネチャ不変 |
| `commands.recover._resolve_target_run_dir` | `recovery.target.select_target_run_dir` | 戻り値契約を変更: `Path \| None` + stderr 印字 → `Path` を返すか `RecoveryTargetError`（`errors.py` に新設、`HarnessError` 派生）を raise。従来の stderr 文言は例外 message に保持し、CLI 側 `cmd_recover` が `print(f"Error: {exc}", file=sys.stderr)` + `EXIT_INVALID_INPUT` で byte 不変に再現する |

`cli_main.py`（時限 shim、#284 で削除）の re-export surface は public 名の単純 import で
可能な範囲で維持する。メソッド化した `_commit_local_issue_change` と改名した
`_resolve_verdict_marker` は shim から除去し、参照している tests を新 path へ機械修正する
（Issue スコープ「import path 変更に必要な tests の機械的修正」）。
これに伴い `tests/test_private_imports.py` の `TRANSITIONAL_ALLOWLIST` のうち
`cli_main → commands.issue` entry の signature（private 名 tuple）が変化するため、
ADR 009 決定 4 の stale 検出に従って entry を実態へ更新する（allowlist の縮小方向のみ）。

### 使用例

```python
# 移動後: command 層を import せずに規則をテストできる
from kaji_harness.providers.context import build_worktree_note_body
from kaji_harness.providers.markers import resolve_verdict_marker
from kaji_harness.recovery.target import resolve_recover_issue_context, select_target_run_dir
from kaji_harness.errors import RecoveryTargetError

body = build_worktree_note_body("## 概要\n...", worktree="kaji-refactor-286", branch="refactor/286")
marker = resolve_verdict_marker("design", "PASS")   # 両方 None なら None を返す
try:
    run_dir = select_target_run_dir(runs_dir, run_id=None)  # 最新 run を選択・終端検証
except RecoveryTargetError as exc:
    ...  # CLI 層が "Error: {exc}" + EXIT_INVALID_INPUT に変換する
```

## 制約・前提条件

- **振る舞い非変更が絶対要件**: CLI command 体系・出力（stdout/stderr の byte 単位）・
  exit code を変えない。feat / bug 修正を混在させない
- **依存方向**: `commands → application/domain → provider → foundation` を維持し、
  provider→commands / application→commands の逆依存を作らない（ADR 009 決定 1）
- **シンボル移動の前に fitness test**: 層方向 fitness test を Red→Green で確立してから
  移動に着手する（#291 中間コードレビューの受理済み指摘）
- **allowlist 規律**: `TRANSITIONAL_ALLOWLIST` は statement 単位の厳密一致（ADR 009 決定 4）。
  移動で import 行が変化した entry は必ず更新し、新規追加はしない（縮小方向のみ）
- **対象外**: provider 公開抽象の再設計 / CLI 仕様変更 / 全 tests import 移行と
  `cli_main` shim 縮小（#284）/ feat / bug 修正
- 依存: 先行 #285 は merge 済み（`57639bf`）。後続 #284 が本 Issue の fitness test を継承する

## 方針

### 1. 層の定義と module→層 対応表

Issue 本文の 4 分類（CLI/application/domain/provider）と ADR 009 決定 1 の層
（foundation/provider/application/command/shim）の対応:

- **CLI/commands** ↔ command 層（+ 時限 shim `cli_main`）
- **application** ↔ application 層
- **provider** ↔ provider 層
- **domain**（外部 I/O に依存しない判定・正規化・本文合成規則）↔ 専用層は設けない。
  ADR 009 帰結「新しい共通ロジックの置き場所は消費者がどの層にいるか」に従い、
  **全消費者から届く最下層の public module** に置く。本 Issue の対象では
  `providers/context.py`（worktree/branch/本文の決定的合成規則群）と
  `providers/markers.py`（verdict marker 契約の正本）が該当する。foundation への昇格は
  「kaji_harness 内部依存ゼロ」が条件であり、これらは `providers._mappings` 等へ依存する
  ため provider 層に置く。

module→層 対応表（層方向 fitness test にこのまま実装する。rank は依存可能方向の順序）:

| 層 | rank | module（prefix 一致。`**` は subtree） |
|----|------|----------------------------------------|
| foundation | 0 | `errors` / `fsio` |
| provider | 1 | `providers/**` |
| application | 2 | `adapters` / `artifacts` / `cli` / `config` / `console_log` / `interactive_terminal` / `local_init` / `logger` / `models` / `prompt` / `recovery/**` / `result` / `runner` / `script_exec` / `scripts/**` / `skill` / `state` / `sync` / `verdict` / `workflow` / `worktree_discovery` |
| command | 3 | `commands/**` |
| shim | 4 | `cli_main`（#284 で削除。削除時に mapping からも除去され、stale 検出で強制される） |

依存規則: **runtime import の edge M→T は `rank(M) >= rank(T)` のときのみ許可**（同層内は許可）。
ただし foundation は例外で、**kaji_harness 内部への import を全面禁止**する
（ADR 009 決定 1 の「内部依存ゼロ」。rank 規則より強い）。
これは Issue 完了条件の最低限 3 禁止（foundation→内部 / provider→application・commands /
application→commands）を包含し、さらに provider→shim 等も一様に禁止する。

### 2. command 層 66 関数の責務分類（全件）

分類凡例 — **残置(CLI)**: 許容責務（①引数解釈 ②入力読取 ③application 呼出し ④出力整形
⑤exit code 決定）のいずれかに該当。**移動**: application / domain / provider へ移す。

#### config.py（4 件 — 全て残置）

| 関数 | 分類 | 理由 |
|------|------|------|
| `_load_config_for_dispatch` | 残置(CLI ②) | dispatch 用 config 読取の薄い wrapper。読込規則の正本は `KajiConfig` |
| `_emit_provider_overlay_divergence_warning` | 残置(CLI ④) | stderr への WARN 出力。判定文字列の正本は `providers.provider_overlay_divergence_warning` |
| `cmd_config_provider_type` / `cmd_config_artifacts_dir` | 残置(CLI ③④⑤) | subcommand handler |

#### issue.py（16 件 — 移動 3・残置 13）

| 関数 | 分類 | 理由 |
|------|------|------|
| `_handle_issue` | 残置(CLI ①③⑤) | provider 別 dispatch と gh passthrough 振り分け |
| `_github_issue_comment_with_verdict` | 残置(CLI ①②③⑤) | argparse + body 読取 + provider 呼出し + exit code 変換 |
| `_resolve_local_id` | 残置(CLI ⑤) | 正規化規則の正本は `providers.normalize_id`。本関数は ValueError/write 拒否 → stderr + exit code への変換 adapter |
| `_resolve_verdict_marker` | **移動(domain → `providers.markers`)** | 両フラグ同時必須の検証と marker 合成は外部 I/O に依存しない規則。verdict marker 契約の正本 module（ADR 008 決定 3 が参照する `providers/markers.py`）へ集約し、public `resolve_verdict_marker` に昇格 |
| `_has_verdict_flags` | 残置(CLI ①) | argv 列に対するフラグ検出（引数解釈そのもの） |
| `build_worktree_note_body` | **移動(domain → `providers.context`)** | Issue 本文の決定的合成。内部依存ゼロの純関数で、`providers/context.py` の合成規則群（`build_branch_name` / `build_worktree_dir` 等）と同族 |
| `_handle_issue_prepend_note` / `_handle_issue_context` / `_handle_issue_local` | 残置(CLI ①③④⑤) | argparse + provider 呼出し + 例外→exit code 変換 |
| `_local_issue_view/create/edit/comment/close/list`（6 件） | 残置(CLI ①②③④⑤) | 同上（LocalProvider CRUD の CLI adapter） |
| `_commit_local_issue_change` | **移動(provider → `LocalProvider.commit_issue_change`)** | `.kaji/issues/` への git atomic commit は「local forge 固有 I/O と永続化境界」の定義そのもの。メソッド化により呼出し側の `provider._resolve_issue_dir(...)` private 属性アクセス（`issue.py:283`）も解消される |

#### main.py（1 件 — 残置）

| 関数 | 分類 | 理由 |
|------|------|------|
| `main` | 残置(CLI ①③⑤) | entry point。parser 生成 → dispatch → exit code |

#### output.py（6 件 — 全て残置）

| 関数 | 分類 | 理由 |
|------|------|------|
| `_compose_json_and_jq` | 残置(CLI ④) | `--json`/`--jq` を gh 互換 jq 式へ合成する出力整形規則 |
| `_read_body_arg` | 残置(CLI ②) | `--body`/`--body-file`/stdin の入力読取 |
| `_apply_jq` / `_format_jq_results` / `_emit_json` | 残置(CLI ④⑤) | `gh --jq` 互換 raw 出力整形と exit code |
| `_issue_to_json_dict` | 残置(CLI ④) | **契約**: `Issue` model → `gh issue view --json` 互換 dict（`number`/`title`/`body`/`state`/`labels[]{name,description,color}`/`comments[]{author,body,createdAt}`）。**残置理由**: この変換は「kaji CLI が gh 互換 JSON を出す」という CLI 出力仕様の実装であり、model 側の関心ではない。gh 互換キー名（camelCase `createdAt` 等）は CLI 表面契約に属する。付随修正: 関数内 deferred import（`output.py:127`）は循環理由が消滅している（`providers.models` は commands へ依存しない）ため top-level へ hoist する（command→provider は正方向） |

#### parser.py（10 件 — 全て残置）

`create_parser` / `_get_version` / `_register_*`（7 件）/ `_add_recovery_arguments` —
全て argparse 設定（CLI ①）。

#### pr.py（16 件 — 全て残置）

| 関数群 | 分類 | 理由 |
|--------|------|------|
| `_user_specified_repo` / `_is_ascii_decimal` / `_has_approve_flag` / `_has_request_changes_flag` | 残置(CLI ①) | argv 解釈 helper |
| `_forward_to_gh` / `_forward_pr_review_comments` / `_forward_pr_reviews` / `_forward_pr_api_list` / `_forward_pr_reply_to_comment` / `_dispatch_pr_builtin` | 残置(CLI ①③⑤) | gh passthrough は「引数を無解釈で外部 CLI へ委譲し exit code を伝播する」CLI 責務。provider 抽象を経由しないのは Phase 3-c の設計判断であり、PR 操作を `IssueProvider` 抽象へ導入するのは「provider 公開抽象の再設計」（本 Issue 対象外） |
| `_detect_repo` / `_gh_capture_value` / `_gh_post_issue_comment_silent` / `_github_pr_review` | 残置(CLI、対象外理由を記録) | 実体は gh への直接 I/O で provider I/O に近いが、GitHub 専用 PR 機能の passthrough 系列に属し、移動先となる provider 側 PR 抽象が存在しない。抽象新設は対象外のため残置し、#284 以降の provider 抽象整理の候補として記録する |
| `_run_pr_review_poll` | 残置(CLI ③) | `scripts.review_poll_entry`（application）呼出しの薄い adapter |

#### recover.py（3 件 — 移動 2・残置 1）

| 関数 | 分類 | 理由 |
|------|------|------|
| `cmd_recover` | 残置(CLI ①③⑤) | handler 起動と例外→exit code 変換 |
| `_resolve_recover_issue_context` | **移動(application → `recovery.target`)** | provider 種別を考慮した IssueContext 解決は use-case orchestration（normalize → provider I/O の手順制御）。stderr / exit code を一切持たず、CLI 許容責務のどれにも該当しない。`runner._resolve_issue_context`（`runner.py:247`）と同型ロジックの整理は #284 以降の課題として記録 |
| `_resolve_target_run_dir` | **移動(application → `recovery.target.select_target_run_dir`)** | run 選択（最新 / 指定 ID）と終端状態検証（`workflow_end` 有無・ERROR/ABORT 判定）は artifact 規則の application ロジック。現実装は stderr 印字と `None` 返しが混在しているため、規則部を raise 方式（`RecoveryTargetError`）で移動し、stderr + `EXIT_INVALID_INPUT` 変換は `cmd_recover` に残す（層定義どおり出力整形と exit code は CLI 責務） |

#### run.py（4 件 — 全て残置）

| 関数 | 分類 | 理由 |
|------|------|------|
| `_apply_execution_overrides` | 残置(CLI ①) | `argparse.Namespace` → config への precedence 適用。CLI フラグ語彙（`--agent-runner` の表記正規化等）に密結合 |
| `cmd_run` | 残置(CLI ①③⑤) | subcommand handler |
| `_run_failure_triage` | 残置(CLI ③④⑤) | orchestration の実体は `RecoveryHandler`（application）に既在。本関数は (a) `WorkflowRunner` インスタンス属性（`last_run_dir` / `canonical_issue_id`）への依存 (b) best-effort 例外→stderr WARN 境界 (c) child exit code の伝播 — すなわち application 呼出し + 出力整形 + exit code 決定の adapter であり、移動すると stderr/exit code 責務が application へ漏れるか IF 再設計が必要になる |
| `_validate_workflow_provider_match` | 残置(CLI ④⑤) | 判定の正本は `workflow.requires_provider` と `actual_provider_type()`（既に application/provider 側）。本関数の実体は不一致時の stderr 切替手順メッセージと `EXIT_INVALID_INPUT` |

#### sync.py（2 件）/ validate.py（4 件） — 全て残置

`cmd_sync_from_github` / `cmd_sync_status` は application `sync.py` 呼出し + 整形 + exit code
の薄い adapter（CLI ③④⑤）。`cmd_validate` / `_print_success` / `_print_error` /
`_resolve_project_root_for_validate` は argparse / 出力整形 / config 読取（CLI ①②④⑤）。

> 定数（`exit_codes.py` の `EXIT_*`、`issue.py._LOCAL_ISSUE_SUBS`、`pr.py._FORGE_METHOD_FLAGS` 等）は
> 関数ではないが、いずれも CLI 語彙（exit code 体系・subcommand/フラグ集合）であり command 層に残す。

### 3. 層方向 fitness test の設計（`tests/test_layer_imports.py` 新設）

`test_private_imports.py` と同じ構成原則: **分類器はファイル I/O を持たない純関数**
（Small テスト、合成ソースで完結）、**実ツリー走査は Medium テスト**に分離する。

構成要素（疑似コード）:

```python
LAYER_RANK = {"foundation": 0, "provider": 1, "application": 2, "command": 3, "shim": 4}
MODULE_LAYERS: dict[str, str] = {...}  # § 方針 1 の対応表をそのまま実装（正本はテストコード側）

def layer_of(module: str) -> str:
    # 最長 prefix 一致（"kaji_harness.providers.local" → provider）。
    # どの entry にも一致しない module は fail（未分類の新 module を黙って許可しない）

def iter_runtime_imports(source_text, module_name) -> list[ImportEdge]:
    # ast.Import / ast.ImportFrom の両ノードを走査（決定 3 と同じ要件）。
    # 相対 import の level 解決は classify_source と同規則。
    # `if TYPE_CHECKING:` ブロック（ast.If の test が Name("TYPE_CHECKING") または
    # Attribute(attr="TYPE_CHECKING")）直下の import は runtime=False として収集し、
    # 方向規則の適用対象から除外する（実行時依存と分離して扱う）。
    # 関数内 deferred import は実行時に評価されるため runtime=True として扱う。

def classify_layer_source(source_text, module_name) -> list[LayerViolation]:
    # runtime edge M→T について rank(layer_of(M)) < rank(layer_of(T)) なら violation。
    # layer_of(M) == "foundation" の場合は T が kaji_harness 内部なら無条件 violation。
```

- **Small テスト（Red→Green の Red を構成）**:
  - #291 指摘の再現例そのもの: `classify_layer_source("from ..sync import sync_from_github\n", "kaji_harness.providers.local")` が violation 1 件を返す（現行 `classify_source` はこの入力で `[]` — 検出穴の固定化）
  - 同一 import が `if TYPE_CHECKING:` 内なら violation 0 件（分離の検証）
  - application→provider / command→application / shim→command は許可
  - foundation（`kaji_harness.fsio`）→ `kaji_harness.models` は violation（rank 規則では同順位以下でも foundation 例外で禁止）
  - `import kaji_harness.sync` 形式（`ast.Import`）の逆依存も検出する
  - 未分類 module（mapping に無い名前）は fail する
- **Medium テスト**:
  - 実 `kaji_harness/` ツリー走査で runtime 層違反 0 件（導入時点で Green — R3 完了時点の
    棚卸しで runtime 層逆依存は 0 件。上向き依存は `providers/__init__.py:25` → `config` の
    TYPE_CHECKING 内 1 件のみで、除外規則の対象）
  - mapping 完全性: ツリー上の全 module が mapping で分類でき、mapping の entry に対応する
    module が実在する（stale entry は fail。`cli_main` 削除（#284）時に mapping 更新を強制する —
    決定 4 と同じ規律）
- **allowlist は持たない**: shim（`cli_main`）は最上位 rank のため層規則violation を生まず、
  時限許容の仕組みは不要。private import 検査の `TRANSITIONAL_ALLOWLIST` とは責務を混ぜない。

**TDD 順序（Red→Green）**: (1) 上記 Small/Medium テストを先に書く — 分類器未実装のため fail
（Red）。(2) 分類器と mapping を実装して全件 PASS（Green）。(3) Green を確認してから § 4 の
シンボル移動に着手する。

### 4. 移行ステップ（Before / After と順序）

```
Before                                      After
kaji_harness/commands/issue.py              kaji_harness/providers/context.py
  build_worktree_note_body ────────────────►  build_worktree_note_body（public のまま）
  _resolve_verdict_marker ─────────────────► kaji_harness/providers/markers.py
  _commit_local_issue_change ──┐               resolve_verdict_marker（public 昇格）
                               └───────────► kaji_harness/providers/local.py
kaji_harness/commands/recover.py               LocalProvider.commit_issue_change()
  _resolve_recover_issue_context ──────────► kaji_harness/recovery/target.py（新設）
  _resolve_target_run_dir ─────────────────►   resolve_recover_issue_context
                                               select_target_run_dir（raise 方式）
                                             kaji_harness/errors.py
                                               RecoveryTargetError（HarnessError 派生、新設）
```

実装順序（各ステップで `make check` を通し、commit を分ける）:

1. 層方向 fitness test を Red→Green で確立（§ 3）
2. `resolve_verdict_marker` を `providers/markers.py` へ移動（呼出し側・`cli_main`・tests・
   `TRANSITIONAL_ALLOWLIST` の該当 entry を機械修正）
3. `build_worktree_note_body` を `providers/context.py` へ移動（同上）
4. `recovery/target.py` 新設: `resolve_recover_issue_context` 移動、
   `select_target_run_dir` へ raise 方式で移動 + `RecoveryTargetError` 新設 +
   `cmd_recover` に例外→stderr/exit code 変換を実装（stderr 文言 byte 不変）
5. `LocalProvider.commit_issue_change` メソッド化（`issue.py:283` の private 属性アクセス解消）
6. `output.py` deferred import の hoist
7. ドキュメント更新（§ 影響ドキュメント）と再計測

### 5. 依存図と主要例外境界

移動完了後の runtime 依存方向（層方向 fitness test が固定化する）:

```
commands/ ──► application（runner / recovery / sync / workflow / skill / config / artifacts / local_init / scripts / console_log）
          ──► provider（providers: get_provider / normalize_id / context / markers / models / local / github）
          ──► foundation（errors）
application ──► provider ──► foundation
cli_main(shim) ──► commands   ※ #284 で削除
```

主要例外境界（下位層の例外 → CLI 層で exit code へ変換。すべて既存契約、変更なし）:

| 例外（発生層） | CLI での変換 |
|----------------|--------------|
| `ConfigNotFoundError` / `ConfigLoadError`（application） | `EXIT_CONFIG_NOT_FOUND`（run/recover）/ `EXIT_INVALID_INPUT`（issue/pr dispatch） |
| `ValueError`（`get_provider` / `normalize_id`） | `EXIT_INVALID_INPUT` |
| `WorkflowValidationError` / `SkillNotFound` / `SecurityError` / `SkillFrontmatterError` | `EXIT_DEFINITION_ERROR` |
| `GitHubProviderError` / `IssueNotFoundError`（provider） | `EXIT_RUNTIME_ERROR` |
| `LocalProviderError` / `IssueReadOnlyError` / `SyncError`（provider/application） | `EXIT_INVALID_INPUT` |
| `HarnessError`（総称） | `EXIT_RUNTIME_ERROR` |
| `RecoveryTargetError`（application、**新設**） | `EXIT_INVALID_INPUT`（従来の `None` 返し経路と同一 exit code・同一 stderr 文言） |
| 予期しない `Exception`（`cmd_run`） | `EXIT_ABORT` |

### 6. import 検査の既知の対象外（記録）

| 事象 | 箇所 | 扱い |
|------|------|------|
| 属性アクセス経由の private 依存 | `runner.py:577,1040` の `state._persist()` | import AST 検査（private import / 層方向とも）の構造的対象外。application→application で層違反ではないが、検査限界として記録 |
| 同上（command→provider） | `commands/issue.py:283` の `provider._resolve_issue_dir(...)` | 本 R4 の `LocalProvider.commit_issue_change` メソッド化で解消される |
| `TYPE_CHECKING` の別名 import | `from typing import TYPE_CHECKING as TC` 形式のガード | 検出は `Name("TYPE_CHECKING")` / `Attribute(attr="TYPE_CHECKING")` に限る。現ツリーに別名形式は存在せず、出現時は runtime 扱い（fail 側に倒れる = fail-safe） |
| 文字列 import（`importlib` 等） | 現ツリーに該当なし | AST import ノードに現れないため対象外 |

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ

実行時コード変更（振る舞い非変更のシンボル移動 + fitness test 新設。
CLI 表面の仕様変更は無し）。

### 既存テストのカバレッジ評価（safety net）

CLI 表面は `test_cli_main.py` / `test_cli_main_characterization.py`（characterization =
出力の忠実な固定）/ `test_dispatcher.py` / `test_issue_prepend_note_cli.py` /
`test_local_issue_commit_flag.py` / `test_recovery_cli.py` / `test_verdict_marker.py` /
`test_e2e_cli.py` ほか計 2,365 collected が担保しており、移動起因の挙動変化はここで検出される。
カバレッジ不足を理由とする先行テスト追加は不要と評価する（移動対象 5 シンボルはいずれも
既存テストの実行経路上にある。例: `--commit` フローは `test_local_issue_commit_flag.py`、
recover 対象解決は `test_recovery_cli.py`）。

### Small テスト

- 層分類器（`classify_layer_source` / `layer_of` / `iter_runtime_imports`）の合成ソース
  単体テスト（§ 方針 3 の列挙。**Red→Green の Red を構成**）
- 移動した純粋関数の新 import path での単体テスト: `build_worktree_note_body`（既存テストの
  import 付替えで流用）/ `resolve_verdict_marker`（両 None → None、片方のみ → ValueError、
  合成結果が `build_kaji_verdict_marker` と一致）
- `resolve_recover_issue_context`: mock provider（`resolve_issue_context` を stub）で
  provider 種別ごとの `normalize_id` 引数（`machine_id` の有無）を検証
- いずれも `kaji_harness.commands` / `cli_main` を import しないことが完了条件

### Medium テスト

- 実ツリー層方向 fitness test（runtime 層違反 0 件 + mapping 完全性 + stale mapping 検出）
- `select_target_run_dir`: `tmp_path` に run.log を合成し、(a) 最新 run 選択 (b) `run_id`
  指定 (c) runs_dir 不在 (d) run.log 欠落 (e) `workflow_end` 無し（進行中拒否）
  (f) 終端 status が ERROR/ABORT 以外 — 各分岐の `RecoveryTargetError` message が
  従来 stderr 文言と一致することを固定（bridging: 出力 byte 不変の保証）
- `LocalProvider.commit_issue_change`: 既存 `test_local_issue_commit_flag.py` の
  機械修正（メソッド呼出し形へ）で流用。atomic commit 契約（`--only` / 空 diff skip）は
  既存アサーションを変更しない
- `tests/test_private_imports.py` 19 件の継続 PASS（allowlist 更新後）

### Large テスト

新規追加しない。理由（`docs/dev/testing-convention.md` の 4 条件）:

1. 外部 API 疎通・E2E データフローに新規ロジックを追加しない（シンボル移動のみ）
2. CLI 表面の E2E は既存 `large_local`（`test_local_cli_large_local.py` /
   `test_recovery_e2e_large_local.py` 等）が捕捉済みで、無変更のまま bridging test として機能する
3. 新規 Large を足しても移動の回帰検出情報は増えない（回帰は S/M と characterization で検出される）
4. 省略理由は本節に記録した

### bridging test（振る舞い非変更の保証）

既存テスト全件（2,365 collected）を無変更（import path の機械修正を除く）で PASS させることを
bridging とする。特に characterization テストと `select_target_run_dir` の例外 message 固定が
stderr byte 不変を保証する。新規 bridging test の追加は不要（エビデンス: 上記 safety net 評価）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/009-module-boundary-private-import.md | あり（軽微） | 決定 1 の強制装置として `tests/test_layer_imports.py` が加わる。「参照」節へ検証器を追記（新規 ADR は不要 — 新しい意思決定ではなく決定 1 の強制の実装） |
| docs/ARCHITECTURE.md | あり（軽微） | § 層の依存方向（L119-132）に層方向 fitness test の言及を追加。移動シンボルの module 配置記述を更新 |
| docs/reference/python/python-style.md | あり（軽微） | § モジュール境界と private import の「fitness test」言及を 2 検証器（private import / 層方向）に更新 |
| docs/dev/ | なし | ワークフロー・開発手順に変更なし |
| docs/cli-guides/ | なし | CLI 仕様（command 体系・出力・exit code）不変 |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠(引用/要約) |
|--------|----------|-------------------|
| ADR 009 決定 1 | `docs/adr/009-module-boundary-private-import.md` | 「依存の向きを層で固定する」「下位層から上位層への依存を禁止する」— 本設計の層 rank 規則と module→層対応表の正本。決定 3「境界の強制は fitness test が担う」、決定 4「statement 単位 allowlist と stale 検出」を層方向テストの設計原則として継承 |
| Issue #286 本文 | `kaji issue view 286` | 層定義（CLI ①〜⑤ の許容責務）、6 候補の必須評価、最低限の禁止 3 方向、`TYPE_CHECKING` 分離、既知対象外（`runner.py:577,1040` の `state._persist()`）の記録要求 |
| #291 中間コードレビュー | `kaji issue view 291`（コメント「3/5 完了時点の中間コードレビュー結果」および「中間コードレビュー対応の確認結果」2026-07-13） | 「`classify_source("from ..sync import sync_from_github\n", "kaji_harness.providers.local", PACKAGES)` が `[]` を返す」再現例と「層方向 fitness test を Red→Green にしてからシンボル移動へ進むこと」の指示 — § 方針 3 の Red ケースの正本 |
| 現行検証器 | `tests/test_private_imports.py` | `classify_source` の相対 import level 解決・`ast.Import`/`ast.ImportFrom` 両走査・Small/Medium 分離の実装パターンを層方向テストが踏襲。`TRANSITIONAL_ALLOWLIST` の cli_main→commands.issue entry が移動で更新対象になる根拠 |
| Python 公式: `typing.TYPE_CHECKING` | https://docs.python.org/3/library/typing.html#typing.TYPE_CHECKING | 「A special constant that is assumed to be True by 3rd party static type checkers. It is False at runtime.」— TYPE_CHECKING ガード内 import が実行時依存でないことの根拠（分離規則の正当化） |
| PEP 8 Naming Conventions | https://peps.python.org/pep-0008/#descriptive-naming-styles | `_single_leading_underscore` は "weak internal use indicator" — public 昇格（`resolve_verdict_marker`）が「外部 package が必要とする最小限のみ公開」（ADR 009 決定 2）に基づくことの根拠 |
| 対象コード実体 | `kaji_harness/commands/`（issue.py:165-222 / output.py:125-144 / recover.py:104-159 / run.py:240-287 / issue.py:473-513 ほか） | 66 関数の分類根拠（本文中に行番号で引用） |
| refactor 設計指針 | `.claude/skills/_shared/design-by-type/refactor.md` | ベースライン計測・改善指標・bridging test の必須要件 |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L 判定基準（外部依存の有無）と Large 省略の 4 条件 |
