"""Anonymous usage data for refining the app, appended to _data/events.jsonl (one JSON object per line).

_data/ sits next to _project/ but is never cleaned up with it, so the history survives deleting videos.
Nothing personal is written: no name, email, IP address, photo, prompt or story text, so the file can
be published. Each line has `t` (local time), `event` and the fields below. `ref` is a one-way hash of the
project id (`ref_for(id)`): the id is also the URL of the visitor's video and photo, so it is never stored,
but a line can still be matched to its _project/<id>/ folder while that folder exists.

    created    ref, genre, plot (bool: the visitor typed a story idea), device ("mobile"/"desktop"), models
    rejected   reason ("daily_limit"), genre
    finished   ref, genre, status ("succeeded"/"failed"), stage + code (failures), timing, tokens, video_mb
    email      ref, genre, status ("sent"/"failed"), attempts, code (failures)
    liked      ref, genre, liked (bool)

`python -m services.data_report` summarises the file.
"""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

try:
    import fcntl  # Linux (the server). Absent on Windows, where locking is skipped for local dev.
except ImportError:
    fcntl = None

BASE_DIR = Path(__file__).resolve().parent.parent  # the project root, one level above services/
DATA_DIR = BASE_DIR / "_data"
EVENTS_PATH = DATA_DIR / "events.jsonl"


def device(user_agent):
    return "mobile" if any(k in (user_agent or "") for k in ("Mobi", "Android", "iPhone", "iPad")) else "desktop"


def ref_for(project_id):
    """Short one-way hash of a project id: matches a folder, but can't be turned back into a video URL."""
    return hashlib.sha256(project_id.encode()).hexdigest()[:12]


def record(event, **fields):
    """Append one event (an `id` field is stored as its `ref`). Locked so lines from two gunicorn workers
    never interleave; never raises."""
    if "id" in fields:
        fields = {"ref": ref_for(fields.pop("id")), **fields}
    line = json.dumps({"t": datetime.now().isoformat(timespec="seconds"), "event": event, **fields},
                      ensure_ascii=False, default=str) + "\n"
    try:
        EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_PATH.open("a", encoding="utf-8") as f:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_EX)
            f.write(line)
    except OSError:
        pass


def load():
    """Every event in the file, oldest first; unreadable lines are skipped."""
    events = []
    try:
        with EVENTS_PATH.open(encoding="utf-8") as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return events


def finished_fields(job):
    """The anonymous outcome of a finished project: status, failure stage/code, timings and token counts."""
    timing = job.get("timing") or {}
    detail = job.get("error_detail") or {}
    fields = {
        "id": job["id"],
        "genre": job["genre"],
        "status": job["status"],
        "timing": {k: timing[k] for k in ("image_api_s", "queue_s", "ark_total_s", "total_s") if k in timing},
        "image_tokens": (job.get("image_usage") or {}).get("tokens_out"),
        "video_tokens": (job.get("usage") or {}).get("completion_tokens"),
    }
    if job["status"] == "failed":
        fields.update(stage=detail.get("stage"), code=detail.get("code") or detail.get("status"),
                      elapsed_s=round((datetime.now() - datetime.fromisoformat(job["created_at"])).total_seconds(), 1))
    if job.get("video"):
        fields["video_mb"] = round((job["video"].get("bytes") or 0) / 1e6, 1)
    return fields
