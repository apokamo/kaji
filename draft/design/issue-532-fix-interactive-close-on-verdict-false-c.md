# [設計] close-on-verdict=false で Codex resume step が session lock 画面で停止する問題の修正

Issue: #532

## 概要

Herdr backend の interactive terminal runner で `interactive_terminal_close_on_verdict = false` のとき、
`resume:` step は resume 元の session を開いたままの kaji 所有 pane と並走して起動される。Codex はこの状態で
session lock の確認画面を出して入力待ちになり、step timeout まで進まない。resume step の起動前に、
同じ session を開いている kaji 所有 pane を ownership 確認つきで閉じる。閉じられない場合は、pane を作る前に
理由の分かる `CLIExecutionError` にする。

## 背景・目的

### Observed Behavior（OB）

Issue 本文と [#466 の実機スモークテスト記録](https://github.com/apokamo/kaji/issues/466#issuecomment-6703439735)
（結果 #5、所見 1）による。run `261107145230`、Herdr 0.9.5、Codex CLI 0.167.0、kaji main `6210fbc`。

- codex-fresh（PASS、session `01a1b491-…`）の pane `w1:pG2` は、close-on-verdict=false のため Codex TUI を
  開いたまま残った（`agent_status: idle`、`kaji_step: codex-fresh`）
- 直後の codex-resume pane `w1:pG3` は `This conversation is open in another app / Close it there and press
  R to continue here.` を表示したまま止まった
- codex-resume の `pane-metadata.json` は `kaji_agent_panes_before = ["w1:pG1", "w1:pG2"]`、
  `kaji_agent_panes_pruned = ["w1:pG1"]`、`split_target_pane = "w1:pG2"` だった。resume 元の pane は prune の
  対象にならず、split の基点として残った
- run.log には `StepTimeoutError: Step 'codex-resume' timed out after 600s` が記録された
- 比較: Claude の resume は close-on-verdict=false でも成功した。Codex の resume は close-on-verdict=true
  （既定）なら成功した（run `261107150756`、結果 #4）

### Expected Behavior（EB）

- close-on-verdict の値によらず、Codex の `resume:` step は timeout まで止まらずに完了する。少なくとも、
  kaji 自身が残した pane が原因で agent が入力待ちにならない
- close-on-verdict=false の目的（verdict 後も agent の最終状態を pane で見られる。
  `docs/cli-guides/interactive-terminal-runner.md` § When to use it）と resume が両立する。両立できない
  組み合わせは、timeout ではなく起動前または起動直後の理由が分かるエラーになる
- kaji は Codex の確認画面にキー入力（`r` など）を送らない（#422 と同じ安全方針）

### 再現手順（steps-to-reproduce）

1. Herdr pane 内で、Codex の fresh step（例 `codex-fresh`）と、その session を継ぐ step（例 `codex-resume`、
   `resume: codex-fresh`）を連続させた workflow を用意する
2. `kaji run <workflow> <issue> --agent-runner interactive-terminal --interactive-terminal-backend herdr
   --no-interactive-terminal-close-on-verdict` を実行する
3. codex-fresh は PASS する。codex-resume の pane に lock 画面が出て入力待ちになり、600 秒後に
   `StepTimeoutError` になる

### 根本原因（Root Cause）

調査対象: `kaji_harness/interactive_terminal_herdr.py`（worktree `fix/532`、基点 `6210fbc`）。

1. **kaji は pane がどの session を開いているかを知らない。** pane の ownership token は `kaji_origin` /
   `kaji_run` / `kaji_step` の 3 つだけで（`_build_herdr_marker_argv`）、provider session ID を持たない。
   `HerdrManagedPane` も `pane_id` / `y` / `run_id` だけを持つ
2. **pane の整理は位置（古さ）だけで決まる。** `_launch_herdr_pane` は右列の所有 pane を y 座標の昇順に並べ、
   新しい pane を足した後に 2 枚を超えないぶんだけ上（古い方）から閉じる
   （`prune_count = len(existing) - (_MAX_VISIBLE_AGENT_PANES - 1)`）。直前の step の pane は常に最新なので、
   必ず残る側になる。そのうえ split の基点（`_select_herdr_launch_placement` の最下段）にもなる
3. **resume の前提と pane の寿命がぶつかる。** `session_id` は `execute_interactive_terminal_herdr` に渡るが、
   wrapper のコマンド組み立てにしか使われず、pane の整理には使われない。close-on-verdict=true なら verdict
   直後に pane が閉じられて agent process が終わるので、次の resume は別 process で session を開ける（結果 #4）。
   false なら resume 元の agent process が生きたまま新しい pane で `codex resume <id>` が走る。Codex は同じ
   会話を開いている別 process を検出し、確認画面で人の判断を待つ。kaji の完了条件は `verdict.yaml` の出現だけ
   なので、timeout まで待ち続ける

**いつから**: Herdr backend を追加した #396（ADR 007 v4）からの挙動。pane 保持と最大 2 枚の配置は tmux 時代の
#238（v3）から引き継いでいる。#515（v5）で Herdr 単一になった後も、この判定は変わっていない。どの版でも、
close-on-verdict=false と resume の組み合わせで session がどう扱われるかは決められていなかった。

### 同根の他の壊れ箇所の調査

| 観点 | 調査結果 |
|------|----------|
| **Claude の resume が同条件で成立する理由** | Claude Code は、同じ session を開いている別 process があっても `--resume <id>` で起動を止める確認画面を出さない。#466 結果 #5 で、resume 元の pane が生きたまま resume に成功したことを実測している。ただしこれは Claude Code の現行挙動に頼っているだけで、kaji が保証している契約ではない。同じ会話を 2 つの process が開いたままなので、利用者が古い pane に入力すると会話が分岐する危険もある。本設計は対処を agent 非依存にするので（§ 方針 3）、今後の Claude は「排他 lock を持たない」という前提に頼らなくなる |
| **直前ではない過去の step を `resume:` する場合** | 同梱 workflow の Codex `resume:` step（`docs.yaml` の `doc-verify`、`docs-codex.yaml` の `doc-fix`、`docs-thorough-codex.yaml` の `doc-fix` / `doc-verify`、`dev-thorough-codex.yaml` の `fix-design`）は、どれも resume 元との間に別の step が入る。この場合は右列を 2 枚に保つ prune で、resume 元の pane が先に閉じられることが多い。ただしそれは prune 順序の副作用であって保証ではない。次のどれかで壊れる。(a) prune の close に失敗して `kaji_agent_panes_skipped` に回った。(b) resume 元の pane を作った後に session を継ぐ resume pane が別にある（同じ session を開く pane が 2 枚になる）。(c) 間の step が headless だったり Antigravity だったりして右列の枚数が変わる。本設計は位置ではなく session 一致で判定するので、resume 元がどれだけ前の step でも同じ規則で処理する |
| **前の run が残した pane が、resume 元の session を開いている場合** | `kaji run --from` などで前の run の session state を引き継ぐと、前の run の pane（close-on-verdict=false の残置、Ctrl-C で残った pane、close 失敗で残った pane）が同じ session を開いたままのことがある。`_list_managed_herdr_panes` は `kaji_origin` が一致すれば前の run の pane も管理対象にする。それでも session を知らないので、閉じるかどうかは位置次第になる。origin が別の pane（別の Herdr pane から起動した kaji の pane）は管理対象にすら入らない。本設計では、同じ origin の pane は閉じる。別 origin の pane は閉じずに、起動前のエラーにする（§ 方針 4） |
| **verdict 以外の終了経路** | timeout / shell 復帰の経路は今も best-effort で close するので、close に成功すれば影響はない。Ctrl-C は pane を残す（ADR 007 v4）。resume pane と Claude fresh pane は起動時点で session ID が分かるので、本設計で token を付けた後に残った pane は、後の resume で検出できる。Codex fresh が verdict 以外の経路で終わり、しかも close に失敗した場合は token が付かない。この場合も、回収できた session は未追跡 holder 記録（§ 方針 5）に残すので、後の resume は pane を作る前に検出する |
| **verdict 後の token 付与と close がどちらも失敗する場合（設計レビュー R2）** | close-on-verdict=false で Codex fresh の token 付与が失敗し、続く close も失敗すると、`kaji_session` のない生存 pane が残る。true で close に失敗し、その後の token 付与も失敗した場合も同じ。token 一致の探索には出てこないうえ最新の pane なので prune でも残り、直後の resume が lock 待ちになる。本設計では、この状態を issue 単位の未追跡 holder 記録に書き、後続の resume（同じ run でも別の run でも）が pane を作る前に検査する（§ 方針 5） |
| **verdict 後の token 再付与が ownership を取り戻す経路（設計レビュー R1）** | 既存の `_mark_herdr_pane` は新規 pane 用で、token を書いた**後**に一致を確認する。verdict 後に別 owner に変わった pane へそのまま使うと、旧 origin/run の token を書き戻してしまい、後の close がその pane を自分のものとして閉じられるようになる。本設計では、再付与の前に exact pane の現在の origin/run を確認し、一致しなければ書かない（§ 方針 2） |

## インターフェース

公開 CLI・設定・workflow YAML は変えない。内部関数 `execute_interactive_terminal()` /
`execute_interactive_terminal_herdr()` には、keyword 引数 `untracked_sessions_path: Path | None = None` を 1 つ足す
（§ 方針 5）。runner（`kaji_harness/runner.py`）は、session state と同じ issue ディレクトリのパスを渡す。
`None` のときは未追跡 holder の記録と検査を行わない（既存の単体テスト呼び出しとの互換のため。production の
runner は必ず渡す）。

### 入力

- `execute_interactive_terminal_herdr(..., session_id=<resume 元の provider session ID> | None, close_on_verdict=...)`（既存）
- `untracked_sessions_path`（新規）: `<artifacts_dir>/<issue>/herdr-untracked-sessions.json`。
  `SessionState` の `session-state.json` と同じディレクトリ（`kaji_harness/state.py` の `_state_dir`）
- Herdr の `pane list`（workspace 指定なし。全 workspace の pane とその `tokens` を返す）、`pane get`、
  `pane close`、`pane report-metadata --token`（既存の Herdr CLI 契約の範囲）

### 出力・副作用（変更点）

1. **pane の ownership token に `kaji_session=<provider session ID>` を加える**
   - 起動時点で session ID が分かる pane（resume step の pane、Claude fresh pane）は、最初の marker で付ける
   - Codex fresh pane は、verdict 時に session ID を回収した後で、verdict 後も pane が残る場合
     （close-on-verdict=false、または verdict 後の close に失敗した場合）だけ付ける。付ける前に、exact pane の
     現在の `kaji_origin` / `kaji_run` が自分のものと一致することを確認する。一致しなければ書かない（§ 方針 2）
   - Antigravity と、session ID を回収できなかった Codex fresh pane には付けない
2. **resume step（`session_id` が非 None）の pane を作る前に、session を開いている pane を処理する**
   - `kaji_session == session_id` の pane のうち、`kaji_origin == origin_pane` で `kaji_run` が有効なもの:
     既存の ownership 確認つき close（`_close_owned_herdr_pane`）で閉じ、一覧から消えたことを確認する
   - 閉じられなかった pane があるか、別 origin（または origin pane 自身）の pane が同じ session を開いている場合:
     新しい pane を作らずに `CLIExecutionError` を送出する（step は timeout ではなく即時の失敗になる）
3. **`pane-metadata.json` に診断キーを加える。** 既存の `kaji_agent_panes_pruned` / `_skipped` は意味を変えない
   - `kaji_session_panes_closed`（list[str]）: resume のために閉じた pane ID。resume でない step では空リスト
   - `session_marker`（Codex fresh の verdict 後に token 付与を試みた場合だけ）:
     `{"status": "marked" | "absent" | "ownership_changed" | "failed", "error": str | null}`
   - `untracked_session_recorded`（bool）と、記録に失敗した場合の `untracked_record_error`
4. **未追跡 holder 記録（`herdr-untracked-sessions.json`）を読み書きする**（§ 方針 5）
   - 書く: session を回収できたのに、その session を開いた pane が「`kaji_session` が確認できない状態」で
     生き残りうるとき
   - 読む: resume step が pane を作る前。該当 session の記録があり、その pane がまだ生きていれば、
     ownership を確認できた場合だけ閉じる。閉じられなければ `CLIExecutionError`。pane が消えていれば記録を消す
5. close-on-verdict=false の挙動が変わる。resume step が起動するとき、resume 元の session を開いている
   kaji 所有 pane は閉じられる。会話は resume 側の pane に引き継がれて表示される

### 使用例

```text
# close-on-verdict=false、codex-fresh → codex-resume（resume: codex-fresh）
codex-fresh : pane w1:pG2 起動 → verdict → session S を回収 → close せず kaji_session=S を付与
codex-resume: session_id=S
  → pane list で kaji_session=S, kaji_origin=<origin> の w1:pG2 を検出
  → ownership 確認つきで w1:pG2 を close、一覧から消えたことを確認
  → 通常の prune / 配置（右列 0 枚なら origin の右に split）
  → `codex resume S` が lock なしで起動 → verdict
  pane-metadata.json: kaji_session_panes_closed = ["w1:pG2"]
```

エラーの例（別 origin の pane が同じ session を開いている）:

```text
CLIExecutionError: Herdr pane w9:p4 (kaji origin w9:p1) still has provider session S open;
close it with `herdr pane close w9:p4` and rerun the step.
```

文言の細部は実装で決める。ただし pane ID・session ID・利用者がとる操作を必ず含める。

## 制約・前提条件

- **ownership-safe cleanup（ADR 007 v4）を変えない。** close はすべて既存の `_close_owned_herdr_pane`
  （exact pane を再取得し、origin / run の一致を確認してから close し、一覧から消えたことを確認する）を通す。
  `kaji_session` は「閉じる候補を選ぶ条件」に使うだけで、ownership の判定には使わない。token のない pane、
  `kaji_origin` が別の pane、origin pane 自身は閉じない
- **キー入力を送らない。** kaji が pane に送るのは、従来どおり launcher の child command（`pane run`）1 回だけ。
  lock 画面を検出して `r` / `f` を送るような経路は作らない（#422 の方針）
- **右列は最大 2 枚。** session 一致による close は、既存の prune の前に行う。その後の prune・配置は既存の規則
  のまま（閉じた pane は一覧に出てこない）
- **close-on-verdict=true の挙動を変えない。** verdict 後の close・timeout・shell 復帰時の cleanup は今のまま。
  true でも resume 時の session 検査は走るが、通常は該当する pane がないので副作用はない（Herdr の `pane list`
  が 1 回増えるだけ）
- **token の付け直しは、書く前に現在の ownership を確認し、全 token を再送して確認する。** 書く前の確認は
  設計レビュー R1 への対応で、§ 方針 2 で定める。そのうえで Herdr の `pane report-metadata` は token ごとに
  `--token` / `--clear-token` を持つ（`herdr pane report-metadata --help`、Herdr 0.9.5）。それでも、同じ
  source からの再報告が token を合成するのか置き換えるのかに依存しないよう、`kaji_origin` / `kaji_run` /
  `kaji_step` / `kaji_session` の 4 つをまとめて再送し、`pane get` で 4 つとも一致することを確認する
  （既存の `_mark_herdr_pane` と同じ確認方式）
- **Codex fresh の token 付与に失敗した場合は pane を閉じる。** close-on-verdict=false でも、session を開いたまま
  kaji から識別できない pane を残さない。close も ownership 確認つきで行う。close まで失敗した場合は、
  未追跡 holder 記録に残す（§ 方針 5）。失敗は warning として `pane-metadata.json` の `session_marker` に
  記録する。verdict・session ID は置き換えない（ADR 007 v4 の「cleanup は best-effort。元の結果を置き換えない」
  と同じ扱い）
- **check と write の間の競合**: Herdr には token の compare-and-set がないので、「`pane get` で確認してから
  `report-metadata` で書く」間に別 owner が token を書き換える可能性は残る。この時間幅は、既存の
  `_close_owned_herdr_pane`（`pane get` で確認してから `pane close`）と同じ程度のもので、ADR 007 v4 が既に
  受け入れている範囲にとどまる。書いた後の `pane get` 確認で 4 token すべてが一致しなければ `failed` として扱う
- **session ID を Herdr の metadata に載せる。** session ID はすでに kaji の session state と
  `pane-metadata.json` にあり、Herdr 自身も `agent_session` として表示している（`herdr pane list` の実出力）。
  秘密情報ではない
- **pane を閉じることと lock の解放の順序**: pane の close は、一覧から消えたことの確認までを同期的に行う。
  その後で prune・split・marker・launcher の起動を順に行う。close-on-verdict=true の既存経路も「verdict 後に
  close → 次の step の起動」の順で、Codex resume は成功している（結果 #4）。agent process の終了を待つ追加の
  待機は入れない。実機での確認は merge 後の確認項目で行う
- **残余リスク**: Herdr 操作（token 付与と close）の失敗に加えて、ローカルの
  `herdr-untracked-sessions.json` への書き込み（atomic replace）まで失敗した場合だけ、追跡できない holder が残る。
  このとき `untracked_record_error` と warning を残し、verdict・session ID は置き換えない。issue 単位の記録なので、
  別の issue の session state から同じ session を resume する経路（kaji の通常運用にはない）も対象外。
  旧 kaji 版の pane は下記「後方互換」のとおり扱う
- 後方互換: 本修正より前の kaji が残した pane には `kaji_session` がないので、検出されない。ADR 008 の
  「互換用の fallback を足さない」方針に従い、手当てしない。従来どおり prune の対象にはなる

## 方針

1. **marker の拡張**: `_build_herdr_marker_argv` / `_mark_herdr_pane` が任意の `session_id` を受け取る。
   指定があれば `--token kaji_session=<id>` を加え、確認対象にも含める。`execute_interactive_terminal_herdr` は
   最初の marker に `session_id or launch_session_id or None` を渡す

2. **Codex fresh の verdict 後処理**（verdict 経路だけ。設計レビュー R1 / R2 対応）:

   token 付与は、新しい helper `_report_session_token_if_owned(herdr, pane_id, origin_pane, run_id, step_id,
   session_id) -> SessionMarkerOutcome` で行う。既存の `_mark_herdr_pane` は新規 pane の初回 marker 専用のまま
   にし、verdict 後には使わない。

   ```text
   _report_session_token_if_owned:
       pane = pane get <pane_id>
         pane get が失敗 → 全 workspace の pane list で存在を確かめる
           一覧に pane_id がない     → "absent"（書かない。lock の原因になる process はない）
           一覧も取れない / 一覧にある → "failed"（書かない）
       tokens.kaji_origin != origin_pane or tokens.kaji_run != run_id
                                     → "ownership_changed"（書かない・閉じない）
       report-metadata で 4 token を再送 → pane get で 4 token の一致を確認
         一致                        → "marked"
         不一致 / Herdr エラー       → "failed"
   ```

   ```text
   result_session_id = 既存の解決
   needs_tracking = agent == codex and resume_session 未指定 and result_session_id
   if close_on_verdict:
       close_error = best-effort close（既存）
       if close_error and needs_tracking:
           outcome = _report_session_token_if_owned(...)
           if outcome in ("failed", "ownership_changed"):
               untracked 記録を追加（§ 方針 5）
   else if needs_tracking:
       outcome = _report_session_token_if_owned(...)
       if outcome == "failed":
           close_error = best-effort close（ownership 確認つき）
           if close_error: untracked 記録を追加
       elif outcome == "ownership_changed":
           untracked 記録を追加（閉じない）
   ```

   - `ownership_changed` のときは書かずに閉じない。既存 cleanup が ownership 不一致で close を拒んだ判断を、
     再 marker で覆さない
   - pane が別 owner に変わっていても、中の process は kaji が起動した Codex で、session を開いたままかもしれない。
     そのため untracked 記録には残す。後の resume では閉じずにエラーにする（§ 方針 5）
   - どの分岐も verdict と `CLIResult.session_id` は置き換えない

3. **resume 前の session holder 処理**（`_launch_herdr_pane` に `resume_session_id` を追加し、既存の list /
   prune の前に行う）:

   ```text
   if resume_session_id:
       holders = 全 pane のうち tokens.kaji_session == resume_session_id のもの   # herdr pane list
       for h in holders:
           if h.pane_id == origin_pane or tokens.kaji_origin != origin_pane or not kaji_run:
               raise CLIExecutionError(外部 holder: pane / origin / session / 操作を含む)
           if not _close_owned_herdr_pane(...):   # ownership 不一致 / close 未確認
               raise CLIExecutionError(close できない holder)
           closed.append(h.pane_id)
   untracked 記録の検査（§ 方針 5）
   existing, skipped = _list_managed_herdr_panes(...)   # 以降は既存どおり
   ```

   - **agent 非依存にする**: Codex だけでなく Claude の resume にも同じ規則をかける。理由は次の 3 つ。
     (a) 同じ会話を 2 つの process が同時に開くことを kaji は意図していない
     (b) Claude の成功は provider の現行挙動（排他 lock がない）に頼っているだけで、契約ではない
     (c) agent ごとの分岐を作らない方が単純
     Antigravity は resume に対応していない（`AGENT_CAPABILITIES.supports_resume=False`）ので、この経路に来ない
   - **位置は問わない**: holder は右列かどうかに関係なく、token が ownership を満たせば閉じる
     （位置は prune の古さ判定にだけ使う）
   - holder の処理で例外が出た場合、新しい pane はまだ作っていないので、片付ける pane はない。runner 側では
     通常の dispatch 失敗として扱われる
4. **別 origin の holder を閉じない理由**: 別 origin の pane は、別の kaji 起動（実行中の可能性がある）が
   管理している。ADR 007 の ownership は「自分の origin が作った pane だけを整理する」範囲で成り立っているので、
   それを越えて閉じない。かわりに pane を作る前に失敗させ、利用者が閉じれば再実行できるようにする
   （EB の「理由の分かるエラー」）
5. **未追跡 holder 記録**（設計レビュー R2 対応）

   - **場所**: `<artifacts_dir>/<issue>/herdr-untracked-sessions.json`。session state（`session-state.json`）と
     同じ issue ディレクトリに置く。kaji の resume は issue 単位の session state から session ID を引くので
     （`runner.py` の `self.state.get_session_id(step.resume)`）、同じ issue の後続 step にも、`kaji run --from`
     などの別 run にも届く。前の run が残した pane もこの範囲に入る
   - **形式**: `{"entries": [{"session_id", "pane_id", "origin_pane", "run_id", "step_id", "reason"}]}`。
     `reason` は `marker_failed_close_failed` / `ownership_changed` / `close_failed_marker_failed` /
     `abnormal_exit_close_failed` のどれか。書き込みは同じディレクトリの一時ファイルから `os.replace` で行う
     。既存の `session-state.json` の保存は `write_text` だけだが、この記録は失敗時の fail-safe なので、途中まで書かれた状態を残さないようにする。内容は Pydantic で検証して読む。壊れていれば
     `CLIExecutionError` にする（fail-loud。中身の分からない記録を無視して lock 待ちに進まない）
   - **書く契機**: § 方針 2 の表のとおり。加えて、timeout / shell 復帰の経路で Codex fresh の session を解決でき、
     close に失敗した場合（`abnormal_exit_close_failed`）。設計初版の残余リスクだった経路も、これで閉じる
   - **resume での検査**（§ 方針 3 の session token による処理の後、既存の list / prune の前）:

     ```text
     for e in 記録のうち e.session_id == resume_session_id:
         pane = pane list（全 workspace）から e.pane_id を探す
         なし                                     → 記録を消す（process も消えている）
         あり・token の origin/run が e と一致し、e.origin_pane == origin_pane
                                                  → _close_owned_herdr_pane で close
                                                     成功: 記録を消す / 失敗: CLIExecutionError
         あり・それ以外（別 owner・別 origin・token 不明）
                                                  → CLIExecutionError（閉じない）
     ```

     - エラーメッセージには pane ID・session ID・記録ファイルのパス・利用者がとる操作（`herdr pane close <id>`
       で閉じて再実行する）を含める
     - 利用者が手で pane を閉じれば、次の検査で「なし」になって記録が消えるので、記録ファイルを手で直す必要はない
     - 記録の削除に失敗した場合は warning にとどめ、step は続ける（pane が消えていることは確認済みなので、
       次回の検査でも同じ判定になる）
   - これは旧版 pane 向けの互換 fallback ではない。本修正で新しく生まれる失敗経路のための fail-safe（ADR 008 の
     対象外）

6. **metadata**: `HerdrPaneLaunch` に `session_panes_closed: list[str]` を追加し、`_write_herdr_metadata` で
   `kaji_session_panes_closed` として出力する。`session_marker` / `untracked_session_recorded` /
   `untracked_record_error` も出力する（§ 出力・副作用 3）

### 却下した代替案

| 代替案 | 却下理由 |
|--------|----------|
| 組み合わせ（close-on-verdict=false × resume）を起動前に一律エラーにする | EB は満たす。しかし `codex-fresh → codex-resume` のような直接 resume の workflow が close-on-verdict=false で毎回失敗し、設定の価値が下がる。close で両立できるので、エラーは閉じられない場合に限る |
| `kaji_step == step.resume` の pane を閉じる | step ID は session の識別子ではない。前の run の同じ step ID、resume の連鎖（resume 元 session を resume pane が開いているケース）、RETRY ループで、閉じすぎも閉じ漏れも起きる |
| Herdr が表示する `agent_session` で判定する | Herdr 側の agent 検出に基づく表示用の情報で、version・recorder 経由の起動・agent ごとに出たり出なかったりする。kaji が自分で書いて確認できる token の方が確実 |
| lock 画面を検出してキー入力（`r` / `f`）を送る | Issue の安全方針（キー入力を送らない）に反する。検出の一般化は #422 の範囲 |
| resume の前に右列の所有 pane をすべて閉じる | close-on-verdict=false で残したい無関係な pane まで消える。範囲が広すぎる |
| Codex だけに適用する | 最小侵襲ではある。しかし Claude は provider の現行挙動に頼り続け、会話の分岐の危険も残る。agent ごとの分岐も増える。挙動の変化は「resume 元の pane が resume 時に閉じる」だけで、会話は resume 側の pane に表示される |

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 解決方針 | resume step の起動前に、resume 元の session を開いている kaji 所有 pane を閉じる。閉じられないときは起動前に明示エラーにする | Issue 本文「重要判断 > 解決方針」の AI 推奨案（メンテナが起票を承認。方針自体は人間決定ではない）。two-way door として design review で検査する。EB を満たす 2 案のうち推奨案を採り、代替案（明示エラー）は閉じられない場合にだけ使う | 「session を開いている pane」を `kaji_session` token の一致で識別する。close は既存の ownership 確認つき close を使う。同じ origin の pane は閉じ、別 origin の pane はエラーにする |
| holder の識別方法 | kaji が書いて確認する `kaji_session` token を使う | AI の仮定。根拠: 既存の ownership token と同じ仕組み（ADR 007 v4）で書けて確認でき、step ID や位置よりも正確。review-design / review-code で検査する | 起動時に分かる session は最初の marker に入れる。Codex fresh は verdict 後に pane が残る場合だけ、全 token を再送して確認する |
| 適用範囲（agent） | agent 非依存（Claude の resume にも適用） | AI の仮定。根拠: § 方針 3 の (a)〜(c)。Issue の完了条件「Claude の resume が成立する前提」の調査結果から導いた。review-design で検査する | close-on-verdict=false の Claude の挙動も変わるので、docs に書く |
| 別 origin の holder | 閉じずに、起動前に `CLIExecutionError` にする | AI の仮定。根拠: ADR 007 の ownership の範囲（origin ごとの整理）と、EB の「両立できない組み合わせは理由の分かるエラー」 | エラーメッセージに pane ID・origin・session ID・利用者がとる操作を含める |
| token 付与の失敗 | Codex fresh の token 付与に失敗したら、close-on-verdict=false でも pane を閉じる | AI の仮定。根拠: 識別できない holder を残すと、元の不具合（lock → timeout）が再発する。close-on-verdict=false の「残す」目的より、EB を優先する。review-design で検査する | 失敗は `session_marker` に記録し、verdict・session ID は置き換えない |
| 再付与前の ownership 確認（設計レビュー R1） | verdict 後の token 再付与の前に exact pane の現在の origin/run を確認し、一致しなければ書かず、閉じない | 既存契約 ADR 007 v4「close/prune は exact pane を再取得し origin/run 一致を確認した場合だけ行う」「marker 設定失敗時は unowned pane を閉じない」を、token の書き込みにも広げた。設計レビュー R1 の要求 | 新しい helper `_report_session_token_if_owned` と結果 4 種（marked / absent / ownership_changed / failed）。既存の `_mark_herdr_pane` は初回 marker 専用のまま |
| 二重失敗時の追跡（設計レビュー R2） | token 付与と close がどちらも成功しなかった生存 holder を、issue 単位の `herdr-untracked-sessions.json` に記録する。resume は pane を作る前にこれを検査し、閉じられなければ明示エラーにする | AI の仮定。根拠: Issue の EB「kaji 自身が残した pane による入力待ちを防ぐ」と、ADR 007 v4「cleanup は verdict・session を置き換えない」の両立。Herdr の token が使えない状態でも kaji が自分で持てる記録が要る。session state と同じ issue ディレクトリに置けば後続 step にも別 run にも届く。新しいファイルは診断・安全用の追加ファイルで、外部から読まれず、revert で消せる（two-way door）。review-design / review-code で検査する | 置き場所・形式・書く契機（4 種の reason）・resume での検査手順・壊れた記録は fail-loud・手で pane を閉じれば自動で記録が消える |
| 確認画面への応答 | kaji はキー入力を送らない | Issue 本文「重要判断 > 確認画面への応答」（#422 と同じ安全方針）。Issue の EB にも明記されている | 送るのは従来どおり launcher の `pane run` 1 回だけ。テストで確認する |
| scope | Codex の session lock につながる、kaji が残した pane だけを扱う。確認画面の一般的な検出は #422 で扱う | Issue 本文「重要判断 > scope」 | 残余リスクは、Herdr 操作の失敗に加えて記録ファイルの書き込みまで失敗した場合だけ、と明記した |

one-way door にあたる判断はない。どれも内部挙動と docs の変更で、revert で戻せる。公開 CLI・設定・
workflow YAML・`session-state.json` の schema は変えない。`pane-metadata.json` には診断用のキーを足すだけ。
新しい `herdr-untracked-sessions.json` は kaji 内部だけが読み書きする追加ファイルで、既存のデータを移行しない。

## テスト戦略

### 変更タイプ

- 実行時コード変更（`kaji_harness/interactive_terminal_herdr.py`）

### 実装前 Red 証跡

bug の escape clause を使う。OB を直接示す実ログ（#466 記録の結果 #5: lock 画面の表示、`kaji_agent_panes_pruned`
が resume 元を含まない metadata、`StepTimeoutError ... after 600s`）を、実装前 Red の代わりにする。下の Medium の
再現テストはこの OB に対応する EB（resume 元の pane が split の前に閉じられ、step が verdict で完了する）を
assert する。修正前のコードでも、resume 元の pane が閉じられないので FAIL する。実装時に一度 Red を確認できれば
記録する。

### 実行時コード変更の場合

#### Small テスト（`tests/test_interactive_terminal_herdr.py` の `TestHerdrCommandContract` 方式。helper を patch）

- marker argv: `session_id` を指定すると `--token kaji_session=<id>` が加わり、指定しなければ従来の 3 token
  のままになる
- `_mark_herdr_pane` の確認: `kaji_session` が `pane get` の結果と一致しなければ `CLIExecutionError` になる
- `_launch_herdr_pane(resume_session_id=S)`:
  - 同じ origin・有効な run の holder は、既存の list / prune より前に `_close_owned_herdr_pane` で閉じられ、
    `session_panes_closed` に入る
  - holder の close が未確認（`False`）なら `CLIExecutionError` になり、`pane split` は呼ばれない
  - 別 origin の holder、`kaji_run` が欠けた holder、origin pane 自身は close されずに `CLIExecutionError`
    になり、split は呼ばれない
  - 同じ session の holder が複数あれば全部閉じる
  - `kaji_session` が別の値の pane や token のない pane は、session 処理の対象にならない（既存の prune には従う）
- `_launch_herdr_pane(resume_session_id=None)`: 全 workspace の `pane list` を呼ばず、未追跡 holder 記録も
  読まず、従来どおりの呼び出しになる（fresh step の挙動は変わらない）
- `_report_session_token_if_owned`（設計レビュー R1）:
  - 現在の token が別 origin / 別 run → `ownership_changed`。`report-metadata` は呼ばれない（呼び出し記録で確認）
  - token が欠けている → `ownership_changed`。書かない
  - pane が一覧にない → `absent`。書かない
  - origin/run が一致 → 4 token を再送し、`pane get` で確認できれば `marked`。書いた後の確認が一致しなければ `failed`
- 未追跡 holder 記録（設計レビュー R2）:
  - 追加・削除・読み込みが atomic replace で行われ、壊れた JSON や schema 違反は `CLIExecutionError` になる
  - `untracked_sessions_path=None` では読み書きしない
  - 記録の pane が一覧にない → 記録を消して続行。ownership が一致 → close して記録を消す。close 失敗、
    別 owner、別 origin → `CLIExecutionError` で split しない
  - 別の session ID の記録は対象にしない

#### Medium テスト（OB の再現テスト。`TestExecuteHerdr` と同じ medium 層）

複数 pane の状態（pane ごとの tokens・layout rect・生存）を持つ in-process の fake Herdr を、
`_run_herdr` 境界（Herdr CLI の唯一の subprocess 境界）に patch する。`pane get` / `pane list` / `pane layout` /
`pane split` / `pane report-metadata` / `pane run` / `pane close` / `pane process-info` を状態として扱う。
`pane run` を受けたら verdict と `codex resume <uuid>` を含む `terminal.log` を書く。
`_wait_for_herdr_launcher_start` は既存テストと同じく patch する。

- **再現テスト（OB → EB）**: close_on_verdict=False で `codex-fresh` を実行し、続いて
  `session_id=<回収した ID>` で `codex-resume` を実行する。次を assert する
  - fresh の pane に `kaji_session` が付く
  - resume の `pane split` より前に、fresh の pane の `pane close` が呼ばれ、一覧から消える
  - resume が verdict で完了する
  - `pane-metadata.json` の `kaji_session_panes_closed` に fresh の pane が入る
- **Claude も同じ規則**: claude fresh → claude resume（close_on_verdict=False）で、resume 元の pane が閉じられる
- **close_on_verdict=True の退行防止**: 同じ順序を true で実行すると、fresh の pane は verdict で閉じられる。
  resume 時の session 処理では何も閉じず（`kaji_session_panes_closed == []`）、resume は完了する
- **右列 2 枚の維持**: close_on_verdict=False で 3 step 以上（fresh → 無関係な step → resume）を実行しても、
  所有 pane の生存数が常に 2 以下で、既存の prune 順序（上から）が保たれる
- **ownership-safe**: kaji の token を持たない pane（同じ workspace の利用者 pane）は、どの経路でも close されない
- **キー入力を送らない**: fake への呼び出し記録で、pane への入力にあたる操作が各 pane の `pane run` 1 回だけ
  であることを assert する（Herdr 0.9.5 の入力系 subcommand `pane send-keys` / `pane send-text` が呼ばれない。`herdr pane --help` で確認）
- **token 付与の失敗**: close_on_verdict=False で Codex fresh の再 marker が失敗すると、pane が閉じられ、
  `session_marker.status == "failed"` が記録され、`CLIResult.session_id` は回収した値のまま
- **再付与が ownership を取り戻さない（R1）**: Codex fresh の verdict 前に、fake 上で pane の token を別
  origin/run に書き換える。close_on_verdict=False と True（close が ownership 不一致で拒否される）の両方で、
  次を assert する
  - `report-metadata` が再送されず、pane の token は別 owner のまま
  - pane は閉じられない
  - `session_marker.status == "ownership_changed"`、untracked 記録に追加される
  - 直後の resume が pane を作らずに `CLIExecutionError` になる
- **二重失敗でも lock 待ちにならない（R2）**: close_on_verdict=False で、fake の `report-metadata` を失敗させ
  （token は保存されない）、`pane close` も拒否させる。その後 `session_id=S` で resume を実行する。次を assert する
  - `herdr-untracked-sessions.json` に S と pane ID が記録される
  - resume は `pane split` も `pane run` も呼ばずに `CLIExecutionError` になり、メッセージに pane ID と S が入る
  - 同じ記録ファイルを使う別の `execute_interactive_terminal_herdr` 呼び出し（別 run 相当）でも同じ結果になる
  - その後 fake 上で pane を消して resume を再実行すると、記録が消え、resume は完了する
  - true で close が失敗し、続く token 付与も失敗した場合も、同じく記録される
- **timeout 経路**: Codex fresh が timeout し、session を解決でき、close が失敗した場合、記録に
  `abnormal_exit_close_failed` が入る
- **runner からの受け渡し**（`tests/test_runner_interactive_dispatch.py`）: interactive dispatch が
  `untracked_sessions_path = <artifacts_dir>/<issue>/herdr-untracked-sessions.json` を backend に渡す
- **別 origin の holder**: 別 origin の token を持つ holder がいると、resume step は pane を作らずに
  `CLIExecutionError` になり、その holder は生存したまま

#### Large テスト

- 恒久 Large テストは追加しない。実 Herdr・実 Codex CLI の画面挙動（lock 画面が出ないこと）は CI で再現
  できない（外部の対話 TUI と利用者の subscription 認証が必要）。kaji 側の契約（どの pane をいつ閉じるか、
  キー入力を送らないこと）は、Medium の fake Herdr で全部決定論的に検証できる。provider の挙動との突き合わせは、
  Issue の「ワークフロー完了後の確認項目」（merge 後に実 Herdr と実 Codex で 1 回実行）で行う。これは
  `docs/dev/workflow_completion_criteria.md` の「workflow 内で環境に依存せず再現できない項目は事後確認に分ける」
  に従った配置
- 既存テストの回帰: `pytest tests/test_interactive_terminal_herdr.py tests/test_runner_interactive_dispatch.py`
  と `make check`

### 長時間 acceptance の配置

該当なし。この Issue の受入検証は fake Herdr を使う pytest（数秒）と `make check` だけで、nested `kaji run`・
外部 agent を含む dogfood・fresh 環境での繰り返しは含まない。実 Herdr / 実 Codex での確認は merge 後の
事後確認項目（workflow の外）に分けてある。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/007-interactive-terminal-runner.md | あり | 改訂履歴に v6（Issue #532）を足し、決定を書く: ownership token に `kaji_session` を追加、再付与の前に ownership を確認する、resume 時の session holder の close、別 origin の holder を起動前にエラーにする、未追跡 holder 記録。ownership の範囲は変えないことも明記する |
| docs/cli-guides/interactive-terminal-runner.md / .ja.md | あり | § When to use it（close-on-verdict=false でも、resume 元の pane は resume step の起動時に閉じる）、§ Behavior 2（session 一致の close、未追跡 holder 記録の検査、別 origin のエラー）、§ Pane metadata（`kaji_session_panes_closed` / `session_marker` / `untracked_session_recorded` / `untracked_record_error`）、§ Troubleshooting（holder によるエラーと対処: pane を閉じて再実行すれば記録は自動で消える） |
| docs/ARCHITECTURE.md | なし | runner の dispatch 構造は変えない（内部引数を 1 つ渡すだけ） |
| docs/dev/ | なし | workflow・開発手順は変えない |
| docs/reference/ | なし | 設定キーは変えない |
| AGENTS.md / CLAUDE.md | なし | 規約は変えない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| #466 実機スモークテスト記録 | https://github.com/apokamo/kaji/issues/466#issuecomment-6703439735 | 結果 #5・所見 1: close-on-verdict=false で、codex-resume が lock 画面のまま 600 秒で timeout し、resume 元の pane `w1:pG2` は prune されずに split の基点になった。結果 #4: true なら同じ workflow で Codex resume が成功。Claude resume は false でも成功 |
| Issue #532 本文 | https://github.com/apokamo/kaji/issues/532 | OB / EB / 再現手順 / 完了条件 / 重要判断（解決方針の推奨案、キー入力禁止、scope） |
| ADR 007 v4 / v5 | `docs/adr/007-interactive-terminal-runner.md` | 「pane ownership は source-scoped token `kaji_origin` / `kaji_run` / `kaji_step` で表現する。token は `--ttl-ms` を省略し、置換・明示消去・pane close まで保持する Herdr 契約を利用する。close/prune は exact pane を再取得し origin/run 一致を確認した場合だけ行う」「cleanup は best-effort とし、close 失敗は … verdict や元の例外、解決済み session 情報を置換しない」 |
| interactive terminal runner ガイド | `docs/cli-guides/interactive-terminal-runner.md` § When to use it / § Behavior 2・7 / § Session continuation | close-on-verdict=false の目的（verdict 後も最終状態を見られる。右列は最新 2 枚）。prune は origin/run token を再読してから close し、不確かな pane は skip する。Codex resume は `codex resume <uuid>` を起動する |
| 現行実装 | `kaji_harness/interactive_terminal_herdr.py`（`_build_herdr_marker_argv` / `_mark_herdr_pane` / `_launch_herdr_pane` / `_list_managed_herdr_panes` / `_close_owned_herdr_pane` / `execute_interactive_terminal_herdr`） | token が 3 種類で session を持たないこと。prune が y 座標の古い順だけで決まること。verdict 後の close が `close_on_verdict` だけで決まること（§ 根本原因） |
| runner の resume dispatch | `kaji_harness/runner.py`（`session_id = self.state.get_session_id(step.resume)` と `execute_interactive_terminal(..., session_id=session_id, close_on_verdict=...)`） | resume 元の session ID は既に Herdr backend に渡っている。runner の変更は、未追跡 holder 記録のパスを渡すことだけ |
| session state の置き場所 | `kaji_harness/state.py`（`SessionState.load_or_create` の `artifacts_dir / issue / STATE_FILE`、`_state_dir`） | session state は issue 単位で run をまたいで使われる。未追跡 holder 記録を同じディレクトリに置く根拠。既存の保存は `write_text`（atomic ではない） |
| 設計レビュー（R1 / R2） | Issue #532 の review-design コメント（commit `6b7cbc9` 対象） | R1: 既存 `_mark_herdr_pane` は書いてから確認するので、verdict 後に使うと別 owner の token を書き戻せる。R2: token 付与と close が両方失敗すると、`kaji_session` のない生存 pane が残る |
| agent capability | `kaji_harness/agents.py`（`supports_resume`） | Antigravity は resume に対応しない（`supports_resume=False`）。resume 時の session 処理は Claude / Codex にだけかかる |
| Herdr CLI 0.9.5 の help | `herdr pane report-metadata --help`（ローカルの Herdr 0.9.5 で確認） | `--token <NAME=VALUE>` と `--clear-token <NAME>` が token ごとに用意されている。本設計は合成か置換かに依存しないよう、全 token を再送して `pane get` で確認する |
| Herdr CLI 0.9.5 の `pane list` 実出力 | `herdr pane list`（workspace 指定なし。ローカルの Herdr 0.9.5 で確認） | 全 workspace（`w1` / `w2`）の pane が `tokens`（`kaji_origin` / `kaji_run` / `kaji_step`）付きで返る。Herdr 自身も `agent_session.value` に session ID を表示している（session ID は秘密情報として扱われていない） |
| 同梱 workflow の Codex resume step | `.kaji/wf/official/docs.yaml`（`doc-verify`）/ `.kaji/wf/custom/docs/docs-codex.yaml`（`doc-fix`）/ `.kaji/wf/custom/docs/docs-thorough-codex.yaml`（`doc-fix` / `doc-verify`）/ `.kaji/wf/custom/dev/dev-thorough-codex.yaml`（`fix-design`） | resume 元との間に別の step が入る構成なので、現状は prune 順序の副作用で停止を避けている。`doc-verify`（`resume: doc-review`）は RETRY ループで繰り返されるので、同じ session を開く pane（`doc-review` の pane と、前回の `doc-verify` の pane）が複数になりうる（§ 同根調査 (b)） |
| テスト規約 | `docs/dev/testing-convention.md` | サイズの判定基準（ファイル I/O を伴う fake 結合は Medium）。Large を省略する場合の正当な理由 |
| 完了条件の分離 | `docs/dev/workflow_completion_criteria.md` | 実 Herdr / 実 Codex の確認は、workflow 内で環境に依存せず再現できないので、事後確認項目に置く |
