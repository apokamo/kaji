"""``kaji verdict`` CLI handlers（Issue #568）。

argparse の引数を ``verdict_publish.publish_agent_verdict`` へ渡し、結果を exit code に
対応付けるだけを担う。
"""

from __future__ import annotations

import argparse
import os
import sys

from ..errors import (
    VerdictPublishContextError,
    VerdictPublishInputError,
    VerdictSerializationError,
)
from ..verdict_publish import publish_agent_verdict
from .exit_codes import EXIT_INVALID_INPUT, EXIT_OK, EXIT_RUNTIME_ERROR, EXIT_VALIDATION_ERROR


def cmd_verdict_publish(args: argparse.Namespace) -> int:
    """現在の agent step の verdict を検証して公開する。

    Args:
        args: ``status`` / ``reason`` / ``evidence`` / ``suggestion`` を持つ namespace。

    Returns:
        0: 公開成功。1: 入力が無効（直して再実行できる）。2: 状況エラー（公開済み・契約不備）。
        3: I/O エラーまたは直列化の不変条件違反（``verdict.yaml`` は作られていない）。
    """
    try:
        published = publish_agent_verdict(
            os.environ,
            status=args.status,
            reason=args.reason,
            evidence=args.evidence,
            suggestion=args.suggestion,
        )
    except VerdictPublishInputError as exc:
        sys.stderr.write(f"Error: invalid verdict (fix the values and rerun): {exc}\n")
        return EXIT_VALIDATION_ERROR
    except VerdictPublishContextError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return EXIT_INVALID_INPUT
    except (VerdictSerializationError, OSError) as exc:
        sys.stderr.write(f"Error: verdict was not published: {exc}\n")
        return EXIT_RUNTIME_ERROR

    if published.cleanup_warning is not None:
        sys.stderr.write(f"Warning: {published.cleanup_warning}\n")
    sys.stdout.write(
        f"verdict published: step={published.step_id} attempt={published.attempt:03d} "
        f"status={published.status} path={published.path}\n"
    )
    return EXIT_OK
