"""Verify high DPI icons and native message-button/keyboard behavior."""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from gui.message_box import MessageBox, status_icon
from gui.theme import apply_theme, configure_dpi

configure_dpi()
app = QApplication([])
apply_theme(app)


class MessageTests(unittest.TestCase):
    def test_icons_have_physical_resolution_and_smooth_alpha_edges(self):
        for kind in (MessageBox.Information, MessageBox.Warning,
                     MessageBox.Critical, MessageBox.Question):
            for dpr in (1.0, 1.25, 1.5, 2.0):
                with self.subTest(kind=kind, dpr=dpr):
                    pixmap = status_icon(kind, dpr)
                    self.assertEqual(pixmap.width(), math.ceil(40 * dpr))
                    self.assertAlmostEqual(pixmap.devicePixelRatioF(), dpr)
                    image = pixmap.toImage()
                    self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                    alphas = [image.pixelColor(x, y).alpha()
                              for x in range(image.width()) for y in range(image.height())]
                    self.assertTrue(any(0 < alpha < 255 for alpha in alphas))

    def test_all_single_button_messages_return_ok(self):
        for show in (MessageBox.information, MessageBox.warning, MessageBox.critical):
            with self.subTest(method=show.__name__):
                QTimer.singleShot(60, lambda: QTest.keyClick(app.activeModalWidget(), Qt.Key_Return))
                self.assertEqual(show(None, "测试提示", "测试内容"), MessageBox.Ok)

    def test_question_keeps_no_default_and_escape(self):
        def escape():
            box = app.activeModalWidget()
            self.assertEqual(box.standardButton(box.defaultButton()), MessageBox.No)
            QTest.keyClick(box, Qt.Key_Escape)
        QTimer.singleShot(60, escape)
        self.assertEqual(MessageBox.question(None, "是否覆盖？", "确认内容",
                                             MessageBox.Yes | MessageBox.No, MessageBox.No), MessageBox.No)

    def test_yes_button_returns_yes(self):
        def choose_yes():
            box = app.activeModalWidget()
            QTest.mouseClick(box.button(MessageBox.Yes), Qt.LeftButton)
        QTimer.singleShot(60, choose_yes)
        self.assertEqual(MessageBox.question(None, "是否继续？", "确认内容"), MessageBox.Yes)

    def test_show_uses_actual_screen_dpi(self):
        box = MessageBox(MessageBox.Information, "测试提示", "图标清晰度检查")
        box.show()
        app.processEvents()
        self.assertAlmostEqual(box.iconPixmap().devicePixelRatioF(), box.devicePixelRatioF())
        self.assertEqual(box.iconPixmap().width(), math.ceil(40 * box.devicePixelRatioF()))
        box.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
