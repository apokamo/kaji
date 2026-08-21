"""Run a guarded real-Herdr lifecycle smoke with a fake coding agent."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from kaji_harness.interactive_terminal import execute_interactive_terminal
from kaji_harness.models import Step


def _parse_args() -> argparse.Namespace:
    """Parse the optional retained-pane verification mode."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--retain",
        action="store_true",
        help="leave the response-created pane open for explicit ownership verification",
    )
    return parser.parse_args()


def main() -> None:
    """Create and optionally retain one owned real pane from an explicit Herdr caller."""
    args = _parse_args()
    if os.environ.get("HERDR_ENV") != "1" or not os.environ.get("HERDR_PANE_ID"):
        raise SystemExit("run only inside Herdr with HERDR_ENV=1 and HERDR_PANE_ID set")

    script_dir = Path(__file__).resolve().parent
    fake_bin = script_dir / "fake-bin"
    os.environ["PATH"] = f"{fake_bin}:{os.environ['PATH']}"

    with tempfile.TemporaryDirectory(prefix="kaji-live-herdr-") as temporary_directory:
        temp_path = Path(temporary_directory)
        prompt_path = temp_path / "prompt.txt"
        verdict_path = temp_path / "verdict.yaml"
        prompt_path.write_text("Run the guarded live Herdr fake-agent smoke.", encoding="utf-8")

        result = execute_interactive_terminal(
            step=Step(id="live-herdr-fake", skill="fake", agent="claude"),
            prompt_path=prompt_path,
            verdict_path=verdict_path,
            workdir=temp_path,
            timeout=30,
            backend="herdr",
            close_on_verdict=not args.retain,
        )

        metadata = json.loads((temp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        summary = {
            "origin_pane": os.environ["HERDR_PANE_ID"],
            "created_pane": metadata["pane_id"],
            "kaji_run": metadata["kaji_run"],
            "retained": args.retain,
            "session_id_present": result.session_id is not None,
            "verdict": verdict_path.read_text(encoding="utf-8"),
            "terminal_log": (temp_path / "terminal.log").read_text(encoding="utf-8"),
            "metadata": metadata,
        }
        assert metadata["backend"] == "herdr"
        assert metadata["marker_confirmed"] is True
        assert metadata["transcript_available"] is True
        assert verdict_path.is_file()
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
