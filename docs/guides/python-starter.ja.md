# Python Starter ガイド（kaji-starter-python）

Language: [English](python-starter.md) | 日本語

[kaji-starter-python](https://github.com/apokamo/kaji-starter-python)
template repository を起点に、kaji の Issue 起点開発 workflow
（設計 → 実装 → レビュー → PR）を初日から回せる Python プロジェクトを作る手順。

starter には以下が同梱されている。

- Python プロジェクト骨格（`src/` layout / `uv` / `ruff` / `mypy` / `pytest` / `Makefile`）
- kaji（dev dependency として導入済み。`uv run kaji` で実行）
- `.kaji/wf/` の workflow YAML 5 本（GitHub provider 用 3 本 + local provider 用 2 本、
  既定は claude 単騎構成）
- `.claude/skills/` の汎用化 skills 23 本（Claude 以外の agent は
  `.agents/skills/` の symlink から同じ正本を参照）

本ガイドが starter 側ではなく kaji リポジトリにあるのは、template から作られた
各リポジトリに「starter の使い方」というメタ文書を残さないため。

## 1. starter から repository を作る

前提: [uv](https://docs.astral.sh/uv/)、[gh](https://cli.github.com/)、
agent CLI（既定構成は [Claude Code](https://claude.com/claude-code)。
codex / gemini は [§ 2.4](#24-agent-cli-と-model) を参照）。

1. GitHub で
   [kaji-starter-python](https://github.com/apokamo/kaji-starter-python)
   を開き、**Use this template** → **Create a new repository** で自分の
   repository を作成して clone する。
2. セットアップ値を書き換える:
   - `.kaji/config.toml`: `[provider.github]` の `repo = "<owner>/<repo>"` を
     自分の repository にする（必須）
   - `AGENTS.md`: `<project-name>` placeholder を埋める
   - `LICENSE`: starter は 0BSD（帰属義務なし）のため、自分のプロジェクトの
     ライセンスに自由に差し替えてよい
   - （任意）package の rename — [§ 4.3](#43-package-の-rename)
3. インストールして品質 gate を実行する:

   ```bash
   uv sync
   source .venv/bin/activate && make check   # 作成直後にパスする
   ```

4. 初期セットアップを commit し、**workflow を回す前に** `main` へ反映する:

   ```bash
   git add -A && git commit -m "chore: initial setup"
   ```

   `uv sync` の後に commit することで、再生成された `uv.lock` も含まれる。
   未 commit のまま workflow を回すと、skill が feature worktree 内で
   自己修復し、最初の feature PR にセットアップ変更が混入する。

5. GitHub 認証と workflow 用ラベルの作成（初回のみ）:

   ```bash
   gh auth status
   scripts/setup_labels.sh
   ```

   GitHub ラベル（`type:*`）は「Use this template」では**複製されず**、
   workflow の Issue 処理はこのラベルに依存する。

6. 最初の workflow を実行する:

   ```bash
   uv run kaji issue create --title "..." --body-file issue.md --label type:feature
   uv run kaji run .kaji/wf/dev.yaml <issue-id>
   ```

## 2. セットアップ詳細

### 2.1 対応環境

Linux / macOS / WSL2。native Windows は対象外（interactive terminal runner が
tmux 前提のため）。Windows 利用者は WSL2 内で作業する。

### 2.2 provider の選択

| provider | 使いどころ | workflow |
|----------|-----------|----------|
| `github`（既定） | 標準導線。Issue / PR / レビューを GitHub で回す | `dev.yaml` / `dev-thorough.yaml` / `docs.yaml` |
| `local` | GitHub なしでの試用、認証前の確認、障害時の fallback | `dev-local.yaml` / `docs-local.yaml` |

starter の tracked `.kaji/config.toml` は GitHub 前提。local provider を試す
場合は、machine-local overlay（gitignored）を作る:

```bash
uv run kaji local init
```

local provider は Issue を `.kaji/issues/`（tracked）に永続化し、PR の概念を
持たない。`dev-local.yaml` は `design` step から始まり、issue-create /
issue-start（worktree 作成を含む）は手動実行済みが前提。手動手順は starter の
[`docs/dev/kaji-workflow.md`](https://github.com/apokamo/kaji-starter-python/blob/main/docs/dev/kaji-workflow.md)
（§ local provider での issue-create / issue-start）に記載。GitHub と同じ体験
（PR レビューサイクル、GitHub Issue による review-ready gate）を local provider
に期待しないこと。

provider と workflow は一致させる — github provider で `dev-local.yaml` を
実行する（またはその逆の）不整合は、kaji が fail-fast で reject する。

### 2.3 GitHub 認証とラベル

- GitHub 系 workflow の実行前に `gh auth status` が通っていること
  （kaji は Issue / PR 操作を `gh` CLI へ委譲する）
- `scripts/setup_labels.sh` が `type:*` ラベル
  （`type:feature` / `type:bug` / `type:refactor` / `type:docs` / `type:test` /
  `type:chore` / `type:perf` / `type:security`）を作成する。冪等
  （`gh label create --force`）で、GitHub provider の場合のみ必要

### 2.4 agent CLI と model

workflow YAML 5 本の既定は **claude 単騎構成**。別の agent CLI を使う場合は
全 YAML を一括変換する:

```bash
uv run python scripts/set_agent.py codex    # または gemini / claude
```

CLI → model の対応は step の tier で決まる: 軽量 step（`start` / `pr` /
`close`）には軽い model、それ以外は heavy 扱い。対応表の正本は
`scripts/set_agent.py` 内にあり、以下はそこからの転記。

| CLI | model（heavy step） | model（light step） | 有効な `effort` 値 | 無効な `effort` の fallback |
|-----|---------------------|---------------------|--------------------|------------------------------|
| `claude` | `opus` | `sonnet` | low / medium / high / xhigh / max | — |
| `codex` | `gpt-5.5` | `gpt-5.5` | none / minimal / low / medium / high / xhigh | max → xhigh |
| `gemini` | `gemini-3-pro` | `gemini-3-flash` | low / medium / high / xhigh | max → xhigh、none · minimal → low |

`effort` は workflow ごとのチューニング値（例: `dev-thorough.yaml` は高め）で、
対象 CLI で無効な値でない限り変換では保持される。script は冪等かつ atomic:
同じ CLI で再実行しても差分ゼロ、エラー時はどのファイルも書き換えない。

選択した CLI は `kaji run` を実行する machine にインストール・認証済みで
あること。

### 2.5 interactive terminal runner（tmux）

既定の runner は `headless` で tmux 不要。`.kaji/config.toml` を
`execution.agent_runner = "interactive_terminal"`（agent セッションを可視
pane に表示）へ切り替える場合は、**tmux 3.1 以上**の **tmux セッション内**で
kaji を実行する必要がある。詳細は
[Interactive Terminal Runner ガイド](../cli-guides/interactive-terminal-runner.md)。

## 3. 開発の進め方

### 3.1 workflow の 1 周

```bash
# 1. Issue を起票する（type:* ラベルで振り分け。本文品質は review-ready が gate する）
uv run kaji issue create --title "feat: ..." --body-file issue.md --label type:feature

# 2. workflow を実行する
uv run kaji run .kaji/wf/dev.yaml <issue-id>
```

`dev.yaml` は Issue を次のとおり進める: Issue 本文のレディネスレビュー
（`review-ready`）→ worktree 作成（`start`）→ 設計 + 設計レビューサイクル →
TDD 実装 + コードレビューサイクル → 最終チェック → PR 作成 → `review` skill
による PR レビュー（`pr-fix` / `pr-verify` で収束）→ merge と worktree 掃除
（`close`）。各 step は作業報告と verdict を Issue に投稿し、実行記録は
`.kaji/artifacts/<issue>/runs/<timestamp>/`（gitignored）に出力される。

再開・単一 step 実行:

```bash
uv run kaji run .kaji/wf/dev.yaml <issue-id> --from <step>   # 指定 step から再開
uv run kaji run .kaji/wf/dev.yaml <issue-id> --step <step>   # 1 step だけ実行
```

### 3.2 workflow 5 本の使い分け

| workflow | provider | 用途 |
|----------|----------|------|
| `dev.yaml` | github | 開発作業の標準。Issue → 設計 → 実装 → レビュー → PR → close |
| `dev-thorough.yaml` | github | `dev.yaml` と同じ遷移グラフで設計・実装の effort を上げた丁寧版 |
| `docs.yaml` | github | docs-only 変更。doc-update → doc-review → PR → close |
| `dev-local.yaml` | local | GitHub 連携なしの開発 workflow（PR concept なし） |
| `docs-local.yaml` | local | GitHub 連携なしの docs workflow（PR concept なし） |

## 4. カスタマイズ

### 4.1 agent CLI の寄せ替え

[§ 2.4](#24-agent-cli-と-model) のとおり `scripts/set_agent.py` を使う。
変換後は validate する:

```bash
uv run kaji validate .kaji/wf/*.yaml
```

### 4.2 混成構成

複数の agent CLI を契約している場合は、YAML を直接編集して step ごとに CLI を
割り当てられる。主な動機はレビュー視点の多様性 — 実装した model とは別の
model にレビューさせること。kaji リポジトリ自体がこの構成で動いている
（[dev.yaml](../../.kaji/wf/dev.yaml) では claude が実装し codex がレビュー）。
レビュー系 step（`review-code`、`verify-code`、`review`、`pr-verify` など）の
`agent:` / `model:` / `effort:` を書き換えたら再 validate する。workflow YAML
の記法は [workflow-authoring.md](../dev/workflow-authoring.md) を参照。

`set_agent.py` は**全** step を単一 CLI へ変換する点に注意 — 混成構成にした後で
実行すると step ごとの割り当てが上書きされる。

### 4.3 package の rename

既定の package は `src/starter_app/` で、rename しなくても `make check` は
通る。rename する場合:

1. `pyproject.toml` の `name` を変更
2. `git mv src/starter_app src/<your_package>`
3. `tests/test_smoke.py` の import を更新
4. `uv sync`（`uv.lock` が再生成される）→ `make check`

初期セットアップ commit（[§ 1](#1-starter-から-repository-を作る) 手順 4）の
一部として行う。

### 4.4 docs・開発規約の育て方

starter は意図的に最小 docs 構成で始まる。育てるときは:

- `AGENTS.md` は薄い入口（索引 + 絶対ルール）のまま保ち、詳細は `docs/` へ
  分割して `docs/README.md` 索引に登録する
- `docs/reference/python-standards.md` と `configuration.md` は skill が
  コードを書く前にロードする規約の正本 — 拡張は skill ファイルではなく
  こちらで行う

### 4.5 skills の調整

`.claude/skills/` が正本で、`.agents/skills/` には Claude 以外の agent 向けの
per-skill 相対 symlink がある。skill を追加・調整するときは両者を同期させる
（skill ごとに symlink 1 本、`_shared` 含む）。skills は品質 gate として
starter の `make check` 系ターゲットを呼ぶため、Makefile のターゲット名を
変えた場合は参照する skill も更新する。

skills は同梱コピーであり kaji 本体とは同期されない: kaji を更新しても skills
は変わらず、自分の編集はそのまま資産として残る。

### 4.6 品質 gate の拡張

`make check` は `ruff check` / `ruff format --check` / `mypy` / `pytest` を
実行する。拡張は `Makefile` で行い（coverage やセキュリティスキャナの追加
など）、変更種別ごとの required gate は
`docs/dev/change-types-and-gates.md` に記録する。`make verify-docs`
（doc link checker、`AGENTS.md` も対象）は最初から任意 gate として使える。

## 5. オプション: Codex auto-review（`review-poll`）

既定では、GitHub 系 workflow は同梱の `review` skill で PR をレビューする —
外部 bot には依存しない。repository に **Codex GitHub integration**
（`chatgpt-codex-connector[bot]`）を導入している場合は、kaji の `review-poll`
フロー（PR への Codex auto-review を待ち、その結果を workflow verdict に変換
する）へ切り替えられる。

1. repository に Codex GitHub connector を導入し（ChatGPT の Codex 設定 →
   GitHub integration）、PR を開くと `chatgpt-codex-connector[bot]` から
   自動レビューが付くことを確認する
2. `dev.yaml`（必要なら `dev-thorough.yaml` / `docs.yaml` も）で `pr` step の
   `PASS` 遷移先を `review` から `review-poll` に変え、次の step を追加する:

   ```yaml
     - id: review-poll
       exec: [uv, run, kaji, pr, review-poll]
       on:
         PASS: close
         RETRY: pr-fix
         BACK_FALLBACK: review
         ABORT: end
   ```

   既存の `review` step は残す: bot が応答しないときは `BACK_FALLBACK` で
   そちらへ fallback し、bot なしでも workflow が収束する。kaji リポジトリ
   自体の [dev.yaml](../../.kaji/wf/dev.yaml) が同じ構成の参照実装。
3. validate する: `uv run kaji validate .kaji/wf/dev.yaml`

## 6. 同梱ドキュメント

| ファイル | 役割 |
|---------|------|
| `AGENTS.md` | agent instructions の正本: 入口・索引・絶対ルールのみ |
| `CLAUDE.md` | `@AGENTS.md` インポート + Claude Code 固有の記述（skills 一覧、memory 設定） |
| `README.md` / `README.ja.md` | 人間向けの概要 + quickstart（英語正本 + 日本語版） |
| `docs/README.md` | ドキュメント索引 |
| `docs/dev/change-types-and-gates.md` | 変更種別ごとの required 品質 gate |
| `docs/dev/testing-convention.md` | Small / Medium / Large テスト規約と恒久テスト追加の判断基準 |
| `docs/dev/git-workflow.md` | branch / commit / merge 運用（`--no-ff`、main 直コミット禁止） |
| `docs/dev/kaji-workflow.md` | workflow 5 本・skill lifecycle・local provider の手動手順 |
| `docs/dev/shared_skill_rules.md` | skill 間の責務境界と verdict 規約 |
| `docs/dev/documentation_update_criteria.md` | 変更に docs 更新が必要かの判断フレームワーク |
| `docs/reference/configuration.md`（+ `.en.md`） | `.kaji/config.toml` と `.env` の責務。実行ログの出力先の定義を含む（日本語正本 + 英語） |
| `docs/reference/python-standards.md`（+ `.en.md`） | skill がコードを書く前にロードする Python コーディング規約（日本語正本 + 英語） |
| `LICENSE` | 0BSD — 帰属義務なし、自由に差し替え可。kaji 本体（Apache-2.0）とは意図的に別: starter の中身は*あなたの* repository になるため |
| `scripts/` | `set_agent.py`（agent 変換）/ `setup_labels.sh`（ラベル）/ `check_doc_links.py`（doc link checker） |
| `.claude/skills/` + `.agents/skills/` | 汎用化 skills 23 本（正本 + per-skill symlink） |

ドキュメント言語方針（暫定。kaji 本体の選択的 bilingual 運用に準拠）:
公開面は英語メイン + 日本語版（`README.ja.md`、本ガイドの `.ja`）、内部
reference は日本語正本 + `.en.md` 訳、内部 process docs（`docs/dev/` /
`AGENTS.md` / `CLAUDE.md` / skills）は当面日本語のみ。

## 7. トラブルシュート

| 症状 | 原因 | 対処 |
|------|------|------|
| Issue / PR 操作が認証エラーで失敗する | `gh` が未認証 | `gh auth login` を実行し、`gh auth status` で確認 |
| Issue 起票がラベルエラーで失敗する | `type:*` ラベルが存在しない — GitHub ラベルは「Use this template」で複製されない | `scripts/setup_labels.sh` を一度実行 |
| workflow が repository を解決できない | `.kaji/config.toml` の `repo = "<owner>/<repo>"` が placeholder のまま | 自分の repository を設定し、commit して `main` へ反映 |
| セットアップ変更（config / rename / `uv.lock`）が最初の feature PR に混入する | 初期セットアップを未 commit のまま workflow を回し、skill が worktree 内で自己修復した | rename → `uv sync` → `make check` → commit を初回実行**前**に済ませる（[§ 1](#1-starter-から-repository-を作る) 手順 4） |
| kaji が provider エラーで即終了する | provider と workflow の不一致（github provider で `dev-local.yaml`、または local で `dev.yaml`） | 一致させる。この不整合は設計どおり fail-fast で reject される |
| step が agent 起動時に落ちる、model が拒否される | YAML が指定する agent CLI が未インストール、または model / effort が手元環境と不一致 | CLI をインストールするか `uv run python scripts/set_agent.py <cli>` で寄せ替える |
| `review-poll` が polling し続けて進まない | review-poll 構成へ書き換えたが `chatgpt-codex-connector[bot]` が repo に未導入 | Codex GitHub connector を導入するか、既定の `review` step 構成へ戻す（§ 5 参照） |
| interactive terminal runner の pane が起動しない | tmux セッション外で実行している、または tmux が 3.1 未満 | tmux セッションを先に開始する、tmux を更新する、または既定の `headless` runner を使う |
| `dev-local.yaml` が `design` 付近で worktree 不在で止まる | local 系 workflow は `design` から始まり、issue-create / issue-start の手動実行が前提 | starter の `docs/dev/kaji-workflow.md` の手動手順を実施してから再実行 |
| `issue-close` が未追跡ファイルを警告して ABORT する | repo 直下に stray なログファイル（例: `kaji run ... > run.log` のリダイレクト）があり、close の安全ガードが検知した | リダイレクトは不要 — 完全ログは `.kaji/artifacts/<issue>/runs/<ts>/` に残っている。stdout を残すなら repo 外（または gitignored な `tmp/`）へ |
| local provider が「物足りない」 | local provider に GitHub PR / review-poll / review-ready gate を期待している | 仕様どおり: local provider に PR の概念はない。フル体験には GitHub を使う |

## 関連ドキュメント

- starter repository: <https://github.com/apokamo/kaji-starter-python>
- kaji 設定リファレンス: [configuration.md](../reference/configuration.md)
  （[English](../reference/configuration.en.md)）
- workflow YAML の書き方: [workflow-authoring.md](../dev/workflow-authoring.md)
- Local Mode CLI ガイド: [local-mode.md](../cli-guides/local-mode.md)
- Interactive Terminal Runner: [interactive-terminal-runner.md](../cli-guides/interactive-terminal-runner.md)
