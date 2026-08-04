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

__all__ = [
    "User", "Role", "Student", "Examiner", "FaceProfile",
    "Organization", "OrganizationMember", "ExamParticipant",
    "Exam", "Section", "Question", "Option",
    "StudentExamAttempt", "StudentAnswer", "ExamResult", "AttemptReset", "ProctorEvent",
    "AccessRequest",
]
