"""Tests for ``provider_overlay_divergence_warning`` (Issue gl:28).

``git worktree add`` は gitignored な ``.kaji/config.local.toml`` overlay を
新規 worktree にコピーしない。overlay 不在 worktree から provider 解決が tracked
``.kaji/config.toml`` の値へ沈黙でフォールバックし、main worktree の overlay と
食い違うケースを検出して WARN を出す機能の検証。

Issue #191 (GitLab forge 撤去) 以降、divergence の対比対象は
``github`` × ``local`` の組合せで表現する（gl:28 が保護する「overlay 差分の
沈黙的 provider 取り違え」OB は forge 種別非依存で、有効な 2 type 間の divergence
として再現可能）。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from kaji_harness.commands.config import _load_config_for_dispatch
from kaji_harness.commands.main import main as cli_main
from kaji_harness.config import (
    ExecutionConfig,
    GitHubProviderConfig,
    KajiConfig,
    LocalProviderConfig,
    PathsConfig,
    ProviderConfig,
)
from kaji_harness.providers import (
    _read_overlay_provider_type as read_overlay_provider_type,
)
from kaji_harness.providers import provider_overlay_divergence_warning
from kaji_harness.providers.local import LocalProviderError

# auto-close hazard pattern（WARN 文言に含めてはならない、GitHub auto-close 互換）。
_AUTOCLOSE_HAZARD = re.compile(
    r"(?i)(clos(e[sd]?|ing)|fix(e[sd]|ing)?|resolv(e[sd]?|ing)|implement(s|ing|ed)?)\s*#\d"
)


def _make_config(
    repo_root: Path,
    *,
    provider_type: str | None = "github",
    overlay_present: bool = False,
    linked_worktree: bool = True,
) -> KajiConfig:
    """テスト用 ``KajiConfig`` を構築する。

    ``linked_worktree`` が ``True`` のとき ``repo_root/.git`` を *ファイル* として
    作成し、``git worktree add`` 由来の linked worktree を模擬する。``False`` のとき
    は ``.git`` ディレクトリを作成し通常 clone / main worktree を模擬する。
    """
    if linked_worktree:
        (repo_root / ".git").write_text("gitdir: /tmp/fake/.bare/worktrees/wt\n")
    else:
        (repo_root / ".git").mkdir(exist_ok=True)
    provider: ProviderConfig | None
    if provider_type is None:
        provider = None
    else:
        provider = ProviderConfig(
            type=provider_type,  # type: ignore[arg-type]
            local=LocalProviderConfig(machine_id="pc1"),
            github=GitHubProviderConfig(repo="owner/repo"),
        )
    return KajiConfig(
        repo_root=repo_root,
        paths=PathsConfig(artifacts_dir=".kaji/artifacts", skill_dir=".claude/skills"),
        execution=ExecutionConfig(default_timeout=1800),
        provider=provider,
        provider_overlay_present=overlay_present,
    )


# ============================================================
# Small tests — 分岐ロジック（resolve_main_worktree / overlay 読取を mock）
# ============================================================


@pytest.mark.small
class TestProviderOverlayDivergenceWarning:
    """``provider_overlay_divergence_warning`` の分岐網羅。"""

    def test_provider_none_returns_none(self, tmp_path: Path) -> None:
        cfg = _make_config(tmp_path, provider_type=None)
        assert provider_overlay_divergence_warning(cfg) is None

    def test_overlay_present_returns_none_without_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """現 worktree 自身に overlay あり → resolve_main_worktree を呼ばず None。"""
        called = False

        def _spy(**_kwargs: object) -> Path:
            nonlocal called
            called = True
            return tmp_path

        monkeypatch.setattr("kaji_harness.providers.resolve_main_worktree", _spy)
        cfg = _make_config(tmp_path, overlay_present=True)
        assert provider_overlay_divergence_warning(cfg) is None
        assert called is False

    def test_non_linked_worktree_returns_none_without_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``.git`` がディレクトリ（通常 clone / main worktree）→ subprocess 起動せず None。"""
        called = False

        def _spy(**_kwargs: object) -> Path:
            nonlocal called
            called = True
            return tmp_path

        monkeypatch.setattr("kaji_harness.providers.resolve_main_worktree", _spy)
        cfg = _make_config(tmp_path, linked_worktree=False)
        assert provider_overlay_divergence_warning(cfg) is None
        assert called is False

    def test_resolve_main_worktree_error_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """非 git / git CLI 不在 等で LocalProviderError → 握り潰して None。"""

        def _raise(**_kwargs: object) -> Path:
            raise LocalProviderError("not a git repository")

        monkeypatch.setattr("kaji_harness.providers.resolve_main_worktree", _raise)
        cfg = _make_config(tmp_path)
        assert provider_overlay_divergence_warning(cfg) is None

    def test_main_worktree_is_current_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """解決された main worktree が現 worktree と同一 → 自分が main → None。"""
        monkeypatch.setattr(
            "kaji_harness.providers.resolve_main_worktree",
            lambda **_kw: tmp_path,
        )
        cfg = _make_config(tmp_path)
        assert provider_overlay_divergence_warning(cfg) is None

    def test_main_overlay_absent_or_typeless_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main overlay 不在 / type 無し / parse 失敗（_read が None）→ None。"""
        monkeypatch.setattr(
            "kaji_harness.providers.resolve_main_worktree",
            lambda **_kw: tmp_path / "main",
        )
        monkeypatch.setattr(
            "kaji_harness.providers._read_overlay_provider_type",
            lambda _p: None,
        )
        cfg = _make_config(tmp_path)
        assert provider_overlay_divergence_warning(cfg) is None

    def test_main_overlay_type_matches_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main overlay の type が現解決 type と一致 → ズレ無し → None。"""
        monkeypatch.setattr(
            "kaji_harness.providers.resolve_main_worktree",
            lambda **_kw: tmp_path / "main",
        )
        monkeypatch.setattr(
            "kaji_harness.providers._read_overlay_provider_type",
            lambda _p: "github",
        )
        cfg = _make_config(tmp_path, provider_type="github")
        assert provider_overlay_divergence_warning(cfg) is None

    def test_main_overlay_type_diverges_returns_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main overlay の type が現解決 type と相違 → WARN を返す。"""
        monkeypatch.setattr(
            "kaji_harness.providers.resolve_main_worktree",
            lambda **_kw: tmp_path / "main",
        )
        monkeypatch.setattr(
            "kaji_harness.providers._read_overlay_provider_type",
            lambda _p: "local",
        )
        cfg = _make_config(tmp_path, provider_type="github")
        warning = provider_overlay_divergence_warning(cfg)
        assert warning is not None
        # 両方の provider.type 値を含む
        assert "github" in warning
        assert "local" in warning
        # 復旧手順を案内する
        assert "config.local.toml" in warning
        assert "kaji local init" in warning
        # auto-close hazard pattern を含まない
        assert not _AUTOCLOSE_HAZARD.search(warning)


# ============================================================
# Medium tests — _read_overlay_provider_type（ファイル I/O）
# ============================================================


@pytest.mark.medium
class TestReadOverlayProviderType:
    """``_read_overlay_provider_type`` の入力分岐（実ファイル）。"""

    def test_absent_file_returns_none(self, tmp_path: Path) -> None:
        assert read_overlay_provider_type(tmp_path / "missing.toml") is None

    def test_valid_provider_type(self, tmp_path: Path) -> None:
        overlay = tmp_path / "config.local.toml"
        overlay.write_text('[provider]\ntype = "local"\n')
        assert read_overlay_provider_type(overlay) == "local"

    def test_no_provider_section_returns_none(self, tmp_path: Path) -> None:
        overlay = tmp_path / "config.local.toml"
        overlay.write_text('[paths]\nskill_dir = ".claude/skills"\n')
        assert read_overlay_provider_type(overlay) is None

    def test_provider_section_without_type_returns_none(self, tmp_path: Path) -> None:
        overlay = tmp_path / "config.local.toml"
        overlay.write_text('[provider.github]\nrepo = "owner/repo"\n')
        assert read_overlay_provider_type(overlay) is None

    def test_malformed_toml_returns_none(self, tmp_path: Path) -> None:
        overlay = tmp_path / "config.local.toml"
        overlay.write_text("not valid toml [[[\n")
        assert read_overlay_provider_type(overlay) is None


# ============================================================
# Medium tests — 再現テスト（実 git worktree, Issue gl:28 OB 再現）
# ============================================================


@pytest.fixture()
def hybrid_worktrees(tmp_path: Path) -> tuple[Path, Path]:
    """tracked ``config.toml`` (type=github) + main overlay (type=local) の
    main / feature worktree ペアを作成する（Issue gl:28 の再現環境）。

    Issue #191 撤去後は GitLab を使わず、有効な 2 type（``github`` × ``local``）
    間の divergence で gl:28 の OB（沈黙の provider 取り違え）を再現する。

    Returns:
        ``(main_worktree, feature_worktree)`` の絶対パス。feature worktree には
        gitignored な ``config.local.toml`` が存在しない。
    """
    bare = tmp_path / "repo.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(bare)],
        check=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True)
    for key, value in (
        ("user.email", "t@t"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(["git", "-C", str(seed), "config", key, value], check=True)
    kaji_dir = seed / ".kaji"
    kaji_dir.mkdir()
    (kaji_dir / "config.toml").write_text(
        "[paths]\n"
        'skill_dir = ".claude/skills"\n'
        'artifacts_dir = ".kaji/artifacts"\n\n'
        "[execution]\n"
        "default_timeout = 1800\n\n"
        "[provider]\n"
        'type = "github"\n\n'
        "[provider.github]\n"
        'repo = "owner/repo"\n'
    )
    (seed / ".gitignore").write_text(".kaji/config.local.toml\n")
    subprocess.run(
        ["git", "-C", str(seed), "add", ".kaji/config.toml", ".gitignore"],
        check=True,
    )
    subprocess.run(["git", "-C", str(seed), "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True)
    main_wt = tmp_path / "main"
    feat_wt = tmp_path / "feat"
    subprocess.run(
        ["git", "-C", str(bare), "worktree", "add", "-q", str(main_wt), "main"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(bare), "worktree", "add", "-q", "-b", "fix/28", str(feat_wt), "main"],
        check=True,
    )
    # overlay は main worktree にのみ置く（gitignored・非 tracked のため
    # feature worktree には物理的に存在しない）。tracked が ``github`` で overlay
    # が ``local`` なので、main worktree は ``local`` 解決、feature worktree は
    # tracked へのフォールバックで ``github`` 解決になる（gl:28 の OB 再現）。
    (main_wt / ".kaji" / "config.local.toml").write_text(
        '[provider]\ntype = "local"\n\n[provider.local]\nmachine_id = "pc1"\n'
    )
    return main_wt.resolve(), feat_wt.resolve()


@pytest.mark.medium
class TestProviderOverlayDivergenceReproduction:
    """Issue gl:28 の OB（沈黙の provider 取り違え）を実 worktree で再現する。"""

    def test_overlay_present_flag_per_worktree(self, hybrid_worktrees: tuple[Path, Path]) -> None:
        """feature worktree は overlay 不在、main worktree は overlay 在。"""
        main_wt, feat_wt = hybrid_worktrees
        feat_cfg = KajiConfig.discover(start_dir=feat_wt)
        assert feat_cfg.provider_overlay_present is False
        main_cfg = KajiConfig.discover(start_dir=main_wt)
        assert main_cfg.provider_overlay_present is True

    def test_feature_worktree_emits_divergence_warning(
        self, hybrid_worktrees: tuple[Path, Path]
    ) -> None:
        """再現テスト本体: overlay 不在 worktree で WARN が両 type を含んで返る。

        修正前は ``provider_overlay_divergence_warning`` 自体が存在せず本 assert
        が成立しない（Red）。修正後に WARN が返る（Green）。
        """
        _main_wt, feat_wt = hybrid_worktrees
        feat_cfg = KajiConfig.discover(start_dir=feat_wt)
        warning = provider_overlay_divergence_warning(feat_cfg)
        assert warning is not None
        assert "github" in warning  # 現 worktree の解決値（tracked 由来）
        assert "local" in warning  # main worktree overlay の選択値
        assert not _AUTOCLOSE_HAZARD.search(warning)

    def test_main_worktree_no_false_positive(self, hybrid_worktrees: tuple[Path, Path]) -> None:
        """main worktree 起点では WARN を出さない（誤検出しない）。"""
        main_wt, _feat_wt = hybrid_worktrees
        main_cfg = KajiConfig.discover(start_dir=main_wt)
        assert provider_overlay_divergence_warning(main_cfg) is None


# ============================================================
# Medium tests — CLI 発火点の統合検証（実 git worktree, 検証 4〜8）
# ============================================================

# WARN 文言に常に含まれる識別句。1 コマンドあたり 1 回しか現れないため、
# stderr 内の出現回数を数えれば WARN の重複発火を検出できる（検証 7）。
_DIVERGENCE_MARKER = "no .kaji/config.local.toml overlay"


@pytest.mark.medium
class TestProviderOverlayDivergenceCliWiring:
    """検証 4〜8: WARN が CLI 発火点（``kaji issue`` / ``kaji pr`` の dispatch、
    ``kaji run``、``kaji config provider-type``）から実際に出ることを、CLI 表面を
    駆動して確認する。

    検証 2/3 の関数直接呼び出しだけでは「``provider_overlay_divergence_warning()``
    は正しいが CLI コマンドから呼ばれていない（発火点の配線抜け）」回帰を検出
    できない。本クラスは発火点の配線そのものを保護する（設計レビュー Must Fix 1）。
    """

    def test_dispatch_path_emits_warning(
        self,
        hybrid_worktrees: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """検証 4: ``kaji issue`` / ``kaji pr`` が共有する ``_load_config_for_dispatch()``
        が overlay 不在 worktree で stderr に WARN を出す。config 解決契約は不変で
        stdout には何も漏らさない。"""
        _main_wt, feat_wt = hybrid_worktrees
        monkeypatch.chdir(feat_wt)
        config = _load_config_for_dispatch()
        captured = capsys.readouterr()
        # config 解決契約は不変（例外を出さず provider 付き config を返す）。
        assert config.provider is not None
        assert config.provider.type == "github"
        # WARN は stderr のみ。stdout 契約を壊さない。
        assert captured.out == ""
        assert _DIVERGENCE_MARKER in captured.err
        assert "'github'" in captured.err  # 現 worktree の解決値（tracked 由来）
        assert "'local'" in captured.err  # main worktree overlay の選択値
        assert "kaji local init" in captured.err  # 復旧手順
        assert not _AUTOCLOSE_HAZARD.search(captured.err)

    def test_cmd_run_path_emits_warning(
        self,
        hybrid_worktrees: tuple[Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """検証 5: ``kaji run`` 経路（``cmd_run``）が overlay 不在 worktree で
        stderr に WARN を出す。WARN は workflow 読込前に出るため、存在しない
        workflow を渡しても WARN は観測でき、exit code 契約（workflow 不在 = 2）
        は WARN 追加で変わらない。"""
        _main_wt, feat_wt = hybrid_worktrees
        rc = cli_main(
            ["run", str(feat_wt / "missing-workflow.yaml"), "28", "--workdir", str(feat_wt)]
        )
        captured = capsys.readouterr()
        assert rc == 2  # workflow 不在 → EXIT_DEFINITION_ERROR（WARN 追加で不変）
        assert _DIVERGENCE_MARKER in captured.err
        assert "'github'" in captured.err
        assert "'local'" in captured.err

    def test_cmd_config_provider_type_path_emits_warning(
        self,
        hybrid_worktrees: tuple[Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """検証 6: ``kaji config provider-type`` 経路（``cmd_config_provider_type``）。
        stdout は従来どおり ``"github\\n"`` のみ、stderr に WARN という stdout /
        stderr 分離契約を保つ。"""
        _main_wt, feat_wt = hybrid_worktrees
        rc = cli_main(["config", "provider-type", "--workdir", str(feat_wt)])
        captured = capsys.readouterr()
        assert rc == 0
        assert captured.out == "github\n"  # stdout 契約は不変
        assert _DIVERGENCE_MARKER in captured.err
        assert "'local'" in captured.err

    def test_warning_emitted_at_most_once_per_command(
        self,
        hybrid_worktrees: tuple[Path, Path],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """検証 7: 1 コマンド実行につき WARN は最大 1 回。発火 helper を二重に
        呼んでいないことを stderr 内のマーカー出現回数で確認する。"""
        _main_wt, feat_wt = hybrid_worktrees
        cli_main(["config", "provider-type", "--workdir", str(feat_wt)])
        captured = capsys.readouterr()
        assert captured.err.count(_DIVERGENCE_MARKER) == 1

    def test_main_worktree_no_warning_at_cli_surface(
        self,
        hybrid_worktrees: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """検証 8: overlay を持つ main worktree からは CLI 表面でも WARN を
        出さない（誤検出しない）。dispatch 経路と config provider-type 経路の
        双方を確認する。"""
        main_wt, _feat_wt = hybrid_worktrees
        # dispatch 経路（kaji issue / kaji pr 相当）
        monkeypatch.chdir(main_wt)
        _load_config_for_dispatch()
        # kaji config provider-type 経路
        rc = cli_main(["config", "provider-type", "--workdir", str(main_wt)])
        captured = capsys.readouterr()
        assert rc == 0
        assert captured.out == "local\n"  # main worktree では overlay が効く
        assert _DIVERGENCE_MARKER not in captured.err
