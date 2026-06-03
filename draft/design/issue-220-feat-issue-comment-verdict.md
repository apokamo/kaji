# [設計] artifact verdict.yaml を primary とする verdict 受け渡し経路

Issue: #220

## 概要

`kaji run` の各 agent / script step の verdict を、stdout 依存ではなく
**artifact の `verdict.yaml`（primary）→ 作業報告 Issue comment の `---VERDICT---` block（fallback）→ stdout parse（互換 fallback）** の順で解決できるようにする。
あわせて artifact/log layout を `runs/<run_id>/steps/<step_id>/attempt-NNN/` 構造へ移行し、verdict を attempt 単位で保存する。

## 背景・目的

### ユースケース

- **kaji 利用者として**、agent が stdout を直接返さない実行方式（別ターミナル起動 / 通常 interactive CLI / 人間による handoff）でも、workflow step の verdict を artifact から確実に回収したい。
- **workflow author として**、Claude / Codex / human handoff の違いに依らず、同じ artifact verdict 形式（`verdict.yaml`）で step 完了を表現したい。
- **maintainer として**、各 step の作業報告と verdict が Issue 上にも履歴として残り、後から「なぜ PASS / RETRY / ABORT したか」を追跡したい。

### 現状の問題

現在の harness は agent stdout から verdict を抽出して workflow 遷移している
（`kaji_harness/runner.py:519-523` の `parse_verdict(result.full_output, ...)` が唯一の verdict source）。
`docs/dev/skill-authoring.md` § verdict 出力規約 でも「verdict は stdout にそのまま出力」が唯一の契約になっている。

今後 `kaji` から Claude Code / Codex を subscription の通常コンソール利用に近い形で使うには、headless stdout を直接読む方式だけでは制約が強い。
`draft/lab/headless-terminal-spawn/design.md` の PoC では別ターミナルで agent を起動し sentinel / output file で回収する方針が示されているが、workflow の正規 verdict 経路としては未実装である。stdout に依存しない完了判定経路が必要。

### 代替案と不採用理由

| 代替案 | 不採用理由 |
|--------|-----------|
| Issue comment を verdict の primary source にする | comment は人間向け作業報告履歴であり、attempt 境界・取得タイミングが API 依存で不安定。primary には不適（Issue § 認識違いを避けるための明確化）|
| verdict 専用 Issue comment を新規作成する | 作業報告 comment と分離すると投稿数が倍増し追跡性が下がる。既存作業報告 comment の末尾追記で十分（同上）|
| harness が agent stdout を parse して verdict.yaml を書くだけ（agent は書かない） | future の no-stdout runner では harness に stdout が無く成立しない。agent 側が書ける契約が前提（§ 方針で両立させる）|

## インターフェース

### 入力

#### `verdict.yaml`（artifact 保存形式 / 新規）

attempt directory に置く **pure YAML**。`---VERDICT---` delimiter は付けない。

```yaml
status: PASS
reason: |
  設計書と整合し品質基準を満たす
evidence: |
  ruff / mypy / pytest すべて pass
suggestion: ""
```

- `status`: 必須。当該 step の `on:` キー（valid_statuses）のいずれか。
- `reason`: 必須（空文字不可）。
- `evidence`: 必須（空文字不可）。
- `suggestion`: `ABORT` / `BACK*` のとき必須。`PASS` / `RETRY` では任意（空文字可）。
- run_id / step_id / attempt_id は **保存しない**（attempt path 自体が現在の run / step / attempt を表す。Issue § 認識違いを避けるための明確化）。

検証規則は既存 stdout verdict と同一（`verdict.py:_validate` を再利用）。

#### 作業報告 Issue comment 末尾の `---VERDICT---` block（fallback source / 契約変更）

skill が投稿する作業報告 comment の末尾に、既存 stdout 形式と同じ block を追記する。

```markdown
（実施内容・確認結果・残課題などの作業報告本文）

---VERDICT---
status: PASS
reason: |
  ...
evidence: |
  ...
suggestion: ""
---END_VERDICT---
```

#### harness が agent に注入する保存先パス（新規 prompt 変数 / env）

| 経路 | 注入方法 | 値 |
|------|----------|-----|
| agent step | prompt 変数 `verdict_path` | 当該 attempt の `verdict.yaml` 絶対パス |
| exec_script step | env `KAJI_VERDICT_PATH` | 同上 |

agent / script はこのパスへ `verdict.yaml` を書く。worktree 外（main worktree の `.kaji-artifacts/` 配下）の絶対パスになるため、相対パスでなく絶対パスで渡す。

### 出力

#### artifact/log layout（新構造）

```text
.kaji-artifacts/<issue>/
  session-state.json
  progress.md
  runs/<run_id>/
    run.log
    steps/<step_id>/
      attempt-001/
        prompt.txt          # agent step のみ。build_prompt 結果を保存
        stdout.log          # 生の CLI stdout（既存 stream_and_log 出力）
        console.log         # decode 済みテキスト（既存）
        stderr.log          # 既存
        verdict.yaml        # resolve 後に harness が正規化保存（agent が書いていればそれを採用）
      attempt-002/
        ...
      latest -> attempt-002  # 人間 / 外部ツール向け convenience symlink（best-effort）
```

- `run_id` は既存どおり `datetime.now().strftime("%y%m%d%H%M")`（minute 精度）。
- step log は従来 `runs/<run_id>/<step_id>/` だったものを `runs/<run_id>/steps/<step_id>/attempt-NNN/` に移す。
- attempt 番号は当該 run 内で step が cycle / retry により複数回 dispatch されるたびに増える（`steps/<step_id>/` 配下の既存 `attempt-*` 数 + 1）。
- `latest` symlink は最新 attempt を指す **convenience**。harness の verdict 解決は in-memory で保持した attempt path を直接使い、`latest` には依存しない（symlink 非対応 FS でも解決が壊れない）。

#### verdict 解決結果（戻り値・遷移）

harness は解決した `Verdict` で従来どおり workflow 遷移する。解決経路（artifact / comment / stdout）を `run.log` に記録する。

### 使用例

```python
# runner 内部（擬似コード）
attempt_dir = allocate_attempt_dir(run_dir, step.id)   # steps/<step_id>/attempt-NNN/
# build_prompt に verdict_path を渡し、出力要件に保存先を埋め込む（prompt 生成が先）
prompt = build_prompt(step, ..., verdict_path=str(attempt_dir / "verdict.yaml"))
write_prompt_txt(attempt_dir, prompt)                  # 生成済み prompt を保存（agent step のみ）

attempt_started_at = now()   # dispatch 直前に記録。comment fallback の lower bound
result = execute_cli(step=step, prompt=prompt, log_dir=attempt_dir, ...)

verdict, source = resolve_verdict(
    attempt_dir=attempt_dir,
    full_output=result.full_output,
    valid_statuses=set(step.on.keys()),
    attempt_started_at=attempt_started_at,   # comment を現在 attempt 以降に scope
    comment_loader=lambda: _load_comments(provider, issue_id),  # 遅延。artifact 不在時のみ呼ぶ。body+created_at を返す
    ai_formatter=formatter,
)
if source != "artifact":
    write_verdict_yaml(attempt_dir / "verdict.yaml", verdict)  # 正規化保存
```

### エラー

| 状況 | 挙動 |
|------|------|
| `verdict.yaml` が壊れた YAML / 必須欠落 / invalid status | `VerdictParseError` / `InvalidVerdictValue`。fail-loud（comment / stdout への自動 fallthrough はしない。artifact が存在する以上それが意図された source）|
| `verdict.yaml` 不在 + comment に block 無し + stdout に delimiter 無し | 既存どおり `VerdictNotFound`（fabrication 防止。`verdict.py:294-298` の方針を維持）|
| comment fallback で provider 取得失敗（GitHub API / local read エラー） | comment source を skip し stdout parse へ。取得失敗は WARN ログ。fail-loud は最終 stdout 経路が担う |
| exec_script 経路 | AI formatter fallback は呼ばない（`runner.py:507-510` 維持）。verdict.yaml → comment → stdout(plain) の順で解決 |

> **fail-loud の方針**: `verdict.yaml` が「存在するが壊れている」場合は誤魔化さず例外にする。これは Issue #193 で確立した「delimiter があるのに中身が verdict として不成立なら fail-loud」の artifact 版。

## 制約・前提条件

- 既存 `kaji_harness/verdict.py` の parser（STRICT / RELAXED パターン、`_parse_yaml_fields`、`_validate`）を再利用し、新規 parser を作らない。
- comment 取得は既存 `provider.view_issue(issue_id).comments` を再利用する。comment 投稿・metadata 詳細保存・代理投稿の新規実装はしない（Issue § 認識違いを避けるための明確化）。
- comment は GitHub（`gh issue view --json comments`）/ local（comment ファイル）ともに **投稿順（時系列昇順）** で返る前提。resolver は `created_at >= attempt_started_at` の comment に絞った上で newest-first に走査する（comment fallback の attempt scoping。詳細は § 方針 2）。
- artifact path 解決は既存 `resolve_artifacts_dir(config)`（main worktree 基準）を変えない。
- run layout 参照は `runner.py` に限局している（`grep` で確認済。`workflow.py:75` の `steps` は workflow YAML 用で無関係）。layout 変更の影響範囲は runner と runner 系テストに収まる。
- `run_id` の minute 精度衝突は本 Issue の scope 外（既存制約）。同一 run_id 内でも attempt-NNN で step 実行ごとに分離されるため、本機能の正しさには影響しない。
- パフォーマンス: comment fallback は **artifact 不在時のみ** provider 呼び出しする（happy path で余計な API hit を出さないよう、`comment_loader` を遅延 callable にする）。

### スコープ境界（Issue 準拠）

含まない: 課金・認証方式の変更 / interactive terminal runner・tmux runner の本実装 / 既存 provider の unrelated CRUD 変更 / docs-only 改定 / unrelated parser・adapter の全面リファクタ / artifact と comment の厳密同一性検証 / comment metadata 詳細保存 / harness による comment 代理投稿。

## 変更スコープ

| ファイル | 変更内容 |
|----------|----------|
| `kaji_harness/verdict.py` | `load_verdict_yaml()` / `write_verdict_yaml()` / `parse_verdict_block()`（delimiter 抽出のみ、AI formatter 無し）を追加 |
| `kaji_harness/runner.py` | attempt dir 採番 / prompt.txt 保存（build_prompt 後）/ dispatch 直前の `attempt_started_at` 記録 / verdict 解決順（artifact→comment→stdout）/ 正規化保存 / layout を `steps/<step_id>/attempt-NNN/` へ |
| `kaji_harness/prompt.py` | `verdict_path` 引数追加。`## 出力要件` を「verdict.yaml 保存 + comment 末尾追記 + stdout 互換出力」に書き換え。`## コンテキスト変数` に `verdict_path` 追加 |
| （新規 or runner 内） verdict resolver | `resolve_verdict(attempt_dir, full_output, valid_statuses, attempt_started_at, comment_loader, ai_formatter)` と attempt 採番 helper。comment fallback を `created_at >= attempt_started_at` に scope。runner に閉じてよいが、単体テスト容易性のため純関数として切り出す |
| `.claude/skills/` 共通契約 | 個別 skill は編集せず、prompt 注入（`prompt.py`）で契約を一元化。skill-authoring.md に契約を明記 |

> kaji は Python 単一スタック。backend / frontend の scope 分岐は無い。

## 方針（Minimal How）

### 1. verdict.py への純関数追加

- `load_verdict_yaml(path, valid_statuses) -> Verdict`: `path` を読み、`_parse_yaml_fields` + `_validate` を通す（delimiter 無しの pure YAML）。
- `write_verdict_yaml(path, verdict) -> None`: `Verdict` を `status/reason/evidence/suggestion` の pure YAML（`yaml.safe_dump`、block scalar 許容）で書く。round-trip 可能。
- `parse_verdict_block(text, valid_statuses) -> Verdict | None`: comment 本文から STRICT→RELAXED delimiter を抽出し `_parse_yaml_fields`+`_validate`。comment 契約が「末尾に追記」のため、**本文中で成立する最後（末尾）の block** を採用する（引用・過去ログ中の古い block を誤採用しない）。block 不在は `None`（comment fallback は best-effort のため例外にしない）。

### 2. verdict 解決順（resolver）

```text
resolve_verdict(attempt_dir, full_output, valid_statuses, attempt_started_at, comment_loader, ai_formatter):
  1. (attempt_dir/verdict.yaml) が存在 → load_verdict_yaml（壊れていれば raise / fail-loud）, source="artifact"
  2. else comments = comment_loader();
     current = [c for c in comments if c.created_at >= attempt_started_at]   # 現在 attempt 以降に scope
     current を newest-first で走査し parse_verdict_block で成立した最初の block, source="comment"
  3. else parse_verdict(full_output, ai_formatter=...)（既存 stdout 経路まるごと）, source="stdout"
```

- **artifact による attempt scoping**: 解決対象は常に **今 dispatch した attempt の dir** であり、artifact が存在すれば comment / stdout は **見ない**。よって stale comment が fresh artifact を上書きすることはない（核心の不変条件）。
- **comment fallback の attempt scoping（本修正の核心）**: artifact 不在時の comment fallback は、`attempt_started_at`（当該 attempt の dispatch 直前に harness が記録したローカル時刻）を lower bound とし、`Comment.created_at >= attempt_started_at` を満たす comment **のみ** を対象にする。これにより、retry / resume で当該 attempt が verdict.yaml も stdout verdict も出さなかった場合に、**前 attempt の作業報告 comment を誤採用しない**（Issue #220 完了条件「cycle / retry / resume 時に古い step verdict や別 attempt の verdict を誤採用しない」を comment 経路でも満たす）。
  - 前 attempt の comment は当該 attempt の dispatch 前に投稿済みのため `created_at < attempt_started_at` となり、構造的に対象外になる。
  - lower bound を満たす comment が 1 件も無ければ comment fallback は成立せず、stdout 経路（無ければ `VerdictNotFound`）へ進む。古い comment を拾うより「解決失敗」を選ぶ fail-safe 方針。
  - clock skew の扱い: `attempt_started_at` はローカル時刻、`Comment.created_at` は provider（GitHub はサーバ時刻 / local は投稿時刻）。skew により **本来採用すべき現在 comment を取りこぼす**方向のリスクは残るが、その場合も誤った古い verdict を採るのではなく stdout / `VerdictNotFound` へ落ちるため、workflow 遷移の正しさは壊れない。誤った遷移（false verdict）よりも解決失敗（fail-safe）を優先する設計とする。
  - comment 本文に run/step/attempt marker を埋め込む案は不採用。agent 挙動に依存し、Issue §「認識違いを避けるための明確化」の「comment metadata を増やさない」方針に反するため、harness 制御の `created_at` lower bound を採る。
- **複数 verdict block の扱い**: 作業報告 comment の契約は「末尾に verdict block を追記」（§ インターフェース）。引用・過去ログを含む comment で誤採用しないよう、`parse_verdict_block` は comment 本文中で成立する **最後（末尾）の block** を採用する（先頭ではない）。stdout 経路の `parse_verdict` は既存挙動を変えない（互換 fallback のため）。

### 3. runner の layout 変更

- `run_dir = artifacts/<issue>/runs/<run_id>/`（`run.log` はここ、不変）。
- 各 step dispatch 直前に `attempt_dir = allocate_attempt_dir(run_dir, step.id)` を採番（`runs/<run_id>/steps/<step_id>/` 配下の `attempt-*` 数 + 1 → `attempt-NNN` mkdir、`latest` symlink を best-effort で張り替え）。
- `attempt_dir` を `execute_cli` / `execute_script` の `log_dir` として渡す（stdout/console/stderr はこの下に出る）。
- agent step は build_prompt 結果を `attempt_dir/prompt.txt` に保存。exec_script は prompt 無し（env で注入済）。
- dispatch 後 `resolve_verdict`。`source != "artifact"` のとき `write_verdict_yaml` で正規化保存（legacy skill が stdout しか出さなくても attempt-NNN/verdict.yaml が必ず残る → 完了条件「verdict が attempt-NNN/verdict.yaml に保存される」を決定論的に満たす）。
- comment fallback 用 provider は既存 `provider = get_provider(self.config)`（`runner.py:267`）を再利用。

### 4. 契約の一元注入（prompt.py）

`build_prompt` の `## 出力要件` を次に変更（個別 skill は触らない）:

```text
## 出力要件
作業完了後、以下を必ず実施してください:

1. 次の YAML を `[verdict_path]` に保存する（pure YAML。---VERDICT--- delimiter は付けない）:
   status: <PASS | ...>
   reason: 判定理由
   evidence: 具体的根拠
   suggestion: 次アクション（ABORT/BACK 時必須）
2. 作業報告 Issue comment の末尾に、同じ内容を ---VERDICT--- block として追記する。
3. 互換のため、同じ ---VERDICT--- block を stdout にも出力する。
```

`## コンテキスト変数` に `verdict_path` を追加。stdout 出力（手順 3）を残すことで既存 stdout verdict テストと un-migrated 環境の互換を保つ。

### 5. 段階的廃止方針

stdout parse は当面の互換 fallback として残す。完全移行（全 agent が verdict.yaml を書く）後に stdout 経路の段階廃止を別 Issue で検討する旨を skill-authoring.md に注記する。

## テスト戦略

> **CRITICAL**: 本変更は実行時の振る舞いを変えるコード変更（feat）。Small / Medium で契約とファイル I/O を網羅し、Large は real-agent 非依存で wiring を検証する。

### 変更タイプ
- 実行時コード変更（runner / verdict / prompt の振る舞い変更 + 新規ファイル I/O）

### Small テスト（外部依存なし・純関数）

- `load_verdict_yaml`: 正常 YAML → `Verdict`。必須欠落 / invalid status / ABORT・BACK で suggestion 空 → 各例外。
- `write_verdict_yaml` → `load_verdict_yaml` の round-trip 一致（複数行 evidence の block scalar 含む）。
- `parse_verdict_block`: block 有り → `Verdict`、block 無し → `None`、invalid status block → 例外、複数 block → **末尾採用**（引用・過去ログ中の先頭 block を拾わない）の定義を検証。
- `resolve_verdict` 優先順位:
  - artifact 有り → artifact 採用かつ `comment_loader` が **呼ばれない**（happy path で API hit しない不変条件）。
  - artifact 無し + 現在 attempt 以降の comment 有り（`created_at >= attempt_started_at`）→ comment 採用。
  - **artifact 無し + 古い comment のみ（`created_at < attempt_started_at`）+ stdout 無し → 古い comment を採用せず `VerdictNotFound`**（comment fallback の attempt scoping 回帰テスト。Issue 完了条件の核心）。
  - artifact 無し + 古い comment のみ + stdout 有り → stdout 採用（古い comment は無視）。
  - artifact 無し + comment 無し + stdout 有り → stdout 採用。
  - 全滅 → `VerdictNotFound`。
  - artifact が壊れている → fail-loud（comment/stdout に落ちない）。
- attempt 採番 helper: 初回 `attempt-001`、再 dispatch で `attempt-002`、`latest` 更新（symlink 失敗時も例外を出さず採番継続）。

### Medium テスト（runner レベル・mock CLI + 実 filesystem）

- mock CLI が stdout verdict のみ返す（agent は verdict.yaml 未書き込み）→ harness が stdout で解決し `attempt-001/verdict.yaml` を正規化保存、遷移が正しい（= 既存 stdout 挙動の保全確認も兼ねる）。
- attempt dir に事前 verdict.yaml がある（agent が書いた想定）→ artifact-primary で解決し、stdout と内容が異なっても **artifact を採用**。
- **cycle / retry**: RETRY で同一 step が 2 回 dispatch → `attempt-001` と `attempt-002` が各々の `verdict.yaml` を持ち、遷移は **2 回目（attempt-002）** の verdict に従う（古い attempt を誤採用しないことの回帰テスト）。
- comment fallback（正常）: mock provider.view_issue が **現在 attempt 以降（`created_at >= attempt_started_at`）** の verdict block 付き comment を返し、mock CLI は stdout verdict も verdict.yaml も出さない → comment で解決。
- **comment fallback の stale 防止（resume 含む / 本修正の回帰テスト）**: 前 attempt の verdict block 付き古い comment（`created_at < attempt_started_at`）のみが存在し、当該 attempt は verdict.yaml も stdout verdict も出さない状況で、(a) retry 経由の 2 回目 dispatch、(b) `--from` による resume 経由の dispatch の双方について、**古い comment を採用せず `VerdictNotFound`** になることを確認する（artifact / stdout が無ければ古い comment へ遷移しない）。
- legacy layout 読み取り互換: `<issue>/runs/<旧run_id>/<step_id>/`（attempt 無し旧構造）が残存していても、新 run（新 run_id）が新 layout で正常完了し crash しない。

### Large テスト（large_local・real-agent 非依存）

- fake-agent shell script（`claude` を騙る stub）が `verdict.yaml` 書き込み + stdout 出力する状態で、実 CLI entrypoint 経由 `kaji run` を 1 step 実行し、artifact-primary 解決と layout（`steps/<step_id>/attempt-001/verdict.yaml`）を end-to-end 検証する。
- real LLM を使う Large は **不要**と判断: 本機能は file I/O + 解決ロジックであり、LLM 推論を介さずに Medium（実 filesystem + mock CLI）で完全に exercise できる。real-agent E2E は非決定性を増やすだけで新規回帰シグナルを生まない（`testing-convention.md` § 省略してよい理由「既存ゲートで不具合パターンを捕捉できる」+「real LLM 経路は本契約に新規回帰情報を加えない」）。GitHub comment fallback の実 API 疎通は既存 `test_providers_github.py` の `view_issue` 解析テストで担保済。

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | あり | verdict source を stdout-only → artifact-primary に変える設計決定 + attempt-NNN layout 採用。新規 ADR を起こす |
| docs/ARCHITECTURE.md | あり | § Verdict 判定機構 を artifact-primary + 解決順 + attempt layout に更新 |
| docs/dev/skill-authoring.md | あり | § verdict 出力規約 に verdict.yaml 保存 + comment 末尾追記契約、解決順、`verdict_path` 変数、stdout 段階廃止注記を追加 |
| docs/dev/shared_skill_rules.md | あり（軽微） | verdict 永続化（verdict.yaml + comment 末尾）の共通ルールを 1 項追加 |
| docs/reference/python/logging.md | あり（軽微） | RunLogger の step log 出力先が `steps/<step_id>/attempt-NNN/` に変わる旨を反映 |
| docs/dev/ (workflow 系) | なし | workflow YAML / 遷移仕様は不変 |
| docs/reference/python/ (style 系) | なし | コーディング規約変更なし |
| docs/cli-guides/ | なし | `kaji run` の CLI IF（引数・exit code）は不変 |
| CLAUDE.md | なし | プロジェクト規約・必読 docs 一覧に変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 既存 verdict parser | `kaji_harness/verdict.py:99-214, 294-298` | `_parse_yaml_fields` / `_validate` / STRICT・RELAXED パターンを再利用し新 parser を作らない。delimiter 不在の fail-loud（fabrication 防止）方針を artifact にも踏襲 |
| 既存 verdict 解決呼び出し | `kaji_harness/runner.py:505-523` | 現状 `parse_verdict(result.full_output, ...)` が唯一の source。ここを resolver に差し替える起点 |
| 現行 step log layout | `kaji_harness/runner.py:310-318, 418-419` | `runs/<run_id>/run.log` と `runs/<run_id>/<step_id>/`。後者を `steps/<step_id>/attempt-NNN/` に移す。参照は runner 限局 |
| stream_and_log の出力 | `kaji_harness/cli.py:198-260` | `log_dir` 配下に `stdout.log` / `console.log` / `stderr.log` を出力。`log_dir` を attempt_dir にすればそのまま新 layout に乗る |
| prompt の出力要件契約 | `kaji_harness/prompt.py:44-99` | `## 出力要件` で stdout verdict を指示。契約注入の一元ポイント。`verdict_path` 変数を足す |
| comment 取得経路 | `kaji_harness/providers/github.py:197-209`, `kaji_harness/providers/local.py:551`, `kaji_harness/providers/models.py:24-39,42-64` | `view_issue().comments`（`Comment.body` / `created_at`）が両 provider 共通。fallback はこれを再利用、新規 provider method 不要 |
| exec_script の verdict 契約 | `docs/dev/skill-authoring.md:101-109`, `kaji_harness/runner.py:507-510` | exec_script は AI formatter fallback を呼ばない。新 resolver でも同方針を維持 |
| Issue 決定方針 | 本 Issue #220 本文 §「決定方針: artifact primary / Issue comment fallback」「artifact/log layout」「認識違いを避けるための明確化」 | artifact primary / comment fallback / run_id・step_id・attempt_id を YAML に重複保存しない / 代理投稿しない等の制約の正本 |
| headless terminal spawn PoC | `draft/lab/headless-terminal-spawn/design.md` | stdout 非依存の完了判定（sentinel + output file）が必要という動機の一次メモ。本 verdict.yaml はその正規 verdict 経路にあたる |
| PyYAML safe_dump/safe_load | https://pyyaml.org/wiki/PyYAMLDocumentation | `verdict.yaml` の pure YAML 直列化・読込は `yaml.safe_load` / `yaml.safe_dump` を使用（任意オブジェクト構築を避ける安全 API）。既存 `verdict.py` も `yaml.safe_load` を採用 |
| testing-convention 省略条件 | `docs/dev/testing-convention.md:112-130` | real-agent Large 省略の正当化（既存ゲートで捕捉 + 新規回帰情報を増やさない）の根拠 |
