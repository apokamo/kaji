# [設計] verdict の YAML 禁止制御文字を parse 境界で正規化し、完了済み step の再実行を防ぐ

Issue: #298

## 概要

agent verdict の `reason` / `evidence` に YAML 1.2 の printable 範囲外の制御文字（ESC 等）が
生で混入すると `_parse_yaml_fields` の `yaml.safe_load` が失敗し、commit・push・外部コメント投稿
まで完了済みの step が ABORT 扱いになる。verdict 解決の唯一の合流点である `_parse_yaml_fields`
で YAML 禁止制御文字を parse 前に安全な文字へ正規化し、意味的な verdict を保ったまま解決する。

## 背景・目的

### Observed Behavior（OB）

Issue #137 / PR #297 の `pr-fix` attempt-002 では、修正 commit `2d43f04`・`make check`・push・
inline reply・PR summary 投稿まで完了していた。しかし agent が生成した `verdict.yaml` の evidence に
生の ESC（U+001B）が混入し、以下で workflow が ERROR 終了した。

```text
VerdictParseError: YAML parse error in verdict block: unacceptable character #x001b: special characters are not allowed
  in "<unicode string>", position 444
```

`run.log`（`.kaji-artifacts/137/runs/260711011329/run.log:50-54`）実記録:

```text
failure_event kind=verdict_exception step_id=pr-fix exception_type=VerdictParseError
step_end step_id=pr-fix status=ABORT reason="step aborted without a usable verdict"
workflow_end status=ERROR
recovery_decision cause=verdict_resolution_failure failed_step=pr-fix resume_from=pr-fix
```

`resolve_verdict`（`kaji_harness/verdict.py:653-655`）は artifact を最優先で読むため、
`attempt-002/verdict.yaml:7` の ESC を含む `verdict.yaml` を `load_verdict_yaml` →
`_parse_yaml_fields` に渡し、`yaml.safe_load` が YAMLError を送出。fail-loud 契約により
comment / stdout へは fallthrough しない。runner（`runner.py:863-933`）はこれを
`verdict_exception` として合成 ABORT verdict に変換し、recovery が `pr-fix` からの再開を提示した。

再現に用いた実障害 artifact:

- `.kaji-artifacts/137/runs/260711011329/run.log:50-54`
- `.kaji-artifacts/137/runs/260711011329/steps/pr-fix/attempt-002/verdict.yaml:7`
- `.kaji-artifacts/137/runs/260711011329/recovery.json:12-20`
- 調査コメント: https://github.com/apokamo/kaji/issues/137#issuecomment-4942814585

### Expected Behavior（EB）

- verdict を YAML parser に渡す境界で、YAML 1.2 の printable 範囲外の制御文字すべてを検出し、
  構造を壊さない printable 文字へ正規化して parse を成立させる。
- TAB（U+0009）/ LF（U+000A）/ CR（U+000D）など YAML が許可し構造上必要な文字は置換しない。
- 意味的に PASS の verdict が生成され step の外部副作用が完了している場合、制御文字だけを理由に
  完了済み step を未完了として再実行しない（= verdict.yaml を再解決して本来の PASS を得る）。
- 禁止文字を処理した場合、コードポイントと位置を安全な表記（`U+XXXX`）で診断ログに残し、
  生の禁止文字をログへ再出力しない。

EB の一次裏付けは「## 参照情報」の PyYAML `Reader.NON_PRINTABLE` と YAML 1.2 spec。

### 再現手順（Steps to Reproduce）

1. verdict の `evidence`（または `reason`）に生の ESC（U+001B）を含む `verdict.yaml` を用意する。
   最小例: `status: PASS\nreason: ok\nevidence: "done\x1bhere"\nsuggestion: ""`。
2. `load_verdict_yaml(path, {"PASS"})` を呼ぶ（= `resolve_verdict` の artifact 経路）。
3. `_parse_yaml_fields` → `yaml.safe_load` が `unacceptable character #x001b` で `VerdictParseError`。
4. 同一入力を stdout 経路（`parse_verdict`）・comment 経路（`parse_verdict_block`）に渡しても、
   いずれも `_parse_yaml_fields` を通るため同じ境界条件で失敗する。
5. ESC 以外の YAML 禁止制御文字（U+0000〜U+0008 / U+000B / U+000C / U+000E〜U+001F / U+007F /
   U+0080〜U+0084 / U+0086〜U+009F）も同じ境界条件で再現する（parameterized）。

## 根本原因（Root Cause）

- **なぜ壊れるか**: `_parse_yaml_fields`（`verdict.py:112-138`）が verdict block を無検証で
  `yaml.safe_load` に渡す。PyYAML の `Reader`（`yaml/reader.py`）は入力ストリームを走査し、
  `NON_PRINTABLE` にマッチする文字を発見すると `ReaderError`（`YAMLError` サブクラス）を送出する。
  ESC は YAML 1.2 の `c-printable` に含まれないため、evidence に生で入ると parse 全体が失敗する。
- **いつから壊れているか**: verdict の YAML parse は当初からこの前提。artifact 経路
  （`load_verdict_yaml`, Issue #220）追加後は verdict.yaml が第一 source になったため、agent が
  生成した制御文字混入 verdict.yaml が fail-loud で必ず ABORT に落ちる経路が確定した。
- **同根の他の壊れ箇所（完了条件: 同根の入力経路調査）**: verdict 解決の 3 経路は
  すべて `_parse_yaml_fields` に合流する（`grep _parse_yaml_fields`）:
  - stdout 経路: `parse_verdict` → `_extract_block_strict/relaxed` → `_parse_yaml_fields`
  - artifact 経路: `load_verdict_yaml`（`verdict.py:516`）→ `_parse_yaml_fields`
  - comment 経路: `parse_verdict_block`（`verdict.py:568`）→ `_parse_yaml_fields`

  したがって `_parse_yaml_fields` の 1 箇所を直せば block 抽出経路と artifact 読み込み経路の
  双方が同時に修復される。verdict block 抽出（正規表現）自体は制御文字を弾かないため、抽出後の
  parse が唯一の失敗点である。
- **verdict 以外の `yaml.safe_load`（調査結果・スコープ外）**: `providers/local.py:200`（local
  issue frontmatter）/ `workflow.py:29,48`（workflow 定義）/ `skill.py:92`（skill frontmatter）は
  いずれも **repo 管理下の YAML** を読む経路であり、agent 生成 verdict とは入力源が異なる。制御文字
  混入の failure mode を共有しないため本 Issue のスコープ外とする（混在させない: bug.md「リファクタ
  混在を避ける」）。

## インターフェース

bug 修正だが、診断証跡を永続化する完了条件のため verdict 解決 IF を **後方互換の範囲で拡張** する。
変更範囲を以下に確定する（下の「findings の伝播 IF」が正本）。

**変更する点**:

- `parse_verdict` / `load_verdict_yaml` / `parse_verdict_block` / `_parse_yaml_fields` に
  **keyword-only の optional 引数 `findings_sink=None` を追加**する。既存の位置引数・例外契約は不変で、
  `findings_sink` を渡さない既存呼び出し・既存テストは挙動が変わらない（後方互換）。
- `resolve_verdict` の**戻り値を 2-tuple `(Verdict, source)` から 3-tuple
  `(Verdict, source, findings)` に拡張**する。呼び出し元は runner.py:841 の 1 箇所のみ。

**変更しない点**:

- 上記関数の既存位置引数の順序・型・意味。
- 例外契約（`VerdictParseError` / `VerdictNotFound` / `InvalidVerdictValue` の送出条件）。
  制御文字を除いても verdict として不成立なら従来どおり送出する。
- recovery の分類（`classify_failure`）・判定（`plan_recovery`）ロジック。

### 追加する型・内部ヘルパ

```python
@dataclass(frozen=True)
class ControlCharFinding:
    """sanitize 時に検出した YAML 禁止制御文字 1 個の診断情報（生文字は保持しない）。"""

    position: int   # sanitize 前 text 上の 0-based index
    codepoint: int  # ord(禁止文字)

    @property
    def label(self) -> str:
        """安全表記。生の制御文字を出さず ``U+001B`` 形式で返す。"""
        return f"U+{self.codepoint:04X}"


def _sanitize_yaml_control_chars(text: str) -> tuple[str, list[ControlCharFinding]]:
    """YAML 1.2 printable 範囲外の制御文字を U+FFFD へ置換する。

    Returns:
        (sanitized_text, findings)。findings は検出順の ``ControlCharFinding`` 列。
        forbidden 文字が無ければ text はそのまま、findings は空リスト。
    """
```

### findings の伝播 IF（永続化のための呼び出し経路）

`_parse_yaml_fields` の findings を、`RunLogger` と `attempt_dir` を保持する runner まで
届けるための経路を明示する。`logger.warning`（stdlib、`kaji_harness.verdict` logger）は
console tree（`kaji.*`）でも run.log でもないため成功 run 後に findings を再確認できない
（review-design Must Fix）。よって **findings を戻り値で surface し、runner が RunLogger の
構造化 event として run.log に永続化する**。

1. `_parse_yaml_fields(block, *, findings_sink: list[ControlCharFinding] | None = None)`:
   sink が渡されたら sanitize findings を append する（sink 無しの既存呼び出しは挙動不変）。
2. 3 経路の公開関数に optional sink を透過的に通す（デフォルト `None` で後方互換）:
   - `load_verdict_yaml(path, valid_statuses, *, findings_sink=None)`
   - `parse_verdict_block(text, valid_statuses, *, findings_sink=None)`
   - `parse_verdict(output, valid_statuses, *, ..., findings_sink=None)`
3. `resolve_verdict` は sink を 1 本生成して採用経路へ渡し、戻り値を
   **`tuple[Verdict, str, list[ControlCharFinding]]`** に拡張する（呼び出し元は runner.py:841 の
   1 箇所のみ。third element は空リストが常態）。
4. `RunLogger` に新 event を追加:

   ```python
   def log_verdict_sanitization(
       self, step_id: str, attempt: str, findings: list[ControlCharFinding]
   ) -> None:
       """禁止制御文字の正規化を run.log に永続記録する（生文字は書かない）。"""
       self._write(
           "verdict_sanitization",
           step_id=step_id,
           attempt=attempt,
           count=len(findings),
           findings=[{"codepoint": f.label, "position": f.position} for f in findings],
       )
   ```

`_write` は run.log（JSONL）へ追記するため、event は成功 run 後も artifact として残る。
`findings` は `label`（`U+XXXX`）と `position` のみで生の禁止文字を含まない。

### 禁止文字集合（Source of Truth = PyYAML `Reader.NON_PRINTABLE`）

許可（置換しない）:

| 範囲 | 内容 |
|------|------|
| U+0009 / U+000A / U+000D | TAB / LF / CR |
| U+0020–U+007E | ASCII printable |
| U+0085 | NEL（YAML 1.2 c-printable に含まれる） |
| U+00A0–U+D7FF / U+E000–U+FFFD / U+10000–U+10FFFF | 上位 printable |

禁止（U+FFFD へ置換）: 上記補集合。実装上は PyYAML と同一の否定文字クラスで表現する:

```python
_YAML_FORBIDDEN = re.compile(
    "[^\x09\x0a\x0d\x20-\x7e\x85\xa0-퟿-�\U00010000-\U0010ffff]"
)
```

### 置換文字の選択と根拠

- 置換先は **U+FFFD（REPLACEMENT CHARACTER）**。U+FFFD 自体が許可範囲（U+E000–U+FFFD）に含まれる
  ため再度 forbidden にヒットしない。
- **backslash escape（`\x1b` / ``）を出力しない**。これらは double-quoted scalar 内で
  YAML が解釈し、置換後に禁止文字を復元してしまう（プレーン / block scalar 内では逆に文字化ける）。
  scalar style に依存せず安全なのは「1 個の printable 文字への単純置換」であり、U+FFFD を採用する。
- コードポイント情報は inline に埋めず診断ログへ回す（生禁止文字の再出力を避ける完了条件）。

## 制約・前提条件

- PyYAML（`pyyaml`）を parser として使用し続ける。禁止集合は PyYAML `Reader.NON_PRINTABLE` と
  一致させ、二重管理を避ける（実測: `yaml.reader.Reader.NON_PRINTABLE.pattern` と同等の否定クラス）。
- 修正が触れるモジュールは 3 つ: `verdict.py`（sanitize + findings surface）、`logger.py`
  （新 event `log_verdict_sanitization`）、`runner.py`（resolve_verdict 呼び出し 1 箇所で event 発行）。
  recovery の**分類・判定ロジックは変更しない**。
- 正規化は「意味的 verdict の救済」であり、status / reason / evidence の必須性検証（`_validate`）は
  従来どおり後段で行う。制御文字を除いても内容が verdict として不成立なら従来どおり fail する。
- **診断証跡の正本は `RunLogger` の `verdict_sanitization` event（run.log に永続化）** とする。
  `verdict.py` の stdlib `logging` logger は非永続の console breadcrumb に留め、証跡としては依拠しない
  （理由は「findings の伝播 IF」参照）。いずれの出力にも生の禁止文字は含めない。

## 方針

1. `_sanitize_yaml_control_chars(text)` を追加。`_YAML_FORBIDDEN` で走査し、各マッチの
   `ControlCharFinding(match.start(), ord(char))` を findings に集め、`_YAML_FORBIDDEN.sub("�", text)`
   で置換。
2. `_parse_yaml_fields` を次のように変更（findings は sink 経由で surface する）:

   ```python
   def _parse_yaml_fields(
       block: str, *, findings_sink: list[ControlCharFinding] | None = None
   ) -> Verdict:
       sanitized, findings = _sanitize_yaml_control_chars(block)
       if findings and findings_sink is not None:
           findings_sink.extend(findings)
       try:
           fields: Any = yaml.safe_load(sanitized)
       except yaml.YAMLError as e:
           raise VerdictParseError(f"YAML parse error in verdict block: {e}") from e
       ...  # 以降の必須フィールド検証は不変
   ```

   stdlib `logger.warning` は console breadcrumb としては残せるが、**診断証跡の正本にはしない**
   （console_log.py が明記するとおり `kaji_harness.verdict` logger は run.log にも console tree にも
   伝播しないため）。永続化は下記 4 の RunLogger event が担う。

3. `resolve_verdict` は sink を生成して採用経路の公開関数へ渡し、
   `(Verdict, source, findings)` を返す（IF は「## インターフェース」参照）。これにより:
   - artifact 経路（`load_verdict_yaml`）: 制御文字混入 verdict.yaml が本来の PASS として解決 →
     完了済み step が ABORT に落ちず、`resolve_verdict` が `(Verdict, "artifact", findings)` を返す。
   - stdout / comment 経路: 同じ合流点で同時に修復され、findings も同様に surface される。

4. runner（runner.py:841-862）は `resolve_verdict` の findings が非空なら
   `logger.log_verdict_sanitization(current_step.id, attempt_dir.name, findings)` を呼び、run.log に
   `verdict_sanitization` event を永続化する（既存 `log_verdict_source` と同じ場所・同じ attempt 文脈）。
   これで **成功 run 後も** run.log から U+001B 等のコードポイントと位置を確認でき、生禁止文字は
   どの artifact にも出力されない（完了条件: 診断証跡の永続化）。

5. **recovery を変更しない設計判断（完了条件: 副作用完了後の verdict 解決失敗の区別方針）**:
   - 本 Issue の OB は「artifact に本来の verdict が既に存在するのに parse だけが失敗する」ケース。
     正しい復旧は **artifact の再解決** であり、その最も正確な実施点は verdict parser 境界である
     （verdict.yaml は agent の意味的 verdict をそのまま保持しており、制御文字だけがノイズ）。
   - parser 修正により、副作用完了済み step は「未完了」ではなく本来の PASS verdict として解決される。
     これが「副作用完了後に verdict 解決だけ失敗したケースを step 本体の未実行・失敗と区別する」方針の
     実体であり、区別を verdict 解決の時点で行う（recovery が起動する前に決着する）。
   - 結果として本 failure mode では recovery が起動せず、`verdict_exception` も発生しない。
     recovery 側の分類（`classify_failure` の `verdict_resolution_failure`）・resume 判定
     （`plan_recovery`）は変更しない。これにより dispatch_failure / agent_abort /
     VerdictNotFound / InvalidVerdictValue など既存ケースの挙動は回帰しない（最小侵襲）。
   - **残存する一般問題（スコープ外・follow-up 候補）**: 制御文字以外の理由で verdict 解決が失敗し、
     かつ副作用が完了済みの一般ケースを recovery が機械判別する仕組み（step 単位の副作用完了マーカー）は
     本 Issue では扱わない。kaji には現状その汎用マーカーが無く、導入は独立した設計判断を要する。
     `_shared/report-unrelated-issues.md` に従い follow-up として別 Issue 化を提案する。

## テスト戦略

### 変更タイプ

実行時コード変更（verdict parser の入力正規化ロジック追加）。恒久回帰テストが必要。

### Small テスト（主戦場）

`tests/test_verdict_parser.py`:

- **境界の parameterized 固定（完了条件: TAB/LF/CR と禁止制御文字の境界）**:
  許可 = {U+0009, U+000A, U+000D, U+0020, U+0085, U+007E} は置換されず、それらを含む verdict が
  従来どおり parse される。禁止 = {U+0000, U+0008, U+000B, U+000C, U+000E, U+001B, U+001F, U+007F,
  U+0080, U+0084, U+0086, U+009F} は U+FFFD へ置換され parse が成立する。各コードポイントを
  `pytest.mark.parametrize` で列挙し、境界（0x1F 禁止 / 0x20 許可、0x7E 許可 / 0x7F 禁止、
  0x84 禁止 / 0x85 許可 / 0x86 禁止、0x9F 禁止 / 0xA0 許可）を明示的に含める。
- **`_sanitize_yaml_control_chars` 単体**: findings が `ControlCharFinding(position, codepoint)` を
  検出順で返し、`label` が `U+001B` 形式であること。禁止文字が無ければ findings 空・text 不変。
- **findings 伝播**: `resolve_verdict` が 3 経路（artifact / comment / stdout）それぞれで findings を
  第 3 戻り値として surface すること。sink 無しの既存呼び出し（`_parse_yaml_fields` 直接等）は
  挙動不変であること。
- **再現テスト（完了条件: 禁止制御文字を含む verdict の再現 → 修正後に意味的解決）**:
  ESC を evidence に含む block が `_parse_yaml_fields` で `status=PASS` の `Verdict` を返す。
  実装前は同入力が `VerdictParseError` になる（bug.md の実装前 Red 証跡は #137 実障害ログで代替可、
  ただし恒久回帰テスト自体は必須で追加する）。
- **3 経路の合流確認**: 同一の制御文字入りテキストを `parse_verdict`（stdout）/
  `parse_verdict_block`（comment）に渡し、いずれも救済されることを assert。
- **回帰: 制御文字以外の parse 失敗は従来どおり**: status 欠落 / 非 mapping は `VerdictParseError`。

`tests/test_logger.py`（RunLogger event の永続化・完了条件: 診断証跡）:

- `log_verdict_sanitization` が run.log に `event=verdict_sanitization` の JSONL 行を書き、
  `findings` が `{"codepoint": "U+001B", "position": <n>}` 形式で、行のバイト列に生の制御文字
  （`\x1b` 等）が含まれないことを assert。`count` が findings 数と一致すること。

### Medium テスト（file I/O + artifact 解決）

`tests/test_verdict_artifact.py`:

- 制御文字入り `verdict.yaml` を tmp_path に書き、`load_verdict_yaml` が PASS を解決する。
  `findings_sink` を渡した場合に該当 findings が append されること、渡さない場合に挙動が不変であること。
- `resolve_verdict` が 3-tuple `(PASS, "artifact", findings)` を返し（`findings` に該当コードポイントを
  含む）、comment / stdout に fallthrough しないことを確認（fail-loud 契約は制御文字だけでは発火しない）。

`tests/test_verdict_artifact_runner.py`:

- runner の verdict 解決経路で、制御文字入り artifact が `verdict_exception` を発生させず
  step が正常完了することを確認（副作用完了済み step が ABORT に落ちない）。
- **診断証跡の永続化（完了条件）**: 同 run 後、run.log に `verdict_sanitization` event が存在し、
  `findings` に該当コードポイント（`U+001B` 等）と位置が記録されていること。run.log ファイルの
  バイト列に生の禁止制御文字が含まれないことを assert（`caplog` ではなく実 artifact を検証する点が
  review-design Must Fix への直接対応）。

### Large テスト（不要 — 理由明記）

新規の外部 API / E2E 疎通面は増えない。修正は既存 parser 内部の入力正規化に閉じており、
`test_verdict_artifact_e2e_large_local.py` / `test_verdict_e2e.py` が既にパイプライン全体を
カバーする。`docs/dev/testing-convention.md` の 4 条件を満たす（独自ロジックは Small/Medium で
検証済み / 想定不具合パターンは境界 parameterized で捕捉 / Large を足しても回帰情報が増えない /
理由をレビュー可能な形で明記）。既存 large_local が緑であることは回帰確認として実行する。

### recovery 回帰テスト（挙動非変更の固定）

`tests/test_recovery_classify.py` / `tests/test_recovery_plan.py`:

- 制御文字以外の真の `VerdictParseError`（例: status 欠落）に対する `verdict_resolution_failure`
  分類と resume 判定が従来どおりであることを固定（本修正が recovery を変えていない証跡）。
- 本 Issue の failure mode（制御文字入り artifact）が verdict 解決で救済され、そもそも
  `verdict_exception` failure_event を生まないことを、可能なら runner events テスト
  （`tests/test_recovery_runner_events.py`）で確認する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし（既存 PyYAML の入力正規化に閉じる） |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | なし | ワークフロー・開発手順の変更なし |
| docs/reference/python/error-handling.md | あり | verdict parse 境界で制御文字を正規化する挙動を追記（VerdictParseError の発生条件が狭まる） |
| docs/reference/python/logging.md | あり | RunLogger の新 event `verdict_sanitization`（フィールド: step_id / attempt / count / findings）を追記。診断表記規約（`U+XXXX`、生制御文字を出さない）も記載 |
| docs/cli-guides/failure-recovery.md | あり | 「副作用完了済み step が制御文字だけで再実行対象になる」旧挙動の記述があれば、正規化により解消する旨へ更新 |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| YAML 1.2 spec § 5.1 Character Set | https://yaml.org/spec/1.2.2/#51-character-set | `c-printable ::= #x9 \| #xA \| #xD \| [#x20-#x7E] \| #x85 \| [#xA0-#xD7FF] \| [#xE000-#xFFFD] \| [#x10000-#x10FFFF]`。この範囲外の文字は YAML stream に出現できない=禁止集合の定義。 |
| PyYAML `Reader.NON_PRINTABLE`（実測） | `python -c "import yaml.reader; print(yaml.reader.Reader.NON_PRINTABLE.pattern)"` → `[^\t\n\r -~\x85\xa0-퟿-�\U00010000-\U0010ffff]` | 実 parser が拒否する集合そのもの。本設計の `_YAML_FORBIDDEN` はこの否定クラスと一致させ、置換対象を parser の受理集合と厳密に整合させる。 |
| PyYAML 拒否の実測 | `yaml.safe_load('evidence: "a\x1bb"')` → `unacceptable character #x001b: special characters are not allowed` | OB のエラー文言を再現。ESC が `_parse_yaml_fields` で失敗する直接証跡。 |
| 現行 parser | `kaji_harness/verdict.py:112-138`（`_parse_yaml_fields`）、`:497-518`（`load_verdict_yaml`）、`:544-570`（`parse_verdict_block`）、`:608-674`（`resolve_verdict`） | 3 経路が `_parse_yaml_fields` に合流する単一修正点であること、artifact 最優先・fail-loud 契約の裏付け。 |
| 現行 runner | `kaji_harness/runner.py:841-933` | verdict 解決例外を `verdict_exception` として合成 ABORT に変換する経路。parser 救済によりこの経路に到達しなくなることの裏付け。 |
| 現行 recovery | `kaji_harness/recovery/classify.py:94-101`（`_classify_verdict`）、`kaji_harness/recovery/handler.py:240-277`（resume 判定）、`recovery/models.py:30`（`NON_RESUMABLE_STEPS`） | `pr-fix` が `NON_RESUMABLE_STEPS` に無いため candidate として再開提示されること=OB の recovery 側挙動の裏付け。recovery を変更しない判断の根拠。 |
| console logging の系統 | `kaji_harness/console_log.py:1-24` | 「``run.log``（``RunLogger`` が書く JSONL）とは別系統」「``kaji`` ルート logger… ``RunLogger`` (``kaji_harness.*``) とは別ツリー」。stdlib `logger.warning`（`kaji_harness.verdict`）を診断証跡の正本にできない根拠 = 永続化を RunLogger event に載せる判断の裏付け。 |
| RunLogger の永続 event 契約 | `kaji_harness/logger.py:118-124`（`log_verdict_source`）、`:33`（`_write`）、`:152-175`（`log_failure_event`） | `_write` が run.log（JSONL）へ追記する既存パターン。新 event `log_verdict_sanitization` を同契約で追加し attempt 文脈と共に永続化する設計の一次根拠。 |
| resolve_verdict 呼び出し元 | `kaji_harness/runner.py:841-862` | `resolve_verdict` の唯一の呼び出し元。戻り値 3-tuple 化と `log_verdict_sanitization` 呼び出しの追加点。findings 永続化の実施箇所。 |
| #137 実障害 artifact | `.kaji-artifacts/137/runs/260711011329/`（run.log:50-54 / attempt-002/verdict.yaml:7 / recovery.json:12-20） | OB の一次記録。bug.md の実装前 Red 代替証跡。 |
