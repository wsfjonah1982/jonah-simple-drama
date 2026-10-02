"""Seedance video generation over Ark's REST API, plus the shared endpoint/key/error helpers.

Plain HTTP (httpx): the SDK appends its own sub-path to base_url, which conflicts with a literal
full-URL endpoint. Endpoints and API keys are resolved per service:

* endpoint: `<service>_maas_api_endpoint` in config.json (a full URL, used as it is), else
  `maas_api_endpoint` (the API root) plus the service's default path;
* key: `<service>_maas_api_key` in credential.json, else `model_ark_key` / `maas_api_key` / `api_key`.
"""
import json
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent.parent  # the project root, one level above services/
CONFIG_PATH = BASE_DIR / "config.json"
CREDENTIAL_PATH = BASE_DIR / "credential.json"

DEFAULT_PATHS = {"video": "/contents/generations/tasks", "image": "/images/generations"}

_config = None


def _load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_config():
    global _config
    if _config is None:
        _config = _load_json(CONFIG_PATH)
    return _config


def get_endpoint(service):
    """Full request URL for "video" (task create; a task is GET {url}/{id}) or "image"."""
    cfg = get_config()
    override = (cfg.get(f"{service}_maas_api_endpoint") or "").strip()
    return override or cfg["maas_api_endpoint"].rstrip("/") + DEFAULT_PATHS[service]


def get_api_key(service=None):
    creds = _load_json(CREDENTIAL_PATH)
    if service:
        key = creds.get(f"{service}_maas_api_key")
        if key:
            return key
    key = creds.get("model_ark_key") or creds.get("maas_api_key") or creds.get("api_key")
    if not key:
        raise KeyError("No API key in credential.json (need `maas_api_key`, or `video_maas_api_key` / `image_maas_api_key`)")
    return key


def _headers(service):
    return {"Authorization": f"Bearer {get_api_key(service)}", "Content-Type": "application/json"}


class ArkHttpError(Exception):
    """Non-200 answer from an Ark endpoint, with the API's error fields split out for the project log."""

    def __init__(self, status_code, body_text, request_id=None):
        self.status_code = status_code
        self.request_id = request_id
        self.body = None
        self.code = None
        message = (body_text or "")[:500]
        try:
            self.body = json.loads(body_text)
            error = self.body.get("error") or {}
            self.code = error.get("code")
            message = error.get("message") or message
        except (ValueError, AttributeError):
            pass
        super().__init__(f"Error code: {status_code} - {message}")


def friendly_error(raw, stage=None):
    """Turn an Ark error into a short message the user can act on.

    stage is "image" (Seedream character image) or "submit"/"task" (Seedance video); the fix differs.
    """
    text = str(raw or "")
    low = text.lower()
    if "real person" in low or "real human" in low or "privacyinformation" in low or "portrait" in low:
        if stage == "image":
            return (
                "The image service (Seedream) rejected this photo because it appears to show a real person. "
                "Try a different photo."
            )
        return (
            "The video service (Seedance) rejected the character image because it appears to show a real person. "
            "Seedance only accepts real faces that were registered in the BytePlus real-human "
            "asset library (set its asset ID as `reference_asset_id` in config.json)."
        )
    if "sensitive" in low or "moderation" in low or "content policy" in low:
        return "The photo or story was blocked by BytePlus content safety. Try a different photo or story idea."
    if "balance" in low or "quota" in low or "arrears" in low or "insufficient" in low:
        return "The video service has no quota or balance left. Please contact the site owner."
    return text[:300] or "Video generation failed."


def create_task(prompt, image_data_url):
    """Submit a video task. The face comes from a registered asset if configured, else the given image."""
    cfg = get_config()
    asset_id = (cfg.get("reference_asset_id") or "").strip()
    image_url = f"asset://{asset_id}" if asset_id else image_data_url

    image_part = {"type": "image_url", "image_url": {"url": image_url}}
    if cfg.get("image_role"):
        image_part["role"] = cfg["image_role"]

    resp = httpx.post(
        get_endpoint("video"),
        headers=_headers("video"),
        json={
            "model": cfg["video_model_id"],
            "content": [{"type": "text", "text": prompt}, image_part],
            "duration": cfg["duration"],
            "ratio": cfg["ratio"],
            "resolution": cfg["resolution"],
            "generate_audio": cfg["generate_audio"],
            "watermark": cfg["watermark"],
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise ArkHttpError(resp.status_code, resp.text, resp.headers.get("x-request-id"))
    task_id = resp.json().get("id")
    if not task_id:
        raise RuntimeError(f"Task created but no id returned: {resp.text[:300]}")
    return task_id


def describe_error(exc):
    """Everything useful about an exception raised by an Ark call, for the project log."""
    detail = {"type": type(exc).__name__, "message": str(exc)}
    for attr in ("code", "status_code", "request_id", "param", "type"):
        value = getattr(exc, attr, None)
        if value not in (None, ""):
            detail["api_type" if attr == "type" else attr] = value
    body = getattr(exc, "body", None)
    if body:
        detail["body"] = body
    return detail


_SIGNED_URL_KEYS = {"video_url", "last_frame_url", "file_url"}


def _redact(value):
    """Drop the signed (temporary, bearer-style) download URLs before a dump goes to disk."""
    if isinstance(value, dict):
        return {k: ("<redacted>" if k in _SIGNED_URL_KEYS and v else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def get_task(task_id):
    """Return the task's state, usage and timestamps.

    status is queued/running/succeeded/failed/expired/cancelled. `error` is a raw
    "code: message" string (or None) and `raw` the redacted full response for the project log.
    """
    resp = httpx.get(f"{get_endpoint('video')}/{task_id}", headers=_headers("video"), timeout=30)
    if resp.status_code != 200:
        raise ArkHttpError(resp.status_code, resp.text, resp.headers.get("x-request-id"))
    data = resp.json()

    err = data.get("error") if isinstance(data.get("error"), dict) and data.get("error") else None
    error_code = err.get("code") if err else None
    error_message = (err.get("message") or json.dumps(err)) if err else None
    return {
        "status": data.get("status") or "running",
        "video_url": (data.get("content") or {}).get("video_url"),
        "error": (f"{error_code}: {error_message}" if error_code else error_message) if err else None,
        "error_code": error_code,
        "error_message": error_message,
        "usage": data.get("usage") if isinstance(data.get("usage"), dict) else None,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "raw": _redact(data),
    }
