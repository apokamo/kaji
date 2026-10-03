# incident ラベル運用ガイド（2 軸）

failure triage の第1層（インシデント検知・集約層。Issue #304 / EPIC #303）が扱う
`kaji:incident` ラベル体系の意味と遷移意図をまとめる。ラベルの宣言的定義は
[`.github/labels.yml`](../../.github/labels.yml)、一般的なラベル運用は
[labels.md](./labels.md) を参照。第1層の動作は
[workflow_guide.md](./workflow_guide.md) § 第1層: インシデント検知・集約。

## 2 軸の構成

incident ラベルは **種別キー 1 つ**と、直交する **2 軸**からなる。

- **種別キー** `kaji:incident`: 第1層が起票したインシデントイシューであることを示す検索キー。
  第1層はこのラベルで全件検索して照合する。起票時に必ず付与する。
- **status 軸**: インシデントの対応状況。
- **classification 軸**: インシデントの原因分類。

## ラベル一覧と遷移意図

以降のラベル名は既定名である。`[incident]` で名前を変えているリポジトリでは、設定名に読み替える。

| ラベル | 軸 | 付与者 | 意味 / 遷移意図 |
|--------|-----|--------|-----------------|
| `kaji:incident` | 種別キー | 第1層（起票時に必ず） | インシデントイシュー本体。検索・照合のキー |
| `kaji:incident:investigating` | status | 第1層（起票時の初期値） | 調査中。第1層が起票時に自動付与する初期状態 |
| `kaji:incident:mitigated` | status | 人間 | 暫定緩和済み（恒久対処は未完）。第2層以降の判断で人間が遷移 |
| `kaji:incident:resolved` | status | 人間 | 恒久解決済み。人間が付与。以後の同一署名一致はリグレッションとして扱う |
| `kaji:incident:cause:internal` | classification | 人間 | kaji 内部起因。第2層の調査結論を受けて人間が付与 |
| `kaji:incident:cause:upstream` | classification | 人間 | 上流（外部 CLI / API）起因。人間が付与 |
| `kaji:incident:cause:environment` | classification | 人間 | 実行環境起因。人間が付与 |
| `kaji:incident:cause:transient` | classification | **第1層が自動付与** | 一過性。auto-resume 自己回復時に第1層が付与し即クローズ |

## ラベル名は設定で変更できる（既定は `kaji:` 接頭辞付き）

第1層が起票・検索・遷移で使うラベル名は `.kaji/config.toml` の `[incident]` で変更できる
（key の仕様: [configuration.md § `[incident]`](../reference/configuration.md)）。
未設定時の既定名は `kaji:` 接頭辞付きで、本番障害などの一般的な incident と区別しやすくするため。
kaji のコードが参照するのは次の 3 ラベルだけで、残りは人間が付けるため設定 key を持たない
（推奨する命名として下表に示す）。

| 既定名（推奨名） | 設定 key |
|------------------|----------|
| `kaji:incident` | `kind_label` |
| `kaji:incident:investigating` | `initial_status_label` |
| `kaji:incident:mitigated` | —（人間が付与） |
| `kaji:incident:resolved` | —（人間が付与） |
| `kaji:incident:cause:internal` | —（人間が付与） |
| `kaji:incident:cause:upstream` | —（人間が付与） |
| `kaji:incident:cause:environment` | —（人間が付与） |
| `kaji:incident:cause:transient` | `transient_label` |

- 旧名の互換検索・互換判定は持たない。重複検索は `kind_label` だけで行い、transient 判定は
  `transient_label` だけで行う。旧名を使い続けるリポジトリは `[incident]` で旧名を指定する（[旧名からの移行手順](#旧名からの移行手順)）。
- incident 本文末尾のガイドリンクの参照先は `labels_guide_path` で変更できる。

## 自動付与の範囲（第1層がラベルに触れる箇所）

第1層が**自動で**ラベルを操作するのは次の 2 箇所のみ。それ以外の遷移は人間が行う。

1. **起票時**: `kaji:incident` + `kaji:incident:investigating` を付与する。
2. **transient 即クローズ時**: `--auto-recover` の child run が `COMPLETE`（自己回復）し、かつ
   この run が起票したインシデントに対して、`kaji:incident:cause:transient` を付与し
   `kaji:incident:investigating` を外してクローズする。

status 軸の `mitigated` / `resolved` と、classification 軸の `internal` / `upstream` /
`environment` は**すべて人間が付与する**。第1層はこれらを自動で付けない。

## 照合規則との関係

ラベルは照合（`plan_incident_action`）の分岐条件になる。

| 一致した既存インシデントの状態 | 第1層のアクション |
|--------------------------------|-------------------|
| open | occurrence コメントを追記（回数 +1） |
| closed かつ `kaji:incident:cause:transient` あり | reopen せず occurrence コメントを追記 |
| closed かつ `kaji:incident:cause:transient` なし（人間 resolve 済み） | 新規起票し旧イシューへリンク（リグレッション検知） |
| 一致なし | 新規起票 |

- `kaji:incident:cause:transient` が付いた closed インシデント（第1層の自動クローズ分）は、以後も
  closed のまま occurrence が追記され、頻発パターンの昇格判断材料になる。
- 人間が `kaji:incident:resolved` 相当でクローズしたインシデントに同一署名が再来した場合は、
  reopen せず新規起票して旧イシューへリンクする（resolve 済みを蒸し返さず、リグレッションを
  独立に追跡する）。

## 第1層が incident 記録しない cause

`INCIDENT_EXEMPT_CAUSES`（`kaji_harness/recovery/models.py`）に属する cause は、新規起票 /
再発追記 / ローカル `occurrences.jsonl` 追記のいずれも行わない（照合の母集団に入らない）。
triage コメント・run artifact・console 表示は維持され、失われる情報はない。

| cause | 除外理由 | 出典 |
|-------|----------|------|
| `user_precondition_error` | 既知のユーザー前提エラー（例: tmux セッション必須）。原因と対処がエラー文に含まれ、障害調査を要さない | Issue #322 |
| `user_interrupted` | 利用者による中断（Ctrl-C）。harness の不具合ではなく、再開要否は人間が artifact を見て判断する | Issue #403 |
| `agent_declared_abort` | agent が返した正規の ABORT verdict（安全停止・手動確認要求）。契約上の正常終端であり障害ではない | Issue #405 |
| `cycle_exhausted` | cycle が `max_iterations` に到達した安全弁の正常作動。triage コメントが `--reset-cycle` の次アクションを既に提示する | Issue #405 |

`agent_declared_abort` / `cycle_exhausted` は例外を伴わない終端のため、識別署名の
canonical input（`attempt_error` / `workflow_end_error`）が常に空になり、fingerprint が
cause ごとの定数へ退化する。`cause` 自体は照合キーに含まれるため、この 2 cause 同士が
混ざることはない。除外前はこの退化により、同じ cause 内で対象 step や実際の停止理由が
異なる安全停止が、cause ごとに 1 つの incident イシューへ誤って集約されていた（Issue #405）。

## 遷移の機械強制はしない

status 軸・classification 軸の遷移順序（例: investigating → mitigated → resolved）は
**機械的に強制しない**。第1層は初期状態と transient 自動クローズだけを担い、以降の運用判断
（原因分類・緩和・解決の宣言）は人間と第2層（調査・提案。Issue #305）に委ねる。

## 調査フローと処遇判断（第2層・Issue #305）

第2層（インシデント原因調査・対応策提案。手動起動）は、第1層が起票したインシデントイシューを入力に
**調査 → 査読 → 修正 → 確認 → 最終提案**のレビュー収束サイクルを回す。詳細は
[workflow_guide.md](./workflow_guide.md) § 第2層: 調査・提案。

- **起動**: `/incident-cycle <incident_issue_id>`（手動のみ）。
- **出力**: 調査結論（conclusion）＋対応策＋処遇メニューを含む最終提案コメント。**ラベル遷移・
  クローズ・バグイシュー化・統合の実行は行わない**（すべて人間の処遇判断）。
- **調査結論とレビュー verdict は別軸**（EPIC #303 決定 D）: 証拠不足のときは `INCONCLUSIVE`
  （棄却済み仮説＋不足証拠）を返し、記述が十分ならレビュー品質としては PASS になり得る。

### conclusion → 推奨ラベル・後続アクション（処遇メニュー）

最終提案コメントが提示する対応表。**実行はすべて人間**。第2層はラベルに触れない。

| 調査結論（conclusion） | 推奨 classification ラベル | status 軸の目安 | 後続アクション（人間が実行） |
|------------------------|----------------------------|-----------------|------------------------------|
| `internal-bug` | `kaji:incident:cause:internal` | `kaji:incident:mitigated` → `kaji:incident:resolved` | バグイシュー化ドラフトの起票、緩和策 / 恒久対策の判断 |
| `upstream` | `kaji:incident:cause:upstream` | `kaji:incident:mitigated` 等 | 上流 issue への報告 / watch、回避策の適用 |
| `environment` | `kaji:incident:cause:environment` | `kaji:incident:mitigated` 等 | 実行環境の修正、運用手順の更新 |
| `transient` | `kaji:incident:cause:transient`（第1層が自動付与済みの場合あり） | closed 維持が多い | 再発頻度を監視し、頻発なら昇格判断 |
| `duplicate` | 統合先に準ずる | 統合先に集約 | 統合先イシューへの集約（実行は人間） |
| `INCONCLUSIVE` | 付与しない | `kaji:incident:investigating` 維持 | 不足証拠を収集後に再調査 |

- `kaji:incident:cause:*` の付与、`kaji:incident:mitigated` / `kaji:incident:resolved` への遷移は、第2層の提案を
  受けて**人間が付与する**（第1層の transient 自動付与を除く）。第2層はラベルを自動で操作しない。
- `risk-accepted` は人間専用の処遇語彙であり、第2層エージェントの出力（conclusion / 提案文面）には
  現れない。リスク受容の宣言は人間が行う。

## 旧名からの移行手順

kaji v0.21.0 より前の既定名（`kaji:` 接頭辞なし）のラベルを使っているリポジトリを、既定名へ移す手順。
既存の incident Issue の履歴（付与済みラベル）を保つため、**`gh label edit --name` による in-place 改名**を使う。
旧名の互換検索・互換判定は kaji にない（[ADR 008](../adr/008-no-backward-compat-layer.md)）ため、
旧名のラベルと既定の設定が同時に存在する期間は、incident の記録が欠落する（後述）。

### 1. 影響の判定

`gh label list --search incident --limit 100` でラベルを確認し、`.kaji/config.toml` の `[incident]` の
有無と合わせて判定する。

| `[incident]` | GitHub 上のラベル | 状態 | 対応 |
|--------------|-------------------|------|------|
| 旧名を指定 | 旧名 | 旧名で正常運用中 | 維持するなら何もしない（下記「旧名を維持する場合」）。移行するなら改名 → `[incident]` 削除 |
| なし（既定） | 旧名 | **影響あり**（既定名のラベルがなく、記録が欠落している） | 直ちに改名する（設定変更は不要） |
| なし（既定） | 既定名 | 移行済み | なし |

### 2. 旧名を維持する場合

移行しない場合は、`.kaji/config.toml` に旧名を明示する。第2層 skill の前提ガードもこの設定値に従う。

```toml
[incident]
kind_label = "incident"
initial_status_label = "incident:investigating"
transient_label = "incident:cause:transient"
```

### 3. 改名の順序と記録欠落の窓

- **ラベルの改名を先に行う**。宣言的なラベル同期（kaji の `labels-sync.yml` のような、追加と更新だけを行い
  削除しない仕組み）が先に新しい定義を反映すると、既定名のラベルが**空で**作られる。改名先の名前が
  既に存在すると `gh label edit --name` は失敗する。同期定義の更新（`.github/labels.yml` など）は
  改名より後にする。
- 改名してから `.kaji/config.toml` の `[incident]` を撤去して default branch へ反映するまでの間、kaji は
  旧名で起票・検索する。旧名のラベルは存在しないため、GitHub がラベルを拒否し、記録は WARNING を出して
  欠落する（fail-open で triage は継続する）。逆に設定を先に切り替えても、存在しない既定名を使うので同じである。
- この窓を最小にするため、次を守る。
  - ラベル同期定義の更新と `[incident]` の撤去を**同じ変更（同じ merge）**に入れる。
  - 実行中の `kaji run` がない時間帯に行い、反映が終わるまで新しい run を起動しない。週次などの定期同期が
    走る時刻をまたがない。
  - `kaji run` を起動する checkout を最新の default branch へ更新する。改名前に分岐した作業ブランチ
    （古い config を持つ）から `kaji run` を起動すると、旧名のままラベルを付ける。
  - 窓の間に終了した run の triage が WARNING を出していたら、その incident を手動で起票する。

### 4. 改名手順

付与の確認には closed を含む Issue と PR の両方を数える（ラベルを削除すると両方から付与が外れる）。

```bash
# ラベル L が付いた Issue / PR の番号集合（closed を含む）
labeled() {
  { gh issue list --state all --label "$1" --limit 1000 --json number --jq '.[].number'
    gh pr list --state all --label "$1" --limit 1000 --json number --jq '.[].number'; } | sort -n
}
```

1. **付与の保存**: 旧名 8 件それぞれについて、`labeled "<旧名>" > before-<旧名>.txt` で番号集合を保存する。
2. **組ごとの状態判定と処理**: `gh label list --search incident --limit 100 --json name` で、旧名・既定名の
   組ごとに有無を見て、次の表に従う。全組を無条件に改名しない。途中で失敗したら、この手順を最初からやり直す
   （判定は現在の状態だけで決まる）。

   | 状態 | 判定 | 処理 |
   |------|------|------|
   | A. 未移行 | 旧名あり・既定名なし | `gh label edit "<旧名>" --name "<既定名>"`（通常はこの経路） |
   | B. 移行済み | 旧名なし・既定名あり | 何もしない |
   | C. 空の既定名が先行 | 両方あり、`labeled "<既定名>"` が空 | `gh label delete "<既定名>" --yes` → A と同じ改名 |
   | D. 既定名が使用済み | 両方あり、`labeled "<既定名>"` が空でない | 付け替え → 確認 → 旧名を削除（下記） |
   | E. 異常 | 両方なし | 処理を止めて原因を調べる |

   **状態 A の 8 コマンド**（1 件でも失敗したら止め、原因を確認してから手順 2 をやり直す）:

   ```bash
   gh label edit "incident" --name "kaji:incident"
   gh label edit "incident:investigating" --name "kaji:incident:investigating"
   gh label edit "incident:mitigated" --name "kaji:incident:mitigated"
   gh label edit "incident:resolved" --name "kaji:incident:resolved"
   gh label edit "incident:cause:internal" --name "kaji:incident:cause:internal"
   gh label edit "incident:cause:upstream" --name "kaji:incident:cause:upstream"
   gh label edit "incident:cause:environment" --name "kaji:incident:cause:environment"
   gh label edit "incident:cause:transient" --name "kaji:incident:cause:transient"
   ```

   **状態 D の処理**:
   1. `labeled "<旧名>"` の各番号について、Issue なら
      `gh issue edit <番号> --add-label "<既定名>" --remove-label "<旧名>"`、PR なら
      `gh pr edit <番号> --add-label "<既定名>" --remove-label "<旧名>"` を実行する。
   2. 終了条件: `labeled "<旧名>"` が空になり、かつ `before-<旧名>.txt` の全番号が
      `labeled "<既定名>"` に含まれる。満たさなければ削除せず、残った番号に 1 を繰り返す。
   3. 終了条件を満たした後で `gh label delete "<旧名>" --yes` を実行する。
3. **設定とラベル同期定義の反映**: `.kaji/config.toml` から `[incident]` の旧名指定を削除し、ラベル同期定義の
   incident ラベル名を既定名に更新して、同じ変更として default branch へ反映する。
4. **確認**:
   - `gh label list --search incident` に既定名 8 件があり、旧名が 0 件である。
   - 8 組それぞれについて、`before-<旧名>.txt` の全番号が `labeled "<既定名>"` に含まれる
     （件数ではなく番号集合で照合する。改名後の新規起票が件数の不足を隠さないようにするため）。
   - ラベル同期の実行ログで、既定名のラベルが新規作成（`Created`）されていない。

### 5. 反映後に旧名のラベルが作り直された場合

反映が定期同期をまたぎ、旧い同期定義が旧名のラベルを空で作り直すことがある。`labeled "<旧名>"` が空で
あることを確認してから `gh label delete "<旧名>" --yes` で削除する。付与が残っている場合は、
手順 4 の状態 D の処理（付け替え → 終了条件の確認 → 削除）に従う。

## 関連ドキュメント

- [labels.md](./labels.md) — GitHub ラベル全体の運用ガイド
- [workflow_guide.md](./workflow_guide.md) § failure triage と自動再開 / 第1層
- [failure-recovery.ja.md](../cli-guides/failure-recovery.ja.md) — triage / recovery CLI リファレンス
