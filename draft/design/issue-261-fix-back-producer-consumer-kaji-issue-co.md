# [設計] BACK 判定コメントの producer/consumer 契約を `kaji issue comment` の verdict マーカーで恒久対応する

Issue: #261

## 概要

`issue-design` Step 1.6 の BACK 再入検出が参照するコメントパターンを producer スキルが一切出力しておらず、BACK 再入が常に「初回起動」と誤判定される契約不一致を、`kaji issue comment` への verdict マーカー付与機能（CLI 層の決定的契約）で恒久対応する。あわせて、この設計方針の根拠となる ADR 008（後方互換レイヤを提供しない）を本設計フェーズの成果物として作成する。

## 背景・目的

### Observed Behavior（OB）

consumer（`.claude/skills/issue-design/SKILL.md:187` 付近、Step 1.6 観測 3）は次のパターンを検出する:

```
\[x\] Changes Requested / BACK   または   \| *判定 *\|.*BACK
```

しかし producer 側の実出力はどちらにも一致しない:

- `.claude/skills/issue-review-code/SKILL.md:272-273`: 判定 checkbox は `[x] Approve (修正なしでマージ可)` / `[x] Changes Requested (要修正)` の 2 択のみ。**BACK verdict を出す場合のコメント上の表現がテンプレートに存在しない**（`/ BACK` サフィックスなし、`| 判定 | BACK |` テーブルもなし）
- `.claude/skills/i-dev-final-check/SKILL.md:287-289`: `### 判定` 見出し + `PASS / RETRY / BACK_DESIGN / BACK_IMPLEMENT / BACK` のメニュー形式テキスト行。`| 判定 |` テーブルではない

このため `BACK_COUNT` は構造的に常に 0 となり、BACK 経由の design 再入が「初回起動」と誤判定され、Step 2 の通常フローが**既存設計書を上書きしうる**（Step 1.6 自身が防ぐと明言している failure mode）。

静的再現（Issue 本文の再現手順を 2026-07-07 に本設計フェーズでも追試）:

1. `grep -n 'Changes Requested / BACK' .claude/skills/issue-design/SKILL.md` → 187 行にヒット（consumer の期待パターン）
2. `grep -rn 'Changes Requested / BACK\|| 判定 |.*BACK' .claude/skills/issue-review-code/ .claude/skills/i-dev-final-check/` → **0 件**（producer はこのパターンを出力しない）

### Expected Behavior（EB）

BACK 再入が producer / consumer 間で**決定的に**検出できること。根拠:

- `issue-design/SKILL.md` Step 1.6 の明記された目的（「初回起動を前提とした Step 2 以降の通常フローを素朴に実行すると scope 違反になる」ことの防止）
- ADR 008 決定 3（本設計フェーズで作成、後述）: スキルを跨ぐ契約は SKILL.md の散文ではなく CLI / harness 層（コード）に置く
- 既存慣行: `kaji_harness/providers/github.py:39-56` の `<!-- kaji-review: state=... -->` HTML コメントマーカー（`build_kaji_review_marker()`、`_REVIEW_STATES_VALID` による語彙検証、不正値は `ValueError` で fail-loud）

### 再現手順（steps-to-reproduce）

契約不一致は静的に再現可能（環境非依存・provider 非依存）:

1. 前提: kaji リポジトリの main（commit `1e37cf4` 時点）
2. consumer 期待パターンの確認: `grep -n 'Changes Requested / BACK' .claude/skills/issue-design/SKILL.md` → 1 件ヒット
3. producer 実出力テンプレートの確認: `grep -rn 'Changes Requested / BACK\|| 判定 |.*BACK' .claude/skills/issue-review-code/ .claude/skills/i-dev-final-check/` → 0 件
4. よって github / local いずれの provider でも、BACK verdict 後の design 再入で `BACK_COUNT=0` → 初回起動分岐 → 既存設計書の上書き経路に入る

### 根本原因（Root Cause）

**なぜ間違っているか**: producer（判定コメントのテンプレート）と consumer（検出 regex）が別々の SKILL.md の散文として二重管理されており、両者を突き合わせて整合を保証する機構が存在しない。consumer 側 regex は「producer がこう書くはず」という想定で書かれたが、producer テンプレートはその表現を一度も含んでいなかった。散文契約は silent に壊れる（不一致でもエラーにならず、検出ゼロ＝初回起動として振る舞う）。

**いつから壊れているか**: Step 1.6/1.7 は gl:22 で導入され（commit `f2a4e40`）、問題の regex `[x] Changes Requested / BACK` は同 Issue のレビュー反映 commit `34f26f9` で導入された。導入時点から producer 側テンプレートにこの表現は存在せず、**一度も機能したことがない**。#259（commit `0452612`）で jq comment-unit フィルタと fail-loud が改善されたが、regex と producer 出力の不一致自体は残った（#259 項目 2 として本 Issue に切り出し）。

**同根の他の壊れ箇所の調査結果**（2026-07-07 実施）:

| 箇所 | 調査結果 |
|------|---------|
| `issue-design` Step 1.7 サブステップ 2（直近 BACK verdict の特定） | 同じ regex + heading gate を使用。**同根・本 Issue で同時修正** |
| docs workflow（`i-doc-update`）の BACK 再入検出 | `grep -n 'BACK\|再起動' .claude/skills/i-doc-update/SKILL.md` → 0 件。BACK 再入検出そのものが存在しないため本バグの影響なし（`docs.yaml:115` の `BACK: doc-update` 再入は素通しで上書きしうるが、これは別の未実装課題であり本 Issue のスコープ外。マーカー契約は将来そのまま流用可能） |
| kaji-starter-python | #242 で tactical fix 済み（consumer regex を producer 出力へ整合）。恒久対応の還流 follow-up Issue を起票する（Issue 完了条件） |
| `review-poll`（PR レビュー polling） | codex bot の reactions / reviews を参照し、Issue 判定コメントを参照しない。影響なし |

## インターフェース

### 入力（CLI 拡張）

`kaji issue comment` に verdict マーカー付与フラグを追加する:

```
kaji issue comment <issue_id> [--body TEXT | --body-file PATH] [--commit] \
  --verdict-step <step> --verdict-status <STATUS>
```

| フラグ | 型 | 制約 |
|--------|-----|------|
| `--verdict-step` | str | `^[a-z][a-z0-9_-]*$`。発行元 step の識別子（例: `review-code` / `final-check` / `design`）。不一致は fail-loud（exit 2） |
| `--verdict-status` | str | `^(PASS\|RETRY\|ABORT\|BACK\|BACK_[A-Z0-9_]+)$`。標準 4 status + `BACK_*` 拡張（`docs/dev/workflow-authoring.md` § `BACK_*` プレフィックス拡張の文法 `[A-Z0-9_]+` と整合）。不一致は fail-loud（exit 2） |

制約:

- **両フラグは同時必須**。片方のみの指定は exit 2（stderr にエラー）。マーカーなし従来呼び出し（両方なし）は完全に不変
- github / local **両 provider で同一の振る舞い**（語彙検証・マーカー形式・付与位置）
- `--commit` の意味は従来通り（local: atomic commit / github: silent に無視）

### 出力（マーカー形式）

comment body の **1 行目** に以下の HTML コメントを決定的に付与する（2 行目以降が user body。`build_kaji_review_marker()` と同じ配置慣行）:

```
<!-- kaji-verdict: step=<step> status=<STATUS> -->
```

- GitHub UI 上では HTML コメントとして不可視（review 体験を壊さない）
- local provider では `.kaji/issues/<id>/comments/<seq>-<machine>.md` の 1 行目に永続化される
- consumer は `kaji issue view <id> --json comments` の `.comments[].body` 先頭で厳密照合する（github / local とも同一構造。`_local_issue_view` は gh 互換 `--json` 実装済み: `kaji_harness/cli_main.py:1556-1589`）

### 使用例

```bash
# producer（例: issue-review-code が BACK を返す判定コメント）
kaji issue comment 261 --commit \
  --verdict-step review-code --verdict-status BACK \
  --body-file - <<'EOF'
# コードレビュー結果
（指摘内容）
EOF
# → 投稿される body:
# <!-- kaji-verdict: step=review-code status=BACK -->
# # コードレビュー結果
# （指摘内容）

# 不正語彙は fail-loud
kaji issue comment 261 --verdict-step review-code --verdict-status back --body x
# → exit 2, stderr: invalid verdict status 'back': ...
```

```bash
# consumer（issue-design Step 1.6 観測 3 の置き換え。heading gate・旧 regex は完全削除）
BACK_COUNT=$(kaji issue view [issue_id] --json comments \
  | jq '[
      .comments[]
      | select(.body | test("^<!-- kaji-verdict: step=[a-z][a-z0-9_-]* status=(BACK|BACK_DESIGN) -->"))
    ] | length')
```

（`test()` は `m` フラグなしのため `^` は文字列先頭のみに一致 = 1 行目マーカーのみを検出。過去コメント本文中の regex 引用への誤検出は構造的に起きない）

### design 再入と判定する status の集合

consumer は **status ∈ {`BACK`, `BACK_DESIGN`}** のマーカーを design 再入として数える。根拠（現行 workflow YAML の全 BACK 遷移の悉皆調査、2026-07-07）:

| workflow | step | verdict | 戻し先 |
|----------|------|---------|--------|
| dev.yaml:123 / dev-local.yaml:81 / dev-thorough(-fable).yaml:125 | implement | `BACK` | design |
| dev.yaml:134 / dev-local.yaml:92 / dev-thorough(-fable).yaml:136 | review-code | `BACK` | design |
| dev.yaml:135 / dev-thorough(-fable).yaml 同等行 | review-code | `BACK_IMPLEMENT` | implement（対象外） |
| dev.yaml:166 / dev-thorough(-fable).yaml:168 | final-check | `BACK_DESIGN` | design |
| dev.yaml:167 | final-check | `BACK_IMPLEMENT` | implement（対象外） |
| dev.yaml:185 | review-poll | `BACK_FALLBACK` | review（対象外） |
| docs(-local/-fable).yaml | doc-review 系 | `BACK` | doc-update（docs workflow に BACK 再入 consumer なし・スコープ外） |

`BACK_IMPLEMENT` / `BACK_FALLBACK` は完全一致で除外される（regex は status トークン全体を照合）。dev 系 workflow において bare `BACK` は常に design 行きであり、`BACK_DESIGN` は final-check の design 行き。docs workflow の bare `BACK` は doc-update 行きだが、`issue-design` は docs workflow から起動されない（`i-doc-update` が入口）ため誤検出経路にならない。

> **保守点の縮小**: 旧 heading gate は「新しい判定 step を追加したら見出し OR リストに追記」という保守点を持っていた。新契約では「design を戻し先とする新 status を追加したら consumer の status 集合に追記」に置き換わる。前者はコメントの自然文見出し（silent に壊れる）、後者は workflow YAML の `on:` キー（`validate_workflow` が形式検証する語彙）に紐づくため、契約の壊れ方が fail-loud 側に寄る。

## 制約・前提条件

- **確定済み決定事項に従う**: Issue 本文「検討済み決定事項（2026-07-06 確定・maintainer 合意済み）」1〜10 を設計の前提とし、再検討しない
- **後方互換レイヤを書かない**（ADR 008 決定 1）: 旧 regex の OR フォールバック等は実装しない。旧検出は一度も機能しておらず「動いていた過去」が存在しない。open issue に BACK 判定コメント 0 件（2026-07-06 実測、Issue 本文）で in-flight もない
- **マーカーは全判定コメントに無条件付与**（決定 3）: 「BACK のときだけ」の条件付き出力は禁止。producer スキルのテンプレートは status によらず常に `--verdict-step/--verdict-status` を使う
- **`inject_verdict` は必須要件にしない**（決定 8）: runner 内遷移しかカバーせず、手動 slash command 実行（コメントが唯一のセッション横断チャネル）に効かない。本設計では上乗せ最適化としても採用しない（マーカー単独で要件を満たし、二重の検出経路は契約を複雑化するため）
- **repo 上の ADR 008 を既存文書として参照しない**（決定 9）: 無レビューの先行 commit `4391bec` は `git merge-base --is-ancestor 4391bec main` → NOT IN MAIN を確認済み（2026-07-07。既に main 履歴から除去され dangling）。`docs/adr/` の現存は 001〜007 のみ。ADR 008 は本 worktree で新規作成する
- 依存: `gh` CLI（github provider）、PyPI `jq` package（consumer の jq 式。既に runtime dependency）。新規依存の追加なし
- スキル（`.claude/skills/`）は下流リポジトリでカスタマイズされる前提（ADR 008 コンテキスト）

## 変更スコープ

| 領域 | ファイル | 変更内容 |
|------|---------|---------|
| harness（新規） | `kaji_harness/providers/markers.py` | `build_kaji_verdict_marker(step, status)` + 語彙検証（provider 中立のため `github.py` ではなく独立モジュール） |
| harness | `kaji_harness/cli_main.py` | github 経路: `_handle_issue` で `comment` + verdict フラグ検出時に passthrough せず構造化経路（`GitHubProvider.comment_issue`）へ。local 経路: `_local_issue_comment` の argparse にフラグ追加。共通: 片方のみ指定 / 不正語彙 → exit 2 |
| consumer skill | `.claude/skills/issue-design/SKILL.md` | Step 1.6 観測 3 をマーカー照合に置換（heading gate・旧 regex **完全削除**）、fail-safe ABORT 分岐追加、Step 1.7 サブステップ 2 も同一フィルタに置換 |
| producer skill | `.claude/skills/issue-review-code/SKILL.md` | Step 3 判定コメントを `--verdict-step review-code --verdict-status <返す status>` 付きに |
| producer skill | `.claude/skills/i-dev-final-check/SKILL.md` | 最終チェック結果コメントを `--verdict-step final-check --verdict-status <返す status>` 付きに |
| producer skill | `.claude/skills/issue-implement/SKILL.md` | 実装報告系の判定コメントを `--verdict-step implement --verdict-status <返す status>` 付きに |
| producer skill | `.claude/skills/issue-review-design/SKILL.md` | Step 3 判定コメントを `--verdict-step review-design --verdict-status <返す status>` 付きに |
| producer skill | `.claude/skills/issue-design/SKILL.md` | Step 4 / Step 1.7 の自身の判定コメントを `--verdict-step design --verdict-status PASS` 付きに |
| ADR | `docs/adr/008-no-backward-compat-layer.md` | **本設計フェーズで作成済み**（ステータス: 提案。review-design 通過で承認へ更新） |
| docs | `docs/dev/shared_skill_rules.md` | § 後方互換（共通）追加 + § verdict マーカー契約（producer/consumer 契約の docs 側案内。正本は CLI コード） |
| docs | `docs/dev/skill-authoring.md` | cross-skill 契約は CLI / harness 層に置く指針（ADR 008 決定 3）を追記 |
| docs | `docs/cli-guides/github-mode.md` / `local-mode.md` | `--verdict-step/--verdict-status` の説明追記 |
| release skill | `.claude/skills/release/SKILL.md` | Step 3 に BREAKING エントリ 3 要素の記載要件を追記 |
| tests | `tests/test_verdict_marker.py`（新規）+ `tests/test_cli_main.py` | 後述テスト戦略 |

### producer 対象範囲の確定（設計フェーズ残タスクの決定）

**含める（上表の 5 スキル）**:

- `issue-review-code` / `i-dev-final-check` / `issue-implement`: design を戻し先とする verdict（`BACK` / `BACK_DESIGN`）の全発行元（上記悉皆調査）。`issue-implement` を落とすと dev.yaml:123 経由の BACK 再入が常に fail-safe ABORT に落ち、運用ノイズになる
- `issue-review-design`: **含める**。旧バグの誤検出源だった `[x] Changes Requested (設計修正が必要)` の発行元であり、マーカー無条件付与により「review-design の RETRY コメントと design 再入 BACK の混同」を語彙レベルで構造的に排除する。変更は判定コメント投稿 1 箇所のフラグ追加のみ
- `issue-design` 自身: Step 1.7 の設計再確認コメント / Step 4 の完了コメントも判定コメントであり、無条件付与原則（決定 3）の一貫性を保つ

**含めない（理由つき）**:

- `issue-verify-*` / `issue-fix-*` / `issue-review-ready` 系: design 再入検出に関与せず（BACK を design に発行しない）、レビュー収束スキルへの変更は収束保証（`development_workflow.md:75`）を乱すリスクの方が大きい。新 consumer はマーカーのみを読むため、これらの未マーカーコメントは検出対象外として無害
- docs 系（`i-doc-review` / `i-doc-final-check`）: docs workflow に BACK 再入検出 consumer が存在しない（根本原因調査参照）。契約は流用可能であり、必要になった時点で別 Issue
- `review-poll` / `review`（PR フェーズ）: Issue 判定コメントを消費する consumer が存在しない

## 方針

### 1. CLI: マーカー付与（決定的・fail-loud）

```python
# kaji_harness/providers/markers.py（新規、疑似コード）
_VERDICT_STATUS_RE = re.compile(r"^(PASS|RETRY|ABORT|BACK|BACK_[A-Z0-9_]+)$")
_VERDICT_STEP_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

def build_kaji_verdict_marker(step: str, status: str) -> str:
    if not _VERDICT_STEP_RE.match(step):
        raise ValueError(f"invalid verdict step {step!r}: expected ...")
    if not _VERDICT_STATUS_RE.match(status):
        raise ValueError(f"invalid verdict status {status!r}: expected ...")
    return f"<!-- kaji-verdict: step={step} status={status} -->"
```

`cli_main.py` 側（疑似コード）:

```python
# github 経路: _handle_issue 内、passthrough の手前
if args[0] == "comment" and _has_verdict_flags(args):
    return _github_issue_comment_with_verdict(provider, args[1:])
    # argparse: issue_id / --body / --body-file / --commit(無視) / --verdict-step / --verdict-status
    # body = _read_body_arg(...); marked = f"{marker}\n{body}"
    # provider.comment_issue(issue_id, marked)

# local 経路: _local_issue_comment の argparse に --verdict-step / --verdict-status を追加し、
# body 解決後に marked body へ差し替え（--commit の atomic commit 挙動は不変）
```

- 片方のみの指定は共通バリデーションで exit 2（`EXIT_INVALID_INPUT`）。local 経路の `ValueError` → exit 2 マッピングは既存（`cli_main.py:1548-1550`）を利用
- verdict フラグなしの `kaji issue comment` は github passthrough / local 既存経路とも**一切変更しない**（gh 任意フラグの透過性を維持）

### 2. consumer: issue-design Step 1.6 / 1.7 の置換

観測 3 を前掲の jq マーカー照合に置き換え、heading gate（見出し OR リスト）と旧 regex を完全削除する。分岐判定を次の 4 分岐に再定義:

| 観測 1（設計書あり） | 観測 2（設計後コミットあり） | 観測 3（design 再入マーカーあり） | 分岐 |
|:---:|:---:|:---:|------|
| ✓ | ✓ | ✓ | BACK 経由再起動 → Step 1.7 |
| ✓ | ✓ | ✗ | **曖昧状態 → ABORT（fail-safe、新設）** |
| ✗ または（✓ ∧ 観測 2 ✗） | | | 初回起動 → Step 2 以降（挙動不変） |
| —（`BACK_COUNT` が空文字 = パイプライン失敗） | | | ABORT（fail-loud、既存踏襲） |

- fail-safe ABORT は互換策ではなく恒久的な安全設計（決定 5、ADR 008 帰結にも明記）。マーカー付与失敗（producer カスタマイズ逸脱等）の帰結を「設計書上書き事故」から「一時停止」に変える。ABORT verdict の `suggestion` には「直近の BACK 判定コメントを `kaji issue comment --verdict-step <step> --verdict-status BACK` で再投稿して再実行する」復旧手順を含める
- Step 1.7 サブステップ 2「直近 BACK verdict の特定」は、同じマーカーフィルタで最後（配列末尾）の該当 comment を選ぶ形に置換する

### 3. ADR 008 と付随 docs

- `docs/adr/008-no-backward-compat-layer.md` を Issue 帰属コメント（2026-07-06）のドラフトから作成する（**本設計フェーズで実施・設計書と同一 commit**）。ステータスは「提案 — Issue #261 の設計レビューで承認を判定する」とし、`/issue-review-design` の Approve をもって実装フェーズ冒頭で「承認（2026-07-07 以降のレビュー通過日）」に更新する
- `release/SKILL.md` Step 3・`shared_skill_rules.md` への反映（帰属コメントの付随変更ドラフト）は**実装フェーズ**で行う（コード変更と同じ PR で審査するため）

### 4. BREAKING エントリ（次リリースの CHANGELOG / Release notes 向けドラフト）

ADR 008 決定 2 の 3 要素に従う:

- **壊れる契約**: `issue-design` Step 1.6 の BACK 再入検出は、判定コメントの見出し・checkbox 表現（`[x] Changes Requested / BACK` / `| 判定 |` テーブル / 判定見出し gate）を読まなくなる。検出対象は `kaji issue comment --verdict-step/--verdict-status` が付与する 1 行目マーカー `<!-- kaji-verdict: ... -->` のみ。マーカーを付与しない判定コメントは BACK 再入として検出されず、「設計書あり + 設計後コミットあり + マーカーなし」は ABORT で停止する
- **影響の判定方法**: 下流 repo で `grep -rn 'Changes Requested / BACK' .claude/skills/` がヒットする場合、旧 consumer を保持しており更新が必要。producer 側は `grep -rln 'kaji issue comment' .claude/skills/issue-review-code .claude/skills/i-dev-final-check` 等で判定コメント投稿箇所を特定する
- **適用指針**: 未カスタマイズなら該当 SKILL.md の再コピー + kaji 本体更新で完結。カスタマイズ済み repo は、producer 側は判定コメント投稿コマンドへの `--verdict-step <step> --verdict-status <STATUS>` の付与（呼び出し 1 行の差し替え）、consumer 側は本 PR の Step 1.6 diff を参照して自版へ移植する（上流 PR 参照を Release notes に記載）

### 5. 検証と還流（実装フェーズ以降のタスクとして設計で固定）

- **dogfood 1 周**: 実装完了後、本 Issue 上で BACK 再入経路を 1 周させ証跡を Issue コメントに残す。手順: (1) 実装 commit 後、`kaji issue comment 261 --verdict-step review-code --verdict-status BACK --body <検証用 BACK 判定コメント>` を投稿 → (2) `/issue-design 261` を再実行 → (3) Step 1.6 が BACK 経由再起動と判定し Step 1.7 の設計再確認コメントが投稿されることを確認 → (4) マーカーなし判定コメントのみの状態で fail-safe ABORT に入ることも確認
- **kaji-starter-python 還流 follow-up Issue の起票**（Issue 完了条件。final-check までに実施）

## テスト戦略

### 変更タイプ

実行時コード変更（CLI 新機能 + skill instruction 変更 + docs 変更の混合。テスト対象は CLI 部分）。

### 再現テスト（bug 固有ルール）

本 bug の OB は「SKILL.md 散文契約の不一致」であり、修正は CLI 新機能の追加を含む。実装前 Red 証跡は **OB 静的証跡（本設計書 § 再現手順の grep 結果 = producer 出力 0 件）で代替**する（Issue 完了条件に明記された escape clause: 「CLI 新機能のため実装前 FAIL 証跡は本 Issue の OB 静的証跡で代替」。`_shared/design-by-type/bug.md` の escape clause 条件〈OB を直接示す静的証跡 + EB を検証する恒久回帰テスト〉を充足）。恒久回帰テスト自体（修正後 Green）は下記の通り必須で追加する。

### Small テスト（`tests/test_verdict_marker.py` 新規）

検証観点: **マーカー文字列の決定性と語彙検証の fail-loud 性**（純粋関数・外部依存なし）。

- `build_kaji_verdict_marker("review-code", "BACK")` が仕様通りの 1 行文字列（改行なし）を返す
- status 全カテゴリの受理: `PASS` / `RETRY` / `ABORT` / `BACK` / `BACK_DESIGN` / `BACK_IMPLEMENT`（`BACK_[A-Z0-9_]+` 文法の代表値）
- status の拒否: lowercase（`back`）/ mixed-case / `BACK_`（suffix 空）/ `BACK_design` / 空文字 / 未知語（`APPROVE`）→ `ValueError`（workflow-authoring.md の `BACK_*` 文法との整合を境界値で固定）
- step の受理 / 拒否: `review-code` 受理、大文字・空文字・先頭数字・スペース入りは `ValueError`

### Medium テスト（`tests/test_cli_main.py` 拡張）

検証観点: **両 provider で comment body の 1 行目が決定的にマーカーになること・不正入力が silent に通らないこと・従来経路の非回帰**（CLI dispatch + ファイル I/O / subprocess 境界 mock）。

- local provider（系統 A: `git init` fixture、`testing-convention.md` § `subprocess.run` patch スコープ遵守）:
  - `kaji issue comment <id> --verdict-step design --verdict-status PASS --body X` → `.kaji/issues/<id>/comments/*.md` の 1 行目 == マーカー、2 行目以降 == body
  - `--commit` 併用で atomic commit 挙動が不変
  - 不正 status / 片方のみのフラグ指定 → exit 2 + stderr メッセージ
- github provider（既存 `TestGithubPrReviewHandler` パターン: handler 単体 + 投稿 body capture）:
  - verdict フラグあり → passthrough せず `GitHubProvider.comment_issue` に 1 行目マーカー付き body が渡る
  - verdict フラグなし → 従来 passthrough（`--commit` strip / `--repo` 注入）が不変
  - 不正語彙 → exit 2（gh を起動しない）

### Large テスト

恒久 Large は追加しない。理由（`testing-convention.md` 4 条件へのマッピング）:

1. 実 GitHub API への新規リクエスト形は増えない（`GitHubProvider.comment_issue` = 既存の `gh issue comment` 呼び出しを再利用。独自ロジックは marker 合成のみで Small/Medium が捕捉）
2. gh 疎通自体の不具合パターンは既存の provider 経路テストで捕捉済み
3. 実 API を叩いても marker 文字列合成の回帰検出情報は増えない
4. E2E の検証は本 Issue の **dogfood 1 周**（§ 方針 5）が変更固有検証として担い、証跡を Issue コメントに残す（恒久化しない理由: BACK 再入の E2E は workflow runner + agent セッションを要し CI で決定的に再現する構成にならないため。skill instruction（bash/jq）は pytest の対象外であり、dogfood + 本設計書の producer/consumer 対照表で契約を固定する）

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | **あり** | ADR 008 新規作成（本設計フェーズの成果物、ステータス: 提案） |
| docs/ARCHITECTURE.md | なし | モジュール構成の追加は `providers/markers.py` 1 ファイルで、アーキテクチャ層構造は不変 |
| docs/dev/shared_skill_rules.md | **あり** | § 後方互換（共通）+ § verdict マーカー契約を追加（実装フェーズ） |
| docs/dev/skill-authoring.md | **あり** | cross-skill 契約は CLI / harness 層に置く指針を追記（実装フェーズ） |
| docs/dev/workflow-authoring.md | なし | verdict 語彙・`BACK_*` 文法は不変（マーカーは同文法を参照するのみ） |
| docs/reference/ | なし | Python 規約変更なし |
| docs/cli-guides/github-mode.md / local-mode.md | **あり** | `kaji issue comment` の新フラグ説明（実装フェーズ） |
| .claude/skills/release/SKILL.md | **あり** | Step 3 に BREAKING エントリ 3 要素要件（実装フェーズ） |
| AGENTS.md / CLAUDE.md | なし | 常時適用ルール・ライフサイクル表に変更なし |
| CHANGELOG / Release notes | あり（次リリース時） | § 方針 4 の BREAKING エントリドラフトを `/release` 時に反映 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #261 本文 | https://github.com/apokamo/kaji/issues/261 | OB/EB・確定済み決定事項 1〜10・完了条件の正本。「契約は CLI 層に置く」「マーカーは全判定コメントに無条件付与」等を設計の前提とする |
| #261 帰属検討コメント | https://github.com/apokamo/kaji/issues/261#issuecomment-4894167442 | ADR 008 ドラフト全文と帰属判断（「ADR 008 は本 Issue の設計フェーズの成果物として作成し、/issue-review-design で設計書と同時に審査する」）。付随変更（release skill / shared_skill_rules）ドラフト |
| consumer 現行実装 | `.claude/skills/issue-design/SKILL.md`（Step 1.6 観測 3 / Step 1.7 サブステップ 2） | 検出 regex `\[x\] Changes Requested / BACK|\| *判定 *\|.*BACK` と heading gate（`# コードレビュー結果` / `## 最終チェック結果` の OR）が現行契約。「新しい判定 step を追加した場合はここに見出しを追記する」という保守点の存在 |
| producer 現行テンプレート | `.claude/skills/issue-review-code/SKILL.md:272-273` / `.claude/skills/i-dev-final-check/SKILL.md:287-289` | 前者は `[x] Approve (修正なしでマージ可)` / `[x] Changes Requested (要修正)` の 2 択のみ、後者は `PASS / RETRY / BACK_DESIGN / BACK_IMPLEMENT / BACK` のメニュー行。いずれも consumer regex に不一致（grep 0 件、§ 再現手順） |
| 既存マーカー慣行 | `kaji_harness/providers/github.py:39-56` | `_KAJI_REVIEW_MARKER_PREFIX = "<!-- kaji-review: state="` / `_REVIEW_STATES_VALID` 集合検証 / `build_kaji_review_marker()` が不正 state を `ValueError` で拒否。「1 行目に置き、2 行目以降が user body」「GitHub UI 上では HTML コメントとして不可視」 |
| CLI comment 経路 | `kaji_harness/cli_main.py:1125-1174`（`_handle_issue`）/ `1704-1737`（`_local_issue_comment`）/ `236-244`（`GitHubProvider.comment_issue`） | github は `gh issue comment` へ passthrough（`--commit` を silent strip、`--repo` 注入）、local は argparse + `provider.comment_issue` + `--commit` atomic commit。拡張ポイントの特定根拠 |
| verdict 語彙の文法 | `docs/dev/workflow-authoring.md` § `BACK_*` プレフィックス拡張（238-254 行） | 「標準 status は `PASS / RETRY / BACK / ABORT` の 4 種。`BACK_*` は拡張点」「suffix は uppercase 英数字 + アンダースコア (`[A-Z0-9_]+`) に限定」「`BACK_` 単独や lowercase suffix は不正で `validate_workflow` が弾く」→ `--verdict-status` の検証 regex の根拠 |
| BACK 遷移の悉皆 | `.kaji/wf/dev.yaml:123,134-135,166-167,185` / `dev-local.yaml:81,92` / `dev-thorough.yaml:125,136,168` / `dev-thorough-fable.yaml:125,136,168` / `docs*.yaml` | design を戻し先とする verdict が `BACK`（implement / review-code 発）と `BACK_DESIGN`（final-check 発）に閉じることの根拠（§ design 再入と判定する status の集合の対照表） |
| BACK の定義 | `docs/dev/workflow-authoring.md:130` 付近 | 「BACK = 差し戻し。前段ステップを再実行」— BACK 再入が正当な遷移であり ABORT 対象でないこと（Step 1.7 PASS 復帰の維持） |
| gh issue view --json 互換 | https://cli.github.com/manual/gh_issue_view | `--json comments` フィールド指定形。`_local_issue_view`（`cli_main.py:1556-1589`）が gh 互換 `--json` を実装済みで provider 別抽出器が不要なことの根拠 |
| テスト規約 | `docs/dev/testing-convention.md` | S/M/L 判定基準、`subprocess.run` patch スコープ（dispatch/provider 結合での名前空間 patch 禁止 → 系統 A `git init` fixture）、恒久テスト不要 4 条件 |
| bug 設計ガイド | `.claude/skills/_shared/design-by-type/bug.md` | OB/EB/再現手順/根本原因の必須構成、再現テスト必須 + escape clause（実ログ・静的証跡による実装前 Red 代替）の条件 |
| 先行 commit の除去確認 | `git merge-base --is-ancestor 4391bec main` → exit 1（NOT IN MAIN、2026-07-07 実測） | 決定 9 の前提（無レビュー先行 commit が main 履歴に存在しない）の確認。`docs/adr/` 現存 001〜007 |
| 発見元 | https://github.com/apokamo/kaji/issues/242 / https://github.com/apokamo/kaji/issues/259#issuecomment-4893841743 | starter 側 tactical fix の残余リスク（メニュー行誤検出・RETRY コメント混同）と恒久対応の検討経緯 |
