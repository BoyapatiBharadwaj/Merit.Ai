"""Business logic for the examiner/admin analytics dashboards."""
from sqlalchemy.orm import Session

from app.repositories import analytics_repository

SCORE_BUCKETS = [(0, 20), (21, 40), (41, 60), (61, 80), (81, 100)]


def _bucket_scores(percentages: list[float]) -> list[dict]:
    buckets = [{"range": f"{lo}-{hi}%", "count": 0} for lo, hi in SCORE_BUCKETS]
    for pct in percentages:
        for index, (lo, hi) in enumerate(SCORE_BUCKETS):
            if lo <= pct <= hi:
                buckets[index]["count"] += 1
                break
    return buckets


def exam_analytics(db: Session, exam_id: int) -> dict:
    status_counts = analytics_repository.exam_attempt_status_counts(db, exam_id)
    percentages = analytics_repository.exam_result_percentages(db, exam_id)
    avg_pct, min_pct, max_pct, result_count = analytics_repository.exam_score_stats(db, exam_id)
    return {
        "total_attempts": sum(status_counts.values()),
        "attempts_by_status": status_counts,
        "completed_results": result_count or 0,
        "average_percentage": round(avg_pct, 2) if avg_pct is not None else None,
        "min_percentage": round(min_pct, 2) if min_pct is not None else None,
        "max_percentage": round(max_pct, 2) if max_pct is not None else None,
        "score_distribution": _bucket_scores(percentages),
        "violations_by_type": analytics_repository.exam_violation_counts_by_type(db, exam_id),
        "violations_by_severity": analytics_repository.exam_violation_counts_by_severity(db, exam_id),
    }


def platform_analytics(db: Session) -> dict:
    return analytics_repository.platform_counts(db)
