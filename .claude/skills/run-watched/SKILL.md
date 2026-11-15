---
name: run-watched
description: 保守者が指定した Issue 群を series として直列に起動し、kaji watch で監視させる起動の入口。「#101, #102 を dev で直列に」のような明示的な依頼のときに使う。判断（workflow の選択・順番）は skill が行い、YAML 生成・commit・push・ラベル・タブ・起動は決定的な script（run_watched plan / execute）に任せる。
---

# Run Watched

保守者の依頼から series を作って起動し、`kaji watch` を起動するまでを行う。
CLI と JSON の正本は [`docs/cli-guides/watch.md`](../../../docs/cli-guides/watch.md) の「`/run-watched`」節。

## 前提

- Herdr の pane の中で、kaji repo の **main checkout**（worktree ではない）で実行する。
- tracked の `.kaji/config.toml` に `[watch]` が宣言され、契約の `state:*` ラベルが GitHub にある。
- 前提を満たさなければ `execute` が exit 2 で止まり、何も書かない。

## 絶対規則

1. **series YAML と draft を手で書かない**。`.kaji/series/*.yaml` と
   `<artifacts>/_watch/run-watched/*.json` は `run_watched plan` / `execute` だけが作る。
2. **`kaji run` / `kaji run-series` を直接起動しない**。起動は `run_watched execute` が Herdr の
   `kaji-series-<id>` タブで行う。
3. git の commit / push、ラベルの付与、タブの作成を自分で行わない（すべて `execute` の段が照合付きで行う）。
4. 明示された依頼（Issue と workflow を保守者が指定した）は、それ自体を承認として扱う。
   `plan` が `approval_required: true` を返したときだけ、表を保守者に見せて承認を得る。

## 手順

### Step 1: plan

```bash
python -m kaji_harness.scripts.run_watched plan --issues 101,102 --workflow dev
# Issue ごとに workflow が違う場合
python -m kaji_harness.scripts.run_watched plan --issues 101,102 --member 101=dev --member 102=docs
```

- 順番は保守者の依頼どおりに `--issues` へ並べる。依頼から順番を決めきれず skill が並べた場合は
  `--order-ambiguous` を付ける。
- 出力の `needs_selection` が空でなければ、その Issue の workflow を skill が選び（Issue の type と
  workflow の description から判断する）、`--member <issue>=<workflow>` と `--chosen-by-skill` を付けて
  `plan` をやり直す。
- exit 2（series に入る Issue が 0 件）なら、`excluded` の理由を保守者に報告して終了する。

### Step 2: 承認（`approval_required: true` のときだけ）

`members`（Issue と workflow）と `excluded`（Issue と理由）の表を保守者に示し、承認を得る。
承認されなければ終了する（draft は残してよい）。

### Step 3: execute

```bash
python -m kaji_harness.scripts.run_watched execute --draft <draft-id>
```

- exit 0: 出力の `series_id`・`commit`・`series_pane`・`watch` を保守者に報告する。
- exit 2: 前提違反・外部状態の相違（Issue が close された、`state:hold` が付いた、無関係な commit が
  main にある、既存の YAML の内容が違う、検証が失敗した等）。stderr と済んだ段を報告して止まる。
  原因が解消されたら、**同じ draft で `execute` をやり直す**（済んだ段は外部の照合で飛ばされる）。
- exit 3: remote が先に進んだ等、人手の解決が要る。stderr の案内（`git reset --soft <parent>` 等）を
  保守者に報告し、rebase・force push はしない。

途中で落ちた場合も、同じ draft で `execute` をやり直せばよい（空の commit・二重の push・二重のラベル・
二重のタブ・二重の起動は起きない）。
