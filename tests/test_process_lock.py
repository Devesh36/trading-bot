import subprocess
import sys
from pathlib import Path

import pytest

from process_lock import ProcessLock


ROOT = Path(__file__).resolve().parents[1]
PROBE = """
import sys
from process_lock import ProcessLock
try:
    with ProcessLock(sys.argv[1]):
        pass
except RuntimeError:
    sys.exit(23)
"""
HOLDER = """
import sys
from process_lock import ProcessLock
with ProcessLock(sys.argv[1]):
    print('locked', flush=True)
    sys.stdin.read()
"""


def probe(path):
    return subprocess.run(
        [sys.executable, "-c", PROBE, str(path)],
        cwd=ROOT, capture_output=True, text=True, timeout=10, check=False,
    )


def test_lock_excludes_other_process_and_releases_after_exception(tmp_path):
    path = tmp_path / "nested" / "bot.db.lock"
    with pytest.raises(ValueError, match="body failed"):
        with ProcessLock(path):
            result = probe(path)
            assert result.returncode == 23, result.stderr
            raise ValueError("body failed")
    assert probe(path).returncode == 0


@pytest.mark.parametrize("terminate", [False, True])
def test_lock_released_when_holder_exits(tmp_path, terminate):
    path = tmp_path / "bot.db.lock"
    # Startup must work with a zero-byte lock file left by an earlier run.
    path.touch()
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(path)], cwd=ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert probe(path).returncode == 23
        if terminate:
            holder.terminate()
        holder.communicate(timeout=10)
        assert probe(path).returncode == 0
    finally:
        if holder.poll() is None:
            holder.kill()
        holder.communicate(timeout=10)
