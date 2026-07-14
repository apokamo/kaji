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
2. **重要判断チェックリスト**: `.claude/skills/_shared/critical-decision-checklist.md`
3. **テスト規約**: `docs/dev/testing-convention.md`
4. **コーディング規約**: `docs/reference/python/python-style.md`
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
| **Decision Provenance** | 人間決定の出典、AI の仮定、設計で行った詳細化を分離 |

本フェーズは重要方針を新たに決める場ではなく、**決定済み方針を実装可能な粒度へ
詳細化する場**である。詳細化と未決事項の境界は
[_shared/critical-decision-checklist.md](../_shared/critical-decision-checklist.md)
の可逆性基準で判定する。

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
- [_shared/critical-decision-checklist.md](../_shared/critical-decision-checklist.md) — 人間の重要判断、AI の仮定、one-way door の分類と停止条件の正本

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

### Step 1.55: 重要判断の分類と停止判定

Issue 本文・人間の Issue コメント・指定された source of truth を読み、
`_shared/critical-decision-checklist.md` に従って判断を次の 3 種類に分類する。

1. 決定済み方針の詳細化
2. two-way door の未決
3. one-way door の未決

既存設計書がある場合は、その「重要判断 provenance」も入力に含める。人間が source of
truth と指定した資料を、設計上の都合で上書き・弱化・参考資料へ格下げしない。一次情報
同士が矛盾し、優先順位が人間未決なら AI が選択しない。

- 分類 1: 出典と決定範囲を特定し、Step 2 で範囲内の詳細化を記録する
- 分類 2: AI の仮定、根拠、後段の検査先を特定し、Step 2 で記録して進む
- 分類 3: Step 2 以降へ進まず `ABORT`。`suggestion` に決めるべき項目、競合する
  選択肢・情報源、再開条件を列挙する

公開 CLI/API、永続化 schema、migration、運用、非互換変更、ユーザー価値、source of
truth 指定は分類 3 になりやすい代表軸だが、項目名ではなく「誤ったときに後段で安く
直せるか」で判定する。人間決定は存在し、参照や書式が不足しているだけなら、設計書で
provenance を補完して進めてよい。

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

# 3. 直近の戻し先 `design` の `BACK` verdict マーカーコメントの有無
#    producer skill（review-code / i-dev-final-check / implement 等）は判定
#    コメント投稿時に `kaji issue comment --verdict-step <step> --verdict-status
#    <STATUS>` を無条件付与し、CLI が body 1 行目に決定的な HTML マーカー
#    `<!-- kaji-verdict: step=<step> status=<STATUS> -->` を埋め込む
#    （契約の正本は CLI コード = kaji_harness/providers/markers.py。ADR 008
#    決定 3: cross-skill 契約は SKILL.md 散文ではなく CLI/harness 層に置く）。
#
#    consumer は **このマーカーのみ** を参照する。旧来の判定見出しゲート
#    （`# コードレビュー結果` / `## 最終チェック結果` の OR）と旧 regex
#    （`[x] Changes Requested / BACK` / `| 判定 |.*BACK`）は完全削除した
#    （ADR 008 決定 1: 後方互換フォールバックを残さない。旧検出は一度も
#    機能していないため互換対象の「動いていた過去」が存在しない）。
#
#    design を戻し先とする status 集合 = {BACK, BACK_DESIGN}
#      - dev 系 workflow の bare `BACK`（implement / review-code 発）は常に
#        design 行き。`BACK_DESIGN`（final-check 発）も design 行き
#      - `BACK_IMPLEMENT` / `BACK_FALLBACK` は完全一致で除外される
#        （regex は status トークン全体を照合。部分一致しない）
#      - design を戻し先とする新 status を追加した場合のみ、この status 集合
#        （`(BACK|BACK_DESIGN)` の alternation）に追記する
#
#    (a) 1 行目マーカー厳密照合: `test()` は `m` フラグなしのため `^` は
#        文字列先頭のみに一致 = body 1 行目のマーカーだけを検出する。過去
#        コメント本文中の marker 引用（2 行目以降）への誤検出は構造的に
#        起きない
#    (b) comment 単位フィルタ: `.comments[]` イテレーションで comment 境界
#        を維持する（複数 comment を改行連結した stream の grep 誤検出を排除）
#    (c) fail-loud: kaji / jq が失敗した場合に「BACK 検出ゼロ＝初回起動」と
#        silent fallthrough すると scope 違反を再発する。`2>/dev/null` を
#        付けず、エラー時は BACK_COUNT が空文字となり (e) で ABORT に流す
#
#    `kaji issue view --json comments` は top-level object で、コメント配列を
#    `.comments` プロパティに持つ（各要素は GitHub REST API の Issue Comments
#    リソース。`body` / `created_at` 等のフィールドあり）。gh 互換の `--json`
#    フィールド指定形（https://cli.github.com/manual/gh_issue_view ）を使う。
BACK_COUNT=$(kaji issue view [issue_id] --json comments \
  | jq '[
      .comments[]
      | select(.body | test("^<!-- kaji-verdict: step=[a-z][a-z0-9_-]* status=(BACK|BACK_DESIGN) -->"))
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
# BACK_COUNT >= 1 → design 再入マーカーあり
#
# provider 差分: `--json comments` は github / local 両 provider で同一構造
# （`.comments[].body`）を返す（local は cli_main.py `_local_issue_view` が
# gh 互換 `--json` を実装済み）。provider 別の抽出器は不要。マーカーは CLI が
# 両 provider で同一形式で付与するため、consumer 側も provider 非依存。
```

`[worktree_dir]` は Step 1 で取得した絶対パスを再利用する（再解決しない）。

#### 分岐判定

観測 1（既存設計書）・観測 2（設計後コミット）・観測 3（design 再入マーカー `BACK_COUNT >= 1`）の 3 値で 4 分岐する:

| 観測 1 | 観測 2 | 観測 3 | 分岐先 |
|:---:|:---:|:---:|--------|
| ✓ | ✓ | ✓ | BACK 経由再起動 → **Step 1.7** に進む |
| ✓ | ✓ | ✗（`BACK_COUNT == 0`） | **曖昧状態 → fail-safe ABORT**（下記ブロックを stdout に出力して終了） |
| ✗ または（✓ ∧ 観測 2 ✗） | | | 初回起動（または近接ケース） → **Step 2** 以降の通常フロー |
| `BACK_COUNT` が空文字（パイプライン失敗） | | | fail-loud ABORT（観測 3 の (e) handler で処理済み） |

**fail-safe ABORT（設計書あり + 設計後コミットあり + マーカーなし）**: 「設計書と設計後コミットが揃っているのに design 再入マーカーが 1 件も無い」曖昧状態では、初回起動扱いで Step 2 に進むと既存設計書を上書きしうる。この帰結を「上書き事故」から「一時停止」に変えるため ABORT する（決定 5・ADR 008 帰結。これは後方互換策ではなく恒久的な安全設計であり、producer のマーカー付与失敗時の backstop を兼ねる）。該当時は次のブロックを stdout に出力して skill を終了する（`[step]` / `[status]` は直近の BACK verdict の発行元・status に置換。不明なら `review-code` / `BACK`）:

```bash
cat <<'VERDICT_BLOCK'
---VERDICT---
status: ABORT
reason: |
  Ambiguous restart state: design doc + post-design commit exist but no
  kaji-verdict BACK/BACK_DESIGN marker was found among issue comments.
evidence: |
  観測 1（設計書あり）と観測 2（設計後コミットあり）が真だが、観測 3 の
  design 再入マーカー（<!-- kaji-verdict: ... status=(BACK|BACK_DESIGN) -->）が
  0 件だった。初回起動扱いで Step 2 に進むと既存設計書を上書きするため停止する。
suggestion: |
  直近の BACK 判定コメントを
  `kaji issue comment [issue_id] --verdict-step [step] --verdict-status BACK --body <再投稿内容>`
  で verdict マーカー付きに再投稿してから `/issue-design [issue_id]` を再実行してください。
---END_VERDICT---
VERDICT_BLOCK
exit 1
```

> **マーカー単一情報源と誤検出防止**: 旧実装は「判定見出しゲート + `[x] Changes Requested / BACK` regex」を使い、producer 側テンプレートがその表現を一度も出力していなかったため BACK 検出が常に 0 だった（本 Issue の根本原因）。新実装は CLI が決定的に付与する 1 行目マーカーのみを参照する。producer の判定コメントは status によらず無条件でマーカーを持つため、review-design の RETRY コメントや final-check のメニュー行が BACK と誤検出されることは status 語彙レベルで構造的に排除される。「新しい判定 step を追加したら見出しリストに追記する」という旧保守点は消え、「design を戻し先とする新 status を追加したら観測 3 の status 集合（`(BACK|BACK_DESIGN)`）に追記する」だけになる。

> **fail-loud**: kaji CLI / GitHub API が失敗した場合に `2>/dev/null` で stderr を握りつぶし「BACK 検出ゼロ → 初回フロー」と silent fallthrough すると、本 Issue が防ぎたかった failure mode（既存設計書を上書きする scope 違反）を別経路で再発させる。観測 3 のパイプラインは stderr 抑止を付けず、`$BACK_COUNT` が空文字なら **`---VERDICT--- status: ABORT ... ---END_VERDICT---` ブロックを stdout に出力した上で** skill を終了し、Step 2 以降に進まない（`exit 1` 単体では workflow runner が `VerdictNotFound` 扱いとなり `on:ABORT` 遷移が成立しないため、verdict block の出力は必須）。

> **重要**: 「implementation 済みを検出したから ABORT する」という分岐は採用しない。BACK 経由再起動という workflow 仕様上の正当な遷移
> （`docs/dev/workflow-authoring.md:130` の `BACK = 差し戻し。前段ステップを再実行` 定義）に対しては Step 1.7 で `PASS` を返して通常フローに復帰させる。

### Step 1.7: BACK 経由再起動時の修正/再確認フロー

Step 1.6 で BACK 経由再起動と判定された場合のみ実行する。`PASS` 復帰を原則とし、`ABORT` を返すのは「設計レビュー観点で根本的に修正不能」と判断できる
場合に限定する。

#### サブステップ

1. **既存設計書の読込**: `[worktree_dir]/draft/design/issue-[issue_id]-*.md` を `Read` で読む
2. **直近 BACK verdict の特定**: Step 1.6 と同じ verdict マーカーフィルタを用いて、body 1 行目に `<!-- kaji-verdict: step=<step> status=(BACK|BACK_DESIGN) -->` を持つ comment のうち **配列末尾（直近）のもの** を選び、その 2 行目以降（判定コメント本体）から指摘リストを抽出する。発行元 step（マーカーの `step=` 値。`review-code` / `final-check` / `implement` 等）は問わない。抽出コマンド例:

```bash
kaji issue view [issue_id] --json comments \
  | jq -r '[
      .comments[]
      | select(.body | test("^<!-- kaji-verdict: step=[a-z][a-z0-9_-]* status=(BACK|BACK_DESIGN) -->"))
    ] | last | .body'
```
3. **指摘の分類**: 各指摘を「設計起因」「実装起因」に分類する
   - **設計起因**: 設計書の不備が原因の指摘（IF 設計の漏れ、テスト戦略の未定義、一次情報不足、影響ドキュメント漏れ等）
   - **実装起因**: 設計は正しいが実装が逸脱した指摘（見出し表記、コード品質、テスト失敗等）
4. **分岐実行**:
   - 設計起因の指摘がある場合 → 該当箇所のみ最小修正 → 設計書を `git commit` → 下記「コメント書式（修正あり）」を投稿 → **`PASS`**
   - 設計起因の指摘が無い場合 → 設計書未変更 → 下記「コメント書式（修正なし）」を投稿 → **`PASS`**
   - 設計レビュー観点で根本的に修正不能（例: 一次情報そのものが消失、要件の前提が崩壊） → **`ABORT`**

判定根拠（どの指摘を設計起因と判定したか）はコメントに必ず含める。

> **verdict マーカーの付与（必須）**: 下記いずれのコメント書式を投稿する場合も、`kaji issue comment [issue_id] --commit --verdict-step design --verdict-status <STATUS> --body-file - <<'EOF' ... EOF` の形で verdict マーカーを付与する。`<STATUS>` は本サブステップで返す status（`PASS` 復帰なら `PASS`、根本的に修正不能なら `ABORT`）に置換する。`status=PASS` / `status=ABORT` はいずれも Step 1.6 の BACK 再入検出（`BACK` / `BACK_DESIGN` のみ計数）にヒットしないため、この「設計再確認結果」コメント自身が次回の BACK 検出母集団を汚すことは構造的に起きない（旧実装ではこのコメントが regex を引用して誤検出源になっていた）。

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

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| (後段で独立に検査できる判断) | (採用方針) | (Issue 本文/人間コメント/既存契約、または「AI の仮定」+ 根拠 + 後段の検査先) | (決定範囲内で具体化した内容) |

> 重要判断がない場合も省略せず、「該当なし」と確認根拠を記載する。
> source of truth の格下げ、出典のない one-way door、AI 仮定の人間決定への偽装は禁止。

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
   - [ ] 重要判断 provenance
   - [ ] テスト戦略（変更タイプに応じたセクション）
   - [ ] 影響ドキュメント
   - [ ] 参照情報（Primary Sources）

2. **内容の妥当性確認**:
   - テスト戦略が変更タイプに対して妥当か
   - Primary Sources に根拠が記載されているか
   - 重要判断 provenance で人間決定の出典、AI の仮定、設計で行った詳細化が分離されているか
   - source of truth の格下げや one-way door の自己解釈がないか
   - 影響ドキュメントが網羅的か

3. **Issue 完了条件の段階確認**:
   Issue 本文に `## 完了条件` セクションがある場合、
   `### ワークフロー完了後の確認項目` を除いた設計段階で確認可能な条件を確認する。
   - 設計書に必要なセクションが完了条件の要求を満たしているか
   - 技術制約や前提条件が設計書に反映されているか

4. **workflow 内 / workflow 外の分離確認**:
   `docs/dev/workflow_completion_criteria.md` § workflow 内完了条件と事後確認の分離に従い、
   workflow を RETRY して環境非依存で同じ結果を得られない項目が通常完了条件へ混在していないか確認する。
   誤分類を見つけた場合は、現在の Issue 本文を取得し、項目の文言とチェック状態を維持したまま
   `## 完了条件` の末尾サブセクション `### ワークフロー完了後の確認項目` との間で移動し、
   `kaji issue edit [issue_id] --commit --body-file /tmp/issue-body.md` で反映する。
   事後確認がなければ同サブセクションを削除するか `- なし` とする。

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
4. **重要判断と provenance**
   - `_shared/critical-decision-checklist.md` の 3 分類と可逆性基準に従っているか
   - 人間決定の出典と AI の仮定が区別され、仮定に後段の検査先があるか
   - source of truth を上書き・弱化・格下げしていないか
   - one-way door の未決が見つかった場合は handoff せず `ABORT` する

#### Self-Check の実施手順

1. 上記 4 節を順に Read する
2. 設計書を読み直し、各節の checklist 項目に対する充足度を内部で評価する
3. 不足が見つかったら **Step 3 に進む前に設計書を補完** する（補完後、本 Step 2.6 を再実行）
4. 結果（節ごとの判定と不足の有無）を Step 4 の Issue コメント `## Self-Check 結果` セクションに転記する

#### 出力フォーマット（Issue コメントへの転記用、Step 4 で使用）

```markdown
## Self-Check 結果（design pre-handoff）

- **経路**: main-session self-check
- **対象 commit**: <git-sha>
- **参照 rubric**: `/issue-review-design` SKILL.md Step 1.5 / Step 2 § type 重み付け / Step 2 § 重要判断 audit / Step 2 § レビュー基準 1〜5

### Step 1.5: Gate Check（一次情報・provenance）
- 判定: ✅ / ⚠️ / ❌
- 根拠: 設計書「参照情報（Primary Sources）」セクションの状態

### Step 2 § type 重み付け（type: <採用 type>）
- 判定: ✅ / ⚠️ / ❌
- 根拠: 重点観点との整合

### Step 2 § 重要判断 audit
- 判定: ✅ / ⚠️ / ❌
- 根拠: provenance、人間決定、AI 仮定、source of truth の確認結果

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

**verdict マーカーの無条件付与（必須）**: 設計完了コメントには `--verdict-step design --verdict-status PASS` を付与する（design が完了時に返す status は `PASS`）。CLI が body 1 行目に `<!-- kaji-verdict: step=design status=PASS -->` を付与する。これは自身の判定コメントであり、無条件付与原則（ADR 008 決定 3）の一貫性のために付ける。`status=PASS` のため Step 1.6 の BACK 再入検出（`BACK` / `BACK_DESIGN` のみ計数）には決してヒットしない。

```bash
kaji issue comment [issue_id] --commit \
  --verdict-step design --verdict-status PASS \
  --body-file - <<'EOF'
## 設計書作成完了

設計書を作成しました。

### 成果物

- **ファイル**: `draft/design/issue-[issue_id]-xxx.md`

### 設計の要点

1. **What**: (何を実現するか)
2. **Why**: (なぜこの設計か)
3. **Constraints**: (主な制約)

### 重要判断 provenance

- (人間決定の出典、AI の仮定と検査先、設計で行った詳細化の要点)

### テスト戦略

- (主要な検証ポイント)

### Self-Check 結果（design pre-handoff）

(Step 2.6 で生成した「Self-Check 結果」ブロックをそのまま貼り付け。経路 / 対象 commit / 参照 rubric / 5 観点判定 / 補完項目 / Verdict)

### 完了条件の段階確認

この段階で確認可能な workflow 内完了条件（`### ワークフロー完了後の確認項目` は除外）:

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
| ABORT | 以下のいずれか: (a) one-way door が人間未決、または source of truth 間の優先順位が未決 / (b) Issue 要件が論理的に破綻しており、設計レベルで実現不能 / (c) BACK 経由再起動だが、指摘内容が設計レビュー観点で根本的に修正不能（例: 一次情報そのものが消失、要件の前提が崩壊）|

one-way door の `ABORT` では、`suggestion` に人間が決める項目、競合する選択肢・
情報源、再開条件を具体的に列挙する。設計 agent が妥当そうな方針を選んで続行しない。

> **重要**: 「既に implementation commit がある」「BACK 経由で再起動された」ことそれ自体は `ABORT` 条件に **含めない**。BACK 経由再起動は workflow YAML 仕様上の正当な遷移であり、Step 1.6 / 1.7 で `PASS` 復帰させる（`docs/dev/workflow-authoring.md:130` の `BACK = 差し戻し。前段ステップを再実行` 定義との整合）。
