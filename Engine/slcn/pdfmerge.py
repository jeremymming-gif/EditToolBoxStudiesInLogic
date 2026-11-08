"""合并一期的 PDF（第 88 条：把「合并文件」从 mac 外壳搬进引擎）。

**为什么搬**：这个功能原来是两套实现——mac 用 SwiftUI + PDFKit
（`sources-mac/Core/PDFMerger.swift` + `sources-mac/Features/MergeFeature.swift`），
Windows 用 PyPDF2（上游 `merge.py`）。要做 Windows 版就必须**只留一份逻辑**，
否则「扉页缺了要不要报错」「书签怎么排」这种细节两边迟早不一样。

**这一版是 mac 那套的忠实移植**（mac 那份注释写着「复刻 PyPDF2 `PdfMerger` 的行为」，
所以移植等于把两边的口径合并成一份）。页码与书签语义逐条对齐：

  · 扉页 `扉页.pdf` 只追加页面、**不导入它自带的大纲**，然后登记顶层书签 `cover`（指向第 0 页）；
  · `000…/Chinese Content.pdf` → 书签 `Chinese Content`；`000…/contents.pdf` → 书签 `contents`；
  · 其余 `NN <名字>` 目录 → 用**目录名最后一段**（如 `03 Jie_Zhou` → `Jie_Zhou`）
    去找 `Jie_Zhou.pdf`；**找不到就宽松匹配**（`Yang Liu.pdf` 这种下划线写成空格的情况，
    上游原版会整篇跳过且不报错 —— 实测丢过 33 页）；
  · 编委页 / 封底：**只追加页面、不导入大纲**（实测`cneditors[260113]左移.pdf` 自带一个叫
    「Chinese Content」的陈旧书签，导进来会出现两个同名书签指向不同页），书签名分别是 `编委页` / `封底`；
  · 各源文件自带的大纲默认**平铺在顶层、排在本程序登记的书签之后**（上游原版行为）；
    打开 `nest_outlines` 则挂到该篇文章的书签之下（mac 外壳里的可选增强）。
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # 只在类型检查时可见
    from pypdf import PdfReader, PdfWriter

# ⚠️ pypdf 走**延迟导入**（用到才 import，各处 `from pypdf import …` 就地写）：
#    `compileall` / `toc` / `infocheck` 都 import 本模块拿 `numbered_entries` /
#    `article_name` / `loose_key`，但**根本不碰 PDF** —— 从前它们跟着白付 46 ms。
#    实测（2026-10-17）：`toc --preview` 0.07→0.03 s、`compileall --dry-run` 0.07→0.03 s，
#    **产物与 stdout 逐字节相同**。`infocheck` 自己早就是就地 import 的写法（同一口径）。

#: 一期的期目录下，形如 `01 Jie_Zhou` / `000 目录` 的子目录
_NUMBERED = re.compile(r"^[0-9][0-9]")

#: 工具箱自己生成的合并文件，找编委页/封底时要排除
_MERGED_PREFIX = "合并"

#: 扉页文件名（固定约定）
COVER_NAME = "扉页.pdf"
#: 编委页新约定名（`EditorsPageDetector.preferredName`）
EDITORS_NAME = "编委页.pdf"

#: 写出用的同目录临时文件**固定后缀**（P04-R01）。
#: 固定名而不是 pid/uuid：进程崩溃后目标旁**最多只剩一份**前缀可辨认的残留，
#: 下次写同一个目标时会先清掉它再复用；它不以 `.pdf` 结尾，所以扫描
#: `合并*.pdf` 的清理/识别逻辑（GUI「清理临时文件」）不会把它当成品。
_TMP_SUFFIX = ".tmp-merge"


class OutputIsInputError(ValueError):
    """输出路径与本次读入的输入 PDF 是同一个文件（P04-R02）。

    合并的语义是「读若干输入 → 写一个输出」；当输出就落在某个输入上时，
    任何写出都先截断原稿 —— 用户既拿不到合并件，又丢了原稿。所以**零写入拒绝**。
    """


# ── 路径小工具（对应 mac 外壳的 `PathKit`）────────────────────────────────

def numbered_entries(folder: str) -> list[str]:
    """期目录下形如 `01 …` 的子目录名，按名排序（对应 `PathKit.numberedEntries`）。"""
    if not os.path.isdir(folder):
        return []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(n for n in names
                  if _NUMBERED.match(n) and os.path.isdir(os.path.join(folder, n)))


def loose_key(name: str) -> str:
    """宽松比较用的键：去掉下划线/半角空格/全角空格/连字符，统一小写。

    这样 `Yang_Liu` / `Yang Liu` / `chen-liu` 视为同一个名字。
    """
    for ch in ("_", " ", "\u3000", "-"):
        name = name.replace(ch, "")
    return name.lower()


def article_name(folder_name: str) -> str:
    """`03 Jie_Zhou` → `Jie_Zhou`（取目录名最后一段）。"""
    parts = folder_name.split()
    return parts[-1] if parts else folder_name


def resolve_article_pdf(folder: str, name: str) -> tuple[str, str] | None:
    """在稿件目录里找 `<name>.pdf`：先精确、再宽松匹配。返回 (路径, 实际文件名)。"""
    exact = os.path.join(folder, f"{name}.pdf")
    if os.path.exists(exact):
        return exact, f"{name}.pdf"
    try:
        files = sorted(os.listdir(folder))
    except OSError:
        return None
    pdfs = [f for f in files if f.lower().endswith(".pdf")]
    target = loose_key(name)
    for f in pdfs:
        if loose_key(os.path.splitext(f)[0]) == target:
            return os.path.join(folder, f), f
    return None


def _tex_key(name: str) -> str:
    """比 `loose_key` 再宽一点：**只留字母数字**（撇号、点、括号、下划线全去掉）再小写。

    为什么要它：`01 Tian'an_He/` 里人写的文件名是 `Tianan_He.tex`（撇号在文件名里没了）——
    `loose_key` 只去 `_`/空格/连字符，两边仍然不等。CJK 也算字母数字（`str.isalnum()`），
    所以中文名不会被抹掉。
    """
    return "".join(ch for ch in name if ch.isalnum()).lower()


def find_tex(folder: str, art: str) -> str | None:
    """找这一篇的 `.tex`：**先精确、再宽松**（宽松键见 `_tex_key`）。

    只在精确名不存在时才用宽松匹配（`main.tex` 这类不会误配），供 `compileall` 用。
    """
    try:
        files = sorted(os.listdir(folder))
    except OSError:
        return None
    cands = [f for f in files if f.lower().endswith(".tex")]
    for f in cands:
        if os.path.splitext(f)[0] == art:
            return os.path.join(folder, f)
    target = _tex_key(art)
    for f in cands:
        if _tex_key(os.path.splitext(f)[0]) == target:
            return os.path.join(folder, f)
    return None


def _page_count(path: str) -> int:
    """页数；读不出来返回 -1（与 mac 外壳的 `?? -1` 一致，**不抛**）。"""
    from pypdf import PdfReader           # noqa: PLC0415  延迟导入（见文件抬头）
    try:
        return len(PdfReader(path).pages)
    except Exception:  # noqa: BLE001  坏文件不该让整期合并不下去
        return -1


# ── 附加页识别（对应 `EditorsPageDetector` / `ExtraPages.swift`）────────────

@dataclass
class ExtraPage:
    file_name: str
    page_count: int
    matched_by: str

    def describe(self) -> str:
        return f"{self.file_name}（{self.page_count} 页，{self.matched_by}）"


def _pdfs_in(folder: str) -> list[str]:
    if not os.path.isdir(folder):
        return []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [n for n in names
            if n.lower().endswith(".pdf") and not n.startswith(_MERGED_PREFIX)]


def detect_editors_page(folder: str) -> tuple[ExtraPage | None, list[str]]:
    """找编委信息页，返回 (选中, 疑似的其它候选)。

    **按文件名识别，不读内容**：实测 2024–2025 年的编委页（`cneditor左移_2024.pdf`）
    是印刷厂曲线化过的、**没有文字层**，读内容时灵时不灵。
    优先级：约定名 `编委页.pdf` → `cneditor` 前缀 → 2022 年的 `扉页2.pdf` → 名字含「编委」。
    """
    if not os.path.isdir(folder):
        return None, []
    pdfs = _pdfs_in(folder)

    tiers: list[tuple[str, list[str]]] = [
        ("约定名", [EDITORS_NAME] if EDITORS_NAME in pdfs else []),
        ("cneditor 前缀", sorted(p for p in pdfs if p.lower().startswith("cneditor"))),
        ("2022 年命名", ["扉页2.pdf"] if "扉页2.pdf" in pdfs else []),
        ("名字含「编委」", sorted(p for p in pdfs
                                 if "编委" in p and "封面" not in p and "英文" not in p)),
    ]

    chosen: ExtraPage | None = None
    others: list[str] = []
    for reason, matches in tiers:
        if not matches:
            continue
        if chosen is None:
            chosen = ExtraPage(matches[0], _page_count(os.path.join(folder, matches[0])), reason)
            others.extend(matches[1:])   # 同一档里还有别的候选，也算「疑似遗留」
        else:
            others.extend(matches)

    # ⚠️ 去重时**必须把选中的那个排除掉**：`编委页.pdf` 同时命中「约定名」与「名字含编委」两档，
    #    不去掉的话日志会写「另有 1 个文件也像编委页，已忽略：编委页.pdf」——自己忽略自己。
    #    （mac 那份 Swift 有同样的毛病；第 88 条移植时修掉，两边都以此为准。）
    if chosen is not None:
        others = [o for o in others if o != chosen.file_name]
    return chosen, others


def detect_back_cover(folder: str) -> ExtraPage | None:
    """找封底/封面附加页（如 `cover+bottom+Englishi_2024.pdf`）。

    同样是纯图片、没有文字层，只能按文件名识别：`cover+bottom` 前缀 →
    名字里同时含 `cover` 与 `bottom`。它在成品里放在**最后一页**。
    """
    if not os.path.isdir(folder):
        return None
    pdfs = _pdfs_in(folder)
    tiers: list[tuple[str, list[str]]] = [
        ("cover+bottom 前缀", sorted(p for p in pdfs if p.lower().startswith("cover+bottom"))),
        ("名字含 cover 与 bottom",
         sorted(p for p in pdfs if "cover" in p.lower() and "bottom" in p.lower())),
    ]
    for reason, matches in tiers:
        if matches:
            return ExtraPage(matches[0], _page_count(os.path.join(folder, matches[0])), reason)
    return None


# ── 合并器（对应 `PDFMerger`）────────────────────────────────────────────

@dataclass
class OutlineNode:
    label: str
    page_index: int          # 合并后文档的页码（0 起）；-1 = 定不了
    children: list["OutlineNode"] = field(default_factory=list)


@dataclass
class _TopItem:
    label: str
    page_index: int
    children: list[OutlineNode]


def _collect_outline(reader: PdfReader, offset: int) -> list[OutlineNode]:
    """把源文件自带的大纲读出来，页码平移到合并文档的坐标系。

    pypdf 的 `reader.outline` 是「`Destination` 与它子项的 `list` 交替出现」的结构，
    所以遇到 list 就当成**上一个条目**的 children。
    """
    try:
        items = reader.outline
    except Exception:  # noqa: BLE001  坏大纲不该让合并失败
        return []

    def walk(seq: list) -> list[OutlineNode]:
        out: list[OutlineNode] = []
        i = 0
        while i < len(seq):
            item = seq[i]
            if isinstance(item, list):      # 孤儿子列表：跳过
                i += 1
                continue
            label = (getattr(item, "title", "") or "").strip()
            page_index = -1
            try:
                n = reader.get_destination_page_number(item)
                if isinstance(n, int) and n >= 0:
                    page_index = n + offset
            except Exception:  # noqa: BLE001  目标页坏了就记 -1
                pass
            children: list[OutlineNode] = []
            if i + 1 < len(seq) and isinstance(seq[i + 1], list):
                children = walk(seq[i + 1])
                i += 1
            out.append(OutlineNode(label, page_index, children))
            i += 1
        return out

    return walk(list(items))


class Merger:
    """按顺序拼页面 + 重建书签大纲（语义与 mac 的 `PDFMerger` 一致）。"""

    def __init__(self, nest_outlines: bool = False) -> None:
        from pypdf import PdfWriter           # noqa: PLC0415  延迟导入（见文件抬头）
        self.writer = PdfWriter()
        self.nest_outlines = nest_outlines
        self._top: list[_TopItem] = []
        self._source_nodes: list[OutlineNode] = []
        #: 本次**真正读入**的输入 PDF 绝对路径（P04-R02：写出前拿输出路径和它们比）
        self._inputs: list[str] = []

    @property
    def page_count(self) -> int:
        return len(self.writer.pages)

    def _remember_input(self, path: str) -> None:
        """登记本次要读入的输入 PDF（P04-R02 的底账）。

        ⚠️ 必须在**打开之前**登记：损坏/打不开的输入也是「本次要读的文件」，
        若等 `PdfReader` 成功才登记，`--out` 指向损坏原稿时仍会把它覆盖掉。
        """
        abspath = os.path.abspath(os.path.expanduser(path))
        if abspath not in self._inputs:
            self._inputs.append(abspath)

    @staticmethod
    def _same_file(a: str, b: str) -> bool:
        """两个路径是否指向同一个文件；目标可能还不存在，所以 realpath 兜底。

        相对路径、中文/空格、符号链接都不能绕过：`os.path.samefile` 认硬链接与
        符号链接，`realpath` 认「目标不存在但解析后同名」的情况。
        """
        try:
            if os.path.samefile(a, b):
                return True
        except OSError:
            pass                              # 目标不存在等：交给 realpath 判
        try:
            return os.path.realpath(a) == os.path.realpath(b)
        except OSError:
            return False

    def _reject_output_as_input(self, target: str) -> None:
        """输出若就是某个输入 PDF，抛中文原因（含文件名），且不做任何写入。"""
        for src in self._inputs:
            if self._same_file(target, src):
                raise OutputIsInputError(
                    f"输出路径与本次合并要读入的输入 PDF 是同一个文件："
                    f"{os.path.basename(src)}（{src}）。"
                    f"继续写会覆盖原稿，已拒绝；请换一个输出路径。")

    def _append_pages(self, path: str) -> tuple[int, list[OutlineNode]]:
        """追加一个 PDF 的全部页面，返回 (起始页码, 它自带的大纲)。"""
        from pypdf import PdfReader           # noqa: PLC0415  延迟导入（见文件抬头）
        self._remember_input(path)            # P04-R02：先登记再读（坏文件也要保护）
        reader = PdfReader(path)
        start = self.page_count
        nodes = _collect_outline(reader, start)
        for page in reader.pages:
            self.writer.add_page(page)
        return start, nodes

    def append(self, path: str, title: str) -> int:
        """追加并登记一个顶层书签；返回它自带的大纲（供嵌套模式使用）。"""
        start, nodes = self._append_pages(path)
        self._top.append(_TopItem(title, start, nodes))
        self._source_nodes.extend(nodes)
        return start

    def append_without_title(self, path: str) -> int:
        """只追加页面，不登记书签（扉页就是这么处理的）。"""
        start, nodes = self._append_pages(path)
        self._source_nodes.extend(nodes)
        return start

    def append_ignoring_outline(self, path: str, title: str) -> int:
        """追加页面并登记书签，但**不导入该文件自带的大纲**（编委页 / 封底）。"""
        from pypdf import PdfReader           # noqa: PLC0415  延迟导入（见文件抬头）
        self._remember_input(path)            # P04-R02：先登记再读（坏文件也要保护）
        reader = PdfReader(path)
        start = self.page_count
        for page in reader.pages:
            self.writer.add_page(page)
        self._top.append(_TopItem(title, start, []))
        return start

    def add_top_level_item(self, title: str, page_index: int) -> None:
        self._top.append(_TopItem(title, page_index, []))

    # 书签的最终形态：本程序登记的排前面；源文件自带的要么平铺在后（默认，上游行为），
    # 要么挂到对应文章之下（nest_outlines）。
    def _add_node(self, node: OutlineNode, parent=None):
        item = self.writer.add_outline_item(
            node.label or "（无标题）",
            node.page_index if node.page_index >= 0 else None,
            parent=parent,
        )
        for child in node.children:
            self._add_node(child, parent=item)
        return item

    def build_outline(self) -> None:
        if not self._top and not self._source_nodes:
            return
        items = []
        for top in self._top:
            items.append(self.writer.add_outline_item(
                top.label, top.page_index if top.page_index >= 0 else None))
        if self.nest_outlines:
            for top, item in zip(self._top, items):
                for child in top.children:
                    self._add_node(child, parent=item)
        else:
            for node in self._source_nodes:
                self._add_node(node)

    def write(self, path: str) -> None:
        """把合并结果写成 PDF（P04-R01：同目录临时文件 + fsync + 原子替换）。

        **绝不直接 `open(target, "wb")`**：那样写盘中途失败会把旧成品截断、
        目标原本不存在时留下半成品。这里的顺序是：

          1. 先查「输出是不是本次读入的输入」（P04-R02，命中则零写入直接抛错）；
          2. 写同目录固定名临时文件 `<target>.tmp-merge`，完整写 + flush + fsync；
          3. `close` 之后才 `os.replace(tmp, target)`（同目录 rename，原子）。

        任一步失败都删临时文件：旧目标逐字节不变，新目标仍不存在。
        """
        target = os.path.abspath(os.path.expanduser(path))
        self._reject_output_as_input(target)  # 在任何写入之前
        self.build_outline()
        folder = os.path.dirname(target)
        os.makedirs(folder, exist_ok=True)
        tmp = target + _TMP_SUFFIX
        try:
            if os.path.exists(tmp):
                os.remove(tmp)                # 上次崩溃的残留：先清掉，保证最多一份
        except OSError:
            pass                              # 清不掉就让下面的 open 报错（不吞失败）
        try:
            with open(tmp, "wb") as fh:
                self.writer.write(fh)
                fh.flush()
                os.fsync(fh.fileno())
            # `with` 退出 = close 已完成；之后才是原子替换
            os.replace(tmp, target)
        except BaseException:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass                          # 清理尽力而为，真正的异常照原样抛
            raise


# ── 一期的编辑规则（对应 `MergeFeature.run`）──────────────────────────────

@dataclass
class MergeResult:
    output: str | None
    page_count: int
    outline: list[tuple[str, int]]      # (书签名, 页码) —— 只列顶层，给日志/测试用
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def stamp(now: _dt.datetime | None = None) -> str:
    """输出文件名里的时间戳，格式与 mac 外壳一致（`yyyy_MM_dd_HH_mm_ss`）。"""
    return (now or _dt.datetime.now()).strftime("%Y_%m_%d_%H_%M_%S")


def merge_issue(folder: str,
                *,
                out: str | None = None,
                nest_outlines: bool = False,
                editors_page: bool = False,
                back_cover: bool = False,
                log=print) -> MergeResult:
    """合并一期的 PDF：扉页 → 目录（中文/英文）→ 各篇文章（按序号）→ 封底。

    `log` 逐行收日志（外壳直接显示；默认打到 stdout）。

    写出安全（P04-R01/R02）：产物先写同目录临时文件再原子替换目标；若 `out`
    指向本次**实际读入**的某个输入 PDF（含 `--editors-page`/`--back-cover` 检测
    挑选过的候选文件），抛 `OutputIsInputError`，一个字节都不写。
    """
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        raise NotADirectoryError(folder)

    log(f"运行合并文件的函数\n")
    log(f"文件夹路径：{folder}\n")

    merger = Merger(nest_outlines=nest_outlines)
    warnings: list[str] = []
    missing: list[str] = []

    # ---------- 扉页 ----------
    cover = os.path.join(folder, COVER_NAME)
    log(f"找pdf路径：{cover}\n")
    cover_pages = 0
    if os.path.exists(cover):
        try:
            merger.append_without_title(cover)
            cover_pages = merger.page_count
        except Exception as e:  # noqa: BLE001
            warnings.append(f"处理扉页时出错（{COVER_NAME}）：{e}")
            log(f"处理扉页时出错啦：{e}\n")
    else:
        missing.append(COVER_NAME)
        log("没有找到扉页文件！\n")
    merger.add_top_level_item("cover", 0)

    # ---------- 编委信息页（可选，插在扉页之后）----------
    if editors_page:
        chosen, others = detect_editors_page(folder)
        # P04-R02：--editors-page 时 detect 会挑选候选文件（chosen 实际被打开算过
        # 页数）；即便本次没选中，把合并产物写到候选原稿上同样是破坏性操作——零成本
        # 一并纳入保护集，由 write() 统一拒绝。
        candidates = ([chosen.file_name] if chosen else []) + list(others)
        for name in candidates:
            merger._remember_input(os.path.join(folder, name))  # noqa: SLF001
        if chosen:
            path = os.path.join(folder, chosen.file_name)
            log(f"找编委信息页：{path}\n")
            try:
                merger.append_ignoring_outline(path, "编委页")
                log(f"已加入编委信息页：{chosen.describe()}\n")
            except Exception as e:  # noqa: BLE001
                warnings.append(f"处理编委信息页时出错（{chosen.file_name}）：{e}")
                log(f"处理编委信息页时出错啦：{e}\n")
            if others:
                log(f"⚠️ 另有 {len(others)} 个文件也像编委页，已忽略：{'、'.join(others)}\n")
                log("   建议只保留最终版，并命名为「编委页.pdf」。\n")
        else:
            log("未找到编委信息页文件，已跳过。建议把该文件命名为「编委页.pdf」放在期目录下。\n")
        if cover_pages > 1:
            # 扉页不止一页时可能它本身就含编委页，插进去就重复了 —— 只提示，不擅自决定
            log(f"⚠️ 扉页.pdf 有 {cover_pages} 页，可能已经包含编委信息页，"
                f"请确认合并结果是否重复。\n")

    # ---------- 目录与正文 ----------
    for name in numbered_entries(folder):
        sub = os.path.join(folder, name)
        if name.startswith("000"):
            for fname, title in (("Chinese Content.pdf", "Chinese Content"),
                                 ("contents.pdf", "contents")):
                path = os.path.join(sub, fname)
                log(f"找pdf路径：{path}\n")
                if os.path.exists(path):
                    try:
                        merger.append(path, title)
                    except Exception as e:  # noqa: BLE001
                        warnings.append(f"处理 {fname} 时出错：{e}")
                        log(f"处理{title}时出错啦：{e}\n")
                else:
                    missing.append(fname)
                    log(f"没有找到{'中文' if title == 'Chinese Content' else '英文'}目录文件！\n")
            continue

        art = article_name(name)
        found = resolve_article_pdf(sub, art)
        log(f"找pdf路径：{os.path.join(sub, art + '.pdf')}\n")
        if not found:
            missing.append(f"{name}/{art}.pdf")
            log(f"没有找到{art}文件！\n")
            continue
        path, actual = found
        if actual != f"{art}.pdf":
            log(f"⚠️ 实际文件名是「{actual}」，与文件夹名「{art}」不一致，已按宽松匹配采用它。\n")
        try:
            merger.append(path, art)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"处理 {art} 时出错（{actual}）：{e}")
            log(f"处理{art}时出错啦：{e}\n")

    # ---------- 封底附加页（可选，放最后）----------
    if back_cover:
        back = detect_back_cover(folder)
        # P04-R02：detect 选封底时会打开候选文件，同样纳入保护集（理由同上）。
        if back:
            merger._remember_input(os.path.join(folder, back.file_name))  # noqa: SLF001
            path = os.path.join(folder, back.file_name)
            log(f"找封底页：{path}\n")
            try:
                merger.append_ignoring_outline(path, "封底")
                log(f"已在末尾加入封底页：{back.describe()}\n")
            except Exception as e:  # noqa: BLE001
                warnings.append(f"处理封底页时出错（{back.file_name}）：{e}")
                log(f"处理封底页时出错啦：{e}\n")
        else:
            log("未找到封底页文件，已跳过。\n")

    # ---------- 写出 ----------
    # 统一成绝对路径：CLI 已经把 `--out` 转绝对，库调用（外壳/测试）传相对路径时
    # 也保证 `MergeResult.output`/契约行是绝对路径（P04-R01 验收要求）。
    output = (os.path.abspath(os.path.expanduser(out)) if out
              else os.path.join(folder, f"合并{stamp()}.pdf"))
    merger.write(output)
    log(f"已生成合并文件（共 {merger.page_count} 页）：{output}\n")
    log("运行结束，请确认上方是否有错。\n")

    outline: list[tuple[str, int]] = []
    for node in _top_items_of(merger):
        outline.append(node)
    return MergeResult(output=output, page_count=merger.page_count,
                       outline=outline, warnings=warnings, missing=missing)


def _top_items_of(merger: Merger) -> list[tuple[str, int]]:
    """已登记顶层书签的 (名字, 页码) —— 引擎侧自己记的，不依赖重新读文件。"""
    return [(t.label, t.page_index) for t in merger._top]  # noqa: SLF001  同模块内部记账


def read_outline(path: str) -> list[tuple[str, int]]:
    """读回一个 PDF 的顶层书签 (名字, 页码)，供验收/外壳展示用。"""
    from pypdf import PdfReader               # noqa: PLC0415  延迟导入（见文件抬头）
    reader = PdfReader(path)
    out: list[tuple[str, int]] = []
    try:
        items = list(reader.outline)
    except Exception:  # noqa: BLE001
        return out
    for it in items:
        if isinstance(it, list):
            continue
        title = (getattr(it, "title", "") or "").strip()
        try:
            n = reader.get_destination_page_number(it)
        except Exception:  # noqa: BLE001
            n = -1
        out.append((title, n if isinstance(n, int) else -1))
    return out
