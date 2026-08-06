"""
Durable record of every transactional email the app has tried to send.

This exists because "queue a background task and hope" was never actually
observable: `email_service.queue()` fires a `BackgroundTasks.add_task(send, ...)`
and the caller finds out nothing about what happened after the response has
already gone out. For most of the messages in this app that is fine -- a login
notice or an exam reminder is a courtesy, and losing one silently is an
acceptable failure mode.

It is NOT fine for an examiner access-request notification or an account
activation link, because both gate something the recipient cannot do any other
way: an admin who was never told about a pending request cannot approve it, and
an examiner who never receives the activation link cannot sign in at all. Those
callers need a real answer to "did this actually get delivered", and a bounded,
inspectable way to retry when the answer is no.

Every row here is one message, tracked from the moment delivery is first
attempted:

    pending  -- not yet delivered; eligible for another attempt once
                next_retry_at has passed (NULL means "try immediately")
    sent     -- delivered; `sent_at` records when
    failed   -- exhausted its attempt budget; needs a human to look at
                `last_error` and decide whether to intervene

The row is written BEFORE the first send attempt, not after a failure -- so a
process that crashes mid-send still leaves a `pending` row an operator (or the
worker) can find and retry, rather than losing the message entirely.
"""
from sqlalchemy import Column, DateTime, Integer, String, Text, func

from app.database.session import Base


class EmailOutboxStatus:
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class EmailOutbox(Base):
    __tablename__ = "email_outbox"

    id = Column(Integer, primary_key=True, index=True)
    to_address = Column(String(255), nullable=False, index=True)
    subject = Column(String(255), nullable=False)
    text_body = Column(Text, nullable=False)
    html_body = Column(Text, nullable=True)

    status = Column(String(20), nullable=False, default=EmailOutboxStatus.PENDING, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    # Bounded so a permanently-broken address (typo, full mailbox, a mail
    # server that rejects every message) cannot retry forever and paper over a
    # configuration problem that needs a human.
    max_attempts = Column(Integer, nullable=False, default=5)
    last_error = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)
    # NULL means "due now". Set on failure to an exponentially-backed-off
    # future time, so a mail server having a bad minute is not hammered once a
    # second by the retry loop.
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)
