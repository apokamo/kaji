# EPIC オーケストレーション 検討資料

> **ステータス**: 検討中（設計 / RFC ではない）
> **更新日**: 2026-05-01
> **発端**: kamo2 リポジトリの EPIC Issue（例: kamo2#1080）の子イシュー群を kaji で全自動連続実行したい、という要望
> **位置づけ**: 方針合意前の議論メモ。決定事項は含まない。RFC / ADR / 設計書への昇格は未定

## 0. 目次

1. 背景
2. 現状の kaji の制約（事実ベース）
3. 課題一覧
4. 改善案の方向性
5. 新設スキル案
6. ワークフロー拡張案
7. EPIC runner 素描
8. 未決事項・反対意見
9. 次のステップ候補
10. 実装計画（イシュー化の計画）

---

## 1. 背景

kamo2 リポジトリの EPIC Issue は、複数子イシュー（規模 S〜M）+ 依存グラフ + マイルストーンを持つ構造で起票されている。例:

- 親: kamo2#1080「[EPIC] アクティビスト通知機能 Phase 3 実装」
- 子: A（#1081）〜 G（#1087）の 7 件
- 依存: A→B / A→G / A→E / B→E / D→E / C→F / E→F（C と D は並列可）
- 親 ADR: ADR-017

これを **kaji で全自動連続実行**したい。子イシューごとに `kaji run feature-development.yaml <n>` を順次叩く方式では、依存解決・並列・post-merge レビュー対応・失敗伝播が手作業になり、現実には半自動以下になる。

## 2. 現状の kaji の制約（事実ベース）

コード確認結果:

- `kaji_harness/cli_main.py` の `kaji run` は `issue: int` を 1 つだけ受け取る（単一 Issue 起点）。
- `workflows/feature-development.yaml` は `design → review-design → … → i-pr` 終端。merge 待ち・post-merge レビュー・close を含まない。
- `issue-create` / `issue-start` / `issue-close` は **ワークフロー外**（README / skill 上、事前手動実行が前提）。
- `kaji_harness/runner.py` は while ループで逐次実行（並列実行なし）。
- `SessionState` は Issue scoped（`runner.py:65` 参照）。EPIC 全体の状態を束ねる構造はない。
- cycle は `max_iterations: 3`、`on_exhaust: ABORT`。人手エスカレーション (`PAUSE`) のフックはない。
- `--from <step>` は単一 Issue 内の途中再開のみ。

kamo2 側の運用前提:

- merge 後に codex で post-merge review を実施し、指摘があれば `pr-fix` → `pr-verify` → close、というサイクルが運用上ある。kaji の現行ワークフローはここをカバーしていない。
- kamo2 のスキル `pr-fix` / `pr-verify` は merge 前 PR への修正を想定した語彙で、post-merge fix（main に取り込まれた後の追加修正）は明示的にサポートされていない。

## 3. 課題一覧

### 3.1 オーケストレーション層

1. **単一 Issue 前提**: `kaji run` が `issue: int` を 1 つしか受け取らず、EPIC の DAG を投入できない。
2. **依存グラフ非対応**: A→B 等の依存を表現する仕組みがない。
3. **並列実行非対応**: C/D は並列可能だが、runner は逐次のみ。
4. **クロス Issue 状態管理の欠如**: SessionState が Issue scoped。EPIC 全体の進捗・失敗伝播を束ねる構造がない。
5. **クロス Issue resume 不可**: `--from <step>` は単一 Issue 内のみ。「A は完了、B から再開」を表現できない。
6. **EPIC 単位のコスト・予算管理なし**: `max_budget_usd` は step 単位のみ。

### 3.2 ワークフロー定義の不足

7. **issue-start がワークフロー外**: 各子の worktree 作成を kaji が行わない。
8. **issue-close がワークフロー外**: PR 完了後の worktree 削除・ブランチ削除も同様。
9. **PR merge 待機がない**: `feature-development.yaml` は `i-pr` で終端。
10. **main CI green の確認がない**: merge 後の main CI 失敗を検知できない。
11. **post-merge codex review がない**: 実運用の `merge → codex review → pr-fix → pr-verify → close` がワークフローに組み込まれていない。
12. **post-merge fix の skill 不整合**: `pr-fix` は元 PR 前提の語彙。merge 済への追加 push は不可で、新ブランチ・新 PR が必要だが skill 側が未対応。
13. **PR をまたぐ再帰サイクル非対応**: 追加 PR にも post-merge review がかかる場合の繰返し上限を表現できない。

### 3.3 依存ゲートの設計

14. **「Issue 完了」の定義が曖昧**: PR merge ≠ レビュー収束。下流を開く基準を `REVIEW_SETTLED`（post-merge fix 収束済）に置く必要がある。
15. **merge の直列化が必要**: C/D を並列開発しても、main への merge は順序付けないと post-merge review の commit range が紛らわしい。
16. **依存前提の preflight 検証なし**: B 着手時に「A の migration が main に取込済」「seed 準備物が存在」を検証する仕組みがない。

### 3.4 失敗時のハンドリング

17. **cycle 枯渇 = ABORT 直行**: 人手エスカレーション (`PAUSE`) のフックがない。
18. **失敗伝播の自動化なし**: A が ABORT したとき B/E/G を自動でスキップ／停止する仕組みがない。
19. **CI 失敗時の判断ルートなし**: merge 後 main CI が落ちたときの revert / hotfix 分岐を表現できない。
20. **通知機構なし**: 長時間ジョブで PAUSE / ABORT / cycle 枯渇を人手に知らせる経路がない（ntfy 等）。

### 3.5 環境・運用前提

21. **secrets / API キー前提**: kamo2 側の例だと E/F は実 I/O、L テストは API キー必須。preflight で fail-fast できる仕組みが要る。
22. **DB / staging 到達性**: `make gate-backend` 等が DB 必須。実行ホストの環境前提を明示する手段がない。
23. **長時間実行の信頼性**: 一気通貫で数時間〜十時間規模。CLI 側のレート制限・容量制限・タイムアウトに対する耐性が単発 retry のみ。
24. **PR レビュー運用との整合**: 自動連続を成立させるには auto-merge + CI 必須グリーンが前提。利用側の運用ポリシーとの整合は別途要検討。

## 4. 改善案の方向性

完全自動化を一気に目指さず、**半自動（人手介入は PR レビュー承認と PAUSE 解除のみ）**を最初の到達点とする方針が現実的。

優先度の素案:

| 優先度 | 改修 | 対応する課題 |
|---|---|---|
| 高 | EPIC runner（依存 DAG・並列・resume・状態集約） | 1, 2, 3, 4, 5 |
| 高 | `wait-merge` + `verify-main-green` + `post-merge-review` フェーズ | 9, 10, 11, 19 |
| 高 | 依存ゲートを `REVIEW_SETTLED` に固定 + merge 直列化キュー | 14, 15 |
| 中 | issue-start / issue-close を pre/post に組み込み | 7, 8 |
| 中 | cycle 枯渇時の PAUSE + 通知 | 17, 18, 20 |
| 中 | `pr-fix` の post-merge モード + 再帰上限 | 12, 13 |
| 中 | preflight skill（依存前提検証） | 16 |
| 低 | EPIC 単位の予算・累計レポート | 6 |
| 低 | 環境前提の preflight（secrets/DB/CI 設定） | 21, 22, 23 |
| 運用 | auto-merge ポリシー整理 | 24 |

## 5. 新設スキル案

### 5.1 EPIC 起動準備フェーズの新スキル

実施方針レビュー / YAML 整合レビューの 2 系統。verify は省略（後述「未決事項」で議論）。

| スキル | 責務 | agent |
|---|---|---|
| `i-epic-policy-review` | 子 Issue 群の方針整合をレビュー（指摘のみ） | codex |
| `i-epic-policy-fix` | 子 Issue 本文を `gh issue edit` で修正 | claude |
| `i-epic-yaml-generate` | EPIC + 子 Issue 構造から EPIC YAML を生成 | claude |
| `i-epic-yaml-review` | 生成 YAML が EPIC 設計と整合するかレビュー（指摘のみ） | codex |
| `i-epic-yaml-fix` | YAML を修正（人手 PAUSE で代替可、まずは無効化も検討） | claude |

#### 5.1.1 `i-epic-policy-review` の観点

「設計レベル」ではなく「**実施方針の整合**」に絞る。

1. 役割分担の MECE 性（VIEW 定義は A、参照は E、等）
2. 依存記述の対称性（上流の「下流影響」と下流の「依存先」が呼応）
3. 成果物の境界（出力カラム最低限、seed の出所、等）
4. 受入条件の整合（上流の受入が下流の前提を満たすか）
5. 規模・優先度の妥当性（S/M 表記と実態の乖離）
6. マイルストーン順序（依存グラフと矛盾しないか）
7. EPIC 固有の運用判断（auto-merge 範囲、PAUSE 通知先、staging 空運転責任など）

ADR の Decision を子 Issue 群に割当する表を出力に含めると、観点 1, 2 が機械的に拾える。

#### 5.1.2 `i-epic-yaml-generate` の動作

決定的処理と判断的処理を分離するハイブリッド設計を推奨。

- **決定的処理（Python スクリプト）**: 子 Issue 番号抽出、依存表 → `depends_on` 変換、並列ノード判定、マイルストーン → merge キュー順序付け
- **判断的処理（LLM）**: ワークフロー選択（feature-development / docs-maintenance）、`max_iterations` 調整、preflight 引数推論、post-merge フェーズ組込、cycle 枯渇時通知先

冪等性: 既存 YAML がある場合はマージ更新（手書きセクションを消さない）。生成根拠コメントを YAML 冒頭に埋め、再生成時に必ず再書き込み。

#### 5.1.3 `i-epic-yaml-review` の観点

policy-review と直交。

1. 網羅性（子 Issue 全件が含まれるか）
2. 依存グラフ一致（YAML の `depends_on` が EPIC 本文の Mermaid graph と一致するか）
3. 並列指定の妥当性
4. ワークフロー選択の妥当性
5. マージ直列化がマイルストーンと整合するか
6. post-merge フェーズの組込
7. preflight の組込
8. 依存ゲートが `REVIEW_SETTLED` か
9. cycle 上限と PAUSE 経路
10. secrets / 環境前提の preflight
11. EPIC 単位の予算・タイムアウト
12. EPIC 本文と YAML の双方向整合（YAML の運用判断が EPIC 本文に反映されているか）

### 5.2 ワークフロー内に追加するスキル

post-merge フェーズで使用。

| スキル | 責務 | agent |
|---|---|---|
| `wait-merge` | PR の merge を gh API でポーリング | claude or 軽量 runner |
| `verify-main-green` | merge 後 main CI の green を確認 | claude |
| `post-merge-review` | merged commit を codex でレビュー | codex |

`pr-fix` は post-merge モードを追加（または `post-merge-fix` を別 skill 化）。`pr-verify` は再利用可能性を要検討（merge 済 PR 構造が違う）。

## 6. ワークフロー拡張案

### 6.1 改訂後の単一 Issue ワークフロー

```
issue-start (pre, EPIC runner が呼ぶ)
  → design → review-design → [fix-design ↔ verify-design]
  → implement → review-code → [fix-code ↔ verify-code]
  → i-dev-final-check → i-pr
  → wait-merge                    # 新規
  → verify-main-green             # 新規
  → post-merge-review             # 新規
      ├─ PASS → issue-close → 子 Issue 完了状態 = REVIEW_SETTLED
      └─ RETRY → pr-fix (新ブランチ) → 新規 PR → wait-merge → verify-main-green → post-merge-review (再帰、上限あり)
```

`feature-development.yaml` を上記まで拡張するか、新規ワークフロー（例: `feature-with-postmerge.yaml`）として分けるかは要検討。

### 6.2 EPIC ワークフロー（上位）

```
[EPIC start]
  → i-epic-policy-review (← fix)            # Issue 群の方針整合
  → i-epic-yaml-generate                    # YAML 生成
  → i-epic-yaml-review (← fix)              # YAML 意図整合
  → EPIC runner 起動
      ├─ 各子 Issue を依存グラフに従って実行
      ├─ 並列可能なグループは asyncio で同時起動
      ├─ merge は直列化キューで順序付け
      └─ 子の状態が REVIEW_SETTLED になったら下流ゲートを開く
```

## 7. EPIC runner 素描

### 7.1 入力

- EPIC Issue 番号（または EPIC YAML パス）
- 子 Issue 群と依存グラフ（YAML から読込）
- `--resume-from <child>` / `--skip-completed` オプション

### 7.2 状態モデル

- 子 Issue ごとの状態: `PENDING` / `IN_PROGRESS` / `MERGED` / `REVIEW_SETTLED` / `ABORT` / `PAUSED`
- 下流ゲート開放条件: 上流すべてが `REVIEW_SETTLED`
- 状態は EPIC scoped に保存: `.kaji/artifacts/_epics/<epic_n>/state.json`

### 7.3 並列実行

- asyncio もしくはサブプロセス。依存制約を満たした子 Issue を同時起動。
- CLI（claude/codex）側のレート制限・課金枠の競合に注意。並列度上限を設定可能にする。

### 7.4 失敗ハンドリング

- 子の `ABORT`: 下流をスキップ（依存先すべての子を `BLOCKED` にマーク）。
- 子の `PAUSE`: EPIC runner も待機。ntfy 通知。手動 resume 後に継続。
- merge 後 main CI 失敗: `PAUSE`。revert / hotfix の判断は人手。

### 7.5 ブートストラップ問題

EPIC runner 自体の開発に EPIC runner は使えない。最初の数 Issue は手動で従来 `feature-development.yaml` を回す前提を明記する必要がある。

## 8. 未決事項・反対意見

### 8.1 verify を省略してよいか

policy / yaml レビューで verify を省略する案。理由は「Issue 本文や YAML は diff で人手確認できる」「review 再回しで収束を保証できる」。

- **賛成側**: コード修正と異なり副作用範囲が閉じている。
- **懸念**: review が「修正の妥当性確認」を兼ねないと、毎回新規指摘が混入してループが収束しない。**review の責務に「修正が指摘に対応しているか」を含めない**ルールを skill に明記する必要がある。

### 8.2 `i-epic-yaml-fix` を skill 化するか

- 自動連続を優先するなら skill 化。
- ただし YAML 不整合は EPIC 起票直後に集中するので、最初は人手 PAUSE 代替で十分という判断もあり得る。

### 8.3 設計整合レビュー（cross-design-review）は必要か

検討当初に `i-epic-cross-design-review`（子 design.md の横断レビュー）案も挙がったが、**実施方針レビュー（policy-review）でカバーできる範囲は policy で見て、設計詳細は個別 `issue-review-design` に任せる**方針に倒した。実運用で漏れが見つかれば再検討。

### 8.4 `feature-development.yaml` の拡張 vs 新規ワークフロー

post-merge フェーズを既存 YAML に足すと既存ユーザーが影響を受ける。新規 YAML として分けるか、フラグで切替可能にするか要検討。

### 8.5 EPIC runner と既存 single-issue runner の関係

- 上位 runner が下位を呼ぶ構造。下位を変更せず上位を新規追加する形が望ましい。
- ただし `wait-merge` / `verify-main-green` / `post-merge-review` は単一 Issue 単位で必要なので、ワークフロー定義側の拡張は単一 Issue runner 内で完結する。

### 8.6 merge 直列化のキュー実装

- gh API での merge 実行を EPIC runner が直列に発行する想定。
- ただし auto-merge を使う場合、merge タイミングは GitHub 側に委ねられる。EPIC runner 側で順序を強制するには「上流が REVIEW_SETTLED になるまで下流の PR を作らない」アプローチが現実的。

## 9. 次のステップ候補

1. この検討資料を kaji チーム（メンテナ）と共有し、方向性合意の有無を判断。
2. 方向性合意なら、`docs/rfc/epic-orchestration.md` に昇格して RFC レビュー。
3. RFC 採択後、EPIC Issue + 子 Issue 群を起票（5〜7 件想定）。
4. ADR（kaji の `docs/adr/004-*.md`）採択。
5. 子 Issue ごとに既存 `feature-development.yaml` で実装（ブートストラップ期）。
6. EPIC runner が動き出した段階で、kaji 自身の今後の EPIC を EPIC runner で回す（ドッグフーディング）。

---

## 10. 実装計画（イシュー化の計画）

### 10.1 方針

- 本設計ドキュメントは実装完了まで非公開（ローカル保持）。RFC / ADR は全イシュー完了後にまとめてマージ。
- 各 PR のスコープを絞り、単体で意味が成立するが全体像は読み取れない粒度に保つ。
- ブートストラップ制約: 全イシューを既存 `feature-development.yaml` で実装（EPIC runner が動く前）。

### 10.2 フェーズ構成

```
Phase 1  単一 Issue ワークフロー拡張 + EPIC YAML スキーマ ← まず着手
Phase 2  EPIC runner コア（逐次・状態管理）
Phase 3  EPIC runner 並列・失敗制御
Phase 4  EPIC 準備スキル
Phase 5  周辺整備
Phase 6  RFC / ADR 公開 + ドッグフーディング
```

### 10.3 イシュー一覧

#### Phase 1 — 単一 Issue ワークフロー拡張 + EPIC YAML スキーマ

| # | タイトル（案） | 対応課題 | 規模 |
|---|---|---|---|
| P1-1 | post-merge スキル群（`wait-merge` / `verify-main-green` / `post-merge-review`）+ `feature-with-postmerge.yaml` + EPIC YAML スキーマ定義 | 1, 2, 9, 10, 11 | M（~1.5日） |

**P1-1 スコープ:**
- `wait-merge`: gh API で PR の merge 状態をポーリング（interval 設定可能）
- `verify-main-green`: merge 後 main の CI run を gh API で確認（green / failure 判定）
- `post-merge-review`: merged commit range を codex でレビュー（PASS / RETRY を返す）
- 新規ワークフロー `feature-with-postmerge.yaml` を追加（既存 `feature-development.yaml` は変更しない）
- PASS 時: issue-close を呼び出し完了、RETRY 時: 次フェーズ（P5-1）の stub で PAUSE
- `EpicConfig` Pydantic モデル（子 Issue リスト、`depends_on`、並列グループ、merge キュー順序）
- YAML バリデーション（`kaji validate-epic`）
- ユニットテスト（DAG 循環検出、並列グループ推定）

**公開情報の制御:**
post-merge 品質担保と「複数 Issue を束ねる設定フォーマット」が同時に出るため、EPIC orchestration の意図はある程度読み取られる。許容判断。

---

#### Phase 2 — EPIC runner コア（逐次・状態管理）

| # | タイトル（案） | 対応課題 | 規模 |
|---|---|---|---|
| P2-1 | EPIC runner コア: DAG 解決・逐次実行・状態永続化 | 1, 4, 5 | M |

**P2-1 スコープ:**
- `kaji run-epic <epic.yaml>` CLI コマンド追加
- DAG 解決（トポロジカルソート）・逐次起動（依存解決済みから順次）
- 状態モデル: `PENDING / IN_PROGRESS / MERGED / REVIEW_SETTLED / ABORT / PAUSED`
- 状態永続化: `.kaji/artifacts/_epics/<epic_n>/state.json`
- `--resume-from <child>` / `--skip-completed` オプション
- 統合テスト: stub ワークフローで DAG 順序を検証

---

#### Phase 3 — EPIC runner 並列・失敗制御

| # | タイトル（案） | 対応課題 | 規模 |
|---|---|---|---|
| P3-1 | EPIC runner 並列実行（asyncio）と並列度制限 | 3 | M |
| P3-2 | 失敗ハンドリング: PAUSE + 失敗伝播（BLOCKED） | 17, 18, 19 | S〜M |

**P3-1 スコープ:**
- asyncio で依存制約を満たした子 Issue を同時起動
- `max_parallelism` 設定（レート制限・コスト競合の安全弁）

**P3-2 スコープ:**
- cycle 枯渇 → `PAUSE`（ABORT 直行を廃止）
- 子の `ABORT` → 下流を `BLOCKED` にマーク・skip
- merge 後 main CI 失敗 → `PAUSE`（revert / hotfix は人手判断）

---

#### Phase 4 — EPIC 準備スキル

| # | タイトル（案） | 対応課題 | 規模 |
|---|---|---|---|
| P4-1 | `i-epic-yaml-generate` スキル | — | M |
| P4-2 | `i-epic-policy-review` / `i-epic-yaml-review` スキル | — | S |

**P4-1 スコープ:**
- 決定的処理（Python）: 子 Issue 番号抽出・`depends_on` 変換・並列ノード判定・merge キュー順序付け
- 判断的処理（LLM）: ワークフロー選択・`max_iterations` 調整・preflight 引数推論
- 冪等性: 既存 YAML がある場合はマージ更新

**P4-2 スコープ:**
- `i-epic-policy-review`: 実施方針整合の 7 観点レビュー（セクション 5.1.1 参照）
- `i-epic-yaml-review`: YAML 意図整合の 12 観点レビュー（セクション 5.1.3 参照）

---

#### Phase 5 — 周辺整備

| # | タイトル（案） | 対応課題 | 規模 |
|---|---|---|---|
| P5-1 | post-merge fix 対応（`post-merge-fix` スキル新設） | 12, 13 | S〜M |
| P5-2 | issue-start / issue-close のワークフロー組み込み | 7, 8 | S |
| P5-3 | preflight スキル（依存前提検証） | 16 | S |

**P5-1 スコープ:**
- `post-merge-fix`: 新ブランチ作成 → fix → 新 PR 作成（merge 済 PR への push は不可のため）
- `feature-with-postmerge.yaml` の RETRY ルートに接続
- 再帰上限（`max_postmerge_cycles`）をワークフロー YAML で設定可能に

---

#### Phase 6 — RFC / ADR 公開 + ドッグフーディング

| # | タイトル（案） | 規模 |
|---|---|---|
| P6-1 | `docs/rfc/epic-orchestration.md` 昇格 + ADR 採択 | S |
| P6-2 | kaji 自身の次 EPIC を EPIC runner で回す（ドッグフーディング） | 運用 |

### 10.4 依存グラフ

```
P1-1 (post-merge スキル + EPIC YAML スキーマ)
  └─→ P2-1 (EPIC runner コア)
        ├─→ P3-1 (並列実行)
        │     └─→ P3-2 (失敗制御)
        └─→ P4-1 (yaml-generate)
              └─→ P4-2 (policy/yaml-review)

P5-1 (post-merge-fix)    ← P1-1 完了後に着手可
P5-2 (issue-start/close) ← 独立、任意のタイミング
P5-3 (preflight)         ← 独立、任意のタイミング

全 Phase 完了 → P6-1 (RFC/ADR公開)
```

### 10.5 実装順序（推奨）

1. **P1-1**（最初の着手）
2. **P2-1**（P1-1 完了後）
3. **P3-1** → **P3-2**（順次）
4. **P4-1** → **P4-2**、**P5-1**（並行可）
5. **P5-2**、**P5-3**（任意のタイミング）
6. **P6-1**（全実装完了後に RFC/ADR 公開）

---

## 付録 A: 想定ユースケース（kamo2#1080）

参考用。本検討の発端となった具体ケース。

- 親: kamo2#1080
- 子: A (#1081) DDL + VIEW、B (#1082) seed、C (#1083) EDINET 抽出改修、D (#1084) TDnet 統合、E (#1085) 通知 worker、F (#1086) scheduler、G (#1087) 四半期レビュー運用
- 依存: A→B, A→G, A→E, B→E, D→E, C→F, E→F
- 並列可能: C と D
- 統合点: E

このケースで EPIC runner があれば、人手介入は「PR レビュー承認」と必要なら「PAUSE 解除」だけになる想定。

## 付録 B: 検討の経緯

- 発端: kamo2#1080 の子イシュー連続自動実行をやりたい。
- 当初の課題抽出: 24 件。
- 初期スキル案: policy-review / yaml-review + verify。
- 議論を経て: verify は省略、yaml-generate を追加、設計レベルではなく「実施方針レベル」に解像度を下げる、で着地。
- 本ドキュメントは検討段階の整理であり、決定事項ではない。
