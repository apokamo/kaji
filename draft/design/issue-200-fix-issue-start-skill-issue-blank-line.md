# [設計] issue-start のメタ情報追記で blank line 欠落を起こさない決定的な合成経路を導入する

Issue: #200

## 概要

`/issue-start` Step 4 が Issue 本文先頭へ `> [!NOTE]` メタ情報ブロックを追記する際、blockquote と既存本文の間の blank line（および直前の改行）が、Haiku 等の一部エージェントが multi-line heredoc を忠実に再現できないことで欠落し、`> **Branch**: \`fix/199\`## 概要` のように本文先頭 heading が blockquote 行へ吸着する。本文合成を skill（エージェント）側の heredoc から `kaji` 内部の決定的な Python 経路へ移し、blank line を仕様上保証する。

## 背景・目的

### Observed Behavior（OB）

`full-cycle.yaml` の `start` ステップ（`model: haiku`）で `/issue-start 199` を実行した結果、Issue #199 本文先頭が以下のように崩れた（Issue #200 本文に記録された実観測ログ）:

```text
$ gh issue view 199 --json body -q '.body' | head -4
> [!NOTE]
> **Worktree**: `../kaji-fix-199`
> **Branch**: `fix/199`## 概要
```

`> **Branch**: \`fix/199\`` の直後に、本来あるべき「改行 + blank line」が 2 つとも欠落し、本文先頭の `## 概要` が blockquote 最終行へ連結している。結果として `## 概要` は heading ではなく blockquote 内テキストとして解釈される。

### Expected Behavior（EB）

Opus 担当実行（Issue #190）では blank line が保持され、blockquote 終端と本文の間に空行 1 行が入る（実観測）:

```text
$ gh issue view 190 --json body -q '.body' | head -6
> [!NOTE]
> **Worktree**: `../kaji-fix-190`
> **Branch**: `fix/190`
> **PR**: gh:198
                  ← blank line
## 設計書
```

skill spec（`.claude/skills/issue-start/SKILL.md:116-123`）の heredoc も、`> **Branch**: \`$BRANCH\`` の次行に blank line を置いてから `$CURRENT_BODY` を展開する形であり、EB はこの blank line が保持された状態。

> **Bettenburg et al. (2008)** に倣い OB / EB / steps-to-reproduce を分離記述。本 Issue は OB（#199 実ログ）と EB（#190 実ログ + skill spec の意図）が一次情報で揃っている。

### 目的

エージェント（モデル）の multi-line 忠実度に依存せず、`/issue-start` のメタ情報追記が **どのモデルでも決定的に** blank line を保持することを保証する。

## 再現手順（Steps to Reproduce）

1. 前提: GitHub provider の Issue が存在し、本文先頭が `## ` で始まる（先頭に blank line を持たない）。`full-cycle.yaml` のように `start` ステップが `model: haiku`。
2. `kaji run .kaji/wf/full-cycle.yaml <issue_id>`（または Haiku エージェントで `/issue-start <issue_id>`）を実行。
3. `gh issue view <issue_id> --json body -q '.body' | head -5` で本文先頭を観測。
4. `> **Branch**: ...` の直後に空行・改行なしで本文 heading が連結したケースを確認（OB）。

> **再現の性質**: 本 OB はエージェントの heredoc 再現挙動に依存し、Haiku で観測・Opus で未観測の **モデル依存・確率的** な事象。確率的事象である以上、エージェントの再実行による回帰検証は安定せず、回帰テストは「決定的なコード経路」に対してのみ成立する（→ § テスト戦略、§ 方針）。

## 根本原因（Root Cause）

### なぜ壊れるか

`SKILL.md:114-123` のメタ情報合成は、エージェントが以下の multi-line bash heredoc を **忠実に再構成して実行する** ことに依存している:

```bash
NEW_BODY=$(cat <<EOF
> [!NOTE]
> **Worktree**: \`../$WT_BASENAME\`
> **Branch**: \`$BRANCH\`
            ← この「空行 1 行」が分離子
$CURRENT_BODY
EOF
)
kaji issue edit [issue_id] --commit --body "$NEW_BODY"
```

blockquote と `$CURRENT_BODY` の分離子は **「文字としては空の 1 行」** に過ぎない。エージェントは SKILL.md を逐語実行するのではなく、`[issue_id]` 等を補完して **自らコマンドを再構成** する。Haiku 等の小型・高速モデルは再構成時に「意味的に不可視」な空行を脱落させやすく、`> **Branch**: \`$BRANCH\`` 行末の改行ごと潰して `$CURRENT_BODY` を同一行へ連結する。OB の `\`fix/199\`## 概要` は、改行 1 + 空行 1 の計 2 つが消えた状態と一致する。

### kaji 側に正規化は無い

`kaji issue edit --body` は body を素通しで `gh issue edit --body` へ渡すだけで、Python 側に blank line 正規化は存在しない（`kaji_harness/providers/github.py:211-234` `edit_issue`、`kaji_harness/cli_main.py:1061-1070` dispatch）。よって本文の正否は **完全にエージェントが組んだ文字列に委ねられている**。これが robustness 不足の核心。

### いつから

heredoc の空行分離子は skill 初版（`b0ade53`, 2026-01-08、旧 `.claude/commands/issue-start.md`）から存在する latent な脆弱性。`git blame .claude/skills/issue-start/SKILL.md -L 116,123` で line 120 の空行が初版由来であることを確認。`full-cycle.yaml` の `start` ステップを `model: haiku`（`.kaji/wf/full-cycle.yaml:52-55`）に割当てたことで顕在化した。Opus を使う `full-cycle-xhigh.yaml`（`start` は `model: sonnet`）等では未顕在。

### 同根の他壊れ箇所

メタ情報の追記はエージェント heredoc 合成に依存する箇所が `SKILL.md` Step 4 の **1 箇所のみ**。`/i-pr` の `**PR**` 追記は別 skill だが「既存 NOTE ブロックへの 1 行追記」であり、本文 heading との境界問題は発生しない（調査の結果、本 Issue のスコープ外）。`kaji issue edit` を直接叩く他 skill（review 系コメント等）はメタ情報の先頭追記を行わないため非該当。

## インターフェース

### 新規 CLI subcommand（provider 共通）

```
kaji issue prepend-note <issue_id> --worktree <worktree_basename> --branch <branch> [--commit]
```

- **入力**: `issue_id`（必須）、`--worktree`（NOTE に載せる worktree 相対パスの basename、例 `kaji-fix-200`）、`--branch`（例 `fix/200`）、`--commit`（local provider で `issue.md` を atomic commit。github では silent に無視＝既存 `edit` と同契約）。すべて **単一トークン引数** で、エージェントは multi-line 文字列を組まない。
- **動作**: kaji 内部で (1) `provider.view_issue(id).body` で現在本文を取得 → (2) 純粋関数で NOTE ブロック + blank line + 現在本文を決定的に合成 → (3) `provider.edit_issue(id, body=...)`（local は `--commit` 時に既存 commit helper へ委譲）。
- **dispatch 位置**: `gh issue prepend-note` は存在しないため、`context` と同様に `_handle_issue` の **local/github 分岐より前** で provider 共通 helper として捕捉する（`kaji_harness/cli_main.py:1052-1059` の `context` パターンに倣う）。`view_issue` / `edit_issue` は両 provider に実装済みのため単一実装で両対応。
- **出力**: 副作用は Issue 本文更新（GitHub API / local `issue.md`）。stdout には成功時に更新後本文の要約 or 何も出さない（既存 `edit` に準拠）。失敗時 exit code は既存規約（`EXIT_INVALID_INPUT` 等）に従う。

### 純粋関数（テスト対象の核）

```python
def build_worktree_note_body(current_body: str, *, worktree: str, branch: str) -> str:
    """NOTE メタブロックを current_body 先頭へ blank line 1 行を保証して合成する。"""
```

- **保証する不変条件**:
  - 戻り値 = `> [!NOTE]\n> **Worktree**: \`../{worktree}\`\n> **Branch**: \`{branch}\`\n\n{normalized_body}`
  - blockquote と本文の間は **常に空行ちょうど 1 行**（`current_body` 先頭の余分な空行は剥がしてから 1 行を付与）。
  - `current_body` が空文字 → NOTE ブロックのみ（末尾 blank line は付けない）。
  - I/O 副作用なし（純粋関数）。
- backtick / `$` 等を含む値も文字列として安全に埋め込む（shell 評価を経ないため heredoc の backtick 展開問題が原理的に消える）。

### SKILL.md Step 4 の変更

heredoc による `NEW_BODY` 合成 + `kaji issue edit --body "$NEW_BODY"` を廃止し、次へ置換:

```bash
WT_BASENAME=$(basename "$WT")
kaji issue prepend-note [issue_id] --worktree "$WT_BASENAME" --branch "$BRANCH" --commit
```

エージェントは単一トークン 3 つを渡すだけで、multi-line 本文合成を一切行わない。

### 後方互換性

- 出力本文の形（NOTE ブロックのレイアウト）は EB と同一を維持 → `development_workflow.md` の NOTE ブロック例（lines 187-193）と整合し、後段 skill（`/i-pr` の `**PR**` 追記、`/i-dev-final-check` の設計書添付）への影響なし。
- `kaji issue edit` の既存挙動・引数は不変（新 subcommand を追加するのみ）。

## 制約・前提条件

- kaji は Python 単一スタック。Scope 分岐なし。
- 新 subcommand は github / local 両 provider で動作必須（`/issue-start` が両対応のため）。
- **非目標（スコープ外）**: NOTE ブロックの重複追記防止（idempotency ガード）は現行 heredoc 方式にも無く、本 Issue では既存挙動と parity を維持する（混在を避ける）。必要なら別 Issue で追跡。
- 最小侵襲を優先しつつ、bug の回帰テスト必須要件（`bug.md` § 8）を満たすため、決定的なコード経路の導入は不可避（→ § 方針 の代替案比較）。

## 方針

### アプローチ選定

| 案 | 内容 | 採否 | 理由 |
|----|------|------|------|
| A: skill printf 化 | SKILL.md の heredoc を `printf '...\n\n%s' "$CURRENT_BODY"` に置換 | ✗ | 依然エージェントが `\n\n` を含むコマンドを再構成する＝モデル忠実度依存が残る。かつ Python コードが増えず **恒久回帰テストが作れない**（`bug.md` § 8 の必須要件を満たせない） |
| B: kaji 側で決定的合成（採用） | view→合成→edit を kaji 内部 Python で実施。skill は単一トークン引数を渡すのみ | ✓ | エージェントの multi-line 忠実度依存を **原理的に除去**。純粋関数として Small で回帰テスト可能。Issue が許容する「`kaji issue edit` 側で normalize」方向 |
| C: 既存 body の事後正規化 | `kaji issue edit` 受領 body に「blockquote 直後へ blank line 挿入」を後付け | ✗ | OB は `## 概要` が blockquote 最終行へ **吸着済み**（`> **Branch**: ...## 概要`）であり、`## 概要` は既に blockquote 内テキスト扱い。事後正規化では「本来本文だった行」を blockquote から切り離せない。合成時に防ぐしかない |

### 採用案 B の合成擬似コード

```python
def build_worktree_note_body(current_body, *, worktree, branch):
    note = f"> [!NOTE]\n> **Worktree**: `../{worktree}`\n> **Branch**: `{branch}`"
    body = current_body.lstrip("\n")  # 先頭の余分な空行を剥がす
    if not body:
        return note + "\n"
    return f"{note}\n\n{body}"

# CLI handler（_handle_issue_prepend_note, provider 共通; context と同位置で捕捉）
current = provider.view_issue(issue_id).body
new_body = build_worktree_note_body(current, worktree=wt, branch=branch)
# local: 既存 _local_issue_edit / _commit_local_issue_change 経路へ委譲（--commit atomic）
# github: provider.edit_issue(issue_id, body=new_body)
```

blank line がエージェントではなく **Python 文字列リテラル `\n\n`** に固定されるため、モデルに依存しない。

## テスト戦略

### 変更タイプ

実行時コード変更（kaji_harness に新 subcommand + 純粋関数を追加）+ 付随する skill spec（SKILL.md）更新。中核は実行時コード変更。

> **bug 固有ルール（`bug.md` § 8）**: 修正前 Red → 修正後 Green の恒久回帰テストを最低 1 本、省略不可。本 Issue の OB はモデル依存・確率的でエージェント再実行による回帰検証は安定しないため、**決定的なコード経路（純粋関数 + CLI dispatch）に対して** 回帰テストを定義する。新規の純粋関数はテスト記述時点で未実装＝Red、実装後 Green に遷移し、実装前 Red 証跡となる。加えて Issue #199 の実観測 OB ログを escape clause の実世界 Red 証跡として併記する。

#### Small テスト（核となる回帰テスト）

`build_worktree_note_body` の不変条件を検証:

- **blank line 保証**: `current_body="## 概要\n..."` → 戻り値が `...> **Branch**: \`fix/200\`\n\n## 概要` を含み、blockquote 行と `## 概要` の間が **空行ちょうど 1 行**（OB の `\`fix/200\`## 概要` 連結が起こらないことを assert）。← OB を直接 assert する回帰テスト本体。
- **本文先頭の余分な空行を 1 行に正規化**: `current_body="\n\n## 概要"` でも空行 1 行へ収束（冪等性）。
- **空 body**: `current_body=""` → NOTE ブロックのみ、末尾に余分な blank line を付けない。
- **特殊文字耐性**: backtick / `$` / 複数行を含む body をそのまま保持（shell 評価を経ないこと）。

#### Medium テスト（CLI dispatch 結合）

`kaji issue prepend-note` の end-to-end（local provider、git fixture）:

- `git init` fixture + local Issue を用意し `kaji issue prepend-note <id> --worktree ... --branch ... --commit` 実行後、`issue.md` 本文先頭が NOTE ブロック + blank line + 元本文の形になることを検証。
- `context` と同じ provider 共通 dispatch で捕捉され、`gh` へ誤 forward しないこと（github 側は `view_issue`/`edit_issue` 経路を使う）。
- **subprocess patch 方針**: `testing-convention.md` § subprocess.run patch スコープに従い、dispatch 結合テストでは `subprocess.run` 名前空間 patch を使わず、`git init -q --initial-branch=<default_branch>` fixture 系で検証する。

#### Large テスト

不要。理由（`testing-convention.md` の 4 条件に照合）:

1. 独自ロジックの中核は純粋関数（Small）と dispatch（Medium）で被覆済み、実 GitHub API 疎通に固有の新規ロジックは無い（github 経路は既存 `edit_issue` の再利用）。
2. 想定不具合（blank line 欠落）は Small が決定的に捕捉。
3. 実 API 疎通テストを足しても回帰検出情報は増えない。
4. github 実機での最終確認は完了条件どおり実 Issue 再実行（Haiku / Opus）で代替（`make verify-*` ではなく実運用確認、Issue #200 完了条件 item 3）。

### 完了条件との対応

- 根本原因の特定 → § 根本原因（heredoc 合成のエージェント忠実度依存 + kaji 側正規化不在）。
- blank line 保持の保証 → 案 B（kaji 内部決定的合成）。
- Haiku / Opus 双方で保持を再現確認 → 単一トークン引数化により合成がエージェント非依存となり、両モデルで実 Issue 再実行確認可（item 3）。
- `make check` 通過 → 新規コードに Small/Medium テスト + ruff/mypy。
- `/issue-start` Haiku 担当化の記載整合 → § 影響ドキュメント。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキ構造変更なし（既存 dispatch パターンの踏襲） |
| docs/dev/development_workflow.md | 要確認（おそらく変更なし） | NOTE ブロック例（lines 185-202）は出力レイアウト不変のため整合維持。`/issue-start` を Haiku 担当化する旨の明示記載は development_workflow.md には無く、モデル割当は `.kaji/wf/full-cycle.yaml:55` の `model: haiku` に存在（item 5 の条件付き整合確認の結論: doc 側に該当記述なし＝doc 変更不要） |
| docs/cli-guides/ | あり（要追記） | `kaji issue prepend-note` を CLI ガイドに追加（新 subcommand のため） |
| docs/reference/ | なし | API 規約変更なし |
| CLAUDE.md | なし | 規約変更なし |
| .claude/skills/issue-start/SKILL.md | あり | Step 4 を `kaji issue prepend-note` 呼び出しへ置換（本 Issue の skill spec 変更本体） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| issue-start skill spec | `.claude/skills/issue-start/SKILL.md:114-127` | Step 4 heredoc。blockquote と `$CURRENT_BODY` の分離子が「空行 1 行」であり、エージェント再構成で脱落しうる脆弱性の所在 |
| Issue #199 本文（OB 実ログ） | `gh issue view 199 --json body -q '.body'`（Issue #200 本文にも転記） | `> **Branch**: \`fix/199\`## 概要` の連結。改行 1 + 空行 1 の計 2 つが欠落した実観測 |
| Issue #190 本文（EB 実ログ） | `gh issue view 190 --json body -q '.body'` | NOTE ブロック終端と本文の間に空行 1 行が保持された正しいレイアウト |
| 担当モデル割当 | `.kaji/wf/full-cycle.yaml:52-55` | `start` ステップ `model: haiku`。OB 顕在化の発生源。対して `full-cycle-xhigh.yaml` の `start` は `sonnet` |
| heredoc 初版 | `git blame .claude/skills/issue-start/SKILL.md -L 116,123`（`b0ade53`, 2026-01-08） | 空行分離子が skill 初版由来の latent 脆弱性であること（「いつから」の裏付け） |
| issue dispatch（provider 共通捕捉） | `kaji_harness/cli_main.py:1052-1070` | `context` が local/github 分岐前に捕捉される実装。`prepend-note` も `gh` に存在しないため同位置で捕捉する設計根拠 |
| provider edit/view | `kaji_harness/providers/github.py:197-234` | `view_issue` / `edit_issue` が両 provider に実装済みで、`--body` を素通しする（kaji 側正規化が無い）こと |
| local commit helper | `kaji_harness/cli_main.py:1440-1470` `_commit_local_issue_change` | `--commit` の atomic commit ロジック（no-op edit skip 含む）を prepend-note が再利用する根拠 |
| テスト規約（4 条件 / サイズ / subprocess patch） | `docs/dev/testing-convention.md:60-76, 132-145` | Large 省略の 4 条件照合、dispatch 結合テストの subprocess patch 禁止スコープ |
| bug 設計ガイド（回帰テスト必須 / escape clause） | `.claude/skills/_shared/design-by-type/bug.md:62-74` | 修正前 Red → 修正後 Green の恒久回帰テスト必須、実ログ escape clause の適用条件 |
