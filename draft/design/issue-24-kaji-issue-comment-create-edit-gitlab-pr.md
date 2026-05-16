# [設計] kaji issue comment/create/edit が GitLab provider で --body-file を受理しない不具合の修正

Issue: gl:24

## 概要

GitLab provider 配下で `kaji issue comment` / `kaji issue create` / `kaji issue edit` に
`--body-file PATH`（`-` で stdin）を渡すと、未知 flag `--body-file` が `glab` に到達して
reject される。`_handle_issue_gitlab` に body-file 展開の前処理を追加し、Local / GitHub
provider と対称な「skill 互換 CLI 契約」を回復する。

## 背景・目的

### Observed Behavior (OB)

`/issue-review-design` workflow 実行中 (2026-05-15 00:07:42 JST) の console.log（Issue gl:24 本文より一次引用）:

```
[2026-05-15T00:07:42] [review-design] [exec] $ /bin/bash -lc "kaji issue comment 23 --commit --body-file - <<'EOF' # 設計レビュー結果  …
   ERROR
  Unknown flag: --body-file.
  Try --help for usage.
[exit=1]
```

その後 agent が即興で `--message` に書き換えて投稿成功している。すなわち skill 契約上の
`--body-file -` heredoc パターンが GitLab provider では一切通らず、AI agent の即興 recover に
依存してかろうじて部分的に動いている状態。

### Expected Behavior (EB)

`provider.type = "gitlab"` 配下でも `kaji issue {comment,create,edit}` は GitHub mode と同じ
skill 互換 contract で動作しなければならない。これは `docs/cli-guides/gitlab-mode.md` § 2 が
明文化している原則（「skill 側に GitHub/GitLab 分岐を持ち込まない」）であり、`--body-file`
（`-` で stdin）も GitHub / Local と同様に受理されることが期待挙動。

EB の一次情報的裏付け（同一 codebase 内の対照実装）:

- **Local mode** — `_local_issue_comment` (`cli_main.py:1278-1311`) は argparse で `--body` /
  `--body-file` を宣言し `_read_body_arg` で解決。`--body-file` 動作する。
- **GitHub mode** — `_forward_to_gh "issue"` 経由。`gh issue comment/create/edit` が
  `--body-file` をネイティブサポート。
- **GitLab `kaji pr` 経路** — `_gitlab_pr_comment` (`cli_main.py:1759-1777`) /
  `_gitlab_pr_create` (`1623-1655`) / `_gitlab_pr_review` (`1780-`) は **既に** argparse +
  `_read_body_arg` で `--body-file`（stdin 含む）を解決済み。

→ GitLab provider の `kaji issue` 経路だけが、同じ provider の `kaji pr` 経路とも、他 provider
とも非対称に `--body-file` 未対応。これは provider 間挙動非対称によるバグ。

## 再現手順

1. **前提**: `.kaji/config.toml` に `[provider]` `type = "gitlab"` を設定したリポジトリ。
2. **実行**:
   ```bash
   echo "hello from bug repro" | kaji issue comment gl:<id> --body-file -
   ```
3. **観測される出力 (OB)**:
   ```
   Unknown flag: --body-file.
   ```
   exit code 1。`create --body-file FILE` / `edit <id> --body-file -` も同構造で失敗する。

再現は workflow 上の偶発でなく、`_handle_issue_gitlab` の引数素通し構造から決定論的に発生する。

## 根本原因（Root Cause）

### 問題のロジックがなぜ間違っているか

`_handle_issue_gitlab` (`cli_main.py:1438-1480`) は薄い passthrough dispatcher で、sub 名 /
flag 名の写像を `_GITLAB_ISSUE_SUB_MAP` (`cli_main.py:1424-1433`) に集約している:

```python
_GITLAB_ISSUE_SUB_MAP = {
    "create":  ("create", {"--body": "--description"}),
    "edit":    ("update", {"--body": "--description"}),
    "comment": ("note",   {"--body": "--message"}),
    ...
}
```

flag 変換は `_rewrite_flags` (`cli_main.py:1394-1419`) が担うが、その仕様は
「**flag_map に無い flag はそのまま流す**」。`--body-file` は flag_map に登録されていないため
rewrite も展開もされず、`_forward_to_glab` 経由でそのまま `glab` に到達する。`glab issue
note/create/update` の parser に `--body-file` flag は存在しない（§ 参照情報の glab help 引用を
参照）ため `Unknown flag` で reject される。

`--body` だけが map にあり `--body-file` の rewrite / 内容展開が無い、というのが直接の欠落。

### いつから壊れているか

GitLab issue dispatcher 実装（Issue local-pc5090-6 / `_handle_issue_gitlab` 導入）の時点から。
`_GITLAB_ISSUE_SUB_MAP` は当初から `--body` のみを map しており、`--body-file` 対応は一度も
存在しない。`kaji pr` 経路は後発で argparse handler 群（`_gitlab_pr_*`）として実装され
`--body-file` を取り込んだため、issue 経路だけが取り残された。

### 同根で他にも壊れている箇所

- `kaji issue create` / `edit` / `comment` の 3 sub が同一構造で全滅（いずれも `--body-file`
  を取りうる sub）。
- `view` / `list` / `close` は body 引数を取らないため対象外。
- `kaji pr` 経路（`_gitlab_pr_*`）は argparse 化済みで本バグの影響を受けない（調査済み）。

→ 修正対象は `kaji issue` の create/edit/comment 3 sub に限定される。

## インターフェース

bug 修正のため公開 IF（CLI の外形）は維持する。変更は「GitLab provider 配下で受理される
flag 集合」を GitHub / Local と揃えることのみ。

### 入力

```
kaji issue comment <id> --body-file <PATH>
kaji issue comment <id> --body-file -        # stdin（heredoc skill パターン）
kaji issue create --title T --body-file <PATH>
kaji issue edit <id> --body-file -
```

`--body` 既存指定形式は不変。

### 出力

`--body-file` の内容を読み取り、`glab` には sub に応じて以下へ変換して転送:

| kaji sub | glab sub | body flag |
|----------|----------|-----------|
| `comment` | `note` | `--message <content>` |
| `create` | `create` | `--description <content>` |
| `edit` | `update` | `--description <content>` |

### 後方互換性

- `--body` 指定時の挙動は完全に不変（前処理は `--body-file` 不在時に `rest` を素通し）。
- `--body-file` は GitLab provider では今まで常に exit 1 で失敗していたため、成功側への変更で
  退行する既存挙動は存在しない。
- Local / GitHub provider の経路には触れない。

## 制約・前提条件

- 修正は `kaji_harness/cli_main.py` 単一ファイル内に閉じる。skill 側 (`.claude/skills/`) の
  `--body-file -` heredoc パターンは一切書き換えない（Issue 修正方針: CLI 側補正のみ）。
- `glab issue create` / `glab issue update` の `--description` は値 `-` で editor を開く仕様
  （§ 参照情報の glab help 引用）。`--body-file` 展開後の content は通常マルチライン文字列で
  あり衝突しないが、コメント本文が単一文字 `-` という degenerate 入力のみ editor 起動と衝突
  しうる。実運用外の入力であり特別扱いはしない（`note` 側の `--message` には `-` の特殊挙動は
  無い）。
- `--commit` の silent strip は body-file 展開より前（`cli_main.py:1451`）で実行される。
  展開後の content は単一 argv 要素であり、`--commit` フィルタに再度かからない（順序保証）。

## 方針

最小侵襲。`_handle_issue_gitlab` の `comment` / `create` / `edit` 経路で、既存
`_rewrite_flags` 呼出の **直前** に `rest` を前処理し `--body-file` を `--body <content>` に
展開する。展開後の `--body` は既存 `_rewrite_flags` が `--description` / `--message` へ変換する
ため、`_GITLAB_ISSUE_SUB_MAP` の flag_map には手を入れない。

### 擬似コード

新規 helper（`cli_main.py` 内、`_rewrite_flags` 近傍に配置）:

```python
def _expand_body_file_in_rest(rest: list[str]) -> list[str]:
    """``--body-file PATH`` (``-`` で stdin) を ``--body <content>`` に展開する。

    ``--body`` 単独 / body flag 不在は ``rest`` を素通し。``--body`` と
    ``--body-file`` の同時指定は ``_read_body_arg`` が ValueError を送出する。
    """
    body_val      = <rest から --body / --body=X の値を抽出（無ければ None）>
    body_file_val = <rest から --body-file / --body-file=X の値を抽出（無ければ None）>
    if body_file_val is None:
        return rest                              # --body 単独 or 無し → 不変
    content = _read_body_arg(body_val, body_file_val)  # 両指定 → ValueError
    remaining = <rest から --body-file トークン（と値）を除去したリスト>
    return [*remaining, "--body", content]
```

`_handle_issue_gitlab` 内、`glab_sub, flag_map` 取得後・`view`/`list` 正規化分岐
(`cli_main.py:1468`) の後、`_rewrite_flags` 呼出 (`cli_main.py:1472`) の前に挿入:

```python
if sub in {"create", "edit", "comment"}:
    try:
        rest = _expand_body_file_in_rest(rest)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
rewritten = _rewrite_flags(rest, flag_map)   # --body → --description / --message
```

### 設計判断

- **`_read_body_arg` の再利用**: stdin (`-`) / ファイル読込と「`--body` と `--body-file` の
  mutual exclusion」を Local / `kaji pr` 経路と同一の単一関数に集約する。重複ロジックを作らない。
- **flag_map を変更しない**: `--body-file` を `--body` に展開してから `_rewrite_flags` に渡す
  ことで、既存の `--body → --description/--message` 変換をそのまま再利用する。`_GITLAB_ISSUE_SUB_MAP`
  には手を入れず、変更を 1 helper + 数行の挿入に閉じ込める。
- **mutual exclusion の外形**: `_gitlab_pr_comment` (`cli_main.py:1769-1773`) と同じく ValueError
  を catch して `EXIT_INVALID_INPUT` + stderr メッセージで返す。Issue 受入条件「`--body` と
  `--body-file` の同時指定で従来通り ValueError」の解釈 — 内部の判定機構は `_read_body_arg` が
  送出する `ValueError("--body and --body-file are mutually exclusive")` であり、CLI 外形は
  非 0 exit + エラーメッセージ。`kaji pr` 経路の確立挙動と一致させる。
- **採用しなかった案**: `kaji issue` 経路全体を `kaji pr` 経路同様の argparse handler 群
  （`_gitlab_issue_create` 等）へ作り替え、`kaji pr` と完全対称にする案。構造的には美しいが、
  bug 修正の scope を超える広域リファクタであり、bug.md の「リファクタ混在は避ける」原則に反する。
  passthrough → argparse handler 化は別 Issue として切り出すのが妥当。

### エッジケース

- 展開後 `[*remaining, "--body", content]` を末尾追加しても、id positional の解決
  (`_normalize_gitlab_issue_id_in_args`) は「最初の non-flag を id とみなす」ため影響なし
  （id は `remaining` 内に既存、`content` は `--body` flag の値位置）。
- content がマルチラインでも単一 argv 要素として保持され、`glab` の `--message` /
  `--description` は単一引数として受理する。

## テスト戦略

> **CRITICAL**: 変更タイプに応じて妥当な検証方針を定義すること。

### 変更タイプ

実行時コード変更（bug fix）。bug 固有ルールにより、**修正前に Red になる再現テスト**を
必須で 1 本以上定義する。

### 実行時コード変更の場合

#### Small テスト

新規 helper `_expand_body_file_in_rest` の単体検証（外部依存なし。stdin は mock）:

- body flag 不在の `rest` → 同一リストを素通し（不変）
- `--body` 単独 → 素通し（既存挙動不変の保証）
- `--body` と `--body-file` 同時指定 → `ValueError` 送出
- `--body-file -` → mock した `sys.stdin` を読み、`[..., "--body", <stdin内容>]` を返す
- `--body-file=PATH` / `--body-file PATH` の両表記から値を正しく抽出し `--body-file` トークンを
  除去する

#### Medium テスト

`tests/test_phase4_dispatcher_gitlab.py`（既存 GitLab dispatch テスト、`@pytest.mark.medium`）に
dispatch 統合テストを追加。`subprocess.run` を mock し `_handle_issue` 経由で `glab` へ渡る
コマンド列を検証する:

- **再現テスト（修正前 Red / 修正後 Green）**: `_handle_issue(["comment","42","--body-file","-"])`
  + stdin mock → `glab` cmd に `note` と `--message <stdin内容>` が含まれ、`--body-file` が
  **含まれない**こと。修正前は `--body-file` が flag_map 外で素通しされ cmd に残るため Red。
- `create --body-file <実temp file>` → cmd に `create` と `--description <ファイル内容>`、
  `--body-file` 不在
- `edit --body-file -` + stdin mock → cmd に `update` と `--description <内容>`
- マルチライン content（`-` stdin）が単一 argv 要素として `--message` 値に保持されること
- `--body` と `--body-file` 同時指定 → `rc == 2`、stderr に "mutually exclusive"、
  `subprocess.run` 未呼出
- **回帰**: 既存の `--body` 単独テスト（`test_comment_maps_to_note_and_message` /
  `test_create_forwards_with_repo_and_host_env` / `test_edit_maps_to_update_and_description`）
  が不変で pass すること（passthrough 不変経路の保証）

`subprocess.run` の namespace patch スコープ（`docs/dev/testing-convention.md`）について:
本テストは `_handle_issue` → `_forward_to_glab` の forward 引数構築を検証する目的で、git /
worktree 解決を介在しない。既存 `test_phase4_dispatcher_gitlab.py` が `kaji_harness.cli_main.
subprocess.run` を namespace patch する確立パターンを持つため、本テストもその同一ファイルの
既存パターンを踏襲する（同 doc が禁ずる「dispatch/provider 結合での暗黙の MagicMock truthy
依存」は本テストに該当しない — 戻り値は `MagicMock(returncode=0)` を明示し分岐に使わない）。

#### Large テスト

`tests/test_large_gitlab/test_issue_roundtrip.py`（`@pytest.mark.large_gitlab`、`make
test-large-gitlab` で実行、`make check` デフォルトからは除外）の既存 create / edit / comment
ステップを `--body-file`（stdin / ファイル）駆動に拡張する。実 `glab` が rewrite 後コマンドを
受理し、Issue 本文 / コメントに内容が反映されることを E2E 確認する。

省略しない理由 / Large の位置づけ: バグ真因は kaji 側の引数構築のみで、Medium の mock テストが
真因（`--body-file` の素通し）を厳密に再現・回帰保護する。Large は「実 `glab` が rewrite 後の
`--message` / `--description` を実際に受理する」確証を加える追加層であり、既存 roundtrip
テストへの低コストな引数差し替えで賄える範囲。新規 large_gitlab ファイルは作らない。

### Local / GitHub provider の回帰

変更は `_handle_issue_gitlab`（GitLab 専用分岐）と新規 helper に閉じる。`_local_issue_comment`
/ `_local_issue_create` / `_forward_to_gh` 経路は無変更のため、既存テスト群でそのまま回帰検出
される。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | なし | ワークフロー・開発手順の変更なし |
| docs/reference/ | なし | API 仕様・規約の変更なし |
| docs/cli-guides/gitlab-mode.md | あり（任意） | § 2 が `kaji issue` GitLab 挙動を扱う。`--commit` silent strip 節と並べて「`--body-file`（`-` stdin 含む）対応」を 1 文明記しうる。既存「GitHub mode と同じ contract」記述で意味的には充足するため、implement 時に要否を判断する |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `glab issue note --help`（ローカル再現可） | `glab issue note --help` の出力 | FLAGS は `-h --help` / `-m --message`（"Message text."）/ `-R --repo` のみ。`--body` / `--body-file` は **存在しない**。→ comment の正しい転送先は `--message` |
| `glab issue create --help`（ローカル再現可） | `glab issue create --help` の出力 | body 系 flag は `-d --description`（"Issue description. Set to \"-\" to open an editor."）。`--body-file` は存在しない。→ create の転送先は `--description`、値 `-` は editor 起動の特殊値 |
| `glab issue update --help`（ローカル再現可） | `glab issue update --help` の出力 | body 系 flag は `-d --description`（"... Set to \"-\" to open an editor."）。→ edit→update の転送先は `--description` |
| kaji 実装（OB の発生箇所） | `kaji_harness/cli_main.py:1424-1480` | `_GITLAB_ISSUE_SUB_MAP` に `--body` のみ map、`_handle_issue_gitlab` が `_rewrite_flags`→`_forward_to_glab` で rest 素通し。`--body-file` が glab に到達する直接原因 |
| kaji 実装（再利用する一次接点） | `kaji_harness/cli_main.py:869-882` | `_read_body_arg(body, body_file)`: 両指定で `ValueError("--body and --body-file are mutually exclusive")`、`body_file == "-"` で `sys.stdin.read()`、それ以外は `Path(body_file).read_text()` |
| kaji 実装（対照: 既に body-file 対応済の経路） | `kaji_harness/cli_main.py:1759-1777`（`_gitlab_pr_comment`） | `kaji pr` GitLab 経路は argparse で `--body` / `--body-file` を宣言し `_read_body_arg` で解決、ValueError を catch して `EXIT_INVALID_INPUT`。issue 経路が揃えるべき参考実装 |
| Issue gl:24 本文（OB 一次ログ） | gl:24 / `/issue-review-design` 実行 console.log (2026-05-15 00:07:42 JST) | `Unknown flag: --body-file.` exit=1 の実観測 |

> glab help 出力は実行環境（`glab` v in PATH）で `glab issue {note,create,update} --help` により
> レビュワーが再現確認できる公開 CLI のヘルプであり、アクセス制限はない。
