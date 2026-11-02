"""Shared typography and high DPI setup for the app and preview tools."""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QApplication, QLabel
from pathlib import Path
import sys


def configure_dpi() -> None:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    if hasattr(QApplication, "setHighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    families = set(QFontDatabase().families())
    family = next((f for f in ("Microsoft YaHei UI", "Microsoft YaHei",
                               "Noto Sans CJK SC", "Segoe UI") if f in families),
                  app.font().family())
    font = QFont(family, 11)
    font.setPointSizeF(10.5)
    font.setStyleStrategy(QFont.PreferAntialias)
    font.setHintingPreference(QFont.PreferDefaultHinting)
    app.setFont(font)
    assets = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "assets"
    app.setStyleSheet(STYLE.replace("@ASSETS@", assets.as_posix()))


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("sectionTitle", True)
    return label


STYLE = """
QWidget { color: #25334a; }
QMainWindow { background: #f4f6fa; }
QDialog { background: #f4f6fa; }
QDialog#roundedDialog { background: transparent; }
QFrame#dialogSurface { background: #ffffff; border: 1px solid #cbd7e7; border-radius: 22px; }
QToolButton#dialogClose { background: transparent; color: #8390a3; border: none;
  border-radius: 15px; font-size: 18pt; }
QToolButton#dialogClose:hover { background: #e9eef6; color: #334969; }
QLabel { background: transparent; }
QLabel#appTitle { font-size: 17pt; font-weight: 600; color: #17243b; }
QLabel#subtitle, QLabel#hint { color: #637189; }
QLabel#hint { font-size: 9.5pt; }
QLabel#eyebrow { color: #3970dc; font-size: 10pt; font-weight: 400; }
QLabel#dialogTitle { font-size: 17pt; font-weight: 600; color: #17243b; }
QLabel#stepTitle { font-size: 10.5pt; font-weight: 400; color: #17243b; }
QLabel#stepNumber { background: #eaf0fe; color: #3468cf; border-radius: 16px;
  min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; }
QFrame#installCard { background: white; border: 1px solid #e5ebf3; border-radius: 16px; }
QGroupBox, QLabel[sectionTitle="true"] { font-size: 10.5pt; font-weight: 400; color: #17243b; }
QGroupBox { background: #ffffff; border: 1px solid #e5ebf3; border-radius: 16px;
  margin-top: 0; padding: 34px 10px 10px; }
QGroupBox::title { subcontrol-origin: border; subcontrol-position: top left;
  left: 14px; top: 10px; padding: 0; }
QScrollArea, QScrollArea > QWidget > QWidget { background: #f4f6fa; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: white;
  border: 1px solid #dbe2ed; border-radius: 11px; padding: 5px 9px;
  min-height: 20px; selection-background-color: #3671e8; }
QComboBox { background: #edf2f9; border-color: #d6e3f5; border-radius: 13px;
  padding-left: 11px; padding-right: 28px; }
QComboBox:hover, QComboBox[popupOpen="true"] { background: #e8eff9; border-color: #c9d9f2; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #3671e8; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
  background: #f0f3f8; color: #8691a3; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border;
  subcontrol-position: top right; width: 26px; border: none; border-top-right-radius: 6px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border;
  subcontrol-position: bottom right; width: 26px; border: none; border-bottom-right-radius: 6px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url(@ASSETS@/chevron-up.svg); width: 10px; height: 7px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url(@ASSETS@/chevron-down.svg); width: 10px; height: 7px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #e9f0ff;
  selection-color: #244679; padding: 4px; }
QPushButton { background: white; border: 1px solid #dbe2ed; border-radius: 12px;
  padding: 5px 10px; min-height: 20px; }
QPushButton:hover { background: #edf3ff; border-color: #9db6ec; }
QPushButton:pressed { background: #e0eaff; }
QPushButton:disabled { background: #edf0f5; color: #98a3b4; border-color: #dfe5ef; }
QPushButton[primary="true"] { background: #356fe5; color: white; border-color: #356fe5;
  font-weight: 600; }
QPushButton[primary="true"]:hover { background: #285fd1; }
QPushButton[primary="true"]:disabled { background: #a6bde9; border-color: #a6bde9; }
QPushButton#dependencyButton { color: #3567bd; background: #edf3ff; border-color: #d6e3ff; }
QCheckBox { spacing: 8px; padding: 3px 0; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #c5d0e1;
  background: white; border-radius: 4px; }
QCheckBox::indicator:hover { border-color: #5485ee; }
QCheckBox::indicator:checked { background: #356fe5; border-color: #356fe5;
  image: url(@ASSETS@/check.svg); }
QCheckBox::indicator:disabled { background: #edf0f5; border-color: #dbe2ee; }
QTabWidget::pane { border: none; background: transparent; }
QTabWidget::tab-bar { left: 0; }
QTabBar::tab { background: #e9eef7; color: #6b7990; border: none;
  border-radius: 11px; padding: 7px 14px; margin: 0 4px 7px 0; }
QTabBar::tab:selected { background: #e3ecff; color: #2f65cd; font-weight: 400; }
QTabBar::tab:hover:!selected { background: #e0e7f2; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 3px 0; }
QScrollBar::handle:vertical { background: #b7c3d3; border-radius: 3px; min-height: 42px; }
QScrollBar::handle:vertical:hover { background: #a5b6ce; }
QScrollBar::handle:vertical:pressed { background: #819cc3; }
QScrollBar:horizontal { background: transparent; height: 7px; margin: 0 3px; }
QScrollBar::handle:horizontal { background: #b7c3d3; border-radius: 3px; min-width: 42px; }
QScrollBar::handle:horizontal:hover { background: #a5b6ce; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: none; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QAbstractScrollArea::corner { background: transparent; }
QTableWidget, QPlainTextEdit { background: white; alternate-background-color: #f7f9fd;
  border: 1px solid #e5ebf3; border-radius: 16px; gridline-color: #edf0f6;
  selection-background-color: #e4edff; selection-color: #25334a; }
QPlainTextEdit { padding: 8px; }
QHeaderView::section { background: #edf2fa; border: none;
  border-bottom: 1px solid #dfe5ef; padding: 7px 6px; font-size: 10.5pt; font-weight: 400; }
QTableWidget::item { padding: 5px; }
QSplitter::handle { background: transparent; width: 8px; height: 8px; }
QStatusBar { background: #eaf0f8; padding: 4px; }
QStatusBar::item { border: none; }
QProgressBar { background: #e5ebf5; border: none; border-radius: 6px;
  min-height: 14px; text-align: center; }
QProgressBar::chunk { background: #5485ee; border-radius: 6px; }
QToolTip { background: #25334a; color: white; padding: 7px; border: none; }
"""
