"""Run the Herdr backend end-to-end against the stateful fake CLI."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from kaji_harness.interactive_terminal import execute_interactive_terminal
from kaji_harness.models import Step


def main() -> None:
    """Exercise subprocess, JSON, artifact, marker, snapshot, and close paths."""
    script_dir = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="kaji-herdr-fake-") as temporary_directory:
        temp_path = Path(temporary_directory)
        state_path = temp_path / "state.json"
        prompt_path = temp_path / "prompt.txt"
        verdict_path = temp_path / "verdict.yaml"
        prompt_path.write_text("write the verdict artifact", encoding="utf-8")

        os.environ["PATH"] = f"{script_dir}:{os.environ['PATH']}"
        os.environ["HERDR_BIN_PATH"] = str(script_dir / "herdr")
        os.environ["HERDR_ENV"] = "1"
        os.environ["HERDR_PANE_ID"] = "w1:p1"
        os.environ["KAJI_FAKE_HERDR"] = "1"
        os.environ["KAJI_FAKE_HERDR_STATE"] = str(state_path)

        result = execute_interactive_terminal(
            step=Step(id="fake-herdr", skill="fake", agent="claude"),
            prompt_path=prompt_path,
            verdict_path=verdict_path,
            workdir=temp_path,
            timeout=10,
            backend="herdr",
        )

        state = json.loads(state_path.read_text(encoding="utf-8"))
        metadata = json.loads((temp_path / "pane-metadata.json").read_text(encoding="utf-8"))
        split_calls = [call for call in state["calls"] if call[:2] == ["pane", "split"]]
        split_path_preserved = (
            len(split_calls) == 1 and f"PATH={os.environ['PATH']}" in split_calls[0]
        )
        summary = {
            "session_id_present": result.session_id is not None,
            "verdict_present": verdict_path.is_file(),
            "terminal_log": (temp_path / "terminal.log").read_text(encoding="utf-8"),
            "metadata": metadata,
            "fake_closed": state["closed"],
            "call_count": len(state["calls"]),
            "split_path_preserved": split_path_preserved,
        }
        assert summary["session_id_present"] is True
        assert summary["verdict_present"] is True
        assert summary["fake_closed"] is True
        assert summary["split_path_preserved"] is True
        assert metadata["marker_confirmed"] is True
        assert metadata["transcript_truncated"] is None
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
