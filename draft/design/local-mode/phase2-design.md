# [設計] kaji local mode — Phase 2: Skill の `gh` → `kaji` 置換 + provider 中立コンテキスト変数移行

- **対応 Issue**: TBD（GitHub アカウント停止中。復旧後に起票して紐付ける。**Issue 起票後は `type:*` ラベル / 完了条件 / review-ready 7 観点に対する再確認が必要**。本設計書は親設計の Phase 2 スコープに対する詳細化として書かれており、Issue 単体の review-ready は別途実施する）
- **親設計**: `draft/design/local-mode/design.md`
- **前 Phase**: `draft/design/local-mode/phase1-implementation-report.md`
- **対象 Phase**: Phase 2（design.md「工数見積」表より 3 日見積）
- **作成日**: 2026-05-05

## Primary Sources（一次情報）

| カテゴリ | パス / コマンド | 参照目的 |
|---------|----------------|---------|
| 親設計 | `draft/design/local-mode/design.md`「Skill の改修」章（行 803-841）| 置換マッピング正本 |
| 親設計 | 同「provider 中立コンテキスト変数」章（行 855-883）| 変数体系の正本 |
| 親設計 | 同「Phase 2 で更新される全 Skill 範囲」章（行 871-881）| 更新箇所のチェックリスト化 |
| 親設計 | 同「Phase 1-2 の暫定動作」章（行 275-283）| Phase 2 段階で provider 抽象を導入しない根拠 |
| Phase 1 報告 | `phase1-implementation-report.md` 5.1 / 6 章 | `__post_init__` の int→str 正規化、`issue_number` alias の維持と Phase 2 完了時撤去方針 |
| 既存 Skill 全件 | `.claude/skills/*/SKILL.md`（23 ファイル）| 置換対象。`grep -hE "^\s*gh " .claude/skills/*/SKILL.md` で 20 ファイルが `gh` を呼び、3 ファイル（`i-doc-verify` / `i-doc-review` / `i-doc-fix`）は呼ばない |
| 既存 Skill 規約 | `.claude/skills/issue-start/SKILL.md:32-33` | branch 命名 `<prefix>/<gh-number>` および worktree dir `kaji-<prefix>-<id>` の正本 |
| 既存 Skill 規約 | `.claude/skills/issue-design/SKILL.md:28` | `issue_number: int` 前提の代表例（型変更影響箇所） |
| Phase 1 実装 | `kaji_harness/cli_main.py` の `kaji issue` / `kaji pr` subparser、`_forward_to_gh`、`pr merge` の `--merge` 強制 | Phase 2 で Skill から呼ぶ wrapper の現状契約 |
| Phase 1 実装 | `kaji_harness/cli_main.py:29-34` の `EXIT_*` 定数 | `EXIT_OK=0` / `EXIT_ABORT=1` / `EXIT_VALIDATION_ERROR=1` / `EXIT_DEFINITION_ERROR=2` / `EXIT_CONFIG_NOT_FOUND=2` / `EXIT_RUNTIME_ERROR=3` のみ存在し、CLI 引数文法エラー用の定数が無い事実（Phase 2 で `EXIT_INVALID_INPUT=2` を追加する根拠）|
| Phase 1 実装 | `kaji_harness/prompt.py` の `issue_number` / `issue_id` / `issue_ref` 同時注入 | Skill が参照可能な provider 中立変数の現状 |
| 既存 placeholder 実態 | `grep -rE '#\[issue-number\]\|\[number\]\|issue-\[number\]\|\[prefix\]/\[number\]\|kaji-\[prefix\]-\[number\]\|Closes #\[issue-number\]' .claude/skills/*/SKILL.md` で **47 件以上** | `[issue-number]` 単純置換だけでは取りこぼす表記ゆれの根拠（後述「placeholder の網羅検出」）|
| 規約 | `docs/dev/shared_skill_rules.md` | Skill 共通規約（placeholder 表記等） |
| 規約 | `docs/dev/skill-authoring.md` | Skill 改修時の品質基準 |
| **外部一次情報** | GitHub CLI manual: `gh help api` / `gh help pr merge` / `gh help pr review` | `gh api` は **`--jq EXPR` のみで `--json FIELDS` を持たない**事実 → kaji 側で `--json` を受けて `--jq` に合成する必要がある根拠。`gh pr merge` の `--merge` / `--squash` / `--rebase` は排他、Phase 1 の `--merge` 強制と整合 |
| **外部一次情報** | GitHub REST API docs: `Pull request review comments` (`/repos/{o}/{r}/pulls/{n}/comments`) / `reviews` (`/repos/{o}/{r}/pulls/{n}/reviews`) / `Create reply for a review comment` (`POST /repos/{o}/{r}/pulls/{n}/comments/{cid}/replies`) | `kaji pr review-comments` / `reviews` / `reply-to-comment` の URL path 構造の正本。**reply API は top-level review comment への reply のみ受け付け、issue comment や thread reply には適用不可**（Phase 2 では既存 `pr-fix` Skill が使うパターンと完全一致するため問題なし）|

## 概要

Phase 2 では、`.claude/skills/*/SKILL.md` 内に存在する **GitHub CLI 直接呼び出し（`gh issue` / `gh pr` / `gh api`）を `kaji` ラッパー経由に置換**し、同時に **placeholder 表記の表記ゆれ（`[issue-number]` / `[number]` / `#[issue-number]` / `issue_number:` / `issue-[number]-*.md` など）を `[issue_id]` / `[issue_ref]` の 2 変数に正規化**する。

**親設計（design.md 行 861-869）が想定する 7 変数移行（`issue_id` / `issue_ref` / `issue_input` / `branch_prefix` / `branch_name` / `worktree_dir` / `design_path`）のうち、Phase 2 では `issue_id` / `issue_ref` の 2 変数のみを完成させ、残り 5 変数は Phase 3 以降に延期する。** 理由は親設計が前提する Workflow / Step モデルへの prefix / slug 供給経路がまだ存在せず、Phase 2 段階で 7 変数すべてを生成すると default 値の hard-code（`feat` 等）が `docs/247` / `fix/123` 等の既存 worktree 解決と矛盾するため。詳細はスコープ章および out-of-scope 章を参照。

> **親設計の Phase 2 約束との差分**: 親設計は「Phase 2 で 7 変数移行」と書かれているが、本 Phase 2 設計書では実装可能性に基づき **「Phase 2 = `issue_id` / `issue_ref` への移行 + Skill が `[prefix]` 等を自前計算する経路の温存」** に縮小する。Phase 3 開始時に親設計を更新し、残り 5 変数の供給経路（workflow YAML 拡張 / Issue NOTE 読み出し / Skill 内計算正本化）の決定とともに整理する。

この Phase の本質は **Skill markdown のテキスト編集**であり、Python 実装側の変更は最小限（`prompt.py` の `issue_number` alias 撤去のみ、Phase 2 完了時）。LocalProvider・provider 切替・`kaji sync` は Phase 3 以降のスコープであり、本 Phase では一切扱わない。

Phase 1 実装報告（5.1）にあるとおり `WorkflowRunner` / `SessionState` は `__post_init__` で int → str 正規化を行うため、Skill 改修と Python テスト fixture の str 化は **同 Phase で揃える**ことが望ましい（Phase 1 では型変更だけ、Phase 2 で fixture も含めて str ベースに揃える）。

## 背景・目的

### Phase 1 完了時点の状況

- `kaji issue` / `kaji pr` の薄い wrapper は実装済（中身は `gh` 転送）
- `kaji run` の issue 引数は str 受理に切替済
- `prompt.py` は **`issue_number` を後方互換 alias として維持**したまま、provider 中立な `issue_id` / `issue_ref` も注入する状態
- Skill 側はまだ `gh` 直叩き + `[issue-number]` placeholder のままで、Phase 1 の wrapper を一切使っていない

### Phase 2 が解決する問題

| 問題 | 現状 | Phase 2 後 |
|------|------|-----------|
| Skill の forge 依存 | 20/23 Skill が `gh issue` / `gh pr` / `gh api` を直接呼ぶ | 全 Skill が `kaji issue` / `kaji pr` 経由（provider 中立化の前提） |
| placeholder の int 前提と表記ゆれ | `[issue-number]` / `[number]` / `#[issue-number]` / `issue_number: int` / `issue-[number]-*.md` 等の表記が混在し、いずれも数値 GitHub Issue 番号を暗黙前提 | `[issue_id]` / `[issue_ref]` の 2 表記に統一（`#` の hard-code は `[issue_ref]` に集約。残り 5 変数の正本化は Phase 3 以降）|
| 表示文言の hard-code | `Issue #[issue-number]` のように `#` prefix が hard-code | `Issue [issue_ref]`（local では `#` なし、`prompt.py` 側で provider 別整形） |
| `gh pr merge --merge` の冗長性 | `gh pr merge X --merge` を Skill が直書き | `kaji pr merge X` に簡素化（method flag は wrapper が固定で `--no-ff` 相当を強制） |

### Phase 2 が解決しない問題（後続 Phase スコープ）

- LocalProvider 実装、`provider.type` の fail-fast 化（**Phase 3**）
- `pr-fix` / `pr-verify` の provider=local エラー停止 + 代替ガイド（**Phase 4**。Phase 2 では `gh api` → `kaji pr review-comments` 等の文字列置換のみ行い、CLI 側の bare provider エラー実装は Phase 3-4）
- `kaji sync from-github` / `local-to-github-plan`（**Phase 5**）
- `feature-development-local.yaml` 追加（**Phase 4**）

これらに依存しない範囲で Phase 2 を完結させる方針。Phase 2 完了時点でも GitHub provider のみで運用する利用者にとっては **挙動変化ゼロ**（wrapper を経由するだけで `gh` のフラグは全て透過する）。

## スコープ

### in-scope

1. **20 Skill の `gh` 呼び出し置換**: `gh issue` / `gh pr` / `gh api` を `kaji issue` / `kaji pr` / `kaji pr review-comments|reviews|reply-to-comment` へ
2. **`gh pr merge X --merge` の `--merge` 同時除去**: `kaji pr merge X` に統一
3. **placeholder の網羅リネーム**: 全 23 Skill の以下の表記すべてを **`[issue_id]` / `[issue_ref]` の 2 種**へ統合する。詳細マッピングは後述「placeholder の網羅検出」表に従う
   - `[issue-number]` / `[number]`（裸の数値 placeholder）→ `[issue_id]`
   - `#[issue-number]`（`#` 込み hard-code、30 件確認）→ `[issue_ref]`
   - `Closes #[issue-number]`（PR body の auto-link）→ `Closes [issue_ref]`
   - `issue-[number]-*.md`（design 書ファイル名 hard-code）→ `issue-[issue_id]-*.md`（**Phase 2 では provider 別の `[design_path]` 化はせず、`[issue_id]` リネームのみ**）
   - `[prefix]/[number]` / `kaji-[prefix]-[number]` → `[prefix]/[issue_id]` / `kaji-[prefix]-[issue_id]`（**`[prefix]` 自体は Skill 自前計算を温存**）
   - `issue_number:` frontmatter 型注釈 → `issue_id:`
4. **`kaji_harness/prompt.py` の後方互換 alias 撤去**: `issue_number` キーの注入を Phase 2 完了時に削除し、`issue_id` / `issue_ref` の 2 変数のみを残す
5. **Python テスト fixture の str 化**: `WorkflowRunner` / `SessionState` 等への int 渡しを廃止
6. **改修後 Skill の lint / link checker 緑**: `make check`（ruff / mypy / pytest）と `make verify-docs`（Markdown link checker）の両方を通す
7. **影響ドキュメントの更新**: `docs/dev/skill-authoring.md`（注入変数表）/ `docs/dev/shared_skill_rules.md`（placeholder 表記）/ `docs/ARCHITECTURE.md`（`issue_number` 言及）/ `docs/dev/development_workflow.md` / `docs/dev/docs_maintenance_workflow.md`（`issue_number` / `gh pr create` 言及）を更新する

### out-of-scope

- LocalProvider のファイル I/O 実装（Phase 3）
- `kaji issue --json` / `--jq` の自前実装（Phase 3。Phase 2 段階では `gh` 透過で動作する）
- `provider.type` 必須化、`config.local.toml` 導入、`.gitignore` 更新（Phase 3）
- `pr-fix` / `pr-verify` の bare provider 専用エラー（Phase 4）
- `feature-development-local.yaml` 追加（Phase 4）
- `kaji sync ...` 系コマンド（Phase 5）
- 既存 Issue 番号 (`#1`〜`#170`) の local 形式への移行（恒久的に out of scope）
- **`branch_prefix` / `branch_name` / `worktree_dir` / `design_path` を `prompt.py` で生成し prompt 辞書に注入する変更（Phase 3 以降）**。理由: 現行 Workflow / Step モデルには step args が無く、prefix / slug の供給経路が存在しない。`.claude/skills/issue-start/SKILL.md:23-40` のとおり prefix は `$ARGUMENTS` から取得して Skill 内で `[prefix]/[issue-number]` を組み立てる方式が正本。`prompt.py` 側で default `feat` を勝手に注入すると `docs/247` / `fix/123` の worktree 解決や `draft/design/issue-*-*.md` 探索が壊れる。Phase 2 では Skill 内の既存計算ロジック（`[prefix]/[issue_id]` を Skill 自身が組み立てる）を**そのまま温存**し、placeholder 名のリネーム（`[issue-number]` → `[issue_id]`）と表示文言整形（`Issue #N` → `Issue [issue_ref]`）に限定する

## 詳細設計

### Skill 改修の正本マッピング

`grep -hE "^\s*gh " .claude/skills/*/SKILL.md | sort -u` で抽出した実在パターン全件に対し、置換後の `kaji` 呼び出しを定義する。

#### `gh issue` 系（→ `kaji issue`、フラグ透過）

| 既存 | 置換後 | 備考 |
|------|--------|------|
| `gh issue create --title T --body B --label L` | `kaji issue create --title T --body B --label L` | フラグ完全透過 |
| `gh issue view N --comments` | `kaji issue view N --comments` | 同上 |
| `gh issue view N --json F --jq E` | `kaji issue view N --json F --jq E` | Phase 2 では中身は `gh` 直叩きのため挙動完全一致 |
| `gh issue view N --json body -q '.body'` | `kaji issue view N --json body -q '.body'` | Skill `issue-start` 等が `CURRENT_BODY=$(...)` パターンで使用。Phase 2 では `gh` 透過で raw 出力も維持される |
| `gh issue edit N --body ...` / `--body-file ...` | `kaji issue edit N --body ...` / `--body-file ...` | |
| `gh issue comment N --body ...` / `--body-file -` | `kaji issue comment N --body ...` / `--body-file -` | heredoc / stdin 透過 |
| `gh issue close N --reason completed` | `kaji issue close N --reason completed` | |

#### `gh pr` 系（→ `kaji pr`）

| 既存 | 置換後 | 備考 |
|------|--------|------|
| `gh pr list --search Q --json F --jq E` | `kaji pr list --search Q --json F --jq E` | `pr-fix` 等で使用 |
| `gh pr list --head B --json F --jq E` | `kaji pr list --head B --json F --jq E` | branch から PR 逆引き |
| `gh pr view PR --comments` | `kaji pr view PR --comments` | |
| `gh pr comment PR --body-file -` | `kaji pr comment PR --body-file -` | heredoc |
| `gh pr review PR --approve --body-file -` | `kaji pr review PR --approve --body-file -` | heredoc |
| `gh pr review PR --request-changes --body-file -` | `kaji pr review PR --request-changes --body-file -` | heredoc |
| **`gh pr merge B --merge`** | **`kaji pr merge B`**（`--merge` を**同時除去**）| Phase 1 wrapper 側で `--merge` を強制注入。Skill 側に method flag を残すと user に「method を選べる」誤認を与えるため除去必須 |

#### `gh api` 系（→ `kaji pr review-comments` / `kaji pr reviews` / `kaji pr reply-to-comment`）

| 既存 | 置換後 | 備考 |
|------|--------|------|
| `gh api repos/{o}/{r}/pulls/N/comments --jq E` | `kaji pr review-comments PR --jq E` | `pr-fix` / `pr-verify` で使用 |
| `gh api repos/{o}/{r}/pulls/N/reviews --jq E` | `kaji pr reviews PR --jq E` | 同上 |
| `gh api repos/{o}/{r}/pulls/N/comments/CID/replies` (POST 経由) | `kaji pr reply-to-comment PR --to CID --body B` | `pr-fix` のレビュー返信処理 |

**重要**: 親設計（design.md 行 113-118）では `kaji pr review-comments` / `kaji pr reviews` / `kaji pr reply-to-comment` を **CLI 仕様として定義済**。Phase 2 ではこれら 3 サブコマンドを `_forward_to_gh` 経由で実装する必要がある（Phase 1 wrapper には未実装）。

##### `kaji pr review-comments` / `reviews` / `reply-to-comment` の CLI 契約

3 サブコマンドは forge 専用の補助で、Phase 1 の `pr` 直 passthrough（`gh pr <args>...`）では表現できない（`gh api` への変換が必要）。Phase 2 で以下の契約で `cli_main.py` に追加する。

**コマンド仕様**:

```
kaji pr review-comments PR_ID [--json FIELDS] [--jq EXPR | -q EXPR]
kaji pr reviews         PR_ID [--json FIELDS] [--jq EXPR | -q EXPR]
kaji pr reply-to-comment PR_ID --to COMMENT_ID --body TEXT
```

**引数の扱い（review-comments / reviews 共通）**:

| 引数 | 必須 | 転送先 |
|------|-----|-------|
| `PR_ID` | 必須 | URL path に埋め込む。数値以外は文法エラーで `EXIT_INVALID_INPUT=2` |
| `--json FIELDS` | 任意 | **kaji 側で受け取り、`gh api` の `--jq` に `[.[] \| {field1: .field1, ...}]` 相当に合成する**。`gh api` 自身は `--json` を持たない（`gh help api` 確認済、外部一次情報参照）ため、kaji 層で fields を jq projection に変換するのが本契約の核 |
| `--jq EXPR` / `-q EXPR` | 任意 | `gh api` の `--jq` に渡す。`--json` 同時指定時は `--json` の field projection を先に適用してから `--jq` の式を適用（`[.[] \| {fields}] \| <user_jq>` の合成形）|

**実装ロジック**:

```python
def _forward_pr_review_comments(
    pr_id: str,
    *,
    json_fields: list[str] | None,
    jq_expr: str | None,
) -> int:
    """Forward to `gh api repos/<repo>/pulls/<N>/comments`.

    Returns:
        gh process の exit code（0=成功、非ゼロ=失敗）。stdout/stderr は
        親プロセスに inherit される（capture せず透過）。`gh` 不在 → 3、
        repo 検出失敗 → 3、PR_ID が非数値 → 2。
    """
    if not pr_id.isdigit():
        sys.stderr.write(f"ERROR: PR_ID must be numeric, got: {pr_id}\n")
        return EXIT_INVALID_INPUT  # 2 — Phase 2 で新規追加する定数（後述）

    if shutil.which("gh") is None:
        sys.stderr.write(_GH_MISSING_GUIDANCE)
        return EXIT_RUNTIME_ERROR  # 3

    repo = _detect_repo()  # gh repo view --json nameWithOwner -q .nameWithOwner
    if repo is None:
        sys.stderr.write(
            "ERROR: failed to detect current repository.\n"
            "Run 'gh repo view --json nameWithOwner' in a checked-out repo first.\n"
        )
        return EXIT_RUNTIME_ERROR  # 3

    cmd = ["gh", "api", f"repos/{repo}/pulls/{pr_id}/comments"]
    effective_jq = _compose_json_and_jq(json_fields, jq_expr)
    if effective_jq is not None:
        cmd.extend(["--jq", effective_jq])

    return subprocess.run(cmd, check=False).returncode  # stdout/stderr は inherit


def _compose_json_and_jq(fields: list[str] | None, jq: str | None) -> str | None:
    """`--json` と `--jq` を `gh api --jq` の単一式に合成する。

    - fields のみ → `[.[] | {f1: .f1, f2: .f2}]`（gh の `--json` 既定動作と一致）
    - jq のみ → そのまま返す
    - 両方 → `[.[] | {f1: .f1, ...}] | <user_jq>`（field filter → user 式 の順）
    - どちらも None → None（`--jq` 自体を gh に渡さない）
    """
    if fields is None and jq is None:
        return None
    field_proj = (
        "[.[] | {" + ", ".join(f"{f}: .{f}" for f in fields) + "}]"
        if fields else None
    )
    if field_proj and jq:
        return f"{field_proj} | {jq}"
    return field_proj or jq
```

`reviews` は URL を `pulls/{pr_id}/reviews` に変えただけの同形。

`reply-to-comment` は POST：

```python
def _forward_pr_reply_to_comment(pr_id: str, *, comment_id: str, body: str) -> int:
    if not pr_id.isdigit() or not comment_id.isdigit():
        return EXIT_INVALID_INPUT  # 2 — Phase 2 で新規追加
    if shutil.which("gh") is None:
        return EXIT_RUNTIME_ERROR
    repo = _detect_repo()
    if repo is None:
        return EXIT_RUNTIME_ERROR
    cmd = [
        "gh", "api", "--method", "POST",
        f"repos/{repo}/pulls/{pr_id}/comments/{comment_id}/replies",
        "-f", f"body={body}",
    ]
    return subprocess.run(cmd, check=False).returncode
```

**契約の補足**:

- **stdout / stderr**: capture せず親プロセスに inherit する。Skill が `RESULT=$(kaji pr review-comments PR --jq ...)` のように command substitution で受ける既存パターンを壊さない
- **exit code**: `gh` の exit code をそのまま返す。`gh api` が 404 / network error 時の非ゼロ終了は呼び出し側に伝播
- **LocalProvider 分岐**: Phase 2 では行わない（Phase 4 スコープ）。`provider=local` で呼ばれた場合の bare-provider エラー化は Phase 4 で実装する。Phase 2 段階では `gh` 経由で動作する（または `gh` 不在 / repo 未検出で 3 で停止する）が既存挙動の延長として許容される
- **エラー定数**: `EXIT_RUNTIME_ERROR=3` は Phase 1 で `cli_main.py:34` に定義済（`_forward_to_gh` で使用）、これを流用する。`EXIT_INVALID_INPUT=2` は **Phase 2 で新規追加する**。Phase 1 時点の定数は `EXIT_OK=0` / `EXIT_ABORT=1` / `EXIT_VALIDATION_ERROR=1` / `EXIT_DEFINITION_ERROR=2` / `EXIT_CONFIG_NOT_FOUND=2` / `EXIT_RUNTIME_ERROR=3` のみであり、CLI 引数文法エラーを表現する定数が存在しない（`EXIT_DEFINITION_ERROR=2` は workflow YAML スキーマ違反、`EXIT_CONFIG_NOT_FOUND=2` は config 不在で意味が異なる）。Phase 2 で `kaji_harness/cli_main.py` の `EXIT_*` セクションに `EXIT_INVALID_INPUT = 2` を追加し、`_forward_pr_review_comments` / `_forward_pr_reviews` / `_forward_pr_reply_to_comment` から使用する。Phase 1 既存の `_forward_to_gh` の挙動（gh 経由の引数文法エラーは gh 自身が exit 1 を返す）は本定数を導入しても変化しない

##### argparse 実装方針（受け入れ条件「`kaji --help` から到達可能」を満たす形）

Phase 1 の `kaji pr` は `add_help=False` + `argparse.REMAINDER` の透過 passthrough（`kaji_harness/cli_main.py:99` 付近）。Phase 2 で 3 サブコマンドを追加するにあたり、以下の **2 段ディスパッチ方式**を採用する。

**方針**: `kaji pr` の subparser は Phase 1 の REMAINDER 透過のまま温存しつつ、ハンドラ内で REMAINDER token の先頭を検査して専用ハンドラに振り分ける。これにより既存 passthrough 互換と新コマンドの help 露出を両立する：

```python
# kaji_harness/cli_main.py（擬似コード）
_PR_BUILTIN_SUBCOMMANDS = {"review-comments", "reviews", "reply-to-comment"}

def _handle_pr(args: argparse.Namespace) -> int:
    raw = _strip_leading_dash_dash(args.args)
    if not raw:
        return _forward_to_gh("pr", raw)  # `kaji pr` 単独 → `gh pr` の help が出る
    sub = raw[0]
    if sub in _PR_BUILTIN_SUBCOMMANDS:
        return _dispatch_pr_builtin(sub, raw[1:])
    return _forward_to_gh("pr", raw)


def _dispatch_pr_builtin(sub: str, rest: list[str]) -> int:
    """builtin sub 専用 argparse で rest を parse する。

    `--help` / `-h` はここで build した parser が処理し、
    sub ごとの usage を表示する。invalid args は argparse が exit 2 で停止する
    （argparse default の SystemExit）— Phase 2 で `EXIT_INVALID_INPUT=2` と一致。
    """
    p = argparse.ArgumentParser(
        prog=f"kaji pr {sub}",
        add_help=True,  # ← ここは True にして専用 help を出す
    )
    p.add_argument("pr_id", type=str, help="PR number")
    if sub in {"review-comments", "reviews"}:
        p.add_argument("--json", dest="json_fields", default=None,
                       help="Comma-separated field list")
        # `--jq` と `-q` はどちらも同じ dest に入れる
        p.add_argument("--jq", "-q", dest="jq_expr", default=None,
                       help="jq expression applied after --json projection")
    elif sub == "reply-to-comment":
        p.add_argument("--to", dest="comment_id", required=True, type=str)
        p.add_argument("--body", required=True, type=str)
    ns = p.parse_args(rest)
    fields = (
        [f.strip() for f in ns.json_fields.split(",")]
        if getattr(ns, "json_fields", None) else None
    )
    if sub == "review-comments":
        return _forward_pr_review_comments(ns.pr_id, json_fields=fields, jq_expr=ns.jq_expr)
    if sub == "reviews":
        return _forward_pr_reviews(ns.pr_id, json_fields=fields, jq_expr=ns.jq_expr)
    return _forward_pr_reply_to_comment(ns.pr_id, comment_id=ns.comment_id, body=ns.body)
```

**この方針が満たす受け入れ条件**:

- `kaji pr review-comments --help` → 専用 usage 表示（`add_help=True` の効果）
- `kaji pr review-comments` 引数不足 → argparse が `EXIT_INVALID_INPUT=2` 相当で停止
- `kaji pr view 153 --comments` 等の既存コマンド → REMAINDER token 先頭が builtin に該当しないため `_forward_to_gh` に流れ、Phase 1 互換
- `kaji pr review-comments 153 --json id,body --jq '.[]'` → `_compose_json_and_jq()` で `[.[] | {id: .id, body: .body}] | .[]` に合成され `gh api` に渡る

**`kaji issue` は本 Phase で変更しない**: `gh issue` 系には `gh api` への変換が必要なサブコマンドが存在しないため、Phase 1 の単純 passthrough を維持する（受け入れ条件側でも `kaji issue` 配下の builtin 追加は要求していない）。

### Phase 2 で扱う prompt 注入変数（限定版）

親設計（design.md 行 861-869）の 7 変数（`issue_id` / `issue_ref` / `issue_input` / `branch_prefix` / `branch_name` / `worktree_dir` / `design_path`）のうち、**Phase 2 では `issue_id` / `issue_ref` の 2 変数だけを正式変数として扱う**。残り 5 変数は Phase 3 以降に延期する（前述「out-of-scope」参照）。

| 変数名 | 型 | 内容 | 例（github）| 例（local）| Phase |
|--------|----|----|------------|------------|------|
| `issue_id` | `str` | 正規化済み内部 ID。CLI 引数の正本 | `"153"` | `"local-pc1-1"` | **Phase 1 で注入済**（継続）|
| `issue_ref` | `str` | 人間可読参照（表示用）| `"#153"` | `"local-pc1-1"` | **Phase 1 で注入済**（継続）|
| `issue_input` | `str` | shell-safe な CLI 引数 | `"153"` | `"local-pc1-1"` / `"gh:153"` | Phase 3 |
| `branch_prefix` | `str` | feat / fix / docs 等 | `"feat"` | `"feat"` | Phase 3 |
| `branch_name` | `str` | branch 名全体 | `"feat/153"` | `"feat/local-pc1-1"` | Phase 3 |
| `worktree_dir` | `str` | worktree dir 名 | `"kaji-feat-153"` | `"kaji-feat-local-pc1-1"` | Phase 3 |
| `design_path` | `str` | 設計書パス | `"draft/design/issue-153-<slug>.md"` | `"draft/design/local-pc1-1-<slug>.md"` | Phase 3 |

#### Phase 2 段階での `prompt.py` 仕様

Phase 1 では `issue_number`（int → str 後方互換 alias）と `issue_id` / `issue_ref` の 3 変数が同時注入されている（実装報告 2.1 / 5.3）。Phase 2 では：

- **alias `issue_number` を撤去**し、注入辞書を `issue_id` / `issue_ref` の 2 変数に集約する
- `branch_prefix` / `branch_name` / `worktree_dir` / `design_path` / `issue_input` の **prompt.py 側での生成・注入は行わない**（Phase 3 以降）
- 結果として `prompt.py` の変更は「`issue_number` キーの削除」と「既存 Skill placeholder のリネームに合わせた docstring 更新」だけで済み、`_derive_neutral_context()` の新設は **不要**

#### Skill 内 placeholder の扱い（branch / design_path）

`[prefix]` / `[branch-name]` / `[worktree-absolute-path]` / `draft/design/issue-[issue-number]-*.md` 等の Skill 内 hard-code は **Phase 2 では構造変更しない**。次の機械置換のみ実施：

- 全 placeholder 中の `[issue-number]` を `[issue_id]` に純粋リネーム
  - 例: `[prefix]/[issue-number]` → `[prefix]/[issue_id]`
  - 例: `kaji-[prefix]-[issue-number]` → `kaji-[prefix]-[issue_id]`
  - 例: `draft/design/issue-[issue-number]-*.md` → `draft/design/issue-[issue_id]-*.md`
- 表示文言中の `Issue #[issue-number]` を `Issue [issue_ref]` に置換（`#` の hard-code を撤去）
- `[prefix]` / `[branch-name]` 等は **そのまま**。これらは Skill 自身が `$ARGUMENTS` / Issue NOTE から計算する設計（`.claude/skills/issue-start/SKILL.md:23-40` 参照）であり、Phase 2 で正本を移すと既存 worktree 解決が壊れるため Phase 3 まで保留する

これにより「github mode で従来動作する」状態を維持したまま `[issue-number]` の死語化と `#` hard-code の撤去だけ達成できる。`branch_name` / `design_path` の正本化（workflow model 拡張 vs Issue NOTE 読み出し vs Skill 内計算維持の選択）は Phase 3 のオープン論点として持ち越す。

### Skill 別の改修サマリ

`grep` で抽出した 23 Skill のうち、**`gh` 呼び出しがある 20 Skill** と **placeholder のみ持つ 3 Skill** で改修内容が異なる。

#### `gh` 呼び出し + placeholder の両方を持つ Skill (20)

| Skill | `gh` 呼び出し数 | 主な置換 |
|-------|----------------|---------|
| `pr-verify` | 7 | `gh api .../pulls/N/comments\|reviews` × 複数、`gh pr review` |
| `pr-fix` | 7 | 同上 + `gh pr list --search` |
| `i-dev-final-check` | 6 | `gh issue view --json` 多用 |
| `issue-review-ready` | 5 | `gh issue view`、`gh issue edit`（コメント追記）|
| `issue-fix-ready` | 4 | 同上 |
| `issue-close` | 4 | `gh pr merge --merge`、`gh issue close`、`gh issue comment` |
| `kaji-run-verify` | 3 | `gh issue comment`、`gh issue view` |
| `issue-review-design` / `issue-review-code` / `issue-implement` | 3 各 | `gh issue view --json body -q '.body'`、`gh issue comment`（heredoc）|
| `issue-verify-design` / `issue-verify-code` / `issue-fix-design` / `issue-fix-code` / `issue-design` / `i-doc-final-check` | 2 各 | `gh issue view`、`gh issue comment`/`edit` |
| `issue-start` / `issue-create` / `i-pr` / `i-doc-update` | 1 各 | 各 1 件の単純置換 |

#### `gh` 呼び出しを持たないが placeholder を持つ Skill (3)

| Skill | 改修内容 |
|-------|---------|
| `i-doc-verify` | `[issue-number]` / `issue_number` の placeholder を `[issue_id]` / `issue_id` に置換 |
| `i-doc-review` | 同上 |
| `i-doc-fix` | 同上 |

これら 3 Skill は `gh` 置換が不要のため、Phase 2 では **placeholder 移行のみ**。

### Skill 改修の機械的手順

#### `gh` 残存検出の grep 仕様

Phase 1 報告 56 行目で「**注: この計測は文字列マッチで verdict 例の prose にも hit しうるため、コードブロック内の `gh` 呼び出しのみを抽出する形へ計測手法を見直す**」と保留されていた論点。Phase 2 ではこの `gh` 残存検出を受け入れ条件 / Medium テストに直接載せるため、**行頭マッチでは不十分**。command substitution（`BODY=$(gh issue view ...)`、`CURRENT_BODY=$(gh issue view ...)`、`gh issue view ... | grep -q ...` 等）も検出する必要がある。

実態確認: `i-pr/SKILL.md:129` および `i-dev-final-check/SKILL.md:220` に `BODY=$(gh issue view ...)` 形式が存在する。行頭マッチ (`^\s*gh `) では取りこぼす。

**正本検出パターン**:

```bash
# 直前文字が word boundary になる位置の `gh (issue|pr|api)` を検出
grep -rE '(^|[]=$({;|&[:space:]])gh (issue|pr|api)\b' .claude/skills/*/SKILL.md
```

文字クラスは `[` / `]` / `=` / `$` / `(` / `{` / `;` / `|` / `&` / 空白類を含める。これにより以下がすべて検出される：

- 行頭の `gh issue view ...`
- `BODY=$(gh issue view ...)`
- `CURRENT_BODY=$(gh issue view ...)`
- `gh issue view ... | grep -q '...'`（pipe）
- `if gh issue view ... ; then`（条件式内）
- `[ -n "$(gh issue view ...)" ]`（test 内）

**verdict 例 / 説明文（prose）への誤 hit 対策**: Phase 2 の Skill 置換では prose 内の `gh` 文字列も置換対象に含める運用にする（`gh` を説明する Skill が無いため、文字列としての `gh` が markdown に残るのは避ける）。誤 hit が問題になるケースは Phase 2 段階では存在しない。

#### 機械置換の手順

各 Skill markdown に以下を順に適用する。1〜3 は機械置換、4〜6 は人手レビュー：

1. **`gh issue` / `gh pr` の置換（command substitution 含む）**: 上記 grep 正規表現で hit する箇所すべてに対し `gh issue` → `kaji issue`、`gh pr` → `kaji pr`、`gh api` → 後述の `kaji pr review-comments` 等に書き換える。`sed -E 's/(^|[]=$({;|&[:space:]])gh (issue|pr) /\1kaji \2 /g'` で機械置換可能（直前文字を保持する後方参照を使用）
2. **`gh pr merge ... --merge` の `--merge` 除去**: 正規表現 `kaji pr merge ([^\s]+) --merge\b` → `kaji pr merge \1`
3. **`gh api .../pulls/N/...` の置換**: 3 パターン（`comments` / `reviews` / `comments/CID/replies`）を `kaji pr review-comments PR_ID --jq '...'` / `kaji pr reviews PR_ID --jq '...'` / `kaji pr reply-to-comment PR_ID --to CID --body '...'` に手動書き換え。API path から PR_ID を抽出する必要があるため機械置換不可
4. **placeholder の網羅リネーム**: 下表の置換マッピングをすべて適用する。順序が重要（`#[issue-number]` を先に処理しないと `[issue-number]` 単独置換に飲まれる）。**branch / worktree の placeholder（`[prefix]` / `[branch-name]` / `[worktree-absolute-path]`）は触らない**（Phase 3 範囲）
5. **frontmatter の `issue_number: int` 型注釈の更新**: 例 `issue_number: int` → `issue_id: str`（`.claude/skills/issue-design/SKILL.md:28` 等）
6. **prose 中の hard-code 整理**: `Issue #123` のような数値入り例示を `Issue [issue_ref]` に置き換える（Skill 共通の表記揺れ吸収）

##### placeholder の網羅検出と置換マッピング

実 Skill から `/usr/bin/grep -rh '#\[issue-number\]\|\[number\]\|issue-\[number\]\|\[prefix\]/\[number\]\|kaji-\[prefix\]-\[number\]\|Closes #\[issue-number\]'` で抽出した実在パターン全件を網羅する。**順序通りに sed/置換を適用すること**：

| # | 検出パターン（regex）| 置換後 | 件数(参考) | 注記 |
|---|---------------------|--------|---------|------|
| 1 | `Closes #\[issue-number\]` | `Closes [issue_ref]` | 数件 | PR body の auto-link。github では `Closes #153` に展開、local では `Closes local-pc1-1`（auto-link しない）。**先に処理しないと #2 に飲まれる** |
| 2 | `#\[issue-number\]` | `[issue_ref]` | 約 30 件 | Skill prose / 表示文言。`#` を `[issue_ref]` に集約 |
| 3 | `Issue #\[issue-number\]` | `Issue [issue_ref]` | 数件 | #2 と重複するが冗長表現として明示 |
| 4 | `\[issue-number\]` | `[issue_id]` | 約 60 件 | コマンド引数 placeholder の主体 |
| 5 | `issue-\[number\]-\*\.md` | `issue-[issue_id]-*.md` | 数件（`i-doc-update` / `i-doc-final-check` 等）| 設計書ファイル名。Phase 2 では裸の数値部分のみ `[issue_id]` 化、provider 別パスは Phase 3 |
| 6 | `\[prefix\]/\[number\]` | `[prefix]/[issue_id]` | 数件（`issue-start` 等）| `[prefix]` は Skill 自前計算を温存 |
| 7 | `kaji-\[prefix\]-\[number\]` | `kaji-[prefix]-[issue_id]` | 数件 | worktree dir 名 |
| 8 | `\[number\]` 単独 | `[issue_id]` | 数件（`i-doc-update` 等の prose）| `[issue-number]` の表記ゆれ |
| 9 | `^[[:space:]]*issue_number:` | `issue_id:` | 数件（frontmatter 型注釈）| `issue_number: int` → `issue_id: str` |

**`Closes #...` の意味の保存**: Phase 2 では Skill markdown 上は `Closes [issue_ref]` に統一する。runtime 展開時に `[issue_ref]` が github では `#153`、local では `local-pc1-1` に置換されるため、github mode では従来どおり GitHub auto-link が機能する。local mode で auto-link が機能しない件は本 Phase の関心外（local では PR 自体が無いため `Closes` 文字列は表示文言にすぎない）。

### Python 実装側の変更

#### `kaji_harness/cli_main.py`

- `kaji pr review-comments` / `kaji pr reviews` / `kaji pr reply-to-comment` の 3 サブコマンドを追加（前述「CLI 契約」参照）
- 既存 `kaji pr` の REMAINDER ベース transparent passthrough と排他的に動作させる。実装方針: `pr` の REMAINDER token の先頭が `review-comments` / `reviews` / `reply-to-comment` のいずれかなら専用ハンドラに振り分け、それ以外は Phase 1 の `_forward_to_gh("pr", args)` に委譲する
- `_detect_repo()` ヘルパーを追加（`gh repo view --json nameWithOwner -q .nameWithOwner` を `subprocess.run` で呼び、stdout を返す。失敗時は None）

#### `kaji_harness/prompt.py`

- `issue_number` キーの注入を削除する（`issue_id` / `issue_ref` の 2 変数のみ残す）
- 上記以外の変更は行わない（`branch_*` / `design_path` は Phase 3 以降）

#### テスト fixture の str 化

Phase 1 報告 5.1 で「(B) 境界正規化」を採用したため、既存テストは int 渡しのまま緑。Phase 2 では：

- `tests/test_runner_before.py` / `tests/test_state_persistence.py` / `tests/test_prompt_builder.py` 等の `issue=42` を `issue="42"` に書き換える（`tests/test_runner.py` は実在しない。実ファイル名は `test_runner_before.py`）
- `WorkflowRunner.__post_init__` / `SessionState.__post_init__` の `str(...)` 正規化は **撤去しない**（外部 API としての int 受理は親設計の後方互換要件）。test fixture 側だけ綺麗にする
- `tests/test_prompt_builder.py::test_prompt_emits_both_issue_number_alias_and_issue_id` を **`test_prompt_emits_only_issue_id_and_issue_ref`** に rename し、`issue_number` キーが注入辞書に存在しないことを assert する内容に書き換える

### Workflow YAML との関係

Phase 2 では **既存 `.kaji/wf/feature-development.yaml` 等は変更しない**。step ごとに渡される引数は Skill 側で参照するだけなので、Skill markdown の placeholder 改修だけで済む。新規 `feature-development-local.yaml` は Phase 4 スコープ。

## テスト戦略

`docs/dev/testing-convention.md` および親設計「テスト戦略」章に従う。Phase 2 では Small / Medium で完結し、Large（実 subprocess）は Phase 3 以降で扱う。

### Small（mock 完結）

| 対象 | テストケース |
|------|-------------|
| `prompt.py` の注入辞書 | Phase 2 完了時点で `issue_number` キー **不在**、`issue_id` / `issue_ref` のみ存在。数値 ID `"153"` で `issue_ref="#153"`、非数値 `"local-pc1-1"` で `issue_ref="local-pc1-1"`（`#` なし）|
| `_compose_json_and_jq()` | `fields=["title","body"], jq=None` → `[.[] \| {title: .title, body: .body}]` / `fields=None, jq=".[]"` → `.[]` / 両方 → `[.[] \| {...}] \| <jq>` / 両 None → None |
| `kaji pr review-comments` の `subprocess.run` 引数組み立て | `gh repo view` mock で repo を返し、生成 argv が `["gh", "api", "repos/<repo>/pulls/153/comments", "--jq", "..."]` と一致 |
| `kaji pr review-comments` の異常系 | PR_ID が非数値 → exit 2 / `gh` 不在 → exit 3 / `gh repo view` 失敗 → exit 3 |
| `kaji pr reply-to-comment` の argv | `--method POST` および `-f body=<body>` が argv に含まれる |
| `kaji pr merge X --squash --rebase` | `--squash` / `--rebase` が剥がれ、`--merge` が末尾に 1 つだけ付く（Phase 1 既存テストの維持確認）|

### Medium（実 file system / 実 subprocess wrapper / `CliRunner`）

| 対象 | テストケース |
|------|-------------|
| Skill markdown の `gh` 残存検証 | `grep -rE '(^\|[]=$({;\|&[:space:]])gh (issue\|pr\|api)\b' .claude/skills/*/SKILL.md` の hit 数が **0**（command substitution / pipe を含めた網羅検出）|
| Skill markdown の placeholder 残存検証 | `grep -rE '\[issue-number\]\|\[number\]\|#\[issue-number\]\|issue-\[number\]\|\[prefix\]/\[number\]\|kaji-\[prefix\]-\[number\]\|^[[:space:]]*issue_number:' .claude/skills/*/SKILL.md` の hit 数が **0**（frontmatter 型注釈・表記ゆれを網羅）|
| Skill markdown の `Issue #` hard-code 検証 | `grep -rE 'Issue #\[issue\|Closes #\[issue' .claude/skills/*/SKILL.md` の hit 数が **0** |
| Skill markdown link checker | `make verify-docs` 相当が緑（既存ルール準拠）|
| `kaji pr review-comments` の CliRunner 経由 invoke | `gh` を mock した上で `kaji pr review-comments 153 --json id,body --jq '.[]'` が `gh api ... --jq '[.[] \| {id: .id, body: .body}] \| .[]'` を起動 |

### Large

Phase 2 では Large テスト（実 subprocess + 実外部通信）は **追加しない**。

理由: 親設計のテスト戦略（design.md `testing-size-guide.md:28` 引用）に従い、subprocess での実 CLI 起動および外部 API 実通信は Large に分類される。Phase 2 の検証対象は Skill markdown の改修と CLI ハンドラの単体動作であり、Medium（CliRunner + gh mock）で十分に保証可能。実 GitHub API 疎通テストは Phase 3 以降の LocalProvider / sync 実装と合わせて Large テストとして整備する。

**E2E 動作確認は GitHub に依存しない wrapper 契約検証で代替する**: 当初は `provider=github` での手動 smoke（5 経路）を要求していたが、本プロジェクトは GitHub アカウント停止時の継続性を目的とした local-mode 整備であり、GitHub 復旧を待って smoke を回す前提は本末転倒である（design.md「停止時の継続性」§ 参照）。代替として以下の Medium テストで wrapper 契約と Skill 改修の正当性を保証する：

- **Skill 静的検証**（grep）: `gh` 残存ゼロ / placeholder 残存ゼロ / `Issue #` hard-code ゼロ / `--merge` flag ゼロ
- **wrapper pass-through 検証**（CliRunner + subprocess mock）: `kaji issue view N --json body -q '.body'` の stdout pass-through、`--body-file -` での stdin pass-through、heredoc ラウンドトリップの bit-exact 維持
- **`kaji pr review-comments` 等の合成 jq 検証**（既存 Phase 2-A Small テストで網羅済）
- **`kaji pr merge` の method flag 強制**（既存 Phase 1 テストで網羅済）

実 `gh` 固有のバグ（quoting / TTY / 環境変数依存）は本 Phase ではカバーしない既知ギャップとして受け入れ、Phase 3 完了時の `provider=local` での `feature-development-local.yaml` 完走を end-to-end 検証の正本とする。Phase 2 単独で end-to-end 検証は原理的に不可能と割り切る。

### 既存 Phase 1 テストの維持

- `tests/test_cli_main.py::TestIssuePrPassthrough` の 5 ケースが緑のまま（Phase 1 passthrough は Phase 2 でも基本契約）
- `tests/test_prompt_builder.py::test_prompt_emits_both_issue_number_alias_and_issue_id` を `test_prompt_emits_only_issue_id_and_issue_ref` に **rename + 内容書き換え**（alias 不在の検証へ）

### CI 統合

- `make check` が緑（既存ターゲット）
- 新規 Medium テスト 4 件（`grep` ベースの Skill 検証 3 件 + CliRunner 1 件）を `make test-medium` に乗せる
- Large テストは Phase 3 以降

## 受け入れ条件

### 機械検証可能

- [ ] **`gh` 残存検出（command substitution 含む）**: `grep -rE '(^|[]=$({;|&[:space:]])gh (issue|pr|api)\b' .claude/skills/*/SKILL.md` が **0 hit**（word boundary を含む正規表現で `BODY=$(gh issue view ...)` 等も検出）。**実行可能性確認済**（`/usr/bin/grep -rE` で `i-pr/SKILL.md:129` の `CURRENT_BODY=$(gh issue view ...)` 等が hit することを設計時に検証）
- [ ] **placeholder 残存検出（網羅版）**: `grep -rE '\[issue-number\]|\[number\]|#\[issue-number\]|issue-\[number\]|\[prefix\]/\[number\]|kaji-\[prefix\]-\[number\]|^[[:space:]]*issue_number:' .claude/skills/*/SKILL.md` が **0 hit**。`[issue-number]` 単独だけでなく `#[issue-number]` 30 件、`[number]` 単独、`issue-[number]-*.md`、`[prefix]/[number]`、`kaji-[prefix]-[number]`、frontmatter 型注釈をすべて網羅する
- [ ] **`Issue #[issue-number]` / `Closes #[issue-number]` hard-code 検出**: `grep -rE 'Issue #\[issue|Closes #\[issue' .claude/skills/*/SKILL.md` が **0 hit**
- [ ] **`--merge` flag 検出**: `grep -rE 'kaji pr merge .* --merge' .claude/skills/*/SKILL.md` が **0 hit**
- [ ] **CLI 到達性**: `kaji pr review-comments PR_ID --json F --jq E` / `kaji pr reviews PR_ID --json F --jq E` / `kaji pr reply-to-comment PR_ID --to CID --body B` が `kaji pr review-comments --help` 等で usage を表示する（前述 argparse 実装方針の `add_help=True` 効果）
- [ ] **CLI 引数挙動**: `kaji pr review-comments` 引数不足時に exit 2 で停止 / `kaji pr review-comments abc`（非数値 PR_ID）で `EXIT_INVALID_INPUT=2` で停止
- [ ] **CLI 既存互換**: `kaji pr view 153 --comments` 等の既存 passthrough は Phase 1 と同じ `_forward_to_gh` 経由で動作（builtin sub に該当しない token は素通り）
- [ ] **`--json` / `--jq` 合成**: `kaji pr review-comments 153 --json id,body --jq '.[]'` が内部で `gh api ... --jq '[.[] | {id: .id, body: .body}] | .[]'` を組み立てる（`_compose_json_and_jq()` の合成形）
- [ ] **`EXIT_INVALID_INPUT=2` 追加**: `kaji_harness/cli_main.py:29-34` の `EXIT_*` セクションに `EXIT_INVALID_INPUT = 2` が追加されている
- [ ] `prompt.py` の注入辞書から `issue_number` キーが削除され、`issue_id` / `issue_ref` の 2 変数のみが残る（`branch_*` / `design_path` / `issue_input` は Phase 3 以降のため Phase 2 では追加しない）
- [ ] 既存 Phase 1 テスト（`TestIssuePrPassthrough` 5 件）が緑のまま
- [ ] Phase 2 で追加した Small / Medium テストが緑
- [ ] `make check` 全体が緑（ruff / mypy / pytest）
- [ ] **`make verify-docs` 緑**（Markdown link checker — `make check` には含まれないため独立した条件として明記）
- [ ] Phase 1 の `__post_init__` 境界正規化が維持されており、CLI が int 風文字列 `"42"` を受け付ける後方互換が壊れていない

### 手動確認

- [ ] **wrapper pass-through 検証（Medium テスト）**: 以下を CliRunner + `subprocess.run` mock で機械検証する（実 `gh` 通信なし、CI 自動化）
  - `kaji issue view N --json body -q '.body'` → mock の stdout が呼び出し側に inherit される（bit-exact）
  - `kaji issue comment N --body-file -` → stdin が `gh issue comment` に pass-through される
  - `kaji issue edit N --body "$CURRENT_BODY"` ラウンドトリップ → `view` の出力を `edit` へ書き戻したときに mock 引数列が body 改変なく一致
  - `Closes [issue_ref]` 展開後の文字列が `kaji pr create --body` に正しく渡る
- [ ] **既存 worktree 解決の維持**: `[issue-number]` → `[issue_id]` リネーム後も、`docs/247` / `fix/123` 等 prefix を伴う既存 Skill フローが既存挙動どおり worktree 解決できる（`[prefix]` を Skill 自前計算する経路が壊れていないこと）— Skill markdown の grep / 目視確認で担保
- [ ] **end-to-end smoke は Phase 3 に持ち越し**: 実 `gh` 経由の `feature-development.yaml` 完走 / `provider=local` 経由の `feature-development-local.yaml` 完走は Phase 3 完了時に実施する。Phase 2 では原理的に GitHub 非依存の検証しかできないことを既知ギャップとして許容する

### ドキュメント更新

- [ ] `docs/dev/skill-authoring.md` の prompt 注入変数表が更新され、`issue_number` 削除と `issue_id` / `issue_ref` の 2 変数化が明記されている
- [ ] `docs/dev/shared_skill_rules.md` の placeholder 表記規約が `[issue_id]` / `[issue_ref]` ベースに更新されている
- [ ] `docs/ARCHITECTURE.md` の `issue_number` 言及が `issue_id` に更新されている
- [ ] `docs/dev/development_workflow.md` / `docs/dev/docs_maintenance_workflow.md` 内の `issue_number` / `gh pr create` / `gh issue ...` 言及が `issue_id` / `kaji pr` / `kaji issue` に更新されている
- [ ] CHANGELOG / release notes に以下を明記:
  - Skill placeholder `[issue-number]` 等を `[issue_id]` / `[issue_ref]` にリネーム（網羅検出表参照）
  - `prompt.py` 注入辞書から `issue_number` を削除
  - Skill が `kaji issue` / `kaji pr` 経由に統一
  - `kaji pr review-comments` / `reviews` / `reply-to-comment` 新規追加
  - `EXIT_INVALID_INPUT=2` 追加

## 段階リリース戦略

main を壊さない原則（CLAUDE.md「Never commit to main directly」「Pre-Commit (REQUIRED)」）と本設計の受け入れ条件「`feature-development.yaml` を `provider=github` で完走できる」を満たすため、**「先に CLI 側を整える PR → 次に Skill を切り替える PR」の順序**で 2 段階リリースする。中間状態（main に merge された時点）でも既存 workflow が壊れない順序を厳守する。

### PR 2-A: CLI 側準備（Skill が依存する新コマンドを先に main に投入）

このステップでは Skill markdown を変更しないため、既存 workflow は引き続き `gh` 直叩きで動作する。main は壊れない：

- `kaji_harness/cli_main.py` に `kaji pr review-comments` / `kaji pr reviews` / `kaji pr reply-to-comment` の 3 サブコマンド追加（前述「CLI 契約」）
- `kaji_harness/prompt.py` の Phase 1 alias（`issue_number`）は **このステップでは温存**（Skill が `[issue-number]` を参照中のため）
- 新規 Small テスト（`_compose_json_and_jq` / 各サブコマンドの argv 組み立て / 異常系）追加
- `make check` 緑

PR 2-A merge 後の状態:

- 既存 Skill は `gh` 直叩きのまま動作（Phase 1 と同じ）
- 新コマンド `kaji pr review-comments` 等は実装済（Skill からはまだ呼ばれない）
- `feature-development.yaml` の `provider=github` 完走は維持

### PR 2-B: Skill 切り替え + alias 撤去（Skill と Python 側を atomic に切り替える）

PR 2-A が main に merge されたあと、以下を **単一 PR** で実施する。Skill 改修と `issue_number` alias 撤去は同 PR 内で揃えないと中間状態で壊れるため、分割しない：

- 全 23 Skill の `gh` → `kaji` 機械置換（command substitution 含む、前述 grep 仕様で残存ゼロ確認）
- `gh pr merge --merge` の `--merge` 除去
- `gh api ...` → `kaji pr review-comments` / `reviews` / `reply-to-comment` への手動書き換え
- placeholder リネーム（`[issue-number]` → `[issue_id]`、`Issue #[issue-number]` → `Issue [issue_ref]`、`issue_number:` → `issue_id:`）
- `kaji_harness/prompt.py` から `issue_number` キー注入を削除
- `tests/*` の int → str 化、`test_prompt_emits_both_issue_number_alias_and_issue_id` の rename + 内容書き換え
- Medium テスト（grep ベース Skill 検証 3 件 + CliRunner 1 件）追加
- `docs/dev/skill-authoring.md` の注入変数表更新
- `make check` 緑、`make verify-docs` 緑、Medium pass-through テスト（受け入れ条件「wrapper pass-through 検証」参照）緑

PR 2-B merge 後の状態:

- 全 Skill が `kaji` 経由
- `prompt.py` の注入辞書は `issue_id` / `issue_ref` の 2 変数のみ
- 既存 workflow は引き続き完走

### なぜ「Skill 先行」を採らないか

Skill を先に切り替えると、CLI 未実装の `kaji pr review-comments` 等を呼ぶ Skill が main に入り、その時点で `feature-development.yaml` の `provider=github` 完走が壊れる。これは **CLAUDE.md「Pre-Commit (REQUIRED)」原則および本設計の受け入れ条件に違反する**。「同日中に後続 PR を merge する」では品質保証にならない（CI 失敗、他開発者の中間 pull で破損、reverts 不能）。よって CLI 整備が先、Skill 切り替えが後。

### なぜ PR 2-B を分割しないか

PR 2-B 内で「Skill 改修先行 → `issue_number` 削除後行」とすると、Skill が `[issue_id]` を参照する状態で `prompt.py` がまだ `issue_number` を出している中間状態が許容できそうに見える。しかしその逆（`prompt.py` から `issue_number` 削除を Skill 改修より先に出す）は Skill が `[issue-number]` を参照中なのに変数が無い状態を作るため壊れる。両方向に安全な分割切り口が無く、Skill 改修と alias 撤去は atomic で main に入れる必要がある。

## リスク

| リスク | 影響 | 緩和策 |
|--------|-----|--------|
| Skill 機械置換で文脈依存の hit を巻き込む | `gh` という単語が prose 内に出てきた箇所を誤って書き換える | 検出には本文「`gh` 残存検出の grep 仕様」と同じ `grep -rE '(^\|[]=$({;|&[:space:]])gh (issue\|pr\|api)\b'` を使用し、command substitution / pipe / 条件式内の呼び出しまで網羅的に拾う。置換後 hit 数 0 を確認後、`make verify-docs` で link 切れも確認 |
| `kaji pr review-comments` の repo 検出失敗 | `gh repo view` が失敗する環境（detached HEAD 等）でエラー | Phase 1 と同じ `EXIT_RUNTIME_ERROR=3` で fail-fast。エラーメッセージに「`gh repo view --json nameWithOwner` が成功する状態で実行してください」とガイド追加 |
| `issue_number` alias 撤去で外部 Skill / 自作 workflow が破損 | 利用者が独自に `issue_number` を参照している可能性 | 現状利用者は apokamo 1 名のため影響範囲は限定的。`prompt.py` の alias 削除を CHANGELOG に明記し、`issue_id` への移行ガイドを示す |
| Phase 2 PR 分割期間中の Skill 実行 | 中間 main で workflow が壊れる | PR 順序を「CLI 整備 (2-A) → Skill 切替 (2-B)」に固定。各 PR 単独で main に入っても `feature-development.yaml` が完走することを CI で検証する |
| placeholder 表記ゆれの取りこぼし | `<issue-number>` `<issue>` `[issue]` 等の半端な表記が残る | 正規表現を多パターンで grep し、Skill ごとに目視確認。Medium テストで残存ゼロを継続監視 |

## 工数再見積

親設計の Phase 2 見積は **3 日**。本設計書を踏まえた内訳：

| 作業 | PR | 見積 |
|------|----|----|
| Skill markdown 機械置換（23 件、`gh` 置換 + `--merge` 除去 + `gh api` 置換、command substitution 含む）| 2-B | 0.5 日 |
| placeholder リネーム（`[issue-number]` → `[issue_id]`、`Issue #` → `Issue [issue_ref]`、frontmatter 含む 23 件）| 2-B | 0.5 日 |
| `kaji_harness/cli_main.py` に 3 サブコマンド追加 + Small テスト（argv 組み立て / 異常系）| 2-A | 0.5 日 |
| `kaji_harness/prompt.py` から `issue_number` 削除 + テスト rename・書き換え | 2-B | 0.25 日 |
| テスト fixture の int → str 化（既存 fixture 多数）| 2-B | 0.5 日 |
| Medium テスト（grep ベース Skill 検証 3 件 + CliRunner 1 件）追加 | 2-B | 0.25 日 |
| 影響ドキュメント更新（`skill-authoring.md` / `shared_skill_rules.md` / `ARCHITECTURE.md` / `development_workflow.md` / `docs_maintenance_workflow.md`）+ CHANGELOG 記載 | 2-B | 0.5 日 |
| Medium pass-through テスト（CliRunner + subprocess mock、4 シナリオ）| 2-B | 0.5 日 |
| **合計** | | **3.5 日** |

親設計の見積（3 日）から 0.5 日増えるが、内訳は影響ドキュメント拡張（5 ファイル）と smoke 経路拡張（5 経路）の純増分。Phase 2 着手時に親設計の見積も同時更新する。

親設計の見積と整合。Phase 1 で `__post_init__` 境界正規化を入れた分、Phase 2 のテスト fixture 修正は「機械的だが量がある」作業として 0.5 日を確保。

## オープンな論点

- **Phase 3 へ持ち越し**: `branch_prefix` / `branch_name` / `worktree_dir` / `design_path` / `issue_input` の正本をどこに置くか（workflow YAML 拡張 / Issue NOTE 読み出し / Skill 内計算継続のいずれか）。Phase 2 では Skill 内計算を温存し、placeholder リネームのみ実施
- `kaji pr review-comments` の repo 検出を `gh repo view` に依存するか、`git remote get-url` ベースに変更するか。Phase 3 で provider 抽象を入れる際の整合性に影響
- Phase 2 完了時点で「LocalProvider 未実装でも `kaji pr review-comments` を呼ぶ可能性」をどう扱うか。Phase 4 で bare provider エラーを実装する前段として、Phase 2 で警告ログを出すか
- 手動 smoke の代替: 実 GitHub Issue を使わずに `feature-development.yaml` の workflow simulation（CliRunner で 1 step 起動 + `gh` mock）を Medium テストとして組めるか。Phase 2 段階で機械化できると Phase 3 以降の回帰検出が容易になる
