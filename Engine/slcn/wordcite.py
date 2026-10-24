r"""B-217①：正文「引导语 + 裸编号引用」→ ``\parencite``（判据与脚注通道同源）。

背景（B-215 实测）：Word 稿按印刷体例写「参见文献[4]」「参看[5，6]」
「详见[7]第8–12 页」「引自[8]」——体例正确，但旧实现只认**括号组**里的编号
（``（[4]）``，`docxsource.link_numbered_citations` 的既有分支），这些正文引用
不会自动链成 ``\parencite``，编辑要逐篇补。

本模块提供三样东西（都是**单一判据**，脚注通道与正文通道共用）：

  · ``CUE_LEAD_NEAR_RE`` / ``CUE_LEAD_FAR_RE``：引用引导语证据的正则。
    原先定义在 ``docxsource``（B-130/B-137 脚注通道），B-217 抽到这里，
    ``docxsource.link_footnote_bare_citations`` 反向引用同一组常量——
    **不允许出现第二份引导语判据**（LEAD-LESSONS §4「重复实现零容忍」）；
  · ``cue_evidence(pre)`` / ``cue_evidence_match(pre)``：近/远两种证据的统一入口；
  · ``fn_in_paren_depths`` / ``fn_in_math``：位置判据（括号组内 / ``$…$`` 内），
    两条通道共用；
  · ``link_body_cue_citations()``：正文通道本体。

正文通道判据（保守，宁缺勿编）：
  ① 只换 ``[N]`` / ``[N，M]`` 这类**纯编号**方括号，且必须是裸编号：
     不在中文/英文括号组里（括号组归 ``link_numbered_citations`` 既有分支，避免二次转换）、
     不在 ``$…$`` 公式里、不在 ``\footnote{…}`` 命令参数里（脚注裸编号归
     ``link_footnote_bare_citations``，本通道不越界）；
  ② ``N ∈ num2key``（编号超文末清单范围 → 原样保留 + warning，不猜）；
  ③ ``[N]`` 前 32 字内要有**引用引导语**证据（参见/参看/详见/见/引自/转引自/
     参见文献/参考文献/参照/参考/例如…；判定函数与脚注通道同一份）；
  ④ 引导语与本文编号之间若夹着上一个 ``[N]``（枚举 ``参见[1] [2]`` 的第二处）→ 不转；
  ⑤ 只换编号本身：引导语、页码定位（``第8–12 页``）原样保留；
     英文稿（``lang == "en"``）把 ``p. 33`` / ``pp. 33-40`` 定位写进命令可选参数
     （``\parencite[p.~33]{键}``）——口径与 ``link_numbered_citations`` 的英文分支一致。

反例（不转，且**不改变原文字节**）：
  · 无引导语的裸 ``[1]``、枚举 ``[1] [2]``、公式/数值区间 ``[1,2]``；
  · 已在括号组内（``（见[3]）`` 由既有通道处理，不重复转）；
  · 引导语后跟的是非编号方括号（``[图]``/``[注]``/``[a]``）——正则本身不匹配。
"""

from __future__ import annotations

import re

# ── 引用引导语判据（B-130/B-137 脚注通道原样抽出，唯一一份）─────────────────
#: 引导语紧贴在 `[N]` 前（`参见[12]` / `详见[3]` / `可参考[16]`）
CUE_LEAD_NEAR_RE = re.compile(
    r"(?:参见|参看|详见|另见|又见|亦见|并见|转引自|引自|参见文献|参照|参考|"
    r"参考文献|参考书目|例如)\s*$")
#: 引导语在同一句里用 ≤12 字隔开（`参见吉尔伯特在[13]的批评`）；`见`/`据` 只在
#: 句读/空白边界后算证据（避免「意见[3]」「数据[3]」这类词内误伤）。
CUE_LEAD_FAR_RE = re.compile(
    r"(?:^|[。！？；：，、\s“”「」『』（）()])"
    r"(?:参见|参看|详见|另见|又见|亦见|并见|转引自|引自|参见文献|参照|参考|"
    r"参考文献|参考书目|例如|见|据)"
    r"[^。！？；\n]{0,12}$")


#: B-217① F4：**英文稿**最小引用引导语集合（`see` / `cf.` / `see also` /
#: `cited in` / `e.g.`）。只在 `lang == "en"` 时参与判定——中文稿默认路径一个字不变。
#: 防误报：① 紧贴 `[N]`（`see [4]`）；② ② 引导词与编号之间最多 3 个词
#: （`cited in Smith [4]` 这类不含人名时保守放行，最多 3 词）；③ 冒号后同句
#: （`no parentheses: see [4]`）。无引导语裸 `[4]` / 枚举 / 区间仍不转。
EN_CUE_NEAR_RE = re.compile(
    r"(?:see\ also|see|cited\ in|cf\.?|e\.g\.?,?)\s*$", re.I)
EN_CUE_FAR_RE = re.compile(
    r"(?:^|[.!?;:：,，、\s“”\"'()（）])"
    r"(?:see\ also|see|cited\ in|cf\.?|e\.g\.?,?)"
    r"(?:\s+[A-Za-z0-9.'’\-]+){0,3}\s*$", re.I)


def en_cue_evidence_match(pre: str):
    """英文引导语证据（近处优先）；没有返回 ``None``。"""
    return EN_CUE_NEAR_RE.search(pre) or EN_CUE_FAR_RE.search(pre)


#: 正文通道排除的引导语：正文里的「参考文献」「参考书目」多是**标题/节名**
#: （验收回归：fixture `citation` 的「参考文献」标题 + 未解析清单首行 `[1] …`
#: 被误转成 `\parencite{…}`，默认路径逐字节 FAIL）。脚注通道**保留**这两个词
#: （脚注里「参考文献[12]」是正常引导语），所以只在正文通道过滤。
_BODY_EXCLUDED_CUES = ("参考文献", "参考书目")


def body_cue_evidence_match(pre: str):
    """正文通道的引导语证据：与脚注通道同一份判据，但排除「参考文献/参考书目」。

    实现上不复制第二份正则：命中后若引导语是排除词，把该片段遮掉再判一次
    （遮掉后仍可能在更早位置命中真正的动作型引导语）。
    """
    cur = pre
    for _ in range(3):
        m = cue_evidence_match(cur)
        if m is None:
            return None
        if not any(bad in m.group(0) for bad in _BODY_EXCLUDED_CUES):
            return m
        cur = cur[: m.start()] + "\x00" * (m.end() - m.start()) + cur[m.end():]
    return None


def cue_evidence(pre: str) -> bool:
    """``pre``（``[N]`` 之前的原文）里有没有引用引导语证据。"""
    return bool(CUE_LEAD_NEAR_RE.search(pre) or CUE_LEAD_FAR_RE.search(pre))


def cue_evidence_match(pre: str):
    """返回引导语证据的 ``re.Match``（近处优先），没有则返回 ``None``。"""
    return CUE_LEAD_NEAR_RE.search(pre) or CUE_LEAD_FAR_RE.search(pre)


# ── 位置判据（括号组 / 公式），两条通道共用 ─────────────────────────────────


def fn_in_paren_depths(text: str) -> list[int]:
    """每个字符位置之前的**括号深度**（全角/半角都算）——判 `[N]` 是否被括号包裹。"""
    depth = 0
    out = [0] * (len(text) + 1)
    for i, ch in enumerate(text):
        out[i] = depth
        if ch in "（(":
            depth += 1
        elif ch in "）)" and depth > 0:
            depth -= 1
    out[len(text)] = depth
    return out


def fn_in_math(text: str) -> list[bool]:
    r"""每个字符位置是否在 `$…$` 数学里（``\$`` 是转义美元符，不算分隔符）。

    数学里的 ``[3]`` 是区间/下标之类，**不是**文献编号，绝不能替换
    （否则会产出 ``$\parencite{…}$`` 这种坏 LaTeX）。
    """
    out = [False] * (len(text) + 1)
    in_math = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out[i] = in_math
            out[i + 1] = in_math
            i += 2
            continue
        if ch == "$":
            out[i] = in_math          # 分隔符本身算「外侧」
            in_math = not in_math
        else:
            out[i] = in_math
        i += 1
    out[len(text)] = in_math
    return out


def _command_arg_mask(text: str, command: str) -> list[bool]:
    r"""``\command{…}`` 参数覆盖的字符位置（花括号配平；不配平则不标）。"""
    n = len(text)
    mask = [False] * (n + 1)
    for m in re.finditer(r"\\" + re.escape(command) + r"\s*\{", text):
        i = m.end() - 1
        depth = 0
        for j in range(i, n):
            ch = text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    for k in range(m.start(), j + 1):
                        mask[k] = True
                    break
    return mask


#: 正文里的纯编号方括号：`[4]` / `[5，6]` / `[1, 2]`（与 `_NUM_GROUP` 同形；
#: 不匹配 `[图]`/`[a]`/`[width=…]` 这类非编号方括号）
_BARE_GROUP_RE = re.compile(r"\[\s*(\d{1,3}(?:\s*[，,、;；]\s*\d{1,3})*)\s*\]")

#: 英文定位：`p. 33` / `pp. 33-40` / `Sec. 7.1`（紧跟在 `[N]` 之后才认）
_EN_LOCATOR_RE = re.compile(
    r"[\s,，;；]*(?P<loc>(?:pp?\.?\s*\d{1,5}(?:\s*[-–—~]\s*\d{1,5})?"
    r"|Sec(?:tion)?\.?\s*\d+(?:\.\d+)*"
    r"|chap(?:ter)?\.?\s*\d+))", re.I)


def link_body_cue_citations(
    body: str,
    num2key: dict[int, str],
    lang: str = "zh",
    en_locator=None,
) -> tuple[str, int, list[str]]:
    r"""正文「引导语 + 裸编号」→ ``\parencite``（B-217①）。

    返回 ``(新正文, 转换处数, 问题清单)``。只换 ``[N]`` 本身：

        `参见文献[4]`      → `参见文献\parencite{键}`
        `参看[5，6]`       → `参看\parencite{键5,键6}`
        `详见[7]第8–12 页` → `详见\parencite{键}第8–12 页`
        `引自[8]`          → `引自\parencite{键}`
        `see [4], p. 33`（lang=en）→ `see \parencite[p.~33]{键}`

    不转换（原样保留；越界才记 warning，其余静默——默认路径的日志也不变）：
      · ``[N]`` 在括号组里（``（见[3]）`` 归 ``link_numbered_citations``）；
      · ``[N]`` 在 ``$…$`` 公式里 / 在 ``\footnote{…}`` 参数里；
      · ``[N]`` 前没有引用引导语证据（无引导语裸 ``[1]`` / 枚举 ``[1] [2]`` /
        数值区间 ``[1,2]``）；
      · 引导语与编号间夹着上一个编号（枚举续列，见 ④）。
    """
    if not body or not num2key:
        return body, 0, []
    depths = fn_in_paren_depths(body)
    in_math = fn_in_math(body)
    in_footnote = _command_arg_mask(body, "footnote")
    hi = max(num2key)
    parts: list[str] = []
    pos = 0
    n_done = 0
    issues: list[str] = []
    for m in _BARE_GROUP_RE.finditer(body):
        s, e = m.span()
        if depths[s] > 0 or in_math[s] or in_footnote[s]:
            continue
        nums = [int(x) for x in re.split(r"[，,、;；]", m.group(1)) if x.strip()]
        keys: list[str] = []
        missing = False
        for n in nums:
            k = num2key.get(n)
            if not k:
                missing = True
                break
            if k not in keys:
                keys.append(k)
        if missing:
            snippet = body[max(0, s - 12):min(len(body), e + 12)].replace("\n", " ")
            issues.append(
                f"正文引用编号 {nums} 超出文末清单编号范围（1..{hi}）内，"
                f"已原样保留（不猜）：「…{snippet}…」")
            continue
        pre = body[max(0, s - 32):s]
        cm = body_cue_evidence_match(pre)
        if cm is None and lang == "en":
            cm = en_cue_evidence_match(pre)   # F4：英文稿的最小英文引导语集合
        if not cm:
            continue                     # 无引导语证据：不是引用（默认路径不动）
        if "]" in pre[cm.end():]:
            continue                     # 枚举（`参见[1] [2]` 的第二处）：不继承
        cmd = "\\parencite{" + ",".join(keys) + "}"
        rep_end = e
        if lang == "en" and en_locator is not None:
            lm = _EN_LOCATOR_RE.match(body, e)
            if lm:
                cmd = en_locator(cmd, lm.group("loc"))
                rep_end = lm.end()
        parts.append(body[pos:s])
        parts.append(cmd)
        pos = rep_end
        n_done += 1
    if not n_done:
        return body, 0, issues
    parts.append(body[pos:])
    return "".join(parts), n_done, issues
