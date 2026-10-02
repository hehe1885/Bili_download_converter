"""PyQt5 图形界面。

用法::

    python main.py            # 默认启动 GUI
    python main.py --cli ...  # 强制走命令行
"""

from __future__ import annotations

__all__ = ["run"]


def run(argv: list[str] | None = None) -> int:
    """启动图形界面（延迟导入，避免无 PyQt5 时 main.py 直接崩）。"""
    from .main_window import run as _run

    return _run(argv)
