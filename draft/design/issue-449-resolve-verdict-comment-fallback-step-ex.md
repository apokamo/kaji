# [設計] resolve_verdict の comment fallback が別 step の作業報告コメントを採用し exec step の RETRY を PASS に化けさせる問題の修正

Issue: #449

## 概要

`kaji_harness/runner.py` の verdict 解決（`_StepExecutor._resolve_step_verdict` → `kaji_harness/verdict.py` `resolve_verdict`）が、
**直前の別 step が投稿した作業報告コメントの verdict** を現在 step の verdict として採用する。
exec 系 step（`exec` / `exec_script`）では comment fallback を行わず、agent step では
「別 step を指す `kaji-verdict` marker を持つコメント」を候補から除外する。

## 背景・目的

### Observed Behavior (OB)

利用側 repo（apokamo/kamo2 Issue #1652、workflow `dev-web-thorough`、run `261001175550`）で、
`pr`（agent step）→ `review-poll`（exec step）の遷移時に次が起きた（Issue 本文 § 現象の run.log 引用）。

```
{"event": "verdict_source", "step_id": "review-poll", "source": "comment", "attempt": "attempt-001"}
{"event": "step_end", "step_id": "review-poll", "verdict": {"status": "PASS", "reason": "PR 作成を完了した", "evidence": "git push github HEAD 成功、uv run kaji pr create で PR #1658 作成", ...}, "dispatch": "exec"}
```

- `review-poll` は stdout に `status: RETRY` を出したが、runner は `pr` step の作業報告コメント
  （`09:33:02Z` 投稿、末尾 `status: PASS`、marker 無し）を `source=comment` で採用した
- 採用した PASS は `steps/review-poll/attempt-001/verdict.yaml` に書き戻され、事後調査でも
  「artifact」として誤った verdict が見える
- 結果として `close` step が codex review 指摘（P2 × 2）未対応の PR を merge した

### Expected Behavior (EB)

- 各 step の遷移は **その step 自身の verdict** でのみ決まる（Issue 本文 § 目的）
- exec 系 step の verdict の正本は stdout と `KAJI_VERDICT_PATH`。根拠:
  `docs/dev/skill-authoring.md` § exec_script **出力契約**「verdict ブロックを stdout に出力する責務は
  script 側にある」、`docs/dev/workflow-authoring.md` § exec-step「script は `KAJI_VERDICT_PATH` に
  `verdict.yaml` を書く artifact-primary 経路で完了判定できる」。exec 系 step の
  verdict の正本は stdout / artifact である（`kaji_harness/scripts/review_poll_entry.py` は stdout へ verdict を emit、
  `baseline_precheck.py` は `_emit_verdict` で stdout / `KAJI_VERDICT_PATH` へ出す）。なお baseline-precheck は
  `_post_comment` で証跡コメントを投稿するが、`_format_comment` の本文に verdict block は含まれない。
  dispatch 自体は custom exec script のコメント投稿を禁止していない
- 上記シナリオでは `review-poll` の verdict は `RETRY`（→ `pr-fix`）となり、書き戻される
  `verdict.yaml` も `RETRY` であること（Issue 完了条件 必須 1〜3）

## 再現手順（steps-to-reproduce）

1. 前提: Issue に「前 step の作業報告コメント」が 1 件ある
   - 本文末尾 `---VERDICT--- status: PASS ... ---END_VERDICT---`、1 行目に `kaji-verdict` marker 無し
   - `created_at = 2026-06-04T12:00:00Z`
2. 現 step は exec step（`dispatch=exec`、`on: {PASS, RETRY, ...}`）。`attempt_started_at = 2026-06-04T12:00:00.500000+00:00`
   （前コメントと同一秒）。`verdict.yaml` は書かず、stdout に `status: RETRY` の block のみを出す
3. `_StepExecutor._resolve_step_verdict` を呼ぶ
4. 観測（OB）: `resolve_verdict` は `("PASS", "comment")` を返し、`verdict.yaml` に `PASS` が書き戻される

Small テストでは `attempt_started_at` と comment `created_at` を固定値で与えるため決定的に再現する。

## 根本原因（Root Cause）

3 つの条件の重なり（Issue 本文 § 根本原因をコードで確認済み）。

1. **step を絞り込まない**: `resolve_verdict` の comment fallback（`kaji_harness/verdict.py` の
   `if comment_loader is not None:` 節）は Issue の全コメントを `_is_current_comment` の時刻条件だけで
   絞り、`parse_verdict_block` は本文末尾の block を step 無関係に採用する。コメントがどの step の
   ものかを判定する情報（ADR 008 の `kaji-verdict: step=` marker）を見ていない
2. **時刻下限が秒単位**: `_is_current_comment` は `attempt_started_at.replace(microsecond=0)` を下限にする
   （#220 PR review 対応 `af42840` で導入。provider の `created_at` が秒精度のため、同一秒の自 step
   コメントを取りこぼさないための措置）。この結果、**前 step が同一秒に投稿したコメント**も
   「現 attempt 由来」と判定される。秒精度の timestamp では「同一秒の自 step コメント」と
   「同一秒の前 step コメント」を時刻だけでは原理的に区別できない
3. **exec 系 step にも comment fallback を適用**: `runner.py` `_resolve_step_verdict` は dispatch 種別
   （`settings.is_script_like`）を formatter の有無にだけ使い、`comment_loader` は常に
   `lambda: self.provider.view_issue(...).comments` を渡している。exec 系 step の
   verdict 契約は stdout / `KAJI_VERDICT_PATH` であり（コメントを投稿する script でも、そのコメントは
   verdict の正本ではない。例: baseline-precheck の証跡コメントは verdict block を含まない）、comment 経路は
   exec 系 step の verdict 解決に寄与しない。それにもかかわらず comment が stdout より優先され、他 step の
   作業報告コメントが採用される

**いつから**: comment fallback と全 dispatch 共通の `comment_loader` は #220（`f011d19` / `af42840`、
v0.12.0）で導入。exec step（#205）はその後に追加され同じ経路を継承した。v0.21.0（`a03c53b`）および
現 `main`（`8c34347`）まで未修正。

**同根の他の壊れ箇所の調査**:

| 箇所 | 同根か | 結論 |
|------|--------|------|
| `runner._resolve_step_verdict`（exec / exec_script） | 同根（原因 3） | 本 Issue で修正 |
| `runner._resolve_step_verdict`（agent） | 同根（原因 1・2）。agent が `verdict.yaml` を書かなかった場合に、直前 step の同一秒コメントを採用しうる | marker が別 step を指すコメントの除外で本 Issue 修正。marker 無しコメントの残余リスクは § 方針 / 検討候補 2 |
| `kaji issue resolve-verdict`（`commands/issue.py` `_resolve_marker`） | 非該当 | marker の `step=` で既に step を絞っている（`marker.step != step` を照合） |
| `issue-design` Step 1.6 の BACK 再入検出 | 非該当 | 1 行目 marker の status で判定しており、時刻・step 無関係の block 採用はしない |
| `resolve_verdict` の呼び出し元 | — | `runner.py` の 1 箇所のみ（`grep -rn "resolve_verdict(" kaji_harness`）。他経路への波及なし |

## インターフェース

公開 CLI・workflow YAML・`verdict.yaml` の schema・run.log event schema は **不変**。変更は harness 内部 API のみ。

### 変更前 / 変更後

`kaji_harness/verdict.py`

```python
# 変更前
def resolve_verdict(
    *,
    attempt_dir: Path,
    full_output: str,
    valid_statuses: set[str],
    attempt_started_at: datetime,
    comment_loader: Callable[[], Sequence[CommentLike]] | None,
    ai_formatter: Callable[[str], str] | None = None,
    max_retries: int = 2,
) -> tuple[Verdict, str, list[ControlCharFinding]]: ...

# 変更後: 必須 keyword 引数 step_id を追加
def resolve_verdict(
    *,
    attempt_dir: Path,
    full_output: str,
    valid_statuses: set[str],
    attempt_started_at: datetime,
    step_id: str,
    comment_loader: Callable[[], Sequence[CommentLike]] | None,
    ai_formatter: Callable[[str], str] | None = None,
    max_retries: int = 2,
) -> tuple[Verdict, str, list[ControlCharFinding]]: ...
```

- `step_id`: 現在解決中の workflow step id。comment 候補のうち 1 行目の `kaji-verdict` marker が
  **別 step を指す** ものを除外するために使う
- `comment_loader=None` の既存意味（comment fallback を行わない）は不変。runner が exec 系 step で
  `None` を渡すことで fallback を無効化する

`kaji_harness/runner.py` `_StepExecutor._resolve_step_verdict`

| 項目 | 変更前 | 変更後 |
|------|--------|--------|
| `comment_loader`（agent） | `lambda: provider.view_issue(...).comments` | 同左（不変） |
| `comment_loader`（`exec` / `exec_script`） | 同上（provider を呼ぶ） | `None`（provider を呼ばない） |
| `step_id` | 渡さない | `step.id` を渡す |
| 非 artifact 時の `verdict.yaml` 書き戻し | 採用 verdict を保存 | 不変（採用 verdict が正しくなる結果、RETRY が保存される） |

### 後方互換性

- **agent step・marker 無しコメント**: 従来どおり候補（Issue 完了条件 必須 4「後方互換を維持」）
- **agent step・自 step marker 付きコメント**: 従来どおり候補
- **#220 の同一秒許容**: `_is_current_comment` は変更しない。`test_same_second_comment_adopted` を維持
- **exec 系 step**: comment 由来 verdict は採用されなくなる。exec 系の出力契約（stdout / `KAJI_VERDICT_PATH`）
  に従う script には影響しない。コメントにだけ verdict を残す独自 exec script が仮に存在した場合は
  `VerdictNotFound` で fail-loud になる（silent な誤遷移より fail-loud を優先する ADR 005 の fail-safe 方針と整合）
- `resolve_verdict` は内部 API（呼び出し元は runner 1 箇所 + テスト）。必須引数追加はテスト側の呼び出し更新のみ

### 破壊的変更の評価（ADR 008 決定 2）

- **BREAKING には該当しないと判定する（AI の仮定、検査先: review-design / `/release` Step 3）**。
  exec 系の出力契約は「verdict を stdout に出す責務は script 側」（`docs/dev/skill-authoring.md` §
  exec_script 出力契約）であり、Issue コメントだけに verdict を残す exec script は既に契約違反である。
  契約を満たす script の挙動は変わらない
- ただし `docs/dev/workflow-authoring.md` の exec-step 記述（「artifact → comment → stdout」）は
  観測可能な解決順を変えるため、release notes では Fixed として「exec / exec_script step は comment
  fallback を行わなくなった」ことを明記する。影響判定方法は「custom exec script が stdout にも
  `KAJI_VERDICT_PATH` にも verdict を出さず、Issue コメントだけに出しているか」を確認すること。
  CHANGELOG は `/release` skill が生成する運用のため本 Issue では更新しない
- agent step の別 step marker 除外は、他 step の verdict を誤採用しなくなる方向の変更で、
  自 step の verdict 解決（artifact / 自 step コメント / marker 無しコメント / stdout）は維持される

### 使用例

```python
# runner 側（疑似コード）
verdict, source, findings = resolve_verdict(
    attempt_dir=attempt_dir,
    full_output=result.full_output,
    valid_statuses=set(step.on.keys()),
    attempt_started_at=attempt_started_at,
    step_id=step.id,
    comment_loader=(
        None
        if settings.is_script_like  # exec / exec_script: stdout と KAJI_VERDICT_PATH のみが正本
        else lambda: self.provider.view_issue(self.run_ctx.canonical_id).comments
    ),
    ai_formatter=formatter,
)
```

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/verdict.py` | `resolve_verdict` に `step_id` 追加、comment 候補の step scoping、docstring 更新 |
| `kaji_harness/runner.py` | `_resolve_step_verdict` で script-like なら `comment_loader=None`、`step_id=step.id` を渡す |
| `tests/test_verdict_artifact.py` | 既存 `resolve_verdict` 呼び出しへ `step_id` 追加、marker scoping の Small テスト追加 |
| `tests/test_verdict_step_scoping.py`（新規） | `_resolve_step_verdict` レベルの回帰テスト（exec / exec_script / agent） |
| `tests/test_exec_step_dispatch.py` | runner 結合の Medium テスト追加 |
| docs（§ 影響ドキュメント） | 解決順の記述更新 |

## 制約・前提条件

- comment の `created_at` は provider 共通で秒精度（`%Y-%m-%dT%H:%M:%SZ`）。時刻だけで同一秒の
  自 step / 前 step コメントは区別できない
- `kaji-verdict` marker は CLI（`kaji issue comment --verdict-step/--verdict-status`）が body 1 行目に
  決定的に付与する（`kaji_harness/providers/markers.py`、ADR 008）。github / local で同一形式
- marker の `step=` 値は skill 側が `--verdict-step` に与える値であり、workflow step id と一致させるのは
  運用規約。official workflow（`dev.yaml` / `docs.yaml` / `incident.yaml` / `local/*.yaml`）では
  marker を出す skill の `--verdict-step` 値と workflow step id が一致することを確認済み
  （`design` / `review-design` / `implement` / `review-code` / `final-check` / `review` /
  `investigate` / `fix` / `verify` / `report`、small-change 系は `[step_id]` コンテキスト変数）
- `verdict.py` は provider 実装に依存しない（`CommentLike` Protocol）。marker 解析は既に依存している
  `kaji_harness.providers.markers`（provider 中立モジュール）の `parse_kaji_verdict_marker` を用いる
- 外部入力は既存の Pydantic / marker 文法検証を通す。新規の外部入力は無い

## 方針

### 1. exec 系 step では comment fallback を行わない（原因 3 への対処）

`runner._resolve_step_verdict` で `settings.is_script_like`（= `dispatch in ("exec", "exec_script")`、
`_resolve_settings` で算出済み）のとき `comment_loader=None` を渡す。判定は既存の `is_script_like`
を再利用し、dispatch 種別の判定ロジックを新設しない。`resolve_verdict` 側に dispatch 種別の知識は
持ち込まない（「comment を見るか」は呼び出し側が `comment_loader` の有無で表現する既存の責務分担を維持）。

これにより exec 系 step の verdict 候補は `verdict.yaml`（`KAJI_VERDICT_PATH`）と stdout のみになり、
OB のシナリオは原因 1・2 に関係なく防がれる。

### 2. agent step では別 step を指す marker のコメントを除外（原因 1 への対処）

`resolve_verdict` の comment fallback で、時刻フィルタ（`_is_current_comment`）を通ったコメントに
さらに step scoping を適用する。

```python
def _comment_step_scope_allows(comment: CommentLike, step_id: str) -> bool:
    first_line = comment.body.splitlines()[0] if comment.body.splitlines() else ""
    if not first_line.startswith("<!-- kaji-verdict: "):
        return True                      # marker 無し → 後方互換で候補に残す
    marker = parse_kaji_verdict_marker(first_line)
    if marker is None:
        return False                     # marker 風だが不正 → step 不明のため除外（fail-closed）
    return marker.step == step_id        # 自 step → 残す / 別 step → 除外

current = [
    c for c in comments
    if _is_current_comment(c, attempt_started_at) and _comment_step_scope_allows(c, step_id)
]
# 以降は従来通り newest-first で末尾 block を採用
```

- フィルタは newest-first 走査の **前** に適用する。最新コメントが別 step のものでも、それより古い
  （現 attempt 内の）自 step コメントや marker 無しコメントは引き続き候補になる
- 1 行目の取り出し方は `commands/issue.py` `_resolve_marker` と同じ（`splitlines()[0]`）にそろえる
- 除外の結果 comment 候補が無くなれば、従来どおり stdout（→ AI formatter）へ fallthrough する。
  agent の prompt は stdout にも同じ block を出す契約（`docs/dev/skill-authoring.md` § verdict 出力規約）
  のため、通常は stdout で解決できる

### 3. 書き戻し（`verdict.yaml`）

ロジックは変更しない。方針 1 / 2 により採用される verdict が正しくなるため、OB シナリオでは
`RETRY` が書き戻される（完了条件 必須 3）。

### 4. 検討候補の採否

| 候補 | 採否 | 理由 |
|------|------|------|
| 1. `verdict.yaml` 書き戻し時に `source` を記録 | **見送り** | (a) 方針 1 / 2 で「他 step の verdict が永続化される」経路自体を塞ぐ。(b) 解決経路は既に `run.log` の `verdict_source` event に attempt 単位で記録されている（`docs/reference/python/logging.md` § `verdict_source`）。(c) `verdict.yaml` は skill / script が書く pure YAML 契約（ADR 005、`docs/dev/skill-authoring.md` § verdict 出力規約）で、`kaji issue resolve-verdict`（#426）も読む永続 artifact。key 追加は永続化ファイル形式の変更にあたり、本 Issue の必須範囲外。必要なら別 Issue で consumer 影響を含めて扱う |
| 2a. marker 無しコメントを agent step の候補から外す | **見送り** | Issue 完了条件 必須 4 が「marker の無いコメントは従来通り候補に残し、後方互換を維持する」と明示しているため、採用すると必須条件に反する |
| 2b. 「前 step の終了時刻より後」等の追加時刻境界 | **見送り** | comment `created_at` は秒精度。OB では前 step 終了 `09:33:02.283` / 前 step コメント `09:33:02Z` / 現 step 開始 `09:33:02.944` が同一秒に収まる。前 step 終了時刻を秒に切り捨てて `>` とすれば同一秒の自 step コメントも落ち #220 の同一秒許容（必須 5）を回帰させ、`>=` とすれば前 step コメントを区別できない。秒精度の下では追加の時刻境界で両立できない |

**残余リスク（記録）**: agent step A（marker 無しの作業報告コメント）→ agent step B が連続し、B が
`verdict.yaml` を書かず、A のコメントが B 開始と同一秒、かつ B が自 step コメントを投稿しない場合、
A のコメントは依然として候補になりうる。agent step は `verdict.yaml`（artifact primary）を書く契約で
あり comment fallback は二次経路であること、producer skill の marker 付与が進むほど除外対象が増える
ことから、本 Issue では上記 2b の理由により時刻境界の追加は行わない。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| exec 系 step の comment fallback | `exec` / `exec_script` では行わない。候補は `verdict.yaml` と stdout のみ | Issue 本文 § 完了条件 必須 1（`issue-fix-ready` で § 提案の方向「exec / exec_script step では comment fallback を行わない」から追記され、`issue-review-ready` PASS で着手ゲート通過）。exec 系出力契約 `docs/dev/skill-authoring.md` § exec_script 出力契約 | runner で既存の `settings.is_script_like` を判定に再利用し `comment_loader=None` を渡す。`resolve_verdict` に dispatch 知識を入れない |
| agent step の別 step marker 除外 | 1 行目 marker が別 step を指すコメントを除外。自 step marker・marker 無しは候補に残す | Issue 本文 § 完了条件 必須 4（同上の経緯） | 時刻フィルタ後・newest-first 走査前に適用。`resolve_verdict` に必須 keyword `step_id` を追加 |
| 不正な marker 風 1 行目の扱い | `<!-- kaji-verdict: ` で始まるが `parse_kaji_verdict_marker` が `None` を返すコメントは除外 | AI の仮定。根拠: step を判定できない以上、他 step の verdict を採用するより stdout へ fallthrough する方が安全（ADR 005「false verdict より解決失敗を優先」、`parse_kaji_verdict_marker` の fail-closed 設計）。marker は CLI が検証して付与するため通常発生しない。検査先: review-design / review-code | 1 行目を `commands/issue.py` と同じ方法で取り出し、prefix 一致 + parse 失敗で除外 |
| `step_id` を必須引数にするか | 必須 keyword 引数 | AI の仮定。根拠: 任意（既定 `None` = scoping 無し）にすると将来の呼び出し元が scoping を黙って失う。呼び出し元は runner 1 箇所のみで変更コストが小さい。検査先: review-code | テストの既存呼び出しに `step_id` を追加 |
| marker step と workflow step id の照合 | 完全一致で比較 | AI の仮定。根拠: official workflow で marker 値と step id が一致することを確認（§ 制約）。custom workflow で不一致の場合、自 step コメントが除外されても stdout fallback で解決でき、誤遷移ではなく経路変更にとどまる（two-way door）。検査先: review-design / review-code | 不一致時の挙動を § 方針 2 に記述 |
| #220 同一秒許容の維持 | `_is_current_comment` は変更しない | Issue 本文 § 完了条件 必須 5 | 既存 `test_same_second_comment_adopted` を `step_id` 追加のみで維持 |
| 検討候補 1（source 記録） | 見送り | Issue 本文 § 完了条件 検討候補「設計で採否を判断し、見送る場合は理由を設計書に残す」 | § 方針 4 に理由を記録 |
| 検討候補 2（marker 無し除外 / 時刻境界） | 見送り | 同上。2a は必須 4 の後方互換要求と矛盾するため不採用 | § 方針 4 に理由と残余リスクを記録 |
| BREAKING 該当性 | BREAKING ではなく Fixed として release notes に明記 | AI の仮定。根拠: exec_script 出力契約（stdout 責務）を満たす script の挙動は不変、ADR 008 決定 2。検査先: review-design / `/release` Step 3 | § 後方互換性「破壊的変更の評価」に影響判定方法を記述 |
| 公開契約への影響 | CLI / workflow YAML / `verdict.yaml` schema / run.log schema は不変 | AI の確認結果（変更スコープが `verdict.py` / `runner.py` 内部に閉じる）。検査先: review-design | — |

one-way door の未決は無い。変更は harness 内部の解決ロジックに閉じ、永続化 schema・公開 CLI・
workflow YAML の契約を変えない。exec 系 step で comment 由来 verdict が採用されなくなる挙動変更は、
Issue 完了条件 必須 1 で決定済み。

## テスト戦略

### 変更タイプ

- 実行時コード変更（verdict 解決ロジック）。bug 修正のため **修正前に Red になる再現テスト** を必須とする

### 実行時コード変更の場合

#### Small テスト

`tests/test_verdict_step_scoping.py`（新規、`@pytest.mark.small`）— runner の判断層を直接駆動する回帰テスト（Issue 必須 2）。
ファイル I/O を持たない、モックで完結する構成にする（`docs/dev/testing-convention.md` § 判定基準）。

`_StepExecutor._resolve_step_verdict(step, settings, result, attempt_dir, verdict_path, attempt_started_at)` は
`attempt_started_at` を引数で受けるため、同一秒の条件を固定値で決定的に作れる。I/O 境界は次のように置き換える。

- provider: `view_issue` が固定コメント列を返す fake。呼び出し回数を記録する
- `RunLogger`: `MagicMock`。`log_verdict_source` の呼び出し引数を検査する（run.log には書かない）
- 書き戻し: `kaji_harness.runner.write_verdict_yaml` を `patch` する。保存に渡された `Verdict` を検査する（ファイルは作らない）
- artifact 不在: `attempt_dir` / `verdict_path` には、作成しない固定の存在しないパス（例 `Path("/nonexistent/attempt-001")`）を渡す。
  `resolve_verdict` の `exists()` 判定が False になるだけで、ファイルの作成・読み込みは起きない。
  `tmp_path` は使わない

- **再現テスト（Red → Green）**: `kind ∈ {"exec", "exec_script"}`（parametrize）、`is_script_like=True`。
  Issue に前 step の作業報告コメントを置く（`status: PASS` block、marker 無し、`created_at=2026-06-04T12:00:00Z`）。
  `attempt_started_at=2026-06-04T12:00:00.500000+00:00`、`result.full_output` は `status: RETRY` block。
  検証観点:
  - 返り値が `verdict.status == "RETRY"` であること
  - `logger.log_verdict_source` が `source="stdout"` で呼ばれる（`comment` ではない）こと
  - patch した `write_verdict_yaml` に渡る `Verdict.status` が `RETRY` であること（前 step の PASS を保存しようとしない）
  - fake provider の `view_issue` が呼ばれないこと（exec 系は comment を見ない）
  - 修正前のコードでは `PASS` / `source=comment` / 保存対象 PASS となって FAIL することを、implement 時に確認・記録する（必須 2）
- **agent step は comment fallback を維持**: `kind="agent"` で、自 step marker 付きのコメント（現 attempt 内）があり、
  stdout に verdict が無い場合は `source=comment` で採用されること（過剰に無効化していないことの保護）。
  `create_verdict_formatter` は patch で無害化する

`tests/test_verdict_artifact.py`（既存、`@pytest.mark.small`）— `resolve_verdict` の step scoping。

- 本 Issue で追加する scoping テストは `tmp_path` を使わない。artifact 不在は、作成しない存在しないパスを
  `attempt_dir` に渡して表現し、モック完結の Small とする（既存テストの再分類は本 Issue の範囲外）
- 既存の全 `resolve_verdict` 呼び出しに `step_id` を追加し、既存観点（artifact primary / 同一秒採用 /
  古いコメント除外 / loader 失敗 fallthrough / parse 不能 `created_at` 除外 / control char findings）を回帰させない
  （`test_same_second_comment_adopted` は必須 5 の回帰ガード）
- **別 step marker の除外**: 現 attempt 内・1 行目 `<!-- kaji-verdict: step=pr status=PASS -->` の PASS コメント
  + stdout RETRY、`step_id="review-poll"` → `RETRY` / `source="stdout"`（必須 4）
- **別 step marker のみ・stdout 無し**: `VerdictNotFound`（他 step verdict で穴埋めしない）
- **自 step marker は採用**: `step=review-code` marker、`step_id="review-code"` → `source="comment"`
- **marker 無しは採用（後方互換）**: marker 無しコメント → `source="comment"`（必須 4 の後方互換側）
- **不正 marker は除外**: 1 行目 `<!-- kaji-verdict: step=Bad status=PASS -->`（文法外）→ 候補外で stdout へ
- **走査順との関係**: 最新が別 step marker（PASS）、それより古い現 attempt 内の自 step marker（RETRY）
  → 自 step の `RETRY` が採用される（フィルタが newest-first 走査の前に効く）
- **2 行目以降の marker 引用は判定に使わない**: 1 行目は通常本文、2 行目以降に別 step marker を引用
  したコメントは marker 無し扱いで候補に残る

#### Medium テスト

実ファイルへの保存（`verdict.yaml`）と run.log を確認する観点は Medium に置く（Issue 必須 3）。

- **判断層 + 実 I/O**（`tests/test_verdict_step_scoping.py` 内の `@pytest.mark.medium` クラス）: Small の再現テストと
  同じ固定入力（同一秒の前 step marker 無し PASS コメント、stdout RETRY、exec / exec_script を parametrize）で、
  `RunLogger` は `tmp_path` 配下の実体、`attempt_dir` / `verdict_path` は `tmp_path` 配下の実パス、`write_verdict_yaml` は
  patch しない構成で `_resolve_step_verdict` を駆動する
  - `attempt_dir/verdict.yaml` を `load_verdict_yaml` で読むと `RETRY` であること（前 step の PASS が永続化されない）
  - run.log の `verdict_source` event が `source="stdout"` であること

`tests/test_exec_step_dispatch.py` `TestRunnerExecDispatch`（既存クラス、`@pytest.mark.medium`）に追加。
`WorkflowRunner.run()` → `_StepExecutor` → `resolve_verdict` → `verdict.yaml` 書き戻し → 遷移までの結合を
既存パターン（`execute_exec` を patch、artifacts を `tmp_path` に実書き込み）で検証する。

- exec step（`on: {PASS: end, RETRY: end}`）、provider の `view_issue` を patch して
  `created_at` が十分未来（例 `2099-01-01T00:00:00Z`、時刻フィルタを必ず通過する決定的な値）の
  PASS コメント（marker 無し）を返す。`execute_exec` は stdout に `RETRY` を返す
  - run.log の `verdict_source` が `stdout`、`step_end` の verdict status が `RETRY`
  - `steps/run/attempt-001/verdict.yaml` が `RETRY`
  - `view_issue` は verdict 解決のために呼ばれない（context 解決用の呼び出しと区別するため、
    patch は verdict 解決経路のみに効く形で実装時に選ぶ。区別が困難なら verdict の status / source で判定する）

#### Large テスト

- 追加しない。理由:
  - 不具合は harness 内部の判断（`comment_loader` の選択と候補フィルタ）にあり、外部 API の挙動・
    応答形式には依存しない。provider 境界は `Comment(body, created_at)` 列であり、Small / Medium で
    同じ境界値を決定的に与えられる
  - 実 GitHub を使う Large では「前 step コメントと現 step 開始が同一秒」という発生条件を決定的に
    作れず、回帰検出情報が Small / Medium より増えない（むしろ flaky になる）
  - 既存の `tests/test_exec_step_e2e_large_local.py`（exec step の実 subprocess E2E）は `make check` で
    引き続き実行され、exec 経路全体の非回帰を担保する
  - 実環境での確認は Issue § ワークフロー完了後の確認項目（kamo2 での `review-poll` RETRY → `pr-fix`）で扱う

### 品質ゲート

- `source .venv/bin/activate && make check`（ruff / format check / mypy / pytest 全件）が PASS（必須 6）

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/005-artifact-primary-verdict.md | あり | 決定節に「comment fallback は当該 attempt の…コメントのみ対象」とある。exec 系 step では comment fallback を行わないこと、agent step は別 step marker を除外することを「追記（Issue #449）」として記録する。新 ADR は不要（ADR 005 の解決順・ADR 008 の marker 契約の範囲内の精緻化） |
| docs/ARCHITECTURE.md | あり | § Verdict 判定機構「verdict source 解決順」の図と「comment fallback の attempt scoping」に、step scoping（別 step marker 除外）と exec 系 step の comment fallback 無効化を追記 |
| docs/dev/workflow-authoring.md | あり | exec-step 節「verdict 解決: artifact → comment → stdout の順」を「artifact → stdout（comment fallback は行わない）」へ修正 |
| docs/dev/skill-authoring.md | あり | § exec_script 出力契約に comment fallback 非適用を追記。§ verdict 出力規約の解決順説明に、agent step では別 step を指す marker のコメントを除外する旨を追記 |
| docs/dev/shared_skill_rules.md | あり | 3 経路と解決順を要約している段落に、exec 系は comment を見ない旨を短く補足（詳細は skill-authoring.md へ委譲する現行構成を維持） |
| docs/reference/python/logging.md | なし | `verdict_source` の値集合・フィールドは不変 |
| docs/cli-guides/ | なし | CLI 仕様の変更なし |
| docs/reference/configuration.md | なし | 設定 key の変更なし |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| CHANGELOG.md | なし（本 Issue では更新しない） | release 時に `/release` skill が扱う運用 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #449 本文 | https://github.com/apokamo/kaji/issues/449 | § 現象の run.log 引用（`source: comment` で `pr` step の PASS を採用）、§ 根本原因 3 条件、§ 完了条件（必須 6 項目・検討候補 2 項目） |
| verdict 解決実装 | `kaji_harness/verdict.py`（`resolve_verdict` / `_is_current_comment` / `parse_verdict_block`） | 時刻フィルタのみで comment を絞り、`replace(microsecond=0)` で秒切り捨て。step 情報を見ずに末尾 block を採用し、stdout より先に返す |
| runner の呼び出し | `kaji_harness/runner.py`（`_dispatch_kind` / `_resolve_settings` / `_resolve_step_verdict`） | `is_script_like` は formatter 無効化にのみ使われ、`comment_loader` は dispatch 種別によらず provider を呼ぶ。`source != "artifact"` で `write_verdict_yaml` |
| verdict marker 契約 | `kaji_harness/providers/markers.py`（`parse_kaji_verdict_marker` / `_VERDICT_MARKER_RE`） | body 1 行目の `<!-- kaji-verdict: step=<step> status=<STATUS>[ meta] -->`。完全一致しない行は `None`（fail-closed） |
| marker の step 照合の先例 | `kaji_harness/commands/issue.py` `_resolve_marker` | 1 行目を `splitlines()[0]` で取り出し、`marker.step != step` を照合する既存パターン |
| ADR 005 | `docs/adr/005-artifact-primary-verdict.md` | artifact → comment → stdout の解決順、`attempt_started_at` 下限、「false verdict より解決失敗」方針 |
| ADR 008 | `docs/adr/008-no-backward-compat-layer.md` | 決定 2「破壊的変更は CHANGELOG / Release notes の BREAKING で明示」、決定 3「スキルを跨ぐ契約は CLI / harness 層（コード）に置く」（`markers.py` docstring が参照）。本設計の BREAKING 判定と marker 利用の根拠 |
| exec_script 出力契約 | `docs/dev/skill-authoring.md` § exec_script「出力契約」 | 「verdict ブロックを stdout に出力する責務は script 側にある」「AI formatter fallback は呼ばれない」 |
| exec-step 仕様 | `docs/dev/workflow-authoring.md` § exec-step | `KAJI_VERDICT_PATH` を注入し artifact-primary 経路で完了判定。現行記述「verdict 解決: artifact → comment → stdout」が本修正で変わる |
| #220 同一秒テスト | `tests/test_verdict_artifact.py::TestResolveVerdict::test_same_second_comment_adopted` | dispatch `12:00:00.5` / comment `12:00:00Z` の同一秒コメントを採用する既存回帰テスト（必須 5 のガード） |
| exec 系 script の出力実体 | `kaji_harness/scripts/review_poll_entry.py`、`kaji_harness/scripts/baseline_precheck.py` | review-poll は stdout に `---VERDICT---` を emit、baseline-precheck は `_emit_verdict` で stdout / `KAJI_VERDICT_PATH` へ出す。baseline-precheck は `_post_comment` で証跡コメントを投稿するが、`_format_comment` の本文に verdict block は無い |
| verdict_source event | `docs/reference/python/logging.md` § `verdict_source` | 解決経路 `artifact` / `comment` / `stdout` を attempt 単位で run.log に記録済み（検討候補 1 見送りの根拠） |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L 判定基準、bug の再現テスト要件、Large 省略の正当化要件 |
