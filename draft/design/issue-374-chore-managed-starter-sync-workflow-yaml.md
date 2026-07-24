# [設計] managed starter sync workflow YAML の追加

Issue: #374

## 概要

managed starter 同期（`update-starter` → 別 session の `review-starter-update` → 承認後の
`release-starter`）を、bounded retry と publish 前の有人停止境界を持つ repository 固有 workflow
`.kaji/wf/custom/operations/starter-sync.yaml` として宣言する。

## 背景・目的

### 現状の問題

[starter sync runbook](../../docs/operations/release/starter-sync-runbook.md) § Workflow は
candidate 作成 → 別 session review → workflow 外の人間承認 → atomic publish の順序を定めているが、
この 3 skill を接続する workflow YAML が存在しない。3 skill を手動で個別起動すると、

- review が `RETRY` を返したときに `update-starter` へ戻す遷移が実行者の記憶に依存する
- RETRY の反復回数に上限がなく、収束しない場合の停止条件が宣言されていない
- publish 直前で停止する手順（`--before`）が運用者ごとに揺れる

### ユースケース

kaji maintainer として、kaji Release 後の managed starter 追随を、
「独立 review」と「publish 前の人間承認」を維持したまま再現可能な 1 本の workflow で実行したい。

- **Role**: kaji maintainer
- **Goal**: starter 追随の遷移・retry 上限・停止境界を宣言物として固定する
- **Action**: `kaji run .kaji/wf/custom/operations/starter-sync.yaml <tracking_issue_id> --before release-starter`
  で candidate 作成と独立 review まで進め、承認後に `--from release-starter` で publish する

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| workflow を作らず 3 skill の手動起動を継続 | RETRY 遷移と retry 上限が宣言されず、Issue の完了条件（bounded retry）を満たせない |
| `.kaji/wf/official/` に置く | official は kaji が Release で配布・更新する公式提供物。managed starter 同期は kaji repository 固有の保守運用であり、下流利用者に配布する対象ではない（[workflow-authoring.md](../../docs/dev/workflow-authoring.md) § 所有権） |
| workflow を `review-starter-update` の PASS で終端し、`release-starter` を含めない | Issue 完了条件が `release-starter` までの正常系遷移の定義を要求している。step として宣言した上で `--before` barrier で停止させる方が、publish への到達経路が宣言物として辿れる |
| 汎用の manual approval step を workflow schema に追加する | Issue のスコープ境界で明示的に除外。既存の `--before` / `--from` barrier で同じ停止境界を表現できる |

## インターフェース

### 入力

| 項目 | 型 | 説明 |
|------|-----|------|
| workflow file | path | `.kaji/wf/custom/operations/starter-sync.yaml` |
| `<tracking_issue_id>` | int（GitHub Issue 番号） | starter-sync tracking Issue。本文に `starter_repo` / `target_kaji_release` / 任意 `starter_path` を持つ（[runbook](../../docs/operations/release/starter-sync-runbook.md) § Tracking Issue） |
| provider | config | `config.provider.type == "github"` であること。不一致は `kaji run` 起動時 exit 2 |

workflow 自体は追加の CLI 引数を定義しない。停止・再開は `kaji run` の既存 flag で行う。

```bash
# Phase 1: candidate 作成 → 独立 review（publish 直前で停止）
kaji run .kaji/wf/custom/operations/starter-sync.yaml <tracking_issue_id> --before release-starter

# --- workflow 外で人間が candidate SHA と tag を確認し、明示承認する ---

# Phase 2: 承認後の publish
kaji run .kaji/wf/custom/operations/starter-sync.yaml <tracking_issue_id> --from release-starter
```

### 出力

| 種別 | 内容 |
|------|------|
| step 遷移 | `update-starter` → `review-starter-update` →（`RETRY` 時は `update-starter` へ）→ `release-starter` → `end` |
| verdict | 各 step が tracking Issue コメント / stdout / `verdict_path` へ `PASS` / `RETRY` / `ABORT` を出力（各 SKILL.md の Verdict 節が正本） |
| 副作用 | starter local main への commit（`update-starter`）、tracking Issue コメント（全 step）、承認後の annotated tag + main の atomic push と GitHub Release 作成（`release-starter`） |
| 停止 | `review-starter-update` の RETRY が `max_iterations: 3` に達すると `on_exhaust: ABORT` で run 停止（exit 1） |

### 使用例（workflow YAML の全文案）

```yaml
name: starter-sync
description: |
  series 自動選択対象外。kaji repository 固有の managed starter 同期運用（手動起動）。
  starter-sync tracking Issue を入力に、candidate 作成 → 別 session の独立 review →
  承認後の atomic publish を回す。
  publish は workflow 外の人間承認を前提とするため、通常は
  `--before release-starter` で停止し、承認後に `--from release-starter` で再開する。
execution_policy: auto
requires_provider: github
default_timeout: 3600

cycles:
  starter-review:
    entry: review-starter-update
    loop: [update-starter, review-starter-update]
    max_iterations: 3
    on_exhaust: ABORT

steps:
  - id: update-starter
    skill: update-starter
    agent: claude
    model: opus
    effort: high
    on:
      PASS: review-starter-update
      ABORT: end

  - id: review-starter-update      # update-starter と別 session（resume を持たない）
    skill: review-starter-update
    agent: codex
    model: gpt-5.6-sol
    effort: high
    on:
      PASS: release-starter
      RETRY: update-starter
      ABORT: end

  - id: release-starter            # 通常は --before release-starter で手前停止する
    skill: release-starter
    agent: claude
    model: opus
    effort: high
    on:
      PASS: end
      ABORT: end
```

### エラー / 異常系

| 事象 | 挙動 |
|------|------|
| `config.provider.type != "github"` | `kaji run` が workflow load 直後に exit 2（`requires_provider` fail-fast） |
| tracking Issue の必須 field 欠落 / remote identity 不一致 / Release 不在 | `update-starter` が `ABORT` → `end`（skill 側 guardrail） |
| candidate SHA が review 中に変化 | `review-starter-update` が `ABORT` → `end` |
| review 指摘が 3 反復で収束しない | `starter-review` cycle の `on_exhaust: ABORT` で停止 |
| 独立 review verdict / meta 不整合、remote main の前進 | `release-starter` が fail-closed で `ABORT` |
| publish の部分失敗 | workflow の RETRY edge では復旧しない。人間が `--from release-starter` で再実行する（後述「方針」参照） |

## 制約・前提条件

- `.kaji/wf/custom/**` は利用者所有であり、kaji の pytest 回帰対象外。tracked な YAML に対して
  `make validate-workflows` の L1/L2/L3 静的検証のみが掛かる
  （[workflow-authoring.md](../../docs/dev/workflow-authoring.md) § 品質保証の責務境界）
- `name:` は filename stem と一致させる（`starter-sync`）
- skill-step は `agent` 必須。3 skill はいずれも `exec_script` frontmatter を持たない
  （`kaji_harness/preflight.py:90-95`）
- skill file の解決は `workdir / skill_dir / <name> / SKILL.md`（`kaji_harness/skill.py:49`）で
  **agent 非依存**。`.claude/skills/` 配下の 3 skill は claude / codex いずれからも解決できる
- cycle の loop 末尾 step の `on.RETRY` は loop 先頭 step を指す必要がある
  （`kaji_harness/workflow.py:660-671`）
- cycle の iteration は `step.id == cycle.loop[-1] and verdict.status == "RETRY"` のときだけ
  increment される（`kaji_harness/runner.py:1061-1062`）
- `make validate-workflows` は `git ls-files -- '.kaji/wf'` を対象にするため、新規 YAML は
  **git add 済み**でなければ検証対象に入らない（`Makefile:20-25`）
- 本 Issue では `apokamo/kaji-starter-python` に対する実同期・tag push・Release 公開を行わない
  （forward test は Issue #371）

## 変更スコープ

| 対象 | 変更 |
|------|------|
| `.kaji/wf/custom/operations/starter-sync.yaml` | 新規追加 |
| `docs/operations/release/starter-sync-runbook.md` | workflow 起動手順と `--before` / `--from` の停止・再開手順を追記 |
| `docs/dev/workflow_guide.md` | custom 一覧表と provider × workflow 対応表に 1 行追加 |
| `docs/dev/workflow-authoring.md` | ファイル配置ツリーに `custom/operations/starter-sync.yaml` を反映 |
| `kaji_harness/**` / `tests/**` | 変更なし |

## 方針

### 1. 遷移グラフ

3 step の線形遷移に、review → update の 1 本の RETRY edge を足すだけの最小構成にする。

```text
update-starter --PASS--> review-starter-update --PASS--> release-starter --PASS--> end
                    ^                 |
                    +-----RETRY-------+
```

`update-starter` は先頭 step（reachability の root）であり、`release-starter` まで
`on` 遷移で到達できる（`kaji_harness/workflow.py:600-624` の到達可能性検証）。

### 2. bounded retry の cycle 構造

```yaml
cycles:
  starter-review:
    entry: review-starter-update
    loop: [update-starter, review-starter-update]
    max_iterations: 3
    on_exhaust: ABORT
```

- RETRY を発行するのは `review-starter-update`。runner の increment 条件が
  `cycle.loop[-1]` に限定されているため、**`review-starter-update` を `loop` 末尾**に置く
- validator の「loop 末尾の `on.RETRY` は loop 先頭を指す」制約により、`loop` 先頭は
  `update-starter` になる。これは求める差し戻し先と一致する
- `entry` は cycle の membership を広げるだけの field（`kaji_harness/models.py:107-112`）。
  既存 `docs-codex.yaml` の `ready-review`（`entry: review-ready` / `loop: [fix-ready, review-ready]`）
  と同じく、review 側 step を `entry` に置く慣習に合わせる
- exhaust 後は cycle 所属 step の dispatch 直前に synthetic `ABORT` verdict が生成される
  （`kaji_harness/runner.py:1013-1027`）
- `PASS` が cycle 外（`release-starter`）へ抜けるため、cycle exit 検証を満たす

### 3. 別 session の独立 review

3 step とも `resume:` を書かない。`resume` の無い step は前段 agent の session を継承しないため、
`review-starter-update` は `update-starter` の思考文脈を持たない独立 session として起動する。
さらに agent 自体を分離（update = claude / review = codex）し、`dev.yaml` の
「producer は claude、reviewer は codex」という既存の分業に揃える。

RETRY 時のフィードバックは **tracking Issue コメント**を経由する。`review-starter-update` は
target / base / candidate と指摘を Issue へ投稿し、再実行される `update-starter` は同じ
tracking Issue を入力として読む。agent session を跨ぐ状態共有を Issue に一本化することで、
`resume` を使わずに収束させる。

### 4. publish 前の有人停止境界

publish は 2 層で守る。

1. **workflow 層**: 運用の既定コマンドを `--before release-starter` とする。`--before` は
   「次に dispatch する step ID が一致した瞬間に停止する exclusive barrier」であり、
   `release-starter` は dispatch されない
2. **skill 層**: `release-starter` 自身が ref push 前に workflow 外の人間の明示承認を要求する
   （[release-starter SKILL.md](../../.claude/skills/release-starter/SKILL.md) § Publish）

`resume:` を一切使わないため、[workflow-authoring.md](../../docs/dev/workflow-authoring.md)
§ `--before` が警告する「barrier を挟んだ resume の context 整合崩れ」は構造的に発生しない。

`release-starter` に RETRY edge は張らない。publish の部分成功再実行は release-plan が返す
不足分だけを処理する人間主導の操作であり、workflow が自動で再突入すると承認境界を
迂回するため、`--from release-starter` の再実行として扱う。

### 5. workdir を設定しない

step に `workdir` を設定せず、cwd を kaji project root のままにする。starter repository の
path は各 skill が tracking Issue（`starter_path`、既定は sibling `../<repo-name>`）から解決する。
starter repo を `workdir` にすると `.kaji/config.toml` 探索と `kaji issue` の repo 解決が
starter 側を向き、tracking Issue 操作が壊れる。

### 6. series 自動選択からの除外

`series-create` は `.kaji/wf/custom/**/*.yaml` の `description` も読んで member workflow を
自動選択する（[series-create SKILL.md](../../.claude/skills/series-create/SKILL.md) 手順 3）。
既存 custom variant と `incident.yaml` に倣い、`description` 先頭で
「series 自動選択対象外」「手動起動」を明示する。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| workflow の所有権と配置 | `.kaji/wf/custom/operations/starter-sync.yaml` | Issue #374 本文「重要判断」（人間決定）+ [workflow-authoring.md](../../docs/dev/workflow-authoring.md) § ファイル配置（`custom/operations/` は保守 workflow 用の予約カテゴリ、実ファイルが必要になるまで作らない） | 予約カテゴリの最初の実ファイルとして directory を作成。`name: starter-sync` を filename stem と一致させる |
| publish 前の承認境界 | `release-starter` の直前で停止し、workflow 外の人間承認後に再開 | Issue #374 本文「重要判断」（人間決定）+ [runbook](../../docs/operations/release/starter-sync-runbook.md) § Workflow 手順 4 + [release-starter SKILL.md](../../.claude/skills/release-starter/SKILL.md) § Publish | barrier を `--before release-starter` / 再開を `--from release-starter` に具体化。`release-starter` に RETRY edge を張らず、部分成功再実行も人間起動に寄せる |
| forward test の追跡先 | YAML 作成と静的検証は #374、実同期は #371 | Issue #374 本文「重要判断」（人間決定） | テスト戦略を静的検証に限定し、runtime 検証項目を本 Issue の完了条件へ持ち込まない |
| 独立 review の担保方法 | 3 step とも `resume` を持たせない | Issue #374 完了条件（人間決定）+ [review-starter-update SKILL.md](../../.claude/skills/review-starter-update/SKILL.md)「update と別 session で」 | `resume` 不使用に加え agent を claude / codex に分離。RETRY 時のフィードバック伝達路を tracking Issue コメントに固定 |
| bounded retry の cycle 構造 | `entry: review-starter-update` / `loop: [update-starter, review-starter-update]` | AI の詳細化（決定範囲内）。根拠は `kaji_harness/runner.py:1061-1062` の increment 条件と `kaji_harness/workflow.py:660-671` の loop tail 制約。検査先: `/issue-review-design` と `kaji validate` | RETRY 発行 step を loop 末尾に置く形へ具体化。`entry` は既存 `docs-codex.yaml` の慣習に合わせた |
| `max_iterations: 3` | 3 | AI の仮定。根拠: 既存 official / custom workflow の全 cycle が 3 で統一。検査先: `/issue-review-design`、#371 の forward test | cycle 定義へ反映 |
| agent / model / effort 割当 | update=claude/opus/high、review=codex/gpt-5.6-sol/high、release=claude/opus/high | AI の仮定（two-way door）。根拠: `dev.yaml` の producer=claude / reviewer=codex 分業、`incident.yaml` の高難度調査 step への opus 割当。検査先: `/issue-review-design`、#371 の forward test | publish step を reviewer agent から外し、review と publish の実行主体を分離 |
| `default_timeout: 3600` | 3600 秒 | AI の仮定。根拠: `incident.yaml` が長時間 step を理由に 3600 を採用済み。repo 既定 2400 では全件分類 + quality gate 実行が不足しうる。検査先: #371 の forward test | workflow レベルに設定し、step 個別 timeout は置かない |
| `workdir` を設定しない | 未設定（project root） | AI の詳細化。根拠: [update-starter SKILL.md](../../.claude/skills/update-starter/SKILL.md) § 入力 が starter path を tracking Issue から解決すると規定。検査先: #371 の forward test | starter repo を workdir にしない理由を設計書に明記 |
| series 自動選択対象外の宣言 | `description` 先頭に明示 | AI の詳細化。根拠: [series-create SKILL.md](../../.claude/skills/series-create/SKILL.md) 手順 3 が custom description も読む。検査先: `/issue-review-design` | 既存 custom variant / `incident.yaml` と同じ宣言文言を採用 |
| `requires_provider: github` | github | Issue #374 完了条件（人間決定） | YAML へ反映。`kaji run` 起動時の exit 2 fail-fast を得る |
| 恒久テストを追加しない | pytest は追加しない | [workflow-authoring.md](../../docs/dev/workflow-authoring.md) § 品質保証の責務境界（custom は pytest 対象外）+ Issue #374 完了条件（人間決定） | 静的検証で代替する根拠を「テスト戦略」に記載 |

one-way door の未決は無い。配置・承認境界・forward test 追跡先はいずれも Issue 本文で人間が
決定済みであり、参照正本（runbook / workflow-authoring.md / 3 skill）と矛盾しない。

## テスト戦略

### 変更タイプ

**metadata-only 相当**（宣言的 asset の追加 + docs 整合）。`kaji_harness/` に変更はなく、
新規の実行時ロジックを持たない。追加するのは既存 schema に従った workflow 宣言のみ。

### 変更固有検証

| 検証 | コマンド | 期待 |
|------|---------|------|
| L1/L2/L3 単体検証 | `kaji validate .kaji/wf/custom/operations/starter-sync.yaml` | exit 0、`✓ starter-sync.yaml` |
| tracked 一括検証 | `git add` 後に `make validate-workflows` | exit 0（`git ls-files` ベースのため add 済みが前提） |
| 既存回帰 + 品質ゲート | `make check` | exit 0。official inventory 系テストが custom を拾わないこと（glob 起点が `.kaji/wf/official/`）を含めて確認 |
| docs リンク整合 | `make verify-docs` | exit 0 |
| 運用手順の step ID 整合 | runbook / workflow_guide に記載した `--before release-starter` / `--from release-starter` の step ID が YAML の `id` と一致することを目視照合 | 一致 |

#### 設計時に実施済みの feasibility 検証

上記「使用例（workflow YAML の全文案）」と同一の cycle / 遷移構造を持つ candidate を
worktree 内の `.kaji/wf/custom/operations/starter-sync.yaml` に一時配置して
`kaji validate` を実行し、`✓ .kaji/wf/custom/operations/starter-sync.yaml` / exit 0 を確認した
（確認後にファイルと directory は削除済み。設計フェーズの commit 対象は `draft/design/` のみ）。
これにより、本設計の cycle 構造（`loop: [update-starter, review-starter-update]`）が
validator の loop tail 制約・cycle exit 制約・到達可能性検証・skill 解決（codex step からの
`.claude/skills/` 参照を含む）をすべて満たすことが実測で裏付けられている。

`kaji validate` が本設計で意図した不変条件を実際に検査することの根拠:

- cycle loop 末尾の `RETRY` 遷移先（`workflow.py:660-671`）
- cycle の exit 存在（`workflow.py:673-685`）
- 先頭 step からの到達可能性・全 step の `on.PASS`（`workflow.py:600-624`）
- skill の存在と `agent` 省略条件（`preflight.py:83-95`）
- `requires_provider` の enum 妥当性

### 恒久テストを追加しない理由（`docs/dev/testing-convention.md` の 4 条件）

1. **独自ロジックの追加・変更をほぼ含まない**: harness コードは無変更。YAML は既存 schema の宣言のみ
2. **想定不具合が既存ゲートで捕捉済み**: schema 違反・遷移先の参照破損・skill 未解決・agent 欠落・
   cycle 構造違反はすべて L1/L2/L3 が検出し、`make validate-workflows` が `make check` 経由で常時実行される
3. **新規テストで増える回帰検出情報がほとんど無い**: custom workflow を pytest で固定すると
   `workflow-authoring.md` の所有権境界（custom は kaji の pytest 回帰対象外）を破る一方、
   検出できる欠陥は静的検証と重複する
4. **理由をレビュー可能な形で説明できる**: 本節と Issue コメントに記録する

既存契約の維持（custom が official inventory に混入しないこと）の証跡:
`tests/workflows/test_self_retry_cycle_membership.py`（`OFFICIAL_DIR.rglob("*.yaml")`）と
`tests/test_recovery_workflow_inventory.py`（`.kaji/wf/official/**/*.yaml` を glob）はいずれも
official を glob 起点にしており、除外リスト方式ではない。したがって custom カテゴリを
1 つ増やしても pytest の対象集合は変化しない。なお本 workflow は self-RETRY step を持たない
（RETRY は `review-starter-update` → `update-starter` の他 step 遷移）ため、
self-RETRY cycle 所属の不変条件にも該当しない。

runtime の振る舞い（実 starter に対する 3 skill の forward test）は Issue #371 で検証する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/operations/release/starter-sync-runbook.md` | あり | workflow 起動コマンドと `--before release-starter` → 人間承認 → `--from release-starter` の停止・再開手順を追記（Issue 完了条件） |
| `docs/dev/workflow_guide.md` | あり | § custom 一覧表と § provider × workflow 対応表へ 1 行追加。`incident.yaml` と同じく「通常運用には含めない（手動起動）」の注記を付す |
| `docs/dev/workflow-authoring.md` | あり | § ファイル配置のツリー図に `custom/operations/starter-sync.yaml` を反映（「実ファイルが必要になるまで作成しない」という現行記述と実体を整合させる） |
| `docs/adr/` | なし | 新しい技術選定は無い。ADR 011 の overlay 実装は Issue スコープ外 |
| `docs/ARCHITECTURE.md` | なし | 構造・レイヤに変更なし |
| `docs/reference/configuration.md` | なし | config の key / 既定値に変更なし |
| `docs/cli-guides/` | なし | CLI の引数・終了コードに変更なし |
| `AGENTS.md` / `CLAUDE.md` | なし | `CLAUDE.md` の「Starter 追随（Release 後）」行は 3 skill の順序を示しており、workflow 追加後も正しい。skill 単体起動も有効な起動経路として残る |
| `.claude/skills/**` | なし | skill の入力・verdict・guardrail は変更しない。`release/SKILL.md` の `/update-starter` handoff 案内も引き続き有効 |
| `docs/guides/python-starter.md` / `.ja.md` | なし | runbook へのリンクのみで、手順本体を持たない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| ワークフロー定義マニュアル（配置・所有権） | `docs/dev/workflow-authoring.md` | 「`custom/operations/` は障害対応・保守・移行 workflow 用の予約カテゴリ。Git は空 directory を追跡できないため、実ファイルが必要になるまで作成しない」「`name:` は filename stem と一致させる」 |
| 同（品質保証の責務境界） | `docs/dev/workflow-authoring.md` | custom は pytest 対象外、tracked な custom YAML は `make validate-workflows` が L1/L2/L3 で静的検証する。「custom workflow の構文・参照破損は静的検証で検出できるが、振る舞いの回帰は kaji のテストスイートでは検出しない」 |
| 同（cycles / `--before` / `--from`） | `docs/dev/workflow-authoring.md` | 「`loop` 末尾ステップの `on.RETRY` は `loop` 先頭ステップを指すこと」「`--before` は次に dispatch しようとしている step ID が一致した瞬間に停止する exclusive barrier」 |
| cycle 検証実装 | `kaji_harness/workflow.py:660-671, 673-685` | loop 末尾の RETRY 遷移先が loop 先頭でなければ `WorkflowValidationError`。cycle 内の PASS が cycle 外へ抜けなければ「has no exit」エラー |
| 到達可能性検証 | `kaji_harness/workflow.py:600-624` | 先頭 step を root として `on` 遷移を辿り、未到達 step をエラーにする |
| cycle iteration の increment 条件 | `kaji_harness/runner.py:1061-1062` | `if cycle and current_step.id == cycle.loop[-1] and verdict.status == "RETRY"` のときだけ `increment_cycle` |
| cycle exhaust 時の挙動 | `kaji_harness/runner.py:1013-1027` | `state.cycle_iterations(cycle.name) >= cycle.max_iterations` で `cycle.on_exhaust` の synthetic verdict を生成し dispatch しない |
| cycle membership | `kaji_harness/models.py:107-112` | `find_cycle_for_step` は `step_id in cycle.loop or step_id == cycle.entry` で判定する |
| L3 preflight（agent 必須条件） | `kaji_harness/preflight.py:83-95` | skill が `exec_script` を宣言していない step で `agent` を省略するとエラー |
| skill 解決パス | `kaji_harness/skill.py:49` | `base = workdir / skill_dir / skill_name / "SKILL.md"`。agent に依存しないため codex からも `.claude/skills/` を解決できる |
| `make validate-workflows` の対象 | `Makefile:20-25` | `git ls-files -- '.kaji/wf' \| grep '\.yaml$'` を `kaji validate` に渡す（untracked は対象外） |
| starter sync runbook | `docs/operations/release/starter-sync-runbook.md` | § Workflow 手順 2〜4: candidate は review 前に push しない、別 session で独立 review、ref push は「workflow 外の人間承認後に annotated tag と main を atomic push」 |
| update-starter skill | `.claude/skills/update-starter/SKILL.md` | verdict は `PASS \| ABORT`。starter path は tracking Issue から解決。「次は update と別 session で `/review-starter-update` を実行する」 |
| review-starter-update skill | `.claude/skills/review-starter-update/SKILL.md` | verdict は `PASS \| RETRY \| ABORT`。「`/update-starter` 再実行後は candidate が変わるため再 review」 |
| release-starter skill | `.claude/skills/release-starter/SKILL.md` | verdict は `PASS \| ABORT`。「workflow 外で**人間の明示承認**を得てから annotated tag と main を `git push --atomic`」 |
| series 自動選択の入力 | `.claude/skills/series-create/SKILL.md` 手順 3 | `.kaji/wf/official/**` と `.kaji/wf/custom/**` の `description` を読み、標準 series auto-selection 対象かを description の記述で判定する |
| cycle 記述の既存前例 | `.kaji/wf/custom/docs/docs-codex.yaml:10-15` | `ready-review: entry: review-ready / loop: [fix-ready, review-ready]`（review step を entry かつ loop 末尾に置く形） |
| official inventory テストの glob 起点 | `tests/workflows/test_self_retry_cycle_membership.py` | `OFFICIAL_DIR = REPO_ROOT / ".kaji" / "wf" / "official"` を rglob。「custom workflow は利用者所有のため対象外とする」 |
| テスト規約 | `docs/dev/testing-convention.md` | docs-only / metadata-only / packaging-only は 4 条件を満たせば恒久回帰テスト不要 |
