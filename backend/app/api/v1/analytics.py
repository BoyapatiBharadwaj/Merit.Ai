"""Analytics/reporting endpoints for examiners and admins."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.database.session import get_db
from app.models.user import User
from app.repositories import exam_repository
from app.schemas.analytics import ExamAnalyticsOut, PlatformAnalyticsOut
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/exams/{exam_id}", response_model=ExamAnalyticsOut)
def get_exam_analytics(exam_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    exam = exam_repository.get_exam(db, exam_id)
    if not exam or (user.role.name != "admin" and (not user.examiner_profile or exam.examiner_id != user.examiner_profile.id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found.")
    return analytics_service.exam_analytics(db, exam_id)


@router.get("/platform", response_model=PlatformAnalyticsOut)
def get_platform_analytics(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return analytics_service.platform_analytics(db)
