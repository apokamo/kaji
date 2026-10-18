# [設計] issue-close で Issue の一時作業ディレクトリ tmp/kaji/<issue_id>/ を削除する

Issue: #509

## 概要

`issue-close` スキル（`.claude/skills/issue-close/SKILL.md`）に、Issue close 成功後に
`<MAIN_REPO>/tmp/kaji/<issue_id>/` を削除する Step（Step 5.6）を追加する。harness 経由の close step では、
実行中の close step 自身の `$KAJI_TMP_DIR`（とそこへ至る親 dir）だけを残す。削除結果は Step 6 の報告に載せ、
削除の失敗やスキップで close 全体を ABORT にしない。あわせて「作成した dir は自動削除しない」と書いた docs を更新する。

## 背景・目的

- #407 で runner は attempt ごとに `<project_root>/tmp/kaji/<issue_id>/...` を作り、`KAJI_TMP_DIR` / `TMPDIR` /
  `TMP` / `TEMP` として渡すようになった。保持期間と自動削除はスコープ外とされ、`docs/ARCHITECTURE.md`
  （§ attempt 固有の一時作業ディレクトリ）と `docs/dev/skill-authoring.md` に「作成した dir は自動削除しない」とある
- 結果、ディレクトリが増え続ける（Issue 本文の観測: 2026-10-18 時点の main checkout で `tmp/kaji/` 5.8 GB、
  Issue dir 18 個、attempt dir 176 個。大半は `make check` が作る `pytest-of-<user>/`）
- 中身は使い捨て（pytest の `tmp_path`、agent の scratchpad、uv lock、Node compile cache 等）で、正式な記録は
  `.kaji-artifacts/` が持つ。残す価値はその Issue の作業中・直後の調査に限られる

### ユースケース

1. **開発者**: `/issue-close`（手動）または dev 系 workflow の `close` step で Issue を完了したとき、その Issue の
   一時ディレクトリが自動で消え、ディスクを圧迫しない
2. **調査者**: 作業中（close 前）の Issue の一時ディレクトリは残り、失敗した step の `tmp_dir`
   （`run.log` の `step_start.tmp_dir` / `result.json` の `tmp_dir`）から中身を調べられる

## インターフェース

### 入力

Step 5.6 のスクリプトが参照する値（すべて Step 5.6 の 1 回の Bash 呼び出し内で定義する。agent の Bash 呼び出し間で
shell 変数は引き継がれない前提で、Step 2 の `MAIN_REPO` にも依存せず内部で再計算する）。

| 名前 | 出所 | 用途 |
|------|------|------|
| `ISSUE_ID` | skill のプレースホルダー `[issue_id]` を agent が埋める | 削除対象 dir 名。`^[0-9]+$` 以外は削除しない |
| `DEFAULT_BRANCH` | skill のプレースホルダー `[default_branch]` を agent が埋める（harness では provider 解決時に注入。手動起動では provider 設定の値、通常 `main`） | 削除基点となる checkout の特定 |
| `CLOSE_VIA_HARNESS` | プロンプトに `[verdict_path]` があれば `1`、なければ `0`（Step 2.5.1 と同じ判定基準） | harness 経由なのに `KAJI_TMP_DIR` が無い異常時の fail-safe |
| `KAJI_TMP_DIR` | 環境変数（harness が全 dispatch 経路に注入。手動起動では通常未設定） | 保護対象（close step 自身の attempt dir） |
| 削除基点（main checkout） | `git worktree list --porcelain` のうち `branch refs/heads/<DEFAULT_BRANCH>` を持つエントリの `worktree` 行（ちょうど 1 件。0 件・複数件はスキップ） | `tmp/kaji/` の基点。既存 `kaji_harness/providers/_worktree.py::resolve_main_worktree` と同じ解決契約。porcelain の先頭エントリは使わない（bare repo 構成では先頭が `.bare` になり、default branch の checkout と一致しない） |

### 出力

- 副作用: `<MAIN_REPO>/tmp/kaji/<ISSUE_ID>/` 配下の削除（保護対象とその祖先 dir は残す）
- 結果変数 `tmp_cleanup_result`（スクリプトは `TMP_CLEANUP_RESULT=<値>` を stdout に 1 行出力し、agent が Step 6 で使う）

| 値 | 条件 |
|----|------|
| `削除済み` | 対象 dir を丸ごと削除した（手動起動など保護対象なし） |
| `削除済み（実行中 attempt dir を保持: <path>）` | 保護対象とその祖先以外を削除した |
| `対象なし` | `tmp/kaji/<ISSUE_ID>` が存在しない |
| `スキップ（<理由>）` | 安全条件を満たさず何も削除していない（下記「制約」の各ガード） |
| `一部削除失敗（要手動対応: <path>）` | `rm` / `rmdir` の一部が失敗した、または一部を削除した後に辿る dir を列挙できなかった |

- スクリプトは常に exit 0。どの値でも close の verdict は変えない（PASS 条件・ABORT 条件は既存のまま）
- Step 6a の Issue コメント表と 6b の stdout 表に `| 一時ディレクトリ | [tmp_cleanup_result] |` 行を追加する

### 使用例

harness 経由（`dev.yaml` の `close` step、#508 layout）:

```text
削除前: tmp/kaji/461/{0001..0014}/, tmp/kaji/461/0015/  ← 0015 が close step 自身の KAJI_TMP_DIR
削除後: tmp/kaji/461/0015/ のみ残る
報告:   | 一時ディレクトリ | 削除済み（実行中 attempt dir を保持: /home/u/dev/repo/tmp/kaji/461/0015） |
```

旧 layout（#508 より前の harness で実行した場合）:

```text
削除前: tmp/kaji/461/261018140038/{design,implement,...}/attempt-NNN/, .../close/attempt-001/
削除後: tmp/kaji/461/261018140038/close/attempt-001/ のみ残る（祖先 dir 261018140038/ と close/ は残し、兄弟は削除）
```

手動 `/issue-close 461`（`KAJI_TMP_DIR` 未設定）:

```text
削除後: tmp/kaji/461/ ごと消える。報告: | 一時ディレクトリ | 削除済み |
```

## 制約・前提条件

- 削除対象は `<MAIN_REPO>/tmp/kaji/<ISSUE_ID>` に限る。次の場合は何も削除せず `スキップ（理由）` とする
  - `ISSUE_ID` が空、または数字以外を含む（`..` や `/` を含む値で `tmp/kaji/` 外へ出ることを構造的に防ぐ。
    GitHub provider の正規化済み issue_id は数値。SKILL.md の入力契約）
  - `DEFAULT_BRANCH` の checkout をちょうど 1 件に特定できない、またはその物理パスを解決できない
  - **基点から対象までの各段**（`<base>/tmp`、`<base>/tmp/kaji`、`<base>/tmp/kaji/<ISSUE_ID>`）のいずれかが
    symlink、またはディレクトリでない。`tmp` や `tmp/kaji` が外部を指す symlink の場合も、外部データに触れずスキップする
  - 対象の物理パス（`cd -P && pwd -P`）が、**信頼する基点の物理パス** `<base_real>` から組み立てた
    `<base_real>/tmp/kaji/<ISSUE_ID>` と文字列一致しない（`tmp/kaji` 自身の物理パスとは比較しない。祖先 symlink を
    解決した値どうしを比べると、両辺が同時に境界外へ移って一致してしまうため）
  - `KAJI_TMP_DIR` が設定されているのに物理パスへ解決できない（保護対象を特定できないため安全側に倒す）
  - `KAJI_TMP_DIR` が対象 dir そのものと一致する（保護すると何も消せず、消すと実行中 step を壊す）
  - `CLOSE_VIA_HARNESS=1` なのに `KAJI_TMP_DIR` が未設定（harness 契約違反。保護対象が不明）
  - 削除を始める前に、辿る dir（Issue dir または保護対象の祖先）を列挙できない（読取権限がない等）。
    読めない dir では glob が展開されず子が見えないため、`ls -A` で列挙できることを各段で先に確かめる
    （review-code R1）。一部を削除した後に列挙できなくなった場合は、その dir を `一部削除失敗` として報告する
- 保護対象: `KAJI_TMP_DIR` の物理パスが対象 dir の配下にある場合、その dir と、対象 dir からそこへ至る祖先 dir を
  残す。close step の実行中も終了時も消さない。残った dir は本 Issue では消さない（Issue スコープ境界）
- `KAJI_TMP_DIR` が対象 dir の外（別 project root から `kaji run` した等）なら、保護対象なしとして対象 dir を丸ごと消す。
  保護対象は対象 dir の外にあるので影響しない
- `TMPDIR` / `TMP` / `TEMP` は harness が `KAJI_TMP_DIR` と常に同一値で注入する（`docs/ARCHITECTURE.md`）。
  保護判定は `KAJI_TMP_DIR` のみで行う
- 対象 dir 内の symlink は `rm -rf` でリンク自体だけを消し、リンク先を辿らない。祖先判定は物理パスの文字列前方一致
  （`<child>/` 接頭辞）で行い、symlink の子は祖先として扱わない
- ドットファイル（`.gitignore` 等の `.` 始まりの名前）も削除対象に含める
- 削除基点の信頼範囲: `DEFAULT_BRANCH` の checkout の物理パス `<base_real>` だけを信頼し、それより下の段は
  すべて実 dir（非 symlink）であることを要求する。`<base_real>` 自体やその祖先が symlink 経由でも、`cd -P` で
  解決した実体を基点として扱うので問題ない
- 既存 Step 2 との関係: Step 2 は merge / worktree 削除用に `git worktree list | head -1` を使う。Step 5.6 はこの
  `MAIN_REPO` を使わず、上記の default branch 照合で基点を独立に解決する。Step 2 の先頭エントリ前提を直すことは
  本 Issue の対象外（後続 git 操作は bare repo 上でも動くため、merge / pull の挙動は本 Issue で変えない）
- 実行位置は Step 5.5（Issue クローズ）の成功後・Step 6 の前。Step 5.5 までに ABORT した場合は実行しない
  （Issue が完了していないため。調査用に残す）
- GNU / BSD（macOS）両対応の POSIX sh 構文に限る（`realpath` / `readlink -f` / `find -delete` を使わない）
- #508 との関係: 削除単位は `tmp/kaji/<issue_id>/` で、それより下の構成（`<NNNN>/` か旧 run_id 構成か）に依存しない。
  保護は「保護対象の祖先を辿る」汎用処理なので、どちらの layout でも同じ手順で動く

## 方針

### SKILL.md の変更

1. **Step 5.6「Issue の一時作業ディレクトリ削除」を新設**（Step 5.5 と Step 6 の間）
   - 冒頭に目的、削除しないケース、結果値の表を書く
   - 実行する bash を 1 つのコードブロックとして置く。ブロック先頭行を識別コメント
     `# kaji-issue-tmp-cleanup` で始め、テストがこのブロックを抽出して実行できるようにする
     （先例: `tests/test_issue_close_evidence_jq.py` が SKILL.md の jq プログラムを正規表現で抽出して実行）
   - 冒頭で `ISSUE_ID='[issue_id]'` と `CLOSE_VIA_HARNESS=<0|1>` を agent が埋める旨を書く
   - 「失敗・スキップでも ABORT にしない。結果を記録して Step 6 へ進む」と明記する
2. **Step 6a / 6b の表に `一時ディレクトリ` 行を追加**、「Step 3〜5.5 の結果」→「Step 3〜5.6 の結果」
3. **冒頭の「実行手順」要約と frontmatter `description`** に一時ディレクトリ削除を追記する
4. **Verdict 出力例の evidence** に一時ディレクトリ削除結果を含める。status 選択基準表は変更しない
   （削除結果は PASS / ABORT に影響しないことを表の注記で明示）

### 削除スクリプトの骨子（疑似コード。設計時に scratchpad で GNU bash 上の動作を試作確認済み）

```sh
# kaji-issue-tmp-cleanup
ISSUE_ID='[issue_id]'; DEFAULT_BRANCH='[default_branch]'; CLOSE_VIA_HARNESS=0   # agent が埋める
validate ISSUE_ID ~ ^[0-9]+$                  || skip
main = worktree whose porcelain block has "branch refs/heads/$DEFAULT_BRANCH" (exactly 1, else skip)
base_real = (cd -P main && pwd -P)            || skip
target = base_real/tmp/kaji/$ISSUE_ID
not exists (and not symlink)                  -> "対象なし"
for p in base_real/tmp, base_real/tmp/kaji, target:
    p is symlink or not dir                   -> skip
target_real = (cd -P target && pwd -P)
target_real != target                         -> skip   # 基点由来の文字列と一致すること
if KAJI_TMP_DIR set:
    keep = (cd -P KAJI_TMP_DIR && pwd -P)     || skip
    keep == target_real                       -> skip
    keep under target_real/                   -> protect keep
elif CLOSE_VIA_HARNESS == 1                   -> skip
delete_except(target_real):
    for child in dir/* dir/.[!.]* dir/..?*:
        child == keep                          -> keep
        child is real dir and keep under child/ -> delete_except(child)
        else rm -rf -- child                   (失敗は記録して続行)
no keep -> rmdir target_real
echo "TMP_CLEANUP_RESULT=..."; exit 0
```

### docs の変更

- `docs/ARCHITECTURE.md` § attempt 固有の一時作業ディレクトリ: 「作成した dir は自動削除しない」を、
  「runner は作成した dir を削除しない。Issue 単位の dir は `issue-close` が Issue close 後に削除する
  （harness 経由では close step 自身の attempt dir とその祖先を残す。それ以外の契機・保持期間による削除はない）」へ更新
- `docs/dev/skill-authoring.md`（exec_script の入力 env 表の後の段落）: 同趣旨に更新し ARCHITECTURE.md を参照
- `docs/dev/development_workflow.md:262` と `docs/guides/git-worktree.md:155` の `/issue-close` 役割説明に
  「Issue の一時作業ディレクトリ削除」を追記

### 検討した代替案

| 案 | 内容 | 不採用理由 |
|----|------|-----------|
| A. Issue dir を丸ごと削除 | 保護なしで `rm -rf tmp/kaji/<id>` | 実行中 close step の scratchpad / TMPDIR を消す（Issue 本文の実装上の制約）。完了条件違反 |
| B. kaji CLI サブコマンド（`kaji tmp clean <id>` 等）を新設 | Python で実装し pytest で検査 | CLI 公開面の追加・`docs/cli-guides/` 更新が増え、完了条件（SKILL.md に手順）より大きい。runner の削除は Issue スコープ外。SKILL.md 内 bash でも抽出実行テストで十分に検査できる |
| C. skill dir に別 script ファイルを置く | `.claude/skills/issue-close/scripts/*.sh` | skill 内に実行ファイルを置く先例がなく、managed starter 配布物の構成も変わる。SKILL.md 内ブロック + 抽出テストの先例（evidence jq）に合わせる |
| D. close step 終了後に runner が消す | harness 側で削除 | Issue スコープ境界「`issue-close` 以外の契機での削除」に当たり対象外 |

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 一時ディレクトリをいつ削除するか | `issue-close` で、その Issue の `tmp/kaji/<issue_id>/` を削除する | 人間の決定（Issue 本文「重要判断」: 2026-10-18 の対話で「削除は提案内容でOK」と承認） | 実行位置を Step 5.5（Issue クローズ成功）の後・Step 6 の前に置く。close 前に ABORT した場合は削除しない |
| close step 自身の attempt dir の扱い | 保護対象（`KAJI_TMP_DIR`）とその祖先を残し、それ以外を削除。実行中・終了時とも消さない | AI の仮定（Issue 本文「重要判断」、two-way door）。検査先: review-design | 保護対象は `KAJI_TMP_DIR` の物理パス。祖先判定は物理パスの `<child>/` 前方一致。`KAJI_TMP_DIR` が解決不能・対象 dir と一致・harness 経由なのに未設定の場合はスキップ（fail-safe） |
| 削除失敗時の扱い | close 全体を ABORT にせず報告に記録 | AI の仮定（Issue 本文「重要判断」、根拠: 一時 dir は使い捨てで PR merge / Issue close より優先度が低い。two-way door）。検査先: review-design | スクリプトは常に exit 0。結果値 5 種（削除済み / 保持付き削除済み / 対象なし / スキップ / 一部削除失敗）を Step 6 に記録 |
| 削除境界の安全条件 | `tmp/kaji/` 直下のその Issue の dir に限る。空 issue_id・`tmp/kaji/` 外を指す path では削除しない | Issue 完了条件（人間決定の範囲） | `^[0-9]+$` 検証、基点から対象までの各段（`tmp` / `tmp/kaji` / `<id>`）の symlink・非 dir 拒否、基点物理パス由来の文字列との一致検証、対象内 symlink は辿らない（review-design R2 で祖先 symlink 対策を追加） |
| 削除基点の解決 | Issue 完了条件の「main repo」を `default_branch` の checkout と解釈する | Issue 完了条件 + 既存契約（`resolve_main_worktree` が `refs/heads/<default_branch>` を照合）。review-design R1 で porcelain 先頭エントリ案を撤回 | porcelain から `branch refs/heads/<DEFAULT_BRANCH>` のエントリをちょうど 1 件選ぶ。0 件・複数件・解決不能はスキップ |
| #508 との関係 | 削除単位を `tmp/kaji/<issue_id>/` とし下位構成に依存しない | AI の確認（Issue 本文「重要判断」）。#508 は main に取り込み済み（`9ccbdf3`）で `tmp/kaji/<issue_id>/<NNNN>/` | 保護処理を「祖先を辿る」汎用処理にし、旧 run_id layout（現行インストール版 harness が作る `tmp/kaji/509/261019010253/design/attempt-001` 等）でも同じ手順で動く |
| 実装形態（SKILL.md 内 bash） | CLI や別 script ではなく SKILL.md のコードブロックとして置く | AI の仮定（two-way door。後から CLI 化できる）。根拠: 完了条件が SKILL.md の手順を要求、抽出実行テストの先例あり。検査先: review-design | 識別コメント `# kaji-issue-tmp-cleanup` で始まる単一ブロックにし、テストが抽出して実行する |

one-way door の未決はない（削除時期は人間決定済み、その他はすべて skill 手順で後から変更可能）。

## テスト戦略

### 変更タイプ

実行時の振る舞いを変える変更（skill 手順の追加。agent が実行する bash ロジックを含む）。
恒久回帰テストを追加する（新ファイル `tests/test_issue_close_tmp_cleanup.py`）。

### 実行時コード変更の場合

#### Small テスト

SKILL.md の静的構造（ファイル読み込みのみ。既存 `tests/test_skill_harness_adaptation.py` の small と同じ扱い）:

- `# kaji-issue-tmp-cleanup` で始まるコードブロックがちょうど 1 つ抽出できる
- Step 5.6 が Step 5.5 の後・Step 6 の前にある
- Step 6a・6b の両方の表に `一時ディレクトリ` 行と `[tmp_cleanup_result]` がある
- ブロックが `realpath` / `readlink -f` / `find ... -delete` を使わない（BSD 互換の制約）

#### Medium テスト

不要（独立した Medium 層を設けない）。本変更の振る舞いロジックは SKILL.md 内の bash にあり、Python の in-process
コード（`CliRunner` で駆動できる内部サービスや、`tmp_path` に対して Python 関数が読み書きするロジック）を追加しない。
ファイルシステムに対する削除の振る舞いは、bash の実プロセスを起動しないと検証できないため、下記の Large
（`large_local`）で検証する。`docs/reference/testing-size-guide.md` § subprocess / コマンド実行の「実際の CLI コマンドを
subprocess で実行 → Large」に従う。先例 `tests/test_issue_close_evidence_jq.py` が medium であることは、新規検証の
分類根拠にしない。

#### Large テスト（`@pytest.mark.large` + `@pytest.mark.large_local`）

抽出したブロックを実 `bash` subprocess で実行し、実 `git` で作った fixture に対する削除結果を検証する。外部ネットワーク
（GitHub API 等）は使わないため `large_local` を併記する（`make test-large-local` で単独実行でき、`make check` の
`pytest` 全体実行にも含まれる）。

fixture と実行条件:

- **fixture は project 外に作る**: `tests/conftest.py` の `outside_project_tmp_path` を使い、`tmp_path` は使わない。
  workflow 内では `tmp_path` が kaji repo 内（`tmp/kaji/<issue_id>/...`）になる。fixture の git 構築が途中で失敗すると、
  subprocess の `git worktree list` が実 kaji repo を返し、実 repo の `tmp/kaji/<id>` が削除対象になりうる。設計時の
  試作でも、構築に失敗した dir から実 kaji repo が解決される事象を観測した（このときは対象 dir が存在せず、削除は
  起きていない）
- **前提の自己検査**: スクリプトの実行前に、fixture の cwd で `git worktree list --porcelain` を実行する。
  `refs/heads/main` のエントリが fixture 内の main checkout を指すことを assert し、満たさなければ fail する（skip しない）
- **構成**: bare repo（`.bare`）+ `main` を checkout した linked worktree + feature branch の linked worktree。
  先頭エントリが bare になる kaji の標準構成（`docs/guides/git-worktree.md`）を再現する。cwd は feature worktree
  （close 時に main 以外から実行されうる状況）と main checkout の両方で検証する
- プレースホルダー（`[issue_id]` / `[default_branch]` / `CLOSE_VIA_HARNESS`）はテスト側で置換し、`KAJI_TMP_DIR` は
  `env` で制御する

検証観点:

- **削除基点の解決（R1）**: bare + main + feature の構成で、`main/tmp/kaji/<id>` だけが消える。`.bare/tmp/kaji/<id>` と
  `feat/tmp/kaji/<id>`（ダミー）は残る。`DEFAULT_BRANCH` が存在しない branch 名ならスキップし、何も消えない
- **祖先 symlink の拒否（R2）**: `main/tmp/kaji` が project 外の dir を指す symlink のとき、および `main/tmp` が外部を指す
  symlink のときはスキップする。リンク先の `<id>/` とその中身は変更されない（ファイルの存在と内容で確認する）
- **削除境界**: 対象 Issue の dir だけが消え、隣の Issue dir（`tmp/kaji/<別 id>/`）と `tmp/kaji/.gitignore` は残る
- **保護（#508 layout）**: `KAJI_TMP_DIR=tmp/kaji/<id>/0003` のとき 0003 とその中身が残り、0001・0002 と
  Issue dir 直下のファイルは消える。結果は `削除済み（実行中 attempt dir を保持: ...）`
- **保護（旧 layout）**: `KAJI_TMP_DIR=tmp/kaji/<id>/<run>/close/attempt-001` のとき祖先 `<run>/`・`close/` と保護 dir が残り、
  兄弟 step・兄弟 attempt・別 run は消える
- **手動起動**: `KAJI_TMP_DIR` 未設定・`CLOSE_VIA_HARNESS=0` で Issue dir ごと消え `削除済み`
- **対象なし**: dir が無ければ `対象なし`、何も変わらない
- **スキップ**: issue_id が空 / `../x` / `12a`、対象が symlink（リンク先が残ること）、`KAJI_TMP_DIR` が解決不能、
  `KAJI_TMP_DIR` が Issue dir と一致、`CLOSE_VIA_HARNESS=1` かつ `KAJI_TMP_DIR` 未設定 — いずれも何も消えない
- **対象外の保護対象**: `KAJI_TMP_DIR` が Issue dir の外なら Issue dir が丸ごと消え、`KAJI_TMP_DIR` 側は無傷
- **対象内 symlink**: 対象 dir 内の symlink（他 Issue dir を指す）を消してもリンク先は残る
- **ドットファイル**: `.hidden` ファイル / dir も消える
- **削除失敗**: 書き込み権限を外した子 dir を置くと `一部削除失敗（要手動対応: ...）` を出し、exit code は 0
  （root 実行時は権限で失敗しないため skip 条件付き）
- **列挙失敗**: 読取権限だけを外した dir（Issue dir、または保護対象の祖先）に削除前に到達すると
  `スキップ（列挙できない: ...）` で何も消えない。一部削除後に到達すると `一部削除失敗（要手動対応: <その dir>）`
  を出し、保護対象は残る（root 実行時は skip 条件付き。review-code R1）
- **常に exit 0**: 上記すべてのケースで exit code 0、stdout に `TMP_CLEANUP_RESULT=` 行がちょうど 1 行

`large_forge`（実 GitHub API）は追加しない。harness 経由で実際の close step が自身の attempt dir を残すことは、merge 後の
実運用でしか再現できないため、Issue 本文の `### ワークフロー完了後の確認項目` で扱う
（`docs/dev/workflow_completion_criteria.md` の分類どおり）。`KAJI_TMP_DIR` が全 dispatch 経路へ注入されること自体は、
#407 / #508 の既存テストが保護している。

### 長時間 acceptance の配置

該当なし。追加する検証は pytest の Small と Large（`large_local`。ローカルの bash / git 実行のみで、合計して秒オーダー）と
`make check` だけで、nested `kaji run` や外部 agent を含む dogfood はない。harness 経由の実確認は merge 後の事後確認項目で
あり、workflow の step 内では実行しない。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない（削除時期は Issue で人間決定済み） |
| docs/ARCHITECTURE.md | あり | § attempt 固有の一時作業ディレクトリの「作成した dir は自動削除しない」を issue-close による削除方針へ更新 |
| docs/dev/skill-authoring.md | あり | exec_script 入力 env 説明の「作成した dir は自動削除されない」を同様に更新 |
| docs/dev/development_workflow.md | あり | `/issue-close` の役割説明（262 行付近）に一時ディレクトリ削除を追記 |
| docs/dev/workflow_guide.md | なし | `issue-close` の詳細役割は SKILL.md を参照する構成で、削除処理への言及箇所がない |
| docs/dev/testing-convention.md | なし | `tmp_path` が workflow 内でリポジトリ内になる説明は不変（削除は close 後のみ） |
| docs/reference/ | なし | `logging.md` の `tmp_dir` 記述は runner の挙動で不変 |
| docs/cli-guides/ | なし | CLI 仕様の変更なし |
| docs/guides/git-worktree.md | あり | `/issue-close` の処理列挙（155 行付近）に一時ディレクトリ削除を追記 |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |
| CHANGELOG.md | なし（Release 時） | Release 作業（`/release`）で記載する運用 |
| managed starter | なし（本 Issue 外） | Issue スコープ境界: Release 後の `/update-starter` で反映 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #509 本文 | https://github.com/apokamo/kaji/issues/509 | 完了条件（削除対象の限定、close step 自身の `$KAJI_TMP_DIR` 非削除、失敗で ABORT しない、Step 6 報告、docs 整合）と「重要判断」（削除時期は 2026-10-18 人間承認） |
| issue-close SKILL.md | `.claude/skills/issue-close/SKILL.md` | Step 2 で main repo を `git worktree list` 先頭から得る（Step 5.6 はこれに依存しない）。Step 2.5.1 は `[verdict_path]` の有無で harness 起動を判定。Step 5.5 の close 失敗は ABORT。Step 6a / 6b の報告表 |
| ARCHITECTURE.md | `docs/ARCHITECTURE.md` § attempt 固有の一時作業ディレクトリ（Issue #407 / #508） | `KAJI_TMP_DIR` / `TMPDIR` / `TMP` / `TEMP` は常に同一値で全 dispatch 経路に注入、layout は `<project_root>/tmp/kaji/<issue_id>/<NNNN>/`、「作成した dir は自動削除しない」 |
| skill-authoring.md | `docs/dev/skill-authoring.md` § exec_script 入力 env | agent 経路を含む全 dispatch に 4 変数が入る。「作成した dir は自動削除されない」 |
| 先例テスト | `tests/test_issue_close_evidence_jq.py` | SKILL.md のコードブロックを正規表現で抽出し subprocess で実行する medium テストの先例 |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L の定義（ファイル I/O 結合は Medium）、`tmp_path` が workflow 内でリポジトリ内になる注意 |
| 完了条件の分類 | `docs/dev/workflow_completion_criteria.md` § workflow 内完了条件と事後確認の分離 | merge 後の harness 実確認は事後確認項目へ分離する基準 |
| POSIX `cd -P` / `pwd -P` | https://pubs.opengroup.org/onlinepubs/9799919799/utilities/cd.html , https://pubs.opengroup.org/onlinepubs/9799919799/utilities/pwd.html | `-P` は symlink を解決した物理パスで動作 / 出力する。GNU・BSD 共通で使える物理パス解決手段（`realpath` / `readlink -f` に依存しない） |
| POSIX `rm` | https://pubs.opengroup.org/onlinepubs/9799919799/utilities/rm.html | `-R` は symlink を辿らずリンク自体を削除する（対象内 symlink の先を消さない根拠） |
| git worktree | https://git-scm.com/docs/git-worktree#_porcelain_format | `--porcelain` は worktree ごとに `worktree <path>` で始まる属性ブロックを空行区切りで出し、checkout 中の branch は `branch refs/heads/<name>`、bare は `bare` 属性で示す。先頭エントリは Git のいう main worktree で、bare 構成では bare repo になる。default branch の checkout とは `branch` 行で照合する必要がある（R1） |
| main checkout の解決契約 | `kaji_harness/providers/_worktree.py::resolve_main_worktree` | `git worktree list --porcelain` を parse し、`branch == refs/heads/<default_branch>` のエントリを main worktree とする。一致 0 件はエラー |
| テストサイズ判断 | `docs/reference/testing-size-guide.md` § subprocess / コマンド実行、§ Large の細分マーカー | 実コマンドの subprocess 実行は Large、外部ネットワークなしは `large_local`（R3） |
| project 外 fixture | `tests/conftest.py::outside_project_tmp_path`、`docs/dev/testing-convention.md` § workflow 内実行での `tmp_path` | workflow 内では `tmp_path` がリポジトリ内になる。祖先に `.git` を持たない dir を作り、満たせなければ fail する |
| 実測（現行 harness の layout） | 本 design step の `KAJI_TMP_DIR=/home/aki/dev/kaji/main/tmp/kaji/509/261019010253/design/attempt-001` | インストール済み harness は旧 run_id layout を作るため、保護処理は多段の祖先に対応する必要がある |
