"""Message boxes with vector-drawn, per-monitor high DPI status icons."""
from __future__ import annotations

import math

from PyQt5.QtCore import QEvent, QRectF, Qt
from PyQt5.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import QMessageBox as NativeMessageBox


def status_icon(kind: int, dpr: float, size: int = 40) -> QPixmap:
    """Render geometry at 3x, then downsample to the screen's physical pixels."""
    dpr = max(1.0, float(dpr))
    pixels = math.ceil(size * dpr)
    source = QImage(pixels * 3, pixels * 3, QImage.Format_ARGB32_Premultiplied)
    source.fill(Qt.transparent)
    painter = QPainter(source)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(source.width() / 48, source.height() / 48)
    painter.setPen(Qt.NoPen)

    if kind == NativeMessageBox.Warning:
        painter.setBrush(QColor("#fff1cc"))
        painter.setPen(QPen(QColor("#df9c25"), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        triangle = QPainterPath()
        triangle.moveTo(24, 5)
        triangle.lineTo(45, 41)
        triangle.lineTo(3, 41)
        triangle.closeSubpath()
        painter.drawPath(triangle)
        painter.setPen(QPen(QColor("#b57509"), 3.2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(24, 18, 24, 29)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#b57509"))
        painter.drawEllipse(QRectF(22.3, 33, 3.4, 3.4))
    else:
        painter.setBrush(QColor("#e45567" if kind == NativeMessageBox.Critical else "#3478df"))
        painter.drawEllipse(QRectF(2, 2, 44, 44))
        painter.setBrush(QColor("white"))
        if kind == NativeMessageBox.Information:
            painter.drawEllipse(QRectF(22, 11, 4, 4))
            painter.drawRoundedRect(QRectF(22, 20, 4, 16), 2, 2)
        elif kind == NativeMessageBox.Critical:
            painter.setPen(QPen(QColor("white"), 3.2, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(17, 17, 31, 31)
            painter.drawLine(31, 17, 17, 31)
        else:
            painter.setPen(QPen(QColor("white"), 3.1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            question = QPainterPath()
            question.moveTo(17, 17)
            question.cubicTo(17, 9, 32, 9, 32, 18)
            question.cubicTo(32, 23, 24, 23, 24, 29)
            painter.drawPath(question)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("white"))
            painter.drawEllipse(QRectF(22.2, 34, 3.6, 3.6))
    painter.end()
    image = source.scaled(pixels, pixels, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


class MessageBox(NativeMessageBox):
    """Keep QMessageBox button/keyboard semantics; replace its raster icons."""
    def __init__(self, kind, title, text, buttons=NativeMessageBox.Ok,
                 default_button=NativeMessageBox.NoButton, parent=None):
        super().__init__(parent)
        self._status_kind = kind
        self.setWindowTitle(title)
        self.setTextFormat(Qt.PlainText)
        self.setText(text)
        self.setStandardButtons(buttons)
        if default_button != self.NoButton:
            self.setDefaultButton(default_button)
        self._refresh_icon()

    def _refresh_icon(self):
        self.setIconPixmap(status_icon(self._status_kind, self.devicePixelRatioF()))

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_icon()

    def event(self, event):
        result = super().event(event)
        if event.type() == getattr(QEvent, "ScreenChangeInternal", 216) and hasattr(self, "_status_kind"):
            self._refresh_icon()
        return result

    @classmethod
    def _show(cls, kind, parent, title, text, buttons, default_button):
        return cls(kind, title, text, buttons, default_button, parent).exec_()

    @classmethod
    def information(cls, parent, title, text, buttons=NativeMessageBox.Ok,
                    defaultButton=NativeMessageBox.NoButton):
        return cls._show(cls.Information, parent, title, text, buttons, defaultButton)

    @classmethod
    def warning(cls, parent, title, text, buttons=NativeMessageBox.Ok,
                defaultButton=NativeMessageBox.NoButton):
        return cls._show(cls.Warning, parent, title, text, buttons, defaultButton)

    @classmethod
    def critical(cls, parent, title, text, buttons=NativeMessageBox.Ok,
                 defaultButton=NativeMessageBox.NoButton):
        return cls._show(cls.Critical, parent, title, text, buttons, defaultButton)

    @classmethod
    def question(cls, parent, title, text, buttons=NativeMessageBox.Yes | NativeMessageBox.No,
                 defaultButton=NativeMessageBox.NoButton):
        return cls._show(cls.Question, parent, title, text, buttons, defaultButton)
