"""Daily review email, like Readwise's morning digest.

Run from cron / Task Scheduler:  python -m margin digest
SMTP settings come from environment variables (never stored in code):

    MARGIN_SMTP_HOST, MARGIN_SMTP_PORT (default 587), MARGIN_SMTP_USER, MARGIN_SMTP_PASSWORD,
    MARGIN_EMAIL_FROM (default: MARGIN_SMTP_USER), MARGIN_EMAIL_TO, MARGIN_APP_URL (optional link)
"""

import html
import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def build_message(highlights: list[dict], day: str, app_url: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"Your daily highlights — {day}"

    text_parts, html_parts = [], []
    for i, h in enumerate(highlights, 1):
        source = h["title"] + (f" — {h['author']}" if h["author"] else "")
        text_parts.append(f"{i}. \"{h['text']}\"\n   {source}" + (f"\n   Note: {h['note']}" if h["note"] else ""))
        html_parts.append(
            '<div style="margin:0 0 24px;padding:0 0 0 14px;border-left:3px solid #c9a227">'
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


def send_digest(store, dry_run: bool = False) -> int:
    """Email today's review set. Returns the number of highlights included (0 = nothing sent)."""
    highlights = store.daily_review()
    if not highlights:
        log.info("digest: no highlights to send")
        return 0
    msg = build_message(highlights, store.today().isoformat(), os.environ.get("MARGIN_APP_URL", ""))

    if dry_run:
        print(msg.get_body(("plain",)).get_content())
        return len(highlights)

    env = os.environ
    missing = [k for k in ("MARGIN_SMTP_HOST", "MARGIN_EMAIL_TO") if not env.get(k)]
    if missing:
        raise RuntimeError(f"set {', '.join(missing)} to send email (or use --dry-run)")
    msg["From"] = env.get("MARGIN_EMAIL_FROM") or env.get("MARGIN_SMTP_USER", "margin@localhost")
    msg["To"] = env["MARGIN_EMAIL_TO"]

    port = int(env.get("MARGIN_SMTP_PORT", "587"))
    smtp_cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with smtp_cls(env["MARGIN_SMTP_HOST"], port, timeout=30) as smtp:
        if port != 465:
            smtp.starttls()
        if env.get("MARGIN_SMTP_USER"):
            smtp.login(env["MARGIN_SMTP_USER"], env.get("MARGIN_SMTP_PASSWORD", ""))
        smtp.send_message(msg)
    log.info("digest: sent %d highlights to %s", len(highlights), msg["To"])
    return len(highlights)
