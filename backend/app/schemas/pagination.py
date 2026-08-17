"""One pagination shape, used by every list that grows with the number of candidates or violations."""
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 25
# A ceiling the client cannot raise. Without it `?page_size=100000` reproduces
# exactly the unbounded response this exists to prevent, on request.
MAX_PAGE_SIZE = 100


class PageParams:
    """`page` and `page_size` as a FastAPI dependency."""

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
    """The envelope every paginated list returns."""
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
