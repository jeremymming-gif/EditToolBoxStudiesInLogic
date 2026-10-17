"""SLCN 稿件转换引擎（Python）。

版本号**只有这一份**：mac 的 `build.sh` 把它写进 `Info.plist`、Windows 的打包脚本
写进 exe 的版本信息、`convert.py --version` 直接打印它。
（从前 App 版本写死在 `build.sh` 的 plist 里，双平台之后必然漂移。）
"""

from __future__ import annotations

__version__ = "1.0.5"
