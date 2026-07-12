# [設計] 第2層: インシデント原因調査・対応策提案のレビュー収束サイクル（手動起動）

Issue: #305

## 概要

第1層（#304）が起票したインシデントイシューを入力に、調査 → 査読 → 修正 → 確認 → 最終報告の
レビュー収束サイクルを回す workflow `.kaji/wf/incident.yaml` と、対応する skill 群・実行型査読役の
agent 定義・調査 artifact テンプレートを追加する。起動は手動（`/incident-cycle` slash wrapper）、
全終端は「提案」であり、ラベル遷移・クローズ・バグイシュー化・統合の実行はすべて人間が行う。

**設計上の全決定は EPIC #303 本文「設計方針（合意済み決定事項）」を正本とする。**
本設計書の各節に、対応する #303 の決定（A〜F / D 詳細等）を明記する。

## 背景・目的

### ユーザーストーリー（Issue #305 より）

- kaji 運用者として、原因調査と対応策の提案を**レビュー収束サイクルで検証してから**受け取りたい
  （もっともらしく間違った原因分析をそのまま信じない）ために、インシデントイシューを指定して
  調査ワークフローを手動起動したい。
- kaji 運用者として、証拠不足のときに無理な断定ではなく **`INCONCLUSIVE`（棄却済み仮説＋不足証拠
  の列挙）** を受け取りたいために、調査結論とレビュー品質が別軸で判定されてほしい。
- kaji 運用者として、最終的なバグイシュー化・インシデントのクローズ・統合は自分の判断で行いたい。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| 単発のエージェント調査（workflow なし・レビューなし） | #301 で人間＋AI の手動調査でも誤診を繰り返した実績。反証義務を持つ査読と収束サイクルなしでは「もっともらしく間違った原因分析」を量産する（#303「得られた教訓」） |
| `dev.yaml` の流用 | dev は worktree / branch / PR を前提とするが、インシデントイシューには worktree も PR も存在しない。成果物も code ではなく調査 artifact（提案）であり、終端（PR merge / close）が根本的に異なる |
| 第1層への LLM 組み込み（単一層で完結） | #303 決定 F で棄却済み。第1層が最も働くべき瞬間は LLM 側が壊れている時であり、LLM の付加価値（可読サマリ・意味的類似・統合提案）は第2層が担うと合意済み |

## インターフェース

### 入力

| 入力 | 形式 | 説明 |
|------|------|------|
| 起動コマンド | `/incident-cycle <incident_issue_id>` または `kaji run .kaji/wf/incident.yaml <incident_issue_id>` | 手動起動のみ（#303 決定 C）。`incident_issue_id` は第1層が起票したインシデントイシューの番号 |
| インシデントイシュー | GitHub Issue（`incident` ラベル、本文 1 行目に identity marker） | 第1層の出力（`kaji_harness/recovery/incident.py` の `render_incident_issue`）。識別署名・redaction 済み evidence・発生元 issue / run_id 参照を含む |
| occurrence コメント | 行頭 occurrence marker（`signature_schema` / `signature_hash` / `source_issue` / `run_id`） | 再発 run の一覧と回数 N の導出元 |
| ローカル run artifact | `.kaji-artifacts/<source_issue>/runs/<run_id>/`（run.log / result.json / steps/） | 調査の一次情報。redaction されていない生ログを含むため、引用時は sanitize が必要（後述） |
| ローカル台帳 | `.kaji-artifacts/incidents/occurrences.jsonl` | 全 occurrence の一次台帳 |

前提条件（investigate step の Step 0 ガードで検証。違反時は ABORT）:

- 対象 Issue に `incident` ラベルが付与されている
- 本文 1 行目に identity marker（`<!-- kaji-incident: ... -->`）が存在する

### 出力

| 出力 | 経路 | 説明 |
|------|------|------|
| 調査 artifact（作業ファイル） | `.kaji-artifacts/<incident_issue_id>/investigation/report.md` | gitignore 済み領域（`artifacts_dir` は main worktree 基準で解決）。step 間で共有する作業コピー。**正本はインシデントイシューのコメント**（worktree 削除の影響を受けない長期記憶） |
| 調査報告 / 査読結果 / 修正報告 / 確認結果 / 最終提案の各コメント | `kaji issue comment <id> --verdict-step <step> --verdict-status <STATUS>` | 全コメントに verdict マーカーを無条件付与（ADR 008 決定 3）。可読サマリ・意味的類似の指摘・統合提案は最終提案コメントに含まれる（Issue 完了条件「harness 経由」= kaji CLI 経由の投稿） |
| workflow verdict | PASS / RETRY / ABORT（3 経路: artifact `verdict.yaml` / コメント末尾 / stdout） | レビュー品質の判定軸。調査結論とは別軸（#303 決定 D） |

**行わない副作用**（全終端は「提案」。#303 決定 D）:

- ラベルの付与・除去（`incident:cause:*` / `incident:resolved` 等の遷移は人間）
- インシデントイシュー / 発生元イシューのクローズ・reopen
- バグイシューの起票・既存インシデントへの統合の実行
- コード変更・commit・push・PR 作成

### 使用例

```bash
# 第1層が起票したインシデント #314 の調査を手動起動する
/incident-cycle 314

# 同等の直接起動
kaji run .kaji/wf/incident.yaml 314

# 査読 cycle が exhaust（ABORT）した後、人間が artifact を確認してから再開する
kaji run .kaji/wf/incident.yaml 314 --from review --reset-cycle
```

### エラー

| 失敗 | 挙動 |
|------|------|
| `provider.type != 'github'` | `requires_provider: github` により `kaji run` 起動時に exit 2（v1 は第1層と同じく GitHub provider のみ） |
| 対象が非インシデントイシュー（`incident` ラベルなし / identity marker なし） | investigate が ABORT verdict（suggestion に対象確認手順を記載） |
| 証拠不足で結論を断定できない | エラーではない。結論 `INCONCLUSIVE` ＋棄却済み仮説＋不足証拠を列挙して PASS 可（#303 決定 D） |
| 査読 cycle が `max_iterations`（3）到達 | `on_exhaust: ABORT`。人間が介入し `--from review --reset-cycle` で再開 |
| 査読役 subagent の起動不可（Agent tool 失敗等） | main-session self-review に縮退し、縮退メタデータを調査 artifact と verdict に明記（#303 決定 B の同型運用） |

## 調査結論とレビュー verdict の別軸設計（#303 決定 D）

本機能の中核となる不変条件。**混同すると「証拠不足を正しく示した INCONCLUSIVE がレビュー未収束と
解釈され、サイクルが無理な断定を促す」ため、両軸を明確に分離する。**

| 軸 | 語彙 | 担い手 | 記録先 |
|----|------|--------|--------|
| 調査結論（conclusion） | `internal-bug` / `upstream` / `environment` / `transient` / `duplicate` / `INCONCLUSIVE` | 調査 artifact の `結論` セクション | artifact ＋ 最終提案コメント |
| レビュー verdict | `PASS` / `RETRY` / `ABORT` | 査読・確認 step | verdict 3 経路 |

査読の判定基準は**調査品質のみ**（受理基準の充足・反証への耐性・記述の充足）であり、
conclusion の値そのものではない。「結論は `INCONCLUSIVE` だが棄却仮説・不足証拠・再現結果の
記述が十分なので verdict は PASS」を明示的に許可する。

**受理基準は実証**（#303 決定 A / D）:

- conclusion が `internal-bug` / `upstream` / `environment` / `transient` / `duplicate` の場合:
  **実再現、または実障害ログの引用（run_id / パス付き citation）が必須**。欠く場合は RETRY。
- conclusion が `INCONCLUSIVE` の場合: 棄却済み仮説（各仮説の反証根拠つき）・不足証拠の列挙・
  試行した再現の記録が必須。欠く場合は RETRY。
- `risk-accepted` は人間専用語彙であり、エージェントの出力語彙（conclusion / 提案文面）に含めない。

## 制約・前提条件

- **正本は EPIC #303**。本設計と #303 の記述が矛盾する場合は #303 が優先する。
- **手動起動・人間ゲート**: 自動起動・自動昇格は実装しない（#303「自動化への移行条件」の 4 条件が
  未解消）。recovery からの child run 接続・lock 機構・予算機構は本 Issue のスコープ外。
- **査読役の書き込み禁止は指示（プロンプト）レベル**（#303 決定 A）: `gh` 書き込み系・push・
  issue 操作の禁止は agent 定義と skill の指示で課す。harness の capability policy 機構は将来の
  別 Issue 候補（リスク受容済み。手動起動＝人間が起動を認知している前提で成立）。
- **worktree 非依存**: インシデントイシューには `type:*` ラベルも worktree も無い。
  `IssueContext` の `branch_prefix` は `chore` fallback（`kaji_harness/providers/_mappings.py` の
  `DEFAULT_BRANCH_PREFIX`）となり `kaji run` は失敗しないが、注入される `worktree_dir` は実在
  しないパスを指す。**全 skill は `worktree_dir` を参照しない**。作業場所は main repo（読み取り）＋
  調査 artifact ディレクトリ（書き込み）＋使い捨て検証環境（後述）に限定する。
- **新規 Python コードを追加しない**: 変更対象は workflow YAML / skill / agent 定義 / テンプレート /
  tests / docs のみ（Issue #305 の対象列挙に `kaji_harness/` は含まれない）。verdict 発行・コメント
  投稿は既存の kaji CLI / harness 機構をそのまま使う（#303 決定 A「verdict 発行・issue へのコメントは
  harness 側が担う」）。
- **第1層の変更を含まない**（Issue スコープ境界）: 第1層のバグを発見した場合は別 Issue として起票する
  （`_shared/report-unrelated-issues.md` 準拠）。
- **redaction**: インシデントイシュー上の evidence は redaction 済みだが、ローカル
  `.kaji-artifacts/<issue>/runs/<run_id>/run.log` は生ログである。**ログをコメント / artifact に
  引用する際は、既存 `sanitize_evidence` と同方針（トークン・資格情報・秘匿 URL のマスク）を
  skill の指示レベルで課す**。
- **auto-close hazard 回避**: 全コメント・テンプレートは `docs/dev/shared_skill_rules.md`
  § auto close keyword 回避規約に従う（提案文面で「バグイシュー化」等の名詞表現を使い、
  ハザードパターンと issue 番号の連接を避ける）。
- **依存**: #304 完了済み（PR #306 マージ済み）。入力インターフェース（インシデントイシュー＋
  機械可読 occurrence marker＋redaction 済み evidence）は成立している。

## 変更スコープ

| ファイル | 変更 | 内容 |
|----------|------|------|
| `.kaji/wf/incident.yaml` | 新規 | レビュー収束サイクルの workflow 定義 |
| `.claude/skills/incident-investigate/SKILL.md` | 新規 | 調査 skill（#301 手順①〜⑥を組込み） |
| `.claude/skills/incident-investigate/artifact-template.md` | 新規 | 調査 artifact テンプレート |
| `.claude/skills/incident-review/SKILL.md` | 新規 | 査読 skill（実行型査読役の起動・転記・verdict） |
| `.claude/skills/incident-fix/SKILL.md` | 新規 | 修正 skill（指摘対応または反論） |
| `.claude/skills/incident-verify/SKILL.md` | 新規 | 確認 skill（新規指摘なしの収束規則） |
| `.claude/skills/incident-report/SKILL.md` | 新規 | 最終提案 skill（可読サマリ・類似指摘・統合提案・処遇メニュー） |
| `.claude/skills/incident-cycle/SKILL.md` | 新規 | slash wrapper（`/review-cycle` 同型） |
| `.claude/agents/kaji-incident-reviewer.md` | 新規 | 実行型査読役の agent 定義 |
| `tests/workflows/test_workflow_set_invariants.py` | 更新 | `EXPECTED_WORKFLOWS` に `incident` を追加 |
| `tests/workflows/test_incident_workflow.py` | 新規 | incident.yaml の構造 invariant ＋ 資産存在検証 |
| `docs/dev/workflow_guide.md` | 更新 | 第2層セクション・workflow 本数 / 選択表 / provider 対応表 |
| `docs/dev/incident-labels.md` | 更新 | 調査フロー・処遇判断（conclusion → ラベル対応）の追記 |
| `CLAUDE.md` | 更新 | スキル表に incident 行を追加 |

kaji は Python 単一スタック。backend / frontend の Scope 分岐はない。

## 方針

### 1. workflow 定義 `.kaji/wf/incident.yaml`

既存レビュー収束サイクル（`dev.yaml` の design-review / code-review cycle）を流用した構造
（#303「実装時に土台にできる既存資産」）。step 構成:

```yaml
name: incident
description: |
  第2層: インシデント原因調査・対応策提案のレビュー収束サイクル（手動起動）。
  第1層（インシデント検知・集約）が起票したインシデントイシューを入力とする。
  全終端は「提案」であり、ラベル遷移・クローズ・統合の実行は人間が行う。
execution_policy: auto
requires_provider: github        # v1 は第1層と同じく GitHub のみ
default_timeout: 3600            # 再現実験を含むため標準より長め

cycles:
  incident-review:
    entry: review
    loop: [fix, verify]
    max_iterations: 3
    on_exhaust: ABORT

steps:
  - id: investigate              # 提案役（調査）
    skill: incident-investigate
    agent: claude
    model: opus                  # 調査は最難度の認知タスク（#301 の実績）
    effort: high
    on: { PASS: review, ABORT: end }

  - id: review                   # 査読（実行型査読役 subagent を起動）
    skill: incident-review
    agent: claude
    model: sonnet                # 提案役 opus と分離（#303 決定 B）
    effort: high
    on: { PASS: report, RETRY: fix, ABORT: end }

  - id: fix                      # 指摘対応（調査セッションを継続）
    skill: incident-fix
    agent: claude
    model: opus
    effort: medium
    resume: investigate
    inject_verdict: true
    on: { PASS: verify, ABORT: end }

  - id: verify                   # 確認（新規指摘なし）
    skill: incident-verify
    agent: claude
    model: sonnet
    effort: medium
    on: { PASS: report, RETRY: fix, ABORT: end }

  - id: report                   # 最終提案コメントの投稿
    skill: incident-report
    agent: claude
    model: sonnet
    effort: medium
    on: { PASS: end, ABORT: end }
```

設計判断:

- **`BACK` を定義しない**: `BACK` の意味は「前段ステップの再実行」だが、review の前段は
  investigate（先頭 step）であり、根本的な再調査は `fix`（`resume: investigate` で調査セッションを
  継続）で表現できる。verdict 語彙は最小の `PASS / RETRY / ABORT` に保つ
  （`prompt.py` が `step.on` のキーを valid status として skill に注入するため、
  定義しない status は構造的に返せない）。
- **cycle 制約の遵守**: `loop` 末尾 `verify` の `on.RETRY` は `loop` 先頭 `fix` を指す
  （`docs/dev/workflow-authoring.md` § サイクル定義の制約）。self-RETRY step は存在しない。
- **`report` を独立 step にする**: review PASS（初回受理）と verify PASS（修正後受理）の
  2 経路が同一の最終提案 step に合流する。最終提案の生成箇所を 1 つにし、
  review / verify 両 skill への出力フォーマット重複を避ける。
- **`resume: investigate` の妥当性**: `fix` は調査コンテキスト（読んだログ・試した再現）を
  引き継ぐと修正コストが下がる。査読の指摘は Issue コメント（verdict マーカー付き）を正とし、
  `inject_verdict: true` で要約も注入する（`dev.yaml` の `fix-design` / `fix-code` と同型）。
  `resume` 先と同一 agent（claude）のため validation を通る。
- **timeout**: 再現実験（full pytest 約 192〜300 秒＋複数回）を含むため workflow
  `default_timeout: 3600` とする。`.kaji/config.toml` の既定値より workflow 側の宣言を優先する。

### 2. skill 群 `.claude/skills/incident-*`

全 skill 共通:

- `docs/dev/skill-authoring.md` § 手動・ハーネス両立スキルの書き方に従う
  （`## 入力` セクション、コンテキスト変数 > `$ARGUMENTS`）。
- verdict は 3 経路（作業報告コメント末尾 → stdout → artifact `verdict.yaml`）に残す。
  コメントには `kaji issue comment --verdict-step <step> --verdict-status <STATUS>` を無条件付与。
- Step 0 ガード: 対象 Issue の `incident` ラベルと identity marker を確認（violate → ABORT）。
- `worktree_dir` コンテキスト変数を参照しない（実在しないパスのため）。
- 長時間コマンドは foreground ＋明示 timeout で待ち切り、background 実行・wake 系 tool に
  依存しない（#301 の上流問題を踏まないための運用規約）。

#### incident-investigate（調査・提案役）

責務: インシデントイシュー＋ローカル run artifact を読み、調査 artifact
（`.kaji-artifacts/<issue_id>/investigation/report.md`）を作成し、全文をコメント投稿する。

**#301 の調査手順①〜⑥を skill の実行手順として組み込む**（Issue 完了条件）:

| 手順 | 内容 | 主な入力 |
|------|------|----------|
| ① 一時的か判定 | 再発回数 N・auto-resume 自己回復の有無・発生間隔から transient 可能性を評価 | occurrence marker 群、`occurrences.jsonl` |
| ② 識別署名の確認 | identity marker / fingerprint block を読み、署名が障害実体と整合するか検証（過剰統合の検出を含む） | Issue 本文、`kaji_harness/recovery/signature.py` の正規化仕様 |
| ③ バージョンの時系列 | 発生 run 前後の CLI / 依存バージョン変化と発生時期の相関を整理 | run.log、CHANGELOG、`git log` |
| ④ 上流既知不具合と照合 | WebSearch / WebFetch で上流 issue tracker・release note を独立検索 | 上流リポジトリ（例: anthropics/claude-code） |
| ⑤ 再現実験 | 使い捨て環境（`git worktree add --detach` した一時 worktree ＋隔離 venv、または scratch ディレクトリ）で最小再現を試行。結果（成功 / 失敗 / 実施不能の理由）を必ず記録 | run artifact、再現コマンド |
| ⑥ 内部／外部要因の切り分け | ①〜⑤の結果から conclusion 6 値のいずれかに到達、または `INCONCLUSIVE` として棄却済み仮説＋不足証拠を列挙 | ①〜⑤の記録 |

出力: artifact 全文＋verdict。**結論を断定できないことは失敗ではない**（`INCONCLUSIVE` で PASS 可）
を skill 冒頭に明記し、無理な断定を促さない。

#### incident-review（査読）

責務: 調査 artifact に対する**実行型査読**。main session は環境準備・転記・verdict 発行のみを担い、
検証本体は `kaji-incident-reviewer` subagent が行う。

実行手順の骨子:

1. 調査 artifact・インシデントイシュー・直近の調査報告コメントを読む
2. 使い捨て検証環境（`git worktree add --detach` の一時 worktree）を準備する
3. `kaji-incident-reviewer` subagent を起動し、artifact 全文・検証環境パス・対象 run_id 一覧を
   prompt で渡す
4. subagent の報告（観点別判定・指摘事項・受理可否の推奨）を受領し、検証環境を破棄する
5. 受理基準（§ 調査結論とレビュー verdict の別軸設計）と突き合わせ、
   査読結果コメント（verdict マーカー付き）を投稿する
6. verdict 発行: 受理 → PASS / 指摘あり → RETRY / 前提崩壊（対象が非インシデント等）→ ABORT

**capability-based fallback**（`docs/dev/development_workflow.md` § Pre-Handoff Review と同型）:
Agent tool が使えない runtime / subagent 起動失敗時は、main session が agent 定義内の同一 rubric を
自セッションで適用する。この場合は**縮退**であり、経路（`subagent` / `main-session`）と
モデル同一性を調査 artifact のメタデータと査読コメントに明記する（§ 4）。

#### incident-fix（修正）

責務: 直近の査読 RETRY コメント（verdict マーカー `step=review` または `step=verify` の最新）から
指摘リストを抽出し、各指摘に「追加調査・再実験・artifact 修正」または「技術的根拠を示した反論」で
対応する。artifact を更新し、指摘ごとの対応表をコメント投稿する。

- 指摘対応以外の scope 拡大（新しい調査論点の追加）は行わない（収束保証）。
- 指摘参照は `指摘 N` 形式（auto-close hazard 回避）。

#### incident-verify（確認）

責務: 既出指摘が解消されたかのみを確認する。**新規指摘は行わない**
（`issue-verify-code` / `issue-verify-design` と同じ収束規則）。

- 全指摘解消 → PASS（report へ）/ 未解消あり → RETRY（fix へ）。
- 判定はチェックリスト形式（指摘 N ごとに 解消 / 未解消 / 反論受理）でコメント投稿する。

#### incident-report（最終提案）

責務: 収束した調査 artifact を正本として、インシデントイシューへ最終提案コメントを投稿する。
内容（Issue 完了条件の「可読サマリ・意味的類似の指摘・統合提案」に対応）:

1. **可読サマリ**: 障害の 2〜3 文の平易な要約（テンプレート生成の起票本文より高い可読性を提供）
2. **調査結論**: conclusion ＋確度＋根拠 citation の要約
3. **対応策の提案**: 緩和策・恒久対策（バグイシュー化する場合の本文ドラフトを含む）
4. **意味的類似インシデントの指摘**: 第1層のあいまい照合候補（Issue 本文の「関連の可能性」）を
   意味レベルで再評価し、文字列類似では拾えない類似も指摘（#303 決定 F）
5. **統合提案**: conclusion が `duplicate` の場合、統合先イシューと根拠（実行は人間）
6. **処遇メニュー**: conclusion → 推奨ラベル・後続アクションの対応表（#303 決定 D の表を転記した
   チェックボックス形式。人間がそのまま実行判断できる形）
7. **モデルメタデータ**: 提案役 / 査読役モデル・縮退の有無（§ 4）

ラベル操作・クローズ・イシュー起票は一切行わない。PASS: end。

#### incident-cycle（slash wrapper）

`/review-cycle` と同型の Bash wrapper skill（Issue 完了条件の「slash wrapper」）。

- `$ARGUMENTS` 第 1 トークンを `incident_issue_id` として
  `kaji run .kaji/wf/incident.yaml <id>` を起動する。
- 引数欠落時は usage を stderr に出して ABORT verdict（`review-cycle` の Step 1 と同一パターン）。
- exit code → verdict の対応表（0 → PASS / 1 ＋ `^Workflow aborted:` マーカー → ABORT / 2・3 →
  ABORT）も `review-cycle` の表をそのまま踏襲する。
- `--before` は使わない（close step が存在せず、全終端が既に人間ゲートのため）。

### 3. 実行型査読役 `.claude/agents/kaji-incident-reviewer.md`

`kaji-code-reviewer` を雛形に、実行権限を拡張した査読役（#303 決定 A、
「`kaji-code-reviewer`（査読役 agent の雛形。ただし実行権限の拡張が必要）」）。

```yaml
# frontmatter（宣言のみ抜粋）
name: kaji-incident-reviewer
model: sonnet          # 提案役（opus）とのモデル分離（#303 決定 B）
tools: [Read, Grep, Glob, Bash, WebFetch, WebSearch]
```

本文で課す義務（#303 決定 A「反証義務＋一次情報の独立検証」）:

- **反証優先**: 調査 artifact の結論を支持する証拠ではなく、**反証する証拠を先に探す**。
- **一次情報の独立検証**: artifact の citation をそのまま信じず、引用元の run.log / result.json を
  再読して引用の正確性を確認する（ログ再読）。
- **再現の再実行**: artifact が「再現した」と主張する実験を、渡された使い捨て検証環境で
  独立に再実行する（Bash。foreground ＋明示 timeout）。
- **独立検索**: artifact の上流照合結果に依存せず、自分で WebSearch / WebFetch を行う。
- **受理基準の機械的適用**: 実証（実再現 or 実障害ログ引用）を欠く断定は受理しない。
  `INCONCLUSIVE` は記述充足（棄却仮説・不足証拠・再現記録）のみで判定する。

指示レベルの禁止事項（#303 決定 A のリスク受容を本文に明記）:

- `gh` 書き込み系・`kaji issue` 書き込み系・push・commit・ラベル操作・イシュー操作の禁止
- main checkout / 調査 artifact の変更禁止（検証は使い捨て環境内に限定）
- 正式 verdict（PASS/RETRY/ABORT）の発行禁止・イシューコメント投稿経路を持たない
  （転記と verdict は incident-review の main session が担う）
- 出力は観点別判定＋指摘事項＋受理可否の**推奨**（`accept` / `needs-fix` / `reject`）まで

出力義務: 報告 markdown の冒頭に**自セッションのモデル ID**（self-reported）を含める
（§ 4 モデルメタデータの情報源契約。main session がこの申告値を artifact メタデータへ転記する）。

### 4. モデル分離と縮退メタデータ（#303 決定 B）

- **デフォルト分離**: 提案役 = investigate step（`model: opus`）、査読役 =
  `kaji-incident-reviewer`（frontmatter `model: sonnet`）。workflow 定義と agent 定義の
  宣言で「step ごとのモデル指定」（Issue 完了条件）を満たす。
- **縮退の定義**: 提案役と査読役が同一モデルで動作した場合（例: subagent 起動不可で
  main-session fallback になり step モデルと調査モデルが一致した場合、人間が model を
  上書きして起動した場合）。
- **記録の契約**: 調査 artifact のメタデータセクションに `提案役モデル` を investigate が記録し、
  査読時に incident-review の main session が `査読役モデル` / `査読経路`（subagent /
  main-session）/ `縮退の有無` を追記する（メタデータセクションのみは review 側の書き込みを
  許可する。調査本文への変更は不可）。縮退時は verdict の evidence にも明記する
  （#303 決定 B「縮退した場合は verdict にその旨を明記」）。
- **モデル値の情報源**: `prompt.py` の注入コンテキスト変数に agent / model は含まれないため、
  記録するモデル値の取得元を次のとおり固定する。**主: セッションの自己申告値**
  （Claude Code が system prompt で提示する実行中モデル ID。`--model` override 後の
  「実際に選択されたモデル」を反映する）。査読役 subagent にも、報告 markdown に自セッションの
  モデル ID を含める義務を agent 定義で課し、main session はその申告値を転記する。
  **従: 設定値**（workflow YAML の `model` / agent frontmatter の `model`）。自己申告を取得
  できない runtime では設定値を記録する。artifact のメタデータには値とあわせて
  `情報源: self-reported | configured` を記録し、`configured` の場合は「実選択モデルと
  一致しない可能性がある宣言値」であることを意味づける。縮退判定（提案役と査読役の
  モデル同一性）はこの記録値どうしの比較で行う。
- ハード要件にはしない: モデル同一でも workflow は停止しない（ソフト要件）。

### 5. 調査 artifact テンプレート `.claude/skills/incident-investigate/artifact-template.md`

Issue 完了条件「結論 6 値・棄却済み仮説・citation・モデル縮退メタデータ」を必須セクションとして
持つ Markdown テンプレート。セクション構成:

```markdown
# インシデント調査報告: #<incident_issue_id>

## メタデータ
- 対象インシデント / 調査対象 run_id 一覧 / 再発回数 N
- 提案役モデル / 査読役モデル / 査読経路 / モデル縮退: あり・なし（理由）
- モデル値の情報源: self-reported | configured（§ 方針 4 の取得元契約に従う）

## 可読サマリ
（2〜3 文。人間向けの平易な要約）

## 結論
- conclusion: internal-bug | upstream | environment | transient | duplicate | INCONCLUSIVE
- 確度と、断定に用いた実証（再現 or 実障害ログ引用）への参照

## 根拠（citation）
（実障害ログの引用。各引用に出典 `<run_id>:<ファイル>` を付す。redaction 済みであること）

## 調査手順の実施記録
（手順①〜⑥それぞれの実施内容と結果。⑤は「実施不能」の場合も理由を記録）

## 棄却済み仮説
| 仮説 | 反証根拠（citation） |

## 意味的類似インシデント
（第1層のあいまい候補の再評価＋独自に発見した類似。duplicate 統合提案の根拠）

## 対応策の提案
（緩和策 / 恒久対策 / バグイシュー化ドラフト。すべて提案であり実行は人間）

## 不足証拠（INCONCLUSIVE 時は必須）
（何が得られれば断定できるかの列挙）
```

テンプレートの必須セクション見出しはテストで固定する（§ テスト戦略）。

### 6. データフロー（全体）

```
第1層（#304, 実装済み）                第2層（本設計）
インシデント起票・occurrence 集約  →  /incident-cycle <id>
                                      ├─ investigate: artifact 作成＋コメント投稿
                                      ├─ review: 実行型査読（反証・独立検証）
                                      │    ├─ PASS → report
                                      │    └─ RETRY → fix → verify →（PASS → report / RETRY → fix）
                                      └─ report: 最終提案コメント（サマリ・類似・統合・処遇メニュー）
                                                        ↓
                                      人間: ラベル付与・クローズ・バグイシュー化・統合の実行
```

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義する（`docs/dev/testing-convention.md`）。

### 変更タイプ

**実行時 Python コードの変更なし**。変更は (a) 既存 workflow engine が読み込む宣言的定義
（incident.yaml）、(b) プロンプト資産（skill / agent / テンプレート）、(c) tests / docs。
(a) は既存 engine の実行時挙動を新しい定義で駆動するため、**metadata-only とは扱わず**、
定義の構造健全性を恒久回帰テストで固定する。(b) の意味内容（プロンプトの質）は機械検証
できないため、変更固有検証（手動実行）で担保する。

### Small テスト

新規 `tests/workflows/test_incident_workflow.py`（`@pytest.mark.small`。
`load_workflow` / `validate_workflow` は純 parse でリポジトリ内ファイル読みのみ。
既存 `test_workflow_set_invariants.py` と同分類）:

- **構造 invariant**: `load_workflow(.kaji/wf/incident.yaml)` が成功し、
  `requires_provider == "github"`・`execution_policy == "auto"`・step 集合が
  `{investigate, review, fix, verify, report}`・cycle `incident-review` の
  `loop == [fix, verify]` / `max_iterations == 3` / `on_exhaust == ABORT` であること
- **遷移 invariant**: `verify.on.RETRY == fix`（loop 末尾 → 先頭）、review / verify の PASS が
  report に合流、全 `on` キーが `{PASS, RETRY, ABORT}` の範囲内（BACK 系なし）、
  `fix.resume == investigate` かつ同一 agent
- **モデル分離 invariant**: investigate の model と `kaji-incident-reviewer.md` frontmatter の
  model が異なること（決定 B のデフォルト分離をドリフトから保護）
- **資産存在 invariant**: incident.yaml が参照する各 skill の `SKILL.md`、
  `.claude/agents/kaji-incident-reviewer.md`、`artifact-template.md` が存在し、
  テンプレートに必須セクション見出し（メタデータ / 可読サマリ / 結論 / 根拠 /
  棄却済み仮説 / 不足証拠）と conclusion 6 値の語彙が含まれること
- **slash wrapper 契約 invariant**: `/incident-cycle` は incident.yaml から参照されない
  手動入口のため、workflow 参照ベースの存在検証では欠落を検出できない。
  `.claude/skills/incident-cycle/SKILL.md` を**明示的にテスト対象へ追加**し、
  (a) ファイルが存在すること、(b) 起動対象が `.kaji/wf/incident.yaml` であること
  （本文に起動コマンド文字列が含まれる）、(c) 引数欠落時の ABORT 経路の記述が存在すること、
  (d) exit code → verdict の縮約契約（0 → PASS / 非 0 → ABORT）の記述が存在することを固定する
- **禁止語彙 invariant**: テンプレート・agent 定義本文に `risk-accepted` が
  エージェント出力語彙として現れないこと（人間専用語彙。#303 決定 D）
- 既存 `test_workflow_set_invariants.py` の `EXPECTED_WORKFLOWS` に `incident` を追加
  （name == filename stem 検証は既存 parametrize が自動で拾う）

### Medium テスト

- 既存の `kaji validate` 系テスト（`tests/test_cli_validate.py`）が CLI 経路を保持済みのため、
  incident.yaml 固有の新規 Medium テストは追加しない。**根拠**（4 条件）: (1) 新規ロジックなし
  （検証対象は既存 `validate_workflow` / runner preflight のコード）、(2) 定義不正は Small の
  `load_workflow` / `validate_workflow` 直接呼び出しで同一コードパスを捕捉済み、(3) CLI 層を
  重ねても回帰検出情報が増えない、(4) 本節がレビュー可能な省略理由の記録である。

### Large テスト

- **実 LLM での incident.yaml E2E 実行は恒久（CI）テストにしない**。根拠: workflow engine の
  dispatch / cycle / verdict 解決の E2E は既存テスト（`tests/test_e2e_cli.py` /
  `tests/test_dev_workflow.py` 等）が stub agent で保持しており、incident.yaml は同一 engine を
  新しい宣言で駆動するのみ。プロンプト資産の質は非決定的で CI の合否判定になじまない。
- ただし skill 間の artifact / comment / verdict 受け渡し・subagent 起動と fallback・
  report 合流という**結合面は Small の構造 invariant では検証できない**ため、
  以下の**マージ前の変更固有検証**で担保する（`.claude/skills/kaji-run-verify/SKILL.md` の
  「workflow 変更後の実機検証」規約に従う）。

#### 変更固有検証（マージ前・PR 作成前に必須）

- **実施時期**: 実装完了（`make check` 通過）後、`/i-dev-final-check` → `/i-pr` に進む**前**。
  失敗した場合は本 Issue 内で修正してから再検証する（別作業に送らない）。
- **実施内容**: `/kaji-run-verify` により `kaji run .kaji/wf/incident.yaml <検証対象>` を
  実行し、workflow が report step まで完走することを確認する。
- **検証対象の選定（順序）**:
  1. 実行時点で `incident` ラベルの Issue が存在すればそれを対象とする
  2. 存在しない場合（2026-07-12 時点で該当。`gh issue list --label incident --state all` は 0 件）、
     **検証用インシデントを明示的に用意する**: 実障害 run artifact
     （`.kaji-artifacts/298/runs/260712010554/` 等、#301 の N=3 の実 run。ローカルに現存）を
     入力に、第1層の純関数（`kaji_harness.recovery` の署名算出＋
     `render_incident_issue` / `render_occurrence_comment`）を Python ワンライナーで呼んで
     title / body / occurrence コメントを生成し、`kaji issue create` で起票する。
     フォーマットの正しさ（identity marker・occurrence marker）は第1層コードが保証し、
     evidence は実障害ログなので受理基準（実証）の検証にも使える。検証後の処遇
     （クローズ等）は人間が判断する。
- **検証観点**（Issue #305 完了条件との対応）: incident.yaml が `kaji run` で起動し完走するか /
  ①〜⑥手順の実施記録が artifact に残るか / conclusion とレビュー verdict の別軸判定が機能するか
  （結論が `INCONCLUSIVE` でも記述充足なら PASS になるか、を含む）/ 最終提案コメントが可読サマリ・
  類似指摘・処遇メニューを含むか / 査読役が書き込み禁止を守り、査読経路とモデルメタデータが
  artifact に記録されるか。
- **証跡の記録先**: `/kaji-run-verify` の標準契約では結果は検証対象（インシデント Issue）に
  記録されるため、それに**加えて #305 へ証跡コメントを投稿する**: 検証対象 Issue 番号 /
  `kaji run` の run_id（`.kaji-artifacts/<検証対象>/runs/<run_id>/`）/ 各 step の verdict /
  最終提案コメントへの参照 / 観点別の確認結果。`/i-dev-final-check` はこのコメントを
  完了条件の証跡として確認する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし（既存 workflow / skill / agent 機構の適用。設計方針の正本は EPIC #303 が担う） |
| docs/ARCHITECTURE.md | なし | harness コード・アーキテクチャの変更なし |
| docs/dev/workflow_guide.md | **あり** | 「第2層: 調査・提案（Issue #305）」節の追加（§ failure triage 配下）、通常運用 workflow 本数・選択表・provider × workflow 対応表への incident 追加 |
| docs/dev/incident-labels.md | **あり** | 調査フロー（第2層の起動〜最終提案）と処遇判断（conclusion → ラベル・後続アクション対応表）の追記 |
| docs/dev/ その他 | なし | workflow-authoring / skill-authoring の仕様変更なし（既存仕様の適用のみ） |
| docs/reference/ | なし | Python API / 規約変更なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし（`kaji run` の既存機能で起動） |
| AGENTS.md / CLAUDE.md | **あり**（CLAUDE.md のみ） | スキル表に incident フェーズ（`/incident-cycle` 等）の行を追加。AGENTS.md の常時適用ルールは変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠(引用/要約) |
|--------|----------|-------------------|
| EPIC #303 本文「設計方針(合意済み決定事項)」 | `kaji issue view 303`(GitHub Issue #303) | 決定 A〜F の正本。「査読役は実行型…禁止は指示(プロンプト)レベル」「別モデルをデフォルトとするソフト要件…縮退した場合は verdict にその旨を明記」「分類はレビュー verdict とは別軸」「`risk-accepted` は人間専用語彙」等、本設計の全決定の出典 |
| Issue #301(調査手順の実施記録) | `kaji issue view 301` | 手順①〜⑥(一時的か判定/識別署名/バージョン時系列/上流照合/再現実験/内外切り分け)の実施記録。incident-investigate の手順設計の素材 |
| Issue #304 成果物(第1層) | `kaji_harness/recovery/incident.py` / `docs/dev/incident-labels.md` | L1→L2 インターフェースの正本。identity marker が本文 1 行目・occurrence marker はコメント行頭・「本文に occurrence marker は置かない(count の正本はコメントのみ)」(incident.py docstring) |
| ワークフロー定義マニュアル | `docs/dev/workflow-authoring.md` | cycle 制約「loop 末尾ステップの on.RETRY は loop 先頭ステップを指すこと」、`requires_provider` の exit 2 fail-fast、`resume` の同一 agent 制約、effort 小文字制約 |
| スキル作成マニュアル | `docs/dev/skill-authoring.md` | verdict 3 経路(artifact primary)、手動・ハーネス両立の入力規約、「cross-skill 契約は CLI / harness 層に置く」(ADR 008 決定 3) |
| 既存レビュー収束 workflow | `.kaji/wf/dev.yaml` | cycle 定義(entry/loop/max_iterations/on_exhaust)と提案役/査読役のモデル分離パターンの雛形 |
| 査読役 agent の雛形 | `.claude/agents/kaji-code-reviewer.md` | frontmatter(tools 宣言/model)・critic の立場・禁止事項・出力形式の雛形。「実行権限の拡張が必要」(#303)に従い Bash/WebFetch/WebSearch を追加 |
| slash wrapper の雛形 | `.claude/skills/review-cycle/SKILL.md` | 引数解析・exit code → verdict 対応表・ABORT 経路の同型パターン |
| workflow set invariant テスト | `tests/workflows/test_workflow_set_invariants.py` | `EXPECTED_WORKFLOWS` 集合検証。incident.yaml 追加時に本テストの更新が必須になる根拠 |
| branch_prefix fallback | `kaji_harness/providers/_mappings.py` | 「label 不在時の fallback = `chore`」— type ラベルの無いインシデントイシューでも `kaji run` の IssueContext 構築が失敗しない根拠 |
| テスト規約 | `docs/dev/testing-convention.md` | 恒久テスト不要の 4 条件・変更固有検証の記録義務。本設計のテスト戦略の判断基準 |
| workflow 実機検証 skill | `.claude/skills/kaji-run-verify/SKILL.md` | workflow 変更後の実機検証を必須とし、結果を Issue に記録する契約。本設計の「変更固有検証（マージ前）」の実施手段と証跡契約の根拠 |
| 実障害 run artifact | `.kaji-artifacts/298/runs/260712010554/` ほか（#296 / #298 の計 5 run、ローカル現存） | 検証用インシデント起票の素材（#301 の N=3 の実障害）。受理基準（実証）検証の一次情報 |
| 上流不具合(#301 の真因) | <https://github.com/anthropics/claude-code/issues/59864> | wake 系 tool が print mode で advertise されるが実行 semantics がない。skill 共通規約「foreground ＋明示 timeout」の根拠 |

