---
description: 第2層。インシデントイシューとローカル run artifact を読み、#301 の調査手順①〜⑥に沿って原因調査 artifact を作成し、全文をインシデントイシューへコメント投稿する。結論を断定できない場合は INCONCLUSIVE で PASS 可。
name: incident-investigate
---

# Incident Investigate（調査・提案役）

第1層（#304）が起票したインシデントイシューを入力に、原因を調査し、調査 artifact
（`<artifact_root>/<incident_issue_id>/investigation/report.md`）を作成して全文をコメント投稿する。

> **結論を断定できないことは失敗ではない。** 証拠が不足するときは無理に断定せず、`INCONCLUSIVE`
> を選び、棄却済み仮説・不足証拠・再現の記録を充足させること。**調査結論（conclusion）と
> レビュー verdict（PASS/RETRY/ABORT）は別軸**であり、記述が十分なら結論が `INCONCLUSIVE` でも
> verdict は PASS になり得る（EPIC #303 決定 D）。

**ワークフロー内の位置**: **investigate** → review →（fix → verify）→ report

## 入力

### ハーネス経由（コンテキスト変数）

| 変数 | 型 | 説明 |
|------|-----|------|
| `issue_id` | str | 調査対象のインシデントイシュー ID |
| `issue_ref` | str | 人間可読の Issue 参照（GitHub では `#<issue_id>`） |
| `step_id` | str | 現在のステップ ID |

### 手動実行（スラッシュコマンド）

```
$ARGUMENTS = <incident_issue_id>
```

### 解決ルール

コンテキスト変数 `issue_id` が存在すればそちらを使用。なければ `$ARGUMENTS` の第 1 トークンを
`issue_id` として使用する。

## 全 incident-* skill 共通ルール

- **`worktree_dir` を参照しない**: インシデントイシューには `type:*` ラベルも worktree も無く、
  注入される `worktree_dir` は実在しないパスを指す。作業場所は main repo（読み取り）＋調査 artifact
  ディレクトリ（書き込み）＋使い捨て検証環境に限定する。
- **artifact root は main worktree 基準で一意に解決する**（Issue #305）: `kaji run` は
  `resolve_artifacts_dir()` により run/state artifact を **main worktree の `.kaji-artifacts`** へ
  集約する。skill が feature worktree（例 `kaji-feat-305`）の cwd から起動されると、そこには
  `.kaji-artifacts` が存在しない。したがって **cwd 相対の `.kaji-artifacts` を参照してはならない**。
  各 skill は最初に絶対 root を解決し、source run・台帳・investigation report の全読み書きに同じ
  root を用いる:
  ```bash
  ART="$(kaji config artifacts-dir)"   # main worktree 基準の絶対パス（副作用なし）
  ```
  以降、本ドキュメントの `<artifact_root>` は `$ART` を指す。
- **verdict 3 経路**: 作業報告コメント末尾 → stdout → artifact `verdict.yaml`。コメントには
  `kaji issue comment <id> --verdict-step <step> --verdict-status <STATUS>` を無条件付与する。
- **長時間コマンドは foreground ＋明示 timeout** で待ち切る。background 実行・wake 系 tool に依存しない
  （#301 の上流不具合 [anthropics/claude-code#59864](https://github.com/anthropics/claude-code/issues/59864) を踏まないため）。
- **副作用の禁止**（全終端は「提案」。#303 決定 D）: ラベル付与・除去、イシューのクローズ / reopen、
  バグイシューの起票、統合の実行、コード変更・commit・push・PR 作成を**行わない**。
- **ログの sanitize**: `<artifact_root>/<issue>/runs/<run_id>/run.log` は生ログである。コメント /
  artifact に引用する際はトークン・資格情報・秘匿 URL を既存 `sanitize_evidence` と同方針でマスクする。
- **auto-close hazard 回避**: `docs/dev/shared_skill_rules.md` § auto close keyword 回避規約に従う。

## 実行手順

### Step 0: 前提ガード（violate → ABORT）

```bash
kaji issue view [issue_id] --json labels,body
```

- 対象イシューに `incident` ラベルが付与されていること
- 本文 1 行目に identity marker（`<!-- kaji-incident: ... -->`）が存在すること

いずれかを欠く場合は調査に入らず ABORT（suggestion に「対象がインシデントイシューか確認する手順」を記載）。

### Step 1: 入力の読み込み

0. artifact root を解決する（共通ルール参照。以降のパスはこの絶対 root 基準）:
   ```bash
   ART="$(kaji config artifacts-dir)"
   ```
1. インシデントイシュー本文・全コメント（occurrence marker 群）を読む:
   ```bash
   kaji issue view [issue_id] --comments
   ```
2. occurrence marker から調査対象 run_id 一覧と再発回数 N（ユニーク `run_id` 件数）を導出する。
3. ローカル run artifact（`$ART/<source_issue>/runs/<run_id>/` の
   `run.log` / `result.json` / `steps/`）と台帳 `$ART/incidents/occurrences.jsonl` を読む。

### Step 2: 調査手順①〜⑥の実施（#301 の実施記録が素材）

| 手順 | 内容 | 主な入力 |
|------|------|----------|
| ① 一時的か判定 | 再発回数 N・auto-resume 自己回復の有無・発生間隔から transient 可能性を評価 | occurrence marker 群、`occurrences.jsonl` |
| ② 識別署名の確認 | identity marker / fingerprint block を読み、署名が障害実体と整合するか検証（過剰統合の検出を含む） | Issue 本文、`kaji_harness/recovery/signature.py` の正規化仕様 |
| ③ バージョンの時系列 | 発生 run 前後の CLI / 依存バージョン変化と発生時期の相関を整理 | `run.log`、CHANGELOG、`git log` |
| ④ 上流既知不具合と照合 | `WebSearch` / `WebFetch` で上流 issue tracker・release note を独立検索 | 上流リポジトリ（例: anthropics/claude-code） |
| ⑤ 再現実験 | 使い捨て環境（`git worktree add --detach` の一時 worktree ＋隔離 venv、または scratch dir）で最小再現を試行。結果（成功 / 失敗 / 実施不能の理由）を必ず記録 | run artifact、再現コマンド |
| ⑥ 内部／外部要因の切り分け | ①〜⑤の結果から conclusion 6 値のいずれかに到達、または `INCONCLUSIVE` として棄却済み仮説＋不足証拠を列挙 | ①〜⑤の記録 |

- ⑤ の使い捨て環境は調査完了後に破棄する（`git worktree remove` / scratch 削除）。
- 再現できなかった場合も「実施不能の理由」を必ず記録する（空欄にしない）。

### Step 3: 調査 artifact の作成

`.claude/skills/incident-investigate/artifact-template.md` をテンプレートとして、
`$ART/[issue_id]/investigation/report.md` を作成する（親ディレクトリは `mkdir -p` で用意）。必須セクション:

- メタデータ（対象 / run_id 一覧 / N / 提案役モデル / モデル値の情報源）
- 可読サマリ / 結論（conclusion 6 値） / 根拠（citation） / 調査手順の実施記録①〜⑥ /
  棄却済み仮説 / 意味的類似インシデント / 対応策の提案 / 不足証拠

**モデルメタデータの記録**（#303 決定 B）: メタデータの `提案役モデル` に本セッションの実行中モデル ID を
記録する。取得元は **主: セッションの自己申告値**（system prompt が提示する実行中モデル ID。`--model`
override 後の実選択モデルを反映）、**従: 設定値**（workflow YAML の `model`）。`モデル値の情報源` に
`self-reported` / `configured` を明記する。`査読役モデル` 以降は review step が追記する（本 step は空欄可）。

**受理基準を満たす**（#303 決定 A / D）:

- conclusion が `INCONCLUSIVE` 以外: **実再現、または実障害ログの引用（`<run_id>:<ファイル>` 付き
  citation）を必須**とする。欠けば査読で RETRY になる。
- conclusion が `INCONCLUSIVE`: 棄却済み仮説（反証根拠つき）・不足証拠の列挙・再現の記録を必須とする。

### Step 4: コメント投稿（正本の永続化）

調査 artifact 全文をインシデントイシューへコメント投稿する。artifact は gitignore 済み領域の作業コピー
であり、**正本はコメント**（worktree 削除の影響を受けない長期記憶）。verdict マーカーを無条件付与する。

```bash
kaji issue comment [issue_id] --commit \
  --verdict-step investigate --verdict-status <STATUS> \
  --body-file "$ART/[issue_id]/investigation/report.md"
```

## Verdict 出力

作業報告コメント末尾 → stdout → artifact `verdict.yaml` の 3 経路に残す。

---VERDICT---
status: PASS
reason: |
  調査 artifact を作成し、受理基準を満たす記述を投稿した
evidence: |
  conclusion=<値>、citation <run_id>:<path>、①〜⑥ の実施記録あり
suggestion: |
---END_VERDICT---

### status の選択基準

| status | 条件 |
|--------|------|
| PASS | 調査 artifact を作成し、受理基準（実証 or INCONCLUSIVE の記述充足）を満たす |
| ABORT | 対象が非インシデント（`incident` ラベルなし / identity marker なし）等の前提崩壊 |

> `RETRY` は investigate では返さない（`incident.yaml` の `investigate.on` は `{PASS, ABORT}` のみ）。
> 調査の不足は後段 review が RETRY を発行して fix へ回す。
