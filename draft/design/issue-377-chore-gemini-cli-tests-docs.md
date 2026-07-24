# [設計] Gemini CLI 関連コード・tests・docs の削除

Issue: #377

## 概要

検証手段を維持できない Gemini CLI を kaji の公開 agent contract から外し、
`kaji_harness/` の実装・`tests/`・packaging metadata・利用者向け docs に残る
Gemini CLI サポートを一体で削除する。削除後、workflow YAML の `agent: gemini` は
unknown agent として validation error になる（alias / 自動 migration なし）。

## 背景・目的

kaji maintainer は現在の Gemini CLI backend を利用できる認証済み検証手段を持たず、
headless・tool execution・stream event・failure・session resume を E2E 検証できない。
mock や過去の CLI help だけでは現行外部 CLI contract を保証できないため、検証不能な
実装を残すと利用者に保守対象と誤認させ、allowed-agent validation・tests・docs の
継続コストを発生させる。

- **ユースケース**: kaji maintainer として、再現可能に検証できる agent
  （Claude Code / Codex / Antigravity CLI）だけを公開サポート対象として示すために、
  Gemini CLI の実装・tests・docs を一体で削除したい。
- 削除判断と将来の再対応条件は Issue #267 本文・maintainer コメントを
  source of truth とする（詳細は「重要判断 provenance」）。
- Gemini CLI 自体も公式に Antigravity CLI への移行を告知しており
  （google-gemini/gemini-cli discussion #27274）、kaji 側は #376 で Antigravity CLI
  対応を独立に完了済み。

## インターフェース

### 入力

- workflow YAML の step `agent` フィールド（str）。
  削除後の valid agent 集合は `{"claude", "codex", "antigravity"}`
  （`kaji_harness/agents.py` の `AGENT_CAPABILITIES` が単一情報源。
  `workflow.py` の `VALID_AGENTS` は同 dict から導出される既存構造を変更しない）。

### 出力

- `agent: gemini` を含む workflow YAML のロード時、既存の unknown-agent 検証
  （`kaji_harness/workflow.py` の `step.agent not in VALID_AGENTS` 分岐）が
  `WorkflowValidationError` を送出する。エラーメッセージの allowed 列挙は
  `(allowed: ['antigravity', 'claude', 'codex'])` に変わる。
- gemini 専用の新規エラーメッセージ・警告・migration 出力は追加しない
  （#267 決定: alias / 自動変換なし。unknown agent と同一扱い）。

### 使用例

```yaml
# 削除後: この workflow はロード時に WorkflowValidationError
steps:
  - id: design
    skill: issue-design
    agent: gemini        # -> "unknown agent 'gemini' (allowed: ['antigravity', 'claude', 'codex'])" 相当
    on: {PASS: end}
```

利用者の対応は workflow YAML の `agent` を `claude` / `codex` / `antigravity` の
いずれかへ手動で書き換えることのみ。

### エラー

- `build_cli_args()`（`kaji_harness/cli.py`）と formatter args builder
  （`kaji_harness/verdict.py`）の `case "gemini"` 分岐を削除すると、既存の
  `case _` fallback が `ValueError("Unknown agent: ...")` を送出する。
  workflow validation が先段で止めるため、通常経路では到達しない防御層。

## 制約・前提条件

- **breaking change**: `agent: gemini` を使う既存 workflow YAML はロード不能になる。
  互換層・deprecation 期間は設けない（#267 の人間決定）。
- #376 で追加済みの Antigravity CLI 対応（adapter / args builder / capability
  registry）には手を入れない（Issue スコープ境界）。
- Claude / Codex adapter の仕様変更を行わない。
- 過去 artifact（`legacy/`・`CHANGELOG.md` 既存 entry・`docs/adr/`・過去 Issue/PR・
  Git 履歴）は書き換えない（Issue スコープ境界）。
- Gemini の **model 名**（`gemini-3-pro` 等）は Antigravity CLI が実行する model の
  識別子として正当に残る。削除対象は「agent としての gemini」のみ。
- 削除は revert 可能（Git 履歴に全実装が残る）だが、公開サポート表明の撤回という
  意味で対外的には one-way door。この判断自体は #267 で人間決定済み。

## 変更スコープ

| 領域 | ファイル | 変更内容 |
|------|----------|----------|
| capability registry | `kaji_harness/agents.py` | `AGENT_CAPABILITIES` の `"gemini"` entry 削除（`VALID_AGENTS` / effort 検証は導出により自動追随） |
| adapter | `kaji_harness/adapters.py` | `GeminiAdapter` クラスと `ADAPTERS["gemini"]` entry 削除。`treats_stream_error_as_failure` 周辺コメントの「Claude / Gemini」表記を実態に合わせ更新 |
| args builder | `kaji_harness/cli.py` | `build_cli_args()` の `case "gemini"` と `_build_gemini_args()` 削除 |
| verdict formatter | `kaji_harness/verdict.py` | formatter args builder の `case "gemini"` と docstring の agent 列挙から gemini 削除 |
| workflow validator | `kaji_harness/workflow.py` | 直接変更なし（registry 導出）。gemini 前提のコメントがあれば整理 |
| tests | `tests/test_agents.py` / `test_adapters.py` / `test_cli_args.py` / `test_cli_streaming_integration.py` / `test_codex_robustness.py` / `test_e2e_cli.py` / `test_verdict_integration.py` / `test_wf_token_usage.py` / `test_workflow_validator.py` / `test_workflow_parser.py` / `test_interactive_terminal.py` / `test_cli_validate.py` / `test_cli_main.py` / `test_provider_guard_large_local.py` / `test_local_cli_large_local.py` | Gemini 専用 test / fixture / E2E availability test（`TestRealGeminiCLI` 等）の削除、共有 parametrize の allowed-agent 期待値更新、`agent="gemini"` を使う汎用テストの他 agent への置換（詳細はテスト戦略） |
| packaging | `pyproject.toml` | `keywords` から `"gemini"` 削除 |
| 利用者向け docs | `README.md` / `README.ja.md` / `llms.txt` | 正式対応表記から Gemini CLI を除去（対応 agent は Claude Code / Codex / Antigravity CLI）|
| docs (architecture / reference) | `docs/ARCHITECTURE.md` / `docs/reference/configuration.md` / `configuration.ja.md` | support matrix・adapter 一覧・起動コマンド例・関連ドキュメント表（gemini guide への link 行）を更新 |
| docs (dev) | `docs/dev/workflow-authoring.md` / `skill-authoring.md` / `workflow_guide.md` / `development_workflow.md` | `agent` 許容値列挙・effort 検証表の gemini 行・「Codex / Gemini」等の agent 列挙を更新 |
| プロジェクト規約 | `AGENTS.md` | 冒頭の agent 列挙（Claude / Codex / Gemini）を現行 support matrix に一致させる |
| guide 削除 | `docs/cli-guides/gemini-cli-session-guide.md` | ファイル削除。参照元（`docs/ARCHITECTURE.md` の関連ドキュメント表）から link 行を除去 |

### `rg -i 'gemini'` 残件の分類方針（完了条件対応）

実装完了時に `rg -i 'gemini'` を実行し、残件を以下の分類で実装報告に記録する。

| 分類 | 対象 | 残す理由 |
|------|------|----------|
| 過去 artifact | `legacy/**`・`CHANGELOG.md` 既存 entry・`docs/adr/002`・`docs/adr/003`・`.kaji/issues/`・`draft/design/` 過去設計書 | Issue スコープ境界「過去 Issue、過去 PR、Git 履歴内 artifact の書き換え」に該当。履歴の書き換えは行わない |
| Gemini model 名 | `docs/cli-guides/antigravity-cli-session-guide.md` の `model: gemini-3-pro`、tests / docs 内の `gemini-3-pro` 等の model 文字列（Antigravity 文脈） | Antigravity CLI が実行する model の識別子。agent contract とは別軸で、削除対象外 |
| starter 記述 | `docs/guides/python-starter.md` / `python-starter.ja.md` の gemini 行 | 別 repo `kaji-starter-python` の現状 snapshot を記述する guide。starter 追随は release 後の `/update-starter` workflow（starter-sync）の責務で、本 Issue では書き換えない（AI の仮定。review-design で検査） |
| 上記以外 | — | 原則ゼロにする。残る場合は個別に理由を記録 |

## 方針

1. **単一情報源からの削除**: `kaji_harness/agents.py` の `AGENT_CAPABILITIES` から
   `"gemini"` を削除する。`VALID_AGENTS`・effort 検証辞書は同 dict から導出されて
   いるため、workflow validation は追加コードなしで gemini を拒否する。
2. **dispatch 分岐の削除**: `adapters.py` / `cli.py` / `verdict.py` の gemini 分岐と
   `GeminiAdapter` / `_build_gemini_args` を削除する。unknown agent への防御は
   既存の `case _` / registry lookup 失敗経路をそのまま使う。
3. **tests の再編**: Gemini 専用テストは削除、共有 parametrize は期待値を
   3 agent 構成へ更新、gemini を「便宜的な素材」として使っていた汎用テスト
   （resume 契約・interactive terminal guard・effort passthrough 等）は
   検証対象の性質を保ったまま他 agent または合成 fixture へ置換する。
4. **docs の一括整合**: 利用者向け docs から正式対応表記を除去し、
   `gemini-cli-session-guide.md` を削除、参照元 link を除去する。
   削除後の support matrix（Claude Code / Codex / Antigravity CLI）が
   `AGENT_CAPABILITIES` の実装上の valid agent 集合と一致することを確認する。
5. **互換層は作らない**: `gemini` → `antigravity` の alias・自動変換・警告付き
   deprecation は実装しない（#267 決定）。

## 互換性・migration 方針（breaking change の記録）

- **非互換の内容**: workflow YAML の `agent: gemini` がロード時に
  `WorkflowValidationError` になる。CLI 実行・verdict formatter でも gemini は
  unknown agent 扱い。
- **利用者への migration 手順**: workflow YAML の `agent` を `claude` / `codex` /
  `antigravity` へ手動で書き換える。自動変換は提供しない。Antigravity CLI は
  Gemini model（`gemini-3-pro` 等）を実行できるため、Gemini model を使いたい
  利用者は `agent: antigravity` + `model: gemini-3-pro` が実質的な移行先
  （ただし kaji としての自動 mapping は行わない）。
- **将来の再対応条件**: Git 履歴を参考にできるが、再対応時はその時点の CLI 仕様と
  認証済み E2E 検証環境を前提に再設計する（#267 決定）。
- release 時の CHANGELOG には breaking change として記載する（記載自体は
  `/release` フェーズの責務。本設計書がその根拠記録）。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| Gemini support の扱い | 正式サポート対象外。legacy adapter としても維持しない | Issue #267 maintainer コメント「geminiCLIは検証手段がない」「関連ソースとドキュメントは削除方針」（#377 本文「重要判断」表に転記済み、人間決定） | `AGENT_CAPABILITIES` entry 削除を起点に、導出値（`VALID_AGENTS` / effort 検証）を自動追随させる構成へ分解 |
| code / tests / docs の一体削除 | 同一 Issue / PR で実装・tests・利用者向け docs を削除 | #267 maintainer コメント「ドキュメントとプログラムは一体」（人間決定） | 変更スコープ表のファイル単位へ具体化。`rg -i 'gemini'` 残件の 4 分類（過去 artifact / model 名 / starter 記述 / その他）を定義 |
| workflow 互換性 | `agent: gemini` は validation error。alias / 自動 migration なし | #267 の削除方針と Gemini/AGY の contract 差（人間決定） | 既存 unknown-agent 検証をそのまま使い、gemini 専用エラーを追加しない方針へ詳細化 |
| 将来の再対応 | Git 履歴を参考に、現行仕様・認証済み E2E 環境前提で再設計 | #267 maintainer 確認と決定事項（人間決定） | 互換性・migration 方針節に記録。本 Issue では準備コード（hook 等）を残さない |
| Antigravity との分離 | AGY 追加は #376 で独立実装済み。本 Issue では AGY に触れない | #267 maintainer が指定した機能単位分割（人間決定） | #376 成果物（`AntigravityAdapter` / `_build_antigravity_args` / capability entry）を変更対象から除外 |
| Gemini model 名の扱い | `gemini-3-pro` 等の model 文字列は削除対象外 | AI の仮定。根拠: Antigravity CLI は Gemini model を実行する CLI であり（antigravity-cli-session-guide.md の `model: gemini-3-pro` 例）、model 名は agent contract と別軸。検査先: review-design / review-code | rg 残件分類「Gemini model 名」として明文化 |
| python-starter guide の gemini 記述 | 本 Issue では書き換えず残す | AI の仮定。根拠: 同 guide は別 repo `kaji-starter-python` の snapshot を記述し、guide 自身が「starter は post-release starter-sync で追随」と明記。starter 追随は `/update-starter` workflow の責務。検査先: review-design | rg 残件分類「starter 記述」として明文化。starter-sync 時の追随対象であることを実装報告に残す |
| gemini を素材に使う汎用テストの置換方法 | 検証対象の性質を保ったまま他 agent / 合成 fixture へ置換 | AI の仮定。根拠: resume 契約・interactive terminal guard・effort passthrough は gemini 固有でなく registry 駆動の一般機構。検査先: review-code（テスト意図の保存を確認） | テスト戦略の Small / Medium 節へ置換方針を記載 |

## テスト戦略

### 変更タイプ

実行時コード変更（`kaji_harness/` の実行経路変更）＋ docs 削除・metadata 変更の複合。
実行時コード変更を含むため S/M/L の観点を定義する。

### Small テスト

- **capability registry**: `AGENT_CAPABILITIES` の key 集合が
  `{"claude", "codex", "antigravity"}` であること（`tests/test_agents.py` の
  期待値更新。gemini の capability assert は削除）。
- **workflow validation（breaking change の回帰ガード）**: `agent: gemini` を含む
  workflow が `WorkflowValidationError` になり、エラーメッセージの allowed 列挙が
  `['antigravity', 'claude', 'codex']` であることを明示的に検証する
  （完了条件「validation error になる」の直接証跡）。
- **共有 parametrize の更新**: `test_workflow_validator.py` の valid-agent
  parametrize から gemini を除去し、allowed 列挙の期待文字列を更新。
- **args builder**: `build_cli_args()` が gemini で `ValueError` fallback に
  到達すること（防御層の確認）。`_build_gemini_args` 直接テストは削除。
- **adapter registry**: `ADAPTERS` の key 集合更新。`GeminiAdapter` 単体テスト
  （`test_adapters.py` / `test_codex_robustness.py` の Gemini JSONL ケース）は削除。
- **verdict formatter**: gemini formatter args テスト（`test_verdict_integration.py`）
  を削除し、unknown agent で `ValueError` になることを確認。
- **置換が必要な汎用テスト**:
  - resume 契約テスト（`test_workflow_validator.py` の gemini resume）→
    resume 対応 agent（claude / codex）で同一観点を維持
  - effort passthrough テスト（`test_workflow_parser.py`。`effort_allowed=None`
    経路の唯一の registry 該当が gemini だった）→ 経路自体は registry 駆動の
    一般機構として残し、テストは合成 capability または削除で対応
    （実装時に判断。削除する場合は理由を実装報告に記録）
  - interactive terminal guard テスト（`test_interactive_terminal.py` の
    `agent="gemini"`）→ 未登録 agent 名で同じ guard 経路
    （`capabilities is None`）を検証する形へ置換

### Medium テスト

- **streaming 統合**: `test_cli_streaming_integration.py` の Gemini streaming /
  failure terminal ケースを削除し、残存 agent の streaming 経路が全パスすること。
- **token usage**: `test_wf_token_usage.py` の gemini step fixture を残存 agent へ
  置換または削除（token 集計ロジック自体は agent 非依存であることを確認）。
- **CLI main / validate / dispatch**: `test_cli_main.py` / `test_cli_validate.py` の
  gemini 参照（helper の agent_dirs 等）を更新し、dispatch 結合経路が全パスすること。

### Large テスト

- **E2E availability**: `test_e2e_cli.py` の `TestRealGeminiCLI`（実 CLI 検出時のみ
  走る availability test）を削除。これが「検証手段を持たない E2E」の本体であり、
  削除自体が本 Issue の目的。
- **large_local**: `test_local_cli_large_local.py` / `test_provider_guard_large_local.py` /
  `test_antigravity_large_local.py` のコメント・docstring 中の agent 列挙を更新
  （実行経路の変更なし）。
- **新規 Large テストは追加しない**: 理由 — 本変更は機能追加でなく削除であり、
  削除後の外部疎通対象（claude / codex / antigravity）の Large テストは既存のまま
  有効。削除の回帰は Small の validation テストで検出できる。

### 変更固有検証

- `rg -i 'gemini'` を worktree 全体で実行し、残件を「rg 残件の分類方針」の 4 分類で
  実装報告に記録する（恒久テスト化しない理由: 残件分類は本削除に固有の一時確認で、
  過去 artifact を許容する grep ルールを恒久化すると false positive 保守が発生する。
  testing-convention の 4 条件のうち「新規テストを追加しても回帰検出情報が
  ほとんど増えない」に該当。将来 agent 追加時の回帰は registry の Small テストが守る）。
- `make verify-docs` で削除した guide への link 切れがないことを確認する。
- `make check` 全パス（完了条件）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 技術選定の追加はない。削除判断の記録は #267 / #377 / 本設計書で足りる。既存 ADR 002/003 の Gemini 言及は過去決定の記録として不変 |
| docs/ARCHITECTURE.md | あり | adapter 一覧・support matrix・起動コマンド例・関連ドキュメント表（gemini guide link）の更新 |
| docs/dev/ | あり | `workflow-authoring.md`（agent 許容値・effort 検証表）、`skill-authoring.md` / `workflow_guide.md` / `development_workflow.md`（agent 列挙表記）|
| docs/reference/ | あり | `configuration.md` / `configuration.ja.md` の headless / interactive 対応 agent 記述 |
| docs/cli-guides/ | あり | `gemini-cli-session-guide.md` を削除 |
| AGENTS.md / CLAUDE.md | あり（AGENTS.md のみ） | 冒頭の agent 列挙から Gemini を除去し現行 support matrix と一致させる。CLAUDE.md に gemini 言及なし |
| README.md / README.ja.md / llms.txt | あり | 正式対応表記・対応 agent 列挙の更新 |
| docs/guides/python-starter*.md | なし（今回） | 別 repo starter の snapshot 記述。starter-sync（`/update-starter`）で追随（provenance 表参照） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠(引用/要約) |
|--------|----------|-------------------|
| Issue #377 本文 | https://github.com/apokamo/kaji/issues/377 | スコープ境界・完了条件・重要判断表の正本。「`agent: gemini`を設定段階で明確に拒否したい」 |
| Issue #267（親調査 Issue） | https://github.com/apokamo/kaji/issues/267 | 削除判断の source of truth。maintainer コメント「geminiCLIは検証手段がない」「関連ソースとドキュメントは削除方針」「ドキュメントとプログラムは一体」（#377 本文に転記あり） |
| Gemini CLI → Antigravity 公式移行告知 | https://github.com/google-gemini/gemini-cli/discussions/27274 | Gemini CLI 側も Antigravity CLI への移行を告知しており、削除判断の外部裏付け |
| capability registry | `kaji_harness/agents.py` | `AGENT_CAPABILITIES` に `"gemini"` entry（binary/resume/JSONL/effort=None）。削除の起点 |
| workflow validation | `kaji_harness/workflow.py:65` / `:500-503` | `VALID_AGENTS = frozenset(AGENT_CAPABILITIES)` の導出と unknown-agent エラー `(allowed: [...])`。registry 削除だけで validation error が成立する根拠 |
| dispatch / adapter / formatter | `kaji_harness/cli.py:148-149,454-472` / `kaji_harness/adapters.py:337-431` / `kaji_harness/verdict.py:499-503` | 削除対象の gemini 分岐の所在。`case _` fallback が防御層として既存 |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L 定義・恒久テスト不要の 4 条件。変更固有検証（rg 残件記録）を恒久化しない判断の根拠 |
| Antigravity CLI guide | `docs/cli-guides/antigravity-cli-session-guide.md:41` | `model: gemini-3-pro` の例。Gemini model 名が Antigravity 文脈で正当に残る根拠 |
| starter guide | `docs/guides/python-starter.md:16-19` | 「The starter ... follows in a post-release starter-sync」— starter 記述を本 Issue で書き換えない根拠 |
