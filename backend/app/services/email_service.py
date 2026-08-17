"""Outbound transactional email."""
import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from functools import lru_cache
from pathlib import Path

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger("app")


def is_enabled() -> bool:
    """True when this deployment can actually send mail."""
    return bool(settings.EMAIL_ENABLED and settings.SMTP_HOST and settings.SMTP_USERNAME)


def from_address() -> str:
    """The envelope/From address. Defaults to the authenticated SMTP user."""
    return settings.EMAIL_FROM.strip() or settings.SMTP_USERNAME.strip()


# The brand mark, embedded rather than linked.
LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "logo-mark.png"
LOGO_CID_NAME = "meritai-logo"


@lru_cache
def _logo_bytes() -> bytes | None:
    """Read the mark once per process. None if it is missing, in which case the
    templates fall back to the drawn tile -- a missing asset should cost the
    letterhead its polish, never the message its delivery."""
    try:
        return LOGO_PATH.read_bytes()
    except OSError:
        logger.warning("Email logo not found at %s; falling back to the drawn mark.", LOGO_PATH)
        return None


def _build_message(*, to: str, subject: str, text_body: str, html_body: str | None) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.EMAIL_FROM_NAME, from_address()))
    message["To"] = to
    # The plain-text part is set first and the HTML added as an alternative, so a text-only
    # client gets a genuinely readable message rather than a stripped tag soup.
    message.set_content(text_body)
    if html_body:
        logo = _logo_bytes()
        if logo and f"cid:{LOGO_CID_NAME}" in html_body:
            # The Content-ID header must match what the HTML's src points at, angle brackets and
            # all -- a src of "cid:x" resolves against a header of "<x>".
            cid = f"<{LOGO_CID_NAME}>"
            message.add_alternative(html_body, subtype="html")
            # related, not mixed: the image is part of the HTML body, not a
            # separate attachment the reader should see listed at the bottom.
            message.get_payload()[-1].add_related(
                logo, maintype="image", subtype="png", cid=cid,
                filename="merit-ai.png", disposition="inline",
            )
        else:
            message.add_alternative(html_body, subtype="html")
    return message


def send(*, to: str, subject: str, text_body: str, html_body: str | None = None) -> bool:
    """Deliver one message. Returns True only on a successful handshake+send."""
    if not is_enabled():
        logger.info("Email disabled; not sending %r to %s (set EMAIL_ENABLED and SMTP_* to enable).", subject, to)
        return False

    message = _build_message(to=to, subject=subject, text_body=text_body, html_body=html_body)

    try:
        if settings.SMTP_USE_TLS:
            # STARTTLS: connect in the clear on 587, then upgrade.
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT,
                              timeout=settings.SMTP_TIMEOUT_SECONDS) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(message)
        else:
            # Implicit TLS, normally port 465: the socket is encrypted from the
            # first byte, so there is no plaintext phase to upgrade.
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT,
                                  timeout=settings.SMTP_TIMEOUT_SECONDS,
                                  context=ssl.create_default_context()) as smtp:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        # By far the most common real-world failure, and the one with the most
        # specific fix, so it gets its own branch instead of being buried in the
        # generic handler with an opaque "(535, b'...')".
        logger.error(
            "SMTP authentication failed for %s at %s. With Gmail, SMTP_PASSWORD must be a 16-character "
            "App Password (not the account password), and 2-Step Verification must be enabled on the account.",
            settings.SMTP_USERNAME, settings.SMTP_HOST,
        )
        return False
    except (smtplib.SMTPException, ssl.SSLError, OSError):
        logger.exception("Could not send %r to %s via %s:%s", subject, to, settings.SMTP_HOST, settings.SMTP_PORT)
        return False

    logger.info("Sent %r to %s", subject, to)
    return True


def queue(background: BackgroundTasks | None, *, to: str, subject: str,
          text_body: str, html_body: str | None = None) -> None:
    """Send after the response has been returned, when a
    BackgroundTasks is available; otherwise send inline.
    """
    if background is not None:
        background.add_task(send, to=to, subject=subject, text_body=text_body, html_body=html_body)
        return
    send(to=to, subject=subject, text_body=text_body, html_body=html_body)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue_tracked(db: Session, *, to: str, subject: str, text_body: str,
                    html_body: str | None = None, max_attempts: int | None = None):
    """Queue one message for reliable delivery, with a durable
    Postgres record that never claims success it didn't earn.
    """
    from app.repositories import email_outbox_repository
    from app.worker import jobs

    row = email_outbox_repository.create(
        db, to_address=to, subject=subject, text_body=text_body, html_body=html_body,
        max_attempts=max_attempts or settings.EMAIL_OUTBOX_MAX_ATTEMPTS,
    )
    jobs.enqueue_email(row.id)
    return row


# --- - ---
# Templates Deliberately plain inline HTML with inline styles
# and no <style> block, no external CSS and no images.

# Brand palette, matched to the web app rather than approximated.
#   _BRAND     frontend --primary (the ".Ai" in the wordmark, buttons, the mark)
#   _INK       frontend --ink (the "Merit" in the wordmark, headings)
_BRAND = "#2563eb"
_BRAND_DEEP = "#1d4ed8"
_INK = "#0f172a"
_MUTED = "#64748b"
_BORDER = "#e2e8f0"
_PAGE = "#f4f6fb"

_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

# The tagline, matching frontend/src/components/Tagline.jsx. Kept as three words
# with a middot separator rather than a sentence, exactly as the site renders it.
_TAGLINE = "Conduct &nbsp;&middot;&nbsp; Monitor &nbsp;&middot;&nbsp; Evaluate"


def _logo_mark(size: int = 40) -> str:
    """The Merit.Ai mark: a rounded blue tile with a white check."""
    if _logo_bytes() is not None:
        # The real mark from app/assets/logo-mark.png, attached inline (see _build_message).
        return (f'<img src="cid:{LOGO_CID_NAME}" width="{size}" height="{size}" alt="Merit.Ai" '
                f'style="display:block;width:{size}px;height:{size}px;border:0;outline:none;'
                f'text-decoration:none;border-radius:11px;" />')

    return f"""\
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
  <tr>
    <td width="{size}" height="{size}" align="center" valign="middle"
        style="width:{size}px;height:{size}px;background-color:{_BRAND};border-radius:11px;
               text-align:center;vertical-align:middle;">
      <span style="color:#ffffff;font-size:{int(size * 0.55)}px;line-height:{size}px;
                   font-family:{_FONT};font-weight:700;">&#10003;</span>
    </td>
  </tr>
</table>"""


def _wordmark(font_size: int = 22) -> str:
    """"Merit" in ink, ".Ai" in brand blue -- the same split the site uses."""
    return (f'<span style="font-family:{_FONT};font-size:{font_size}px;font-weight:800;'
            f'letter-spacing:-0.4px;color:{_INK};">Merit'
            f'<span style="color:{_BRAND};">.Ai</span></span>')


def _wrap(title: str, body_html: str, *, preheader: str | None = None) -> str:
    """Standard shell: header with mark + wordmark + tagline, card, footer."""
    preheader_html = ""
    if preheader:
        # The grey snippet a client shows next to the subject in the inbox list.
        preheader_html = (
            f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;'
            f'mso-hide:all;font-size:1px;line-height:1px;color:{_PAGE};">{preheader}</div>'
        )

    return f"""\
<body style="margin:0;padding:0;background-color:{_PAGE};">
{preheader_html}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:{_PAGE};padding:32px 12px;">
  <tr>
    <td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0"
             style="width:100%;max-width:560px;">

        <!-- Brand header, outside the card so it reads as letterhead -->
        <tr>
          <td align="center" style="padding:0 0 22px;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td valign="middle" style="padding-right:12px;">{_logo_mark()}</td>
                <td valign="middle">{_wordmark()}</td>
              </tr>
            </table>
            <div style="font-family:{_FONT};font-size:11px;font-weight:700;letter-spacing:1.6px;
                        text-transform:uppercase;color:{_MUTED};padding-top:10px;">
              {_TAGLINE}
            </div>
          </td>
        </tr>

        <!-- Content card -->
        <tr>
          <td style="background-color:#ffffff;border:1px solid {_BORDER};border-radius:16px;padding:36px 34px;">
            <h1 style="margin:0 0 18px;font-family:{_FONT};font-size:20px;line-height:1.35;
                       font-weight:700;color:{_INK};">{title}</h1>
            {body_html}
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td align="center" style="padding:22px 12px 0;">
            <p style="margin:0;font-family:{_FONT};font-size:12px;line-height:1.6;color:{_MUTED};">
              This is an automated message from Merit.Ai — please don't reply to it.
            </p>
            <p style="margin:8px 0 0;font-family:{_FONT};font-size:12px;color:{_MUTED};">
              Secure, AI-proctored online examinations.
            </p>
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
</body>"""


def _paragraph(text: str, *, margin: str = "0 0 16px") -> str:
    return (f'<p style="margin:{margin};font-family:{_FONT};font-size:15px;'
            f'line-height:1.65;color:#334155;">{text}</p>')


def _button(label: str, url: str) -> str:
    """A padded anchor, not a <button>."""
    return (f'<a href="{url}" style="display:inline-block;background-color:{_BRAND};color:#ffffff;'
            f'text-decoration:none;font-family:{_FONT};font-size:15px;font-weight:600;'
            f'padding:13px 26px;border-radius:10px;">{label}</a>')


def _detail_rows(pairs) -> str:
    """Label/value table used by the notification templates."""
    rows = "".join(
        f'<tr>'
        f'<td style="padding:9px 16px 9px 0;font-family:{_FONT};font-size:13px;color:{_MUTED};'
        f'white-space:nowrap;vertical-align:top;">{label}</td>'
        f'<td style="padding:9px 0;font-family:{_FONT};font-size:14px;color:{_INK};'
        f'font-weight:500;">{value}</td>'
        f'</tr>'
        for label, value in pairs
    )
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;margin:0 0 22px;">{rows}</table>')


def _code_block(code: str) -> str:
    """The passcode itself — the entire reason the message exists, so it gets
    the most visual weight on the page."""
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:22px 0;"><tr><td align="center" '
            f'style="background-color:{_PAGE};border:1px solid {_BORDER};border-radius:12px;padding:22px;">'
            f'<span style="font-family:{_FONT};font-size:34px;font-weight:800;letter-spacing:9px;'
            f'color:{_INK};">{code}</span>'
            f'</td></tr></table>')


def otp_message(*, code: str, purpose_label: str, ttl_minutes: int) -> tuple[str, str, str]:
    """Returns (subject, text, html) for a one-time passcode."""
    subject = f"Your Merit.Ai verification code: {code}"
    text = (
        f"Your Merit.Ai verification code is: {code}\n\n"
        f"Use it to {purpose_label}. It expires in {ttl_minutes} minutes and can only be used once.\n\n"
        "If you didn't request this code, you can ignore this email -- nothing has changed on your account.\n"
    )
    html = _wrap(
        "Your verification code",
        _paragraph(f"Use this code to {purpose_label}.")
        + _code_block(code)
        + _paragraph(f"It expires in <strong>{ttl_minutes} minutes</strong> and can only be used once.")
        + f'<p style="margin:18px 0 0;font-family:{_FONT};font-size:13px;line-height:1.6;color:{_MUTED};">'
          "Didn't request this? You can ignore this email &mdash; nothing has changed on your account.</p>",
        preheader=f"Your code is {code}. It expires in {ttl_minutes} minutes.",
    )
    return subject, text, html


def access_request_message(*, first_name: str, last_name: str, email: str,
                           organization_name: str, purpose: str) -> tuple[str, str, str]:
    """Sent TO an administrator when someone asks for an examiner account."""
    name = f"{first_name} {last_name}".strip()
    subject = f"New examiner access request from {name}"
    review_url = f"{settings.APP_BASE_URL.rstrip('/')}/admin/examiners"
    text = (
        f"{name} has requested an examiner account on Merit.Ai.\n\n"
        f"Name:         {name}\n"
        f"Email:        {email}\n"
        f"Organization: {organization_name}\n"
        f"Purpose:      {purpose}\n\n"
        f"Review it here: {review_url}\n"
    )
    html = _wrap(
        "New examiner access request",
        _paragraph(f"<strong>{name}</strong> has asked for an examiner account.")
        + _detail_rows((
            ("Name", name), ("Email", email),
            ("Organization", organization_name), ("Purpose", purpose),
        ))
        + _button("Review request", review_url),
        preheader=f"{name} from {organization_name} requested an examiner account.",
    )
    return subject, text, html


def activation_message(*, full_name: str, email: str, activation_url: str,
                       expires_hours: int, organization_name: str | None,
                       role_label: str = "examiner") -> tuple[str, str, str]:
    """Sent to a newly created account so its owner can set their own password."""
    subject = "Activate your Merit.Ai account"
    org_line = f"Organization: {organization_name}\n" if organization_name else ""
    window = "1 hour" if expires_hours == 1 else f"{expires_hours} hours"
    text = (
        f"Hello {full_name},\n\n"
        f"An {role_label} account has been created for you on Merit.Ai. "
        "Choose a password to activate it.\n\n"
        f"Email: {email}\n"
        f"{org_line}\n"
        f"Activate your account: {activation_url}\n\n"
        f"This link works once and expires in {window}. If it has already expired, "
        "use \"Forgot password\" on the sign-in page to get a new one.\n\n"
        "If you were not expecting this, you can ignore this email -- the account "
        "cannot be used until a password is set.\n"
    )
    rows = [("Email", email)]
    if organization_name:
        rows.append(("Organization", organization_name))

    html = _wrap(
        "Activate your account",
        _paragraph(f"Hello {full_name}, an {role_label} account has been created for you on "
                   "Merit.Ai. Choose a password to activate it.")
        + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
          f'style="background-color:{_PAGE};border:1px solid {_BORDER};border-radius:12px;margin:0 0 22px;">'
          f'<tr><td style="padding:20px 22px;">{_detail_rows(rows).replace("margin:0 0 22px", "margin:0")}</td></tr>'
          f'</table>'
        + _button("Choose a password", activation_url)
        + f'<p style="margin:22px 0 0;font-family:{_FONT};font-size:13px;line-height:1.6;color:{_MUTED};">'
          f"This link works once and expires in {window}. If you were not expecting this, "
          "you can ignore it \u2014 the account cannot be used until a password is set.</p>",
        preheader="Choose a password to activate your Merit.Ai account.",
    )
    return subject, text, html


def reverification_message(*, full_name: str, reason: str | None) -> tuple[str, str, str]:
    """Tells a candidate their institution has asked them to verify again."""
    subject = "Action needed: verify your identity again"
    profile_url = f"{settings.APP_BASE_URL.rstrip('/')}/profile"
    reason_text = (reason or "").strip()
    text = (
        f"Hello {full_name},\n\n"
        "Your institution has asked you to verify your identity again before your next "
        "proctored exam on Merit.Ai.\n\n"
        + (f"Reason given: {reason_text}\n\n" if reason_text else "")
        + "What to do:\n"
        "  1. Sign in and open your Profile page.\n"
        "  2. Register your face again.\n"
        "  3. Capture your ID card again.\n\n"
        f"Profile page: {profile_url}\n\n"
        "Until both steps are complete you will not be able to start a proctored exam, so "
        "please do this before your next one rather than on the day.\n"
    )
    html = _wrap(
        "Verify your identity again",
        _paragraph(f"Hello {full_name}, your institution has asked you to verify your identity "
                   "again before your next proctored exam.")
        + (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
           f'style="background-color:{_PAGE};border:1px solid {_BORDER};border-radius:12px;margin:0 0 22px;">'
           f'<tr><td style="padding:16px 20px;font-family:{_FONT};font-size:14px;line-height:1.6;color:#1f2937;">'
           f'<strong>Reason given:</strong> {reason_text}</td></tr></table>' if reason_text else "")
        + _paragraph("Open your Profile page, register your face again, and capture your ID card "
                     "again. Both steps are needed.")
        + _button("Go to my Profile", profile_url)
        + f'<p style="margin:22px 0 0;font-family:{_FONT};font-size:13px;line-height:1.6;color:#b45309;">'
          "Until both steps are complete you cannot start a proctored exam \u2014 please do this "
          "before your next one rather than on the day.</p>",
        preheader="Your institution has asked you to verify your identity again.",
    )
    return subject, text, html


def staff_login_message(*, full_name: str, role_label: str, when: str,
                        ip: str | None, user_agent: str | None) -> tuple[str, str, str]:
    """Tells a staff member their account was just signed into."""
    subject = "New sign-in to your Merit.Ai account"
    rows = [("When", when), ("Account type", role_label.title())]
    if ip:
        rows.append(("IP address", ip))
    if user_agent:
        rows.append(("Device", user_agent[:120]))

    reset_url = f"{settings.APP_BASE_URL.rstrip('/')}/forgot-password"
    text = (
        f"Hello {full_name},\n\n"
        f"Your Merit.Ai {role_label} account was just signed into.\n\n"
        + "".join(f"{label}: {value}\n" for label, value in rows)
        + f"\nIf this was you, nothing to do.\n"
        f"If it was not, reset your password immediately: {reset_url}\n"
        "Resetting signs out every existing session.\n"
    )
    html = _wrap(
        "New sign-in to your account",
        _paragraph(f"Hello {full_name}, your Merit.Ai {role_label} account was just signed into.")
        + _detail_rows(rows)
        + _paragraph("If this was you, there is nothing to do.")
        + _button("This wasn't me \u2014 reset my password", reset_url)
        + f'<p style="margin:22px 0 0;font-family:{_FONT};font-size:13px;line-height:1.6;color:{_MUTED};">'
          "Resetting your password signs out every existing session.</p>",
        preheader="Your Merit.Ai staff account was just signed into.",
    )
    return subject, text, html


def exam_published_message(*, exam_title: str, examiner_name: str, starts_at: str | None,
                           duration_minutes: int, status: str = "published",
                           reason: str = "published") -> tuple[str, str, str]:
    """Details of one exam, sent to the address the examiner nominated."""
    is_publish = reason == "published"
    subject = (f"Exam published: {exam_title}" if is_publish
               else f"You've been added to an exam: {exam_title}")
    when = starts_at or ("Not scheduled - opens immediately" if is_publish
                         else "Not scheduled yet")
    lead = (f"{exam_title} has been published on Merit.Ai." if is_publish
            else f"You've been added as the contact for {exam_title} on Merit.Ai.")
    status_label = "Published - open to candidates" if status == "published" else "Draft - not yet open"

    text = (
        f"{lead}\n\n"
        f"Exam:     {exam_title}\n"
        f"Examiner: {examiner_name}\n"
        f"Status:   {status_label}\n"
        f"Starts:   {when}\n"
        f"Duration: {duration_minutes} minutes\n\n"
        f"You'll get a reminder shortly before it starts.\n\n"
        f"{settings.APP_BASE_URL.rstrip('/')}/login\n"
    )
    html = _wrap(
        "Exam published" if is_publish else "You've been added to an exam",
        _paragraph(lead)
        + _detail_rows((
            ("Exam", exam_title), ("Examiner", examiner_name),
            ("Status", status_label), ("Starts", when),
            ("Duration", f"{duration_minutes} minutes"),
        ))
        + f'<p style="margin:0;font-family:{_FONT};font-size:13px;line-height:1.6;color:{_MUTED};">'
          "You'll get a reminder shortly before it starts.</p>",
        preheader=f"{exam_title} — {status_label.lower()}, starts {when}.",
    )
    return subject, text, html


def exam_reminder_message(*, exam_title: str, minutes_before: int,
                          starts_at: str | None, duration_minutes: int) -> tuple[str, str, str]:
    """The "starting shortly" nudge, sent once per exam by reminder_service."""
    subject = f"Starting in {minutes_before} minutes: {exam_title}"
    when = starts_at or "shortly"
    text = (
        f"{exam_title} starts in about {minutes_before} minutes.\n\n"
        f"Starts:   {when}\n"
        f"Duration: {duration_minutes} minutes\n\n"
        f"Sign in and get set up now: {settings.APP_BASE_URL.rstrip('/')}/login\n\n"
        "Make sure your camera and microphone are working before the exam opens.\n"
    )
    login_url = f"{settings.APP_BASE_URL.rstrip('/')}/login"
    html = _wrap(
        f"Starting in {minutes_before} minutes",
        _paragraph(f"<strong>{exam_title}</strong> starts in about {minutes_before} minutes.")
        + _detail_rows((("Starts", when), ("Duration", f"{duration_minutes} minutes")))
        + _button("Sign in", login_url)
        + f'<p style="margin:22px 0 0;font-family:{_FONT};font-size:13px;line-height:1.6;color:{_MUTED};">'
          "Check your camera and microphone before the exam opens.</p>",
        preheader=f"{exam_title} starts in about {minutes_before} minutes.",
    )
    return subject, text, html
