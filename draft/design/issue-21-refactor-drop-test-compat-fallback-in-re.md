# [設計] `resolve_main_worktree()` から test-compat fallback を撤去する

Issue: gl:21

## 概要

`kaji_harness/providers/_worktree.py::resolve_main_worktree()` から、
production 不到達の test-compat fallback (`FileNotFoundError` / `returncode != 0`
→ `start_dir.resolve()`) を撤去し、両経路で `LocalProviderError` を raise させる。
影響テストは「`git init --initial-branch=<default_branch>` を含む fixture」または
「`patch("kaji_harness.providers.resolve_main_worktree")` 局所 mock」へ移行する。
公開シグネチャ (`start_dir`, `default_branch` → `Path`) は不変、外部から観測可能な
production 挙動は不変。

## 背景・目的

### 現状の問題（観測可能な形）

gl:11 (`feat: pin LocalProvider repo_root to main worktree`) で導入された
`resolve_main_worktree()` には、production では到達しない 2 つの fallback 分岐が
test fixture 互換のために残っている
（[`kaji_harness/providers/_worktree.py:59-76`](../../kaji_harness/providers/_worktree.py)）:

```python
try:
    proc = subprocess.run([...], capture_output=True, text=True, check=False)
except FileNotFoundError:
    return start_dir.resolve()       # ① git CLI 不在 fallback
if proc.returncode != 0:
    return start_dir.resolve()       # ② 非 git repo fallback
```

これにより、以下 3 つの観測可能な歪みが発生している:

1. **production 契約の曖昧化（grep 検証可能）**
   - `kaji_harness/providers/_worktree.py` 内に `return start_dir.resolve()` が
     2 件、`docs/cli-guides/local-mode.md` のトラブルシュート節 (line 範囲は実装時
     再確認) に「main worktree 不在は `LocalProviderError`」と書かれているが、
     git CLI 不在 / 非 git repo は静かに cwd 起点に戻る。仕様と実装が一致していない。
   - `provider.type='local'` を誤って非 git 配下で設定すると、本来は早期に
     `LocalProviderError` が出るべきところで fallback が動き、後続の `git add` /
     `git commit --only` (`kaji_harness/cli_main.py:1212-1234`) で
     `fatal: not a git repository` という二次エラーが出る形になる。gl:11 で fix
     した「cwd 依存」root cause を部分的に再導入している。

2. **テストの暗黙挙動依存（grep 検証可能）**
   - `tests/test_phase3c_dispatcher.py` / `tests/test_phase4_pr_bare_provider.py`
     は `_write_repo(tmp_path)` で `.kaji/config.toml` だけを作る非 git
     ディレクトリを fixture に持ち、`kaji_harness.cli_main.subprocess.run` を
     `MagicMock` で patch する。`_worktree.py` 内の `subprocess.run` は patch
     対象外なので実 `git` が走り、非 git ディレクトリ上では `returncode != 0`
     fallback に乗って `start_dir.resolve()` が返る。
   - この経路は `_worktree.py` の docstring に「test 経路維持のための明示仕様」
     と書かれているが、テスト側にはこの依存関係の宣言が無い。`_worktree.py` を
     fail-fast 化した瞬間に複数 test ファイルが連鎖的に壊れる構造であり、
     refactor 耐性が低い。

3. **設計書の自己矛盾（grep 検証可能）**
   - gl:11 の設計書
     [`draft/design/issue-11-kaji-issue-comment-worktree-cwd-local-is.md`](issue-11-kaji-issue-comment-worktree-cwd-local-is.md)
     は「§ 制約・前提条件: production は git repo を前提」(line 159-160) と
     「§ インターフェース §§ `resolve_main_worktree()` の契約 §§§ 失敗ケース表」
     (line 113-122) で「非 git は fallback」を同時に主張している。設計書内で
     normative な記述同士が衝突しており、後続 reviewer が一貫した判断を下せない。

### 改善指標（観測可能 / 測定可能）

| 指標 | 現状 (fix/11 ＝ `dc192ba`) | 完了後 | 計測方法 |
|------|---------------------------|--------|---------|
| `kaji_harness/providers/_worktree.py` 内の `return start_dir.resolve()` 文 | 2 | 0 | `grep -c 'return start_dir.resolve()' kaji_harness/providers/_worktree.py` |
| 非 git tmp_path 上で `get_provider()` を呼んでも fallback で通過するテストファイル数 | 2+ (`test_phase3c_dispatcher.py` / `test_phase4_pr_bare_provider.py`) | 0 | refactor 後に `LocalProviderError` を raise させ、影響テストが「`git init` fixture」または「`resolve_main_worktree` 局所 mock」のいずれかへ移行済であること（移行漏れがあれば `pytest` で fail） |
| `_worktree.py` の fallback 関連コード行数 | 8 行（try/except + if 分岐 + 2 つの return + コメント） | 0 | `git diff --stat` |
| gl:11 設計書内の「制約・前提条件 (git repo 前提)」と「失敗ケース表 (fallback)」の矛盾 | 1 箇所 | 0 | gl:11 設計書 § 失敗ケース表が「git CLI 不在 / 非 git → `LocalProviderError`」に書き換わっていること |
| `_worktree.py` docstring の `Returns` 節と `Raises` 節の整合 | fallback と raise が両立して記述 | raise のみ | 設計書の docstring サンプルと実装が一致 |

### 改善後に得られるもの

- production の `provider.type='local'` 起動シーケンスが「git 不在 / 非 git → 即
  `LocalProviderError` (remediation 案内付き)」の 1 経路に統一される。後続 git
  ops での二次エラーが消える。
- テストが「設計時に意識した経路」を明示的に組む形になる: 非 git の挙動を
  検証したいテストは `pytest.raises(LocalProviderError)` で expectations を書き、
  dispatch ロジックを検証したいテストは `patch("kaji_harness.providers.resolve_main_worktree")`
  で worktree 解決をスタブ化する。暗黙依存が消える。
- gl:11 設計書が自己矛盾を解消し、後続の設計レビュー（gl:21 の本設計書を含む）
  が gl:11 を「整合した normative な参照」として引用できる。

## ベースライン計測

実装フェーズの冒頭 (`/issue-implement` Step 1 相当) で、refactor 着手前に再計測
する基準。誰が実行しても同じ値が出る形にする:

```bash
# (1) fallback の現在件数（期待: 2）
grep -c 'return start_dir.resolve()' kaji_harness/providers/_worktree.py

# (2) 影響テストの現状件数（期待: 既存テスト 1 件 + 連鎖影響テスト）
grep -l 'test_non_git_dir_falls_back_to_start_dir' tests/

# (3) 非 git tmp_path で _handle_issue 経路を踏むテストの洗い出し
#     （実装時に grep で網羅した結果をベースラインとして記録）
grep -rln 'type = "local"' tests/ \
  | xargs grep -L 'git init' \
  | xargs grep -l '_handle_issue\|_handle_pr\|get_provider'

# (4) 既存テストの全体 pass 状態（baseline）
make check  # → 全 pass を確認してから refactor に入る

# (5) docstring / 設計書の矛盾検証
grep -n 'fallback' kaji_harness/providers/_worktree.py
grep -n '失敗ケース表\|fallback' draft/design/issue-11-kaji-issue-comment-worktree-cwd-local-is.md
```

`make check` baseline が green であることを `/issue-implement` の Red 着手前に
確認し、本 refactor の Red 状態（fallback 削除直後）が「想定通りの test 群」だけを
落とすことを assert する（連鎖範囲の網羅性検証）。

## インターフェース

### 公開 IF は不変

`resolve_main_worktree(*, start_dir: Path, default_branch: str) -> Path` の
公開シグネチャ・呼び出し側 (`kaji_harness/providers/__init__.py:114-117`) は
変更しない。`LocalProvider.__init__()` のシグネチャも不変。

### 失敗ケース挙動の変更（外部観測される差分）

| 条件 | 変更前 | 変更後 |
|------|--------|--------|
| `git` CLI が PATH 上に無い (`FileNotFoundError`) | `start_dir.resolve()` を返す | `LocalProviderError` を raise（remediation: `git` をインストールするか PATH を通す） |
| `git worktree list` が exit != 0（非 git repo） | `start_dir.resolve()` を返す | `LocalProviderError` を raise（remediation: `git init` 済みの worktree から実行するか、`provider.type='local'` を使わない構成にする） |
| `default_branch` に一致する worktree が無い | `LocalProviderError`（変更なし） | `LocalProviderError`（変更なし） |
| 同一 branch を複数 worktree が checkout | 最初のものを採用 + stderr warning（変更なし） | 同左（変更なし） |
| porcelain 出力 parse 不能 | `LocalProviderError`（一致 0 件経路に合流, 変更なし） | 同左（変更なし） |

production の `provider.type='local'` 利用者は「git repo + main worktree」を必ず
持つ前提（gl:11 設計書 § 制約・前提条件）なので、変更前後で production 観測挙動
は同一。差分が出るのは「git 不在 / 非 git 環境で `provider.type='local'` を誤
設定した運用ミス」と「非 git tmp_path で `get_provider()` を踏むテスト」のみ。

### `LocalProviderError` メッセージの最低限の規定

両 fallback 削除経路で raise する `LocalProviderError` は、利用者が次の行動を
取れる文言にする（actionable error）。具体的な wording は実装フェーズで決めるが、
本設計では以下を要件として固定する:

- 何が起きたか（git CLI 不在 / 非 git ディレクトリでの起動）
- なぜ問題か（`provider.type='local'` は git repo + main worktree を前提とする）
- どう直すか（git をインストール、または別ディレクトリから起動、または
  `provider.type` を別の値に切り替え）

既存の「`default_branch` に一致する worktree が無い」エラーメッセージ
（`_worktree.py:83-87`）のスタイルに揃える。

### 使用例

公開 IF が変わらないため、production 利用コードは無変更:

```python
# kaji_harness/providers/__init__.py (line 114-117) — 変更なし
main_root = resolve_main_worktree(
    start_dir=config.repo_root,
    default_branch=local_cfg.default_branch,
)
```

テスト側の典型パターン (refactor 後):

```python
# パターン A: git repo を tmp_path に組む（production と同形の検証）
def test_dispatch_under_real_git_repo(tmp_path, monkeypatch):
    repo = _write_repo(tmp_path, provider="local")
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main", str(repo)],
        check=True,
    )
    monkeypatch.chdir(repo)
    rc = _handle_issue([...])
    assert rc == 0

# パターン B: dispatch ロジックだけを検証したいので worktree 解決を mock
def test_dispatch_logic_isolated(tmp_path, monkeypatch):
    repo = _write_repo(tmp_path, provider="local")
    monkeypatch.chdir(repo)
    with patch(
        "kaji_harness.providers.resolve_main_worktree",
        return_value=repo,
    ):
        rc = _handle_issue([...])
    assert rc == 0
```

どちらを使うかはテストの検証対象に応じて選ぶ（§ 方針 §§ テスト移行方針 参照）。

## 制約・前提条件

- **production の `provider.type='local'` は git repo + main worktree を必ず持つ**
  （gl:11 設計書 § 制約・前提条件 / `docs/guides/git-worktree.md:54-60`）。
  本 refactor の正当性はこの前提に依存する。前提が崩れる場合は別 Issue で
  扱う（本 Issue scope 外）。
- **`make check` (= `pytest -m "not large_gitlab"`) は refactor 直後に全 pass
  すること**。Red→Green の遷移は「fallback 削除→影響テスト移行→全 pass」の順で
  進める。large_gitlab は本変更スコープに含まれない。
- **`kaji_harness.providers._worktree.subprocess.run` の名前空間 patch スコープ規定**:
  - **dispatch / provider 結合テスト（`tests/test_phase3c_dispatcher.py` /
    `tests/test_phase4_pr_bare_provider.py` 等、`get_provider()` / `_handle_issue` /
    `_handle_pr` 経路を踏むテスト）では使用禁止**。production と同じ実 `git` を
    呼ばせるか（系統 A: `git init` fixture）、`kaji_harness.providers.resolve_main_worktree`
    自体を mock する（系統 B）。これらの層で `subprocess` 名前空間 patch を使うと、
    本 Issue が解消したい「`MagicMock != 0` truthy 評価による fallback 偶発依存」と
    同型の暗黙依存が再発する。
  - **`resolve_main_worktree()` 自身の Small unit test
    （`tests/test_resolve_main_worktree.py`）では許可**。検証対象が
    `subprocess.run` 呼び出しの戻り値・例外に対する分岐そのもの（`FileNotFoundError`
    / `returncode != 0` → `LocalProviderError` raise）であり、ここで patch を
    禁止すると検証経路が成立しない。これは「関数自身の入力境界を mock する」
    legitimate な unit test であって暗黙依存ではない（mock 対象が検証対象の
    モジュール内 `subprocess.run` 一点に閉じ、テスト側が依存関係を明示する）。
- **gl:11 設計書の整合性維持**: fallback 削除に合わせて gl:11 設計書 § 失敗ケース
  表を書き換える。設計書間の参照整合を維持する。
- **テスト fixture の `git init` は最小限**: `--initial-branch=<default_branch>`
  オプションのみ。`git config user.email` 等は worktree 一覧取得に不要なので
  足さない（fixture を肥らせない）。

## 方針

### 1. `resolve_main_worktree()` の fallback 削除

`kaji_harness/providers/_worktree.py` の以下 2 経路を `LocalProviderError` raise に
置換する:

```python
def resolve_main_worktree(*, start_dir: Path, default_branch: str) -> Path:
    try:
        proc = subprocess.run(
            ["git", "-C", str(start_dir), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise LocalProviderError(
            "git CLI not found on PATH. provider.type='local' requires git. "
            "Install git, or switch provider.type."
        ) from exc
    if proc.returncode != 0:
        raise LocalProviderError(
            f"'git -C {start_dir} worktree list' failed (exit {proc.returncode}). "
            f"provider.type='local' requires a git repository. "
            f"stderr: {proc.stderr.strip() or '(empty)'}"
        )
    # 以下、blocks のパースと matches 抽出は不変
    ...
```

docstring の `Returns` 節からも fallback 言及を削除し、`Raises` 節に上記 2 経路を
追記する。コメントの「test fixture 互換のための明示仕様」も削除する。

### 2. テスト移行方針（影響テストの分類と適用パターン）

影響テストは検証対象に応じて 2 系統に分ける:

**系統 A: 「local provider 配下での dispatch / commit 動線」を実際に検証するテスト**
→ `git init -q --initial-branch=<default_branch>` を fixture に追加。実 git
worktree list が成功し、main worktree が解決される。

- 該当: `tests/test_phase3c_dispatcher.py` の `TestHandleIssueDispatch::test_local_provider_*`
  系、`TestLocalDispatcherIdNormalization` / `TestLocalDispatcherFlags` の
  `local_repo` fixture を使うクラス群
- 既存 fixture `_write_repo(tmp_path, provider="local")` のうち local 経路を
  踏むものに `git init` を後付け、または `local_repo` fixture 内で行う

**系統 B: 「provider 種別の判定ロジック / dispatch 分岐」だけを検証するテスト**
→ `patch("kaji_harness.providers.resolve_main_worktree", return_value=<repo>)` の
局所 mock を with ブロックで適用。worktree 解決の中身は無関心。

- 該当: `tests/test_phase4_pr_bare_provider.py::test_pr_local_provider_blocks_all_subcommands`
  系 (provider=local では `kaji pr` が exit 2 になることだけ確認したい)
- 該当: `tests/test_phase3c_dispatcher.py::TestHandleIssueDispatch::test_no_provider_section_fails_fast_exit_2`
  などの「`provider` セクション不在で exit 2」系（そもそも `get_provider()` まで
  辿り着かないので影響なし。要確認）

**既存テスト `tests/test_resolve_main_worktree.py::test_non_git_dir_falls_back_to_start_dir`**:
→ `test_non_git_dir_raises` 等にリネームし、`pytest.raises(LocalProviderError, match=...)`
で書き換える。message に「git repository」または同等のキーワードが含まれることを
match で検証（actionable error の最低限の証跡）。

### 3. 影響範囲の確定（実装時にベースライン計測の (3) で実施）

設計時の暫定的な影響候補（Issue 本文より）:

- 確認済: `tests/test_phase3c_dispatcher.py` / `tests/test_phase4_pr_bare_provider.py`
  （非 git tmp_path + 実 `git` 呼び出しで fallback 経路を踏む構造）
- 該当する可能性: `tests/test_phase3d_preflight.py` / `tests/test_phase4_provider_type.py`
  / `tests/test_workdir_config.py`

実装時に以下の grep を実行して該当ファイル一覧を確定する:

```bash
grep -rln 'type = "local"' tests/ \
  | xargs grep -L 'git init' \
  | xargs grep -l 'get_provider\|_handle_issue\|_handle_pr'
```

`grep -L 'git init'` で「git init を含まないテストファイル」に絞ることで、すでに
git 初期化済の fixture（例: `tests/test_resolve_main_worktree.py::bare_with_two_worktrees`）
を誤検出しない。

### 4. gl:11 設計書の更新

[`draft/design/issue-11-kaji-issue-comment-worktree-cwd-local-is.md`](issue-11-kaji-issue-comment-worktree-cwd-local-is.md)
の § インターフェース §§ `resolve_main_worktree()` の契約 §§§ 失敗ケース表
(line 113-122) を更新する:

- `git CLI 不在` 行: 振る舞いを「`start_dir.resolve()` を返す」→「`LocalProviderError` を
  raise」に書き換え、`(fallback 採用の根拠)` の段落を削除
- `git worktree list が exit != 0` 行: 同上
- §§§ fallback 採用の根拠 段落 (line 122) を削除し、§§§ 「失敗ケース統一の根拠」
  に置換: 「production は git repo + main worktree を前提とするため、git 不在 /
  非 git は production では到達せず、到達した場合も `LocalProviderError` のほうが
  actionable」と書き直す
- 加えて、gl:21 の follow-up であることを脚注で示す

設計書の変更は本 Issue の `draft/` 内で扱う。`/i-dev-final-check` で
worktree の design ファイルが Issue 本文に添付される際、gl:11 側の改訂版が
gl:21 の design と同時に保存される（既存 archive プロセスを利用）。

### 5. 関連挙動の意図的非対応

以下は本 refactor の scope に含めない:

- `LocalProvider` 自体のシグネチャ変更（`repo_root` を `Optional[Path]` にする等）
- `KajiConfig.discover()` の挙動変更
- `GitHubProvider` / `GitLabProvider` の `repo_root` 解決経路（cwd 起点のまま据え置き）
- 新規 CLI フラグの追加（`--allow-non-git` 等）：Issue § スコープ境界の `feat 禁止`
- `_worktree.py` の他箇所（parser / matches 解決）の変更：振る舞い非変更原則に違反

## テスト戦略

### 変更タイプ
- **実行時コード変更**（Python 実装の変更 + 既存テストの fixture / mock 構造変更）

### Small テスト

`resolve_main_worktree()` の純粋なロジック（subprocess を mock した状態での
分岐遷移）を網羅する。`tests/test_resolve_main_worktree.py` 配下に既存
`TestParseWorktreePorcelain` クラスがあるが、これは parser のみ。本 refactor で
カバーすべき小サイズの観点:

- `subprocess.run` を `patch` し `FileNotFoundError` を raise させたとき、
  `LocalProviderError` が raise されること（message にキーワード含む）
- `subprocess.run` を `patch` し `returncode != 0` の `CompletedProcess` を返した
  とき、`LocalProviderError` が raise されること（message に exit code / stderr 含む）
- 既存の正常系 / `no matching branch` / `multiple matches warning` テストが
  refactor 後も同じ behaviour で pass すること（振る舞い非変更の bridging）

これら 2 つの新規 Red テストを先に追加し、fallback 削除で Green に転じることを
確認する（refactor 中の safety net 兼回帰検出）。

### Medium テスト

`bare_with_two_worktrees` fixture を使う既存 Medium テスト群
（`TestResolveMainWorktree`）は public IF 不変なので原則無改修で pass する想定。
変更が必要なのは:

- 既存 `test_non_git_dir_falls_back_to_start_dir` を `test_non_git_dir_raises` に
  改名し、`pytest.raises(LocalProviderError, match=r"git repository|not a git")`
  で actionable message を assert（bug の Red→Green に相当する「変更前と変更後で
  挙動が異なる」唯一のテスト）

連鎖影響テスト（`test_phase3c_dispatcher.py` / `test_phase4_pr_bare_provider.py`
他）は § 方針 §§ 2 のパターン A / B のいずれかへ移行する。これらは新規追加では
なく、**既存 Medium テストの構造変更**として扱う。観点としては:

- 系統 A 移行後: 実 `git worktree list` が成功し、`get_provider()` が
  `LocalProvider(repo_root=<main_worktree>, ...)` を返すこと（dispatch / commit
  動線が production と同形）
- 系統 B 移行後: `resolve_main_worktree` mock により `get_provider()` が
  worktree 解決を経由せず即 LocalProvider を組むこと（provider 種別判定だけを
  検証）

### Large テスト

不要。判断根拠（`docs/dev/testing-convention.md` の 4 条件）:

1. **独自ロジック追加なし**: 本 refactor は分岐削除のみで、新規 production
   ロジックを増やさない。porcelain parser / worktree matches 抽出は不変
2. **想定不具合パターンは Medium で再現可能**: 「非 git で `LocalProviderError`」
   は実 git CLI + `tmp_path` で確実に再現できる。Large 領域（実外部 API 疎通 /
   subprocess E2E）でしか出ない不具合は本変更経路には存在しない
3. **Large 追加で増える回帰シグナルが小さい**: 既存 `tests/test_phase3e_large_local.py`
   / `tests/test_phase4_large_local.py` は subprocess で `kaji` CLI を呼ぶが、
   それらは「git repo + main worktree」を fixture に持つため fallback 経路を
   通らない。本 refactor で挙動が変わるのは「非 git で `get_provider()` を呼ぶ
   経路」のみで、これは Medium で十分網羅できる
4. **`make check` の標準セット (`pytest -m "not large_gitlab"`) に Large local
   が含まれており、回帰は既存スイートで自動検出される**

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 新規技術選定なし。gl:11 で確定した「main worktree 固定」仕様を実装側で純化するのみ |
| `docs/ARCHITECTURE.md` | なし | LocalProvider / `repo_root` への言及が現状の ARCHITECTURE.md には無く、provider 抽象の章自体が存在しない |
| `docs/dev/` | なし | workflow 構造 / テスト規約への影響なし |
| `docs/reference/python/` | なし | エラー型 (`LocalProviderError`) は既存、message スタイルは既存規約の範囲内 |
| `docs/cli-guides/local-mode.md` | あり（最終確認） | gl:11 で追記したトラブルシュート節に「git 不在 / 非 git → `LocalProviderError`」を追加・明示する必要があるか実装時に確認。既存記述が「main worktree 不在」のみなら 1 行追記、既に網羅していれば変更なし |
| `CLAUDE.md` | なし | 規約は変えない |
| `.claude/skills/*/SKILL.md` | なし | 実装手順 / レビュー観点に影響なし |
| `draft/design/issue-11-kaji-issue-comment-worktree-cwd-local-is.md` | あり（必須） | gl:11 設計書 § 失敗ケース表 / fallback 採用の根拠 を「LocalProviderError 統一」に書き換え。詳細は § 方針 §§ 4 |

`docs/cli-guides/local-mode.md` の判定は実装時に grep
(`grep -n 'worktree\|fallback\|git repository' docs/cli-guides/local-mode.md`)
で確定し、本表を実態に合わせて更新する。設計時点で「あり（最終確認）」と
評価しているのはこの 1 件のみ。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `git-worktree(1)` 公式 — `--porcelain` 出力仕様 | https://git-scm.com/docs/git-worktree#_porcelain_format | "A line-oriented format ... A blank line separates each worktree." → blocks パーサは不変。fallback 撤去後も parse ロジックに変更なし |
| `git-worktree(1)` 公式 — primary worktree の概念 | https://git-scm.com/docs/git-worktree#_description | "The repository ... is called the 'main worktree'. ... Other worktrees are called 'linked worktrees'." → kaji が指す「main worktree」と git の主 worktree 概念が一致する根拠（gl:11 から引き継ぎ） |
| `git-init(1)` 公式 — `--initial-branch` | https://git-scm.com/docs/git-init#Documentation/git-init.txt---initial-branchltbranch-namegt | "Use the specified name for the initial branch ... If not specified, fall back to the default name (currently master)." → テスト fixture で `git init --initial-branch=main` を呼ぶ正当性。`provider.local.default_branch` の既定値 `"main"` と整合させる |
| Python `subprocess` 公式 — `FileNotFoundError` 発生条件 | https://docs.python.org/3/library/subprocess.html#subprocess.Popen | "If `args` is a sequence ... If file cannot be executed, `OSError` (subclass `FileNotFoundError` if the executable is missing) will be raised." → `git` CLI 不在経路で `FileNotFoundError` が確実に raise される根拠（fallback 削除後の Raise 経路の前提） |
| Python `pytest.raises` 公式 — `match` 引数 | https://docs.pytest.org/en/stable/reference/reference.html#pytest.raises | "If `match` is not None, the exception's string representation is searched using `re.search`." → 新規テストで actionable message を regex で assert する根拠 |
| kaji `CLAUDE.md` § Prohibitions / Git & GitHub | [`../../CLAUDE.md`](../../CLAUDE.md) | `make check` を pre-commit ゲートとして要求、`--no-ff` merge の規約。本 refactor の品質ゲートに準拠する |
| kaji `docs/dev/testing-convention.md` § 恒久回帰テストと変更固有検証 | [`../../docs/dev/testing-convention.md`](../../docs/dev/testing-convention.md) | 「実行時の振る舞いを変えるコード変更 → 原則必要」「サイズごとに検証対象が異なる」→ 本 refactor は実行時挙動を変えるため Small + Medium を要求するが Large は 4 条件で省略可能 |
| gl:11 設計書 § 制約・前提条件 / 失敗ケース表 | [`./issue-11-kaji-issue-comment-worktree-cwd-local-is.md`](issue-11-kaji-issue-comment-worktree-cwd-local-is.md) | 「production は `git repo + main worktree` 前提」と「非 git は fallback」が同居しており、本 refactor の解消対象。書き換え後は同設計書が単一の正本となる |
| 既存実装 — `kaji_harness/providers/_worktree.py:53-92` | [`../../kaji_harness/providers/_worktree.py`](../../kaji_harness/providers/_worktree.py) | refactor 対象の本体。fix/11 ブランチ時点 `dc192ba` の line 番号で固定 |
| 既存実装 — `kaji_harness/providers/__init__.py:102-123` | [`../../kaji_harness/providers/__init__.py`](../../kaji_harness/providers/__init__.py) | `get_provider()` の local 分岐。本 refactor は呼び出し側を変えず、`resolve_main_worktree()` の内部実装のみを変える |
| 既存テスト — `tests/test_resolve_main_worktree.py:133-142` | [`../../tests/test_resolve_main_worktree.py`](../../tests/test_resolve_main_worktree.py) | `test_non_git_dir_falls_back_to_start_dir` の現在版。書き換え対象（rename + `pytest.raises`） |
