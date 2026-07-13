# [設計] 指定した Issue 群を直列実行する sequential series runner

Issue: #313

## 概要

明示された Issue 列に既存の単一 Issue workflow を記載順で適用する上位 runner
`kaji run-series <series.yaml>` と、その事前検証 `kaji validate-series <series.yaml>`、
および series YAML を安全に生成する `series-create` skill を追加する。
既存の `kaji run` / builtin workflow / provider 契約は変更しない。

## 背景・目的

### ユーザーストーリー

- **複数の関連 Issue を管理する maintainer として**、EPIC の有無にかかわらず順序と
  workflow を series YAML に一度記述し、前段が正常完了した場合だけ次の Issue を
  自動起動したい。
- **長時間の連続実行を運用する maintainer として**、途中で ABORT / ERROR が発生した
  場合は後続を起動せず、永続化された状態から安全に再開したい。
- **実行前に計画を確認する maintainer として**、設定の妥当性、Issue の実行順、
  確定済み workflow を副作用なしで確認したい。
- **series を準備する maintainer として**、順序付き Issue 番号と必要な workflow
  override を指定し、検証済みの series YAML を安全に生成したい。

### 代替案と不採用理由

Issue #313 本文「代替案と採否」を正本とする。要点のみ再掲する:

- CronCreate / 自己完結 prompt（#178 実証）は状態・停止・再開契約が prompt ごとに
  重複するため恒久機能にしない。
- 旧 EpicConfig / draft ADR-004 の DAG・並列・merge queue は #291 の直列ユースケース
  に不要なため先行実装しない（`draft/lab/adr-004-epic-orchestration.md` は Draft の
  まま将来の拡張検討資料として残す）。
- 既存 single-issue runner の multi-issue 拡張は、Issue 単位の `SessionState` と
  series 全体の状態を混在させるため採用せず、**上位 runner として分離**する
  （ADR-004 Alternatives A と同じ判断）。

## インターフェース

### 1. series YAML（入力契約）

配置先は `.kaji/series/<id>.yaml`（軽微な設定ファイルとして main 直コミット許容）。

```yaml
id: cli-refactor-series      # 必須: series 識別子
parent_issue: 291            # 任意: EPIC 親 Issue（トレーサビリティ専用）
strategy: sequential         # 必須: 初期実装は "sequential" のみ受理
members:                     # 必須: 1 件以上の順序付きリスト
  - issue: 282               # 必須: GitHub Issue 番号（正の整数）
    workflow: .kaji/wf/dev.yaml   # 必須: repo_root 相対の workflow path
  - issue: 283
    workflow: .kaji/wf/dev-thorough-fable.yaml
on_failure: stop             # 必須: 初期実装は "stop" のみ受理
```

#### field 仕様

| field | 型 | 必須 | 制約 |
|-------|-----|------|------|
| `id` | str | ✅ | `^[a-z0-9][a-z0-9-]{0,63}$`。artifact / lock / resume の安定識別子であり、ディレクトリ名にそのまま使うため filesystem-safe に限定 |
| `parent_issue` | int | — | 正の整数。指定しても sub-issues の自動取得・関係検証は行わない。実行意味論に影響しない |
| `strategy` | str | ✅ | literal `"sequential"` のみ。他値は validation error |
| `members` | list | ✅ | 1 件以上。記載順 = 実行順（並べ替えない） |
| `members[].issue` | int | ✅ | 正の整数。series 内で重複禁止 |
| `members[].workflow` | str | ✅ | repo_root 相対 path。実在し `load_workflow()` が成功すること。`requires_provider` が `github` / `any` であること |
| `on_failure` | str | ✅ | literal `"stop"` のみ |

- **未知 key は error**（typo の silent 破棄を防ぐ fail-loud。`description` 等の
  実行に不要な field は schema 上存在しない — Issue 本文「series YAML の入出力契約」）。
- parse は `yaml.safe_load` → **Pydantic v2 model による検証**（AGENTS.md
  「外部入力は Pydantic で検証する」に準拠。§ 制約 参照）。`model_config =
  ConfigDict(extra="forbid")` で未知 key を拒否し、`ValidationError` が全 field
  エラーを集約する（エラー列挙の手書き実装をしない）。Pydantic model
  （`SeriesConfig` / `SeriesMember`）が schema の**単一の正本**であり、
  `validate-series` / `run-series` / generator のすべてが同一 model を通る。
  workflow path の実在・`load_workflow` 成功・`requires_provider` 互換は
  filesystem / config 依存のため model 外の loader 層で検証する（Pydantic は
  値の構造検証に限定）。

### 2. CLI

#### `kaji validate-series <series.yaml>...`

副作用なしで series YAML を検証する。検出対象: 空 members / 不正 id / Issue 重複 /
不正 Issue 番号 / 存在しない・load 不能な workflow / provider 非互換 workflow /
未対応 strategy・failure policy / 未知 key。出力形式は既存 `kaji validate` に揃える
（stdout `✓`、stderr `✗` + error 列挙）。exit code: 全件 OK → `EXIT_OK(0)`、
エラーあり → `EXIT_VALIDATION_ERROR(1)`。GitHub API へはアクセスしない
（Issue の実在確認は行わない。member 実行時の `kaji run` が解決する）。

#### `kaji run-series <series.yaml> [--dry-run] [--resume] [--workdir <dir>] [--quiet]`

| option | 説明 |
|--------|------|
| `--dry-run` | validation 結果 + 実行順 + 確定済み workflow + fingerprint を表示して終了。member 起動・state 作成/更新・lock 取得・provider アクセスを一切行わない |
| `--resume` | 既存 state から再開。fingerprint 照合 + 完了済み member の GitHub 再検証を行い、最初の未完了 member から続行 |
| `--workdir` | config discovery の起点（`kaji run` と同じ契約、default: cwd） |
| `--quiet` | member `kaji run` へ `--quiet` を伝播（agent 出力 streaming 抑制） |

**exit code 契約**（`commands/exit_codes.py` を再利用）:

| code | 条件 |
|------|------|
| `EXIT_OK(0)` | 全 member 完了（dry-run 成功もここ） |
| `EXIT_ABORT(1)` | member 失敗による停止: `kaji run` 非 0 / 成功ゲート不一致（`not_planned` close・open のまま等）/ 起動対象 member（fresh / resume 共通）が起動前に既に closed という外部不整合 / resume 再検証での完了済み member 巻き戻り検出 |
| `EXIT_INVALID_INPUT(2)` | series YAML validation error / config 不在 / provider が `github` でない / fingerprint 不一致 / lock 取得失敗（`EACCES`・`EAGAIN` = 二重起動）/ state 既存なのに `--resume` なし / `--resume` なのに state 不在 / resume 時に中断遺留 member の child が生存（二重起動防止の保守的拒否） |
| `EXIT_RUNTIME_ERROR(3)` | 予期しない harness error（state 書き込み失敗、lock の `EACCES`・`EAGAIN` 以外の `OSError` 等） |

#### member 成功ゲート

member を「完了」と判定する条件は **両方** の成立:

1. member の `kaji run <workflow> <issue>` subprocess が exit 0 で終了している
   （`kaji run` は failure triage の child run を含む chain 最終結果を exit code に
   反映済みのため、series 側は exit code のみを見る）
2. 直後に provider へ問い合わせた Issue が `state == "closed"` かつ
   `state_reason == "completed"` である

一方のみ成立（run 成功なのに open のまま / `not_planned`・`duplicate` close 等）は
不整合として `stop_reason` に記録し `EXIT_ABORT` で停止する。後続 member は
起動しない。

#### 実行時の副作用（出力契約）

- **member 実行**: subprocess として `kaji run <workflow> <issue> [--quiet]` を
  cwd=repo_root で起動（`RecoveryHandler._default_child_launcher` と同型）。
  stdout/stderr は親へそのまま流す。
- **state 永続化**: `resolve_artifacts_dir(config)/_series/<id>/state.json`
  （`fsio.atomic_write` による tmp → rename。series YAML とは分離）。
- **lock**: 同ディレクトリの `lock` ファイルを**書き込みモードで open** し
  `fcntl.flock(LOCK_EX | LOCK_NB)`。`OSError` のうち `errno` が `EACCES` /
  `EAGAIN` の場合のみ「同一 series id の同時実行」として `EXIT_INVALID_INPUT`
  で拒否し、それ以外の `OSError` は `EXIT_RUNTIME_ERROR` として区別する
  （誤診断防止）。advisory lock は fd close（= プロセス終了を含む）で解放される
  （`flock(2)`）ため stale lock 掃除は不要。ただし lock が守るのは **series
  プロセス間の排他のみ**であり、親クラッシュ後に生存する child `kaji run` とは
  無関係（child の扱いは § resume 意味論）。
- **signal / 終了処理**: series runner が `SIGINT` / `SIGTERM` を受けた場合、
  実行中 child に `SIGTERM` を転送して終了を待ち、member を `interrupted` として
  state に記録した上で非 0 終了する（lock は fd close で自動解放）。
- **コンソール進捗**: member ごとに開始 / exit code / ゲート判定 / 停止理由を
  1 行ずつ stdout に出す。

#### state.json スキーマ

```json
{
  "series_id": "cli-refactor-series",
  "fingerprint": "sha256:<hex>",
  "status": "running | stopped | completed",
  "stop_reason": null,
  "updated_at": "<ISO8601>",
  "members": [
    {
      "issue": 282,
      "workflow": ".kaji/wf/dev.yaml",
      "status": "pending | running | interrupted | completed | failed",
      "child_pid": null,
      "run_id": null,
      "exit_code": null,
      "gate": null,
      "started_at": null,
      "finished_at": null
    }
  ]
}
```

**member field の初期値と遷移ごとの更新規則**（nullable 契約の明示）:

| field | 初期値（`pending`） | 更新タイミング |
|-------|--------------------|----------------|
| `status` | `"pending"` | 起動直前に `"running"`、終了時に `"completed"` / `"failed"`、中断検出時に `"interrupted"` |
| `child_pid` | `null` | child `Popen` 直後、**wait でブロックする前に** `"running"` とともに永続化（crash 時の liveness 判定材料）。member 終了時も監査用に保持 |
| `run_id` | `null` | child 終了後、`<artifacts>/<issue>/runs/` から `started_at` 以降に作られた最新 run dir を best-effort 探索して記録（`recovery/snapshot.py` の `find_child_run_id` / `list_newer_run_ids` と同じ手法）。発見不能なら `null` のまま（監査情報であり実行判断には使わない） |
| `exit_code` | `null` | child 終了時 |
| `gate` | `null` | ゲート判定時。成功は `"closed_completed"`、不一致は `"mismatch:<state>/<state_reason>"`（例: `"mismatch:closed/not_planned"` / `"mismatch:closed/duplicate"` / `"mismatch:open/"`） |
| `started_at` / `finished_at` | `null` | 起動時 / 終了時（`interrupted` 検出時は検出時刻を `finished_at` に記録） |

- `fingerprint` = 正規化した series 設定（`id` / `strategy` / `on_failure` /
  `parent_issue` / 順序付き `members` の `issue`+`workflow`）の canonical JSON
  （sorted keys）に対する SHA-256。コメントや YAML 表記揺れは影響しない。
- Issue 完了条件の「実行した workflow、run 識別情報、停止理由」は
  `members[].workflow` / `members[].run_id`（+ `child_pid` / `exit_code`）/
  `stop_reason` が担う。

#### resume 意味論

| 状況 | 挙動 |
|------|------|
| `--resume` + fingerprint 一致 | `completed` member を GitHub で再検証（closed/completed のままか）。全一致なら最初の未完了 member（`pending` / `failed` / `interrupted` / `running`）へ。`running` member は下記「中断遺留 `running` member の reconciliation」を先に通す |
| `--resume` + fingerprint 不一致 | 変更後の順序・workflow を暗黙適用せず `EXIT_INVALID_INPUT` で拒否（新 id での作り直し、または state 削除の明示操作を案内） |
| `--resume` + 完了済み member が closed/completed でなくなっている | 巻き戻り不整合として `EXIT_ABORT` で停止（暗黙再実行しない） |
| `--resume` なし + state 既存 | `EXIT_INVALID_INPUT` で拒否し `--resume` を案内（誤った初回起動による状態破壊を防ぐ） |
| 起動対象 member の Issue が起動前に既に closed（fresh run の未開始 member / resume の再実行対象 member とも） | 外部不整合として `EXIT_ABORT` で停止（series 設定の誤り、または外部 close の可能性。member 除去または close 理由の解消を案内）。resume では reconciliation で `completed` 昇格・skip 済みの member を除いた「これから起動する member」に対して判定するため、closed Issue に対する `kaji run` の空振り起動（worktree / PR の重複生成）を防ぐ |

#### 中断遺留 `running` member の reconciliation（crash-safe resume）

series lock は親プロセス終了で解放されるため、`running` のまま残った member を
盲目的に再実行すると、(a) 親だけが死んで child `kaji run` が生存しているケースで
同一 Issue workflow の二重起動、(b) child がゲート成立（Issue close）直後・state
書き込み前に親が死んだケースで完了済み member の再実行、が起こり得る。resume は
`running` member に対し次の順で判定してから進む:

1. **child 生存確認**: state の `child_pid` に `os.kill(pid, 0)` で疎通確認。
   - 生存（または `EPERM` = 判定不能を保守的に生存扱い）→ member run が進行中
     として `EXIT_INVALID_INPUT` で resume を拒否（二重起動防止。child の完了を
     待ってから再度 `--resume`）
   - `ESRCH`（不在）→ 2 へ
2. **GitHub との reconciliation**: `view_issue` で成功ゲートを判定。
   - `closed` + `completed` → child は完了していたと確定。member を
     `completed`（`gate="closed_completed"`）へ更新し、**再実行せず**次へ進む
     （crash window (b) の救済）
   - それ以外 → member を `interrupted` に更新した上で **再実行する**
     （完了条件「最初の未完了 member から再開」の通常経路）

既知の制約: PID reuse により死んだ child の pid が別プロセスに再利用されている
場合、1 が偽陽性（生存扱い）になり resume が保守側（拒否）に倒れる。二重起動側
には倒れないため安全性は維持される。この保守的拒否は対象プロセス終了後の再試行で
自然解消するため、v1 では追加の識別（プロセス開始時刻比較等）を行わない。

### 3. `Issue` model の拡張（close reason）

成功ゲートに close reason が必要だが、現行 `providers/models.py` の `Issue` は
`state` のみで close reason を持たない。以下を追加する:

- `Issue.state_reason: str = ""` を末尾に追加（frozen dataclass の default 付き
  field 追加。既存 callsite は無変更で互換）。
- `GitHubProvider.view_issue` の `--json` field 列に `stateReason` を追加し、
  parse 境界で **小文字に正規化**（`"COMPLETED"` → `"completed"`。`state` と同じ
  正規化方針）。値域は現行 GitHub REST / GraphQL に従い `completed` /
  `not_planned` / `duplicate` / `reopened` / `""`（null = open 等）。成功ゲートを
  通すのは `completed` のみで、`duplicate` close は `not_planned` と同様に
  `"mismatch:closed/duplicate"` として停止する。将来 GitHub が値域を拡張しても
  「`completed` 以外はすべて不一致」の判定は安全側に保たれる（allowlist 方式）。
- `LocalProvider` は `""` のまま（初期実装は GitHub provider のみが対象）。

### 4. `series-create` skill

配置: `.claude/skills/series-create/SKILL.md`。利用例:

```text
/series-create 310 311 --id maintenance-2026-07
/series-create 282 283 285 --id cli-refactor-series --parent 291 \
  --workflow 283=.kaji/wf/dev-thorough-fable.yaml
```

（1 例目は全 member 自動選択。2 例目は #283 のみ variant を明示 override し、
残りは自動選択 — #291 の確定 workflow と一致する）

**責務（Issue #313「`series-create` skill の責務」1〜9 に 1:1 対応）**:

1. Issue 番号列（順序保持）、`--id`、任意 `--parent`、member 単位 `--workflow`
   override を収集する
2. `kaji issue view <n> --json labels,title,state` と `.kaji/wf/*.yaml` の
   `description` を read-only で参照し、workflow を自動選択して根拠を表示する
3. 一意に決められない場合は生成を進めず、明示 override を要求して停止する
4. 決定的 generator（下記）で `.kaji/series/<id>.yaml` を生成する（順序不変）
5. 既存ファイルは `--update` 明示がない限り上書きしない（generator 側で強制）
6. `kaji validate-series` を実行し、失敗時は完了扱いにしない
7. `kaji run-series <path> --dry-run` で実行順と確定済み workflow を提示する
8. dry-run 後に停止し、本実行は開始しない
9. Issue 本文・ラベル・sub-issue 関係など外部状態を変更しない

**workflow 自動選択規則**（SKILL.md に記載する判断基準）:

判定の情報源は「Issue metadata（type ラベル・state）」と「`.kaji/wf/*.yaml` の
`description`」であり、skill 内に固定の type → workflow 対応表をハードコード
しない（Issue #313 要件「workflow 定義の `description` を基に自動選択」に従う。
workflow の追加・改廃に skill の改修なしで追随させるため）。手順:

1. `.kaji/wf/*.yaml` を列挙し、`description` の記述から各 workflow の
   (a) 対象 type（feat / bug / docs 等）、(b) series 自動選択の対象か
   明示 override 専用か、(c) `requires_provider`、を読み取る
   （§ 影響ドキュメント の通り、`description` をこの 3 点が判別できる内容へ
   更新することが本設計の一部。例: 標準 `dev.yaml` / `docs.yaml` は
   「series 自動選択の標準対象」、`dev-thorough*` は「品質優先が明示された
   場合の override 専用 variant」、`dev-local` / `docs-local` / `incident` は
   「series 自動選択対象外」と判別できる記述にする）
2. member Issue の type ラベル（1 つ）と provider（github）に適合し、かつ
   「自動選択対象」と読み取れる workflow を候補集合として絞り込む
3. 候補がちょうど 1 件 → 自動選択し、根拠（type ラベルと description の
   該当記述）を表示する
4. 候補が 0 件または 2 件以上、type ラベル未付与・複数付与 → 曖昧として
   生成を進めず、明示 override を要求して停止する
5. `--workflow <issue>=<path>` override が指定された member は 1〜4 を
   バイパスし、指定 path をそのまま採用する（loader 検証は generator /
   `validate-series` 側で必ず通る）

thorough / fable 等の variant を使うかは「Issue metadata から一意に決められない
運用上の選好」であるため、自動選択せず override 専用とする。#291 相当の series
で #283 に `dev-thorough-fable.yaml` を使うケースは、下記使用例の通り member
override の代表例として扱う（Issue 本文の EPIC あり YAML 例と実行計画が一致する）。

**決定的 generator**: `python -m kaji_harness.scripts.series_generate`
（`kaji_harness/scripts/` の既存 script 配置に倣う）。

```text
python -m kaji_harness.scripts.series_generate \
  --id cli-refactor-series [--parent 291] \
  --member 282=.kaji/wf/dev.yaml --member 283=.kaji/wf/dev-thorough-fable.yaml \
  [--output .kaji/series/cli-refactor-series.yaml] [--update]
```

- YAML serialization / field 正規化 / 上書き制御（`--update` なしで既存ファイル →
  exit 非 0）を Python 側で決定的に行う。LLM の自由記述で YAML を生成しない。
- 生成前に `kaji_harness/series` の loader と同一の検証 model を通す
  （schema の正本は 1 箇所。skill 側に validation を重複実装しない）。
- 生成物には確定済み `workflow` path のみを保存し、選択理由等の余剰 field を
  追加しない。

### 5. 使用例（E2E）

```bash
# 1) series 準備（skill 経由。生成 + validate + dry-run で停止）
#    #283 のみ thorough variant を明示 override（#291 の確定 workflow と一致）
/series-create 282 283 285 286 284 --id cli-refactor-series --parent 291 \
  --workflow 283=.kaji/wf/dev-thorough-fable.yaml

# 2) 実行計画の最終確認（副作用なし）
kaji run-series .kaji/series/cli-refactor-series.yaml --dry-run

# 3) 本実行（前段 closed/completed 確認後に次 member を起動）
kaji run-series .kaji/series/cli-refactor-series.yaml

# 4) 途中停止（例: #285 で ABORT）後、原因解消してから再開
kaji run-series .kaji/series/cli-refactor-series.yaml --resume
```

## 制約・前提条件

- **provider**: 初期実装は `provider.type == "github"` のみ。`run-series` 起動時に
  `actual_provider_type(config)` を突合し、不一致は `EXIT_INVALID_INPUT` で
  fail-fast（`kaji run` の provider 整合ガードと同じ流儀）。
- **strategy / on_failure**: `sequential` / `stop` のみ。DAG・並列・merge queue・
  budget・通知・member 自動発見は non-goal（Issue #313 スコープ境界）。
- **既存契約の不変**: `kaji run`、builtin workflow、`IssueProvider` protocol の
  既存メソッド契約、`SessionState` を変更しない。`Issue.state_reason` は default
  付き追加のみ（後方互換）。
- **member 実行は subprocess**: in-process で `WorkflowRunner` を直接呼ばず、
  `kaji run` CLI を子プロセス起動する。根拠: (a) exit code が既に「failure triage
  chain を含む最終結果」を表現する公開契約である（`commands/run.py`）、(b) 単一
  Issue の状態と series 状態の分離という採用理由と一致する、(c) `RecoveryHandler`
  の child run 起動と同型で precedent がある。
- **artifacts 配置**: `resolve_artifacts_dir(config)` を経由し main worktree 基準で
  `_series/<id>/` を解決する（`kaji run` / `kaji config artifacts-dir` と同一の
  解決規則。feature worktree 消滅でログが消えない）。
- **検証方式**: series YAML は新規の外部入力契約であるため、AGENTS.md
  常時適用ルール「外部入力は Pydantic で検証する」に従い **Pydantic v2** で
  検証する。`pyproject.toml` に `pydantic>=2` を依存追加する（新規ライブラリ
  採用のため ADR を追加 — § 影響ドキュメント）。既存 `workflow.py` の dataclass
  手書き検証は既存契約として本 Issue では変更しない（規約と既存実装の乖離解消は
  別 Issue スコープ）。Pydantic の適用範囲は series YAML の構造検証
  （`SeriesConfig` / `SeriesMember`）と state.json の load 検証に限定し、
  filesystem / config 依存の検証（workflow 実在・provider 互換）は loader 層に
  置く。
- **排他は POSIX advisory lock**: `fcntl.flock` は Linux / WSL2（本プロジェクトの
  実行環境）で動作し、プロセス終了時に自動解放される。Windows native は非対象。
- **module 境界（ADR 009）**: 新規 `kaji_harness/series/` package は application
  層（`runner.py` 相当）。foundation（`fsio` / `config` / `models`）と provider 層
  にのみ依存し、`commands/` からのみ import される。package 跨ぎの private import
  をしない。`tests/test_layer_imports.py` の module 分類完全性テストに新 module の
  分類追加が必要。
- **時刻・乱数**: state のタイムスタンプは `datetime.now(UTC).isoformat()`
  （`state.py` の既存流儀）。

## 変更スコープ

| 区分 | パス | 変更内容 |
|------|------|----------|
| 新規 package | `kaji_harness/series/`（`models.py` / `loader.py` / `state.py` / `lock.py` / `runner.py` / `generator.py`） | series schema・検証・状態永続化・排他・直列実行・決定的生成 |
| 新規 command | `kaji_harness/commands/series.py` | `cmd_validate_series` / `cmd_run_series` |
| 変更 | `kaji_harness/commands/parser.py` | `validate-series` / `run-series` subcommand 登録 |
| 変更 | `kaji_harness/commands/main.py` | `args.command == "validate-series" / "run-series"` の明示 dispatch 追加（現行 `main()` は if 分岐で handler へ到達させるため、parser 登録だけでは help + `EXIT_ABORT` になる） |
| 変更 | `pyproject.toml` | `pydantic>=2` を dependencies へ追加 |
| 新規 | `docs/adr/010-*.md`（番号は採番時確定） | 新規依存 pydantic 採用の記録（AGENTS.md 規約準拠の初適用・適用範囲・既存 dataclass 検証との共存方針） |
| 変更 | `kaji_harness/providers/models.py` / `providers/github.py` | `Issue.state_reason` 追加、`view_issue` の `stateReason` 取得 + 小文字正規化 |
| 変更 | `kaji_harness/errors.py` | `SeriesValidationError` 等（`HarnessError` 階層） |
| 新規 script | `kaji_harness/scripts/series_generate.py` | generator の argv エントリ |
| 新規 skill | `.claude/skills/series-create/SKILL.md` | § インターフェース 4 の責務定義 |
| 変更 | `.kaji/wf/*.yaml` の `description` | 自動選択の判断材料整備（§ 影響ドキュメント） |
| 新規 tests | `tests/test_series_*.py` + `tests/fixtures/series/` | § テスト戦略 |
| 変更 tests | `tests/test_layer_imports.py`（分類追加）、skill 構造検証（既存 `test_skill_*` の流儀に倣う） | fitness 維持 |

## 方針（Minimal How）

### run-series のデータフロー

```text
config discover → provider=github 確認
→ load_series（validate-series と同一 loader = Pydantic model + loader 層検証）
→ [--dry-run] 計画表示のみで終了
→ _series/<id>/ 確保 → flock 取得（EACCES/EAGAIN = 二重起動拒否）
→ state 初期化 or --resume 検証
   （fingerprint 照合 → 完了済み member の GitHub 再検証
     → running member の reconciliation: child_pid 生存 → 拒否 /
       死亡 + closed/completed → completed 昇格 / 死亡 + 未完了 → interrupted）
→ for member in 未完了 members（記載順）:
     pre-check（fresh / resume 共通）: Issue が既に closed → 不整合停止
     Popen: kaji run <workflow> <issue> [--quiet]（cwd=repo_root）
     → status=running + child_pid を wait 前に永続化（atomic write）
     → wait → exit_code / run_id（runs/ 探索 best-effort）記録
     exit != 0 → member failed / stop_reason 記録 → EXIT_ABORT
     view_issue → closed/completed でない → gate mismatch 記録 → EXIT_ABORT
     member completed 記録（atomic write）
→ 全件完了 → status=completed → EXIT_OK
（SIGINT/SIGTERM 受信時: child へ SIGTERM 転送 → interrupted 記録 → 非 0 終了）
```

### 新規シンボルと責務

| シンボル | 責務 |
|----------|------|
| `SeriesConfig` / `SeriesMember` | series YAML schema の正本となる Pydantic v2 model（`extra="forbid"`、field 制約、`ValidationError` によるエラー集約） |
| `load_series(path, config) -> SeriesConfig` | `yaml.safe_load` → Pydantic 検証 + loader 層検証（workflow 実在・load 可否・provider 互換）。エラーは集約して `SeriesValidationError`（`HarnessError` 階層）に包む |
| `series_fingerprint(config) -> str` | 正規化 canonical JSON の SHA-256 |
| `SeriesState` / `MemberState` | state.json の load / atomic save / 遷移記録（`run_id` / `child_pid` を含む） |
| `SeriesLock` | flock の context manager（EACCES/EAGAIN と他 OSError を区別） |
| `SeriesRunner` | 直列実行・成功ゲート・crash-safe resume（reconciliation）。`member_launcher`（subprocess 起動）・`provider`・`pid_alive`（liveness 判定）をコンストラクタ注入（テスト差し替え点。`RecoveryHandler` の `child_launcher` と同型） |
| `generate_series_yaml(...)` | 正規化 + 決定的 YAML serialization + 上書き制御（`SeriesConfig` model を共有） |

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ

実行時コード変更（新規 CLI・状態永続化・外部プロセス起動・provider 拡張）。

### Small テスト

外部依存なしの純粋ロジック:

- schema 検証マトリクス（Pydantic model を直接駆動）: 空 members / id pattern
  違反 / Issue 重複 / 非整数・0 以下の Issue 番号 / 未対応 strategy・on_failure /
  未知 key（`extra="forbid"`）/ parent_issue 有無で受理が変わらないこと /
  複数エラーが `ValidationError` に集約されること
- `series_fingerprint`: 決定性（同一入力 → 同一 hash）、YAML 表記揺れ非依存、
  順序・workflow 変更で hash が変わること
- 成功ゲート判定関数: `exit_code × state × state_reason` の判定表。
  `0×closed×completed` のみ成功とし、`not_planned` / **`duplicate`** /
  `reopened` / open / 非 0 exit / 未知の将来値、の各停止理由（allowlist 方式の
  検証を含む）
- `stateReason` の小文字正規化（`duplicate` 含む）と `Issue.state_reason`
  default 互換
- generator の正規化ロジック（member 順序保持、余剰 field 非出力）

### Medium テスト

ファイル I/O・プロセス内結合（tmp_path / 注入 fake）:

- loader: 実ファイルからの load、workflow 不在・load 不能・`requires_provider:
  local` workflow の検出
- state: save/load round-trip、atomic write（中途 tmp が残らない）
- lock: 同一 lock ファイルへの二重 flock が非ブロックで失敗すること
- `SeriesRunner`（fake launcher + fake provider 注入）:
  - 記載順で 1 件ずつ起動し、前段ゲート成立まで次を起動しない
  - member 非 0 / gate mismatch / 起動前に既 closed の member（fresh run の未開始
    member、resume の再実行対象 member の双方）で停止し
    後続 launcher が呼ばれない
  - `--dry-run` で launcher 不呼出し・state ディレクトリ非作成・lock 非取得
  - resume: 完了済み skip / fingerprint 不一致拒否 / 完了済み巻き戻り停止 /
    state 既存 + `--resume` なし拒否
  - **crash window の reconciliation**（`pid_alive` / `provider` を fake 注入し
    window ごとに検証）:
    (a) `running` + child 生存 → resume 拒否・launcher 不呼出し（二重起動防止）、
    (b) `running` + child 死亡 + Issue closed/completed → `completed` 昇格・
    再実行なしで次 member へ（gate 成立直後の親クラッシュ救済）、
    (c) `running` + child 死亡 + Issue 未完了 → `interrupted` 経由で再実行、
    (d) `status=running` + `child_pid` が wait ブロック**前に**永続化されている
    こと（クラッシュ時に liveness 判定材料が残る契約）、
    (e) `EPERM` を保守的に生存扱いすること
  - gate mismatch 表現: `mismatch:closed/not_planned` / `mismatch:closed/duplicate`
    / `mismatch:open/` が `gate` と `stop_reason` に記録されること
- `cmd_validate_series` / `cmd_run_series`: exit code 契約（0/1/2/3 の代表経路）
- parser → `main()` dispatch の統合: `main(["validate-series", ...])` /
  `main(["run-series", ...])` が help + `EXIT_ABORT` に落ちず各 handler に到達
  すること（既存 `test_cli_args` / `test_cli_main` の流儀）
  - EPIC あり（#291 相当: parent_issue=291, members 282→283→285→286→284、
    283 のみ `dev-thorough-fable.yaml` override）と EPIC なしの fixture
    （`tests/fixtures/series/`）で実行意味論が同一であること
    （受入シナリオ: 直列実行・途中停止・再開の 3 本を両 fixture で駆動）
- generator: 既存ファイル上書き拒否 / `--update` 許可 / 生成物が `load_series`
  を無修正で通ること（schema drift 防止の bridging test）
- 既存回帰: `view_issue` の `--json` field 追加が既存 `test_providers_github` の
  期待と互換であること + `stateReason` の `completed` / `not_planned` /
  `duplicate` / `reopened` / null 各値の parse 回帰
- skill 構造: `.claude/skills/series-create/SKILL.md` の frontmatter・必須節
  （trigger / 入力 / 出力 / 停止条件 / non-goals）を既存 `test_skill_*` の流儀で
  検証。決定的部分（generator の順序保持・上書き制御・EPIC あり / なし出力）は
  generator + loader の Medium テストで機械検証し、LLM 判断部分は次の forward
  test（dry-run までの手動実行記録を Issue コメントに残す）で確認する:
  (1) `type:feature` member → 標準 `dev.yaml` を自動選択し根拠を表示、
  (2) `type:docs` member → 標準 `docs.yaml` を自動選択、
  (3) type ラベル未付与 member → 曖昧として停止し override を要求、
  (4) `--workflow 283=.kaji/wf/dev-thorough-fable.yaml` override → 指定 path を
  採用（#291 例の再現）、
  (5) 既存 `.kaji/series/<id>.yaml` あり + `--update` なし → 生成せず停止

### Large テスト

- **large_local（追加する）**: 実 CLI subprocess で
  `kaji validate-series <fixture>` と `kaji run-series <fixture> --dry-run` を駆動
  し、ネットワークなしで exit code / 表示 / 副作用なしを検証（両コマンドとも
  provider アクセスを行わない設計のため network 不要）。
- **large_forge（恒久テストとしては追加しない）**: 成功ゲートの完全な E2E は
  「実 GitHub Issue を workflow が close する」ことが前提で、外部の可変状態を
  破壊的に消費し CI で決定的に再現できない。代替検証: (a) gate 判定は Small の
  判定表 + Medium の fake provider で網羅、(b) `kaji run` 側の実 GitHub 疎通は
  既存 `large_forge` テスト群が保証済み、(c) 本物の受入は #291 系列での
  ドッグフーディング実行記録を Issue コメントに残す。この省略判断は
  `docs/dev/testing-convention.md` の「物理的に作成不可」（破壊的外部状態）に
  該当し、サイズごとの検証観点自体は上記 S/M/large_local で定義済み。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | あり | 新規依存 `pydantic>=2` の採用記録（AGENTS.md「外部入力は Pydantic で検証する」の初適用・適用範囲・既存 dataclass 検証との共存方針）。series オーケストレーション自体の DAG 拡張 ADR は将来スコープ（`draft/lab/adr-004` は Draft のまま） |
| `docs/ARCHITECTURE.md` | あり | package tree（`kaji_harness/` 配下）・層図（application 層に `series/` 追加）・artifacts layout（`_series/<id>/state.json` / `lock`）の追記が必要 |
| `docs/dev/workflow_guide.md` | あり | `run-series` / `validate-series` / `series-create` の使い方と位置づけ追記 |
| `docs/dev/testing-convention.md` | なし | 既存規約の適用のみ |
| `docs/reference/configuration.md` / `.ja.md` | あり | `artifacts_dir` 配下 `_series/<id>/` の state / lock 配置を追記 |
| `docs/cli-guides/github-mode.md` / `.ja.md` | あり | GitHub provider 前提の series 実行手順を追記 |
| `README.md` / `README.ja.md` | あり | CLI コマンド一覧に `run-series` / `validate-series` を追加 |
| `.kaji/wf/*.yaml` `description` | あり | 自動選択の判断材料整備: dev / docs の標準 vs thorough / fable variant の使い分け、`dev-local` / `docs-local` / `incident` が series 自動選択対象外であることを判別可能にする |
| `AGENTS.md` / `CLAUDE.md` | なし | 規約変更なし（skill 一覧表は CLAUDE.md にあるが、`series-create` はライフサイクル表の対象外の独立 skill。必要なら final-check で再評価） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| gh CLI `gh issue view` manual | https://cli.github.com/manual/gh_issue_view | `--json` で取得可能な field 一覧に `stateReason` が含まれる（`state` と別 field）。成功ゲートの close reason 取得手段の裏付け |
| GitHub REST API — Issues | https://docs.github.com/en/rest/issues/issues | Issue の `state_reason` は `completed` / `not_planned` / `duplicate` / `reopened` / `null` を取る（GraphQL `IssueStateReason` も同値域）。ゲート判定の値域と `not_planned` / `duplicate` 停止の根拠 |
| GitHub GraphQL reference — Issues | https://docs.github.com/en/graphql/reference/issues | `IssueStateReason` enum に `DUPLICATE` が含まれる。`gh issue view --json stateReason` は GraphQL 由来のため大文字値を返し得る → parse 境界での小文字正規化の根拠 |
| Python `fcntl` 公式 | https://docs.python.org/3/library/fcntl.html | `flock(fd, LOCK_EX \| LOCK_NB)` の非ブロック排他と、取得失敗時 `OSError`（`EACCES` / `EAGAIN`）の根拠 |
| `flock(2)` man page | https://man7.org/linux/man-pages/man2/flock.2.html | 「the lock is released ... when all of the file descriptors referring to the open file description have been closed」— プロセス終了で lock が自動解放され stale lock 掃除が不要になる根拠 |
| Pydantic v2 公式 | https://docs.pydantic.dev/latest/ | `ConfigDict(extra="forbid")` による未知 field 拒否、`ValidationError` が全 field のエラーを集約して報告する挙動。series YAML 検証方式の根拠 |
| PyYAML `safe_load` | https://pyyaml.org/wiki/PyYAMLDocumentation | `safe_load` は任意オブジェクト構築を許さず単純な Python object のみ生成する。Pydantic 検証に渡す前段の parse 手段の根拠 |
| `AGENTS.md` | repo 内 `AGENTS.md`（Always-Apply Rules） | 「外部入力は Pydantic で検証する」— series YAML（新規外部入力契約）へ Pydantic を採用する規約上の根拠 |
| `kaji_harness/recovery/handler.py` | repo 内 `kaji_harness/recovery/handler.py`（`_default_child_launcher` / `child_launcher` 注入） | 「`kaji run` を subprocess 起動し exit code を消費する + launcher をテスト注入する」既存 precedent。member 実行方式の根拠 |
| `kaji_harness/commands/run.py` | repo 内 `kaji_harness/commands/run.py` | `kaji run` の exit code は failure triage chain の最終結果を反映する（`return child_exit_code if ...`）。series が exit code のみを成功入力にできる根拠 |
| `kaji_harness/artifacts.py` | repo 内 `kaji_harness/artifacts.py` | `resolve_artifacts_dir` が main worktree 基準で artifacts を解決する。`_series/<id>/` 配置規則の根拠 |
| `kaji_harness/fsio.py` | repo 内 `kaji_harness/fsio.py` | `atomic_write`（tmp → `os.replace`）が foundation 層に存在。state.json 破損対策の根拠 |
| `kaji_harness/workflow.py` / `pyproject.toml` | repo 内 | 既存 workflow loader は `yaml.safe_load` + dataclass 検証（本 Issue では不変）。現行依存は `pyyaml` / `jq` のみ → `pydantic>=2` が**新規**依存追加であり ADR 記録を要する根拠 |
| ADR 009 | `docs/adr/009-module-boundary-private-import.md` | package 跨ぎ private import 禁止・層方向規約。`series/` package の依存方向と `test_layer_imports.py` 追随の根拠 |
| draft ADR-004 / RFC 検討資料 | `draft/lab/adr-004-epic-orchestration.md` / `draft/lab/epic-orchestration.md` | 旧 EPIC 構想（DAG / 並列 / merge queue / post-merge 状態）。本設計はその縮小直列版であり、Alternatives A「single-issue runner 拡張の却下」判断を継承 |
| Issue #178 / #291 | GitHub Issues（`kaji issue view 178` / `291`） | 連続実行の実証（#178）と EPIC あり代表受入事例（#291: 282→283→285→286→284 の直列運用実績） |
