import base64, json, struct, sys, tempfile, zlib
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from services import data_log, data_report, genre_stats, project_store, seedance, seedream, uploads_store
tmp = Path(tempfile.mkdtemp(prefix="drama_smoke_"))
project_store.PROJECT_DIR = tmp / "_project"
genre_stats.STATS_PATH = tmp / "_data" / "genre_counts.json"
data_log.EVENTS_PATH = tmp / "_data" / "events.jsonl"
# Tests never use the real config.json / credential.json: the example config plus fake keys.
seedance.CONFIG_PATH = PROJECT / "config.example.json"
seedance.CREDENTIAL_PATH = Path(tempfile.mkdtemp()) / "credential.json"
seedance.CREDENTIAL_PATH.write_text(json.dumps({"video_maas_api_key": "test-video-key", "image_maas_api_key": "test-image-key"}))
seedance.get_config().update(history_scope="all", mail_enabled=True, mail_from="sender@example.com")

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name, extra)
    if not cond: sys.exit(1)

# ---- a valid-enough PNG (signature + IHDR 1440x2560) so image_dimensions() has something to parse
def png(w, h):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")
CHARACTER_PNG = png(2560, 1440)
check("dimensions of png", seedream.image_dimensions(CHARACTER_PNG) == (2560, 1440))
jpg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\xff\xc0\x00\x11\x08\x05\x00\x02\xd0\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
check("dimensions of jpeg", seedream.image_dimensions(jpg) == (720, 1280), seedream.image_dimensions(jpg))
check("sniff types", seedream.sniff_type(CHARACTER_PNG)[1] == ".png" and seedream.sniff_type(jpg)[1] == ".jpg")

# ---- ImageServiceError splits the real API error body
body = json.dumps({"error": {"code": "InputImageSensitiveContentDetected.PrivacyInformation", "message": "may contain real person", "type": "BadRequest"}})
e = seedream.ImageServiceError(400, body, "req-img-1")
check("ImageServiceError fields", e.code == "InputImageSensitiveContentDetected.PrivacyInformation" and e.status_code == 400 and e.request_id == "req-img-1" and "real person" in str(e) and e.body["error"]["type"] == "BadRequest")
check("token usage normalised", seedream.extract_token_usage({"generated_images": 1, "output_tokens": 16384, "total_tokens": 16384}) == {"tokens_in": None, "tokens_out": 16384, "tokens_total": 16384})

# ---- stubs
seen = {"seedream": [], "seedance": []}
img_state = {"raise": None}
def fake_generate(photo_url, prompt):
    seen["seedream"].append((photo_url, prompt))
    if img_state["raise"]: raise img_state["raise"]
    return {"url": "http://img.example/c.png?sig=SECRET", "usage": {"generated_images": 1, "output_tokens": 16384, "total_tokens": 16384},
            "raw": {"model": "dola-seedream-5-0-pro-260628", "data": [{"url": "<redacted>"}], "usage": {"output_tokens": 16384}}}
def fake_download(url, dest_no_ext):
    p = Path(str(dest_no_ext) + ".png"); p.write_bytes(CHARACTER_PNG)
    return {"path": str(p), "bytes": len(CHARACTER_PNG), "width": 2560, "height": 1440, "mime": "image/png"}
seedream.generate_image, seedream.download = fake_generate, fake_download

class ApiError(Exception):
    code = "InputImageSensitiveContentDetected.PrivacyInformation"; status_code = 400; request_id = "req-abc123"; body = {"error": {"code": "x"}}
def fake_create(prompt, image):
    seen["seedance"].append((prompt, image)); return "task-123"
seedance.create_task = fake_create

state = {}
def fake_get(task_id):
    if state.get("raise"): raise ConnectionError(state["raise"])
    st = state["status"]; err = state.get("err")
    raw = {"id": task_id, "status": st, "content": {"video_url": "<redacted>" if st == "succeeded" else ""}, "usage": state.get("usage"),
           "created_at": 1789950000, "updated_at": 1789950000 + 187, "duration": 30, "frames": 720, "framespersecond": 24,
           "resolution": "720p", "ratio": "9:16", "seed": 42, "revised_prompt": "rp", "error": err}
    return {"status": st, "video_url": "http://example/x.mp4?sig=SECRET" if st == "succeeded" else None,
            "error": f"{err['code']}: {err['message']}" if err else None, "error_code": err and err["code"], "error_message": err and err["message"],
            "usage": state.get("usage"), "created_at": raw["created_at"], "updated_at": raw["updated_at"], "raw": raw}
seedance.get_task = fake_get

import app as appmod
appmod.spawn = lambda fn, *a: fn(*a)      # run the background pipeline inline
dl = {"fail": 0}
def fake_dl(url, dest):
    if dl["fail"]: dl["fail"] -= 1; raise OSError("connection reset")
    dest.write_bytes(b"\x00\x00\x00 ftypmp42fake")
appmod._download = fake_dl
mails = []                                 # (rcpt, message, sender, mode) per delivery; mail_state scripts failures
mail_state = {"errors": []}
def fake_deliver(msg, rcpt, cfg, password):
    if mail_state["errors"]: raise mail_state["errors"].pop(0)
    mails.append((rcpt, msg, cfg["mail_from"], cfg.get("mail_mode"))); return {"mx": "mx.example.com", "tls": True, "response": "250 ok"}
real_send, real_deliver = appmod.mailer.send, appmod.mailer.deliver
appmod.mailer.deliver = fake_deliver
appmod.EMAIL_RETRY_DELAYS = (0, 0, 0)

jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 64
img = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
c = appmod.app.test_client()
post = lambda body: c.post("/api/generate", json={"name": "Ada Lee", "email": "ada@example.com", **body}, environ_base={"REMOTE_ADDR": "1.2.3.4"}, headers={"User-Agent": "TestUA/1"})
def events(pid): return [json.loads(l) for l in (project_store.project_dir(pid) / "log.jsonl").read_text().splitlines()]
def job(pid): return json.loads((project_store.project_dir(pid) / "job.json").read_text())

# ---- pages + validation
for path in ["/", "/videos"]: check(f"GET {path}", c.get(path).status_code == 200)
check("prefix in links", b'href="/drama/videos"' in c.get("/", headers={"X-Forwarded-Prefix": "/drama"}).data)
check("no consent rejected", post({"image": img, "genre": "romance"}).status_code == 400)
check("bad genre rejected", post({"image": img, "genre": "nope", "consent": True}).status_code == 400)
check("missing name rejected", post({"image": img, "genre": "romance", "consent": True, "name": "  "}).status_code == 400)
for bad in ["", "ada", "ada@", "ada@example", "a b@example.com", "ada@example.com\r\nBcc: x@y.com", None]:
    r = post({"image": img, "genre": "romance", "consent": True, "email": bad})
    check(f"bad email rejected: {bad!r}", r.status_code == 400 and "email" in r.get_json()["error"])
check("create page has the contact fields", b'id="contact-name"' in c.get("/").data and b'id="contact-email"' in c.get("/").data)
r = post({"image": "data:image/jpeg;base64,AAAA", "genre": "romance", "consent": True})
check("bad photo -> 400, no project, no Seedream call", r.status_code == 400 and not seen["seedream"] and not (project_store.PROJECT_DIR.exists() and list(project_store.PROJECT_DIR.glob("*"))))

# ---- genre cards ordered by how often each genre was picked
import re as _re
card_order = lambda: _re.findall(rb'name="genre" value="(\w+)"', c.get("/").data)
check("no stats file: seed order, most picked first, ties keep GENRES order", card_order()[:4] == [b"scifi", b"wuxia", b"palace", b"revenge"]
      and card_order()[-3:] == [b"heir", b"thriller", b"timeslip"] and b'value="scifi" checked' in c.get("/").data)
check("rejected submissions are not counted", not genre_stats.STATS_PATH.exists())

# ---- success path through both steps
r = post({"image": img, "genre": "wuxia", "plot": "owns the cafe", "consent": True}); pid = r.get_json()["job_id"]
check("accepted submission counted on top of the seed", genre_stats.load() == {**genre_stats.SEED_COUNTS, "wuxia": 3})
for _ in range(2): genre_stats.record("wuxia")
check("cards re-sort as counts change", card_order()[:2] == [b"wuxia", b"scifi"])
check("generate returns a job id straight away", r.status_code == 200 and project_store.is_valid_id(pid))
root = project_store.project_dir(pid); j = job(pid)
check("project files incl. character sheet", all((root / f).exists() for f in ["job.json", "log.jsonl", "prompt.txt", "character_prompt.txt", "assets/photo.jpg", "assets/character.png", "ark_image.json"]))
check("Seedream got the photo + character-sheet prompt", len(seen["seedream"]) == 1 and seen["seedream"][0][0] == img
      and seen["seedream"][0][1] == (root / "character_prompt.txt").read_text() and "Character sheet, turnaround, three views" in seen["seedream"][0][1]
      and "flowing ink-grey and indigo" in seen["seedream"][0][1] and seen["seedream"][0][1].endswith("Art style: Cinematic Realism."))
sent_prompt, sent_image = seen["seedance"][0]
check("Seedance got the CHARACTER image, not the photo", sent_image.startswith("data:image/png;base64,") and base64.b64decode(sent_image.split(",", 1)[1]) == CHARACTER_PNG)
check("Seedance prompt follows the skill", sent_prompt == (root / "prompt.txt").read_text() and "@image1 is the character sheet of <Subject1>" in sent_prompt
      and "Shot 1 (0-6s): [EWS" in sent_prompt and "Dialogue (Subject1, calm): {" in sent_prompt and "owns the cafe" in sent_prompt and "no watermark" in sent_prompt and "--" not in sent_prompt)
check("job.json image fields", j["status"] == "queued" and j["task_id"] == "task-123" and j["character"] == {"file": "assets/character.png", "bytes": len(CHARACTER_PNG), "width": 2560, "height": 1440}
      and j["image_usage"]["tokens_out"] == 16384 and j["image_usage"]["raw"]["output_tokens"] == 16384 and {"image_api_s", "image_total_s", "submit_api_s"} <= set(j["timing"])
      and j["params"]["image_model"] == "dola-seedream-5-0-pro-260628" and j["params"]["image_size"] == "2K", j["timing"])
ev = events(pid)
check("log order: created > image_started > image_succeeded > submitted", [e["event"] for e in ev] == ["created", "image_started", "image_succeeded", "submitted"], [e["event"] for e in ev])
check("image_succeeded logs usage + timing", ev[2]["usage"]["tokens_out"] == 16384 and "image_api_s" in ev[2] and ev[2]["character"]["width"] == 2560)
check("no signed URL stored", "SECRET" not in (root / "ark_image.json").read_text() and all("SECRET" not in p.read_text() for p in root.glob("*.json*")))

# ---- video polling (unchanged behaviour)
state.update(status="running"); c.get(f"/api/jobs/{pid}"); c.get(f"/api/jobs/{pid}")
check("running logged once", [e["event"] for e in events(pid)][-1] == "status" and sum(e["event"] == "status" for e in events(pid)) == 1)
dl["fail"] = 1; state.update(status="succeeded", usage={"completion_tokens": 648000, "total_tokens": 648000})
c.get(f"/api/jobs/{pid}"); check("download failure retried", job(pid)["status"] == "running")
r = c.get(f"/api/jobs/{pid}").get_json(); j = job(pid)
check("succeeded: video + video usage kept apart from image usage", r["status"] == "succeeded" and j["usage"]["completion_tokens"] == 648000
      and j["image_usage"]["tokens_out"] == 16384 and j["timing"]["ark_total_s"] == 187 and j["video"]["frames"] == 720 and (root / "video.mp4").exists())

# ---- "video ready" email with the TOS link
check("contact stored on the project", j["notify"] == {"name": "Ada Lee", "email": "ada@example.com"} and j["tos_url"] == "http://example/x.mp4?sig=SECRET")
check("one email sent to the visitor", len(mails) == 1 and mails[0][0] == "ada@example.com" and mails[0][2] == "sender@example.com" and mails[0][3] == "relay")
m = mails[0][1]; text = m.get_body(("plain",)).get_content(); htm = m.get_body(("html",)).get_content()
check("email content: TOS link, genre title, greeting, expiry note", "http://example/x.mp4?sig=SECRET" in text and "http://example/x.mp4?sig=SECRET" in htm
      and "Last Sword of the Mist" in m["Subject"] and text.startswith("Hi Ada Lee,") and "about 24 hours" in text and m["To"] == "Ada Lee <ada@example.com>", m["Subject"])
ev = events(pid)
check("email_sent logged with a masked address", j["email"]["status"] == "sent" and j["email"]["mx"] == "mx.example.com" and ev[-1]["event"] == "email_sent"
      and ev[-1]["to"] == "a***@example.com" and "ada@example.com" not in (root / "log.jsonl").read_text())
c.get(f"/api/jobs/{pid}"); check("a later poll does not send again", len(mails) == 1)

# ---- step 1 fails: Seedream rejects the photo -> Seedance is never called
n_seedance = len(seen["seedance"])
img_state["raise"] = seedream.ImageServiceError(400, body, "req-img-9")
r = post({"image": img, "genre": "romance", "consent": True}); pid2 = r.get_json()["job_id"]; j = job(pid2); ev = events(pid2)
check("image failure -> failed at stage image", j["status"] == "failed" and j["error_detail"]["stage"] == "image" and j["error_detail"]["code"] == "InputImageSensitiveContentDetected.PrivacyInformation"
      and j["error_detail"]["request_id"] == "req-img-9" and j["error_detail"]["status_code"] == 400, j["error_detail"])
check("friendly image message names Seedream", "Seedream" in j["error"] and "reference_asset_id" not in j["error"], j["error"])
check("Seedance not called, photo kept, failure logged", len(seen["seedance"]) == n_seedance and (project_store.project_dir(pid2) / "assets/photo.jpg").exists()
      and ev[-1]["event"] == "failed" and ev[-1]["error"]["stage"] == "image" and "image_api_s" in j["timing"])
img_state["raise"] = None

# ---- step 2 fails: Seedance rejects the character image -> character sheet is kept
seedance.create_task = lambda p, i: (_ for _ in ()).throw(ApiError("The input image may contain a real person"))
r = post({"image": img, "genre": "heir", "consent": True}); pid3 = r.get_json()["job_id"]; j = job(pid3)
check("submit failure -> stage submit, character kept", j["status"] == "failed" and j["error_detail"]["stage"] == "submit" and j["error_detail"]["request_id"] == "req-abc123"
      and (project_store.project_dir(pid3) / "assets/character.png").exists() and j["character"] and j["image_usage"], j["error_detail"])
check("friendly submit message names Seedance + asset library", "Seedance" in j["error"] and "reference_asset_id" in j["error"], j["error"])
check("events: image_succeeded then failed", [e["event"] for e in events(pid3)][-2:] == ["image_succeeded", "failed"])
seedance.create_task = fake_create

# ---- asset library face: no Seedream step
seedance.get_config()["reference_asset_id"] = "asset-xyz"
n_img = len(seen["seedream"])
r = post({"image": img, "genre": "palace", "consent": True}); pid4 = r.get_json()["job_id"]; j = job(pid4)
check("asset id -> Seedream skipped, video still submitted", len(seen["seedream"]) == n_img and j["status"] == "queued" and j["character"] is None
      and [e["event"] for e in events(pid4)] == ["created", "image_skipped", "submitted"], [e["event"] for e in events(pid4)])
seedance.get_config()["reference_asset_id"] = ""

# ---- unexpected bug inside the thread never leaves a project spinning
real = uploads_store.file_to_data_url
calls_left = {"n": 1}
def flaky(path):
    if calls_left["n"] == 0: raise ValueError("boom")
    calls_left["n"] -= 1; return real(path)
uploads_store.file_to_data_url = flaky
r = post({"image": img, "genre": "thriller", "consent": True}); pid5 = r.get_json()["job_id"]; j = job(pid5)
check("internal error -> failed, not stuck", j["status"] == "failed" and j["error_detail"]["stage"] == "internal" and "boom" in j["error_detail"]["message"], j.get("error_detail"))
uploads_store.file_to_data_url = real

# ---- stalled stages (thread lost after a restart)
def make_stalled(status):
    r = post({"image": img, "genre": "timeslip", "consent": True}); p = r.get_json()["job_id"]
    j = job(p); j.update(status=status, task_id=None, character=None); j["created_at"] = (datetime.now() - timedelta(minutes=11)).isoformat(timespec="seconds")
    project_store.save(j); return p
p_img, p_sub = make_stalled("image_generating"), make_stalled("submitting")
c.get(f"/api/jobs/{p_img}"); c.get(f"/api/jobs/{p_sub}")
check("stalled image stage -> failed (image)", job(p_img)["status"] == "failed" and job(p_img)["error_detail"]["stage"] == "image")
check("stalled submit stage -> failed (submit)", job(p_sub)["status"] == "failed" and job(p_sub)["error_detail"]["stage"] == "submit")
fresh = post({"image": img, "genre": "timeslip", "consent": True}).get_json()["job_id"]
j = job(fresh); j.update(status="image_generating", task_id=None); project_store.save(j)
c.get(f"/api/jobs/{fresh}"); check("a young image stage is left alone", job(fresh)["status"] == "image_generating")

# ---- video task failure + poll error + timeout (unchanged behaviour)
r = post({"image": img, "genre": "palace", "consent": True}); pidT = r.get_json()["job_id"]
state.update(status="failed", usage={"completion_tokens": 0}, err={"code": "OutputVideoSensitiveContentDetected", "message": "output blocked"})
c.get(f"/api/jobs/{pidT}"); j = job(pidT)
check("video task failure recorded (stage task)", j["error_detail"] == {"stage": "task", "status": "failed", "code": "OutputVideoSensitiveContentDetected", "message": "output blocked"} and j["image_usage"])
state.pop("err")

# ---- My videos list
seedance.get_config()["access_code"] = ""
items = c.get("/api/projects").get_json(); by = {i["id"]: i for i in items}
check("list has image_generating as in-progress and image-stage failure detail", by[fresh]["status"] == "image_generating"
      and by[pid2]["detail"]["stage"] == "image" and by[pid]["status"] == "succeeded" and by[pid]["video"]["url"] == f"/media/{pid}.mp4")
check("TOS link never exposed by the list API", "SECRET" not in json.dumps(items))
check("contact shown with the email state", by[pid]["contact"] == {"name": "Ada Lee", "email": "ada@example.com", "email_status": "sent",
      "email_at": job(pid)["email"]["at"], "email_error": None} and by[fresh]["contact"]["email_status"] is None, by[pid]["contact"])
ALLOWED = {"id", "genre", "genre_title", "plot", "created_at", "status", "error", "detail", "ark_total_s", "video", "liked", "contact"}
check("only whitelisted fields exposed", all(set(i) == ALLOWED for i in items) and "SECRET" not in json.dumps(items) and "1.2.3.4" not in json.dumps(items))

# ---- like label
def like(pid, body): return c.post(f"/api/projects/{pid}/like", json=body)
check("nothing liked at first", not any(i["liked"] for i in items))
r = like(pid, {"liked": True})
check("like sets the label", r.status_code == 200 and r.get_json() == {"id": pid, "liked": True}
      and (project_store.project_dir(pid) / "liked").exists() and events(pid)[-1]["event"] == "liked")
n_events = len(events(pid))
check("liking twice is idempotent (no toggle back, no extra log line)", like(pid, {"liked": True}).get_json()["liked"] is True and len(events(pid)) == n_events)
by = {i["id"]: i for i in c.get("/api/projects").get_json()}
check("list shows the like on that project only", by[pid]["liked"] is True and sum(i["liked"] for i in by.values()) == 1)
check("like survives a job.json rewrite", (project_store.save(job(pid)) or True) and project_store.is_liked(pid))
r = like(pid, {"liked": False})
check("unlike clears it", r.get_json()["liked"] is False and not (project_store.project_dir(pid) / "liked").exists() and events(pid)[-1]["event"] == "unliked")
check("like: bad body -> 400", like(pid, {"liked": "yes"}).status_code == 400 and like(pid, {}).status_code == 400)
check("like: bad id / unknown project -> 404", like("..%2Fapp.py", {"liked": True}).status_code == 404
      and like("20260101-000000-000000000000", {"liked": True}).status_code == 404)
check("like: GET not allowed", c.get(f"/api/projects/{pid}/like").status_code == 405)

# ---- photo picker: /api/photos + /asset/<id>/photo
jpeg2 = b"\xff\xd8\xff\xe0" + b"\x01" * 80
img2 = "data:image/jpeg;base64," + base64.b64encode(jpeg2).decode()
import time; time.sleep(1.1)   # folder names are per-second, so make the newer project unambiguously newer
n_before = len(c.get("/api/photos").get_json())
pidP = post({"image": img2, "genre": "romance", "consent": True}).get_json()["job_id"]     # a second, different photo
plist = c.get("/api/photos").get_json()
check("distinct photos are listed once each (same file re-used many times = one entry)", len(plist) == 2 and n_before == 1, (n_before, len(plist)))
check("newest photo first; its project id is the source", plist[0]["id"] == pidP and plist[0]["uses"] == 1)
old_entry = plist[1]
n_same = sum(1 for j in project_store.list_projects() if project_store.photo_path(j["id"]) and project_store.photo_path(j["id"]).read_bytes() == jpeg)
check("re-used photo carries its use count, newest project", old_entry["uses"] == n_same and n_same > 3
      and old_entry["id"] == max(j["id"] for j in project_store.list_projects() if project_store.photo_path(j["id"]).read_bytes() == jpeg), (old_entry["uses"], n_same))
check("only whitelisted fields", all(set(e) == {"id", "created_at", "genre_title", "uses", "url"} for e in plist) and plist[0]["genre_title"] == "Rainy Night Reunion")
r = c.get(plist[0]["url"])
check("photo route serves the stored bytes as an image", r.status_code == 200 and r.data == jpeg2 and r.mimetype == "image/jpeg" and "private" in r.headers["Cache-Control"])
check("photo url gets the /drama prefix behind nginx", c.get("/api/photos", headers={"X-Forwarded-Prefix": "/drama"}).get_json()[0]["url"] == f"/drama/asset/{pidP}/photo")
check("photo route: bad id / missing project -> 404", c.get("/asset/..%2Fapp.py/photo").status_code == 404 and c.get("/asset/20260101-000000-000000000000/photo").status_code == 404)
seedance.get_config()["history_scope"] = "browser"
check("browser scope: no ids -> empty picker", c.get("/api/photos").get_json() == [])
only = c.get(f"/api/photos?ids={pidP},../../etc").get_json()
check("browser scope: only remembered ids", [e["id"] for e in only] == [pidP])
seedance.get_config()["history_scope"] = "all"

# ---- guards
check("bad id", c.get("/api/jobs/../../etc/passwd").status_code == 404 and c.get("/media/..%2Fapp.py.mp4").status_code == 404)
seedance.get_config()["daily_limit"] = 1
check("daily limit", post({"image": img, "genre": "palace", "consent": True}).status_code == 429)
n_today = project_store.count_created_today()
seedance.get_config()["daily_limit_reset_at"] = (datetime.now() + timedelta(seconds=1)).isoformat(timespec="seconds")  # same-second projects still count
check("reset: earlier projects today no longer count", n_today >= 1 and project_store.count_created_today(seedance.get_config()["daily_limit_reset_at"]) == 0
      and post({"image": img, "genre": "palace", "consent": True}).status_code == 200, n_today)
check("reset from another day changes nothing", project_store.count_created_today("2000-01-01T00:00:00") == project_store.count_created_today())
seedance.get_config().pop("daily_limit_reset_at")
seedance.get_config()["daily_limit"] = 10; seedance.get_config()["access_code"] = "sesame"
check("access code required", post({"image": img, "genre": "palace", "consent": True}).status_code == 403)
# ---- email retries, permanent refusal, switch
from services import mailer
def run_to_success(body):
    state.update(status="queued"); p = post(body).get_json()["job_id"]
    state.update(status="succeeded"); c.get(f"/api/jobs/{p}"); return p
seedance.get_config()["daily_limit"] = 100; seedance.get_config()["access_code"] = ""
mails.clear(); mail_state["errors"] = [mailer.MailError("421 try later", code=421, mx="mx1"), mailer.MailError("TimeoutError", mx="mx1")]
p = run_to_success({"image": img, "genre": "scifi", "consent": True, "email": "bo@example.org", "name": "Bo"}); j = job(p)
check("temporary failures retried, third attempt delivers", len(mails) == 1 and j["email"]["status"] == "sent" and j["email"]["attempts"] == 3
      and [e["event"] for e in events(p)][-3:] == ["email_retry", "email_retry", "email_sent"], [e["event"] for e in events(p)])
mails.clear(); mail_state["errors"] = [mailer.MailError("550 5.7.25 no PTR", permanent=True, code=550, mx="gmail-smtp-in")]
p = run_to_success({"image": img, "genre": "revenge", "consent": True}); j = job(p)
check("permanent refusal: no retry, recorded", not mails and j["email"]["status"] == "failed" and j["email"]["attempts"] == 1
      and j["email"]["error"]["code"] == 550 and events(p)[-1]["event"] == "email_failed" and j["status"] == "succeeded")
lc = {i["id"]: i for i in c.get("/api/projects").get_json()}[p]["contact"]
check("failed email shows its reason in the list", lc["email_status"] == "failed" and "no PTR" in lc["email_error"])
mails.clear(); seedance.get_config()["mail_enabled"] = False
p = run_to_success({"image": img, "genre": "fantasy", "consent": True})
check("mail_enabled false: nothing sent", not mails and "email" not in job(p))
seedance.get_config()["mail_enabled"] = True

# ---- _data/events.jsonl: anonymous usage data
import contextlib, io
de = data_log.load()
of = lambda kind, **kw: [e for e in de if e["event"] == kind and all(e.get(k) == v for k, v in kw.items())]
raw_data = data_log.EVENTS_PATH.read_text(encoding="utf-8")
check("one 'created' event per project", len(of("created")) == len(list(project_store.PROJECT_DIR.glob("*/job.json"))) > 0)
check("nothing personal in _data", not any(x in raw_data for x in ("Ada", "ada@example.com", "a***@", "1.2.3.4", "TestUA", "owns the cafe", "data:image", "SECRET", "Subject1")))
check("created: genre, story flag, device, models", of("created", id=pid)[0] | {"t": 0} == {"t": 0, "event": "created", "id": pid, "genre": "wuxia", "plot": True,
      "device": "desktop", "image_model": "dola-seedream-5-0-pro-260628", "video_model": "dreamina-seedance-2-5-260628"}, of("created", id=pid))
ok = of("finished", id=pid)
check("finished (succeeded): timings, tokens, size", len(ok) == 1 and ok[0]["status"] == "succeeded" and ok[0]["timing"]["ark_total_s"] == 187
      and ok[0]["image_tokens"] == 16384 and ok[0]["video_tokens"] == 648000 and "stage" not in ok[0], ok)
bad = of("finished", status="failed", stage="image")
check("finished (failed): stage + error code", bad and bad[0]["code"] == "InputImageSensitiveContentDetected.PrivacyInformation" and "elapsed_s" in bad[0], bad)
check("stage 'task' failures carry the task code", any(e["code"] for e in of("finished", status="failed", stage="task")))
check("turned away by the daily limit", of("rejected", reason="daily_limit", genre="palace"))
check("email outcomes", of("email", id=pid, status="sent") and of("email", status="failed"))
check("likes: one event per change", [e["liked"] for e in of("liked", id=pid)] == [True, False])
summary = data_report.summarise(de); rows = {r["genre"]: r for r in summary["genres"]}
check("report: popularity and failure rate", summary["created"] == len(of("created")) and rows["wuxia"]["succeeded"] >= 1
      and summary["failure_pct"] is not None and len(rows) == len(appmod.genres.GENRES) and rows["wuxia"]["likes"] == 0
      and any(k.startswith("image: InputImageSensitive") for k, _ in summary["failure_reasons"]), summary["failure_reasons"])
out = io.StringIO()
with contextlib.redirect_stdout(out): data_report.main([])
check("report prints", "Last Sword of the Mist" in out.getvalue() and "Failure reasons" in out.getvalue())
with contextlib.redirect_stdout(out := io.StringIO()): data_report.main(["--json", "--since", "2999-01-01"])
check("report --since filters, --json is JSON", json.loads(out.getvalue())["events"] == 0)

# ---- mailer unit checks (no network: fake DNS and fake SMTP)
check("clean_email", mailer.clean_email(" a.b+c@mail.example.co ") == "a.b+c@mail.example.co" and mailer.clean_email("a@b") is None and mailer.clean_email("a@b.com\nBcc:x") is None)
check("clean_name drops control chars", mailer.clean_name("Ada\r\nBcc: x@y.com\t Lee") == "AdaBcc: x@y.com Lee" and len(mailer.clean_name("x" * 200)) == 80, mailer.clean_name("Ada\r\nBcc: x@y.com\t Lee"))
check("mask", mailer.mask("jane@gmail.com") == "j***@gmail.com")
exp = mailer.tos_expiry("https://b.tos-ap-southeast-1.bytepluses.com/v.mp4?X-Tos-Algorithm=a&X-Tos-Date=20260930T012000Z&X-Tos-Expires=86400&X-Tos-Signature=s")
check("tos_expiry reads X-Tos-Date + X-Tos-Expires", exp is not None and exp.isoformat() == "2026-10-01T01:20:00+00:00", exp)
m = mailer.build_message("noreply@d.me", "Drama Flow", "<b>Eve</b>", "eve@example.com", "T", "https://x/v.mp4?a=1&b=2", exp)
check("html part escapes the name, text states local expiry", "&lt;b&gt;Eve&lt;/b&gt;" in m.get_body(("html",)).get_content()
      and "until 01 Oct 2026, 09:20 (UTC+8)" in m.get_body(("plain",)).get_content() and "&amp;b=2" in m.get_body(("html",)).get_content())
import smtplib
tried = []
class FakeSMTP:
    script = {}
    def __init__(self, host, port, timeout, local_hostname): self.host = host; tried.append(host)
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def ehlo(self): pass
    def has_extn(self, name): return False
    def send_message(self, msg, from_addr, to_addrs):
        code = FakeSMTP.script.get(self.host)
        if code: raise smtplib.SMTPDataError(code, b"refused")
    def noop(self): return 250, b"ok"
real_smtp, real_mx = smtplib.SMTP, mailer.mx_hosts
mailer.smtplib.SMTP = FakeSMTP; mailer.mx_hosts = lambda d: ["mx1." + d, "mx2." + d]
FakeSMTP.script = {"mx1.example.com": 451}
r = real_send(m, "eve@example.com", "noreply@d.me", "d.me")
check("send: 4xx on first MX falls through to the backup", r["mx"] == "mx2.example.com" and r["tls"] is False and tried == ["mx1.example.com", "mx2.example.com"], (r, tried))
tried.clear(); FakeSMTP.script = {"mx1.example.com": 550}
try:
    real_send(m, "eve@example.com", "noreply@d.me", "d.me"); ok = False
except mailer.MailError as exc:
    ok = exc.permanent and exc.code == 550 and tried == ["mx1.example.com"]
check("send: 5xx is final, backups not tried", ok)
mailer.smtplib.SMTP, mailer.mx_hosts = real_smtp, real_mx

# relay mode (Gmail): fake SMTP_SSL server
relay_log = []
class FakeSSL:
    password_ok = "app-pass"; refuse = None
    def __init__(self, host, port, timeout, context): relay_log.append(("connect", host, port, context.verify_mode == __import__("ssl").CERT_REQUIRED))
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def ehlo(self): pass
    def login(self, user, pw):
        relay_log.append(("login", user))
        if pw != FakeSSL.password_ok: raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    def send_message(self, msg, from_addr, to_addrs):
        if FakeSSL.refuse: raise smtplib.SMTPRecipientsRefused({to_addrs[0]: FakeSSL.refuse})
        relay_log.append(("send", from_addr, to_addrs)); return {}
real_ssl = mailer.smtplib.SMTP_SSL; mailer.smtplib.SMTP_SSL = FakeSSL
cfg_relay = {"mail_mode": "relay", "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_user": "sender@example.com", "mail_from": "sender@example.com"}
r = real_deliver(m, "eve@example.com", cfg_relay, "app-pass")
check("relay: TLS-verified connect to smtp.gmail.com:465, login, send from the account", r["mx"] == "smtp.gmail.com"
      and relay_log == [("connect", "smtp.gmail.com", 465, True), ("login", "sender@example.com"), ("send", "sender@example.com", ["eve@example.com"])], relay_log)
try:
    real_deliver(m, "eve@example.com", cfg_relay, "wrong"); ok = False
except mailer.MailError as exc:
    ok = exc.permanent and exc.code == 535 and "login failed" in str(exc)
check("relay: bad app password is a permanent failure", ok)
try:
    real_deliver(m, "eve@example.com", cfg_relay, None); ok = False
except mailer.MailError as exc:
    ok = exc.permanent and "smtp_password" in str(exc)
check("relay: missing password is reported, nothing sent", ok)
FakeSSL.refuse = (550, b"5.1.1 no such user")
try:
    real_deliver(m, "eve@example.com", cfg_relay, "app-pass"); ok = False
except mailer.MailError as exc:
    ok = exc.permanent and exc.code == 550
check("relay: recipient refused by Gmail is permanent", ok)
FakeSSL.refuse = None; mailer.smtplib.SMTP_SSL = real_ssl
print("ALL OK; temp dir:", tmp)
