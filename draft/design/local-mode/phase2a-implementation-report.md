# [実装報告] kaji local mode — Phase 2-A: CLI 側準備

- **設計書**: `draft/design/local-mode/phase2-design.md`（PR 2-A セクション）
- **対象ブランチ**: `feat/local-mode-phase2a`（worktree: `/home/aki/dev/kaji/kaji-feat-local-mode-phase2a`）
- **コミット**:
  - `7bb5ac9` — `feat(cli): add kaji pr review-comments / reviews / reply-to-comment (Phase 2-A)`
  - `d261981` — `fix(cli): tighten input validation for kaji pr review-comments / reply-to-comment`（レビュー指摘対応、§ 7 参照）
- **作業日**: 2026-05-05
- **GitHub Issue**: 未起票（GitHub 利用不可のため）

## 0. 注意事項

GitHub Issue 起票が不可のため、本来 `/issue-start` で行う Issue 連携・worktree NOTE 追記は省略している。`/issue-start` の通常手順では Issue 本文に worktree のメタ情報を追記するが、本作業はそれを行わずに worktree (`kaji-feat-local-mode-phase2a`) を直接作成した。Phase 2-B で GitHub が復旧した場合、本実装内容を後追いで Issue/PR にリンクする必要がある。

## 1. スコープと境界

設計書「段階リリース戦略」§ PR 2-A に従い、本 Phase では **CLI 側のみ**を変更し、Skill markdown と `prompt.py` の `issue_number` alias は **意図的に温存**した。これにより main へ merge しても既存 Skill は引き続き `gh` 直叩きで動作し、`feature-development.yaml` の `provider=github` 完走は維持される。

| 項目 | Phase 2-A で扱った | 根拠 |
|------|-------------------|------|
| `kaji pr review-comments` 追加 | ✅ | 設計 § PR 2-A |
| `kaji pr reviews` 追加 | ✅ | 同上 |
| `kaji pr reply-to-comment` 追加 | ✅ | 同上 |
| `EXIT_INVALID_INPUT = 2` 追加 | ✅ | 設計「CLI 契約 補足」§ エラー定数 |
| Small テスト（argv 組み立て / 異常系 / passthrough 互換）追加 | ✅ | 設計「テスト戦略 § Small」 |
| Skill markdown の `gh` → `kaji` 置換 | ❌ | Phase 2-B（atomic 切替） |
| `prompt.py` の `issue_number` alias 撤去 | ❌ | Phase 2-B（Skill 改修と同 PR） |
| `[issue-number]` placeholder リネーム | ❌ | Phase 2-B |
| Medium テスト（Skill grep ベース）追加 | ❌ | Phase 2-B |
| 影響ドキュメント更新 | ❌ | Phase 2-B |

## 2. 変更内容

### 2.1 `kaji_harness/cli_main.py`

**追加した定数**:

```python
EXIT_INVALID_INPUT = 2
```

既存の 2 系定数（`EXIT_DEFINITION_ERROR` = workflow YAML スキーマ違反、`EXIT_CONFIG_NOT_FOUND` = config 不在）と意味が分離している。CLI 引数の文法エラー（非数値 PR_ID 等）に使用する。argparse default の `SystemExit(2)` とも数値が一致しているため、ユーザから見た外部仕様の整合性が取れる。

**追加したヘルパ**:

| 関数 | 責務 |
|------|------|
| `_detect_repo()` | `gh repo view --json nameWithOwner -q .nameWithOwner` を `subprocess.run(capture_output=True)` で呼び、stdout の `owner/name` を返す。失敗時 None |
| `_compose_json_and_jq(fields, jq)` | `--json FIELDS` を `[.[] \| {f: .f, ...}]` の jq projection に変換し、user 提供の `--jq` を後段に chain（`<proj> \| <user_jq>`）。両 None のとき None |
| `_forward_pr_review_comments(pr_id, json_fields, jq_expr)` | `repos/<repo>/pulls/<N>/comments` への `gh api` 呼び出し |
| `_forward_pr_reviews(...)` | 同 `pulls/<N>/reviews` |
| `_forward_pr_api_list(..., path_suffix=...)` | 上記 2 関数の共通実装。`path_suffix` だけ差し替え |
| `_forward_pr_reply_to_comment(pr_id, comment_id, body)` | `--method POST` で `comments/<CID>/replies` に POST。`-f body=<body>` で送信 |
| `_dispatch_pr_builtin(sub, rest)` | builtin sub 専用 argparse parser を build。`add_help=True` で `--help` を有効化、引数不足時の argparse default `SystemExit(2)` が `EXIT_INVALID_INPUT` と一致 |
| `_handle_pr(raw_args)` | 2 段ディスパッチ。先頭 token が `_PR_BUILTIN_SUBCOMMANDS` に該当 → `_dispatch_pr_builtin`、それ以外 → 既存 `_forward_to_gh("pr", ...)` |

**main() の dispatch 変更**:

```python
if args.command == "pr":
    return _handle_pr(args.args)   # was: _forward_to_gh("pr", args.args)
```

`kaji pr` の subparser は Phase 1 と同一（`add_help=False` + `argparse.REMAINDER`）に保ったまま、ハンドラ層で 2 段振り分けする方式を採用した。これにより既存 passthrough（`kaji pr view 153 --comments`、`kaji pr merge feat/153`）は完全互換のまま `_forward_to_gh` に流れる。

**意図的に変更しなかったもの**:

- `prompt.py` — Skill が `[issue-number]` を参照中のため、`issue_number` キーは温存
- 既存 Phase 1 の `_FORGE_METHOD_FLAGS` / `_forward_to_gh` の `pr merge` 強制 `--merge` 注入ロジック
- `kaji issue` の subparser（設計通り、`gh issue` には `gh api` 変換が必要なサブコマンドが無い）

### 2.2 `tests/test_cli_main.py`

`@pytest.mark.small` で **5 クラス・合計 19 ケース**を追加した（うち 4 ケースはレビュー指摘対応の境界値テスト、§ 7 参照）。

| クラス | ケース数 | 検証内容 |
|--------|---------|---------|
| `TestComposeJsonAndJq` | 4 | fields-only / jq-only / both / neither の 4 パターン |
| `TestPrReviewCommentsBuiltin` | 8 | argv 組み立て（合成 jq を含む）、`--jq` を渡さないケース、非 ASCII / Unicode 数字 PR_ID → 2、`gh` 不在 → 3、repo 検出失敗 → 3、`--json id,,body` → 2、`--json ,` → 2 |
| `TestPrReviewsBuiltin` | 1 | path_suffix が `reviews` になり、`-q` も `--jq` と同 dest として受理される |
| `TestPrReplyToCommentBuiltin` | 3 | `--method POST` / `-f body=<body>` の生成、非数値 comment_id → 2、Unicode 数字 comment_id → 2 |
| `TestPrBuiltinDispatch` | 3 | 既存 `kaji pr view 153 --comments` は `_forward_to_gh` 経由で素通り（builtin に該当しない token は分岐しない）、`--help` は SystemExit(0)、引数不足は SystemExit(2) |

既存 `TestIssuePrPassthrough` 5 ケース（Phase 1 passthrough + `pr merge --squash` の `--merge` 強制 + `gh` 不在エラー）は無修正で緑のまま維持。

## 3. 検証

```bash
cd /home/aki/dev/kaji/kaji-feat-local-mode-phase2a
source .venv/bin/activate
make check
```

結果: **701 passed, 1 skipped in 61.04s**（レビュー対応コミット `d261981` 後の最新）。ruff (lint + format) / mypy / pytest 全て緑。

設計書「受け入れ条件 § 機械検証可能」のうち、Phase 2-A の範囲に属する以下を満たすことを確認:

- [x] `EXIT_INVALID_INPUT = 2` 追加
- [x] `kaji pr review-comments --help` / `kaji pr reviews --help` / `kaji pr reply-to-comment --help` で usage 表示（`add_help=True` の効果、テスト `test_review_comments_help_exits_zero` で確認）
- [x] `kaji pr review-comments` 引数不足 → exit 2（`test_review_comments_missing_args_exits_two`）
- [x] 非数値 PR_ID → `EXIT_INVALID_INPUT=2`（`test_non_numeric_pr_id_returns_invalid_input`）
- [x] `kaji pr view 153 --comments` 等の既存 passthrough は Phase 1 と同じ `_forward_to_gh` 経由（`test_existing_pr_view_falls_back_to_passthrough`）
- [x] `--json` / `--jq` 合成形が `gh api ... --jq '[.[] | {id: .id, body: .body}] | .[]'`（`test_argv_contains_repo_path_and_composed_jq`）
- [x] 既存 Phase 1 テスト `TestIssuePrPassthrough` 5 件が緑

Phase 2-B 範囲（Skill grep / placeholder grep / `Issue #` hard-code 検出 / `--merge` flag 検出 / `prompt.py` 注入辞書から `issue_number` 削除 / `make verify-docs` / 手動 smoke）は本 Phase の対象外。

## 4. 設計通りに進めたが補足が必要な点

### 4.1 `_forward_pr_api_list` の共通化

設計書では `_forward_pr_review_comments` と `_forward_pr_reviews` を独立関数として擬似コード化していたが、実装では `path_suffix` だけ異なる完全コピーになるため `_forward_pr_api_list(pr_id, *, path_suffix, ...)` に共通化した。設計の契約（exit code、stdout/stderr inherit、引数バリデーション順序）は完全に維持している。

### 4.2 stdout / stderr の inherit

設計書「契約の補足 § stdout/stderr」のとおり、builtin handler 側では `subprocess.run(cmd, check=False)` を `capture_output=False`（デフォルト）で呼んでおり、Skill が `RESULT=$(kaji pr review-comments PR --jq ...)` で受ける既存パターンを壊さない。一方 `_detect_repo()` だけは `capture_output=True` を使い、`owner/name` 文字列を Python 側で読む必要があるため例外的に capture している（user の stdout 経路ではないため副作用なし）。

### 4.3 `--json` の field split

設計擬似コードでは `[f.strip() for f in ns.json_fields.split(",")]` だが、空 field（`--json id,,body` のような typo）が入ると `[.[] | {: .}]` になり jq syntax error を引き起こすため、`if f.strip()` で空要素を除外する形に微修正した。

### 4.4 `Path` の使用

`from pathlib import Path` が cli_main.py の既存 import に含まれているが、本 Phase の追加コードでは Path を使っていない（`_forward_to_gh` 経路と同じく、subprocess 引数は str のまま組み立てる）。

## 5. Phase 2-B への申し送り事項

Phase 2-B は本 Phase が main へ merge された後に着手する前提。以下を atomic に実施する必要がある（設計書「PR 2-B」の通り）:

1. **Skill 機械置換**: `grep -rE '(^|[]=$({;|&[:space:]])gh (issue|pr|api)\b' .claude/skills/*/SKILL.md` で hit する全箇所を `kaji` ベースへ。`gh api .../pulls/N/...` の 3 パターンは PR_ID 抽出が必要なため手動書き換え
2. **`gh pr merge X --merge` の `--merge` 同時除去**
3. **placeholder 網羅リネーム**（設計「placeholder の網羅検出と置換マッピング」表の順序通り）
4. **`prompt.py` の `issue_number` キー削除**（Skill 改修と同 PR で）
5. **`tests/test_prompt_builder.py::test_prompt_emits_both_issue_number_alias_and_issue_id` の rename + 内容書き換え**（`test_prompt_emits_only_issue_id_and_issue_ref` へ）
6. **テスト fixture の `issue=42` → `issue="42"` 化**
7. **Medium テスト追加**（Skill markdown の grep ベース 3 件 + CliRunner 1 件）
8. **影響ドキュメント更新**（5 ファイル + CHANGELOG）
9. **手動 smoke 5 経路**

### 補足: GitHub Issue 復旧後の対応

GitHub が復旧した時点で、本 Phase 2-A コミットは既存 worktree (`kaji-feat-local-mode-phase2a`) のまま PR 化できる。Phase 2 親 Issue を新規起票し、本 PR を「Phase 2-A」として紐付ける運用が想定される（設計書冒頭注記参照）。

## 7. レビュー指摘対応（コミット `d261981`）

初回コミット `7bb5ac9` に対するコードレビューで 3 件の指摘があり、以下のとおり対応した。

### 7.1 Must Fix: `--json` の空 field を silently 受理していた

**指摘**: `--json id,,body` が `id,body` に丸められ、`--json ,` が `--jq` 無しの full response にフォールバックする。CLI の typo を成功扱いにするのはプロジェクトの「外部入力は validation」方針と相性が悪い。

**対応**: `_dispatch_pr_builtin` 内の field 抽出ロジックを「空要素を strip」から「空要素を含む or 全 strip 後 0 件 → `EXIT_INVALID_INPUT=2` で fail-fast」に変更。`gh` を呼ばずに即時エラーで停止する（テスト `test_empty_json_field_rejected` / `test_only_comma_json_rejected` で `mock_run.assert_not_called()` を検証）。

### 7.2 Should Fix: PR/comment ID の `str.isdigit()` が Unicode digits を通す

**指摘**: `"１２３".isdigit()` は True を返し、`repos/o/r/pulls/１２３/comments` のような壊れた API path が組み立てられる。GitHub PR/comment ID は ASCII decimal のみで受けるべき。

**対応**: `_is_ascii_decimal(s) -> bool` ヘルパを追加（`bool(s) and s.isascii() and s.isdigit()`）。PR_ID / comment_id の検証 3 箇所すべてを `_is_ascii_decimal()` 経由に切替。エラーメッセージも「must be numeric」→「must be ASCII decimal」に更新。境界値テスト `test_unicode_digit_pr_id_rejected` / `test_unicode_digit_comment_id_rejected` を追加。

### 7.3 補足: 報告書のテスト数記述が実体と乖離

**指摘**: コード上は 5 クラス 15 ケースだが報告書では 6 クラス 13 ケースとなっている（PR 証跡として要修正）。

**対応**: § 2.2 の表をクラスごとのケース数を明示する形に書き直し、レビュー対応分を含めた最終値（5 クラス・19 ケース）に更新した。初回時点の集計ミス（5 クラスを 6 と誤数えし、ケース合計も誤算）を訂正。

### 7.4 対応しなかった検討事項

なし。3 件すべて対応済。

## 8. 既知の制約

- 本 Phase の追加コマンド（`kaji pr review-comments` 等）は **Skill からまだ呼ばれない**。動作確認は Small テスト（mock 経由）のみで、実 `gh` への疎通テストは Phase 2-B の手動 smoke で行う
- `_detect_repo()` は `gh repo view` への依存が残る。設計書「オープンな論点」のとおり、Phase 3 で `git remote get-url` ベースに置き換えるかは未決
- `provider=local` での bare provider エラー化は Phase 4 スコープであり、現状 `kaji pr review-comments` を local mode で呼んでも `gh repo view` 失敗で `EXIT_RUNTIME_ERROR=3` 終了するだけ（既存挙動の延長として許容）
