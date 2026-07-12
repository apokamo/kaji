# [設計] cli_main characterization test 補強とカバレッジ / patch target 棚卸し（R0）

Issue: #282

## 概要

後続リファクタ（#283 = R1、`kaji_harness/commands/` への分割）の safety net を先に固める。
着手時 main を正本として `kaji_harness/cli_main.py`（2,421 行 / トップレベル関数 66 個）の
カバレッジを関数単位で棚卸しし、主要正常系・主要エラー分岐に characterization test（現挙動の
写し取りテスト）を補強する。同時に tests 内の `cli_main` patch/monkeypatch target を全件列挙・
分類し、R1 で単純 re-export では届かなくなる patch を対応表として固定する。

## 背景・目的

### ユーザーストーリー

R1（#283）の実装者として、`cli_main.py` を機械的に分割したときに (a) 振る舞いが変わっていないこと
を既存 test 群で確認でき、(b) module 移動で無効化される patch を事前に把握して互換 shim か target
書換えかを判断できるように、**現行 main の主要分岐・例外経路・test patch 依存を固定した再現可能な
ベースライン**が欲しい。

### なぜ必要か（一次情報に基づく R1 リスク）

`unittest.mock.patch("target")` は「その名前が *look up* される名前空間」に対して差し替える
（一次情報: [unittest.mock — Where to patch](https://docs.python.org/3/library/unittest.mock.html#where-to-patch)）。
関数を別 module へ移して `cli_main` から re-export しても、移動先関数は **移動先 module の globals**
から名前を解決する。したがって次のような現行 patch は、re-export だけでは移動先関数へ届かない:

```python
# 例: cmd_run が commands/run.py へ移り cli_main が re-export した場合、
#     patch は cli_main の名前空間の WorkflowRunner を差し替えるが、
#     移動後の cmd_run は commands/run.py の globals["WorkflowRunner"] を見る。
patch("kaji_harness.cli_main.WorkflowRunner")
```

この失敗は静かに起きる（patch が「効かない」だけで import は通る）ため、R1 前に全 patch target を
分類し、互換維持できるか／限定的 target 書換えが要るかを機械的に確定しておく必要がある。

### 着手時偵察で判明した patch target の分布（設計根拠。件数は R0 実装時に再計測）

tests から `kaji_harness.cli_main.<symbol>` を文字列 target として参照している主なシンボルは、
定義元により 2 群に分かれる。

| 群 | シンボル例（着手時偵察） | cli_main での由来 | re-export 時の懸念 |
|----|--------------------------|-------------------|--------------------|
| A: module import 由来 | `WorkflowRunner`（`from .runner`）, `subprocess`, `shutil`, `version`（`from importlib.metadata`）, `validate_skill_exists`（`from .skill`） | import 文で cli_main の globals に束縛 | 参照元関数が移動すると、移動先 globals の同名束縛を patch できず不達 |
| B: cli_main 内定義関数 | `_detect_repo`, `_forward_to_gh`, `_load_config_for_dispatch`, `_github_pr_review` | cli_main 内 `def` | 定義関数と呼び出し元が同一移動先へ移ると、cli_main target の patch が不達 |

いずれも「patch される名前」ではなく「patch される名前を lookup する関数がどこへ移るか」で可否が
決まる。R0 は **target → 参照元関数 → R1 移行先候補** の対応表を作り、この判定入力を固定する。

## インターフェース

本 Issue は「テスト追加 + 棚卸し」で閉じるため、公開 API の追加はない。入出力は R1 実装者への
**再現可能なベースラインと判断入力**として定義する。

### 入力

- 着手時 main（worktree `test/282` の HEAD）の `kaji_harness/cli_main.py`
- 着手時 main の tests ツリー全体（`cli_main` 参照箇所）

### 出力（生成物）

1. **characterization test**: `tests/test_cli_main_characterization.py`（新規。既存 test file を
   改変せず、追加分を独立 file に置いて棚卸し境界を明確化する）
2. **棚卸し成果物**（Issue コメントに記録。再現 command 付き）:
   - 行数・トップレベル関数一覧の再計測値
   - coverage 再実行結果（coverage 値 / missing lines / 実行件数 / 所要時間）
   - 全 66 関数の分類表（保護済み / 追加対象 / 対象外〔理由付き〕）
   - patch target 全件表（test file・test 名・target symbol・参照元・群 A/B・re-export 可否・R1 移行先候補）
3. **再現スクリプト**: `scripts/inventory_cli_main_patch_targets.sh`（棚卸しを再実行できる形で repo に記録）

### 使用例（R1 実装者の利用）

```bash
# R1 着手時: R0 が固定したベースラインを再取得
bash scripts/inventory_cli_main_patch_targets.sh   # patch target 表を再生成
pytest tests/test_cli_main_characterization.py -q   # 分割前の振る舞いを Green で確認
# 分割後、同 test が Green のままなら振る舞い保存。Red になれば patch 不達 or 挙動差を検知
```

### エラー / 判定できないケース

- coverage 未カバー行のうち、到達に外部 forge / 実 API を要する経路は **対象外（理由付き）**に
  分類し、characterization test を追加しない（下記「対象外の分類基準」）。

## 制約・前提条件（スコープ境界）

- production code（`kaji_harness/`）の diff は **0 行**。characterization test は現挙動を assert する
  のみで、挙動を変えない
- 既存 tests の import / patch target / test logic は **変更しない**（target 書換えは R1 = #283 の責務）
- 発見したバグは修正せず、別 Issue として起票する（[report-unrelated-issues](../../.claude/skills/_shared/report-unrelated-issues.md) に従う）
- 追加 test は既存の test 規約（`docs/dev/testing-convention.md`）とサイズマーカー付与に従う
- 件数（patch target 数・missing lines 等）は本設計に固定値を書かず、**着手時 main に対する再現可能な
  command / script の出力**を正本とする

## 変更スコープ

| 対象 | 変更種別 |
|------|----------|
| `tests/test_cli_main_characterization.py` | 新規追加（characterization test） |
| `scripts/inventory_cli_main_patch_targets.sh` | 新規追加（棚卸し再現スクリプト） |
| `kaji_harness/**` | 変更なし（diff 0 を完了条件で検証） |
| 既存 `tests/**` | 変更なし（棚卸しのみ。参照は read-only） |

## 方針（Minimal How）

R0 の作業は 4 パートで構成する。すべて着手時 main を正本とし、再現 command を成果物に残す。

### パート 1: ベースライン再計測

```bash
source .venv/bin/activate
wc -l kaji_harness/cli_main.py
grep -nE '^(def|async def) ' kaji_harness/cli_main.py          # トップレベル関数一覧
pytest --cov=kaji_harness.cli_main --cov-report=term-missing -m 'small or medium' -q
```

- 行数・関数数・coverage 値・missing lines・実行件数・所要時間を Issue コメントに記録する
- `term-missing` の missing lines を関数単位に対応づける（下記パート 2 の入力）
  （coverage 一次情報: [coverage.py term-missing](https://coverage.readthedocs.io/en/latest/cmd.html#text-annotation)）

### パート 2: 全 66 関数の分類

各トップレベル関数を次の 3 区分へ分類する。recovery/config 追加 7 関数
（`_add_recovery_arguments` / `_register_recover` / `_run_failure_triage` / `cmd_recover` /
`_resolve_recover_issue_context` / `_resolve_target_run_dir` / `cmd_config_artifacts_dir`）を必ず含める。

| 区分 | 判定基準 |
|------|----------|
| 既存テストで保護 | term-missing に当該関数の行が現れない、または主要分岐が既存 test で assert 済み |
| テスト追加対象 | missing lines を持ち、かつ Small / Medium で到達可能な正常系・エラー分岐 |
| 対象外（理由付き） | 下記「対象外の分類基準」に該当 |

**対象外の分類基準**（いずれかに該当し、理由を明記した場合のみ対象外）:

- 到達に実 forge / 実 API 疎通（Large_forge 相当）が必要で、Small/Medium で再現不能
- `argparse` の自動生成 usage / `sys.exit` 直後の到達不能行など、振る舞い保護価値が無い行
- 既存の同等 test が同じ分岐を既に固定している（重複回避）

### パート 3: patch target 棚卸しと分類（R1 判断入力の中核）

`scripts/inventory_cli_main_patch_targets.sh` として再現スクリプト化する。最低限の抽出源:

```bash
# 文字列 target
grep -rnoE 'kaji_harness\.cli_main\.[A-Za-z_][A-Za-z0-9_]*' tests/
# patch.object / monkeypatch
grep -rnE '(patch\.object|monkeypatch\.(setattr|delattr))\([^)]*cli_main' tests/
# module object 経由の属性書換え / cli_main 参照 fixture・helper
grep -rnE 'from kaji_harness import cli_main|import kaji_harness\.cli_main' tests/
```

各 target について次を記録する（対応表）:

| 列 | 内容 |
|----|------|
| test file / test 名 | patch を適用している test の識別子 |
| target symbol | `kaji_harness.cli_main.<symbol>` の symbol |
| 参照元 | その symbol を lookup する cli_main 内関数（例: `WorkflowRunner` → `cmd_run`） |
| 群 | A（module import 由来）/ B（cli_main 内定義関数） |
| re-export 可否 | 参照元関数の R1 移行先候補に基づく暫定判定（維持可能 / 維持不可能） |
| R1 移行先候補 | 参照元関数が移る先の `kaji_harness/commands/<mod>.py`（暫定） |
| 対応方針 | 互換 shim 維持 / target 機械的書換え（→ 新 target 案） |

re-export 可否の判定則（[Where to patch](https://docs.python.org/3/library/unittest.mock.html#where-to-patch) に基づく）:

- 参照元関数が cli_main に残る → cli_main の名前空間で lookup されるため **維持可能**
- 参照元関数が別 module へ移る → 移動先 globals で lookup されるため cli_main target は **維持不可能**
  → R1 で `patch("kaji_harness.commands.<mod>.<symbol>")` への書換え候補を記録

移行先候補は R1 の分割案（`kaji_harness/commands/`）に基づく暫定値であり、確定は R1 で行う。R0 は
「どの target が確定判断を要するか」を漏れなく列挙することを責務とする。

### パート 4: characterization test の追加

パート 2 の「テスト追加対象」の主要正常系・主要エラー分岐に対し、現挙動を assert する test を
`tests/test_cli_main_characterization.py` に追加する。

- 既存 test の patch スタイル（`patch("kaji_harness.cli_main.<symbol>")`）を踏襲し、既存 target を
  **変更しない**（R0 は棚卸しのみ）
- `subprocess.run` の名前空間 patch は `docs/dev/testing-convention.md` の
  「`subprocess.run` patch スコープ」表に従う（dispatch/provider 結合経路では名前空間 patch を避け、
  fixture 系 A/B を用いる）
- 各 test に size marker（`@pytest.mark.small` / `@pytest.mark.medium`）を付与する

## テスト戦略

### 変更タイプ

**実行時コード変更ではない**（production code diff 0）。本 Issue の成果物そのものが characterization
test（恒久回帰テスト）である。したがって「テスト追加 = 本 Issue の deliverable」であり、追加 test の
検証観点をサイズ別に定義する。

#### Small テスト

- 純粋分岐関数の characterization: `_is_ascii_decimal` / `_has_approve_flag` /
  `_has_request_changes_flag` / `_compose_json_and_jq` / `_resolve_verdict_marker` /
  `_has_verdict_flags` 等の入力→出力を現挙動で固定
- parser 構築（`create_parser` / `_register_*` / `_add_recovery_arguments`）の subcommand・
  option 表面を assert（argparse namespace の形）
- version 解決（`_get_version`）の正常系と `PackageNotFoundError` フォールバック分岐

#### Medium テスト

- dispatch 経路（`cmd_run` / `cmd_recover` / `_handle_issue` / `_handle_pr`）の主要正常系と
  主要エラー分岐を、既存 patch 対象（`WorkflowRunner` / `_load_config_for_dispatch` /
  `_detect_repo` / `subprocess`）を現行 target のまま固定
- local provider 経路（`_local_issue_*` / `_commit_local_issue_change`）のファイル I/O 挙動
- `_run_failure_triage` / `_resolve_recover_issue_context` / `_resolve_target_run_dir` /
  `cmd_config_artifacts_dir` の追加 7 関数分岐

#### Large テスト

- **追加しない**。実 forge / 実 API 疎通を要する経路は R0 の characterization 対象外
  （パート 2「対象外の分類基準」）とし、理由を棚卸し表に明記する。R1 の safety net には
  Small/Medium で到達可能な分岐固定で十分であり、Large は本 Issue のスコープ（分割前の
  振る舞い写し取り）に対して回帰検出情報を増やさない。

### 検証の合否

- 追加 test を含む全 test が PASS（`pytest`、`-m` フィルタなしで全実行）
- `kaji_harness/` の diff が 0 行（`git diff --stat main..HEAD -- kaji_harness/` が空）
- 既存 tests に diff が無い（棚卸しは read-only）
- `make check` 通過

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ不変（分割は R1） |
| docs/dev/ | なし | 既存 testing-convention に従うのみ |
| docs/reference/ | なし | API 仕様・規約変更なし |
| docs/cli-guides/ | なし | CLI 仕様不変 |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |

> 棚卸し表・再現スクリプトは R1（#283）設計の入力として引き渡す。恒久 docs への昇格は R0 では
> 行わず、Issue コメントと `scripts/` 配下スクリプトに留める（時限的な移行アーティファクトのため）。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| unittest.mock — Where to patch | https://docs.python.org/3/library/unittest.mock.html#where-to-patch | 「patch by looking up an object in the *namespace where it is looked up*」。re-export 後に patch が不達になる根拠。群 A/B の re-export 可否判定の一次情報 |
| coverage.py — Text reporting (`term-missing`) | https://coverage.readthedocs.io/en/latest/cmd.html#text-annotation | 未カバー行番号の出力仕様。関数単位分類（パート 2）の入力ソース |
| 対象コード | `kaji_harness/cli_main.py`（着手時 main の HEAD） | 2,421 行 / トップレベル関数 66 個。分類・棚卸しの正本 |
| 既存 patch 参照 | `tests/`（`kaji_harness.cli_main.<symbol>` 参照箇所） | 群 A（`WorkflowRunner`/`subprocess`/`shutil`/`version`/`validate_skill_exists`）・群 B（`_detect_repo`/`_forward_to_gh`/`_load_config_for_dispatch`/`_github_pr_review`）の実在確認。件数は R0 実装時に再計測 |
| テスト規約 | `docs/dev/testing-convention.md` | サイズ定義・`subprocess.run` patch スコープ・恒久テスト要否の判定基準 |
