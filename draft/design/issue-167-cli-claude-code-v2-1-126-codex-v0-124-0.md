# [設計] CLI 仕様変更（claude-code v2.1.150 / codex v0.124.0 / gemini v0.39.1）への追従

Issue: #167

## 概要

claude-code / codex / gemini CLI の更新によって生じたハーネス実装と
`docs/cli-guides/` 配下ガイドの不整合を解消する。中核は claude v2.1.x
で削除された `--max-turns` に依存するハーネスコード（`Step.max_turns`）
の整理と、3 つの CLI ガイドを現行 `--help` 出力に揃える更新。

## 背景・目的

### 現状の問題

| CLI | 種類 | 内容 |
|---|---|---|
| Claude Code v2.1.150 | ハーネス | `kaji_harness/cli.py:287-288` で `step.max_turns` 指定時に `--max-turns N` を付与するが、現行 claude `--help` から `--max-turns` が消えている（v2.1.71 → v2.1.150 の間で削除済）。指定されると unknown option で実行失敗となる潜在バグ |
| Claude Code v2.1.150 | docs | `docs/cli-guides/claude-code-cli-guide.md` に `--max-turns` / `--init` / `--maintenance` / `--remote` / `--teleport` 等の旧オプションが残存。`--permission-mode` の `auto` choice が未記載。対象バージョン v2.1.71 のまま |
| Codex v0.124.0 | docs | `docs/cli-guides/codex-cli-session-guide.md` の対象バージョンが v0.112.0。`codex app`（macOS デスクトップ起動）の記載が古く、現行は `app-server`（experimental）。`--experimental-json` 表記が現行 help と差異 |
| Gemini v0.39.1 | docs | `docs/cli-guides/gemini-cli-session-guide.md` で `-p` を「非推奨」と断定しているが、現行 `gemini --help` に deprecated 表記なし（`--experimental-acp` / `--allowed-tools` には明示的に deprecated タグあり、`-p` にはなし）|

### 救い（max_turns の実害なし）

リポジトリ全体（`.kaji/wf/` / `.claude/skills/` / その他）で `max_turns:` を
**YAML として指定している箇所はゼロ**（`grep -rn "max_turns"` 確認済み。
ヒットは Python コード・テスト・docs リファレンスのみ）。よって現時点では
unknown option エラーは実発火していない潜在バグ。

### ユーザーストーリー

- **ハーネス保守者として**、ワークフロー作者が `step.max_turns` を設定したときに突然 unknown option で落ちるリスクを排除したい
- **ワークフロー作者として**、`docs/cli-guides/` を参照したときに現行 CLI と齟齬のないオプション情報を得たい

### 代替案と本設計の選択

`Step.max_turns` の扱いには 3 案ある：

| 案 | 内容 | 採否 | 理由 |
|----|------|------|------|
| A | `Step.max_turns` フィールドごと廃止 | **採用** | (1) 実利用箇所がゼロ。(2) claude が削除した時点で代替が無い（codex / gemini も元から非対応）。(3) 既に `max_budget_usd` がコスト制御の正規手段として存在。(4) 廃止しても破壊範囲ゼロ |
| B | `--max-budget-usd` 案内に統合（field は残す） | 不採用 | field を残しても CLI 引数に展開できないので、設定可能だが効かない dead field になる。むしろ罠 |
| C | claude changelog で復活待ち | 不採用 | claude の budget 制御は明確に `--max-budget-usd` へ移行済。`--max-turns` が再追加される根拠なし |

## インターフェース

### 入力

#### ハーネス側の変更

- `Step` データクラス: `max_turns: int | None` フィールドを**削除**
- `_build_claude_args()`: `--max-turns` の追加処理を**削除**
- `_parse_step()` 相当（`workflow.py`）: YAML `max_turns:` の読み取りを**削除**

#### docs 側の変更（contents のみ。CLI 引数や API には触れない）

- `docs/cli-guides/claude-code-cli-guide.md`: 対象バージョンを v2.1.150 に更新、`--max-turns` / `--init` / `--maintenance` / `--remote` / `--teleport` を削除、`--permission-mode` の choices に `auto` 追記
- `docs/cli-guides/codex-cli-session-guide.md`: 対象バージョンを v0.124.0 に更新、`codex app` → `app-server`（experimental）に修正、`--experimental-json` の説明を `--json` に統一（codex v0.124.0 では `--json` が正式名）
- `docs/cli-guides/gemini-cli-session-guide.md`: 対象バージョンを v0.39.1 に更新、`-p` の「非推奨」表記を是正（位置引数 = interactive、`-p` = headless の関係を明示）

### 出力

#### ハーネス側

- `_build_claude_args()` の出力配列から `--max-turns N` が永久に消える
- `kaji validate <workflow>.yaml` で `max_turns:` を含む YAML が来た場合 → **WorkflowValidationError**（不明キーとして扱う）。後方互換シムは設けない（実利用ゼロが確認済のため）

> **不明キー検証の挙動**: 現状の `workflow.py` パーサは未知キーを silent ignore する設計か、エラーにする設計か実装を確認したうえで実装フェーズで判断する。silent ignore のままなら本変更ではエラー化しない（破壊変更を増やさない）。エラー化する派生改修は別 Issue で起票する。

#### docs 側

- 各ガイドの「変更履歴」に 1 行追記
- 各ガイドの「一次情報と検証状況」テーブルの検証日を 2026-05-23 に更新

### 使用例

#### Before（現状の YAML 例）

```yaml
# 実利用ゼロのため、これは仮想例
steps:
  - id: design
    agent: claude
    max_turns: 80    # ← 廃止対象
    max_budget_usd: 5.0
```

#### After

```yaml
steps:
  - id: design
    agent: claude
    max_budget_usd: 5.0    # turn 制御は budget で代替
```

### エラー

- 廃止後に `max_turns:` を含む YAML が読み込まれた場合の挙動は workflow.py の不明キーポリシーに従う。本 Issue では「許容（silent ignore）」「拒否（ValidationError）」のどちらでも構わない（実害ゼロ）。実装フェーズで現行挙動を確認し、最も影響の少ない方を採る

## 制約・前提条件

- **Python 単一スタック**（backend / frontend の分岐なし）
- 既存の `_build_claude_args()` / `_build_codex_args()` / `_build_gemini_args()` の **公開シグネチャは不変**（外部から呼ぶ箇所は test_cli_args.py のみ）
- `max_budget_usd` フィールドは現状維持（claude のみ実引数として渡る、他 CLI では ignore する既存挙動を維持）
- docs 更新は文章のみ。`docs/cli-guides/` 配下の構造（章立て）は変更しない（既存リンクへの影響回避）

## 方針

### 1. ハーネス変更（最小スコープ）

3 ファイルの編集だけで完結する：

- `kaji_harness/models.py:51` — `max_turns: int | None = None` 行を削除
- `kaji_harness/cli.py:287-288` — `if step.max_turns: args += [...]` の 2 行を削除
- `kaji_harness/workflow.py:168` — `max_turns=step_data.get("max_turns"),` の 1 行を削除

### 2. テスト更新

- `tests/test_cli_args.py` の `TestClaudeArgs.test_with_max_turns` を削除
- `tests/test_cli_args.py` の `TestCodexArgs.test_max_turns_ignored` を削除（フィールド廃止に伴い無意味化）
- `tests/test_cli_args.py` の `_make_step()` ヘルパから `max_turns` 引数を削除
- `tests/test_workflow_parser.py` の `max_turns: 10` を含む YAML サンプルと assertion を削除

### 3. docs 更新（3 ファイル独立に編集）

各ガイドは independent な編集で、コード変更とは別 commit に分けることを推奨（review 単位を明確化）。

### 4. 検証

- `make check` 通過
- 各 CLI で実際に `--help` を実行して docs と突き合わせるエビデンスを PR 説明に添付（手元検証済の出力は本設計書「参照情報」セクションにも記載）

### 5. 参照ドキュメントの影響範囲

`max_turns` を言及している以下も併せて修正対象とする：

- `docs/dev/workflow-authoring.md:92` — `max_turns` の表エントリを削除
- `docs/reference/python/type-hints.md:89` — サンプルコード内の `max_turns:` 行を削除（または別フィールドに差し替え）

## テスト戦略

### 変更タイプ

- **混合変更**:
  - 実行時コード変更: `kaji_harness/cli.py` / `models.py` / `workflow.py` の 3 ファイル（Step フィールド廃止 + 引数組立変更）
  - docs-only: `docs/cli-guides/` 3 ファイル + `docs/dev/workflow-authoring.md` + `docs/reference/python/type-hints.md`

### 実行時コード変更の場合

#### Small テスト

- `tests/test_cli_args.py`: `_make_step()` から `max_turns` パラメータが削除されてもコンパイル可能（mypy 通過）
- `tests/test_cli_args.py::TestClaudeArgs::test_basic_new_session` 等の既存 assertion（`--max-turns` を期待しないケース）は不変で通過することを再確認
- `tests/test_workflow_parser.py`: `max_turns:` を含まない YAML サンプルでパースが通ること（既存テスト維持）
- **新規追加**: なし。`max_turns` フィールド廃止は「機能の削除」であり、削除後の状態を保護する正の検査は既存テスト（`test_basic_new_session` で `--max-turns` を含まない配列を厳密一致で expect している）で十分カバーされる

#### Medium テスト

- 不要。本変更は subprocess 経由の CLI 起動挙動を変えない（引数組立ロジックの分岐削除のみで、ファイル I/O / DB / 内部サービス結合は無関係）
- `docs/dev/testing-convention.md` の 4 条件: (1) 独自ロジック追加なし ✅ (2) Small で既存ゲート充分 ✅ (3) Medium 追加で得られる回帰シグナル増分なし ✅ (4) 本 Why 欄で説明可能 ✅

#### Large テスト

- 不要。同上。実 CLI 疎通テストは本変更の検証対象外（CLI が実在しても `--max-turns` の有無は引数組立段階で決定し、subprocess 起動には到達しない）
- `docs/dev/testing-convention.md` の 4 条件: 上記 Medium と同じ理由で全 4 条件充足

### docs-only / metadata-only / packaging-only の場合（docs 更新分）

#### 変更固有検証

- `make verify-docs` — リンク整合性チェック（docs 内クロスリンクが切れていないこと）
- 各 CLI ガイドについて、対応する `<cli> --help` 出力を手元で取得し、docs の主要オプション表との突き合わせを目視で確認した記録を PR に添付
- 検証コマンド: `claude --help > /tmp/claude-help.txt`, `codex --help > /tmp/codex-help.txt`, `gemini --help > /tmp/gemini-help.txt`

#### 恒久テストを追加しない理由

`docs/dev/testing-convention.md` の 4 条件:

1. **独自ロジックの追加・変更を含まない** ✅ — docs の文字列更新のみ
2. **想定される不具合パターンが既存テスト/ゲートで捕捉済み** ✅ — リンク切れは `make verify-docs` で捕捉、CLI 引数齟齬は本 Issue の Small テスト（既存 assertion）で捕捉
3. **新規テスト追加で回帰検出情報が増えない** ✅ — docs 文字列の正誤を CI でテストするには `<cli> --help` を CI 環境にインストールする必要があり、コストに対して回帰価値が見合わない（CLI のバージョン追従はこの種の手動 Issue で対応する性質のもの）
4. **テスト未追加の理由をレビュー可能な形で説明できる** ✅ — 本セクションで明記

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 技術選定変更なし（既存 CLI のバージョン追従のみ） |
| docs/ARCHITECTURE.md | なし | アーキテクチャ非変更 |
| docs/dev/workflow-authoring.md | **あり** | `max_turns` フィールドの記述削除（line 92） |
| docs/dev/development_workflow.md | なし | ワークフロー手順非変更 |
| docs/reference/python/type-hints.md | **あり** | `Step` クラスのサンプルコードに `max_turns:` が含まれる（line 89）。サンプル更新 |
| docs/cli-guides/claude-code-cli-guide.md | **あり** | v2.1.150 への追従（主修正対象） |
| docs/cli-guides/codex-cli-session-guide.md | **あり** | v0.124.0 への追従（主修正対象） |
| docs/cli-guides/gemini-cli-session-guide.md | **あり** | v0.39.1 への追従（主修正対象） |
| CLAUDE.md | なし | プロジェクト規約非変更 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Claude Code `--help`（v2.1.150 ローカル実行、2026-05-23） | `claude --help \| grep -E "(--max-turns\|--max-budget-usd\|--permission-mode)"` の出力 | 出力に `--max-turns` 行ゼロ。`--max-budget-usd <amount>` と `--permission-mode <mode>` の choices に `"acceptEdits", "auto", "bypassPermissions", "default", "dontAsk", "plan"` を確認 → `--max-turns` 廃止と `--permission-mode auto` 追加の一次根拠 |
| Claude Code `--help`（v2.1.150 ローカル実行、2026-05-23） | `claude --help \| grep -E "^\s*--(init\|maintenance\|teleport)"` の出力 | 出力ゼロ。`--init` / `--maintenance` / `--teleport` が v2.1.150 では削除されていることを確認（docs ガイドからの削除根拠）|
| Codex CLI `codex --help`（v0.124.0 ローカル実行、2026-05-23） | `codex --help` の Commands セクション | 現行コマンドに `app-server [experimental] Run the app server or related tooling` がある一方、`codex app` 単体は存在しない → docs の `codex app` 記述の修正根拠 |
| Gemini CLI `gemini --help`（v0.39.1 ローカル実行、2026-05-23） | `gemini --help \| grep -E "(--prompt\|-p)"` の出力 | `-p, --prompt   Run in non-interactive (headless) mode with the given prompt.` に `DEPRECATED` / `deprecated` の語なし。比較として `--allowed-tools` には `[DEPRECATED: Use Policy Engine instead ...]` が明示されている → docs の「`-p` 非推奨」表記は誤りであることの一次根拠 |
| ハーネス実装（現状） | `/home/aki/dev/kaji/main/kaji_harness/cli.py:287-288` | `if step.max_turns: args += ["--max-turns", str(step.max_turns)]` — claude args 組立で削除対象となる箇所 |
| ハーネス実装（現状） | `/home/aki/dev/kaji/main/kaji_harness/models.py:51` | `max_turns: int | None = None` — Step データクラスのフィールド定義（削除対象）|
| ハーネス実装（現状） | `/home/aki/dev/kaji/main/kaji_harness/workflow.py:168` | `max_turns=step_data.get("max_turns"),` — YAML パース箇所（削除対象）|
| 既存テスト | `/home/aki/dev/kaji/main/tests/test_cli_args.py:89-96, 195-202` | `test_with_max_turns` / `test_max_turns_ignored` — 廃止に伴い削除する対象テスト |
| 既存テスト | `/home/aki/dev/kaji/main/tests/test_workflow_parser.py:50,123,157` | `max_turns: 10` YAML サンプルおよび assertion — 削除対象 |
| 実利用ゼロの確認 | `grep -rn "max_turns" .kaji/wf/ .claude/skills/` の結果 | YAML / Markdown 内で `max_turns:` 指定箇所がゼロであることを確認（破壊範囲ゼロの根拠）|
| testing-convention | `docs/dev/testing-convention.md` § テスト戦略の原則 / 省略してよい理由 | 本設計書「テスト戦略」セクションの省略判断は本ドキュメントの 4 条件に依拠 |
