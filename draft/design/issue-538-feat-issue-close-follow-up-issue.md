# [設計] follow-up Issue に機械で判定できる「## 確認できる時期」を加える

Issue: #538

## 概要

`issue-close` が起票する follow-up Issue に、確認できる時期を 1 行 1 条件の書式で書く節
`## 確認できる時期` を加える。書式の正本は `docs/dev/workflow_completion_criteria.md` の 1 か所に置き、
issue-create の 4 テンプレートと issue-close の移管手順から参照する。後続の見回り skill（EPIC #535 の子 Issue）が、
時期の来た follow-up だけを確かめられるようにするためのデータ契約を定める。

## 背景・目的

### 現状

- #327 で、`### ワークフロー完了後の確認項目` の未チェック項目を follow-up Issue に移す仕組みが入った。
  現行の `.claude/skills/issue-close/templates/follow-up-issue.md` の節は「親 Issue / 未完了の確認項目 /
  参照 docs / 証跡の記録先 / 完了と close 方針」だけで、**いつ確かめられるか** を書く場所がない。
- 確認には「次の release の後」「別 Issue の close の後」「特定の日付の後」などの待ち条件がある。
  構造化されていないため、保守者は open の follow-up を毎回すべて読んで時期を判断している。
- 利用側 apokamo/kamo2 では同じ節を書式付きで運用し、見回り skill がスクリプトで時期を判定している
  （同 repo `.claude/skills/maintainer-sweep/SKILL.md`「`## 確認できる時期` の書き方」）。

### ユースケース

- 保守者として、時期の来ていない follow-up を読まずに済ませるために、follow-up の本文に確認できる時期を
  書式どおりに書いておきたい。
- Issue の起票者として、`### ワークフロー完了後の確認項目` を書くときに、その確認がいつできるかを
  同じ書式で書きたい。
- （後続）見回り skill として、`## 確認できる時期` の各行を機械で判定し、全行が「来た」follow-up だけを
  確認対象に挙げたい（本 Issue のスコープ外。本 Issue はその入力契約を定める）。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| 時期を自由記述のまま `## 未完了の確認項目` の各行に書く | 機械判定できず、現状の「全件を読む」問題が残る |
| 確認項目ごとに時期を付ける（行単位の注記） | follow-up は項目をまとめて 1 Issue で扱い、close 単位も Issue 単位。判定単位を Issue に揃える方が見回り・close 方針と整合する。kamo2 の運用実績も Issue 単位 |
| GitHub label / milestone で時期を表す | 日付・Issue 番号・release 版をパラメータとして持てない。label は repo 設定に依存する |

## インターフェース

本 Issue のインターフェースは「Markdown 本文の書式（データ契約）」と「skill の手順」である。

### 入力

1. **親 Issue 本文の時期記載（任意）**: 親の `## 完了条件` 末尾サブセクション
   `### ワークフロー完了後の確認項目` の中、確認項目の後に置く小見出し `#### 確認できる時期` と、
   その直下の箇条書き（書式は下記「書式の正本」）。
2. **follow-up テンプレートの新 placeholder**: `[check_timing]`（`## 確認できる時期` 節の本文）。

親 Issue 本文の記載例:

```markdown
## 完了条件

- [ ] workflow 内で確認する条件

### ワークフロー完了後の確認項目

- [ ] v0.25.0 の PyPI 配布物で `kaji --version` が 0.25.0 を返す

#### 確認できる時期

- release: v0.25.0 の後
```

### 書式の正本（`docs/dev/workflow_completion_criteria.md` に新設する節の内容）

`## 確認できる時期`（follow-up）または `#### 確認できる時期`（親 Issue）の直下に、箇条書きで 1 行 1 条件。
複数行はすべてを満たしたとき（AND）に時期が来たとする。行末の全角括弧 `（…）` は理由の注記で、判定には使わない。
待つ条件がなければ `- 即時` を書く。

| 書き方 | 時期が来た条件 |
|---|---|
| `- 即時` | 常に来た |
| `- 日付: YYYY-MM-DD 以降` | 今日 ≥ 日付 |
| `- close: #<n> の close の後` | Issue #n が closed |
| `- release: vX.Y.Z の後` | GitHub Release vX.Y.Z が公開済み（draft ではない）。tag があるだけでは来ていない |
| `- その他: <自由記述>` | 機械では判定しない（人か LLM が本文で判定する） |

表は Issue #538 本文の書式表をそのまま採録する（人間決定。下の「重要判断 provenance」）。正本節には加えて次を書く。

- **`release:` の判定手段（参考）**: `gh release view vX.Y.Z --json isDraft --jq .isDraft` が成功し `false` を返すこと。
  Release が存在しない（tag だけ、または未作成）ときは同コマンドが失敗し、「来ていない」とする。
  prerelease は draft ではないため「公開済み」に含める（人間決定「公開済み（draft ではない）」の文言どおり）。
- **AND と `その他:`**: `その他:` を含む場合、機械判定だけでは「来た」と確定しない（他の行がすべて来た場合に
  人か LLM が判定する）。
- **標準外の書き方**: 表にない書き方（例: kamo2 の `営業日:` / `deploy:`）は kaji 標準では使わない。
  書く場合は `その他:` の自由記述にする。
- **例**（下記）を 1 つ以上載せる。

```markdown
## 確認できる時期

- release: v0.25.0 の後
- close: #546 の close の後（見回り skill の導入後に確かめる）
```

### 出力

1. **follow-up Issue 本文**: `follow-up-issue.md` に `## 確認できる時期` 節（本文 `[check_timing]`）を追加する。
   位置は `## 未完了の確認項目` の直後。節が常に 1 つ存在し、空にならない。
2. **issue-close の移管手順**（`SKILL.md`「共通: ワークフロー完了後の確認項目の移管」）: `[check_timing]` の決め方
   （下記「方針 2」）と、親の `#### 確認できる時期` ブロックを確認項目の抽出対象から外す規則。
3. **issue-create の 4 テンプレート**（feat / bug / refactor / docs）: 本文生成時のヒントに、事後確認項目を書くときは
   `#### 確認できる時期` を添えること・正本への参照・例を追加する。テンプレート本文（コードフェンス内）の既定値
   `- なし` は変更しない（事後確認がないときに時期は不要）。
4. **契約テスト**: 下記「テスト戦略」。

### エラー・境界の挙動（issue-close 移管時）

| 親の状態 | `[check_timing]` の値 / 処理 |
|----------|------------------------------|
| `#### 確認できる時期` があり、全行が正本の書き方に合う | 行をそのまま（注記ごと）転記 |
| 一部の行が正本の書き方に合わない | 合う行はそのまま、合わない行は `- その他: 親の記載「<元の行の本文>」は標準の書き方ではない` に置き換えて転記（情報を失わず、機械判定を人の判断に倒す） |
| `#### 確認できる時期` がない、または箇条書きが 0 行 | `- その他: 親 Issue [issue_ref] に確認できる時期の記載がない。未完了の確認項目から判断する` |
| `#### 確認できる時期` が同じサブセクション内に 2 つ以上 | 既存規則「見出し重複 → ABORT」に含める（親 Issue を close しない） |
| 既存 follow-up を再利用する場合 | 既存本文は変更しない（現行の再利用規則を維持）。時期の追記は行わない |

`- 即時` を既定値にしない理由: 親に記載がないことは「待つ条件がない」ことを意味しない。`即時` にすると、
release 待ちの項目が見回りで「来た」と誤判定される。`その他:` なら人の判断に倒れる（安全側）。

## 制約・前提条件

- **書式の正本は 1 か所**（Issue 完了条件）。正本は `docs/dev/workflow_completion_criteria.md` の新節とし、
  issue-close SKILL.md / follow-up テンプレート周辺 / issue-create テンプレートは同節へのリンクで参照する。
  テンプレートの「例」は正本の複製ではなく使用例であり、契約テストで正本との一致を保証する。
- follow-up テンプレートは「ファイル全体がそのまま本文」（#520 の契約、`tests/test_issue_close_follow_up_template.py`）。
  説明文・コードフェンスをテンプレートに加えない。説明は SKILL.md 側に置く。
- 1 行目の親マーカー `<!-- kaji-follow-up-parent: [parent_issue_ref] -->` と既存 placeholder を維持する。
- 親の `### ワークフロー完了後の確認項目` を更新対象外とする final-check の既存規則は変えない。
  `#### 確認できる時期` は同サブセクション内に置くため、final-check の照合・更新対象に入らない。
- 既存 open Issue / 既存 follow-up の一括移行はしない（既存規約「既存 open Issue の一括移行は行わない」に合わせる）。
- 見回り skill（時期の判定・確認・クローズ）は別の子 Issue（#546）の範囲。本 Issue は判定ロジックを実装しない。
- #522（follow-up 本文にテンプレート見出し等が混ざる不具合）の修正は含めない。
- kaji の release は tag → GitHub Release → PyPI publish の順に進む（`/release` skill）。

## 方針

### 1. 正本節の新設（docs）

`docs/dev/workflow_completion_criteria.md` の「follow-up Issue への移管」節の後に
「確認できる時期の書き方」節を新設し、上記「書式の正本」の内容（表・AND・注記・`即時`・`release:` 判定手段・
標準外の扱い・例）を書く。同ファイルの「workflow 内完了条件と事後確認の分離 > Issue 本文の形式」の例に
`#### 確認できる時期` を追加し、「follow-up Issue への移管」の箇条に「follow-up 本文に確認できる時期を含める」旨を足す。

### 2. issue-close の移管手順

`SKILL.md`「共通: ワークフロー完了後の確認項目の移管」を次のように改める。

- § 1（未完了項目の抽出）: サブセクション内の `#### 確認できる時期` から次の見出しまでの行を確認項目の抽出対象から除く。
  同ブロックの箇条書きを `TIMING_LINES` として取り出す。`#### 確認できる時期` の重複は「見出し重複」として `ABORT`。
- § 3（作成）: placeholder 一覧に `[check_timing]` を追加し、上記「エラー・境界の挙動」の表で値を決める。
  正本に合うかの判定は正本表の書き方（パラメータ部分 `YYYY-MM-DD` / `#<n>` / `vX.Y.Z` / `<自由記述>` を値に置き換えた形、
  行末の全角括弧注記は任意）との照合とする。親の記載から AI が時期を推測して `release:` 等の機械判定できる行を新たに作らない
  （推測した場合は `その他:` に書く）。
- 正本へのリンクを追加し、書式の表は複製しない。

疑似コード:

```text
block = 親サブセクション内の「#### 確認できる時期」ブロック
if block が 2 個以上: ABORT（見出し重複）
lines = block の "- " 行（なければ []）
if lines == []: check_timing = "- その他: 親 Issue <ref> に確認できる時期の記載がない。未完了の確認項目から判断する"
else: check_timing = [l if 正本の書き方に合う(l) else "- その他: 親の記載「<l の本文>」は標準の書き方ではない" for l in lines]
```

### 3. follow-up テンプレート

`## 未完了の確認項目` の直後に次を追加する（説明文なし）。

```markdown
## 確認できる時期

[check_timing]
```

### 4. issue-create テンプレート

4 テンプレートの「本文生成時のチェックポイント」の事後確認項目（共通観点 14）に、事後確認を書くときは
`#### 確認できる時期` を添え、待つ条件がなければ `- 即時` と書くことを追記する。書式は正本へのリンクで示し、
例（正本の書き方に合う 1〜2 行）を載せる。`issue-create/SKILL.md` の該当ヒント（`## 完了条件` の末尾に…の箇条）にも
同じ趣旨と正本リンクを 1 行追加する。

### 5. 契約テスト

新規 `tests/test_check_timing_format.py`（名前は実装時に調整可）に置く。既存
`tests/test_issue_close_follow_up_template.py` は変更せず、引き続き通ることだけを確認する。詳細は「テスト戦略」。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 書式の種類 | `即時` / `日付:` / `close:` / `release:` / `その他:` の 5 種。`営業日:` / `deploy:` は標準に入れない | 人間決定: #538 本文「重要判断」1 件目（保守者の決定 2026-11-09、[保守者コメント](https://github.com/apokamo/kaji/issues/538#issuecomment-6728244845)）。親 #535 の業務依存部分除外 | 表を正本節に verbatim 採録。標準外の書き方は `その他:` に書くと明記 |
| `release:` の成立条件 | GitHub Release vX.Y.Z が公開済み（draft ではない）。tag だけでは来ていない | 人間決定: #538 本文「重要判断」2 件目（判定手段は「設計で決める」と委任） | 判定手段を `gh release view vX.Y.Z --json isDraft` の成功かつ `false` と定めた。prerelease は「draft ではない」に含める（人間決定の文言どおりに解釈。kaji の現行 Release に prerelease 運用はない） |
| 1 行 1 条件・AND・行末全角括弧は注記 | kamo2 の規約を踏襲 | Issue #538 本文の書式説明（「1 行 1 条件、複数行は AND、行末の全角括弧は注記で判定に使わない」） | 正本節に記載 |
| 書式の正本の置き場所 | `docs/dev/workflow_completion_criteria.md` の新節 | AI の仮定（two-way door）。根拠: 同ファイルが既に follow-up 移管の正本で、issue-close SKILL.md から参照済み。後段の検査先: review-design | issue-close / issue-create はリンクで参照し表を複製しない |
| 親 Issue で時期を書く位置 | `### ワークフロー完了後の確認項目` 内、確認項目の後の `#### 確認できる時期` | AI の仮定（two-way door）。根拠: Issue「`### ワークフロー完了後の確認項目` を書くときに同じ書式で書きたい」、final-check が同サブセクション全体を対象外にしているため既存判定に影響しない。後段の検査先: review-design / review-code | follow-up 側の `## ` と区別するため `#### ` とする |
| 親に記載がない・標準外の行があるときの転記 | `その他:` に倒す（`即時` を既定にしない）。AI 推測で機械判定行を作らない | Issue 完了条件「親に書かれていなければ `- 即時` ではなく `- その他:` で根拠を書く等」。標準外行の扱いは AI の仮定（two-way door。follow-up 本文は人が後から直せる）。後段の検査先: review-design | 文言と境界表を定義 |
| 既存 follow-up 再利用時の本文 | 変更しない | AI の仮定（two-way door）。根拠: 現行の再利用規則は本文を書き換えない。後段の検査先: review-design | 境界表に明記 |
| 「今日」のタイムゾーン | 本 Issue では定めない | AI の仮定（two-way door）。根拠: Issue の書式表は「今日 ≥ 日付」のみを定める。判定は #546 の範囲で、TZ を後から定めても既存本文の書き換えは不要（境界が最大 1 日ずれるだけ）。後段の検査先: #546 の設計 | 正本節には書かない |
| 既存 Issue の移行 | しない | 既存規約（`workflow_completion_criteria.md`「既存 open Issue の一括移行は行わない」） | — |

one-way door の未決: なし（データ契約の中身は人間決定済み。残りは後段で安く直せる配置・文言の詳細化）。

## テスト戦略

### 変更タイプ

- skill 手順・テンプレート・docs の変更（instruction-only）。Python 実行時コードの変更はない。
  ただし follow-up テンプレートと書式は後続 skill が機械で読むデータ契約であり、Issue 完了条件が
  契約テストの追加を求めるため、恒久テストを追加する。

### Small / Medium の割り当て

`docs/dev/testing-convention.md` § 判定基準（「DB / ファイル / 内部サービス結合あり → Medium」「それ以外（純粋関数・モック完結） → Small」）に従い、
純粋な解析部分とファイル結合部分を分けて次のように割り当てる。

- **Small（`@pytest.mark.small`）**: 観点 6。正本表の「書き方」セル文字列をパターンに変換する純粋関数と、その自己検査。
  入力は表のセルを模した固定文字列と固定の正常行・異常行だけで、ファイルを読まない
- **Medium（`@pytest.mark.medium`）**: 観点 1〜5。実ファイル（follow-up テンプレート、正本 docs、issue-close SKILL.md、
  issue-create 4 テンプレート）を読む構造検査と、正本と複数テンプレートを照合する文書間検査、`.claude/skills/` と
  `docs/` の走査。観点 3 は観点 6 と同じ純粋関数を使い、ファイルから取り出した表と例を入力にする
- 既存 `tests/test_issue_close_follow_up_template.py`（`small`）は変更しない。既存マーカーの再分類は本 Issue の範囲外とし、
  新規に追加する検査だけを上記の基準で分類する。`[check_timing]` placeholder の存在確認も新規ファイルの観点 1（Medium）で行う

### 契約テストの検証観点


1. **follow-up テンプレートの節**（Medium）: `## 確認できる時期` 見出しが 1 つだけあり、直下が `[check_timing]` であること。
   既存の「1 行目が親マーカー」「説明見出し・フェンスなし」「既存 placeholder 保持」テスト（既存ファイル）が引き続き通ること。
2. **正本の書式表が人間決定どおり**（Medium）: `workflow_completion_criteria.md` の正本節の表から「書き方」列を抽出し、
   ちょうど `- 即時` / `- 日付: YYYY-MM-DD 以降` / `- close: #<n> の close の後` / `- release: vX.Y.Z の後` /
   `- その他: <自由記述>` の 5 種であること（`営業日:` / `deploy:` が無いこと、`release:` があること）。
3. **例が正本と一致**（Medium。文書間照合）: 正本表の「書き方」をパターンに変換し（`YYYY-MM-DD`→日付、`#<n>`→`#数字`、`vX.Y.Z`→
   `v数字.数字.数字`、`<自由記述>`→任意の非空文字列、行末の全角括弧注記を任意で許容）、次の例の各行がいずれかに合うこと:
   - 正本節の例
   - issue-create 4 テンプレートの例
   - `workflow_completion_criteria.md`「Issue 本文の形式」の例
   正本表からパターンを導出することで、テスト側に書式を二重定義しない（正本 1 か所の原則をテストでも守る）。
4. **正本は 1 か所**（Medium。走査）: 書式表のヘッダ行（`| 書き方 | 時期が来た条件 |`）が repo の `.claude/skills/` と `docs/` の中で
   正本ファイルにだけ存在すること。issue-close SKILL.md と issue-create 4 テンプレートが正本節へのリンク
   （`workflow_completion_criteria.md` を含む参照）を持つこと。
5. **issue-close 手順**（Medium）: SKILL.md の移管節に `[check_timing]` と `#### 確認できる時期` の扱い、親に記載がないときは
   `- その他:` に倒すこと（`- 即時` を既定にしないこと）が書かれていること（キーワードの存在確認。#520 の既存テストと同じ粒度）。
6. **パターン変換の自己検査**（Small。純粋関数）: 表のセルを模した固定文字列から作ったパターンについて、 正常行（例: `- release: v0.25.0 の後`、`- 日付: 2026-12-01 以降（月次集計の後）`）が合い、
   異常行（例: `- 営業日: 2026-11-04 から 3 営業日の後`、`- release: 0.25.0 の後`、`- 日付: 2026/12/01 以降`）が合わないこと。

### 追加の Medium テスト

- 観点 1〜5 以外の Medium（DB・サブプロセス・ファイル書込・harness 内部サービスとの結合）は不要。理由: 変更対象は
  リポジトリ内 Markdown だけで、issue-close の移管処理は agent が手順に従って実行する instruction であり、
  harness に結合するコードパスがない。Markdown の実ファイル読込・文書間照合は観点 1〜5 の Medium で網羅する。

### Large テスト

- 不要。理由: 実際に follow-up Issue を GitHub に起票する E2E は外部副作用を伴い、`issue-close` の移管処理は
  agent が手順に従って実行する instruction であり、harness のコードパスではない。手順と書式の内容は Medium の契約テスト（観点 1〜5）と Small の自己検査（観点 6）で、
  起票の実挙動は既存の #327 / #520 の運用経路で担保される。判定ロジック（時期が来たか）の検証は #546 の範囲。

### 実行コマンド

- `source .venv/bin/activate && pytest tests/test_issue_close_follow_up_template.py tests/test_check_timing_format.py -q`
- `make check`（`Makefile` の依存は lint / format / typecheck / validate-workflows / test）。文書リンクの検証は
  pytest 経由の既存 `tests/test_check_doc_links.py::TestRealRepo` で、今回変更する docs / skills を含めて実施される
- `make verify-docs`（独立ターゲット。変更した docs のリンクを直接確認する）

### 長時間 acceptance の配置

- 該当なし。根拠: nested workflow・外部 agent を含む dogfood・fresh 環境の反復 acceptance はない。
  追加する検証は数秒で終わる Small / Medium の契約テストと `make check` / `make verify-docs` のみで、implement step の timeout の 25% を大きく下回る。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/dev/workflow_completion_criteria.md` | あり | 書式の正本節を新設。Issue 本文の形式の例と follow-up 移管の箇条を更新 |
| `.claude/skills/issue-close/SKILL.md` | あり | 移管手順に `[check_timing]` と親の時期ブロックの扱いを追加 |
| `.claude/skills/issue-close/templates/follow-up-issue.md` | あり | `## 確認できる時期` 節を追加 |
| `.claude/skills/issue-create/templates/*.md`（4 件） | あり | 時期の書き方のヒント・正本リンク・例を追加 |
| `.claude/skills/issue-create/SKILL.md` | あり | 事後確認ヒントに正本リンクを 1 行追加 |
| `docs/adr/` | なし | 新しい技術選定はない（既存の Markdown 契約の拡張） |
| `docs/ARCHITECTURE.md` | なし | harness のアーキテクチャ変更はない |
| `docs/reference/` | なし | コーディング規約・API 仕様の変更はない |
| `docs/cli-guides/` | なし | CLI 仕様の変更はない |
| `docs/dev/workflow_guide.md` 等 | なし | skill の役割・フェーズ構成は変わらない（issue-close の責務の範囲内の拡張） |
| `AGENTS.md` / `CLAUDE.md` | なし | 規約変更はない |
| managed starter | なし | `tests/test_starter_skills.py` 等で issue-close / issue-create は starter 配布対象として扱われていない（grep で参照なしを確認） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #538 本文「重要判断」と書式表 | https://github.com/apokamo/kaji/issues/538 | 5 種の書式表、「`営業日:` と `deploy:` は標準に入れない。`release:` を標準に足す」「GitHub Release vX.Y.Z が公開済み（draft ではない）…tag があるだけでは来ていない」「判定の手段: `gh release view vX.Y.Z --json isDraft` などで…（具体的な方法は設計で決める）」 |
| 保守者の決定コメント | https://github.com/apokamo/kaji/issues/538#issuecomment-6728244845 | 「`release:` を kaji の標準の書式に入れる」「成立条件は GitHub Release vX.Y.Z が公開済み（draft ではない）。tag があるだけでは時期が来たとしない」 |
| kamo2 maintainer-sweep の書き方（移植元） | https://github.com/apokamo/kamo2/blob/main/.claude/skills/maintainer-sweep/SKILL.md | 「見出し `## 確認できる時期` の直下に、箇条書きで 1 行 1 条件。複数行はすべてを満たしたとき（AND）…行末の全角括弧 `（…）` は理由の注記で、判定には使わない」。`営業日:` / `deploy:` / `close:` / `日付:` / `即時` / `その他:` の表 |
| kamo2 確認 Issue の生成規約 | https://github.com/apokamo/kamo2/blob/main/docs/dev/workflow_completion_criteria.md | 確認 Issue 本文の必須要素 (7) `## 確認できる時期`「条件が無ければ `- 即時`」。書き方の正本を 1 か所（maintainer-sweep SKILL.md）に置き参照する構成 |
| 現行 follow-up 移管の正本 | `docs/dev/workflow_completion_criteria.md` § follow-up Issue への移管 / § workflow 内完了条件と事後確認の分離 | 未チェック項目だけを follow-up へ移す、テンプレートはファイル全体が本文、既存 open Issue の一括移行は行わない |
| 現行 issue-close 移管手順 | `.claude/skills/issue-close/SKILL.md`「共通: ワークフロー完了後の確認項目の移管」 | § 1 抽出範囲（次の `## ` 見出しまで）・見出し重複は ABORT、§ 3 placeholder 以外の置換をしない |
| 現行 follow-up テンプレートと契約テスト | `.claude/skills/issue-close/templates/follow-up-issue.md`、`tests/test_issue_close_follow_up_template.py` | 節は 5 つで時期の節がない。1 行目親マーカー・説明見出し/フェンスなし・placeholder 保持をテスト済み |
| GitHub CLI `gh release view` | https://cli.github.com/manual/gh_release_view | `gh release view [<tag>]`「View information about a GitHub Release」、`--json` で `isDraft` / `isPrerelease` 等のフィールドを出力できる。存在しない tag では失敗する |
| GitHub REST API: Releases | https://docs.github.com/en/rest/releases/releases | Release は tag と別のリソースで `draft` / `prerelease` 属性を持つ。draft Release は push 権限のある利用者にしか見えない |
| テスト規約 | `docs/dev/testing-convention.md` § 判定基準 | 「外部 API / 実サービス疎通あり → Large / DB / ファイル / 内部サービス結合あり → Medium / それ以外（純粋関数・モック完結） → Small」。新規の実ファイル読込・文書間照合・走査は Medium、固定文字列だけのパターン変換は Small に分類した |
| 重要判断チェックリスト | `.claude/skills/_shared/critical-decision-checklist.md` | 「公開 CLI / API とデータ契約」は one-way door になりやすい判断軸。本 Issue では人間決定済み |
