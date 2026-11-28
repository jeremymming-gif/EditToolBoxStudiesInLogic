"""Pure page-range planning for an ordered journal issue."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


class PagePlanError(ValueError):
    """The measured article sequence cannot form a valid page plan."""


@dataclass(frozen=True)
class MeasuredArticle:
    folder: str
    count: int


@dataclass(frozen=True)
class PageRange:
    folder: str
    first: int
    last: int
    count: int


def plan_pages(articles: Sequence[MeasuredArticle]) -> tuple[PageRange, ...]:
    """Assign contiguous body page ranges, always starting with page one."""
    result: list[PageRange] = []
    next_page = 1
    seen: set[str] = set()
    for article in articles:
        if not isinstance(article.folder, str) or not article.folder:
            raise PagePlanError("篇目目录名为空")
        if article.folder in seen:
            raise PagePlanError(f"篇目目录重复：{article.folder}")
        seen.add(article.folder)
        count = article.count
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise PagePlanError(f"篇目页数必须为正整数：{article.folder}")
        last = next_page + count - 1
        result.append(PageRange(article.folder, next_page, last, count))
        next_page = last + 1
    return tuple(result)
