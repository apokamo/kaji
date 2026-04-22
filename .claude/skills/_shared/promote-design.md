# 設計書の恒久保存（昇格）手順

`i-dev-final-check` から参照される共有手順。
`draft/design/` に置かれた設計書のうち、アーキテクチャ決定や汎用ガイドラインとして恒久化する価値があるものを `docs/adr/` または `docs/dev/` に昇格する。

## 前提

- `draft/design/issue-[number]-*.md` が存在すること
- 設計書の内容が後述「いつ昇格するか」に該当すること

## いつ昇格するか

| 条件 | 昇格先 |
|------|--------|
| アーキテクチャに関わる技術選定・方針決定 | `docs/adr/NNNN-title.md` に ADR として |
| 汎用的な開発ガイドライン / ワークフロー規約 | `docs/dev/xxx.md` にガイドとして |
| 上記以外 | 昇格不要（Issue 本文へのアーカイブで十分） |

> 昇格は任意。すべての設計書が昇格対象ではない。該当しなければスキップし、`i-dev-final-check` が行う Issue 本文へのアーカイブのみで終える。

## 昇格手順

### 1. 昇格対象の判断

設計書の内容が「いつ昇格するか」の表に該当するか確認する。迷う場合は昇格不要と判断し、Issue 本文アーカイブで留める。

### 2. 昇格先ファイルの作成

| 昇格先 | 作成パス | 命名規約 |
|--------|----------|----------|
| ADR | `docs/adr/NNNN-title.md` | `NNNN` は既存 ADR の次番号（4 桁ゼロ埋め）、`title` は英小文字 kebab-case |
| 開発ガイド | `docs/dev/xxx.md` | `xxx` は英小文字 snake_case。既存ファイルと命名慣習を合わせる |

### 3. 内容の変換

`draft/design/issue-[number]-*.md` をそのままコピーせず、恒久ドキュメントとして独立した文書に書き直す:

- Issue 固有の文脈（Issue 番号への直接参照、worktree パス、`draft/design/` からの相対リンク等）を除去する
- 「背景」「OB/EB」等、設計時点の一時的な議論は要約し、決定事項のみ残す
- 参照先は `docs/` 配下からのパスで書き直す（`../../` で始まる相対パスを避ける）

### 4. 設計書からの参照

元の `draft/design/issue-[number]-*.md` の冒頭（概要セクション直後）に、昇格先ドキュメントへのリンクを追記する。これにより `i-dev-final-check` が Issue 本文へアーカイブした際にも、読み手が恒久版の所在を辿れる。

例:
```markdown
> 本設計は `docs/adr/0012-xxx.md` に昇格済み。最新の決定事項はそちらを参照。
```

### 5. コミット

昇格先ドキュメントと設計書の更新を 1 つのコミットにまとめる:

```bash
git add docs/adr/NNNN-title.md draft/design/issue-[number]-*.md
git commit -m "docs: promote design to docs/adr/NNNN-title for #[number]"
```

`docs/dev/` に昇格する場合も同様に `docs: promote design to docs/dev/xxx for #[number]` とする。

## 注意事項

- 昇格先ドキュメントは draft 設計書のコピーではなく、**恒久ドキュメントとして独立した内容**にする
- Issue 番号や worktree パス等の一時的な情報は昇格先に含めない
- `draft/design/issue-[number]-*.md` はそのまま残す（worktree 削除時に自然消滅する）
- ADR の番号衝突を避けるため、`ls docs/adr/` で既存の最大番号を確認してから採番する
