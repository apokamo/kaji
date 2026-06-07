---
id: local-p1-25
title: PoC interactive terminal runner real CLI validation
state: closed
slug: interactive-terminal-runner-real-cli-poc
labels:
- type:feature
- area:harness
created_at: '2026-06-04T15:02:00Z'
closed_at: '2026-06-04T15:56:45Z'
closed_by: pc5090
close_reason: completed
---
## Goal

Validate the PoC interactive terminal runner without merging this work to main.

## Requirements

- Use the feature-development design workflow only.
- Confirm that the interactive_terminal runner can launch the real Claude and Codex interactive CLIs.
- Confirm that each real agent reads the attempt prompt and writes the requested artifact verdict.yaml.
- Keep any generated implementation work out of main.

## Acceptance Criteria

- The design step produces a draft design document or aborts with a concrete reason.
- The review-design step reads the design attempt context and produces a pure YAML artifact verdict.
- The run log records artifact as the verdict source for the attempted real-agent steps.
