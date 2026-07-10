---
status: draft
phase: 3ab
parent: phase3-design.md
created: 2026-05-05
branch: feat/local-phase3ab
worktree: /home/aki/dev/kaji/kaji-feat-local-phase3ab
---

# [実装報告] kaji local mode — Phase 3-ab: providers/ package + LocalProvider/GitHubProvider

phase3-design.md の **PR-3a + PR-3b を統合した PR-3ab** として実装した。
GitHub が利用不可のため PR は作らず、本書で作業内容を報告する。

## サマリー

| 項目 | 値 |
|------|----|
| ブランチ | `feat/local-phase3ab` |
| Worktree | `/home/aki/dev/kaji/kaji-feat-local-phase3ab` |
| 変更ファイル数 | kaji_harness 7 / tests 4 / 計 11（すべて新規） |
| 追加 LoC | 2041（実装 1415 + テスト 626） |
| `make check` | 緑（770 passed, 1 skipped, 60.9s） |
| dispatcher 切替 | **未実施**（PR-3c に持ち越し）。既存 `cli_main.py` / `prompt.py` は変更していないので、外部挙動は完全に従来どおり |

## 統合判断（PR-3a + 3b → PR-3ab）

設計書では 5 段（3a / 3b / 3c / 3d / 3e）で進める計画だったが、user との相談で **3a と 3b を統合**した。GitHub が利用不可で「PR」が local merge (`--no-ff`) になる前提では、外部レビュー単位で分割する利得がほぼ消えるため。

統合の利点（実装で確認できた範囲）:

- contract の往復が実際に発生した。`Issue.slug` field の追加 / `IssueContext.branch_prefix_fallback` の用途 / `LocalProvider` 配下の cache reader を provider 全体の `is_readonly` と切り分ける必要などは、provider 実装着手後に確定した。3a 単独で contract を凍結していたら 3b で revisit する PR が増えていた
- diff 規模 ~2000 行は単一 PR としては大きいが、commit 境界（1 step = 1 commit を想定）で bisect 性は維持できる粒度

懸念点（記録）:

- self-review で見落とすリスクは残っている。報告書（本書）で構造を明示しレビューしやすくする方針で吸収

## 実装範囲

### 追加した module（`kaji_harness/providers/`）

| ファイル | 内容 | LoC | 設計書対応 |
|----------|------|-----|------------|
| `__init__.py` | `ResolvedId` / `normalize_id`、package re-export | 136 | phase3-design.md L286 |
| `base.py` | `IssueProvider` Protocol（`runtime_checkable`） | 88 | phase3-design.md L287 |
| `models.py` | `Issue` / `Comment` / `Label` / `IssueContext` dataclass | 97 | phase3-design.md L288 |
| `_mappings.py` | `LABEL_TO_PREFIX` の正本（Skill markdown から Python へ移譲） | 42 | phase3-design.md L324-346 |
| `context.py` | `validate_slug` / `derive_slug_from_title` / branch / worktree / design path builder | 82 | phase3-design.md L348-364 |
| `github.py` | `GitHubProvider`（既存 cli_main.py の gh 呼び出しは未撤去、本 provider を並列に追加） | 299 | phase3-design.md L289 |
| `local.py` | `LocalProvider` + frontmatter parse/serialize + `_atomic_write` + `_counter_lock` + `next_local_id` + `resolve_issue_dir` + remote cache reader + `IssueContext` 解決 | 671 | phase3-design.md L290 |

### 追加したテスト

| テスト | 範囲 | size | テスト数 |
|--------|------|------|---------|
| `test_providers_normalize_id.py` | `normalize_id` 全パターン（github / local / 短縮 / 数値 + machine_id / `gh:` / 不正 / machine_id 欠落 / 大文字拒否） | small | 12 |
| `test_providers_context.py` | label mapping / slug 検証 / slug 導出 / path builder | small | 14 |
| `test_providers_github.py` | subprocess mock pass-through、payload parse、IssueContext 解決、引数組み立て、エラー伝搬 | small | 7 |
| `test_providers_local.py` | machine_id 検証 / frontmatter round-trip / atomic write / Issue CRUD 全経路 / counter & 既存 dir max / comment seq / list 絞り込み / dir 重複検出 / IssueContext 解決 / cache reader / readonly 経路 | medium | 27 |

合計 60 ケース追加、全 770 ケース緑。

### 設計書からの解釈・微調整

phase3-design.md の本文に対し、実装過程で以下を解釈・微調整した（本書を一次情報として確定する）:

1. **`is_readonly` の粒度**
   設計書は「provider 全体の bool」として扱っていたが、`LocalProvider` 配下の `gh:N` 経路（remote_cache）だけを read-only にしたいケースがある。`LocalProvider.is_readonly` は False のまま、`is_readonly_id(resolved_kind: str)` で経路別に判定する API を追加した。CLI 層は `ResolvedId.kind == "remote_cache"` を見てから write 系を弾く想定（PR-3c で組み込み）。

2. **コメント直下 `comments/<seq>-<machine>.md` の frontmatter**
   設計書 L931 はファイル名規約のみ提示。本実装では `author` / `created_at` を frontmatter で持ち、本文を本文として残す形式に確定した。`_serialize_frontmatter` を comment にも流用。

3. **frontmatter parser/serializer の自前実装**
   PyYAML 等への新規依存を避けるため、必要最小限の serializer/parser を `local.py` 内に実装した。対象は `str` / `int` / `bool` / `list[str]` / `list[dict[str,scalar]]` のみ。
   実装中に `"type:feature"` のような `:` を含むスカラーを list 内で書くと parser が dict と誤認するバグを発見し、quote 付き scalar を先に判定するよう修正した（`local.py:213-225`）。テスト `TestFrontmatter::test_list_labels` はこの retest を兼ねている。

4. **machine_id 検証の二重設置**
   `normalize_id` 内（数値 ID で machine_id を補完する経路）と `LocalProvider.__post_init__` の双方で `[a-z0-9]{1,16}` を検証する。CLI 側で setup ミスを早期に弾くため、provider 構築自体を fail-fast 化した。

5. **`GitHubProvider` における `slug` 引数の扱い**
   GitHub では title から slug を導出する設計（phase3-design.md L362）のため、`create_issue(slug=...)` 引数は受け取るが採用しない（`del slug` で明示的に破棄）。Protocol 互換のための signature 統一目的。

6. **既存 `cli_main.py` の `gh` 直接呼び出しは未撤去**
   PR-3ab では provider を**並列に追加するだけ**で dispatcher は切り替えない。`cli_main.py` の Phase 1-2 経路（`_forward_to_gh`）はそのまま動作している。PR-3c で `get_provider()` 経由に切り替える際に撤去する。

## 設計書スコープに対する実装範囲

### in-scope（PR-3ab 内で完了）

- [x] `providers/` package 新設（7 ファイル）
- [x] `IssueProvider` Protocol（Issue 系のみ。PR/MR 系は Phase 4）
- [x] `Issue` / `Comment` / `Label` / `IssueContext` dataclass
- [x] `ResolvedId` + `normalize_id`（全形式 + 全エラーパス）
- [x] `LABEL_TO_PREFIX` を Python 上の正本に移譲
- [x] `validate_slug` / `derive_slug_from_title` / path builders
- [x] `GitHubProvider` 全 CRUD + `IssueContext` 解決（subprocess mock テスト緑）
- [x] `LocalProvider` 全 CRUD + frontmatter + atomic write + flock + comment seq + 既存 dir max 統合
- [x] `resolve_issue_dir` glob + 重複検出
- [x] remote cache reader（`.kaji/cache/issues/N.json`）
- [x] machine_id 文法検証
- [x] Windows 暫定挙動（platform 検出 + 警告 1 回 + flock skip）

### 未実施（PR-3ab スコープ外、PR-3c 以降）

- [ ] `cli_main.py` dispatcher を `get_provider()` 経由に切替（PR-3c, Step 11）
- [ ] `prompt.py` を `IssueContext` 注入へ切替（PR-3c, Step 12）
- [ ] `kaji local init` 実装（PR-3d, Step 6）
- [ ] `[provider]` config schema の `KajiConfig` への追加（PR-3c で `get_provider` が必要とする）
- [ ] `feature-development-local.yaml` 追加（PR-3d, Step 16）
- [ ] dev repo `.kaji/config.toml` への `[provider]` 追記（PR-3d, Step 14）
- [ ] `.gitignore` に `.kaji/config.local.toml` を追加（PR-3d）
- [ ] Skill markdown の 5 変数移行（PR-3d, Step 15）
- [ ] fail-fast 化（PR-3e, Step 18）
- [ ] Large-local テスト（PR-3e, Step 19）
- [ ] CHANGELOG / `docs/cli-guides/local-mode.md` ドラフト（PR-3e, Step 20）
- [ ] flock 並列実行の Medium テスト（multiprocessing による counter 一意性検証）— PR-3c 以降で `kaji local init` と並行追加する想定

## 検証

| チェック | 結果 |
|----------|------|
| `make lint` | 緑 |
| `make format` | 差分なし |
| `make typecheck`（mypy strict） | 緑（21 source files、providers/ 含む 7 ファイル） |
| `make test` | 770 passed, 1 skipped（既存テスト 707 + 新規 60 + 既存変動 3） |
| 既存テストの deprecate | ゼロ（Phase 1-2 のテストは無変更で全て通過） |

## 次のアクション

PR-3c（dispatcher 切替 + `IssueContext` 注入）が次の作業単位。前提となる作業:

1. **`KajiConfig` への `[provider]` schema 追加** — `provider.type` / `provider.github.repo` / `provider.local.machine_id` / `provider.local.default_branch` を `tomllib` で読む。fallback あり（`provider.type` 未設定 → WARN + `github`）。
2. **`get_provider(config)` を `providers/__init__.py` に追加** — 本 PR では未実装（dispatcher 切替が無いため不要だった）。PR-3c の冒頭で追加する。
3. **`prompt.py` の `build_prompt` を `IssueContext` 受領に変更** — provider を runner で 1 度 resolve し、Skill 起動ごとに同じ `IssueContext` を流す（phase3-design.md § L314-322 の cache 戦略）。

## 参考

- 設計書: `draft/design/local-mode/phase3-design.md`
- 親設計: `draft/design/local-mode/design.md`
- 前 Phase 報告: `draft/design/local-mode/phase2b-implementation-report.md`
