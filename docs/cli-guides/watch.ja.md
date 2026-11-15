# series の監視（`kaji watch`）

[English](watch.md)（英語正本）

`kaji watch` は series に投入した Issue を LLM なしで見張る。毎周、事実から各 Issue の `state:*` ラベルを
計算し直し、GitHub との差分だけを直す。run の停止を検知すると、報告コメントを投稿し、外部へ通知し、
判断用の skill `/watch-triage` を Herdr の新しいタブで起動する。

**opt-in** の機能で、tracked の `.kaji/config.toml` に `[watch]` を宣言した repo でだけ動く。宣言しない repo では
`issue-close` を含む既存の挙動は変わらない。run と series の事実は [`kaji status --json`](run-status.ja.md) と
同じ経路（`kaji_harness.runstate.query`）だけから取得し、kaji の内部ファイルを直接読まない。

## 設定

```toml
[watch]                                         # table があること自体が opt-in の宣言（空でもよい）
notify_command = ["scripts/notify-discord.sh"]  # 任意の argv。未設定なら通知しない
```

- 読むのは tracked の `.kaji/config.toml` だけ。`config.local.toml` の `[watch]` は有効にも無効にもせず、警告して無視する。
- `provider.type` は `github` であること。
- 契約の 6 ラベル（`state:planned` / `state:running` / `state:aborted` / `state:done` / `state:hold` /
  `state:needs-maintainer`）が GitHub にあること。kaji はラベルを作らない（`.github/labels.yml` から同期する）。
- 周期・しきい値は定数（`kaji_harness/watch/constants.py`）: 180 秒ごと、`working` 以外が 2 周で `pane_idle`、
  進み具合が 90 分変わらなければ `log_stall`、対象 0 件が 2 周続けば終了。

## コマンド

```bash
kaji watch start                 # `kaji-watch` タブで監視の loop を起動（冪等）
kaji watch status [--json]       # 監視対象の Issue を表示（読むだけ）
kaji watch once [--dry-run]      # 1 周分を実行。--dry-run は計算結果だけを出し何も書かない
kaji watch record --event FILE --outcome OUTCOME [--comment-url URL] [--summary TEXT]
kaji watch events [--json]       # event と判断の記録（読むだけ）
```

すべて `--workdir DIR` を受け付ける。`stop` はない。`start` と `once`（`--dry-run` なし）は Herdr の中
（`HERDR_ENV=1` と `HERDR_PANE_ID`）でだけ動く。`OUTCOME` は `handoff` / `still-working`（`pane_idle` 専用）/
`not-stalled`（`log_stall` 専用）/ `recovered`。

| 終了コード | 意味 |
|------|---------|
| 0 | 正常（2 周続けて対象が 0 件になって終えた場合、動作中の `start` を含む） |
| 2 | 引数の誤り・config がない・`[watch]` がない・ラベルがない・github 以外の provider・Herdr の外での `start` / `once`・lock 保持中の `once`・不正な event ファイルや outcome |
| 3 | 予期しないエラー |

ラベルの契約・event の種類・通知の JSON（schema v1）・`issue-close` の動作の詳細は英語正本
[watch.md](watch.md) を参照する。
