"""jq / JSON 整形・body 引数読込（共有 leaf。#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

import sys
from pathlib import Path

from ..providers.models import Issue
from .exit_codes import EXIT_OK, EXIT_RUNTIME_ERROR


def _compose_json_and_jq(fields: list[str] | None, jq: str | None) -> str | None:
    """Compose ``--json FIELDS`` and ``--jq EXPR`` into a single ``gh api --jq`` expression.

    `gh api` does not accept ``--json`` (only ``--jq``), so kaji turns
    ``--json`` into a jq projection and chains it before the user expression.

    - fields only          -> ``[.[] | {f1: .f1, f2: .f2}]``
    - jq only              -> ``<jq>``
    - both                 -> ``[.[] | {f1: .f1, ...}] | <jq>``
    - neither              -> None (do not pass ``--jq`` to gh)
    """
    if fields is None and jq is None:
        return None
    field_proj = "[.[] | {" + ", ".join(f"{f}: .{f}" for f in fields) + "}]" if fields else None
    if field_proj and jq:
        return f"{field_proj} | {jq}"
    return field_proj or jq


def _read_body_arg(body: str | None, body_file: str | None) -> str | None:
    """``--body`` / ``--body-file`` を解決する。両方指定 / 不在の扱いは呼出側。

    ``body_file == "-"`` で stdin、それ以外はファイル読み込み。
    """
    if body is not None and body_file is not None:
        raise ValueError("--body and --body-file are mutually exclusive")
    if body is not None:
        return body
    if body_file is None:
        return None
    if body_file == "-":
        return sys.stdin.read()
    return Path(body_file).read_text(encoding="utf-8")


def _apply_jq(json_text: str, expr: str) -> tuple[str, int]:
    """Python ``jq`` package で ``json_text`` に式を適用する（``gh --jq`` 互換 raw 出力）。

    Phase 3-d preflight: system ``jq`` バイナリ依存を撤去し、PyPI ``jq``
    package を runtime dependency に格上げした（design.md / phase3d-preflight
    § 2）。

    `gh --jq` および `jq -r` と互換な raw 出力ルール:

    - string         → 改行を含めてそのまま出力 + 末尾 newline 1
    - number / bool  → decimal / ``true`` / ``false`` + newline
    - null           → 空行（newline のみ）
    - object / array → compact JSON + newline
    - stream         → 各結果を上記ルールで整形し連結
    - empty stream   → 出力なし、exit 0
    - syntax/runtime → exit 3、stderr に jq 例外メッセージを user-facing 整形

    Skill 群は ``CURRENT_BODY=$(kaji issue view N --json body -q '.body')``
    のように shell 変数代入で raw 値を期待しているため、string は quote 無しで
    出さなければならない。
    """
    import json as _json

    try:
        data = _json.loads(json_text)
    except _json.JSONDecodeError as exc:
        sys.stderr.write(f"Error: invalid JSON passed to jq: {exc}\n")
        return "", EXIT_RUNTIME_ERROR

    try:
        import jq as _jq  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — runtime dependency 化後は不到達
        sys.stderr.write(
            "Error: Python 'jq' package is required but not installed. "
            f"Reinstall kaji ('uv sync' / 'pip install kaji'). Detail: {exc}\n"
        )
        return "", EXIT_RUNTIME_ERROR

    try:
        program = _jq.compile(expr)
    except ValueError as exc:
        sys.stderr.write(f"Error: jq compile failed: {exc}\n")
        return "", EXIT_RUNTIME_ERROR

    try:
        results = program.input_value(data).all()
    except ValueError as exc:
        sys.stderr.write(f"Error: jq runtime error: {exc}\n")
        return "", EXIT_RUNTIME_ERROR

    return _format_jq_results(results), EXIT_OK


def _format_jq_results(results: list[object]) -> str:
    """``jq.compile(...).all()`` の結果配列を ``jq -r`` 互換 raw 出力に整形する。

    各 result を 1 行として扱い末尾 newline を付ける。string は raw、null は
    空行、object/array は compact JSON にする(design.md § jq 互換 / phase3d
    preflight § 2 出力契約)。
    """
    import json as _json

    parts: list[str] = []
    for value in results:
        if value is None:
            parts.append("")
        elif isinstance(value, str):
            parts.append(value)
        elif isinstance(value, bool):
            parts.append("true" if value else "false")
        elif isinstance(value, (int, float)):
            parts.append(_json.dumps(value))
        else:
            parts.append(_json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    if not parts:
        return ""
    return "\n".join(parts) + "\n"


def _issue_to_json_dict(issue: object, *, include_comments: bool = True) -> dict[str, object]:
    """``Issue`` → gh ``issue view --json ...`` 互換の dict に整形。"""
    assert isinstance(issue, Issue)
    out: dict[str, object] = {
        "number": issue.id,
        "title": issue.title,
        "body": issue.body,
        "state": issue.state,
        "labels": [
            {"name": label.name, "description": label.description, "color": label.color}
            for label in issue.labels
        ],
    }
    if include_comments:
        out["comments"] = [
            {"author": c.author, "body": c.body, "createdAt": c.created_at} for c in issue.comments
        ]
    return out


def _emit_json(payload: object, *, jq_expr: str | None) -> int:
    """JSON を ``--jq`` 経由で整形して stdout に書く。"""
    import json as _json

    text = _json.dumps(payload, ensure_ascii=False)
    if jq_expr is None:
        sys.stdout.write(text + "\n")
        return EXIT_OK
    out, rc = _apply_jq(text, jq_expr)
    if rc != EXIT_OK:
        return rc
    # jq は末尾 newline を出すため二重出力を避けて write
    sys.stdout.write(out)
    return EXIT_OK
