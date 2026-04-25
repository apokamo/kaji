# [設計] kamo2 移植 (3/6) design cycle スキル全置換

Issue: #146 / 親: #143 / 依存: #144 / 並行: #145, #147

## 概要

設計サイクル 4 スキル（`issue-design` / `issue-review-design` / `issue-fix-design` /
`issue-verify-design`）を kamo2 版に全置換する。kamo2 版は `_shared/design-by-type/{feat,bug,refactor}.md`
への dispatch パターンを採用し、type 別観点（feat: ユースケース / bug: OB/EB /
refactor: 測定指標）が review-design 側でも整合する構造になっている。本 Issue では
kamo2 固有の「backend / frontend / fullstack」スタック分岐を Python 単一スタックに
畳み、docs 参照を kaji 配置（#141 / #144 で整備済）に再マップする。

## 背景・目的

- 親 Issue #143 の方針に基づく 6 分割移植の 3/6。
- #144 で `_shared/design-by-type/{feat,bug,refactor}.md` が整備済み。`issue-design`
  の Step 1.5 で type ラベル → dispatch する前提基盤が揃っている。
- #145 で `issue-create` の type→テンプレ dispatch、および `issue-review-ready` の
  type 軸 4 canonical (`type:feature` / `type:bug` / `type:refactor` / `type:docs`)
  が確定。本 Issue の 4 スキルもこの type 軸と整合させる。
- kaji 現行の設計サイクル 4 スキルは type 分岐を持たず、test 戦略も単一記述。
  kamo2 版へ置換することで、type 別観点（feat の使用例／bug の OB/EB／refactor の
  測定指標）がレビュー品質ゲートに反映される。
- 本 Issue は「設計サイクル 4 スキル」の責務のみ。implement / review-code /
  final-check / doc 系スキルは後続子 Issue (#147 以降) の責務として触らない。

## インターフェース

本 Issue の成果物はスキル定義ファイル群（Markdown）のみ。Python 実行時 IF は
存在しない。論理的 IF は以下。

### 入力（移植元）

- kamo2 リポジトリ:
  - `/home/aki/dev/kamo2/.claude/skills/issue-design/SKILL.md` (341 行)
  - `/home/aki/dev/kamo2/.claude/skills/issue-review-design/SKILL.md` (317 行)
  - `/home/aki/dev/kamo2/.claude/skills/issue-fix-design/SKILL.md` (201 行)
  - `/home/aki/dev/kamo2/.claude/skills/issue-verify-design/SKILL.md` (221 行)

### 出力（最終配置）

```
.claude/skills/
├── issue-design/SKILL.md         (全置換: kamo2 版 + kaji 適応)
├── issue-review-design/SKILL.md  (全置換: kamo2 版 + kaji 適応)
├── issue-fix-design/SKILL.md     (全置換: kamo2 版 + kaji 適応)
└── issue-verify-design/SKILL.md  (全置換: kamo2 版 + kaji 適応)
```

`.agents/skills/` の symlink は 4 スキル分すべて既存（#145 以前から存在）のため
**追加・変更不要**。

### 使用例（置換後の誘導フロー）

```text
/issue-start <issue-number>
  ↓
/issue-design <issue-number>
  ├─ type ラベル未付与 → ABORT（/issue-review-ready へ差し戻し）
  ├─ type:docs → ABORT（/i-doc-update へ誘導）
  ├─ type:feature / type:refactor / canonical 外 → _shared/design-by-type/feat.md or refactor.md を Read
  └─ type:bug → _shared/design-by-type/bug.md を Read
  ↓
/issue-review-design <issue-number>
  ├─ APPROVE → /issue-implement
  └─ Changes Requested → /issue-fix-design → /issue-verify-design
                          ↑                       │
                          └── Changes ────────────┘
```

## 制約・前提条件

- **スコープ厳守**: implement / review-code / final-check / doc 系スキルの SKILL.md
  は変更しない（後続子 Issue #147 以降の責務）。これらのスキルは旧来の誘導文言を
  持つ状態で許容する。
- **本 PR 内の参照整合は完結させる**: 4 スキル間の相互参照（`/issue-design` →
  `/issue-review-design` → `/issue-fix-design` → `/issue-verify-design`）は本 PR
  内で完結する。外部誘導先（`/issue-start`、`/issue-review-ready`、`/i-doc-update`、
  `/issue-implement`）はすべて既存スキル（#145 で導入済）として参照可能。
- **Python 単一スタック化**: kamo2 の `backend / frontend / fullstack` のスタック
  分岐を Python 単一に畳む。`issue-design` の設計書テンプレート「変更スコープ」節
  （kamo2 版 174-184 行）は削除し、テスト戦略は Scope 分岐なしで Small / Medium /
  Large の単一構成に統合する。
- **type 軸は canonical + フォールバック維持**: kamo2 と同じ規則を踏襲。
  - `type:feature` → `feat.md`
  - `type:bug` → `bug.md`
  - `type:refactor` → `refactor.md`
  - canonical 外 (`type:test` / `type:chore` / `type:perf` / `type:security`) → `feat.md`
  - `type:docs` → ABORT（`/i-doc-update` へ誘導）
  - type ラベル未付与 → ABORT（`/issue-review-ready` へ差し戻し）
- **review-design の type 別重み付けは維持**: kamo2 版 168-185 行の重み付け表と
  type 固有追加観点は維持。ただし表中の docs 列は `/i-doc-review` への差し戻しを
  促す補助情報のため記述を温存する（kaji でも `/i-doc-review` は存在）。
- **kamo2 固有要素の完全除去**: 対象 4 スキルディレクトリに、以下のパターンが
  残ってはならない：
  - `apps/api`, `apps/web`, `apps/*`
  - `verify-backend`, `verify-frontend`, `gate-backend`, `gate-frontend`, `FE_E2E`
  - `backend` / `frontend` / `fullstack` の Scope 分岐記述
  - `docs/reference/backend/`, `docs/reference/frontend/`, `docs/howto/backend/`
  - `docs/product/features/`
  - kamo2 固有 Issue 番号（`#948`, `#959`, `#971`, `#981` 等）
  - worktree プレフィックス `kamo2-`
- **参照先再マップ**:
  - `docs/reference/backend/testing-convention.md` → `docs/dev/testing-convention.md`
  - `docs/reference/backend/testing-size-guide.md` → `docs/reference/testing-size-guide.md`
  - `docs/reference/backend/coding-standards-comprehensive.md` / `python-style.md` →
    `docs/reference/python/python-style.md` ほか分割済ファイル
    (`docs/reference/python/{naming-conventions,type-hints,docstring-style,error-handling,logging}.md`)
    の該当するテーマ一致ファイル
  - `docs/reference/frontend/*` 参照は**削除**（kaji に対応物なし）
  - `docs/howto/backend/run-tests.md` への参照は `CLAUDE.md` の Essential Commands
    節案内に置換
  - `development_workflow.md` 参照は #144 のリネーム済み名称を使用（kaji では
    `docs/dev/development_workflow.md` が正規名）
- **一次情報アクセス**: 移植元 kamo2 はローカルパス `/home/aki/dev/kamo2/` で
  レビュワー（agent）がアクセス可能。
- **canonical `type:*` ラベル整備は本 Issue のスコープ外**: `gh label list`
  で確認した結果、kaji リポジトリには canonical `type:docs` のみ存在し、
  `type:feature` / `type:bug` / `type:refactor` 等はラベル自体が未作成。
  これは #145 で `issue-create` の type→ラベル dispatch（type→`type:*`）が
  導入された際の残課題であり、本 Issue では label 整備自体は行わない。
  `/issue-create` 実行時に該当ラベルが未作成だった場合の挙動は `issue-create`
  スキル側の責務であり、本 Issue の scope では扱わない。手動試行セットアップで
  必要になった場合は、`gh label create --force` で冪等作成する（フェーズ 5.6
  参照）。
- **kaji 現行 issue-design の docs-only / metadata-only / packaging-only 対応は
  温存**: kaji 現行テンプレートは変更タイプ（実行時コード変更 / docs-only /
  metadata-only / packaging-only）分岐を持つ（`docs/dev/testing-convention.md` 準拠）。
  kamo2 版は Scope 分岐（backend / frontend / fullstack）はあるが変更タイプ分岐は
  ない。kaji 固有の変更タイプ分岐構造は kaji 側の正当な進化であり、**置換後も
  維持する**。具体的には、kamo2 版テンプレートの「バックエンド / フロントエンド
  テスト」節を、kaji 現行の「実行時コード変更の場合 / docs-only ・metadata-only ・
  packaging-only の場合」分岐にマップする。

## 方針

### フェーズ 1: `issue-design` 置換

1. `.claude/skills/issue-design/SKILL.md` を kamo2 版ベースで全置換
2. 置換時の機械置換・構造変更:
   - **Step 1.5（type 判定と dispatch）**: kamo2 版 101-124 行をそのまま移植。
     `_shared/design-by-type/{feat,bug,refactor}.md` への Read 指示を維持。
     `#948` 等の kamo2 Issue 番号は「フォールバック規則」等の一般文言に置換。
   - **前提知識読み込み**（kamo2 版 42-62 行）: Scope 別分岐を削除し、以下の
     単一構成に置換:
     - **共通（常に読み込む）**:
       1. 開発ワークフロー: `docs/dev/development_workflow.md`
       2. テスト規約: `docs/dev/testing-convention.md`
       3. コーディング規約: `docs/reference/python/python-style.md`
        （必要に応じて naming-conventions.md / type-hints.md / docstring-style.md /
        error-handling.md / logging.md を追加読込）
   - **設計書テンプレート**（kamo2 版 136-245 行）:
     - 「変更スコープ」節（174-184 行）を**削除**
     - 「テスト戦略」節（189-222 行）を kaji 現行テンプレートの「変更タイプ」分岐
       構造（`docs/dev/testing-convention.md` 準拠）に置換:
       - 変更タイプを先に宣言（実行時コード変更 / docs-only / metadata-only /
         packaging-only）
       - 実行時コード変更: Small / Medium / Large テスト観点
       - docs-only / metadata-only / packaging-only: 変更固有検証 + 恒久テストを
         追加しない理由（4 条件）
     - 「影響ドキュメント」節（224-235 行）: 参照先を kaji 配置に置換
       （`docs/reference/` は既に存在、`docs/howto/` は kaji に存在しないため削除、
       `docs/adr/` / `docs/dev/` / `CLAUDE.md` は維持、`docs/ARCHITECTURE.md` /
       `docs/cli-guides/` を追加）
   - **Step 2.5（完了条件の段階確認）**: kamo2 版 247-255 行を維持しつつ、kaji
     現行の「必須セクション存在確認 / 内容妥当性確認」の段階確認リスト
     （kaji 現行 208-224 行）を統合する。両者は矛盾せず、kaji 版の方が具体的な
     ため kaji 版を採用する。
   - **Step 4 Issue コメント**: kamo2 版 263-298 行を維持。完了条件の段階確認結果
     を含める形は維持。
   - **`make verify-backend` / `make gate-backend` 等の具体例** → `make check` に統一
   - **kamo2 Issue 番号除去**
3. Verdict 出力形式（kamo2 版 319-341 行）を維持。

### フェーズ 2: `issue-review-design` 置換

1. `.claude/skills/issue-review-design/SKILL.md` を kamo2 版ベースで全置換
2. 置換時の機械置換・構造変更:
   - **前提知識読み込み**（kamo2 版 52-58 行）: Scope 別分岐がない単一構成に置換
     （kamo2 版は元から単一構成だが、参照先を kaji 配置に再マップ）:
     1. テスト規約: `docs/dev/testing-convention.md`
     2. コーディング規約: `docs/reference/python/python-style.md` 他
     3. 開発ワークフロー: `docs/dev/development_workflow.md`
   - **Step 1.5（一次情報の Gate Check）**: kamo2 版 71-131 行をそのまま移植
     （Scope 分岐なし、再マップ不要）
   - **Step 2 の type 別重み付け表**（kamo2 版 154-185 行）: そのまま移植。
     - `#948` の kamo2 固有 Issue 番号はフォールバック規則の一般文言に置換
     - type 列の docs は `/i-doc-review` への差し戻しを示す補助情報として温存
   - **Step 2 の検証可能性観点**（kamo2 版 206-215 行）: 参照先を再マップ:
     - `docs/reference/backend/testing-convention.md` → `docs/dev/testing-convention.md`
     - §2 / §2.2 / §2.3 の節番号参照は kaji の `docs/dev/testing-convention.md`
       の実節構成に合わせて調整する（kaji 側は節番号制を採っていないため、
       「テスト戦略の原則」「変更タイプごとの期待値」等のセクション名参照に置換）
   - **Step 2.5 / Step 3 Issue コメント**: kamo2 版 220-273 行を維持
   - **Verdict 出力形式**（kamo2 版 294-317 行）: 維持

### フェーズ 3: `issue-fix-design` 置換

1. `.claude/skills/issue-fix-design/SKILL.md` を kamo2 版ベースで全置換
2. 置換時の機械置換:
   - **前提知識読み込み**（kamo2 版 53-54 行）:
     - `docs/reference/backend/testing-convention.md` → `docs/dev/testing-convention.md`
     - `docs/reference/backend/coding-standards-comprehensive.md` → `docs/reference/python/python-style.md` 他
   - kamo2 Issue 番号除去
   - 他の本体ロジックは Scope 分岐を含まないため、機械置換のみで完了

### フェーズ 4: `issue-verify-design` 置換

1. `.claude/skills/issue-verify-design/SKILL.md` を kamo2 版ベースで全置換
2. 置換時の機械置換: フェーズ 3 と同様の docs 参照再マップのみ
   - `docs/reference/backend/testing-convention.md` → `docs/dev/testing-convention.md`
   - `docs/reference/backend/coding-standards-comprehensive.md` → `docs/reference/python/python-style.md` 他
   - kamo2 Issue 番号除去

### フェーズ 5: 検証

1. `make check` 通過（Python コード変更ゼロのため baseline 確認）
2. `make verify-docs` 通過（新規追加した docs 参照リンクがすべて実在）
3. **kamo2 固有パターン残骸ゼロ**:
   ```
   rg 'apps/api|apps/web|verify-backend|verify-frontend|gate-backend|gate-frontend|FE_E2E|docs/reference/backend|docs/reference/frontend|docs/howto/backend|docs/product/features|kamo2-' \
     .claude/skills/{issue-design,issue-review-design,issue-fix-design,issue-verify-design}/
   rg '#948|#959|#971|#981' \
     .claude/skills/{issue-design,issue-review-design,issue-fix-design,issue-verify-design}/
   ```
   いずれも空であること。
4. **ダングリング参照ゼロ**: 4 スキル内で新規に言及される `docs/**` / `/issue-*` /
   `/i-*` 参照がすべて実在する。
   ```
   rg -oN '(docs/[A-Za-z0-9_/.-]+\.md|/(issue-[a-z-]+|i-[a-z-]+|pr-[a-z-]+))' \
     .claude/skills/{issue-design,issue-review-design,issue-fix-design,issue-verify-design}/
   ```
   抽出されたパスに対応する実ファイル／スキルディレクトリが存在することを確認。
5. **`_shared/design-by-type/` dispatch 成立**: `issue-design` Step 1.5 の
   dispatch 表が指す `feat.md` / `bug.md` / `refactor.md` が
   `.claude/skills/_shared/design-by-type/` 下に存在することを確認
   （#144 で整備済）。
6. **手動試行**（セットアップ付き end-to-end 確認）:

   **前提**: canonical `type:feature` / `type:bug` / `type:refactor` ラベルが
   GitHub リポジトリに存在し、各 type のテスト対象 Issue が 1 件以上存在すること。

   **現状（2026-04-24 時点の確認結果）**: `gh label list --limit 100` の結果、
   kaji リポジトリの canonical `type:*` ラベルは `type:docs` のみ。
   `type:feature` / `type:bug` / `type:refactor` はラベル自体が未作成。open
   Issue の canonical type 付与も `#149 type:docs` のみで、`type:feature` /
   `type:bug` / `type:refactor` 付き open Issue は 0 件
   （`gh issue list --state open --limit 100 --json number,labels` で確認）。
   従って手動試行は以下の **セットアップ**（実装フェーズ内で実施）を経ずに
   成立しない。

   **セットアップ手順**:

   ```bash
   # (A) canonical type:* ラベルを冪等作成（--force で既存時上書き）
   gh label create type:feature --description "新機能追加"        --color 0e8a16 --force
   gh label create type:bug     --description "バグ修正"          --color d93f0b --force
   gh label create type:refactor --description "リファクタリング" --color fbca04 --force

   # (B) 検証用 scratch Issue を 3 件作成
   # or 既存の bug / refactoring ラベル付き open Issue（#137, #139, #147, #148 等）に
   #    一時的に canonical ラベルを attach する
   #    （例: gh issue edit 139 --add-label type:bug）
   ```

   **実行**:

   - feat 経路: `type:feature` 付き Issue に対し `/issue-design` を実行し、Step 1.5
     が `_shared/design-by-type/feat.md` を Read することを確認
   - bug 経路: `type:bug` 付き Issue で同様に実行し、`bug.md` Read を確認
   - refactor 経路: `type:refactor` 付き Issue で同様に実行し、`refactor.md`
     Read を確認
   - 各経路とも `/issue-review-design` を連続実行し、type 別重み付け表
     （feat: 使用例観点強調 / bug: OB/EB 観点強調 / refactor: 測定指標観点強調）が
     review コメントに反映されることを目視確認

   **クリーンアップ**:

   - scratch Issue 方式: `gh issue close <n>` で全てクローズ（scratch と分かる
     タイトル・本文にすること）
   - 既存 Issue への一時付与方式: `gh issue edit <n> --remove-label type:<...>`
     で一時ラベルを除去

   **手動試行を省略する場合の代替根拠**:

   フェーズ 5.1〜5.5 の静的検証（`make check` / `make verify-docs` / kamo2 残骸
   grep / ダングリング参照検証 / dispatch 先ファイル実在）が全通過し、かつ
   `issue-design` SKILL.md 内の dispatch 表と `_shared/design-by-type/` の
   ファイル群の対応が目視で一致していれば、dispatch の正当性は保証される。
   手動試行は追加の end-to-end 信頼性確認であり、セットアップ労力（ラベル作成 +
   scratch issue 準備 + クリーンアップ）は本 Issue のスコープ外要素
   （canonical `type:*` ラベル運用の整備）に依存する。手動試行を省略する場合は、
   設計書／PR コメントに代替根拠（静的検証の通過と dispatch 表の目視確認結果）
   を明記する。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ

**docs-only**（`.claude/skills/` 配下の SKILL.md Markdown の置換のみ。Python
実行時コードは一切触らない。新規ディレクトリ・symlink 追加もない）

### 変更固有検証

- **`make check`**: 実行時コード変更はないが、baseline として既存品質ゲート通過を
  確認
- **`make verify-docs`**: 4 スキル内の docs 参照（`docs/dev/testing-convention.md`,
  `docs/reference/python/*.md`, `docs/dev/development_workflow.md` 等）がリンク
  切れしていないことを保証
- **kamo2 残骸 grep（2 種）**: 上記「フェーズ 5」の 2 本の rg コマンドが空
- **ダングリング参照検証**: 4 スキル内で新規に言及される docs パスおよびスキル名
  がすべて実在することを grep で確認
- **`_shared/design-by-type/` 整合**: `issue-design` Step 1.5 の dispatch 先
  (`feat.md` / `bug.md` / `refactor.md`) が `.claude/skills/_shared/design-by-type/`
  下に実在することを `ls` で確認
- **手動試行（条件付き・省略可）**: canonical `type:*` ラベルが GitHub リポジトリ
  に整備されている前提で、`/issue-design` → `/issue-review-design` を feat / bug /
  refactor 各 1 件完走させる。現状 `type:feature` / `type:bug` / `type:refactor`
  はラベル自体が未作成のため、フェーズ 5.6 のセットアップ手順（ラベル冪等作成 +
  scratch Issue 準備 or 既存 Issue への一時付与）を経て実行する。セットアップ
  労力が不釣合いな場合は、静的検証（上記 5 項目）通過と dispatch 表の目視確認
  をもって代替根拠とする（フェーズ 5.6 の「手動試行を省略する場合の代替根拠」
  に従う）

### 恒久テストを追加しない理由

[docs/dev/testing-convention.md](../../docs/dev/testing-convention.md) の 4 条件に従う：

1. **独自ロジックの追加・変更を含まない**: 追加物は Markdown（スキル定義）のみ。
   Python コード変更ゼロ。
2. **想定不具合パターンが既存ゲートで捕捉可能**:
   - リンク切れ → `make verify-docs`
   - Python 品質 → `make check`
   - スキルファイル配置不備 → kaji harness のロード時警告
   - kamo2 残骸 → フェーズ 5 の grep 検証（PR レビュー時に実行）
3. **新規テストで回帰検出情報が増えない**: 「スキル SKILL.md に kamo2 残骸が
   含まれないか」を pytest 化しても、本 PR 単発の一時チェックに過ぎず、将来の
   drift 検出価値がほぼない（親 #143 の完了時に一括 grep 検証される）。
4. **レビュー可能な形で説明**: 本セクションに 4 条件を明示。

## 影響ドキュメント

本 Issue は設計サイクル 4 スキルの置換に限定される。設計サイクル自体のフロー
（design → review-design → fix-design → verify-design → implement）は kamo2 移植
前後で同一であり、workflow docs への反映は不要。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | アーキテクチャ決定なし（#143 方針に沿う分割実装のみ） |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/development_workflow.md | なし | 設計サイクル内のフローは kamo2 移植前後で同一。#145 で既に `/issue-review-ready` 導入済 |
| docs/dev/workflow_overview.md | なし | 設計サイクル内のフローは変化なし |
| docs/dev/workflow_guide.md | **なし（後続 Issue に委譲）** | 各スキルの責務・実行順の詳細解説ドキュメントは、implement/review-code/final-check/doc 系スキル置換（後続子 Issue #147〜）完了後に一括更新する方が整合しやすい。最終子 Issue (#6) の責務 |
| docs/dev/shared_skill_rules.md | なし | `_shared/` は #144 で整備済。設計サイクル置換で共通規約は変わらない |
| docs/dev/skill-authoring.md | なし | スキル記述規約変更なし |
| docs/dev/testing-convention.md | なし | テスト規約側は変更しない。4 スキルからの参照先として利用されるのみ |
| docs/dev/workflow_completion_criteria.md | **なし（後続 Issue に委譲）** | 完了条件ドキュメントは各スキル差し替え完了後に一括更新する方が整合しやすい。最終子 Issue (#6) の責務 |
| docs/dev/documentation_update_criteria.md | なし | docs 更新基準は変わらない |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| docs/reference/python/ | なし | 参照先として利用されるのみ、コンテンツ変更なし |
| docs/rfc/github-labels-standardization.md | なし | ラベル体系変更なし（type 軸は #145 で確定した 4 canonical を継承） |
| CLAUDE.md | なし | 規約変更なし |
| workflows/*.yaml | なし | ワークフロー YAML は `/issue-design`〜`/i-pr` を呼び出しており、スキル名自体は変わらないため YAML 側の変更不要 |
| .claude/skills/_shared/design-by-type/ | なし | #144 で整備済、本 Issue では参照するのみ |
| .claude/skills/{implement,review-code,final-check,doc}* | **なし（意図的、後続 Issue に委譲）** | 本 Issue のスコープ外。後続子 Issue #147 以降の責務 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 親 Issue #143 | `gh issue view 143` | 6 分割移植の方針・各子 Issue の責務定義 |
| Issue #146 本体 | `gh issue view 146` | 完了条件として design cycle 4 スキル置換、変換ルール共通適用、検証 5 項目を規定 |
| 先行 Issue #144 | `gh issue view 144`, 6dcd48b | `_shared/design-by-type/{feat,bug,refactor}.md` が整備済み（dispatch 先が実在する根拠） |
| 先行 Issue #145 | `gh issue view 145`, a9f1b87 | type 軸 4 canonical (`type:feature` / `type:bug` / `type:refactor` / `type:docs`) と canonical 外フォールバックの規則が確定 |
| kamo2 `issue-design` | `/home/aki/dev/kamo2/.claude/skills/issue-design/SKILL.md` (341 行) | Step 1.5 type 判定 dispatch、設計書テンプレート構成の移植元。Scope 分岐（174-184 行）と Scope 別テスト戦略（189-222 行）が除去対象 |
| kamo2 `issue-review-design` | `/home/aki/dev/kamo2/.claude/skills/issue-review-design/SKILL.md` (317 行) | 一次情報 Gate Check、type 別重み付け表（154-185 行）、5 つの汎用レビュー観点の移植元 |
| kamo2 `issue-fix-design` | `/home/aki/dev/kamo2/.claude/skills/issue-fix-design/SKILL.md` (201 行) | 設計修正フローの移植元。docs 参照再マップのみで他はそのまま |
| kamo2 `issue-verify-design` | `/home/aki/dev/kamo2/.claude/skills/issue-verify-design/SKILL.md` (221 行) | 設計修正検証フローの移植元。docs 参照再マップのみで他はそのまま |
| kaji 現行 `issue-design` | [.claude/skills/issue-design/SKILL.md](../../.claude/skills/issue-design/SKILL.md) (285 行) | 置換前の現行実装。「変更タイプ」分岐（実行時コード変更 / docs-only / metadata-only / packaging-only）は kaji 固有の正当な進化として温存する根拠 |
| kaji テスト規約 | [docs/dev/testing-convention.md](../../docs/dev/testing-convention.md) | docs-only で恒久テストを省略できる 4 条件、変更タイプ分岐構造の根拠。kamo2 版の「§2 / §2.2 / §2.3」節番号参照は kaji では「テスト戦略の原則」「変更タイプごとの期待値」等の節名参照に置換する根拠 |
| kaji Python 参照 docs | [docs/reference/python/](../../docs/reference/python/) | kamo2 の `docs/reference/backend/coding-standards-*` 参照の置換先（#141 で移植済。`python-style.md` / `naming-conventions.md` / `type-hints.md` / `docstring-style.md` / `error-handling.md` / `logging.md` の 6 ファイル） |
| kaji `_shared/design-by-type/` | [.claude/skills/_shared/design-by-type/](../../.claude/skills/_shared/design-by-type/) | #144 で整備済。`feat.md` / `bug.md` / `refactor.md` の 3 ファイルが存在することを確認済（dispatch 先の実在根拠） |
| kaji 現行 `_shared/` | [.claude/skills/_shared/](../../.claude/skills/_shared/) | `worktree-resolve.md` / `report-unrelated-issues.md` / `promote-design.md` / `design-by-type/` / `implement-by-type/` が整備済 |
