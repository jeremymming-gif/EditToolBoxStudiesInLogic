"""Stage a verified bilingual table of contents for issue page refresh."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Protocol
import unicodedata

from pypdf import PdfReader

from . import latex, toc, tocdelivery, toctemplate
from .clean_workspaces import checked_stat, is_link


class IssueTocError(RuntimeError):
    """The pair of issue contents files cannot be staged safely."""


class PageRangeLike(Protocol):
    folder: str
    first: int
    last: int
    count: int


@dataclass(frozen=True)
class TocEntryPages:
    folder: str
    zh: int
    en: int


@dataclass(frozen=True)
class StagedToc:
    toc_dir: Path
    mode: str
    tex: dict[str, Path]
    pdf: dict[str, Path]
    pages: dict[str, int]
    entries: tuple[TocEntryPages, ...]
    work_dir: Path
    _owned_work_dir: bool = False
    max_pages: int = 1
    compression_skipped: bool = False

    def cleanup(self) -> None:
        if self._owned_work_dir:
            shutil.rmtree(self.work_dir)


@dataclass(frozen=True)
class _Article:
    folder: str
    volume: str
    issue: str
    year: str
    titles: dict[str, str]
    authors: dict[str, list[str]]


# Require the complete numeric page cell, with optional braces paired.
_PAGE_CELL = re.compile(r"&\s*(?:([0-9]+)|\{\s*([0-9]+)\s*\})\s*\\\\")
_NUMBERED = re.compile(r"(?:0[1-9]|[1-9][0-9]) (.+)")
_TEX_NAMES = {key: stem + ".tex" for key, stem in tocdelivery.TEX_STEMS}


def _checked_file(root: Path, relative: str) -> Path:
    info = checked_stat(str(root), relative)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise IssueTocError(f"目录刷新输入必须是普通文件：{root / relative}")
    return root / relative


def _issue_root(issue: Path) -> Path:
    path = Path(os.path.abspath(issue))
    info = path.lstat()
    if is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise IssueTocError(f"期目录不安全：{path}")
    return path


def _target_dir(issue: Path) -> Path:
    candidates = []
    for candidate in issue.iterdir():
        if not candidate.name.startswith("000"):
            continue
        info = candidate.lstat()
        if is_link(info):
            raise IssueTocError(f"目录成品路径包含链接：{candidate}")
        if stat.S_ISDIR(info.st_mode):
            checked_stat(str(issue), candidate.name)
            candidates.append(candidate)
    if len(candidates) > 1:
        raise IssueTocError("存在多个 000 目录，无法唯一确定目录成品位置")
    return candidates[0] if candidates else issue / toctemplate.TOC_DIR


def _articles(issue: Path, ranges: Sequence[PageRangeLike],
              metadata_tex: Mapping[str, os.PathLike] | None) -> list[_Article]:
    if not ranges or len({item.folder for item in ranges}) != len(ranges):
        raise IssueTocError("页码计划没有篇目或含有重复篇目")
    result = []
    for item in ranges:
        match = _NUMBERED.fullmatch(item.folder)
        if not match or any(char in item.folder for char in "/\\:\r\n"):
            raise IssueTocError(f"稿件目录名不合法：{item.folder!r}")
        if metadata_tex is None:
            relative = f"{item.folder}/{match[1]}.tex"
            source = _checked_file(issue, relative)
            if source.name not in os.listdir(source.parent):
                raise IssueTocError(f"未找到与稿件目录精确同名的 TeX：{relative}")
        else:
            source = Path(metadata_tex[item.folder])
            _checked_file(source.parent, source.name)

        macros = latex.parse_macros(source.read_text(encoding="utf-8-sig"))

        def value(name: str) -> str:
            macro = macros.get(name)
            if macro is None:
                return ""
            text = " ".join(latex.strip_comments(macro.value).split())
            return "" if text == "null" else text

        primary_is_english = bool(value("mytitleCN") and not value("mytitleEN"))
        titles = {"zh": value("mytitleCN") or value("mytitle"),
                  "en": value("mytitleEN") or (value("mytitle") if primary_is_english else "")}
        authors = {"zh": [], "en": []}
        for role in ("first", "second", "third"):
            name = f"my{role}author"
            zh = value(name + "CN") or (value(name) if not primary_is_english else "")
            en = value(name + "EN") or (value(name) if primary_is_english else "")
            if zh:
                authors["zh"].append(zh)
            if en:
                authors["en"].append(en)
        if not all(titles.values()) or not any(authors.values()):
            raise IssueTocError(f"{item.folder} 缺少可识别的中英文题名或作者")
        result.append(_Article(item.folder, value("myvolnumber"), value("myissnumber"),
                               value("myyearnumber"), titles, authors))
    return result


def _mask_comments(text: str) -> str:
    """Remove comment contents without shifting spans in the original TeX."""
    result = []
    for line in text.splitlines(keepends=True):
        live = latex.strip_comment(line.rstrip("\r\n"))
        result.append(live.ljust(len(line.rstrip("\r\n"))) + line[len(line.rstrip("\r\n")):])
    return "".join(result)


def _table_body(text: str, name: str) -> tuple[int, str]:
    begins = list(re.finditer(r"\\begin\{tabularx\}", text))
    ends = list(re.finditer(r"\\end\{tabularx\}", text))
    if len(begins) != 1 or len(ends) != 1:
        raise IssueTocError(f"{name} 无法唯一识别目录表格，请选择重生成目录")
    start = latex.skip_ws_comments(text, begins[0].end())
    for _ in range(2):
        if text[start:start + 1] != "{":
            raise IssueTocError(f"{name} 无法识别目录列定义，请选择重生成目录")
        stop = latex.match_brace(text, start)
        if text[stop:stop + 1] != "}":
            raise IssueTocError(f"{name} 的目录列定义不完整，请选择重生成目录")
        start = latex.skip_ws_comments(text, stop + 1)
    if start >= ends[0].start():
        raise IssueTocError(f"{name} 的目录表格为空")
    return start, text[start:ends[0].start()]


def _title_key(value: str) -> str:
    value = re.sub(r"\\(?:phantom|hspace|vspace|zihao)\*?\s*\{[^{}]*\}", "", value)
    value = re.sub(r"\\newline(?![A-Za-z])", "", value)
    value = value.replace(r"\cndash", "-").replace(r"\edash", "-")
    value = toc.clean_title(value, keep_braces=False)
    value = re.sub(r"\\(?:bfseries|bf|rmfamily|songti|kaishu)(?![A-Za-z])", "", value)
    value = re.sub(r"[\s{}~]", "", unicodedata.normalize("NFC", value))
    return re.sub(r"[-—–]+", "-", value)


def _entry_key(segment: str, language: str) -> str:
    """Extract the title before the author/page cells, ignoring generated line breaks."""
    # A segment begins immediately after the previous row terminator. Its optional
    # spacing and the table's column heading are independent of the article title.
    lines = []
    for line in segment.splitlines():
        if re.search(r"(?:专栏|专辑|主持人|Column|Special Issue)\s*[:：]", line):
            continue
        line = re.sub(r"^\s*(?:\[[^]\r\n]*\]|\\\\\[[^]\r\n]*\]|\\vdistance)\s*", "", line)
        lines.append(line)
    value = "\n".join(lines)
    if language == "zh":
        separator = re.search(r"(?<!\\)&", value)
    else:
        # English titles occupy separate rows before the italic author row.
        separator = re.search(r"(?m)^\s*\\textit\s*\{|(?<!\\)&", value)
    return _title_key(value[:separator.start()] if separator else value)


def _update_body(source: Path, language: str, articles: Sequence[_Article],
                 ranges: Sequence[PageRangeLike]) -> bytes:
    text = source.read_bytes().decode("utf-8")  # BOM and original line endings remain in text.
    start, body = _table_body(_mask_comments(text), source.name)
    matches = list(_PAGE_CELL.finditer(body))
    expected = [_title_key(article.titles[language]) for article in articles]
    if len(matches) != len(articles) or "" in expected or len(set(expected)) != len(expected):
        raise IssueTocError(f"{source.name} 篇目或页码列无法唯一对应，请选择重生成目录")

    edits = []
    previous_end = 0
    for expected_key, match, pages in zip(expected, matches, ranges, strict=True):
        if _entry_key(body[previous_end:match.start()], language) != expected_key:
            raise IssueTocError(f"{source.name} 条目顺序或题名与篇目不一致，请选择重生成目录")
        left, right = match.span(match.lastindex)
        edits.append((start + left, start + right, str(pages.first)))
        previous_end = match.end()
    for left, right, replacement in reversed(edits):
        text = text[:left] + replacement + text[right:]
    return text.encode("utf-8")


def _existing_tex(issue: Path, target: Path) -> dict[str, Path]:
    sources = {}
    for key, name in _TEX_NAMES.items():
        try:
            sources[key] = _checked_file(issue, f"{target.name}/{name}")
        except FileNotFoundError:
            continue
    return sources


def _existing_files(target: Path) -> bool:
    if not target.exists():
        return False
    return any((target / f"{stem}.{suffix}").exists() or
               (target / f"{stem}.{suffix}").is_symlink()
               for _, stem in tocdelivery.TEX_STEMS for suffix in ("tex", "pdf"))


def check_issue_toc(issue: Path, page_ranges: Sequence[PageRangeLike], *,
                    metadata_tex: Mapping[str, os.PathLike] | None = None) -> str:
    """Read-only preflight: return a reason when preserve mode cannot proceed."""
    try:
        issue = _issue_root(issue)
        target = _target_dir(issue)
        articles = _articles(issue, page_ranges, metadata_tex)
        current = _existing_tex(issue, target)
        if not _existing_files(target):
            return ""
        if len(current) != len(_TEX_NAMES):
            return "现有中英文目录不完整，请选择重生成目录"
        for key, source in current.items():
            _update_body(source, key, articles, page_ranges)
    except (OSError, UnicodeError, IssueTocError, KeyError) as exc:
        return str(exc)
    return ""


def _template(issue: Path, target: Path, settings: toc.TocSettings,
              current: dict[str, Path], key: str) -> tuple[toc.TemplateChoice, Path | None]:
    filename = _TEX_NAMES[key]
    if key in current:
        source = current[key]
        path = source
        kind, note, folder = toc.TEMPLATE_CURRENT, f"继承本期 {target.name} 的 {filename}", str(issue)
    else:
        # Keep to the existing configured-template / previous-issue priority.
        configured = Path(settings.templateFolder) if settings.templateFolder else None
        previous = toctemplate.previous_issue_folder(str(issue))
        for candidate, candidate_kind in ((configured, toc.TEMPLATE_EXPLICIT),
                                          (Path(previous) if previous else None, toc.TEMPLATE_PREVIOUS)):
            if candidate is None:
                continue
            try:
                root = _issue_root(candidate)
                source = _checked_file(root, f"{toctemplate.TOC_DIR}/{filename}")
            except FileNotFoundError:
                continue
            path = source
            kind, note, folder = candidate_kind, f"继承{candidate_kind}的 {filename}", str(root)
            break
        else:
            return toc.TemplateChoice(toctemplate.builtin(filename), toc.TEMPLATE_BUILTIN,
                                      "使用内置目录模板"), None
    parts = toctemplate.split(path.read_text(encoding="utf-8-sig"), source=folder)
    return toc.TemplateChoice(parts, kind, note, folder), source


def _settings(issue: Path) -> toc.TocSettings:
    try:
        _checked_file(issue, toc.TocSettings.fileName)
    except FileNotFoundError:
        return toc.TocSettings()
    return toc.TocSettings.load(str(issue))


def toc_source_paths(issue: Path, *, regenerate: bool = False) -> tuple[Path, ...]:
    """Existing inputs that the refresh transaction must include in its source fingerprint."""
    issue = _issue_root(issue)
    target = _target_dir(issue)
    current = _existing_tex(issue, target)
    sources = list(current.values())
    try:
        sources.append(_checked_file(issue, toc.TocSettings.fileName))
    except FileNotFoundError:
        pass
    if regenerate or not _existing_files(target):
        settings = _settings(issue)
        for key in _TEX_NAMES:
            _, template_source = _template(issue, target, settings, current, key)
            if template_source is not None and template_source not in sources:
                sources.append(template_source)
    return tuple(sources)


def _generate_pair(issue: Path, target: Path, work: Path, articles: Sequence[_Article],
                   ranges: Sequence[PageRangeLike], current: dict[str, Path]) -> dict[str, Path]:
    settings = _settings(issue)
    report = work / "issue-metadata.txt"
    lines = []
    for article, span in zip(articles, ranges, strict=True):
        lines.extend((article.folder,
                      f"卷号：{article.volume}  期号：{article.issue}  年份：{article.year}",
                      f"起始页码：{span.first}  终止页码：{span.last}  页数：{span.count}",
                      f"中文标题: {article.titles['zh']}", f"英文标题: {article.titles['en']}"))
        for key, label in (("zh", "中文"), ("en", "英文")):
            for role, name in zip("一二三", article.authors[key]):
                lines.append(f"第{role}作者{label}: {name}")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    generated = {}
    for key, flag in (("zh", "ch"), ("en", "en")):
        choice, _ = _template(issue, target, settings, current, key)
        log = []
        output = toc.generate_tex(str(report), flag, settings, log=log.append, stamp="page-refresh",
                                  output_dir=str(work), template=choice, issue_dir=str(issue))
        if output is None:
            raise IssueTocError(f"生成 {_TEX_NAMES[key]} 失败：{'；'.join(log)}")
        generated[key] = output
    return {key: Path(path) for key, path in tocdelivery.place_tex(
        str(issue), str(work), sources=generated).items()}


def _compile_pair(work: Path, *, preserve: bool, max_pages: int = 1) -> tuple[dict[str, Path], dict[str, int]]:
    def check_pdf(folder: str, key: str, stem: str, result: tuple[bool, str]) -> tuple[Path, int]:
        good, detail = result
        path = Path(folder) / f"{stem}.pdf"
        if not good:
            raise IssueTocError(f"目录 PDF 编译失败（{stem}.tex）：{detail}")
        try:
            _checked_file(Path(folder), path.name)
            with path.open("rb") as stream:
                pages = len(PdfReader(stream).pages)
        except Exception as exc:
            raise IssueTocError(f"缺少可读取的本轮目录 PDF（{stem}.pdf）：{exc}") from exc
        log_count = tocdelivery.page_count_from_log(folder, stem)
        if pages < 1 or pages != log_count:
            raise IssueTocError(f"{stem}.pdf 的实测页数与本轮编译日志不一致")
        return path, pages

    results = tocdelivery.compile_pdfs(str(work), tocdelivery.TEX_STEMS)
    pdfs, counts = {}, {}
    for key, stem in tocdelivery.TEX_STEMS:
        pdfs[key], counts[key] = check_pdf(str(work), key, stem, results[key])
    if any(count > max_pages for count in counts.values()):
        if preserve:
            limit = "一" if max_pages == 1 else str(max_pages)
            raise IssueTocError(f"更新页码后目录超过{limit}页，请手工调整版式或选择重生成目录")

        def compile_one(folder: str, stem: str) -> tuple[bool, str]:
            key = next(key for key, candidate in tocdelivery.TEX_STEMS if candidate == stem)
            for suffix in ("log", "pdf"):
                (Path(folder) / f"{stem}.{suffix}").unlink(missing_ok=True)
            result = tocdelivery.compile_pdfs(folder, ((key, stem),))[key]
            check_pdf(folder, key, stem, result)
            return result

        counts, error, _ = tocdelivery.fit_single_page(str(work), tocdelivery.TEX_STEMS, compile_one,
                                                       max_pages=max_pages)
        if error:
            raise IssueTocError(error)
    return pdfs, counts


def stage_issue_toc(stage_issue: Path, page_ranges: Sequence[PageRangeLike], *,
                    regenerate: bool = False, work_dir: Path | None = None,
                    metadata_tex: Mapping[str, os.PathLike] | None = None) -> StagedToc:
    """Produce both verified files in a fresh staging directory without writing to the issue."""
    work = None
    try:
        issue = _issue_root(stage_issue)
        target = _target_dir(issue)
        articles = _articles(issue, page_ranges, metadata_tex)
        current = _existing_tex(issue, target)
        existing = _existing_files(target)
        preserve = existing and not regenerate
        if preserve and len(current) != len(_TEX_NAMES):
            raise IssueTocError("现有中英文目录不完整，请选择重生成目录")
        updates = {key: _update_body(source, key, articles, page_ranges)
                   for key, source in current.items()} if preserve else {}
        if work_dir is None:
            work = Path(tempfile.mkdtemp(prefix="slcn-issue-toc-"))
        else:
            candidate = Path(work_dir)
            if candidate.resolve().is_relative_to(issue.resolve()):
                raise IssueTocError("目录暂存位置必须在期目录外")
            candidate.mkdir()
            work = candidate
        if preserve:
            tex = {key: work / _TEX_NAMES[key] for key in updates}
            for key, content in updates.items():
                tex[key].write_bytes(content)
        else:
            tex = _generate_pair(issue, target, work, articles, page_ranges, current)

        max_pages = 1 if _settings(issue).singlePageToc else 2
        pdf, pages = _compile_pair(work, preserve=preserve, max_pages=max_pages)
        entries = tuple(TocEntryPages(item.folder, item.first, item.first) for item in page_ranges)
        mode = "updated" if preserve else "regenerated" if existing else "generated"
        return StagedToc(target, mode, tex, pdf, pages, entries, work, work_dir is None,
                         max_pages, max_pages >= 2)
    except (OSError, UnicodeError, IssueTocError, KeyError, tocdelivery.DeliveryError,
            toc.TocWriteError) as exc:
        # The refresh transaction owns its diagnostic directory even on failure.
        if work_dir is None and work is not None:
            shutil.rmtree(work, ignore_errors=True)
        if isinstance(exc, IssueTocError):
            raise
        raise IssueTocError(f"目录暂存失败：{exc}") from exc
