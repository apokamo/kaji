# [設計] GitHub ラベル刷新（RFC 改訂 + type:* への一括移行 + labels.yml/sync workflow 導入）

Issue: #154

## 概要

`docs/rfc/github-labels-standardization.md` の方針を「共存無期限」から「一括刷新」へ反転改訂し、Conventional Commits 準拠の `type:*` 11 ラベル + 直交 meta 8 ラベル（合計 19）を `.github/labels.yml` で宣言的に管理し、GitHub Actions（`labels-sync.yml`）で同期する基盤を導入する。併せて旧ラベル 5 個を削除し、OPEN Issue を新ラベルへ一括再マッピングする。

## 背景・目的

### 背景（一次情報）

- RFC `docs/rfc/github-labels-standardization.md` は採用済みだが、共存無期限方針が運用実態と乖離（#141, #147, #148, #149 で旧/新の二重付与が観測された）
- `/issue-create` スキルは `type:*` ラベル前提だが repo 側未整備（#153 起票時に `type:feature` not found で failed）
- ラベル定義が version control 外 → 変更履歴・色・description が追跡不能、複製も手動

### ユースケース（bootstrap exception 含む）

- **[Maintainer]** として、`/issue-create` が要求する `type:*` 体系を repo 側に確立し、スキル実行を成功させたい
- **[Maintainer]** として、`.github/labels.yml` 編集 → push → 自動 sync の宣言的フローでラベル運用を回したい
- **[Maintainer]** として、リリース系・breaking change を独立追跡し、release-please / Dependabot の将来導入時に再調整不要な状態にしたい
- **[Contributor]** として、ラベル名から分類意図が即座に読み取れ、検索が単一クエリで完結する状態が欲しい

### bootstrap 例外

本 Issue は `type:*` 体系そのものを新設する Issue のため、Issue 自身に付与する `type:*` ラベルが存在しない。Issue 本文末尾の bootstrap 宣言で例外として扱い、完了時に `type:chore` を Issue に切り替える（self-validation）。

## インターフェース

### 入力

#### `.github/labels.yml`（新規）

YAML スキーマ:

```yaml
labels:
  - name: <string, required, unique>      # 例: "type:feature"
    color: <hex string, required>          # 6 文字 hex（# プレフィックスなし）
    description: <string, required>        # 日本語可
```

合計 19 ラベル定義（type 11 + meta 8）。配色は Issue 本文の Catppuccin Mocha パレット表に従う。

#### `.github/workflows/labels-sync.yml`（新規）

トリガー:

- `workflow_dispatch`（手動、`dry_run: true|false` 入力）
- `push` to `main`（`paths: .github/labels.yml`）
- `schedule`（`cron: '0 0 * * 1'` ＝ JST 月曜 9:00、drift 検知）

権限: `issues: write` / `contents: read`。`GITHUB_TOKEN` は built-in。

### 出力

| 出力対象 | 内容 |
|---------|------|
| GitHub repo のラベル | type 11 + meta 8 = **19 ラベルが存在**。旧 5 ラベル（`enhancement`, `bug`, `documentation`, `refactoring`, `e2e-test`）は削除 |
| OPEN Issue のラベル | 旧ラベル → 新ラベルに再マッピング（手動、9 件以下） |
| `labels-backup.json` | 削除前の既存ラベルを `gh label list --json` で取得し、PR に添付 |
| Workflow 実行ログ | `created / updated / unchanged` のサマリーを `$GITHUB_STEP_SUMMARY` に出力 |
| docs | RFC 改訂、`docs/dev/labels.md` 新規、`docs/README.md` / `CLAUDE.md` 追記 |

### 使用例

#### Maintainer が新ラベルを追加する場合

```bash
# 1. labels.yml を編集
$EDITOR .github/labels.yml

# 2. ローカルで YAML 妥当性確認（pytest small）
pytest -m small tests/test_labels_yml.py

# 3. push → workflow が自動実行
git add .github/labels.yml && git commit -m "chore: add type:* label"
git push
```

#### 緊急時の手動 sync

```bash
gh workflow run labels-sync.yml -f dry_run=false
```

### エラー

| 失敗ケース | 挙動 |
|----------|------|
| `labels.yml` の YAML パース失敗 | workflow Step 4 (validate) で `process.exit(1)` |
| 色コードが 6 文字 hex でない | validate で `Invalid color for label '<name>'` |
| ラベル名重複 | validate で `Duplicate label name: '<name>'` |
| 必須フィールド欠落 | validate で `Label '<name>' missing <field>` |
| GitHub API エラー | `octokit` 例外 → `process.exit(1)` |
| `gh label delete` 失敗（手動） | 手作業で再試行。完了条件チェックで再確認 |

## 制約・前提条件

### 技術的制約

- `.github/` ディレクトリは本 Issue で初めて作成される（既存なし）
- workflow は `actions/checkout@v4` / `actions/setup-node@v4` (`node 24`) / `js-yaml` / `@octokit/rest` に依存
- `GITHUB_TOKEN` の `issues: write` 権限はラベル CRUD に十分（PAT 不要）
- workflow は **追加・更新のみ**。削除は workflow 範囲外（後段の手作業で実施）

### スコープ境界（明示）

**含む:**

- RFC 改訂（方針反転、Conventional Commits 完全準拠拡張、`type:release` / `breaking-change` / `dependencies` 追加根拠、bot 所有ラベル分離方針）
- `.github/labels.yml` 配置（19 ラベル）
- `.github/workflows/labels-sync.yml` 配置（kamo2 同型、`validate-label-usage` ジョブは除外）
- `docs/dev/labels.md` 新規作成
- `docs/README.md` / `CLAUDE.md` のインデックス追記
- 旧 5 ラベルの backup 取得 → 削除
- OPEN Issue の一括再ラベル（9 件以下、手動）
- pytest small（YAML 妥当性検証 1 ケース）

**含まない:**

- CLOSED Issue の再ラベル
- Dependabot 導入（`dependencies` ラベル先行作成のみ）
- kamo2 のフル 66 ラベル体系（`priority:*`, `status:*`, `area:*` 等）
- `validate-label-usage` ジョブ（PR テンプレートで誘導）
- PR への一括再ラベル
- `type:style` 採用
- bot 所有ラベル（`autorelease:*`, `release-please:*`）の管理 — release-please 導入時に bot が自動生成

### 配色制約

Catppuccin Mocha パレット（hex のみ。MIT ライセンス、attribution 不要）。Issue 本文の配色表を正本とする。

## 方針（Minimal How）

### 1. labels.yml 設計

kamo2 の `labels.yml` 構造（`labels:` 配列）を踏襲。kamo2 の `automation:` / `metrics:` セクションは kaji スコープ外のため取り込まない。

```yaml
labels:
  # type:* 11 個
  - name: "type:feature"
    color: "a6e3a1"
    description: "新機能の追加"
  # ... (Issue 本文の配色表に従う)

  # meta 8 個
  - name: "breaking-change"
    color: "f2cdcd"
    description: "破壊的変更を含む（SemVer major bump 対象）"
  # ...
```

### 2. labels-sync.yml 設計

kamo2 の workflow を以下の **削減** で取り込む:

- `validate-label-usage` ジョブを **削除**（PR テンプレートで代替）
- `Notify on Failure` の Issue #13 ハードコード参照を **削除**（kaji には該当 Issue 無し）
- それ以外（validate / backup / sync / summary）は同型を維持

### 3. docs/dev/labels.md（新規）

最低限カバーする内容:

| セクション | 内容 |
|----------|------|
| ラベル一覧 | type / meta それぞれの意図と Conventional Commits 対応 |
| 追加・変更手順 | labels.yml 編集 → push → workflow 自動実行 |
| 緊急時の手動 sync | `gh workflow run labels-sync.yml -f dry_run=false` |
| Catppuccin Mocha 採用方針 | パレット選定理由・色衝突回避ポリシー |
| bot 所有ラベルとの境界 | `autorelease:*`, `release-please:*` は labels.yml 管理対象外 |
| Dependabot 導入時の設定 | `labels: ["dependencies", "type:chore"]` の併用 |
| 複数 `type:*` 付与ポリシー | 許可するが主たる 1 つを推奨 |
| `type:security` 運用 | 公開済 CVE のみ。embargo 中は public Issue/PR に付けない |
| cron drift 運用 | 週次 cron による自動再同期、手動編集の drift 検知 |

### 4. RFC 改訂

`docs/rfc/github-labels-standardization.md` を以下の構造に書き換える:

- ステータス: 採用済み → **実装済み**
- 背景: 共存方針が運用実態で破綻した観測事実（#141, #147, #148, #149, #153）
- 提案: type:* 11 個（feat, fix, refactor, docs, test, chore, perf, security, release, build, ci）+ meta 8 個（breaking-change, dependencies, good first issue, help wanted, question, duplicate, invalid, wontfix）
- 移行マッピング表（旧 5 → 新）
- bot 所有ラベル分離方針
- `.github/labels.yml` / `.github/workflows/labels-sync.yml` への相互参照

### 5. 移行手順（実装フェーズで実施）

```
1. labels-backup.json を取得 (gh label list --json)
2. labels.yml + labels-sync.yml を main にマージ
3. workflow を dry_run=true で実行 → 19 ラベル追加予定を確認
4. workflow を dry_run=false で実行 → 19 ラベルが repo に存在
5. OPEN Issue 一覧をコメント → 旧→新マッピングに従って手動 re-label
   - enhancement → type:feature (#156, #158) / type:chore (#154 self) / type:feature (#153)
   - bug → type:bug (#161, #137)
   - documentation → type:docs (#119, #78)
6. 旧 5 ラベル削除 (gh label delete)
7. gh label list で完全一致確認
8. 本 Issue のラベルが type:chore に切り替わっていることを self-validation
```

## 影響ドキュメント

| ドキュメント | 影響 | 理由 |
|-------------|------|------|
| `docs/rfc/github-labels-standardization.md` | あり（**改訂**） | 方針反転、ラベル拡張、bot 所有ラベル分離方針追加 |
| `docs/dev/labels.md` | あり（**新規**） | ラベル運用ガイド |
| `docs/README.md` | あり（追記） | RFC セクションは既存、`docs/dev/labels.md` を「How-to」セクションに追加 |
| `CLAUDE.md` | あり（追記） | Documentation 表に `docs/dev/labels.md` |
| `docs/adr/` | なし | アーキテクチャ決定ではなく運用標準化のため RFC で扱う |
| `docs/ARCHITECTURE.md` | なし | システム構成変更なし |
| `docs/dev/development_workflow.md` | 影響可能性あり（要確認） | 既に「type → ラベル マッピング」表があり、`type:release` / `type:build` / `type:ci` 追加に伴い更新の可能性 |
| `docs/cli-guides/` | なし | CLI 仕様変更なし |
| `.claude/skills/issue-create/` | 影響可能性あり（要確認） | type:* 拡張に伴い、テンプレート type 候補に release/build/ci 追加の可能性 |

## テスト戦略

### 変更タイプ

**metadata-only（labels.yml）+ ci-only（labels-sync.yml）+ docs-only（RFC / labels.md / README / CLAUDE.md）の複合**。Python 実行時コードの変更は最小（pytest small 1 ケースのみ）。

### 恒久回帰テスト（pytest small）

`tests/test_labels_yml.py` を 1 ケース追加:

- **検証対象**: `.github/labels.yml` の機械的妥当性
  - YAML として parse 可能
  - 全 label に `name` / `color` / `description` が存在
  - `color` が `^[0-9a-f]{6}$` の hex 形式
  - `name` の重複なし
- **理由（恒久化する根拠）**:
  - `.github/labels.yml` は将来も Maintainer が手編集する
  - workflow の validate ジョブは push 後にしか動かないため、ローカル `make check` 段階で検出する価値が高い
  - 1 ファイル parse のみで Small 範囲、保守コスト極小

### Medium / Large テスト

**追加しない**。理由（`testing-convention.md` の 4 条件）:

1. ✅ 独自ロジックの追加・変更をほぼ含まない（YAML 定義 + GitHub Actions 標準パターン）
2. ✅ 想定不具合パターンは既存ゲート（pytest small + workflow validate ジョブ）で捕捉
3. ✅ Medium/Large を追加しても回帰検出情報が増えない（GitHub API 疎通テストは GitHub 側依存で reproducible 性が低い）
4. ✅ 理由をレビュー可能な形で記載済み（本セクション）

### 変更固有検証（一時的、PR で証跡提示）

| 検証 | 手段 |
|------|------|
| YAML 妥当性 | `pytest -m small tests/test_labels_yml.py`（恒久） |
| workflow 構文 | `gh workflow run labels-sync.yml -f dry_run=true` で actionlint 相当の検証 |
| dry-run 結果 | workflow log の `created: 19 / updated: 0 / unchanged: 0` を PR コメントに添付 |
| 本番 sync 結果 | `gh label list` 出力（19 ラベルのみ）を PR コメントに添付 |
| OPEN Issue 再ラベル | `gh issue list --state open --label <旧>` の出力 0 件を PR コメントに添付（5 旧ラベルすべて） |
| docs リンク整合 | `make verify-docs` |
| Python 品質 | `make check`（test_labels_yml.py を含む） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 改訂対象 RFC | `docs/rfc/github-labels-standardization.md` | 「共存期間: 無期限。既存 Issue の再ラベルは行わない」が現行方針。本 Issue で反転 |
| 二重ラベル証跡 | `gh issue view 141`, `gh issue view 147`, `gh issue view 148`, `gh issue view 149` | 旧 (`documentation` / `refactoring`) と新 (`type:docs` / `type:refactor`) を両方付与している実例 |
| スキル failure 証跡 | `gh issue view 153` | `/issue-create` 実行時に `type:feature` not found |
| 現行 repo ラベル | `gh label list --json name,color,description` 実行結果 | 13 ラベルが存在: `bug`, `documentation`, `duplicate`, `enhancement`, `good first issue`, `help wanted`, `invalid`, `question`, `wontfix`, `refactoring`, `e2e-test`, `type:docs`, `type:refactor` |
| 移植元 labels.yml | `/home/aki/dev/kamo2/.github/labels.yml` | 66 ラベル体系のうち type / meta 系のみ kaji 規模に合わせて抽出 |
| 移植元 workflow | `/home/aki/dev/kamo2/.github/workflows/labels-sync.yml` | 検証 → backup → sync → summary の 4 段構成。`validate-label-usage` ジョブと Issue #13 通知は kaji スコープ外 |
| Conventional Commits 1.0.0 | <https://www.conventionalcommits.org/en/v1.0.0/> | 「The type `feat` MUST be used... The type `fix` MUST be used...」標準 type 一覧の根拠 |
| release-please | <https://github.com/googleapis/release-please> | autorelease:pending / autorelease:tagged 等を bot が自動生成・管理する仕様（labels.yml 管理対象外とする根拠） |
| Dependabot config | <https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuration-options-for-the-dependabot.yml-file> | `labels` フィールドで PR ラベルを指定可能。デフォルト `dependencies` ラベル自動生成に合わせて先行作成する根拠 |
| GitHub Managing labels | <https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/managing-labels> | ラベルの色は 6 文字 hex（`#` なし）の仕様 |
| Octokit REST API (issues) | <https://octokit.github.io/rest.js/v20/#issues> | `octokit.issues.createLabel` / `updateLabel` / `listLabelsForRepo` の API シグネチャ |
| Catppuccin Mocha パレット | <https://catppuccin.com/palette> | 各色の hex コード正本（Green=`a6e3a1`, Red=`f38ba8` 等） |
| Catppuccin ライセンス | <https://github.com/catppuccin/catppuccin/blob/main/LICENSE> | MIT ライセンス。hex コード使用に attribution 義務なし |
| testing-convention | `docs/dev/testing-convention.md` | metadata-only / docs-only 変更で恒久テスト不要となる 4 条件の根拠 |
