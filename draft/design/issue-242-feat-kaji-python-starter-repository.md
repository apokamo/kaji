# [設計] kaji で開発できる Python starter repository（kaji-starter-python）

Issue: #242

## 概要

kaji の Issue 起点 workflow（設計・実装・レビュー・PR）を最初から回せる Python repository を
GitHub template repository `kaji-starter-python` として新規作成し、kaji 本体側に利用ガイド
`docs/guides/python-starter.md` を新設する。

## 背景・目的

### ユーザーストーリー

- **新規ユーザー**として、kaji を試すために、「Use this template」で repository を作り、
  最小の書き換え（repo 名・package 名・`.kaji/config.toml` の `repo`）だけで
  最初の `kaji run` から PR 作成まで到達したい
- **kaji 導入検討者**として、GitHub 連携なしで挙動を確かめるために、local provider で
  `kaji local init` から `dev-local.yaml` を 1 周させたい
- **codex / gemini 契約ユーザー**として、既定の claude 単騎構成を自分の契約 CLI に
  寄せ替えるために、`scripts/set_agent.py` で全 workflow YAML を一括変換したい

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| 作成ガイド文書のみ（旧本文の `create-python-starter-repository.md` 案） | 想定完成形が約 30 ファイルあり、手作りの完走率が低い。Issue 本文の背景節で棄却済み |
| kaji 本体 skills をそのままコピーさせる | skills が `kaji_harness/` 配下パス・kaji 固有コマンド・`kaji-code-reviewer` agent をハードコードしており他リポジトリで動かない（kamo2 では `issue-implement` だけで約 500 行の改変が必要だった。Issue 本文の背景節参照） |
| scaffold コマンド（`kaji init` 等）の実装 | 実装コストが高く、まず template repository で需要と形を検証する。Issue スコープ外として明記済み |

## インターフェース

本 feat の「インターフェース」は利用者との契約 = starter の repository 構成・quickstart 手順・
同梱 script の CLI 仕様である。

### 成果物 1: GitHub template repository `apokamo/kaji-starter-python`

#### ファイル構成（契約）

```text
kaji-starter-python/
├── AGENTS.md            # agent instructions の正本（入口・索引・最小ルールのみ）
├── CLAUDE.md            # @AGENTS.md インポート + Claude Code 固有記述のみ
├── Makefile             # check = lint format typecheck test / verify-docs（任意）
├── README.md            # 概要 + 最小 quickstart + kaji 本体側ガイドへのリンクのみ
├── pyproject.toml       # requires-python >= 3.11 / dev deps に kaji（Git 依存）
├── uv.lock
├── .env.example
├── .gitignore                          # .venv / .env / .kaji/artifacts / *.log 等（発見 4）
├── src/starter_app/__init__.py
├── tests/
│   ├── conftest.py                     # worktree のローカル src/ を優先（発見 3）
│   └── test_smoke.py
├── docs/
│   ├── README.md                        # 索引
│   ├── dev/
│   │   ├── change-types-and-gates.md    # 変更種別ごとの required gate
│   │   ├── testing-convention.md        # S/M/L テスト規約（starter 向け簡約版）
│   │   ├── git-workflow.md              # branch / commit / merge 運用
│   │   └── kaji-workflow.md             # workflow 5 本の使い分け・skill lifecycle
│   └── reference/
│       ├── configuration.md             # .kaji/config.toml と .env の責務
│       └── python-standards.md          # コーディング規約（style / naming / typing 統合）
├── scripts/
│   ├── set_agent.py                     # workflow YAML の agent/model/effort 一括変換
│   ├── setup_labels.sh                  # type:* ラベルを一括作成（発見 1）
│   └── check_doc_links.py               # make verify-docs の doc link checker
├── .agents/skills/                      # .claude/skills/ への per-skill symlink（_shared 含む）
├── .claude/skills/                      # 汎用化版 skills 23 本 + _shared（正本）
└── .kaji/
    ├── config.toml                      # GitHub provider 前提の tracked config
    └── wf/
        ├── dev.yaml / dev-thorough.yaml / docs.yaml        # GitHub provider
        └── dev-local.yaml / docs-local.yaml                # local provider
```

- package 名の既定は `starter_app` とし、rename せずとも `make check` が通る。
  rename 手順はガイドのカスタマイズ節で扱う
- docs 一覧は候補であり実装時に統合・分割してよい（Issue 本文 § 想定完成形の注記どおり）。
  ただし `AGENTS.md` を肥大化させない方針は維持する

#### 入力（利用者の操作、quickstart 契約）

```bash
# 1. GitHub 上で「Use this template」→ 自分の repo を作成して clone
# 2. 初回セットアップ commit（workflow を回す前に main へ反映する。発見 2）
#    - .kaji/config.toml: [provider.github] repo = "<owner>/<repo>"
#    - （任意）pyproject.toml の project name / src/starter_app の rename
git add -A && git commit -m "chore: initial setup"
#    ↑ 未 commit のまま workflow を回すと設定変更が最初の feature PR に混入する
# 3. セットアップと品質ゲート確認
uv sync
source .venv/bin/activate && make check     # 作成直後にパスすること（契約）
# 4. GitHub 認証とラベル作成（発見 1）
gh auth status
scripts/setup_labels.sh                      # workflow が使う type:* ラベルを作成（初回のみ）
# 5. 最初の workflow 実行
uv run kaji issue create ...                # または gh issue create
uv run kaji run .kaji/wf/dev.yaml <issue-id>
```

- kaji は project-local 導入（dev dependency の Git 依存）とし、**`uv run kaji` を正**とする
- **初回セットアップ commit（発見 2）**: `.kaji/config.toml` の repo 書き換えは workflow 実行前に
  commit して main へ反映する。未 commit のまま回すと skill が worktree 内で自己修復して feature
  PR に混入する
- **`type:*` ラベル作成（発見 1）**: GitHub ラベルは template 複製されないため、起票前に
  `scripts/setup_labels.sh`（`gh label create --force` 一括、冪等）で作成する。GitHub provider
  でのみ必要（local provider は不要）
- **worktree テスト分離（発見 3）**: worktree の `.venv` は親 repo の `.venv` への symlink で
  editable install が親 `src/` を指すため、`tests/conftest.py` でローカル `src/` を `sys.path`
  先頭に挿入し、worktree 内テストが自身の変更を検証できるようにする
- local provider 試用は `kaji local init` を正とする（`.kaji/config.local.toml.example` は置かない。
  overlay 作成と gitignore 追記は `kaji local init` が行う: docs/cli-guides/local-mode.md § 2）

#### `scripts/set_agent.py` の CLI 仕様

| 項目 | 仕様 |
|------|------|
| 呼び出し | `uv run python scripts/set_agent.py <cli>`（`cli` ∈ `claude` / `codex` / `gemini`、必須・位置引数 1 個） |
| 入力 | `.kaji/wf/*.yaml` 全 5 本 |
| 動作 | 各 step の `agent:` / `model:` / `effort:` 行を、script 内の対応表（正本）に従い指定 CLI の値へ決定的に書き換える。行単位置換でコメント・構造を保持する |
| 対応表 | 役割 tier（例: 重量級 = design / implement / review 系、軽量級 = start / pr / close 系）ごとに CLI → model / effort を定義。**具体的な model 名は script 内の表を正本**とし、ガイドの表はそこから転記する |
| 出力 | 書き換えた file / step 数のサマリを stdout に出力。冪等（同じ CLI 指定で再実行しても差分ゼロ） |
| エラー | 未知の CLI 名 → usage を stderr に出して exit 2。YAML 内に想定パターン外の `agent:` 行を検出 → 対象を明示して非ゼロ exit（silent skip しない） |
| 依存 | 標準ライブラリのみ（PyYAML round-trip はコメントを失うため使わない） |

#### workflow YAML（GitHub 系 3 本）の kaji 本体との差分

- **`review-poll` step（Codex auto-review 連携）を置かない**。`pr` step の PASS 遷移先を
  `review`（`review` skill による新規 PR レビュー）とし、`pr-review` cycle の entry も `review` にする
  （kaji 本体 `.kaji/wf/dev.yaml` は `pr → review-poll → (BACK_FALLBACK) review` 構成。
  starter は外部 bot 依存を初見導線から外すため fallback ではなく標準にする）
- **全 step の `agent:` を `claude` に統一**（単騎構成）。model / effort は step の重さで
  opus / sonnet 相当を割り当てる
- `requires_provider` は kaji 本体と同じく明示する（github / local）。provider 不整合は
  kaji 本体の fail-fast（exit 2）に委ねる

### 成果物 2: kaji 本体側の変更（本 worktree の PR 範囲）

| ファイル | 変更 |
|---------|------|
| `docs/guides/python-starter.md` | 新設。§ 方針の「利用ガイドの章立て」参照 |
| `README.md` | L182 の「Initial Setup Guide issue #242」参照を、ガイドと starter repository へのリンクに差し替え |
| `docs/README.md` | guides 索引に `python-starter.md` を追加 |

### エラー・詰まりどころ（ガイドのトラブルシュート節の契約）

Issue 本文「よくある詰まりどころ」の 7 項目（`gh auth` 未認証 / `repo` placeholder のまま /
provider と YAML の不一致 / agent CLI 未導入・不一致 / review-poll 化したのに bot 未導入 /
tmux 外・tmux < 3.1 / local provider への過剰期待）をそのまま採録する。

## 制約・前提条件

- **対応環境**: Linux / macOS / WSL2。native Windows は対象外（interactive terminal runner が
  tmux 前提のため）。Windows 利用者には WSL2 を案内する
- **kaji の導入形態**: `pyproject.toml` の dev dependencies に Git 依存で含める。
  uv の Git source 指定（`[tool.uv.sources]` + `tag`）を用い、リリース tag に pin して
  `uv.lock` と整合させる
- **template repository の性質**: 「Use this template」で作られた repo は git 履歴を引き継がず
  単一 initial commit から始まる（GitHub Docs）。starter に置く相対 symlink
  （`.agents/skills/*`）は git 上は通常の symlink object として複製されるため成立する
- **skills の同期は行わない**: kaji 本体 skills の更新は starter に自動反映されない
  （配布・同期機構は Issue スコープ外。同梱コピーで代替）
- **`.gitignore` 方針**（Issue 本文の表を契約とする）: `.venv/` / `.env` / build artifact /
  `.kaji/config.local.toml` / `.kaji/counters/` / `.kaji/artifacts/` は ignore、
  `.kaji/issues/` は tracked（local provider の Issue 永続化に必要）。
  加えて `*.log` を ignore する（stray なログが `issue-close` の安全ガードを誤発火させないため。
  Phase 2 dogfooding 発見 4）
- **実行ログの出力先**（starter の契約として明記）: `kaji run` の run 記録は
  `paths.artifacts_dir`（既定 `.kaji/artifacts/<issue>/`、gitignored）に構造化出力される
  （`runs/<ts>/run.log` / `steps/<step>/attempt-NNN/{console,stdout}.log` / `verdict.yaml`）。
  kaji は stdout にも進捗を流し完全ログは artifacts に残るため通常リダイレクト不要。stdout を
  別途ファイルに残す場合は repository 直下ではなく scratch の `tmp/`（gitignored）か repo 外へ
  出力する（repo 直下の未追跡ファイルは `issue-close` 安全ガードが検知するため）。この定義は
  `docs/reference/configuration.md` § 実行ログの出力先 を正本とする
- **AGENTS.md / CLAUDE.md 構造**: #243 の結論を踏襲（`AGENTS.md` 正本 + `CLAUDE.md` は
  `@AGENTS.md` インポート + Claude Code 固有記述のみ）
- **starter skills の pre-handoff review**: kaji 本体の `kaji-code-reviewer` subagent
  （`.claude/agents/`）は同梱しないため、`issue-implement` の pre-handoff review は
  main-session self-check 経路のみとする（kaji 本体の非 Claude agent 向け fallback と同じ構造）
- **開発時の作業場所**: starter は kaji 本体とは独立した git repository のため、本 Issue の
  実装は kaji worktree 外のローカル作業 copy（`../kaji-starter-python`、main checkout の
  sibling）で行い、`gh repo create` → push → `gh repo edit --template` で公開・template 化する。
  kaji worktree の PR には starter の実体ファイルを含めない
- **レビュー・検証の証跡**: starter 側成果物は kaji worktree の diff に現れないため、
  review-code / final-check が確認できるよう、starter repo の URL・対象 commit hash・
  dogfooding ログ（実行コマンドと結果）を Issue コメントに記録する。ローカル作業 copy の
  絶対パスも記録し、レビュー agent が Read / Grep で直接検査できるようにする

## 変更スコープ

- **kaji 本体（本 worktree）**: `docs/guides/python-starter.md`（新規）/ `README.md` /
  `docs/README.md` / `draft/design/`（本設計書）。`kaji_harness/` / `tests/` / `Makefile` /
  `pyproject.toml` は変更しない
- **外部**: `apokamo/kaji-starter-python`（新規 repository 全体、約 30 ファイル）

## 方針（Minimal How）

実装は以下の順で進める。

1. **starter 骨格**: `uv init` 相当の構成から `pyproject.toml` / `Makefile` / `src/starter_app/` /
   `tests/test_smoke.py` / `.gitignore` / `.env.example` を作り、`make check` を通す
2. **kaji 導入**: dev dependency に kaji（Git 依存、リリース tag pin）を追加し
   `uv run kaji --help` / `uv run kaji validate` が動くことを確認。`.kaji/config.toml` を
   GitHub provider 前提で作成（`repo = "<owner>/<repo>"` placeholder を含める）
3. **workflow YAML 5 本**: kaji 本体の同名 YAML を出発点に、(a) claude 単騎化、
   (b) GitHub 系 3 本から `review-poll` を除去して `review` skill step を標準化、
   (c) skill 参照を汎用化版 skills に揃える。全 5 本を `uv run kaji validate` に通す
4. **skills 汎用化**: kaji 本体の 23 skills + `_shared` をコピーし、以下を機械的に除去・置換する
   - `kaji_harness/` 前提の記述・kaji 本体 docs への参照 → starter の docs 構成へ張り替え
   - 品質 gate 呼び出し → starter の `make check` 系ターゲット
   - `kaji-code-reviewer` subagent 経路 → main-session self-check のみ
   - kaji CLI 呼び出しは `uv run kaji` に統一
   - 汎用化漏れは禁止 token の negative-grep（§ テスト戦略）で検出する
5. **`.agents/skills/` symlink**: kaji 本体と同構造の per-skill 相対 symlink を張る（`_shared` 含む）
6. **docs / AGENTS.md / CLAUDE.md / README**: § インターフェースの構成どおり最小で作成
7. **`scripts/set_agent.py`**: § インターフェースの CLI 仕様どおり実装
8. **公開と template 化**: `gh repo create apokamo/kaji-starter-python --public` → push →
   `gh repo edit apokamo/kaji-starter-python --template`
9. **dogfooding**（§ テスト戦略の変更固有検証）
10. **kaji 本体側**: ガイド新設・README / docs 索引更新 → `make verify-docs`

### 利用ガイドの章立て（`docs/guides/python-starter.md`）

Issue 本文 § 扱う内容 7 の 6 項目をそのまま章にする:
(1) starter からの repository 作成手順 / (2) セットアップ詳細（対応環境・provider 選択・
`gh` 認証・agent CLI と model 対応表・tmux runner 前提） / (3) 開発の進め方（1 周の流れと
workflow 5 本の使い分け） / (4) カスタマイズ（`set_agent.py` 寄せ替え・混成構成・docs の育て方・
skills 調整・品質 gate 拡張） / (5) オプション: Codex auto-review（`review-poll`）導入手順 /
(6) 同梱ドキュメントの役割説明。加えてトラブルシュート節（詰まりどころ 7 項目）を置く。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ

- **kaji 本体（本 PR）**: docs-only（ガイド新設 + README / 索引更新。実行時コード変更なし）
- **starter repository（外部成果物）**: 新規 repository の作成。kaji_harness の実行時コードは
  一切変更しない。starter 同梱物のうち `scripts/set_agent.py` のみ実行可能 script

### docs-only / repository 新規作成の変更固有検証

#### kaji 本体側

- `make verify-docs` — ガイド・README・docs 索引のリンク整合
- ガイド内のコマンド例が starter の実体（Makefile ターゲット・ファイルパス・YAML 名）と
  一致することの突き合わせ確認

#### starter 側（完了条件に対応する検証マトリクス）

| 検証 | 手段 | 対応する完了条件 |
|------|------|------------------|
| 骨格の健全性 | fresh template repo で `uv sync` → `make check` パス | 「作成直後に `make check` が通る」 |
| workflow YAML 妥当性 | 既定（claude 単騎）の 5 本すべて `uv run kaji validate` パス | — |
| `set_agent.py` 変換 | codex / gemini へ変換 → 全 YAML 再 validate パス、同一 CLI 再実行で差分ゼロ（冪等）、未知 CLI で exit 2 | 「変換後の YAML が `kaji validate` を通る」 |
| skills 汎用化漏れ | 禁止 token の negative-grep が 0 hit: `kaji_harness` / `apokamo/kaji`（Git 依存の `pyproject.toml` を除く） / `.claude/agents/` / `kaji-code-reviewer` / kaji 本体固有 docs パス（`docs/reference/python/` 等） | 「23 skills すべてが汎用化されて同梱」 |
| symlink 整合 | `.agents/skills/` の全エントリが `../../.claude/skills/<name>` を指し、リンク切れゼロ | 「`.agents/skills/` の symlink が張られている」 |
| GitHub dogfooding | fresh repo でサンプル Issue 1 件 → `dev.yaml` を最初から PR 作成まで実行 | 「`dev.yaml` が最初の workflow 実行から PR 作成まで通る」 |
| local dogfooding | fresh repo で `kaji local init` → `dev-local.yaml` 1 周 | 「local provider でも 1 周する」 |
| template 有効化 | `gh api repos/apokamo/kaji-starter-python --jq .is_template` が `true` | 「template repository として有効化」 |

検証ログ（コマンドと結果）は Issue コメントに証跡として残す。

#### dogfooding 実施結果と発見（Phase 2 実施後に追記）

GitHub / local dogfooding を実施し、いずれも完走した（GitHub: `dev.yaml` を PR 作成まで、
local: `dev-local.yaml` を close まで 1 周）。workflow 本体（汎用化 skills・YAML・verdict 経路・
worktree・PR / local merge）は初回実行で正しく動いた。一方、初見利用者の導線・starter の定義に
関わる不足を 4 件発見し、starter に反映した（本設計の該当箇所に契約として織り込み済み）。

| # | 発見 | 対応（反映先） |
|---|------|----------------|
| 1 | GitHub ラベル（`type:*`）が template 複製されず起票が依存する | `scripts/setup_labels.sh` 同梱（ファイル構成 / quickstart 契約） |
| 2 | `.kaji/config.toml` の repo 変更を commit する手順が無く feature PR に混入する | 初回セットアップ commit を明記（quickstart 契約） |
| 3 | worktree の共有 `.venv` で editable install が親 `src/` を指しテストが親コードを import する | `tests/conftest.py` 同梱（ファイル構成 / 制約・前提） |
| 4 | 実行ログの出力先が未文書化で、repo 直下の stray ログが `issue-close` 安全ガードを誤発火させた | ログ出力先を `configuration.md` に定義 + `.gitignore` に `*.log`（制約・前提「実行ログの出力先」） |

発見 4 の本質は「`*.log` の対症療法」ではなく「ログ出力先の定義を文書に明記する」ことである。
いずれも実行を止める致命傷ではなく（発見 1 は事前ラベル作成、3 は agent 回避で自動吸収、
4 は close 再実行で完走）、starter 側の定義・導線を正すための対応である。ガイド側の記述は
Phase 3 で扱う。

### 恒久テストを追加しない理由（testing-convention の 4 条件）

kaji 本体側（docs-only）および starter への `set_agent.py` 用恒久テスト不追加について:

1. **独自ロジックの追加・変更をほぼ含まない**: kaji 本体は文書のみ。`set_agent.py` は
   決定的な行置換 script で、分岐は CLI 名の validate と行 pattern match のみ
2. **想定される不具合パターンが既存ゲートで捕捉済み**: 変換結果の妥当性は
   `uv run kaji validate`（kaji 本体の恒久実装）が捕捉する。docs リンク切れは
   `make verify-docs` が捕捉する
3. **新規テストを追加しても回帰検出情報がほとんど増えない**: `set_agent.py` は利用者 repo では
   初期設定時に高々数回実行される変換 tool であり、starter の `tests/` に同梱すると
   テンプレ利用者の資産に starter メンテ用テストという目的外のコードを残すことになる
4. **理由をレビュー可能な形で説明**: 本節および上記検証マトリクス（validate / 冪等性 /
   exit code の変更固有検証）で担保する

なお starter の `tests/test_smoke.py` は「fresh repo で `make check` が通る」ための最小
Small テスト（package import 等）であり、starter 利用者の恒久資産として同梱する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/guides/python-starter.md | あり（新設） | 本 Issue の成果物 2 |
| README.md | あり | Initial Setup Guide の Issue 参照をガイド + starter リンクへ差し替え |
| docs/README.md | あり | guides 索引への追加 |
| docs/adr/ | なし | 新規技術選定なし（GitHub template repository は GitHub 標準機能。採用判断は Issue と本設計書に記録） |
| docs/ARCHITECTURE.md | なし | kaji_harness のアーキテクチャ変更なし |
| docs/dev/ | なし | kaji 本体の開発ワークフロー変更なし |
| docs/reference/ | なし | kaji CLI / 設定仕様の変更なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし（local-mode.md 等は参照するのみ） |
| AGENTS.md / CLAUDE.md（kaji 本体） | なし | 本体の規約変更なし（starter 側は新規作成） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| GitHub Docs: Creating a template repository | https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-template-repository | 「You can make an existing repository a template, so you and other people can generate new repositories with the same directory structure, branches, and files.」— template 化は既存 repo の設定変更で可能。starter を通常 repo として作成後に template 化する手順の裏付け |
| GitHub Docs: Creating a repository from a template | https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-repository-from-a-template | template から作られた repo は「a single commit」から始まる（履歴を引き継がない）。starter に開発履歴を残す運用でも利用者 repo は汚れない根拠 |
| gh manual: `gh repo create` / `gh repo edit` | https://cli.github.com/manual/gh_repo_create / https://cli.github.com/manual/gh_repo_edit | `gh repo edit --template` で「Make the repository available as a template repository」。公開・template 化を CLI で完結できる根拠 |
| uv docs: Git dependency sources | https://docs.astral.sh/uv/concepts/projects/dependencies/#git | `[tool.uv.sources]` で `{ git = "...", tag = "..." }` 指定により Git 依存を tag に pin できる。kaji の project-local 導入（`uv run kaji`）の根拠 |
| kaji 本体 workflow 定義 | `.kaji/wf/dev.yaml` / `.kaji/wf/dev-local.yaml` | starter YAML の出発点。`pr → review-poll → (BACK_FALLBACK) review` の現行構成を確認済み。starter では `review` を標準 step に昇格させる差分の基準 |
| kaji local mode 仕様 | `docs/cli-guides/local-mode.md` | 「`kaji local init` は overlay (`.kaji/config.local.toml`) しか作らない」— `.kaji/config.local.toml.example` を starter に置かない判断の根拠 |
| kaji 設定仕様 | `docs/reference/configuration.md` | `.kaji/config.toml` の key 型・既定値の正本。starter の config.toml 作成の基準 |
| workflow 記法 | `docs/dev/workflow-authoring.md` | step / cycles / verdict 遷移・`requires_provider` の正本。starter YAML 5 本の構成基準 |
| Issue #243（完了済み） | https://github.com/apokamo/kaji/issues/243 | `AGENTS.md` 正本 + `CLAUDE.md` は `@AGENTS.md` インポート + Claude 固有記述のみ、という構造決定。starter の同構造採用の根拠 |
| Issue #242 本文 | https://github.com/apokamo/kaji/issues/242 | kamo2 での実測（`issue-implement` 約 500 行改変、docs 読み込み過多、`.kaji/artifacts/` tracked 肥大化）に基づく方針の正本。kamo2 repo 自体は private のため、教訓は Issue 本文の記述を一次情報として扱う |
