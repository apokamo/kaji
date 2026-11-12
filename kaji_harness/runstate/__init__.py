"""Run state facts shared by ``kaji status`` / retry / series resume (Issue #540).

- ``process``: 生存記録（``process.json`` / launch record）と生存判定
- ``launch``: Issue 単位の launch record と ``LaunchLock``
- ``runlog``: run.log の解釈と必須 artifact の判定（recovery からも使う）
- ``facts``（``kaji_harness.runstate.facts``）: run / Issue の事実の収集（唯一の判定実装）
- ``schema``（``kaji_harness.runstate.schema``）: ``kaji status --json`` の schema v1

``facts`` / ``schema`` は recovery package に依存し、recovery の snapshot は ``runlog`` に
依存するため、循環を避けて本 facade では再 export しない。
"""

from __future__ import annotations

from .launch import (
    LAUNCH_LOCK_FILE,
    LAUNCHES_DIR,
    LaunchEntry,
    LaunchLock,
    launch_record_path,
    launches_dir,
    list_launch_records,
    remove_launch_record,
    set_launch_run_id,
    write_launch_record,
)
from .process import (
    PROCESS_FILE,
    PROCESS_RECORD_SCHEMA_VERSION,
    HostProbe,
    Liveness,
    ProcessRecord,
    ProcessRecordError,
    complete_with_run_log,
    current_process_record,
    is_finished_by_log,
    parse_proc_stat,
    probe_liveness,
    read_process_record,
    write_process_record,
)
from .runlog import (
    ArtifactProblem,
    RunLogRead,
    find_unterminated_step,
    read_run_log,
    required_result_problems,
)

__all__ = [
    "LAUNCHES_DIR",
    "LAUNCH_LOCK_FILE",
    "PROCESS_FILE",
    "PROCESS_RECORD_SCHEMA_VERSION",
    "ArtifactProblem",
    "HostProbe",
    "LaunchEntry",
    "LaunchLock",
    "Liveness",
    "ProcessRecord",
    "ProcessRecordError",
    "RunLogRead",
    "complete_with_run_log",
    "current_process_record",
    "find_unterminated_step",
    "is_finished_by_log",
    "launch_record_path",
    "launches_dir",
    "list_launch_records",
    "parse_proc_stat",
    "probe_liveness",
    "read_process_record",
    "read_run_log",
    "remove_launch_record",
    "required_result_problems",
    "set_launch_run_id",
    "write_launch_record",
    "write_process_record",
]
