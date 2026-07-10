# AI-Driven Development Strategy

Language: English | [日本語](ai-driven-strategy.ja.md)

## Overview

kaji adopts a **95% AI / 5% human** development model. AI agents execute design,
implementation, review, and documentation updates; humans focus on decisions,
approval, and direction changes.

## Principles

### 1. AI executes the process

- Create design documents (`/issue-design`)
- Implement with TDD (`/issue-implement`)
- Run code reviews and design reviews
- Update documentation and check consistency

### 2. Humans make judgments

- Set issue priorities
- Give final approval to design direction
- Decide whether to merge PRs
- Permit deviations from the workflow

### 3. Workflows preserve quality

AI output quality depends on workflow design. Skill files work as instructions
to AI, and quality gates (review cycles, `make check`, and final-check) preserve
quality.

## Workflow structure

```
Issue creation -> pre-start gate -> design -> review -> implementation -> review
               -> quality gate -> PR -> post-PR review cycle -> merge
```

AI executes each step, and verdicts (PASS/RETRY/BACK/ABORT) control transitions
to the next step.

### Role of the pre-start gate and post-PR review cycle

Rather than moving directly from `issue-create` to implementation, the workflow
first filters the issue body's quality once. Similarly, after PR creation,
review feedback handling is closed as a convergence-guaranteed cycle.

| Skill | Role |
|-------|------|
| `issue-review-ready` | **Pre-start gate**: determines whether the issue body has enough description quality to start work (common to GitHub workflows; local workflows assume manual issue creation and start from design) |
| `issue-fix-ready` | **Gate fix**: updates the issue body based on RETRY feedback from `issue-review-ready` |
| `review-poll` | **PR review entry**: polls codex auto-review results and decides PASS / RETRY / fallback (`/review`) |
| `pr-fix` | **PR review handling**: chooses between fixing and rebutting PR review comments |
| `pr-verify` | **PR review convergence**: verifies only the validity of fixes and forbids new findings, closing the cycle |

## Human intervention points

| Point | Intervention |
|-------|--------------|
| Issue creation | Requirements definition and priority setting |
| Design review result confirmation | Judgment on the validity of the direction |
| PR review feedback judgment | Final quality confirmation; selecting fix vs rebuttal in `pr-fix` and reaching agreement with reviewers (the first review is handled by codex auto-review) |
| Running `/issue-close` | Merge decision |

## Prerequisites

- AI agents have read/write access to the repository
- `gh` CLI is authenticated (for GitHub mode; unnecessary in local mode)
- Workflow definitions (YAML) and skill files (Markdown) are prepared
