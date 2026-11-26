"""用户文档 / 官网主页更新的一致性检查（按任务分节追加）。

约定：每个任务只追加自己命名空间下的用例（本批为 ``test_task234_*``），
不重写、不删除其它任务追加的用例。
"""

from __future__ import annotations

import pathlib
import re


# ── Task 2：changelog 顶部补当前版本的人读条目 ─────────────────────────────


def test_task234_changelog_top_entry_is_current_version_human_readable():
    text = pathlib.Path("docs/reference/changelog.md").read_text(encoding="utf-8")
    first = next(line for line in text.splitlines() if line.startswith("## "))
    assert first.startswith("## 1.7.2（2026-11-26）")
    section = text.split("## 1.7.2", 1)[1].split("\n## ", 1)[0]
    for marker in ("✨ 新增", "💎 改进", "🐞 修复"):
        assert marker in section
    for keyword in (
        "AI 辅助编译修复",
        "确认并发送一次",
        "设置中心",
        "命令面板",
        "字段差异",
        "整期工作台",
        "original/",
        "onedir",
    ):
        assert keyword in section, keyword
    for bad in ("commit", "B-", "批次", "/Users/", "六步流水线", "案例法", "26 条"):
        assert bad not in section, bad


# ── Task 3：导航与侧栏按目标信息架构重排 ───────────────────────────────────


def test_task234_config_nav_and_sidebar_match_target_ia():
    raw = pathlib.Path("docs/.vitepress/config.mts").read_text(encoding="utf-8")

    # 维护者文档不再出现在公开导航里
    assert "activeMatch: '/(reference|maintain)/'" not in raw
    assert "activeMatch: '/reference/'" in raw
    for dead in ("/maintain/quickstart", "/maintain/troubleshooting"):
        assert dead not in raw

    # nav 五项，顺序固定；「参考」指向版本记录
    nav_src = raw.split("nav: [", 1)[1].split("sidebar: [", 1)[0]
    nav_links = re.findall(r"link: '([^']+)'", nav_src)
    assert nav_links == [
        "/guide/quickstart",
        "/features/single",
        "/templates/",
        "/reference/changelog",
        "https://github.com/jeremymming-gif/EditToolBoxStudiesInLogic/releases/latest",
    ], nav_links

    # sidebar 五组、组内 5 / 5 / 5 / 4 项，顺序固定
    sidebar_src = raw.split("sidebar: [", 1)[1].split("socialLinks", 1)[0]
    groups = re.findall(r"text: '([^']+)',\s*\n\s*items: \[", sidebar_src)
    assert groups == ["入门", "功能", "模板", "参考"], groups
    links = re.findall(r"link: '([^']+)'", sidebar_src)
    assert links == [
        "/guide/quickstart",
        "/guide/install",
        "/guide/system-requirements",
        "/reference/faq",
        "/guide/version-history",
        "/features/single",
        "/features/assembly",
        "/features/bibdb",
        "/features/settings",
        "/features/search",
        "/templates/",
        "/templates/word",
        "/templates/latex",
        "/templates/reference-style",
        "/templates/checklist",
        "/reference/changelog",
        "/reference/known-limitations",
        "/reference/environment-check",
        "/reference/submission-template",
    ], links

    # 构建放行规则与阅读体验配置保持不变
    for kept in (
        r"/^\/templates\/.+\.(docx|pdf|tex|bib)(\?.*)?$/",
        r"/^\/templates\/SLCN-MIX\.sty(\?.*)?$/",
        r"/^\/templates\/SLEN\.sty(\?.*)?$/",
        "outline: { level: [2, 3], label: '本页目录' }",
        "docFooter: { prev: '上一篇', next: '下一篇' }",
        "lastUpdated: { text: '最后更新于' }",
        "text: '在 GitHub 上编辑此页'",
    ):
        assert kept in raw, kept


# ── Task 4：首页任务卡 + 截图永久取消 ──────────────────────────────────────


def test_task234_index_homepage_task_cards_and_entry_points():
    text = pathlib.Path("docs/index.md").read_text(encoding="utf-8")

    # 四张任务卡：按「我当前要做的事」组织，而不是按软件模块
    for card in ("处理一篇稿件", "装配一整期", "治理引用库", "调整设置"):
        assert card in text, card
    assert text.count('class="doc-card"') == 4

    # 三条价值主张
    for claim in ("稿件与产物留在你本机", "按本刊规范可配置", "离线可跑"):
        assert claim in text, claim

    # 「第一次来？」五项入口
    for anchor in ("安装与首次打开", "三分钟上手", "系统要求", "常见问题", "版本记录"):
        assert anchor in text, anchor

    # 页脚四件套
    for footer in ("文档", "问题与反馈", "版本记录", "获取帮助"):
        assert footer in text, footer

    # 用户文本不写内部语汇、旧名与手写版本号
    for bad in ("参考文献规范化", "--metadata-json", "诊断记录", "v1.5.0", "1.7.2",
                "万 token", "/Users/", "B-", "批次"):
        assert bad not in text, bad


def test_task234_no_screenshots_anywhere():
    text = pathlib.Path("docs/index.md").read_text(encoding="utf-8")
    assert "shot-placeholder" not in text
    assert "界面截图" not in text
    assert "截图" not in text
    assert "/maintain/quickstart" not in text
    assert "/maintain/" not in text

    css = pathlib.Path("docs/.vitepress/theme/custom.css").read_text(encoding="utf-8")
    assert ".shot-placeholder" not in css
    assert "截图" not in css

    shot_readme = pathlib.Path("docs/public/screenshots/README.md").read_text(encoding="utf-8")
    assert "本站不使用截图" in shot_readme
    for leftover in ("01-convert.png", "02-checkbib.png", "03-bibdb-stats.png",
                     "04-bibdb-query.png", "05-bibdb-settings.png", "06-assembly.png",
                     "EditToolBoxWebFig", "占位"):
        assert leftover not in shot_readme, leftover


# ── Task 5：功能域页与指南页按六段式重写 ───────────────────────────────────

PAGES = ["docs/features/single.md", "docs/features/assembly.md", "docs/features/bibdb.md",
         "docs/features/settings.md", "docs/features/search.md"]


def test_task56_assembly_is_four_chips_not_six_steps():
    text = pathlib.Path("docs/features/assembly.md").read_text(encoding="utf-8")
    for chip in ("生成目录", "编译并合并", "清理临时文件", "生成推送文案"):
        assert chip in text, chip
    assert "六步流水线" not in text and "六步顺序" not in text


def test_task56_feature_pages_follow_six_section_order_and_keywords():
    texts = {p: pathlib.Path(p).read_text(encoding="utf-8") for p in PAGES}
    for p, t in texts.items():
        assert "On this page" in t or "本页目录" in t or "# " in t, p
        for section in ("入口", "前提", "操作", "示例", "限制"):
            assert section in t, (p, section)
    joined = "\n".join(texts.values())
    for kw in ("AI 辅助编译修复", "确认并发送一次", "设置中心", "命令面板",
               "AI 辅助处理 Bib", "original/", "字段差异", "字段归位", "整期工作台"):
        assert kw in joined, kw
    assert "29 条" in texts["docs/features/single.md"]


def test_task56_user_pages_have_no_banned_terms():
    for p in PAGES + ["docs/index.md", "docs/guide/quickstart.md",
                      "docs/reference/faq.md", "docs/reference/known-limitations.md"]:
        t = pathlib.Path(p).read_text(encoding="utf-8")
        assert "参考文献规范化" not in t, p
        assert "诊断包 zip" not in t and "导出 zip" not in t, p


def test_task56_new_pages_exist_and_submission_template_moved():
    for p in ("docs/guide/system-requirements.md", "docs/guide/version-history.md",
              "docs/features/settings.md", "docs/features/search.md",
              "docs/reference/submission-template.md"):
        assert pathlib.Path(p).is_file(), p
    assert not pathlib.Path("docs/guide/submission-template.md").exists()

    # 新页没有手写版本流水，版本一律指向版本记录
    history = pathlib.Path("docs/guide/version-history.md").read_text(encoding="utf-8")
    assert "/reference/changelog" in history
    assert not re.search(r"1\.\d+\.\d+", history), "版本记录页不抄版本号流水"


def test_task56_guide_pages_follow_current_ia():
    quick = pathlib.Path("docs/guide/quickstart.md").read_text(encoding="utf-8")
    install = pathlib.Path("docs/guide/install.md").read_text(encoding="utf-8")
    # 整期是单页工作台，不是六步流水线
    assert "单页工作台" in quick or "整期工作台" in quick
    assert "六步流水线" not in quick and "流水线总览" not in quick
    # 安装：Windows 保留原目录结构；macOS 不再宣传 onefile 解包
    assert "SLCNEngine/SLCNEngine.exe" in install
    assert "onefile" not in install
    # 旧名清零
    for text in (quick, install):
        assert "参考文献规范化" not in text


# ── Task 6：使用说明.md 收敛 ────────────────────────────────────────────────


def test_task56_usage_manual_converged():
    t = pathlib.Path("使用说明.md").read_text(encoding="utf-8")
    assert "单页工作台" in t and "四个独立功能" in t
    assert "六步流水线" not in t
    assert "参考文献规范化" not in t
    assert "复制诊断信息" in t and "导出为文本文件" in t
    assert "SLCNEngine/SLCNEngine.exe" in t or "SLCNEngine.exe" in t
    assert "29 条" in t and "26 条" not in t


def test_task56_usage_manual_has_no_cli_ledger_or_machine_terms():
    t = pathlib.Path("使用说明.md").read_text(encoding="utf-8")
    assert "退出码 2 与负数退出码" not in t        # §8 流水账已收敛
    assert "py -3.14 convert.py --source" not in t  # §9.1 CLI 表已收敛为一句话指针
    for bad in ("commit", "B-", "批次", "/Users/", "诊断记录"):
        assert bad not in t, bad


def test_task56_usage_manual_nine_settings_routes():
    t = pathlib.Path("使用说明.md").read_text(encoding="utf-8")
    for route in ("外观", "引擎与环境", "路径", "AI 与模型", "网络代理",
                  "日志与诊断", "高级与关于", "窗口与托盘", "语言与地区"):
        assert route in t, route


# ── Task 7：软件内帮助文案对齐（入口 + DOCS 指向用户站）──────────────────
def _help_const(src: str, name: str):
    """按纯数据常量解析 help_content.py，不 import UI（不需要 Flet 运行期）。"""
    import ast

    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(target, "id", "") == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"help_content.{name} 不是可解析的纯数据常量")


def test_task789_help_entries_and_docs_pointer():
    src = pathlib.Path("sources-flet/app/ui/help_content.py").read_text(encoding="utf-8")
    for kw in ("AI 辅助编译修复", "设置中心", "命令面板"):
        assert kw in src, kw
    assert "九个设置" in src or "九路由" in src or "九个详情" in src
    assert "Engine/docs/GUIDE.md" not in src              # 不再指向维护者文档
    assert "DOCS = (" in src and "用户站" in src            # DOCS 纯文本改指用户站
    assert "__version__" not in src                       # 版本不手写
    assert "1.7.2" not in src


def test_task789_help_entries_carry_the_three_new_entries():
    src = pathlib.Path("sources-flet/app/ui/help_content.py").read_text(encoding="utf-8")
    entries = _help_const(src, "ENTRIES")
    names = {name for name, _where, _detail in entries}
    for name in ("AI 辅助编译修复", "设置中心", "命令面板"):
        assert name in names, name
    for name, where, detail in entries:                   # 仍是「功能 / 入口 / 说明」
        assert name and where, name
        if name != "AI 任务书":                            # 该条的启用状态由页面补
            assert detail, name


def test_task789_help_docs_point_to_user_site_not_maintainer_doc():
    src = pathlib.Path("sources-flet/app/ui/help_content.py").read_text(encoding="utf-8")
    docs = _help_const(src, "DOCS")
    assert docs
    for label, text in docs:
        assert "Engine/docs" not in label and "Engine/docs" not in text, label
    assert any("用户站" in label for label, _text in docs)


# ── Task 8：README 文档地图与安装口径 ─────────────────────────────────────
def test_task789_readme_doc_map_and_install_wording():
    t = pathlib.Path("README.md").read_text(encoding="utf-8")
    assert "docs/maintain/quickstart.md" not in t
    assert "docs-internal" in t or "EditToolBox-notes" in t
    assert "SLCNEngine.exe" in t and "同一文件夹" in t
    assert "只读视图" not in t                     # 不声称引用库只读
    assert "解压" in t                             # 安装口径仍在


def test_task789_readme_has_no_screenshots():
    t = pathlib.Path("README.md").read_text(encoding="utf-8")
    assert "![" not in t
    assert "screenshots/" not in t


# ── Task 9：release.yml 陈旧用户可见陈述（仅文本）────────────────────────
def test_task789_release_text_fixes():
    t = pathlib.Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "六步流水线" not in t
    assert "四个独立功能" in t or "页内四个功能" in t
    assert "引用库改为**只读视图**" not in t
    assert "查阅 / 采录 / 治理" in t or "逐次确认" in t


# ── Task 10：内部区物理外移与引用修复 ─────────────────────────────────────
def test_internal_docs_removed_from_public():
    for rel in ("docs/maintain/quickstart.md", "docs/maintain/troubleshooting.md",
                "docs/maintain/windows-gate-timeout-handoff.md",
                "docs/reference/development.md", "docs/reference/windows-smoke-checklist.md"):
        assert not pathlib.Path(rel).exists(), rel
    notes = pathlib.Path("/Users/zhangwenming/Documents/GitHub/EditToolBox-notes")
    assert (notes / "docs-internal/maintain/quickstart.md").exists()
    assert (notes / "docs-internal/reference/development.md").exists()
    t = pathlib.Path("使用说明.md").read_text(encoding="utf-8")
    assert "Windows 冒烟验证请联系维护者" in t and "docs/reference/windows-smoke-checklist.md" not in t
    assert "taskkill" not in t and "21ccc4dc" not in t


# ── Task 11：docs.yml 接入门禁并扩大触发路径 ──────────────────────────────
def test_docs_yml_triggers_on_version_change():
    t = pathlib.Path(".github/workflows/docs.yml").read_text(encoding="utf-8")
    for path in ("Engine/slcn/__init__.py", "使用说明.md",
                 "sources-flet/app/ui/help_content.py", ".github/workflows/release.yml"):
        assert path in t, path
    build = t.index("安装依赖并构建")
    gate = t.index("docs_freshness_gate")
    assert gate < build                      # 门禁在 build 之前


# ── Task 12：本地自查入口接入门禁 ─────────────────────────────────────────
def test_selfcheck_hooks_gate():
    t = pathlib.Path("Engine/tools/selfcheck.py").read_text(encoding="utf-8")
    assert "docs_freshness_gate" in t
