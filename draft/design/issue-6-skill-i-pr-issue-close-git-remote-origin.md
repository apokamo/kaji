# [設計] skill `i-pr` / `issue-close` の git remote `origin` hardcode を provider 種別解決に置換

Issue: gl:6

## 概要

`.claude/skills/i-pr/SKILL.md` / `.claude/skills/issue-close/SKILL.md` 内の `origin` hardcode（コマンド行 + コメント + 説明文 + echo文を含む SKILL 全文、合計 15 箇所）を、kaji harness が provider config から解決して prompt 経由で注入する `[git_remote]` placeholder に置換する。あわせて `docs/cli-guides/gitlab-mode.md` / `local-mode.md` / `github-mode.md` を `git_remote` IF 追加に合わせて更新し、`local-mode.md` には gl:8 統合分の `--commit` flag semantics も追記する。

## 背景・目的

### Observed Behavior (OB)

- skill SKILL.md 内に remote 名 `origin` が hardcode されている（Issue 本文 § Observed Behavior の表で初期 9 箇所を列挙。再現手順 § 静的検出 を **SKILL 全文** に広げた本設計時点では合計 15 箇所＝コマンド行 9 + コメント 2 + 説明文 2 + echo文 3）
- `provider.type='gitlab'` 配下で `origin = git@github.com:...` / `gitlab = git@gitlab.com:...` の hybrid setup を組むと `/i-pr` の `git push -u origin HEAD` が GitHub 側に向かい認証失敗で workflow が ABORT する（Issue 本文 § 動的観測 step 5）
- `kaji issue` / `kaji pr` 抽象は provider 別 routing が揃っているのに対し、git-native な remote 操作だけが provider 非対応で取り残されている
- `docs/cli-guides/gitlab-mode.md` / `local-mode.md` には `kaji issue {edit,comment} --commit` flag の semantics（GitHub/GitLab で silent strip / Local で `chore(local)` commit atomic 化）が記載されていない（gl:8 調査 note_3333217711）

### Expected Behavior (EB)

- `provider.type` に応じて remote 名が解決され、skill 内の git 操作（push / fetch / pull / merge-base / ls-remote）が正しい remote に向かう
- `provider.type='github'` / 未設定では従来通り `origin` にフォールバックし、既存の GitHub 経路が壊れない（regression なし）
- gl:8 統合の docs 修正で `--commit` flag の provider 別挙動（silent strip vs atomic commit）が一次情報として `docs/cli-guides/{gitlab,local}-mode.md` から参照できる

EB の裏付けは以下の一次情報:

- `kaji_harness/providers/context.py` / `prompt.py:43-56` — context 変数注入の既存規約（`default_branch` / `branch_name` / `worktree_dir` が同経路）
- `docs/cli-guides/gitlab-mode.md` § 1.4 — `[provider.gitlab]` config 形式（`repo`, `default_branch` が既存。`git_remote` を同じ形で追加）
- `kaji_harness/cli_main.py:1452` — GitLab `--commit` silent strip 実装
- `kaji_harness/cli_main.py:1188-1235` — LocalProvider `_commit_local_issue_change` の atomic 永続化実装

## 再現手順

Issue 本文 § 再現手順を正本とし、本設計では検証経路のみ要約する。

1. **静的検出**: `grep -nE '\borigin\b' .claude/skills/i-pr/SKILL.md .claude/skills/issue-close/SKILL.md` で 15 箇所（コマンド行 9 + コメント 2 + 説明文 2 + echo文 3。Issue 本文 § Observed Behavior 表に追加 6 箇所を含む）の hardcode が列挙される（修正後は 0 箇所＝SKILL 全文で `\borigin\b` が単語境界マッチしない状態にする）
2. **動的観測**: `.kaji/config.local.toml` に `provider.type='gitlab'` + `[provider.gitlab]` で `git_remote = "gitlab"` 設定 + `git remote -v` に `gitlab` が存在する状態で `kaji run workflows/feature-development.yaml gl:N` を実行 → `/i-pr` の push が `gitlab` remote に向かい MR 作成まで完走する
3. **regression**: `provider.type='github'` で `git_remote` 未指定 → 既定値 `origin` で従来通り動作する

## 根本原因

### なぜ間違っているか

skill SKILL.md は markdown テンプレートであり、`[branch_name]` / `[default_branch]` / `[worktree_dir]` 等の placeholder を harness が prompt 注入時に解決する設計（`kaji_harness/prompt.py:43-56`）。しかし remote 名だけは「実 git は `origin` 一択が慣習」という前提で **文字列リテラル** として書かれており、provider 抽象から取り残されていた。

### いつから壊れているか

- `i-pr` skill: `feat: add i-pr skill` 以降、push 命令を `origin` で固定
- `issue-close` skill: 同様に `git push origin --delete` / `git fetch origin` 等を当初から hardcode
- GitLab provider 経路（`kaji_harness/providers/gitlab.py`）導入時に remote 名抽象は追加されなかった（既存の GitHub-only 前提を引き継いだ盲点）

### 同じ原因の他箇所

`grep -nE '\borigin\b' kaji_harness/` を実施したところ、Python 実装側に `origin` hardcode は **無い**（remote 操作は skill SKILL.md 内に閉じている）。ただし以下は本 issue scope 外として注意:

- `docs/guides/git-worktree.md` 等の guide 文書内の例示 `origin` — ユーザ向け説明であり provider 抽象対象ではない（変更不要）
- `tests/` 内のテストデータ `origin` — fixture 文字列で本物の remote 操作ではない（変更不要）

## インターフェース

### 既存 IF の変更（後方互換あり）

#### 1. `provider.<type>` config に `git_remote` field 追加

`.kaji/config.toml` および `.kaji/config.local.toml`:

```toml
[provider.gitlab]
repo = "apokamo/kaji"
git_remote = "gitlab"     # NEW (任意、default "origin")

[provider.github]
git_remote = "origin"     # NEW (任意、default "origin")

[provider.local]
git_remote = "origin"     # NEW (任意、default "origin")
```

未指定時は `"origin"` にフォールバック → **既存 config は無変更で動作**（後方互換保持）。

#### 2. `IssueContext` に `git_remote: str` field 追加

`kaji_harness/providers/__init__.py` の `IssueContext` dataclass に追加。各 provider の `resolve_issue_context` 実装で config から読み取って充填する。

#### 3. prompt 注入変数に `git_remote` を追加

`kaji_harness/prompt.py:43-56` の `variables` dict に `"git_remote": issue_context.git_remote` を追加。skill SKILL.md は `[git_remote]` placeholder で参照する。

#### 4. skill SKILL.md の `origin` → `[git_remote]` 置換（**SKILL 本文全箇所**）

レビュー指摘（review-design 1st cycle）を受け、**コマンド行 / コメント / 説明文を一括で contract に揃える**。LLM は SKILL 全文をプロンプト文脈として読むため、コマンド行だけ placeholder 化しても説明文に旧契約が残ると半分温存となる。

`i-pr/SKILL.md`:

| 行 | 変更前 | 変更後 |
|-----|--------|--------|
| 160 | `git push -u origin HEAD` | `git push -u [git_remote] HEAD` |

`issue-close/SKILL.md`（コマンド行 + 説明文 + コメント、全 13 箇所）:

| 行 | 種別 | 変更前 | 変更後 |
|-----|------|--------|--------|
| 127 | 説明文 | `\`git fetch origin\` で \`origin/main\` を最新化してから...` | `\`git fetch [git_remote]\` で \`[git_remote]/[default_branch]\` を最新化してから...` |
| 132 | コメント | `# 1. fetch して origin/main を更新` | `# 1. fetch して [git_remote]/[default_branch] を更新` |
| 133 | コマンド | `git fetch origin` | `git fetch [git_remote]` |
| 137 | コマンド | `git merge-base --is-ancestor [branch_name] origin/main` | `git merge-base --is-ancestor [branch_name] [git_remote]/[default_branch]` |
| 140 | echo文 | `echo "WARNING: branch not merged into origin/main, ..."` | `echo "WARNING: branch not merged into [git_remote]/[default_branch], ..."` |
| 145 | コマンド | `git ls-remote --exit-code --heads origin [branch_name]` | `git ls-remote --exit-code --heads [git_remote] [branch_name]` |
| 148 | コマンド | `git push origin --delete [branch_name]` | `git push [git_remote] --delete [branch_name]` |
| 149 | echo文 | `echo "ERROR: git push origin --delete failed"` | `echo "ERROR: git push [git_remote] --delete failed"` |
| 160 | コマンド | `git fetch --prune origin` | `git fetch --prune [git_remote]` |
| 172 | コマンド | `git pull origin main` | `git pull [git_remote] [default_branch]` |
| 326-328 | コマンド | `git remote get-url origin` / `git fetch origin [default_branch]` / `git merge --ff-only "origin/[default_branch]"` | `git remote get-url [git_remote]` / `git fetch [git_remote] [default_branch]` / `git merge --ff-only "[git_remote]/[default_branch]"` |
| 330 | echo文 | `echo "WARNING: git fetch origin [default_branch] failed; ..."` | `echo "WARNING: git fetch [git_remote] [default_branch] failed; ..."` |
| 336 | 説明文 | `- fast-forward できない (ローカル main が origin/main から分岐) → ...` | `- fast-forward できない (ローカル [default_branch] が [git_remote]/[default_branch] から分岐) → ...` |
| 379-380 | コマンド | `git remote get-url origin` / `git push origin [default_branch]` | `git remote get-url [git_remote]` / `git push [git_remote] [default_branch]` |

**契約の一貫性**: 修正後の SKILL 本文には `\borigin\b` という単語が **コマンド行・説明文・コメントいずれにも残らない** ことを再現テストの assertion とする（後述 § テスト戦略）。例外: 一般的な git 教育目的の文（あれば）はコードフェンス外の自然言語として残す可能性があるが、本 issue 修正後の grep 結果は 0 件を目標とする。

### 後方互換性

- 既存 GitHub 経路（`git_remote` 未指定）: `"origin"` フォールバックで完全に従来通り
- 既存 skill 呼び出し: placeholder 置換のみのため呼び出し側変更なし
- prompt 注入の追加変数: 既存変数を破壊しない（追加のみ）

## 変更スコープ

### 影響モジュール

- `kaji_harness/config.py` — `GitHubProviderConfig` / `GitLabProviderConfig` / `LocalProviderConfig` に `git_remote: str = "origin"` 追加 + TOML 読み取り
- `kaji_harness/providers/__init__.py` — `IssueContext` に `git_remote` 追加
- `kaji_harness/providers/github.py` / `gitlab.py` / `local.py` — `resolve_issue_context` で `git_remote` 充填
- `kaji_harness/prompt.py` — variables dict に追加
- `.claude/skills/i-pr/SKILL.md` — コマンド行 1 箇所置換
- `.claude/skills/issue-close/SKILL.md` — **コマンド行 + コメント + 説明文 + echo文 合計 14 箇所** 置換（§ インターフェース #4 の表で全列挙）。SKILL 全文から `\borigin\b` を 0 件にする
- `docs/cli-guides/gitlab-mode.md` — § 2 に git remote 前提 + `--commit` silent strip 説明（gl:8 統合）
- `docs/cli-guides/local-mode.md` — § 影響ドキュメント の 4 点を全て更新: (a) § 2 overlay 例に `git_remote = "origin"` 追加、(b) § 6 step 6 の `git push origin [default_branch]` を `git push [git_remote] [default_branch]` に書き換え、(c) `git_remote` 上書き例の追記、(d) gl:8 統合の `--commit` flag section 新設
- `docs/cli-guides/github-mode.md` — `git_remote` 任意 field の透明化（軽微 1 行 / 1 paragraph）
- `tests/test_phase4_dispatcher_gitlab.py` 等 — `git_remote` 注入の assertion 追加
- `tests/test_skill_remote_placeholder.py` (新規) — § テスト戦略の bug 固有 regression test

### scope 外（変更しない）

- 実 git remote の作成/管理（ユーザ責務）。kaji 側は preflight で `git remote get-url <git_remote>` 解決可能性を検証するに留める（preflight 自体も初回 PR では TODO 許容）
- `docs/guides/git-worktree.md` 等の **一般 git 解説**（kaji 抽象の外、ユーザ向け git 教育文書）
- skill SKILL.md 外の markdown（`docs/dev/*.md` 等）に登場する `origin` 一般説明（kaji の挙動説明ではなく git 標準用語として用いられている箇所）

## 方針（修正アプローチ）

### 採用案: (a) IssueContext + prompt 注入（Issue 本文 § 修正方針 案 a を採用）

理由:

- 既存 `default_branch` / `branch_name` 等と **同一経路** で対称性が高く、設計負荷が最小
- skill SKILL.md は markdown 内 placeholder（コマンド行 + コメント + 説明文 + echo文を含む SKILL 全文）のみの変更で済み、shell escape リスクなし
- config に `git_remote` を集約することで「remote 名はどこで決まるか」が一次情報として明確（`kaji validate` で型検証も可能）

### 不採用案

- **(c) `kaji git push` ラッパー command**: skill 全面書換が必要 + bash パイプ操作との相性が悪い（`git fetch | merge-base` 等の組み合わせを全 wrap するのは過剰）。将来 git 操作の audit が要件化した時点で再検討
- env (`KAJI_REMOTE`) のみ: skill SKILL.md は markdown 内 `${KAJI_REMOTE:-origin}` のような shell 変数を持つことになり、placeholder 規約（`[name]`）と不整合。env で渡しても結局 placeholder 化が必要
- **`git_remote` を GitLab provider のみに限定する案**: review-design 1st cycle で代替案として提示された。**不採用**。理由:
  1. `IssueContext` の field を provider 別に optional 化すると prompt 注入経路で「あるかないか」の分岐が増え、`prompt.py:variables` dict が provider 別に異なる shape を取ることになる（既存の `default_branch` 等が全 provider 必須なのと不整合）
  2. skill SKILL.md は provider 抽象済みで「どの provider 経由でも同じ template が機能する」設計（`kaji issue` / `kaji pr` が passthrough/local 両方を吸収する設計と対称）。一部 provider のみ placeholder 化されない状態は skill 契約を分断する
  3. GitHub / Local 経路で `git_remote` を任意 field（default `"origin"`）として持つコストは TOML 1 行 + ProviderConfig 1 field のみで微小
  4. 将来 GitHub 側でも `origin != github` の hybrid setup (例: `origin = self-hosted` + `github = github.com`) が要件化した際に、再度 IF 変更を強いられる
  → 3 provider 統一で IF を切る方が長期的に整合が取れる。代償として `local-mode.md` の docs 更新が増えるが、 § 影響ドキュメント で範囲を明示し対応する

### 実装手順（概略）

1. `config.py` の 3 つの ProviderConfig に `git_remote: str = "origin"` field 追加 + TOML 読み取り（型検証含む）
2. `IssueContext` に `git_remote: str` 追加（dataclass field、provider 経由で必ず充填）
3. 各 provider の `resolve_issue_context` で `self.config.<type>.git_remote` を渡す
4. `prompt.py:variables` に `git_remote` 追加
5. skill SKILL.md 15 箇所（i-pr 1 + issue-close 14、コマンド行 + コメント + 説明文 + echo文）を `[git_remote]` / `[git_remote]/[default_branch]` に置換し、SKILL 全文で `\borigin\b` を 0 件にする
6. 既存テストで `IssueContext` 構築箇所 / prompt variables の assertion を更新
7. 新規 Small テスト: config 読み込みで `git_remote` field が反映されること（3 provider 分）
8. 新規 Medium テスト: 各 provider の `resolve_issue_context` 結果に `git_remote` が含まれること
9. docs 更新（gl:8 統合分含む）

### preflight 検証（任意）

`kaji run` 起動時に `git remote get-url <git_remote>` を実行して remote が解決できることを確認し、未解決なら警告 / ABORT。本 issue では **任意機能** として設計に記載するが、初回 PR では実装せず TODO で残す方針も許容（review 時に判断）。理由: preflight 失敗時の挙動（hard fail vs warning）に意見が分かれる可能性があり、最小修正に絞るため。

## テスト戦略

### 変更タイプ

実行時コード変更（kaji_harness） + skill SKILL.md 修正 + docs-only 追記が混在。

### Small テスト

- `tests/test_config.py` (or 相当): 各 ProviderConfig が TOML から `git_remote` を読み取れること（default `"origin"` / 明示指定 / 型エラー時の `ConfigLoadError`）
- `tests/test_prompt.py` (or 相当): `build_prompt` の variables dict に `git_remote` が含まれること

### Medium テスト

- `tests/test_phase4_dispatcher_gitlab.py` 等: GitLab provider の `resolve_issue_context` 結果に `git_remote` が正しく充填されることを assert（config に `git_remote = "gitlab"` 設定時 / 未設定時の両方）
- 同様に GitHub / Local provider についても assertion

### bug 固有: 再現テスト（regression test）

OB を assert する再現テストを 1 本以上必須:

- **Red 状態（修正前）**: `tests/test_skill_remote_placeholder.py` (新規) — `.claude/skills/i-pr/SKILL.md` および `.claude/skills/issue-close/SKILL.md` を **ファイル全体** で読み込み、`\borigin\b` の単語が **どこにも残っていない** ことを assert（コマンド行 / コメント / 説明文の全てを対象）。修正前は両ファイル合計 14+ 箇所マッチで FAIL、修正後は 0 マッチで PASS
- 補助 assertion 1: `[git_remote]` placeholder が `i-pr/SKILL.md` で ≥1 回、`issue-close/SKILL.md` で ≥10 回出現すること
- 補助 assertion 2: `[git_remote]/[default_branch]` という組み合わせ表記が `issue-close/SKILL.md` で ≥3 回出現すること（旧 `origin/main` の置換完了確認）

このテストは Small 相当（純粋な文字列パターン検査、外部 I/O なし）。例外として「git 一般用語としての `origin/<branch>` 説明」を意図的に残す必要が将来生じた場合は、テスト側に明示的な whitelist コメントを追加する規約とする（本 issue 修正時点では 0 件を目標）。

### Large テスト

- 不要。理由（`docs/dev/testing-convention.md` の 4 条件）:
  1. 実 GitLab API 接続を伴う test は既存 `make test-large-gitlab` が存在し、本 issue は既存テストの green 維持で十分（新規 Large 観点なし）
  2. 本変更の core ロジックは「config → IssueContext → prompt placeholder」の純粋データフローであり、Small + Medium で十分に網羅される
  3. 実 git push の挙動検証は手動 dogfood（gl:5 着手）に委ねる方が ROI が高い
  4. CI 安定性: Large テスト追加は失敗時の切り分けコスト増、本 issue scope では benefit < cost

### 手動検証（dogfood）

- `provider.type='gitlab'` 配下で `kaji run workflows/feature-development.yaml gl:N` を実走行し、`/i-pr` step が GitLab に push できることを確認（Issue 本文 § テスト方針）
- gl:5 着手が本 issue の真の受入

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし（既存 provider 抽象の拡張） |
| docs/ARCHITECTURE.md | なし | アーキテクチャ大枠の変更なし |
| docs/dev/ | なし | ワークフロー / 開発手順の変更なし |
| docs/reference/ | なし | API / 規約の新規追加なし |
| docs/cli-guides/gitlab-mode.md | **あり** | § 2 に git remote 前提（`git_remote` config）追記 + gl:8 統合分の `--commit` silent strip 説明 1 paragraph |
| docs/cli-guides/local-mode.md | **あり（範囲拡張）** | (a) gl:8 統合分の `--commit` flag section（LocalProvider の atomic 永続化 + `chore(local)` commit 仕様）追加、(b) § 2 overlay 例 (lines 77-85) の `[provider.local]` block に `git_remote = "origin"` を追加（コメントで「任意。default `"origin"`」と注記）、(c) § 6 `/issue-close の挙動（local）` step 6 の `git push origin [default_branch]` を `git push [git_remote] [default_branch]` に書き換え、(d) 同 section 末尾に「`git_remote` を上書きする例（local + 外部 mirror remote 連携）」を 3-5 行で追記 |
| docs/cli-guides/github-mode.md | **あり（軽微）** | overlay 例があれば `[provider.github]` block に `git_remote = "origin"` の任意 field を 1 行追記（既存挙動の透明化）。なければ「§ X に `git_remote` field 説明」として 1 paragraph 追加。実装範囲は実 docs を見て判断 |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| kaji prompt 注入規約 | `kaji_harness/prompt.py:43-56` | `variables` dict に `default_branch` 等を追加する既存経路。本設計はこの拡張として `git_remote` を追加 |
| IssueContext 構築規約 | `kaji_harness/providers/context.py` | `branch_name` / `worktree_dir` / `design_path` を純粋関数で構築する既存規約。`git_remote` は config から直接渡すため新関数追加は不要 |
| GitLab provider config | `kaji_harness/providers/gitlab.py:80-86` | `default_branch: str = "main"` の field 定義パターン。`git_remote: str = "origin"` も同形式で追加 |
| GitHub silent strip 実装 | `kaji_harness/cli_main.py:844` | `kaji issue --commit` の GitHub passthrough 時に `--commit` を除去するロジック。gl:8 統合 docs の出典 |
| GitLab silent strip 実装 | `kaji_harness/cli_main.py:1452` | 同上 GitLab 版。gl:8 統合 docs の出典 |
| LocalProvider atomic commit | `kaji_harness/cli_main.py:1188-1235` | `_commit_local_issue_change` 実装。gl:8 統合 local-mode.md docs の出典 |
| GitLab mode 既存 docs | `docs/cli-guides/gitlab-mode.md` § 1.4 / § 2 | `[provider.gitlab]` config 形式と `kaji issue` 挙動の現行記述。追記対象 |
| Local mode 既存 docs | `docs/cli-guides/local-mode.md` | LocalProvider の現行記述。`--commit` flag section の追加対象 |
| gl:8 調査結果 | gl:8 note_3333217711 / note_3333264129 | docs gap の判定マトリクスと案 B2 採用経緯 |
| git push 仕様 | https://git-scm.com/docs/git-push | `git push <remote> <refspec>` の引数仕様。remote 名引数化は git 標準機能であり追加実装不要 |
| testing convention | `docs/dev/testing-convention.md` | テストサイズ判定と「テスト追加しない」4 条件の根拠 |
