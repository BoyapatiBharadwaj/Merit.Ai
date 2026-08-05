"""
One pagination shape, used by every list that grows with the number of
candidates or violations.

The lists were returned whole. `GET /attempts/exam/{id}` sent every attempt an
exam had ever had, `GET /proctoring/events/exam/{id}` sent every violation, and
the browser sliced them for display -- so the cost of viewing page 1 of an exam
was the cost of transferring all of it. A hall of 500 candidates producing a
dozen events each is 6,000 rows, serialised, sent, parsed and held in memory, to
render 25. Several examiners refreshing that during a live sitting is the same
query repeatedly against the database it is already busiest serving.

Deliberately offset-based rather than cursor-based. These lists are browsed by
people jumping to a page and sorting by a column, which cursors do not support;
the deep-offset cost cursors exist to avoid needs page 400, and an exam does not
have 10,000 attempts. The violation feed, which genuinely can, is the one worth
revisiting if it ever becomes a problem.
"""
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 25
# A ceiling the client cannot raise. Without it `?page_size=100000` reproduces
# exactly the unbounded response this exists to prevent, on request.
MAX_PAGE_SIZE = 100


class PageParams:
    """`page` and `page_size` as a FastAPI dependency.

    A class rather than two loose Query parameters so an endpoint declares one
    argument and every list shares the same bounds -- the limit is enforced in
    one place instead of being re-typed per route and eventually mistyped.
    """

    def __init__(
        self,
        page: int = Query(1, ge=1, description="1-based page number."),
        page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    ):
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page(BaseModel, Generic[T]):
    """The envelope every paginated list returns.

    `total` is included because the UI needs to render "1,280 candidates" and
    build a page selector, and neither is derivable from a page of rows. It costs
    one COUNT per request, which is the price of the client not having to
    download everything to find out how much there is.
    """
    items: list[T]
    page: int
    page_size: int
    total: int
    total_pages: int


def build_page(items: list, total: int, params: PageParams) -> dict:
    total_pages = (total + params.page_size - 1) // params.page_size if total else 0
    return {
        "items": items,
        "page": params.page,
        "page_size": params.page_size,
        "total": total,
        "total_pages": total_pages,
    }
