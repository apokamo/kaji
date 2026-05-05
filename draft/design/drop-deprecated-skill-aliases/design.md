# [設計] 互換用 Skill エイリアスの削除（issue-pr / issue-doc-check）

Issue: TBD（未起票。`/issue-create` 実行前の事前設計）

## 概要

`.claude/skills/` 配下に残っている互換用エイリアス 2 件 (`issue-pr`, `issue-doc-check`) を、`.agents/skills/` 配下のシンボリックリンクと合わせて削除し、参照する README / CLAUDE.md / docs を正本名 (`i-pr`, `i-dev-final-check`) に統一する。これにより、後続の local mode 設計 (`draft/design/local-mode/design.md`) が対象とする「`gh` 直叩きを `kaji` CLI 経由へ書き換える」リファクタの母数を 25 → 23 に減らす。

**位置づけ: 互換 alias の意図的削除 — 運用インターフェース上の breaking change**。kaji runtime のロジック変更はゼロだが、これまで受理していた slash command `/issue-pr`, `/issue-doc-check` は **skill not found** で失敗するようになる。利用者は正本 `/i-pr`, `/i-dev-final-check` に切り替える必要がある。利用者は実質的に maintainer 単独で本人合意済みのため周知コストは小さいが、変更分類としては breaking として扱う。

## Primary Sources（一次情報）

| カテゴリ | パス / コマンド | 参照目的 |
|---------|----------------|---------|
| 削除対象 Skill | `.claude/skills/issue-pr/SKILL.md:2` (`description: 互換用エイリアス。新しい正本は i-pr`) | 自己宣言で alias であることを確認 |
| 削除対象 Skill | `.claude/skills/issue-doc-check/SKILL.md:2` (`description: 互換用エイリアス。新しい正本は i-dev-final-check`) | 自己宣言で alias であることを確認 |
| 削除対象 symlink | `.agents/skills/issue-pr` （`.claude/skills/issue-pr` への symlink） | `find .agents .claude -type l` で発見。`.claude` 実体削除と同 PR で消さないと dangling symlink が残る |
| 削除対象 symlink | `.agents/skills/issue-doc-check` （`.claude/skills/issue-doc-check` への symlink） | 同上 |
| symlink 構成根拠 | `docs/ARCHITECTURE.md:58`「他エージェント用ディレクトリ（例: `.agents/skills/`）はカノニカルディレクトリへのシンボリックリンクとして構成する」 | `.agents/skills/` 配下を同期して扱う規範 |
| 委譲先（正本） | `.claude/skills/i-pr/SKILL.md` | issue-pr の処理委譲先。削除後はこちらに完全集約 |
| 委譲先（正本） | `.claude/skills/i-dev-final-check/SKILL.md` | issue-doc-check の処理委譲先 |
| Workflow YAML | `.kaji/wf/*.yaml` を `grep -l "issue-pr\|issue-doc-check"` → **0 件** | 自動 step からは参照されていない（手動起動のみ）→ workflow 改修不要 |
| docs/dev/ 配下 | `grep -rn "issue-pr\|issue-doc-check" docs/dev/` → **0 件**（`/i-pr`, `/i-dev-final-check` の言及のみ） | 既に正本へ統一済み。本リファクタで `docs/dev/` の更新は不要 |
| 参照箇所（要更新） | `README.md:39`「`/issue-pr → /pr-fix → /pr-verify → /issue-close`」 | workflow 例の slash command 表記を `/i-pr` に置換 |
| 参照箇所（要更新） | `CLAUDE.md:136`「`/issue-pr`（`/i-pr` 経由）」 | スキル一覧表の更新 |
| 参照箇所（要更新） | `docs/ARCHITECTURE.md:43`「`/issue-pr → [PR review] → /pr-fix → /pr-verify → /issue-close`」 | 本文中の slash command 表記を `/i-pr` に置換 |
| 参照箇所（要更新） | `docs/ARCHITECTURE.md:60`「計 25 種」 | 23 種に更新 |
| 参照箇所（要更新） | `docs/ARCHITECTURE.md:70-72` (Skill カテゴリ表で `issue-pr`, `issue-doc-check` を別行掲載) | 表構造の更新 |
| 参照箇所（要更新） | `docs/guides/git-worktree.md:169` (`/issue-pr` 表記) | `/i-pr` へ置換 |
| 参照箇所（要更新） | `docs/guides/git-commit-flow.md:31, 86, 97, 113` (`/issue-pr` 表記 4 箇所) | `/i-pr` へ置換 |
| 既存設計の依存 | `draft/design/local-mode/design.md`（背景・目的セクション、「25 Skill のうち 21 が `gh` 直叩き」） | 母数の同期更新が必要（25→23 確定。21 → 20 が暫定値だが KPI 計測手法そのものに欠陥あり、後述「オープン論点 1」参照）。本 PR と同 PR で更新する |
| Grep コマンド | `grep -rn "issue-pr\|issue-doc-check" .claude .agents .kaji docs CLAUDE.md README.md` | 全参照箇所の網羅確認に使用（README / `.agents` 含む完全範囲）|
| Symlink 検証 | `find .agents -xtype l` | dangling symlink が残っていないこと（実装後 0 行）の確認 |

## 背景・目的

### 現状

`.claude/skills/issue-pr/` と `.claude/skills/issue-doc-check/` は「互換用エイリアス」を自称しており、それぞれ以下のように正本へ委譲するだけの薄いラッパーになっている。

- `issue-pr` → `i-pr`（`issue-pr/SKILL.md:8` に「新しい正本は i-pr」と記載）
- `issue-doc-check` → `i-dev-final-check`（`issue-doc-check/SKILL.md:8` に「新しい正本は i-dev-final-check」と記載）

両者ともに workflow YAML (`.kaji/wf/*.yaml`) からは参照されておらず、手動の slash command (`/issue-pr` 等) として叩かれた場合のみ機能する。また `.agents/skills/` 配下に `.claude/skills/` のカノニカル実体への symlink が同名で存在する（`docs/ARCHITECTURE.md:58` の規範に基づく）。

### 削除理由

| 理由 | 内容 |
|------|------|
| **後続作業の射程縮小** | `draft/design/local-mode/design.md` は `gh` 直叩きを `kaji` CLI 経由へ移行するリファクタを掲げている。alias を残したままだと「正本 + alias」両経路を書き換える必要があり、また alias は正本と二重メンテになる |
| **二重メンテの解消** | alias 側 `SKILL.md` には verdict 仕様や入力契約の写しが含まれており、正本との同期が崩れるリスクが恒常的に存在する（現状は同期が取れている） |
| **ドキュメント整合性** | CLAUDE.md / ARCHITECTURE.md / git-commit-flow.md が `/issue-pr` と `/i-pr` を混在させており、新規ユーザーが正本を判別しづらい |

### 削除しないもの

- `i-pr`, `i-dev-final-check`（正本）
- 他の Skill（`pr-fix`, `kaji-run-verify`, `i-doc-*` 系などはすべて現役）

ユーザー合意済み（2026-05-05）：alias 2 件以外で「使っていない」と判定できる Skill は無いと結論。

### このリファクタが軽い理由

`docs/dev/` 配下は既に正本 (`/i-pr`, `/i-dev-final-check`) へ統一済みで、alias 言及は 0 件（`grep -rn "issue-pr\|issue-doc-check" docs/dev/` で確認）。よって今回の置換対象は CLAUDE.md / ARCHITECTURE.md / docs/guides/ の少数のみ。

## スコープ

### In Scope

1. `.claude/skills/issue-pr/` ディレクトリの削除
2. `.claude/skills/issue-doc-check/` ディレクトリの削除
3. `.agents/skills/issue-pr` symlink の削除
4. `.agents/skills/issue-doc-check` symlink の削除
5. CLAUDE.md / docs 配下の参照を正本名へ置換（対象は「影響範囲」表に列挙）
6. `docs/ARCHITECTURE.md` の Skill 数記述を `25` → `23` に更新、Skill カテゴリ表から alias 行を削除
7. `draft/design/local-mode/design.md` の母数記述を本リファクタ後の数字に整合させる

### Out of Scope

- 正本 (`i-pr`, `i-dev-final-check`) の挙動変更
- workflow YAML の改修（参照無しのため不要）
- local mode 設計の前進（本リファクタ完了 _後_ に着手）
- 他 Skill の削除・統廃合
- local-mode design の KPI 計測手法そのものの再設計（オープン論点 1 で扱うが、本リファクタの完了条件には含めない）

## 影響範囲

### コード（実体ファイル / symlink）

```
.claude/skills/issue-pr/             ← ディレクトリごと削除
.claude/skills/issue-doc-check/      ← ディレクトリごと削除
.agents/skills/issue-pr              ← symlink 削除
.agents/skills/issue-doc-check       ← symlink 削除
```

### ドキュメント（文言更新）

| ファイル | 更新内容 |
|---------|---------|
| `README.md` | L39 の workflow 例 `/issue-pr → ...` を `/i-pr → ...` に置換 |
| `CLAUDE.md` | L136 の「PR 作成」行を `/i-pr` に統一（`（/i-pr 経由）` 表記を削除） |
| `docs/ARCHITECTURE.md` | L60「計 25 種」→「計 23 種」 |
| `docs/ARCHITECTURE.md` | L70-72 の表から `issue-pr`（`i-pr` への委譲ラッパー）と `issue-doc-check` を削除し、PR 行は `i-pr` のみ、その他 行は `kaji-run-verify` のみに |
| `docs/ARCHITECTURE.md` | L43 の `/issue-pr → [PR review] → ...` を `/i-pr → [PR review] → ...` に置換 |
| `docs/guides/git-worktree.md` | L169 の `/issue-pr` を `/i-pr` に置換 |
| `docs/guides/git-commit-flow.md` | L31, L86, L97, L113 の `/issue-pr` を `/i-pr` に置換（計 4 箇所） |
| `draft/design/local-mode/design.md` | Skill 数 `25` → `23`、`gh` 直叩き計数の補正（暫定 21 → 20、ただし計測手法の見直し方針はオープン論点 1 で示す） |

### 検証

- **主の検証**: `grep -rn "issue-pr\|issue-doc-check" .claude .agents .kaji docs CLAUDE.md README.md` の出力が **0 行**になること
- **symlink 検証**: `find .agents -xtype l` の出力が 0 行（dangling symlink がない）
- **補助検証**: `make check` 通過（コードに影響しないが念のため）
- **`make verify-docs` の位置づけ**: `Makefile` の実体は `python3 scripts/check_doc_links.py docs/ README.md CLAUDE.md .claude/skills/` で、markdown link (`[text](path)`) のリンク切れチェッカ。slash command 表記 `/issue-pr` は markdown link ではないため置換漏れは検出できない。**真の検証は grep**であり、`make verify-docs` の通過は副次的な確認に留まる

## テスト戦略

> CRITICAL: 変更タイプに応じて妥当な検証方針を定義すること。本変更は実行時コード変更を含まないため、変更固有検証で十分とする方針を採る（`docs/dev/testing-convention.md` の docs-only / metadata-only / packaging-only 区分に準ずる扱い）。

### 変更タイプ

**Skill / docs / symlink 整理のみ**（実行時コード変更なし）。`kaji_harness/` 配下の Python コード、`.kaji/wf/` 配下の workflow YAML、`pyproject.toml` 等のビルド設定はいずれも変更しない。変更内容は以下に限定される：

- `.claude/skills/` 配下 2 dir の削除
- `.agents/skills/` 配下 2 symlink の削除
- README / CLAUDE.md / docs / `draft/design/local-mode/design.md` の文言更新

### 変更固有検証

- `grep -rn "issue-pr\|issue-doc-check" .claude .agents .kaji docs CLAUDE.md README.md` → 0 行
- `find .agents -xtype l` → 0 行（dangling symlink 検知）
- `make check`（lint / format / typecheck / pytest が通過することを念のため確認）
- `make verify-docs`（markdown link checker、副次的確認）

### 恒久テストを追加しない理由

`docs/dev/testing-convention.md:101-107` の「正当化できる理由」に沿って：

- **実行時ロジック変更がなく、変更固有検証で十分**: kaji runtime の挙動変更はゼロ。Skill 解決ロジックは「存在する Skill を呼ぶ」だけであり、削除対象 Skill が無くなった時の挙動は既存ロジックの自然な動作（skill not found）で確認できる
- **既存ゲートで不具合パターンを捕捉できる**: `make check`（pytest 含む）と `make verify-docs` の既存ゲートで、コード回帰と markdown link 切れを捕捉できる。本変更固有の問題（参照漏れ・dangling symlink）は変更固有 grep / `find -xtype l` で捕捉可能であり、これらは恒久 CI ジョブ化する価値が薄い（このリファクタ以降は対象 Skill 自体が存在しないため、再発し得ない）
- **物理的に作成不可ではない**が、上 2 条件で正当化できるため恒久テスト化は不要

## 移行・互換性

### ユーザー影響

- これまで `/issue-pr <number>` を打鍵していた利用者は `/i-pr <number>` に変更が必要
- これまで `/issue-doc-check <number>` を打鍵していた利用者は `/i-dev-final-check <number>` に変更が必要

利用者は実質的に maintainer (apokamo) のみであり、本人合意済みのため周知コストは小さい。

### 互換期間

設けない。alias 自体が「互換用」であり、正本への移行は既に完了している段階。これ以上の互換維持は二重メンテのコストを払う対価が無い。

### Rollback

symlink を含む 4 path（実体 2 dir + symlink 2 個）の削除と docs 文言更新を 1 コミットにまとめる。`git revert` で巻き戻せる。symlink は git 上で `120000` モードとして記録されており、revert 時に復元される。

## リスクと対策

| リスク | 対策 |
|-------|------|
| 個人の打鍵習慣で `/issue-pr` を叩いてしまう | Claude Code の skill 一覧から消えるため自然に slash 補完で気付ける。誤打鍵は「skill not found」エラーで止まり、データを壊さない |
| 外部ドキュメント（README 等）から `/issue-pr` への言及漏れ | `grep` の対象範囲に `.claude .agents .kaji docs CLAUDE.md README.md` を含め、Primary Sources でも全参照箇所を列挙済み（README.md:39 を含む） |
| `.agents/skills/` symlink の削除漏れ | `find .agents -xtype l` を完了条件に組み込み、dangling symlink を検出 |
| `local-mode` 設計の母数記述（25 / 21）と乖離 | 同 PR 内で `draft/design/local-mode/design.md` の数字も更新。本設計のスコープに含める |

## 作業手順（実装フェーズの参考）

1. 事前 grep（KPI 確定）: `grep -l "gh issue\|gh pr\|gh api" .claude/skills/issue-pr/SKILL.md .claude/skills/issue-doc-check/SKILL.md` を実行し、削除対象 alias が「`gh` 直叩き 21」KPI に何件寄与していたかを確定する（オープン論点 1 参照）
2. `git rm -r .claude/skills/issue-pr .claude/skills/issue-doc-check .agents/skills/issue-pr .agents/skills/issue-doc-check`
3. CLAUDE.md / docs を「影響範囲」表に従って正本名に置換
4. `docs/ARCHITECTURE.md` の Skill 数（25 → 23）と表構造を更新
5. `draft/design/local-mode/design.md` の Skill 数（25 → 23）と「`gh` 直叩き」の値を Step 1 の結果で補正
6. 検証コマンド一括実行
   - `grep -rn "issue-pr\|issue-doc-check" .claude .agents .kaji docs CLAUDE.md README.md` → 0 行
   - `find .agents -xtype l` → 0 行
   - `make check`
   - `make verify-docs`（参考実行）
7. Conventional Commit `refactor!: drop deprecated skill aliases (issue-pr, issue-doc-check)` で 1 PR にまとめる（`!` で運用インターフェース上の breaking を明示。commit body に `BREAKING CHANGE: /issue-pr and /issue-doc-check slash commands are removed; use /i-pr and /i-dev-final-check instead.` を含める）

## 完了条件

- [ ] `.claude/skills/issue-pr/` 削除完了
- [ ] `.claude/skills/issue-doc-check/` 削除完了
- [ ] `.agents/skills/issue-pr` symlink 削除完了
- [ ] `.agents/skills/issue-doc-check` symlink 削除完了
- [ ] `find .agents -xtype l` が 0 行（dangling symlink がない）
- [ ] README.md, CLAUDE.md, docs 配下の `/issue-pr` `/issue-doc-check` 言及がすべて正本名に置換済み
- [ ] `docs/ARCHITECTURE.md` の Skill 数記述が 23 に更新済み
- [ ] `draft/design/local-mode/design.md` の母数記述（Skill 数・`gh` 直叩き数）が新しい値に整合
- [ ] `grep -rn "issue-pr\|issue-doc-check" .claude .agents .kaji docs CLAUDE.md README.md` が 0 行
- [ ] `make check` 通過
- [ ] commit message が `refactor!:` プレフィックス + `BREAKING CHANGE:` フッタを含む

## オープン論点

### 1. local-mode design の KPI「`gh` 直叩き 21」の計測手法見直し

事前検証（2026-05-05）で判明した事実：

```
$ grep -n "gh " .claude/skills/issue-pr/SKILL.md
77:  push と gh pr create が成功した   ← verdict サンプルテキストの prose
```

`grep -l "gh issue\|gh pr\|gh api" .claude/skills/*/SKILL.md` は文字列マッチであり、**verdict 例の prose にもヒットする**。`issue-pr/SKILL.md` はこの prose のみで hit しており、実際の `gh` 呼び出しは正本 `i-pr` への委譲になっている。`issue-doc-check/SKILL.md` は `gh` 文字列を含まず未 hit。

帰結：
- 本リファクタによる KPI 補正値は **21 → 20**（19 ではない）
- ただし、より重大な問題として **KPI 計測手法そのものが prose を含めて数えており、信頼性が低い**

対応方針：
- 本 PR では暫定値 20 で `local-mode/design.md` を更新する
- 計測手法の改善（コードブロック内の `gh` 呼び出しのみを抽出する等）は **本リファクタのスコープ外**とし、`local-mode/design.md` 側に「KPI 計測手法は再設計予定」の注記を追加する
- KPI の真値が不明な状態で local-mode を進めるのは設計判断としてリスクがあるため、local-mode 着手前に別途棚卸しを検討する（このリファクタ完了後の判断事項）

### 2. （削除）

旧設計にあった「PR 分割の論点」は意味が薄いため削除。本 PR は alias 削除・docs 更新・local-mode design 数字補正をすべて 1 PR にまとめる方針で確定。
