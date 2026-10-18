# ADR 012: `verdict.yaml` に判定時刻を追加しない（時刻は `result.json` の `ended_at` に委ねる）

## ステータス

提案（2026-10-18、Issue #440）— 本 ADR は検討・文書化の成果物であり、この文書を含む PR を
人間が merge した時点で承認とみなす。merge 前は本節の「提案」が示すとおり、以下の推奨・
仕様案はいずれも人間決定ではない。

## コンテキスト

Issue #426（`kaji issue resolve-verdict` の artifact fallback）の grill-me で、artifact から
解決した判定の新旧比較に使う時刻が論点になった。#426 は `result.json` の `ended_at` を使う方針で
完了し、`verdict.yaml` 自体への時刻追加は本 Issue（#440）へ分離された
（[#426 provenance](https://github.com/apokamo/kaji/issues/426#issuecomment-5914616383) 項目 5）。

### 現行の時刻の生成・保存・利用経路

| 時刻 | 生成主体 / 時計 | 生成タイミング | 保存先 | 一次情報 |
|------|----------------|----------------|--------|----------|
| `attempt_started_at` | harness / ローカル `datetime.now(UTC)` | agent step は prompt 生成時（`AttemptDeadline.start`）、exec / exec_script は起動直前 | in-memory。agent step は prompt の `attempt_started_at_utc`、両者とも `result.json` の `started_at` | `kaji_harness/deadline.py` `AttemptDeadline.start` / `kaji_harness/runner.py` `_dispatch` |
| comment `created_at` | provider（GitHub サーバ時計）。秒精度 `%Y-%m-%dT%H:%M:%SZ` | 作業報告コメント投稿時 | provider 側 | `kaji_harness/providers/github.py` / `kaji_harness/verdict.py` `_parse_comment_timestamp` |
| `result.json` `ended_at` | harness / ローカル `datetime.now(UTC).isoformat()` | 正常経路は verdict 解決（必要なら `verdict.yaml` 正規化保存）の**後**。異常経路は例外捕捉時 | `attempt-NNN/result.json`（best-effort。`OSError` は WARN で握る） | `kaji_harness/runner.py` `_record_attempt_end` / `_record_dispatch_failure`、`kaji_harness/result.py` `AttemptResult` |
| `verdict.yaml` | 時刻を持たない | — | — | `kaji_harness/verdict.py` `write_verdict_yaml` |

`verdict.yaml` の扱いは次のとおり。

- **書き手**: agent（prompt の出力要件で `verdict_path` へ pure YAML を保存。`kaji_harness/prompt.py`）、
  exec_script（`KAJI_VERDICT_PATH`）、harness（`source != "artifact"` または制御文字 sanitize が
  あった場合に `write_verdict_yaml` で正規化保存。`runner.py` `_resolve_step_verdict`）。
- **形式**: `write_verdict_yaml` は `status` / `reason` / `evidence` / `suggestion` の 4 key のみを書く。
  `_parse_yaml_fields` も 4 key のみを読み、未知 key は黙って無視する。したがって agent が追加 key を
  書いても、harness が正規化保存すると消える。
- **interactive terminal runner**: `verdict.yaml` の出現が完了トリガであり、agent は外部副作用の後に
  最後に保存する（ADR 005 / ADR 007）。

時刻の利用者は次のとおり。

- `resolve_verdict` の comment fallback: `created_at >= attempt_started_at`（下限は秒切り捨て）で
  dispatch 開始秒より前のコメントを除外する（ADR 005）。異なる時計（GitHub とローカル）を比較している。
  秒切り捨てのため、開始と同じ秒に投稿された前 attempt のコメントは時刻条件では除外できない
  （§ 時計のずれと比較の限界）。
- `kaji issue resolve-verdict`: marker 解決は `created_at`、artifact 解決は同じ attempt の
  `result.json` の `ended_at` を出力する。`ended_at` は記録文字列のまま出し、欠落・parse 不能・
  timezone なしなら `null`。mtime や現在時刻で補完しない。artifact 出力は `created_at` を含まない
  （`kaji_harness/artifact_verdict.py` `evaluate_result` / `ResolvedArtifactVerdict.as_json`）。
- consumer（`docs/dev/shared_skill_rules.md` § resolve-verdict consumer、
  `.claude/skills/issue-small-change-execute/SKILL.md` / `issue-small-change-review/SKILL.md`）:
  marker は `created_at`、artifact は `ended_at` を ISO 8601 の時刻として比較し、`null` で比較が
  必要なら推測せず停止する。

保持と欠落の経路は次のとおり。

- 復旧（auto recovery）は parent run の attempt をコピーしない。`recovery-chain.json` の
  `parent_run_id` を辿って parent run の attempt をその場で読む（`artifact_verdict.py`
  `resolve_artifact_verdict`）。harness に `verdict.yaml` を単体でコピー・移送するコードは無い。
- `result.json` が無い attempt: #222 以前の旧 run、`result.json` 書き出し失敗、割込み
  （`KeyboardInterrupt`）中の進行中 attempt（割込み時は `result.json` を作らない契約。
  `kaji_harness/recovery/snapshot.py` `_orphan_pane_id`）。artifact fallback はこの場合も
  `ended_at = null` で採用する（`docs/ARCHITECTURE.md` § `result.json` の欠落許容）。

### 区別すべき 3 つの時刻

- **判定時刻**: agent が判定を確定した時刻。harness からは観測できない。
- **保存時刻**: `verdict.yaml` を書いた時刻。ファイル mtime はコピー・checkout・復元で変わり、
  古い判定が新しく見えるため使えない（#426 で mtime 補完を禁止済み）。
- **attempt 終了時刻**: `result.json` の `ended_at`。正常経路では verdict 解決後に harness が記録する
  ため、同じ attempt の作業報告コメント投稿・`verdict.yaml` 保存より後になる（時計のずれを除く）。

## 決定（提案）

**`verdict.yaml` の schema は変更せず、時刻を追加しない。** artifact 由来の判定の新旧比較には、
引き続き同じ attempt の `result.json` の `ended_at` を使う。

採用する時刻契約（現状の明文化。新たな実装は伴わない）:

| 項目 | 規則 |
|------|------|
| 時刻の意味 | attempt 終了時刻。判定時刻・保存時刻ではない。正常経路では判定の上界（判定はこれより前に確定している） |
| 生成主体 | harness のみ。agent / script は時刻を書かない |
| 形式 | `datetime.now(UTC).isoformat()`（tz-aware、マイクロ秒、`+00:00` 表記）。consumer は文字列でなく ISO 8601 の時刻として比較する |
| 保持 | `result.json` に attempt 単位で保存。harness は attempt dir を移動・コピーしないため、復旧・再読込で値は変わらない。mtime・現在時刻で補完しない |
| 旧 artifact | `result.json` 欠落・`ended_at` 欠落は時刻不明（`null`）。migration しない（ADR 005 / 006） |
| 不正値 | parse 不能・timezone なしは時刻不明（`null`）。status は返すが、新旧比較が必要な consumer は停止する |
| consumer 互換性 | 現行 CLI 出力（`created_at` / `ended_at`）と consumer 規則を変更しない。BREAKING なし |

### 推奨の根拠

- **新しい情報が得られない**: harness が `verdict.yaml` に時刻を書く場合、書けるのは verdict 解決時刻で
  あり、`ended_at` との差は同一処理内のミリ秒程度。agent / harness 以外に判定時刻を観測する主体は無い。
- **欠落ケースの救済は限定的**: `result.json` が無い経路ごとに、案 C（harness が verdict 解決時に
  `verdict.yaml` へ時刻を書く）が時刻を残せるかは次のとおり。

  | `result.json` が無い経路 | 案 C で時刻が残るか | 理由 |
  |---------------------------|--------------------|------|
  | 旧 run（#222 以前・案 C 導入前） | 残らない | 新 key も存在しない。migration しない |
  | 割込み（verdict 解決前。agent 実行中など） | 残らない | harness が解決処理に到達しない |
  | 割込み（verdict 解決後・`_record_attempt_end` 前） | 残る | 解決時に保存済み |
  | `result.json` 書き出しの `OSError`（解決後） | 残る | `_resolve_step_verdict` の保存が先行し、`_record_attempt_end` は `result.json` の `OSError` だけを WARN で握って継続する |

  つまり案 C は「解決後に `result.json` だけが残らない」経路を救える（doc-review で
  `write_result_json` のみを失敗させ `verdict.yaml` が残ることを確認済み）。一方、旧 run と解決前の
  割込みは救えず、後者は agent 生成時刻（案 B）でなければ時刻を残せない。案 A を推奨するのは、案 C が
  救う経路が (1) 解決直後のミリ秒単位の区間での割込み、(2) 直前に同じ attempt dir への
  `verdict.yaml` 書き出しが成功した直後の `result.json` 書き出し失敗、という発生頻度の低い経路に限られ、
  かつ救っても consumer は `ended_at` とほぼ同値の時刻を得るだけである一方、次の追加コストが恒常的に
  かかるためである: artifact 経路でも agent が書いた `verdict.yaml` を常に書き直す、reader / writer /
  `resolve-verdict` 出力 / consumer 規則に新 key と「どちらの時刻を使うか」の規則を追加する、旧 artifact
  （key 無し）の扱いを定める。`result.json` 書き出し失敗が実際に問題になった場合は、時刻追加より先に
  `result.json` 書き出しの失敗耐性（再試行など）を検討する方が、`status` / `synthetic` / `error` を含む
  終了情報全体を守れる。
- **agent 生成の時刻は信頼できない**: LLM は prompt の `attempt_started_at_utc` などを転記しうる。
  harness が `[attempt_started_at, 解決時刻]` の範囲検証を行っても、範囲内の誤値は検出できない。
  誤った時刻は「古い判定が新しく見える」方向にも働き、Issue の制約（復旧・コピー・再読込で古い判定を
  新しく見せない）を時計で担保できなくなる。
- **正規化と混線する**: comment / stdout 経路では harness が `verdict.yaml` を書き直すため、時刻の由来が
  経路により agent・コメント `created_at`・harness 解決時刻と変わり、同じ key の意味が一定しない。
  現行の `_parse_yaml_fields` は未知 key を捨てるため、追加には reader / writer / prompt / exec_script /
  docs の同時変更が必要になる。
- **コピーの論点は現状で発生しない**: `verdict.yaml` 単体を移送する経路が無いため、「verdict 単体で時刻を
  自己完結させる」便益は現時点では仮定上のものに留まる。

## 代替案と比較

| 案 | 内容 | 便益 | 制約・リスク | 評価 |
|----|------|------|--------------|------|
| A. 現状維持（推奨） | `verdict.yaml` は 4 key。時刻は `result.json` `ended_at` | 変更・BREAKING なし。生成主体が harness に一本化。#426 の consumer 規則と整合 | `result.json` 欠落時は時刻不明で consumer が停止する | 採用（提案） |
| B. agent が判定時刻を書く | prompt で `decided_at` 等の保存を要求 | 割込み等で `result.json` が無くても時刻が残る | 値を検証できない。stdout / comment 経路では harness が補う値の意味が別物になる。exec_script・下流カスタマイズ skill に BREAKING | 却下 |
| C. harness が解決時に `verdict.yaml` へ書く | `resolved_at` 等を正規化保存時に付与（artifact 経路も常に書き直す） | 時刻の生成主体が harness。`verdict.yaml` 単体で時刻が読める | `ended_at` とほぼ同値で新情報が無い。agent artifact を常に書き直す。救えるのは解決後の `result.json` 欠落（書き出し失敗・解決直後の割込み）のみで、旧 run と解決前の割込みは救えない | 現時点は不要。下記の条件で再検討 |
| D. harness が `result.json` に別 key で書く | `verdict_resolved_at` 等を追加 | 判定上界をより厳密に表せる | `ended_at` との差がミリ秒程度で consumer の判断を変えない | 不要 |

## 判断を変える条件

次のいずれかを観測したら再検討する。

- **`verdict.yaml` を attempt dir から切り離して移送・コピーする経路が生まれた場合**（別マシンへの
  artifact 移送、復旧時のコピー方式への変更など）。その場合は案 C（harness 生成）を第一候補とする。
- **`ended_at = null` による consumer 停止が運用上繰り返し問題になった場合**（割込み後の再開で
  手動判断が頻発する等）。先に `result.json` 欠落経路そのもの（割込み時の記録方針）を見直し、
  agent 生成時刻（案 B）は最後の手段とする。
- **時計の異なる時刻同士の比較（GitHub `created_at` とローカル `ended_at`）で誤順序が実際に起きた
  場合**。時刻追加ではなく、attempt 番号や marker による順序付けを検討する。

## 時計のずれと比較の限界

- `created_at`（GitHub サーバ時計・秒精度）と `ended_at`（kaji 実行ホストのローカル時計・マイクロ秒）は
  別の時計である。ホスト時計のずれ以内で近接した判定同士は順序を保証できない。workflow の step は
  逐次実行で通常は分単位以上離れるため現行規則で足りるが、厳密な全順序は提供しない。
- comment fallback の `attempt_started_at` 下限（ADR 005）も同じ限界を持つ。加えて下限は秒に
  切り捨てるため、dispatch 開始と同じ秒に投稿されたコメントは時刻だけでは前後を区別できない。
  `kaji-verdict` marker の step scoping（Issue #449）が補うのは、**有効な marker で別 step と識別できる
  コメント（および marker 風だが文法外のコメント）の除外のみ**である。`_comment_step_scope_allows` は
  step 名だけを比較するため、次の 2 つは識別できず、comment fallback で採用されうる（既知の限界。
  本 ADR は動作変更を扱わない）。
  - **同じ step の前 attempt** が dispatch 開始と同じ秒に投稿したコメント（retry / cycle で同一 step が
    直後に再 dispatch された場合）。doc-review で、開始 `05:00:00.900000+00:00`・前 attempt コメント
    `05:00:00Z`・同一 step の正常 marker・artifact / stdout なしの条件で、前 attempt のコメントが
    `source=comment` として採用されることを確認済み。
  - **marker 無しのコメント**（後方互換で候補に残る）が同じ秒に投稿された場合。
  - いずれも artifact（`verdict.yaml`）が存在すれば comment fallback 自体を見ないため、artifact-primary
    運用では顕在化しない。`verdict.yaml` に時刻を追加してもこの限界は解消しない（comment 経路の問題で
    あり、識別には attempt 単位の marker などが必要）。

## 影響

- コード・テスト・`verdict.yaml` / `result.json` 形式・CLI 出力・skill の変更は無い。
- `docs/ARCHITECTURE.md` § `result.json` に、`ended_at` が判定時刻ではないこと、`verdict.yaml` が時刻を
  持たないことと本 ADR への参照を追記する。
- 本提案が承認されれば実装 Issue は不要。

## 未決事項（実装前の人間判断）

本 ADR では確定しない。

- **本提案（案 A）の採否**。PR merge をもって承認とみなす。
- 案 A を却下して時刻を追加する場合のみ、以下を人間が決め、実装 Issue を別途起票する:
  - 生成主体（harness / agent）と保存先（`verdict.yaml` / `result.json`）
  - key 名と、artifact 経路でも harness が `verdict.yaml` を書き直すか
  - `resolve-verdict` の artifact 出力の時刻 key を `ended_at` から切り替えるか、併記するか
  - ADR 008 に従う BREAKING の範囲（prompt の出力要件・exec_script・下流カスタマイズ skill・consumer 規則）

## 参照

- Issue #440（本 ADR）、Issue #426（artifact fallback と `ended_at` の採用）
- ADR 005（artifact-primary verdict）、ADR 006（`result.json`）、ADR 007（interactive terminal runner）、
  ADR 008（後方互換レイヤを提供しない）
- `docs/ARCHITECTURE.md` § verdict source 解決順 / § `result.json`
- `docs/dev/shared_skill_rules.md` § resolve-verdict consumer
