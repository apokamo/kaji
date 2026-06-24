# 設定リファレンス（`.kaji/config.toml`）

> English version: [configuration.en.md](configuration.en.md)

kaji の設定ファイル `.kaji/config.toml` および overlay `.kaji/config.local.toml` の
全 section / key 仕様を集約した**正本（Source of Truth）**。各 key の型 / 既定値 / 検証規則 /
挙動はすべて `kaji_harness/config.py` / `kaji_harness/local_init.py` を一次情報として記載する。

how-to 文脈での最小設定例は各 CLI ガイド（[GitHub Mode](../cli-guides/github-mode.md) /
[Local Mode](../cli-guides/local-mode.md) /
[Interactive Terminal Runner](../cli-guides/interactive-terminal-runner.md)）を参照する。
本書は「key の網羅的な仕様」に責務を限定する。

## 概要

- `.kaji/config.toml` は kaji が **repository root を特定するためのマーカー**でもある。
  kaji は `--workdir` または cwd から親方向へ `.kaji/config.toml` を探索し、`.kaji/` を
  含む directory を repository root とみなす。
- 本ファイルは config 仕様の正本である。設定項目の型 / 既定 / 検証を更新するときは、
  まず本書を更新し、各 how-to / CLI ガイドは最小例とリンクに留める。

## ファイルの役割

| ファイル | git 管理 | 役割 |
|---------|---------|------|
| `.kaji/config.toml` | tracked | repository default。リポジトリ標準の設定値を保持する |
| `.kaji/config.local.toml` | gitignored | 個人環境の overlay。tracked の値を key 単位で上書きする |

- overlay は `kaji local init` が生成する（[Local Mode CLI Guide](../cli-guides/local-mode.md) § 2 参照）。
- `git worktree add` は **tracked ファイルだけを checkout** するため、gitignored の overlay は
  **新規 worktree に引き継がれない**。詳細と回避手順は
  [Git Worktree ガイド](../guides/git-worktree.md#provider-overlay-kajiconfiglocaltoml-は新規-worktree-に引き継がれない)
  を参照。

## 探索ルール

`KajiConfig.discover()`（`kaji_harness/config.py`）は次の順で repository root を解決する。

1. `--workdir`（未指定なら cwd）を起点にする。
2. 起点から親方向へ歩き、`.kaji/config.toml` が存在する最初の directory を repository root とする。
3. filesystem root まで到達しても見つからなければ `ConfigNotFoundError` で停止する。

`kaji run` の config 探索起点は `--workdir` で指定できる（workflow YAML の `workdir` とは別物。
[ワークフロー作成](../dev/workflow-authoring.md) § 実行コマンド 参照）。

## overlay merge 規則

overlay（`.kaji/config.local.toml`）は **top-level section 内の key 単位**で tracked を上書きする。

- `[execution]`: overlay の同名 key が tracked の同名 key を上書きする。overlay に書かれていない
  key は tracked の値を維持する。
- `[provider]`: `type` / `[provider.github]` / `[provider.local]` を **deep-1 merge** できる。
  overlay が `type = "local"` を書けば tracked が `type = "github"` でも provider を切り替えられ、
  `[provider.github]` / `[provider.local]` のサブテーブルは key 単位でマージされる。
- tracked と overlay の双方に `[provider]` が無い場合のみ、loader は provider を `None` として返す。

検証エラーのメッセージが「その key を実際に定義したファイル」（tracked / overlay）を指すのは、
`[execution]` の各 key と `provider.local.machine_id` に限られる（`config.py:213-249` の `source()`、
`config.py:369-374` の `source_path`）。`[provider]` のそれ以外の検証エラー（`type` /
`[provider.github]` の型エラーなど）は `ConfigLoadError(path, ...)` を使うため、overlay 由来の値で
あっても tracked `.kaji/config.toml` を指す（`config.py:323-354`）。

## section / key 仕様

各 key の「既定」は config loader 層が parse 時に採用する値である。loader の既定値と、provider 層が
path 算出時に適用する**実効 fallback** が異なる key（`worktree_prefix`）は、両者を分けて記載する。

### `[paths]`

| key | 必須/任意 | 型 | 既定 | 検証規則 | 一次情報 |
|-----|----------|----|------|----------|---------|
| `artifacts_dir` | 必須 | str | —（未設定はエラー） | 相対パスは `..` 不可（repo root 脱出防止）。絶対パス / `~` 展開は許可 | `config.py:118-125`, `397-417` |
| `skill_dir` | 必須 | str | —（未設定はエラー） | 相対パスのみ。絶対パス不可・`..` 不可 | `config.py:126-133`, `419-434` |
| `worktree_prefix` | 任意 | str | `""`（未設定） | 非空時は単一の安全な segment（`[A-Za-z0-9._-]+`、separator / 空白 / `..` / 絶対 不可） | `config.py:134-140`, `436-452` |

> **`worktree_prefix` の「設定 default」と「実効 fallback」**:
>
> - **設定 default（config loader 層）**: 未設定時の `config.paths.worktree_prefix` は **空文字 `""`**
>   （`config.py:134` の `paths_data.get("worktree_prefix", "")`）。
> - **実効 fallback（provider 層）**: worktree dir 名を算出する箇所では `worktree_prefix or "kaji"`
>   が使われる（`kaji_harness/providers/context.py:94` /
>   `kaji_harness/worktree_discovery.py:92`）。つまり空文字は path 算出時に `"kaji"` へ倒れる。
> - 結果として worktree dir 名は `kaji-<branch_prefix>-<id>` 形式になる。`.kaji/config.toml` で
>   `worktree_prefix = "kaji"` を明示しても、未設定のままでも、**生成される worktree path は同一**。
>   field 値（`config.paths.worktree_prefix`）だけが `"" → "kaji"` に変わる。

`artifacts_dir` の相対パスは main worktree（`provider.<type>.default_branch` を checkout している
worktree）基準で解決される（Issue #177、[ワークフロー作成](../dev/workflow-authoring.md) § 前提条件 参照）。

### `[execution]`

| key | 必須/任意 | 型 | 既定 | 検証規則 | 一次情報 |
|-----|----------|----|------|----------|---------|
| `default_timeout` | 必須 | int | —（未設定はエラー） | `> 0` の整数（bool 不可） | `config.py:219-231` |
| `agent_runner` | 任意 | `"headless"` \| `"interactive_terminal"` | `"headless"` | 列挙外は `ConfigLoadError` | `config.py:233-244` |
| `interactive_terminal_close_on_verdict` | 任意 | bool | `true` | bool 以外は `ConfigLoadError` | `config.py:246-252` |

- `agent_runner` は agent step を headless CLI で起動するか tmux pane 上の対話 CLI で起動するかを選ぶ。
  `interactive_terminal` の挙動・CLI option・優先順位は
  [Interactive Terminal Runner ガイド](../cli-guides/interactive-terminal-runner.md) を参照。
- `interactive_terminal_close_on_verdict` は `agent_runner = "interactive_terminal"` のときのみ作用する
  （verdict 検知後に pane を閉じるか）。headless 運用では無効。

`timeout` の解決順位は step.timeout → workflow.default_timeout → `config.execution.default_timeout`
（[ワークフロー作成](../dev/workflow-authoring.md) § ステップフィールド 参照）。

### `[provider]`

`[provider]` section は config loader 層では任意で、tracked / overlay の双方に無い場合 loader は
provider を `None` として返す（`config.py:303-304`）。ただし `kaji issue` / `kaji pr` / `kaji run` の
provider 解決経路では `[provider]` は必須化されている（Phase 3-e、未設定は exit 2）。詳細な migration は
[Local Mode CLI Guide](../cli-guides/local-mode.md) § 10 を参照。

| key | 必須/任意 | 型 | 既定 | 検証規則 | 一次情報 |
|-----|----------|----|------|----------|---------|
| `type` | `[provider]` を書くなら必須 | `"github"` \| `"local"` | —（`[provider]` 記載時は必須） | 列挙外は `ConfigLoadError` | `config.py:320-329` |

### `[provider.github]`

| key | 必須/任意 | 型 | 既定 | 検証規則 | 一次情報 |
|-----|----------|----|------|----------|---------|
| `repo` | 任意 | str（`owner/name`） | `""` | str 以外は `ConfigLoadError`。`https://` プレフィクスや `.git` サフィックスは付けない | `config.py:334-336` |
| `default_branch` | 任意 | str | `"main"` | str 以外は `ConfigLoadError` | `config.py:337-339` |
| `git_remote` | 任意 | str | `"origin"` | str 以外は `ConfigLoadError` | `config.py:340-342` |

- `repo` は `gh --repo <owner>/<name>` / `gh api repos/<owner>/<name>/...` に渡される。設定すると
  worktree の git remote が fork を指していても書き先がズレない。
- `git_remote` は skill 内の `git push` / `git fetch` が対象とする git remote 名。release 運用では
  `provider.github.git_remote` が GitHub を指す remote 名の整合確認に使われる
  （[Release Runbook](../operations/release/runbook.md) 参照）。

### `[provider.local]`

| key | 必須/任意 | 型 | 既定 | 検証規則 | 一次情報 |
|-----|----------|----|------|----------|---------|
| `machine_id` | 任意 | str | `""` | 非空時は `[a-z0-9]{1,16}`（lowercase 英数字のみ・ハイフン不可・最大 16 文字）。違反は `ConfigLoadError` | `config.py:352-377` |
| `default_branch` | 任意 | str | `"main"` | str 以外は `ConfigLoadError` | `config.py:378-380` |
| `git_remote` | 任意 | str | `"origin"` | str 以外は `ConfigLoadError` | `config.py:381-383` |

## local provider / overlay の扱い

local provider はこのリポジトリの標準運用ではない。値の仕様は上記 `[provider.local]` を、運用 how-to は
[Local Mode CLI Guide](../cli-guides/local-mode.md) を参照する。

`kaji local init` は overlay（`.kaji/config.local.toml`）に次の 3 値を書き込む
（`kaji_harness/local_init.py:243-258`）:

- `[provider] type = "local"`
- `[provider.local] machine_id = "<解決値>"`
- `[provider.local] default_branch = "<--default-branch | main>"`

`machine_id` の解決順（`local_init.py:215-240`）:

1. `--machine-id <name>` 明示（`[a-z0-9]{1,16}` 違反は exit 2）。
2. `socket.gethostname()` を sanitize（lowercase + 英数字 + 16 文字切り詰め）し、既存 local Issue と
   衝突しなければ採用。
3. `pc1` / `pc2` / … に fallback（既存 `.kaji/issues/local-*` と衝突しない最小値）。

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

[execution]
default_timeout = 2400
agent_runner = "headless"           # "headless"（既定） | "interactive_terminal"
# interactive_terminal_close_on_verdict = true   # interactive_terminal のときのみ作用

[provider]
type = "github"

[provider.github]
repo = "apokamo/kaji"
default_branch = "main"
git_remote = "origin"
```

## 関連ドキュメント

- [GitHub Mode CLI Guide](../cli-guides/github-mode.md) — GitHub provider のセットアップ / 運用
- [Local Mode CLI Guide](../cli-guides/local-mode.md) — local provider / overlay の運用 how-to
- [Interactive Terminal Runner ガイド](../cli-guides/interactive-terminal-runner.md) — `[execution] agent_runner`
- [ワークフロー作成](../dev/workflow-authoring.md) — `.kaji/config.toml` を前提とする workflow 定義
- [Git Worktree ガイド](../guides/git-worktree.md) — overlay が新規 worktree に引き継がれない注意
- [Release Runbook](../operations/release/runbook.md) — `provider.github.git_remote` の参照
