"""Generate the genre-picker thumbnails with Seedream (text only, fictional leads, no reference photo).

Run locally from the project folder; it uses config.json / credential.json like the app does.
Each genre costs one paid Seedream call, so pass only the ids you want to (re)make:

    python deploy/make_genre_thumbnails.py romance heir      # just these
    python deploy/make_genre_thumbnails.py --all             # every genre

The full-size poster goes to _thumbnail_src/<id>.<ext> (kept locally, not deployed) and a small
3:4 JPEG to static/img/genres/<id>.jpg, which the index page shows when it exists.
Never write a double hyphen in a prompt: the model silently drops everything after it.
"""
import io
import os
import sys

import httpx
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from services import genres, seedance, seedream  # noqa: E402

SRC_DIR = os.path.join(ROOT, "_thumbnail_src")
OUT_DIR = os.path.join(ROOT, "static", "img", "genres")
POSTER_SIZE = "1440x2560"  # 9:16, within Seedream's 2K pixel budget
THUMB_W, THUMB_H = 360, 480  # 3:4 crop shown on the genre cards

# One key moment per genre, matching its shot list. The lead is always a fictional adult.
SCENES = {
    "romance": "a young adult stands under a rainy bus-stop shelter at night, looking up with a surprised, hopeful smile, neon reflections on wet pavement",
    "heir": "a confident young executive strides through tall glass doors into a grand ballroom as the crowd parts, chandeliers glowing",
    "wuxia": "a lone swordsman in a misty bamboo forest, one hand on the sword hilt, robes and bamboo leaves swirling in the wind",
    "thriller": "an office worker alone in a dim office corridor at night, glancing back over their shoulder, a shadow behind frosted glass",
    "timeslip": "a young adult in a cosy retro café touches the window as the golden wall clocks spin backwards, floating dust in warm light",
    "palace": "a composed noble stands on red palace steps at dusk, lanterns glowing, silk sleeves fluttering, courtiers bowing below",
    "revenge": "an elegant young adult walks calmly into a glittering banquet, champagne glasses in the foreground, guests turning to stare",
    "scifi": "an astronaut floats by the round window of a space station, Earth glowing below, soft red warning lights on the panels",
    "fantasy": "a hooded adventurer stands before a great silver dragon on a cliff above a misty valley at sunset, a silver amulet glowing",
}


def poster_prompt(genre):
    return (
        f"Cinematic film still, vertical movie poster composition, {genre['tagline'].lower()}. "
        f"{SCENES[genre['id']]}. The lead wears {genre['costume']}. {genre['setting']} "
        "One clear main character, a fictional person, face visible, strong key light, rich colour grade. "
        "no text, no title, no letters, no watermark, no logo, no border."
    )


def generate(genre):
    cfg = seedance.get_config()
    prompt = poster_prompt(genre)
    assert "--" not in prompt, genre["id"]
    resp = httpx.post(
        seedance.get_endpoint("image"),
        headers={"Authorization": f"Bearer {seedance.get_api_key('image')}", "Content-Type": "application/json"},
        json={"model": cfg["image_model_id"], "prompt": prompt, "size": POSTER_SIZE,
              "response_format": "url", "watermark": False},
        timeout=cfg.get("image_timeout_seconds", 300),
    )
    if resp.status_code != 200:
        raise seedream.ImageServiceError(resp.status_code, resp.text, resp.headers.get("x-request-id"))
    url = resp.json()["data"][0]["url"]
    saved = seedream.download(url, os.path.join(SRC_DIR, genre["id"]))
    return saved


def make_thumb(src_path, genre_id):
    img = Image.open(src_path).convert("RGB")
    w, h = img.size
    crop_h = int(w * THUMB_H / THUMB_W)  # 3:4 window, a little above centre where faces sit
    top = max(0, min(h - crop_h, int((h - crop_h) * 0.35)))
    img = img.crop((0, top, w, top + crop_h)).resize((THUMB_W, THUMB_H), Image.LANCZOS)
    out = os.path.join(OUT_DIR, f"{genre_id}.jpg")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82, optimize=True, progressive=True)
    with open(out, "wb") as f:
        f.write(buf.getvalue())
    return out, len(buf.getvalue())


def main(argv):
    ids = [g["id"] for g in genres.GENRES] if argv == ["--all"] else argv
    unknown = [i for i in ids if not genres.get_genre(i)]
    if not ids or unknown:
        sys.exit(f"usage: make_genre_thumbnails.py <genre id>... | --all  (unknown: {unknown})")
    os.makedirs(SRC_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    for gid in ids:
        missing = gid not in SCENES
        if missing:
            print(f"{gid}: no scene line in SCENES, skipped")
            continue
        try:
            saved = generate(genres.get_genre(gid))
        except Exception as exc:  # keep going; one refused prompt should not stop the rest
            print(f"{gid}: FAILED {exc}")
            continue
        out, size = make_thumb(saved["path"], gid)
        print(f"{gid}: {saved['width']}x{saved['height']} -> {os.path.relpath(out, ROOT)} ({size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1:])
