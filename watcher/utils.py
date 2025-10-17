import os
import time
import logging
from pathlib import Path

# Optional: use pikepdf to probe readiness; fallback to size-stable check if unavailable
try:  # pragma: no cover - optional
    import pikepdf  # type: ignore

    HAVE_PIKEPDF = True
except Exception:  # pragma: no cover - optional
    HAVE_PIKEPDF = False


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def wait_for_file_ready(
    path: Path, use_polling: bool, retries: int = 30, sleep_s: float = 0.5
) -> bool:
    """Wait until a file is fully written and ready to read.

    Ready when either:
      - pikepdf can open it (preferred), or
      - size is stable across two checks and it exists.
    """
    last_size = -1
    stable_count = 0

    for _ in range(max(1, retries)):
        if not path.exists():
            time.sleep(sleep_s)
            continue

        # Try opening with pikepdf if available
        if HAVE_PIKEPDF:
            try:
                with pikepdf.open(str(path)):
                    return True
            except Exception:
                # Not ready yet; fall back to size check
                pass

        # Size-stable check
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = -1
        if size > 0 and size == last_size:
            stable_count += 1
            if stable_count >= 2:  # two consecutive stable checks
                return True
        else:
            stable_count = 0

        last_size = size
        time.sleep(sleep_s)

    return path.exists()
