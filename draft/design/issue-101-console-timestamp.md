# [設計] コンソール出力にタイムスタンプを追加する

Issue: #101

## 概要

`kaji run` のコンソール出力（`stream_and_log` の `print` 呼び出し）にタイムスタンプを追加し、各イベントの発生時刻を把握できるようにする。

## 背景・目的

kaji のコンソール出力は `stream-json` のイベント単位で表示されるため、リアルタイム感が薄い（#100 参照）。タイムスタンプを付与することで、各ステップの所要時間や進捗の時間感覚を低コストで補える。

## インターフェース

### 入力

変更なし。既存の `stream_and_log` 関数のシグネチャは維持する。

### 出力

ターミナル出力（`print`）のフォーマットが変更される。

**変更前:**

```
[step_id] テキスト
```

**変更後:**

```
[2026-03-13T15:04:23] [step_id] テキスト
```

### 使用例

```python
# 変更は内部実装のみ。ユーザーコードへの影響なし。
# kaji run workflow.yaml 42 実行時のターミナル出力が変わる。
```

## 制約・前提条件

- タイムスタンプフォーマット: `YYYY-MM-DDTHH:MM:SS`（ISO 8601、ローカルタイム、タイムゾーン表記なし）
- タイムスタンプと step_id は別ブラケットで分離（パース容易性・既存出力との互換性）
- `CLIResult.full_output` にはタイムスタンプを含めない（下流のパーサー、特に verdict パーサーに影響を与えないため）
- `console.log` ファイルにもタイムスタンプを含めない（Issue の対象は `print` 呼び出しのみ）
- `stdout.log` は生の JSONL をそのまま記録するため変更しない

## 方針

`stream_and_log` 関数内の 2 箇所の `print` 呼び出し（L110, L123）にタイムスタンプを追加する。

```python
from datetime import datetime

# タイムスタンプ生成（ヘルパー関数）
def _now_stamp() -> str:
    """現在時刻を ISO 8601 形式（秒精度、タイムゾーンなし）で返す。"""
    return datetime.now().isoformat(timespec="seconds")

# print 呼び出しの変更（2箇所）
# 変更前: print(f"[{step_id}] {text}")
# 変更後: print(f"[{_now_stamp()}] [{step_id}] {text}")
```

変更対象:
- `kaji_harness/cli.py` の `_now_stamp` ヘルパー追加（モジュールプライベート）
- `stream_and_log` 内の `print` 2 箇所のフォーマット変更

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。

### Small テスト

- `_now_stamp()` の戻り値フォーマット検証: ISO 8601 形式（`YYYY-MM-DDTHH:MM:SS`）であること
- `_now_stamp()` が `datetime.now()` を使用していること（`freezegun` または `unittest.mock.patch` で固定時刻を注入し、期待値と一致することを検証）

### Medium テスト

- `stream_and_log` の `verbose=True` 時、`print` 出力にタイムスタンプが含まれること（`capsys` でキャプチャし、`[YYYY-MM-DDTHH:MM:SS]` パターンを正規表現で検証）
- `stream_and_log` の `verbose=True` 時、非JSON行の `print` 出力にもタイムスタンプが含まれること
- `CLIResult.full_output` にタイムスタンプが含まれ**ない**こと（既存テストの暗黙的保証だが、明示的に検証）
- `console.log` にタイムスタンプが含まれ**ない**こと

### Large テスト

### スキップするサイズ（該当する場合のみ）

- Large: `kaji run` の E2E テストには実際の agent CLI（claude/codex/gemini）のインストールが必要であり、CI 環境にこれらを用意する手段が存在しない。Medium テストでサブプロセス結合まで検証するため、タイムスタンプ表示の検証としては十分。

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 新しい技術選定なし |
| docs/ARCHITECTURE.md | なし | アーキテクチャ変更なし |
| docs/dev/ | なし | ワークフロー・開発手順変更なし |
| docs/cli-guides/ | なし | CLI の引数・サブコマンド仕様に変更なし（出力フォーマットのみ） |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Python datetime.isoformat | https://docs.python.org/3/library/datetime.html#datetime.datetime.isoformat | `timespec="seconds"` で `YYYY-MM-DDTHH:MM:SS` 形式を得る。Issue 要件の「ISO 8601、タイムゾーン表記なし」に合致 |
| 対象コード | `kaji_harness/cli.py` L78-140 (`stream_and_log`) | `print(f"[{step_id}] {text}")` が L110, L123 の 2 箇所に存在。これらがタイムスタンプ追加の対象 |
| Issue #101 | GitHub Issue #101 | フォーマット仕様: `[YYYY-MM-DDTHH:MM:SS] [step_id] テキスト` |
