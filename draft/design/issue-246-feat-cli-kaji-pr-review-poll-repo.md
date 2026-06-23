# [設計] `kaji pr review-poll` の repo 指定挙動を dispatch 分離 + コメントで self-documenting にする

Issue: #246

## 概要

`kaji pr review-poll`（workflow 専用 step）を `_PR_BUILTIN_SUBCOMMANDS` の一括 dispatch から分離し、`_handle_pr` 内の独立分岐として扱う。分岐に「review-poll が repo 引数（CLI `-R` / config `repo_override`）を受理しない理由」を明記し、`_dispatch_pr_builtin` の `del repo_override` / `if sub == "review-poll"` 不格好分岐を撤去する。あわせて `codex_review_poll.py` の旧 docstring を `#234` の exec step 起動実態に修正する。

## 背景・目的

### ユーザーストーリー

kaji の maintainer / コントリビューターとして、`_dispatch_pr_builtin` の `del repo_override` や `if sub == "review-poll"` 分岐を読み返したとき（または review-poll に repo 指定を足せないか検討したとき）に、review-poll が repo 引数を受理しない理由をコード上で即座に把握したい。毎回コメント欄・git 履歴・他 builtin を再調査せず意図を確認できるようにし、今回のような疑問の連鎖の再発を止めたい。

### 現状の問題

review-poll の repo 解決仕様（`KAJI_GIT_REMOTE`→`git remote get-url` から env 経由で一意に解決）がコードのどこにも明文化されておらず、コードを読むたびに「`-R` はなぜ要る/無い/repo を取らないのか」という疑問が再発する。

- `_dispatch_pr_builtin` は他 builtin と同じ shape で `repo_override` を受け取りながら、review-poll に限り `del repo_override`（`cli_main.py:797`）で即破棄する。「受け取って即捨てる」構造が意図不明瞭。
- review-poll が repo を取らない理由を説明するコメントが存在しない。
- `codex_review_poll.py` の docstring「The skill bash wrapper invokes this module via `python -m ...`」は、`#234` で exec step（`exec: [kaji, pr, review-poll]`）へ移行した現在の起動実態と乖離している（実際は workflow runner = `script_exec.py` が subprocess 起動）。

### 代替案と不採用理由

- **代替案 A: `kaji pr review-poll -R owner/repo` の CLI 受理を実装する** → 不採用。review-poll は worktree 内で自 PR を待つ唯一用途であり、repo は `KAJI_GIT_REMOTE`→git remote から一意に解決される（`review_poll_entry.py:103-126`）。CLI から repo を上書きする動機が実在せず（Issue コメントでの調査で需要なしと確定）、受理面（argparse / 解決経路の分岐）を増やすだけで利用価値がない。
- **代替案 B: 現状維持（`del repo_override` のまま）** → 不採用。本 Issue が解消したい「疑問の連鎖」の根本原因がそのまま残る。

## インターフェース

本変更は **CLI の利用者向けインターフェース（引数仕様・出力）を一切変えない**。`kaji pr review-poll` の受理引数・終了コード・dispatch 先は現状を bit-exact に維持する。変わるのは内部 dispatch 構造とコメント/docstring のみ。

### 入力（不変）

- `kaji pr review-poll`（追加引数なし）。未知引数は argparse が exit code 2 で拒否（現状維持）。
- repo は引き続き env 経由（`KAJI_GIT_REMOTE`、既定 `origin`）→ `git remote get-url` から解決。CLI `-R` / config `repo_override` のいずれも受理しない（現状維持を明文化）。

### 出力（不変）

- `_run_pr_review_poll(rest)` → `review_poll_entry.main([])` の戻り値（int 終了コード）をそのまま返す。
- 副作用（GitHub Reactions/Reviews API polling、verdict 出力）は review_poll_entry / codex_review_poll 側のまま不変。

### 使用例（workflow からの起動。不変）

```yaml
# .kaji/wf/dev.yaml 等の review 収束ループ中の exec step（#234 で移行済み）
- id: review-poll
  exec: [kaji, pr, review-poll]
```

```python
# _handle_pr 内の dispatch（変更後イメージ）
# review-poll は workflow 専用 step。repo は review_poll_entry が
# KAJI_GIT_REMOTE→git remote から一意解決するため repo 引数を受理しない。
if args and args[0] == "review-poll":
    return _run_pr_review_poll(args[1:])
if args and args[0] in _PR_BUILTIN_SUBCOMMANDS:
    return _dispatch_pr_builtin(args[0], args[1:], repo_override=repo_override)
```

### エラー（不変）

- `provider.type='local'` 配下では `_handle_pr` 冒頭の bare-provider ガードで `EXIT_INVALID_INPUT`（現状維持。review-poll 分岐はこのガードより後段に置く）。
- `provider.type='github'` で `config.provider.github.repo` 未設定なら、現状どおり `_handle_pr` の repo 検証（`cli_main.py:1066-1072`）で `EXIT_INVALID_INPUT`。review-poll 分岐はこの検証より後段に置くことで挙動を維持する（後述「制約・前提条件」参照）。

## 制約・前提条件

- **挙動の bit-exact 維持**: 公開 workflow の `exec: [kaji, pr, review-poll]` の動作（dispatch 先・終了コード・repo 検証順序）を変えない。既存テスト（`tests/test_cli_main.py` の `TestHandlePrReviewPoll` 系 `test_dispatches_to_installed_review_poll_entry` / `test_unknown_arg_exits_two`、`tests/workflows/test_review_poll_exec_migration.py`）が green であること。
- **分岐の配置順**: review-poll 分岐は `_handle_pr` 内で
  1. bare-provider ガード（`cli_main.py:1061-1063`）
  2. `repo_override` 解決 + `config.provider.github.repo` 必須検証（`cli_main.py:1065-1073`）

  の **後** かつ `_PR_BUILTIN_SUBCOMMANDS` 判定（`cli_main.py:1084`）の **前** に置く。これにより、現状 review-poll が受けていた「bare-provider 拒否」「github 時の repo 必須検証」を維持しつつ、`repo_override` を review-poll へ渡さない（受理しないことを構造で示す）。
- **他 builtin の不可侵**: `review-comments` / `reviews` / `reply-to-comment` の `repo_override` / config repo 経路には一切手を入れない（別 repo 運用を壊す破壊的変更を避ける）。`_dispatch_pr_builtin` のシグネチャ（`repo_override` 引数）は他 builtin のため維持する。
- **`_PR_BUILTIN_SUBCOMMANDS` の意味整合**: 同 set は「`gh api` 直叩き builtin」を表す（`_handle_pr` docstring `cli_main.py:1035` / `_dispatch_pr_builtin` docstring が既にそう説明）。review-poll は gh api 直叩きではない（workflow helper）ため、set から除外するのが意味的に正しい。
- **依存**: `argparse`（既存）、`kaji_harness.scripts.review_poll_entry`（既存 import）。新規 import / 新規モジュールは作らない。

## 変更スコープ

- `kaji_harness/cli_main.py`
  - `_PR_BUILTIN_SUBCOMMANDS`（`:595`）から `"review-poll"` を除外。
  - `_handle_pr`（`:1032`）に review-poll 独立分岐 + 意図コメント (b) を追加。
  - `_dispatch_pr_builtin`（`:790`）から `if sub == "review-poll": del repo_override; return _run_pr_review_poll(rest)`（`:796-798`）を削除。
- `kaji_harness/scripts/codex_review_poll.py`
  - module docstring（`:6-7`）の「The skill bash wrapper invokes this module via `python -m ...`」を exec step 起動実態に修正 (c)。
- 付随確認（変更要否を判断、不要なら理由を残す）:
  - `_GH_MISSING_GUIDANCE`（`:623-626`、gh 未インストール時の案内に `review-poll` を列挙）/ `_register_pr` help 文（`:626` 付近）に review-poll の言及あり。review-poll は `gh` / `git` を内部利用するため、これら案内文の review-poll 言及は維持してよい（gh 依存は事実）。

## 方針（Minimal How）

1. **(a) 構造整理**:
   - `_PR_BUILTIN_SUBCOMMANDS` を `{"review-comments", "reviews", "reply-to-comment"}` に縮小。
   - `_handle_pr` の `args[0] in _PR_BUILTIN_SUBCOMMANDS` 判定の直前に `if args and args[0] == "review-poll": return _run_pr_review_poll(args[1:])` を追加。`repo_override` は渡さない。
   - `_dispatch_pr_builtin` の review-poll 特例分岐を削除し、関数を「gh api 直叩き builtin 専用」に純化。`_run_pr_review_poll` 関数自体（`:780`）は `_handle_pr` から直接呼ぶため残す。
2. **(b) 意図の明文化（再発防止の本体）**:
   - review-poll 独立分岐の直上に、次の趣旨のコメントを置く:
     「review-poll は workflow 専用 step（`#234` で exec step 化）。repo は `review_poll_entry` が `KAJI_GIT_REMOTE`→`git remote get-url` から一意に解決するため、CLI `-R` / config `repo_override` のいずれも受理しない。他 builtin と異なり `repo_override` を渡さないのはこのため。」
3. **(c) docstring 修正**:
   - `codex_review_poll.py` の「The skill bash wrapper invokes this module via `python -m kaji_harness.scripts.codex_review_poll`.」を、`#234` 以降の実態（workflow runner が `exec: [kaji, pr, review-poll]` 経由で `kaji_harness.scripts.review_poll_entry` を起動し、本モジュールはその polling コアとして呼ばれる）に合わせて書き換える。

## テスト戦略

> 変更タイプ: **実行時コード変更**（dispatch ルーティングの構造変更）。ただし利用者向け振る舞いは bit-exact 不変で、コメント/docstring 追加が主。回帰防止の焦点は「review-poll の dispatch 先・終了コード・repo 非受理が維持され、構造変更で壊れていないこと」。

### 変更タイプ
- 実行時コード変更（内部 dispatch 構造の変更。外部挙動は不変）

### Small テスト
- **既存維持（回帰）**: `_handle_pr(["review-poll"])` が `review_poll_entry.main([])` を 1 回呼び戻り値 0（`test_dispatches_to_installed_review_poll_entry`）。`_handle_pr(["review-poll", "--unexpected"])` が exit code 2（`test_unknown_arg_exits_two`）。これらは構造変更後も pass すること。
- **新規（構造不変条件の固定）**:
  - `"review-poll" not in _PR_BUILTIN_SUBCOMMANDS`（除外を回帰固定。誤って set に戻すと検出）。
  - review-poll 経路で `repo_override` が review-poll helper に渡らないこと（`_run_pr_review_poll` は `rest` のみ受ける署名であることをもって担保。必要なら `_dispatch_pr_builtin` が review-poll を受けても処理しない＝ KeyError 相当にならず gh-api 分岐に落ちる、を明示する負例テストは過剰なので原則追加しない）。
  - github provider で `config.provider.github.repo` 未設定時、review-poll が現状どおり repo 必須検証で `EXIT_INVALID_INPUT` になること（検証順序の維持を固定）。
- **省略するもの**: コメント文言・docstring 文言自体のテストは不要（恒久回帰価値がなく、`testing-convention.md` の「回帰検出情報がほとんど増えない」に該当）。

### Medium テスト
- 不要。ファイル I/O / DB / 内部サービス結合の新規ロジックを追加しない。dispatch は既存の Small レベルで `review_poll_entry.main` を mock して検証できる（`testing-convention.md` 4 条件: 独自ロジック追加なし / 既存ゲートで捕捉 / 回帰情報増えない / 理由説明可、を満たす）。

### Large テスト
- 不要。実 GitHub API 疎通の振る舞いは review_poll_entry / codex_review_poll 側で不変であり、本変更は dispatch 構造のみ。既存の `tests/workflows/test_review_poll_exec_migration.py`（exec step 移行の回帰テスト）が exec 経路の健全性を担保する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし。dispatch 構造の局所整理 |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | なし | workflow / 開発手順は不変（review-poll は引き続き exec step） |
| docs/reference/ | なし | API 仕様 / 規約変更なし |
| docs/cli-guides/ | なし | review-poll は workflow 専用 step で手動 CLI として文書化されていない。利用者向け CLI 仕様は不変 |
| CLAUDE.md | なし | 規約変更なし |

> (b) のコードコメントで意図が self-documenting になるため、追加の docs 記述は省略する（Issue 完了条件「(b) のコードコメントで意図が自明になる場合、追加 docs は最小限 or 省略可」に合致）。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `_dispatch_pr_builtin` 実装 | `kaji_harness/cli_main.py:790-798` | `if sub == "review-poll": del repo_override; return _run_pr_review_poll(rest)` — 「受け取って即捨てる」分岐の現物。(a) で撤去する対象 |
| `_handle_pr` dispatch | `kaji_harness/cli_main.py:1061-1086` | bare-provider ガード→repo_override 解決/検証→`review` 分岐→`_PR_BUILTIN_SUBCOMMANDS` 分岐→`gh` passthrough の順序。review-poll 独立分岐の挿入位置と検証順維持の根拠 |
| `_PR_BUILTIN_SUBCOMMANDS` 定義 | `kaji_harness/cli_main.py:595` | `{"review-comments", "reviews", "reply-to-comment", "review-poll"}`。set から review-poll を除外する対象。docstring 上は「gh api 直叩き」builtin と位置づけ（`:1035` / `:790`） |
| repo 解決経路 | `kaji_harness/scripts/review_poll_entry.py:102-126` | `git_remote = os.environ.get("KAJI_GIT_REMOTE", "origin")` → `git remote get-url` → `parse_remote_url` で owner/repo を一意解決。CLI から repo を渡す必要がないことの裏付け |
| 旧 docstring | `kaji_harness/scripts/codex_review_poll.py:6-7` | 「The skill bash wrapper invokes this module via `python -m kaji_harness.scripts.codex_review_poll`.」— (c) で exec step 起動実態へ修正する対象 |
| 既存 dispatch テスト | `tests/test_cli_main.py:841-860` | `test_dispatches_to_installed_review_poll_entry` / `test_unknown_arg_exits_two`。構造変更後も維持すべき回帰基準 |
| exec step 移行 | Issue `#234` / PR `#245` / `tests/workflows/test_review_poll_exec_migration.py` | review-poll が bash wrapper から exec step（`exec: [kaji, pr, review-poll]`）へ移行した経緯。(c) docstring 修正と (b) コメント文言の根拠 |
