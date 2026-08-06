"""
Core user + role tables. Students / Examiners extend this with a 1-1 profile table.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship

from app.database.session import Base


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(20), unique=True, nullable=False, index=True)  # admin/examiner/student

    users = relationship("User", back_populates="role")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    # first/last are the authoritative fields captured at registration.
    # full_name is a denormalized cache of "first last" kept in sync by
    # set_name() -- it stays a real column because the ID-card OCR matcher,
    # PDF certificates, and every existing read path use it directly, and
    # because a stored column can be indexed/searched later if needed.
    first_name = Column(String(75), nullable=False, default="")
    last_name = Column(String(75), nullable=False, default="")
    full_name = Column(String(150), nullable=False)
    email = Column(String(150), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)

    # When this address was proved to belong to whoever holds the account, by
    # entering a code mailed to it. NULL means never verified.
    #
    # Without a column, a verified account and an unverified one were literally
    # indistinguishable: the OTP was checked, the account was created, and
    # nothing recorded that it had happened. So the platform could not display
    # verification state, could not start requiring it later without forcing
    # every existing account through it, and -- the one that matters -- could not
    # notice that an address had been CHANGED after being verified. set_email
    # below clears this, so changing an email drops the verification with it
    # rather than letting the new address inherit the old one's trust.
    email_verified_at = Column(DateTime(timezone=True), nullable=True)

    # What the person agreed to, and which version of it.
    #
    # The registration form's two consent checkboxes were enforced entirely in
    # React and never sent to the server, so a direct API call registered
    # without agreeing to anything -- and even for someone who ticked them,
    # nothing recorded that they had, when, or which wording they saw. For a
    # platform collecting biometric data that is the one consent record that
    # actually matters, and it did not exist.
    terms_accepted_at = Column(DateTime(timezone=True), nullable=True)
    terms_version = Column(String(20), nullable=True)
    proctoring_consent_at = Column(DateTime(timezone=True), nullable=True)

    # The moment the password last changed. Stamped into every token issued
    # afterwards; app/api/deps.py rejects any token minted before it.
    #
    # JWTs carried only sub/role/exp, so they stayed valid until expiry no matter
    # what happened to the account behind them. "Reset your password" is what
    # everyone is told to do when they think they have been compromised, and it
    # did nothing to the attacker's existing token for up to two hours.
    password_changed_at = Column(DateTime(timezone=True), nullable=True)

    # True while the account holds a password its owner never chose.
    #
    # Set when an account is created for somebody else (an approved access
    # request, an admin reset), cleared the moment they set their own. It exists
    # so that "this account did X" means something: while it is true, at least
    # two people can sign in, so nothing the account does is attributable to its
    # owner alone. The login response carries it so the app can route the person
    # to a password change before anything else.
    must_change_password = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    role = relationship("Role", back_populates="users")
    # foreign_keys is required, not optional: students now hold a second FK
    # into users (reverification_requested_by_id, the admin who asked), so
    # "the student profile belonging to this user" is genuinely ambiguous
    # without it -- exactly as it already was for Examiner.user_id below.
    student_profile = relationship("Student", back_populates="user", uselist=False,
                                   cascade="all, delete-orphan", foreign_keys="Student.user_id")
    examiner_profile = relationship("Examiner", back_populates="user", uselist=False, cascade="all, delete-orphan", foreign_keys="Examiner.user_id")

    def set_name(self, first_name: str, last_name: str) -> None:
        """Single place that writes a name, so full_name can never drift out
        of sync with first_name/last_name."""
        self.first_name = (first_name or "").strip()
        self.last_name = (last_name or "").strip()
        self.full_name = f"{self.first_name} {self.last_name}".strip()

    def set_email(self, email: str) -> None:
        """Single place that writes an email, so verification cannot survive it.

        Changing the address invalidates the proof, which was about the OLD one.
        Without this, someone could verify an address they control, switch to
        one they do not, and keep a verified badge on it -- which is exactly the
        manoeuvre email verification exists to prevent.
        """
        new_email = (email or "").strip().lower()
        if new_email != (self.email or "").lower():
            self.email_verified_at = None
        self.email = new_email

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

