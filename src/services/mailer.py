"""Minimal SMTP mailer used to send generated claim PDFs.

Configuration (environment / .env):
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
  SMTP_FROM (defaults to SMTP_USER), SMTP_USE_SSL (true => implicit TLS on 465),
  CLAIM_EMAIL_CC (optional default CC, prefilled in the UI and editable per send),
  CLAIM_EMAIL_BCC (optional, always BCC'd on claim emails, e.g. yourself).
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Iterable, Optional

from dotenv import load_dotenv

load_dotenv()


class MailConfigError(RuntimeError):
    pass


def _split_addresses(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [a.strip() for a in value.replace(";", ",").split(",") if a.strip()]


def mail_config_ok() -> bool:
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER"))


def default_claim_cc() -> str:
    return os.getenv("CLAIM_EMAIL_CC", "").strip()


def send_email_with_attachment(
    *,
    to: Iterable[str] | str,
    subject: str,
    body: str,
    attachment_bytes: bytes,
    attachment_filename: str,
    attachment_mime: tuple[str, str] = ("application", "pdf"),
    cc: Iterable[str] | str | None = None,
    extra_attachments: Iterable[tuple[str, bytes, str]] = (),
) -> dict:
    """Send an email with a primary attachment plus optional (filename, bytes, content_type) extras."""
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    # Gmail app passwords are displayed with spaces; they are not part of the secret
    password = os.getenv("SMTP_PASSWORD", "").replace(" ", "")
    sender = os.getenv("SMTP_FROM", "").strip() or user
    port = int(os.getenv("SMTP_PORT", "587"))
    use_ssl = str(os.getenv("SMTP_USE_SSL", "false")).lower() in ("1", "true", "yes")
    if not host or not sender:
        raise MailConfigError(
            "SMTP is not configured. Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD (and optionally SMTP_FROM/SMTP_PORT) in .env."
        )

    to_list = _split_addresses(to) if isinstance(to, str) else list(to)
    cc_list = _split_addresses(cc) if isinstance(cc, str) else list(cc or [])
    bcc_list = _split_addresses(os.getenv("CLAIM_EMAIL_BCC"))
    if not to_list:
        raise ValueError("At least one recipient is required")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(
        attachment_bytes,
        maintype=attachment_mime[0],
        subtype=attachment_mime[1],
        filename=attachment_filename,
    )
    for name, data, ctype in extra_attachments:
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(
            data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=name,
        )

    recipients = to_list + cc_list + bcc_list
    if use_ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
    try:
        server.ehlo()
        if not use_ssl:
            try:
                server.starttls()
                server.ehlo()
            except smtplib.SMTPNotSupportedError:
                pass
        if user and password:
            server.login(user, password)
        server.send_message(msg, from_addr=sender, to_addrs=recipients)
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return {"from": sender, "to": to_list, "cc": cc_list, "bcc": bcc_list}
