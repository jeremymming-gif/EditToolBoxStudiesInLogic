"""B-246c P1 · IA 映射表（D2）：Tk 版 15 页 → 新 4 组 IA 的逐页映射。

事实来源（只读）：
- Windows 页面契约：sources-win/main.py `nav_groups=[("单篇稿件",(0,3)),("整期装配",(4,5,6,7,2,8)),("引用库",(9,10,11,12,1,13))]` + 设置=14；
- 对等矩阵：C:\\slcn-work\\b304-2026-10-31\\evidence\\s2\\parity-matrix.md（15/15 PASS）。

新 IA（4 组，组内「页面」= 一级导航；页内「视图/步骤」= 胶囊或分步）：
  工作区 · 工作台 / 单篇稿件 / 整期装配
  资料   · 引用库
  系统   · 设置

「取长补短」的两条关键取舍（详见 p1-design.md）：
1. 「参考文献规范化」回归「单篇稿件」生产链（mac 分组口径）：它的输入是单篇 returned.docx，
   Tk 版 B-286 把它降级到「引用库」组是历史产物；新 IA 只保留一条学习路径。
2. 「整期装配」从 6 个平级页签改为一个页面的 6 个**步骤**：一期流水线天然有顺序，
   一屏能看到「进度 + 当前步骤 + 日志」比 6 次点页签更接近真实工作。
"""
from __future__ import annotations

NAV_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("工作区", [("home", "工作台", "AUTO_AWESOME"),
             ("single", "单篇稿件", "DESCRIPTION_OUTLINED"),
             ("issue", "整期装配", "VIEW_AGENDA_OUTLINED")]),
    ("资料", [("bib", "引用库", "LIBRARY_BOOKS_OUTLINED")]),
    ("系统", [("settings", "设置", "SETTINGS_OUTLINED")]),
]

LEGACY_TAB_ORDER = [
    "初排转换", "参考文献规范化", "合并文件", "一校核查", "检查tex信息", "生成目录",
    "编译全部", "删除临时文件", "目录摘要推送", "库概况", "查库", "入藏", "待裁决",
    "引用库设置", "设置",
]  # 15 页；Tk 版 9 个平级入口 = 前 8 页 + 设置（目录摘要推送/引用库系列为后加页）

IA_MAP: list[dict] = [
    {"idx": 0, "old_page": "初排转换", "old_group": "单篇稿件", "new_page": "单篇稿件",
     "new_view": "步骤「初排转换」", "component": "PathPicker + OptionRow×4 + 运行按钮 + LogConsole",
     "action": "convert（P2 接 Engine/convert.py）", "first_batch": "骨架已实现（模拟运行）"},
    {"idx": 1, "old_page": "参考文献规范化", "old_group": "引用库", "new_page": "单篇稿件",
     "new_view": "步骤「参考文献规范化」", "component": "PathPicker + OptionRow + 运行按钮",
     "action": "checkbib（P2 接 Engine/checkbib.py）", "first_batch": "骨架已实现（模拟运行）"},
    {"idx": 2, "old_page": "合并文件", "old_group": "整期装配", "new_page": "整期装配",
     "new_view": "步骤 4「合并文件」", "component": "步骤行 + 状态徽标 + 运行按钮",
     "action": "merge（P2 接 Engine/mergepdf.py）", "first_batch": "骨架已实现（模拟运行）"},
    {"idx": 3, "old_page": "一校核查", "old_group": "单篇稿件", "new_page": "单篇稿件",
     "new_view": "步骤「一校核查」", "component": "PathPicker + OptionRow + 运行按钮 + 结果卡",
     "action": "proofcheck（P2 接 Engine/proofcheck.py）", "first_batch": "骨架已实现（模拟运行）"},
    {"idx": 4, "old_page": "检查tex信息", "old_group": "整期装配", "new_page": "整期装配",
     "new_view": "步骤 1「检查 tex 信息」", "component": "期目录 PathPicker + 单步运行",
     "action": "infocheck（P2 接 Engine/infocheck.py）", "first_batch": "骨架已实现（模拟运行）"},
    {"idx": 5, "old_page": "生成目录", "old_group": "整期装配", "new_page": "整期装配",
     "new_view": "步骤 2「生成目录」", "component": "单步运行 + 结果徽标",
     "action": "toc（P2 接 Engine/toc.py）", "first_batch": "骨架已实现（模拟运行）"},
    {"idx": 6, "old_page": "编译全部", "old_group": "整期装配", "new_page": "整期装配",
     "new_view": "步骤 3「编译全部」", "component": "单步运行 + 进度条 + 日志台",
     "action": "compileall（P2 接 Engine/compileall.py）", "first_batch": "骨架已实现（模拟运行）"},
    {"idx": 7, "old_page": "删除临时文件", "old_group": "整期装配", "new_page": "整期装配",
     "new_view": "步骤 5「删除临时文件」", "component": "单步运行 + ConfirmDialog（危险动作）",
     "action": "cleantemp（P2 接 Engine/cleantemp.py）", "first_batch": "骨架已实现（模拟运行）"},
    {"idx": 8, "old_page": "目录摘要推送", "old_group": "整期装配", "new_page": "整期装配",
     "new_view": "步骤 6「目录摘要推送」", "component": "单步运行 + 预览选项",
     "action": "dirpush（P2 接 Engine/dirpush.py）", "first_batch": "骨架已实现（模拟运行）"},
    {"idx": 9, "old_page": "库概况", "old_group": "引用库", "new_page": "引用库",
     "new_view": "视图「概况」", "component": "StatCard×3 + EmptyState",
     "action": "读取 .bib 统计（P2 接 Engine/slcn/bib）", "first_batch": "骨架已实现（占位数据）"},
    {"idx": 10, "old_page": "查库", "old_group": "引用库", "new_page": "引用库",
     "new_view": "视图「查库」", "component": "搜索框 + 高级检索 + 结果空态",
     "action": "查库（P2 接 Engine）", "first_batch": "骨架已实现（占位）"},
    {"idx": 11, "old_page": "入藏", "old_group": "引用库", "new_page": "引用库",
     "new_view": "视图「入藏」", "component": "PathPicker(.bib) + OptionRow + 运行按钮",
     "action": "入藏（P2 接 Engine）", "first_batch": "骨架已实现（模拟运行）"},
    {"idx": 12, "old_page": "待裁决", "old_group": "引用库", "new_page": "引用库",
     "new_view": "视图「待处理」", "component": "EmptyState + 刷新 + 筛选胶囊",
     "action": "裁决队列（P2 接 Engine）", "first_batch": "骨架已实现（占位）"},
    {"idx": 13, "old_page": "引用库设置", "old_group": "引用库", "new_page": "引用库",
     "new_view": "视图「设置」", "component": "verifyMax 输入框 + Switch×2 + 保存",
     "action": "写 settings.bibdb（P2 接 settings.py 纯函数）", "first_batch": "骨架已实现（落盘）"},
    {"idx": 14, "old_page": "设置", "old_group": "设置", "new_page": "设置",
     "new_view": "页面（外观/路径/AI/日志）", "component": "Switch + Slider + PathPicker + TextField",
     "action": "settings_store（P2 接 settings.py，密钥掩码）", "first_batch": "骨架已实现（落盘）"},
]

# 旧 9 个平级入口（B-289 之前）：前 8 页 + 设置；其余 6 页为后续批次新增
LEGACY_ENTRY_COUNT = 9


def stats() -> dict:
    total = len(IA_MAP)
    implemented = sum(1 for row in IA_MAP if row["first_batch"].startswith("骨架已实现"))
    pages = sorted({row["new_page"] for row in IA_MAP})
    return {
        "legacy_pages": total,
        "legacy_entries": LEGACY_ENTRY_COUNT,
        "new_pages": len(pages),
        "new_page_keys": pages,
        "mapped_in_first_batch": implemented,
        "unmapped": total - implemented,
    }
