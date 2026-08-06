"""
Why is no mail arriving when an examiner requests access?

Run this INSIDE the running container:

    docker compose exec core-api python scripts/check_access_request_email.py

SMTP working (scripts/check_email.py) does not mean THIS path works. Between
"the server can send mail" and "the admin got an email" sit four separate
things that can each silently produce nothing:

  1. The build. If the container predates the re-notification fix, a pending
     request suppresses its own notification permanently.
  2. The de-dupe cooldown. A request notified five minutes ago will not
     re-notify, by design.
  3. The recipient list. With no ADMIN_NOTIFICATION_EMAIL and no admin
     account, the request is recorded and nobody is told.
  4. Gmail's self-send filing. A message from an address TO ITSELF is filed
     under Sent, not Inbox -- indistinguishable from never arriving.

This walks all four and, at the end, actually sends the real notification
inline (not via BackgroundTasks, which would hide the result) and reports what
happened. Read-only apart from that one message and the timestamp it stamps.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.database.session import SessionLocal  # noqa: E402
from app.models.access_request import AccessRequest  # noqa: E402
from app.models.enums import AccessRequestStatus  # noqa: E402
from app.services import access_request_service, email_service  # noqa: E402

OK, BAD, WARN, INFO = "  OK   ", " FAIL  ", " WARN  ", " INFO  "


def line(status, message):
    print(f"[{status}] {message}")


def main():
    print()
    print("Access-request notification diagnostics")
    print("=" * 64)

    # --- 1. Is the fix even in this image? ---------------------------------
    print()
    print("1. Is this container running the re-notification fix?")
    has_fix = hasattr(access_request_service, "_notification_is_due")
    has_column = hasattr(AccessRequest, "last_notified_at")
    if not (has_fix and has_column):
        line(BAD, "No. This image predates the fix.")
        print()
        print("  Before the fix, submit() returned early for ANY pending request and sent")
        print("  nothing -- forever. A request made while email was misconfigured stayed")
        print("  permanently unannounced, and resubmitting did nothing at all.")
        print()
        print("  Rebuild, from the folder that contains this file:")
        print("      docker compose up -d --build core-api")
        print()
        sys.exit(1)
    line(OK, "Yes -- the cooldown replaces the old permanent suppression.")
    line(INFO, f"Cooldown is {settings.ACCESS_REQUEST_RENOTIFY_SECONDS}s "
               f"({settings.ACCESS_REQUEST_RENOTIFY_SECONDS // 60} min).")

    # --- 2. Can this deployment send mail at all? --------------------------
    print()
    print("2. Email delivery")
    if not email_service.is_enabled():
        line(BAD, "Email is disabled in this process.")
        print("\n  Run scripts/check_email.py first -- it diagnoses SMTP specifically.\n")
        sys.exit(1)
    line(OK, "Enabled. (Run scripts/check_email.py if SMTP itself is in doubt.)")

    # --- 3. Who would actually be told? ------------------------------------
    print()
    print("3. Recipients")
    with SessionLocal() as db:
        recipients = access_request_service._admin_recipients(db)

    if not recipients:
        line(BAD, "Nobody. No ADMIN_NOTIFICATION_EMAIL, and no admin account in the database.")
        print("\n  The request is recorded and no one is notified. Set")
        print("  ADMIN_NOTIFICATION_EMAIL in .env, or create an admin account.\n")
        sys.exit(1)

    for r in recipients:
        line(OK, f"would notify: {r}")

    if settings.ADMIN_NOTIFICATION_EMAIL.strip():
        line(INFO, "Source: ADMIN_NOTIFICATION_EMAIL (it overrides the admin accounts).")
    else:
        line(INFO, "Source: the admin accounts in the database (no ADMIN_NOTIFICATION_EMAIL set).")

    # The single most common reason a working send looks like a failure.
    sender = (settings.EMAIL_FROM.strip() or settings.SMTP_USERNAME).strip().lower()
    self_send = [r for r in recipients if r.strip().lower() == sender]
    if self_send:
        line(WARN, f"{self_send[0]} is the SAME address the mail is sent FROM.")
        print()
        print("       Gmail files a message you send to yourself under 'Sent' and")
        print("       'All Mail', usually NOT 'Inbox'. From the inbox this is")
        print("       indistinguishable from the mail never arriving.")
        print()
        print("       Check Sent and All Mail before concluding it failed, or point")
        print("       ADMIN_NOTIFICATION_EMAIL at a different mailbox for testing.")

    # --- 4. What is actually sitting in the queue? -------------------------
    print()
    print("4. Pending requests, and when each was last notified")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        pending = (db.query(AccessRequest)
                   .filter(AccessRequest.status == AccessRequestStatus.PENDING)
                   .order_by(AccessRequest.created_at.desc())
                   .all())

        if not pending:
            line(INFO, "None. Submit one from /request-access, then run this again.")
            print()
            print("=" * 64)
            return

        target = None
        for req in pending:
            last = access_request_service._as_utc(req.last_notified_at)
            if last is None:
                state = "never notified -- the next submission WILL email"
            else:
                elapsed = int((now - last).total_seconds())
                remaining = settings.ACCESS_REQUEST_RENOTIFY_SECONDS - elapsed
                state = (f"notified {elapsed}s ago"
                         + (f"; cooldown has {remaining}s left" if remaining > 0
                            else "; cooldown elapsed, the next submission WILL email"))
            print(f"      #{req.id}  {req.email:<34} {state}")
            if target is None:
                target = req

    # --- 5. Send the real thing, inline, and report ------------------------
    print()
    print(f"5. Sending the real notification for request #{target.id} now")
    print("   (inline, not via BackgroundTasks -- so the outcome is visible)")

    with SessionLocal() as db:
        req = db.query(AccessRequest).get(target.id)
        subject, text, html = email_service.access_request_message(
            first_name=req.first_name, last_name=req.last_name, email=req.email,
            organization_name=req.organization_name, purpose=req.purpose,
        )
        results = []
        for recipient in access_request_service._admin_recipients(db):
            sent = email_service.send(to=recipient, subject=subject,
                                      text_body=text, html_body=html)
            results.append((recipient, sent))
            line(OK if sent else BAD, f"{'accepted by the mail server' if sent else 'REFUSED'}: {recipient}")

        if all(sent for _, sent in results):
            req.last_notified_at = datetime.now(timezone.utc)
            db.commit()

    print()
    print("=" * 64)
    if all(sent for _, sent in results):
        print("The mail server accepted the message.")
        print()
        print("If it still is not in the inbox, it is no longer a sending problem:")
        print("  * Check Spam, and Sent/All Mail if sender and recipient match.")
        print("  * Gmail can delay its own self-addressed mail by a minute or two.")
    else:
        print("The mail server refused it. Run scripts/check_email.py for the reason.")
    print()


if __name__ == "__main__":
    main()
