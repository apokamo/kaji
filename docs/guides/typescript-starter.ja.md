# TypeScript Starter ガイド

Language: [English](typescript-starter.md) | 日本語

[kaji-starter-typescript](https://github.com/apokamo/kaji-starter-typescript)
は、kaji workflow、TypeScript 6、typed ESLint、Prettier、Vitest S/M/L test、
coverage、CI、project-local kaji を接続した framework 非依存の Node.js
application starter です。

## 作成とセットアップ

Linux / macOS / WSL2、Node 24.18.1、npm 11.16.0、uv、対応 agent CLI が前提です。

1. **Use this template** で repository を作成し、clone 後に `make setup` を
   実行します。
2. `starter-app` package 名と `.kaji/config.toml` の repository identity を
   まとめて変更します。既定値一式は有効ですが、部分変更は static gate が拒否します。
3. `make check` を実行します。以降 identity を変更したときも、commit 前に必ず
   `make check` を再実行し、static gate に最終状態を検査させます。
4. exact lockfile を維持し、workflow 前に初期設定を commit します。
5. kaji は常に `./scripts/kaji` から起動します。

GitHub 開発は `.kaji/wf/custom/dev/dev.yaml`、local 試行は
`./scripts/kaji local init` 後に `.kaji/wf/custom/local/dev-local.yaml` を使います。

`npm run set-agent -- codex` は全 workflow を atomic・冪等に変換します。対応
target は Claude / Codex のみです。Gemini は kaji v0.18.0 で廃止済み、
Antigravity は `resume` 契約を満たせないため、いずれも変更前に拒否します。

`make check` は format、typed lint、typecheck、effective tag audit、S/M/L 全 test、
80% coverage、build、workflow、docs、supply-chain、actionlint を確認します。
`make setup` 後は network を使わず tracked file を変更しません。

TypeScript 7 native port は typed lint が必要とする compiler API を提供せず、
typescript-eslint 8.65.0 の対応範囲は TypeScript 6.1 未満です。このため 6.0.3 を
固定します。`@typescript/typescript6` の併用は compiler identity と更新経路を
二重化するため初期版では採りません。

upstream の任意 `review-poll` step は初期構成に含めません。外部 review bot を
設定済みのチームだけが custom workflow へ追加し、追加後に
`make validate-workflows` を再実行してください。

問題は [kaji Issue tracker](https://github.com/apokamo/kaji/issues)へ報告してください。
