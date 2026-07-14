# Workflow Completion Criteria

各フェーズで何を確認し、どこで全体確定するかの対応表。

## フェーズ別の確認項目

| 項目 | review-ready | design | review-design | implement | review-code | i-dev-final-check | i-doc-final-check |
|------|-------------|--------|---------------|-----------|-------------|-------------------|-------------------|
| Issue 本文の記述品質 | ✅ | - | - | - | - | - | - |
| テスト分類・実行面の記載 | - | ✅ | ✅ | - | - | ✅ | - |
| docs 影響評価 | - | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 実装・差分反映 | - | - | - | ✅ | ✅ | ✅ | - |
| 最終品質ゲート（`make check`） | - | - | - | 参考実施可 | 参考確認可 | ✅ | - |
| docs-only 最終整合確認（`make verify-docs`） | - | - | - | - | - | - | ✅ |
| workflow 内完了条件の段階確認 | - | ✅ | ✅ | ✅ | ✅ | ✅（集約） | ✅（集約） |
| Issue 本文更新 | - | - | - | - | - | ✅ | ✅ |
| PR へ進める最終判定 | - | - | - | - | - | ✅ | ✅ |

## 判定原則

- 前段で確認できる項目を final-check に先送りしない
- final-check は前段の証跡を集約し、未充足なら `RETRY` または `BACK` を返す
- `RETRY` は final-check 文脈で閉じる軽微修正に限定する（自己ループ）
- `BACK` は原因に応じて design / implement / docs の前段に戻す
  - 戻し先で skill が即 `ABORT` を返してはならない。`design` に戻された場合の skill 側挙動は [`.claude/skills/issue-design/SKILL.md`](../../.claude/skills/issue-design/SKILL.md) Step 1.6 / 1.7（BACK 経由再起動の検出と分岐 / 設計再確認フロー）で `PASS` 復帰させる規約に従う

## workflow 内完了条件と事後確認の分離

### 判定基準

`## 完了条件` の各項目は、次の問いで分類する。

> workflow を RETRY して同じ入力から再実行したとき、環境に依存せず同じ結果を得られるか。

- **Yes**: workflow 内で再現可能な完了条件として扱い、final-check の PASS 判定と
  チェックボックス更新の対象にする
- **No**: `## 完了条件` の末尾に置く
  `### ワークフロー完了後の確認項目` へ分離し、final-check の PASS 判定と
  チェックボックス更新の対象外にする

後者には、PR merge 後の状態、実機 host への適用やサービス再起動後の状態、
production / staging 固有環境、外部サービスの非同期応答、admin 権限や未登録 secret を
必要とする確認が含まれる。単に実行時間が長い、検証が難しい、agent が未実施という理由だけで
事後確認へ移してはならない。workflow 内で再現可能なら通常の完了条件として残す。

### Issue 本文の形式

事後確認がある場合は、`## 完了条件` 内の**末尾サブセクション**を次の名前に統一する。

```markdown
## 完了条件

- [ ] workflow 内で確認する条件

### ワークフロー完了後の確認項目

- [ ] merge 後に確認する具体的な状態
```

事後確認がない場合は、同サブセクションを省略するか、チェックボックスではない
`- なし` を置く。通常完了条件と事後確認を同じ箇条書きへ混在させない。

### ライフサイクル

1. `issue-create` が type 別 template に分離欄を用意する
2. `issue-review-ready` が通常完了条件の workflow 内判定可能性と分離欄の配置を確認する
3. `issue-fix-ready` / `issue-design` が誤分類を見つけた場合は項目を適切な側へ移す
4. `i-dev-final-check` / `i-doc-final-check` は通常完了条件だけを照合・更新する
5. `issue-close` が未チェックの事後確認を follow-up Issue へ移管してから親 Issue を close する

既存 open Issue の一括移行は行わない。新規作成時または各 workflow で当該 Issue を扱う際に
適用する。

## admin 権限を要する検証の扱い

### 原則

**AI 単独で達成不能な検証は通常の完了条件に含めない。** 手順を docs に残し、
`### ワークフロー完了後の確認項目` の user 側運用タスクとして位置付ける。

具体的には以下のいずれかに該当する検証は、Issue の完了条件（PR merge を阻害する条件）に含めず、merge 後 / 初回リリース前 / 初回利用前までに user が実施する運用タスクとして整理する:

- **admin 権限が必要な検証**: GitHub Repository Settings 変更、organization 設定変更、Branch Protection 変更などを伴う動作確認
- **外部 secret / credential が必要な検証**: GitHub App secret、外部 SaaS の API token、本番相当の認証情報が事前登録されていなければ動作しない検証
- **物理デバイス / 専用環境が必要な検証**: 特定の OS / GPU / モバイル端末 / 本番ネットワーク等を伴う検証

### AI フェーズで担保する代替検証

上記理由で動作検証を後回しにする場合、AI フェーズでは以下の **静的検証** で品質を担保する:

- 設定ファイルの schema validation（公式 JSON schema 等）
- workflow / 設定ファイルの lint（`actionlint`、`yamllint` など）
- 移植元 / 既存実装との diff レビュー
- 構文 / 値の妥当性確認（`python -c "import json; ..."` 等）

### docs への反映義務

- 動作検証を user に委ねる場合、**手順を docs に残すことを必須とする**（admin 向け setup 手順 + 初回利用前のチェックリスト等）
- Issue 本文の `### ワークフロー完了後の確認項目` に対応タスクを明記し、merge 阻害条件ではないことを明示する

### 適用例

- Issue #153（release-please 導入）: dry-run は GitHub App secret 登録（admin 権限要）が前提。AI フェーズでは JSON schema validation + actionlint + kamo2 移植元との diff レビューで代替し、dry-run は `docs/operations/release/admin-setup.md` および `docs/operations/release/runbook.md` の「初回リリース前チェックリスト」に従い user が post-merge / 初回リリース前に実施する。

## 各ステップの証跡責務

各ステップは、自分の段階で確認可能な Issue 完了条件を確認し、後段が追跡可能な形で証跡を残す。

### 証跡の定義

**証跡（evidence）** とは、あるステップが Issue の完了条件を確認した事実を示す記録である。以下のいずれかの形式で残す。

| 証跡の形式 | 説明 | 例 |
|-----------|------|-----|
| Issue コメント | ステップ完了時に投稿する構造化コメント | 設計レビュー結果、実装完了報告、コードレビュー結果 |
| コミット内容 | Git に記録された成果物 | 設計書、テストコード、docs 更新 |
| コマンド出力 | Issue コメントに含まれる実行結果 | pytest 出力、`make check` の結果 |

### ステップ別の確認責務と証跡

| ステップ | Issue 完了条件のうち確認する範囲 | 確認の根拠 | 証跡の残し方 |
|----------|-------------------------------|-----------|-------------|
| `issue-review-ready` | Issue 本文の記述品質（構造・具体性・根拠・検証可能性・整合性・スコープ推定・workflow 内判定可能性） | 共通観点 1〜7・14 と type 別追加観点の充足 | Issue コメント（レディネスレビュー結果 + PASS/RETRY/ABORT 判定） |
| `issue-design` | 設計書で対応可能な条件（テスト方針、docs 影響評価、技術制約） | 設計書の各セクションが条件に対応 | 設計書コミット + Issue コメント（設計完了報告 + Step 2.6 Self-Check 結果） |
| `issue-review-design` | 設計書が完了条件を充足できる構造か | 設計書の S/M/L 網羅性、一次情報整合、影響評価 | Issue コメント（レビュー結果 + Approve/CR 判定） |
| `issue-implement` | 実装・テストで対応可能な条件（実装完了、テスト通過、品質ゲート、docs 更新） | pytest 出力、`make check` 出力 | Issue コメント（実装完了報告 + テスト結果 + 品質チェック結果 + Step 8.5 Pre-Handoff Review 結果） |
| `issue-review-code` | 実装が設計と整合し、テスト・docs が揃っているか | 独立テスト実行、差分レビュー | Issue コメント（レビュー結果 + Approve/CR 判定） |
| `i-dev-final-check` | **workflow 内の全条件**（事後確認を除く前段証跡の集約 + 未確認条件の最終確認） | 前段コメントの走査 + 最終品質ゲート実行 | Issue コメント（最終チェック結果）+ **Issue 本文更新** |
| `i-doc-final-check` | **docs-only workflow 内の全条件**（事後確認を除く docs 整合 + Issue 状態） | docs 差分 + リンクチェック | Issue コメント + **Issue 本文更新** |

### type 別に追加で確認する項目

Issue の `type:` ラベルに応じて、前段スキルが確認する完了条件の追加項目が変わる。final-check はこれらの type 別項目も集約して確認する。

| ステップ | feat 追加確認 | bug 追加確認 | refactor 追加確認 |
|----------|---------------|--------------|-------------------|
| `issue-design` | 使用例・エラー挙動が設計書に含まれている | OB / EB / 再現手順 / 根本原因が設計書に含まれている | ベースライン計測コマンド・改善指標が設計書に含まれている |
| `issue-implement` | 設計書のユースケースが受け入れテストでカバーされている | 再現テストが Red → Green に遷移したログが Issue コメントに含まれている（実ログ代替時は後述の escape clause を参照） | ベースライン値 / 改修後値が Issue コメントに含まれ、改善指標を達成 |
| `issue-review-code` | IF 契約（型・命名・戻り値・エラー）が設計書と一致 | 同根欠陥の波及修正が行われている / 再現テストの Red→Green 証跡が確認できる（実ログ代替時は後述の escape clause を参照） | 既存テスト全件 PASS（振る舞い非変更） / safety net テストの追加 |
| `i-dev-final-check` | 上記 3 段の type 別証跡を横断集約 | 同上 | 同上 |

**bug の Red→Green 証跡の escape clause（実ログによる実装前 Red 代替）**: bug Issue 本文またはリンク先に OB を直接示す実世界障害ログ（失敗コマンド・エラー文言・exit code・API 応答・関連 Issue/PR の実行ログ等）が存在し、恒久回帰テストがその OB に対応する EB を検証している場合、その実ログを **実装前 Red 証跡の代替**として扱ってよい。この場合、`issue-review-ready` は ABORT せず admissible とし、`issue-review-code` は合成再現の実装前 FAIL ログが無いことのみを理由に差し戻してはならない。ただしこの例外は実装前 Red ログの代替に限る。修正後の回帰テスト Green・影響範囲の品質ゲート・同根欠陥確認は免除しない。実ログが OB と対応しない場合、単なる省力化・実行時間短縮・後付け都合を理由とする場合は代替不可。

**canonical 外 type（`type:test` / `type:chore` / `type:perf` / `type:security`）**: 上記 feat 列の追加確認を適用する（フォールバック規則）。

**type:docs の扱い**: dev workflow ではなく docs-only workflow（`/i-doc-*`）が担当する。dev workflow の各スキルでは対象外として処理を停止し、`/i-doc-update` に誘導する。

**fix/verify 系スキル（`issue-fix-*` / `issue-verify-*` / `pr-fix` / `pr-verify` / `i-doc-fix` / `i-doc-verify`）では type 別追加確認を行わない**。レビューサイクルの収束保証のため、新規指摘を行わないという原則に従う。

### docs-only / metadata-only / packaging-only の追加確認

`docs/dev/testing-convention.md` の 4 条件に基づき、恒久テストの追加が不要と判断できるケース（docs-only / metadata-only / packaging-only）では、final-check は以下を追加で確認する:

- 4 条件の充足が設計書または Issue コメントで明示されている
- 代替検証（`make verify-docs` / `make verify-packaging` / grep 検証等）の実行ログが残っている

### 前段証跡の確認方法

`i-dev-final-check` は以下の手順で前段の証跡を集約する。

1. `gh issue view [number] --comments` で Issue コメントを取得
2. 各ステップの完了報告コメントが存在するか確認
3. 各コメントの判定結果（Approve / CR）を確認
4. Issue 本文の完了条件リストから `### ワークフロー完了後の確認項目` を除外して照合し、充足 / 未充足を判定

## Issue 本文更新プロトコル

### コメントと本文の役割分担

| 役割 | 格納先 | 説明 |
|------|--------|------|
| 各ステップの詳細報告 | Issue **コメント** | テスト結果、レビュー指摘、品質チェック出力など。ステップごとに投稿 |
| workflow 内完了条件の充足状態 | Issue **本文** | 事後確認を除くチェックボックスの更新で「どの条件が確認済みか」を表現 |
| workflow 完了後の確認項目 | follow-up Issue | 親 Issue close 前に未完了項目を移管し、人間が証跡を記録して手動 close |
| 設計書アーカイブ | Issue **本文** | NOTE ブロック直下に `<details>` で添付（`/i-dev-final-check` Step 7.5） |

### 本文更新のタイミングと実行者

| タイミング | 実行者 | 更新内容 |
|-----------|--------|---------|
| `/issue-start` 時 | `issue-start` | 本文先頭の NOTE ブロックに Worktree / Branch を追記 |
| final-check PASS 時 | `i-dev-final-check` / `i-doc-final-check` | 事後確認を除く完了条件のチェックボックスを `[x]` に更新、設計書を NOTE 直下に添付（dev のみ） |
| Issue close 前 | `issue-close` | 未完了の事後確認を follow-up Issue へ移管し、親本文へ冪等マーカーを追記 |
| final-check BACK 時 | `i-dev-final-check` / `i-doc-final-check` | 本文更新なし（コメントで未充足条件と戻し先を明示） |
| final-check RETRY 時 | `i-dev-final-check` / `i-doc-final-check` | 本文更新なし（軽微修正後に再実行するため） |
| PR 作成時 | `i-pr` | NOTE ブロックに `PR: #NNN` を追記 |

### チェックボックス更新の方法

Issue 本文に `## 完了条件` セクションがあり、チェックボックス形式（`- [ ]`）で条件が列挙されている場合、
`### ワークフロー完了後の確認項目` より前にある確認済み条件だけを更新する。同サブセクション内の
チェックボックスは `[ ]` のまま維持する。

```bash
# 現在の本文を取得
gh issue view [number] --json body -q '.body' > /tmp/issue-body.md

# チェックボックスを更新（手動または sed）
# - [ ] 条件A → - [x] 条件A

# 更新を反映
gh issue edit [number] --body-file /tmp/issue-body.md
```

### 設計書の NOTE 直下添付

`/i-dev-final-check` の PASS 時に、設計書（`draft/design/issue-XXX-*.md`）を Issue 本文の NOTE ブロック直下に `<details>` タグで添付する。worktree 削除後も Issue から設計書を辿れるようにするため。

```markdown
> [!NOTE]
> **Worktree**: `../kaji-feat-123`
> **Branch**: `feat/123`
> **PR**: #456

<details>
<summary>設計書: issue-123-xxx.md</summary>

(設計書本文)

</details>

(元の Issue 本文)
```

### 本文にチェックボックスがない場合

完了条件が自由記述の場合は、final-check コメントに充足状態を一覧し、本文末尾に注記を追加する。

```markdown
> **Final Check**: YYYY-MM-DD に workflow 内完了条件の充足を確認。事後確認は判定対象外。詳細は最終チェックコメント参照。
```

### BACK 時の本文更新

未充足条件がある場合、Issue 本文のチェックボックスは `[ ]` のまま残し、コメントで未充足理由と戻し先を明示する。

```markdown
## final-check 結果: BACK

### 未充足条件

- `- [ ] 条件X` — 理由: ○○が不足。戻し先: `issue-implement`
- `- [ ] 条件Y` — 理由: △△の整合が取れていない。戻し先: `issue-design`
```

## follow-up Issue への移管

`issue-close` は親 Issue を close する前に、`### ワークフロー完了後の確認項目` を確認する。

- セクションなし、`- なし`、全項目 `[x]` の場合は follow-up を作成しない
- 未チェック項目がある場合だけ、`.claude/skills/issue-close/templates/follow-up-issue.md`
  を使って follow-up Issue を作成する
- follow-up 本文には親 Issue、未完了項目、参照 docs、証跡記録先、人間による手動 close 方針を含める
- 既存 open Issue の完全一致タイトルを検索して再利用し、親本文の
  `<!-- kaji-follow-up-issue: <issue-id> -->` マーカーで再実行時の重複を防ぐ
- マーカーの参照先を再利用できるのは、その Issue が **open** かつタイトル・親マーカーが
  一致する場合だけとする。close 済みの Issue は再利用しない
- follow-up 作成後に親マーカー追記が失敗した場合、再実行時は既存 open Issue を再利用する
- 同一タイトルが複数ある、follow-up 作成に失敗する、親マーカー追記に失敗する、または
  マーカーの参照先が close 済みの場合は `ABORT` とし、親 Issue を close しない
  （close 済み参照先は、親に未完了項目が残るのに追跡先が閉じている不整合であり、
  人間が親本文の更新・follow-up の reopen・マーカー行の削除のいずれかで解消する）

移管は確認の完了を意味しない。follow-up Issue の各項目は、実施者が具体的証跡をコメントへ
記録して `[x]` に更新し、全項目完了後に人間が手動で close する。
