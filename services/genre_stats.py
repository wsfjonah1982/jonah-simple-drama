"""How often each genre has been picked, kept in _data/genre_counts.json.

Lives in _data/, outside _project/, so deleting projects does not reset it. The create page orders its genre cards by
these counts, most picked first (ties keep the order in genres.GENRES).
"""
import json
import os
from pathlib import Path

try:
    import fcntl  # Linux (the server). Absent on Windows, where locking is skipped for local dev.
except ImportError:
    fcntl = None

BASE_DIR = Path(__file__).resolve().parent.parent  # the project root, one level above services/
STATS_PATH = BASE_DIR / "_data" / "genre_counts.json"

# Starting order when the file does not exist yet: visitors' finished videos on the first day online (2026-10-01).
SEED_COUNTS = {"scifi": 3, "revenge": 2, "wuxia": 2, "palace": 2, "fantasy": 1}


def load():
    try:
        return {k: int(v) for k, v in json.loads(STATS_PATH.read_text(encoding="utf-8")).items()}
    except FileNotFoundError:
        return dict(SEED_COUNTS)
    except (OSError, ValueError, AttributeError):
        return {}


def record(genre_id):
    """Add one pick. Locked so two gunicorn workers never lose an update; never raises."""
    try:
        STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with (STATS_PATH.parent / ".genre_counts.lock").open("w") as lock:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_EX)
            counts = load()
            counts[genre_id] = counts.get(genre_id, 0) + 1
            tmp = STATS_PATH.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(counts, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, STATS_PATH)
    except OSError:
        pass


def sort_genres(genre_list):
    """Most picked first; sorted() is stable, so ties keep the given order."""
    counts = load()
    return sorted(genre_list, key=lambda g: -counts.get(g["id"], 0))
