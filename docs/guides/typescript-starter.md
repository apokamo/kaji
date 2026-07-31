# TypeScript Starter Guide

Language: English | [日本語](typescript-starter.ja.md)

Use
[kaji-starter-typescript](https://github.com/apokamo/kaji-starter-typescript)
to start a framework-neutral Node.js application with kaji workflows,
TypeScript 6, typed ESLint, Prettier, Vitest S/M/L tests, coverage, CI, and
project-local kaji.

## Create and set up

Prerequisites are Linux, macOS, or WSL2; Node 24.18.1; npm 11.16.0; uv; and
at least one supported agent CLI.

1. Select **Use this template**, clone the generated repository, and run
   `make setup`.
2. Change the `starter-app` package name and `.kaji/config.toml` repository
   identity together. The pristine defaults are valid, but a partial identity
   change fails the static gate.
3. Run `make check`, and re-run it after every later identity edit so the
   static gate always inspects the state you are about to commit.
4. Keep the exact lockfiles and commit the initial setup before starting a
   workflow.
5. Use `./scripts/kaji` for every kaji command.

GitHub development starts with `.kaji/wf/custom/dev/dev.yaml`. For a local
trial, run `./scripts/kaji local init`, create/start a local issue, and select
`.kaji/wf/custom/local/dev-local.yaml`.

## Agent and quality commands

Convert all workflow files atomically with `npm run set-agent -- codex`; only
`claude` and `codex` are supported. Gemini was removed from kaji v0.18.0, and
Antigravity cannot satisfy workflows that use `resume`, so both fail before
files are changed.

`make check` runs format check, typed lint, no-emit typecheck, effective-tag
audit, all S/M/L tests with 80% V8 coverage, a clean TypeScript build, kaji
workflow validation, docs links, supply-chain checks, and actionlint. After
`make setup`, it uses no network and does not modify tracked files.

## Tool compatibility

The starter pins TypeScript 6.0.3 because typescript-eslint 8.65.0 supports
TypeScript below 6.1 while the TypeScript 7 native port does not expose the
compiler API used by typed lint tooling. A side-by-side
`@typescript/typescript6` alias adds two compiler identities and upgrade paths,
so the initial starter keeps one exact TypeScript 6 graph.

Node native type stripping runs `.ts` source directly in development. `tsc`
builds `dist/` and rewrites relative `.ts` imports to `.js`; no `tsx`,
`ts-node`, or bundler is required.

The optional upstream `review-poll` step is not bundled. Teams with an external
review bot may add it to custom workflows and must re-run workflow validation.
Report defects in the
[kaji issue tracker](https://github.com/apokamo/kaji/issues).
