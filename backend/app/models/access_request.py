"""
Examiner access requests.

Examiners never self-register -- an admin creates their account (see
auth_service.create_examiner). That leaves a gap for an institution that
wants in: this table is the front door. A prospective examiner submits their
details from the public marketing site, an admin reviews the request, and
approving it creates the real examiner account.

Kept deliberately separate from the users table: a request is not an account,
carries no credentials, and most requests may never become one.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship

from app.database.session import Base
from app.models.enums import AccessRequestStatus, db_enum


class AccessRequest(Base):
    __tablename__ = "access_requests"

    id = Column(Integer, primary_key=True, index=True)

    first_name = Column(String(75), nullable=False)
    last_name = Column(String(75), nullable=False)
    email = Column(String(150), nullable=False, index=True)
    organization_name = Column(String(150), nullable=False)
    purpose = Column(Text, nullable=False)

    status = Column(db_enum(AccessRequestStatus), default=AccessRequestStatus.PENDING, nullable=False, index=True)

    # Set when an admin acts on the request, so the review trail survives even
    # after the resulting account is edited or deactivated.
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    review_note = Column(String(255), nullable=True)
    created_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    reviewed_by = relationship("User", foreign_keys=[reviewed_by_id])
    created_user = relationship("User", foreign_keys=[created_user_id])

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()
