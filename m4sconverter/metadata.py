"""元数据解析：``entry.json`` / ``videoInfo.json`` / ``.videoInfo`` / ``.playurl`` / ``index.json``。

字段兜底链覆盖各家客户端下载器的差异，并额外提取若干用于命名的字段。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

LOG = logging.getLogger("m4s")

INFO_ENTRY = "entry.json"
INFO_VIDEOINFO_JSON = "videoInfo.json"
INFO_VIDEOINFO = ".videoInfo"
INFO_PLAYURL = ".playurl"
INFO_INDEX = "index.json"
DANMAKU_XML = "danmaku.xml"
DANMAKU_ASS = "danmaku.ass"

#: 「缓存已完成」状态白名单。
COMPLETE_STATUS = {"", "completed", "视频已缓存完成"}

_INFO_ORDER = (INFO_ENTRY, INFO_VIDEOINFO_JSON, INFO_VIDEOINFO)


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

def read_json(path: Union[str, Path]) -> dict[str, Any] | None:
    """容错读取 JSON（自动处理 BOM）。"""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        LOG.debug("读取 %s 失败: %s", path, exc)
        return None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            data = json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        return data if isinstance(data, dict) else None
    LOG.warning("无法解析 JSON: %s", path)
    return None


def _s(value: Any) -> str:
    """安全转字符串：None / 非标量 → 空串。"""
    if value is None or isinstance(value, (dict, list)):
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _i(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _dig(obj: Any, *keys: str) -> Any:
    """安全的逐层取值，任一层缺失返回 None。"""
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


# ---------------------------------------------------------------------------
# 文件定位
# ---------------------------------------------------------------------------

def _playurl_candidates(level: Path) -> list[Path]:
    return [
        level / INFO_PLAYURL,
        level / f"{level.name}{INFO_PLAYURL}",
    ]


def find_info_file(media_dir: Path, root: Path, max_up: int = 4) -> tuple[Path | None, str]:
    """从 ``media_dir`` 起逐级向上找元数据文件，返回 ``(路径, 类型)``。

    每一级内部按 ``entry.json`` → ``videoInfo.json`` → ``.videoInfo`` → ``.playurl`` 的
    优先级取第一个命中的，因此「近处优先、同级按优先级」。
    """
    level = media_dir
    root = root.resolve() if root.exists() else root
    for _ in range(max_up):
        for name in _INFO_ORDER:
            cand = level / name
            if cand.is_file():
                return cand, name
        for cand in _playurl_candidates(level):
            if cand.is_file():
                return cand, INFO_PLAYURL
        try:
            if level.resolve() == root or level.parent == level:
                break
        except OSError:
            break
        level = level.parent
    return None, ""


def find_danmaku_xml(media_dir: Path, cid: str) -> Path | None:
    """按固定优先级找本地弹幕 xml。"""
    names: list[Path] = [media_dir / DANMAKU_XML, media_dir.parent / DANMAKU_XML]
    if cid:
        names += [media_dir / f"{cid}.xml", media_dir.parent / f"{cid}.xml"]
    for p in names:
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p
        except OSError:
            continue
    for directory in (media_dir, media_dir.parent):
        try:
            found = sorted(directory.glob("*.xml"))
        except OSError:
            continue
        for p in found:
            try:
                if p.stat().st_size > 0:
                    return p
            except OSError:
                continue
    return None


def find_existing_ass(media_dir: Path, cid: str) -> Path | None:
    names: list[Path] = [media_dir / DANMAKU_ASS, media_dir.parent / DANMAKU_ASS]
    if cid:
        names += [media_dir / f"{cid}.ass", media_dir.parent / f"{cid}.ass"]
    for p in names:
        if p.is_file():
            return p
    for directory in (media_dir, media_dir.parent):
        try:
            found = sorted(directory.glob("*.ass"))
        except OSError:
            continue
        if found:
            return found[0]
    return None


def find_cover(media_dir: Path, cover_url: str) -> Path | None:
    for name in ("cover.jpg", "cover.png", "cover.webp"):
        for directory in (media_dir, media_dir.parent):
            p = directory / name
            if p.is_file():
                return p
    if cover_url:
        stem = Path(cover_url.split("?")[0]).stem
        if stem:
            for directory in (media_dir, media_dir.parent):
                p = directory / f"{stem}.jpg"
                if p.is_file():
                    return p
    for directory in (media_dir, media_dir.parent):
        try:
            found = sorted(directory.glob("*.jpg")) + sorted(directory.glob("*.png"))
        except OSError:
            continue
        if found:
            return found[0]
    return None


# ---------------------------------------------------------------------------
# 轨道提示（index.json / .playurl）
# ---------------------------------------------------------------------------

@dataclass
class TrackHint:
    """从 index.json / .playurl 提取的轨道 id，用于把 m4s 文件配成对。"""

    video_ids: list[str] = field(default_factory=list)
    audio_ids: list[str] = field(default_factory=list)
    video_qid: str = ""

    @property
    def empty(self) -> bool:
        return not self.video_ids and not self.audio_ids


def parse_track_hint(info_path: Path | None, info_kind: str, media_dir: Path) -> TrackHint:
    hint = TrackHint()
    if info_kind == INFO_PLAYURL and info_path is not None:
        data = read_json(info_path)
        if not data:
            return hint
        dash = _dig(data, "data", "dash") or data.get("dash") or {}
        for track in (dash.get("video") or []):
            tid = _s(_dig(track, "id"))
            if tid:
                hint.video_ids.append(tid)
        for track in (dash.get("audio") or []):
            tid = _s(_dig(track, "id"))
            if tid:
                hint.audio_ids.append(tid)
        if hint.video_ids:
            hint.video_qid = hint.video_ids[0]
        return hint

    index = media_dir / INFO_INDEX
    if index.is_file():
        data = read_json(index)
        if data:
            for track in (data.get("video") or []):
                tid = _s(_dig(track, "id"))
                if tid:
                    hint.video_ids.append(tid)
            for track in (data.get("audio") or []):
                tid = _s(_dig(track, "id"))
                if tid:
                    hint.audio_ids.append(tid)
            if hint.video_ids:
                hint.video_qid = hint.video_ids[0]
    return hint


# ---------------------------------------------------------------------------
# 主解析
# ---------------------------------------------------------------------------

_QUALITY_RE = re.compile(r"^\d{2,4}$")


def normalize(raw: dict[str, Any], kind: str, media_dir: Path) -> dict[str, Any]:
    """把任意来源的 info JSON 归一化成统一字段（含字段兜底链）。"""
    page = raw.get("page_data") if isinstance(raw.get("page_data"), dict) else {}
    ep = raw.get("ep") if isinstance(raw.get("ep"), dict) else {}
    dim = raw.get("dimension") if isinstance(raw.get("dimension"), dict) else {}

    group = _s(raw.get("groupTitle")) or _s(raw.get("owner_name"))
    title = _s(_dig(page, "download_subtitle")) or _s(raw.get("title")) or _s(ep.get("title"))
    part = _s(_dig(page, "part"))
    up = _s(raw.get("uname")) or _s(raw.get("owner_name"))
    status = _s(raw.get("status")) or _s(_dig(page, "download_title"))
    item_id = _s(raw.get("itemId")) or _s(raw.get("item_id")) or _s(raw.get("owner_id"))

    cid = _s(_dig(page, "cid")) or _s(raw.get("cid"))
    if not cid and media_dir.name.startswith("c_"):
        cid = media_dir.name[2:]

    qid = _s(raw.get("video_quality")) or _s(raw.get("prefered_video_quality")) or _s(raw.get("quality"))
    if not qid and _QUALITY_RE.match(media_dir.name):
        qid = media_dir.name

    quality = _s(raw.get("quality_pithy_description")) or _s(raw.get("quality_description"))

    width = _i(_dig(page, "width")) or _i(dim.get("width")) or _i(raw.get("width"))
    height = _i(_dig(page, "height")) or _i(dim.get("height")) or _i(raw.get("height"))

    series = _s(raw.get("season_title")) or _s(raw.get("series_title")) or _s(ep.get("long_title"))

    return {
        "kind": kind,
        "title": title,
        "part": part,
        "up": up,
        "group": group,
        "status": status,
        "avid": _s(raw.get("avid")) or _s(raw.get("aid")),
        "bvid": _s(raw.get("bvid")),
        "cid": cid,
        "qid": qid,
        "quality": quality,
        "owner_id": _s(raw.get("owner_id")),
        "owner_name": _s(raw.get("owner_name")),
        "item_id": item_id or "0",
        "group_id": _s(raw.get("groupId")) or _s(raw.get("group_id")),
        "uid": _s(raw.get("uid")),
        "width": width,
        "height": height,
        "duration_ms": _i(raw.get("total_time_milli")),
        "danmaku_count": _i(raw.get("danmaku_count")),
        "cover_url": _s(raw.get("cover")),
        "series_title": series,
        "type_tag": _s(raw.get("type_tag")),
        "media_type": _i(raw.get("media_type")),
        "season_id": _s(raw.get("season_id")),
        "page": _i(_dig(page, "page")),
        "raw": raw,
    }


def is_complete(status: str) -> bool:
    """状态白名单：``completed`` / ``视频已缓存完成`` / 空。"""
    return _s(status) in COMPLETE_STATUS
