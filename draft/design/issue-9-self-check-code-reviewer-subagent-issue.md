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

- `obra/superpowers` のライセンスを設計レビュー時点で確認する必要がある（リポジトリ `LICENSE` の取得・記録）。MIT 想定。
- **attribution 方針: rubric 参考（paraphrased）**。コピーではなく観点項目を kaji 固有 rubric として再構成する。出典として `kaji-code-reviewer.md` 冒頭コメントに以下を記載:
  - 出典 URL: `https://github.com/obra/superpowers/blob/main/skills/requesting-code-review/code-reviewer.md`
  - ライセンス記載（MIT であれば `Based on obra/superpowers (MIT License)` を明記）
  - 改変方針: kaji rubric（設計書整合 / テスト証跡 / Scope 混在 / auto-close 回避規約）への適合化
- MIT 以外だった場合: 該当文の転記を一切行わず、観点項目のみを独立に再定義する（一次情報の URL は参考として残す）。実装フェーズ着手前にライセンスを確認し、結果を Issue コメントへ記録すること。

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

### `.claude/agents/kaji-code-reviewer.md` の frontmatter

```yaml
---
name: kaji-code-reviewer
description: kaji workflow の pre-handoff code review を実施する第三者視点 critic。設計書整合・テスト証跡・Scope 混在・GitLab auto-close 規約遵守を検査し、Yes/No/With fixes verdict を返す。kaji workflow の正式 verdict (PASS/RETRY/BACK/ABORT) は発行しない。
model: sonnet  # 高速かつ十分な reasoning。opus は不要、haiku では rubric 適用に不足
tools:
  - Read
  - Grep
  - Glob
  - Bash  # ただし system prompt 内 allowlist のみ
# maxTurns: 8（subagent 内で複数 round の調査が必要な場合の上限）
# 注: maxTurns は frontmatter で直接指定可能か Claude Code 公式仕様を確認の上、不可なら system prompt で運用上の上限として明示する
---
```

### Bash allowlist（read-only）

subagent prompt 内で **明示** する read-only コマンド allowlist:

```
- git diff <range>       # 差分閲覧のみ
- git log <range> [--format=...]
- git show <sha>
- git status
- ruff check kaji_harness/ tests/         # --fix は禁止
- ruff format --check kaji_harness/ tests/  # --check のみ。format 実行は禁止
- mypy kaji_harness/
- pytest -q [--collect-only]              # 既に実装側が実行済み。再実行は最小限
- cat / head / tail / wc                  # 内容閲覧
- ls
```

**禁止**:
- ファイル書き換え（`Edit` / `Write` tool 不付与で防御。Bash 経由でも `>`, `>>`, `tee`, `sed -i`, `git commit`, `git push`, `git checkout` 等は禁止）
- ネットワーク IO（`curl`, `wget`, `gh`, `glab`, `kaji issue comment` 等）— Issue コメント転記は main session 側で行う

### subagent system prompt 本文（骨子、実装フェーズで最終化）

```markdown
あなたは kaji workflow の **pre-handoff code reviewer** です。

## 立場
- あなたは critic です。修正・コミット・push・コメント投稿は行いません。
- あなたの verdict (`Yes` / `No` / `With fixes`) は **pre-handoff 自己評価** であり、kaji workflow の正式 verdict (`PASS` / `RETRY` / `BACK` / `ABORT`) ではありません。
- 正式 verdict は `/issue-review-code` が後段で発行します。

## 入力
- 設計書: `<worktree_dir>/draft/design/issue-<id>-*.md`
- 差分: `git diff main...HEAD`（main session が提示した range）
- baseline failure（あれば Issue コメントから取得済みのものを prompt で受け取る）

## チェック観点（kaji rubric）
1. **設計書整合**: 設計書「インターフェース」「方針」「テスト戦略」と diff が対応しているか
2. **テスト証跡**: 設計書 S/M/L の有無 / pytest PASSED / baseline 比較
3. **Scope 混在**: 設計書にない変更 / type 責任範囲を超える変更
4. **auto-close 規約**: 直後生成される commit body / Issue コメント候補に `Close[sd]?` / `Fix(es|ed|ing)?` / `Resolv(e[sd]?|ing)` / `Implement(s|ed|ing)?` の直後 `#<digit>` が無いか（参照: `docs/dev/shared_skill_rules.md` § GitLab auto close keyword 回避規約）

## 出力形式
指摘の参照は **`Must Fix item N` / `指摘 N` / `point N`** 形式に統一。`Must Fix #N` / `Fix [N]` のような close keyword と隣接する `#` / `[]` 表記は禁止。
（後段の「出力フォーマット」テンプレートに従う）

## 許可コマンド
（上記 allowlist）

## 出典
Based on obra/superpowers code-reviewer rubric (MIT License, 要確認).
kaji-specific rubric への paraphrase / 改変済み。
```

### capability 検出ロジック（`/issue-implement` Step 7.6 冒頭）

```python
# 擬似コード
def detect_capability() -> str:
    """
    Claude Code agent か否かを判定。
    - 環境変数 / agent context / 利用可能 tool 一覧などから決定
    - kaji harness 側で agent_type を context として注入できればそれを使う
    - 注入が無ければ skill prompt 内で `if Agent tool available` の dynamic check
    """
    # 実装フェーズで harness 側の context.agent_type を確認し、
    # 利用可能なら kaji_harness/cli/agent_context.py 等で参照する形にする
    ...
```

実装段階で kaji harness の agent identifier（`claude` / `codex` / `gemini`）を skill prompt に注入する経路が既にあるかを確認する。無ければ skill prompt 側で `Agent tool` の有無を試行する形にする（fail したら fallback）。

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

> 本変更は skill markdown / agent markdown のファイル追加・更新が主体。Python 実装コードを伴わない見込み。テスト規約 `docs/dev/testing-convention.md` のうち「実行時コード変更」よりも **docs-only に近い metadata / structural 変更** が中心。
> ただし「動作検証」項目（subagent 実起動 / fallback dry run）は手動検証として必須。

### 変更タイプ
- **主**: docs-only（skill markdown / agent markdown の追加・更新）
- **副**: 動作検証（実行時的な要素を持つ手動検証ステップ）

### docs-only として扱う部分

#### 変更固有検証
1. **skill markdown 内のパス参照整合**: 設計書・SKILL.md・shared_skill_rules.md 等の相対パスリンクが解決すること（`make verify-docs` で確認）
2. **frontmatter spec 整合**: `kaji-code-reviewer.md` の YAML frontmatter が Claude Code subagent 仕様（公式 docs）に従っていること（manual diff レビュー）
3. **auto-close hazard grep**: 設計書および skill 更新分の commit body に `grep -iE '\b(clos(e[sd]?|ing)|fix(e[sd]|ing)?|resolv(e[sd]?|ing)|implement(s|ing|ed)?)\s*:?\s*#[0-9]'` を実行し 0 match（`shared_skill_rules.md` § push 前検証準拠）

#### 恒久テストを追加しない理由（`docs/dev/testing-convention.md` 4 条件マッピング）

1. **実行時の振る舞いを変えない**: skill markdown / agent markdown は Claude Code / agent 側で interpret されるテキスト。Python runtime の挙動を変える変更ではない
2. **既存テストでカバーできない契約変更ではない**: 既存の harness テスト群（`tests/test_*.py`）は skill 内容ではなく harness 側 Python 機能を検証している。本変更の対象（skill 文書）は harness テストの守備範囲外であり、新規 pytest を作っても検証対象がない
3. **過去障害の再発防止ではない**: 特定 bug の再発防止テストではなく、新規機能（pre-handoff review）の追加
4. **手動 / 別経路で検証可能**: 動作検証は subagent 実起動 + Issue コメント証跡で確認可能（後述）

### 動作検証として扱う部分（手動 / dry run）

> 恒久 pytest 化はしないが、Issue 完了条件の「動作検証」セクションを満たすために実施する。

| 検証項目 | 方法 | 合格条件 |
|---------|------|---------|
| `kaji-code-reviewer` が新規セッションで selectable | 新規 Claude Code セッションを起動し、Agent tool で subagent 一覧を確認 | 一覧に `kaji-code-reviewer` が含まれる |
| `/issue-implement` 経由で subagent が実起動 | 検証用の小さな実装変更で `/issue-implement` を流す | Issue コメントに「経路: subagent」「起動 agent: kaji-code-reviewer」が記録される |
| Codex / Gemini agent での fallback 経路証跡 | dry run（または検証用 issue）で `/issue-implement` を流す | Issue コメントに「経路: self-check (subagent unavailable, fallback)」が記録される |
| `With fixes` ループ動作 | 意図的に設計書から外れた diff を作って実行 | subagent が `With fixes` を返し、main session が修正後に再起動して `Yes` で抜ける |
| auto-close hazard 検査 | 上記 grep を全変更 commit body / Issue コメント候補に適用 | 0 match |

### Small / Medium / Large テストの省略理由

| サイズ | 省略可否 | 理由 |
|--------|---------|------|
| Small | 省略 | skill markdown / agent markdown の文字列を assert する Small テストは保守コストに見合わない（drift しやすい）。frontmatter parser を Python 側で持つわけでもない |
| Medium | 省略 | DB / 内部サービス連携を伴わない |
| Large | 省略 | E2E 経路は kaji workflow 全体（`kaji run feature-development …`）の既存検証で副次的にカバーされる。本変更単体で実 API 疎通を増やすことはない |

> 上記 4 条件すべて（`docs/dev/testing-convention.md` の docs-only / metadata-only / packaging-only 変更条件）を満たすため、恒久 pytest は追加しない。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Claude Code Subagents 公式 docs | https://code.claude.com/docs/en/sub-agents | subagent は `.claude/agents/<name>.md` に frontmatter (`name` / `description` / `tools` / `model` 等) + system prompt 本文で定義。Agent tool（`subagent_type` 指定）で起動。subagent は subagent を spawn できない。session start 時にロードされる |
| Anthropic Engineering: Multi-agent research system | https://www.anthropic.com/engineering/multi-agent-research-system | 「critic / reviewer subagent を独立コンテキストで動かすことで楽観バイアスを軽減できる」設計パターンの根拠。本 Issue の Claude Code 経路でこのパターンを採用 |
| obra/superpowers code-reviewer | https://github.com/obra/superpowers/blob/main/skills/requesting-code-review/code-reviewer.md | reviewer subagent の rubric 構成（観点列挙 + Yes/No/With fixes verdict）を参考。kaji 固有 rubric として paraphrase 採用 |
| obra/superpowers code-quality-reviewer-prompt | https://github.com/obra/superpowers/blob/main/skills/subagent-driven-development/code-quality-reviewer-prompt.md | reviewer prompt の出力形式（指摘列挙 + verdict）の参考 |
| obra/superpowers LICENSE | https://github.com/obra/superpowers/blob/main/LICENSE | 設計フェーズで取得・MIT であることを確認する。MIT 以外なら attribution 方針を「rubric 参考のみ / 文転記禁止」に縮退する。**実装フェーズ着手前に確認・Issue コメントに記録すること** |
| DeepWiki: superpowers Code Review Process | https://deepwiki.com/obra/superpowers/6.6-code-review-process | superpowers 内での reviewer 起動フローの理解補助。一次情報ではなく二次解説のため設計判断の主根拠にはしない |
| kaji `docs/dev/shared_skill_rules.md` § GitLab auto close keyword 回避規約 | `docs/dev/shared_skill_rules.md` (line 45〜160) | subagent prompt の指摘表記、出力形式、commit body 検証 grep の根拠。`Must Fix item N` / `指摘 N` / `point N` 形式必須、`Must Fix #N` / `Fix [N]` 禁止 |
| kaji `docs/cli-guides/gitlab-mode.md` § 5.7 | `docs/cli-guides/gitlab-mode.md` § 5.7 | GitLab auto-close 実発生例。pre-handoff review の auto-close 規約検査観点の動機 |
| kaji `docs/dev/testing-convention.md` 4 条件 | `docs/dev/testing-convention.md` | docs-only / metadata-only / packaging-only 変更で恒久テストを追加しない判断の根拠 |
| kaji `.claude/skills/issue-review-design/SKILL.md` | `.claude/skills/issue-review-design/SKILL.md` (Step 1.5 / Step 2 § type 重み付け / § レビュー基準) | design self-check の rubric 単一情報源として参照 |
| kaji `.claude/skills/issue-review-code/SKILL.md` | `.claude/skills/issue-review-code/SKILL.md` (Step 1.5 / Step 2 § type 別追加観点 / § 共通観点) | pre-handoff review の rubric 単一情報源として参照 |
