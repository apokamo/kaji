# [設計] issue-design BACK 検出コマンド不正 / local workflow の self-RETRY cycle 欠落 / make format の mutating gate 修正

Issue: #259

## 概要

#242 Phase 2 レビューで発見された、kaji 本体に現存する 3 件の実行時バグを修正する:
(1) `issue-design/SKILL.md` の BACK 検出コマンドが GitHub provider で常に失敗する、
(2) local provider 系 workflow で self-RETRY step が cycle 未所属のため RETRY 上限が効かず無限ループしうる、
(3) `make check` の `format` target が mutating で worktree を汚す。

項目 2（BACK 検出マーカーの producer/consumer 契約不一致）は #261 に切り出し済みで本設計の対象外。

## 背景・目的

3 項目とも「skill / YAML の正本手順どおりに動く agent が壊れる / 品質 gate が worktree を汚す」類のバグ。
starter（kaji-starter-python）側は #242 で修正済みであり、本体へ還流する。
修正方式は Issue 本文「検討済み決定事項（2026-07-06 確定・maintainer 合意済み）」で確定しており、設計フェーズで再検討しない。

### 項目 1: `issue-design` の BACK 検出コマンドが GitHub provider で常に失敗

- **Observed Behavior (OB)**: `.claude/skills/issue-design/SKILL.md:183` の
  `kaji issue view [issue_id] --comments --output json` は GitHub provider で
  `unknown flag: --output`（exit≠0）となり、BACK_COUNT が空文字 → fail-loud 規定により
  design step は毎回 ABORT する。2026-07-07 に本 design セッションでも再現を実測した:

  ```
  $ kaji issue view 259 --output json
  unknown flag: --output
  Usage:  gh issue view {<number> | <url>} [flags]
  ```

- **Expected Behavior (EB)**: BACK 検出 pipeline が正常に comment 配列を取得し、
  BACK verdict コメント数（0 以上の整数）を返すこと。正しいコマンド形は
  `kaji issue view [issue_id] --json comments`（gh 互換 `--json` フィールド指定。
  https://cli.github.com/manual/gh_issue_view ）。実測で
  `kaji issue view 259 --json comments | jq '.comments | length'` が exit 0 で件数を返すことを確認済み。
  local provider も `cli_main.py` の `_local_issue_view` が gh 互換 `--json` を実装済みで、
  両 provider とも同一構造（`.comments[].body`）を返す（Issue 検討経緯コメントで確認済み）。

### 項目 3: local workflow の self-RETRY step が cycle 未所属（無限ループの恐れ）

- **Observed Behavior (OB)**: `.kaji/wf/dev-local.yaml` の `implement`（`RETRY: implement`）/
  `final-check`（`RETRY: final-check`）/ `close`（`RETRY: close`）、および
  `.kaji/wf/docs-local.yaml` の `final-check` / `close` が、いずれの cycle にも所属していない
  （2026-07-07 grep で実測。dev-local.yaml:70,115,124 / docs-local.yaml:65,75）。
  runner は cycle 経由でのみ RETRY 上限を enforce する（`kaji_harness/runner.py:860`、
  increment 条件は `cycle and current_step.id == cycle.loop[-1] and verdict.status == "RETRY"`）ため、
  RETRY を出し続ける agent がいると無限ループする。
- **Expected Behavior (EB)**: GitHub 系 3 本（dev / dev-thorough / docs）と同じく、self-RETRY step は
  1-step cycle（`loop: [<step>]`, `max_iterations: 3`, `on_exhaust: ABORT`）に所属し、上限到達で ABORT する。
  `close` の `RETRY: close` は dead edge（`issue-close` skill は RETRY を返さない。dev.yaml の
  `close` step にも RETRY edge は無い）であり、cycle 追加ではなく除去する。

### 項目 4: `make check` の `format` が mutating

- **Observed Behavior (OB)**: `Makefile` の `format:` は `ruff format $(SOURCES)`（`--check` なし）。
  gate として実行するとファイルを書き換え、「check 通過なのに worktree に未コミット差分」が生じ、
  `i-pr` の未コミット確認を詰まらせ得る。
- **Expected Behavior (EB)**: `make check` は非破壊 gate（`ruff format --check`）であり、
  mutating な整形は明示的な `make fmt` でのみ行われる（starter と同構成）。

## 再現手順

### 項目 1

1. 前提: GitHub provider（`.kaji/config.toml` の provider=github）のリポジトリ
2. 実行: `kaji issue view <既存 issue 番号> --comments --output json`
3. 観測: `unknown flag: --output` を stderr に出力し exit≠0（→ SKILL.md Step 1.6 の
   fail-loud 規定に従うと BACK_COUNT 空文字 → 毎回 ABORT）

### 項目 3

1. 前提: `.kaji/wf/dev-local.yaml` の `cycles:` セクションを確認
2. 観測: `implementation` / `final-check` cycle が存在しない一方、step 定義には
   `RETRY: implement` / `RETRY: final-check` / `RETRY: close` の self-edge がある
3. `runner.py:860` の increment 条件により、cycle 未所属 step の RETRY はカウントされず
   `max_iterations` が効かない（コード確認で確定。実際に無限ループを走らせる再現は不要）

### 項目 4

1. 実行: 未整形の Python ファイルがある状態で `make check`
2. 観測: `format` target がファイルを書き換え、`git status` に未コミット差分が発生する
   （`ruff format` は `--check` なしでは整形を適用する。
   https://docs.astral.sh/ruff/formatter/ ）

## 根本原因（Root Cause）

| 項目 | なぜ間違っているか | いつから |
|------|-------------------|---------|
| 1 | `--output json` は kaji CLI にも gh にも存在しないフラグのまま、実測検証なしで SKILL.md に書かれた。GitHub provider の `kaji issue view` は `gh issue view` への素通しであり、gh の JSON 出力は `--json <fields>` 形式が正しい | gl:22（GitLab 時代）の Step 1.6 追加時（commit 84ad6de / cc9988d）から。GitHub 移行後（4b1d52d）も未修正 |
| 3 | #247 で local 系 2 本を新設した際、dev.yaml が当時既に持っていた `implementation` / `final-check` の 1-step cycle を移植し漏れた（005d271 時点の dev.yaml には `implementation:` cycle が存在するが dev-local.yaml には無いことを `git show` で確認）。さらに dev.yaml に無い `RETRY: close` dead edge を持ち込んだ | commit 005d271（2026-06-23, #247）から |
| 4 | `format` target が gate（検査）と整形（変更）の 2 役を兼ね、`check` の依存に mutating なまま組み込まれた | commit 1f2c73d（#124）から |

**同根の他の壊れ箇所の調査結果**（2026-07-07 実測）:

- `--output json` の残存出現は `issue-design/SKILL.md` の 4 行（179 / 183 / 203 / 216）のみ。
  `draft/lab/gitlab-validation/kaji-pr-mr-bridge.md:41` にも出現するが、これは撤去済み GitLab 対応の
  歴史的検討記録であり修正対象外
- self-RETRY edge の全出現は grep で確認済み: GitHub 系 3 本（dev / dev-thorough / docs）は
  すべて 1-step cycle 所属済み（dev-thorough.yaml:27-35 / docs.yaml:20-23 で確認）。
  未追跡の `dev-thorough-fable.yaml` / `docs-fable.yaml` も 1-step cycle 構成のため対象外（Issue 確定事項 6）
- Makefile に `fmt` target は未存在（名前衝突なし）。CI は `labels-sync.yml` のみで `make check` を
  使わないため CI 影響なし（Issue 確定事項）
- **mutating `ruff format` を gate として実行する箇所の棚卸し**（2026-07-07 grep 実測、設計レビュー指摘 1 を受けて追加）:
  `issue-implement/SKILL.md:239` と `issue-fix-code/SKILL.md:114` が「AGENTS.md の pre-commit 契約
  （`make check`）と等価」と明記した品質チェックチェーン内で `--check` なしの
  `ruff format kaji_harness/ tests/` を直接実行している。`make check` を非破壊化すると
  この等価性が崩れ、かつ gate 実行がファイルを書き換える同根の問題が skill 側に残るため、
  両 skill も修正対象に含める。`issue-review-code/SKILL.md:118` は既に `ruff format --check` であり対象外

## インターフェース

bug 修正のため IF は原則維持。変更点は以下のみ:

### 変更前 / 変更後

| 対象 | 変更前 | 変更後 |
|------|--------|--------|
| SKILL.md Step 1.6 観測 3 | `kaji issue view [issue_id] --comments --output json` | `kaji issue view [issue_id] --json comments` |
| dev-local.yaml cycles | `design-review` / `code-review` のみ | + `implementation` / `final-check`（1-step cycle） |
| dev-local.yaml `close.on` | `PASS / RETRY / ABORT` | `PASS / ABORT`（`RETRY: close` 除去） |
| docs-local.yaml cycles | `doc-review` のみ | + `final-check`（1-step cycle） |
| docs-local.yaml `close.on` | `PASS / RETRY / ABORT` | `PASS / ABORT`（`RETRY: close` 除去） |
| `make format` | `ruff format $(SOURCES)`（mutating） | `ruff format --check $(SOURCES)`（非破壊 gate） |
| `make fmt`（新設） | — | `ruff format $(SOURCES)`（mutating な整形） |
| issue-implement / issue-fix-code の品質チェックチェーン | `... && ruff format kaji_harness/ tests/ && ...`（mutating） | `... && ruff format --check kaji_harness/ tests/ && ...`（非破壊。整形差分で FAIL した場合は `make fmt` で整形し、差分をコミット対象に含めて再チェック） |

### 後方互換性の評価

- 項目 1: 旧コマンドは一度も動作していない（導入時から exit≠0）ため、互換対象の「動いていた過去」が
  存在しない。互換レイヤは書かない（Issue 確定事項 5）
- 項目 3: cycle 追加は RETRY 無限ループを上限 3 回 + ABORT に変える挙動変更だが、これが本来の仕様
  （GitHub 系 3 本と同一パターン）。`close` の RETRY edge は producer（issue-close skill）が
  RETRY を返さないため、除去による挙動変化なし
- 項目 4: `make format` の意味が「整形する」→「検査する」に変わる。「make check が整形してくれる」
  前提の手順は無い（Issue 確定事項）が、issue-implement / issue-fix-code は `make check` 等価チェーン内で
  mutating な `ruff format` を**直接**実行しており、`make check` 非破壊化後は等価性が崩れる。
  両 skill のチェーンも `--check` 化して等価性を維持する（設計レビュー指摘 1 対応）。
  整形が必要な場合は新設の `make fmt` を使う

## 制約・前提条件

- Issue 本文「検討済み決定事項（2026-07-06 確定・maintainer 合意済み）」6 項は確定事項であり、
  設計・実装フェーズで再検討しない
- **#261 との境界**: BACK 検出の regex / heading gate / fail-loud 構造には触れない。
  本 Issue で直すのはコマンド呼び出し形（`--json comments` 化）のみ。marker 方式への
  恒久対応・ADR 008 は #261 のスコープ
- 後方互換レイヤ（旧 regex / 旧コマンドのフォールバック温存）は書かない（Issue 確定事項 5）
- Makefile 変更を含むためコード変更扱い: feature branch（`fix/259`）→ `--no-ff` merge、
  コミット前に `make check` 必須（AGENTS.md Always-Apply Rules）
- 項目 4 の `--check` 化は「現リポジトリが `ruff format --check` を素通しする」ことが前提
  （2026-07-07 実測で exit 0 を確認済み。実装フェーズ冒頭で再確認する）
- 未追跡ファイル `dev-thorough-fable.yaml` / `docs-fable.yaml` には触れない（対象外確定。
  ただし新規テストが `.kaji/wf/*.yaml` を glob する場合、未追跡ファイルの扱いに注意する —
  既存 `test_workflow_set_invariants.py` は 5 本固定を assert しており、未追跡 YAML が
  worktree に存在すると既存テストが先に落ちる。新規テストは既存テストと同じ glob 方式に揃え、
  baseline failure の扱いは実装フェーズの regression 判定基準に従う）

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `.claude/skills/issue-design/SKILL.md` | 4 行（179 / 183 / 203 / 216）を `--json comments` ベースに更新。216 行の provider 別フォールバック注記は「両 provider 同構造（`.comments[].body`）のため分岐不要」に簡素化 |
| `.kaji/wf/dev-local.yaml` | `implementation` / `final-check` の 1-step cycle 追加、`close` の `RETRY: close` 除去 |
| `.kaji/wf/docs-local.yaml` | `final-check` の 1-step cycle 追加、`close` の `RETRY: close` 除去 |
| `Makefile` | `format` を `--check` 化、`fmt` 新設、`.PHONY` / help 文字列更新 |
| `.claude/skills/issue-implement/SKILL.md` | Step 7a のチェーン（L239）を `ruff format --check` 化し、整形差分 FAIL 時は `make fmt` → 再チェックの手順を追記。証跡テンプレート内の gate 出力ラベル（L297 / L327 / L464 の `ruff format`）を `ruff format --check` 表記に整合 |
| `.claude/skills/issue-fix-code/SKILL.md` | Step 3.1 のチェーン（L114）を同様に `--check` 化、証跡テンプレート（L155）の表記を整合 |
| `docs/dev/workflow-authoring.md` | 「サイクル定義」節に「self-RETRY を持つ step は cycle 所属必須（loop 末尾）」の制約を追記（再発防止。完了条件の要否判断 → **必要と判断**: runner の enforce 実装と YAML 記述の暗黙契約であり、#247 の移植漏れはこの制約が明文化されていれば防げた） |
| `docs/dev/testing-convention.md` | L211 のコミット前ゲート構成 `ruff format` → `ruff format --check` に更新 |
| `docs/dev/development_workflow.md` | L141 の `make check` 構成説明 `ruff format` → `ruff format --check` に更新 |
| `tests/workflows/test_self_retry_cycle_membership.py`（新規） | 項目 3 の恒久回帰テスト（後述） |

対象外: 項目 2（→ #261）、`dev-thorough-fable.yaml` / `docs-fable.yaml`（未追跡・既に 1-step cycle 構成）、
`draft/lab/` 配下の歴史的記録、starter リポジトリ（#242 で修正済み）。

## 方針

最小侵襲で 3 項目を独立に修正する。リファクタは混在させない。

1. **項目 1**: SKILL.md の該当 4 行を文字列置換。実行コマンド（183）に加え、説明注記（179）・
   ABORT メッセージ内引用（203）・provider 注記（216）の整合を取る。検出 regex や fail-loud
   構造には触れない（それらは #261 のスコープ）
2. **項目 3**: `dev.yaml` の既存パターンをそのまま移植する。
   ```yaml
   # dev-local.yaml / docs-local.yaml の cycles に追加（dev.yaml:25-34 と同形）
   implementation:            # dev-local のみ
     entry: implement
     loop: [implement]
     max_iterations: 3
     on_exhaust: ABORT
   final-check:               # 両方
     entry: final-check
     loop: [final-check]
     max_iterations: 3
     on_exhaust: ABORT
   ```
   `close` step の `on` から `RETRY: close` 行を削除する
3. **項目 4**: 実装順序として、まず `ruff format --check` が現状素通しすることを再確認してから
   Makefile を変更する（2026-07-07 時点で「128 files already formatted」exit 0 を実測済み）。
   `format:` を `--check` 化し、`fmt:` を新設、`.PHONY` に `fmt` を追加、help に
   `make fmt` の行を追加し `make check` の説明行を非破壊 gate である旨に更新する。
   あわせて、`make check` 等価を謳う skill 側 gate（issue-implement Step 7a / issue-fix-code Step 3.1）の
   チェーンを `ruff format --check` に変更し、「整形差分で FAIL した場合は `make fmt` で整形し、
   差分をコミット対象に含めて再チェックする」旨を両 skill に追記する。証跡テンプレート内の
   gate 出力ラベルも `--check` 表記に整合させる（設計レビュー指摘 1 対応）
4. **TDD 順序**: 項目 3 は恒久回帰テストを先に書き、YAML 修正前に Red（dev-local / docs-local で
   violation 検出）→ 修正後に Green を証跡として残す

## テスト戦略

> 変更タイプが混在するため項目別に定義する。3 項目とも Python 実行時コード
> （`kaji_harness/`）の変更は無い。

### 変更タイプ

| 項目 | 変更タイプ |
|------|-----------|
| 1 | instruction-only（skill 指示文書の修正。docs-only 相当） |
| 3 | workflow YAML（runner が消費する設定 = 実行時の workflow 挙動を変える） |
| 4 | tooling-only（Makefile。metadata-only 相当） |

### 項目 3: 恒久回帰テスト（再現テスト必須 / bug 固有ルール）

`tests/workflows/test_self_retry_cycle_membership.py` を新規追加する（Small）。
既存の `tests/workflows/test_review_code_routing.py` / `test_workflow_set_invariants.py` と
同パターン（YAML パース + アサーションのみ、外部依存なし）。

**検証観点**:

- canonical workflow セット（`.kaji/wf/*.yaml`）の全 step について、
  `on.RETRY == 自 step id`（self-RETRY）を持つ step は、いずれかの cycle の `loop` に所属し、
  かつ `cycle.loop[-1]` がその step であること（`runner.py:860` の increment 条件と対になる不変条件）
- 検証対象 workflow が空でないこと（glob 誤りによる silent skip の防止。既存テストの慣行に倣う）

**Red→Green 遷移**: YAML 修正前に実行すると dev-local（implement / final-check / close）と
docs-local（final-check / close）の計 5 violation で FAIL、YAML 修正後（cycle 追加 + dead edge 除去）に
PASS することを実装フェーズの証跡とする。

**Medium / Large が不要な理由**: 検証対象は YAML の静的構造であり、ファイル I/O は
リポジトリ内 YAML の読み込みのみ（既存 `load_workflow` 経由）。runner の増分ロジック自体
（`runner.py:860`）は変更しないため、runner 結合の Medium テストを追加しても回帰検出情報が増えない。
外部 API 疎通は無関係のため Large も不要。

### 項目 1: 変更固有検証（恒久テストなし）

- 実測証跡: `kaji issue view <n> --json comments | jq '.comments | length'` が GitHub provider で
  exit 0 かつ整数を返す（Issue 完了条件。2026-07-07 に issue 259 で実測済み、実装フェーズで再取得）
- `grep -n -- '--output json' .claude/skills/issue-design/SKILL.md` の出現ゼロを確認

**恒久テストを追加しない理由**（testing-convention の 4 条件）:

1. 独自ロジックの追加・変更を含まない（指示文書内のコマンド文字列修正のみ）
2. 想定される不具合パターン（リンク切れ・参照不整合）は既存の `make verify-docs`
   （`.claude/skills/` を対象に含む）で捕捉済み。コマンドの実行時妥当性は agent 実行経由でしか
   検証できず、既存 pytest ゲートの守備範囲外
3. SKILL.md の文字列を grep する pytest を追加しても、#261 で BACK 検出機構自体が
   marker 方式に置き換わる予定のため回帰検出情報がほとんど増えない
4. 以上の理由を本設計書に記録し、レビュー可能にしている

### 項目 4: 変更固有検証（恒久テストなし）

- 事前確認: `ruff format --check kaji_harness/ tests/` が exit 0（2026-07-07 実測済み:
  「128 files already formatted」。実装フェーズ冒頭で再確認し、`--check` 化による既存未整形
  ファイルでの check 事故を防ぐ）
- 非破壊性の確認: `make format` 実行後に `git status --porcelain` の差分がゼロであること
- `make fmt` が整形を実行すること（意図的に整形崩れを作った一時ファイルで確認、確認後に破棄）
- `make check` が全体通過すること（共通完了条件）
- `make help` の出力に `fmt` 行が含まれ、`check` の説明が更新されていること
- gate 用途の mutating `ruff format` の残存ゼロ確認:
  `grep -rn 'ruff format' .claude/skills/ Makefile docs/dev/` の出力で、gate として実行される行が
  すべて `--check` 付きであること（`make fmt` の定義行と、整形手段としての `ruff format` 言及は除く）

**恒久テストを追加しない理由**（testing-convention の 4 条件)):

1. Makefile target は宣言的 tooling で独自ロジックを含まない
2. `make check` 自体がコミット前ゲートとして常時実行されるため、target の破損は日常運用で即検出される
3. pytest から `make` を起動する回帰テストは環境依存（make / ruff の PATH 前提）が強く、
   回帰検出情報がほとんど増えない
4. 以上の理由を本設計書に記録し、レビュー可能にしている

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし。「後方互換レイヤを提供しない」ADR 008 は #261 のスコープ |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/workflow-authoring.md | **あり** | 項目 3 再発防止:「self-RETRY を持つ step は cycle 所属必須（loop 末尾）」制約を「サイクル定義」節に追記 |
| docs/dev/testing-convention.md | **あり** | 項目 4: L211 の `make check` 相当構成の記述を `ruff format --check` に更新 |
| docs/dev/development_workflow.md | **あり** | 項目 4: L141 の品質ゲート構成説明を更新 |
| docs/reference/ | なし | `python-style.md:12` の「すべて make check 経由で機械的に判定」は `--check` 化後も真のまま（整合確認のみ） |
| docs/cli-guides/ | なし | `kaji issue view` の CLI 仕様自体は変更しない（skill 側の呼び出し方の修正） |
| AGENTS.md / CLAUDE.md | なし | `make check` の呼称のみで内部構成に言及していない（整合確認のみ） |
| .claude/skills/（issue-implement / issue-fix-code） | **あり** | 項目 4: `make check` 等価の品質チェックチェーンを `ruff format --check` 化し、証跡テンプレート表記を整合（設計レビュー指摘 1 対応。詳細は「変更スコープ」） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #259 本文 | https://github.com/apokamo/kaji/issues/259 | 修正方式の確定事項（`--json comments` 化 / dev.yaml パターン整合 / `fmt` 分離 / 互換レイヤなし）。設計フェーズで再検討しない |
| #259 検討経緯コメント | https://github.com/apokamo/kaji/issues/259#issuecomment-4893841743 | 全 4 項目の main 現存の実測・コード確認エビデンス。local provider の gh 互換 `--json` 実装確認を含む |
| gh CLI マニュアル | https://cli.github.com/manual/gh_issue_view | `gh issue view` の JSON 出力は `--json <fields>` 形式。`--output` フラグは存在しない |
| ruff formatter ドキュメント | https://docs.astral.sh/ruff/formatter/ | `ruff format --check` は「Avoid writing any formatted files back; instead, exit with a non-zero status code if any files would have been modified」（非破壊 gate としての利用が公式サポート） |
| 修正対象 skill | `.claude/skills/issue-design/SKILL.md:179,183,203,216` | `--output json` の全 4 出現箇所（2026-07-07 grep 実測） |
| runner の cycle enforce 実装 | `kaji_harness/runner.py:860` | `if cycle and current_step.id == cycle.loop[-1] and verdict.status == "RETRY"` — cycle 経由でのみ RETRY をカウントする（コード実読で確認） |
| 参照パターン | `.kaji/wf/dev.yaml:25-34` | `implementation` / `final-check` の 1-step cycle 構成（移植元） |
| 修正対象 YAML | `.kaji/wf/dev-local.yaml:70,115,124` / `.kaji/wf/docs-local.yaml:65,75` | cycle 未所属の self-RETRY edge の実在箇所（2026-07-07 grep 実測） |
| 修正対象 Makefile | `Makefile`（`format:` target） | `ruff format $(SOURCES)` が `--check` なしで `check` の依存に組み込まれている |
| 修正対象 skill gate | `.claude/skills/issue-implement/SKILL.md:239` / `.claude/skills/issue-fix-code/SKILL.md:114` | 「`make check` と等価」と明記した品質チェックチェーン内で mutating な `ruff format kaji_harness/ tests/` を直接実行している（2026-07-07 grep 実測）。比較: `issue-review-code/SKILL.md:118` は既に `--check` 付き |
| 既存テストパターン | `tests/workflows/test_workflow_set_invariants.py` / `tests/workflows/test_review_code_routing.py` | canonical workflow セットへの静的不変条件テストの先行例（Small / YAML パース + アサーション） |
| workflow 仕様 | `docs/dev/workflow-authoring.md`（サイクル定義節） | cycle の構造と「loop 末尾ステップの on.RETRY は loop 先頭を指す」既存制約。再発防止追記の挿入先 |
| 実測ログ（OB 再現） | 本設計セッション（2026-07-07） | `kaji issue view 259 --output json` → `unknown flag: --output` / `kaji issue view 259 --json comments \| jq '.comments \| length'` → exit 0 / `ruff format --check kaji_harness/ tests/` → 「128 files already formatted」exit 0 |
