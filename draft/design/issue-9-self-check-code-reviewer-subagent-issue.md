# [設計] レビュー事前 self-check と code-reviewer subagent 導入で /issue-review-* の NG 率を下げる

Issue: gl:9

## 概要

`/issue-design` / `/issue-implement` のハンドオフ直前に「review 側 rubric と作業成果物を突き合わせる明示的なフェーズ」を追加する。Claude Code セッションでは新規 subagent `.claude/agents/kaji-code-reviewer.md` を Agent tool で起動し、非対応 agent（Codex / Gemini 等）では同 rubric を埋め込んだ main-session self-check に fallback する capability-based 設計を採る。

## 背景・目的

### ユースケース

- **AI コラボレーター（kaji 利用者）として**、`/issue-design` 完了時に `/issue-review-design` rubric の各観点（Gate Check / type weighting / Primary Sources）と作業成果物を突き合わせる self-check を経由したい。重複チェックリストを別途維持するのではなく `/issue-review-design` SKILL.md を **単一情報源** として参照したい。
- **AI コラボレーター（Claude Code セッション）として**、`/issue-implement` 完了時に `kaji-code-reviewer` subagent を Agent tool で起動し、設計書整合・テスト証跡・Scope 混在を第三者視点で検査した結果を Issue コメントへ自動転記したい。
- **AI コラボレーター（Codex / Gemini 等の非対応 agent セッション）として**、Agent tool が無くても同一 rubric の main-session self-check に fallback でき、起動経路（`subagent` / `self-check`）を Issue コメントへ明記したい。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| review-design / review-code に rubric を強化し、handoff 前 self-check を追加しない | 楽観バイアスがそのまま review に持ち越され、現状の RETRY 構造が変わらない。「振り返る場」を持たないこと自体が根本原因 |
| design / implement に巨大なチェックリストを複製する | 単一情報源（review 側 rubric）から外れて drift が発生する。Issue Must 指摘 3（Should 指摘 3）でも明確に否定 |
| Agent tool を必須化（Claude Code 専用化） | kaji の AI 横断 orchestrator 性を壊す。Codex / Gemini で workflow が動かなくなる |
| 全 agent で main-session self-check のみに統一（subagent を作らない） | Claude Code セッションでは independent な第三者視点（separate context）が得られず、楽観バイアスが残る |

→ **capability-based**（Claude Code = subagent / 他 = self-check）が最小コストで両条件を満たす。

## インターフェース

本 Issue は **skill markdown と agent markdown の追加・更新** が成果物。Python の関数 API ではないため「IF」はスキル/agent の I/O 契約として定義する。

### 入力

#### 1. design self-check（`/issue-design` Step 2.6 として追加）

| 項目 | 値 |
|------|-----|
| 起動 | `/issue-design` の Step 2.5（完了条件の段階確認）直後、Step 3（コミット）の前 |
| 入力 | `worktree_dir`, `issue_id`, `design_path`（`draft/design/issue-<id>-*.md`） |
| 参照 rubric | `/issue-review-design` SKILL.md の以下節へ直接リンク: <br> - **Step 1.5 Gate Check**（一次情報の記載・アクセス可能性） <br> - **Step 2 § type 別重み付け**（feat/bug/refactor の表）<br> - **Step 2 § レビュー基準 1〜5**（抽象化／IF／信頼性／検証可能性／影響ドキュメント） |
| 出力 | self-check 結果（不足項目の列挙）を Issue コメントへ転記。不足があれば設計書を補完してから Step 3 へ進む |

#### 2. `kaji-code-reviewer` subagent（`/issue-implement` Step 7.6 として追加、capability=Claude Code 時）

| 項目 | 値 |
|------|-----|
| 起動 | Agent tool により `subagent_type: "kaji-code-reviewer"` で起動 |
| 入力（prompt 経由） | `worktree_dir`, `issue_id`, `design_path`, `git diff main...HEAD` の範囲, baseline failure 一覧（あれば） |
| 内部処理 | read-only ツールのみで設計書 / diff / テスト出力を点検 |
| 出力 | 後述「出力形式」セクションの Markdown verdict（`Yes` / `No` / `With fixes`） |

#### 3. main-session self-check（`/issue-implement` Step 7.6 の fallback、capability=非 Claude Code 時）

同じ rubric（後述 § Rubric 本文）を main session 自身が実行。subagent と同じ出力形式で Issue コメントへ転記する。経路情報として `subagent unavailable, self-check executed` を必ず含める。

### 出力

#### subagent / self-check 共通の出力形式

```markdown
## Pre-Handoff Review

- **経路**: `subagent` / `self-check`
- **起動 agent**: kaji-code-reviewer / main-session (capability=<agent_name>)
- **対象 commit**: <git-sha>

### 1. 設計書整合
- 観点: 設計書「インターフェース」「方針」「テスト戦略」と実装 diff の対応
- 判定: ✅ / ⚠️ / ❌
- 根拠: （ファイル名:行数 と該当 diff の引用 / 不一致箇所の指摘）

### 2. テスト証跡
- 観点: 設計書「テスト戦略」の S/M/L が `tests/` に存在し PASSED か、または変更固有検証が実施済みか
- 判定: ✅ / ⚠️ / ❌
- 根拠: pytest 結果 / baseline 比較結果の引用

### 3. Scope 混在
- 観点: 設計書にない「ついで修正」の有無、type 責任範囲（feat/bug/refactor）を超える変更
- 判定: ✅ / ⚠️ / ❌
- 根拠: 設計書外の変更箇所列挙

### 4. 規約遵守（auto-close 回避）
- 観点: 本コメント・関連 commit body / MR description 内に GitLab auto-close hazard pattern（`Close[sd]?` / `Fix(es|ed|ing)?` / `Resolv(e[sd]?|ing)` / `Implement(s|ed|ing)?` の直後 `#<digit>`）が無いか
- 判定: ✅ / ⚠️ / ❌

### 指摘事項
- 指摘 1: ...
- 指摘 2: ...
（参照表記は `指摘 N` / `Must Fix item N` / `point N`。`Must Fix #N` / `Fix [N]` 禁止）

### Pre-Handoff Review Verdict
- **Yes** — handoff 可
- **No** — main session で修正が必要
- **With fixes** — 一部修正後に再度本フェーズを実行
```

### 使用例

```python
# /issue-implement Step 7.6 内（Claude Code 経路）擬似コード
capability = detect_capability()  # "claude_code" or "other"
if capability == "claude_code":
    result = Agent(
        subagent_type="kaji-code-reviewer",
        description="Pre-handoff code review for kaji workflow",
        prompt=build_reviewer_prompt(worktree_dir, issue_id, design_path),
    )
    route = "subagent"
else:
    # main-session が同じ rubric を自分で実行
    result = run_self_check(rubric_path=".claude/agents/kaji-code-reviewer.md")
    route = "self-check"

post_issue_comment(issue_id, format_review_comment(result, route=route))

if result.verdict == "With fixes":
    apply_fixes_in_main_session(result.findings)
    # 再度本フェーズを実行（ループ）
elif result.verdict == "No":
    # 大幅な手戻り → main session が修正、再度本フェーズへ
    pass
# Yes ならそのまま `/issue-review-code` へ
```

### エラーケース

| ケース | 挙動 |
|--------|------|
| Claude Code 環境で `.claude/agents/kaji-code-reviewer.md` がロードされていない（session start 後に追加した場合等） | Agent tool 起動が失敗 → main-session self-check に fallback。経路を `self-check (subagent unavailable, fallback)` と明記 |
| subagent が `With fixes` を 3 回連続で返した | abort せず main session が手動で修正方針を整理して再実行。それでも収束しない場合は `/issue-review-code` 側で BACK 相当の判定に委ねる（pre-handoff の自己評価ループでは正式 verdict を出さない） |
| read-only allowlist 外のコマンドを subagent が呼ぼうとした | tool 権限制限で fail。subagent prompt 側で「allowlist 内のみ」と明記して防ぐ |
| 一次情報が design 段階で不足 | design self-check で検出 → `/issue-fix-design` ルートに戻る（pre-handoff フェーズの結果を Issue コメントに残す） |

## 制約・前提条件

### 技術的制約

- **Claude Code subagent は subagent を spawn できない**（公式仕様）。controller（slash skill）は main session のままとし、critic だけを subagent に出す構成。
- **`.claude/agents/` は session start 時にロードされる**。追加直後のセッションでは selectable でない可能性があるため、動作検証では「新規セッション or 再起動後」を前提とする。
- **Claude Code 以外（Codex / Gemini 等）の adapter には Agent tool 相当が存在しない**。必須化はできず、必ず fallback 経路を持つ必要がある。
- **kaji は Python 単一スタック**。本変更は skill markdown / agent markdown のみで完結し、`kaji_harness/` の Python コード変更は伴わない見込み。

### 既存モジュールへの依存

- `.claude/skills/issue-design/SKILL.md`（更新）
- `.claude/skills/issue-implement/SKILL.md`（更新）
- `.claude/skills/issue-review-design/SKILL.md`（rubric 単一情報源、参照のみ。本 Issue では変更しない）
- `.claude/skills/issue-review-code/SKILL.md`（rubric 単一情報源、参照のみ。本 Issue では変更しない）
- `.claude/skills/_shared/design-by-type/feat.md` 等（design type 別ガイド、変更不要）
- `docs/dev/shared_skill_rules.md` § GitLab auto close keyword 回避規約（subagent prompt の出力形式に反映）

### 規約整合

- `docs/dev/shared_skill_rules.md` の auto-close 回避規約に subagent prompt と出力形式が **準拠** すること。具体的には:
  - 指摘 index は `Must Fix item N` / `指摘 N` / `point N` 形式
  - 禁止: `Must Fix #<N>` / `Fix [N]` 等（`<N>` は仕様例文 placeholder としての例示）
  - GitHub/GitLab 両対応のため厳しい側（GitLab）に揃える

### ライセンス・attribution（obra/superpowers）

- `obra/superpowers` のライセンスは **MIT** であることを設計レビュー時点で `LICENSE` ファイル（https://github.com/obra/superpowers/blob/main/LICENSE）参照により確認済み。
- **attribution 方針: rubric 参考（paraphrased）**。コピーではなく観点項目を kaji 固有 rubric として再構成する。出典として `kaji-code-reviewer.md` 冒頭コメントに以下を記載:
  - 出典 URL: `https://github.com/obra/superpowers/blob/main/skills/requesting-code-review/code-reviewer.md`
  - ライセンス明示: `Based on obra/superpowers code-reviewer (MIT License, Copyright (c) obra/superpowers contributors)`
  - 改変方針: kaji rubric（設計書整合 / テスト証跡 / Scope 混在 / auto-close 回避規約）への適合化

## 方針

### 全体構成

```
/issue-design ───────────────── Step 2.5 完了条件確認
                                   │
                                   ▼
                              Step 2.6 (新規) design self-check
                                   │ rubric は /issue-review-design SKILL.md を参照リンク
                                   │ 結果を Issue コメントへ転記
                                   ▼
                              Step 3 コミット
                              Step 4 Issue コメント
                              Step 5 完了報告

/issue-implement ────────────── Step 7.5 完了条件確認
                                   │
                                   ▼
                              Step 7.6 (新規) Pre-Handoff Review (MANDATORY)
                                   │
                          ┌────────┴────────┐
                          ▼                 ▼
                   capability=             capability=
                   Claude Code             非対応 agent
                          │                 │
                          ▼                 ▼
                   Agent tool で         main-session
                   kaji-code-reviewer    self-check
                   を起動                 (同 rubric)
                          │                 │
                          └────────┬────────┘
                                   ▼
                              Issue コメント転記（経路情報含む）
                              verdict: Yes/No/With fixes
                                   │
                                   ▼
                              Step 8 コミット
                              Step 9 Issue コメント（実装完了報告）
                              Step 10 完了報告
```

### `.claude/agents/kaji-code-reviewer.md` の frontmatter（hard boundary 固定）

```yaml
---
name: kaji-code-reviewer
description: kaji workflow の pre-handoff code review を実施する第三者視点 critic。設計書整合・テスト証跡・Scope 混在・GitLab auto-close 規約遵守を検査し、Yes/No/With fixes verdict を返す。kaji workflow の正式 verdict (PASS/RETRY/BACK/ABORT) は発行しない。
model: sonnet
tools:
  - Read
  - Grep
  - Glob
maxTurns: 8
---
```

**hard boundary の根拠（レビュー指摘 Must 2 への対応）**:

- **Bash を tools から外す**: Claude Code subagent では tools allowlist に含まれないツールは subagent 内で呼び出せない。`Bash` を含めない時点で shell 起動経路自体が遮断され、親セッションの `permissionMode` が `bypassPermissions` / `acceptEdits` であっても shell 経由の書き換えは仕様上不可能。
- **Edit / Write も tools 不付与**: ファイル書き換え経路を網羅的に遮断。
- **ネットワーク系ツール（WebFetch / WebSearch / 各種 mcp_*）も付与しない**: critic は外部 IO を必要としない。一次情報の URL は main session が prompt で提供する。
- **`permissionMode` の扱い**: Claude Code 公式 subagent docs では subagent frontmatter での `permissionMode` 個別指定は明示的にサポートされていない。本設計では「tool allowlist を最小化する」ことを **hard guarantee の主軸** とし、`permissionMode` には依存しない。`Read` / `Grep` / `Glob` のみであれば、親セッションの permissionMode が緩くてもファイル read 以外の副作用は生じない（read のみのため）。
- **`model: sonnet`**: 高速かつ rubric 適用に十分な reasoning。opus は不要、haiku では rubric 適用に不足。
- **`maxTurns: 8`**: subagent が複数 round の調査（設計書 → diff → テスト出力の参照を複数往復）を要する場合の上限。短すぎると rubric 適用が不完全になり、長すぎると無限調査ループになるため、4 観点 × 2 round 程度を想定して 8 に固定。

> 公式 docs に基づき frontmatter は `name` / `description` / `tools` / `model` / `maxTurns` のみを使用する。`permissionMode` 等の subagent frontmatter で正式サポートされていない項目は付与しない（再現性が保証されないため）。

### main session 側で事前取得し prompt に注入する情報（Bash 廃止に伴う再設計）

subagent は Bash を持たないため、`git diff` / `pytest` / `ruff` / `mypy` / `git log` は **main session が pre-handoff review 実行前に取得し、prompt のテキストとして渡す**。subagent は与えられたテキストと `Read` / `Grep` / `Glob` のみで判断する。

| 情報 | 取得元（main session） | prompt への渡し方 |
|------|----------------------|-----------------|
| `git diff main...HEAD` の全文 | main session が `git diff main...HEAD` 実行 | prompt 内の `## Diff` セクションに code block で貼付 |
| 直近 pytest 出力 | Step 7b で取得済みの出力をそのまま | prompt 内の `## Test Output` セクションに code block で貼付 |
| ruff / mypy 出力 | Step 7a で取得済みの出力をそのまま | prompt 内の `## Quality Check` セクションに code block で貼付 |
| baseline failure 一覧 | Issue コメントの `## Baseline Check 結果` をそのまま引用 | prompt 内の `## Baseline Failures` セクション |
| 設計書パス | `worktree_dir/draft/design/issue-<id>-*.md` | path を prompt に明示し、subagent が `Read` ツールで参照 |

この設計により:
- subagent の権限は `Read` / `Grep` / `Glob` の 3 つに限定（hard boundary）
- main session が情報取得の責務を負うため、入力の再現性が高い
- main session 側で Bash 実行結果を取得する処理は `/issue-implement` Step 7a / 7b で既に行っているため、追加のオーバーヘッドは prompt 組み立てのみ

**禁止経路（hard boundary により仕様上不可能）**:
- subagent からのファイル書き換え（Edit / Write / Bash いずれも tools 不付与）
- subagent からのコマンド実行（Bash 不付与）
- subagent からのネットワーク IO（WebFetch / WebSearch / mcp_* 不付与）
- subagent からの Issue コメント投稿 / push（必要ツール群が不付与）— Issue コメント転記は main session 側で実施

### subagent system prompt 本文（骨子、実装フェーズで最終化）

```markdown
あなたは kaji workflow の **pre-handoff code reviewer** です。

## 立場
- あなたは critic です。修正・コミット・push・コメント投稿は行いません。
- あなたの verdict (`Yes` / `No` / `With fixes`) は **pre-handoff 自己評価** であり、kaji workflow の正式 verdict (`PASS` / `RETRY` / `BACK` / `ABORT`) ではありません。
- 正式 verdict は `/issue-review-code` が後段で発行します。

## 入力（prompt 経由で受領）
- 設計書のパス: `<worktree_dir>/draft/design/issue-<id>-*.md`（Read ツールで参照）
- 差分: prompt の `## Diff` セクションに main session が貼付した `git diff main...HEAD` の全文
- テスト出力: prompt の `## Test Output` セクションの pytest 結果
- 品質チェック出力: prompt の `## Quality Check` セクションの ruff / mypy 結果
- baseline failure: prompt の `## Baseline Failures` セクション（あれば）

## 利用可能ツール
- `Read`: 設計書および worktree 内の任意ファイル参照
- `Grep`: コード内パターン検索（regression / 同根欠陥の探索）
- `Glob`: ファイル列挙

その他のツール（Bash / Edit / Write / WebFetch / WebSearch 等）は付与されていない。コマンド実行・ファイル書き換え・外部 IO はできない。必要な実行結果は main session が prompt で提供する。

## チェック観点（kaji rubric）
1. **設計書整合**: 設計書「インターフェース」「方針」「テスト戦略」と diff が対応しているか
2. **テスト証跡**: 設計書 S/M/L の有無 / pytest PASSED / baseline 比較
3. **Scope 混在**: 設計書にない変更 / type 責任範囲を超える変更（Grep で設計書外のファイルへの diff を確認）
4. **auto-close 規約**: 本コメントおよび直後生成される commit body 候補に `Close[sd]?` / `Fix(es|ed|ing)?` / `Resolv(e[sd]?|ing)` / `Implement(s|ed|ing)?` の直後 `#<digit>` が無いか（参照: `docs/dev/shared_skill_rules.md` § GitLab auto close keyword 回避規約）

## 出力形式
指摘の参照は **`Must Fix item N` / `指摘 N` / `point N`** 形式に統一。`Must Fix #N` / `Fix [N]` のような close keyword と隣接する `#` / `[]` 表記は禁止。
（後段の「出力フォーマット」テンプレートに従う）

## 出典
Based on obra/superpowers code-reviewer rubric (MIT License, リポジトリ LICENSE で確認済み).
kaji-specific rubric への paraphrase / 改変済み。
```

### capability 判定（1 方式に固定、Must 1 への対応）

採用方式: **`/issue-implement` skill markdown 内で「Agent tool 利用可否の試行」を唯一の分岐条件とする**。

理由（他案との比較）:

| 案 | 採否 | 理由 |
|----|------|------|
| A. Agent tool 利用可否を skill prompt 内で試行する | **採用** | 実際に分岐したい条件（Claude Code = subagent / Codex / Gemini = self-check）と一致する。harness 変更不要 |
| B. harness 側で `agent_type` を skill prompt に注入する | 不採用 | 現行 repo で `agent_type` / `context.agent_type` を skill prompt に注入する既存実装は見つからず（`kaji_harness/` 全文 grep 確認済み）、本 Issue は skill / agent markdown 変更で完結する scope のため Python 変更を含めると scope creep |
| C. 環境変数（`CLAUDE_CODE=1` 等）で判定 | 不採用 | agent runtime ごとに環境変数が異なり、間接指標になる。直接 capability を測る案 A の方が頑健 |

`/issue-implement` SKILL.md Step 7.6 に記述する判定フロー（実装フェーズで skill markdown に落とす指示文）:

```text
1. main session は `kaji-code-reviewer` subagent を Agent tool で起動するよう試行する。
   - 起動成功（subagent からの応答テキストが取得できる）→ 経路: subagent
   - Agent tool が利用不可（Codex / Gemini 等で tool が未定義）または起動失敗 → 経路: self-check
2. 経路: self-check の場合、main session 自身が `.claude/agents/kaji-code-reviewer.md` の prompt 本文を読み込み、同じ rubric を main session 内で適用する。
3. いずれの経路でも、Issue コメント転記時に経路情報（`subagent` / `self-check (subagent unavailable, fallback)`）を必ず記載する。
```

実装上の判定動作:
- skill markdown の prompt 自体が「Agent tool を試行 → 失敗時 fallback」のフローを instruction として記述する
- Agent tool を解釈できる agent runtime（Claude Code）では試行が成功し、subagent 経路が走る
- Agent tool を解釈できない agent runtime（Codex / Gemini）では instruction を読んだ main session が fallback ブランチを実行する
- harness 側の追加機構（agent_type 注入等）は不要

### subagent verdict ↔ kaji verdict の階層分離

| 階層 | verdict | 発行者 | 用途 |
|------|---------|--------|------|
| 自己評価 | `Yes` / `No` / `With fixes` | `kaji-code-reviewer` subagent または main-session self-check | pre-handoff の品質ゲート。main session の修正トリガー |
| 正式 verdict | `PASS` / `RETRY` / `BACK` / `ABORT` | `/issue-review-code`（別セッション推奨） | kaji workflow の合否 |

- `With fixes` → main session が修正 → 再度 pre-handoff review を実行（ループ）
- `No` → 大幅な修正が必要。main session が修正後、再度 pre-handoff review を実行
- `Yes` → `/issue-review-code` へ進行
- pre-handoff review の結果は **Issue コメントに全文転記** することで、後段の `/issue-review-code` がコンテキストとして参照可能にする

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 既存技術（subagent / Agent tool）の利用に留まり、新規 ADR が必要な技術選定ではない |
| docs/ARCHITECTURE.md | なし | skill / agent ファイルの追加・更新のみで、harness の architecture には影響しない |
| docs/dev/workflow_guide.md | **あり** | feature-development の design / implement フェーズに pre-handoff review が追加されることを明記する必要がある |
| docs/dev/development_workflow.md | **あり** | design → review-design / implement → review-code の中間に pre-handoff review が入る経路を追記 |
| docs/dev/shared_skill_rules.md | **あり（軽微）** | 影響を受ける skill 一覧（§ 影響を受ける skill）に `kaji-code-reviewer` agent と新規 design self-check / pre-handoff review section を追記。auto-close 規約本体は変更しない |
| docs/dev/workflow_completion_criteria.md | あり（要評価） | design / implement の完了条件に pre-handoff review 通過が含まれる場合、追記する |
| docs/reference/ | なし | Python API / 規約変更なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| CLAUDE.md | なし | プロジェクト規約レベルの変更ではない |

## テスト戦略

### 変更タイプ

**capability-based workflow change**（agent runtime が interpret する skill / agent markdown による workflow 振る舞いの追加）。

レビュー指摘 Should 1 への対応として、従来の「docs-only」表現を撤回する。本変更は単なる文書整備ではなく、`/issue-design` / `/issue-implement` の skill が agent runtime で interpret される際の **振る舞い**（pre-handoff review の起動、subagent / self-check の分岐、Issue コメント転記）を追加する変更である。一方で Python runtime のコードパスは変更しない（追加する .md は Python module ではなく agent runtime 解釈対象）。この特性により以下の二段構造でテスト戦略を組む:

- **Python runtime のテスト（pytest）**: 追加対象なし。理由は後述「恒久 pytest を追加しない理由」。
- **agent runtime の振る舞い検証**: 手動動作検証を **必須ゲート** として運用する（後述「必須動作検証ゲート」）。

### 静的整合検証（必須・PR 前ゲート）

skill / agent markdown 追加時の整合性を機械的に確認する。Python テストではないが PR 前に必ず実行する。

| 検証項目 | コマンド | 合格条件 |
|---------|---------|---------|
| パス参照整合 | `make verify-docs` | exit 0（追加ファイル含む全リンク解決） |
| frontmatter spec 整合 | manual diff レビュー（公式 [Claude Code Subagents docs](https://code.claude.com/docs/en/sub-agents) との突合せ） | `name` / `description` / `tools` / `model` / `maxTurns` のみ使用、未サポート項目なし |
| auto-close hazard 検査（commit body） | `git log <range> --format='%B' \| grep -iE '\b(clos(e[sd]?\|ing)\|fix(e[sd]\|ing)?\|resolv(e[sd]?\|ing)\|implement(s\|ing\|ed)?)\s*:?\s*#[0-9]'` | 0 match |
| auto-close hazard 検査（Issue コメント候補） | 同 grep を投稿予定本文に適用 | 0 match |
| 既存 lint / format / typecheck / pytest | `make check`（Python 側 regression 検出） | 既存通り pass（本変更は Python に触れないため変動しない想定） |

### 必須動作検証ゲート（capability-based 振る舞い変更の検証）

Python pytest 化はしないが、Issue 完了条件の「動作検証」セクションに対応する **必須ゲート** として運用する。recurring pytest にしない代わりに、本 Issue の PR 上で証跡を残すことを以下に明示する:

| 検証項目 | 方法 | 合格条件 | 証跡保存先 |
|---------|------|---------|-----------|
| 1. `kaji-code-reviewer` が新規セッションで selectable | `.claude/agents/kaji-code-reviewer.md` 追加 commit を含む状態で、新規 Claude Code セッションを起動。Agent tool 起動時に `subagent_type: "kaji-code-reviewer"` が解決すること | 起動応答が得られる | Issue gl:9 コメント（実装フェーズ末） |
| 2. `/issue-implement` 経由で subagent が実起動 | 検証用 small Issue で `/issue-implement` を実行 | Issue コメントに「経路: subagent」「起動 agent: kaji-code-reviewer」「verdict: Yes/No/With fixes」が記録される | 検証用 Issue コメント |
| 3. Codex / Gemini での fallback 経路証跡 | Codex / Gemini agent で `/issue-implement` を dry run（または skill markdown を読み込んだ上で main session が fallback ブランチを実行） | Issue コメントに「経路: self-check (subagent unavailable, fallback)」が記録される | 検証用 Issue コメント |
| 4. `With fixes` ループ動作 | 意図的に設計書から外れた diff を作って実行 | subagent が `With fixes` を返し、main session が修正後に再起動して `Yes` で抜ける（ループ回数を Issue コメントに記載） | 検証用 Issue コメント |
| 5. auto-close hazard 検査が hazard を検出できること | 意図的に `Fix #99` を含む dry run prompt を投入 | subagent / self-check が auto-close 規約観点で ❌ を返す | 検証用 Issue コメント |

> 検証用 Issue は本 Issue とは別の small test Issue を用意して実施する。`kaji issue create` で local Issue を作って試験するのが現実的。

### 恒久 pytest を追加しない理由（`docs/dev/testing-convention.md` 4 条件マッピング）

本変更は skill 層の振る舞い変更だが、以下の 4 条件すべてを満たすため pytest を新設しない:

1. **Python runtime の振る舞いは変えない**: 追加する `.md` ファイル群は agent runtime（Claude Code / Codex / Gemini）が解釈する prompt テキスト。`kaji_harness/` 配下の Python module には変更が入らないため、`tests/test_*.py` で assert すべき Python 関数 / クラスの増加がない。
2. **既存テストの守備範囲外**: 既存の harness テスト群は CLI / workflow runner / provider adapter 等の Python 機能を検証しており、skill markdown 内容の assertion は持たない。仮に「`kaji-code-reviewer.md` に `tools: [Read, Grep, Glob]` が含まれること」を pytest 化しても、skill 内容の drift を機械的に検知する以外の価値が薄く、保守コスト（skill 修正のたびに test 修正）が見合わない。
3. **過去障害の再発防止ではない**: 特定の Python bug 回避ではなく、新規 workflow フェーズの追加。再現テストの対象となる障害がない。
4. **手動 / 別経路で検証可能**: 振る舞いの正しさは上述「必須動作検証ゲート」（subagent 実起動・fallback 経路・auto-close hazard 検出）で確認する。これらは agent runtime 上の動作確認であり Python unit test 化しても agent runtime の挙動を再現できない。

### Small / Medium / Large テストの省略について

| サイズ | 省略可否 | 理由 |
|--------|---------|------|
| Small | 省略 | 上記 4 条件マッピングの 1, 2 に該当。skill / agent markdown の文字列 assertion は drift しやすく価値が薄い |
| Medium | 省略 | DB / 内部サービス連携を伴わない（kaji harness の既存 medium テスト群と重複しない） |
| Large | 省略 | E2E 経路は kaji workflow 全体（`kaji run feature-development …`）の既存検証で副次的にカバー。本変更単体で実 API 疎通を増やすことはない |

> 「省略してはいけない理由」（環境不備 / 実行時間 / Small で十分等）には該当しない。capability-based workflow change として skill 層の振る舞い検証は **手動動作検証ゲート** が一次経路であり、これを必須化することで省略の妥当性を担保する。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Claude Code Subagents 公式 docs | https://code.claude.com/docs/en/sub-agents | subagent は `.claude/agents/<name>.md` に frontmatter (`name` / `description` / `tools` / `model` 等) + system prompt 本文で定義。Agent tool（`subagent_type` 指定）で起動。subagent は subagent を spawn できない。session start 時にロードされる |
| Anthropic Engineering: Multi-agent research system | https://www.anthropic.com/engineering/multi-agent-research-system | 「critic / reviewer subagent を独立コンテキストで動かすことで楽観バイアスを軽減できる」設計パターンの根拠。本 Issue の Claude Code 経路でこのパターンを採用 |
| obra/superpowers code-reviewer | https://github.com/obra/superpowers/blob/main/skills/requesting-code-review/code-reviewer.md | reviewer subagent の rubric 構成（観点列挙 + Yes/No/With fixes verdict）を参考。kaji 固有 rubric として paraphrase 採用 |
| obra/superpowers code-quality-reviewer-prompt | https://github.com/obra/superpowers/blob/main/skills/subagent-driven-development/code-quality-reviewer-prompt.md | reviewer prompt の出力形式（指摘列挙 + verdict）の参考 |
| obra/superpowers LICENSE | https://github.com/obra/superpowers/blob/main/LICENSE | **MIT License で確認済み**（設計レビューで一次情報照会済み）。attribution は `Based on obra/superpowers code-reviewer (MIT License)` を `kaji-code-reviewer.md` 冒頭に明示し、rubric は paraphrase 採用 |
| DeepWiki: superpowers Code Review Process | https://deepwiki.com/obra/superpowers/6.6-code-review-process | superpowers 内での reviewer 起動フローの理解補助。一次情報ではなく二次解説のため設計判断の主根拠にはしない |
| kaji `docs/dev/shared_skill_rules.md` § GitLab auto close keyword 回避規約 | `docs/dev/shared_skill_rules.md` (line 45〜160) | subagent prompt の指摘表記、出力形式、commit body 検証 grep の根拠。`Must Fix item N` / `指摘 N` / `point N` 形式必須、`Must Fix #N` / `Fix [N]` 禁止 |
| kaji `docs/cli-guides/gitlab-mode.md` § 5.7 | `docs/cli-guides/gitlab-mode.md` § 5.7 | GitLab auto-close 実発生例。pre-handoff review の auto-close 規約検査観点の動機 |
| kaji `docs/dev/testing-convention.md` 4 条件 | `docs/dev/testing-convention.md` | docs-only / metadata-only / packaging-only 変更で恒久テストを追加しない判断の根拠 |
| kaji `.claude/skills/issue-review-design/SKILL.md` | `.claude/skills/issue-review-design/SKILL.md` (Step 1.5 / Step 2 § type 重み付け / § レビュー基準) | design self-check の rubric 単一情報源として参照 |
| kaji `.claude/skills/issue-review-code/SKILL.md` | `.claude/skills/issue-review-code/SKILL.md` (Step 1.5 / Step 2 § type 別追加観点 / § 共通観点) | pre-handoff review の rubric 単一情報源として参照 |
