# [設計] Codex step の対象 skill を明示 invocation で指定する

Issue: #408

## 概要

`kaji_harness/prompt.py` `build_prompt()` は backend を問わず先頭行に
``スキル `{step.skill}` を実行してください。`` を生成する。Codex はこれを明示 skill invocation
として扱わず、ファイル探索で `SKILL.md` を見つけて従っている。Codex step では先頭行を
`$<skill> を実行してください。` に変える（D1）。あわせて、全 workflow 共通の preflight で
`<workdir>/.agents/skills/<skill>/SKILL.md` の存在を検証し、欠けていれば Codex を起動する前に
エラーで止める（D2）。

## 背景・目的

### Observed Behavior (OB)

証跡: 事前調査コメント https://github.com/apokamo/kaji/issues/408#issuecomment-5914559295 §2
（codex-cli 0.159.2。main ace7347 で採取。現 main `ffc90bb` の `kaji_harness/prompt.py:87` も同じ形式）。

1. 現行形式 ``スキル `secret-token` を実行してください。`` を渡すと、応答は正しい
   （`TOKEN-ZEBRA-4821`）。ただし skill として注入されてはいない。Codex は `find` で
   `~/.codex/skills` などを探し、最後に `cat .agents/skills/secret-token/SKILL.md` で読んで従った。
   tool 呼び出しは複数回で、入力は約 68k tokens だった
2. `$no-such-skill を実行してください。` はエラーにならない。未解決の `$name` は普通の文字列として
   扱われ、Codex は自分で探しに行く。exit code や警告では検出できない（fail-open）
3. kaji の skill 存在検証（`kaji_harness/skill.py:30` `validate_skill_exists`、
   `kaji_harness/preflight.py:83-85`）は `<project_root>/<paths.skill_dir>/<skill>/SKILL.md`
   だけを見る。Codex が探す `.agents/skills` は検証していない

### Expected Behavior (EB)

- Codex step は、step 定義の skill を `$<skill>` で明示的に呼び出す。新規実行と resume の両方で
  同じにする（D1）。公式仕様では、明示 invocation は「Include the skill directly in your prompt」
  であり、CLI では `$` で mention する。`allow_implicit_invocation: false` を付けた skill でも
  「explicit `$skill` invocation still works」とされている（Primary Sources 参照）
- `codex exec` と resume で `$name` が効くことは、事前調査 §2 の #2・#3 で実機確認済み
  （codex-cli 0.159.2）
- `$<skill>` が解決できない構成なら、Codex を起動する前に kaji がエラーで止まる（D2）
- Claude / Antigravity の prompt は変えない

## 再現手順

Issue 本文 `## 再現手順` と同じ。前提: codex-cli 0.159.2（認証済み）。scratchpad の git repo に
`.agents/skills/secret-token/SKILL.md`（本文 `Reply with exactly: TOKEN-ZEBRA-4821`）と
`.agents/skills/secret-token/agents/openai.yaml`（`policy: allow_implicit_invocation: false`）を置く。

1. ``codex exec --json --skip-git-repo-check -s read-only -C <dir> "スキル `secret-token` を実行してください。" </dev/null``
   → 探索（`find` → `cat`）を経て応答する。明示 invocation ではない
2. 同じ条件で prompt を `$secret-token を実行してください。` にする → tool 呼び出しなしで即答する
3. 2 の session に `codex exec resume <id> ... 'もう一度 $secret-token を実行してください。'`
   → resume でも同じ応答になる
4. `$no-such-skill を実行してください。` → エラーにならず探索に落ちる（fail-open）

kaji 側の再現は、Codex を起動しない回帰テストで固定する（テスト戦略参照）。
`build_prompt` に codex step を渡すと先頭行がバッククォート形式になること、`.claude/skills`
にしか skill がない codex step を preflight が通してしまうことを assert する。

## 根本原因

### なぜ壊れているか

- `build_prompt` は導入時（465b380、2026-03-10、dao_harness 時代の #57）から backend を区別せず
  ``スキル `{step.skill}` を実行してください。`` を生成している。`docs/dev/skill-authoring.md:7`
  の「スキルのロードは CLI に完全に委譲する」という前提のとおり、skill 名を本文に書けば各 CLI が
  ネイティブに読み込むと想定していた
- Codex の明示 invocation 構文は `$<name>` の mention である。バッククォートで名前を書いても
  invocation にはならない。Codex は description による暗黙選択か、ファイル探索に頼ることになる。
  事前調査では探索で動いていた。結果が正しいのは偶然であって、決定的ではない
- Codex 対応（4267b9c、2026-03-10「make claude skills codex-compatible via symlink mirror」）は
  `.agents/skills/` に symlink を置いただけだった。prompt の形式と検証対象は変えていない。
  kaji の検証は今も `paths.skill_dir`（canonical）しか見ない。Codex 側から見えるかどうかは
  検証されていない

### 同根の他の壊れ箇所（調査結果）

1. **kaji 自身の repo で `review` skill が Codex から見えない**:
   `.kaji/wf/**` の codex step が使う skill を `.agents/skills/<skill>/SKILL.md` と照合した。
   `review` だけが存在しなかった。`review` は `dev.yaml` / `docs.yaml` /
   `custom/dev/dev-small.yaml` / `dev-thorough*.yaml` / `custom/docs/docs-*.yaml` の
   `review` step（codex）で使われている。`.claude/skills/review` はあるが、
   `.agents/skills/review` symlink がない。この step は今、明示 invocation でも暗黙選択でもなく、
   ファイル探索で動いている。D2 の preflight を入れると、この欠落が即エラーになる
   （`make validate-workflows` と `tests/test_cli_validate.py::test_official_workflows_all_validate`
   が失敗する）。本 Issue で `.agents/skills/review` symlink を追加する
2. **interactive_terminal（tmux / herdr）の codex 経路**: `build_prompt` の出力は `prompt.txt`
   に書かれる。しかし Codex TUI が user 入力として受け取るのは
   `kaji_harness/assets/interactive-terminal/wrapper.sh:43-50` の `initial_prompt`
   （"Read the full task prompt from: …"）だけで、`prompt.txt` は agent が後で file として読む。
   したがって `prompt.txt` の先頭を `$<skill>` にしても、user 入力の mention にはならない。
   A3 を実効的に適用するには、wrapper の初期メッセージにも mention を入れる必要がある（方針 4）
3. 他の skill の frontmatter `name` はすべてディレクトリ名と一致している（`.claude/skills/*/SKILL.md`
   を全件確認）。`$<step.skill>` と Codex が使う skill `name` の不一致は、現時点の kaji repo にはない

## インターフェース

### 入力

変更なし。workflow YAML の `steps[].skill` / `steps[].agent` / `steps[].workdir`、
workflow の `workdir`、`.kaji/config.toml` の `paths.skill_dir` をそのまま使う。新しい設定キーは
追加しない。

### 出力（変更前 / 変更後）

| 対象 | 変更前 | 変更後 |
|------|--------|--------|
| `build_prompt()` 先頭行（agent=codex） | ``スキル `review` を実行してください。`` | `$review を実行してください。` |
| `build_prompt()` 先頭行（agent=claude / antigravity） | ``スキル `review` を実行してください。`` | 変更なし |
| `codex exec` / `codex exec resume` の最終位置引数 | 上の prompt | 上の prompt（`$review …` で始まる） |
| interactive_terminal codex の初期メッセージ | `Read the full task prompt from: …` | `$review を実行してください。` + 空行 + 従来の文面 |
| interactive_terminal claude / antigravity の初期メッセージ | `Read the full task prompt from: …` | 変更なし |
| preflight（`kaji run` / `kaji validate` / `kaji recover` / series loader）| codex step でも canonical のみ検証 | codex step は `<step の実効 workdir>/.agents/skills/<skill>/SKILL.md` も検証する。欠落・traversal はエラー |

preflight のエラーは既存の `WorkflowPreflightResult.errors` に 1 行追加する形で返す。呼び出し側の
扱い（`kaji run` は `WorkflowValidationError`、`kaji validate` は ✗ と exit code）は変えない。
エラー文には少なくとも step id、skill 名、検査したパス、対処（canonical skill ディレクトリへの
symlink を `.agents/skills/<skill>` に置く）を含める。

例:

```text
Step 'review' uses agent 'codex' but skill 'review' is not discoverable by Codex:
/repo/.agents/skills/review/SKILL.md not found. Codex resolves `$review` from
<workdir>/.agents/skills; add .agents/skills/review (e.g. a symlink to .claude/skills/review).
```

### 使用例

```python
# 内部 API（疑似コード）。公開 CLI の引数・終了コードは変えない。
step = Step(id="review", skill="review", agent="codex", on={...})
prompt = build_prompt(step, ..., issue_context=ctx, verdict_path=vp)
assert prompt.splitlines()[0] == "$review を実行してください。"

args = build_cli_args(step, prompt, workdir, session_id=None, execution_policy="auto")
assert args[-1].startswith("$review ")          # 新規
args = build_cli_args(step, prompt, workdir, session_id="thread-1", execution_policy="auto")
assert args[-1].startswith("$review ")          # resume

result = preflight_workflow(workflow, project_root=root, skill_dir=".claude/skills")
# .agents/skills/review が無ければ result.errors に上記エラーが入る
```

```console
$ kaji validate .kaji/wf/official/dev.yaml
✗ .kaji/wf/official/dev.yaml
  - Step 'review' uses agent 'codex' but skill 'review' is not discoverable by Codex: ...
```

## 変更スコープ

| ファイル | 変更 |
|----------|------|
| `kaji_harness/prompt.py` | 先頭行（skill invocation 行）を backend で分岐する。codex 用の invocation 行を返す helper を公開し、wrapper 経路と共有する |
| `kaji_harness/preflight.py` | codex step（`exec_script` を持たない agent 経路のみ）に `.agents/skills` の存在検証を追加する |
| `kaji_harness/skill.py` | Codex の探索ディレクトリ定数（`.agents/skills`）を置く。検証には既存の `validate_skill_exists` を再利用する |
| `kaji_harness/runner.py` | step の実効 workdir 解決（`step.workdir or workflow.workdir or project_root`）を preflight と共有できる helper に切り出す。interactive_terminal 呼び出しに invocation 行を渡す |
| `kaji_harness/interactive_terminal.py` / `interactive_terminal_herdr.py` | `_build_wrapper_command` に第 10 引数（invocation 行。codex 以外は空文字）を追加する |
| `kaji_harness/assets/interactive-terminal/wrapper.sh` | 第 10 引数が空でなければ `initial_prompt` の先頭に付ける |
| `.agents/skills/review` | `../../.claude/skills/review` への symlink を追加する（同根の壊れ箇所 1） |
| tests / docs | テスト戦略・影響ドキュメント参照 |

Python 単一スタック。リファクタは workdir 解決 helper の切り出しにとどめ、ほかは混ぜない。

## 制約・前提条件

- Claude / Antigravity の prompt（headless の argv、`prompt.txt`、interactive の初期メッセージ）は
  1 文字も変えない（Issue「既に決定済み」）
- skill 名の検証は既存の `validate_skill_exists` をそのまま使う。`..` を含む名前は拒否し、
  resolve 後のパスが workdir の外に出たら `SecurityError` にする。`.agents/skills` 側の検証にも
  同じ実装を使う。`.agents/skills/<skill>` が workdir 外を指す symlink なら、resolve 後に外へ
  出るのでエラーになる。repo 内 canonical への symlink（kaji の現行構成）は通る
- D2 は非互換変更である（人間が承認済み）。`.agents/skills` なしで codex step を動かしていた
  利用 repo は、preflight で失敗するようになる
- 検証は `<workdir>/.agents/skills/<skill>/SKILL.md` の 1 箇所だけを見る（D2 の文言どおり）。
  Codex 自体は cwd から repo root までの各階層と `$HOME/.agents/skills` なども探索するが、kaji は
  それらを合格扱いにしない。user / admin スコープの skill は repo の外にあり、workflow の再現性を
  保証できないためである
- Codex の最低 version は強制しない。確認済み version（codex-cli 0.159.2）を docs に記録する（A4）
- codex の `exec_script` skill は agent を起動しない（`agent` は無視される）。そのため
  `.agents/skills` 検証の対象外にする
- 依存ライブラリの追加はない

## 方針

### 1. prompt の backend 分岐（D1）

```python
def skill_invocation_line(step: Step) -> str:
    assert step.skill is not None
    if step.agent == "codex":
        return f"${step.skill} を実行してください。"
    return f"スキル `{step.skill}` を実行してください。"
```

- `build_prompt` の先頭行をこの helper の戻り値にする。2 行目以降は変えない
- resume でも毎 attempt `build_prompt` を呼び、`_build_codex_args` が最終位置引数に付ける。
  したがって新規・resume の両経路に同じ形式が自動で乗る。`_build_codex_args` 自体は変えない
- 文言は事前調査 #2・#3 で実機確認した `$secret-token を実行してください。` と同じ構造にする。
  `$<skill>` の直後に半角スペースを置き、mention の終端を明確にする

### 2. preflight の Codex 探索パス検証（D2）

`preflight_workflow` のループで、既存の canonical 検証と metadata 取得に成功した後に判定する。

```python
if step.agent == "codex" and metadata.exec_script is None:
    codex_root = resolve_step_workdir(step, workflow, project_root)
    try:
        validate_exists(step.skill, codex_root, CODEX_SKILL_DIR)   # 既存 seam を再利用
    except (SkillNotFound, SecurityError) as exc:
        errors.append(f"Step '{step.id}' uses agent 'codex' but skill '{step.skill}' "
                      f"is not discoverable by Codex: {exc}. ...対処...")
```

- 検証関数には、既存の注入 seam（`skill_exists_validator`）を使う。runner テストが
  `kaji_harness.runner.validate_skill_exists` を patch している既存の境界を、そのまま流用できる
- canonical の検証が失敗した step は従来どおり `continue` する。同じ step に二重のエラーは出さない
- preflight は `kaji run`（`runner._collect_skill_metadata`）、`kaji validate`、`kaji recover`、
  series loader の共通入口である。ここに入れれば全経路で、Codex を起動する前に止まる

### 3. 検証の root は step の実効 workdir

- Codex の起動 cwd は `_resolve_settings` が決める `step.workdir or workflow.workdir or
  project_root` である。headless では `-C <workdir>` と `cwd=workdir`、interactive では
  wrapper の `cd` と `--cd` で使う。D2 は「Codex 側の探索パス」を検証すると決めているので、
  同じ解決結果を root にする
- 解決ロジックは `resolve_step_workdir(step, workflow, project_root) -> Path` として 1 箇所に
  切り出し、runner と preflight で共有する。二重実装はしない
- workdir 指定のない workflow（kaji の全 workflow がこれに当たる）では
  `project_root` になる。既存の canonical 検証と同じ root である

### 4. interactive_terminal の codex 経路（A3 の詳細化）

- `prompt.txt` は `build_prompt` を共有するので、先頭行は自動で `$<skill>` 形式になる
- そのうえで、Codex TUI が user 入力として受け取る wrapper の `initial_prompt` の先頭にも、
  方針 1 の helper が返す invocation 行を付ける。runner が wrapper の第 10 位置引数
  `skill_invocation` として渡す（codex のときだけ非空。claude / antigravity は空文字）
- wrapper は第 10 引数が空でなければ `initial_prompt="<invocation>\n\n<従来文面>"` とする。
  空なら従来どおり。mention の書式は Python 側の helper にだけ置き、bash 側では組み立てない
- TUI が初期の位置引数 prompt の `$` mention を解決するかは、実機で確かめていない（A3 の注記）。
  解決しなくても、文字列が 1 行増えるだけで現状より悪くはならない。また preflight（D2）は
  interactive 経路にも効く

### 5. repo 側の整合

- `.agents/skills/review -> ../../.claude/skills/review` を追加する。D2 導入後も kaji 自身の
  `make validate-workflows` が通るようにするためである
- 既存テストの fixture のうち、codex step を実 preflight に通すもの（例:
  `tests/test_cli_validate.py` の codex を含む YAML）には `.agents/skills/<skill>/SKILL.md` を追加する。
  承認済みの非互換に伴う期待値の更新であり、検証を弱める変更ではない

## 重要判断 provenance

| 判断 | 方針 | 出典または仮定 | 設計で行った詳細化 |
|------|------|----------------|--------------------|
| D1: Codex での skill 指定方法 | `$<skill>` のみで指定する。SKILL.md は注入しない。新規と resume の両方に適用する | 人間決定。Issue 本文 `## 決定事項` D1。出典は Claude Code session `fc7edc19-2ecd-4319-8fcb-746f1fed9e4e` と `## grill-me provenance` コメント | 先頭行を `$<skill> を実行してください。` に固定した（事前調査 #2・#3 の実機形式）。resume は `build_prompt` の再生成で同じ形式になるため、`_build_codex_args` は変えない |
| D2: fail-open 対策 | codex step の実行前に preflight で `<workdir>/.agents/skills/<skill>/SKILL.md` を検証し、なければエラーにする（非互換を承認済み） | 人間決定。Issue 本文 `## 決定事項` D2。出典は同 session と provenance コメント | 実装位置を `preflight_workflow`（4 経路の共通入口）にした。`exec_script` skill は対象外にした。既存の `validate_skill_exists` と注入 seam を再利用した。エラー文の必須要素を決めた |
| D2 の `<workdir>` をどこと解釈するか | step の実効 workdir（`step.workdir or workflow.workdir or project_root`） | AI の仮定。根拠: D2 は「Codex 側の探索パス」と明記しており、Codex の cwd はこの解決結果である。workdir 未指定の workflow では `project_root` と一致する。検査先: review-design / review-code | 解決ロジックを `resolve_step_workdir` に切り出し、runner と共有する |
| `.agents/skills/review` の追加 | symlink を追加する | 既存規約の適用。`docs/dev/skill-authoring.md:13` が `.agents/skills` を canonical への symlink として構成すると定めている。D2 導入後に kaji 自身の workflow を動かし続けるのに必須 | `review` 1 件だけを追加する（codex step の全 skill を照合した結果） |
| 既決: Claude / Antigravity の prompt を変えない | 変えない | Issue 本文「既に決定済み」 | headless・`prompt.txt`・interactive 初期メッセージの 3 面で不変であることをテストで固定する |
| 既決: skill 名検証の維持 | `..` 拒否と workdir 外への escape 拒否を `.agents/skills` にも適用する | Issue 本文「既に決定済み」と完了条件 | 既存関数をそのまま使い、新しい検証ロジックは書かない |
| 既決: AGENTS.md に詳細を増やさない | AGENTS.md は変えない | Issue 本文「既に決定済み」 | 運用契約は `docs/dev/skill-authoring.md` に書く（A6） |
| A3: interactive_terminal の codex 経路 | 同じ `$<skill>` 形式を適用する | AI の仮定（Issue 本文 A3）。検査先: review-design / PR review | `prompt.txt` は共有で自動適用される。ただし user 入力は wrapper の `initial_prompt` なので、そこにも invocation 行を付ける（wrapper 第 10 引数）。TUI での `$` の実機確認は残存リスクとして記録する |
| A4: Codex の最低 version | 強制しない。docs に 0.159.2 を記録する | AI の仮定（Issue 本文 A4） | `docs/dev/skill-authoring.md` に「確認済み version」として記載する |
| A5: 統合テスト | `large` marker を付ける。`codex` がない・未認証なら skip する | AI の仮定（Issue 本文 A5） | skip 判定は `shutil.which("codex")` と `codex login status` の exit code にする（0.159.2 で存在を確認済み）。実行は project / git の外の一時 dir で行う |
| A6: 運用契約の記載先 | `docs/dev/skill-authoring.md` | AI の仮定（Issue 本文 A6） | 「ファイル配置」節に、codex step での `.agents/skills/<skill>/SKILL.md` 必須化、`$<skill>` 形式、確認済み version を追記する |
| Large テストの argv と sandbox 指定 | production の `build_cli_args` を `execution_policy="interactive"` で使う。新規・resume とも sandbox 引数は付けない。sandbox resume の不具合は #458 に分離する | AI の判断。根拠: codex-cli 0.159.2 の `codex exec resume` は `-s` を受け付けない（設計レビュー M1 の実測と `codex exec resume --help`）。`_build_codex_args` の修正は本 Issue の完了条件の外にある。検査先: verify-design / review-code | 新規・resume の具体的 argv を設計に固定した |
| frontmatter `name` とディレクトリ名の一致を検証するか | 今回は検証しない（スコープ外） | AI の判断。根拠: D2 が承認した非互換は「探索パスの存在」だけである。name の不一致まで検査すると承認範囲を超えて非互換が広がる。kaji repo では全件一致を確認済み。検査先: review-design | 残存リスクとして記録し、必要なら別 Issue で扱う |

one-way door の未決はない。D1・D2 は人間決定済みである。上の AI 仮定は、いずれも誤っていても
review / PR で安く直せる範囲（内部 helper、wrapper の内部引数、テストの skip 条件、docs の記載先）に
収まっている。

## テスト戦略

### 変更タイプ

実行時コード変更（prompt 生成、preflight 検証、interactive wrapper）。

### 実行時コード変更の場合

#### Small テスト

- **再現テスト（修正前は Red）**: agent=codex の step で `build_prompt` の先頭行が
  `$<skill> を実行してください。` になる。現行実装ではバッククォート形式なので FAIL する
- Claude / Antigravity の step では、先頭行が従来の ``スキル `<skill>` を実行してください。`` と
  完全一致する（回帰ガード）
- 2 行目以降（セッション開始プロトコル、コンテキスト変数、出力要件）は backend で変わらない
- `build_cli_args`（codex）: 新規（`session_id=None`）でも resume（`session_id` あり）でも、
  最終位置引数が `$<skill> ` で始まる。prompt を外部から与えるのではなく、`build_prompt` の出力を
  通して検証する
- `skill_invocation_line` は codex のときだけ `$` 形式を返し、それ以外は従来形式を返す
- `resolve_step_workdir`: step.workdir > workflow.workdir > project_root の優先順位

#### Medium テスト

- **再現テスト（修正前は Red）**: `.claude/skills/<skill>/SKILL.md` だけがある codex step を
  `preflight_workflow` に通すと、errors に step id・skill 名・`.agents/skills/<skill>/SKILL.md`
  を含むエラーが入る。現行実装では errors が空なので FAIL する
- `.agents/skills/<skill>` が canonical への symlink なら通る（kaji の構成）
- claude / antigravity step は `.agents/skills` がなくても通る（新しい要件を課さない）
- `exec_script` を宣言した skill を agent=codex で参照しても、`.agents/skills` 検証は行わない
- path traversal: `..` を含む skill 名の codex step は、canonical 検証で SecurityError になる。
  `.agents/skills/<skill>` が workdir 外のディレクトリを指す symlink のとき、`.agents/skills` 側の
  検証が SecurityError（escape）になり、エラーとして集約される
- `step.workdir` を指定した codex step は、project_root ではなくその workdir の `.agents/skills` で
  検証する
- runner 経由: `.agents/skills` がない codex step の workflow を `WorkflowRunner.run()` すると
  `WorkflowValidationError` で止まり、`execute_cli` / `execute_interactive_terminal` は呼ばれない
  （実ファイルで検証する。`validate_skill_exists` は patch しない）
- `kaji validate` / `kaji recover` / series loader が同じエラーを出す（preflight 共通化の確認。
  代表として `kaji validate` を 1 件）
- 既存の `tests/test_cli_validate.py::test_official_workflows_all_validate` と
  `make validate-workflows` が、`.agents/skills/review` の追加後に通る。D2 だけを入れて symlink を
  入れない状態では落ちる。これが同根の壊れ箇所 1 の回帰検出になる
- `_build_wrapper_command`（tmux / herdr 共通）の argv の第 10 要素が、codex なら invocation 行、
  それ以外なら空文字になる（文字列構築のみ。Small に分類する）
- interactive wrapper を実 subprocess（bash + fake agent）で起動するテストは、規約に合わせて
  `large` + `large_local` に分類する（既存の fake agent で argv を記録するパターンを流用）:
  - codex で第 10 引数を与えると、初期メッセージが `$<skill> を実行してください。` で始まり、
    その後ろに従来の `Read the full task prompt from:` が続く。新規と resume の両方を確認する
  - claude / antigravity（第 10 引数は空）の初期メッセージは `Read the full task prompt from:` で
    始まる（従来どおり）

#### Large テスト

- `tests/test_codex_skill_invocation_large.py`（`@pytest.mark.large`）
  - skip 条件: `shutil.which("codex") is None`、または `codex login status` が非 0 で終わる（A5）。
    引数エラー（exit 2）などの CLI 失敗は skip にせず FAIL とする
  - 準備: `outside_project_tmp_path` に `git init` した repo を作る。次の 2 ファイルを置く。
    project / git の外に置くので、kaji repo 自身の `.agents/skills` は探索範囲に入らない
    - `.agents/skills/kaji-fixture-token/SKILL.md`: frontmatter に `name: kaji-fixture-token` と
      `description` を持ち、本文は固定トークンを返す指示
    - `.agents/skills/kaji-fixture-token/agents/openai.yaml`: `policy: allow_implicit_invocation: false`
  - prompt: production の `skill_invocation_line(step)`（`$kaji-fixture-token を実行してください。`）の
    後ろに短い指示を続ける。step は `agent="codex"`、`skill="kaji-fixture-token"`、model / effort は未指定
  - argv: production の `build_cli_args` を `execution_policy="interactive"` で呼ぶ。
    `_build_codex_args` は interactive では sandbox / approval の引数を付けないので、新規・resume
    ともに codex-cli 0.159.2 が受け付ける引数だけになる
    - 新規: `build_cli_args(step, prompt, workdir, None, "interactive")`
      → `["codex", "exec", "--json", "-C", <workdir>, <prompt>]`
    - resume: `build_cli_args(step, prompt2, workdir, <新規の thread.started の thread_id>, "interactive")`
      → `["codex", "exec", "resume", <thread_id>, "--json", <prompt2>]`
      （`codex exec resume --help` の Usage `[OPTIONS] [SESSION_ID] [PROMPT]` と `--json` に合致）
  - 実行: 両方とも `subprocess.run(argv, cwd=workdir, stdin=DEVNULL, capture_output=True, timeout=180)`。
    sandbox は CLI 引数で指定せず、codex の既定 sandbox に任せる。fixture repo は使い捨てで、
    prompt はトークンの応答だけを求める
  - 検証: 新規・resume とも exit code 0、最終 agent message に固定トークンが含まれる。新規では
    `command_execution` item が出ないことも確認する。これは、ファイル探索ではなく skill が
    注入されたことの証跡になる（事前調査 #1 と #2 を分ける観測点）
  - `sandbox` policy を使わない理由: 現行の `_build_codex_args` は resume でも `-s workspace-write`
    を付ける。codex-cli 0.159.2 の `codex exec resume` は `-s` を受け付けない（exit 2、
    `unexpected argument '-s' found`。設計レビューで実測）。これは本 Issue の対象（prompt の invocation
    形式と preflight）とは別の、既存の production 不具合である。そこで #458 として起票し、本 Issue
    では `_build_codex_args` を変えない。Large テストは production builder を通したまま、両経路で
    有効な policy を選ぶ。#458 の修正時に sandbox 経路の引数テストを追加する

### 恒久テストとして残す理由

prompt の形式と preflight の検証は Codex との公開挙動の境界であり、将来の prompt 整理や preflight
変更で再発しうる。Small / Medium は Codex を起動せずに CI で常に回るので、回帰シグナルとして残す。
Large は Codex の挙動変化（`$` mention の仕様変更）を検出する唯一の手段なので、認証済み環境で回す。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定はない。既存の skill 配置規約の強化にとどまる |
| docs/ARCHITECTURE.md | あり | `:59` の「`.agents/skills/` はシンボリックリンクとして構成する」に、codex step では preflight で必須検証されることを 1 文追記する |
| docs/dev/skill-authoring.md | あり | 運用契約の正本（A6）。codex step の `$<skill>` 形式、`.agents/skills/<skill>/SKILL.md` の必須化と preflight エラー、確認済み codex-cli version（0.159.2）を「ファイル配置」節に追記する。存在検査で保証する範囲（`.agents/skills/<skill>/SKILL.md` の存在と traversal 防御のみ。frontmatter `name` の不一致は検査しない）も短く書く |
| docs/dev/shared_skill_rules.md | あり | `:127` の「必要なら `.agents/skills/` に symlink を追加する」を、codex step で使う skill では必須、に改める |
| docs/dev/workflow_overview.md | あり | `:55` の「必要に応じて symlink で追随する」を同じ趣旨に改める |
| docs/reference/ | なし | 設定キーと公開 API は変えない |
| docs/cli-guides/interactive-terminal-runner.md / `.ja.md` | あり | wrapper の位置引数（第 10 引数 `skill_invocation`）と、codex の初期メッセージ先頭に invocation 行が付くことを追記する（lifecycle 手順 4） |
| docs/guides/python-starter.md | なし（本 Issue） | starter repo の `.agents/skills` 構成は Release 後の `/update-starter` で追随する |
| CHANGELOG.md | あり | `[Unreleased]` に BREAKING エントリを追加する。codex step で `.agents/skills/<skill>/SKILL.md` が必須になった（D2）。あわせて Fixed（Codex prompt の明示 invocation）を記載する |
| AGENTS.md / CLAUDE.md | なし | Issue の既決事項により詳細を増やさない |

## 残存リスク

- Codex TUI が初期の位置引数 prompt の `$` mention を解決するかは、実機で確認していない（A3）。
  解決しなくても悪化はしない。PR 後に interactive_terminal + codex で 1 回実行して確かめることを
  推奨する
- skill の frontmatter `name` とディレクトリ名が一致しない利用 repo では、`$<dir名>` が解決されず
  fail-open が残る（今回のスコープ外）
- `execution_policy: sandbox` の codex resume は、本 Issue の変更と無関係に引数エラーになる（#458）。
  Large テストはこの経路を使わない
- Large テストはモデル出力に依存する。`command_execution` が出ないことの assert が不安定になった
  場合は、トークン一致を主判定に残し、tool 呼び出しの assert を緩めることを review で検討する

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| OpenAI: Build skills（Codex skills 公式。`https://developers.openai.com/codex/skills/` は 308 でここへ転送される） | https://learn.chatgpt.com/docs/build-skills | 「**Explicit invocation:** Include the skill directly in your prompt.」「In Codex CLI or the IDE extension, run `/skills` or type `$` to mention a skill.」「`allow_implicit_invocation` (default: `true`): When `false`, Codex won't implicitly invoke the skill based on user prompt; explicit `$skill` invocation still works.」「Codex scans `.agents/skills` in every directory from your current working directory up to the repository root.」（REPO: `$CWD/.agents/skills` ほか）「Codex supports symlinked skill folders and follows the symlink target when scanning these locations.」→ D1 の `$` 形式、`.agents/skills` の検証パス、symlink 構成が有効であることの根拠 |
| 事前調査コメント（codex-cli 0.159.2 実機） | https://github.com/apokamo/kaji/issues/408#issuecomment-5914559295 | §2 #1: 現行形式は探索経由。#2: `$name` は `codex exec` で即答。#3: resume でも有効。#4: 未解決の `$name` は fail-open。公式 docs は `codex exec` を明記していないので、その有効性はこの実機証跡で裏付ける |
| grill-me provenance コメント | Issue #408 コメント `## grill-me provenance` | D1「A: `$<skill>` のみ」、D2「preflight で検証して実行前にエラー」は起票者の選択。D2 の非互換は選択肢に明記したうえで承認された |
| 現行 prompt 生成 | `kaji_harness/prompt.py:87` | backend 分岐のない ``スキル `{step.skill}` を実行してください。`` |
| 現行 skill 検証 | `kaji_harness/skill.py:30-58`、`kaji_harness/preflight.py:77-89` | canonical の `skill_dir` だけを検証する。`..` 拒否と workdir 外への escape 拒否の実装 |
| Codex argv 構築 | `kaji_harness/cli.py:442-462` | 新規は `codex exec --json -C <workdir> … <prompt>`、resume は `codex exec resume <id> --json … <prompt>` |
| 実効 workdir | `kaji_harness/runner.py:460-473` | `step.workdir or workflow.workdir or project_root` |
| interactive wrapper | `kaji_harness/assets/interactive-terminal/wrapper.sh:43-50,76-89` | Codex TUI の user 入力は `initial_prompt` であり、`prompt.txt` の内容ではない |
| skill 配置規約 | `docs/dev/skill-authoring.md:11-31` | `.agents/skills/` は canonical への symlink として構成する |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L の判定基準、`outside_project_tmp_path` の使い方 |
