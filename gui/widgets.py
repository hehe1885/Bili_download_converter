"""Rounded dialogs and scrollbars that fade like browser scrollbars."""
from PyQt5.QtCore import (QEasingCurve, QEvent, QPoint, QPointF, QRect, QRectF, QSize,
                          QParallelAnimationGroup, QPropertyAnimation,
                          Qt, QTimer, pyqtProperty)
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QRegion
from PyQt5.QtWidgets import (QAbstractScrollArea, QDialog, QFrame,
                            QApplication, QComboBox, QListView, QScrollBar,
                            QAbstractItemView, QStyle, QStyledItemDelegate, QTableWidget,
                            QStyleOptionSlider, QStyleOptionComboBox, QToolButton, QVBoxLayout, QWidget,
                            QGraphicsOpacityEffect)


def picker_easing():
    curve = QEasingCurve(QEasingCurve.BezierSpline)
    curve.addCubicBezierSegment(QPointF(.22, 1), QPointF(.36, 1), QPointF(1, 1))
    return curve


class DialogBackdrop(QWidget):
    """Dim only the owning window while its dialog is visible."""
    def __init__(self, owner):
        super().__init__(owner)
        self.setObjectName("dialogBackdrop")
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(22, 34, 54, 64))

    def mousePressEvent(self, event):
        event.accept()


class RoundedDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("roundedDialog")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._backdrop = None
        self._backdrop_owner = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 20)
        self.surface = QFrame(self)
        self.surface.setObjectName("dialogSurface")
        outer.addWidget(self.surface)
        self.body_layout = QVBoxLayout(self.surface)
        self.close_btn = QToolButton(self.surface)
        self.close_btn.setText("×")
        self.close_btn.setObjectName("dialogClose")
        self.close_btn.setToolTip("关闭")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.clicked.connect(self.reject)
        self.surface.installEventFilter(self)
        self._drag_offset = None
        self._entrance = QPropertyAnimation(self, b"pos", self)
        self._entrance.setDuration(180)
        self._entrance.setEasingCurve(QEasingCurve.OutCubic)

    def showEvent(self, event):
        self._show_backdrop()
        super().showEvent(event)
        end = self.pos()
        self._entrance.stop()
        self._entrance.setStartValue(end + QPoint(0, 12))
        self._entrance.setEndValue(end)
        self._entrance.start()

    def _show_backdrop(self):
        parent = self.parentWidget()
        if parent is None:
            return
        owner = parent.window()
        if self._backdrop is None:
            self._backdrop_owner = owner
            self._backdrop = DialogBackdrop(owner)
            owner.installEventFilter(self)
        self._backdrop.setGeometry(owner.rect())
        self._backdrop.show()
        self._backdrop.raise_()

    def hideEvent(self, event):
        self._entrance.stop()
        if self._backdrop is not None:
            backdrop = self._backdrop
            self._backdrop_owner.removeEventFilter(self)
            self._backdrop = None
            self._backdrop_owner = None
            backdrop.hide()
            backdrop.deleteLater()
        super().hideEvent(event)

    def paintEvent(self, event):
        # Paint a soft shadow directly, keeping translucent round edges and
        # avoiding nested graphics effects on the dialog's child widgets.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        rect = QRectF(self.surface.geometry()).translated(0, 4)
        for spread in range(14, 0, -1):
            alpha = max(1, int(5 * (1 - spread / 15)))
            painter.setBrush(QColor(22, 36, 60, alpha))
            painter.drawRoundedRect(rect.adjusted(-spread, -spread, spread, spread),
                                    22 + spread, 22 + spread)
        painter.end()

    def eventFilter(self, watched, event):
        if watched is self._backdrop_owner and self._backdrop is not None:
            if event.type() == QEvent.Resize:
                self._backdrop.setGeometry(watched.rect())
            elif event.type() == QEvent.Hide:
                self._backdrop.hide()
        if watched is self.surface and event.type() == QEvent.Resize:
            self.close_btn.move(self.surface.width() - 44, 12)
            self.close_btn.raise_()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.close_btn.move(self.surface.width() - 44, 12)
        self.close_btn.raise_()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.pos().y() < 95:
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class BrowserScrollBar(QScrollBar):
    def __init__(self, orientation, area):
        super().__init__(orientation, area)
        self._opacity = 0.0
        self.animation = QPropertyAnimation(self, b"thumbOpacity", self)
        self.animation.setDuration(220)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._fade)
        area.viewport().installEventFilter(self)
        area.viewport().setMouseTracking(True)
        self.valueChanged.connect(self.reveal)
        self.sliderPressed.connect(self.reveal)
        self.sliderReleased.connect(self.reveal)

    def _get_opacity(self):
        return self._opacity

    def _set_opacity(self, value):
        self._opacity = value
        self.update()

    thumbOpacity = pyqtProperty(float, _get_opacity, _set_opacity)

    def paintEvent(self, event):
        if self.maximum() <= self.minimum() or self._opacity <= 0:
            return
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        rect = self.style().subControlRect(QStyle.CC_ScrollBar, option,
                                          QStyle.SC_ScrollBarSlider, self)
        rect.adjust(1, 1, -1, -1)
        color = QColor("#8e9fb6" if self.underMouse() else "#b1bfd1")
        color.setAlphaF(self._opacity)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(rect, 3, 3)

    def reveal(self, *args):
        if self.maximum() <= self.minimum():
            return
        self.animation.stop()
        self._set_opacity(1)
        self.timer.start(1000)

    def _fade(self):
        if self.underMouse() or self.isSliderDown():
            self.timer.start(600)
            return
        self.animation.stop()
        self.animation.setStartValue(self._opacity)
        self.animation.setEndValue(0)
        self.animation.start()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Enter, QEvent.MouseMove, QEvent.Wheel):
            self.reveal()
        elif event.type() == QEvent.Leave:
            self.timer.start(500)
        return False

    def enterEvent(self, event):
        self.reveal()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.timer.start(500)
        super().leaveEvent(event)


def apply_browser_scrollbars(root):
    areas = root.findChildren(QAbstractScrollArea)
    if isinstance(root, QAbstractScrollArea):
        areas.append(root)
    for area in areas:
        if not area.property("browserScrollbars"):
            area.setProperty("browserScrollbars", True)
            area.setVerticalScrollBar(BrowserScrollBar(Qt.Vertical, area))
            area.setHorizontalScrollBar(BrowserScrollBar(Qt.Horizontal, area))


class RoundedTable(QTableWidget):
    """Clip the header too: a stylesheet radius alone cannot clip child views."""
    def resizeEvent(self, event):
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 16, 16)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))


class ChoiceDelegate(QStyledItemDelegate):
    """Inset round selection and a persistent checkmark, matching the reference."""
    def __init__(self, combo):
        super().__init__(combo)
        self.combo = combo

    def sizeHint(self, option, index):
        metrics = QFontMetrics(self.combo.font())
        return QSize(metrics.horizontalAdvance(str(index.data() or "")) + 52, 36)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(1, 1, -1, -1)
        checked = index.row() == self.combo.currentIndex()
        hovered = bool(option.state & (QStyle.State_MouseOver | QStyle.State_Selected))
        if checked or hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#ecf2ff" if checked else "#f5f8fc"))
            painter.drawRoundedRect(rect, 10, 10)
        if checked:
            middle = rect.center().y()
            mark = QPainterPath()
            mark.moveTo(rect.left() + 11, middle)
            mark.lineTo(rect.left() + 14, middle + 3)
            mark.lineTo(rect.left() + 20, middle - 5)
            painter.setPen(QPen(QColor("#3474ff"), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(mark)
        font = QFont(self.combo.font())
        painter.setFont(font)
        painter.setRenderHint(QPainter.TextAntialiasing)
        enabled = bool(index.flags() & Qt.ItemIsEnabled)
        painter.setPen(QColor("#3474ff" if checked else "#25334a") if enabled else QColor("#98a3b4"))
        text_rect = rect.adjusted(31, 0, -10, 0)
        text = painter.fontMetrics().elidedText(str(index.data() or ""), Qt.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        painter.restore()


class AnimatedComboBox(QComboBox):
    """Animate the native popup, retaining keyboard and editable-combo behavior."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._popup_group = None
        self._popup_target = None
        self._popup_closing = False
        self._popup_widget = None
        self._popup_owner = None
        self._popup_reveal = 1.0
        self._popup_opacity = 1.0
        self._popup_upward = False
        self._dismissing_popup = False
        self._native_scrollers = []
        self._motion_snapshot = None
        self._motion_effect = None
        self._arrow_angle = 0.0
        self._arrow_animation = QPropertyAnimation(self, b"arrowAngle", self)
        self._arrow_animation.setDuration(160)
        arrow_easing = QEasingCurve(QEasingCurve.BezierSpline)
        arrow_easing.addCubicBezierSegment(QPointF(.25, .1), QPointF(.25, 1), QPointF(1, 1))
        self._arrow_animation.setEasingCurve(arrow_easing)
        self._opacity_easing = arrow_easing
        view = QListView(self)
        view.setFrameShape(QFrame.NoFrame)
        view.setMouseTracking(True)
        view.setSpacing(2)
        view.setUniformItemSizes(True)
        view.setAutoScroll(False)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        view.setStyleSheet("QListView { background: white; border: none; outline: none; padding: 0; }")
        self.setView(view)
        self.setItemDelegate(ChoiceDelegate(self))
        self.setMaxVisibleItems(7)
        self._prepare_popup(self.view().window())
        QApplication.instance().applicationStateChanged.connect(self._application_state_changed)

    def _get_arrow_angle(self):
        return self._arrow_angle

    def _set_arrow_angle(self, value):
        self._arrow_angle = value
        self.update()

    arrowAngle = pyqtProperty(float, _get_arrow_angle, _set_arrow_angle)

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        rect = self.style().subControlRect(QStyle.CC_ComboBox, option,
                                           QStyle.SC_ComboBoxArrow, self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(rect.center().x() + .5, rect.center().y() + .5)
        painter.rotate(self._arrow_angle)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#7086a5" if self.isEnabled() else "#b8c4d5"))
        arrow = QPainterPath()
        arrow.moveTo(-4, -2.5)
        arrow.lineTo(4, -2.5)
        arrow.lineTo(0, 2.5)
        arrow.closeSubpath()
        painter.drawPath(arrow)
        painter.end()

    def _prepare_popup(self, popup):
        if popup is self.window() or popup is self._popup_widget:
            return
        # Configure translucency before the native window is first shown.
        # A rounded QRegion is only a one-bit stencil and produces stair steps.
        self._popup_widget = popup
        popup.setObjectName("choicePopup")
        popup.setWindowFlags(popup.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)
        popup.setAttribute(Qt.WA_OpaquePaintEvent, False)
        popup.setAutoFillBackground(False)
        popup.setFrameShape(QFrame.NoFrame)
        popup.setStyleSheet("QFrame#choicePopup { background: transparent; border: none; }")
        popup.clearMask()
        popup.installEventFilter(self)
        if popup.layout():
            popup.layout().setContentsMargins(6, 6, 6, 6)
        # Qt's private menu-style arrow strips are separate from the regular
        # scrollbar. Keep the scrollbar and suppress both arrow strips.
        self._native_scrollers = [child for child in popup.findChildren(QWidget)
                                  if child.metaObject().className() == "QComboBoxPrivateScroller"]
        for scroller in self._native_scrollers:
            scroller.setFixedHeight(0)
            scroller.hide()
            scroller.installEventFilter(self)

    def _application_state_changed(self, state):
        if state != Qt.ApplicationActive:
            self._dismiss_popup()

    def _get_popup_reveal(self):
        return self._popup_reveal

    def _set_popup_reveal(self, value):
        self._popup_reveal = value
        if self._popup_widget is not None:
            self._popup_widget.update()

    popupReveal = pyqtProperty(float, _get_popup_reveal, _set_popup_reveal)

    def _get_popup_opacity(self):
        return self._popup_opacity

    def _set_popup_opacity(self, value):
        self._popup_opacity = value
        if self._popup_widget is not None:
            self._popup_widget.update()

    popupOpacity = pyqtProperty(float, _get_popup_opacity, _set_popup_opacity)

    def _set_popup_open(self, value):
        angle = 180.0 if value else 0.0
        if not (self._arrow_animation.state() == QPropertyAnimation.Running
                and self._arrow_animation.endValue() == angle):
            self._arrow_animation.stop()
            self._arrow_animation.setStartValue(self._arrow_angle)
            self._arrow_animation.setEndValue(angle)
            self._arrow_animation.start()
        font = QFont(self.font())
        font.setFamily(font.family())
        if font.pixelSize() > 0:
            font.setPixelSize(font.pixelSize())
        else:
            font.setPointSizeF(font.pointSizeF())
        font.setWeight(font.weight())
        self.setProperty("popupOpen", value)
        self.style().unpolish(self)
        self.style().polish(self)
        # Re-polishing a dynamic selector can restore Qt's smaller class font.
        # Preserve the resolved control font and give the view the same font.
        self.setFont(font)
        self.view().setFont(font)
        self.update()

    def eventFilter(self, watched, event):
        if watched in getattr(self, "_native_scrollers", []):
            if event.type() == QEvent.Show:
                watched.hide()
                return True
        if watched is getattr(self, "_popup_owner", None):
            if (event.type() in (QEvent.Hide, QEvent.Close)
                    or (event.type() == QEvent.WindowStateChange and watched.isMinimized())):
                self._dismiss_popup()
        if watched is getattr(self, "_popup_widget", None):
            if event.type() == QEvent.Paint:
                painter = QPainter(watched)
                painter.setCompositionMode(QPainter.CompositionMode_Source)
                painter.fillRect(watched.rect(), Qt.transparent)
                painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
                painter.setRenderHint(QPainter.Antialiasing)
                if self._motion_snapshot is not None:
                    # Animate the cached picker surface without relayout:
                    # translateY(-4px), scale(.98), opacity -> natural state.
                    # The native window and list stay at their final geometry.
                    progress = self._popup_reveal
                    scale = .98 + .02 * progress
                    anchor_y = watched.height() if self._popup_upward else 0
                    offset = (4 if self._popup_upward else -4) * (1 - progress)
                    painter.setRenderHint(QPainter.SmoothPixmapTransform)
                    painter.setOpacity(self._popup_opacity)
                    painter.translate(watched.width() / 2, anchor_y + offset)
                    painter.scale(scale, scale)
                    painter.translate(-watched.width() / 2, -anchor_y)
                    painter.drawPixmap(0, 0, self._motion_snapshot)
                else:
                    painter.setPen(QPen(QColor("#d9e5f5"), 1))
                    painter.setBrush(QColor("white"))
                    painter.drawRoundedRect(QRectF(.5, .5, watched.width() - 1,
                                                   watched.height() - 1), 15, 15)
                painter.end()
                return True
            elif event.type() == QEvent.Hide:
                self._stop_popup_animation()
                self._clear_popup_motion()
                self._set_popup_open(False)
                self._popup_closing = False
            elif event.type() == QEvent.WindowDeactivate:
                self._dismiss_popup()
        return super().eventFilter(watched, event)

    def _capture_popup_motion(self, popup):
        if self._motion_snapshot is not None:
            return
        self._motion_snapshot = popup.grab()
        # Hide live list painting without changing its layout, focus or input.
        # A single view effect is removed as soon as the transition settles.
        self._motion_effect = QGraphicsOpacityEffect(self.view())
        self._motion_effect.setOpacity(0)
        self.view().setGraphicsEffect(self._motion_effect)

    def _clear_popup_motion(self):
        if self._motion_effect is not None:
            self.view().setGraphicsEffect(None)
            self._motion_effect = None
        self._motion_snapshot = None
        if self._popup_widget is not None:
            self._popup_widget.update()

    def _stop_popup_animation(self):
        if self._popup_group is not None:
            self._popup_group.stop()
            self._popup_group.deleteLater()
            self._popup_group = None

    def _animate_popup(self, popup, opening):
        self._stop_popup_animation()
        self._capture_popup_motion(popup)
        group = QParallelAnimationGroup(self)
        # Transform and alpha composite one cached layer with the reference's
        # respective easing curves. Native geometry and regions never change.
        reveal = QPropertyAnimation(self, b"popupReveal", group)
        reveal.setDuration(220 if opening else 160)
        reveal.setStartValue(self._popup_reveal)
        reveal.setEndValue(1.0 if opening else 0.0)
        reveal.setEasingCurve(picker_easing())
        group.addAnimation(reveal)
        opacity = QPropertyAnimation(self, b"popupOpacity", group)
        opacity.setDuration(220 if opening else 160)
        opacity.setStartValue(self._popup_opacity)
        opacity.setEndValue(1.0 if opening else 0.0)
        opacity.setEasingCurve(self._opacity_easing)
        group.addAnimation(opacity)
        if not opening:
            group.finished.connect(self._finish_popup_hide)
        else:
            group.finished.connect(self._finish_popup_show)
        self._popup_group = group
        group.start()

    def showPopup(self):
        # A pending click/animation must never open a top-level popup over
        # another application after the converter has lost focus.
        if (not self.isVisible() or not self.isEnabled()
                or QApplication.applicationState() != Qt.ApplicationActive
                or not self.window().isActiveWindow()):
            self._dismiss_popup()
            return
        if self._popup_widget is not None and self._popup_widget.isVisible():
            if self._popup_closing:
                self._popup_closing = False
                self._set_popup_open(True)
                self._animate_popup(self._popup_widget, True)
            return
        self._stop_popup_animation()
        self._clear_popup_motion()
        self._popup_closing = False
        self._popup_reveal = 1.0
        self._popup_opacity = 1.0
        owner = self.window()
        if owner is not self._popup_owner:
            if self._popup_owner is not None:
                self._popup_owner.removeEventFilter(self)
            self._popup_owner = owner
            owner.installEventFilter(self)
        # Do not expose a full-opacity native frame before the first animated
        # paint. QWidget.grab still captures the full content at opacity zero.
        if self._popup_widget is not None:
            self._popup_widget.setWindowOpacity(0)
        super().showPopup()
        popup = self.view().window()
        if popup is self.window() or not popup.isVisible():
            return
        if self._popup_widget is not popup:
            self._prepare_popup(popup)
        apply_browser_scrollbars(self.view())
        self._set_popup_open(True)
        anchor = self.mapToGlobal(QPoint(0, self.height()))
        top = self.mapToGlobal(QPoint(0, 0))
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        available = screen.availableGeometry()
        width = min(self.width(), available.width() - 16)
        rows = min(self.count(), self.maxVisibleItems())
        # Include the actual frame, layout margins and view padding instead
        # of guessing them. Even a few missing pixels can make hover scroll.
        popup.resize(width, popup.height())
        if popup.layout():
            popup.layout().activate()
        self.view().doItemsLayout()
        stride = max(1, self.view().sizeHintForRow(0)) + 2 * self.view().spacing()
        chrome = popup.height() - self.view().viewport().height()
        height = max(48, rows * stride + chrome)
        below = available.bottom() - anchor.y() - 14
        above = top.y() - available.top() - 14
        upward = below < height and above > below
        space = max(48, above if upward else below)
        visible_rows = max(1, min(rows, (space - chrome) // stride))
        height = min(space, max(48, visible_rows * stride + chrome))
        x = max(available.left() + 8, min(anchor.x(), available.right() - width - 7))
        y = top.y() - height - 6 if upward else anchor.y() + 6
        target = QRect(x, y, width, height)
        self._popup_target = target
        self._popup_upward = upward
        popup.setMinimumHeight(0)
        popup.setGeometry(target)
        if popup.layout():
            popup.layout().activate()
        self.view().doItemsLayout()
        self.view().scrollTo(self.view().model().index(self.currentIndex(), 0),
                             QAbstractItemView.EnsureVisible)
        self._set_popup_reveal(0)
        self._set_popup_opacity(0)
        self._animate_popup(popup, True)
        popup.repaint()
        popup.setWindowOpacity(1)

    def _finish_popup_show(self):
        if (QApplication.applicationState() != Qt.ApplicationActive
                or not (self.window().isActiveWindow()
                        or QApplication.activePopupWidget() is self._popup_widget)):
            self._dismiss_popup()
        else:
            self._clear_popup_motion()

    def _dismiss_popup(self):
        if self._popup_widget is None or self._dismissing_popup:
            return
        self._dismissing_popup = True
        try:
            self._stop_popup_animation()
            super().hidePopup()
            self._clear_popup_motion()
            self._popup_closing = False
            self._set_popup_open(False)
        finally:
            self._dismissing_popup = False

    def hidePopup(self):
        popup = self.view().window()
        if (self._popup_target is None or popup is self.window()
                or not popup.isVisible()):
            self._stop_popup_animation()
            self._set_popup_open(False)
            super().hidePopup()
            return
        if self._popup_closing:
            return
        if QApplication.applicationState() != Qt.ApplicationActive:
            self._dismiss_popup()
            return
        self._popup_closing = True
        self._set_popup_open(False)
        self._animate_popup(popup, False)

    def _finish_popup_hide(self):
        super().hidePopup()
        self._popup_closing = False
        self._set_popup_open(False)
