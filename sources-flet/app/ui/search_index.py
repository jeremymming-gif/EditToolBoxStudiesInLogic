"""顶栏搜索索引（批 114 · 用户 m02696：搜索不能是假的）。

真实可执行的搜索：**页面 / 常用命令 / 最近记录**。本模块只做纯数据构建与匹配，
不 import flet、不碰 UI；`main.App` 负责执行 `action` 与渲染结果列表。

数据源（有就索引；没有的绝不假装）：
- 页面：`ui.ia_map.NAV_TREE`（6 个一级页面 + 二级视图；父项 + 子项都进索引）；
- 命令：主题切换、引擎刷新、运行 / 停止当前页、缩放、引用库 10 个视图、
  整期装配 6 个步骤；
- 最近记录：`SettingsStore.recent.*` 的非空路径 + `paths.notes_repo`；
- **不索引**：稿件正文、运行日志正文、`startup_log`（该模块按设计只记生命周期
  事件，不记标题，所以不拿它冒充「最近文稿」）；`form.*` 的填写值不单独成条，
  避免与「最近记录」重复出现。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

#: 结果条数上限（下拉列表一屏可读）
DEFAULT_LIMIT = 8

#: UI 底部固定说明：搜索边界写在界面上，避免用户误以为能搜正文。
SOURCE_NOTE = "只搜页面、命令与最近记录；不搜稿件正文与运行日志。"

#: 结果条目的种类文案（渲染侧也用它）。
KIND_LABELS = {"page": "页面", "command": "命令", "recent": "最近", "path": "路径"}

#: `recent` 键 → 人话标签；未知键给通用兜底，保证以后新增的最近记录也能被搜到。
RECENT_LABELS = {
    "single_source": "最近源稿",
    "single_out": "最近输出根目录",
    "issue_dir": "最近期目录",
    "toc_report": "最近目录报告",
    "proof_tex": "最近待核查 tex",
    "bib_returned": "最近 returned 文档",
    "bib_original": "最近原始稿",
    "bib_tex": "最近 bib tex",
    "bib_out": "最近输出目录",
    "bib_import_file": "最近导入文件",
    "dirpush_out": "最近摘要推送目录",
    "merge_out": "最近合并输出",
    "ai_reply": "最近 AI 回填文件",
    "ai_stage": "最近 AI 暂存目录",
}

#: 常用命令：(id, 标题, 详情, 关键词)。关键词含中英文别名，大小写不敏感。
_COMMANDS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("theme.toggle", "切换深浅色", "外观", ("主题", "深浅", "深色", "浅色", "暗色", "theme")),
    ("theme.light", "切换到浅色模式", "外观", ("浅色", "亮色", "light")),
    ("theme.dark", "切换到深色模式", "外观", ("深色", "暗色", "夜间", "dark")),
    ("engine.refresh", "刷新引擎状态", "系统", ("刷新", "引擎", "重载", "refresh", "engine")),
    ("run.current", "运行当前页", "命令", ("运行", "开始", "执行", "run")),
    ("stop.current", "停止当前运行", "命令", ("停止", "取消", "中断", "stop")),
    ("zoom.in", "放大界面", "外观", ("放大", "缩放", "zoom in")),
    ("zoom.out", "缩小界面", "外观", ("缩小", "缩放", "zoom out")),
    ("zoom.reset", "恢复 100% 缩放", "外观", ("缩放", "重置", "100%", "zoom reset")),
    ("new.single", "新建单篇（打开单篇稿件页）", "命令", ("新建", "单篇", "稿件", "new")),
)


@dataclass(frozen=True)
class SearchEntry:
    """一条可执行结果。

    `kind`   page / command / recent / path
    `key`    页面 key、命令 id、recent 键或路径
    `action` 执行串：`goto:<route>`（route 形如 `single:proofcheck`）/ 命令 id / `open:<path>`
    """

    kind: str
    key: str
    label: str
    detail: str = ""
    keywords: tuple[str, ...] = field(default_factory=tuple)
    action: str = ""

    @property
    def search_text(self) -> str:
        """匹配用文本：标题 + 详情 + 关键词，统一小写。"""
        parts = [self.label, self.detail, KIND_LABELS.get(self.kind, ""), *self.keywords]
        return " ".join(part for part in parts if part).casefold()


def nav_entries(nav_tree=()) -> list[SearchEntry]:
    """一级页面 + 二级视图 → 可导航结果（批 114：视图全部进导航后的搜索直达）。

    父项用页面默认路由（`goto:single` → main.goto 落到 `single:convert`），子项用
    显式路由（`goto:single:proofcheck` / `goto:bib:edit` / `goto:issue:toc`）。
    """
    entries: list[SearchEntry] = []
    for section in nav_tree or ():
        group = str(section.get("group", ""))
        for entry in section.get("entries", ()) or ():
            key = str(entry.get("key", ""))
            label = str(entry.get("label", key))
            entries.append(SearchEntry(
                kind="page", key=key, label=label,
                detail=f"{group} · 页面", keywords=(group, "页面", "打开"),
                action=f"goto:{key}",
            ))
            views: list[dict] = []
            if entry.get("views"):
                views = list(entry["views"])
            for view_group in entry.get("groups", ()) or ():
                views.extend(view_group.get("views", ()) or ())
            for view in views:
                view_key = str(view.get("key", ""))
                view_label = str(view.get("label", view_key))
                keywords = [group, label, "视图", "打开", view_key]
                if view.get("group"):
                    keywords.append(view["group"])
                entries.append(SearchEntry(
                    kind="page", key=f"{key}:{view_key}",
                    label=f"{label} · {view_label}",
                    detail=f"{group} · 视图",
                    keywords=tuple(keywords),
                    action=f"goto:{key}:{view_key}",
                ))
    return entries


def command_entries(bib_views=(), issue_steps=()) -> list[SearchEntry]:
    """常用命令 + 引用库 10 视图 + 整期装配 4 功能页 / 6 个底层操作。"""
    entries = [
        SearchEntry(kind="command", key=cmd_id, label=label, detail=detail,
                    keywords=keywords, action=cmd_id)
        for cmd_id, label, detail, keywords in _COMMANDS
    ]
    for view_key, view_label in bib_views or ():
        view_label = str(view_label)
        entries.append(SearchEntry(
            kind="command", key=f"bib.{view_key}",
            label=f"引用库 · {view_label}", detail="引用库视图",
            keywords=("引用库", "bib", str(view_key), view_label),
            action=f"goto:bib:{view_key}",
        ))
    for step_key, step_title in issue_steps or ():
        step_title = str(step_title)
        entries.append(SearchEntry(
            kind="command", key=f"issue.{step_key}",
            label=f"整期装配 · {step_title}", detail="整期步骤",
            keywords=("整期", "issue", str(step_key), step_title),
            action=f"goto:issue:{step_key}",
        ))
    return entries


def _basename(path: str) -> str:
    value = str(path or "").rstrip("/\\")
    name = os.path.basename(value)
    return name or value


def recent_entries(recent, paths=None) -> list[SearchEntry]:
    """最近记录：recent.* 里的非空路径 + paths.notes_repo。"""
    entries: list[SearchEntry] = []
    if isinstance(recent, dict):
        for key, value in recent.items():
            if not isinstance(value, str) or not value.strip():
                continue
            label = RECENT_LABELS.get(str(key), f"最近记录 · {key}")
            path = value.strip()
            entries.append(SearchEntry(
                kind="recent", key=str(key), label=f"{label}：{_basename(path)}",
                detail=path, keywords=(label, str(key), _basename(path), "最近", "记录"),
                action=f"open:{path}",
            ))
    if isinstance(paths, dict):
        for key, value in paths.items():
            if not isinstance(value, str) or not value.strip():
                continue
            path = value.strip()
            label = "笔记仓目录" if key == "notes_repo" else f"路径 · {key}"
            entries.append(SearchEntry(
                kind="path", key=str(key), label=f"{label}：{_basename(path)}",
                detail=path, keywords=(label, str(key), _basename(path), "路径"),
                action=f"open:{path}",
            ))
    return entries


def build_index(*, nav_tree=(), bib_views=(), issue_steps=(),
                recent=None, paths=None) -> list[SearchEntry]:
    """汇总全部可执行条目（页面 → 命令 → 最近记录，顺序稳定便于测试）。"""
    return [
        *nav_entries(nav_tree),
        *command_entries(bib_views, issue_steps),
        *recent_entries(recent, paths),
    ]


def split_query(query: str) -> list[str]:
    """按空白与常见分隔符切词；中文串整体保留。"""
    value = str(query or "").strip().casefold()
    if not value:
        return []
    return [token for token in re.split(r"[\s,，、;；/]+", value) if token]


def match_entries(entries, query: str, *, limit: int = DEFAULT_LIMIT) -> list[SearchEntry]:
    """即时过滤：所有词都命中才返回；标题前缀/包含优先，页面/命令优先于记录。

    大小写不敏感；中英文别名都在 `keywords` 里。`limit` 限制下拉条数。
    """
    tokens = split_query(query)
    if not tokens:
        return []
    whole = "".join(tokens)
    kind_boost = {"page": 30, "command": 20, "recent": 10, "path": 5}
    scored: list[tuple[int, int, SearchEntry]] = []
    for index, entry in enumerate(entries or ()):
        haystack = entry.search_text
        if not all(token in haystack for token in tokens):
            continue
        label = entry.label.casefold()
        score = 0
        if label.startswith(whole):
            score += 100
        elif label.startswith(tokens[0]):
            score += 60
        if tokens[0] in label:
            score += 40
        if whole in haystack:
            score += 10
        score += kind_boost.get(entry.kind, 0)
        scored.append((-score, index, entry))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [entry for _score, _index, entry in scored[:max(1, int(limit))]]
