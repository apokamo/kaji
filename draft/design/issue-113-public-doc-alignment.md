# [設計] 公開向けに README と docs の前提を現行設計に揃える

Issue: #113

## 概要

公開前チェックで判明した README と docs の前提ズレを解消し、初見ユーザーが現行の `kaji` 設計を誤解しない状態にする。

## 背景・目的

現状、README と一部ドキュメントには次のズレがある。

- skill 配置が `.claude/skills/` 実体 / `.agents/skills/` symlink という現行方針に揃っていない
- `resume` と `inject_verdict` の見せ方が、現状の記事や想定運用とずれている
- README に最小導入例がなく、初見ユーザーが最初の一歩で詰まりやすい

公開状態でこのズレを残すと、記事・README・docs が別々のことを言っているように見え、導入判断やフィードバックの質を下げる。

## インターフェース

### 入力

- Issue #113 の要件
- 現行の README
- `docs/dev/skill-authoring.md`
- `docs/dev/workflow-authoring.md`
- 公開予定の記事で説明している導入・運用方針

### 出力

- README の記述更新
- `docs/dev/skill-authoring.md` の記述更新
- `docs/dev/workflow-authoring.md` の記述更新
- 初見ユーザー向けの最小導入例

### 使用例

```bash
# README に記載する想定の導入例
kaji run workflows/minimal-code-review.yaml 57
```

## 制約・前提条件

- `kaji` 本体の実装変更は必須ではなく、今回の主対象は README / docs の整合
- skill 配置については `.claude/skills/` を実体、`.agents/skills/` を symlink とする設計方針を前提にする
- `resume` / `inject_verdict` は機能として存在していても、公開向けの見せ方は現行運用に合わせて整理する
- 記事で説明している内容と矛盾しないこと

## 方針

1. README を公開向けの入口として再整理する
   - 最小導入例を載せる
   - skill 配置の前提を明示する
   - 詳細は docs へ送る
2. `skill-authoring.md` を現行設計に合わせる
   - skill 配置の説明を `.claude/skills/` 実体 / `.agents/skills/` symlink に更新する
   - 「ハーネスは skill の中身を読まない」と「VERDICT 契約に依存する」の関係を誤解なく説明する
3. `workflow-authoring.md` を公開向けに整理する
   - `resume` を主軸に説明する
   - `inject_verdict` は存在を残しつつ、前面に出しすぎない
   - サンプル workflow を現行の review / verify 運用に合わせる

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。

今回の変更はドキュメント中心のため、コードテストは原則対象外とする。ただし、公開導線として破綻しないことは手動で確認する。

### Small テスト
- README / docs のリンク切れがないことを確認する
- サンプル YAML / skill 名の記述が互いに一致していることを確認する

### Medium テスト
- なし
  理由: 今回はドキュメント変更が主であり、ファイルI/O や内部サービス結合を伴う新規機能追加はない

### Large テスト
- README の最小導入例を見た初見ユーザー視点で、`kaji run` の最初の一歩が理解できるかを手動確認する

### スキップするサイズ（該当する場合のみ）
- サイズ: Medium
  理由: 対象が docs の整合であり、Medium に相当する結合観点が存在しない

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 設計方針の追認であり、新規 ADR は不要 |
| docs/ARCHITECTURE.md | なし | 本件は主に公開向け docs の整合 |
| docs/dev/ | あり | `skill-authoring.md` と `workflow-authoring.md` を更新する |
| docs/cli-guides/ | なし | CLI 仕様自体は変更しない |
| CLAUDE.md | なし | 開発規約自体は変更しない |
| README.md | あり | 公開向け入口として整備する |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Issue #113 | https://github.com/apokamo/kaji/issues/113 | README / docs の前提ズレと対応範囲が整理されている |
| README | `README.md` | 公開向け入口だが、最小導入例と現行 skill 配置前提が不足している |
| Workflow 定義マニュアル | `docs/dev/workflow-authoring.md` | `resume` / `inject_verdict` の現状説明とサンプルの見直し対象 |
| スキル作成マニュアル | `docs/dev/skill-authoring.md` | skill 配置説明が現行の symlink 方針とズレている |
