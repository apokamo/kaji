# [設計] 設定リファレンスを日英で整備し config docs の重複を整理する

Issue: #253

## 概要

`.kaji/config.toml` / `.kaji/config.local.toml` の全 section/key 仕様を集約した
専用リファレンス `docs/reference/configuration.md`（日本語）と
`docs/reference/configuration.en.md`（英語）を新規追加し、それを config 仕様の正本
（Source of Truth）にする。あわせて `.kaji/config.toml` の挙動に効く主要値を暗黙
default に依存せず明示し、各既存 docs に分散している config key の詳細説明を
「最小例＋リファレンスへのリンク」に整理する。

## 背景・目的

### ユースケース

- **利用者**: kaji を別 repo に導入する人が `.kaji/config.toml`（このリポジトリの
  標準設定）を参照設定例として読む。挙動に効く値が暗黙 default に隠れていると、
  どの key が何に効くのか config ファイル単体からは読み取れない。
- **メンテナ**: ある config key の仕様（型 / 既定値 / 検証規則 / 挙動）を更新するとき、
  記載箇所が 6 つの docs に分散していると、片方だけ古くなる二重記載リスクがある。
  正本が 1 箇所に集約されていれば更新点が 1 つに収束する。

### 現状の問題（一次情報で確認）

- `.kaji/config.toml`（現行、`.kaji/config.toml:1-19`）は
  `[paths] artifacts_dir / skill_dir`、`[execution] default_timeout`、
  `[provider] type`、`[provider.github] repo / default_branch` のみを記載し、
  以下の挙動に効く値は暗黙 default 依存になっている:
  - `[paths] worktree_prefix`（未設定 → `"kaji"` fallback。`config.py:24`, `447`）
  - `[execution] agent_runner`（未設定 → `"headless"`。`config.py:32`, `233`）
  - `[execution] interactive_terminal_close_on_verdict`（未設定 → `True`。`config.py:33`, `246`）
  - `[provider.github] git_remote`（未設定 → `"origin"`。`config.py:51`, `340`）
- config 説明が分散している（`grep` で確認した箇所）:
  - `docs/dev/workflow-authoring.md`: 最小構成、`artifacts_dir` 解決、`agent_runner`
  - `docs/cli-guides/github-mode.md`: GitHub config 例、`worktree_prefix`、`git_remote`、`repo` 形式
  - `docs/cli-guides/local-mode.md`: local provider、overlay、`machine_id`、`git_remote`
  - `docs/cli-guides/interactive-terminal-runner.md`: `[execution]` 詳細、overlay merge、解決順位
  - `docs/guides/git-worktree.md`: overlay が新規 worktree に引き継がれない注意
  - `docs/operations/release/runbook.md`: `provider.github.git_remote` 参照

### 到達したい状態

- 各 config key の詳細仕様は `docs/reference/configuration.md` / `.en.md` に集約。
- `.kaji/config.toml` は GitHub 標準運用の実設定として主要値を明示し、各設定に短い
  コメントとリファレンスへの導線を持つ。
- 既存 how-to / CLI guide は文脈に必要な最小例とリファレンスへのリンクに絞る。
- local provider / overlay / `machine_id` の詳細は、GitHub 標準運用と混同しない形で
  configuration reference と local-mode guide に責務分担して配置する。

## インターフェース

本変更の「契約」は (A) 新リファレンスの構成、(B) `.kaji/config.toml` の目標 key 集合、
(C) 既存 docs の整理方針、(D) README からの導線、の 4 つ。

### A. `docs/reference/configuration.md` の構成（正本）

以下の節構成を契約とする。各 key の仕様値はすべて `kaji_harness/config.py` /
`kaji_harness/local_init.py` を一次情報として転記する（推測値を書かない）。

1. **概要**: config ファイルが repo root マーカーであること、本書が config 仕様の正本であること。
2. **ファイルの役割**:
   - `.kaji/config.toml`（tracked）= repository default。
   - `.kaji/config.local.toml`（gitignored）= 個人環境の overlay。
   - `git worktree add` は tracked のみ checkout するため overlay は新規 worktree に
     引き継がれない（`docs/guides/git-worktree.md:183-193` と整合）。
3. **探索ルール**: `--workdir` または cwd から親方向へ `.kaji/config.toml` を探索し、
   `.kaji/` を含む directory を repo root とする（`config.py:94-105`）。
4. **overlay merge 規則**: overlay は **top-level section 内の key 単位**で tracked を
   上書きする。`[provider]` は `type` / `[provider.github]` / `[provider.local]` を
   deep-1 merge できる（`config.py:182-318`）。
5. **section/key 仕様表**: 各 key について `必須/任意`・`型`・`既定値`・`検証規則`・`挙動`
   を列挙する。最低限カバーする key:

   | section | key | 必須/任意 | 型 | 既定 | 一次情報 |
   |---------|-----|----------|----|------|---------|
   | `[paths]` | `artifacts_dir` | 必須 | str | — | `config.py:118-125`, `397-417` |
   | `[paths]` | `skill_dir` | 必須 | str | — | `config.py:126-133`, `419-434` |
   | `[paths]` | `worktree_prefix` | 任意 | str | `""`（未設定） | `config.py:134-140`, `436-452` |
   | `[execution]` | `default_timeout` | 必須 | int>0 | — | `config.py:219-231` |
   | `[execution]` | `agent_runner` | 任意 | `"headless"`/`"interactive_terminal"` | `"headless"` | `config.py:233-244` |
   | `[execution]` | `interactive_terminal_close_on_verdict` | 任意 | bool | `True` | `config.py:246-252` |
   | `[provider]` | `type` | 任意※ | `"github"`/`"local"` | — | `config.py:320-329` |
   | `[provider.github]` | `repo` | 任意 | str(`owner/name`) | `""` | `config.py:334-336` |
   | `[provider.github]` | `default_branch` | 任意 | str | `"main"` | `config.py:337-339` |
   | `[provider.github]` | `git_remote` | 任意 | str | `"origin"` | `config.py:340-342` |
   | `[provider.local]` | `machine_id` | 任意 | str | `""` | `config.py:352-377` |
   | `[provider.local]` | `default_branch` | 任意 | str | `"main"` | `config.py:378-380` |
   | `[provider.local]` | `git_remote` | 任意 | str | `"origin"` | `config.py:381-383` |

   ※ `[provider]` 自体は Phase 3-c では optional（tracked/overlay 双方欠如時 `None`、
   `config.py:303-304`）。`type` は `[provider]` を書く場合は必須（`config.py:320-323`）。

   > **`worktree_prefix` は「設定 default」と「実効 fallback」を分けて書く**（review-design
   > Should Fix 反映）: reference 本体の仕様表では config loader 層の **設定 default = `""`
   > （未設定）**（`config.py:134`）を「既定」欄に書き、別の「挙動」欄（または注記）で
   > provider 層の **実効 fallback = `"kaji"`**（`worktree_prefix or "kaji"`、
   > `context.py:94` / `worktree_discovery.py:92`）を説明する。default と fallback を 1 セルに
   > 混在させず、config loader（値を `""` のまま保持）と provider layer（path 算出時に
   > `"kaji"` へ倒す）の責務分離が読み手に伝わる記述にする。
6. **local provider / overlay の扱い**: `kaji local init` が overlay に書く 3 値
   （`type="local"` / `machine_id` / `default_branch`、`local_init.py:243-258`）と、
   `machine_id` 解決順（CLI → hostname sanitize → `pcN` fallback、`local_init.py:215-240`）の
   要点を記載。詳細手順そのものは `docs/cli-guides/local-mode.md` に委譲し、reference は
   「値の仕様」に責務を限定する。
7. **英語版への相互リンク**。

`configuration.en.md` は上記 1〜7 と同一構成・同一情報量の英語版とし、日英で相互リンクする。

### B. `.kaji/config.toml` の目標 key 集合

GitHub 標準運用（`type="github"` / headless）で**挙動に効く主要値**を暗黙 default に
依存せず明示する。各 key に短いコメント＋ reference への導線を置く。目標差分:

- `[paths] worktree_prefix = "kaji"` を明示追加（現行の実効 fallback 値と一致。既定は
  空文字 `""` のため field 値は変化するが、実効 worktree path は不変。下記不変条件参照）。
- `[execution] agent_runner = "headless"` を明示追加（現行 default と一致）。
- `[provider.github] git_remote = "origin"` を明示追加（現行 default と一致）。
- 既存の `artifacts_dir` / `skill_dir` / `default_timeout` / `type` / `repo` /
  `default_branch` は維持。
- `interactive_terminal_close_on_verdict` は headless 標準運用では無効
  （`agent_runner="interactive_terminal"` 時のみ作用）のため、明示値ではなく
  コメント例＋ reference 導線に留める（明示すると「効かない値」を標準設定に置くことになる）。
- 先頭にコメントで configuration reference への導線を置く。

> **不変条件（key により根拠が異なる）**:
>
> - `agent_runner` / `git_remote`: parse 時の既定値そのものが明示値と同値
>   （`config.py:233` `merged.get("agent_runner", "headless")` / `config.py:340`
>   `github_raw.get("git_remote", "origin")`）。明示しても `KajiConfig` の field 値・
>   load 後 dataclass は変更前後で**不変**。
> - `worktree_prefix`: parse 時の既定は**空文字 `""`**（`config.py:134`
>   `paths_data.get("worktree_prefix", "")`、`tests/test_worktree_prefix.py:122-127` が
>   未記載時 `config.paths.worktree_prefix == ""` を保証）。`"kaji"` を明示すると
>   `config.paths.worktree_prefix` の **field 値は `"" → "kaji"` に変化する**（dataclass は
>   不変ではない）。ただし実効的な worktree dir 名は `worktree_prefix or "kaji"` の
>   fallback（`kaji_harness/providers/context.py:94` /
>   `kaji_harness/worktree_discovery.py:92`）を経るため、未記載時（`"" → "kaji"`）と明示時
>   （`"kaji"`）で生成 path は同一の `kaji-<branch_prefix>-<id>`。すなわち**実効挙動（生成
>   される worktree path）は不変**。
>
> まとめると、本変更が保証するのは「parse 後 dataclass の不変」ではなく
> **「実効挙動の不変」**（`worktree_prefix` は field 値が変わるが fallback で実効 path は同一、
> 他 2 key は field 値も不変）。Issue 目的である「暗黙 default 依存の解消」に沿って
> default 値そのものを config に明示するため、`worktree_prefix` の field 値変化は意図した
> 変更である。

### C. 既存 docs の整理方針

各 docs は「その文脈で必要な最小例」と「configuration reference へのリンク」に絞り、
key の詳細仕様（型 / 既定 / 検証）の正本記述は reference に集約する。

| docs | 残すもの | reference へ移す/リンク化するもの |
|------|----------|----------------------------------|
| `docs/dev/workflow-authoring.md` | 最小構成例、`artifacts_dir` 解決の注記 | key 詳細はリンク |
| `docs/cli-guides/github-mode.md` | GitHub config 最小例 | `worktree_prefix`/`git_remote`/`repo` 形式の詳細仕様 |
| `docs/cli-guides/local-mode.md` | local init 手順、overlay 運用の how-to | key の型/既定の仕様部 |
| `docs/cli-guides/interactive-terminal-runner.md` | runner の挙動・tmux 手順 | `[execution]` key 仕様表・解決順位の正本 |
| `docs/guides/git-worktree.md` | overlay 非引き継ぎの注意（how-to 文脈） | 必要に応じ reference へリンク |
| `docs/operations/release/runbook.md` | `git_remote` 参照箇所 | 値仕様は reference リンク |

> **判断基準**: how-to 文脈で読者がその場で必要とする最小例は残す。「全 key の網羅的な
> 型/既定/検証」は reference へ一本化する。過剰削除で how-to が読めなくなるのを避ける。

### D. README からの導線

`docs/README.md` の `## Reference` セクション（`docs/README.md:28-34`）に
configuration reference への行を追加し、到達可能にする。

## 制約・前提条件

- **実効挙動非変更**: 追加する明示値は現行の実効挙動を変えない（§ B 不変条件）。
  `agent_runner` / `git_remote` は既定値が明示値と同値で field 値も不変。`worktree_prefix`
  は field 値が `"" → "kaji"` に変わるが、`worktree_prefix or "kaji"` fallback により
  生成される worktree path は不変。config loader / provider の挙動は変えない（スコープ外）。
- **リンク検証**: `make verify-docs` =
  `python3 scripts/check_doc_links.py docs/ README.md CLAUDE.md .claude/skills/`
  （`Makefile:32-33`）が相対リンクと heading anchor を検証する。日英相互リンク・
  README からのリンクはすべて実在 path/heading に解決させる必要がある。
- **`.en.md` 前例なし**: `docs/` 配下に既存 `.en.md` は存在しない（本 Issue が初）。
  link checker は拡張子非依存に `.md` を走査するため新規 `.en.md` も検証対象になる。
- **責務分担**: local provider はこのリポジトリの標準運用ではないため
  `.kaji/config.toml` 本体では前面に出さない。詳細は reference（値仕様）と
  local-mode guide（運用 how-to）に分ける。
- **スコープ外**: `kaji_harness/` 実装変更、config loader 仕様変更、provider 挙動変更、
  local provider を標準運用化、恒久テスト追加。

## 方針

1. `config.py` / `local_init.py` を一次情報として `configuration.md` を作成（§ A の構成）。
2. `configuration.md` と同一構成で `configuration.en.md` を作成し、日英相互リンク。
3. `.kaji/config.toml` を編集（§ B）。`agent_runner` / `git_remote` は追加値が既定値と
   同値であることを、`worktree_prefix` は既定 `""` に対し実効 fallback（`"kaji"`）で生成
   worktree path が不変であることを、それぞれ `config.py` / `context.py` /
   `worktree_discovery.py` と突合して確認（§ B 不変条件）。
4. 既存 6 docs を § C に従い「最小例＋リンク」へ整理。
5. `docs/README.md` Reference に行追加（§ D）。
6. `make verify-docs` を実行し、リンク・heading の整合を確認。config の load 健全性は
   `.kaji/config.toml` を読む kaji コマンド（例 `kaji validate .kaji/wf/dev.yaml` /
   `kaji issue view`）が `ConfigLoadError` を出さないことで確認する。

## テスト戦略

> **CRITICAL**: 本変更は docs-only + config ファイルの内容変更（metadata 的）であり、
> 実行時ロジックの追加・変更を含まない（§ B 不変条件）。

### 変更タイプ

docs-only / metadata-only（`.kaji/config.toml` の内容変更を含むが、追加値は実行時の
**実効挙動**を変えない。`worktree_prefix` のみ field 値が `"" → "kaji"` に変わるが、
fallback により生成 worktree path は不変。§ B 不変条件参照）。

### 変更固有検証

- `make verify-docs`: 新規 `configuration.md` / `.en.md`、日英相互リンク、README リンク、
  各既存 docs のリンク・heading anchor が解決すること（`scripts/check_doc_links.py`）。
- **config load 健全性**: `.kaji/config.toml` を読む kaji コマンドが `ConfigLoadError`
  なく完了すること（TOML 構文・key 検証の通過確認）。
- **実効挙動非変更の根拠確認**（key ごとに根拠が異なる）:
  - `agent_runner="headless"` / `git_remote="origin"`: parse 時の既定値
    （`config.py:233` / `config.py:340`）が明示値と同値 → field 値・dataclass とも不変。
  - `worktree_prefix="kaji"`: 既定は空文字（`config.py:134`、`tests/test_worktree_prefix.py:122-127`）
    で field 値は変化するが、`worktree_prefix or "kaji"` fallback
    （`kaji_harness/providers/context.py:94` / `kaji_harness/worktree_discovery.py:92`）により
    生成 worktree path が未記載時と同一であることを突合する。

### 恒久テストを追加しない理由（`docs/dev/testing-convention.md` の 4 条件）

1. 独自ロジックの追加・変更を含まない（docs 整理 ＋ 既定値/実効挙動を変えない値の明示化のみ）。
2. 想定不具合（リンク切れ）は `make verify-docs`、config 構文/key 不正は既存 config
   loader テスト群＋ load 時 `ConfigLoadError` で捕捉済み。
3. 実効挙動が不変（`agent_runner`/`git_remote` は default 同値、`worktree_prefix` は
   fallback で生成 path 同一）のため、新規テストを足しても回帰検出情報が増えない。なお
   既定 `""` と fallback `"kaji"` の関係自体は `tests/test_worktree_prefix.py` で既にカバー済み。
4. 以上の理由をレビュー可能な形で本節に記載済み。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/reference/ | あり | `configuration.md` / `configuration.en.md` を新規追加（本変更の主成果物） |
| docs/README.md | あり | Reference セクションに configuration reference の行を追加 |
| docs/dev/workflow-authoring.md | あり | config key 詳細をリンク化、最小例に整理 |
| docs/cli-guides/github-mode.md | あり | `worktree_prefix`/`git_remote`/`repo` 仕様を reference へ集約 |
| docs/cli-guides/local-mode.md | あり | key 仕様部を reference へ、運用 how-to は残す |
| docs/cli-guides/interactive-terminal-runner.md | あり | `[execution]` key 仕様表・解決順位を reference へ集約 |
| docs/guides/git-worktree.md | あり（軽微） | overlay 非引き継ぎ注記は残し、必要に応じ reference リンク |
| docs/operations/release/runbook.md | あり（軽微） | `git_remote` 値仕様は reference リンク |
| docs/adr/ | なし | 新しい技術選定はない（既存 config 構造の文書化） |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| CLAUDE.md | 要検討（任意） | Documentation index 表に configuration reference 行を足す候補。ただし Issue 列挙スコープ外。link checker 対象のため追加する場合はリンク健全性を担保する |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| config dataclass 定義・既定値 | `kaji_harness/config.py:18-65` | `PathsConfig.worktree_prefix=""`（→`"kaji"` fallback）、`ExecutionConfig.agent_runner="headless"`、`interactive_terminal_close_on_verdict=True`、`GitHubProviderConfig.git_remote="origin"` 等、明示化する値の default の一次情報 |
| config 探索・load・検証 | `kaji_harness/config.py:94-452` | 探索（94-105）、overlay 読込（164-180）、`[execution]`/`[provider]` の key 単位 merge（182-318）、各 key 検証（397-452）。reference の探索/merge/検証記述の根拠 |
| `worktree_prefix` の parse 既定 | `kaji_harness/config.py:134`, `tests/test_worktree_prefix.py:122-127` | 未記載時 `paths_data.get("worktree_prefix", "")` → field 値は空文字 `""`。test が `config.paths.worktree_prefix == ""` を保証。明示時に field 値が `"" → "kaji"` に変化する根拠（dataclass 不変ではない） |
| `worktree_prefix` の実効 fallback | `kaji_harness/providers/context.py:94`, `kaji_harness/worktree_discovery.py:92` | いずれも `worktree_prefix or "kaji"` で空文字を `"kaji"` に倒す。未記載（`"" → "kaji"`）と明示（`"kaji"`）で生成 worktree path が同一＝実効挙動不変の根拠 |
| local overlay 生成・machine_id 解決 | `kaji_harness/local_init.py:36-63, 215-258` | overlay に書く 3 値（243-258）、`machine_id` 解決順（215-240）、`default_branch` 検証（36-63）。reference の local section 根拠 |
| 現行標準設定 | `.kaji/config.toml:1-19` | 暗黙 default 依存箇所（`worktree_prefix`/`agent_runner`/`git_remote` 未記載）の確認元 |
| テスト規約（変更タイプ判定） | `docs/dev/testing-convention.md:52-76, 112-130` | docs-only/metadata-only の恒久テスト不要 4 条件、変更タイプ別期待値 |
| link checker 仕様 | `scripts/check_doc_links.py:2-58`, `Makefile:32-33` | 相対リンク・heading 解決を検証。日英相互リンク/README リンクの検証根拠 |
| 既存 docs の現状記述 | `docs/dev/workflow-authoring.md` / `docs/cli-guides/github-mode.md` / `docs/cli-guides/local-mode.md` / `docs/cli-guides/interactive-terminal-runner.md` / `docs/guides/git-worktree.md` / `docs/operations/release/runbook.md` | 整理対象の config 記述箇所（§ 背景 grep 結果） |
