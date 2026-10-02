"""Outgoing mail for the "video ready" email, in one of two modes (config `mail_mode`):

- "relay" (default): log in to an SMTP account (e.g. Gmail: smtp.gmail.com:465 with an App Password kept
  in credential.json as `smtp_password`) and let it deliver. Reliable, because the provider is trusted.
- "direct": look up the recipient domain's MX hosts and deliver over port 25 ourselves. Big providers
  often refuse this from a VPS IP without a PTR record or that is on a policy blocklist.

Every attempt's outcome is logged by the caller.
"""
import html
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from urllib.parse import parse_qs, urlparse

import dns.exception
import dns.resolver

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
EMAIL_RE = re.compile(rf"^[A-Za-z0-9.!#$%&'*+/=?^_`{{|}}~-]{{1,64}}@{_LABEL}(?:\.{_LABEL})+$")
MAX_NAME_LEN = 80
SMTP_TIMEOUT = 30


class MailError(Exception):
    """permanent=True: the receiving side refused for good (5xx, no such domain); retrying won't help."""

    def __init__(self, message, permanent=False, code=None, mx=None):
        super().__init__(message)
        self.permanent, self.code, self.mx = permanent, code, mx

    def as_dict(self):
        return {"message": str(self), "permanent": self.permanent, "code": self.code, "mx": self.mx}


def clean_email(value):
    """The address, trimmed, or None if it is not a plain user@domain.tld address."""
    value = str(value or "").strip()
    return value if len(value) <= 254 and EMAIL_RE.match(value) else None


def clean_name(value):
    """One line of printable text (no control characters, so no header injection), at most 80 chars."""
    value = "".join(ch for ch in str(value or "") if ch.isprintable())
    return " ".join(value.split())[:MAX_NAME_LEN]


def mask(address):
    """j***@gmail.com, for logs."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


def mx_hosts(domain):
    """MX hosts by preference; the domain itself when it has no MX (RFC 5321 implicit MX)."""
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=15)
    except dns.resolver.NXDOMAIN:
        raise MailError(f"the domain {domain} does not exist", permanent=True)
    except dns.resolver.NoAnswer:
        return [domain]
    except dns.exception.DNSException as exc:
        raise MailError(f"MX lookup for {domain} failed: {exc}")
    hosts = [str(r.exchange).rstrip(".") for r in sorted(answers, key=lambda r: r.preference)]
    hosts = [h for h in hosts if h]
    if not hosts:  # null MX ("."): the domain says it accepts no mail
        raise MailError(f"the domain {domain} accepts no mail (null MX)", permanent=True)
    return hosts


def tos_expiry(url):
    """When a signed TOS/S3-style URL stops working (aware datetime), or None if it can't be read."""
    q = {k.lower(): v[0] for k, v in parse_qs(urlparse(url).query).items()}
    try:
        start = datetime.strptime(q.get("x-tos-date") or q["x-amz-date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return start + timedelta(seconds=int(q.get("x-tos-expires") or q["x-amz-expires"]))
    except (KeyError, ValueError):
        return None


def build_message(sender, sender_name, name, email, title, url, expires_at):
    """The "your video is ready" email: plain text plus a small HTML part with the link."""
    if expires_at:
        local = expires_at.astimezone(timezone(timedelta(hours=8)))
        until = f"until {local:%d %b %Y, %H:%M} (UTC+8)"
    else:
        until = "for about 24 hours"
    msg = EmailMessage()
    msg["Subject"] = f"Your drama is ready: {title}"
    msg["From"] = formataddr((sender_name, sender))
    msg["To"] = formataddr((name, email))
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.partition("@")[2])
    msg.set_content(
        f"Hi {name},\n\n"
        f"Your short drama \"{title}\" is ready. Download it here:\n\n{url}\n\n"
        f"The link works {until}; save the video before then.\n\n"
        "Drama Flow\n"
    )
    e = html.escape
    msg.add_alternative(
        f"<p>Hi {e(name)},</p>"
        f"<p>Your short drama <b>{e(title)}</b> is ready.</p>"
        f'<p><a href="{e(url)}" style="display:inline-block;padding:10px 18px;background:#e5484d;color:#fff;'
        f'text-decoration:none;border-radius:8px">Download your video</a></p>'
        f"<p style=\"color:#666;font-size:13px\">The link works {e(until)}; save the video before then.<br>"
        f'If the button does not work, copy this address:<br><a href="{e(url)}">{e(url)}</a></p>'
        "<p>Drama Flow</p>",
        subtype="html",
    )
    return msg


def deliver(msg, rcpt, cfg, password):
    """Send msg to rcpt the way config.json says (mail_mode "relay" or "direct")."""
    if cfg.get("mail_mode", "relay") == "direct":
        return send(msg, rcpt, cfg["mail_from"], cfg.get("mail_helo", "localhost"))
    if not password:
        raise MailError("no SMTP password: set smtp_password in credential.json", permanent=True)
    return send_relay(msg, rcpt, cfg["mail_from"], cfg["smtp_host"], int(cfg.get("smtp_port", 465)),
                      cfg["smtp_user"], password)


def send_relay(msg, rcpt, sender, host, port, user, password):
    """Hand msg to an SMTP account (implicit TLS on 465, STARTTLS otherwise), certificate verified."""
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=ctx)
        else:
            smtp = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
        with smtp:
            smtp.ehlo()
            if port != 465:
                smtp.starttls(context=ctx)
                smtp.ehlo()
            smtp.login(user, password)
            refused = smtp.send_message(msg, from_addr=sender, to_addrs=[rcpt])
            if refused:
                code, resp = refused[rcpt]
                raise MailError(f"{code} {resp.decode(errors='replace')[:300]}", permanent=code >= 500, code=code, mx=host)
            return {"mx": host, "tls": True, "response": "250 accepted by relay"}
    except smtplib.SMTPAuthenticationError as exc:
        # Wrong/revoked app password: retrying can't fix it, someone has to update credential.json.
        raise MailError(f"SMTP login failed: {exc.smtp_code} {exc.smtp_error.decode(errors='replace')[:200]}",
                        permanent=True, code=exc.smtp_code, mx=host)
    except smtplib.SMTPRecipientsRefused as exc:
        code, resp = next(iter(exc.recipients.values()))
        raise MailError(f"{code} {resp.decode(errors='replace')[:300]}", permanent=code >= 500, code=code, mx=host)
    except smtplib.SMTPResponseException as exc:
        raise MailError(f"{exc.smtp_code} {exc.smtp_error.decode(errors='replace')[:300]}",
                        permanent=exc.smtp_code >= 500, code=exc.smtp_code, mx=host)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailError(f"{type(exc).__name__}: {exc}"[:300], mx=host)


def send(msg, rcpt, sender, helo):
    """Deliver msg to rcpt's MX hosts in order. Returns {"mx", "tls", "response"}; raises MailError."""
    domain = rcpt.rpartition("@")[2]
    last = None
    for host in mx_hosts(domain):
        try:
            with smtplib.SMTP(host, 25, timeout=SMTP_TIMEOUT, local_hostname=helo) as smtp:
                smtp.ehlo()
                tls = False
                if smtp.has_extn("starttls"):
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False  # opportunistic TLS: MX certificates often don't match
                    ctx.verify_mode = ssl.CERT_NONE
                    smtp.starttls(context=ctx)
                    smtp.ehlo()
                    tls = True
                smtp.send_message(msg, from_addr=sender, to_addrs=[rcpt])
                code, resp = smtp.noop()
                return {"mx": host, "tls": tls, "response": f"{code} {resp.decode(errors='replace')[:200]}"}
        except smtplib.SMTPRecipientsRefused as exc:
            code, resp = next(iter(exc.recipients.values()))
            last = MailError(f"{code} {resp.decode(errors='replace')[:300]}", permanent=code >= 500, code=code, mx=host)
        except smtplib.SMTPResponseException as exc:
            last = MailError(f"{exc.smtp_code} {exc.smtp_error.decode(errors='replace')[:300]}",
                             permanent=exc.smtp_code >= 500, code=exc.smtp_code, mx=host)
        except (OSError, smtplib.SMTPException) as exc:
            last = MailError(f"{type(exc).__name__}: {exc}"[:300], mx=host)
        if last.permanent:
            raise last  # a 5xx from one MX is the domain's answer; the backups would say the same
    raise last or MailError(f"no MX host for {domain}")
