# [設計] feature-development workflow の final-check BACK 遷移を root-cause 別に分割

Issue: #158

## 概要

`feature-development` workflow の `final-check` ステップが返す BACK を `BACK_DESIGN` / `BACK_IMPLEMENT` の 2 値に分割し、`i-dev-final-check` skill が判定した root-cause（設計起因 / 実装起因）に応じて 1 ホップで `design` / `implement` のいずれかへ遷移できるようにする。

## 背景・目的

### ユーザーストーリー

開発者として feature workflow を実行している際、`final-check` で設計起因の不足（例: 設計書の影響ドキュメント漏れ、テスト戦略の未定義）が見つかった場合に、`implement` を経由せず直接 `design` ステップへ戻したい。理由は以下:

- 現状の builtin `.kaji/wf/feature-development.yaml` では `final-check` ステップに `BACK` 遷移が未定義であり、skill が BACK を返すと `InvalidTransition` で workflow が落ちる
- 旧 `workflows/feature-development.yaml` には `BACK: implement` が固定されており、設計起因の差し戻しでも一旦 `implement` に流れ、そこから `implement → design` の二次遷移を期待する間接構造になる
- harness は `next_step_id = current_step.on.get(verdict.status)` で `status → destination` を 1:1 解決するため、`suggestion` テキストで destination を上書きすることはできない

本機能により、`i-dev-final-check` の判定意図がそのまま遷移に反映され、無駄な実装サイクル（あるいは workflow 落ち）が解消される。

### 嬉しさ

- feature workflow の差し戻しが root-cause に追従し、設計起因 BACK が 1 ホップで `design` に到達する
- skill 設計（root-cause を区別）と YAML 設計（遷移先）が一致し、harness の動的 status 受理（`runner.py:368` `valid = set(current_step.on.keys())`）の恩恵を素直に活かせる

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| (A) 既存 `BACK` 1 種のまま、`suggestion` で destination を表現 | harness が `step.on[status]` で destination を解決する責務を持ち、skill 文面で上書きできない |
| (B) `final-check` を 2 つの skill に分離（`final-check-design-aware` / `final-check-implement-aware`） | 同じ証跡集約ロジックを二重化するだけで、判定責務は skill 内に残る。skill / YAML 双方の複雑度が増える |
| (C) `BACK` を返した後 `implement` を経由し、`implement` 内で再度 `BACK: design` を返す | 現在の `.kaji/wf/feature-development.yaml:68` で `implement.on.BACK = design` が定義済みのため動作はする。ただし `final-check` から見て **意図が伝わらない 2 ホップ** になり、log / Issue コメント上で「なぜ implement を経由した BACK が出たのか」を読み解く認知コストが残る |

→ harness の `step.on` を 1:1 に保ったまま、status 集合を root-cause 別に拡張する **(D) 本設計** を採用する。

## インターフェース

### 入力（YAML スキーマ拡張）

`final-check` ステップの `on` フィールドに新規 status キーを追加できるようにする:

```yaml
- id: final-check
  skill: i-dev-final-check
  on:
    PASS: pr
    RETRY: final-check
    BACK_DESIGN: design
    BACK_IMPLEMENT: implement
    ABORT: end
```

旧来の `BACK` キーも引き続き受理する（後方互換）。他 step（`implement` / `review-code` 等）の `BACK: design` は本 Issue では変更しない。

### 出力（skill verdict / harness 遷移）

`i-dev-final-check` skill が返す `status` 値は以下:

| status | 意味 | 遷移先（feature-development.yaml） |
|--------|------|-----------------------------------|
| `PASS` | 全完了条件充足、PR 作成へ進む | `pr` |
| `RETRY` | final-check 文脈で閉じる軽微修正 | `final-check`（自己ループ） |
| `BACK_DESIGN` | 設計起因の不足（影響ドキュメント漏れ / テスト戦略未定義 / 要件解釈の食い違い 等） | `design` |
| `BACK_IMPLEMENT` | 実装起因の不足（テスト証跡欠落 / docs 更新漏れ / 品質ゲート未通過 等） | `implement` |
| `ABORT` | 重大な前提不整合 | `end` |

`BACK`（無印）は **受け取り可能だが skill 側からは推奨しない**。後方互換のため harness はそのまま遷移を解決する（旧 YAML が動作するため）。

### 使用例

設計起因 BACK の verdict 例:

```text
---VERDICT---
status: BACK_DESIGN
reason: |
  設計書「影響ドキュメント」評価が docs/cli-guides/kaji-issue.md を見落としている
evidence: |
  実差分には kaji_harness/cli_main.py:_handle_issue の引数追加が含まれるが、
  設計書の影響ドキュメント表で対応する CLI guide 更新が「なし」と記載
suggestion: |
  /issue-design に戻り、影響ドキュメント表を再評価して
  docs/cli-guides/kaji-issue.md を「あり」に修正してください。
---END_VERDICT---
```

実装起因 BACK の verdict 例:

```text
---VERDICT---
status: BACK_IMPLEMENT
reason: |
  実装完了報告コメントに pytest 結果（Large）が欠落
evidence: |
  Step 2-1 の「issue-implement」期待コメントに pytest Large 出力が含まれず、
  設計書「テスト戦略」では Large 検証を必須としていた
suggestion: |
  /issue-implement を再開し、Large テストを実行した上で
  証跡コメントを追記してください。
---END_VERDICT---
```

### エラー / バリデーション

- YAML に `BACK_DESIGN` / `BACK_IMPLEMENT` が定義されているのに skill が `BACK`（無印）を返した場合、harness は `step.on["BACK"]` の有無で挙動が決まる: 定義されていなければ `InvalidTransition`。本設計では新 YAML から `BACK` キーを削除するため、`BACK` が返ると落ちる（fail-loud）
- YAML に `BACK_DESIGN` が定義されているのに skill が `BACK_IMPLEMENT` を返した場合: 旧 YAML との混在を避けるため、`final-check` の新 YAML には両方を必ず定義する（片方しかない構成は推奨しない）
- 旧 `BACK` キーのみを持つ古い workflow YAML は引き続き動作する（skill 側が後方互換のため `BACK` も返せる）

## 制約・前提条件

### harness 側制約（**Issue 想定に対する重要な訂正**）

Issue 本文には「harness 側の status 受理ロジックの変更（現状の動的受理で要件を満たすため不要）」とあるが、実機調査の結果これは **runtime のみ正しく、static validation では誤り**である。具体的な制約:

1. **`kaji_harness/workflow.py:292`**: `validate_workflow()` が `valid_verdicts = {"PASS", "RETRY", "BACK", "ABORT"}` という静的セットで `step.on` の各 key を検査する（同 392-394 行 `if verdict not in valid_verdicts`）。`BACK_DESIGN` / `BACK_IMPLEMENT` をそのまま追加すると `kaji validate` が失敗する
2. **`kaji_harness/verdict.py:195`**: `if verdict.status in ("ABORT", "BACK") and not verdict.suggestion` で BACK / ABORT のときのみ `suggestion` を必須化している。新 status を追加する場合、同等の必須化を行わないと「次にどこへ戻すか」が空のまま遷移する
3. **`kaji_harness/runner.py:368`**: `valid = set(current_step.on.keys())` は **runtime parse** にのみ使われ、上記 1/2 の static validation はパス済みであることが前提

→ 本設計は **harness の (1)(2) を最小拡張する**。(3) はそのまま活用する。

### YAML 側制約

- builtin `.kaji/wf/feature-development.yaml`（PyPI 配布対象 / 実行時 default）と legacy `workflows/feature-development.yaml`（リポジトリ ルート直下、過去のサンプル） の **両方** に同じ root-cause 別遷移を追加する。Issue 本文は legacy 側のみ言及しているが、`/issue-start` 以降で実際に呼ばれるのは builtin のため必須
- 同じ skill (`i-dev-final-check`) を呼ぶ `.kaji/wf/feature-development-light.yaml` / `feature-development-local.yaml` / `full-cycle.yaml` / `implement-to-pr.yaml` も final-check 直後の遷移を持つ。これらは **Issue スコープ外（refactor 領域）** とし、別 Issue で追従する。本 Issue では builtin / legacy 2 本の `feature-development.yaml` に限定する
- 他 workflow（`docs-maintenance*.yaml`）は `i-doc-final-check` skill を呼ぶため本 Issue の対象外

### skill 側制約

- `i-dev-final-check` SKILL.md の Verdict status 選択基準（Step 2-3 の「前段証跡が不足している場合」/ Verdict 出力 § status の選択基準）に `BACK_DESIGN` / `BACK_IMPLEMENT` の判定境界を明文化する
- 既存 BACK 1 種を返していたコード経路は **すべて root-cause 判定を経由する**。skill が「root-cause 不明」と判断した場合は `BACK_DESIGN` を default にせず、`ABORT`（重大な前提不整合）を返す。`BACK` 無印は SKILL.md の出力例から削除するが、テストで明示的に検証する

## 方針（Minimal How）

### 1. harness 側変更（`kaji_harness/workflow.py`）

`validate_workflow()` の `valid_verdicts` チェックを **prefix-aware** に拡張する:

```python
# Before (line 292)
valid_verdicts = {"PASS", "RETRY", "BACK", "ABORT"}
# ...
for verdict in step.on:
    if verdict not in valid_verdicts:
        errors.append(...)

# After
BASE_VERDICTS = frozenset({"PASS", "RETRY", "BACK", "ABORT"})
BACK_PREFIX = "BACK_"
# ...
for verdict in step.on:
    if verdict in BASE_VERDICTS:
        continue
    if verdict.startswith(BACK_PREFIX) and len(verdict) > len(BACK_PREFIX):
        continue  # BACK_DESIGN / BACK_IMPLEMENT / ... を許可
    errors.append(f"Step '{step.id}' has invalid verdict '{verdict}'")
```

理由: enum を `{"PASS", "RETRY", "BACK", "BACK_DESIGN", "BACK_IMPLEMENT", "ABORT"}` のように固定列挙にすると、将来 root-cause を増やすたびに harness 修正が必要になる。本 Issue の本質は「BACK の root-cause を YAML 側で名付けたい」というもので、`BACK_*` プレフィックスを拡張点として受け入れるのが最小かつ将来安全。

`cycle.on_exhaust` の検査（同 458 行 `if cycle.on_exhaust not in valid_verdicts`）も同じ判定関数を共有する。

### 2. harness 側変更（`kaji_harness/verdict.py`）

`_validate()` の suggestion 必須化を `BACK_*` プレフィックスにも適用する:

```python
# Before (line 195)
if verdict.status in ("ABORT", "BACK") and not verdict.suggestion:
    raise VerdictParseError(...)

# After
if verdict.status == "ABORT" or verdict.status.startswith("BACK"):
    if not verdict.suggestion:
        raise VerdictParseError(f"{verdict.status} verdict requires non-empty suggestion")
```

`"BACK".startswith("BACK")` は True なので無印 `BACK` も従来通り判定される。

### 3. YAML 変更

`.kaji/wf/feature-development.yaml` および `workflows/feature-development.yaml` の `final-check` ステップに以下を適用:

```yaml
- id: final-check
  skill: i-dev-final-check
  agent: claude
  model: opus
  effort: medium  # builtin のみ
  on:
    PASS: pr
    RETRY: final-check
    BACK_DESIGN: design
    BACK_IMPLEMENT: implement
    ABORT: end
```

builtin から `BACK` キー（現在は未定義）を新規には追加しない。legacy 側の既存 `BACK: implement` は削除して `BACK_DESIGN` / `BACK_IMPLEMENT` に置換する。

### 4. skill 変更（`.claude/skills/i-dev-final-check/SKILL.md`）

以下 3 箇所を更新:

- **Step 2-3「前段証跡が不足している場合」**: BACK 単一表記を `BACK_DESIGN` / `BACK_IMPLEMENT` に分け、root-cause 判定の境界を明示
  - 設計起因の例: 影響ドキュメント評価漏れ、テスト戦略未定義、要件解釈の食い違い → `BACK_DESIGN`
  - 実装起因の例: 前段コメント欠落、最新判定が Changes Requested のまま、品質ゲート未通過、docs 更新漏れ → `BACK_IMPLEMENT`
- **Step 8「最終チェックコメントのテンプレート」**: 判定行を `PASS / RETRY / BACK_DESIGN / BACK_IMPLEMENT` に更新
- **Verdict 出力 § status の選択基準**: 既存「BACK」行を 2 行に分割し、root-cause 判定基準を明記

### 5. tests 変更（`tests/test_feature_development_workflow.py`）

既存 Small / Medium のクラスに以下のケースを追加（**新規ファイル作成はしない**）:

- `final-check` ステップが `BACK_DESIGN` / `BACK_IMPLEMENT` キーを持ち、それぞれ `design` / `implement` に遷移する
- `final-check` ステップが `BACK`（無印）キーを **持たない**（旧固定遷移の負債を残さないことを保証）
- 既存の `kaji validate` Medium テストが新 YAML でも exit 0 で通る

加えて以下の Small テストを `kaji_harness` 配下に追加（既存 test_*.py ファイルが該当する場合は同居）:

- `tests/test_workflow_validation.py`（既存があればそこに追記、無ければ新規）に `validate_workflow` の `BACK_*` プレフィックス受理ケース / `BACK_` 単独などの不正ケースを追加
- `tests/test_verdict.py`（既存）に `BACK_DESIGN` / `BACK_IMPLEMENT` で suggestion 空の場合 `VerdictParseError` になることを追加

## テスト戦略

### 変更タイプ
実行時コード変更（harness の `workflow.py` / `verdict.py` ロジック変更を含むため）

### Small テスト

- **`workflow.py` の `validate_workflow`**:
  - `BACK_DESIGN` / `BACK_IMPLEMENT` を含む step.on を受理し、`WorkflowValidationError` を投げない
  - `BACK_` 単体（プレフィックスのみ）は不正として弾く
  - `BACK_FOO` のような未知 root-cause も形式的には受理する（prefix-based、Issue スコープでは利用しない）
  - 既存の `{"PASS", "RETRY", "BACK", "ABORT"}` のみの YAML は引き続き通る（後方互換）
- **`verdict.py` の `_validate`**:
  - `status=BACK_DESIGN` で `suggestion` 空 → `VerdictParseError`
  - `status=BACK_IMPLEMENT` で `suggestion` 空 → `VerdictParseError`
  - `status=BACK` で `suggestion` 空 → `VerdictParseError`（既存挙動を回帰検証）
  - `status=BACK_DESIGN` で `suggestion` あり → 通過
- **`tests/test_feature_development_workflow.py` Small**:
  - `final-check` ステップに `BACK_DESIGN: design` / `BACK_IMPLEMENT: implement` が存在
  - `final-check` ステップに `BACK`（無印）が存在しない

### Medium テスト

- **`kaji validate workflows/feature-development.yaml` と `kaji validate .kaji/wf/feature-development.yaml`** が両方 exit 0
- **`runner` 経由の遷移シミュレーション** は既存の Medium テスト構造がない場合は不要。Small で `step.on.get("BACK_DESIGN")` の dict ルックアップ自体は検証済みのため、runtime 経路は (3) `runner.py:368` の `valid_statuses = set(current_step.on.keys())` でカバー済み

### Large テスト

恒久 Large テストは追加しない。理由（`testing-convention.md` 4 条件の充足）:

1. **独自ロジック追加なし**: 本変更は status 集合と判定境界の調整のみで、独自の外部 I/O や状態遷移エンジンを新規導入しない
2. **既存ゲートで捕捉**: AI が誤って `BACK_DESIGN` / `BACK_IMPLEMENT` を不適切に返す可能性は、`/kaji-run-verify` による 1 本完走で検出可能（既存運用ゲート）
3. **回帰検出情報がほぼ増えない**: 実 AI を呼ぶ Large は flaky になりやすく、root-cause 判定の正確性は Small で skill SKILL.md と YAML の整合を検証する方が信号対雑音比が高い
4. **省略理由のレビュー可能性**: 本セクションが省略理由を明文化している

→ 代わりに **変更固有検証** として以下を実施し、結果を Issue コメントに記録する:

- `kaji run .kaji/wf/feature-development.yaml <test-issue>` を 1 本完走させ、設計起因 BACK が `design` に 1 ホップで到達することを観察する（Issue 完了条件 4 項目目に対応）
- 実行コマンドは `/kaji-run-verify` 経由（既存運用フロー）

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 既存 dispatcher / verdict アーキテクチャ内の最小拡張で、新規 ADR 相当の決定なし |
| `docs/ARCHITECTURE.md` | なし | dispatcher の責務範囲は不変 |
| `docs/dev/development_workflow.md` | **あり** | line 41-43 の mermaid に `BACK: implement` / `BACK: design` が記載されており、`BACK_IMPLEMENT` / `BACK_DESIGN` に書き換える |
| `docs/dev/workflow_guide.md` | 要確認 | BACK 表記があれば同様に書き換える（Issue 完了条件 2 に紐づくため implement 段階で grep ベースで確認） |
| `docs/dev/workflow-authoring.md` | **あり** | line 130（仮）の `BACK = 差し戻し` 説明に `BACK_*` プレフィックスの拡張ルールを追記する。これは harness 側の `BACK_*` 受理を YAML 作者に伝える唯一の場所 |
| `docs/reference/` | なし | API 変更を伴う public IF はない |
| `docs/cli-guides/` | なし | CLI 仕様（`kaji run` / `kaji validate`）は不変 |
| `CLAUDE.md` | なし | プロジェクト規約に該当変更なし |
| `.claude/skills/i-dev-final-check/SKILL.md` | **あり** | 上記「方針 §4」に記載済み |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| harness runner の動的 status 受理 | `kaji_harness/runner.py:368` | `valid = set(current_step.on.keys())` で runtime は YAML から動的に status を受理。新規 status を YAML に書けば runtime parse は通る |
| harness workflow validation の静的 enum | `kaji_harness/workflow.py:292` / 392-394 | `valid_verdicts = {"PASS", "RETRY", "BACK", "ABORT"}` と `if verdict not in valid_verdicts` の組合せで、validate_workflow は固定 enum を要求する。本設計が拡張する対象 |
| harness verdict の suggestion 必須化 | `kaji_harness/verdict.py:195` | `if verdict.status in ("ABORT", "BACK") and not verdict.suggestion` で BACK / ABORT のみ suggestion 必須。新 status を同等扱いする根拠 |
| harness runner の遷移解決 | `kaji_harness/runner.py:404` | `next_step_id = current_step.on.get(verdict.status)` / 不在で `InvalidTransition`。status と destination が 1:1 で解決される根拠 |
| 旧 workflow YAML の BACK 固定 | `workflows/feature-development.yaml:98-106` | `BACK: implement` が固定されており、root-cause 別遷移が不可能な現状の根拠 |
| 新 builtin の BACK 未定義 | `.kaji/wf/feature-development.yaml:102-110` | final-check に BACK 自体が無く、現状 BACK を返すと `InvalidTransition` で落ちる |
| 起票元 PR review | PR #157 review thread (Codex P2, comment 3142168439) | Issue 本文「背景」節からの転載。「skill 側の suggestion テキストでは destination を上書きできない」の指摘根拠 |
| Issue スコープ宣言 | Issue #158 本文 §スコープ | 本 Issue を type:feature に限定し、他 workflow の BACK 再設計（refactor）、harness 受理ロジック変更を「不要」と宣言（後者は本設計で訂正） |
