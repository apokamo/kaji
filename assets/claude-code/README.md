# Claude Code ステータスライン

Claude Code のステータスライン表示をカスタマイズする共有アセット。

## ファイル

| ファイル | 役割 |
|----------|------|
| `statusline-command.sh` | ステータスライン描画スクリプト（stdin で受け取った JSON を整形して 1 行出力） |

## 表示内容

左から順に:

- プロジェクト名（`project_dir` の basename）
- プロジェクトルートからの相対パス（ルート直下の場合は省略）
- git ブランチ名（未コミット変更があれば `*` 付き）
- モデル名（effort / context window サイズ付き、例: `Opus4.7(high,1M)`）
- コンテキスト使用量（`使用トークン/使用率%`）
- 5 時間 / 7 日のレートリミット使用率と JST リセット時刻

使用率は 50/70/85% を境にセージ → アンバー → ダスティローズ → muted レッドで色分けする。

## 導入

1. スクリプトを `~/.claude/` に配置する。

   ```bash
   cp assets/claude-code/statusline-command.sh ~/.claude/statusline-command.sh
   chmod +x ~/.claude/statusline-command.sh
   ```

2. `~/.claude/settings.json` に `statusLine` ブロックを追加する。

   ```json
   {
     "statusLine": {
       "type": "command",
       "command": "bash /home/<user>/.claude/statusline-command.sh"
     }
   }
   ```

3. `jq` が必要（JSON パースに使用）。未インストールなら `sudo apt install jq` 等で導入する。

## 注意

`~/.claude/` 配下の設定はプロジェクト横断のため、本アセットは「kaji 用」ではなく Claude Code 共通の個人設定の共有を意図している。各自の環境に合わせて `command` のパスを調整すること。
