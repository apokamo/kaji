"""Shared preconditions for ``kaji watch`` entry points (Issue #543 D1・D4・M7).

5 つのサブコマンド・内部 loop・``run_watched execute``・``watch_labels`` が同じ関数で
前提を検証する。書き込みを行う入口は、最初の副作用（lock 以外）より前にこれを呼ぶ。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..config import KajiConfig, WatchConfig
from ..errors import CLINotFoundError, WatchPreconditionError
from ..interactive_terminal_herdr import resolve_herdr_cli
from ..providers import IssueProvider, get_provider
from .constants import CONTRACT_LABELS


@dataclass(frozen=True)
class WatchContext:
    """前提を満たした実行文脈。

    Attributes:
        config: 読み込んだ config。
        watch: tracked の ``[watch]``。
        provider: GitHub provider。
        repo: provider の repo（``owner/name``）。
        herdr_bin: 解決した ``herdr``（Herdr を要求も観測もしなかった、または観測で満たさなかった場合は ``None``）。
        herdr_pane_id: 呼び出し元の pane（同上）。
    """

    config: KajiConfig
    watch: WatchConfig
    provider: IssueProvider
    repo: str
    herdr_bin: str | None
    herdr_pane_id: str | None


def check_herdr(
    env: Mapping[str, str] | None = None,
    resolver: Callable[[], str] = resolve_herdr_cli,
) -> tuple[str, str]:
    """Herdr の前提（``HERDR_ENV=1``・``HERDR_PANE_ID``・``herdr``）を検証する。

    Returns:
        ``(herdr の実行パス, 呼び出し元の pane_id)``。

    Raises:
        WatchPreconditionError: Herdr の外、または ``herdr`` が解決できない場合。
    """
    environ = os.environ if env is None else env
    if environ.get("HERDR_ENV") != "1" or not environ.get("HERDR_PANE_ID"):
        raise WatchPreconditionError(
            "herdr", "kaji watch must run inside a Herdr pane (HERDR_ENV=1 and HERDR_PANE_ID)"
        )
    try:
        herdr = resolver()
    except CLINotFoundError as exc:
        raise WatchPreconditionError("herdr", str(exc)) from exc
    return herdr, environ["HERDR_PANE_ID"]


def check_watch_preconditions(
    config: KajiConfig,
    *,
    require_herdr: bool,
    probe_herdr: bool = False,
    provider: IssueProvider | None = None,
    env: Mapping[str, str] | None = None,
    herdr_resolver: Callable[[], str] = resolve_herdr_cli,
) -> WatchContext:
    """``[watch]`` の宣言・github provider・Herdr・契約の 6 ラベルを順に検証する。

    Args:
        config: 読み込んだ config。
        require_herdr: Herdr の前提を求めるか（書き込みを行う入口）。
        probe_herdr: ``require_herdr`` が偽のとき、Herdr の前提を満たしていれば解決する
            （読み取りの入口。満たさなくてもエラーにせず ``None`` のままにする）。
        provider: 注入する provider（省略時は ``get_provider``）。
        env: 環境変数（テスト用）。
        herdr_resolver: ``herdr`` の解決（テスト用）。

    Returns:
        前提を満たした実行文脈。

    Raises:
        WatchPreconditionError: いずれかの前提を満たさない場合（``reason`` で種類を示す）。
    """
    if config.watch is None:
        raise WatchPreconditionError(
            "not_declared", "[watch] is not declared in the tracked .kaji/config.toml"
        )
    if config.provider is None or config.provider.type != "github":
        raise WatchPreconditionError("provider", "kaji watch requires provider.type = 'github'")
    herdr_bin: str | None = None
    herdr_pane: str | None = None
    if require_herdr:
        herdr_bin, herdr_pane = check_herdr(env, herdr_resolver)
    elif probe_herdr:
        try:
            herdr_bin, herdr_pane = check_herdr(env, herdr_resolver)
        except WatchPreconditionError:
            herdr_bin, herdr_pane = None, None
    resolved = provider if provider is not None else get_provider(config)
    existing = {label.name for label in resolved.list_labels()}
    missing = [name for name in CONTRACT_LABELS if name not in existing]
    if missing:
        raise WatchPreconditionError(
            "labels",
            "[watch] is declared but contract labels are missing on GitHub: " + ", ".join(missing),
        )
    return WatchContext(
        config=config,
        watch=config.watch,
        provider=resolved,
        repo=config.provider.github.repo,
        herdr_bin=herdr_bin,
        herdr_pane_id=herdr_pane,
    )
