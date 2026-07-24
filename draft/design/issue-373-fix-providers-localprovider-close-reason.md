# [設計] LocalProvider の close_reason を Issue.state_reason へ反映する

Issue: #373

## 概要

`LocalProvider` の Issue 読み出し境界（`kaji_harness/providers/_local_store.py` の
`read_issue()`）が frontmatter の `close_reason` を読まないため、`Issue.state_reason` が
常に空文字列になり `providers/models.py` の docstring 契約と食い違う。読み出し境界 1 箇所で
`close_reason` を GitHub provider と同じ `.lower()` 正規化のみ行って `state_reason` に載せ、
契約と実装を一致させる。

## 背景・目的

### Observed Behavior（OB）

`fix/373` HEAD（`main` = `3eff22f` と同一内容）で以下を実行し、Issue 本文の OB を再現した。

```python
# 実行環境: /home/aki/dev/kaji/kaji-fix-373, source .venv/bin/activate
import tempfile, pathlib
from kaji_harness.providers.local import LocalProvider
from kaji_harness.series.models import evaluate_member_gate

with tempfile.TemporaryDirectory() as td:
    repo = pathlib.Path(td) / "repo"
    (repo / ".kaji").mkdir(parents=True)
    p = LocalProvider(repo_root=repo, machine_id="pc1")
    p.create_issue(title="repro", body="b", slug="repro")
    closed = p.close_issue("local-pc1-1", reason="completed")
    print("close_issue() ret :", repr(closed.state), repr(closed.state_reason))
    v = p.view_issue("local-pc1-1")
    print("view_issue()      :", repr(v.state), repr(v.state_reason))
    l = p.list_issues(state="all")[0]
    print("list_issues()     :", repr(l.state), repr(l.state_reason))
    print("gate              :", evaluate_member_gate(0, v.state, v.state_reason))
    d = p._resolve_issue_dir("local-pc1-1")
    print("frontmatter       :", [ln for ln in (d / "issue.md").read_text().splitlines()
                                  if "close_reason" in ln])
```

実出力:

```text
close_issue() ret : 'closed' ''
view_issue()      : 'closed' ''
list_issues()     : 'closed' ''
gate              : GateResult(success=False, gate='mismatch:closed/')
frontmatter       : ['close_reason: completed']
```

frontmatter は `close_reason: completed` を保持しているが、`close_issue()` の戻り値・
`view_issue()`・`list_issues()` のいずれの読み出し経路でも `state_reason` は空文字列である。
理由を引数で渡した直後の `close_issue()` 戻り値ですら空になる。

### Expected Behavior（EB）

`kaji_harness/providers/models.py:60-61` の `Issue.state_reason` docstring は次を規定する。

```text
state_reason: Issue state の理由。GitHub の値を小文字に正規化し、
    provider が理由を持たない場合は空文字列。
```

LocalProvider は `close_reason` を frontmatter に永続化しており（`local.py:218`）、
「provider が理由を持たない場合」に該当しない。したがって上記 3 経路はいずれも
`state_reason='completed'` を返し、`evaluate_member_gate(0, 'closed', 'completed')` は
`GateResult(success=True, gate='closed_completed')` になるべきである。

是正の方向（実装側を直す / docstring を実態に合わせる）は 2026-07-24 の grill-me interview で
人間決定済み（Issue `## 決定事項` 決定 1）。本設計はこの決定の実装方針を具体化する。

## 再現手順（Steps to Reproduce）

CLI 経路（Issue 本文記載、frontmatter の永続化まで確認）:

1. 前提: 空の git repo で `main` branch に 1 commit 以上ある状態
2. `kaji local init --machine-id pc1 --non-interactive` 後、`.kaji/config.toml` に
   `[provider] type = "local"` を設定
3. `kaji issue create --title "repro state reason" --body "b" --label "type:bug"` → `local-pc1-1`
4. `kaji issue close local-pc1-1 --reason completed`
5. `head -12 .kaji/issues/local-pc1-1-repro-state-reason/issue.md` → `close_reason: completed`
6. provider 経由で読み戻すと `state_reason=''`

provider API 直接経路（上記 OB のスクリプト、`tmp_path` のみで完結。恒久回帰テストはこの経路を採る）:
`create_issue` → `close_issue(reason=...)` → `close_issue()` 戻り値 / `view_issue()` /
`list_issues()` の `state_reason` を観測する。

## 根本原因（Root Cause）

### なぜ壊れているか

`_local_store.py:137-145` の `read_issue()` は `parse_frontmatter()` で得た `meta` から
`id` / `title` / `state` / `labels` / `slug` を `Issue` に載せるが、`meta["close_reason"]` を
参照していない。`Issue.state_reason` は `field default = ""`（`models.py:71`）のため、
**キーワードを渡さなければ黙って空文字列になる**。frozen dataclass の default 付き field
追加は既存 callsite を壊さない代わりに、追加時に全 provider の構築箇所を更新したかが
型検査で検出されない。これが「永続化はされているのに公開されない」歪みを生んだ機構である。

`validate_issue_meta()`（`_local_common.py:78-`）も `id` / `state` / `labels` / `slug` のみを
検証対象とし、`close_reason` は検証も参照もしない。すなわち書き込み側（`local.py:218`）と
読み出し側（`_local_store.py:137`）を接続する経路が一度も書かれていない。

### いつから壊れているか

| commit | 日付 | 内容 |
|--------|------|------|
| `390dc7a` | 2026-05-06 | `close_issue()` が `close_reason` を frontmatter に永続化（書き込み側のみ） |
| `49c9a28` | 2026-07-14 | `#313` で `Issue.state_reason` field と `GitHubProvider` の `stateReason` 読み出しを追加（GitHub 側のみ） |

`49c9a28` 以前は `state_reason` field 自体が存在せず契約も無かったため、**契約と実装の
食い違いは `49c9a28` 以降**である。`draft/design/issue-313-*.md:237` の
「`LocalProvider` は `""` のまま（初期実装は GitHub provider のみが対象）」は series 初期
実装スコープの意図的な繰り延べであり、恒久方針ではない（決定 1 の整理）。

### 同根の他の壊れ箇所の調査

`state_reason` を落としうる `Issue` 構築箇所を全数列挙した
（`grep -rn "Issue(" --include=*.py kaji_harness/`）。

| 構築箇所 | `state_reason` | 判定 |
|----------|----------------|------|
| `github.py:172` (`_parse_pr_payload` 相当) | 未設定 | PR 表現。`state_reason` は Issue 固有概念であり対象外 |
| `github.py:232` (`_parse_issue_payload`) | `stateReason` を `.lower()` | 正常。本修正の対称基準 |
| `_local_store.py:137` (`read_issue`) | **未設定** | **本 Issue の修正対象（唯一の修正点）** |
| `_local_cache.py:113` (`_listed_issue_from_payload`) | 未設定 | 後述のとおりスコープ外 |
| `_local_cache.py:127/130` (`cached_github_issue_from_payload`) | 未設定 | 同上 |

**local Issue 側の読み出し経路は `read_issue()` に一点集約されている**ことを確認した。
`view_issue()`（`local.py:159`）・`close_issue()`（`local.py:220`）・`edit_issue()`
（`local.py:193`）・`list_issues()`（`local.py:235`）・`list_issue_comments_all()`
（`local.py:163`）はすべて `self._store.read_issue(...)` を経由する。したがって
`read_issue()` 1 箇所の修正で OB の 3 経路すべてが同時に解消し、他に修正を要する local 経路は
存在しない。

**`_local_cache.py` の GitHub cache reader はスコープ外**とする。理由:

1. 対象データが異なる。cache は `kaji sync from-github` が GitHub REST の Issue payload を
   そのまま `.kaji/cache/gh-<n>.json` の `issue` キーに包んだもの（`sync.py:255-271`）で、
   local の `close_reason` 契約とは無関係
2. 単純な pass-through が正しいと言えない。cache reader は
   `state = "open" if not is_stale and github_state == "open" else "closed"`
   （`_local_cache.py:86`）で **stale entry を強制的に `closed` へ倒す** 合成 state を返す。
   ここに GitHub 由来の `state_reason` を素通しすると、GitHub 上は open のまま stale 化した
   entry が `closed` + `reopened` 等の非現実的な組み合わせを返す。「stale entry の
   `state_reason` を何にすべきか」は本 Issue の人間決定（決定 1・決定 2）が扱っていない
   別の判断であり、設計フェーズで AI が自己解釈で埋めない
3. 消費者が存在しない。`state_reason` の唯一の消費者は `series/runner.py:152,165,234` の
   `evaluate_member_gate()` で、series は `commands/series.py:65` により
   `provider.type='github'` 限定。cache 経路の Issue（id は `gh:<n>` / read-only）はこの
   経路に到達しない

この観察は本 Issue の完了条件「同根の他の壊れ箇所の調査結果」への回答として本設計書に記録する
（現時点でユーザー影響も消費者も無いため独立 Issue は起票していない。追跡が必要と判断された
場合は review-design での指摘に従い起票する）。

## インターフェース

bug 修正のため公開 IF は不変。`Issue` dataclass の field 構成、`read_issue()` のシグネチャ、
CLI の引数・出力形式のいずれも変更しない。変わるのは既存 field `Issue.state_reason` に
入る **値** のみである。

### 入力

`.kaji/issues/<id>-<slug>/issue.md` の YAML frontmatter。

| キー | 型 | 必須 | 備考 |
|------|-----|:----:|------|
| `close_reason` | YAML scalar（通常 str） | 任意 | `close_issue()` が close 時に書き込む。`--reason` 未指定 / 空文字なら `"completed"`（`local.py:218`）。値域検証は無く自由文字列を許す |

### 出力

`Issue.state_reason: str`。

| frontmatter の `close_reason` | 返る `state_reason` |
|------------------------------|---------------------|
| キー不在（open Issue、close 前） | `""`（従来どおり） |
| `completed` | `"completed"` |
| `COMPLETED` | `"completed"`（`.lower()`） |
| `not-planned` | `"not-planned"`（pass-through。GitHub の `not_planned` へは寄せない） |
| `merged into main` | `"merged into main"`（pass-through） |
| `null` / 空文字 | `""` |

### 使用例

```python
from kaji_harness.config import KajiConfig
from kaji_harness.providers import get_provider
from kaji_harness.series.models import evaluate_member_gate

issue = get_provider(KajiConfig.discover(Path("."))).view_issue("local-pc1-1")
print(issue.state, issue.state_reason)
# 修正前: closed ''
# 修正後: closed completed

evaluate_member_gate(0, issue.state, issue.state_reason)
# 修正前: GateResult(success=False, gate='mismatch:closed/')
# 修正後: GateResult(success=True, gate='closed_completed')
```

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/providers/_local_store.py` | `read_issue()` の `Issue(...)` に `state_reason` を 1 行追加 |
| `kaji_harness/providers/models.py` | `Issue.state_reason` docstring に local の値域を追記（既存契約文は維持） |
| `tests/test_providers_local.py` | Medium 再現テスト / 値域テストを追加 |

`local.py` の `close_issue()` 書き込み側、`_local_common.py` の `validate_issue_meta()`、
`series/` 配下、CLI 層はいずれも変更しない。

## 制約・前提条件

- **書き込み側の契約を変えない**: `local.py:218` は `reason` を検証せず任意文字列を永続化する。
  既存テスト `tests/test_providers_local.py:245-256`（`"merged into main"`）/ `:283-289`
  （`"not-planned"`）がこの自由文字列契約を固定しており、これを破らない（決定 2）
- **既存永続データを migration しない**: 既に `.kaji/issues/**/issue.md` に書かれている
  `close_reason` の値を書き換えるバッチ処理は行わない（決定 2）
- **`Issue` は frozen dataclass**（`models.py:46`）。field 追加・順序変更は行わず、既存
  field への値供給のみ行う
- **`validate_issue_meta()` に `close_reason` の検証を追加しない**: 検証を足すと、
  非 str 値を手書きした既存 frontmatter が読み出し時に `LocalProviderError` で
  ハードフェイルし、「既存永続データを維持する」制約に反する
- **後方互換**: `close_reason` を持たない既存 Issue（open Issue、`390dc7a` より前に close
  された Issue）は `state_reason=""` のままで、現行と同一の挙動を保つ

## 方針

### 修正の本体

`_local_store.py` の `read_issue()` に 1 行追加する。

```python
        return Issue(
            id=str(meta["id"]),
            title=str(meta.get("title", "") or ""),
            body=body,
            state=str(meta.get("state", "open")),
            labels=labels_from_meta(meta.get("labels")),
            comments=self.comments.read_comments(issue_dir),
            slug=str(slug_value or ""),
            state_reason=str(meta.get("close_reason", "") or "").lower(),
        )
```

### この式を選ぶ理由

1. **`.lower()` のみ / 値域変換なし**: 決定 2 の「小文字化のみの pass-through」をそのまま
   表現する。`github.py:240` の
   `state_reason=str(payload.get("stateReason", "") or "").lower()` と **同一 idiom** であり、
   provider 間で読み出し境界の正規化方針が揃う
2. **`str(... or "")` による防御的 coercion**: frontmatter は手編集可能で、YAML は
   `close_reason: 123` を int、`close_reason:` を `None` として読む。`or ""` が `None` /
   空文字 / falsy 値を `""` へ倒し、`str()` が非 str を文字列化する。同関数内の `title` /
   `slug` と同じ書き方であり、新しい規約を持ち込まない
3. **`state` による条件分岐を入れない**: 「`state == "closed"` のときだけ読む」条件は
   決定 2 の pass-through を超える追加ルールになる。GitHub 側も reopen 後の open Issue に
   `reopened` を返す（下記 Primary Sources）ため、open Issue の `state_reason` が非空である
   こと自体は契約違反ではない。手編集で `state: open` + `close_reason: completed` になった
   退化データでも `evaluate_member_gate()` は `state == "closed"` を併せて要求するため
   `mismatch:open/completed` となり安全側に倒れる

### 表記ゆれの扱い

local の `not-planned`（ハイフン、既存テストが固定）と GitHub の `not_planned`（アンダー
スコア）は一致しないが、`evaluate_member_gate()`（`series/models.py:86-98`）は `completed`
のみを成功とする allowlist であり、不一致は必ず `success=False` 側に倒れる。alias 正規化は
gate 判定に影響しない値のための解釈ルール追加になるため導入しない（決定 2）。

### docstring の更新

`models.py:60-61` を次のとおり更新する。既存契約文（「GitHub の値を小文字に正規化し、
provider が理由を持たない場合は空文字列」）は削除・改変せず、local の値域を追記する。

```python
        state_reason: Issue state の理由。GitHub の値を小文字に正規化し、
            provider が理由を持たない場合は空文字列。local では frontmatter
            ``close_reason`` を同じく小文字化して載せるため、GitHub の値域
            (``completed`` / ``not_planned`` / ``duplicate`` / ``reopened``)
            外の自由文字列（``"merged into main"`` 等）もありうる。
```

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| 契約と実装の食い違いの是正方向 | LocalProvider 側で `close_reason` を `state_reason` に反映し、`Issue.state_reason` docstring の既存契約文は維持する | Issue 本文 `## 決定事項` § 人間決定 第 1 項（2026-07-24 grill-me interview）／同 Issue の grill-me provenance コメント | 修正点を `_local_store.py:137` の `read_issue()` 内 `Issue(...)` 1 箇所に特定。`view_issue` / `close_issue` / `list_issues` / `edit_issue` が同関数を経由することを実測で確認し、他経路の変更を不要と確定 |
| 反映時の `close_reason` 値域の扱い | 読み出し境界で `.lower()` のみの pass-through。書き込み側の自由文字列契約と既存永続データを維持し migration しない | Issue 本文 `## 決定事項` § 人間決定 第 2 項（2026-07-24 grill-me interview） | 具体式を `str(meta.get("close_reason", "") or "").lower()` に固定し、`github.py:240` と同一 idiom で揃えた。alias 正規化・書き込み時検証・`validate_issue_meta()` への検証追加をいずれも行わない根拠を制約節に明文化 |
| `close_reason` を持たないデータの扱い | `state_reason` は `""` のまま（現行と同一） | Issue 本文 `## 決定事項` § 残す AI 仮定 第 1 項。docstring 契約「provider が理由を持たない場合は空文字列」に合致。検査先: issue-review-design / issue-review-code | `or ""` により YAML の `None` / 空文字 / falsy 値も `""` へ倒す coercion として実装に落とし、Medium テストで固定 |
| open Issue に `close_reason` が残る退化ケースの扱い | 条件分岐を入れず pass-through する（`state` で読み分けない） | AI の仮定。根拠: (a) 決定 2 の pass-through を超える追加ルールを持ち込まない、(b) GitHub REST も reopen 後の open Issue に `reopened` を返すため非空自体は契約違反でない、(c) `evaluate_member_gate()` が `state == "closed"` を併せて要求するため安全側に倒れる。LocalProvider に reopen API は存在せず（`local.py` に該当メソッドなし）、手編集でのみ発生する。検査先: issue-review-design / issue-review-code | 上記 Issue 仮定第 1 項が扱っていない境界として明示的に分離し、方針節に判断根拠を記載 |
| `models.py` docstring と docs の更新範囲 | docstring は local 値域を追記（既存契約文は保持）。`docs/` 配下は更新不要 | Issue 本文 `## 決定事項` § 残す AI 仮定 第 2 項（設計フェーズで具体化）。検査先: issue-review-design | 追記後の docstring 文面を方針節に確定。`grep -rn "close_reason\|state_reason" docs/` を本設計時に再実行し hit 0 件（exit 1）を実測、完了条件 (b) を満たすことを影響ドキュメント節に記録 |
| `_local_cache.py` の GitHub cache reader | 本 Issue では修正しない | AI の判断。根拠: 対象データが GitHub REST payload で local の `close_reason` 契約と無関係／stale entry の合成 `state` に素通しすると非現実的な組み合わせを生み、その扱いは人間未決／`evaluate_member_gate()` へ到達する消費者が存在しない。検査先: issue-review-design（起票要否の判断を含む） | 根本原因節に全 `Issue(` 構築箇所の調査表とスコープ外根拠を記録 |

## テスト戦略

### 変更タイプ

実行時コード変更（`read_issue()` の戻り値が変わる）。docstring 更新を同梱するが、
主変更は実行時の振る舞いであるため実行時コード変更として扱う。

### 実行時コード変更の場合

#### Small テスト

新規追加は不要。本修正は file I/O を伴う `read_issue()` 内の 1 式であり、外部依存なしに
駆動できる純粋関数・分岐ロジックを新設しない。純粋層の既存カバレッジは以下で確保済み。

- `tests/test_series_models.py:170-173` `test_issue_state_reason_defaults_to_empty` —
  `Issue` の default 空文字列契約
- `tests/test_series_models.py:112-121` `test_member_gate_allowlists_only_closed_completed` —
  `evaluate_member_gate()` の allowlist（`completed` のみ成功）

これは `docs/dev/testing-convention.md` の「省略してよい理由」§「既存ゲートで不具合パターンを
捕捉できる」に該当する（不正当な省略理由「Small で十分」ではなく、**Small で表現できる
新規ロジックが存在しない**という理由である点に注意）。

#### Medium テスト（必須。bug 固有の再現テストを含む）

`tests/test_providers_local.py`（`pytestmark = pytest.mark.medium`、`tmp_path` 上の
実ファイル I/O）に追加する。検証観点:

1. **再現テスト（修正前 Red / 修正後 Green）**: `close_issue(reason="completed")` の
   **戻り値** の `state_reason == "completed"`。OB の「理由を渡した直後の戻り値ですら空」を
   直接 assert する
2. **読み戻し経路の網羅**: 同一 Issue に対し `view_issue()` と `list_issues(state="all")`
   の要素でも `state_reason == "completed"` になること。根本原因節で「1 関数集約」と主張した
   3 経路を実測で固定する
3. **`.lower()` 正規化**: 手編集で `close_reason: COMPLETED` と書かれた frontmatter を
   読み、`"completed"` が返ること（`github.py:240` との対称性）
4. **自由文字列 pass-through**: `close_reason: "merged into main"` がそのまま返ること。
   既存の書き込み側テスト（`:245-256`）と対になる読み出し側の契約固定
5. **表記ゆれが安全側に倒れること**: `close_reason: not-planned` → `state_reason ==
   "not-planned"` かつ `evaluate_member_gate(0, "closed", state_reason)` が
   `success=False` / `gate="mismatch:closed/not-planned"`
6. **`close_reason` 不在**: close 前の open Issue は `state_reason == ""`（後方互換）
7. **gate との結合**: `evaluate_member_gate(0, issue.state, issue.state_reason)` が
   `GateResult(success=True, gate="closed_completed")` を返すこと。OB で示された
   `mismatch:closed/` の解消を End-to-End で確認する

観点 3〜5 は frontmatter を直接書いて読ませる形で駆動する（書き込み側 API を変えないため）。
`docs/dev/testing-convention.md` の判定基準では「ファイル I/O / 内部サービス結合あり」→
Medium に該当し、既存ファイルの `pytestmark` と一致する。

Red→Green 証跡: 実装フェーズで、修正前に上記 1 が FAIL することと、修正後に 1〜7 が
PASS することを記録する（`_shared/design-by-type/bug.md` § 8 の必須要件。escape clause は
使用しない）。

#### Large テスト

新規追加は不要。理由:

- CLI / subprocess 境界の契約は変更しない。`kaji issue close --reason` の引数・出力・
  frontmatter への書き込み形式はいずれも不変であり、既存の
  `tests/test_local_cli_large_local.py:146-153`
  `test_issue_close_writes_close_reason` が CLI 経由の `close_reason: completed` 永続化を
  引き続き保護する
- 修正対象は provider 内部の読み出し境界であり、Medium で `tmp_path` 上の実ファイルを
  使って完全に再現できる（実 API 疎通・外部サービスを要しない）
- `state_reason` の唯一の消費者である series runner は `commands/series.py:65` により
  `provider.type='github'` 限定で、local provider の E2E 経路が存在しない

これは `testing-convention.md` の「省略してよい理由」§「既存ゲートで不具合パターンを
捕捉できる」に該当し、不正当な省略理由（実行時間・環境不備・「Small で十分」）のいずれにも
当たらない。

#### 副作用のある検証

`uv pip install -e .` 等の環境汚染を伴う検証は行わない（packaging / metadata の変更を
含まないため）。品質ゲートは `make check`（`ruff check` → `ruff format --check` → `mypy` →
`pytest` 全件）を使う。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `kaji_harness/providers/models.py`（`Issue.state_reason` docstring） | **あり** | 契約の正本。local の値域（`.lower()` のみの pass-through で GitHub 値域外もありうる）を追記する。既存契約文は保持（完了条件 (a)、決定 1） |
| `docs/adr/` | なし | 新規の技術選定・アーキテクチャ決定を伴わない。既存契約への実装追随であり ADR 化する判断が無い |
| `docs/ARCHITECTURE.md` | なし | provider の層構造・責務境界は不変 |
| `docs/dev/` | なし | 開発ワークフロー・テスト規約に変更なし |
| `docs/reference/` | なし | Python 規約・API 仕様の変更なし。既存 idiom（`github.py:240`）に揃えるのみ |
| `docs/cli-guides/` | なし | CLI の引数・出力・終了コードは不変。`local-mode.md` / `local-mode.ja.md` に `close_reason` / `state_reason` の記述なし（下記 grep） |
| `docs/operations/` | なし | `local-mode-runbook.md` / `local-mode-runbook.ja.md` に該当記述なし（下記 grep） |
| `AGENTS.md` / `CLAUDE.md` | なし | 規約変更なし |

完了条件 (b) の検証（本設計時に `fix/373` worktree で実行）:

```console
$ grep -rn "close_reason\|state_reason" docs/
$ echo $?
1
```

hit 0 件のため `docs/` 配下の更新は不要。Issue 本文が挙げた `docs/cli-guides/local-mode.md` /
`local-mode.ja.md` / `docs/operations/local-mode-runbook.md` / `local-mode-runbook.ja.md` を
含め、`docs/` 全体に該当記述は存在しない。

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `Issue.state_reason` 契約（正本） | `kaji_harness/providers/models.py:60-61` | 「Issue state の理由。GitHub の値を小文字に正規化し、provider が理由を持たない場合は空文字列。」— EB の根拠。LocalProvider は理由を持つため空文字列は契約違反 |
| 壊れている読み出し境界 | `kaji_harness/providers/_local_store.py:125-145` | `read_issue()` が `meta` から `id`/`title`/`state`/`labels`/`slug` のみを `Issue` に渡し、`close_reason` を参照しない。root cause の一次情報 |
| 書き込み側の契約 | `kaji_harness/providers/local.py:205-220` | `meta["close_reason"] = reason if reason else "completed"`。`reason` を検証せず任意文字列を永続化し、末尾で `read_issue()` を返す（戻り値も空になる根拠） |
| local 読み出し経路の集約 | `kaji_harness/providers/local.py:159, 163, 193, 220, 235` | `view_issue` / `list_issue_comments_all` / `edit_issue` / `close_issue` / `list_issues` がすべて `self._store.read_issue(...)` を経由する。修正点 1 箇所で足りる根拠 |
| GitHub provider の正規化 idiom | `kaji_harness/providers/github.py:240` | `state_reason=str(payload.get("stateReason", "") or "").lower()`。決定 2「GitHub provider と同じ `.lower()` 正規化」の具体形。本修正はこれと同一形にする |
| gate の allowlist | `kaji_harness/series/models.py:86-98` | `state == "closed"` かつ `reason == "completed"` のみ `success=True`。表記ゆれが安全側に倒れる根拠、および `state` 条件分岐が不要である根拠 |
| `state_reason` の消費者 | `kaji_harness/series/runner.py:152, 165, 234` / `kaji_harness/commands/series.py:65` | 消費は `evaluate_member_gate()` の 3 呼び出しのみ。series は `provider.type='github'` 限定で、現時点の利用者影響が無い（潜在バグである）根拠 |
| frontmatter 検証範囲 | `kaji_harness/providers/_local_common.py:78-120` | `validate_issue_meta()` は `id`/`state`/`labels`/`slug` のみ検証。`close_reason` が無検証であること、および検証追加が既存データをハードフェイルさせる根拠 |
| 既存の書き込み側テスト契約 | `tests/test_providers_local.py:245-290` | `"merged into main"` / `"not-planned"` を許容。自由文字列契約を破らない（＝書き込み時検証を追加しない）根拠 |
| 既存の GitHub 側正規化テスト | `tests/test_series_models.py:124-173` | `COMPLETED`→`completed` 等の正規化と default 空文字列を検証済み。Small を新規追加しない根拠 |
| 繰り延べの経緯 | `draft/design/issue-313-feat-issue-sequential-series-runner.md:228-237` | 「`LocalProvider` は `""` のまま（初期実装は GitHub provider のみが対象）」。空文字が意図的な繰り延べであり恒久方針でないこと、および GitHub 値域・allowlist 方針の根拠 |
| GitHub REST Issues API | https://docs.github.com/en/rest/issues/issues?apiVersion=2022-11-28 | `state_reason` の enum は `completed` / `reopened` / `not_planned` / `duplicate` / `null`。説明は "The reason for the state change. Ignored unless state is changed."（2026-07-24 取得）。local の `not-planned`（ハイフン）が GitHub 値域外であること、および reopen 後の open Issue が `reopened` を持ちうる＝非空 `state_reason` が open で契約違反にならないことの根拠 |
| 変更の起点 commit | `390dc7a`（2026-05-06） / `49c9a28`（2026-07-14, `#313`） | `git log -S` で特定。前者が書き込み側、後者が `state_reason` field と GitHub 読み出しを追加。契約食い違いは後者以降 |
| テスト規約 | `docs/dev/testing-convention.md` | サイズ判定基準（ファイル I/O → Medium）、省略の正当/不正当理由の分類 |
| bug 設計ガイド | `.claude/skills/_shared/design-by-type/bug.md` | OB/EB/再現手順の分離、根本原因の「なぜ・いつから・同根の他の壊れ箇所」、再現テスト必須（§ 8） |
