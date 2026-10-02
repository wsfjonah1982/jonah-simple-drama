import hmac
import os
import threading
import time
import urllib.request
from datetime import datetime

from flask import Flask, render_template, request, jsonify, url_for, abort, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from services import data_log, genre_stats, genres, mailer, project_store, seedance, seedream, uploads_store

app = Flask(__name__)
# Behind nginx the app is served under /drama/. nginx sends X-Forwarded-Prefix, and ProxyFix turns it
# into SCRIPT_NAME so url_for() and request.script_root include the prefix. Locally there is no prefix.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

MAX_IMAGE_DATA_URL_LEN = 8 * 1024 * 1024
JOB_TIMEOUT_SECONDS = 90 * 60
# The two pipeline stages run in a background thread. The Seedream call is capped at 5 min, so a project
# still in one of these statuses after 10 min lost its thread (e.g. the service restarted mid-run).
PIPELINE_STATUSES = ("image_generating", "submitting")
STAGE_STALL_SECONDS = 10 * 60
EMAIL_RETRY_DELAYS = (0, 120, 600)  # seconds before each delivery attempt; 4xx and network errors are retried
FAILED_STATUSES = ("failed", "expired", "cancelled")


@app.context_processor
def static_helpers():
    def static_v(filename):
        """static URL with the file's mtime appended, so browsers refetch it after a deploy."""
        try:
            version = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
        except OSError:
            version = 0
        return url_for("static", filename=filename, v=version)

    return {"static_v": static_v}


@app.route("/")
def index():
    cfg = seedance.get_config()
    return render_template(
        "index.html",
        genres=genre_stats.sort_genres(genres.GENRES),
        genre_thumbs={g["id"] for g in genres.GENRES
                      if os.path.exists(os.path.join(app.static_folder, "img", "genres", f"{g['id']}.jpg"))},
        max_plot_len=genres.MAX_PLOT_LEN,
        needs_code=bool(cfg.get("access_code")),
        duration=cfg["duration"],
        ratio=cfg["ratio"],
        resolution=cfg["resolution"],
    )


@app.route("/watch/<job_id>")
def watch(job_id):
    job = project_store.load(job_id)
    if not job:
        abort(404)
    genre = genres.get_genre(job["genre"]) or {}
    return render_template("watch.html", job_id=job_id, genre_title=genre.get("title", "Your drama"))


@app.route("/videos")
def videos():
    titles = {g["id"]: g["title"] for g in genres.GENRES}
    return render_template("videos.html", titles=titles)


@app.route("/media/<job_id>.mp4")
def media(job_id):
    if not project_store.is_valid_id(job_id) or not project_store.video_path(job_id).exists():
        abort(404)
    return send_from_directory(project_store.project_dir(job_id), "video.mp4", mimetype="video/mp4")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _seconds_since(iso):
    return (datetime.now() - datetime.fromisoformat(iso)).total_seconds()


def _video_params(cfg):
    return {
        "image_model": cfg["image_model_id"],
        "image_size": cfg["image_size"],
        "image_style": cfg["image_style"],
        "model": cfg["video_model_id"],
        "duration": cfg["duration"],
        "ratio": cfg["ratio"],
        "resolution": cfg["resolution"],
        "generate_audio": cfg["generate_audio"],
        "watermark": cfg["watermark"],
        "image_role": cfg.get("image_role") or None,
        "face_from_asset_library": bool((cfg.get("reference_asset_id") or "").strip()),
    }


@app.route("/api/generate", methods=["POST"])
def generate():
    cfg = seedance.get_config()
    payload = request.get_json(silent=True) or {}

    code = cfg.get("access_code") or ""
    if code and not hmac.compare_digest(str(payload.get("access_code") or ""), code):
        return jsonify({"error": "Wrong access code."}), 403

    genre = genres.get_genre(payload.get("genre"))
    if not genre:
        return jsonify({"error": "Please choose a drama genre."}), 400
    if payload.get("consent") is not True:
        return jsonify({"error": "Please confirm the photo shows you, or that you have the person's permission."}), 400

    name = mailer.clean_name(payload.get("name"))
    if not name:
        return jsonify({"error": "Please enter your name."}), 400
    email = mailer.clean_email(payload.get("email"))
    if not email:
        return jsonify({"error": "Please enter a valid email address, like name@example.com."}), 400

    image = payload.get("image") or ""
    if not image.startswith("data:image") or len(image) > MAX_IMAGE_DATA_URL_LEN:
        return jsonify({"error": "Please take or upload a face photo first."}), 400

    if project_store.count_created_today(cfg.get("daily_limit_reset_at")) >= cfg.get("daily_limit", 10):
        data_log.record("rejected", reason="daily_limit", genre=genre["id"])
        return jsonify({"error": "Today's video limit has been reached. Please try again tomorrow."}), 429

    plot = genres.clean_plot(payload.get("plot"))
    style = cfg["image_style"]
    prompt = genres.build_prompt(genre, plot, cfg["duration"], cfg["ratio"], style)
    character_prompt = genres.build_character_prompt(genre, style)
    client = {"ip": request.remote_addr, "user_agent": request.headers.get("User-Agent", "")[:200]}
    try:
        # Creates _project/<id>/ with the photo as an asset and the "created" log line.
        job = project_store.create(genre["id"], plot, image, prompt, character_prompt, _video_params(cfg), client,
                                   notify={"name": name, "email": email})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    genre_stats.record(genre["id"])
    data_log.record("created", id=job["id"], genre=genre["id"], plot=bool(plot), device=data_log.device(client["user_agent"]),
                    image_model=cfg["image_model_id"], video_model=cfg["video_model_id"])

    # Seedream can take minutes, so the two steps run in the background and the page follows along in My videos.
    spawn(_run_pipeline, job["id"])
    return jsonify({"job_id": job["id"]})


def spawn(fn, *args):
    """Run fn in a background thread (the tests swap this for a direct call)."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def _run_pipeline(project_id):
    """Step 1: Seedream turns the photo into a character sheet. Step 2: Seedance films the drama from it."""
    job = project_store.load(project_id)
    try:
        _image_stage(job)
        if job["status"] == "submitting":
            _video_stage(job)
    except Exception as exc:  # a bug must not leave the project spinning forever
        _fail(job, "internal", "Something went wrong on the server. Please try again.", seedance.describe_error(exc))


def _image_stage(job):
    """Seedream 5.0 Pro: photo + character-sheet prompt -> assets/character.*. Leaves status 'submitting' on success."""
    pid, root = job["id"], project_store.project_dir(job["id"])
    if job["params"]["face_from_asset_library"]:
        # A registered real-human asset is sent to Seedance as it is, so there is nothing to draw first.
        job["status"] = "submitting"
        project_store.save(job)
        project_store.log(pid, "image_skipped", reason="reference_asset_id is set: using the asset library face")
        return

    cfg = seedance.get_config()
    project_store.log(pid, "image_started", model=cfg["image_model_id"], size=cfg["image_size"],
                      style=cfg["image_style"])
    started = time.monotonic()
    try:
        result = seedream.generate_image(uploads_store.file_to_data_url(root / job["photo"]),
                                         (root / "character_prompt.txt").read_text(encoding="utf-8"))
        api_s = round(time.monotonic() - started, 2)
        saved = seedream.download(result["url"], root / "assets" / "character")
    except Exception as exc:
        detail = seedance.describe_error(exc)
        job.setdefault("timing", {})["image_api_s"] = round(time.monotonic() - started, 2)
        _fail(job, "image", seedance.friendly_error(exc, "image"), detail)
        return

    timing = job.setdefault("timing", {})
    timing["image_api_s"] = api_s
    timing["image_total_s"] = round(time.monotonic() - started, 2)
    usage = {**seedream.extract_token_usage(result["usage"]), "raw": result["usage"]}
    job.update(
        status="submitting",
        image_usage=usage,
        character={"file": f"assets/{os.path.basename(saved['path'])}", "bytes": saved["bytes"],
                   "width": saved["width"], "height": saved["height"]},
    )
    project_store.write_json(pid, "ark_image.json", result["raw"])
    project_store.save(job)
    project_store.log(pid, "image_succeeded", image_api_s=api_s, image_total_s=timing["image_total_s"],
                      usage=usage, character=job["character"])


def _video_stage(job):
    """Seedance 2.5: the character sheet (or the asset-library face) is the reference image."""
    pid, root = job["id"], project_store.project_dir(job["id"])
    if job["character"]:
        reference = uploads_store.file_to_data_url(root / job["character"]["file"])
    else:
        reference = uploads_store.file_to_data_url(root / job["photo"])  # unused: create_task sends the asset id
    started = time.monotonic()
    try:
        job["task_id"] = seedance.create_task((root / "prompt.txt").read_text(encoding="utf-8"), reference)
    except Exception as exc:
        detail = seedance.describe_error(exc)
        job.setdefault("timing", {})["submit_api_s"] = round(time.monotonic() - started, 2)
        _fail(job, "submit", seedance.friendly_error(exc, "submit"), detail)
        return
    timing = job.setdefault("timing", {})
    timing["submit_api_s"] = round(time.monotonic() - started, 2)
    job.update(status="queued", submitted_at=_now())
    project_store.save(job)
    project_store.log(pid, "submitted", task_id=job["task_id"], submit_api_s=timing["submit_api_s"])


def _download(url, dest):
    """Ark result URLs expire after about a day, so keep our own copy of the mp4."""
    tmp = dest.with_suffix(f".{os.getpid()}.tmp")
    with urllib.request.urlopen(url, timeout=60) as resp, tmp.open("wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, dest)


def _fail(job, stage, friendly, detail, task=None):
    job.update(status="failed", error=friendly, error_detail={"stage": stage, **detail})
    if task:
        job["usage"] = task["usage"]
        project_store.write_json(job["id"], "ark_task.json", task["raw"])
    project_store.save(job)
    project_store.log(job["id"], "failed", error=job["error_detail"], friendly=friendly,
                      usage=job["usage"], elapsed_s=round(_seconds_since(job["created_at"]), 1))
    data_log.record("finished", **data_log.finished_fields(job))


def _ark_seconds(ts):
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds") if isinstance(ts, (int, float)) else None


def _finish_success(job, task):
    """Download the mp4 and record timings, token usage and video facts."""
    pid = job["id"]
    started = time.monotonic()
    try:
        _download(task["video_url"], project_store.video_path(pid))
    except Exception as exc:
        job["download_attempts"] = job.get("download_attempts", 0) + 1
        if job["download_attempts"] <= 3 or job["download_attempts"] % 20 == 0:  # don't flood the log
            project_store.log(pid, "download_failed", attempt=job["download_attempts"],
                              error=seedance.describe_error(exc))
        project_store.save(job)
        return  # the next poll retries; the Ark URL stays valid for about a day

    raw = task["raw"]
    timing = job.setdefault("timing", {})
    timing["download_s"] = round(time.monotonic() - started, 2)
    timing["total_s"] = round(_seconds_since(job["created_at"]), 1)  # click -> mp4 saved on our server
    if job.get("running_since"):
        timing["generation_observed_s"] = round(_seconds_since(job["running_since"]), 1)
    created, updated = raw.get("created_at"), raw.get("updated_at")
    if isinstance(created, (int, float)) and isinstance(updated, (int, float)):
        timing["ark_total_s"] = updated - created  # Ark's own clock: task created -> finished
        job["ark_finished_at"] = _ark_seconds(updated)

    job.update(
        status="succeeded",
        tos_url=task["video_url"],  # the signed BytePlus TOS link, emailed to the visitor; expires in about a day
        error=None,
        error_detail=None,
        usage=task["usage"],
        video={
            "file": "video.mp4",
            "bytes": project_store.video_path(pid).stat().st_size,
            "duration_s": raw.get("duration"),
            # Ark reports `framespersecond` but no frame count, so derive it (30 s x 24 fps = 720)
            "frames": raw.get("frames") or (
                raw["duration"] * raw["framespersecond"] if raw.get("duration") and raw.get("framespersecond") else None),
            "fps": raw.get("framespersecond"),
            "resolution": raw.get("resolution"),
            "ratio": raw.get("ratio"),
            "seed": raw.get("seed"),
        },
    )
    project_store.write_json(pid, "ark_task.json", raw)
    project_store.save(job)
    project_store.log(pid, "succeeded", usage=job["usage"], timing=timing, video=job["video"],
                      ark_finished_at=job.get("ark_finished_at"))
    data_log.record("finished", **data_log.finished_fields(job))
    if (job.get("notify") or {}).get("email") and seedance.get_config().get("mail_enabled", True):
        job["email"] = {"status": "queued"}
        project_store.save(job)
        spawn(_send_ready_email, pid)


def _send_ready_email(project_id):
    """Email the TOS link (via the Gmail relay, or direct to MX); retry 4xx/network errors, stop on 5xx."""
    cfg = seedance.get_config()
    job = project_store.load(project_id)
    notify, url = job.get("notify") or {}, job.get("tos_url")
    title = (genres.get_genre(job["genre"]) or {}).get("title", "Drama")
    sender = cfg["mail_from"]
    msg = mailer.build_message(sender, cfg.get("mail_from_name", "Drama Flow"), notify["name"], notify["email"],
                               title, url, mailer.tos_expiry(url))
    to = mailer.mask(notify["email"])
    for attempt, delay in enumerate(EMAIL_RETRY_DELAYS, 1):
        time.sleep(delay)
        try:
            password = seedance._load_json(seedance.CREDENTIAL_PATH).get("smtp_password")  # read per send: no restart to set it
            result = mailer.deliver(msg, notify["email"], cfg, password)
        except mailer.MailError as exc:
            final = exc.permanent or attempt == len(EMAIL_RETRY_DELAYS)
            project_store.log(project_id, "email_failed" if final else "email_retry", to=to, attempt=attempt,
                              error=exc.as_dict())
            if final:
                job["email"] = {"status": "failed", "at": _now(), "attempts": attempt, "error": exc.as_dict()}
                project_store.save(job)
                data_log.record("email", id=project_id, genre=job["genre"], status="failed", attempts=attempt,
                                code=exc.as_dict().get("code"))
                return
            continue
        except Exception as exc:  # a bug must not kill the thread silently
            job["email"] = {"status": "failed", "at": _now(), "attempts": attempt, "error": seedance.describe_error(exc)}
            project_store.save(job)
            project_store.log(project_id, "email_failed", to=to, attempt=attempt, error=job["email"]["error"])
            data_log.record("email", id=project_id, genre=job["genre"], status="failed", attempts=attempt, code="internal")
            return
        job["email"] = {"status": "sent", "at": _now(), "attempts": attempt, **result}
        project_store.save(job)
        project_store.log(project_id, "email_sent", to=to, attempt=attempt, **result)
        data_log.record("email", id=project_id, genre=job["genre"], status="sent", attempts=attempt)
        return


def _poll(job):
    """One poll of Ark for a running job: log status changes, then finish or fail the project."""
    pid = job["id"]
    age = _seconds_since(job["created_at"])
    try:
        task = seedance.get_task(job["task_id"])
    except Exception as exc:
        detail = seedance.describe_error(exc)
        if detail["message"] != job.get("last_poll_error"):  # log a repeating error once, not every poll
            job["last_poll_error"] = detail["message"]
            project_store.log(pid, "poll_error", error=detail)
        if age > JOB_TIMEOUT_SECONDS:
            _fail(job, "timeout", "Timed out waiting for the video service.", {"message": "gave up after "
                  f"{JOB_TIMEOUT_SECONDS // 60} min; last poll error: {detail['message']}"})
        else:
            project_store.save(job)  # passing network error: the next poll retries
        return

    job.pop("last_poll_error", None)
    status = task["status"]

    if status == "succeeded" and task["video_url"]:
        _finish_success(job, task)
    elif status in FAILED_STATUSES:
        detail = {"status": status, "code": task["error_code"], "message": task["error_message"]}
        _fail(job, "task", seedance.friendly_error(task["error"] or f"Video generation {status}."), detail, task)
    elif age > JOB_TIMEOUT_SECONDS:
        _fail(job, "timeout", "Timed out waiting for the video service.",
              {"message": f"still '{status}' after {JOB_TIMEOUT_SECONDS // 60} min"})
    else:
        status = status if status in ("queued", "running") else "running"
        if status != job["status"]:
            job["status"] = status
            log_fields = {"status": status, "elapsed_s": round(age, 1)}
            if status == "running" and not job.get("running_since"):
                job["running_since"] = _now()
                job.setdefault("timing", {})["queue_s"] = round(_seconds_since(job["submitted_at"]), 1)
                log_fields["queue_s"] = job["timing"]["queue_s"]
            project_store.log(pid, "status", **log_fields)
        project_store.save(job)


def _refresh(job):
    """Poll Ark for a running job and update the stored record."""
    if job["status"] in PIPELINE_STATUSES and _seconds_since(job["created_at"]) > STAGE_STALL_SECONDS:
        # The background thread died before finishing this stage (e.g. the service restarted mid-run).
        with project_store.try_lock(job["id"]) as got:
            job = project_store.load(job["id"]) or job
            if got and job["status"] in PIPELINE_STATUSES:
                stage = "image" if job["status"] == "image_generating" else "submit"
                _fail(job, stage, "The request was interrupted before the video was started. Please try again.",
                      {"message": f"stuck in '{job['status']}' for over {STAGE_STALL_SECONDS // 60} min, "
                                  "probably the server restarted mid-run"})
        return job
    if job["status"] not in ("queued", "running") or not job.get("task_id"):
        return job
    with project_store.try_lock(job["id"]) as got:
        if not got:
            return job  # another worker is polling this project right now
        job = project_store.load(job["id"]) or job  # it may have just finished in the other worker
        if job["status"] in ("queued", "running"):
            _poll(job)
    return job


@app.route("/api/jobs/<job_id>")
def job_status(job_id):
    job = project_store.load(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    job = _refresh(job)
    body = {
        "id": job["id"],
        "genre": job["genre"],
        "status": job["status"],
        "created_at": job["created_at"],
        "error": job.get("error"),
    }
    if job["status"] == "succeeded":
        body["video_url"] = url_for("media", job_id=job["id"])
    return jsonify(body)


def _summary(job):
    """What the My videos page may see: no photo, prompt, client IP, TOS link or raw API body.

    The visitor's name and email are shown (with the email's delivery state), as the site owner asked.
    """
    genre = genres.get_genre(job["genre"]) or {}
    item = {
        "id": job["id"],
        "genre": job["genre"],
        "genre_title": genre.get("title", "Drama"),
        "plot": job.get("plot") or "",
        "created_at": job["created_at"],
        "status": job["status"],
        "error": job.get("error"),
        "detail": None,
        "ark_total_s": (job.get("timing") or {}).get("ark_total_s"),
        "video": None,
        "liked": project_store.is_liked(job["id"]),
        "contact": None,
    }
    notify = job.get("notify")
    if notify:
        sent = job.get("email") or {}
        item["contact"] = {
            "name": notify.get("name"),
            "email": notify.get("email"),
            "email_status": sent.get("status"),  # None (video not done yet) / queued / sent / failed
            "email_at": sent.get("at"),
            "email_error": (sent.get("error") or {}).get("message"),
        }
    d = job.get("error_detail")
    if d:
        item["detail"] = {
            "stage": d.get("stage"),
            "code": d.get("code"),
            "status_code": d.get("status_code"),
            "request_id": d.get("request_id"),
            "message": str(d.get("message") or "")[:600],
        }
    if job["status"] == "succeeded" and project_store.video_path(job["id"]).exists():
        v = job.get("video") or {}
        item["video"] = {
            "url": url_for("media", job_id=job["id"]),
            "duration_s": v.get("duration_s"),
            "bytes": v.get("bytes"),
            "resolution": v.get("resolution"),
        }
    return item


def _history_ids():
    """None = every project; a set = only the ids this browser remembers (history_scope "browser")."""
    if seedance.get_config().get("history_scope", "all") == "browser":
        return {i for i in request.args.get("ids", "").split(",") if project_store.is_valid_id(i)}
    return None


@app.route("/api/photos")
def photos():
    """Photos uploaded in earlier generations, for the "Choose from gallery" picker."""
    titles = {g["id"]: g["title"] for g in genres.GENRES}
    return jsonify([
        {
            "id": p["id"],
            "created_at": p["created_at"],
            "genre_title": titles.get(p["genre"], "Drama"),
            "uses": p["uses"],
            "url": url_for("project_photo", project_id=p["id"]),
        }
        for p in project_store.list_photos(_history_ids())
    ])


@app.route("/asset/<project_id>/photo")
def project_photo(project_id):
    path = project_store.photo_path(project_id)
    if not path:
        abort(404)
    response = send_from_directory(path.parent, path.name)
    response.headers["Cache-Control"] = "private, max-age=3600"  # only behind the login; never in shared caches
    return response


@app.route("/api/projects")
def projects():
    """Every generation, newest first: passed, failed and in progress.

    history_scope "all" (default) lists every project on the server; "browser" lists only the ids
    this browser remembers (?ids=a,b,c), so visitors can't see each other's videos.
    """
    return jsonify([_summary(j) for j in project_store.list_projects(_history_ids())])


@app.route("/api/projects/<project_id>/like", methods=["POST"])
def like(project_id):
    """Set (not toggle) the like label, so a double click or a retry can't flip it back."""
    job = project_store.load(project_id)
    if not job:
        abort(404)
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("liked"), bool):
        return jsonify({"error": "Send {\"liked\": true} or {\"liked\": false}."}), 400
    if payload["liked"] != project_store.is_liked(project_id):  # count changes only, not repeated clicks
        data_log.record("liked", id=project_id, genre=job["genre"], liked=payload["liked"])
    project_store.set_liked(project_id, payload["liked"])
    return jsonify({"id": project_id, "liked": project_store.is_liked(project_id)})


if __name__ == "__main__":
    app.run(debug=True)
