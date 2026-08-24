# [設計] interactive terminal runner に Herdr backend を追加する

Issue: #396

## 概要

`kaji` の `interactive_terminal` runner を、既存の tmux に加えて Herdr 0.8.2+ で実行可能にする。
runner mode（`headless` / `interactive_terminal`）と terminal multiplexer backend
（`tmux` / `herdr`）を別の設定軸にし、既定の tmux 挙動を維持する。

主契約は「Herdr pane 内で起動した `kaji run` が、workflow step ごとに Herdr pane を作り、
通常の interactive agent CLI を起動し、filesystem の `verdict.yaml` を完了 authority として
workflow を進める」である。

追加要件として、Herdr pane 内の Codex / Claude Code 自身が release-matched Herdr skill を使って
sibling pane を開き、そこで interactive な `kaji` を起動できる導線を提供する。Claude Code の
`-p` / print mode はこの経路では使用しない。Herdr plugin は人間向け起動 UX の任意拡張として
core backend / agent skill の後に評価する。

> 実装後の確定仕様は `docs/adr/007-interactive-terminal-runner.md` と利用者向け docs に統合済み。
> Herdr 0.8.2 実機で判明した mutation の empty stdout、plain-text `pane read`、PATH pinning、
> live agent matrix の詳細証跡は
> `experiments/herdr-interactive-terminal/reports/2026-08-21-live-herdr-fake-agent.md` を参照。

## 背景・目的

現行 `kaji_harness/interactive_terminal.py` は tmux 固有の pane 作成、配置、marker、transcript、
存命判定、metadata、cleanup を単一 module に持つ。ADR 007 v3 は「広い terminal abstraction を
先に作る価値が薄い」として tmux 単一を選んだが、当時比較した kitty と Herdr は性質が異なる。

Herdr 0.8.2 は pane / agent automation を release-matched CLI と protocol schema として公開し、
caller pane context、pane metadata token、agent detection、session identity integrationを持つ。
2026-08-20 の実機 smoke testでは、非フォーカス split、cwd、foreground process、rendered
scrollback、明示 pane close が成立した。したがって backend 追加の実現性はある。

一方、tmux `pipe-pane` の raw transcript と Herdr の rendered screen / ANSI frame は同一ではない。
また Herdr official skill は `HERDR_ENV=1` を安全境界とし、Herdr 外の agent に focused session を
操作させない。この2点を曖昧にして「tmux コマンドを Herdr コマンドへ置換」してはならない。

## ユーザーストーリー

1. Herdr 利用者として、Herdr pane 内から既存 workflow を interactive agent CLI で実行したい。
2. tmux 利用者として、Herdr 対応後も設定と pane lifecycle を変更せず利用したい。
3. workflow 運用者として、backend にかかわらず `verdict.yaml`、timeout、resume、failure triage の
   意味を同じに保ちたい。
4. Herdr 内の Codex / Claude Code として、caller pane context の範囲で別 pane を開き、
   interactive `kaji` を安全に起動したい。
5. 障害調査者として、transcript が raw stream か rendered snapshot か、truncated かを artifact
   から判別したい。

## verified facts（2026-08-20）

### runtime / protocol

- installed client/server: Herdr 0.8.2 stable、protocol 20、compatible
- split responseは `.result.pane.pane_id` を返す
- `pane process-info` は shell PID、foreground process group、process名 / PID / argv / cwd を返す
- `PaneInfo` は `label`、`tokens`、`agent`、`agent_status`、optional `agent_session` を返す
- `pane report-metadata` は source-scoped title / display label / state label / token を設定し、agent
  lifecycle authorityを奪わない
- protocol schemaの`PaneReadResult`は`text`と`truncated: bool`を持つが、installed 0.8.2の
  `herdr pane read` CLI stdoutはJSON envelopeではなくplain rendered textである。revisionは同じ
  explicit paneへの`pane get`で取得し、CLIがtruncationを公開しないため`null`（unknown）とする
- layout snapshot は各paneの `x` / `y` / `width` / `height` を返す

### smoke test

- `pane split <origin> --direction right --cwd <repo> --no-focus` は期待どおり動作
- foreground processはコマンド実行中 `sleep`、完了後 `bash` に戻った
- `recent-unwrapped` はplain rendered textを返した
- `wait-output --match` はshellにechoされたcommand lineへ先に一致し得る
- terminal observerはbase64 ANSI frame（initial full + subsequent delta）であり、raw PTY streamではない
- 作成した検証paneだけをID確認後にcloseできた

詳細証跡は Issue #396 の「検証記録 1」「調査記録 2」を正本とする。

## インターフェース

### 設定

`ExecutionConfig` に明示 backend を追加する。

```toml
[execution]
agent_runner = "interactive_terminal"
interactive_terminal_backend = "tmux" # tmux | herdr
interactive_terminal_close_on_verdict = true
```

```python
interactive_terminal_backend: Literal["tmux", "herdr"] = "tmux"
```

- defaultは `tmux`。既存configの挙動を変えない
- `agent_runner = "headless"` のときbackend値は保存するが実行には使わない
- unknown valueはconfig load時にfail-fast
- `config.local.toml` overlayのper-key precedenceを既存どおり適用する

run単位 override:

```text
--interactive-terminal-backend tmux|herdr
```

`--agent-runner herdr` は追加しない。runner modeとmultiplexerを混同させないためである。
`auto` は追加しない。tmux inside Herdrのように両contextが存在し得るため、暗黙選択は診断を難しくする。

### 公開 runner IF

`execute_interactive_terminal()` に keyword-only backend を追加する。

```python
def execute_interactive_terminal(
    *,
    step: Step,
    prompt_path: Path,
    verdict_path: Path,
    workdir: Path,
    timeout: int,
    session_id: str | None = None,
    close_on_verdict: bool = True,
    execution_policy: str = "auto",
    backend: Literal["tmux", "herdr"] = "tmux",
) -> CLIResult: ...
```

defaultを `tmux` にし、既存直接呼び出しtestの互換を保つ。runner dispatchはresolved config値を渡す。

### 完了と出力

- backend共通の唯一の正常完了triggerは `verdict_path.is_file()`
- Herdr `idle` / `done`、pane output pattern、shell promptは完了authorityにしない
- `CLIResult.full_output` は従来どおり空文字
- session IDは既存stateへ保存する
- attempt artifactとして `terminal.log` と `pane-metadata.json` を維持するが、metadataにbackendと
  transcript方式を追加する

```json
{
  "backend": "herdr",
  "herdr_version": "herdr 0.8.2",
  "pane_id": "w1:p2",
  "origin_pane": "w1:p1",
  "marker_confirmed": true,
  "transcript_kind": "rendered_recent_unwrapped_snapshot",
  "transcript_available": true,
  "transcript_revision": 7,
  "transcript_truncated": null
}
```

## 実装方針

### 1. backend境界

現在のmoduleを全面framework化せず、public dispatchとbackend固有pane操作を分ける。実装時の
patch namespace互換性を確認した結果、既存tmux helperは元moduleに維持し、Herdr helperだけを
別moduleにする構成で確定した。共通Protocolは導入しない。

```text
kaji_harness/interactive_terminal.py
  - public execute_interactive_terminal
  - backend dispatch
  - 既存tmux preflight / layout / pipe / liveness / metadata / close
  - common terminal diagnostic
  - session ID fallback

kaji_harness/interactive_terminal_herdr.py
  - Herdr CLI JSON wrapper
  - caller context preflight
  - token marker / layout / launch / process-info liveness / read / metadata / close
```

backendをterminal一般、agent一般、plugin一般へ広げない。

### 2. Herdr executable / context preflight

1. `HERDR_ENV == "1"` を要求
2. `HERDR_PANE_ID` 非空を要求しoriginに固定
3. `HERDR_BIN_PATH` が実行可能なら優先し、無ければ `shutil.which("herdr")`
4. `herdr --version` をparseし `>= 0.8.2` を要求
5. `herdr status` でserver running / compatibleを確認
6. `pane current --current` の返すpane IDが `HERDR_PANE_ID` と一致することを確認

Herdr外でbackendを選んだ場合は `HerdrSessionRequiredError` を送出する。これは
`TmuxSessionRequiredError` と同じknown user precondition errorとしてincident作成を抑止するが、
triage comment / result artifact / console errorは残す。

focused pane省略形は使わない。preflight後はoriginまたはresponse由来IDだけを使う。

### 3. Herdr CLI wrapper

- 全呼び出しをargv list + `shell=False` で行う
- query commandはstdout JSONをschemaで期待する最小fieldだけparseする
- unknown fieldは無視する
- nonzero exit、invalid JSON、期待field欠落はoperation名を含む `CLIExecutionError`
- installed Herdr 0.8.2で成功時empty stdoutとなる`pane report-metadata` / `pane run` /
  `pane close`だけは、exit 0 + empty stdoutまたはtyped `ok` JSONを受理する。非空malformed /
  non-`ok`は拒否する
- command timeoutを設け、server不応答でstep timeout全体を消費しない
- raw socket transportをkaji内に実装しない

### 4. pane markerと配置

作成直後、agent起動前にsource `kaji` のmetadata tokenを設定する。

```text
kaji_origin=<origin pane id>
kaji_run=<run id or attempt-stable identifier>
kaji_step=<step id>
```

marker設定またはexact readbackに失敗した場合はfail-loudし、ownership未確認paneを自動closeしない。
markerなしpaneを後続prune対象にしない。

paneを閉じるauthorityは、次の2経路を区別する。

1. **current attempt cleanup**: `pane split` responseから得たpane IDだけを対象にし、作成時の
   `kaji_origin` / `kaji_run`とclose直前のtokenがexact一致する場合だけcloseする。IDまたはtokenを
   再取得できない、不一致、workspace ID欠落の場合はfail-closedで残す。
2. **past-run prune**: origin workspaceを明示した`pane list` responseに含まれ、
   `kaji_origin == origin`と非空の`kaji_run`を持つ右列paneだけをmanaged候補にする。対象pane IDと
   list responseから得たrun tokenをclose直前の`pane get`で再確認し、exact一致する場合だけcloseする。
   markerなし、別origin、originと同じ列、layout欠落、不一致のpaneはpruneしない。run token / layout欠落や
   close直前の不一致は候補をskipしてwarningと`kaji_agent_panes_skipped`へ残し、stepは継続する。

`pane list --workspace <origin workspace>` から `tokens.kaji_origin == origin` のpaneだけを管理対象にする。
layout snapshotの `rect.y` / `rect.x` で右列内の順序を決め、tmux版と同じく最大2枚を維持する。

- 0枚: originを `right` split
- 1枚:既存managed paneを `down` split
- 2枚以上: layout上最古相当の上側managed paneを後段の破壊的検証対象としてcloseし、残存paneを
  `down` split

MVPの非破壊PoCではprune closeを実行せず、候補ID算出まで確認する。実装testはfake CLIでclose argvを検証し、
real pane pruneは後段で明示実施する。

### 5. agent起動

採用方式:

```text
pane split -> report-metadata -> pane run <existing wrapper command>
```

理由:

- existing wrapperのmodel / effort / resume / execution policy mappingを再利用できる
- tmux版とagent argvを一致させやすい
- promptはpath参照の短いinitial promptで、`pane run`のbracketed pasteを利用できる

起動後のlivenessは`pane process-info --pane <pane_id>`で確認する。existing wrapper経路ではHerdr
integration未導入のagentも許容するため、Herdrのagent identity / statusを必須にしない。
`process_info`は次の3状態へ分類する。

- **active**: integer `shell_pid`と非空の`foreground_processes`があり、integer `pid`が
  `shell_pid`と異なるprocessを1件以上確認した
- **confirmed_shell_only**: integer `shell_pid`と非空の`foreground_processes`があり、全要素が
  integer `pid == shell_pid`として検証できた
- **unknown**: `shell_pid` / `foreground_processes`の欠落・`null`・型不正・空list、またはprocess要素の
  `pid`欠落・型不正。optional fieldの未観測をshell-onlyの証拠にしない

`confirmed_shell_only`を2秒間隔で3回連続観測し、かつverdictが無い場合だけ早期終了とする。
`active`と`unknown`は連続回数を0へresetする。`process_info` container自体の欠落・型不正、query non-zero、
invalid JSONは`CLIExecutionError`としてfail-loudし、livenessを確認できないためpaneを自動closeしない。
Herdr agent statusの`unknown`自体も失敗でも完了でもない。

split時にcaller PATHをexplicit envとして渡すだけではinteractive shell startupがPATHを再構成するため、
`pane run`へ渡すwrapper commandにもshell-quoted `env PATH=<caller-path>`を前置する。

比較PoCとして `agent start` + `agent prompt` を1 agentで評価する。これを採用する場合だけagent別argv builderを
Pythonへ移す。PoCなしにwrapperを廃止しない。

### 6. transcript / diagnostic

Herdr backendは `pane read <id> --source recent-unwrapped --lines <limit>` のplain stdoutを
`terminal.log` へ保存する。その後、同じexplicit pane IDへの`pane get`でinteger revisionを確認する。
installed 0.8.2 CLIはstructured truncation flagを公開しないため、`transcript_truncated=null`を保存する。

制約:

- rendered snapshotでありraw PTY bytesではない
- alternate-screenから失われた行は復元できない場合がある
- snapshotより古いalternate-screen履歴の完全性は保証しない
- snapshot read failureはmetadataへavailabilityを記録し、artifact verdictによるmain resultをmaskしない

provider error診断は取得できたtext全体へ既存pattern scanを行う。`transcript_truncated=true` のときは
「known patternなし」を「provider errorなし」と断定しない。

terminal observer frame再構成とportable PTY recorderはMVP対象外。完全raw transcriptが正式対応の必須条件に
なる場合はHerdr upstreamへexport/recording APIを提案する。

### 7. session ID

優先順位:

1. resume inputの既存 `session_id`
2. Claude freshでkajiが生成したlaunch UUID
3. Codexはrendered snapshotのexplicit resume line、続いて既存session store marker fallback
4. Antigravityはadapter capabilityどおり`None`

official Herdr integrationと`agent_session`はoptional enhancementとして初期実装に採用しない。未installでも
既存fallbackだけでfresh/resumeが成立することを実機受入条件とし、Claude/Codexで確認済み。
tmuxのverdict経路にある最大5秒の回収graceとcleanup後の再走査はHerdrには適用しない。Herdrはrendered
snapshot 1回とstore fallbackで解決し、各verdictへの待ち時間追加を避ける。

### 8. close / retain / timeout

- verdict + `close_on_verdict=true`: 作成response由来IDかつmarker一致を再確認し `pane close`
- verdict + `false`: paneを残す。Herdrではagent終了後shellへ戻るためtmuxの`[dead]`表示と同一ではない
- early exit: diagnostic capture後、origin/run ownershipを再確認できたresponse paneだけをcloseする
- timeout: diagnostic / metadata capture後、marker一致paneだけをclose

closeはすべてownership-safeかつbest-effortとする。close失敗はwarningとmetadataの`close_error`へ残すが、
verdict、pane runの元例外、早期終了、`StepTimeoutError`、それぞれのsession解決結果を置換しない。

real timeout / forced close / server stop / session deleteは破壊的検証として後段に分ける。

## 追加要件: agent -> pane -> kaji

### primary: Herdr skill + kaji skill/guide

Herdr 0.8.2 bundled skillを前提に、kaji側は次だけを追加で教える。

1. `HERDR_ENV=1`と非空`HERDR_PANE_ID`を確認し、失敗時は停止
2. `pane split "$HERDR_PANE_ID" --cwd "$PWD" --no-focus` でsibling pane作成
3. responseからpane ID取得
4. `pane run <id> "kaji ..."` でinteractive起動
5. `-p` / print modeを使わない
6. focused paneを暗黙targetにしない
7. 自分が作成していないpaneをcloseしない

skillはHerdr command全文を複製せず、release-matched `herdr --skill` / official Herdr skillへの依存を明記する。

### optional: Herdr plugin

pluginは人間がkeybinding/actionからkajiを開く用途に限定する。

- manifest actionはinvocation contextからfocused pane / cwd / worktreeを取得
- `HERDR_BIN_PATH` でCLIを呼ぶ
- split pane entrypointまたはaction scriptからinteractive kajiを起動
- dynamic issue/workflow選択が必要ならpane内でkaji自身のinteractive selectionを使うか、action引数契約を別設計する
- plugin codeはsandboxされないため、manifestとscriptをreview可能にする

plugin v1はruntime argv pane registrationを持たないため、core runnerの代替にはしない。

## エラー契約

| 事象 | 挙動 |
|---|---|
| backend値不正 | config load / argparseでexit 2 |
| Herdr binaryなし | `CLINotFoundError` |
| Herdr外 | `HerdrSessionRequiredError`、known precondition |
| version < 0.8.2 | `CLINotFoundError`でfail-fast |
| server停止 / incompatible | `CLIExecutionError` |
| JSON不正 / required ID欠落 | `CLIExecutionError`、作成済みpaneが判明する場合のみcleanup |
| marker設定 / exact readback失敗 | ownership未確認paneをcloseせずfail-loud |
| verdict前にforegroundがshellへ復帰 | transcript snapshot + 構造化diagnostic metadata後fail-loud。rendered snapshotの不完全性を明記 |
| verdict前agent消失 | transcript diagnostic付き`CLIExecutionError` |
| process field欠落 / null / 型不正 / 空list | liveness `unknown`。shell-only連続回数をresetし、polling継続 |
| process-info query失敗 / container欠落・型不正 | `CLIExecutionError`。liveness未確認のためpaneを自動closeしない |
| read失敗 | metadataへ記録。verdictがあれば成功をmaskしない |
| timeout | diagnostic capture + safe close後`StepTimeoutError`。close失敗は記録するがtimeoutを置換しない |

## security / safety

- agent入力、workflow値、pathをshell command文字列へ未quoteで連結しない
- wrapper commandは既存 `shlex.join` を再利用
- Herdr CLIはargvで起動しshellを介さない
- current attempt cleanupは作成response由来ID + current run marker一致の両方で検証
- past-run pruneはworkspace-scoped list response由来ID + list/get間で一致するorigin/run markerで検証
- user pane、unmarked pane、別origin paneをprune/closeしない
- `HERDR_ENV`だけでfocused paneを信用せず、`HERDR_PANE_ID`と`pane current --current`を照合
- pluginはユーザー権限でunsandboxed実行されることをdocsに明記
- official integration installはuser configを変更するため自動実行しない

## 重要判断 provenance

人間指定の安全条件は、[作業終了時handoffコメント](https://github.com/apokamo/kaji/issues/396#issuecomment-5359779314)
を具体的な出典とする。このコメントの「今回作成したpane以外をclose/reuseしない」は、resume時の
追加live検証に対する操作境界であり、製品の既存tmux契約であるownership確認済みpast-run pruneを
廃止する決定ではない。製品契約では上記のcurrent attempt cleanupとpast-run pruneを分離する。

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 | 後段の検査先 |
|---|---|---|---|---|
| pane targetの識別 | focusや予測IDへ依存せず、CLI response由来IDだけを使う | [Issue本文](https://github.com/apokamo/kaji/issues/396)とhandoffコメントの人間決定 | current attemptはsplit response、past-run pruneはworkspace-scoped list responseをID sourceにし、両方でexplicit IDを後続commandへ渡す | command argv tests、stateful fake、live pane inventory |
| destructive pane操作のownership | close直前のorigin/run tokenがauthority sourceとexact一致する場合だけcloseする | handoffコメントの人間決定（安全上のone-way door） | current attemptとpast-run pruneのauthorityを上記2経路に分離し、token欠落・不一致・workspace不明はfail-closedにする | ownership mismatch tests、retained-pane live smoke、fake prune test |
| 追加live検証の破壊範囲 | server stop、session delete、force kill、real prune等を無断実行しない | handoffコメントの人間決定 | selection/close契約はfake CLIで検証し、real pruneを未検証として明記する | testsと最終report |
| agentからkajiを起動する経路 | Claude `-p`を使わず、plugin installを必須にしない | [Issue本文](https://github.com/apokamo/kaji/issues/396)の人間決定。plugin自動install禁止はhandoffコメント | real interactive pane commandとrepository skillを採用する | agent-to-kaji live run、利用者docs |
| backend選択と互換性 | backendを明示選択してfail-fastし、既定tmuxを維持する | [Issue本文](https://github.com/apokamo/kaji/issues/396)「主要件」の人間決定 | runner modeとbackendを別軸にする | config / dispatch tests、full regression |
| config surface | `interactive_terminal_backend`とrun単位CLI overrideを追加する | AIのtwo-way-door仮定。既存execution config / override patternが根拠で、review-design / review-codeで検査 | `Literal["tmux", "herdr"]`、tmux default、暗黙fallbackなし | config / parser / dispatch tests |
| Herdr command response | commandごとのinstalled 0.8.2契約を採用する | installed 0.8.2 live responseと`herdr --skill` / schemaによる検証済み事実 | mutation empty-successとquery/read responseを分離し、strict failureを維持 | RED/GREEN tests、installed-Herdr live smoke |

## テスト戦略

変更タイプは実行時コード変更であり、Small / Medium / Largeをすべて定義・実施する。

### 検証資産の保存契約

再実行可能な検証コード、fake executable、fixture、raw command output、環境情報、集計レポートは
`experiments/herdr-interactive-terminal/` 以下へ保存する。Issueコメントには結論だけでなく、実行条件、
再実行command、保存path、観測結果を記載する。

```text
experiments/herdr-interactive-terminal/
  README.md                 # 対象version、安全境界、再実行順
  scripts/                  # 非破壊probe / report生成
  fixtures/                 # sanitized JSON / terminal read fixture
  reports/                  # 日付・version付き検証レポート
```

- secrets、auth token、未redactのagent transcriptはcommitしない
- machine固有pathをfixtureへ固定する場合はplaceholderへsanitizeする
- destructive test scriptは非破壊scriptと分離し、defaultでは実行不能にする
- `experiments/` は配布package外だが品質ゲート対象なので、追加codeはruffを通す
- 製品仕様の正本はdesign / ADR / docs。`experiments/` は再現証跡であり仕様を暗黙定義しない

### Small（自動、非破壊）

- config default / tracked / overlay / invalid value
- CLI override precedence
- Herdr version parse / env preflight / current pane mismatch
- query CLI JSON success / nonzero / invalid JSON / missing field
- metadata / run / closeのempty-success、typed ok、nonzero、malformed / non-ok
- split argv、cwd、no-focus、ID parse
- token marker parse / foreign pane exclusion
- layout rectによるmanaged pane順序とprune候補
- pane read plain text + exact pane get revision / truncation unknown
- foreground processのactive / confirmed shell-only / unknown判定（field欠落・null・型不正・空listを含む）
- Claude/Codex/Antigravityのsession ID fallback
- metadata backend fields
- tmux既存test全回帰

### Medium（fake Herdr executable / attempt file I/O、非破壊）

- stateful fake executableによるpreflight -> split -> marker -> run -> verdict -> metadata -> close
- immediate verdict race
- marker failure時はownership未確認paneをcloseせずfail-loud
- early agent disappearance + provider error diagnostic
- process-info unknownがshell-only確認へ加算されず、後続artifact verdictまでpollingを継続する
- process-info query / container failureでowned paneを誤cleanupしない
- timeout safe close
- close_on_verdict=falseでcloseなし
- runner dispatchがconfig backendを渡す
- incident suppressionがtmux / Herdr known preconditionだけに限定される

### Large / manual（real Herdr、段階実施）

非破壊を先行:

1. Herdr内fake command + verdict artifact
2. Codex fresh単一step、通常完了、rendered log、session fallback
3. Claude fresh単一step
4. Antigravity単一step
5. Herdr内Codex / Claude Codeがsibling paneで`kaji --help`をinteractive起動
6. Herdr内agentが実workflowのkajiを起動

後段の破壊的検証:

1. timeout cleanup
2. verdict前agent終了 / pane close
3. managed pane prune
4. server restart / native session restore
5. named test session stop/delete

各manual testは対象pane/sessionを事前列挙し、Issue #396へ条件・command・結果・cleanupをコメントする。

## 影響ドキュメントと変更スコープ

| 対象 | 変更 |
|---|---|
| `kaji_harness/config.py` | backend設定とvalidation |
| `kaji_harness/commands/parser.py` | run override |
| `kaji_harness/commands/run.py` | override適用 |
| `kaji_harness/runner.py` | resolved backend伝播 |
| `kaji_harness/interactive_terminal.py` | common orchestration / dispatch |
| `kaji_harness/interactive_terminal_herdr.py` | Herdr CLI backend |
| `kaji_harness/errors.py` | Herdr session precondition error |
| `kaji_harness/recovery/` | known precondition suppressionの一般化 |
| `tests/` | Small / Medium tests |
| `experiments/herdr-interactive-terminal/` | 検証code、sanitized fixture、report |
| `docs/adr/007-interactive-terminal-runner.md` | tmux単一決定をv4で改訂 |
| `docs/ARCHITECTURE.md` | backend boundary / artifacts |
| `docs/reference/configuration*.md` | config / precedence |
| `docs/cli-guides/interactive-terminal-runner*.md` | setup / usage / failure recovery |
| `.claude/skills/herdr-kaji-launch/` / `.agents/skills/herdr-kaji-launch` | agent -> pane -> kaji |
| optional Herdr plugin（後段） | human keybinding/action UX |

## 非目標

- tmux backend削除
- headless runner削除
- backend auto detection / auto fallback
- Herdr raw socket protocol clientの実装
- Herdr server lifecycle管理
- Herdr integrationの自動install
- terminal observer frameのVT再構成
- Claude Code `-p` / print mode経路
- Windows native正式対応（kaji全体の対応範囲変更は別Issue）

## 実装順序

1. config / CLI / error contract + tests
2. Herdr CLI wrapper + fake executable tests
3. Herdr pane lifecycle + rendered transcript + metadata
4. runner integration + tmux regression
5. real Codex non-destructive PoC
6. docs / ADR
7. kaji agent skill/guide + agent-originated `kaji --help` PoC
8. optional plugin設計判断
9. destructive manual validation（明示承認後）

## 実装後判定（2026-08-21）

1. existing wrapper + `pane run`方式を採用した。`agent start`はcore backendに使用しない。
2. installed 0.8.2の`pane read`はplain rendered text。Codex alternate-screenのsnapshot取得はPASSしたが、
   古い履歴の完全性は保証せずtruncationをunknownとする。
3. Claude / Codexはfresh session ID保存とresume input exact matchを実機確認した。Antigravity resumeは
   adapter capabilityとして非対応を維持する。
4. early exit / timeout / verdict cleanupとretainは自動testで固定し、timeoutとretainの実機安全経路も確認した。
5. layout y座標によるowned prune選択とclose前exact ownershipはfake CLIで確認した。安全条件によりreal
   prune自体は実施していない。
6. repositoryのagent skill / guideで要件を満たしたためpluginは追加しない。
7. Herdr agent identity / `agent_session`はoptional integrationに依存するため初期必須契約にせず、
   `pane process-info` livenessと既存provider session fallbackを採用した。

## 一次情報

| 情報源 | 設計に使用した根拠 |
|---|---|
| https://herdr.dev/docs/agent-skill/ | `HERDR_ENV=1` guardとrelease-matched `herdr --skill`をagent操作の安全境界にする |
| https://herdr.dev/docs/agent-automation/ | pane / agentの責務分離、creation responseからのID取得、CLI readのrendered text契約 |
| https://herdr.dev/docs/integrations/ | Claude / Codex等のsession identity integrationはoptionalで、state authorityとは別である |
| https://herdr.dev/docs/socket-api/ | process-infoはplatformが公開可能なfieldだけを返すため、optional field欠落をunknownとして扱う |
| https://herdr.dev/docs/cli-reference/ | pane split / run / read / process-info / metadata / closeのCLI surface |
| https://github.com/herdrdev/herdr/blob/v0.8.2/skills/herdr/SKILL.md | 最低対応versionと同じreleaseのagent向けcommand / guardrail |
| https://github.com/herdrdev/herdr/blob/master/docs/next/website/src/content/docs/plugins.mdx | plugin v1の責務とunsandboxed executionの制約 |
| `herdr api schema --json`（installed 0.8.2 / protocol 20） | `PaneProcessInfo` requiredは`pane_id`だけで、`shell_pid` / `foreground_processes`はoptional |
| `docs/adr/007-interactive-terminal-runner.md` | 既存tmux runner契約とHerdr追加後の恒久backend方針 |
