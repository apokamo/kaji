# [設計] resolve-verdict の marker 不在時 local artifact fallback

Issue: #426

## 概要

`kaji issue resolve-verdict` は Issue コメントの verdict marker しか走査しない。そのため、producer step が
harness 管理の `verdict.yaml` に判定を保存済みでも、marker が欠落していると exit 4 で停止する。
本修正では、**marker が存在しない場合に限り**、特定できた run（と記録済み復旧元 run）の最新 attempt の
`verdict.yaml` から判定を解決する。marker がある場合の出力・終了コードは一切変えない。

## 背景・目的

### Observed Behavior (OB)

- downstream の #122 / #50 / #93 で、producer の `verdict.yaml` は `status: PASS` だった。しかし Issue コメント先頭の
  marker が欠落しており、consumer (`final-check`) の `kaji issue resolve-verdict <id> --step <producer>` が
  `Error: no verdict marker found for step 'implement-precheck'` / **exit 4** で停止した。
  いずれも作業は完了済みで全 gate green だったが、復旧には人間による marker 復元コメントの手投稿が必要だった
  （Issue 本文「実害」、[#122 復元コメント](https://github.com/apokamo/fullstack-agent-template/issues/122#issuecomment-5527400830)）。
- 現行コード: `kaji_harness/commands/issue.py` `resolve_latest_verdict()` は `comments` だけを逆順走査し、
  見つからなければ `VerdictMarkerNotFoundError` を送出する。`_handle_issue_resolve_verdict()` はこれを
  `EXIT_VERDICT_NOT_FOUND = 4` に写像する。attempt / artifact への参照はない。

### Expected Behavior (EB)

Issue 本文「決定事項」（grill-me でユーザーが承認した方針）に従い、次のように振る舞う。

- marker があれば従来どおり解決する（出力・エラーは不変）
- marker が無く、対象 run を明示指定または実行コンテキストから特定できる場合は、その run（run 内で対象 step が
  一度も実行されていなければ、記録済み復旧元 run）の**最新 attempt** の `verdict.yaml` を返す
- 最新 attempt の判定が欠落・破損していたり `result.json` と矛盾したりする場合は、過去 attempt に遡らず停止する
- artifact から解決した場合は、`source: artifact`、採用した run / attempt / ファイル参照、`ended_at` を出力して出典を区別する

runner 側の `resolve_verdict()`（`kaji_harness/verdict.py`）は `verdict.yaml` を最上位の真実として扱っている。
provenance 解決 CLI だけがこのファイルを無視している非対称を、marker を優先したまま解消する。

## 再現手順

1. local provider の Issue を作成する。`<artifacts_dir>/<issue>/runs/<run_id>/steps/implement/attempt-001/verdict.yaml`
   に `status: PASS` の pure YAML を置く（harness の producer step 完了時と同じ状態）
2. Issue へ marker なしのコメントだけを投稿する（`kaji issue comment <id> --body "report"`。`--verdict-step` なし）
3. 同じ run 内の consumer として `KAJI_VERDICT_PATH=<artifacts_dir>/<issue>/runs/<run_id>/steps/final-check/attempt-001/verdict.yaml`
   を設定し、`kaji issue resolve-verdict <id> --step implement` を実行する
4. OB: `Error: no verdict marker found for step 'implement'`、exit 4（変更前）。EB: exit 0、`source: artifact` の JSON

## 根本原因（Root Cause）

- **なぜ間違っているか**: `resolve-verdict` は #341（commit `73c7abf`、managed starter release）で、
  `release-starter` 向けに「別 session / 別マシンから marker の meta を読む」consumer として導入された。
  run 非依存・comment-only の設計はこの用途には妥当だった。その後、#310 / #383 で `inject_verdict` を削除した際、
  `kaji_harness/workflow.py` の移行メッセージと `docs/dev/workflow-authoring.md` が、step 間で verdict を受け渡す
  汎用手段として本 CLI を案内した。しかし、同一マシン・同一 run 内の consumer という新しい用途に合わせて
  情報源は拡張されなかった。その結果、harness 自身が書いた最上位の情報源（`verdict.yaml`。ADR 005）を参照できず、
  marker 付与という producer skill の instruction 遵守だけが単一障害点になった。
- **いつから**: #341 の導入時点から comment-only。汎用 consumer として案内された #383 以降に顕在化した。
- **同根の他の箇所**:
  - `.claude/skills/review/SKILL.md` Step 5（dev-small 経路の判定）: workflow 内で `resolve-verdict --step verify-change`
    を呼ぶが、run コンテキストを渡していない。同じ marker 欠落で dev-small 経路を誤判定しうる → 本 Issue で
    `--current-verdict-path [verdict_path]` を渡すよう更新する
  - `kaji_harness/workflow.py` の `inject_verdict` 移行メッセージと `docs/dev/workflow-authoring.md` の移行手順:
    run コンテキストの渡し方が未記載 → docs を更新する（エラーメッセージ文言は既存テストが固定しており、
    仕様上の不足もないため変更しない）
  - `.claude/skills/issue-small-change-execute` / `issue-small-change-review` の手動実行時の phase 判定、
    `release-starter`: workflow 外の手動実行、または `--require-meta` 必須の consumer。artifact fallback の恩恵は
    受けないが、artifact 出力を受け取った場合の扱い（時刻不明なら停止）を明記する

## インターフェース

### 入力（変更前 / 変更後）

変更前:

```
kaji issue resolve-verdict <issue_id> --step <step> [--require-meta <key>]...
```

変更後（既存引数は不変。run コンテキストを与える手段を追加）:

```
kaji issue resolve-verdict <issue_id> --step <step> [--require-meta <key>]...
    [--run <run_id> | --current-verdict-path <path>]
```

| 入力 | 意味 | 検証 |
|------|------|------|
| `--run <run_id>` | 対象 run を run_id で明示指定する（人間の手動実行向け）。run dir は `<resolve_artifacts_dir(config)>/<正規化済み issue_id>/runs/<run_id>`（`kaji run` と同じ main worktree 基準）とする | `^\d{12}(-\d{3,})?$`（`allocate_run_dir` の採番形式）。不一致は exit 2 |
| `--current-verdict-path <path>` | 呼び出し元 step 自身の `verdict_path`（agent step はプロンプトのコンテキスト変数 `[verdict_path]` をそのまま渡す）。run はパスから導出する | 形状 `<root>/<issue>/runs/<run_id>/steps/<step>/attempt-NNN/verdict.yaml` に一致し、`<issue>` が正規化済み issue_id と一致すること。不一致は exit 2。ファイル自体の存在は要求しない（consumer 自身の verdict は未保存のため） |
| 環境変数 `KAJI_VERDICT_PATH` | 上記 2 option がどちらも無い場合の実行コンテキスト（exec / exec_script step に runner が注入する） | `--current-verdict-path` と同じ形状検証。ただし**不一致や不正は exit 2 にせず「run を特定できない」扱い（fallback しない）**にする。marker 解決経路へ影響させないため |

- `--run` と `--current-verdict-path` は argparse の mutually exclusive group で排他にする（両指定は exit 2）
- 優先順位: 明示 option > `KAJI_VERDICT_PATH`
- 明示 option の形状検証は引数解析直後に行う（fail-fast）。これは新 option の利用者だけに影響し、既存の呼び出しは変わらない

### 出力

**marker 解決時（不変）**: 従来どおり `{"step", "status", "meta", "created_at"}` の JSON を返す。
key の追加・削除も値の変更もしない。

**artifact 解決時（新規）**: stdout に次の JSON を出力する（`_emit_json`、既存と同じ整形）。

```json
{
  "step": "implement-precheck",
  "status": "PASS",
  "meta": {},
  "source": "artifact",
  "run_id": "260903210335",
  "requested_run_id": "260903220114",
  "attempt": 1,
  "verdict_path": "/abs/.kaji/artifacts/122/runs/260903210335/steps/implement-precheck/attempt-001/verdict.yaml",
  "ended_at": "2026-09-03T12:03:11.482113+00:00"
}
```

| key | 型 | 説明 |
|-----|----|------|
| `step` / `status` | str | 対象 step と `verdict.yaml` の status |
| `meta` | object | 常に `{}`。artifact は marker metadata を持たない。消費側の `.meta` 参照が壊れないよう shape だけ揃える |
| `source` | `"artifact"` | artifact 解決であることを示す。marker 解決の出力にはこの key を**付けない**（出力を不変に保つため）。consumer は「`source == "artifact"` なら artifact、key が無ければ marker」と判別する |
| `run_id` | str | 判定を採用した run（復旧元へ遡った場合は親 run） |
| `requested_run_id` | str | run コンテキストから特定した起点 run（`run_id` と異なれば recovery chain を遡った証跡になる） |
| `attempt` | int | 採用した attempt 番号（`attempt-NNN` の NNN） |
| `verdict_path` | str | 採用した `verdict.yaml` の絶対パス |
| `ended_at` | str \| null | 同じ attempt の `result.json` の `ended_at`（記録値そのまま）。`result.json` が無い場合や、`ended_at` が欠落・parse 不能・timezone なしの場合は `null`（時刻不明）。mtime や現在時刻で補完しない |

`created_at` key は artifact 出力に**含めない**。コメント投稿時刻と attempt 終了時刻を取り違えさせないため。

**終了コード**

| code | 条件 | 変更 |
|------|------|------|
| 0 | marker 解決成功 / artifact 解決成功 | artifact 成功を追加 |
| 3（既存 `EXIT_RUNTIME_ERROR`）| コメント取得失敗（`GitHubProviderError` / `IssueNotFoundError`）。**artifact で迂回しない** | 不変 |
| 2 | 引数不正（既存）+ 明示 run option の形状不正・排他違反・issue 不一致 | 条件を追加 |
| 4 | marker 不在で、かつ次のいずれか: run コンテキストなし / 特定不能、run dir がローカルに無い、run と記録済み復旧元のどれでも対象 step が一度も実行されていない、`recovery-chain.json` が読めない・不正 | 条件を拡張（意味は「解決できる verdict が無い」のまま） |
| 5 | 最新 marker が不正。**fallback しない** | 不変 |
| 6 | 最新 marker に必須 meta が欠落（既存）。artifact 解決時に `--require-meta` 指定があれば常にこれ（meta を推測しない） | artifact 経路を追加 |
| 7（新規 `EXIT_VERDICT_ARTIFACT_UNUSABLE`） | 対象 run の最新 attempt が採用不能: `verdict.yaml` 欠落・parse 不能・status 語彙外・ABORT/BACK で suggestion 空、`result.json` が存在するが JSON object として読めない、`result.json` が異常終了（`synthetic: true` または `error` 非 null）や `verdict.yaml` と異なる `status` を示す | 新規 |

exit 7 は run コンテキストを与えた呼び出しでしか発生しない。既存の呼び出し（run コンテキストなし）の終了コード集合は変わらない。

### 使用例

```bash
# agent step（consumer skill）: プロンプト注入の verdict_path をそのまま渡す
kaji issue resolve-verdict [issue_id] --step implement-precheck \
  --current-verdict-path [verdict_path]

# 人間が run を指定して手動確認
kaji issue resolve-verdict 122 --step implement-precheck --run 260903210335

# exec / exec_script step: runner 注入の KAJI_VERDICT_PATH が自動で使われる
kaji issue resolve-verdict "$KAJI_ISSUE_ID" --step implement
```

## 制約・前提条件

- `.kaji/artifacts/`（`paths.artifacts_dir`）は gitignore 対象のマシンローカルである。artifact が無い環境
  （別マシン / 別 clone）では、marker が無ければ従来どおり exit 4 になる
- runner の `resolve_verdict()`（実行時の verdict 解決）は変更しない。本修正は provenance 解決 CLI だけを変える
- `resolve-verdict` は読み取り専用の CLI とする。marker の自動投稿や artifact への書き込みは行わない
- attempt layout（`runs/<run_id>/steps/<step_id>/attempt-NNN/`）だけを対象にする。旧 flat layout
  （`runs/<run_id>/<step_id>/`）は「step 未実行」と同じ扱いになる（ADR 005 で新 layout を正としている）
- `latest` symlink には依存しない（ARCHITECTURE.md: 「harness の verdict 解決は `latest` に依存しない」と同じ原則）
- 新規 status 語彙は導入しない。`verdict.yaml` の status は marker と同じ文法
  （`PASS|RETRY|ABORT|BACK|BACK_[A-Z0-9_]+`）で検証する。resolve-verdict は workflow の `on:` キーを知らないため
- Python 規約: `docs/reference/python/` に従う（型ヒント、Google docstring、エラー階層は `kaji_harness/errors.py`）

## 変更スコープ

| ファイル | 変更 |
|----------|------|
| `kaji_harness/artifact_verdict.py`（新規） | run コンテキストの解析、run / 復旧元の探索、最新 attempt の選択、`verdict.yaml` / `result.json` の検証、`ResolvedArtifactVerdict` の返却 |
| `kaji_harness/errors.py` | `VerdictArtifactNotFoundError`（→ 4）/ `VerdictArtifactUnusableError`（→ 7）を追加 |
| `kaji_harness/verdict.py` | `verdict.yaml` を任意 status 語彙で読む公開関数（例: `load_verdict_yaml_for_marker_vocabulary`）を追加。既存 `_parse_yaml_fields` を再利用し、status 検証を marker 文法に差し替える |
| `kaji_harness/providers/markers.py` | status 文法の判定を公開関数（例: `is_valid_verdict_status`）として公開し、`verdict.py` から再利用する（regex の二重定義を避ける） |
| `kaji_harness/commands/issue.py` | `resolve-verdict` の引数追加、marker 不在時の fallback 分岐、exit 7、artifact 出力。`_handle_issue` から artifacts dir の遅延 resolver を渡す |
| `tests/test_resolve_verdict.py` ほか | 下記テスト戦略 |
| docs / skills | 下記「影響ドキュメント」 |

## 方針

### 解決フロー（疑似コード）

```python
def handle_resolve_verdict(provider, rest, *, artifacts_dir_resolver=None):
    ns = parse(rest)                        # --run / --current-verdict-path は排他
    issue_id = normalize(ns.issue_id)       # 既存
    explicit_ctx = parse_explicit_run_context(ns, issue_id)   # 形状不正 → exit 2
    try:
        comments = provider.list_issue_comments_all(issue_id)  # 失敗 → exit 3（迂回しない）
    except ...: return 3
    try:
        return emit(resolve_latest_verdict(comments, step=..., required_meta=...))  # 既存そのまま
    except VerdictMarkerNotFoundError as exc:
        ctx = explicit_ctx or run_context_from_env(os.environ, issue_id)  # env 不正 → None
        if ctx is None:
            return 4                        # 既存メッセージのまま
        # --run の場合だけ、ここで初めて artifacts dir を解決する（git subprocess を遅延させる）
        try:
            art = resolve_artifact_verdict(ctx.run_dir, step=ns.step)
        except VerdictArtifactNotFoundError: return 4
        except VerdictArtifactUnusableError: return 7
        if ns.require_meta:
            return 6                        # artifact は meta を持たない。推測しない
        return emit(art.as_json())
    except VerdictMarkerMalformedError: return 5      # fallback しない
    except VerdictMarkerMetaMissingError: return 6    # fallback しない
```

- `resolve_latest_verdict()` の関数契約・例外は変更しない。fallback は CLI ハンドラ側の `VerdictMarkerNotFoundError`
  分岐でだけ行う。これにより「marker がある場合は artifact を一切読まない」が構造的に保証される
  （artifact が壊れていても marker 解決に影響しない）
- コメント取得を `resolve_latest_verdict` の try から分離し、取得失敗が fallback 分岐に入らないことをコード構造で示す

### run / attempt の選択（`resolve_artifact_verdict`）

```python
def resolve_artifact_verdict(run_dir, *, step):
    runs_dir = run_dir.parent
    if not run_dir.is_dir(): raise NotFound("run dir not present locally")
    visited = set()
    current = run_dir
    while True:
        attempts = sorted(attempt dirs matching ^attempt-(\d{3,})$ under current/steps/<step>/, key=int(N))
        if attempts:
            return adopt(attempts[-1])       # 最新 attempt のみ。過去 attempt には戻らない
        chain = read_recovery_chain(current / "recovery-chain.json")   # 既存 reader
        if chain is None: raise NotFound("step never executed in run or recorded recovery sources")
        parent = chain.parent_run_id
        if not RUN_ID_RE.fullmatch(parent) or parent in visited: raise NotFound(...)
        visited.add(current.name); current = runs_dir / parent
        if not current.is_dir(): raise NotFound(...)
```

- 「一度も実行されていない」は `steps/<step>/` 配下に `attempt-NNN` ディレクトリが 1 つも無いこととする
  （`latest` symlink のみ、または空ディレクトリの場合も未実行扱い）
- 復旧元は `recovery-chain.json` の `parent_run_id` だけを辿る（runner が child run 起動時に書く記録。
  `recovery/models.py` `write_recovery_chain`）。同じ runs dir 内の他の run（Issue 内の最新 run など）は探索しない
- `parent_run_id` は run_id 文法で検証し、パス traversal と循環を防ぐ（不正・循環は not found = exit 4）

### 最新 attempt の採用判定（`adopt`）

1. `verdict.yaml` が無い → Unusable（7）。過去 attempt の PASS は探さない
2. `verdict.yaml` を読む（`_parse_yaml_fields` + marker status 文法 + ABORT/BACK の suggestion 必須）。失敗 → Unusable（7）
3. `result.json` が無い → 採用する（`ended_at = None`）。ARCHITECTURE.md の「読み手は `result.json` の欠落を許容」に合わせる
4. `result.json` があるが JSON object として読めない → Unusable（7）。明示的な矛盾の可能性を無視しないため
5. `result.json` の `synthetic is True` または `error` が非 null → Unusable（7、異常終了）
6. `result.json` の `status` が `verdict.yaml` の status と異なる → Unusable（7、status 矛盾）
7. `ended_at`: str であり、`datetime.fromisoformat` で parse でき、tz-aware であれば記録文字列をそのまま出力する。
   それ以外は `null`

異常終了の判定に `exit_code` / `signal` を使わない理由: 正常な attempt でも CLI の SIGTERM ハンドリングによって
正値の returncode が残ることがあり（`kaji_harness/cli.py` の terminate 後処理のコメント）、異常の根拠として使えない。
runner は異常終了経路で必ず `synthetic: true` と `error` を記録する（`_record_dispatch_failure` → `AttemptResult`）ため、
この 2 つで判定する。

### consumer / docs の更新方針

- 時刻比較の規則: marker 出力は `created_at`、artifact 出力は `ended_at` を比較キーにする。文字列比較ではなく
  ISO 8601 の時刻として比較する（`Z` と `+00:00` の表記差があるため）。キーが `null` / 不在で新旧比較が必要な場合は停止する
- `review/SKILL.md` の dev-small 判定は harness 起動時に `--current-verdict-path [verdict_path]` を渡す
  （手動実行時はコンテキスト変数が無いため付けない）

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 情報源の優先順位・既存互換 | marker 優先。artifact は marker 不在時だけ。marker 不正・必須 meta 欠落は救済しない | Issue 本文「決定事項」表 1 行目（grill-me 最終要約へのユーザー承認） | fallback を CLI ハンドラの `VerdictMarkerNotFoundError` 分岐だけに置き、`resolve_latest_verdict` の契約は変えない。marker 出力には `source` key も足さない |
| 対象 run | 明示指定または実行コンテキストで特定した run と、記録済み復旧元だけ。特定できなければ exit 4 | 「決定事項」表 2 行目（質問 1 への yes） | 明示指定 = `--run` / `--current-verdict-path`。実行コンテキスト = `KAJI_VERDICT_PATH`。復旧元 = `recovery-chain.json` の `parent_run_id` 連鎖 |
| attempt と遡及 | 最新 attempt のみ採用。欠落・破損なら停止。ABORT/RETRY はそのまま返す。復旧元へは step 未実行時だけ進む | 「決定事項」表 3 行目（質問 2 への yes） | 「最新」は `attempt-NNN` の数値最大（`latest` symlink 非依存）。「未実行」は attempt dir が 0 件 |
| artifact 出力と provenance | `source: artifact`、run / attempt / ファイル参照を返す。marker 出力は維持 | 「決定事項」表 4 行目（質問 3 への yes） | JSON key を `source` / `run_id` / `requested_run_id` / `attempt` / `verdict_path` / `ended_at` / `meta: {}` と定めた。`created_at` は出さない |
| 時刻 | `result.json` の `ended_at` を使い、取得できなければ不明（null）。mtime / 現在時刻で補完しない。新旧比較が必要な consumer は不明時に停止 | 「決定事項」表 5 行目（質問 4 への yes） | 記録文字列をそのまま出す（正規化しない）。parse 不能・tz なしは null。consumer には時刻として比較するよう docs で規定 |
| `result.json` の欠落・矛盾 | 欠落なら `verdict.yaml` 単独で採用。存在して異常終了・status 矛盾なら停止 | 「決定事項」表 6 行目（質問 6 への yes） | 異常終了 = `synthetic: true` または `error` 非 null。status 矛盾 = `result.status != verdict.status` |
| verdict への時刻追加 | 今回は含めない（#440） | 「決定事項」表 7 行目 | 本設計は `verdict.yaml` の schema を変更しない |
| 必須 meta の非推測 | artifact 解決時に `--require-meta` があれば exit 6 | Issue 本文「AI の仮定と後段の検査先」1 行目（記録承認済みの AI 仮定） | 設計・コードレビューとテストで検査 |
| コメント取得失敗の非迂回 | 取得失敗は exit 3（既存 `EXIT_RUNTIME_ERROR`）。artifact を見ない | 同 2 行目（AI 仮定） | コメント取得の try を marker 解決から分離 |
| marker 自動投稿なし | 読み取り専用のまま | 同 3 行目（AI 仮定） | 書き込み API を呼ばない。テストでコメント数の不変を確認 |
| CLI option 名・終了コード割当・コンテキスト伝搬 | `--run` / `--current-verdict-path`、新 exit 7、明示 option > env | grill-me provenance コメント「CLI オプション名、artifact JSON の詳細 schema、停止理由ごとの終了コード割当、コンテキスト伝搬・artifact ディレクトリ解決の具体化は…設計する」（人間が設計へ委任した範囲） | 採用不能（7）を未検出（4）と分けた。consumer が「artifact はあるが最新 attempt が壊れている」ことを判別できるようにするため。既存の呼び出しでは 7 は発生しない |
| `KAJI_VERDICT_PATH` の暗黙利用 | option が無ければ env を実行コンテキストとして使う。env が不正なら exit 2 にせず fallback しない | **AI の仮定**。根拠: exec / exec_script step の実行コンテキストは runner が注入する env であり、ADR 005 でも `verdict_path` と同じ役割を持つ。不正な env で exit 2 にすると、marker がある既存呼び出しまで壊れる。後段の検査先: review-design / review-code | 優先順位と、env 不正時に fallback しない扱いを定義 |
| `result.json` が JSON として読めない場合 | 採用不能（7） | **AI の仮定**。根拠: 決定事項は「欠落」だけを許容しており、壊れたファイルは異常終了を否定できない。fail-closed を優先する。後段の検査先: review-design / review-code | 欠落（許容）と破損（停止）を区別 |
| `ended_at` の表記 | 記録文字列をそのまま出力 | **AI の仮定**。根拠: provenance の忠実性を優先する。比較の正しさは consumer 規則（時刻として比較）で担保する。後段の検査先: review-design | 正規化しない代わりに、parse 可能・tz-aware であることを検証 |

one-way door の未決は無い。公開 CLI の引数・終了コード・stdout 契約は one-way door になりやすい軸だが、
(1) 既存の引数・出力・終了コードは不変であり、(2) 追加分の具体化は grill-me provenance で人間が設計へ明示的に委任している。

## テスト戦略

### 変更タイプ

- 実行時コード変更（公開 CLI の振る舞いを拡張）

### 再現テスト（bug 固有・必須）

- `KAJI_VERDICT_PATH` を consumer の attempt パスに設定し、marker なしのコメントと producer の `verdict.yaml`（PASS）を置く。
  その状態で `_handle_issue_resolve_verdict` を呼ぶ。修正前は exit 4（OB そのもの）、修正後は exit 0 で
  `source: artifact` / `status: PASS` となる。env 経路は既存の引数だけで再現できるため、修正前にも Red として実行できる。
- 加えて、downstream #122 の実ログ（`no verdict marker found` / exit 4）が OB と一致しているため、実装前 Red 証跡の
  代替としても扱える（bug.md escape clause）。恒久回帰テスト自体は省略しない。

### Small テスト

外部依存のない純粋ロジック:

- run コンテキストのパス解析: 正しい `verdict_path` から run dir / run_id を導出できること。`steps` / `runs` の
  位置ずれ、`attempt-NNN` 以外の名前、issue 不一致を拒否すること
- run_id 文法の検証（`260903210335` / `260903210335-002` は受理、`../x`・空文字・桁数違いは拒否）
- attempt 名から番号への変換と、数値順での最新選択（`attempt-010` > `attempt-009`。`latest` や不正名は無視）
- `result.json` の dict から異常判定と `ended_at` を抽出するロジック（synthetic / error / status 矛盾 /
  ended_at の parse 不能・tz なし → null）
- marker 文法での status 検証（`BACK_DESIGN` 受理、`back`・`BACK_` 拒否）

### Medium テスト

tmp_path 上に artifact dir と LocalProvider の Issue を構築し、`_handle_issue_resolve_verdict` を通して検証する
（完了条件と 1 対 1 に対応させる）:

- **marker 互換**: 有効な marker と artifact（別 status、または壊れたもの）が共存する場合も、出力 JSON が
  `{step, status, meta, created_at}` のみで marker の値と一致し、exit 0 であること
- **marker 不正 / 必須 meta 欠落**: artifact があっても exit 5 / exit 6 のままであること
- **コメント取得失敗**: `list_issue_comments_all` が `GitHubProviderError` を送出する fake provider で、
  artifact があっても exit 3 になり、artifact を読まないこと
- **最新 attempt の採用**: attempt-001 が PASS、attempt-002 が RETRY → RETRY を返すこと。ABORT も素通しされること
- **run 特定不能**: option も env も無い、または env が不正 → exit 4。同じ runs dir の別 run に PASS があっても
  採用しないこと
- **最新 attempt の欠落・破損**: attempt-002 の `verdict.yaml` 欠落 / YAML 破損 / 語彙外 status → exit 7。
  attempt-001 の PASS に戻らないこと
- **復旧元 run**: 起点 run に step の attempt が無く、`recovery-chain.json` に parent がある → parent の最新 attempt を返し、
  `run_id` が parent、`requested_run_id` が起点になること。起点 run に attempt があれば parent を見ないこと。
  `recovery-chain.json` が無ければ、他の run に PASS があっても exit 4。循環・不正 parent_run_id → exit 4
- **artifact 不在**: run dir が存在しない（別マシン相当）→ exit 4
- **出力と時刻**: artifact 出力に `source` / `run_id` / `requested_run_id` / `attempt` / `verdict_path` / `ended_at` が含まれ、
  `created_at` が含まれないこと。`ended_at` は `result.json` の値と一致すること。`result.json` が無い場合や
  `ended_at` が壊れている場合は null であること（ファイルの mtime を変えても値が変わらないこと）
- **`result.json` の矛盾**: `synthetic: true` / `error` 非 null / status 不一致 → exit 7。`result.json` なし → exit 0
- **`--require-meta` + artifact** → exit 6
- **副作用なし**: 実行前後で LocalProvider のコメント数が変わらず、artifact dir にファイルが増えないこと
- **引数**: `--run` と `--current-verdict-path` の同時指定、`--run` の文法違反、issue 不一致の `--current-verdict-path` → exit 2
- **`--run`**: 注入した `artifacts_dir_resolver` で run dir を解決できること。marker がある場合は resolver が呼ばれないこと

### Large テスト

- `tests/test_starter_cli_large_local.py` の既存 `resolve-verdict` subprocess テストと同じ形式で 1 本追加する
  （`large_local`）。実 `kaji` CLI を subprocess 起動し、local provider の `.kaji/config.toml` の
  `paths.artifacts_dir` と `--run` を使って artifact 解決が成功すること（exit 0、`source: artifact`）を確認する。
  config → `resolve_artifacts_dir` → `_handle_issue` dispatch の結合は Medium ではカバーできないため必要
- 実 GitHub API 疎通（`large_forge`）は追加しない。GitHub provider との差分はコメント取得部分だけで、その部分は
  変更しない（取得失敗時の非迂回は Medium で fake provider を使って検証する）

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定ではない。ADR 005（runner の artifact 優先解決）は変更しない。provenance CLI の情報源規則は ARCHITECTURE.md と Issue 決定事項に記録する |
| docs/ARCHITECTURE.md | あり | 「実行アーティファクト」節の近くに `resolve-verdict` の解決規則（marker 優先・artifact fallback・run/復旧元・最新 attempt・出力・終了コード）を追記する |
| docs/dev/shared_skill_rules.md | あり | verdict marker の consumer 節を一般化する。run コンテキストの渡し方、artifact 出力の判別、時刻比較の規則（不明なら停止）を追加する |
| docs/dev/workflow-authoring.md | あり | `inject_verdict` の移行手順で、`--current-verdict-path [verdict_path]` の付与を案内する |
| .claude/skills/review/SKILL.md | あり | dev-small 判定の `resolve-verdict` に harness 起動時の `--current-verdict-path [verdict_path]` を追加する |
| .claude/skills/issue-small-change-execute/SKILL.md / issue-small-change-review/SKILL.md | あり | 手動 phase 判定の時刻比較に artifact 出力の扱い（`ended_at`、null なら停止）を追記する |
| .claude/skills/release-starter/SKILL.md | なし | `--require-meta` 必須のため、artifact 解決時は常に exit 6（fail-closed で ABORT）となり、既存記述のままで正しい |
| docs/reference/ | なし | Python 規約・API 仕様の変更なし |
| docs/cli-guides/ | なし | `resolve-verdict` を記載したガイドは無い（grep 確認済み） |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| CHANGELOG.md | あり | `[Unreleased]` に Fixed / Added（新 option と exit 7）を追記する |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #426 本文「決定事項」「完了条件」 | https://github.com/apokamo/kaji/issues/426 | 人間承認済みの 7 方針と 3 つの AI 仮定。本設計の要件の正本 |
| grill-me provenance コメント | https://github.com/apokamo/kaji/issues/426#issuecomment-5914616383 | 質問ごとの決定理由。CLI オプション名・JSON schema・終了コード割当・コンテキスト伝搬は設計へ委任 |
| 現行 provenance 解決 | `kaji_harness/commands/issue.py`（`resolve_latest_verdict` / `_handle_issue_resolve_verdict` / `EXIT_VERDICT_*`） | comment のみを逆順走査。未検出 4 / 不正 5 / meta 欠落 6、取得失敗 1 |
| runner の verdict 解決 | `kaji_harness/verdict.py` `resolve_verdict()` / `load_verdict_yaml` / `write_verdict_yaml` | artifact → comment → stdout の順。`verdict.yaml` は `status/reason/evidence/suggestion` の 4 フィールドで、run/step/attempt はパスで表す |
| attempt 採番と context 注入 | `kaji_harness/runner.py` `allocate_run_dir` / `allocate_attempt_dir` / `_build_context_env` / `_record_attempt_end` | run_id は `%y%m%d%H%M%S`（衝突時 `-NNN`）。attempt は `attempt-NNN`。exec 系には `KAJI_VERDICT_PATH` を注入。result.json は best-effort |
| attempt 終了情報 | `kaji_harness/result.py` `AttemptResult` / docs/ARCHITECTURE.md § `result.json` | `ended_at` は UTC ISO 8601。読み手は欠落を許容。`synthetic` は合成 ABORT を示す |
| 復旧 chain | `kaji_harness/recovery/models.py` `write_recovery_chain` / `read_recovery_chain`、docs/ARCHITECTURE.md § `recovery-chain.json` | child run が起動直後に `{root_run_id, parent_run_id}` を書く。読めなければ None |
| marker 文法 | `kaji_harness/providers/markers.py` | status `PASS|RETRY|ABORT|BACK|BACK_[A-Z0-9_]+` |
| artifacts dir 解決 | `kaji_harness/artifacts.py` `resolve_artifacts_dir` | `kaji run` と同じ main worktree 基準。非 run の callsite では git subprocess を避ける設計 → 遅延呼び出しにする |
| ADR 005 | `docs/adr/005-artifact-primary-verdict.md` | `verdict.yaml` を primary とする決定。exec_script では env `KAJI_VERDICT_PATH` |
| 異常終了時の returncode | `kaji_harness/cli.py`（terminate 後処理のコメント） | 正常終了でも正値の returncode が残りうるため、`exit_code` は異常の根拠にならない |
| downstream 実害 | https://github.com/apokamo/fullstack-agent-template/issues/128 、https://github.com/apokamo/fullstack-agent-template/issues/122#issuecomment-5527400830 | marker 欠落と artifact の非対称。#122 は run 260903210335 / implement-precheck / attempt 001 の保存済み PASS を復元した |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L の判定基準（ファイル I/O → Medium、subprocess → large_local） |
