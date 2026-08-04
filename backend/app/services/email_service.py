"""
Outbound transactional email.

This is the one place in the application that opens an SMTP connection.
Everything else -- OTP delivery, access-request notifications, credential
handoff, exam reminders -- calls `send()` or one of the typed helpers below and
never touches smtplib itself.

Three properties this module guarantees, because every caller depends on them:

**It never raises.** `send()` returns a bool and swallows every exception. A
mail server being down, misconfigured, rate-limiting, or simply not configured
at all must never turn into a 500 on a student pressing "register" or an admin
approving an examiner. Email here is a notification channel layered on top of
flows that already complete successfully in the database; if delivery fails the
flow has still happened, and the failure belongs in the log, not in the user's
face. This mirrors the graceful-degradation pattern already used for the AI
worker (app/ai/ai_worker_client.py) and the code sandbox
(code_runner_service.is_available).

**It never blocks a request.** SMTP handshakes take hundreds of milliseconds on
a good day and can take twenty seconds against a struggling host. Callers pass
FastAPI's `BackgroundTasks` and the send happens after the response is already
on the wire. `send()` is still safe to call synchronously (the tests do), it
just isn't what the endpoints do.

**It is off by default.** With EMAIL_ENABLED false -- the default, and what the
test suite and any fresh clone run with -- `send()` logs the message it would
have sent and returns False. Nothing needs a mail server to run.

On Gmail specifically: SMTP_PASSWORD must be a 16-character App Password, not
the account password. Google rejects plain-password SMTP outright, and App
Passwords are only available once 2-Step Verification is on. The daily cap is
around 500 recipients, which is comfortable for institutional use but is a real
ceiling worth knowing about before an exam-day broadcast.
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from functools import lru_cache
from pathlib import Path

from fastapi import BackgroundTasks

from app.core.config import settings

logger = logging.getLogger("app")


def is_enabled() -> bool:
    """True when this deployment can actually send mail.

    Checked by callers that want to adapt their behaviour rather than fire a
    message into the void -- for example, the OTP endpoints refuse to pretend a
    code was delivered when there is no way to deliver it, because "check your
    inbox" for a mail that cannot arrive is worse than an honest error.
    """
    return bool(settings.EMAIL_ENABLED and settings.SMTP_HOST and settings.SMTP_USERNAME)


def from_address() -> str:
    """The envelope/From address. Defaults to the authenticated SMTP user.

    Gmail silently rewrites a From: that doesn't match the authenticated
    account, so defaulting to SMTP_USERNAME keeps what the recipient sees the
    same as what was actually configured instead of quietly diverging.
    """
    return settings.EMAIL_FROM.strip() or settings.SMTP_USERNAME.strip()


# The brand mark, embedded rather than linked.
#
# A hosted <img src="https://..."> is blocked by default in Gmail, Outlook and
# Apple Mail until the reader clicks "show images", so the letterhead would be a
# broken box for most first-time recipients. A data: URI is worse -- Gmail strips
# those from <img> entirely. An inline CID attachment is the one approach every
# major client renders without asking, because the bytes travel inside the
# message rather than being fetched from a third party.
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
    # The plain-text part is set first and the HTML added as an alternative, so
    # a text-only client gets a genuinely readable message rather than a
    # stripped tag soup. Every template below writes both by hand for that
    # reason -- auto-generating text from HTML produces something noticeably
    # worse, and these messages carry passcodes and credentials that must stay
    # legible in any client.
    message.set_content(text_body)
    if html_body:
        logo = _logo_bytes()
        if logo and f"cid:{LOGO_CID_NAME}" in html_body:
            # The Content-ID header must match what the HTML's src points at,
            # angle brackets and all -- a src of "cid:x" resolves against a
            # header of "<x>". Getting that pairing wrong is the usual reason an
            # inline image shows as a broken box.
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
    """Deliver one message. Returns True only on a successful handshake+send.

    Never raises -- see the module docstring. The recipient address is included
    in failure logs but the body never is: these messages carry passcodes and
    initial credentials, and a log file is a much easier thing to read than a
    mailbox.
    """
    if not is_enabled():
        logger.info("Email disabled; not sending %r to %s (set EMAIL_ENABLED and SMTP_* to enable).", subject, to)
        return False

    message = _build_message(to=to, subject=subject, text_body=text_body, html_body=html_body)

    try:
        if settings.SMTP_USE_TLS:
            # STARTTLS: connect in the clear on 587, then upgrade. The context
            # verifies the server certificate by default, which is the point of
            # using ssl.create_default_context() rather than an unverified one.
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
    """Send after the response has been returned, when a BackgroundTasks is
    available; otherwise send inline.

    The `None` branch exists so service-layer code can be called from places
    with no request context at all -- the exam reminder loop, a management
    script, a test -- without every caller needing to care which it is.
    """
    if background is not None:
        background.add_task(send, to=to, subject=subject, text_body=text_body, html_body=html_body)
        return
    send(to=to, subject=subject, text_body=text_body, html_body=html_body)


# ------------------------------------------------------------------------------
# Templates
#
# Deliberately plain inline HTML with inline styles and no <style> block, no
# external CSS and no images. Mail clients strip stylesheets, block remote
# content by default, and Gmail's clipping kicks in around 102KB -- so the
# robust thing for a message whose entire job is to deliver six digits legibly
# is a table-free, inline-styled layout that degrades to readable text.
# ------------------------------------------------------------------------------

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
    """The Merit.Ai mark: a rounded blue tile with a white check.

    Built from a table cell with a background colour and a text glyph, NOT an
    <img> or inline <svg>. Both of those lose in email: Gmail refuses data: URIs
    on images so an embedded PNG renders as a broken box, a hosted image is
    blocked by default until the reader clicks "show images", and Outlook's Word
    renderer drops inline SVG entirely. A styled cell always paints.

    border-radius is ignored by Outlook, which degrades to a square tile -- an
    acceptable loss, and the reason the mark is a solid colour rather than the
    site's gradient (Outlook would drop the gradient and leave a transparent
    box, which is not).
    """
    if _logo_bytes() is not None:
        # The real mark from app/assets/logo-mark.png, attached inline (see
        # _build_message). Explicit width/height attributes AND matching CSS:
        # Outlook ignores the CSS, everything else prefers it, and without both
        # the 512px source renders at full size in at least one client.
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
    """"Merit" in ink, ".Ai" in brand blue -- the same split the site uses.

    Two spans rather than one coloured string, because the two halves are
    genuinely different colours in the product and a single-colour wordmark in
    email would be the one place the brand is rendered wrong.
    """
    return (f'<span style="font-family:{_FONT};font-size:{font_size}px;font-weight:800;'
            f'letter-spacing:-0.4px;color:{_INK};">Merit'
            f'<span style="color:{_BRAND};">.Ai</span></span>')


def _wrap(title: str, body_html: str, *, preheader: str | None = None) -> str:
    """Standard shell: header with mark + wordmark + tagline, card, footer.

    Table-based throughout. Modern CSS layout (flex, grid) is unusable here --
    Outlook renders mail through Word, which supports neither -- so nested
    tables with inline styles remain the only thing that lays out reliably
    across clients.
    """
    preheader_html = ""
    if preheader:
        # The grey snippet a client shows next to the subject in the inbox list.
        # Hidden in the body itself; without it the client grabs the first
        # visible text, which here is the word "Merit".
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
    """A padded anchor, not a <button>.

    Bulletproof-button techniques (VML for Outlook) would render this more
    consistently, but they triple the markup and Outlook still shows a legible,
    clickable link without them -- the wrong trade for a transactional message
    whose primary content is text.
    """
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


def credentials_message(*, full_name: str, email: str, password: str,
                        organization_name: str | None) -> tuple[str, str, str]:
    """Sent TO a newly approved examiner with their initial credentials.

    The password is in the body because there is nowhere else to put it: this
    account has no other channel yet and no existing password to authenticate a
    reset against. That is why the message tells them to change it immediately
    -- the credential is only as private as their mailbox.
    """
    subject = "Your Merit.Ai examiner account is ready"
    login_url = f"{settings.APP_BASE_URL.rstrip('/')}/login"
    org_line = f"Organization: {organization_name}\n" if organization_name else ""
    text = (
        f"Hello {full_name},\n\n"
        "Your examiner account on Merit.Ai has been approved and is ready to use.\n\n"
        f"Email:    {email}\n"
        f"Password: {password}\n"
        f"{org_line}\n"
        f"Sign in here: {login_url}\n\n"
        "Please change this password as soon as you sign in -- it was sent by email and "
        "should be treated as temporary.\n"
    )
    credential_rows = [("Email", email),
                       ("Password", f'<span style="font-family:ui-monospace,SFMono-Regular,Menlo,'
                                    f'monospace;font-weight:700;">{password}</span>')]
    if organization_name:
        credential_rows.append(("Organization", organization_name))

    html = _wrap(
        "Your examiner account is ready",
        _paragraph(f"Hello {full_name}, your examiner account on Merit.Ai has been approved.")
        + f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
          f'style="background-color:{_PAGE};border:1px solid {_BORDER};border-radius:12px;margin:0 0 22px;">'
          f'<tr><td style="padding:20px 22px;">{_detail_rows(credential_rows).replace("margin:0 0 22px", "margin:0")}</td></tr>'
          f'</table>'
        + _button("Sign in", login_url)
        + f'<p style="margin:22px 0 0;font-family:{_FONT};font-size:13px;line-height:1.6;color:#b45309;">'
          "<strong>Change this password as soon as you sign in.</strong> "
          "It was sent over email, so treat it as temporary.</p>",
        preheader="Your Merit.Ai examiner account has been approved.",
    )
    return subject, text, html


def exam_published_message(*, exam_title: str, examiner_name: str, starts_at: str | None,
                           duration_minutes: int, status: str = "published",
                           reason: str = "published") -> tuple[str, str, str]:
    """Details of one exam, sent to the address the examiner nominated.

    `reason` distinguishes the two moments this fires -- the address being added
    to an exam, and the exam later going live. Same details either way; only the
    framing changes, because "you've been added to an exam that opens next week"
    and "that exam is now live" are different things to tell someone.

    `status` is surfaced explicitly so a draft never reads as if candidates can
    already sit it.
    """
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
