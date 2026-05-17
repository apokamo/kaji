# [設計] worktree 間で provider overlay (`.kaji/config.local.toml`) が継承されず provider 解決が沈黙でズレる問題への WARN + docs 対応

Issue: gl:28

## 概要

`git worktree add` で作った新規 worktree には gitignored な provider overlay
(`.kaji/config.local.toml`) が存在せず、`kaji issue` / `kaji pr` / `kaji run` の
provider 解決が tracked `.kaji/config.toml` の `[provider] type` に**沈黙で**
フォールバックする。本修正は、その沈黙のズレを検出して stderr に WARN を出し、
worktree 運用 docs に注意書きを加える（Issue 修正方針の **案 D**）。

## 背景・目的

### Observed Behavior（OB）

main worktree が「tracked `config.toml` = `type=github`」「overlay `config.local.toml`
= `type=gitlab`」の hybrid 構成のとき、`git worktree add` で作った feature worktree
から provider 解決を伴うコマンドを叩くと、overlay が無いため tracked の `github` に
解決される。Issue gl:28 の OB（実発生ログ）:

```
$ kaji pr create ...
HTTP 403: Sorry. Your account was suspended (https://api.github.com/graphql)
```

| 観測 | 値 |
|------|-----|
| main worktree `kaji config provider-type` | `gitlab`（overlay が効く） |
| 新規 worktree `kaji config provider-type` | `github`（overlay 不在 → tracked 値） |
| 新規 worktree の `.kaji/` 中身 | `config.toml` / `issues/` / `wf/` のみ。`config.local.toml` 不在 |
| 結果 | `kaji pr` が `gh pr create` 経路へ routing され、suspended な GitHub アカウントで 403 |

このとき **WARN 等の気付き手段が一切無い**ことが本 Issue の核心である。403 で
止まったのは結果的に発覚しただけで、構造的には「意図しない repo / forge へ沈黙で
routing される」リスクがある。

### Expected Behavior（EB）

`git worktree add` で作った feature worktree 内でも、provider 解決が main worktree
と食い違う場合は **明示的な気付き（WARN）とガイダンス**が出るべき。Issue gl:28
「修正方針」では 3 つの成立条件が挙げられ、本設計は **(c) WARN + docs 加筆（案 D）**
を採用する:

- (c) overlay 不在で provider 解決が tracked default にフォールバックし、かつ
  main worktree には overlay が存在して provider 解決が食い違う場合に、stderr に
  WARN を出して気付かせる
- 併せて `docs/guides/git-worktree.md` / `docs/cli-guides/local-mode.md` に
  「新規 worktree に overlay は引き継がれない」「コピー or `kaji local init`
  再実行が必要」を明文化する

採用方針の根拠（Issue gl:28「修正方針（議論ポイント）」の整理に同意）:

- **案 A（cascading 探索）不採用**: 「per-worktree で異なる provider」（例: 同一
  bare repo で github / gitlab を両方試す）という正当なユースケースを潰す。
- **案 C（`kaji worktree add` ラッパー）不採用**: `git worktree add` を直接呼ぶ
  既存 skill / runner まで改修が波及し、bug 修正のスコープを大きく超える。
- **案 B（`kaji local init --inherit` 等のシードコマンド）は別 Issue**: 新規 CLI
  サブコマンド／フラグの追加であり、`type:bug` Issue に feature を混在させない
  （`_shared/design-by-type/bug.md` §7「リファクタ／feature 混在は避ける」）。
  中長期の改善として **follow-up Issue を別途起票することを推奨**する。
- **案 D 採用**: 実装変更が最小侵襲で、「per-worktree で違う provider を使う」
  正当ケースを壊さず、沈黙の routing ミスに気付き手段を与えられる。

### 再現手順（Steps to Reproduce）

最小再現環境（Issue gl:28「再現手順」と整合）:

1. 前提: main worktree（`default_branch` を checkout）の `.kaji/config.toml`
   （tracked）に `[provider] type = "github"`、`.kaji/config.local.toml`
   （overlay, gitignored）に `[provider] type = "gitlab"` を設定済み。
2. `git worktree add <new-worktree> -b <branch>` で新規 feature worktree を作成。
3. `cd <new-worktree> && kaji config provider-type` を実行。
4. 観測: main worktree では `gitlab`、新規 worktree では tracked `config.toml`
   の値（`github`）が返る。両者の食い違いに対して WARN は一切出ない。
5. 同 worktree で `kaji pr create ...` 等を実行すると、意図と異なる provider
   （`github`）へ沈黙で routing される（OB の 403 に至る）。

### Root Cause（根本原因）

`kaji_harness/config.py` の `KajiConfig._parse_provider` は overlay を
**そのコマンドを実行している worktree の `.kaji/` 直下のみ**から探す:

```python
# kaji_harness/config.py:201
local_overlay_path = path.parent / "config.local.toml"
```

`path` は `KajiConfig.discover()` が cwd 起点で walk-up して見つけた
`.kaji/config.toml` のパスである。feature worktree の cwd から discover すると
`path` は feature worktree の `.kaji/config.toml` になり、overlay 探索もその
worktree 内に閉じる（cwd-local resolution）。

`.kaji/config.local.toml` は `.gitignore` で ignore されている。`git worktree add`
は**コミット済み（tracked）ファイルだけを checkout** し、ignored / untracked
ファイルは新規 worktree に存在しない（git のトラッキングモデル上の標準挙動）。
したがって overlay は新規 worktree に物理的に存在せず、`_parse_provider` は
tracked 値だけで provider を確定する。

**いつから壊れているか**: overlay 機構（`config.local.toml`）と worktree 運用は
それぞれ独立に導入された機能であり、両者の交差点（worktree 切替時の overlay
非継承）は当初から考慮されていない。特定のリグレッション commit ではなく、
2 機能の組み合わせで初めて顕在化した構造的欠落である。

**同根の他の壊れ箇所の調査結果**:

- LocalProvider の `.kaji/issues/` 書き込み・`git commit` も同種の cwd-local
  resolution で feature worktree に向かう問題があったが、これは Issue gl:11 で
  `resolve_main_worktree()`（`kaji_harness/providers/_worktree.py`）を導入して
  既に修正済み。本 Issue はその修正対象外だった「overlay の provider 解決」が
  残課題として残っていたもの。
- `config.local.toml` を読む箇所は `_parse_provider`（overlay 解決）のみ。
  `kaji_harness/local_init.py` は overlay を**書く**側で、`kaji local init` 実行
  worktree の `.kaji/` に生成する（cwd-local だが、これは仕様通りの挙動）。
  provider 解決経路で overlay を読む他の箇所は無く、修正対象は `_parse_provider`
  の解決結果に対する WARN 1 点に集約できる。

## インターフェース

bug 修正のため公開インターフェース（CLI 引数、設定ファイル書式、provider 解決の
正常系挙動）は**一切変更しない**。追加されるのは「沈黙だった経路への stderr WARN」
と「内部 API の 1 フィールド・1 関数」のみ。

### 入力

- `.kaji/config.toml`（tracked）/ `.kaji/config.local.toml`（overlay）: 書式・
  解釈ともに不変。
- 環境: git worktree 上で実行されていること（非 git / worktree 未構成でも WARN
  を出さず正常動作する。後述「制約・前提条件」参照）。

### 出力

#### 1. provider 解決のズレ検出時の stderr WARN（新規・主たる成果物）

overlay 不在 worktree から `kaji issue` / `kaji pr` / `kaji run` を実行し、かつ
main worktree の overlay が tracked と異なる provider type を選んでいる場合のみ、
dispatch 前に stderr へ 1 回 WARN を出す。WARN 文言の要件:

- 現 worktree で解決された `provider.type`（tracked 由来）と、main worktree の
  overlay が選ぶ `provider.type` の**両方の値**を含める。
- 復旧手順（main worktree から `.kaji/config.local.toml` をコピー、または当該
  worktree で `kaji local init` を実行）を案内する。
- exit code は変えない（WARN のみ。コマンド自体は従来どおり続行）。

> **規約**: WARN 文言に GitLab auto-close hazard pattern（`Clos*` / `Fix*` /
> `Resolv*` / `Implement*` の直後 `#` + 数字）を含めない。`docs/dev/shared_skill_rules.md`
> § GitLab auto close keyword 回避 準拠。

#### 2. 内部 API の追加（実装の足場）

- `KajiConfig` に bool フィールドを 1 つ追加（例: `provider_overlay_present`）。
  現 worktree の `.kaji/` に overlay ファイルが存在したかを記録する。
  `@dataclass(frozen=True)` に**デフォルト値付き**で追加するため、既存の
  `KajiConfig(...)` 直接構築 callsite（テスト含む）は後方互換。
- ズレ検出関数を 1 つ追加（例: `provider_overlay_divergence_warning(config) -> str | None`）。
  WARN 文言を返すか、ズレが無ければ `None` を返す純粋寄りの関数。配置は
  `resolve_main_worktree` を import 済みの `kaji_harness/providers/__init__.py`
  近傍を想定（実装時に決定）。

### 使用例

ユーザーが新規に書くコードは無い（CLI 挙動の追加のみ）。観測される挙動例:

```console
# overlay を持つ main worktree から（従来どおり、WARN 無し）
$ cd /home/aki/dev/kaji/main && kaji pr list
...

# overlay 不在の feature worktree から（新規 WARN）
$ cd /home/aki/dev/kaji/kaji-fix-28 && kaji pr create ...
WARNING: this worktree has no .kaji/config.local.toml overlay; provider.type
resolved to 'github' from tracked .kaji/config.toml, but your main worktree's
overlay selects 'gitlab'. Copy .kaji/config.local.toml from the main worktree
or run 'kaji local init' here. See docs/guides/git-worktree.md.
...（コマンド自体は従来どおり続行）
```

## 制約・前提条件

- **正常系挙動の不変性**: provider 解決の結果（どの provider に routing するか）
  は本修正で変えない。WARN を出すだけで、フォールバック先は従来どおり tracked 値。
- **best-effort 検出**: ズレ検出は main worktree の特定に `resolve_main_worktree()`
  （`git worktree list --porcelain` の subprocess）を使う。git CLI 不在 / 非 git
  ディレクトリ / main worktree（`default_branch` checkout）未構成などで失敗した
  場合は WARN を出さず無音で続行する（`LocalProviderError` を握って `None`)。
  検出失敗を fail にすると、git を持たない環境でコマンド全体が壊れる。
- **subprocess コストの局所化**: WARN 検出は「現 worktree に overlay が無い」
  場合のみ `resolve_main_worktree()` を呼ぶ。overlay を持つ main worktree（最も
  一般的な実行コンテキスト）では subprocess を一切起動しない。
- **検出スコープは `provider.type` のズレに限定**: main worktree overlay が
  `type` を上書きせず `[provider.github] repo` 等のみ上書きするケースは本 WARN の
  対象外（Issue gl:28 の OB は type のズレ＝forge 取り違えに起因するため）。
- **per-worktree で意図的に異なる provider を使う運用を壊さない**: 当該 worktree
  自身に overlay があれば（`provider_overlay_present == True`）WARN は出さない。
- **依存追加なし**: 既存の `tomllib` / `subprocess` / `resolve_main_worktree` の
  範囲で実装する。

## 方針

### 1. `config.py`: overlay 存在フラグの記録

`KajiConfig._parse_provider`（`config.py:176-326`）は既に
`local_overlay_path.is_file()` で overlay の有無を判定している。この判定結果を
呼出元 `_load` へ伝え、`KajiConfig` の新フィールド `provider_overlay_present` に
格納する（`_parse_provider` の戻り値を `(ProviderConfig | None, bool)` に拡張、
等。詳細は実装時に決定）。

### 2. ズレ検出関数

```text
provider_overlay_divergence_warning(config) -> str | None:
    if config.provider is None: return None
    if config.provider_overlay_present: return None      # この worktree 自身に overlay あり
    default_branch = getattr(config.provider, config.provider.type).default_branch
    try:
        main = resolve_main_worktree(start_dir=config.repo_root,
                                     default_branch=default_branch)
    except LocalProviderError:
        return None                                      # 非 git / git 不在 / main 未構成
    if main == config.repo_root: return None             # 自分が main worktree
    main_overlay = main / ".kaji" / "config.local.toml"
    if not main_overlay.is_file(): return None           # main にも overlay 無し → ズレ無し
    main_type = parse [provider].type from main_overlay  # tomllib, 失敗時 None
    if main_type is None or main_type == config.provider.type:
        return None                                      # type 上書き無し or 一致 → ズレ無し
    return "<WARN 文言: current type / main type / 復旧手順>"
```

`getattr(config.provider, config.provider.type)` は `type ∈ {github, local, gitlab}`
が `ProviderConfig` の同名属性（`.github` / `.local` / `.gitlab`）と一致するため
成立し、いずれも `.default_branch` を持つ。

### 3. `cli_main.py`: WARN の発火点

provider 解決を伴う dispatch 経路で、config discover 直後・`get_provider()` 前後に
上記関数を呼び、戻り値が非 `None` なら `sys.stderr.write(...)` する。

- `kaji issue` / `kaji pr`: 両者が共有する `_load_config_for_dispatch()`
  （`cli_main.py:779`）が単一の差し込み点になる。
- `kaji run`: `_handle_run` 系の `KajiConfig.discover()` 呼出（`cli_main.py:330`
  近傍）直後に同関数を呼ぶ。

1 コマンド実行につき WARN は最大 1 回。exit code・標準出力には影響しない。

### 4. docs 加筆

- `docs/guides/git-worktree.md`「kaji プロジェクトでの運用」に節を追加し、
  「`git worktree add` は gitignored な `.kaji/config.local.toml` を新規 worktree
  にコピーしない」「provider overlay を使う場合は新規 worktree でコピー or
  `kaji local init` 再実行が必要」「ズレ時は WARN が出る」を明記する。
- `docs/cli-guides/local-mode.md` § 3「provider 切替」の状態テーブル付近に
  「overlay は worktree per-instance であり、新規 worktree には引き継がれない」
  旨と WARN の存在を追記する。

## テスト戦略

> **CRITICAL**: 変更タイプに応じた検証方針を定義する。詳細は
> [テスト規約](../../docs/dev/testing-convention.md) 参照。

### 変更タイプ

実行時コード変更（provider 解決経路に WARN 出力ロジックを追加）+ docs-only 加筆。
コード変更分には恒久回帰テストが原則必要。docs 加筆分は `make verify-docs` で
リンク整合のみ確認し恒久テストは追加しない。

bug 固有ルール（`_shared/design-by-type/bug.md` §8）に従い、**修正前 Red →
修正後 Green の再現テストを 1 本以上**定義する。

### Small テスト

`provider_overlay_divergence_warning` の分岐ロジックを、`resolve_main_worktree`
を mock（または引数注入の seam 経由で差し替え）して検証する。検証観点:

- `config.provider is None` → `None`（WARN 無し）。
- 現 worktree に overlay あり（`provider_overlay_present=True`）→ `None`。
- `resolve_main_worktree` が `LocalProviderError` を送出 → `None`（best-effort
  で握り潰し、コマンドを壊さない）。
- 解決された main worktree が現 worktree と同一 → `None`（自分が main）。
- main worktree に overlay 無し → `None`（ズレ無し）。
- main overlay が `[provider].type` を持たない → `None`。
- main overlay の `type` が現解決 `type` と一致 → `None`。
- main overlay の `type` が現解決 `type` と相違 → 非 `None`。WARN 文言に両方の
  type 値と復旧手順が含まれ、auto-close hazard pattern を含まないこと。

### Medium テスト（再現テスト・必須）

実 git worktree を temp ディレクトリに構成し、Issue gl:28 の OB を再現する。
ファイル I/O と `git` subprocess を伴うため Medium。

- セットアップ: temp dir で `git init`（`default_branch=main`）→ 初回コミット →
  `.kaji/config.toml`（tracked, `type=github`）と `.kaji/config.local.toml`
  （overlay, `type=gitlab`、`.gitignore` で ignore）を main worktree に配置 →
  `git worktree add` で feature worktree を作成（overlay は tracked でないため
  feature worktree に存在しない）。
- 検証 1（overlay 存在フラグ）: feature worktree 起点で `KajiConfig.discover()`
  → `provider_overlay_present` が `False`、main worktree 起点では `True`。
- 検証 2（再現テスト本体）: feature worktree の config に対し
  `provider_overlay_divergence_warning` が `gitlab`（main overlay）/ `github`
  （現解決）双方を含む WARN を返す。**修正前は WARN ロジックが存在せず本 assert
  が FAIL、修正後に PASS** へ遷移することを再現テストの検証とする。
- 検証 3（誤検出しないこと）: main worktree 起点では WARN が `None`。

`subprocess.run` の名前空間 patch は使わず、`git init` fixture で実 git repo を
作る方式とする（`docs/dev/testing-convention.md` §`subprocess.run` patch スコープ
の「系統 A」に整合）。

### Large テスト

不要。理由（`docs/dev/testing-convention.md` の正当化基準に沿う）:

- 本修正は stderr への WARN 文字列出力のみで、GitHub / GitLab の実 API 疎通や
  E2E データフローを新たに伴わない。provider routing の実 API 挙動自体は本修正の
  変更対象外（routing 先は不変）。
- 実 forge 疎通を要する観点が無いため、Large を追加しても新規の回帰検出情報は
  増えない。

### docs 加筆分

- 変更固有検証: `make verify-docs`（リンク・参照整合）。
- 恒久テストを追加しない理由: docs 加筆は独自ロジックを含まず（4 条件 1）、
  リンク切れは `make verify-docs` で捕捉でき（4 条件 2）、恒久テスト化しても
  回帰検出情報が増えず（4 条件 3）、本節に理由を明示している（4 条件 4）。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新規アーキテクチャ決定は無い。既存の overlay 機構・`resolve_main_worktree` の範囲内 |
| docs/ARCHITECTURE.md | なし | モジュール構成・責務分担に変更なし |
| docs/dev/ | なし | ワークフロー・開発手順に変更なし |
| docs/reference/ | なし | API 仕様・コーディング規約に変更なし |
| docs/cli-guides/local-mode.md | **あり** | § 3「provider 切替」に「overlay は worktree per-instance／新規 worktree に非継承」と WARN の存在を追記 |
| docs/guides/git-worktree.md | **あり** | 「kaji プロジェクトでの運用」に worktree 作成後の overlay 取り扱い節を追加 |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| `kaji_harness/config.py`（本リポジトリ） | `kaji_harness/config.py:200-216` | `local_overlay_path = path.parent / "config.local.toml"`。overlay 探索が discover で見つけた `.kaji/` 直下に閉じる（cwd-local resolution）ことを示す根本原因の一次根拠 |
| `kaji_harness/providers/_worktree.py`（本リポジトリ） | `kaji_harness/providers/_worktree.py:41-96` | `resolve_main_worktree()` が `git worktree list --porcelain` を解析し `default_branch` checkout の worktree 絶対パスを返す。main worktree 特定に再利用する。git CLI 不在・非 git・main 未構成時は `LocalProviderError` を送出する（best-effort で握る根拠） |
| `kaji_harness/providers/__init__.py`（本リポジトリ） | `kaji_harness/providers/__init__.py:67-135` | `get_provider()` は Phase 3-e で fallback / WARN を撤去済み。`config.provider is None` で `ValueError`。現状 provider 解決経路に「overlay 不在の沈黙フォールバック」を検出する WARN が無いことの一次根拠 |
| `docs/cli-guides/local-mode.md`（本リポジトリ） | `docs/cli-guides/local-mode.md:88-98` | § 3「provider 切替」状態テーブル。「overlay なし → tracked の `[provider]` がそのまま使われる」と記載され、**worktree 切替時に overlay が非継承になる旨は未記載**。docs 加筆対象の一次根拠 |
| `docs/guides/git-worktree.md`（本リポジトリ） | `docs/guides/git-worktree.md:126-181`（「kaji プロジェクトでの運用」） | worktree 運用ガイド。`.venv` symlink 共有の注意はあるが、provider overlay の worktree 間配布についての記載が無い。docs 加筆対象の一次根拠 |
| Git 公式 `git-worktree` マニュアル | https://git-scm.com/docs/git-worktree | `git worktree add <path> <commit-ish>` は指定 commit-ish を新規 worktree に checkout する。checkout はコミット済み（tracked）内容を対象とし、ignored / untracked ファイルは新規 worktree に存在しない。`.gitignore` 管理の `config.local.toml` が新規 worktree にコピーされない挙動の一次根拠 |
| Git 公式 `gitignore` マニュアル | https://git-scm.com/docs/gitignore | ignore されたファイルは git のトラッキング対象外。commit に含まれないため `git worktree add` の checkout 対象にもならない。上記 worktree 挙動の補強根拠 |
| Issue gl:27 コメント | https://gitlab.com/apokamo/kaji/-/work_items/27#note_3350167365 | 本問題が顕在化した契機（MR !13 作成時に踏んだ）。OB の発生文脈の一次記録 |
