# [設計] スキルディレクトリ解決のエージェント非依存化

Issue: #112

## 概要

`skill.py` の `SKILL_DIRS` ハードコードを廃止し、`.kaji/config.toml` で設定可能な単一カノニカルディレクトリによるスキル解決に移行する。

## 背景・目的

現在の `SKILL_DIRS` は agent 名とディレクトリのマッピングをハードコードしている:

```python
SKILL_DIRS = {
    "claude": ".claude/skills",
    "codex": ".agents/skills",
    "gemini": ".agents/skills",
}
```

この設計には以下の問題がある:

1. **暗黙の claude 依存**: `.agents/skills/` は `.claude/skills/` へのシンボリックリンクを前提としており、claude が存在しない構成（codex のみ、gemini のみ）で破綻する
2. **ハーネスが知る必要のない関心事の混入**: 各 CLI がどのディレクトリからスキルをロードするかはファイルシステム（シンボリックリンク）の責務であり、ハーネスの検証ロジックが agent → directory マッピングを持つ必要がない
3. **新エージェント追加時のコード変更**: 新しい agent が追加されるたびに `SKILL_DIRS` にエントリを追加する必要がある

## インターフェース

### 入力

#### config.toml の変更

```toml
[paths]
artifacts_dir = ".kaji-artifacts"
skill_dir = ".claude/skills"        # 追加。デフォルト値: ".claude/skills"
```

- `skill_dir`: スキルの実体が格納されるカノニカルディレクトリ（workdir からの相対パス）
- 省略時は `".claude/skills"` をデフォルトとする（後方互換）
- **相対パス限定**（`..` 禁止、絶対パス不可）。`artifacts_dir` とはルールが異なる
  - 理由: `validate_skill_exists` の `is_relative_to(workdir)` チェックにより、repo 外の絶対パスは必ず `SecurityError` になる。config で受理しても pre-flight で弾かれる矛盾を防ぐ
  - `artifacts_dir` は repo 外（`~/.kaji/artifacts`）に置く正当なユースケースがあるため絶対パスを許容するが、スキルは repo 内に存在する前提のため相対パスのみ

#### validate_skill_exists の変更

**現在**:
```python
def validate_skill_exists(skill_name: str, agent: str, workdir: Path) -> None:
```

**変更後**:
```python
def validate_skill_exists(skill_name: str, workdir: Path, skill_dir: str) -> None:
```

- `agent` パラメータを削除
- `skill_dir` パラメータを追加（config から取得した値を渡す）

### 出力

- 変更なし（`None` を返すか、`SkillNotFound` / `SecurityError` を raise）

### 使用例

```python
# runner.py での呼び出し（変更後）
for step in self.workflow.steps:
    validate_skill_exists(step.skill, self.project_root, self.config.paths.skill_dir)
```

```toml
# claude + codex 構成（デフォルト）
[paths]
skill_dir = ".claude/skills"
# → .agents/skills/ は .claude/skills/ へのシンボリックリンク

# codex のみ構成
[paths]
skill_dir = ".agents/skills"
# → .claude/skills/ は不要。スキル実体を .agents/skills/ に配置
```

## 制約・前提条件

- **後方互換とデフォルト値の根拠**: `skill_dir` 省略時は `".claude/skills"` をデフォルトとする。Issue 本文の「設定なし=エラー。暗黙のデフォルトは持たない」は agent→directory マッピングの問題（未知の agent に対して暗黙でパスを推測しない）を指しており、本設計はそのマッピング自体を廃止することで解決する。`skill_dir` のデフォルト値は agent マッピングとは別次元の設定であり、`execution.default_timeout`（必須・デフォルトなし）とは異なり、既存プロジェクトの破壊的変更を避けるためデフォルト値を持たせる
- **config.toml 必須**: `KajiConfig.discover()` が既に必須であるため、新たな依存は追加しない
- **CLI ごとのスキルロードパスは変更しない**: ハーネスはカノニカルディレクトリで存在確認するのみ。各 CLI がどのパスからスキルをロードするかはファイルシステム（シンボリックリンク）で解決する
- **パストラバーサル防御を維持**: 現行の `..` チェックと `resolve()` + `is_relative_to()` チェックをそのまま残す

## 方針

### 1. config.py への skill_dir 追加

`PathsConfig` に `skill_dir: str` フィールドを追加。デフォルト値 `".claude/skills"`。
`_load()` でのバリデーションは `_validate_skill_dir` として新設（`..` 禁止、絶対パス不可、型チェック）。`artifacts_dir` は絶対パスを許容するため、同一バリデータは使わない。

### 2. skill.py の簡素化

`SKILL_DIRS` dict を削除し、`validate_skill_exists` のシグネチャを変更:

```python
def validate_skill_exists(skill_name: str, workdir: Path, skill_dir: str) -> None:
    if ".." in skill_name.split("/"):
        raise SecurityError(...)
    base = workdir / skill_dir / skill_name / "SKILL.md"
    resolved = base.resolve()
    if not resolved.is_relative_to(workdir.resolve()):
        raise SecurityError(...)
    if not resolved.exists():
        raise SkillNotFound(...)
```

- `agent` パラメータがなくなるため、`Unknown agent` エラーパスも削除される

### 3. runner.py の呼び出し変更

```python
# 変更前
validate_skill_exists(step.skill, step.agent, self.project_root)

# 変更後
validate_skill_exists(step.skill, self.project_root, self.config.paths.skill_dir)
```

### 4. ドキュメント整理

以下のドキュメントで `.agents/skills/` と `.claude/skills/` の二重管理前提の記述を整理:

- `docs/dev/skill-authoring.md`: 「ファイル配置」セクション — カノニカルディレクトリと設定方法の説明に改訂
- `docs/ARCHITECTURE.md`: Layer 3 の説明 — 単一カノニカルディレクトリ + symlink 構成に改訂
- `docs/adr/003-skill-harness-architecture.md`: Layer 3 の `.claude/skills/, .agents/skills/` 併記を、カノニカルディレクトリ（`paths.skill_dir` で設定）+ symlink 構成に改訂。Issue #112 完了条件で更新が明記されているため対象に含める

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。
> 詳細は [テスト規約](../../../docs/dev/testing-convention.md) 参照。

### Small テスト

- **skill_dir パラメータでのスキル解決**: `validate_skill_exists(skill_name, workdir, skill_dir)` が指定ディレクトリで正しく SKILL.md を検出すること
- **カスタム skill_dir**: `".agents/skills"` や `"custom/skills"` など非デフォルトのディレクトリでも動作すること
- **パストラバーサル防御の維持**: `..` を含む skill_name が `SecurityError` を raise すること（既存テストの移行）
- **SkillNotFound**: 存在しないスキルで `SkillNotFound` が raise されること（既存テストの移行）
- **config パース**: `PathsConfig` の `skill_dir` デフォルト値、明示的指定、`..` 禁止バリデーションの検証

### Medium テスト

- **シンボリックリンク経由の解決**: `skill_dir` がシンボリックリンク先を指す場合でも `resolve()` で正しく検出できること（`tmp_path` にシンボリックリンクを作成してテスト）
- **Runner 統合**: `WorkflowRunner` が `config.paths.skill_dir` を `validate_skill_exists` に渡し、ワークフロー内全ステップのスキルを事前検証できること（CLI 実行はモック）

### Large テスト

- **E2E スキルロード検証**: 実際の `.kaji/config.toml` + `.claude/skills/` + `.agents/skills/` シンボリックリンク構成で `kaji run <workflow> <issue> --step <step-id>` を実行し、CLI がスキルをネイティブにロードして実行開始するところまで検証する（pre-flight 検証だけでなく、CLI のスキルロードパス経由の実行を含む E2E）
- **カスタム skill_dir での E2E**: `config.toml` の `skill_dir` を非デフォルト値に変更した状態で `kaji run --step` が正常に動作すること

## 影響ドキュメント

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | あり | ADR 003 の Layer 3 テーブルを更新（`.claude/skills/, .agents/skills/` 併記 → `paths.skill_dir` で設定するカノニカルディレクトリ） |
| docs/ARCHITECTURE.md | あり | Layer 3 とパッケージ構成の skill.py 説明を更新 |
| docs/dev/ | あり | skill-authoring.md のファイル配置セクションを更新 |
| docs/cli-guides/ | なし | CLI 側のスキルロード機構は変更しない |
| CLAUDE.md | なし | 規約変更なし |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| 現行 skill.py | `kaji_harness/skill.py` | `SKILL_DIRS` dict がエージェント→ディレクトリのハードコードマッピングを持つ（L7-11）。本設計の直接的な変更対象 |
| 現行 config.py | `kaji_harness/config.py` | `PathsConfig` に `artifacts_dir` のパターンが存在し、`skill_dir` を同パターンで追加できる。`_validate_artifacts_dir` のバリデーションロジックを `skill_dir` にも適用する根拠 |
| runner.py の呼び出し | `kaji_harness/runner.py:58` | `validate_skill_exists(step.skill, step.agent, self.project_root)` — 現在の呼び出し箇所。`agent` → `config.paths.skill_dir` への変更が必要 |
| 現行 .agents/skills/ | `.agents/skills/` | 既にすべてのエントリが `../../.claude/skills/*` へのシンボリックリンクとして構成済み。カノニカルディレクトリ方式への移行は現行のファイルシステム構成と整合する |
| skill-authoring.md | `docs/dev/skill-authoring.md:11-23` | 「スキルの実体は `.claude/skills/` に置き、`.agents/skills/` はそれを参照する symlink として扱う」と明記済み。本設計はこの方針をコード側で正式に反映する |
| ARCHITECTURE.md | `docs/ARCHITECTURE.md:48-49` | Layer 3 が `.claude/skills/` と `.agents/skills/` を並列に記述しているが、本設計でカノニカル + symlink の関係に改訂する |
