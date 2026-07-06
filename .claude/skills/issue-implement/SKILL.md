---
description: 設計書（draft/design/）に基づき、TDD（テスト駆動開発）アプローチを用いて機能を実装する。
name: issue-implement
---

# Issue Implement

承認された設計書を元に、テストコードの作成から実装を開始します。
**Test-Driven Development (TDD)** の原則に従い、「テスト作成 (Red) → 実装 (Green) → リファクタリング」のサイクルを回します。

## いつ使うか

| タイミング | このスキルを使用 |
|-----------|-----------------|
| 設計レビュー完了・承認後 | ✅ 必須 |
| 設計レビュー未完了 | ❌ 待機 |

**ワークフロー内の位置**: design → review-design → **implement** → review-code → i-dev-final-check → i-pr → close

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

1. **開発ワークフロー**: `docs/dev/development_workflow.md`
2. **完了条件対応**: `docs/dev/workflow_completion_criteria.md`
3. **docs 更新判断**: `docs/dev/documentation_update_criteria.md`
4. **テスト規約**: `docs/dev/testing-convention.md`
5. **Python スタイル**: `docs/reference/python/python-style.md`（必要に応じて他の `docs/reference/python/*.md` も追加読込）

## 前提条件

- `/issue-start` が実行済みであること
- `/issue-design` で設計書が作成済みであること
- 設計レビューが完了・承認されていること

## 共通ルール

- [_shared/report-unrelated-issues.md](../_shared/report-unrelated-issues.md) — 作業中に発見した無関係な問題の報告ルール

## 実行手順

### Step 1: Worktree 情報の取得

[_shared/worktree-resolve.md](../_shared/worktree-resolve.md) の手順に従い、
Worktree の絶対パスを取得すること。以降のステップではこのパスを使用する。

### Step 2: 設計書の読み込み

```bash
cat [worktree_dir]/draft/design/issue-[issue_id]-*.md
```

**特に注目するセクション**:
- 「インターフェース」: 実装すべき API
- 「テスト戦略」: テストケースの元になる
- 「影響ドキュメント」: 実装後に更新が必要なドキュメント

### Step 2.5: Baseline Check

実装開始前にテスト環境の状態を確認し、変更前から存在する失敗（baseline failure）を記録する。

1. **pytest を実行する**:
   ```bash
   cd [worktree_dir] && source .venv/bin/activate && pytest
   ```

2. **全パスの場合**: baseline は clean。コメント不要。Step 2.6 へ進む。

3. **FAILED / ERROR がある場合**:
   a. 各失敗テストの `(nodeid, kind, error_type)` を記録する
   b. Issue コメントに以下のフォーマットで投稿する（commit hash を含める）:

   ````bash
   kaji issue comment [issue_id] --commit --body "$(cat <<'BASELINE_EOF'
   ## Baseline Check 結果

   ### 実行環境

   - **Commit**: [commit-hash]
   - **コマンド**: `pytest`

   ### Baseline Failure 一覧

   | nodeid | kind | error_type | 概要 |
   |--------|------|------------|------|
   | tests/test_foo.py::test_bar | FAILED | AssertionError | expected 1, got 2 |
   | tests/test_baz.py::test_qux | ERROR | ImportError | No module named 'xxx' |

   ### Regression 判定キー

   上記テーブルの `(nodeid, kind, error_type)` の3タプルを比較キーとする。
   以降の pytest 実行で:
   - 比較キーが一致する失敗 → baseline failure（既知）として除外
   - 比較キーが一致しない新規 FAILED/ERROR → regression

   ### 判定

   - **継続**: 上記は変更前から存在する失敗であり、本 Issue の対象外
   - **停止**: (該当する場合のみ記載)
   BASELINE_EOF
   )"
   ````

   c. **停止基準**に該当するか判断する:
      - baseline failure が本 Issue の実装対象と同一モジュール/機能に影響する場合
      - 失敗数が多く、regression の切り分けが困難な場合（目安: 10 件超）
   d. 継続する場合: 以降の regression 判定は baseline failure を除外して行う

> **Baseline コメントの選択規則**: Issue に `## Baseline Check 結果` コメントが複数存在する場合（再実行時など）、**最新のコメントを正とする**。各コメントに commit hash を含めることで、どの時点のスナップショットかを識別できる。

### Step 2.6: type の判定と type 別手順ガイドの読み込み

Issue ラベルから type を取得する（複数 type ラベルを許容しないため、配列として取得して cardinality をチェックする）:

```bash
kaji issue view [issue_id] --json labels --jq '[.labels[].name] | map(select(startswith("type:")))'
```

**判定の優先順**:

1. **配列要素数 ≥ 2** → 複数 type ラベル付与。実装フェーズに入らず処理を停止し、`/issue-review-ready` への差し戻しを案内する（ABORT）。type ラベルは Issue ごとに 1 つに限定する責務。
2. **配列が空** → type ラベル未付与。実装フェーズに入らず処理を停止し、`/issue-review-ready` への差し戻しを案内する（ABORT）。前段レディネスで type ラベル付与を確保する責務。
3. **配列要素数 1**: その要素を採用し、以下の判定を行う:
   - **`type:docs`** → **本スキル対象外**。`/i-doc-update` を使用すること。処理を停止し、ユーザーに誘導する（ABORT）
   - **canonical（`type:feature` / `type:bug` / `type:refactor`）** → 対応するファイルを Read
   - **canonical 外（`type:test` / `type:chore` / `type:perf` / `type:security` など）** → `feat.md` を Read（フォールバック規則）

| type | 読み込むファイル | 手順の特徴 |
|------|------------------|-----------|
| `type:feature` | `.claude/skills/_shared/implement-by-type/feat.md` | 標準 TDD（Red → Green → Refactor）。IF 定義とユースケースを契約として実装 |
| `type:bug` | `.claude/skills/_shared/implement-by-type/bug.md` | 再現テスト先行。Red = 再現テストが OB を再現 / Green = EB に合致 |
| `type:refactor` | `.claude/skills/_shared/implement-by-type/refactor.md` | ベースライン計測 → safety net → 改修 → 再計測。振る舞い非変更が絶対要件 |
| canonical 外 | `.claude/skills/_shared/implement-by-type/feat.md`（フォールバック） | 標準 TDD を適用 |

**type 別手順と Step 3〜5 の関係**:
- 読み込んだガイドは Step 3（Red）〜 Step 5（Refactor）の**具体的な進め方**を定義する
- 下記 Step 3〜5 の汎用的な記述は、type 別ガイドで上書きされる箇所がある（例: bug の Step B1 は下記 Step 3 の「Red Phase」を「再現テスト先行」に具体化する）
- ガイドの手順に従って作業し、下記 Step 3〜5 は**枠組み**として参照する

### Step 3: テスト実装 (Red Phase)

> **CRITICAL — 変更タイプに応じて妥当な検証を選ぶこと**
>
> 実行時コード変更では、都合よく S/M/L を減らさないこと。
> 一方で docs-only / metadata-only / packaging-only 変更に対し、
> 価値の低い恒久テストを機械的に追加してはならない。
>
> **禁止事項**:
> - ❌ 設計書で「作成する」と定義されたテストを省略する
> - ❌ 設計書で「不要」と判断されたテストを独自判断で追加する
> - ❌ 実行時コード変更なのに「実行時間が長い」を理由に M/L を省略する
> - ❌ 実行時コード変更なのに「Small で十分」と決め打ちする
> - ❌ docs-only / metadata-only / packaging-only 変更に無理やり S/M/L テストを新設する
> - ❌ `uv pip install -e .` など副作用のある検証を shared 環境へ常設する
> - ❌ 「API キーがない」「DB が起動していない」などの環境不備を理由にテストをスキップする（環境不備はスキップ理由ではなく修正対象）

設計書の「テスト戦略」セクションに基づき、変更タイプに応じた検証を実施する。テストサイズ判定は
`docs/dev/testing-convention.md` のリソース制約（外部 API / DB / ファイル I/O の有無）に従う。

1. **テストファイルの特定/作成**:
   - 実行時コード変更: `tests/` 配下の適切な場所にテストファイルを作成または特定
   - docs-only / metadata-only / packaging-only: 変更固有検証のみで十分なら、新規テストは作成しない
   - 各テストには `@pytest.mark.small` / `@pytest.mark.medium` / `@pytest.mark.large` のマーカーを付与

2. **テストコード記述**:
   - 実行時コード変更: 設計書の「テスト戦略」をカバーするテストケースを書く（不要と判断されたサイズは設計書側のエビデンスを維持）
   - docs-only / metadata-only / packaging-only: 設計書に記載した変更固有検証を実施する

3. **失敗 / 回帰の確認**:
   - 実行時コード変更: Red Phase として失敗を確認する
   - docs-only / metadata-only / packaging-only: 新規テストを追加しない場合、既存テストに回帰がないかを確認するステップとして扱う
   ```bash
   cd [worktree_dir] && source .venv/bin/activate && pytest
   ```

### Step 4: 機能実装 (Green Phase)

1. **実装ファイルの編集**:
   - 設計書の「インターフェース定義」に従い、`kaji_harness/` 配下のコードを実装

2. **テスト通過確認**:
   ```bash
   cd [worktree_dir] && source .venv/bin/activate && pytest
   ```

   pytest の合否判定基準:
   - **Baseline Check コメントがない場合**: 全テスト PASSED を期待（従来どおり）
   - **Baseline Check コメントがある場合**:
     1. FAILED/ERROR のテストを baseline failure 一覧と照合する
     2. 比較キー `(nodeid, kind, error_type)` が baseline と一致 → 既知（除外）
     3. 比較キーが不一致の新規 FAILED/ERROR → regression（修正が必要）
     4. baseline にあったが消えた（PASSED に変わった）→ 問題なし

### Step 5: リファクタリング

- コードの可読性を高める修正を行う
- テストが引き続きパスすることを確認

### Step 6: ドキュメント更新

設計書の「影響ドキュメント」セクションで「あり」のドキュメントを更新する。

### Step 7: 品質チェック（コミット前必須）

以下の 2 段階で実行すること。**すべての基準をクリアするまでコミットしてはならない**。

> AGENTS.md の pre-commit 契約（`make check`）と等価。`make check` を
> 一括で叩く方法もあるが、kaji では baseline failure 判定のため `pytest` を `&&` チェーンから
> 切り離す必要がある。下記 7a / 7b の分離はそのための運用。

#### 7a. Lint / Format / 型チェック（exit 0 必須）

```bash
cd [worktree_dir] && source .venv/bin/activate && ruff check kaji_harness/ tests/ && ruff format --check kaji_harness/ tests/ && mypy kaji_harness/
```

ruff / mypy は全パス必須。baseline failure の概念を適用しない。

> `ruff format --check` は非破壊 gate（`make check` と等価）。整形差分で FAIL
> した場合は `make fmt`（または `ruff format kaji_harness/ tests/`）で整形し、
> 生じた差分をコミット対象に含めてから再チェックすること。gate 実行自体では
> worktree を書き換えない。

#### 7b. テスト実行

```bash
cd [worktree_dir] && source .venv/bin/activate && pytest
```

**`pytest` は `&&` チェーンに含めず、必ず個別に実行する。** baseline failure が残っていると exit 非 0 になるが、以下の基準で合否を判定する:

- **Baseline Check コメントがない場合**: 全テスト PASSED 必須（exit 0 でなければ NG）
- **Baseline Check コメントがある場合**: Step 4 と同じ regression 判定基準を適用する
  - FAILED/ERROR を baseline 一覧と照合し、比較キー `(nodeid, kind, error_type)` が全一致 → OK（コミット可）
  - 比較キーが不一致の新規 FAILED/ERROR が 1 件でもある → NG（修正が必要）

失敗した場合は原因を修正して再実行すること。

### Step 7.5: 完了条件の段階確認

Issue 本文に `## 完了条件` セクションがある場合、実装段階で確認可能な条件を確認する。

確認対象の例:
- 実装が完了条件で求められている機能を網羅しているか
- テストが完了条件で求められているカバレッジを満たしているか
- docs 更新が完了条件で求められている範囲に対応しているか

確認結果は Step 9 の Issue コメントに含めて後段への証跡とする。

### Step 8: コミット

```bash
cd [worktree_dir] && git add . && git commit -m "feat: implement [feature] for [issue_ref]"
```

> 実装の commit prefix は Issue type に対応させる（`type:bug` なら `fix:`、`type:feature` なら `feat:` 等）。

### Step 8.5: Pre-Handoff Review（MANDATORY）

handoff 直前（`/issue-review-code` への進行前）に「設計書整合・テスト証跡・Scope 混在・auto-close 規約」を第三者視点で検査する。Step 8（コミット）の後に配置されるため、入力に必要な `git diff main...HEAD` と対象 commit hash がいずれも取得可能である。**重複チェックリストは作成しない**。`/issue-review-code` SKILL.md を rubric の単一情報源として参照しつつ、capability に応じて subagent / self-check に分岐する。

#### Step 8.5.1: capability 判定と分岐（1 方式に固定）

main session は以下のフローを実行する。**Agent tool 利用可否の試行を唯一の分岐条件**とする（環境変数や agent context には依存しない）。

1. main session は `kaji-code-reviewer` subagent を **Agent tool で起動** するよう試行する
   - **起動成功**（subagent からの応答テキストが取得できる）→ 経路: `subagent`
   - **Agent tool が利用不可**（Codex / Gemini 等で tool が未定義 / 起動失敗 / `.claude/agents/kaji-code-reviewer.md` が未ロード）→ 経路: `self-check (subagent unavailable, fallback)`

#### Step 8.5.2: 経路別の実行

##### 経路 A: subagent（Claude Code セッション）

1. main session が以下を **事前取得** する（subagent は Bash を持たないため、テキストとして prompt 注入する）:
   - `git diff main...HEAD` の全文
   - 直近 `pytest` の出力（Step 7b で取得済み）
   - 直近 `ruff check` / `ruff format --check` / `mypy` の出力（Step 7a で取得済み）
   - 最新の `## Baseline Check 結果` コメント（あれば）
   - 対象 commit hash

2. Agent tool で `subagent_type: "kaji-code-reviewer"` を指定して起動する。prompt は以下のテンプレートに沿って組み立てる。

   > **入力契約の SoT**: 各セクション（`## Diff` / `## Test Output` / `## Quality Check` / `## Baseline Failures` / 対象 commit / 設計書パス）の意味と必須/省略可規約は `.claude/agents/kaji-code-reviewer.md` § 入力（prompt 経由で受領）が単一情報源。下記テンプレートはそのミラー — 契約を変更する際は agent markdown 側を先に更新し、本テンプレートを後追いで合わせること。

   ````markdown
   # Pre-Handoff Code Review (kaji workflow)

   - 対象 commit: <git-sha>
   - 設計書: <worktree_dir>/draft/design/issue-<id>-*.md
   - Issue: [issue_ref]

   ## Diff

   ```
   (git diff main...HEAD の全文)
   ```

   ## Test Output

   ```
   (pytest の出力)
   ```

   ## Quality Check

   ```
   (ruff check / ruff format --check / mypy の出力)
   ```

   ## Baseline Failures

   (Baseline Check コメント本文を貼り付け。無ければ「無し」と明記)
   ````

3. subagent が返した Markdown 出力を Step 8.5.5 で専用の Issue コメントとして投稿する（main session が投稿経路を担う）。

##### 経路 B: self-check（Codex / Gemini 等の非対応 agent）

1. main session 自身が `.claude/agents/kaji-code-reviewer.md` を `Read` ツールで読み込み、同 markdown 内の rubric を **main session 内で適用** する。
2. 上記 § 経路 A の入力情報（diff / test output / quality check / baseline）を自セッションで参照しながら、`kaji-code-reviewer.md` § 出力形式に沿った Markdown を生成する。
3. 経路情報を `self-check (subagent unavailable, fallback)` と明記して Step 8.5.5 で専用の Issue コメントとして投稿する。

> **判定不能の場合の扱い**: capability 試行が成功も失敗も判別できない（response がタイムアウト等で取得できない）場合は、安全側に倒して経路 B（self-check）を実行する。経路情報には `self-check (subagent ambiguous, fallback)` と明記する。

#### Step 8.5.3: verdict ループと階層分離

subagent / self-check が返す verdict は **pre-handoff の自己評価** であり、kaji workflow の正式 verdict ではない。

| verdict | 取り扱い |
|---------|---------|
| `Yes` | handoff 可。Step 9（実装完了報告）へ進む（実装コミットは Step 8 で確定済み）|
| `With fixes` | main session が指摘事項を反映 → Step 7a / 7b を再実行 → `git add` で修正をステージ → `git commit --amend --no-edit` で実装コミットを更新 → 本 Step 8.5 を再実行（ループ）|
| `No` | 大幅な修正が必要。main session が修正 → Step 7a / 7b を再実行 → `git add` で修正をステージ → `git commit --amend --no-edit` で実装コミットを更新 → 本 Step 8.5 を再実行 |
| verdict 行が出力されない / parse 不能 | subagent が `maxTurns` 打ち切り等で `Pre-Handoff Review Verdict` セクション、または `Yes` / `No` / `With fixes` のいずれも返さなかった場合。**一度だけ** 経路 B（self-check）に fallback して再実行する。経路情報は `self-check (subagent unparseable, fallback)` と明記。fallback でも parse 不能なら main session 出力の全文を Issue コメントに転記し verdict 欄を `No`（修正必須）扱いとして次のループに進める。**無限再起動を避けるため、subagent 経路への再 fallback はしない** |

> **`--amend` を採る理由**: 実装を単一の実装コミット（Issue type に対応する prefix）に保ち、ループのたびに `git diff main...HEAD` と対象 commit hash が「修正反映後の現在状態」を表すようにする。worktree は未 push の feature branch なので amend は安全。**`--amend` 前の `git add` 必須**: amend はデフォルトで index の内容を使うため、修正を `git add` でステージしないと amend 後のコミットに反映されず、`git diff main...HEAD` と対象 commit hash が修正前を指したまま同じ指摘が再発する。

**ループ制限**: `With fixes` / `No` を 3 回連続で返した場合は abort せず、main session が修正方針を Issue コメントに整理して `/issue-review-code` 側で BACK 相当の判定に委ねる（pre-handoff の自己評価ループでは正式 verdict を出さない）。

**ループ回数の機械的カウント（必須）**: main session が自セッション内のカウンタで自己申告するのではなく、**Issue コメントの `## Pre-Handoff Review` セクション数を実際に数える**。Step 8.5.5 が本 Step 8.5 の実行（＝各ループ試行）ごとに専用の `## Pre-Handoff Review` コメントを投稿するため、Issue コメントが永続的なカウンタとなる。

```bash
PHR_COUNT=$(kaji issue view [issue_id] --comments 2>/dev/null | grep -c '^## Pre-Handoff Review$')
```

- Step 8.5.5 で証跡コメントを投稿した**直後**に `PHR_COUNT` を再取得する。**投稿済み実数 `PHR_COUNT` が 3 以上** かつ 今回の自己評価 verdict が `Yes` 以外 → ループ制限到達。本 Step 8.5 をさらに繰り返さず、以下の申し送りコメントを `kaji issue comment` で投稿してから Step 9 へ進む:

  > **Pre-Handoff Review ループ制限到達**: `With fixes` / `No` が 3 回連続で返されました（Issue コメント上の `## Pre-Handoff Review` 件数 = N）。main session 側の自己評価ループでは収束しないため、`/issue-review-code` 側で BACK 相当の判定を求めます。未解消の指摘事項は最新の `## Pre-Handoff Review` コメント § 指摘事項 を参照してください。

- `PHR_COUNT` が 3 未満かつ verdict が `Yes` 以外 → 通常通り Step 7a / 7b に戻ってループを継続

> **趣旨**: ループに陥った当のセッション自身がカウンタを持つと「楽観バイアス」と同型の脆弱性を残す（過去 Issue: history）。Issue コメント側を権威ある回数情報源とすることで、main session の自己申告を経由しないカウントに置き換える。

| 階層 | verdict | 発行者 |
|------|---------|--------|
| 自己評価（本 Step） | `Yes` / `No` / `With fixes` | `kaji-code-reviewer` subagent または main-session self-check |
| 正式 verdict | `PASS` / `RETRY` / `BACK` / `ABORT` | `/issue-review-code`（次セッション推奨） |

#### Step 8.5.4: 出力フォーマット（Step 8.5.5 への引き継ぎ）

subagent / self-check 共通の出力は `.claude/agents/kaji-code-reviewer.md` § 出力形式に従う。経路情報を必ず先頭に明記:

```markdown
## Pre-Handoff Review

- **経路**: subagent / self-check (subagent unavailable, fallback)
- **起動 agent**: kaji-code-reviewer / main-session (capability=<agent_name>)
- **対象 commit**: <git-sha>

### 1. 設計書整合
- 判定: ✅ / ⚠️ / ❌
- 根拠: ...

### 2. テスト証跡
- 判定: ✅ / ⚠️ / ❌
- 根拠: ...

### 3. Scope 混在
- 判定: ✅ / ⚠️ / ❌
- 根拠: ...

### 4. auto-close 規約
- 判定: ✅ / ⚠️ / ❌
- 根拠: ...

### 指摘事項
- 指摘 1: ...
- 指摘 2: ...

### Pre-Handoff Review Verdict
- **Yes** / **No** / **With fixes**
```

> **規約遵守**: 本コメント本文に auto-close hazard pattern（`Clos(e[sd]?|ing)` / `Fix(e[sd]|ing)?` / `Resolv(e[sd]?|ing)` / `Implement(s|ing|ed)?` の直後 `#[0-9]`）を書かない。指摘参照は `指摘 N` / `Must Fix item N` / `point N` 形式に統一する（参照: [`docs/dev/shared_skill_rules.md`](../../../docs/dev/shared_skill_rules.md) § auto close keyword 回避規約）。

#### Step 8.5.5: Pre-Handoff Review 証跡投稿（MANDATORY）

本 Step 8.5 を実行するたび（＝各 verdict ループ試行ごと）に、その試行で生成した `## Pre-Handoff Review` ブロックを **専用の Issue コメントとして即座に投稿する**。これが PHR 出力の一次投稿経路であり、Step 9（実装完了報告）はこれを再投稿しない。

````bash
kaji issue comment [issue_id] --commit --body "$(cat <<'PHR_EOF'
(Step 8.5.4 のフォーマットで生成した「## Pre-Handoff Review」ブロックを全文貼り付け)
PHR_EOF
)"
````

投稿後、Step 8.5.3 の `PHR_COUNT` 再取得とループ制限判定を行う。1 試行 1 コメントで永続記録されるため、`PHR_COUNT` は within-run のループ試行回数の正しいカウンタとなる。`/issue-review-code` Step 1.4 の hard gate は `## Pre-Handoff Review` セクションの存在を確認するが、本ステップが投稿する専用コメントがこれを満たす。

### Step 9: Issueにコメント

実装完了をIssueにコメントします。pytest および品質チェックの出力をそのまま含めること。

````bash
kaji issue comment [issue_id] --commit --body "$(cat <<'COMMENT_EOF'
## 実装完了報告 (TDD)

設計に基づき、TDDにて実装を行いました。

### 実施内容

- **テスト / 検証**: `tests/test_xxx.py` に XX 件のケースを追加、または変更固有検証を実施
- **実装**: `kaji_harness/xxx.py` に機能を実装

### テスト結果

```
(pytest の標準出力をそのまま貼り付け)
```

| 項目 | 結果 |
|------|------|
| テスト総数 | XX |
| passed | XX |
| failed | XX (うち baseline: YY, regression: 0) |
| errors | XX (うち baseline: YY, regression: 0) |
| skipped | XX |
| Small テスト | XX passed |
| Medium テスト | XX passed |
| Large テスト | XX passed |

### 品質チェック結果

```
(ruff check + ruff format --check + mypy の出力をそのまま貼り付け)
```

### 変更ファイル

- `kaji_harness/xxx.py`: (変更内容)
- `tests/test_xxx.py`: (変更内容)

### Pre-Handoff Review 結果

Pre-Handoff Review の出力は Step 8.5.5 が専用の `## Pre-Handoff Review` コメントとして投稿済み。本セクションでは最新コメントを参照し、投稿数と最終 verdict を要約する:

- Pre-Handoff Review コメント数 (`PHR_COUNT`): XX
- 最終 verdict: Yes / With fixes / No
- 最新の `## Pre-Handoff Review` コメント: (リンクまたは投稿日時)

### 完了条件の段階確認

この段階で確認可能な完了条件:

- [ ] (確認した条件1): ✅ 実装で対応済み
- [ ] (確認した条件2): ✅ テスト通過で確認
- (未確認の条件があれば): final-check で確認予定

### 次のステップ

`/issue-review-code [issue_id]` によるコードレビューをお願いします。
COMMENT_EOF
)"
````

### Step 10: 完了報告

```
## 実装完了

| 項目 | 値 |
|------|-----|
| Issue | [issue_ref] |
| テスト | XX 件追加 |
| 品質チェック | すべてパス |

### 次のステップ

`/issue-review-code [issue_id]` でコードレビューを実施してください。
```

## Verdict 出力

実行完了後、以下の形式で verdict を出力すること:

---VERDICT---
status: PASS
reason: |
  実装・テスト・品質チェック全パス
evidence: |
  pytest 全テストパス、ruff/mypy エラーなし
suggestion: |
---END_VERDICT---

**重要**: verdict は **stdout にそのまま出力** すること。Issue コメントや Issue 本文更新とは別に、最終的な verdict ブロックは stdout に残す。

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 実装・テスト・品質チェック全パス |
| RETRY | テスト失敗等 |
| BACK | 設計に問題 |
| ABORT | 重大な問題（type ラベル未付与・複数付与・`type:docs` 等） |
