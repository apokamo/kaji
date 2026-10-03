# [設計] workflow incident ラベルを kaji 既定名へ移行する（kaji 自身の改名と利用側向け移行手順）

Issue: #457

## 概要

kaji 自身のリポジトリの incident ラベル 8 件を `kaji:` 接頭辞付きの既定名に揃える。対象は
`.github/labels.yml`、`.kaji/config.toml`、docs、`incident-*` skill。あわせて、利用側リポジトリ向けの
移行手順を `docs/dev/incident-labels.md` に追加する。`incident-*` skill の前提ガードは、固定の
`incident` ではなく設定値 `[incident] kind_label` で対象 Issue を判定するように改める。

## 背景・目的

#434（v0.21.0）でラベルの既定名を `kaji:` 接頭辞付きに変更したが、移行は行っていない
（CHANGELOG `[0.21.0]` § BREAKING CHANGE「Not migrated by this change」）。現状の問題は次の 3 点である。

1. kaji 自身は `.kaji/config.toml:19-22` で旧名 3 つを明示している。一方、`.github/labels.yml`、
   GitHub 上のラベル、docs、skill は旧名のままで、既定値と実運用を二重に管理している。
2. `incident-investigate` の前提ガードは `incident` ラベルの有無を固定で確認している
   （`.claude/skills/incident-investigate/SKILL.md:72`、ABORT 条件は L154）。`incident-review` も同様
   （L54）。そのため、既定名で運用する利用側リポジトリでは、第 2 層の調査が前提ガードで ABORT する。
3. 利用側向けの移行手順は CHANGELOG の「旧名を維持する設定例」しかない。

### ユースケース

- **kaji の保守者**として、kaji 自身の incident ラベルと設定を既定名に揃え、`[incident]` の旧名指定を
  撤去したい。既定値と実運用の二重管理をなくすため。
- **kaji を組み込んだリポジトリの管理者**として、旧名の incident ラベルを既定名へ移す手順
  （検出方法・改名コマンド・注意点）を docs で確認したい。既存 incident の履歴を保ったまま既定名へ
  切り替えるため。
- **設定でラベル名を変えた利用側リポジトリの管理者**として、`incident-investigate` などの第 2 層 skill
  に、設定したラベル名で対象 Issue を判定してほしい。

### 代替案（skill が `kind_label` を読む方法）

| 案 | 内容 | 採否 | 理由 |
|----|------|------|------|
| A | `kaji config incident-kind-label` のような CLI subcommand を追加する | 不採用 | `kaji_harness/` の変更が必要になる。Issue のスコープ境界（人間決定）で harness の変更は対象外 |
| B | skill の bash 手順で `python3` + 標準ライブラリ `tomllib` を使い、`.kaji/config.toml` の `[incident] kind_label` を読む。キーがなければ既定値を使う | **採用** | harness を変更せずに決定的に動き、Large（large_local）テストで手順そのものを subprocess 実行して検証できる |
| C | agent に `.kaji/config.toml` を読ませ、自由に解釈させる | 不採用 | 非決定的で、テストで固定できない。キーがないときに既定値へ倒す規則を agent ごとに再解釈させることになる |
| D | `kaji` の実行 venv の Python から `KajiConfig.discover()` を import する（shebang からインタプリタを辿る） | 不採用 | インストール方法（uv tool / pipx / venv）で経路が変わり壊れやすい |

## インターフェース

本 Issue は公開 CLI・公開 API・config schema を変更しない（`[incident]` の key・型・既定値・検証は
#434 の契約のまま）。変わるのは次の 4 点である。

### 入力

| 入力 | 形式 | 用途 |
|------|------|------|
| `.kaji/config.toml` の `[incident] kind_label` | TOML の str（任意。省略時は既定値 `"kaji:incident"`） | incident-* skill の前提ガードの判定基準 |
| インシデントイシューの labels / body | `kaji issue view <id> --json labels,body` | 前提ガードの判定対象 |

### 出力

1. **GitHub ラベル定義**: `.github/labels.yml` の incident 8 件の `name` が既定名になる。
   `color` / `description` は変えない。そのため、in-place 改名後に `labels-sync` を実行しても `Unchanged` になる。
2. **kaji 自身の設定**: `.kaji/config.toml` から `[incident]` セクションとその説明コメントを削除する。
   `KajiConfig.discover()` の `config.incident` は `IncidentConfig()`（既定値）と等しくなる。
3. **skill の前提ガード**: `incident-investigate` の Step 0、`incident-review` の Step 0、
   investigate の ABORT 条件で、`kind_label` を解決して判定する。`incident-report` の処遇メニューは
   既定名（推奨名）で示し、設定を変えている場合の読み替えを注記する。
4. **docs**: `incident-labels.md` / `labels.md` / `workflow_guide.md` を既定名で記述する。
   `incident-labels.md` に「旧名からの移行手順」の節を追加する。`CHANGELOG.md [Unreleased]` に追記する。

### 使用例（skill 内の前提ガード。疑似コード）

`incident-investigate/SKILL.md` § 全 incident-* skill 共通ルールに、「`kind_label` の解決」を 1 箇所だけ
定義する。各 skill はそこを参照する。

見出し「`kind_label` の解決」の直後の最初の bash ブロックには、**解決処理だけ**を置く。
成功時は `KIND_LABEL` にラベル値だけが入り（stdout にはラベル値以外を出さない）、終了コードは 0 になる。
失敗時は理由を stderr に出して**非ゼロで終了**し、後続のラベル照合へ進まない。失敗分岐で `echo` などを
実行して終了コードが 0 に変わる書き方はしない。

```bash
# kind_label の解決（cwd から親方向に .kaji/config.toml を探す。KajiConfig.discover と同じ探索規則）
if ! KIND_LABEL="$(python3 - <<'PY'
import pathlib, sys, tomllib
cwd = pathlib.Path.cwd().resolve()
for d in (cwd, *cwd.parents):
    cfg = d / ".kaji" / "config.toml"
    if cfg.is_file():
        break
else:
    sys.exit("kaji config not found: .kaji/config.toml")
data = tomllib.loads(cfg.read_text(encoding="utf-8"))  # 不正 TOML は例外 → 非ゼロ終了
section = data.get("incident", {})
if not isinstance(section, dict):
    sys.exit(f"{cfg}: [incident] must be a table")
label = section.get("kind_label", "kaji:incident")
if not isinstance(label, str) or not label.strip():
    sys.exit(f"{cfg}: incident.kind_label must be a non-empty string")
print(label)
PY
)"; then
    echo "ABORT: kind_label を解決できない（理由は上の stderr）" >&2
    exit 1
fi
```

前提ガードは、上のブロックが成功したときだけ別のブロックで行う。

```bash
# 前提ガード（Step 0。KIND_LABEL の解決に成功した後）
kaji issue view [issue_id] --json labels,body
#  - labels[].name に KIND_LABEL と一致するもの（大文字小文字を区別しない）がある
#  - 本文 1 行目に identity marker（<!-- kaji-incident: ... -->）がある
#  いずれかを欠く → ABORT
```

### エラー時の挙動

| 状況 | 挙動 |
|------|------|
| `.kaji/config.toml` が見つからない | 解決ブロックが非ゼロで終了し、ラベル照合へ進まない → skill は ABORT する（既定値へ黙って倒さない） |
| TOML として不正 / `[incident]` が table でない / `kind_label` が空または文字列でない / `python3` がない、または 3.11 未満で `tomllib` がない | 同上（非ゼロ終了 → ABORT）。suggestion に原因（stderr）を記載する |
| `[incident]` がない、または `kind_label` キーがない | 既定値 `kaji:incident` を使う（`IncidentConfig` の既定と同じ） |
| 対象 Issue に `KIND_LABEL` ラベルがない / identity marker がない | 従来どおり ABORT（非インシデント） |

## 制約・前提条件

- **harness コードは変更しない**（Issue § スコープ境界・人間決定）。旧名の検出警告、旧名との
  二重検索、互換期間は導入しない（#434 決定事項、ADR 008）。
- `[incident]` は tracked `.kaji/config.toml` からだけ読み、overlay（`config.local.toml`）は無視する
  （`docs/reference/configuration.md` § `[incident]`）。skill の解決手順も tracked config だけを読む。
- skill の解決手順は値を読むだけで検証しない。値の検証（空・カンマ・重複の禁止など）は、`kaji run`
  起動時の `KajiConfig._parse_incident` が担う。不正な設定では workflow 自体が起動しない。
- skill 内の既定値リテラル `kaji:incident` は、`IncidentConfig.kind_label`（`kaji_harness/config.py:126`。
  既定値の正本）を写したものになる。乖離は Large（large_local）テストで検出する（§ テスト戦略）。
- kaji は `kind_label` 等をラベルとして作成しない。設定したラベルはリポジトリに存在している必要がある。
  存在しない場合、起票・遷移は GitHub に拒否され、記録は WARNING を出して欠落する（fail-open。
  CHANGELOG `[0.21.0]`）。
- `kaji run` は起動時に一度だけ config を読み、`config.incident` を recovery handler に渡す
  （`kaji_harness/commands/run.py:286`）。config は起動 cwd から親方向に探索される
  （`KajiConfig.discover`）。そのため、実行中の run と、古い config を持つ checkout から起動した run は、
  旧名のままラベルを付ける。
- `labels-sync.yml` はラベルの追加と更新だけを行い、削除しない。`.github/labels.yml` が main に push
  されたとき、手動実行時、週次 cron（月曜 00:00 UTC）で動く。存在しない名前のラベルは空で作成する。
- GitHub のラベル改名は、そのラベルが付いた既存の Issue / PR への付与を保つ。改名先の名前が既に
  存在すると改名は失敗する。

### 変更スコープ

| ファイル | 変更 |
|----------|------|
| `.github/labels.yml` | incident 8 件の `name` を既定名へ変更。先頭コメントの数（29）、セクションコメント、color、description は維持 |
| `.kaji/config.toml` | `[incident]` セクションと直前の説明コメント（L17-22）を削除 |
| `.claude/skills/incident-investigate/SKILL.md` | 共通ルールに「`kind_label` の解決」を追加。Step 0（L72）と ABORT 条件（L154）を `kind_label` 基準に変更 |
| `.claude/skills/incident-review/SKILL.md` | Step 0（L54）を、共通ルールの解決手順と labels 取得による `kind_label` 基準の判定に変更 |
| `.claude/skills/incident-report/SKILL.md` | 処遇メニューのラベル名を既定名にし、設定名への読み替えを注記 |
| `docs/dev/incident-labels.md` | 既定名で全面記述。「kaji 自身は旧名を明示」の段落を削除。「旧名からの移行手順」の節を追加 |
| `docs/dev/labels.md` | incident 節の表を既定名に変更 |
| `docs/dev/workflow_guide.md` | 第 1 層の説明（L204-213）を設定 key + 既定名で記述 |
| `docs/reference/configuration.md` / `.ja.md` | 旧名維持の設定例は残す（正当な旧名の併記）。移行手順の節へのリンクを 1 行追加 |
| `CHANGELOG.md` | `[Unreleased]` に追記 |
| `tests/workflows/test_incident_labels.py`（新規） | § テスト戦略の Medium テスト（ファイル読み取り） |
| `tests/workflows/test_incident_kind_label_large_local.py`（新規） | § テスト戦略の Large テスト（解決手順の subprocess 実行） |

## 方針

### 1. ラベル定義・設定の既定名化

- `labels.yml` は `name` だけを差し替える（旧名 → 既定名の対応は § 移行手順の表）。
  `labels-sync` の Validate step は `:` の前でカテゴリを数えるため、ログ上のカテゴリ名が `incident`
  から `kaji` に変わるが、検証は通る（ログ表示だけの変化）。
- `config.toml` の `[incident]` を削除し、既定値で運用する。

### 2. skill の前提ガード（`kind_label` 基準）

- 解決手順は `incident-investigate` § 全 incident-* skill 共通ルールに**1 箇所だけ**定義する
  （§ インターフェース「使用例」）。`incident-review` は既に同節を参照する構造になっており、その参照を使う。
- 判定は `labels[].name` と `KIND_LABEL` の大文字小文字を区別しない一致で行う。identity marker の
  確認はそのまま残す。
- `incident-report` には前提ガードがないため、変更は処遇メニュー表のラベル名だけである。設定 key を
  持つのは `initial_status_label` / `transient_label` / `kind_label` の 3 つで、残り 5 つは推奨名である
  （`incident-labels.md` の既存の整理に従う）。表の下に「`[incident]` で名前を変えている場合は設定名を、
  設定 key のないラベルはリポジトリで採用している名前を使う」と注記する。

### 3. docs

- `incident-labels.md` の一覧・遷移説明・照合規則・処遇メニューを既定名で書き直す。
  「ラベル名は設定で変更できる」節の対応表を「既定名 | 設定 key」に変え、「kaji 自身のリポジトリは
  …旧名を明示している」段落を削除する。
- 「旧名からの移行手順」の節（見出し `## 旧名からの移行手順`）を追加する。旧名を書いてよいのは、
  この節と CHANGELOG・`configuration.md` の旧名維持例だけである。節の内容は Issue 完了条件の 5 要素で、
  下記 § 移行手順を利用側向けに一般化したもの。
  1. 影響の判定方法（`gh label list --search incident` と `[incident]` の有無）
  2. in-place 改名の 8 コマンド
  3. 旧名を維持する場合の `[incident]` 設定例
  4. `labels-sync` のような宣言的ラベル同期との順序の注意
  5. 記録欠落の窓を最小化する手順と、新名ラベルが先にできた場合の復旧手順（ラベルの組ごとの状態判定、
     Issue / PR 両方の付与確認、空なら削除して改名、使用済みなら付け替えてから旧名を削除）

### 4. 移行手順（kaji 自身。記録欠落の窓を最小化する順序）

旧名 → 既定名の対応（8 件）:

| 旧名 | 既定名 |
|------|--------|
| `incident` | `kaji:incident` |
| `incident:investigating` | `kaji:incident:investigating` |
| `incident:mitigated` | `kaji:incident:mitigated` |
| `incident:resolved` | `kaji:incident:resolved` |
| `incident:cause:internal` | `kaji:incident:cause:internal` |
| `incident:cause:upstream` | `kaji:incident:cause:upstream` |
| `incident:cause:environment` | `kaji:incident:cause:environment` |
| `incident:cause:transient` | `kaji:incident:cause:transient` |

**順序の根拠**

- labels.yml の変更を先に main へ入れると、`labels-sync` が新名ラベルを空で作る。その後の
  `gh label edit --name` は改名先の名前が既にあるため失敗する。したがって**改名が先**である。
- 改名してから、旧名を指定した config が残る間は、kaji が存在しない旧名で起票・検索するため記録が欠落する。
  逆に config を先に切り替えると、存在しない新名を使うことになり、やはり欠落する。
  欠落の窓は「改名」から「`kaji run` を起動する checkout に新しい config が反映される」までであり、
  これを詰める。
- labels.yml・config.toml・skill・docs は**同じ PR（同じ merge commit）**に入れる。こうすると
  「labels.yml だけ」「config だけ」が main に先行する状態は起きない。

**手順**（本 PR の承認後、`/issue-close` による merge の直前に人間が行う。実施結果の確認は Issue の
「ワークフロー完了後の確認項目」に従う）

**付与の列挙**（以下の手順で「付与」と書くときは常にこの方法を使う）: ラベルの削除は Issue と PR の
両方から付与を外す（GitHub Docs: Managing labels）。そのため、付与の確認は closed を含む Issue と PR の
両方を対象にする。

```bash
# ラベル L が付いた Issue / PR の番号集合（closed を含む）
labeled() {
  { gh issue list --state all --label "$1" --limit 1000 --json number --jq '.[].number'
    gh pr list --state all --label "$1" --limit 1000 --json number --jq '.[].number'; } | sort -n
}
```

1. **事前確認**: 実行中の `kaji run` がないことを確認し、手順 6 が終わるまで新しい run を起動しない。
   週次 cron（月曜 00:00 UTC = 09:00 JST）をまたがない時間帯を選ぶ。
2. **付与の保存**: 対応表の 8 組それぞれについて、旧名の付与集合を `labeled <旧名> > before-<旧名>.txt`
   で保存する（`incident` は改名時点で 14 件の見込み）。手順 6 で番号集合として照合する。
3. **組ごとの状態判定と処理**: `gh label list --search incident --limit 100 --json name` で、
   8 組それぞれの旧名・新名の有無を見て、次の表に従い**組ごとに**処理する。全組を無条件に改名せず、
   移行済みの組は飛ばす。途中で失敗して再開するときも、この手順 3 を最初からやり直せばよい
   （判定は現在の状態だけで決まる）。

   | 状態 | 判定 | 処理 |
   |------|------|------|
   | A. 未移行 | 旧名あり・新名なし | `gh label edit "<旧名>" --name "<新名>"`（通常はこの経路。8 コマンドは下記） |
   | B. 移行済み | 旧名なし・新名あり | 何もしない |
   | C. 空の新名が先行 | 両方あり、`labeled <新名>` が空 | `gh label delete "<新名>" --yes` → A と同じ改名 |
   | D. 新名が使用済み | 両方あり、`labeled <新名>` が空でない | 付け替え → 確認 → 旧名を削除（下記） |
   | E. 異常 | 両方なし | 処理を止めて原因を調べる（merge しない） |

   **状態 A の 8 コマンド**（1 件でも失敗したら止め、原因を確認してから手順 3 をやり直す）:
   ```bash
   gh label edit "incident" --name "kaji:incident"
   gh label edit "incident:investigating" --name "kaji:incident:investigating"
   gh label edit "incident:mitigated" --name "kaji:incident:mitigated"
   gh label edit "incident:resolved" --name "kaji:incident:resolved"
   gh label edit "incident:cause:internal" --name "kaji:incident:cause:internal"
   gh label edit "incident:cause:upstream" --name "kaji:incident:cause:upstream"
   gh label edit "incident:cause:environment" --name "kaji:incident:cause:environment"
   gh label edit "incident:cause:transient" --name "kaji:incident:cause:transient"
   ```

   **状態 D の処理**:
   1. `labeled <旧名>` の各番号について、Issue なら `gh issue edit <番号> --add-label "<新名>" --remove-label "<旧名>"`、
      PR なら `gh pr edit <番号> --add-label "<新名>" --remove-label "<旧名>"` を実行する
      （新名の付与と旧名の除去を同じコマンドで行う）。
   2. 終了条件: `labeled <旧名>` が空になり、かつ `before-<旧名>.txt` の全番号が `labeled <新名>` に
      含まれる。満たさなければ削除せず、残った番号に 1 を繰り返す。
   3. 終了条件を満たした後で `gh label delete "<旧名>" --yes` を実行する。

4. **直ちに merge**: `/issue-close 457` で PR を `--no-ff` merge する。merge 後の `labels-sync` は、
   既定名が既にあり color / description も同じなので、8 件とも `Unchanged` になる（`Created` は 0 件）。
5. **起動 checkout の更新**: `kaji run` を起動する checkout（kaji では main worktree）を `git pull` し、
   `KajiConfig.discover().incident == IncidentConfig()` を確認する。merge 前に分岐した feature
   worktree から `kaji run` を起動しない（その worktree の config は旧名のまま）。
6. **確認**:
   - `gh label list --search incident` で既定名 8 件・旧名 0 件。
   - 8 組それぞれについて、`before-<旧名>.txt` の全番号が `labeled <新名>` に含まれる
     （件数ではなく番号集合で照合する。改名後の新規起票が件数の不足を隠さないようにするため）。
   - `labels-sync` の実行ログで `kaji:incident*` の `Created` が 0 件である。

**欠落の扱い**: 手順 3〜5 の間に終了した run があり、その triage が WARNING（ラベル拒否）を出した
場合は、その incident を手動で起票する。Issue 番号・日時・手動起票の有無を #457 にコメントで記録する
（欠落がなくても「なし」と記録する。Issue の事後確認項目）。

**merge 後の復旧**: merge が cron をまたぎ、cron が旧 labels.yml で旧名ラベルを空で作り直した場合は、
`labeled <旧名>` が空であることを確認してから `gh label delete "<旧名>" --yes` で削除する。
付与がある場合は、状態 D の処理（付け替え → 終了条件の確認 → 削除）に従う。

### 5. 利用側向け手順（docs）での一般化

- kaji 固有の部分（`/issue-close`、`labels-sync.yml`、14 件）は、利用側の言葉に置き換える。
  具体的には「`.kaji/config.toml` の `[incident]` 削除を default branch へ commit」「宣言的ラベル同期を
  使っている場合は、その定義の更新を改名より後にする」とする。
- 改名・状態判定・復旧（付与の列挙、組ごとの状態 A〜E、状態 D の付け替えと終了条件）は § 方針 4 と同じ内容を
  利用側の言葉で記載する。リポジトリ名・件数は固定値にせず、`before-<旧名>.txt` との番号集合の照合で確認する。
- 影響の判定は 2 軸で場合分けする。

| `[incident]` | GitHub 上のラベル | 状態 | 対応 |
|--------------|-------------------|------|------|
| 旧名を指定 | 旧名 | 旧名で正常運用中 | 維持するなら何もしない。移行するなら改名 → `[incident]` 削除 |
| なし（既定） | 旧名 | **影響あり**（既定名のラベルがなく記録が欠落している） | 直ちに改名する（設定変更は不要） |
| なし（既定） | 既定名 | 移行済み | なし |

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 既定名・二重検索なし・kaji 自身の移行をフォローアップで行う | 既定名へ移行し、旧名との互換検索は持たない | 人間決定: #434 本文「決定事項」1〜3（https://github.com/apokamo/kaji/issues/434 ）。本 Issue「重要判断」表の 1 行目。ADR 008 | `[incident]` を削除して既定値で運用。互換コードを追加しない |
| 移行方式 | `gh label edit --name` による in-place 改名 | 人間決定: 本 Issue「重要判断」表の 2 行目（session `fc7edc19-…` の AskUserQuestion 回答。review-ready コメントで tool_use_id `toolu_013VSBwHxmaJGwyjoKmQ2xYy` を照合済み） 8 コマンドの具体化。組ごとの状態判定（A〜E）と再開条件。改名先が既にある場合の復旧（Issue / PR 両方の付与を番号集合で確認してから削除） |
| 記録欠落の扱い | 欠落を許容し、手順で最小化。欠落分は手動起票 | 人間決定: 本 Issue「重要判断」表の 3 行目（同 session） | 順序（改名 → 直ちに merge → 起動 checkout の更新）、同一 PR に束ねる、run 停止、cron 回避の詳細化（§ 方針 4） |
| 利用側の範囲 | docs（移行手順）と skill（前提ガードの設定値参照）まで。harness は変更しない | 人間決定: 本 Issue「重要判断」表の 4 行目と § スコープ境界 | skill の変更範囲を investigate / review のガードと report の表に限定 |
| skill が設定値を読む方法 | `python3` + `tomllib` で tracked `.kaji/config.toml` を親方向に探して `kind_label` を読み、キーがなければ既定値 `kaji:incident` を使う | **AI の仮定**（Issue で「設計で決める two-way door」と明記）。根拠: harness 変更が対象外なので CLI 追加（案 A）は使えない。決定的かつ実行テスト可能。`requires-python >= 3.11`。検査先: review-design、Large（large_local）テスト、review-code / PR review | 共通ルールに 1 箇所だけ定義。解決ブロックは成功時にラベル値だけを出力し、config 不在・不正 TOML・非 table・非文字列では非ゼロ終了してラベル照合へ進まない（fail-loud） |
| ラベル照合の大文字小文字 | 区別しない | **AI の仮定**。根拠: `[incident]` の検証が 3 ラベルの相異を大文字小文字を区別せずに判定している（`docs/reference/configuration.md` § `[incident]`）。検査先: review-design / review-code | Step 0 の判定文に明記 |
| 旧名の残存を確認する grep | § テスト戦略「変更固有検証」の 2 コマンド | **AI の仮定**（完了条件が「設計書で定める」と委任）。検査先: review-design / review-code / final-check | 対象ファイルと移行手順の節を除外する方法を固定 |
| 恒久テストの追加 | ファイルを読む Medium テストと、解決手順を subprocess 実行する Large（large_local）テストを 1 ファイルずつ追加 | **AI の仮定**。根拠: skill の解決手順は実行時に効く手順で、既定値の写しは乖離しうる。検査先: review-design / review-code | § テスト戦略 |

one-way door の未決: なし。公開 CLI・config schema・永続データ形式は変更しない。GitHub ラベルの
改名は人間が決めた in-place 方式で、工程は人間が行う事後確認に分けられている。

## テスト戦略

### 変更タイプ

- **実行時の振る舞いを変える変更（instruction / config）＋ docs**。harness の Python コードは変えない。
  ただし、(a) kaji 自身の `config.incident` の実効値、(b) incident-* skill が実行時に行う前提ガードの
  判定規則が変わる。

### Small テスト

- 追加しない。新しい純粋ロジック（Python 関数）はない。`IncidentConfig` の既定値と `[incident]` の
  解析・検証は既存の `tests/test_config.py` が網羅している。本 Issue はそれらを変えない。

### Medium テスト（新規 `tests/workflows/test_incident_labels.py`、`@pytest.mark.medium`）

ファイルの読み取りと `KajiConfig.discover` の呼び出しだけで、subprocess は起動しない。検証観点:

1. **labels.yml の整合**: `.github/labels.yml` を `yaml.safe_load` で読む。ラベルは 29 件で `name` に
   重複がない。名前に `incident` を含むラベルの集合は、既定名 8 件と一致する。
2. **config とラベル定義の整合（drift 防止）**: `KajiConfig.discover(REPO_ROOT).incident` の
   `kind_label` / `initial_status_label` / `transient_label` が `labels.yml` に存在する。
   kaji が起票・遷移に使うラベルが定義から漏れていないことを、今後も保証する。
3. **kaji 自身は既定値で運用**: `KajiConfig.discover(REPO_ROOT).incident == IncidentConfig()`
   （完了条件の機械検証）。
4. **固定ラベル名の再混入防止**: `incident-investigate` / `incident-review` / `incident-report` の
   `SKILL.md` に旧名のトークン（正規表現 ``["`]incident(:[a-z]+)*["`]``）がない。
   investigate / review の Step 0 が `KIND_LABEL` を参照している。

### Large テスト（新規 `tests/workflows/test_incident_kind_label_large_local.py`、`pytestmark = [pytest.mark.large, pytest.mark.large_local]`）

skill の `kind_label` 解決手順を、実際の `bash` と `python3` の subprocess で実行する。
`docs/reference/testing-size-guide.md` の「実際の CLI コマンドを subprocess で実行 → Large」と
「`large_local`: subprocess あり / 外部ネットワーク無し」に当たるため Large に分類し、
`make test-large-local` で実行する。ネットワークや GitHub は使わない。

- **抽出**: `incident-investigate/SKILL.md` の見出し「`kind_label` の解決」の直後にある最初の
  ```bash ブロックを取り出し、末尾に `printf '%s' "$KIND_LABEL"` を付けて実行する。抽出の仕様
  （見出し名・最初の bash ブロック）はテストの docstring に書く。見出しやブロックが見つからなければ
  テストは fail する。
- **正常系**（returncode 0、stdout はラベル値だけ）:
  - `[incident]` のない config を置いた dir → stdout が `IncidentConfig().kind_label` と一致
    （skill の既定値リテラルと正本の乖離を検出）
  - `kind_label = "custom:incident"` を置いた dir → stdout が `custom:incident`
- **異常系**（returncode が非ゼロ、stdout は空、stderr に理由）:
  - config のない dir（`outside_project_tmp_path` fixture を使う。workflow 内の `tmp_path` は repo 内で、
    親方向の探索が実 config を見つけるため）
  - 不正な TOML の config
  - `[incident]` が table でない config（例: `incident = "x"`）
  - `kind_label` が文字列でない、または空の config
- config を置くケースは `tmp_path` 配下に `.kaji/config.toml` を作る。探索はそこで止まるので、実リポジトリの
  config には届かない。

設計段階で、この解決ブロック（§ インターフェース「使用例」）を上記 6 ケースで手動実行し、正常系 2 件は
rc=0 でラベル値だけを出力、異常系 4 件は rc=1 で stdout が空であることを確認した。

追加しない Large:

- 実際の GitHub ラベル改名は本番リポジトリのラベルを変える一度きりの運用作業で、CI で繰り返し
  実行できない。人間が行う「ワークフロー完了後の確認項目」（`gh label list`、番号集合の照合）で確認する。
- 第 1 層が設定したラベル名で起票・検索・遷移する経路は既存の Large テスト
  （`tests/test_recovery_incident_large_local.py`）と handler テストが保護しており、本 Issue は
  その実装を変えない。
- skill の前提ガードを LLM agent が実行する経路は決定的に再現できない。決定的な部分（`kind_label`
  の解決）は上記の Large テストで実行して検証する。

### 変更固有検証（恒久化しない）

- **旧名の残存 grep**（どちらも 0 件になること。CHANGELOG・`docs/reference/configuration*.md` の
  旧名維持例・移行手順の節は対象外）:
  ```bash
  grep -nE '["`]incident(:[a-z]+)*["`]' \
    .github/labels.yml .kaji/config.toml docs/dev/labels.md docs/dev/workflow_guide.md \
    .claude/skills/incident-*/SKILL.md .claude/agents/kaji-incident-reviewer.md \
    docs/cli-guides/failure-recovery.md docs/cli-guides/failure-recovery.ja.md
  awk '/^## 旧名からの移行手順/{s=1;next} /^## /{s=0} !s' docs/dev/incident-labels.md \
    | grep -nE '["`]incident(:[a-z]+)*["`]'
  ```
  加えて、バッククォートのない表記を拾うため、
  `grep -nE '(^|[^:a-z_-])incident:(investigating|mitigated|resolved|cause)'` を同じ対象に実行する
  （incident-labels.md には同じ awk 除外をかける）。
- `make verify-docs`（リンク整合）、`make check`。
- docs の移行手順にある 8 コマンドが § 方針 4 の対応表と一致していることを目視で確認する。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 技術選定の変更なし（ADR 008 の方針に従うだけ） |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/incident-labels.md | あり | 既定名での全面記述、旧名明示の段落を削除、移行手順の節を追加 |
| docs/dev/labels.md | あり | incident 節の表を既定名に変更 |
| docs/dev/workflow_guide.md | あり | 第 1 層の記述を設定 key + 既定名に変更 |
| docs/reference/configuration.md / .ja.md | あり（軽微） | 移行手順の節へのリンクを追加。旧名維持例は残す |
| docs/cli-guides/ | なし | `failure-recovery*.md` は `incident-labels.md` へのリンクだけで、ラベル名を書いていない（残存 grep で確認） |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| CHANGELOG.md | あり | `[Unreleased]` に、`### Changed`（skill の前提ガードが `[incident] kind_label` に従う。0.21.0 の制約を解消）と `### Docs`（移行手順を追加）を追記 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| #434 決定事項 | https://github.com/apokamo/kaji/issues/434 | 既定名を `kaji:` 接頭辞付きにする。旧名との二重検索は持たない。移行はフォローアップで行う |
| CHANGELOG `[0.21.0]` | `CHANGELOG.md` § BREAKING CHANGE | 8 つの既定名。互換検索がないこと。存在しないラベルは GitHub に拒否され記録は fail-open。「Not migrated by this change」と investigate ガードの制約 |
| `[incident]` の仕様 | `docs/reference/configuration.md` § `[incident]` | key・既定値。overlay は無視。3 ラベルの相異は大文字小文字を区別せず判定。kaji はラベルを作らない |
| 既定値の正本 | `kaji_harness/config.py:120-129`（`IncidentConfig`）、`:214-224`（`discover` の親方向探索） | `kind_label = "kaji:incident"` 等。cwd から親方向に `.kaji/config.toml` を探す |
| run 起動時の config 受け渡し | `kaji_harness/commands/run.py:286` | `incident=config.incident` を起動時に一度だけ渡す |
| ラベル同期の挙動 | `.github/workflows/labels-sync.yml` | 追加と更新だけで削除しない。push（labels.yml 変更）・手動・週次 cron `0 0 * * 1`。存在しない名前は `createLabel` |
| 現行の skill ガード | `.claude/skills/incident-investigate/SKILL.md:72,154`、`.claude/skills/incident-review/SKILL.md:54` | `incident` ラベルを固定で確認している |
| `gh label edit` | https://cli.github.com/manual/gh_label_edit | 「A label can be renamed using the `--name` flag.」 |
| `gh label delete` | https://cli.github.com/manual/gh_label_delete | ラベルの削除（`--yes` で確認を省略） |
| GitHub REST: Update a label | https://docs.github.com/en/rest/issues/labels#update-a-label | `new_name` で改名する。同じラベルオブジェクトの更新なので、既存 Issue への付与は維持される |
| GitHub Docs: Managing labels | https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/managing-labels | ラベルの編集（名前変更を含む）と削除の操作。削除すると Issue と PR の両方から付与が外れる（復旧手順で Issue / PR の両方を確認する根拠） |
| Python `tomllib` | https://docs.python.org/3/library/tomllib.html | Python 3.11 で追加された標準の TOML パーサ（`requires-python >= 3.11` と整合） |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L の判定基準。workflow 内では `tmp_path` が repo 内になるため `outside_project_tmp_path` を使う |
| テストサイズ分類 | `docs/reference/testing-size-guide.md` | 「実際の CLI コマンドを subprocess で実行 → Large」。`large_local` は subprocess あり / 外部ネットワーク無しで、`make test-large-local` で実行する |
| ADR 008 | `docs/adr/008-no-backward-compat-layer.md` | 後方互換レイヤを持たない方針 |
