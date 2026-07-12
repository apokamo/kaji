"""kaji CLI の exit code 定数（共有 leaf。#283 R1 で cli_main.py から分割）。"""

from __future__ import annotations

EXIT_OK = 0
EXIT_ABORT = 1
EXIT_VALIDATION_ERROR = 1
EXIT_DEFINITION_ERROR = 2
EXIT_CONFIG_NOT_FOUND = 2
EXIT_INVALID_INPUT = 2
EXIT_RUNTIME_ERROR = 3
