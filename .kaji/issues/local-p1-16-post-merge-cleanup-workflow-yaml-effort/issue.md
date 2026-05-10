---
id: local-p1-16
title: 'post-merge cleanup: workflow YAML effort 値の型強化 + Issue ファイル commit 動線改善'
state: closed
slug: post-merge-cleanup-workflow-yaml-effort
labels:
- type:bug
created_at: '2026-05-09T12:03:18Z'
closed_at: '2026-05-09T13:59:15Z'
closed_by: pc5090
close_reason: completed
---
> [!NOTE]
> **Worktree**: `../kaji-feat-local-p1-16`
> **Branch**: `feat/local-p1-16`

## 設計書

<details>
<summary>設計書全文（draft/design/issue-local-p1-16-post-merge-cleanup-effort-and-commit.md）</summary>

# [設計] post-merge cleanup: workflow YAML effort 値の型強化 + Issue ファイル commit 動線改善

Issue: local-p1-16

## 概要

local-p1-5/14 完了後の workflow ログ調査で発見した独立 2 バグを bundle 修正する:
- **A**: `.kaji/wf/feature-development-local.yaml` の effort 値に大文字 (`xHigh`/`High`) が残存し codex agent step に渡ると `unknown variant` で workflow が ERROR 停止する。`workflow.py` parse 時に runtime validator を入れて agent 別 allowed values で reject する
- **B**: `kaji issue {comment,edit}` が `.kaji/issues/<id>/` 配下を更新するが commit を伴わず、close 直前まで base worktree に蓄積し `issue-close` skill の ABORT ガードが救済 commit で逸脱される。`--commit` フラグで永続化操作と commit を atomic 化し、`issue-close` の救済条件を厳格化する

## 背景・目的

両者とも main 直 commit 系（`.kaji/wf/` または `.kaji/issues/`）の運用問題で関連性が強い。bundle 修正で一度に閉じ、設計書 / レビュー / 実装サイクルの重複コストを避ける。

### Observed Behavior（OB）

#### A: workflow が effort 大文字で ERROR 停止

`.kaji-artifacts/local-p1-5/runs/2605091602/run.log:5` に以下の error が記録:

```
CLIExecutionError: Step 'review-design' CLI exited with code 1:
Error loading config.toml: unknown variant `High`,
expected one of `none`, `minimal`, `low`, `medium`, `high`, `xhigh`
in `model_reasoning_effort`
```

`.kaji/wf/feature-development-local.yaml` の effort 値の現状（`grep -n "effort:"` 結果）:

| line | step | agent | 値 | 妥当性 |
|------|------|-------|-----|-------|
| 27 | design | claude | `xHigh` | ✗ claude `--effort` allowed = `low,medium,high,xhigh,max` |
| 36 | review-design | codex | `high` | ✓ |
| 46 | fix-design | claude | `xHigh` | ✗ |
| 56 | verify-design | codex | `high` | ✓ |
| 67 | implement | claude | `High` | ✗ |
| 78 | review-code | codex | `high` | ✓ |
| 89 | fix-code | claude | `High` | ✗ |
| 100 | verify-code | codex | `high` | ✓ |
| 111 | final-check | claude | `medium` | ✓ |

claude CLI 側は大文字でも accept する場合があるが（`claude --help` の挙動）、claude step 用の `xHigh` を後段の codex resume step などにコピーペーストして混入すると即 ERROR。`.kaji/wf/` 内の他 workflow（`feature-development-light.yaml`, `feature-development.yaml`, `implement-to-pr.yaml`, `design-only.yaml`, `docs-maintenance-local.yaml`）はすべて小文字のみで OK。問題は `feature-development-local.yaml` のみ。

#### B: Issue ファイルが close 直前まで未コミットで蓄積

`.kaji-artifacts/local-p1-14/runs/2605091631/close/console.log:5-12`:

```
base worktree に未コミット変更が残っています（issue.md 修正 + コメントファイル）
issue.md に設計書が追加され、コメント 0006〜0009 が未追跡
```

`.claude/skills/issue-close/SKILL.md:266` の Step 2 は本来:

```bash
test -z "$(git status --porcelain)" || { echo "ABORT: uncommitted changes in base worktree $BASE_WT"; exit 1; }
```

と ABORT 設計だが、Claude が指示を逸脱して救済 commit (`0b14612`, `3501fc2`) を作って続行した。

蓄積する未コミット変更の出元:
- `kaji issue comment` — `.kaji/issues/<id>/comments/<seq>.md` を新規作成（`cli_main.py:1068-1083`）
- `kaji issue edit` — `.kaji/issues/<id>/issue.md` を更新（`cli_main.py:1044-1065`、`issue-start` / `i-dev-final-check` / `i-doc-final-check` / `issue-fix-ready` 等が使用）
- `kaji issue close` — `issue.md` の frontmatter を更新（`cli_main.py:1086-1096`、`issue-close` skill 内で実行）
- 設計書 `draft/design/issue-<id>-<slug>.md` — feature worktree に作成され、feature branch で commit される（base worktree には流入しない）

→ `comment` だけ `--commit` 対応しても `edit` 由来の `issue.md` 変更が残るため、最低限 `comment + edit` を範囲に含める必要がある。

### Expected Behavior（EB）

#### A: workflow YAML の effort 値が agent 仕様と一致し、parse 時に reject される

1. `.kaji/wf/feature-development-local.yaml` の effort 値はすべて小文字で agent 仕様の subset に含まれる（line 27, 46, 67, 89 の `xHigh`/`High` を `xhigh`/`high` に修正）
2. `kaji_harness/workflow.py::_parse_workflow` で `step.agent` 別の allowed values 辞書を runtime validator として実装し、許容外値は `WorkflowValidationError` で reject する
3. `docs/dev/workflow-authoring.md` に「両 CLI の対話 UI では大文字 (`Low/Medium/High/Extra high`) で表示されるが YAML には小文字を書く」+ 採択した許容値方針が明記される
4. `kaji validate .kaji/wf/*.yaml workflows/*.yaml` が全 workflow で通過する

#### B: 各 skill が永続化操作を実行した直後にコミットされ、`issue-close` 開始時点で base worktree が clean

1. `kaji issue comment` / `kaji issue edit` に `--commit` フラグを追加。指定時、永続化に伴って書き換わった `.kaji/issues/<id>/` 配下のパスのみを `git add` し `git commit` する（メッセージは `chore(local): <action> for <issue_ref>` 形式）
2. 主要 skill（`issue-design`, `issue-implement`, `issue-review-*`, `issue-fix-*`, `issue-verify-*`, `i-dev-final-check`, `i-doc-final-check`, `issue-fix-ready`, `issue-start`）の `kaji issue {comment,edit}` 呼び出しに `--commit` を反映
3. `issue-close` skill の Step 2 を「dirty file の判定 + 救済範囲制約 + 救済後再検証」の 3 段ガードに書き換える:
   - 条件 1: `git status --porcelain` の出力パスが **すべて `.kaji/issues/<id>/` 配下** であること
   - 条件 2: 救済 commit の対象を **`.kaji/issues/<id>/issue.md`** と **`.kaji/issues/<id>/comments/*.md`** の whitelist パターンに限定（手動編集で他ファイルが混在していたら ABORT）
   - 条件 3: 救済 commit 後に `git status --porcelain` を再実行し、残差があれば ABORT
4. dev workflow 1 サイクル実行で base worktree が close 直前まで clean を維持する

## 再現手順

### A の再現

1. `.kaji/wf/feature-development-local.yaml` の codex agent step（例 line 36 の review-design）の effort を `high` → `High` に変更
2. `kaji run .kaji/wf/feature-development-local.yaml <issue>` を実行
3. review-design step で `CLIExecutionError: ... unknown variant 'High'` が発生し workflow が ERROR 終了する

または bundle 修正前の現状再現:
1. 現状 `feature-development-local.yaml` を未修正のまま `kaji run` で local issue を流す
2. claude step は通過するが、設計時に effort 値をコピペで他 step に持ち込むとそこで ERROR

### B の再現

1. 任意の Issue を `/issue-start` → `/issue-design` → `/issue-review-design` → `/issue-fix-design` → ... と進める
2. 各 skill が `kaji issue comment` でコメントを生成、`issue-start` / `i-dev-final-check` / `issue-fix-ready` が `kaji issue edit` で `issue.md` を更新
3. close 直前に base worktree で `git status` を実行
4. `.kaji/issues/<issue_id>/comments/*.md` と `issue.md` の更新が untracked / unstaged のまま蓄積していることを観測

## 根本原因（Root Cause）

### A: `effort` 値に runtime validation が無い

- `kaji_harness/models.py::Step.effort` は `str | None` で型は緩い（`Literal[...]` ですらない）
- `kaji_harness/workflow.py::_parse_workflow` は `effort=step_data.get("effort")` で素通し（line 141）
- `kaji_harness/cli.py:240` (`claude --effort`) と `cli.py:267` (`codex -c model_reasoning_effort=...`) でそのまま CLI に passthrough
- 仮に `Step.effort` を `Literal[...]` 化しても、dataclass の Literal hint は **静的解析（mypy）専用** で、実行時には文字列代入を拒否しない（Python 公式仕様）
- 過去 commit `a709a31` で codex 系のみ effort 修正、claude 系 4 箇所の大文字残存に気づかなかった理由 = 機械的に検出する仕組みが無いため

→ 実装場所は **`workflow.py` の YAML load / `_parse_workflow` 内**で agent 別 allowed values を辞書で持ち、step.agent でルックアップして reject する。`models.py` の Literal hint だけでは runtime validation にならない。

**いつから壊れているか**: `feature-development-local.yaml` 初版から（git blame 上は `a709a31` 以前から claude 系大文字が残存）。

**他に壊れている箇所**: `grep` 全 workflow 結果、大文字 effort は `feature-development-local.yaml` の 4 箇所のみ。他 workflow YAML は全て小文字。

### B: 永続化操作と commit が分離

- `LocalProvider.{comment,edit,close}_issue()` は file system に書き込むだけで commit を行わない設計
- 各 skill SKILL.md は `kaji issue {comment,edit}` を呼ぶが commit を呼んでいない
- 結果: 「永続化された file 群」と「Git working tree」の状態が乖離し、close 直前まで蓄積する
- `issue-close` skill の ABORT ガードは「base worktree に何らかの未コミット変更がある時点で停止」する設計だが、Claude が「Issue を close するには Issue 関連ファイルを commit する必要がある」と推論して救済 commit を作るため、ABORT ガードが運用上機能しない

**いつから壊れているか**: LocalProvider 導入時から（Phase 3-c）。
**他にも壊れている箇所**: `kaji issue create` も新規 `.kaji/issues/<id>/issue.md` + `.kaji/labels/*.md` 等を未コミットで作る。ただし `create` は **dev workflow 起動の前段で手動実行**される（i.e. `/issue-create` skill が呼ぶが、その後 `/issue-start` までに user 手動 commit のチャンスがある）ため、優先度は `comment + edit` より低い。本 Issue の bundle スコープからは除外（次 Issue で別途扱う）。

## インターフェース

bug 修正のため、CLI / 設定の追加変更のみ。後方互換性は維持する。

### A: `kaji_harness/workflow.py` の effort validator

**変更前**: `_parse_workflow` で `effort=step_data.get("effort")` で素通し。

**変更後**: `_parse_workflow` の step ループ内で agent 別 allowed values 辞書を参照し、許容外値を `WorkflowValidationError` で reject:

```python
# kaji_harness/workflow.py 内（module level）
_AGENT_EFFORT_ALLOWED: dict[str, frozenset[str]] = {
    "claude": frozenset({"low", "medium", "high", "xhigh", "max"}),
    "codex": frozenset({"none", "minimal", "low", "medium", "high", "xhigh"}),
}

# _parse_workflow の step 解釈ループ内
raw_effort = step_data.get("effort")
if raw_effort is not None:
    if not isinstance(raw_effort, str):
        raise WorkflowValidationError(
            f"Step '{step_data['id']}' 'effort' must be a string, got {type(raw_effort).__name__}"
        )
    allowed = _AGENT_EFFORT_ALLOWED.get(step_data["agent"])
    if allowed is not None and raw_effort not in allowed:
        raise WorkflowValidationError(
            f"Step '{step_data['id']}' effort '{raw_effort}' is not valid for "
            f"agent '{step_data['agent']}' (allowed: {sorted(allowed)})"
        )
```

allowed values 辞書に未登録の agent（例: `gemini`）は **検証スキップ**（passthrough）。理由: agent 仕様が固まっていない段階で whitelist に入れると新 agent 導入のたびに本ファイルを編集する必要がある。

### B: `kaji issue {comment,edit}` の `--commit` フラグ

**変更前**: `kaji_harness/cli_main.py::_local_issue_comment` / `_local_issue_edit` は永続化のみ。

**変更後**: `--commit` フラグを追加し、永続化直後に Git の **partial commit** (`git commit --only/-o <pathspec>`) で該当パスのみを commit する:

```python
# _local_issue_comment（簡略疑似コード）
p.add_argument("--commit", action="store_true",
               help="Stage and commit the resulting .kaji/issues/<id>/ paths after persistence")
ns = p.parse_args(rest)
# ... 既存の永続化処理 ...
comment = provider.comment_issue(rid.value, body)
sys.stdout.write(f"{comment.seq}-{comment.machine_id}\n")
if ns.commit:
    _commit_local_issue_change(
        provider=provider,
        rid=rid,
        action="comment",
        paths=[provider.issue_dir(rid.value) / "comments" / f"{comment.seq:04d}-{comment.machine_id}.md"],
    )
```

`_commit_local_issue_change()` は内部 helper（atomicity 保証付き）:

```python
# kaji_harness/cli_main.py 内（疑似コード）
def _commit_local_issue_change(
    *,
    provider: LocalProvider,
    rid: ResolvedId,
    action: str,
    paths: list[Path],
) -> None:
    """`paths` のみを atomic に commit する。他の staged changes は HEAD に混入しない。"""
    rel_paths = [str(p.relative_to(provider.repo_root)) for p in paths]
    issue_ref = _format_issue_ref(rid.value)  # local: bare ID, github: #N
    msg = f"chore(local): {action} for {issue_ref}"
    # `--only` (`-o`) で partial commit:
    #   - 指定 pathspec の working-tree 内容を temporary index に積み直して commit する
    #   - それ以外の staged changes は HEAD に **含まれず**、commit 後も index に残る
    #   - 出典: man git-commit の `--only` 節（参照情報セクション参照）
    subprocess.run(
        ["git", "commit", "--only", "-m", msg, "--", *rel_paths],
        cwd=provider.repo_root,
        check=True,
    )
```

**atomicity の根拠（Must Fix 1 への直接回答）**:

- `git commit -m <msg>` のみだと index 全体（user が事前に staged した無関係 file 含む）が HEAD に混入する。本設計初版（`git add <paths> -> git commit -m ...`）はこの欠陥があったため修正
- `git commit --only/-o <pathspec> -- <paths>` は man git-commit の `--only` 節より「指定 pathspec の working-tree 内容を **temporary index** に積み直して commit する」仕様。**事前に staged だった他の file は temporary index に積まれず、HEAD に混入しない。commit 後も元の index にそのまま残る**
- 検証手順（テスト戦略 § Medium に追加）: temp repo で `target.txt` と `other.txt` を staged 状態にし、`git commit -o target.txt -m test` を実行 → `git show --stat HEAD` で target のみが入ることを確認、`git diff --cached --name-only` で other.txt が staged のまま残ることを確認
- `git add <paths>` を別途呼ばない設計に変更: `git commit -o <pathspec>` 自体が working-tree から temporary index への取り込みを行うため、明示の `git add` は不要かつ冗長（全体 index に余計な状態を作るのを避ける）

**user の他の staged change の扱い**:
- temporary index 経由で隔離されるため、HEAD に混入しないことが Git 仕様で保証される
- `--commit` 完了後、user の元 index は変更されずそのまま残る（user の作業を破壊しない）

`--commit` 不指定時の挙動は変更前と bit-exact 一致（既存テスト全 PASS が回帰防止）。

### B (続): `issue-close` skill の救済ロジック書き換え

**変更前** (`SKILL.md:266`):
```bash
test -z "$(git status --porcelain)" || { echo "ABORT: uncommitted changes in base worktree $BASE_WT"; exit 1; }
```

**変更後**:
```bash
# 条件 1 + 条件 2 を同一 regex で判定し、永続化由来 path のみを抽出する
DIRTY=$(git status --porcelain)
if [ -n "$DIRTY" ]; then
    # LocalProvider の永続化命名規則（cli_main.py:1082 / providers/local.py 参照）:
    #   - issue.md         : .kaji/issues/<id>-<slug>/issue.md
    #   - comment markdown : .kaji/issues/<id>-<slug>/comments/<4桁seq>-<machine_id>.md
    # 上記命名規則に一致しない dirty path があれば ABORT（手動編集や予期外ファイルを silent
    # に commit しないため）。
    # 命名規則の出典:
    #   - issue_id     : ``local-<machine_id>-<n>`` (machine_id 文字種は providers/local.py:36
    #                    `_MACHINE_ID_RE = ^[a-z0-9]{1,16}$`)
    #   - slug suffix  : providers/context.py:17 `_SLUG_RE = ^[a-z0-9][a-z0-9-]{0,39}$`
    #   - comment file : providers/local.py:586 `f"{seq}-{self.machine_id}.md"` (seq=4 桁)
    # → issue_dir 全体: ``.kaji/issues/<issue_id>-<slug>/``、slug 部分は `[a-z0-9-]+`
    ISSUE_DIR_RE='^\.kaji/issues/[issue_id]-[a-z0-9-]+/(issue\.md|comments/[0-9]{4}-[a-z0-9]{1,16}\.md)$'
    # `git status --porcelain` v1 形式: 先頭 2 文字が status, 続いて space + path
    # rename / copy 形式 (`R  old -> new` / `C  old -> new`) は救済対象外として ABORT に倒す
    UNRELATED=$(printf '%s\n' "$DIRTY" | awk -v re="$ISSUE_DIR_RE" '
        {
            # rename/copy は対応外（OB 観測なし。混入したら user 意図確認のため ABORT）
            if (match($0, /->/)) { print; next }
            # 先頭 3 文字（status + space）を除去して path を取り出す
            path = substr($0, 4)
            # quoted path は対応外（path に空白等が無い前提。混入したら ABORT）
            if (substr(path, 1, 1) == "\"") { print; next }
            if (path !~ re) { print }
        }
    ')
    if [ -n "$UNRELATED" ]; then
        echo "ABORT: dirty files outside LocalProvider persistence whitelist in base worktree $BASE_WT:"
        printf '%s\n' "$UNRELATED"
        echo "  Allowed pattern: $ISSUE_DIR_RE"
        exit 1
    fi
    # 条件 2: whitelist 命名規則に厳密に一致する pathspec のみを add
    #   - 注意: `comments/` ディレクトリ丸ごと add は不可（comments/note.txt 等の
    #     非命名規則ファイルを巻き込むため）。glob で4桁seq prefix の .md のみに絞る
    git add \
        ".kaji/issues/[issue_id]-*/issue.md" \
        ".kaji/issues/[issue_id]-*/comments/[0-9][0-9][0-9][0-9]-*.md" \
        2>/dev/null || true
    # 救済 commit も `--only/-o` で atomic 化する（標準動線と同じ atomicity 保証）
    git commit --only -m "chore(local): salvage uncommitted issue files for [issue_ref]" -- \
        ".kaji/issues/[issue_id]-*/issue.md" \
        ".kaji/issues/[issue_id]-*/comments/[0-9][0-9][0-9][0-9]-*.md" \
        || { echo "ABORT: salvage commit failed"; exit 1; }
    # 条件 3: 救済後の再検証
    test -z "$(git status --porcelain)" || { echo "ABORT: residual dirty files after salvage commit"; git status --porcelain; exit 1; }
fi
```

判定の要点（条件 2 の機械的判定方法 = レビュー指摘事項 Must Fix 2 への直接回答）:

- **path 範囲 whitelist** で「永続化由来」を機械的に近似する。`git diff` の内容（行単位）では「skill が書いたか / 手動編集か」を判定不可能なため、**追加対象を LocalProvider の命名規則 (`issue.md` と `comments/<4桁seq>-<machine_id>.md`) に厳密一致する path に限定**する
- 初版設計の不備（`comments/` ディレクトリ全体を add）を修正:
  - 旧: `git add ".kaji/issues/[issue_id]-*/comments/"` → `comments/note.txt` 等の **非命名規則ファイル** が巻き込まれる
  - 新: `git add ".kaji/issues/[issue_id]-*/comments/[0-9][0-9][0-9][0-9]-*.md"` → 4桁数字 prefix + `.md` 拡張子の glob 限定
- 条件 1 の dirty 検査でも同じ命名規則 regex を使って「LocalProvider 永続化が生成しうる path 以外」を即 ABORT。`<unknown>.md` / `notes.md` / `comments/scratch.txt` 等の予期外ファイルが silent に commit されるリスクを排除
- 救済 commit 自体も `git commit --only -- <pathspec>` の partial commit を使い、user の他の staged changes が HEAD に混入しないことを保証（標準動線の `_commit_local_issue_change` と同じ atomicity 保証）
- 条件 3 の再検証で「whitelist add では拾えなかった残差」（例: 削除された file の rm 操作 / rename / copy）が残っていたら ABORT。これにより救済の安全側挙動を担保

**glob と regex の対応関係**:

| 段階 | 機構 | パターン | 用途 |
|------|------|---------|------|
| 検査（条件 1） | awk regex | `^\.kaji/issues/<id>-[a-z0-9-]+/(issue\.md\|comments/[0-9]{4}-[a-z0-9-]+\.md)$` | dirty path の網羅検査（rename/quoted path も ABORT に倒す） |
| add（条件 2） | shell glob | `comments/[0-9][0-9][0-9][0-9]-*.md` | git add の pathspec 限定（regex は使えない） |
| commit（条件 2） | shell glob | 同上 | `git commit --only -- <pathspec>` の partial commit |
| 再検証（条件 3） | `git status --porcelain` 空チェック | — | 残差が無いこと |

**命名規則の出典**:
- machine_id: `kaji_harness/providers/local.py:36` `_MACHINE_ID_RE = re.compile(r"^[a-z0-9]{1,16}$")` （ハイフン無し）
- slug: `kaji_harness/providers/context.py:17` `_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")` （kebab-case）
- comment file: `kaji_harness/providers/local.py:586` `f"{seq}-{self.machine_id}.md"`（seq は `f"{seq:04d}"` で 4 桁ゼロパディング）

命名規則が変わる場合は本 regex / glob と `issue-close/SKILL.md` を同時に更新する旨を該当 SKILL.md と `providers/local.py` 双方の docstring に相互注記する。

### 後方互換性

- A: 既存 workflow YAML はすべて小文字を使っているため、validator 追加で全 workflow が PASS。`feature-development-local.yaml` の 4 箇所修正のみが残作業
- B: `--commit` 不指定時の `kaji issue {comment,edit}` の挙動は変更前と完全一致。skill 側で `--commit` を付け始めても、CI / scripts で従来動線を使う既存呼び出し点は影響を受けない

## 変更スコープ

| 種別 | パス | 変更内容 |
|------|------|---------|
| 主 (A) | `.kaji/wf/feature-development-local.yaml` | line 27, 46, 67, 89 を `xhigh`/`high` に修正 |
| 主 (A) | `kaji_harness/workflow.py` | `_AGENT_EFFORT_ALLOWED` 辞書追加 + `_parse_workflow` で reject |
| 主 (A) | `tests/test_workflow.py` (or 同等) | effort 大文字 reject の unit test 追加（修正前 FAIL → 修正後 PASS） |
| 主 (A) | `docs/dev/workflow-authoring.md` | UI 表示と YAML 値の差異 + 採択方針 (A2) を明記 |
| 主 (B) | `kaji_harness/cli_main.py` | `_local_issue_comment` / `_local_issue_edit` に `--commit` フラグ追加 + `_commit_local_issue_change` helper 実装 |
| 主 (B) | `tests/test_cli_main.py` (or 同等) | `--commit` フラグの atomic commit 検証テスト追加 |
| 主 (B) | `.claude/skills/issue-close/SKILL.md` | Step 2 を 3 段ガード（path 範囲 / whitelist add / 残差再検証）に書き換え |
| 主 (B) | 各 skill SKILL.md | `kaji issue {comment,edit}` 呼び出しに `--commit` 反映: `issue-design`, `issue-implement`, `issue-review-{design,code}`, `issue-fix-{design,code}`, `issue-verify-{design,code}`, `i-dev-final-check`, `i-doc-final-check`, `issue-fix-ready`, `issue-start`, `i-doc-update`, `i-doc-{review,fix,verify}` |
| 副 | 既存 fixture / pytest helpers | `--commit` フラグを使うテストで Git 操作の isolation（temp repo）を整備 |

## 方針

### 設計判断 1: A の方針 = (A2) agent 別 allowed values

**選択肢**:
- (A1) 共通 subset (`low/medium/high/xhigh`) のみ全 agent に強制
- (A2) agent 別 allowed values を辞書で保持

**採択: (A2)**

**理由**:
- (A1) は simple だが claude 専用 `max` / codex 専用 `none/minimal` を将来的にも禁じる構造になり、agent 拡張時の再修正コストが大きい
- (A2) は新 agent 追加時に `_AGENT_EFFORT_ALLOWED` 辞書に 1 行加えるだけで済む。allowed values 辞書を持たない agent は validation skip（passthrough）にすることで、未知の agent が即 reject されない柔軟性も確保
- agent 仕様の一次情報（`claude --help` の `--effort` 列挙、codex の error message）から派生する 2 値辞書なので、実装複雑度は (A1) と本質的に変わらない

**設計判断 1' の補足**: 辞書の値（`{"low","medium","high","xhigh","max"}` 等）は **mutable な module-level 定数** として管理し、agent 仕様変更時に 1 行修正で追従する。docs（`workflow-authoring.md`）にも同じ表を載せ、agent 仕様の出元を明記する。

### 設計判断 2: B の commit 動線対象範囲 = (B2) `comment + edit`

**選択肢**:
- (B1) `comment` のみ
- (B2) `comment + edit`
- (B3) `comment + edit + close`

**採択: (B2)**

**理由**:
- (B1) は OB に対する不十分な対応。`issue-start` / `i-dev-final-check` 等が `edit` で issue.md を更新する動線を取りこぼす
- (B2) は OB の蓄積パターン全てをカバーする最小スコープ
- (B3) の `close` は `issue-close` skill が「Step 4 で `kaji issue close` → `git add` → `git commit` を確実に連結する」既存設計のため、CLI 側の `--commit` 対応は冗長。`issue-close` Step 4 の skill 側で完結する設計を維持する方が単純

**`create` の扱い**: `create` も新規ファイルを未コミットで作るが、`/issue-create` skill が呼ぶ場面では user が手動で `git add && commit` する慣習が確立しており、OB（dev workflow 中での蓄積）には寄与しない。本 Issue では bundle スコープから除外し、必要なら別 Issue で扱う。

### 設計判断 3: 救済機構の保持 + 厳格化

**選択肢**:
- (C1) 救済機構を完全に廃止し、dirty なら無条件 ABORT
- (C2) 救済機構を 3 段ガードで残す

**採択: (C2)**

**理由**:
- 標準動線が (B2) でカバーされていれば dirty 蓄積は起きないが、skill 側の bug や user の手動操作で例外的に dirty が残る可能性は残る
- (C1) は安全側だが、close 動線が止まると workflow 完了できず user 体験を悪化させる。3 段ガードを厳格に書けば運用上の安全性を担保できる
- (C2) のガードは whitelist pattern + 残差再検証の組み合わせで「skill が新規生成しうる差分のみ救済 / それ以外 ABORT」を機械的に保証する

### 設計判断 4: bundle vs split

両 bug は別系統だが、本 Issue で**同時修正する**:
- 両方 main 直 commit 系の運用問題で関連性が強い（CLAUDE.md の `chore(local)` / `chore(workflow)` 系の整流）
- 設計書 / レビュー / 実装サイクルの重複コストを避ける
- 個別 PR にすると merge order の依存関係が発生する（test fixture 重複等）

ただし**実装フェーズでは A と B のテストファイルを分離**し、片方の修正失敗が他方に波及しないようにする（Conventional Commits の commit 単位は `fix:` × 2 個または `fix:` × 1 + `chore:` × 1 の組合せで分離）。

## テスト戦略

> **CRITICAL**: bug 修正のため、修正前に Red になる再現テストを必ず 1 本以上定義する（[bug.md](../../.claude/skills/_shared/design-by-type/bug.md#8-テスト戦略再現テスト必須) より）。

### 変更タイプ
- **実行時コード変更**（A: workflow.py の validator / B: cli_main.py の `--commit` フラグ実装）
- **設定変更**（A: feature-development-local.yaml の effort 値修正）
- **ドキュメント変更**（A: workflow-authoring.md / B: skill SKILL.md 多数）

設定 / ドキュメント変更は変更固有検証（`kaji validate` / `make verify-docs`）で恒久テストは追加しない。実行時コード変更には Small / Medium テストを追加する。

### Small テスト（A: effort validator）

[testing-convention.md](../../docs/dev/testing-convention.md) の判定基準より、純粋な workflow.py 内ロジックは Small に該当（外部 API / DB / file I/O なし。`load_workflow_from_str` で yaml string を直接 parse）。

**追加テスト**（再現テスト）:

| テスト名 | 検証内容 | 修正前 / 修正後 |
|----------|----------|----------------|
| `test_parse_rejects_uppercase_effort_for_codex` | codex agent step に `effort: High` を渡すと `WorkflowValidationError` が `effort 'High' is not valid for agent 'codex' (allowed: ['high','low','medium','minimal','none','xhigh'])` 形式で raise される | 修正前 PASS（validator 不在で素通し）→ 修正後 FAIL→ 修正後 expectation 反転 → PASS |
| `test_parse_rejects_uppercase_effort_for_claude` | claude agent step に `effort: xHigh` を渡すと WorkflowValidationError | 同上 |
| `test_parse_accepts_lowercase_effort_for_claude` | claude agent step に `effort: max` （claude 専用値）を渡すと PASS | 修正後 PASS（agent 別辞書の正しい挙動の保証） |
| `test_parse_accepts_lowercase_effort_for_codex` | codex agent step に `effort: minimal` （codex 専用値）を渡すと PASS | 同上 |
| `test_parse_skips_validation_for_unknown_agent` | gemini agent step（allowed values 辞書未登録）に任意の effort 値を渡すと PASS（passthrough） | 修正後 PASS（拡張性の保証） |
| `test_parse_rejects_non_string_effort` | `effort: 42` のような int を渡すと `effort must be a string, got int` で reject | 修正後 PASS |

**重要**: bug 再現テストは「修正前にコミット → CI で FAIL を観測 → 修正コードコミットで PASS に遷移」の 2 段階で記録する。

### Small テスト（B: `--commit` フラグ）

`_local_issue_comment` / `_local_issue_edit` の `--commit` 分岐は Git 操作（subprocess）を含むため、Small / Medium のどちらに分類するかが微妙。判定基準（[testing-convention.md](../../docs/dev/testing-convention.md#判定基準)）の「ファイル I/O / 内部サービス結合あり → Medium」に従い **Medium** とする。ただし、helper の純粋なロジック分（commit message 生成、path whitelist フィルタ）は Small で個別に検証する。

| テスト名 | サイズ | 検証内容 |
|----------|--------|---------|
| `test_commit_local_issue_change_message_format` | Small | helper が生成する commit message が `chore(local): comment for <issue_ref>` 形式（local provider なら bare ID `local-p1-16`、github provider なら `#<n>`。`_format_issue_ref` 経由） |
| `test_local_issue_change_paths_whitelist` | Small | helper の path 引数を引き渡しのみで filter しないこと（呼出側責務）の確認 |

### Medium テスト（B: `--commit` フラグ end-to-end）

| テスト名 | 検証内容 | 修正前 / 修正後 |
|----------|----------|----------------|
| `test_local_issue_comment_with_commit_creates_atomic_commit` | temp repo 内で `kaji issue comment <id> --body "x" --commit` 実行 → working tree が clean、最新 commit に `.kaji/issues/<id>/comments/0001-*.md` のみ含まれる | 修正前 FAIL（`--commit` 不存在）→ 修正後 PASS |
| `test_local_issue_edit_with_commit_creates_atomic_commit` | 同上 (`kaji issue edit`) | 同上 |
| **`test_local_issue_comment_with_commit_excludes_unrelated_staged_files`** (**Must Fix 1 再現テスト**) | temp repo で **事前に `other.txt` を staged 状態** にしてから `kaji issue comment <id> --body "x" --commit` 実行。検証: (1) 最新 commit には `.kaji/issues/<id>/comments/0001-*.md` のみ含まれる（`git show --stat HEAD` で確認）、(2) `other.txt` は **commit 後も staged のまま** 残る（`git diff --cached --name-only` で確認）。これにより `git commit --only` の partial commit が user の他 staged change を巻き込まないことを保証 | 旧設計（`git add + git commit`）で **FAIL**（`other.txt` が HEAD に混入）→ 新設計（`git commit --only`）で PASS |
| `test_local_issue_edit_with_commit_excludes_unrelated_staged_files` | 同上 (`kaji issue edit`) | 同上 |
| `test_local_issue_comment_without_commit_leaves_working_tree_dirty` | `--commit` 無しの既存挙動が変わらないこと（working tree dirty） | 修正前後 PASS（後方互換性の保証） |
| `test_local_issue_comment_commit_skipped_when_no_changes` | 異常系: `comment_issue` が file を書かなかったケース（実用上発生しないが防御）で `git commit` が `nothing to commit` で fail せず exit 0 | 修正後 PASS |

### Medium テスト（B: `issue-close` skill rescue ロジック）

`issue-close` skill の rescue ロジックは bash で書かれるため、kaji_harness の pytest 範囲外に見えるが、**bash テスト fixture を pytest で起動して subprocess 検証する Medium テスト**として 1 本以上追加する（rescue が path whitelist の境界条件で正しく動くことの再現テスト）。

| テスト名 | 検証内容 | 修正前 / 修正後 |
|----------|----------|----------------|
| **`test_issue_close_rescue_excludes_non_markdown_in_comments_dir`** (**Must Fix 2 再現テスト**) | temp repo に以下を配置: (a) skill 永続化由来の `.kaji/issues/<id>-<slug>/issue.md` (modified)、(b) skill 永続化由来の `.kaji/issues/<id>-<slug>/comments/0001-pc1.md` (untracked)、(c) **非命名規則ファイル** `.kaji/issues/<id>-<slug>/comments/note.txt` (untracked)。`issue-close` skill の Step 2 ロジック（bash）を起動。検証: (i) 旧設計（`git add ".kaji/issues/<id>-*/comments/"`）では note.txt が rescue commit に巻き込まれる（FAIL）、(ii) 新設計（glob `[0-9][0-9][0-9][0-9]-*.md` 限定）では rescue commit に note.txt が含まれず、note.txt は untracked のまま残る。条件 1 の awk regex 検査で note.txt の存在自体を ABORT に倒す経路も同テスト内で検証 | 旧設計で FAIL → 新設計で PASS |
| `test_issue_close_rescue_aborts_on_unrelated_dirty_path` | `.kaji/issues/<id>/issue.md` が dirty かつ `kaji_harness/foo.py` も dirty なケースで rescue が ABORT する（exit 1）ことを検証 | 修正後 PASS（旧設計でも ABORT 動線は機能していたが、`UNRELATED` 検出 regex 厳格化の回帰防止） |
| `test_issue_close_rescue_residual_check_after_commit` | rescue commit 直後に `git status --porcelain` が空でない（例: rename / copy 由来の残差）ケースで ABORT する | 修正後 PASS（条件 3 の挙動保証） |

> **テスト実装メモ**: skill の bash ロジック検証は `tests/skills/test_issue_close_rescue.sh`（または `tests/test_issue_close_skill.py` 内で subprocess.run("bash", "-c", ...) で起動）の形式で実装可能。pytest fixture で temp repo をセットアップし、rescue ロジックを切り出した bash 関数を source して呼ぶ。

### Large テスト

不要。理由（[testing-convention.md](../../docs/dev/testing-convention.md#docs-only--metadata-only--packaging-only-変更) の 4 条件）:
1. ✓ 独自ロジックは A の validator / B の helper / B の rescue bash のみで、Small / Medium で検証
2. ✓ 実 API / E2E 疎通なし
3. ✓ 既存の `make check` が CLI dispatch / workflow runner のスモークを担保
4. ✓ Large 化のための実 API は本 Issue のスコープ外

### 変更固有検証（恒久テスト化しないもの）

- `kaji validate .kaji/wf/*.yaml workflows/*.yaml` — 全 workflow YAML が修正後 effort 値で PASS することを確認（CI で `make check` の前段に実行可能）
- `make verify-docs` — workflow-authoring.md / skill SKILL.md 群のリンク整合性確認
- **dev workflow 1 サイクル手動実行** — 任意の小規模 Issue を `/issue-start` から `/issue-close` まで流し、close 直前の base worktree が `git status --porcelain` の出力空であることを目視確認（自動化は CI 構築コストに見合わないため恒久化しない）

### 恒久テストを追加しない理由（YAML 値修正部分のみ）

`feature-development-local.yaml` の line 27, 46, 67, 89 の値修正そのものに対しては恒久回帰テストを追加しない:
1. ✓ ロジック変更なし（YAML 設定値の修正のみ）
2. ✓ A の validator が同一カテゴリの回帰を機械的に検出（次回大文字混入で `kaji validate` / `_parse_workflow` が即 fail）
3. ✓ validator のテストで「YAML が許容値の範囲」を保証
4. ✓ 個別 YAML 値の正解 snapshot を恒久化すると、将来の effort 値ポリシー変更時に snapshot を機械修正する手間が増える

## 影響ドキュメント

| ドキュメント | 影響 | 理由 |
|-------------|------|------|
| `docs/adr/` | なし | 新たな技術選定なし。validator パターンは既存 `_parse_workflow` の延長 |
| `docs/ARCHITECTURE.md` | なし | アーキテクチャレベルの変更なし |
| `docs/dev/workflow-authoring.md` | **あり** | A: effort 値の許容範囲（agent 別） + UI 表示と YAML 値の差異を明記 |
| `docs/dev/development_workflow.md` | なし | フローそのものは不変 |
| `docs/dev/shared_skill_rules.md` | あり（軽微） | B: 各 skill が `kaji issue {comment,edit}` を呼ぶ際の `--commit` 慣習を追記（または個別 SKILL.md のみで完結する場合は不要） |
| `docs/dev/workflow_overview.md` | なし | overview レベルの記述に effort 詳細は無い |
| `docs/reference/python/*` | なし | Python 規約変更なし |
| `docs/cli-guides/` | あり（軽微） | B: `kaji issue` の `--commit` フラグを CLI guide に追加（既存 guide 構成に依存） |
| `CLAUDE.md` | なし | 既存規約変更なし |
| `.claude/skills/issue-close/SKILL.md` | **あり** | B: Step 2 ガードの 3 段化 |
| `.claude/skills/{issue-design,issue-implement,issue-review-design,issue-review-code,issue-fix-design,issue-fix-code,issue-verify-design,issue-verify-code,i-dev-final-check,i-doc-final-check,issue-fix-ready,issue-start,i-doc-update,i-doc-review,i-doc-fix,i-doc-verify}/SKILL.md` | **あり** | B: `kaji issue {comment,edit}` 呼び出しに `--commit` を反映 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| codex error message（実行ログ） | `.kaji-artifacts/local-p1-5/runs/2605091602/run.log:5` | `unknown variant 'High', expected one of 'none', 'minimal', 'low', 'medium', 'high', 'xhigh'` — codex `model_reasoning_effort` の正式 allowed values（小文字限定）の出典 |
| close 救済 commit のログ | `.kaji-artifacts/local-p1-14/runs/2605091631/close/console.log:5-12` | `base worktree に未コミット変更が残っています` の OB 観測ログ |
| claude `--effort` allowed values | `claude --help` の出力（ローカル CLI） | `low,medium,high,xhigh,max` — claude の effort 許容値の出典。Issue 本文 § 問題 A § Expected Behavior に記載 |
| 現状の effort 値分布 | `.kaji/wf/feature-development-local.yaml:27,46,67,89` | claude agent step 4 箇所の `xHigh`/`High` 残存箇所 |
| codex 系修正の前例 | git commit `a709a31` (`chore(wf): update feature-development-local.yaml effort levels and resume settings`) | codex agent 系のみ effort 修正、claude 系の漏れ |
| close 救済 commit の前例 | git commit `0b14612`, `3501fc2` | Issue 14 close 時の Claude による救済 commit（仕様逸脱） |
| `issue-close` skill の ABORT ガード設計 | `.claude/skills/issue-close/SKILL.md:266` | 救済すべきでないという既存設計意図の出典 |
| effort passthrough 箇所（claude） | `kaji_harness/cli.py:240` | `args += ["--effort", step.effort]` — 検証無し passthrough |
| effort passthrough 箇所（codex） | `kaji_harness/cli.py:267` | `args += ["-c", f'model_reasoning_effort="{step.effort}"']` — 検証無し passthrough |
| effort 素通し箇所 | `kaji_harness/workflow.py:141` | `effort=step_data.get("effort")` — runtime validation 不在の出典 |
| `_local_issue_comment` 実装 | `kaji_harness/cli_main.py:1068-1083` | `comment` の永続化のみで commit を伴わない出典 |
| `_local_issue_edit` 実装 | `kaji_harness/cli_main.py:1044-1065` | `edit` の永続化のみで commit を伴わない出典 |
| Python dataclass Literal hint の runtime 挙動 | https://docs.python.org/3/library/typing.html#typing.Literal | "Literal types ... are validated by static type checkers" — runtime 検証されない仕様の出典（"At runtime, an arbitrary value is allowed as type argument to Literal[...], but type checkers may impose restrictions"） |
| `git commit --only` (partial commit) | https://git-scm.com/docs/git-commit#Documentation/git-commit.txt---only | "Make a commit by first updating the index with the contents of the paths specified on the command line, ignoring previously staged changes for paths that are not on the command line." — 指定 pathspec のみを commit し、その他の staged change は HEAD に混入しない atomicity の出典（Must Fix 1 への解決根拠） |
| LocalProvider machine_id 文字種 | `kaji_harness/providers/local.py:36` | `_MACHINE_ID_RE = re.compile(r"^[a-z0-9]{1,16}$")` — comment ファイル名の machine_id 部分の文字種出典（rescue regex / glob の根拠） |
| LocalProvider slug 文字種 | `kaji_harness/providers/context.py:17` | `_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")` — issue dir 名の slug 部分の文字種出典 |
| LocalProvider comment ファイル命名規則 | `kaji_harness/providers/local.py:586` | `path = cdir / f"{seq}-{self.machine_id}.md"` — comment ファイルが `<4桁seq>-<machine_id>.md` で生成される出典（rescue glob `[0-9][0-9][0-9][0-9]-*.md` の根拠） |
| local issue_ref 整形 | `kaji_harness/state.py::_format_issue_ref` | local provider の issue_ref が bare ID（`#` 無し）で整形される出典（commit message が `chore(local): comment for local-p1-16` 形式になる根拠） |
| 関連 Issue: type:* label と branch_prefix 不整合 | `local-p1-17` | 別 Issue（本 Issue とは独立、コンテキスト変数 `branch_prefix=fix` の不整合の出元） |
| 関連 Issue: make check fixture 漏れ | `local-p1-15` | 別 Issue（既に close 済。本 Issue とは独立） |
| testing-convention | `docs/dev/testing-convention.md` | テストサイズ判定基準と「再現テスト必須」の根拠 |
| design-by-type/bug | `.claude/skills/_shared/design-by-type/bug.md` | bug type 設計の必須セクション規約の出典 |


</details>


## 概要

local-p1-5/14 完了後の workflow ログ調査で発見した 2 つの独立バグを bundle として扱う:
- **問題 A**: `.kaji/wf/feature-development-local.yaml` の effort 値に大文字 (`High`/`xHigh`) が残存し、codex agent に渡ると `unknown variant` で workflow が ERROR 停止する。許容値は Claude/Codex で **完全に一致しない** ため、validator 設計には方針判断が必要
- **問題 B**: 各 skill の `kaji issue comment` / `kaji issue edit` が `.kaji/issues/<id>/` 配下を更新するが commit を伴わず、base worktree (main) に未コミットファイルが close 直前まで蓄積、`issue-close` skill の ABORT ガードを Claude が逸脱して救済 commit する

両者とも main 直 commit 系（`.kaji/wf/` または `.kaji/issues/`）の運用問題で関連性が強い。

---

## 問題 A: workflow YAML effort 値が CLI 仕様と不整合

### Observed Behavior（OB）

`.kaji/wf/feature-development-local.yaml` で agent ごとに大文字/小文字が混在:
- claude agent (line 27, 46, 67, 89): `xHigh`, `xHigh`, `High`, `High` ← 大文字
- codex agent (line 36, 56, 78, 100): `high` ← 小文字
- final-check (line 111, claude): `medium` ← 小文字

`.kaji-artifacts/local-p1-5/runs/2605091602/run.log:5` の実害:

\`\`\`
CLIExecutionError: Step 'review-design' CLI exited with code 1:
Error loading config.toml: unknown variant \`High\`,
expected one of \`none\`, \`minimal\`, \`low\`, \`medium\`, \`high\`, \`xhigh\`
in \`model_reasoning_effort\`
\`\`\`

過去 commit `a709a31` で codex 系のみ修正、claude 系 4 箇所は大文字残存。`kaji_harness/cli.py:240, 267` で値は CLI に passthrough されるが、validator が無いため codex agent に大文字を渡した瞬間 workflow ERROR で停止する。

### Expected Behavior（EB）

1. workflow YAML の effort 値は agent 仕様と一致した値で書かれている
2. `kaji_harness/workflow.py` の YAML load / validation 時点で許容外の値を reject する（**`models.py` の dataclass `Literal` hint は静的解析のみで runtime 検証にならないため、validation は workflow.py 側で実装**）
3. `docs/dev/workflow-authoring.md` に「両 CLI の対話 UI 選択画面では大文字 (`Low/Medium/High/Extra high`) で表示されるが YAML には小文字を書く」+ 採択した許容値方針が明記されている

**重要: Claude と Codex の許容値は一致しない**:
- claude `--effort`: `low, medium, high, xhigh, max` (`claude --help` で確認) — `max` あり、`none/minimal` なし
- codex `model_reasoning_effort`: `none, minimal, low, medium, high, xhigh` (error message で確認) — `none/minimal` あり、`max` なし
- 共通 subset: `low, medium, high, xhigh`
- claude のみ: `max`
- codex のみ: `none, minimal`

UI 表示の混乱（罠）:
- codex 対話 UI: `1. Low / 2. Medium (default) / 3. High (current) / 4. Extra high` ← 大文字
- claude UI: `● High effort ←/→ to adjust` ← 大文字
- → CLI 内部値と UI 表示が異なるため、開発者が UI 表示を見て YAML に書くと壊れる

**設計フェーズで決定すべき方針**: global whitelist として `Literal[...]` 一律強制すると片方しかサポートしない値が永久に使えない。以下のどちらかを選ぶ:
- (A1) **共通 subset のみ許可**: `Literal["low","medium","high","xhigh"]` を全 agent に強制。simple だが claude `max` / codex `none/minimal` を将来も使えなくする
- (A2) **agent 別 allowed values を YAML parse 時に検証**: `step.agent` の値で許容値辞書を切り替え。expressive だが mapping を永続化する必要あり、将来 agent / CLI 仕様が変わるたび追従

### 再現手順

1. `.kaji/wf/feature-development-local.yaml` の codex agent step (例: line 36 の review-design) の effort を `high` → `High` に変更
2. `kaji run .kaji/wf/feature-development-local.yaml <issue>` を実行
3. review-design step で `CLIExecutionError: ... unknown variant 'High'` が発生し workflow が ERROR 終了する

---

## 問題 B: Issue ファイルが close 直前まで未コミットで蓄積

### Observed Behavior（OB）

`.kaji-artifacts/local-p1-14/runs/2605091631/close/console.log:5-12`:

\`\`\`
base worktree に未コミット変更が残っています（issue.md 修正 + コメントファイル）
issue.md に設計書が追加され、コメント 0006〜0009 が未追跡
\`\`\`

`.claude/skills/issue-close/SKILL.md:266` の Step 2 は本来:

\`\`\`bash
test -z \"\$(git status --porcelain)\" || { echo \"ABORT: uncommitted changes in base worktree \$BASE_WT\"; exit 1; }
\`\`\`

と ABORT 設計だが、Claude が指示を逸脱して救済 commit (`0b14612`, `3501fc2`) を作って続行した。

**蓄積する未コミット変更の出元（`comment` 単独ではない）**:
- `kaji issue comment` — `.kaji/issues/<id>/comments/<seq>.md` を新規作成
- `kaji issue edit` — `.kaji/issues/<id>/issue.md` を更新（`issue-start` / `i-dev-final-check` / `i-doc-final-check` / `issue-fix-ready` 等が使用）
- `kaji issue close` — `.kaji/issues/<id>/issue.md` の frontmatter (`state: closed` 等) を更新
- 設計書ファイル `draft/design/issue-<id>-<slug>.md` — `issue-design` skill が新規作成

→ `comment` だけ `--commit` 対応しても `edit` 由来の `issue.md` 変更が残る。

### Expected Behavior（EB）

各 skill で local issue 永続化操作を実行した直後にコミットされ、`issue-close` skill 開始時点で base worktree が clean である。

**設計フェーズで決定すべき方針**:

1. **commit 動線対象範囲**: 以下のうちどれを `--commit` 対応するか
   - (B1) `comment` のみ
   - (B2) `comment + edit` ← 最低限ここまで（issue.md 更新を含む）
   - (B3) `comment + edit + close`（close は merge 直前で別軌道のため要検討）

2. **`issue-close` 救済 commit の発動条件**（救済を残す場合は以下 3 点を厳格化）:
   - 条件 1: dirty file が **すべて `.kaji/issues/` 配下** であること（unrelated changes が混在していたら救済しない）
   - 条件 2: local issue 永続化由来の変更 (`comment/edit/close` の出力) のみを commit 対象にすること（手動編集が混在しても救済しない）
   - 条件 3: 自動 commit 後に **再度 `git status --porcelain` を確認**し、残差があれば ABORT する

3. **推奨は (A)+(B) 二段構え**: 標準動線 ((B2) 以上) で蓄積を防ぎ、救済機構 (条件 1〜3) は安全装置として残す。base worktree の unrelated changes を抱えたまま close が進むリスクを排除する。

### 再現手順

1. 任意の Issue を `/issue-start` → `/issue-design` → `/issue-review-design` → `/issue-fix-design` → ... と進める
2. 各 skill が `kaji issue comment` でコメントを生成、`issue-start` / `i-dev-final-check` / `issue-fix-ready` 等が `kaji issue edit` で issue.md を更新
3. close 直前に base worktree で `git status` を実行
4. `.kaji/issues/<issue_id>/comments/` 配下の `.md` ファイルと `issue.md` の更新が untracked / unstaged のまま蓄積していることを観測

---

## 完了条件

### 設計段階で確認

- [x] 問題 A: validator 実装位置が `workflow.py` の YAML load / validation 時点であることが設計書に明記されている（`models.py` の dataclass `Literal` hint は静的解析のみで runtime 検証にならない理由付き）
- [x] 問題 A: 許容値の方針 (A1) 共通 subset only / (A2) agent 別 allowed values のどちらを採るか決定
- [x] 問題 B: commit 動線対象範囲 (B1) `comment` のみ / (B2) `comment + edit` / (B3) `comment + edit + close` のうちどれを採用するか決定
- [x] 問題 B: `issue-close` 救済 commit の発動条件 3 点（dirty 範囲 / 永続化由来限定 / 自動 commit 後 git status 再検証）が設計書に明記されている
- [x] 問題 B: 救済条件 2「local issue 永続化由来のみ」の機械的判定方法が設計書に明示されている（候補: 対象パス制約 `.kaji/issues/<id>/` 限定 / 永続化操作実行直後の write set 記録 / 許容差分範囲の whitelist 化 等。git diff だけでは手動編集との由来判定が不可能なため、判定可能な具体ロジックに落とすこと）
- [x] 同根の調査結果（他 workflow YAML に大文字 effort が無いか / 他 skill にも未 commit パターンが無いか / `kaji issue create` 直後の Issue ファイル commit 動線も同様か）が設計書に記載

### 実装段階で確認

- [x] 問題 A: `.kaji/wf/feature-development-local.yaml` line 27, 46, 67, 89 を採択方針に沿って修正
- [x] 問題 A: `kaji_harness/workflow.py` の YAML load / validation で effort validator 実装 + 許容外値を reject する unit test 追加（修正前 FAIL → 修正後 PASS）
- [x] 問題 A: `docs/dev/workflow-authoring.md` の effort 値説明に「UI 表示の大文字 ≠ YAML 必須の小文字」+ 採択した許容値方針 (A1/A2) を明記
- [x] 問題 A: `kaji validate .kaji/wf/*.yaml workflows/*.yaml` が全 workflow で通過
- [x] 問題 B: 採択範囲 (B1/B2/B3) に従い `kaji_harness/cli_main.py` の `kaji issue {comment,edit,close}` に `--commit` フラグ実装
- [x] 問題 B: 採択範囲に応じた skill SKILL.md 更新（`issue-design`, `issue-implement`, `issue-review-*`, `issue-fix-*`, `issue-verify-*`, `i-dev-final-check`, `i-doc-final-check`, `issue-fix-ready`, `issue-start` の `kaji issue {comment,edit}` 呼び出しに `--commit` 反映）
- [x] 問題 B: `issue-close` skill の Step 2 を「dirty file が `.kaji/issues/` 配下のみ かつ 永続化由来のみ なら救済 commit、それ以外は ABORT、救済 commit 後の git status 残差で ABORT」に書き換え
- [ ] 問題 B: dev workflow 1 サイクル実行で base worktree が close 直前まで clean を維持することを確認（実証は post-merge: マージ後の次サイクルで `--commit` 込みの skill が動作するため、本 PR では機構実装と単体検証で代替）
- [x] `make check` 通過

## 影響範囲（初期評価）

- 影響するモジュール / コマンド:
  - 主 (A): `.kaji/wf/feature-development-local.yaml`, `kaji_harness/workflow.py`, `docs/dev/workflow-authoring.md`
  - 主 (B): `kaji_harness/cli_main.py`, `.claude/skills/issue-close/SKILL.md`, 各 skill SKILL.md (`issue-design`/`issue-implement`/`issue-review-*`/`issue-fix-*`/`issue-verify-*`/`i-dev-final-check`/`i-doc-final-check`/`issue-fix-ready`/`issue-start`)
  - 副: `tests/test_workflow*.py`, `tests/test_phase4_workflow_provider_match.py`, `tests/test_cli_main.py` (新 `--commit` フラグの assertion)
- 深刻度:
  - A: medium-high（workflow が突然 ERROR 停止、再現性高い、開発体験を直接悪化）
  - B: medium（close で救済されるため最終的にはデータ損失なし、ただし skill 設計逸脱で運用が揺らぐ）
- 回避策:
  - A: 各 effort 値を都度確認して agent 仕様に合わせて書く（人手依存）
  - B: 各 skill 実行後に手動 `git add .kaji/issues/ && git commit` する（人手依存）

## 参考

- 元調査: 本セッション (post-merge log investigation for local-p1-5/14)
- レビューコメント: `.kaji/issues/local-p1-16-post-merge-cleanup-workflow-yaml-effort/comments/0001-pc5090.md` の 3 件 Findings
- 関連 commit:
  - `a709a31` chore(wf): update feature-development-local.yaml effort levels and resume settings — codex 系のみ effort 修正、claude 系 4 箇所は残存
  - `0b14612` chore(local): add issue-start metadata and review comments — Issue 14 close 救済 commit (1)
  - `3501fc2` chore(local): add design doc and review comments for local-p1-14 — Issue 14 close 救済 commit (2)
- 関連 Issue:
  - `local-p1-15` make check が main で 10 件 fail（別系統、Issue 5 fixture 漏れ + テスト config 隔離欠落）
  - `local-p1-17` issue-start skill のデフォルト prefix が type:* label 由来の branch_prefix と不整合（同調査由来）
- 1 次情報（実行ログ）:
  - `.kaji-artifacts/local-p1-5/runs/2605091602/run.log:5` (effort `High` reject の error)
  - `.kaji-artifacts/local-p1-14/runs/2605091631/close/console.log:5-12` (close 時の未コミット蓄積)
  - `kaji_harness/cli.py:240` (claude `--effort` passthrough)
  - `kaji_harness/cli.py:267` (codex `model_reasoning_effort` passthrough)
  - `kaji_harness/workflow.py:141` (effort=step_data.get('effort'))
  - `.claude/skills/issue-close/SKILL.md:266` (ABORT ガード)
- 関連ドキュメント:
  - `docs/dev/workflow-authoring.md` (effort 値の docs 更新先)
  - 設計書配置予定: `draft/design/issue-local-p1-16-post-merge-cleanup-effort-and-commit.md`

