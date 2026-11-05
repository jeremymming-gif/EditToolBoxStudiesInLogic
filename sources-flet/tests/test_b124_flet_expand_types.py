"""批 124：Flet 1.0.5 的 expand 只能 bool|int —— 防止浮点权重再次炸整页 patch。

现场（用户 m03067）：整期装配跑完「检查 tex 信息」后，切功能页/页内步骤
内容不刷新、按钮像失效，必须重启。根因是 `INFO5_WEIGHTS` 用了浮点 1.6，
Flet 在构造整页 update patch 时 `_before_update_safe()` 校验抛
`ValueError: expand must be of type bool or int, got <class 'float'>`，
patch 没发出去，客户端停在旧树；后续 patch 也基于错位的服务端 prev 树，
所以越点越乱。这里锁住三件事：权重、表格 expand 类型、跑完信息检查后的
整页重建树 expand 类型。
"""
from __future__ import annotations

import flet as ft

from ui import checks as CK
from ui import pages as P

from test_b112_post_b import _build_issue, _walk


def _bad_expand(root) -> list[tuple[str, object]]:
    bad = []
    for ctl in _walk(root):
        value = getattr(ctl, "expand", None)
        if isinstance(value, float):
            bad.append((type(ctl).__name__, value))
    return bad


def _article(**kw):
    data = {
        "folderName": "Jingru_Kou", "folder": "01 Jingru_Kou",
        "volume": "5", "issue": "2026", "year": "2026",
        "startPage": "1", "endPage": "16", "pages": "16",
        "actualPDFPages": 16, "fontSize": "10.5", "lineSpacing": "1.35",
        "firstAuthorChinese": "寇静茹", "chineseTitle": "可知性悖论、形式可证与认知实现",
        "hasPDF": True,
    }
    data.update(kw)
    return CK.normalize_article(data)


def test_info5_weights_are_int():
    assert CK.INFO5_WEIGHTS
    assert all(isinstance(w, int) and not isinstance(w, bool) and w > 0
               for w in CK.INFO5_WEIGHTS), CK.INFO5_WEIGHTS
    # 原比例 5 : 2 : 1.6 : 1.6 : 2（浮点 1.6 是事故源），整数化 ×10 后保持比例。
    assert CK.INFO5_WEIGHTS == (50, 20, 16, 16, 20)
    assert [w / CK.INFO5_WEIGHTS[0] for w in CK.INFO5_WEIGHTS] == [1.0, 0.4, 0.32, 0.32, 0.4]


def test_info_table_expand_types_valid():
    table = CK.build_info_table5(
        _build_issue()[0].t,
        [_article(), _article(folderName="Liang_Zhang", folder="02 Liang_Zhang")],
        active_filter="all",
        on_toggle=lambda key: None,
    )
    assert _bad_expand(table) == []


def test_issue_detail_after_infocheck_has_valid_expand():
    page, _root = _build_issue()
    # 模拟 infocheck 跑完：结果 payload/articles 已就绪，页面按当前视图重建。
    page._info_payload = {"articles": [_article()], "notes": [], "summary": {}}
    page._info_articles = [_article()]
    page._info_report = "/tmp/信息检查.txt"
    page.activate("infocheck", source="test")
    page._rebuild_detail()
    assert _bad_expand(page._content_box) == []


def test_view_switch_after_infocheck_rebuilds_content():
    page, _root = _build_issue()
    page._info_payload = {"articles": [_article()], "notes": [], "summary": {}}
    page._info_articles = [_article()]
    page.activate("compileall", source="test")
    page.activate("infocheck", source="test")
    assert page.selected == "infocheck"
    assert page._content_box.controls, "切换视图后内容树不能为空"
    assert _bad_expand(page._content_box) == []
