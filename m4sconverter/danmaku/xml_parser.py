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


def _read_varint(buf: bytes, pos: int, end: int) -> tuple[int, int]:
    res = 0
    shift = 0
    while pos < end:
        b = buf[pos]
        pos += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            return res, pos
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")
    raise ValueError("unexpected EOF in varint")


def _parse_pb_fields(buf: bytes, start: int, end: int) -> dict[int, list[int | bytes]]:
    pos = start
    fields: dict[int, list[int | bytes]] = {}
    while pos < end:
        tag, pos = _read_varint(buf, pos, end)
        field_no, wire = tag >> 3, tag & 7
        if wire == 0:
            val, pos = _read_varint(buf, pos, end)
            fields.setdefault(field_no, []).append(val)
        elif wire == 1:
            if pos + 8 > end:
                break
            fields.setdefault(field_no, []).append(buf[pos:pos + 8])
            pos += 8
        elif wire == 2:
            ln, pos = _read_varint(buf, pos, end)
            if pos + ln > end:
                break
            fields.setdefault(field_no, []).append(buf[pos:pos + ln])
            pos += ln
        elif wire == 5:
            if pos + 4 > end:
                break
            fields.setdefault(field_no, []).append(buf[pos:pos + 4])
            pos += 4
        else:
            break
    return fields


def parse_danmaku_pb(
    paths: list[Path] | tuple[Path, ...],
    max_bullets: int | None = None,
) -> tuple[list[Bullet], DanmakuHeader]:
    """解析 PC 端 ``dm1``/``dm2``… 或移动端 ``danmaku.pb``（DmSegMobileReply Protobuf）。"""
    header = DanmakuHeader()
    bullets: list[Bullet] = []
    seen_ids: set[int] = set()

    for path in paths:
        try:
            data = path.read_bytes()
        except OSError as exc:
            LOG.warning("无法读取 Protobuf 弹幕 %s: %s", path, exc)
            continue
        try:
            top = _parse_pb_fields(data, 0, len(data))
        except ValueError:
            continue
        for raw_elem in top.get(1, []):
            if not isinstance(raw_elem, bytes):
                continue
            try:
                f = _parse_pb_fields(raw_elem, 0, len(raw_elem))
            except ValueError:
                continue
            dmid = int(f.get(1, [0])[0]) if isinstance(f.get(1, [0])[0], int) else 0
            if dmid and dmid in seen_ids:
                continue
            if dmid:
                seen_ids.add(dmid)

            progress = f.get(2, [0])[0]
            time_ms = int(progress) if isinstance(progress, int) else 0
            mode_val = f.get(3, [1])[0]
            mode = int(mode_val) if isinstance(mode_val, int) else 1
            color_val = f.get(5, [0xFFFFFF])[0]
            rgb = (int(color_val) if isinstance(color_val, int) else 0xFFFFFF) & 0xFFFFFF
            raw_text = f.get(7, [b""])[0]
            text = raw_text.decode("utf-8", "replace") if isinstance(raw_text, bytes) else ""
            normalized = text.replace("\n", "\\N").replace("\r", "\\N").strip()
            if not normalized:
                continue
            bullet_type = _MODE_MAP.get(mode, BulletType.ROLL)
            if bullet_type is BulletType.NONE:
                continue
            bullets.append(
                Bullet(
                    time_ms=max(0, time_ms),
                    type=bullet_type,
                    rgb=rgb,
                    text=normalized,
                    length=len(normalized),
                )
            )
            if max_bullets is not None and len(bullets) >= max_bullets:
                break
        if max_bullets is not None and len(bullets) >= max_bullets:
            break

    LOG.debug("Protobuf 弹幕解析: %d 个文件 -> %d 条", len(paths), len(bullets))
    return bullets, header


_REV_MODE_MAP = {
    BulletType.ROLL: 1,
    BulletType.BOTTOM: 4,
    BulletType.TOP: 5,
    BulletType.SC: 1,
}


def write_danmaku_xml(bullets: list[Bullet], dest: Path, cid: str = "") -> bool:
    """将弹幕列表导出为标准 B 站 XML 格式（供 PC 端 protobuf 弹幕导出 .danmaku.xml）。"""
    from xml.sax.saxutils import escape

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<i>",
        "<chatserver>chat.bilibili.com</chatserver>",
        f"<chatid>{escape(str(cid or 0))}</chatid>",
    ]
    for b in bullets:
        mode = _REV_MODE_MAP.get(b.type, 1)
        time_s = b.time_ms / 1000.0
        text = escape(b.text.replace("\\N", "\n"))
        lines.append(f'<d p="{time_s:.3f},{mode},25,{b.rgb},0,0,0,0">{text}</d>')
    lines.append("</i>\n")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(lines), encoding="utf-8")
        return True
    except OSError as exc:
        LOG.warning("写入 danmaku.xml 失败 %s: %s", dest, exc)
        return False
