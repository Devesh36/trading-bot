"""Local process exclusion using native Windows or POSIX file locks."""

import errno
import os
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class ProcessLock:
    def __init__(self, path):
        self.path = path
        self.file = None

    def __enter__(self):
        if self.file is not None:
            raise RuntimeError("ProcessLock is already acquired")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, "a+b")
        try:
            if os.name == "nt":
                # Always lock byte zero, including on an empty existing lock file.
                # Windows permits locking a region beyond the end of a file.
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise RuntimeError("Another bot process holds this database lock") from None
            raise
        return self

    def __exit__(self, *args):
        try:
            if os.name == "nt":
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self.file, fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.file = None
