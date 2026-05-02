"""EPIC configuration schema and validator.

Issue: #164
複数 Issue を束ねる EPIC 設定のモデル / ローダ / バリデータ。
スキーマと検証のみを提供し、実行ランタイムは含まない。

設計書では Pydantic v2 を使用予定だったが、実装時点で `kaji_harness` は
pydantic を依存に持たず（既存 `config.py` / `models.py` は dataclass で実装されている）、
設計上の制約「新規ライブラリは導入しない」を満たすため dataclass で実装する。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import HarnessError


class EpicValidationError(HarnessError):
    """EPIC 設定の検証エラー。複数のエラーをまとめて報告する。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        msg = f"{len(errors)} validation error(s): " + "; ".join(errors)
        super().__init__(msg)


@dataclass
class EpicMember:
    """EPIC を構成する 1 件の Issue 定義。"""

    issue: int
    depends_on: list[int] = field(default_factory=list)
    parallel_group: str | None = None
    merge_order: int | None = None


@dataclass
class EpicConfig:
    """EPIC 全体の設定。

    `__post_init__` でモデルレベルの検証を実行し、エラーがあれば
    `EpicValidationError` を 1 度の例外でまとめて送出する。
    """

    name: str
    members: list[EpicMember]
    description: str = ""

    def __post_init__(self) -> None:
        errors = _collect_validation_errors(self)
        if errors:
            raise EpicValidationError(errors)

    def topological_order(self) -> list[list[int]]:
        """並列グループを段階別に推定する。

        `parallel_group` の明示があればその値で grouping し、依存関係に従って
        グループ単位で topological レベルを構築する。明示がない Issue は
        Kahn のアルゴリズムで level 単位に分割し、同 level を 1 グループとする。
        """
        adjacency, indegree = _build_graph(self.members)
        explicit = {m.issue: m.parallel_group for m in self.members if m.parallel_group}

        levels: list[list[int]] = []
        ready: deque[int] = deque(i for i, deg in indegree.items() if deg == 0)
        while ready:
            current_level = list(ready)
            ready.clear()
            if explicit:
                grouped: dict[str | None, list[int]] = {}
                for issue in current_level:
                    grouped.setdefault(explicit.get(issue), []).append(issue)
                for members in grouped.values():
                    levels.append(sorted(members))
            else:
                levels.append(sorted(current_level))
            for issue in current_level:
                for nxt in adjacency.get(issue, []):
                    indegree[nxt] -= 1
                    if indegree[nxt] == 0:
                        ready.append(nxt)
        return levels

    def sorted_merge_order(self) -> list[int]:
        """`merge_order` 明示の Issue を昇順に並べ、未指定は topological 順末尾に追加する。

        昇順 = 依存先（小さい値）→ 依存元（大きい値）。
        DAG 整合制約をパスしているので、明示順は依存関係を満たす。
        """
        explicit = sorted(
            (m for m in self.members if m.merge_order is not None),
            key=lambda m: m.merge_order if m.merge_order is not None else 0,
        )
        result = [m.issue for m in explicit]
        explicit_ids = {m.issue for m in explicit}
        for level in self.topological_order():
            for issue in level:
                if issue not in explicit_ids:
                    result.append(issue)
        return result


def load_epic(path: Path) -> EpicConfig:
    """YAML ファイルから EpicConfig をロードする。

    Raises:
        EpicValidationError: YAML パースまたは検証エラー
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise EpicValidationError([f"YAML parse error: {e}"]) from e
    if not isinstance(data, dict):
        raise EpicValidationError(["EPIC definition must be a YAML mapping"])
    return _from_dict(data)


def _from_dict(data: dict[str, Any]) -> EpicConfig:
    """dict からモデルを構築する。型不正は EpicValidationError にまとめる。"""
    errors: list[str] = []

    name = data.get("name")
    if not isinstance(name, str) or not name:
        errors.append("'name' must be a non-empty string")
    description = data.get("description", "")
    if not isinstance(description, str):
        errors.append("'description' must be a string")
    raw_members = data.get("members")
    if not isinstance(raw_members, list):
        errors.append("'members' must be a list")
        raise EpicValidationError(errors)

    members: list[EpicMember] = []
    for i, m in enumerate(raw_members):
        if not isinstance(m, dict):
            errors.append(f"members[{i}] must be a mapping")
            continue
        issue = m.get("issue")
        if not isinstance(issue, int):
            errors.append(f"members[{i}].issue must be an int")
            continue
        depends_on_raw = m.get("depends_on", [])
        if not isinstance(depends_on_raw, list) or not all(
            isinstance(x, int) for x in depends_on_raw
        ):
            errors.append(f"members[{i}].depends_on must be a list of int")
            continue
        parallel_group = m.get("parallel_group")
        if parallel_group is not None and not isinstance(parallel_group, str):
            errors.append(f"members[{i}].parallel_group must be a string or null")
            continue
        merge_order = m.get("merge_order")
        if merge_order is not None and not isinstance(merge_order, int):
            errors.append(f"members[{i}].merge_order must be an int or null")
            continue
        members.append(
            EpicMember(
                issue=issue,
                depends_on=list(depends_on_raw),
                parallel_group=parallel_group,
                merge_order=merge_order,
            )
        )

    if errors:
        raise EpicValidationError(errors)

    return EpicConfig(
        name=name if isinstance(name, str) else "",
        members=members,
        description=description if isinstance(description, str) else "",
    )


def _build_graph(
    members: list[EpicMember],
) -> tuple[dict[int, list[int]], dict[int, int]]:
    """`depends_on` から「依存先 → 依存元」の隣接リストと in-degree を構築する。"""
    adjacency: dict[int, list[int]] = {m.issue: [] for m in members}
    indegree: dict[int, int] = {m.issue: 0 for m in members}
    for m in members:
        for dep in m.depends_on:
            if dep not in adjacency:
                continue
            adjacency[dep].append(m.issue)
            indegree[m.issue] += 1
    return adjacency, indegree


def _collect_validation_errors(epic: EpicConfig) -> list[str]:
    """全検証エラーを 1 回でまとめて収集する。"""
    errors: list[str] = []

    if not epic.members:
        errors.append("members must not be empty")
        return errors

    seen: set[int] = set()
    duplicates: set[int] = set()
    for m in epic.members:
        if m.issue in seen:
            duplicates.add(m.issue)
        seen.add(m.issue)
    for issue in sorted(duplicates):
        errors.append(f"duplicate issue in members: {issue}")

    known = {m.issue for m in epic.members}
    for m in epic.members:
        for dep in m.depends_on:
            if dep == m.issue:
                errors.append(f"self-loop in depends_on: {m.issue} depends on itself")
                continue
            if dep not in known:
                errors.append(
                    f"issue {dep} referenced in depends_on of {m.issue} but not in members"
                )

    order_seen: dict[int, list[int]] = {}
    for m in epic.members:
        if m.merge_order is not None:
            order_seen.setdefault(m.merge_order, []).append(m.issue)
    for value, issues in sorted(order_seen.items()):
        if len(issues) > 1:
            errors.append(f"duplicate merge_order {value} on issues: {sorted(issues)}")

    if errors:
        return errors

    cycles = _detect_cycles(epic.members)
    for cycle in cycles:
        errors.append("cyclic dependency detected: " + " → ".join(str(i) for i in cycle))

    if errors:
        return errors

    reachability = _transitive_closure(epic.members)
    errors.extend(_check_parallel_group_consistency(epic.members, reachability))
    errors.extend(_check_merge_order_consistency(epic.members, reachability))

    return errors


def _detect_cycles(members: list[EpicMember]) -> list[list[int]]:
    """DFS で back-edge を検出し、循環パスを返す。"""
    adjacency: dict[int, list[int]] = {m.issue: list(m.depends_on) for m in members}
    color: dict[int, int] = {m.issue: 0 for m in members}
    parent: dict[int, int | None] = {m.issue: None for m in members}
    cycles: list[list[int]] = []

    for start in adjacency:
        if color[start] != 0:
            continue
        stack: list[tuple[int, int]] = [(start, 0)]
        while stack:
            node, idx = stack[-1]
            if idx == 0:
                color[node] = 1
            neighbors = adjacency[node]
            if idx < len(neighbors):
                stack[-1] = (node, idx + 1)
                nxt = neighbors[idx]
                if nxt not in color:
                    continue
                if color[nxt] == 0:
                    parent[nxt] = node
                    stack.append((nxt, 0))
                elif color[nxt] == 1:
                    cycle = [nxt, node]
                    p = parent[node]
                    while p is not None and p != nxt:
                        cycle.append(p)
                        p = parent[p]
                    cycle.append(nxt)
                    cycle.reverse()
                    cycles.append(cycle)
            else:
                color[node] = 2
                stack.pop()
    return cycles


def _transitive_closure(members: list[EpicMember]) -> dict[int, set[int]]:
    """各 Issue から到達可能な依存先（直接 + 推移的）を計算する。"""
    direct: dict[int, set[int]] = {m.issue: set(m.depends_on) for m in members}
    closure: dict[int, set[int]] = {}
    for issue in direct:
        visited: set[int] = set()
        stack = list(direct[issue])
        while stack:
            cur = stack.pop()
            if cur in visited or cur not in direct:
                continue
            visited.add(cur)
            stack.extend(direct[cur])
        closure[issue] = visited
    return closure


def _check_parallel_group_consistency(
    members: list[EpicMember],
    reachability: dict[int, set[int]],
) -> list[str]:
    errors: list[str] = []
    by_group: dict[str, list[int]] = {}
    for m in members:
        if m.parallel_group:
            by_group.setdefault(m.parallel_group, []).append(m.issue)
    for group, issues in by_group.items():
        for i, a in enumerate(issues):
            for b in issues[i + 1 :]:
                if b in reachability.get(a, set()) or a in reachability.get(b, set()):
                    errors.append(
                        f"parallel_group '{group}' conflicts with depends_on: "
                        f"{a} and {b} are in the same group but have a dependency relation"
                    )
    return errors


def _check_merge_order_consistency(
    members: list[EpicMember],
    reachability: dict[int, set[int]],
) -> list[str]:
    """A が B に depends_on → merge_order(B) < merge_order(A) を強制する。"""
    errors: list[str] = []
    order: dict[int, int] = {m.issue: m.merge_order for m in members if m.merge_order is not None}
    for a, deps in reachability.items():
        if a not in order:
            continue
        for b in deps:
            if b not in order:
                continue
            if not (order[b] < order[a]):
                errors.append(
                    f"merge_order order conflicts with depends_on: "
                    f"{a} depends on {b} but merge_order({a})={order[a]} "
                    f"<= merge_order({b})={order[b]}"
                )
    return errors
