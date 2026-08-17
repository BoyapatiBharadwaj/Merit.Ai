"""Business logic for the examiner/admin analytics dashboards."""
from sqlalchemy.orm import Session

from app.repositories import analytics_repository

# Half-open bands: [lower, upper), with the last one closed so 100% has a home.
SCORE_BUCKETS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]


def _bucket_label(lower: float, upper: float, is_last: bool) -> str:
    return f"{lower}-{upper}%" if is_last else f"{lower}-<{upper}%"


def _bucket_scores(percentages: list[float]) -> list[dict]:
    last = len(SCORE_BUCKETS) - 1
    buckets = [{"range": _bucket_label(lo, hi, index == last), "count": 0}
               for index, (lo, hi) in enumerate(SCORE_BUCKETS)]
    for pct in percentages:
        for index, (lo, hi) in enumerate(SCORE_BUCKETS):
            # The final band includes its upper bound; the rest do not, so no
            # value can fall between two bands and no value can be counted twice.
            if lo <= pct < hi or (index == last and pct == hi):
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
