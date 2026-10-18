# [設計] attempt 一時ディレクトリを Issue 内通番にして AF_UNIX socket path 上限超過を解消する

Issue: #508

## 概要

`kaji_harness/runner.py::prepare_attempt_tmp_dir` が作る attempt 一時ディレクトリ
（`KAJI_TMP_DIR` / `TMPDIR` / `TMP` / `TEMP`）の path に run_id・step_id・`attempt-NNN` という可変長の
識別子が入っているため、`TMPDIR` の下に AF_UNIX socket を作るツール（Chrome / Playwright 等）が
`sun_path` 上限を超えて起動できない。layout を Issue 決定事項の案 B
（`<project_root>/tmp/kaji/<issue_id>/<NNNN>/`、Issue 内の通し番号）に変え、path 長を project root の
長さだけで決まるようにする。

## 背景・目的

### Observed Behavior (OB)

利用側 repo（apokamo/kamo2、kaji 0.23.2、Linux / WSL2）の dev-small workflow で、`review-change` step の
`make gate-frontend`（Playwright + Chrome）が起動段階で全件失敗した（Issue 本文 § Observed Behavior、
apokamo/kamo2#1831 の review-change ABORT 報告と同一ログ）。

```text
FATAL:chrome/browser/process_singleton_posix.cc:313] Socket path too long:
/home/aki/dev/kamo2/tmp/kaji/1831/261018213458/review-change/attempt-001/com.google.Chrome.guSFOP/SingletonSocket
```

同じ run の `change` step（attempt dir 65 文字 → socket path 106 文字）は通り、`fix-change`（110）・
`review-change` / `verify-change`（113）は失敗した。差は step 名の長さだけである。

### Expected Behavior (EB)

project root が普通の深さ（例: `/home/<user>/dev/<repo>`）なら、issue 番号・run_id・step 名の長さによらず、
`TMPDIR` の下に socket を作る一般的なツールが起動できる（Issue 本文 § Expected Behavior / § 目的）。
#407 の目的（ツールごとに個別設定を足さずに一時ファイルの置き場所を揃える）は維持する。

## 再現手順（steps-to-reproduce）

Issue 本文 § 再現（Chrome なし）で固定済み。本設計でも同手順を正とする。

1. 前提: Linux（`sun_path` 108 byte）、Python 3（Chrome / Playwright 不要）
2. 操作:
   1. `D=/home/aki/dev/kamo2/tmp/kaji/1831/261018213458/review-change/attempt-001`（72 文字）
   2. `mkdir -p "$D/com.google.Chrome.guSFOP"`
   3. `python3 -c 'import socket,sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "$D/com.google.Chrome.guSFOP/SingletonSocket"`（113 文字）
3. 観測: 操作 2-3 が `OSError: AF_UNIX path too long` で失敗する

## 根本原因（Root Cause）

### なぜ間違っているか

- Linux の `sun_path` は 108 byte で、終端 NUL を除き path は 107 byte まで（unix(7)）。macOS は 104 byte
  （path は 103 byte まで）。
- Chrome は `TMPDIR` の後ろに `/com.google.Chrome.XXXXXX/SingletonSocket`（41 文字）を足す。したがって
  `TMPDIR` に使えるのは Linux で 66 byte まで。
- 現行 layout `<project_root>/tmp/kaji/<issue_id>/<run_id>/<step_id>/attempt-NNN` は、root より後ろに
  `/tmp/kaji/`（10）＋ issue_id ＋ `/` ＋ run_id（12、同秒衝突時は 16）＋ `/` ＋ step_id（任意長）＋
  `/attempt-NNN`（12）を持つ。issue 4 桁・step 名 13 文字（`review-change`）でも root 以降が 53 文字になり、
  root に残るのは 13 文字しかない。成否が step 名・run_id の長さに依存するため、同じ run でも step ごとに
  結果が変わる。

### いつから

- `f888f80 feat: set KAJI_TMP_DIR and standard temp env vars per attempt (#407)` で導入。
- #407 設計書 § 制約「path 長」は 108 byte 上限を認識していたが、例の 68 文字を「compact」と評価し、
  ツールが `TMPDIR` の後ろに足す文字数（Chrome で 41）を見積もらずに残存リスクとして docs 記載に留めた。
  その例でも Chrome の socket path は 109 byte で既に上限を超えている。

### 同根の他の壊れ箇所の調査

「一時ディレクトリの path を組み立てている、または前提にしている箇所」を
`grep -rn "tmp/kaji\|KAJI_TMP_DIR\|TMPDIR\|attempt-NNN\|socket\|gettempdir"`（`kaji_harness/` / `tests/` /
`docs/` / `.claude/skills/`）で洗い出した結果:

| 箇所 | 分類 | 対応 |
|------|------|------|
| `kaji_harness/runner.py::prepare_attempt_tmp_dir` | 根本原因（layout の組み立て） | 本 Issue で修正 |
| `kaji_harness/runner.py::_StepExecutor.execute`（呼び出し側。run_id / step_id / attempt_name を渡す） | 同上 | 新シグネチャに合わせて修正 |
| `kaji_harness/` 内で `TMPDIR` 配下に socket を作るコード | 該当なし（`socket` / `gettempdir` / `mkdtemp` の使用なし） | 対応不要 |
| `tests/test_attempt_tmp_dir.py`（旧 layout・`existing_target_raises`・4 component の不正値検査） | 旧 layout を前提にしたテスト | 新仕様に書き換え（テスト戦略参照） |
| `tests/test_tmp_env_propagation.py::_assert_attempt_env`（`path.parent.name == step_id` / `path.name == "attempt-001"`）・`test_each_attempt_gets_distinct_directory`（`endswith("attempt-001")`） | 旧 layout 前提 | 新 layout（`<issue_id>/<NNNN>`）の検査へ修正 |
| `tests/test_tmp_env_propagation.py:80`（`build_tmp_env` に渡すリテラル path） | 文字列を通すだけで layout 非依存 | 変更不要（任意で新 layout 風に更新） |
| `tests/test_tmp_dir_e2e_large_local.py:238,274`（`expected` に旧 layout） | 旧 layout 前提 | 新 layout へ修正 |
| `tests/test_tmp_dir_e2e_large_local.py:348,377`（wrapper 用の手組み path） | wrapper へ渡す任意 path。layout 非依存 | 変更不要 |
| `tests/test_result.py`（`result.json` の全キー一致 assert） | `AttemptResult` へのフィールド追加で影響 | `tmp_dir` を期待値へ追加 |
| `tests/test_interactive_terminal_herdr.py`（#465 で修正済みテスト） | 長い一時パスで `str(exc)` の 200 文字切り詰めに依存して失敗していた。#465 で `stderr` / `__cause__` 検査に変更済みで path 長非依存 | 変更不要（新 layout で path は短くなる方向のみ） |
| `tests/conftest.py::outside_project_tmp_path` | `TMPDIR` を参照しない platform 既定 temp を使う | 変更不要（新テストでも利用） |
| tmux 系テスト（#407 設計で `-L` を採用済み） | socket を `tmp_path` に置かない設計済み | 変更不要 |
| `docs/ARCHITECTURE.md` § attempt 固有の一時作業ディレクトリ（構成図・path 長注意）・§ `result.json` 表 | 旧構成の記述 | 更新（影響ドキュメント参照） |
| `docs/dev/skill-authoring.md:169`、`docs/dev/testing-convention.md:157` | 旧構成の記述 | 更新 |
| `docs/reference/python/logging.md` § `step_start` | `tmp_dir` フィールドと警告イベントの追加 | 更新 |
| `docs/dev/workflow-authoring.md:187` | 4 変数の注入のみ記述し layout は書いていない | 変更不要 |
| `.claude/skills/` | `tmp/kaji` を前提にする skill なし（ヒットは `/tmp/kaji-*` の無関係な一時ファイル名のみ） | 変更不要 |

## インターフェース

外部契約（4 環境変数の名前・同値性・注入経路、置き場所 `<project_root>/tmp/kaji/`、`.gitignore`、artifacts 構成）は
不変。変わるのは環境変数の **値（ディレクトリ構成）** と、記録の追加のみ。

### 入力

- `prepare_attempt_tmp_dir(project_root: Path, *, issue_id: str) -> Path`
  - 変更前: `issue_id` / `run_id` / `step_id` / `attempt_name` を受け取った
  - 変更後: `issue_id` のみ。`issue_id` は従来どおり `_validate_path_component` で検査する
  - `step_id` の path component 検査は呼び出し側（`_StepExecutor.execute` の artifacts 作成前の検査）に残す
    （Issue 決定事項「step_id の path component 検査は artifacts 側で引き続き必要なので残す」）
- 内部 API（`kaji_harness` の公開 export ではない）のため、呼び出し側は `runner.py` とテストのみ

### 出力

- 戻り値: 作成済みの `<project_root.resolve()>/tmp/kaji/<issue_id>/<NNNN>` 絶対パス
- 採番（Issue 決定事項 § 採番のルール）:
  - 形式 `f"{n:04d}"`（9999 超で桁が増える）
  - 次番号 = `tmp/kaji/<issue_id>/` 直下で名前が `^[0-9]{4,6}$` に合うディレクトリの最大値 + 1（無ければ 1）
  - `mkdir(exist_ok=False)` が `FileExistsError` なら番号を 1 進めて再試行（`allocate_run_dir` と同型の
    `itertools.count` ループ）
  - 旧構成の run_id ディレクトリ（12 桁、`-NNN` 付き）は正規表現に合わないため無視される
- エラー: component 不正・`FileExistsError` 以外の `OSError`（`tmp` が通常ファイル等）は従来どおり
  `TmpDirPreparationError`（dispatch しない）。「既存 dir との衝突」は再試行に変わるのでエラーにならない
- 記録:
  - `run.log` の `step_start` に `tmp_dir`（絶対パス文字列）を追加。`RunLogger.log_step_start` に
    keyword `tmp_dir: str | None = None` を足す（dispatch 前に書かれるので kaji が途中で落ちても残る）
  - `AttemptResult` に末尾 default 付き `tmp_dir: str | None = None` を追加。`_record_attempt_end` は
    正常経路・`_record_dispatch_failure` 経路の両方で `tmp_dir` を受け取り書く
- 長さ警告:
  - 純粋関数 `socket_path_budget_exceeded(tmp_dir: Path, *, platform: str = sys.platform) -> tuple[int, int] | None`
    （名前は two-way door）。`len(os.fsencode(str(tmp_dir))) + 41` が上限を超えたら `(length, limit)`、
    超えなければ `None`
  - 上限: `platform == "darwin"` → 103、それ以外 → 107
  - 41 は `len("/com.google.Chrome.XXXXXX/SingletonSocket")` で定義し、マジックナンバーにしない
  - 超過時: `run.log` に新イベント `tmp_dir_path_warning`（`step_id` / `tmp_dir` / `socket_path_length` /
    `limit`）を出し、console に `_console.warning` を出す。dispatch は続ける

### 使用例

```python
# runner.py _StepExecutor.execute（疑似コード）
_validate_path_component("step_id", step.id, self.project_root.resolve() / "tmp")  # 現行どおり
attempt_dir = allocate_attempt_dir(self.run_dir, step.id)
tmp_dir = prepare_attempt_tmp_dir(self.project_root, issue_id=self.run_ctx.canonical_id)
tmp_env = build_tmp_env(tmp_dir)
self._log_step_start(step, settings, session_id, attempt_dir, attempt_no, tmp_dir)  # step_start に tmp_dir
exceeded = socket_path_budget_exceeded(tmp_dir)
if exceeded is not None:
    length, limit = exceeded
    self.logger.log_tmp_dir_path_warning(step.id, tmp_dir=str(tmp_dir),
                                         socket_path_length=length, limit=limit)
    _console.warning("tmp dir may be too long for AF_UNIX sockets: %s (%d > %d)", ...)
# 以降 dispatch → _record_attempt_end(..., tmp_dir=tmp_dir)
```

```text
<project_root>/tmp/kaji/
  .gitignore     # "*"（変更なし）
  508/
    0001/        # 1 回目の dispatch（design attempt-001）
    0002/        # 2 回目（review-design attempt-001。run や step が変わっても通番）
```

## 変更スコープ

- `kaji_harness/runner.py`（`prepare_attempt_tmp_dir`、長さ判定関数、`_StepExecutor.execute` /
  `_log_step_start` / `_record_dispatch_failure`、`_record_attempt_end`）
- `kaji_harness/logger.py`（`log_step_start` の `tmp_dir`、`log_tmp_dir_path_warning`）
- `kaji_harness/result.py`（`AttemptResult.tmp_dir`）
- `kaji_harness/errors.py`（`TmpDirPreparationError` docstring の「既存 dir との衝突」を実態に合わせる程度）
- テスト・docs（下記）

## 制約・前提条件

- 置き場所は project 内 `tmp/kaji/` のまま（Issue 重要判断「一時ディレクトリの置き場所」）。
  `/tmp` 等へ移さない
- 4 変数・注入経路・`.gitignore`・artifacts layout は不変。同じ attempt の中の headless 一時的再試行
  （`execute_cli` 内の retry）は同じ env = 同じディレクトリを使う（現行どおり。tmp dir は `execute` 内で
  attempt ごとに 1 回だけ作るため自然に成立）
- 旧構成ディレクトリの移行・削除はしない（削除は #509）
- 並行実行（同一 Issue の複数 `kaji run`）は `mkdir(exist_ok=False)` の成否で一意性を担保する
- 長さ判定の 41 は Chrome 基準。これより長い suffix を足すツールは警告の対象外（警告は目安であり保証ではない）
- 警告は dispatch を止めない（socket を使わない step まで止めないため。Issue 決定事項 § 長さのチェック）
- `run.log` へのイベント・フィールド追加は追加的変更で、既存 consumer（`step_start` を読むテスト・recovery）は
  未知フィールドを無視する dict アクセスのため互換。`RUN_LOG_SCHEMA_VERSION` は上げない
- `result.json` の読み手（`artifact_verdict._ResultRecord` は `extra="ignore"`、`recovery/snapshot.py` は dict）は
  `tmp_dir` の有無に依存しない

## 方針

1. `prepare_attempt_tmp_dir` を「issue dir 作成 → 最大値 + 1 から `count` ループで `mkdir(exist_ok=False)`」に
   置き換える。`.gitignore` 作成ロジックと `OSError → TmpDirPreparationError` 変換は維持。境界不変条件
   （`target.parent.parent == base` と normpath 一致）も新 layout の段数に合わせて維持する
2. 長さ判定を純粋関数として切り出し、runner から呼んで警告を出す
3. `step_start` / `result.json` に `tmp_dir` を足す。`_record_attempt_end` の引数に keyword `tmp_dir` を追加し、
   2 つの呼び出し元から渡す
4. 最小侵襲。採番以外のリファクタは混ぜない

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 修正方式 | 案 2 を案 B 仕様（`<issue_id>/<NNNN>`、Issue 内通番）で実施 | 人間の決定（Issue 本文 § 決定事項・§ 重要判断「修正方式」。2026-10-19 の対話で承認） | `prepare_attempt_tmp_dir` の引数を `issue_id` のみにし、`count` ループで採番 |
| 置き場所 | project 内 `tmp/kaji/` のまま | 人間の決定（Issue § 重要判断「一時ディレクトリの置き場所」、#407 の決定） | 変更なし |
| 案 1（root 設定）・案 3（注入無効化モード） | 採らない | AI 提案を人間が承認（Issue § 重要判断） | 設定項目を追加しない |
| 削除 | 本 Issue で扱わない | 人間の決定（Issue § 重要判断、#509） | 採番は残存ディレクトリのみ参照し、削除後の振り直しを許容 |
| 採番ルール（4 桁ゼロ埋め・`^[0-9]{4,6}$` の最大値 + 1・`FileExistsError` 再試行・旧構成無視） | 決定どおり | 人間の決定（Issue § 採番のルール） | `allocate_run_dir` と同型の `itertools.count` ループで実装 |
| 記録項目 | `step_start.tmp_dir` と `AttemptResult.tmp_dir: str \| None = None`（末尾 default） | 人間の決定（Issue § どの step のディレクトリかを辿る記録） | `log_step_start` に keyword 追加、`_record_attempt_end` に keyword 追加し正常・異常の両経路から渡す |
| 長さチェック | `len(os.fsencode(path)) + 41` を Linux 107 / macOS 103 と比較し、超過時は警告のみ | 人間の決定（Issue § 長さのチェック） | 判定を純粋関数に分離。darwin 以外を 107 とする（下行） |
| Linux / macOS 以外の上限 | `sys.platform != "darwin"` はすべて 107 | AI の仮定。根拠: kaji の実行対象は Linux / macOS（Issue 記載の 2 platform）で、それ以外は決定範囲外。警告のみで dispatch を止めないため誤っても影響は表示だけ（two-way door）。検査先: review-design / review-code | — |
| 警告の出力先形式 | `run.log` に新イベント `tmp_dir_path_warning`、console は `_console.warning` | AI の仮定。根拠: Issue は「run.log と console に警告」とだけ定める。既存イベントは用途別に 1 イベント 1 メソッド（`log_verdict_sanitization` 等）の慣習。追加的なイベントで既存 consumer を壊さない（two-way door）。検査先: review-design / review-code | イベント名・フィールド名を設計で決定 |
| 関数名・定数名・引数の形 | `socket_path_budget_exceeded` / `_CHROME_SOCKET_SUFFIX` 等 | AI の仮定（Issue § 重要判断で「設計で決める」と委任済み。two-way door）。検査先: review-design / review-code | — |
| `RUN_LOG_SCHEMA_VERSION` | 上げない | AI の仮定。根拠: 同 version は `failure_event` の存在保証の判別用で、追加フィールド・追加イベントでは意味が変わらない。検査先: review-code | — |

one-way door の未決: なし（ディレクトリ構成・採番・記録・警告の方針はすべて Issue 本文の人間決定で固定済み。
設計で決めたのは内部名と platform 既定値・イベント形式で、いずれも追加的・後から安く変更可能）。

## テスト戦略

### 変更タイプ

- 実行時コード変更

### 実ログによる実装前 Red 代替

Issue 本文 § Observed Behavior の Chrome 実ログ（`Socket path too long: .../review-change/attempt-001/com.google.Chrome.guSFOP/SingletonSocket`）を
実装前 Red 証跡とする（Issue 完了条件で人間が指定、review-ready で admissible と確認済み）。恒久回帰テスト
（下記 Medium の AF_UNIX bind）は修正後に Green となることを必須とする。

### Small テスト

- `socket_path_budget_exceeded`:
  - Linux: path 66 byte → `None`、67 byte → `(108, 107)`（境界）
  - darwin: 62 byte → `None`、63 byte → `(104, 103)`
  - 非 ASCII 文字を含む path で文字数ではなく `os.fsencode` の byte 長で判定する
- `build_tmp_env`（既存テスト維持）
- `AttemptResult`:
  - `asdict` に `tmp_dir` が末尾キーとして入る（`tests/test_result.py` の全キー一致 assert を更新）
  - 旧形式 JSON（`tmp_dir` キーなし）から `AttemptResult(**data)` を組み立てると `tmp_dir is None`
  - 旧形式 JSON を `artifact_verdict.evaluate_result` に渡しても従来どおり評価できる
- `RunLogger.log_step_start(..., tmp_dir=...)` が `step_start` に `tmp_dir` を書く／省略時 `null`
- `RunLogger.log_tmp_dir_path_warning` のイベント形式

### Medium テスト

- `prepare_attempt_tmp_dir`（実 FS）:
  - 初回は `<root>/tmp/kaji/<issue_id>/0001`、2 回目は `0002`（正規化済み絶対パス・dir が存在）
  - 最大値 + 1: `0001` と `0005` が存在すれば `0006`（途中が欠けても重複しない）
  - 旧構成無視: `261018213458`・`261018213458-002`・`abc`・`123`（3 桁）・`0003` という名前の通常ファイルが
    あっても採番は dir 名の正規表現一致のみで決まる
  - `FileExistsError` 時の再試行: 次番号の dir を `mkdir` の直前に作られる競合を `Path.mkdir` の patch
    （1 回目のみ `FileExistsError`）で再現し、次の番号が返る
  - 9999 の次は `10000`
  - `.gitignore` の作成・既存 `.gitignore` 非上書き、`tmp` が通常ファイルなら `TmpDirPreparationError`
    （既存テスト維持）
  - `issue_id` の不正 component は副作用なしで拒否（既存 parametrize を `issue_id` に限定して維持）
- path 長の非依存性（完了条件 3）: 長い step_id・run_id（`-NNN` 付き）・大きい attempt 番号の dispatch を
  runner 経由で行っても、`KAJI_TMP_DIR` が `<root>/tmp/kaji/<issue_id>/<NNNN>` で長さが同じであることを
  `tests/test_tmp_env_propagation.py` の dispatch wiring テストで確認する（`_assert_attempt_env` を新 layout へ）
- `step_start` と `result.json` の `tmp_dir`: runner を exec step で 1 回実行し、`run.log` の `step_start.tmp_dir` と
  `attempt-001/result.json` の `tmp_dir` が env に渡った `KAJI_TMP_DIR` と一致する。dispatch 失敗経路
  （`ScriptExecutionError`）でも `result.json` に `tmp_dir` が入る
- 警告と継続（完了条件 6）: `socket_path_budget_exceeded` が超過を返すよう長い project root（または関数 patch）で
  runner を実行し、`run.log` に `tmp_dir_path_warning` が 1 件、console（`caplog`）に warning が出て、
  かつ exec が呼ばれ verdict が PASS になる。超過しない場合は警告イベントが出ない
  - 注意: workflow 内では `tmp_path` 自体がリポジトリ内の長い path になり、`tmp_path` を project root にした
    既存 runner テストでも警告が出うる。警告は dispatch を変えないため既存テストの結果には影響しないが、
    「警告が出ない」側の検査は `outside_project_tmp_path` 起点の短い root か判定関数の patch で環境非依存にする
- 回帰テスト（AF_UNIX、完了条件 4、Linux 限定 `skipif(sys.platform != "linux")`）:
  - `outside_project_tmp_path`（`/tmp` 配下）を起点に、長さがちょうど 47 byte の project root を作る
    （Issue 決定事項 § 長さの比較「Linux で 47 文字」。`tmp_path` は workflow 内ではリポジトリ内の長い path に
    なるため使わない）
  - issue_id `1831`（4 桁）で `prepare_attempt_tmp_dir` を呼び、戻り値の長さが 66 であること
  - `<tmp_dir>/com.google.Chrome.guSFOP/SingletonSocket` に実際に `socket.AF_UNIX` を bind できること
  - 同じ手順で 48 byte の root では `socket_path_budget_exceeded` が超過を返し、bind が
    `OSError` になること（境界がずれていないことの確認）

### Large テスト

- `tests/test_tmp_dir_e2e_large_local.py`（実 `kaji run` subprocess、`large_local`）の期待 path を
  `<repo>/tmp/kaji/99/0001`（2 本目のテストは同じ repo なら `0002` 等、各テストで fresh repo なら `0001`）に更新し、
  実プロセスへ新 layout の値が渡ることを維持する。新規 Large は追加しない（Chrome 実起動は CI 環境依存で
  再現性がなく、AF_UNIX bind の Medium テストが同じ OB の原因を直接検証するため。
  利用側 Chrome での確認は Issue § ワークフロー完了後の確認項目で扱う）

### 長時間 acceptance の配置

該当なし。nested `kaji run` は既存の `large_local` テスト（数秒規模の subprocess）のみで、外部 agent を含む
dogfood や fresh 環境での繰り返し acceptance は設計に含まない。利用側 repo での Chrome 確認は Release 後の
事後確認項目（workflow 外）として Issue 本文で分離済み。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 技術選定の変更はない（#407 の決定の範囲内での layout 変更）。ADR 006 は履歴として据え置く |
| docs/ARCHITECTURE.md | あり | § attempt 固有の一時作業ディレクトリの構成図（`<issue_id>/<NNNN>/`）・採番・path 長注意（root に使える長さ: Linux 47 / macOS 43、超過時の警告）、§ `result.json` 表に `tmp_dir` 行、`step_start` の `tmp_dir` 言及 |
| docs/dev/ | あり | `docs/dev/skill-authoring.md:169` の実体 path、`docs/dev/testing-convention.md:157` の `.../attempt-NNN/` 表記 |
| docs/reference/ | あり | `docs/reference/python/logging.md` § `step_start` に `tmp_dir`、新イベント `tmp_dir_path_warning` の節、メソッド表 |
| docs/cli-guides/ | なし | CLI 引数・出力の変更なし |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| CHANGELOG.md | なし（本 Issue では） | `/release` で記載する運用 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| unix(7) | https://man7.org/linux/man-pages/man7/unix.7.html | `struct sockaddr_un` の `sun_path[108]`。pathname socket は終端 NUL を含めてこの長さに収める必要がある → path は 107 byte まで |
| macOS / BSD `sys/un.h` | https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/un.h | `sun_path[104]` → 終端 NUL を除き 103 byte（Issue 決定事項の macOS 上限の根拠） |
| Chrome `process_singleton_posix.cc` | https://source.chromium.org/chromium/chromium/src/+/main:chrome/browser/process_singleton_posix.cc | `TMPDIR` 配下の一時ディレクトリに `SingletonSocket` を作り、長すぎると `Socket path too long` で FATAL（OB のログが同ファイルを指す） |
| Issue #508 本文 | `kaji issue view 508` | § Observed Behavior（実ログ・長さ表）、§ 再現、§ 決定事項（構成・採番・記録・長さチェック）、§ 重要判断 |
| apokamo/kamo2#1831 ABORT 報告 | https://github.com/apokamo/kamo2/issues/1831#issuecomment-6250122876 | 利用側の同一エラー（review-ready で照合済み） |
| #407 設計書 | `draft/design/issue-407-feat-kaji-tmp-dir.md` § 制約「path 長」 | 68 文字の例を compact とし、ツールが足す suffix を見積もらず残存リスク扱いにした（根本原因の「いつから」） |
| 現行実装 | `kaji_harness/runner.py::prepare_attempt_tmp_dir` / `allocate_run_dir` / `_StepExecutor.execute` / `_record_attempt_end` | 旧 layout の組み立てと、採番再試行の既存パターン（`count` + `mkdir` の `FileExistsError` で次へ） |
| `result.json` 読み手 | `kaji_harness/artifact_verdict.py::_ResultRecord`（`extra="ignore"`）、`kaji_harness/recovery/snapshot.py::_latest_attempt_result` | フィールド追加が既存読み手を壊さない根拠 |
| #465 修正 | `3413f73`（`tests/test_interactive_terminal_herdr.py`） | 長い一時パス起因のテスト失敗は path 長非依存の検査へ修正済み |
| Python `os.fsencode` | https://docs.python.org/3/library/os.html#os.fsencode | path を filesystem encoding の bytes に変換 → `sun_path` は byte 長で判定する根拠 |
