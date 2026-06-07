---
id: local-p1-26
title: 'PoC: 足し算 CLI の最小実装'
state: closed
slug: poc-addition-cli
labels:
- type:feature
created_at: '2026-06-04T16:26:04Z'
closed_at: '2026-06-04T16:43:09Z'
closed_by: pc5090
close_reason: completed
---
## 概要

非常に小さな検証用タスクとして、Python で 2 つの整数を足す関数と CLI を追加する。

## 目的

interactive_terminal runner の PoC を、設計から実装完了まで実 workflow に近い形で検証する。

## スコープ

### IN

- `kaji_addition.py` を追加する
- `add(a: int, b: int) -> int` を実装する
- `python kaji_addition.py 1 2` が `3` を出力する
- 最小限の pytest を追加する

### OUT

- パッケージ公開
- 複雑な入力形式
- 例外設計の作り込み

## 完了条件

- [x] `add(1, 2) == 3`
- [x] CLI で `python kaji_addition.py 1 2` が `3` を出力する
- [x] 関連テストが通る

## 検証条件

- 本 Issue は PoC 検証専用で main にはマージしない
- モデルは安価なもの、effort は最低設定で実行する
