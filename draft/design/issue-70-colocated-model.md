# [設計] 同居型利用モデルと設定ファイル設計

Issue: #70

## 概要

kaji_harness を別プロジェクトで利用するための同居型利用モデルを定義し、設定ファイル `.kaji/config.toml` の仕様、パス解決規則、artifacts 配置を設計する。

## 背景・目的

kaji_harness は現在、自身のリポジトリ内での実行を暗黙の前提としている。別プロジェクト（例: kamo2）で利用するためには以下の技術的負債を解消する必要がある。

**現状の問題**:

1. `state.py:15` の `STATE_DIR = Path("test-artifacts")` がモジュールレベル定数としてハードコードされている
2. `runner.py:64-65` が `Path(f"test-artifacts/{self.issue_number}/runs/...")` を直接構築しており、`STATE_DIR` と暗黙結合している
3. Skills は `workdir` 基準で解決されるが、State / Logs はプロセス CWD 基準で解決される不整合がある
4. 設定ファイルの仕組みが存在しない

## インターフェース

### 入力

#### `.kaji/config.toml`

対象プロジェクトの repo root に配置する唯一の設定ファイル。

```toml
# .kaji/config.toml — 最小構成（必須キーなし）

[paths]
artifacts_dir = ".kaji-artifacts"   # デフォルト値。repo root 相対
```

| セクション | キー | 型 | 必須 | デフォルト | 説明 |
|-----------|------|-----|------|-----------|------|
| `[paths]` | `artifacts_dir` | string | No | `".kaji-artifacts"` | artifacts 出力先。repo root 相対パス |

**設計判断**: 必須キーを設けない。`.kaji/config.toml` が存在すること自体が「kaji プロジェクトである」というマーカーになる。空ファイルでも有効。

#### CLI 引数

```bash
# 現行 CLI（変更なし）
kaji run <workflow-path> <issue> [--workdir <dir>] [--from <step>] [--step <step>] [--quiet]
kaji validate <workflow-yaml>... [--project-root <dir>]
```

CLI 引数の構造は変更しない。変更するのは内部のパス解決ロジックのみ。

### 出力

- `{artifacts_dir}/{issue}/session-state.json` — セッション状態
- `{artifacts_dir}/{issue}/progress.md` — 人間可読な進捗
- `{artifacts_dir}/{issue}/runs/{timestamp}/run.log` — JSONL 実行ログ
- `{artifacts_dir}/{issue}/runs/{timestamp}/{step_id}/stdout.log` — CLI 生出力
- `{artifacts_dir}/{issue}/runs/{timestamp}/{step_id}/console.log` — adapter 整形済み出力
- `{artifacts_dir}/{issue}/runs/{timestamp}/{step_id}/stderr.log` — エラー出力

### 使用例

```python
# 1. Config のロード（内部 API）
from kaji_harness.config import KajiConfig

config = KajiConfig.discover()  # CWD から .kaji/config.toml を探索
print(config.repo_root)         # /home/user/kamo2
print(config.artifacts_dir)     # /home/user/kamo2/.kaji-artifacts

# 2. WorkflowRunner への注入
runner = WorkflowRunner(
    workflow=workflow,
    issue_number=42,
    project_root=config.repo_root,       # スキル解決 + agent cwd
    artifacts_dir=config.artifacts_dir,   # state + logs
)
```

```bash
# CLI 実行例

# 1. 初回セットアップ（対象プロジェクトで）
cd /path/to/kamo2
mkdir -p .kaji/workflows
touch .kaji/config.toml
echo ".kaji-artifacts/" >> .gitignore

# 2. repo root から実行
cd /path/to/kamo2
kaji run .kaji/workflows/feature-development.yaml 42

# 3. サブディレクトリから実行
#    - workflow path は CWD 相対で解決
#    - config 探索は CWD から walk-up して repo root を自動検出
#    - agent CLI は検出された repo root で実行される
cd /path/to/kamo2/src/deep/nested
kaji run ../../../.kaji/workflows/feature-development.yaml 42

# 4. --workdir 明示（config 探索の起点を上書き）
#    - workflow path は引き続き CWD 相対
#    - config 探索は --workdir を起点にする
kaji run /path/to/kamo2/.kaji/workflows/feature-development.yaml 42 --workdir /path/to/kamo2

# 5. config が見つからない場合
cd /tmp
kaji run workflow.yaml 42
# → stderr: "Error: .kaji/config.toml not found. Searched from /tmp to /."
# → exit 2
```

## 制約・前提条件

- Python 3.11+ が前提（`tomllib` が標準ライブラリに含まれる）
- 設定ソースは `.kaji/config.toml` の一箇所のみ。CLI flag / 環境変数 / `pyproject.toml` からの設定読み込みは対象外
- 分離型（orchestrator repo と対象 PJ repo を分ける運用）は対象外
- Skills のディレクトリパスは各エージェント CLI の慣習に固定される（`.claude/skills/`, `.agents/skills/`）ため、config からの変更は不可

## 方針

### 1. `project_root` と `agent_workdir` の責務分離

現行実装では `workdir` が以下の2つの役割を兼ねている:

| 現行の `workdir` の用途 | 参照箇所 |
|------------------------|---------|
| スキル存在確認の基底パス | `runner.py:55` → `validate_skill_exists(step.skill, step.agent, self.workdir)` |
| エージェント CLI の `cwd` | `cli.py:56` → `subprocess.Popen(cwd=workdir)` |
| Codex の `-C` 引数 | `cli.py:187` → `-C str(workdir)` |
| verdict formatter の `workdir` | `runner.py:149` → `create_verdict_formatter(workdir=self.workdir)` |

同居型モデルでは、これらの役割を **`project_root`** として統一し、config から決定する:

```
project_root（config 由来）
  ├─ スキル解決の基底パス    → project_root / .claude/skills/
  ├─ エージェント CLI の cwd  → subprocess.Popen(cwd=project_root)
  ├─ artifacts 基底パス      → project_root / artifacts_dir
  └─ verdict formatter       → create_verdict_formatter(workdir=project_root)
```

**`--workdir` の役割変更**:

| | 変更前 | 変更後 |
|--|--------|--------|
| 意味 | エージェント CLI の cwd + スキル解決の基底 | config 探索の起点 |
| デフォルト | CWD | CWD |
| 実効果 | そのまま `subprocess.Popen(cwd=)` に渡る | `discover(start_dir=)` の引数になる |

config 発見後は `project_root = config.repo_root` が全パス解決の唯一の基準になる。`--workdir` の値自体は使われない。

**判断理由**: エージェント CLI が `.claude/skills/` や `CLAUDE.md` を正しくロードするには、`cwd` が repo root でなければならない。`--workdir` をそのまま agent cwd に渡す現行の設計は、サブディレクトリ実行で skill 解決が破綻する。config から repo root を確定させ、全パス解決をそこに集約する方が安全。

### 2. Config 発見アルゴリズム

```
discover(start_dir=None):
  1. start_dir が指定されていれば、それを起点とする
  2. 指定されていなければ CWD を起点とする
  3. 起点から親ディレクトリを順に辿り、.kaji/config.toml を探す
  4. ファイルシステムルート（/）に到達しても見つからなければ、
     stderr に探索範囲を含むエラーメッセージを出力し exit 2

CLI との統合:
  - `kaji run`:
    1. --workdir が指定されていれば --workdir を起点、なければ CWD を起点
    2. discover() で .kaji/config.toml を探索
    3. config.repo_root を project_root とし、スキル解決・agent cwd・artifacts に使用
  - `kaji validate`:
    1. --project-root が指定されていればそれを repo root として使用
    2. なければ YAML 親ディレクトリから .kaji/config.toml を探索
    3. config が見つからなければ pyproject.toml を探索（後方互換）
    4. いずれも見つからなければ YAML 親ディレクトリを root とする（現行動作）
```

**`kaji validate` が config を必須にしない理由**: kaji 自身のリポジトリでも `kaji validate` を使用するため。ただし、対象 PJ で `.kaji/config.toml` が存在する場合はそれを優先することで、非 Python リポジトリでの skill 解決破綻（YAML 親 = `.kaji/workflows/` → `.kaji/workflows/.claude/skills/...` を探しに行く問題）を防ぐ。

### 3. Repo root の定義

- **repo root = `.kaji/config.toml` を含むディレクトリ**
- Artifacts, skills はすべて repo root 相対で解決する
- Workflow パスは CWD 相対（後述の §5 参照）
- エージェント CLI の `cwd` は `project_root`（repo root）に固定される

### 4. Artifacts 統一

現行の `test-artifacts/` を config ベースの `artifacts_dir` に置き換える。

```
変更前:
  state.py:   STATE_DIR = Path("test-artifacts")           # CWD 相対
  runner.py:  Path(f"test-artifacts/{issue}/runs/...")      # CWD 相対

変更後:
  config:     artifacts_dir = repo_root / config.paths.artifacts_dir
  state.py:   SessionState.load_or_create(issue, artifacts_dir)
  runner.py:  WorkflowRunner(artifacts_dir=config.artifacts_dir)
```

**影響範囲**:
- `SessionState.__init__` に `artifacts_dir: Path` パラメータを追加
- `STATE_DIR` モジュールレベル定数を削除
- `WorkflowRunner` に `artifacts_dir: Path` パラメータを追加。現行の `workdir` パラメータは廃止し、`project_root` に名称変更
- `runner.py:64-65` のハードコードされたパスを `artifacts_dir` 経由に変更

### 5. Workflow パスの解決基準

`<workflow-path>` は **CWD 相対**で解決する。repo root 相対ではない。

```bash
# CWD = repo root の場合（最も一般的）
cd /path/to/kamo2
kaji run .kaji/workflows/feature-development.yaml 42
#        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#        CWD からの相対パス → /path/to/kamo2/.kaji/workflows/feature-development.yaml

# CWD = サブディレクトリの場合
cd /path/to/kamo2/src/deep
kaji run ../../.kaji/workflows/feature-development.yaml 42
#        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#        CWD からの相対パス → /path/to/kamo2/.kaji/workflows/feature-development.yaml

# 絶対パスも可
kaji run /path/to/kamo2/.kaji/workflows/feature-development.yaml 42
```

**判断理由**: シェルの標準的なパス解決に従う。現行実装 (`cli_main.py:161` の `args.workflow.exists()`) も CWD 相対であり、動作変更なし。`<workflow-path>` だけ repo root 相対にすると、CLI 引数のセマンティクスが混在して直感に反する。

### 6. `test-artifacts/` からの移行契約

**Clean break** を採用する。

- `.kaji/config.toml` が存在する環境では `artifacts_dir`（デフォルト: `.kaji-artifacts/`）のみを参照する
- 旧 `test-artifacts/` への fallback 参照は行わない
- 既存の `session-state.json` は移行しない（`--from` による resume は新しい artifacts_dir で最初からやり直し）
- kaji 自身のリポジトリでは `.kaji/config.toml` を導入し、`test-artifacts/` を段階的に廃止する

**判断理由**: `test-artifacts/` は kaji 自身の開発用ディレクトリ名であり、外部 PJ には存在しない。Fallback を入れると「config あり + 旧ディレクトリあり」の組み合わせテストが必要になり、複雑さに見合う利益がない。resume が壊れるケースは `--from` なしで最初から実行すれば回復可能。

### 7. kaji 自体の導入方法

kaji_harness は対象 PJ の Python 環境に `pip install` する。

```bash
# HTTPS（CI / GitHub Actions 向け — トークン認証）
pip install "kaji @ git+https://github.com/apokamo/kaji.git@v0.2.0"

# SSH（ローカル開発向け）
pip install "kaji @ git+ssh://git@github.com/apokamo/kaji.git@v0.2.0"
```

| 項目 | 方針 |
|------|------|
| プロトコル | HTTPS と SSH の両方をサポート。ドキュメントに両方記載 |
| バージョン固定 | **git ref 指定を必須**とする。tag（`@v0.2.0`）推奨、commit hash（`@79ceab8`）も可。ref なしの `@main` は非推奨。現時点では tag 未作成のため、初回導入時は commit hash を使用し、リリース運用確立後に tag に移行する |
| PyPI | 現時点では未公開。安定後に移行可能（対象 PJ 側の変更は install コマンドのみ） |

### 8. 対象 PJ の標準ディレクトリ構成

```
target-project/
├── .kaji/                          # kaji 設定（git 管理）
│   ├── config.toml                 # 設定ファイル（空でも可）
│   └── workflows/                  # ワークフロー定義
│       ├── feature-development.yaml
│       └── bugfix.yaml
├── .claude/skills/                 # Claude Code 用スキル（パス固定）
│   ├── issue-design/
│   │   └── SKILL.md
│   └── issue-implement/
│       └── SKILL.md
├── .agents/skills/                 # Codex / Gemini 用スキル（パス固定）
│   └── ...
├── .kaji-artifacts/                # 実行 artifacts（.gitignore 対象）
│   └── <issue-number>/
│       ├── session-state.json
│       ├── progress.md
│       └── runs/
│           └── <timestamp>/
│               ├── run.log
│               ├── stdout.log
│               ├── console.log
│               └── stderr.log
├── .gitignore                      # .kaji-artifacts/ を含む
└── (対象PJのソースコード)
```

### 9. 実装方針（疑似コード）

#### config.py（新規）

```python
import tomllib
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class PathsConfig:
    artifacts_dir: str = ".kaji-artifacts"

@dataclass(frozen=True)
class KajiConfig:
    repo_root: Path
    paths: PathsConfig

    @property
    def artifacts_dir(self) -> Path:
        return self.repo_root / self.paths.artifacts_dir

    @classmethod
    def discover(cls, start_dir: Path | None = None) -> "KajiConfig":
        """CWD or start_dir から .kaji/config.toml を探索。"""
        current = (start_dir or Path.cwd()).resolve()
        while True:
            candidate = current / ".kaji" / "config.toml"
            if candidate.is_file():
                return cls._load(candidate)
            parent = current.parent
            if parent == current:
                raise ConfigNotFoundError(start_dir or Path.cwd())
            current = parent

    @classmethod
    def _load(cls, path: Path) -> "KajiConfig":
        """TOML をパースし KajiConfig を構築。"""
        with open(path, "rb") as f:
            data = tomllib.load(f)
        paths_data = data.get("paths", {})
        paths = PathsConfig(**{
            k: v for k, v in paths_data.items()
            if k in PathsConfig.__dataclass_fields__
        })
        return cls(repo_root=path.parent.parent, paths=paths)
```

#### cli_main.py の変更

```python
def cmd_run(args):
    # config 探索: --workdir が指定されていればそこを起点、なければ CWD
    start_dir = args.workdir.resolve() if args.workdir != Path.cwd() else None
    try:
        config = KajiConfig.discover(start_dir=start_dir)
    except ConfigNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_CONFIG_NOT_FOUND  # exit 2

    project_root = config.repo_root  # 全パス解決の基準

    runner = WorkflowRunner(
        workflow=workflow,
        issue_number=args.issue,
        project_root=project_root,     # スキル解決 + agent cwd
        artifacts_dir=config.artifacts_dir,  # state + logs
        ...
    )
```

```python
def _resolve_project_root_for_validate(explicit_root, yaml_path):
    """validate 用の root 解決。run とは異なり config 必須にしない。"""
    # 1. --project-root 明示
    if explicit_root is not None:
        return explicit_root.resolve()
    # 2. .kaji/config.toml を探索
    try:
        config = KajiConfig.discover(start_dir=yaml_path.resolve().parent)
        return config.repo_root
    except ConfigNotFoundError:
        pass
    # 3. pyproject.toml を探索（後方互換）
    current = yaml_path.resolve().parent
    while True:
        if (current / "pyproject.toml").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    # 4. YAML 親ディレクトリ
    return yaml_path.resolve().parent
```

#### runner.py の変更

```python
@dataclass
class WorkflowRunner:
    workflow: Workflow
    issue_number: int
    project_root: Path       # 旧 workdir を名称変更。スキル解決 + agent cwd
    artifacts_dir: Path      # 新パラメータ。state + logs の基底

    def run(self):
        # スキル検証: project_root を基準
        for step in self.workflow.steps:
            validate_skill_exists(step.skill, step.agent, self.project_root)

        # state: artifacts_dir を基準
        state = SessionState.load_or_create(self.issue_number, self.artifacts_dir)

        # run log: artifacts_dir を基準
        run_dir = self.artifacts_dir / str(self.issue_number) / "runs" / timestamp

        # CLI 実行: project_root を cwd に
        result = execute_cli(step=..., workdir=self.project_root, ...)
```

#### state.py の変更

```python
# STATE_DIR 定数を削除

class SessionState:
    def __init__(self, issue_number: int, artifacts_dir: Path):
        self.issue_number = issue_number
        self._artifacts_dir = artifacts_dir

    @classmethod
    def load_or_create(cls, issue: int, artifacts_dir: Path) -> "SessionState":
        path = artifacts_dir / str(issue) / STATE_FILE
        ...
```

### 10. kamo2 導入セットアップ

対象プロジェクト kamo2 への導入手順:

```bash
# 1. kaji のインストール
cd /path/to/kamo2
pip install "kaji @ git+https://github.com/apokamo/kaji.git@v0.2.0"

# 2. 設定ディレクトリの作成
mkdir -p .kaji/workflows

# 3. 設定ファイルの作成（空でも可）
touch .kaji/config.toml

# 4. .gitignore に artifacts を追加
echo ".kaji-artifacts/" >> .gitignore

# 5. ワークフローとスキルの配置
cp /path/to/templates/feature-development.yaml .kaji/workflows/
mkdir -p .claude/skills .agents/skills
# スキルファイルを配置...

# 6. 動作確認
kaji validate .kaji/workflows/feature-development.yaml
kaji run .kaji/workflows/feature-development.yaml 1 --step design
```

**前提条件**:
- Python 3.11+ がインストール済み
- エージェント CLI（claude, codex, gemini のいずれか）がインストール済み
- GitHub CLI (`gh`) がインストール済み（Issue 操作を行うスキルを使う場合）
- ローカル実行のみ。CI 実行は将来検討

## テスト戦略

> **CRITICAL**: S/M/L すべてのサイズのテスト方針を定義すること。
> AI はテストを省略する傾向があるため、設計段階で明確に定義し、省略の余地を排除する。
> 詳細は [テスト規約](../../../docs/dev/testing-convention.md) 参照。

### Small テスト

- **TOML パース**: 有効な config / 空ファイル / 不正 TOML / 未知キー（無視される）のパースと validation
- **PathsConfig デフォルト値**: `artifacts_dir` 省略時にデフォルト値が適用されること
- **repo root 算出**: config.toml のパスから正しく親の親を repo root として返すこと
- **artifacts_dir 解決**: repo root + `paths.artifacts_dir` の結合が正しいこと
- **ConfigNotFoundError**: 探索失敗時のエラーメッセージに探索開始パスが含まれること

### Medium テスト

- **Config discovery（ファイルシステム結合）**: tmpdir に `.kaji/config.toml` を配置し、CWD / サブディレクトリ / 存在しないケースでの探索動作を検証
- **SessionState with artifacts_dir**: `artifacts_dir` パラメータ経由で state ファイルの読み書きが正しいパスに行われること
- **RunLogger with configurable dir**: `artifacts_dir` 配下に run ログが出力されること
- **WorkflowRunner with config**: config から注入された `artifacts_dir` が state と logger に伝播すること
- **CLI integration**: `cmd_run` が config discovery → runner 構築 → 実行の流れで `artifacts_dir` を正しく渡すこと

### Large テスト

- **E2E: config-based run**: `.kaji/config.toml` を持つテストプロジェクトで `kaji run` を実行し、`.kaji-artifacts/` に state と logs が出力されること
- **E2E: config not found**: `.kaji/config.toml` が存在しない環境で `kaji run` を実行し、exit 2 + 適切なエラーメッセージが出ること
- **E2E: validate without config**: `kaji validate` が config なしでも動作すること（後方互換）

### スキップするサイズ

なし。すべてのサイズを実装する。

## 影響ドキュメント

この変更により更新が必要になる可能性のあるドキュメントを列挙する。

| ドキュメント | 影響の有無 | 理由 |
|-------------|-----------|------|
| docs/adr/ | なし | 利用モデルの ADR は設計が安定した段階で別途検討 |
| docs/ARCHITECTURE.md | あり | セッション管理のパス記述 (`test-artifacts/`) を更新。config 層の追加 |
| docs/dev/development_workflow.md | なし | ワークフロー手順自体は変わらない |
| docs/dev/workflow-authoring.md | あり | ワークフロー YAML の配置先として `.kaji/workflows/` を追記 |
| docs/cli-guides/ | なし | CLI 引数構造は変わらない |
| CLAUDE.md | あり | Essential Commands の `kaji run` 例に config 前提を追記 |
| README.md | あり | セットアップ手順、利用例、ディレクトリ構成を更新 |

## 参照情報（Primary Sources）

| 情報源 | URL/パス | 根拠（引用/要約） |
|--------|----------|-------------------|
| Python tomllib | https://docs.python.org/3/library/tomllib.html | Python 3.11+ 標準ライブラリ。外部依存不要で TOML をパースできる |
| kaji_harness/state.py | `kaji_harness/state.py:15` | `STATE_DIR = Path("test-artifacts")` — 変更対象のハードコード定数 |
| kaji_harness/runner.py | `kaji_harness/runner.py:64-65` | `Path(f"test-artifacts/...")` — STATE_DIR と暗黙結合している run log パス |
| kaji_harness/cli_main.py | `kaji_harness/cli_main.py:71-89` | `_resolve_project_root()` — 現行の project root 解決ロジック。config discovery に置き換え対象 |
| kaji_harness/skill.py | `kaji_harness/skill.py:7-11` | `SKILL_DIRS` — Skills ディレクトリは agent CLI の慣習に固定。config からの変更は不可の根拠 |
| TOML v1.0.0 仕様 | https://toml.io/en/v1.0.0 | TOML フォーマットの公式仕様。config.toml の書式根拠 |
| Issue #70 設計レビュー (1st) | Issue #70 コメント (2026-03-11) | config 発見アルゴリズム、workflow 解決契約、移行契約、テスト戦略の must-fix 指摘 |
| Issue #70 設計レビュー (2nd) | previous_verdict (cycle 1) | project_root と agent_workdir の責務分離、validate の root 解決、workflow-path 相対基準の 3 件 |
