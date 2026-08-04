"""
Organization roster and per-exam access endpoints.

Every route here is scoped to the caller's own organization. An examiner never
passes an organization id -- it is read from their own profile -- so there is
no id for them to tamper with and no cross-tenant read to guard against.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_examiner
from app.database.session import get_db
from app.models.user import User
from app.repositories import exam_repository, user_repository
from app.schemas.organization import (
    ExamAccessOut, ExamParticipantAdd, ExamParticipantAddResult, ExamParticipantOut,
    OrganizationOut, OrganizationStudentOut, RosterEnrolRequest, RosterEnrolResult,
    RosterMemberOut,
)
from app.services import organization_service

router = APIRouter(prefix="/organizations", tags=["Organizations"])


def _caller_organization_id(db: Session, user: User) -> int:
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    if not examiner or examiner.organization_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Your account is not linked to an organization yet. Ask an administrator to assign one.")
    return examiner.organization_id


def _member_out(member) -> RosterMemberOut:
    return RosterMemberOut(
        id=member.id,
        email=member.email,
        student_id=member.student_id,
        student_name=member.student.user.full_name if member.student else None,
        linked_at=member.linked_at,
        created_at=member.created_at,
    )


@router.get("/me", response_model=OrganizationOut)
def my_organization(db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    # Called for its guard clause: raises a clear 400 when the examiner has no
    # organization, rather than returning null and leaving the UI to guess.
    _caller_organization_id(db, user)
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    return examiner.organization


@router.get("/me/roster", response_model=list[RosterMemberOut])
def list_roster(db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    organization_id = _caller_organization_id(db, user)
    return [_member_out(m) for m in organization_service.list_members(db, organization_id)]


@router.post("/me/roster", response_model=RosterEnrolResult, status_code=201)
def enrol_students(payload: RosterEnrolRequest, db: Session = Depends(get_db),
                   user: User = Depends(require_examiner)):
    """Enrol one or many student emails. Idempotent -- re-adding is a no-op."""
    organization_id = _caller_organization_id(db, user)
    return organization_service.enrol_emails(db, organization_id, payload.emails, user.id)


@router.delete("/me/roster/{member_id}", status_code=204)
def remove_student(member_id: int, db: Session = Depends(get_db),
                   user: User = Depends(require_examiner)):
    """Remove someone from the roster, revoking access to future exams.

    Attempts and results they have already produced are deliberately kept --
    removing a student from a cohort must not erase the assessment record.
    """
    organization_id = _caller_organization_id(db, user)
    organization_service.remove_member(db, organization_id, member_id)


@router.get("/me/students", response_model=list[OrganizationStudentOut])
def list_students(db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    """Registered students in this organization -- the exam-restriction picker."""
    organization_id = _caller_organization_id(db, user)
    return [
        OrganizationStudentOut(id=s.id, full_name=s.user.full_name, email=s.user.email,
                               roll_number=s.roll_number)
        for s in organization_service.list_organization_students(db, organization_id)
    ]


# --- per-exam restriction ---------------------------------------------------

def _owned_exam(db: Session, user: User, exam_id: int):
    exam = exam_repository.get_exam(db, exam_id)
    examiner = user_repository.get_examiner_by_user_id(db, user.id)
    if not exam or not examiner or exam.examiner_id != examiner.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")
    return exam


def _exam_access_out(db: Session, exam) -> ExamAccessOut:
    participants = organization_service.list_exam_participants(db, exam.id)
    roster = {m.email for m in organization_service.list_members(db, exam.organization_id)}
    return ExamAccessOut(
        exam_id=exam.id,
        organization_id=exam.organization_id,
        organization_name=exam.organization.name if exam.organization else None,
        restricted=bool(participants),
        participants=[
            ExamParticipantOut(
                id=p.id, email=p.email, student_id=p.student_id,
                student_name=p.student.user.full_name if p.student else None,
                in_organization=p.email in roster,
            )
            for p in participants
        ],
    )


@router.get("/exams/{exam_id}/access", response_model=ExamAccessOut)
def get_exam_access(exam_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_examiner)):
    return _exam_access_out(db, _owned_exam(db, user, exam_id))


@router.post("/exams/{exam_id}/participants", response_model=ExamParticipantAddResult, status_code=201)
def add_exam_participants(exam_id: int, payload: ExamParticipantAdd,
                          db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    """Add emails to this exam's allow-list.

    Additive, and accepts addresses that have not registered yet -- they
    become pending invites that resolve when the person signs up. The first
    email added flips the exam from "open to the organization" to
    "allow-list only".
    """
    exam = _owned_exam(db, user, exam_id)
    return organization_service.add_exam_participants(db, exam, payload.emails, user.id)


@router.delete("/exams/{exam_id}/participants/{participant_id}", status_code=204)
def remove_exam_participant(exam_id: int, participant_id: int,
                            db: Session = Depends(get_db), user: User = Depends(require_examiner)):
    exam = _owned_exam(db, user, exam_id)
    organization_service.remove_exam_participant(db, exam, participant_id)


@router.delete("/exams/{exam_id}/participants", status_code=204)
def clear_exam_participants(exam_id: int, db: Session = Depends(get_db),
                            user: User = Depends(require_examiner)):
    """Clear the allow-list entirely, reopening the exam to the organization."""
    exam = _owned_exam(db, user, exam_id)
    organization_service.clear_exam_participants(db, exam)


# --- admin ------------------------------------------------------------------

@router.get("", response_model=list[OrganizationOut])
def list_all_organizations(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return organization_service.list_organizations(db)
