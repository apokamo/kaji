# [設計] Herdr pane の short launcher dispatch

Issue: #415

## 概要

fresh Herdr pane へ長い wrapper command を terminal input として送る競合を、attempt-local の
executable launcher と短い一回の `pane run` に置き換えて防ぐ。

## 背景・目的

### Observed Behavior (OB)

macOS / zsh / Herdr 0.8.2 では、split 直後の 1,424 文字の first command が 3/3 で途中欠落した。
初回修正は短い marker の実行完了後に長い wrapper を送ったが、公式 dev workflow の
review-code で再発した。run `260829181754` の `terminal.log` では wrapper command が prompt と
混在し、wrapper banner、agent 起動、verdict のいずれも現れなかった。

### Expected Behavior (EB)

長い wrapper payload を fresh PTY input から除外する。pane には attempt-local launcher pathだけを
短い child command として一度送り、wrapper argv は file から渡す。launcher が atomic start markerを
作るまでboundedに待ち、作成・dispatch・start確認に失敗した場合は既存のsnapshot・ownership再確認・
best-effort cleanup契約へ流す。

## 根本原因

- Herdr v0.8.2 の `pane run` は text と Enter を一 request で enqueue するが、text の encoding は
  request 時点の `runtime.bracketed_paste_enabled()` に依存する。
- zsh が marker を実行したことと、次 request の処理時に Herdr が ZLE の terminal mode 更新を
  観測済みであることは同値ではない。初回修正の二 request 間には観測不能な race が残った。
- Herdr v0.8.2 の `PaneInfo` / `process-info` は bracketed-paste / line-editor readiness を公開しない。
  prompt、revision、foreground shell の polling では直接的な barrier を作れない。
- 初回 live probe は marker 後の一般的な長文を検査したが、実 wrapper と同じ PATH 長、artifact
  argv、直後 timing を再現せず、実運用 payload に対する検証が不足した。

## インターフェース

公開 CLI、workflow YAML、provider session、verdict artifact の契約は変更しない。

内部では `prompt.txt` と同じ attempt directory に `herdr-launcher.sh` を作成する。内容は owner-only
executable の POSIX shell script で、継承 PATH と shell-quoted wrapper argv を `exec` する。Herdr
へ渡す command は `<quoted-launcher-path>` のみとし、shell process は liveness 判定用に残す。

## 制約・安全性

- launcher は Kaji が組み立てた trusted path / option のみを `shlex` quote して格納する。
- mode `0700` の temporary file を作り、flush / fsync 後に `os.replace` して部分 file を公開しない。
- launcher path 自体も quote し、space 等を許容する。
- prompt 本文や credential は launcher に複製しない。PATH と artifact path は既存 wrapper command
  と同じ process-visible 情報である。
- ownership marker は launcher 作成より先に確認する。作成・dispatch failure は既存 cleanup 経路で
  扱い、cleanup error で元 error を置換しない。
- launcher start marker確認前のshell-only観測はagent終了として数えない。markerを確定証跡、
  `process-info`をtimeout診断として使い、bounded timeout後はfocused dispatch errorにする。
- fixed sleep、prompt regex、pane revision、`process-info` は正しさの前提にしない。

## 変更スコープ

- `kaji_harness/interactive_terminal_herdr.py`: launcher の atomic materialize と short dispatch。
- `tests/test_interactive_terminal_herdr.py`: content / permission / quoting / lifecycle / failure tests。
- `experiments/herdr-interactive-terminal/scripts/herdr`: stateful fake の launcher 契約対応。
- `docs/adr/007-interactive-terminal-runner.md`: Herdr pane lifecycle 契約。
- `docs/cli-guides/interactive-terminal-runner.md` と `.ja.md`: 利用者向け dispatch / diagnostic。

## 方針

1. 既存 `_build_wrapper_command()` で wrapper argv を shell quote する。
2. attempt directory に `herdr-launcher.sh.tmp` を mode `0700` かつ exclusive create する。
3. `#!/bin/sh`、privateなatomic start marker作成、`exec env PATH=... <wrapper command>`を書き、
   flush / fsync / atomic replaceする。
4. pane へ `<launcher path>` を child command として一回だけ `pane run` する。pane-level `exec` は
   Herdr の `shell_pid` を agent PID に変え、早期 shell 復帰を誤検知させるため使用しない。
5. 旧 readiness marker / `wait-output` の二段階 dispatch は削除する。
6. dispatch後、start markerをboundedに待つ。marker前のshell-onlyはstartup stateとして扱い、
   timeout時は最後のprocess livenessを添えたfocused dispatch errorにする。
7. marker確認後のverdict polling、3回連続shell-only、session resolution、snapshot、cleanupは変更しない。

## 重要判断 provenance

| 判断 | 方針 | 根拠 |
|------|------|------|
| readiness の扱い | 推測せず、長文 PTY input を除去 | Herdr 0.8.2 は必要な terminal state を公開しない |
| dispatch 回数 | short command 一回 | 二 request 間 race を構造的に除去する |
| payload 保管 | attempt-local executable | wrapper が既に参照する artifact directory と lifecycle を揃える |
| file publish | `0700` + fsync + atomic replace | 部分 script の実行と他 user の読み取りを防ぐ |
| failure | 既存 dispatch cleanup 契約 | ADR 007 の ownership / diagnostic 方針を維持する |

## テスト戦略

### Small

- 長い PATH / wrapper argv が launcher 内にあり、pane command に含まれないこと。
- launcher mode が `0700`、temporary file から `os.replace` されること。
- space を含む launcher path が quote され、pane command が短いこと。
- launcher materialize error が focused `CLIExecutionError` になること。
- marker前のshell-only観測を無視し、start marker確認で成功すること。
- start marker timeoutがlast process stateを含むfocused `CLIExecutionError`になること。

### Medium

- stateful fake Herdr が short command から launcher を読み、verdict、snapshot、exact close まで完走する。
- published launcherをshebang経由で実行し、start markerとwrapper側artifactの両方が作られる。
- launcher failure / pane dispatch failure で wrapper を実行せず、owned pane cleanup と元 error を保つ。
- verdict success / timeout / early shell return / provider session の既存回帰を通す。

### Large / live

- macOS / zsh / Herdr 0.8.2 の fresh pane で、実 wrapper 相当の長い payload を launcher に格納し、
  short dispatch 直後でも suffix / marker が複数回欠落なく一度だけ出力されること。
- `kaji-run-verify` に従い、公式 dev workflow を `review-code` から `pr` 手前まで Herdr backend で
  一回実行する。失敗時は再実行せず artifact と原因を Issue に記録する。

## 影響ドキュメント

| ドキュメント | 影響 | 理由 |
|-------------|------|------|
| `docs/adr/007-interactive-terminal-runner.md` | あり | Herdr dispatch lifecycle の変更 |
| `docs/cli-guides/interactive-terminal-runner.md` / `.ja.md` | あり | launcher と failure diagnostic の同期 |
| `docs/ARCHITECTURE.md` | なし | backend 境界と artifact-driven completion は不変 |
| `docs/reference/` | なし | 公開設定と Python 規約は不変 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠 |
|--------|----------|------|
| Issue #415 | GitHub Issue 本文・調査コメント | OB、実 workflow failure、修正計画 |
| Herdr 0.8.2 source | commit `9eb521456ac0d19d3ab3d9d7cea3cca10baa8a4c` の `src/cli/pane.rs`, `src/app/api/panes.rs`, `src/app/api_helpers.rs`, `src/api/schema/panes.rs` | atomic request、live bracketed-paste encoding、公開 state の限界 |
| failure artifact | `.kaji-artifacts/415/runs/260829181754` | wrapper 未起動の terminal / result 証跡 |
| Herdr backend ADR | `docs/adr/007-interactive-terminal-runner.md` | ownership、snapshot、cleanup の既存契約 |
| テスト規約 | `docs/dev/testing-convention.md` | 過去障害の恒久回帰と Medium subprocess 結合 |
