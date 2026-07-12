# cli_main.py 分割（#283 R1）patch target 検証成果物

`kaji_harness/cli_main.py` を `kaji_harness/commands/` へ機械的分割した際の、
tests 側 patch/monkeypatch target 書換えの一回性検証用データ。設計書
`draft/design/issue-283-refactor-cli-main-py-kaji-harness-comman.md`
§ patch target 対応表 / § 行単位検証 に対応する。

`scripts/inventory_cli_main_patch_targets.sh` が
`tests/` 内の `kaji_harness.cli_main.<symbol>` 参照を全件抽出する。

| ファイル | 行数 | 意味 |
|----------|------|------|
| `patch_targets_baseline.tsv` | 131 | 分割前（R0）の frozen baseline。全件 `kaji_harness.cli_main.*` |
| `patch_targets_retained.tsv` | 68 | 属性 patch（`subprocess` / `shutil`）。旧 target のまま維持する |
| `patch_targets_rewrite.tsv` | 63 | 名前再束縛 patch。実体 module namespace へ機械的書換え（old → new） |

## 検証（分割完了後）

```bash
# 維持 68 件の不変: 書換え後 inventory が retained TSV と byte 一致
bash scripts/inventory_cli_main_patch_targets.sh | diff - scripts/cli_main_split_283/patch_targets_retained.tsv
```

差分ゼロなら「63 件が `cli_main` namespace から消え、68 件だけが残った」ことの機械証明となる。

分割後は検出対象そのものが消滅する一回性検証のため、恒久 pytest テストにはしない
（`docs/dev/testing-convention.md` 恒久テスト不要 4 条件の 3「回帰検出情報が増えない」）。
最終的な tests import 移行と shim 削除は #284 で行う。
