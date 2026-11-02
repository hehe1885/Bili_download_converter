"""Exercise startup, setup and cache help without network or registry writes."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest, QSignalSpy
from PyQt5.QtWidgets import QApplication, QWidget
from gui.theme import configure_dpi, apply_theme
from gui.main_window import MainWindow
from gui.ffmpeg_dialog import FFmpegDialog, InstallWorker
from gui.cache_help import CacheHelpDialog
from m4sconverter.ffmpeg_setup import InstallCancelled
from m4sconverter.mux import MuxTools

configure_dpi()
app = QApplication([])
apply_theme(app)


def wait_for(predicate):
    until = time.monotonic() + 4
    while not predicate() and time.monotonic() < until:
        app.processEvents()
        QTest.qWait(10)
    return predicate()


class SetupGuiTests(unittest.TestCase):
    def test_modal_backdrop_tracks_owner_and_is_removed_on_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            window = MainWindow(Path(tmp) / "config.json", check_dependencies=False)
            window.show()
            window.show_cache_help()
            app.processEvents()
            dialog = window.cache_help_dialog
            self.assertTrue(dialog._backdrop.isVisible())
            window.resize(window.width() + 20, window.height() + 20)
            app.processEvents()
            self.assertEqual(dialog._backdrop.geometry(), window.rect())
            dialog.reject()
            QTest.qWait(20)
            self.assertIsNone(dialog._backdrop)
            self.assertFalse(any(w.isVisible() for w in window.findChildren(QWidget, "dialogBackdrop")))
            window.close()

    def test_missing_ffmpeg_opens_setup_on_startup(self):
        with tempfile.TemporaryDirectory() as tmp, patch("gui.ffmpeg_dialog.validate_ffmpeg", return_value=False):
            window = MainWindow(Path(tmp) / "config.json")
            window.show()
            self.assertTrue(wait_for(lambda: window.setup_dialog is not None))
            self.assertTrue(window.setup_dialog.isVisible())
            self.assertTrue(window.setup_dialog.system_check.isChecked())
            self.assertTrue(wait_for(lambda: window.tool_worker.isFinished()))
            window.setup_dialog.reject()
            window.close()

    def test_existing_ffmpeg_does_not_prompt(self):
        with tempfile.TemporaryDirectory() as tmp, patch("gui.ffmpeg_dialog.validate_ffmpeg", return_value=True), patch("gui.ffmpeg_dialog.find_tools", return_value=MuxTools(ffmpeg=Path("C:/example/ffmpeg.exe"))):
            window = MainWindow(Path(tmp) / "config.json")
            window.show()
            self.assertTrue(wait_for(lambda: window.dependency_btn.text() == "FFmpeg · 已就绪"))
            self.assertIsNone(window.setup_dialog)
            self.assertTrue(wait_for(lambda: window.tool_worker.isFinished()))
            window.close()

    def test_cache_help_uses_existing_directory_and_syncs_fields(self):
        with tempfile.TemporaryDirectory() as tmp, patch("gui.cache_help.windows_cache_location", return_value=Path(tmp)):
            window = MainWindow(Path(tmp) / "config.json", check_dependencies=False)
            window.show_cache_help()
            dialog = window.cache_help_dialog
            self.assertTrue(dialog.use_btn.isEnabled())
            QTest.mouseClick(dialog.use_btn, Qt.LeftButton)
            self.assertEqual(window.top_import_edit.text(), tmp)
            self.assertEqual(window.options_panel.import_edit.text(), tmp)
            self.assertEqual(window.options_panel.tabs.count(), 4)
            window.close()

    def test_install_failure_and_cancel_have_feedback(self):
        for error in (OSError("network unavailable"), InstallCancelled("UAC cancelled")):
            with self.subTest(error=error), patch("gui.ffmpeg_dialog.install_ffmpeg", side_effect=error):
                dialog = FFmpegDialog()
                dialog._install_or_finish()
                self.assertTrue(wait_for(lambda: not dialog._busy))
                self.assertIn(str(error), dialog.status.text())
                self.assertTrue(dialog.install_btn.isEnabled())
                dialog.close()

    def test_install_success_updates_config_and_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as tmp, patch("gui.ffmpeg_dialog.install_ffmpeg", return_value=Path(tmp) / "ffmpeg.exe") as install:
            window = MainWindow(Path(tmp) / "config.json", check_dependencies=False)
            window.show_ffmpeg_setup()
            dialog = window.setup_dialog
            dialog._install_or_finish()
            dialog._install_or_finish()
            self.assertTrue(wait_for(lambda: not dialog._busy))
            self.assertEqual(install.call_count, 1)
            self.assertEqual(dialog.install_btn.text(), "开始使用")
            self.assertIn("系统 PATH", dialog.status.text())
            self.assertEqual(window.options_panel.ffmpeg_edit.text(), str(Path(tmp) / "ffmpeg.exe"))
            self.assertTrue((Path(tmp) / "config.json").is_file())
            dialog.close()
            window.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
