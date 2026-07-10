# [設計] issue-start skill の branch prefix を helper CLI 経由で context 正本と同期する

Issue: local-pc5090-17

## 概要

`/issue-start` スキルが固定 default `feat` で worktree / branch を作る現状を改め、
`kaji issue context <id> --json branch_prefix,branch_name,worktree_dir,branch_prefix_fallback`
helper CLI を新設し、skill はその出力を読んで worktree / branch を作成する。
明示 `prefix` 引数は廃止し、context 解決の正本（`provider.resolve_issue_context()`）
に一本化する。

## 背景・目的

### Observed Behavior (OB)

`type:bug` ラベル付き Issue `local-pc5090-14` を `/issue-start <id>`（prefix 引数なし）
で起動すると、

- skill が `feat/local-pc5090-14` worktree / `feat/local-pc5090-14` branch を作成
- 一方で `kaji run` が prompt に注入する context 変数は `branch_name=fix/local-pc5090-14` /
  `worktree_dir=.../kaji-fix-local-pc5090-14`（`type:bug` → `fix` の label mapping 由来）

両者が乖離した状態で workflow が進み、`.kaji-artifacts/local-pc5090-14/runs/2605091631/close/console.log:3-4`
で agent が以下のように fallback を強いられた:

```
Issue 本文では `feat/local-pc5090-14` ブランチ・worktree が記載されていますが、
コンテキスト変数では `fix/local-pc5090-14` となっています。実際の worktree 状態を
確認します。
```

同 run 内の verify-code / design / final-check の各 skill console.log にも同種の
「context 変数を無視して実態に合わせる」判断が連鎖記録されている。

### Expected Behavior (EB)

`/issue-start <issue_id>` 実行時:

1. `kaji issue context` helper CLI 経由で `provider.resolve_issue_context()` の結果を取得する。
   解決規則は provider 正本に従う:
   - frontmatter `branch_prefix` 優先（`kaji_harness/providers/local.py:680-683`）
   - 不在時は `labels_to_branch_prefix()` で `type:*` ラベルから導出
     （`kaji_harness/providers/_mappings.py:31-42`）
   - `type:*` 不在時は `DEFAULT_BRANCH_PREFIX = "chore"` に fallback
2. skill は helper CLI が返した `branch_prefix` / `branch_name` / `worktree_dir` を
   そのまま使い、`git worktree add` と Issue 本文 NOTE ブロックに反映する
3. workflow 起動時に `prompt.py` 経由で注入される context 変数（`branch_name` /
   `worktree_dir`）は同じ provider が同じ Issue から再解決するため、
   skill 作業実態と完全に一致する
4. 明示 `prefix` 引数は廃止する（label / frontmatter 一本化）

### 根拠（Primary Sources）

| 情報源 | 根拠 |
|--------|------|
| `kaji_harness/providers/_mappings.py:16-25` | `LABEL_TO_PREFIX` が prefix 解決の正本辞書（`type:bug` → `fix` 等） |
| `kaji_harness/providers/local.py:667-704` | `LocalProvider.resolve_issue_context()` の優先順位（frontmatter > label > fallback） |
| `kaji_harness/providers/base.py:81-88` | `IssueProvider.resolve_issue_context` Protocol（github / local / gitlab 共通） |
| `kaji_harness/providers/context.py:22, 35-43` | `validate_branch_prefix()` の許可値域 = `LABEL_TO_PREFIX.values()` |
| `kaji_harness/providers/models.py:64-103` | `IssueContext` dataclass のフィールド一覧 |
| `kaji_harness/cli_main.py:735-771` | `kaji issue` dispatcher（local: 構造化 CRUD、github: `gh issue` passthrough） |
| `kaji_harness/cli_main.py:936-977` | `_LOCAL_ISSUE_SUBS` と local subcommand 分岐 |
| `.claude/skills/issue-start/SKILL.md:22-49` | 現行 skill の prefix=feat 固定挙動 |
| `.kaji-artifacts/local-pc5090-14/runs/2605091631/close/console.log:3-4` | 実害ログ（agent fallback の記録） |
| `.claude/skills/issue-close/SKILL.md:333` | `git merge --no-ff --no-edit [branch_name]` 直接埋め込み箇所 |

## 再現手順

最小再現環境で OB を再現する手順:

1. **前提**: provider.type = `local`、`type:bug` label が付いた Issue を作成
   ```bash
   kaji issue create --title "repro" --label "type:bug" --body "x"
   # → local-<machine>-<N> が出力される（以降 <id>）
   ```
2. **実行**: prefix 引数なしで issue-start
   ```bash
   /issue-start <id>
   ```
3. **観測 1**: `feat/<id>` worktree が作られる（label と矛盾）
   ```bash
   git worktree list | grep "<id>"
   # → kaji-feat-<id>  feat/<id>
   ```
4. **観測 2**: workflow を起動して context 変数を確認
   ```bash
   kaji run .kaji/wf/feature-development-local.yaml <id>
   ```
   各 skill prompt の `branch_name` / `worktree_dir` が `fix/<id>` /
   `kaji-fix-<id>` であるのに対し、実際の worktree は `feat/` / `kaji-feat-` で
   発火する（`close` skill の console.log に明示記録される）。

## 根本原因

skill 側の prefix default (`feat`) と provider 側の context 正本（label mapping +
frontmatter override）が**別経路で導出されているため**、両者の同期が構造的に取れない。

### 原因の詳細

- `.claude/skills/issue-start/SKILL.md:27-28` 現行記述:
  > `prefix` (任意): ブランチプレフィックス (デフォルト: feat)
  → label を一切参照しない固定値
- `kaji_harness/providers/local.py:680-689` context 正本:
  ```python
  prefix_value = meta.get("branch_prefix")
  if isinstance(prefix_value, str) and prefix_value:
      prefix = prefix_value
  else:
      label_names = [...]
      prefix, fallback = labels_to_branch_prefix(label_names)
      if fallback:
          prefix = DEFAULT_BRANCH_PREFIX
  ```
  → frontmatter > label > fallback の 3 段優先
- skill 側で同じ優先ロジックを再実装する案は却下（review-ready 0001 採用）:
  - frontmatter `branch_prefix` を **`.kaji/issues/<id>-<slug>/issue.md`** から
    読み出す parser を skill bash に書く必要があり、`_validate_issue_meta` 等の
    fail-fast チェックを skill 側に重複実装することになる
  - 今後 provider が増えた際の同期コストが線形で増える

### いつから壊れているか

skill `issue-start` の固定 prefix=feat は同 SKILL の最初期バージョンから存在
（`git log -p .claude/skills/issue-start/SKILL.md` で当初から `feat` 固定）。
一方 provider 側の label-driven context 解決は Phase 3（`_mappings.py` 導入）で
追加されたため、**Phase 3 完了時点から両経路が乖離した状態**となっている。
当時 `type:bug` 等の non-feat Issue が稀だったため顕在化が遅れた。

### 同根の他箇所

context 変数を bash コマンドに直接埋め込んでいる skill 箇所（agent の fallback
判断が効かず実害が出る順に列挙）:

- `.claude/skills/issue-close/SKILL.md:333` `git merge --no-ff --no-edit [branch_name]`
- `.claude/skills/issue-close/SKILL.md:357-358` `git worktree remove [worktree_dir]` / `git branch -d [branch_name]`
- `.claude/skills/issue-close/SKILL.md:134-146` branch 存在確認 + remote 削除
- `.claude/skills/i-pr/SKILL.md` ほか worktree_dir 直接利用箇所多数（cd `[worktree_dir]`）

これらは context 変数が正しく解決される前提であり、本 Issue の根本修正
（skill 作業実態と context 正本を同期させる）により全て恒久的に救われる。
**個別 skill の修正は本 Issue では不要**（前提が満たされれば既に正しい）。

## インターフェース

### 新設 helper CLI: `kaji issue context`

**コマンド形**:

```
kaji issue context <issue_id> [--json FIELDS] [--jq EXPR | -q EXPR]
```

| 引数 | 必須 | 説明 |
|------|------|------|
| `issue_id` | ✅ | `provider.type='local'` 配下では `153` / `pc1-3` / `local-pc1-3` / `gh:N`。`provider.type='github'` 配下では `153` / `gh:153`。GitLab 経路は本 Issue の対象外（後述「対象 provider スコープ」§） |
| `--json FIELDS` | 任意 | 出力フィールドを CSV で限定（`kaji issue view --json` と同フォーマット） |
| `--jq EXPR` / `-q EXPR` | 任意 | 出力 JSON に jq 式適用（`kaji issue view -q` と同フォーマット） |

#### 対象 provider スコープ（GitLab 除外の明示）

本 Issue では `kaji issue context` の対象 provider を **`local` と `github` のみ** に限定する。GitLab は範囲外。

**根拠**:

- `kaji_harness/providers/__init__.py:185-186` の `normalize_id()` は `provider_name in {"github", "local"}` のみ受理し、`gitlab` を渡すと `ValueError("unknown provider: 'gitlab'")` を返す
- `kaji_harness/cli_main.py:735-771` の `_handle_issue` dispatcher も `LocalProvider` 分岐 + 残りは `gh issue` passthrough という二極構成で、GitLab 経路自体が `kaji issue` サブコマンドに存在しない
- 一方、`kaji_harness/providers/gitlab.py:391-409` には `GitLabProvider.resolve_issue_context()` が実装されているため、provider 側の API 単体では呼べる状態にある
- 本 Issue の根本目的は `/issue-start` skill と context 正本の同期であり、GitLab 対応を含めると `normalize_id()` 拡張 + dispatcher 拡張 + GitLab 用 ID 構文（`gl:N` 等）の正書化が必要となり、Issue のスコープを大きく超える

**実装方針**:

- `kaji issue context` は `provider.type='gitlab'` 配下では明示的に拒否する
- 拒否経路: dispatcher が GitLab provider を検出した時点で `EXIT_INVALID_INPUT` + 以下のメッセージ:
  ```
  Error: 'kaji issue context' is not supported under provider.type='gitlab'.
  GitLab support is tracked separately (normalize_id() and dispatcher require
  extension; see future issue). Use provider.type='local' or 'github'.
  ```
- 将来 GitLab 対応する際は、別 Issue で `normalize_id()` の `provider_name` 受理拡張 + dispatcher の GitLab 経路追加 + GitLab 用 ID 構文確定をまとめて行う

**出力フィールド**（`IssueContext` dataclass の全フィールド）:

| field | 型 | 例 |
|-------|----|------|
| `issue_id` | str | `local-pc1-3` |
| `issue_ref` | str | `local-pc1-3` |
| `issue_input` | str | `local-pc1-3` |
| `slug` | str | `issue-start-skill-prefix-type-label-bran` |
| `branch_prefix` | str | `fix` |
| `branch_name` | str | `fix/local-pc1-3` |
| `worktree_dir` | str | `/path/to/kaji-fix-local-pc1-3`（絶対パス） |
| `design_path` | str | `draft/design/issue-local-pc1-3-...md` |
| `provider_type` | str | `local` / `github` / `gitlab` |
| `branch_prefix_fallback` | bool | `type:*` 不在で `chore` fallback された場合 true |
| `default_branch` | str | `main` 等 |

**デフォルト出力**: `--json` 省略時は全フィールドを compact JSON で出力（newline 終端）。

**`--json` フィールドの未知 key 取扱い**:

- 未知 key（`IssueContext` に存在しないフィールド名）は **`null` を返す**（既存 `kaji issue view --json` と同挙動。`kaji_harness/cli_main.py:1012-1018` で `full.get(k)` を実装）
- 例: `kaji issue context <id> --json branch_prefix,nonexistent` →
  `{"branch_prefix": "fix", "nonexistent": null}` + exit 0
- これは **stricter エラーにせず、既存 `--json` 規約に揃える** 設計判断:
  - `_local_issue_view` と挙動を揃えることで skill / shell スクリプト側の jq 経由参照の挙動が統一される
  - 未知 key を `EXIT_INVALID_INPUT` に倒すと、`kaji issue view` との整合性が崩れ、ヘルプ・テスト期待値・skill 動線がぶれる
  - 未知 key の早期検出が必要なケースは `-q '.<key>'` 経由で `null` チェックすれば済む

**exit code**:

| code | 条件 |
|------|------|
| `0` | 成功（未知 `--json` フィールドは含まない） |
| `2` (`EXIT_INVALID_INPUT`) | 不正 `issue_id` 形式 / `provider.type='gitlab'` で未対応 / `argparse` レベルの引数エラー |
| `4` (`EXIT_RUNTIME_ERROR`) | Issue 不在 / frontmatter 不備等の解決時例外（`IssueContextResolutionError` 系） |

**使用例**:

```bash
# skill 側での想定使用
CTX=$(kaji issue context "$ISSUE_ID" --json branch_prefix,branch_name,worktree_dir)
PREFIX=$(echo "$CTX" | jq -r '.branch_prefix')
BRANCH=$(echo "$CTX" | jq -r '.branch_name')
WT=$(echo "$CTX" | jq -r '.worktree_dir')
git worktree add -b "$BRANCH" "$WT" "$DEFAULT_BRANCH"
```

または直接 jq:

```bash
PREFIX=$(kaji issue context "$ISSUE_ID" --json branch_prefix -q '.branch_prefix')
```

### dispatcher の特殊処理（github / gitlab 経路）

`_handle_issue` は `provider.type='github'` で `gh issue` passthrough する。
`gh issue context` は存在しないため、**`context` subcommand は passthrough しない**。
また `provider.type='gitlab'` 配下では `kaji issue context` を未対応として
明示拒否する:

```python
# _handle_issue の擬似コード（変更後）
if isinstance(provider, LocalProvider):
    return _handle_issue_local(provider, raw_args)
# context は github でも provider.resolve_issue_context() で解決
if raw_args and raw_args[0] == "context":
    if isinstance(provider, GitLabProvider):
        sys.stderr.write(
            "Error: 'kaji issue context' is not supported under "
            "provider.type='gitlab'. GitLab support requires "
            "normalize_id() and dispatcher extension (tracked separately). "
            "Use provider.type='local' or 'github'.\n"
        )
        return EXIT_INVALID_INPUT
    return _handle_issue_context(provider, raw_args[1:])
# 既存通り gh passthrough
forwarded = [a for a in raw_args if a != "--commit"]
return _forward_to_gh("issue", forwarded, repo=...)
```

`_handle_issue_local` 側は `_LOCAL_ISSUE_SUBS` に `"context"` を追加し、
`_local_issue_context` を呼ぶ。実装本体（`provider.resolve_issue_context` 呼び出し
+ JSON 整形）は `_handle_issue_context` ヘルパに集約し、local / github 両経路から
同じ関数を呼ぶ。

### `/issue-start` skill の引数仕様変更（breaking change）

**変更前**:

```
/issue-start <issue_id> [prefix]
```

**変更後**:

```
/issue-start <issue_id>
```

第 2 引数 `prefix` は受理しない（指定された場合は ABORT verdict + 廃止アナウンス）。
理由は「方針」セクション参照。

### 後方互換性

| ケース | 影響 | 根拠 |
|--------|------|------|
| `type:feature` ラベル + frontmatter override 無しで `/issue-start <id>` 実行 | **影響なし**。新ロジックでも `feat/<id>` に解決される | label mapping `type:feature` → `feat`（`_mappings.py:17`） |
| `type:bug` ラベル + frontmatter override 無し | **挙動変更**: `feat/` → `fix/`（バグ修正の意図通り） | OB の修正対象 |
| frontmatter `branch_prefix:` を明示している既存 Issue | `.kaji/issues/` 配下で 0 件のため影響対象なし | 確認: `awk '/^---$/{c++; next} c==1 && /^branch_prefix:/' .kaji/issues/*/issue.md` の出力 0 行 |
| `/issue-start <id> fix` のように第 2 引数を渡す既存ユーザ | **breaking**: ABORT verdict | A 案採用（review-ready 0001 採用） |
| 既存 `feat/<id>` 運用中 Issue（`type:feature` ラベル）の再起動 | worktree 既存エラーで fail-fast（既存挙動維持） | git worktree add の既存挙動 |

## 変更スコープ

### 主スコープ（実行時コード変更）

- `kaji_harness/cli_main.py`
  - `_LOCAL_ISSUE_SUBS` に `"context"` 追加
  - `_handle_issue` に context special-case 分岐追加（github は provider 経由処理、gitlab は明示拒否）
  - `_handle_issue_context(provider, rest)` 新設（local / github 共通）
  - `_local_issue_context(provider, rest)` 新設（dispatcher 互換のため、内部で `_handle_issue_context` 呼び出し）

### 副スコープ（docs / skill）

- `.claude/skills/issue-start/SKILL.md`
  - `prefix` 引数廃止、`kaji issue context` 経由のフロー記述に書き換え
  - 第 2 引数を渡された場合の ABORT 処理追加
- `tests/test_cli_main.py`（または相当箇所）
  - `kaji issue context` のユニットテスト追加

### 範囲外（明示）

- `.claude/skills/issue-close/SKILL.md` ほかの `[branch_name]` / `[worktree_dir]`
  直接埋め込み箇所の修正は不要。本 Issue の根本修正により context 変数自体が
  正しくなるため、これらは前提が満たされれば既に正しい
- frontmatter `branch_prefix` を CLI 経由で書き換える `kaji issue edit --branch-prefix`
  追加は本 Issue 範囲外（将来 label と異なる prefix が必要になった時点で別 Issue 化）

## 方針

### 主軸: helper CLI 経由で context 正本を skill から参照する

- skill bash 側に label→prefix 解決ロジックを **複製しない**
- `kaji issue context <id>` は `provider.resolve_issue_context()` を呼ぶ薄いラッパー
- 既存 `_handle_issue_local` の subcommand 分岐に `context` を追加し、
  `_LOCAL_ISSUE_SUBS` に登録
- github 経路は `gh issue context` が存在しないため、`_handle_issue` 側で
  passthrough 前に `context` を捕捉し、provider 経由で直接処理
- 出力は `dataclasses.asdict(IssueContext)` を json.dumps、`--json FIELDS` で
  キー絞り込み（`_local_issue_view` の `--json` 実装パターンを踏襲）

### 明示 prefix 引数の廃止（A 案、review-ready 0001 全件採用済）

廃止理由:

- frontmatter `branch_prefix` を context 正本に永続化する API（`kaji issue edit
  --branch-prefix`）が存在しない（`kaji_harness/cli_main.py:1099-1116` `_local_issue_edit`
  の argparse に `--branch-prefix` なし）
- skill 側だけで明示 prefix を処理しても、frontmatter / label が乖離源として残るため
  乖離を再生産する
- `.kaji/issues/` 配下で `branch_prefix` frontmatter を明示している Issue は 0 件
  （regression リスクなし）
- 将来 label と異なる prefix が必要になった場合は `kaji issue edit --branch-prefix`
  追加で柔軟性を取り戻せる（後方互換性は保たれる）

### 疑似コード（skill 側）

```bash
# Step 0: 引数検査
if [ -n "$ARG2" ]; then
    echo "ABORT: prefix argument is removed; prefix is now derived from type:* label / frontmatter branch_prefix"
    exit 1
fi

# Step 1: helper CLI で context 取得
CTX=$(kaji issue context "$ISSUE_ID" --json branch_prefix,branch_name,worktree_dir)
PREFIX=$(echo "$CTX" | jq -r '.branch_prefix')
BRANCH=$(echo "$CTX" | jq -r '.branch_name')
WT=$(echo "$CTX" | jq -r '.worktree_dir')

# Step 2: worktree / branch 作成
MAIN_REPO=$(git rev-parse --show-toplevel)
git worktree add -b "$BRANCH" "$WT" main
ln -s "$MAIN_REPO/.venv" "$WT/.venv"

# Step 3: Issue 本文 NOTE 追記（変数を埋め込み）
kaji issue edit "$ISSUE_ID" --commit --body "$(printf '> [!NOTE]\n> **Worktree**: `../%s`\n> **Branch**: `%s`\n\n%s' \
    "$(basename "$WT")" "$BRANCH" "$CURRENT_BODY")"
```

### 疑似コード（CLI 側）

```python
# kaji_harness/cli_main.py
_LOCAL_ISSUE_SUBS = {"view", "create", "edit", "comment", "close", "list", "context"}

def _handle_issue_context(provider: IssueProvider, rest: list[str]) -> int:
    p = argparse.ArgumentParser(prog="kaji issue context", add_help=True)
    p.add_argument("issue_id", type=str)
    p.add_argument("--json", dest="json_fields", default=None, type=str)
    p.add_argument("--jq", "-q", dest="jq_expr", default=None, type=str)
    ns = p.parse_args(rest)

    # provider 別に id 正規化（_resolve_local_id 等の既存ヘルパを再利用）
    rid = ...  # 既存の resolve ロジックに準拠
    ctx = provider.resolve_issue_context(rid.value)

    payload = dataclasses.asdict(ctx)
    if ns.json_fields:
        fields = [f.strip() for f in ns.json_fields.split(",") if f.strip()]
        payload = {k: payload.get(k) for k in fields}

    return _emit_json(payload, jq_expr=ns.jq_expr)
```

## テスト戦略

### 変更タイプ

実行時コード変更（CLI コマンド新設 + skill 動線変更）

### Small テスト

- `_handle_issue_context` の引数 parse（`--json` フィールド絞り込み、`-q` jq 適用）
  - 全フィールド出力 → `IssueContext` の全 key が JSON に含まれる
  - `--json branch_prefix,branch_name` → 2 key のみ出力
  - `-q '.branch_prefix'` → raw 値（quote なし）出力
- 不正 issue_id 形式 → `EXIT_INVALID_INPUT`
- 未知 `--json` フィールド名 → `null` を返す + exit 0（`_local_issue_view` の `full.get(k)` 挙動に揃える。インターフェース § で確定した契約）
- `provider.type='gitlab'` で `kaji issue context` 呼び出し → `EXIT_INVALID_INPUT` + 未対応メッセージ

### Medium テスト

- **再現テスト（bug 固有、必須）**: `type:bug` ラベル付き Issue に対し
  `kaji issue context <id> -q '.branch_prefix'` が `fix` を返すことを assert する。
  本テストは現行 main で既に PASS する（context 正本側は label-driven のため）が、
  skill との同期不全を検出するための恒久回帰として保持する
- **frontmatter override 優先順位**: frontmatter `branch_prefix: docs` + label
  `type:bug` の Issue で、`branch_prefix` が `docs` を返すこと
  （`hotfix` 等の non-allowed 値は `validate_branch_prefix()` で
  `resolve_issue_context()` 到達前に `ValueError` となるため使用不可。
  allowed 集合 = `LABEL_TO_PREFIX.values()` = `{feat,fix,refactor,docs,test,chore,perf,security}`）
- **fallback 動作**: `type:*` label 不在の Issue で `branch_prefix=chore`、
  `branch_prefix_fallback=true` を返すこと
- **全 8 種 type label**: `type:feature/bug/refactor/docs/test/chore/perf/security`
  各々で正しい prefix 解決を確認（parametrize）
- **github 経路**: `provider.type='github'` 設定で `kaji issue context` が
  passthrough せずに `provider.resolve_issue_context()` を呼ぶこと
  （`monkeypatch` で GithubProvider をスタブして label → prefix 経路を検証）

### Large テスト

不要。理由:

- 本変更は `LocalProvider.resolve_issue_context()` の既存ロジックを CLI として
  露出するだけで、外部 API 疎通や E2E データフロー上の新規結合点を持たない
- skill 側の動線変更は手動 e2e（後述「実装段階での確認」）でカバー

### 再現テスト（Red → Green 証跡）

- 修正前: `_LOCAL_ISSUE_SUBS` に `context` がないため `kaji issue context <id>`
  → `EXIT_INVALID_INPUT` + 「Supported: ...」エラー
- 修正後: 同コマンドで JSON 出力 + exit 0
- 再現テストはこの遷移を assert する形で書く

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| `docs/adr/` | なし | 既存設計（provider Protocol / IssueContext）の延長で技術選定の新規決定なし |
| `docs/ARCHITECTURE.md` | なし | アーキテクチャ上の新規モジュール / 新規境界なし |
| `docs/dev/` | あり | `development_workflow.md` / `workflow_guide.md` で `/issue-start` の引数記述があれば追従。`shared_skill_rules.md` の context 変数解決手順に helper CLI を追記検討 |
| `docs/reference/` | なし | python style / naming 等への影響なし |
| `docs/cli-guides/` | あり | `kaji issue context` を新規追加（既存の `kaji issue` ガイドに節追加） |
| `CLAUDE.md` | なし | 規約変更なし |
| `.claude/skills/issue-start/SKILL.md` | あり | 引数仕様 / 実行手順の書き換え（主スコープ） |
| `.claude/skills/_shared/worktree-resolve.md` | 要確認 | 「Worktree 情報を Issue 本文から取得」手順は維持で問題ないが、helper CLI も併記すれば skill 着手前の事前確認動線が増えて lint しやすくなる（任意） |
| `tests/test_cli_main.py` | あり | helper CLI のユニットテスト追加（小〜中） |

## 完了条件の段階別マッピング

Issue 本文「完了条件」セクションへの対応:

### 設計段階で確認

- [x] helper CLI `kaji issue context` の引数仕様・出力スキーマ確定 → 「インターフェース」§
- [x] 未知 `--json` フィールドの契約確定（`null` 返却 + exit 0、`kaji issue view` と同挙動） → 「インターフェース § `--json` フィールドの未知 key 取扱い」
- [x] 対象 provider スコープ確定（local / github のみ、gitlab は本 Issue 範囲外） → 「インターフェース § 対象 provider スコープ」
- [x] helper CLI が薄いラッパーで context 解決ロジックを重複実装しないこと → 「方針」§
- [x] 明示 prefix 引数の廃止が breaking change として後方互換セクションに明記 → 「インターフェース § 後方互換性」
- [x] 同根の他 skill 影響調査結果（context 変数を直接埋め込む箇所） → 「根本原因 § 同根の他箇所」

### 実装段階で確認（実装フェーズへの引き継ぎ）

- [ ] `kaji issue context <id> --json <fields>` CLI が `cli_main.py` に追加
- [ ] helper CLI のユニットテスト（`tests/test_cli_main.py`）
- [ ] `.claude/skills/issue-start/SKILL.md` の prefix 引数削除と helper CLI 動線への書き換え
- [ ] `type:bug` Issue で `/issue-start` → `fix/<id>` worktree が作られる手動確認
- [ ] context 変数と実態 worktree が一致することを workflow 1 サイクル実行で確認
- [ ] 全 8 種 type label の prefix 導出確認（parametrized test）
- [ ] type:* 不在時の fallback 一致確認
- [ ] frontmatter `branch_prefix` 優先動作確認（label `type:bug` + frontmatter `branch_prefix: docs` → `docs/<id>`）
- [ ] `/issue-start <id> fix` で第 2 引数指定時の ABORT 動作確認
- [ ] 既存 `feat/<id>` 運用中 Issue 再起動で fail-fast 維持確認
- [ ] 未知 `--json` フィールドが `null` + exit 0 を返すことの test
- [ ] `provider.type='gitlab'` で `kaji issue context` が `EXIT_INVALID_INPUT` + 専用エラーメッセージを返すことの test
- [ ] `make check` 通過

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| LABEL_TO_PREFIX 正本 | `kaji_harness/providers/_mappings.py:16-25` | `type:feature→feat / type:bug→fix / ...` の 8 種 mapping |
| `labels_to_branch_prefix()` | `kaji_harness/providers/_mappings.py:31-42` | `dict` 挿入順を優先順位とした tie-break、不在時は `(DEFAULT_BRANCH_PREFIX, True)` |
| LocalProvider context 解決 | `kaji_harness/providers/local.py:667-704` | frontmatter `branch_prefix` 優先、fallback 時は `DEFAULT_BRANCH_PREFIX` |
| IssueProvider Protocol | `kaji_harness/providers/base.py:81-88` | `resolve_issue_context(issue_id) -> IssueContext` を 3 provider 共通で要求 |
| `validate_branch_prefix()` | `kaji_harness/providers/context.py:22, 35-43` | allowed 値域 = `LABEL_TO_PREFIX.values()`。`hotfix` 等は不可 |
| `IssueContext` dataclass | `kaji_harness/providers/models.py:64-103` | `frozen=True`、11 フィールド、`branch_prefix_fallback: bool = False` 含む |
| `kaji issue` dispatcher | `kaji_harness/cli_main.py:735-771` | local: 構造化 CRUD / github: gh passthrough（context は special case 必要） |
| LocalProvider subcommand 分岐 | `kaji_harness/cli_main.py:936-977` | `_LOCAL_ISSUE_SUBS` セット + 6 分岐 |
| `_local_issue_view` の `--json` 実装 | `kaji_harness/cli_main.py:992-1025` | `--json FIELDS,...` のフィールド絞り込みパターン（流用元） |
| `_local_issue_edit` の argparse | `kaji_harness/cli_main.py:1099-1116` | `--branch-prefix` がないことの根拠 |
| `/issue-start` 現行仕様 | `.claude/skills/issue-start/SKILL.md:22-49` | 固定 default prefix=feat、label 非参照 |
| 実害ログ | `.kaji-artifacts/local-pc5090-14/runs/2605091631/close/console.log:3-4` | agent fallback の生記録 |
| `[branch_name]` 直接埋込 | `.claude/skills/issue-close/SKILL.md:333` | `git merge --no-ff --no-edit [branch_name]` |
| review-ready Findings 採用 | `.kaji/issues/local-pc5090-17-issue-start-skill-prefix-type-label-bran/comments/0001-pc5090.md` | 3 件 Findings 全件採用（A 案 + helper CLI 化 + 同根調査） |
| Bettenburg et al. (2008) 引用 | `.claude/skills/_shared/design-by-type/bug.md:22-27` | 「良い bug report は OB + EB + steps-to-reproduce を備える。設計書でもこの 3 点を分離して書く」 |
