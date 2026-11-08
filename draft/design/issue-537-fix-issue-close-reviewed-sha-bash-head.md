# [設計] issue-close: 承認 SHA と照合モードを Bash 呼び出しをまたいで失わない

Issue: #537

## 概要

`issue-close` skill の Step 2.5.4（承認証跡と現在の PR の照合）で得た承認 SHA を、シェル変数 `$REVIEWED_SHA` ではなく文脈変数 `[reviewed_sha]` と照合モード `[review_poll_mode]` として記録し、Step 3 はそれを明示的に使う。Step 3 の照合付き merge のブロックは SHA を 40 桁の 16 進数として検証し、空・不正なら merge せずに ABORT する。

## 背景・目的

### Observed Behavior（OB）

- `.claude/skills/issue-close/SKILL.md:430`（2.5.4 のブロック）が `REVIEWED_SHA=$(jq -r '.reviewed_head.sha' "$EVIDENCE")` を代入する。
- `SKILL.md:438`（2.5.5）は「`$REVIEWED_SHA` が設定された場合だけ、Step 3 は `--match-head-commit` 付きで実行する」と書く。`SKILL.md:442-445`（Step 3）は、変数が設定されていれば `kaji pr merge [branch_name] --match-head-commit "$REVIEWED_SHA"` を実行し、なければ照合対象外の `kaji pr merge [branch_name]` を実行する。
- Claude Code / Codex の Bash tool は、呼び出しのたびに新しいシェルを起動する。Issue 本文の実行証跡（2026-11-09、main `9259180`、Claude Code 2.1.319）では、呼び出し 1 で `REVIEWED_SHA=[0123…4567]`（pid 3769774）、呼び出し 2 で `REVIEWED_SHA=[<unset>]`（pid 3770052）になった。本文の分岐どおりに書くと `MOCK kaji [pr] [merge] [fix/999]` が exit 0 で組み立てられ、照合済み（`MODE=verify`）なのに `--match-head-commit` なしで merge するコマンドになる。変数を無条件に渡すと `--match-head-commit ""` になる。

### Expected Behavior（EB）

- review-poll の承認証跡で照合した場合（`MODE=verify`）は、Bash 呼び出しの分け方によらず、必ず `--match-head-commit <承認した 40 桁の SHA>` で merge する。
- 照合した SHA を取得できない（空・40 桁の 16 進数でない・placeholder が未置換）ときは、照合なしの merge に進まずに ABORT する。

EB の根拠: `docs/ARCHITECTURE.md:624-627`（consumer `issue-close` は証跡を照合したうえで `kaji pr merge --match-head-commit <reviewed_head.sha>` を実行し、「証跡の欠落・不一致は PR の現状から補わず停止する」）と Issue #537 本文の EB。

### 再現手順（Issue 本文で固定済み）

1. 前提: review-poll の PASS 証跡がある PR を `issue-close` で close する（`MODE=verify`）。再現では `kaji pr merge` を argv を表示する mock に置き換える。
2. 操作: 2.5.4 の代入と Step 3 の分岐を、別々の Bash tool 呼び出しで実行する。
3. 観測: 2 回目の呼び出しで `$REVIEWED_SHA` が未設定になり、`--match-head-commit` なしの merge コマンドが組み立てられる（上記 OB）。

### 根本原因（Root Cause）

- **なぜ壊れるか**: skill の本文は、別々の bash コードブロックの間で同じシェルの状態が続くことを前提に、Step をまたいでシェル変数 `$REVIEWED_SHA` を受け渡している。Bash tool はこの前提を満たさない（呼び出しごとに別プロセス）。さらに Step 3 の経路選択が「変数が設定されているか」で決まるため、変数が消えると「照合しなかった」経路と区別できず、安全側（停止）ではなく照合なしの merge に倒れる。つまり **(a) 状態の受け渡しをシェルに依存している** ことと **(b) 欠落時の既定が fail-open** であることの 2 点が原因である。
- **いつから**: `c785436`（2026-10-03, feat: bind review-poll PASS to PR head SHA with structured evidence (#429)）で Step 2.5 と `--match-head-commit` を導入したときから。
- **同じ根の他の箇所**: 下の「同根箇所の調査」を参照。

## インターフェース

skill の手順（agent が従う契約）だけを変える。公開 CLI（`kaji pr merge` / `kaji issue resolve-verdict`）、証跡の schema、harness が注入する文脈変数は変えない。

### 入力（変更後に agent が記録・参照する文脈変数）

| 文脈変数 | 値 | 記録する場所 | 使う場所 |
|---------|----|-------------|---------|
| `[review_poll_mode]` | `verify` / `skip` / `manual` | 2.5.1（`[verdict_path]` なしなら `manual`、あれば分類結果の `MODE`） | Step 3 の経路選択 |
| `[reviewed_sha]` | 40 桁の小文字 16 進数 | 2.5.4 のブロックが `REVIEWED_SHA=<sha>` として stdout に出した値 | Step 3 の照合付き merge ブロック |

既存の `[follow_up_issue_id]` と同じく、harness ではなく agent が記録して後の Step で実値に置き換える placeholder である。

### 出力（変更前 / 変更後）

| 箇所 | 変更前 | 変更後 |
|------|--------|--------|
| 2.5.1 | `MODE` をシェル変数に入れるだけ | 分類ブロックの末尾で `echo "MODE=$MODE"` を出し、`[review_poll_mode]` として記録すると書く。`[verdict_path]` なしは `manual` と記録 |
| 2.5.4 | `REVIEWED_SHA=$(jq …)` で終わる | 同じブロックで値を `^[0-9a-f]{40}$` で検証し（不正なら ABORT）、`echo "REVIEWED_SHA=$REVIEWED_SHA"` を出す。本文で `[reviewed_sha]` として記録すると書く |
| 2.5.5 | 「`$REVIEWED_SHA` が設定された場合だけ」 | 「`[review_poll_mode]` が `verify` なら Step 3 は必ず照合付き merge。`[reviewed_sha]` は Step 3 で文脈変数として使い、シェル変数を Step をまたいで参照しない」 |
| Step 3 | `$REVIEWED_SHA` の有無で経路選択 | `[review_poll_mode]` で経路選択（`verify` → 照合付き、`skip` / `manual` → 照合なし、未記録・その他 → ABORT） |
| Step 3 照合付きブロック | `kaji pr merge [branch_name] --match-head-commit "$REVIEWED_SHA"` | 下記の自己完結ブロック |
| status の選択基準 | ABORT 条件に SHA 欠落なし | 「照合した SHA の欠落・不正」を追記 |

### 使用例（Step 3 の照合付き merge ブロック）

```bash
REVIEWED_SHA='[reviewed_sha]'
[[ "$REVIEWED_SHA" =~ ^[0-9a-f]{40}$ ]] \
  || { echo "ABORT: reviewed SHA is missing or invalid: '$REVIEWED_SHA'"; exit 1; }
kaji pr merge [branch_name] --match-head-commit "$REVIEWED_SHA"
```

- `[reviewed_sha]` は agent が 2.5.4 の出力の 40 桁で置き換える。置き換え忘れ（`[reviewed_sha]` のまま）、空、桁数・文字種の誤りはすべて正規表現に合わず、`kaji pr merge` を呼ばずに非 0 で終わる。
- 代入・検証・merge が 1 つのブロックに収まるので、ブロック単位で 1 回の Bash 呼び出しにすれば状態の受け渡しは起きない。

## 制約・前提条件

- Bash tool はブロックをまたいでシェルの状態を保持しない（Issue の実行証跡）。agent の文脈（会話履歴）に残した値は Step をまたいでも失われない。
- `kaji pr merge` は method flag 以外の引数を加工せず `gh pr merge` に渡す（`kaji_harness/commands/pr.py:76-91`）。空の `--match-head-commit` に対する `gh` の挙動は未確認なので、空値を `gh` に渡さないこと自体を保証する。
- 2.5.3 の I3 で `reviewed_head.sha` は既に 40 桁 16 進数と検証済み。2.5.4 の再検証は防御的な重複だが、値を表示して記録させる箇所で形式を確定させるために置く。
- `tests/test_issue_close_evidence_jq.py` は `VERIFIED=$(jq -r '…' "$EVIDENCE")` の形で 2.5.3 の jq プログラムを抽出する。2.5.3 のブロックは変えない。
- `docs/reference/python/` の規約に従うのはテストコードのみ（skill 本文は markdown）。

## 方針

1. **経路選択を文脈変数に移す**: Step 3 の分岐をシェル変数の有無ではなく `[review_poll_mode]` で決める。`verify` 以外の値を明示列挙し、未記録・未知の値は ABORT にする。これで「変数が消えた」と「照合対象外」が区別できる（原因 (b) の解消）。
2. **SHA の受け渡しを文脈変数に移す**: 2.5.4 は SHA を検証して表示し、agent は `[reviewed_sha]` として記録する。Step 3 の照合付きブロックは代入・検証・merge を 1 ブロックに閉じる（原因 (a) の解消）。
3. **最小侵襲**: 2.5.1〜2.5.4 の照合ロジック、jq プログラム、照合対象外の merge コマンド（`kaji pr merge [branch_name]`）は変えない。2.5.1 内の `MODE` のブロック間参照や `$EVIDENCE` の 2.5.2 → 2.5.3/2.5.4 参照は、欠落時に ABORT に倒れる（fail-closed、下記調査）ため今回は変えない。

### 検討した代替案

| 案 | 内容 | 採否 |
|----|------|------|
| A. 2.5.2〜2.5.4 と Step 3 を 1 ブロックに統合 | シェル変数のまま 1 回の Bash で照合から merge まで行う | 不採用。I1〜I9 の大きな jq を含むブロックがさらに肥大し、`tests/test_issue_close_evidence_jq.py` の抽出形と結合する。`skip` / `manual` 経路は別ブロックのまま残るため、経路選択の fail-open（原因 (b)）は解けない |
| B. Step 3 で証跡を再読込して SHA を取り直す | Step 3 で resolve-verdict → 証跡 → jq を再実行 | 不採用。証跡パスも Step をまたぐ値で、同じ受け渡し問題が再帰する。照合ロジックの重複も増える |
| C. 文脈変数 `[review_poll_mode]` / `[reviewed_sha]` に記録（採用） | 値を agent の文脈に残し、Step 3 で実値に置換。merge ブロック内で形式検証 | 採用。Issue 本文が例示する直し方で、利用側 repo（apokamo/kamo2）の対処と同型。kamo2 は経路選択が「`reviewed_sha` が記録されているか」のままで記録漏れ時に照合なしへ倒れうるため、本設計は経路選択も `[review_poll_mode]` に移して閉じる |

## 同根箇所の調査

**方法**: `.claude/skills/**/*.md` の全コードブロックについて、あるブロックで代入した大文字シェル変数を別のブロックが参照している箇所を機械的に列挙した（代入 `NAME=` / `for NAME` / `read NAME` と参照 `$NAME` / `${NAME` の照合。スクリプトは設計作業の一時ファイルで、恒久化しない）。検出は 3 skill のみ。各箇所で「値が空になったとき何が起きるか」を分類した。

| 箇所 | 変数 | 空のときの結果 | 分類 | 本 Issue での扱い |
|------|------|----------------|------|------------------|
| issue-close 2.5.4 → Step 3（`SKILL.md:430` → `:445`） | `REVIEWED_SHA` | 照合なし merge、または `--match-head-commit ""` | **fail-open（本 Issue）** | 修正する |
| issue-close 2.5.1 内の 2 ブロック（`:273` → `:290`） | `MODE` | `case` の `*)` で ABORT | fail-closed | 変えない。Step 3 の経路選択は `[review_poll_mode]` に移す |
| issue-close 2.5.1 → 2.5.2（`:273` → `:307`） | `REVIEW_POLL_VERDICT_PATH` | `dirname ""` = `.` → `./review-poll-evidence.json` が無ければ ABORT | fail-closed（cwd に同名ファイルがある異常時のみ誤読の余地。その場合も 2.5.4 の PR 照合で不一致なら ABORT） | 変えない |
| issue-close 2.5.2 → 2.5.3 / 2.5.4（`:307` → `:324` / `:420`） | `EVIDENCE` | `jq` が空パスで失敗 → ABORT | fail-closed | 変えない |
| issue-close Step 2 → Step 2 / Step 4（`:233` → `:242` / `:462`） | `MAIN_REPO` | `cd ""` は何もしない。`git worktree remove "/../kaji-…"` は失敗 | fail-closed | 変えない |
| issue-close 「ワークフロー完了後の確認項目の移管」2 → 3 / 4（`:141` → `:171`、`:171` → `:194`） | `FOLLOW_UP_TITLE` / `FOLLOW_UP_ID` | `FOLLOW_UP_TITLE` 空は `kaji issue create --title ""` が失敗。**`FOLLOW_UP_ID` 空は親本文に値が空のマーカー `<!-- kaji-follow-up-issue:  -->` を書き、そのまま親 close に進みうる**（追跡先が失われる） | **fail-open（別の不具合）** | 本 Issue の完了条件外。別 Issue での修正を推奨（同じ直し方: `[follow_up_issue_id]` を文脈変数として使い、追記ブロック内で数値を検証）。#538 が同じ節を変更予定のため調整が必要 |
| issue-start Step 1 → Step 2 / 2.5 / 2.6 / 4（`:52` → `:64`〜`:110`） | `BRANCH` / `WT` / `MAIN_REPO` | 最初に参照する `git worktree add -b "" "" main` が失敗 | fail-closed（Step 4 の `prepend-note` に空値が届くのは Step 2 以前を別経路で成功させた場合だけ） | 変えない |
| release Step 1 → Step 6 / 復旧手順（`:53` → `:189` 等） | `GITHUB_REMOTE` | `git push --atomic "" …` などが失敗 | fail-closed | 変えない |

結論: 承認ガードが黙って外れる fail-open は本 Issue の箇所だけ。別系統の fail-open が 1 件（follow-up マーカー）あり、別 Issue での対応を推奨する。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| `MODE=verify` で必ず照合付き merge、SHA 欠落・不正は ABORT | 採用 | Issue #537 本文 EB・完了条件 2〜3（人間決定）。`docs/ARCHITECTURE.md:624-627`（既存契約） | 「不正」を `^[0-9a-f]{40}$` 不一致（空・placeholder 未置換を含む）と定義 |
| 直し方 | 文脈変数への記録（代替案 C） | Issue 本文完了条件 2 が「1 回の Bash で照合と merge」「文脈変数として記録」の両方を例示（人間決定の範囲内）。選択は AI の仮定（根拠: 代替案表）。検査先: review-design | 経路選択も `[review_poll_mode]` に移し、未記録・未知値を ABORT にする |
| 文脈変数名 | `[review_poll_mode]` / `[reviewed_sha]` | AI の仮定。根拠: Issue 本文の例示 `[reviewed_sha]` と既存 `[follow_up_issue_id]` の命名。検査先: review-design / review-code | `[review_poll_mode]` の値域を `verify` / `skip` / `manual` に固定 |
| 同根の fail-closed 箇所を変えない | 変えない | AI の仮定。根拠: 完了条件 5 は「調査結果を書く」で修正は要求していない。欠落時に停止するため安全性の問題ではない。検査先: review-design | 調査表に分類と根拠を記載 |
| follow-up マーカーの fail-open を本 Issue に含めない | 別 Issue を推奨 | AI の仮定。根拠: 完了条件外のスコープ拡大を避ける（`_shared/critical-decision-checklist.md` のスコープ変更軸）。#538 が同じ節を変える。検査先: review-design、人間が別 Issue 化を判断 | 推奨する直し方を調査表に記載 |
| 公開 CLI・証跡 schema | 変えない | Issue 本文（skill 手順の修正に限定、重要判断「該当なし」） | — |

## テスト戦略

### 変更タイプ

- 実行時コード変更なし。agent が実行する手順書（`.claude/skills/issue-close/SKILL.md`）の変更と、その契約テストの追加。手順書は実行される手順なので、既存の skill 契約テストの形式で恒久テストを置く。

### 再現テスト（修正前 Red / 修正後 Green）

新規 `tests/test_issue_close_reviewed_sha.py` に置く。

#### Small テスト（SKILL.md の静的検査）

- **ブロックをまたぐ参照がない**: `#### 2.5.4` から `### Step 4:` までの bash ブロックそれぞれについて、`$REVIEWED_SHA` / `${REVIEWED_SHA` を参照するブロックは同じブロック内で `REVIEWED_SHA=` を代入している。修正前は Step 3 のブロック（`SKILL.md:445`）が代入なしで参照するので FAIL。
- **Step 3 の照合付きブロックが文脈変数を使う**: `--match-head-commit` を含むブロックがちょうど 1 つあり、`REVIEWED_SHA='[reviewed_sha]'` を代入し、`^[0-9a-f]{40}$` の検証と `exit 1` を持つ。修正前は FAIL。
- **経路選択が文脈変数で決まる**: Step 3 本文が `[review_poll_mode]` の `verify` / `skip` / `manual` と、未記録・その他での ABORT を述べ、「`$REVIEWED_SHA` が設定されている場合」の表現を含まない。2.5.1 が `[review_poll_mode]` の記録を、2.5.4 が `[reviewed_sha]` の記録と `echo "REVIEWED_SHA=` を含む。修正前は FAIL。
- **ABORT の記述**: 2.5.5 または Step 3 に「照合した SHA が空・40 桁の 16 進数でない場合は照合なしの merge に進まず ABORT」の記述があり、status の選択基準の ABORT 行に SHA 欠落・不正が含まれる。修正前は FAIL。
- 既存 `tests/test_issue_close_approval_route.py::test_merge_commands_are_unchanged` は `"$REVIEWED_SHA"` の旧コマンド文字列を固定しているため、照合付きコマンドの期待値を `kaji pr merge [branch_name] --match-head-commit "$REVIEWED_SHA"`（ブロック内代入版）に合わせて更新し、照合なしコマンド `kaji pr merge [branch_name]` の不変は維持する。

#### Medium テスト（照合付き merge ブロックの実行）

- SKILL.md の Step 3 から `--match-head-commit` を含む bash ブロックを抽出し、`[branch_name]` → `fix/999`、`[reviewed_sha]` → 各ケースの値に置換して `bash` で実行する。`PATH` 先頭の一時ディレクトリに argv をファイルへ書く `kaji` スタブを置く（実 merge なし、`tmp_path` 内で完結）。
  - 40 桁の正しい SHA: exit 0、スタブの argv が `pr merge fix/999 --match-head-commit <sha>`。
  - 空文字・39 桁・大文字混じり・`[reviewed_sha]` 未置換（置換しないケース）: 非 0 で終わり、スタブが呼ばれていない（argv ファイルなし）、stdout に `ABORT`。
- 修正前のブロック（`kaji pr merge [branch_name] --match-head-commit "$REVIEWED_SHA"`）は、新しいシェルで実行すると `$REVIEWED_SHA` が空のままスタブを `--match-head-commit ""` で呼び exit 0 になるため、異常系ケースが FAIL する（Issue の OB をそのまま再現）。
- Medium を選ぶ理由: サブプロセス（`bash`）とファイル I/O を使う（`docs/dev/testing-convention.md` のサイズ定義）。既存 `tests/test_issue_close_evidence_jq.py` が同じく SKILL.md から抽出したプログラムを subprocess で実行する前例。

#### Large テスト

- 追加しない。実 PR の merge は外部副作用があり、`gh pr merge --match-head-commit` の GitHub 側の拒否は gh / GitHub の既存機能（`https://cli.github.com/manual/gh_pr_merge`）で本 Issue の変更対象外。本 Issue の不具合は skill の手順が `gh` に正しい引数を渡すかで決まり、Medium のスタブで argv を直接検証できる。

### 長時間 acceptance の配置

- 該当なし。追加テストは SKILL.md の読み取りと数回の `bash` 実行のみで数秒以内。nested workflow や外部 agent を含む acceptance はない。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `.claude/skills/issue-close/SKILL.md` | あり | 修正対象（2.5.1 / 2.5.4 / 2.5.5 / Step 3 / status の選択基準） |
| docs/adr/ | なし | 新しい技術選定なし |
| docs/ARCHITECTURE.md | なし | `:624-627` の consumer 契約（照合後に `--match-head-commit <reviewed_head.sha>`、欠落は停止）と整合し、記述の変更は不要 |
| docs/dev/ | なし | `workflow_guide.md:119` の記述（照合して `--match-head-commit` 付きで merge）は不変 |
| docs/reference/ | なし | API・規約の変更なし |
| docs/cli-guides/ | なし | CLI 仕様の変更なし |
| AGENTS.md / CLAUDE.md | なし | 規約の変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #537 本文 | https://github.com/apokamo/kaji/issues/537 | OB・EB・再現手順・2026-11-09 の実行証跡（pid が変わり `REVIEWED_SHA` が `<unset>`、`MOCK kaji [pr] [merge] [fix/999]` が exit 0）・完了条件・回帰テスト方針 |
| issue-close skill（修正前） | `.claude/skills/issue-close/SKILL.md:257-302`, `:418-452`, `:742` | 2.5.1 の `MODE` 分類、2.5.4 の `REVIEWED_SHA` 代入、2.5.5 / Step 3 の変数有無による分岐、ABORT 条件 |
| ARCHITECTURE: review-poll の承認証跡 | `docs/ARCHITECTURE.md:624-627` | 「`kaji pr merge --match-head-commit <reviewed_head.sha>` を実行する。証跡の欠落・不一致は PR の現状から補わず停止する」 |
| `kaji pr merge` の実装 | `kaji_harness/commands/pr.py:76-91` | method flag を除き `--merge` を足すだけで、他の引数は加工せず `gh` に渡す |
| gh pr merge マニュアル | https://cli.github.com/manual/gh_pr_merge | "`--match-head-commit <SHA>`: Commit SHA that the pull request head must match to allow merge" |
| 導入コミット | `c785436`（#429） | Step 2.5 と `$REVIEWED_SHA` の受け渡しを導入 |
| 利用側の対処 | apokamo/kamo2 `.claude/skills/issue-close/SKILL.md`（2.5.5 の「結果を記録」と Step 3） | `reviewed_sha` を記録して Step 3 で `--match-head-commit [reviewed_sha]` を使う。経路選択は「`reviewed_sha` が記録されている場合」 |
| 既存の skill 契約テスト | `tests/test_issue_close_approval_route.py`, `tests/test_issue_close_evidence_jq.py` | SKILL.md を読む静的検査と、SKILL.md から抽出したプログラムを subprocess で実行する Medium テストの前例 |
| テスト規約 | `docs/dev/testing-convention.md` | Small / Medium / Large のサイズ定義 |
