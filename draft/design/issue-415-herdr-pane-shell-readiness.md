# [設計] Herdr pane の shell readiness handshake

Issue: #415

## 概要

`kaji_harness.interactive_terminal_herdr` が新規 pane の shell 初期化完了前に長い
wrapper command を送る競合を、短い execution marker による bounded handshake で防ぐ。

## 背景・目的

### Observed Behavior (OB)

macOS / zsh / Herdr 0.8.2 では、split 直後に送った 1,424 文字の first command が
3/3 で途中欠落し、marker を出力しなかった。Issue 本文「macOS / zsh 直接再現証跡」には、
入力末尾の `END415_N` と後続 `printf` が `recent-unwrapped` に存在しない実ログがある。

### Expected Behavior (EB)

新規 pane の shell が command を実行できることを確認してから wrapper command を一度だけ送る。
確認できなければ wrapper command は送らず、dispatch error として既存の snapshot・ownership
再確認・best-effort cleanup 契約へ流す。これは Issue 本文の完了条件と
`docs/adr/007-interactive-terminal-runner.md` の Herdr pane lifecycle 契約に基づく。

## 再現手順

1. macOS / zsh / Herdr 0.8.2 で fresh pane を `pane split --no-focus` する。
2. split response の pane ID へ、1,300 個の `x`、一意 suffix、結果 marker を含む
   1,424 文字の command を直ちに `pane run` で送る。
3. `pane read --source recent-unwrapped` を確認する。
4. 修正前は 3/3 で末尾と marker が欠落する。修正後は handshake の出力確認後に同じ長さの
   wrapper 相当 command が完全に一度だけ実行される。

## 根本原因

- `_launch_herdr_pane()` の split response は pane 作成だけを確定し、interactive shell が
  command を実行可能であることを確定しない。
- `execute_interactive_terminal_herdr()` は ownership marker 確認後、readiness barrier なしで
  `_run_herdr_pane_command()` を呼ぶため、zsh 初期化中の PTY 入力処理と競合する。
- Herdr backend 初回導入 commit `8ede4e9d` から存在する。既存設計も
  `wait-output --match` が echo 済み command line に一致し得ることを把握しているが、split と
  wrapper dispatch の間には execution barrier を置いていなかった。
- 同根箇所を検索した結果、fresh Herdr pane へ最初の payload を送る production 経路はこの
  wrapper dispatch だけである。tmux backend と既存 pane への通常操作は対象外である。

## インターフェース

### 入力

- 既存の `herdr` executable、split response 由来 pane ID、trusted workdir。
- 公開 CLI、設定、workflow YAML の入力は変更しない。

### 出力

- 正常時: shell が生成した一意 readiness marker を確認した後、既存 wrapper command を一度送る。
- timeout / Herdr error 時: readiness に焦点を当てた `CLIExecutionError` を返し、wrapper command は
  未送信のまま既存 dispatch failure cleanup へ進む。

### 使用例

利用者向け API は不変であり、既存どおり Herdr backend を選択して `kaji run` を実行する。

## 制約・前提条件

- prompt 文字列、shell 名、prompt 表示タイミングには依存しない。
- `pane wait-output` は入力 echo にも一致するため、待機対象の完成 marker を probe command の
  リテラルに含めない。shell が断片を結合して出力して初めて一致する構成にする。
- readiness 待機は既存 Herdr request timeout 内で bounded にする。
- ownership marker は readiness より先に確認し、失敗時 cleanup の対象 pane を確定する。
- verdict timeout、pane ownership、snapshot、cleanup、session resolution の既存契約を変えない。

## 変更スコープ

- `kaji_harness/interactive_terminal_herdr.py`: readiness probe / wait と dispatch 順序。
- `tests/test_interactive_terminal_herdr.py`: probe の argv、execution marker 検証、timeout、dispatch 順序。
- `docs/adr/007-interactive-terminal-runner.md` と CLI guide: shell-ready barrier の契約追記。
- PATH command prefix の重複除去は race の根治に不要なため対象外とする。

## 方針

1. ownership marker 確認後、UUID 由来の一意 token を二分した短い `printf` command を
   `pane run` で送る。完成 token は command line に連続して現れない。
2. `pane wait-output --match <完成 token> --source recent-unwrapped --timeout <bounded ms>` を実行し、
   typed `output_matched` response と exact pane ID を検証する。
3. marker 確認後だけ既存 wrapper dispatch を呼ぶ。probe / wait の失敗は readiness diagnostic を
   付けた `CLIExecutionError` とし、既存の dispatch failure snapshot・cleanup 経路で扱う。
4. wrapper command 自体、verdict polling、session resolution、pane ownership cleanup は変更しない。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| wrapper 前の readiness barrier | bounded handshake を必須化 | Issue 本文「対応案」「完了条件」（人間決定） | shell 実行結果だけに一致する分割 marker と `wait-output` を使用 |
| readiness failure | wrapper 未送信で dispatch error | Issue 本文「Expected behavior」（人間決定） | `CLIExecutionError` に focused diagnostic を付け既存 cleanup 経路へ統合 |
| 公開 IF | 変更しない | ADR 007 と CLI guide の既存 backend 契約 | private helper 内に局所化 |
| handshake timeout | 既存 Herdr command timeout の範囲内で固定 | AI の仮定。内部 timing detail で可逆。review-code と live probe で検査 | wait-output の ms 引数と subprocess 上限の順序を明示 |

## テスト戦略

### 変更タイプ

- 実行時コード変更（過去障害の再発防止）。恒久回帰テストを追加する。

### Small テスト

- readiness probe の完成 marker が command argv に連続して含まれず、入力 echo では成功判定できないこと。
- `output_matched` の type と pane ID を検証し、不正 response を拒否すること。
- timeout / command failure を shell readiness の diagnostic を持つ `CLIExecutionError` として保持すること。

### Medium テスト

- subprocess mock を使い、split → ownership marker → readiness `pane run` → `wait-output` → wrapper
  `pane run` の順序と wrapper 一回送信を検証する。
- readiness failure 時に wrapper を未送信のまま snapshot、ownership 再確認、best-effort close を実行し、
  元の readiness error を close error で置換しないことを検証する。

### Large テスト

- macOS / zsh / Herdr 0.8.2 の live pane で、split 直後の handshake 後に 1,424 文字以上の payload が
  suffix と marker を完全に一回だけ出力することを確認する。
- Linux / bash の環境差は unit/medium の決定論的契約を変えない。CI で Herdr UI session を必須に
  できないため live probe は実装時の実機証跡とし、恒久回帰は mock-based Medium test で保持する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/007-interactive-terminal-runner.md | あり | Herdr pane lifecycle に readiness barrier を追加 |
| docs/ARCHITECTURE.md | なし | backend 境界・artifact 構造は不変 |
| docs/dev/ | なし | workflow 開発手順は不変 |
| docs/reference/ | なし | 公開設定・Python 規約は不変 |
| docs/cli-guides/interactive-terminal-runner.md / `.ja.md` | あり | dispatch / failure 診断契約を利用者向けに同期 |
| AGENTS.md / CLAUDE.md | なし | agent 作業規約は不変 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #415 | GitHub Issue #415 本文 | macOS/zsh の OB、wrapper 未送信 timeout、既存契約維持を要求 |
| Herdr 0.8.2 CLI skill | `herdr --skill` の「Run an ordinary command」 | `pane run` は command と Enter を送り、`wait-output` は既存 snapshot を即時検索する |
| Herdr backend ADR | `docs/adr/007-interactive-terminal-runner.md` | explicit pane ID、ownership 再確認、failure cleanup、rendered snapshot の既存契約 |
| 初回 Herdr backend 設計 | `draft/design/issue-396-feat-herdr-interactive-terminal-backend.md` | `wait-output --match` は shell に echo された command line へ先に一致し得る |
| テスト規約 | `docs/dev/testing-convention.md` | 過去障害の再発防止は恒久テスト対象で、subprocess 結合は Medium |
