# [設計] workflow incident ラベルの既定名を `kaji:` 接頭辞付きにし、ラベル名とガイドパスを設定可能にする

Issue: #434

## 概要

failure triage 第1層（`kaji_harness/recovery/`）が起票・重複検索・transient 遷移に使う incident
ラベルの既定名を `kaji:incident` 系に変更する。あわせて、種別・status 初期値・transient の
3 ラベル名と、incident 本文に埋め込む運用ガイドのパスを `.kaji/config.toml` の新セクション
`[incident]` で指定できるようにする。

## 背景・目的

- 現状、ラベル名は `kaji_harness/recovery/incident.py:42-44` の定数、ガイドパスは同 `:50` の
  `_LABELS_GUIDE` に固定されている。利用側リポジトリは変更できない。
- 「incident」はプロダクトの本番障害と同じ語である。同じ GitHub 組織・Project 上では、kaji
  workflow の失敗と本番障害を取り違えやすい（Issue 本文「現状の問題」）。
- 利用側がラベル名だけを変えると、kaji は旧名で検索と起票を続ける。そのため既存 incident が
  見つからず、incident Issue が重複して起票される。

### ユースケース

1. **設定を追加しない利用者**として、kaji が起票する incident を `kaji:incident` で識別したい。
   本番障害の incident と取り違えないためである。→ 未設定時の既定名を変更する。
2. **kaji を組み込んだリポジトリの管理者**として、`[incident]` の 3 ラベル名を自組織の命名規約に
   合わせたい。旧名 `incident` を使い続ける場合も含む。kaji にはその名前で起票・重複検索・
   transient 遷移を行わせたい。
3. **同管理者**として、incident 本文の「ラベル運用ガイド」リンクの参照先を
   `labels_guide_path` で自リポジトリの文書に向けたい。
4. **kaji 自身のリポジトリ**は `[incident]` に旧名を明示する。本 Issue の merge 後も、従来どおり
   旧名で動かす（ラベルの移行はフォローアップ Issue で扱う）。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|------------|
| 既定名を変えず、設定化だけ行う | 目的 1（未設定でも本番障害と区別できること）を満たさない |
| 環境変数で指定する | ラベル名はリポジトリ単位の運用規約である。tracked な `.kaji/config.toml` に置けば、全利用者と全 worktree で同じ値になる。環境変数は利用者ごとにずれる |
| 8 ラベルすべてを設定可能にする | kaji のコードが触るラベルは 3 つだけである。残り 5 つは人間が付けるもので、kaji のコードは参照しない。設定項目にしても効果がなく、完了条件も 3 ラベルの個別指定を求めている |
| `[execution]` の下に置く | `[execution]` は実行制御の設定で、`config.local.toml` の overlay 対象でもある。incident ラベルはリポジトリ単位の規約なので、overlay で利用者ごとに変わるべきではない。そのため独立セクションにする |

## インターフェース

### 入力: `.kaji/config.toml` の `[incident]` セクション（任意）

| key | 必須/任意 | 型 | 既定値 | 用途 |
|-----|-----------|----|--------|------|
| `kind_label` | 任意 | str | `"kaji:incident"` | 種別ラベル。起票時に付与し、重複検索のキーにする |
| `initial_status_label` | 任意 | str | `"kaji:incident:investigating"` | status の初期値。起票時に付与し、transient クローズ時に外す |
| `transient_label` | 任意 | str | `"kaji:incident:cause:transient"` | transient クローズ時に付与する。照合時の transient 判定にも使う |
| `labels_guide_path` | 任意 | str | `"docs/dev/incident-labels.md"` | incident 本文末尾「ラベル運用ガイド」リンクの参照先 |

- セクションがなければ全 key が既定値になる。key を省いた場合も、その key だけ既定値になる。
- `[incident]` は `config.local.toml` の overlay 対象にしない（`[paths]` と同じ扱い）。overlay に
  書かれた `[incident]` は無視する。

#### 検証規則（違反はすべて `ConfigLoadError`。設定読み込み時に fail-fast）

入力境界は Pydantic model（`ConfigDict(extra="forbid", frozen=True, strict=True)`）で検証する
（AGENTS.md「外部入力は Pydantic で検証する」、ADR 010 / ADR 011 と同じ方式）。下表の規則は、
その model の field / model validator として実装する。

| 対象 | 規則 | 理由 |
|------|------|------|
| `[incident]` | table であること | 既存セクションと同じ |
| 未知の key | 拒否する | typo（例: `kind_lable`）を黙って無視すると既定値が使われる。kaji 自身のように旧名を指定したリポジトリが、気づかないまま新名で起票・検索してしまう。これを防ぐ |
| 3 ラベル各値 | str であること | 型の検証 |
| 3 ラベル各値 | 空文字・空白だけの値を拒否する | 完了条件「空文字」 |
| 3 ラベル各値 | 前後の空白を拒否する（`value != value.strip()`） | GitHub 側で空白が落とされ、kaji の保持値と一致しなくなる恐れがある |
| 3 ラベル各値 | `,` を拒否する | `gh issue create --label` / `gh issue edit --add-label` はカンマで値を分割する。REST の `labels` クエリもカンマ区切りである（Primary Sources 参照） |
| 3 ラベル各値 | 制御文字（改行など）を拒否する | CLI 引数やクエリ文字列を壊す |
| 3 ラベル各値 | ダブルクォート `"` を拒否する | gh の `--label` / `--add-label` / `--remove-label` は pflag StringSlice で、値を Go の `encoding/csv` で解析する。裸の `"` は `bare " in non-quoted-field` で gh が失敗する（review-design で gh 2.100.0 により再現済み）。両端が `"` の値は引用符が除去され、kaji が検索に使う名前と付与される名前が食い違う。CSV エスケープして渡す案は採らない（provenance 参照） |
| 3 ラベル相互 | 重複を拒否する。比較は大文字小文字を区別しない（`casefold`） | 完了条件「3 ラベルの重複」。同名だと transient 遷移で付与と除去が打ち消し合い、種別ラベルが外れる |
| `labels_guide_path` | str であること。空白だけでないこと。空白文字・制御文字・`` ` ``・`(`・`)` を含まないこと | 値は本文の `` [`<path>`](<path>) `` にそのまま入る。これらの文字が入ると Markdown のリンクが壊れる。ファイルの存在は確認しない（URL の指定や、リポジトリ外の文書を許すため） |

### 出力・副作用

- `KajiConfig.incident: IncidentConfig`（新設。frozen dataclass で、上表の 4 フィールドと既定値を持つ）
- `RecoveryHandler` の incident 経路が設定値を使う。GitHub provider の場合:
  - 起票: `create_issue(labels=[kind_label, initial_status_label])`
  - 重複検索: `search_issues_all(labels=[kind_label], state="all")`
  - transient 遷移: `edit_issue(add_labels=[transient_label], remove_labels=[initial_status_label])`
    の後に close する
  - 照合: closed の候補は `transient_label` を持つかどうかで recur か regression かを分ける
  - 本文: 末尾の行を `` ラベル運用ガイド: [`<labels_guide_path>`](<labels_guide_path>) `` にする
- `GitHubProvider.search_issues_all` は、クエリに入れる各ラベル名を percent-encode する
  （`urllib.parse.quote(name, safe="")` をカンマで連結する）。空白など、URL のクエリにそのまま
  置けない文字を含むラベル名でも検索できるようにするためである。`incident` はエンコードしても
  値が変わらないので、既存の挙動に影響しない。

### 使用例

```toml
# 旧名を使い続ける場合（kaji 自身のリポジトリの設定）
[incident]
kind_label = "incident"
initial_status_label = "incident:investigating"
transient_label = "incident:cause:transient"
# labels_guide_path は既定値（docs/dev/incident-labels.md）のまま
```

```python
config = KajiConfig.discover(workdir)
config.incident.kind_label          # "incident"（上記設定の場合）/ "kaji:incident"（未設定の場合）
handler = RecoveryHandler(..., incident=config.incident)   # commands/run.py・recover.py で渡す
```

### エラー

- 検証に違反した場合: `ConfigLoadError(path, "incident.<key> ...")` を送出する。`kaji run` など、
  config を読み込むすべての CLI が既存の `ConfigLoadError` 経路で停止する。
- GitHub 側にラベルが存在しない場合（設定した名前のラベルが repo に未作成など）: 従来どおり
  `create_issue` / `edit_issue` の失敗として fail-open になる。`incident_recording_failed` と
  stderr の WARNING が出る。kaji はラベルを自動作成しない（ラベル定義は `labels.yml` と
  利用者の責務）。

## 制約・前提条件

- **互換処理を持たない**（Issue「決定事項」）:
  - 重複検索は `kind_label` 1 つだけで行う。旧名 `incident` との二重検索や移行期間は設けない。
  - 旧 `incident:cause:transient` を transient とみなす互換判定も持たない。
- **local provider は対象外**（Issue「決定事項」、#304 の v1 契約）。local provider での remote
  起票・検索がない点は変えない。設定の読み込みと検証は provider に関係なく行う。
- **ラベルの移行はしない**: `.github/labels.yml` と、kaji 自身が使うラベル名の記述
  （`docs/dev/labels.md`、`docs/dev/workflow_guide.md`、`.claude/skills/incident-*`）は変更しない。
- **layer**: `kaji_harness.config` と `kaji_harness.recovery` は同じ application 層である
  （`tests/test_layer_imports.py`）。`recovery/target.py` はすでに `..config` を import しているので、
  `recovery/incident.py` が `..config` から `IncidentConfig` を import しても層の違反にならない。
- **検証の実装方式**: 新設の `[incident]` は新しい外部入力契約なので、AGENTS.md「外部入力は
  Pydantic で検証する」をそのまま適用する。ADR 011 と同じく **入力境界のみ Pydantic、検証後は
  frozen dataclass `IncidentConfig` へ詰め替えて `KajiConfig` に渡す**。既存セクション
  （`[paths]` / `[execution]` / `[provider]`）の手書き検証は移行しない（ADR 010 が既存 parser を
  対象外とした境界と同じ）。`pydantic>=2` は `pyproject.toml:27` の既存依存で、新規依存はない。

## 変更スコープ

| ファイル | 変更 |
|----------|------|
| `kaji_harness/config.py` | `IncidentConfig`（frozen dataclass・既定値の単一の正本）、入力境界の Pydantic model `_IncidentSection`、`KajiConfig.incident` フィールド、`_parse_incident()` を追加する |
| `kaji_harness/recovery/incident.py` | 定数 `INCIDENT_LABEL` / `INCIDENT_STATUS_INVESTIGATING` / `INCIDENT_CAUSE_TRANSIENT` / `_LABELS_GUIDE` を削除する。transient 判定・本文描画・起票で、ラベル名とガイドパスを引数として受け取る |
| `kaji_harness/recovery/__init__.py` | `__all__` から上の 3 定数を外す（package 外の利用者はいない。grep で確認済み） |
| `kaji_harness/recovery/handler.py` | `RecoveryHandler` に必須フィールド `incident: IncidentConfig` を追加し、検索・起票・transient 遷移に渡す。docstring 中の固定ラベル名を一般的な表現に直す |
| `kaji_harness/commands/run.py` / `commands/recover.py` | `RecoveryHandler(..., incident=config.incident)` を渡す |
| `kaji_harness/providers/github.py` | `search_issues_all` でラベル名を percent-encode する |
| `.kaji/config.toml` | `[incident]` に旧名 3 つを明示する |
| tests | 下記「テスト戦略」を参照。既存の期待値を更新する |
| docs / CHANGELOG | 下記「影響ドキュメント」を参照 |

## 方針

1. **既定値の単一の正本は `IncidentConfig`**: 既定名を定数として `incident.py` と `config.py` の
   両方に置くと、二重管理になる。そこで既定値は `IncidentConfig` のフィールド既定値だけに置き、
   `incident.py` の定数は削除する。
2. **注入経路**: `KajiConfig.incident` → `commands/run.py` / `commands/recover.py` →
   `RecoveryHandler.incident` → `incident.py` の純関数と副作用境界（引数として渡す）。
   `RecoveryHandler.incident` には **既定値を持たせない**（必須フィールドにする）。既定値があると、
   呼び出し側が渡し忘れたときに kaji 自身が黙って新名で動いてしまう。必須にすれば、渡し忘れは
   構築時の `TypeError` として表に出る。
3. **純関数の IF 変更**（名前と責務のみ示し、細部は実装に委ねる）:
   - `plan_incident_action(signature, candidates, *, transient_label: str)`: transient 判定を、
     候補の `labels` に `transient_label` が含まれるかで行う。`IncidentCandidate.is_transient`
     property は定数に依存しているので削除する（またはラベル名を受け取るメソッドに置き換える）。
   - `render_incident_issue(ctx, *, regression_of=None, labels_guide_path: str)`
   - `execute_incident_action(provider, *, action, ctx, local_records, existing_comments, incident: IncidentConfig)`:
     起票時のラベルを `incident` から組み立て、ガイドパスを `render_incident_issue` に渡す。
4. **`_parse_incident(path, data) -> IncidentConfig`**: tracked の `data["incident"]` だけを見る
   （overlay は渡さない）。流れは次のとおり。
   - `[incident]` がなければ `IncidentConfig()`（既定値）を返す。dict でなければ
     `ConfigLoadError(path, "[incident] must be a table")`。
   - `_IncidentSection.model_validate(section)` で検証する。model は 4 フィールドを
     `IncidentConfig` と同じ既定値付きで持ち、`extra="forbid"`（未知 key 拒否）・`strict=True`
     （非 str を型変換せず拒否）とする。3 ラベルの文字規則は共通の `field_validator`、
     `labels_guide_path` の文字規則は個別の `field_validator`、3 ラベルの casefold 重複は
     `model_validator(mode="after")` で検査する。
   - `pydantic.ValidationError` を捕捉し、`ConfigLoadError(path, ...)` に変換して `from exc` で
     連鎖させる。メッセージは各 error の `loc` を `incident.<key>` に整形し、`msg` を続ける
     （例: `incident.kind_label: must not contain ','`、`incident.kind_lable: Extra inputs are not
     permitted`）。複数 error は `; ` で連結する。`ValidationError` は外へ漏らさない。
   - 検証済みの model から `IncidentConfig(**model.model_dump())` を作って返す。
5. **percent-encode**: `search_issues_all` の中で、ラベル名ごとに `quote(name, safe="")` をかけ、
   `,` で連結する。区切りのカンマはエンコードしない。ラベル名の中のカンマは設定検証で拒否済みである。

```python
# 疑似コード（handler._record_incident の変更点）
cfg = self.incident
candidates = parse_candidates(self.provider.search_issues_all(labels=[cfg.kind_label], state="all"))
action = plan_incident_action(signature, candidates, transient_label=cfg.transient_label)
outcome = execute_incident_action(self.provider, ..., incident=cfg)

# _close_transient_incident
self.provider.edit_issue(ref, add_labels=[cfg.transient_label], remove_labels=[cfg.initial_status_label])
```

### 8 ラベルの既定名（確定）

| 現行名（kaji 自身は本 Issue 後も使う） | 新既定名 | 設定 key |
|----------------------------------------|----------|----------|
| `incident` | `kaji:incident` | `kind_label` |
| `incident:investigating` | `kaji:incident:investigating` | `initial_status_label` |
| `incident:mitigated` | `kaji:incident:mitigated` | —（人間が付与する。kaji のコードは参照しない） |
| `incident:resolved` | `kaji:incident:resolved` | — |
| `incident:cause:internal` | `kaji:incident:cause:internal` | — |
| `incident:cause:upstream` | `kaji:incident:cause:upstream` | — |
| `incident:cause:environment` | `kaji:incident:cause:environment` | — |
| `incident:cause:transient` | `kaji:incident:cause:transient` | `transient_label` |

設定 key のない 5 ラベルは、kaji のコードが参照しない。そのため既定名は、`docs/dev/incident-labels.md`
と CHANGELOG に「推奨する命名」として記載するだけにする（コードに定数は置かない）。

### 既知の制約（フォローアップ Issue へ引き継ぐ）

- `.claude/skills/incident-investigate/SKILL.md:72` の前提ガードは `incident` ラベルを確認している。
  Issue の決定により、本 Issue では skill を変更しない。そのため、新しい既定名で運用する利用側
  リポジトリがこの skill をそのまま使うと、第2層の調査が前提ガードで ABORT になる。kaji 自身は
  旧名を明示しているので影響を受けない。この点は `issue-close` 時に起票するフォローアップ Issue
  （ラベル移行）に含めるよう、Issue コメントで引き継ぐ。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 既定名に `kaji:` 接頭辞を付ける | 8 ラベルすべて `kaji:` + 現行名 | Issue 本文「概要」1、完了条件 1（人間決定。具体名は設計で確定するよう委任されている） | 現行名の前に `kaji:` を付ける機械的な規則で、8 名を確定した（上表） |
| 設定可能にする範囲 | 種別・status 初期値・transient の 3 ラベルとガイドパス | Issue 完了条件 3・4（人間決定） | 4 key の名前と `[incident]` セクションを定めた |
| 旧名との二重検索・transient 互換 | 持たない | Issue「決定事項」1・2（人間決定） | 検索キーを `kind_label` 1 つに固定した。旧 transient ラベルを持つ closed 候補を regression と判定することを、テストで固定する |
| kaji 自身の設定 | 3 ラベルに旧名を明示し、`labels.yml` は変更しない | Issue「決定事項」3（人間決定） | ガイドパスは既定値と同じなので明示しない |
| local provider | 対象外。v1 契約を維持する | Issue「決定事項」5、#304 の v1 契約（人間決定。前回の design ABORT を受けて起票者が記録） | 設定の読み込みと検証は provider に依存させない |
| kaji 自身のラベル記述（docs/skills） | 変更しない | Issue 完了条件「影響ドキュメントの更新」（人間決定） | `incident-labels.md` への追記と設定リファレンスの更新だけを行う |
| 不正値を読み込み時にエラーにする | `ConfigLoadError` | Issue 完了条件 7（人間決定。例として空文字と 3 ラベルの重複が挙がっている） | 追加の規則（カンマ・ダブルクォート・前後の空白・制御文字・casefold 重複・未知 key・ガイドパスの文字制約）は AI の仮定。根拠は gh の CSV 解析（pflag StringSlice → `encoding/csv`）と REST のカンマ区切り仕様、typo の silent fallback 防止。review-design で検査する |
| `"` を含むラベル名の扱い | 読み込み時に拒否する（CSV エスケープして渡す方式は採らない） | AI の仮定（review-design R2 で選択を求められた 2 案から選んだ）。エスケープ方式は `GitHubProvider` の全ラベル受け渡し（create / edit の add / remove）に CSV 整形を入れる必要があり、他の呼び出し元の挙動も変える。`"` をラベル名に使う実需は想定しにくく、拒否は後から緩和できる（two-way door）。verify-design / review-code で検査する | 3 ラベル共通の field validator で拒否する |
| セクション名・key 名 | `[incident]` / `kind_label` / `initial_status_label` / `transient_label` / `labels_guide_path` | AI の仮定。未リリースの新しい key なので、リリース前なら安く直せる（two-way door）。根拠は既存 key の snake_case 命名。review-design / PR review で検査する | — |
| overlay の扱い | `[incident]` は overlay 対象外（無視する） | AI の仮定。ラベルはリポジトリ単位の規約であり、`[paths]` と同じ扱いにした。review-design で検査する | — |
| `RecoveryHandler.incident` を必須にする | 既定値なし | AI の仮定。渡し忘れで kaji 自身が黙って新名に切り替わる事故を、構築時の失敗に変えるため。review-code で検査する | — |
| 検索時の percent-encode | ラベル名ごとにエンコードする | AI の仮定。任意の名前を設定できる以上、クエリを壊す文字への対策が要るため。`incident` などの既存の値は変わらない。review-design / Large テストで検査する | — |
| `[incident]` の検証方式 | 入力境界を Pydantic model で検証し、`ValidationError` を `ConfigLoadError` に変換する。検証後は frozen dataclass に詰め替える | 既存規約: AGENTS.md Always-Apply Rules「外部入力は Pydantic で検証する」、ADR 010（既存 parser は対象外のまま新規入力に Pydantic）、ADR 011（入力境界のみ Pydantic・後段 dataclass 維持）。review-design R1 の指摘で初版の「Pydantic 不使用」から修正 | `extra="forbid"` / `strict=True` / field・model validator の割り当てと、エラーメッセージの `incident.<key>: <msg>` 整形 |
| incident skill の前提ガード | 本 Issue では変更せず、フォローアップへ引き継ぐ | Issue 完了条件「影響ドキュメントの更新」が `.claude/skills/incident-*` を変更対象外としている（人間決定） | 既知の制約として記録し、Issue コメントで引き継ぎを依頼する |

one-way door の未決はない。前回の design ABORT の論点（local provider）は、Issue「決定事項」5 で
A 案（v1 契約の維持）に決まっており、完了条件もそれに合わせて修正されている。

## テスト戦略

### 変更タイプ

- 実行時コード変更（config の読み込み、recovery の incident 経路、GitHub provider の検索クエリ）

### Small テスト

- `tests/test_config.py`
  - `[incident]` がない場合、`IncidentConfig` の 4 値が既定値（`kaji:` 接頭辞付き、ガイドパス既定値）になる
  - 全 key を指定すると、その値が採用される。一部だけ指定すると、残りは既定値になる
  - `ConfigLoadError` になるケース: table でない、非 str（int / bool を型変換せず拒否すること）、
    空文字、空白だけ、前後の空白、カンマ、改行、3 ラベルの完全一致の重複、大文字小文字だけが
    違う重複、未知 key、`labels_guide_path` の空・空白・`` ` ``・括弧
  - ダブルクォートの拒否を 3 フィールドそれぞれで確認する（`'ops"incident'` のような途中の `"`、
    `'"incident"'` のような両端の `"`）。parametrize で key × 値の組を網羅する
  - 例外が `pydantic.ValidationError` ではなく `ConfigLoadError` であること、メッセージに
    `incident.<key>` が含まれること、`__cause__` が `ValidationError` であること
  - overlay（`config.local.toml`）に書いた `[incident]` は無視され、tracked の値が採用される
- `tests/test_recovery_incident.py`
  - `plan_incident_action` に独自の `transient_label` を渡したとき、そのラベルを持つ closed 候補は
    recur、持たない候補は create_regression になる
  - 既定設定で、旧 `incident:cause:transient` を持つ closed 候補が create_regression になる
    （互換判定を持たないという決定を固定する）
  - `render_incident_issue` のガイドリンクに、指定したパス（既定値と独自値）が入る
  - 既存テストの期待値を新しい IF（引数）と既定名に合わせて更新する
- `tests/test_providers_github_incident.py`（subprocess を patch する既存の Small テスト）
  - `search_issues_all(labels=["kaji:incident"])` のクエリに `labels=kaji%3Aincident` が入る
  - 空白を含むラベル名が `%20` にエンコードされる。`labels=incident` という既存の期待値は変わらない

### Medium テスト

- `tests/test_recovery_incident_handler.py`（実 artifact の I/O と FakeProvider を使う）
  - FakeProvider の `search_issues_all` を、要求したラベルをすべて持つ Issue だけ返すように変える
    （GitHub の AND 絞り込みを再現する）
  - 独自の `IncidentConfig` を渡すと、起票ラベル・検索ラベル・transient 遷移の add/remove が
    すべて設定値になる
  - 既定の `IncidentConfig` では、起票が `["kaji:incident", "kaji:incident:investigating"]` になる
  - 既定設定のとき、旧名 `incident` だけを持つ既存 incident は検索にかからず、新規起票になる
    （二重検索をしないという決定を固定する）
  - 旧名を指定した設定のとき、同じ既存 incident が recur になる（kaji 自身の運用を保護する）
  - 既存テストの `_handler` helper と `RecoveryHandler` を直接構築している箇所を、
    `incident=` を渡す形に更新する
- 配線（`commands/run.py` / `commands/recover.py`）
  - tmp の `.kaji/config.toml` に `[incident]` を書き、`KajiConfig.discover()` で読み込む。その config で
    `commands/run.py` の `_run_failure_triage` と `commands/recover.py` の recover 経路を起動し、
    `RecoveryHandler` に `config.incident` が渡ることを確認する（`RecoveryHandler` と `get_provider`
    を capture 用の fake に差し替え、kwargs を検証する）。既存の配線テストはない（grep で確認済み）
    ため、新しく追加する。

### Large テスト

- `tests/test_recovery_incident_large_local.py`（stub `gh` を実 subprocess で呼ぶ。ネットワーク不要）
  - stub が受け取った argv を記録し、`GitHubProvider.search_issues_all(labels=["kaji:incident"])` が
    実プロセス越しに `labels=kaji%3Aincident` のクエリを渡すことを確認する
  - `create_issue(labels=["kaji:incident", "kaji:incident:investigating"])` が、`--label` 引数として
    設定名をそのまま渡すことを確認する
  - 注: stub は argv を記録するだけで、gh 本体の CSV 解析（pflag → `encoding/csv`）は再現しない。
    `,` と `"` を含む名前が gh に届かないことは、読み込み時に拒否する Small テスト（上記）で保証する
    （本 Issue は受け渡し側で CSV 整形を行わないため、argv 一致の Large テストでは足りない部分を
    入力境界で塞ぐ）
- large_forge（実 GitHub API との疎通）は追加しない。実 incident の起票という破壊的な副作用があり、
  隔離された repo もないためである。#304 の設計（`tests/test_recovery_incident_large_local.py`
  の docstring）の判断を踏襲する。GitHub の API 仕様との整合は、Primary Sources の公式仕様と
  stub の argv 検証で担保する。

### 変更固有の検証（恒久テストにしない）

- kaji 自身の `.kaji/config.toml` を `KajiConfig.discover()` で読み込み、`config.incident` が旧名 3 つに
  なることを実装報告で一度だけ確認する。恒久テストにしない理由: (1) 独自のロジックがない（設定値
  そのもの）。(2) 読み込みロジックは上の Small テストで捕捉できる。(3) フォローアップ Issue で
  この値を撤去する予定があり、恒久テストにすると移行を妨げるだけで回帰の検出にはほぼ寄与しない。
  (4) 以上の理由を本節で記録できる。
- `make check` / `make verify-docs`

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない（既存の config 方式を踏襲する） |
| docs/ARCHITECTURE.md | なし | モジュール構成と層は変わらない |
| docs/reference/configuration.md / configuration.ja.md | **あり** | `### [incident]` 節（key・型・既定値・検証・overlay 対象外）を追加する。Overlay merge rule に「`[incident]` は overlay 対象外」と追記する |
| docs/dev/incident-labels.md | **あり** | 「ラベル名は設定で変更可能・既定値は `kaji:` 接頭辞付き」の節を追加する（8 ラベルの既定名の対応表と、`[incident]` key との対応）。kaji 自身は旧名を明示設定していることも記す。既存の一覧表（旧名）は変更しない |
| CHANGELOG.md | **あり** | `[Unreleased]` の `### BREAKING CHANGE` に、既定ラベル名の変更と、旧名を維持する設定例（`[incident]` の 3 key）を記載する。`### Added` に `[incident]` 設定を記載する |
| docs/dev/labels.md / docs/dev/workflow_guide.md / `.claude/skills/incident-*` / `.github/labels.yml` | なし（意図的に対象外） | Issue 完了条件により、フォローアップ Issue で移行と同時に更新する |
| docs/cli-guides/failure-recovery.md(.ja) | なし | ラベル名を記載していない。incident-labels.md へのリンクだけがある |
| docs/dev/（ワークフロー手順） | なし | 開発ワークフローは変わらない |
| AGENTS.md / CLAUDE.md | なし | 規約は変わらない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #434 本文「決定事項」「完了条件」 | https://github.com/apokamo/kaji/issues/434 | 二重検索なし、transient 互換なし、kaji 自身は旧名を明示、`labels.yml` は不変、local provider は対象外。3 ラベルとガイドパスを設定可能にする |
| 現行ラベル定数 | `kaji_harness/recovery/incident.py:42-44`, `:50`, `:167`, `:439`, `:604` | 起票ラベル・transient 判定・ガイドリンクが定数に固定されている |
| 現行 handler | `kaji_harness/recovery/handler.py:568-575`, `:618-635` | 検索は `INCIDENT_LABEL` だけで行う。非 `IncidentSearchCapable` の provider は remote に進まない。transient 遷移は add/remove 定数を使う |
| #304 設計（v1 provider 契約） | `draft/design/issue-304-1-incident.md` | 非 GitHub provider では起票を no-op にし、ローカル記録だけを行う |
| config ローダー | `kaji_harness/config.py`（`_load`, `_parse_execution`, `_read_overlay`） | 既存の検証は手書きで `ConfigLoadError` を使う。overlay は `[execution]` / `[provider]` だけに適用される |
| 外部入力の検証規約 | `AGENTS.md`（Always-Apply Rules）、`docs/adr/010-pydantic-series-input-validation.md`、`docs/adr/011-workflow-overlay-single-layer.md` § overlay 表層は Pydantic model で検証する | 「外部入力は Pydantic で検証する」。ADR 011: 新規の外部入力契約には AGENTS.md の規約がそのまま適用され、入力境界のみ Pydantic、後段は既存 dataclass に渡す。unknown field は禁止する |
| Pydantic の既存利用 | `kaji_harness/series/models.py:19`（`ConfigDict(extra="forbid", frozen=True, strict=True)`）、`kaji_harness/series/loader.py:41`（`ValidationError` の捕捉とドメイン例外への変換）、`pyproject.toml:27` | 同じ model 設定と例外変換の前例。依存は既存 |
| Pydantic ドキュメント | https://docs.pydantic.dev/latest/concepts/validators/ 、https://docs.pydantic.dev/latest/api/config/#pydantic.config.ConfigDict.extra | `field_validator` / `model_validator(mode="after")` の用法。`extra="forbid"` は未定義の field を検証エラーにする |
| 設定リファレンス | `docs/reference/configuration.md` § Overlay merge rule | `[paths]` は overlay されず、overlay 内の `[paths]` は無視される（本設計の `[incident]` と同じ扱い） |
| 層の規約 | `tests/test_layer_imports.py`（`MODULE_LAYERS`）、`kaji_harness/recovery/target.py:7` | config と recovery はどちらも application 層で、recovery → config の import には前例がある |
| GitHub REST: List repository issues | https://docs.github.com/en/rest/issues/issues#list-repository-issues | `labels` パラメータは "A list of comma separated label names"。ラベル名に含まれるカンマは区切りと区別できない |
| gh issue create | https://cli.github.com/manual/gh_issue_create | `-l, --label <name>`: "Add labels by name"。help の例に `gh issue create --label "bug,help wanted"` がある（カンマで複数ラベルを指定できる）。実装は `StringSliceVarP(&opts.Labels, "label", "l", ...)`（https://github.com/cli/cli/blob/trunk/pkg/cmd/issue/create/create.go ） |
| pflag StringSlice | https://pkg.go.dev/github.com/spf13/pflag#StringSlice 、https://raw.githubusercontent.com/spf13/pflag/master/string_slice.go | StringSlice フラグは値を `encoding/csv` の Reader で解析する（`--label "a,b"` は 2 ラベルになる） |
| Go `encoding/csv` | https://pkg.go.dev/encoding/csv | 引用符は CSV 構文として解釈され、引用されていない field 中の裸の `"` は `ErrBareQuote`（`bare " in non-quoted-field`）になる。両端の引用符は除去される |
| review-design の再現 | Issue #434 の review-design コメント | gh 2.100.0 で `gh issue create --help --label 'ops"incident'` / `gh issue edit --help --add-label 'ops"incident'` がともに exit 1、`bare " in non-quoted-field` |
| Python `urllib.parse.quote` | https://docs.python.org/3/library/urllib.parse.html#urllib.parse.quote | `safe=""` を指定すると `/` を含む予約文字をすべて `%XX` にエンコードする |
| Python `tomllib` | https://docs.python.org/3/library/tomllib.html | TOML を dict に読み込む。table は `dict` になる（`[incident]` の table 判定の根拠） |
| incident skill の前提ガード | `.claude/skills/incident-investigate/SKILL.md:69-72` | 前提として `incident` ラベルを確認している（既知の制約の根拠） |
