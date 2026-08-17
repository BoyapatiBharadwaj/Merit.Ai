"""Why is no mail arriving?"""
import os
import socket
import ssl
import smtplib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.services import email_service  # noqa: E402

OK = "  OK   "
BAD = " FAIL  "
WARN = " WARN  "


def line(status: str, message: str) -> None:
    print(f"[{status}] {message}")


def fail(message: str, fix: str) -> None:
    line(BAD, message)
    print()
    print("  What to do:")
    for part in fix.strip().split("\n"):
        print(f"    {part}")
    print()
    sys.exit(1)


def main() -> None:
    recipient = sys.argv[1] if len(sys.argv) > 1 else settings.SMTP_USERNAME

    print()
    print("Merit.Ai email diagnostics")
    print("=" * 60)

    # --- 1. What the RUNNING PROCESS believes ---
    # Read from settings, not from the .env file on disk.
    print()
    print("1. Configuration, as this process actually sees it")
    print(f"      EMAIL_ENABLED   = {settings.EMAIL_ENABLED}")
    print(f"      SMTP_HOST       = {settings.SMTP_HOST or '(empty)'}")
    print(f"      SMTP_PORT       = {settings.SMTP_PORT}")
    print(f"      SMTP_USE_TLS    = {settings.SMTP_USE_TLS}")
    print(f"      SMTP_USERNAME   = {settings.SMTP_USERNAME or '(empty)'}")
    password = settings.SMTP_PASSWORD or ""
    print(f"      SMTP_PASSWORD   = {len(password)} characters"
          f"{' -- CONTAINS SPACES' if ' ' in password else ''}"
          f"{' (empty)' if not password else ''}")
    print(f"      EMAIL_FROM      = {settings.EMAIL_FROM or '(empty, falls back to SMTP_USERNAME)'}")
    print(f"      APP_BASE_URL    = {settings.APP_BASE_URL}")
    print()

    if not email_service.is_enabled():
        missing = []
        if not settings.EMAIL_ENABLED:
            missing.append("EMAIL_ENABLED is not true")
        if not settings.SMTP_HOST:
            missing.append("SMTP_HOST is empty")
        if not settings.SMTP_USERNAME:
            missing.append("SMTP_USERNAME is empty")
        fail(
            "Email is DISABLED in this process: " + "; ".join(missing) + ".\n"
            "       Every send is skipped with a log line and no error.",
            """
If your .env already looks right, the container is running with stale values.
`docker compose up -d` does not update the environment of a container that is
already running. Force it to be recreated:

    docker compose up -d --force-recreate core-api scheduler job-worker

Then run this script again.
""",
        )
    line(OK, "Email is enabled and the required settings are present.")

    if " " in password:
        line(WARN, "SMTP_PASSWORD contains spaces. Gmail displays App Passwords in "
                   "four groups of four, but the spaces are for reading only -- "
                   "store the 16 characters with no spaces.")
    elif password and len(password) != 16 and "gmail" in settings.SMTP_HOST.lower():
        line(WARN, f"SMTP_PASSWORD is {len(password)} characters. A Gmail App Password "
                   "is exactly 16. If you pasted your normal account password, "
                   "Gmail will reject it.")

    # --- 2. Can this container reach the internet at all? -------------------
    print()
    print(f"2. Network: can this container open {settings.SMTP_HOST}:{settings.SMTP_PORT}?")
    try:
        with socket.create_connection((settings.SMTP_HOST, settings.SMTP_PORT), timeout=10):
            pass
        line(OK, "TCP connection succeeded.")
    except socket.gaierror:
        fail(
            f"Cannot resolve {settings.SMTP_HOST} -- DNS is not working in this container.",
            """
Check that core-api is attached to a network with outbound access, and that
no `internal: true` was added to it in docker-compose.yml.
""",
        )
    except (OSError, socket.timeout) as exc:
        fail(
            f"Cannot connect to {settings.SMTP_HOST}:{settings.SMTP_PORT} -- {exc}",
            """
Something between this container and the internet is blocking outbound SMTP.
The usual suspects, in order:

  * A corporate or campus firewall blocking outbound port 587. Very common on
    university networks, which is exactly where this software runs.
  * Your ISP blocking 587 or 465 to fight spam.
  * A VPN or proxy that does not route container traffic.

Test from the host to tell the two apart:
    Test-NetConnection smtp.gmail.com -Port 587      (PowerShell)

If the host can connect and the container cannot, it is Docker networking.
If neither can, it is the network you are on -- try a phone hotspot to confirm,
then use an SMTP provider on port 2525 (Mailtrap, Brevo) which is rarely blocked.
""",
        )

    # --- 3. TLS + authentication -------------------------------------------
    print()
    print("3. TLS handshake and login")
    try:
        if settings.SMTP_USE_TLS:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT,
                              timeout=settings.SMTP_TIMEOUT_SECONDS) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                line(OK, "STARTTLS negotiated.")
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                line(OK, f"Authenticated as {settings.SMTP_USERNAME}.")
        else:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT,
                                  timeout=settings.SMTP_TIMEOUT_SECONDS,
                                  context=ssl.create_default_context()) as smtp:
                line(OK, "Implicit TLS connected.")
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                line(OK, f"Authenticated as {settings.SMTP_USERNAME}.")
    except smtplib.SMTPAuthenticationError as exc:
        fail(
            f"Authentication REJECTED by {settings.SMTP_HOST}: {exc.smtp_code} "
            f"{exc.smtp_error.decode(errors='replace') if isinstance(exc.smtp_error, bytes) else exc.smtp_error}",
            """
This is the most common cause of silent non-delivery, and with Gmail it is
almost always one of these:

  * SMTP_PASSWORD is the account password, not an App Password. Gmail refuses
    plain account passwords for SMTP outright.
  * 2-Step Verification is off on the account. App Passwords do not exist
    without it -- turn on 2SV first, then generate one.
  * The App Password was revoked. If it was ever pasted into a chat, an email
    or a commit, revoke and regenerate it anyway.

Generate one at: Google Account -> Security -> 2-Step Verification ->
App passwords. Store the 16 characters with NO spaces.
""",
        )
    except ssl.SSLError as exc:
        fail(
            f"TLS failed: {exc}",
            """
Check SMTP_USE_TLS matches the port:
    port 587 -> SMTP_USE_TLS=true   (STARTTLS)
    port 465 -> SMTP_USE_TLS=false  (implicit TLS)
Using the wrong combination hangs or fails the handshake.
""",
        )
    except (smtplib.SMTPException, OSError) as exc:
        fail(f"SMTP error before sending: {exc}", "See the message above.")

    # --- 4. An actual message ----------------------------------------------
    print()
    print(f"4. Sending a real test message to {recipient}")
    sent = email_service.send(
        to=recipient,
        subject="Merit.Ai email test",
        text_body=(
            "This is a test from scripts/check_email.py.\n\n"
            "If you are reading this, SMTP delivery works and the problem is "
            "elsewhere -- most likely the recipient address, or the mail landing "
            "in spam.\n"
        ),
    )
    if not sent:
        fail("send() returned False even though login succeeded.",
             "Check the core-api logs for the exception it swallowed:\n"
             "    docker compose logs --tail=100 core-api")

    line(OK, f"Message accepted by {settings.SMTP_HOST} for {recipient}.")
    print()
    print("=" * 60)
    print("SMTP works from inside this container.")
    print()
    print("If application email still does not arrive, check in this order:")
    print("  1. The spam folder. New senders land there routinely.")
    print("  2. ADMIN_NOTIFICATION_EMAIL -- where access-request alerts go.")
    print(f"     Currently: {settings.ADMIN_NOTIFICATION_EMAIL or '(empty: falls back to every active admin account)'}")
    print("  3. That you are sending TO a different address than you send FROM.")
    print("     Gmail hides a message you send to yourself from the inbox and files")
    print("     it under 'Sent' -- which looks exactly like non-delivery.")
    print()


if __name__ == "__main__":
    main()
