"""Daily review email, like Readwise's morning digest.

Set up with `python -m margin setup-email`, send with `python -m margin digest`.
Settings come from environment variables, falling back to ~/.margin/email.env:

    MARGIN_SMTP_HOST, MARGIN_SMTP_PORT (default 587), MARGIN_SMTP_USER, MARGIN_SMTP_PASSWORD,
    MARGIN_EMAIL_FROM (default: MARGIN_SMTP_USER), MARGIN_EMAIL_TO, MARGIN_APP_URL (optional link)

The password is never written to the file: it comes from MARGIN_SMTP_PASSWORD or the macOS Keychain.
"""

import html
import logging
import os
import shutil
import smtplib
import ssl
import subprocess
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

log = logging.getLogger(__name__)


# The bar beside each highlight matches its colour in the reader.
COLOR_HEX = {"yellow": "#c9a227", "green": "#4caf50", "blue": "#3b82f6", "pink": "#e84a8a",
             "purple": "#8b5cf6", "underline": "#e5484d"}


def build_message(highlights: list[dict], day: str, app_url: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"Your daily highlights — {day}"

    text_parts, html_parts = [], []
    for i, h in enumerate(highlights, 1):
        source = h["title"] + (f" — {h['author']}" if h["author"] else "")
        text_parts.append(f"{i}. \"{h['text']}\"\n   {source}" + (f"\n   Note: {h['note']}" if h["note"] else ""))
        html_parts.append(
            '<div style="margin:0 0 24px;padding:0 0 0 14px;border-left:3px solid '
            f'{COLOR_HEX.get(h.get("color"), COLOR_HEX["yellow"])}">'
            f'<p style="font-size:17px;line-height:1.5;margin:0 0 6px">{html.escape(h["text"])}</p>'
            f'<p style="color:#666;font-size:13px;margin:0">{html.escape(source)}</p>'
            + (f'<p style="font-size:14px;margin:6px 0 0"><b>Note:</b> {html.escape(h["note"])}</p>' if h["note"] else "")
            + "</div>")

    footer = f"\n\nReview them and choose when to see each again: {app_url}" if app_url else ""
    msg.set_content("\n\n".join(text_parts) + footer)
    link = f'<p><a href="{html.escape(app_url)}">Open your daily review</a></p>' if app_url else ""
    msg.add_alternative(
        '<div style="font-family:Georgia,serif;max-width:600px;margin:auto;color:#222">'
        f'<h2 style="font-weight:normal">Your daily highlights</h2>{"".join(html_parts)}{link}</div>',
        subtype="html")
    return msg


KEYCHAIN_SERVICE = "margin-smtp"
CONFIG_KEYS = ("MARGIN_SMTP_HOST", "MARGIN_SMTP_PORT", "MARGIN_SMTP_USER", "MARGIN_EMAIL_FROM",
               "MARGIN_EMAIL_TO", "MARGIN_APP_URL")


def read_env_file(path: Path) -> dict:
    """Parse simple KEY=value lines; blank lines and # comments are ignored."""
    config = {}
    if path and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip()
    return config


def write_env_file(path: Path, config: dict):
    """Write non-secret settings only. The file is private to your user account."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Margin email settings (the password is kept in the macOS Keychain, not here)"]
    lines += [f"{k}={config[k]}" for k in CONFIG_KEYS if config.get(k)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def keychain_password(account: str) -> str:
    """Look up the SMTP password in the macOS Keychain. Returns '' if not on a Mac or not stored."""
    if not account or not shutil.which("security"):
        return ""
    result = subprocess.run(
        ["security", "find-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def load_config(config_file: Path | None = None) -> dict:
    """Environment variables win over the config file; the password comes from env or the Keychain."""
    config = read_env_file(config_file) if config_file else {}
    config.update({k: v for k, v in os.environ.items() if k.startswith("MARGIN_") and v})
    if not config.get("MARGIN_SMTP_PASSWORD"):
        config["MARGIN_SMTP_PASSWORD"] = keychain_password(config.get("MARGIN_SMTP_USER", ""))
    return config


def check_host(host: str) -> str:
    """Reject things like "smtp.gmail.com, port 587": the server name only, the port has its own setting."""
    host = host.strip()
    if not host or any(c in host for c in " ,:/@"):
        raise ValueError(f"SMTP server should be just a name like smtp.gmail.com, got {host!r}; "
                         "the port goes in MARGIN_SMTP_PORT")
    return host


def is_configured(config: dict) -> bool:
    return bool(config.get("MARGIN_SMTP_HOST") and config.get("MARGIN_EMAIL_TO"))


def send(msg: EmailMessage, config: dict):
    missing = [k for k in ("MARGIN_SMTP_HOST", "MARGIN_EMAIL_TO") if not config.get(k)]
    if missing:
        raise RuntimeError(f"email isn't set up ({', '.join(missing)} missing). "
                           "Run: python3 -m margin setup-email")
    sender = config.get("MARGIN_EMAIL_FROM") or config.get("MARGIN_SMTP_USER") or "margin@localhost"
    msg["From"] = sender
    msg["To"] = config["MARGIN_EMAIL_TO"]
    # smtplib doesn't add these; spam filters penalise mail without them.
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or "localhost")
    port = int(config.get("MARGIN_SMTP_PORT") or 587)
    try:
        host = check_host(config["MARGIN_SMTP_HOST"])
    except ValueError as e:
        raise RuntimeError(f"{e}. Fix it in ~/.margin/email.env or run setup-email again")
    # smtplib skips certificate checks unless given a context; verify so the password only goes to the real server.
    tls = ssl.create_default_context()
    try:
        if port == 465:
            connection = smtplib.SMTP_SSL(host, port, timeout=30, context=tls)
        else:
            connection = smtplib.SMTP(host, port, timeout=30)
        with connection as smtp:
            if port != 465:
                smtp.starttls(context=tls)
            if config.get("MARGIN_SMTP_USER"):
                smtp.login(config["MARGIN_SMTP_USER"], config.get("MARGIN_SMTP_PASSWORD", ""))
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise RuntimeError("the mail server rejected the username/password. Gmail and Microsoft 365 "
                           "need an app password; run setup-email again to update it")
    except ssl.SSLCertVerificationError as e:
        raise RuntimeError(f"{host}'s security certificate couldn't be verified ({e.verify_message}); "
                           "check the server name")
    except (smtplib.SMTPException, OSError) as e:
        raise RuntimeError(f"couldn't send email via {host}:{port}: {e}")


def send_digest(store, dry_run: bool = False, config: dict | None = None) -> int:
    """Email today's review set. Returns the number of highlights included (0 = nothing sent)."""
    config = load_config() if config is None else config
    highlights = store.daily_review()
    if not highlights:
        log.info("digest: no highlights to send")
        return 0
    msg = build_message(highlights, store.today().isoformat(), config.get("MARGIN_APP_URL", ""))
    if dry_run:
        print(msg.get_body(("plain",)).get_content())
        return len(highlights)
    send(msg, config)
    log.info("digest: sent %d highlights to %s", len(highlights), msg["To"])
    return len(highlights)
