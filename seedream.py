"""Seedream 5.0 Pro image generation over Ark's REST API.

Same request shape as the video-workflow skill's `ArkImageService` (POST to the image endpoint,
reference photo in `image`, response_format=url). The endpoint and key come from
seedance.get_endpoint("image") and get_api_key("image"), i.e. `image_maas_api_endpoint` and
`image_maas_api_key` when set. Image generation is one blocking HTTP call that can run past two
minutes, hence the long timeout.
"""
import json
import os
import struct
import urllib.request

import httpx

import seedance


ImageServiceError = seedance.ArkHttpError  # one shared class for every Ark endpoint


def extract_token_usage(usage):
    """Normalise an Ark `usage` dict into {tokens_in, tokens_out, tokens_total}.

    Image generation reports {"input_images", "generated_images", "output_tokens", "total_tokens"},
    chat/video use prompt_tokens/completion_tokens. Missing values are None, never 0.
    """
    usage = usage or {}
    tokens_in = usage.get("prompt_tokens")
    if tokens_in is None:
        tokens_in = usage.get("input_tokens")
    tokens_out = usage.get("completion_tokens")
    if tokens_out is None:
        tokens_out = usage.get("output_tokens")
    return {"tokens_in": tokens_in, "tokens_out": tokens_out, "tokens_total": usage.get("total_tokens")}


def generate_image(photo_data_url, prompt):
    """One Seedream call with the photo as reference. Returns {"url", "usage", "raw"} (raw has the URL redacted)."""
    cfg = seedance.get_config()
    payload = {
        "model": cfg["image_model_id"],
        "prompt": prompt,
        "image": photo_data_url,
        "size": cfg["image_size"],
        "response_format": "url",
        "watermark": cfg["watermark"],
    }
    resp = httpx.post(
        seedance.get_endpoint("image"),
        headers={"Authorization": f"Bearer {seedance.get_api_key('image')}", "Content-Type": "application/json"},
        json=payload,
        timeout=cfg.get("image_timeout_seconds", 300),
    )
    if resp.status_code != 200:
        raise ImageServiceError(resp.status_code, resp.text, resp.headers.get("x-request-id"))
    data = resp.json()
    items = data.get("data") or []
    url = items[0].get("url") if items else None
    if not url:
        raise RuntimeError(f"Image generation returned no image URL: {json.dumps(data)[:300]}")
    raw = dict(data)
    raw["data"] = [{**item, "url": "<redacted>"} if item.get("url") else item for item in items]
    return {"url": url, "usage": data.get("usage") or {}, "raw": raw}


def download(url, dest_no_ext):
    """Save the generated image next to dest_no_ext, picking the extension from the file signature.

    Returns {"path", "bytes", "width", "height", "mime"}.
    """
    with urllib.request.urlopen(url, timeout=60) as resp:
        raw = resp.read()
    mime, ext = sniff_type(raw)
    path = f"{dest_no_ext}{ext}"
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, path)
    size = image_dimensions(raw)
    return {"path": path, "bytes": len(raw), "width": size[0] if size else None,
            "height": size[1] if size else None, "mime": mime}


def sniff_type(raw):
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise RuntimeError("The downloaded file is not a PNG, JPEG or WebP image")


def image_dimensions(raw):
    """(width, height) of a PNG or JPEG, or None. Enough for the log without depending on Pillow."""
    try:
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return struct.unpack(">II", raw[16:24])
        if raw.startswith(b"\xff\xd8\xff"):
            i = 2
            while i + 9 < len(raw):
                if raw[i] != 0xFF:
                    i += 1
                    continue
                marker = raw[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", raw[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    height, width = struct.unpack(">HH", raw[i + 5:i + 9])
                    return width, height
                i += 2 + length
    except struct.error:
        pass
    return None
