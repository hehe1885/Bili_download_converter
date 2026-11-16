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


def find_danmaku_pb(media_dir: Path) -> list[Path]:
    """查找 PC 端 ``dm1``/``dm2``… 或移动端 ``danmaku.pb`` 等 Protobuf 弹幕文件。"""
    for directory in (media_dir, media_dir.parent):
        out: list[ tuple[int, Path] ] = []
        try:
            for p in directory.iterdir():
                if not p.is_file():
                    continue
                low = p.name.lower()
                if low == "danmaku.pb" and p.stat().st_size > 0:
                    out.append((0, p))
                elif low.startswith("dm") and low[2:].isdigit() and p.stat().st_size > 0:
                    out.append((int(low[2:]), p))
        except OSError:
            continue
        if out:
            out.sort(key=lambda item: (item[0], item[1].name))
            return [p for _, p in out]
    return []


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


def find_cover(media_dir: Path, cover_url: str, raw: dict[str, Any] | None = None) -> Path | None:
    if isinstance(raw, dict):
        for key in ("coverPath", "groupCoverPath"):
            val = _s(raw.get(key))
            if val:
                p = Path(val)
                if p.is_file():
                    return p
    for name in ("cover.jpg", "cover.png", "cover.webp", "image.jpg", "image.png", "group.jpg"):
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

_URL_TRACK_RE = re.compile(r"-(\d+)\.m4s(?:$|\?)", re.IGNORECASE)


@dataclass
class TrackHint:
    """从 index.json / .playurl 提取的轨道 id，用于把 m4s 文件配成对。"""

    video_ids: list[str] = field(default_factory=list)
    audio_ids: list[str] = field(default_factory=list)
    video_qid: str = ""

    @property
    def empty(self) -> bool:
        return not self.video_ids and not self.audio_ids


def _append_unique(target: list[str], value: str) -> None:
    if value and value not in target:
        target.append(value)


def _extract_track_ids(track: Any, target: list[str]) -> None:
    if not isinstance(track, dict):
        return
    tid = _s(track.get("id"))
    if tid:
        _append_unique(target, tid)
        if tid.isdigit() and int(tid) < 30000:
            # B 站 PC / TV 端将 dash.video[].id（如 80/112/116）加 30000 作为文件名后缀（30080/30112/30116）
            _append_unique(target, str(30000 + int(tid)))
    for url_key in ("baseUrl", "base_url"):
        url = _s(track.get(url_key))
        if url:
            m = _URL_TRACK_RE.search(url)
            if m:
                _append_unique(target, m.group(1))


def _collect_dash_hint(dash: Any, hint: TrackHint) -> None:
    if not isinstance(dash, dict):
        return
    for track in (dash.get("video") or []):
        _extract_track_ids(track, hint.video_ids)
    for track in (dash.get("audio") or []):
        _extract_track_ids(track, hint.audio_ids)
    dolby = dash.get("dolby")
    if isinstance(dolby, dict):
        for track in (dolby.get("audio") or []):
            _extract_track_ids(track, hint.audio_ids)
    flac = dash.get("flac")
    if isinstance(flac, dict) and isinstance(flac.get("audio"), dict):
        _extract_track_ids(flac.get("audio"), hint.audio_ids)
    if hint.video_ids and not hint.video_qid:
        hint.video_qid = hint.video_ids[0]


def _find_playurl_file(media_dir: Path, info_path: Path | None = None, info_kind: str = "") -> Path | None:
    if info_kind == INFO_PLAYURL and info_path is not None and info_path.is_file():
        return info_path
    for level in (media_dir, media_dir.parent):
        for cand in _playurl_candidates(level):
            if cand.is_file():
                return cand
    return None


def parse_track_hint(info_path: Path | None, info_kind: str, media_dir: Path) -> TrackHint:
    hint = TrackHint()
    playurl_path = _find_playurl_file(media_dir, info_path, info_kind)
    if playurl_path is not None:
        data = read_json(playurl_path)
        if data:
            dash = (
                _dig(data, "data", "dash")
                or _dig(data, "result", "dash")
                or data.get("dash")
                or {}
            )
            _collect_dash_hint(dash, hint)

    if info_path is not None and info_kind != INFO_PLAYURL and info_path.is_file():
        data = read_json(info_path)
        if data:
            dash = (
                _dig(data, "data", "dash")
                or _dig(data, "result", "dash")
                or data.get("dash")
                or {}
            )
            _collect_dash_hint(dash, hint)

    index = media_dir / INFO_INDEX
    if index.is_file():
        data = read_json(index)
        if data:
            _collect_dash_hint(data, hint)
    return hint


# ---------------------------------------------------------------------------
# 主解析
# ---------------------------------------------------------------------------

_QUALITY_RE = re.compile(r"^\d{2,4}$")

_QN_LABELS: dict[str, str] = {
    "127": "8K",
    "126": "杜比视界",
    "125": "HDR",
    "120": "4K",
    "116": "1080P60",
    "112": "1080P+",
    "80": "1080P",
    "74": "720P60",
    "64": "720P",
    "32": "480P",
    "16": "360P",
    "6": "240P",
}


def normalize(raw: dict[str, Any], kind: str, media_dir: Path) -> dict[str, Any]:
    """把任意来源的 info JSON 归一化成统一字段（含字段兜底链）。"""
    page = raw.get("page_data") if isinstance(raw.get("page_data"), dict) else {}
    ep = raw.get("ep") if isinstance(raw.get("ep"), dict) else {}
    dim = raw.get("dimension") if isinstance(raw.get("dimension"), dict) else {}

    group = _s(raw.get("groupTitle")) or _s(raw.get("owner_name"))
    title = _s(_dig(page, "download_subtitle")) or _s(raw.get("title")) or _s(ep.get("title"))
    tab_name = _s(raw.get("tabName"))
    part = _s(_dig(page, "part")) or (tab_name if tab_name and tab_name != "正片" else "")
    up = _s(raw.get("uname")) or _s(raw.get("owner_name"))
    status = _s(raw.get("status")) or _s(_dig(page, "download_title"))
    item_id = _s(raw.get("itemId")) or _s(raw.get("item_id")) or _s(raw.get("owner_id"))

    cid = _s(_dig(page, "cid")) or _s(raw.get("cid"))
    if not cid and media_dir.name.startswith("c_"):
        cid = media_dir.name[2:]
    elif not cid and media_dir.name.isdigit():
        cid = media_dir.name

    qid = (
        _s(raw.get("video_quality"))
        or _s(raw.get("prefered_video_quality"))
        or _s(raw.get("quality"))
        or _s(raw.get("qn"))
    )
    if not qid and _QUALITY_RE.match(media_dir.name):
        qid = media_dir.name

    quality = _s(raw.get("quality_pithy_description")) or _s(raw.get("quality_description"))

    width = _i(_dig(page, "width")) or _i(dim.get("width")) or _i(raw.get("width"))
    height = _i(_dig(page, "height")) or _i(dim.get("height")) or _i(raw.get("height"))
    duration_ms = _i(raw.get("total_time_milli"))

    # PC 端补充：从同目录 .playurl 补齐画质描述、宽高、毫秒时长
    playurl_path = _find_playurl_file(media_dir)
    if playurl_path is not None:
        play_raw = read_json(playurl_path) or {}
        play_data = play_raw.get("data") if isinstance(play_raw.get("data"), dict) else (
            play_raw.get("result") if isinstance(play_raw.get("result"), dict) else play_raw
        )
        if isinstance(play_data, dict):
            if not qid:
                qid = _s(play_data.get("quality"))
            if not duration_ms:
                duration_ms = _i(play_data.get("timelength")) or (_i(_dig(play_data, "dash", "duration")) * 1000)
            if not quality and qid:
                for fmt in (play_data.get("support_formats") or []):
                    if isinstance(fmt, dict) and _s(fmt.get("quality")) == qid:
                        disp = _s(fmt.get("display_desc"))
                        sup = _s(fmt.get("superscript"))
                        if disp and sup and sup not in disp:
                            quality = f"{disp} {sup}" if not sup.isascii() else f"{disp}{sup}"
                        else:
                            quality = disp or _s(fmt.get("new_description"))
                        break
            if not (width and height):
                dash_videos = _dig(play_data, "dash", "video") or []
                if isinstance(dash_videos, list):
                    for vtrack in dash_videos:
                        if isinstance(vtrack, dict) and (not qid or _s(vtrack.get("id")) == qid):
                            width = width or _i(vtrack.get("width"))
                            height = height or _i(vtrack.get("height"))
                            if width and height:
                                break

    if not duration_ms:
        duration_ms = _i(raw.get("duration")) * 1000

    if not quality and qid in _QN_LABELS:
        quality = _QN_LABELS[qid]

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
        "duration_ms": duration_ms,
        "danmaku_count": _i(raw.get("danmaku_count")) or _i(raw.get("danmaku")),
        "cover_url": _s(raw.get("cover")) or _s(raw.get("coverUrl")) or _s(raw.get("groupCoverUrl")),
        "series_title": series,
        "type_tag": _s(raw.get("type_tag")),
        "media_type": _i(raw.get("media_type")),
        "season_id": _s(raw.get("season_id")),
        "page": _i(_dig(page, "page")) or _i(raw.get("p")),
        "raw": raw,
    }


def is_complete(status: str) -> bool:
    """状态白名单：``completed`` / ``视频已缓存完成`` / 空。"""
    return _s(status) in COMPLETE_STATUS
