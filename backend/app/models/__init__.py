"""
Import all models here so Alembic autogenerate and Base.metadata.create_all
can discover every mapped class through a single import.
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

# job_outbox and rate_limit_counters were Postgres stand-ins for a job queue
# and shared rate-limit counters while this project ran with no Redis. Both
# roles are Redis's now (app/core/queues.py, app/core/rate_limit.py) -- see
# alembic/versions/0030_drop_job_and_rate_limit_outbox_tables.py -- so their
# models are gone too. email_outbox stays: it is the permanent delivery
# record (status/attempts/error/sent_at) PostgreSQL is required to keep even
# though the queueing and retry timing that drives it now live in Redis/RQ.

__all__ = [
    "User", "Role", "Student", "Examiner", "FaceProfile",
    "Organization", "OrganizationMember", "ExamParticipant",
    "Exam", "Section", "Question", "Option",
    "StudentExamAttempt", "StudentAnswer", "ExamResult", "AttemptReset", "ProctorEvent",
    "AccessRequest", "OtpCode", "OtpPurpose", "ActivityLog", "ActivityType",
    "EmailOutbox", "EmailOutboxStatus",
]
