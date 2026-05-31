# [設計] resumed workflow が mutable label から worktree を再合成しないようにする

Issue: #218

## 概要

`GitHubProvider.resolve_issue_context()` が Issue の現在ラベルから毎回 `branch_prefix` を再計算する結果、`issue-start` が実際に作成した worktree/branch と再実行時の `IssueContext.worktree_dir` / `branch_name` が乖離し、`exec_script` 系 step（実例: `review-poll`）が存在しない cwd で `FileNotFoundError` を起こして workflow が ERROR 終了する不具合を修正する。

`SessionState` に「初めて物理的に存在を確認した worktree/branch」を構造化保存し、以降の run/step ではそちらを `IssueContext` の正本として override することで、後段の label 変更が確定済み workflow state を上書きしない不変条件を harness 側で保証する。あわせて `review_poll_entry.py` の cwd 不存在を Python traceback ではなく ABORT verdict で診断可能にする。

## 背景・目的

### Observed Behavior (OB)

実例: `apokamo/kamo2#1084` / kaji 0.11.1 / `kaji run .kaji/wf/full-cycle.yaml 1084`。

`review-poll/stderr.log` に直接エラーが残る（Issue #218 本文 § 失敗時に注入された値）:

```text
File ".../kaji_harness/scripts/review_poll_entry.py", line 99, in main
    remote_url = subprocess.run(
...
    cwd=worktree_dir or None,
...
FileNotFoundError: [Errno 2] No such file or directory: '/home/aki/dev/kamo2-feat-1084'
```

実在 worktree は `/home/aki/dev/kamo2-chore-1084` （branch `chore/1084`）。`review-poll` が受け取った `KAJI_WORKTREE_DIR` は `/home/aki/dev/kamo2-feat-1084` で、`issue-start` が作成した物とは異なる prefix で再合成された path だった。

`runner.py` の env 注入経路（`kaji_harness/runner.py:380-385`）:

```python
"KAJI_WORKTREE_DIR": issue_context.worktree_dir,
"KAJI_BRANCH_NAME": issue_context.branch_name,
```

`issue_context` の合成経路（`kaji_harness/providers/github.py:314-338`）:

```python
issue = self.view_issue(issue_id)
label_names = [label.name for label in issue.labels]
prefix, fallback = labels_to_branch_prefix(label_names)
...
branch_name=build_branch_name(prefix, issue.id),
worktree_dir=build_worktree_dir(prefix, issue.id, self.repo_root, self.worktree_prefix),
```

→ `resolve_issue_context()` は Issue の現在ラベルだけを source of truth として扱い、`issue-start` が実際に作成した worktree/branch を参照していない。

### Expected Behavior (EB)

`issue-start` 実行後の workflow 再実行 / 再開（`--from` / `--step` を含む）では:

1. `IssueContext.worktree_dir` / `branch_name` は「実際に作成された worktree/branch」と一致する。
2. ラベル変更（`review-ready` → `fix-ready` で `type:*` 付与等）は **既存** worktree/branch の path 再計算根拠にならない。
3. `exec_script` 系 step（`review-poll` 等）が agent skill と同じ worktree を見る。
4. 万一 cwd が物理的に消えていた場合、Python traceback で死なず `ABORT` verdict（reason / evidence / suggestion 付き）として診断可能にする。

EB の根拠:

- Issue 本文 § 期待動作 — 「`issue-start` 済みの Issue では、再実行/再開時も既存 worktree/branch を優先する」。
- `docs/dev/development_workflow.md:53` — `/issue-start` は worktree 作成 + Issue 本文 NOTE ブロックに Worktree / Branch を追記する責務を持つ。これが確定すれば以後の workflow step は label ではなく確定済み state を参照すべき。
- `.claude/skills/issue-start/SKILL.md:33-34` — branch / worktree 命名規約は `<prefix>/<id>` / `<repo_parent>/kaji-<prefix>-<id>` であり、`prefix` は `kaji issue context` 由来。`issue-start` 後の path は固定資産。

## 再現手順（steps-to-reproduce）

最小再現環境:

1. `type:*` ラベルが付いていない Issue を作る（例: `local-test-1`）。
2. workflow を `issue-start` 経由で起動: `kaji run .kaji/wf/full-cycle.yaml <issue_id>` （またはこれに相当する、内部で `issue-start` → label 付与 → 再 run が起きる workflow）。
3. workflow の途中で別 step が `type:feature` を付与する（実例では `fix-ready`）。
4. workflow を中断（任意の `exec_script` step より前で SIGTERM 等）。
5. `kaji run --from <some-step> .kaji/wf/full-cycle.yaml <issue_id>` で再開する。

観測される出力（OB）:
- 再開後 `exec_script` step（`review-poll`）の `stderr.log` に `FileNotFoundError: ... '/home/aki/dev/kamo2-feat-1084'`。
- workflow 全体は `ERROR` で停止。
- 一方 `git worktree list` には `chore/1084` の worktree のみが実在。

最小回帰テスト相当の擬似シナリオは「テスト戦略」§ Medium 参照。

## 根本原因（Root Cause）

`GitHubProvider.resolve_issue_context()` が **state-less な「現在ラベル → prefix 合成」の純関数** として実装されており、「workflow がすでに確定した worktree/branch」という workflow state を入力に取らない。`runner.py` 側もこれを毎回そのまま信頼し、`SessionState` には worktree/branch を保存していないため、再実行時に label が変わると path が変わる。

なぜ間違っているか:
- Issue label は **mutable metadata** であり、`review-ready` / `fix-ready` 等の workflow step 自身が編集する（`type:*` 付与は明示的な workflow 仕様）。
- 一方 `git worktree add` の結果（branch 名 + dir path）は **immutable な workflow state** で、`issue-start` 完了時点で確定する。
- mutable input から immutable state を **毎回再導出する** 設計は、両者が乖離した瞬間に決定論を破壊する。これは harness の不変条件違反。

いつから壊れているか:
- `GitHubProvider` 導入時（Phase 3-ab）から構造的に存在。当時は同一 run 内で label が変更されない前提だった。`review-cycle.yaml` / `full-cycle.yaml` のように review-ready 経由で label が動的に付与される workflow を導入したことで露見した。

同根の他壊れ箇所の調査:
- `runner.py:330` の `_resolve_pr_context_safe(provider, issue_context.branch_name)` も label 由来 `branch_name` を使う。ただし `branch_name` を override すれば本経路も同じ修正で恩恵を受ける（追加修正不要）。
- `prompt.py` の `build_prompt` が `issue_context.worktree_dir` / `branch_name` を agent prompt に注入する。これも override で連動修正される。
- `LocalProvider.resolve_issue_context()` も類似構造だが、local provider は frontmatter `branch_prefix` を source としており、label のような workflow-mutable input を見ていない。よって同根ではない。本 Issue 修正の override は provider 非依存に効くため副作用は中立。
- 他の `exec_script` 系 entry (`kaji_harness/scripts/*_entry.py`) で `KAJI_WORKTREE_DIR` を cwd として subprocess.run に渡している箇所も同様の traceback リスクがある。今回は本 Issue で発生した `review_poll_entry.py` のみ ABORT 化する（症状緩和の局所修正）。他 entry の防御化は派生 Issue とする。

## インターフェース

公開 IF は維持。本修正は harness 内部の state 拡張と override 経路追加。

### 変更: `SessionState`（kaji_harness/state.py）

フィールド追加（既存挙動に対し additive / 後方互換）:

| 既存 / 新規 | フィールド | 型 | 既定値 |
|------------|-----------|-----|--------|
| 既存 | `issue_number` | `str` | (required) |
| 既存 | `sessions` | `dict[str, str]` | `{}` |
| 既存 | `step_history` | `list[StepRecord]` | `[]` |
| 既存 | `cycle_counts` | `dict[str, int]` | `{}` |
| 既存 | `last_completed_step` | `str \| None` | `None` |
| 既存 | `last_transition_verdict` | `Verdict \| None` | `None` |
| **新規** | `worktree_dir` | `str \| None` | `None` |
| **新規** | `branch_name` | `str \| None` | `None` |

新規メソッド:

```python
def capture_worktree(self, worktree_dir: str, branch_name: str) -> None:
    """worktree/branch を構造化保存する（冪等。既に保存済みなら no-op）。"""
```

JSON schema 互換性:
- 既存 `session-state.json` には `worktree_dir` / `branch_name` キーが無い → load 時 `None` 既定で吸収。
- 新規 run では key が追加されるが、旧 kaji 版で読んでも `dataclass(**data)` の未知 key で `TypeError` になりうる点だけ後述「制約・前提条件」で言及。

### 変更: `WorkflowRunner.run()`（kaji_harness/runner.py）

`_resolve_run_issue_context()` 完了直後・`SessionState.load_or_create()` 完了直後に、以下の **backfill → override → capture** の 3 段経路を追加:

1. **既存 state への backfill（load 直後・state.worktree_dir が None の場合のみ実行）**:

   旧 kaji 版で作られた `session-state.json` には新規 key が無いため、override の前段で physical worktree を発見して state に書き戻す。`git worktree list --porcelain` の出力を parse し、以下のいずれかにマッチする entry を候補とする:

   - `branch` が `refs/heads/<prefix>/<issue_id>` の形式（`<prefix>` は `LABEL_TO_PREFIX` の値集合 + `DEFAULT_BRANCH_PREFIX` のいずれか）
   - かつ `worktree` path がディレクトリとして実在する（`Path.is_dir()`）
   - かつ path basename が `<worktree_prefix><sanitized-prefix>-<issue_id>` の規約に一致する（`build_worktree_dir` と同じ命名 = 偶発一致除外）

   候補の扱い:

   | 候補数 | 挙動 |
   |--------|------|
   | 0 | backfill skip。後段は label 由来の `issue_context` をそのまま使用（既存挙動を維持） |
   | 1 | `state.capture_worktree(path, branch)` を呼び、override 経路に進む |
   | 2 以上 | ABORT verdict で run 全体を停止（reason: `multiple worktrees match issue <id>` / evidence: 候補 path 一覧 / suggestion: 想定外 state を解消するため `git worktree remove` 要請） |

   一次情報 parse は `git worktree list --porcelain` の機械可読出力に限定し、Issue 本文 NOTE の文字列 parse は行わない（人為改変リスク回避 + provider 責務外）。

2. **state → context override（backfill 結果も反映後）**:
   ```python
   if state.worktree_dir and state.branch_name:
       prefix = state.branch_name.split("/", 1)[0]  # "chore/218" → "chore"
       issue_context = replace(
           issue_context,
           worktree_dir=state.worktree_dir,
           branch_name=state.branch_name,
           branch_prefix=prefix,
       )
       run_ctx = replace(run_ctx, issue_context=issue_context)
   ```

   `branch_prefix` も state 由来の `branch_name` から復元することで、env (`KAJI_BRANCH_PREFIX`) / prompt template / `IssueContext.branch_prefix` 直接参照箇所が `branch_name` と整合する（混在による downstream 不整合の予防）。

3. **新規 capture（main loop 内、各 step dispatch 直前に 1 度判定）**:
   ```python
   if state.worktree_dir is None and Path(issue_context.worktree_dir).is_dir():
       state.capture_worktree(issue_context.worktree_dir, issue_context.branch_name)
   ```
   - 冪等。`is_dir()` 判定で「physical worktree が確定した瞬間」だけを捕える。
   - `issue-start` を含まない workflow（例: `feature-development.yaml`、`issue-start` は事前手動）でも、worktree が既に物理存在していれば最初の step dispatch 時に capture される。
   - `issue-start` を含む workflow（例: `full-cycle.yaml`）では `issue-start` PASS 後の次 step dispatch 時に capture される。
   - 注: 本経路は「同一 run 内で新規作成された worktree」を捕捉する。前段 (1) と相補的 — (1) は旧 state file の救済、(3) は新規 run の確定。

### 変更: `review_poll_entry.main()`（kaji_harness/scripts/review_poll_entry.py）

`KAJI_WORKTREE_DIR` 取得直後に存在検査を追加（既存 `_abort` helper を再利用）:

```python
worktree_dir = os.environ.get("KAJI_WORKTREE_DIR", "")
if worktree_dir and not Path(worktree_dir).is_dir():
    return _abort(
        "worktree directory does not exist.",
        f"KAJI_WORKTREE_DIR={worktree_dir!r}",
    )
```

`subprocess.run(..., cwd=worktree_dir or None)` への伝播前にゲートする。`subprocess.CalledProcessError` ではなく `FileNotFoundError` を ABORT verdict に変換する単独の hard gate。

## 制約・前提条件

- **public IF 不変**: `IssueContext` / `GitHubProvider.resolve_issue_context()` / `LocalProvider.resolve_issue_context()` の public signature は変更しない。`SessionState` は dataclass フィールド追加のみ（既存 caller の positional 構築は `issue_number` 1 引数前提なので壊れない）。
- **後方互換（旧 state file）**: `session-state.json` に新規 key が無い既存ファイルを load しても `None` 既定で吸収する。`load_or_create` の `cls(**data)` 経路で未知 key を弾かないため、新規 key 追加は load 互換。
- **forward compat（旧 kaji 版での読み込み）**: 新版で書いた `session-state.json` を旧版 kaji で読むと未知 key で `TypeError`。これは kaji 版の混在運用が想定外（CLAUDE.md / docs 上でも明示）のため本 Issue scope 外。
- **責務分離**: provider は state-less な「現在状態から context 合成」責務のみを持つ。「workflow state による override」は harness (runner) 側の責務とする。これにより GitHub / local 双方の provider に手を入れずに修正が効く。
- **worktree dir 削除耐性**: user が手動で worktree を消した場合、`capture_worktree` の `is_dir()` 判定が False を返すため state 更新は走らない。一方 state にすでに保存済みのケースでは override 後の `worktree_dir` 自体が存在しない可能性があり、後段の exec_script で `review_poll_entry` の hard gate（§ ABORT verdict 化）にヒットする。これは fail-loud として正しい挙動。
- **Issue 本文 NOTE は parse しない**: 本修正案では Issue 本文 NOTE（`> [!NOTE]\n> **Worktree**: ...`）を override / backfill の source として使わない。理由は (a) 人間が手で改変可能で source of truth として脆い、(b) provider に文字列 parsing 責務を持ち込む、の 2 点。代わりに `git worktree list --porcelain` の機械可読出力 + 規約準拠 path basename + physical existence の AND 条件で backfill 候補を絞る。これは `git` の世界の事実のみを参照し、Issue 本文や人間入力に依存しない。
- **backfill の信頼境界**: backfill 候補が単一の場合のみ state を書き換える。複数候補（path 命名規約に偶発一致する別 issue の worktree が混ざる極端ケース等）は fail-loud ABORT として stop し、user に手動 cleanup を要請する。これにより「誤った worktree を勝手に採用」リスクを排除。
- **backfill の探索コスト**: `git worktree list --porcelain` は worktree 数に対し線形だが、通常数件〜数十件なので O(N) で十分。実行頻度も run / step 単位ではなく run 起動時の 1 回のみ。
- **同一 host 前提**: SessionState は `artifacts_dir`（既定 `.kaji/artifacts/`）に保存され、別 host で `kaji run` を再開しても state は引き継がれない。別 host 再実行ケースは scope 外（issue-start からやり直す運用）。
- **Python**: 既存スタック。snake_case / type hints / Google docstring。

## 方針

最小侵襲修正。

1. **`SessionState` 拡張**: `worktree_dir` / `branch_name` の Optional フィールドを追加し、`capture_worktree()` の冪等保存 helper を追加。`_persist()` の data dict 構築と `load_or_create()` の dict→dataclass 変換を更新。
2. **`runner.run()` の 3 箇所修正**:
   - state load 直後の **backfill**: `state.worktree_dir is None` なら `git worktree list --porcelain` を scan し、`<known-prefix>/<issue_id>` branch + 規約準拠 path basename + physical existence の 3 条件 AND で唯一の候補を発見した場合のみ `state.capture_worktree()`。複数候補は ABORT、0 候補は skip。
   - state load 後の **override**: context の worktree/branch を state 値で置換し、`branch_prefix` も state の `branch_name` 先頭セグメントから復元（`dataclasses.replace` で immutable に再構築）。
   - main loop の step dispatch 直前に **capture** 判定（`Path(worktree_dir).is_dir()` で物理確認できたら state に保存）。
3. **`review_poll_entry.main()` 防御化**: `KAJI_WORKTREE_DIR` 取得直後に存在検査を入れ、不存在なら `_abort()` 経由で ABORT verdict を stdout に emit して return 0。
4. **新規 helper**: `kaji_harness/worktree_discovery.py`（仮）に `discover_existing_worktree(repo_root, issue_id, worktree_prefix) -> tuple[str, str] | None` を追加。`git worktree list --porcelain` を `subprocess.run` で取り、上記 3 条件で candidates を返す。複数候補は専用例外（`AmbiguousWorktreeError`）で raise し、runner 側が ABORT verdict に変換する。

リファクタリング・新規抽象化・他 exec_script entry の同種防御化は scope 外（派生 Issue）。

## テスト戦略

> **CRITICAL**: 実行時コード変更。Small / Medium 両サイズで再現テストを定義する。Large は不要（GitHub API 疎通や複数 host シナリオは本不具合の本質ではない）。

### 変更タイプ
- 実行時コード変更

### bug 固有: 再現テスト（修正前 Red / 修正後 Green）

Issue #218 § OB の `FileNotFoundError` ケースを `escape clause`（実ログ）として Issue 本文に持つ。これを実装前 Red 証跡の代替とする。一方、恒久回帰テストは以下を repo に追加する。

#### Small テスト

`tests/unit/test_state.py`（既存があれば追加、無ければ新規）:
- `SessionState.capture_worktree()` を呼ぶと `worktree_dir` / `branch_name` がセットされ、`_persist()` 経由で JSON に書き込まれる。
- 既に値がセットされた `SessionState` で `capture_worktree()` を再度呼んでも値が上書きされない（冪等）。
- 既存 JSON（`worktree_dir` / `branch_name` キー無し）を `load_or_create()` で読み込むと、両フィールドが `None` で復元される（後方互換）。

`tests/unit/test_runner_issue_context_override.py`（新規）:
- 偽 provider が `IssueContext(worktree_dir="/tmp/kaji-feat-218", branch_name="feat/218", branch_prefix="feat", ...)` を返す状態で、`SessionState` に `worktree_dir="/tmp/kaji-fix-218"` / `branch_name="fix/218"` が事前保存されていれば、`WorkflowRunner.run()` 起動後に `issue_context` の 3 フィールド（`worktree_dir`, `branch_name`, `branch_prefix`）が一括 override されることを検証（`branch_prefix == "fix"` の assert を含む）。

`tests/unit/test_worktree_discovery.py`（新規）:
- `discover_existing_worktree(repo_root, issue_id, worktree_prefix)` の単体テスト。tmp git repo + `git worktree add` で fixture を作り、以下を検証:
  - 該当 issue_id の worktree が 1 件存在 → `(path, branch)` を返す。
  - 該当 worktree が 0 件 → `None` を返す。
  - 該当 issue_id 末尾だが path basename が規約違反（例 `random-218`）→ 候補から除外され `None`。
  - 複数候補（`chore/218` と `feat/218` が両方実在）→ `AmbiguousWorktreeError` raise。
  - branch が `refs/heads/<known-prefix>/<issue_id>` 形式以外（例 `main`、`feature/foo`）→ 候補から除外。

#### Medium テスト

`tests/integration/test_resumed_workflow_worktree_persistence.py`（新規。Medium、tmp_path + fake provider）:

OB に対応するシナリオを再現:
1. fake `IssueProvider` を 2 通り用意:
   - V1: `branch_prefix="chore"` を返す（label 未付与の状態を模擬）。
   - V2: `branch_prefix="feat"` を返す（後続で label 付与された状態を模擬）。
2. `tmp_path` に `<repo_parent>/kaji-chore-218` を `mkdir` して physical worktree を模擬。
3. **初回 run**: provider V1 + 1-step ダミー workflow (skill = exec_script no-op) → 起動時 `IssueContext.worktree_dir=".../kaji-chore-218"` → step dispatch 前に capture → `SessionState.worktree_dir == ".../kaji-chore-218"`。
4. **再 run**: provider V2 に差し替え + 同じ artifacts_dir で `WorkflowRunner.run()` → 起動時 `IssueContext.worktree_dir=".../kaji-feat-218"` だが、state load 後の override により実 dispatch 時の `issue_context.worktree_dir == ".../kaji-chore-218"`（V2 の `feat` ではなく state 由来の `chore` を維持）。
5. exec_script step に注入される `KAJI_WORKTREE_DIR` も `.../kaji-chore-218` であることを検証。

assert 観点:
- 修正前: assert は再 run 時に `feat/218` が注入されるため FAIL（Red）。
- 修正後: assert は `chore/218` が維持されるため PASS（Green）。
- 修正範囲が runner + state + worktree_discovery の 3 module に閉じることを示す。

**追加シナリオ（旧 state file backfill ケース。Issue #218 の実例に最も近い Red→Green）**:

1. tmp `repo_root` 配下に `git init --bare` + worktree 用 dir 構造を作り、`git worktree add <repo_parent>/kaji-chore-218 -b chore/218 HEAD` を実行して既存 worktree を模擬。
2. 旧版互換 `session-state.json`（`worktree_dir` / `branch_name` キー無し、`issue_number="218"` のみ）を artifacts_dir に配置。
3. provider V2（`branch_prefix="feat"` を返す）で `WorkflowRunner.run()` を起動。
4. backfill 経路で `discover_existing_worktree` が `chore/218` を発見 → `state.capture_worktree` → override が走る。
5. exec_script step に注入される `KAJI_WORKTREE_DIR` が `<repo_parent>/kaji-chore-218`、`KAJI_BRANCH_NAME` が `chore/218`、`KAJI_BRANCH_PREFIX` が `chore` であることを assert。
6. 修正前は backfill 経路が無いため `kaji-feat-218` / `feat/218` / `feat` が注入され FAIL（Red）。修正後は PASS（Green）。

**追加シナリオ（多重候補 ABORT）**:

- 同 issue_id に対し `chore/218` worktree と `feat/218` worktree を両方作り、旧 state file で起動 → runner が ABORT verdict を emit して run 全体が ERROR ではなく ABORT として停止すること、stderr/log に候補 path 一覧と `git worktree remove` 案内が含まれることを assert。

`tests/scripts/test_review_poll_entry.py`（既存があれば追加、無ければ新規。Medium、subprocess + tmp_path）:
- `KAJI_WORKTREE_DIR` に存在しない path を渡して `review_poll_entry.main()` を呼ぶと、stdout に `---VERDICT---\nstatus: ABORT\n...---END_VERDICT---` が出て return 0 になる（FileNotFoundError traceback で死なない）。
- evidence 行に `KAJI_WORKTREE_DIR=` と渡した path 値が含まれる。
- 既存 path（`tmp_path` の dir）を渡したケースでは ABORT 経路を踏まずに既存の git remote 解決経路に進む（fixture で `git init` した tmp_path で subprocess を mock せず assert する形）。

### Large テスト
- 不要。実 GitHub API 疎通や複数 host での state 復元は本 bug の必要十分条件に含まれない。`docs/dev/testing-convention.md` の 4 条件を引用すれば: (1) GitHub API 側の挙動変更ではなく harness 内部 state の整合性問題、(2) 既存 Large gate は本回帰を捕捉できない、(3) 新規 Large を追加しても本回帰の検出情報が増えない、(4) `make verify-packaging` 等の固有検証は本変更には無関係 — 以上を満たすため Large 追加は不要。

## 影響ドキュメント

| ドキュメント | 影響 | 理由 |
|-------------|------|------|
| docs/adr/ | なし | 既存設計の bug fix。新規技術選定なし |
| docs/ARCHITECTURE.md | なし | provider 責務境界は不変（state 拡張は runner 側責務として吸収） |
| docs/dev/development_workflow.md | なし | フロー / フェーズ責務不変 |
| docs/dev/workflow_overview.md | なし | 同上 |
| docs/dev/workflow_guide.md | なし | 同上 |
| docs/dev/shared_skill_rules.md | なし | skill 側に変更なし |
| docs/reference/python/error-handling.md | なし | 既存原則（握り潰し禁止 / fail-loud）に沿う |
| docs/cli-guides/ | なし | CLI 公開 IF 不変 |
| CLAUDE.md | なし | 規約不変 |
| `CHANGELOG.md` | あり | `fix:` 行を追加（v0.11.2 想定）。「resumed workflow recomputed worktree from mutable labels; SessionState now persists worktree/branch on first physical confirmation」程度 |

設計書（本ファイル）は `i-dev-final-check` PASS 時に Issue 本文 NOTE 直下に添付する（development_workflow.md 規約）。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #218 本文 | https://github.com/apokamo/kaji/issues/218 | OB（`FileNotFoundError` の stderr 全文）、期待動作、修正案 A/B/C の 3 択提示、影響範囲列挙の一次情報 |
| `kaji_harness/providers/github.py` (L314-338) | `kaji_harness/providers/github.py:314` | `resolve_issue_context()` が `label_names → labels_to_branch_prefix → build_branch_name / build_worktree_dir` の経路で label から毎回再計算している実装根拠 |
| `kaji_harness/runner.py` (L376-388) | `kaji_harness/runner.py:376` | `exec_script` step への env 注入が `issue_context.worktree_dir` / `branch_name` をそのまま使っている根拠。override 修正の挿入点 |
| `kaji_harness/runner.py` (L211-269) | `kaji_harness/runner.py:211` | `_resolve_run_issue_context()` → `SessionState.load_or_create()` の順序。override / capture の挿入点を決める根拠 |
| `kaji_harness/scripts/review_poll_entry.py` (L94-111) | `kaji_harness/scripts/review_poll_entry.py:94` | `KAJI_WORKTREE_DIR` を取得後そのまま `subprocess.run(cwd=...)` に渡し、`FileNotFoundError` をハンドルしていない実装根拠。ABORT 化の挿入点 |
| `kaji_harness/state.py` (L31-73) | `kaji_harness/state.py:31` | `SessionState` dataclass / `load_or_create` / `_persist` の現行構造。新規フィールド追加と JSON 互換戦略の根拠 |
| `kaji_harness/providers/_mappings.py` (L31-42) | `kaji_harness/providers/_mappings.py:31` | `labels_to_branch_prefix` の挿入順 = 優先順位、`type:*` 無の場合は `chore` fallback で `fallback=True` を返すという mapping ルール |
| `.claude/skills/issue-start/SKILL.md` (Step 1-4) | `.claude/skills/issue-start/SKILL.md` | issue-start の確定責務（`kaji issue context` から prefix / branch / worktree を取得 → `git worktree add` → Issue 本文 NOTE 追記）。「issue-start 完了時点で worktree/branch は immutable」という前提の根拠 |
| `docs/dev/testing-convention.md` § bug 固有ルール | `docs/dev/testing-convention.md` | bug 設計には Red→Green 回帰テスト必須、escape clause として実ログを実装前 Red 証跡に充てられる規約 |
| `_shared/design-by-type/bug.md` § 4 根本原因 | `.claude/skills/_shared/design-by-type/bug.md:38` | bug 設計書は「なぜ」「いつから」「他にも壊れている箇所がないか」を必須項目として要求する根拠 |
| `git worktree list --porcelain` 仕様 | https://git-scm.com/docs/git-worktree#_porcelain_format | porcelain format は機械可読・stable な `worktree <path>` / `branch <ref>` 行で構成され、parse 用の信頼境界として使える根拠 |
| Issue #218 設計レビュー（2026-05-31） | `gh api repos/:owner/:repo/issues/218/comments` | レビュアー指摘「既存 session-state に canonical worktree が未保存の再開ケース」「branch_prefix 整合」の 2 点を Must Fix / Should Fix として受け入れ、backfill 経路 + prefix 復元方針に反映した根拠 |
