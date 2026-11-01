"""GUI 自动走查（真实桌面、真实进程、截图基线）。

默认不参与 CI：只有显式 ``--run-gui`` 或 ``SLCN_GUI_AUTO=1`` 时才运行，
其余情况由本目录 conftest 统一 skip（见 README.md）。
"""
