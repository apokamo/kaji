# [設計] agent の手書き verdict.yaml による判定喪失を共通の確定処理で防ぐ

Issue: #568

## 概要

agent step では、完了トリガであり判定の正本でもある `verdict.yaml` を agent が手書きしている。
字下げを 1 文字誤るだけで、副作用は完了済みなのに判定だけが失われる（`VerdictParseError`）。
これを、runner が attempt ごとに用意する契約を使って検証し、既存形式へ直列化し、原子的に公開する
内部コマンド `kaji verdict publish` に置き換える。判定内容（status / reason / evidence / suggestion）
を決めるのは引き続き agent である。

## 背景・目的

### Observed Behavior（OB）

Issue #551 の dev-small run `261113020532` の `start` step では、worktree と Issue NOTE の作成に成功した後、
agent が次の `verdict.yaml` を保存した。`evidence:` の行頭に 1 文字余分な空白がある（Issue 本文 § OB）。

```yaml
status: PASS
reason: |
  Issue #551 の worktree セットアップが完了
 evidence: |
  fetch した origin/main と FETCH_HEAD は一致した
```

runner は artifact を primary として読み、壊れた artifact では fail-loud となる（comment / stdout へ
fallthrough しない）。そのため workflow は ERROR で停止した（incident #565 の run.log /
`steps/start/result.json`）:

```text
VerdictParseError: YAML parse error in verdict block: while parsing a block mapping
  in "<unicode string>", line 1, column 1:
    status: PASS
expected <block end>, but found '<block mapping start>'
  in "<unicode string>", line 4, column 2:
     evidence: |
failure_event kind=verdict_exception step_id=start exception_type=VerdictParseError synthetic=True
```

同じ指紋の再発が、同じ Issue の別 run `261113105818` の `close` step でも記録されている（incident #565 の
再発コメント。N=2）。特定の step や skill に限った問題ではない。

### Expected Behavior（EB）

Issue 本文 § EB（人間決定）に従う:

- agent は `status`・`reason`・`evidence`・`suggestion` を判断する。共通処理は、runner 由来の
  現在の step/attempt と `on:` の status 集合に照らしてそれらを検証し、既存の pure YAML 形式へ直列化する。
- 公開前に現行規則を適用する: `reason` / `evidence` は非空、`ABORT` / `BACK*` の `suggestion` も非空。
  入力が無効なら `verdict.yaml` を作らず、agent は同じ attempt 内で訂正できる。
- Issue コメントの投稿コマンド、先頭 verdict marker、stdout block、artifact → comment → stdout の解決順、
  壊れた既存 artifact の fail-loud は維持する。artifact はコメント投稿と stdout 出力の後に、
  完成した内容を原子的に公開する。
- 既存の手書き artifact も引き続き読める。共通処理は agent の根拠の真偽やテストの成否を推測・判定しない。

根拠となる既存契約: `docs/dev/skill-authoring.md` § verdict 出力規約（3 経路・必須項目・verdict-last）、
`docs/ARCHITECTURE.md` § Verdict 判定機構（解決順・fail-loud）、`kaji_harness/verdict.py:_parse_yaml_fields`
/ `_validate` / `_require_suggestion`（必須項目と suggestion 規則の実装）。

### 再現手順（Steps to Reproduce）

最小再現（HEAD `ed61776`。review-ready で独立に再現済み）:

1. 上記 OB の YAML をファイル `verdict.yaml` に保存する
2. `kaji_harness.verdict.load_verdict_yaml(Path("verdict.yaml"), {"PASS", "ABORT"})` を呼ぶ
3. `VerdictParseError: YAML parse error in verdict block: ... expected <block end>, but found '<block mapping start>'` が送出される

実 run の再現条件: agent step を対話 runner（Herdr）または headless で起動し、agent が prompt
§ 出力要件の 3 番目の手順に従って pure YAML を手書きした場合。Herdr は `verdict_path.is_file()` が真に
なった時点で完了とみなすため（`kaji_harness/interactive_terminal_herdr.py:351`）、壊れたファイルが
そのまま判定対象になる。

修正後: 同じ判定フィールド（`status=PASS`、上記の reason / evidence、`suggestion` 空）を
`kaji verdict publish` に渡すと、現在の attempt に有効な `verdict.yaml` だけが公開される。

## 根本原因（Root Cause）

### なぜ壊れるか

1. **完了トリガとなるファイルの作者が LLM になっている**: `kaji_harness/prompt.py:191-193`（§ 出力要件の手順 3）と
   Herdr wrapper の初期メッセージ（`kaji_harness/assets/interactive-terminal/wrapper.sh:48-51`
   「write only a pure YAML verdict file to this exact path」）が、字下げに敏感な YAML の手書きを agent に求めている。
   機械的な検証や直列化を挟まずに、手書きの結果がそのまま正本になる。
2. **公開前の検証がなく、壊れたファイルを attempt 内で直せない**: Herdr はファイルが存在するだけで完了と判定する。
   runner は ADR 005 の不変条件として、壊れた artifact では comment / stdout へ fallthrough しない
   （`kaji_harness/verdict.py:819-822`）。agent はファイルが壊れていると知る機会がないまま attempt が終わり、
   step は ERROR になる。副作用（worktree 作成・コメント投稿・merge 等）は完了しているため、
   resume できない step では手動復旧が必要になる。
3. **共通 serializer を agent が使えない**: `write_verdict_yaml`（`kaji_harness/verdict.py:653-673`）は runner の
   正規化保存でしか使われていない。agent が呼べる経路がない。しかも `path.write_text` で直接書くため原子的でもない。
4. **書き込み途中のファイルを公開しうる**: agent の Write ツールやシェルによる書き込みは原子的と保証されていない。
   Herdr のポーリング（`_VERDICT_POLL_INTERVAL_SECONDS` 間隔の `is_file()`）が書き込み途中のファイルを
   検知する余地がある。今回の障害の直接原因ではないが、Issue 本文 § OB が要件として挙げている。

### いつから

- `f011d19`（2026-06-04, #220 artifact-primary verdict）で、agent に `verdict_path` へ pure YAML を手書きさせる契約が入った。
- ADR 007（対話 runner）で `verdict.yaml` の出現が完了トリガになり、壊れたファイルの影響が即時の確定になった。
- `795bb72`（#536）が修正したのは出力例を literal block scalar にすることだけで、手書きという構造は残っている。
  agent が例から字下げを逸脱する今回の失敗は防げなかった（Issue 本文 § OB）。

### 同根の他の壊れ箇所（調査結果）

| 箇所 | 状態 | 本 Issue での扱い |
|------|------|-------------------|
| `kaji_harness/prompt.py` § 出力要件 手順 3 | 全 agent step に手書きを指示 | 修正（publish コマンドへ置換） |
| `kaji_harness/assets/interactive-terminal/wrapper.sh` 初期メッセージ | Herdr の全 agent step に手書きを指示 | 修正（publish の指示へ置換。banner 行は維持。後述） |
| 同梱 skill の明示的な手書き指示（`issue-implement` L108-116 / L264、`issue-small-change-execute` L165 / L231 / L241、`issue-small-change-review` L173 / L219 / L228、`incident-investigate` L54 / L181、`review-starter-update` L49、`release-starter` L91、`update-starter` L64） | prompt と同じく「`verdict_path` に pure YAML を保存」と指示 | 修正（publish へ移行） |
| incident #565 の再発（run `261113105818` の `close` step） | 同じ指紋で再発 | 上記の共通経路の修正で同時に塞がる |
| runner の正規化保存 `write_verdict_yaml` | 非原子的な `write_text` | 原子的な置換に変更（agent の完了後に実行されるため影響は小さいが、同じ serializer を使うので揃える） |
| exec_script（`baseline_precheck` 等） | `fsio.atomic_write` で script 自身が書く | 対象外（agent ではない） |
| `issue-close` / `review` の `--current-verdict-path [verdict_path]` | verdict_path を読むだけ | 変更なし |

## インターフェース

bug 修正ではあるが、agent 向けに内部コマンドを 1 つ追加する。既存の公開契約
（`verdict.yaml` schema、3 経路、解決順、`kaji issue comment --verdict-step/--verdict-status`、
`KAJI_VERDICT_PATH` を読む既存 consumer）は変更しない。

### 入力

#### 1. `kaji verdict publish`（新規・agent step 内専用の内部コマンド）

```
kaji verdict publish --status=<S> --reason=<R> --evidence=<E> [--suggestion=<G>]
```

| 引数 | 必須 | 説明 |
|------|:---:|------|
| `--status` | ✅ | 判定 status。現在の step の `on:` キーのいずれか（契約ファイルで検証。完全一致） |
| `--reason` | ✅ | 判定理由（前後の空白を除いて非空） |
| `--evidence` | ✅ | 具体的根拠（前後の空白を除いて非空） |
| `--suggestion` | — | 既定は空。`ABORT` / `BACK*` のときは非空が必須 |

- 許容 status を指定する引数や、公開先パスを上書きする引数は**持たない**（許容 status を agent の自己申告にしない。
  別 attempt への書き込みも防ぐ）。
- 現在の attempt との結び付けは、runner が agent プロセスに注入する環境変数 `KAJI_VERDICT_CONTRACT`
  （契約ファイルの絶対パス）だけで行う。
- 値の受け渡しには `--field=value` 形式を推奨する。値が `-` で始まる場合（`- item` など）でも
  argparse がオプションと誤認しない。

#### 2. 契約ファイル `<attempt_dir>/verdict-contract.json`（新規・runner が書く）

runner が agent の起動前に原子的に書く（`fsio.atomic_write`）。Pydantic で検証する（`extra="forbid"`、strict 型）。

```json
{
  "schema_version": 1,
  "issue_id": "568",
  "run_id": "261114152607",
  "step_id": "design",
  "attempt": 1,
  "verdict_path": "/abs/.kaji-artifacts/568/runs/261114152607/steps/design/attempt-001/verdict.yaml",
  "valid_statuses": ["PASS", "ABORT"]
}
```

- `valid_statuses` は `step.on` のキーを定義順に並べたもの（prompt の status 候補と同じ計算元）。空は不正。
- `verdict_path` は絶対パスで、ファイル名は `verdict.yaml`、親ディレクトリは契約ファイルと同じでなければならない。
- `verdict.yaml` の schema は変えない（ADR 012 の「verdict.yaml に run/step/attempt を保存しない」と整合する）。
  attempt の識別情報は別ファイルに持つ。

#### 3. 環境変数 `KAJI_VERDICT_CONTRACT`（新規・agent step のみ）

runner は agent step の起動時に、既存の 4 変数（`KAJI_TMP_DIR` / `TMPDIR` / `TMP` / `TEMP`）に加えてこの変数を注入する
（headless: `execute_cli(env=...)`、Herdr: wrapper の `env K=V` prefix）。exec / exec_script step には注入しない
（これらの step は従来どおり `KAJI_VERDICT_PATH` へ自分で書く）。

### 出力

| 結果 | exit code | stdout / stderr | ファイル |
|------|:---:|------|------|
| 公開成功 | 0 | stdout 1 行: `verdict published: step=<id> attempt=<NNN> status=<S> path=<abs>` | `verdict.yaml` が完成した内容で 1 回だけ出現する。一時ファイルは残らない |
| 入力検証エラー（status が `on:` 外、reason / evidence が空、`ABORT` / `BACK*` で suggestion が空） | 1（`EXIT_VALIDATION_ERROR`） | stderr に理由と許容 status 一覧。**入力を直せば同じ attempt で再実行できる** | 何も作らない |
| 状況エラー（`KAJI_VERDICT_CONTRACT` が未設定、契約ファイルが欠落・破損・不整合、attempt が終了済み（`result.json` がある）、`verdict.yaml` が既にある） | 2（`EXIT_INVALID_INPUT`） | stderr に理由。入力を直しても解決しない | 何も作らない・上書きしない |
| I/O エラー（一時ファイル作成・fsync・link の `OSError`。hard link を作れないファイルシステムを含む） | 3（`EXIT_RUNTIME_ERROR`） | stderr に理由 | `verdict.yaml` は作らない。一時ファイルは削除を試みる |

副作用: 成功時に `verdict.yaml` を 1 つ作るだけ。Issue コメントの投稿・stdout block の出力は行わない
（コメント投稿の統合は Issue 本文 § 範囲外）。

### Python API（内部）

| シンボル | 区分 | 責務 |
|----------|------|------|
| `verdict.dump_verdict_yaml(verdict) -> str` | 新規（既存 `write_verdict_yaml` から抽出） | 既存の pure YAML 直列化（`yaml.safe_dump(allow_unicode=True, default_flow_style=False, sort_keys=False)`、4 キー固定順） |
| `verdict.write_verdict_yaml(path, verdict)` | 変更 | `dump_verdict_yaml` と `fsio.atomic_write` を使う（runner の正規化保存専用。上書きは許す） |
| `fsio.atomic_publish_new(path, content)` | 新規 | 同じディレクトリに一意名の一時ファイルを作り、write → flush → fsync → `os.link(tmp, path)` → 一時ファイルを unlink（finally）。`path` が既にあれば `FileExistsError`。途中状態は外から見えず、上書きもしない |
| `verdict_publish.VerdictContract` | 新規 | 契約ファイルの Pydantic model |
| `verdict_publish.write_verdict_contract(attempt_dir, *, issue_id, run_id, step_id, attempt, valid_statuses) -> Path` | 新規 | runner が呼ぶ |
| `verdict_publish.publish_agent_verdict(environ, *, status, reason, evidence, suggestion) -> PublishedVerdict` | 新規 | 状況検証 → 正規化 → 入力検証 → 直列化と round-trip 確認 → 原子的公開 |
| `errors.VerdictPublishInputError` / `errors.VerdictPublishContextError` | 新規（`HarnessError` 派生） | exit 1 / 2 への対応付け用 |
| `commands/verdict.py:cmd_verdict_publish` + `parser._register_verdict` | 新規 | argparse と exit code の対応付けだけを担う |

### 使用例

prompt § 出力要件の手順 3 で agent に示すコマンド（status 候補は prompt 生成時に埋め込む）:

```bash
kaji verdict publish --status=PASS \
  --reason="$(cat <<'KAJI_VERDICT_EOF'
Issue #551 の worktree セットアップが完了
KAJI_VERDICT_EOF
)" \
  --evidence="$(cat <<'KAJI_VERDICT_EOF'
fetch した origin/main と FETCH_HEAD は一致した
- 値が "- " で始まっても、: や # や \ を含んでもそのまま渡せる
KAJI_VERDICT_EOF
)" \
  --suggestion=""
```

- `'KAJI_VERDICT_EOF'` のように区切り語を引用符で囲んだ heredoc では `$`・バッククォート・`\`・引用符が展開されない。
  値はそのまま渡り、YAML の字下げを agent が書くことはない。
- exit 1 なら stderr を読んで値を直し、同じコマンドを再実行する。exit 2 / 3 は再実行で解決しないため、
  `verdict_path` を手書きせずに状況をそのまま報告する（headless では comment / stdout fallback が引き続き働く）。

Python からの利用（テスト・内部）:

```python
published = publish_agent_verdict(
    os.environ, status="PASS", reason="...", evidence="...", suggestion=""
)
assert published.path.name == "verdict.yaml"
```

## 変更スコープ

| ファイル | 変更 |
|----------|------|
| `kaji_harness/verdict.py` | `dump_verdict_yaml` を抽出し、`write_verdict_yaml` を原子化。正規化（strip + #298 の制御文字置換）と検証の共通 helper を公開する。`resolve_verdict` / `load_verdict_yaml` は不変 |
| `kaji_harness/fsio.py` | `atomic_publish_new` を追加 |
| `kaji_harness/verdict_publish.py` | 新規（契約 model・契約の書き出しと読み込み・publish 本体） |
| `kaji_harness/errors.py` | publish 用の例外 2 種を追加 |
| `kaji_harness/commands/parser.py` / `commands/main.py` / `commands/verdict.py` | `kaji verdict publish` を登録・dispatch する |
| `kaji_harness/runner.py` | `_StepExecutor._dispatch` の agent 分岐で、prompt 生成前に契約を書き出し、env に `KAJI_VERDICT_CONTRACT` を加える |
| `kaji_harness/prompt.py` | § 出力要件の手順 3 を publish コマンドに置換する（手順 1・2 と書き方の規則は維持） |
| `kaji_harness/assets/interactive-terminal/wrapper.sh` | 初期メッセージを publish の指示へ変更する（banner 行は維持） |
| `.claude/skills/*/SKILL.md`（上表の 7 skill） | 手書き指示を publish に置換する |
| `tests/` | 下記テスト戦略。`tests/conftest.py` に `KAJI_VERDICT_CONTRACT` を除去する autouse fixture を追加 |
| docs | 影響ドキュメント参照 |

## 制約・前提条件

- **公開契約を変えない**（Issue 本文 § 重要判断 3）: `verdict.yaml` の 4 キーと schema、artifact → comment → stdout の解決順、
  壊れた artifact の fail-loud、`kaji issue comment` の marker 付与、stdout block、comment fallback の
  attempt / step scoping は不変。runner は既存の手書き artifact をこれまでどおり読む。
- **`KAJI_VERDICT_PATH` を agent env に入れない**: この変数は `kaji issue resolve-verdict` の run context
  （`artifact_verdict.run_context_from_env`）、`review_poll_entry`、`baseline_precheck` が読む。agent step に入れると、
  これらを agent 内から（`--current-verdict-path` なしで）呼んだときや、agent が実行するテストの挙動が変わる。
  そのため consumer のいない新しい変数名 `KAJI_VERDICT_CONTRACT` を使う。
- **判定内容に介入しない**: publish は形式（status 集合・非空・suggestion 規則）だけを検証する。根拠の真偽や
  テスト成否は推測しない（Issue 本文 § EB）。
- **原子性と上書き禁止**: `os.link` は宛先が既にあれば `EEXIST` で失敗し、成功すれば完成した inode が一度に見える
  （POSIX `link(2)`）。一時ファイルは宛先と同じディレクトリに作る（同一ファイルシステムに置くため）。
  一時ファイル名は `verdict.yaml` と一致しない（`.verdict.yaml.<random>.tmp`）。Herdr の `is_file()` は
  `verdict_path` だけを見るので、一時ファイルを検知しない。
- **hard link 非対応のファイルシステム**: artifacts dir が hard link を作れない場所にある場合、publish は exit 3 で
  fail-loud になる。上書き可能な `rename` への暗黙の降格はしない（上書き禁止の保証が崩れるため）。
  対応環境: Linux / macOS のローカル FS と WSL2 の ext4（現行の想定実行環境）。
- **agent の環境変数の伝播**: agent のツール用シェルが runner の注入した env を継承することは、#407
  （`KAJI_TMP_DIR`、`_shared/skill-tmp-resolve.md`）で既に運用上依存している前提。継承されない環境では
  publish は exit 2 で fail-loud になる（手書きへの暗黙 fallback は prompt で指示しない）。
- **テスト環境の隔離**: implement 等の agent step の中で `make check` を実行すると、テストプロセスにも
  `KAJI_VERDICT_CONTRACT` が継承される。テストが誤って実 attempt に publish しないよう、`tests/conftest.py`
  の autouse fixture で除去し、必要なテストだけが `monkeypatch.setenv` で設定する。
- **agent が呼ぶ `kaji` の版**: agent の PATH 上の `kaji` は runner と同じ installation であるという前提を置く。
  既存の `kaji issue comment --verdict-step/--verdict-status` も同じ前提に依存している。古い `kaji` が PATH にあると
  `verdict` subcommand がなく argparse エラー（exit 2）になり、fail-loud で表面化する。
- **依存**: 新規の外部依存はない（PyYAML / Pydantic / 標準ライブラリ）。

## 方針

### 状態遷移（publish 1 回分）

```
publish_agent_verdict(environ, fields)
  1. 状況検証（失敗 → VerdictPublishContextError / exit 2）
     - environ[KAJI_VERDICT_CONTRACT] がある・絶対パス・ファイルが存在する
     - VerdictContract として Pydantic 検証に通る
     - contract.verdict_path.name == "verdict.yaml" かつ parent == 契約ファイルの parent
     - attempt_dir/result.json が無い（attempt が未終了）
     - verdict_path が無い（公開済み・手書き済みなら拒否。上書きしない）
  2. 正規化: 各値を strip し、#298 の制御文字（YAML 1.2 printable 外）を U+FFFD に置換する
     （load 側と同じ規則。artifact に書く値と読み戻した値を一致させる）
  3. 入力検証（失敗 → VerdictPublishInputError / exit 1）
     - status ∈ contract.valid_statuses（完全一致）
     - reason / evidence が非空、ABORT / BACK* なら suggestion も非空（既存 _validate / _require_suggestion と同じ規則）
  4. text = dump_verdict_yaml(verdict)
     round-trip 確認: _parse_yaml_fields(text) + _validate(…, valid_statuses) の結果が verdict と一致する
     （内部不変条件。不一致は実装バグなので fail-loud）
  5. fsio.atomic_publish_new(verdict_path, text)
     （FileExistsError → 競合として exit 2、OSError → exit 3）
  6. PublishedVerdict(path, step_id, attempt, status) を返す → CLI が 1 行を出力
```

### runner（agent 分岐）

```
_dispatch(agent):
  deadline = AttemptDeadline.start(...)
  contract_path = write_verdict_contract(attempt_dir, issue_id, run_id, step.id, attempt_no,
                                         valid_statuses=list(step.on.keys()))
  agent_env = {**tmp_env, "KAJI_VERDICT_CONTRACT": str(contract_path)}
  prompt = build_prompt(...)            # 手順 3 は publish コマンド
  execute_cli(env=agent_env) / execute_interactive_terminal(env=agent_env)
  # 以降の resolve_verdict は不変（artifact → comment → stdout）
```

`attempt_no` を `_dispatch` へ渡すため、`execute` から引数を 1 つ追加する。

### prompt（§ 出力要件）

- 「`verdict_path` は完了トリガ。外部副作用を終えるまで公開しない」という前置きは維持する。
- 手順 1（コメント末尾に block を追記）、手順 2（stdout に block を出力）、block の例と「書き方の規則」は維持する
  （comment / stdout fallback の互換のため）。
- 手順 3 を「最後に、同じ内容を次のコマンドで公開する。`{verdict_target}` に直接書き込まない」に置き換え、使用例の
  コマンドを status 候補付きでレンダリングする。exit 1 なら直して再実行、exit 2 / 3 なら手書きせず報告、と添える。
- `verdict_path is None`（直接呼び出し・legacy）の場合も同じ文面にする（コマンドはパスを含まない）。

### wrapper.sh

- `initial_prompt` の「When the step is complete, write only a pure YAML verdict file to this exact path: $verdict_path
  / Do not wrap the YAML in Markdown...」を、「When the step is complete, publish the verdict with the
  `kaji verdict publish` command described in the prompt. Do not write $verdict_path yourself; it appears only
  after a successful publish.」に変える。
- banner 行 `The agent must read prompt_path and write a pure YAML verdict to verdict_path.` は #527 の
  terminal.log 解析契約（`interactive_terminal_common._WRAPPER_BANNER_END` と契約テスト）が固定しているので変えない。
  この行は人間向けの診断表示で、agent への指示ではない。

### skill

上表の 7 skill の「`verdict_path` へ pure YAML を保存」を「`verdict_path` が注入されている場合は prompt の
`kaji verdict publish` で公開する（直接書かない）」に置き換える。順序（comment → stdout → 公開）、checkpoint 手順、
ABORT 時の扱いは変えない。手動起動（`verdict_path` なし）の扱いも変えない。

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| agent / 共通処理の責務境界 | 判定内容は agent が決める。検証・直列化・artifact の公開は共通処理が担う | 人間決定: Issue 本文 § 重要判断 1「判定内容は agent が決め、機械的な検証・直列化・artifact 公開を共通処理に移す」/ § EB 1 項目目 | 共通処理を `kaji verdict publish` として実装し、形式検証だけを行うと定義（根拠の真偽は判定しない） |
| 既存の 3 経路・形式・解決順・fail-loud の維持 | 変更しない。runner は手書き artifact も読む | 人間決定: § 重要判断 1「正常時の workflow 遷移、3 経路、既存形式と解決順を維持」/ § EB 3・4 項目目 | `resolve_verdict` / `load_verdict_yaml` は不変。prompt の手順 1・2 は残し、手順 3 だけを置換する |
| コメント投稿の扱い | 既存の `kaji issue comment --verdict-step/--verdict-status` を使い、統合しない | 人間決定: § 重要判断 2 / § 影響範囲「範囲外: Issue コメント投稿の統合…新しいコメント marker」 | publish はコメントにも stdout にも出力しない |
| 公開契約を変えない・内部 CLI の形状は設計で決める | 公開契約は不変。内部コマンドの形状は可逆な詳細として本設計で決める | 人間決定: § 重要判断 3「内部 CLI 形状・検証実装など可逆な詳細は設計レビューで定める」 | 下の行で具体化 |
| 内部コマンドの形状 | `kaji verdict publish --status= --reason= --evidence= [--suggestion=]`。値は `--field=value` と引用符付き heredoc で渡す | AI の仮定。根拠: フィールド単位で渡せば agent が YAML の字下げを書く工程が構造的になくなる。argparse の `=` 形式なら先頭 `-` の値も安全。検査先: review-design | exit code を 0 / 1（直せる）/ 2（状況）/ 3（I/O）に分け、agent が再実行すべきか判断できるようにした |
| 現在の attempt との結び付け | runner が書く契約ファイル + 新しい env `KAJI_VERDICT_CONTRACT`。パスや status を引数で受けない | 人間決定（要件）: § EB「runner 由来の現在の step/attempt と `on:` の status 集合」/ 完了条件「許容 status は agent の自己申告で決めない」「別 attempt への書き込みを拒否」。方式自体は AI の仮定。根拠: env は attempt ごとのプロセスに設定されるので、resume session に残った古い prompt のパスを誤用しない。前例は #407 の `KAJI_TMP_DIR`。検査先: review-design / review-code | 終了済み attempt（`result.json` あり）と公開済み（`verdict.yaml` あり）を拒否条件として具体化した |
| agent env の拡張範囲 | 既存 4 変数に `KAJI_VERDICT_CONTRACT` を 1 つだけ追加する。`KAJI_VERDICT_PATH` 等の既存 `KAJI_*` は渡さない | Issue 本文 § 概要が「runner の agent context」を対象に含める（人間決定のスコープ）。#407 設計書の「agent へは 4 変数のみ」は当時の AI の仮定（同設計書 provenance 表）であり人間決定ではない。最小追加にとどめるのは AI の仮定。根拠: 制約・前提条件 § `KAJI_VERDICT_PATH` を agent env に入れない。検査先: review-design | `tests/test_tmp_env_propagation.py` の「agent は 4 変数のみ」assert を「4 変数 + 契約変数」に更新する |
| 原子的公開の方式 | 同じディレクトリの一時ファイル + fsync + `os.link`（上書きしない）。hard link 非対応なら exit 3 | AI の仮定。根拠: POSIX `link(2)` の EEXIST 意味論と `os.replace` が上書きすることの比較。検査先: review-code | 一時ファイル名を `verdict.yaml` と一致させない。一時ファイルは finally で削除する |
| runner の正規化保存の原子化 | `write_verdict_yaml` を `fsio.atomic_write` 経由にする（上書きは許す） | AI の仮定。根拠: Issue 本文 § OB が `write_verdict_yaml` の直接 `write_text` を問題として挙げており、serializer を共有するので揃える。検査先: review-code | `dump_verdict_yaml` を抽出して publish と共有する |
| 制御文字の扱い | publish でも #298 の規則（YAML 1.2 printable 外 → U+FFFD）を適用する | AI の仮定。根拠: 手書き経路では load 時に置換されていたので、publish 経路でも読み戻した値を同じにする（`verdict.py:_sanitize_yaml_control_chars`）。検査先: review-code | 置換後の値で round-trip 一致を確認する |
| wrapper banner | banner 行は維持し、`initial_prompt` だけを変える | AI の仮定。根拠: #527 の banner 終端契約（`interactive_terminal_common.py:43-45` とテスト）。検査先: review-design | — |
| 「同梱 agent skill」の範囲 | 本リポジトリの `.claude/skills/` の agent skill（7 件）。managed starter への追随は Release 後の `/update-starter` | AI の仮定。根拠: CLAUDE.md のライフサイクル「Starter 追随（Release 後）」。検査先: review-design | exec_script skill（`baseline-precheck` / `review-poll`）は対象外 |
| グリル | 不要 | 人間決定: § 重要判断 3 | — |

one-way door の確認: 公開 CLI と API（追加だけで既存の引数・exit code は不変）、永続化 schema（`verdict.yaml` は不変。
契約ファイルは新規の attempt 内部ファイル）、運用（workflow 遷移・解決順は不変）、スコープ（Issue の対象範囲内）の
いずれにも、人間の決定が必要な未決は見つからなかった。

## テスト戦略

> bug 固有ルール: 修正前に Red になる再現テストを定義する。OB に直接対応する実障害ログ（incident #565: run
> `261113020532` / `start`、run `261113105818` / `close` の `VerdictParseError`）があるため、escape clause に従い
> 実装前 Red の証跡としてこれを使う。そのうえで恒久回帰テストは必ず追加する。加えて、下記の prompt 契約テストと
> publish API のテストは修正前のコードでは実際に Red になる。

### 変更タイプ

- 実行時コード変更（runner の dispatch env、prompt、新 CLI、fsio、verdict serializer）と、skill / docs の手順変更

### 実行時コード変更の場合

#### Small テスト

- **回帰（#551）**: OB の YAML 文字列を `_parse_yaml_fields` / `load_verdict_yaml` に通すと `VerdictParseError` になる
  （手書き経路の失敗の固定）。同じ判定フィールドを正規化・検証・`dump_verdict_yaml` に通した結果は、parser で
  同じ値に戻る（共通処理経路の成功）。
- **round-trip**: `dump_verdict_yaml` → `_parse_yaml_fields` + `_validate` で元の値と一致する。値の例: 複数行、`: `、
  ` #`、`'` と `"`、`\`、Unicode（日本語・絵文字）、行頭の `- ` / `|` / `>`、`---` や `---END_VERDICT---`
  だけの行、80 桁を超える長い行、前後の空白（strip 後の値で比較）、制御文字（U+FFFD 置換後の値で比較）。
- **入力検証**: `on:` 外の status（marker 文法上は正しい `RETRY` を `on: {PASS, ABORT}` に渡す場合を含む）、空白だけの
  reason / evidence、suggestion が空の `ABORT` / `BACK` / `BACK_DESIGN` を拒否する。suggestion が空の `PASS` / `RETRY`
  は受理する。status は大文字小文字を区別して完全一致で照合する。
- **契約 model**: schema_version の不一致、余分なキー、空の `valid_statuses`、相対パスやファイル名違いの
  `verdict_path` を Pydantic 検証で拒否する。
- **prompt 契約**（修正前は Red）: `build_prompt` の出力に、status 候補を埋め込んだ `kaji verdict publish` コマンド、
  引用符付き heredoc、exit code ごとの対応指示が含まれる。`verdict_path` へ pure YAML を保存させる文言は含まれない。
  手順 1・2、block の例、書き方の規則、§ 実行期限は維持される。`verdict_path=None` でも成立する。
- **skill 手順の lint**: 同梱 agent skill（frontmatter に `exec_script` のない SKILL.md）に「`verdict_path` へ
  pure YAML を保存」型の手書き指示が残っていない。artifact の保存に触れる skill は `kaji verdict publish` を参照している。

#### Medium テスト（ファイル I/O・runner 内部結合）

- **原子的公開**: `os.link` を spy で包み、link の直前には `verdict.yaml` が存在せず、一時ファイルの中身は完成していて
  parse できることを確認する。publish 後に attempt dir に一時ファイルが残らない。
- **上書き禁止・1 回だけ**: 手書きの `verdict.yaml` があるとき、および 2 回目の publish では exit 2 になり、既存ファイルの
  内容が変わらない。link 直前に別プロセスが作った場合（`FileExistsError` を注入）も上書きしない。
- **別 attempt / 終了済み attempt の拒否**: `result.json` のある attempt、契約の `verdict_path` が別ディレクトリを指す場合、
  env 未設定、契約欠落・破損を exit 2 で拒否し、ファイルを作らない。
- **attempt 内での訂正**: 無効な status で exit 1 → `verdict.yaml` なし → 正しい値で再実行 → 成功。
- **I/O 失敗**: 一時ファイル書き込みや link で `OSError`（`EPERM` 等）を注入すると exit 3 になり、`verdict.yaml` も
  一時ファイルも残らない。
- **CLI**: `kaji_harness.commands.main` 経由で各 exit code、stderr の理由と許容 status 一覧、成功時の stdout 1 行を確認する。
- **runner の結合**: headless / interactive の両経路で、agent 起動前に `verdict-contract.json` ができている
  （`valid_statuses == list(step.on)`、step_id、attempt、`verdict_path`）。agent の env は 4 変数 + `KAJI_VERDICT_CONTRACT`
  で、exec / exec_script の env は変わらない（`tests/test_tmp_env_propagation.py` を更新）。
- **runner の e2e（モック agent）**: fake `execute_cli` / fake `execute_interactive_terminal` が、渡された env で
  `publish_agent_verdict` を呼ぶ → `resolve_verdict` の source は `artifact` で、正規化による再保存は起きない。手書き
  artifact 互換（有効な手書き → artifact 採用、壊れた手書き → fail-loud）の既存テストは変更せずに Green を保つ。
- **Herdr 完了検知**: 既存の fake Herdr テスト基盤で、ポーリング中に一時ファイルだけが存在する状態では完了せず、publish 後の
  最初のポーリングで 1 回だけ完了することを確認する。
- **`write_verdict_yaml` の原子化**: 正規化保存後の round-trip（既存テスト）が通り、`.tmp` が残らない。
- **テスト隔離**: autouse fixture により、テスト中は `KAJI_VERDICT_CONTRACT` が未設定になっている。

#### Large テスト（large_local: 実 subprocess、ネットワークなし）

- **headless 代表 workflow**: 既存の `tests/test_verdict_artifact_e2e_large_local.py` と同じ方式で実 `kaji run` を subprocess
  起動する。PATH 上の fake `claude` が、継承した env のまま実 `kaji verdict publish` を subprocess で実行する（値は #551 型の
  複数行・`: `・`#`・引用符・`\`・Unicode）。workflow が完了し、`verdict.yaml` が artifact source で採用され、run.log に
  `verdict_source=artifact` が残る。続けて、fake agent が最初に無効な status で exit 1 を受け、同じ attempt 内で訂正して
  成功する経路も検証する。
- **Herdr wrapper**: `tests/test_interactive_wrapper_skill_invocation_large_local.py` の方式で実 `wrapper.sh` を起動し、
  初期メッセージが publish を指示していて手書き指示を含まないこと、banner 行が維持されていることを確認する。
- 実 Herdr と実 LLM は使わない: Herdr 側で変わるのは「`verdict_path` が出現した時点で完了する」ことだけで、
  出現の原子性は publish 側で保証している。そのため Medium の fake Herdr ポーリングテストと wrapper の large_local テストで、
  回帰の検出に必要な観点を網羅できる。実 LLM の推論経路は新しい回帰情報を加えない（#220 の e2e と同じ判断）。

### 長時間 acceptance の配置

- 該当なし。nested full workflow や外部 agent を含む dogfood は設計に含めない。Large テストは fake agent による
  large_local（既存の同種テストで数秒〜十数秒）で、implement step の timeout の 25% を大きく下回る。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | ADR 005（artifact primary・解決順・fail-loud）、ADR 007（`verdict.yaml` の出現が完了トリガ）、ADR 012（`verdict.yaml` に識別情報を入れない）はいずれも維持し、契約ファイルは別ファイルにした。変わるのは agent の書き込み手段だけで、正本は skill-authoring.md / ARCHITECTURE.md に置く |
| docs/ARCHITECTURE.md | あり | § Verdict 判定機構「agent 側の書き込み順」に publish による原子的公開・契約ファイル・`KAJI_VERDICT_CONTRACT` を追記。パッケージ構成に `verdict_publish.py`・`commands/verdict.py`、`fsio.py` の説明に `atomic_publish_new` を追加 |
| docs/dev/skill-authoring.md | あり | § verdict 出力規約の手順 3 を publish に変更（exit code 別の対応、手書きしない）。§ コンテキスト変数 `verdict_path` の説明、§ 実行期限の「`verdict_path` を保存」、`KAJI_TMP_DIR` 節の agent env に契約変数を追記 |
| docs/dev/shared_skill_rules.md | あり | L62 の 3 経路の説明に、agent step の artifact は publish で公開することを追記 |
| docs/dev/workflow-authoring.md | あり | L380 の verdict-last の表現（「最後に `verdict.yaml` を保存」）を publish に合わせる。agent step env の記述を確認する |
| docs/cli-guides/interactive-terminal-runner.md / .ja.md | あり | wrapper が「`verdict.yaml` に pure YAML を書くよう指示」としている箇所（en L301 / ja L258）と、timeout のトラブルシュート（ja L406 等）を publish に更新 |
| docs/cli-guides/antigravity-cli-session-guide.md | あり | L165「Confirm the agent wrote pure YAML to the exact `verdict.yaml` path」を publish の確認に変更 |
| docs/reference/ | なし | 設定項目・規約の追加はない |
| README.md / llms.txt | なし | 「`verdict.yaml` を primary に読む」という記述は不変 |
| AGENTS.md / CLAUDE.md | なし | 規約変更はない |
| `.claude/skills/`（7 skill） | あり | 変更スコープ参照 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #568 本文 | https://github.com/apokamo/kaji/issues/568 | OB / EB / 完了条件 / 重要判断 1〜3 / 範囲外。責務境界と維持すべき契約の出典 |
| incident #565 | https://github.com/apokamo/kaji/issues/565 | run `261113020532` の `start` と run `261113105818` の `close` で同じ `VerdictParseError` の指紋（`line 4, column 2: evidence: \|`）。OB の実ログで、実装前 Red の代替証跡 |
| 現行 prompt | `kaji_harness/prompt.py:164-194` | 手順 3「pure YAML を `{verdict_target}` に保存する」が手書きを指示している（根本原因 1） |
| Herdr wrapper | `kaji_harness/assets/interactive-terminal/wrapper.sh:41-51` | 初期メッセージ「write only a pure YAML verdict file to this exact path」と banner 行 |
| banner 契約 | `kaji_harness/interactive_terminal_common.py:41-45` | `_WRAPPER_BANNER_END` が banner 行を terminal.log 解析の終端として固定している（#527） |
| Herdr 完了検知 | `kaji_harness/interactive_terminal_herdr.py:349-382`（ポーリング間隔 `_VERDICT_POLL_INTERVAL_SECONDS = 2`、L50） | `verdict_path.is_file()` が真になった時点で即座に完了する。既存テスト（`tests/test_interactive_terminal_herdr.py` L1129 等）は fake Herdr の呼び出し中に `verdict_path` を書いてこれを再現している |
| verdict 解決 / 検証 / 直列化 | `kaji_harness/verdict.py:155-191, 260-273, 592-620, 653-673, 764-849` | `_parse_yaml_fields` の必須項目、`_validate` / `_require_suggestion` の規則、`load_verdict_yaml` の fail-loud、`write_verdict_yaml` の直接 `write_text`、`resolve_verdict` の解決順 |
| runner dispatch | `kaji_harness/runner.py:582-697` | agent には `tmp_env` だけを渡し、`KAJI_*` context env は script-like step だけに渡している |
| `KAJI_VERDICT_PATH` の既存 consumer | `kaji_harness/artifact_verdict.py:29, 151-164`、`kaji_harness/scripts/review_poll_entry.py:229-230`、`kaji_harness/scripts/baseline_precheck.py:485` | agent env に入れると既存の挙動が変わる箇所（新しい変数名を選ぶ根拠） |
| atomic write の既存 helper | `kaji_harness/fsio.py:18-55` | `atomic_write` は `os.replace`（上書き）、`atomic_write_new` は O_EXCL だが書き込み中の状態が見える。どちらも「途中状態を見せず、上書きもしない」を満たさない |
| #407 設計書 | `draft/design/issue-407-feat-kaji-tmp-dir.md` provenance 表（L270） | 「agent 経路へ渡す env は 4 変数のみ」は AI の仮定だった（人間決定ではない）。agent シェルへの env 伝播の前例 |
| verdict 出力規約 | `docs/dev/skill-authoring.md` § verdict 出力規約 / § YAML block scalar の利用 | 3 経路・書き込み順・必須項目・#536 の literal block 規則 |
| Verdict 判定機構 | `docs/ARCHITECTURE.md` § Verdict 判定機構 | artifact → comment → stdout、壊れた artifact の fail-loud、正規化保存 |
| ADR 005 / 007 / 012 | `docs/adr/005-artifact-primary-verdict.md`、`docs/adr/007-interactive-terminal-runner.md`、`docs/adr/012-verdict-yaml-no-timestamp.md` | 維持する不変条件（artifact primary、出現が完了トリガ、`verdict.yaml` に識別情報を入れない） |
| POSIX `link(2)` | https://pubs.opengroup.org/onlinepubs/9799919799/functions/link.html | 要約: ERRORS の `[EEXIST]` により、path2 が既存のディレクトリエントリを指す場合 link() は失敗する（宛先を上書きしない）。成功すると、書き込み済みの inode を指す新しいエントリが 1 回の操作で作られる |
| Python `os.link` / `os.replace` | https://docs.python.org/3/library/os.html#os.link , https://docs.python.org/3/library/os.html#os.replace | `os.replace`: 宛先が存在すれば置き換える（上書き禁止の手段にはならない）。`os.link`: hard link を作る |
| PyYAML `safe_dump` | https://pyyaml.org/wiki/PyYAMLDocumentation | 既存 serializer の出力。表現できない文字は quoted style とエスケープで出力され、`safe_load` で元に戻る |
| argparse（`--opt=value`） | https://docs.python.org/3/library/argparse.html#option-value-syntax | 「For long options ... the option and value can also be passed as a single command-line argument, using = to separate them」。値が `-` で始まってもよい |
| Bash heredoc（引用符付き区切り語） | https://www.gnu.org/software/bash/manual/bash.html#Here-Documents | 「If any part of word is quoted ... the lines in the here-document are not expanded」。値をそのまま渡せる |
