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

### 分類の 3 軸（Issue 完了条件との対応）

Issue 完了条件は patch target を「module 経由 / 直接シンボル」および「re-export で維持可能 / 維持
不可能」に分類することを要求する。この 2 つは **別軸** であり、さらに補助として **シンボル定義元**
を加えた 3 軸で分類する。混同を避けるため各軸を独立列として定義する。

**軸 1: 参照形態（Issue 完了条件の「module 経由 / 直接シンボル」に対応）** — test が target をどう
差し替えるか、の形式。re-export 互換の第一決定要因。

| 値 | 定義 | 該当パターン | re-export 時の性質 |
|----|------|--------------|--------------------|
| **module 経由** | cli_main の module 名前空間を通して名前を再束縛し、consumer が module globals lookup で解決する形 | `patch("kaji_harness.cli_main.X")` / `monkeypatch.setattr(cli_main, "X", ...)` / `cli_main.X = ...` / これらを行う helper・fixture | consumer が別 module へ移ると cli_main の束縛を patch しても不達（脆弱） |
| **直接シンボル** | import 済みオブジェクトの **同一性** に対して差し替える形 | `patch.object(<import 済みクラス/関数>, "attr")` / 直接 import した object を identity 参照 | どの module から参照されても同一 object を解決するため頑健 |

**軸 2: シンボル定義元（補助情報）** — target symbol が cli_main でどこに束縛されるか。参照形態が
module 経由のとき、R1 移行先を推定する材料になる。

| 値 | 定義 | 着手時偵察のシンボル例 |
|----|------|------------------------|
| **import 束縛** | cli_main の import 文で globals に束縛 | `WorkflowRunner`（`from .runner`）, `subprocess`, `shutil`, `version`（`from importlib.metadata`）, `validate_skill_exists`（`from .skill`） |
| **cli_main 内定義** | cli_main 内 `def` | `_detect_repo`, `_forward_to_gh`, `_load_config_for_dispatch`, `_github_pr_review` |

**軸 3: re-export 可否** — 軸 1 と「参照元関数の R1 移行先」から導出する（判定則はパート 3）。

軸 1 と軸 2 は独立である（例: 参照形態＝module 経由でも、シンボル定義元は import 束縛のことも
cli_main 内定義のこともある）。R0 は **target → 参照形態 → シンボル定義元 → 参照元関数 → R1 移行先
候補 → re-export 可否** を全件表に出し、この判定入力を固定する。

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
   - patch target 全件表（test file・test 名・target symbol・**参照形態〔module 経由 / 直接シンボル〕**・
     **シンボル定義元〔import 束縛 / cli_main 内定義〕**・参照元関数・re-export 可否・R1 移行先候補）
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

#### 3-a. 抽出スクリプトの出力契約

`scripts/inventory_cli_main_patch_targets.sh` は **候補抽出 + 機械決定可能列の自動生成** を担う
（全列の自動生成は行わない）。参照形態を漏れなく表へ出すため、下記の全パターンを走査対象にする。

| 参照形態 | 抽出パターン（grep 正規表現の骨子） |
|----------|-------------------------------------|
| module 経由: 文字列 target | `(patch|mocker\.patch)\(["']kaji_harness\.cli_main\.<sym>["']` |
| module 経由: monkeypatch / 属性代入 | `monkeypatch\.(setattr|delattr)\(\s*(kaji_harness\.)?cli_main` / `cli_main\.<sym>\s*=` |
| 直接シンボル: patch.object | `patch\.object\(\s*<import 済みオブジェクト>` かつ対象が cli_main 由来 import |
| helper/fixture 経由 | `import kaji_harness\.cli_main` / `from kaji_harness import cli_main` を持つ fixture・helper |

スクリプトが **自動生成する列**（機械決定可能、再実行で再現）:

- `test_file`、`line`、`test_name`（当該行を含む `def test_...` / fixture 名）
- `target_symbol`
- `参照形態`（上表パターンのどれに一致したか）
- `シンボル定義元`（`cli_main.py` の import 文 / `def` を突き合わせて `import 束縛` か `cli_main 内定義` を判定）

スクリプトが **自動生成しない列**（人手判断。committed table に手で埋める）:

- `参照元関数`（symbol を lookup する cli_main 内関数）、`re-export 可否`、`R1 移行先候補`、`対応方針`

**再現契約と合否条件**:

- スクリプトは自動生成列のみの CSV/表を **決定的順序**（`test_file`,`line` ソート）で stdout に出力し、
  exit 0 で終了する
- 自動生成列のベースラインを `docs`/artifact ではなく Issue コメント + committed 表として固定する。
  再実行時に自動生成列を baseline と `diff` し、**差分ゼロ = 棚卸し再現成功**とする（drift 検知）。
  新規/消失 target があれば diff に現れ、手動列の追補が必要な箇所を特定できる
- 合否: (1) 上表の全参照形態パターンが grep 対象に含まれる、(2) 着手時 main に対しスクリプトが
  exit 0、(3) 自動生成列が committed baseline と一致（drift なし）、を満たすこと

#### 3-b. 全件対応表の列

| 列 | 生成 | 内容 |
|----|------|------|
| test file / test 名 | 自動 | patch を適用している test / fixture の識別子 |
| target symbol | 自動 | `kaji_harness.cli_main.<symbol>` の symbol |
| 参照形態 | 自動 | **module 経由 / 直接シンボル**（3-a のパターン一致。Issue 完了条件の分類軸） |
| シンボル定義元 | 自動 | **import 束縛 / cli_main 内定義**（軸 2） |
| 参照元関数 | 手動 | その symbol を lookup する cli_main 内関数（例: `WorkflowRunner` → `cmd_run`） |
| re-export 可否 | 手動 | 3-c の判定則による（維持可能 / 維持不可能） |
| R1 移行先候補 | 手動 | 参照元関数が移る先の `kaji_harness/commands/<mod>.py`（暫定） |
| 対応方針 | 手動 | 互換 shim 維持 / target 機械的書換え（→ 新 target 案） |

#### 3-c. re-export 可否の判定則（[Where to patch](https://docs.python.org/3/library/unittest.mock.html#where-to-patch) に基づく）

参照形態（軸 1）を第一決定要因とし、module 経由のときのみ参照元関数の移行先で分岐する:

- **参照形態＝直接シンボル** → object 同一性で解決するため **維持可能**（移行先に依存しない）
- **参照形態＝module 経由 かつ 参照元関数が cli_main に残る** → cli_main 名前空間で lookup されるため **維持可能**
- **参照形態＝module 経由 かつ 参照元関数が別 module へ移る** → 移動先 globals で lookup されるため
  cli_main target は **維持不可能** → R1 で `patch("kaji_harness.commands.<mod>.<symbol>")` への
  書換え候補を記録

移行先候補は R1 の分割案（`kaji_harness/commands/`）に基づく暫定値であり、確定は R1 で行う。R0 は
「どの target が確定判断を要するか」を漏れなく列挙することを責務とする。

### パート 4: characterization test の追加

パート 2 の「テスト追加対象」の主要正常系・主要エラー分岐に対し、現挙動を assert する test を
`tests/test_cli_main_characterization.py` に追加する。

**既存 patch と新規 test の分離（規約適用範囲）**:

- **棚卸し対象の既存 test（read-only）**: 参照形態・target は記録するのみで **変更しない**（target
  書換えは R1 の責務）。既存 dispatch test が `cli_main.subprocess.run` 名前空間 patch を使っていても、
  R0 では改変せず記録する。規約違反を発見した場合は観測として報告し、修正は R0 では行わない
  （[report-unrelated-issues](../../.claude/skills/_shared/report-unrelated-issues.md)）
- **R0 が新規追加する characterization test**: `docs/dev/testing-convention.md` の
  「`subprocess.run` patch スコープ」表（dispatch/provider 結合では `cli_main.subprocess.run` 名前空間
  patch を **禁止**）に従い、新規に規約違反 patch を **導入しない**

**新規 test の関数別 test double 方針（一意化）**:

| 対象関数 | 使用する test double / fixture | `cli_main.subprocess.run` 名前空間 patch |
|----------|-------------------------------|------------------------------------------|
| `_handle_issue` / `_handle_pr`（dispatch/provider 結合） | 系統 A: `git init -q --initial-branch=main` fixture、または系統 B: `patch("kaji_harness.providers.resolve_main_worktree", return_value=...)` + provider 境界の mock | **使わない**（規約 132–142 行の禁止に従う） |
| `cmd_run` / `cmd_recover`（WorkflowRunner 境界） | `patch("kaji_harness.cli_main.WorkflowRunner")` / `patch("kaji_harness.cli_main._load_config_for_dispatch")`（class/関数境界の mock。subprocess.run 名前空間 patch ではない） | 使わない |
| `_forward_to_gh` / `_detect_repo` 単体（subprocess 分岐が検証主対象の unit） | subprocess mock 許可（規約表「`resolve_main_worktree()` 自身の Small unit test」と同じく、戻り値・例外分岐の検証には mock 必須） | 当該 unit のみ許可 |
| `_local_issue_*` / `_commit_local_issue_change`（local provider I/O） | `tmp_path` fixture による実ファイル I/O + `git init` fixture | 使わない |

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

- dispatch 経路の主要正常系・主要エラー分岐。**test double はパート 4 の関数別方針表に従う**:
  - `cmd_run` / `cmd_recover` → `WorkflowRunner` / `_load_config_for_dispatch` の class/関数境界 mock
  - `_handle_issue` / `_handle_pr`（dispatch/provider 結合）→ 系統 A（`git init` fixture）または
    系統 B（`patch("kaji_harness.providers.resolve_main_worktree", ...)`）。
    `cli_main.subprocess.run` 名前空間 patch は新規導入しない（規約 132–142 行）
- local provider 経路（`_local_issue_*` / `_commit_local_issue_change`）のファイル I/O 挙動
  （`tmp_path` + `git init` fixture）
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
| unittest.mock — Where to patch | https://docs.python.org/3/library/unittest.mock.html#where-to-patch | 「patch by looking up an object in the *namespace where it is looked up*」。参照形態（module 経由 / 直接シンボル）が re-export 互換の第一決定要因になる根拠。パート 3-c 判定則の一次情報 |
| coverage.py — Text reporting (`term-missing`) | https://coverage.readthedocs.io/en/latest/cmd.html#text-annotation | 未カバー行番号の出力仕様。関数単位分類（パート 2）の入力ソース |
| 対象コード | `kaji_harness/cli_main.py`（着手時 main の HEAD） | 2,421 行 / トップレベル関数 66 個。分類・棚卸しの正本 |
| 既存 patch 参照 | `tests/`（`kaji_harness.cli_main.<symbol>` 参照箇所） | シンボル定義元＝import 束縛（`WorkflowRunner`/`subprocess`/`shutil`/`version`/`validate_skill_exists`）・cli_main 内定義（`_detect_repo`/`_forward_to_gh`/`_load_config_for_dispatch`/`_github_pr_review`）の実在確認。参照形態・件数は R0 実装時にスクリプトで再計測 |
| テスト規約 | `docs/dev/testing-convention.md` | サイズ定義・`subprocess.run` patch スコープ・恒久テスト要否の判定基準 |
