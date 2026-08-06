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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue_tracked(db: Session, *, to: str, subject: str, text_body: str,
                    html_body: str | None = None, max_attempts: int | None = None):
    """Queue one message for reliable delivery, with a durable Postgres record
    that never claims success it didn't earn.

    This is the one path every transactional message in this app goes
    through now -- OTP codes, examiner activation and its resend, exam
    notifications, report-ready emails, and (via
    access_request_service._notify_admins) admin access-request notices.
    `queue()` above is still what it always was, fire-and-forget with no
    delivery record, for the one message that is genuinely disposable (a
    staff login notice: losing one to a mail hiccup is a missed FYI, not a
    broken flow).

    An `email_outbox` row is written FIRST, in this same request's
    transaction, so a crash between writing it and the queue actually
    accepting the job still leaves something a human or a retry can find --
    Postgres is the durable record; Redis/RQ (app/core/queues.py) is only the
    delivery mechanism, and the row's status/attempts/error/sent_at is the
    real, final answer to "did this ever send", not whatever RQ's own job
    state says.

    Returns the created row. The row is `pending` when this function returns
    -- delivery happens on the `worker` service asynchronously, with
    exponential-backoff retries, up to JOB_MAX_RETRIES times (see
    app/worker/jobs.deliver_outbox_email and app/core/queues.py). A caller
    that must know whether delivery ultimately succeeded (there is
    deliberately only one: access_request_service, which must not stamp
    `last_notified_at` on a request nobody was actually told about) uses the
    dedicated `app/worker/jobs.enqueue_access_request_notification` job
    instead of this generic one, precisely because that side effect cannot be
    decided here, before the send has even been attempted.
    """
    from app.repositories import email_outbox_repository
    from app.worker import jobs

    row = email_outbox_repository.create(
        db, to_address=to, subject=subject, text_body=text_body, html_body=html_body,
        max_attempts=max_attempts or settings.EMAIL_OUTBOX_MAX_ATTEMPTS,
    )
    jobs.enqueue_email(row.id)
    return row


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


def activation_message(*, full_name: str, email: str, activation_url: str,
                       expires_hours: int, organization_name: str | None,
                       role_label: str = "examiner") -> tuple[str, str, str]:
    """Sent to a newly created account so its owner can set their own password.

    This replaces a message that mailed the password itself. That older design
    had three problems that no amount of "please change it immediately" fixes:

      1. The password sat in an inbox forever, in plain text, on whatever mail
         providers it passed through -- long after the account was in use.
      2. Whoever created the account knew the password, so "only this person
         could have done that" was never true of anything the account did.
      3. Mailbox compromise handed over a live credential rather than a link
         that expires.

    A link carries the same convenience with none of that: it expires, it is
    single-use, it is stored only as a hash, and the password that ends up on
    the account was chosen by its owner and has never been transmitted.
    """
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
    """Tells a candidate their institution has asked them to verify again.

    The reason is included verbatim and given its own visual block, because a
    demand to re-prove your identity with no explanation attached reads, from
    the receiving end, as either an accusation or a malfunction. Neither is
    what was meant, and both make the person less likely to just go and do it.

    Sent when the request is made rather than discovered at the exam gate: the
    entire value of asking early is that the candidate has time to act. A
    candidate who first learns of this ten minutes before a paper starts has
    effectively been blocked from it.
    """
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
    """Tells a staff member their account was just signed into.

    Only staff. An examiner or admin account can read candidate identity
    photographs, alter results and export personal data, so an unexpected
    sign-in is worth interrupting someone's inbox for. Sending the same for
    every candidate login would produce one email per exam per student, which
    is how a security notice becomes something people filter away.

    Deliberately not blocking and deliberately not fatal: this is a notice, not
    a control. Losing one to a mail outage must never stop somebody signing in.
    """
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
