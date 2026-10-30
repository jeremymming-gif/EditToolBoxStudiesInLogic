"""标准 LaTeX 前言（普通 article）元数据解析（B-276）。

纯函数、无 IO、无全局状态：给一段**主文件** `.tex` 文本，解析前面板的
title / first author / affiliation / email / abstract / keywords / thanks / grants，
供 ④ 输出名回退（`naming.py`）与 ③ 元数据搬运（`build.py`，第二阶段接线）复用。
识别不了返回 `None` 或空串，**不猜**。

口径（spec `drafts/specs/b276-tex-frontmatter.md` §3 + B-275 限界补充）
--------------------------------------------------------------------
- 只读**主文件**：`\\input` 不展开（不递归、不读磁盘）。
- 先 `latex.strip_comments()` 再解析——注释掉的 `\\title`/`\\author` 不算。
- 括号一律用 `latex.match_brace()` 配平，支持嵌套花括号与嵌套 `\\thanks`。
- 作者切分（顶层）：`\\and`、`\\\\`、`\\quad`/`\\qquad`、`,` `，` `、` `;` `；`、
  字面 ` and `；取第一段；剥 `\\thanks`/`\\footnote`/上标/字体命令后过
  `naming.looks_like_person_name()` 与单位词闸门；拿不准返回空串
  （由上层报错要求 `--author`/`--name`）。
- B-275 限界：`\\begin{document}` **前后均扫描**——作者写在正文里也作回退；
  abstract/keywords 实际也常写在 `\\begin{document}` 之后（普通 article 的
  `\\begin{abstract}` 就在正文开头）。
- 空值 / `null` / 纯空白 = 无值；无法分类的 `\\thanks` 入 `thanks`（不丢信息）。
- 不做基金号提取、不做人名猜测、不推导英文翻译、不按语种硬编码。

模板槽位映射见 `TEMPLATE_FIELDS` / `TexFrontmatter.template_values()`：
`mytitle` / `myrunningtitle` / `myfirstauthor` / `myfirstaffiliation` /
`myfirstemail` / `myabstract` / `mykeywords` / `mythanks` / `mygrants`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields

from .latex import match_brace, strip_comments

# ── 源字段 → 模板槽位（spec §2 填槽表；单一出处，调用方不要再抄一份）──────────
TEMPLATE_FIELDS: dict[str, str] = {
    "title": "mytitle",
    "running_title": "myrunningtitle",
    "author": "myfirstauthor",
    "affiliation": "myfirstaffiliation",
    "email": "myfirstemail",
    "abstract": "myabstract",
    "keywords": "mykeywords",
    "thanks": "mythanks",
    "grants": "mygrants",
}

# 单位特征词（spec §2：大学/学院/研究所/University/Institute/College/Department）
_AFFIL_WORDS = (
    "大学", "学院", "学校", "研究院", "研究所", "研究中心", "实验室", "学部",
    "学系", "教研室", "编辑部", "系",
)
_AFFIL_WORDS_EN = ("university", "institute", "college", "department", "school of")

# 基金/项目特征词（spec §2：基金/项目/资助/编号/fund/grant/NSFC）
_GRANT_WORDS = ("基金", "项目", "资助", "经费", "课题", "编号", "社科")
_GRANT_WORDS_EN = ("fund", "grant", "nsfc", "project no", "project number")

# 关键词行的标签（中英文三种写法共用）
_KEYWORD_LABEL = r"(?:关键词|关键字|Keywords?)"

# 作者候选里的单位/组名否决词（含 大学/学院 等，与 `looks_like_person_name` 互补）
_TEX_ORG_WORDS = (
    "大学", "学院", "学校", "研究院", "研究所", "研究中心", "中心", "实验室",
    "学部", "学系", "教研室", "编辑部", "委员会", "课题组", "研究组", "团队",
    "小组", "公司", "集团", "医院", "出版社", "杂志社", "学会", "协会", "基金会",
    "单位", "作者",
)

# 多作者分隔：\\ 换行、\and、\quad/\qquad、全/半角逗号、顿号、分号、字面 ` and `
_AUTHOR_SEP_RE = re.compile(r"\\\\|\\and\b|\\qquad\b|\\quad\b|\s+and\s+|[,，、;；]")

# 字体/字号/上下标命令：出现在姓名前后的排版命令不是姓名内容
_FONT_CMD_RE = re.compile(
    r"\\(?:rm|bf|it|sl|sc|tt|em|kaishu|heiti|songti|fangsong|lishu|youyuan|"
    r"large|Large|LARGE|huge|Huge|small|footnotesize|scriptsize|tiny|normalsize|"
    r"textsuperscript|textsubscript)\b\s*"
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# 作者 byline 里要剥掉的嵌套命令（花括号内容整段删除）
_AUTHOR_STRIP_CMDS = ("thanks", "footnote", "textsuperscript", "textsubscript")

# 单位/邮箱可能藏在这些「脚注体」命令里（普通 article 常见 \thanks / \blfootnote；
# Rathjen 类英文稿把单位放 \blfootnote）。只在前言与正文开头窄窗口里找。
_THANKS_CMDS = ("thanks",)
_FOOTNOTE_CMDS = ("footnote", "blfootnote", "footnotetext")

_CMD_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _cmd_re(name: str) -> re.Pattern[str]:
    pat = _CMD_RE_CACHE.get(name)
    if pat is None:
        pat = re.compile(r"\\" + re.escape(name) + r"(?![A-Za-z])")
        _CMD_RE_CACHE[name] = pat
    return pat


# ── 基础文本工具（纯函数）─────────────────────────────────────────────────


def _norm_ws(text: str) -> str:
    """折叠空白（含换行/制表）为单个空格并去首尾。"""
    return re.sub(r"\s+", " ", text or "").strip()


def _none_if_empty(value: str | None) -> str | None:
    """空值 / ``null`` / 纯空白 → None（spec §3）。"""
    if value is None:
        return None
    v = _norm_ws(value)
    if not v or v.strip().lower() == "null":
        return None
    return v


def _scopes(text: str) -> tuple[str, str]:
    """切成 (``\\begin{document}`` 之前, 之后)；没有 document 环境时整段算前言。"""
    at = text.find("\\begin{document}")
    if at < 0:
        return text, ""
    return text[:at], text[at:]


def _braced_arg(text: str, i: int) -> str | None:
    """从 i 起跳过空白与可选 ``[...]`` 参数，取一个配平 ``{...}`` 的内容。"""
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    while i < n and text[i] == "[":
        j = text.find("]", i)
        if j < 0:
            return None
        i = j + 1
        while i < n and text[i].isspace():
            i += 1
    if i >= n or text[i] != "{":
        return None
    try:
        end = match_brace(text, i)
    except ValueError:
        return None
    return text[i + 1:end]


_NEWCMD_RE = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand)\*?\s*\{?\s*$")
_DEFFAMILY_RE = re.compile(r"\\(?:def|gdef|xdef|edef|let)\s*$")


def _is_definition_site(text: str, pos: int) -> bool:
    """pos 处的命令名是否是宏定义**被定义的对象**（``\\def\\blfootnote{...}``）。"""
    prefix = text[max(0, pos - 120):pos]
    return bool(_NEWCMD_RE.search(prefix) or _DEFFAMILY_RE.search(prefix))


def _iter_cmd(text: str, name: str):
    """按出现顺序 yield 所有 ``\\name{...}`` 的花括号内容（支持 ``\\name[opt]{...}``）。

    跳过宏定义位点（``\\def\\name{...}`` 的 body 不是调用，例如
    ``\\def\\blfootnote{\\xdef\\@thefnmark{}\\@footnotetext}``）。
    """
    for m in _cmd_re(name).finditer(text):
        if _is_definition_site(text, m.start()):
            continue
        content = _braced_arg(text, m.end())
        if content is not None:
            yield content


def _iter_cmds(text: str, *names: str):
    """按文档顺序 yield (命令名, 花括号内容)；多个命令名合并按位置排序。"""
    found: list[tuple[int, str, str]] = []
    for name in names:
        for m in _cmd_re(name).finditer(text):
            content = _braced_arg(text, m.end())
            if content is not None:
                found.append((m.start(), name, content))
    found.sort(key=lambda x: x[0])
    for _, name, content in found:
        yield name, content


def _first_cmd_value(text: str, *names: str) -> str | None:
    """文档顺序第一个**非空**的 ``\\name{...}`` 内容；没有则 None。"""
    for _, content in _iter_cmds(text, *names):
        v = _none_if_empty(content)
        if v is not None:
            return v
    return None


def _find_env(text: str, env: str) -> str | None:
    """取第一个 ``\\begin{env}...\\end{env}`` 的内容（不展开嵌套同名环境）。"""
    m = re.search(r"\\begin\{" + re.escape(env) + r"\}", text)
    if not m:
        return None
    e = re.search(r"\\end\{" + re.escape(env) + r"\}", text[m.end():])
    if not e:
        return None
    return text[m.end(): m.end() + e.start()]


def _strip_balanced_cmd(text: str, cmd: str) -> str:
    """删除 text 里所有 ``\\cmd{...}``；花括号按 match_brace 配平（支持嵌套）。"""
    pat = _cmd_re(cmd)
    out: list[str] = []
    i = 0
    while True:
        m = pat.search(text, i)
        if not m:
            out.append(text[i:])
            return "".join(out)
        j = m.end()
        while j < len(text) and text[j].isspace():
            j += 1
        if j >= len(text) or text[j] != "{":
            out.append(text[i:m.end()])
            i = m.end()
            continue
        try:
            end = match_brace(text, j)
        except ValueError:
            out.append(text[i:m.end()])
            i = m.end()
            continue
        out.append(text[i:m.start()])
        i = end + 1


# ── 作者 ─────────────────────────────────────────────────────────────────


def _looks_like_person(name: str) -> bool:
    """人名可信度闸门（单一实现仍在 `naming.looks_like_person_name`，此处只调用）。"""
    from .naming import looks_like_person_name

    return looks_like_person_name(name)


def _looks_like_byline_author(name: str) -> bool:
    if not _looks_like_person(name):
        return False
    return not any(w in name for w in _TEX_ORG_WORDS)


def _strip_author_tail(name: str) -> str:
    """去姓名后的常见尾缀：译/著、单位序号、括号注释；`陈 松` 这类姓名内空格不切。"""
    s = re.sub(r"\s+", " ", name).strip()
    s = re.sub(r"\s*(?:译注|译著|译者|译|著|编)$", "", s).strip()
    s = re.sub(r"[\s\d*†‡¶§]+$", "", s).strip()
    s = re.sub(r"\s*[（(][^（）()]*[）)]$", "", s).strip()
    s = s.strip(" .,，、;；:：-—")
    # 「李雷 示例大学」：姓名后的空格单位 → 只留姓名；`陈 松`（余下 1 字）不切
    if " " in s and re.search(r"[\u4e00-\u9fff]", s):
        head, _, rest = s.partition(" ")
        if _looks_like_byline_author(head) and len(re.sub(r"\s+", "", rest)) >= 3:
            s = head
    return s


def _first_author_from_byline(content: str) -> str:
    """从 ``\\author{...}`` 的花括号内容里取第一作者；拿不准返回空串。"""
    for cmd in _AUTHOR_STRIP_CMDS:
        content = _strip_balanced_cmd(content, cmd)
    content = _FONT_CMD_RE.sub("", content)
    content = re.sub(r"[{}]", "", content).replace("~", " ")
    first = _AUTHOR_SEP_RE.split(content)[0]
    first = _strip_author_tail(first)
    return first if _looks_like_byline_author(first) else ""


def author_from_document_author(text: str) -> str:
    """从普通 article 的 ``\\author{...}`` 取第一作者（B-275，B-276 起本模块唯一实现）。

    先扫 ``\\begin{document}`` 之前；前言里读不到时再扫全文（作者写在正文里的
    真实稿存在，B-275 限界补充）。注释掉的不算；空值跳过；拿不准返回空串。

    典型形态：``\\author{李雷\\thanks{单位}\\thanks{基金}}``、
    ``\\author{Michael Rathjen and Shuangshuang Shu}``、authblk 的
    ``\\author[1]{...}``。
    """
    if not text:
        return ""
    stripped = strip_comments(text)
    pre, body = _scopes(stripped)
    for scope in (pre, body):
        if not scope:
            continue
        for content in _iter_cmd(scope, "author"):
            cand = _first_author_from_byline(content)
            if cand:
                return cand
    return ""


def first_author_from_tex(text: str) -> str:
    """``\\author{}`` 第一作者（``naming.author_from_tex_metadata_ex`` 的回退实现）。"""
    return author_from_document_author(text)


# ── 单字段解析 ───────────────────────────────────────────────────────────


def _strip_title_notes(content: str | None) -> str | None:
    """标题：剥 ``\\thanks``/``\\footnote``（另作 grants / thanks），保留内部 ``\\\\``。"""
    if content is None:
        return None
    s = _strip_balanced_cmd(content, "thanks")
    s = _strip_balanced_cmd(s, "footnote")
    return _none_if_empty(s)


def _clean_inline(content: str | None) -> str | None:
    """行内值（摘要/关键词/单位/邮箱）：折叠空白后判空。"""
    return _none_if_empty(content)


def _split_abstract_keywords(content: str) -> tuple[str | None, str | None]:
    """摘要环境里常把关键词写成一行（``\\textbf{关键词：}A；B``）——拆出来。"""
    m = re.search(
        r"(?:\\par\s*)?(?:\\noindent\s*)?(?:\\textbf\{)?\s*" + _KEYWORD_LABEL + r"\s*[：:]\s*\}?",
        content, re.I)
    if not m:
        return _norm_ws(content), None
    abstract = content[:m.start()]
    kw = content[m.end():]
    cut = re.search(r"\n|\\end\{|\\par\b", kw)
    if cut:
        kw = kw[:cut.start()]
    kw = kw.strip().rstrip("}").strip()
    return _norm_ws(abstract), _none_if_empty(kw)


def _keywords_from_text(text: str) -> str | None:
    """``关键词：``/``关键字：``/``Keywords:`` 行（可包在 ``\\textbf{}`` 里）。"""
    if not text:
        return None
    m = re.search(r"(?:\\textbf\{)?\s*" + _KEYWORD_LABEL + r"\s*[：:]\s*\}?", text, re.I)
    if not m:
        return None
    rest = text[m.end():]
    cut = re.search(r"\n|\\end\{|\\par\b", rest)
    if cut:
        rest = rest[:cut.start()]
    return _none_if_empty(rest.rstrip("}").strip())


def _find_abstract(pre: str, body: str) -> str | None:
    for scope in (pre, body):
        if not scope:
            continue
        env = _find_env(scope, "abstract")
        if env is not None:
            return env
        v = _first_cmd_value(scope, "abstract")
        if v is not None:
            return v
    return None


def _find_keywords(pre: str, body: str) -> str | None:
    for scope in (pre, body):
        if not scope:
            continue
        env = _find_env(scope, "keywords")
        if env is not None:
            kw = _none_if_empty(env)
            if kw:
                return kw
        v = _first_cmd_value(scope, "keywords", "keyword")
        if v is not None:
            return v
        v = _keywords_from_text(scope)
        if v is not None:
            return v
    return None


def _institution_segment(content: str) -> str | None:
    """从 ``\\thanks``/脚注内容里取「像单位」的那一段；特征词命中才算。"""
    for part in re.split(r"[。；;]|\\\\", content):
        low = part.lower()
        if any(w in part for w in _AFFIL_WORDS) or any(w in low for w in _AFFIL_WORDS_EN):
            seg = _norm_ws(part).strip(" .,，、;；")
            if seg:
                return seg
    return None


def _is_grant(content: str) -> bool:
    low = content.lower()
    return (any(w in content for w in _GRANT_WORDS)
            or any(w in low for w in _GRANT_WORDS_EN))


def _font_cmd_free(content: str) -> str:
    return _FONT_CMD_RE.sub("", content)


def _front_region(pre: str, body: str) -> str:
    """前言 + 正文开头窄窗口：单位/邮箱/致谢通常就在 ``\\maketitle`` 附近。"""
    return pre + "\n" + body[:2000]


# ── 结果对象 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TexFrontmatter:
    """一份普通 article 主文件里能保守识别出的前置元数据（无值 = None）。"""

    title: str | None = None
    running_title: str | None = None
    author: str | None = None
    affiliation: str | None = None
    email: str | None = None
    abstract: str | None = None
    keywords: str | None = None
    thanks: str | None = None
    grants: str | None = None

    def as_dict(self) -> dict[str, str]:
        """只含识别出的字段（源字段名，如 ``title``）。"""
        return {f.name: getattr(self, f.name) for f in fields(self)
                if getattr(self, f.name)}

    def template_values(self) -> dict[str, str]:
        """映射到模板槽位（如 ``{"mytitle": ..., "myfirstauthor": ...}``）。"""
        out: dict[str, str] = {}
        for name, value in self.as_dict().items():
            slot = TEMPLATE_FIELDS.get(name)
            if slot:
                out[slot] = value
        return out


def parse_tex_frontmatter(text: str) -> TexFrontmatter | None:
    """解析主文件前面板；一个字段都识别不出时返回 ``None``（不是空 dict）。

    扫描顺序（保守）：前言优先，前言没有再看 ``\\begin{document}`` 之后
    （普通 article 的 abstract / ``关键词：`` 行本来就在正文开头；B-275 限界
    允许正文内 ``\\author{}`` 作回退）。``\\input`` **不展开**。
    """
    if not text or not text.strip():
        return None
    stripped = strip_comments(text)
    pre, body = _scopes(stripped)

    title = _strip_title_notes(_first_cmd_value(pre, "title"))
    if title is None:
        title = _strip_title_notes(_first_cmd_value(body, "title"))
    running = (_first_cmd_value(pre, "shorttitle", "runningtitle")
               or _first_cmd_value(body, "shorttitle", "runningtitle"))
    running = _font_cmd_free(running) if running else None
    running = _none_if_empty(running)

    author = author_from_document_author(stripped) or None

    # 单位/邮箱/致谢/基金：前言 + 正文开头窄窗口；优先显式命令
    front = _front_region(pre, body)
    affiliation = _first_cmd_value(pre, "affil", "affiliation", "institute")
    if affiliation is None:
        affiliation = _first_cmd_value(body, "affil", "affiliation", "institute")
    affiliation = _clean_inline(affiliation)

    thanks_entries: list[str] = []

    def _collect(scope: str, *cmds: str) -> None:
        for cmd in cmds:
            for content in _iter_cmd(scope, cmd):
                v = _none_if_empty(content)
                # 含 #1 的是宏定义体，不是实际调用（保守跳过，避免垃圾进 mythanks）
                if v is not None and "#" not in v:
                    thanks_entries.append(v)

    _collect(pre, *_THANKS_CMDS)
    for cmd in _FOOTNOTE_CMDS:
        _collect(pre, cmd)
        _collect(body[:2000], cmd)

    grants_entries: list[str] = []
    thanks_left: list[str] = []
    for entry in thanks_entries:
        if _EMAIL_RE.search(entry):
            # 通讯地址块：单位作 affiliation、邮箱作 email，不算致谢文字
            if affiliation is None:
                affiliation = _institution_segment(entry)
            continue
        if _is_grant(entry):
            grants_entries.append(entry)
            continue
        inst = _institution_segment(entry)
        if inst is not None and affiliation is None:
            affiliation = inst
            continue
        thanks_left.append(entry)

    email = _first_cmd_value(pre, "email") or _first_cmd_value(body, "email")
    if email is None:
        m = _EMAIL_RE.search(front)
        email = m.group(0) if m else None
    email = _clean_inline(email)

    abstract_raw = _find_abstract(pre, body)
    abstract: str | None = None
    keywords: str | None = None
    if abstract_raw is not None:
        abstract, keywords = _split_abstract_keywords(abstract_raw)
    if keywords is None:
        keywords = _find_keywords(pre, body)

    grants = "；".join(dict.fromkeys(grants_entries)) or None
    thanks = "；".join(dict.fromkeys(thanks_left)) or None

    fm = TexFrontmatter(
        title=title,
        running_title=running,
        author=author,
        affiliation=affiliation,
        email=email,
        abstract=_none_if_empty(abstract),
        keywords=_font_cmd_free(keywords) if keywords else None,
        thanks=_font_cmd_free(thanks) if thanks else None,
        grants=_font_cmd_free(grants) if grants else None,
    )
    return fm if fm.as_dict() else None
