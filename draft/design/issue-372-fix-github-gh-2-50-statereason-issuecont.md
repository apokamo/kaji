# [設計] GitHub provider に gh 最低 version (>= 2.50.0) の preflight 検査を追加する

Issue: #372

## 概要

`GitHubProvider` が `gh` を起動する前に `gh --version` を 1 度だけ検査し、`gh < 2.50.0`
では検出 version・必要 version・理由・公式インストール URL を含む `GitHubProviderError`
で停止する。これにより `Unknown JSON field: "stateReason"` という GitHub CLI 内部由来の
不可解なエラーで `kaji run` が落ちる状態を解消する。

## 背景・目的

### Observed Behavior (OB)

`gh 2.45.0` / `kaji 0.15.0` の環境で `kaji run` を実行すると、Issue の内容や workflow の
種類にかかわらず IssueContext 解決の時点で失敗し、agent dispatch に到達しない
（一次情報: Issue #372 本文 `## 壊れた挙動（OB / Observed Behavior）` の実行ログ）。

```text
Error: Failed to resolve IssueContext for '1461' under provider.type='github':
GitHubProviderError: gh failed (exit 1): Unknown JSON field: "stateReason"
Available fields:
  assignees
  author
  body
  ...
```

このエラーの問題は 3 点。

1. 失敗原因が「`gh` が古いこと」だと読み取れない。メッセージは GitHub CLI が出す field
   allowlist の羅列で、kaji 側の要件（最低 version）に言及しない。
2. 復旧手段（`gh` の更新）が示されない。
3. `create_issue()` / `edit_issue()` / `close_issue()` は末尾で `view_issue()` を呼ぶ
   （`kaji_harness/providers/github.py:270` / `309` / `332`）ため、GitHub 側の mutation が
   成功した後に再取得だけが失敗する。利用者からは操作全体が失敗したように見える。

根本原因は `view_issue()` が `--json ...,stateReason,...` を要求している
（`kaji_harness/providers/github.py:280`）一方で、kaji の prerequisites が「認証済み `gh`」
のみを定義し、最低対応 version を定義・検査していなかったことにある。`stateReason` 要求は
sequential series runner（#313, commit `49c9a28`）で追加されたが、共通 `view_issue()` に
入ったため通常の IssueContext 解決にも波及した。

### Expected Behavior (EB)

- `gh >= 2.50.0` では IssueContext 解決も close reason gate も現行どおり動作する（回帰なし）。
- `gh < 2.50.0` では `gh` を 1 度も業務実行せず副作用を起こす前に停止し、
  検出 version / 必要 version / その version を要求する理由 / 公式インストール手順 URL
  (<https://github.com/cli/cli#installation>) を含む `GitHubProviderError` を返す。
- `gh --version` の出力を解析できない場合は素通り（fail-open）し、独自ビルド利用者を
  無条件に弾かない。
- README / GitHub mode guide (en / ja) の prerequisites に最低 version が記載される。
- 新しい `--json` field / gh flag を採用する際に最低 version 定数と docs を同時に更新する
  ルールが dev guide に記載される。

### 目的の境界

対象は GitHub provider の互換性・診断。`stateReason` を要求する field 列そのもの、
`IssueProvider` protocol、series の gate 仕様は変更しない
（Issue #372 `## 目的` / `## 決定した方針`）。

## 再現手順

前提条件:

- OS: Ubuntu 24.04 (Noble)
- kaji: `0.15.0`
- provider: `github`（`provider.type: github`）
- GitHub CLI: `2.45.0`（`gh auth status` は認証済み）

手順:

1. `gh --version` を実行し `gh version 2.45.0` を確認する。
2. GitHub provider 設定のリポジトリで `uv run kaji run .kaji/wf/dev-fable.yaml 1461` を実行する。
3. 観測結果: 上記 OB のログで終了する。agent は 1 つも起動しない。

最小再現（kaji を介さない切り分け）:

1. `gh issue view 1461 -R <owner>/<name> --json stateReason` を実行する。
2. 観測結果: exit 1 で `Unknown JSON field: "stateReason"` が出力される。`gh 2.50.0` 以降では成功する。

本設計での再現は上記を Small テストへ写像する（後述「テスト戦略」）。実 `gh 2.45.0` binary は
CI に持ち込まず、`gh --version` の応答を差し替えて同じ分岐を駆動する。

## 根本原因（Root Cause）

| 観点 | 内容 |
|------|------|
| なぜ間違っているか | `_run_gh()`（`github.py:99-117`）は `shutil.which("gh")` で **存在** のみ検査し、**version** を検査しない。`view_issue()` が要求する `stateReason` は gh v2.50.0 で `api/query_builder.go` の `IssueFields` に追加された field であり、それ未満の gh は allowlist 検査で exit 1 になる |
| いつから壊れているか | `stateReason` を `view_issue()` に追加した #313 / commit `49c9a28`（sequential series runner）以降。gh >= 2.50.0 の開発環境では顕在化しなかった |
| 同じ原因で他に壊れている箇所 | `stateReason` の要求は `github.py:280` の 1 箇所のみ。`list_issues()` は `stateReason` を要求せず、`_parse_issue_payload()` は `payload.get("stateReason", "")` で欠落を許容する（`github.py:240`）。kaji が使う他の gh 機能の下限は `gh api --paginate --slurp`（`github.py:139`）の v2.48.0 で、2.50.0 に包含される。provider 外の gh 起動箇所（`commands/pr.py` の passthrough / `gh pr list --json` / `gh api repos/...`、`kaji_harness/sync.py:166` の `gh api -X GET`、`kaji_harness/scripts/codex_review_poll.py:156` の `gh api --paginate`）はいずれも v2.50.0 を要する機能を使わない |
| 既存テストが検出できなかった理由 | provider テストは `subprocess.run` を mock して成功 JSON を返すため、実 `gh` の `--json` field allowlist との互換性を検出できない |

## インターフェース

公開 IF（CLI 引数、終了コード、`IssueProvider` protocol、`Issue` model、`view_issue()` の
`--json` field 列）は **すべて不変**。追加されるのは失敗経路のみ。

### 入力

- 環境の `gh` binary（`gh --version` の stdout）
- 既存の provider 設定（変更なし）

### 出力

- `gh >= 2.50.0` / version 解析不能: 従来どおりの戻り値（振る舞い不変）
- `gh < 2.50.0`: `GitHubProviderError`（既存例外型。新しい例外型は追加しない）

エラーメッセージは既存 provider メッセージと同じく英語で、次の 4 要素を含む。

```text
gh 2.45.0 is too old for provider.type='github' (kaji requires gh >= 2.50.0).
kaji requests the `stateReason` JSON field when reading issues, which gh added
in v2.50.0; older gh exits with `Unknown JSON field: "stateReason"`.
Upgrade GitHub CLI: https://github.com/cli/cli#installation
```

4 要素はいずれも Issue #372 `## あるべき挙動（EB）` が要求する契約であり、恒久テストで
検証する。要素と assert 対象 token の対応は次のとおり（テスト戦略 Small 観点 1 と 1 対 1）。

| 要素 | assert 対象 token | 欠落したときに起きること |
|------|-------------------|--------------------------|
| 検出 version | `2.45.0` | 利用者が自環境の gh version を特定できない |
| 必要 version | `2.50.0` | 更新先が不明で復旧できない |
| その version を要求する理由 | `stateReason`（および旧版で当該 field が利用不能である旨） | OB の `Unknown JSON field: "stateReason"` と結び付かず、なぜ 2.50.0 なのかが不明のまま |
| 公式インストール手順 | `https://github.com/cli/cli#installation` | 復旧手段が示されない |

### 追加する内部 IF（すべて private）

| 名前 | 種別 | 役割 |
|------|------|------|
| `_MIN_GH_VERSION: Final[tuple[int, int, int]]` | module 定数 | `(2, 50, 0)`。最低 version の単一情報源 |
| `_GH_VERSION_RE` | module 定数 | `gh --version` 1 行目の parse |
| `_GH_INSTALL_URL` | module 定数 | `https://github.com/cli/cli#installation` |
| `GitHubProvider._detect_gh_version()` | private method | `gh --version` を起動し `(major, minor, patch)` または `None`（解析不能）を返す |
| `GitHubProvider._ensure_gh_version()` | private method | memo 済み結果で判定し、未満なら `GitHubProviderError` |
| `_gh_version` / `_gh_version_probed` | dataclass field（`init=False`） | インスタンス内 memo |

### 使用例

```python
# gh 2.96.0 の環境 — 従来どおり
provider = GitHubProvider(repo="owner/name", repo_root=Path("/repo"))
issue = provider.view_issue("372")

# gh 2.45.0 の環境 — 業務 gh 実行前に停止
provider.view_issue("372")
# GitHubProviderError: gh 2.45.0 is too old for provider.type='github'
#   (kaji requires gh >= 2.50.0). ... https://github.com/cli/cli#installation

provider.create_issue(title="t", body="b")
# 同上。`gh issue create` は 1 度も起動されないため mutation は発生しない
```

## 制約・前提条件

- `gh --version` の stdout 1 行目は `gh version <major>.<minor>.<patch> (<date>)` 形式
  （実機確認: 本設計時点の開発機 `gh version 2.96.0 (2026-07-02)` / exit 0）。
- version 検査は `_run_gh()` の内部にあるため、`gh --version` の probe 自身は `_run_gh()` を
  経由できない（無限再帰）。probe は `subprocess.run` を直接呼ぶ。
- probe は `kaji_harness.providers.github.subprocess.run` 経由とする。既存テストの
  `patch("kaji_harness.providers.github.subprocess.run")` が probe も覆うため、テストが
  意図せず実 `gh` を起動することはない。
- オーバーヘッドは provider インスタンスあたり `gh --version` subprocess 1 回のみ。
- 検査対象は `GitHubProvider._run_gh()` を通る経路（`kaji issue` / `kaji run` の
  IssueContext 解決 / series gate）。`kaji pr` passthrough・`kaji sync from-github`・
  `codex_review_poll` は provider を経由せず、かつ v2.50.0 を要する gh 機能を使わないため
  対象外（根拠は上記「根本原因」表）。
- 依存追加なし（`re` / `subprocess` は標準ライブラリ）。

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/providers/github.py` | 定数 3 件、`_detect_gh_version()` / `_ensure_gh_version()`、memo field 2 件、`_run_gh()` への 1 行呼び出し |
| `tests/test_providers_github.py` | `TestGhVersionPreflight` 追加（再現テスト含む） |
| `tests/conftest.py` | version probe を既定で stub する autouse fixture + marker 登録 |
| `pyproject.toml` | opt-out marker の登録（`[tool.pytest.ini_options] markers`） |
| `tests/test_gh_version_large_local.py` | 実 `gh --version` 出力に対する parse 検証（新規） |
| `README.md` / `README.ja.md` | prerequisites に `gh >= 2.50.0` |
| `docs/cli-guides/github-mode.md` / `.ja.md` | § 1.1 必須ツール表 + troubleshooting 4.6 |
| `docs/guides/python-starter.md` / `.ja.md` | § 2.3 に github-mode guide § 1.1 への参照リンクを 1 行追加（version 番号は複記しない） |
| `docs/dev/development_workflow.md` | 新 section「GitHub CLI の最低 version 管理」 |

リファクタは混在させない。`view_issue()` の field 列・protocol・REST 経路は触らない。

## 方針

### 1. preflight の挿入位置

`_run_gh()` の既存 `shutil.which("gh")` チェック（`github.py:104`）の直後に
`self._ensure_gh_version()` を置く。検査が最初の業務 gh 実行より前に走るため、古い gh では
`create_issue` / `edit_issue` / `close_issue` が **mutation 前に** 停止し、「mutation 成功後に
再取得だけ失敗」という曖昧な結果は構造的に発生しない。追加の try/except ラップは不要。

### 2. 疑似コード

```python
_MIN_GH_VERSION: Final[tuple[int, int, int]] = (2, 50, 0)
_GH_VERSION_RE: Final = re.compile(r"^gh version (\d+)\.(\d+)\.(\d+)")
_GH_INSTALL_URL: Final = "https://github.com/cli/cli#installation"


@dataclass
class GitHubProvider:
    ...
    # 解析結果の memo。init/repr/compare からは除外し dataclass の等価性を変えない
    _gh_version: tuple[int, int, int] | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _gh_version_probed: bool = field(default=False, init=False, repr=False, compare=False)

    def _detect_gh_version(self) -> tuple[int, int, int] | None:
        """`gh --version` を解析。解析できなければ None（fail-open）。"""
        try:
            proc = subprocess.run(
                ["gh", "--version"], check=False, capture_output=True, text=True
            )
        except OSError:
            return None
        if proc.returncode != 0:
            return None
        first_line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
        m = _GH_VERSION_RE.match(first_line)
        if m is None:
            return None
        major, minor, patch = (int(g) for g in m.groups())
        return (major, minor, patch)

    def _ensure_gh_version(self) -> None:
        if not self._gh_version_probed:
            self._gh_version = self._detect_gh_version()
            self._gh_version_probed = True
        version = self._gh_version
        if version is None:
            return  # fail-open: 独自ビルド等を無条件に弾かない
        if version < _MIN_GH_VERSION:
            raise GitHubProviderError(...)  # 4 要素を含む message

    def _run_gh(self, *args, capture=True):
        if shutil.which("gh") is None:
            raise GitHubProviderError("'gh' CLI not found in PATH. ...")
        self._ensure_gh_version()          # <-- 追加はこの 1 行
        ...
```

判定は tuple 比較（`(2, 45, 0) < (2, 50, 0)`）で行う。文字列比較は使わない
（`"2.9.0" > "2.50.0"` になるため）。

memo は「解析結果」を保持し、判定は毎回 cache から導出する。これにより
`gh < 2.50.0` の場合は probe を 1 回だけ実行しつつ、以降の全 `_run_gh()` 呼び出しが
同じエラーを返す。

### 3. 既存テストへの影響と吸収方法

preflight を入れると、`subprocess.run` を mock している既存テストで **1 回目の呼び出しが
version probe になる**。実測で次の破壊が起きる。

- `captured[0]` に業務コマンドを期待する assertion
  （`tests/test_providers_github.py:103`、`:237`、`tests/test_providers_github_incident.py`）
- `outputs = iter([...])` を `side_effect` に渡す test（`tests/test_providers_github.py:128`）が
  probe で 1 要素消費される

全 13 箇所の `GitHubProvider(...)` 構築を個別に直すと churn が大きく、将来の provider テスト
追加ごとに同じ配慮が必要になる。したがって `tests/conftest.py` に autouse fixture を置き、
既定で `GitHubProvider._detect_gh_version` が supported version を返すよう stub する。

```python
@pytest.fixture(autouse=True)
def _stub_gh_version(request: pytest.FixtureRequest):
    """既定で gh version probe を supported 値に固定する。

    `@pytest.mark.gh_version_probe` を付けたテストのみ opt-out し、
    probe 経路そのものを検証する。
    """
    if request.node.get_closest_marker("gh_version_probe"):
        yield
        return
    with patch.object(GitHubProvider, "_detect_gh_version", return_value=_MIN_GH_VERSION):
        yield
```

この stub が preflight の回帰を隠さないよう、**probe 経路そのものを検証するテストは
`gh_version_probe` marker による opt-out を必須とする**。対象は次の 2 つで、どちらも
marker が無い状態では `_detect_gh_version` が固定値に置換され偽陽性になる。

| 対象 | marker の宣言位置 | marker が無いと起きること |
|------|-------------------|---------------------------|
| `tests/test_providers_github.py::TestGhVersionPreflight` | class 単位（`pytestmark` またはデコレータ） | `gh --version` の stdout 差し替えが `_detect_gh_version` の置換に潰され、2.45.0 拒否・fail-open・memo 化のいずれも検証できない |
| `tests/test_gh_version_large_local.py` | module 単位の `pytestmark` | 実 `gh` binary を 1 度も起動せず固定値で Green になり、parse 前提の乖離を検出できない |

marker は `pyproject.toml` の `[tool.pytest.ini_options] markers` に既存 5 件と同じ形式で
登録する。ただし現行の `addopts`（`-v --tb=short -n auto`）に `--strict-markers` は
**含まれていない**ため、marker 名を typo しても pytest は warning を出すだけで opt-out が
黙って無効化される。したがって marker 宣言だけを頼りにせず、Large-local 側に「独立取得した
実 version との一致」assertion を置いて構造的に fail させる（後述「Large テスト」観点 1）。
`--strict-markers` の追加は本 Issue のスコープ（GitHub provider の互換性・診断）外のため
行わない。

### 4. docs 更新方針

- README (en/ja) の prerequisites 行を「認証済み `gh`」から「`gh` 2.50.0 以降（認証済み）」に更新
- `docs/cli-guides/github-mode.md` / `.ja.md` § 1.1 必須ツール表の `gh` 行の備考に
  `Must be on PATH, version >= 2.50.0` を追記し、troubleshooting に `4.6` を追加
  （症状 = 上記エラーメッセージ、原因 = 古い gh、対処 = 公式手順で更新）
- `docs/guides/python-starter.md` / `.ja.md` § 2.3 に「最低 version は github-mode guide § 1.1
  を参照」の 1 行を追加する（version 番号は複記せず、正本を 1 箇所に保つ）
- `docs/dev/development_workflow.md` に新 section を追加し、
  「新しい `--json` field / gh flag を採用する際は `_MIN_GH_VERSION` と README / github-mode
  guide (en/ja) を同時に更新する」ルールと、下限調査の手順（cli/cli の release / compare で
  field 追加 version を確認）を記載する

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 対応方針の選択 | 案 1（最低 version 定義 + preflight）+ 案 3（actionable error）を採用、案 2（fallback / REST 迂回）は不採用 | Issue #372 本文 `## 決定した方針（2026-07-24）` および人間コメント「検討経緯」§2（人間決定） | `view_issue()` の field 列 / protocol / REST 経路を触らない変更スコープに落とし込み |
| 最低 version | `gh >= 2.50.0` | Issue #372 `## 調査結果`（2.45〜2.49 失敗 / 2.50.0 成功の実機比較）＋人間コメント §3（`--slurp` は v2.48.0 で包含）（人間決定） | module 定数 `_MIN_GH_VERSION = (2, 50, 0)` として単一情報源化 |
| 検査の挿入位置 | `_run_gh()` の `shutil.which` チェック直後、provider インスタンス内で memo 化 | Issue #372 `### 実装内容` 2 および人間コメント §4（人間決定） | `_ensure_gh_version()` を 1 行呼び出しで差し込み、mutation 前停止を順序だけで満たす構成に具体化 |
| 解析不能時の扱い | fail-open（素通り） | Issue #372 `### 実装内容` 4 および人間コメント §5（人間決定） | `_detect_gh_version()` が `None` を返す形に写像 |
| エラーメッセージの要素 | 検出 version / 必要 version / 理由 / 公式インストール URL | Issue #372 `## あるべき挙動（EB）`（人間決定） | 英語 1 メッセージの文面案に具体化（既存 provider メッセージが英語） |
| 再発防止ルールの記載先 | dev guide | Issue #372 `### 実装内容` 5（人間決定・記載先は未指定） | `docs/dev/development_workflow.md` の新 section に配置。AI の詳細化。review-design で検査 |
| probe の実行手段 | `_run_gh()` を経由せず `subprocess.run` を直接呼ぶ | AI の詳細化。根拠: `_ensure_gh_version()` を `_run_gh()` 内に置く人間決定の帰結として再帰が発生するため。review-code で検査 | `kaji_harness.providers.github.subprocess.run` 経由に固定し、既存 mock が覆う形にした |
| probe の失敗（exit != 0 / OSError）の扱い | 解析不能と同じく fail-open | AI の仮定。根拠: 人間決定の fail-open 趣旨（独自ビルド利用者を無条件に弾かない）と、実際に問題があれば直後の業務 gh 実行でエラーになること。review-design / review-code で検査 | `_detect_gh_version()` が `None` を返す分岐に統合 |
| 検査の適用範囲 | `GitHubProvider._run_gh()` 経由の経路のみ。`kaji pr` passthrough / `kaji sync from-github` / `codex_review_poll` は対象外 | AI の詳細化。根拠: Issue の対象が「GitHub provider」であること、および grep で確認した通り当該 3 経路は v2.50.0 を要する gh 機能を使わないこと。review-design で検査 | 「根本原因」表に経路ごとの下限根拠を記録し、範囲外の理由を検証可能にした |
| 既存テストの回帰吸収 | `tests/conftest.py` の autouse fixture で probe を stub、probe 経路を検証するテストは `gh_version_probe` marker での opt-out を必須とする | AI の詳細化。根拠: 13 箇所の provider 構築を個別修正する案より churn が小さく、将来の provider テスト追加でも同じ配慮が不要。review-code で検査 | marker 名 `gh_version_probe` を定義し、opt-out 必須対象を `TestGhVersionPreflight`（class 単位）と Large-local file（module 単位 `pytestmark`）の 2 つに特定。`--strict-markers` が未設定で typo が silent に通る点を踏まえ、Large-local に「独立取得した実 version との一致」assertion を置いて marker 失効を構造的に検出する |
| starter guide への最低 version 記載 | version 番号は複記せず、github-mode guide § 1.1 への参照リンクのみ追加 | AI の詳細化（review-design の Should Fix 指摘を受けた再評価）。根拠: `_MIN_GH_VERSION` / README / github-mode guide に加えて 4 つ目の情報源を作ると drift する一方、starter § 1 Prerequisites が `gh` を列挙している以上、要件への到達経路は必要。review-design / i-dev-final-check で検査 | § 2.3 に参照リンク 1 行を追加する範囲に限定 |
| version 比較方式 | tuple 比較 | AI の詳細化。根拠: 文字列比較では `2.9.0 > 2.50.0` になる。review-code で検査 | `(major, minor, patch)` を int tuple に正規化 |
| スコープ外 | `LocalProvider` の `close_reason` 読み戻し欠落は別 Issue | Issue #372 `### スコープ外`（人間決定） | 本設計では扱わない |

one-way door の未決は無い。公開 CLI 契約・データ契約・永続化 schema の変更はなく、
追加されるのは既存例外型による失敗経路のみで、誤りは後段で安く直せる。

## テスト戦略

### 変更タイプ

実行時コード変更（provider の失敗経路を追加する振る舞い変更）。

### Small テスト

`tests/test_providers_github.py` に `TestGhVersionPreflight` を追加。すべて
`@pytest.mark.gh_version_probe` を付与し conftest の stub を opt-out する。
`subprocess.run` を mock するため worktree 解決の git 経路には到達しない
（`docs/dev/testing-convention.md` § `subprocess.run` / `shutil.which` patch スコープの
「provider 構築前 fail-fast の不呼出し検証」に該当し、到達すれば必ず fail する assertion
を伴う）。

検証観点:

1. **再現テスト（OB → EB）**: `gh --version` が `gh version 2.45.0 (2024-04-04)` を返す状態で
   `view_issue("372")` を呼ぶと `GitHubProviderError` が送出され、message に「出力」節の
   4 要素すべて — 検出 version `2.45.0` / 必要 version `2.50.0` / **理由（`stateReason` と、
   旧版で当該 field が利用できない旨）** / インストール URL `https://github.com/cli/cli#installation`
   — が含まれる。理由要素を assert に含めるのは、これを落としても version 2 要素と URL だけで
   Green になり、Issue の EB「その version を要求する理由」を満たさない実装が通過してしまう
   ため。**修正前は preflight が存在しないため gh が呼ばれ Red**、修正後 Green。
2. **mutation 前停止**: 同条件で `create_issue()` / `edit_issue()` / `close_issue()` が
   raise し、捕捉した argv 一覧に `["gh", "issue", "create"]` 等の業務コマンドが
   **1 件も含まれない**（`gh --version` のみ）。
3. **境界値**: `2.50.0` は通過する（`< ` ではなく `<=` の取り違えを検出）。
4. **上位 version**: `2.96.0` は通過する。
5. **fail-open（解析不能）**: `gh version dev-custom` / 空 stdout / 想定外形式で通過する。
6. **fail-open（probe 失敗）**: `gh --version` が exit 1 を返す場合、および `OSError` を
   送出する場合に通過する。
7. **memo 化**: 同一 provider インスタンスで `_run_gh()` を複数回通す操作を行っても
   `gh --version` の起動は 1 回だけ（argv 一覧中の `--version` 出現数 == 1）。
8. **version 比較の桁**: `2.9.0` が拒否される（文字列比較なら通過してしまうケース）。

### Medium テスト

追加しない。本変更はファイル I/O・DB・内部サービス結合を伴わず、外部境界は `gh`
subprocess の 1 点のみである。その境界は Small（mock による分岐網羅）と Large-local
（実 binary の出力形式）で両端を押さえるため、中間層に新たな回帰検出情報が生じない
（`docs/dev/testing-convention.md` の 4 条件: 独自ロジックは Small で全分岐カバー済み /
想定不具合は Small + Large-local で捕捉 / 追加しても情報増分なし / 本節が省略理由）。

### Large テスト

`tests/test_gh_version_large_local.py`（新規）。ネットワーク疎通は不要。
module 冒頭で marker を **3 つとも** 宣言し、`shutil.which("gh") is None` で skip する。

```python
pytestmark = [
    pytest.mark.large,
    pytest.mark.large_local,
    # 必須: conftest の autouse fixture (_stub_gh_version) を opt-out する。
    # これが無いと _detect_gh_version が固定値に置換され、実 binary を 1 度も
    # 起動しないまま Green になり、本 file の存在意義が消える。
    pytest.mark.gh_version_probe,
]
```

`gh_version_probe` marker を module 単位で宣言することで、本 file に後から test を
追加しても opt-out が自動的に効く（test ごとの付け忘れが起きない）。

検証観点:

1. **stub 無効化の自己検証**: test 内で `subprocess.run(["gh", "--version"])` を独立に実行して
   得た version 文字列と、`_detect_gh_version()` の戻り値が一致する。autouse stub が効いた
   状態では戻り値が `_MIN_GH_VERSION` 固定になるため、`gh != 2.50.0` の環境ではこの assertion
   が fail する。「実 binary を起動している」ことを assertion で担保し、marker の付け忘れが
   silent に通らないようにする。
2. **parse 前提の検証**: 実 `gh --version` の stdout に対し `_detect_gh_version()` が `None`
   ではなく 3 要素 int tuple を返す（parse 前提が実 binary の出力形式と乖離していないことの
   検証。Small の mock だけでは誤った regex がそのまま通ってしまう）。
3. **preflight の疎通**: 解析した version が `_MIN_GH_VERSION` 以上なら
   `_ensure_gh_version()` が raise しない。

`large_forge`（実 GitHub API 疎通）は追加しない。本変更は gh binary の version 検査で
完結し、GitHub API の応答に依存しない。

### 回帰確認

`make check`（`ruff check` → `ruff format --check` → `mypy` → `pytest` 全件）を実行し、
conftest の autouse stub 追加による既存 provider テストへの副作用がないことを確認する。
docs 変更を含むため `make verify-docs` も実行する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `README.md` | あり | prerequisites の `gh` 行に最低 version を追記（Issue 完了条件） |
| `README.ja.md` | あり | 同上（en/ja 対訳） |
| `docs/cli-guides/github-mode.md` | あり | § 1.1 必須ツール表 + troubleshooting 4.6 追加（Issue 完了条件） |
| `docs/cli-guides/github-mode.ja.md` | あり | 同上 |
| `docs/dev/development_workflow.md` | あり | 新 `--json` field / gh flag 採用時に `_MIN_GH_VERSION` と docs を同時更新するルール（Issue 完了条件） |
| `docs/adr/` | なし | 新しい技術選定ではない。既存 provider 内の失敗経路追加 |
| `docs/ARCHITECTURE.md` | なし | レイヤ構成・モジュール責務は不変 |
| `docs/reference/python/` | なし | Python 規約の変更なし |
| `docs/dev/testing-convention.md` | なし | 既存の patch スコープ規約の範囲内。新規則は追加しない |
| `docs/guides/python-starter.md` / `.ja.md` | あり（リンクのみ） | § 1 の Prerequisites 行（en `python-starter.md:29` / ja `python-starter.ja.md:26`）が `gh` を <https://cli.github.com/> リンク付きで列挙し、§ 2.3 が `gh auth status` を要求する。version 番号をここに複記すると `_MIN_GH_VERSION` / README / github-mode guide に続く 4 つ目の情報源になり drift するため、**version は書かず** § 2.3 から github-mode guide § 1.1（最低 version の正本）への参照リンクを 1 行追加し、starter 利用者が要件へ到達できるようにする |
| `AGENTS.md` / `CLAUDE.md` | なし | 開発規約の変更なし |
| `llms.txt` | なし | `gh` の前提条件を記載していない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| cli/cli v2.49.0...v2.50.0 diff | <https://github.com/cli/cli/compare/v2.49.0...v2.50.0> | `api/query_builder.go` の `IssueFields` に `stateReason` が追加された。これが `--json stateReason` の下限が v2.50.0 である理由 |
| cli/cli v2.50.0 release | <https://github.com/cli/cli/releases/tag/v2.50.0> | 上記 field 追加を含む最初の公開 release |
| cli/cli PR 8620 (`gh api --slurp`) | <https://github.com/cli/cli/pull/8620> | `--paginate --slurp` の導入 PR。2024-04-17 マージ、v2.48.0 で出荷。`_gh_json_slurp()`（`github.py:139`）の下限が v2.48.0 であり、2.50.0 に包含されることの根拠 |
| cli/cli v2.48.0 release | <https://github.com/cli/cli/releases/tag/v2.48.0> | `--slurp` を含む release |
| GitHub CLI installation | <https://github.com/cli/cli#installation> | エラーメッセージに載せる公式インストール手順。gh の入れ替えが容易であることが「互換シムより最低 version を切る」判断の前提 |
| `gh --version` の実出力 | 開発機での実行結果（gh 2.96.0） | `gh version 2.96.0 (2026-07-02)` を stdout 1 行目に exit 0 で出力。parse 正規表現 `^gh version (\d+)\.(\d+)\.(\d+)` の根拠 |
| Issue #372 本文 OB ログ | Issue #372 `## 壊れた挙動（OB）` | gh 2.45.0 / kaji 0.15.0 での exit 1 と `Unknown JSON field: "stateReason"`。再現テストが assert する対象 |
| Issue #372 実機 version 比較表 | Issue #372 `## 調査結果` | 公式 Linux amd64 binary で 2.45.0〜2.49.0 失敗 / 2.50.0 以降成功。`_MIN_GH_VERSION` の直接根拠 |
| 現行実装 | `kaji_harness/providers/github.py:99-117`（`_run_gh`）、`:280`（`stateReason` 要求）、`:270` / `:309` / `:332`（mutation 後の `view_issue()` 再取得）、`:240`（`payload.get("stateReason", "")`） | 挿入位置と mutation 前停止が成立する根拠 |
| テスト規約 | `docs/dev/testing-convention.md` | サイズ定義、`subprocess.run` patch スコープ、恒久テスト省略の 4 条件 |
| bug 設計ガイド | `.claude/skills/_shared/design-by-type/bug.md` | OB / EB / 再現手順の分離と、修正前 Red の再現テスト必須ルール |
