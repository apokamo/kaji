# [設計] kaji run の run_id / run_dir 一意性を保証する

Issue: #292

## 概要

`kaji run` の artifact run directory を、同一 Issue で同一分または同一秒に複数回起動しても衝突しない方式へ変更する。既存 CLI の入力・遷移仕様は維持し、`runs/<run_id>/` の命名と作成処理だけを局所的に修正する。

## 背景・目的

### Observed Behavior (OB)

現状の `WorkflowRunner.run()` は run directory を `self.artifacts_dir / canonical_id / "runs" / datetime.now().strftime("%y%m%d%H%M")` で決め、`run_dir.mkdir(parents=True, exist_ok=True)` で作成する。そのため同一 Issue の同一分内再実行では既存 directory がそのまま再利用される。

既存 artifact には、`run.log` に `workflow_start` が 2 件ある run directory が 7 件残っている。

- `.kaji-artifacts/287/runs/2607092319/run.log`
- `.kaji-artifacts/269/runs/2607080138/run.log`
- `.kaji-artifacts/243/runs/2607032157/run.log`
- `.kaji-artifacts/242/runs/2607051332/run.log`
- `.kaji-artifacts/184/runs/2605260005/run.log`
- `.kaji-artifacts/153/runs/2605031543/run.log`
- `.kaji-artifacts/local-pc5090-5/runs/2605091612/run.log`

手元確認では上記すべてで `"event": "workflow_start"` が 2 行ある。代表例では、1 回目の `workflow_end` が `ERROR`、20 秒後の再実行が同じ `runs/2607092319/` に `workflow_start` を追記している。別例では `dev` と `docs` の異なる workflow も同じ run directory に混在している。

混線は `run.log` だけではない。`allocate_attempt_dir()` は `run_dir/steps/<step_id>/` 配下の既存 `attempt-*` 数から次番号を作るため、2 回目の workflow run は 1 回目の attempt の続き番号を採番する。結果として `prompt.txt` / `logs` / `verdict.yaml` / `result.json` の対応関係は attempt 単位では守られても、workflow run 境界では守られない。

### Expected Behavior (EB)

1 回の `kaji run` 実行は、一意な `runs/<run_id>/` を持つ。各 run directory の `run.log` は当該 workflow run の `workflow_start` と `workflow_end` を 1 対だけ含み、`steps/<step_id>/attempt-NNN/` の採番は同一 workflow run 内の retry / cycle / resume にだけ閉じる。

この期待値は、`allocate_attempt_dir()` の docstring が「prompt / logs / verdict の対応関係を一意に保つ」としていること、ADR 005 が attempt layout を `runs/<run_id>/steps/<step_id>/attempt-NNN/` と定義していること、`docs/ARCHITECTURE.md` が artifact layout を run / step / attempt の階層として説明していることに基づく。

## 再現手順

1. kaji project と有効な workflow YAML、対象 Issue を用意する。provider は local / github のどちらでもよい。
2. 同一分内に同じ Issue へ `kaji run` を 2 回実行する。agent dispatch を避けて決定的に再現するには、最初の step を `--before` に指定する。

   ```bash
   kaji run .kaji/wf/dev.yaml <issue_id> --before <first_step>
   kaji run .kaji/wf/dev.yaml <issue_id> --before <first_step>
   ```

3. 現状は `runs/` 配下に同じ `YYMMDDHHMM` directory が 1 つだけ作られ、その `run.log` に `workflow_start` が 2 行記録される。

## 根本原因

根本原因は次の 2 点の組み合わせである。

1. `kaji_harness/runner.py` の run directory 生成が分精度の `strftime("%y%m%d%H%M")` だけを使う。Python の `strftime()` は指定された format string に従って日時文字列を作るため、format に秒を含めなければ同一分内の値は同じになる。
2. 同じ `Path` に対して `mkdir(parents=True, exist_ok=True)` を呼ぶ。Python の `Path.mkdir()` は `exist_ok=True` の場合、対象が既存 directory なら `FileExistsError` を出さないため、衝突を検知せず再利用する。

この状態で `RunLogger._write()` が `open(log_path, "a", encoding="utf-8")` を使うため、同じ `run.log` へ後続 run のイベントが追記される。Python の `open(..., "a")` は既存ファイル末尾への追記モードなので、混在は仕様どおり発生する。

`git blame` では、分精度の timestamp は 2026-05-06 の `7050c58c`、`exist_ok=True` と `RunLogger(log_path=run_dir / "run.log")` は 2026-03-10 の `465b3809` に由来する。artifact layout が attempt 単位へ改善された後も、run directory 自体の一意性が未保証だったことが同根の残存欠陥である。

同じ原因で壊れる箇所の調査結果:

- `allocate_attempt_dir()` は `run_dir` が共有されると run 跨ぎで attempt を連番化する。run directory が一意になれば既存ロジックのまま run 内 retry / cycle に限定される。
- `RunLogger` の append mode は同じ `run.log` が再利用される限り混線する。run directory が一意になれば append mode は 1 run 内のイベント追記として正しく機能する。
- `docs/ARCHITECTURE.md` は `run_id` を分精度と明記しており、実装変更と同時に更新が必要。
- `docs/adr/005-artifact-primary-verdict.md` / `docs/adr/006-attempt-result-json.md` / `docs/reference/python/logging.md` は `runs/<run_id>/...` layout を説明するが、具体的な timestamp format は固定していないため、format 変更による本文更新は原則不要。

## インターフェース

### 入力

- CLI 入力: 既存の `kaji run <workflow> <issue> [--from ...] [--single-step ...] [--before ...] [--reset-cycle]` を維持する。
- 内部入力: `WorkflowRunner.run()` が解決する `artifacts_dir`、canonical issue id、workflow 名、現在時刻。

### 出力

- 変更前: `<artifacts_dir>/<canonical_id>/runs/<YYMMDDHHMM>/`
- 変更後: `<artifacts_dir>/<canonical_id>/runs/<YYMMDDHHMMSS>/` を基本形にし、同一秒内衝突時は `<YYMMDDHHMMSS>-NNN` を使う。
  - 例: `260710014301`
  - 同一秒内 2 件目: `260710014301-002`
  - 同一秒内 3 件目: `260710014301-003`

`run.log`、`steps/<step_id>/attempt-NNN/`、`latest` symlink、`result.json`、`verdict.yaml` の配置は変更しない。run_id は引き続き path が source of truth であり、`result.json` 等に新しい field は追加しない。

### 使用例

```bash
kaji run .kaji/wf/dev.yaml 292 --before design
kaji run .kaji/wf/dev.yaml 292 --before design

find .kaji-artifacts/292/runs -maxdepth 1 -mindepth 1 -type d | sort
# .kaji-artifacts/292/runs/260710014301
# .kaji-artifacts/292/runs/260710014301-002
```

## 制約・前提条件

- 既存 artifact の移行・リネームはしない。過去の `YYMMDDHHMM` directory は履歴としてそのまま読む。
- `kaji run` の CLI IF、workflow YAML、verdict status、state file の schema は変更しない。
- run_id は人間が時系列を推測でき、単純な lexicographic sort でも概ね時系列になる形式を維持する。既存 tests の `_run_root()` のような sorted directory selection を壊さない。
- 秒精度だけでは同一秒起動・自動 recovery・並列プロセスで再衝突するため、atomic な衝突時 suffix 採番を併用する。
- suffix 採番は `Path.mkdir(exist_ok=False)` の成功/`FileExistsError` を基準に行い、事前存在チェックと作成の race を避ける。
- `datetime.now()` の timezone semantics は今回の主題ではない。既存のローカル時刻ベースを維持し、format 精度と衝突処理に scope を絞る。

## 方針

`WorkflowRunner.run()` 内の inline path construction を小さな helper に切り出し、run directory を「生成してから返す」責務に閉じ込める。

方針:

1. `runs_dir = self.artifacts_dir / canonical_id / "runs"` を作り、親 directory は `mkdir(parents=True, exist_ok=True)` で作成する。
2. `base_id = datetime.now().strftime("%y%m%d%H%M%S")` を作る。
3. `candidate = runs_dir / base_id` を `candidate.mkdir(exist_ok=False)` で作成する。
4. `FileExistsError` の場合は `base_id-002`, `base_id-003`, ... を順に試す。
5. 作成に成功した `candidate` だけを `RunLogger(log_path=candidate / "run.log")` と `allocate_attempt_dir(candidate, step_id)` に渡す。

疑似コード:

```python
def allocate_run_dir(runs_dir: Path, timestamp: datetime) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    base_id = timestamp.strftime("%y%m%d%H%M%S")

    for sequence in itertools.count(1):
        name = base_id if sequence == 1 else f"{base_id}-{sequence:03d}"
        candidate = runs_dir / name
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
```

設計上の非採用案:

| 案 | 判断 |
|----|------|
| 秒精度化のみ | 同一秒内の 2 run で再衝突する。#288 の recovery のような即時再実行では不十分。 |
| 分精度 + suffix のみ | 正しくはなるが、同一分内の通常再実行が常に suffix になり、既知の低精度 timestamp を残す。 |
| UUID / random suffix | 一意性は高いが、人間が artifact directory を時系列に追いにくくなる。timestamp + deterministic suffix で十分。 |
| `run.log` を truncate mode にする | log 混線を隠すだけで `steps/` attempt 混線は残る。run directory の一意性を直す必要がある。 |

## テスト戦略

### 変更タイプ

- 実行時コード変更

### Small テスト

- 新 helper の pure-ish な採番ロジックを検証する。
  - 空の `runs_dir` では `<YYMMDDHHMMSS>` directory が作成される。
  - 同じ base id が既にある場合は `<YYMMDDHHMMSS>-002` が作成される。
  - `-002` も既にある場合は `-003` へ進む。
- `Path.mkdir(exist_ok=False)` の `FileExistsError` を衝突として扱うことを検証する。事前存在チェックだけに依存しないことがポイント。

### Medium テスト

- `tests/test_verdict_artifact_runner.py` に runner-level regression test を追加する。
- `WorkflowRunner.run()` を同一 `tmp_path` / 同一 local issue / 同一 fixed timestamp で 2 回起動する。agent dispatch を避けるため `before_step` に最初の step を指定し、既存 helper と同様に `validate_skill_exists` を patch する。
- 検証観点:
  - `runs/` 配下に 2 directory が作られる。
  - 1 件目は base id、2 件目は `-002` suffix を持つ。
  - 各 `run.log` の `workflow_start` は 1 行だけで、2 run が同じ file に追記されない。
- 追加で、通常 dispatch ありの既存 Medium tests が `attempt-001` を維持することにより、`allocate_attempt_dir()` が run 内に閉じていることを間接的に確認する。

### Large テスト

- 外部 API / forge 疎通は不要。対象は local filesystem 上の artifact directory 作成と runner wiring であり、Medium test で実際の filesystem と runner を通す。
- 実装後の最終確認として `make check` を通す。

### 実ログによる Red 代替

Issue 本文に列挙された 7 件の既存 artifact は OB を直接示す実世界ログであり、実装前 Red 証跡の代替にできる。ただし恒久回帰テストは追加し、修正後に Green になることを必須とする。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | ADR 005 / 006 は layout を `runs/<run_id>/...` として説明し、具体的な分精度 format を契約化していない。 |
| docs/ARCHITECTURE.md | あり | `run_id` が分精度（`%y%m%d%H%M`）と明記されているため、秒精度 + suffix 採番へ更新する。 |
| docs/dev/ | なし | workflow 手順や skill 契約は変わらない。 |
| docs/reference/ | なし | `docs/reference/python/logging.md` は run.log の位置と RunLogger 契約を説明しており、run_id format 固定はない。 |
| docs/cli-guides/ | なし | `kaji run` の CLI IF は不変。 |
| AGENTS.md / CLAUDE.md | なし | 開発規約・運用規約は変わらない。 |

## 完了条件の段階確認

- 設計書で根本原因が特定されている: 分精度 run_id と `exist_ok=True` による衝突黙認、append log、attempt 採番への波及を記載済み。
- run_id 生成方式の修正方針が確定している: 秒精度 base + atomic suffix 採番を採用する。
- 再現テスト方針がある: 同一 fixed timestamp で `WorkflowRunner.run()` を 2 回起動する Medium regression test を追加する。
- 同根の壊れ箇所の調査結果がある: `allocate_attempt_dir()`、`RunLogger`、関連 docs を調査済み。
- 影響モジュール全体のテスト green / `make check` 通過: 実装段階で確認する。
- run_id 形式に言及する docs 更新: `docs/ARCHITECTURE.md` を実装段階で更新する。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #292 本文 | https://github.com/apokamo/kaji/issues/292 | OB として 7 件の既存 artifact、EB、再現手順、完了条件が記載されている。 |
| runner の run_dir 生成 | `kaji_harness/runner.py:479-486` | `datetime.now().strftime("%y%m%d%H%M")` と `run_dir.mkdir(parents=True, exist_ok=True)` により同一分内の同一 path を再利用する。 |
| attempt 採番 | `kaji_harness/runner.py:58-84` | `run_dir/steps/<step_id>/attempt-NNN/` 配下の既存 attempt 数から採番するため、run_dir 共有時は run 跨ぎで連番化する。 |
| RunLogger append 実装 | `kaji_harness/logger.py:18-29` | `open(..., "a")` で `run.log` に JSONL event を追記する。run_dir 共有時は複数 run の event が同じ file に混在する。 |
| artifact layout docs | `docs/ARCHITECTURE.md:361-377` | run / step / attempt layout と、現状の `run_id` 分精度記述がある。実装変更時に更新対象。 |
| ADR 005 | `docs/adr/005-artifact-primary-verdict.md` | attempt layout を `runs/<run_id>/steps/<step_id>/attempt-NNN/` とし、run.log は `runs/<run_id>/` 直下とする決定。 |
| Python datetime docs | https://docs.python.org/3/library/datetime.html#strftime-and-strptime-behavior | `strftime()` は explicit format string に従って文字列を作る。秒を format に含める必要がある。 |
| Python pathlib docs | https://docs.python.org/3/library/pathlib.html#pathlib.Path.mkdir | `Path.mkdir(exist_ok=True)` は既存 directory で `FileExistsError` を出さない。衝突検知には `exist_ok=False` が必要。 |
| Python open docs | https://docs.python.org/3/library/functions.html#open | mode `a` は既存ファイル末尾への append。`RunLogger` の追記挙動の裏付け。 |
