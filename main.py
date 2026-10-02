"""程序入口。

默认启动 PyQt5 图形界面；显式传 ``--cli`` 或环境里没有 PyQt5 时退回命令行。

    python main.py                          启动图形界面
    python main.py --cli -i <缓存目录>      走命令行
"""

from __future__ import annotations

import sys


def main() -> int:
    argv = sys.argv[1:]

    if "--cli" in argv:
        argv = [a for a in argv if a != "--cli"]
        from cli import main as cli_main

        return cli_main(argv)

    try:
        from gui.main_window import run

        return run(argv)
    except ImportError as exc:
        print(f"[提示] 未加载图形界面（{exc}），切换到命令行模式。\n"
              f"       安装界面依赖: pip install PyQt5\n")
        from cli import main as cli_main

        return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
