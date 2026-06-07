# ADR 005: artifact `verdict.yaml` を primary とする verdict 受け渡し

## ステータス

承認 (2026-06-04)

## コンテキスト

kaji の harness は agent の stdout から verdict を抽出して workflow を遷移させてきた（`parse_verdict(result.full_output, ...)` が唯一の verdict source）。`docs/dev/skill-authoring.md` § verdict 出力規約 も「verdict は stdout にそのまま出力する」を唯一の契約としていた。

この stdout-only 方式は、headless で agent CLI を起動し JSONL stdout を直接読む実行形態を前提にしている。今後 `kaji` から Claude Code / Codex を subscription の通常コンソール利用に近い形（別ターミナル起動 / 通常 interactive CLI / 人間による handoff）で動かす場合、harness は agent の stdout を直接保持できない。`draft/lab/headless-terminal-spawn/design.md` の PoC では別ターミナルで agent を起動し sentinel / output file で回収する方針が示されているが、workflow の正規 verdict 経路としては未実装だった。stdout に依存しない完了判定経路が必要になった（Issue #220）。

あわせて、従来の step log layout（`runs/<run_id>/<step_id>/` への append）は、cycle / retry / resume で同一 step が同一 run 内に複数回 dispatch される場合に、prompt / logs / verdict と attempt の対応関係を一意に保てない弱さがあった。

## 決定

verdict の受け渡しを **artifact `verdict.yaml`（primary）→ 作業報告 Issue comment 末尾の `---VERDICT---` block（fallback）→ stdout parse（互換 fallback）** の順で解決する方式に変更する（Issue #220）。

- agent / script は同じ verdict block を作業報告コメント末尾と stdout に残し、harness が注入する `verdict_path`（exec_script では env `KAJI_VERDICT_PATH`）へ pure YAML の `verdict.yaml`（`status` / `reason` / `evidence` / `suggestion`）を保存する。interactive terminal runner では `verdict.yaml` の出現が完了トリガになるため、agent は外部副作用を完了してから最後に artifact を保存する。
- harness は `resolve_verdict()` で artifact → comment → stdout の順に解決する。artifact が存在すれば comment / stdout は見ない。comment fallback は当該 attempt の dispatch 直前に記録した `attempt_started_at` を下限に `created_at >= attempt_started_at` のコメントのみ対象とし、前 attempt の作業報告コメントを誤採用しない。
- `verdict.yaml` が「存在するが壊れている」場合は fail-loud（comment / stdout へ fallthrough しない）。全 source 不在は従来どおり `VerdictNotFound`。
- comment / stdout で解決した場合は harness が同じ verdict を `verdict.yaml` へ正規化保存し、未移行スキルでも attempt 単位の `verdict.yaml` が必ず残る。
- artifact / log layout を `runs/<run_id>/steps/<step_id>/attempt-NNN/`（attempt 単位）へ移行する。`run.log` は `runs/<run_id>/` 直下に据え置く。新規 run は新 layout を正とし、旧 flat layout の読み取り互換は温存する（migration 必須化はしない）。

stdout 経路は未移行スキル・既存 stdout ベーステストとの互換のため当面残す。全スキルが `verdict.yaml` を書く運用へ移行後、stdout 経路の段階廃止を別 Issue で検討する。

## 影響

- `docs/dev/skill-authoring.md` § verdict 出力規約 が stdout-only から 3 経路（artifact / comment / stdout）契約に変わる。`verdict_path` がコンテキスト変数に追加される。
- runner の step log 出力先が `runs/<run_id>/steps/<step_id>/attempt-NNN/` に変わる（`docs/reference/python/logging.md` / `docs/ARCHITECTURE.md` § 実行アーティファクトの layout）。
- workflow YAML / 遷移仕様 / `kaji run` の CLI IF（引数・exit code）は不変。
- 既存の stdout verdict ベーステストは、stdout 互換経路の温存により破壊されない。

## 代替案と却下理由

| 代替案 | 却下理由 |
|--------|----------|
| Issue comment を verdict の primary source にする | comment は人間向け作業報告履歴であり、attempt 境界・取得タイミングが API 依存で不安定。primary には不適 |
| verdict 専用 Issue comment を新規作成する | 作業報告 comment と分離すると投稿数が倍増し追跡性が下がる。既存作業報告 comment の末尾追記で十分 |
| harness が stdout を parse して `verdict.yaml` を書くだけ（agent は書かない） | future の no-stdout runner では harness に stdout が無く成立しない。agent 側が書ける契約が前提 |
| comment 本文に run/step/attempt marker を埋め込み attempt を識別する | agent 挙動依存で comment metadata を増やす方針に反する。harness 制御の `created_at` 下限で十分 |
