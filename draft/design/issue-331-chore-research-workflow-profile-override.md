# [設計] workflow overlay（base + overrides）方式の採否 research と decision record

Issue: #331

## 概要

workflow YAML の重複を解消する単層 overlay（`base` + `overrides`）方式について、採用可否を決める
research を実施し、結論を恒久 decision record（新規 ADR）と Issue コメントとして残す。本設計書は
**調査の実施仕様**（何を一次情報として、何を判定し、どの成果物のどこへ書くか）を定義する。overlay の
実装は行わない。

## 背景・目的

Issue #331 の目的（workflow variant の増殖が topology の source of truth を壊す前に、単層 overlay
方式の採否を確定し、後続の設計 Issue が拠って立つ decision record を残す）はすでに Issue 本文で固定
されている。本設計書が追加で決めるのは次の 2 点に限られる。

1. **成果物の形と配置**: research の結論が Issue コメントだけに散逸すると、後続 Issue（loader /
   validation / CLI / tests / docs）が参照する正本が repo 内に存在しない。#352 の決定（custom は
   pytest 対象外）と同様に、以後の実装判断が拠る規範は repo 内の恒久 doc に置く必要がある。
2. **調査の受入基準**: Issue の完了条件 10 項目に対し、どの一次情報を根拠に、どの節へ記録すれば
   充足したと判定するかを先に固定する。これがないと implement フェーズで「調べた気になる」記述が
   混入し、review-code が実証できない。

本 research 自体の受益者は「次に overlay を実装する設計 Issue の担当」である。担当は O1 を読むだけで、
schema・merge 規則・制約・validate 診断・resolved 可視化と artifact の要件・棄却済み選択肢を確定情報と
して受け取れ、Issue #331 の全コメントを読み直さずに設計へ入れる。これが本 Issue の成功条件である。

なお本 Issue は `type:chore` だが、canonical 外 type のため
[`_shared/design-by-type/feat.md`](../../.claude/skills/_shared/design-by-type/feat.md) をフォールバック
ガイドとして適用する（ユースケース起点、IF 明示、テスト戦略の変更タイプ判定）。Issue 本文の
`## ユースケース` 5 件がこの research の受益者定義であり、本設計書では再掲しない。

## インターフェース

本 Issue の成果物はコードではなく調査記録である。したがって IF は「調査の入力（read-only な一次情報）」
と「出力（生成する文書とコメント）」として定義する。

### 入力

| 種別 | 実体 | 扱い |
|------|------|------|
| 確定方針 | Issue #331 本文 `## 決定事項 > 人間が確定した方針`（6 件）と、owner の `grill-me provenance` コメント | source of truth。弱化・格下げ・上書きをしない |
| 実測結果 | Issue #331 本文 `## 実測サマリ` と、計測スクリプトを含む owner コメント（2026-07-22） | 再現済み前提。再計測は任意 |
| 現行実装 | `kaji_harness/workflow.py` / `models.py` / `preflight.py` / `runner.py` / `commands/run.py` / `commands/validate.py` / `commands/recover.py` | read-only。変更しない |
| 現行定義 | `.kaji/wf/official/**`（5 本）/ `.kaji/wf/custom/**`（5 本）/ `/home/aki/dev/kamo2/.kaji/wf/*.yaml`（23 本） | read-only |
| 現行規範 | `docs/dev/workflow-authoring.md` / `docs/adr/008` / `docs/adr/010` / `Makefile` / `pyproject.toml` / `docs/guides/python-starter.md` | read-only |

### 出力

| # | 成果物 | 内容 | 生成 step |
|---|--------|------|-----------|
| O1 | `docs/adr/0NN-workflow-overlay-single-layer.md`（新規。`NN` は採番時点で未使用の最小連番。現時点では 011） | decision record 本体。後述の節構成に従う | implement |
| O2 | Issue #331 コメント | 調査結論サマリ、完了条件 10 項目の充足対応表、GO 時の後続 Issue 分割案（NO-GO 時は代替策） | implement |
| O3 | Issue #330 コメント | 結論 1 段落と、O1（repo path）/ O2（コメント URL）への参照 | implement |

O1 は恒久文書、O2 / O3 は運用連絡である。後続 Issue 分割案（backlog）は時間とともに陳腐化するため
O1 には置かず O2 に置く。

### 使用例

decision record が固定する overlay YAML の確定形（GO の場合に O1 §「決定」へ載せる形）。Issue 本文の
schema に、人間確定方針（field 別 defaults 適用）を反映したもの。

```yaml
# .kaji/wf/custom/dev/dev-thorough-fable.yaml
name: dev-thorough-fable
base: ../../official/dev.yaml     # overlay ファイル起点の相対パス。project root 内限定
description: |
  dev の思考量重視 variant（fable）。
overrides:
  defaults:
    model: fable                  # LLM step のみ。exec step へは注入しない
    effort: xhigh                 # 同上
    timeout: 3600                 # 全 step に適用
  steps:
    review-code:
      agent: codex
      effort: high
```

NO-GO の場合に O1 §「決定」へ載せる代替形（同期チェック案）。

```make
validate-workflows:
	@... kaji validate $$files
	@python scripts/check_workflow_topology.py   # 正規化ハッシュ比較を追加
```

### エラー・停止条件

| 事象 | 扱い |
|------|------|
| 人間確定方針と現行実装が矛盾し、どちらを優先するか未決 | 調査を止め `ABORT`。矛盾内容と選択肢を Issue へ記録する |
| 調査の結果 GO / NO-GO いずれとも判定できない | O1 を「保留」として書き、保留を解く条件（追加で必要な計測・判断）を明記する。verdict は `PASS` |
| 調査中に overlay 以外の解が優位と判明 | NO-GO 側の記録として O1 へ残す。scope を広げて第 3 案の詳細設計へ進まない |

## 制約・前提条件

- **実装しない**: `kaji_harness/**`・`.kaji/wf/**`・既存 workflow YAML を変更しない。本 Issue の
  git 差分は `draft/design/` と `docs/adr/` に閉じる（Issue `## スコープ境界`）。
- **人間確定方針を弱化しない**: defaults の field 別適用、base の信頼境界、base 更新の反映、resolved
  事前可視化の追加、run artifact の provenance 保存、recovery 境界の 6 件は前提であり、research の
  検討対象は「それを満たす具体形」に限る。
- **ADR 008（後方互換レイヤ非提供）と整合させる**: `base` キーを持たない既存 YAML が従来経路のまま
  動くことは「旧フォーマット読み取り器の追加」ではなく `base` 不在という単一分岐であり、互換レイヤ
  ではない。O1 ではこの区別を明示する。
- **overlay 表層 schema は Pydantic で検証する（確定方針）**: `base` / `overrides.defaults` /
  `overrides.steps` は既存 validator が一切カバーしない新規の外部入力契約であり、`AGENTS.md`
  Always-Apply Rules「外部入力は Pydantic で検証する」がそのまま適用される。ADR 010 が
  `SeriesConfig` / `SeriesMember` に適用したのと同じ扱いであり、同 ADR が
  「Existing workflow parsing is unchanged」として既存 workflow parser の移行を対象外にした境界も
  そのまま維持する。したがって O1 は「overlay 表層を Pydantic model で検証し、merge 後は既存の
  `Workflow` / `Step` dataclass へ渡す」を確定方針として記す。ADR 010 と同じく overlay model は
  unknown field を禁止し、これにより Issue 本文の制約「overlay ファイルへの `steps:` / `cycles:`
  直書きは禁止」は個別の検査を書かずに schema で成立する。後続 Issue へ残すのは実装詳細のみ
  （model 名、Pydantic `ValidationError` を `WorkflowValidationError` へ正規化する位置と
  エラー集約の粒度）。
- **既存の所有権境界を壊さない**: `official/**` は kaji 所有・Release で更新されうる、`custom/**` は
  利用者所有。overlay の `base` は custom → official の一方向参照になるため、この向きが所有権境界と
  矛盾しないことを O1 で確認する。
- **計測スクリプトを repo へ恒久化しない**: 既に Issue コメントに全文があり、`/issue-review-ready` の
  レビュワーが再実行して 33 ファイル・10 topology を再現済み。`experiments/` へ置くと `make check`
  （ruff / mypy）の恒久保守対象になり、research 用途に対して費用が上回る。

## 方針

### 調査手順（Q1〜Q8）

各 Q は「一次情報 → 判定 → 記録先」を固定する。Q1・Q2 は Issue 本文が明示的に「本 Issue の調査で
確定する」と留保した論点、Q3〜Q6 は `## 残調査観点`、Q7・Q8 は結論部に対応する。

| Q | 問い | 一次情報 | 判定方法 | 記録先 |
|---|------|----------|----------|--------|
| Q1 | `overrides.steps.<id>` が exec step に `agent` / `model` / `effort` を指定した場合、validation error にするか無視するか | `workflow.py:62` `_EXEC_FORBIDDEN_KEYS`、`workflow.py:177-183`（parse 時 fail-fast）、`workflow.py:514-527`（validate ミラー） | 現行契約は「exec step が当該キーを持つこと」自体を error にしている。overlay で silent skip すると、利用者の明示指定が無言で消える経路が新設される。fail-loud / silent skip の帰結を並べ、現行契約に整合する方を選ぶ | O1 §「決定」merge 規則 |
| Q2 | `defaults` の「LLM step」判定基準。`agent` 省略の skill step（例: `official/dev.yaml` の `baseline`）は defaults の適用対象か。混在診断で `agent: null` を混在と数えるか | `.kaji/wf/official/dev.yaml:115-120`（`baseline` は skill step かつ agent 省略）、`preflight.py`（exec_script skill への agent/model/effort 無視 warning）、`workflow.py:267-282`（agent 省略時は effort の agent 別検証を skip） | 「LLM step = `skill` を持つ step」と定義すると `baseline` にも defaults が乗る。現行 preflight は exec_script skill に対し warning を出す構造なので、その warning 経路と重複しないかを確認する。混在診断は `agent` 値の異同のみで決定的に判定できる必要がある（Issue 本文の制約） | O1 §「決定」merge 規則・validate 診断 |
| Q3 | resolved workflow の事前可視化に必要な要件（「合成した完全な workflow」を欠落なく表現する条件） | `kaji_harness/models.py:46-112`（`Workflow` / `Step` の全 field＝表示すべき集合の正本）、`commands/validate.py:51-96`（現行 `kaji validate` は L1/L2/L3 の可否のみ出力）、`docs/dev/workflow-authoring.md:540-560` | 人間確定方針は「base と overrides を合成した**完全な** workflow を run 前に表示する」であるため、要件を「解決後 `Workflow` の全 field（`name` / `description` / `execution_policy` / `requires_provider` / `default_timeout` / `workdir` / `cycles` の全 field、および全 step の `id` / `skill` or `exec` / `agent` / `model` / `effort` / `max_budget_usd` / `timeout` / `workdir` / `resume` / `inject_verdict` / `on`）を欠落なく表現できること」と置く。値の由来（base / `overrides.defaults` / `overrides.steps.<id>`）の併記は、完全表示に**加える**追加要件として扱う（Issue ユースケース 4「一括 override の実効値を run 前に確認したい」を満たすための AI 仮定。検査先: 後続 Issue の review-design）。CLI 名と serialization 形式は two-way door として未確定のまま残す | O1 §「決定」resolved 可視化の要件 |
| Q4 | run artifact に resolved 定義を保存する要件（保存先・形式・provenance の粒度） | `runner.py:64-104`（`runs/<run_id>/` と `steps/<id>/attempt-NNN/` の layout）、`runner.py:938-949`（`run.log` と同階層に採番）、`commands/run.py:159-198`（workflow_path は `resolve()` 済み） | 「base が後から変わっても当該 run の実行定義を確定的に再構成できる」（人間確定方針）を満たす最小要件を、既存 run_dir layout に載る形で定義する。overlay / base の path は project-relative（人間確定方針） | O1 §「決定」artifact 要件 |
| Q5 | 後方互換と migration | `workflow.py:17-33`（`load_workflow` は単一ファイル読取）、`workflow.py:132-434`（`base` は未知キーとして現状無視される）、`docs/adr/008` | `base` キー不在の YAML が現行と同一経路を通ること、および移行がファイル単位で任意（既存 33 本を一括変換しない）であることを確認する。ADR 008 との整合を明記 | O1 §「影響」互換性 |
| Q6 | builtin / custom / local / GitHub / packaging / starter への影響 | `pyproject.toml:59-60`（package-data は `assets/interactive-terminal/wrapper.sh` のみ＝workflow YAML は wheel に同梱されない）、`docs/dev/workflow-authoring.md:29-92`（所有権・品質保証の責務境界）、`docs/guides/python-starter.md:14-16`（starter は flat `.kaji/wf/` layout で YAML をコピー配布）、`Makefile:20-25`（tracked な `.kaji/wf` を一括 validate）、`.kaji/wf/official/local/*`（local provider 用） | 「official base を参照する custom overlay」の配布境界を、wheel 非同梱・starter コピー配布・flat layout という実態に照らして判定する。starter が flat layout のままだと `base: ../../official/dev.yaml` が解決できないため、starter 側の前提条件を影響として記録する | O1 §「影響」配布境界 |
| Q7 | GO / NO-GO / 保留の結論と、判断を変える条件 | Q1〜Q6 の結果、Issue 本文 `## 期待効果`、`## 比較対象` の 2 案 | 2 案を「重複削減効果 / topology 保証 / 実装コストと変更面積 / 失敗モード」の 4 軸で比較する。継承・generator は実測の需要不在（steps・cycles・`on` の差がゼロ）を根拠に一文で棄却 | O1 §「決定」「判断を変える条件」 |
| Q8 | GO の場合の後続 Issue 分割案 | Q1〜Q6 で確定した契約範囲 | 公開 IF / loader / validation / CLI（resolved 表示）/ artifact / tests / docs の粒度で、依存順と各 Issue のスコープ境界を書く | O2 |

### O1（decision record）の節構成

repo 既存 ADR の書式（`docs/adr/010` の Context / Decision / Consequences、`docs/adr/008` の
ステータス・コンテキスト・決定・帰結）に合わせ、次の節を置く。

1. **ステータス**: 本 Issue 時点は「提案」。人間が PR を merge した時点で承認とみなす旨を 1 行で記す
   （既存 ADR は `Accepted` / `承認` のみを使うため、提案段階であることを明示する）
2. **コンテキスト**: 実測サマリ（33 ファイル → 10 topology、dev 系 13 本同一）、#352 による custom の
   pytest 対象外化、運用ルールでは再発を防げない実績（kamo2 #1327 後 21→23 本）
3. **決定**: GO / NO-GO / 保留。GO の場合は schema（Pydantic model による overlay 表層検証・unknown
   field 禁止を含む）・merge 規則（3 層）・制約（単層・scalar のみ・`base` の信頼境界）・validate
   診断・resolved 可視化の要件（解決後 workflow の完全表示）・artifact 要件を確定形で記す
4. **棄却した選択肢**: 汎用継承、外部 generator、selector 型条件置換、多段継承、profile 自動合成、
   現状維持 + 同期チェック（NO-GO の場合は逆）。各 1〜2 文で棄却根拠を実測に紐づける
5. **帰結**: 得られるもの（重複削減量、topology 保証の回復）と代償（`load_workflow` の責務拡大、
   overlay 特有の failure mode、starter の layout 前提）
6. **判断を変える条件**: この決定を再検討すべき観測（例: topology 差分を要する variant の出現、
   overlay の解決コスト起因の障害）
7. **未確定事項**: 後続 Issue で決める論点（resolved 表示の CLI 名と serialization 形式、artifact の
   具体 schema、overlay Pydantic model の命名と `ValidationError` 正規化位置）。Pydantic を使うか
   否か自体は `AGENTS.md` / ADR 010 により確定済みのため、ここには置かない

### 受入基準（Issue 完了条件との対応）

implement フェーズは、下表の「充足先」がすべて埋まった時点で完了とする。埋まっていない項目を
「調査したが記録しない」形で通過させない。

| # | 完了条件（Issue 本文） | 充足先 | 判定 |
|---|------------------------|--------|------|
| 1 | 計測スクリプトと結果が Issue に記録され再実行可能 | 既存 owner コメント（2026-07-22）+ O1 §「コンテキスト」からの参照 | 再実行して 33 ファイル・10 topology を再現できる |
| 2 | overlay vs 現状維持 + 同期チェックの比較と、継承・generator の棄却根拠 | O1 §「決定」「棄却した選択肢」 | 4 軸比較が書かれ、継承・generator の棄却が実測に紐づく |
| 3 | 本文記載の merge 規則・制約・validate 診断からの変更差分と理由 | O1 §「決定」 | Q1 / Q2 の結論が差分として明示される（変更なしなら「変更なし」と根拠） |
| 4 | resolved 可視化と artifact 保存の要件 | O1 §「決定」 | Q3 の要件が「解決後 `Workflow` の全 field を欠落なく表現できること」として書かれ（CLI 名・serialization 形式は未確定でよい）、Q4 の artifact 要件が既存 `runs/<run_id>/` layout に載る形で書かれている |
| 5 | backward compatibility と migration | O1 §「影響」 | Q5 の結論と ADR 008 との整合が書かれている |
| 6 | builtin / custom / local / GitHub workflow への影響 | O1 §「影響」 | Q6 の 4 経路すべてに言及がある |
| 7 | GO / NO-GO / 保留の結論と、判断を変える条件 | O1 §「決定」「判断を変える条件」 | 結論が 1 つに定まり、再検討トリガが観測可能な形で書かれている |
| 8 | GO の場合の後続 Issue 分割案 | O2 | 公開 IF / loader / validation / CLI / tests / docs の粒度と依存順がある |
| 9 | NO-GO の場合の代替策 | O1 §「決定」（NO-GO 時のみ） | 同期チェックの設置先（`make validate-workflows`）と限界が書かれている |
| 10 | 調査結果が親 Epic #330 から参照できる | O3 | #330 のコメントから O1 と O2 に到達できる |

### 実施しないこと

- overlay の loader / schema / CLI の実装、既存 YAML の変換、model / effort 値の変更
- `docs/dev/workflow-authoring.md` の更新（採用が確定し実装 Issue が動く段階で行う。本 Issue で先に
  書くと、未実装の schema を authoring の正本に載せることになる）
- 継承・composition・generator の worked example 作成（実測により需要不在）

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 本 Issue の scope | 調査と decision record に閉じ、実装しない | Issue #331 `## スコープ境界`（人間決定） | git 差分を `draft/design/` と `docs/adr/` に限定する制約として明文化 |
| GO / NO-GO を research へ委譲すること | 採否を事前に固定せず、本 Issue の調査成果として結論を出す | Issue #331 `## 目的`「この Issue のゴールは『採用可否の結論と、GO の場合の後続 Issue 分割案』であり、実装ではない」および `## 完了条件` 7「GO / NO-GO / 保留の結論と、判断を変える条件が記録されている」（人間決定） | 委譲された判断手順を Q7 の 4 軸比較として具体化し、保留を許容したうえで保留解除条件の記載を義務化 |
| 実際の GO / NO-GO 結論 | Q1〜Q6 の調査結果に基づき implement フェーズで決定する | AI の判断（人間決定ではない）。owner `grill-me provenance` コメント §「残した AI 仮定」も「GO / NO-GO は research の成果として判断し、interview では固定しない」として未確定のまま残している。根拠は Q7 の 4 軸比較。検査先: `/issue-review-code`（結論と根拠の整合検査）と PR review（人間の merge が承認に相当） | 結論が 1 つに定まらない場合は「保留」と保留解除条件を書く逃げ道を用意し、AI が根拠なく GO を出す経路を塞いだ |
| `overrides.defaults` の適用対象 | `agent` / `model` / `effort` は LLM step のみ、`timeout` は全 step | Issue 本文 `## 決定事項`（2026-07-23 `/grill-me` 確認） | Q2 として「LLM step の定義」と「`agent` 省略 step の扱い」を調査項目に分解（`official/dev.yaml:115` の `baseline` が該当実例） |
| `base` の信頼境界 | overlay 起点の相対パス。symlink 解決後も同一 project root 内。外部は validation error | Issue 本文 `## 決定事項`（人間決定） | 実装詳細（project root 判定、symlink 解決 API、診断文言）を O1 §「未確定事項」ではなく §「決定」の要件として記述する範囲に限定 |
| base 更新の反映 | pin せず常に現在の base を継承。stale な step override は validation error | Issue 本文 `## 決定事項`（人間決定） | Q4（artifact への resolved 保存）と組で扱い、「pin しない代償を artifact で補う」構造として O1 §「帰結」に記録 |
| resolved の事前可視化 | base と overrides を合成した**完全な** workflow を run 前に表示する公開 CLI 機能を追加する | Issue 本文 `## 決定事項 > resolved workflow の事前可視化`（人間決定） | Q3 で表示要件を「解決後 `Workflow` の全 field を欠落なく表現できること」と定義（対象集合の正本は `kaji_harness/models.py:46-112`）。CLI 名・serialization 形式のみ two-way door として未確定に残す。owner コメントが `kaji validate --resolved` を AI 仮定として残した扱いを踏襲 |
| 値の由来（base / defaults / steps）の併記 | 完全表示に**加える**追加要件とする | AI の仮定。根拠: Issue `## ユースケース` 4「一括 override の実効値が想定どおりか run 前に確認したい」は、値がどの層から来たかが読めると確認コストが下がる。人間決定は「完全な workflow の表示」までであり、由来併記はそれを削る根拠にならない。検査先: 後続 Issue の review-design | 完全表示を必須要件、由来併記を追加要件として Q3 の判定方法で階層化した |
| run artifact | resolved workflow と overlay / base の project-relative path を保存 | Issue 本文 `## 決定事項`（人間決定） | Q4 で既存 `runs/<run_id>/` layout（`runner.py:64-104`）に載る形へ具体化 |
| recovery との境界 | artifact snapshot を `kaji recover` の実行定義に流用しない | Issue 本文 `## 決定事項`（人間決定） | 調査 Q から除外し、O1 §「未確定事項」にも載せない（スコープ外として明示） |
| overlay 表層 schema の検証方式 | Pydantic model で検証し、unknown field を禁止する。merge 後は既存 `Workflow` / `Step` dataclass へ渡す | `AGENTS.md` Always-Apply Rules「外部入力は Pydantic で検証する」と `docs/adr/010-pydantic-series-input-validation.md`（既存契約。ADR 010 は新規外部入力に Pydantic を適用し、既存 workflow parser の移行は対象外と明記） | 適用範囲を overlay 表層（`base` / `overrides.*`）に限定し、既存 workflow parser を触らない境界を明示。unknown field 禁止により Issue 本文の制約「overlay への `steps:` / `cycles:` 直書き禁止」が schema で成立することを示す。後続 Issue へ残すのは model 名と `ValidationError` 正規化位置のみ |
| 成果物 O1 の配置 | 新規 ADR（`docs/adr/0NN-workflow-overlay-single-layer.md`） | AI の仮定。根拠: `docs/README.md` §「ADR（アーキテクチャ決定記録）」と `i-dev-final-check` SKILL.md:155「新規機能・新規 ADR 相当の決定 → 恒久 docs（`docs/adr/` ほか）へ昇格」。検査先: `/issue-review-design` と `/i-dev-final-check` Step 6 | 連番は採番時点で未使用の最小値（現時点 011）とし、番号衝突時は implement で繰り上げる |
| 後続 Issue 分割案の配置 | ADR ではなく Issue #331 コメント（O2） | AI の仮定。根拠: backlog は時間で陳腐化し、ADR の「決定の記録」という性格と合わない。検査先: `/issue-review-design` | 完了条件 8 の充足先を O2 と明示し、O1 には載せない |
| 計測スクリプトの恒久化 | repo に置かず Issue コメントの記載を正本とする | AI の仮定。根拠: 既に owner コメントに全文があり `/issue-review-ready` レビュワーが再実行して再現済み。`experiments/` は `Makefile:4` の `SOURCES` に含まれ ruff / mypy の恒久保守対象になる。検査先: `/issue-review-code`（完了条件 1 の充足判定） | O1 §「コンテキスト」から Issue コメントを参照する形で再現手順を担保 |
| 変更タイプ | docs-only | AI の仮定。根拠: 出力は `.md` 2 ファイルと Issue コメントのみで、実行時コード経路に触れない。検査先: `/issue-review-code` のテスト戦略検査 | テスト戦略を変更固有検証（`make verify-docs`）+ 恒久テスト非追加の 4 条件充足として構成 |

## テスト戦略

### 変更タイプ

**docs-only**。成果物は `docs/adr/0NN-*.md`（新規）と `draft/design/issue-331-*.md`（本設計書）、
および Issue コメントのみで、`kaji_harness/**` / `tests/**` / `.kaji/wf/**` / `Makefile` /
`pyproject.toml` を変更しない。実行時の振る舞いは一切変わらない。

### 変更固有検証

| 検証 | コマンド / 手順 | 何を担保するか |
|------|-----------------|----------------|
| docs リンク整合 | `make verify-docs` | O1 と本設計書が張る repo 内リンク（`docs/adr/008` / `docs/dev/workflow-authoring.md` / `.kaji/wf/official/dev.yaml` 等）が壊れていない |
| 引用した実装事実の照合 | O1 に載せた各 `file:line` を `Read` で再確認 | 「`_EXEC_FORBIDDEN_KEYS` が exec step の agent/model/effort を拒否する」等の主張が現行コードと一致する（行番号ずれの検出を含む） |
| 実測の再現性 | Issue コメントの計測スクリプトを再実行し、33 ファイル・10 topology・kamo2 dev 系 13 本同一を確認 | 完了条件 1（記録され、再実行可能である）の充足 |
| 回帰確認 | `pytest`（フィルタなし） | docs-only であっても既存スイートが緑であることの確認（新規テストは追加しない） |

### 恒久テストを追加しない理由

`docs/dev/testing-convention.md` の 4 条件に照らす。

1. **独自ロジックの追加・変更をほぼ含まない**: 追加物は Markdown 文書のみで、実行される
   コードパスが存在しない。
2. **想定される不具合パターンが既存ゲートで捕捉済み**: 想定不具合は「リンク切れ」と「記述と実装の
   乖離」であり、前者は `make verify-docs`、後者は本設計書の Primary Sources と review-code の
   実証確認で捕捉する。
3. **新規テストを追加しても回帰検出情報がほとんど増えない**: 「ADR ファイルが存在する」ことを
   pytest で固定しても、内容の正しさは検出できず、将来の decision 更新に対して偽陽性となる。
4. **理由をレビュー可能な形で説明できる**: 本節が Issue コメントと PR から参照可能である。

overlay の実装（loader / merge / validation / CLI）に対する Small / Medium / Large テストは、GO の
場合に O2 の後続 Issue へ引き継ぐ。その設計責務は本 Issue にはない。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | あり | 本 Issue の主成果物 O1 が新規 ADR。番号は採番時点の未使用最小値 |
| docs/ARCHITECTURE.md | なし | 実装しないため構造は変わらない。GO の場合、`load_workflow` の責務拡大は後続実装 Issue で反映する |
| docs/dev/ | なし | `workflow-authoring.md` は overlay schema の正本になるが、未実装の schema を authoring 正本へ先に載せない（後続 Issue で更新） |
| docs/reference/ | なし | Python 規約・API 仕様に変更なし |
| docs/cli-guides/ | なし | CLI を追加しない。resolved 表示 CLI は後続 Issue |
| docs/guides/python-starter.md | なし（ただし O1 に影響として記録） | starter が flat `.kaji/wf/` layout である事実は overlay 採用時の前提条件になるため、Q6 の結果として O1 §「帰結」へ記録するに留める |
| AGENTS.md / CLAUDE.md | なし | 規約・ワークフロー入口に変更なし |
| docs/README.md | なし | ADR は `docs/adr/` へのリンクのみで個別列挙していないため更新不要 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| exec step の禁止フィールド | `kaji_harness/workflow.py:62`, `:177-183`, `:514-527` | `_EXEC_FORBIDDEN_KEYS = ("agent", "model", "effort", "resume", "inject_verdict", "max_budget_usd")` を parse 時に検出して `Step '<id>' with 'exec' must not set '<key>'` を送出。`validate_workflow` にも同等のミラーがある。Q1 の判定根拠 |
| agent 省略 step の実在 | `.kaji/wf/official/dev.yaml:115-120` | `baseline` は `skill: baseline-precheck` を持つ skill step だが `agent` を持たない。Q2 の「LLM step 判定」の実例 |
| effort の agent 別検証 | `kaji_harness/workflow.py:267-282` | `agent` が省略された step では `_AGENT_EFFORT_ALLOWED` 参照をスキップする。defaults で `model` / `effort` だけを注入したときの検証挙動に影響する |
| workflow load の単一ファイル前提 | `kaji_harness/workflow.py:17-33` | `load_workflow(path)` は 1 ファイルを `yaml.safe_load` して `_parse_workflow` に渡すのみ。overlay resolve を load 時に完結させる場合の変更点 |
| Workflow / Step モデル | `kaji_harness/models.py:46-112` | `Step` / `Workflow` は dataclass。overlay を load 時解決にすればこのモデルは無変更で済む（Issue 本文の制約と一致）。同時に、両 dataclass の全 field が Q3「完全な resolved workflow 表示」で欠落を許さない対象集合の正本になる |
| run artifact の layout | `kaji_harness/runner.py:64-104`, `:938-949` | `runs/<run_id>/` を atomic 採番し、`run.log` と `steps/<step_id>/attempt-NNN/` を配置する。Q4 の保存先候補の制約 |
| `kaji run` の workflow 解決 | `kaji_harness/commands/run.py:159-198` | `workflow_path.resolve()` 後に `load_workflow` し、`WorkflowRunner` へ `Workflow` を渡す。resolved 保存の差し込み点の把握に使用 |
| `kaji validate` の現行出力 | `kaji_harness/commands/validate.py:51-96` | 成功時は `✓ <path>`、失敗時は error 列挙のみ。resolved 表示は現行 CLI に存在しない（Q3 の起点） |
| official / custom の所有権と品質保証境界 | `docs/dev/workflow-authoring.md:29-92` | `official/**` は kaji 所有・Release で更新、`custom/**` は利用者所有・pytest 対象外・`make validate-workflows` のみ。Q6 の判定根拠 |
| workflow YAML は wheel に同梱されない | `pyproject.toml:59-60` | `[tool.setuptools.package-data] kaji_harness = ["assets/interactive-terminal/wrapper.sh"]` のみ。builtin workflow を package から解決する経路は存在しない |
| starter の workflow layout | `docs/guides/python-starter.md:14-16` | starter は 5 本の workflow YAML を flat `.kaji/wf/` に配置しており、kaji 本体の official / custom 階層とは異なる。overlay の相対 `base` に対する前提条件 |
| tracked workflow の一括検証 | `Makefile:20-25` | `git ls-files -- '.kaji/wf'` の YAML を `kaji validate` に渡す。NO-GO 案（同期チェック）の設置先および overlay の検証経路 |
| 後方互換レイヤ非提供ポリシー | `docs/adr/008-no-backward-compat-layer.md` | 「後方互換レイヤを書かない。旧フォーマット読み取り・フォールバック・バージョン分岐を実装しない」。`base` 不在時の従来経路がこれに抵触しないことを O1 で示す必要がある |
| 外部入力の検証規約 | `AGENTS.md` § Always-Apply Rules | 「secrets をハードコードしない。外部入力は Pydantic で検証する」。overlay の `base` / `overrides` は既存 validator が扱わない新規外部入力であり、本規約が適用される |
| 外部入力の Pydantic 検証（先例と境界） | `docs/adr/010-pydantic-series-input-validation.md` | 「All models forbid unknown fields; Pydantic reports all structural field errors together」「Existing workflow parsing is unchanged; migrating that established contract is outside this decision」。overlay 表層を Pydantic、既存 workflow parser を dataclass のまま据え置く境界の先例 |
| ADR の書式 | `docs/adr/010-pydantic-series-input-validation.md:1-30`, `docs/adr/008-no-backward-compat-layer.md:1-30` | Status / Context / Decision / Consequences（または ステータス / コンテキスト / 決定 / 帰結）。O1 の節構成の下地 |
| 設計書昇格の基準 | `.claude/skills/i-dev-final-check/SKILL.md:148-157` | 「新規機能・新規 ADR 相当の決定 → 恒久 docs（`docs/adr/` ほか）へ昇格」。O1 を `docs/adr/` に置く根拠 |
| symlink 解決と親子判定の標準 API | https://docs.python.org/3/library/pathlib.html#pathlib.Path.resolve | `Path.resolve()` は「シンボリックリンクを解決して絶対パスを返す」。`base` の信頼境界（symlink 解決後も project root 内）を実装可能にする一次情報。併せて `Path.is_relative_to()` を包含判定に使う |
| YAML 1.1 の `on` キー | https://pyyaml.org/wiki/PyYAMLDocumentation | PyYAML は YAML 1.1 の bool 解釈を行うため bare `on` が `True` になる。`workflow.py:188-194` が同キーを両表現で読む理由であり、overlay 側で `steps` を扱う場合も同じ制約を受ける |
| 親 Epic / 関連 Issue | https://github.com/apokamo/kaji/issues/330 , https://github.com/apokamo/kaji/issues/352 , https://github.com/apokamo/kamo2/issues/1327 | #330 は本 research の親。#352 は custom を pytest 対象外とする人間決定。kamo2 #1327 は整理後の再増殖（21→23 本）の実績 |
