---
description: 設計ドキュメントに対し、汎用的なソフトウェア設計原則に基づいてレビューを行う。
name: issue-review-design
---

# Issue Review Design

> **重要**: このスキルは実装/設計を行ったセッションとは **別のセッション** で実行することを推奨します。
> 同一セッションで実行すると、実装時のバイアスがレビュー判断に影響する可能性があります。

実装フェーズに入る前に、設計ドキュメントの品質を検証します。
特定の実装詳細（How）に依存せず、要件（What）、制約（Constraints）、および利用者視点（UX）が明確に定義されているかを確認します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| 設計完了後、実装開始前 | ✅ 必須 |
| 仕様変更時の再レビュー | ⚠️ 推奨 |

**ワークフロー内の位置**: design → **review-design** → (fix → verify) → implement

## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_number` | int | GitHub Issue 番号 |
| `step_id` | str | 現在のステップ ID |

**条件付きで注入される変数:**

| 変数 | 型 | 条件 | 説明 |
|------|-----|------|------|
| `cycle_count` | int | サイクル内ステップのみ | 現在のイテレーション番号 |
| `max_iterations` | int | サイクル内ステップのみ | サイクルの上限回数 |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue-number>
```

### 解決ルール

コンテキスト変数 `issue_number` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_number` として使用。

## 前提知識の読み込み

以下のドキュメントを Read ツールで読み込んでから作業を開始すること。

1. **テスト規約**: `docs/dev/testing-convention.md`
2. **コーディング規約**: `docs/reference/python/python-style.md`
   - 必要に応じて `docs/reference/python/naming-conventions.md` /
     `type-hints.md` / `docstring-style.md` / `error-handling.md` /
     `logging.md` を追加読込
3. **開発ワークフロー**: `docs/dev/development_workflow.md`

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。以降のステップではこのパスを使用する。

### Step 1.5: 設計書の読み込みと一次情報の確認（Gate Check）

1. **設計書の読み込み**:
   ```bash
   cat [worktree-absolute-path]/draft/design/issue-[number]-*.md
   ```

2. **一次情報の記載を確認**:

設計書に以下が明記されているか確認：

- [ ] **参照した一次情報の一覧**（公式ドキュメント、RFC、API仕様書、ライブラリのソースコード等）
- [ ] **各一次情報へのURL/パス**（検証可能な形式）

#### 一次情報がない場合 → 早期リターン

設計書に一次情報の記載がない、または不十分な場合は、**レビュー本体に入らず**以下のコメントを投稿して終了：

```bash
gh issue comment [issue-number] --body-file - <<'EOF'
# 設計レビュー：一次情報の記載が必要

## 指摘事項

設計書に**一次情報（Primary Sources）の記載がありません**。

設計レビューを行うには、以下を設計書に追記してください：

### 必要な情報

1. **参照した一次情報の一覧**
   - 公式ドキュメント、RFC、API仕様書、ライブラリのソースコード等
   - URLまたはファイルパスを明記

2. **一次情報から得た根拠**
   - 設計判断の裏付けとなる情報を引用または要約

### 例

\`\`\`markdown
## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|------------------------|
| Python公式ドキュメント | https://docs.python.org/... | 「〜を使用することで...」（該当箇所の引用） |
| Pydantic 2 Migration | https://docs.pydantic.dev/... | 〜が推奨されている（要約） |
\`\`\`

## 判定

❌ **Changes Requested** - 一次情報を追記後、再度レビューを依頼してください。

### 次のステップ

\`/issue-fix-design [issue-number]\` で一次情報を追記
EOF
```

**この時点でレビュー終了。Step 2以降は実行しない。**

---

### Step 2: 設計レビュー（一次情報を参照）

一次情報が記載されている場合のみ、このステップに進みます。

**重要**: レビュー時は設計書の記述だけでなく、**一次情報を実際に参照**して整合性を確認してください。

#### レビュー時の一次情報活用

1. **一次情報を取得**: 設計書に記載されたURLにアクセス、またはファイルを読み込んで確認
2. **整合性チェック**: 設計内容が一次情報と矛盾していないか検証
3. **最新性チェック**: 一次情報が古くなっていないか（deprecatedなAPI等）確認

#### 一次情報にアクセスできない場合

一次情報にアクセスできない場合は **Changes Requested** として設計者に対応を求めてください。

対応方法は `issue-design` の「一次情報のアクセス可能性ルール」を参照：
- 公開URL → そのまま記載
- ログイン必須/有償 → ダウンロードしてリポジトリ配置、または該当箇所を引用
- 社内限定/NDA → 使用不可、公開版または引用で代替

#### type の取得と観点の重み付け

Issue ラベルから `type:*` ラベルを **配列として** 取得し、共通観点の重み付けを調整する:

```bash
gh issue view [issue-number] --json labels --jq '[.labels[].name] | map(select(startswith("type:")))'
```

**cardinality チェック（先に判定）**:

- **配列要素数が 2 以上** → 複数 type ラベルが付与されている。設計レビューに入らず、`/issue-review-ready` への差し戻しを Must Fix として投稿する（observation: 複数 type ラベル付与）。Changes Requested で停止する
- **配列が空** → type ラベル未付与。設計レビューに入らず、`/issue-review-ready` への差し戻しを Must Fix として投稿する（observation: type ラベル未付与）
- **配列要素数が 1** → その要素を採用し、以下の判定に進む

**type 値による分岐**:

1. **canonical（`type:feature` / `type:bug` / `type:refactor` / `type:docs`）** → 対応する重み付けを適用
2. **canonical 外（`type:test` / `type:chore` / `type:perf` / `type:security` など）** → `type:feature` と同等に扱う（フォールバック規則）

**type 別重み付け（共通観点 1〜5 に対する強調度）**:

| 観点 | feat | bug | refactor | docs |
|------|:----:|:---:|:--------:|:----:|
| 1. 抽象化と責務の分離 | **高**（IF 設計が中心） | 中 | **高**（責務再編が主題） | — |
| 2. インターフェース設計 | **高**（使用例・命名・idiom 必須） | 低（IF 原則不変） | 中（IF 不変の確認） | — |
| 3. 信頼性とエッジケース | 高（新規ロジックのエラー系） | **高**（OB/EB 整合・同根の漏れ） | 低（振る舞い非変更） | — |
| 4. 検証可能性（テスト戦略） | **高**（S/M/L 網羅） | **高**（再現テスト必須） | **高**（safety net・bridging test） | — |
| 5. 影響ドキュメント | 高 | 中 | 中 | **高**（docs-only なら主題） |

**type 固有の追加観点**:

- **feat**: 代替案の検討がある / ユースケースが具体的 / 使用例が実装前に提示されている
- **bug**: OB と EB が 1 次情報で裏付けられている / 再現手順が最小 / 根本原因が「なぜ」まで書かれている / 同根の他の壊れ箇所が調査済み
- **refactor**: ベースライン計測コマンドが明記 / 改善指標が測定可能 / 公開 IF 不変の宣言がある / safety net テストの方針が明確
- **docs**: type=docs は基本的に `/i-doc-review` の守備範囲。dev workflow の review-design に来るのは誤経路 → `/i-doc-update` への差し戻しを検討

> **重み付けの運用**: 上表で「高」にあたる観点で不足があれば Must Fix として CR 判定。「低」は Should Fix に留める。type に関係しない観点（共通）の指摘は従来通り Must / Should を内容に応じて判断する。

#### レビュー基準

以下の汎用的な原則に基づいてレビューしてください。

1. **抽象化と責務の分離 (Abstraction & Scope)**:
   - **What & Why**: 「何を作るか」と「なぜ作るか」が明確か？
   - **No Implementation Details**: 特定の言語やライブラリの内部実装（How）に過度に踏み込んでいないか？（疑似コードはOK）
   - **Constraints**: システムの制約条件（性能、セキュリティ、依存関係）が明記されているか？

2. **インターフェース設計 (Interface Design)**:
   - **Usage Sample**: 利用者が実際に使用する際のコード例やAPI定義が含まれているか？
   - **Idiomatic**: そのインターフェースは、対象言語やプラットフォームの慣習（Idioms）に適合しているか？
   - **Naming**: 直感的で意図が伝わる命名がなされているか？

3. **信頼性とエッジケース (Reliability)**:
   - **Source of Truth**: 一次情報の内容と設計が整合しているか？（コピペではなく理解に基づいているか）
   - **Error Handling**: 正常系だけでなく、異常系（エラー、境界値）の挙動が定義されているか？
   - **一次情報との乖離**: 一次情報に記載されているが設計で考慮されていない点はないか？

4. **検証可能性 (Testability)**:
   - テストケースの羅列ではなく、**「検証すべき観点」**が言語化されているか？
   - **変更タイプに応じたテスト戦略チェック（`docs/dev/testing-convention.md` 準拠）**:
     - [ ] 変更タイプ（実行時コード変更 / docs-only / metadata-only / packaging-only）が明示されているか
     - [ ] 実行時コード変更なら Small / Medium / Large の検証観点が定義されているか
     - [ ] 恒久テストを追加しない場合、その理由が「docs-only / metadata-only / packaging-only 変更」節の 4 条件に沿って示されているか
     - [ ] スキップ時のエビデンス（既存テスト名、外部保証、確認内容）が記載されているか
     - [ ] `uv pip install -e .` など副作用のある検証を行う場合、隔離方針が明記されているか
   - **原則として新規テストを要求する変更**（`testing-convention.md` の「実行時の振る舞いを変える変更」節参照）: ドメインロジック追加・条件分岐追加・データ変換仕様変更・API 契約変更・過去障害の再発防止。これらに該当する変更でテストを省略している場合は Changes Requested
   - **不正当な省略理由**（「省略してはいけない理由」節に該当する場合は Changes Requested）:
     - 「実行時間が長い」「Large テストはステージングで検証」など → サンプル期間等で短縮可能 / CI で再現できる構成にすること
     - 「API キーがない」「DB が起動していない」など環境不備 → 修正可能な問題でありスキップ理由ではない
     - 実行時コード変更に対し「Small テストで十分カバーされている」→ テストサイズごとに検証対象が異なる

5. **影響ドキュメント**:
   - 「影響ドキュメント」セクションが存在し、影響範囲が適切に評価されているか？

### Step 2.5: 完了条件の段階確認

Issue 本文に `## 完了条件` セクションがある場合、設計が完了条件を充足できる構造になっているか確認する。

確認対象の例:
- 設計書のテスト方針が完了条件の要求を満たしているか
- 影響ドキュメントの評価が完了条件と整合しているか
- 設計段階で対応すべき条件が漏れていないか

確認結果は Step 3 の Issue コメントに含めて後段への証跡とする。

### Step 3: レビュー結果のコメント

レビュー結果をGitHub Issueにコメントします。

```bash
gh issue comment [issue-number] --body-file - <<'EOF'
# 設計レビュー結果

## 参照した一次情報

（レビュー時に確認した一次情報とその確認結果）

| 情報源 | 確認結果 |
|--------|----------|
| [URL] | ✅ 設計と整合 / ⚠️ 差異あり |

## 概要

(設計の明確さと、実装着手の可否判定)

## 指摘事項 (Must Fix)

- [ ] **項目**: 指摘内容
  - (要件の欠落、論理的な矛盾、不明確なインターフェースなど)
  - **一次情報との関連**: （該当する場合）

## 改善提案 (Should Fix)

- **項目**: 提案内容
  - (より良い命名、将来性を考慮した構造の提案など)

## 完了条件の段階確認

設計段階の完了条件に対する充足判定:

- [ ] (条件1): ✅ 設計書の○○で対応 / ❌ 不足（理由）
- [ ] (条件2): ✅ / ❌

## 判定

[ ] Approve (実装着手可)
[ ] Changes Requested (設計修正が必要)
EOF
```

### Step 4: 完了報告

以下の形式で報告してください:

```
## 設計レビュー完了

| 項目 | 値 |
|------|-----|
| Issue | #[issue-number] |
| 判定 | Approve / Changes Requested |

### 次のステップ

- Approve: `/issue-implement [issue-number]` で実装を開始
- Changes Requested: `/issue-fix-design [issue-number]` で修正
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

```
---VERDICT---
status: PASS
reason: |
  設計品質基準を満たしている
evidence: |
  一次情報との整合性確認済み、テスト戦略 S/M/L 網羅
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | Approve |
| RETRY | Changes Requested |
| ABORT | 重大な問題 |
