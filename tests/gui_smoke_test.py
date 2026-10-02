"""GUI 冒烟测试：不弹窗（offscreen 平台），验证窗口能建起来、选项能往返。

用法::

    python tests/gui_smoke_test.py           # 只测界面与选项往返
    python tests/gui_smoke_test.py --real    # 额外跑一次真实扫描 + 真实转换

``--real`` 需要本机有 ffmpeg，并且 ``--input`` 指向真实缓存目录。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from gui.main_window import MainWindow  # noqa: E402
from m4sconverter.models import (  # noqa: E402
    ConflictMode,
    Engine,
    HashMode,
    ItemState,
    LayoutMode,
    NameFilter,
    Options,
)

#: 真实转换测试的输入目录。默认取「当前用户桌面\download」，
#: 免得把某个人的绝对路径写死在仓库里；可用环境变量覆盖。
DEFAULT_INPUT = os.environ.get(
    "M4S_TEST_INPUT", str(Path.home() / "Desktop" / "download")
)

_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}: got={got!r} want={want!r}")
        _failures.append(label)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def _silence_dialogs() -> None:
    """offscreen 下对话框会永久阻塞，全部替换成自动应答。"""
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)


def wait_until(app: QApplication, predicate, timeout: float = 1800.0) -> bool:
    """转 Qt 事件循环直到 predicate 为真。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.03)
    return False


def test_panel_roundtrip(win: MainWindow) -> None:
    section("选项面板往返")
    opt = Options()
    opt.import_dir = DEFAULT_INPUT
    opt.output_dir = r"D:\tmp\out"
    opt.output_per_import = True
    opt.layout = LayoutMode.TITLE_DIR
    opt.name_filter = NameFilter.STANDARD
    opt.conflict = ConflictMode.RENAME
    opt.hash_mode = HashMode.DEDUP_ONLY
    opt.engine = Engine.FFMPEG
    opt.workers = 3
    opt.write_nfo = False
    opt.embed_cover = True
    opt.container = "mkv"
    opt.ass.enabled = True
    opt.ass.font_name = "微软雅黑"
    opt.ass.font_size = 34
    opt.ass.alpha = 0.55
    opt.ass.roll_time = 12.5
    opt.ass.canvas = "1280x720"
    opt.ass.block_keywords = ["抽奖", "广告"]
    opt.ass.convert = "s -> r"

    win.options_panel.load_from(opt)
    got = win.options_panel.collect()

    check("导入目录", got.import_dir, DEFAULT_INPUT)
    check("输出目录", got.output_dir, r"D:\tmp\out")
    check("按导入分子目录", got.output_per_import, True)
    check("布局", got.layout, LayoutMode.TITLE_DIR)
    check("文件名清洗", got.name_filter, NameFilter.STANDARD)
    check("清洗只有两项", win.options_panel.filter_combo.count(), 2)
    check("清洗有解释行", bool(win.options_panel.filter_hint.text().strip()), True)
    check("同名冲突", got.conflict, ConflictMode.RENAME)
    check("hash 策略", got.hash_mode, HashMode.DEDUP_ONLY)
    check("引擎", got.engine, Engine.FFMPEG)
    check("并发数", got.workers, 3)
    check("不写 nfo", got.write_nfo, False)
    check("嵌入封面", got.embed_cover, True)
    check("容器", got.container, "mkv")
    check("字体", got.ass.font_name, "微软雅黑")
    check("字号", got.ass.font_size, 34)
    check("透明度", round(got.ass.alpha, 2), 0.55)
    check("滚动时长", got.ass.roll_time, 12.5)
    check("画布", got.ass.canvas, "1280x720")
    check("屏蔽词", got.ass.block_keywords, ["抽奖", "广告"])
    check("转换规则", got.ass.convert, "s -> r")


def test_custom_canvas(win: MainWindow) -> None:
    section("自定义画布")
    win.options_panel.canvas_combo.setCurrentIndex(3)   # 自定义…
    win.options_panel.canvas_edit.setText("1080x1920")
    check("画布取值", win.options_panel.collect().ass.canvas, "1080x1920")
    win.options_panel.canvas_combo.setCurrentIndex(0)   # 跟随视频
    check("回到 auto", win.options_panel.collect().ass.canvas, "auto")


def test_layout_switching(win: MainWindow) -> None:
    section("布局切换与置灰")
    win.options_panel.layout_combo.setCurrentIndex(0)   # 平铺
    check("平铺模板", win.options_panel.template_edit.text(), "{title}.{ext}")
    check("模板框置灰", win.options_panel.template_edit.isEnabled(), False)
    win.options_panel.layout_combo.setCurrentIndex(4)   # 自定义
    check("自定义可编辑", win.options_panel.template_edit.isEnabled(), True)
    win.options_panel.layout_combo.setCurrentIndex(3)   # 按合集建文件夹
    check("合集布局模板", win.options_panel.template_edit.text(), "{group}-{up}/{part}.{ext}")


def test_real(win: MainWindow, app: QApplication, input_dir: str, output_dir: str) -> None:
    section("真实扫描 + 真实转换")
    win.options_panel.load_from(Options())
    win.top_import_edit.setText(input_dir)
    win.options_panel.output_edit.setText(output_dir)
    win.options_panel.layout_combo.setCurrentIndex(0)      # 平铺
    win.options_panel.conflict_combo.setCurrentIndex(1)     # 覆盖
    win.options_panel.workdir_edit.setText(output_dir)      # 中间产物留在测试目录，便于排查
    win.options_panel.workers_spin.setValue(1)

    win.on_scan()
    ok = wait_until(app, lambda: win.scan_worker is not None
                    and win.scan_worker.isFinished() and bool(win.items))
    if not ok:
        print("  [FAIL] 扫描没有返回任何任务")
        _failures.append("real-scan")
        return
    check("扫描到任务", len(win.items) > 0, True)
    print(f"         扫到 {len(win.items)} 个任务，表格 {win.table.rowCount()} 行")
    check("表格行数一致", win.table.rowCount(), len(win.items))

    target = win.items[0]
    print(f"         本次只转第 1 个：{target.primary_title()}")
    win._set_all_checked(False)
    win.items[0].selected = True
    win.table.item(0, 0).setCheckState(2)  # Qt.Checked

    win.on_start()
    ok = wait_until(app, lambda: win.convert_worker is not None
                    and win.convert_worker.isFinished(), timeout=600)
    if not ok:
        print("  [FAIL] 转换线程超时未结束")
        _failures.append("real-convert-timeout")
        return

    # 处理剩余排队信号
    for _ in range(20):
        app.processEvents()
        time.sleep(0.02)

    check("任务状态", target.state, ItemState.DONE)
    out = target.output_path
    print(f"         输出：{out}")
    check("输出文件存在", bool(out and Path(out).is_file()), True)
    if out:
        base = Path(out).with_suffix("")
        for suffix in (".ass", ".hash", ".nfo"):
            check(f"附属 {suffix}", base.with_suffix(suffix).is_file(), True)


def main() -> int:
    args = sys.argv[1:]
    real = "--real" in args
    input_dir = DEFAULT_INPUT
    if "--input" in args:
        input_dir = args[args.index("--input") + 1]

    app = QApplication([])
    _silence_dialogs()

    tmp = Path(tempfile.mkdtemp(prefix="m4s-gui-test-"))
    win = MainWindow(config_path=tmp / "config.json")
    win.show()
    app.processEvents()

    print(f"窗口标题：{win.windowTitle()}")
    print(f"表格列数：{win.table.columnCount()}")
    print(f"选项面板组数：{win.options_panel.findChildren(type(win.options_panel.import_edit)) and 'ok'}")

    test_panel_roundtrip(win)
    test_custom_canvas(win)
    test_layout_switching(win)

    if real:
        out = tmp / "out"
        out.mkdir(parents=True, exist_ok=True)
        test_real(win, app, input_dir, str(out))

    win.close()
    app.processEvents()

    print()
    if _failures:
        print(f"失败 {len(_failures)} 项：{', '.join(_failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
