"""Native Qt regression checks for popup sizing and application focus.

Run on a desktop: python tests/test_dropdown.py
No browser, registry or user files are touched.
"""
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QVBoxLayout, QWidget
from gui.theme import apply_theme, configure_dpi
from gui.widgets import AnimatedComboBox

configure_dpi()
app = QApplication([])
apply_theme(app)


class DropdownTests(unittest.TestCase):
    def setUp(self):
        self.host = QWidget()
        self.host.setWindowTitle("下拉菜单回归检查")
        self.host.resize(380, 180)
        self.host.move(100, 100)
        layout = QVBoxLayout(self.host)
        self.combo = AnimatedComboBox()
        layout.addWidget(self.combo)
        self.host.show()
        self.host.raise_()
        self.host.activateWindow()
        until = time.monotonic() + 1.5
        while (not self.host.isActiveWindow() or app.applicationState() != Qt.ApplicationActive) and time.monotonic() < until:
            QTest.qWait(20)
        QTest.qWait(60)

    def tearDown(self):
        self.combo._dismiss_popup()
        self.host.close()
        self.host.deleteLater()
        app.processEvents()

    def populate(self, count):
        self.combo.clear()
        self.combo.addItems([f"选项 {i + 1}" for i in range(count)])
        self.combo.setCurrentIndex(0)

    def open(self):
        self.host.activateWindow()
        QTest.qWait(60)
        self.combo.showPopup()
        for _ in range(12):
            QTest.qWait(25)
        self.assertTrue(self.combo.view().window().isVisible())
        return self.combo.view()

    def test_hover_last_item_keeps_first_item_visible(self):
        for count in (2, 3, 5, 7):
            with self.subTest(count=count):
                self.populate(count)
                view = self.open()
                self.assertEqual(view.verticalScrollBar().maximum(), 0)
                last = view.model().index(count - 1, 0)
                self.assertTrue(view.viewport().rect().contains(view.visualRect(last)))
                QTest.mouseMove(view.viewport(), view.visualRect(last).center())
                QTest.qWait(350)
                popup = view.window()
                QTest.mouseMove(popup, QPoint(popup.width() // 2, popup.height() - 12))
                QTest.qWait(350)
                self.assertEqual(view.verticalScrollBar().value(), 0)
                first = view.model().index(0, 0)
                self.assertTrue(view.viewport().rect().contains(view.visualRect(first)))
                self.combo._dismiss_popup()

    def test_animation_keeps_native_window_and_list_stationary(self):
        self.populate(5)
        self.combo.showPopup()
        view = self.combo.view()
        popup = view.window()
        geometry = popup.geometry()
        size = view.viewport().size()
        progress = []
        for _ in range(12):
            QTest.qWait(25)
            self.assertEqual(popup.geometry(), geometry)
            self.assertEqual(view.viewport().size(), size)
            self.assertTrue(popup.mask().isEmpty())
            self.assertAlmostEqual(popup.windowOpacity(), 1)
            self.assertEqual(view.verticalScrollBar().maximum(), 0)
            progress.append(self.combo.popupReveal)
        self.assertTrue(any(0 < value < 1 for value in progress), progress)
        self.assertIsNone(view.graphicsEffect())
        self.combo.hidePopup()
        for _ in range(9):
            QTest.qWait(25)
            if popup.isVisible():
                self.assertEqual(popup.geometry(), geometry)
                self.assertEqual(view.viewport().size(), size)
                self.assertTrue(popup.mask().isEmpty())
        self.assertFalse(popup.isVisible())
        self.assertIsNone(view.graphicsEffect())

    def test_rapid_reversal_resumes_current_frame_without_reopening_window(self):
        self.populate(5)
        self.combo.showPopup()
        for _ in range(3):
            QTest.qWait(25)
        popup = self.combo.view().window()
        geometry = popup.geometry()
        before_close = self.combo.popupReveal
        self.assertGreater(before_close, 0)
        self.assertLess(before_close, 1)
        self.combo.hidePopup()
        self.assertAlmostEqual(self.combo.popupReveal, before_close)
        for _ in range(2):
            QTest.qWait(25)
        before_reopen = self.combo.popupReveal
        self.assertLess(before_reopen, before_close)
        self.combo.showPopup()
        self.assertTrue(popup.isVisible())
        self.assertAlmostEqual(self.combo.popupReveal, before_reopen)
        self.assertEqual(popup.geometry(), geometry)
        for _ in range(12):
            QTest.qWait(25)
        self.assertTrue(popup.isVisible())
        self.assertAlmostEqual(self.combo.popupReveal, 1)
        self.assertIsNone(self.combo.view().graphicsEffect())

    def test_mouse_and_keyboard_selection(self):
        self.populate(5)
        view = self.open()
        QTest.keyClick(view, Qt.Key_Down)
        QTest.keyClick(view, Qt.Key_Return)
        QTest.qWait(200)
        self.assertEqual(self.combo.currentIndex(), 1)
        view = self.open()
        index = view.model().index(4, 0)
        QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.visualRect(index).center())
        QTest.qWait(200)
        self.assertEqual(self.combo.currentIndex(), 4)
        self.assertFalse(view.window().isVisible())

    def test_round_popup_has_alpha_edges_and_no_arrow_strips(self):
        self.populate(12)
        view = self.open()
        popup = view.window()
        self.assertTrue(popup.testAttribute(Qt.WA_TranslucentBackground))
        self.assertTrue(popup.mask().isEmpty())
        for scroller in self.combo._native_scrollers:
            self.assertFalse(scroller.isVisible())
            self.assertEqual(scroller.height(), 0)
        image = popup.grab().toImage()
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        alphas = [image.pixelColor(x, y).alpha()
                  for x in range(min(25, image.width()))
                  for y in range(min(25, image.height()))]
        self.assertTrue(any(0 < alpha < 255 for alpha in alphas))

    def test_arrow_rotates_while_opening_and_closing(self):
        self.populate(5)
        self.assertAlmostEqual(self.combo.arrowAngle, 0)
        self.combo.showPopup()
        opening = []
        for _ in range(9):
            QTest.qWait(25)
            opening.append(self.combo.arrowAngle)
        self.assertTrue(self.combo.view().window().isVisible())
        self.assertTrue(any(0 < angle < 180 for angle in opening), opening)
        self.assertAlmostEqual(self.combo.arrowAngle, 180)
        self.combo.hidePopup()
        closing = []
        for _ in range(9):
            QTest.qWait(25)
            closing.append(self.combo.arrowAngle)
        self.assertTrue(any(0 < angle < 180 for angle in closing), closing)
        self.assertAlmostEqual(self.combo.arrowAngle, 0)

    def test_popup_keeps_trigger_font_before_during_and_after_open(self):
        self.populate(5)
        font = self.combo.font()
        signature = lambda f: (f.family(), f.pointSizeF(), f.pixelSize(), f.weight())
        before = signature(font)
        view = self.open()
        self.assertEqual(signature(self.combo.font()), before)
        self.assertEqual(signature(view.font()), before)
        self.combo.hidePopup()
        QTest.qWait(200)
        self.assertEqual(signature(self.combo.font()), before)
        self.combo.setEditable(True)
        self.combo.setEditText("自定义字体")
        view = self.open()
        QTest.keyClick(view, Qt.Key_Escape)
        QTest.qWait(200)
        self.assertEqual(self.combo.currentText(), "自定义字体")

    def test_long_menu_still_scrolls_with_keyboard(self):
        self.populate(12)
        view = self.open()
        self.assertGreater(view.verticalScrollBar().maximum(), 0)
        QTest.keyClick(view, Qt.Key_End)
        QTest.keyClick(view, Qt.Key_Return)
        QTest.qWait(200)
        self.assertEqual(self.combo.currentIndex(), 11)

    def test_inactive_application_cannot_open_popup(self):
        self.populate(5)
        self.open()
        popup = self.combo.view().window()
        self.combo._application_state_changed(Qt.ApplicationInactive)
        self.assertFalse(popup.isVisible())
        QTest.qWait(250)
        self.assertFalse(popup.isVisible())
        with patch.object(QApplication, "applicationState", return_value=Qt.ApplicationInactive):
            self.combo.showPopup()
            QTest.qWait(250)
            self.assertFalse(popup.isVisible())

    def test_owner_hiding_does_not_leave_popup_over_another_process(self):
        self.populate(5)
        self.open()
        popup = self.combo.view().window()
        helper = """
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QWidget
app = QApplication([])
window = QWidget()
window.setWindowTitle('外部窗口切换检查')
window.resize(280, 120)
window.move(600, 100)
window.show()
window.raise_()
window.activateWindow()
QTimer.singleShot(1700, app.quit)
app.exec_()
"""
        child = subprocess.Popen([sys.executable, "-c", helper],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deactivated = False
        deadline = time.monotonic() + 6
        try:
            QTest.qWait(300)
            self.host.hide()
            self.assertFalse(popup.isVisible())
            while child.poll() is None and time.monotonic() < deadline:
                app.processEvents()
                if app.applicationState() != Qt.ApplicationActive:
                    deactivated = True
                    self.assertFalse(popup.isVisible())
                QTest.qWait(15)
            child.wait(timeout=2)
            self.assertTrue(deactivated, "Application remained active after hiding its owner")
            QTest.qWait(300)
            self.assertFalse(popup.isVisible())
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=3)

    def test_focus_loss_while_opening_cancels_the_animation(self):
        self.populate(5)
        self.combo.showPopup()
        QTest.qWait(35)
        popup = self.combo.view().window()
        self.assertTrue(popup.isVisible())
        other = QWidget()
        other.setWindowTitle("窗口焦点切换检查")
        other.show()
        QApplication.setActiveWindow(other)
        QTest.qWait(30)
        self.assertFalse(popup.isVisible())
        QTest.qWait(300)
        self.assertFalse(popup.isVisible())
        other.close()

    def test_minimizing_owner_dismisses_popup(self):
        self.populate(5)
        view = self.open()
        self.host.showMinimized()
        QTest.qWait(50)
        self.assertFalse(view.window().isVisible())
        QTest.qWait(250)
        self.assertFalse(view.window().isVisible())


if __name__ == "__main__":
    unittest.main(verbosity=2)
