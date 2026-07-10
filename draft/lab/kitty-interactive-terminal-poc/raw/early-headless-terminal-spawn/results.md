# Headless Terminal Spawn — 検証結果

> **Date**: 2026-05-14
> **Status**: 成功（claude / codex 共に dry-run 合格）
> **Related**: [design.md](./design.md)

## TL;DR

- **方式は実装可能**: 別ターミナルウィンドウを spawn し、sentinel ファイル経由で完了通知 → 出力ファイル経由で結果回収のパターンは、`claude` も `codex` も問題なく動作した
- **設計書通りの 4 検証項目（V1〜V5）すべて合格**。所要時間は claude 81s / codex 18s
- **`wt.exe` 経路は claude code セッションのサンドボックス内では window を spawn できなかった**。代わりに WSLg + Linux X terminal（zutty）で検証を完遂。ユーザの通常 WSL シェルから `wt.exe` を呼ぶ運用なら問題なく動くはず（要追加検証）

## 検証環境

- WSL2 (Ubuntu 24.04, Linux 6.6.87.2-microsoft-standard-WSL2)
- WSLg 有効（`DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-0`）
- Windows Terminal: `wt.exe` 2 インスタンス稼働中（ホスト側）
- claude CLI: `/home/aki/.local/bin/claude`
- codex CLI: `/home/aki/.nvm/versions/node/v24.13.0/bin/codex`
- Linux 側 terminal: `/usr/bin/zutty`（既定の `x-terminal-emulator`）

## 実行結果

### claude 経路

```
$ python3 draft/lab/headless-terminal-spawn/spawn.py claude --terminal zutty --timeout 180 --kill-on-done
[driver] run_id=56b6ca25 agent=claude
[driver] run_dir=/tmp/kaji-spawn/56b6ca25
[driver] terminal=zutty PID=60861
[driver] watching sentinel (timeout=180.0s)...
{
  "run_id": "56b6ca25",
  "agent": "claude",
  "elapsed_seconds": 81.01,
  "sentinel_seen": true,
  "status": "OK",
  "output": "HELLO_FROM_CLAUDE_56b6ca25",
  "expected": "HELLO_FROM_CLAUDE_56b6ca25",
  "match": true
}
```

artifacts:

```
/tmp/kaji-spawn/56b6ca25/
├── output.txt   "HELLO_FROM_CLAUDE_56b6ca25"
├── prompt.txt   772 bytes
├── sentinel     (empty, mtime=18:02)
└── status.json  {"agent":"claude","exit_code":0,"started_at":"...","finished_at":"..."}
```

`status.json` の `exit_code:0` は **wrapper.sh が `claude` プロセスの自然終了後に書き込んだもの**。つまり `claude --dangerously-skip-permissions "<prompt>"` は1ターンの応答完了後にプロセスを終了している模様。これは想定外に好ましい挙動（人手で `/exit` 不要）。本実装で安定して再現するか追加検証が必要。

### codex 経路

```
$ python3 draft/lab/headless-terminal-spawn/spawn.py codex --terminal zutty --timeout 240 --kill-on-done
[driver] run_id=06f44691 agent=codex
[driver] run_dir=/tmp/kaji-spawn/06f44691
[driver] terminal=zutty PID=61935
[driver] watching sentinel (timeout=240.0s)...
{
  "run_id": "06f44691",
  "agent": "codex",
  "elapsed_seconds": 18.0,
  "sentinel_seen": true,
  "status": "OK",
  "output": "HELLO_FROM_CODEX_06f44691",
  "expected": "HELLO_FROM_CODEX_06f44691",
  "match": true
}
```

artifacts: 同様に `output.txt` / `sentinel` / `status.json (exit_code:0)` 揃って生成。

### 検証項目チェックリスト

| # | 項目 | 結果 |
|---|---|---|
| V1 | ターミナルウィンドウ spawn | ✅ zutty で確認（`wt.exe` は claude code session 内では再現せず、後述） |
| V2 | spawn 内で claude が起動・応答 | ✅ output.txt が期待値で生成 |
| V3 | sentinel を driver が検知 | ✅ polling 1s 間隔で 81s / 18s 後に検知 |
| V4 | output.txt が driver で取得可能 | ✅ |
| V5 | codex も同じ仕組みで動作 | ✅ |

## 想定外 / 知見

### 1. `wt.exe` は claude code session 内では window を開けない

`wt.exe new-window wsl.exe bash -lc "..."` を `subprocess.Popen` で起動しても、claude code セッションの bash 環境からは新規 WindowsTerminal インスタンスが起動しなかった（既存 `tasklist.exe` でプロセス数の増加なし、wrapper.sh も未実行）。

仮説:
- claude code の sandbox（bwrap）が Windows interop の GUI session 連携を阻害している
- もしくは wt.exe が detached process として spawn される際にセッション ID 関連で失敗

**対応**: 検証は zutty に切替。本番運用（ユーザの通常 WSL シェルから kaji ハーネスを起動）では wt.exe 経路を再検証する必要がある。spawn.py には `--terminal {wt,zutty}` で両対応済み。

### 2. zutty は font 引数指定が必須

`zutty -e ...` のみだと `No suitable files for '9x18' found!` で abort。`-font DejaVuSansMono` を明示すれば動作。spawn.py に hardcode 済み。

### 3. agent が prompt の指示通り output / sentinel を書く堅牢性

PoC の試行ではいずれも一発成功。ただし試行数は各1のため、production では:
- 「sentinel が touch されなかったが output.txt は書かれた」
- 「output.txt にゴミが混入」

などの異常系を観察する追加 dry-run が必要。期待値マッチを strict にするとリトライ要否の判断材料になる。

### 4. claude の自然終了挙動

`claude --dangerously-skip-permissions "<prompt>"` が単発ターン完了後に exit する挙動を観測。本来 interactive モードはユーザ入力待ちで残るはずなので、これは:
- stdin が tty だが入力が来ない場合の autoexit
- もしくは prompt 引数が消化されきった時の auto-exit

のいずれかと思われる。再現性が確認できれば、ターミナル close / kill 処理を簡略化できる。

### 5. `--kill-on-done` の効き目

`proc.terminate()` は zutty に SIGTERM を送るため、子の bash → claude/codex が SIGHUP で連鎖終了する。ウィンドウは閉じる。wt.exe 経由だと wt.exe の PID は spawn 直後に exit するため、`proc.pid` を kill しても効かない（実 window は別 PID）。本実装では wt.exe 配下の bash/agent PID を `pgrep -f` 等で探す必要がある。

## PoC 実装で確定した contract

| 要素 | 仕様 |
|---|---|
| run_dir | `/tmp/kaji-spawn/<run_id>/`（run_id = uuid hex 8 桁） |
| 完了通知 | `<run_dir>/sentinel`（空ファイル touch） |
| 結果出力 | `<run_dir>/output.txt`（agent が書き込む plain text） |
| ステータス | `<run_dir>/status.json`（wrapper.sh が書き込む。`exit_code` / `started_at` / `finished_at` / `agent`） |
| プロンプト | `<run_dir>/prompt.txt`（driver が事前生成。本文 + 完了指示テンプレ） |
| 完了指示 | プロンプト末尾に「output.txt に書け / sentinel を touch せよ」と明示 |
| ターミナル | `wt.exe` / `zutty` 両対応（`--terminal` で選択） |
| permission | claude: `--dangerously-skip-permissions` / codex: `--dangerously-bypass-approvals-and-sandbox`。WSL2 サンドボックス前提 |

## 次フェーズへの提言

### 短期（本実装着手前にもう一度回す dry-run）

1. **wt.exe 経路の再検証**: 通常 WSL シェル（claude code 外）から `spawn.py claude --terminal wt` を実行し、window が開くこと・wrapper.sh が走ることを確認
2. **エラーパス検証**: 故意に sentinel を書かないプロンプトで timeout 経路を確認
3. **複数試行**: claude / codex を各 5 回ずつ流し、instruction 遵守率を測定
4. **長文プロンプト / 日本語混在**: シェル引数経由でのエスケープ問題がないか

### 中期（kaji ハーネスへの組み込み設計）

- `LocalAgentRunner`（または既存 runner）に `mode=spawn-terminal` を追加
- run_dir を kaji の cache ディレクトリ配下（例: `.kaji/cache/agent-runs/<run_id>/`）に統一
- inotify（`watchfiles`）への置換で polling 廃止
- timeout 時の kill 手順を OS/terminal 別に確定
- workflow YAML から `agent.runtime: spawn-terminal` のように選択可能にする

### 中期（agent 固有最適化）

- **claude**: Stop hook を `.claude/settings.json` に注入 → 「ターン終了」通知を sentinel と独立に取れる。プロンプトの完了指示は冗長化として残す
- **codex**: `-o, --output-last-message <FILE>` 相当を interactive モードで使えるか調査。使えれば output.txt の agent 側書き込みを置き換え可能

### 未解決事項

- claude が単発プロンプトで autoexit する条件の特定（観測は1試行のみ）
- wt.exe で spawn 後の実 PID を確実に追跡する方法
- ライセンス的に `claude --dangerously-skip-permissions` が `-p` と同じ扱いにならないかの最終確認（恐らく interactive 扱いだが要 doublecheck）

## ファイル一覧

| ファイル | 役割 |
|---|---|
| `design.md` | 設計書 |
| `spawn.py` | driver（Python） |
| `wrapper.sh` | spawned terminal で実行する shell |
| `prompt_template.txt` | プロンプト本体テンプレ |
| `results.md` | 本書 |

## 追補: 2回目検証（フォント + workspace trust 修正後）

### 修正内容

| # | 問題 | 修正 |
|---|---|---|
| F1 | zutty default font (`9x18` bitmap) で日本語が豆腐 | `-font HackGenConsoleNF -dwfont HackGenConsoleNF -fontpath ~/.local/share/fonts` を指定。HackGen は Windows 側の `%LOCALAPPDATA%\Microsoft\Windows\Fonts` から `~/.local/share/fonts/` にコピー（fc-cache 実行） |
| T1 | fresh `/tmp/kaji-spawn/<run_id>/` を cwd にすると claude が workspace trust dialog で停止（`-p` モード以外では skip 不可、ユーザ設定 `skipDangerousModePermissionPrompt` も無効） | wrapper.sh の cwd を **既に trusted な dir**（`$HOME/dev/kaji/main`、無ければ `$HOME`）に変更。`KAJI_TRUSTED_CWD` で override 可。agent には絶対パスでファイル操作を依頼（`--dangerously-skip-permissions` があれば cwd 外への書き込みも可） |
| T2 | `--add-dir <fresh-dir>` を渡すと別の trust dialog がかかる挙動を観測 | `--add-dir` を撤去。run_dir は prompt 内の絶対パスで指示 |

### 再検証結果（修正後）

| run_id | agent | elapsed | match | 備考 |
|---|---|---:|:---:|---|
| `ee686901` | claude | **4.0s** | ✅ | trusted cwd 効果で大幅高速化 |
| `bc26d7f1` | codex | 17.0s | ✅ | 前回 18s と同等 |

claude が 81s → 4s に短縮したのは、初回の trust dialog 関連の onboarding/init コストが消えたためと思われる（cwd が既知 trusted dir なので claude が project session 初期化を高速 path に乗せる）。

### 観察された claude の挙動

- **sentinel は agent のターン途中（Bash tool 実行時）に作られる**。sentinel 出現後も agent プロセスは対話モードで生存（`read` 入力待ち）。よって driver からの完了検知と agent プロセスの終了は独立した事象として扱う必要がある
- 前回検証の `status.json: exit_code=0` は、検証時に `--kill-on-done` で zutty を kill した際、wrapper.sh の trap が動作する間に `claude` が SIGHUP で先に死に、その exit code が 0 として保存されたものと思われる（再観測で agent は sentinel 後も生きていることを確認）
- `cwd=fresh-dir` または `--add-dir=fresh-dir` を含むと workspace trust dialog で停止し、入力なしで永遠に sleep する（`stat=Sl+`, `wchan=do_epoll_wait`, CPU time≒0）
- ユーザ設定 `~/.claude/settings.json` の `skipDangerousModePermissionPrompt` / `skipAutoPermissionPrompt` / `permissions.defaultMode=auto` は workspace trust dialog には影響しない

### contract への含意

- sentinel 出現 = タスク完了。`status.json` は agent プロセス終了後にしか書かれないので、本実装では sentinel + output.txt をプライマリ契約に、status.json は補助とする
- driver は sentinel 検知後に **agent をどう終わらせるか** を明示する必要がある:
  - `--kill-on-done`: 強制 kill（PoC で採用）
  - 何もしない: window が残り、ユーザが手動 close（PoC default）
  - agent に「sentinel touch → 即終了せよ」と指示: claude は `/exit` slash command 相当が必要、codex も同様。要追加検証

### 確定 contract（更新）

| 要素 | 仕様 |
|---|---|
| **wrapper cwd** | `KAJI_TRUSTED_CWD`（既定: `$HOME/dev/kaji/main` → なければ `$HOME`）。**fresh run_dir は cwd にしない** |
| **agent オプション** | claude: `--dangerously-skip-permissions`（`--add-dir` 不要）／ codex: `--dangerously-bypass-approvals-and-sandbox` |
| **prompt** | agent への path 指定は **絶対パス**で記述（run_dir 配下も `/tmp/kaji-spawn/<id>/output.txt` の形） |
| **zutty 起動** | `-font HackGenConsoleNF -dwfont HackGenConsoleNF -fontpath /home/aki/.local/share/fonts` 固定 |
| **font 配置** | Windows 側 `HackGenConsoleNF-Regular/Bold.ttf` を `~/.local/share/fonts/` にコピー + `fc-cache -f` 必要（初回 1 回） |

### font セットアップ（初回のみ）

```bash
mkdir -p ~/.local/share/fonts
cp "/mnt/c/Users/<user>/AppData/Local/Microsoft/Windows/Fonts/HackGenConsoleNF-Regular.ttf" ~/.local/share/fonts/
cp "/mnt/c/Users/<user>/AppData/Local/Microsoft/Windows/Fonts/HackGenConsoleNF-Bold.ttf"    ~/.local/share/fonts/
fc-cache -f ~/.local/share/fonts
```

HackGen が Windows 側にもない場合は `sudo apt install fonts-vlgothic` 等を代替に。spawn.py の `ZUTTY_FONT` を切り替える。

## 追補2: zutty CJK バグ → mlterm 移行

### zutty で観測したバグ

スクリーンショット検証で2つの問題:
1. **CJK 文字間が広い**: 各 Japanese char に約 1 cell 分の空白が挿入される。`-font` と `-dwfont` に同一 font (`HackGenConsoleNF`) を渡したため、zutty は CJK を「2セル幅、ただし glyph は左セルのみ描画」と扱い、右セルが空白に
2. **テキスト欠落**: 上記の幅誤認の結果、行 wrap 位置が狂い、文字列が途中で切れる

zutty は本来 bitmap font 前提の軽量ターミナル。CJK 対応は限定的で、TTF + fontconfig 経由のフル CJK は弱い。

### mlterm に切替して解決

`sudo apt install mlterm` (565KB) → spawn.py に `--terminal mlterm` 追加。fontconfig 経由で Noto CJK TTC を自動検出するので、font 設定不要。

| run_id | agent | elapsed | match | terminal | 備考 |
|---|---|---:|:---:|---|---|
| `b0bc5f00` | claude | 5.0s | ✅ | mlterm | 日本語表示正常（要ユーザ最終確認） |
| `c8151b4f` | codex | 15.0s | ✅ | mlterm | 同上 |

### terminal 選択ガイド（現時点）

| terminal | CJK | 推奨度 | 用途 |
|---|---|---|---|
| `wt.exe` (Windows Terminal) | ◎ Windows font system | **本番** | 通常 WSL シェルから kaji ハーネス起動時。claude code session 内では window 開けず不可 |
| `mlterm` | ◎ fontconfig + CJK 特化 | **dry-run / 開発** | claude code session 内など WSLg 経由でも安定 |
| `zutty` | × 幅処理バグ | 非推奨 | CJK 含む prompt では使わない |

`spawn.py` は3つすべて `--terminal {wt,zutty,mlterm}` で選択可能。CJK を含む agent prompt なら **mlterm or wt** を選ぶ。

## 追補3: emoji / symbol font の最終調整

### 観測した残課題

xfce4-terminal で日本語は正常になったが、claude code TUI フッターの bypass indicator `⏵⏵` (U+23F5) が豆腐表示。

### 切り分け

```bash
fc-list :charset=23f5 | wc -l   # → 0 (WSL に U+23F5 をカバーする font が存在しない)
```

U+23F5 は「Miscellaneous Technical」ブロックの media-control 記号。一般的な CJK / 絵文字 font のカバー外。

### 修正

```bash
sudo apt install -y fonts-noto-core   # ~50MB / Noto Sans Symbols2 含む
fc-cache -f
fc-list :charset=23f5   # → /usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf
```

軽量代替: `fonts-symbola` (~3MB) も同じ codepoint をカバー。

### 最終 dry-run

| run_id | agent | terminal | elapsed | match | 視覚確認 |
|---|---|---|---:|:---:|:---:|
| `2d33fc08` | claude | xfce4 | 5.0s | ✅ | 日本語 + emoji + ⏵⏵ すべて正常（ユーザ確認済み） |

### 確定 font セットアップ（dry-run / 開発環境）

| 用途 | font / package | 入手元 |
|---|---|---|
| ASCII + 日本語 | HackGen Console NF | Windows 側 `%LOCALAPPDATA%\Microsoft\Windows\Fonts` → `~/.local/share/fonts/` にコピー + `fc-cache` |
| color emoji | Noto Color Emoji | `sudo apt install fonts-noto-color-emoji` |
| symbol / 媒体制御記号 (⏵, ⏸, ⏩ 等) | Noto Sans Symbols2 | `sudo apt install fonts-noto-core` |
| terminal default font 指定 | `~/.config/xfce4/terminal/terminalrc` の `FontName=HackGen Console NF 12` | — |

これで claude code / codex の TUI を WSLg + xfce4-terminal 上で完全表示可能。

## 追補4: wt.exe 経路の最終検証（通常 WSL シェルから）

### 当初の障害

通常 WSL シェルから `spawn.py --terminal wt` を実行すると、Windows Terminal は新規ウィンドウを開くものの、その中で wt.exe が以下のエラーを表示:

```
['new-window --title kaji-spawn-claude-<id> wsl.exe bash -lc "/home/aki/.../wrapper.sh /tmp/.../<id> claude"' の起動時にエラー 2147942402 (0x80070002) が発生しました]
指定されたファイルが見つかりません。
```

### 原因

**`new-window` は wt.exe の subcommand では存在しない**。正規 subcommand は `new-tab` / `split-pane` / `focus-tab` / `move-focus` のみ。新規ウィンドウ生成は `-w new`（または `--window new`）グローバルオプションで指示する。

`new-window` を渡すと wt.exe は **bare-command mode** にフォールバックし、続く全 args を「`new-window --title ... wsl.exe ...` という名前の実行ファイル」として ShellExecute に渡す → 当然 file not found で 0x80070002 失敗。

参考: 当初 claude code session 内で wt.exe spawn が「無音で何も起きない」と観測したのも、bare-command mode + sandbox の組み合わせで window が開けず silently abort していた可能性が高い（sandbox 起因と判断したのは早計だった）。

### 修正

`spawn.py` の wt 分岐を以下に変更:

```python
cmd = [
    WT_EXE,
    "-w", "new",          # 新規ウィンドウ
    "new-tab",             # 正規 subcommand
    "--title", f"kaji-spawn-{agent}-{run_dir.name}",
    "wsl.exe", "bash", "-lc",
    f"{WRAPPER} {run_dir} {agent}",
]
```

### 検証結果

通常 WSL シェルから `python3 draft/lab/headless-terminal-spawn/spawn.py claude --terminal wt --timeout 180` 実行 → **正常動作**（ユーザ確認済み）。

## 追補5: kitty を 4 環境統一候補として検証

### 動機

xfce4-terminal は Linux / WSL2 / Mac VM 内 Ubuntu の 3 環境はカバーするが、**macOS native は別系統**。kitty なら同一バイナリ（apt / brew / DMG）で 4 環境すべて動くため、`LocalAgentRunner` の I/F が単純化できる。

### セットアップ

```bash
sudo apt install -y kitty
```

`~/.config/kitty/kitty.conf` に以下を配置（font は xfce4 と同じ HackGen + Noto Color Emoji + Symbols2、追加で X11 backend 強制）:

```conf
font_family      HackGen Console NF
bold_font        HackGen Console NF Bold
font_size        12.0

symbol_map U+1F300-U+1F5FF,...,U+1FA00-U+1FAFF Noto Color Emoji
symbol_map U+2300-U+23FF Noto Sans Symbols2
symbol_map U+25A0-U+25FF,U+2600-U+26FF,U+2700-U+27BF,U+2B00-U+2BFF Noto Sans Symbols2

# WSLg では Wayland だと window decoration が出ない → X11 強制
linux_display_server x11
hide_window_decorations no
confirm_os_window_close 0
```

kitty は **fontconfig 自動フォールバックを使わない**（明示的な `symbol_map` が必要）— xfce4-terminal の pango とは設計思想が違うので最初は化けるが、symbol_map で範囲指定すれば完全に解決。

### 検証結果

| run_id | agent | terminal | elapsed | match | 視覚確認 |
|---|---|---|---:|:---:|:---:|
| `0dd3a3d7` | claude | kitty | 6.0s | ✅ | 日本語 + emoji + ⏵⏵ + window decoration すべて OK（ユーザ確認済み） |
| `e9607aa9` | codex | kitty | 20.0s | ✅ | 同上 |

### terminal 選択ガイド（最終形）

| terminal | Linux native | WSL2 | Mac VM (Ubuntu) | Mac native | CJK + emoji | 推奨度 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **kitty** | ✅ | ✅ | ✅ | ✅ | ◎ (symbol_map 設定後) | **本命** |
| xfce4-terminal | ✅ | ✅ | ✅ | ❌ | ◎ (pango 自動) | 副本命 |
| wt.exe | × | ✅ | × | × | ◎ | WSL 限定の代替 |
| mlterm | ✅ | ✅ | ✅ | ❌ | △ (emoji 色付き弱) | 軽量代替 |
| zutty | ✅ | ✅ | ✅ | ❌ | × (CJK 幅バグ) | 非推奨 |

**結論**: ハーネスの spawn 経路は **kitty を default にして 4 環境統一**、wt.exe / xfce4-terminal は environment-specific fallback、という構成が最も I/F が単純。

## PoC 完了条件

- [x] kaji ハーネスから別ターミナルを spawn できる（wt.exe / mlterm / xfce4-terminal / kitty / zutty 全対応）
- [x] その中で claude code を起動し返り値（output.txt）を取得できる
- [x] codex も同様の経路で動作する
- [x] 完了通知（sentinel）の検知機構が動く
- [x] 視覚的に日本語・絵文字・記号がすべて正しく描画される
- [x] wt.exe 経路を通常 WSL シェルから検証済み

**PoC ステータス: 完遂**

## 再現コマンド

```bash
cd /home/aki/dev/kaji/main

# 推奨: mlterm 経路（CJK 正常表示）
python3 draft/lab/headless-terminal-spawn/spawn.py claude --terminal mlterm --timeout 180
python3 draft/lab/headless-terminal-spawn/spawn.py codex  --terminal mlterm --timeout 180

# wt.exe 経路（要: claude code 外の通常 WSL シェルから実行）
python3 draft/lab/headless-terminal-spawn/spawn.py claude --terminal wt --timeout 180

# zutty 経路（CJK 非推奨、ASCII のみのプロンプトなら可）
python3 draft/lab/headless-terminal-spawn/spawn.py claude --terminal zutty --timeout 180
```
