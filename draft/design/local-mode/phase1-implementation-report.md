# [実装報告] local mode — Phase 1

- **対応 Issue**: TBD（GitHub アカウント停止中。復旧後に起票して紐付ける）
- **対応 design**: `draft/design/local-mode/design.md`
- **対応 Phase**: Phase 1 — `kaji issue` / `kaji pr` CLI 追加（中身は `gh` 呼び出し）+ `kaji run` の issue 型 str 化
- **ブランチ**: `feat/local-mode-phase1`
- **作業日**: 2026-05-05

## 1. スコープと完了基準（design からの引用）

design.md「工数見積」表より:

> | Phase | 内容 | 見積 |
> |-------|------|------|
> | 1 | `kaji issue/pr` CLI 追加（中身は gh 呼び出し）+ `kaji run` の issue 型 str 化（state.py / prompt.py / logger.py 含む） | 1.5 日 |

design.md「kaji run の issue パラメータ型変更」より:

> これらの修正は **Phase 1（CLI 追加）と Phase 2（Skill 置換）の境界で集中的に実施**する。`SessionState` の型変更は破壊的なので、Phase 1 で完了させる方針。

design.md「既存 GitHub 運用への影響」より:

> 移行は段階実装可能：
> 1. Phase 1: `kaji issue` / `kaji pr` CLI を追加し、内部実装は `gh` 直呼び出し（GitHub provider のみ）
> 2. Phase 2: 全 Skill の `gh` を `kaji` に置換
> ...

→ Phase 1 では provider 抽象（`kaji_harness/providers/`）は導入しない。`kaji issue ...` / `kaji pr ...` は user の引数を `gh` にそのまま転送する薄い wrapper として実装する。例外は `kaji pr merge` のみ（後述）。

## 2. 実装内容

### 2.1 `kaji run` の issue パラメータ型変更（int → str）

design.md「影響を受ける実装ファイル」表に対応：

| ファイル | 変更内容 |
|---------|---------|
| `kaji_harness/cli_main.py` | `add_argument("issue", ..., type=int)` → `type=str`。help 文言も `"GitHub Issue number"` → `"Issue ID (GitHub number like '153' or local form like 'local-pc1-1')"` に更新。完了表示は新規 helper `_format_issue_ref` 経由で `#153` / `local-pc1-1` を出し分け。 |
| `kaji_harness/state.py` | `SessionState.issue_number: int` → `str`。`load_or_create(issue: str, ...)`。旧 cache 互換のため、JSON に int で保存されていた `issue_number` も str 化して読み込む。`_state_dir` の `str(self.issue_number)` を撤去。`_write_progress_md` は `_format_issue_ref` を使い、`local-pc1-1` には `#` を付けない。`__post_init__` で int / 任意型を str へ正規化（既存呼び出しテスト互換）。 |
| `kaji_harness/prompt.py` | `build_prompt(step, issue: int, ...)` → `issue: str`（受け側は `str(issue)` で正規化し、`build_prompt` 単体の契約も `issue_id: str` と一貫させる）。プロンプト内変数を `issue_id` / `issue_ref` で追加（design.md「provider 中立コンテキスト変数」表）。`issue_number` は **Phase 1 後方互換 alias として維持**：既存 Skill (`.claude/skills/issue-design/SKILL.md:28` 等) が「常に注入される変数」として参照しており、provider 中立変数への全 Skill 移行は design 上 Phase 2 のスコープ。Phase 2 完了時に削除する。プロンプト本文の `GitHub Issue #{issue}` は `Issue {issue_ref}` に変更（local では `#` なし）。 |
| `kaji_harness/logger.py` | `log_workflow_start(self, issue: int, ...)` → `issue: str`。 |
| `kaji_harness/runner.py` | `WorkflowRunner.issue_number: int` → `str`。`__post_init__` で str 正規化。`artifacts_dir / str(self.issue_number)` を `artifacts_dir / self.issue_number` へ簡略化。 |

新規 helper `kaji_harness.state._format_issue_ref(issue)`:

- 数値のみ → `#153`
- それ以外（`local-pc1-1` 等）→ そのまま
- design.md「設計判断のサマリ」の「`Issue ID — local-<machine>-<int> 形式、CLI 入力では <int> 単独も省略可`」「frontmatter 表記は string 配列で表現するが、`kaji issue view --json` で出力する際は GitHub API スキーマに揃え…」の精神を踏まえ、Phase 1 段階では「数値のみは GitHub Issue とみなして `#` を補う」というシンプルなフォーマット規則のみを実装する。Phase 3 で provider 抽象が入ったあと、provider 由来の本格的な `issue_ref` 整形に置換する想定。

#### 後方互換維持の仕組み

- `WorkflowRunner.__post_init__` / `SessionState.__post_init__` で `self.issue_number = str(self.issue_number)` を行う。これにより、既存テストや既存 cache が `issue_number` を int で渡してきても破綻しない。
- design.md「後方互換: 既存の `kaji run wf.yaml 153` 呼び出しはそのまま動作する」と整合。

### 2.2 `kaji issue` / `kaji pr` CLI 追加（gh passthrough wrapper）

`kaji_harness/cli_main.py` に subparser を 2 つ追加：

```python
def _register_issue(...):
    p = subparsers.add_parser(
        "issue",
        help="Issue operations (Phase 1: gh issue passthrough)",
        add_help=False,
    )
    p.add_argument("args", nargs=argparse.REMAINDER, ...)


def _register_pr(...):
    p = subparsers.add_parser(
        "pr",
        help="Pull request operations (Phase 1: gh pr passthrough)",
        add_help=False,
    )
    p.add_argument("args", nargs=argparse.REMAINDER, ...)
```

`add_help=False` は user が `kaji issue --help` を打った場合に、そのまま `gh issue --help` の出力が見えるようにするためのもの（自前 help が干渉しない）。

転送ロジックは `_forward_to_gh(group, raw_args)`：

- `shutil.which("gh")` で `gh` 不在を検出し、ガイダンス付きで非ゼロ終了（`EXIT_RUNTIME_ERROR=3`）
- argparse の REMAINDER が先頭に `--` を残すケースを除去
- **`pr merge` 特殊処理**: `--merge` / `--squash` / `--rebase` を引数列から取り除き、末尾に `--merge` を 1 つだけ追加してから `gh pr merge ...` を呼ぶ。  
  根拠: design.md「`kaji pr merge` は `--method` フラグを露出しない。内部で常に `gh pr merge --merge`（`--no-ff` 相当）固定で実行する。squash / rebase は kaji の merge 規約 (`docs/guides/git-commit-flow.md`) に反するため CLI 上に出さない」。
- `subprocess.run(cmd, check=False)` の終了コードをそのまま返す

#### Phase 1 で意図的に未実装のもの

- `kaji issue list` `view` 等の独自パース・JSON 整形ロジック（Phase 3 で `LocalProvider` 実装時にまとめて行う想定。Phase 1〜2 は `gh` 直叩きで動作不変）
- `kaji sync ...` 系コマンド（Phase 5 スコープ）
- `kaji config set` 系コマンド（Phase 3 スコープ）
- `provider` 抽象層 (`kaji_harness/providers/`)（Phase 3 スコープ）
- ID 正規化 (`normalize_id`) や `local-<machine>-<n>` 形式の解釈（Phase 3 スコープ）

design.md「Phase 1-2 の暫定動作」と整合：

> `provider` 抽象が未導入の Phase 1（…）と Phase 2（…）では、`kaji issue` / `kaji pr` は **provider config なしでも `gh` 互換ラッパーとして動作**する（既存挙動の維持）。

## 3. テスト

### 3.1 既存テスト

issue 型 str 化に伴って 6 件の既存アサーションを修正：

| ファイル | 変更 |
|---------|------|
| `tests/test_cli_main.py:81` | `args.issue == 42` → `"42"` |
| `tests/test_session_state.py:50` | `state.issue_number == 99` → `"99"` |
| `tests/test_state_persistence.py:33,122,195` | int → str 比較に修正 |
| `tests/test_config.py:408` | 同上 |

`prompt_builder` / `runner` 系のテストはコンストラクタや `build_prompt(..., issue=42, ...)` を int で渡したままだが、`__post_init__` の str 正規化と `_format_issue_ref` が int を str() するため、修正不要で緑。これは設計上の意図（境界での吸収）。

### 3.2 新規テスト

`tests/test_prompt_builder.py::TestPromptContainsIssueNumber` に Small テスト 2 件を追加：

- `test_prompt_emits_both_issue_number_alias_and_issue_id` — `- issue_number: 123` / `- issue_id: 123` / `- issue_ref: #123` の 3 行が同時に出ることを明示検証（後方互換 alias の維持を契約として固定）
- `test_prompt_local_id_uses_bare_ref_without_hash` — `local-pc1-1` 入力で `issue_ref` に `#` が付かないことを検証

`tests/test_cli_main.py::TestIssuePrPassthrough` 配下に Small テスト 5 件を追加：

1. `test_run_issue_accepts_local_id` — `kaji run wf.yaml local-pc1-1` の parse が string を保つ
2. `test_issue_subcommand_forwards_to_gh` — `kaji issue view 153 --json title` の REMAINDER が user 入力を保つ
3. `test_pr_subcommand_forwards_to_gh` — `kaji pr create --base main --title x` の引数転送
4. `test_pr_merge_strips_method_flags_and_forces_no_ff` — `pr merge feat/153 --squash` を呼ぶと `--squash` が除去され、末尾に `--merge` が 1 つだけ付くことを mock で検証
5. `test_forward_returns_error_when_gh_missing` — `gh` 不在時に非ゼロ終了

### 3.3 結果

```
$ make check
... ruff check (pass)
... ruff format (pass)
... mypy (Success: no issues found in 14 source files)
... pytest (682 passed, 1 skipped in 60.89s)
```

## 4. design 受け入れ条件への対応状況

design.md「受け入れ条件」のうち、Phase 1 で達成されたもの:

- [x] `kaji issue` / `kaji pr` CLI が `kaji --help` で確認できる（Phase 1 範囲。中身は gh passthrough）
- [x] `kaji run` の `issue` パラメータが str 受理に変更され、既存テストが更新後 緑になっている
- [x] `kaji pr merge` が `--method` フラグを露出せず、内部で常に `--no-ff` 相当の merge を実行する（`gh pr merge --merge` 固定）
- [x] 既存の `make check` が通る

Phase 2 以降に持ち越しのもの（design の Phase 計画通り）:

- [ ] provider=local での E2E（Phase 3-4）
- [ ] LocalProvider のテスト（Phase 3）
- [ ] `kaji sync from-github` / `local-to-github-plan`（Phase 5）
- [ ] Skill の `gh` → `kaji` 置換（Phase 2）
- [ ] provider 中立コンテキスト変数の Skill 全体への展開（Phase 2）
- [ ] `.gitignore` への `.kaji/config.local.toml` 追加（Phase 3 で `LocalProvider` 実装に合わせて投入）
- [ ] config の fail-fast 化（Phase 3）
- [ ] `kaji issue --json` / `--jq` の自前実装（Phase 3。Phase 1-2 は gh 直叩きで動作）

## 5. 非自明な設計判断（実装時に行ったもの）

### 5.1 `__post_init__` で int → str 正規化を行う方針

design.md は「`SessionState.issue_number: int` → `str`」と明記しているが、既存テスト（`prompt_builder`, `runner_before`, `workflow_execution`, `verdict_integration` 等 多数）が `issue=42`（int）を keyword で `WorkflowRunner` / `SessionState` に渡していた。

選択肢:

- (A) 全テストを `issue="42"` 化する（数十箇所）
- (B) `__post_init__` で境界正規化する

(B) を採用した理由:

- design.md「後方互換: 既存の `kaji run wf.yaml 153` 呼び出しはそのまま動作する」の精神は、**外部 API に対する後方互換だけでなく、Python レベルのコンストラクタ呼び出し互換にも妥当**。境界で str 化すれば内部実装は完全に str ベースになり、design 意図は損なわれない。
- (A) は Phase 1 の目的（CLI 拡張）から外れた巨大 diff になり、レビュー効率と review-ready 性を落とす。Phase 2 の Skill 置換時に `[issue-number]` placeholder ごと整理する文脈で、テスト fixture も str ベースに書き直すのが筋。

### 5.2 `add_help=False` を `kaji issue` / `kaji pr` の subparser に付ける

argparse の `add_help=True` だと `kaji issue --help` で kaji 自身の generic help が出てしまい、user が期待する `gh issue --help` の出力が得られない。Phase 1 wrapper は **完全に gh の薄い前置層**であるべきなので、`-h` / `--help` を REMAINDER に流して `gh` に処理させる。

### 5.3 `_format_issue_ref` を `state.py` に置く

provider 抽象が未導入の Phase 1 では、`#153` / `local-pc1-1` の出し分けロジックを置く自然な場所がない。Phase 3 で `kaji_harness/providers/` ができたら、そちら側に移管する想定。Phase 1 では `state.py` に内部 helper として置き、`prompt.py` / `cli_main.py` から import する。

## 6. 残課題・次 Phase への引き継ぎ

- **Phase 2 着手時**: `[issue-number]` placeholder の全 Skill 一括置換と、prompt の `issue_number` キー消失に追随する Skill 側のテンプレート修正が必要。design.md「Phase 2 で更新される全 Skill 範囲」の手順に従う。
- **Phase 3 着手時**: `_format_issue_ref` を `kaji_harness/providers/` 配下に移管し、provider 別整形（`#153` vs `local-pc1-1`）の正本ロジックにする。`__post_init__` 経由の int 受理は維持しても良いが、tests が str ベースに揃ったタイミングで撤去候補。
- design.md オープン論点との接続: Phase 1 では `--jq` の `gh` 互換実装は **gh 直叩きでそのまま正しい**ため未対応。Phase 3 で LocalProvider に切り替わると初めて Python `jq` library が必要になる（`pyproject.toml` への `jq>=1.6` 依存追加もそのタイミング）。

## 7. 変更ファイル一覧

```
 kaji_harness/cli_main.py          |  87 +++++++++++- (issue/pr subparser, _forward_to_gh, _format_issue_ref 経由の表示)
 kaji_harness/logger.py            |   2 +-          (issue: int → str)
 kaji_harness/prompt.py            |  12 +-          (issue: str, issue_ref 変数化)
 kaji_harness/runner.py            |  11 +-          (issue_number: str + __post_init__)
 kaji_harness/state.py             |  30 +++-        (SessionState.issue_number: str, _format_issue_ref helper)
 tests/test_cli_main.py            |  53 ++++++-     (TestIssuePrPassthrough 追加 + str 期待値修正)
 tests/test_config.py              |   2 +-          (str 期待値)
 tests/test_session_state.py       |   2 +-          (str 期待値)
 tests/test_state_persistence.py   |   6 +-          (str 期待値)
```

`draft/design/local-mode/design.md` の差分は本 Phase の作業ではなく、レビュー収束過程での design 加筆（既に working tree 上で進行していたもの）。Phase 1 実装と同じブランチに乗せて運ぶ。
