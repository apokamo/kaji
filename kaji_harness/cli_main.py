"""kaji CLI 互換 shim。実体は kaji_harness.commands 配下(#283 R1 で分割)。

旧 `from kaji_harness.cli_main import X` / `python -m kaji_harness.cli_main` /
console entrypoint `kaji_harness.cli_main:main` を維持する。最終削除は #284。
"""

from __future__ import annotations

import shutil  # 互換 export: 旧 patch target cli_main.shutil.which の解決に必須(§patch 対応表)
import subprocess  # 互換 export: 旧 patch target cli_main.subprocess.run の解決に必須(§patch 対応表)
import sys

from .commands.config import (
    _emit_provider_overlay_divergence_warning,
    _load_config_for_dispatch,
    cmd_config_artifacts_dir,
    cmd_config_provider_type,
)
from .commands.exit_codes import (
    EXIT_ABORT,
    EXIT_CONFIG_NOT_FOUND,
    EXIT_DEFINITION_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
    EXIT_VALIDATION_ERROR,
)
from .commands.issue import (
    _LOCAL_ISSUE_SUBS,
    _commit_local_issue_change,
    _github_issue_comment_with_verdict,
    _handle_issue,
    _handle_issue_context,
    _handle_issue_local,
    _handle_issue_prepend_note,
    _has_verdict_flags,
    _local_issue_close,
    _local_issue_comment,
    _local_issue_create,
    _local_issue_edit,
    _local_issue_list,
    _local_issue_view,
    _resolve_local_id,
)
from .commands.main import main
from .commands.output import (
    _apply_jq,
    _compose_json_and_jq,
    _emit_json,
    _format_jq_results,
    _issue_to_json_dict,
    _read_body_arg,
)
from .commands.parser import (
    _add_recovery_arguments,
    _get_version,
    _register_config,
    _register_issue,
    _register_pr,
    _register_recover,
    _register_run,
    _register_sync,
    _register_validate,
    create_parser,
)
from .commands.pr import (
    _FORGE_METHOD_FLAGS,
    _GH_MISSING_GUIDANCE,
    _PR_BARE_PROVIDER_ERROR,
    _PR_BUILTIN_SUBCOMMANDS,
    _detect_repo,
    _dispatch_pr_builtin,
    _forward_pr_api_list,
    _forward_pr_reply_to_comment,
    _forward_pr_review_comments,
    _forward_pr_reviews,
    _forward_to_gh,
    _gh_capture_value,
    _gh_post_issue_comment_silent,
    _github_pr_review,
    _handle_pr,
    _has_approve_flag,
    _has_request_changes_flag,
    _is_ascii_decimal,
    _run_pr_review_poll,
    _user_specified_repo,
)
from .commands.recover import (
    _resolve_recover_issue_context,
    _resolve_target_run_dir,
    cmd_recover,
)
from .commands.run import (
    _apply_execution_overrides,
    _run_failure_triage,
    _validate_workflow_provider_match,
    cmd_run,
)
from .commands.sync import cmd_sync_from_github, cmd_sync_status
from .commands.validate import (
    _print_error,
    _print_success,
    _resolve_project_root_for_validate,
    cmd_validate,
)

__all__ = [
    # stdlib module 束縛（属性 patch 68 件の target 解決に必須）
    "shutil",
    "subprocess",
    # exit_codes（7 定数）
    "EXIT_OK",
    "EXIT_ABORT",
    "EXIT_VALIDATION_ERROR",
    "EXIT_DEFINITION_ERROR",
    "EXIT_CONFIG_NOT_FOUND",
    "EXIT_INVALID_INPUT",
    "EXIT_RUNTIME_ERROR",
    # parser（10 関数）
    "_get_version",
    "create_parser",
    "_register_sync",
    "_register_config",
    "_register_run",
    "_add_recovery_arguments",
    "_register_recover",
    "_register_issue",
    "_register_pr",
    "_register_validate",
    # validate（4 関数）
    "_resolve_project_root_for_validate",
    "cmd_validate",
    "_print_success",
    "_print_error",
    # run（4 関数）
    "_apply_execution_overrides",
    "cmd_run",
    "_run_failure_triage",
    "_validate_workflow_provider_match",
    # recover（3 関数）
    "cmd_recover",
    "_resolve_recover_issue_context",
    "_resolve_target_run_dir",
    # pr（16 関数 + 4 定数）
    "_FORGE_METHOD_FLAGS",
    "_user_specified_repo",
    "_forward_to_gh",
    "_PR_BUILTIN_SUBCOMMANDS",
    "_PR_BARE_PROVIDER_ERROR",
    "_is_ascii_decimal",
    "_GH_MISSING_GUIDANCE",
    "_detect_repo",
    "_forward_pr_review_comments",
    "_forward_pr_reviews",
    "_forward_pr_api_list",
    "_forward_pr_reply_to_comment",
    "_run_pr_review_poll",
    "_dispatch_pr_builtin",
    "_has_approve_flag",
    "_has_request_changes_flag",
    "_gh_capture_value",
    "_gh_post_issue_comment_silent",
    "_github_pr_review",
    "_handle_pr",
    # config（4 関数）
    "_emit_provider_overlay_divergence_warning",
    "_load_config_for_dispatch",
    "cmd_config_provider_type",
    "cmd_config_artifacts_dir",
    # output（6 関数）
    "_compose_json_and_jq",
    "_read_body_arg",
    "_apply_jq",
    "_format_jq_results",
    "_issue_to_json_dict",
    "_emit_json",
    # issue（16 関数 + 1 定数）
    "_handle_issue",
    "_github_issue_comment_with_verdict",
    "_resolve_local_id",
    "_has_verdict_flags",
    "_handle_issue_prepend_note",
    "_handle_issue_context",
    "_LOCAL_ISSUE_SUBS",
    "_handle_issue_local",
    "_local_issue_view",
    "_local_issue_create",
    "_commit_local_issue_change",
    "_local_issue_edit",
    "_local_issue_comment",
    "_local_issue_close",
    "_local_issue_list",
    # sync（2 関数）
    "cmd_sync_from_github",
    "cmd_sync_status",
    # main（1 関数）
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
