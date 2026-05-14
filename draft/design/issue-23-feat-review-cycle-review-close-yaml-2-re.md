# [設計] review-cycle / review-close ワークフロー YAML 2 本 + `/review-cycle` スキル追加

Issue: gl:23

## 概要

PR 作成後の「レビュー → pr-fix → pr-verify」ループを `kaji run` 1 コマンドで自動化する。
`review-cycle.yaml`（close 前で停止）と `review-close.yaml`（close まで実行）の 2 種類を提供し、
`/review-cycle` スキルで前者を起動する slash command を追加する。

## 背景・目的

### 現状の問題

PR レビュー対応は `/review` → `/pr-fix` → `/pr-verify` → （指摘が残れば繰り返し）→
`/issue-close` を毎回手動で順番に叩いており、ループ回数と終了判定も人間が目視で行っている。
1 PR に対して 5〜10 回 slash command を打ち直す必要があり、cycle の収束保証
（既存 `feature-development.yaml` の `code-review` cycle と同じ仕組み）を享受できていない。

### 到達したい状態

- **開発者として**、PR 作成済み Issue に対して `kaji run .kaji/wf/review-cycle.yaml <issue>`
  の 1 コマンドで review → pr-fix → pr-verify ループを自動実行し、PASS または ABORT verdict を
  受け取って終了したい。close は別の判断ステップとして手動で行いたい。
- **開発者として**、`kaji run .kaji/wf/review-close.yaml <issue>` の 1 コマンドで
  review → pr-fix → pr-verify → issue-close まで全自動で完走させたい。
- **開発者として**、`/review-cycle <issue>` slash command で review-cycle.yaml を起動し、
  終了後に「PASS なら `/issue-close` を実行してください」というメッセージを受け取りたい。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|------------|
| 既存 `feature-development.yaml` の `code-review` cycle を流用 | このサイクルは PR 作成前の `issue-review-code` / `issue-fix-code` / `issue-verify-code` を対象とする。PR 作成後の `pr-fix` / `pr-verify` とは入力（PR コメント）も skill も異なる |
| `feature-development.yaml` の末尾 step を `pr-fix` / `pr-verify` ループに拡張 | 設計→実装→PR の long workflow に PR レビュー対応が混ざり、PR 作成済みの Issue に対して `--from` で部分実行する運用が直感的でなくなる。PR 作成済み state からの「短いワークフロー」を独立 YAML として持つほうが粒度が揃う（`implement-to-pr.yaml` と同じ思想） |
| 手動 slash command を維持し cycle 化しない | 完了条件（max_iterations 自動収束 / ABORT 自動発火）を満たせない |

## インターフェース

### 入力

#### `.kaji/wf/review-cycle.yaml` / `.kaji/wf/review-close.yaml`

```bash
kaji run .kaji/wf/review-cycle.yaml <issue_id>
kaji run .kaji/wf/review-close.yaml <issue_id>
```

- `issue_id`: GitLab MR (= kaji PR) と紐づく Issue ID（数値）。**必須**
- 既存の `kaji run` フラグ（`--from` / `--step` / `--before` / `--workdir` / `--quiet`）は
  そのまま使用可能（workflow runner 側の機能）
- 前提: `<issue_id>` に紐づく PR/MR が **既に作成済みで open** であること。これは
  `pr-fix` / `pr-verify` の Step 1（`kaji pr list --search` で PR 解決）に委譲する

#### `/review-cycle <issue_id>` slash command

```
/review-cycle <issue_id>
```

- `issue_id` のみ受け取る（pr_id は受け取らない。kaji pr で逆引きする）

### 出力

#### workflow 終了時の verdict

| 終了条件 | 終了 verdict | issue-close 実行 |
|----------|--------------|------------------|
| review が即 Approve | PASS | review-close のみ実行 |
| pr-verify PASS（cycle 内収束） | PASS | review-close のみ実行 |
| `max_iterations: 3` 超過 | ABORT（`on_exhaust: ABORT`）| **実行しない** |
| 任意 step の ABORT | ABORT | **実行しない** |

`workflow_completion_criteria.md` の verdict 階層に従い、最終 verdict は workflow runner が
集約する（個別 skill verdict ではない）。

#### 副作用

| 経路 | 副作用 |
|------|--------|
| `review` step | PR への review コメント投稿（既存 `kaji pr review --approve` / `--request-changes`）|
| `pr-fix` step | branch への commit + push、PR への対応報告コメント |
| `pr-verify` step | PR の review state 更新（Approve / Changes Requested）|
| `issue-close` step（review-close.yaml のみ）| PR merge、worktree 削除、branch 削除、Issue close |

#### `/review-cycle` の最終出力

`kaji run` を呼んだ後、その exit code と verdict に応じて以下を stdout に出す:

- PASS の場合: 「review-cycle 完了。PASS verdict。次に `/issue-close <issue_id>` を実行してください」
- ABORT の場合: 「review-cycle が ABORT で終了しました。Issue gl:<id> を確認してください」
- その他 exit code: kaji run の stderr を含めてエラー報告

### 使用例

```bash
# Case 1: PR レビューループだけ自動化し、close 判断は手動で残す
kaji run .kaji/wf/review-cycle.yaml 23
# → 終了後、ユーザーが /issue-close 23 を手動実行

# Case 2: close まで全自動
kaji run .kaji/wf/review-close.yaml 23

# Case 3: slash command 経由（review-cycle.yaml 起動）
/review-cycle 23
```

### エラー

| エラー条件 | 挙動 |
|------------|------|
| `<issue_id>` に open な PR/MR が見つからない | `review` / `pr-fix` / `pr-verify` の Step 1 が ABORT verdict を出す（既存仕様）→ workflow も ABORT で終了 |
| `provider.type` が `gitlab` 以外 | `requires_provider` ガードが exit 2 で fail-fast（既存仕様）|
| `max_iterations: 3` 超過 | `on_exhaust: ABORT` で workflow ABORT |
| `/review-cycle` で `<issue_id>` 未指定 | skill 側で「issue_id is required」と stderr 出力し exit 1 |

## 制約・前提条件

### 暗黙の前提（重要 / 設計レビューで判断を仰ぐ）

> **本 Issue 本文には明記されていないが、ワークフロー成立のために必須な前提**

Issue 本文では参考リソースとして `.claude/skills/review/` が挙げられているが、現状
`.claude/skills/` 配下に `review` という skill は **存在しない**
（`/home/aki/dev/kaji/main/.claude/skills/` を listing して確認済み。
`issue-review-code` / `issue-review-design` / `i-doc-review` は別物で、いずれも PR 前段の draft レビュー）。

`kaji validate` は `kaji_harness/cli_main.py:266` で `validate_skill_exists()` を全 step に対して
呼ぶため、`review` step を含む YAML を validate するには `.claude/skills/review/SKILL.md` が
リポジトリに存在する必要がある。完了条件「`kaji validate .kaji/wf/review-cycle.yaml
.kaji/wf/review-close.yaml` がエラーなし」を満たすには `review` skill の作成が
**本 Issue で必須**となる。

**取りうる選択肢**:

- **A. 本 Issue で `review` skill を新規作成する（推奨）**: ワークフローの自己完結性を確保。
  scope 厳密化の Issue 本文の文言とは緊張があるが、`/review-cycle` も skill 新規作成な
  ので「skill 追加」のスコープは元から開いている
- **B. 別 Issue として切り出す**: 本 Issue の YAML 2 本は `review` skill 完成後にしか
  validate 通過しない。事実上、別 Issue を先行マージしないと完了できない依存関係になる
- **C. `review` step を既存 skill で代替する**: 例えば `pr-fix` を review 兼修正として
  二重利用。しかし `pr-fix` は「コメント受信 → 修正」が責務で「レビュー実施 → Approve/
  Changes Requested 判定」とは別。skill 単一責務原則に反する

設計レビューで判断を仰ぐが、本設計書は **案 A** を前提に詳述する。確定後に scope を調整する。

### 技術的制約

- **`requires_provider`**: `review` / `pr-fix` / `pr-verify` / `issue-close`（github 経路）
  / `issue-close`（gitlab 経路）はいずれも forge 必須。`docs/dev/workflow_guide.md`
  § provider × workflow の対応表に従い、`gitlab` を指定する
  （現運用 default。同等 workflow `feature-development.yaml` も `requires_provider: gitlab`）
- **`max_iterations: 3`**: 既存 cycle（`code-review` / `design-review`）と揃える。
  workflow-authoring.md § サイクル定義に従い `loop` 末尾 step の `on.RETRY` は `loop`
  先頭を指す制約あり
- **`execution_policy: auto`**: 完全自動実行（既存 workflow と揃える）
- **`/review-cycle` の起動方法**: `release` skill が `glab` CLI を Bash 経由で叩くのと同
  パターンで、`kaji run` を Bash 経由で呼び出す wrapper skill とする。kaji_harness 側
  に slash command → workflow 起動の bridge を追加する必要はない（既存 `kaji run` を
  呼ぶだけ）
- **`requires_approval` 機能**: Issue 本文のスコープ境界で除外明記。本設計でも触れない

### 既存機能との互換性

- 既存の `feature-development.yaml` / `implement-to-pr.yaml` / `docs-maintenance.yaml`
  には影響を与えない（新規ファイルの追加のみ）
- 既存の `pr-fix` / `pr-verify` / `issue-close` skill は変更しない。step ID として参照
  するのみ

## 変更スコープ

### 新規追加ファイル

| ファイル | 種類 | 概要 |
|----------|------|------|
| `.kaji/wf/review-cycle.yaml` | workflow YAML | review → pr-fix ↔ pr-verify。close 含まず |
| `.kaji/wf/review-close.yaml` | workflow YAML | 上記 + 末尾 `issue-close` step |
| `.claude/skills/review/SKILL.md` | skill | PR レビュー実施 skill（暗黙の前提 / 案 A 採用時のみ）|
| `.claude/skills/review-cycle/SKILL.md` | skill | review-cycle.yaml を起動する slash command wrapper |

### 既存ファイルへの加筆

| ファイル | 加筆内容 |
|----------|----------|
| `docs/dev/workflow_guide.md` | § provider × workflow の対応表に `review-cycle.yaml` / `review-close.yaml` を追加 |
| `CLAUDE.md` の Development Skills 表 | `/review-cycle` を「PR レビュー後サイクル起動」として追記 |

`kaji_harness/` 配下のコード変更は**発生しない**（YAML 追加 + skill 追加のみ。runner /
validator は既存実装で動作する）。

## 方針（Minimal How）

### 1. `review` skill（新規）

> 案 A 採用前提

- **責務**: 指定 Issue に紐づく PR/MR の現状を取得し、コードとレビューコメントを評価して
  Approve / Changes Requested の正式 review を投稿する
- **入力**: コンテキスト変数 `issue_id`、`provider_type`
- **出力**: PR review コメント投稿 + verdict
  - `PASS`: review が Approve（review-cycle 終了 / review-close は close へ進む）
  - `RETRY`: Changes Requested（pr-fix へ）
  - `ABORT`: PR 未存在 / provider mismatch
- **エージェント**: `codex`（既存 review 系と同じ）
- **構造**: `pr-verify/SKILL.md` の Step 0（provider check）/ Step 1（PR 解決）構造を
  雛形に流用し、Step 2 を「初回レビュー実施」に置き換える。`issue-review-code` の
  レビュー観点（設計整合 / コード品質 / テスト証跡）を引き継ぐ
- **Step 構成と情報源の責務分担**（実装時のブレを抑える粒度で明示）:

  | Step | 必須 / 任意 | 用途 |
  |------|-------------|------|
  | Step 0: provider check（`pr-verify` Step 0 を流用）| 必須 | `github` / `gitlab` 以外なら ABORT |
  | Step 1: PR 解決（`pr-verify` Step 1.1 を流用：`kaji pr list --search` → `--head` フォールバック）| 必須 | `pr_id` / `pr_ref` 確定。PR 未存在で ABORT |
  | Step 2: worktree 解決（`_shared/worktree-resolve.md`）| 必須 | 以降の `git diff` / `make check` 実行ディレクトリ確定 |
  | Step 3a: PR 概要取得 `kaji pr view [pr_id]` | **必須** | タイトル / 説明 / 状態 / linked Issue を読み、PR 目的を把握 |
  | Step 3b: 差分本体取得 `cd [worktree_dir] && git fetch [git_remote] [default_branch] && git diff [git_remote]/[default_branch]...HEAD` | **必須** | レビュー対象コードの全量。仕様判定の主入力 |
  | Step 3c: 既存 inline 指摘取得 `kaji pr review-comments [pr_id]` | **必須** | 既に他レビュアーが付けた inline コメントを重複指摘しないため確認 |
  | Step 3d: 既存 review state 取得 `kaji pr reviews [pr_id]` | 任意 | 過去の Approve / Changes Requested 履歴の確認。重複 Approve や逆転判定の検知用 |
  | Step 3e: top-level コメント取得 `kaji pr view [pr_id] --comments` | 任意 | 非 inline の議論経緯を補足参照（初回レビューでは差分が情報の中心） |
  | Step 4: `make check` 実行 `cd [worktree_dir] && source .venv/bin/activate && make check` | **必須** | レビュー判定の基礎ゲート。失敗時は Changes Requested 必至 |
  | Step 5: レビュー観点評価（設計書整合 / コード品質 / テスト証跡 / docs 更新）| 必須 | `issue-review-code` の観点を引き継ぐ |
  | Step 6: 正式 review 投稿 `kaji pr review [pr_id] --approve` / `--request-changes` | 必須 | verdict の写像（Approve → PASS / Changes → RETRY）|
  | Step 7: verdict 出力（`---VERDICT---`）| 必須 | skill-authoring.md § verdict 出力規約に従う |

  > **必須 / 任意の判断基準**: 「初回レビュー判定 (Approve / Changes Requested) を出すために
  > **必ず読まなければならない情報源**」を必須、「あれば判断の補助になる情報源」を任意とする。
  > Step 3a / 3b / 3c / 4 を欠いた状態でのレビュー判定は許可しない（実装時に skill 本文で
  > 明示する）。Step 3d / 3e は過去文脈の補足で、レビュー対象自体ではない。

### 2. `.kaji/wf/review-cycle.yaml`

```yaml
name: review-cycle
description: |
  PR レビュー → 修正 → 確認ループを 1 コマンドで実行する。
  close は手動 (本 workflow は close を含まない)。PR が既に open であることが前提。
execution_policy: auto
requires_provider: gitlab

cycles:
  pr-review:
    entry: review
    loop: [pr-fix, pr-verify]
    max_iterations: 3
    on_exhaust: ABORT

steps:
  - id: review
    skill: review
    agent: codex
    model: gpt-5.5
    effort: medium
    on:
      PASS: end       # Approve → 終了
      RETRY: pr-fix   # Changes Requested → 修正へ
      ABORT: end

  - id: pr-fix
    skill: pr-fix
    agent: claude
    model: opus
    effort: medium
    inject_verdict: true
    on:
      PASS: pr-verify
      ABORT: end

  - id: pr-verify
    skill: pr-verify
    agent: codex
    model: gpt-5.5
    effort: medium
    on:
      PASS: end
      RETRY: pr-fix   # 不十分 → 再修正
      ABORT: end
```

`cycles.pr-review` の `entry: review` / `loop: [pr-fix, pr-verify]` / `on_exhaust: ABORT`
により、`pr-verify` RETRY が 3 回連続発生（= loop が 3 周）したら ABORT verdict が発火する
（`workflow-authoring.md` § サイクル定義に従う）。

### 3. `.kaji/wf/review-close.yaml`

review-cycle.yaml と同一構造に末尾 `close` step を追加し、`review` PASS と `pr-verify` PASS の
遷移先を `close` に変更する。ABORT 経路は `end` のままなので「ABORT 時に issue-close を
実行しない」要件が成立する。

```yaml
name: review-close
description: |
  PR レビュー → 修正 → 確認 → close まで 1 コマンドで全自動実行する。
  PR が既に open であることが前提。
execution_policy: auto
requires_provider: gitlab

cycles:
  pr-review:
    entry: review
    loop: [pr-fix, pr-verify]
    max_iterations: 3
    on_exhaust: ABORT

steps:
  - id: review
    skill: review
    agent: codex
    model: gpt-5.5
    effort: medium
    on:
      PASS: close
      RETRY: pr-fix
      ABORT: end

  - id: pr-fix
    skill: pr-fix
    agent: claude
    model: opus
    effort: medium
    inject_verdict: true
    on:
      PASS: pr-verify
      ABORT: end

  - id: pr-verify
    skill: pr-verify
    agent: codex
    model: gpt-5.5
    effort: medium
    on:
      PASS: close
      RETRY: pr-fix
      ABORT: end

  - id: close
    skill: issue-close
    agent: claude
    model: sonnet
    effort: medium
    on:
      PASS: end
      ABORT: end
```

### 4. `/review-cycle` skill（新規）

- **責務**: `kaji run .kaji/wf/review-cycle.yaml <issue_id>` を Bash 経由で起動し、
  完了後に `/issue-close` 実行案内を含む verdict を stdout に出す
- **入力**: `$ARGUMENTS = <issue_id>`
- **skill 契約**: 本 skill も `.claude/skills/review-cycle/SKILL.md` として配置される正規 skill
  のため、`docs/dev/skill-authoring.md` § verdict 出力規約に従い **最終出力に `---VERDICT---`
  ブロックを stdout に出す**。人間向けの `/issue-close` 案内は `reason` / `suggestion` に
  埋め込み、verdict ブロック前の通常出力にも併載する
- **verdict 設計**:

  | 経路 | exit code | stderr / stdout の追加情報 | 出力 verdict |
  |------|-----------|----------------------------|--------------|
  | review 即 Approve / cycle 内 verify PASS で正常完了 | `0` (`EXIT_OK`) | — | `PASS` |
  | 任意 step の ABORT / `on_exhaust: ABORT` 発火 | `1` (`EXIT_ABORT`) | stderr に `Workflow aborted: <reason>`（`kaji_harness/cli_main.py:395-401`）| `ABORT`、reason に「workflow ABORT verdict を確認」 |
  | 予期しない例外（`cli_main.py` の `except Exception` 経路）| `1` (`EXIT_ABORT`) | stderr に `Workflow aborted:` が**ない** | `ABORT`、reason に「予期しないエラー」、suggestion でログ確認を促す |
  | 定義エラー（YAML 不正、skill 未検出）| `2` (`EXIT_DEFINITION_ERROR`)| stderr に validate / skill エラー | `ABORT`、suggestion: `kaji validate` を直接実行して原因確認 |
  | config 未検出 / provider mismatch | `2` (`EXIT_CONFIG_NOT_FOUND` / `EXIT_INVALID_INPUT`)| stderr に設定エラー | `ABORT`、suggestion: `.kaji/config.toml` を確認 |
  | runtime error | `3` (`EXIT_RUNTIME_ERROR`) | stderr に runtime traceback | `ABORT`、suggestion: stderr の traceback を確認 |
  | その他（未知の exit code）| ≠ 0/1/2/3 | — | `ABORT`、reason: 「unknown exit code」 |

  > **exit 1 の曖昧性の解消**: `docs/dev/workflow-authoring.md` の終了コード表は `1 = ワークフロー
  > ABORT または予期しないエラー` と定義しており、`cli_main.py:387-401` の実装でも両経路が
  > `EXIT_ABORT` に集約される。ただし正規 ABORT verdict のときは `kaji_harness/cli_main.py:398`
  > の `print(f"Workflow aborted: ...", file=sys.stderr)` が確実に走るため、`stderr` を
  > capture して `^Workflow aborted:` が含まれるかで区別できる。これにより「正規 ABORT」と
  > 「予期しないエラー」を verdict の `reason` で書き分け、誤って正規 ABORT と断定しない

- **実装方針**（疑似コード）:

  ```bash
  set -u  # set -e は外す（exit code は明示的に拾う）

  # Step 1: 引数チェック
  ISSUE_ID="${1:?usage: /review-cycle <issue_id>}"

  # Step 2: kaji run 起動
  #   stdout はそのまま流し、stderr のみ tee で capture して
  #   `Workflow aborted:` シグナルを後で grep する。
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
              echo "review-cycle が ABORT verdict で終了しました。Issue gl:$ISSUE_ID を確認してください。" >&2
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

  # Step 4: verdict ブロック出力（必須 / skill-authoring.md § verdict 出力規約）
  if [ "$EXIT" -eq 0 ]; then
      cat <<VERDICT_EOF
  ---VERDICT---
  status: PASS
  reason: |
    review-cycle workflow completed successfully (exit 0).
  evidence: |
    kaji run .kaji/wf/review-cycle.yaml $ISSUE_ID exited with code 0.
    Next step: /issue-close $ISSUE_ID
  suggestion: |
    Run /issue-close $ISSUE_ID to merge the PR and clean up.
  ---END_VERDICT---
  VERDICT_EOF
  else
      # exit != 0 はすべて ABORT に倒す。reason / suggestion で原因と次の手を書き分ける
      case "$EXIT" in
          1)
              if [ "$HAS_ABORT_MARKER" -eq 1 ]; then
                  REASON="workflow ABORT verdict (exit 1, 'Workflow aborted:' marker present in stderr)"
                  SUGG="Inspect the Issue and Issue comments for the failing step's verdict, then decide whether to /pr-fix manually or close the workflow."
              else
                  REASON="exit 1 without 'Workflow aborted:' marker — possibly an unexpected exception in cli_main.py"
                  SUGG="Check stderr / traceback. Re-run with --quiet off or re-execute the failing step manually for diagnosis."
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

  - `kaji run` の stdout はそのままユーザに流れる（workflow 実行ログ）。本 skill が追加で
    出すのは「人間向け 1 行サマリ」と「`---VERDICT---` ブロック」のみ
  - stderr の `Workflow aborted:` シグナル（`kaji_harness/cli_main.py:395-401`）を
    `grep` で検出し、exit 1 の二重意味（正規 ABORT / 予期しない例外）を verdict の `reason`
    で区別する
  - `release` skill と同じ「kaji 外部コマンドを Bash で叩く」パターン（`release/SKILL.md`
    Step 1 と同型）だが、本 skill では exit code の意味解析と verdict 出力が責務に加わる

- **PASS / ABORT verdict の使い分けの根拠**:
  - `docs/dev/skill-authoring.md` § verdict の選択基準には `PASS` / `RETRY` / `BACK` / `ABORT`
    の 4 種があるが、本 skill は workflow runner の cycle に組み込まれない top-level slash
    command として動作するため `RETRY` / `BACK` は意味を持たない。よって `PASS` か `ABORT`
    の 2 値に縮約する

### データフロー

```
   /review-cycle <id>
        │
        ▼
   kaji run review-cycle.yaml <id>
        │
        ▼
   review step
   ├── Approve   → PASS → end
   ├── Changes   → RETRY → pr-fix
   └── ABORT     → ABORT → end
        │
        ▼
   pr-fix step
   ├── PASS → pr-verify
   └── ABORT → end
        │
        ▼
   pr-verify step
   ├── PASS → end           ← review-cycle
   │       → close          ← review-close
   ├── RETRY → pr-fix       (max 3 周で on_exhaust: ABORT)
   └── ABORT → end
```

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義する。

### 変更タイプ

**実行時コード変更を伴わない混合変更**:

- YAML 2 本追加（`.kaji/wf/`）— workflow runner のロジック変更なし
- skill 2 本追加（`.claude/skills/`）— agent prompt source。Python コードへの影響なし
- docs 加筆 — `docs/dev/workflow_guide.md`、`CLAUDE.md`

`kaji_harness/` の Python ロジックには変更なし。よって testing-convention.md の「実行時の振る舞いを
変えるコード変更」の S/M/L 義務付けは直接は適用されない。一方、YAML/skill の妥当性は
`kaji validate` という既存ゲートで担保される。

### 変更固有検証

| 検証項目 | 手段 | 期待結果 |
|----------|------|----------|
| YAML 構文 + skill 存在 + transition 整合 | `kaji validate .kaji/wf/review-cycle.yaml .kaji/wf/review-close.yaml` | exit 0 + `✓` 表示 |
| `cycles.pr-review.loop` 末尾 `pr-verify` の `on.RETRY` が `pr-fix` を指す制約 | `kaji validate`（workflow.py の `validate_workflow()` 内 cycle 制約チェック）| OK |
| `requires_provider: gitlab` ガードが効く | `provider=local` に切り替えて `kaji run .kaji/wf/review-cycle.yaml 1` → exit 2 を確認 | exit 2 + stderr に切替手順 |
| `/review-cycle` skill が SKILL.md 形式（frontmatter）の最小 contract を満たす | `Read` + 目視（既存 skill 群と同様。skill 自体に自動検証はない）| frontmatter 適合 |
| ループ走行（dry-run 相当） | 既存 `kaji-run-verify` skill での手動検証（後述）| 動作確認 |

### 恒久テストを追加しない理由（`docs/dev/testing-convention.md` § 省略してよい理由）

1. **独自ロジックの追加・変更をほぼ含まない**: 新規 YAML は既存 `workflow.py` の cycle / on /
   step の枠組みで完全に表現される。新規 skill は既存 skill と同じ Markdown 形式
2. **想定される不具合パターンが既存テストまたは既存品質ゲートで捕捉済み**:
   - YAML 整合性 → `kaji validate`（既存）
   - cycle 制約 / unknown step transition → `workflow.py` の既存テスト群（gl:21 / fix/22 で
     強化された validator）
   - skill 存在チェック → `kaji_harness/skill.py` の `validate_skill_exists`
3. **新規テストを追加しても回帰検出情報がほとんど増えない**: YAML 2 本に対する文字列
   一致テストや「`steps[i].on.PASS == 'end'`」のような snapshot は、既存 validator
   が見るのと同じ抽象層で重複する
4. **テスト未追加の理由を本セクションで説明可能**

### 手動検証（変更固有 / 一時検証）

`docs/dev/testing-convention.md` § 変更固有の一時検証に該当。`make check` のような恒久回帰
チェインには載せず、本 Issue の `i-dev-final-check` 段で 1 回だけ実施する。

- **手段**: 既存 `kaji-run-verify` skill を使い、テスト用 PR（open MR の最小再現）に対し
  `kaji run .kaji/wf/review-cycle.yaml <id>` を 1 回走らせる。Approve 経路 / Changes
  Requested → pr-fix → pr-verify PASS 経路 / on_exhaust ABORT 経路の 3 ケースを最小限カバー
- **記録**: `kaji-run-verify` の手順に従い Issue にコメントで結果を残す

### `large_gitlab` マーカーへの恒久テスト追加について

PR レビュー実通信は `make test-large-gitlab` の領分だが、本 Issue では追加しない:

- `pr-fix` / `pr-verify` / `issue-close` の GitLab 実通信は既に既存 skill 内で動作実績あり
  （`feature-development.yaml` の運用で日常的に検証されている）
- 本 Issue で新規追加するのは「step を組み合わせた workflow YAML」のみ。各 step の挙動は
  既存テストで担保済み
- workflow の組み合わせを `large_gitlab` で固定テスト化すると、PR の事前準備（open MR の
  確保）/ branch state / レビュー状態のセットアップコストが大きい割に回帰価値が低い

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|--------------|------------|------|
| `docs/adr/` | なし | 新しい技術選定（ライブラリ / プロトコル）は含まない |
| `docs/ARCHITECTURE.md` | なし | アーキテクチャ層には変更なし |
| `docs/dev/workflow_guide.md` | **あり** | § provider × workflow の対応表に新規 2 本を追加（行 21-28 のテーブル）。「PR レビュー後サイクル」の選択基準セクションも追加 |
| `docs/dev/development_workflow.md` | なし | dev workflow の流れ自体は不変。新 workflow は PR 作成後の独立フロー |
| `docs/dev/docs_maintenance_workflow.md` | なし | docs-only ワークフローへの影響なし |
| `docs/dev/workflow_completion_criteria.md` | なし | 完了条件は新 workflow も既存と同じ verdict 階層に従う |
| `docs/dev/workflow-authoring.md` | なし | YAML 仕様自体は変更しない |
| `docs/dev/skill-authoring.md` | なし | skill 仕様自体は変更しない |
| `docs/cli-guides/` | なし | 既存 `kaji run` の挙動に変更なし |
| `docs/reference/python/` | なし | Python コード変更なし |
| `CLAUDE.md` | **あり** | § Development Skills 表に `/review-cycle` を追記（必要なら「PR レビュー後フェーズ」行を新設）|
| `.github/labels.yml` | なし | ラベル定義に変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約）|
|--------|----------|--------------------|
| Workflow Authoring Manual | `docs/dev/workflow-authoring.md` | `cycles` セクション仕様（`entry` / `loop` / `max_iterations` / `on_exhaust`）と `on` マッピング（PASS / RETRY / BACK / ABORT）の定義。「`loop` 末尾ステップの `on.RETRY` は `loop` 先頭ステップを指すこと」（行 150）を本設計の cycle 構造の根拠とする |
| Workflow Guide | `docs/dev/workflow_guide.md` | `requires_provider` の値域と builtin workflow への適用方針。「feature-development.yaml は `requires_provider: gitlab`」（行 23 テーブル）を本設計の provider 値の根拠とする |
| 既存 feature-development.yaml | `.kaji/wf/feature-development.yaml` | `code-review` cycle（行 14-18）の構造を本設計の `pr-review` cycle 雛形として参照 |
| Skill 解決ロジック | `kaji_harness/skill.py:8-31` | `validate_skill_exists` が `<workdir>/<skill_dir>/<skill_name>/SKILL.md` の存在を確認する。`review` step を含む YAML を validate するには `.claude/skills/review/SKILL.md` が必要、という制約の根拠 |
| Validate コマンド実装 | `kaji_harness/cli_main.py:249-287` | `cmd_validate` が `validate_workflow` に加えて全 step の `validate_skill_exists` を呼ぶ。Issue 完了条件「kaji validate がエラーなし」に skill 存在が必要な根拠 |
| 既存 pr-verify skill | `.claude/skills/pr-verify/SKILL.md` | Step 0 の provider check / Step 1 の PR 解決パターンを `review` skill の雛形として流用する根拠 |
| 既存 release skill | `.claude/skills/release/SKILL.md` | 「Bash で外部コマンドを叩く wrapper skill」のパターン。`/review-cycle` の実装方針の参照 |
| Testing Convention | `docs/dev/testing-convention.md` | § 省略してよい理由 4 条件を「変更固有検証で十分」判断に適用 |
