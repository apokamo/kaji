"""Run non-destructive real-agent lifecycle checks against the Herdr backend."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import yaml

from kaji_harness.errors import CLIExecutionError, StepTimeoutError
from kaji_harness.interactive_terminal import execute_interactive_terminal
from kaji_harness.models import Step

_TIMEOUT_SECONDS = 180
_RESUME_AGENTS = ("claude", "codex")
_FRESH_AGENTS = (*_RESUME_AGENTS, "antigravity")


def _run_attempt(
    *,
    agent: str,
    phase: str,
    artifacts_dir: Path,
    workdir: Path,
    session_id: str | None = None,
) -> tuple[dict[str, object], str | None]:
    """Run one real interactive agent attempt and return sanitized evidence."""
    attempt_dir = artifacts_dir / agent / phase
    attempt_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = attempt_dir / "prompt.txt"
    verdict_path = attempt_dir / "verdict.yaml"
    prompt_path.write_text(
        "Verification only. Do not modify repository files, configuration, issues, or external "
        "state. Read the runner instructions and write a PASS verdict only to the exact temporary "
        "verdict path supplied by the runner. Use reason 'real Herdr interactive verification' "
        "and evidence naming your agent and this phase.\n",
        encoding="utf-8",
    )

    try:
        result = execute_interactive_terminal(
            step=Step(id=f"live-{agent}-{phase}", skill="verification", agent=agent),
            prompt_path=prompt_path,
            verdict_path=verdict_path,
            workdir=workdir,
            timeout=_TIMEOUT_SECONDS,
            session_id=session_id,
            close_on_verdict=True,
            backend="herdr",
        )
    except (CLIExecutionError, StepTimeoutError, OSError, ValueError) as error:
        return (
            {
                "agent": agent,
                "phase": phase,
                "status": "FAIL",
                "error_type": type(error).__name__,
                "error": str(error),
                "verdict_present": verdict_path.is_file(),
                "terminal_log_present": (attempt_dir / "terminal.log").is_file(),
                "metadata_present": (attempt_dir / "pane-metadata.json").is_file(),
            },
            None,
        )

    verdict = yaml.safe_load(verdict_path.read_text(encoding="utf-8"))
    metadata = json.loads((attempt_dir / "pane-metadata.json").read_text(encoding="utf-8"))
    terminal_log = (attempt_dir / "terminal.log").read_text(encoding="utf-8")
    result_session_id = result.session_id
    evidence = {
        "agent": agent,
        "phase": phase,
        "status": "PASS" if verdict.get("status") == "PASS" else "FAIL",
        "verdict_status": verdict.get("status"),
        "session_id_present": result_session_id is not None,
        "resume_session_matches": session_id is None or result_session_id == session_id,
        "terminal_log_chars": len(terminal_log),
        "metadata": {
            "backend": metadata.get("backend"),
            "pane_id": metadata.get("pane_id"),
            "origin_pane": metadata.get("origin_pane"),
            "marker_confirmed": metadata.get("marker_confirmed"),
            "transcript_available": metadata.get("transcript_available"),
            "transcript_revision": metadata.get("transcript_revision"),
            "transcript_truncated": metadata.get("transcript_truncated"),
        },
    }
    return evidence, result_session_id


def main() -> None:
    """Run fresh and supported resume checks without retaining agent transcripts."""
    if os.environ.get("HERDR_ENV") != "1" or not os.environ.get("HERDR_PANE_ID"):
        raise SystemExit("run only inside Herdr with HERDR_ENV=1 and HERDR_PANE_ID set")

    workdir = Path(__file__).resolve().parents[3]
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="kaji-live-herdr-real-agents-") as temporary_directory:
        artifacts_dir = Path(temporary_directory)
        fresh_sessions: dict[str, str] = {}
        for agent in _FRESH_AGENTS:
            evidence, session_id = _run_attempt(
                agent=agent,
                phase="fresh",
                artifacts_dir=artifacts_dir,
                workdir=workdir,
            )
            results.append(evidence)
            if session_id is not None:
                fresh_sessions[agent] = session_id

        for agent in _RESUME_AGENTS:
            session_id = fresh_sessions.get(agent)
            if session_id is None:
                results.append(
                    {
                        "agent": agent,
                        "phase": "resume",
                        "status": "NOT_RUN",
                        "reason": "fresh attempt did not return a session ID",
                    }
                )
                continue
            evidence, _ = _run_attempt(
                agent=agent,
                phase="resume",
                artifacts_dir=artifacts_dir,
                workdir=workdir,
                session_id=session_id,
            )
            results.append(evidence)

    print(json.dumps({"results": results}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
