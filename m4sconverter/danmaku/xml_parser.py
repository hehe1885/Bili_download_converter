"""B 站弹幕 XML 解析。

支持两种根节点：普通 ``<i>``（comment.bilibili.com）与 BililiveRecorder 的
``<BililiveRecorder>``。逐条 ``<d>`` 的 ``p`` 属性按 B 站弹幕协议解析。
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .ass_writer import Bullet, BulletType, _MODE_MAP

LOG = logging.getLogger("m4s")


@dataclass
class DanmakuHeader:
    """XML 里携带的记录信息，用于 ASS 头部的注释行。"""

    roomid: str = ""
    name: str = ""
    title: str = ""

    def as_tuple(self) -> tuple[str, str, str]:
        return self.roomid, self.name, self.title


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_bullet_p(value: str, text: str) -> Bullet | None:
    """解析 ``p`` 属性 + 文本，返回弹幕或 ``None``。"""
    if not text:
        return None
    parts = value.split(",")
    if len(parts) < 8:
        return None
    try:
        time_s = float(parts[0])
    except (TypeError, ValueError):
        return None
    try:
        mode = int(float(parts[1]))
    except (TypeError, ValueError):
        mode = 1
    try:
        rgb = int(float(parts[3])) & 0xFFFFFF
    except (TypeError, ValueError):
        rgb = 0xFFFFFF

    bullet_type = _MODE_MAP.get(mode, BulletType.ROLL)
    if bullet_type is BulletType.NONE:
        return None
    normalized = text.replace("\n", "\\N").replace("\r", "\\N").strip()
    if not normalized:
        return None
    return Bullet(
        time_ms=int(time_s * 1000),
        type=bullet_type,
        rgb=rgb,
        text=normalized,
        length=len(normalized),
    )


def parse_danmaku_xml(path: Path, max_bullets: int | None = None) -> tuple[list[Bullet], DanmakuHeader]:
    """流式解析弹幕 XML，返回 ``(弹幕列表, 头部信息)``。"""
    header = DanmakuHeader()
    bullets: list[Bullet] = []
    bad = 0

    try:
        context = ET.iterparse(str(path), events=("start", "end"))
        for event, elem in context:
            tag = _strip_ns(elem.tag)
            if event == "start":
                if tag == "BililiveRecorderRecordInfo":
                    header.roomid = elem.get("roomid", "") or ""
                    header.name = elem.get("name", "") or ""
                    header.title = elem.get("title", "") or ""
                continue
            # end
            if tag == "d":
                bullet = parse_bullet_p(elem.get("p", ""), elem.text or "")
                if bullet is not None:
                    bullets.append(bullet)
                elif (elem.text or "").strip():
                    bad += 1
                elem.clear()
            elif tag in ("sc", "gift"):
                elem.clear()
            if max_bullets is not None and len(bullets) >= max_bullets:
                break
    except ET.ParseError as exc:
        LOG.warning("弹幕 XML 解析中断(%s): %s（已读 %d 条）", path, exc, len(bullets))
    except OSError as exc:
        LOG.error("无法读取弹幕文件 %s: %s", path, exc)

    if bad:
        LOG.debug("%s 中有 %d 条弹幕格式异常已忽略", path.name, bad)
    LOG.debug("弹幕解析: %s -> %d 条", path.name, len(bullets))
    return bullets, header
