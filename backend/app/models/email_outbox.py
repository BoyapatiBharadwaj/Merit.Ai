"""Durable record of every transactional email the app has tried to send."""
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
    # NULL means "due now". Set on failure to an exponentially-backed-off future time, so a mail
    # server having a bad minute is not hammered once a second by the retry loop.
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)
