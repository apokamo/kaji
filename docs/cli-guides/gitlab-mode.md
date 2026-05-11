# GitLab Mode CLI Guide

`kaji` を `provider.type = "gitlab"` で運用するためのセットアップ / 運用 / 前提
ガイド。`provider.type = "github"` 用の各種 skill を **無修正のまま** GitLab project
へ向けるために必要な手順と前提条件を 1 ファイルで提供する。

## いつ使うか

- GitLab 上の project（`gitlab.com/<group>/<project>`）を kaji の primary forge
  として運用する場合
- 検証期間中の local-mode（`docs/cli-guides/local-mode.md`）から GitLab を本格
  forge として採用する移行段階
- `make test-large-gitlab` 等の GitLab 通信を伴う E2E 検証を回す場合

## 1. 前提

### 1.1 必須ツール

| ツール | 役割 | 備考 |
|--------|------|------|
| `glab` | GitLab CLI（`kaji pr` / `kaji issue` / `kaji sync from-gitlab` の背後で起動） | PATH 上に必須 |
| `git` | 通常運用 | `git@gitlab.com` への SSH push が前提 |

`glab` 未導入の場合、`kaji sync from-gitlab` および `provider.type='gitlab'` 配下の
`kaji issue` / `kaji pr` は以下のメッセージで exit する:

```
'glab' CLI not found in PATH. Install glab to use provider.type='gitlab'.
```

### 1.2 SSH 鍵 / 認証

#### SSH 鍵

`gitlab.com` への push / fetch を SSH 経由で行うため、鍵を 1 度登録する:

1. ローカルで鍵を生成または既存鍵を確認（例: `~/.ssh/id_ed25519`）
2. 公開鍵を `gitlab.com` → User Settings → SSH Keys に追加
3. 疎通確認: `ssh -T git@gitlab.com` → `Welcome to GitLab, @<username>!`

> 鍵生成 / セキュリティ判断の詳細（パスフレーズ無し許容条件 等）は
> `draft/lab/gitlab/setup-log.md` § 「設計判断と理由」を参照。

#### Personal Access Token / `glab auth`

`kaji` は GitLab API 呼び出しを `glab` 経由で行うため、以下のいずれかで認証する:

- **(a) `glab auth login` 済み**: `glab auth login --hostname gitlab.com` で
  対話的に Personal Access Token (PAT) を登録する
- **(b) `GITLAB_TOKEN` 環境変数**: PAT を export しておく。CI / 無人スクリプト
  向け

PAT の scope は **`api`** が必須（Issue / MR の read/write、`projects` API の
read を含む）。`read_api` だけでは `kaji pr create` / `kaji pr merge` 等の
write 系で 403 になる。

> どちらの認証経路を取るかの判断は運用者が選んでよい（OQ-1 は実装中決定可と
> されており、`glab` 自身が両方を等価に扱うため kaji 側で経路選択を強制しない）。

### 1.3 GitLab project の必須前提（merge method）

`kaji` の merge 規約は **`--no-ff` only（squash merge 禁止）** であり、
`/issue-close` および `kaji pr merge` は GitHub mode と同じ guard を GitLab mode
でも適用する。`glab mr merge` には GitHub の `--merge` 相当の明示 flag が無く、
**実際の merge method は GitLab project 設定に依存** するため、対象 project 側
で以下を満たすこと:

| 設定 | 必須値 | 補足 |
|------|--------|------|
| **Merge method** | `Merge commit` | `Merge commit with semi-linear history` / `Fast-forward merge` は不可 |
| **Squash commits when merging** | `Do not allow` または `Allow` | `Encourage` / `Require` は不可（`--no-ff` only と矛盾） |

設定場所: `gitlab.com/<group>/<project>` → Settings → Merge requests。

> 由来: `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md` § 「merge method 保証
> 範囲」(ii) docs での前提明記。本文書がその責務を負う。

設定が満たされない場合の症状:

- `Squash: Require` → `kaji pr merge` 経由で squash merge が発生し、kaji の
  commit history 規約と矛盾する
- `Fast-forward merge` 強制 → merge commit が作られず、`/issue-close` が想定する
  `--no-ff` merge 履歴にならない

### 1.4 Project の `.kaji/config.toml`

`provider.type = "gitlab"` の最小設定:

```toml
# .kaji/config.toml （tracked）
[paths]
artifacts_dir = ".kaji-artifacts"
skill_dir = ".claude/skills"

[execution]
default_timeout = 1800

[provider]
type = "gitlab"

[provider.gitlab]
repo = "<group>/<project>"          # 例: "apokamo/kaji"
default_branch = "main"             # 既定 "main"
git_remote = "gitlab"               # 任意。default `"origin"`。hybrid setup での remote 名
```

要点:

- `[provider.gitlab].repo` は **`group/project` の namespace path**。`https://`
  プレフィクス / `.git` サフィックスは付けない
- `glab --repo <group>/<project>` および `glab api projects/<URL-encoded repo>`
  に渡される
- `hostname` フィールドは持たない（`gitlab.com` 固定。self-hosted 非対応）
- `default_branch` を省略すると `main`
- `git_remote` は skill 内の `git push` / `git fetch` 等が対象とする git remote
  名。**default `"origin"`**。`origin = git@github.com:...`（suspended 等）+
  `gitlab = git@gitlab.com:...` の hybrid setup では `"gitlab"` を指定する。
  `git remote get-url <git_remote>` が解決できる必要があり、未登録の場合は
  `/i-pr` / `/issue-close` 内の push / fetch が失敗する（事前に
  `git remote add gitlab git@gitlab.com:<group>/<project>.git` で登録すること）

`[provider.gitlab].repo` 未設定で `provider.type = "gitlab"` だと kaji 起動時に:

```
provider.type='gitlab' requires provider.gitlab.repo (e.g. 'group/project').
```

### 1.5 認証フロー（運用上の手順）

新しい開発機 / CI ホストで GitLab mode を有効にするときの 1 回限りの手順:

```bash
# 1. glab を install（環境ごと: brew / apt / scoop など）

# 2. 認証（(a) または (b) のどちらか）
#    (a) glab auth
glab auth login --hostname gitlab.com

#    (b) env var
export GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx

# 3. 疎通確認
glab auth status            # → "Logged in to gitlab.com as <user>"
glab api user --jq '.username'

# 4. SSH 疎通
ssh -T git@gitlab.com
```

## 2. `kaji issue` / `kaji pr` の挙動

`provider.type = "gitlab"` 配下では `kaji issue` / `kaji pr` は **GitHub mode と
同じ skill 互換 contract** で動作する（skill 側に GitHub/GitLab 分岐を持ち込ま
ない原則）。

具体的な subcommand 対応 / 引数体系吸収範囲は確定 contract:
[draft/lab/gitlab-validation/kaji-pr-mr-bridge.md](../../draft/lab/gitlab-validation/kaji-pr-mr-bridge.md)
を参照。要点のみ抜粋:

- `kaji pr create` / `view` / `list` / `comment` / `review` / `merge` /
  `review-comments` / `reviews` / `reply-to-comment` は GitLab でも同じ呼び出し
  方が通る
- `kaji pr merge` は `--squash` / `--rebase` flag を **kaji 側で拒否**（GitHub
  mode と同じ guard）
- `glab mr` 固有の sub（`approvers` / `checkout` / `diff` / `for` / `subscribe`
  / `todo` 等）は **silent passthrough せず明示的な未対応エラー** で `EXIT_INVALID_INPUT`

### `kaji issue {edit,comment} --commit` の挙動（GitLab mode）

`provider.type = "gitlab"` 配下では `kaji issue edit` / `kaji issue comment`
に `--commit` flag が渡されても **silent strip** され、`glab` への passthrough
は `--commit` 無しで実行される（実装: `kaji_harness/cli_main.py:1452`）。これは
skill が provider 非依存に `--commit` を常時付与する設計（local mode で
`chore(local)` commit を生成するための一次接点）と互換性を保つための no-op で
あり、GitLab 側は Issue / MR が forge 上の正本であるため commit を作る必要が
ない（local mode の atomic 永続化挙動の詳細は
[Local Mode CLI Guide § 6](local-mode.md#6-issueclose-の挙動local) 参照）。

## 3. `kaji sync from-gitlab` の使い方

`provider.type = "local"` 配下から `gl:N` で GitLab Issue を read-only 参照する
ための cache populate 経路。`provider.type = "gitlab"` ではこの sync は不要
（直接 API を叩く）。

```bash
# 初回 sync（[provider.gitlab].repo を config に書いておく場合）
kaji sync from-gitlab

# repo を CLI 引数で指定する場合
kaji sync from-gitlab --repo <group>/<project>

# cache から read
kaji issue view gl:42

# sync 状態の確認
kaji sync status
```

詳細は [docs/cli-guides/local-mode.md § 9b](local-mode.md#9b-kaji-sync-fromgitlab-kaji-sync-statusgitlab-cache-populate)
を参照。

## 4. `make test-large-gitlab` 実行前提

GitLab provider の実通信 E2E（子 Issue `local-p1-10` 範囲）を回す場合の
前提:

- `glab` install + 認証（§ 1.2 の (a) または (b)）
- 検証用 GitLab project（**実 production project と分離**。テストが実 MR / Issue
  を作成・close する）
  - project 側 § 1.3 の Merge method / Squash 設定を満たすこと
- 必要に応じて `KAJI_TEST_GITLAB_REPO=<group>/<project>` 等の env を設定
  （実装は子 Issue `local-p1-10` で確定。本ガイドは前提条件側のみ記述）

> 注: `make test-large-gitlab` ターゲット自体は子 Issue `local-p1-10` で
> 追加される。本文書は当該 target を回すための運用前提を先取りで集約する責務
> のみ持ち、ターゲット未追加の段階では `make: *** No rule to make target` で
> 失敗する。

## 5. トラブルシューティング

### 5.1 `'glab' CLI not found in PATH`

`provider.type='gitlab'` 配下で `kaji issue` / `kaji pr` / `kaji sync from-gitlab`
を実行したが `glab` 未 install。OS パッケージマネージャで install する。

### 5.2 `glab auth status` が `not logged in`

`glab auth login --hostname gitlab.com` を実行、または `GITLAB_TOKEN` env を
export する。CI では env 経路を推奨。

### 5.3 401 Unauthorized / `glab API authentication failed`

PAT が失効、または scope 不足（`read_api` のみ等）。GitLab → User Settings →
Access Tokens で `api` scope の PAT を再発行する。

### 5.4 `kaji pr merge` が squash で merge してしまう

GitLab project の Settings → Merge requests → Squash commits when merging が
`Encourage` / `Require` になっている。§ 1.3 の必須前提に従い `Do not allow`
または `Allow` に戻す。

### 5.5 `kaji pr` が `glab mr` 固有 sub で fail する

`approvers` / `checkout` / `diff` / `for` / `subscribe` / `todo` 等は kaji 側で
明示的に未対応として reject する設計（silent passthrough 禁止）。`glab` を
直接呼び出す必要があれば skill 経由ではなく shell から `glab mr <sub>` を
実行する。

### 5.6 `provider.type='gitlab' requires provider.gitlab.repo`

`.kaji/config.toml` の `[provider.gitlab]` セクションに `repo = "group/project"`
を追加する（§ 1.4）。

### 5.7 commit / MR description の `Fix #N` 等が無関係 GitLab issue を auto-close する

#### GitLab 仕様（公式）

GitLab は **commit message** と **merge request description** 内の以下パターン
（大小区別なし、長い文字列内の部分一致でも有効）を **auto-close keyword** として
解釈し、commit が default branch に push された時点 / MR が merge された時点で
当該 project の issue を自動 close する（公式仕様:
[GitLab Closing issues automatically / Default closing pattern](https://docs.gitlab.com/ee/user/project/issues/managing_issues.html#default-closing-pattern)）。

- `Closes #N` / `Closing #N` / `Close #N`
- `Fixes #N` / `Fixing #N` / `Fix #N`
- `Resolves #N` / `Resolving #N` / `Resolve #N`
- `Implements #N` 系

issue reference の form は `#N` / `group/project#N` / issue URL を公式が列挙する
（角括弧 `[N]` 形式は GitLab spec の closing pattern 対象に含まれない）。

Issue note / comment は本 auto-close pattern の対象 **外**。Issue 上の close は
`/close` 等の quick action という別経路で処理される。

#### kaji workflow で発生した実害

2026-05-11 smoke test 中、kaji の review workflow が生成した commit body に
**`Must Fix #N`**（N=1,2,3,...、review item index）が直接記述されており、
push 時に `Fix #1` 部分が auto-close keyword に match → 当該 GitLab project の
issue #1（= smoke tracking issue gl:1）が意図せず close された。

- 該当 commit: `3760ae2` (`fix(test_large_gitlab): pin note→approve order + diff path/line shape`)
- commit body に含まれた hazard pattern: `レビュー指摘 (Must Fix #2, #3) を反映。` / `Live observation (Must Fix #1, ...)`
- `Must Fix #1` 部分が `Fix #1` に部分一致し gl:1 が auto-close

#### 回避ルール（GitLab 仕様準拠 / 必須）

`provider.type='gitlab'` 配下で必須:

- commit body / MR description には `Closes #N` / `Fixes? #N` / `Resolves? #N` /
  `Implements? #N` を意図せず混入させない。`Must Fix #N` / `(Fix #N)` のように
  close keyword + `#` + 数字が連続する任意のテキストが対象
- review item の参照は `#N` の生表記を避け、`Must Fix item N` / `Must Fix 指摘 N` /
  `point N` 等で代替する
- close keyword + 数字を例文で書く必要があるときは数字を `<N>` placeholder にする
  （例: `Fix #<N>` / `Closes #<N>`）。GitLab は code fence 内も scan するため、
  fence で囲うだけでは不十分
- issue 参照は `gl:N` 統一とする（`#N` を使わない）
- push 前に以下 grep で hazard pattern を検出する:
  ```bash
  git log <range> --format='%B' | grep -iE '(clos|fix|resolv|implement)e[sd]?:?\s*#[0-9]'
  ```
  1 件でも match したら commit を amend して keyword 部分を placeholder 化して
  から push する。MR description も同様の grep で確認する
- push 後は `glab issue list --repo <group>/<project> --state opened` で意図しない
  close が発生していないか確認する。消えた issue があれば即 reopen し、原因
  commit / MR を特定する

#### kaji の追加運用ルール（仕様ではなく保守的防御）

GitLab 仕様外だが kaji 側で踏襲する追加ルール:

- **`Must Fix [N]` / `Fix [N]` 等の角括弧表記も使わない**（kaji 独自規約）。理由:
  GitLab 仕様の closing pattern 対象では **ない** が、review workflow の出力が
  後で commit message へ転記される導線で `[` `]` が `#` に書き換わる事故を避ける
  ため、review item index 自体を `item N` / `指摘 N` 形式に統一する
- **`kaji issue comment` 本文での `#N` 表記も避ける**（kaji 独自規約）。Issue
  note は GitLab spec の auto-close 対象外であり comment 単体で issue を close
  することはない。ただし comment 内容を後で commit / MR description へ転記する
  際に hazard pattern を持ち込まないため、起点側で抑止する

skill / workflow 側の規約と grep 手順は
[docs/dev/shared_skill_rules.md § GitLab auto-close keyword 回避](../dev/shared_skill_rules.md#gitlab-autoclose-keyword-回避)
に集約する。

## 6. 参照

- 互換 contract: [draft/lab/gitlab-validation/kaji-pr-mr-bridge.md](../../draft/lab/gitlab-validation/kaji-pr-mr-bridge.md)
- GitLab ミラー初期化ログ: [draft/lab/gitlab/setup-log.md](../../draft/lab/gitlab/setup-log.md)
- Local mode: [docs/cli-guides/local-mode.md](local-mode.md)
- Local mode 検証期間運用 Runbook: [docs/operations/local-mode-runbook.md](../operations/local-mode-runbook.md)
- 設計書: `draft/design/issue-local-pc5090-5-gitlab-provider-impl.md` /
  `draft/design/issue-local-pc5090-6-kaji-issue-pr-gitlab-passthrough.md` /
  `draft/design/issue-local-pc5090-7-gitlab-resolve-pr-context.md` /
  `draft/design/issue-local-pc5090-8-kaji-sync-from-gitlab.md`
