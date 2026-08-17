"""Read-side probe of the pipeline mutex (ADR-052).

``pipeline.sh`` holds a kernel BSD flock (``LOCK_EX``) on ``.pipeline.flock``
for the lifetime of its process tree. Observers ask "is a pipeline running?"
by probing the same file with a non-blocking shared lock: if the exclusive
lock is held the probe fails, otherwise it succeeds and is released
immediately. The probe never reads process metadata, so there is no liveness
heuristic to drift (the class of failure that ADR-052 removed).
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from . import db

DEFAULT_PIPELINE_LOCK_PATH = db.PROJECT_ROOT / ".pipeline.flock"


def pipeline_lock_is_held(lock_path: str | Path = DEFAULT_PIPELINE_LOCK_PATH) -> bool | None:
    """Probe the pipeline mutex without participating in it.

    Returns ``True`` when a pipeline process tree holds the exclusive lock,
    ``False`` when the lock is free (including when the anchor file does not
    exist yet — a pipeline that never ran is not running), and ``None`` when
    the probe itself failed and the state is unknown.
    """
    try:
        fd = os.open(os.fspath(lock_path), os.O_RDONLY)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return None
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)
