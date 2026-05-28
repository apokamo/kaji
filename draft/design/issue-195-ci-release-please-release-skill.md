# [設計] release-please 資産を削除し `/release` skill 単独運用に統一する

Issue: #195

## 概要

`.github/workflows/release-please.yml` / `release-please-lock.yml` および `.github/release-please-config.json` / `.release-please-manifest.json` を削除し、`docs/operations/release/admin-setup.md` 現況 banner を「資産削除済み」と整合する記述に更新する。release 運用を `/release` skill 単独に統一する。

## 背景・目的

### 経緯

- 2026-05-25: `#153` (release-please 導入) と `local-p1-11` (CI 資産の GitLab 移植) が **close not-planned**。release-please は forge を問わず採用しない方針が確定。
- 2026-05-25 以降: release 運用は `/release` skill ベース（CI 非依存 / maintainer 手元実行）に移行済み。`docs/operations/release/admin-setup.md` 冒頭 banner で「現状: 非運用 (historical)」と明示。
- `#184`（forge GitHub primary 化 / close not-planned）と `#191`（GitLab forge 完全撤去 / merge）の整理過程で release-please workflow 資産だけが残置。
- 結果として `.github/workflows/release-please.yml` は `on: push(main)` で main への push ごとに起動し続けるが、`RELEASE_PLEASE_APP_ID` / `RELEASE_PLEASE_APP_PRIVATE_KEY` secret が未登録のため `actions/create-github-app-token@v1` step で失敗する。

### ユーザーストーリー

- **release maintainer** として、main への push ごとに secret 欠如で失敗する release-please workflow が起動する状態を解消したい。failure 通知ノイズと「どちらが正の release 経路か」の認知負荷を排除するため。
- **release maintainer** として、`/release` skill 実行時に release-please workflow が意図せず競合発火する余地を残したくない。採用不可と確定（#153 close not-planned）した経路の資産が残っていると、将来 secret が誤って付与された瞬間に二重 release を引き起こすリスクがあるため。
- **contributor** として、main merge 後に GitHub Actions タブで赤い release-please run が並ぶ状態を見たくない。自分の PR に起因する真の失敗との区別が付きづらく、調査コストが上がるため。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| workflow ファイルを残し `on:` を `workflow_dispatch` のみに変更 | 「採用不可と確定済み資産」を残す合理的理由がない。secret 誤付与時の二重 release リスクを構造的に排除できる削除が優越。 |
| `if: false` で disable | 上記同様。`/release` skill 単独運用の宣言と矛盾する偽の選択肢を repo に残す。 |
| admin-setup.md ごと削除 | historical 経緯として残す合意が #153 close 時点で形成済み（admin-setup.md 行 5–10 の banner と `docs/README.md:52` の `(historical)` ラベル付与）。本 Issue では削除しない。 |

## インターフェース

### 入力

- 削除対象 4 ファイル（リポジトリ root 相対）:
  - `.github/workflows/release-please.yml`
  - `.github/workflows/release-please-lock.yml`
  - `.github/release-please-config.json`
  - `.release-please-manifest.json`
- 更新対象 1 ファイル:
  - `docs/operations/release/admin-setup.md`（冒頭 banner のみ。historical 本文 = 行 13 以降は不変）

### 出力

- **削除**: 上記 4 ファイルが repository から消失。次回 main push 以降、release-please workflow は GitHub Actions に列挙されなくなる。
- **banner 更新**: `admin-setup.md` 冒頭 banner に「release-please 資産は #195 で削除済み」相当の一文を追記。historical 参照価値（過去の構成・運用フロー）は本文に残す。
- **commit**: `chore: remove release-please assets, consolidate to /release skill (#195)` 相当の単一 commit。
- **PR description**: 「release-please は #153 close not-planned で採用不可と確定済み。`/release` skill 単独運用に統一」相当の宣言文を必ず含める（Issue #195 完了条件）。`/i-pr` 実行時に body にこの文言を反映する。

### 使用例

本 Issue 完了後の運用フロー:

```bash
# release maintainer 手元で
/release            # version bump + CHANGELOG + tag + GitHub Release ページ作成
/release --dry-run  # push / gh release create を skip するローカル確認
```

main push 時に GitHub Actions タブで release-please run は **起動しない**（workflow file 自体が存在しないため）。

### エラー

本 Issue は **ファイル削除 + banner 文言更新** のみで実行時コードを含まない。ランタイムエラーケースは存在しない。検証段階で発生し得る失敗:

- `make check`: 削除対象は全て `.github/` 配下と repo root の json で kaji_harness/ には影響しない。失敗が出た場合は baseline 失敗（本 Issue 変更と無関係）か regression かを切り分ける。baseline であれば「無関係な問題」として報告するが、Issue #195 の完了条件は `make check` PASS であるため、未達のまま final-check に進めない（baseline であっても本 Issue PR 内で fix するか、別 Issue で先行 fix した上で再度 `make check` PASS を確認する必要がある）。
- `make verify-docs`: `admin-setup.md` 内の link が banner 更新で切れた場合に検出される。

## 制約・前提条件

- `/release` skill (`.claude/skills/release/SKILL.md`) は `.github/workflows/release-please.yml` に依存していない（skill 内の言及は「再有効化は対象外」という除外記述のみ。skill 動作自体は不変）。
- `.github/labels.yml` には `release-please が自動生成する autorelease:* / release-please:* は bot 所有で管理対象外` というコメント（行 7）と、`type:release` description 内に `release-please 自動 PR には付けない`（行 49）の言及がある。これらは「将来 release-please bot が動いた場合」の前提を残しており、削除によって厳密には obsolete になる。ただし `#195` のスコープ外（Issue 完了条件に含まれていない）として **本 Issue では変更しない**。後続 cleanup Issue で扱う候補として「無関係な問題」記録対象。
- `docs/dev/workflow_completion_criteria.md:55` には `#153` (release-please 導入) を例示した historical 記述がある。これも本 Issue スコープ外。
- `draft/design/issue-153-release-please.md` / `draft/design/issue-154-labels-standardization.md` / `docs/rfc/github-labels-standardization.md` / `docs/dev/labels.md` 等の historical / RFC ドキュメント中の release-please 言及は **削除しない**（Issue スコープ外。歴史的経緯として保持）。
- `.release-please-manifest.json` は現在 `{".": "0.9.1"}` を保持しているが、`/release` skill は `pyproject.toml` の `version` を SoT としており manifest を参照しない（`.claude/skills/release/SKILL.md` 確認済み）。削除しても release 運用上の SoT 喪失は発生しない。

## 変更スコープ

### 変更対象

| ファイル | 操作 |
|---------|------|
| `.github/workflows/release-please.yml` | 削除 |
| `.github/workflows/release-please-lock.yml` | 削除 |
| `.github/release-please-config.json` | 削除 |
| `.release-please-manifest.json` | 削除 |
| `docs/operations/release/admin-setup.md` | 冒頭 banner 一文追記のみ |

### スコープ外

- `draft/design/issue-153-release-please.md`（historical 設計書、残置）
- `docs/operations/release/admin-setup.md` 行 13 以降の historical 本文
- `.claude/skills/release/SKILL.md`（既に「再有効化は対象外」と明示済みで矛盾なし）
- `.github/labels.yml` の release-please 関連コメント（後続 cleanup 候補）
- `docs/dev/workflow_completion_criteria.md` の `#153` historical 言及（後続 cleanup 候補）
- `docs/dev/labels.md` / `docs/rfc/github-labels-standardization.md` / `docs/README.md` 内の release-please 言及（historical / RFC として保持）
- `/release` skill の挙動変更
- 他の `.github/workflows/` ファイル（`labels-sync.yml` 等）

### 混在禁止

- 本 Issue は release-please 資産削除のみ。他の CI / release 改修は含めない。

## 方針（Minimal How）

### Step 1: ファイル削除

```bash
cd [worktree_dir]
git rm .github/workflows/release-please.yml \
       .github/workflows/release-please-lock.yml \
       .github/release-please-config.json \
       .release-please-manifest.json
```

### Step 2: admin-setup.md banner 更新

冒頭 banner（現状 行 3–10）に「`.github/workflows/release-please.yml` 等の資産は #195 で削除済み」相当の一文を追記する。文言案:

```markdown
> **⚠️ 現状: 非運用 (historical)**
>
> kaji の release 運用は **`/release` skill ベース（CI 非依存 / maintainer 手元実行）** に移行している。本ドキュメントが対象とする GitHub release-please フローは **現在使用していない**。
>
> **`.github/workflows/release-please.yml` / `release-please-lock.yml` / `.github/release-please-config.json` / `.release-please-manifest.json` は #195 で削除済み**。本ドキュメントは将来 release-please ベース運用を再開する場合の参考資料として残置する。
>
> - 現行リリース運用: [`runbook.md`](./runbook.md)（`/release` skill ベース）と [`.claude/skills/release/SKILL.md`](../../../.claude/skills/release/SKILL.md)
> - 本ドキュメントの位置付け: 将来 release-please ベース運用を再開する場合の参考資料 / 歴史的経緯
```

historical 本文（行 13 以降の Step 1–6 手順、トラブルシューティング、PAT rotation）は不変。

### Step 3: 検証

```bash
cd [worktree_dir]
make check         # ruff + mypy + pytest
make verify-docs   # link checker（banner 更新後の link 整合）
```

### Step 4: commit

```bash
git add -A
git commit -m "chore: remove release-please assets, consolidate to /release skill (#195)"
```

### Step 5: PR description ハンドオフ事項

`/i-pr` で PR を作成する際、body に以下の宣言文を必ず含めること（Issue #195 完了条件）:

> release-please は #153 close not-planned で採用不可と確定済み。本 PR で `.github/workflows/release-please*.yml` および `.github/release-please-config.json` / `.release-please-manifest.json` を削除し、release 運用を `/release` skill 単独に統一する。

`/i-pr` 側 skill が PR body テンプレートを生成する際、本セクションを参照して同等趣旨の文言を `## Summary` / `## Why` 相当のセクションに反映する。文言は逐語ではなく要旨一致で可とするが、「#153 close not-planned」「`/release` skill 単独運用」の 2 要素は必ず含める。

## テスト戦略

### 変更タイプ

**metadata-only / docs-only 混在**。実行時 Python コード変更はゼロ。

- `.github/workflows/*.yml` 削除 → GitHub Actions metadata 変更（CI workflow 構成）
- `.github/release-please-config.json` / `.release-please-manifest.json` 削除 → metadata 変更
- `admin-setup.md` 更新 → docs-only

### 変更固有検証

| 検証項目 | 手段 | 期待結果 |
|---------|------|---------|
| 削除後 GitHub Actions に release-please workflow が列挙されない | PR merge 後、main で `gh workflow list -R apokamo/kaji` | release-please / release-please-lock が表示されない |
| 削除後 main push で release-please run が起動しない | PR merge 後、次回 main push 時 `gh run list -R apokamo/kaji --limit 5` | release-please workflow の run が存在しない |
| 削除によって既存 CI が壊れない | `make check` PASS（pre-merge）。CI 上の `labels-sync` 等の他 workflow が引き続き起動 | `make check` PASS、他 workflow 影響なし |
| admin-setup.md link 整合 | `make verify-docs` PASS（pre-merge） | link checker PASS |
| `/release` skill が動作不変 | `.claude/skills/release/SKILL.md` を読み、release-please 資産に依存していないことを確認（静的レビュー）。skill 実行自体は本 Issue スコープ外 | release-please.yml への依存記述なし |
| PR description に採用不可確定と `/release` 単独運用が明記されている | `/i-pr` 実行後に `gh pr view --json body -q .body` で取得し、「#153 close not-planned」「`/release` skill 単独運用」相当の文言を grep 確認 | 両要素を含む宣言文が body に存在 |

### 恒久テストを追加しない理由

`docs/dev/testing-convention.md` の 4 条件すべてを満たす:

1. **独自ロジックの追加・変更をほぼ含まない** — Python コード変更ゼロ。ファイル削除のみ。
2. **想定される不具合パターンが既存テストまたは既存品質ゲートで捕捉済み** — `make check` / `make verify-docs` / actionlint（GitHub 側）で workflow 妥当性は担保。release-please 自体が消えるため将来回帰する対象なし。
3. **新規テストを追加しても回帰検出情報がほとんど増えない** — 「削除されていることを保証するテスト」は既存ゲートでカバー不可（存在しないものをテストする価値が低い）。仮に再追加された場合は別の design / review プロセスで検知される。
4. **テスト未追加の理由をレビュー可能な形で説明できる** — 本セクションで明示。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 新規技術選定なし（既存採用判断の撤回） |
| `docs/ARCHITECTURE.md` | なし | アーキテクチャ変更なし |
| `docs/dev/` | なし（本 Issue スコープ） | `workflow_completion_criteria.md:55` の `#153` 言及は historical として残置（スコープ外）。`labels.md` の release-please 言及も historical として残置 |
| `docs/reference/` | なし | API / 規約変更なし |
| `docs/cli-guides/` | なし | CLI 仕様変更なし |
| `docs/operations/release/admin-setup.md` | **あり** | 冒頭 banner に削除済み宣言を追記（行 13 以降の historical 本文は不変） |
| `docs/operations/release/runbook.md` | なし | `/release` skill ベース運用は不変 |
| `docs/README.md` | なし | admin-setup.md エントリは既に `(historical)` ラベル付き（行 52） |
| `CLAUDE.md` | なし | 規約変更なし |
| `.claude/skills/release/SKILL.md` | なし | 既に「`release-please.yml` の再有効化は対象外」と明示済み（行 18）。削除によって整合性が崩れない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #195 本文 | `kaji issue view 195` | 完了条件で 4 ファイル削除 + admin-setup.md banner 更新 + `make check` PASS + PR description 文言を明示 |
| Issue #153 close 経緯 | `gh issue view 153 -R apokamo/kaji` | 2026-05-25 close not-planned。release-please 採用見送りが forge を問わず確定 |
| Issue #184 close 経緯 | `gh issue view 184 -R apokamo/kaji` | forge GitHub primary 化 close 時点で release-please 資産削除を未着手のまま close |
| 現行 release skill | `.claude/skills/release/SKILL.md` 行 18 | `.github/workflows/release-please.yml` の再有効化は skill 対象外と明示。skill 動作は workflow 不在でも完結 |
| 現行 admin-setup banner | `docs/operations/release/admin-setup.md` 行 3–10 | 「現状: 非運用 (historical)」「GitHub release-please フローは現在使用していない」と明示済み。本 Issue で「削除済み」一文を追記 |
| testing-convention 4 条件 | `docs/dev/testing-convention.md` 行 70–75 | docs-only / metadata-only 変更で恒久テスト省略を正当化する 4 条件 |
| GitHub Actions workflow file 解釈 | https://docs.github.com/en/actions/using-workflows/about-workflows | workflow YAML を `.github/workflows/` から削除すると、次回以降のイベントで該当 workflow は trigger されない（ファイル不在＝定義不在） |
| release-please-action README | https://github.com/googleapis/release-please-action#github-credentials | release-please は `.github/release-please-config.json` / `.release-please-manifest.json` をベースに動作。これらを削除すれば action は構造的に起動できない |
