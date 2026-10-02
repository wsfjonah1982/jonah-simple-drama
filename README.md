# Jonah Simple Drama

A small web app that turns a selfie into the lead of a 30-second AI short drama. The visitor takes or uploads a face photo, picks one of nine genres (optionally adding a one-line story idea), and two BytePlus Ark models work in turn:

1. **Seedream 5.0 Pro** draws a three-view character sheet of the person in the genre's costume.
2. **Seedance 2.5** films a 9:16, 720p, 30 s video with sound and dialogue, using that sheet as the reference image.

A run takes about 10 minutes end to end. When the video is ready the visitor can get an email with the download link.

## Stack

- Flask, Jinja templates and vanilla JS. Both Ark services are called over plain HTTP with `httpx` (no SDK).
- No database: every generation is a folder under `_project/<id>/` (`job.json`, `log.jsonl`, the prompts, the photo, the character sheet and `video.mp4`).
- An optional site-wide login gate (`site_auth/`) for nginx `auth_request`.

## Quick start

```bash
pip install -r requirements.txt
cp config.example.json config.json
cp credential.example.json credential.json   # put your BytePlus Ark API key in it
python app.py                                # http://127.0.0.1:5000
```

The live camera only works over HTTPS or on `localhost`. Uploading a photo works everywhere.

Each generation makes two paid API calls (one image, one video). `daily_limit` caps how many generations the server accepts per day. `access_code`, when set, must be entered on the create page.

## Configuration (`config.json`)

| Key | Meaning |
|---|---|
| `video_model_id`, `image_model_id` | Seedance model or endpoint id, Seedream model id |
| `duration`, `ratio`, `resolution`, `generate_audio` | Video settings (30 s, 9:16, 720p, audio on) |
| `image_size`, `image_style` | Character-sheet size and art style |
| `reference_asset_id` | If set, skip Seedream and send this asset-library face to Seedance directly |
| `history_scope` | `"browser"`: each browser sees only its own videos and photos. `"all"`: everyone with access sees every video, uploaded photo, name and email |
| `daily_limit`, `daily_limit_reset_at` | Generations per day; an ISO time that resets today's count |
| `mail_enabled`, `mail_mode`, `smtp_*`, `mail_from*` | "Video ready" email. `relay` logs in to an SMTP account (password: `smtp_password` in `credential.json`); `direct` delivers to the recipient's MX itself |
| `access_code` | Optional code required to generate |

The config is cached, so restart the app after changing it.

`credential.json` holds `video_maas_api_key` / `image_maas_api_key` (or one `maas_api_key`) and `smtp_password`. It is git-ignored; never commit it.

## Structure

```
app.py              routes, background pipeline (Seedream then Seedance), polling, email
genres.py           the nine genres and the two prompt builders
genre_stats.py      pick counts per genre; the create page shows the most picked genres first
seedance.py         Seedance client + shared endpoint / key / error helpers
seedream.py         Seedream client
project_store.py    _project/<id>/ folders, job.json, logs, daily count
uploads_store.py    face-photo validation
mailer.py           "video ready" email (SMTP relay or direct to MX)
templates/, static/ pages: create, watch, My videos
site_auth/          optional login gate service for nginx auth_request
deploy/             systemd unit, nginx snippet, thumbnail generator, resume script
tests/              offline tests (mocks only, no network, no spend)
```

## Tests

```bash
python tests/test_pipeline.py
python tests/test_http_layer.py
python tests/test_site_auth.py
```

The tests use `config.example.json` and fake keys, so they need no credentials.

## Deploying (outline)

1. Copy the app to the server (e.g. `/opt/drama-app`), create a venv, `pip install -r requirements.txt`, add `config.json` and `credential.json`.
2. Install `deploy/drama-app.service` (gunicorn on 127.0.0.1:8002) and add `deploy/nginx-drama.conf.snippet` to your HTTPS server block. The app is served under `/drama/`.
3. Optional login gate: run `site_auth/make_auth_config.py` to create `auth.json`, install `site_auth/site-auth.service`, and protect the location with `auth_request /_auth/check`.

Generation steps run in a thread of the gunicorn worker. Don't restart the service while a project is `image_generating` or `submitting`, or that run is cut off (`deploy/resume_project.py` can recover it).

Prompts must never contain a double hyphen: the model silently drops everything after it.
