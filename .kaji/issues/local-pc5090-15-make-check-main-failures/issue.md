---
id: local-pc5090-15
title: make check が main で 10 件 fail する（テストの local-mode CWD 隔離欠落 + Issue 5 fixture
  漏れ）
state: closed
slug: make-check-main-failures
labels:
- type:bug
created_at: '2026-05-09T08:43:01Z'
closed_at: '2026-05-09T09:14:30Z'
closed_by: pc5090
close_reason: completed
---
> [!NOTE]
> **Worktree**: `../kaji-fix-local-pc5090-15`
> **Branch**: `fix/local-pc5090-15`

## 設計書

<details>
<summary>クリックして展開</summary>

# [設計] `make check` を main 直で clean 通過させる（テスト隔離 + Issue 5 fixture 漏れ）

Issue: local-pc5090-15

## 概要

`/home/aki/dev/kaji/main` で `make check` を実行すると 10 件 fail する 2 つの根本原因（A: PR コマンド系テストが CWD の `.kaji/config.local.toml` overlay を隔離していない / B: Issue 5 commit `a60e6f4` で `tests/test_phase4_large_local.py:188` の `requires_provider: gitlab` fixture が更新漏れ）を、テスト側修正のみで同時に解消する。

## 背景・目的

### Observed Behavior (OB)

`source .venv/bin/activate && make check` 末尾:

```
============ 10 failed, 1091 passed, 1 skipped in 61.17s (0:01:01) =============
```

10 件の fail は次の 2 グループに切り分け済み（Issue 本文の調査結果）:

- **A 群（9 件）**: PR コマンド系テスト。`mv .kaji/config.local.toml /tmp/` 後の subset 実行で全て PASS（残り 1 件は B）
- **B 群（1 件）**: `tests/test_phase4_large_local.py::test_validate_rejects_unknown_requires_provider`

#### 原因 A — `_load_config_for_dispatch` が CWD 起点で overlay を拾う

`tests/test_cli_main.py:629-808` 周辺の `_handle_pr` 直呼びテストおよび `tests/test_skill_phase2b_migration.py:122-142` の `main(["pr", ...])` 経路は `kaji_harness.cli_main._handle_pr` を invoke する。`_handle_pr` は冒頭で `_load_config_for_dispatch()`（= `KajiConfig.discover(start_dir=Path.cwd())`）を呼び、`KajiConfig._parse_provider`（`kaji_harness/config.py:175-216`）が `.kaji/config.toml` と `.kaji/config.local.toml` の deep-1 merge を行う。

`.kaji/config.local.toml`（gitignore 対象、`kaji local init` で生成）が `[provider] type = "local"` を含む環境では `_handle_pr:702` の `isinstance(provider, LocalProvider)` ガードで bare-provider error を返して early-exit する:

```
Error: 'kaji pr' is a forge-only command and cannot run under provider.type='local'.
```

結果、9 テストはそれぞれ:

- `test_argv_*`（subprocess 引数 assert）→ `subprocess.run` が呼ばれず assert に到達しない
- `test_*_help_exits_zero` / `test_*_missing_args_exits_two` → argparse の `SystemExit` が発生する前に early-exit、`pytest.raises(SystemExit)` が `DID NOT RAISE`
- `test_missing_gh_returns_runtime_error` / `test_repo_detect_failure_returns_runtime_error` → `_PR_BARE_PROVIDER_ERROR` 経路で `EXIT_INVALID_INPUT (=2)` を返すが、テストは `EXIT_RUNTIME_ERROR (=3)` を assert

worktree 配下では `.kaji/config.local.toml` が（gitignored であるため）存在せず、tracked `.kaji/config.toml`（`type = "github"`, `repo = "apokamo/kaji"`）のみが load される。よって worktree 内の `make check` および従来の CI は通っていた（Issue 5 / 14 マージ時に問題が露呈しなかった理由）。

#### 原因 B — Issue 5 で `gitlab` が valid 値に昇格したが fixture が未更新

`kaji_harness/models.py:79`:

```python
requires_provider: Literal["github", "local", "gitlab", "any"] = "any"
```

`tests/test_phase4_large_local.py:184-194` `test_validate_rejects_unknown_requires_provider` は `requires_provider: gitlab` を fixture に書いたまま「unknown 値が reject される」を assert する。Issue 5（commit `a60e6f4`）で `gitlab` が valid に昇格したため、`requires_provider` 検証では reject されず、後段の skill 解決で別エラー（`SKILL.md not found`）に切り替わり、assertion メッセージが整合しなくなる:

```
E   AssertionError: assert 'requires_provider' in '✗ /tmp/.../bad.yaml\n  - /tmp/.../noop/SKILL.md not found\n'
```

Issue 5 の commit message は `test_phase4_workflow_requires_provider.py` / `test_phase3c_dispatcher.py` の fixture 更新を記録するが、`test_phase4_large_local.py:188` のみ更新漏れ（差分確認済み）。

### Expected Behavior (EB)

#### 原因 A について

`make check` を main 直で実行しても（`.kaji/config.local.toml` で provider=local 設定中でも）`_handle_pr` 系の単体テスト 9 件が PASS する。テスト境界を「PR コマンド argv 組み立てロジック」に固定し、`_load_config_for_dispatch` の CWD 依存を **テスト側で隔離** する。

根拠（一次情報）:
- 同モジュール `tests/test_cli_main.py:778-792` の `TestPrBuiltinDispatch::test_existing_pr_view_fails_when_config_missing` は既に `patch("kaji_harness.cli_main._load_config_for_dispatch", side_effect=ConfigNotFoundError(...))` パターンで隔離されており、これが Phase 4 後の意図と一致する。**同パターンを A 群 9 テストに横展開する** のが既存設計と最も整合する。
- 別パターンとして `tests/test_phase3c_dispatcher.py:155-253`（`_handle_issue` 直呼び）は `monkeypatch.chdir(tmp_repo)` + 一時 `.kaji/config.toml` 書き出しで隔離している。`_handle_pr` 系も同じ前例を持つが、本件のテスト群は「argv 組み立て」が test 主旨であり config 解決はノイズなので、より軽量な「stub config を直接返す」方式（前者）を採用する。

#### 原因 B について

`test_validate_rejects_unknown_requires_provider` が `requires_provider` の **未知の値** を本当に reject することを assert する状態に戻る。fixture の値を Issue 5 後も valid 集合 `{github, local, gitlab, any}` に含まれないリテラル（例: `nonexistent`）に置き換える。

根拠（一次情報）:
- `kaji_harness/models.py:79` の `Literal` 定義
- `git log -p a60e6f4 -- tests/test_phase4_large_local.py` で当該行が変更されていないことを確認可能

## 再現手順（Steps to Reproduce）

1. 前提: `/home/aki/dev/kaji/main/.kaji/config.local.toml` が存在し `[provider] type = "local"` を含む
2. `cd /home/aki/dev/kaji/main && source .venv/bin/activate && make check`
3. 末尾に `10 failed, 1091 passed, 1 skipped` が表示されること
4. 切り分け: `mv .kaji/config.local.toml /tmp/` 後に `pytest tests/test_cli_main.py::TestPrReviewCommentsBuiltin tests/test_cli_main.py::TestPrReviewsBuiltin tests/test_cli_main.py::TestPrReplyToCommentBuiltin tests/test_cli_main.py::TestPrBuiltinDispatch tests/test_skill_phase2b_migration.py::TestPrReviewCommentsCliRunnerIntegration tests/test_phase4_large_local.py::test_validate_rejects_unknown_requires_provider` を実行 → 9 PASS / 1 FAIL（B のみ残る）
5. `mv /tmp/config.local.toml .kaji/config.local.toml` で復元

## 根本原因（Root Cause）

### 原因 A: テスト境界が `_load_config_for_dispatch` を含んでいない

- 影響テスト 9 件は `_handle_pr` を直接呼ぶ in-process テストで、`_handle_pr` 内部の `_load_config_for_dispatch()` 呼び出しを mock していない
- `_load_config_for_dispatch`（`kaji_harness/cli_main.py:724-732`）は `KajiConfig.discover(start_dir=Path.cwd())` を呼び、CWD 起点に config を walk-up 探索する
- `KajiConfig._parse_provider`（`kaji_harness/config.py:175-216`）は tracked `.kaji/config.toml` と untracked `.kaji/config.local.toml` を deep-1 merge する設計（Phase 3-c で導入）。`config.local.toml` の `[provider]` セクションは tracked を完全に上書きする
- 結果: テスト実行時の CWD（pytest 起動ディレクトリ）配下に overlay があると、テストの mock 群（`shutil.which` / `_detect_repo` / `subprocess.run`）に到達する前に bare-provider gate で early-exit する
- いつから壊れているか: `_PR_BARE_PROVIDER_ERROR` 経路は Phase 4 で導入（`local-pc5090-5` 以前の Phase 3 では provider gate なし）。テスト群はそれ以前に書かれており、Phase 4 移行時にテスト側の隔離強化が漏れた

### 原因 B: Issue 5 fixture 移行漏れ（Phase 4 commit `a60e6f4` の積み残し）

- `kaji_harness/models.py:79` の `requires_provider: Literal["github", "local", "gitlab", "any"]` で `gitlab` が valid に昇格
- `tests/test_phase4_large_local.py:188` の fixture は Issue 5 着手前に「未知の値の代表」として `gitlab` を採用していたが、Issue 5 commit `a60e6f4` で 2 ファイル（`test_phase4_workflow_requires_provider.py` / `test_phase3c_dispatcher.py`）の fixture を更新する一方、本ファイルが見落とされた
- いつから壊れているか: commit `a60e6f4`（`feat: add GitLabProvider + config + dispatcher (local-pc5090-5)`）以降

### 同根欠陥の波及調査

- `_handle_issue` 直呼びテスト（`tests/test_phase3c_dispatcher.py`）は `monkeypatch.chdir(tmp_repo)` + 専用 `.kaji/config.toml` で隔離済（CWD 依存を断ち切っている）
- `cmd_run` / `cmd_validate` / `cmd_config_provider_type` 系テストは `--workdir` 引数または `monkeypatch.chdir(tmp_path)` を使用しており CWD 依存なし
- 残る穴は本 Issue で対象とする 9 テストのみ（`grep -rn "_handle_pr" tests/` で確認、本設計の検証時にも実装段階で再確認する）
- `requires_provider` fixture の他の用例（`grep -rn "requires_provider" tests/`）は valid 値（github / local / gitlab / any）または既に正しい unknown 値を使っており、本ケースのみが移行漏れ

## インターフェース

bug 修正のため公開 IF は変更しない。テスト内部の fixture / patch 範囲のみ変更する。

- 変更対象は `tests/` 配下のみ
- `kaji_harness/cli_main.py` `_handle_pr` / `_load_config_for_dispatch` の挙動は変更しない（プロダクション側の Phase 4 fail-fast 契約は維持）
- 後方互換性: production には影響なし

## 変更スコープ

- `tests/test_cli_main.py`（`TestPrReviewCommentsBuiltin` / `TestPrReviewsBuiltin` / `TestPrReplyToCommentBuiltin` / `TestPrBuiltinDispatch` のうち 8 テスト）
- `tests/test_skill_phase2b_migration.py`（`TestPrReviewCommentsCliRunnerIntegration` 1 テスト）
- `tests/test_phase4_large_local.py`（fixture 1 行）

`kaji_harness/` への変更なし。`tests/conftest.py` への autouse 追加もしない（後述の理由）。

## 方針（修正アプローチ）

### 原因 A: 各 test class 内に **function-scoped** autouse fixture を nest して `_load_config_for_dispatch` を stub する

各影響クラスの内部に **`@pytest.fixture(autouse=True)`（scope は明示しない = pytest デフォルトの function scope）** を method として定義し、`monkeypatch.setattr` で `_load_config_for_dispatch` を「github 設定の `KajiConfig` を返す callable」に置き換える。

#### 用語の正確な定義（review-design RETRY 反映）

「class 内に nest する」と「`scope="class"`」は別概念であり、本設計では混同しない:

- **scope（fixture のライフサイクル）**: 本設計は **`scope="function"`（pytest デフォルト、明示指定しない）** を採用する。`pytest.MonkeyPatch` fixture は function-scoped であり、`scope="class"` を指定すると `ScopeMismatch: You tried to access the function scoped fixture monkeypatch with a class scoped request object.` で fail する。よって `scope="class"` は採用しない
- **適用範囲（fixture が autouse で動く対象テストの集合）**: fixture を class の method として nest 定義することで、その class 内の test method にのみ autouse が及ぶ（pytest の標準仕様。class 外で定義された fixture は同 module の全 test に autouse される）。本設計の「affected class のみに限定する」要件はこの nest 定義で満たす

要約すると、本設計の autouse fixture は「**function scope** で **affected class 内に nest 定義**」する。

#### 採用根拠

1. **既存パターンとの整合**: 同 file `tests/test_cli_main.py:783-787` の passing test `test_existing_pr_view_fails_when_config_missing` が同一の patch target（`_load_config_for_dispatch`）を使っている。横展開はパターン継承のみで完結する
2. **テスト主旨の明確化**: 9 テストはいずれも「argv 組み立て / 引数バリデーション / argparse exit code」を検証する。config 解決はテスト主旨ではないため、boundary を `_handle_pr` の内部呼び出し点でカットするのが意図と整合
3. **ambient state からの独立**: CWD / overlay / tracked config いずれの変更にも非依存になり、worktree 環境変動（gitignored overlay の存否）に対する耐性が増す
4. **`tests/conftest.py` を汚染しない**: 全テスト共通の autouse にすると `test_existing_pr_view_fails_when_config_missing`（config-not-found を意図的に発生させる test）や `test_phase3c_dispatcher.py` 系（実 config を tmp に置く test）と衝突する可能性。**class 内 nest 定義** で適用範囲を局所化する
5. **`monkeypatch.setattr` と `with patch` の共存**: `test_existing_pr_view_fails_when_config_missing` は内部で `with patch(...)` を使っているため、autouse の `monkeypatch.setattr` を test 内 `with patch` がさらに上書きする形で動作する（pytest の patch stack 仕様）。autouse を入れても既存挙動を破壊しない

#### 擬似コード（実装段階で TDD する）

```python
# tests/test_cli_main.py に共通 helper を追加
def _stub_github_config() -> KajiConfig:
    return KajiConfig(
        repo_root=Path("/tmp/stub"),
        paths=PathsConfig(skill_dir=".claude/skills", artifacts_dir=".kaji/artifacts"),
        execution=ExecutionConfig(default_timeout=1800),
        provider=ProviderConfig(
            type="github",
            local=LocalProviderConfig(),
            github=GitHubProviderConfig(repo="owner/repo"),
        ),
    )

# 各 affected class の method として nest 定義（scope は省略 = function scope）
class TestPrReviewCommentsBuiltin:
    @pytest.fixture(autouse=True)
    def _isolate_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # function-scoped: monkeypatch も function-scoped で整合する
        # nest 定義: TestPrReviewCommentsBuiltin の test_* method のみに適用
        monkeypatch.setattr(
            "kaji_harness.cli_main._load_config_for_dispatch",
            _stub_github_config,
        )
    # ... 既存テストはそのまま
```

`scope="class"` を使わない理由（review-design 指摘反映）:

```python
# ❌ 採用しない: ScopeMismatch エラーになる
@pytest.fixture(scope="class", autouse=True)
def _isolate_config(self, monkeypatch):  # monkeypatch は function-scoped
    ...
```

代替案として `pytest.MonkeyPatch()` を手動 instantiate して `scope="class"` 化する手段も検討したが、

- function-scoped autouse でも実害（class 内 test 数 × stub 生成回数 = 軽量 dataclass 構築 × 9〜数個）は無視できる
- 手動 `MonkeyPatch()` は finalizer 管理（`undo()` を `yield` 後に呼ぶ等）が必要で実装が冗長になる
- 採用パターン（function scope + class 内 nest）が `tests/test_phase3c_dispatcher.py` の既存 isolation 設計（`monkeypatch.chdir` を function fixture で都度設定）と粒度が一致する

ため **不採用** とする。

#### 設計上の trade-off

- **stub config の `provider.github.repo` 値**: テストが mock する `_detect_repo.return_value` と一致させると、`_handle_pr` が `repo_override = "owner/repo"` を `_dispatch_pr_builtin` に渡し、最終的に `_detect_repo(override="owner/repo")` がコールされる。`_detect_repo` 自体が mock されているため `return_value` がそのまま採用され、`repos/owner/repo/...` パスが組み立てられる（既存 assert 式と整合）
- **既存 `_detect_repo` mock との関係**: stub config の repo 値と test の `_detect_repo` mock の return_value が異なる場合、後者が優先される（`_detect_repo` 関数全体が mock されているため）。よって既存テストの assert 式（`cmd[2] == "repos/owner/repo/..."` 等）を破壊しない
- **`test_repo_detect_failure_returns_runtime_error` の互換性**: このテストは `_detect_repo` を `return_value=None` で mock。stub config が `repo="owner/repo"` を返しても、`_dispatch_pr_builtin` → `_forward_pr_api_list` 内で `_detect_repo(override="owner/repo")` が呼ばれ、mock が `None` を返すため `EXIT_RUNTIME_ERROR` 経路に到達する（既存 assert と整合）

### 原因 B: fixture 値を unknown リテラルに置換

```diff
-        "requires_provider: gitlab\n"
+        "requires_provider: nonexistent\n"
```

#### 採用根拠

- `kaji_harness/models.py:79` の `Literal["github", "local", "gitlab", "any"]` に含まれない任意の文字列で OK（`unknown` / `bogus` / `nonexistent` 等）
- `nonexistent` を選ぶ理由: 「現存しない / 未来も valid に昇格しない」意図が明示的。`unknown` は曖昧さがあり、`bogus` は口語的なので冗長
- 1 行修正で完結し、テスト挙動の根本意図（unknown 値の reject 検証）を維持

## テスト戦略

### 変更タイプ

実行時コード変更**なし**。テストフィクスチャ修正および test isolation の追加のみ。ただし「既存の壊れた 10 テストが PASS する」状態への遷移自体が検証対象。

### Small テスト

- 既存 9 テスト（A 群）が `_load_config_for_dispatch` stub fixture 適用後に PASS することを確認
- 既存 1 テスト（B 群、`test_validate_rejects_unknown_requires_provider`）が fixture 修正後に PASS することを確認
- 既存 passing test `test_existing_pr_view_fails_when_config_missing` が autouse fixture 追加後も継続 PASS（`with patch(side_effect=ConfigNotFoundError)` が autouse `monkeypatch.setattr` を上書きする pytest 仕様の検証）
- **fixture scope 整合性の事前確認**: 実装段階で fixture 追加時にまず `pytest tests/test_cli_main.py::TestPrReviewCommentsBuiltin -x` を 1 件流し、`ScopeMismatch` が出ないことを確認する（function scope + monkeypatch の整合は pytest 標準仕様だが、書き間違いを早期検出するため）

### Medium テスト

- 不要。本変更は test isolation の修正であり、新規 production logic を導入しない。`_handle_pr` 自身の Medium テスト（`test_phase3c_dispatcher.py` の `_handle_issue` 系類似）は本 Issue のスコープ外
- 既存 Medium テストの regression 確認: `make check` 全件で `0 failed`

### Large テスト

- 不要。production 挙動の変更がないため

### 再現テスト（bug 固有要件）

- A 群: 既存 9 テストが「修正前は 10 failed のうち 9 件として fail / 修正後は PASS」する事実が再現テストの役割を果たす（追加テストは作らず、既存 test を Red → Green の証跡に使う）
- B 群: 既存 `test_validate_rejects_unknown_requires_provider` が fixture 修正前後で同様に Red → Green を示す
- Red 確認: 修正前の `make check` 出力（Issue 本文記載の 10 failed ログ）を一次情報として参照
- Green 確認: 実装段階で `make check` を main 直で実行し `0 failed` を確認

### CWD 依存リグレッション保護

- `make check` が main 直（overlay 存在）と worktree（overlay 不在）の両環境で clean に通ることを実装段階で確認する
- 環境差検証手順:
  1. `cd /home/aki/dev/kaji/main && source .venv/bin/activate && make check`（overlay あり）
  2. `cd /home/aki/dev/kaji/kaji-fix-local-pc5090-15 && source .venv/bin/activate && make check`（overlay なし）

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | なし | ワークフロー / 開発手順変更なし |
| docs/dev/testing-convention.md | なし | テスト規約自体は変更しない（既存規約に沿った修正） |
| docs/reference/ | なし | API 仕様 / 規約変更なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | なし | 規約変更なし |

設計書 promote 不要（恒久的な docs として残す価値のある決定はなく、修正手順は本設計書 + 実装 commit message に閉じる）。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `kaji_harness/cli_main.py:673-732` | `/home/aki/dev/kaji/kaji-fix-local-pc5090-15/kaji_harness/cli_main.py` | `_handle_pr` 冒頭で `_load_config_for_dispatch()` を呼び、`isinstance(provider, LocalProvider)` で bare-provider error を返す Phase 4 fail-fast 経路の確認元 |
| `kaji_harness/config.py:175-216` | `/home/aki/dev/kaji/kaji-fix-local-pc5090-15/kaji_harness/config.py` | `KajiConfig._parse_provider` が `.kaji/config.toml` と `.kaji/config.local.toml` を deep-1 merge する設計の確認元（`config.local.toml` の `[provider]` 全体が tracked を上書きする） |
| `kaji_harness/models.py:79` | `/home/aki/dev/kaji/kaji-fix-local-pc5090-15/kaji_harness/models.py` | `requires_provider: Literal["github", "local", "gitlab", "any"]`。`gitlab` が valid 値であることの根拠 |
| `tests/test_cli_main.py:778-792` | 同 worktree 内 | 既存 passing test `test_existing_pr_view_fails_when_config_missing` の `patch("kaji_harness.cli_main._load_config_for_dispatch", side_effect=...)` パターン（採用パターンの先行例） |
| `tests/test_phase3c_dispatcher.py:155-253` | 同 worktree 内 | `_handle_issue` 直呼びテストの `monkeypatch.chdir(tmp_repo)` + tmp config 隔離パターン（採用候補だが本件では不採用とした alternative） |
| `tests/conftest.py:105-132` | 同 worktree 内 | 既存の autouse fixture が `WorkflowRunner.__post_init__` 限定であり、`_load_config_for_dispatch` には介入していないことの確認元 |
| Issue 5 commit `a60e6f4` | `git log -p a60e6f4 -- tests/test_phase4_large_local.py` | `feat: add GitLabProvider + config + dispatcher (local-pc5090-5)` の差分が `test_phase4_large_local.py:188` を含まないことの確認元（fixture 移行漏れの根拠） |
| Issue 本文 `make check` 実測ログ | `.kaji/issues/local-pc5090-15-make-check-main-failures/issue.md` § OB | `10 failed, 1091 passed` の一次出力。切り分け subset 実行 `1 failed, 16 passed in 0.84s` も同節に記載 |
| pytest 公式 — `monkeypatch` fixture | https://docs.pytest.org/en/stable/how-to/monkeypatch.html | 「`monkeypatch` is a function-scoped fixture」（要約）。本設計が `scope="class"` を採用しない根拠（review-design RETRY 反映） |
| pytest 公式 — fixture scopes | https://docs.pytest.org/en/stable/how-to/fixtures.html#fixture-scopes | scope 上位（class/module/session）の fixture が scope 下位（function）の fixture を request すると `ScopeMismatch` で fail することの一次情報 |

</details>

## 概要

`/home/aki/dev/kaji/main` で `source .venv/bin/activate && make check` を実行すると 10 件のテストが fail する。原因は 2 つあり（A: テストが CWD の `.kaji/config.local.toml` overlay を隔離していない / B: Issue 5 の fixture 移行漏れ）、両方を `make check` clean 通過の前提条件として同時に解消する。

## 目的

### Observed Behavior（OB）

`source .venv/bin/activate && make check` 末尾:

```
FAILED tests/test_cli_main.py::TestPrReviewCommentsBuiltin::test_missing_gh_returns_runtime_error
FAILED tests/test_cli_main.py::TestPrReviewCommentsBuiltin::test_repo_detect_failure_returns_runtime_error
FAILED tests/test_cli_main.py::TestPrReviewCommentsBuiltin::test_argv_uses_reviews_path  # （TestPrReviewsBuiltin 配下）
FAILED tests/test_cli_main.py::TestPrReviewCommentsBuiltin::test_argv_contains_repo_path_and_composed_jq
FAILED tests/test_cli_main.py::TestPrReviewCommentsBuiltin::test_argv_omits_jq_when_neither_flag
FAILED tests/test_cli_main.py::TestPrReplyToCommentBuiltin::test_argv_contains_post_method_and_body
FAILED tests/test_cli_main.py::TestPrBuiltinDispatch::test_review_comments_help_exits_zero
FAILED tests/test_cli_main.py::TestPrBuiltinDispatch::test_review_comments_missing_args_exits_two
FAILED tests/test_skill_phase2b_migration.py::TestPrReviewCommentsCliRunnerIntegration::test_review_comments_invokes_gh_with_composed_jq
FAILED tests/test_phase4_large_local.py::test_validate_rejects_unknown_requires_provider
============ 10 failed, 1091 passed, 1 skipped in 61.17s (0:01:01) =============
```

#### 内訳と切り分け

**原因 A: テストが CWD の `.kaji/config.local.toml` overlay を隔離していない（9 件）**

上記 1〜9 番（PR コマンド系）。`mv .kaji/config.local.toml /tmp/` してから当該テストを再実行すると 9 件すべて PASS することを確認済み（`1 failed, 16 passed in 0.84s` — 残り 1 件は原因 B）。

これらは `kaji_harness.cli_main._handle_pr` を直接呼ぶ in-process テスト。`_load_config_for_dispatch` が CWD 起点で config 探索を行うため、main 直下で実行すると `.kaji/config.local.toml`（`[provider] type = "local"`）が見つかり provider gate で early-exit する:

```
Error: 'kaji pr' is a forge-only command and cannot run under provider.type='local'.
Pull request concept does not exist in local mode (bare provider). ...
```

その結果、argparse の `SystemExit` を期待するテスト（`test_review_comments_help_exits_zero` 等）は `DID NOT RAISE` で fail し、特定の subprocess 引数を assert するテスト（`test_argv_uses_reviews_path` 等）は subprocess 自体が呼ばれず assert に到達せず fail する。

**原因 B: Issue 5 の fixture 移行漏れ（1 件）**

10 番 `tests/test_phase4_large_local.py:185-193 test_validate_rejects_unknown_requires_provider`。Issue 5（commit `a60e6f4 feat: add GitLabProvider + config + dispatcher (local-pc5090-5)`）の commit message は `test_phase4_workflow_requires_provider.py` と `test_phase3c_dispatcher.py` の fixture 更新を記録しているが、`test_phase4_large_local.py:188` の `requires_provider: gitlab` fixture は更新漏れ。

このテストは「未知の値が rejected される」ことを assert するが、Issue 5 で `gitlab` が valid 値に昇格したため reject されず、別のエラー（`SKILL.md not found`）で先に validate が失敗する:

```
E   AssertionError: assert 'requires_provider' in '✗ /tmp/.../bad.yaml\n  - /tmp/.../noop/SKILL.md not found\n'
```

### Expected Behavior（EB）

#### 原因 A について

`make check` を main 直で実行しても（`.kaji/config.local.toml` で provider=local 設定中でも）PR コマンド系の単体テスト 9 件が PASS する。テストが CWD-level config overlay を意図的に上書き / 隔離する仕組み（conftest fixture / monkeypatch / 環境変数等）を持つ。

根拠:
- 既存の同モジュール内テスト（`with patch("kaji_harness.cli_main.WorkflowRunner")` を使う `cmd_run` 系）は in-process patch 方式で隔離されており、PR コマンド系テストもこれと整合する形で `_load_config_for_dispatch` を隔離するのが本来の意図と推測される。`tests/test_cli_main.py:780-810` 付近の `_handle_pr` 直呼びテストはこの隔離が抜けている。
- 設計判断は本 Issue の設計フェーズで詰める（隔離単位 / mock 方針 / conftest 配置 / 既存 patch パターンとの整合性）。

#### 原因 B について

`test_validate_rejects_unknown_requires_provider` が `requires_provider` の **未知の値** を本当に reject することを assert する状態に戻る。fixture の値を Issue 5 後も unknown なリテラル（例: `nonexistent` / `unknown` / `bogus`）に置き換える。

根拠:
- 同テストは Issue 5 commit message に列挙された fixture 更新対象 2 ファイルと同じ「Issue 5 で valid に昇格した `gitlab` を unknown 値の代表として参照していた」パターン。Issue 5 のスコープに含まれていた更新の見落とし（commit a60e6f4 の差分に当該行が無いことを確認済み）。

### 再現手順（Steps to Reproduce）

1. 前提: `/home/aki/dev/kaji/main` に `.kaji/config.local.toml` が存在し `[provider] type = "local"` が設定されている（kaji 自身を local-mode で開発している環境）
2. `source .venv/bin/activate && make check` を実行
3. 末尾に `10 failed, 1091 passed, 1 skipped` が表示されることを確認
4. `mv .kaji/config.local.toml /tmp/` してから `pytest tests/test_cli_main.py::TestPrReviewCommentsBuiltin tests/test_cli_main.py::TestPrReviewsBuiltin tests/test_cli_main.py::TestPrReplyToCommentBuiltin tests/test_cli_main.py::TestPrBuiltinDispatch tests/test_skill_phase2b_migration.py::TestPrReviewCommentsCliRunnerIntegration tests/test_phase4_large_local.py::test_validate_rejects_unknown_requires_provider` を実行すると 9 PASS / 1 FAIL（原因 B のみ残る）。
5. `mv /tmp/config.local.toml.bak .kaji/config.local.toml` で復元。

## 完了条件

### 設計段階で確認

- [x] 原因 A: テストでの config 隔離方式が決定されている（conftest.py に fixture 追加 / `_load_config_for_dispatch` の monkeypatch 統一 / 環境変数による config-path 強制 等の中から選定）
- [x] 原因 A: 隔離方式が PR コマンド系以外の他 `tests/test_*.py` にも波及すべきか調査結果が設計書に記載されている（同種の CWD 依存が他のテストに無いか網羅的に確認済み）
- [x] 原因 A: 既存の `with patch("kaji_harness.cli_main.WorkflowRunner")` パターンと整合する設計になっている
- [x] 原因 B: fixture 値の置換先（unknown リテラル）が決定されている

### 実装段階で確認

- [x] 原因 A の再現テスト（または既存 9 テスト）が、`.kaji/config.local.toml`（provider=local）配下で PASS する
- [x] 原因 A: `.kaji/config.local.toml` を退避した状態でも `make check` が clean 通過する（既存の通過経路を破壊しない）
- [x] 原因 B: `test_validate_rejects_unknown_requires_provider` が PASS する
- [x] `make check` clean 通過（原因 A・B の両方が解消した状態、`/home/aki/dev/kaji/main` での実行で `0 failed`）
- [x] 同根の他の壊れ箇所の調査結果が設計書に記載されている（PR コマンド以外の builtin 系コマンドでも同種の CWD 依存が起きないか）
- [x] 影響モジュール全体のテストが green（デグレ無し）

## 影響範囲（初期評価）

- 影響するモジュール / コマンド:
  - 主: `tests/test_cli_main.py`（PR コマンド系 8 テスト）
  - 主: `tests/test_skill_phase2b_migration.py`（`TestPrReviewCommentsCliRunnerIntegration` 1 テスト）
  - 主: `tests/test_phase4_large_local.py:185-193`（fixture 1 行）
  - 副: `tests/conftest.py` 新設または既存追記の可能性
  - 副: `kaji_harness/cli_main.py::_load_config_for_dispatch`（テスト時 mock 経路の整理が必要なら）
- 深刻度: medium-high
  - main 直での `make check` が常時 fail し、kaji 自身を local-mode で開発する全ユーザーで開発体験を悪化させる
  - Issue 5 の "completed" close は原因 B により前提崩れ（修正対象が残っている状態で close された）
- 回避策の有無:
  - 原因 A: `.kaji/config.local.toml` を一時退避すれば 9 件は通る（worktree 内で開発する限りは worktree が overlay を継承しないため `make check` は通る — issue 5 / 14 のマージ時に CI 相当が通っていた理由）
  - 原因 B: fixture を一時的に手元で書き換える以外なし

## 参考

- 関連 commit:
  - `a60e6f4 feat: add GitLabProvider + config + dispatcher (local-pc5090-5)` — 原因 B の発生元。commit message が `test_phase4_workflow_requires_provider.py` / `test_phase3c_dispatcher.py` の fixture 更新を記録するも `test_phase4_large_local.py:188` を見落とし（差分確認済み）
- 関連 Issue:
  - `local-pc5090-5`（completed close 済みだが原因 B の修正漏れがある。本 Issue で完全クリーンアップする。Issue 5 を reopen はしない）
  - `local-pc5090-4`（EPIC: GitLab 対応検証）— 原因 A は本 EPIC のスコープ外、原因 B は本 EPIC の子 Issue 5 の積み残し
- 関連コード:
  - `kaji_harness/cli_main.py::_handle_pr`（PR コマンド dispatch + provider gate）
  - `kaji_harness/cli_main.py::_load_config_for_dispatch`（CWD 起点の config 探索）
  - `tests/test_cli_main.py:780-810` 付近（`_handle_pr` を直接呼ぶテスト群、TestPrBuiltinDispatch）
  - `tests/test_phase4_large_local.py:185-193`（fixture 漏れ箇所）
- 1 次情報（実行ログ）:
  - `make check` 末尾出力: `10 failed, 1091 passed, 1 skipped in 61.17s`
  - `.kaji/config.local.toml` 退避後の subset 実行: `1 failed, 16 passed in 0.84s`（原因 B のみ残ることを確認済み）
- 関連ドキュメント:
  - `docs/dev/testing-convention.md` / `docs/reference/testing-size-guide.md`（test 設計方針との整合確認）
  - 設計書配置予定: `draft/design/issue-local-pc5090-15-make-check-main-failures.md`
