---
description: Issue要件に基づき、draft/design/に設計書を作成する。worktree内での作業が前提。
name: issue-design
---

# Issue Design

指定されたIssueに基づき、設計書（Markdown）を作成します。
設計書は `draft/design/` に作成され、`i-dev-final-check` 時に Issue 本文へアーカイブされます。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| Issue着手後、実装前 | ✅ 必須 |
| worktreeが存在しない | ❌ 先に `/issue-start` を実行 |

**ワークフロー内の位置**: create → start → **design** → review-design → implement → review-code → doc-check → i-dev-final-check → i-pr → close

## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値または local ID） |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`、local では bare ID） |
| `step_id` | str | 現在のステップ ID |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <issue_id>
```

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用。
なければ `$ARGUMENTS` の第1引数を `issue_id` として使用。

`issue_ref` はハーネス経由ではプロンプトに自動注入される（`prompt.py` 側で provider 別に整形）。手動実行時は `issue_id` から導出する: GitHub 数値 ID なら `#<issue_id>`、`local-*` 形式なら bare ID（`#` を付けない）。

## 前提知識の読み込み

以下のドキュメントを Read ツールで読み込んでから作業を開始すること。

### 共通（常に読み込む）

1. **開発ワークフロー**: `docs/dev/development_workflow.md`
2. **テスト規約**: `docs/dev/testing-convention.md`
3. **コーディング規約**: `docs/reference/python/python-style.md`
   - 必要に応じて `docs/reference/python/naming-conventions.md` /
     `type-hints.md` / `docstring-style.md` / `error-handling.md` /
     `logging.md` を追加読込

## 前提条件

- `/issue-start` が実行済みであること
- Issue本文にWorktree情報が記載されていること

## 設計書ルール

| ルール | 説明 |
|--------|------|
| **What & Constraint** | 入力/出力と制約のみ |
| **Minimal How** | 実装詳細は方針のみ。疑似コードはOK |
| **Primary Sources** | 一次情報（公式ドキュメント等）のURL/パスを必ず記載 |
| **API仕様** | 公式リンク参照（コピペ禁止） |
| **Test Strategy** | ID羅列ではなく検証観点を言語化 |

### 一次情報のアクセス可能性ルール

> **重要**: レビュワー（agent）がアクセスできない一次情報は使用できません。

| 情報の種類 | 対応方法 |
|------------|----------|
| 公開URL | そのまま記載（推奨） |
| ログイン必須/有償 | ローカルにダウンロードしてリポジトリに配置、または該当箇所を引用 |
| 社内限定/NDA | 使用不可。公開版ドキュメントを探すか、該当箇所のスクリーンショット・引用で代替 |

設計レビュー時にアクセス不可の一次情報があると、レビューが中断されます。

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。以降のステップではこのパスを使用する。

### Step 1.5: type の判定と type 別ガイドの読み込み

Issue ラベルから `type:*` ラベルを **配列として** 取得する:

```bash
kaji issue view [issue_id] --json labels --jq '[.labels[].name] | map(select(startswith("type:")))'
```

**cardinality チェック（先に判定）**:

- **配列要素数が 2 以上** → 複数 type ラベルが付与されている。設計フェーズに入らず処理を停止し、`/issue-review-ready` への差し戻しを案内する（ABORT）。type ラベルは Issue ごとに 1 つに限定する責務
- **配列が空** → type ラベル未付与。設計フェーズに入らず処理を停止し、`/issue-review-ready` への差し戻しを案内する（ABORT）。前段レディネスで type ラベル付与を確保する責務
- **配列要素数が 1** → その要素を採用し、以下の判定に進む

**type 値による分岐**:

1. **`type:docs`** → **本スキル対象外**。`/i-doc-update` を使用すること。処理を停止し、ユーザーに誘導する（ABORT）
2. **canonical（`type:feature` / `type:bug` / `type:refactor`）** → 対応するファイルを Read
3. **canonical 外（`type:test` / `type:chore` / `type:perf` / `type:security` など）** → `feat.md` を Read（フォールバック規則）

| type | 読み込むファイル |
|------|------------------|
| `type:feature` | `.claude/skills/_shared/design-by-type/feat.md` |
| `type:bug` | `.claude/skills/_shared/design-by-type/bug.md` |
| `type:refactor` | `.claude/skills/_shared/design-by-type/refactor.md` |
| canonical 外 | `.claude/skills/_shared/design-by-type/feat.md`（フォールバック） |

読み込んだ type 別ガイドは、Step 2 の設計書セクション構成・必須項目・テスト戦略の判断基準として使う。

### Step 2: 設計書の作成

1. **ディレクトリ作成**（絶対パスを使用）:
   ```bash
   mkdir -p [worktree_dir]/draft/design
   ```

2. **ファイル名決定**:
   - `draft/design/issue-[issue_id]-[short-name].md`
   - 例: `draft/design/issue-42-workflow.md`

3. **設計書テンプレート**:

```markdown
# [設計] タイトル

Issue: [issue_ref]

## 概要

(何を実現するか、1-2文で)

## 背景・目的

(なぜこの変更が必要か)

## インターフェース

### 入力

(引数、パラメータ、設定など)

### 出力

(戻り値、副作用、生成物など)

### 使用例

\`\`\`python
# ユーザーコード例
\`\`\`

## 制約・前提条件

- (技術的制約)
- (ビジネス制約)
- (依存関係)

## 方針

(実装の大まかな方針。疑似コードOK)

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。
> 実行時コード変更では Small / Medium / Large の観点を定義し、
> docs-only / metadata-only / packaging-only 変更では変更固有検証と
> 恒久テストを追加しない理由を明記する。
> 詳細は [テスト規約](../../../docs/dev/testing-convention.md) 参照。

### 変更タイプ
- (実行時コード変更 / docs-only / metadata-only / packaging-only)

### 実行時コード変更の場合

#### Small テスト
- (検証対象を列挙: 単体ロジック、バリデーション、マッピング等)
- (不要な場合: 不要理由と `docs/dev/testing-convention.md` の 4 条件の充足根拠)

#### Medium テスト
- (検証対象を列挙: DB連携、内部サービス結合等)
- (不要な場合: 不要理由と `docs/dev/testing-convention.md` の 4 条件の充足根拠)

#### Large テスト
- (検証対象を列挙: 実API疎通、E2Eデータフロー等)
- (不要な場合: 不要理由と `docs/dev/testing-convention.md` の 4 条件の充足根拠)

### docs-only / metadata-only / packaging-only の場合

#### 変更固有検証
- (例: `make verify-docs`、隔離環境での `uv pip install -e .`、`importlib.metadata` 確認)

#### 恒久テストを追加しない理由
- (`docs/dev/testing-convention.md` の 4 条件に沿って記載)

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | あり/なし | (新しい技術選定がある場合) |
| docs/ARCHITECTURE.md | あり/なし | (アーキテクチャ変更がある場合) |
| docs/dev/ | あり/なし | (ワークフロー・開発手順変更がある場合) |
| docs/reference/ | あり/なし | (API仕様・規約変更がある場合) |
| docs/cli-guides/ | あり/なし | (CLI仕様変更がある場合) |
| CLAUDE.md | あり/なし | (規約変更がある場合) |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| (公式ドキュメント名) | (URL) | (設計判断の裏付けとなる引用または要約) |

> **重要**: 設計判断の根拠となる一次情報を必ず記載してください。
> - URLだけでなく、**根拠（引用/要約）** も記載必須
> - レビュー時に一次情報の記載がない場合、設計レビューは中断されます
```

### Step 2.5: 完了条件の段階確認

設計書の品質と Issue 完了条件の充足を段階的に確認する。

1. **必須セクションの存在確認**:
   - [ ] 概要
   - [ ] 背景・目的
   - [ ] インターフェース（入力・出力）
   - [ ] 制約・前提条件
   - [ ] 方針
   - [ ] テスト戦略（変更タイプに応じたセクション）
   - [ ] 影響ドキュメント
   - [ ] 参照情報（Primary Sources）

2. **内容の妥当性確認**:
   - テスト戦略が変更タイプに対して妥当か
   - Primary Sources に根拠が記載されているか
   - 影響ドキュメントが網羅的か

3. **Issue 完了条件の段階確認**:
   Issue 本文に `## 完了条件` セクションがある場合、設計段階で確認可能な条件を確認する。
   - 設計書に必要なセクションが完了条件の要求を満たしているか
   - 技術制約や前提条件が設計書に反映されているか

不足がある場合は設計書を補完してからコミットする。この段階で確認した条件は、Step 4 の Issue コメントに含めて後段への証跡とする。

### Step 2.6: Self-Check（ハンドオフ前 / MANDATORY）

`/issue-review-design` の rubric と作業中の設計書を突き合わせ、handoff 直前の楽観バイアスを抑止する。**重複チェックリストは作成しない**。review-design SKILL.md を **rubric の単一情報源** として参照し、不足があれば Step 3（コミット）に進む前に設計書を補完する。

#### 参照する review-design rubric の節

`.claude/skills/issue-review-design/SKILL.md` の以下節を **直接読む**:

1. **Step 1.5（一次情報の記載と Gate Check）**
   - 設計書「参照情報（Primary Sources）」の URL/パスが記載されているか
   - 一次情報の引用 / 要約が「設計判断の裏付け」として機能しているか
   - アクセス可能性ルール（公開URL / ログイン必須 / 社内限定）に違反していないか
2. **Step 2 § type の取得と観点の重み付け**
   - 採用した type ラベルに対応する重み付け表（feat / bug / refactor / docs）と設計書の重点が整合しているか
   - **feat**: 代替案 / ユースケース / 使用例の有無
   - **bug**: OB / EB の 1 次情報裏付け / 再現手順最小 / 根本原因「なぜ」/ 同根の他壊れ箇所の調査
   - **refactor**: ベースライン計測コマンド / 改善指標の測定可能性 / 公開 IF 不変宣言 / safety net 方針
3. **Step 2 § レビュー基準 1〜5**
   - 1. 抽象化と責務の分離（What & Why / Constraints）
   - 2. インターフェース設計（Usage Sample / Idiomatic / Naming）
   - 3. 信頼性とエッジケース（Source of Truth / Error Handling / 一次情報との乖離）
   - 4. 検証可能性（テストサイズ別検証観点 / `docs/dev/testing-convention.md` の 4 条件マッピング / 不正当な省略理由の排除）
   - 5. 影響ドキュメント

#### Self-Check の実施手順

1. 上記 3 節を順に Read する
2. 設計書を読み直し、各節の checklist 項目に対する充足度を内部で評価する
3. 不足が見つかったら **Step 3 に進む前に設計書を補完** する（補完後、本 Step 2.6 を再実行）
4. 結果（節ごとの判定と不足の有無）を Step 4 の Issue コメント `## Self-Check 結果` セクションに転記する

#### 出力フォーマット（Issue コメントへの転記用、Step 4 で使用）

```markdown
## Self-Check 結果（design pre-handoff）

- **経路**: main-session self-check
- **対象 commit**: <git-sha>
- **参照 rubric**: `/issue-review-design` SKILL.md Step 1.5 / Step 2 § type 重み付け / Step 2 § レビュー基準 1〜5

### Step 1.5: Gate Check（一次情報）
- 判定: ✅ / ⚠️ / ❌
- 根拠: 設計書「参照情報（Primary Sources）」セクションの状態

### Step 2 § type 重み付け（type: <採用 type>）
- 判定: ✅ / ⚠️ / ❌
- 根拠: 重点観点との整合

### Step 2 § レビュー基準 1〜5
- 1. 抽象化と責務の分離: ✅ / ⚠️ / ❌
- 2. インターフェース設計: ✅ / ⚠️ / ❌
- 3. 信頼性とエッジケース: ✅ / ⚠️ / ❌
- 4. 検証可能性: ✅ / ⚠️ / ❌
- 5. 影響ドキュメント: ✅ / ⚠️ / ❌

### 補完した項目
- （補完前に検出した不足と、補完内容を列挙。なければ「無し」）

### Self-Check Verdict
- **Yes** — handoff 可（全 ✅ または ⚠️ のみで補完済み）
- **With fixes** — 補完後に再度本フェーズを実行する必要あり
- **No** — `/issue-fix-design` 相当の大幅修正が必要（本フェーズで自己解決できない）
```

> **規約遵守**: 本コメント本文に GitLab auto-close hazard pattern（`Clos(e[sd]?|ing)` / `Fix(e[sd]|ing)?` / `Resolv(e[sd]?|ing)` / `Implement(s|ing|ed)?` の直後 `#[0-9]`）を書かない。指摘参照は `指摘 N` / `Must Fix item N` / `point N` 形式に統一する（参照: [`docs/dev/shared_skill_rules.md`](../../../docs/dev/shared_skill_rules.md) § GitLab auto close keyword 回避規約）。

### Step 3: コミット

```bash
cd [worktree_dir] && git add draft/design/ && git commit -m "docs: add design for [issue_ref]"
```

### Step 4: Issueにコメント

設計完了をIssueにコメントします。

```bash
kaji issue comment [issue_id] --commit --body-file - <<'EOF'
## 設計書作成完了

設計書を作成しました。

### 成果物

- **ファイル**: `draft/design/issue-[issue_id]-xxx.md`

### 設計の要点

1. **What**: (何を実現するか)
2. **Why**: (なぜこの設計か)
3. **Constraints**: (主な制約)

### テスト戦略

- (主要な検証ポイント)

### Self-Check 結果（design pre-handoff）

(Step 2.6 で生成した「Self-Check 結果」ブロックをそのまま貼り付け。経路 / 対象 commit / 参照 rubric / 5 観点判定 / 補完項目 / Verdict)

### 完了条件の段階確認

この段階で確認可能な完了条件:

- [ ] (確認した条件1): ✅ 設計書の○○セクションで対応
- [ ] (確認した条件2): ✅ 設計書の△△セクションで対応
- (未確認の条件があれば): 実装段階以降で確認予定

### 次のステップ

`/issue-review-design [issue_id]` でレビューをお願いします。
EOF
```

### Step 5: 完了報告

以下の形式で報告してください:

```
## 設計書作成完了

| 項目 | 値 |
|------|-----|
| Issue | [issue_ref] |
| 設計書 | draft/design/issue-[issue_id]-xxx.md |
| コミット | [commit-hash] |

### 次のステップ

`/issue-review-design [issue_id]` でレビューを実施してください。
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

```
---VERDICT---
status: PASS
reason: |
  設計書作成・コミット完了
evidence: |
  draft/design/issue-XX-*.md を作成
suggestion: |
---END_VERDICT---
```

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 設計書作成・コミット完了 |
| ABORT | 設計不可能な要件 |
