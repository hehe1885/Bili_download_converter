"""主窗口。

界面结构::

    ┌───────────────────────────────────────────────────────────┐
    │ 导入目录 [____] [浏览]   输出目录 [____] [浏览]   [扫描]   │
    ├───────────────┬───────────────────────────────────────────┤
    │               │  任务列表（勾选 / 状态 / 进度）           │
    │  选项面板     ├───────────────────────────────────────────┤
    │  （可滚动）   │  日志                                     │
    ├───────────────┴───────────────────────────────────────────┤
    │ 状态栏 + 总进度条                                          │
    └───────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from m4sconverter import __version__
from m4sconverter.config import default_config_path, load_options, save_options
from m4sconverter.converter import summarize_results
from m4sconverter.models import ItemState, Options, VideoItem
from m4sconverter.mux import check_tools
from m4sconverter.naming import output_root_for, preview_path
from m4sconverter.scanner import describe
from m4sconverter.utils import CallbackHandler, human_duration

from .options_panel import OptionsPanel
from .worker import ConvertWorker, ScanWorker

LOG = logging.getLogger("m4s")

COL_CHECK = 0
COL_INDEX = 1
COL_TITLE = 2
COL_UP = 3
COL_QUALITY = 4
COL_DURATION = 5
COL_DANMAKU = 6
COL_STATE = 7
COL_PROGRESS = 8
COL_MESSAGE = 9

HEADERS = ["选择", "#", "标题", "UP主", "画质", "时长", "弹幕", "状态", "进度", "说明"]

STATE_COLORS = {
    ItemState.PENDING: "#444",
    ItemState.CONVERTING: "#0a58ca",
    ItemState.DONE: "#1a7f37",
    ItemState.SKIPPED: "#8a6d00",
    ItemState.FAILED: "#b02a37",
}


class MainWindow(QMainWindow):
    """B站缓存转换器主窗口。"""

    log_line = pyqtSignal(str)

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self.config_path = Path(config_path) if config_path else default_config_path()
        self.items: list[VideoItem] = []
        self.rows: dict[int, int] = {}
        self._finished_rows: set[int] = set()
        self.scan_worker: ScanWorker | None = None
        self.convert_worker: ConvertWorker | None = None

        self.setWindowTitle(f"B站缓存转换器 v{__version__}")
        self.resize(1280, 820)

        self._install_log_handler()
        self._build_ui()
        self._load_startup_config()
        self._install_tool_status()

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------

    def _install_log_handler(self) -> None:
        self.log_line.connect(self._append_log)
        logger = logging.getLogger("m4s")
        logger.setLevel(logging.INFO)
        handler = CallbackHandler(self.log_line.emit, logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)
        self._log_handler = handler

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 4)

        # 选项面板要先建：顶部路径栏的双向同步需要连它的控件。
        self.options_panel = OptionsPanel()
        self.options_panel.setMinimumWidth(400)

        root.addWidget(self._build_path_bar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.options_panel)

        right = QSplitter(Qt.Vertical)
        right.addWidget(self._build_task_area())
        right.addWidget(self._build_log_area())
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        # 左栏宽度要一次给够：QScrollArea 里塞得下才不会出现"控件只显示一半"。
        splitter.setSizes([470, 890])
        self._splitter = splitter
        self._splitter_fitted = False
        root.addWidget(splitter, 1)

        self._build_status_bar()

    def _build_path_bar(self) -> QWidget:
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)

        self.top_import_edit = QLineEdit()
        self.top_import_edit.setPlaceholderText("B 站缓存目录")
        self.top_import_edit.textChanged.connect(self._sync_import_to_panel)
        self.options_panel.import_edit.textChanged.connect(self._sync_import_to_top)

        imp_btn = QPushButton("浏览…")
        imp_btn.clicked.connect(self._pick_top_import)

        self.top_output_edit = QLineEdit()
        self.top_output_edit.setPlaceholderText("留空 = <导入目录>\\output")
        self.top_output_edit.textChanged.connect(self._sync_output_to_panel)
        self.options_panel.output_edit.textChanged.connect(self._sync_output_to_top)

        out_btn = QPushButton("浏览…")
        out_btn.clicked.connect(self._pick_top_output)

        self.scan_btn = QPushButton("扫描缓存")
        self.scan_btn.clicked.connect(self.on_scan)
        self.preview_btn = QPushButton("预览命名")
        self.preview_btn.clicked.connect(self.on_preview)
        self.start_btn = QPushButton("开始转换")
        self.start_btn.clicked.connect(self.on_start)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.clicked.connect(self.on_stop)
        self.stop_btn.setEnabled(False)

        lay.addWidget(QLabel("导入目录"))
        lay.addWidget(self.top_import_edit, 3)
        lay.addWidget(imp_btn)
        lay.addSpacing(8)
        lay.addWidget(QLabel("输出目录"))
        lay.addWidget(self.top_output_edit, 2)
        lay.addWidget(out_btn)
        lay.addSpacing(8)
        lay.addWidget(self.scan_btn)
        lay.addWidget(self.preview_btn)
        lay.addWidget(self.start_btn)
        lay.addWidget(self.stop_btn)
        return bar

    def _build_task_area(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)

        tools = QHBoxLayout()
        self.task_label = QLabel("尚未扫描")
        tools.addWidget(self.task_label)
        tools.addStretch(1)

        for text, slot in (
            ("全选", lambda: self._set_all_checked(True)),
            ("全不选", lambda: self._set_all_checked(False)),
            ("反选", self._invert_checked),
            ("删除未勾选", self._remove_unchecked),
        ):
            btn = QPushButton(text)
            btn.setFixedWidth(84)
            btn.clicked.connect(slot)
            tools.addWidget(btn)
        lay.addLayout(tools)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._on_item_changed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_MESSAGE, QHeaderView.Stretch)
        for col, width in (
            (COL_CHECK, 48), (COL_INDEX, 48), (COL_UP, 130), (COL_QUALITY, 66),
            (COL_DURATION, 76), (COL_DANMAKU, 66), (COL_STATE, 76), (COL_PROGRESS, 66),
        ):
            self.table.setColumnWidth(col, width)
        lay.addWidget(self.table, 1)
        return box

    def _build_log_area(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)

        tools = QHBoxLayout()
        tools.addWidget(QLabel("日志"))
        tools.addStretch(1)
        clear_btn = QPushButton("清空")
        clear_btn.setFixedWidth(84)
        clear_btn.clicked.connect(lambda: self.log_view.clear())
        tools.addWidget(clear_btn)

        self.save_cfg_btn = QPushButton("保存配置")
        self.save_cfg_btn.setFixedWidth(104)
        self.save_cfg_btn.clicked.connect(self.on_save_config)
        tools.addWidget(self.save_cfg_btn)

        load_cfg_btn = QPushButton("导入配置…")
        load_cfg_btn.setFixedWidth(104)
        load_cfg_btn.clicked.connect(self.on_load_config)
        tools.addWidget(load_cfg_btn)
        lay.addLayout(tools)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        lay.addWidget(self.log_view, 1)
        return box

    def _build_status_bar(self) -> None:
        self.status_label = QLabel("就绪")
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(260)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress_bar)

    # ------------------------------------------------------------------
    # 路径两处同步
    # ------------------------------------------------------------------

    def _sync_import_to_panel(self, text: str) -> None:
        if self.options_panel.import_edit.text() != text:
            self.options_panel.import_edit.setText(text)

    def _sync_import_to_top(self, text: str) -> None:
        if self.top_import_edit.text() != text:
            self.top_import_edit.setText(text)

    def _sync_output_to_panel(self, text: str) -> None:
        if self.options_panel.output_edit.text() != text:
            self.options_panel.output_edit.setText(text)

    def _sync_output_to_top(self, text: str) -> None:
        if self.top_output_edit.text() != text:
            self.top_output_edit.setText(text)

    def _pick_top_import(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择缓存目录",
                                                 self.top_import_edit.text().strip())
        if chosen:
            self.top_import_edit.setText(chosen)

    def _pick_top_output(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择输出目录",
                                                 self.top_output_edit.text().strip())
        if chosen:
            self.top_output_edit.setText(chosen)

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------

    def current_options(self) -> Options:
        return self.options_panel.collect()

    def _load_startup_config(self) -> None:
        path = self.config_path
        if not path.is_file():
            self._append_log(f"未找到配置文件，使用默认选项（可点“保存配置”写入 {path}）")
            return
        try:
            opt = load_options(path)
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"[!] 读取配置失败：{exc}")
            return
        self.options_panel.load_from(opt)
        if opt.import_dir:
            self.top_import_edit.setText(opt.import_dir)
        if opt.output_dir:
            self.top_output_edit.setText(opt.output_dir)
        self._append_log(f"已载入配置：{path}")

    def on_save_config(self) -> None:
        try:
            saved = save_options(self.current_options(), self.config_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._append_log(f"配置已保存：{saved}")
        self.status_label.setText(f"配置已保存到 {saved}")

    def on_load_config(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "导入配置",
                                               str(self.config_path.parent),
                                               "JSON 配置 (*.json);;所有文件 (*)")
        if not chosen:
            return
        try:
            opt = load_options(chosen)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "读取失败", str(exc))
            return
        self.options_panel.load_from(opt)
        self.top_import_edit.setText(opt.import_dir)
        self.top_output_edit.setText(opt.output_dir)
        self._append_log(f"已导入配置：{chosen}")

    def _install_tool_status(self) -> None:
        ok, text = check_tools(self.current_options())
        self._append_log(text)
        if not ok:
            self.status_label.setText("未找到可用的合并引擎，请检查 ffmpeg / MP4Box")

    # ------------------------------------------------------------------
    # 扫描
    # ------------------------------------------------------------------

    def on_scan(self) -> None:
        if self.scan_worker is not None and self.scan_worker.isRunning():
            return
        opt = self.current_options()
        if not opt.import_dir:
            QMessageBox.information(self, "请先选择导入目录", "还没有指定 B 站缓存目录。")
            return
        root = Path(opt.import_dir).expanduser()
        if not root.is_dir():
            QMessageBox.warning(self, "目录不存在", str(root))
            return

        self.table.setRowCount(0)
        self.items.clear()
        self.rows.clear()
        self._finished_rows.clear()
        self.progress_bar.setValue(0)
        self._set_busy(True, scanning=True)
        self.status_label.setText(f"正在扫描 {root} …")
        self._append_log(f"开始扫描：{root}")

        self.scan_worker = ScanWorker(root, opt, parent=self)
        self.scan_worker.scanned.connect(self._on_scanned)
        self.scan_worker.failed.connect(self._on_scan_failed)
        # 注意：必须连到 QObject 的绑定方法，不能连 lambda —— lambda 没有线程归属，
        # 会被当成直接调用，从而在工作线程里操作控件。
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.scan_worker.start()

    def _on_scan_finished(self) -> None:
        self._set_busy(False)

    def _on_scan_failed(self, message: str) -> None:
        self._append_log(f"[X] 扫描失败：{message}")
        self.status_label.setText("扫描失败")
        QMessageBox.critical(self, "扫描失败", message)

    def _on_scanned(self, report) -> None:
        self.items = list(report.items)
        self.rows = {id(it): idx for idx, it in enumerate(self.items)}
        self._fill_table()
        for warn in report.warnings[:50]:
            self._append_log(f"[!] {warn}")
        if len(report.warnings) > 50:
            self._append_log(f"[!] 其余 {len(report.warnings) - 50} 条警告已省略")
        self._append_log(describe(report, limit=10))
        self.task_label.setText(f"共 {len(self.items)} 个任务（勾选后点“开始转换”）")
        self.status_label.setText(f"扫描完成：{len(self.items)} 个任务，"
                                  f"遍历 {report.scanned_dirs} 个目录，"
                                  f"耗时 {report.elapsed:.2f}s")

    def _fill_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked if item.selected else Qt.Unchecked)
            self.table.setItem(row, COL_CHECK, check)

            self.table.setItem(row, COL_INDEX, QTableWidgetItem(str(row + 1)))
            self.table.setItem(row, COL_TITLE, QTableWidgetItem(item.primary_title()))
            self.table.setItem(row, COL_UP, QTableWidgetItem(item.up))
            self.table.setItem(row, COL_QUALITY, QTableWidgetItem(item.quality or item.qid))
            self.table.setItem(row, COL_DURATION,
                               QTableWidgetItem(human_duration(item.duration_s) if item.duration_ms else ""))
            self.table.setItem(row, COL_DANMAKU,
                               QTableWidgetItem(str(item.danmaku_count) if item.danmaku_count else ""))
            self._set_state_cell(row, item.state)
            self.table.setItem(row, COL_PROGRESS, QTableWidgetItem(""))
            self.table.setItem(row, COL_MESSAGE, QTableWidgetItem(item.message))
        self.table.blockSignals(False)

    def _set_state_cell(self, row: int, state: ItemState) -> None:
        cell = self.table.item(row, COL_STATE)
        if cell is None:
            cell = QTableWidgetItem()
            self.table.setItem(row, COL_STATE, cell)
        cell.setText(state.label)
        cell.setForeground(QColor(STATE_COLORS.get(state, "#444")))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != COL_CHECK:
            return
        row = item.row()
        if 0 <= row < len(self.items):
            self.items[row].selected = item.checkState() == Qt.Checked

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        self.table.blockSignals(True)
        for row, item in enumerate(self.items):
            cell = self.table.item(row, COL_CHECK)
            if cell is not None:
                cell.setCheckState(state)
            item.selected = checked
        self.table.blockSignals(False)

    def _invert_checked(self) -> None:
        self.table.blockSignals(True)
        for row, item in enumerate(self.items):
            cell = self.table.item(row, COL_CHECK)
            if cell is None:
                continue
            new = Qt.Unchecked if cell.checkState() == Qt.Checked else Qt.Checked
            cell.setCheckState(new)
            item.selected = new == Qt.Checked
        self.table.blockSignals(False)

    def _remove_unchecked(self) -> None:
        keep = [(it, self.table.item(row, COL_CHECK)) for row, it in enumerate(self.items)
                if self.table.item(row, COL_CHECK) is not None
                and self.table.item(row, COL_CHECK).checkState() == Qt.Checked]
        self.items = [it for it, _ in keep]
        self.rows = {id(it): idx for idx, it in enumerate(self.items)}
        self._finished_rows.clear()
        self._fill_table()
        self.task_label.setText(f"共 {len(self.items)} 个任务")

    # ------------------------------------------------------------------
    # 预览
    # ------------------------------------------------------------------

    def on_preview(self) -> None:
        targets = [it for it in self.items if it.selected] or self.items
        if not targets:
            QMessageBox.information(self, "没有任务", "请先扫描缓存目录。")
            return
        opt = self.current_options()
        root = Path(opt.import_dir).expanduser() if opt.import_dir else Path.cwd()
        output_root = output_root_for(root, opt)
        lines = [f"输出根目录：{output_root}", ""]
        for item in targets[:80]:
            lines.append(f"{item.index or '-':>3}. {preview_path(item, opt, output_root)}")
        if len(targets) > 80:
            lines.append(f"…… 其余 {len(targets) - 80} 个已省略")
        self._show_text_dialog("命名预览", "\n".join(lines))

    def _show_text_dialog(self, title: str, text: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(900, 560)
        lay = QVBoxLayout(dlg)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        lay.addWidget(view)
        btn = QPushButton("关闭")
        btn.clicked.connect(dlg.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn)
        lay.addLayout(row)
        dlg.exec_()

    # ------------------------------------------------------------------
    # 转换
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        if self.convert_worker is not None and self.convert_worker.isRunning():
            return
        if not self.items:
            QMessageBox.information(self, "没有任务", "请先扫描缓存目录。")
            return
        targets = [it for it in self.items if it.selected]
        if not targets:
            QMessageBox.information(self, "没有勾选", "至少勾选一个任务。")
            return

        opt = self.current_options()
        ok, text = check_tools(opt)
        if not ok:
            QMessageBox.critical(self, "缺少合并引擎", text)
            return

        for item in targets:
            item.state = ItemState.PENDING
            item.message = ""
            item.progress = 0.0
        self._finished_rows.clear()
        self.progress_bar.setValue(0)
        self._set_busy(True, converting=True)
        self.status_label.setText(f"正在转换 {len(targets)} 个任务 …")
        self._append_log("=" * 60)
        self._append_log(f"开始转换：{len(targets)} 个任务，引擎={opt.engine.value}，并发={opt.workers}")

        self.convert_worker = ConvertWorker(opt, targets, self.rows, parent=self)
        self.convert_worker.progressed.connect(self._on_progress)
        self.convert_worker.conflict.connect(self._on_conflict)
        self.convert_worker.all_finished.connect(self._on_all_finished)
        self.convert_worker.failed.connect(self._on_convert_failed)
        self.convert_worker.start()

    def _on_progress(self, row: int, stage: str, percent: float, state_label: str) -> None:
        if not (0 <= row < len(self.items)):
            return
        item = self.items[row]
        cell = self.table.item(row, COL_PROGRESS)
        if cell is not None:
            cell.setText(f"{percent * 100:.0f}%")
        msg = self.table.item(row, COL_MESSAGE)
        if msg is not None:
            msg.setText(item.message or stage)
        state_cell = self.table.item(row, COL_STATE)
        if state_cell is not None:
            state_cell.setText(state_label)
            state_cell.setForeground(QColor(STATE_COLORS.get(item.state, "#444")))
        self.status_label.setText(f"{item.primary_title()} —— {stage}")

        if percent >= 1.0:
            self._finished_rows.add(row)
            total = max(1, len([it for it in self.items if it.selected]))
            self.progress_bar.setValue(int(100 * len(self._finished_rows) / total))

    def _on_conflict(self, path_text: str) -> None:
        answer = QMessageBox.question(
            self, "同名文件已存在",
            f"目标文件已存在：\n{path_text}\n\n是否覆盖？\n（选择“否”将自动改名保存）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        worker = self.convert_worker
        if worker is not None:
            worker.answer_conflict(answer == QMessageBox.Yes)

    def _on_convert_failed(self, message: str) -> None:
        self._append_log(f"[X] 转换线程异常：{message}")
        QMessageBox.critical(self, "转换异常", message)
        self._set_busy(False)

    def _on_all_finished(self, results) -> None:
        self._set_busy(False)
        self._append_log(summarize_results(results))
        done = sum(1 for r in results if r.ok and not r.skipped)
        skipped = sum(1 for r in results if r.skipped)
        failed = sum(1 for r in results if not r.ok and not r.skipped)
        self.status_label.setText(f"完成 {done} / 跳过 {skipped} / 失败 {failed}")
        self.progress_bar.setValue(100 if results else 0)

        opt = self.current_options()
        if opt.open_output_dir and done:
            self._open_output_dir(opt)
        if failed:
            QMessageBox.warning(self, "有任务失败",
                                f"失败 {failed} 个，详情见日志面板。")

    def on_stop(self) -> None:
        if self.convert_worker is not None and self.convert_worker.isRunning():
            self._append_log("正在取消，等待当前任务结束 …")
            self.convert_worker.request_stop()
            self.stop_btn.setEnabled(False)

    def _set_busy(self, busy: bool, scanning: bool = False, converting: bool = False) -> None:
        self.options_panel.setEnabled(not busy)
        self.scan_btn.setEnabled(not busy)
        self.preview_btn.setEnabled(not busy)
        self.start_btn.setEnabled(not busy)
        if converting:
            self.stop_btn.setEnabled(True)
        elif not busy:
            self.stop_btn.setEnabled(False)

    def _open_output_dir(self, opt: Options) -> None:
        root = Path(opt.import_dir).expanduser() if opt.import_dir else None
        if root is None:
            return
        target = output_root_for(root, opt)
        if not target.exists():
            return
        try:
            import os

            if os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system(f'open "{target}"')
            else:
                os.system(f'xdg-open "{target}"')
        except OSError as exc:  # pragma: no cover
            self._append_log(f"[!] 打开输出目录失败：{exc}")

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if not self._splitter_fitted:
            self._splitter_fitted = True
            self._fit_options_panel()

    def _fit_options_panel(self) -> None:
        """按选项面板的真实需要分配左栏宽度。

        不需要用户自己拖分隔条，也避免高 DPI（125% / 150%）下字体变大后
        输入框被挤出可视区——那时候再点一下"重置布局"就来不及了。
        """
        panel = self.options_panel
        need = panel.sizeHint().width()
        if need <= 0:
            return
        total = self._splitter.width()
        if total <= 0:
            return
        # 左栏给到它想要的宽度，但至少 470、且给右侧留够 420。
        left = min(max(470, need), max(400, total - 420))
        self._splitter.setSizes([left, max(1, total - left)])

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self.convert_worker is not None and self.convert_worker.isRunning():
            answer = QMessageBox.question(self, "仍在转换",
                                          "转换还没结束，确定退出吗？",
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.convert_worker.request_stop()
            self.convert_worker.wait(5000)
        try:
            save_options(self.current_options(), self.config_path)
        except Exception:  # noqa: BLE001 - 退出时保存失败不打扰用户
            LOG.debug("退出时保存配置失败", exc_info=True)
        super().closeEvent(event)


def resource_path(relative: str) -> Path:
    """取随程序分发的资源文件（图标等）。

    源码直接运行时资源在项目根目录；被 PyInstaller 打包后会被解到
    ``sys._MEIPASS`` 里，两种情形都要能找到。
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / relative
    return Path(__file__).resolve().parent.parent / relative


def run(argv: list[str] | None = None) -> int:
    """创建 QApplication 并显示主窗口。"""
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Bili_download_converter")
    app.setApplicationVersion(__version__)

    # 任务栏 / 标题栏 / Alt+Tab 用的图标；打包后由 --add-data 带进 _MEIPASS
    for name in ("assets/icon.ico", "assets/icon.png"):
        icon_file = resource_path(name)
        if icon_file.exists():
            app.setWindowIcon(QIcon(str(icon_file)))
            break

    app.setStyleSheet(
        "QGroupBox { font-weight: bold; margin-top: 10px; }"
        "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
        "QPushButton { padding: 5px 10px; }"
    )

    window = MainWindow()
    window.show()
    return app.exec_()


__all__ = ["MainWindow", "resource_path", "run"]
