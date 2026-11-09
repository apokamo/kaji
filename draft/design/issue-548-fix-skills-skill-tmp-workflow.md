# [設計] skill の一時ファイルを Issue / attempt ごとに分かれる場所へ移す

Issue: #548

## 概要

`.claude/skills/` の手順に残る Issue 番号を含まない `/tmp` 固定パス（`/tmp/issue-body.md` など）を、
harness 経由では attempt 固有の `KAJI_TMP_DIR`、手動起動では main checkout の `tmp/kaji/<issue_id>/` 配下へ置き換える。
`.claude/skills/` に Issue 番号を含まない絶対 `/tmp/` パスが残っていないことを、契約テストで恒久的に検査する。

## 背景・目的

### Observed Behavior（OB）

main `e0fca31` の worktree で `grep -rn "/tmp" .claude/skills --include=*.md` を実行すると、次の固定パスが出る（行番号は実測）。

| 箇所 | 固定パス | 用途 |
|------|----------|------|
| `issue-close/SKILL.md:148,151` | `/tmp/kaji-open-issues.json`、`/tmp/kaji-follow-up-candidates.txt` | follow-up Issue の完全一致検索 |
| `issue-close/SKILL.md:166,197,205` | `/tmp/kaji-follow-up-body.md` | follow-up Issue 本文の作成・永続化 |
| `issue-close/SKILL.md:222,225` | `/tmp/kaji-parent-body-with-follow-up.md` | 親 Issue 本文へのマーカー追記 |
| `i-dev-final-check/SKILL.md:168,171,174` | `/tmp/issue-body.md` | 完了条件チェックボックス更新 |
| `i-dev-final-check/SKILL.md:246` | `/tmp/issue-body-updated.md` | 設計書を Issue 本文へ挿入 |
| `i-doc-final-check/SKILL.md:80,82` | `/tmp/issue-body.md` | 完了条件チェックボックス更新 |
| `issue-design/SKILL.md:564` | `/tmp/issue-body.md` | 完了条件の workflow 内 / 外の移動 |
| `release/SKILL.md:272,278` | `/tmp/open-tracking.json` | starter tracking-plan への入力 |

Issue 番号を含む `/tmp` パス（完了条件で扱いの決定を求められたもの）:

| 箇所 | パス |
|------|------|
| `incident-review/SKILL.md:73,94` | `git worktree add --detach /tmp/kaji-incident-review-[issue_id] HEAD` / `git worktree remove --force ...` |
| `_shared/implement-by-type/refactor.md:25,62` | `/tmp/baseline-<issue>.txt`、`/tmp/after-<issue>.txt` |

範囲外として確認済み: `grill-me/SKILL.md:122` は `mktemp "${TMPDIR:-/tmp}/kaji-grill-me.XXXXXX"` で、
呼び出しごとに一意な名前を作る（固定パスではない）。harness 経由では `TMPDIR` = `KAJI_TMP_DIR` になる。

2 つの workflow が同時に `issue-close` や final-check に入ると、別 Issue の本文を同じファイルへ書き、
`kaji issue edit [issue_id] --commit --body-file /tmp/issue-body.md` で他 Issue の本文を自 Issue に書き込みうる。
`kaji run-series` の並列運用（利用側で実施中）で現実に起こりうる。

### Expected Behavior（EB）

skill の一時ファイルは Issue または attempt ごとに分かれる場所に置き、同時に流れる別 Issue の workflow とパスが重ならない。

根拠（既存契約）:

- `docs/dev/skill-authoring.md:164-174`: runner は agent step を含む全 dispatch 経路に attempt 固有の
  `KAJI_TMP_DIR`（実体 `<project_root>/tmp/kaji/<issue_id>/<NNNN>/`）を注入し、「kaji 固有の処理は
  `$KAJI_TMP_DIR` を明示参照してよい」
- `issue-close/SKILL.md:587-` Step 5.6: Issue close 後に `<main checkout>/tmp/kaji/[issue_id]/` を削除する（#509）

### 再現手順（Steps to Reproduce）

1. 前提: main `e0fca31` の checkout（本 worktree の基点）
2. 実行: `grep -rn "/tmp/" .claude/skills --include=*.md`
3. 観測: 上表の Issue 番号を含まない固定パスが出力される

同時実行での実際の上書きは観測していない（Issue 本文の記載どおり、パスの共有から導いたリスク）。
したがって実世界障害ログによる Red 代替は使わず、契約テストの修正前 FAIL を Red 証跡とする。

### 根本原因（Root Cause）

- **なぜ間違っているか**: 一時ファイルの置き場を「プロセスごとに分かれる場所」ではなく、ホスト全体で共有される
  `/tmp` 直下の固定名にしている。名前に Issue 番号も実行ごとの一意性もないため、同時実行時に名前空間が衝突する
- **いつから**: `/tmp/issue-body.md` は `ced857b`（2026-04-25、#148 で kamo2 版 skill を移植）から。
  `issue-close` の follow-up 系は `4561f32`（2026-07-15、#327）、`release` の `/tmp/open-tracking.json` は
  `b5583d6`（2026-09-01、#423）で追加された。`KAJI_TMP_DIR` は #407 / #508 で後から導入されたため、
  先行して書かれた手順が置き換えられないまま残った
- **同根の他箇所**: `.claude/skills/` 全体を上記 grep で調査し、上表が全件。`.claude/agents/` / `.agents/skills/` に
  `/tmp` 参照はない。skill 外では `docs/dev/workflow_completion_criteria.md:207,213` が同じ
  `/tmp/issue-body.md` 手順を人間向けに記載している（影響ドキュメントで扱う）

## インターフェース

### 入力

skill 手順の利用者（agent）から見た入力:

- 環境変数 `KAJI_TMP_DIR`（harness 経由では runner が必ず設定。手動起動では未設定）
- 既存 placeholder `[issue_id]`、`[default_branch]`（手動起動の fallback で main checkout を特定するために使う）

### 出力

- 新しい共通手順 `.claude/skills/_shared/skill-tmp-resolve.md`。実行すると skill 用一時ディレクトリの絶対パスを
  1 行で標準出力に出す。agent はこの値を placeholder `[skill_tmp_dir]` として以降のコマンドに埋め込む
- 各 skill の一時ファイルは `[skill_tmp_dir]/<ファイル名>` に置く
- 副作用（手動起動時のみ）: `<main checkout>/tmp/kaji/[issue_id]/manual-XXXXXX/` を作成し、
  `<main checkout>/tmp/kaji/.gitignore`（内容 `*`）が無ければ作る（runner の `prepare_attempt_tmp_dir` と同じ規則）

公開 CLI・Python API・workflow YAML の変更はない。

### 使用例

共通手順のブロック（実装時に `_shared/skill-tmp-resolve.md` に置く。疑似コードではなく完成形の方針を示す）:

```sh
# kaji-skill-tmp-resolve
ISSUE_ID='[issue_id]' DEFAULT_BRANCH='[default_branch]' sh -s <<'KAJI_SKILL_TMP_EOF'
set -eu
if [ -n "${KAJI_TMP_DIR:-}" ]; then          # harness 経由: attempt 固有 dir をそのまま使う
  # `cd ... && pwd` の形は AND リストの左辺の失敗を set -e が拾わず exit 0 で成功扱いになるため使わない
  RESOLVED=$(CDPATH= cd -P -- "$KAJI_TMP_DIR" && pwd -P) || {
    echo "skill-tmp-resolve: KAJI_TMP_DIR を解決できない: $KAJI_TMP_DIR" >&2; exit 1
  }
  [ -n "$RESOLVED" ] || { echo "skill-tmp-resolve: KAJI_TMP_DIR の解決結果が空" >&2; exit 1; }
  printf '%s\n' "$RESOLVED"
  exit 0                                     # 手動経路へは fallback しない
fi
case $ISSUE_ID in ''|*[!0123456789]*) echo "skill-tmp-resolve: issue_id が数字でない: $ISSUE_ID" >&2; exit 1 ;; esac
# default branch の checkout をちょうど 1 件特定する（issue-close Step 5.6 と同じ方式。bare 構成対応）
MAIN=...（git worktree list --porcelain を while read で走査）
# 0 件 / 複数件は明示的に判定して exit 1（パイプラインの終了状態には頼らない）
mkdir -p "$MAIN/tmp/kaji/$ISSUE_ID"
[ -e "$MAIN/tmp/kaji/.gitignore" ] || printf '*\n' > "$MAIN/tmp/kaji/.gitignore"
mktemp -d "$MAIN/tmp/kaji/$ISSUE_ID/manual-XXXXXX"
KAJI_SKILL_TMP_EOF
```

利用側 skill の書き換え例（`i-dev-final-check`）:

```bash
# 事前に _shared/skill-tmp-resolve.md で [skill_tmp_dir] を解決しておく
kaji issue view [issue_id] --json body -q '.body' > [skill_tmp_dir]/issue-body.md
kaji issue edit [issue_id] --commit --body-file [skill_tmp_dir]/issue-body.md
```

`release` の書き換え例（一時ファイルを使わない）:

```bash
OPEN_TRACKING_JSON=$(kaji issue list --label starter-sync --state open --json number,body \
  | jq '[.[] | {issue_id: .number, body: .body}]')
kaji starter tracking-plan <<EOF
{ ..., "open_tracking_issues": ${OPEN_TRACKING_JSON}, ... }
EOF
```

## 制約・前提条件

- Claude Code の Bash は呼び出しごとにシェル状態が消える（`_shared/worktree-resolve.md` 注意事項）。手動起動の
  fallback は呼び出しごとに新しい dir を作るため、**解決は skill 内で 1 回だけ行い、絶対パスを `[skill_tmp_dir]` として
  使い回す**。`$KAJI_TMP_DIR` を各コマンドで直接参照する書き方は手動起動で壊れるため採らない
- 解決に失敗した場合（default branch の checkout が 0 件 / 複数、issue_id が数字でない等）は `/tmp` へ fallback しない
  （fallback すると衝突が再発する）。その skill の失敗時規則に従い停止する（final-check / issue-design は `ABORT`、
  issue-close の follow-up 移管は既存どおり `ABORT` で親 Issue を close しない）
- **失敗の伝播（共通ブロックの契約）**: 解決できないときは必ず非 0 で終了し、標準出力に何も出さない。
  `cmd1 && cmd2` の左辺や、パイプライン・`if` 条件内のコマンドの失敗は `set -e` で止まらないため、
  失敗しうる手順（`KAJI_TMP_DIR` の `cd` / `pwd`、default branch checkout の特定）は `|| { ...; exit 1; }` または
  明示的な件数・空文字判定で exit 1 にする。`mkdir` / `printf` / `mktemp` は単純コマンドなので `set -e` で止まる
- **利用側の契約**: 各 skill は解決ブロックの終了コードが非 0、または標準出力が空のとき、一時ファイルを使う後続コマンド
  （Issue 本文の取得・編集、follow-up 作成など）に進まず、その skill の失敗時規則で停止する。これを
  `_shared/skill-tmp-resolve.md` の利用規則に明記する
- POSIX sh で書く（GNU / BSD 両対応。`realpath` / `readlink -f` / `mktemp --tmpdir` を使わない。
  `mktemp -d <template>` は両対応）
- skill markdown に `$<数字>` を書かない（`tests/test_skill_placeholders.py` の Issue #524 契約。awk の `$1` も不可）
- 新 placeholder は `[snake_case]` 形式（`tests/test_skill_placeholders.py` の forbidden list に抵触しない）
- 手動起動で作る `manual-XXXXXX` は runner の採番規則 `^[0-9]{4,6}$` に一致しないため、runner の通し番号と干渉しない
- 手動起動で作った dir は `issue-close` Step 5.6 が Issue close 後に Issue dir ごと削除する（既存契約で回収される）

## 方針

### 置き換え先（箇所ごとの決定）

| 箇所 | 置き換え先 | 理由 |
|------|-----------|------|
| `issue-close` follow-up 4 ファイル | `[skill_tmp_dir]`（harness: `KAJI_TMP_DIR` / 手動: `tmp/kaji/<id>/manual-*`） | Issue 本文を読み書きする。Step 5.4 で使い、Step 5.6 の削除より前に用済みになる |
| `i-dev-final-check` 2 ファイル | `[skill_tmp_dir]` | Issue 本文を読み書きする |
| `i-doc-final-check` 1 ファイル | `[skill_tmp_dir]` | 同上 |
| `issue-design` Step 2.5 の 1 ファイル | `[skill_tmp_dir]` | 同上 |
| `_shared/implement-by-type/refactor.md` の baseline / after | `[skill_tmp_dir]/baseline.txt`、`[skill_tmp_dir]/after.txt` | 単純なファイル。同一 implement attempt 内で記録・報告に使う。`<issue>` は正規 placeholder でもない |
| `release` の `/tmp/open-tracking.json` | 一時ファイルを廃止し、シェル変数で渡す | release は Issue に紐づかず（`issue_id` が無い）、`tmp/kaji/<issue_id>/` も `KAJI_TMP_DIR` も使えない。1 回の Bash 呼び出しで完結する |
| `incident-review` の `/tmp/kaji-incident-review-[issue_id]` | **現状維持** | Issue 番号を含み、異なる Issue 間では衝突しない。git worktree を main checkout の `tmp/` 配下に作ると、nested worktree になる。issue-close Step 5.6 の `rm -rf` が `git worktree remove` を経ずに消し、stale な worktree 登録が残る。インシデント Issue は issue-close を通らず回収経路もない。同一インシデントの査読を並列に回す運用はない |

### 共通手順の追加

- `.claude/skills/_shared/skill-tmp-resolve.md` を新設し、上記「使用例」のブロック（識別コメント `# kaji-skill-tmp-resolve`）と
  利用規則（1 回解決して使い回す・失敗時に `/tmp` へ fallback しない・自分で削除しなくてよい理由）を書く
- 利用する各 skill は、一時ファイルを使う最初の手順の直前に `_shared/skill-tmp-resolve.md` を参照する 1 文を置き、
  パスを `[skill_tmp_dir]/<ファイル名>` に置換する。共通ブロックを各 skill に複製しない（単一情報源）

### 最小侵襲

- ファイル名は既存名を維持する（`issue-body.md` など）。dir が分かれるため `kaji-` 接頭辞は不要だが、変更理由がないので残す
- 一時ファイルを使うロジック（jq 式、本文の組み立て）は変更しない

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 置き換え先の候補 | `KAJI_TMP_DIR` か main checkout の `tmp/kaji/<issue_id>/` | Issue 本文「概要」「完了条件 1」（人間決定）。review-ready コメントで既存契約（#508 / #509）との整合を確認済み | harness 経由は `KAJI_TMP_DIR`、手動起動は `tmp/kaji/<issue_id>/` 配下と、起動経路で使い分ける共通手順に具体化 |
| 手動起動時の一意性 | `tmp/kaji/<issue_id>/` 直下に `mktemp -d` で `manual-XXXXXX` を作る | AI の仮定。根拠: 同一 Issue の手動起動を複数 session で同時に流す可能性を安価に排除でき、runner の採番 `^[0-9]{4,6}$` と干渉しない。検査先: review-design / review-code | 命名、`.gitignore` の作成規則を runner に合わせた |
| 解決失敗時の扱い | `/tmp` へ fallback せず停止 | AI の仮定。根拠: fallback は本 Issue の衝突を再発させる。検査先: review-design | 各 skill の既存の失敗時規則（ABORT）に委ねる |
| `release` の扱い | 一時ファイルを廃止しシェル変数で渡す | AI の仮定。根拠: release は Issue に紐づかず、Issue 指定の 2 候補をどちらも使えない。ファイル自体をなくせば衝突も起きない。検査先: review-design | 1 回の Bash 呼び出しで完結させる手順にする |
| `incident-review` の扱い | 現状維持 | Issue 本文「完了条件 2」が扱いの決定を要求（人間決定は「設計書で決める」まで）。現状維持は AI の判断。根拠は「方針」表。検査先: review-design | 契約テストで「`[issue_id]` を含む `/tmp/` パスは許容」とする規則に落とした |
| `refactor.md` の扱い | `[skill_tmp_dir]` へ移す | 同上（完了条件 2）。AI の判断。根拠: 単純ファイルで移せない理由がない。`<issue>` は正規 placeholder ではない。検査先: review-design | ファイル名を `baseline.txt` / `after.txt` にした（dir が attempt / Issue 固有のため） |
| one-way door の有無 | 該当なし | Issue 本文「重要判断」に「該当なし（skill の内部の一時パス。後段の review で直せる two-way door）」（人間決定）。本設計も公開 CLI / API / データ契約 / workflow 遷移を変えない | — |

## テスト戦略

### 変更タイプ

- instruction-only（skill markdown の手順変更）＋ 恒久契約テストの追加。Python 実行時コードの変更はない。
  ただし共通手順のシェルブロックは agent が実行するコードなので、実行して挙動を検証する

### 新規テストファイル

`tests/test_skill_tmp_paths.py`（命名はテスト対象ドメイン。フェーズ番号を含めない）

### Small テスト

- 共通手順 `_shared/skill-tmp-resolve.md` に、識別コメント `# kaji-skill-tmp-resolve` 付きブロックがちょうど 1 つあり、
  抽出できる。placeholder `[issue_id]` / `[default_branch]` を各 1 回含む
- 一時ファイルを使う 5 箇所（`issue-close` / `i-dev-final-check` / `i-doc-final-check` / `issue-design` /
  `_shared/implement-by-type/refactor.md`）が `skill-tmp-resolve.md` を参照し、`[skill_tmp_dir]/` を使う
  （置換漏れや参照の脱落を検出する）
- `release/SKILL.md` の tracking-plan 手順にファイル経由の受け渡しが無い（`/tmp/` も `[skill_tmp_dir]` も現れない）

（`tests/test_issue_close_tmp_cleanup.py` の前例に合わせ、単一ファイルの静的構造は small とする）

### Medium テスト（Red → Green の再現テスト）

- **`.claude/skills/` 全体の固定 `/tmp/` パス禁止**: `.claude/skills/**/*.md` を走査し、絶対パスの `/tmp/`
  （直前が行頭・空白・引用符 `"` `'`・バッククォート・`(`・`=` のいずれか。例: 本文中の `` `/tmp/kaji-follow-up-body.md` `` もバッククォート直後として検出する）を検出する。検出したパスの残り部分に `[issue_id]` を
  含まないものを違反として、`ファイル:行: パス` の形で列挙して FAIL させる
  - 修正前: 上記 OB の 8 箇所（16 行）と `refactor.md` 2 行が違反として出て **FAIL**
  - 修正後: 違反 0 件で **PASS**（`incident-review` は `[issue_id]` を含むため許容）
  - 誤検出しないもの: `$BASE/tmp/kaji/...`、`<main checkout>/tmp/kaji/...`、`${TMPDIR:-/tmp}/...`（直前文字が規則外、
    または `/tmp}` で `/tmp/` に一致しない）
- テストは対象パスの一覧を持たず全件走査する（新たな固定パスの混入も検出する）
- 複数ファイルの I/O を伴う全件走査なので medium（`tests/test_skill_placeholders.py` と同じ扱い）

### Large テスト（large_local）

`tests/test_issue_close_tmp_cleanup.py` と同じく、抽出したブロックを実 `sh` で実行し、実 `git` で作った fixture に対して
検証する。fixture は `outside_project_tmp_path` に作る（kaji repo 内に作ると、fixture 構築の失敗時に `git worktree list` が
実 repo を返しうるため）。

- `KAJI_TMP_DIR` が設定されているとき: その物理パスを出力し、main checkout に何も作らない
- `KAJI_TMP_DIR` が存在しない dir を指すとき（review-design MF-1 の回帰テスト）: 非 0 で終了し、標準出力が空で、
  手動経路の `<main>/tmp/kaji/<id>/` や `manual-*` を作らない（fallback しない）。修正前の `cd ... && pwd -P; exit 0`
  形では exit 0・stdout 空になり FAIL する
- `KAJI_TMP_DIR` が未設定のとき: `<main>/tmp/kaji/<id>/manual-*` を作ってその絶対パスを出力し、
  `<main>/tmp/kaji/.gitignore`（内容 `*`）を作る。2 回実行すると別々の dir になる（同時実行でも衝突しない）
- linked worktree から実行しても、default branch の checkout 配下に作る（bare + linked worktree 構成を含む）
- `issue_id` が数字でない / default branch の checkout が見つからないとき: 非 0 で終了し、`/tmp` に何も作らない

外部ネットワークは使わない（subprocess のみ）ので `large_local`。

### 変更固有検証

- `make check`（ruff / mypy / pytest 全体。新規テストを含む）
- `grep -rn "/tmp/" .claude/skills --include=*.md` で、残るのが次だけであることを目視確認する。
  絶対パスは `incident-review` の `[issue_id]` 付き 2 行だけ。ほかは既存の相対表記で、意図して残す安全な行:
  `issue-close/SKILL.md` の `<main checkout>/tmp/kaji/[issue_id]/`（590 行）と `$BASE/tmp/...`（638・640 行）、
  新設する `_shared/skill-tmp-resolve.md` の `$MAIN/tmp/kaji/...`。判定基準は契約テストの絶対パス規則と同じ

### 長時間 acceptance の配置

該当なし。nested `kaji run`、外部 agent を含む dogfood、fresh 環境での繰り返し acceptance はない。追加するテストは
`git init` 程度の fixture を作る数秒単位の large_local で、`make check` の所要（数分）に収まり、implement step の
timeout の 25% を超えない。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない（既存の `KAJI_TMP_DIR` / `tmp/kaji/<issue_id>/` 契約の適用） |
| docs/ARCHITECTURE.md | なし | runner の tmp layout は変えない。手動起動の `manual-*` は skill 側の規則で、runner の layout 説明の範囲外 |
| docs/dev/skill-authoring.md | あり | § `KAJI_TMP_DIR` の説明（164-174 行）に、skill 手順で一時ファイルを使うときは `_shared/skill-tmp-resolve.md` を使い `/tmp` 固定パスを書かないこと、契約テストで検査されることを追記する。既存の「明示的な `/tmp` 書き込みは禁止しない」は runner が環境変数以外を強制しないという意味であり、本 repo の skill 手順への規則と両立するよう文言を整理する（`[issue_id]` を含む `incident-review` の worktree は例外として明記） |
| docs/dev/workflow_completion_criteria.md | あり | 207-213 行の人間向け手順が `/tmp/issue-body.md` を使う。skill と同じ衝突を招く手順例なので、`mktemp` で作った一時ファイルを使う例に直す（skill 外のため契約テストの対象外） |
| docs/reference/ | なし | API 仕様・規約の変更なし |
| docs/cli-guides/ | なし | CLI 仕様の変更なし |
| AGENTS.md / CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #548 本文 | https://github.com/apokamo/kaji/issues/548 | 置き換え先を `KAJI_TMP_DIR` か main checkout の `tmp/kaji/<issue_id>/` とする。`incident-review` / `refactor.md` の扱いを設計書で決める。修正前 FAIL / 修正後 PASS の契約テスト |
| skill authoring の `KAJI_TMP_DIR` 契約 | `docs/dev/skill-authoring.md:164-174` | 「agent step を含む全 dispatch 経路」に注入。実体は `tmp/kaji/<issue_id>/<NNNN>/`。「kaji 固有の処理は `$KAJI_TMP_DIR` を明示参照してよい」。Issue 単位 dir は issue-close が削除 |
| runner の tmp dir 作成 | `kaji_harness/runner.py:146-200`（`prepare_attempt_tmp_dir`） | 採番 `^[0-9]{4,6}$`、`tmp/kaji/.gitignore`（内容 `*`）を初回のみ作成、dir を削除しない |
| issue-close Step 5.6 | `.claude/skills/issue-close/SKILL.md:587-720` | `<main checkout>/tmp/kaji/[issue_id]/` を close 後に削除。基点は `git worktree list --porcelain` の `branch refs/heads/[default_branch]`（bare 構成のため `$MAIN_REPO` を使わない）。手動起動は Issue dir ごと削除 |
| issue-close Step 5.6 の検証方式 | `tests/test_issue_close_tmp_cleanup.py:1-10` | skill のシェルブロックを抽出して実 `bash` / 実 `git` fixture で検証する前例。fixture は `outside_project_tmp_path` |
| placeholder 契約 | `tests/test_skill_placeholders.py:17-60` | skill markdown の forbidden placeholder と `$<数字>` 禁止（#524） |
| Bash のシェル状態 | `.claude/skills/_shared/worktree-resolve.md`「注意事項」 | 「Claude Code では Bash の cwd は毎回リセットされる」。解決値は絶対パスで使い回す必要がある |
| POSIX `mktemp` の可搬性 | `.claude/skills/grill-me/SKILL.md:122-124` | 「GNU 固有の `mktemp --tmpdir` は BSD/macOS で失敗するため使わない」。template 指定は両対応 |
| 利用側の同等対策 | apokamo/kamo2#2034（`.claude/skills/_shared/main-tmp-resolve.md`） | `/tmp` を使わない規則と main checkout の tmp 解決手順を共通手順として導入した前例 |
