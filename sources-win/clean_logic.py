"""B-281：「删除临时文件」页的**纯逻辑**（模式 / argv / 确认文案 / 撤销判定）。

为什么不写在界面里：mac 的 `DeleteTempFeature.CleanMode` 三种模式要在 Windows 壳上
逐项对齐，而「对等」必须能被 CI 验证 —— 本模块不 import tkinter，于是
`sources-win/tests/test_bridge.py` 的 (k) 组在 mac / Linux / CI 上就能逐条断言，
不用开窗口、不用 Windows 真机。

口径与 `Engine/cleantemp.py`、mac `sources-mac/Features/DeleteTempFeature.swift`
**同一份**：
  · aux（默认，旧行为）：清编译中间文件；界面一律 `--trash`（移入回收站，可撤销）；
  · delivery：交付目录收尾（白名单 + tex 引用闭包优先，引用缺失整批拒绝）；
  · workspace：工作区旧件（每期只留最新一份「合并*.pdf」，固定交付物不动）。
  delivery / workspace 默认也移入回收站（引擎默认口径），`--delete` 才是永久删。
"""
from __future__ import annotations

#: 与 mac `CleanMode` 的 rawValue 一一对应；顺序 = 下拉框顺序 = mac `allCases`。
MODE_ORDER: tuple[str, ...] = ("aux", "delivery", "workspace")
#: 默认模式 = 老行为（编译中间文件），与 mac `DeleteTempFeature.run()` 默认值一致。
DEFAULT_MODE = "aux"

#: 模式标题：与 mac `CleanMode.title` **逐字一致**。
MODE_TITLES: dict[str, str] = {
    "aux": "编译临时文件",
    "delivery": "交付收尾（白名单）",
    "workspace": "工作区旧件（每期留最新合并 PDF）",
}

#: 模式说明：与 mac `CleanMode.detail` **逐字一致**（显示在下拉框下方）。
MODE_DETAILS: dict[str, str] = {
    "aux": "清编译中间文件；不动 .tex/.pdf/.sty/.bib/.log，也不进 original/（作者来稿）。",
    "delivery": ("交付前收尾：保留白名单与 tex 引用闭包，清 .ai前/.log/备份/核对报告/aux；"
                 "引用缺失时引擎整批拒绝，一个文件都不动。"),
    "workspace": ("工作区旧件：每期只留最新一份「合并*.pdf」，固定交付物不动；"
                  "解析不出时间时保守保留。"),
}

#: 运行前确认框的标题：与 mac `DeleteTempFeature.confirm(folder:mode:)` 逐字一致。
MODE_CONFIRM_TITLES: dict[str, str] = {
    "aux": "删除临时文件（可撤销）",
    "delivery": "交付收尾清理（可撤销）",
    "workspace": "工作区旧件清理（可撤销）",
}

#: 运行前确认框：标题与正文与 mac `DeleteTempFeature.confirm(folder:mode:)` 逐字一致；
#: 正文里的 `{folder}` 由界面替换成实际目录（Tk 不做字符串插值）。
_CONFIRM_INFO: dict[str, str] = {
    "aux": (
        "将把「{folder}」里的编译中间文件移入同目录的 .toolbox-trash/（可撤销）。\n"
        "只处理 .aux/.bbl/.bcf/.blg/.out/.run.xml/.synctex.gz/.DS_Store；\n"
        "不会动 .tex/.pdf/.sty/.bib/.log，也不进 original/（作者来稿）。\n"
        "移入之后，可点横幅上的「撤销清理」还原。"
    ),
    "delivery": (
        "将把「{folder}」里的交付残留移入同目录的 .toolbox-trash/（可撤销），"
        "白名单外的一律不动。\n"
        "保留：*.tex/*.pdf/*.bib/*.sty/*.cls、tex 引用闭包"
        "（\\includegraphics/\\input/\\include/\\addbibresource/\\bibliography 指向的文件与目录）、"
        "original/、人工 *交付清单*.md。\n"
        "清理：.ai前/、.log/、*.bak、_AI*/、_文献核实.md、_回读校验报告.md、"
        "_参考文献核对.txt、_一校核查*.md、_原稿.pdf、aux 全套、.DS_Store、_待核/。\n"
        "引用缺失时引擎整体拒绝、一个文件都不动；未识别文件保留并给 warning。\n"
        "移入之后，可点横幅上的「撤销清理」还原。"
    ),
    "workspace": (
        "将把「{folder}」里的工作区旧件移入同目录的 .toolbox-trash/（可撤销）。\n"
        "每期目录里的 合并*.pdf 只保留文件名时间最新的一份；固定名 合并.pdf 永不删。\n"
        "解析不出时间的唯一一份保留；多份都解析失败时全部保留并给 warning。\n"
        "固定交付物 000/、contents.*、Chinese Content.*、扉页/编委/封底/ 与人工文件永不删；\n"
        "另清 信息检查*/、*.bak/、*.coop-bak-*/、*_待核/、.DS_Store。\n"
        "移入之后，可点横幅上的「撤销清理」还原。"
    ),
}

#: 清空回收站确认框：标题固定「（不可撤销）」，正文与 mac `confirmEmptyTrash` 逐字一致。
_EMPTY_TRASH_TITLE = "清空回收站（不可撤销）"
_EMPTY_TRASH_INFO = (
    "将永久删除「{folder}」下 .toolbox-trash/ 里的所有回收目录，清空后无法恢复。\n"
    "只影响回收站，不动期目录里的其它文件。\n"
    "还想撤销上一次清理的话，请先点「取消」，改用横幅上的「撤销清理」。"
)


def normalize_mode(mode: str) -> str:
    """把模式名/标题归一成 raw value（`aux` / `delivery` / `workspace`）；不认识的抛 ValueError。"""
    raw = (mode or "").strip()
    if raw in MODE_TITLES:
        return raw
    for key in MODE_ORDER:
        if MODE_TITLES[key] == raw:
            return key
    raise ValueError(f"不认识的清理模式：{mode!r}（可用：{' / '.join(MODE_ORDER)}）")


def mode_title(mode: str) -> str:
    """模式下拉的显示文字（与 mac `CleanMode.title` 逐字一致）。"""
    return MODE_TITLES[normalize_mode(mode)]


def mode_detail(mode: str) -> str:
    """下拉框下方的模式说明（与 mac `CleanMode.detail` 逐字一致）。"""
    return MODE_DETAILS[normalize_mode(mode)]


def mode_labels() -> list[str]:
    """下拉框的值（顺序 = `MODE_ORDER` = mac `allCases`）。"""
    return [MODE_TITLES[key] for key in MODE_ORDER]


def mode_key(label: str) -> str:
    """下拉框选中的标题 → raw value（界面取值用；也接受 raw value）。"""
    return normalize_mode(label)


def cleantemp_args(folder: str, mode: str = DEFAULT_MODE, dry_run: bool = False,
                   delete: bool = False) -> list[str]:
    """拼「运行一次清理」的参数（不含解释器与脚本名，`cleantemp.py` 之后那一段）。

    口径：
      · aux：默认 `--trash`（移入回收站，界面永远走可撤销那条）；`delete=True` 才是
        引擎老行为（直接删，不带 `--trash`）——那是 CLI 专用，界面不提供。
      · delivery / workspace：`--mode …`，默认移入回收站；`--delete` 永久删。
    dry-run 只读不改（界面里「先预演」勾选时走这条）。
    """
    mode = normalize_mode(mode)
    args = ["--folder", folder]
    if mode == DEFAULT_MODE:
        if dry_run:
            args.append("--dry-run")
        if not delete:
            args.append("--trash")
        return args
    args += ["--mode", mode]
    if dry_run:
        args.append("--dry-run")
    if delete:
        args.append("--delete")
    return args


def confirm_text(mode: str) -> tuple[str, str]:
    """运行前确认框（标题, 正文）。正文里的 `{folder}` 由界面替换。

    与 mac `DeleteTempFeature.confirm(folder:mode:)` 同义：说清「清什么 / 不碰什么 /
    可撤销」，按钮用动词（先预演 → 具体清单 → askyesno）。
    """
    key = normalize_mode(mode)
    return MODE_CONFIRM_TITLES[key], _CONFIRM_INFO[key]


def empty_trash_text() -> tuple[str, str]:
    """「清空回收站」确认框（标题, 正文）；正文里的 `{folder}` 由界面替换。

    必须说清**不可撤销**（永久删除、清空后无法恢复），与 mac `confirmEmptyTrash` 同义。
    """
    return _EMPTY_TRASH_TITLE, _EMPTY_TRASH_INFO


def undo_available(argv: list[str] | tuple[str, ...]) -> bool:
    """这次运行的契约行会不会给出「回收目录：…」——即横幅该不该出现「撤销清理」。

    只在**真的移入了回收站**时为真：dry-run / --delete（永久删）/ --restore（本身是撤销）/
    --empty-trash（永久清空）都给不出可撤销的回收目录。
    delivery / workspace 默认就是移入回收站（引擎口径），所以带 `--mode` 且没 `--delete`
    时为真；aux 则必须显式带 `--trash`（引擎老行为是直接删）。
    """
    args = list(argv)
    if any(flag in args for flag in ("--empty-trash", "--restore", "--delete", "--dry-run")):
        return False
    if "--mode" in args:
        return True
    return "--trash" in args
