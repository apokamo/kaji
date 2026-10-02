# [設計] KAJI_TMP_DIR と標準一時ディレクトリ環境変数を設定する

Issue: #407

## 概要

kaji が workflow の各 attempt の dispatch 前に、project root 直下の `tmp/` 配下へ attempt 固有の
一時作業ディレクトリを作成し、その正規化済み絶対パスを `KAJI_TMP_DIR` / `TMPDIR` / `TMP` / `TEMP`
の 4 変数として全 dispatch 経路（headless agent / interactive terminal（tmux・Herdr）/ `exec` /
`exec_script`）の起動プロセスへ渡す。

## 背景・目的

### 現状

- リポジトリには作業用の `tmp/` があり、`.gitignore:64-66` で「作業ローカルの一時ファイルを
  ここに置く。コミット禁止」と定義されている。しかし workflow step へその場所を伝える共通の
  環境変数はない。
- `kaji_harness/runner.py` の `_StepExecutor._build_context_env()` は `KAJI_*` を
  script-like step（`exec` / `exec_script`）にだけ渡し、一時ディレクトリ系の変数は持たない。
  agent 経路（`execute_cli` / `execute_interactive_terminal`）には env を一切渡していない。
- Issue #406 の調査（https://github.com/apokamo/kaji/issues/406#issuecomment-5380701055 ）では、
  agent が WSL の `/tmp` 配下に独自の scratch directory を作っていた。

### ユースケース

- **workflow step 作成者**として、ツールごとに個別設定を足さずに、`tempfile` / `mktemp` /
  Node `os.tmpdir()` 等の標準的な一時ファイル生成が attempt 固有のリポジトリ内 `tmp/` を
  使うようにしたい。
- **実行 agent / exec script** として、kaji 固有処理では同じ場所を `KAJI_TMP_DIR` から明示的に
  参照したい（例: `out="$KAJI_TMP_DIR/diff.txt"`）。
- **調査者**として、attempt の一時ファイルを run / step / attempt から辿れる場所で確認したい。

### 代替案と不採用理由

| 案 | 不採用理由 |
|----|-----------|
| `KAJI_TMP_DIR` のみ設定 | 一般ツールは認識しない（Issue 本文「現状の問題」）。人間決定で 4 変数一体と確定済み |
| `tempfile.mkdtemp()` で `tmp/kaji/` 配下にランダム名 | run / step / attempt との対応を辿れない |
| artifacts layout を完全ミラー（`tmp/kaji/<id>/runs/<run>/steps/<step>/attempt-NNN`） | パスが長くなり AF_UNIX socket の `sun_path` 上限（108 byte）に近づく（§ 制約） |
| tmux `split-window -e` / Herdr `--env` で pane env を設定 | backend ごとに別機構になる。wrapper command 前置で両 backend を 1 箇所で賄える |
| wrapper.sh に 10 番目の引数を追加して export | wrapper の 9 引数契約（`_build_wrapper_command` docstring）を変える必要があり、得るものがない |

## インターフェース

### 入力

| 入力 | 型 | 出所 | 説明 |
|------|----|------|------|
| `project_root` | `Path` | `WorkflowRunner.project_root`（`kaji run` では `config.repo_root`） | `.kaji/config.toml` を持つ kaji project root。issue worktree ではない |
| `issue_id` | `str` | `RunIssueContext.canonical_id` | 既に artifacts path の component として使われている値 |
| `run_id` | `str` | `run_dir.name`（`allocate_run_dir` が排他採番） | `YYMMDDHHMMSS` または `-NNN` suffix 付き |
| `step_id` | `str` | `Step.id` | 既に `allocate_attempt_dir` で path component として使われている値 |
| `attempt_name` | `str` | `attempt_dir.name` | `attempt-NNN` |

新しい CLI 引数・config キー・workflow YAML フィールドは追加しない。

### 出力

#### 生成物（ディレクトリ layout）

```text
<project_root>/tmp/kaji/
  .gitignore                       # 内容 "*\n"。tmp/kaji 配下全体を git から隠す（初回のみ作成）
  <issue_id>/<run_id>/<step_id>/attempt-NNN/   # = KAJI_TMP_DIR（attempt 固有、dispatch 前に作成）
```

- `attempt-NNN` は同 attempt の artifacts（`<artifacts_dir>/<issue_id>/runs/<run_id>/steps/<step_id>/attempt-NNN/`）
  と同じ番号。artifacts と tmp を (issue, run, step, attempt) で 1:1 に対応付けられる。
- 作成したディレクトリは削除しない（保持期間・自動削除方針は本 Issue のスコープ外）。

#### 環境変数（4 変数）

| 変数 | 値 | 役割 |
|------|----|------|
| `KAJI_TMP_DIR` | attempt tmp dir の正規化済み絶対パス | kaji 固有処理が明示参照する正本 |
| `TMPDIR` | `KAJI_TMP_DIR` と同値 | POSIX / Python `tempfile` / Node `os.tmpdir()` / `mktemp` 等が参照 |
| `TMP` | 同上 | Python `tempfile` / Node（POSIX）等が参照 |
| `TEMP` | 同上 | 同上 |

- 4 変数は常に同一文字列。親プロセス（`kaji run` 起動シェル・tmux server global env・Herdr pane
  初期 env）から継承した同名変数は attempt 固有値で**上書き**される。
- 同一 attempt 内（headless の transient retry を含む）では同じ値。attempt が変われば別値。

#### 関数・内部 IF（名前と責務のみ）

| 対象 | 変更 | 責務 |
|------|------|------|
| `runner.prepare_attempt_tmp_dir(project_root, *, issue_id, run_id, step_id, attempt_name) -> Path` | 新規（module-level。`allocate_attempt_dir` と同列） | `tmp/kaji/.gitignore` を確保し、attempt tmp dir を排他作成して正規化済み絶対パスを返す。境界外なら raise |
| `runner.build_tmp_env(tmp_dir: Path) -> dict[str, str]` | 新規 | 4 変数の dict を固定順（`KAJI_TMP_DIR`, `TMPDIR`, `TMP`, `TEMP`）で返す純粋関数 |
| `_StepExecutor.execute()` | 変更 | `allocate_attempt_dir` 直後に `prepare_attempt_tmp_dir` を呼び、tmp env を `_dispatch` へ渡す |
| `_StepExecutor._dispatch()` | 変更 | script-like: `context_env` に tmp env を merge。agent: tmp env のみを `execute_cli` / `execute_interactive_terminal` に渡す |
| `cli.execute_cli(..., env: Mapping[str, str] \| None = None)` | 引数追加 | `Popen(env={**os.environ, **env})`。`None` なら現行どおり親 env を継承 |
| `interactive_terminal.execute_interactive_terminal(..., env=None)` / `interactive_terminal_herdr.execute_interactive_terminal_herdr(..., env=None)` | 引数追加 | `_build_wrapper_command` へ env を伝搬 |
| `interactive_terminal._build_wrapper_command(..., env: Mapping[str, str] \| None = None)` | 引数追加 | env があれば `shlex.join(["env", "K=V", ..., wrapper, <9 args>])` を返す。9 引数の順序・意味は不変 |
| `errors.TmpDirPreparationError(HarnessError)` | 新規 | tmp dir 作成失敗（`OSError`）・既存衝突・境界外を表す |
| `script_exec._run_argv` | 変更なし | 既に `{**os.environ, **env}` で context env が親 env を上書きするため、merge 済み env を渡すだけで要件を満たす |
| `assets/interactive-terminal/wrapper.sh` | 変更なし | `exec bash -c` で起動する agent は wrapper の env を継承する |

### 使用例

```bash
# agent（Claude Code / Codex 等）の Bash ツール、または exec step の script 内
echo "$KAJI_TMP_DIR"     # /home/u/proj/tmp/kaji/407/261003050715/design/attempt-001
mktemp                    # /home/u/proj/tmp/kaji/407/261003050715/design/attempt-001/tmp.XXXXXXXXXX
git diff > "$KAJI_TMP_DIR/diff.txt"
```

```python
# exec_script / exec step の Python
import os, tempfile
assert tempfile.gettempdir() == os.environ["KAJI_TMP_DIR"]
with tempfile.NamedTemporaryFile() as f:   # KAJI_TMP_DIR 配下に作成される
    ...
```

```python
# runner 内部（疑似コード）
attempt_dir = allocate_attempt_dir(self.run_dir, step.id)
tmp_dir = prepare_attempt_tmp_dir(
    self.project_root,
    issue_id=self.run_ctx.canonical_id,
    run_id=self.run_dir.name,
    step_id=step.id,
    attempt_name=attempt_dir.name,
)
tmp_env = build_tmp_env(tmp_dir)
# script-like: env={**context_env, **tmp_env} / agent: env=tmp_env
```

### エラー

| 状況 | 挙動 |
|------|------|
| `tmp/` / `tmp/kaji/` / attempt dir の作成で `OSError`（権限不足、`tmp` が通常ファイル等） | `TmpDirPreparationError`（原因 `OSError` を chain）。dispatch しない。`WorkdirNotFoundError` と同じく dispatch 前の失敗として伝播し、`kaji run` は runtime error で終了 |
| attempt tmp dir が既に存在（stale な残骸で run_id が再利用された等） | `TmpDirPreparationError`。別 attempt と同じ dir を共有しない（衝突しない保証を機械的に担保） |
| `issue_id` / `run_id` / `step_id` / `attempt_name` が単一の有効な path component でない（空、`.`、`..`、`/` や NUL を含む、絶対パス、非正規表記。例: `a/../design`、`../other`） | ディレクトリを一切作成せずに `TmpDirPreparationError`（§ 方針 1 手順 1）。`step_id` は `workflow.py` で非空文字列としか検査されないため、ここで拒否する |
| 組み立てた path が不変条件（base 直下 4 階層・正規化済み）を満たさない | `TmpDirPreparationError`（防御的検査） |
| `tmp/kaji/.gitignore` が既に存在 | 内容を変更しない（利用者の編集を尊重） |

## 制約・前提条件

- **配置の基準は project root**: `kaji run` の `project_root`（`config.repo_root`）を `resolve()` した
  絶対パスを基準に、`tmp/kaji/...` を**字句的に**連結する。issue worktree 側で作業する agent にも
  main checkout 側の `tmp/` が渡る（Issue 本文「Kaji の project root 直下の `tmp/`」）。
- **境界の意味**: 4 変数の値は常に `<resolved project_root>/tmp/` を前置する文字列になる。`tmp/` 自体が
  リポジトリ外を指す symlink であるケースの実体位置は保証しない（書き込み先の強制・サンドボックスは
  スコープ外）。
- **強制しない**: 明示的な `/tmp` 書き込み、環境変数を参照しないツール、agent CLI 固有の scratch 機構
  は対象外（Issue 本文「強制範囲」）。
- **既存 env 契約の維持**: agent 経路へは 4 変数のみを追加し、`KAJI_ISSUE_ID` 等の他の `KAJI_*` は
  渡さない（現行どおり script-like 専用）。prompt のコンテキスト変数も変更しない。
- **path 長**: 例 `/home/aki/dev/kaji/main/tmp/kaji/407/261003050715/design/attempt-001` は 68 文字。
  AF_UNIX socket の `sun_path` は 108 byte 上限（unix(7)）で、一時ディレクトリに socket を作るツール
  （Python `multiprocessing` の forkserver 等）は長い `TMPDIR` で失敗しうる。layout を compact に
  保つことで影響を下げるが、深い project root では残存リスクとして docs に記載する。
- **git 作業ツリーへの影響**: kaji 本体は `tmp/` を gitignore 済みだが、kaji を導入した他リポジトリは
  そうとは限らない。`issue-close` の未追跡ファイル安全ガード（`docs/guides/python-starter.md:337`）を
  誤発火させないよう、`tmp/kaji/.gitignore`（`*`）で配下全体を自己完結的に ignore する
  （git は nested `.gitignore` を解釈し、`*` は当該 `.gitignore` 自身も対象にする）。
- **上位ディレクトリ探索への影響（実測済み）**: 一時ディレクトリがリポジトリ内になると、そこを起点に
  親方向を探索する処理（`KajiConfig.discover` の `.kaji/config.toml` 探索、`git rev-parse` 等）が
  実リポジトリを見つける。worktree で次を実測した:
  - `TMPDIR=TMP=TEMP=<worktree>/tmp/kaji-probe pytest -p no:cacheprovider -n auto` → **18 failed /
    3396 passed**（例: `tests/test_config.py::TestKajiConfigDiscover::test_discover_not_found_raises`、
    `tests/test_resolve_main_worktree.py::TestResolveMainWorktree::test_non_git_dir_raises`、
    `tests/test_local_cli_large_local.py::test_failfast_issue_view_no_config_toml` 等）
  - 同じ失敗 test 群を env 未設定で実行 → **141 passed**

  本機能の導入後、workflow の implement / final-check 等の agent が実行する `make check` は
  `KAJI_TMP_DIR` 配下の `tmp_path` で走るため、この 18 件を hermetic にしないと以降の全 workflow の
  品質ゲートが壊れる。よって本 Issue の範囲で対処する（§ 方針 4）。
- **依存**: 新規ライブラリなし（標準ライブラリ `os` / `pathlib` / `shlex` のみ）。性能影響は attempt
  ごとの `mkdir` 数回のみ。

## 変更スコープ

- `kaji_harness/runner.py`（`prepare_attempt_tmp_dir` / `build_tmp_env` 追加、`execute` / `_dispatch` 配線）
- `kaji_harness/cli.py`（`execute_cli` / `_execute_cli_once` に `env`）
- `kaji_harness/interactive_terminal.py`（`execute_interactive_terminal` / `_launch_pane` /
  `_build_tmux_split_argv` / `_build_wrapper_command` に `env`）
- `kaji_harness/interactive_terminal_herdr.py`（`execute_interactive_terminal_herdr` に `env`）
- `kaji_harness/errors.py`（`TmpDirPreparationError`）
- `tests/conftest.py`（project 外ディレクトリ fixture）と、§ 制約で列挙した 18 件の test
- docs（§ 影響ドキュメント）

新規 module は作らない（`tests/test_layer_imports.py` の module 分類更新を不要にするため）。

## 方針

1. **attempt tmp dir の準備（runner）**
   - `_StepExecutor.execute()` で `allocate_attempt_dir` の直後、step start ログより前に
     `prepare_attempt_tmp_dir` を呼ぶ。
   - `prepare_attempt_tmp_dir` の手順:
     1. **component 検査（副作用前）**: `issue_id` / `run_id` / `step_id` / `attempt_name` の各値が
        「単一の有効な path component」であることを検査し、違反は `TmpDirPreparationError` で拒否する。
        規則（いずれかに該当すれば拒否）:
        - 空文字列
        - `.` または `..`
        - `/` を含む（POSIX separator。`os.sep` と `os.altsep` が非 `None` ならそれも含む）
        - NUL（`\x00`）を含む
        - 絶対パスとして解釈される（`os.path.isabs(value)`。`/` 拒否で実質包含されるが明示する）
        - `os.path.normpath(value) != value`（上記で拾えない非正規表記を保険として拒否）
        これにより `step_id="a/../design"`（base 内の別表記 alias）や `"../other"`（traversal）も
        境界の内外を問わず拒否する。包含検査だけに頼らない。
     2. `base = project_root.resolve() / "tmp" / "kaji"`、`target = base / issue_id / run_id / step_id / attempt_name`
        （検査済み component の連結なので `..` / `.` / 重複 separator を含まない正規化済み絶対パス）
     3. 防御的不変条件として `target.parent.parent.parent.parent == base` かつ
        `os.path.normpath(target) == str(target)` を assert 相当で検査（違反は `TmpDirPreparationError`）
     4. `base` を `mkdir(parents=True, exist_ok=True)`
     5. `base / ".gitignore"` を排他作成（`"x"` mode）で `*\n` を書く。`FileExistsError` は無視
     6. `target` の親を `mkdir(parents=True, exist_ok=True)`、`target` を `mkdir(exist_ok=False)`
     7. 手順 2 で組み立て手順 3 で検査した**同一の** `target` を返す（作成するパスと返却するパスは常に一致）
     - component 違反 / `OSError` / 既存 / 不変条件違反は `TmpDirPreparationError` に wrap して raise
2. **env の配線（runner → 各 dispatch）**
   - `tmp_env = build_tmp_env(tmp_dir)`。
   - `exec` / `exec_script`: `{**context_env, **tmp_env}` を `execute_exec` / `execute_script` に渡す。
     `_run_argv` が `{**os.environ, **env}` で親 env を上書きする既存挙動で継承値の上書きが成立する。
   - headless agent: `execute_cli(..., env=tmp_env)` → `Popen(env={**os.environ, **tmp_env})`。
     `execute_cli` の transient retry は同じ env を再利用する（同一 attempt 内で同値）。
   - interactive terminal: `execute_interactive_terminal(..., env=tmp_env)`。tmux pane は kaji
     プロセスではなく tmux server の environment を継承する（tmux(1) GLOBAL AND SESSION ENVIRONMENT）
     ため、kaji の `os.environ` を変えても届かない。よって pane に渡す command 文字列自体に
     `env KAJI_TMP_DIR=... TMPDIR=... TMP=... TEMP=... <wrapper> <9 args>` を前置する。
     Herdr は同じ `_build_wrapper_command` の結果を launcher に `exec env PATH=... <command>` として
     書くため、同じ前置で届く。shell の `K=V cmd` 代入構文ではなく `env(1)` を使うのは、
     `shlex.join` が空白を含むパスをクォートした場合に代入語として解釈されなくなるのを避けるため。
3. **ドキュメント**: 4 変数の役割、layout、明示的な `/tmp` 書き込みを禁止しないこと、自動削除しないこと、
   上位探索・AF_UNIX path 長の注意を開発者向け docs に記載する（§ 影響ドキュメント）。
4. **既存テストの hermetic 化（上位探索問題への対処）**
   - `tests/conftest.py` に「どの kaji project / git repo の配下でもないこと」を保証する fixture
     （仮称 `outside_project_tmp_path`）を追加する。`tempfile.gettempdir()` が `.kaji/config.toml` または
     `.git` を祖先に持つ場合は、`TMPDIR` / `TMP` / `TEMP` を参照しない platform 既定候補（POSIX では
     `/tmp`）配下に一意ディレクトリを作る。作成後に祖先検査を行い、満たせなければ fail loud
     （skip しない）。後始末は fixture が行う。
   - § 制約で実測した 18 件（「project 外 / git 外であること」を前提とする test）だけをこの fixture へ
     切り替える。それ以外の test の `tmp_path` は従来どおり `TMPDIR`（= `KAJI_TMP_DIR`）に従う。
   - 明示的な project 外ディレクトリの使用は、Issue 本文「明示的な `/tmp` 書き込みの禁止は扱わない」の
     範囲内であり、test 前提を明文化するものである。

### データフロー

```text
WorkflowRunner.run()
  └ _StepExecutor.execute(step)
       ├ allocate_attempt_dir(run_dir, step.id)            -> artifacts/.../attempt-NNN
       ├ prepare_attempt_tmp_dir(project_root, ...)        -> tmp/kaji/<id>/<run>/<step>/attempt-NNN
       ├ tmp_env = build_tmp_env(tmp_dir)
       └ _dispatch
            ├ exec / exec_script  : env = context_env | tmp_env  -> _run_argv(Popen env=os.environ|env)
            ├ headless agent      : execute_cli(env=tmp_env)     -> Popen(env=os.environ|tmp_env)
            └ interactive terminal: execute_interactive_terminal(env=tmp_env)
                 ├ tmux : split-window "<env K=V ...> wrapper.sh <9 args>"
                 └ Herdr: launcher "exec env PATH=... env K=V ... wrapper.sh <9 args>"
```

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 一時作業領域の正本 | `KAJI_TMP_DIR` を kaji 固有の正本として追加 | Issue 本文「重要判断」表 1 行目（人間決定、起票依頼 2026-08-22） | 値は正規化済み絶対パス、attempt ごとに dispatch 前作成 |
| 一般ツールへの適用 | `TMPDIR` / `TMP` / `TEMP` を `KAJI_TMP_DIR` と同値に設定し、継承値を上書き | Issue 本文「重要判断」表 2 行目・完了条件 3 項目目（人間決定） | 4 変数を `build_tmp_env` で一体生成し、全経路で同じ dict を使う |
| 配置境界 | project root 直下の `tmp/` 配下 | Issue 本文「重要判断」表 3 行目（人間決定。具体構造は設計で詳細化と明記） | `tmp/kaji/<issue_id>/<run_id>/<step_id>/attempt-NNN`。`kaji` 名前空間で手動の作業ファイルと分離 |
| 強制範囲 | 環境変数を尊重する処理の誘導のみ。`/tmp` 明示書き込みは禁止しない | Issue 本文「重要判断」表 4 行目・スコープ境界（人間決定） | docs に明記。テストの project 外 fixture もこの範囲内として扱う |
| 対象 dispatch 経路 | headless / interactive terminal / exec / exec_script の全経路 | Issue 本文「概要」・完了条件 5 項目目（人間決定） | interactive は tmux / Herdr の両 backend を含むと解釈（`interactive_terminal_backend` の両値） |
| ディレクトリ layout の詳細 | artifacts の識別子を compact にミラー（`runs/` `steps/` は省略） | AI の仮定。根拠: artifacts と 1:1 対応で調査容易、AF_UNIX `sun_path` 108 byte 上限への配慮。two-way door（内部 layout、後から変更可）。検査先: review-design | — |
| 既存 dir 衝突時の挙動 | `exist_ok=False` で fail loud（`TmpDirPreparationError`） | AI の仮定。根拠: 完了条件「run / step / attempt 間で衝突しない」を機械的に担保。run_id は排他採番なので通常発生しない。検査先: review-design / review-code | — |
| interactive への env 伝達方式 | wrapper command に `env K=V` を前置（wrapper.sh 不変） | AI の仮定。根拠: tmux pane は server env を継承するため明示伝達が必須。両 backend 共通の 1 箇所で済む。検査先: review-design、隔離 tmux server の Large テスト、Herdr launcher 実行テスト | — |
| path component の検査規則 | 4 識別子を単一の有効な component として検査し、traversal と alias を作成前に拒否 | AI の仮定（review-design の指摘 MF1 を受けて具体化）。根拠: `workflow.py` は step ID を非空文字列としか検査しない。完了条件の「正規化済み絶対パス」「run / step / attempt 間で衝突しない」を字句上で担保する。two-way door。検査先: verify-design / review-code | — |
| agent 経路へ渡す env の範囲 | 4 変数のみ。他の `KAJI_*` は渡さない | AI の仮定。根拠: Issue は 4 変数のみ要求。agent への `KAJI_*` 追加は skill-authoring.md の env 契約変更となりスコープ外。検査先: review-design | — |
| `tmp/kaji/.gitignore` の自動作成 | 初回に `*` を書く。既存なら触らない | AI の仮定。根拠: kaji 導入先リポジトリで `tmp/` が未 ignore だと `issue-close` の安全ガードや `git add -A` に影響。two-way door。検査先: review-design | — |
| 18 件の test の hermetic 化 | project 外を保証する fixture へ切り替え | AI の仮定。根拠: § 制約の実測。本機能導入で `make check` が壊れるため本 Issue の範囲（無関係な修正ではない）。test コードのみで可逆。検査先: review-design / review-code | 対象は実測で失敗した 18 件に限定 |
| 自動削除 | 行わない | Issue 本文スコープ境界「保持期間・自動削除方針の変更」を含まない（人間決定） | docs に「自動削除されない」と記載 |

one-way door の未決は検出しなかった。公開 CLI 引数・終了コード・永続化 schema・config キーは変更しない。
新たに agent / script へ見える契約は 4 環境変数のみで、これは人間決定済み。

## テスト戦略

### 変更タイプ

- 実行時コード変更（dispatch 時の env と生成物が変わる）

### 検証観点

1. 4 変数が常に同一の正規化済み絶対パスで、`<project_root>/tmp/` 外を指さない
2. 親プロセスの `TMPDIR` / `TMP` / `TEMP` が attempt 固有値で上書きされる
3. run / step / attempt が異なれば値が異なり、同一 attempt 内（transient retry）では同値
4. 4 つの dispatch 経路すべてで、起動されたプロセスの標準 temp API が `KAJI_TMP_DIR` 配下を使う
5. 失敗系（作成失敗・既存衝突・境界外）で dispatch しない
6. 既存挙動の維持（env 未指定時の `execute_cli` / `_build_wrapper_command`、wrapper 9 引数契約）
7. `make check` が `KAJI_TMP_DIR` 相当（リポジトリ内 TMPDIR）環境でも通る

### Small テスト

- `build_tmp_env`: キー集合が 4 つちょうど、全値が同一、順序固定（観点 1）
- `_build_wrapper_command`:
  - `env` 指定時、`shlex.split` で先頭が `env` + 4 個の `K=V`、続いて wrapper と 9 引数が既存順で並ぶ。
    空白を含むパスでも round-trip で値が保たれる（観点 6）
  - `env=None` で出力が現行と完全一致（既存 test の期待値を維持）（観点 6）

### Medium テスト

- `prepare_attempt_tmp_dir`（`tmp_path` を project root とする実 FS）:
  - 返り値が絶対・正規化済みで `<project_root>/tmp/kaji/<id>/<run>/<step>/attempt-NNN` に一致し、
    ディレクトリが存在（観点 1）
  - `tmp/kaji/.gitignore` が `*` で作成され、既存内容は上書きされない
  - 既存 target で `TmpDirPreparationError`、`tmp` が通常ファイルのとき `TmpDirPreparationError`（観点 5）
  - 不正 component 入力を 4 つの引数それぞれについて parametrize し、`TmpDirPreparationError` となり
    `tmp/` 配下に何も作成されない（`tmp/` 自体も作られない）ことを確認（観点 1, 5）。入力には次を含める:
    - base 外への traversal: `"../other"`、`"../../x"`、`"/abs"`
    - **base 内に戻る traversal / alias**: `"a/../design"`、`"./design"`、`"design/."`、`"design//x"`
    - `""`、`"."`、`".."`、`"a/b"`、NUL 含み
  - 正常入力の返却パスが `os.path.normpath` で不変、かつ `..` / `.` component を含まないこと（観点 1）
  - 異なる run / step / attempt で異なるパス（観点 3）
- runner 結合（既存 `tests/test_runner_exec_script_dispatch.py` / `test_runner_interactive_dispatch.py` /
  `test_exec_step_dispatch.py` の mock パターン）:
  - `exec` / `exec_script` / headless / interactive（tmux・herdr）の各 dispatch 関数が受け取る env に
    4 変数が同値で含まれ、値が `project_root/tmp/` 配下で、dispatch 時点でディレクトリが存在（観点 1, 4）
  - 同一 step が cycle で 2 attempt 走ると値が異なる（観点 3）
  - `prepare_attempt_tmp_dir` が失敗すると dispatch 関数が呼ばれず `TmpDirPreparationError` が伝播（観点 5）
- `execute_cli` + 実 subprocess: `Popen` に渡る env が `os.environ` を土台に 4 変数で上書きされること。
  `monkeypatch.setenv("TMPDIR", "<other>")` 下でも上書きされる。transient retry 時も同じ env（観点 2, 3）
- `execute_exec` + 実 subprocess（`sys.executable -c`）: 親 `TMPDIR` を別値にした状態で、子の
  `tempfile.gettempdir()` と `NamedTemporaryFile` の作成先が `KAJI_TMP_DIR` 配下（観点 2, 4）

### Large テスト（`large` + `large_local`。subprocess あり・ネットワークなし）

- 実 `kaji run`（既存 `tests/test_exec_step_e2e_large_local.py` パターン）で exec step を実行し、script 内で
  4 変数の一致・`tempfile.gettempdir()`・`mktemp` 相当の作成先・`<repo>/tmp/` 配下であることを検査して
  verdict を書く。外側の `TMPDIR` は別ディレクトリに設定して起動する。実行後に
  `tmp/kaji/<id>/<run_id>/<step>/attempt-001` と `.gitignore` の存在を確認（観点 1, 2, 4）
- 実 `kaji run` + PATH 上の fake `claude`（既存 `tests/test_verdict_artifact_e2e_large_local.py` パターン）で
  headless agent 経路を通し、fake CLI プロセス内の `tempfile.gettempdir()` を検査（観点 4）
- **interactive（tmux）: 隔離した実 tmux server での E2E**（観点 2, 4）
  - 目的: 「tmux pane は kaji プロセスではなく server の environment を継承する」という設計上の分岐を、
    実 backend で検証する。
  - 隔離: 利用者の既存 tmux server / session は一切使わない。test 専用 server を
    `tmux -L kaji-test-<uuid> -f /dev/null new-session -d -s <name> -x 200 -y 50 'sleep 600'` で起動する
    （`-f /dev/null` で利用者の設定を読まない。`-L` を使うのは、socket を長い `tmp_path` 配下に置くと
    AF_UNIX の `sun_path` 108 byte 上限を超えうるため）。`display-message -p '#{socket_path}'` と
    `'#{pane_id}'` から `TMUX`（`<socket>,<server pid>,<session idx>`）と `TMUX_PANE` を組み立て、
    `monkeypatch.setenv` する。`execute_interactive_terminal` は `$TMUX` 経由でこの専用 server に接続する。
  - 前提値の食い違いを作る: 専用 server に `set-environment -g TMPDIR/TMP/TEMP <server_dir>`、
    test プロセスの `os.environ` に `TMPDIR/TMP/TEMP=<parent_dir>` を設定する（両者とも attempt 値と異なる）。
    PATH の先頭に fake `claude` を置く。
  - 実行: `execute_interactive_terminal(step=<claude agent step>, ..., env=build_tmp_env(<attempt dir>),
    backend="tmux", timeout=<短め>)`。実 `split-window` → 実 `wrapper.sh` → fake `claude` が、自身の
    4 変数・`tempfile.gettempdir()`・`NamedTemporaryFile` の作成先を JSON に記録してから verdict を書く。
  - 検証: 4 変数がすべて attempt 値（`<server_dir>` / `<parent_dir>` ではない）であること、temp API の
    作成先が attempt dir 配下であること、`execute_interactive_terminal` が verdict を観測して正常に戻ること。
  - 後始末: fixture の finalizer で `tmux -L kaji-test-<uuid> kill-server` を必ず実行する（失敗時も
    finalizer で実行し、専用 server を残さない）。
  - 前提環境: tmux ≥ 3.1（interactive runner の既存最小要件）。tmux 不在は環境不備として扱い、skip しない
    （`testing-convention.md`「環境不備は修正対象」）。本リポジトリの GitHub Actions は現状 test job を
    持たない（`.github/workflows/` は `labels-sync.yml` / `publish-pypi.yml` のみ）ため、品質ゲートは各開発環境の
    `make check` で実行され、tmux はその前提に加わる。CI test job を新設する場合は tmux を install する
    （新設自体は本 Issue のスコープ外）。
- **interactive（Herdr）: launcher 実行による代替検証**（観点 4）
  - `_materialize_herdr_launcher` で生成した実 launcher を `sh` で実行し、実 `wrapper.sh` → fake `claude`
    の経路で 4 変数と temp API が attempt 値になることを検証する（launcher の `exec env PATH=... env K=V ...`
    連結の実行検証）。親 `os.environ` の `TMPDIR` を別値にして上書きも確認する。
  - 実 Herdr `pane split` / `pane run` の起動は恒久テストに含めない。tmux とは理由が異なる:
    Herdr は kaji の必須依存ではない任意 backend であり、`herdr --help`（0.8.2）で確認できる起動手段は
    `herdr` / `herdr --session <name>`（TTY client で attach する対話起動）で、test から隔離 server を
    headless に起動し後始末する手段を確認できなかった。tmux で実証した「server 環境と pane 環境の分離」が
    Herdr でも成り立つとは**仮定しない**。Herdr の pane 環境は launcher の `exec env ...` が明示的に
    上書きするため、server 側の環境の値に関係なく attempt 値になる構造。それを launcher 実行テストで確認する。
  - 代替証跡: Herdr 実 backend の確認は、実装工程の変更固有検証として、Herdr が利用可能な環境で
    `interactive_terminal_backend = "herdr"` の 1 step（fake ではない agent でも可）を手動実行し、
    agent プロセス内の `echo $KAJI_TMP_DIR $TMPDIR $TMP $TEMP` 結果を実装報告に記録する。
    利用できない環境なら、その旨を記録する。
  - 既存 Medium（mock）で、Herdr launcher に渡す command と tmux `split-window` argv の末尾が
    `_build_wrapper_command` の出力であることを引き続き検証する
- `make check` のリポジトリ内 TMPDIR 実行（観点 7）: hermetic 化後、実装工程の変更固有検証として
  `TMPDIR=TMP=TEMP=<worktree>/tmp/<dir> make check` を実行し全件 pass を確認する。恒久的な回帰検出は、
  本機能導入後に全 workflow の agent が `KAJI_TMP_DIR` 下で `make check` を走らせること自体が担う
  （新たな test 追加では得られない回帰シグナルが、dogfooding で常時得られる）

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規ライブラリ・技術選定なし |
| docs/ARCHITECTURE.md | あり | § 実行アーティファクトの layout に `tmp/kaji/...` の attempt tmp layout と 4 変数の注入経路を追記 |
| docs/dev/skill-authoring.md | あり | exec_script の env 表に 4 変数を追加。agent step でも 4 変数がプロセス env に入ること、明示 `/tmp` は禁止しないこと、自動削除しないことを記載 |
| docs/dev/workflow-authoring.md | あり | exec step の context env 記述に 4 変数を追加し、agent step にも注入される旨を記載 |
| docs/dev/testing-convention.md | あり | workflow 内では `tmp_path` がリポジトリ内になる旨と、project 外 / git 外を前提とする test は専用 fixture を使う規約を追記 |
| docs/reference/ | なし | API 仕様・コーディング規約の変更なし |
| docs/cli-guides/ | なし | CLI 引数・出力の変更なし |
| docs/guides/python-starter*.md | なし | 既存記述（gitignored `tmp/` 推奨）と矛盾しない |
| AGENTS.md / CLAUDE.md | なし | always-apply 規約の変更なし |
| README.md / llms.txt | なし | 外部読者向けの機能説明は変えない（開発者向け docs で完結） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #407 本文 | https://github.com/apokamo/kaji/issues/407 | 重要判断表（4 変数・project root 直下 `tmp/`・強制範囲）、スコープ境界、完了条件 |
| Issue #406 調査コメント | https://github.com/apokamo/kaji/issues/406#issuecomment-5380701055 | agent が `/tmp` 配下に scratch を作った記録（動機） |
| POSIX Base Definitions, Environment Variables | https://pubs.opengroup.org/onlinepubs/9799919799/basedefs/V1_chap08.html | `TMPDIR`: 「一時ファイルを作るプログラムのために用意されたディレクトリのパス名を表す」 |
| Python `tempfile.gettempdir` | https://docs.python.org/3/library/tempfile.html#tempfile.gettempdir | 探索順は `TMPDIR` → `TEMP` → `TMP` → platform 既定（POSIX は `/tmp` 等）。4 変数同値なら順序に依らず同じ dir |
| Node.js `os.tmpdir()` | https://nodejs.org/api/os.html#ostmpdir | POSIX では `TMPDIR` / `TMP` / `TEMP` を参照（Claude Code / Codex 周辺の Node 系ツールへの波及根拠） |
| GNU coreutils `mktemp` | https://www.gnu.org/software/coreutils/manual/html_node/mktemp-invocation.html （取得できない環境向けの代替: ローカルの `mktemp --help`、coreutils 9.x） | `mktemp --help` から引用（日本語ロケール）:「TEMPLATE が指定されない場合、tmp.XXXXXXXXXX が使用され、--tmpdir が暗黙のうちに指定されます」「-p DIR, --tmpdir[=DIR] … DIR が指定されていない場合、$TMPDIR が設定されていれば $TMPDIR が使用され、設定されていなければ /tmp が使用される」 |
| tmux socket 選択 | https://man7.org/linux/man-pages/man1/tmux.1.html | `-L socket-name` は既定 socket ディレクトリ内の別名 socket を使う。`-f file` は代替設定ファイル。`$TMUX` は client が接続する server の socket を示す → 隔離 server を使った Large テストの根拠 |
| tmux(1) | https://man7.org/linux/man-pages/man1/tmux.1.html | GLOBAL AND SESSION ENVIRONMENT: 新しい window / pane のプロセスは server の global / session environment から作られる → kaji プロセスの env は継承されないため command 前置で明示伝達する |
| env(1) | https://man7.org/linux/man-pages/man1/env.1.html | `env NAME=VALUE... COMMAND` で変更した環境で COMMAND を実行 |
| unix(7) | https://man7.org/linux/man-pages/man7/unix.7.html | `sun_path` は 108 byte。長い一時ディレクトリでの socket 作成失敗リスクの根拠 |
| gitignore | https://git-scm.com/docs/gitignore | 下位ディレクトリの `.gitignore` はそのディレクトリ相対で評価される → `tmp/kaji/.gitignore` の `*` で配下を ignore |
| Python `subprocess.Popen` | https://docs.python.org/3/library/subprocess.html#subprocess.Popen | `env` 指定時はその mapping が子の環境になる（`{**os.environ, **env}` で継承＋上書き） |
| `kaji_harness/runner.py` | `_StepExecutor.execute` / `_dispatch` / `_build_context_env`（L263-508） | 現行は script-like のみ context env、agent 経路は env 未指定 |
| `kaji_harness/script_exec.py` | `_run_argv`（L71-125） | `{**base_env, **env}` で context env が親 env を上書き |
| `kaji_harness/interactive_terminal.py` / `interactive_terminal_herdr.py` | `_build_wrapper_command`（L227-260）、`_materialize_herdr_launcher`（L820-869） | 両 backend が同じ wrapper command を使う。Herdr は `exec env PATH=... <command>` |
| `.gitignore` | L64-66 | `tmp/` は作業ローカルの一時領域、コミット禁止 |
| 実測ログ（本設計時） | `TMPDIR=TMP=TEMP=<worktree>/tmp/kaji-probe pytest -p no:cacheprovider -n auto` | 18 failed / 3396 passed。同じ失敗 test 群を env 未設定で実行すると 141 passed |
