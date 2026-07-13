# ADR 010: Pydantic v2 for series input validation

- Status: Accepted
- Date: 2026-07-14

## Context

Sequential series YAML and its persisted resume state are new external input contracts. `AGENTS.md`
requires external input to be validated with Pydantic, while existing workflow YAML predates that
rule and uses dataclasses plus explicit validation. Reimplementing field checks in each CLI and in
`series-create` would create schema drift and would silently discard unknown keys unless every
consumer repeated the same defensive logic.

## Decision

Add `pydantic>=2` as a runtime dependency. `SeriesConfig` and `SeriesMember` are the sole structural
schema for `validate-series`, `run-series`, and the deterministic generator. `SeriesState` and
`MemberState` validate persisted JSON on load. All models forbid unknown fields; Pydantic reports
all structural field errors together.

Filesystem-dependent checks remain in the series loader: workflow paths must stay under the repo
root, exist, parse through `load_workflow()`, and require the GitHub or any provider. Existing
workflow parsing is unchanged; migrating that established contract is outside this decision.

## Consequences

- A malformed series fails loudly at every entry point with the same schema semantics.
- The generator cannot emit fields that the runtime does not understand.
- Runtime installation gains Pydantic and its transitive dependencies.
- Two validation styles coexist temporarily: Pydantic for the new series contracts and the existing
  dataclass validator for workflow YAML.
