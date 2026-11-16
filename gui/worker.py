"""后台工作线程：目录扫描 与 转换队列。

两个线程都以 Qt 信号回到 GUI 线程，绝不在工作线程里碰控件。
转换线程额外实现了「同名冲突询问」：工作线程阻塞等待，GUI 线程弹框，
结果通过 :meth:`ConvertWorker.answer_conflict` 回填。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from PyQt5.QtCore import QThread, pyqtSignal

from m4sconverter import __version__
from m4sconverter.converter import Converter
from m4sconverter.models import ConvertResult, Options, ScanReport, VideoItem
from m4sconverter.scanner import scan

LOG = logging.getLogger("m4s")

GITHUB_REPO = "hehe1885/Bili_download_converter"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_LIST_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases"


@dataclass
class ReleaseInfo:
    tag_name: str
    version: str
    name: str
    html_url: str
    body: str
    has_update: bool


def parse_version(text: str) -> tuple[int, ...]:
    """把 ``v0.2.2`` / ``0.2.2`` 解析为可比较的整数元组 ``(0, 2, 2)``。"""
    cleaned = (text or "").strip().lstrip("vV")
    nums = [int(p) for p in re.findall(r"\d+", cleaned)]
    return tuple(nums) if nums else (0,)


def is_newer_version(remote: str, local: str = __version__) -> bool:
    r = list(parse_version(remote))
    l = list(parse_version(local))
    length = max(len(r), len(l))
    r.extend([0] * (length - len(r)))
    l.extend([0] * (length - len(l)))
    return tuple(r) > tuple(l)


def fetch_latest_release(current_version: str = __version__, timeout: float = 10.0) -> ReleaseInfo:
    """查询 GitHub Releases 最新版本信息。"""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Bili_download_converter/{current_version}",
    }
    data = None
    last_exc: Exception | None = None
    for url, is_list in ((LATEST_RELEASE_API, False), (RELEASES_LIST_API, True)):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            if is_list:
                if isinstance(payload, list) and payload:
                    data = next((r for r in payload if isinstance(r, dict) and not r.get("draft")), payload[0])
            elif isinstance(payload, dict):
                data = payload
            if data:
                break
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            last_exc = exc
            continue

    if not isinstance(data, dict) or not data.get("tag_name"):
        if last_exc is not None:
            raise RuntimeError(f"无法连接 GitHub Releases：{last_exc}") from last_exc
        raise RuntimeError("未在仓库中找到已发布的 Release 版本")

    tag = str(data.get("tag_name") or "").strip()
    ver = tag.lstrip("vV") or tag
    name = str(data.get("name") or tag).strip()
    html_url = str(data.get("html_url") or RELEASES_PAGE_URL).strip()
    body = str(data.get("body") or "").strip()
    return ReleaseInfo(
        tag_name=tag,
        version=ver,
        name=name,
        html_url=html_url,
        body=body,
        has_update=is_newer_version(tag, current_version),
    )


class UpdateCheckWorker(QThread):
    """在后台查询 GitHub Release 是否有新版本。"""

    checked = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, current_version: str = __version__, parent=None) -> None:
        super().__init__(parent)
        self.current_version = current_version

    def run(self) -> None:
        try:
            info = fetch_latest_release(self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.checked.emit(info)


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


__all__ = [
    "ScanWorker",
    "ConvertWorker",
    "UpdateCheckWorker",
    "ReleaseInfo",
    "RELEASES_PAGE_URL",
    "parse_version",
    "is_newer_version",
    "fetch_latest_release",
]
