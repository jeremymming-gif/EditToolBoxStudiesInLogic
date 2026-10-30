#!/usr/bin/env python3
"""报告摘要头（B-285a）：四类报告共用的「结论 / 计数 / top5 / 折叠明细」块。

**为什么要有这一层**：编辑打开一份报告，第一件事不是逐条读，而是先回答
「有没有问题？有几处？先看哪几条？」——这三问答完才决定要不要展开。四类报告的
正文形态各不相同（一校核查是规则表、回读校验是分节清单、信息检查是字段清单、
编译报告是日志摘要），但**摘要的读法必须一样**，否则同一批人每天要切换四种习惯。

所以这里只提供**纯函数**：不读文件、不写文件、不取当前时间（时钟可注入），
四类报告各自拼自己的数据，格式由这一处统一。

约定：
* 摘要块插在正文**最前**（`insert()`），旧正文一个字节都不改（`header + old`）——
  这是「旧正文行按原顺序逐行找回」这条验收判据的实现方式。
* 同一输入逐字节确定：同样入参 → 同样返回值，不依赖 `now`、环境变量或哈希顺序。
* `when=` 只给需要时间的报告用；不传则块里**不出现**时间（默认确定）。
"""
from __future__ import annotations

#: 明细折叠块的标题前缀（`<details><summary>全部明细（N 条）</summary>`）
DETAILS_TITLE = "全部明细"
#: 最需处理只给前 5 条（编辑一次能记住的量）
TOP_LIMIT = 5
#: 计数行分隔符（全角竖线，中文报告里比 `|` 更清楚）
_SEP = " ｜ "


def one_line(text: str) -> str:
    """压成一行（摘要里不许有换行把 markdown 结构顶开）。"""
    return " ".join(str(text).replace("\r", " ").replace("\n", " ").split())


def render(
    *,
    conclusion: str,
    counts: tuple[tuple[str, int], ...] | list[tuple[str, int]] = (),
    top: tuple[str, ...] | list[str] = (),
    details: tuple[str, ...] | list[str] = (),
    when: str | None = None,
) -> str:
    """渲染摘要头；返回值**以空行结尾**，可直接拼在正文前。

    * `conclusion`：一句结论（必填；四类报告各自怎么得出这句话由调用方负责）。
    * `counts`：``[(标签, 数字), …]``，按给定顺序打印，不做排序（顺序本身是口径）。
    * `top`：最需处理条目，只取前 :data:`TOP_LIMIT` 条；空也给标题行（写「（无）」）。
    * `details`：全部明细行，逐条放进 ``<details>`` 折叠块。
    * `when`：生成时间；不传则不出现时间行（保证默认逐字节确定）。
    """
    lines: list[str] = [f"**摘要**：{one_line(conclusion)}", ""]
    if when:
        lines += [f"**生成**：{one_line(when)}", ""]
    if counts:
        joined = _SEP.join(f"{one_line(label)} {int(value)}" for label, value in counts)
        lines += [f"**计数**：{joined}", ""]
    lines += ["**最需处理（top 5）**", ""]
    picked = [_one_item(t) for t in list(top)[:TOP_LIMIT]]
    if picked:
        lines += [f"{i}. {t}" for i, t in enumerate(picked, 1)]
    else:
        lines.append("（无）")
    lines += ["", "<details>", f"<summary>{DETAILS_TITLE}（{len(details)} 条）</summary>", ""]
    lines += [f"- {_one_item(d)}" for d in details]
    if not details:
        lines.append("- （无）")
    lines += ["", "</details>", ""]
    return "\n".join(lines) + "\n"


def _one_item(text: str) -> str:
    """单条明细压成一行；过长时截断（明细本来就该短句）。"""
    s = one_line(text)
    return s if len(s) <= 160 else s[:157] + "…"


def insert(body: str, header: str) -> str:
    """把摘要头插到正文最前（旧正文**逐字节**不动）。

    只做一件事：``header + body``。为什么单独给个函数：四类接线和测试都走它，
    「旧正文一字不改」的保证就不会在某一处被手写字符串拼接绕过去。
    """
    return header + body


def split(markdown: str) -> tuple[str, str]:
    """把「摘要头 + 旧正文」拆回两段（给测试/排查用；不参与生产路径）。"""
    marker = "**摘要**："
    if not markdown.startswith(marker):
        return "", markdown
    end = markdown.find("\n\n", markdown.find("</details>"))
    if end < 0:
        return "", markdown
    return markdown[:end + 2], markdown[end + 2:]
