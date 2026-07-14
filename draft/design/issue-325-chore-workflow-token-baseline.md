# [設計] workflow token 使用量の測定基盤と baseline の確立

Issue: #325

## 概要

kaji workflow の run artifacts（`paths.artifacts_dir` 配下）と agent transcript（Claude / Codex のローカルセッションログ）を入力に、step attempt 単位の API calls / output tokens / cache-read / max context / wall time / verdict を系列別に集計する測定ツールを `experiments/wf-token-usage/` に追加し、現行 `issue-implement` の固定読込文字数と直近 run の実測値を比較 baseline として記録する。

## 背景・目的

### ユーザーストーリー

workflow 保守者として、token 削減施策（skill 軽量化・step 構成変更等）の採否を判断するために、変更前後の同一系列 run を再現可能なコマンドで集計し、token 指標と品質指標（RETRY / BACK 率）を並べて比較したい。

### 現状の問題

- token 削減施策の効果を測る共通 baseline がなく、skill 文字数・API calls・cache-read・wall time・品質回帰を同じ系列で比較できない。
- kamo2 #1382 では root / Web を混ぜない測定が必要と判明した。kaji でも workflow / step / agent / model / effort の異なる run を単純平均すると施策効果を誤認する（例: `dev-thorough-fable.yaml` は implement を codex `gpt-5.6-sol` effort=high、design を claude `fable` effort=high で走らせており、agent 混在平均は無意味）。

### 代替案と不採用理由

- **kaji_harness runner に usage 記録を組み込む**: `run.log` の `step_end` は `cost: null` を記録しており、runner 側の計測拡張は artifact schema 変更（本 Issue スコープ外）になる。既存 artifact + agent 側ローカル transcript の読み取りだけで必要指標が揃うことを実測で確認済みのため、読み取り専用ツールとする。
- **kamo2 の `measure_implement_usage.py` をそのまま移植**: kamo2 版は Claude transcript 専用・implement step 固定・baseline 数値のハードコードあり。kaji は codex step が主系列（dev-thorough-fable の implement は codex）のため、Codex rollout JSONL の読み取りと系列分離を最初から設計に含める必要がある。アルゴリズム（usage 集計・session_id 横断検索）は踏襲する。

## インターフェース

### 入力

CLI（公開 `kaji` コマンドには追加しない。`experiments/` 配下の standalone script）:

```
uv run python experiments/wf-token-usage/measure_wf_usage.py <issue_id> [<issue_id>...]
    [--run RUN_ID]          # 特定 run のみ（省略時は issue 配下の全 run）
    [--step STEP_ID]        # 特定 step のみ（省略時は全 step）
    [--format table|json]   # 既定 table
```

| 引数 | 型 | 必須 | 説明 |
|------|-----|------|------|
| `issue_id` | str（可変長、1 個以上） | ✅ | `<artifacts_dir>/<issue_id>/runs/` を走査する |
| `--run` | str | - | run ID（例: `260714213832`）。存在しない場合は exit 2 |
| `--step` | str | - | step ID（例: `implement`） |
| `--format` | `table` \| `json` | - | 出力形式。既定 `table` |

**入力起点の解決（ハードコード禁止）**: `kaji_harness.config.KajiConfig.discover()` + `kaji_harness.artifacts.resolve_artifacts_dir(config)` を **import して直接使う**。「相当の再実装」ではなく正本そのものを経由するため、`artifacts_dir` を変更した環境・worktree 起動でも同じ解決結果になる。`.venv` に kaji が editable install 済み（`make check` 前提環境）であることを利用する。legacy パス（`.kaji/artifacts/`）の探索は行わない。

### データソースと取得可能フィールド（一次情報に基づく一覧）

実在 run `.kaji-artifacts/323/runs/260714213832/` の実測に基づく。

| ソース | パス | 取得フィールド |
|--------|------|----------------|
| run log | `<artifacts_dir>/<issue>/runs/<run_id>/run.log`（JSONL） | `workflow_start`: workflow 名 / `step_start`: step_id, **agent, model, effort**, attempt, dispatch / `step_end`: verdict.status, duration_ms, exit_code, signal（`cost` は現行 null、`step_start.session_id` も null） |
| step result | `steps/<step_id>/attempt-NNN/result.json` | status, started_at, ended_at, **duration_ms**（wall time）, **session_id**, dispatch, error, synthetic |
| session state | `<artifacts_dir>/<issue>/session-state.json` | `sessions`（step→session_id map）, `step_history`（verdict 全文） |
| Claude transcript | `~/.claude/projects/*/<session_id>.jsonl` | assistant message の `usage`: output_tokens, cache_read_input_tokens, input_tokens, cache_creation_input_tokens, `message.model` |
| Codex rollout | `~/.codex/sessions/YYYY/MM/DD/rollout-*-<session_id>.jsonl` | `event_msg` type=`token_count` の `info`: `total_token_usage`（累計: input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens, total_tokens）, `last_token_usage`（コール単位）, `model_context_window` |
| Gemini | 取得手段なし（`~/.gemini/` にセッション transcript 相当が存在しないことを確認済み） | すべて missing（reason: `provider_unsupported`） |

**指標の定義**:

| 指標 | Claude | Codex |
|------|--------|-------|
| calls | `usage` を持つ assistant エントリ数 | `token_count` イベント数 |
| output_tokens | `output_tokens` の総和 | `total_token_usage.output_tokens`（最終イベントの累計値） |
| cache_read | `cache_read_input_tokens` の総和 | `total_token_usage.cached_input_tokens`（同上） |
| max_context | `input + cache_read + cache_creation` の最大値 | `last_token_usage.input_tokens` の最大値（実測で `total_tokens = input_tokens + output_tokens` であり cached は input の内数） |
| wall_time | `result.json` の `duration_ms`（transcript 不要） | 同左 |

transcript の検索は kamo2 方式を踏襲し、session_id（グローバル一意）で glob 横断検索する。checkout パス・worktree 位置に依存しない。

### 出力

1. **stdout（table）**: step attempt 単位のレコードを系列キー `(workflow, step_id, agent, model, effort)` でグループ化して表示。列: issue, run_id, attempt, dispatch, verdict, wall_time_s, calls, output_tokens, cache_read, max_context, usage_status。
2. **stdout（json）**: 同レコードの機械可読形式（後続比較や series 蓄積に使う）。
3. **系列サマリ**: 系列キーごとに件数・中央値/合計を表示。**usage_status=missing のレコードは token 系列の集計母数から除外**し、missing 件数と reason 内訳を必ず併記する（0 補完しない）。wall_time / verdict は transcript 不要のため missing でも集計する。
4. **品質指標**: run ごとに `run.log` の `step_end` verdict を走査し、step 別の RETRY / BACK（`BACK_IMPLEMENT` / `BACK_DESIGN` / `BACK_FALLBACK` 含む）件数と attempt 数を集計。review-code / final-check の RETRY・BACK 率を token 指標と同じ表に併記する。
5. **副作用**: なし（読み取り専用。ファイル書き込み・ネットワークアクセスなし）。

**欠損理由の語彙**（`usage_status=missing` の `missing_reason`）:

| reason | 条件 |
|--------|------|
| `session_id_null` | result.json の session_id が null（synthetic step 等） |
| `transcript_not_found` | session_id はあるが glob 検索でファイルが見つからない（別マシンの run 等） |
| `provider_unsupported` | agent が claude / codex 以外（gemini 等） |
| `parse_error` | transcript は存在するが usage エントリを 1 件も抽出できない |

### 使用例

```bash
# Issue 323 の全 run を集計（table）
uv run python experiments/wf-token-usage/measure_wf_usage.py 323

# 特定 run の implement step のみを JSON で
uv run python experiments/wf-token-usage/measure_wf_usage.py 323 \
    --run 260714213832 --step implement --format json

# 施策前後の比較: 系列キーが一致する行同士を比較する
uv run python experiments/wf-token-usage/measure_wf_usage.py 323 325 --step implement
```

### エラー

| 状況 | 挙動 |
|------|------|
| artifacts_dir 不在 / issue ディレクトリ不在 / `--run` 指定の run 不在 | stderr にメッセージ、exit 2 |
| run.log 不在・パース不能な行 | 該当 run / 行を skip し stderr に警告（他 run の集計は継続） |
| transcript 不在 | エラーにせず `usage_status=missing` として出力に含める |

## 制約・前提条件

- **kaji_harness 本体は変更しない**（読み取り専用の利用のみ）。依存方向は `experiments → kaji_harness` の一方向。
- **公開 CLI（`kaji` コマンド）には追加しない**（Issue「対象スコープ」の明示事項）。
- transcript はローカル環境依存（`~/.claude/projects/` / `~/.codex/sessions/`）。CI や別マシンでは missing になるだけで fail しない設計とする。
- 追加依存なし（stdlib のみ: `json` / `argparse` / `pathlib` / `statistics`。config 読み取りは kaji_harness 経由）。
- artifact schema・workflow YAML・skill 本文は変更しない（スコープ境界）。
- `run.log` の `step_start.session_id` は null のため、session_id は各 attempt の `result.json` を正とする（`session-state.json` の `sessions` map は run 跨ぎで上書きされるため attempt 単位の対応が取れない）。

## 変更スコープ

| 種別 | パス | 内容 |
|------|------|------|
| 新規 | `experiments/wf-token-usage/measure_wf_usage.py` | 測定 CLI（収集・集計・表示） |
| 新規 | `experiments/wf-token-usage/baseline.md` | 測定記録: `issue-implement` 固定読込文字数、直近 run baseline、使い方と比較時の注意点 |
| 新規 | `tests/test_wf_token_usage.py` | Small / Medium テスト |
| 変更 | `Makefile` | `SOURCES` に `experiments/` を追加し ruff 対象へ（mypy は `kaji_harness/` 固定のまま。配布 package 外のため型ゲートは課さない） |
| 不変更 | `kaji_harness/` / `.kaji/wf/` / `.claude/skills/` | スコープ境界（混在禁止） |

## 方針（Minimal How）

単一 script 内を「収集（I/O）」と「集計（純粋関数）」に分離する。

- `iter_step_records(run_dir) -> list[StepRecord]` — run.log と result.json を突き合わせ、step attempt 単位のメタ（workflow, step, agent, model, effort, attempt, verdict, duration_ms, session_id, dispatch）を組み立てる
- `find_transcript(session_id) -> Path | None` / `find_codex_rollout(session_id) -> Path | None` — glob 横断検索
- `aggregate_claude_usage(lines: Iterable[str]) -> UsageSummary | None` — 純粋関数（入力は行イテレータ、I/O なし）
- `aggregate_codex_usage(lines: Iterable[str]) -> UsageSummary | None` — 同上
- `attach_usage(record, ...) -> MeasuredRecord` — agent 種別で振り分け、missing_reason を決定
- `summarize_series(records) -> ...` — 系列キーでグループ化、missing 除外集計、RETRY/BACK 計数
- `render_table(...)` / `render_json(...)`

データフロー: `config 解決 → issue/run 走査 → StepRecord 列 → transcript 解決 → UsageSummary 付与 → 系列サマリ → 表示`。

テストからの import は `tests/test_wf_token_usage.py` 内で `importlib.util.spec_from_file_location` により script を直接ロードする（`experiments/` は配布 package（`include = ["kaji_harness*"]`）に含めないため、パッケージ化しない）。

### baseline.md に記録する内容

1. **`issue-implement` 固定読込文字数**: SKILL.md + 前提知識 5 docs + type 別ガイド + 共有ルールの `wc -c`（バイト数）と `wc -m`（文字数）。2026-07-14 時点（main 9f1b3c5）の実測では固定読込 8 ファイル合計 83,039 bytes（SKILL.md 29,080 / development_workflow.md 13,172 / workflow_completion_criteria.md 13,635 / documentation_update_criteria.md 2,570 / testing-convention.md 12,411 / python-style.md 7,091 / implement-by-type/feat.md 3,650 / report-unrelated-issues.md 1,430）。「必要に応じて追加読込」される可変分（`docs/reference/python/*.md` 等）は固定分に含めない旨を明記し、再計測コマンド（`wc -c <ファイル列挙>`）を記録する。
2. **直近 run baseline**: 取得可能な直近 run（例: #323 `260714213832` の implement = codex `gpt-5.6-sol` effort=high）を本ツールで集計した値（calls / output_tokens / cache_read / max_context / wall_time / verdict / RETRY・BACK）。測定条件（日付・workflow 名・main の commit・系列キー）を併記する。
3. **比較時の注意点**: 系列キーが一致する run 同士のみ比較する / missing を含む平均は使わない / Codex は累計値ベースのため attempt 途中 kill された run は過小になる、等。

## テスト戦略

> 変更タイプ: **実行時コード変更**（新規測定ツールの追加。kaji_harness 本体は不変更）。

### Small テスト

純粋関数を fixture 文字列 / dict で検証する（外部依存なし）:

- `aggregate_claude_usage`: usage 付き assistant 行から calls / output / cache_read / max_context を正しく合算する。usage なし行・非 assistant 行・破損 JSON 行を無視する。usage が 1 件もなければ None（→ `parse_error`）
- `aggregate_codex_usage`: `token_count` イベント列から累計値（最終イベント）と max_context（`last_token_usage.input_tokens` の最大）を取る。イベント 0 件で None
- missing_reason の決定ロジック: session_id null / transcript 不在 / 未対応 agent の各分岐
- 系列キー生成と `summarize_series`: **missing レコードが token 集計の母数に入らない**こと（0 補完しないことの直接検証）、missing 件数・reason 内訳が出ること
- RETRY / BACK 計数: `step_end` verdict 列から step 別に RETRY / BACK / BACK_* を数える

### Medium テスト

ファイル I/O 結合を tmp_path 上の合成 fixture で検証する:

- 模擬 artifacts ツリー（`run.log` + `steps/*/attempt-NNN/result.json`）+ 模擬 transcript JSONL を作成し、収集→transcript 解決→集計→JSON 出力までの結合を確認する（transcript 検索 root はテスト時に差し替え可能にする）
- `run.log` 欠損 run の skip、`--run` / `--step` フィルタ、issue ディレクトリ不在時の exit 2
- artifacts_dir 解決が `KajiConfig` 経由であること（tmp の `.kaji/config.toml` で `artifacts_dir` を非既定値にして解決されることを確認）

### Large テスト

**不要**。理由（`docs/dev/testing-convention.md` の 4 条件に沿う）:

1. 本ツールは外部 API / 実サービス疎通を一切行わない（ローカルファイル読み取りのみ）。Large の定義（実 API・E2E・外部サービス疎通）に該当する経路が存在しない
2. ファイル I/O 結合は Medium で捕捉済み、lint / 型は `make check`（ruff）で捕捉される
3. 実 transcript を使う Large を追加してもローカル環境の `~/.claude` / `~/.codex` に依存して flaky になるだけで、回帰検出情報は Medium の合成 fixture と同等
4. 以上の省略理由を本設計書および baseline.md に記録する

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし（stdlib のみ、既存 `resolve_artifacts_dir()` の利用） |
| docs/ARCHITECTURE.md | なし | kaji_harness 非変更。`experiments/` は runtime 構成外 |
| docs/dev/ | なし | workflow 手順・テスト規約の変更なし |
| docs/reference/ | なし | 設定仕様・API 仕様の変更なし |
| docs/cli-guides/ | なし | 公開 CLI 追加なし |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| experiments/wf-token-usage/baseline.md（新規） | あり | 使い方・比較時の注意点・baseline の正本（Issue 完了条件「使い方と比較時の注意点が docs または測定記録にある」は測定記録側で満たす） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| artifacts 設定の正本 | `.kaji/config.toml:6` | `artifacts_dir = ".kaji-artifacts"` — 入力起点はこの設定値のみから解決する |
| 解決ロジックの正本 | `kaji_harness/artifacts.py:20` | `resolve_artifacts_dir(config)`: 「相対パス + main worktree 解決成功 → `<main_worktree>/<artifacts_dir>`」「絶対パス / `~` 展開後絶対パス → そのまま返す」。本ツールはこれを import して使用 |
| 実在 run の構造 | `.kaji-artifacts/323/runs/260714213832/` | `run.log` の `step_start` に `"agent": "codex", "model": "gpt-5.6-sol", "effort": "high", "session_id": null` が、`steps/implement/attempt-001/result.json` に `"session_id": "019f60b1-cf43-7261-a177-9c027de9c0d7", "duration_ms": 1682755` が実在（session_id の正は result.json 側という設計判断の根拠） |
| Claude transcript の実在 | `~/.claude/projects/-home-aki-dev-kaji-main/9d085d40-b7f6-458b-ae71-7488bf7cad71.jsonl` | #323 design step（claude/fable）の session_id で glob 検索し実在を確認。assistant message の `usage` に output_tokens / cache_read_input_tokens 等を保持 |
| Codex rollout の実在 | `~/.codex/sessions/2026/07/14/rollout-2026-07-14T21-54-53-019f60b1-cf43-7261-a177-9c027de9c0d7.jsonl` | #323 implement step の session_id で実在確認。`token_count` イベント 148 件、payload 実測: `{"total_token_usage": {"input_tokens": 14146, "cached_input_tokens": 9984, "output_tokens": 245, ...}, "model_context_window": 258400}`。`total_tokens = input + output` より cached は input の内数 |
| 系列定義の実例 | `.kaji/wf/dev-thorough-fable.yaml` | step ごとに `agent` / `model` / `effort` が異なる（implement: codex/gpt-5.6-sol/high、design: claude/fable/high 等）— 系列分離が必須である根拠 |
| 先行実装（アルゴリズム踏襲元） | `/home/aki/dev/kamo2/experiments/wf-token-usage/measure_implement_usage.py` | Claude transcript の usage 集計（calls / output / cache_read / max_context）と session_id glob 横断検索の実装。同一マシン上でレビュワーも Read 可能 |
| kamo2 測定結果 | https://github.com/apokamo/kamo2/issues/1382 | 「root / Web を混ぜない測定が必要」— 系列分離要件の出所 |
| 固定読込文字数の実測 | `.claude/skills/issue-implement/SKILL.md:45`〜 | 「前提知識の読み込み」で 5 docs を必読指定。固定読込 8 ファイル合計 83,039 bytes（2026-07-14, main 9f1b3c5 実測） |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L 判定基準「外部 API / 実サービス疎通あり → Large」— 本ツールに Large 対象経路がない根拠 |
| packaging 境界 | `pyproject.toml:55-57` | `[tool.setuptools.packages.find] include = ["kaji_harness*"]` — `experiments/` は配布対象外、パッケージ化しない根拠 |
