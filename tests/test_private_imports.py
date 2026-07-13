"""private import の境界規約を機械検証する fitness test (Issue #285)。

ADR 009「モジュール境界と private import 規約」の強制装置。`__all__` は
``from package import *`` の対象を制御するだけで named import を禁止する
強制力を持たないため、境界の強制は本 module が担う。

分類器 (`classify_source`) は **ファイル I/O を持たない純関数** として実装し、
実ツリーの走査 (`collect_forbidden_signatures`) を呼び出し側に分離する。
これにより分類器の単体テストは合成ソース文字列だけで完結し Small、
実 ``kaji_harness/`` を読む fitness test は Medium になる
(`docs/dev/testing-convention.md` のリソース基準)。
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass
from typing import Literal

import pytest

ROOT_PACKAGE = "kaji_harness"

Classification = Literal["forbidden", "allowed", "public_reexport"]

# statement の正規化 signature。行番号は含めない（無関係な行の増減で churn するため）。
Signature = tuple[str, str, tuple[str, ...]]


@dataclass(frozen=True)
class PrivateImport:
    """private import statement 1 件の分類結果。

    Attributes:
        module: import 元 module の dotted name (`M`)。package の ``__init__.py``
            は ``kaji_harness.providers.__init__`` のように ``__init__`` を
            末尾成分として保持する（`pkg()` / 相対 import の level 解決を
            module / package で統一的に扱うため）。
        target: 解決後の import 先 module の dotted name (`T`)。
        private_names: import される名前のうち private なもの（`N` の部分集合）。
        lineno: statement の行番号（診断出力用。signature には含めない）。
        classification: 禁止 / 許容 / public re-export。
    """

    module: str
    target: str
    private_names: tuple[str, ...]
    lineno: int
    classification: Classification

    @property
    def signature(self) -> Signature:
        return (self.module, self.target, self.private_names)


def is_private(name: str) -> bool:
    """PEP 8 の ``_single_leading_underscore``（dunder は除外）。"""
    return name.startswith("_") and not name.startswith("__")


def _pkg(module: str, packages: frozenset[str]) -> str:
    """module が属する package (ADR 009 の ``pkg(X)``)。

    ``X`` が package 自身（``__init__`` を持つ dotted name）なら ``X`` そのもの、
    通常の module なら末尾成分を落としたもの。この区別が無いと
    ``from .providers import _internal`` のように sub-package の facade を
    import 先とする境界違反を、親 package 同士の比較で許容と誤判定する。

    ``kaji_harness.providers``          → ``kaji_harness.providers``（package 自身）
    ``kaji_harness.providers.__init__`` → ``kaji_harness.providers``（自 package）
    ``kaji_harness.providers.local``    → ``kaji_harness.providers``

    Args:
        module: 対象の dotted name。
        packages: 実在する package の dotted name 集合（``__init__`` は含めない）。
    """
    if module in packages:
        return module
    return module.rsplit(".", 1)[0]


def _ancestor(package: str, levels: int) -> str:
    for _ in range(levels):
        package = package.rsplit(".", 1)[0]
    return package


def _extract_dunder_all(tree: ast.Module) -> frozenset[str]:
    """module top-level の ``__all__`` に列挙された名前を取り出す。"""
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            continue
        if isinstance(node.value, ast.List | ast.Tuple):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.add(elt.value)
    return frozenset(names)


def classify_source(
    source_text: str, module_name: str, packages: frozenset[str]
) -> list[PrivateImport]:
    """ソース文字列中の private import を分類する（純関数・ディスクに触れない）。

    ``ast.Import``（``import a.b``）と ``ast.ImportFrom``（``from a import b``）は
    別ノードなので **両方**を走査する。``ast.Import`` に相対形式は無く
    （``import ..x`` は構文エラー）、束縛されるのは top-level package 名だけで
    private symbol は import されないため、private module 判定のみで禁止を決める。

    Args:
        source_text: 対象 module のソース。
        module_name: 対象 module の dotted name。package の ``__init__.py`` は
            ``__init__`` を末尾に含めて渡す。
        packages: 実在する package の dotted name 集合。``pkg(T)`` を ADR 009 の
            定義どおり解決するために必須（import 先が package 自身か通常 module か
            は AST だけでは判別できない）。実ツリーでは `discover_packages` が返す。

    Returns:
        private import と判定された statement の分類結果（出現順）。
    """
    tree = ast.parse(source_text)
    dunder_all = _extract_dunder_all(tree)
    is_package_init = module_name.rsplit(".", 1)[-1] == "__init__"
    self_pkg = _pkg(module_name, packages)

    results: list[PrivateImport] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "") == "__future__":
                continue
            if node.level:
                base = _ancestor(self_pkg, node.level - 1)
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            all_names = tuple(a.name for a in node.names)
            candidates = [(target, all_names)]
        elif isinstance(node, ast.Import):
            candidates = [(a.name, ()) for a in node.names]
        else:
            continue

        for target, all_names in candidates:
            if target.split(".")[0] != ROOT_PACKAGE:
                continue  # stdlib / 第三者 package は対象外
            private_names = tuple(sorted(n for n in all_names if is_private(n)))
            private_module = any(is_private(c) for c in target.split(".") if c)
            if not (private_module or private_names):
                continue

            if self_pkg != _pkg(target, packages):
                classification: Classification = "forbidden"
            elif is_package_init and any(n in dunder_all for n in all_names):
                classification = "public_reexport"
            else:
                classification = "allowed"

            results.append(
                PrivateImport(
                    module=module_name,
                    target=target,
                    private_names=private_names,
                    lineno=node.lineno,
                    classification=classification,
                )
            )
    return results


def module_name_for(path: pathlib.Path, package_root: pathlib.Path) -> str:
    """ソースファイルの path を dotted module name へ変換する。

    ``__init__.py`` は ``__init__`` を末尾成分として残す（`PrivateImport.module`）。
    """
    rel = path.relative_to(package_root.parent).with_suffix("")
    return ".".join(rel.parts)


def discover_packages(package_root: pathlib.Path) -> frozenset[str]:
    """実ツリーから package の dotted name 集合を集める（``__init__.py`` を持つ dir）。"""
    names = {ROOT_PACKAGE}
    for init in package_root.rglob("__init__.py"):
        rel = init.parent.relative_to(package_root.parent)
        names.add(".".join(rel.parts))
    return frozenset(names)


def collect_private_imports(package_root: pathlib.Path) -> list[PrivateImport]:
    """実ツリーを走査して private import を集める（ここだけがファイル I/O）。"""
    packages = discover_packages(package_root)
    found: list[PrivateImport] = []
    for path in sorted(package_root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        found.extend(classify_source(source, module_name_for(path, package_root), packages))
    return found


# ---------------------------------------------------------------------------
# 時限許容 (transitional) allowlist
# ---------------------------------------------------------------------------
# #284 で cli_main の互換 shim を撤去したため、#285 が登録した時限許容 7 entry も撤去する。
# 検証器は引き続き {禁止 signature} == TRANSITIONAL_ALLOWLIST の厳密一致を要求する。
TRANSITIONAL_ALLOWLIST: frozenset[Signature] = frozenset()

# allowlist 機構の Small test は、production の時限許容と独立した合成データで
# filter / 未登録違反 / stale entry の検出を固定する。
SYNTHETIC_TRANSITIONAL_ALLOWLIST: frozenset[Signature] = frozenset(
    {
        (
            "kaji_harness.cli_main",
            "kaji_harness.commands.config",
            (
                "_emit_provider_overlay_divergence_warning",
                "_load_config_for_dispatch",
            ),
        ),
        (
            "kaji_harness.cli_main",
            "kaji_harness.commands.issue",
            (
                "_LOCAL_ISSUE_SUBS",
                "_github_issue_comment_with_verdict",
                "_handle_issue",
                "_handle_issue_context",
                "_handle_issue_local",
                "_handle_issue_prepend_note",
                "_has_verdict_flags",
                "_local_issue_close",
                "_local_issue_comment",
                "_local_issue_create",
                "_local_issue_edit",
                "_local_issue_list",
                "_local_issue_view",
                "_resolve_local_id",
            ),
        ),
        (
            "kaji_harness.cli_main",
            "kaji_harness.commands.output",
            (
                "_apply_jq",
                "_compose_json_and_jq",
                "_emit_json",
                "_format_jq_results",
                "_issue_to_json_dict",
                "_read_body_arg",
            ),
        ),
        (
            "kaji_harness.cli_main",
            "kaji_harness.commands.parser",
            (
                "_add_recovery_arguments",
                "_get_version",
                "_register_config",
                "_register_issue",
                "_register_pr",
                "_register_recover",
                "_register_run",
                "_register_sync",
                "_register_validate",
            ),
        ),
        (
            "kaji_harness.cli_main",
            "kaji_harness.commands.pr",
            (
                "_FORGE_METHOD_FLAGS",
                "_GH_MISSING_GUIDANCE",
                "_PR_BARE_PROVIDER_ERROR",
                "_PR_BUILTIN_SUBCOMMANDS",
                "_detect_repo",
                "_dispatch_pr_builtin",
                "_forward_pr_api_list",
                "_forward_pr_reply_to_comment",
                "_forward_pr_review_comments",
                "_forward_pr_reviews",
                "_forward_to_gh",
                "_gh_capture_value",
                "_gh_post_issue_comment_silent",
                "_github_pr_review",
                "_handle_pr",
                "_has_approve_flag",
                "_has_request_changes_flag",
                "_is_ascii_decimal",
                "_run_pr_review_poll",
                "_user_specified_repo",
            ),
        ),
        (
            "kaji_harness.cli_main",
            "kaji_harness.commands.run",
            (
                "_apply_execution_overrides",
                "_run_failure_triage",
                "_validate_workflow_provider_match",
            ),
        ),
        (
            "kaji_harness.cli_main",
            "kaji_harness.commands.validate",
            (
                "_print_error",
                "_print_success",
                "_resolve_project_root_for_validate",
            ),
        ),
    }
)

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1] / ROOT_PACKAGE


def _fmt(signatures: set[Signature]) -> str:
    return "\n".join(f"  {m} -> {t} {names}" for m, t, names in sorted(signatures))


# ---------------------------------------------------------------------------
# Small: 分類器の単体テスト（合成ソースのみ・ディスクに触れない）
# ---------------------------------------------------------------------------

# 合成ソース用の package 集合（実ツリーの package 構成に対応）。
PACKAGES = frozenset(
    {
        "kaji_harness",
        "kaji_harness.commands",
        "kaji_harness.providers",
    }
)


@pytest.mark.small
def test_relative_import_level_resolution() -> None:
    """``from ..state import _x`` (level=2) が top-level module に解決される。"""
    result = classify_source(
        "from ..state import _format_issue_ref\n",
        "kaji_harness.commands.issue",
        PACKAGES,
    )
    assert len(result) == 1
    assert result[0].target == "kaji_harness.state"
    assert result[0].private_names == ("_format_issue_ref",)
    assert result[0].classification == "forbidden"


@pytest.mark.small
def test_package_init_level_1_resolves_to_own_package() -> None:
    """package ``__init__.py`` 起点の level=1 が自 package に解決される。

    これが ``providers/__init__.py`` → ``providers._worktree`` を許容にする根拠。
    """
    result = classify_source(
        '__all__ = ["resolve_main_worktree"]\nfrom ._worktree import resolve_main_worktree\n',
        "kaji_harness.providers.__init__",
        PACKAGES,
    )
    assert len(result) == 1
    assert result[0].target == "kaji_harness.providers._worktree"
    assert result[0].classification == "public_reexport"


@pytest.mark.small
def test_package_init_private_import_not_in_dunder_all_is_allowed() -> None:
    """``__init__`` が private module を読んでも ``__all__`` に無ければ単なる許容。"""
    result = classify_source(
        '__all__ = ["other"]\nfrom ._worktree import resolve_main_worktree\n',
        "kaji_harness.providers.__init__",
        PACKAGES,
    )
    assert [r.classification for r in result] == ["allowed"]


@pytest.mark.small
def test_same_package_private_import_is_allowed() -> None:
    result = classify_source(
        "from ._mappings import LABEL_TO_PREFIX\n",
        "kaji_harness.providers.context",
        PACKAGES,
    )
    assert [r.classification for r in result] == ["allowed"]


@pytest.mark.small
def test_cross_package_private_symbol_is_forbidden() -> None:
    result = classify_source(
        "from .providers.local import _atomic_write\n",
        "kaji_harness.sync",
        PACKAGES,
    )
    assert [r.classification for r in result] == ["forbidden"]
    assert result[0].target == "kaji_harness.providers.local"


@pytest.mark.small
def test_sub_package_facade_private_symbol_is_forbidden_relative() -> None:
    """sub-package の facade (`__init__`) から private symbol を取るのも境界違反。

    ``pkg(T)`` は「T が package なら T 自身」なので、``kaji_harness.sync``
    (所属 ``kaji_harness``) から見た ``kaji_harness.providers`` は別 package。
    import 先を package 自身と判別しないと、末尾成分を落とした親 package 同士の
    比較になって許容と誤判定する。
    """
    result = classify_source(
        "from .providers import _internal\n",
        "kaji_harness.sync",
        PACKAGES,
    )
    assert len(result) == 1
    assert result[0].target == "kaji_harness.providers"
    assert result[0].private_names == ("_internal",)
    assert result[0].classification == "forbidden"


@pytest.mark.small
def test_sub_package_facade_private_symbol_is_forbidden_absolute() -> None:
    """絶対 import 形式でも sub-package facade 経由の private symbol は禁止。"""
    result = classify_source(
        "from kaji_harness.providers import _internal\n",
        "kaji_harness.sync",
        PACKAGES,
    )
    assert [r.classification for r in result] == ["forbidden"]
    assert result[0].target == "kaji_harness.providers"


@pytest.mark.small
def test_own_package_facade_private_symbol_is_allowed() -> None:
    """同一 package の facade (``from . import _x``) は package 内なので許容。"""
    result = classify_source(
        "from . import _internal\n",
        "kaji_harness.providers.local",
        PACKAGES,
    )
    assert len(result) == 1
    assert result[0].target == "kaji_harness.providers"
    assert result[0].classification == "allowed"


@pytest.mark.small
def test_cross_package_private_module_is_forbidden() -> None:
    result = classify_source(
        "from .providers._mappings import DEFAULT_BRANCH_PREFIX, LABEL_TO_PREFIX\n",
        "kaji_harness.worktree_discovery",
        PACKAGES,
    )
    assert [r.classification for r in result] == ["forbidden"]
    # private module 経由なので import される名前自体は public
    assert result[0].private_names == ()


@pytest.mark.small
def test_plain_import_of_private_module_is_forbidden() -> None:
    """``ast.Import`` 形式（絶対 import）の private module を検出する。

    ``ast.ImportFrom`` だけを見る実装ではここが落ちる。
    """
    result = classify_source(
        "import kaji_harness.providers._mappings\n",
        "kaji_harness.worktree_discovery",
        PACKAGES,
    )
    assert len(result) == 1
    assert result[0].target == "kaji_harness.providers._mappings"
    assert result[0].private_names == ()
    assert result[0].classification == "forbidden"


@pytest.mark.small
def test_plain_import_of_public_module_is_out_of_scope() -> None:
    result = classify_source(
        "import kaji_harness.providers\nimport kaji_harness.sync\n",
        "kaji_harness.worktree_discovery",
        PACKAGES,
    )
    assert result == []


@pytest.mark.small
def test_dunder_import_is_not_private() -> None:
    result = classify_source(
        "from __future__ import annotations\n",
        "kaji_harness.sync",
        PACKAGES,
    )
    assert result == []


@pytest.mark.small
def test_public_symbol_and_third_party_imports_are_out_of_scope() -> None:
    result = classify_source(
        "import os\nimport yaml\nfrom pathlib import Path\n"
        "from .providers import get_provider\n"
        "from kaji_harness.errors import SyncError\n",
        "kaji_harness.sync",
        PACKAGES,
    )
    assert result == []


@pytest.mark.small
def test_signature_is_line_and_order_independent() -> None:
    """signature は行番号に依存せず、private 名の順序が違っても同一になる。"""
    a = classify_source(
        "from .config import _load_config_for_dispatch, _emit_provider_overlay_divergence_warning\n",
        "kaji_harness.cli_main",
        PACKAGES,
    )
    b = classify_source(
        "import os\n\n\n"
        "from .config import _emit_provider_overlay_divergence_warning, _load_config_for_dispatch\n",
        "kaji_harness.cli_main",
        PACKAGES,
    )
    assert a[0].lineno != b[0].lineno
    assert a[0].signature == b[0].signature


@pytest.mark.small
def test_allowlist_filters_registered_forbidden_signature() -> None:
    """allowlist に登録済みの禁止 signature は残差から除かれる。"""
    source = (
        "from .commands.run import _apply_execution_overrides, _run_failure_triage, "
        "_validate_workflow_provider_match, cmd_run\n"
    )
    found = classify_source(source, "kaji_harness.cli_main", PACKAGES)
    forbidden = {r.signature for r in found if r.classification == "forbidden"}
    assert forbidden <= SYNTHETIC_TRANSITIONAL_ALLOWLIST
    assert forbidden - SYNTHETIC_TRANSITIONAL_ALLOWLIST == set()


@pytest.mark.small
def test_unregistered_violation_in_shim_is_detected() -> None:
    """allowlist に無い禁止 signature は 1 件でも検出される。

    ``cli_main.py`` は module 単位ではなく statement 単位で除外されるため、
    新しい境界違反が追加されれば既存 7 件が残っていても検出される。
    """
    source = (
        "from .commands.run import _apply_execution_overrides, _run_failure_triage, "
        "_validate_workflow_provider_match, cmd_run\n"
        "from .commands.output import _brand_new_violation\n"
    )
    found = classify_source(source, "kaji_harness.cli_main", PACKAGES)
    forbidden = {r.signature for r in found if r.classification == "forbidden"}
    residual = forbidden - SYNTHETIC_TRANSITIONAL_ALLOWLIST
    assert residual == {
        ("kaji_harness.cli_main", "kaji_harness.commands.output", ("_brand_new_violation",))
    }


@pytest.mark.small
def test_stale_allowlist_entry_is_detected() -> None:
    """allowlist entry に対応する statement が消えたら stale として検出される。"""
    # shim が 1 statement しか持たない状態を合成する（= 残り 6 entry が stale）
    source = (
        "from .commands.run import _apply_execution_overrides, _run_failure_triage, "
        "_validate_workflow_provider_match, cmd_run\n"
    )
    found = classify_source(source, "kaji_harness.cli_main", PACKAGES)
    forbidden = {r.signature for r in found if r.classification == "forbidden"}
    stale = SYNTHETIC_TRANSITIONAL_ALLOWLIST - forbidden
    assert len(stale) == 6
    assert all(m == "kaji_harness.cli_main" for m, _, _ in stale)


# ---------------------------------------------------------------------------
# Medium: 実 kaji_harness/ ツリーに対する fitness test（ディスク走査あり）
# ---------------------------------------------------------------------------


@pytest.mark.medium
def test_discovered_packages_include_sub_packages() -> None:
    """``discover_packages`` が sub-package を取りこぼさない。

    取りこぼすと ``pkg(T)`` が親 package に落ち、facade 経由の境界違反が
    許容と誤判定される（下の fitness test が黙って素通りする）。
    """
    packages = discover_packages(PACKAGE_ROOT)
    assert {ROOT_PACKAGE, "kaji_harness.providers", "kaji_harness.commands"} <= packages


@pytest.mark.medium
def test_no_forbidden_private_imports_in_package() -> None:
    """禁止 signature の集合が ``TRANSITIONAL_ALLOWLIST`` と厳密一致する。

    - 未登録の禁止 signature がある → 新規の境界違反 → fail
    - allowlist entry に対応する statement が無い → stale → fail（entry の撤去を強制する）
    """
    found = collect_private_imports(PACKAGE_ROOT)
    forbidden = {r.signature for r in found if r.classification == "forbidden"}

    unregistered = forbidden - TRANSITIONAL_ALLOWLIST
    stale = TRANSITIONAL_ALLOWLIST - forbidden

    assert not unregistered, (
        f"package / 層の境界を越える private import が検出された (ADR 009):\n{_fmt(unregistered)}"
    )
    assert not stale, (
        "TRANSITIONAL_ALLOWLIST が stale。対応する import statement が存在しない "
        f"entry を削除すること:\n{_fmt(stale)}"
    )
