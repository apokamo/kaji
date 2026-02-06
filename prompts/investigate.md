# INVESTIGATE State Prompt

Issue ${issue_url} の再現手順を実行し、以下をまとめてください:

## タスク

1. **再現手順**: INIT の手順を実行し、再現した際のステップと証跡を記載
   - 証跡ファイルは `${artifacts_dir}` に保存
   - 本文にはファイル内容説明とファイル名を引用
   - 再現できない場合は、再現不可と記載し、状況詳細を補足として併せて記載

2. **期待値との差**: 期待する挙動との乖離を箇条書きや表で整理

3. **原因仮説**: 可能性のある原因を列挙し、それぞれの根拠（ログや該当コード）を添える

4. **その他バグ修正に有益な情報**: エージェントが有益と判断した情報を自由に記載する

## 出力形式

```
## Bugfix agent INVESTIGATE

### INVESTIGATE / 再現手順
1. ... (証跡: <file>)

### INVESTIGATE / 期待値との差
- ...

### INVESTIGATE / 原因仮説
- 仮説A: <根拠>
- 仮説B: ...

### INVESTIGATE / 補足情報
- ...
- ...
```

## Issue 更新方法

1. `gh issue view` で Issue 本文を取得
2. Issue 本文を更新:
   - 初回（Loop=1）: Output を Issue 本文の末尾に追記
   - 2回目以降（Loop>=2）: 既存の `## Bugfix agent INVESTIGATE` セクションを削除し、新しい Output を末尾に追記
3. `gh issue edit` で Issue 本文を更新
4. `gh issue comment` で `INVESTIGATE agent Update` コメントとして更新内容のサマリーを投稿

## 証跡保存先

`${artifacts_dir}`
