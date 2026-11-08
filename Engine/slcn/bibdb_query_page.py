"""引用库查询分页元数据（P06 R01/R02）：`page` 字段字典与 `offset` 校验的唯一来源。

纯函数模块，**不 import `bibdb`**（避免 `bibdb → bibdb_query_page` 之类的循环依赖）：
`bibdb.SearchSpec` 只用 `offset_error`，CLI / GUI 只用 `build_page`。

口径（CONTRACT §2）：
- `offset` 0 基，表示跳过多少条**命中记录**（不是库行号）；
- 页内 = 全量命中排序后的 `[offset, offset+count)`；`count` = 本页实际条数；
- `total_matched` = 全量命中总数（切片前）；
- `has_more = offset + count < total_matched`；
- `next_offset = offset + count`（`has_more` 为真），否则 `None`；
- `db_sha256` = 本次查询所读库文件字节的 SHA-256（小写 hex）。
"""
from __future__ import annotations

#: `page` 字典的键（顺序即输出顺序；恰好 6 个）。
PAGE_KEYS: tuple[str, ...] = (
    "offset", "limit", "total_matched", "next_offset", "has_more", "db_sha256",
)


def offset_error(value) -> str | None:
    """校验 `offset`：合法返回 None，非法返回中文错误文本（契约逐字）。

    `bool` 是 `int` 的子类，但不接受（`True`/`False` 不是有意义的偏移量）。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return "offset 必须是整数"
    if value < 0:
        return f"offset 不能为负：{value}"
    return None


def build_page(*, offset: int, limit: int, total_matched: int, count: int,
               db_sha256: str) -> dict:
    """构造 `page` 字典（恰好 `PAGE_KEYS` 六键，顺序同 `PAGE_KEYS`）。

    纯计算、零 I/O；`db_sha256` 由调用方从 `bibdb.db_sha256(path)` 取。
    """
    has_more = offset + count < total_matched
    return {
        "offset": offset,
        "limit": limit,
        "total_matched": total_matched,
        "next_offset": offset + count if has_more else None,
        "has_more": has_more,
        "db_sha256": db_sha256,
    }
