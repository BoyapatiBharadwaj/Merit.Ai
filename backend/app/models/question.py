"""
Question + Option tables. Each Question belongs to a Section and is either
an MCQ (multiple Options, exactly one correct) or a coding question (a
language, starter code, and a set of stdin/stdout test cases executed in an
isolated Docker container -- see app/services/code_runner_service.py).
"""
import json

from sqlalchemy import Column, Integer, String, Text, ForeignKey, Boolean, func, DateTime
from sqlalchemy.orm import relationship

from app.database.session import Base
from app.models.enums import QuestionType, db_enum


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    section_id = Column(Integer, ForeignKey("sections.id", ondelete="CASCADE"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    marks = Column(Integer, default=1, nullable=False)
    order_index = Column(Integer, default=0, nullable=False)
    question_type = Column(db_enum(QuestionType), default=QuestionType.MCQ, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Coding-question-only fields (null for MCQ questions).
    language = Column(String(20), nullable=True)
    starter_code = Column(Text, nullable=True)
    test_cases_json = Column(Text, nullable=True)  # JSON list of {input, expected_output, is_sample}
    time_limit_seconds = Column(Integer, nullable=True)

    # Optional, shown only on the post-exam report (never during the attempt
    # itself) so a candidate reviewing their result understands *why* an
    # answer was marked correct/incorrect.
    explanation = Column(Text, nullable=True)

    section = relationship("Section", back_populates="questions")
    options = relationship("Option", back_populates="question", cascade="all, delete-orphan")
    answers = relationship("StudentAnswer", back_populates="question", cascade="all, delete-orphan")

    @property
    def test_cases(self) -> list[dict]:
        """Every configured test case (sample + hidden), parsed from JSON.
        Examiner-facing only -- see QuestionOut. The student-facing question
        view filters this down to `is_sample` entries before responding."""
        if not self.test_cases_json:
            return []
        try:
            return json.loads(self.test_cases_json)
        except (ValueError, TypeError):
            return []

    @property
    def sample_test_cases(self) -> list[dict]:
        return [case for case in self.test_cases if case.get("is_sample")]


class Option(Base):
    __tablename__ = "options"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True)
    text = Column(String(500), nullable=False)
    is_correct = Column(Boolean, default=False, nullable=False)

    question = relationship("Question", back_populates="options")
