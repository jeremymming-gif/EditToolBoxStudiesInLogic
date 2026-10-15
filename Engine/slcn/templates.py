"""模板选择：让用户自行指定生成时采用的模板文件。

需求（用户 2026-10 提出）
------------------------
生成目标文件时所采用的模板文件必须**可由用户选择**，默认按稿件语种决定：

    中文稿件 → 编辑笔记/模板文件/SLCN-MIX.sty
    英文稿件 → 编辑笔记/模板文件/SLEN.sty

一个必须注意的坑
----------------
骨架 tex 里写死了 `\\usepackage{SLCN-MIX}`（CN）或 `\\usepackage{SLEN}`（EN）。
如果用户换成别的 sty（例如历史版本 `SLCN.sty`、`SLCN-MIX4.sty`），
而 tex 里的 `\\usepackage` 没跟着改，编译会直接失败——而且报错信息
（"File `SLCN-MIX.sty' not found"）离真正的原因很远。
所以本模块在选定 sty 后，会**同步改写骨架里的 \\usepackage 行**。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# 模板目录定位：引擎现在与界面同住一个项目，位置有两处，所以**逐级向上找**，
# 不写死层数（合并进 EditToolBox 时这里曾经差点写错）：
#   仓库里：<仓库>/Engine/slcn/         → <仓库>/编辑笔记/模板文件
#   app 包：<app>/Contents/Resources/Engine/slcn/  → …/Resources/编辑笔记/模板文件
def _find_repo_dir() -> str:
    """向上找出含 `编辑笔记/模板文件` 的那一层（仓库根 / app 包的 Resources）。"""
    d = os.path.dirname(os.path.abspath(__file__))
    cands = []
    for _ in range(4):
        d = os.path.dirname(d)
        cands.append(d)
    for c in cands:
        if os.path.isdir(os.path.join(c, "编辑笔记", "模板文件")):
            return c
    return cands[1]  # 都没找到时给最可能的一处，便于报错信息定位


REPO_ROOT = _find_repo_dir()
TPL_DIR = os.path.join(REPO_ROOT, "编辑笔记", "模板文件")


@dataclass(frozen=True)
class TemplatePreset:
    """一套模板：版面样式文件（.sty）+ 可填写的骨架（.tex）。"""

    lang: str
    sty: str
    skeleton: str

    @property
    def sty_name(self) -> str:
        """不含扩展名的 sty 名，用于改写 \\usepackage。"""
        return os.path.splitext(os.path.basename(self.sty))[0]


# 语种 → 默认模板。sty 的取值按用户要求固定。
PRESETS: dict[str, TemplatePreset] = {
    "zh": TemplatePreset(
        lang="zh",
        sty=os.path.join(TPL_DIR, "SLCN-MIX.sty"),
        skeleton=os.path.join(TPL_DIR, "CN_template.tex"),
    ),
    "en": TemplatePreset(
        lang="en",
        sty=os.path.join(TPL_DIR, "SLEN.sty"),
        skeleton=os.path.join(TPL_DIR, "EN_template.tex"),
    ),
}

# 模板目录下可用于覆盖的候选（供界面/CLI 列举）
AVAILABLE_STY = ("SLCN-MIX.sty", "SLCN-MIX4.sty", "SLCN.sty", "SLEN.sty")


@dataclass
class ResolvedTemplate:
    lang: str
    sty: str
    skeleton: str
    sty_overridden: bool = False
    skeleton_overridden: bool = False

    @property
    def sty_name(self) -> str:
        return os.path.splitext(os.path.basename(self.sty))[0]


def resolve(
    lang: str,
    sty: str | None = None,
    skeleton: str | None = None,
) -> ResolvedTemplate:
    """解析出本次生成要用的模板。

    lang       'zh' 或 'en'，决定默认值
    sty        用户指定的 .sty 路径（None 则用该语种默认值）
    skeleton   用户指定的骨架 .tex 路径（None 则用该语种默认值）
    """
    preset = PRESETS.get(lang, PRESETS["zh"])
    if sty and not os.path.isabs(sty):
        # 允许只给文件名，如 `--sty SLCN.sty`，自动到模板目录里找
        cand = os.path.join(TPL_DIR, sty)
        sty = cand if os.path.exists(cand) else os.path.abspath(sty)
    if skeleton and not os.path.isabs(skeleton):
        cand = os.path.join(TPL_DIR, skeleton)
        skeleton = cand if os.path.exists(cand) else os.path.abspath(skeleton)

    return ResolvedTemplate(
        lang=preset.lang,
        sty=sty or preset.sty,
        skeleton=skeleton or preset.skeleton,
        sty_overridden=bool(sty),
        skeleton_overridden=bool(skeleton),
    )


def describe(t: ResolvedTemplate) -> str:
    parts = [f"语种 {t.lang}", f"版面样式 {os.path.basename(t.sty)}"]
    if t.sty_overridden:
        parts.append("（用户指定）")
    parts.append(f"骨架 {os.path.basename(t.skeleton)}")
    if t.skeleton_overridden:
        parts.append("（用户指定）")
    return "，".join(parts)


def validate(t: ResolvedTemplate) -> list[str]:
    """检查模板文件是否存在，返回问题清单（空 = 没问题）。"""
    problems = []
    for label, p in (("版面样式", t.sty), ("骨架", t.skeleton)):
        if not os.path.exists(p):
            problems.append(f"{label}文件不存在：{p}")
        elif os.path.getsize(p) == 0:
            problems.append(f"{label}文件为空：{p}")
    return problems


_USE_PKG_RE = re.compile(r"\\usepackage(?:\[[^\]]*\])?\{\s*(SLCN-MIX4|SLCN-MIX|SLCN|SLEN)\s*\}")


def sync_package_line(skeleton_text: str, sty_name: str) -> tuple[str, str | None]:
    """把骨架里的 \\usepackage{...} 改成与所选 sty 一致。

    返回 (新文本, 改动说明或 None)。找不到该行时原样返回。
    """
    m = _USE_PKG_RE.search(skeleton_text)
    if not m:
        return skeleton_text, None
    old = m.group(1)
    if old == sty_name:
        return skeleton_text, None
    new_text = skeleton_text[: m.start(1)] + sty_name + skeleton_text[m.end(1) :]
    return new_text, f"\\usepackage{{{old}}} → \\usepackage{{{sty_name}}}（与所选模板同步）"
