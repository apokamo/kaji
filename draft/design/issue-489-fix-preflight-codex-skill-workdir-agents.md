# [設計] Codex step preflight の skill 探索範囲を workdir から Git repo root までに広げる

Issue: #489

## 概要

`kaji_harness/preflight.py` の `_codex_discovery_error` は、実効 workdir 直下の
`.agents/skills/<skill>/SKILL.md` だけを確認している。workdir が repo のサブディレクトリにあると、
Codex は repo root の skill を解決できるのに preflight は失敗する。これを、Codex と同じ
「実効 workdir から Git repo root まで（両端を含む）」の探索範囲に合わせる。

## 背景・目的

### Observed Behavior (OB)

Issue #489「再現手順」4 の `kaji validate` 実出力（kaji 0.23.0）:

```
Step '...' uses agent 'codex' but skill 'zq-probe' is not discoverable by Codex: <repo>/sub/deeper/.agents/skills/zq-probe/SKILL.md not found.
```

同じ配置で `codex exec --json -s read-only -C <repo>/sub/deeper '$zq-probe'` を実行すると
`PROBE-TOKEN-7731` が返り、`command_execution` は 0 件だった（skill がファイル探索なしで注入された）。
利用 repo kamo2 でも、`workdir: <repo>/apps/web` の workflow 6 件が同じ理由で失敗した（Issue「影響」）。

### Expected Behavior (EB)

- 実効 workdir が属する Git repo の中では、workdir・中間ディレクトリ・repo root のどこかに
  `.agents/skills/<skill>/SKILL.md` があれば preflight は通る（Issue「決定事項」・完了条件 1）
- repo root より上にしかない場合と、範囲内のどこにもない場合は、Codex 起動前に失敗する
  （Issue 完了条件 2。Codex はこの配置では skill を注入せず fail-open になる。Issue「Codex の解決範囲」の実測表）
- 根拠: OpenAI 公式 Build skills の「Where Codex loads local skills」（cwd から repo root までの各階層を探索する）、
  Issue #489 の codex-cli 0.163.0 実測表

### 目的

サブディレクトリを workdir にする Codex step で、preflight を通すためだけの `.agents` 複製（symlink）を
不要にする。#408 で防いだ fail-open（Codex が解決できない構成で起動すること）は引き続き起動前に止める。

## 再現手順（steps-to-reproduce）

1. 前提: 一時 Git repo `repo/`（`git init`）。canonical skill `repo/.claude/skills/review/SKILL.md` と、
   Codex 用 symlink `repo/.agents/skills/review -> ../../.claude/skills/review` を置く。`repo/sub/deeper/` を作る
2. `agent: codex`、`skill: review`、`workdir: <repo>/sub/deeper` の step を持つ workflow に
   `preflight_workflow(..., project_root=repo, skill_dir=".claude/skills")` を実行する（`kaji validate` も同じ経路）
3. OB: `errors` に `... is not discoverable by Codex: <repo>/sub/deeper/.agents/skills/review/SKILL.md not found. ...` が 1 件入る

この手順を恒久回帰テストとして固定する。Medium ではファイル操作で `.git` マーカーを作り、Large（`large_local`）では実 `git init` と `kaji validate` を使う（テスト戦略を参照）。

## 根本原因（Root Cause）

- **なぜ間違っているか**: `_codex_discovery_error`（`kaji_harness/preflight.py:118-155`）は
  `resolve_step_workdir()` の結果 1 箇所を `validate_skill_exists(step.skill, codex_root, ".agents/skills")` に渡す。
  この関数は `codex_root` を存在確認の基点にし、symlink の許容境界（`resolved.is_relative_to(workdir.resolve())`）にも使う。
  そのため次の 2 つの誤りがある
  1. 候補が workdir 直下 1 箇所だけで、Codex が見る中間ディレクトリと repo root を見ない
  2. 仮に中間ディレクトリを候補に加えても、そこから repo root の canonical を指す symlink
     （例: `repo/sub/.agents/skills/review -> ../../../.claude/skills/review`）は「workdir の外」として拒否される
- **いつから**: 330f65b（#408、PR #462、v0.22.0）で導入。#408 の設計書
  `draft/design/issue-408-fix-prompt-codex-step-skill-invocation.md` は、検査対象を
  「`<step の実効 workdir>/.agents/skills/<skill>/SKILL.md`」1 箇所に意図的に限定していた。当時の判断は
  workdir = project root の構成だけを前提にしており、Codex の実際の探索範囲とずれていた
- **同じ原因で壊れている他の箇所**:
  - preflight を呼ぶ `kaji run`（`runner.py:905-918` `_collect_skill_metadata`）・`kaji validate`・`kaji recover`・
    series loader は、すべて `preflight_workflow` を経由する。修正箇所は `_codex_discovery_error` の 1 箇所で足りる
  - runner が Codex を起動する cwd（`runner.py:470` の `resolve_step_workdir`）は正しい。変更しない
  - 誤った探索範囲を「正しい挙動」として書いている文書: `docs/dev/skill-authoring.md` § codex step の skill 解決、
    `docs/ARCHITECTURE.md:59`、`docs/dev/shared_skill_rules.md:135`、`kaji_harness/skill.py` の `CODEX_SKILL_DIR` コメント、
    `tests/test_codex_skill_discovery.py` の module docstring と `test_step_workdir_is_the_discovery_root`
  - `CHANGELOG.md` 0.22.0 の BREAKING 記述は過去 release の記録なので書き換えない

## インターフェース

公開 CLI・公開関数のシグネチャは変えない。変わるのは preflight の合否とエラー文だけ。

### 入力

- 既存どおり `preflight_workflow(workflow, project_root=..., skill_dir=..., skill_exists_validator=..., skill_metadata_loader=...)`
- `SkillExistsValidator`（`Callable[[str, Path, str], Path]`）の test seam と、
  `validate_skill_exists(skill_name, workdir, skill_dir)` のシグネチャも変えない

### 出力（挙動の変更前 / 変更後）

| 条件 | 変更前 | 変更後 |
|------|--------|--------|
| workdir 直下に skill あり | 合格 | 合格 |
| Git repo 内で、中間ディレクトリまたは repo root にだけ skill あり | **不合格** | 合格 |
| Git worktree（`.git` がファイル）の root にだけ skill あり、workdir はそのサブディレクトリ | **不合格** | 合格 |
| workdir が `project_root` 外の別 Git repo にあり、その repo の root に skill あり | **不合格** | 合格 |
| repo root より上（Git repo 外の親）にだけ skill あり | 不合格 | 不合格 |
| 探索範囲のどこにもない | 不合格 | 不合格（エラー文に探索範囲を明示） |
| 範囲内の候補が repo 内の別ディレクトリを指す symlink | workdir 外なら不合格 | 合格 |
| 範囲内の候補が repo 外を指す symlink | 不合格（workdir 外） | 不合格（repo 外） |
| workdir が Git repo の外 | workdir 直下のみ検査 | 変更なし |
| canonical（`paths.skill_dir`）検証、`exec_script` skill、claude / antigravity step | — | 変更なし |

### 使用例

```python
# repo/ は Git repo。skill は repo/.agents/skills/review -> ../../.claude/skills/review
workflow = Workflow(..., steps=[Step(id="review", skill="review", agent="codex",
                                     workdir="/abs/repo/sub/deeper", on={...})])
result = preflight_workflow(workflow, project_root=Path("/abs/repo"), skill_dir=".claude/skills")
assert result.errors == []  # 変更前は "not discoverable by Codex" が 1 件
```

エラー文の構成（Git repo 内で見つからない場合。文言の詳細は実装で調整してよい）:

```
Step 'review' uses agent 'codex' but skill 'review' is not discoverable by Codex:
no .agents/skills/review/SKILL.md in any directory from /abs/repo/sub/deeper up to the Git repo root /abs/repo.
Codex resolves `$review` from .agents/skills in each directory from the workdir up to the Git repo root
(not above it); add .agents/skills/review in one of those directories (e.g. a symlink to the canonical skill directory).
```

実際には 1 行で出力する（既存の `errors` は 1 要素 1 行の契約）。

## 変更スコープ

- `kaji_harness/preflight.py`: `_codex_discovery_error` の探索ロジックとエラー文、docstring
- `kaji_harness/skill.py`: `CODEX_SKILL_DIR` のコメントのみ（`validate_skill_exists` の本体は変えない）
- `tests/test_codex_skill_discovery.py`: 回帰テスト追加、旧探索範囲を固定するテストの更新、tmp 配置の隔離
- `docs/dev/skill-authoring.md` / `docs/ARCHITECTURE.md` / `docs/dev/shared_skill_rules.md`: 探索範囲の記述

## 制約・前提条件

- Codex の探索範囲は「cwd から Git repo root まで（両端を含む）の各階層の `.agents/skills`」とする
  （Issue 実測と公式 docs）。user / admin スコープ（`~/.agents/skills` 等）は対象外のまま
- skill 名の path traversal 防止（`..` を含む名前の拒否）と canonical 検証は維持する
- Git repo の判定に外部コマンド（`git`）を使わない。preflight は `kaji validate` などから頻繁に呼ばれ、
  現状 subprocess を使わないファイル検査だけで完結している
- 実効 workdir は L1 validation で絶対パスに制限済み（`workflow.py:258,436,516,583`）
- テスト実行時の `tmp_path` は workflow 内では `TMPDIR` によりリポジトリ配下になる（#407、
  `tests/conftest.py` の `outside_project_tmp_path`）。祖先を辿る今回のロジックでは、テストが
  実リポジトリ（kaji 自身の `.git` と `.agents/skills`）を拾わないように配置を隔離する必要がある

## 方針

`_codex_discovery_error` を次の流れにする。既存の `skill_exists_validator` seam を
そのまま使い、`validate_skill_exists` の「`workdir` 引数 = 存在確認の基点 かつ symlink 境界」という性質を利用する。

```python
def _codex_discovery_error(step, workflow, project_root, *, skill_exists_validator):
    # 非文字列 workdir の早期 return は現状維持
    workdir = resolve_step_workdir(step, workflow, project_root)
    repo_root = _find_git_repo_root(workdir.resolve())
    if repo_root is None:
        # Git repo 外: 現行どおり workdir 直下 1 箇所（境界も workdir）
        try: skill_exists_validator(step.skill, workdir, CODEX_SKILL_DIR)
        except (SkillNotFound, SecurityError) as exc: return 現行と同じ形のエラー
        return None

    found = False
    for directory in [workdir.resolve(), *祖先 ... repo_root]:   # workdir から repo root まで（両端を含む）
        relative = directory.relative_to(repo_root)
        candidate_dir = (relative / CODEX_SKILL_DIR).as_posix()  # 例: "sub/deeper/.agents/skills"
        try:
            # 基点を repo_root にすることで、symlink 境界 = repo root になる
            skill_exists_validator(step.skill, repo_root, candidate_dir)
            found = True
        except SkillNotFound:
            continue
        except SecurityError as exc:
            return repo 外 symlink のエラー（exc を含め、境界が repo root であることを明示）
    return None if found else 範囲を示した not-found エラー


def _find_git_repo_root(start: Path) -> Path | None:
    # start 自身と祖先を近い順に見て、`.git`（ディレクトリまたはファイル）が最初にある階層を返す
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return None
```

- **repo root の判定**: `.git` が存在する最も近い祖先（自身を含む）。通常の clone は `.git` ディレクトリ、
  `git worktree add` の worktree と submodule は `.git` ファイル（`gitdir: ...`）を持つので、
  `exists()` で両方を受け入れる。最初に見つかった階層で止まるため、入れ子の repo では内側が境界になる
- **workdir の正規化**: Git repo 内の探索は `workdir.resolve()` を起点にする。Codex は `-C` / cwd に chdir した
  実パスを起点に探すため、symlink 経由の workdir でも実パスの祖先を辿るのが整合する。Git repo 外の分岐は
  現行コードと同じく未正規化の `workdir` を渡し、挙動とエラー文を変えない
- **全候補の走査**: 見つかった時点で打ち切らず、範囲内の全候補を確認する。範囲内に 1 つでも
  repo 外を指す同名候補があれば、他に正しい候補があっても不合格にする（同名 skill が複数あるとき、
  Codex がどれを使うかに依存しないため）。存在しない候補（dangling でない単なる不在）は無視する
- **symlink の境界**: 境界は「実効 workdir が属する Git repo の root」（`repo_root.resolve()`）。
  `project_root` は境界に使わない。workdir が `project_root` 外の別 repo にあれば、その repo の root が境界になる
- **test seam**: runner 系テストは `kaji_harness.runner.validate_skill_exists` を MagicMock に差し替え、
  それが `skill_exists_validator` として codex 検査にも渡る。呼び出し形 `(skill, Path, str)` を保つので、
  これらのテストは変更不要（mock は例外を投げず、最初の候補で合格扱いになる）

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| Codex step の探索範囲 | 実効 workdir から、その workdir が属する Git repo root まで（両端を含む）の各階層の `.agents/skills/<skill>/SKILL.md`。repo root より上は候補にしない。`project_root` 外の別 repo ではその repo root が境界 | Issue 本文「決定事項」1 行目、grill-me provenance コメント「人間の決定と理由」1（人間決定） | 候補を近い順に列挙し、`repo_root` 基点の相対 `skill_dir` に変換して既存 validator に渡す構成へ分解 |
| symlink の許容境界 | repo 内の別ディレクトリを指す symlink は許可、repo 外を指す symlink は拒否 | Issue 本文「決定事項」2 行目、grill-me provenance「人間の決定と理由」2（人間決定。推奨案の採用と明示） | 境界を `repo_root.resolve()` とし、`validate_skill_exists` の `workdir` 引数に `repo_root` を渡して既存の `is_relative_to` 検査をそのまま使う |
| canonical skill 検証 | `paths.skill_dir` の存在・メタデータ検証を維持 | Issue 本文「決定事項」3 行目、grill-me provenance「人間の決定と理由」3（人間決定、選択肢 A） | `preflight_workflow` の canonical 検証部分は変更しない。canonical 失敗時に codex エラーを重ねない既存挙動も維持 |
| Git repo 外の workdir | 現行の workdir 直下検査を維持 | Issue 本文「決定事項」4 行目（書き戻し確認で人間が承認） | `_find_git_repo_root` が `None` のときは現行コードと同じ呼び出し・同じエラー文にする |
| 旧い制約・テスト・文書 | Codex の実挙動と照合して更新または削除。fail-open 防止は維持 | Issue 本文「決定事項」5 行目、grill-me provenance「人間の決定と理由」4（人間決定） | 更新対象を「根本原因」の列挙に特定。`test_step_workdir_is_the_discovery_root` は Git repo 外の現行挙動を保つテストへ置き換え、Git repo 内で合格するテストを追加 |
| repo root の判定方法 | `.git`（ディレクトリまたはファイル）が存在する最も近い祖先。`git rev-parse` は使わない | AI の仮定。根拠: Issue 本文が判定方法を設計に委ねている。gitrepository-layout / git-worktree の公式仕様で worktree・submodule は `.git` ファイルを持つ。preflight を subprocess なしに保てる。Issue 実測の worktree ケースと一致。検査先: review-design、Medium テスト（`.git` ファイル形の worktree）と Large（`large_local`）テスト（実 `git worktree add`） | `exists()` で両形式を受け入れ、最初に見つかった階層で止める（入れ子の repo では内側で止まることを Medium で検査） |
| 探索の起点の正規化 | Git repo 内の探索は `workdir.resolve()` を起点にする | AI の仮定。根拠: Codex は chdir 後の実パスから探索する。symlink 境界も resolve 後のパスで比較している。検査先: review-design / review-code、Medium テスト（実体が別 repo にある symlink workdir） | Git repo 外の分岐は未正規化のまま（現行維持） |
| repo 外 symlink と正しい候補が同時にある場合 | 範囲内の同名候補が 1 つでも repo 外を指せば不合格 | AI の仮定。根拠: 決定事項は repo 外 symlink を「拒否」としている。Codex が同名候補のどれを採るかは公式 docs で保証されていないため、厳しい側に倒す。誤りなら判定を緩めるだけで直せる two-way door。検査先: review-design、Medium テスト | 全候補を走査し、`SecurityError` を見つけた時点でエラーを返す |
| エラー文の構成 | 探索範囲（workdir と repo root）と追加先の案内を含む 1 行 | AI の仮定。根拠: Issue 本文が具体的な構成を実装に委ねている。検査先: review-code | 既存テストが依存する `not discoverable by Codex` と、repo 外 symlink の `escapes workdir` 文字列は残す |
| CHANGELOG | 本 PR では `CHANGELOG.md` を編集しない | AI の仮定。根拠: `[Unreleased]` は空で、変更点は `/release` が記載している（0.23.1 の記載例）。0.22.0 の BREAKING 記述は過去 release の記録。検査先: i-dev-final-check | — |
| #408 設計書 | `draft/design/issue-408-*.md` は書き換えない | AI の仮定。根拠: 完了済み Issue の設計判断の記録で、現行の制約を定める文書ではない。現行の正本は `docs/dev/skill-authoring.md`。検査先: review-design | — |

one-way door の未決はない。公開 CLI の引数・終了コード・出力形式は変えず、変わるのは合否条件（人間決定済み）と
エラー文の文言（two-way door）だけである。

## テスト戦略

### 変更タイプ

- 実行時コード変更（preflight の合否条件の変更）

### 再現テスト（Red → Green）

修正前に Red になる恒久回帰テストを追加する。Issue 本文の `kaji validate` の実出力（OB）も、実装前に Red だったことを示す実ログになる。

- Medium: `.git` マーカーをファイル操作で作った repo の root に skill symlink を置き、workdir を `repo/sub/deeper` にする。この codex step が preflight を通ること（現状は `not discoverable by Codex` が出るので Red）
- Large（`large_local`）: 実 `git init` した repo で同じ構成の workflow YAML（`workdir: <repo>/sub/deeper`）を作り、`kaji validate`（`cmd_validate`）が exit 0 になること（Issue 再現手順 4 の CLI 経路）

### テストサイズの配置方針

`docs/reference/testing-size-guide.md` に従う。実 CLI を subprocess で実行するテストは Large、外部ネットワークを使わないものには `large_local` を付ける。

- **Medium**: subprocess を使わない実ファイル検査。repo root の判定は `.git` の有無だけを見るので（方針を参照）、Git repo はファイル操作で作る。通常の repo は `.git/` ディレクトリを mkdir し、linked worktree / submodule の形は `gitdir: <path>` を書いた `.git` ファイルで作る（gitrepository-layout の形式）。探索範囲・symlink 境界・現行維持の観点は主にここで網羅する
- **Large（`@pytest.mark.large` と `@pytest.mark.large_local` を併記）**: 実 `git` コマンド（`git init` / `git commit` / `git worktree add`）で作った repo と worktree を使う。ファイルで作ったマーカーが、実 Git が作る構造と一致していることを確認する。外部ネットワークと実 Codex は使わない
- 既存テストのサイズは再分類しない。`TestRunnerStopsBeforeCodex` と `TestValidateCommandReportsCodexDiscovery` は `_make_config` の中で `git init` を呼ぶ既存の Medium テストで、今回は内容を変えずに残す（レビュー指摘でも全体の再分類は求められていない）。新しく追加するテストのうち実 Git を使うものは、すべて Large に置く

### Small テスト

- 既存の `TestResolveStepWorkdir`（workdir の優先順位）はそのまま残す
- Small は新しく追加しない。探索ロジックの本体は `.git` と `SKILL.md` の存在確認（ファイルシステム）であり、mock で置き換えると、回帰が起きやすい「祖先のどこまで見るか」「symlink の境界」を検証できないため。これらは実ファイルを使う Medium で検証する

### Medium テスト

`tests/test_codex_skill_discovery.py` で、一時ディレクトリに実ファイル・実 symlink・ファイル操作で作った `.git` マーカーを置いて検証する。subprocess は使わず、`validate_skill_exists` も patch しない。

- **合格する配置**（完了条件 1）
  - repo root の `.agents/skills`、workdir = `repo/sub/deeper`
  - 中間ディレクトリ `repo/sub/.agents/skills`、workdir = `repo/sub/deeper`
  - 中間ディレクトリから repo root の canonical を指す symlink（`repo/sub/.agents/skills/review -> ../../../.claude/skills/review`）。symlink の境界が候補ディレクトリではなく repo root であることを確認する
  - root に `.git` ファイル（`gitdir: ...`）を持つ worktree 形のディレクトリで、root の skill が workdir = `wt/sub/deeper` から見つかる
  - workdir が `project_root` 外の別 repo にあるとき、その repo の root の skill で合格する
- **不合格になる配置**（完了条件 2）
  - repo の外にある親ディレクトリ（`outer/.agents/skills`。repo root は `outer/inner`）にだけ skill があり、workdir = `outer/inner/sub` → 不合格
  - 探索範囲のどこにもない → 不合格。エラー文に workdir・repo root・`not discoverable by Codex` を含む
  - `project_root` には skill があるが、workdir は skill のない別 repo にある → 不合格（`project_root` を境界にも候補にも使わない）
- **境界に関する仮定**（review-design の Should Fix を取り込んだもの）
  - 入れ子の repo（`outer/` と `outer/inner/` の両方に `.git`）では、内側の root で探索が止まる。`outer/.agents/skills` にしかない場合は不合格
  - workdir が symlink で、その実体が別 repo の中にある場合は、実体側の repo root を境界にして探索する（`workdir.resolve()` を起点にするという仮定の検査）
- **symlink**（完了条件 3）
  - repo 内の別ディレクトリ（例 `repo/tools/skills/review`）を指す symlink → 合格
  - repo root の候補が repo 外を指す symlink → 不合格。エラー文に `escapes workdir` を含む（既存の `test_symlink_escaping_workdir_is_error` を repo 内の構成に移す）
  - 中間ディレクトリの候補が repo 外を指し、repo root には正しい候補がある → 不合格（全候補を走査するという仮定の検査）
  - `..` を含む skill 名は、canonical 検証のエラー 1 件だけになる（既存テストを維持）
- **変えない挙動**（完了条件 4）
  - Git repo の外にある workdir（`outside_project_tmp_path` 配下）: workdir 直下にあれば合格、親にしかなければ不合格。既存の `test_step_workdir_is_the_discovery_root` をこの形に置き換える
  - claude / antigravity step、`exec_script` skill、canonical が無いときにエラーを重ねない挙動、非文字列の workdir（既存テストを維持）
  - `TestRunnerStopsBeforeCodex` / `TestValidateCommandReportsCodexDiscovery` の既存ケース（Codex を起動しないこと）
- **テストの隔離**: 今回のロジックは祖先を辿るので、repo を前提にするテストは必ず一時ディレクトリに自前の `.git` マーカーを置く。Git repo の外を前提にするテストは `outside_project_tmp_path` を使う。workflow 内では `tmp_path` がリポジトリ配下になり（#407）、そのままでは kaji 自身の `.git` と `.agents/skills` を拾ってしまうため。既存の `TestCodexSkillPreflight` で `tmp_path` に直接置いているケースも、この方針で書き直す

### Large テスト

`tests/test_codex_skill_discovery.py` に `@pytest.mark.large` と `@pytest.mark.large_local` を併記したクラスを追加する。実 `git` を subprocess で呼ぶが、外部ネットワークと実 Codex は使わない。

- 実 `git init` した repo で、root に skill symlink を置き workdir を `repo/sub/deeper` にした workflow YAML に `kaji validate`（`cmd_validate`）を実行し、exit 0 になる（再現テストの CLI 経路）
- 実 `git worktree add` で作った linked worktree で、worktree の root にある skill が workdir = `worktree/sub/deeper` から見つかる。実 Git が作る `.git` ファイルを repo root として判定できることを確認する
- 実 `git init` した別 repo（`project_root` の外）にある workdir で、その repo の root の skill によって合格する
- 実 `git init` した repo で skill がどこにもない場合、`kaji validate` が 0 以外で終了し、`not discoverable by Codex` を出力する

実 Codex を呼ぶテストは追加しない。理由:

1. 今回変えるのは kaji 側の「どのファイルを確認するか」という検査で、上記の Medium と Large（実 Git）で再現できる
2. Codex 自身の解決範囲は外部ツールの仕様で、根拠は公式 docs と Issue #489 の codex-cli 0.163.0 実測表にある。実 Codex を呼ぶテストは Codex の挙動（LLM の応答と認証）を検証することになり、kaji の回帰検出の情報はほとんど増えない
3. preflight は Codex を起動する前に終わるので、誤った合格・不合格の回帰は Codex なしで検出できる

`make check` の `test` ターゲットは marker で絞らずに `pytest` を実行するので、追加する Large（`large_local`）も含めて実行される。`make test-large-local` で個別に実行することもできる。

### 長時間 acceptance の配置

該当なし。追加する検証は、`pytest` の Medium テストと Large（`large_local`。ローカルの `git init` / `git worktree add` だけで、1 件あたり数秒程度）と `make check` だけ。nested `kaji run`、外部 agent を使う dogfood、fresh 環境での繰り返し acceptance は含まない。どれも 1 回の所要時間が implement step の timeout の 25% を大きく下回る。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない。#408 の preflight 方針の範囲を直すだけ |
| docs/ARCHITECTURE.md | あり | 59 行目「`<workdir>/.agents/skills/<skill>/SKILL.md` の存在を preflight が必須検証」を、workdir から Git repo root までの探索に直す |
| docs/dev/skill-authoring.md | あり | § codex step の skill 解決の「`<workdir>/.agents/skills/...` から解決」「1 箇所だけ」「workdir の外へ出る symlink は拒否」を、探索範囲・repo root 境界・Git repo 外の扱いに直す。確認済み Codex version に codex-cli 0.163.0 の実測を加える |
| docs/dev/shared_skill_rules.md | あり | 135 行目「codex step で使う skill では symlink が必須」を「workdir から Git repo root までのどこかに必須」と読める表現にする |
| docs/dev/workflow_overview.md | なし | 55 行目は「symlink で追随する（必須。preflight が検証する）」で、探索範囲を書いていない |
| docs/reference/ | なし | API 仕様・規約の変更はない |
| docs/cli-guides/ | なし | CLI の引数・出力形式は変わらない。interactive-terminal-runner の記述は herdr-kaji-launch symlink の存在の説明だけ |
| docs/guides/python-starter*.md | なし | `.agents/skills` symlink の配置の説明だけで、探索範囲を書いていない |
| AGENTS.md / CLAUDE.md | なし | 規約の変更はない |
| CHANGELOG.md | なし（本 PR） | `/release` で `[Unreleased]` に記載する。0.22.0 の記述は過去の記録なので書き換えない |
| kaji_harness/skill.py のコメント | あり | `CODEX_SKILL_DIR` の「workdir からの相対パス」を、workdir から repo root までの各階層からの相対パスに直す |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| OpenAI 公式 Build skills | https://learn.chatgpt.com/docs/build-skills （「Where Codex loads local skills」） | Codex は repo スコープの skill を、cwd から repo root までの各階層の `.agents/skills` から読み込む。symlink はたどる。レディネスレビューでも同節を確認済み |
| Issue #489 本文 | https://github.com/apokamo/kaji/issues/489 | OB（`kaji validate` のエラー出力）、codex-cli 0.163.0 の配置別実測表（repo root・中間・worktree は注入、repo root より上と未配置は注入されない）、決定事項 5 行 |
| grill-me provenance コメント | https://github.com/apokamo/kaji/issues/489#issuecomment-6245293537 | 別 repo の workdir もその repo root まで探索、repo 外 symlink 拒否・repo 内許可、canonical 維持、旧テスト・制約の更新を人間が決定 |
| #408 設計書 | draft/design/issue-408-fix-prompt-codex-step-skill-invocation.md | 112 行目: 検査対象を `<step の実効 workdir>/.agents/skills/<skill>/SKILL.md` 1 箇所に限定した当時の判断。fail-open の再現（Codex がファイル探索して読む）の記録 |
| 現行実装 | kaji_harness/preflight.py:118-155、kaji_harness/skill.py:35-62 | `_codex_discovery_error` が `validate_skill_exists(step.skill, codex_root, CODEX_SKILL_DIR)` を 1 回だけ呼ぶ。`validate_skill_exists` は `workdir` 引数を存在確認の基点と symlink 境界（`is_relative_to(workdir.resolve())`）の両方に使う |
| runner の preflight 呼び出し | kaji_harness/runner.py:905-918、470 | `_collect_skill_metadata` が `validate_skill_exists` を seam として渡す。Codex の起動 cwd は `resolve_step_workdir` |
| 旧挙動を固定するテスト | tests/test_codex_skill_discovery.py `test_step_workdir_is_the_discovery_root` | project root に symlink があっても `sub` を workdir にすると不合格、を期待している |
| テスト用 tmp の隔離 | tests/conftest.py `outside_project_tmp_path`（#407） | workflow 内の `tmp_path` は `TMPDIR` によりリポジトリ配下になるため、git 外を前提にするテストは専用 fixture を使う |
| Git: gitrepository-layout | https://git-scm.com/docs/gitrepository-layout | 作業ツリーの `.git` は、リポジトリのディレクトリか、実体を指す `gitdir: <path>` を書いたファイルのどちらか（worktree・submodule で使う） |
| Git: git-worktree | https://git-scm.com/docs/git-worktree | linked worktree は作業ツリーの root に `.git` ファイルを持ち、`$GIT_DIR/worktrees/<id>` を指す |
| テスト規約 | docs/dev/testing-convention.md | サイズ判定（ファイル I/O は Medium）、恒久テストを省略するときに示す理由の要件 |
| テストサイズ判断ガイド | docs/reference/testing-size-guide.md | 「実際の CLI コマンドを subprocess で実行」は Large。subprocess あり・外部ネットワークなしは `large_local` を併記する。実 `git` を使うテストを Large に置く根拠 |
