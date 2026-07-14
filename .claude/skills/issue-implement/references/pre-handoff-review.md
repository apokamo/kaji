# Pre-Handoff Review

> 読込タイミング: `/issue-implement` のコミット後。開始時には読まない。
> Baseline Check / Pre-Handoff Review の責務変更や削除は行わず、遅延読込のために本文を分離した。

この reference は `/issue-implement` Step 8.5（`/issue-review-code` への handoff 直前）に「設計書整合・テスト証跡・Scope 混在・auto-close 規約」を第三者視点で検査する。Step 8（コミット）の後に配置されるため、入力に必要な `git diff main...HEAD` と対象 commit hash がいずれも取得可能である。**重複チェックリストは作成しない**。`/issue-review-code` SKILL.md を rubric の単一情報源として参照しつつ、capability に応じて subagent / self-check に分岐する。

#### Step 8.5.1: capability 判定と分岐（1 方式に固定）

main session は以下のフローを実行する。**Agent tool 利用可否の試行を唯一の分岐条件**とする（環境変数や agent context には依存しない）。

1. main session は `kaji-code-reviewer` subagent を **Agent tool で起動** するよう試行する
   - **起動成功**（subagent からの応答テキストが取得できる）→ 経路: `subagent`
   - **Agent tool が利用不可**（Codex / Gemini 等で tool が未定義 / 起動失敗 / `.claude/agents/kaji-code-reviewer.md` が未ロード）→ 経路: `self-check (subagent unavailable, fallback)`

#### Step 8.5.2: 経路別の実行

##### 経路 A: subagent（Claude Code セッション）

1. main session が以下を **事前取得** する（subagent は Bash を持たないため、テキストとして prompt 注入する）:
   - `git diff main...HEAD` の全文
   - 直近 `pytest` の出力（Step 7 を `make check` で実行した場合はその出力の pytest 部分、7a / 7b へ分離実行した場合は 7b の出力）
   - 直近 `ruff check` / `ruff format --check` / `mypy` の出力（Step 7 を `make check` で実行した場合はその出力の該当部分、7a / 7b へ分離実行した場合は 7a の出力）

   Step 7 の出力はいずれの経路でも保持済みであり、証跡取得のために同じコマンドを再実行しない。
   - 最新の `## Baseline Check 結果` コメント（あれば）
   - 対象 commit hash

2. Agent tool で `subagent_type: "kaji-code-reviewer"` を指定して起動する。prompt は以下のテンプレートに沿って組み立てる。

   > **入力契約の SoT**: 各セクション（`## Diff` / `## Test Output` / `## Quality Check` / `## Baseline Failures` / 対象 commit / 設計書パス）の意味と必須/省略可規約は `.claude/agents/kaji-code-reviewer.md` § 入力（prompt 経由で受領）が単一情報源。下記テンプレートはそのミラー — 契約を変更する際は agent markdown 側を先に更新し、本テンプレートを後追いで合わせること。

   ````markdown
   # Pre-Handoff Code Review (kaji workflow)

   - 対象 commit: <git-sha>
   - 設計書: <worktree_dir>/draft/design/issue-<id>-*.md
   - Issue: [issue_ref]

   ## Diff

   ```
   (git diff main...HEAD の全文)
   ```

   ## Test Output

   ```
   (pytest の出力。`make check` 経路ではその出力の pytest 部分)
   ```

   ## Quality Check

   ```
   (ruff check / ruff format --check / mypy の出力。`make check` 経路ではその出力の該当部分)
   ```

   ## Baseline Failures

   (Baseline Check コメント本文を貼り付け。無ければ「無し」と明記)
   ````

3. subagent が返した Markdown 出力を Step 8.5.5 で専用の Issue コメントとして投稿する（main session が投稿経路を担う）。

##### 経路 B: self-check（Codex / Gemini 等の非対応 agent）

1. main session 自身が `.claude/agents/kaji-code-reviewer.md` を `Read` ツールで読み込み、同 markdown 内の rubric を **main session 内で適用** する。
2. 上記 § 経路 A の入力情報（diff / test output / quality check / baseline）を自セッションで参照しながら、`kaji-code-reviewer.md` § 出力形式に沿った Markdown を生成する。
3. 経路情報を `self-check (subagent unavailable, fallback)` と明記して Step 8.5.5 で専用の Issue コメントとして投稿する。

> **判定不能の場合の扱い**: capability 試行が成功も失敗も判別できない（response がタイムアウト等で取得できない）場合は、安全側に倒して経路 B（self-check）を実行する。経路情報には `self-check (subagent ambiguous, fallback)` と明記する。

#### Step 8.5.3: verdict ループと階層分離

subagent / self-check が返す verdict は **pre-handoff の自己評価** であり、kaji workflow の正式 verdict ではない。

| verdict | 取り扱い |
|---------|---------|
| `Yes` | handoff 可。Step 9（実装完了報告）へ進む（実装コミットは Step 8 で確定済み）|
| `With fixes` | main session が指摘事項を反映 → Step 7 の品質チェックを再実行（clean baseline なら `make check`、baseline failure 時は 7a / 7b）→ `git add` で修正をステージ → `git commit --amend --no-edit` で実装コミットを更新 → 本 Step 8.5 を再実行（ループ）|
| `No` | 大幅な修正が必要。main session が修正 → Step 7 の品質チェックを再実行（同上）→ `git add` で修正をステージ → `git commit --amend --no-edit` で実装コミットを更新 → 本 Step 8.5 を再実行 |
| verdict 行が出力されない / parse 不能 | subagent が `maxTurns` 打ち切り等で `Pre-Handoff Review Verdict` セクション、または `Yes` / `No` / `With fixes` のいずれも返さなかった場合。**一度だけ** 経路 B（self-check）に fallback して再実行する。経路情報は `self-check (subagent unparseable, fallback)` と明記。fallback でも parse 不能なら main session 出力の全文を Issue コメントに転記し verdict 欄を `No`（修正必須）扱いとして次のループに進める。**無限再起動を避けるため、subagent 経路への再 fallback はしない** |

> **`--amend` を採る理由**: 実装を単一の実装コミット（Issue type に対応する prefix）に保ち、ループのたびに `git diff main...HEAD` と対象 commit hash が「修正反映後の現在状態」を表すようにする。worktree は未 push の feature branch なので amend は安全。**`--amend` 前の `git add` 必須**: amend はデフォルトで index の内容を使うため、修正を `git add` でステージしないと amend 後のコミットに反映されず、`git diff main...HEAD` と対象 commit hash が修正前を指したまま同じ指摘が再発する。

**ループ制限**: `With fixes` / `No` を 3 回連続で返した場合は abort せず、main session が修正方針を Issue コメントに整理して `/issue-review-code` 側で BACK 相当の判定に委ねる（pre-handoff の自己評価ループでは正式 verdict を出さない）。

**ループ回数の機械的カウント（必須）**: main session が自セッション内のカウンタで自己申告するのではなく、**Issue コメントの `## Pre-Handoff Review` セクション数を実際に数える**。Step 8.5.5 が本 Step 8.5 の実行（＝各ループ試行）ごとに専用の `## Pre-Handoff Review` コメントを投稿するため、Issue コメントが永続的なカウンタとなる。

```bash
PHR_COUNT=$(kaji issue view [issue_id] --comments 2>/dev/null | grep -c '^## Pre-Handoff Review$')
```

- Step 8.5.5 で証跡コメントを投稿した**直後**に `PHR_COUNT` を再取得する。**投稿済み実数 `PHR_COUNT` が 3 以上** かつ 今回の自己評価 verdict が `Yes` 以外 → ループ制限到達。本 Step 8.5 をさらに繰り返さず、以下の申し送りコメントを `kaji issue comment` で投稿してから Step 9 へ進む:

  > **Pre-Handoff Review ループ制限到達**: `With fixes` / `No` が 3 回連続で返されました（Issue コメント上の `## Pre-Handoff Review` 件数 = N）。main session 側の自己評価ループでは収束しないため、`/issue-review-code` 側で BACK 相当の判定を求めます。未解消の指摘事項は最新の `## Pre-Handoff Review` コメント § 指摘事項 を参照してください。

- `PHR_COUNT` が 3 未満かつ verdict が `Yes` 以外 → 通常通り Step 7 の品質チェックに戻ってループを継続

> **趣旨**: ループに陥った当のセッション自身がカウンタを持つと「楽観バイアス」と同型の脆弱性を残す（過去 Issue: history）。Issue コメント側を権威ある回数情報源とすることで、main session の自己申告を経由しないカウントに置き換える。

| 階層 | verdict | 発行者 |
|------|---------|--------|
| 自己評価（本 Step） | `Yes` / `No` / `With fixes` | `kaji-code-reviewer` subagent または main-session self-check |
| 正式 verdict | `PASS` / `RETRY` / `BACK` / `ABORT` | `/issue-review-code`（次セッション推奨） |

#### Step 8.5.4: 出力フォーマット（Step 8.5.5 への引き継ぎ）

subagent / self-check 共通の出力は `.claude/agents/kaji-code-reviewer.md` § 出力形式に従う。経路情報を必ず先頭に明記:

```markdown
## Pre-Handoff Review

- **経路**: subagent / self-check (subagent unavailable, fallback)
- **起動 agent**: kaji-code-reviewer / main-session (capability=<agent_name>)
- **対象 commit**: <git-sha>

### 1. 設計書整合
- 判定: ✅ / ⚠️ / ❌
- 根拠: ...

### 2. テスト証跡
- 判定: ✅ / ⚠️ / ❌
- 根拠: ...

### 3. Scope 混在
- 判定: ✅ / ⚠️ / ❌
- 根拠: ...

### 4. auto-close 規約
- 判定: ✅ / ⚠️ / ❌
- 根拠: ...

### 指摘事項
- 指摘 1: ...
- 指摘 2: ...

### Pre-Handoff Review Verdict
- **Yes** / **No** / **With fixes**
```

> **規約遵守**: 本コメント本文に auto-close hazard pattern（`Clos(e[sd]?|ing)` / `Fix(e[sd]|ing)?` / `Resolv(e[sd]?|ing)` / `Implement(s|ing|ed)?` の直後 `#[0-9]`）を書かない。指摘参照は `指摘 N` / `Must Fix item N` / `point N` 形式に統一する（参照: [`docs/dev/shared_skill_rules.md`](../../../../docs/dev/shared_skill_rules.md) § auto close keyword 回避規約）。

#### Step 8.5.5: Pre-Handoff Review 証跡投稿（MANDATORY）

本 Step 8.5 を実行するたび（＝各 verdict ループ試行ごと）に、その試行で生成した `## Pre-Handoff Review` ブロックを **専用の Issue コメントとして即座に投稿する**。これが PHR 出力の一次投稿経路であり、Step 9（実装完了報告）はこれを再投稿しない。

````bash
kaji issue comment [issue_id] --commit --body "$(cat <<'PHR_EOF'
(Step 8.5.4 のフォーマットで生成した「## Pre-Handoff Review」ブロックを全文貼り付け)
PHR_EOF
)"
````

投稿後、Step 8.5.3 の `PHR_COUNT` 再取得とループ制限判定を行う。1 試行 1 コメントで永続記録されるため、`PHR_COUNT` は within-run のループ試行回数の正しいカウンタとなる。`/issue-review-code` Step 1.4 の hard gate は `## Pre-Handoff Review` セクションの存在を確認するが、本ステップが投稿する専用コメントがこれを満たす。
