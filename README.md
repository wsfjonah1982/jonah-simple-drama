# Jonah Simple Drama

A small web app that turns a selfie into the lead of a 30-second AI short drama. The visitor takes or uploads a face photo, picks one of nine dramas (optionally adding a one-line story idea; adding more is easy, see [Adding a new drama](#-adding-a-new-drama)), and two BytePlus Ark models work in turn:

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

## ⭐ Adding a new drama

Each drama (genre) is one entry in the `GENRES` list in **`services/genres.py`**. That entry is the whole story: the app builds both AI prompts from it, and the new card appears on the create page automatically. No other code changes are needed.

### 1. Add the entry

Copy this to the end of `GENRES` in `services/genres.py` and fill it in:

```python
    {
        "id": "detective",                 # short, lowercase letters only; used in URLs and file names, never change it later
        "title": "The Last Clue",          # shown on the card, in My videos and in the email subject
        "tagline": "Noir detective mystery",  # one short phrase; also tells Seedream what kind of lead to draw
        "emoji": "🕵️",                     # shown on the card when there is no thumbnail
        "setting": "A rain-soaked 1940s city at night. Black-and-white film noir look, hard shadows, slow smoky camera moves.",
        "costume": "a belted grey trench coat over a dark suit, with a fedora",  # one outfit, kept in every shot
        "beats": [                         # five shots, about 6 s each in a 30 s video
            "[WS, slow push-in] <Subject1> ... (music cue) <sound cue>",
            "[CU, static] <Subject1> ... Dialogue (Subject1, tense): {A line of English dialogue.}",
            "[MS, over the shoulder] <Subject2>, a ..., ...",
            "[MCU, handheld] ...",
            "[WS, slow pull-back] ... Dialogue (Subject1, quietly): {Closing line.} (music fades)",
        ],
    },
```

### 2. Writing rules for the beats

- **`<Subject1>`** is always the visitor (the lead). **`<Subject2>`** is an optional supporting character. Don't add more leads: the video must never show two copies of the visitor.
- Start each beat with a **camera tag** in square brackets: `[WS]` wide, `[MS]` medium, `[MCU]` medium close-up, `[CU]` close-up, `[EWS]` extreme wide, plus a move (`slow push-in`, `static`, `handheld`, `tracking`, ...).
- Dialogue: `Dialogue (Subject1, tone): {line}`. Use short English lines; a 6 s shot fits about one sentence.
- Music cues go in `( )`, sound effects in `< >`.
- Keep it wholesome: no violence shown in detail, no nudity, no real people or brands. Seedance rejects these, and the visitor's run fails.
- **Never write a double hyphen (`--`)** anywhere: the model silently drops everything after it. Also don't write on-screen text, since the prompt asks for none.

### 3. Preview the prompts (free)

```bash
python -c "from services import genres; g = genres.get_genre('detective'); print(genres.build_character_prompt(g, 'Cinematic Realism')); print(); print(genres.build_prompt(g, ''))"
```

This prints exactly what Seedream and Seedance will receive. Then run the tests (`python tests/test_pipeline.py`) to check that nothing broke.

### 4. Add a card thumbnail (optional, one paid image)

Without a thumbnail the card shows the emoji. To make one, add a line for the new id to `SCENES` in `deploy/make_genre_thumbnails.py` (one key moment, a fictional lead), then:

```bash
pip install pillow
python deploy/make_genre_thumbnails.py detective
```

It writes `static/img/genres/detective.jpg` (360x480). The full-size poster goes to `_thumbnail_src/`, which is not committed.

### 5. Ship it

- Restart the app. On a server, deploy `services/genres.py` (and the thumbnail), then restart `drama-app` **only when no project is `image_generating` or `submitting`**.
- A new drama starts with zero picks, so it appears near the end of the grid until visitors choose it (cards are ordered by popularity, see `services/genre_stats.py`).
- The grid is 3 cards wide (2 on phones), so a count that divides by 3 looks tidiest.
- To retire a drama, delete its entry. Old videos of that genre still play and show the title "Drama".

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
services/
  genres.py         the nine genres and the two prompt builders
  genre_stats.py    pick counts per genre; the create page shows the most picked genres first
  data_log.py       anonymous usage events -> _data/events.jsonl
  data_report.py    summary of _data: popularity, failure rate, timings (python -m services.data_report)
  seedance.py       Seedance client + shared endpoint / key / error helpers
  seedream.py       Seedream client
  project_store.py  _project/<id>/ folders, job.json, logs, daily count
  uploads_store.py  face-photo validation
  mailer.py         "video ready" email (SMTP relay or direct to MX)
templates/, static/ pages: create, watch, My videos
site_auth/          optional login gate service for nginx auth_request
deploy/             systemd unit, nginx snippet, thumbnail generator, resume script
tests/              offline tests (mocks only, no network, no spend)
```

## Usage data (`_data/`)

Everything useful for refining the app is collected in **`_data/`** at the project root. It is separate from `_project/`, so deleting videos and photos never deletes it, and it holds **no personal data**: no names, emails, IP addresses, photos, prompts or story text.

| File | What it holds |
|---|---|
| `_data/events.jsonl` | One JSON line per event (below), appended as things happen |
| `_data/genre_counts.json` | How often each drama was picked; orders the cards on the create page |

| Event | Fields |
|---|---|
| `created` | `ref` (a one-way hash of the project id), `genre`, `plot` (whether a story idea was typed), `device` (mobile / desktop), image and video model |
| `rejected` | `reason` (`daily_limit`: a visitor was turned away), `genre` |
| `finished` | `ref`, `genre`, `status` (succeeded / failed), failure `stage` and `code`, `timing` (Seedream, queue, Seedance, end to end), token counts, video size |
| `email` | `ref`, `genre`, `status` (sent / failed), attempts, error code |
| `liked` | `ref`, `genre`, `liked` (true / false), recorded only when the label changes |

Read it with:

```bash
python -m services.data_report                     # tables
python -m services.data_report --since 2026-10-01  # only recent events
python -m services.data_report --json              # the same numbers as JSON
```

The report shows, per drama: times picked, share, succeeded, failed, failure rate and likes. It then lists failure reasons by stage and error code, median and max timings, visitors turned away by the daily limit, email delivery, devices and generations per day. To add a new measurement, call `data_log.record("<event>", ...)` where it happens in `app.py` and count it in `services/data_report.py`.

`_data/` is committed to git, so the history travels with the code. That is safe only because nothing personal is ever written to it: never log names, emails, IPs, photos, prompts, story text or raw project ids (a project id is the URL of a visitor's video, so events store `ref` instead). Check new fields against this rule before adding them; `tests/test_pipeline.py` checks the existing ones.

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
