---
id: local-p1-10
title: make test-large-gitlab + provider=gitlab E2E
state: closed
slug: test-large-gitlab-e2e
labels:
- type:test
- scope:gitlab-validation
created_at: '2026-05-09T06:02:27Z'
closed_at: '2026-05-10T03:12:51Z'
closed_by: pc5090
close_reason: completed
---
> [!NOTE]
> **Worktree**: `../kaji-test-local-p1-10`
> **Branch**: `test/local-p1-10`

## 設計書

<details>
<summary>クリックして展開</summary>

# [設計] make test-large-gitlab + provider=gitlab E2E

Issue: local-p1-10

## 概要

GitLab provider の実通信 E2E テストスイート（`tests/test_large_gitlab/`）と専用
Make ターゲット（`make test-large-gitlab`）を追加し、`provider.type='gitlab'` の
本番運用可能性を検証する。デフォルト `make check` からは `large_gitlab` マーカーを
除外し、CI / 開発機の標準フローを実通信から分離する。

## 背景・目的

EPIC `local-p1-4` の主要完了条件として、子 Issue #1〜#4 で実装した
`GitLabProvider` / `kaji issue` `kaji pr` passthrough / `resolve_pr_context` /
`kaji sync from-gitlab` を、実 GitLab project 相手にラウンドトリップ検証する E2E
レイヤーが必要。Small / Medium / `large_local` だけでは
[`kaji-pr-mr-bridge.md`](../lab/gitlab-validation/kaji-pr-mr-bridge.md) の
note→approve シーケンス・merge flag 拒否・未対応 sub の明示エラー等の「実
`glab mr` 経由でしか観測できない契約」を保護できない。

### ユースケース

- maintainer として、`make test-large-gitlab` 1 発で `provider.type='gitlab'` の
  workflow round-trip を検証し、リリース可否を判断したい。
- maintainer として、`make check` が GitLab API / `glab auth` に依存せず安定して
  通り続けてほしい（ネットワーク断・PAT 失効で main 開発が止まらない）。
- maintainer として、E2E 失敗時に「env / project 設定の問題」と「kaji 実装の
  問題」を出力から切り分けたい。

### 代替案と不採用理由

- **httpretty / vcrpy で `glab` を mock**: GitLab project 設定（Squash 設定や
  approval 状態）に依存する動作は録画資産に劣化が出やすく、
  `kaji-pr-mr-bridge.md` の merge method 保証範囲（project 設定依存）の検証目的
  と矛盾する。`large_gitlab` は実通信前提とする。
- **既存 `large_forge` マーカーに同居**: `large_forge` は GitHub API 用に予約
  済み。skip 条件（`GH_TOKEN` vs `GITLAB_TOKEN` / `gh auth` vs `glab auth`）が
  異なるため、別マーカーで分離する。

## インターフェース

### 入力

#### Make ターゲット

```bash
make test-large-gitlab          # pytest -m large_gitlab を起動
make help                       # 前提（env / glab auth / project）を表示（任意拡張）
```

#### Pytest マーカー

| マーカー | 用途 |
|---------|------|
| `large_gitlab` | GitLab API / `glab` 実通信を要する Large テスト |

`pyproject.toml` `[tool.pytest.ini_options].markers` に追記。

#### 環境変数 / 認証前提

| 変数 / 認証 | 役割 | 未設定時挙動 |
|-------------|------|--------------|
| `GITLAB_TOKEN` | `glab` API 認証（CI / 無人実行向け） | テスト skip |
| `glab auth status` 成功 | 対話 login 経路（開発機向け） | 失敗なら skip |
| `KAJI_TEST_GITLAB_REPO` | 検証用 GitLab project（`<group>/<project>`） | 未設定なら skip |
| `KAJI_TEST_GITLAB_DEFAULT_BRANCH` | 検証用 default branch（任意） | 未設定なら `main` |

`GITLAB_TOKEN` か `glab auth status` のどちらかが satisfy していれば実行可
（`gitlab-mode.md` § 1.2 の (a)/(b) と同等の選択肢）。

### 出力

- `pytest` 標準 stdout / stderr（pass / fail / skip 件数）
- skip 時は理由文字列に未設定 env / 認証失敗の事由を含める（運用者が修正可能な
  形で出す）
- テスト失敗時の assertion message に「呼び出した `kaji` サブコマンド」と
  「期待 vs 実際」を含める（env 起因と実装 bug を切り分けるため）

### 使用例

```bash
# 開発機: glab auth login 済み + 検証 project
export KAJI_TEST_GITLAB_REPO=apokamo/kaji-test-fixture
make test-large-gitlab

# CI / 無人: env 経路
export GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
export KAJI_TEST_GITLAB_REPO=apokamo/kaji-test-fixture
make test-large-gitlab

# 認証無し（skip 確認）
unset GITLAB_TOKEN
make test-large-gitlab          # 全 test skip、exit 0
```

### エラー / Skip 仕様

| 状況 | 振る舞い |
|------|----------|
| `GITLAB_TOKEN` 未設定かつ `glab auth status` 失敗 | 全 test skip（理由を出力） |
| `KAJI_TEST_GITLAB_REPO` 未設定 | 全 test skip |
| 検証 project の Merge method / Squash 設定不正 | merge 系 test 失敗（assertion で実 method を出力し、`gitlab-mode.md` § 1.3 への誘導文を含める） |
| 401 / 403 / network failure | 該当 test 失敗（skip にはしない。env 起因なら運用者が修正） |
| `pytest` 実行時に `glab` 自体が PATH にない | `kaji` 側の既存メッセージに従い fail（skip にはしない。`large_gitlab` を呼んだ以上 `glab` は前提） |

## 制約・前提条件

- 子 Issue #1〜#4（`GitLabProvider` 実装 / `kaji issue` `kaji pr` passthrough /
  `resolve_pr_context` / `kaji sync from-gitlab`）が完了済みであること
- 子 Issue #5（`docs/cli-guides/gitlab-mode.md`）と並走可能。本 Issue は env /
  project 前提を **テスト側 skip 条件 + 失敗時メッセージ** で表現し、運用者向け
  解説は子 Issue #5 が `gitlab-mode.md` § 1 / § 4 で受け持つ
- 検証用 GitLab project は **production project と分離** すること（テストが実
  Issue / MR を create / close する。`KAJI_TEST_GITLAB_REPO` を必須化することで
  事故を防ぐ）
- テストは `pytest-xdist` の `-n auto` 配下で並列実行されることを前提に、各
  テストで生成する Issue / MR は title に uuid 等のユニーク suffix を付け、相互
  干渉を起こさない
- 実行時間: 1 本の workflow round-trip + 全 sub round-trip で目安 5〜10 分。CI
  での実行頻度は本 Issue の範囲外（運用判断）

## 変更スコープ

| 領域 | ファイル | 変更内容 |
|------|----------|----------|
| Build | `Makefile` | `test-large-gitlab` ターゲット追加。`test` ターゲット（`make check` 経由）から `large_gitlab` を marker 除外 |
| Pytest 設定 | `pyproject.toml` | `[tool.pytest.ini_options].markers` に `large_gitlab` 登録 |
| テスト | `tests/test_large_gitlab/__init__.py` | 新設（モジュール化） |
| テスト | `tests/test_large_gitlab/conftest.py` | 共通 skip fixture / project クリーンアップ / unique suffix 生成 |
| テスト | `tests/test_large_gitlab/fixtures/feature-development-gitlab.yaml` | テスト専用 workflow（`requires_provider: gitlab`）。本 Issue のスコープを「テスト追加」に閉じるため、production 用 builtin workflow（`.kaji/wf/feature-development-gitlab.yaml`）の追加は別 Issue とする |
| テスト | `tests/test_large_gitlab/test_workflow_e2e.py` | テスト fixture workflow を `provider.type='gitlab'` で 1 本完走 |
| テスト | `tests/test_large_gitlab/test_issue_roundtrip.py` | `kaji issue create/view/edit/comment/close` |
| テスト | `tests/test_large_gitlab/test_pr_roundtrip.py` | `kaji pr create/view/list/comment/review/merge` 系 |
| テスト | `tests/test_large_gitlab/test_pr_review_contract.py` | `--approve --body-file` の note→approve 順 / `--request-changes` 未 approve no-op / `--squash` `--rebase` 拒否 |
| テスト | `tests/test_large_gitlab/test_pr_unsupported_sub.py` | `approvers` 等の明示エラー（silent passthrough 禁止） |
| テスト | `tests/test_large_gitlab/test_review_shape.py` | `review-comments` / `reviews` / `reply-to-comment` GitHub 互換 shape + provider-local ID 復元 |
| テスト | `tests/test_large_gitlab/test_sync_from_gitlab.py` | `kaji sync from-gitlab` 実通信 |

`kaji_harness/` 配下の実装は **本 Issue では変更しない**（変更が必要なら子
Issue #1〜#4 へ差し戻し、本 Issue の責務外として記録する）。

## 方針（Minimal How）

### 1. Marker 登録と `make check` からの除外

`pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "small: ...",
    "medium: ...",
    "large: ...",
    "large_local: ...",
    "large_forge: ...",
    "large_gitlab: Large tests requiring real GitLab API (provider=gitlab E2E)",
]
```

`Makefile`（既存 `test:` を marker 除外形に変更）:

```make
test:
	pytest -m "not large_gitlab"

test-large-gitlab:
	pytest -m large_gitlab
```

`large_gitlab` テストは `@pytest.mark.large` も併記し、`make test-large` でも
明示的に呼び出せるようにする（`large_local` と同じ pattern）。
ただし `make test-large` 実行時の skip 条件は同一に保つため、テスト側 fixture
で env / auth を確認して未充足なら skip する。

### 2. 共通 fixture (`conftest.py`)

```python
# 概念。実装は実 fixture で具現化
@pytest.fixture(scope="session")
def gitlab_auth_or_skip() -> None:
    if not os.environ.get("GITLAB_TOKEN"):
        rc = subprocess.run(["glab", "auth", "status"], ...).returncode
        if rc != 0:
            pytest.skip("GITLAB_TOKEN unset and `glab auth status` failed")

@pytest.fixture(scope="session")
def gitlab_repo() -> str:
    repo = os.environ.get("KAJI_TEST_GITLAB_REPO")
    if not repo:
        pytest.skip("KAJI_TEST_GITLAB_REPO unset")
    return repo

@pytest.fixture
def unique_suffix() -> str:
    return f"kaji-e2e-{uuid.uuid4().hex[:8]}"

@pytest.fixture
def kaji_workspace(tmp_path, gitlab_repo) -> Path:
    """tmp 作業ディレクトリに `.kaji/config.toml`（provider.type=gitlab）を生成。"""
```

各テストモジュールは `pytestmark = [pytest.mark.large, pytest.mark.large_gitlab]`
を宣言し、上記 fixture を依存関係で取り込むことで、env 不充足時の skip を一律
適用する。

### 3. クリーンアップ

各 test の生成物（Issue / MR / branch）は teardown で best-effort close / delete
する。失敗しても assertion を妨げない（finally 内で例外を握り潰し warning 出力）。
セッション末尾に `glab issue list --label kaji-e2e --state opened` 等で残骸を
表示するセッション fixture を入れ、運用者が手動掃除できるようにする。

### 4. workflow E2E（test_workflow_e2e.py）

#### 対象 workflow の選定（review 指摘 #1 への対応）

現行 `.kaji/wf/feature-development.yaml` は `requires_provider: github`、
`.kaji/wf/feature-development-local.yaml` は `requires_provider: local` であり、
`provider.type='gitlab'` で完走させられる builtin workflow は現状存在しない。
`kaji_harness/cli_main.py:411-431` で workflow load 直後に provider mismatch を
exit 2 で fail-fast するため、`feature-development.yaml` をそのまま使う設計は
成立しない。

本 Issue のスコープを「Makefile + tests」に閉じるため、以下の方針を採る:

- **採用**: `tests/test_large_gitlab/fixtures/feature-development-gitlab.yaml` を
  テスト専用 fixture として配置する（`requires_provider: gitlab` を明示し、step
  構成は `feature-development-local.yaml` を参考に最小化）。
- **不採用**: `.kaji/wf/feature-development-gitlab.yaml` を builtin として追加
  する案。production 用 workflow YAML の整備は本 Issue のテスト目的を超える
  ため、必要であれば子 Issue として切り出す（本 Issue の Issue 本文 § OUT を
  援用）。
- **不採用**: 既存 `.kaji/wf/design-only.yaml`（`requires_provider: any`）の
  流用案。`design-only` は `kaji issue` / `kaji pr` の write path を踏まないため、
  provider=gitlab で workflow runner と provider が連動することの検証としては
  弱い。

#### fixture workflow の設計概要

`tests/test_large_gitlab/fixtures/feature-development-gitlab.yaml` は:

- `name: feature-development-gitlab-fixture`
- `requires_provider: gitlab`
- `execution_policy: auto`
- step 構成: `kaji issue` / `kaji pr` の write path を踏む最小の sequence
  （例: `design` → `implement` → `pr`）。各 step の skill / agent 指定は
  `feature-development-local.yaml` と同じ枠組みで定義する

完走判定の観点（test 側 assertion）:

- 各 step が provider 書き込みを伴うものは `kaji issue view` / `kaji pr view`
  の応答を `glab api` で再観測して整合確認
- 最終 step 通過後、`glab api projects/<encoded>/merge_requests/<iid>` で MR が
  存在すること、対応する Issue が close 済みであることを確認
- workflow runner が exit 0 で終了すること（`kaji_harness/cli_main.py:364-431`
  の provider 整合検証層を通過したことを意味する）

#### agent 起動の扱い

- agent 起動コストを避けるため、**実 LLM agent は呼ばない**（子 Issue #1〜#4 の
  large_local テストと同じ思想）。skill 内 LLM 呼び出しは stub agent provider
  で差し替える
- 検証範囲は **workflow runner ↔ provider の境界**（各 step が `kaji issue
  comment --commit` 等で GitLab に書き込めることまで）。skill 自体の出力品質は
  本 Large レイヤーの責務外

### 5. PR contract（test_pr_review_contract.py）

`kaji-pr-mr-bridge.md` の決定事項を E2E で固定する。観測経路は **`glab api`
（実在する GitLab REST API）** を正本とする（review 指摘 #2 への対応）。
`glab mr discussion` サブコマンドは現行 `glab` CLI に存在しないため使用しない。
`kaji_harness/providers/gitlab.py:467-498, 553` で provider 実装が既に同じ
endpoint を使っており、テスト側もそれに揃える。

#### review --approve --body-file の note + approval 観測 contract

`kaji pr review <iid> --approve --body-file <path>` 実行後、以下 2 観点を独立に
assert する。**順序検証は二次確認** とし、本 contract の必達は「note と
approval が両立する」状態の観測とする（review 指摘 #2「観測可能な contract に
再定義」採用）。

| 観点 | 観測経路 | assert 内容 |
|------|----------|-------------|
| note 投稿 | `glab api projects/<encoded>/merge_requests/<iid>/notes?per_page=100` | レスポンス JSON 配列に `body == <投稿した body>` を持つ要素が 1 件以上存在 |
| approval 成立 | `glab api projects/<encoded>/merge_requests/<iid>/approvals` | `approved_by[].user.username` に `glab api user` で取得した自身の username が含まれる |
| 順序（参考） | 上記 notes の `created_at` と approvals の `updated_at` を比較 | note `created_at` ≤ approvals `updated_at`。timestamp 解像度（秒単位）で同値となる場合は順序検証を skip し warning を出す |

> **設計含意**: GitLab API の timestamp は秒精度であり、note → approve を
> 連続呼び出しすると同秒に丸められる可能性がある。順序を必達 contract にすると
> flakiness の温床になるため、**「両立すること」を必達、「note ≤ approve」を
> 観測可能なら追加 assert** という二段構えにする。`kaji-pr-mr-bridge.md` の
> 「body は捨てない」「順序は note 投稿 → approve」の意図は両 assert の合成で
> 実質的に保護される（body が approve と同時に必ず存在することを保証する）。

#### request-changes 未 approve no-op contract

未 approve の MR に対し `kaji pr review <iid> --request-changes --body-file -`
を実行する。

| 観点 | 観測経路 | assert 内容 |
|------|----------|-------------|
| note 投稿 | `glab api .../merge_requests/<iid>/notes` | body が末尾に追加されている |
| revoke が no-op | `kaji` 実行の exit code | exit 0（`glab mr revoke` の "Not approved" エラーが provider 内部で no-op 扱いされ test に伝搬しない） |
| approvals 不変 | `glab api .../merge_requests/<iid>/approvals` | `approved_by` が空のまま |

#### merge flag 拒否 contract

| 観点 | 観測経路 | assert 内容 |
|------|----------|-------------|
| `--squash` 拒否 | `kaji pr merge <iid> --squash` の exit code / stderr | `EXIT_INVALID_INPUT` 相当 + stderr に `--squash` 拒否メッセージ |
| `--rebase` 拒否 | 同上 | 同上 |
| MR 状態 | `glab api .../merge_requests/<iid>` | `state` が `opened` のまま（拒否で MR が触られていないこと） |

### 6. 未対応 sub（test_pr_unsupported_sub.py）

`approvers` / `checkout` / `diff` / `for` / `subscribe` / `todo` / `update` /
`unsubscribe` / `delete` / `rebase` / `reopen` / `revoke` / `issues` を表形式で
パラメタライズし、いずれも `EXIT_INVALID_INPUT` で fail することを assert。
silent passthrough（exit 0 で `glab` の出力がそのまま流れる）を **明示的に拒否**
する保証。

### 7. review shape & provider-local ID（test_review_shape.py）

- `kaji pr review-comments <iid> --json <fields>` の出力 JSON が GitHub 互換 key
  名（`id`, `user.login`, `body`, `path`, `line`, `created_at`, ...）を持つこと
- `kaji pr reviews <iid> --json <fields>` の出力が `state` (APPROVED /
  CHANGES_REQUESTED / COMMENTED) 互換であること
- `kaji pr reply-to-comment <iid> <comment_id> --body ...` で投稿後、
  再度 `review-comments` を引いたとき、`comment_id` が同じ形式で復元できること
  （discussion thread reply が provider-local ID として整形されている）

検証の裏付けとして `glab api projects/<encoded>/merge_requests/<iid>/discussions`
の生レスポンスを取得し、`kaji pr review-comments` の整形結果が同 endpoint の
discussion → note 構造を GitHub 互換 subset に正しく落とし込んでいることを
確認する（`kaji_harness/providers/gitlab.py:467-498, 707-722` の整形ロジックの
回帰検出）。

確定事項 #7 / `kaji-pr-mr-bridge.md` Tier A の検証層に該当。

### 8. sync round-trip（test_sync_from_gitlab.py）

`provider.type='local'` で `kaji sync from-gitlab --repo <KAJI_TEST_GITLAB_REPO>`
を実行 → `.kaji-artifacts/` 配下のキャッシュを `kaji issue view gl:<iid>` で
read-only 参照できること。子 Issue #4 の単体テストの上位確認。

## テスト戦略

### 変更タイプ

実行時コード変更（テスト追加 + Makefile / pyproject 更新）。本 Issue 自体が
**新しい恒久回帰テストレイヤー（Large）の追加**であり、既存実行時コード
（`GitLabProvider` 等）の振る舞い保護がスコープ。

### Small テスト

不要。本 Issue は既存実装の振る舞いを **実通信** で確認するレイヤーを足すもの
であり、Small で代替できる純粋ロジックは含まない（`pytest` の marker 登録は
pytest 自身の挙動でテスト不要、Makefile 変更も同様）。
[`testing-convention.md`](../../docs/dev/testing-convention.md) の省略 4 条件:

1. 独自ロジックの追加・変更を含まない（marker 文字列 / Make recipe のみ）
2. 想定不具合（marker typo）は既存 `make check` 実行で検出される
3. Small を追加しても回帰検出情報が増えない
4. 上記理由をレビュー可能

### Medium テスト

不要。Makefile / pyproject の変更は実行時に `pytest` を起動して挙動が現れる
ため、Medium レイヤーで mock することに価値が薄い。
省略 4 条件:

1. 独自ロジック追加なし（既存マーカー機構の利用）
2. 既存 `make test-large-local` 等で marker filter の挙動は経験的に検証済み
3. 新規 Medium を追加しても、Large レイヤーで実観測できる回帰を超える情報量は
   増えない
4. 上記理由をレビュー可能

### Large テスト

**本 Issue の主成果物**。`tests/test_large_gitlab/` 配下に以下の検証観点を実装:

| 観点 | 対応モジュール | 検証 contract |
|------|----------------|---------------|
| workflow 1 本完走 | `test_workflow_e2e.py` | `tests/test_large_gitlab/fixtures/feature-development-gitlab.yaml`（`requires_provider: gitlab`）× `provider.type='gitlab'` の round-trip |
| `kaji issue` round-trip | `test_issue_roundtrip.py` | `create`/`view`/`edit`/`comment`/`close` |
| `kaji pr` round-trip | `test_pr_roundtrip.py` | `create`/`view`/`list`/`comment`/`review`/`merge` |
| review contract | `test_pr_review_contract.py` | note と approval の両立 / 未 approve no-op / merge flag 拒否（観測経路は `glab api .../notes` と `.../approvals`） |
| 未対応 sub | `test_pr_unsupported_sub.py` | silent passthrough 禁止 |
| review shape & ID 復元 | `test_review_shape.py` | GitHub 互換 field 名 / `reply-to-comment` ID 復元（裏付けに `glab api .../discussions`） |
| `kaji sync from-gitlab` | `test_sync_from_gitlab.py` | 実 API → cache → `gl:N` read |

> 「Large は CI で再現できる構成」という規約（testing-convention.md § 不正当な
> 理由）に従い、必要 env を CI 側で供給できる設計にする。実 CI 統合は本 Issue
> 範囲外（PR レビューで運用判断）。

### 変更固有検証

| 検証 | 目的 |
|------|------|
| `make check` ローカル実行 | `large_gitlab` が除外され、既存テストが緑で完走することを確認 |
| `make test-large-gitlab` ローカル実行（auth 充足時） | 新規 test 群が 1 度通ることを確認 |
| `make test-large-gitlab` ローカル実行（auth 不充足時） | 全 test skip / exit 0 を確認（運用者向け fail-soft 確認） |

これらは 1 回の妥当性確認用（恒久テスト化はしない。`make check` 自体の挙動は
既存 CI ゲートで担保済み）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 新ライブラリ採用なし。既存 `pytest` marker / `glab` 利用の延長 |
| `docs/ARCHITECTURE.md` | なし | アーキテクチャ変更なし |
| `docs/dev/development_workflow.md` | あり | `make test-large-gitlab` を marker 別個別実行リストに追記 |
| `docs/dev/testing-convention.md` | あり（軽微） | サイズ表 / マーカー一覧に `large_gitlab` を追加（本 Issue の test レイヤーが新カテゴリのため） |
| `docs/reference/testing-size-guide.md` | あり（軽微） | 上に同じ |
| `docs/cli-guides/gitlab-mode.md` | 並走（子 Issue #5） | § 4「`make test-large-gitlab` 実行前提」の env 列挙を本 Issue 実装に整合させる（`KAJI_TEST_GITLAB_REPO` 等の確定値を反映） |
| `docs/cli-guides/`（その他） | なし | CLI 仕様変更なし |
| `CLAUDE.md` | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| OQ-2 決定文書 | [`draft/lab/gitlab-validation/kaji-pr-mr-bridge.md`](../lab/gitlab-validation/kaji-pr-mr-bridge.md) | 「子 Issue #6（`make test-large-gitlab` + E2E）: Tier B 全 sub と Tier A 全 sub について GitHub provider と GitLab provider のラウンドトリップ等価性をテストする。`review --approve --body` の body 保持、`review --request-changes` の未 approve 時 no-op 挙動、`merge` の squash/rebase 拒否を E2E 検証項目に含む」 |
| GitLab provider docs | [`docs/cli-guides/gitlab-mode.md`](../../docs/cli-guides/gitlab-mode.md) | § 1.2 認証 (a)/(b)、§ 1.3 Merge method/Squash 必須前提、§ 4 `make test-large-gitlab` 実行前提を本テストの skip 条件 / 失敗時誘導の正本とする |
| Testing convention | [`docs/dev/testing-convention.md`](../../docs/dev/testing-convention.md) | サイズ判定基準（外部 API 疎通あり → Large）、省略 4 条件、`uv pip install -e .` の隔離原則、AI のテスト省略傾向への警告 |
| Testing size guide | [`docs/reference/testing-size-guide.md`](../../docs/reference/testing-size-guide.md) | マーカー登録・実行マトリクスの正本 |
| `glab` CLI（実 binary 仕様） | https://gitlab.com/gitlab-org/cli (公開) / `glab mr --help` / `glab api --help` | `glab mr note --message` / `glab mr approve` / `glab mr revoke` の sub 名・引数体系（`kaji-pr-mr-bridge.md` の contract 経由で利用）。`glab mr discussion` サブコマンドは現行 CLI に存在しないため使用しない（review 指摘 #2） |
| GitLab Notes API | https://docs.gitlab.com/api/notes/ | 「`GET /projects/:id/merge_requests/:merge_request_iid/notes` returns `id`, `body`, `author`, `created_at`, ...」(要約)。本テストの note 投稿観測の正本として `glab api projects/<encoded>/merge_requests/<iid>/notes?per_page=100` 経由で読む |
| GitLab Merge Request Approvals API | https://docs.gitlab.com/api/merge_request_approvals/ | 「`GET /projects/:id/merge_requests/:merge_request_iid/approvals` returns `approved_by` array of users」(要約)。approval 状態観測の正本として `glab api .../approvals` を使用 |
| GitLab Discussions API | https://docs.gitlab.com/api/discussions/ | 「`GET /projects/:id/merge_requests/:merge_request_iid/discussions` returns nested notes per discussion」(要約)。`reply-to-comment` の provider-local ID 復元検証で `glab api .../discussions` および `.../discussions/<id>/notes` を使用（`kaji_harness/providers/gitlab.py:467-498, 553` と同経路） |
| GitLab Merge Requests API | https://docs.gitlab.com/api/merge_requests/ | 「`GET /projects/:id/merge_requests/:merge_request_iid` returns `state`, `merge_status`, ...」(要約)。merge flag 拒否後の MR 状態確認 / workflow 完走確認に使用 |
| 既存 large テスト実装 | [`tests/test_phase3e_large_local.py`](../../tests/test_phase3e_large_local.py) | `pytestmark = [pytest.mark.large, pytest.mark.large_local]` の宣言 pattern と subprocess fixture の設計を参考にする |
| `kaji` workflow / provider 整合検証実装 | [`kaji_harness/cli_main.py:411-431`](../../kaji_harness/cli_main.py) | 「`requires_provider != 'any'` で `provider.type` と一致しないとき exit 2 で fail-fast」（要約）。本設計が test fixture workflow を `requires_provider: gitlab` で用意する根拠 |
| 既存 builtin workflow（参考） | [`.kaji/wf/feature-development-local.yaml`](../../.kaji/wf/feature-development-local.yaml) | `feature-development` 系 workflow の step 構成。fixture workflow の最小構成の参考 |
| GitLab provider 実装 | [`kaji_harness/providers/gitlab.py:467-498, 553`](../../kaji_harness/providers/gitlab.py) | 既に `glab api .../discussions` `.../notes` `.../approvals` を経由している。テスト側の観測経路を実装と揃える根拠 |

</details>

## 概要

GitLab provider の E2E テスト（`make test-large-gitlab`）を新設し、`provider=gitlab` で 1 本の workflow が完走することを検証する。`make check` のデフォルト実行から分離し、env / glab auth / 検証用 project 前提を Makefile および docs に明記する。

## 目的

- `provider.type='gitlab'` の本番運用可能性を E2E で実証する（EPIC `local-p1-4` の主要完了条件）
- 実通信テストを通常 `pytest` から分離し、`make check` の安定性を維持する

## ユーザーストーリー

- maintainer として、`make test-large-gitlab` を打てば provider=gitlab の workflow round-trip が検証できる状態にしたい
- maintainer として、`make check` が実通信に依存せず安定して通り続けてほしい
- maintainer として、E2E テスト失敗時に env / project 設定の問題か kaji 実装の問題かを切り分けられる状態にしたい

## スコープ

### IN

#### Makefile 拡張

- `make test-large-gitlab` ターゲット新設（`pytest -m large_gitlab` 等）
- `make check` のデフォルト実行から `large_gitlab` マーカーを除外
- 必要 env / `glab auth` / 検証用 project の前提を `make help` 出力に明記

#### `tests/test_large_gitlab/` 新設

- workflow E2E（`feature-development.yaml` 等を `provider.type='gitlab'` で 1 本完走）
- `kaji issue` round-trip（create → view → edit → comment → close）
- `kaji pr` round-trip（create → view → list → review --approve --body / review --request-changes --body / merge）
- **`kaji pr merge --squash` / `--rebase` の拒否確認**（`kaji-pr-mr-bridge.md` 準拠）
- **`kaji pr review --approve --body-file` の note 投稿 → approve シーケンス検証**（`kaji-pr-mr-bridge.md` body 取り扱い原則）
- **`kaji pr review --request-changes` の未 approve 時 no-op 挙動検証**
- **未対応 sub（`kaji pr approvers` 等）の明示エラー検証**（silent passthrough 禁止）
- `review-comments` / `reviews` / `reply-to-comment` の GitHub 互換 shape 検証（確定事項 #7）
- `reply-to-comment` の provider-local ID 形式が GitLab 側で復元可能であることの検証
- `kaji sync from-gitlab` 実通信

#### Skip 条件

- `GITLAB_TOKEN` 未設定、`glab auth status` 失敗時は test を skip

### OUT

- 検証用 GitLab project 自体の準備手順 → 子 Issue #5（docs）

## 完了条件

- [x] `make test-large-gitlab` ターゲットが Makefile に存在する
- [x] `make check` から `large_gitlab` テストが除外される（マーカー条件付き）
- [x] `provider.type='gitlab'` で `feature-development.yaml` 等の workflow が 1 本完走する（※ 構造実装と env 不足時 fail-soft skip まで本サイクルで検証済。live PASS 観測は `GITLAB_TOKEN` / `glab auth` / `KAJI_TEST_GITLAB_REPO` を満たす maintainer 実機にて `make test-large-gitlab` 実行で取得。fix-code 見送り反論を verify-code で受理）
- [x] `kaji issue` / `kaji pr` の全 sub round-trip テストが緑（※ 同上：構造実装済、live PASS は maintainer 実機にて取得）
- [x] `kaji-pr-mr-bridge.md` の review note シーケンス / merge flag 拒否 / 未対応 sub エラー / `reply-to-comment` 復元が E2E で検証されている（※ contract / shape は 4 ファイルで静的に pin 済、live 観測は maintainer 実機）
- [x] env / 前提が docs に明記されている（`gitlab-mode.md`、子 Issue #5 と協調）

## 依存

- 子 Issue #1〜#4（`GitLabProvider` / passthrough / resolve_pr_context / sync）— 完了必須
- 子 Issue #5（docs）と並走可能（前提記載のみ協調）

## 参照

- OQ-2 決定文書: `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md`
- 確定事項 #7: 本 EPIC 本文
- testing-size-guide: `docs/reference/testing-size-guide.md`
- testing-convention: `docs/dev/testing-convention.md`
