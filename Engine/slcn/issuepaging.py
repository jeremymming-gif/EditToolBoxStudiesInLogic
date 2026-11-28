"""Isolated compilation and stable page planning for a numbered issue."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import shutil
import tempfile
import unicodedata
from xml.etree import ElementTree

from pypdf import PdfReader

from . import latex
from .clean import SKIP_DIRS
from .clean_workspaces import boundary, checked_stat, identity, is_link
from .compilefix.compiler import COMPILE_EXTENSIONS, _environment as _compiler_environment
from .convert_compile import compile_pdf
from .pageplan import MeasuredArticle, PagePlanError, PageRange, plan_pages
from .pdfmerge import (AmbiguousArticlePdfError, article_name,
                       resolve_issue_article_pdf)


class PageCompileError(RuntimeError):
    """An isolated build failed or did not produce a trustworthy PDF."""


@dataclass(frozen=True)
class ArticleSource:
    folder: str
    directory: Path
    tex: Path
    pdf: Path
    first: int
    last: int
    count: int
    tex_bytes: bytes
    dynamic: bool = False


@dataclass(frozen=True)
class StagedArticle:
    source: ArticleSource
    tex: Path
    pdf: Path
    physical_pages: int
    compiled: bool
    recompiled: bool


@dataclass(frozen=True)
class StagedArticles:
    issue: Path
    stage: Path
    articles: tuple[StagedArticle, ...]
    page_ranges: tuple[PageRange, ...]
    rounds: int
    full_refresh: bool


_NUMBERED_DIR = re.compile(r"^([0-9]{2}) (.+)$")
_DIGITS = re.compile(r"[0-9]+")
_PAGE_MACROS = ("myfirstpage", "mylastpage", "mypages")
# F2: exactly two LastPage-derived macros are admitted as dynamic; all other
# non-integer definitions remain rejected.  Values are exact macro.value strings.
_DYNAMIC_MACROS = {
    "mylastpage": r"\pageref*{LastPage}",
    "mypages": r"\getpagerefnumber{LastPage}",
}
_ARCHIVES = frozenset(SKIP_DIRS)
_TEX_FILE_REFERENCE = re.compile(
    r"\\(?:input|include|includegraphics|addbibresource|bibliography|graphicspath)\*?"
    r"\s*(?:\[[^\]]*\]\s*)?\{((?:[^{}]|\{[^{}]*\})*)\}"
)


def _ordinary(info: os.stat_result) -> bool:
    return not is_link(info) and stat.S_ISREG(info.st_mode) and info.st_nlink == 1


@contextmanager
def _open_input(root: Path, path: Path):
    rel = path.relative_to(root).as_posix()
    try:
        root_info = root.lstat()
        if is_link(root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise OSError("期目录已变化")
        before = checked_stat(str(root), rel)
        if not _ordinary(before):
            raise OSError("文件不是独占普通文件")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not _ordinary(opened) or identity(opened) != identity(before):
                raise OSError("打开时文件身份已变化")
            yield stream, opened
            if identity(os.fstat(stream.fileno())) != identity(opened):
                raise OSError("读取期间文件身份已变化")
            if identity(checked_stat(str(root), rel)) != identity(opened):
                raise OSError("读取期间文件路径已变化")
    except OSError as exc:
        raise PagePlanError(f"篇目输入不安全或已变化：{rel}（{exc}）") from exc


def _read_macros(issue: Path, tex_path: Path, folder: str) -> tuple[bytes, tuple[int, int, int], bool]:
    try:
        with _open_input(issue, tex_path) as (stream, _):
            raw = stream.read()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise PagePlanError(f"无法读取主 TeX：{folder}/{tex_path.name}") from exc

    all_macros = latex.find_all_macros(text)
    values: list[int] = []
    dynamic = False
    for name in _PAGE_MACROS:
        matches = [macro for macro in all_macros if macro.name == name]
        def_aliases = re.findall(r"\\def\s*\\" + re.escape(name) + r"\b",
                                 latex.strip_comments(text))
        if len(matches) != 1 or def_aliases:
            raise PagePlanError(f"{folder} 的 {name} 定义缺失、重复或不受支持")
        macro = matches[0]
        definition = text[macro.span[0]:macro.value_span[0]]
        value = latex.strip_comments(macro.value).strip()
        # F2: admit only the two exact LastPage-derived values as dynamic;
        # every other non-integer or parameterized form is still rejected.
        allowed_dynamic = name in _DYNAMIC_MACROS and value == _DYNAMIC_MACROS[name]
        if "[" in definition or (not _DIGITS.fullmatch(value) and not allowed_dynamic):
            raise PagePlanError(f"{folder} 的 {name} 必须是无参数的十进制整数宏")
        for outer in all_macros:
            if outer is not macro and outer.value_span[0] <= macro.span[0] < outer.value_span[1]:
                raise PagePlanError(f"{folder} 的 {name} 不是顶层宏定义")
        if _inside_false_block(text, macro.span[0]):
            raise PagePlanError(f"{folder} 的 {name} 位于禁用的条件块中")
        if allowed_dynamic:
            dynamic = True
            # Integer sentinel only; the real physical page count is settled
            # later in the staged compile/convergence loop.
            values.append(1)
        else:
            values.append(int(value))
    first, last, count = values
    return raw, (first, last, count), dynamic


def _inside_false_block(text: str, position: int) -> bool:
    comments = latex.strip_comments(text[:position])
    stack: list[str] = []
    for match in re.finditer(r"\\([A-Za-z@]+|.)", comments):
        command = match.group(1)
        if command == "fi":
            if stack:
                stack.pop()
        elif command.startswith("if"):
            stack.append(command)
    return "iffalse" in stack


def _find_pdf(directory: Path, tex_path: Path, folder: str) -> Path:
    merge_name = article_name(folder)
    label_match = _NUMBERED_DIR.fullmatch(folder)
    target_name = label_match.group(2).strip() if label_match else merge_name
    try:
        pdfs = [item for item in directory.iterdir() if item.suffix.lower() == ".pdf"]
    except OSError as exc:
        raise PagePlanError(f"无法扫描正文 PDF：{folder}") from exc
    try:
        resolved = resolve_issue_article_pdf(str(directory), folder)
    except AmbiguousArticlePdfError as exc:
        raise PagePlanError(str(exc)) from exc
    if resolved:
        candidate = Path(resolved[0])
        if not _ordinary(candidate.lstat()):
            raise PagePlanError(f"正文 PDF 不是普通文件：{folder}/{candidate.name}")
        return candidate
    if pdfs and any(
            not re.fullmatch(r"fig(?:ure)?[ _-]?\d*", item.stem, re.I) for item in pdfs):
        raise PagePlanError(f"正文 PDF 命名无法被合刊识别：{folder}")
    name = f"{target_name}.pdf"
    key = unicodedata.normalize("NFC", name).casefold()
    if any(unicodedata.normalize("NFC", item.name).casefold() == key
           for item in directory.iterdir()):
        raise PagePlanError(f"正文 PDF 目标已被占用：{folder}/{name}")
    return directory / name


def _page_count(root: Path, path: Path) -> int | None:
    if not os.path.lexists(path):
        return None
    with _open_input(root, path) as (stream, _):
        try:
            count = len(PdfReader(stream, strict=True).pages)
        except Exception:
            return None
    return count if count > 0 else None


def inspect_articles(issue_dir: Path, *, excluded: tuple[str, ...] = ()) -> tuple[ArticleSource, ...]:
    supplied_issue = Path(issue_dir).absolute()
    try:
        issue_info = supplied_issue.lstat()
    except OSError as exc:
        raise PagePlanError(f"无法读取期目录：{supplied_issue}") from exc
    if is_link(issue_info) or not stat.S_ISDIR(issue_info.st_mode):
        raise PagePlanError(f"期目录不是普通目录：{supplied_issue}")
    issue = supplied_issue.resolve(strict=True)
    if not issue.is_dir():
        raise PagePlanError(f"期目录不是目录：{issue}")
    excluded_set = set(excluded)
    if len(excluded_set) != len(excluded):
        raise PagePlanError("排除目录重复")
    try:
        entries = sorted(issue.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise PagePlanError(f"无法扫描期目录：{issue}") from exc

    article_entries: list[Path] = []
    consumed_exclusions: set[str] = set()
    for entry in entries:
        match = _NUMBERED_DIR.fullmatch(entry.name)
        if entry.name in excluded_set:
            info = entry.lstat()
            if (not stat.S_ISDIR(info.st_mode) or is_link(info)
                    or boundary(info, str(entry), issue_info.st_dev)):
                raise PagePlanError(f"排除项不是安全目录：{entry.name}")
            consumed_exclusions.add(entry.name)
            continue
        info = entry.lstat()
        if is_link(info) and not entry.name.startswith("000"):
            raise PagePlanError(f"期目录含链接，无法安全暂存：{entry.name}")
        if not match:
            if (stat.S_ISDIR(info.st_mode) and re.match(r"^[0-9]{2}", entry.name)
                    and not entry.name.startswith("000")):
                raise PagePlanError(f"无效的编号篇目目录：{entry.name}")
            continue
        number = int(match.group(1))
        if number != len(article_entries) + 1 or not entry.name[3:].strip():
            raise PagePlanError(f"篇目编号须从 01 连续排列且不得重复：{entry.name}")
        if not stat.S_ISDIR(info.st_mode) or boundary(info, str(entry), issue_info.st_dev):
            raise PagePlanError(f"无效的编号篇目目录：{entry.name}")
        article_entries.append(entry)

    articles: list[ArticleSource] = []
    for entry in article_entries:
        names = entry.name[3:]
        main_tex = entry / f"{names}.tex"
        if main_tex.name not in {item.name for item in entry.iterdir()}:
            raise PagePlanError(f"缺少精确同名主 TeX：{entry.name}/{main_tex.name}")
        raw, (first, last, count), dynamic = _read_macros(issue, main_tex, entry.name)
        pdf = _find_pdf(entry, main_tex, entry.name)
        articles.append(ArticleSource(entry.name, entry, main_tex, pdf, first, last, count, raw,
                                      dynamic))

    missing_exclusions = excluded_set - consumed_exclusions
    if missing_exclusions:
        raise PagePlanError("指定排除目录不存在：" + ", ".join(sorted(missing_exclusions)))
    if not articles:
        raise PagePlanError("期目录中没有有效编号篇目")
    return tuple(articles)


def article_inputs(article: ArticleSource) -> tuple[Path, ...]:
    """Return the local snapshot inputs, without entering source archives."""
    issue = article.directory.parent
    transient = {article.tex.stem + suffix for suffix in COMPILE_EXTENSIONS}
    result: list[Path] = []

    def walk(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            if path.name.casefold() in _ARCHIVES:
                continue
            rel = path.relative_to(issue).as_posix()
            info = checked_stat(str(issue), rel)
            if stat.S_ISDIR(info.st_mode):
                walk(path)
            elif (directory == article.directory and path.name in transient
                  and path != article.pdf):
                continue
            elif _ordinary(info):
                result.append(path)
            else:
                raise PagePlanError(f"篇目输入不是独占普通文件：{rel}")

    try:
        walk(article.directory)
    except OSError as exc:
        raise PagePlanError(f"篇目输入路径不安全：{exc}") from exc
    return tuple(result)


def _snapshot(article: ArticleSource, dest: Path | None = None,
              *, omit_body_pdf: bool = False) -> str:
    digest = hashlib.sha256()
    issue = article.directory.parent
    for path in article_inputs(article):
        relative = path.relative_to(article.directory)
        digest.update(relative.as_posix().encode("utf-8") + b"\0")
        with _open_input(issue, path) as (reader, info):
            if path == article.tex:
                raw = reader.read()
                if raw != article.tex_bytes:
                    raise PagePlanError(f"篇目主 TeX 在预检后变化：{article.folder}")
                reader.seek(0)
            digest.update(str(identity(info)).encode("ascii") + b"\0")
            entry_digest = hashlib.sha256()
            if dest is not None and not (omit_body_pdf and path == article.pdf):
                target = dest / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as writer:
                    while chunk := reader.read(1024 * 1024):
                        entry_digest.update(chunk)
                        writer.write(chunk)
            else:
                entry_digest = hashlib.file_digest(reader, "sha256")
            digest.update(entry_digest.digest())
    return digest.hexdigest()


def article_fingerprint(article: ArticleSource) -> str:
    """Bind the source identities and bytes observed by isolated compilation."""
    return _snapshot(article)


def _normalize_dynamic_macros(path: Path) -> None:
    """Replace dynamic LastPage macro values with integer sentinels in the staged copy only."""
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    for name in ("mylastpage", "mypages"):
        parsed = [item for item in latex.find_all_macros(text) if item.name == name]
        if len(parsed) != 1:
            raise PageCompileError(f"暂存 TeX 的 {name} 无法唯一识别")
        item = parsed[0]
        value = latex.strip_comments(item.value).strip()
        if value == _DYNAMIC_MACROS[name]:
            text = text[:item.value_span[0]] + "1" + text[item.value_span[1]:]
    path.write_bytes((b"\xef\xbb\xbf" if had_bom else b"") + text.encode())


def _rewrite_pages(path: Path, page_range: PageRange) -> None:
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    for macro, value in zip(_PAGE_MACROS,
                            (page_range.first, page_range.last, page_range.count), strict=True):
        parsed = [item for item in latex.find_all_macros(text)
                  if item.name == macro]
        if len(parsed) != 1 or not _DIGITS.fullmatch(latex.strip_comments(parsed[0].value).strip()):
            raise PageCompileError(f"暂存 TeX 的 {macro} 无法唯一更新")
        item = parsed[0]
        start, end = _active_number_span(text, item.value_span[0], item.value_span[1])
        text = text[:start] + str(value) + text[end:]
    result = text.encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if had_bom else b"") + result)


def _active_number_span(text: str, start: int, end: int) -> tuple[int, int]:
    index = start
    while index < end:
        if text[index] == "%":
            newline = text.find("\n", index, end)
            index = end if newline < 0 else newline + 1
            continue
        if text[index].isdigit():
            finish = index + 1
            while finish < end and text[finish].isdigit():
                finish += 1
            return index, finish
        index += 1
    raise PageCompileError("页码宏没有可替换的数字")


def _local_reference(raw: str) -> bool:
    name = raw.strip().strip('"')
    parts = name.split("/")
    return bool(name and not name.startswith("/") and "\\" not in name
                and ":" not in name and "~" not in name and all(
                    part != ".." and part.casefold() not in _ARCHIVES for part in parts
                ))


def _check_tex_references(directory: Path) -> None:
    # The TeX process also uses Kpathsea's restrictive settings. Check literal
    # file references here to identify unsupported source paths before launch.
    for path in directory.rglob("*"):
        if path.suffix.lower() not in {".tex", ".sty", ".cls", ".ltx", ".cfg", ".def"}:
            continue
        if not path.is_file():
            continue
        text = latex.strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        for reference in _TEX_FILE_REFERENCE.finditer(text):
            value = reference.group(1)
            names = re.findall(r"\{([^{}]*)\}", value) or value.split(",")
            for name in names:
                if name and not _local_reference(name):
                    raise PageCompileError(f"拒绝期外或原稿目录的 TeX 引用：{path.name}（{name}）")


def _check_biber_inputs(directory: Path, stem: str) -> None:
    control = directory / f"{stem}.bcf"
    if not control.exists():
        return
    with _open_input(directory, control) as (stream, _):
        content = stream.read()
    if b"\0" in content or re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", content, re.I):
        raise PageCompileError("Biber 控制文件包含不受支持的 XML 声明")
    try:
        data = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise PageCompileError("无法解析本轮 Biber 控制文件") from exc
    for item in data.iter():
        if item.tag.rsplit("}", 1)[-1] != "datasource":
            continue
        name = item.text or ""
        if (not _local_reference(name) or item.get("type", "file") != "file"
                or item.get("glob", "0") not in {"0", "false"}):
            raise PageCompileError("Biber 仅允许暂存目录内的本地 Bib 文件")
        with _open_input(directory, directory / name):
            pass


def _fresh_compile(directory: Path, stem: str) -> tuple[Path, int]:
    output = directory / f"{stem}.pdf"
    output.unlink(missing_ok=True)
    _check_tex_references(directory)
    with tempfile.TemporaryDirectory(prefix="slcn-page-compile-") as home:
        environment = _compiler_environment(home)

        def run_xelatex(run, command, workdir, step):
            result = run([command[0], "-no-shell-escape", *command[1:]], cwd=workdir,
                         env=environment, quiet=True, check=False)
            if step == 1 and result.returncode == 0:
                _check_biber_inputs(directory, stem)
            return result

        def run_biber(run, command, workdir):
            return run(command, cwd=workdir, env=environment, quiet=True, check=False)

        ok, message = compile_pdf(str(directory), stem, run_xelatex=run_xelatex,
                                  run_biber=run_biber)
    if not ok or not os.path.lexists(output):
        raise PageCompileError(f"编译失败或缺少本轮 PDF：{directory.name}/{stem}.tex：{message}")
    try:
        count = _page_count(directory, output)
        if count is None:
            raise ValueError("PDF 页数为零")
        log_path = output.with_suffix(".log")
        if log_path.is_file():
            log = log_path.read_text(encoding="utf-8", errors="replace")
            found = re.findall(r"Output written on .*?\((\d+) pages?\)", log)
            if found and int(found[-1]) != count:
                raise ValueError("编译日志与 PDF 物理页数不一致")
    except Exception as exc:
        raise PageCompileError(f"本轮生成的 PDF 无法验证：{output}") from exc
    return output, count


def _compile_mapped(directory: Path, article: ArticleSource) -> tuple[Path, int]:
    generated, count = _fresh_compile(directory, article.tex.stem)
    mapped = directory / article.pdf.name
    if mapped != generated:
        os.replace(generated, mapped)
    return mapped, count


def prepare_articles(issue_dir: Path, stage_dir: Path, *, only: tuple[int, ...] = (),
                     excluded: tuple[str, ...] = (), max_rounds: int = 3) -> StagedArticles:
    articles = inspect_articles(issue_dir, excluded=excluded)
    issue = articles[0].directory.parent
    stage = Path(stage_dir).absolute()
    try:
        stage_resolved = stage.resolve(strict=False)
        if stage_resolved == issue or stage_resolved.is_relative_to(issue) or issue.is_relative_to(stage_resolved):
            raise PagePlanError("隔离暂存目录不得与期目录重叠")
    except OSError as exc:
        raise PagePlanError("无法解析隔离暂存目录") from exc
    if stage.exists():
        raise PagePlanError(f"隔离暂存目录已存在：{stage}")
    if max_rounds < 1:
        raise PagePlanError("最大页码编译轮数必须为正整数")
    if only and (any(isinstance(index, bool) or not isinstance(index, int) for index in only)
                 or len(set(only)) != len(only)
                 or any(index < 1 or index > len(articles) for index in only)):
        raise PagePlanError("指定篇目序号无效")

    source_stamps = [article_fingerprint(article) for article in articles]
    old_counts = [_page_count(issue, article.pdf) for article in articles]
    old_coherent = all(count is not None for count in old_counts)
    if old_coherent:
        old_ranges = plan_pages([MeasuredArticle(article.folder, int(count))
                                 for article, count in zip(articles, old_counts, strict=True)])
        old_coherent = all(
            article.first == span.first and article.last == span.last and article.count == span.count
            for article, span in zip(articles, old_ranges, strict=True)
        )
    full_refresh = not only or not old_coherent or any(article.dynamic for article in articles)
    selected = set(range(1, len(articles) + 1)) if not only or full_refresh else set(only)
    stage.mkdir(parents=True)
    staged_dirs: list[Path] = []
    try:
        for index, article in enumerate(articles, 1):
            target = stage / article.folder
            stamp = _snapshot(article, target, omit_body_pdf=index in selected)
            if stamp != source_stamps[index - 1]:
                raise PagePlanError(f"篇目输入在复制期间发生变化：{article.folder}")
            staged_dirs.append(target)

        # F2: normalize dynamic LastPage macros in the staged copies only;
        # the original manuscript is never modified.
        for index, article in enumerate(articles):
            if article.dynamic:
                _normalize_dynamic_macros(staged_dirs[index] / article.tex.name)

        def verify_inputs() -> None:
            for article, stamp in zip(articles, source_stamps, strict=True):
                if article_fingerprint(article) != stamp:
                    raise PagePlanError(f"篇目输入在隔离编译期间发生变化：{article.folder}")

        actual = list(old_counts)
        declared = [(article.first, article.last, article.count) for article in articles]
        compiled = [False] * len(articles)
        for index in sorted(selected):
            article = articles[index - 1]
            _, count = _compile_mapped(staged_dirs[index - 1], article)
            actual[index - 1] = count
            compiled[index - 1] = True

        if any(count is None for count in actual):
            raise PageCompileError("有篇目没有可读的本轮 PDF 页数")

        ranges = plan_pages([MeasuredArticle(article.folder, int(count))
                             for article, count in zip(articles, actual, strict=True)])
        rounds = 0
        range_key = lambda spans: tuple((span.first, span.last, span.count) for span in spans)
        seen_ranges = {range_key(ranges)}
        if only and not full_refresh:
            changed = any(
                span.first != article.first or span.last != article.last or span.count != article.count
                for article, span in zip(articles, ranges, strict=True)
            )
            if not changed:
                result_articles = tuple(
                    StagedArticle(article, staged_dirs[i] / article.tex.name,
                                  staged_dirs[i] / (article.pdf.name), int(actual[i]),
                                  compiled[i], False)
                    for i, article in enumerate(articles)
                )
                verify_inputs()
                return StagedArticles(issue, stage, result_articles, ranges, 0, False)
            full_refresh = True
            for index, article in enumerate(articles):
                if compiled[index]:
                    continue
                _, actual[index] = _compile_mapped(staged_dirs[index], article)
                compiled[index] = True
            ranges = plan_pages([MeasuredArticle(article.folder, count)
                                 for article, count in zip(articles, actual, strict=True)])
            seen_ranges = {range_key(ranges)}

        while True:
            changed_indices: list[int] = []
            for index, (article, span) in enumerate(zip(articles, ranges, strict=True)):
                desired = (span.first, span.last, span.count)
                if declared[index] != desired:
                    _rewrite_pages(staged_dirs[index] / article.tex.name, span)
                    declared[index] = desired
                    changed_indices.append(index)
            if not changed_indices:
                break
            if rounds >= max_rounds:
                raise PageCompileError(f"页码字段在 {max_rounds} 轮后仍未稳定")
            rounds += 1
            for index in changed_indices:
                article = articles[index]
                _, actual[index] = _compile_mapped(staged_dirs[index], article)
                compiled[index] = True
            vector = tuple(actual)
            next_ranges = plan_pages([MeasuredArticle(article.folder, count)
                                      for article, count in zip(articles, vector, strict=True)])
            stable = all(
                declared[index] == (span.first, span.last, span.count)
                for index, span in enumerate(next_ranges)
            )
            if stable:
                actual = list(vector)
                ranges = next_ranges
                break
            next_key = range_key(next_ranges)
            if next_key in seen_ranges:
                raise PageCompileError("页数向量出现振荡，未发布暂存结果")
            seen_ranges.add(next_key)
            actual = list(vector)
            ranges = next_ranges

        verify_inputs()
        result_articles = tuple(
            StagedArticle(article, staged_dirs[i] / article.tex.name,
                          staged_dirs[i] / article.pdf.name, int(actual[i]),
                          compiled[i], (article.first, article.last, article.count) !=
                          (ranges[i].first, ranges[i].last, ranges[i].count))
            for i, article in enumerate(articles)
        )
        return StagedArticles(issue, stage, result_articles, ranges, rounds, full_refresh)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
