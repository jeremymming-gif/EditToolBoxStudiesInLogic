"""B-246c P1 · IA 映射表（D2）：Tk 版 15 页 → 新 4 组 IA 的逐页映射。

事实来源（只读）：
- Windows 页面契约（历史来源：旧 Tk 壳 sources-win/main.py，2026-11-01 批 108 已删除）：
  `nav_groups=[("单篇稿件",(0,3)),("整期装配",(4,5,6,7,2,8)),("引用库",(9,10,11,12,1,13))]` + 设置=14；
- 对等矩阵：C:\\slcn-work\\b304-2026-10-31\\evidence\\s2\\parity-matrix.md（15/15 PASS）。

新 IA（3 组：工作区 / 引用库 / 系统；侧栏只放一级页面 + 单篇/引用库的二级视图）：
  工作区 · 工作台 / 单篇稿件 / 整期装配（单页工作台，6 步在页内步骤栏）
  引用库 · 顶层 section：3 个不可点击组标签（查阅 / 采录 / 治理）+ 9 个叶子
  系统   · 设置 / 帮助与关于

批 115 D1-115-31/32（IA-B）：引用库设置不再占侧栏组，入口移到页头齿轮；
`bib:settings` 仍是可深链路由（⌘K/搜索直达），只在 NAV_TREE 的 `views` 中保留、不进 groups。

「取长补短」的两条关键取舍（详见 p1-design.md）：
1. 「参考文献规范化」随 GUI-06 纯迁到「引用库 → 回读校验」，与「入藏」同组（采录）：它的输入是
   单篇 returned.docx，体检放在入库前；「单篇稿件」页只留「去引用库 → 回读校验」跳转，不再有第三步骤。
2. 「整期装配」批 114 曾把 6 个步骤全部挂到侧栏；批 115（D1-115-26）起改为一个**单页工作台**：
   侧栏只留「整期装配」叶子，6 步在页内流水线栏切换——一期流水线天然有顺序，
   一屏同时看到「步骤栏 + 当前步骤 + 日志」比 6 次点页签更接近真实工作。
   `issue:overview` 仅保留一版兼容别名（落到默认步），不再有「流水线总览」屏。
"""
from __future__ import annotations

# ── 批 114 IA 定稿 + 批 115 整期收敛：一级页面 + 二级视图进左侧导航（用户 m03256）─
# 树结构是导航唯一事实来源：
#   · 父项 = 一级页面；点父项进入其 default 视图并展开；
#   · views = 父项下平铺的二级视图（单篇 2；整期批 115 D1-115-26 起叶子化，无子行，
#     6 步改在页内步骤栏，见 ISSUE_ROUTE_STEPS）；
#   · groups = 引用库的三层中间层（组标签 → 视图）；批 115 D1-115-31/32 起组标签
#     只是不可点击的文本行，叶子始终可见（不再折叠/记忆组展开）；
#   · views = 与 groups 并存的「仅路由」视图（引用库设置：页头齿轮/⌘K 可达，
#     侧栏不列）；nav_views() 把它排在组内叶子之后，保证 10 条路由不丢。
NAV_TREE: list[dict] = [
    {"group": "工作区", "entries": [
        {"key": "home", "label": "工作台", "icon": "AUTO_AWESOME"},
        {"key": "single", "label": "单篇稿件", "icon": "DESCRIPTION_OUTLINED",
         "default": "convert",
         "views": [{"key": "convert", "label": "初排转换"},
                   {"key": "proofcheck", "label": "一校核查"}]},
        # 批 115 D1-115-26：整期装配 = 单页工作台，侧栏只留叶子父项（无 views/groups）；
        # 6 步在页内步骤栏切换，default 只作无记忆时的静态兜底。
        {"key": "issue", "label": "整期装配", "icon": "VIEW_AGENDA_OUTLINED",
         "default": "infocheck"},
    ]},
    # 批 115 D1-115-31/32：引用库升为顶层 section（原「资料」组），组标签纯文本、
    # 9 叶常驻；设置只留路由（views），由页头齿轮/⌘K 进入。
    {"group": "引用库", "entries": [
        {"key": "bib", "label": "引用库", "icon": "LIBRARY_BOOKS_OUTLINED",
         "default": "overview",
         "groups": [
             {"key": "read", "label": "查阅",
              "views": [{"key": "overview", "label": "概览"},
                        {"key": "query", "label": "查库"}]},
             {"key": "intake", "label": "采录",
              "views": [{"key": "import", "label": "入藏"},
                        {"key": "normalize", "label": "回读校验"}]},
             {"key": "treat", "label": "治理",
              "views": [{"key": "review", "label": "待处理"},
                        {"key": "edit", "label": "编辑"},
                        {"key": "dupes", "label": "查重合并"},
                        {"key": "fieldfix", "label": "字段归位"},
                        {"key": "health", "label": "库健康"}]},
         ],
         # 仅路由：D1-115-32 把设置入口移到引用库页头齿轮；深链/搜索仍可用。
         "views": [{"key": "settings", "label": "设置", "route_only": True}]},
    ]},
    {"group": "系统", "entries": [
        {"key": "settings", "label": "设置", "icon": "SETTINGS_OUTLINED"},
        {"key": "help", "label": "帮助与关于", "icon": "HELP_OUTLINE"},
    ]},
]

# ── 批 115 D1-115-26：整期装配步骤路由（不是侧栏子项）──────────────────────
# 6 步只在页内步骤栏；本表只服务「可深链/可持久化」的路由、标题与 22 条路由计数。
# 标签沿用报告口径（页内详情标题由 pages.IssuePage.STEPS 自己维护）。
ISSUE_ROUTE_STEPS: list[dict[str, str]] = [
    {"key": "infocheck", "label": "检查tex信息"},
    {"key": "toc", "label": "生成目录"},
    {"key": "compileall", "label": "编译全部"},
    {"key": "merge", "label": "合并文件"},
    {"key": "cleantemp", "label": "删除临时文件"},
    {"key": "dirpush", "label": "目录摘要推送"},
]
_ISSUE_STEP_KEYS = frozenset(step["key"] for step in ISSUE_ROUTE_STEPS)
# 裸 issue / issue:overview 都是「默认步」别名；由 main 结合 last_step 解析。
_ISSUE_ALIAS_VIEWS = frozenset({"", "overview"})


def issue_step_keys() -> list[str]:
    """整期装配合法步骤 key（顺序即页内步骤栏顺序）。"""
    return [step["key"] for step in ISSUE_ROUTE_STEPS]


def issue_step_label(view_key: str) -> str:
    """整期装配步骤 → 中文标签；非步骤返回空串。"""
    return next((step["label"] for step in ISSUE_ROUTE_STEPS
                 if step["key"] == view_key), "")


def is_issue_landing_route(route: str | None) -> bool:
    """是否「落到默认步」的整期路由：`issue` / `issue:` / `issue:overview`。"""
    text = str(route or "").strip()
    page, _, view = text.partition(":")
    return page == "issue" and view in _ISSUE_ALIAS_VIEWS


# 兼容旧调用（test_p3 / 迁移期代码仍读 NAV_GROUPS）：由 NAV_TREE 派生，单一事实来源。
NAV_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    (section["group"],
     [(entry["key"], entry["label"], entry["icon"]) for entry in section["entries"]])
    for section in NAV_TREE
]


def nav_parent_entries() -> list[dict]:
    """所有父项（一级页面），带所属分组名。"""
    return [dict(entry, group=section["group"])
            for section in NAV_TREE for entry in section["entries"]]


def page_defaults() -> dict[str, str]:
    """一级页面 → 默认二级视图（无二级视图的页面为空串）。"""
    return {entry["key"]: str(entry.get("default", "") or "")
            for entry in nav_parent_entries()}


def nav_views(page_key: str) -> list[dict]:
    """页面的平铺二级视图（引用库把组展开后平铺，最后接仅路由视图）。

    引用库 = 3 组共 9 叶 + `views` 里的设置（route_only）→ 10 项；设置排在最后，
    与 `BibPage.VIEWS` 的既有顺序兼容，但侧栏只渲染 groups（见 main `_bib_nav_rows`）。
    """
    entry = next((item for item in nav_parent_entries() if item["key"] == page_key), None)
    if entry is None:
        return []
    views: list[dict] = []
    for group in entry.get("groups", []) or []:
        views.extend(dict(view, group=group["key"]) for view in group["views"])
    views.extend(dict(view) for view in (entry.get("views", []) or []))
    return views


def route_views(page_key: str) -> list[dict]:
    """路由层可用的视图：整期装配用步骤表（侧栏仍无子项），其余同 nav_views。"""
    if page_key == "issue":
        return list(ISSUE_ROUTE_STEPS)
    return nav_views(page_key)


def view_label(page_key: str, view_key: str) -> str:
    return next((str(view["label"]) for view in route_views(page_key)
                 if view["key"] == view_key), "")


def group_of_view(page_key: str, view_key: str) -> str:
    """引用库视图所属组的 key（其它页面返回空串）。"""
    return next((str(view["group"]) for view in nav_views(page_key)
                 if view["key"] == view_key and view.get("group")), "")


def group_label(page_key: str, group_key: str) -> str:
    entry = next((item for item in nav_parent_entries() if item["key"] == page_key), None)
    if entry is None:
        return ""
    return next((str(group["label"]) for group in entry.get("groups", []) or []
                 if group["key"] == group_key), "")


def group_views(page_key: str) -> list[dict]:
    """引用库：组行列表（[{key,label,views}]）；其它页面返回空列表。"""
    entry = next((item for item in nav_parent_entries() if item["key"] == page_key), None)
    if entry is None:
        return []
    return [{"key": str(group["key"]), "label": str(group["label"]),
             "views": list(group["views"])}
            for group in entry.get("groups", []) or []]


def all_routes() -> list[str]:
    """全部可持久化/可深链的路由（含父项默认视图、无子视图页面与旧别名）。

    批 115 D1-115-26：整期装配侧栏叶子化后，22 条路由口径不变——
    `issue:overview` 兼容别名 + 6 个页内步骤路由，共 7 条。
    """
    routes: list[str] = []
    for entry in nav_parent_entries():
        page = entry["key"]
        if page == "issue":
            routes.append("issue:overview")
            routes.extend(f"issue:{step['key']}" for step in ISSUE_ROUTE_STEPS)
            continue
        views = nav_views(page)
        if not views:
            routes.append(page)
            continue
        default = str(entry.get("default", "") or "")
        if default:
            routes.append(f"{page}:{default}")
        routes.extend(f"{page}:{view['key']}" for view in views
                      if view["key"] != default)
    return routes


def normalize_route(route: str | None) -> tuple[str, str]:
    """把任意路由串归一成 (page, view)；非法时回落到工作台。"""
    text = str(route or "").strip()
    page, _, view = text.partition(":")
    defaults = page_defaults()
    if page not in defaults:
        return "home", ""
    # 页面默认视图不一定是侧栏子项（整期装配的 6 步只在页内步骤栏），
    # 但它是合法页面状态，因此用 route_views 并入允许集合。
    default = defaults[page]
    allowed = {item["key"] for item in route_views(page)} | {default}
    if not view:
        view = default
    if view not in allowed:
        # 旧 `issue:overview` 等失效视图静态回落到页面默认步（D1-115-26）；
        # main 会在运行期再按 issue_state.last_step 解析裸 issue/overview 别名。
        view = default
    return page, view


def route_string(page: str, view: str = "") -> str:
    return f"{page}:{view}" if view else page


def route_labels(route: str | None) -> dict:
    """路由 → 面包屑/标题/分组标签。"""
    page, view = normalize_route(route)
    entry = next((item for item in nav_parent_entries() if item["key"] == page), None)
    page_label = str(entry["label"]) if entry else page
    label = view_label(page, view)
    group_key = group_of_view(page, view)
    group = group_label(page, group_key)
    if not view or not label:
        # 无子项页面的父页状态（如工作台/设置/帮助）→ 只显示父页名。
        crumb, title = page_label, page_label
    elif group:
        crumb, title = f"{page_label} / {group} / {label}", label
    else:
        crumb, title = f"{page_label} / {label}", label
    return {"page": page, "view": view, "page_label": page_label,
            "view_label": label, "group": group_key, "group_label": group,
            "crumb": crumb, "title": title}


IA_MAP: list[dict] = [
    {"idx": 0, "old_page": "初排转换", "old_group": "单篇稿件", "new_page": "单篇稿件",
     "new_view": "步骤「初排转换」", "component": "PathPicker + OptionRow×4 + 运行按钮 + LogConsole",
     "action": "convert（P2 接 Engine/convert.py）", "first_batch": "骨架已实现（模拟运行）"},
    {"idx": 1, "old_page": "参考文献规范化", "old_group": "引用库", "new_page": "引用库",
     "new_view": "视图「回读校验」（采录组）", "component": "PathPicker + OptionRow + 运行按钮 + LogConsole",
     "action": "checkbib（Engine/checkbib.py；只出报告、不并回、不写库）", "first_batch": "已实装（S3a2）"},
    {"idx": 2, "old_page": "合并文件", "old_group": "整期装配", "new_page": "整期装配",
     "new_view": "步骤 4「合并文件」", "component": "步骤行 + 状态徽标 + 运行按钮",
     "action": "merge（P2 接 Engine/mergepdf.py）", "first_batch": "骨架已实现（模拟运行）"},
    {"idx": 3, "old_page": "一校核查", "old_group": "单篇稿件", "new_page": "单篇稿件",
     "new_view": "步骤「一校核查」", "component": "PathPicker + 语种下拉 + --fix 开关 + 计数卡/规则表 + 打开报告",
     "action": "proofcheck（Engine/proofcheck.py；报告 md 摘要 + 契约行计数）", "first_batch": "已实装（W2b）"},
    {"idx": 4, "old_page": "检查tex信息", "old_group": "整期装配", "new_page": "整期装配",
     "new_view": "步骤 1「检查tex信息」", "component": "期目录 PathPicker + 单步运行 + 10 列结果表 + 摘要/打开报告",
     "action": "infocheck（Engine/infocheck.py --preview JSON）", "first_batch": "已实装（W2b）"},
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
     "new_view": "视图「概览」", "component": "StatCard×3 + EmptyState",
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

# ── P3 完成度（T1：15 页矩阵）─────────────────────────────────────────────
# done=本机可验证且已实现；readonly=只读视图/有意降级（写操作留 P4）；deferred=本机无法验证
P3_STATUS: dict[int, str] = {
    0: "done",
    1: "done",
    2: "done",
    3: "done",
    4: "done",
    5: "done",
    6: "done",
    7: "done",
    8: "done",
    9: "done",
    10: "done",
    11: "readonly",
    12: "readonly",
    13: "done",
    14: "done",
}

P3_NOTE: dict[int, str] = {
    11: "入藏会写库：P3 仅做只读说明与入口指引，写库留 P4（按 spec 需用户显式授权）",
    12: "待裁决/ack-duplicates 会写 sidecar：P3 只读展示口径，写操作留 P4",
    9: "概览：Engine/bibdb.py stats --json（只读）",
    10: "查库：Engine/bibdb.py query --json（只读）",
    14: "设置页：外观/缩放/路径/环境检查（envcheck --json）/关于与诊断",
}

for _row in IA_MAP:
    _row["p3_status"] = P3_STATUS.get(_row["idx"], "deferred")
    _row["p3_note"] = P3_NOTE.get(_row["idx"], "")

# IA 之外的 P3 页面：帮助与关于（新页）+ 任务书页（IA 无此页 → N/A）
P3_PAGES: list[dict] = [
    {"key": "help", "title": "帮助与关于", "group": "系统",
     "status": "done", "note": "版本/许可/链接/诊断入口；IA 无对应旧页"},
    {"key": "taskbook", "title": "任务书页", "group": "—",
     "status": "na", "note": "IA 15 页矩阵无此页 → N/A（任务书由引擎两段式协同在外部 open）"},
]


def stats() -> dict:
    total = len(IA_MAP)
    implemented = sum(
        1 for row in IA_MAP
        if row["first_batch"].startswith(("骨架已实现", "已实装"))
    )
    # IA_MAP idx1 从「单篇稿件」迁到「引用库 → 回读校验」（采录组）后，new_pages 仍是 4 个：
    # 单篇稿件 / 整期装配 / 引用库 / 设置；15 个旧页的映射总数不变。
    pages = sorted({row["new_page"] for row in IA_MAP})
    p3_done = sum(1 for row in IA_MAP if row.get("p3_status") == "done")
    p3_readonly = sum(1 for row in IA_MAP if row.get("p3_status") == "readonly")
    return {
        "legacy_pages": total,
        "legacy_entries": LEGACY_ENTRY_COUNT,
        "new_pages": len(pages),
        "new_page_keys": pages,
        "mapped_in_first_batch": implemented,
        "unmapped": total - implemented,
        "p3_done": p3_done,
        "p3_readonly": p3_readonly,
        "p3_deferred": total - p3_done - p3_readonly,
        "p3_extra_pages": [page["key"] for page in P3_PAGES if page["status"] == "done"],
        "p3_na_pages": [page["key"] for page in P3_PAGES if page["status"] == "na"],
    }
