# [設計] verdict の出力例を YAML として壊れない書き方（`|` ブロック）に揃える

Issue: #536

## 概要

`kaji_harness/prompt.py` が全 step の prompt に埋め込む verdict の出力例（delimiter block と pure YAML の例）が、
そのまま写すと `yaml.safe_load` で読めない、または値に `: `・` #` を含むと壊れる書き方になっている。
出力例と `verdict.py` の formatter の例を、`reason` / `evidence` / `suggestion` を常に YAML の literal block scalar（`|`）で
書く形に揃え、必須注記は YAML の値の外に出す。

## 背景・目的

### Observed Behavior（OB）

`kaji_harness/prompt.py:156-168`（`build_prompt` の「出力要件」節。HEAD `1067b1e`）:

```text
---VERDICT---
status: {status_choices}
reason: "判定理由"
evidence: |
  具体的根拠（複数行可。抽象表現禁止）
suggestion: "次のアクション提案"（ABORT/BACK時必須）
---END_VERDICT---

3. 最後に、同じ内容の pure YAML を `{verdict_target}` に保存する（`---VERDICT---` delimiter は付けない）:
   status: {status_choices} のいずれか
   reason: 判定理由
   evidence: 具体的根拠（複数行可。抽象表現禁止）
   suggestion: 次のアクション提案（ABORT/BACK 時必須）
```

- delimiter block の `suggestion` 行は閉じ引用符の後に `（ABORT/BACK時必須）` が続き、例そのものが YAML として不正
  （`yaml.safe_load` → `ParserError while parsing a block mapping`）。
- pure YAML の例は plain scalar（引用符なし）。値に `: ` があると `ScannerError mapping values are not allowed here`、
  ` #` があると以降がコメント扱いで黙って切り詰められる。注記 `（ABORT/BACK 時必須）` も値の一部に見える。
- `kaji_harness/verdict.py:45-58` の `FORMATTER_PROMPT` の例は `reason: "判定理由"` 等のダブルクォート。`: ` と ` #` は保持されるが、
  値の中の `"` は `ParserError`、`\`（例 `C:\work`）は無効エスケープで `ScannerError`、改行は空白へ畳まれて内容が変わる。
  formatter は agent の任意の出力を整形し直す経路なので、これらの文字は通常入力である。

実障害: apokamo/kamo2#1703 で `VerdictParseError: YAML parse error in verdict block: mapping values are not allowed here`
により正しい ABORT が失われた。利用側は skill 共通規約（apokamo/kamo2#2092: 値は `|` ブロックか `"..."`、保存前に
`yaml.safe_load` で確認）で回避している。

### Expected Behavior（EB）

Issue #536 本文の EB を正とする:

- harness が示す出力例を、そのまま写しても YAML として正しく読める。
- 値に `: `・` #`・引用符・`\`・複数行を含んでも、例の書き方に従えば意味が変わらない（すべての値を `|` ブロックで書く）。
- 必須の注記（ABORT/BACK のときは suggestion が必須）は YAML の値の外に書く。

裏付け: YAML 1.2 仕様の literal block scalar（§8.1.2）は内容をエスケープ解釈せず、改行を保持し、
`#` / `: ` / 引用符を内容として扱う。PyYAML（kaji の依存。`yaml.safe_load`）での実測も下記「再現手順」のとおり。

## 再現手順（steps-to-reproduce）

前提: kaji main（`1067b1e`。`prompt.py` / `verdict.py` は `158f21c` から変更なし）の `.venv`。

1. `source .venv/bin/activate`
2. 次を `yaml.safe_load` に渡す:
   - `'status: PASS\nsuggestion: "次のアクション提案"（ABORT/BACK時必須）'` → `ParserError`
   - `'status: PASS\nreason: 判定理由: 根拠あり'` → `ScannerError`
   - `'status: PASS\nreason: 修正済み #12 を参照'` → `{'reason': '修正済み'}`（切り詰め）
   - `'status: PASS\nreason: "設定値 "timeout" を修正"'` → `ParserError`
   - `'status: PASS\nreason: "パス C:\\work\\x を参照"'` → `ScannerError`
   - `'status: PASS\nevidence: "1行目\n  2行目"'` → `'1行目 2行目'`（改行消失）
3. 設計段階の追加確認（本設計で採用する書き方。2026-11-08 実測）:
   - `status: PASS | ABORT\nreason: |\n  判定理由\nevidence: |\n  具体的根拠（…）\nsuggestion: |\n  次のアクション提案\n` → 4 キーの mapping
   - `status: ABORT\nreason: |\n  設定値 "timeout" を修正: 根拠あり #12\nevidence: |\n  C:\work\x\n  2行目: a #b\nsuggestion: |\n`
     → `reason` / `evidence` は全文・改行を保持、空の `suggestion: |` は `''`

## 根本原因（Root Cause）

- **なぜ壊れているか**: 出力例が「人間向けの説明文」と「YAML の値」を区別せずに 1 行へ混在させている（`"…"（注記）`）。
  さらに値の書き方として plain scalar / double-quoted scalar を例示しており、どちらも YAML の特殊文字
  （plain: `: `・` #`、double-quoted: `"`・`\`・改行の畳み込み）に対して脆い。agent は例を写すため、例の脆さがそのまま
  verdict の脆さになる。パーサ（`_parse_yaml_fields` → `yaml.safe_load`）は仕様どおりに動作しており、原因は例の側にある。
- **いつから**:
  - delimiter block の `suggestion: "次のアクション提案"（ABORT/BACK時必須）` と `reason: "判定理由"`:
    `465b380`（#57、dao_harness 初期実装、2026-03-10）から存在。
  - pure YAML の plain scalar 例: `f011d19`（#220、artifact-primary verdict 導入、2026-06-04）で追加。
  - `FORMATTER_PROMPT` のダブルクォート例: `465b380` / `eaa08d7`（#77、3-stage fallback parser）由来。
- **同根の他箇所の調査結果**: 下記「同根箇所の調査」を参照。

### 同根箇所の調査

調査方法: worktree 内の `.claude/skills/` / `docs/` / `kaji_harness/` 配下で `---VERDICT---` を含む全ファイルから
`---VERDICT---` 〜 `---END_VERDICT---` の block を抽出し、`reason` / `evidence` / `suggestion` 行の値の書き方を分類した
（一時スクリプト。恒久化しない）。加えて delimiter 外の pure YAML 例（`status:` 直後に plain scalar の `reason:` 等が続く箇所）を grep した。

| 箇所 | 書き方 | 判定 | 本 Issue での扱い |
|------|--------|------|------------------|
| `kaji_harness/prompt.py:159-161`（delimiter block） | `"…"` ＋閉じ引用符後の注記 | 例自体が YAML 不正（根本原因） | 修正 |
| `kaji_harness/prompt.py:164-168`（pure YAML 例） | plain scalar ＋値内の注記 | `: ` / ` #` で破損 | 修正 |
| `kaji_harness/verdict.py:54-56`（`FORMATTER_PROMPT`） | `"…"` | `"` / `\` / 改行で破損 | 修正（`|` に統一） |
| `docs/dev/skill-authoring.md:215-220`（`verdict.yaml` の例） | plain scalar | 例の値自体は読めるが、skill 作者に脆い書き方を教える | 修正（`|` に統一） |
| `docs/dev/skill-authoring.md:246-248`（「YAML block scalar の利用」節の例） | `reason` のみ plain scalar | 同上 | 修正（`|` に統一） |
| `.claude/skills/issue-implement/templates/implement-report.md:67,70` | `reason: "(判定理由)"` / `suggestion: "(…)"` | `"` / `\` / 改行で破損しうる | 修正（`|` に統一） |
| `.claude/skills/*/SKILL.md` の verdict 例（計 138 フィールド） | すべて `|` block | 問題なし | 変更なし |
| `docs/dev/skill-authoring.md:190-201`（verdict 出力規約のテンプレート） | `|` block | 問題なし | 変更なし（規約文を 1 行追加） |
| `kaji_harness/scripts/*.py` / `runner.py` / `errors.py` | verdict を **生成** する側は `write_verdict_yaml`（`yaml.safe_dump`）経由、または例示なし | 問題なし | 変更なし |

`prompt.py:120` の `previous_verdict` 注入（`reason: {v.reason}` 等）は prompt 内の参考情報で、YAML として解析されない
（`build_prompt` の出力をパーサへ渡す経路は無い）ため対象外。

## インターフェース

公開 IF（関数シグネチャ、CLI、`verdict.yaml` のスキーマ、パーサの受理範囲）は変更しない。
変わるのは agent / formatter LLM に提示する **prompt 文面** のみ。

### 入力

- `build_prompt(...)`: 引数不変。
- `FORMATTER_PROMPT.safe_substitute(raw_output=..., valid_statuses_str=...)`: placeholder 不変（`$raw_output` / `$valid_statuses_str`）。

### 出力（変更後の prompt 文面。文言の最終形は実装時に調整可）

`build_prompt` の「出力要件」節:

```text
---VERDICT---
status: {status_choices}
reason: |
  判定理由
evidence: |
  具体的根拠（複数行可。抽象表現禁止）
suggestion: |
  次のアクション提案
---END_VERDICT---

書き方の規則（YAML の外の注記）:
- status は {status_choices} のいずれか 1 つだけを書く
- reason / evidence / suggestion は常に `|` の後で改行し、各行を同じ 2 スペースで字下げして書く。
  `: `・`#`・引用符・`\`・改行を含んでもエスケープ不要
- status が ABORT / BACK 系のときは suggestion が必須（空不可）。それ以外で不要なら `suggestion: |` の後を空にしてよい

3. 最後に、上の block から `---VERDICT---` / `---END_VERDICT---` の 2 行を除いた同じ内容を、pure YAML として `{verdict_target}` に保存する:

status: {status_choices}
reason: |
  判定理由
...（delimiter block の本文と同一）
```

`FORMATTER_PROMPT` の出力フォーマット節:

```text
---VERDICT---
status: <$valid_statuses_str のいずれか1つ>
reason: |
  判定理由
evidence: |
  判定根拠
suggestion: |
  次のアクション提案
---END_VERDICT---

reason / evidence / suggestion は必ず `|` ブロックで書き、各行を 2 スペースで字下げしてください。
入力の文言（引用符・バックスラッシュ・改行・コロン・# を含む）はエスケープせず、そのまま写してください。
```

`---NO_VERDICT_FOUND---` sentinel の節と `status` 制限の注意文は維持する。

### 使用例

agent が例に従って書いた verdict（値に特殊文字を含む）:

```yaml
status: ABORT
reason: |
  設定値 "timeout" を修正: 根拠あり #12
evidence: |
  C:\work\x を参照
  2 行目: pytest 3 failed
suggestion: |
  前提を人間が決めてから再実行する
```

→ `parse_verdict` / `parse_verdict_block` / `load_verdict_yaml` / `load_verdict_yaml_for_marker_vocabulary` のいずれでも
各値が（前後空白の `strip()` を除き）そのまま保持される。

## 変更スコープ

| ファイル | 変更 |
|----------|------|
| `kaji_harness/prompt.py` | 出力要件節の delimiter block / pure YAML 例 / 注記を差し替え |
| `kaji_harness/verdict.py` | `FORMATTER_PROMPT` の出力フォーマット例と書き方注記 |
| `docs/dev/skill-authoring.md` | § verdict 出力規約: `verdict.yaml` 例と block scalar 節の例を `|` に統一、規約文 1 行追加 |
| `.claude/skills/issue-implement/templates/implement-report.md` | verdict 例の `reason` / `suggestion` を `|` に統一 |
| `tests/test_prompt_builder.py` / `tests/test_verdict_parser.py` / `tests/test_verdict_artifact.py` / `tests/test_artifact_verdict.py`（または同等の既存ファイル） | 回帰テスト追加 |

パーサ（`_parse_yaml_fields` / `_parse_relaxed_fields` 等）は変更しない。

## 制約・前提条件

- パーサの受理範囲を広げる・緩める変更（例: 閉じ引用符後の文字を許す前処理）は行わない。原因は例の側にあり、
  パーサ側で救うと「壊れた YAML を受理する」暗黙契約が増える（ADR 008 の後方互換レイヤ不採用方針とも整合）。
- 既存の `"..."` / plain scalar で書かれた verdict は従来どおり受理される（パーサ不変のため互換性影響なし）。
- `status` 行は `{status_choices}`（例 `PASS | ABORT`）の表記を維持する。既存テスト `TestPromptContainsValidStatuses`
  （status 名の包含確認）と prompt の status 候補提示を崩さない。`status: PASS | ABORT` は plain scalar として YAML 上は有効。
- `string.Template` の placeholder（`$raw_output` / `$valid_statuses_str`）を壊さない。例中に `$` を追加しない。
- Python 実装は `docs/reference/python/` の規約に従う（f-string 内の `{}` エスケープに注意）。

### block scalar の既知のエッジケース（本 Issue の扱い）

| ケース | YAML 上の挙動 | 扱い |
|--------|---------------|------|
| 値の 1 行目が以降の行より深く字下げされている | 字下げ量は最初の非空行で決まり、以降の浅い行で `ScannerError` | 注記「各行を同じ 2 スペースで字下げ」で回避を指示。パーサは不変（失敗は従来どおり fail-loud） |
| 値の中に `#`・`: `・`"`・`\` | すべて内容として保持 | 本 Issue の EB。テスト 3・4 で検証 |
| `suggestion: |` の後が空 | 空文字 `''` | PASS / RETRY で suggestion 不要時の書き方として注記に記載。ABORT / BACK 系は既存 `_require_suggestion` が fail-loud |
| 値の中に `---END_VERDICT---` 行 | delimiter 抽出（`STRICT_PATTERN`）が先に切る | 既存の delimiter 契約の制約で本 Issue の対象外（pure YAML の artifact 経路には影響しない） |

## 方針（修正アプローチ）

1. **単一情報源化（prompt.py）**: delimiter block の本文と pure YAML 例を、同一の文字列（モジュール内のヘルパ関数、
   例 `_verdict_example_yaml(status_choices: str) -> str`）から生成し、2 箇所の書き方が再び乖離しないようにする。
   注記は block の外の箇条書きに移す。
2. **formatter の例を同じ書き方へ**: `FORMATTER_PROMPT` の 3 行を `|` block に置き換え、エスケープ不要・字下げの注記を足す。
   formatter は別モジュールの `Template` で、`prompt.py` と共有する必然性は薄い（status 行の表記も異なる）ため、
   文字列共有はせず、テストで両者が `yaml.safe_load` 可能かつ 3 フィールドが block scalar であることを担保する。
3. **docs / skill テンプレート**: 同根調査で見つけた plain / double-quoted の例を `|` に揃え、
   `skill-authoring.md` § verdict 出力規約に「`reason` / `evidence` / `suggestion` は `|` block scalar で書く」を明記する。
4. リファクタは混在させない（prompt.py の他節、パーサには触れない）。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 例の書き方を何にするか | 3 フィールドとも `|` literal block scalar | Issue #536 本文 EB「例: すべての値を `|` のブロックで書く」、完了条件「`prompt.py` と同じく `"`・`\`・改行にも耐える書き方に揃える」 | `|` を採用（`|-` や `>` は不採用: `>` は改行を畳む、`|-` は `strip()` 済みのため差が無く表記が増えるだけ） |
| 必須注記の置き場所 | YAML 値の外の箇条書き | Issue #536 本文 EB「必須の注記は、YAML の値の外に書く」 | ABORT / BACK 系で suggestion 必須、不要時は空可、の 2 点を注記として記載 |
| パーサを変えるか | 変えない | AI の仮定。根拠: パーサは YAML 仕様どおりで、OB の原因は例側（根本原因節）。完了条件もパーサ変更を求めていない。後段の検査先: `/issue-review-design` | 受理範囲は不変、既存 verdict の互換性影響なし |
| 同根箇所（`skill-authoring.md` / `implement-report.md`）を本 Issue で直すか | 直す（`|` に統一） | AI の仮定。根拠: 完了条件は「調査結果を設計書に書く」のみだが、同じ契約の例で修正コストが小さく、two-way door（docs / template 文面）。Issue 本文「重要判断: 該当なし（two-way door）」と整合。後段の検査先: `/issue-review-design`（スコープ過大なら除外可） | 修正対象を表で列挙し、SKILL.md 本体（すでに `|`）は対象外と明記 |
| prompt.py の 2 例を単一情報源にするか | ヘルパ関数で共有 | AI の仮定。根拠: 乖離（delimiter block は `"…"`、pure YAML は plain）が本バグの一因。内部実装の詳細で two-way door。後段の検査先: `/issue-review-design` / `/issue-review-code` | formatter とは共有しない（status 行表記が異なるため） |

one-way door の未決は無い: 公開 CLI / `verdict.yaml` スキーマ / パーサ受理範囲は不変で、prompt 文面は後段で安く直せる
（Issue #536 本文「重要判断」節、レディネスレビュー PASS コメントの観点 15 と同旨）。

## テスト戦略

### 変更タイプ

- 実行時コード変更（agent / formatter へ渡す prompt 文字列の変更。パーサは不変）

### 実行時コード変更の場合

#### Small テスト

1. **出力例の YAML 妥当性（再現テスト・修正前 Red）** — `tests/test_prompt_builder.py`
   - `build_prompt(...)` の出力から `STRICT_PATTERN`（`kaji_harness.verdict`）で delimiter block の本文を抽出し、
     `yaml.safe_load` が `dict` を返し、キーが `status` / `reason` / `evidence` / `suggestion` であること。
     修正前は `ParserError` で FAIL（OB の delimiter block の再現）。
   - pure YAML 例（「出力要件」節の手順 3 以降に現れる例）を抽出し、同様に `yaml.safe_load` で 4 キーの mapping になること。
     注記文言（`ABORT/BACK`）が `suggestion` の値に含まれないこと。修正前は `ScannerError` または
     `suggestion` 値に注記混入で FAIL。抽出方法は実装時に決めるが、ヘルパ関数の戻り値が prompt 中に 2 回現れることの assert でもよい。
   - 例の `reason` / `evidence` / `suggestion` が block scalar で書かれていること（各キー行が `<key>: |` であること）。
     これは「`: `・`#` 等を含む値でも壊れない書き方を示している」ことの構造的保証で、`"…"` 例への後退を検出する。
   - `verdict_path` あり / なし（`[verdict_path]` placeholder）の両方、複数 status 構成（`PASS | ABORT`、`PASS | RETRY | BACK`）で成立すること（parametrize）。
2. **formatter 例の YAML 妥当性と書き方** — `tests/test_verdict_parser.py`
   - `FORMATTER_PROMPT.safe_substitute(raw_output="", valid_statuses_str="PASS | RETRY | ABORT")` の
     出力フォーマット節の delimiter block を `yaml.safe_load` でき、3 フィールドが `<key>: |` であること。
     修正前は `"…"` のため「block scalar であること」の assert で FAIL。
   - 既存の `$valid_statuses_str` / `---NO_VERDICT_FOUND---` テストは維持（回帰なし）。
3. **例の書き方で書いた特殊文字入り verdict の解析（文字列経路）** — `tests/test_verdict_parser.py`
   - 値に `: `・` #`・`"`・`\`・改行を含む reason / evidence / suggestion を、例と同じ書き方（`<key>: |` ＋ 2 スペース字下げ）で組んだ
     verdict について、`parse_verdict`（Step 1 strict）と `parse_verdict_block`（comment 経路）が各値を `strip()` 後の原文どおり返すこと。
     status は `PASS` / `ABORT`（suggestion 必須経路）の両方。
   - 補足: パーサは不変なのでこのテストは修正前から PASS する（EB の保護を目的とする特性テスト）。Red 要件は 1・2 で満たす。

#### Medium テスト

4. **ファイル経路（artifact `verdict.yaml`）** — `tests/test_verdict_artifact.py`（`load_verdict_yaml`）と
   `tests/test_artifact_verdict.py` もしくは `tests/test_resolve_verdict_artifact.py` の既存 fixture（`resolve_artifact_verdict`）
   - 3 と同じ特殊文字入り verdict を `tmp_path` 配下の `verdict.yaml` に書き、`load_verdict_yaml` /
     `load_verdict_yaml_for_marker_vocabulary` が全値を保持すること。
   - `<run>/steps/<step>/attempt-001/verdict.yaml` に配置し、`resolve_artifact_verdict` が `VerdictArtifactUnusableError` を
     出さずに status を採用すること（`ResolvedArtifactVerdict` は status のみ保持するため、値の保持は直前の load 系で検証する）。
   - ファイル I/O を伴うため Medium（`docs/dev/testing-convention.md` 判定基準）。

#### Large テスト

- 追加しない。理由（`docs/dev/testing-convention.md` の省略理由に沿う）:
  1. 変更は prompt 文字列のみで、実 agent / 外部 API との結合点（CLI 起動・stdout 収集・artifact 保存）は不変。
  2. 想定不具合（例が YAML 不正 / 特殊文字で値が壊れる）は Small / Medium で決定的に検出できる。
  3. 実 LLM を使う Large は agent が例を写すかの確率的挙動しか追加せず、回帰検出情報が増えない。
  4. 既存の `tests/test_verdict_artifact_e2e_large_local.py` が artifact 経路の E2E を保護しており、本変更で振る舞いは変わらない。

#### 実装前 Red 証跡

- 1・2 は修正前の HEAD で FAIL することを implement 時に確認・記録する。
- 補助として実障害ログ apokamo/kamo2#1703（`VerdictParseError: YAML parse error in verdict block: mapping values are not allowed here`）が OB と対応する。

### 長時間 acceptance の配置

- 該当なし。根拠: 追加検証は Small / Medium の pytest と `make check` のみで、nested `kaji run`・外部 agent を含む dogfood・
  fresh 環境での反復 acceptance は無い。1 回の所要は数十秒〜数分で、implement step の timeout の 25% を超えない。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし（YAML / PyYAML は既存） |
| docs/ARCHITECTURE.md | なし | verdict 解決機構・パーサは不変。出力例の記載なし（grep 確認済み） |
| docs/dev/ | あり | `docs/dev/skill-authoring.md` § verdict 出力規約の例を `|` に統一し、規約文を追加 |
| docs/reference/ | なし | API 仕様・規約変更なし |
| docs/cli-guides/ | なし | CLI 仕様変更なし |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| `.claude/skills/issue-implement/templates/implement-report.md` | あり | verdict 例を `|` に統一（同根箇所） |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| YAML 1.2.2 仕様 § 8.1.2 Literal Style | https://yaml.org/spec/1.2.2/#812-literal-style | （要約）literal style（`|`）では字下げされた全文字が内容として扱われ、エスケープは解釈されず、改行は保持される。`#`・`: `・引用符・`\` を内容として保持できる根拠 |
| YAML 1.2.2 仕様 § 7.3.3 Plain Style | https://yaml.org/spec/1.2.2/#733-plain-style | plain scalar は `: ` と ` #` を含めない（mapping indicator / comment と衝突）。OB の ScannerError / 切り詰めの根拠 |
| YAML 1.2.2 仕様 § 7.3.1 Double-Quoted Style | https://yaml.org/spec/1.2.2/#731-double-quoted-style | double-quoted は `\` エスケープを解釈し、改行は line folding で空白に畳まれる。formatter 例の弱点の根拠 |
| PyYAML documentation | https://pyyaml.org/wiki/PyYAMLDocumentation | `yaml.safe_load` が kaji のパーサ実体（`kaji_harness/verdict.py:165`） |
| 出力例（修正対象） | `kaji_harness/prompt.py:156-168` | OB の引用元 |
| formatter 例（修正対象） | `kaji_harness/verdict.py:45-58` | ダブルクォート例 |
| パーサ | `kaji_harness/verdict.py:148-184`（`_parse_yaml_fields`）、`:585-643`（load 系）、`kaji_harness/artifact_verdict.py:261-292`（`_adopt`） | 全経路が `yaml.safe_load` を通ることの確認。`.strip()` 後の値を保持 |
| verdict 出力規約 | `docs/dev/skill-authoring.md` § verdict 出力規約 | skill 側の正本。テンプレートは既に `|`、例 2 箇所が plain |
| Issue #536 本文 | https://github.com/apokamo/kaji/issues/536 | OB / EB / 完了条件 / 重要判断（two-way door）の出典 |
| 利用側 incident | apokamo/kamo2#1703（private。Issue #536 本文とレディネスレビューで実ログ照合済み） | `VerdictParseError: … mapping values are not allowed here` で ABORT 喪失 |
| 利用側回避策 | apokamo/kamo2#2092（private。同上） | 値は `|` か `"..."`、保存前に `yaml.safe_load` で確認 |
| git 履歴 | `git log -S` / `git log -L`（`465b380` #57、`f011d19` #220、`eaa08d7` #77） | 「いつから」の根拠 |
