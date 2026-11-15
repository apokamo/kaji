# [設計] 判断用の skill に、決められた種類の停止を承認なしで戻す自動復旧を加える

Issue: #545

## 概要

`/watch-triage`（#543）に、`## 決定事項` D1 の 5 種類の停止だけを保守者の承認なしで、回数の上限付きで戻す
自動復旧を加える。上限の確認と記録は `kaji watch` の下のサブコマンドが `records.jsonl` の `flock` の下で
1 回に行い、上限に達したら追記せずに exit 5 を返す。skill は exit 0 のときだけ操作する。

## 背景・目的

### ユースケース

- 保守者として、provider の一時エラー・usage limit・repo の中のパスの信頼ダイアログ・Issue の決定事項から
  答えられる質問・環境が原因の失敗で止まった step は、記録だけ残して自動で戻り、自分が判断すべき停止だけが
  届いてほしい。
- 保守者として、自動で行った復旧を後から一覧で確かめたい（`kaji watch recoveries`）。
- 保守者として、one-way door（重要判断チェックリストの分類 3）を無人の workflow が埋めないことを保証してほしい。

### 自動復旧の 5 種類（D1。設計で増減しない）

<!-- watch-triage-auto-recovery:start -->
| ID | 停止 | 操作 | 上限の単位 | 上限 |
|---|---|---|---|---|
| continue-sent | 一時エラー（`at capacity`・rate limit・`overloaded`・5xx・接続の一時的な失敗）で止まった `pane_idle` | `continue` を送り、`working` に戻るかを待つ | issue・run・step・attempt | 3 |
| wait | usage limit（画面に再開の予定時刻がある。なければ continue-sent として扱う） | 予定時刻まで待つ（記録して自分のタブを閉じる） | issue・run | 3 |
| dialog-answered | repo の main checkout・worktree の中のパスへの信頼・取り込みのダイアログ | 許可で答える | issue・run・step・attempt | 3 |
| question-answered | step の agent の質問のうち `critical-decision-checklist.md` の分類 1・2 | 答える（分類 2 は仮定・根拠・後段の検査先を Issue にコメント） | issue・run・step・attempt | 3 |
| retry-sent | 環境が原因の member の失敗（`aborted`） | 失敗した step から 1 回やり直す（`kaji recover <workflow> <issue> --retry-failed-step`） | issue・anchor | 1 |
<!-- watch-triage-auto-recovery:end -->

除外条件（D1 のとおり）:

- dialog-answered: CLI の更新・モデルの切り替え・repo の外のパス・認証・ログインのダイアログは対象外
- question-answered: 分類 3 と、分類に迷ったものは答えずに保守者に回す
- retry-sent: 正規の ABORT・cycle の上限・worktree / branch の不整合・認証 / 権限の形跡・中断、再開禁止の skill
  （`issue-start` / `i-pr` / `issue-close`）、古い agent / pane の残存は除外

### 種類の採用根拠（D3 の 3 点）

1. **kamo2 #2126 の決定と運用**: apokamo/kamo2 `.claude/skills/lane-triage/SKILL.md` の「自動復旧の一覧（#2126）」と
   `scripts/ops/lane_watch/models.py` の `AUTO_LIMITS`。上の表は outcome 名・単位・上限をそのまま移したもの。
2. **対象の停止の実例**: kamo2 #1872 の run `261106000729`（2026-11-06）で、Codex が `Selected model is at capacity`
   を出して入力欄に戻り、79 分空転して `StepTimeoutError` になった（kamo2 incident #2102）。Herdr runner は
   生きている pane の provider エラーを検出しない。#1797 の実ログでは同じ表示の後に `continue` を送って続いている。
3. **kamo2 での発動 0 件の理由**: #2126 の merge（2026-11-07）の後に対象の停止が起きていないため（step の
   `terminal.log` の走査で確認）。step の agent は API エラー等を自分では直せないので、step の中で続けるルール
   （kamo2 #2251 等）では代替できない。

### A2 の事実確認: `pane_idle` は Codex の要求失敗で入力欄に戻った pane を検知できる

- **Herdr の仕様**（`herdr --skill`、herdr 0.9.5）: agent の lifecycle state は `idle` / `working` / `blocked` /
  `done` / `unknown`。「`idle` and `done` normally indicate the agent is ready for input」。
- **kaji の検知**（`kaji_harness/watch/detect.py` の `detect_events`）: `agent_status` が `working` 以外の周が
  `PANE_IDLE_POLLS`（2）回続けば `pane_idle` を出す。`unknown` も数える。
- **実機の確認**（2026-11-15、herdr 0.9.5、codex-cli 0.169.0）: Herdr のタブで
  `codex -m kaji-a2-probe-nonexistent-model 'reply with OK'` を起動した（at capacity の代わりに、要求が失敗して
  入力欄に戻る停止を作った）。画面は `■ The 'kaji-a2-probe-nonexistent-model' model is not supported ...` の後に
  `› Ask Codex to do anything` で入力待ちになり、`herdr pane get` の値は 10 秒後から 60 秒後まで
  `{"agent":"codex","agent_status":"idle"}` のままだった。at capacity と同じ「`■` のエラー行 → 入力欄」の形である
  （incident #2102 引用 2 の `clean_tail` と一致）。
- **結論**: `working` 以外になるので、`pane_idle` は 2 周（約 6 分）で検知する。検知の追加は不要で、保守者への
  確認（A2 の分岐）は発生しない。実際の at capacity での確認は事後確認項目（release 後の Herdr 実機）に残る。

### 代替案と不採用理由

- **step の中のルールで続ける**（kamo2 #2251 の類）: step の agent は API エラーを自分で直せない（D3 の 3 点目）。
- **Herdr runner の待ちループで provider エラーを検出する**（incident #2102 の恒久対策案）: kaji 本体の dispatch
  の変更で、本 Issue のスコープ外。本 Issue は D2 で「監視の CLI と判断用の skill」に置くと決まっている。

## インターフェース

### 入力

#### `kaji watch record` の拡張（outcome の追加）

```bash
kaji watch record --event FILE --outcome continue-sent     --reason TEXT
kaji watch record --event FILE --outcome wait              --reason TEXT --until ISO8601
kaji watch record --event FILE --outcome dialog-answered   --reason TEXT --path ABS [--path ABS ...]
kaji watch record --event FILE --outcome question-answered --reason TEXT
```

| outcome | 使える event の `kind` | 必須の引数と検証 |
|---|---|---|
| `continue-sent` / `dialog-answered` / `question-answered` | `pane_idle` | event の `run_id` / `step` / `attempt` が空でない。`--reason` が空でない |
| `wait` | `pane_idle` / `aborted` | event の `run_id` が空でない。`--until` が時刻帯付きの ISO 8601 で、今より後かつ 7 日以内 |
| `dialog-answered` | `pane_idle` | `--path` が 1 つ以上。各パスは絶対パスで、`resolve()` 後に repo の main checkout または `git worktree list --porcelain` の worktree の内側 |

既存の outcome（`handoff` / `still-working` / `not-stalled` / `recovered`）の挙動は変えない。`--reason` / `--until` /
`--path` を既存の outcome に渡したら exit 2。

#### 新しいサブコマンド `kaji watch retry`

```bash
kaji watch retry --event FILE --reason TEXT [--workdir DIR]
```

- `aborted` の event だけ。event の `workflow` / `run_id` / `anchor` が空でないこと。Herdr の中であること（exit 2）。
- 上限の確認と `retry-sent` の記録を lock の下で 1 回に行い、exit 0 のときだけ、新しい Herdr タブ
  `kaji-retry-<issue>` で `kaji recover <workflow> <issue> --retry-failed-step --run-id <run_id>` を起動する。
  安全ゲートの判定は `kaji recover` に任せる（#540。本コマンドは判定を複製しない）。
- やり直しを `/watch-triage` のタブの中で動かさない理由: `kaji recover` は子の run を前面で動かす。triage のタブで
  動かすと、そのタブが開いている間は `pane_idle` / `log_stall` が出ない（`open_triage`）ため、子の run の停止を
  検知できなくなる。

- 除外条件（D1 の 5.）と判定する場所:

  | 除外条件 | skill の判断（`retry` を呼ぶ前） | `kaji recover` の安全ゲート（#540） |
  |---|---|---|
  | 正規の ABORT・cycle の上限・中断 | 原因が環境でなければ呼ばない | `agent_declared_abort` / `cycle_exhausted` / `user_interrupted` |
  | worktree / branch の不整合 | 同上 | `worktree_unavailable` / `branch_mismatch` / `ambiguous_worktree_abort` |
  | 認証 / 権限の形跡 | 同上 | `sensitive_failure_pattern` |
  | 再開禁止の skill | 同上 | `non_resumable_skill` |
  | 古い agent / pane の残存 | 同上 | `agent_pane_alive` / `agent_pane_unverifiable` / `run_alive` / `issue_run_alive` |

  skill の判断が誤っても、ゲートが拒否する（二重の防御）。ゲートに拒否された場合も `retry-sent` の記録は残り、
  同じ anchor でもう一度は自動でやり直さない（上限 1 を消費する。kamo2 の `rerun` と同じ）。
- 起動の確認: `kaji recover` は別のタブで動くので、skill は `retry` の exit 0 の後に、最大 120 秒、
  `kaji status --json <issue>` の `latest_run.run_id` が変わって `termination: running` になるのを待つ。変わらなければ
  `herdr pane read <retry の pane_id>` で `retry-gate:` 行を読み、報告して `handoff` する（タブを閉じて黙って止まらない）。

#### 新しいサブコマンド `kaji watch recoveries`

```bash
kaji watch recoveries [--issue N] [--json] [--workdir DIR]
```

読み取り専用。自動復旧の記録（outcome が上の 5 種類のもの）を記録順に一覧する。

#### event ファイル（schema v1 のまま、任意の項目を追加）

| 項目 | 型 | 意味 |
|---|---|---|
| `anchor` | `str \| null` | event を出した時点の Issue の anchor（`retry-sent` の上限の単位）。`member:<run_id>` / `baseline:<run_id>` / `none` |
| `waited_until` | `str \| null` | `wait` の予定時刻を過ぎてから出し直した event のとき、その予定時刻（UTC）。それ以外は null |

### 出力

- `records.jsonl` の 1 行（`WatchRecord`）に任意の項目を追加する: `reason` / `until` / `paths` / `unit`
  （上限を数える単位の値の列。例 `[101, "261115113853", "implement", 1]`）。既存の行は新しい項目なしで読める。
- `kaji watch record`（自動復旧の outcome）: exit 0 で記録した行を JSON で stdout に出す。
- `kaji watch retry`: exit 0 で `{"record": {...}, "tab_id": "...", "pane_id": "...", "command": "..."}` を stdout に出し、
  通知 `retry` を送る。
- `kaji watch recoveries --json`: `{"schema_version": 1, "recoveries": [{"recorded_at", "issue", "event_key",
  "outcome", "unit", "count", "limit", "reason", "until", "paths", "judge"}]}`。`count` はその行を含めた同じ単位の
  通し番号（例 `2/3` の 2）。
- 通知の payload の `kind` に `retry` を追加する（`aborted | handoff | watch_exit | retry`）。schema v1 の追加の値。

### 終了コード

| Code | 意味 |
|---|---|
| 0 | 成功（記録した。`retry` は記録してタブを起動した） |
| 2 | 既存の意味に加えて: outcome と event の `kind` が合わない、必須の引数がない・不正（`--reason` が空・`--until` が不正・`--path` が repo の外）、`retry` を Herdr の外で呼んだ |
| 3 | 予期しないエラー（既存のまま。`retry` で記録の後にタブの起動に失敗した場合もここ。記録は残る） |
| 5 | **新設**。自動復旧の上限に達した。追記しない（`retry` はタブも起動しない）。stderr に単位と上限を 1 行出す |

5 を選んだ理由: `kaji watch` の既存の 0 / 2 / 3 と、`kaji recover --retry-failed-step` の 4（安全ゲートの拒否）の
どちらとも重ならない。skill は同じ手順で両方の終了コードを扱うので、4 を流用すると区別できない。

### 使用例

```bash
# 一時エラーへの continue（1 回分）
if kaji watch record --event "$EVENT" --outcome continue-sent --reason "Selected model is at capacity"; then
  herdr pane send-text "$STEP_PANE" continue && herdr pane send-keys "$STEP_PANE" Enter
  herdr agent wait "$STEP_PANE" --until working --timeout 180000
fi   # exit 5 / 2 なら操作せずに報告と handoff へ

# 環境が原因の失敗のやり直し（1 回だけ）
kaji watch retry --event "$EVENT" --reason "provider at capacity で StepTimeoutError"

# 一覧
kaji watch recoveries --issue 101
```

## 制約・前提条件

- 依存: #543 の `kaji watch`（`kaji_harness/watch/`）、#540 の `kaji recover --retry-failed-step`。どちらも merge 済み。
- 上限の確認と追記は `records.jsonl` を `open("a+")` して `fcntl.flock(LOCK_EX)`（blocking）の下で行う。監視の loop の
  lock（`_watch/lock`、loop の生存期間ずっと保持）は使わない（使うと loop の動作中に記録できない）。既存の outcome
  の追記も同じ `flock` を通し、数えている間に別の行が割り込まないようにする。
- 回数は `records.jsonl` の行から数える（状態ファイルには持たない）。壊れた行は既存どおり飛ばす（数に入らない）。
- `retry-sent` は `kaji watch retry` だけが書く（`record --outcome retry-sent` は argparse で受け付けない）。
- 承認なしの操作は 5 種類だけ。禁止リスト（#543）は変えない。
- Herdr の外では `retry` は動かない（タブを作れない）。`record` / `recoveries` は Herdr の外でも動く。

## 方針

### 1. `kaji_harness/watch/` の変更

- `models.py`
  - `OutcomeName` に `continue-sent` / `wait` / `dialog-answered` / `question-answered` / `retry-sent` を追加。
  - `WatchRecord` に任意の `reason` / `until` / `paths` / `unit` を追加。`WatchEventFile` に任意の `anchor` /
    `waited_until` を追加（`extra="forbid"` のまま。既存のファイルは default で読める）。
- `constants.py`: `AUTO_RECOVERY_LIMITS: dict[str, tuple[tuple[str, ...], int]]`（outcome → (単位の項目, 上限)）、
  `WAIT_HORIZON_SECONDS = 7 * 24 * 3600`、`RETRY_TAB_PREFIX = "kaji-retry-"`。値は上の表と一致させる。
- 新モジュール `recovery.py`（純関数）: outcome ごとの event の検証、`--until` の検証、`--path` の内側判定
  （checkout の一覧は引数で受け取る）、単位の値の計算 `unit_of(event, outcome)`、一覧の行の組み立て。
- `store.py`: `append_record_limited(record, *, unit, limit) -> bool`（`flock` の下で同じ outcome・同じ `unit` の行を数え、
  上限未満なら追記して `True`、上限なら追記せず `False`）。既存の `append_record` も同じ `flock` を取る。
- `service.py`: `record` を拡張し、自動復旧の outcome では検証 → `append_record_limited` を呼ぶ。上限なら
  `AutoRecoveryLimitReached` を送出。`retry(deps, event_file, reason)` と `recoveries(store, issue)` を追加。
  `retry` は記録の後に `HerdrClient.create_tab(f"kaji-retry-{issue}", repo_root)` → `run_in_pane` で起動し、通知 `retry`
  を送る。
- `detect.py` / `loop.py`: `wait` の記録を反映する。
  - 予定時刻の前: その Issue・run の `pane_idle` / `log_stall` の候補を出さない。`aborted` は検知・報告（M5）を
    続け、triage の起動だけを予定時刻の後に回す（`open_triage` と同じ扱い）。
  - 予定時刻の後: `wait` を記録した event key の最新の記録が `wait` なら、`<key>:w<n>`（n はその key 系列の `wait` の
    回数）を出し、event ファイルの `waited_until` に予定時刻を書く。`:w<n>` の `aborted` は報告コメントと通知を
    出し直さない（報告は run ごとに 1 回のまま）。
  - その Issue の最新の run が変わった、または pane の episode が増えた（予定時刻の前に `working` に戻った）場合は、
    新しい key の event として通常どおり検知する（kamo2 の `prune_waits` と同じ結果）。
  - event ファイルを書くときに `anchor`（`resolve_anchor` の値）を入れる。
- `commands/watch.py` / `commands/parser.py`: `record` の新しい引数、`retry` / `recoveries` サブコマンド、
  `AutoRecoveryLimitReached` → exit 5。`commands/exit_codes.py` に `EXIT_AUTO_RECOVERY_LIMIT = 5`。
- `notify.py`: `kind` に `retry` を追加。
- 通知（A3）: 1.〜4. で戻った場合は通知しない。5. の実行（`retry`）と、上限・直らないで `handoff` した場合
  （既存の `record --outcome handoff`）は通知する。

### 2. `/watch-triage` の変更（`.claude/skills/watch-triage/SKILL.md`）

- frontmatter の description を「自動復旧の一覧の 5 種類は承認なしで戻し、それ以外は報告して保守者に回す」に改める。
- 「自動復旧の一覧」節を追加し、上の 5 種類の表をマーカー `<!-- watch-triage-auto-recovery:start/end -->` で囲む
  （契約テストが code の `AUTO_RECOVERY_LIMITS` と照合する）。
- 絶対規則 1 を書き換える（A4）: 「agent の質問には、`critical-decision-checklist.md` の分類 1・2 だけ答える。分類 2 は
  仮定・根拠・後段の検査先を Issue にコメントする（1 行目 `<!-- kaji-watch-triage: answer key=<event key> -->`）。
  分類 3 と、分類に迷ったものは答えずに報告して保守者に回す」。
- 絶対規則 4 を書き換える: 失敗した step のやり直しは `kaji watch retry`（中で `kaji recover --retry-failed-step` を
  起動）を 1 回だけ承認なしで使える。2 回目以降と、除外条件に当たるものは保守者の承認の後に
  `kaji recover ... --retry-failed-step` を使う。
- 手順 2 に「一次判断（自動復旧）」を足す: 画面と artifact から 5 種類のどれに当たるかを判定し、各操作の前に
  `kaji watch record` / `kaji watch retry` を呼び、exit 0 のときだけ操作する。exit 5（上限）/ 2（検証に通らない）は
  操作せずに報告へ進む。成功したら `kaji watch record --outcome recovered --summary ...` を記録し、通知せずに自分の
  タブを閉じる（`herdr tab close <自分の tab_id>`）。`continue` の待ちは 1 回目の後 180000 ms、2・3 回目の後 360000 ms
  （kamo2 と同じ）。`herdr agent wait` が 3 回とも timeout なら報告へ進む。`wait` は記録して自分のタブを閉じる。
  `retry` は exit 0 の後に子の run の起動を確かめ（§ インターフェース「起動の確認」）、起動していれば Issue にコメント
  （1 行目 `<!-- kaji-watch-triage: retry key=<event key> -->`。原因・根拠・やり直した step・`1/1`・新しいタブの pane）→
  `kaji watch start` → 自分のタブを閉じる。起動していなければ報告して `handoff` する。
  series の member なら、kamo2 と同じく「series の残りの member は止まったまま。やり直しが close まで進んだ後に、保守者が
  `/run-watched` で続きを起動する（`run-series --resume` は失敗した member を最初の step からやり直すので使わない）」を
  コメントと通知の summary に書く。
- 手順 3（報告）に「自動復旧の記録と結果（`kaji watch recoveries --issue <issue>` の行）」を含める。
- 禁止リストは変えない（`kaji-state` は「kaji watch のサブコマンドを通さない書き換え」を禁じるもので、`record` /
  `retry` の追記は対象外であることを 1 行明記する）。

### 3. ドキュメント

- `docs/cli-guides/watch.md` / `watch.ja.md`: Commands に `retry` / `recoveries`、`record` の新しい outcome と引数、
  Exit codes に 5、通知の `kind` に `retry`、event ファイルの `anchor` / `waited_until`、「Automatic recovery」節
  （5 種類の表・上限・`wait` の抑止）を追加する。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 自動復旧の種類・上限・除外条件 | kamo2 #2126 の 5 種類をそのまま移す | 人間決定 D1（Issue 本文 `## 決定事項`、2026-11-15 `/grill-me`） | outcome 名・単位の項目を kamo2 `AUTO_LIMITS` と同じにし、`AUTO_RECOVERY_LIMITS` と skill の表を照合する契約テストを置く |
| やり直しの手段と上限の置き場所 | 5. は `kaji recover --retry-failed-step` だけ。上限は `kaji watch` 側 | 人間決定 D2 | `kaji watch retry` が記録と同時に `kaji recover` を新しい Herdr タブで起動する（タブを分ける理由は § インターフェース） |
| 種類の採用根拠 | kamo2 #2126・#1872 の実例・発動 0 件の理由の 3 点 | 人間決定 D3 | § 背景・目的に 3 点を記載 |
| サブコマンドの形 | 1.〜4. は `record` の拡張、5. は新しい `retry`、一覧は新しい `recoveries` | AI の仮定 A1。根拠: kamo2 も `record` の拡張 + `rerun` + `recoveries`。5. は確認・記録・起動を 1 つにしないと記録とタブの起動がずれる。後段の検査先: `issue-review-design` | 引数・検証・出力を § インターフェースで定義 |
| 上限の終了コード | 5 | AI の仮定 A1。根拠: `kaji watch` の 0/2/3、`kaji recover` の 4 と重ならない。後段の検査先: `issue-review-design` | `EXIT_AUTO_RECOVERY_LIMIT = 5` |
| `pane_idle` で at capacity の pane を検知できるか | 検知できる。検知の追加はしない | A2 の事実確認（Herdr の仕様と 2026-11-15 の実機確認。§ A2 の事実確認） | 実 at capacity での確認は事後確認項目に残す |
| 通知 | 1.〜4. で戻ったら通知しない。5. の実行と handoff は通知する | AI の仮定 A3。根拠: D1（kamo2 と揃える）。後段の検査先: `issue-review-design` | 通知の `kind` に `retry` を追加（schema v1 の追加の値） |
| 絶対規則 1 の書き換え | D1 の 4. に合わせて分類 1・2 だけ答える | AI の仮定 A4。根拠: 現行規則 1 が「答えて戻すのは #545 の範囲」としている。後段の検査先: `issue-review-code` | 文言を § 方針 2 に定義し、契約テストを更新 |
| 上限の記録の排他 | `records.jsonl` の `flock`（blocking）の下で数えて追記する | AI の仮定。根拠: 完了条件 2「lock の下で 1 回に」、kamo2 `store.append_record`。loop の lock は loop が保持し続けるので使えない。後段の検査先: `issue-review-design` | 既存の outcome の追記も同じ `flock` を通す |
| `wait` の後の再検知 | 予定時刻まで pane_idle / log_stall を出さず、過ぎたら `<key>:w<n>` で出し直す | AI の仮定。根拠: D2「これ以外は kamo2 と揃える」（kamo2 `apply_wait` / `prune_waits`）。後段の検査先: `issue-review-design` | `aborted` は報告を続けて起動だけ遅らせる（既存の `open_triage` の扱いに合わせる） |
| `retry-sent` の単位の anchor | event ファイルに `anchor`（`member:<run_id>` / `baseline:<run_id>` / `none`）を入れる | AI の仮定。根拠: D1「Issue・anchor（1 回の投入）ごと」、kaji watch の anchor の定義（`labels.resolve_anchor`）。後段の検査先: `issue-review-design` | event schema v1 に任意の項目として追加 |

one-way door の未決はない。公開 CLI の追加（サブコマンド・終了コード・通知の `kind`）は A1・A3 で人間が設計に委ねた範囲で、
いずれも既存の値を変えない追加である。

## テスト戦略

### 変更タイプ

- 実行時コード変更（`kaji_harness/watch/`・`kaji_harness/commands/`）+ skill 変更 + docs

### 実行時コード変更の場合

#### Small テスト

- `recovery.py` の純関数: outcome ごとの `kind` の検証（`continue-sent` に `aborted` → 拒否、`wait` に `log_stall` → 拒否）、
  `--reason` の空、`--until` の時刻帯なし・過去・ちょうど今・7 日より先、`--path` の相対パス・symlink・`..` で repo の外に
  出るパス・worktree の内側（checkout の一覧は引数で渡す）、`unit_of` が outcome ごとに正しい項目を取ること。
- `AUTO_RECOVERY_LIMITS` が設計書の表（5 種類・単位・上限）と一致すること。
- `detect_events`: `wait` の予定時刻の前は同じ Issue・run の `pane_idle` / `log_stall` を出さず、`aborted` は出すが起動を
  抑止する。予定時刻の後は `<key>:w1` を出す。episode が増えた・run が変わった場合は新しい key で出す。
- 一覧の行の組み立て（`count` / `limit` の通し番号、`--issue` の絞り込み）。
- skill の契約テスト（`tests/test_watch_skills.py`）: `/watch-triage` の自動復旧の表が `AUTO_RECOVERY_LIMITS` と設計書の表に
  一致する。絶対規則 1 が分類 1・2 だけ答える文言になっている（旧「agent の質問には答えない」の assert を差し替える）。
  `kaji watch retry` と `--retry-failed-step` の記載、起動の確認で `retry-gate:` を読む記載がある。禁止リストは不変。

#### Medium テスト

- `WatchStore.append_record_limited` の実ファイル I/O: 上限ちょうどで `False` を返し追記しない、別の単位・別の outcome は
  数えない、壊れた行を数えない、既存の行（新項目なし）を読める。
- 排他: `multiprocessing` で同じ単位の `continue-sent` を 6 プロセス同時に記録し、成功が 3 件・`records.jsonl` の該当行が
  3 行になること（`flock` が数えと追記を 1 回にしていることの検証）。
- `service.record` / `service.retry`（fake の `HerdrClient` と fake の通知コマンド）: `retry` が上限内ならタブを作り
  `kaji recover <workflow> <issue> --retry-failed-step --run-id <run_id>` を流して `retry` 通知を送る。上限ならタブを
  作らず通知しない。1.〜4. の記録では通知しない。
- `run_once` の 1 周（fake provider / fake Herdr）: `wait` の記録の後、予定時刻の前に triage を起動せず、後に `:w1` の
  event ファイル（`waited_until` 入り）を書いて起動する。event ファイルに `anchor` が入る。

#### Large テスト

- `tests/test_watch_cli_large_local.py` に追加（実 `kaji` CLI を subprocess で起動。PATH 先頭の fake `gh`）:
  `kaji watch record --outcome continue-sent` を 4 回呼び、exit が `0,0,0,5` で stderr に上限の 1 行が出ること。
  `--outcome dialog-answered --path /etc` が exit 2。`kaji watch recoveries --json` が schema v1 の文書で 3 行を返すこと。
  `kaji watch retry` を Herdr の外で呼ぶと exit 2。
- 実 Herdr・実 agent での自動復旧の確認は、release 後の事後確認項目（Issue 本文 `### ワークフロー完了後の確認項目`）で
  行う。CI に Herdr と provider の一時エラーを再現する環境はない（`testing-convention.md` の「物理的に作成不可」）。
  Herdr との結合部は Medium の fake `HerdrClient` で、CLI の配線は上の Large で検証する。

### 長時間 acceptance の配置

該当なし。本 Issue の受入は pytest（Small / Medium / Large-local）と `make check` で、いずれも nested workflow・外部 agent・
fresh 環境の反復を含まない。実 Herdr での確認は workflow 外の事後確認項目に分離済み（Issue 本文）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない（`fcntl.flock` は既存の採用） |
| docs/ARCHITECTURE.md | なし | `kaji watch` のサブコマンドの追加で、構成は変わらない |
| docs/dev/ | なし | 開発ワークフローは変わらない（`workflow_guide.md` の `/watch-triage` の 1 行の説明は「保守者に回す」のままで矛盾しないか implement で確認する） |
| docs/reference/ | なし | 規約の変更はない |
| docs/cli-guides/watch.md / watch.ja.md | あり | 判断用の skill の運用ガイド。`retry` / `recoveries`、`record` の新 outcome、exit 5、通知 `retry`、event の新項目、自動復旧の節 |
| .claude/skills/watch-triage/SKILL.md | あり | 自動復旧の一覧・絶対規則 1・4・手順 2・3 |
| AGENTS.md / CLAUDE.md | なし | CLAUDE.md の表の `/watch-triage` の位置づけは変わらない |
| README.md / llms.txt | 要確認 | `kaji watch` のサブコマンドを列挙していれば追記（implement で grep する） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #545 本文 `## 決定事項` | https://github.com/apokamo/kaji/issues/545 | D1（5 種類・上限・除外）、D2（`--retry-failed-step` だけ、上限は監視の CLI 側）、D3（根拠 3 点）、A1〜A4 |
| kamo2 `/lane-triage` | https://github.com/apokamo/kamo2/blob/main/.claude/skills/lane-triage/SKILL.md | 「自動復旧の一覧（#2126）」: 5 種類・実行の前に呼ぶもの・上限。手順 2 の `continue` の待ち時間（180000 / 360000 ms） |
| kamo2 lane_watch | https://github.com/apokamo/kamo2/blob/main/scripts/ops/lane_watch/models.py ・ `store.py` ・ `recovery.py` ・ `events.py` | `AUTO_LIMITS`（単位と上限）、`append_record` が `flock` の下で数えて追記、`check_until`（7 日）・`check_paths`（checkout の内側）、`apply_wait` / `prune_waits` |
| kamo2 incident #2102 | https://github.com/apokamo/kamo2/issues/2102 | #1872 run `261106000729` の at capacity → 79 分空転 → `StepTimeoutError`。Codex は at capacity の後に入力欄に戻り、`continue` で続く（引用 8） |
| kamo2 #2109 | https://github.com/apokamo/kamo2/issues/2109 | `agent_status` が `idle` になるかは未確認のまま not planned で close（本設計で実機確認した） |
| Herdr の agent lifecycle | `herdr --skill`（herdr 0.9.5 同梱） | 「`idle` and `done` normally indicate the agent is ready for input」「`blocked` means Herdr recognized an approval or question UI」 |
| kaji watch の実装 | `kaji_harness/watch/detect.py`・`store.py`・`service.py`・`commands/watch.py` | `pane_idle` は `working` 以外が 2 周。`records.jsonl` の追記。既存の exit 0/2/3 |
| kaji watch の運用ガイド | `docs/cli-guides/watch.md` | Exit codes 表（0 / 2 / 3）、通知 payload の `kind`、event の key |
| `kaji recover --retry-failed-step` | `docs/cli-guides/failure-recovery.md` § `kaji recover --retry-failed-step` (Issue #540) | 安全ゲートを kaji が判定、拒否は exit 4、子の run を前面で起動して子の exit code を返す |
| 重要判断チェックリスト | `.claude/skills/_shared/critical-decision-checklist.md` | 3 分類と、分類 2・3 の境界に迷ったら分類 3 とする規則（question-answered の除外の根拠） |
