# 設計メモ: bugfix-agent-v5 をホストに Phase1/2 を統合（v5-first）

## 背景 / 前提
- v5（`/home/aki/claude/kamo2/.claude/agents/bugfix-v5/`）は動作実績あり
- dao（dev-agent-orchestra）は Phase1/2 を完了済みだが、移植漏れが多発
- 「最小移植」方針は失敗（漏れ多発）
- 以降は「不要なもの以外は全移植」方針で進める

## 目的
- v5 の動作基盤を維持したまま、#1 Phase1/2（core基盤/DesignWorkflow）を v5 に統合
- dao の成果を“互換追加”として v5 に差し込み、動作実績を崩さない
- 将来的に dao 側へ抽出可能な「整理済み基盤」を v5 に整備

## スコープ（移植対象）

### A. Core基盤（Phase1の成果）
1) 設定層統合
- v5: `bugfix_agent/config.py` + `config.toml`
- dao: `src/core/config.py`（pydantic-settings）
- 方針:
  - v5に Settings（pydantic-settings）導入
  - 既存 `get_config_value()` を互換ラッパーとして維持
  - `config.toml` は読み込み互換を保つ（差分追加）

2) IssueProvider強化
- v5: retry付き gh 呼び出し
- dao: URL検証・例外階層（IssueNotFound/RateLimit/Auth）
- 方針:
  - v5 retry は維持
  - dao URL検証と例外階層を追加
  - API互換を維持しハンドラ破壊を避ける

3) Verdict/Abort の一致
- v5: `bugfix_agent/verdict.py`
- dao: `src/core/verdict.py`（3段パース+AI formatter）
- 方針:
  - dao 実装へ v5 を寄せる
  - ABORT時の field抽出拡張（Summary/Reason/Next Action）

4) SessionState APIの統合
- v5: `bugfix_agent/state.py` の SessionState
- dao: `src/workflows/base.py` にある便利API
- 方針:
  - v5 SessionStateを維持しつつ
    - increment/reset/is_loop_exceeded
    - set/get conversation id
    を追加

5) Context構築
- v5: `bugfix_agent/context.py`（build_context）
- dao: allowed_root検証など
- 方針:
  - v5の build_context を dao に近づける（安全対策追加）

### B. DesignWorkflow（Phase2の成果）
1) DesignWorkflow wrapper
- v5 `handlers/design.py`（detail_design系）を流用
- 状態: DESIGN / DESIGN_REVIEW / COMPLETE
- 既存 `load_prompt()` を使用

2) Designプロンプト移植
- `prompts/detail_design.md`
- `prompts/detail_design_review.md`
- #28 で dao に移植済み内容を v5 に反映

3) CLI実行ルート
- v5 orchestrator に design 実行入口を追加
- CLIオプション名は実装時に確定
- bugfix の既存 CLI は壊さない

### C. ログ・実行基盤（維持・必要なら拡張）
- RunLogger は維持（`bugfix_agent/run_logger.py`）
- `cli_console.log` 出力は既存維持
- `format_jsonl_line` の 3ツール対応は v5 既存実装を維持
- dao 側の未実装分は不要

## プロンプト移植チェックリスト（必須）

### detail_design.md
- [ ] 出力形式セクション（マークダウン構造指定）
- [ ] テンプレート変数: `${issue_url}`, `${artifacts_dir}`, `${loop_count}`, `${max_loop_count}`
- [ ] Issue更新方法（Loop=1 vs Loop>=2 の分岐）

### detail_design_review.md
- [ ] 完了条件チェックリスト（4項目の表形式）
- [ ] 禁止事項セクション（次ステート責務の実行禁止）
- [ ] VERDICT出力形式
- [ ] 判定ガイドライン（PASS→IMPLEMENT / RETRY→DETAIL_DESIGN）

### _common.md
- [ ] Output Format (VERDICT)
- [ ] Status Keywords (PASS/RETRY/BACK_DESIGN/ABORT)
- [ ] ABORT Conditions
- [ ] Prohibited Actions
- [ ] Issue Operation Rules
- [ ] Evidence Storage

### _review_preamble.md
- [ ] Review preamble（Devil's Advocate）を review ステートに付与

### _footer_verdict.md
- [ ] VERDICT footer を review/INIT ステートに付与

## 各ステップの完了判定基準

### 1) config統合
- [ ] Settings（pydantic-settings）クラス追加
- [ ] get_config_value() 互換ラッパー実装
- [ ] 既存テスト通過

### 2) errors / providers
- [ ] IssueNotFoundError, RateLimitError, AuthError 追加
- [ ] URL検証ロジック追加
- [ ] 既存 retry 動作維持確認

### 3) verdict
- [ ] 3段パース（strict → relaxed → AI formatter）
- [ ] InvalidVerdictValueError 追加
- [ ] ABORT field抽出拡張

### 4) session state / context
- [ ] increment_loop / reset_loop / is_loop_exceeded
- [ ] set_conversation_id / get_conversation_id
- [ ] build_context の allowed_root 検証と max_chars 制御

### 5) DesignWorkflow wrapper + CLI
- [ ] design 実行入口の追加（CLIオプション名は実装時に確定）
- [ ] handle_design / handle_design_review ハンドラ
- [ ] bugfix CLI 互換維持確認

### 6) prompts
- [ ] 上記「プロンプト移植チェックリスト」全項目

### 7) tests
- [ ] 既存 unit/E2E tests 通過
- [ ] DesignWorkflow unit test 追加

## 設計判断（明記）

| 項目 | 選択肢 | 採用 | 理由 |
|------|--------|------|------|
| ステート名 | `DETAIL_DESIGN` vs `DESIGN` | DESIGN/DESIGN_REVIEW | DesignWorkflow は独立ワークフロー。内部で detail_design ハンドラを流用して互換を確保し、dao Phase2 との整合を優先する。 |
| Issue更新方式 | 本文追記 vs artifacts保存 | 本文追記（v5維持） | v5 の動作実績・運用フローを維持するため。 |
| Session 3原則 | 明示 vs 暗黙 | 明示 | バグ回避のため、session管理ルールを明文化する。 |

### Session 3原則（明示）
1) ロール単位で session_id を保持（analyzer / reviewer / implementer）
2) RETRY 時は同一ロールの session_id を継続
3) フェーズ切替（Design→Implement等）では必要に応じて明示リセット

## 作業順序（依存順）
1) config統合
2) errors / providers
3) verdict
4) session state / context
5) DesignWorkflow wrapper + CLI
6) prompts
7) tests

## テスト・検証
- v5既存ユニット/E2Eが破綻しないこと
- DesignWorkflowの最小動作確認（unit or 小さなE2E）
- run.log / cli_console.log が引き続き出力されること

## 完了条件
- v5 bugfix フロー継続動作
- DesignWorkflow が実行可能
- 設定・IssueProvider・Verdict 仕様が dao と一致
- 主要ログが維持される（run.log / cli_console.log）

## 非スコープ
- bugfix 9ステート再設計（Phase4扱い）
- dao へ直接移植する作業（今回は v5 ホスト）
