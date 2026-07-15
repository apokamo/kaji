---
name: grill-me
description: Issue 作成後・workflow 起動前に人間が明示起動する要件 interview。one-way door を含みうる重要な Issue で、未決の decision tree を 1 問ずつ推奨案付きで確認し、決定事項と provenance を Issue に固定するときだけ使用する。軽微な Issue や workflow 実行中には自動起動しない。
---

# Grill Me

重要判断を workflow の有人区間で前倒しして確定する。質問数を減らすことではなく、
shared understanding に達するまで未決分岐を残さないことを優先する。ただし、調査で
判明する事実や後段で安く直せる詳細を人間へ質問しない。

## 位置と責務境界

```text
issue-create → (/grill-me: 任意・明示起動・有人) → issue-review-ready → issue-start → …
```

- `.kaji/wf/*.yaml` の step には追加しない。headless workflow に同期対話を持ち込まない。
- `issue-review-ready` を代替しない。interview 後も独立した readiness gate を通す。
- 明示起動専用とする。対応 runtime が `agents/openai.yaml` を読む場合は
  `policy.allow_implicit_invocation: false` に従い、それ以外でもこの使用条件を守る。
- 判断軸、可逆性、停止条件、provenance の正本は
  [_shared/critical-decision-checklist.md](../_shared/critical-decision-checklist.md) とする。

## 入力

```text
$ARGUMENTS = <issue_id>
```

人間が対話に参加できる状態で `/grill-me <issue_id>` と明示起動する。Issue ID がない、
Issue を取得できない、または回答者が不在なら、Issue を変更せず停止する。

## Interview の 5 不変条件

1. 全 aspect の未決分岐を shared understanding に達するまで追跡する。
2. 決定間の依存順に decision tree の branch を 1 つずつ解決する。
3. 1 回に提示する質問は必ず 1 問とし、回答を受けるまで次を提示しない。
4. コード、docs、既存 Issue / PR で回答できる事項は先に調査し、人間へ聞かない。
5. 各質問に推奨回答と短い理由を付ける。

加えて、人間の確認なしに決定事項を確定または Issue へ書き戻さない。

`relentless` は質問を省略しないという意味であり、攻撃的に問い詰めることではない。
前置きを短くし、判断コストを下げる協働的な質問にする。

## 実行手順

### Step 1: Issue と正本を読む

1. Issue 本文と全コメントを読む。
2. [_shared/critical-decision-checklist.md](../_shared/critical-decision-checklist.md) を読む。
3. Issue が参照するコード、docs、既存 Issue / PR、外部仕様を調査する。
4. 事実、人間が既に決定した事項、AI の仮定、真に未決の事項を分離する。

source of truth 同士が矛盾する場合は、都合のよい方を選ばず未決分岐に戻す。

### Step 2: 要否門番を通す

次のいずれかを含みうる場合は interview を続ける。

- ユーザー価値、主要ユースケース、画面・操作構造
- 公開 CLI / API、永続化形式、migration、rollback
- source of truth の指定または競合
- 既存利用者、外部連携、運用、権限、監視への影響
- 非互換変更、quality gate の省略、曖昧なスコープ境界

typo、リンク修正、既存文言の明確化、形式調整など、one-way door がないと調査で
確認できた軽微な Issue はスキップする。Issue を変更せず、次の 1 行だけを返す。

```text
軽微な変更で one-way door がないため grill-me は不要です。/issue-review-ready <issue_id> へ進んでください。
```

迷う場合は interview を続ける。

### Step 3: decision tree を作る

チェックリストの判断軸を使い、調査後も残る未決事項を依存順に並べる。

- one-way door を先に解決する。
- two-way door は、人間の選好が必要な場合だけ質問する。その他は AI の仮定、根拠、
  後段の検査先として記録候補にする。
- 既に出典付きで決定済みの事項を聞き直さない。ただし、新しい情報との矛盾は質問する。
- 固定質問リストを機械的に読み上げず、回答によって後続 branch を更新する。

### Step 4: 1 問ずつ interview する

各 turn では次の形で、1 つの決定だけを問う。1 問の選択肢を示すことはよいが、別の
決定を同じ質問へ混ぜない。

```text
質問: <人間にしか決められない 1 つの判断>
推奨: <推奨回答>
理由: <短い根拠と主な trade-off>
```

- 回答が曖昧なら、その判断だけを明確にする follow-up を 1 問出す。
- 回答を受けたら decision tree を更新し、次に依存する 1 問だけを出す。
- 新しい選択肢や影響が判明したら、調査できる事実を先に調べてから質問へ戻る。
- shared understanding は、全 one-way door が出典付きで確定し、残る two-way door の
  仮定と検査先を双方が説明できる状態とする。

### Step 5: 書き戻し前の確認を 1 問する

確定した人間判断、残す AI 仮定、source of truth、スコープ外を短く要約する。その要約を
Issue へ固定してよいか、最後の 1 問として確認する。修正を求められたら該当 branch に
戻り、確認なしに書き込まない。

### Step 6: Issue 本文と provenance に固定する

1. 現在の Issue 本文を再取得する。
2. 既存本文を保持し、`## 決定事項` を追加する。既に同見出しがある場合は重複追加せず、
   確認済み内容へ更新する。
3. 人間決定は `判断 / 方針 / 出典`、two-way door は
   `仮定 / 根拠 / 後段の検査先` が分かる形にする。
4. `kaji issue edit <issue_id> --body-file <file>` で本文を更新する。
5. 別の `## grill-me provenance` コメントに、調査した一次情報、質問ごとの決定理由、
   変更された選択肢、残した仮定を記録する。
6. 本文とコメントを再取得し、決定事項と provenance が永続化されたことを確認する。

一時ファイルが必要なら tracked path を使わず、`mktemp --tmpdir` で `$TMPDIR` または
`/tmp` に作成し、使用後に削除する。秘密情報や不要な逐語 transcript は永続化しない。

### Step 7: handoff する

更新した決定事項と provenance の要点を報告し、次を案内する。

```text
/issue-review-ready <issue_id>
```

## 非目標

- 全 Issue での強制実行
- 複数質問の同時提示
- workflow YAML への step 追加
- workflow 開始後の人間判断の代行
- Issue 作成後の workflow 自動起動
- 再利用需要がない段階での interview engine 分割

## 出典

interview の 5 不変条件は Matt Pocock の
[skills](https://github.com/mattpocock/skills) にある `grill-me` / `grilling` の考え方と、
[kamo2 Issue 1404](https://github.com/apokamo/kamo2/issues/1404) /
[PR 1408](https://github.com/apokamo/kamo2/pull/1408) における適用例を参考にし、kaji の重要判断正本と
Issue provenance 運用へ合わせて再構成した。
