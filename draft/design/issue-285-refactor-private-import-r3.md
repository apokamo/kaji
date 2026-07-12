# [設計] private シンボルのモジュール横断 import を解消(R3)

Issue: #285

## 概要

`kaji_harness` 内の private symbol / private module import を AST で全件棚卸しし、**package 境界を越えて内部実装へ依存する 7 statement** を共通 module 抽出・責務移動・facade 経由の public re-export で解消する。同一 package 内の implementation detail 利用（13 statement）と `__init__.py` の意図的 re-export（1 statement）は「許容」として検証対象から除外し、除外条件を機械検証可能な形で固定する。

副次効果として、`sync ↔ providers.local` の**実行時循環依存**（deferred import で回避されていた）を構造的に解消する。

## 背景・目的

### 現状の問題

Issue 本文は 2026-07-12 main を基準に「11 statement」としていたが、先行 Issue #283（`cli_main.py` → `kaji_harness/commands/` 分割）が `7951fb8` でマージされた結果、**着手時 main では 29 statement** に増えている。完了条件「着手時 main を AST で再計測し、private import 全 statement を設計書へ列挙」に従い、本設計は 29 statement を正本とする。

Issue 本文が挙げた禁止対象候補 5 関係（6 statement）は、#283 による移動を反映すると以下に対応する（**全 5 関係が本設計の禁止対象に含まれる**）。

| Issue 本文の候補 | 着手時 main での実体 | 本設計での扱い |
|---|---|---|
| `cli_main.py -> state._format_issue_ref` | `commands/issue.py:21`, `commands/run.py:26` へ移動（1 → 2 statement） | 禁止（F2/F3） |
| `sync.py -> providers.local._atomic_write` | `sync.py:23`（不変） | 禁止（F6） |
| `worktree_discovery.py -> providers._mappings` | `worktree_discovery.py:15`（不変） | 禁止（F7） |
| `artifacts.py -> providers._worktree` | `artifacts.py:51`（不変） | 禁止（F1） |
| `providers/local.py -> sync._detect_legacy_forge_cache`（2 statement） | `providers/local.py:691,792`（不変） | 禁止（F4/F5） |

構造上の中核問題は 2 つある。

1. **実行時循環依存**: `sync.py:23` が `providers.local._atomic_write` を module top-level で import する一方、`providers/local.py:691,792` は `..sync._detect_legacy_forge_cache` を**関数内 deferred import** で読んでいる。deferred import は循環を回避するための回避策であり、依存の向きが双方向であるという事実を隠している。
2. **層の逆流**: `providers/` package は top-level module への実行時依存を `sync` **1 本しか持たない**（他は stdlib / 同一 package / `TYPE_CHECKING` 下の `config` のみ。`kaji_harness/providers/*.py` の import 文を全数確認済み）。つまり `providers/local.py -> sync` を除去すれば、`providers/` は「上位層へ依存しない下位層」として完全に成立する。

### 改善指標

| 指標 | ベースライン | 目標 |
|---|---|---|
| private import statement 総数 | 29 | 分類済み 29（全件を表に記録） |
| **禁止**分類の statement | 7 | **0** |
| `providers/` → application 層への実行時依存 | 1（`local.py` → `sync`） | **0** |
| 循環依存（`sync` ↔ `providers.local`） | 1 | **0** |
| deferred import による循環回避 | 2 箇所（`local.py:691,792`） | 0 |
| `format_issue_ref` ロジックの重複実装 | 2（`state.py:18`, `providers/context.py:107`） | 1 |
| 禁止 import 0 件の機械検証 | 無し | 恒久 fitness test で `make check` にゲート |
| CLI 契約（`kaji` entry point） | — | 不変 |

## ベースライン計測

すべて worktree `../kaji-refactor-285`（base = `7951fb8`）で実行し、記録した。

### 1. private import 全件棚卸し（AST）

再現コマンド（実装後は恒久 fitness test が同じ分類器を使う。§ テスト戦略）:

```bash
python - <<'PY'
import ast, pathlib
for p in sorted(pathlib.Path("kaji_harness").rglob("*.py")):
    for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        if isinstance(n, ast.ImportFrom):
            if (n.module or "") == "__future__":
                continue
            mod = [c for c in (n.module or "").split(".") if c]
            priv_mod = any(c.startswith("_") and not c.startswith("__") for c in mod)
            priv_sym = [a.name for a in n.names
                        if a.name.startswith("_") and not a.name.startswith("__")]
            if priv_mod or priv_sym:
                print(f"{p}:{n.lineno}\tfrom {'.'*n.level}{n.module or ''} "
                      f"import {', '.join(a.name for a in n.names)}")
PY
```

**結果: 29 statement**（`__future__` 除外）。内訳は § private import の分類 の表を正本とする。

### 2. 既存テスト / 品質ゲート

```bash
source .venv/bin/activate && make check      # lint + format --check + typecheck + test（Makefile:6）
```

- `pytest`: **2345 passed, 1 skipped**（194s）。baseline failure 無し。
- 以降の実装フェーズでは「新規 FAILED / ERROR が 0」を回帰判定基準とする。

### 3. 変更対象モジュールの safety net（カバレッジ）

```bash
python -m pytest -q --cov=kaji_harness.state --cov=kaji_harness.sync \
  --cov=kaji_harness.artifacts --cov=kaji_harness.worktree_discovery \
  --cov=kaji_harness.providers --cov=kaji_harness.commands \
  --cov-report=term-missing:skip-covered
```

| module | Cover | 移動対象シンボルを守る既存テスト |
|---|---|---|
| `state.py` | 99% | `tests/test_state_persistence.py`（progress.md generation） |
| `sync.py` | 82% | `tests/test_sync_from_github.py` / `tests/test_legacy_forge_cache_detection.py` |
| `artifacts.py` | 100% | `tests/test_artifacts_dir.py` |
| `worktree_discovery.py` | 91% | `tests/test_worktree_discovery.py` |
| `providers/local.py` | 80% | `tests/test_providers_local.py`（`_atomic_write` を直接検証: L90） |
| `providers/context.py` | 100% | （complete coverage） |
| `providers/_mappings.py` | 100% | （complete coverage） |
| `providers/_worktree.py` | 97% | `tests/test_resolve_main_worktree.py` |
| `providers/__init__.py` | 98% | — |
| `commands/issue.py` | 85% | — |
| `commands/run.py` | 93% | — |

**判定: safety net は十分**。移動する 4 シンボルはいずれも既存テストで直接または経路経由で検証されている。特に `_detect_legacy_forge_cache` は専用ファイル `tests/test_legacy_forge_cache_detection.py`（関数直呼び 3 ケース + provider 経路 3 ケース + CLI dispatch 2 ケース）が存在する。refactor 着手前に safety net を追加する必要は無い。

## private import の分類

### 判定ルール（機械判定可能な定義）

statement `S`（import 元 module `M`、解決後の import 先 module `T`、import される名前群 `N`）について:

```
private(S)  := T の kaji_harness 配下コンポーネントに `_x` を含む（= private module）
               または N に `_x` を含む（= private symbol）
               ※ dunder（`__x`）は除外。`__future__` は対象外
pkg(X)      := X が package の __init__ なら X 自身、そうでなければ X の親 package
禁止(S)     := private(S) かつ pkg(M) != pkg(T)
```

相対 import の解決は Python 言語仕様（`ImportFrom.level`）に従う。`base := ancestor(pkg(M), level - 1)`、`T := base + "." + module`。

**この 1 行の規則が、Issue が要求する 3 分類をそのまま生成する**:

- `pkg(M) == pkg(T)` → **許容**（同一 cohesive package 内の implementation detail）
- `pkg(M) != pkg(T)` → **禁止**（package / 層を越えた内部実装依存）
- `__init__.py` から自 package の private module を読む → `pkg(M) == pkg(T)` なので自動的に許容。これを `__all__` に載せた時のみ **public re-export** として区別する

分類は「名前が `_` で始まる」ことだけを根拠にせず、**境界を越えるか否か**を根拠にする。PEP 8 が `_single_leading_underscore` を "weak *internal use* indicator" と定義している（[Primary Sources] 1）のに整合する: 内部利用の範囲を決めるのは package の凝集境界であって、名前そのものではない。

### 第 4 分類: 時限的除外（transitional shim）

上記 3 分類では `cli_main.py`（8 statement）を扱えない。`cli_main.py` は #283 が作った**互換 shim** であり、その docstring 自身が最終削除先を宣言している:

> ```
> """kaji CLI 互換 shim。実体は kaji_harness.commands 配下(#283 R1 で分割)。
> 旧 `from kaji_harness.cli_main import X` / `python -m kaji_harness.cli_main` /
> console entrypoint `kaji_harness.cli_main:main` を維持する。最終削除は #284。
> """
> ```
> — `kaji_harness/cli_main.py:1-5`

この 8 statement を「禁止」として解消するには `commands/*` の private symbol 約 60 個を public 昇格する必要があるが、

1. #284 が shim 自体を削除するため、昇格した public 名は**即座に不要になる**
2. Issue 本文の指示「`_` で始まる事実だけで public 化せず、公開範囲を広げるコストも判断理由へ含める」に真っ向から反する
3. Issue 本文の対象外宣言「tests の最終 import 移行と `cli_main` shim 削除（#284）」と衝突する

よって **`kaji_harness/cli_main.py` を検証除外（時限的許容）** とする。除外が恒久化して腐るのを防ぐため、検証器に **stale exclusion 検出**を実装する: 除外エントリが禁止 statement に 1 件も一致しなくなったら **fail** させる。#284 が `cli_main.py` を削除した瞬間にテストが赤くなり、除外エントリの撤去が強制される。

> ADR 008（後方互換レイヤを提供しない）との関係: 本設計は互換レイヤを**新規に書かない**。既存 shim は #282 → #283 → #284 の移行シーケンスの中間成果物であり、本 Issue はそれを延命も拡張もしない。stale exclusion 検出はむしろ shim の確実な除去を機械的に強制する装置である。

### 全 29 statement の分類

| # | statement | 分類 | 理由 |
|---|---|---|---|
| 1 | `artifacts.py:51` → `providers._worktree.resolve_main_worktree` | **禁止** | top-level → `providers` の private module |
| 2-9 | `cli_main.py:13,28,48,56,68,90,95,102` → `commands.{config,issue,output,parser,pr,recover,run,validate}` | **時限除外** | 互換 shim。#284 で削除（stale 検出付き） |
| 10 | `commands/issue.py:21` → `..state._format_issue_ref` | **禁止** | `commands` package → top-level の private symbol |
| 11 | `commands/issue.py:22` → `.config._load_config_for_dispatch` | 許容 | 同一 package |
| 12 | `commands/issue.py:24` → `.output._emit_json, _issue_to_json_dict, _read_body_arg` | 許容 | 同一 package |
| 13 | `commands/issue.py:25` → `.pr._forward_to_gh` | 許容 | 同一 package（責務配置は #286 の検討対象） |
| 14 | `commands/main.py:7` → `.issue._handle_issue` | 許容 | 同一 package |
| 15 | `commands/main.py:9` → `.pr._handle_pr` | 許容 | 同一 package |
| 16 | `commands/pr.py:14` → `.config._load_config_for_dispatch` | 許容 | 同一 package |
| 17 | `commands/pr.py:16` → `.output._compose_json_and_jq, _read_body_arg` | 許容 | 同一 package |
| 18 | `commands/recover.py:30` → `.run._validate_workflow_provider_match` | 許容 | 同一 package |
| 19 | `commands/run.py:26` → `..state._format_issue_ref` | **禁止** | `commands` package → top-level の private symbol |
| 20 | `commands/run.py:28` → `.config._emit_provider_overlay_divergence_warning` | 許容 | 同一 package |
| 21 | `providers/__init__.py:17` → `._worktree.resolve_main_worktree` | **public re-export** | facade。`__all__` へ明示化する（後述） |
| 22 | `providers/context.py:14` → `._mappings.LABEL_TO_PREFIX` | 許容 | 同一 package |
| 23 | `providers/github.py:19` → `._mappings.labels_to_branch_prefix` | 許容 | 同一 package |
| 24 | `providers/local.py:24` → `._mappings.DEFAULT_BRANCH_PREFIX` | 許容 | 同一 package |
| 25 | `providers/local.py:691` → `..sync._detect_legacy_forge_cache` | **禁止** | `providers` → application 層（層の逆流 + 循環） |
| 26 | `providers/local.py:792` → `..sync._detect_legacy_forge_cache` | **禁止** | 同上 |
| 27 | `providers/local.py:748` → `._mappings.labels_to_branch_prefix` | 許容 | 同一 package |
| 28 | `sync.py:23` → `providers.local._atomic_write` | **禁止** | top-level → `providers` package の private symbol |
| 29 | `worktree_discovery.py:15` → `providers._mappings.DEFAULT_BRANCH_PREFIX, LABEL_TO_PREFIX` | **禁止** | top-level → `providers` の private module |

**集計: 禁止 7 / 許容 13 / public re-export 1 / 時限除外 8 = 29**

`commands/` 内 9 statement と `providers/` 内 4 statement を「許容」とする根拠: いずれも単一 package 内で完結する implementation detail であり、package 外に消費者が存在しない。public 化しても API 表面が広がるだけで、境界を守る効果は無い。ただし `commands/issue.py → commands/pr.py._forward_to_gh`（#13）は共有 utility の配置ずれであり、責務移動は後続 #286 の範囲として記録する（本 Issue では変更しない）。

## インターフェース

### 入力 / 出力

本 Issue は内部構造の再編であり、**CLI の入出力契約を一切変更しない**。

### 公開 IF 不変宣言

`pyproject.toml:41-42` が示すとおり、kaji が外部へ公開する契約は console entry point ただ 1 つ:

```toml
[project.scripts]
kaji = "kaji_harness.cli_main:main"
```

`kaji_harness.*` の Python import path は published API ではない（library として配布していない）。したがって:

- `kaji <subcommand>` の引数・出力・exit code: **不変**
- `kaji_harness.cli_main:main` の解決可能性: **不変**（shim は維持）
- 内部 module 間の import path 変更: 公開契約の破壊に当たらない → ADR 008 決定 2 の CHANGELOG BREAKING エントリは**不要**

### 内部 IF の新構造（層と依存の向き）

```
[foundation]  errors.py (SyncError を追加) ,  fsio.py (新設)      ← kaji_harness 内部依存ゼロ
                    ▲                              ▲
[provider]    providers/                           │
                ├ __init__.py   facade: __all__ に resolve_main_worktree /
                │               LABEL_TO_PREFIX / DEFAULT_BRANCH_PREFIX を明示
                ├ cache_guard.py (新設, public)  detect_legacy_forge_cache
                ├ context.py     format_issue_ref を正本化
                ├ _mappings.py / _worktree.py    private のまま（facade 経由でのみ外部公開）
                └ local.py / github.py / base.py / models.py / markers.py
                    ▲
[application] sync.py / state.py / artifacts.py / worktree_discovery.py / runner.py …
                    ▲                          （providers の public 名のみを import）
[command]     commands/
                    ▲
[shim]        cli_main.py   （#284 で削除）
```

**規約**: 依存は上向き（下位層 → 上位層）を禁止する。private module（`_*.py`）を package 外から読むことを禁止し、外部公開が必要なら `__init__.py` facade の `__all__` に載せる。

### 使用例

```python
# Before: private symbol を package を跨いで読む
from .providers._worktree import resolve_main_worktree   # artifacts.py
from .providers._mappings import DEFAULT_BRANCH_PREFIX   # worktree_discovery.py
from .providers.local import _atomic_write               # sync.py
from ..state import _format_issue_ref                    # commands/issue.py
from ..sync import _detect_legacy_forge_cache            # providers/local.py（関数内 deferred）

# After: facade の public 名 / foundation module / 正本化した public 関数
from .providers import resolve_main_worktree                       # artifacts.py
from .providers import DEFAULT_BRANCH_PREFIX, LABEL_TO_PREFIX      # worktree_discovery.py
from .fsio import atomic_write                                     # sync.py
from ..providers.context import format_issue_ref                   # commands/issue.py
from .cache_guard import detect_legacy_forge_cache                 # providers/local.py（top-level）
```

## 制約・前提条件

- **振る舞い非変更が絶対要件**。feat / bug 修正を混在させない。
- `SyncError` の**基底クラスは `RuntimeError` のまま維持**する。`errors.py` の既存階層は `HarnessError(Exception)` を基底とするが、`SyncError` を `HarnessError` に付け替えると `except` の到達範囲が変わり振る舞い変更になる。基底の統一は本 Issue の scope 外（別 Issue 候補として記録）。
- **patch target の維持**: `tests/test_preflight.py:446` は `patch.object(_local_mod, "_atomic_write_new")` で `providers/local.py` の名前空間を差し替えている。`from ..fsio import atomic_write_new` と書けば名前は `local.py` の名前空間に束縛されるため、`patch.object(_local_mod, "atomic_write_new")` は引き続き機能する（[Primary Sources] 4「Where to patch」: patch は定義元ではなく**参照側の名前空間**を差し替える）。import 形式を `import fsio` + `fsio.atomic_write_new(...)` に変えると patch 経路が壊れるため、**`from ... import <name>` 形式を維持する**。
- `.venv` は main worktree への symlink。`source .venv/bin/activate && make check` で実行する。
- ADR 008 に従い、後方互換 re-export（例: `sync.py` に `SyncError` を残す alias）は**書かない**。呼び出し側を移行する。

## 変更スコープ

### 変更するファイル

| ファイル | 変更内容 |
|---|---|
| `kaji_harness/fsio.py` | **新設**。`atomic_write` / `atomic_write_new`（`providers/local.py` から移動、public 化） |
| `kaji_harness/providers/cache_guard.py` | **新設**。`detect_legacy_forge_cache`（`sync.py` から移動、public 化） |
| `kaji_harness/errors.py` | `SyncError` を移設（基底 `RuntimeError` を維持） |
| `kaji_harness/sync.py` | `_atomic_write` / `_detect_legacy_forge_cache` / `SyncError` の定義を削除し、新配置から import |
| `kaji_harness/providers/local.py` | `_atomic_write` / `_atomic_write_new` 定義を削除し `..fsio` から import。deferred import 2 箇所を `from .cache_guard import ...` の top-level import へ |
| `kaji_harness/providers/__init__.py` | facade 強化: `_mappings` の 2 定数を re-export、`__all__` に `resolve_main_worktree` / `LABEL_TO_PREFIX` / `DEFAULT_BRANCH_PREFIX` を明示 |
| `kaji_harness/providers/context.py` | `format_issue_ref` を正本化（docstring の「将来の統合時は本実装を正本にする」を実行） |
| `kaji_harness/state.py` | `_format_issue_ref` 定義を削除し `providers.context.format_issue_ref` を使用 |
| `kaji_harness/commands/issue.py` | `..state._format_issue_ref` → `..providers.context.format_issue_ref` |
| `kaji_harness/commands/run.py` | 同上 |
| `kaji_harness/commands/sync.py` | `SyncError` の import 元を `..errors` へ |
| `kaji_harness/artifacts.py` | `providers._worktree` → `providers` facade |
| `kaji_harness/worktree_discovery.py` | `providers._mappings` → `providers` facade |
| `tests/test_private_imports.py` | **新設**。分類器 + fitness test |
| `tests/test_providers_local.py` | `_atomic_write` の import 元・名前を機械的修正 |
| `tests/test_preflight.py` | patch target 名を `_atomic_write_new` → `atomic_write_new` に機械的修正 |
| `tests/test_legacy_forge_cache_detection.py` | `_detect_legacy_forge_cache` / `SyncError` の import 元を機械的修正 |
| `docs/adr/009-*.md` | **新設**。モジュール境界と private import 規約 |
| `docs/ARCHITECTURE.md` | パッケージ構成に新 module と層の依存方向を追記 |
| `docs/reference/python/python-style.md` | ADR 009 への参照を 1 節追加 |

### 対象外

- `cli_main.py` shim の削除と tests の最終 import 移行（**#284**）
- domain / application 責務の全面移動、`commands/issue.py → commands/pr.py` の共有 utility 再配置（**#286**）
- `SyncError` の基底を `HarnessError` へ統一すること（振る舞い変更のため別 Issue）
- feat / bug 修正

## 方針

### 禁止 7 statement の解消策

| ID | 対象 | 手段 | 内容 |
|---|---|---|---|
| **F1** | `artifacts.py:51` → `providers._worktree` | public re-export | `providers/__init__.py` の `__all__` に `resolve_main_worktree` を明示し、`from .providers import resolve_main_worktree` に変更。関数内 deferred import の形は維持（import タイミングを変えない） |
| **F2/F3** | `commands/{issue,run}.py` → `state._format_issue_ref` | 責務移動（重複解消） | `providers/context.py:107` の `format_issue_ref` を正本にし、`state._format_issue_ref` を削除。`state.py` / `commands/*` は `providers.context.format_issue_ref` を使う |
| **F4/F5** | `providers/local.py:691,792` → `sync._detect_legacy_forge_cache` | 責務移動（層の是正） | `providers/cache_guard.py` を新設して `detect_legacy_forge_cache` を移動。`SyncError` は `errors.py`（foundation）へ移設。`sync.py` は `providers.cache_guard` から import（下位層 → 上位層の依存が消える） |
| **F6** | `sync.py:23` → `providers.local._atomic_write` | 共通 module 抽出 | `kaji_harness/fsio.py` を新設し `atomic_write` / `atomic_write_new` を public 化。`providers/local.py` と `sync.py` の双方が foundation を見る |
| **F7** | `worktree_discovery.py:15` → `providers._mappings` | public re-export | `providers/__init__.py` の `__all__` に `LABEL_TO_PREFIX` / `DEFAULT_BRANCH_PREFIX` を明示。`_mappings.py` は private module のまま（`labels_to_branch_prefix` は package 外に消費者がいないので公開しない） |

`_mappings.py` / `_worktree.py` を public module へ改名しない理由: 外部が必要とするのは 3 シンボルのみで、module ごと公開すると不要な `labels_to_branch_prefix` まで表面化する。facade で必要最小限だけ公開する方が、Issue 本文の「公開範囲を広げるコストも判断理由へ含める」に合致する。

### 新設 module の配置根拠（代替案の検討）

**`kaji_harness/fsio.py`（`atomic_write` / `atomic_write_new`）**

- 既存 module への相乗りを検討したが該当なし: `errors.py` は例外専用、`config.py` / `state.py` は自身が application 層で `providers/` から依存できない、`local_init.py` は `kaji local init` 専用。atomic write は kaji 固有の概念を含まない純粋な filesystem utility であり、foundation 層に独立させるのが凝集度・依存方向の双方で最適。
- 配置を `providers/` 配下にしない理由: 消費者は `providers.local` と `sync`（application 層）の両方であり、provider 固有の概念ではない。`providers/` に置くと `sync -> providers` の依存が「fs utility を借りるため」という弱い理由で残り、層の意味が濁る。
- 命名: 既存 module 群（`config` / `models` / `errors` / `state` / `sync` / `runner`）と同じ「短い小文字の名詞」慣習に合わせた。`atomic_io` も候補だが、将来 atomic 以外の I/O helper を集約する余地を残す `fsio` を採る。

**`kaji_harness/providers/cache_guard.py`（`detect_legacy_forge_cache`）**

- `providers/local.py` 内に直接置く案は却下: `sync.py` が `providers.local` を import することになり、F6 で解消したはずの「application → provider 実装 module」の結合が別経路で復活する。cache layout の guard は `LocalProvider` の実装詳細ではなく、`sync`（書き手）と `LocalProvider`（読み手）が共有する契約なので、独立した public module に置く。
- 命名: 役割（legacy cache の検出と fail-fast）をそのまま表す。`SyncError` を送出する契約は不変。

### import 循環を新たに生まないことの検証

本設計の依存追加は次の 3 本のみで、いずれも**下位層への一方向**であり循環を作らない:

- `providers/local.py -> kaji_harness.fsio`（foundation）
- `providers/cache_guard.py -> kaji_harness.errors`（foundation）
- `sync.py -> kaji_harness.fsio` / `kaji_harness.providers.cache_guard`

`fsio.py` と `errors.py` は kaji_harness 内部への import を一切持たない（`errors.py` は現状 `pathlib` のみ、`fsio.py` は `os` / `pathlib` のみ）。したがって `providers/` から上位層への実行時依存は**ゼロ**になり、`providers/local.py` の deferred import（`local.py:691,792`）は top-level import へ戻せる。

実装フェーズでは、deferred import を除去した状態で `python -c "import kaji_harness.sync, kaji_harness.providers.local"` が `ImportError` を出さないことを確認する（循環があればここで即座に失敗する）。恒久的には fitness test（§ テスト戦略）が import 方向の規約違反を継続的に検出する。

### `format_issue_ref` 正本化の妥当性（振る舞い等価性）

2 実装のロジックは完全に一致する:

```python
# state.py:18   s = str(issue); return f"#{s}" if s.isdigit() else s
# providers/context.py:113   return f"#{issue_id}" if issue_id.isdigit() else issue_id
```

差分は `str | int` を受けるか `str` のみかだけ。全呼び出し元が str を渡すことを確認済み:

- `state.py:182` `_format_issue_ref(self.issue_number)` — `issue_number: str`（`state.py:56`）かつ `__post_init__` が `str(...)` で正規化（`state.py:70`）
- `commands/issue.py:496` `_format_issue_ref(rid.value)` — `ResolvedId.value: str`
- `commands/run.py:221` `_format_issue_ref(args.issue)` — argparse 由来の str

よって `format_issue_ref(issue_id: str)` へ集約しても到達可能な振る舞いは変わらない。mypy が型面をバックストップする。`_format_issue_ref` を直接参照するテストは存在しない（grep 済み）ため、テスト側の修正も発生しない。

### Before / After 依存構造

```
Before                                   After
------                                   -----
sync.py ──top-level──▶ providers.local   sync.py ──▶ fsio (foundation)
   ▲                        │                        ▲
   └──deferred(循環回避)─────┘            providers.local ──▶ fsio
                                         sync.py ──▶ providers.cache_guard
   ※ 双方向依存。deferred import が          providers.cache_guard ──▶ errors
     循環を隠蔽                            ※ providers → application の依存ゼロ。
                                            循環消滅。deferred import 不要
```

### 移行ステップ（TDD、この順序で実施）

1. **検証器を先に書く**（Red）: `tests/test_private_imports.py` に分類器と fitness test を実装。この時点で「禁止 7 件」を検出して**失敗する**ことを確認する（検証器が実際に効いていることの証明）。
2. `kaji_harness/fsio.py` 新設 → `providers/local.py` / `sync.py` を移行（F6）。tests の import / patch target を機械修正。
3. `errors.py` へ `SyncError` 移設 → `providers/cache_guard.py` 新設（F4/F5）。`sync.py` / `commands/sync.py` / `commands/issue.py` / tests の import を移行。deferred import を削除し、`python -c "import kaji_harness.sync, kaji_harness.providers.local"` が成功する（= 循環が無い）ことを確認。
4. `providers/context.format_issue_ref` 正本化 → `state.py` / `commands/*` を移行（F2/F3）。
5. `providers/__init__.py` の facade を明示化 → `artifacts.py` / `worktree_discovery.py` を移行（F1/F7）。
6. fitness test が **Green**（禁止 0 件）になることを確認。
7. `make check` 全通過を確認。

各ステップ後に `pytest` を回し、新規 FAILED / ERROR が 0 であることを確認する。

## テスト戦略

### 変更タイプ

**実行時コード変更**（module 間のシンボル移動・import 経路変更）。振る舞いは非変更だが実行時コードが動くため、`docs/dev/testing-convention.md` の「実行時の振る舞いを変える変更」に準じて S/M/L の観点を定義する。

### Small テスト

**新規追加**: `tests/test_private_imports.py`（`@pytest.mark.small`）

1. **分類器の単体テスト**（合成入力。実リポジトリに依存しない）
   - 相対 import の level 解決: `from ..state import _x`（level=2, `commands/issue.py` 起点）→ `kaji_harness.state` に解決されること
   - package `__init__.py` 起点の level=1 が**自 package** に解決されること（`providers/__init__.py` → `providers._worktree` が許容になる根拠）
   - 同一 package 内の private import → **許容**
   - package 境界を越える private symbol / private module import → **禁止**
   - dunder（`__future__` 等）を private と誤判定しないこと
   - public symbol の import は対象外であること
   - 除外リストに載る module の禁止 statement が結果から除かれること
   - **stale exclusion 検出**: 除外エントリが 1 件も一致しない場合に fail すること
2. **fitness test（回帰ゲート）**
   - 実際の `kaji_harness/` ツリーに対し **禁止 statement が 0 件**であること
   - 除外リストが stale でないこと（= `cli_main.py` に禁止 statement が現存すること）

これは恒久回帰テストとして追加する妥当性がある: 将来の module 追加で境界違反が再発しうる（実際 #283 が 11 → 29 statement に増やした）、かつ既存ゲート（ruff / mypy）はこの規則を検出できない。

**既存テストで担保する等価性**（新規 Small は追加しない）:
- `format_issue_ref` の入出力等価性 → `tests/test_state_persistence.py`（progress.md generation）が `#<n>` / `local-*` 双方の描画を既にカバー（`state.py` 99%）

### Medium テスト

**新規追加なし**。移動する 4 シンボルはいずれも既存 Medium テストが直接検証しており、それらが**そのまま bridging test として機能する**（refactor.md § 7「既存テストで十分な場合は新規不要（エビデンスを書く）」）:

| 移動シンボル | bridging test（既存） | 検証内容 |
|---|---|---|
| `_atomic_write` → `fsio.atomic_write` | `tests/test_providers_local.py:90` | 書き込み結果の同一性 |
| `_atomic_write_new` → `fsio.atomic_write_new` | `tests/test_preflight.py:446,594,604` | `FileExistsError` 経路・short write 経路 |
| `_detect_legacy_forge_cache` → `providers.cache_guard.detect_legacy_forge_cache` | `tests/test_legacy_forge_cache_detection.py`（関数直呼び 3 + provider 経路 3 + CLI dispatch 2） | `SyncError` raise 条件と CLI exit code 翻訳 |
| `_format_issue_ref` → `providers.context.format_issue_ref` | `tests/test_state_persistence.py` | progress.md の issue ref 描画 |
| `resolve_main_worktree` facade 化 | `tests/test_resolve_main_worktree.py` / `tests/test_artifacts_dir.py` | main worktree 解決と artifacts path |
| `_mappings` facade 化 | `tests/test_worktree_discovery.py` / `tests/test_worktree_prefix.py` | branch prefix 解決 |

これらのテストは import 元の変更に伴い**機械的修正**（import 行・patch target 名）を受けるが、**アサーションは一切変更しない**。アサーションを変更しないことが、振る舞い非変更の証跡になる。

### Large テスト

**新規追加なし**。理由:

- CLI 契約（引数・出力・exit code）は不変で、外部 API 疎通経路（`gh` CLI 呼び出し等）のコードには触れない
- 既存の `large_local` テスト群（`tests/test_verdict_artifact_e2e_large_local.py` 等）が E2E 回帰網として存在し、`make check` の `pytest`（marker フィルタ無し = 全件）で実行される
- 新規 Large を足しても回帰検出情報が増えない（`docs/dev/testing-convention.md` 4 条件のうち 2・3 に該当）

### 回帰判定

`make check` を実行し、**ベースライン 2345 passed / 1 skipped に対して新規 FAILED / ERROR が 0** であること。加えて fitness test が「禁止 0 件」を報告すること。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | **あり** | ADR 009 を新設。「モジュール境界と private import 規約」（層の依存方向・3 分類 + 時限除外・facade 経由公開・fitness test による強制）は内部構造に関する恒久的意思決定であり、今後の module 追加すべてを拘束する |
| `docs/ARCHITECTURE.md` | **あり** | § パッケージ構成に `fsio.py` / `providers/cache_guard.py` を追記し、層の依存方向（下位層は上位層へ依存しない）を明記。現状の tree は `providers/` / `commands/` を欠いており、本 Issue の範囲で触れる module に限って更新する |
| `docs/reference/python/python-style.md` | **あり** | private import 規約は「コードを書く前にロードする規約」（AGENTS.md）に属するため、ADR 009 への参照を 1 節追加。規則本文は ADR に置き二重管理しない |
| `docs/dev/` | なし | ワークフロー・開発手順に変更なし |
| `docs/cli-guides/` | なし | CLI 仕様は不変（`pyproject.toml:41-42` の entry point 契約を維持） |
| `AGENTS.md` / `CLAUDE.md` | なし | 規約の追加先は `docs/reference/python/` 配下であり、索引の変更は不要 |
| `CHANGELOG.md`（BREAKING） | なし | 公開契約は `kaji` CLI entry point のみ。`kaji_harness.*` の import path は published API ではないため、ADR 008 決定 2 の BREAKING エントリ要件に該当しない |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| PEP 8 — Naming Conventions | https://peps.python.org/pep-0008/#descriptive-naming-styles | 引用: "`_single_leading_underscore`: weak 'internal use' indicator. E.g. `from M import *` does not import objects whose names start with an underscore." → `_` は**弱い**内部利用指標にすぎず、機械的な禁止ではない。「内部」の範囲を決めるのは凝集境界であるという分類ルール（同一 package 内は許容）の直接的裏付け |
| Python Language Reference — Package Relative Imports | https://docs.python.org/3/reference/import.html#package-relative-imports | 相対 import は `level` 個の親 package を遡って解決される。要約: `from ..state import X` は「現 module の package の親」を基点に解決する。検証器が `ImportFrom.level` から絶対 module 名を復元するアルゴリズム（`base := ancestor(pkg(M), level-1)`）の仕様根拠 |
| Python Tutorial — Packages / `__init__.py` | https://docs.python.org/3/tutorial/modules.html#packages | `__init__.py` は package の初期化と公開名の集約点。facade（`__all__` に載せた名前だけを package 外へ公開し、`_*.py` は実装詳細に留める）という設計の根拠 |
| unittest.mock — Where to patch | https://docs.python.org/3/library/unittest/mock.html#where-to-patch | 要約: patch は「オブジェクトが定義された場所」ではなく「**参照されている場所（look up される名前空間）**」を差し替える。`from ..fsio import atomic_write_new` で `providers/local.py` の名前空間に名前が束縛されるため、既存の `patch.object(_local_mod, ...)` が移動後も機能する（= `import fsio` 形式にしてはならない）という制約の根拠 |
| Python Standard Library — `ast` | https://docs.python.org/3/library/ast.html | `ast.ImportFrom(module, names, level)` / `ast.Import(names)` のノード仕様。棚卸しと検証器の実装根拠 |
| repo: `kaji_harness/providers/context.py:107-113` | `kaji_harness/providers/context.py` | 引用: 「`kaji_harness.state._format_issue_ref` と同じロジック。本 module は provider package として state.py に依存させたくないため独立に持つ。**将来の統合時は本実装を正本にする**」→ F2/F3 で context 側を正本化する判断は、既存コードが明示した意図の実行 |
| repo: `kaji_harness/cli_main.py:1-5` | `kaji_harness/cli_main.py` | 引用: 「kaji CLI 互換 shim。…**最終削除は #284**」→ 時限除外（第 4 分類）の根拠 |
| repo: `kaji_harness/state.py:56,70` | `kaji_harness/state.py` | `issue_number: str` と `__post_init__` での `str(...)` 正規化 → `format_issue_ref(issue_id: str)` への signature 集約が安全である根拠 |
| repo: `pyproject.toml:41-42` | `pyproject.toml` | `[project.scripts] kaji = "kaji_harness.cli_main:main"` → 公開契約は CLI entry point のみ。内部 import path 変更が BREAKING に当たらない根拠 |
| repo: `docs/adr/008-no-backward-compat-layer.md` | `docs/adr/008-no-backward-compat-layer.md` | 決定 1「後方互換レイヤを書かない」→ `sync.SyncError` の互換 alias を残さず呼び出し側を移行する判断の根拠。決定 2 は「破壊的変更の CHANGELOG 明示」を要求するが、対象は公開契約であり本件は該当しない |
| repo: `docs/dev/testing-convention.md` | `docs/dev/testing-convention.md` | 実行時コード変更では S/M/L の観点を定義し、恒久テスト不追加は 4 条件で正当化する。Medium / Large を新規追加しない理由の根拠 |
| repo: `.claude/skills/_shared/design-by-type/refactor.md` | `.claude/skills/_shared/design-by-type/refactor.md` | § 7「既存テストで十分な場合は新規不要（エビデンスを書く）」/ § 3「ベースライン計測」→ bridging test をエビデンス付きで既存テストに委ねる判断とベースライン節の構成根拠 |
| repo: `Makefile:6` | `Makefile` | `check: lint format typecheck test` → fitness test を `pytest` に置けば `make check` が自動でゲートになる（新規 Makefile ターゲット不要）根拠 |
