---
id: local-p1-23
title: gitlab provider が glab の --hostname を全 subcommand に注入し issue/mr 経路が不通
state: open
slug: glab-hostname-flag-incompat
labels:
- type:bug
created_at: '2026-05-10T14:25:34Z'
---
## 概要

`kaji_harness` の GitLab provider が `glab` 起動時に `--hostname gitlab.com` を全 subcommand に強制注入しているが、`glab` v1.36.0 / v1.95.0 双方で `--hostname` は `glab api` 専用 sub-flag として実装されている。結果として `kaji issue list` / `kaji issue create` 等の `glab issue` / `glab mr` を経由する全 mutating パスが起動直後に `Unknown flag: --hostname` で reject され、`provider.type='gitlab'` 配下では `glab api` を直叩きする `kaji sync from-gitlab` のみ偶然動作する。

## 目的

### Observed Behavior（OB）

`.kaji/config.local.toml` overlay を `provider.type = "gitlab"` (`apokamo/kaji`) に切り替え、smoke test を実施した結果:

```
$ kaji issue list --limit 5
unknown flag: --hostname

Usage:  glab issue list [flags]

Flags:
  -A, --all                    Get all issues
  -a, --assignee string        Filter issue by assignee <username>
  ...
exit: 1
```

```
$ kaji sync from-gitlab
Fetching open issues from gitlab.com:apokamo/kaji ...
Wrote 0 issues to .kaji/cache/ (0 newly added, 0 updated, 0 unchanged signature).
Sync completed at 2026-05-10T14:07:20Z (0 issues, 0 pages, 1.4s).
exit: 0
```

`glab` を直接叩いて切り分けた結果（再現環境: WSL2 Ubuntu, glab v1.95.0）:

```
$ glab api --hostname gitlab.com user                       → 200 OK (apiは受理)
$ glab issue list --hostname gitlab.com --repo apokamo/kaji → ❌ Unknown flag: --hostname
$ glab --hostname gitlab.com issue list --repo apokamo/kaji → ❌ Unknown flag: --hostname
```

glab 1.36.0 → 1.95.0（公式最新、2026-05-08 リリース）に bump しても再現する一貫した挙動。

### Expected Behavior（EB）

`provider.type = "gitlab"` 配下で `kaji issue list` / `kaji issue create` / `kaji issue note` / `kaji issue close` / `kaji pr ...` が `Unknown flag` を出さずに `glab` を起動でき、`glab` が `gitlab.com` を target host として API を叩く。

根拠:

- `docs/cli-guides/gitlab-mode.md` § 1.4 に `provider.type='gitlab'` 配下では `glab issue` / `glab pr` が「GitHub mode と同じ skill 互換 contract」で動作することが明記されている
- `glab --help` 出力に `GITLAB_HOST or GL_HOST: Specify the URL of the GitLab server if self-managed.` と記載されており、hostname 切替は **環境変数経由が正しい仕様**
- `glab api --help` でのみ `--hostname` フラグが定義されている（global flag ではなく api 専用 sub-flag）

### 再現手順（Steps to Reproduce）

1. 前提環境: `glab` CLI が install 済（v1.36 以上で同一挙動）、`glab auth login` 済（PAT scope `api`）、`gitlab.com:<group>/<project>` への SSH 疎通済
2. `.kaji/config.local.toml` を以下に切替:
   ```toml
   [provider]
   type = "gitlab"

   [provider.gitlab]
   repo = "apokamo/kaji"
   default_branch = "main"
   ```
3. `kaji config provider-type` で `gitlab` が解決されることを確認
4. `kaji issue list --limit 5` を実行
5. 観測される出力: `unknown flag: --hostname` で exit 1

別経路の確認:

- `kaji sync from-gitlab` は exit 0 で成功する（`glab api projects/...` 経路のため `--hostname` を受理）
- 同 overlay 配下で `kaji issue create` / `kaji issue note` / `kaji issue close` / `kaji pr create` / `kaji pr merge` / `kaji pr note` / `kaji pr approve` も同じ `unknown flag` でエラーになると見込まれる（dispatcher が `_forward_to_glab` または `_run_glab` 経由で `glab issue` / `glab mr` を起動するため）

## 完了条件

- [ ] 設計書で根本原因（`--hostname` の glab 仕様誤認 = `api` 専用 sub-flag を global と誤って扱った）と修正方針（引数注入を停止し、環境変数 `GITLAB_HOST` 経由で hostname を渡す）が特定されている
- [ ] 同根の他の壊れ箇所の調査結果が設計書に記載されている（`kaji_harness/{providers/gitlab.py:94, cli_main.py:1384, sync.py:114}` の 3 箇所が同パターン。3 つ目は `glab api` 起動のため偶然動作している点も含めて記載）
- [ ] `kaji_harness/providers/gitlab.py:_run_glab` / `kaji_harness/cli_main.py:_forward_to_glab` / `kaji_harness/sync.py:_glab_api_get` の 3 箇所から `--hostname <host>` の引数注入を削除し、`subprocess.run(..., env={**os.environ, "GITLAB_HOST": _GITLAB_HOSTNAME})` で hostname を渡している
- [ ] 再現テストが 1 本以上追加され、修正前は FAIL（subprocess 起動 args に `--hostname` が含まれる、または `glab issue list` が `Unknown flag` を返す挙動を fake で再現）、修正後は PASS することを確認
- [ ] 影響モジュール（`tests/` の gitlab provider / cli_main dispatcher / sync 関連既存テスト）が green。subprocess args/env を assert している既存テストは `--hostname` 参照を削除し env 注入を assert する形に更新する
- [ ] `make check` 通過
- [ ] 実機 smoke 再現: 修正後の HEAD で `provider.type=gitlab` overlay を再適用し、`kaji issue list` が `Unknown flag` を出さずに（権限/ラベル等の別要因を除き）正常に `glab issue list` を起動できることを確認

## 影響範囲（初期評価）

- 影響するモジュール / コマンド:
  - `kaji_harness/providers/gitlab.py:_run_glab`（GitLabProvider 全 mutating: issue create/edit/note/close、mr review 経由の approve/revoke、その他）
  - `kaji_harness/cli_main.py:_forward_to_glab`（`kaji issue` / `kaji pr` の GitLab dispatcher 全経路: create/view/list/edit/close/comment/merge/note/approve/revoke）
  - `kaji_harness/sync.py:_glab_api_get`（`glab api` 経路。現状偶然動作しているが、引数注入の整合性のため同様に env に統一する）
- 深刻度: 中〜高（gitlab provider の write 系全経路が起動直後に reject されるため、`provider.type='gitlab'` 配下での運用が成立しない。dev 検証フェーズ中で運用 blocker ではないが、gitlab mode 本格採用の前提条件）
- 回避策の有無: なし。`provider.type='gitlab'` 配下では `glab` を別途手動で叩くしかなく、kaji の skill ワークフローを通せない。`provider.type='local'` に戻せば作業継続は可能（現状の運用形態）

## スコープ外

- self-managed GitLab への対応（既存 `docs/cli-guides/gitlab-mode.md` § 1.4 で明示的に non-goal、本 fix とは独立）
- `glab` CLI バージョンの最低要件文書化（別 docs 改善 Issue で扱う）
- gitlab provider の他の glab 仕様誤認（実装中に発見次第、別 Issue に切り出す）
- write 系 smoke test 自体（`kaji issue create` 〜 `kaji pr merge` の実機通し検証は本 fix のマージ後に別作業として実施）

## 参考

- 関連 Issue: `local-p1-22`（kaji step runner の独立した別 bug。同じ検証セッション中に発見）
- 関連実装:
  - `kaji_harness/providers/gitlab.py:78-104`（`_run_glab` / `_glab_api_get`）
  - `kaji_harness/cli_main.py:1365-1389`（`_forward_to_glab`）
  - `kaji_harness/sync.py:104-128`（`_glab_api_get`）
- glab CLI 仕様:
  - `glab --help` 出力の `GITLAB_HOST or GL_HOST` 環境変数記載（hostname 切替の正規ルート）
  - `glab api --help` で `--hostname` が sub-flag として定義されている（他 subcommand には無い）
- 既存 docs: `docs/cli-guides/gitlab-mode.md` § 1.4 で「`hostname` フィールドは持たない（`gitlab.com` 固定。self-hosted 非対応）」と明記
- 観測環境: WSL2 Ubuntu, glab 1.95.0 (公式 .deb 最新), `apt-mark hold glab` 済（apt 自動 downgrade を抑止）
- 検証実施日: 2026-05-10。`.kaji/config.local.toml` を gitlab に切替 → smoke test → 障害発見 → local 切り戻しの順で実施。本 Issue 作成時点で provider は local に復帰済み
