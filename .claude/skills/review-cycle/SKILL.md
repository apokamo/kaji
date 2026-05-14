---
description: review-cycle.yaml を kaji run で起動し、終了後に /issue-close 案内を含む verdict を出力する slash command wrapper。
name: review-cycle
---

# Review Cycle

`kaji run .kaji/wf/review-cycle.yaml <issue_id>` を Bash 経由で起動し、終了後に
`/issue-close` 実行案内を含む verdict を出力する slash command wrapper skill。

`review-cycle.yaml` 自体は close を含まないため、本 skill 経由で workflow を回した後は
PASS なら `/issue-close <issue_id>` を **手動で** 実行する運用となる。

## いつ使うか

| タイミング | このスキル |
|-----------|-----------|
| PR レビューループを 1 コマンドで自動化したい（close は手動で確認） | ✅ 必須 |
| close まで全自動で進めたい | ❌ 代わりに `kaji run .kaji/wf/review-close.yaml <id>` |
| `provider.type='local'` 配下 | ❌ `requires_provider: gitlab` のため workflow 側が exit 2 |

**ワークフロー内の位置**: i-pr → [PR 作成] → **/review-cycle** → (PASS なら手動 /issue-close)

## 入力

```
/review-cycle <issue_id>
```

- `$ARGUMENTS = <issue_id>`（Issue 番号のみ。PR ID は workflow 内の `review` skill が逆引きする）
- 未指定の場合は skill 側で `usage: /review-cycle <issue_id>` を stderr に出して
  ABORT verdict を返す

## 実行手順

```bash
set -u  # set -e は外す（exit code を明示的に拾う）

# Step 1: 引数チェック
ISSUE_ID="${1:?usage: /review-cycle <issue_id>}"

# Step 2: kaji run 起動
#   stdout はそのまま流し、stderr のみ tee で capture して
#   ^Workflow aborted: シグナル（kaji_harness/cli_main.py:395-401）を後で grep する。
STDERR_LOG=$(mktemp)
trap 'rm -f "$STDERR_LOG"' EXIT

kaji run .kaji/wf/review-cycle.yaml "$ISSUE_ID" \
    2> >(tee "$STDERR_LOG" >&2)
EXIT=$?

HAS_ABORT_MARKER=0
if grep -q '^Workflow aborted:' "$STDERR_LOG"; then
    HAS_ABORT_MARKER=1
fi

# Step 3: 人間向けの先出しメッセージ（verdict ブロック前に出す）
case "$EXIT" in
    0)
        echo
        echo "review-cycle 完了（workflow PASS）。次に /issue-close $ISSUE_ID を実行してください。"
        ;;
    1)
        if [ "$HAS_ABORT_MARKER" -eq 1 ]; then
            echo "review-cycle が ABORT verdict で終了しました。Issue を確認してください。" >&2
        else
            echo "review-cycle が exit 1 で終了しましたが、'Workflow aborted:' マーカーが見当たりません。予期しないエラーの可能性があります。stderr を確認してください。" >&2
        fi
        ;;
    2)
        echo "review-cycle が定義エラー / 設定エラーで終了しました（exit 2）。kaji validate および .kaji/config.toml を確認してください。" >&2
        ;;
    3)
        echo "review-cycle が runtime error で終了しました（exit 3）。stderr の traceback を確認してください。" >&2
        ;;
    *)
        echo "review-cycle が未知の exit code $EXIT で終了しました。" >&2
        ;;
esac

# Step 4: verdict ブロック出力（必須 / docs/dev/skill-authoring.md § verdict 出力規約）
if [ "$EXIT" -eq 0 ]; then
    cat <<VERDICT_EOF
---VERDICT---
status: PASS
reason: |
  review-cycle workflow completed successfully (exit 0).
evidence: |
  kaji run .kaji/wf/review-cycle.yaml $ISSUE_ID exited with code 0.
suggestion: |
  Run /issue-close $ISSUE_ID to merge the PR and clean up.
---END_VERDICT---
VERDICT_EOF
else
    # exit != 0 はすべて ABORT に倒す。reason / suggestion で原因と次の手を書き分ける。
    case "$EXIT" in
        1)
            if [ "$HAS_ABORT_MARKER" -eq 1 ]; then
                REASON="workflow ABORT verdict (exit 1, 'Workflow aborted:' marker present in stderr)"
                SUGG="Inspect the Issue and Issue comments for the failing step's verdict, then decide whether to /pr-fix manually or close the workflow."
            else
                REASON="exit 1 without 'Workflow aborted:' marker — possibly an unexpected exception in cli_main.py"
                SUGG="Check stderr / traceback. Re-execute the failing step manually for diagnosis."
            fi
            ;;
        2)
            REASON="definition error / config error (exit 2)"
            SUGG="Run 'kaji validate .kaji/wf/review-cycle.yaml' to surface the YAML or skill error; verify .kaji/config.toml has [provider]."
            ;;
        3)
            REASON="runtime error in kaji run (exit 3)"
            SUGG="Inspect stderr traceback. The failing CLI dispatch or verdict parse is logged there."
            ;;
        *)
            REASON="unknown exit code $EXIT"
            SUGG="Inspect kaji run stdout/stderr. This exit code is not defined in docs/dev/workflow-authoring.md § 終了コード."
            ;;
    esac
    cat <<VERDICT_EOF
---VERDICT---
status: ABORT
reason: |
  $REASON
evidence: |
  kaji run .kaji/wf/review-cycle.yaml $ISSUE_ID exited with code $EXIT.
  Workflow aborted marker in stderr: $HAS_ABORT_MARKER (1 = present, 0 = absent).
suggestion: |
  $SUGG
---END_VERDICT---
VERDICT_EOF
fi
```

### exit code の意味（一次情報）

`docs/dev/workflow-authoring.md` § 終了コード および `kaji_harness/cli_main.py:50-56` の
定数定義に従う:

| exit code | 意味 | 本 skill の verdict |
|-----------|------|-------------------|
| 0 (`EXIT_OK`) | workflow PASS | `PASS` |
| 1 (`EXIT_ABORT`) + stderr に `^Workflow aborted:` | 正規 ABORT verdict | `ABORT`（reason に「workflow ABORT verdict」） |
| 1 (`EXIT_ABORT`) + marker なし | `cli_main.py` の `except Exception` 経路（予期しない例外） | `ABORT`（reason に「予期しないエラー」、suggestion: stderr 確認） |
| 2 (`EXIT_DEFINITION_ERROR` / `EXIT_CONFIG_NOT_FOUND` / `EXIT_INVALID_INPUT`) | 定義 / 設定エラー | `ABORT`（suggestion: `kaji validate` / `.kaji/config.toml` 確認） |
| 3 (`EXIT_RUNTIME_ERROR`) | runtime error | `ABORT`（suggestion: traceback 確認） |
| その他 | 未知の exit code | `ABORT`（reason: unknown） |

> **exit 1 の曖昧性**: `cli_main.py` の実装では「正規 ABORT」と「予期しない例外」が
> 両方 `EXIT_ABORT (=1)` に集約される。ただし正規 ABORT のときは
> `print(f"Workflow aborted: ...", file=sys.stderr)` （`cli_main.py:398`）が確実に走る
> ため、stderr の `^Workflow aborted:` を grep して書き分ける。

### PASS / ABORT に縮約する根拠

本 skill は workflow runner の cycle に組み込まれない top-level slash command として
動作する。`docs/dev/skill-authoring.md` § verdict の選択基準にある `RETRY` / `BACK` は
本 skill では意味を持たないため、`PASS` / `ABORT` の 2 値に縮約する。

## Verdict 出力

上記疑似コードの Step 4 で生成される verdict ブロックを **stdout にそのまま出力** すること。
人間向けの `/issue-close` 案内は verdict ブロック前（Step 3）の通常出力と、verdict の
`reason` / `suggestion` 両方に埋め込む。
