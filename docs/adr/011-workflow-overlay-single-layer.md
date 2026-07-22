# ADR 011: workflow variant を単層 overlay（base + overrides）で表現する

## ステータス

提案（2026-07-23、Issue #331）— 本 ADR は research の成果物であり、この文書を含む PR を
人間が merge した時点で承認とみなす。既存 ADR は `Accepted` / `承認` のみを使うため、
merge 前は「提案」であることを本節で明示する。

## コンテキスト

kaji と実利用 repository（kamo2）で workflow YAML が重複しており、topology の source of
truth が壊れかけている。Issue #331 で実測（再実行手順は後述）した結果は次のとおり。

- kaji 10 本 + kamo2 23 本 = 33 ファイルに対し、`name` / `description` / `model` /
  `effort` / `agent` / `workdir` / `timeout` を除去して正規化した topology は **10 種類**
- kamo2 の **dev 系 13 本が全て同一 topology**（合計 2,897 行、`dev.yaml` 単体は 221 行）。
  kamo2 docs 系 4 本、kaji docs 系 4 本、kaji dev 系 3 本もそれぞれ同一 topology
- variant 間の差分行キーは `effort`(126) / `model`(80) / `name`(22) / `agent`(20) /
  `workdir`(6) と `description` 文言のみ。**`steps` / `cycles` / `on` 遷移 / step の追加削除 /
  順序変更の差はゼロ**

この状態では、topology に step を 1 つ足すたびに 13 ファイルを人手で同期する必要がある。
kamo2 #1327 で 21 本を 5 本へ整理した後も 23 本へ再増殖しており、「ファイル数を人手で制限する」
という運用ルールでは再発を防げないことが実績で示されている。

さらに #352 の人間決定により、`custom/**` の variant は pytest の inventory / contract /
invariant の対象外となった（`docs/dev/workflow-authoring.md` の所有権・品質保証の責務境界）。
その結果、custom variant が official base から topology 乖離しても自動検出する手段が現状ない。

### 実測の再現手順

計測スクリプト（Python 3 + PyYAML、約 25 行）の全文は Issue #331 の owner コメント
（2026-07-22）にある。repo には置かない（`experiments/` は `Makefile` の `SOURCES` に含まれ
ruff / mypy の恒久保守対象になるため、research 用途に対して費用が上回る）。2026-07-23 に
再実行し、33 ファイル・10 topology・kamo2 dev 系 13 本同一を再現済み。

## 決定

**GO**: workflow variant を、通常 workflow を `base` として参照し scalar 値のみを上書きする
**単層 overlay** で表現する方式を採用する。実装は本 ADR では行わず、後続 Issue へ分割する。

### schema

```yaml
# .kaji/wf/custom/dev/dev-thorough-fable.yaml
name: dev-thorough-fable          # 必須
base: ../../official/dev.yaml     # 必須。overlay ファイル起点の相対パス
description: |                    # 任意（上書き）
  dev の思考量重視 variant（fable）。
workdir: /path/to/dir             # 任意（上書き）
overrides:                        # 任意
  defaults:                       # 任意
    model: fable
    effort: xhigh
    timeout: 3600
  steps:                          # 任意。step id ごとの scalar 上書き
    review-code:
      agent: codex
      effort: high
```

`overrides` とその配下の `defaults` / `steps` はいずれも省略可。3 行（`name` / `base` /
`workdir`）だけの variant も書ける。

**overlay 表層は Pydantic model で検証する**。`base` / `overrides.defaults` /
`overrides.steps` は既存 validator が一切カバーしない新規の外部入力契約であり、`AGENTS.md`
Always-Apply Rules「外部入力は Pydantic で検証する」がそのまま適用される。
[ADR 010](010-pydantic-series-input-validation.md) が `SeriesConfig` / `SeriesMember` に
適用したのと同じ扱いであり、同 ADR が "Existing workflow parsing is unchanged" として既存
workflow parser の移行を対象外にした境界もそのまま維持する。すなわち **overlay 表層のみ
Pydantic、merge 後は既存の `Workflow` / `Step` dataclass へ渡す**。

ADR 010 と同じく overlay model は unknown field を禁止する。これにより「overlay ファイルへの
`steps:` / `cycles:` 直書き禁止」は個別の検査を書かずに schema で成立する。

### merge 規則

```
優先度: base の step 値 < overrides.defaults < overrides.steps.<id>
```

`defaults` の適用対象は **field 別**（人間確定方針）。

| field | 適用対象 |
|-------|----------|
| `agent` / `model` / `effort` | **LLM step のみ** |
| `timeout` | 全 step |

**LLM step の定義（Q2 の結論）**: 「base が `agent` を非 null で宣言している step」とする。
`agent: null` の明示は省略と同一に扱い、LLM step に含めない。根拠は次の 3 点。

1. exec step は `agent` を持てない（`kaji_harness/workflow.py:62` の `_EXEC_FORBIDDEN_KEYS` と
   `:177-183` の parse 時 fail-fast）。したがって exec step はこの定義で**構造的に除外**され、
   人間確定方針「`exec` step へ禁止フィールドを注入しない」が特例なしで満たされる。
2. `agent` を省略した skill step は、preflight L3
   （`kaji_harness/preflight.py:90-95`: `agent is None` かつ skill が `exec_script` 未宣言なら
   error）を通る限り必ず exec_script skill である。例: `.kaji/wf/official/dev.yaml:115-120` の
   `baseline`（`skill: baseline-precheck`、`agent` 無し）。ここへ `model` / `effort` を注入しても
   実行時は無視され、`preflight.py:96-102` の warning
   （"'agent' / 'model' / 'effort' are ignored"）を新たに量産するだけなので、適用対象から外す。
3. base YAML の表層値だけで決定でき、skill metadata の読み込みを要しない。これにより overlay の
   解決を `load_workflow()` 内で完結させられる（後述の制約）。

なお `defaults.timeout` は各 step の `timeout` を上書きするものであり、workflow top-level の
`default_timeout`（`Workflow.default_timeout`）とは別物である。overlay からは
`default_timeout` を変更しない。

**`overrides.steps.<id>` が exec step に `agent` / `model` / `effort` を指定した場合は
validation error とする（Q1 の結論）**。現行契約は「exec step が当該キーを持つこと」自体を
parse 時 error にしており（`workflow.py:177-183`、`validate_workflow` 側のミラーは
`workflow.py:514-527`）、overlay で silent skip すると「利用者が明示指定した値が無言で消える」
経路を新設することになる。fail-loud 側が現行契約と整合する。

一方、`overrides.steps.<id>` が **exec_script skill step**（`agent` 省略の skill step）に
`agent` / `model` / `effort` を指定した場合は error にせず、既存の preflight warning に委ねる。
明示指定に対して現行実装が warning で応じているのと同じ扱いにするためであり、`defaults` の
field 別 skip（無言）とは扱いを分ける。

`overrides.steps.<id>` の `<id>` が base に存在しない場合は validation error（人間確定方針
「削除・改名済み step を `overrides.steps` が参照した場合は validation error」）。

どちらの層にも無いキーは base の値をそのまま継承する（混在 base は混在のまま保存される）。

### 制約

- **単層のみ**: `base` の参照先は `base` キーを持たない通常 workflow に限る。多段は validation
  error。解決順序と循環検出の問題が構造的に消える。
- **scalar 上書きのみ**: top-level は `name` / `description` / `workdir`、step 単位は `agent` /
  `model` / `effort` / `timeout`（うち `agent` / `model` / `effort` は LLM step に限る）。
  step の追加・削除・順序変更、`on` 遷移、`cycles`、`skill`、`exec` の変更は schema で拒否する。
  topology 差分が必要なら別 base を書くのが唯一の答え。
- **`base` の信頼境界**: overlay ファイル起点の相対パスとし、`Path.resolve()` による symlink
  解決後の実体を含めて同一 kaji project root 内のみ許可する。root 外参照は validation error
  （人間確定方針）。包含判定には `Path.is_relative_to()` を用いる。
- **base 更新の反映**: version pin も埋め込みコピーも持たず、overlay は常に現在の base を継承する
  （人間確定方針）。
- **resolve は load 時に完結**: `load_workflow()` が `base` を検知したら base を読み、merge 済みの
  通常 `Workflow` を返す。`kaji_harness/models.py` の `Workflow` / `Step` は無変更、実行エンジン
  （`runner.py`）には一切手を入れない。

### validate 診断（混在 base × defaults の footgun 対策）

`defaults` が `agent` を指定せずに `model` を設定し、かつ base の **LLM step 間で `agent` が
混在している**場合、`overrides.steps` で `model` を再指定していない異種 agent の LLM step が
あれば **error** とする。診断メッセージは該当 step 名を列挙し、対処法（`defaults.agent` を
指定する / 当該 step を `overrides.steps` で再指定する）を提示する。

これは実在の footgun である。`.kaji/wf/official/dev.yaml` の LLM step 17 本は `claude` と
`codex` が混在しており、`defaults: {model: fable}` を素朴に適用すると codex step まで書き換えて
壊れる。kamo2 `dev.yaml`（LLM step 18 本）、`official/docs.yaml`（13 本）、
`official/local/dev-local.yaml`（10 本）も同じく `claude` / `codex` 混在である。単一 agent の
base（例: `official/incident.yaml` は全 5 step が `claude`）では診断は発火しない。

判定に model registry は不要で、base の `agent` 値の異同のみで決定的に決まる。`defaults` に
`agent` と `model` を両方指定した場合（agent 統一 variant）や `effort` のみの `defaults` は
無条件に安全なので診断対象外。

### resolved workflow の事前可視化（要件）

base と overrides を合成した**完全な** workflow を run 前に表示できる公開 CLI 機能を追加する
（人間確定方針）。要件を次のとおり確定する。

- **必須**: 解決後 `Workflow` の全 field を欠落なく表現できること。対象集合の正本は
  `kaji_harness/models.py:46-112`、すなわち `Workflow` の `name` / `description` /
  `execution_policy` / `requires_provider` / `default_timeout` / `workdir` / `cycles`
  （`CycleDefinition` の `name` / `entry` / `loop` / `max_iterations` / `on_exhaust`）と、
  全 step の `id` / `skill` または `exec` / `agent` / `model` / `effort` / `max_budget_usd` /
  `timeout` / `workdir` / `resume` / `inject_verdict` / `on`。
- **追加要件**: 各値の由来（base / `overrides.defaults` / `overrides.steps.<id>`）を併記できる
  こと。Issue #331 ユースケース 4（一括 override の実効値を run 前に確認したい）を満たすため。
  必須要件を削る根拠にはならない。
- **未確定**: CLI 名（`kaji validate --resolved` 等）と serialization 形式。two-way door として
  後続 Issue へ残す。
- 現行 `kaji validate` は成功時に `✓ <path>`、失敗時に error 列挙を出すのみで、resolved 表示は
  存在しない（`kaji_harness/commands/validate.py:51-96`）。

serialization を YAML にする場合は、次の 2 点が parse の非対称性として残ることに注意する。
`exec` は parse 境界で `shlex.split` により `list[str]` へ正規化されるため、表層が str だった
定義も list として出力される。`on` は YAML 1.1 で bare `on` が bool `True` に解釈されるため、
出力側で quote が必要になる（`workflow.py:188-194` が両表現を読む理由と同根）。

### run artifact への provenance 保存（要件）

run ごとに resolved workflow と、overlay / base の project-relative path を保存する
（人間確定方針）。要件を既存 layout に載る形で確定する。

- 保存先は `runs/<run_id>/` 直下、`run.log` と同階層（`kaji_harness/runner.py:64-104` の
  `allocate_run_dir`、`:938-949` の `RunLogger` 配置）。step ごとの
  `steps/<step_id>/attempt-NNN/` ではない。resolved 定義は run 単位で不変だからである。
- 内容は (a) 解決後 `Workflow` の全 field（resolved 可視化と同じ集合）、(b) overlay ファイルの
  project-relative path、(c) base ファイルの project-relative path。
- overlay を使わない run でも同じ artifact を書き、overlay path は null とする。分岐を減らし、
  障害調査時に「artifact が無い」と「overlay ではなかった」を区別できるようにするため。
- 差し込み点は `kaji_harness/commands/run.py:159-198`（`workflow_path.resolve()` 後に
  `load_workflow` し `WorkflowRunner` へ渡す箇所）と、run_dir を採番する `runner.py:938` 近傍。
- これは「base を pin しない」ことの代償を補う仕組みである。base が後から変わっても、当該 run が
  実際に何で動いたかは artifact から確定的に再構成できる。
- `kaji recover` がこの snapshot を実行定義として自動利用する変更は**本決定に含めない**
  （人間確定方針）。既存どおり明示された現在の workflow を使う。

## 影響

### 後方互換性と移行

- `base` キーを持たない既存 YAML は従来経路のまま動く。`load_workflow()` は
  `path.read_text()` → `yaml.safe_load` → `_parse_workflow` の単純経路であり
  （`kaji_harness/workflow.py:17-33`）、追加されるのは「`base` があるか」という**単一分岐**
  だけである。
- これは [ADR 008](008-no-backward-compat-layer.md)（後方互換レイヤを提供しない）に抵触しない。
  ADR 008 が禁じるのは「旧フォーマット読み取り器・フォールバック・バージョン分岐」であり、
  ここで増えるのは新フォーマットの受理であって、旧フォーマットを別経路で読み直す層ではない。
  既存 33 本の YAML は 1 バイトも変えずに現行と同一の parse 結果になる。
- **移行はファイル単位で任意**。既存 variant を一括変換しない。overlay へ移すかは各 repository の
  判断に委ねる。
- **overlay ファイルを overlay 非対応の kaji が読んでも silent に誤動作しない**ことを実測で確認した。
  overlay 相当の YAML（`name` / `base` / `overrides` のみ）を現行 `kaji validate` に渡すと
  `'execution_policy' is required` で exit 1 になる。`kaji run` も
  `runner.py:802` の `preflight_workflow` 経由で `validate_workflow` を通るため同様に fail する
  （`workflow.py:493` の "Workflow must have at least one step" も同じ経路の backstop）。
  すなわち「新形式のファイルを古い kaji が 0 step の workflow として黙って実行する」経路は存在しない。

### builtin / custom / local / GitHub / packaging / starter

- **builtin**: workflow YAML は wheel に同梱されない。`pyproject.toml:59-60` の
  `package-data` は `assets/interactive-terminal/wrapper.sh` のみで、package から workflow を
  解決する経路は存在しない。したがって「builtin base を package 内から参照する」問題は発生しない。
  base はあくまで project root 内のファイルである。
- **custom → official 参照の向き**: overlay の `base` は custom（利用者所有）から official
  （kaji 所有・Release で更新されうる）への一方向参照になる。これは
  `docs/dev/workflow-authoring.md:29-92` の所有権境界と矛盾しない。利用者が自分のファイルから
  kaji 提供物を参照する形であり、逆向き（official が custom を参照する）は発生しない。
  代償として、official base の変更が custom overlay の実効定義を無告知で変えうる
  （後述「帰結」）。
- **local provider**: `.kaji/wf/official/local/*.yaml` も通常 workflow であり、base として
  参照できる。`requires_provider` は overlay の上書き対象外（scalar 上書きの許可リストに
  含めない）なので、local base を GitHub 用に流用する誤りは schema で防がれる。
- **GitHub provider**: 同上。provider 差は topology 差であり、別 base で表現する。
- **`make validate-workflows`**: `git ls-files -- '.kaji/wf'` の YAML を `kaji validate` に
  渡す（`Makefile:20-25`）。overlay ファイルも tracked なのでそのまま検証対象になる。base が
  未 tracked / 存在しない場合はここで fail-loud になる。
- **starter**: `docs/guides/python-starter.md:14-16` のとおり starter は今も flat
  `.kaji/wf/` layout で 5 本の YAML をコピー配布しており、kaji 本体の `official/` /
  `custom/` 分割（#352）に追随していない。flat layout のままでは
  `base: ../../official/dev.yaml` が解決できない。**starter で overlay を使うには、先に
  starter-sync で official / custom 分割へ追随させる必要がある**。これは overlay 実装の
  前提条件であり、後続 Issue のスコープ境界に明記する。

## 棄却した選択肢

- **現状維持 + 同期チェック**（topology 正規化ハッシュ比較を `make validate-workflows` に追加）:
  実装コストはほぼゼロだが、variant ファイルの増殖そのもの（kamo2 dev 系 13 本 2,897 行）を
  解決しない。乖離を検出できても、13 ファイルへの手作業同期は残る。overlay の代替ではなく、
  overlay が構造的に不能にする問題を事後検出するだけである。
- **汎用継承**（`steps` / `cycles` / `on` の構造 merge、step の追加削除、循環検出）: 実測で
  variant 間の topology 差分が**ゼロ**であり、需要が存在しない。導入すれば解決順序・循環検出・
  部分 merge の semantics という恒久的な複雑さを、使われない機能のために抱えることになる。
- **外部 generator**（テンプレートから YAML を生成）: 同上で需要不在。加えて生成物と生成元の
  二重管理、生成漏れの検出という新しい失敗モードを持ち込む。
- **selector 型の条件置換**（`where: {model: opus}` 等）: `defaults` + 例外 step の再指定で
  同等のことが書ける。selector の照合規則という第 2 の言語を増やす価値がない。
- **多段継承**: 単層に限れば解決順序と循環検出の問題が構造的に消える。多段が必要になる状況は
  実測にない。
- **profile の組み合わせ自動合成**（thorough × fable 等）: overlay ファイル 1 本が 5〜20 行で
  済むため、組み合わせ爆発を自動合成で解く必要がない。複製コストが十分小さい。

## 帰結

### 得られるもの

- kamo2 dev 系: 13 ファイル 2,897 行 → base 1 本（221 行）+ overlay 12 本（各 5〜20 行）≈ 500 行。
- topology への step 追加が「13 ファイル編集」→「1 ファイル編集」になる。
- #352 で pytest の inventory / contract / invariant 対象外となった custom variant の topology
  乖離が**構造的に不能**になる（overlay は topology を持てない）。失われた保証が実質回復する。
- variant を増やすコストが下がるため、「ファイル数を人手で制限する」という守れない運用ルールに
  依存しなくてよくなる。

### 代償

- `load_workflow()` の責務が「1 ファイルを読む」から「必要なら 2 ファイルを読み merge する」へ
  拡大する。単一ファイル読み取りという単純さは失われる。
- overlay 特有の failure mode が新設される: base が見つからない / project root 外 / base 自身が
  overlay（多段）/ `overrides.steps` が存在しない step を参照 / 混在 base × agent 無指定
  defaults。いずれも validation error として fail-loud にするが、診断文言の質がそのまま利用者の
  体験になる。
- **base を pin しないため、official base の変更が custom overlay の実効定義を無告知で変えうる**。
  これは意図した設計（人間確定方針: 常に現在の base を継承）だが、代償は run artifact の
  provenance 保存で補う。すなわち「将来の run が変わること」は受け入れ、「過去の run が何で
  動いたか分からなくなること」は artifact で防ぐ。
- starter は flat `.kaji/wf/` layout のままでは overlay を使えない。starter-sync による
  official / custom 分割への追随が前提条件になる。
- 定義を読むのに 2 ファイルを突き合わせる必要が生じる。resolved 表示 CLI はこの代償に対する
  必須の緩和策であり、「後で足す」ものではない。

## 判断を変える条件

次のいずれかを観測したら、この決定を再検討する。

- **topology 差分を要する variant が実在するようになった場合**（step の追加削除・`on` 遷移の
  差し替えを必要とする variant が 1 本でも出現）。scalar 上書きのみという制約が現実に合わなくなる。
  計測スクリプトの再実行で機械的に検出できる。
- **overlay の解決に起因する障害が発生した場合**（base 変更による意図しない実効定義の変化で
  run が失敗する、診断が原因特定に役立たない、等）。provenance artifact と診断文言の設計を
  見直すか、pin を導入するかの再判断が必要になる。
- **overlay 化しても variant ファイル数が減らない場合**（overlay 移行後も 13 本相当が残り、
  各 overlay が 20 行を大きく超える）。期待効果の前提（差分は scalar のみ、5〜20 行）が崩れる。
- **exec step / exec_script skill の契約が変わった場合**。LLM step の定義（base が `agent` を
  宣言している step）は現行の parse 契約と preflight L3 に依存しているため、そこが変われば
  `defaults` の適用対象を定義し直す必要がある。

## 未確定事項

後続 Issue で決める。本 ADR では確定しない。

- resolved 表示 CLI の名前と serialization 形式（`kaji validate --resolved` は候補であって決定
  ではない）。
- run artifact の具体 schema（ファイル名、JSON / YAML、由来情報の表現）。
- overlay Pydantic model の命名と、Pydantic `ValidationError` を `WorkflowValidationError` へ
  正規化する位置・エラー集約の粒度。
- `base` の project root 判定の実装（`KajiConfig.repo_root` を使うか、`validate` が持つ
  `pyproject.toml` 探索を使うか）と、診断メッセージの文言。

Pydantic を使うか否か自体は `AGENTS.md` と ADR 010 により確定済みのため、ここには置かない。
後続 Issue の分割案（公開 IF / loader / validation / CLI / artifact / tests / docs）は backlog
であり時間とともに陳腐化するため、ADR ではなく Issue #331 のコメントに置く。
