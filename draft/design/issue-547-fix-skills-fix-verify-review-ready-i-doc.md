# [設計] fix・verify・review-ready・i-doc 系の判定コメントに verdict marker を付ける

Issue: #547

## 概要

`.claude/skills/` のうち、判定を Issue コメントに投稿する 10 skill（issue-review-ready / issue-fix-ready / issue-fix-design / issue-verify-design / issue-fix-code / issue-verify-code / i-doc-update / i-doc-review / i-doc-fix / i-doc-verify）が、`kaji issue comment` に `--verdict-step` / `--verdict-status` を付けていない。そのため、これらの step の判定コメントには 1 行目の `<!-- kaji-verdict: ... -->` marker が無い。本設計では、10 skill すべての判定コメントに marker を常に付ける。また issue-fix-ready が review-ready の判定を探す方法を、見出しの文字列から marker に置き換える。

## 背景・目的

### Observed Behavior（OB）

worktree `fix/547`（基点 `main` = `5248b3a`）で、各 SKILL.md の `kaji issue comment` と `--verdict-step` の出現数を数えた（`grep -c`）。

| skill | `kaji issue comment` | `--verdict-step` |
|---|---|---|
| issue-fix-code / issue-fix-design / issue-fix-ready | 各 1 | 0 |
| issue-verify-code / issue-verify-design | 各 1 | 0 |
| issue-review-ready | 3（PASS / RETRY / ABORT） | 0 |
| i-doc-update | 1 | 0 |
| i-doc-review / i-doc-fix / i-doc-verify | 0（手順には「Issue にコメントする」としか書いていない） | 0 |
| （比較）issue-review-code / issue-review-design / issue-implement / issue-design | 1 / 2 / 1 / 4 | 2 / 4 / 2 / 5 |

さらに issue-fix-ready の Step 1 は「最新の「レディネスレビュー」コメントから RETRY の指摘事項を抽出する」（`.claude/skills/issue-fix-ready/SKILL.md:52`）と書いており、見出しの文字列でコメントを探している。

marker が無いため、次のことが起きる。

- `kaji issue resolve-verdict <id> --step <step>` が、この 10 step の判定コメントを marker で特定できない（`docs/dev/shared_skill_rules.md` § verdict マーカー契約 の resolve-verdict consumer）。
- runner の comment fallback（`kaji_harness/verdict.py` の `_comment_step_scope_allows`）は、marker の無いコメントを互換のため「どの step の候補にもなる」扱いで残す。そのため、この 10 step の作業報告は、ほかの step の verdict 候補から除外されない。#449 / #453 では、comment fallback が直前の別 step の作業報告を採用し、RETRY が PASS に化けた。

### Expected Behavior（EB）

判定を Issue コメントに投稿するすべての workflow の step で、コメントの 1 行目に `<!-- kaji-verdict: step=<step id> status=<STATUS> -->` が付く。`<STATUS>` は、その skill が `---VERDICT---` で返す status と同じ値にする。根拠は次の 2 つ。

- `docs/dev/shared_skill_rules.md` § verdict マーカー契約: 「producer（判定を発行する skill）: 判定コメント投稿時に `--verdict-step <step> --verdict-status <STATUS>` を **無条件付与**する」
- ADR 008 決定 3（`docs/adr/008-no-backward-compat-layer.md`）: cross-skill 契約は SKILL.md の散文ではなく CLI / harness 層に置く

利用側の repo は、同じ skill に marker を付けて運用している（apokamo/kamo2#1465）。

### 再現手順（Steps to Reproduce）

1. 前提: kaji の worktree（`main` = `5248b3a` 以降で本修正の前）
2. 実行: `grep -c "verdict-step" .claude/skills/issue-verify-code/SKILL.md`
3. 観測: `0` が返る（`issue-review-code` は `2`）。i-doc-review / i-doc-fix / i-doc-verify では `grep -c "kaji issue comment"` も `0` が返る

## 根本原因（Root Cause）

- **なぜ欠けているか**: verdict marker は #261（commit `1d0dfa7`）で導入された。当時の目的は `issue-design` Step 1.6 の BACK 再入検出だったため、marker を付けた producer は「BACK を出しうる、または検出に関わる 5 skill」（review-code / final-check / implement / review-design / design）に限られた。commit メッセージにも「producer 5 skill … が全判定コメントで verdict マーカーを無条件付与」と書かれている。#449（commit `067a217`）は comment fallback に step scoping を入れたが、marker の無いコメントは互換のため候補に残した。そして producer の範囲は広げなかった。そのため、BACK を出さない fix・verify・review-ready・i-doc 系の step は、marker の無いまま残った。
- **いつから**: #261 で marker を導入したとき（`1d0dfa7`）から。i-doc-review / i-doc-fix / i-doc-verify は、それより前から明示の投稿コマンドが無い。
- **issue-fix-ready の見出し検索**: marker 導入より前の書き方が残っている。見出し `## レディネスレビュー` は review-ready の 3 テンプレートで共通なので、PASS / ABORT のコメントや、本文に見出しを引用したコメントとも区別できない。
- **同じ原因で壊れている、スコープ外の箇所**（`.claude/skills/*/SKILL.md` を全数調べた結果）:
  - `i-doc-final-check`（docs 系 workflow の `final-check` step。`BACK: doc-update` を返しうる）: 手順に「Issue に最終チェック結果をコメントする」とだけあり、明示のコマンドも marker も無い
  - `issue-start`（`start` step）: 完了報告のテンプレートだけがあり、投稿コマンドも marker も無い
  - `issue-close`（`close` step）: `kaji issue comment` はあるが marker が無い
  - これらは Issue のスコープ（対象 10 skill を列挙）に入っていない。スコープを広げることは完了条件の変更になるため、本 Issue では扱わない。follow-up Issue #554 として起票済み。
  - `issue-small-change-*` / `incident-*` / `update-starter` 系は、すでに marker を付けている。`kaji-run-verify` は workflow の step ではない（手動検証 skill）ので対象外。

## インターフェース

公開 CLI と harness のコードは変更しない。変更するのは skill（SKILL.md）の手順と、恒久テストの追加だけ。

### 入力

- 各 skill が投稿する判定コメントの本文と、その skill が返す status
- issue-fix-ready の場合は Issue コメント一覧（`kaji issue view <id> --json comments`）

### 出力

- 10 skill の判定コメントの 1 行目に、CLI が `<!-- kaji-verdict: step=<step id> status=<STATUS> -->` を付ける
- issue-fix-ready は、`step=review-ready` の marker を持つコメントのうち最新の 1 件を、指摘の取得元にする

### skill と step id の対応（完了条件 1）

`.kaji/wf/` 配下の全 workflow YAML（official 3 本、custom 6 本）を走査して、`skill:` と `id:` の対応を確認した。どの skill も、すべての workflow で同じ step id に対応している。

| skill | step id | その skill を使う workflow | `<STATUS>` の取りうる値（各 SKILL.md § status の選択基準） | 投稿箇所 |
|---|---|---|---|---|
| issue-review-ready | `review-ready` | dev, docs, dev-small, dev-thorough, dev-thorough-codex, docs-codex, docs-thorough-codex | PASS / RETRY / ABORT | Step 4 の 3 テンプレートすべて |
| issue-fix-ready | `fix-ready` | 上と同じ 7 本 | PASS / ABORT | Step 4 |
| issue-fix-design | `fix-design` | dev, dev-thorough, dev-thorough-codex | PASS / ABORT | 対応報告コメント |
| issue-verify-design | `verify-design` | dev, dev-thorough, dev-thorough-codex | PASS / RETRY / ABORT | Step 3 |
| issue-fix-code | `fix-code` | dev, dev-thorough, dev-thorough-codex | PASS / ABORT | 対応報告コメント |
| issue-verify-code | `verify-code` | dev, dev-thorough, dev-thorough-codex | PASS / RETRY / ABORT | Step 3 |
| i-doc-update | `doc-update` | docs, docs-codex, docs-thorough-codex | PASS / ABORT | Step 7 |
| i-doc-review | `doc-review` | docs, docs-codex, docs-thorough-codex | PASS / RETRY / ABORT | Step 4（新しく明示のコマンドを書く） |
| i-doc-fix | `doc-fix` | docs, docs-codex, docs-thorough-codex | PASS / ABORT | 手順 7（同上） |
| i-doc-verify | `doc-verify` | docs, docs-codex, docs-thorough-codex | PASS / RETRY / ABORT | 手順 6（同上） |

### 使用例

```bash
# 例: issue-verify-code Step 3（RETRY のとき）
kaji issue comment [issue_id] --commit \
  --verdict-step verify-code --verdict-status <STATUS> \
  --body-file - <<'EOF'
## 修正確認結果
...
EOF
# → body 1 行目: <!-- kaji-verdict: step=verify-code status=RETRY -->
```

```bash
# 例: issue-fix-ready Step 1（方針の擬似コード。jq の厳密な形は実装で決める）
# 1. 候補の選択: 1 行目が `<!-- kaji-verdict: step=review-ready `（末尾の空白を含む）で
#    始まるコメントを、すべて候補にする（resolve_latest_verdict の marker_prefix と同じ）。
#    状態や形が不正でも候補から外さない。
# 2. 最新の候補を 1 件だけ取る（last）。
# 3. 最新の候補の 1 行目全体を、markers.py の _VERDICT_MARKER_RE と同じ文法で検証する。
#    文法に合わなければ state=malformed とし、それより古い正常な marker には遡らない。
REVIEW_READY_JSON=$(kaji issue view [issue_id] --json comments | jq -c '
  [ .comments[]
    | select((.body | split("\n")[0]) | startswith("<!-- kaji-verdict: step=review-ready ")) ]
  | last
  | if . == null then {state: "not_found"}
    else (.body | split("\n")[0]) as $l
      | if ($l | test("^<!-- kaji-verdict: step=review-ready status=(PASS|RETRY|ABORT|BACK|BACK_[A-Z0-9_]+)( [a-z][a-z0-9_]*=[A-Za-z0-9][A-Za-z0-9._/-]*)* -->$"))
        then {state: "ok",
              status: ($l | capture("status=(?<s>[A-Z0-9_]+)").s),
              body: .body}
        else {state: "malformed", first_line: $l} end
    end')
# 空文字（kaji / jq の失敗）       → ABORT（fail-loud）
# state=not_found                → ABORT（review-ready の marker が無い。review-ready を再実行する）
# state=malformed                → ABORT（最新の marker が不正。古い RETRY は使わない）
# state=ok かつ status != RETRY   → ABORT（直すべき RETRY が無い）
# state=ok かつ status == RETRY   → body の 2 行目以降から指摘を抽出する
```

## 制約・前提条件

- **CLI の契約は変えない**: `kaji issue comment --verdict-step/--verdict-status` の語彙検証と marker の生成（`kaji_harness/providers/markers.py`）は既存のものを使う。step id はすべて `^[a-z][a-z0-9_-]*$` に合い、status はすべて標準の 4 語彙の中にある。
- **marker は常に付ける**: PASS でも付ける。「RETRY のときだけ」のような条件付きの付与は禁止（`docs/dev/shared_skill_rules.md` の producer 規定）。
- **後方互換のフォールバックは残さない**（ADR 008 決定 1）: issue-fix-ready は、見出し検索への fallback を持たない。marker の無い古い review-ready コメントしか無い場合は ABORT し、review-ready を再実行するよう案内する。これは互換策ではなく、曖昧なときに止まる fail-safe（`shared_skill_rules.md` § 後方互換ルールの対象外規定）である。ただし入力契約の変更なので、ADR 008 決定 2 に従い CHANGELOG の BREAKING で伝達する（方針 5）。
- **既存の BACK 再入検出に影響しない**: 10 skill はどれも `BACK` / `BACK_DESIGN` を返さない。そのため、新しく付く marker が `issue-design` Step 1.6 の計数（`status=(BACK|BACK_DESIGN)` のみを数える）に当たることはない。
- **auto close の規約**: コメントのテンプレートに `Fix #N` などの hazard pattern を書かない（`docs/dev/shared_skill_rules.md` § auto close keyword 回避）。
- **スコープ**: Issue に列挙された 10 skill だけを対象にする。同じ原因で壊れている i-doc-final-check / issue-start / issue-close は follow-up に分ける。
- **starter 側への反映**: managed starter が skill の変更を受け取るのは、Release 後の `/update-starter` である（本 Issue のスコープ外）。

## 変更スコープ

- `.claude/skills/{issue-review-ready,issue-fix-ready,issue-fix-design,issue-verify-design,issue-fix-code,issue-verify-code,i-doc-update,i-doc-review,i-doc-fix,i-doc-verify}/SKILL.md`
- `tests/`（新しい契約テスト 1 ファイル。名前の例: `tests/test_skill_verdict_marker_producers.py`）
- `docs/dev/shared_skill_rules.md`（§ verdict マーカー契約 の「現行 producer」一覧）
- `CHANGELOG.md`（`[Unreleased]`）

`kaji_harness/` のコードは変更しない。

## 方針

1. **投稿コマンドへのフラグ追加（既存コマンドのある 7 skill。review-ready は 3 テンプレート）**: 既存の `kaji issue comment [issue_id] --commit` の直後に `--verdict-step <step id> --verdict-status <STATUS>` を入れる。`--body "$(cat <<'EOF'` の形（issue-fix-code）でも、フラグは body 引数より前に置く。issue-review-ready の 3 テンプレートは status が決まっているので、`--verdict-status PASS` / `RETRY` / `ABORT` と固定で書く。そのほかの skill は `<STATUS>` と書き、置き換えの規則（「この skill が返す status に置き換える」）を 1 文で添える。issue-review-code の Step 3 と同じ書き方にする。
2. **step id は値をそのまま書き、`[step_id]` は使わない**: どの skill もすべての workflow で同じ step id に 1 対 1 で対応しているので、既存の producer（review-code / review-design / implement / design / final-check）と同じく値をそのまま書く。こうすると、`step_id` が注入されない手動実行でも同じ marker が付く。workflow YAML と食い違えば、契約テスト（Medium）が検出する。`[step_id]` は、1 つの skill を複数の step id で使う `issue-small-change-*` だけで使う。
3. **i-doc-review / i-doc-fix / i-doc-verify への明示コマンドの追加**: 「Issue にコメント」という散文を、`kaji issue comment [issue_id] --commit --verdict-step <id> --verdict-status <STATUS> --body-file - <<'EOF' ... EOF` の形のコマンドとテンプレートに置き換える。テンプレートは、既存の散文が求めている内容（review は Must Fix / Should Fix、fix は指摘ごとの対応、verify は指摘ごとの OK / NG）の見出しだけを持つ最小のものにする。末尾には `---VERDICT---` ブロックを付ける（runner の comment fallback が読む作業報告の契約に合わせる）。
4. **issue-fix-ready の取得方法の置き換え**: Step 1-2 を「`step=review-ready` の marker を持つ最新コメントを取る」手順に改める。マッチは 1 行目だけにする（`test()` を `m` フラグなしで使う。`^` が文字列の先頭にだけ合うので、2 行目以降に引用された marker を誤って拾わない）。コメント単位でフィルタする。最新の候補の選び方と検証は、`kaji_harness/commands/issue.py::resolve_latest_verdict` と同じ停止契約にそろえる。
   - **候補**: 1 行目が `<!-- kaji-verdict: step=review-ready `（末尾の空白を含む）で始まるコメント。status の欠落、閉じの ` -->` の欠落、不正な meta があっても候補から外さない。
   - **選択**: 候補のうち最新の 1 件だけを見る。最新の RETRY を探すのではない。そうすると、後から出た PASS や不正な marker より前の古い RETRY を拾ってしまうからである。
   - **検証**: 最新の候補の 1 行目全体を、`providers/markers.py` の `_VERDICT_MARKER_RE` と同じ文法（step / status の語彙、任意の meta、閉じの ` -->`、行末）で検証する。合わなければ `malformed` とし、より古い正常な marker には遡らない（fail-closed）。
   - **停止**: kaji / jq の失敗、marker が無い、最新の marker が不正、最新の status が RETRY でない。この 4 つは理由を分けて ABORT の verdict ブロックを stdout に出し、Issue 本文の修正には進まない（`issue-design` Step 1.6 の fail-loud handler、`shared_skill_rules.md` の「最新 marker 不正は fail-closed」と同じ考え方）。
   - **文法の二重管理への対策**: jq の正規表現は `markers.py` の写しになる。食い違いは Medium テストの差分照合（後述）で検出する。
   - **使わない代案: `kaji issue resolve-verdict --step review-ready` で status を検証してから、別途 jq で本文を取る**。CLI を 2 回呼ぶことになり、その間に新しいコメントが投稿されると、検証した marker と本文が別のコメントになりうる。1 回の `kaji issue view` の結果の中で選択・検証・本文抽出を行う方が、決定的である。
   - **使わない代案: `kaji issue resolve-verdict --step review-ready`**。返すのは `step / status / meta / created_at` だけで、指摘の本文を含まない。marker が無いときは artifact に fallback して本文の無い status を返すので、fix-ready が欲しい「指摘の本文」は得られない。
5. **docs と CHANGELOG**: `shared_skill_rules.md` の「現行 producer」一覧に 10 skill を足す。CHANGELOG の `[Unreleased]` に marker 付与の拡大を書く。issue-fix-ready の入力契約の変更は、ADR 008 決定 2 に従い **BREAKING** セクションに書き、次の 3 要素を明示する（`/release` Step 3 の確認対象）。
   - **壊れる契約**: issue-fix-ready が、marker の無い「## レディネスレビュー」見出しのコメントを入力として受け付けなくなる。最新の `step=review-ready` marker が不正なときも ABORT する。
   - **影響の判定方法**: (a) 途中の Issue: 最新の review-ready 判定コメントの 1 行目が `<!-- kaji-verdict: step=review-ready status=... -->` でなければ影響を受ける。(b) カスタマイズ済みの下流 repo: 自版の issue-review-ready の投稿コマンドに `--verdict-step review-ready --verdict-status` が無い、または自版の issue-fix-ready が見出し検索のままなら影響を受ける。
   - **適用指針**: producer（issue-review-ready）の投稿コマンドに `--verdict-step review-ready --verdict-status <STATUS>` を追加し、consumer（issue-fix-ready）の Step 1 を本変更の版に差し替える（実装 commit / PR を参照先として書く）。途中の Issue は review-ready を再実行して、marker 付きの判定コメントを作り直す。互換のフォールバックは追加しない（ADR 008 決定 1）。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 対象 skill の範囲 | Issue に列挙された 10 skill だけ。同じ原因の i-doc-final-check / issue-start / issue-close は follow-up に分ける | Issue 本文「概要」の対象列挙（人間決定）。スコープを広げることは完了条件の変更（critical-decision-checklist「スコープ変更」軸）なので、AI は選ばない | 全 SKILL.md を調べ、同じ原因の箇所を「根本原因」に記録した |
| marker を常に付ける | PASS を含むすべての status で付ける | Issue 本文「完了条件 2」（「PASS でも付ける」）、`docs/dev/shared_skill_rules.md` の producer 規定、ADR 008 決定 3 | 各 skill の status 集合を対応表にまとめた |
| step id の書き方 | `[step_id]` ではなく値をそのまま書く | AI の仮定。根拠: 全 workflow で 1 対 1 に対応していること、既存の 1 対 1 producer（review-code 等）がそのまま書いていること、手動実行でも同じ値になること。検査先: 契約テスト（Medium）と review-code | YAML と食い違ったときに検出するテストを用意した |
| issue-fix-ready の取得方法 | 最新の `step=review-ready` marker コメントを取り、status が RETRY であることを確かめる。見出し検索への fallback は持たない | Issue 本文「完了条件 4」（人間決定: 見出しから marker へ）。fallback を持たないのは ADR 008 決定 1 による | 最新の RETRY ではなく最新の review-ready の候補を見ること、その候補を完全な marker 文法で検証し、不正なら古い marker に遡らず停止すること（`resolve_latest_verdict` と同じ契約）、失敗を 4 種類に分けて ABORT することを決めた（AI の詳細化。検査先: review-design / review-code、jq の Medium テスト） |
| i-doc 系の明示コマンド | `kaji issue comment` のコマンドとテンプレートを追加する | Issue 本文「完了条件 3」（人間決定） | テンプレートは既存の散文が求める内容の見出しだけの最小構成にした（AI の詳細化。検査先: review-code） |
| CHANGELOG の分類 | fix-ready の入力契約の変更を `[Unreleased]` の BREAKING に書き、3 要素を明示する | ADR 008「コンテキスト」と決定 2（破壊的変更は BREAKING で 3 要素を明示する）、`.claude/skills/release/SKILL.md` Step 3。設計レビューの Must Fix item 2 を受けて、初版の「BREAKING にしない」という AI の仮定を撤回した | 壊れる契約、途中の Issue と下流 repo それぞれの影響判定方法、producer / consumer の更新と review-ready の再実行という適用指針に分けた（方針 5） |

one-way door の未決は無い。Issue 本文「重要判断」も「該当なし（既存の marker の契約を、付いていない skill に広げるだけ。後段の review で直せる two-way door）」としている。

## テスト戦略

### 変更タイプ

- instruction-only（SKILL.md の手順変更）＋ 恒久契約テストの追加。`kaji_harness/` の実行時コードは変更しない。
- bug 固有ルール（修正前に Red になる回帰テストが必須）と Issue 完了条件 5（修正前 FAIL → 修正後 PASS）があるので、恒久テストを追加する。

### Small テスト（SKILL.md のテキスト契約。既存の `tests/test_skill_harness_adaptation.py` と同じく、SKILL.md を読むテストは small とする慣行に従う）

- **marker 指定の存在（OB の回帰テスト）**: 10 skill それぞれで、SKILL.md の `kaji issue comment` 呼び出しが 1 件以上あり、すべての呼び出しが body 引数（`--body` / `--body-file`）より前に `--verdict-step <期待する step id> --verdict-status` を持つ。修正前は、7 skill が「`--verdict-step` が無い」で、i-doc 系の 3 skill が「呼び出しが 0 件」で FAIL する。
- **review-ready の status の固定**: issue-review-ready の 3 テンプレートの `--verdict-status` が `PASS` / `RETRY` / `ABORT` を 1 つずつ持つ（テンプレートと status の取り違えを防ぐ）。
- **issue-fix-ready の見出し検索の撤去**: SKILL.md に「「レディネスレビュー」コメントから」のような見出し検索の指示が無い。`step=review-ready` の marker 照合がある。修正前は FAIL する。

### Medium テスト

- **step id と workflow YAML の一致（完了条件 1 の恒久化）**: `.kaji/wf/**/*.yaml` を読み込み、10 skill それぞれについて、対応する step id の集合がちょうど 1 要素であること、その id が SKILL.md の `--verdict-step` の値と一致することを確かめる。workflow を追加したり step の名前を変えたりしたときに、marker の step と `resolve-verdict --step` が食い違うのを検出する（ファイルを読み YAML を解析するので Medium。既存の `TestFixtureWorkflowParseable` と同じ分類）。
- **issue-fix-ready の jq プログラムの振る舞い**: `tests/test_issue_close_evidence_jq.py` と同じ方式で、SKILL.md から決まった変数名（例: `REVIEW_READY_JSON=$(...)`）を手がかりに jq プログラムを抜き出し、`subprocess` で実際の `jq` に fixture の comments JSON を流す（jq が無い環境では skip）。観点は次のとおり。
  - review-ready の RETRY → PASS → RETRY と並ぶとき、最後の RETRY を選ぶ。RETRY の後に PASS が来るときは、status として PASS を返す（古い RETRY は採らない）
  - 2 行目以降に引用された marker は無視する
  - 別 step の marker（`step=fix-ready` / `step=review-code` 等）は無視する
  - marker の無いコメント（見出し `## レディネスレビュー` だけのもの）しか無いときは `null` を返す
  - meta 付きの marker（`... status=RETRY key=v -->`）でも status を正しく取り出す
  - **不正な最新候補は fail-closed（Must Fix item 1 の回帰）**: 正常な RETRY の後に、次のどれかが最新の候補として来たとき、`state=malformed` を返し、古い RETRY を採らない
    - 閉じの無いもの: `<!-- kaji-verdict: step=review-ready status=RETRY`
    - status の欠落: `<!-- kaji-verdict: step=review-ready -->`
    - 不正な meta: `<!-- kaji-verdict: step=review-ready status=RETRY Key=v -->` など
    - 語彙外の status: `<!-- kaji-verdict: step=review-ready status=retry -->`
  - **`markers.py` との差分照合**: 上の正常 / 不正の 1 行目の fixture をそれぞれ `parse_kaji_verdict_marker` と jq に通し、受理 / 拒否の判定と status が一致することを確かめる（`tests/test_issue_close_evidence_jq.py` が producer model と jq を差分照合するのと同じ考え方）

### Large テスト

- 追加しない。理由: 実行時コードは変更しない。CLI が marker を生成し、step scoping と `resolve-verdict` がそれを読む部分は、既存の `tests/test_cli_main.py`（`--verdict-step/--verdict-status` の Medium）、`tests/test_verdict_marker.py`、`tests/test_resolve_verdict.py`、`tests/test_verdict_step_scoping.py` で確かめている。実際の agent で workflow を流すテストは非決定的で費用が大きい。また、本変更が導入する回帰の形（skill の記述からフラグが落ちること）は Small / Medium の契約テストで決定的に検出できるので、得られる情報はほとんど増えない。

### 変更固有の検証

- `source .venv/bin/activate && make check`（Issue 完了条件 6）
- `make verify-docs`（`shared_skill_rules.md` / SKILL.md のリンクの整合）
- 修正前 FAIL の証跡: 新しいテストを先に commit するか、修正前の HEAD で実行して FAIL を記録し、修正後に PASS を記録する（implement の報告に残す）

### 長時間 acceptance の配置

- 該当なし。理由: nested workflow、外部 agent の dogfood、fresh 環境での繰り返し acceptance のどれも無い。検証は pytest（Small / Medium、数秒）と `make check` だけなので、implement step の timeout（3600 秒）の 25% には遠く届かない。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定は無い。ADR 008 決定 3 の範囲での適用拡大 |
| docs/ARCHITECTURE.md | なし | harness の verdict 判定機構は変わらない（marker の無いコメントは候補に残すという記述も変わらない） |
| docs/dev/shared_skill_rules.md | あり | § verdict マーカー契約 の「現行 producer」一覧に 10 skill を足す |
| docs/dev/skill-authoring.md | なし | producer / consumer 契約の一般原則は変わらない |
| docs/dev/workflow_guide.md / development_workflow.md | なし | step の構成と遷移は変わらない |
| docs/reference/ | なし | API や規約は変わらない |
| docs/cli-guides/ | なし | CLI の仕様（`--verdict-step` / `--verdict-status`）は変わらない |
| AGENTS.md / CLAUDE.md | なし | 規約は変わらない |
| CHANGELOG.md | あり | `[Unreleased]` に marker 付与の拡大を書く。fix-ready の入力契約の変更は BREAKING セクションに 3 要素で書く（方針 5） |

## 同じ原因の follow-up

スコープ外の i-doc-final-check / issue-start / issue-close の marker 欠落は、follow-up Issue #554 として起票済み。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| verdict marker の生成と語彙検証（契約の正本） | `kaji_harness/providers/markers.py` | `build_kaji_verdict_marker` が `<!-- kaji-verdict: step=<step> status=<STATUS> -->` を作る。step は `^[a-z][a-z0-9_-]*$`、status は `PASS|RETRY|ABORT|BACK|BACK_[A-Z0-9_]+` |
| comment fallback の step scoping | `kaji_harness/verdict.py` `_comment_step_scope_allows` | 「marker が別 step を指す comment は除外。marker 無しは後方互換で候補に残す」。そのため marker の無い step の作業報告は、ほかの step の候補から外れない |
| producer / consumer 契約 | `docs/dev/shared_skill_rules.md` § verdict マーカー契約 | producer は `--verdict-step/--verdict-status` を無条件付与する。条件付きの出力は禁止。resolve-verdict consumer は対象 step の最新の marker コメントを選ぶ |
| ADR 008 | `docs/adr/008-no-backward-compat-layer.md` | 決定 1: 後方互換のフォールバックを残さない。決定 2: 破壊的変更は BREAKING で 3 要素（壊れる契約 / 影響の判定方法 / 適用指針）を明示する。決定 3: cross-skill 契約は CLI / harness 層に置く |
| 最新 marker の解決と fail-closed | `kaji_harness/commands/issue.py::resolve_latest_verdict` | `step=<step> ` で始まる最新の候補を選び、`parse_kaji_verdict_marker` で 1 行全体を検証する。不正なら `VerdictMarkerMalformedError` を出し、古い marker には遡らない |
| BREAKING 3 要素の確認 | `.claude/skills/release/SKILL.md` Step 3 | release のとき、CHANGELOG の BREAKING が 3 要素を満たすかを確かめる |
| ADR 005 追記（#449） | `docs/adr/005-artifact-primary-verdict.md` | 別 step の marker を持つ comment を候補から除外し、marker の無い comment は残す |
| marker 導入の範囲 | commit `1d0dfa7`（#261） | 「producer 5 skill（review-code / final-check / implement / review-design / design）」に限って導入した |
| comment fallback の取り違え | Issue #449 / #453 | 「resolve_verdict の comment fallback が直前の別 step の作業報告コメントを採用し、exec step の RETRY を PASS に化けさせる」 |
| workflow の step id | `.kaji/wf/official/{dev,docs}.yaml`、`.kaji/wf/custom/dev/{dev-small,dev-thorough,dev-thorough-codex}.yaml`、`.kaji/wf/custom/docs/{docs-codex,docs-thorough-codex}.yaml` | 対応表の step id は、すべての workflow で同じ値 |
| 既存 producer の書き方 | `.claude/skills/issue-review-code/SKILL.md` Step 3、`.claude/skills/issue-design/SKILL.md` Step 1.6 | step id をそのまま書き、`<STATUS>` を置き換える書き方。1 行目だけに合わせ、コメント単位でフィルタし、失敗時は fail-loud で ABORT する jq の書き方 |
| SKILL.md の jq を抜き出してテストする前例 | `tests/test_issue_close_evidence_jq.py` | SKILL.md から jq プログラムを正規表現で抜き出し、実際の jq で fixture を判定する Medium テスト |
| テストサイズの規約 | `docs/dev/testing-convention.md` | ファイルを読むものは Medium、純粋なロジックは Small。bug 修正では Red → Green の回帰テストが必須（`.claude/skills/_shared/design-by-type/bug.md`） |
| CLI ガイド | `docs/cli-guides/github-mode.md` § 2.1 | `kaji issue comment --verdict-step/--verdict-status` が body 1 行目に marker を付ける。不正な値は exit 2 |
| 利用側の実装 | apokamo/kamo2#1465 | 同じ skill（issue-verify-code ほか）に marker を付けて運用している（Issue 本文で人間が提示した参考） |
