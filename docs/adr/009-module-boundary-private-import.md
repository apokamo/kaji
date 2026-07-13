# ADR 009: モジュール境界と private import 規約

## ステータス

承認（2026-07-13） — Issue #285 の設計レビュー（`/issue-review-design` → `/issue-verify-design`）を
通過し、実装フェーズで承認に更新した。

## コンテキスト

`kaji_harness` は package / module 間の依存規約を持たず、`_` 始まりのシンボルや private module
（`_*.py`）を package を跨いで直接 import する箇所が蓄積していた。着手時 main を AST で棚卸しすると
private import は 29 statement あり、うち 7 statement が package / 層の境界を越えて他 module の内部実装に
依存していた。

構造上の害は 2 つ。

1. **実行時循環依存**: `sync.py` が `providers.local._atomic_write` を top-level import する一方、
   `providers/local.py` は `..sync._detect_legacy_forge_cache` を関数内 deferred import で読んでいた。
   deferred import は循環を回避するための回避策であり、依存が双方向であるという事実を隠していた。
2. **層の逆流**: `providers/` は下位層であるべきなのに、application 層の `sync` へ実行時依存していた。

一方、「名前が `_` で始まる」ことだけを根拠に全件を public 化する対処は誤りである。PEP 8 の
`_single_leading_underscore` は "weak *internal use* indicator" にすぎず、「内部」の範囲を決めるのは
名前ではなく package の凝集境界である。同一 package 内の implementation detail を public 化しても
API 表面が広がるだけで、境界を守る効果はない。

## 決定

### 決定 1: 依存の向きを層で固定する

```
[foundation]  errors.py / fsio.py            ← kaji_harness 内部依存ゼロ
[provider]    providers/                     ← foundation のみに依存
[application] sync.py / state.py / artifacts.py / worktree_discovery.py / runner.py …
[command]     commands/
[shim]        cli_main.py                    （#284 で削除）
```

下位層から上位層への依存を禁止する。循環回避のための deferred import（関数内 import）は、
依存の向きが誤っていることの兆候として扱い、構造で解消する。

### 決定 2: private import を 3 分類し、境界を越えるものだけを禁止する

statement `S`（import 元 module `M`、解決後の import 先 module `T`、import される名前群 `N`）について:

```
is_private(x) := x.startswith("_") かつ not x.startswith("__")   # dunder は除外
pkg(X)        := X が package の __init__ なら X 自身、そうでなければ X の親 package
private(S)    := T のコンポーネントに private module がある、または N に private symbol がある
禁止(S)       := private(S) かつ pkg(M) != pkg(T)
```

| 分類 | 条件 | 扱い |
|---|---|---|
| **禁止** | `pkg(M) != pkg(T)` | 解消する（public 昇格 / 責務移動 / 共通 module 抽出） |
| **許容** | `pkg(M) == pkg(T)` | 同一 cohesive package 内の implementation detail。public 化しない |
| **public re-export** | `__init__.py` が自 package の private module を読み、その名前を `__all__` に載せる | facade として意図的に公開する |

private module（`_*.py`）を package 外から読むことは禁止する。外部公開が必要なら `__init__.py`
facade の `__all__` に**必要最小限のシンボルだけ**を載せる。module ごと public 化すると、外部が
必要としないシンボルまで表面化する。

### 決定 3: `__all__` は宣言であり、強制は fitness test が担う

`__all__` の実行時効果は `from package import *` が import する名前の制御に限られ、
`from kaji_harness.providers._mappings import LABEL_TO_PREFIX` のような named import を禁止する
**強制力を持たない**。したがって `__all__` は PEP 8 に基づく「public API の宣言」という規約上の
意思表示であり、private 境界の**強制は `tests/test_private_imports.py` の fitness test** が担う。

fitness test は `ast.Import`（`import a.b`）と `ast.ImportFrom`（`from a import b`）の**両ノード**を
走査する。前者を見落とすと `import kaji_harness.providers._mappings` 形式の境界違反を取りこぼす。
`make check` が marker フィルタ無しの `pytest` を実行するため、追加の Makefile ターゲットは不要。

層方向は `tests/test_layer_imports.py` が runtime import 全体を検査する。module→層の明示的な
対応表を持ち、下位層から上位層への依存と foundation から `kaji_harness` 内部への依存を禁止する。
`TYPE_CHECKING` guard 内の import は実行時依存と分離し、関数内 deferred import は runtime edge
として検査する。新 module の未分類と mapping の stale entry も fail させる。

### 決定 4: 時限許容は statement 単位の allowlist で管理し、stale を fail させる

移行途上の shim（現状は #283 が作った `cli_main.py`、削除は #284）が持つ境界違反は、「許容」の
subtype = **時限許容**として扱う。分類軸は 3 つのまま増やさない。

allowlist は **module 単位にしない**。`cli_main.py` を丸ごと除外すると、新しい境界違反が追加されても
既存 1 件が残る限り検査を素通りしてしまう。代わりに statement 単位の正規化 signature を持つ:

```
signature(S) := (M, T, tuple(sorted(N のうち private な名前)))
```

行番号は含めない（無関係な行の増減で churn するため）。検証器は 2 条件を**同時に**課す:

| 条件 | 検出する事象 | 判定 |
|---|---|---|
| 禁止 signature が allowlist に無い | 新規の境界違反 | fail |
| allowlist entry に対応する statement が無い | 期限切れ / 変化（shim 削除、import 行の書き換え） | fail（stale） |

両者を合わせると `{禁止 signature} == TRANSITIONAL_ALLOWLIST` の厳密一致になり、追加・変化・
期限切れがすべて fail する。#284 が `cli_main.py` を削除した時点で全 entry が stale になり、
allowlist の撤去が機械的に強制される。

ADR 008（後方互換レイヤを提供しない）との関係: 本規約は互換レイヤを**新規に書かない**。stale 検出は
むしろ shim の確実な除去を強制する装置である。

## 帰結

- `providers/` から application 層への実行時依存がゼロになり、`sync ↔ providers.local` の循環と
  deferred import 2 箇所が消えた。
- 境界違反は `make check` で機械的に検出される。将来の module 追加で規約違反が再発しても、
  ruff / mypy が検出できないこの規則を fitness test が拾う。
- 新しい共通ロジックの置き場所は「消費者がどの層にいるか」で決まる。複数層が使う汎用 utility は
  foundation（`fsio.py`）へ、複数 module が共有する契約は当該 package の public module
  （`providers/cache_guard.py`）へ置く。
- 内部 module の import path 変更は公開契約（`pyproject.toml` の `kaji` console entry point）を
  壊さないため、ADR 008 決定 2 の CHANGELOG BREAKING エントリの対象外。

## 参照

- Issue #285 / 設計書 `draft/design/issue-285-refactor-private-import-r3.md`
- 検証器: `tests/test_private_imports.py`
- 層方向検証器: `tests/test_layer_imports.py`
- [PEP 8 — Naming Conventions](https://peps.python.org/pep-0008/#descriptive-naming-styles)
- [Python Tutorial — Importing * From a Package](https://docs.python.org/3/tutorial/modules.html#importing-from-a-package)
- ADR 008: 後方互換レイヤを提供しない
