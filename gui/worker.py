"""后台工作线程：目录扫描 与 转换队列。

两个线程都以 Qt 信号回到 GUI 线程，绝不在工作线程里碰控件。
转换线程额外实现了「同名冲突询问」：工作线程阻塞等待，GUI 线程弹框，
结果通过 :meth:`ConvertWorker.answer_conflict` 回填。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Iterable, Sequence

from PyQt5.QtCore import QThread, pyqtSignal

from m4sconverter.converter import Converter
from m4sconverter.models import ConvertResult, Options, ScanReport, VideoItem
from m4sconverter.scanner import scan

LOG = logging.getLogger("m4s")


class ScanWorker(QThread):
    """在后台扫描缓存目录。"""

    #: 扫描完成，携带 ScanReport。
    scanned = pyqtSignal(object)
    #: 扫描抛异常，携带错误文本。
    failed = pyqtSignal(str)

    def __init__(
        self,
        root: str | Path,
        options: Options,
        extra_exclude: Iterable[Path] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self.options = options
        self.extra_exclude = list(extra_exclude) if extra_exclude else None

    def run(self) -> None:  # noqa: D102 - QThread 入口
        try:
            report: ScanReport = scan(self.root, self.options,
                                      extra_exclude=self.extra_exclude)
        except Exception as exc:  # noqa: BLE001 - 任何异常都回报给界面
            LOG.exception("扫描失败")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.scanned.emit(report)


class ConvertWorker(QThread):
    """在后台执行转换队列。"""

    #: (行号, 阶段文本, 进度 0~1, 状态文本)
    progressed = pyqtSignal(int, str, float, str)
    #: 需要用户裁决同名冲突，携带目标路径文本。
    conflict = pyqtSignal(str)
    #: 全部完成，携带 list[ConvertResult]。
    all_finished = pyqtSignal(object)
    #: 线程级异常。
    failed = pyqtSignal(str)

    def __init__(
        self,
        options: Options,
        items: Sequence[VideoItem],
        rows: dict[int, int],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.options = options
        self.items = list(items)
        self.rows = dict(rows)          # id(item) -> 表格行号
        self.converter: Converter | None = None
        self._ask_event = threading.Event()
        self._ask_result = False

    # ------------------------------------------------------------------
    # 供 GUI 线程调用
    # ------------------------------------------------------------------

    def request_stop(self) -> None:
        """请求取消（不会中断正在合并的那个任务）。"""
        if self.converter is not None:
            self.converter.stop()
        # 释放可能正阻塞在询问对话框上的工作线程
        self._ask_result = False
        self._ask_event.set()

    def answer_conflict(self, overwrite: bool) -> None:
        """GUI 线程回复冲突询问。"""
        self._ask_result = bool(overwrite)
        self._ask_event.set()

    # ------------------------------------------------------------------
    # 工作线程内部
    # ------------------------------------------------------------------

    def _on_progress(self, item: VideoItem, stage: str, percent: float) -> None:
        row = self.rows.get(id(item))
        if row is None:
            return
        self.progressed.emit(row, stage, float(percent), item.state.label)

    def _on_ask(self, path: Path) -> bool:
        self._ask_event.clear()
        self._ask_result = False
        self.conflict.emit(str(path))
        # 兜底超时，避免界面异常时工作线程永久卡死
        if not self._ask_event.wait(timeout=1800):
            LOG.warning("等待冲突裁决超时，按跳过处理: %s", path)
            return False
        return self._ask_result

    def run(self) -> None:  # noqa: D102 - QThread 入口
        try:
            self.converter = Converter(
                self.options,
                progress_cb=self._on_progress,
                ask_cb=self._on_ask,
            )
            results: list[ConvertResult] = self.converter.run(self.items)
        except Exception as exc:  # noqa: BLE001 - 兜底，界面必须能恢复
            LOG.exception("转换线程异常")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.all_finished.emit(results)


__all__ = ["ScanWorker", "ConvertWorker"]
