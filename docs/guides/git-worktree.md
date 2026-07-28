# Git Worktree ガイド

Bare Repository + Worktree パターンによる並列開発環境の構築・運用ガイド。

> **本ドキュメントの構成**: 前半は汎用的な Bare Repository パターン、後半（「[kaji プロジェクトでの運用](#kaji-プロジェクトでの運用)」）は kaji 固有の通常リポジトリ + worktree パターンを記載している。

## 概要

Git Worktree を使用することで、1つのリポジトリで複数のブランチを同時に作業ディレクトリとして展開できる。これにより：

- **並列開発**: 複数のブランチで同時に作業可能
- **コンテキスト切り替え不要**: `git checkout` なしでディレクトリ移動のみ
- **AI並列開発**: 各worktreeで独立したClaude Codeセッション実行可能

## 推奨構成: Bare Repository パターン

```
/home/user/dev/project-name/        # プロジェクトコンテナ
├── .bare/                          # bare git repository (実データ)
├── .git                            # ポインタファイル → .bare を参照
├── main/                           # worktree (main ブランチ)
├── feature-xxx/                    # worktree (feature-xxx ブランチ)
└── issue-42/                       # worktree (issue-42 ブランチ)
```

### 構成のメリット

| 観点 | メリット |
|------|----------|
| 整理性 | 1リポジトリ = 1ディレクトリ、他リポジトリと混ざらない |
| 分離 | bare repo は純粋なGitデータ、worktree がファイル操作 |
| AI並列開発 | 各worktreeで独立したClaude Codeセッション実行可能 |
| コンテキスト保持 | ブランチごとに会話履歴・状態が保持される |

## セットアップ手順

### 新規リポジトリの場合

```bash
# 1. プロジェクトコンテナ作成
mkdir -p /home/user/dev/project-name
cd /home/user/dev/project-name

# 2. GitHubリポジトリ作成（READMEを含めて初期コミットを作成）
gh repo create username/project-name --public \
  --description "Project description" \
  --add-readme

# 3. bare repository として初期化
git clone --bare git@github.com:username/project-name.git .bare

# 4. .git ポインタファイル作成
echo "gitdir: ./.bare" > .git

# 5. fetch 設定追加
git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"

# 6. main worktree 作成
git worktree add main main
```

> **Note**: `--add-readme` オプションで初期コミットが作成される。
> これがないと空リポジトリとなり、`git worktree add main main` が失敗する。

### 既存リポジトリの移行

```bash
# 1. 既存リポジトリをbare形式でクローン
cd /home/user/dev
mkdir project-name
cd project-name
git clone --bare git@github.com:username/project-name.git .bare

# 2. .git ポインタファイル作成
echo "gitdir: ./.bare" > .git

# 3. fetch 設定追加
git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"

# 4. main worktree 作成
git worktree add main main
```

## 日常運用

### Worktree の作成

```bash
# プロジェクトルートから実行
cd /home/user/dev/project-name

# 新規ブランチでworktree作成
git worktree add -b feature/new-feature ./feature-new-feature main

# 既存ブランチでworktree作成
git worktree add ./hotfix-123 hotfix/123
```

### Worktree の一覧表示

```bash
git worktree list
```

### Worktree の削除

```bash
# worktreeディレクトリを削除
git worktree remove ./feature-new-feature

# ブランチも削除する場合（マージ済み）
git branch -d feature/new-feature

# ブランチも削除する場合（強制）
git branch -D feature/new-feature
```

### ブランチ切り替え

```bash
# git checkout は使わない
# 代わりにディレクトリ移動
cd ../feature-xxx
```

## kaji プロジェクトでの運用

kaji では Bare Repository パターンではなく、**通常リポジトリ + worktree** パターンを採用している。
Issue ごとに worktree を作成し、並列開発を実現する。

### ディレクトリ構成

```
/home/user/dev/
├── kaji/                           # メインリポジトリ (main ブランチ)
├── kaji-feat-42/                   # worktree (feat/42 ブランチ)
├── kaji-fix-73/                    # worktree (fix/73 ブランチ)
└── kaji-docs-79/                   # worktree (docs/79 ブランチ)
```

### 命名規則

| 項目 | パターン | 例 |
|------|----------|-----|
| ブランチ名 | `[prefix]/[issue-number]` | `feat/42` |
| ディレクトリ | `../kaji-[prefix]-[issue-number]` | `../kaji-feat-42` |

### スキルによる自動化

worktree のライフサイクルはスキルで管理される:

- `/issue-start [issue-number]`: worktree 作成、`.venv` シンボリックリンク、Issue 本文にメタ情報追記
- `/pr-fix [issue-number]`: PR レビュー指摘対応を **同じ worktree** で実行
- `/pr-verify [issue-number]`: 指摘修正の収束確認を行う（新規指摘は禁止）
- `/issue-close [issue-number]`: `.venv` symlink 削除、worktree 削除、ブランチ削除、PR マージ

手動で worktree を削除する場合は、`.venv` シンボリックリンクを先に削除する必要がある（untracked file があると `git worktree remove` が失敗する）:

```bash
rm ../kaji-feat-42/.venv
git worktree remove ../kaji-feat-42
git branch -d feat/42
```

### worktree のスコープ運用ルール

PR 作成は worktree のゴールではなく中間チェックポイントである。`/issue-close` を実行するまでは worktree を残し、PR レビュー指摘対応も同一 worktree 上で完結させる。

- **PR 作成後も worktree は残す**: `/i-pr` 完了時点では worktree を削除しない。`/issue-close` の実行までが worktree のスコープである
- **PR レビュー指摘対応は同じ worktree で実施**: 別 worktree や `main` ブランチに切り替えず、`/pr-fix` を **同じ worktree 内で** 実行する。これにより branch / venv / artifacts の整合が崩れない
- **`/issue-close` を経由してから削除**: `gh pr merge` を直接叩くのではなく `/issue-close` を経由することで、`.venv` symlink 削除 → worktree 削除 → ブランチ安全削除の順序が保証される

### .venv の共有

各 worktree はメインリポジトリの `.venv` へのシンボリックリンクを使用する:

```bash
ln -s /home/user/dev/kaji/.venv /home/user/dev/kaji-feat-42/.venv
```

> **⚠️ 注意**: `.venv` を共有しているため、worktree 内での `uv pip install` はメインリポジトリの環境にも影響する。`pyproject.toml` の依存関係を変更する場合は、個別の venv を作成して検証すること。

### provider overlay (`.kaji/config.local.toml`) は新規 worktree に引き継がれない

`.kaji/config.local.toml`（provider overlay）は `.gitignore` 管理されている。`git worktree add` は **コミット済み（tracked）ファイルだけを checkout** するため、gitignored な overlay は新規 worktree にコピーされない。

その結果、overlay でメインリポジトリと異なる provider（例: tracked `config.toml` は `type = "github"`、overlay は `type = "local"`）を選んでいる場合、**新規 worktree では overlay が効かず tracked default にフォールバックする**。`kaji issue` / `kaji pr` / `kaji run` が意図と異なる provider に routing され得る。

worktree 作成後に overlay を使う場合は、いずれかで揃える:

```bash
# メインリポジトリから overlay をコピーする
cp /home/user/dev/kaji/.kaji/config.local.toml ../kaji-feat-42/.kaji/config.local.toml

# または当該 worktree で再初期化する
cd ../kaji-feat-42 && kaji local init
```

> **WARN**: overlay 不在の worktree から provider 解決を伴うコマンドを実行し、かつメインリポジトリの overlay が異なる `provider.type` を選んでいる場合、kaji は stderr に WARN を出して気付かせる（コマンド自体は従来どおり続行する）。詳細は [`docs/cli-guides/local-mode.md`](../cli-guides/local-mode.md) § 3「provider 切替」を参照。

`.kaji/config.toml` / `.kaji/config.local.toml` の役割と全 key 仕様は
[設定リファレンス](../reference/configuration.md) を参照。

## Serena code intelligence の運用（Codex 専用・optional）

本節が Serena を含む worktree 運用の**正本**である（Issue #78）。

Serena は **Codex 向けの optional な LSP / symbol-navigation tool** として扱う。
memory を持たない交換可能な code intelligence 層であり、チーム知識の保管先にはしない。
永続知識の正本は `AGENTS.md` / `docs/` / skills / Issue コメント / design docs に一本化する。

### 対応 agent

| Agent / surface | Serena 方針 |
|---|---|
| Codex CLI | worktree root から新規起動した場合のみ任意利用 |
| Codex App | 絶対パス activate + root 照合後のみ任意利用 |
| Claude Code | 使用しない。公式 Pyright LSP plugin を任意利用 |
| Claude Agent Teams / worktree-isolated subagent | 使用しない |
| Antigravity | 使用しない（root lifecycle を検証するまで対象外） |

Claude 系を非対象とする理由: Claude Code はセッション途中の worktree 切替（`EnterWorktree`、
セッション中の `git worktree add`、primary checkout から worktree への移動を含む）で
MCP `roots` の変更通知を送らず、Serena は元の active root を保持し続ける
（[serena#1496](https://github.com/oraios/serena/issues/1496)）。
同名ファイルが両 checkout に存在すると、エラーなく誤った checkout を読み書きしうる。
このため Claude Code には Serena を登録しない。

### 利用できる機能（Python + Pyright LSP backend）

- symbol search / symbol overview
- references の列挙
- declaration / definition への移動
- implementations（language server の対応状況に依存）
- diagnostics
- symbol rename、symbol body の置換、symbol 前後への挿入

**type hierarchy、symbol / file / directory move、inline refactoring は JetBrains backend
専用であり、この構成では利用できない。**

### `rg` / native tools との使い分け

`rg` + native read/edit を既定とし、Serena は semantic な操作が必要な時だけ使う。

| 先に `rg` / native tools | Serena を使う |
|---|---|
| literal string・ログ文言・CLI option の検索 | symbol の definition / references / implementations |
| Markdown / YAML / TOML / JSON | 同名文字列を巻き込まない rename |
| file inventory、小さな局所変更 | class / function 単位の構造把握 |
| 正規表現で十分な調査 | 複数ファイルにまたがる semantic refactor |
| | 大きなファイルでの symbol 単位の挿入・置換 |

品質ゲートは従来通り `make check` である。Serena / LSP の diagnostics は補助であり、
判定の正本にしない。

### インストールと version pin

安定版を明示的に固定する。GitHub `main` の直接実行（`uvx --from git+...`）は、
再現性・障害切り分け・supply-chain の観点から行わない。

```bash
uv tool install -p 3.13 'serena-agent==1.6.1'
```

version を更新する場合は、CHANGELOG と config schema の差分を確認したうえで、
後述の移行手順（退避 → 再生成 → 再 index → root 照合）で再検証する。このとき
custom context `codex-kaji`（後述）と新 version の built-in `codex` context の差分も確認し、
excluded_tools の乖離があれば追従させる。

### custom context `codex-kaji`（native-first 整合）

**built-in の `codex` context はそのまま使用しない。** v1.6.1 の
[`contexts/codex.yml`](https://github.com/oraios/serena/blob/v1.6.1/src/serena/resources/config/contexts/codex.yml)
は「Serena tools を優先し、code file 全体の native read を避ける」ことを agent に指示しており、
本節の native-first 方針（`rg` / native read-edit を既定、Serena は semantic 操作のみ）と矛盾する。
`--add-mode no-memories` は memory / onboarding tools を除外するだけで、
context の tool-selection prompt は上書きしない。

そのため、tool-selection instruction を native-first に差し替えた custom context を
`~/.serena/contexts/codex-kaji.yml` に作成し、名前で参照する
（`--context` は built-in context 名または custom context YAML の path を受け付ける。
`SERENA_HOME` を設定している場合は `$SERENA_HOME/contexts/`）。

```bash
# 雛形を built-in codex からコピーして作成し、prompt を下記内容へ差し替える
serena context create -n codex-kaji --from-internal codex
serena context edit codex-kaji
```

`~/.serena/contexts/codex-kaji.yml` の内容:

```yaml
description: kaji Codex context - native-first (derived from built-in codex)
prompt: |
  You are running in the Codex IDE assistant mode, where file operations, basic (line-based) edits and reads
  as well as shell commands are handled by your own, internal tools.
  Don't attempt to use any excluded tools; instead, rely on your own internal tools for basic file or shell operations.

  Default to your own native tools (ripgrep-based search, file reads and edits) for exploration and editing.
  Use Serena's tools only when the task requires semantic code operations, such as listing symbol
  references, safe renames, symbol-level overviews of large files, or multi-file semantic refactorings.
  Do not use Serena's tools for plain-text, config, or documentation searches.

excluded_tools:
  - create_text_file
  - read_file
  - execute_shell_command
  - replace_content
  - find_file
  - list_dir

tool_description_overrides: {}
```

`excluded_tools` は built-in `codex` と同一に維持する。これは Codex 内蔵の file / shell 操作と
重複する Serena 側 tool の除外であり、native-first 方針と整合する（差し替えるのは prompt のみ）。

Codex 設定例:

```toml
[mcp_servers.serena]
startup_timeout_sec = 15
command = "serena"
args = [
  "start-mcp-server",
  "--project-from-cwd",
  "--context=codex-kaji",
  "--add-mode", "no-memories",
  "--open-web-dashboard", "false",
]

# 任意。利用統計を送らない場合
[mcp_servers.serena.env]
SERENA_USAGE_REPORTING = "false"
```

- **memory / onboarding は `no-memories` mode で無効化する**。知識を捨てるためではなく、
  レビュー可能な正本（リポジトリ側）へ一本化するため
- hooks は**既定で無効**とする。Serena の reminder hook は `rg` / native read を減らして
  Serena 利用を促す設計であり、kaji の用途限定方針（semantic task でのみ任意利用）と
  衝突する。将来有効化する場合は、その時点の Codex / Serena の組合せで hook contract と
  tool-selection への影響を再検証する

#### 起動 instruction の再検証

custom context 導入後、および version 更新後は、実際に注入される instruction を確認する:

```bash
serena print-system-prompt --context codex-kaji --only-instructions [worktree_root]
```

- `Context description:` 節が上記 native-first 文言になっており、built-in `codex` の
  「prioritize them」「avoid reading entire source code files」指示が含まれないこと
- MCP client 側の tool 一覧に file / shell 系（`read_file` / `execute_shell_command` 等）と
  memory / onboarding 系 tool が現れないこと（`excluded_tools` + `no-memories` の効果）

> **Note**: `print-system-prompt` は未登録 path を渡すと project 登録と
> `.serena/project.yml` 生成の副作用がある。移行手順で `project.yml` を再生成した後の
> worktree に対して実行する。また、instruction 冒頭の汎用部（"You have semantic coding
> tools..."）は Serena tool を使う場面での効率指針であり、tool 選択の既定は
> `Context description:` 節の native-first 文言が定める。

### root 確定手順

Serena server は active project を 1 つだけ保持する stateful な stdio MCP server である。
**symbol read/edit の前に、active root が現在の worktree と絶対パスで一致することを必ず照合する。**
project 名（basename）だけの照合は禁止（複数 repository / worktree で同名 basename が使われうる）。

#### Codex CLI

1. 対象 worktree の root へ移動する
2. その root から新しい Codex プロセスを起動する（セッション使い回し禁止）
3. Serena の active root を確認してから symbol read/edit を行う

#### Codex App

Codex App は project directory からセッションを開始しないため、`--project-from-cwd` の
初期判定を信用せず、次の手順で root を確定する。

1. セッション開始時に現在の workspace を**絶対パス**で activate する
2. `get_current_config` で active project path を取得する
3. `git rev-parse --show-toplevel` の結果と**絶対パスで**一致することを確認する
4. 一致しない限り symbol read/edit を行わない

### worktree ごとの process / cache 分離

```text
worktree A ── Codex session A ── stdio Serena A ── project A / cache A
worktree B ── Codex session B ── stdio Serena B ── project B / cache B
```

- worktree ごとに独立した stdio Serena process を起動する。複数 session / worktree から
  同一 server を共有し active project を切り替える構成は禁止
- language-server cache は絶対パスを含むため、**worktree 間での共有・コピー・symlink は禁止**
  （[serena#1455](https://github.com/oraios/serena/issues/1455)）。各 worktree で再 index する

### 既存 `.serena` の移行手順（開発版 → 安定版）

開発版（例: `1.6.2.dev0`）が生成した `.serena/project.yml`（`language_servers` 形式）は、
安定版 v1.6.1 の `languages` 必須スキーマと互換性がない。起動引数の変更だけでは
切り替えられないため、一度限りのローカル cleanup を行う。

1. 既存 `.serena/` をリポジトリ外へ退避する（復元用。repository へ push しない）
2. 安定版を固定インストールする: `uv tool install -p 3.13 'serena-agent==1.6.1'`
3. Codex 設定を `uvx --from git+...` から固定版 `serena` コマンド起動へ変更する
   （上記設定例。custom context `codex-kaji` の作成を含む）
4. v1.6.1 で `project.yml` を再生成し、再 index する。worktree root で実行する:

   ```bash
   serena project index [worktree_root] --language python
   ```

   `project.yml` が存在しない場合は自動生成される。生成された `.serena/project.yml` が
   `languages: ["python"]` 形式であることを確認する
5. 開発版が生成した cache は引き継がない。各 worktree でも同じ `serena project index` を
   実行して再 index する
6. stale な既存 memory は復元しない（退避先に残すのみ）
7. `no-memories` が有効であることを確認する
8. `get_current_config` で active project root が現在の worktree の絶対パスと一致することを確認する

### `.serena/` の ignore 方針

Serena 公式は project config / memories 共有のため `.serena` の version 管理を推奨している
（[Serena and Git Worktrees](https://oraios.github.io/serena/02-usage/999_additional-usage.html#serena-and-git-worktrees)）。
一方 kaji では、Serena memory を使用せず、Serena 自体を個人環境の optional tool とするため、
**公式推奨から意図的に逸脱して `.serena/` 全体の ignore を維持する**。

### 効果測定（スコープ外）

Serena LSP の費用対効果の実測（A/B pilot）と継続可否判断は、本 docs の対象外とし
follow-up Issue で行う。実験設計要件は
[Issue #78 改訂版方針コメント](https://github.com/apokamo/kaji/issues/78#issuecomment-5093775746)
§7 を参照。

### 参照

- [Serena and Git Worktrees（公式）](https://oraios.github.io/serena/02-usage/999_additional-usage.html#serena-and-git-worktrees)
- [serena#1496（Claude の worktree 切替で active root が再バインドされない）](https://github.com/oraios/serena/issues/1496)
- [serena#1455（cache は worktree 間でコピー不可）](https://github.com/oraios/serena/issues/1455)
- [Issue #78 改訂版方針コメント（決定経緯の正本）](https://github.com/apokamo/kaji/issues/78#issuecomment-5093775746)

## 運用ルール

### Do

- ディレクトリ移動でブランチ切り替え (`cd ../feature-xxx`)
- worktree管理はプロジェクトルートから実行
- 各worktreeでupstream設定 (`git branch --set-upstream-to=origin/xxx`)

### Don't

- `git checkout` を使わない（ディレクトリ移動で対応）
- プロジェクトルートで一般的なgitコマンドを実行しない（Bare Repository パターンの場合。通常リポジトリでは問題ない）

## 参考資料

- [Git 公式 `git-worktree` マニュアル](https://git-scm.com/docs/git-worktree)
- [How to use git worktree and in a clean way](https://morgan.cugerone.com/blog/how-to-use-git-worktree-and-in-a-clean-way/)
- [Bare Git Worktrees AGENTS.md](https://gist.github.com/ben-vargas/fd99be9bbce6d485c70442dd939f1a3d)
- [Git Worktree Best Practices and Tools](https://gist.github.com/ChristopherA/4643b2f5e024578606b9cd5d2e6815cc)
- [incident.io: Shipping faster with Claude Code and Git Worktrees](https://incident.io/blog/shipping-faster-with-claude-code-and-git-worktrees)
- [Parallel AI Coding with Git Worktrees](https://docs.agentinterviews.com/blog/parallel-ai-coding-with-gitworktrees/)
