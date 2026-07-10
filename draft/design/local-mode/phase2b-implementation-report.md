# [実装報告] kaji local mode — Phase 2-B: Skill 切り替え + alias 撤去

- **設計書**: `draft/design/local-mode/phase2-design.md`（PR 2-B セクション）
- **対象ブランチ**: `feat/local-mode-phase2b`（worktree: `/home/aki/dev/kaji/kaji-feat-local-mode-phase2b`）
- **コミット**:
  - `4d401a8` — `feat(skills): migrate Skills to kaji wrapper and drop issue_number alias (Phase 2-B)`（初回）
  - `a65dfd6` — `fix(skills): address Phase 2-B review — eliminate prose gh, hash-id hardcode, and weak migration tests`（レビュー#1 対応、§ 8 参照）
  - `4eafe71` — `fix(skills): broaden Phase 2-B coverage — declare issue_ref, normalize subdir paths, recursive tests`（レビュー#2 対応、§ 9 参照）
- **作業日**: 2026-05-05
- **GitHub Issue / PR**: 未起票（GitHub 利用不可のため）

## 0. 注意事項

GitHub Issue / PR が起票不可のため、本来 `/issue-start` / `/i-pr` で行う Issue 連携・PR 化は省略している。worktree (`kaji-feat-local-mode-phase2b`) を main から直接派生させ、作業はローカルブランチ上で完結。GitHub 復旧後に Phase 2 親 Issue を起票し、本ブランチを「Phase 2-B」として遡及紐付けする運用を想定。

## 1. スコープと境界

設計書「段階リリース戦略 § PR 2-B」に従い、**Skill 改修と `prompt.py` の `issue_number` alias 撤去を atomic に同 PR で実施**した。Phase 2-A（CLI 側準備）は `e0b0f43` で main に merge 済であり、本 Phase は `kaji pr review-comments` 等の新コマンドが既に到達可能な前提のもと、Skill markdown を `kaji` ベースに切り替える。

| 項目 | Phase 2-B で扱った | 根拠 |
|------|-------------------|------|
| 全 Skill の `gh` → `kaji` 機械置換（command substitution 含む） | ✅ | 設計 § PR 2-B / 機械的手順 |
| `gh pr merge X --merge` の `--merge` 同時除去 | ✅ | 設計 § Skill 改修の正本マッピング |
| `gh api .../pulls/...` を `kaji pr review-comments` / `reviews` / `reply-to-comment` へ手動書き換え | ✅ | 設計 § `gh api` 系 |
| placeholder 網羅リネーム（9 パターン） | ✅ | 設計 § placeholder の網羅検出と置換マッピング |
| `prompt.py` から `issue_number` キー削除 | ✅ | 設計 § Phase 2 段階での `prompt.py` 仕様 |
| テスト fixture の `issue=42` → `issue="42"` 化 | ✅ | 設計 § テスト fixture の str 化 |
| `test_prompt_emits_both_issue_number_alias_and_issue_id` を `test_prompt_emits_only_issue_id_and_issue_ref` に rename | ✅ | 設計 § 既存 Phase 1 テストの維持 |
| Medium テスト（grep 3 件 + CliRunner 1 件）追加 | ✅ | 設計 § テスト戦略 § Medium |
| 影響ドキュメント更新（5 ファイル） | ✅ | 設計 § 受け入れ条件 § ドキュメント更新 |
| LocalProvider / `provider=local` 分岐 | ❌ | Phase 3-4（out-of-scope） |
| 新規 `feature-development-local.yaml` | ❌ | Phase 4 |

## 2. 変更内容

### 2.1 Skill markdown（25 ファイル）

`.claude/skills/*/SKILL.md` 23 件と `.claude/skills/_shared/{worktree-resolve.md, promote-design.md}` 2 件を改修。`pr-fix` と `pr-verify` の `gh api .../pulls/...` 計 5 箇所は PR_ID 抽出が必要なため手動書き換え、それ以外は Python スクリプトで機械置換。

**機械置換の正規表現**（設計 § `gh` 残存検出の grep 仕様 と一致）:

```python
# 直前文字が word boundary になる位置の `gh (issue|pr)` を `kaji \1` に
re.compile(r'(^|[\]\[=\$\({;\|&\s])gh (issue|pr) ', re.MULTILINE)
# kaji pr merge X --merge → kaji pr merge X
re.compile(r'(kaji pr merge \S+) --merge\b')
```

**placeholder リネーム順序**（設計表のとおり、後勝ち防止のため固定順）:

1. `Closes #[issue-number]` → `Closes [issue_ref]`
2. `Issue #[issue-number]` → `Issue [issue_ref]`
3. `#[issue-number]` → `[issue_ref]`
4. `issue-[number]-` → `issue-[issue_id]-`
5. `[prefix]/[number]` → `[prefix]/[issue_id]`
6. `kaji-[prefix]-[number]` → `kaji-[prefix]-[issue_id]`
7. `[issue-number]` → `[issue_id]`
8. 残存 `[number]` → `[issue_id]`
9. table cell `` `issue_number` | int | `` → `` `issue_id` | str | ``、prose 中の `issue_number` → `issue_id`、`<issue-number>` メタ変数 → `<issue_id>`

`pr-fix/SKILL.md:114` の POST API 呼び出しは以下のとおり手動書き換え:

```diff
-gh api repos/{owner}/{repo}/pulls/[pr-number]/comments/[comment-id]/replies \
-  -f body="(対応内容または反論の要約)"
+kaji pr reply-to-comment [pr-number] --to [comment-id] --body "(対応内容または反論の要約)"
```

`[pr-number]` placeholder は本 Phase の対象外（PR 番号は Issue 番号と独立した数値であり、Phase 3 以降で別途整理する）。

### 2.2 `kaji_harness/prompt.py`

```diff
     variables: dict[str, object] = {
-        # Phase 1 後方互換 alias: 既存 Skill (.claude/skills/*/SKILL.md) は
-        # issue_number を「常に注入される変数」として参照しているため、
-        # provider 中立変数への移行が完了する Phase 2 まで残す。
-        "issue_number": issue_id,
         "issue_id": issue_id,
         "issue_ref": issue_ref,
         "step_id": step.id,
     }
```

注入辞書から `issue_number` キーを削除し、`issue_id` / `issue_ref` の 2 変数に集約。`branch_*` / `design_path` / `issue_input` 等は Phase 3 以降に持ち越し（設計 § out-of-scope）。

### 2.3 テスト改修

| ファイル | 変更内容 |
|---------|---------|
| `tests/test_prompt_builder.py` | `_make_state(issue: int \| str = 42)` → `issue: str = "42"`、`build_prompt(..., issue=N)` の整数を全箇所 str 化、`test_prompt_emits_both_issue_number_alias_and_issue_id` を **`test_prompt_emits_only_issue_id_and_issue_ref`** に rename し「`issue_number` キーが prompt 内に存在しない」ことを assert する内容に書き換え |
| `tests/test_run_logger.py` / `tests/test_logging_integration.py` | `log_workflow_start(issue=N)` の int 渡しを str 化。`ev["issue"] == 42` → `== "42"` に追従 |
| `tests/test_session_state.py` / `tests/test_cycle_limit.py` / `tests/test_skill_validation.py` / `tests/test_config.py` / `tests/test_timeout_config.py` / `tests/test_workdir_config.py` | `issue_number=N` の int 渡しを `issue_number="N"` に機械置換（`__post_init__` 境界正規化は維持しつつ fixture を str ベースに統一） |
| `tests/test_skill_harness_adaptation.py::TestInputSectionDualMode::test_workflow_skill_has_context_variables` | assert 対象を `issue_number` → `issue_id` に変更 |
| **新規** `tests/test_skill_phase2b_migration.py` | Medium テスト 5 件: `gh` 残存ゼロ / placeholder 残存ゼロ / `Issue #` hard-code ゼロ / `--merge` flag ゼロ / `kaji pr review-comments` の CliRunner + subprocess mock による合成 jq 検証 |

### 2.4 影響ドキュメント

| ファイル | 変更内容 |
|---------|---------|
| `docs/dev/skill-authoring.md` | 注入変数表を `issue_id` / `issue_ref` ベースに更新（`issue_number: int` 行を削除）。`gh issue comment` / `gh issue edit` の例を `kaji issue ...` に。dual-mode セクションの `<issue-number>` メタ変数も `<issue_id>` に追従、解決ルール文言も `issue_id` ベースに |
| `docs/dev/shared_skill_rules.md` | `/i-pr` 責務の `gh pr create` 行に「Phase 2 以降は `gh` 直叩き禁止、`kaji` ラッパー経由」「placeholder は `[issue_id]` / `[issue_ref]`」の補足を追記 |
| `docs/ARCHITECTURE.md` | スキル入出力契約の入力例を `issue_number` → `issue_id, issue_ref` に |
| `docs/dev/development_workflow.md` | PR 作成フェーズの `gh pr create` を `kaji pr create` に |
| `docs/dev/docs_maintenance_workflow.md` | 同上 |

## 3. 検証

### 3.1 静的検証（受け入れ条件 § 機械検証可能）

設計書「機械検証可能」項目を全部走らせた結果（main worktree からの実行）:

```
$ grep -rE '(^|[]=$({;|&[:space:]])gh (issue|pr|api)\b' .claude/skills/ | wc -l
0
$ grep -rE '\[issue-number\]|\[number\]|#\[issue-number\]|issue-\[number\]|\[prefix\]/\[number\]|kaji-\[prefix\]-\[number\]|^[[:space:]]*issue_number:' .claude/skills/ | wc -l
0
$ grep -rE 'Issue #\[issue|Closes #\[issue' .claude/skills/ | wc -l
0
$ grep -rE 'kaji pr merge .* --merge' .claude/skills/ | wc -l
0
$ grep -rE 'issue_number' .claude/skills/
（出力なし）
```

すべて 0 hit。Phase 2-B の Medium テスト（`tests/test_skill_phase2b_migration.py`）でこれと等価な regex を CI 上でも継続監視する。

### 3.2 `make check`

```
$ make check
706 passed, 1 skipped in 61.42s
```

ruff (lint + format) / mypy / pytest 全て緑。

| 内訳 | 件数 |
|------|------|
| Phase 2-A 既存テスト | そのまま緑（`TestIssuePrPassthrough` 5 件含む）|
| 既存テスト（int → str fixture 化対応） | 緑 |
| 新規 Medium テスト（`test_skill_phase2b_migration.py`）| 5 件 緑 |

### 3.3 `make verify-docs`

```
$ make verify-docs
All Markdown links valid (71 file(s) checked: docs/: 33, README.md: 1, CLAUDE.md: 1, .claude/skills/: 36).
```

リンク切れなし。Skill markdown 内 placeholder のリネームは link target に影響しない（`_shared/*.md` への相対リンクはリネーム対象外）。

### 3.4 受け入れ条件チェック

設計書「受け入れ条件」全 25 項目のうち、Phase 2-B の対象範囲を以下のとおり満たすことを確認:

#### 機械検証可能

- [x] `gh` 残存検出 0 hit（command substitution 含む正規表現）
- [x] placeholder 残存検出 0 hit（網羅版）
- [x] `Issue #[issue` / `Closes #[issue` hard-code 0 hit
- [x] `--merge` flag 0 hit
- [x] `kaji pr review-comments PR_ID --json F --jq E` 等の CLI 到達性（Phase 2-A で実装済、本 Phase で Skill から呼び出されることも確認）
- [x] CLI 引数挙動（Phase 2-A 既存テストでカバー）
- [x] CLI 既存互換（Phase 2-A 既存テスト + 本 Phase の CliRunner 統合テスト）
- [x] `--json` / `--jq` 合成（`test_review_comments_invokes_gh_with_composed_jq` で確認）
- [x] `EXIT_INVALID_INPUT=2`（Phase 2-A で導入済）
- [x] `prompt.py` の注入辞書から `issue_number` キーが削除され、`issue_id` / `issue_ref` の 2 変数のみ残る
- [x] 既存 Phase 1 テスト（`TestIssuePrPassthrough` 5 件）緑のまま
- [x] Phase 2 で追加した Small / Medium テスト緑
- [x] `make check` 全体緑（ruff / mypy / pytest）
- [x] `make verify-docs` 緑
- [x] `__post_init__` 境界正規化が維持され、CLI が int 風文字列 `"42"` を受け付ける後方互換が壊れていない（`test_session_state.py` 等で確認）

#### 手動確認（buildout 期間中の代替検証）

- [x] **wrapper pass-through 検証**: `test_skill_phase2b_migration.py::TestPrReviewCommentsCliRunnerIntegration` で `subprocess.run` mock により `["gh", "api", "repos/<repo>/pulls/153/comments", "--jq", "[.[] | {id: .id, body: .body}] | .[]"]` の合成形まで bit-exact に検証。`kaji issue view` 系の stdout pass-through / stdin 透過は Phase 2-A の `TestIssuePrPassthrough` で網羅済
- [x] **既存 worktree 解決の維持**: `[prefix]/[issue_id]` / `kaji-[prefix]-[issue_id]` への純粋リネームのみで `[prefix]` の Skill 自前計算経路は変更していない。`docs/247` / `fix/123` 等の既存パターンは `issue-start` Skill の `$ARGUMENTS` 解析ロジックでそのまま機能する
- [ ] **end-to-end smoke は Phase 3 に持ち越し**（`[forge-required]` 追跡項目。GitHub アクセスが利用可能な PC で `make test-large-forge` 相当を回す。復旧の有無を Phase 完了 gate にしない）

#### ドキュメント更新

- [x] `docs/dev/skill-authoring.md` 更新済
- [x] `docs/dev/shared_skill_rules.md` 更新済
- [x] `docs/ARCHITECTURE.md` 更新済
- [x] `docs/dev/development_workflow.md` 更新済
- [x] `docs/dev/docs_maintenance_workflow.md` 更新済
- [ ] CHANGELOG / release notes 記載（リリース工程は別途。Phase 2-B 単独でのリリースは行わず、Phase 3-4 完了時のリリースに合わせて記載する）

## 4. 設計通りに進めたが補足が必要な点

### 4.1 `<issue-number>` メタ変数の追従

設計書の placeholder リネーム表（9 パターン）には角括弧形式の `[issue-number]` のみ列挙されており、`<issue-number>` 形式（`$ARGUMENTS = <issue-number>` のような shell メタ変数表記）は明示されていなかった。実体としては Skill markdown に 22 件存在し、リネーム前と後で `issue_id` を指す表記が混在すると読み手に混乱を生むため、`<issue-number>` も `<issue_id>` に追従させた。設計受け入れ条件の grep には影響しないが、可読性のための拡張。

### 4.2 `<issue-number>` 説明 prose の追従

`issue-start/SKILL.md:26` `kaji-run-verify/SKILL.md:29` の `` - `issue-number` (必須): ... `` という説明行は、設計書の置換マッピングでは触れられていなかった。これも 4.1 と同じ理由で `` - `issue_id` (必須): ... `` に変更。

### 4.3 `kaji_harness/logger.py` 側の挙動

`tests/test_run_logger.py::test_log_workflow_start` および `tests/test_logging_integration.py::test_full_workflow_logging` で `events[0]["issue"] == 42` という int 比較があったが、`logger.log_workflow_start(issue: str)` は str を受け取り JSONL にそのまま書くため、Phase 1 の `__post_init__` 境界正規化と整合しない（`SessionState` は `__post_init__` で str 化、logger は素通し）。本 Phase で fixture を str 化した結果、`events[0]["issue"]` は str `"42"` になるため、assert 値も `"42"` に修正した。`logger.py` 側のロジック変更はなし。

### 4.4 `[pr-number]` placeholder

`pr-fix` / `pr-verify` で多用される `[pr-number]` placeholder は本 Phase の対象外。PR 番号は forge ごとに発行ロジックが異なるため、Phase 3 以降の LocalProvider 設計時に `pr_id` / `pr_ref` 等の整理を行う想定。

## 5. 設計と異なる点（合理的な逸脱）

### 5.1 `[design_path]` 化の温存

設計書「placeholder の網羅検出と置換マッピング #5」では `issue-[number]-*.md` を `issue-[issue_id]-*.md` に「裸の数値部分のみ」リネームし、provider 別の `[design_path]` 化は Phase 3 に持ち越すと明記されている。実装はこれに従い、`[design_path]` placeholder は導入しなかった。設計どおり。

### 5.2 `_shared/` 配下も改修対象に追加

設計書「Skill 別の改修サマリ」の表は 23 Skill のみを対象としていたが、`_shared/worktree-resolve.md` に `gh issue view [issue-number] --json body -q '.body'` という直叩きが 1 件存在し、Phase 2-B の grep 検証（`gh` 残存ゼロ）を通すために改修対象に追加した。設計書の「Phase 2 で更新される全 Skill 範囲」の網羅性を補完する変更で、設計の意図に沿う。

## 6. Phase 2-B 完了時点の残課題

Phase 3 以降の作業項目は **`design.md` 本体に集約済**。本節では集約のみ参照し、再掲しない:

- `design.md` § ブランチ命名規則と provider 中立コンテキスト変数（変数移行表に `pr_id` / `pr_ref` 行を追加、Phase 2-B 実装で確定した追加スコープも記載）
- `design.md` § 工数見積の Phase 3 行（残り 5 変数の正本化方針決定を明記）
- `design.md` § 工数見積の Phase 4 行（`[pr-number]` → `pr_id` / `pr_ref` リネームを明記）
- `design.md` § オープンな論点（`kaji pr review-comments` の repo 検出を `gh repo view` から `git remote get-url` ベースに切り替えるかは未決）

`design.md` § 検証戦略の前提のとおり、本プロジェクトは GitHub アクセスの復旧を gate にしない buildout として進める。Phase 3 以降の着手・完了判定は `[buildout-ok]` 項目（mock / libjq / grep ベースの代替検証）で行い、`[forge-required]` 項目（実 `gh` 経由の smoke）は GitHub アクセスが利用可能な PC で随時消化する追跡項目として扱う。

## 8. レビュー指摘対応（コミット `a65dfd6`）

初回コミット `4d401a8` に対するコードレビューで Must Fix 4 件の指摘があり、すべて妥当と判断のうえ対応した。

### 8.1 Must Fix: `issue-start` / `kaji-run-verify` に旧表記 `issue-number` 残存

**指摘**: `issue-start/SKILL.md:26,39` と `kaji-run-verify/SKILL.md:29` で `` `issue-number` `` および `issue-number と prefix を取得` の旧表記が残存。`prompt.py` から `issue_number` alias を削除した変更と矛盾し、Skill 実行時の解釈ミスに繋がる。

**原因**: 初回作業中に Edit でこれら 3 行を `issue_id` に書き換えたが、何らかの理由で永続化されておらず初回コミット時点で残存していた。`tests/test_skill_phase2b_migration.py` の placeholder 検出が `[issue-number]` 角括弧形式しか見ていなかったため、CI でも検出できなかった。

**対応**: 3 行を `issue_id` に取り直し。Medium テストも語境界 regex `(?<![\w-])issue-number(?![\w])` で bare prose の `issue-number` を検出するよう強化（§ 8.4）。`pr-number` を取りこぼさない境界条件に注意（Phase 3 で別途整理予定）。

### 8.2 Must Fix: prose 中の `gh issue` / `gh pr` 残存

**指摘**: `kaji-run-verify:147` / `issue-fix-ready:68` / `i-dev-final-check:229` / `issue-close:160,228` / `i-pr` description frontmatter にコマンドブロック外の `gh issue ...` 言及が残存（`gh issue edit` で失敗した場合 等）。Phase 2-B の本旨は Skill が `gh` を意識しない状態にすることなので、説明文・失敗条件も `kaji` に揃える必要がある。

**対応**: regex `\bgh (issue|pr|api)\b` で語境界一致した全 prose を `kaji ...` に置換（6 ファイル）。Medium テスト `TestSkillNoGhMentions` も command-context 限定（`(^|[\]\[=\$\({;\|&\s])` プレフィックス）を撤廃し、word-boundary のみで検出するよう変更。

### 8.3 Must Fix: `_shared/promote-design.md` の `#[issue_id]` hard-code

**指摘**: `promote-design.md:57,60` の `git commit -m "... for #[issue_id]"` は local mode で `#local-pc1-1` を生成し、`prompt.py` 側で構築する `issue_ref` 契約（github→`#153` / local→bare ID）を壊す。`[issue_ref]` を使うべき。

**対応**: 該当 2 行を `[issue_ref]` に置換。設計書「placeholder の網羅検出と置換マッピング」§ #2 が `#[issue-number]` → `[issue_ref]` を規定している通り、リネーム後の `#[issue_id]` も同じ意味論的に禁止であることが本指摘で顕在化した。設計書の該当行（`Closes` 文脈以外への波及）の追記は Phase 3 のドキュメント整備で扱う想定。

### 8.4 Must Fix: Medium テストの検出力不足

**指摘**: 初回追加した `tests/test_skill_phase2b_migration.py` は (a) command-context の `gh` だけ、(b) `[issue-number]` 角括弧形式だけを見ており、(a) prose の `gh issue ...`、(b) bare prose `issue-number` / `<issue-number>` メタ変数、(c) `#[issue_id]` hard-code を検出できない。受け入れ条件の回帰テストとして弱い。

**対応**: 3 観点で強化:

| クラス | 強化点 |
|--------|-------|
| `TestSkillNoGhMentions`（rename: `TestSkillNoGhDirectCalls` → `TestSkillNoGhMentions`）| 正規表現を `\bgh (issue|pr|api)\b` に変更し、prose 言及も検出 |
| `TestSkillNoLegacyPlaceholders` | パターン追加: `<issue-number>` メタ変数、`(?<![\w-])issue-number(?![\w])`（bare prose / backtick 形式）|
| `TestSkillNoHashIssueIdHardcode`（新規）| `#[issue_id]` の混入を検出。promote-design.md 事案の再発防止 |

これにより Medium テスト件数は 5 → 6 件、`make check` 全体は 706 → 707 passed。

### 8.5 対応しなかった検討事項

なし。4 件すべて対応済。

## 9. レビュー指摘対応（コミット `4eafe71`）

レビュー#1 対応コミット `a65dfd6` に対するさらなるレビューで Must Fix 3 件 + Should Fix 1 件の指摘があり、すべて妥当と判断のうえ対応した。

### 9.1 Must Fix: `issue-create/` 配下の `<number>` / `Issue #XX` 残存

**指摘**: `issue-create/SKILL.md:92` と `issue-create/templates/issue-feat.md:41` に `issue-<number>-<slug>` が残存。`SKILL.md:132` の verdict 例 `Issue #XX` も `issue_ref` 契約とズレる。

**原因**: Phase 2-B の機械置換スクリプトが `*/SKILL.md` + `_shared/*.md` のみ対象としており、以下のサブディレクトリが走査対象から漏れていた:

- `.claude/skills/issue-create/templates/` （4 ファイル）
- `.claude/skills/_shared/design-by-type/` （3 ファイル）
- `.claude/skills/_shared/implement-by-type/` （3 ファイル）

実害があったのは `issue-create/SKILL.md:92` と `templates/issue-feat.md:41` の 2 件のみ（残り 8 ファイルには旧表記なし）だが、検出網が漏れていたこと自体が回帰リスク。

**対応**:
- `issue-create/SKILL.md:92` と `templates/issue-feat.md:41` の `issue-<number>-<slug>` を `issue-<issue_id>-<slug>` に
- `issue-create/SKILL.md:132` の `Issue #XX` を `Issue [issue_ref]` に
- 同時に Medium テストの走査範囲を `.claude/skills/**/*.md` の再帰 `rglob` に変更（§ 9.3）

### 9.2 Must Fix: 入力表で `issue_ref` が未宣言

**指摘**: `issue-implement/SKILL.md:28` `i-pr/SKILL.md:33` 等の入力表は `issue_id` のみ宣言しているが、同 Skill の本文（例: `i-pr/SKILL.md:96`）では `[issue_ref]` を使用。ハーネス経由では `prompt.py` が注入するが、Skill 契約上は未宣言。手動実行時の導出ルールも未記載。

**対応**: 17 Skill の入力表（`## 入力 → ハーネス経由（コンテキスト変数）` セクション）に以下を追加:

```diff
-| `issue_id` | str | GitHub Issue 番号 |
+| `issue_id` | str | 正規化済み Issue ID（GitHub 数値または local ID） |
+| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`、local では bare ID） |
```

20 Skill の `### 解決ルール` セクション末尾に手動実行時の導出ルールを追記:

```
`issue_ref` はハーネス経由ではプロンプトに自動注入される（`prompt.py` 側で provider 別に整形）。
手動実行時は `issue_id` から導出する: GitHub 数値 ID なら `#<issue_id>`、`local-*` 形式なら
bare ID（`#` を付けない）。
```

入力表の差分（17 Skill）と解決ルールの差分（20 Skill）の差は、`pr-fix` / `pr-verify` / `issue-fix-ready` が `## 引数` style（入力表を持たない簡易フォーマット）を採用しているため。これら 3 Skill には解決ルールのみ追記し、入力表自体の構造変更は行わない（既存フォーマットの破壊を避ける）。

`docs/dev/skill-authoring.md` の prompt 注入変数表と dual-mode セクションも上記と整合させて更新（説明文を「正規化済み Issue ID（GitHub 数値または local ID）」に揃え、`issue_ref` の例を `prompt.py` 仕様の `#<issue_id>` 表記に統一）。

### 9.3 Must Fix: テスト走査範囲が狭い

**指摘**: `tests/test_skill_phase2b_migration.py:17` の `ALL_SKILL_DOCS` が `*/SKILL.md` + `_shared/*.md` のみで、`issue-create/templates/*.md` や `_shared/design-by-type/*.md` / `_shared/implement-by-type/*.md` を見ていない。`<number>` 残存を検出できていない。

**対応**:

```diff
-SKILL_GLOB = list((PROJECT_ROOT / ".claude" / "skills").glob("*/SKILL.md"))
-SHARED_GLOB = list((PROJECT_ROOT / ".claude" / "skills" / "_shared").glob("*.md"))
-ALL_SKILL_DOCS = SKILL_GLOB + SHARED_GLOB
+ALL_SKILL_DOCS = sorted((PROJECT_ROOT / ".claude" / "skills").rglob("*.md"))
```

加えて `TestSkillNoLegacyPlaceholders` に `<number>` パターンを追加。`<issue-number>` のみだと `issue-<number>-<slug>` 形式（`<` の前が `-` で `issue-` を含むが、対象は `<number>`）を取りこぼす。

これら 2 点により、§ 9.1 で見逃した残存はもちろん、今後 `_shared/` 配下に新規 sub-directory が追加されても自動で検査対象に入る。

### 9.4 Should Fix: 説明文「GitHub Issue 番号」

**指摘**: 多くの Skill 入力表で `issue_id | str | GitHub Issue 番号` のまま。`issue_id` は local mode では `local-pc1-1` 形式も含むため、説明として不正確。

**対応**: § 9.2 の表更新に合わせ、説明文を「正規化済み Issue ID（GitHub 数値または local ID）」に統一（17 Skill）。後続 Phase 3-4 で `provider=local` 経路を実装する際、Skill 作者が「数値しか入らない」と誤読するリスクを回避できる。

### 9.5 対応しなかった検討事項

なし。Must Fix 3 件 + Should Fix 1 件すべて対応済。

## 7. 既知の制約

- Phase 2-B の Skill 改修は **GitHub 経由の実 `gh` 完走による検証は未実施**。grep ベースの静的検証 + CliRunner + subprocess mock の Medium テスト + Phase 2-A の Small テストの 3 段で wrapper 契約を担保しているが、quoting / TTY / 環境変数依存などの実 `gh` 固有のバグは本 Phase ではカバーしない既知ギャップ
- `[pr-number]` placeholder と `[branch-name]` / `[worktree-absolute-path]` / `[prefix]` 等の Skill 自前計算 placeholder は本 Phase で touch していない（Phase 3 範囲）
- `provider=local` で `kaji pr review-comments` を呼んだ場合の bare provider エラーは Phase 4 スコープ。現状は `gh repo view` 失敗で `EXIT_RUNTIME_ERROR=3` 終了するだけ
