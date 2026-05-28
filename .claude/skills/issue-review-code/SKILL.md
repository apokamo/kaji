---
description: 実装完了後の成果物に対し、設計整合性とコード品質の観点から厳格なレビューを実施する
name: issue-review-code
---

# Issue Review Code

> **重要**: このスキルは実装/設計を行ったセッションとは **別のセッション** で実行することを推奨します。
> 同一セッションで実行すると、実装時のバイアスがレビュー判断に影響する可能性があります。

実装コードに対して、設計書を基に厳格なコードレビューを実施します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| `/issue-implement` 完了後 | ✅ 必須 |
| 実装途中 | ⚠️ 任意（中間レビューとして） |

**ワークフロー内の位置**: implement → **review-code** → (fix → verify) → i-dev-final-check → i-pr → close

## 入力

### ハーネス経由（コンテキスト変数）

**常に注入される変数:**

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 正規化済み Issue ID（GitHub 数値または local ID） |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`、local では bare ID） |
| `step_id` | str | 現在のステップ ID |

**条件付きで注入される変数:**

| 変数 | 型 | 条件 | 説明 |
|------|-----|------|------|
| `cycle_count` | int | サイクル内ステップのみ | 現在のイテレーション番号 |
| `max_iterations` | int | サイクル内ステップのみ | サイクルの上限回数 |

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

1. **開発ワークフロー**: `docs/dev/development_workflow.md`
2. **テスト規約**: `docs/dev/testing-convention.md`
3. **Python スタイル**: `docs/reference/python/python-style.md`（必要に応じて他の `docs/reference/python/*.md` も追加読込）

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: コンテキストの取得

1. [_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、Worktree の絶対パスを取得。

2. **設計情報の取得**:
   ```bash
   cat [worktree_dir]/draft/design/issue-[issue_id]-*.md
   ```

3. **実装サマリーの取得**:
   ```bash
   kaji issue view [issue_id] --comments
   ```
   直近の「実装完了報告」を確認。Baseline Check コメントの有無もここで把握する。

4. **実装差分の取得**:
   ```bash
   cd [worktree_dir] && git diff main...HEAD
   ```
   変更内容を把握。差分が大きい場合は主要ファイルを個別に確認。

### Step 1.4: Pre-Handoff Review 証跡の存在チェック（hard gate）

`/issue-implement` Step 8.5 で生成される `## Pre-Handoff Review` セクション（経路情報を含む）が Issue コメントに存在することを機械的に確認する。**実装側のゲートが Codex / Gemini 等で skill markdown を誤読・省略してサイレントにバイパスされていないか**を、レビュー側でハードチェックする責務。

```bash
PHR_COUNT=$(kaji issue view [issue_id] --comments 2>/dev/null | grep -c '^## Pre-Handoff Review$')
PHR_ROUTE_COUNT=$(kaji issue view [issue_id] --comments 2>/dev/null | grep -cE '^- \*\*経路\*\*:')
```

**判定**:

- `PHR_COUNT == 0` または `PHR_ROUTE_COUNT == 0` → **BACK_IMPLEMENT**（→ implement step）。`/issue-implement` Step 8.5 が未実施 / 出力欠落と判断し、以下を Must Fix として投稿してレビューに入らない:
  - 「Pre-Handoff Review コメントが Issue に存在しない（または `経路:` 行が無い）。`/issue-implement` を再実行し、Step 8.5 を完了してから再度 review に渡すこと。」
- `PHR_COUNT ≥ 1` かつ `PHR_ROUTE_COUNT ≥ 1` → Step 1.5 に進む

> **趣旨**: 本 Issue (gl:9) で導入した pre-handoff review の自己評価バイパスを抑える hard boundary。
> capability 判定（subagent / self-check）が機能しないランタイムでも、review-code 側で必ず止まる。

### Step 1.5: 独立テスト実行（必須）

レビュワー自身が独立した環境でテストを実行し、結果を確認する。
実装者の報告だけに依存せず、テスト結果を独自に検証することが目的。

1. **Baseline Check コメントの確認**:
   Step 1.3 で取得した Issue コメント群から、最新の `## Baseline Check 結果` を検索する。
   複数存在する場合は **最新のコメントを正** とする（commit hash で識別）。

2. **Lint / Format / 型チェック（exit 0 必須）**:
   ```bash
   cd [worktree_dir] && source .venv/bin/activate && ruff check kaji_harness/ tests/ && ruff format --check kaji_harness/ tests/ && mypy kaji_harness/
   ```

3. **テスト実行（個別）**:
   ```bash
   cd [worktree_dir] && source .venv/bin/activate && pytest
   ```
   **`pytest` は `&&` チェーンに含めず、必ず個別に実行する。** baseline failure が残っていると exit 非 0 になるため、チェーンに含めると後続の判定に到達できない。

4. **合否判定**:
   - **Baseline Check コメントがない場合**:
     - 全コマンドが exit 0 でなければ **Changes Requested**（従来どおり）
   - **Baseline Check コメントがある場合**:
     - ruff check / ruff format / mypy: exit 0 必須（変更なし）
     - pytest: FAILED/ERROR を baseline 一覧と照合する
       - 比較キー `(nodeid, kind, error_type)` が baseline と完全一致 → 除外
       - 不一致の新規 FAILED/ERROR → **Changes Requested**
       - baseline failure のみ残っている → テスト合否は OK とする

5. テスト総数、passed/failed/errors/skipped を記録しておく（Step 3 のコメントに含める）。

> 最終ゲートは `i-dev-final-check` で `make check` を再実行する。review-code はレビュワーが
> 独立に軽量ゲートを通し、実装者の提示した品質チェック証跡も突き合わせる位置づけ。

### Step 2: コードレビューの実施

#### type の取得

Issue ラベルから type を取得する（複数 type ラベルを許容しないため、配列として取得して cardinality をチェックする）:

```bash
kaji issue view [issue_id] --json labels --jq '[.labels[].name] | map(select(startswith("type:")))'
```

**判定の優先順**:

1. **配列要素数 ≥ 2** → 複数 type ラベル付与。コードレビューに入らず、`/issue-review-ready` への差し戻しを Must Fix として投稿する（type ラベルは 1 つに限定する責務）
2. **配列が空** → type ラベル未付与。コードレビューに入らず、`/issue-review-ready` への差し戻しを Must Fix として投稿する（前段レディネスで type ラベル付与を確保する責務）
3. **配列要素数 1**: その要素を採用し、以下の判定を行う:
   - **canonical（`type:feature` / `type:bug` / `type:refactor` / `type:docs`）** → 対応する追加観点を適用
   - **canonical 外（`type:test` / `type:chore` / `type:perf` / `type:security` など）** → `type:feature` と同等に扱う（フォールバック規則）

#### type 別追加観点

共通観点（1〜4、下記）に加えて、type 別に以下を確認する。

| 観点 | feat | bug | refactor | docs |
|------|:----:|:---:|:--------:|:----:|
| A. **IF 契約の忠実性** — 設計書「インターフェース」「使用例」どおりの IF になっているか。型・命名・戻り値・エラー挙動 | ✅ | — | — | — |
| B. **再現テストの存在と Red→Green の証跡** — 設計書「再現手順」に対応する再現テストが存在し、実装前 FAIL / 実装後 PASS のログが実装完了報告に含まれているか | — | ✅ | — | — |
| C. **同根欠陥の波及修正** — 設計書「根本原因」で列挙された他の壊れ箇所が同時に修正されているか | — | ✅ | — | — |
| D. **振る舞い非変更の保証** — 既存テスト全件 PASS + safety net テストが追加されているか。`git diff` に機能追加・挙動変更が混入していないか | — | — | ✅ | — |
| E. **改善指標の達成** — ベースライン計測値 / 改修後計測値が Issue コメントに含まれ、設計書「改善指標」を達成しているか | — | — | ✅ | — |
| F. **Scope 混在禁止** — type の責任範囲を超える変更が混入していないか（feat に fix/refactor、bug に feat/refactor、refactor に feat/fix 等） | ✅ | ✅ | ✅ | — |

> **観点 B の escape clause（実ログによる実装前 Red 代替）**: bug Issue 本文またはリンク先に OB を直接示す実世界障害ログ（失敗コマンド・エラー文言・exit code・API 応答・関連 Issue/PR の実行ログ等）が存在し、恒久回帰テストがその OB に対応する EB を検証している場合、その実ログを実装前 Red 証跡の代替として扱う。この場合、実装前 FAIL ログが実装完了報告に無いことのみを理由に観点 B を ❌ としてはならない（合成 Red 欠如だけを理由に差し戻さない）。ただし修正後の回帰テスト Green・影響範囲の品質ゲート（観点 F 等）・同根欠陥確認（観点 C）は免除しない。実ログが OB と対応しない場合、単なる省力化・実行時間短縮・後付け都合を理由とする場合は代替不可。

**type=docs の扱い**: docs-only の review は `/i-doc-review` が正本。本スキルに来るのは誤経路 → `/i-doc-review` への差し戻しを検討。

**type 判定不能の場合**: 上記「判定の優先順」で配列要素数 ≥ 2 または空だった場合、レビューに入らず `/issue-review-ready` への差し戻しを求める。

#### 共通観点（type 非依存）

以下の観点で厳格なレビューを行う。

1. **設計との整合性**:
   - 設計書の要件を完全に満たしているか？
   - 勝手な仕様変更や、未実装の機能はないか？

2. **安全性と堅牢性**:
   - エラーハンドリングは適切か？（握りつぶし、汎用 Exception の禁止）
   - 境界値（Boundary Value）や Null 安全性の考慮はあるか？

3. **コード品質**:
   - 型ヒントは具体的か？ (`Any` の乱用禁止)
   - 命名は適切で説明的か？
   - CLAUDE.md および `docs/reference/python/*` のコーディング規約に準拠しているか？

4. **テスト**:
   - 追加された機能に対するテストは十分か？
   - 設計書の「テスト戦略」と実装テストが対応しているか？
   - **変更タイプに応じた検証チェック（必須）**:
     - [ ] 実行時コード変更なら、設計書で定義した Small / Medium / Large が実装・PASSED か
     - [ ] docs-only / metadata-only / packaging-only 変更なら、設計書で定義した変更固有検証が実施済みか
     - [ ] 恒久テストを追加しない理由が `docs/dev/testing-convention.md` と矛盾していないか
     - [ ] pytest 出力（および baseline 比較結果）が Issue コメントに含まれているか
   - テスト / 検証未実施の場合: 設計レビューで承認済みでない限り **Changes Requested**
   - pytest 出力がない場合は **Changes Requested**

### Step 2.5: 完了条件の段階確認

Issue 本文に `## 完了条件` セクションがある場合、コードレビュー段階で確認可能な条件を確認する。

確認対象の例:
- 実装が設計書と整合し、完了条件で求められている機能を網羅しているか
- テスト結果（S/M/L）が完了条件のテスト要件を満たしているか
- docs 更新が完了条件の要求に対応しているか

確認結果は Step 3 の Issue コメントに含めて後段への証跡とする。

### Step 3: レビュー結果のコメント投稿

```bash
kaji issue comment [issue_id] --commit --body "$(cat <<'EOF'
# コードレビュー結果

## 概要

(一言で言うとどうだったか)

## 独立テスト実行結果

| 項目 | 結果 |
|------|------|
| ruff check / ruff format --check / mypy | PASS / FAIL |
| pytest 総数 / passed / failed / errors / skipped | XX / XX / XX / XX / XX |
| baseline failure 一致 | YY 件（Issue: ___ ） |
| 新規 FAILED/ERROR (regression) | 0 件 |

## type 別追加観点の判定

判定対象 type: `type:___`

| 観点 | 該当 | 判定 | 根拠 |
|------|:---:|:---:|------|
| A. IF 契約の忠実性 | feat | ✅ / ❌ / — | (根拠) |
| B. 再現テスト Red→Green | bug | ✅ / ❌ / — | (根拠。実ログ代替を適用した場合はその旨と OB を示す実ログの所在を明記) |
| C. 同根欠陥の波及修正 | bug | ✅ / ❌ / — | (根拠) |
| D. 振る舞い非変更の保証 | refactor | ✅ / ❌ / — | (根拠) |
| E. 改善指標の達成 | refactor | ✅ / ❌ / — | (根拠) |
| F. Scope 混在禁止 | feat/bug/refactor | ✅ / ❌ | (根拠) |

## 指摘事項 (Must Fix)

- [ ] **ファイル名:行数**: 具体的な指摘内容
- [ ] ...

## 改善提案 (Should Fix)

- **ファイル名**: より良い実装パターンの提案

## 良い点

- (特筆すべき良い実装があれば記載)

## 完了条件の段階確認

コードレビュー段階の完了条件に対する充足判定:

- [ ] (条件1): ✅ 実装・テストで確認 / ❌ 不足（理由）
- [ ] (条件2): ✅ / ❌

## 判定

[ ] Approve (修正なしでマージ可)
[ ] Changes Requested (要修正)
EOF
)"
```

### Step 4: 完了報告

```
## コードレビュー完了

| 項目 | 値 |
|------|-----|
| Issue | [issue_ref] |
| 判定 | Approve / Changes Requested |
| Must Fix | N 件 |
| Should Fix | M 件 |

### 次のステップ

- Approve: `/i-dev-final-check [issue_id]` で最終チェック
- Changes Requested: `/issue-fix-code [issue_id]` で修正
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

---VERDICT---
status: PASS
reason: |
  コード品質基準を満たしている
evidence: |
  設計整合性・テストカバレッジ・品質チェックすべて合格
suggestion: |
---END_VERDICT---

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | Approve |
| RETRY | Changes Requested |
| BACK | コードレビューで設計レベルの問題を発見（→ design）。**ただし当該 workflow の `review-code.on` に `BACK` key がある場合のみ発行可** |
| BACK_IMPLEMENT | Step 1.4 hard gate 発火（Pre-Handoff Review 証跡欠落）。implement Step 8.5 未実施（→ implement） |
| ABORT | 重大な問題（type ラベル未付与・複数付与等） |

> **valid_statuses の権威**: 発行可能な status は prompt 注入の `valid_statuses`（= 当該 workflow の `step.on.keys()`）が単一情報源。YAML の `on:` に存在しない status は返さないこと（参照: [`docs/dev/workflow-authoring.md`](../../../docs/dev/workflow-authoring.md) § `BACK_*` プレフィックス拡張）。`BACK` と `BACK_IMPLEMENT` は差し戻し先（design / implement）が異なるため、Step 1.4 の証跡欠落は必ず `BACK_IMPLEMENT` を用い、bare `BACK` は流用しない。
