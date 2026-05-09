# ADR-001 (Revised): VERDICT プロトコルとレビューサイクルパターン

> **Status**: Draft (改定版、2026-05-04 起草)
> **Supersedes**: ADR-001 (2025 初版、"VERDICT プロトコルは拡張しない")
> **Superseded by**: なし

## Status

Draft（明日 2026-05-05 に Accept 予定）

## Context

### 初版 ADR-001 の決定（再掲）

初版は VERDICT 値を `PASS / RETRY / BACK / ABORT` の 4 値に固定し、
**「VERDICT プロトコルは拡張しない、ステート遷移で区別可能」**を中核主張とした。

理由:

1. 後方互換性
2. ステート遷移で区別可能（同じ `RETRY` でも発行ステートで遷移先が決まる）
3. シンプルさ（新値追加で全ワークフロー対応が必要）

### 改定の動機

EPIC orchestration（draft/lab/epic-orchestration.md）の検討で、
VERDICT 4 値モデルでは表現できない要件が複数現れた:

1. **`final-check` の動的後退** (#159)
   設計欠陥は `design` へ、実装欠陥は `implement` へ戻したいが、
   `BACK` は単一遷移先固定のため表現できない。

2. **post-merge-review の戻し先表現**（epic-orchestration.md §5.2 / §6.1）
   merged commit に対する戻し系 verdict（`BACK_FIX`）が必要。
   現状の単一 `BACK` では「PR 前の戻し」と「post-merge での戻し」を区別できない。
   将来「設計欠陥が merged 後に判明 → revert / hotfix / 設計戻し」の分岐拡張が要る可能性があるが、
   初期スコープは `BACK_FIX` 単一ルートに留める（拡張機構があれば後付け追加可能）。

3. **cycle 枯渇時の人手エスカレーション**（同 §3.4 課題 17）
   現状 `on_exhaust: ABORT` 直行のみ。人手介入と通知のための `PAUSE` が必要。

これらを「ステート遷移で区別」では表現しきれない:

- `final-check.BACK` を `triage` ステップで分岐させる案も検討したが、
  PASS/RETRY の semantic 反転が EPIC runner の状態機械可読性を損ねる
  （子 Issue が「設計に戻った」「実装に戻った」を verdict で直接表現できない）。
- triage skill の AI 判定誤りが verdict status に現れず、
  static workflow validation で戻し先漏れを検出できない。

### 初版 ADR-001 の前提変化

- 「シンプルさ」を維持しても、外部要件が増えれば protocol 拡張は不可避。
- 「後方互換性」は段階的な値追加で確保可能（`BACK` を `BACK_<TARGET>` で書換）。
- EPIC orchestration では複数 workflow にまたがる状態集約が要り、
  verdict 値そのもので意図を表現する方が runner 側の処理が単純化する。

## Decision

VERDICT プロトコルを**拡張可能なルールベース**に改定する。

### 1. VERDICT 値の規約

正規表現で定義する:

```
^(PASS|RETRY|ABORT|PAUSE|BACK_[A-Z][A-Z0-9_]*)$
```

**基本値（固定 4 種）**:

| 値 | 意味 |
|----|------|
| `PASS` | 次ステップへ進む |
| `RETRY` | 同ステップを再実行（or サイクル先頭へ） |
| `ABORT` | ワークフロー終了（失敗扱い） |
| `PAUSE` | 人手介入待ち（runner は待機、外部通知） |

**戻し系（拡張可）**:

`BACK_<TARGET>` 接頭辞形式で任意の戻し先を表現する。

- `<TARGET>` は UPPER_SNAKE_CASE（英大文字・数字・アンダースコア）
- ワークフロー著者が `on:` マップで `BACK_<TARGET>: <step_id>` の対応を定義
- step ID と直接対応する必要はない（意図ラベルとして機能）

例:

```yaml
- id: final-check
  on:
    PASS: pr
    RETRY: final-check
    BACK_DESIGN: design
    BACK_IMPLEMENT: implement
    ABORT: end
```

### 2. valid_statuses の決定方法

ステップ単位で `on:` マップのキー集合を valid_statuses とする
（既存実装 `runner.py:180` `valid = set(current_step.on.keys())` を踏襲）。

ワークフロー検証時 (`workflow.py`):

- 各 `on:` キーが上記正規表現に一致することを確認
- `BACK_*` の遷移先が既存 step ID を指すことを確認
- 同一 step に複数の `BACK_*` を持てる

### 3. suggestion 必須条件

以下の verdict 値は `suggestion` フィールドが空でないことを必須とする:

- `ABORT`（中断理由の記述必須）
- `BACK_*`（戻し先で何を修正すべきかの記述必須）
- `PAUSE`（人手介入で何を判断すべきかの記述必須）

`PASS / RETRY` は suggestion 任意。

### 4. レビューサイクルパターンの維持

初版 ADR-001 の中核（`review → fix → verify` パターン、
verify は新規指摘禁止、cycle カウンタによる収束保証）は維持する。

`PAUSE` は cycle カウンタ枯渇時の `on_exhaust` の選択肢として
`ABORT` と並列に提供する（既存ワークフローはデフォルト `ABORT` 維持）。

### 5. 後方互換性

- 既存 `BACK` ラベルは廃止し、`BACK_<TARGET>` への明示的書換を要求する
- 既存ワークフロー YAML の改修箇所:
  - `workflows/feature-development.yaml`: 3 箇所
    - `implement.BACK: design` → `BACK_DESIGN: design`
    - `review-code.BACK: design` → `BACK_DESIGN: design`
    - `final-check.BACK: implement` → `BACK_DESIGN: design` + `BACK_IMPLEMENT: implement`（複数化）
  - `workflows/docs-maintenance.yaml`: 1 箇所
    - `BACK: update-doc` → `BACK_UPDATE_DOC: update-doc`
- skill 側 verdict 出力テンプレートを併せて更新

`BACK` 単独形式を許容し続けるとプロトコル整合性が崩れるため、後方互換は提供しない
（影響範囲が 4 箇所と小さく、同一 PR で書換可能）。

## Consequences

### Positive

- `final-check` の戻し先が verdict で直接表現でき、static validation で漏れ検出可能
- EPIC runner の状態機械が verdict 値で子 Issue の挙動を直接判断できる
- `PAUSE` 導入により人手エスカレーションが正規ルートになる
- `BACK_*` の追加で後続 ADR / workflow 拡張が低コストになる（規約追加不要）

### Negative

- VERDICT パーサ・ワークフロー検証ロジックの改修が必要
- 既存ワークフロー YAML 4 箇所の書換が必要
- skill verdict テンプレートの更新が必要（i-dev-final-check ほか）
- 命名規約（`BACK_<TARGET>`）を skill 側 prompt で AI に正しく出力させる必要がある

### Risks

- AI が `BACK_DESIGN` と `BACK_IMPLEMENT` を誤って入れ替える可能性
  - 対策: skill prompt に判定基準を明示、cycle カウンタで発散防止、
    parser の AI fallback formatter は valid_statuses を動的に渡す（既存実装で対応済み）
- ラベル爆発（`BACK_X` が無秩序に増殖する）
  - 対策: ワークフロー単位で `BACK_*` の総数を運用上 3 以下に抑える指針を `docs/dev/workflow-authoring.md` に追記

## Implementation Notes

### コード変更箇所

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/verdict.py:195` | `("ABORT", "BACK")` ハードコード → `ABORT` または `BACK_` 接頭辞 / `PAUSE` を含む集合判定 |
| `kaji_harness/workflow.py:255` | `valid_verdicts` 固定セット → 正規表現ベース検証 |
| `kaji_harness/workflow.py:341-350` | 各 `on:` キーの正規表現マッチ + 遷移先 step 存在確認 |
| `workflows/feature-development.yaml` | BACK → BACK_DESIGN/BACK_IMPLEMENT 書換 |
| `workflows/docs-maintenance.yaml` | BACK → BACK_UPDATE_DOC 書換 |
| `.claude/skills/i-dev-final-check/SKILL.md` | verdict 選択基準に BACK_DESIGN / BACK_IMPLEMENT を追加 |
| `docs/dev/workflow-authoring.md:76` | VERDICT 値表に BACK_* / PAUSE を追加 |

### テスト追加

- `test_verdict.py`: BACK_<TARGET> パース、PAUSE パース、suggestion 必須検証
- `test_workflow.py`: 正規表現ベース validation、複数 BACK_* の検証、不正命名のエラー
- E2E: feature-development.yaml の動的後退シナリオ

## References

- 初版 ADR-001: docs/adr/001-review-cycle-pattern.md
- ADR-004: EPIC orchestration アーキテクチャ（同時改定）
- Issue #159: final-check から implement/design への動的後退
- draft/lab/epic-orchestration.md
