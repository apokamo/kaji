---
description: 第2層のインシデント調査レビュー収束サイクルを 1 コマンドで手動起動する slash command wrapper。kaji run .kaji/wf/incident.yaml <incident_issue_id> を Bash 経由で起動し、exit code を verdict に縮約する。
name: incident-cycle
---

# Incident Cycle（slash wrapper）

`kaji run .kaji/wf/incident.yaml <incident_issue_id>` を Bash 経由で起動する slash command wrapper
skill（`/review-cycle` 同型）。第2層（インシデント原因調査・対応策提案）の**手動起動の入口**。

- 起動は手動のみ（#303 決定 C）。自動起動・自動昇格は実装しない。
- 全終端は「提案」であり、ラベル遷移・クローズ・統合の実行は人間が行う。close step は無いため
  `--before` は使わない。

## いつ使うか

| タイミング | このスキル |
|-----------|-----------|
| 第1層が起票したインシデントの調査サイクルを 1 コマンドで回したい | ✅ 必須 |
| `provider.type='github'` の環境 | ✅ 前提（`incident.yaml` は `requires_provider: github`） |
| `provider.type='github'` 以外（`local` 等） | ❌ workflow が `requires_provider: github` で exit 2 |

**ワークフロー内の位置**: 第1層のインシデント起票 → **/incident-cycle** →（人間が処遇判断）

## 入力

```
/incident-cycle <incident_issue_id>
```

- `$ARGUMENTS = <incident_issue_id>`（第1層が起票したインシデントイシューの番号）。
- `docs/dev/skill-authoring.md` § 手動実行 の規約に従い、引数は `$ARGUMENTS` から取得する。
- 未指定（`$ARGUMENTS` が空）の場合は `kaji run` を実行せず、stderr に usage を出して
  **ABORT verdict** を返す。

## 実行手順

以下の擬似コードを Claude が Bash 経由で実行する。`$ARGUMENTS` は slash command の引数文字列。

```bash
set -u  # set -e は外す（exit code を明示的に拾う）

# Step 1: 引数チェック（引数欠落時の ABORT 経路）
read -r ISSUE_ID _REST <<<"${ARGUMENTS:-}"
if [ -z "${ISSUE_ID:-}" ]; then
    echo "usage: /incident-cycle <incident_issue_id>" >&2
    cat <<'VERDICT_EOF'
---VERDICT---
status: ABORT
reason: |
  Missing required argument: <incident_issue_id>.
evidence: |
  $ARGUMENTS was empty or did not contain an incident_issue_id token.
suggestion: |
  Re-invoke as /incident-cycle <incident_issue_id> (e.g. /incident-cycle 314).
---END_VERDICT---
VERDICT_EOF
    exit 2
fi

# Step 2: kaji run 起動
#   stderr のみ tee で capture し、^Workflow aborted: シグナルを後で grep する。
STDERR_LOG=$(mktemp)
trap 'rm -f "$STDERR_LOG"' EXIT

kaji run .kaji/wf/incident.yaml "$ISSUE_ID" \
    2> >(tee "$STDERR_LOG" >&2)
EXIT=$?

HAS_ABORT_MARKER=0
if grep -q '^Workflow aborted:' "$STDERR_LOG"; then
    HAS_ABORT_MARKER=1
fi

# Step 3: verdict ブロック出力（exit code → verdict の縮約契約: 0 → PASS / 非 0 → ABORT）
if [ "$EXIT" -eq 0 ]; then
    echo
    echo "incident-cycle 完了（workflow PASS）。最終提案コメントを確認し、ラベル遷移・クローズ・バグイシュー化・統合の処遇を人間が判断してください。"
    cat <<VERDICT_EOF
---VERDICT---
status: PASS
reason: |
  incident workflow completed successfully (exit 0).
evidence: |
  kaji run .kaji/wf/incident.yaml $ISSUE_ID exited with code 0.
suggestion: |
  Review the final proposal comment on the incident issue and decide label transition / close / bug-issue drafting / consolidation (human judgement).
---END_VERDICT---
VERDICT_EOF
else
    # exit != 0 はすべて ABORT に縮約する。
    case "$EXIT" in
        1)
            if [ "$HAS_ABORT_MARKER" -eq 1 ]; then
                REASON="workflow ABORT verdict (exit 1, 'Workflow aborted:' marker present in stderr)"
                SUGG="Inspect the incident issue comments for the aborting step's verdict. Re-run with --from review --reset-cycle after human review if the review cycle exhausted."
            else
                REASON="exit 1 without 'Workflow aborted:' marker — possibly an unexpected exception in cli_main.py"
                SUGG="Check stderr / traceback. Re-execute the failing step manually for diagnosis."
            fi
            ;;
        2)
            REASON="definition error / config error (exit 2)"
            SUGG="Run 'kaji validate .kaji/wf/incident.yaml'; verify .kaji/config.toml [provider] type is github."
            ;;
        3)
            REASON="runtime error in kaji run (exit 3)"
            SUGG="Inspect stderr traceback."
            ;;
        *)
            REASON="unknown exit code $EXIT"
            SUGG="Inspect kaji run stdout/stderr."
            ;;
    esac
    cat <<VERDICT_EOF
---VERDICT---
status: ABORT
reason: |
  $REASON
evidence: |
  kaji run .kaji/wf/incident.yaml $ISSUE_ID exited with code $EXIT.
  Workflow aborted marker in stderr: $HAS_ABORT_MARKER (1 = present, 0 = absent).
suggestion: |
  $SUGG
---END_VERDICT---
VERDICT_EOF
fi
```

### exit code → verdict の縮約契約

本 skill は workflow runner の cycle に組み込まれない top-level slash command として動作する。
`RETRY` / `BACK` は意味を持たないため、**exit 0 → `PASS` / 非 0 → `ABORT`** の 2 値に縮約する
（exit code の意味は `docs/dev/workflow-authoring.md` § 終了コード に従う）。

## Verdict 出力

上記擬似コードが生成する verdict ブロックを **stdout にそのまま出力** すること。
