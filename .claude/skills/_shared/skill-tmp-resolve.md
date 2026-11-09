# skill 用一時ディレクトリの解決（共通手順）

skill の手順で一時ファイル（Issue 本文の編集用ファイル、follow-up 本文、計測結果など）を使うときの置き場を
解決する。ホスト共有の `/tmp` 直下の固定名は、同時に流れる別 Issue の workflow と衝突するため使わない（Issue #548）。

| 起動経路 | 解決先 |
|----------|--------|
| harness 経由（`KAJI_TMP_DIR` が設定済み） | runner が attempt ごとに作る `KAJI_TMP_DIR` の物理パス |
| 手動起動（`KAJI_TMP_DIR` 未設定） | default branch の checkout の `tmp/kaji/[issue_id]/manual-XXXXXX/`（`mktemp -d` で毎回新規作成） |

正本: [`docs/dev/skill-authoring.md`](../../../docs/dev/skill-authoring.md) § `KAJI_TMP_DIR`

## 手順

一時ファイルを使う最初の手順の直前に、ブロック冒頭のプレースホルダーを実値に置き換えて **1 回だけ** 実行する。
cwd は repo 内の既存 dir（worktree または main checkout）にする。本体は POSIX sh で実行する
（GNU / BSD 両対応。`realpath` / `readlink -f` / `mktemp --tmpdir` を使わない）。

```sh
# kaji-skill-tmp-resolve
ISSUE_ID='[issue_id]' DEFAULT_BRANCH='[default_branch]' sh -s <<'KAJI_SKILL_TMP_EOF'
set -eu
if [ -n "${KAJI_TMP_DIR:-}" ]; then
  # `cd ... && pwd` 単独の形は AND リストの左辺の失敗を set -e が拾わないため、明示的に exit 1 する
  RESOLVED=$(CDPATH= cd -P -- "$KAJI_TMP_DIR" && pwd -P) || {
    echo "skill-tmp-resolve: KAJI_TMP_DIR を解決できない: $KAJI_TMP_DIR" >&2; exit 1
  }
  [ -n "$RESOLVED" ] || { echo "skill-tmp-resolve: KAJI_TMP_DIR の解決結果が空" >&2; exit 1; }
  printf '%s\n' "$RESOLVED"
  exit 0   # 手動経路へは fallback しない
fi
case $ISSUE_ID in
  '' | *[!0123456789]*) echo "skill-tmp-resolve: issue_id が数字でない: $ISSUE_ID" >&2; exit 1 ;;
esac
# default branch の checkout をちょうど 1 件特定する（issue-close Step 5.6 と同じ方式。bare 構成対応）
MAINS=$(git worktree list --porcelain 2>/dev/null | while IFS= read -r line || [ -n "$line" ]; do
  case $line in
    'worktree '*) wt=${line#worktree } ;;
    "branch refs/heads/$DEFAULT_BRANCH") printf '%s\n' "$wt" ;;
  esac
done)
NL='
'
case $MAINS in
  '') echo "skill-tmp-resolve: $DEFAULT_BRANCH の checkout が見つからない" >&2; exit 1 ;;
  *"$NL"*) echo "skill-tmp-resolve: $DEFAULT_BRANCH の checkout が複数ある" >&2; exit 1 ;;
esac
MAIN=$(CDPATH= cd -P -- "$MAINS" && pwd -P) || {
  echo "skill-tmp-resolve: main checkout を解決できない: $MAINS" >&2; exit 1
}
[ -n "$MAIN" ] || { echo "skill-tmp-resolve: main checkout の解決結果が空" >&2; exit 1; }
mkdir -p "$MAIN/tmp/kaji/$ISSUE_ID"
[ -e "$MAIN/tmp/kaji/.gitignore" ] || printf '*\n' > "$MAIN/tmp/kaji/.gitignore"
mktemp -d "$MAIN/tmp/kaji/$ISSUE_ID/manual-XXXXXX"
KAJI_SKILL_TMP_EOF
```

標準出力の 1 行（絶対パス）を `[skill_tmp_dir]` として記録し、以降のコマンドでは
`[skill_tmp_dir]/<ファイル名>` の形で実値を埋め込む。

## 利用規則

- **1 回だけ解決して使い回す**: Claude Code の Bash は呼び出しごとにシェル状態が消える。手動起動の経路は
  実行のたびに新しい dir を作るため、ブロックを再実行したり `$KAJI_TMP_DIR` を各コマンドで直接参照したりしない
- **失敗したら停止する**: 終了コードが非 0、または標準出力が空のときは、一時ファイルを使う後続コマンド
  （Issue 本文の取得・編集、follow-up の作成など）に進まず、その skill の失敗時規則で停止する。
  `/tmp` などの共有の場所へ fallback しない（衝突が再発する）
- **自分で削除しなくてよい**: どちらの経路の dir も `<main checkout>/tmp/kaji/[issue_id]/` 配下にあり、
  `/issue-close` の Step 5.6 が Issue close 後に回収する。`tmp/kaji/` は `.gitignore`（内容 `*`）で git から隠れる
- 手動起動の `manual-XXXXXX` は runner の採番規則 `^[0-9]{4,6}$` に一致しないため、runner の通し番号と干渉しない
- skill の手順に、Issue 番号を含まない絶対パスの `/tmp` 配下のファイルを書かない。
  `tests/test_skill_tmp_paths.py` が `.claude/skills/` 全体を検査する
