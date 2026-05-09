---
id: local-pc5090-17
title: issue-start skill のデフォルト prefix が type:* label 由来の branch_prefix と不整合
state: open
slug: issue-start-skill-prefix-type-label-bran
labels:
- type:bug
created_at: '2026-05-09T12:05:59Z'
---
> [!NOTE]
> **Worktree**: `../kaji-fix-local-pc5090-17`
> **Branch**: `fix/local-pc5090-17`

## 概要

`/issue-start <issue_id>` を prefix 引数なしで実行すると `feat/<id>` worktree が作られるが、kaji_harness 側の context 変数解決は frontmatter `branch_prefix` → `type:*` label の優先順で prefix を導出する（`type:bug` → `fix` 等）。両者が同期していないため、type:bug 等の Issue を `/issue-start` のデフォルトで起動すると **context 変数 (`branch_name=fix/<id>`) と実態 (`feat/<id>`) が永続的に乖離** し、後続 skill 全てで fallback 解釈を強いられる。

## 目的

### Observed Behavior（OB）

local-pc5090-14（`type:bug` ラベル）で発生した実害:

`.kaji-artifacts/local-pc5090-14/runs/2605091631/close/console.log:3-4`:

```
Issue 本文では `feat/local-pc5090-14` ブランチ・worktree が記載されていますが、
コンテキスト変数では `fix/local-pc5090-14` となっています。実際の worktree 状態を確認します。
```

同様に同 run 内の verify-code, design, final-check の各 skill console.log で「コンテキスト変数とずれているので実態に合わせて進める」という fallback 判断が繰り返し記録されている。

### Expected Behavior（EB）

`/issue-start <issue_id>` 実行時:
1. kaji_harness の `provider.resolve_issue_context()` と **同一の正本** から prefix を取得する（frontmatter `branch_prefix` 優先 → 不在時は `labels_to_branch_prefix()` → 最終 fallback `DEFAULT_BRANCH_PREFIX="chore"`）
2. 結果として作られる worktree / branch 名が、後続 skill が受け取る context 変数 (`branch_name`, `worktree_dir`) と一致する
3. **明示 prefix 引数は廃止する**（label 一本化）。乖離源を skill API レベルで除去し、frontmatter / label / branch 名の三者整合を確保する

根拠（1 次情報）:
- `kaji_harness/providers/_mappings.py:16-25` LABEL_TO_PREFIX:
  ```python
  LABEL_TO_PREFIX: Final[dict[str, str]] = {
      "type:feature": "feat",
      "type:bug": "fix",
      "type:refactor": "refactor",
      "type:docs": "docs",
      "type:test": "test",
      "type:chore": "chore",
      "type:perf": "perf",
      "type:security": "security",
  }
  ```
- `kaji_harness/providers/local.py:680-689` で context 変数生成時に **frontmatter `branch_prefix` を最優先** し、不在時のみ label mapping にフォールバック:
  ```python
  prefix_value = meta.get("branch_prefix")
  if isinstance(prefix_value, str) and prefix_value:
      prefix = prefix_value
  else:
      label_names = [...]
      prefix, fallback = labels_to_branch_prefix(label_names)
      if fallback:
          prefix = DEFAULT_BRANCH_PREFIX
  ```
- `.claude/skills/issue-start/SKILL.md:27-28`:
  ```
  - prefix (任意): ブランチプレフィックス (デフォルト: feat)
    - 例: docs, fix, feat, refactor, test
  ```
  → label / frontmatter と無関係に固定で `feat`

### 再現手順

1. `type:bug` ラベル付きの新規 Issue を `kaji issue create --label "type:bug"` で作成（例: `local-pc5090-N`）
2. `/issue-start local-pc5090-N` を prefix 引数なしで実行
3. `git worktree list` で `kaji-feat-local-pc5090-N` が作成されることを確認
4. `kaji run .kaji/wf/feature-development-local.yaml local-pc5090-N` で workflow を起動
5. 各 step の skill prompt 中の `branch_name` / `worktree_dir` 変数が `fix/local-pc5090-N` / `kaji-fix-local-pc5090-N` になっているのに、実際の worktree は `feat/`/`kaji-feat-` であることを観測（実害は close skill の console.log で表面化する）

## 修正方針

レビュー指摘（`comments/0001-pc5090.md`）を全件採用した結果、以下の方針で進める。

### 主軸: helper CLI 経由で context 正本を skill から参照する

skill 側に label→prefix ロジックを **複製しない**。`kaji_harness/providers/local.py:680` の `branch_prefix` frontmatter 優先処理を再現できないため、`provider.resolve_issue_context()` を呼び出す helper CLI を追加し、skill はそれを読む。

```bash
kaji issue context <issue_id> --json branch_prefix,branch_name,worktree_dir,branch_prefix_fallback
```

- 内部実装: `LocalProvider.resolve_issue_context(issue_id)` の戻り値（`IssueContext` dataclass）を JSON シリアライズ
- 出力フィールドは `--json` で選択可能（最低限上記 4 つ + 拡張余地）
- GitHubProvider / GitLabProvider にも同シグネチャで実装（`base.IssueProvider.resolve_issue_context` Protocol が既存）

### 明示 prefix 引数は廃止（A 案）

`/issue-start <issue_id> [prefix]` の第 2 引数を削除する。理由:

- frontmatter `branch_prefix` を context 正本に永続化する API（`kaji issue edit --branch-prefix`）は存在しない（`kaji_harness/cli_main.py:1044-1064` `_local_issue_edit` argparse 確認済）。明示 prefix を skill 側だけで処理しても乖離を再生産するだけ
- `.kaji/issues/` 配下で `branch_prefix` frontmatter を明示設定している既存 Issue は **0 件**（frontmatter ブロック（先頭 `---` で囲まれた領域）のみを対象に確認: `awk '/^---$/{c++; next} c==1 && /^branch_prefix:/' .kaji/issues/*/issue.md` の出力 0 行。単純な `grep -rn "branch_prefix:"` では本文中の説明文にもヒットするため使用しない）。A 案による regression リスクは無視できる
- 将来 label と異なる prefix が必要になった場合は、別途 `kaji issue edit --branch-prefix` を追加する形で柔軟性を取り戻せる（後方互換性は保たれる）

### 後方互換性

既に `feat/<id>` で運用中の Issue（label `type:feature` で frontmatter override なし）は、新ロジックでも `feat` が解決されるため影響なし。`type:bug` 等で `feat/<id>` worktree がある状態で `/issue-start` を再実行する操作は元々 worktree 作成失敗するため、想定外オペレーションとして fail-fast でよい。

## 完了条件

### 設計段階で確認

- [ ] helper CLI `kaji issue context` の引数仕様・出力スキーマが確定している（`--json` フィールド選択方式 / エラー時の exit code 等）
- [ ] helper CLI 実装が `provider.resolve_issue_context()` をそのまま呼ぶ薄いラッパーであり、context 解決ロジックを重複実装しないことが設計書に明記されている
- [ ] 明示 prefix 引数の廃止が breaking change として後方互換セクションに明記されている（運用中 Issue への影響評価含む）
- [ ] 同根の他 skill 影響調査結果が記載されている（`.claude/skills/issue-close/SKILL.md:280` の `git merge --no-ff --no-edit [branch_name]` 等、context 変数を直接埋め込む箇所のリスト）

### 実装段階で確認

- [ ] `kaji issue context <issue_id> --json <fields>` CLI が `kaji_harness/cli_main.py` に追加されている
- [ ] helper CLI のユニットテストが追加されている（`tests/test_cli_main.py` 等）
- [ ] `.claude/skills/issue-start/SKILL.md` から `prefix` 引数が削除され、helper CLI 結果を使う実行手順に書き換えられている
- [ ] type:bug ラベル付き Issue で `/issue-start` を実行 → `fix/<id>` worktree が作成されることをテスト（手動 or e2e）
- [ ] context 変数 (`branch_name`, `worktree_dir`) と実態 worktree が一致することを workflow 1 サイクル実行で確認
- [ ] 既存 8 種類の `type:*` label すべてで prefix 導出が正しいことを確認（`type:feature/bug/refactor/docs/test/chore/perf/security`）
- [ ] type:* label 不在時の fallback 動作（`DEFAULT_BRANCH_PREFIX = "chore"`）が `issue-start` でも一致する
- [ ] **frontmatter `branch_prefix` 優先動作の確認**: `branch_prefix: docs` を frontmatter に持つテスト Issue で、label が `type:bug` でも `docs/<id>` worktree が作成されること（label と異なる allowed prefix を選び、frontmatter override が label より優先されることを示す。`hotfix` 等の non-allowed 値は `kaji_harness/providers/context.py:22, 41` の `validate_branch_prefix()` で `_ALLOWED_BRANCH_PREFIXES = frozenset(LABEL_TO_PREFIX.values())` チェックにより `resolve_issue_context()` 到達前に ValueError となるため使用不可）
- [ ] **明示 prefix 引数廃止の確認**: `/issue-start <id> fix` のように第 2 引数を渡した場合、skill が ABORT または引数を無視して label 経由で解決することを確認（仕様確定後）
- [ ] **既存 `feat/<id>` 運用中 Issue（`type:feature` ラベル）が破壊されないこと**: 該当 Issue で `/issue-start` を再実行すると worktree 既存エラーで fail-fast し、context は変わらないこと
- [ ] `make check` 通過

## 影響範囲（初期評価）

- 影響するモジュール / コマンド:
  - 主: `kaji_harness/cli_main.py` (helper CLI `kaji issue context` 追加)
  - 主: `.claude/skills/issue-start/SKILL.md` (prefix 引数廃止 + helper CLI 経由化)
  - 副: `tests/test_cli_main.py` 等（helper CLI のユニットテスト）
  - 副: 他 skill のドキュメント（`prefix` 引数言及があれば追従）
- 深刻度: **medium-high**（当初 medium から格上げ）
  - 各 skill が fallback 解釈で吸収しているため最終的な workflow 完了は阻害されないケースが多いが、`.claude/skills/issue-close/SKILL.md:280` の `git merge --no-ff --no-edit [branch_name]` のように **context 変数を bash コマンドに直接埋め込む箇所** では agent の fallback 判断が効かず、merge 失敗 → worktree 残骸の原因になる
  - agent fallback はコード保証ではなく LLM 判断依存。安定運用の前提にできない
  - 新規 skill を追加するたびに同じ fallback コードを書く必要があり、技術負債として蓄積中
- 回避策（恒久対応までの暫定）: `/issue-start <issue_id> fix`（prefix を毎回明示）。人手依存。

## 参考

- 元調査: 本セッション (post-merge log investigation for local-pc5090-5/14)
- レディネスレビュー: `.kaji/issues/local-pc5090-17-issue-start-skill-prefix-type-label-bran/comments/0001-pc5090.md` (3 件 Findings, 全件採用)
- 関連 Issue: `local-pc5090-16`（同調査由来の bundle Issue: workflow YAML effort 値型強化 + Issue ファイル commit 動線改善）
- 1 次情報:
  - `kaji_harness/providers/_mappings.py:16-28` (LABEL_TO_PREFIX 正本)
  - `kaji_harness/providers/local.py:680-699` (context 変数生成ロジック / frontmatter `branch_prefix` 最優先)
  - `kaji_harness/providers/base.py:81` (`IssueProvider.resolve_issue_context` Protocol)
  - `kaji_harness/cli_main.py:1044-1064` (`_local_issue_edit` に `--branch-prefix` が無いことの根拠)
  - `.claude/skills/issue-start/SKILL.md:27-28` (skill 側のデフォルト prefix=feat)
  - `.claude/skills/issue-close/SKILL.md:280` (`git merge --no-ff --no-edit [branch_name]` 直接埋め込みの実害根拠)
  - `.kaji-artifacts/local-pc5090-14/runs/2605091631/close/console.log:3-4` (実害ログ)
- 関連ドキュメント:
  - 設計書配置予定: `draft/design/issue-local-pc5090-17-issue-start-prefix-label-sync.md`


