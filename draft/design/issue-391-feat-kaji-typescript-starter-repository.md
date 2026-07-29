# [設計] TypeScript managed starter repository

Issue: #391

## 概要

Issue 起点の kaji workflow をすぐに開始できる、application-first かつ
framework-neutral な Node.js/TypeScript starter を、独立した検証可能な repository
candidate として構築する。併せて kaji 本体に英語・日本語の利用ガイド、索引導線、
managed starter 登録、登録内容の回帰検証を追加する。

本設計書は Issue #391 を実装可能な責務へ分解する派生文書である。要件、外部契約、
採用技術、品質基準、公開判断の source of truth は Issue #391 本文であり、本設計書は
その決定を変更しない。

## 背景・目的

### ユースケース

- TypeScript 開発者として、GitHub template から repository を生成し、少数の
  placeholder を更新するだけで `make setup && make check` と最初の kaji workflow を
 実行したい。
- GitHub repository をまだ用意していない利用者として、local provider で Issue 作成から
  close までを試したい。
- maintainer として、format、typed lint、typecheck、S/M/L test、coverage、build、
  workflow validation、docs link を単一の非破壊 gate で検証したい。
- agent として、短い `AGENTS.md` から TypeScript 規約、テスト分類、Git/worktree 規約、
  kaji lifecycle の正本へ決定的に到達したい。
- kaji release maintainer として、Python starter と同じ managed-starter 運用で
  TypeScript starter の追随、独立 review、snapshot 公開を管理したい。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|---|---|
| 現行 Python starter をそのまま案内する | Node と kaji の依存分離、TypeScript の dev/build 契約、typed lint、Vitest tag を表現できない |
| `kamo2/apps/web` を切り出す | Next.js、React、bundler、ブラウザ向け設定が framework-neutral な最小 application には過剰 |
| TypeScript 7 と追加 loader を採用する | Issue が固定した compiler API / typed-lint 互換性と、Node native type stripping による依存最小化に反する |
| starter を kaji repository の subtree にする | GitHub template と managed starter の独立 release/snapshot 契約を満たさない |
| 初回公開だけ行い managed starter に登録しない | kaji release への追随漏れを防ぐ継続運用を持てない |

## インターフェース

### 入力

#### 利用者が最初に扱う入力

| 入力 | 型・形式 | 必須性 | 契約 |
|---|---|---|---|
| GitHub template | `apokamo/kaji-starter-typescript` の default branch | 必須 | `Use this template` で履歴非共有の新規 repository を生成する |
| Node.js | `.node-version` の `24.18.1` | 必須 | Linux / macOS / WSL2。native Windows は対象外 |
| npm | `packageManager` の `npm@11.16.0` | 必須 | package manager は npm だけを使い、`npm ci` で lockfile を再現する |
| repository identity | README checklist に列挙した repository 名、package 名、`.kaji/config.toml` の `repo` | GitHub 利用時に変更 | pristine default 一式または整合した変更後一式は許可し、部分変更は検証 script で失敗させる |
| agent | `scripts/set-agent.ts` の `claude` / `codex` / `gemini` | 任意 | 未指定時は単一 agent の既定構成。変換は全 workflow に対して冪等・atomic |
| application config | `process.env` の `APP_MESSAGE` | 任意 | UTF-8 の非空文字列、上限 200 文字。未指定時は非 secret の既定 greeting を使う |
| provider | tracked GitHub config または local overlay | 必須 | `.kaji/config.toml` は GitHub、`.kaji/config.local.toml` は local init が生成し gitignore する |

`APP_MESSAGE` の名前、長さ上限、既定 greeting は、外部契約を持たない初期 sample
application 内の two-way door として本設計で具体化する。後段の review-design /
review-code で「Zod の正常・異常経路を最小に示せるか」を検査し、公開前なら安く変更できる。

#### maintainer が扱う入力

| 入力 | 型・形式 | 契約 |
|---|---|---|
| candidate repository | kaji main worktree の sibling `../kaji-starter-typescript` に置く独立 Git repository | kaji worktree 内へ payload を vendor しない。初回 candidate は remote へ push せず commit SHA で固定する |
| kaji pin | `tools/kaji/pyproject.toml` と `tools/kaji/uv.lock` | kaji v0.18.0 を project-local exact source として固定する |
| dependency graph | `package.json` と `package-lock.json` | dependency spec は exact version、lockfileVersion 3。lockfile は npm が生成し手編集しない |
| publication approval | 人間が確認した candidate SHA、repository 名、visibility、license、tag | workflow 外の明示承認が揃うまで repository 作成、push、template 設定、tag/Release を行わない |

default local path は現行 Python starter と managed-starter runbook の sibling 規約を
TypeScript starter に適用した AI の仮定である。実装開始時に既存 path / remote identity
との衝突がないことを検査し、衝突があれば mutation 前に停止して review-design へ戻す。

### 出力

#### TypeScript starter candidate

- 独立 Git repository の commit SHA で固定された clean candidate。
- 0BSD、`private: true`、ESM、NodeNext、Node native type stripping、`tsc` build を備えた
 最小 CLI-like application。
- exact dependency specs と `package-lock.json`、project-local kaji uv project。
- `.kaji/wf/official/` と `.kaji/wf/custom/` に配置した GitHub/local workflow 群。
- `.claude/skills/` を正本、`.agents/skills/` を per-skill symlink とする consumer skill set。
- 英語正本と日本語版の README、段階化した `docs/dev/` と `docs/reference/`。
- pull request / `main` push で `make check` を実行する read-only CI と、npm /
  GitHub Actions を週次監視する Dependabot。

#### kaji repository

- `docs/guides/typescript-starter.md` と `docs/guides/typescript-starter.ja.md`。
- `README.md` / `README.ja.md` と `docs/README.md` からの TypeScript starter 導線。
- `docs/operations/release/starter-sync-runbook.md` の managed starters 表への登録。
- `.claude/skills/release/SKILL.md` の Release notes 例を managed starters 表と一致させる更新。
- managed starters 表と Release notes template に Python / TypeScript の両 starter が含まれることを
 守る `tests/test_starter_skills.py` の回帰検証。

#### 実行時出力

| コマンド | stdout | stderr / exit |
|---|---|---|
| `npm run dev` | watch mode で sample greeting を出力 | config 不正時は secret value / stack trace を含まない要約を stderr、exit 1 |
| `npm start` | `dist/index.js` から同じ sample greeting を出力 | build 未実施は Node の非 0、config 不正は上記と同じ exit 1 |
| `make check` | 各 gate の結果と最終 PASS | 最初の失敗 gate の診断と非 0。tracked file を変更しない |
| `make test-{small,medium,large}` | 指定 tag の test 結果 | 対象 test なし、tag 契約違反、test failure は非 0 |
| `scripts/set-agent.ts <agent>` | 変更対象または no-op の要約 | 未知 agent / 不正 workflow は書換え前に非 0 |
| template 整合検証 | pristine default または fully customized の判定 | identity の部分変更、残存すべきでない placeholder、skill symlink 切れは非 0 |

#### command contract

| Make target | npm / tool 側の責務 | mutation |
|---|---|---|
| `make setup` | `npm ci` と `uv sync --project tools/kaji --locked` | dependency environment のみ。lockfile は変更しない |
| `make check` | format check → typed lint → no-emit typecheck → test-tag audit → S/M/L 全 Vitest corpus の coverage → clean build → kaji workflow validation → `make verify-static` | tracked file は変更しない。network / credential / agent CLI を要求しない |
| `make lint` | ESLint flat config を対象 source/test/script/config に適用 | なし |
| `make format` | `prettier --check` | なし |
| `make fmt` | Prettier の明示的 write | 対象 tracked file を書換えうる |
| `make typecheck` | `tsc -p tsconfig.json --noEmit` | なし |
| `make test` | tag audit 後、filter なしで恒久 Vitest corpus の `small` / `medium` / `large` を全件実行 | test artifact は gitignore |
| `make test-small` / `medium` / `large` | tag audit 後に対応 tag filter。`large` は network / credential / agent CLI 不要の恒久 test だけ | test artifact は gitignore |
| `make coverage` | tag audit 後、filter なしの S/M/L 全 Vitest corpus で V8 coverage を計測し、`src/**/*.ts` の 4 指標 80% を判定 | coverage artifact は gitignore |
| `make build` | stale `dist/` を除去して `tsc -p tsconfig.build.json` | `dist/` のみ。gitignore |
| `make validate-workflows` | tracked 全 workflow を `./scripts/kaji validate` | なし |
| `make verify-docs` | README / docs / agent instructions / skills の link と path | なし |
| `make verify-static` | `make verify-docs` と npm scripts の template identity、skill set/symlink/語彙、GitHub Actions の actionlint 相当検証を集約 | なし |
| `make dogfood-local` | fresh temporary clone/copy で setup/check と local-provider lifecycle を実行する maintainer acceptance runner | temporary repository と gitignored artifact のみ。network と認証済み agent CLI が必要なため `make check` 外 |

`package.json` scripts は同じ個別責務を直接実行し、Makefile は順序付けと名前の安定した
利用者向け facade に限定する。`verify:template`、`verify:skills`、`validate:actions` を
`verify:static` が束ね、対応する `make verify-static` を `make check` が呼ぶ。local と CI は
同じ `make check` を呼び、別の合格基準を持たない。

### 使用例

#### GitHub template から開始

```bash
git clone <generated-repository-url> starter-app
cd starter-app
make setup
make check
./scripts/kaji issue create --title "feat: first change" \
  --body-file issue.md --label type:feature
./scripts/kaji run .kaji/wf/official/dev.yaml <issue-id>
```

#### local provider で開始

```bash
./scripts/kaji local init
./scripts/kaji issue create --title "feat: local trial" \
  --body-file issue.md --label type:feature
./scripts/kaji run .kaji/wf/official/local/dev-local.yaml <issue-id>
```

#### agent 構成を変更

```bash
npm run set-agent -- codex
make validate-workflows
git diff --exit-code
```

最後の `git diff --exit-code` は同じ agent への 2 回目の変換後に実行し、冪等性を確認する。

### エラー契約

- Zod validation error は issue path と期待形式を示すが、入力値そのもの、環境変数一覧、
  stack trace は通常出力しない。
- Node / npm / uv / agent CLI が存在しない、または pin と不整合なら setup または個別 gate が
 不足 tool と期待 version を示して非 0 で停止する。
- GitHub provider と local workflow の組合せが不一致なら kaji の provider guard により
  dispatch 前に停止する。
- tag audit は missing / duplicate / unknown を test case 単位で列挙し、1 件でもあれば非 0。
- workflow validation、docs link、skill 語彙/symlink、template identity の検証は入力を
 書き換えず、違反 path と契約を示して非 0。
- candidate path が既存の別 repository または dirty worktree なら上書きせず停止する。
- external GitHub 操作、credential 不足、admin 設定未完了は workflow 内成果物の失敗に偽装せず、
 事後確認として Issue に未完了状態を残す。

## 制約・前提条件

- source of truth は Issue #391。本設計と矛盾する場合は Issue を優先し、設計を機械的に
  Issue へ戻せない変更は人間確認まで停止する。
- 対応環境は Linux / macOS / WSL2。Next.js、React、Tailwind、Playwright、bundler、
  container、deployment、npm publish、monorepo、native Windows は含めない。
- Node dependency graph と kaji の Python dependency graph を混ぜない。kaji の唯一の入口は
  executable `scripts/kaji` とし、README、Makefile、skills、workflow の例を統一する。
- runtime dependency は Zod だけとし、quality tool は devDependencies または
  `tools/kaji/` の lock 済み dependency とする。
- `package.json` は `"type": "module"` と `"private": true` を持つ。CommonJS dual package は
 扱わない。
- `tsconfig.json` は source / test / script / config の no-emit typecheck、
  `tsconfig.build.json` は `src/` の emit に責務を限定する。
- compiler option は Issue 記載の全 option を有効にする。特に `strict`、
  `verbatimModuleSyntax`、`isolatedModules`、`erasableSyntaxOnly`、
  `rewriteRelativeImportExtensions`、`skipLibCheck: false` を弱化しない。
- source の相対 import は `.ts` extension、emit 後は `.js` extension。path alias と
  transform-required syntax に依存しない。
- lint は `eslint.config.mjs` の flat config と
  `recommendedTypeChecked + projectService`。format は Prettier の独立 gate とし、
  ESLint plugin 経由で実行しない。
- test case は `small` / `medium` / `large` のちょうど 1 tag を持つ。suite/file からの
 暗黙継承を許す場合も、audit 後の effective tags がちょうど 1 でなければ失敗させる。
- coverage は `src/**/*.ts` 全体の statements / branches / functions / lines を 80% 以上とし、
  entry point を恣意的に除外しない。
- `make check` は setup 後に network access なしで再実行でき、tracked file と
  `git status --short` を変えない。format write は `make fmt` にのみ許可する。
- CI は `pull_request` と `main` push、`permissions: contents: read`、full commit SHA action
  pin、`persist-credentials: false`、lockfile install、`make check` と同一契約を満たす。
- secret、実 credential、private transcript、環境変数値を source、fixture、workflow、
  dogfood evidence に保存しない。
- candidate の independent review までは remote push しない。force push と公開 tag の
 上書きは禁止する。
- `kamo2` は private local source として参照可能だが starter payload へコピーせず、
  framework 固有設定を継承しない。

## 変更スコープ

### 独立 TypeScript starter candidate

| 責務 | 主な file 群 |
|---|---|
| application / config boundary | `src/index.ts`, `src/config.ts`, `.env.example` |
| Node / TypeScript build contract | `package.json`, `package-lock.json`, `.node-version`, `.npmrc`, `tsconfig.json`, `tsconfig.build.json` |
| quality gates | `eslint.config.mjs`, `prettier.config.mjs`, `vitest.config.ts`, `Makefile`, `scripts/audit-test-tags.ts`, docs/template/workflow 検証 scripts |
| regression suite | `tests/` の S/M/L、negative fixtures、agent transformation tests |
| kaji isolation | `tools/kaji/pyproject.toml`, `tools/kaji/uv.lock`, `scripts/kaji`, `.kaji/config.toml`, `.kaji/issues/` |
| workflow / skills | `.kaji/wf/official/`, `.kaji/wf/custom/`, `.claude/skills/`, `.agents/skills/`, `AGENTS.md`, `CLAUDE.md` |
| docs / public surface | `README.md`, `README.ja.md`, `docs/README.md`, `docs/dev/`, `docs/reference/`, `LICENSE` |
| CI / dependency monitoring | `.github/workflows/ci.yml`, `.github/dependabot.yml` |

設計段階の file 名は責務の所在を示す。Issue の公開契約、品質 gate、docs 情報構造を保つ
範囲で、script と test file の統合・分割は two-way door とする。

### kaji repository

| 責務 | file 群 |
|---|---|
| 利用ガイド | `docs/guides/typescript-starter.md`, `docs/guides/typescript-starter.ja.md` |
| 公開導線 | `README.md`, `README.ja.md`, `docs/README.md` |
| managed registration | `docs/operations/release/starter-sync-runbook.md`, `.claude/skills/release/SKILL.md` |
| registration regression | `tests/test_starter_skills.py` |

### 明示的な非変更

- kaji CLI / workflow engine の新機能。
- `kamo2` repository。
- 既存 Python starter の payload または公開履歴。
- managed starter 用の新規 maintenance skill。
- 公開 repository / Settings / tag / GitHub Release（workflow 後の人間 gate まで）。

## 方針

### 責務とデータフロー

1. `src/config.ts` が `process.env` を unknown external input として Zod schema で parse し、
   secret を含まない discriminated な成功値または診断へ変換する。
2. `src/index.ts` は config を受け、正常時は greeting を stdout、異常時は sanitized message を
   stderr に出して process exit code を決める。config parsing と process termination を分離し、
   pure validation を Small test 可能にする。
3. dev は Node native type stripping で `src/index.ts` を直接実行し、build は
   `tsc -p tsconfig.build.json` で `dist/` を再生成する。start は emit 済み JS だけを実行する。
4. npm scripts を各 tool の単一実行面、Makefile を薄い利用者向け束ねとして扱う。
   `make check` は非破壊 gate を決められた順で実行し、stale `dist/` を削除してから build する。
5. Vitest の effective tag metadata を audit し、test 実行前に cardinality と vocabulary を
  検査する。S/M/L 個別 target は同じ test corpus を tag filter する。
6. `scripts/kaji` が `uv run --project tools/kaji --locked kaji ...` へ透過的に委譲し、
   Node toolchain から kaji を隔離する。
7. Python starter の consumer skill set を一覧比較し、maintainer-only skills を除いた上で
   TypeScript command/path/test 語彙へ変換する。symlink と Python 固有語彙の残存を script で
  決定的に検査する。
8. `scripts/set-agent.ts` は全 workflow を memory 上で parse / validate / transform してから
  一括反映する。途中失敗時は 1 file も変更せず、同じ target への再実行は no-op にする。
  既定 workflow に `review-poll` は入れず、外部 review bot を設定済みの利用者だけが選ぶ
  option として starter README/docs に設定条件と検証手順を記載する。
9. local candidate の quality gate と fresh-state dogfood を完了し、candidate SHA と環境・
   command・artifact・再実行結果を Issue #391 に集約する。
10. kaji 側は candidate の公開契約を英日 guide と managed starters 表へ登録し、Python /
    TypeScript の両行を静的 test と docs link check で守る。

### candidate 作成・独立 review・公開準備

1. kaji main worktree の sibling `../kaji-starter-typescript` が存在しないことを確認して
  独立 Git repository を初期化する。存在する場合は remote、branch、status、所有目的を
  read-only で確認し、一致を証明できなければ変更しない。以降、candidate path は runbook の
  default local path と同じこの相対表記を正本とする。
2. candidate payload を TDD で構築し、`make setup` 後の offline `make check`、clean status、
  secret scan、placeholder state を確認して local commit を作る。
3. fresh copy を candidate commit から作り、README だけを入口に setup/check/local provider
   lifecycle を実行する。元 candidate の tracked state に dogfood artifact を混入させない。
4. candidate session と別 session が Issue #391、Python starter、kamo2、公式一次情報に対して
  差分 review し、対象 SHA を固定した PASS evidence を Issue に残す。
5. kaji feature branch の docs/registration/test と candidate SHA の両方が review 済みになった
  時点を workflow 内の公開準備完了とする。
6. workflow 後に人間が SHA、`apokamo/kaji-starter-typescript`、public、0BSD を承認した後だけ
  repository を作成し、reviewed SHA を push して template repository を有効化する。
7. public GitHub Actions と GitHub-provider fresh-template dogfood を確認し、dogfood 後の
  reviewed commit を annotated `kaji-v0.18.0` tag / GitHub Release にする。

### rollback / 再試行

- **公開前**: remote ref がないため、candidate の不合格 SHA は publish 対象から外し、
 既知の review 対象 SHA から新しい修正 commit を作る。dirty directory の上書きや
  destructive reset を自動実行しない。
- **atomic push 失敗**: main と tag の片方だけを公開しない。原因を解消し、remote ref が
 未作成であることを確認して同じ reviewed SHA で再試行する。force push はしない。
- **公開後の defect**: 公開済み tag は削除・移動せず、新しい修正 commit を独立 review する。
 同じ kaji version の修正版 snapshot は runbook に従い `kaji-v0.18.0-rN` を使う。
- **starter failure**: 公開済み kaji v0.18.0 tag / Release / PyPI は rollback しない。
 starter の状態と再試行を Issue / Release の repository 別状態で追跡する。
- **kaji 側 PR の差し戻し**: external candidate SHA を保持しつつ feature branch の
 docs/registration/test を通常の review/fix/verify loop で修正する。candidate 契約も変わる場合は
 新 SHA を作り、古い review evidence を再利用しない。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|---|---|---|---|
| source of truth | Issue #391 を優先し、本設計は派生文書とする | Issue #391「概要」「重要判断」および 2026-07-30 人間決定 | 矛盾時の停止条件と、設計の two-way door を区別した |
| Issue / repository 境界 | repository 骨格、workflow、dogfood、docs、managed 登録を #391 内で扱い、starter payload は独立 repo にする | Issue #391「スコープ境界」「Repository と配布契約」 | kaji branch の成果物と external candidate SHA の証跡境界を定義した |
| 公開形態 | `apokamo/kaji-starter-typescript`、public template、0BSD、npm publish 無効 | Issue #391「Repository と配布契約」、起票前の人間承認 | 公開前 review と workflow 後の admin 操作を分離した |
| product scope | application-first / framework-neutral な Node CLI-like app | Issue #391「Repository と配布契約」「含まないもの」 | `APP_MESSAGE` を Zod で扱う最小 sample IF とした |
| toolchain | Node 24.18.1、npm 11.16.0、TypeScript 6.0.3、native type stripping、ESM/NodeNext、`tsc` build | Issue #391「Runtime」「TypeScript の実行・build 契約」および公式一次情報 | no-emit と emit の責務、dev/build/start の観測点を分離した |
| quality stack | typed ESLint、Prettier、Vitest/V8、exactly-one S/M/L tag、80% coverage、Zod、`make check` | Issue #391「Lint」「Test」「品質 gate」 | effective tag audit、negative test、non-mutating gate の検証面を定義した |
| kaji dependency isolation | `tools/kaji` uv project + `scripts/kaji` wrapper | Issue #391「kaji の分離と workflow」 | docs/skills/Makefile を wrapper へ統一し lock mode で実行する責務を定義した |
| skill baseline | Python starter の consumer skills を TypeScript 用に汎用化し、maintainer skills を除外 | Issue #391「Skills、agent instructions、docs」 | 一覧差分、Python 語彙、per-skill symlink の決定的検査を定義した |
| managed maintenance | 既存の言語非依存 update/review/release 運用を再利用 | Issue #391「継続保守」、Issue #341、`starter-sync-runbook.md` | runbook 表、Release notes 例、回帰 test の同期点を定義した |
| dogfood evidence | local と GitHub の fresh-state 結果を SHA/version/command/artifact/発見事項で記録 | Issue #391「Dogfooding と証跡」、2026-07-30 人間承認 | local は workflow 内、GitHub/admin は事後確認とし、secret/transcript を除外した |
| publication gate | candidate review までは workflow 内、repository creation/settings/push/tag/Release は人間承認後 | Issue #391「外部公開操作」、`workflow_completion_criteria.md`、Issue #341 | 公開前・atomic failure・公開後 defect の rollback 境界を定義した |
| candidate default path | kaji main sibling `../kaji-starter-typescript` | **AI の仮定**。Python starter と managed-starter runbook の既存 sibling 規約が根拠。implement 開始時と review-code で衝突・identity を検査 | external candidate を kaji worktree に vendor しない path とした |
| sample config IF | optional `APP_MESSAGE`: non-empty、最大 200 文字、safe default | **AI の仮定**。Zod 正常/異常経路を過剰な domain logic なしで示せる。review-design / review-code で検査 | config parse と process output/exit を分離した |
| action / script の内部選定 | Issue の contract を満たす最小の pinned tool / deterministic script を使う | **AI の仮定**。個別 package、action SHA、parser 構造は公開前に安く変更可能。implementation と review-code で lockfile、license、offline gate、SHA を検査 | tool 名ではなく検出すべき failure と非破壊性を固定した |

未決の one-way door はない。公開 repository 名、visibility、license、toolchain、quality
threshold、managed 運用、公開 gate は Issue #391 で人間決定済みである。

## テスト戦略

### 変更タイプ

- **実行時コード変更**: starter application、validation、test tag audit、agent conversion、
  template/workflow/docs 検証 script、kaji wrapper。
- **metadata / packaging / CI**: package/uv lock、TypeScript config、GitHub Actions、
  Dependabot、template identity。
- **docs**: starter README/docs と kaji 本体の英日 guide、index、runbook。

実行時コードを含むため、Small / Medium と、offline で決定的な Large を恒久 Vitest
corpus として持つ。agent / external state を要する lifecycle は Vitest corpus へ混在させず、
下記の分類表どおりに `make check` との境界を固定する。

### Small テスト

- `APP_MESSAGE` の未指定、正常、空、上限境界、上限超過を Zod schema に通し、成功値と
  sanitized error が入力値や環境変数一覧を漏らさないこと。
- application の成功 output と config failure の stderr / exit decision を process I/O から
 分離した純粋ロジックとして検証すること。
- tag vocabulary と exactly-one cardinality の判定が missing / duplicate / unknown を
 区別すること。
- agent 名から workflow agent/model/effort への mapping と invalid effort fallback が
 決定的であること。
- template identity の pristine / customized / partial state 判定、skill baseline 集合差分、
 Python 固有語彙の禁止判定。

### Medium テスト

- temporary filesystem に test fixture 群を置き、tag audit が valid corpus を通し、
  missing / duplicate / unknown の各 negative fixture を非 0 にすること。
- temporary copy の全 workflow に `scripts/set-agent.ts` を適用し、Claude/Codex/Gemini の
 各変換が valid、2 回目 no-op、途中の不正 YAML では全 file unchanged になること。
- `tsc` build が clean `dist/` を生成し、source の `.ts` relative import が emit 後 `.js` に
 変換され、`src/` 外を production artifact に含めないこと。
- docs link、template identity、skill symlink/語彙、workflow static validation が
  temporary filesystem の broken fixture を決定的に拒否すること。
- `scripts/kaji` が `tools/kaji` project と lock mode を使い、引数と exit code を
 透過的に委譲すること。実 GitHub API は呼ばない。
- kaji 本体の `tests/test_starter_skills.py` で runbook と Release notes 例の managed starter
 集合が一致し、Python / TypeScript の両 entry が存在すること。

### Large テスト

| 検証観点 | 分類 | 起動面・実行条件 | `make check` |
|---|---|---|---|
| 実 Node child process で TypeScript entry point を起動し、正常/不正 config の stdout / stderr / exit code を確認 | **(a) 恒久 Vitest corpus** (`large`) | `make test-large`。network / credential / agent CLI 不要、bounded timeout | 対象。`make coverage` の全 tag corpus に含む |
| clean build 後の `npm start` が `dist/index.js` から成功し、dev と build/start が同じ利用者契約を満たす。watch smoke は最初の output 後に bounded に終了 | **(a) 恒久 Vitest corpus** (`large`) | `make test-large`。temporary `dist/`、network 不要 | 対象。`make coverage` の全 tag corpus に含む |
| fresh copy で README checklist、setup/check、local provider の Issue 作成から close まで完走し、元 candidate に artifact / secret / placeholder drift を残さない | **(b) 恒久 acceptance runner、Vitest corpus 外** | `make dogfood-local` / `npm run dogfood:local`。認証済み agent CLI と agent API network がある maintainer 環境で candidate snapshot ごとに実行 | 対象外。offline / deterministic gate ではない |
| setup 済み candidate で network を遮断した `make check` が通り、前後の `git status --short` が一致 | **(c) 変更固有の外側検証** | implementation/final-check が warmed dependency 環境で network deny を設定し、`make check` の外側から before/after status と exit 0 を記録 | 自己再帰を避けるため corpus には入れない |
| public template から GitHub-provider workflow を完走し、Actions / URL / SHA を確認 | **(c) workflow 後の変更固有検証** | repository 作成・Settings・credential・人間承認後。Issue の事後確認に command / URL / SHA / 結果を記録 | 対象外 |

分類 (b) は再実行可能な script として repository に残すが、agent の外部 API と非決定的な
agent 応答を必要とするため daily/required gate から分離する。分類 (c) は対象 commit の
環境契約または公開状態を一度確認する acceptance evidence であり、application の恒久
回帰 corpus ではない。

### test corpus / coverage の横断検証

- 初期 corpus は実質的な Small / Medium / Large を各 1 件以上持ち、空分類用 placeholder は
 作らない。
- 各 test case の effective tags は `small` / `medium` / `large` のちょうど 1 つ。
  type augmentation と audit script の両層で unknown / cardinality drift を防ぐ。
- `make test` は filter なしで恒久 Vitest corpus の S/M/L を全件実行し、個別 target は
  同じ corpus を tag filter する。`make test-large` に agent / GitHub dogfood を含めない。
- `make check` は tag audit 後に `make coverage` を呼び、`make coverage` は tag filter なしで
  S/M/L の全恒久 Vitest corpus を実行する。coverage threshold の測定 corpus はこの全件集合で
  固定し、small+medium だけへの縮退を許可しない。
- V8 coverage は上記全件実行で `src/**/*.ts` の statements / branches / functions / lines の
  すべて 80%。entry point を対象に含める。child-process smoke の coverage だけに依存せず、
  entry point が委譲する config/application logic を in-process test でも通して計測可能にする。
- strict option の形骸化は、各 option に対応する invalid fixture または compiler config の
 決定的 inspection で検出する。少なくとも erasable syntax、unchecked index、optional
 property、unused、fallthrough、override、import extension の退行を gate で拒否する。

### 変更固有検証

- candidate と kaji worktree の双方で docs link check。
- exact version specs、lockfileVersion 3、`npm ci` / `uv sync --locked` の再現性。
- GitHub Actions の syntax、PR/main trigger、read-only permissions、full SHA、
  `persist-credentials: false`、lockfile install、`make check` 呼出し。
- Dependabot の npm / github-actions weekly entry。
- candidate commit の tracked file 一覧に `.env`、credential、coverage、dist、
  `.kaji/artifacts/`、local overlay が含まれないこと。
- independent review が target candidate SHA と一致し、古い SHA の PASS を再利用しないこと。

### 恒久テストを追加しない範囲

#### offline / clean `make check` の外側検証

repository 内の恒久 test にせず、implementation/final-check の変更固有検証とする。

1. **独自ロジック**: 新規 application logic を検証するものではなく、出荷対象の
   `make check` 自身の合成契約（network 不要・tracked state 不変）を外側から観測する。
2. **既存 gate**: 個別 failure は `make check` 内の恒久 S/M/L、build、static gate が検出し、
   外側検証は network deny と before/after status だけを追加確認する。
3. **追加情報**: `make check` から `make check` を再帰起動する恒久 test は実行不能で、
   wrapper fixture を足しても実 gate の offline/clean 性以上の回帰情報を増やさない。
4. **review 可能性**: candidate SHA、network deny 方法、実行 command、exit code、
   before/after `git status --short` を Issue の実装報告へ記録する。

#### GitHub/admin 公開状態の検証

GitHub Repository Settings の template 有効化、public repository 上の Actions、
GitHub-provider dogfood、annotated tag / Release は repository 内の恒久 test にしない。

1. **独自ロジック**: repository admin state と外部 GitHub resource の存在確認であり、
   candidate 内の新規 runtime logic ではない。
2. **既存 gate**: workflow/config/permission/SHA pin は `make verify-static`、local lifecycle は
   `make dogfood-local` で公開前に検出する。
3. **追加情報**: mock GitHub state を恒久 fixture にしても実 Settings、Actions、template 生成、
   public URL の成功を保証する回帰情報は増えない。
4. **review 可能性**: workflow 後の確認項目に operator、command、URL、candidate SHA、
   Actions run、結果を記録する。

分類 (b) の local dogfood は `make dogfood-local` として恒久化するため、この「追加しない範囲」
には含めない。workflow 内では static gate、全 S/M/L Vitest、offline/clean 外側検証、
local dogfood までを実施し、workflow 後は実 GitHub evidence で補完する。

## 影響ドキュメント

| ドキュメント | 影響 | 理由 |
|---|---|---|
| `README.md` / `README.ja.md` | あり | Python だけでなく TypeScript starter も starting point として案内する |
| `docs/README.md` | あり | TypeScript starter 英日 guide を Tutorials に追加する |
| `docs/guides/typescript-starter.md` / `.ja.md` | 新規 | template 作成、setup、provider、agent、customization、troubleshooting、TypeScript 7 非採用の tool compatibility、外部 bot 設定済み利用者向け `review-poll` option の公開導線 |
| `docs/operations/release/starter-sync-runbook.md` | あり | managed starters 表に TypeScript repository と default local path を登録する |
| `.claude/skills/release/SKILL.md` | あり | Release notes の repository 別 PENDING 例を runbook の全 managed starters と一致させる |
| starter `README.md` / `README.ja.md` | 新規 | pristine で動く入口、変更 checklist、GitHub/local provider、quality commands |
| starter `docs/dev/` | 新規 | change/gate、testing、Git/worktree、kaji workflow、completion criteria、baseline |
| starter `docs/reference/` | 新規 | configuration と TypeScript standards。TypeScript 6.0.3 baseline、TypeScript 7 の API / typescript-eslint 互換性による非採用理由を永続化 |
| starter `AGENTS.md` / `CLAUDE.md` | 新規 | 最小不変条件と docs routing、Claude import |
| `docs/adr/` | なし | kaji core architecture は変更せず、starter 固有の技術選定は人間決定済み Issue、starter TypeScript standards、公開 guide に永続化する |
| `docs/ARCHITECTURE.md` | なし | kaji runtime / module dependency graph は変更しない |
| `docs/dev/`（kaji 本体） | なし | dev workflow、test size、completion criteria の既存契約を再利用し変更しない |
| `docs/reference/python/` | なし | kaji Python 規約は変更しない |
| `docs/cli-guides/` | なし | kaji CLI の引数・挙動は変更せず、starter 利用手順は専用 guide に置く |
| root `AGENTS.md` / `CLAUDE.md`（kaji 本体） | なし | kaji repository の agent 規約は変更しない |

## 完了条件の段階確認

### 設計段階で充足

- 設計書 path、Issue 決定ごとの provenance、S/M/L 戦略、影響 docs、公開/rollback 手順を
 本書に明記した。
- repository / distribution、toolchain、dev/build、runtime validation、quality stack、
 test/coverage、commands、kaji isolation、workflow/skills/docs、CI、dogfood の全契約を
 実装・検証責務へ対応付けた。
- workflow 内条件と `### ワークフロー完了後の確認項目` の分離は
  `docs/dev/workflow_completion_criteria.md` の再実行可能性基準と一致している。

### 実装段階以降で確認

- candidate payload、lockfiles、test/coverage、CI/Dependabot、workflow/skills、docs の実体。
- offline / clean `make check`、local dogfood、candidate independent review。
- kaji 本体の英日 guide、index、runbook/release skill、回帰 test、`make check`。

### workflow 後に確認

- public repository 作成、template Settings、public Actions、GitHub-provider dogfood、
  `kaji-v0.18.0` tag / GitHub Release。いずれも external state または admin 権限を要するため、
  Issue 本文の現行分類を維持する。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|---|---|---|
| Issue #391 | https://github.com/apokamo/kaji/issues/391 | 要件、外部契約、version、quality threshold、運用判断の source of truth。one-way door は人間決定済み |
| Node.js TypeScript support | https://nodejs.org/api/typescript.html | v24.12.0 で type stripping が stable。Node は tsconfig を読まず erasable syntax だけを直接実行し、`.ts` import extension、`erasableSyntaxOnly`、`rewriteRelativeImportExtensions`、`verbatimModuleSyntax` を推奨 |
| Node.js releases | https://nodejs.org/en/about/previous-releases | Node 24 は LTS line。starter は Issue 決定の 24.18.1 exact pin と `>=24.12 <25` engine range を使う |
| TypeScript 6.0 announcement | https://devblogs.microsoft.com/typescript/announcing-typescript-6-0/ | 6.0 は JavaScript compiler codebase の最終 release で、7.0 native port への bridge。Issue の 6.x 採用判断を裏付ける |
| TypeScript 7 announcement | https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/ | 7.0 は Go native port で、7.0 自体は API を提供せず typescript-eslint 等には 6.0 API との side-by-side 利用を案内している。Issue の compatibility 判断により初期版では 6.0.3 を採用する |
| TypeScript Modules Reference | https://www.typescriptlang.org/docs/handbook/modules/reference.html#nodenext | NodeNext は Node の dual module semantics と package `"type"` に従い、Node application 向けの module/moduleResolution mode |
| typescript-eslint dependency versions | https://typescript-eslint.io/users/dependency-versions/ | 8.65.0 時点の TypeScript support range は `>=4.8.4 <6.1.0`、ESLint 10 を support。TypeScript 6.0.3 と整合 |
| typescript-eslint typed linting | https://typescript-eslint.io/getting-started/typed-linting/ | `recommendedTypeChecked` と `parserOptions.projectService: true` が type-aware lint の公式構成 |
| ESLint configuration files | https://eslint.org/docs/latest/use/configure/configuration-files | `eslint.config.mjs` は root flat config の公式 file 名。TypeScript config file は追加 setup が必要なため loader-free `.mjs` を使う |
| Prettier and linters | https://prettier.io/docs/integrating-with-linters | format と code-quality lint を分離し、`eslint-config-prettier` で競合 rule を無効化。Prettier を lint plugin として実行する構成は非推奨 |
| Vitest test tags | https://vitest.dev/guide/test-tags | test option の tags、TypeScript `TestTags` augmentation、`--tags-filter` を公式に提供する |
| Vitest coverage | https://vitest.dev/guide/coverage | V8 coverage provider と include/reporting を構成できる。threshold 80% は Issue の品質決定 |
| Zod | https://zod.dev/ | untrusted data を schema で parse し validated/type-safe な値を得る TypeScript-first validation。`strict` が要件 |
| npm ci | https://docs.npmjs.com/cli/commands/npm-ci | lockfile に基づく clean install を CI/再現 setup に使い、dependency graph を install 時に更新しない |
| GitHub Actions secure use | https://docs.github.com/en/actions/reference/security/secure-use | full-length commit SHA が action の immutable release を参照する方法。token permission 最小化も要求する |
| GitHub template repositories | https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-repository-from-a-template | read access のある利用者が同じ directory/file 構造から履歴非共有の repository を生成できる |
| uv projects | https://docs.astral.sh/uv/guides/projects/ | `pyproject.toml` と version-control 対象の exact `uv.lock` で再現し、`uv run --project` で隔離環境を実行できる |
| managed starter sync runbook | `docs/operations/release/starter-sync-runbook.md` | kaji Release 後の update → independent review → human approval → atomic publish と repository 別状態管理の正本 |
| workflow completion criteria | `docs/dev/workflow_completion_criteria.md` | external admin / credential state を workflow 後の確認へ分離し、workflow 内では静的検証を行う |
| Python starter | `/home/aki/dev/kaji/kaji-starter-python` / https://github.com/apokamo/kaji-starter-python | consumer workflow/skills/docs、agent conversion、Make targets、managed starter の既存 baseline。Python 固有 tool/path はコピーしない |
| kamo2 TypeScript sources | `/home/aki/dev/kamo2/apps/web/{package.json,tsconfig.json,eslint.config.mjs,vitest.config.ts}` | flat lint、Vitest tags、Zod 等の運用実績を参照する一方、Next.js/React/bundler 固有設定は starter へ持ち込まない |
| kaji v0.18.0 Release | https://github.com/apokamo/kaji/releases/tag/v0.18.0 | project-local kaji pin と初期 starter snapshot tag の対象 release |
