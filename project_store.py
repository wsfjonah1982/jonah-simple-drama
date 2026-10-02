"""One folder per generation under _project/<id>/:

    job.json        current state, timings, token usage, error detail (source of truth)
    log.jsonl       append-only event timeline (created, submitted, status changes, errors, ...)
    prompt.txt      the exact prompt sent to Seedance
    character_prompt.txt  the exact prompt sent to Seedream (character sheet from the photo)
    assets/photo.*  the uploaded face photo
    assets/character.*  the Seedream character sheet that anchors the video
    ark_image.json  Seedream's response (signed URL redacted)
    ark_task.json   the final Ark task response (signed URLs redacted), written once it finishes
    video.mp4       the generated video
"""
import hashlib
import json
import os
import re
import secrets
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import uploads_store

try:
    import fcntl  # Linux (the server). Absent on Windows, where locking is skipped for local dev.
except ImportError:
    fcntl = None

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR / "_project"

_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{12}$")


def is_valid_id(project_id):
    return bool(_ID_RE.match(project_id or ""))


def project_dir(project_id):
    return PROJECT_DIR / project_id


def video_path(project_id):
    return project_dir(project_id) / "video.mp4"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def log(project_id, event, **fields):
    """Append one event line to the project's log.jsonl."""
    entry = {"t": datetime.now().isoformat(timespec="milliseconds"), "event": event, **fields}
    with (project_dir(project_id) / "log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def write_json(project_id, name, data):
    """Write atomically so two gunicorn workers never read a half-written file."""
    path = project_dir(project_id) / name
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def is_liked(project_id):
    return (project_dir(project_id) / "liked").exists()


def set_liked(project_id, liked):
    """The like label is a marker file, so it never races with the pipeline rewriting job.json."""
    marker = project_dir(project_id) / "liked"
    if liked == marker.exists():
        return
    if liked:
        marker.touch()
    else:
        marker.unlink(missing_ok=True)
    log(project_id, "liked" if liked else "unliked")


def save(job):
    write_json(job["id"], "job.json", job)


def load(project_id):
    if not is_valid_id(project_id):
        return None
    try:
        return json.loads((project_dir(project_id) / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def create(genre_id, plot, photo_data_url, prompt, character_prompt, params, client, notify=None):
    """Make the project folder, keep the photo as an asset, and record the request.

    Raises ValueError if the photo is not a valid image (nothing is left behind in that case).
    """
    project_id = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(6)}"
    root = project_dir(project_id)
    (root / "assets").mkdir(parents=True)
    try:
        photo = uploads_store.save_image(photo_data_url, root / "assets")
    except ValueError:
        for p in (root / "assets", root):
            p.rmdir()
        raise
    (root / "prompt.txt").write_text(prompt, encoding="utf-8")
    (root / "character_prompt.txt").write_text(character_prompt, encoding="utf-8")

    job = {
        "id": project_id,
        "created_at": _now(),
        "genre": genre_id,
        "plot": plot,
        "photo": f"assets/{photo['name']}",
        "photo_bytes": photo["bytes"],
        "params": params,
        "task_id": None,
        "status": "image_generating",
        "error": None,
        "error_detail": None,
        "timing": {},
        "usage": None,
        "image_usage": None,
        "character": None,
        "video": None,
        "notify": notify,  # {"name", "email"} for the "video ready" email; never exposed by the API
    }
    save(job)
    log(project_id, "created", genre=genre_id, plot=plot, photo=job["photo"],
        photo_bytes=photo["bytes"], prompt_chars=len(prompt), character_prompt_chars=len(character_prompt),
        params=params, client=client, notify=bool(notify))
    return job


def list_projects(ids=None, limit=200):
    """job.json of the newest projects first (folder names start with the timestamp).

    ids: optional set of project ids to restrict the list to.
    """
    if not PROJECT_DIR.exists():
        return []
    names = sorted((p.name for p in PROJECT_DIR.iterdir() if is_valid_id(p.name)), reverse=True)
    jobs = []
    for name in names:
        if ids is not None and name not in ids:
            continue
        job = load(name)
        if job:
            jobs.append(job)
            if len(jobs) >= limit:
                break
    return jobs


def photo_path(project_id):
    """The project's uploaded photo (assets/photo.*), or None."""
    if not is_valid_id(project_id):
        return None
    return next(iter(sorted((project_dir(project_id) / "assets").glob("photo.*"))), None)


def list_photos(ids=None, limit=60):
    """Distinct uploaded photos, newest first, for the photo picker.

    The same photo is often re-used for several generations; identical files are listed once, at the
    newest project that has it, with a count of how many projects used it.
    """
    found = {}
    for job in list_projects(ids, limit=200):
        path = photo_path(job["id"])
        if not path:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in found:
            found[digest]["uses"] += 1
            continue
        found[digest] = {"id": job["id"], "created_at": job["created_at"], "genre": job["genre"], "uses": 1}
    return list(found.values())[:limit]


def count_created_today(reset_at=None):
    """Every project costs money once submitted, so failures count toward the daily limit too.

    reset_at: optional ISO time ("2026-09-30T10:05:00"); projects created before it don't count.
    A value from an earlier day changes nothing, so a reset expires by itself at midnight.
    """
    today = datetime.now().date().isoformat()
    if not PROJECT_DIR.exists():
        return 0
    count = 0
    for p in PROJECT_DIR.glob("*/job.json"):
        try:
            created = json.loads(p.read_text(encoding="utf-8")).get("created_at", "")
            if created.startswith(today) and (not reset_at or created >= reset_at):
                count += 1
        except (OSError, ValueError):
            continue
    return count


@contextmanager
def try_lock(project_id):
    """Yield True if this worker got the project's lock, False if another worker holds it.

    Stops two gunicorn workers polling the same project from double-downloading or double-logging.
    """
    if fcntl is None:
        yield True
        return
    with (project_dir(project_id) / ".lock").open("w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
