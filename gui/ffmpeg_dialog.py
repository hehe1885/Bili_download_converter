"""Nonblocking first-run dependency setup."""
from __future__ import annotations

import threading
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFrame,
                            QHBoxLayout, QLabel, QProgressBar, QPushButton,
                            QVBoxLayout)

from m4sconverter.ffmpeg_setup import (InstallCancelled, install_ffmpeg,
                                     validate_ffmpeg)
from m4sconverter.models import Options
from m4sconverter.mux import find_tools
from .widgets import RoundedDialog


class CheckWorker(QThread):
    checked = pyqtSignal(str)

    def __init__(self, options: Options, parent=None, candidate: Path | None = None):
        super().__init__(parent)
        self.options = options
        self.candidate = candidate

    def run(self):
        path = self.candidate or find_tools(self.options).ffmpeg
        self.checked.emit(str(path) if path and validate_ffmpeg(path) else "")


class InstallWorker(QThread):
    progress = pyqtSignal(int, str)
    installed = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, system: bool, parent=None):
        super().__init__(parent)
        self.system = system
        self.cancel = threading.Event()

    def run(self):
        try:
            path = install_ffmpeg(self.system, self.cancel, self.progress.emit)
            self.installed.emit(str(path))
        except InstallCancelled as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"安装未完成：{exc}\n请检查网络后重试，也可以选择已安装的 FFmpeg。")


class FFmpegDialog(RoundedDialog):
    configured = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("首次使用 · 安装 FFmpeg")
        self.setWindowModality(Qt.ApplicationModal)
        self.setMinimumWidth(620)
        self.worker: InstallWorker | None = None
        self.check_worker: CheckWorker | None = None
        self._installed_path = ""
        self._busy = False
        self._committing = False
        root = self.body_layout
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)

        eyebrow = QLabel("开始之前  /  环境准备")
        eyebrow.setObjectName("eyebrow")
        root.addWidget(eyebrow)
        self.title = QLabel("还差一步，就能开始转换")
        self.title.setObjectName("dialogTitle")
        root.addWidget(self.title)
        self.description = QLabel("未检测到可用的 FFmpeg。它负责将 B 站缓存中的\n视频与音频无损合并为 MP4，安装一次即可。")
        self.description.setObjectName("subtitle")
        self.description.setWordWrap(True)
        root.addWidget(self.description)

        card = QFrame()
        card.setObjectName("installCard")
        steps = QVBoxLayout(card)
        steps.setContentsMargins(20, 18, 20, 18)
        steps.setSpacing(18)
        for number, title, detail in (
            ("1", "下载并校验 FFmpeg", "获取 Windows 64 位版本，并检查 SHA-256。"),
            ("2", "安装并加入 PATH", "自动配置环境变量，其他软件也能调用。"),
            ("3", "验证完成，立即使用", "本软件即时生效；已打开的终端需重新打开。"),
        ):
            row = QHBoxLayout()
            row.setSpacing(14)
            badge = QLabel(number)
            badge.setObjectName("stepNumber")
            badge.setAlignment(Qt.AlignCenter)
            row.addWidget(badge, 0, Qt.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(4)
            heading = QLabel(title)
            heading.setObjectName("stepTitle")
            sub = QLabel(detail)
            sub.setObjectName("hint")
            sub.setWordWrap(True)
            text.addWidget(heading)
            text.addWidget(sub)
            row.addLayout(text, 1)
            steps.addLayout(row)
        root.addWidget(card)

        self.system_check = QCheckBox("安装到所有用户，并加入系统 PATH（需要管理员权限）")
        self.system_check.setChecked(True)
        root.addWidget(self.system_check)
        self.scope_hint = QLabel()
        self.scope_hint.setObjectName("hint")
        self.scope_hint.setWordWrap(True)
        root.addWidget(self.scope_hint)
        self.system_check.toggled.connect(self._update_scope)
        self._update_scope()

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)
        self.status = QLabel("下载来源：gyan.dev · FFmpeg Essentials")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        root.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.later_btn = QPushButton("稍后再说")
        self.later_btn.clicked.connect(self.reject)
        self.select_btn = QPushButton("选择已有 FFmpeg…")
        self.select_btn.clicked.connect(self._select_existing)
        self.install_btn = QPushButton("一键安装")
        self.install_btn.setProperty("primary", True)
        self.install_btn.setMinimumWidth(150)
        self.install_btn.setDefault(True)
        self.install_btn.clicked.connect(self._install_or_finish)
        buttons.addWidget(self.later_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.select_btn)
        buttons.addWidget(self.install_btn)
        root.addLayout(buttons)

    def _update_scope(self):
        self.scope_hint.setText(
            "Windows 将弹出管理员授权；安装到 Program Files，并写入系统 PATH。"
            if self.system_check.isChecked() else
            "仅安装到当前用户目录，并写入用户 PATH，无需管理员权限。")

    def _set_busy(self, busy: bool):
        self._busy = busy
        for control in (self.system_check, self.select_btn, self.install_btn):
            control.setEnabled(not busy)
        self.later_btn.setText("取消安装" if busy else "稍后再说")
        self.later_btn.setEnabled(not self._committing)

    def _install_or_finish(self):
        if self._installed_path:
            self.accept()
            return
        if self._busy:
            return
        self._committing = False
        self._set_busy(True)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.status.setText("正在连接下载源…")
        self.worker = InstallWorker(self.system_check.isChecked(), self)
        self.worker.progress.connect(self._on_progress)
        self.worker.installed.connect(self._on_installed)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _worker_finished(self):
        # A result signal may arrive just before QThread finishes; keep ownership
        # and block a second installation until the thread has really stopped.
        self._set_busy(False)
        if self._installed_path:
            self.later_btn.hide()
            self.select_btn.hide()

    def _on_progress(self, percent: int, message: str):
        self.progress_bar.setValue(percent)
        self.status.setText(message)
        self._committing = percent >= 82
        self.later_btn.setEnabled(not self._committing)

    def _on_installed(self, path: str):
        self._installed_path = path
        self.title.setText("准备就绪，可以开始转换了")
        self.description.setText("FFmpeg 已安装并通过运行验证。")
        scope = "系统 PATH" if self.system_check.isChecked() else "用户 PATH"
        self.status.setText(f"已加入{scope}，本软件立即生效。\n已打开的命令行或其他软件需重新打开。\n安装位置：{path}")
        self.install_btn.setText("开始使用")
        self.system_check.setVisible(False)
        self.scope_hint.setVisible(False)
        self.configured.emit(path)

    def _on_failed(self, message: str):
        self._committing = False
        self.status.setText(message)
        self.install_btn.setText("重试安装")

    def _select_existing(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 ffmpeg.exe", "",
                                             "FFmpeg (ffmpeg.exe);;所有文件 (*)")
        if not path:
            return
        self._set_busy(True)
        self.status.setText("正在验证所选 FFmpeg…")
        self.check_worker = CheckWorker(Options(), self, Path(path))
        self.check_worker.checked.connect(self._existing_checked)
        self.check_worker.finished.connect(self._worker_finished)
        self.check_worker.start()

    def _existing_checked(self, path: str):
        if not path:
            self.status.setText("所选程序无法运行，请选择有效的 ffmpeg.exe。")
            return
        self._installed_path = path
        self.title.setText("FFmpeg 已就绪")
        self.description.setText("已验证并使用你选择的 FFmpeg。")
        self.status.setText(f"程序路径：{path}")
        self.install_btn.setText("开始使用")
        self.configured.emit(path)

    def reject(self):
        if self._busy:
            if self.worker and not self._committing:
                self.worker.cancel.set()
                self.status.setText("正在取消，请等待当前网络请求结束…")
                self.later_btn.setEnabled(False)
            return
        super().reject()

    def closeEvent(self, event):
        if self._busy:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
