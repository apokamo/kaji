# [設計] kamo2 移植 (1/6) _shared 全置換 + docs/dev 名称リネーム

Issue: #144 / 親: #143

## 概要

`.claude/skills/_shared/` を kamo2 版ベースに全置換し、`docs/dev/` 配下のワークフロー
ドキュメント 2 本をファイル名リネーム（中身未変更の `git mv`）する。本 Issue では
SKILL.md 本体は一切変更しない。後続子 Issue (#2〜#6) が SKILL.md を kamo2 版に置換する
際の参照先を、先行整備してダングリング参照の発生を防ぐ。

## 背景・目的

- kamo2 では `_shared/` 配下に type 別 dispatch (`design-by-type/`, `implement-by-type/`)
  が整備されており、後続子 Issue (#3, #4) の SKILL.md 置換はこれらの存在が前提。
- kaji 現行の `docs/dev/workflow_feature_development.md` / `workflow_docs_maintenance.md`
  は kamo2 では `development_workflow.md` / `docs_maintenance_workflow.md` に揃っており、
  子 Issue #5 での SKILL.md 置換時に参照先が揃っている必要がある。
- 基盤レイヤーを先行整備することで、子 Issue #2〜#6 をそれぞれ自己完結した PR にできる。

## インターフェース

本 Issue の成果物は「リポジトリ内ドキュメント / スキルリソースの配置」であり、
コード IF は存在しない。成果物の論理的 IF は以下：

### 入力

- kamo2 リポジトリ: `/home/aki/dev/kamo2/.claude/skills/_shared/`
- kamo2 リポジトリ: `/home/aki/dev/kamo2/docs/dev/`

### 出力（最終配置）

```
.claude/skills/_shared/
├── report-unrelated-issues.md              (変更なし: kamo2 と同一内容)
├── worktree-resolve.md                     (変更なし: kaji 現行は既に kaji- prefix 済)
├── promote-design.md                       (kaji 版書き下ろし: docs/adr/ 昇格のみ)
├── design-by-type/
│   ├── feat.md                             (新設)
│   ├── bug.md                              (新設)
│   └── refactor.md                         (新設)
└── implement-by-type/
    ├── feat.md                             (新設)
    ├── bug.md                              (新設)
    └── refactor.md                         (新設)

docs/dev/
├── development_workflow.md                 (旧: workflow_feature_development.md, git mv)
├── docs_maintenance_workflow.md            (旧: workflow_docs_maintenance.md,   git mv)
└── (上記以外は未変更)
```

### 使用例

本 Issue では SKILL.md を変更しないため、呼び出し側の差分はない。
後続子 Issue が下記のような参照を書けるようになる：

```markdown
# .claude/skills/issue-design/SKILL.md (子 Issue #3 で置換される)
type ラベルに応じ [_shared/design-by-type/feat.md](../_shared/design-by-type/feat.md) 等に dispatch する。
```

## 制約・前提条件

- **SKILL.md は変更しない**: 本 Issue のスコープ外。子 Issue #2〜#5 の責務。
- **中間状態の参照整合**: リネーム後も既存 SKILL.md 群（kaji 現行）からの参照が切れない
  よう、リネーム対象の旧名称を grep で全拾いして一斉置換する。
- **kamo2 固有要素の完全除去**: 以下のパターンが `_shared/` 配下に残らない：
  - `apps/api`, `apps/web`
  - `verify-backend`, `verify-frontend`, `gate-backend`, `gate-frontend`
  - `docs/reference/backend/`, `docs/reference/frontend/`, `docs/howto/backend/`
  - `docs/product/features/`
  - kamo2 固有 Issue 番号 (`#948`, `#959`, `#971`, `#981` 等)
- **Python 単一スタック化**: kamo2 の Scope 分岐 (backend / frontend / fullstack) は
  Python 単一に畳み、品質ゲートは `make check` に統一。
- **参照先再マップ**: kamo2 の `docs/reference/backend/testing-convention.md` →
  kaji の `docs/dev/testing-convention.md` および `docs/reference/python/*` に置き換える。
- **promote-design 昇格先**: kamo2 の `docs/product/features/` 昇格フローは採用しない。
  kaji 現行の `promote-design.md` が規定する **`docs/adr/` または `docs/dev/` への昇格**
  の 2 経路を維持する。kaji には `docs/product/features/` ディレクトリが存在せず、
  既存の 2 経路を狭める正当化は現時点でないため、kamo2 固有の feature-key 解決フロー
  （front-matter / related_issues 付与）のみ不採用とし、既存の「いつ昇格するか」表の
  昇格先区分は維持する。

## 方針

### フェーズ 1: `_shared/` 全置換

1. **`report-unrelated-issues.md`**: kamo2 版と kaji 現行の内容が完全一致
   （diff 確認済み）のため、置換不要。現状維持。
2. **`worktree-resolve.md`**: kamo2 版を置換元としつつ、プレフィックス `kamo2-` →
   `kaji-` に変換する（実質的に kaji 現行と同一になる。diff 確認済み）。
3. **`promote-design.md`**: kaji 現行を baseline にしつつ、kamo2 の構造（手順の段階化）を
   参考に書き直す。**昇格先は kaji 現行と同じ `docs/adr/` または `docs/dev/` の 2 経路を
   維持**（kamo2 の `docs/product/features/` 昇格フローは kaji にディレクトリがないため
   不採用）。
   - 節構成: 「いつ昇格するか」（現行 kaji の 2 経路表を踏襲）、「昇格手順（kamo2 の
     5 ステップ構成を `docs/adr/` / `docs/dev/` 用に書き換え）」、「注意事項」
   - kamo2 の feature-key 解決ルール・front-matter テンプレート節は採用しない
     （kaji に `docs/product/features/` がないため）。
4. **`design-by-type/{feat,bug,refactor}.md` 新設**: kamo2 版をベースに以下の変換ルールを
   機械適用する：
   - Scope 宣言節を「Python 単一スタック」に置換（backend/frontend 分岐削除）
   - `make verify-*` / `make gate-*` / `vitest` / `Playwright` への言及を削除し、
     `make check` に統一
   - `docs/reference/backend/testing-convention.md` → `docs/dev/testing-convention.md`
   - `docs/reference/backend/*` のその他 → `docs/reference/python/*` に対応付け
     （該当ファイルが kaji に存在する場合のみ。なければ `docs/dev/` 系に寄せる）
   - `docs/howto/backend/`, `docs/product/features/`, `apps/api`, `apps/web` 参照を削除
   - kamo2 固有 Issue 番号 (`#948` 等) を除去
   - frontend / fullstack / uses_resource("filesystem") 等の固有マーカーを削除
5. **`implement-by-type/{feat,bug,refactor}.md` 新設**: 同上の変換ルールを適用。

### フェーズ 2: `docs/dev/` リネーム

1. `git mv docs/dev/workflow_feature_development.md docs/dev/development_workflow.md`
2. `git mv docs/dev/workflow_docs_maintenance.md   docs/dev/docs_maintenance_workflow.md`
3. 参照元を `rg -l` で全抽出し、一斉置換（`sed -i` or 個別 Edit）：
   ```
   rg -l 'workflow_feature_development|workflow_docs_maintenance' \
     .claude/skills/ docs/ CLAUDE.md README.md
   ```
   - `workflow_feature_development` → `development_workflow`
   - `workflow_docs_maintenance` → `docs_maintenance_workflow`
4. 現時点で判明している参照元（上記 rg コマンドによる事前調査結果・**網羅**）:
   - `docs/README.md`, `docs/dev/workflow_guide.md`, `docs/dev/workflow_overview.md`
   - `.claude/skills/{issue-fix-code, issue-review-design, issue-design, issue-fix-design,
     i-doc-update, issue-review-code, issue-doc-check, issue-implement}/SKILL.md`
5. **`draft/design/` 配下の過去設計書は置換対象外**（履歴文書の変更は行わない）。
   旧名称参照は履歴として残る。下記「テスト戦略」の旧名称 grep はこの履歴を除外した
   範囲で評価する。

### フェーズ 3: 検証

- `make check` 通過
- `make verify-docs` 通過（リンク切れなし）
- kamo2 固有パターン残骸ゼロ（下記の grep が空）：
  ```
  rg 'apps/api|apps/web|verify-backend|verify-frontend|gate-backend|gate-frontend|docs/reference/backend|docs/reference/frontend|docs/howto/backend|docs/product/features' .claude/skills/_shared/
  ```
- kamo2 固有 Issue 番号残骸ゼロ：
  ```
  rg '#948|#959|#971|#981' .claude/skills/_shared/
  ```
- ダングリング参照ゼロ: `_shared/` 配下から参照されている docs パスが全て実在

## テスト戦略

### 変更タイプ

**docs-only**（`.claude/skills/_shared/` 配下のガイドライン Markdown、および
`docs/dev/` のファイル名リネーム＋参照追従のみ。実行時 Python コードは一切触らない）

### 変更固有検証

- **`make check`**: 既存品質ゲート通過（実行時コード変更がないため影響しないはずだが、
  念のため baseline として実行）
- **`make verify-docs`**: リンク切れがないことを保証（docs/dev リネームに伴う参照切れの
  網羅検出はこれで担保）
- **kamo2 残骸 grep**: 上記「制約」の除去対象パターンが `_shared/` 配下にゼロ
- **ダングリング参照検証**: 新設ファイル（`_shared/promote-design.md`,
  `_shared/{design,implement}-by-type/*.md`）から参照している docs パスが全て実在：
  ```
  # 各 md ファイルから相対パス参照を抽出し、存在確認
  rg -o '\(\.\./\.\./\.\./[^)]+\)' .claude/skills/_shared/ | ...
  ```
- **旧名称参照ゼロ（現行参照範囲）**: リネーム後に下記コマンドが空であること：
  ```
  rg -n 'workflow_feature_development|workflow_docs_maintenance' \
    .claude/skills/ docs/ CLAUDE.md README.md
  ```
  **`draft/design/` は除外**（本設計書自身と過去設計書が旧名称を含むのは履歴文書として
  正当。これらは動作上の参照解決には関与しないため grep 対象外とする）。

### 恒久テストを追加しない理由

[docs/dev/testing-convention.md](../../docs/dev/testing-convention.md) の 4 条件に従い：

1. **独自ロジックの追加・変更を含まない**: Markdown ドキュメントと git mv のみ。
   Python コード変更ゼロ。
2. **想定不具合パターンが既存ゲートで捕捉可能**: リンク切れは `make verify-docs`、
   Markdown の文法は既存 CI、Python コード変更がないため `make check` で十分。
3. **新規テストで回帰検出情報が増えない**: 「`_shared/*.md` に kamo2 固有文字列が
   混入していないか」を pytest にしても、Issue 単発の一時チェックであり、将来の
   drift 検出価値がほぼない。子 Issue #2〜#6 完了後、親 Issue #143 の最終 grep で
   一括検証される。
4. **レビュー可能な形で説明**: 本セクションで 4 条件を明示。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | アーキテクチャ決定はなし（親 #143 の方針に従う実装分割のみ） |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/workflow_feature_development.md | **あり（リネーム）** | → `development_workflow.md` |
| docs/dev/workflow_docs_maintenance.md | **あり（リネーム）** | → `docs_maintenance_workflow.md` |
| docs/dev/workflow_guide.md | あり | 旧名参照の置換 |
| docs/dev/workflow_overview.md | あり | 旧名参照の置換 |
| docs/README.md | あり | 旧名参照の置換 |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | なし | 規約変更なし（参照 URL は現行でも `docs/dev/` 粒度のみ記載） |
| .claude/skills/*/SKILL.md | あり（参照のみ） | 旧名参照の置換のみ。スキル本体ロジックは未変更 |
| draft/design/ 過去分 | **なし** | 履歴文書のため置換対象外。旧名称は履歴として残す |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 親 Issue #143 | `gh issue view 143` | 「子 Issue 単位で参照整合を閉じた状態で段階マージする」「子1 = `_shared/` 全置換 + `docs/dev/*` 名称リネーム」 |
| Issue #144 本体 | `gh issue view 144` | 完了条件として `_shared/` 5 ファイル群の置換・新設ルール、docs/dev リネーム 2 本、3 種 grep 検証を規定 |
| kamo2 移植元 `_shared/` | `/home/aki/dev/kamo2/.claude/skills/_shared/` | `design-by-type/{feat,bug,refactor}.md`, `implement-by-type/{feat,bug,refactor}.md`, `promote-design.md`, `worktree-resolve.md`, `report-unrelated-issues.md` の現物（置換元） |
| kamo2 移植元 `docs/dev/` | `/home/aki/dev/kamo2/docs/dev/` | `development_workflow.md`, `docs_maintenance_workflow.md` の命名慣習の出典 |
| kaji テスト規約 | [docs/dev/testing-convention.md](../../docs/dev/testing-convention.md) | docs-only 変更で恒久テストを省略できる 4 条件 |
| kaji 現行 `promote-design.md` | [.claude/skills/_shared/promote-design.md](../../.claude/skills/_shared/promote-design.md) | 現行契約は「`docs/adr/` に ADR として」または「`docs/dev/` にガイドとして」の 2 経路。本設計ではこの 2 経路を維持し、kamo2 の `docs/product/features/` 昇格フローのみ不採用とする根拠 |
| kaji 現行 `worktree-resolve.md` | [.claude/skills/_shared/worktree-resolve.md](../../.claude/skills/_shared/worktree-resolve.md) | prefix がすでに `kaji-` である根拠（kamo2 版との差分は prefix のみ） |
