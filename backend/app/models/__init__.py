"""Import all models here so Alembic autogenerate and Base.metadata.create_all can discover
every mapped class through a single import.
"""
from app.models.user import User, Role
from app.models.organization import ExamParticipant, Organization, OrganizationMember
from app.models.student import Student
from app.models.examiner import Examiner
from app.models.face_profile import FaceProfile
from app.models.exam import Exam, Section
from app.models.question import Question, Option
from app.models.attempt import StudentExamAttempt, StudentAnswer, ExamResult, AttemptReset
from app.models.proctor_event import ProctorEvent
from app.models.access_request import AccessRequest
from app.models.otp import OtpCode, OtpPurpose
from app.models.activity_log import ActivityLog, ActivityType
from app.models.email_outbox import EmailOutbox, EmailOutboxStatus

# job_outbox and rate_limit_counters were Postgres stand-ins for a job queue and shared
# rate-limit counters while this project ran with no Redis.

__all__ = [
    "User", "Role", "Student", "Examiner", "FaceProfile",
    "Organization", "OrganizationMember", "ExamParticipant",
    "Exam", "Section", "Question", "Option",
    "StudentExamAttempt", "StudentAnswer", "ExamResult", "AttemptReset", "ProctorEvent",
    "AccessRequest", "OtpCode", "OtpPurpose", "ActivityLog", "ActivityType",
    "EmailOutbox", "EmailOutboxStatus",
]
