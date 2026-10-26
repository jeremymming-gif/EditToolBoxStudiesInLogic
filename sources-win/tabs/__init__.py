"""Windows 壳的标签页子包（B-230 按 tab 拆分，纯搬运）。

把 `sources-win/` 放上 `sys.path`（**幂等**）：与 `main.py:29` 同一个作用，
保证 `import tabs.convert` 单独可用时能找到 `engine.py`/`settings.py` 等兄弟模块。
PyInstaller 以 `main.py` 为入口，静态分析自动收集本子包。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
