# 設定リファレンス（`.kaji/config.toml`）

> 本書は日本語訳（best-effort）。正本は [configuration.md](configuration.md)（英語）。
> 差異がある場合は英語版が正（[`.ja.md` ポリシー](../README.md#翻訳ファイルのポリシーjamd) 参照）。

kaji の設定ファイル `.kaji/config.toml` および overlay `.kaji/config.local.toml` の
全 section / key 仕様を集約したリファレンス。仕様の**正本（Source of Truth）**は英語版
[configuration.md](configuration.md) であり、本書はその日本語訳。各 key の型 / 既定値 /
検証規則 / 挙動はすべて `kaji_harness/config.py` を一次情報として記載する。

how-to 文脈での最小設定例は各 CLI ガイド（[GitHub Mode](../cli-guides/github-mode.md) /
[Interactive Terminal Runner](../cli-guides/interactive-terminal-runner.md)）を参照する。
本書は「key の網羅的な仕様」に責務を限定する。

## 概要

- `.kaji/config.toml` は kaji が **repository root を特定するためのマーカー**でもある。
  kaji は `--workdir` または cwd から親方向へ `.kaji/config.toml` を探索し、`.kaji/` を
  含む directory を repository root とみなす。
- config 仕様の正本は英語版 [configuration.md](configuration.md)。設定項目の型 / 既定 /
  検証を更新するときは、まず英語版を更新し（本書はその日本語訳）、各 how-to / CLI ガイドは
  最小例とリンクに留める。

## ファイルの役割

| ファイル | git 管理 | 役割 |
|---------|---------|------|
| `.kaji/config.toml` | tracked | repository default。リポジトリ標準の設定値を保持する |
| `.kaji/config.local.toml` | gitignored | 個人環境の overlay。`[execution]` / `[provider]` のみ tracked を key 単位で上書きする（[overlay merge 規則](#overlay-merge-規則) 参照） |

- overlay はマシン固有の値（`[execution]` の設定など）が必要な場合に利用者が手で作成する。
- `git worktree add` は **tracked ファイルだけを checkout** するため、gitignored の overlay は
  **新規 worktree に引き継がれない**。詳細と回避手順は
  [Git Worktree ガイド](../guides/git-worktree.md#config-overlay-kajiconfiglocaltoml-は新規-worktree-に引き継がれない)
  を参照。

## 探索ルール

`KajiConfig.discover()`（`kaji_harness/config.py`）は次の順で repository root を解決する。

1. `--workdir`（未指定なら cwd）を起点にする。
2. 起点から親方向へ歩き、`.kaji/config.toml` が存在する最初の directory を repository root とする。
3. filesystem root まで到達しても見つからなければ `ConfigNotFoundError` で停止する。

`kaji run` の config 探索起点は `--workdir` で指定できる（workflow YAML の `workdir` とは別物。
[ワークフロー作成](../dev/workflow-authoring.md) § 実行コマンド 参照）。

## overlay merge 規則

overlay（`.kaji/config.local.toml`）が tracked を上書きできるのは **`[execution]` と `[provider]` の
2 section に限られ**、いずれも **top-level section 内の key 単位**でマージする。

- `[execution]`: overlay の同名 key が tracked の同名 key を上書きする。overlay に書かれていない
  key は tracked の値を維持する。
- `[provider]`: `type` / `[provider.github]` を **deep-1 merge** できる。`[provider.github]` の
  サブテーブルは key 単位でマージされる。`type = "local"` は overlay 由来でも拒否される
  （[`[provider]`](#provider) 参照）。
- tracked と overlay の双方に `[provider]` が無い場合のみ、loader は provider を `None` として返す。
- `[incident]` も overlay 対象外。tracked `.kaji/config.toml` のみから読み、overlay に書かれた
  `[incident]` は無視される（ラベル名はリポジトリ単位の規約のため）。
- `[paths]`（`artifacts_dir` / `skill_dir` / `worktree_prefix` / `design_dir`）は overlay 対象外。`PathsConfig` は
  tracked `.kaji/config.toml` のみから組み立てられ、overlay は `[execution]` / `[provider]` の解析に
  しか渡されない（`config.py` `KajiConfig._load`）。overlay に `[paths]` を書いても無視される。

検証エラーのメッセージが「その key を実際に定義したファイル」（tracked / overlay）を指すのは、
`[execution]` の各 key（`_parse_execution` の `source()`）と、削除済みの `provider.type = "local"` に
限られる。`[provider]` のそれ以外の検証エラー（その他の `type` 値 / `[provider.github]` の型エラーなど）は
`ConfigLoadError(path, ...)` を使うため、overlay 由来の値であっても tracked `.kaji/config.toml` を指す
（`_parse_provider`）。

## section / key 仕様

各 key の「既定」は config loader 層が parse 時に採用する値である。loader の既定値と、provider 層が
path 算出時に適用する**実効 fallback** が異なる key（`worktree_prefix` / `design_dir`）は、両者を分けて記載する。

### `[paths]`

| key | 必須/任意 | 型 | 既定 | 検証規則 | 一次情報 |
|-----|----------|----|------|----------|---------|
| `artifacts_dir` | 必須 | str | —（未設定はエラー） | 相対パスは `..` 不可（repo root 脱出防止）。絶対パス / `~` 展開は許可 | `config.py` `KajiConfig._load` (`artifacts_raw`) / `KajiConfig._validate_artifacts_dir` |
| `skill_dir` | 必須 | str | —（未設定はエラー） | 相対パスのみ。絶対パス不可・`..` 不可 | `config.py` `KajiConfig._load` (`skill_dir_raw`) / `KajiConfig._validate_skill_dir` |
| `worktree_prefix` | 任意 | str | `""`（未設定） | 非空時は単一の安全な segment（`[A-Za-z0-9._-]+`、separator / 空白 / `..` / 絶対 不可） | `config.py` `KajiConfig._load` (`wt_prefix_raw`) / `KajiConfig._validate_worktree_prefix` |
| `design_dir` | 任意 | str | `""`（未設定） | repository 相対の POSIX directory。規則 V1〜V7 は下の `design_dir` 節を参照 | `design_dir.py` `validate_design_dir` / `config.py` `_DesignDirInput` / `KajiConfig._parse_design_dir` |

> **`worktree_prefix` の「設定 default」と「実効 fallback」**:
>
> - **設定 default（config loader 層）**: 未設定時の `config.paths.worktree_prefix` は **空文字 `""`**
>   （`config.py` `KajiConfig._load` の `paths_data.get("worktree_prefix", "")`）。
> - **実効 fallback（provider 層）**: worktree dir 名を算出する箇所では `worktree_prefix or "kaji"`
>   が使われる（`kaji_harness/providers/context.py:94` /
>   `kaji_harness/worktree_discovery.py:92`）。つまり空文字は path 算出時に `"kaji"` へ倒れる。
> - 結果として worktree dir 名は `kaji-<branch_prefix>-<id>` 形式になる。`.kaji/config.toml` で
>   `worktree_prefix = "kaji"` を明示しても、未設定のままでも、**生成される worktree path は同一**。
>   field 値（`config.paths.worktree_prefix`）だけが `"" → "kaji"` に変わる。

#### `design_dir`: 設計書 directory

`[paths] design_dir` で設計書の置き場所を設定する。Kaji は
`design_path = <design_dir>/issue-<id>-<slug>.md`（`design_path` コンテキスト変数、および
`kaji issue context <id>` の `design_path`）を GitHub provider 経由で注入する。
Kaji が行うのは path 値の算出・検証・注入までで、設計書の作成・commit は skill の責務。

- **設定 default（loader 層）**: 未設定（または `design_dir = ""`）のとき `config.paths.design_dir` は空文字 `""`。
- **実効 fallback**: `build_design_path` が `design_dir or "draft/design"`（`LEGACY_DESIGN_DIR`）で解決する。
  未設定なら従来どおり `draft/design/issue-<id>-<slug>.md`。`kaji config design-dir` が実効 directory
  （`draft/design` または設定値）を出力する（config 不在・不正は stderr 診断 + exit 2）。
- 可変なのは directory のみ。`issue-<id>-<slug>.md` のファイル名規約は固定。
- `[paths]` の他 key と同様、tracked `.kaji/config.toml` のみから読む（overlay 対象外）。

**security boundary**（違反は config 読込時に `ConfigLoadError`、CLI では exit 2）。`design_path` は
skill 内の shell command（`git add [design_path]` 等）へ quote なしで展開されうるため、構文的に制限する:

| # | 規則 | 拒否例 |
|---|------|--------|
| V1 | 文字列であること（strict。int / bool / list / table の暗黙変換なし） | `design_dir = 1` |
| V2 | 絶対 path でない（先頭 `/`、Windows drive / UNC） | `/srv/designs`, `C:/designs`, `//host/share` |
| V3 | 各 segment が `[A-Za-z0-9._][A-Za-z0-9._-]*`（空白 / shell metachar / 先頭 `-` / `~` 不可） | `~/designs`, `my designs`, `-x/designs`, `designs/$(id)` |
| V4 | 空 segment なし（正規形） | `designs//issues`, `designs/issues/` |
| V5 | `.` / `..` segment なし | `../designs`, `./designs`, `.` |
| V6 | `.git` segment なし（大文字小文字無視） | `.git/designs` |
| V7 | 既存 prefix を symlink 解決した実体が repository 配下で、解決自体が成功する | `designs -> /tmp/outside`、symlink loop、壊れた symlink |

**migration**（既存 repository で既定外 directory を採用する場合）:

1. `.kaji/config.toml` に `design_dir = "designs/issues"` を追加する。
2. 既存 `draft/design/issue-*.md` を `git mv` するかを判断する（Kaji は移動しない）。
3. 進行中 Issue: 旧 directory に書いた設計書は新しい `design_path` では見つからない。移動するか、
   旧設定のまま完了させてから切り替える。
4. 固定値 `draft/design/...` を変換していた repository 側 adapter（legacy path resolver）を撤去する。
5. skill は `draft/design` を直書きせず `[design_path]`（または `kaji config design-dir`）を参照する。

`artifacts_dir` の相対パスは main worktree（`provider.<type>.default_branch` を checkout している
worktree）基準で解決される（Issue #177、[ワークフロー作成](../dev/workflow-authoring.md) § 前提条件 参照）。

`kaji run-series` も同じ解決規則を使い、実行状態を
`<artifacts_dir>/_series/<series-id>/state.json`、プロセス排他を同階層の `lock` に保存する。
これらは tracked な `.kaji/series/<series-id>.yaml` 定義とは分離される。
`validate-series` と `run-series --dry-run` はこのディレクトリを作成しない。

### `[execution]`

| key | 必須/任意 | 型 | 既定 | 検証規則 | 一次情報 |
|-----|----------|----|------|----------|---------|
| `default_timeout` | 必須 | int | —（未設定はエラー） | `> 0` の整数（bool 不可） | `config.py` `KajiConfig._parse_execution` (`raw_timeout`) |
| `agent_runner` | 任意 | `"headless"` \| `"interactive_terminal"` | `"headless"` | 列挙外は `ConfigLoadError` | `config.py` `KajiConfig._parse_execution` (`raw_agent_runner`) |
| `interactive_terminal_backend` | 任意 | `"tmux"`（非推奨） \| `"herdr"` | `"tmux"` | 列挙外は `ConfigLoadError` | `config.py` `KajiConfig._parse_execution` (`raw_interactive_terminal_backend`) |
| `interactive_terminal_close_on_verdict` | 任意 | bool | `true` | bool 以外は `ConfigLoadError` | `config.py` `KajiConfig._parse_execution` (`raw_close`) |
| `failure_triage` | 任意 | bool | `true` | bool 以外は `ConfigLoadError` | `config.py` `KajiConfig._parse_execution` (`parse_bool`) |
| `auto_recover` | 任意 | bool | `false` | bool 以外は `ConfigLoadError` | `config.py` `KajiConfig._parse_execution` (`parse_bool`) |

- `agent_runner` は agent step を headless CLI で起動するかterminal pane上の対話CLIで起動するかを選ぶ。
  `interactive_terminal_backend` は `tmux` / `herdr` を選ぶ。既定は `tmux` のままで、環境による
  自動判定や暗黙fallbackは行わない。headless runnerではこのkeyは作用しない。
  `interactive_terminal` の挙動・CLI option・優先順位は
  [Interactive Terminal Runner ガイド](../cli-guides/interactive-terminal-runner.md) を参照。
  headless と interactive terminal は Claude / Codex / Antigravity に対応する。
  Antigravity は単発実行のみで、両 backend とも workflow の `resume:` を拒否する。
- **`tmux` backend は非推奨**で、後続リリースで削除される。`"herdr"` または `agent_runner = "headless"`
  へ移行する。`kaji run` は、interactive runner が tmux を使う場合（明示指定でも既定値でも）、この key の
  マージ後の値が `.kaji/config.toml` か `.kaji/config.local.toml` で明示された `"tmux"` の場合（runner に
  関係なく。削除後はこの値が headless 実行でも config 読み込みエラーになる）、
  `--interactive-terminal-backend tmux` を指定した場合に、stderr へ警告を 1 プロセス 1 回出す。
  両方のファイルから `interactive_terminal_backend = "tmux"` を削除する。詳細は
  [tmux backend deprecation](../cli-guides/interactive-terminal-runner.ja.md#tmux-backend-deprecation)。
- `interactive_terminal_close_on_verdict` は `agent_runner = "interactive_terminal"` のときのみ作用する
  （verdict 検知後に pane を閉じるか）。headless 運用では無効。

- `failure_triage` は失敗分類、triage comment、`recovery.json` / `run.log`、stderr summaryを有効にする。
  既定は`true`で、証跡記録だけを行う。
- `auto_recover` は安全gateを通過した場合にrecovery chainあたり1回だけ、固定10分待機後のchild runを
  許可する。既定は`false`で、`failure_triage = false`なら常に無効になる。interactive terminalで
  transient provider error（例: `"at capacity"`）を検出した場合にも適用する。詳細は
  [Failure Triage / Recovery CLI（日本語）](../cli-guides/failure-recovery.ja.md)を参照。

`timeout` の解決順位は step.timeout → workflow.default_timeout → `config.execution.default_timeout`
（[ワークフロー作成](../dev/workflow-authoring.md) § ステップフィールド 参照）。

### `[incident]`

任意。failure triage 第1層（`kaji_harness/recovery/`）が incident Issue の起票・重複検索・transient
遷移に使うラベル名と、運用ガイドのパスを指定する（GitHub provider のみ）。section が無ければ全 key が
既定値になり、key を省いた場合はその key だけ既定値になる。

| key | 必須/任意 | 型 | 既定 | 用途 |
|-----|----------|----|------|------|
| `kind_label` | 任意 | str | `"kaji:incident"` | 種別ラベル。起票時に付与し、重複検索のキーにする |
| `initial_status_label` | 任意 | str | `"kaji:incident:investigating"` | status の初期値。起票時に付与し、transient クローズ時に外す |
| `transient_label` | 任意 | str | `"kaji:incident:cause:transient"` | transient クローズ時に付与する。照合時の transient 判定にも使う |
| `labels_guide_path` | 任意 | str | `"docs/dev/incident-labels.md"` | incident 本文末尾「ラベル運用ガイド」リンクの参照先 |

検証規則（違反はすべて読み込み時の `ConfigLoadError`。入力境界は `extra="forbid"` / `strict=True` の
Pydantic model）:

- 未知の key は拒否する（`kind_lable` のような typo が黙って既定値に落ちるのを防ぐ）。
- すべての値は str であること（型変換しない）。
- 3 ラベルは、空文字・空白だけ・前後の空白・`,`・`"`・制御文字を含まないこと（`gh --label` はカンマで
  分割し値を CSV として解析する。REST の `labels` クエリもカンマ区切り）。
- 3 ラベルは互いに重複しないこと（大文字小文字を区別しない比較）。
- `labels_guide_path` は空でなく、空白・制御文字・`` ` ``・`(`・`)` を含まないこと（Markdown リンクに
  埋め込むため）。ファイルの存在は確認しない。

この section は **overlay 対象外**。tracked `.kaji/config.toml` のみから読み、`config.local.toml` の
`[incident]` は無視される。kaji はラベルを自動作成しない（設定したラベルは repo に存在する必要がある）。
旧名の互換検索は持たず、重複検索は `kind_label` だけで行う。

`kaji:` 接頭辞付きに変わる前の旧ラベル名を使い続ける場合（既定変更: Issue #434）:

```toml
[incident]
kind_label = "incident"
initial_status_label = "incident:investigating"
transient_label = "incident:cause:transient"
```

既存のラベルを既定名へ移行する手順は
[旧名からの移行手順](../dev/incident-labels.md#旧名からの移行手順) を参照。

各ラベルの意味は [incident ラベル運用ガイド](../dev/incident-labels.md) を参照。

### `[provider]`

`[provider]` section は config loader 層では任意で、tracked / overlay の双方に無い場合 loader は
provider を `None` として返す（`config.py` `KajiConfig._parse_provider`）。ただし `kaji issue` / `kaji pr` / `kaji run` の
provider 解決経路では `[provider]` は必須（未設定は exit 2。
[GitHub Mode CLI Guide](../cli-guides/github-mode.md) 参照）。

| key | 必須/任意 | 型 | 既定 | 検証規則 | 一次情報 |
|-----|----------|----|------|----------|---------|
| `type` | `[provider]` を書くなら必須 | `"github"` | —（`[provider]` 記載時は必須） | 列挙外は `ConfigLoadError` | `config.py` の `_parse_provider` |

`provider.type = "local"`（overlay 由来を含む）は設定エラー（exit 2）になる。local provider は kaji v0.21.0
での非推奨化を経て削除された。代わりに `provider.type = "github"` と `[provider.github] repo` を設定する。
残存する `[provider.local]` テーブルは無視される。

### `[provider.github]`

| key | 必須/任意 | 型 | 既定 | 検証規則 | 一次情報 |
|-----|----------|----|------|----------|---------|
| `repo` | 任意 | str（`owner/name`） | `""` | str 以外は `ConfigLoadError`。`https://` プレフィクスや `.git` サフィックスは付けない | `config.py` `KajiConfig._parse_provider` (`repo_raw`) |
| `default_branch` | 任意 | str | `"main"` | str 以外は `ConfigLoadError` | `config.py` `KajiConfig._parse_provider` (`gh_default_branch_raw`) |
| `git_remote` | 任意 | str | `"origin"` | str 以外は `ConfigLoadError` | `config.py` `KajiConfig._parse_provider` (`gh_git_remote_raw`) |

- `repo` は `gh --repo <owner>/<name>` / `gh api repos/<owner>/<name>/...` に渡される。設定すると
  worktree の git remote が fork を指していても書き先がズレない。
- `git_remote` は skill 内の `git push` / `git fetch` が対象とする git remote 名。release 運用では
  `provider.github.git_remote` が GitHub を指す remote 名の整合確認に使われる
  （[Release Runbook](../operations/release/runbook.md) 参照）。

## 設定例

GitHub 標準運用（`type = "github"` / headless）の最小設定。挙動に効く主要値は暗黙 default に依存させず
明示する。各 key の詳細仕様は本書の該当節を参照。

```toml
# .kaji/config.toml （tracked, repository default）
# 設定仕様の正本: docs/reference/configuration.md

[paths]
artifacts_dir = ".kaji-artifacts"
skill_dir = ".claude/skills"
worktree_prefix = "kaji"            # worktree dir 名の先頭 segment（<prefix>-<branch_prefix>-<id>）
# design_dir = "designs/issues"     # 任意: 設計書 directory（未設定 = legacy default "draft/design"）

[execution]
default_timeout = 2400
agent_runner = "headless"           # "headless"（既定） | "interactive_terminal"
# interactive_terminal_backend = "herdr"  # "tmux"（既定・非推奨） | "herdr"
# interactive_terminal_close_on_verdict = true   # interactive_terminal のときのみ作用

# [incident]                        # 任意: incident のラベル名 / ガイドパス（既定は `kaji:` 接頭辞付き）
# kind_label = "kaji:incident"

[provider]
type = "github"

[provider.github]
repo = "apokamo/kaji"
default_branch = "main"
git_remote = "origin"
```

## 関連ドキュメント

- [GitHub Mode CLI Guide](../cli-guides/github-mode.md) — GitHub provider のセットアップ / 運用
- [Interactive Terminal Runner ガイド](../cli-guides/interactive-terminal-runner.md) — `[execution] agent_runner`
- [ワークフロー作成](../dev/workflow-authoring.md) — `.kaji/config.toml` を前提とする workflow 定義
- [Git Worktree ガイド](../guides/git-worktree.md) — overlay が新規 worktree に引き継がれない注意
- [Release Runbook](../operations/release/runbook.md) — `provider.github.git_remote` の参照
