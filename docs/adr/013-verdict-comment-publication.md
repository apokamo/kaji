# ADR 013: verdict 付き Issue 報告コメントの投稿を統合・冪等化しない（投稿前検証のみ後続候補とする）

## ステータス

提案（2026-11-15、Issue #570）— 本 ADR は調査・文書化の成果物であり、この文書を含む PR を
人間が merge した時点で承認とみなす。merge 前は以下の推奨・契約案はいずれも人間決定ではない。
本 ADR はコード・skill・公開 CLI・コメント形式を変更しない。

## コンテキスト

agent skill は判定を **作業報告 Issue コメント末尾 → stdout → `verdict.yaml`** の順に残す（Issue #237、
[`docs/dev/skill-authoring.md`](../dev/skill-authoring.md) § verdict 出力規約）。Issue コメントの投稿
（GitHub）と artifact の公開（ローカル filesystem）は別操作で、両者をまたぐ atomic transaction は組めない。
したがって「投稿は成功したが artifact 公開前に停止した」後の再実行で、同じ報告が再投稿されうる
（crash window）。本 ADR は、この重複を防ぐために投稿を共通処理へ統合すべきかを判断する。

対象は **start 以外の一般の判定コメント**（`kaji issue comment --verdict-step/--verdict-status` で agent
が投稿するもの）である。start 報告は #569 で別契約が実装済みであり、維持を前提とする参照実装として扱う。

### 現行の投稿・解決契約（HEAD `c3bdf85`）

```text
agent skill（一般の判定コメント）                start（exec_script, Issue #569）
──────────────────────────────                ─────────────────────────────────
① kaji issue comment <id> --verdict-step S       ① view_issue で全コメント取得（失敗 → ABORT）
     --verdict-status ST --body-file -           ② classify_start_reports
   └ marker を 1 行目に前置し comment_issue        SAME あり → 投稿 SKIP
     （照合なし・本文の verdict block 検証なし）      SAME なし + UNIDENTIFIABLE → 書き込み前 ABORT
② stdout に ---VERDICT--- block                     それ以外 → 投稿
③ kaji verdict publish（verdict.yaml を          ③ KAJI_VERDICT_PATH へ verdict.yaml
   os.link で原子的・非上書き公開。最後）
        │                                                   │
        └──── runner: resolve_verdict ────────────────────────┘
              artifact → comment（agent step のみ）→ stdout
```

| 要素 | 現行契約 | 一次情報 |
|------|----------|----------|
| 一般の判定コメント投稿 | marker 文法を検証して 1 行目に前置し、`GitHubProvider.comment_issue`（`gh issue comment`）で 1 回投稿する。既存コメントの照合、本文末尾の `---VERDICT---` block の検証、marker status と block status の一致確認は行わない。投稿は 1 回だけ呼び、`GitHubProviderError` は exit 3（`EXIT_RUNTIME_ERROR`）、引数・marker 不正は exit 2（`EXIT_INVALID_INPUT`）。CLI 内の再試行は無い | `kaji_harness/commands/issue.py` `_github_issue_comment_with_verdict`、`kaji_harness/commands/exit_codes.py` |
| marker 文法 | `<!-- kaji-verdict: step=<step> status=<STATUS>[ key=value...] -->`。meta は key 昇順。meta 無しも有効 | `kaji_harness/providers/markers.py` `build_kaji_verdict_marker` / `_VERDICT_MARKER_RE` |
| artifact 公開 | `kaji verdict publish` が `verdict-contract.json`（`run_id` / `attempt` / 公開先 / 許容 status）に従い検証し、`os.link` で非上書き公開。既存なら exit 2（`kaji verdict publish` 独自の exit 契約: 0 / 1 入力不正 / 2 状況エラー / 3 I/O。`kaji issue comment` の exit code とは別体系） | `kaji_harness/verdict_publish.py`、Issue #568 |
| 解決順 | artifact が存在すれば comment / stdout を見ない。comment fallback は `created_at >= attempt_started_at`（秒切り捨て）かつ marker が自 step（または marker 無し）のコメントを newest-first で採用。exec / exec_script は comment fallback なし | `kaji_harness/verdict.py` `resolve_verdict` / `_is_current_comment` / `_comment_step_scope_allows`、ADR 005 |
| comment / stdout 解決時 | harness が同じ verdict を `verdict.yaml` へ正規化保存 | `kaji_harness/runner.py` `_resolve_step_verdict` |
| dispatch 失敗 | `StepTimeoutError` / `CLIExecutionError` 等は verdict 解決を経ず合成 ABORT を記録して送出（comment fallback は走らない） | `runner.py` の dispatch `except` 節、`_record_dispatch_failure` |
| 再実行の層 | (a) 同一 dispatch 内の transient CLI retry（`_MAX_RETRIES = 3`、同じ attempt dir・同じ prompt で agent を再起動）、(b) run recovery（10 分後に新規 `kaji run`。新しい attempt / `attempt_started_at`）、(c) cycle による同一 step の再 dispatch（正当な新しい判定） | `kaji_harness/cli.py` `execute_cli`、`docs/ARCHITECTURE.md` § 失敗リカバリの層構造 |
| Herdr 完了トリガ | `verdict.yaml` の出現で完了。pane が `verdict.yaml` 前に shell へ戻れば `CLIExecutionError` | `kaji_harness/interactive_terminal_herdr.py` の verdict 待機ループ |
| start 報告 | exec_script が PASS 時のみ marker 付きで投稿。投稿前に全コメントを読み、marker + 本文表の `ブランチ` / `ディレクトリ` 行を同一性キーに照合後 dedupe。キーは attempt 非依存（再 start は run をまたいで SKIP） | `kaji_harness/issue_start.py` `classify_start_reports`（L176）、投稿 L717-724、`docs/dev/shared_skill_rules.md` § verdict マーカー契約 |

### marker consumer（互換性評価の対象）

| consumer | 照合方法 | meta 追加への耐性 |
|----------|----------|-------------------|
| `resolve_verdict` comment fallback | `parse_kaji_verdict_marker`（文法全体） | 耐える |
| `kaji issue resolve-verdict` / `release-starter` | 文法全体 + `--require-meta` | 耐える |
| `issue-fix-ready` | 最新 review-ready marker を文法全体で検証 | 耐える |
| `issue_start.classify_start_report` | 文法全体（meta 不問） | 耐える |
| `issue-design` Step 1.6 / 1.7 | jq `test("^<!-- kaji-verdict: step=... status=(BACK\|BACK_DESIGN) -->")` | **耐えない**（status 直後の ` -->` を要求） |
| `issue-small-change-execute` / `issue-small-change-review` | jq `test("^<!-- kaji-verdict: step=change status=PASS -->")` 等 | **耐えない** |
| 外部カスタム skill | 不明（同様の固定文字列照合がありうる） | 不明 |

### 実データ（2026-11-15 取得）

直近更新順 80 Issue（#451〜#570）の全コメント 576 件、うち marker 付き 239 件を走査した。

- **crash window 起因と判定できる重複は 0 件**。同一 step・同一 status の marker 付きコメントが
  15 分以内に連続した例は 4 組（#554 で 2 組、#568・#569 で各 1 組、いずれも `step=close status=PASS`）。
- 4 組はすべて **同一 attempt 内の自己修正再投稿** である。#569 の 2 件（18:04:41Z / 18:05:13Z）の差分は
  `---VERDICT---` block 内の ` evidence: |` / ` suggestion: |` の先頭空白だけで、1 件目は block が YAML として
  壊れていた。run artifact では close は `attempt-001` のみ、`verdict_source` は `artifact` で、
  crash・retry・recovery は発生していない。#568 も同じ先頭空白の差、#554 は block 欠落 → 書式崩れ → 修正の 3 連投。
- 再検証手順: `gh issue list -R apokamo/kaji --state all --limit 80 --search "sort:updated-desc"`（2026-11-15 実行）で
  得た Issue ごとに `gh api repos/apokamo/kaji/issues/<n>/comments --paginate` を取得し、1 行目が
  `<!-- kaji-verdict: step=(\S+) status=(\S+)` に一致するコメントを marker 付きとした。投稿順に並べ、直前の
  コメントも marker 付きで step・status が同じかつ投稿間隔 900 秒未満のものを「連続」とした（間に marker 無し
  コメントが入れば連続を切る）。本文完全一致の連続は 0 件。更新順の取得のため Issue 集合は実行時点に依存する。
- 該当 7 コメント: #554 [1](https://github.com/apokamo/kaji/issues/554#issuecomment-6842445287) /
  [2](https://github.com/apokamo/kaji/issues/554#issuecomment-6842448228) /
  [3](https://github.com/apokamo/kaji/issues/554#issuecomment-6842451501)、
  #568 [1](https://github.com/apokamo/kaji/issues/568#issuecomment-6880843860) /
  [2](https://github.com/apokamo/kaji/issues/568#issuecomment-6880847281)、
  #569 [1](https://github.com/apokamo/kaji/issues/569#issuecomment-6881921137) /
  [2](https://github.com/apokamo/kaji/issues/569#issuecomment-6881927619)。
- つまり観測された重複の原因は「投稿後の停止」ではなく「**投稿前に本文の verdict block を検証していない**」ことである。

## 故障注入による重複・誤 dedupe 条件の特定

各シナリオで現行挙動と、比較案で何が変わるかを示す。`○` は重複しない、`×` は重複しうる、`△` は条件付き。

| # | 故障点 | 現行（案 A） | 現行の結果 |
|---|--------|--------------|------------|
| F1 | 投稿成功 → `gh` 応答喪失（exit≠0） | agent は exit 3（`EXIT_RUNTIME_ERROR`）を見て同じ `kaji issue comment` を再実行しうる | × 同一 attempt で重複 |
| F2 | 投稿成功 → `kaji verdict publish` 前に agent 停止（headless、CLI exit 0） | artifact 不在 → comment fallback が当該コメント（自 step marker、`created_at >= attempt_started_at`）を採用し `verdict.yaml` を正規化保存 | ○ 重複なし・判定回収 |
| F3 | 投稿成功 → publish 前に停止（headless exit≠0 / Herdr pane が shell 復帰 / timeout） | dispatch 例外 → 合成 ABORT → run recovery の新 attempt で skill を再実行 → 再投稿。旧コメントは新 `attempt_started_at` より前なので、新 attempt が artifact を公開すれば判定に使われず、artifact 不在でも通常は時刻下限で除外される（同一秒の場合を除く。§ 提供できる保証の整理） | × attempt をまたいで重複（判定は新 attempt のもの） |
| F4 | 投稿成功 → transient CLI 失敗（同一 dispatch 内 retry） | 同じ attempt dir・同じ prompt で agent 再起動 → 再投稿 | × 同一 attempt で重複 |
| F5 | Issue コメント読み取り失敗 | 投稿経路は読まない。comment fallback 側は WARN して stdout へ | ○（照合しないので影響なし） |
| F6 | 同一 step の複数 attempt（cycle の review → fix → verify → review） | 各 attempt が別コメントを投稿 | ○ 正しい（重複ではない） |
| F7 | 本文の verdict block 書式崩れ → 自己修正 | 崩れたまま投稿され、agent が直して再投稿。直す前に停止し artifact も無ければ、comment fallback が `VerdictParseError` を送出し合成 ABORT | × **実測された唯一の重複**（artifact 不在時は判定喪失） |

比較案ごとの差（詳細は § 代替案と比較）:

- **attempt キーの照合後 dedupe（案 B）** が防げるのは F1・F4 だけである。F3 は新 attempt なのでキーが変わり防げない
  （防ぐには start のような attempt 非依存の内容キーが要るが、F6 を誤 dedupe する）。F7 は本文が異なるため
  SKIP できず、「同一キーの別本文」を ABORT にすると自己修正そのものを止める。
- **照合と投稿の間の並行投稿**は、GitHub に条件付き作成（compare-and-set）が無いため、どの案でも排除できない
  （start も同じ限界を持つ）。kaji は同一 Issue・同一 step を逐次実行するため現実の発生経路は手動実行との競合に限られる。
- **読み取り失敗（F5）** は案 B では「投稿する（重複許容）」か「ABORT（fail-closed、判定喪失）」の
  二択になる。start は fail-closed を選んでいるが、start は副作用が冪等で再 start が安価なためである。

## 提供できる保証の整理

GitHub と filesystem をまたぐ原子性は無いため、提供可能な保証は次に限られる。

| 保証 | 意味 | 実現手段 |
|------|------|----------|
| best-effort 投稿（現行） | `kaji issue comment` は投稿を 1 回試み、失敗すれば非 0 で終わる。配送成功まで続ける CLI 内 retry や永続 outbox は無い。投稿されないまま終わる経路（通信失敗後に agent が再実行しない、agent 停止）があり、agent・transient retry・run recovery による再実行では重複しうる | 現行 |
| at-least-once | 判定コメントが少なくとも 1 回投稿されることを保証する。重複は許容 | 現行には無い。成立させるには配送成功まで再試行し続ける主体（永続 outbox 等）が必要 |
| 照合後 dedupe（best-effort） | 投稿直前に既存コメントを読み、同一キーがあれば SKIP。照合〜投稿間の並行投稿は防げない | start（#569）。一般化は案 B |
| fail-closed | 照合不能・識別不能なら書き込み前に停止 | start の UNIDENTIFIABLE ABORT |
| exactly-once | 不可能（条件付き作成 API が無い） | — |

判定への影響は、当該 attempt で有効な `verdict.yaml` が公開済みかどうかで分かれる。

- **artifact が有効に公開済みの場合**: `resolve_verdict` は comment / stdout を一切読まない（ADR 005）。重複コメントは
  判定に影響せず、実害は人間向け履歴の冗長さと、`resolve-verdict` など「最新 1 件」を選ぶ consumer が自己修正前の
  壊れたコメントを掴みうること（自己修正版が最新として残れば問題にならない）に限られる。実測 4 組はすべてこの条件
  （#569 の close は `verdict_source=artifact`）。
- **artifact が不在の場合（F2 など comment fallback に落ちる経路）**: 現行実装には次の残存リスクがあり、重複・
  書式崩れコメントが判定を誤らせうる。
  - **同一秒の前 attempt コメント**: 時刻下限は `attempt_started_at` を秒へ切り捨てて比較し（`verdict.py`
    `_is_current_comment`）、step scoping は step 名だけを照合する（`_comment_step_scope_allows`）。そのため
    dispatch 開始と同じ秒に投稿された同一 step の前 attempt コメントは除外できず、`source=comment` で採用されうる
    （doc-review で、開始 `18:00:00.900000+00:00`・前 attempt コメント `18:00:00Z`・同一 step marker・artifact /
    stdout なしの条件で前 attempt の判定が採用されることを確認済み）。ADR 012 § 時計のずれと比較の限界 に既載の制約。
  - **最新コメントの block 破損**: `parse_verdict_block` は block が在って YAML / 必須フィールドが不正なら
    `VerdictParseError` を送出し、`resolve_verdict` はそれを捕捉して次の候補・stdout へ進まない。したがって
    F7 型の書式崩れコメントが最新のまま agent が停止すると、正常な旧コメントや正常な stdout があっても判定は
    `VerdictParseError` → 合成 ABORT になる（doc-review で ` evidence: |` の先頭空白により確認済み）。
- いずれも重複抑止（案 B / C）では解消しない。前者は attempt 単位の識別による comment fallback 側の照合、後者は
  投稿前の block 検証（§ 後続 Issue 候補）の対象である。本 ADR は comment fallback の動作変更を扱わない。

## 決定（提案）

1. **一般の判定コメントの投稿を統合・冪等化しない（案 A: 現行維持）。** 案 B（冪等 helper）・案 C（submit 統合）は
   現時点で **NO-GO**。
2. **start 報告の契約（#569）は変更しない。** start の同一性キーは attempt 非依存の「同じ worktree の報告か」であり、
   一般の判定コメント（同一 step が cycle で正当に複数回判定する）とは意味が異なるため、一般化の参照実装にはしない。
3. 先頭 marker・`--verdict-meta` 文法・`verdict.yaml` schema（ADR 012）・解決順（ADR 005）はすべて維持する。
   migration・rollback は不要。
4. **後続候補（要人間判断で起票）: 投稿前の verdict block 検証。** 実測された重複（F7）の原因を投稿前に断つ。
   契約案は § 後続 Issue 候補の公開契約案。これは重複抑止ではなく入力検証であり、本決定 1 と矛盾しない。

### 推奨の根拠

- **実害が観測されていない**: 80 Issue・marker 付き 239 件で crash window 起因の重複は 0 件。観測された 4 組は
  すべて F7（書式崩れの自己修正）。
- **案 B は crash window の主経路を防げない**: 停止後の再実行は run recovery（F3）で新 attempt になる。attempt キーで
  防げるのは同一 attempt 内の F1・F4 だけで、F3 を防ぐ内容キーは F6 を誤 dedupe する。保証の拡大幅が、全 producer
  の移行・読み取り失敗時の方針決定・API 呼び出し増（投稿ごとに全コメント取得）に見合わない。
- **artifact が有効に公開済みなら判定に影響しない**: 正本は artifact で、存在すれば comment を読まない（ADR 005）。
  artifact 不在時の残存リスク（同一秒の前 attempt コメント採用、最新コメント破損による `VerdictParseError`）は
  上記のとおり存在するが、重複抑止では解消せず、前者は comment fallback 側、後者は投稿前検証の課題である。
- **案 C は既存の verdict-last 契約を作り直す**: Herdr の完了トリガ（`verdict.yaml` 出現）、`kaji verdict publish` の
  exit code 契約（#568）、exec_script の自前公開、手動実行（`verdict_contract` 無し）の 4 経路を 1 コマンドへ寄せる
  必要があり、22 producer と外部カスタム skill が同時 BREAKING（ADR 008 で互換層を作らない）になる。

## 代替案と比較

| 観点 | A. 現行維持（推奨） | B. 投稿だけの idempotent helper | C. コメント / stdout / artifact を統合する submit |
|------|--------------------|--------------------------------|--------------------------------------------------|
| 内容 | `kaji issue comment --verdict-*` で投稿、`kaji verdict publish` で公開 | `kaji issue comment --verdict-*` が `verdict-contract.json` の `run_id` / `attempt` を本文に埋め、marker の step と組にした識別キーで投稿前に全コメントを照合し、同一キーなら SKIP | `kaji verdict submit` が 1 回の呼び出しでコメント投稿（照合後 dedupe）→ stdout → artifact 公開を順に行う |
| 重複防止 | なし（best-effort 投稿。配送も重複抑止も保証しない） | F1・F4 のみ。F3 は防げず、F7 は防げない（別本文） | B と同じ。加えて block を構造化入力から生成するので F7 は消える |
| 誤 dedupe | なし | attempt キーなら F6 は誤らない。手動実行（契約無し）はキーを作れず dedupe 不能 | B と同じ |
| 失敗時の安全性 | 投稿失敗は exit 3。その後 agent が `kaji verdict publish` に到達した場合に限り判定は artifact で回収される（到達しなければ F3 と同じ経路）。投稿されないまま終わる経路もある | 読み取り失敗を fail-closed にすると判定喪失、投稿にすると重複。どちらかを選ぶ必要がある | 途中失敗の部分状態（投稿済み・未公開）が 1 コマンド内に移るだけで原子性は無い。exit code 体系の再設計が必要 |
| 互換性 | 変更なし | 識別子の置き場所が必要。1 行目 marker の meta に入れると `issue-design` Step 1.6/1.7 と small-change 2 skill の jq 照合（status 直後の ` -->` 要求）が壊れる。2 行目の別 HTML コメントなら marker consumer は無影響だが、「2 行目以降を本文とする」抽出（`issue-design` Step 1.7）に混入する | B に加え、prompt の出力要件・`skill-authoring.md` の 3 経路規約・全 producer の手順・Herdr の完了契約を同時変更 |
| 移行コスト | 0 | CLI 1 箇所 + テスト。skill の呼び出しは不変（契約がある場合のみ自動付与）でも、手動実行時の挙動差の文書化が必要 | 22 producer + 外部カスタム skill + prompt + docs。ADR 008 により互換層なし |
| 評価 | 採用（提案） | NO-GO（再検討条件あり） | NO-GO |

### 識別情報をコメントへ載せる場合の推奨位置（再検討時の前提）

案 B / C を将来採用する場合に備え、比較結果だけを記録する（本 ADR では採用しない）。

- **1 行目 marker の meta（`run=<run_id> attempt=<N>`）**: 文法上は有効（key `^[a-z][a-z0-9_]*$`、value
  `^[A-Za-z0-9][A-Za-z0-9._/-]*$` を満たす）だが、上表の jq 固定文字列照合 consumer を同時改修しない限り BREAKING。
- **2 行目の独立 HTML コメント（例: `<!-- kaji-attempt: run=<run_id> attempt=<N> -->`）**: 1 行目契約と全 marker
  consumer を壊さない。旧コメント（2 行目なし）は「識別子なし」として扱い、照合では **SKIP の根拠にしない**
  （ABORT にもしない。start と異なり旧コメントが大量にあるため）。推奨はこちら。
- **識別キーの構成**: attempt 番号は `steps/<step_id>/attempt-NNN` 単位で採番される（`runner.py`
  `allocate_attempt_dir`）ため、同一 run の別 step にも `attempt=1` が存在する。2 行目の識別子を単独で使わず、
  **1 行目 marker の `step` + 2 行目の `run` + `attempt` の 3 つ組**を識別キーとする。以下の衝突規則はすべて
  同じ step の報告同士にだけ適用する。
- **衝突時の停止条件**（同一 step の報告同士）: 同一識別子・同一本文 → SKIP。同一識別子・異なる本文（F7 型の自己修正）→ 投稿する
  （SKIP すると壊れた block が最新として残る）。同一識別子・異なる status → 投稿し、stderr に警告。
  ABORT は読み取り失敗時の選択肢としてのみ検討する。
- **provenance**: SKIP / 投稿の別、照合した comment id を `verdict-contract.json` と同じ attempt dir の
  別ファイル（例: `comment-publication.json`）に残す。`verdict.yaml` の schema は変えない（ADR 012）。

## 後続 Issue 候補の公開契約案（投稿前の verdict block 検証）

本 ADR の承認とは別に、人間が起票を判断する。実装 workflow は `dev-small` 相当を想定する。

- **対象**: `kaji issue comment <id> --verdict-step S --verdict-status ST`（`_github_issue_comment_with_verdict` の構造化経路。現行はこの経路だけが marker を前置する）。
  `--verdict-*` を付けない passthrough は対象外。
- **検証**: 投稿前に body 末尾の `---VERDICT---` block を `parse_verdict_block` 相当で厳密に parse し、
  (1) block が存在する、(2) YAML として parse できる、(3) block の `status` が `--verdict-status` と一致する、
  (4) ABORT / BACK* で `suggestion` が非空、を満たさなければ **投稿せず** exit 2（既存の引数・marker 不正と同じ `EXIT_INVALID_INPUT`）と
  stderr に理由を出す。PASS-only producer（`issue-close` 等）も同じ規則。
- **例外**: block を本文に含めない producer があれば列挙してから決める（現行 22 producer は全て末尾に block を置く
  契約。`docs/dev/skill-authoring.md` § verdict 出力規約）。
- **互換性**: 正しい block を投稿している skill には無影響。壊れた block は従来「投稿されてから自己修正」だったものが
  「投稿前に拒否」になる。コメント形式・marker・`verdict.yaml` は不変。
- **完了条件案**: 上記 4 条件それぞれの拒否テストと、#569 の close 1 件目（先頭空白付き ` evidence: |`）を再現した
  入力が投稿されないことの回帰テスト。`issue_start` の投稿経路は対象外（本文を自前生成するため）。

## 検証案（再検討時・後続 Issue 用の故障注入）

案 B / C を再検討する場合、または投稿前検証を実装する場合の回帰テスト設計。いずれも GitHub 実 API は使わず
provider を fake / mock に差し替える。

| ID | 注入方法 | 期待（案 A 現行） | 期待（案 B 採用時） |
|----|----------|-------------------|---------------------|
| F1 | fake `comment_issue` がコメントを保存した後に `GitHubProviderError` を送出 → 同じ入力で再実行 | 2 件 | 1 件（SKIP） |
| F2 | publish を呼ばずに agent fake が exit 0 | `source=comment`、`verdict.yaml` 正規化保存、1 件 | 同左 |
| F3 | 投稿後に dispatch を `CLIExecutionError` で落とし、新 attempt で再実行 | 2 件、新 attempt の判定は新コメント由来 | 2 件（attempt キーでは防げないことを固定） |
| F4 | `execute_cli` の transient retry で同一 prompt を 2 回実行 | 2 件 | 1 件 |
| F5 | fake `view_issue` が例外 | 投稿される | 選択した方針（投稿 or ABORT）どおり |
| F6 | 同一 step を cycle で 2 attempt（同一 status・同一本文） | 2 件 | 2 件（誤 dedupe しない） |
| F7 | 先頭空白付き ` evidence: \|` の block | 投稿される | 投稿前検証の実装後: exit 2（`EXIT_INVALID_INPUT`）・0 件 |
| F7b | F7 のコメントを最新に残し、artifact 不在・正常な旧コメントと正常な stdout ありで `resolve_verdict` | `VerdictParseError`（次候補・stdout へ進まない） | 現行の制約として固定（投稿前検証で入口を断つ） |
| F8 | 同一 step の前 attempt コメントを dispatch 開始と同じ秒に置き、artifact / stdout なしで `resolve_verdict` | 前 attempt の判定を `source=comment` で採用 | 現行の制約として固定（ADR 012 既載） |

## 判断を変える条件

次のいずれかを観測したら本 ADR を再検討する。

- **crash window 起因の重複（F1・F3・F4 型）が実データで観測された場合**。同一 attempt（F1・F4）なら案 B を
  2 行目識別子方式で第一候補とする。attempt をまたぐ（F3）ものが主なら、dedupe より先に run recovery が
  前 attempt の判定コメントを回収できるか（`verdict.yaml` 不在時に前 attempt のコメントを採用する条件）を検討する。
- **comment fallback が判定を誤った場合**（重複コメントのうち古い方を採用した等）。判定正本の問題として
  ADR 005 / ADR 012 § 時計のずれと比較の限界とあわせて attempt 単位の識別を検討する。
- **GitHub に条件付き作成（idempotency key 等）が提供された場合**。exactly-once に近い保証が得られるため案 B の
  費用対効果が変わる。
- **producer の大半が構造化入力からコメントを生成する形へ移行した場合**（start のように exec_script 化が進む等）。
  案 C の移行コストが下がる。
- **投稿前検証を実装した後も F7 型の再投稿が続く場合**（block 以外の本文修正による再投稿）。重複が人間の追跡を
  妨げるなら、再投稿でなく既存コメントの編集（provider への comment edit 追加）を検討する。

## 影響

- コード・テスト・skill・公開 CLI・コメント形式・`verdict.yaml` の変更は無い。
- `docs/dev/skill-authoring.md` § verdict 出力規約 に、判定コメントの投稿は best-effort（配送保証なし）で冪等でもないことと
  本 ADR への参照を 1 文追記する。
- 本提案が承認された場合、必須の実装 Issue は無い。投稿前検証（§ 後続 Issue 候補）は人間が起票を判断する。

## 未決事項（人間判断）

- **本提案（案 A 維持、案 B / C NO-GO）の採否**。PR merge をもって承認とみなす。
- **投稿前の verdict block 検証を後続 Issue として起票するか**。
- 案 B を将来採用する場合のみ: 読み取り失敗時に投稿（重複許容）と ABORT（fail-closed）のどちらを取るか、
  手動実行（`verdict-contract.json` 無し）で識別子を省略してよいか。

## 参照

- Issue #570（本 ADR）、#568（`kaji verdict publish`、PR #571）、#569（start 報告の exec_script 投稿と照合後 dedupe、
  PR #573）、#237（verdict-last の順序）、#554（コメント marker）、#449（comment fallback の step scoping）
- ADR 005（artifact-primary verdict）、ADR 007（interactive terminal runner）、ADR 008（後方互換レイヤを提供しない）、
  ADR 012（`verdict.yaml` に時刻を追加しない）
- `docs/dev/skill-authoring.md` § verdict 出力規約、`docs/dev/shared_skill_rules.md` § verdict マーカー契約、
  `docs/ARCHITECTURE.md` § 失敗リカバリの層構造
