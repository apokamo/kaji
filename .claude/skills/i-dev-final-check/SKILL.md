---
description: dev workflow 向けの最終チェック。PR 前に品質ゲート、docs 整合、設計書昇格、Issue 更新をまとめて確認する。
name: i-dev-final-check
---

# I Dev Final Check

dev workflow の PR 前最終ゲート。
前段で作られた証跡を集約し、必要なら docs 更新や設計書昇格を行ったうえで、PR に進めるか判定する。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-review-code` または `/issue-verify-code` で Approve 後 | ✅ 必須 |
| dev workflow の PR 作成前 | ✅ 必須 |

**ワークフロー内の位置**: implement → review-code → **i-dev-final-check** → i-pr → close

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

1. [docs/dev/development_workflow.md](../../../docs/dev/development_workflow.md)
2. [docs/dev/workflow_completion_criteria.md](../../../docs/dev/workflow_completion_criteria.md)
3. [docs/dev/documentation_update_criteria.md](../../../docs/dev/documentation_update_criteria.md)
4. [docs/dev/shared_skill_rules.md](../../../docs/dev/shared_skill_rules.md)
5. `docs/README.md`
6. [_shared/promote-design.md](../_shared/promote-design.md)

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実施内容

1. worktree と branch を解決する
2. 前段の証跡を集約し、Issue 完了条件との照合を行う
3. 設計書の「影響ドキュメント」と実差分を確認する
4. 品質ゲートを実行する（後述 Step 4 詳細）
5. docs 更新の最終確認を行い、必要なら修正する
6. 設計書昇格判定 → 必要なら昇格、または既存 docs 更新の有無を確認する
7. Issue 本文の完了条件を照合し、充足状態を更新する
7.5. 設計書を Issue 本文の NOTE ブロック直下に添付する
8. Issue に最終チェック結果をコメントする

## Step 2 詳細: 前段証跡の集約と完了条件照合

### 2-1. 前段コメントの走査

```bash
kaji issue view [issue_id] --comments
```

以下の完了報告コメントが存在するか確認する:

| ステップ | 期待するコメント | 必須の内容 |
|----------|----------------|-----------|
| `issue-design` | 「設計書作成完了」 | 設計書パス、テスト戦略、影響ドキュメント |
| `issue-review-design` | 「設計レビュー結果」 | Approve / Changes Requested 判定 |
| `issue-fix-design` → `issue-verify-design` | （経由した場合のみ）「修正確認結果」 | Approve 判定 |
| `issue-implement` | 「実装完了報告」 | pytest 出力（S/M/L 結果）、品質チェック結果 |
| `issue-review-code` | 「コードレビュー結果」 | Approve / Changes Requested 判定、独立テスト実行結果 |
| `issue-fix-code` → `issue-verify-code` | （経由した場合のみ）「修正確認結果」 | Approve 判定 |

> **fix/verify サイクルの扱い**: 実 workflow では `issue-review-*` が Changes Requested を返した場合、
> `issue-fix-*` → `issue-verify-*` を経由して再度 Approve を得てから final-check に到達する。
> コメント履歴に過去の Changes Requested が残るのは正常な状態であり、**最新の判定結果**（verify の Approve）を
> 権威ある判定として採用する。過去の Changes Requested は「解決済みの指摘」として無視してよい。

### 2-2. 完了条件との照合

Issue 本文の `## 完了条件` セクション（チェックボックス形式）を取得し、各条件について:

1. **どの前段で確認されたか** を特定する
2. **確認の根拠** を前段コメントから抽出する（最新のコメントを優先）
3. **未確認の条件** があれば、この final-check で確認するか、前段への差し戻しが必要かを判断する

### 2-3. 前段証跡が不足している場合

差し戻しが必要な場合は **root-cause** に応じて以下を使い分ける。実際にどの status を返せるかは
workflow YAML の `final-check.on` で決まり、prompt 経由で valid status 一覧が注入される
（詳細は § Verdict 出力 § workflow YAML 互換ルール）。

| root-cause | 例 | 推奨 status（新 YAML） | 互換 status（旧 YAML） |
|------------|-----|------------------------|------------------------|
| **設計起因** | 設計書の影響ドキュメント評価漏れ / テスト戦略未定義 / 要件解釈の食い違い | `BACK_DESIGN` | `BACK`（YAML が `BACK` のみ valid な場合） |
| **実装起因** | 前段コメント欠落 / 最新判定が Changes Requested のまま / 品質ゲート未通過 / docs 更新漏れ | `BACK_IMPLEMENT` | `BACK`（YAML が `BACK` のみ valid な場合） |
| **完了条件未充足** | 最新の判定結果は Approve だが Issue 完了条件が未充足 | この final-check で対応可能なら `RETRY`、不可能なら root-cause に応じ `BACK_DESIGN` / `BACK_IMPLEMENT` / `BACK` | 同左 |

> **root-cause 不明の場合**: 自動で `BACK_DESIGN` 等を default にせず、`ABORT` を返して運用に escalation する。

## Step 4 詳細: 品質ゲートの実行

kaji は Python 単一スタックのため、以下の 1 本に統一する。

```bash
cd [worktree_dir] && source .venv/bin/activate && make check
```

`make check` は ruff / format / mypy / pytest を一括で実行する（AGENTS.md の pre-commit 契約と同一）。

特定マーカーや変更タイプ固有の検証が必要な場合は、設計書「テスト戦略」に従い追加実行する:

| 変更タイプ | 追加検証 |
|-----------|----------|
| docs-only | `make verify-docs` |
| metadata-only / packaging-only | `make verify-packaging` |
| 通常 | 追加なし（`make check` で十分） |

> **baseline failure の扱い**: `pytest` 部分は baseline failure を考慮し、
> 比較キー `(nodeid, kind, error_type)` が baseline と一致する失敗は除外、
> 不一致の新規 FAILED/ERROR が 1 件でもあれば NG として扱う。

## Step 6 詳細: 設計書昇格判定

[_shared/promote-design.md](../_shared/promote-design.md) の手順に従い、
`draft/design/issue-[issue_id]-*.md` を恒久ドキュメントへ昇格するか、既存 docs に統合するかを判定する。

判定軸:

- 新規機能・新規 ADR 相当の決定 → 恒久 docs（`docs/adr/` ほか）へ昇格
- 既存 docs の更新で吸収可能 → 既存 docs を更新（昇格しない）
- 設計の決定が draft 段階のまま留めるべき軽微な変更 → 昇格不要

## Step 7 詳細: Issue 本文の完了条件更新

### PASS の場合

Issue 本文のチェックボックスを `[x]` に更新する。

```bash
# 本文を取得
kaji issue view [issue_id] --json body -q '.body' > /tmp/issue-body.md

# チェックボックスを更新（確認済み条件を [x] に変更）
# 例: sed -i 's/- \[ \] 条件A/- [x] 条件A/' /tmp/issue-body.md

# 更新を反映
kaji issue edit [issue_id] --commit --body-file /tmp/issue-body.md
```

### BACK の場合

チェックボックスは `[ ]` のまま残す。コメントで未充足条件と戻し先を明示する。

### RETRY の場合

本文更新は行わない（軽微修正後に再実行するため）。

## Step 7.5 詳細: 設計書の Issue 本文添付

Step 7（完了条件更新）の後、Step 8（最終チェックコメント）の前に、設計書を Issue 本文の NOTE ブロック直下に添付する。

### 7.5-1. 冪等性チェック

```bash
kaji issue view [issue_id] --json body -q '.body' | grep -q '^## 設計書'
```

既に `## 設計書` セクションが存在する場合はスキップする（位置の移動はしない）。

### 7.5-2. 添付対象の決定

| 条件 | 添付対象 |
|------|----------|
| Step 6 で恒久 docs へ昇格を実施した | 昇格後の確定版（`docs/...` 配下） |
| 昇格対象外 | `draft/design/` 版 |

### 7.5-3. 添付位置の判定（NOTE 直下挿入ルール）

Issue 本文を行単位で走査し、以下のルールで挿入位置を決定する:

| ケース | 挿入位置 |
|--------|----------|
| NOTE ブロックが1つ存在する（標準） | NOTE ブロック終端の次の空行の後 |
| NOTE ブロックが複数存在する | **最初の** NOTE ブロック終端の次の空行の後 |
| NOTE ブロックが存在しない（古い Issue） | 本文先頭 |
| 既に `## 設計書` が別位置に存在する | **スキップ**（7.5-1 で検出済み） |

> NOTE ブロックの終端判定: `> [!NOTE]` から始まり、`> ` プレフィックスの連続行が途切れた最初の空行。

### 7.5-4. 添付フォーマット

**昇格済みの場合:**
```markdown
## 設計書

恒久ドキュメントとして昇格済み: [`docs/...`](https://github.com/apokamo/kaji/tree/main/docs/...)
```

**未昇格の場合:**
```markdown
## 設計書

<details>
<summary>クリックして展開</summary>

(設計書全文)

</details>
```

### 7.5-5. Issue 本文への挿入

```bash
# 本文を取得
BODY=$(kaji issue view [issue_id] --json body -q '.body')

# NOTE ブロック終端位置を検出し、その次の空行の後に挿入
# new_body = body[:insert_at] + 設計書セクション + body[insert_at:]
kaji issue edit [issue_id] --commit --body-file /tmp/issue-body-updated.md
```

### 7.5-6. フォールバック

本文サイズ上限超過等で `kaji issue edit` に失敗した場合:

1. Issue **コメント**に設計書全文を投稿する
2. 本文には `## 設計書` セクションとコメントへのリンクのみを追記する
3. フォールバック発生時も **PASS 扱い**（設計書自体は参照可能であり、添付位置の問題に過ぎないため）

## Step 8 詳細: 最終チェックコメントのテンプレート

**verdict マーカーの無条件付与（必須）**: 最終チェック結果コメントには **常に** `--verdict-step final-check --verdict-status <STATUS>` を付与する。`<STATUS>` は本 skill が返す status（`PASS` / `RETRY` / `BACK_DESIGN` / `BACK_IMPLEMENT` / `BACK`。旧 YAML 互換で `BACK` に丸める場合は `BACK`）に置換する。CLI が body 1 行目に `<!-- kaji-verdict: step=final-check status=<STATUS> -->` を決定的に付与し、`issue-design` Step 1.6 の BACK 再入検出は `BACK` / `BACK_DESIGN` マーカーのみを design 再入として数える（契約の正本は CLI コード。ADR 008 決定 3）。「BACK のときだけ付ける」条件付き出力は禁止（決定 3。`PASS` / `RETRY` でも常に付ける）。`--body-file -` は stdin から body を読むため、フラグは以下の順で付与する。

```bash
kaji issue comment [issue_id] --commit \
  --verdict-step final-check --verdict-status <STATUS> \
  --body-file - <<'EOF'
## 最終チェック結果

### 前段証跡の確認

| ステップ | コメント有無 | 最新判定 |
|----------|------------|---------|
| issue-design | ✅ | 設計書作成済み |
| issue-review-design | ✅ | Approve |
| (fix-design → verify-design) | (経由した場合) | (Approve) |
| issue-implement | ✅ | テスト全件 PASSED（または baseline 一致） |
| issue-review-code | ✅ | Approve |
| (fix-code → verify-code) | (経由した場合) | (Approve) |

### 完了条件の充足状態

| 条件 | 充足 | 確認元 |
|------|------|--------|
| (条件1) | ✅ / ❌ | (どのステップ/コメントで確認) |
| (条件2) | ✅ / ❌ | (どのステップ/コメントで確認) |

### 品質ゲート

| ゲート | 結果 | 備考 |
|--------|------|------|
| `make check` | PASS / FAIL | ruff / format / mypy / pytest 一括 |
| 変更タイプ固有検証 | PASS / FAIL / N/A | 例: `make verify-docs` / `make verify-packaging` |

### docs 整合

- 設計書昇格: 実施 (`docs/...`) / 不要
- docs 更新: 実施 / 不要

### Issue 本文更新

- チェックボックス更新: 実施 / 不要
- 設計書添付: 実施 / スキップ（既存） / フォールバック（コメント投稿）

### 判定

PASS / RETRY / BACK_DESIGN / BACK_IMPLEMENT / BACK
EOF
```

> 後方互換: 旧 workflow YAML が `BACK` のみを valid とする場合は `BACK` を返す。詳細は § Verdict 出力 § workflow YAML 互換ルール。

## Verdict 出力

```text
---VERDICT---
status: PASS
reason: |
  dev workflow の最終チェックを完了し、PR に進める状態を確認した
evidence: |
  前段証跡を集約し、全完了条件の充足を確認した。Issue 本文のチェックボックスを更新済み
suggestion: |
---END_VERDICT---
```

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 全完了条件が充足し、Issue 本文更新済み |
| RETRY | final-check 文脈で閉じる軽微修正が必要 |
| BACK_DESIGN | 設計起因の不足（影響ドキュメント評価漏れ / テスト戦略未定義 / 要件解釈の食い違い 等）。`design` に戻す。**`final-check.on` に `BACK_DESIGN` が定義されている YAML でのみ使用** |
| BACK_IMPLEMENT | 実装起因の不足（前段コメント欠落 / 品質ゲート未通過 / docs 更新漏れ 等）。`implement` に戻す。**`final-check.on` に `BACK_IMPLEMENT` が定義されている YAML でのみ使用** |
| BACK | 旧 YAML（`BACK` のみ valid）における差し戻し。**後方互換のため残置**。未充足条件と戻し先を `suggestion` に明示 |
| ABORT | 重大な前提不整合 |

> `BACK_DESIGN` / `BACK_IMPLEMENT` / `BACK` / `ABORT` はいずれも `suggestion` フィールド必須。harness 側の `verdict.py` が空 suggestion を `VerdictParseError` で弾く。

### workflow YAML 互換ルール

`i-dev-final-check` は複数 workflow から呼ばれるため、skill 側は **prompt 経由で注入される
valid status 一覧（`prompt.py:75, 92-97` の `valid_statuses = list(step.on.keys())`）を
権威ある情報源として扱う**。prompt が「使ってよい」と言った status のみ返すことで多 workflow
互換性が成立する。

| 呼び出された workflow の `final-check.on` キー | skill が返すべき status |
|------------------------------------------------|-------------------------|
| `BACK_DESIGN` と `BACK_IMPLEMENT` の両方が定義（例: `dev.yaml`） | root-cause を判定して `BACK_DESIGN` / `BACK_IMPLEMENT` を使い分け。無印 `BACK` は使わない |
| `BACK` のみが定義（例: `implement-to-pr.yaml`） | 従来通り `BACK` を返す（root-cause 判定結果に関わらず YAML 制約に従う） |
| BACK 系が一切未定義（例: `dev-local.yaml`） | BACK 系を返さない。`RETRY`（軽微修正）または `ABORT`（重大な前提不整合）で表現 |
| `BACK_DESIGN` のみ / `BACK_IMPLEMENT` のみ定義 | **想定外構成**。`ABORT` を返し、運用に workflow YAML の見直しを促す |

prompt と skill 出力に不整合が生じた場合（valid status に無い status を返した場合）、harness
の `InvalidVerdictValue` で弾かれる。skill 側はあくまで prompt 内 valid status のみ返す。
