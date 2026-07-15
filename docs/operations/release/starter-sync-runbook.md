# Managed Starter Sync Runbook

kaji Release 後に managed starter を追随、独立 review、snapshot 公開する運用の正本。
starter repository は kaji が所有する配布 repository とし、作業・不具合は kaji Issue で管理する。

## Managed starters

| repository | default local path | quality gate source |
|---|---|---|
| `apokamo/kaji-starter-python` | kaji main worktree の sibling `../kaji-starter-python` | starter repository の実体 |

新しい言語の starter はこの表へ追加する。skill は tracking Issue の `starter_repo` を入力とし、
manifest / lockfile / quality gate を repository から解決する。

## Tracking Issue

kaji Release ごと・managed starter ごとに kaji 側へ一件作る。starter 側 Issue は使わない。

```markdown
# [starter-sync]: owner/repo を kaji vX.Y.Z へ追随する

starter_repo: owner/repo
target_kaji_release: vX.Y.Z
starter_path: /optional/non-standard/path
```

kaji GitHub Release 本文の repository 別状態表を状態正本とする。

| repository | status | tracking Issue | starter Release / N/A 理由 |
|---|---|---|---|
| owner/repo | PENDING | #123 | - |

単一の集約 status は置かない。各行が独立に `PENDING -> PASS` または `N/A` へ遷移する。

## Workflow

1. `/release` が PyPI publish 確認後に tracking Issue を作り、kaji Release 状態表へリンクする。
2. `/update-starter <id>` が全変更を 3 区分し、remote main と同期した local main へ直接 commit する。
   feature branch / worktree / PR / merge は使わず、review 前には push しない。
3. 別 session の `/review-starter-update <id>` が target / base / candidate に固定した独立 review を行う。
4. `/release-starter <id>` が最新 PASS と SHA、quality gate、release-plan を確認し、ref push がある場合は
   workflow 外の人間承認後に annotated tag と main を atomic push する。

初回 tag は `kaji-vX.Y.Z`。同じ kaji version の公開済み snapshot から candidate が変わる修正版だけ
`kaji-vX.Y.Z-rN`（最大 N + 1）を使う。force push / tag 上書きは禁止。Release 作成の再試行では同じ
tag を使う。N/A は独立 review PASS 後だけ状態表更新と Issue close を行い、starter Release は作らない。

古い `PENDING` があっても新しい kaji Release 自体は止めず tracking Issue を作る。ただし同じ starter の
sync は release 順で処理し、新しい update は ABORT する。starter failure から kaji tag / Release / PyPI
を rollback しない。

## One-time bootstrap after Issue 341

Issue 341 の merge / close 後、別の kaji tracking Issue と有人手順で現在の starter main を
`kaji-v0.12.1` annotated tag + GitHub Release として固定する。remote identity、dependency pin、quality
gate、人間承認を確認して atomic push する。通常 skill に初回 fallback を追加せず、bootstrap 完了前の
`update-starter` は Release 不在として ABORT する。managed starter の GitHub Issues は無効化し、報告先を
kaji Issue tracker にする。

## Verification boundary and follow-up Issue

Issue 341 では skill / docs / CLI の静的・決定的テストだけを行い、実 starter を変更しない。
bootstrap 後に別の follow-up Issue を作り、`v0.12.1 -> v0.15.0` の実追随で 3 skill の forward test を
行う。実 repository の branch / push / tag / Release はその Issue の明示スコープなしに実行しない。
