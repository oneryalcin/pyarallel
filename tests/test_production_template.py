"""The production template's interrupt/resume contract, as a subprocess test.

Production bug this prevents — and it already shipped once: the
template's graceful-shutdown path (SIGINT/SIGTERM → drain → signal exit
code → rerun resumes) crashed with an ``AssertionError`` when actually
used, and its "rerun to resume" message pointed at a checkpoint in a
``TemporaryDirectory`` that was deleted on exit. A clean-run CI check
can never see either regression — only sending a real signal to a real
process and rerunning it can.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).parent.parent / "examples" / "07_production_api_job.py"


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX signal delivery to a child process"
)
def test_sigint_keeps_checkpoint_and_rerun_resumes(tmp_path: Path) -> None:
    ckpt = tmp_path / "e07.ckpt"
    env = {
        **os.environ,
        "DEMO_PORT": str(8990 + os.getpid() % 900),  # parallel-run safe
        "DEMO_CKPT": str(ckpt),
        "PYTHONUNBUFFERED": "1",  # progress lines must arrive live
    }
    cmd = [sys.executable, str(EXAMPLE)]

    # Run 1: interrupt at the first deterministic progress marker.
    child = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, env=env)
    assert child.stdout is not None
    completed_at_cancel = None
    interrupted = False
    for line in child.stdout:
        if not interrupted and "progress: 20/60" in line:
            child.send_signal(signal.SIGINT)
            interrupted = True
        match = re.search(r"cancelled\D+(\d+) completed", line)
        if match:
            completed_at_cancel = int(match.group(1))
    returncode = child.wait(timeout=60)

    assert interrupted, "the run finished before the interrupt marker"
    assert returncode == 130, "graceful interrupt must exit 128+SIGINT, not crash"
    assert ckpt.exists(), "the checkpoint must survive the interrupt"
    assert completed_at_cancel is not None and 20 <= completed_at_cancel < 60

    # Run 2: the SAME command must resume — only the remainder may hit
    # the server — then clean up and exit 0.
    rerun = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, env=env, timeout=60)
    assert rerun.returncode == 0, rerun.stdout
    assert "resuming a previously interrupted run" in rerun.stdout
    served = re.search(r"server served (\d+) OK calls", rerun.stdout)
    assert served is not None, rerun.stdout
    assert int(served.group(1)) == 60 - completed_at_cancel, (
        "a resumed run must not re-spend checkpointed calls"
    )
    assert not ckpt.exists(), "a completed run must clean up its checkpoint"
