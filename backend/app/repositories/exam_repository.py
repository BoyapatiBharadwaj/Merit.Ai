"""Data access for Exam, Section, Question, Option tables."""
from datetime import datetime, timezone
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.enums import ExamStatus
from app.models.exam import Exam, Section
from app.models.question import Question, Option


def create_exam(db: Session, examiner_id: int, data: dict) -> Exam:
    exam = Exam(examiner_id=examiner_id, **data)
    db.add(exam)
    db.commit()
    db.refresh(exam)
    return exam


def get_exam(db: Session, exam_id: int) -> Exam | None:
    return db.query(Exam).options(joinedload(Exam.sections).joinedload(Section.questions).joinedload(Question.options)).filter(Exam.id == exam_id).first()


def list_exams_for_examiner(db: Session, examiner_id: int) -> list[Exam]:
    return db.query(Exam).filter(Exam.examiner_id == examiner_id).order_by(Exam.created_at.desc()).all()


def list_all_exams(db: Session) -> list[Exam]:
    return db.query(Exam).order_by(Exam.created_at.desc()).all()



def publish_exam(db: Session, exam: Exam) -> Exam:
    exam.status = ExamStatus.PUBLISHED
    db.commit()
    db.refresh(exam)
    return exam


def update_exam(db: Session, exam: Exam, data: dict) -> Exam:
    """Generic field-setter used by both the full draft edit
    (exam_service.update_exam_details) and the schedule-only edit
    (exam_service.update_exam_schedule) -- callers are responsible for
    validating which fields are safe to include in `data` for the exam's
    current state; this just persists them."""
    for field, value in data.items():
        setattr(exam, field, value)
    db.commit()
    db.refresh(exam)
    return exam


def delete_exam(db: Session, exam: Exam) -> None:
    """Cascades to sections, questions, and options via the relationship's
    cascade="all, delete-orphan" (see Exam.sections in models/exam.py) --
    only callable from exam_service.delete_exam, which has already verified
    the exam is still a DRAFT (and therefore has no attempts/participants of
    real consequence to lose)."""
    db.delete(exam)
    db.commit()


def add_section(db: Session, exam_id: int, data: dict) -> Section:
    section = Section(exam_id=exam_id, **data)
    db.add(section)
    db.commit()
    db.refresh(section)
    return section


def get_section(db: Session, section_id: int) -> Section | None:
    return db.query(Section).filter(Section.id == section_id).first()


def add_question(db: Session, section_id: int, text: str, marks: int, order_index: int, **coding_fields) -> Question:
    question = Question(section_id=section_id, text=text, marks=marks, order_index=order_index, **coding_fields)
    db.add(question)
    db.commit()
    db.refresh(question)
    return question


def add_option(db: Session, question_id: int, text: str, is_correct: bool) -> Option:
    option = Option(question_id=question_id, text=text, is_correct=is_correct)
    db.add(option)
    db.commit()
    db.refresh(option)
    return option


def reorder_questions(db: Session, section_id: int, question_ids: list[int]) -> None:
    questions = {q.id: q for q in db.query(Question).filter(Question.section_id == section_id).all()}
    for index, question_id in enumerate(question_ids):
        if question_id in questions:
            questions[question_id].order_index = index
    db.commit()


def get_question(db: Session, question_id: int) -> Question | None:
    return db.query(Question).options(joinedload(Question.options), joinedload(Question.section)).filter(Question.id == question_id).first()


def replace_question(db: Session, question: Question, text: str, marks: int, order_index: int, question_type: str,
                      language: str | None, starter_code: str | None, test_cases_json: str | None,
                      time_limit_seconds: int | None, explanation: str | None = None) -> Question:
    """Overwrite every editable field on an existing question in place (used
    by the exam builder's "Edit" action) -- a full replace rather than a
    partial patch, since the edit form always resubmits the complete
    question either way. Coding-only fields are passed as None when saving
    an MCQ so switching a question's type never leaves stale data behind."""
    question.text = text
    question.marks = marks
    question.order_index = order_index
    question.question_type = question_type
    question.language = language
    question.starter_code = starter_code
    question.test_cases_json = test_cases_json
    question.time_limit_seconds = time_limit_seconds
    question.explanation = explanation
    db.commit()
    db.refresh(question)
    return question


def replace_options(db: Session, question_id: int, options: list[dict]) -> None:
    """Drop every existing option for this question and insert the new set.
    Simpler and safer than diffing old vs. new rows -- there's no other
    table that references an Option by id (a student's saved MCQ answer
    stores the option id directly, but only questions still open for
    editing -- i.e. still in a draft exam -- can reach this code path, and a
    draft exam by definition has no attempts or saved answers yet)."""
    db.query(Option).filter(Option.question_id == question_id).delete()
    for option in options:
        db.add(Option(question_id=question_id, text=option["text"], is_correct=option["is_correct"]))
    db.commit()


def delete_question(db: Session, question: Question) -> None:
    db.delete(question)
    db.commit()


def delete_section(db: Session, section: Section) -> None:
    """Delete a section and, by cascade, every question and option inside it.

    The cascade is the schema's (questions.section_id is ON DELETE CASCADE, and
    options hang off questions the same way), not something re-implemented here
    -- so this cannot leave orphaned questions behind if the loop it would
    otherwise need were ever to fail halfway.
    """
    db.delete(section)
    db.commit()


def get_all_question_ids_for_exam(db: Session, exam_id: int) -> list[int]:
    return [row[0] for row in db.query(Question.id).join(Section, Question.section_id == Section.id).filter(Section.exam_id == exam_id).order_by(Section.order_index, Question.order_index, Question.id).all()]