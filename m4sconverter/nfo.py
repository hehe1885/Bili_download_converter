"""Emby / Jellyfin / Kodi 刮削用 ``.nfo`` 生成。

供 Emby / Jellyfin / Kodi 直接刮削，是本版新增的可选产物。
"""

from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from xml.sax.saxutils import escape

from .models import VideoItem

LOG = logging.getLogger("m4s")

#: 顶层标签，按内容类型决定 movie / tvshow 之外一律用 movie。
_ROOT_TAG = "movie"


def _tag(name: str, value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return f"  <{name}>{escape(text)}</{name}>\n"


def build_nfo(item: VideoItem, poster_name: str = "", fanart_name: str = "") -> str:
    """生成 NFO 文本。"""
    title = item.primary_title()
    outline = item.part_title() if item.part and item.part != title else ""

    body = "".join([
        _tag("title", title),
        _tag("originaltitle", item.title if item.title != title else ""),
        _tag("sorttitle", title),
        _tag("outline", outline),
        _tag("studio", item.up),
        _tag("credits", item.up),
        _tag("director", item.up),
        _tag("premiered", _dt.date.today().isoformat()),
        _tag("year", _dt.date.today().year),
        _tag("runtime", int(item.duration_s / 60) if item.duration_ms else ""),
    ])

    ids = "".join([
        _tag("avid", item.avid),
        _tag("bvid", item.bvid),
        _tag("cid", item.cid),
        _tag("up", item.up),
        _tag("upid", item.owner_id),
        _tag("quality", item.quality),
    ])

    art = ""
    if poster_name:
        art += f"    <poster>{escape(poster_name)}</poster>\n"
    if fanart_name:
        art += f"    <fanart>{escape(fanart_name)}</fanart>\n"
    art_block = f"  <art>\n{art}  </art>\n" if art else ""

    return (
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
        f"<{_ROOT_TAG}>\n{body}{art_block}"
        f"  <bilibili>\n{ids}  </bilibili>\n"
        f"</{_ROOT_TAG}>\n"
    )


def write_nfo(path: Path, item: VideoItem, poster_name: str = "",
              fanart_name: str = "") -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_nfo(item, poster_name, fanart_name), encoding="utf-8")
    except OSError as exc:
        LOG.warning("写入 NFO 失败 %s: %s", path, exc)
        return False
    return True


__all__ = ["build_nfo", "write_nfo"]
