"""批 115 P0-1（设计评审，用户批准方案 A）：k = 用户 zoom，DPI 交给 Flutter/OS。

根因回归：旧 `Tokens` 把 `dpi / 144` 乘进 k，mac 上 `_system_dpi()` 拿不到 Windows
API 返回 96，于是 zoom=1.0 实际按 96/144=0.667 渲染（220 侧栏 → 146.7）。

锁四件事：
1. `Tokens.k == zoom`（与 dpi 无关），`window_size` 跟着 k；
2. 旧 zoom 一次性迁移纯函数（1.6@96 → 1.067，144 DPI 不变）；
3. `_compute_max_zoom` 不含 DPI 因子且只由工作区逻辑尺寸决定；
4. 顶栏不再显示页面标题文字（页面内 h1/面包屑负责层级），meta 仍可见。

不启动 Flet 窗口。
"""
from __future__ import annotations

import pytest

import app.main as M
from app.ui.tokens import BASE_H, BASE_W, ZOOM_MAX, ZOOM_MIN, Tokens


# ── 1. Tokens：k 只等于用户 zoom ──────────────────────────────────────────
@pytest.mark.parametrize("dpi", [96, 120, 144, 192])
def test_k_is_zoom_only_regardless_of_dpi(dpi):
    t = Tokens(zoom=1.0, dpi=dpi)
    assert t.k == pytest.approx(1.0)
    # dpi 仍保留作诊断（截图元数据），但不参与像素计算。
    assert t.screenshot_dpi == dpi
    assert not hasattr(t, "dpi_k"), "DPI 因子已从 Tokens 删除（批 115 P0-1 方案 A）"


def test_zoom_1_6_renders_at_1_6_not_67_percent():
    t = Tokens(zoom=1.6, dpi=96)
    assert t.k == pytest.approx(1.6)
    assert t.px(220) == pytest.approx(352.0)   # 220 侧栏不再缩成 146.7
    assert t.font("body") == pytest.approx(round(13 * 1.6, 2))


@pytest.mark.parametrize("zoom", [0.8, 1.0, 1.25, 1.6, 2.0])
def test_window_size_follows_k(zoom):
    t = Tokens(zoom=zoom, dpi=96)
    assert t.window_size == (round(BASE_W * t.k), round(BASE_H * t.k))
    assert t.window_size == (round(BASE_W * zoom), round(BASE_H * zoom))


def test_tokens_clamps_zoom_to_supported_range():
    assert Tokens(zoom=0.1).k == pytest.approx(ZOOM_MIN)
    assert Tokens(zoom=9.9).k == pytest.approx(ZOOM_MAX)


# ── 2. 旧 zoom 一次性迁移 ─────────────────────────────────────────────────
def test_migrate_zoom_value_matches_old_effective_rendering():
    # 旧 k = 1.6 × 96/144 = 1.067（用户当前观感）；新语义下直接存 1.067。
    assert M._migrate_zoom_value(1.6, 96) == pytest.approx(1.067, abs=1e-9)
    # Windows 真 144 DPI：旧 k 就是 zoom，迁移不改变观感。
    assert M._migrate_zoom_value(1.0, 144) == pytest.approx(1.0, abs=1e-9)
    # 120 DPI：0.9 × 120/144 = 0.75。
    assert M._migrate_zoom_value(0.9, 120) == pytest.approx(0.75, abs=1e-9)


def test_migrate_zoom_value_rounds_3_and_caps_high():
    # 换算值是等效观感，纯函数只挡上界；下限由 migrate_zoom_settings 夹。
    assert M._migrate_zoom_value(2.5, 192) == pytest.approx(ZOOM_MAX)   # 3.333 → 2.0
    assert M._migrate_zoom_value(1.3333, 144) == pytest.approx(1.333)
    assert M._migrate_zoom_value(0.9, 120) == pytest.approx(0.75)


def test_migrate_settings_clamps_to_supported_range():
    low = _FakeStore({"zoom": 0.9})     # 0.75 等效值低于 ZOOM_MIN → 存 0.8
    M.migrate_zoom_settings(low, 120, exists=True)
    assert low.data["zoom"] == pytest.approx(ZOOM_MIN)
    high = _FakeStore({"zoom": 2.5})    # 3.333 高于 ZOOM_MAX → 存 2.0
    M.migrate_zoom_settings(high, 192, exists=True)
    assert high.data["zoom"] == pytest.approx(ZOOM_MAX)


class _FakeStore:
    """SettingsStore 的最小替身：get/set/save 三件套（不碰磁盘）。"""

    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})
        self.saves = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saves += 1


def test_migrate_settings_writes_new_zoom_and_version_once():
    store = _FakeStore({"zoom": 1.6})
    record = M.migrate_zoom_settings(store, 96, exists=True)
    assert store.data["zoom"] == pytest.approx(1.067, abs=1e-9)
    assert store.data["ui_scale_version"] == 2
    assert store.saves == 1
    assert record["old"] == pytest.approx(1.6)
    assert record["new"] == pytest.approx(1.067, abs=1e-9)
    # 幂等：版本号已 2 → 重启不再二次缩小。
    again = M.migrate_zoom_settings(store, 96, exists=True)
    assert again == {}
    assert store.saves == 1
    assert store.data["zoom"] == pytest.approx(1.067, abs=1e-9)


def test_migrate_settings_first_install_only_stamps_version():
    store = _FakeStore()
    record = M.migrate_zoom_settings(store, 96, exists=False)
    assert record == {"first_install": True}
    assert store.data["ui_scale_version"] == 2
    assert "zoom" not in store.data       # 首装默认 1.0，不写脏值
    assert store.saves == 0               # 随首次 save 落盘即可，不在启动时建文件


def test_migrate_settings_failure_does_not_block_startup():
    class _BrokenStore(_FakeStore):
        def save(self):
            raise OSError("disk full")

    store = _BrokenStore({"zoom": 1.6})
    record = M.migrate_zoom_settings(store, 96, exists=True)
    assert "save_error" in record
    assert store.data["zoom"] == pytest.approx(1.067, abs=1e-9)


# ── 3. 缩放上限：不再含 DPI 因子 ──────────────────────────────────────────
def _app_with_work(work_w: int, work_h: int, system_dpi: int):
    app = M.App.__new__(M.App)       # 不进 __init__（会连引擎/设置/窗口）
    app.work_w, app.work_h = work_w, work_h
    app.system_dpi = system_dpi
    return app


def test_compute_max_zoom_ignores_design_dpi():
    app = _app_with_work(1920, 1080, 96)
    values = set()
    for dpi in (96, 120, 144, 192):
        app.dpi = dpi                 # P2_DPI 只作诊断，不许影响上限
        values.add(app._compute_max_zoom())
    assert values == {1.07}           # (1080-160)/860 ≈ 1.070；高度是瓶颈


def test_max_zoom_depends_only_on_logical_work_area():
    # 同一块工作区的物理 2×（96→192 DPI）必须得到同一上限。
    assert M.max_zoom_for_work(1920, 1080, 96) == M.max_zoom_for_work(3840, 2160, 192)
    assert M.max_zoom_for_work(1920, 1080, 96) == pytest.approx(1.07)
    # 上限仍被 ZOOM_MIN/ZOOM_MAX 夹住（极小屏 / 极大屏）。
    assert M.max_zoom_for_work(800, 600, 96) == pytest.approx(ZOOM_MIN)
    assert M.max_zoom_for_work(7680, 4320, 192) == pytest.approx(ZOOM_MAX)


# ── 4. 顶栏标题去重 ───────────────────────────────────────────────────────
def _toolbar_app():
    app = M.App.__new__(M.App)
    app.t = Tokens("light", 1.0, 96, "c")
    app._search_query = ""
    app._toolbar_row = None
    app.current_page_key = "home"
    app.goto = lambda *a, **k: None
    app.toggle_sidebar = lambda *a, **k: None
    app._toolbar_spec = lambda: ("文稿", "引擎就绪 · 本地只读", None)
    return app


def test_toolbar_hides_page_title_but_keeps_meta_and_object():
    app = _toolbar_app()
    app._toolbar_content()
    # 标题对象仍在（复用断言 test_b114_toolbar_reuse 依赖它），但不可见、不占位。
    assert app._toolbar_title in app._toolbar_row.controls
    assert app._toolbar_title.visible is False
    assert app._toolbar_title.value == "文稿"
    # 左侧只留 meta，meta 正常可见。
    assert app._toolbar_meta.visible is True
    assert app._toolbar_meta.value == "引擎就绪 · 本地只读"
