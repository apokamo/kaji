---
id: local-p1-28
title: PoC03 clean addition CLI for tmux lifecycle verification
state: open
slug: poc03-clean-addition
labels:
- type:feature
created_at: '2026-06-07T00:30:00Z'
---
> [!NOTE]
> **Worktree**: `../kaji-feat-local-p1-28`
> **Branch**: `feat/local-p1-28`

# PoC03 clean addition CLI for tmux lifecycle verification

## Goal

実 Claude / Codex + tmux lifecycle workflow を、未実装のクリーンな状態から
design → implement まで検証する。安価モデル / 最低 effort で実行する。

## Requirements

- `kaji_addition_poc03.py` を新規追加する（既存ファイルなし）。
- `add(a: int, b: int) -> int` を実装する。
- `python kaji_addition_poc03.py 1 2` が `3` を出力する。
- 最小限の pytest を `tests/test_kaji_addition_poc03.py` に追加する。
- 変更は意図的に小さく保つ。

## Scope

### IN

- `kaji_addition_poc03.py` の新規追加
- `add(a: int, b: int) -> int` の実装
- 最小 pytest の追加

### OUT

- パッケージング変更
- 複雑な入力形式・例外設計の作り込み
- docs 変更（生成される設計成果物を除く）
- PR 作成・main へのマージ

## 完了条件

- [ ] `add(1, 2) == 3`
- [ ] CLI で `python kaji_addition_poc03.py 1 2` が `3` を出力する
- [ ] 追加した最小テストが通る

## 検証条件

- 本 Issue は PoC 検証専用で main にはマージしない。
- モデルは安価なもの（claude: haiku / codex: gpt-5.4-mini）、effort は最低。
- 実装テストの検証は対象テストに絞り、フル pytest スイートの繰り返し実行は避ける。
