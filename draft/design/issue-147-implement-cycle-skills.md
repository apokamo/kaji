# [設計] kamo2 移植 (4/6) implement cycle スキル全置換

Issue: #147 / 親: #143 / 依存: #144 / 並行: #145, #146

## 概要

実装サイクル 4 スキル（`issue-implement` / `issue-review-code` / `issue-fix-code` /
`issue-verify-code`）を kamo2 版に全置換する。kamo2 版は `_shared/implement-by-type/{feat,bug,refactor}.md`
への dispatch パターンを採用し、type 別実装戦略（feat: 標準 TDD / bug: 再現テスト
先行 / refactor: 計測 → safety net → 改修 → 再計測）を取り込む。同根欠陥の波及修正・
Red→Green 証跡・振る舞い非変更の保証などの観点は review-code の type 別追加観点
表として整合する。本 Issue では kamo2 固有の「backend / frontend / fullstack」
スタック分岐を Python 単一スタックに畳み、docs 参照を kaji 配置（#141 / #144 で
整備済）に再マップする。kaji 現行の Baseline Check 機構（pre-existing failure の
区別）と docs-only / metadata-only / packaging-only テスト戦略分岐は kaji 固有の
正当な進化として**温存**する。

## 背景・目的

- 親 Issue #143 の方針に基づく 6 分割移植の 4/6。
- #144 で `_shared/implement-by-type/{feat,bug,refactor}.md` が整備済み。`issue-implement`
  の Step 2.5 で type ラベル → dispatch する前提基盤が揃っている（#146 の `_shared/design-by-type/`
  と対称）。
- #146 で設計サイクル 4 スキルが kamo2 版に置換され、設計書側で type 別観点が定義
  されるようになった。本 Issue の review-code では設計書「インターフェース」「再現手順」
  「ベースライン計測」など、type 別設計セクションに対応する type 別追加観点（A〜F）
  を導入し、設計サイクル ↔ 実装サイクルの type 軸整合を完成させる。
- 親 Issue #143 の type 軸 4 canonical (`type:feature` / `type:bug` / `type:refactor`
  / `type:docs`) は #145 で確定済み。本 Issue の 4 スキルもこの type 軸と整合させる。
- kaji 現行の実装サイクル 4 スキルは type 分岐を持たず、共通手順だけで TDD を回す。
  bug 修正の再現テスト先行や refactor の振る舞い非変更保証は、現行ではレビュワーの
  暗黙ノウハウに依存している。kamo2 版へ置換することで、type 別ノウハウが手順
  として明文化され、レビュー判定にも組み込まれる。
- 本 Issue は「実装サイクル 4 スキル」の責務のみ。final-check / PR / doc 系スキルは
  後続子 Issue（#148 以降）の責務として触らない。

## インターフェース

本 Issue の成果物はスキル定義ファイル群（Markdown）のみ。Python 実行時 IF は
存在しない。論理的 IF は以下。

### 入力（移植元）

- kamo2 リポジトリ:
  - `/home/aki/dev/kamo2/.claude/skills/issue-implement/SKILL.md` (378 行)
  - `/home/aki/dev/kamo2/.claude/skills/issue-review-code/SKILL.md` (303 行)
  - `/home/aki/dev/kamo2/.claude/skills/issue-fix-code/SKILL.md` (190 行)
  - `/home/aki/dev/kamo2/.claude/skills/issue-verify-code/SKILL.md` (220 行)

### 出力（最終配置）

```
.claude/skills/
├── issue-implement/SKILL.md      (全置換: kamo2 版 + kaji 適応)
├── issue-review-code/SKILL.md    (全置換: kamo2 版 + kaji 適応)
├── issue-fix-code/SKILL.md       (全置換: kamo2 版 + kaji 適応)
└── issue-verify-code/SKILL.md    (全置換: kamo2 版 + kaji 適応)
```

`.agents/skills/` 配下の symlink は 4 スキル分すべて既存のため**追加・変更不要**。

### 使用例（置換後の誘導フロー）

```text
/issue-review-design [APPROVE]
  ↓
/issue-implement <issue-number>
  ├─ type ラベル数 ≥ 2 → ABORT（/issue-review-ready へ差し戻し）
  ├─ type ラベル未付与 → ABORT（/issue-review-ready へ差し戻し）
  ├─ type:docs → ABORT（/i-doc-update へ誘導）
  ├─ type:feature / canonical 外 → _shared/implement-by-type/feat.md を Read（標準 TDD）
  ├─ type:bug → _shared/implement-by-type/bug.md を Read（再現テスト先行）
  └─ type:refactor → _shared/implement-by-type/refactor.md を Read（計測 → safety net → 改修 → 再計測）
  ↓
/issue-review-code <issue-number>
  ├─ APPROVE → /i-dev-final-check
  └─ Changes Requested → /issue-fix-code → /issue-verify-code
                          ↑                       │
                          └── Changes ────────────┘
```

## 制約・前提条件

### スコープ厳守

- final-check / PR / doc 系スキル（`i-dev-final-check`, `i-pr`, `i-doc-*`,
  `issue-doc-check` 等）の SKILL.md は変更しない（後続子 Issue #148 以降の責務）。
  これらのスキルは旧来の誘導文言を持つ状態で許容する。

### 本 PR 内の参照整合は完結させる

- 4 スキル間の相互参照（`/issue-implement` → `/issue-review-code` →
  `/issue-fix-code` → `/issue-verify-code`）は本 PR 内で完結する。
- 外部誘導先（`/issue-design`, `/issue-review-design`, `/issue-review-ready`,
  `/i-dev-final-check`, `/i-doc-update`, `/i-doc-review`）はすべて既存スキル
  （#145 / #146 で整備済）として参照可能。

### type 軸の取り扱い（cardinality チェック必須）

`/issue-design` 側（#146）と同じく、type ラベルは Issue ごとに 1 つに限定する。

- `gh issue view [issue-number] --json labels --jq '[.labels[].name] | map(select(startswith("type:")))'`
  を**配列として**取得し、cardinality チェックを先行。
- **配列要素数 ≥ 2** → 複数 type ラベル付与。実装フェーズに入らず ABORT し、
  `/issue-review-ready` への差し戻しを案内する（type ラベルは 1 つに限定する責務）。
- **配列空** → type ラベル未付与。実装フェーズに入らず ABORT し、`/issue-review-ready`
  への差し戻しを案内する（前段レディネスで type ラベル付与を確保する責務）。
- **配列要素数 1** → その要素を採用。

#### type 値による分岐

| type | dispatch 先 | 手順の特徴 |
|------|-------------|-----------|
| `type:feature` | `_shared/implement-by-type/feat.md` | 標準 TDD（Red → Green → Refactor）。IF 定義とユースケースを契約として実装 |
| `type:bug` | `_shared/implement-by-type/bug.md` | 再現テスト先行。Red = 再現テストが OB を再現 / Green = EB に合致 |
| `type:refactor` | `_shared/implement-by-type/refactor.md` | ベースライン計測 → safety net → 改修 → 再計測。振る舞い非変更が絶対要件 |
| `type:docs` | ABORT（`/i-doc-update` へ誘導） | 本スキル対象外 |
| canonical 外 (`type:test` / `type:chore` / `type:perf` / `type:security` 等) | `_shared/implement-by-type/feat.md`（フォールバック） | 標準 TDD を適用 |

`issue-fix-code` / `issue-verify-code` では type 別 dispatch を行わない（修正は
レビュー指摘ベースで進めるため）。`issue-review-code` は type 別追加観点表として
取り込む（後述）。

### Python 単一スタック化（kamo2 固有要素の完全除去）

kamo2 の `backend / frontend / fullstack` の Scope 分岐を Python 単一に畳む。
対象 4 スキルディレクトリに、以下のパターンが残ってはならない：

- `apps/api`, `apps/web`, `apps/*`
- `verify-backend`, `verify-frontend`, `gate-backend`, `gate-frontend`,
  `check-all`, `FE_E2E`
- `backend` / `frontend` / `fullstack` の Scope 分岐記述
- `Scope: backend / frontend / fullstack` といった設計書 Scope 行への参照
- `docs/reference/backend/`, `docs/reference/frontend/`, `docs/howto/backend/`
- vitest / Playwright / ESLint / React BP への参照
- kamo2 固有 Issue 番号（`#542`, `#948` 等）
- worktree プレフィックス `kamo2-`

#### 参照先再マップ

| kamo2 参照 | kaji での対応 |
|-----------|--------------|
| `make verify-backend` / `make gate-backend` | `make check` |
| `docs/reference/backend/testing-convention.md` | `docs/dev/testing-convention.md` |
| `docs/reference/backend/testing-size-guide.md` | `docs/reference/testing-size-guide.md` |
| `docs/reference/backend/coding-standards-comprehensive.md` / `python-style.md` | `docs/reference/python/python-style.md` ほか分割済ファイル群 (`naming-conventions.md` / `type-hints.md` / `docstring-style.md` / `error-handling.md` / `logging.md`) |
| `docs/reference/frontend/*` | **削除**（kaji に対応物なし） |
| `docs/howto/backend/run-tests.md` | `CLAUDE.md` の Essential Commands 節案内に置換 |
| `development_workflow.md` 参照 | #144 のリネーム済み名称（`docs/dev/development_workflow.md`） |

### kaji 固有進化の温存

以下の kaji 現行進化は**置換後も維持する**。kamo2 版にこれらが無いのは kamo2 が
未対応なだけで、kaji の方が機能的に進んでいる箇所。

#### Baseline Check 機構（issue-implement / issue-review-code / issue-verify-code）

- kaji 現行の `issue-implement` Step 2.5 は、実装開始前に pytest を実行し、
  pre-existing な FAILED/ERROR を Baseline Check 結果として Issue コメントに記録する。
  以降の pytest 実行で `(nodeid, kind, error_type)` 三タプルで baseline と比較し、
  新規 FAILED/ERROR のみを regression として扱う。
- 停止基準（baseline failure が本 Issue の対象モジュールに影響する / 失敗数 10 件超）も
  kaji 現行どおり維持。
- `issue-review-code` / `issue-verify-code` 側でも Baseline Check コメントを参照して
  regression 判定する仕組みは維持する。
- kamo2 版にはこの機構が無いため、置換時に Baseline Check 関連ステップ
  （Step 2.5、Step 4 / 7b の合否判定、review-code Step 1.5 の判定、verify-code Step 1.3 の参照）を
  **kamo2 ベース構造に上書き再注入**する。

#### 品質チェックの 7a / 7b 分離（issue-implement）

- kaji 現行は「Step 7a: ruff check + ruff format + mypy（exit 0 必須）」と
  「Step 7b: pytest（baseline 判定可、`&&` チェーンに含めない）」を分離している
  （`fix: separate pytest from && chain to support baseline failure judgment for #76`
  に由来）。
- kamo2 版は単一の `make verify-backend` / `make gate-backend` で済ませているが、
  kaji の baseline 判定では pytest を分離する必要があるため、**7a / 7b 分離は維持**。
- ただし 7a 部分は CLAUDE.md の Essential Commands に「`make check` 同等の個別
  コマンド」として記載されており、`source .venv/bin/activate && ruff check ... && ruff
  format ... && mypy ...` で表現する。

#### docs-only / metadata-only / packaging-only テスト戦略分岐（issue-implement /
issue-review-code）

- kaji 現行 `issue-implement` Step 3 / Step 7b、`issue-review-code` Step 2 の
  「変更タイプに応じた検証チェック」は `docs/dev/testing-convention.md` 準拠の
  kaji 固有進化（#76 起点）。
- kamo2 版にはこの分岐が無く、Small / Medium / Large 単一構成で記述されているが、
  本置換では kaji 既存の変更タイプ分岐構造を**温存**する。
- 具体的には、kamo2 版テンプレートの「バックエンドテスト」節を、kaji 現行の
  「実行時コード変更の場合 / docs-only ・metadata-only ・packaging-only の場合」
  分岐にマップする。

### review-code の type 別追加観点（kamo2 から取り込み）

kamo2 版 `issue-review-code` Step 2 の type 別追加観点表（A〜F）を取り込む。
ただし Scope 軸を持たないため、表中の Scope 関連列・行は削除し、type 軸のみで
構成する。

| 観点 | feat | bug | refactor | docs |
|------|:----:|:---:|:--------:|:----:|
| A. **IF 契約の忠実性** | ✅ | — | — | — |
| B. **再現テストの存在と Red→Green の証跡** | — | ✅ | — | — |
| C. **同根欠陥の波及修正** | — | ✅ | — | — |
| D. **振る舞い非変更の保証** | — | — | ✅ | — |
| E. **改善指標の達成** | — | — | ✅ | — |
| F. **Scope 混在禁止**（type の責任範囲を超える変更が混入していないか） | ✅ | ✅ | ✅ | — |

`type=docs` の扱い: docs-only review は `/i-doc-review` が正本。本スキルに来るのは
誤経路 → `/i-doc-review` への差し戻しを検討する旨を補助情報として記述。

### 一次情報アクセス

- 移植元 kamo2 はローカルパス `/home/aki/dev/kamo2/` でレビュワー（agent）が
  アクセス可能。
- kaji 現行スキルは `/home/aki/dev/kaji/.claude/skills/issue-{implement,review-code,fix-code,verify-code}/SKILL.md`
  でアクセス可能。
- `_shared/implement-by-type/{feat,bug,refactor}.md` は #144 で kaji にマージ済み。

### canonical `type:*` ラベル整備は本 Issue のスコープ外

#146 と同様、kaji リポジトリの canonical `type:*` ラベル整備自体は本 Issue の
スコープ外。`/issue-create` 実行時のラベル作成は `issue-create` スキル側の責務。
手動試行で必要になった場合は `gh label create --force` で冪等作成する。

## 方針

### フェーズ 1: `issue-implement` 置換

1. `.claude/skills/issue-implement/SKILL.md` を kamo2 版ベースで全置換。
2. **kaji 適応**:
   - 「ワークフロー内の位置」を kaji 現行（`design → review-design → implement →
     review-code → i-dev-final-check → i-pr → close`）に合わせる。
   - 「前提知識の読み込み」: 設計書の Scope 行参照を削除。kaji 共通文書（開発ワーク
     フロー / テスト規約）+ 必要に応じた `docs/reference/python/*` を読み込む構成に。
   - **Baseline Check 機構（Step 2.5）** を kamo2 ベース構造に再注入（kaji 現行
     からそのまま移植）。
   - **type 判定（Step 2.5 後半 or 新 Step 2.6）** を導入: cardinality チェック →
     dispatch。
   - **Step 3〜5（Red / Green / Refactor）** は kamo2 版同様、type 別ガイドが
     上書きする「枠組み」として記述。Scope 分岐削除。
   - **Step 6（docs 更新）** は kaji 既存どおり「影響ドキュメント」セクション参照。
   - **Step 7（品質チェック）** は kaji の 7a / 7b 分離を維持。`make verify-backend`
     / `make gate-backend` への参照は削除し、`make check` 同等の個別コマンドで記述。
   - **Step 7.5（完了条件の段階確認）** は kamo2 版にあるため取り込み（#146 の
     `issue-design` でも採用済の構造）。
   - **Step 8（コミット）/ Step 9（Issue コメント）** は kaji 現行ベース。Scope 別
     テスト結果表（vitest / Playwright / FE 関連）は削除し、Python 単一の表に統合。
3. テスト戦略は kaji 既存の「実行時コード変更 / docs-only / metadata-only /
   packaging-only」分岐を維持。

### フェーズ 2: `issue-review-code` 置換

1. `.claude/skills/issue-review-code/SKILL.md` を kamo2 版ベースで全置換。
2. **kaji 適応**:
   - 「前提知識の読み込み」: Scope 行参照削除。kaji 共通文書のみ。
   - **Step 1.5（独立テスト実行）** は kamo2 版コメント `（#542 必須）` を削除し、
     kaji 現行の Baseline Check 参照ロジック（最新の `## Baseline Check 結果`
     コメント検索 → `(nodeid, kind, error_type)` 比較）を再注入。
   - 品質チェックコマンドは `make check` 同等の個別コマンド（`ruff check ... &&
     ruff format --check ... && mypy ...` + 個別 `pytest`）で記述。
   - **Step 2（コードレビュー）**:
     - kamo2 版の type 取得 + cardinality チェック（≥2 / 0 で `/issue-review-ready`
       差し戻し）を取り込み。
     - type 別追加観点表（A〜F、Scope 軸列を削除した上記版）を取り込み。
     - 共通観点 1〜4 は kaji 現行ベース + kamo2 の「変更タイプに応じた検証チェック」
       4 項目を維持。
     - Scope 混在禁止（観点 F）は kaji の「実行時コード変更 / docs-only / metadata-only
       / packaging-only 混在禁止」と整合させる。
   - **Step 2.5（完了条件の段階確認）** を取り込み。
   - **Step 3（コメント投稿）**: kamo2 の Scope 照合表を削除（Python 単一のため
     不要）。type 別追加観点の判定結果を含むコメント構成に。
3. ワークフロー位置を kaji 現行（`implement → review-code → (fix → verify) →
   i-dev-final-check → i-pr → close`）に合わせる。

### フェーズ 3: `issue-fix-code` 置換

1. `.claude/skills/issue-fix-code/SKILL.md` を kamo2 版ベースで全置換。
2. **kaji 適応**:
   - 「前提知識の読み込み」: `docs/reference/backend/*` 参照を `docs/reference/python/python-style.md`
     + `docs/dev/testing-convention.md` に再マップ。
   - **Step 3.2（品質チェック）**: `make verify-backend` への参照を `CLAUDE.md`
     の Essential Commands 節案内 +`make check` 同等個別コマンドに置換。
   - 構造（Step 1〜6）は kamo2 ベースのまま維持。
3. ワークフロー位置・誘導先を kaji 現行に合わせる（次は `/issue-verify-code`）。

### フェーズ 4: `issue-verify-code` 置換

1. `.claude/skills/issue-verify-code/SKILL.md` を kamo2 版ベースで全置換。
2. **kaji 適応**:
   - 「前提知識の読み込み」: `docs/reference/backend/*` 参照を kaji の正規パスに再マップ。
   - **Step 1.3（Baseline Check 参照）** を kaji 現行から再注入（kamo2 版には無い）。
     `pytest` の `&&` チェーン非含有指針も併記。
   - 構造（Step 1〜4）は kamo2 ベースを維持。
   - **新規発見事項の記録**は kamo2 / kaji 両者にある（収束保証のため判定外）。kamo2 版の
     重要度別対応案内（高: ブロッカー級は `/issue-review-code` やり直し検討、
     中: 別 Issue 起票、低: 記録のみ）を取り込む。
3. ワークフロー位置・誘導先を kaji 現行に合わせる（次は `/i-dev-final-check`）。

### フェーズ 5: 全体検証

#### 5.1 コマンドラインによる kamo2 残骸検出（grep ゼロ確認）

```bash
cd /home/aki/dev/kaji-refactor-147 && \
  grep -rEn '(verify-backend|gate-backend|verify-frontend|gate-frontend|FE_E2E|check-all)' .claude/skills/issue-implement/ .claude/skills/issue-review-code/ .claude/skills/issue-fix-code/ .claude/skills/issue-verify-code/

cd /home/aki/dev/kaji-refactor-147 && \
  grep -rEn '(apps/api|apps/web|apps/\*)' .claude/skills/issue-implement/ .claude/skills/issue-review-code/ .claude/skills/issue-fix-code/ .claude/skills/issue-verify-code/

cd /home/aki/dev/kaji-refactor-147 && \
  grep -rEn '(backend|frontend|fullstack|Scope[: ])' .claude/skills/issue-implement/ .claude/skills/issue-review-code/ .claude/skills/issue-fix-code/ .claude/skills/issue-verify-code/

cd /home/aki/dev/kaji-refactor-147 && \
  grep -rEn '(docs/reference/backend|docs/reference/frontend|docs/howto/backend)' .claude/skills/issue-implement/ .claude/skills/issue-review-code/ .claude/skills/issue-fix-code/ .claude/skills/issue-verify-code/

cd /home/aki/dev/kaji-refactor-147 && \
  grep -rEn '#542|#948|kamo2-' .claude/skills/issue-implement/ .claude/skills/issue-review-code/ .claude/skills/issue-fix-code/ .claude/skills/issue-verify-code/
```

すべて 0 件であること。`Scope[: ]` の grep は文脈を確認した上で許容判定（GitHub
Scope の文脈で出る場合のみ許容、設計書 Scope 行への参照は禁止）。

#### 5.2 ダングリング参照チェック

差分中の docs パス・スラッシュコマンド参照が全て実在することを確認:

```bash
cd /home/aki/dev/kaji-refactor-147 && \
  grep -rEoh 'docs/[a-z/_-]+\.md' .claude/skills/issue-implement/ .claude/skills/issue-review-code/ .claude/skills/issue-fix-code/ .claude/skills/issue-verify-code/ | sort -u | xargs -I{} test -e {} && echo "ALL EXISTS"
```

すべての docs パスが存在することを確認。

#### 5.3 `make check` / `make verify-docs` 通過

```bash
cd /home/aki/dev/kaji-refactor-147 && source .venv/bin/activate && make check
cd /home/aki/dev/kaji-refactor-147 && source .venv/bin/activate && make verify-docs
```

#### 5.4 手動試行（既存 open Issue 利用）

feat / bug / refactor 各 1 件の既存 open Issue で `/issue-implement` →
`/issue-review-code` を完走し、TDD の Red→Green ログが Issue コメントに残ることを
確認。

- feat: type:feature 付与の小さい Issue（再実装が容易なもの）
- bug: type:bug 付与の Issue（再現テスト先行の確認）
- refactor: type:refactor 付与の Issue（ベースライン計測 → safety net → 改修 → 再計測の確認）

該当する open Issue が無い場合は手動試行をスキップし、その旨を最終チェックで明記する。

## テスト戦略

### 変更タイプ

**docs-only**（スキル定義 Markdown のみの変更。Python 実行時コードは変更なし）

ただし、Markdown 内の `make check` / `pytest` コマンド指示の記述変更が下流の手動
実行・harness 実行の挙動を変えうるため、運用上の整合検証は必須。

### docs-only としての変更固有検証

#### 5.1 grep 検証（kamo2 残骸ゼロ）

フェーズ 5.1 のコマンド群を実行し、すべて 0 件であることを確認する。

#### 5.2 ダングリング参照ゼロ

フェーズ 5.2 のコマンドで、差分内に出現する `docs/**.md` パス・スラッシュコマンド
名（`/issue-*`, `/i-doc-*`, `/i-dev-final-check`, `/i-pr`）がすべて実在することを
確認する。

#### 5.3 `make check` / `make verify-docs` 通過

フェーズ 5.3 のコマンドで両方とも exit 0 になることを確認する。`make verify-docs`
はリンク整合性、`make check` は ruff / format / mypy / pytest の通過を担保する。
本 Issue では Python コードに変更を入れないため、`make check` の `pytest` 部分は
既存テストの回帰がないことの確認用。

#### 5.4 手動試行（type 別ディスパッチの動作確認）

フェーズ 5.4 のとおり feat / bug / refactor の Issue で実装サイクルを完走し、
type 別 dispatch が想定どおり動作することを確認する。Red→Green ログが Issue
コメントに残ることを確認する。

### 恒久テストを追加しない理由

`docs/dev/testing-convention.md` の 4 条件に沿って:

1. **独自ロジックの追加・変更をほぼ含まない**: 本 Issue はスキル定義 Markdown の
   全置換であり、Python 実行時コード（`kaji_harness/`）の変更は無い。harness
   ロジックには手を入れない。
2. **想定される不具合パターンが既存テストまたは既存品質ゲートで捕捉済み**:
   - リンク整合性 → `make verify-docs` で捕捉
   - kamo2 固有要素の残骸 → 5.1 の grep で捕捉
   - スラッシュコマンド名のタイポ → 5.2 のダングリング参照チェックで捕捉
   - 手順自体の妥当性 → 5.4 の手動試行で捕捉
3. **新規テストを追加しても回帰検出情報がほとんど増えない**: スキル Markdown の
   テキスト構造をユニットテスト化しても、現実の運用で問題になるのは「文中の参照
   先パスが存在するか」「kamo2 固有要素が混入していないか」であり、これらは既存
   ゲート (`make verify-docs`) と本 Issue で実施する grep 検証で十分検出できる。
4. **テスト未追加の理由をレビュー可能な形で説明できる**: 本セクションがその説明。
   レビュー時に grep / verify-docs / 手動試行ログを確認することで、変更妥当性を
   外部から検証可能。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 技術選定の変更なし。kamo2 移植方針自体は #143 で確定済み |
| `docs/ARCHITECTURE.md` | なし | アーキテクチャ変更なし |
| `docs/dev/development_workflow.md` | なし | ワークフローのフェーズ構成は不変。本 Issue 範囲では参照名称も #144 で確定済の名称を使うため更新不要 |
| `docs/dev/testing-convention.md` | なし | テスト規約の内容は不変（kaji 既存進化を温存するだけ） |
| `docs/dev/workflow_completion_criteria.md` | なし | 完了条件の参照ルールは不変 |
| `docs/dev/documentation_update_criteria.md` | なし | docs 更新基準は不変 |
| `docs/reference/python/*` | なし | コーディング規約は #141 で整備済 |
| `docs/cli-guides/` | なし | CLI 仕様変更なし |
| `CLAUDE.md` | なし | プロジェクト規約の変更なし。Pre-Commit セクションへの誘導は維持 |
| `.claude/skills/_shared/implement-by-type/*.md` | なし | #144 で整備済。本 Issue では既存ファイルを参照するのみ |
| `.claude/skills/_shared/worktree-resolve.md` | なし | #144 で整備済。参照のみ |
| `.claude/skills/_shared/report-unrelated-issues.md` | なし | #144 で整備済。参照のみ |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 親 Issue #143 | GitHub Issue #143（kaji リポジトリ） | kamo2 移植 6 分割方針。本 Issue は 4/6 |
| 依存 Issue #144 | コミット `6dcd48b` (`feat: kamo2 移植 (1/6) _shared 全置換 + docs/dev 名称リネーム`) | `_shared/implement-by-type/{feat,bug,refactor}.md` 整備済。dispatch 先として参照 |
| 並行 Issue #146（先行 merge） | コミット `ed66db4` (`refactor: 設計サイクル 4 スキルを kamo2 版に全置換 (#146)`) | 設計サイクル 4 スキルが kamo2 版に置換済。type 軸の設計が implement サイクルと整合 |
| kamo2 移植元 issue-implement | `/home/aki/dev/kamo2/.claude/skills/issue-implement/SKILL.md` | type 別 dispatch パターン（Step 2.5）+ Scope 軸との直交。本 Issue で Scope 軸を畳み type 軸のみ採用 |
| kamo2 移植元 issue-review-code | `/home/aki/dev/kamo2/.claude/skills/issue-review-code/SKILL.md` | type 別追加観点表（A〜F）。Scope 軸列を削除した上で取り込む |
| kamo2 移植元 issue-fix-code | `/home/aki/dev/kamo2/.claude/skills/issue-fix-code/SKILL.md` | レビュー指摘への対応方針（Agree / Disagree-Discuss）と品質チェックの位置 |
| kamo2 移植元 issue-verify-code | `/home/aki/dev/kamo2/.claude/skills/issue-verify-code/SKILL.md` | 修正項目確認 + 反論検討 + 新規発見事項記録（収束保証） |
| kaji 現行 issue-implement | `/home/aki/dev/kaji/.claude/skills/issue-implement/SKILL.md` | Baseline Check 機構（Step 2.5）/ 7a-7b 品質チェック分離 / docs-only 分岐の温存元 |
| kaji 現行 issue-review-code | `/home/aki/dev/kaji/.claude/skills/issue-review-code/SKILL.md` | Baseline Check 参照ロジック（Step 1.5）の温存元 |
| kaji 現行 issue-verify-code | `/home/aki/dev/kaji/.claude/skills/issue-verify-code/SKILL.md` | Baseline Check 参照（Step 1.3）の温存元 |
| baseline failure 機構の起源 | コミット `61617ba` (`fix: separate pytest from && chain to support baseline failure judgment for #76`), `a413bca` (`fix: add baseline failure recording to implement/review/verify skills for #76`) | 7a / 7b 分離 + Baseline Check の根拠コミット |
| 開発ワークフロー定義 | `docs/dev/development_workflow.md` | ワークフロー全体図 + 各フェーズの責務。本 Issue で参照する正規ファイル名 |
| テスト規約 | `docs/dev/testing-convention.md` | docs-only / metadata-only / packaging-only 分岐の根拠 + 4 条件 |
| Python スタイル規約 | `docs/reference/python/python-style.md` | kamo2 の `docs/reference/backend/python-style.md` の再マップ先 |
| 共有スキルルール | `docs/dev/shared_skill_rules.md` | スキル間共通の運用ルール |
| 完了条件運用 | `docs/dev/workflow_completion_criteria.md` | Issue 本文「## 完了条件」セクションの段階確認方針 |
