import json, sys, tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
import httpx, seedance, seedream
# Tests never use the real config.json / credential.json: the example config plus fake keys.
seedance.CONFIG_PATH = PROJECT / "config.example.json"
seedance.CREDENTIAL_PATH = Path(tempfile.mkdtemp()) / "credential.json"
seedance.CREDENTIAL_PATH.write_text(json.dumps({"video_maas_api_key": "test-video-key", "image_maas_api_key": "test-image-key"}))

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name, extra)
    if not cond: sys.exit(1)

# ---- the real project config now has the literal per-service endpoints
cfg = seedance.get_config()
check("config has literal video endpoint", seedance.get_endpoint("video") == "https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks")
check("config has literal image endpoint", seedance.get_endpoint("image") == "https://ark.ap-southeast.bytepluses.com/api/v3/images/generations")

# ---- endpoint resolution: override wins, else root + default path
saved = dict(cfg)
for k in ("video_maas_api_endpoint", "image_maas_api_endpoint"): cfg[k] = ""
cfg["maas_api_endpoint"] = "https://example.test/api/v3/"
check("fallback: root + default path (trailing slash tolerated)", seedance.get_endpoint("video") == "https://example.test/api/v3/contents/generations/tasks"
      and seedance.get_endpoint("image") == "https://example.test/api/v3/images/generations")
cfg["video_maas_api_endpoint"] = "https://proxy.test/custom/video-tasks"
check("override is used literally", seedance.get_endpoint("video") == "https://proxy.test/custom/video-tasks")
cfg.clear(); cfg.update(saved)

# ---- key resolution (temp credential files), lookup order
def with_creds(creds, fn):
    p = Path(tempfile.mkdtemp()) / "credential.json"; p.write_text(json.dumps(creds))
    old = seedance.CREDENTIAL_PATH; seedance.CREDENTIAL_PATH = p
    try: return fn()
    finally: seedance.CREDENTIAL_PATH = old
check("per-service key wins", with_creds({"maas_api_key": "GEN", "video_maas_api_key": "VID", "image_maas_api_key": "IMG"},
      lambda: (seedance.get_api_key("video"), seedance.get_api_key("image"), seedance.get_api_key()) == ("VID", "IMG", "GEN")))
check("falls back to maas_api_key", with_creds({"maas_api_key": "GEN"}, lambda: seedance.get_api_key("video") == "GEN"))
check("falls back to model_ark_key, then api_key", with_creds({"model_ark_key": "MAK", "maas_api_key": "GEN"}, lambda: seedance.get_api_key("image") == "MAK")
      and with_creds({"api_key": "OLD"}, lambda: seedance.get_api_key() == "OLD"))
try:
    with_creds({"access_token": "x"}, lambda: seedance.get_api_key("video")); ok = False
except KeyError: ok = True
check("no key -> clear KeyError", ok)

# ---- mock httpx
calls = []
class R:
    def __init__(self, status, body, headers=None): self.status_code, self._b, self.headers = status, body, headers or {}
    text = property(lambda s: json.dumps(s._b) if not isinstance(s._b, str) else s._b)
    def json(self): return self._b
reply = {}
def fake_post(url, headers=None, json=None, timeout=None): calls.append(("POST", url, headers, json, timeout)); return reply["post"]
def fake_get(url, headers=None, timeout=None): calls.append(("GET", url, headers, None, timeout)); return reply["get"]
seedance.httpx.post, seedance.httpx.get = fake_post, fake_get
seedream.httpx.post = fake_post

# ---- create_task: literal URL, per-service bearer key, exact payload
reply["post"] = R(200, {"id": "cgt-2026-abc"})
tid = seedance.create_task("PROMPT", "data:image/png;base64,AAAA")
verb, url, headers, body, timeout = calls[-1]
key = json.load(open(seedance.CREDENTIAL_PATH))["video_maas_api_key"]
check("create_task posts to the literal video endpoint", verb == "POST" and url == seedance.get_endpoint("video") and tid == "cgt-2026-abc")
check("create_task uses the video key", headers["Authorization"] == f"Bearer {key}" and timeout == 60)
check("create_task payload", body == {"model": cfg["video_model_id"], "content": [{"type": "text", "text": "PROMPT"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}, "role": "reference_image"}],
      "duration": 30, "ratio": "9:16", "resolution": "720p", "generate_audio": True, "watermark": False}, body)
cfg["reference_asset_id"] = "asset-77"; seedance.create_task("P", "data:x")
check("asset id replaces the image", calls[-1][3]["content"][1]["image_url"]["url"] == "asset://asset-77"); cfg["reference_asset_id"] = ""

reply["post"] = R(400, {"error": {"code": "InputImageSensitiveContentDetected.PrivacyInformation", "message": "may contain real person"}}, {"x-request-id": "rid-1"})
try: seedance.create_task("P", "data:x"); e = None
except seedance.ArkHttpError as exc: e = exc
d = seedance.describe_error(e)
check("HTTP error -> ArkHttpError with code/status/request id/body", e and d["code"] == "InputImageSensitiveContentDetected.PrivacyInformation"
      and d["status_code"] == 400 and d["request_id"] == "rid-1" and d["body"]["error"]["message"] == "may contain real person" and "real person" in d["message"], d)
reply["post"] = R(200, {"nope": 1})
try: seedance.create_task("P", "x"); ok = False
except RuntimeError: ok = True
check("200 without id -> RuntimeError", ok)

# ---- get_task: URL is endpoint/{id}; running, succeeded (usage, timestamps, redaction), failed
reply["get"] = R(200, {"id": "cgt-1", "status": "running", "error": None, "content": {}, "usage": None})
t = seedance.get_task("cgt-1")
check("get_task GETs endpoint/id with the video key", calls[-1][1] == seedance.get_endpoint("video") + "/cgt-1" and calls[-1][2]["Authorization"] == f"Bearer {key}" and calls[-1][4] == 30)
check("running parsed", t["status"] == "running" and t["video_url"] is None and t["error"] is None and t["usage"] is None)
reply["get"] = R(200, {"id": "cgt-1", "status": "succeeded", "content": {"video_url": "https://cdn/x.mp4?sig=SECRET", "last_frame_url": "https://cdn/l.png?sig=SECRET"},
                       "usage": {"completion_tokens": 648000, "total_tokens": 648000}, "created_at": 100, "updated_at": 287, "duration": 30, "frames": 720, "seed": 7})
t = seedance.get_task("cgt-1")
check("succeeded parsed", t["status"] == "succeeded" and t["video_url"].endswith("sig=SECRET") and t["usage"]["completion_tokens"] == 648000 and (t["created_at"], t["updated_at"]) == (100, 287))
check("raw is redacted", "SECRET" not in json.dumps(t["raw"]) and t["raw"]["content"]["video_url"] == "<redacted>" and t["raw"]["frames"] == 720)
reply["get"] = R(200, {"id": "cgt-1", "status": "failed", "error": {"code": "OutputVideoSensitiveContentDetected", "message": "blocked"}, "content": {}})
t = seedance.get_task("cgt-1")
check("failed parsed", t["status"] == "failed" and t["error"] == "OutputVideoSensitiveContentDetected: blocked" and t["error_code"] == "OutputVideoSensitiveContentDetected" and t["error_message"] == "blocked")
reply["get"] = R(404, {"error": {"code": "ResourceNotFound", "message": "task not found"}}, {"x-request-id": "rid-2"})
try: seedance.get_task("cgt-x"); ok = None
except seedance.ArkHttpError as exc: ok = exc
check("404 -> ArkHttpError", ok and ok.status_code == 404 and ok.code == "ResourceNotFound")

# ---- Seedream uses the image endpoint + image key
img_key = json.load(open(seedance.CREDENTIAL_PATH))["image_maas_api_key"]
reply["post"] = R(200, {"data": [{"url": "https://cdn/i.png?sig=SECRET"}], "usage": {"generated_images": 1, "output_tokens": 16384, "total_tokens": 16384}})
res = seedream.generate_image("data:image/jpeg;base64,AAAA", "PROMPT")
verb, url, headers, body, timeout = calls[-1]
check("seedream posts to the literal image endpoint with the image key", url == seedance.get_endpoint("image") and headers["Authorization"] == f"Bearer {img_key}" and timeout == 300)
check("seedream payload", body == {"model": cfg["image_model_id"], "prompt": "PROMPT", "image": "data:image/jpeg;base64,AAAA", "size": "2K", "response_format": "url", "watermark": False}, body)
check("seedream result + redaction", res["url"].endswith("SECRET") and "SECRET" not in json.dumps(res["raw"]) and res["usage"]["output_tokens"] == 16384)
check("ImageServiceError is the shared class", seedream.ImageServiceError is seedance.ArkHttpError)
print("ALL OK")
