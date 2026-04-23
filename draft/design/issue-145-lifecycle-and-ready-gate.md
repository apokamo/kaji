# [設計] kamo2 移植 (2/6) lifecycle 系 + ready/PR gate 4 スキル新設

Issue: #145 / 親: #143 / 依存: #144

## 概要

ライフサイクル系 3 スキル（`issue-create` / `issue-start` / `issue-close`）を kamo2 版に
全置換し、新規 4 スキル（`issue-review-ready` / `issue-fix-ready` / `pr-fix` / `pr-verify`）
を新設する。Codex 併用のため `.agents/skills/` に新 4 スキル分の symlink を追加する。
本 PR merge 時点で、`issue-create` が誘導する次ステップ `/issue-review-ready` が
同一 PR 内に存在することを保証し、ダングリング参照を発生させない。

## 背景・目的

- 親 Issue #143 の方針に基づく 6 分割移植の 2/6。
- #144 で `_shared/` 基盤が揃ったことを前提に、ライフサイクル系（Issue の入口・着手・
  完了）を kamo2 の review-ready 7 観点ベースに差し替える。
- kamo2 では「Issue 本文の品質を着手前にゲート」する `issue-review-ready` /
  `issue-fix-ready` と、「PR レビュー後サイクル」の `pr-fix` / `pr-verify` が確立
  されており、kaji にも同等ゲートを導入する。
- `issue-create` は type 別テンプレート (`templates/issue-{feat,bug,refactor,docs}.md`)
  を Read して本文を組み立てる dispatch 方式。
- 本 Issue は「ライフサイクル系と ready/PR ゲート」の責務のみ。design/implement/review/
  final-check/doc 系スキルは後続子 Issue (#3〜#6) の責務として触らない。

## インターフェース

本 Issue の成果物はスキル定義ファイル群（Markdown）と symlink の配置であり、
Python 実行時 IF は存在しない。論理的 IF は以下。

### 入力（移植元）

- kamo2 リポジトリ: `/home/aki/dev/kamo2/.claude/skills/` 配下
  - `issue-create/SKILL.md` + `issue-create/templates/{issue-feat,issue-bug,issue-refactor,issue-docs}.md`
  - `issue-start/SKILL.md`
  - `issue-close/SKILL.md`
  - `issue-review-ready/SKILL.md`
  - `issue-fix-ready/SKILL.md`
  - `pr-fix/SKILL.md`
  - `pr-verify/SKILL.md`

### 出力（最終配置）

```
.claude/skills/
├── issue-create/
│   ├── SKILL.md                              (全置換: kamo2 版 + kaji 適応)
│   └── templates/
│       ├── issue-feat.md                     (新設)
│       ├── issue-bug.md                      (新設)
│       ├── issue-refactor.md                 (新設)
│       └── issue-docs.md                     (新設)
├── issue-start/SKILL.md                      (全置換: kamo2 版 + prefix kamo2- → kaji-)
├── issue-close/SKILL.md                      (全置換: kamo2 版 + prefix kamo2- → kaji-)
├── issue-review-ready/SKILL.md               (新設: kamo2 版 + kaji 適応)
├── issue-fix-ready/SKILL.md                  (新設: kamo2 版 + kaji 適応)
├── pr-fix/SKILL.md                           (新設: kamo2 版 + Scope 分岐除去)
└── pr-verify/SKILL.md                        (新設: kamo2 版 + Scope 分岐除去)

.agents/skills/
├── issue-review-ready → ../../.claude/skills/issue-review-ready
├── issue-fix-ready    → ../../.claude/skills/issue-fix-ready
├── pr-fix             → ../../.claude/skills/pr-fix
└── pr-verify          → ../../.claude/skills/pr-verify
```

### 使用例（置換後の誘導フロー）

```text
/issue-create "タイトル" feat
  ↓
/issue-review-ready <issue-number>
  ├─ APPROVE → /issue-start
  └─ RETRY  → /issue-fix-ready → /issue-review-ready …

(… 従来の design / implement / review / final-check / i-pr …)
  ↓ (PR merge 後、レビュー指摘あり)
/pr-fix <pr-number> → /pr-verify <pr-number>
  ↓
/issue-close <issue-number>
```

## 制約・前提条件

- **スコープ厳守**: design / implement / review / final-check / doc 系スキルの SKILL.md
  は変更しない（後続子 Issue #3〜#6 の責務）。これらの SKILL.md は旧来の誘導文言
  （`/issue-start` 直後に `/issue-design` を案内するなど）を持つ状態で許容する。
- **本 PR 内の参照整合は完結させる**: 本 PR で追加される `/issue-create` → `/issue-review-ready`、
  `/issue-review-ready` → `/issue-fix-ready`、`/pr-fix` ↔ `/pr-verify` の内部誘導は
  すべて同 PR 内の 7 スキルディレクトリ配置で解決する。
- **Python 単一スタック化**: kamo2 の `backend / frontend / fullstack` のスタック分岐は
  Python 単一に畳み、品質ゲートは `make check` に統一する。これは `issue-review-ready`
  の観点 #7「作業スコープの推定可能性」の **dev workflow 側評価材料**に対応する変更
  （kamo2 では「backend / frontend / fullstack の推定が可能」を判断材料として要求する
  が、kaji では「対象モジュール / ディレクトリ / ファイルパスの言及」に置き換える）。
  docs-only workflow 側の観点 #7 判断材料（docs パス / 文書カテゴリの言及）は kamo2
  と同一で温存する。
- **type 軸は 4 canonical 維持**: `issue-review-ready` の type 別追加観点は kamo2 と
  同じ **4 canonical (`type:feature` / `type:bug` / `type:refactor` / `type:docs`)**
  を維持する。canonical 外 (`type:test` / `type:chore` / `type:perf` / `type:security`)
  は feat フォールバック。`issue-create` の type → テンプレート dispatch も同じ
  4 テンプレ (`issue-{feat,bug,refactor,docs}.md`) で揃える。
- **kamo2 固有要素の完全除去**: 対象 6 スキルディレクトリおよび `templates/` 配下に、
  以下のパターンが残ってはならない：
  - `apps/api`, `apps/web`, `apps/*`
  - `verify-backend`, `verify-frontend`, `gate-backend`, `gate-frontend`, `FE_E2E`
  - `docs/reference/backend/`, `docs/reference/frontend/`, `docs/howto/backend/`
  - `docs/product/features/`
  - kamo2 固有 Issue 番号 (`#948`, `#959`, `#971`, `#981` 等)
  - worktree プレフィックス `kamo2-`
- **参照先再マップ**:
  - `apps/api` → `kaji_harness/`
  - `make verify-backend` / `make verify-frontend` / `make gate-*` → `make check`
  - `docs/reference/backend/testing-convention.md` → `docs/dev/testing-convention.md`
  - `docs/reference/backend/coding-standards-*.md` → `docs/reference/python/python-style.md`
    ほか `docs/reference/python/{naming-conventions,type-hints,docstring-style,error-handling,logging}.md`
    の該当する方（テーマ一致を優先、なければ `docs/dev/` 配下）
  - `docs/reference/frontend/*` 参照は**削除**（kaji に対応物なし）
- **参照が消える項目の扱い**: `docs/reference/frontend/*`, `docs/product/features/*`,
  `docs/howto/backend/*` への参照は削除する（対応物を kaji に持たないため）。削除した
  項目が review-ready 7 観点を毀損しないことを確認する（該当項目は観点 #7 の
  **dev workflow 側判断材料**の一部に過ぎず、Python 単一スタック化で「対象モジュール
  / ディレクトリ / ファイルパスの言及」に置き換えても #7 は成立する。type 軸は
  上記の通り 4 canonical を維持する）。
- **Codex 併用 symlink 仕様**: `.agents/skills/<name>` は `../../.claude/skills/<name>`
  への相対 symlink（既存 `.agents/skills/*` と同形式）。Codex がスキルをロード時に
  `failed to load skill` 警告が出ないこと。
- **一次情報アクセス**: 移植元 kamo2 はローカルパス `/home/aki/dev/kamo2/` で
  レビュワー（agent）がアクセス可能。公開 URL ではないため、レビュー時に参照する
  際はローカルパスから引用する。

## 方針

### フェーズ 1: `issue-create` 置換 + templates 新設

1. `.claude/skills/issue-create/SKILL.md` を kamo2 版で全置換
   - `make verify-backend` の具体例 → `make check` に書き換え
   - `#948`, `#959` 等の kamo2 固有 Issue 番号を**除去**（参照理由を本文に残す場合は
     「dispatch 方式の根拠」等の文言に一般化）
2. `.claude/skills/issue-create/templates/issue-{feat,bug,refactor,docs}.md` を新設
   - kamo2 版をベースに以下を機械置換：
     - `make verify-backend` / `make verify-frontend` / `make gate-*` → `make check`
     - `backend / frontend / fullstack` 分岐記述 → **Python 単一スタック**に縮約
       （`issue-refactor.md` の「Scope 明示」節は「対象モジュール/ディレクトリの
       明示」に改題し、混在禁止（docs / packaging / 実行時コードの混在禁止）の
       趣旨は温存）
     - `apps/api` / `apps/web` → `kaji_harness/` / `tests/` / `docs/`
     - `docs/reference/backend/testing-convention.md` → `docs/dev/testing-convention.md`
     - `docs/reference/backend/coding-standards-*` → `docs/reference/python/python-style.md`
       等の対応ファイル
     - `docs/reference/frontend/*` 言及 → 削除
     - kamo2 固有 Issue 番号 → 除去

### フェーズ 2: `issue-start` / `issue-close` 置換

1. `issue-start/SKILL.md` を kamo2 版で全置換
   - worktree パス `../kamo2-[prefix]-[issue-number]` → `../kaji-[prefix]-[issue-number]`
   - `make verify-backend / gate-backend` 具体例 → `make check`
   - `secrets/` symlink 節: kaji 現行に存在しない前提。**除去**する（kaji 現行の
     `issue-start` も `secrets/` を扱わない。存在しないディレクトリを前提にすると
     ユーザーが戸惑う）。
2. `issue-close/SKILL.md` を kamo2 版で全置換
   - worktree パス prefix を `kaji-` に書き換え
   - ブランチ安全削除フロー（merge-base 確認、stale ref クリーンアップ）は kamo2 版の
     ものをそのまま採用

### フェーズ 3: 新規 4 スキル新設

1. `issue-review-ready/SKILL.md`: kamo2 版をベースに以下を適応
   - docs 参照再マップ（上記「制約」の再マップ規則を機械適用）
   - type 軸は **4 canonical 維持** (`type:feature` / `type:bug` / `type:refactor` /
     `type:docs`)。canonical 外 (`type:test` / `type:chore` / `type:perf` /
     `type:security`) は feat にフォールバック（kamo2 と同規則）
   - 観点 #7「作業スコープの推定可能性」は 2-workflow 構造（dev / docs-only）を
     温存しつつ、dev workflow 側の判断材料を kamo2 の「backend / frontend /
     fullstack の推定が可能」から「対象モジュール / ディレクトリ / ファイルパスの
     言及」に置き換える。docs-only workflow 側は kamo2 と同一（docs パス / 文書
     カテゴリの言及）
   - kamo2 固有 Issue 番号除去
   - 誘導先: RETRY 時 `/issue-fix-ready`、APPROVE 時 `/issue-start`
2. `issue-fix-ready/SKILL.md`: kamo2 版をベースに docs 参照再マップと Issue 番号除去
3. `pr-fix/SKILL.md`: kamo2 版をベースに以下を適応
   - 「バックエンド / フロントエンド」節を**統合**し「対象スコープの事前確認」に改題
   - `make verify-backend` / `make verify-frontend` → `make check` に統一
   - `docs/reference/backend/*` → `docs/dev/testing-convention.md` + `docs/reference/python/*`
   - `docs/reference/frontend/*` 言及 → 削除
4. `pr-verify/SKILL.md`: 同上の適応

### フェーズ 4: `.agents/skills/` symlink 追加

```bash
cd .agents/skills
ln -s ../../.claude/skills/issue-review-ready issue-review-ready
ln -s ../../.claude/skills/issue-fix-ready    issue-fix-ready
ln -s ../../.claude/skills/pr-fix             pr-fix
ln -s ../../.claude/skills/pr-verify          pr-verify
```

既存 `.agents/skills/*` も `../../.claude/skills/<name>` 形式の相対 symlink なので
同形式で揃える。

### フェーズ 5: workflow docs の最小更新（ready gate 導入反映）

`issue-review-ready` は「全 workflow 共通」ゲートであり、導入後の運用文書導線と
食い違わないよう、本 Issue の責務として 3 ファイルを最小差分で更新する。

1. **`docs/dev/development_workflow.md`**:
   - Mermaid flow の先頭を `A["/issue-create"] --> B["/issue-start"]` から
     `A["/issue-create"] --> RR["/issue-review-ready"] --> B["/issue-start"]` に変更
   - RETRY 時のループ `/issue-fix-ready → /issue-review-ready` を追記
   - Phase 概要テーブルに「1.5 レディネスレビュー」行を追加
2. **`docs/dev/workflow_overview.md`**:
   - feature-development フロー (18-20 行) を
     `/issue-create → /issue-review-ready → /issue-start → ...` に変更
   - docs-maintenance フロー (30-31 行) を
     `/issue-review-ready → /issue-start → /i-doc-update → ...` に変更
     （docs-maintenance でも `/issue-create` が前提だが、kamo2 `workflow_overview.md`
     line 10 の記法を踏襲する）
3. **`docs/dev/docs_maintenance_workflow.md`**:
   - 「フロー概要」(7 行) を
     `issue-review-ready → issue-start → i-doc-update → ...` に変更
   - kamo2 `docs_maintenance_workflow.md` line 17 の形式と揃える

**更新時の約束**:
- design/implement/review/final-check 系スキル名・誘導文言は触らない
  （後続子 Issue #3〜#6 の責務）
- `pr-fix` / `pr-verify` の記述は追加しない（PR レビュー後の手動実行サイクルであり
  main workflow flow の一部ではない）
- 旧名称参照（例: 既に `development_workflow.md` へのリネームが #144 で完了して
  いる前提）のみを追加・修正する

### フェーズ 6: 検証

1. `make check` 通過
2. `make verify-docs` 通過（docs 参照リンク切れなし）
3. **ダングリング参照ゼロ**: 本 PR で追加した `/issue-*` / `/pr-*` 参照の全てに
   対応する `.claude/skills/<name>/` ディレクトリが存在することを grep で検証
   ```
   rg -oN '/(issue-[a-z-]+|pr-[a-z-]+)' .claude/skills/{issue-create,issue-start,issue-close,issue-review-ready,issue-fix-ready,pr-fix,pr-verify}/
   ```
   上記で抽出された全スキル名に対応する `.claude/skills/<name>/` が存在する
   （本 PR 外のスキル = 既存スキルも含めて充足しているか確認）
4. **kamo2 固有パターン残骸ゼロ**:
   ```
   rg 'apps/api|apps/web|verify-backend|verify-frontend|gate-backend|gate-frontend|FE_E2E|docs/reference/backend|docs/reference/frontend|docs/howto/backend|docs/product/features|kamo2-' \
     .claude/skills/{issue-create,issue-start,issue-close,issue-review-ready,issue-fix-ready,pr-fix,pr-verify}/
   rg '#948|#959|#971|#981' \
     .claude/skills/{issue-create,issue-start,issue-close,issue-review-ready,issue-fix-ready,pr-fix,pr-verify}/
   ```
   上記がいずれも空であること。
5. **workflow docs 整合**: `/issue-review-ready` の出現を以下で確認：
   ```
   rg '/issue-review-ready' docs/dev/development_workflow.md docs/dev/workflow_overview.md docs/dev/docs_maintenance_workflow.md
   ```
   3 ファイルすべてで出現し、`/issue-create` と `/issue-start` の間、または
   docs-maintenance のフロー先頭に位置することを目視確認。
6. **Codex 併用確認**: kaji harness または Codex 経由でスキルロード時に
   `failed to load skill` 警告が出ないこと（4 symlink が正しく解決する）。
7. **手動試行**: 既存 open Issue に対して `/issue-review-ready` を実行し、
   レビューコメントが Issue に投稿されるまで完走する。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ

**docs-only**（`.claude/skills/` 配下の SKILL.md / テンプレート Markdown の追加・置換、
および `.agents/skills/` への symlink 追加のみ。Python 実行時コードは一切触らない）

### 変更固有検証

- **`make check`**: 実行時コード変更はないが、baseline として既存品質ゲート通過を
  確認
- **`make verify-docs`**: スキル内の docs 参照 (`docs/dev/*`, `docs/reference/python/*`,
  `docs/rfc/*` 等) がリンク切れしていないことを保証
- **kamo2 残骸 grep（2 種）**: 上記「フェーズ 6」の 2 本の rg コマンドが空
- **ダングリング参照検証**: 本 PR 内で新規に追加された `/issue-*` / `/pr-*` 参照に
  対応する `.claude/skills/<name>/` が全て実在
- **symlink 解決確認**: `ls -L .agents/skills/{issue-review-ready,issue-fix-ready,pr-fix,pr-verify}`
  が `SKILL.md` を含む実体を返す
- **workflow docs 整合**: `docs/dev/{development_workflow,workflow_overview,docs_maintenance_workflow}.md`
  の 3 ファイルすべてに `/issue-review-ready` が `/issue-create` と `/issue-start`
  の間（または docs-maintenance のフロー先頭）に出現していること
- **手動試行**: 既存 open Issue で `/issue-review-ready` を実行し、レビューコメント
  投稿まで到達する（振る舞いの end-to-end 確認。恒久化はしない）

### 恒久テストを追加しない理由

[docs/dev/testing-convention.md](../../docs/dev/testing-convention.md) の 4 条件に従う：

1. **独自ロジックの追加・変更を含まない**: 追加物は Markdown（スキル定義）と
   symlink のみ。Python コード変更ゼロ。
2. **想定不具合パターンが既存ゲートで捕捉可能**:
   - リンク切れ → `make verify-docs`
   - Python 品質 → `make check`
   - スキルファイル配置不備 → Codex / kaji harness のロード時警告および
     `/issue-review-ready` の手動試行
3. **新規テストで回帰検出情報が増えない**: 「スキル SKILL.md に kamo2 残骸が
   含まれないか」を pytest 化しても、本 PR 単発の一時チェックに過ぎず、将来の
   drift 検出価値がほぼない（親 #143 の完了時に一括 grep 検証される）。
4. **レビュー可能な形で説明**: 本セクションに 4 条件を明示。

## 影響ドキュメント

`issue-review-ready` は kamo2 一次情報 (`/home/aki/dev/kamo2/.claude/skills/issue-review-ready/SKILL.md`
8 行目) で「**全 workflow 共通**」のゲートであり、dev / docs-only の両 workflow で
`create → review-ready → start` が要求される。kaji 現行 workflow docs はいずれも
この gate を含まない導線を記載しているため、merge 時点で導入 gate と運用文書の
導線を一致させる責務を本 Issue で負う。更新対象を「本 Issue で更新する」「後続
Issue に明示的に委譲する」の 2 区分に明示する。

| ドキュメント | 影響の有無 | 理由・更新内容 |
|-------------|-----------|------|
| docs/adr/ | なし | アーキテクチャ決定なし（#143 方針に沿う分割実装のみ） |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| **docs/dev/development_workflow.md** | **あり（本 Issue で更新）** | dev workflow の mermaid flow および Phase 概要に `/issue-review-ready` を `/issue-create` と `/issue-start` の間に追加。ready gate RETRY 時の `/issue-fix-ready` → `/issue-review-ready` 再ループも併記。kamo2 同ファイル (line 18) の形式に揃える |
| **docs/dev/workflow_overview.md** | **あり（本 Issue で更新）** | feature-development / docs-maintenance 両 workflow のフロー記述（18-20 行, 30-31 行）に `/issue-review-ready` を `/issue-start` の前に追加。kamo2 同ファイル (line 9-10) と同形式 |
| **docs/dev/docs_maintenance_workflow.md** | **あり（本 Issue で更新）** | フロー概要 (7 行) 冒頭に `/issue-review-ready` を追加し、ready gate が docs-only にも適用される旨を明記。kamo2 同ファイル (line 17) と同形式 |
| docs/dev/workflow_guide.md | **なし（後続 Issue に委譲）** | 本ファイルは各スキルの責務・実行順の詳細解説であり、design/implement/review 系スキルの差し替え（子 Issue #3〜#6）と同時更新する方が記述の一貫性を保てる。本 Issue では触らず、最終子 Issue (#6) の責務とする |
| docs/dev/shared_skill_rules.md | なし | `_shared/` は #144 で整備済み。ready gate 追加で共通規約は変わらない |
| docs/dev/skill-authoring.md | なし | スキル記述規約変更なし |
| docs/dev/workflow_completion_criteria.md | **なし（後続 Issue に委譲）** | 完了条件ドキュメントは各スキル差し替え完了後に一括更新する方が整合しやすい。最終子 Issue (#6) の責務 |
| docs/dev/documentation_update_criteria.md | なし | docs 更新基準は変わらない |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| docs/rfc/github-labels-standardization.md | なし | ラベル体系変更なし（`issue-create` の type→ラベル表は既存体系を継承） |
| CLAUDE.md | なし | 規約変更なし |
| workflows/*.yaml | なし | ワークフロー YAML は `/issue-design`〜`/i-pr` のみを呼び出しており、本 Issue で追加する `/issue-review-ready` 等は手動実行前提（kamo2 の運用と同じ） |
| .claude/skills/issue-design 以下の既存スキル | **なし（意図的、後続 Issue に委譲）** | 本 Issue のスコープ外。後続子 Issue #3〜#6 の責務。旧来の誘導文言（`/issue-start` 直後に `/issue-design` を案内など）は許容 |

### 更新粒度の注意

- `docs/dev/development_workflow.md` / `docs_maintenance_workflow.md` / `workflow_overview.md`
  の更新は **ready gate 導入に直接必要な最小差分のみ**。既存の design/implement/
  review/final-check 系スキル誘導文言は触らない。
- `pr-fix` / `pr-verify` は PR レビュー後の手動実行サイクルであり、main workflow flow
  の一部ではない。workflow docs への追記は行わず、各スキル SKILL.md 内の誘導
  （`pr-fix` ↔ `pr-verify`）でのみ参照整合を保つ。workflow docs への反映は、必要に
  応じて後続 Issue でまとめて検討する。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 親 Issue #143 | `gh issue view 143` | 6 分割移植の方針・各子 Issue の責務定義 |
| Issue #145 本体 | `gh issue view 145` | 完了条件として lifecycle 3 スキル置換、新 4 スキル新設、symlink 4 本、検証 6 項目を規定 |
| 先行 Issue #144 | `gh issue view 144`, 6dcd48b | `_shared/` 基盤（design-by-type / implement-by-type / promote-design）整備済み |
| kamo2 `issue-create` | `/home/aki/dev/kamo2/.claude/skills/issue-create/SKILL.md` (144 行) | review-ready 7 観点に整合する本文生成誘導、type 別テンプレ Read dispatch 方式 |
| kamo2 `issue-create/templates/` | `/home/aki/dev/kamo2/.claude/skills/issue-create/templates/issue-{feat,bug,refactor,docs}.md` | 本文構造・チェックポイントの移植元 |
| kamo2 `issue-start` | `/home/aki/dev/kamo2/.claude/skills/issue-start/SKILL.md` (153 行) | worktree 作成・Issue 本文メタ情報追記フロー。prefix `kamo2-` が書換対象 |
| kamo2 `issue-close` | `/home/aki/dev/kamo2/.claude/skills/issue-close/SKILL.md` (228 行) | merge + 安全ブランチ削除 + worktree 削除の責務構成 |
| kamo2 `issue-review-ready` | `/home/aki/dev/kamo2/.claude/skills/issue-review-ready/SKILL.md` (273 行) | 7 観点レビュー、canonical 外 type のフォールバック規則、Scope 推定観点 |
| kamo2 `issue-fix-ready` | `/home/aki/dev/kamo2/.claude/skills/issue-fix-ready/SKILL.md` (143 行) | RETRY 指摘への対応フロー |
| kamo2 `pr-fix` | `/home/aki/dev/kamo2/.claude/skills/pr-fix/SKILL.md` (203 行) | PR レビュー指摘対応フロー。Scope 分岐が除去対象 |
| kamo2 `pr-verify` | `/home/aki/dev/kamo2/.claude/skills/pr-verify/SKILL.md` (255 行) | pr-fix の verify 対。Scope 分岐が除去対象 |
| kaji テスト規約 | [docs/dev/testing-convention.md](../../docs/dev/testing-convention.md) | docs-only で恒久テストを省略できる 4 条件 |
| kaji Python 参照 docs | [docs/reference/python/](../../docs/reference/python/) | kamo2 の `docs/reference/backend/coding-standards-*` 参照の置換先（#141 で移植済み） |
| kaji 現行 `_shared/` | [.claude/skills/_shared/](../../.claude/skills/_shared/) | #144 で整備済。`worktree-resolve.md` の prefix は `kaji-` |
| kaji 既存 `.agents/skills/` | `.agents/skills/` (19 symlink) | symlink 形式 `../../.claude/skills/<name>` の既存パターン |
| kaji 現行 `issue-create` SKILL | [.claude/skills/issue-create/SKILL.md](../../.claude/skills/issue-create/SKILL.md) | 置換前の現行実装。置換後は review-ready ゲートが前段に挿入される差分を把握する根拠 |
