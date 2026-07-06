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

### Step 1.6: BACK 経由再起動の検出と分岐

`dev` workflow では `review-code` / `i-dev-final-check` 等が `BACK` verdict を返すと、戻し先が `design` の場合に本 skill が再起動される
（`.kaji/wf/dev.yaml:134` の `review-code` の `BACK: design` 等）。この再起動経路では設計書・implementation commit が既に存在するため、初回起動を前提とした
Step 2 以降の通常フローを素朴に実行すると scope 違反になる。本 Step では Step 1 で解決済みの `[worktree_dir]` を使って内部状態を観測し、初回起動 / BACK
経由再起動を分岐する。

**ステップ挿入位置の原則**: 本 Step は Step 1（worktree resolve）と Step 1.5（type 判定）の **直後** に位置する。worktree 絶対パスは Step 1 で確定し、
type ラベルの cardinality / canonical 判定（複数付与 / 未付与 / `type:docs`）は再起動経路でも崩せないガードとして Step 1.5 で先に走らせる。本 Step では
`[worktree_dir]` は Step 1 で取得した絶対パスを再利用し、再解決しない。

#### 観測コマンド

以下の 3 観測を実施する。すべて Step 1 で解決済みの `[worktree_dir]` を前提とする。

```bash
# 1. 既存設計書の有無
ls [worktree_dir]/draft/design/issue-[issue_id]-*.md 2>/dev/null

# 2. 設計後コミット（実装または skill/doc 改修）の有無
#    fix/[issue_id] ブランチが [default_branch] から枝分かれ後、commit が
#    2 件以上ある（最初は設計書作成 commit、それ以外に implementation /
#    skill / doc 改修 commit が存在）。Python 実装範囲（kaji_harness/ tests/
#    Makefile pyproject.toml）に pathspec を固定すると instruction-only
#    （.claude/skills/ 改修）/ docs-only Issue を取りこぼすため、
#    リポジトリ全体 commit から draft/design/ を除外する方式とする。
TOTAL=$(git -C [worktree_dir] log --oneline [default_branch]..HEAD | wc -l)
NON_DESIGN=$(git -C [worktree_dir] log --oneline [default_branch]..HEAD -- ':(exclude)draft/design/' | wc -l)
# 該当条件: TOTAL >= 2 かつ NON_DESIGN >= 1

# 3. 直近の戻し先 `design` の `BACK` verdict コメントの有無
#    review-code / i-dev-final-check 等が投稿する判定コメントは、harness の
#    `---VERDICT---` ブロックではなく Issue コメント本文中に
#    `[x] Changes Requested / BACK` チェック行や hard gate 結果テーブル
#    `| 判定 | BACK |` の形で表現される。
#
#    検出方針（dogfood 検証で判明した制約の最終形）:
#      (a) BACK 必須: `[x] Changes Requested` 単体は review-design の差し戻し
#          にも使われるため `/ BACK` を必須にする
#      (b) comment 単位フィルタ: jq の `.comments[]` イテレーションで
#          comment 境界を維持し、複数 comment を改行連結した stream を
#          grep する誤検出を排除する。過去の判定コメント本文（例: 「設計
#          再確認結果」コメントが BACK regex を引用するケース）が前の
#          comment の判定セクションに混入する事故を防ぐ
#      (c) 判定見出しゲート: 「判定セクション本体を持つ note」だけを対象
#          にするため、`# コードレビュー結果`（review-code 由来）と
#          `## 最終チェック結果`（i-dev-final-check 由来）を OR で
#          列挙する。新しい判定 step を追加した場合はここに見出しを追記
#          する。skill 側はどの step からの BACK かは意識しない
#      (d) fail-loud: kaji / jq が失敗した場合に「BACK 検出ゼロ＝初回
#          起動」と silent fallthrough すると、本 Issue が防ぎたかった
#          scope 違反を再発する。`2>/dev/null` を付けず、エラー発生時
#          は BACK_COUNT が空文字となり (e) で ABORT 経路に流す
#
#    `kaji issue view --json comments` は top-level object で、コメント配列を
#    `.comments` プロパティに持つ（各要素は GitHub REST API の Issue Comments
#    リソース。`body` / `created_at` 等のフィールドあり）。gh 互換の `--json`
#    フィールド指定形（https://cli.github.com/manual/gh_issue_view ）を使う。
BACK_COUNT=$(kaji issue view [issue_id] --json comments \
  | jq '[
      .comments[]
      | select(.body | test("^(# コードレビュー結果|## 最終チェック結果)"; "m"))
      | select(.body | test("\\[x\\] Changes Requested / BACK|\\| *判定 *\\|.*BACK"))
    ] | length')

# (e) fail-loud handler: kaji / jq 失敗時は BACK_COUNT が空文字。silent
#     に初回フローへ進めず、workflow runner が読める ABORT verdict ブロック
#     を stdout に出力した上で Step 2 以降には進まない。`exit 1` 単体では
#     workflow runner 側で `VerdictNotFound` 扱いになり on:ABORT 遷移が
#     成立しないため、必ず `---VERDICT--- ... ---END_VERDICT---` を stdout
#     に出してから skill を終了する。
if [ -z "$BACK_COUNT" ]; then
    cat <<'VERDICT_BLOCK'
---VERDICT---
status: ABORT
reason: |
  BACK detection pipeline failed (kaji issue view / jq error).
evidence: |
  `kaji issue view [issue_id] --json comments | jq ...` の評価に
  失敗し BACK_COUNT が空文字となった。初回フローへの silent fallthrough は
  既存設計書の上書きという scope 違反を再発させるため抑止する。
suggestion: |
  kaji CLI / GitHub API 接続 / .kaji/config.toml の `[provider]` 設定を
  確認した上で `/issue-design [issue_id]` を再実行してください。
---END_VERDICT---
VERDICT_BLOCK
    exit 1
fi
# BACK_COUNT >= 1 → 該当
#
# provider 差分: `--json comments` は github / local 両 provider で同一構造
# （`.comments[].body`）を返す（local は cli_main.py `_local_issue_view` が
# gh 互換 `--json` を実装済み）。provider 別の抽出器は不要。
```

`[worktree_dir]` は Step 1 で取得した絶対パスを再利用する（再解決しない）。

#### 分岐判定

| 条件 | 分岐先 |
|------|--------|
| 3 観測すべて該当（既存設計書 ∧ 設計後コミット ∧ 戻し先 `design` の `BACK` verdict コメント） | BACK 経由再起動 → **Step 1.7** に進む |
| いずれか欠ける | 初回起動（または近接ケース） → **Step 2** 以降の通常フロー |

> **BACK 必須化と誤検出防止**: `[x] Changes Requested` 単体（BACK なし）は `/issue-review-design` の `[x] Changes Requested (設計修正が必要)` のような **設計レビューの差し戻し** にも使われるため、戻し先 `design` の `BACK` verdict 検出には **`/ BACK` を必須**とする。さらに、過去の判定コメント本文（例: 設計再確認結果コメント自身）が同じ regex を引用する形で含むことがあるため、**jq の `.comments[]` イテレーションで comment 単位にフィルタ** し、判定セクション本体の見出し（`# コードレビュー結果` / `## 最終チェック結果` 等）を持つ comment のみを対象にする。新規の判定 step を追加した場合は (c) heading gate の OR リストに見出しを追記する。

> **fail-loud**: kaji CLI / GitHub API が失敗した場合に `2>/dev/null` で stderr を握りつぶし「BACK 検出ゼロ → 初回フロー」と silent fallthrough すると、本 Issue が防ぎたかった failure mode（既存設計書を上書きする scope 違反）を別経路で再発させる。観測 3 のパイプラインは stderr 抑止を付けず、`$BACK_COUNT` が空文字なら **`---VERDICT--- status: ABORT ... ---END_VERDICT---` ブロックを stdout に出力した上で** skill を終了し、Step 2 以降に進まない（`exit 1` 単体では workflow runner が `VerdictNotFound` 扱いとなり `on:ABORT` 遷移が成立しないため、verdict block の出力は必須）。

> **重要**: 「implementation 済みを検出したから ABORT する」という分岐は採用しない。BACK 経由再起動という workflow 仕様上の正当な遷移
> （`docs/dev/workflow-authoring.md:130` の `BACK = 差し戻し。前段ステップを再実行` 定義）に対しては Step 1.7 で `PASS` を返して通常フローに復帰させる。

### Step 1.7: BACK 経由再起動時の修正/再確認フロー

Step 1.6 で BACK 経由再起動と判定された場合のみ実行する。`PASS` 復帰を原則とし、`ABORT` を返すのは「設計レビュー観点で根本的に修正不能」と判断できる
場合に限定する。

#### サブステップ

1. **既存設計書の読込**: `[worktree_dir]/draft/design/issue-[issue_id]-*.md` を `Read` で読む
2. **直近 BACK verdict の特定**: Step 1.6 と同じ jq comment-unit + heading gate + BACK marker フィルタを用い、`[x] Changes Requested / BACK` または `\| 判定 \|.*BACK` を含む comment のうち **直近のもの** から指摘リストを抽出する。発行元 step（`review-code` / `i-dev-final-check` 等）は問わない
3. **指摘の分類**: 各指摘を「設計起因」「実装起因」に分類する
   - **設計起因**: 設計書の不備が原因の指摘（IF 設計の漏れ、テスト戦略の未定義、一次情報不足、影響ドキュメント漏れ等）
   - **実装起因**: 設計は正しいが実装が逸脱した指摘（見出し表記、コード品質、テスト失敗等）
4. **分岐実行**:
   - 設計起因の指摘がある場合 → 該当箇所のみ最小修正 → 設計書を `git commit` → 下記「コメント書式（修正あり）」を投稿 → **`PASS`**
   - 設計起因の指摘が無い場合 → 設計書未変更 → 下記「コメント書式（修正なし）」を投稿 → **`PASS`**
   - 設計レビュー観点で根本的に修正不能（例: 一次情報そのものが消失、要件の前提が崩壊） → **`ABORT`**

判定根拠（どの指摘を設計起因と判定したか）はコメントに必ず含める。

#### コメント書式（修正なし: 設計変更不要）

```markdown
## 設計再確認結果（BACK 経由再起動）

直近の `BACK: design` verdict（発行元 step: <review-code | i-dev-final-check 等>）を確認しました。

### 直近 BACK verdict の指摘内容

- 指摘 1: ...
- 指摘 2: ...

### 設計起因 / 実装起因の分類

| 指摘 | 分類 | 根拠 |
|------|------|------|
| 指摘 1 | 実装起因 | 設計書 X 節で要件は明示済み。実装側の逸脱 |
| 指摘 2 | 実装起因 | ... |

### 判定

設計起因の指摘は無し → 設計書を変更せず PASS を返します。
後続フロー（review-design → implement）で実装を修正してください。
```

#### コメント書式（修正あり: 設計書を更新）

```markdown
## 設計再確認結果（BACK 経由再起動）

直近の `BACK: design` verdict（発行元 step: <review-code | i-dev-final-check 等>）を確認し、設計書の以下箇所を修正しました。

### 修正箇所

- 設計書 X 節: ...
- 設計書 Y 節: ...

### 直近 BACK verdict 指摘の分類

| 指摘 | 分類 | 対応 |
|------|------|------|
| 指摘 1 | 設計起因 | 設計書 X 節を修正 |
| 指摘 2 | 実装起因 | 後続 implement フェーズで対応 |

### 判定

設計修正完了 → PASS。
```

> **規約遵守**: 本コメント本文に auto-close hazard pattern（`Clos(e[sd]?|ing)` / `Fix(e[sd]|ing)?` / `Resolv(e[sd]?|ing)` / `Implement(s|ing|ed)?`
> の直後 `#[0-9]`）を書かない。指摘参照は `指摘 N` / `Must Fix item N` / `point N` 形式に統一する
> （参照: [`docs/dev/shared_skill_rules.md`](../../../docs/dev/shared_skill_rules.md) § auto close keyword 回避規約）。

#### Step 1.7 終了後の挙動

BACK 経由再起動フローで `PASS` を返した場合、Step 2 以降の通常フローは実行しない。`PASS` で復帰すれば `.kaji/wf/dev.yaml:82` の
`design` の `PASS: review-design` 遷移が機能し、通常フロー（`review-design` → `implement` → `review-code`）が再開する。

#### 初回起動への影響（後方互換）

Step 1.6 の 3 観測のうち少なくとも 1 つが欠ける初回起動時は、Step 1.6 はノーオペとして素通りし Step 2 以降の通常フローに進む。初回起動の挙動は完全に不変。

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
| AGENTS.md / CLAUDE.md | あり/なし | (規約変更がある場合) |

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

> **規約遵守**: 本コメント本文に auto-close hazard pattern（`Clos(e[sd]?|ing)` / `Fix(e[sd]|ing)?` / `Resolv(e[sd]?|ing)` / `Implement(s|ing|ed)?` の直後 `#[0-9]`）を書かない。指摘参照は `指摘 N` / `Must Fix item N` / `point N` 形式に統一する（参照: [`docs/dev/shared_skill_rules.md`](../../../docs/dev/shared_skill_rules.md) § auto close keyword 回避規約）。

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
| PASS | 設計書作成・コミット完了、または BACK 経由再起動時の設計再確認完了（Step 1.6 / 1.7）|
| ABORT | 以下のいずれか: (a) Issue 要件が論理的に破綻しており、設計レベルで実現不能 / (b) BACK 経由再起動だが、指摘内容が設計レビュー観点で根本的に修正不能（例: 一次情報そのものが消失、要件の前提が崩壊）|

> **重要**: 「既に implementation commit がある」「BACK 経由で再起動された」ことそれ自体は `ABORT` 条件に **含めない**。BACK 経由再起動は workflow YAML 仕様上の正当な遷移であり、Step 1.6 / 1.7 で `PASS` 復帰させる（`docs/dev/workflow-authoring.md:130` の `BACK = 差し戻し。前段ステップを再実行` 定義との整合）。
