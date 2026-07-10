# AI Documentation Management Policy

Language: English | [日本語](ai-docs-management.ja.md)

## Overview

Docs-as-Code operating rules for kaji. Documentation is managed in the same
repository as code and goes through the same review process.

## Principles

### 1. Documentation is part of the code

- Manage Markdown under `docs/`
- Include documentation updates caused by code changes in the same PR
- Check documentation impact in each phase and finalize it in `/i-dev-final-check`

### 2. Progressive disclosure

- Keep documents small
- If they grow, structure and split them
- Do not write information that can be inferred from code

### 3. Deleting is harder than adding

- Decide whether a new addition is truly necessary
- Actively delete and slim down unnecessary information
- Do not write concrete code in documentation; point to the real code instead

## Documentation structure

Documentation is categorized based on the
[Diataxis framework](https://diataxis.fr/). The source of truth for category to
directory mapping is [docs/README.md](../README.md) (documentation index). This
document does not duplicate that table.

## Workflow integration

The primary source for deciding whether documentation updates are needed is
[documentation_update_criteria.md](../dev/documentation_update_criteria.md).
This section describes how that decision is integrated into workflows.

### dev / dev-thorough

Documentation consistency is protected by three lines of defense:

1. **Design document table**: include an "Affected documentation" table in the
   design document and make the expected scope explicit
2. **Impact checks inside each cycle**: check impact-scope diffs in the design,
   implementation, and review cycles
3. **`/i-dev-final-check`**: gate completeness before PR creation and send work
   back if anything is missing

### docs

1. Update documentation with `/i-doc-update`
2. Review consistency with `/i-doc-review`
3. Run link checks and completion-condition verification with `/i-doc-final-check`

## Design document lifecycle

| Phase | Location | Description |
|-------|----------|-------------|
| In progress | `draft/design/issue-XXX-*.md` | In the worktree; committed |
| At final-check | Archived in the issue body | Appended to the body under a collapsible `<details>` tag (responsibility of the `/i-dev-final-check` skill) |
| Permanent | `docs/adr/` or `docs/dev/` | Promoted as an ADR / general guide only when applicable (procedure: `.claude/skills/_shared/promote-design.md`) |
