# [設計] review-poll exec_script の --jq スカラー出力を json.loads せず生文字列で解決する

Issue: #209

## 概要

`review_poll_entry._gh_json()` が `kaji pr view --jq '.headRefOid'` 等のスカラー抽出の生 CLI 出力（クォートなし文字列）を `json.loads()` に渡して `JSONDecodeError` でクラッシュする。スカラー抽出を JSON parse から分離し、生文字列として解決する。

## 背景・目的

### Observed Behavior（OB）

`review-cycle` / `review-close` workflow の `review-poll` step が exec_script の non-zero exit で fail-loud し、ポーリングに到達できない。

`.kaji-artifacts/195/runs/2605281039/review-poll/stderr.log`:

```
  File ".../review_poll_entry.py", line 151, in main
    pr_view = _gh_json(
  File ".../review_poll_entry.py", line 54, in _gh_json
    return json.loads(result.stdout)
json.decoder.JSONDecodeError: Extra data: line 1 column 2 (char 1)
```

本設計フェーズで再現を確認した（worktree 内 Python 3.12）:

```
$ python3 -c "import json; json.loads('4c212ed7f6b886c110d116be714e134e99f79cf0')"
json.decoder.JSONDecodeError: Extra data: line 1 column 2 (char 1)   # ← ログと完全一致
$ python3 -c "import json; json.loads('2026-05-28T01:58:57Z')"
json.decoder.JSONDecodeError: Extra data: line 1 column 5 (char 4)
```

`gh`/`kaji pr view --jq` はスカラー（string）結果を **クォートなし生文字列** で stdout に出す。`json.loads("4c21...")` は先頭 `4` を数値として読んだ後 `c` で `Extra data` を投げる。

### Expected Behavior（EB）

`review_poll_entry.main()` が PR / owner / repo / head_sha / head_committed_at を正しく解決し、`codex_review_poll.main()` に argv を委譲して exit code 0 を返す。設計書 `draft/design/issue-204-feat-skill-frontmatter-exec-script-llm-s.md:289-328`（§5 review_poll_entry 責務境界）の委譲契約どおりに動作すること。EB の一次根拠は §5 の **argv 委譲契約** ブロック（`codex_review_poll.main([...])` への 5 引数委譲）。

### 再現手順（Steps to Reproduce）

1. `provider.type=github`、PR が存在する Issue（例: #195 / PR gh:208）
2. `kaji run .kaji/wf/review-close.yaml 195`（または review-cycle）で review-poll step に到達
3. review-poll step が exit code 1 で fail。stderr に上記 `JSONDecodeError`

`KAJI_PR_ID` 注入時は line 151（headRefOid 補完）で即死、未注入でも line 173（committedDate 取得）で必ず死ぬため、経路に関わらず毎回クラッシュする。

## 根本原因（Root Cause）

### なぜ間違っているか

`_gh_json()`（`review_poll_entry.py:49-54`）は CLI 出力を **常に JSON とみなして** `json.loads(result.stdout)` する単一ヘルパー。しかし 3 つの呼び出しは `--jq` の出力フォーマットが異なる:

| 行 | 呼び出し | `--jq` | jq 出力フォーマット | `json.loads` |
|----|---------|--------|--------------------|-------------|
| 119 | `pr list` | `.[0]` | JSON object（`{...}`） | OK |
| 151 | `pr view` | `.headRefOid` | 生 SHA 文字列（クォートなし） | 破綻 |
| 173 | `pr view` | `.commits[-1].committedDate` | 生日付文字列（クォートなし） | 破綻 |

jq は string scalar を raw（クォートなし）で出力し、object/array は JSON テキストで出力する。`_gh_json` はこの 2 種を区別せず、ヘルパー名自体が「常に JSON」という誤前提を内包する。

### いつから壊れているか

#204（exec_script shim 導入、commit `9cb6f49` / merge `b5b6125`）で `review_poll_entry.py` が新設された時点から。#204 導入以降の全 review-poll 実行で再現。

### 同根の他の壊れ箇所の調査

**調査スコープ**: 本バグは「exec_script / `scripts/` 配下のヘルパーが `--jq` スカラー出力を `json.loads` する」誤用である。したがって同根調査の主対象は **`kaji_harness/scripts/` 配下（exec_script から呼ばれる shim 群）** に限定する。`kaji_harness/cli_main.py` の `--jq`（`_format_jq_results` ほか）は **kaji 側の `--jq` raw 出力を生成する実装側**であり、本バグの「出力を消費する側」ではないため調査対象外（=同根ではなく、むしろ「`--jq` スカラー＝raw 文字列」というワイヤフォーマット契約の一次情報。SF-1 で Primary Sources に追加）。

`rg -n -- '--jq' kaji_harness/scripts/` の結果、`scripts/` 配下で `--jq` を使うのは `review_poll_entry.py` の 3 箇所のみ。加えて `rg -n 'json\.loads' kaji_harness/` で全 `json.loads` 箇所を棚卸しし、`--jq` スカラー出力と組み合わさっているものが他に無いことを確認した:

| 箇所 | 入力ソース | スカラー `--jq`? | 判定 |
|------|-----------|----------------|------|
| `codex_review_poll.py:120` | `gh api --paginate`（JSON array） | なし | 安全 |
| `providers/github.py:113` | `gh ... --json`（JSON、`--jq` なし） | なし | 安全 |
| `sync.py:252` | `gh api`（JSON） | なし | 安全 |
| `sync.py:104,168,307,438` / `providers/local.py:788,808` / `cli.py:208` / `cli_main.py:1142` / `state.py:64` | ファイル / NDJSON 行 | なし | 安全 |

→ scripts / exec_script 対象範囲で同型の誤用は他に存在しない。`cli_main.py` の `--jq` 利用は raw 出力の **生成側**（消費側でない）であり本バグと無関係。本バグは `review_poll_entry.py` に局所化されている。

## インターフェース

bug 修正のため公開 IF（`main()` / `parse_remote_url()` / env 契約 / `codex_review_poll.main` への委譲 argv）は **不変**。

### 内部ヘルパーの変更

スカラー抽出用の内部ヘルパー `_gh_raw()` を新設し、JSON 抽出用 `_gh_json()` と責務を分離する。両者とも module-private（`_` 前缀）なので公開 API 影響なし。

| ヘルパー | 用途 | 戻り値 | json.loads |
|----------|------|--------|-----------|
| `_gh_json(args, cwd)`（既存・据置） | `.[0]` 等の JSON object/array 抽出 | parse 済み Python 値 / None | 経由する |
| `_gh_raw(args, cwd)`（新設） | `.headRefOid` / `.committedDate` 等のスカラー抽出 | `stdout.strip()` の str（空なら `""`） | 経由しない |

両ヘルパーとも `subprocess.run(check=True, ...)` を共有し、`subprocess.CalledProcessError` は catastrophic として raise する（既存の fail-loud 契約を維持）。

## 制約・前提条件

- `codex_review_poll` の polling ロジック / argparse 契約には一切手を入れない（#204 スコープ境界）
- 既存 ABORT 経路（head_sha unavailable / head committed_at unavailable 等、設計書 §5 の ABORT 表）の挙動を維持する。スカラー抽出が空文字を返したときの ABORT 判定は従来どおり `if not head_sha` / `if not head_committed_at` で成立する
- リファクタ混在を避ける。`_gh_json` の JSON 経路（line 119 / 145 の `.[0]` + `.get(...)`）は変更しない

## 方針（修正アプローチ）

最小侵襲。`review_poll_entry.py` のみ変更する。

1. `_gh_raw(args, cwd)` を新設:

   ```python
   def _gh_raw(args: list[str], cwd: str | None = None) -> str:
       """CLI を実行し --jq スカラーの生 stdout を返す（json.loads しない）。"""
       result = subprocess.run(args, check=True, capture_output=True, text=True, cwd=cwd)
       return result.stdout.strip()
   ```

2. スカラー抽出 2 箇所を `_gh_raw` へ差し替え:
   - line 151（headRefOid 補完）: `pr_view = _gh_raw([...])` → `head_sha = pr_view`（`isinstance(..., str)` 分岐は不要に。`_gh_raw` は常に str）
   - line 173（committedDate）: `head_committed_at = _gh_raw([...])`（同上）

3. JSON object 抽出（line 119 の `pr list --jq '.[0]'`）は `_gh_json` のまま据置。`.get("headRefOid")`（line 145）は object 内の値取得なので変更不要。

`head_sha` / `head_committed_at` の後段は空文字チェック（`if not head_sha` / `if not head_committed_at`）で ABORT 判定するため、`_gh_raw` が `""` を返せば従来の ABORT 経路にそのまま乗る。

## テスト戦略

### 変更タイプ

実行時コード変更（`kaji_harness/scripts/review_poll_entry.py`）。

bug 固有ルールに従い、修正前 Red → 修正後 Green の再現テストを必須で追加する。

#### Small テスト（`tests/test_review_poll_entry.py`）

**再現テスト（新規・必須）**: モック位置を `_gh_json` ではなく **`subprocess.run` 境界**に置き、`stdout` に実際の生 SHA / 生日付を流して「`--jq` スカラーは生文字列」契約をピン留めする。

> **MF-1 対応（headRefOid raw scalar 経路の通過保証）**: 現行実装では `pr list --jq .[0]` の結果に `headRefOid` が含まれると line 145 で `head_sha` が非空になり、line 150 の `if not head_sha:` 分岐に入らず `pr view --jq .headRefOid`（修正後 `_gh_raw`）が**呼ばれない**。したがって headRefOid の raw scalar 経路を必ず実行させるため、**成功系の再現テストでは `pr list --jq .[0]` の出力に `headRefOid` を含めない**（`'{"number": 42, "headRefName": "feat/x"}'`）。これにより line 145 の `head_sha` が空 → line 150〜163 の `pr view --jq .headRefOid` が必ず呼ばれ、生 SHA の raw scalar 抽出を検証できる。`committedDate` は別経路（line 173）で常に呼ばれるため同テストで同時に検証する。

- 検証観点 1（再現＝回帰防止の主軸 / headRefOid 経路）: `subprocess.run` を fake し、`git remote get-url` → remote URL、`pr list --jq .[0]` → JSON object テキスト（`'{"number": 42, "headRefName": "feat/x"}'`、**headRefOid を含めない**）を **文字列で** stdout に、`pr view --jq .headRefOid` → 生 SHA `4c212ed7...\n`、`pr view --jq .commits[-1].committedDate` → 生日付 `2026-05-28T01:58:57Z\n` を返す。`codex_review_poll.main` を patch して、委譲 argv が生 SHA / 生日付をそのまま含むこと、rc==0 を確認する。**修正前は line 151（headRefOid 補完経路）で `JSONDecodeError` により FAIL、修正後 PASS**。`pr list` の出力も実 stdout 文字列にすることで、`.[0]` object 抽出が従来どおり JSON parse される（デグレ無し）ことも同時に確認する。
- 検証観点 1b（headRefOid を pr list 経路から取得する成功系・デグレ確認）: `KAJI_PR_ID` を注入する（または `pr list --jq .[0]` に `headRefOid` を含める）ことで `head_sha` が pr list の JSON object 由来で解決され、`pr view --jq .headRefOid` がスキップされる従来分岐も rc==0 で通ることを確認。これにより MF-1 の「headRefOid を空にした経路」と「pr list から取得する経路」の双方を網羅する。
- 検証観点 2（スカラー空→ABORT のデグレ無し）: `pr view --jq .headRefOid` の stdout を空文字にし、`head_sha unavailable` ABORT + rc==0 を確認。committedDate 空でも `head committed_at unavailable` ABORT を確認。

**既存テストの移行（必須・破壊回避）**: 現行の `test_head_sha_empty_returns_abort` / `test_committed_at_empty_returns_abort` / `test_full_flow_delegates_argv_to_codex_review_poll` は `_gh_json` を patch して headRefOid / committedDate に Python 値を返している。修正後これらスカラー抽出は `_gh_raw` 経由になり `_gh_json` mock が hit しなくなる（＝既存テストが実 subprocess を叩いて壊れる）。完了条件「`tests/test_review_poll_entry.py` 含む green」を満たすため、これら 3 テストを **`subprocess.run` 境界モックへ移行**する（上記再現テストと同じ fake 戦略に統一）。`_gh_json` を直接 patch するテストは `pr list`（`.[0]`）の JSON 経路検証用途に限定するか、subprocess fake へ統合する。

#### Medium テスト

不要。根本原因は単一プロセス内の stdout→Python 値変換境界であり、DB / 内部サービス結合を伴わない。`subprocess.run` を fake する Small で継ぎ目を完全に再現できる（`testing-convention.md` の「Small で十分な検証は Medium に昇格しない」に該当）。

#### Large テスト

不要。実 `gh` CLI / GitHub API 疎通は本バグの対象外（バグは生文字列の Python 側パース誤用であり、CLI 出力フォーマット自体は正しい）。実 API を叩く Large は外部依存で不安定化し、生文字列契約のピン留めには寄与しない（`testing-convention.md` の Large 追加 4 条件を満たさない）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ不変 |
| docs/dev/ | なし | ワークフロー / 開発手順不変 |
| docs/reference/ | なし | 公開 API / 規約不変（内部ヘルパーのみ変更） |
| docs/cli-guides/ | なし | CLI 仕様不変 |
| CLAUDE.md | なし | 規約不変 |
| `draft/design/issue-204-...md` | なし | #204 設計書 §5 の委譲契約は正しく、実装が契約を逸脱していただけ。設計書の修正は不要（実装を契約に合わせる） |

> 補足: #204 設計書 §5 の ABORT 表は値レベル異常のみ規定し「`--jq` スカラー＝生文字列」ワイヤフォーマット契約を未定義だった（Issue「テストの死角」§2）。ただし本 Issue は #204 設計書を改訂対象とせず、本設計書で当該契約を明文化する（上記テスト戦略 検証観点 1）。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| クラッシュログ | `.kaji-artifacts/195/runs/2605281039/review-poll/stderr.log` | `json.loads(result.stdout)` → `JSONDecodeError: Extra data: line 1 column 2 (char 1)`。根本原因の一次証拠 |
| 実装 | `kaji_harness/scripts/review_poll_entry.py:49-54`（`_gh_json`）, `:119,151,173` | 単一ヘルパーが object 抽出とスカラー抽出を混同。3 呼び出しの `--jq` 差異 |
| #204 設計書 §5 | `draft/design/issue-204-feat-skill-frontmatter-exec-script-llm-s.md:289-328` | EB の委譲契約: `codex_review_poll.main(["--pr",...,"--head-sha",head_sha,"--head-committed-at",head_committed_at])`。ABORT 表（head_sha / committed_at unavailable） |
| 再現確認（本設計時実行） | worktree `python3 -c "import json; json.loads('4c21...')"` | `JSONDecodeError: Extra data: line 1 column 2 (char 1)` をログと完全一致で再現。生 SHA / 生日付がともに json.loads 不可であることを実証 |
| 同根調査 | `rg -n 'json.loads' kaji_harness/` / `rg -n -- '--jq' kaji_harness/scripts/` | `scripts/` 配下で `--jq` スカラー × json.loads の組み合わせは review_poll_entry.py のみ。他は JSON 入力で安全。`cli_main.py` の `--jq` は raw 出力の生成側で消費側でない |
| kaji `--jq` raw 出力仕様（実装） | `kaji_harness/cli_main.py:1171`（`_format_jq_results`）, `:1119`（`gh --jq` 互換 raw 出力の docstring） | kaji の `--jq` は string scalar を quote なし raw 文字列、object/array を JSON テキストで出力する。本バグの「`--jq` スカラー＝生文字列」前提のワイヤフォーマット一次根拠 |
| kaji `--jq` raw 出力仕様（テスト） | `tests/test_dispatcher.py:445`（`test_view_json_with_jq_emits_raw_string`） | `pr view --json ... --jq <scalar>` が raw string（クォートなし）を emit することをピン留めする既存テスト。raw scalar 契約の回帰防止 |
