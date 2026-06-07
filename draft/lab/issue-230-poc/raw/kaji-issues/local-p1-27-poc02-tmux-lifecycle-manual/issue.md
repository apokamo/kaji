---
id: local-p1-27
title: PoC02 tmux lifecycle manual real-agent verification
state: closed
slug: poc02-tmux-lifecycle-manual
labels:
- type:feature
created_at: '2026-06-06T08:20:00Z'
closed_at: '2026-06-06T15:09:58Z'
closed_by: pc5090
close_reason: completed
---
# PoC02 tmux lifecycle manual real-agent verification

## Goal

Implement a tiny addition function and CLI for PoC02 real-agent verification.

## Requirements

- Add `kaji_addition_poc02.py`.
- Implement `add(a: int, b: int) -> int`.
- `python kaji_addition_poc02.py 1 2` prints `3`.
- Add minimal pytest coverage.
- Keep the change intentionally small.

## Scope

- No packaging change.
- No docs change required except generated design artifacts.
- No PR required.
