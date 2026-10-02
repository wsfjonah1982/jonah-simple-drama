import os, subprocess, sys, tempfile
from pathlib import Path

SA = Path(__file__).resolve().parent.parent / "site_auth"
tmp = Path(tempfile.mkdtemp(prefix="auth_smoke_"))
cfg = tmp / "auth.json"
subprocess.run([sys.executable, str(SA / "make_auth_config.py"), str(cfg)], input="testpw1\n", text=True, check=True, capture_output=True)
short = subprocess.run([sys.executable, str(SA / "make_auth_config.py"), str(tmp / "x.json")], input="abc\n", text=True, capture_output=True)
os.environ["SITE_AUTH_CONFIG"] = str(cfg)
sys.path.insert(0, str(SA))
import auth_app

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name, extra)
    if not cond: sys.exit(1)

check("short password rejected", short.returncode != 0)
check("hash only, no plaintext", "testpw1" not in cfg.read_text() and "scrypt" in cfg.read_text() or "pbkdf2" in cfg.read_text())

S = "https://drama.example.com"
c = auth_app.app.test_client()
check("check 401 when anonymous", c.get("/_auth/check", base_url=S).status_code == 401)

r = c.get("/_auth/login?next=/study?q=a&page=2", base_url=S)
check("login page", r.status_code == 200 and b'value="/study?q=a&amp;page=2"' in r.data, "")
check("open redirect blocked", b'value="/drama/"' in c.get("/_auth/login?next=//evil.com/x", base_url=S).data
      and b'value="/drama/"' in c.get("/_auth/login?next=https://evil.com", base_url=S).data
      and b'value="/drama/"' in c.get("/_auth/login?next=/\\evil.com", base_url=S).data)

r = c.get("/_auth/login?next=/drama/", base_url="http://drama.example.com")
check("http login GET -> https", r.status_code == 302 and r.headers["Location"] == "https://drama.example.com/_auth/login?next=/drama/", r.headers.get("Location"))
check("http login POST refused", c.post("/_auth/login", data={"password": "testpw1"}, base_url="http://drama.example.com").status_code == 400)

r = c.post("/_auth/login", data={"password": "wrong", "next": "/drama/"}, base_url=S)
check("wrong password 401", r.status_code == 401 and b"Wrong password" in r.data)
check("still anonymous", c.get("/_auth/check", base_url=S).status_code == 401)

r = c.post("/_auth/login", data={"password": "testpw1", "next": "/study?q=a&page=2"}, base_url=S)
cookie = r.headers.get("Set-Cookie", "")
check("login ok -> redirect next", r.status_code == 302 and r.headers["Location"] == "/study?q=a&page=2", r.headers.get("Location"))
check("cookie flags", all(x in cookie for x in ("site_session=", "HttpOnly", "Secure", "SameSite=Lax", "Expires=")), cookie[:120])
check("check 204 when logged in", c.get("/_auth/check", base_url=S).status_code == 204)
check("login page skips when authed", c.get("/_auth/login?next=/drama/", base_url=S).headers["Location"] == "/drama/")
r = c.post("/_auth/login", data={"password": "testpw1", "next": "https://evil.com"}, base_url=S)
check("post-login next sanitised", r.headers["Location"] == "/drama/")

c.get("/_auth/logout", base_url=S)
check("logout -> anonymous", c.get("/_auth/check", base_url=S).status_code == 401)

# forged cookie
c2 = auth_app.app.test_client()
c2.set_cookie("site_session", "eyJvayI6dHJ1ZX0.aaaaaa.bbbbbbbbbbbbbbbbbbbbbbbbbbb", domain="drama.example.com")
check("forged cookie rejected", c2.get("/_auth/check", base_url=S).status_code == 401)

# throttle: 5 failures then even the right password is refused
c3 = auth_app.app.test_client()
for i in range(5):
    c3.post("/_auth/login", data={"password": f"bad{i}"}, base_url=S, environ_base={"REMOTE_ADDR": "9.9.9.9"})
r = c3.post("/_auth/login", data={"password": "testpw1"}, base_url=S, environ_base={"REMOTE_ADDR": "9.9.9.9"})
check("throttled after 5 failures", r.status_code == 429)
r = c3.post("/_auth/login", data={"password": "testpw1"}, base_url=S, environ_base={"REMOTE_ADDR": "8.8.8.8"})
check("other IP unaffected", r.status_code == 302)
print("ALL OK")
