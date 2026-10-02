"""Validate and store the face photo captured on the home page (saved into the project's assets/)."""
import base64
import binascii
import re
from pathlib import Path

MAX_IMAGE_BYTES = 6 * 1024 * 1024

_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_DATA_URL_RE = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", re.DOTALL)


def _matches_type(mime, raw):
    """Check the file signature so arbitrary bytes can't be saved as an image."""
    if mime == "image/jpeg":
        return raw.startswith(b"\xff\xd8\xff")
    if mime == "image/png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/webp":
        return raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    return False


def file_to_data_url(path):
    """Read a saved image back as a data URL (the mime type comes from the extension)."""
    path = Path(path)
    mime = {ext: m for m, ext in _EXTENSIONS.items()}.get(path.suffix.lower(), "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def save_image(data_url, dest_dir):
    """Write an uploaded data-URL image into dest_dir as photo.<ext>.

    Returns {"name", "bytes"}. Raises ValueError if the data is not a supported, well-formed image.
    """
    m = _DATA_URL_RE.match(data_url)
    if not m or m.group(1) not in _EXTENSIONS:
        raise ValueError("unsupported image type")
    mime = m.group(1)
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("invalid image data")
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("image is empty or too large")
    if not _matches_type(mime, raw):
        raise ValueError("image data does not match its type")

    name = f"photo{_EXTENSIONS[mime]}"
    (Path(dest_dir) / name).write_bytes(raw)
    return {"name": name, "bytes": len(raw)}
