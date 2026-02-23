# Worktree パス解決（共通手順）

## 手順

1. **Issue 本文から Worktree 情報を取得**:
   ```bash
   gh issue view [issue-number] --json body -q '.body'
   ```

2. **Worktree の相対パスを抽出**:
   - `> **Worktree**: \`../dao-[prefix]-[number]\`` の形式

3. **絶対パスに変換**:
   ```bash
   MAIN_REPO=$(git rev-parse --show-toplevel)
   WORKTREE_PATH=$(realpath "$MAIN_REPO/../dao-[prefix]-[number]")
   ```

4. **存在確認**:
   - 存在しない場合は `/issue-start [issue-number]` を案内して終了

## 注意事項

- Claude Code では Bash の cwd は毎回リセットされる
- Bash コマンドは毎回 `cd [absolute-path] && command` で実行
- Read/Edit/Write ツールでは絶対パスを使用
