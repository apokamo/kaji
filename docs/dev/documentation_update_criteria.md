# Documentation Update Criteria

docs 更新要否を各フェーズで判断するための基準。

## 各フェーズで確認すること

| フェーズ | 確認内容 |
|----------|----------|
| design | 影響ドキュメント候補を設計書に列挙する |
| review-design | 影響評価が過不足なく行われているか確認する |
| implement | 実装差分に照らして docs 更新要否を再確認する |
| review-code | 差分に対する docs 更新漏れがないか確認する |
| final-check | PR に含める docs が揃っているか最終確定する |

## 更新が必要になりやすいケース

- workflow や運用手順が変わる
- quality gate やコマンド（`make check` / `make verify-docs` 等）の正本が変わる
- skill の責務や遷移が変わる
- docs-only / dev workflow の使い分けが変わる
- 設計書昇格ルールや恒久 docs の責務が変わる
- CLI 引数・出力（`kaji run` / `kaji validate` 等）が変わる
- アーキテクチャ・モジュール構成・依存関係が変わる
- 規約（コーディング規約・コミット規約等）が変わる

## 更新対象ドキュメントの目安

| ドキュメント | 主な更新トリガー |
|-------------|----------------|
| `AGENTS.md` / `CLAUDE.md` | 常時適用ルール・agent 向け指示・ツール設定の変更 |
| `docs/ARCHITECTURE.md` | モジュール構成・依存関係・データフローの変更 |
| `docs/adr/` | 重要な技術選定・方針決定 |
| `docs/cli-guides/` | CLI コマンドの追加・変更・削除 |
| `docs/dev/` | 開発ワークフロー・手順・規約の変更 |
| `docs/guides/` | 運用ガイド・手順の変更 |
| `docs/reference/python/` | Python コーディング規約の変更 |

## 更新不要と判断しやすいケース

- 実装内部のみの軽微修正で外部仕様・運用が不変
- テストコードのみの追加で参照手順に変更がない
- 既存 docs が現状をすでに正確に表している

## docs-only で止めるべきケース

- docs だけでは整合が取れず、コード・設定・テストの変更が必要
- docs に書くと現行実装と矛盾する
- workflow 定義と実体がずれており、skill / YAML の更新が不可欠

## 設計書「影響ドキュメント」テーブルとの関係

設計書に記載された「影響ドキュメント」テーブルは設計時点の予測。`/i-dev-final-check` では実装後の実際の変更内容に基づいて最終確定する。

## Quickref と正本の同期

quickref は、毎回必要な最小規律と「状況 → 正本セクション」のポインタに限定し、詳細規則・例外・閾値の正本にしない。

- 正本 docs の見出し、パス、規範、コマンドが変わる変更では、参照する quickref の pointer と最小規律も同じ変更で監査する
- quickref の規範的記述を変更する場合は、対応する正本 docs を先に更新し、quickref を追従させる
- 同じ規則本文を quickref と正本へ複製しない。短い不変条件と読込タイミングだけを quickref に置く
- link check に加え、pointer が意図した正本セクションを指すことをレビューで確認する

現行の実装用 quickref は [implement-quickref.md](./implement-quickref.md)。同期責務は `/issue-implement`、`/issue-review-code`、`/i-dev-final-check` に関わる docs または skill を変更する担当者が負う。
