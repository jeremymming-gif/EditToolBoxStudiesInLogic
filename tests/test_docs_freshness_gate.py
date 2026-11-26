from tools.docs_freshness_gate import (
    V_KEYWORDS, BANNED_TERMS, get_current_version,
    check_changelog, check_banned, check_rule_count, check_keywords, run_all,
)

def test_version_source_is_1_7_2():
    assert get_current_version("Engine/slcn/__init__.py") == "1.7.2"

def test_v_keywords_are_one_shared_table():
    for kw in ("AI 辅助编译修复", "确认并发送一次", "设置中心", "命令面板",
               "AI 辅助处理 Bib", "original/", "字段差异", "字段归位", "整期工作台"):
        assert kw in V_KEYWORDS
    assert "案例法" not in V_KEYWORDS          # §10.2 #10：案例法不是用户功能

def test_changelog_top_requires_version_date_and_categories():
    good = "## 1.7.2（2026-11-26）\n✨ 新增\n💎 改进\n🐞 修复\n"
    assert check_changelog(good, "1.7.2") == []
    assert check_changelog("## 1.7.1\n", "1.7.2") != []   # 陈旧 → FAIL

def test_banned_terms_caught_in_user_pages_but_not_changelog_history():
    assert check_banned("见 88d01d4 与 B-358，路径 /Users/x/y", "docs/index.md") != []
    assert check_banned("参考文献规范化", "docs/features/single.md") != []
    assert check_banned("诊断记录见附件", "docs/index.md") != []
    assert check_banned("B-358、批 112", "docs/reference/changelog.md") == []  # 历史豁免

def test_rule_count_requires_29_not_26():
    assert check_rule_count("按手册 29 条规则核查", "docs/features/single.md") == []
    assert check_rule_count("26 条规则", "docs/features/single.md") != []

def test_keyword_coverage_reports_missing_terms():
    miss = check_keywords("只有设置中心", "docs/features/settings.md")
    assert any("AI 辅助编译修复" in m for m in miss)

def test_run_all_aggregates_failures(tmp_path):
    # 最小夹具内联构建，无需仓库外的 fixture 目录
    (tmp_path / "Engine" / "slcn").mkdir(parents=True)
    (tmp_path / "Engine" / "slcn" / "__init__.py").write_text(
        '__version__ = "1.7.2"\n', encoding="utf-8")
    (tmp_path / "docs" / "reference").mkdir(parents=True)
    (tmp_path / "docs" / "reference" / "changelog.md").write_text(
        "## 1.7.1（2026-11-01）\n✨ 新增\n", encoding="utf-8")
    fails = run_all(str(tmp_path))   # changelog 停在 1.7.1 → 应报陈旧
    assert any("changelog" in f for f in fails)
