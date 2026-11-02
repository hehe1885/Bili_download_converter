"""Explain cache locations without scanning personal folders."""
from __future__ import annotations

import os
from pathlib import Path

from PyQt5.QtCore import Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                            QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout,
                            QWidget)
from .widgets import RoundedDialog, apply_browser_scrollbars


def windows_cache_location() -> Path:
    appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    return appdata / "BilibiliDownload" / "cache" / "data"


class CacheHelpDialog(RoundedDialog):
    directory_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("帮助 · B 站缓存目录在哪？")
        self.resize(720, 660)
        root = self.body_layout
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(10)
        title = QLabel("找到你的 B 站缓存目录")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        intro = QLabel("先在 B 站客户端完成视频缓存，再选择包含数字文件夹的那一层。")
        intro.setObjectName("subtitle")
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.tabs = tabs = QTabWidget()
        self.location = windows_cache_location()
        for platform, text, path in (
            ("Windows", "常见位置（已替换为当前用户的实际路径）", str(self.location)),
            ("安卓手机", "按客户端版本找到缓存目录，将目录复制到电脑后，再在本软件中选择。", ""),
            ("macOS", "在 Finder 中选择“前往 → 前往文件夹”，输入以下位置。",
             "~/Library/Application Support/Bilibili/com.bilibili.downloads/web_cache/data"),
            ("iOS", "在 B 站 App 的“我的下载”中查看。\n本软件需要可访问的 m4s 缓存文件，\n请选择已经导出到电脑的目录。", ""),
        ):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(10)
            explanation = QLabel(text)
            explanation.setWordWrap(True)
            layout.addWidget(explanation)
            if path:
                field = QLineEdit(path)
                field.setReadOnly(True)
                field.setToolTip(path)
                field.setCursorPosition(0)
                layout.addWidget(field)
            if platform == "安卓手机":
                for edition, directory in (
                    ("国内版", "Android/data/tv.danmaku.bili/download"),
                    ("国际版", "Android/data/com.bilibili.app.in/download"),
                ):
                    label = QLabel(edition)
                    label.setObjectName("stepTitle")
                    layout.addWidget(label)
                    field = QLineEdit(directory)
                    field.setReadOnly(True)
                    field.setToolTip(directory)
                    field.setCursorPosition(0)
                    layout.addWidget(field)
                layout.addStretch(1)
            if platform == "Windows":
                self.detected_label = QLabel("已找到此目录，可以直接使用。" if self.location.is_dir()
                                             else "此常见位置尚不存在，客户端可能使用了其他下载位置。")
                self.detected_label.setObjectName("hint")
                self.detected_label.setWordWrap(True)
                layout.addWidget(self.detected_label)
                buttons = QHBoxLayout()
                self.open_btn = QPushButton("打开此文件夹")
                self.open_btn.setEnabled(self.location.is_dir())
                self.open_btn.clicked.connect(self._open_location)
                self.use_btn = QPushButton("使用这个目录")
                self.use_btn.setProperty("primary", True)
                self.use_btn.setEnabled(self.location.is_dir())
                self.use_btn.clicked.connect(self._use_location)
                buttons.addWidget(self.open_btn)
                buttons.addWidget(self.use_btn)
                buttons.addStretch()
                layout.addLayout(buttons)
            tabs.addTab(page, platform)
        root.addWidget(tabs)
        step = QLabel("应该选哪一层？")
        step.setObjectName("stepTitle")
        root.addWidget(step)
        tree = QPlainTextEdit()
        tree.setReadOnly(True)
        tree.setMinimumHeight(180)
        tree.setPlainText("download / data / 你设置的缓存目录   ← 选择这一层\n"
                          "  ├─ 116278203322248/    视频编号\n"
                          "  │   └─ c_40090993383/\n"
                          "  │       ├─ entry.json\n"
                          "  │       └─ 80/\n"
                          "  │           ├─ video.m4s\n"
                          "  │           └─ audio.m4s\n"
                          "  └─ 其他视频编号/ …")
        root.addWidget(tree, 1)
        tip = QLabel("找不到？在 B 站客户端“设置”中查看下载／缓存位置。\n"
                     "AppData 是隐藏文件夹，也可以直接复制上方路径到资源管理器地址栏。\n"
                     "请选包含视频编号文件夹的目录，程序会自动扫描其中的 m4s 文件。")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        root.addWidget(tip)
        footer = QHBoxLayout()
        footer.addStretch()
        close = QPushButton("我知道了")
        close.setMinimumWidth(120)
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        root.addLayout(footer)
        apply_browser_scrollbars(self)

    def _open_location(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.location)))

    def _use_location(self):
        if self.location.is_dir():
            self.directory_selected.emit(str(self.location))
            self.accept()
