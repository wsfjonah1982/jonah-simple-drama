"""Create auth.json from a password read on stdin (so it never lands in shell history or a file).

    echo 'the-password' | python3 make_auth_config.py [auth.json]

Stores only a salted hash plus a random cookie-signing key. Re-running changes the password and
signs everyone out (new key). Keep the file private (chmod 600).
"""
import json
import os
import secrets
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

password = sys.stdin.readline().rstrip("\r\n")
if len(password) < 6:
    sys.exit("Password must be at least 6 characters.")

path = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent / "auth.json")
config = {
    "password_hash": generate_password_hash(password),
    "secret_key": secrets.token_hex(32),
    "session_days": 7,
    "https_only": True,
}
path.write_text(json.dumps(config, indent=2), encoding="utf-8")
try:
    os.chmod(path, 0o600)
except OSError:
    pass
print(f"wrote {path}")
