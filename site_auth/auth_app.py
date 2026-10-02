"""Site-wide login gate for the server, used through nginx `auth_request`.

nginx asks GET /_auth/check (with the visitor's cookies) before serving a protected location:
204 = let the request through, 401 = nginx redirects the browser to /_auth/login?next=<original uri>.
One shared password, stored only as a hash in auth.json (see make_auth_config.py).
"""
import json
import os
import time
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, abort, redirect, render_template_string, request, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

CONFIG_PATH = Path(os.environ.get("SITE_AUTH_CONFIG", Path(__file__).resolve().parent / "auth.json"))
DEFAULT_NEXT = "/drama/"
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 10 * 60

_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = _cfg["secret_key"]
app.config.update(
    SESSION_COOKIE_NAME="site_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_cfg.get("https_only", True),
    PERMANENT_SESSION_LIFETIME=timedelta(days=_cfg.get("session_days", 7)),
)

# In-memory throttle. Fine because gunicorn runs a single worker process (threads only).
_failures = defaultdict(deque)


def _safe_next(target):
    """Only allow same-site paths, so the login page can't be used as an open redirect."""
    if (not target or not target.startswith("/") or target.startswith("//")
            or "\\" in target or "\n" in target or "\r" in target):
        return DEFAULT_NEXT
    return target


def _throttled(ip):
    now = time.monotonic()
    attempts = _failures[ip]
    while attempts and now - attempts[0] > FAILURE_WINDOW_SECONDS:
        attempts.popleft()
    return len(attempts) >= MAX_FAILURES


def _requires_https_redirect():
    return _cfg.get("https_only", True) and not request.is_secure


LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex">
  <title>Sign in</title>
  <style>
    :root { color-scheme: dark; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 1rem;
           background: #14121a; color: #f2eff8; font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }
    form { width: 100%; max-width: 340px; background: #1f1c2a; border: 1px solid #34304a;
           border-radius: 12px; padding: 1.5rem; }
    h1 { margin: 0 0 0.3rem; font-size: 1.3rem; }
    p { margin: 0 0 1rem; color: #a29db3; font-size: 0.92rem; }
    input[type=password] { width: 100%; padding: 0.7rem; font: inherit; color: inherit; background: #14121a;
                           border: 1px solid #34304a; border-radius: 8px; }
    button { width: 100%; margin-top: 0.9rem; padding: 0.75rem; font: inherit; font-weight: 600; color: #fff;
             background: #e5484d; border: 0; border-radius: 8px; cursor: pointer; }
    .error { color: #ff7a80; margin: 0.8rem 0 0; font-size: 0.92rem; }
  </style>
</head>
<body>
  <form method="post" action="/_auth/login">
    <h1>Please sign in</h1>
    <p>Enter the access password to continue.</p>
    <input type="hidden" name="next" value="{{ next }}">
    <input type="password" name="password" placeholder="Password" autocomplete="current-password" autofocus required>
    <button type="submit">Sign in</button>
    {% if error %}<p class="error" role="alert">{{ error }}</p>{% endif %}
  </form>
</body>
</html>"""


@app.route("/_auth/check")
def check():
    if session.get("ok"):
        return "", 204
    return "", 401


@app.route("/_auth/login", methods=["GET", "POST"])
def login():
    # The password must never travel over plain HTTP.
    if _requires_https_redirect():
        if request.method == "POST":
            abort(400)
        return redirect("https://" + request.host + request.full_path.rstrip("?"))

    if request.method == "GET":
        # `next` is everything after "next=" in the raw query string: nginx passes $request_uri
        # unescaped, so an "&" inside it (e.g. /study?q=a&page=2) must not split the parameter.
        raw = request.query_string.decode("utf-8", "replace")
        target = _safe_next(unquote(raw[5:]) if raw.startswith("next=") else "")
        if session.get("ok"):
            return redirect(target)
        return render_template_string(LOGIN_PAGE, next=target, error=None)

    target = _safe_next(request.form.get("next", ""))
    ip = request.remote_addr or "?"
    if _throttled(ip):
        return render_template_string(LOGIN_PAGE, next=target, error="Too many attempts. Try again in a few minutes."), 429

    if check_password_hash(_cfg["password_hash"], request.form.get("password", "")):
        _failures.pop(ip, None)
        session.clear()
        session["ok"] = True
        session.permanent = True
        return redirect(target)

    _failures[ip].append(time.monotonic())
    return render_template_string(LOGIN_PAGE, next=target, error="Wrong password."), 401


@app.route("/_auth/logout")
def logout():
    session.clear()
    return redirect("/_auth/login")
