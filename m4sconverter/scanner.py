"""缓存目录扫描与结构探测。

不写死缓存目录结构，而是按「哪些目录里有 m4s」自底向上探测元数据文件，
于是同时兼容：PC 旧客户端（``.playurl``）、手机/新版客户端（``entry.json``）、
番剧/TV 端，以及直接把单集目录当导入目录。
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import metadata as meta
from .models import ItemState, Options, ScanReport, VideoItem
from .utils import human_size

LOG = logging.getLogger("m4s")

M4S_SUFFIX = ".m4s"

#: 一层目录里超过这个数量的 m4s 才值得用 ffprobe 兜底判型。
_PROBE_MIN_FILES = 2

#: 画质目录名形如 64 / 80 / 112 / 30080。
_QUALITY_DIR_RE = re.compile(r"^\d{2,5}$")

_INT_FIELDS = frozenset({"width", "height", "duration_ms", "danmaku_count"})

_STR_FIELDS = (
    "title", "part", "up", "group", "avid", "bvid", "cid", "qid", "quality",
    "status", "owner_id", "item_id", "group_id", "uid", "cover_url", "series_title",
)


@dataclass
class _ScanCtx:
    root: Path
    excluded: list[Path]
    scanned_dirs: int = 0


def _is_excluded(path: Path, excluded: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for ex in excluded:
        try:
            resolved.relative_to(ex)
            return True
        except ValueError:
            continue
    return False


# ---------------------------------------------------------------------------
# 音视频配对
# ---------------------------------------------------------------------------

def _classify_by_name(files: list[Path]) -> tuple[Path | None, Path | None]:
    video = audio = None
    for p in files:
        low = p.name.lower()
        if video is None and "video" in low:
            video = p
        elif audio is None and ("audio" in low or "sound" in low):
            audio = p
    return video, audio


def _classify_by_hint(files: list[Path], hint: meta.TrackHint) -> tuple[Path | None, Path | None]:
    """按 ``-<id>.m4s`` 的文件名匹配 index.json / .playurl 里的轨道 id。"""
    if hint.empty:
        return None, None
    video = audio = None
    for p in files:
        if video is None:
            for vid in hint.video_ids:
                if p.name.endswith(f"-{vid}{M4S_SUFFIX}") or f"-{vid}." in p.name:
                    video = p
                    break
        if audio is None:
            for aid in hint.audio_ids:
                if p.name.endswith(f"-{aid}{M4S_SUFFIX}") or f"-{aid}." in p.name:
                    audio = p
                    break
    return video, audio


def _classify_by_probe(files: list[Path]) -> tuple[Path | None, Path | None]:
    """名字完全无线索时，用 ffprobe 判 codec_type；失败则按体积猜（大的是视频）。"""
    from .mux import probe_tracks  # 延迟导入，避免循环

    video = audio = None
    for p in files[:4]:
        kind = probe_tracks(p)
        if kind == "video" and video is None:
            video = p
        elif kind == "audio" and audio is None:
            audio = p
    if video is None and audio is None and len(files) == 2:
        sized = sorted(files, key=lambda x: _safe_size(x), reverse=True)
        video, audio = sized[0], sized[1]
    return video, audio


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def pair_m4s(
    files: list[Path],
    hint: meta.TrackHint,
    allow_probe: bool = True,
) -> tuple[Path | None, Path | None, str]:
    """把一层目录里的 m4s 配成 ``(视频, 音频, 判定方式)``。"""
    if not files:
        return None, None, ""

    # 1) 字面 video.m4s / audio.m4s（新版客户端）
    exact: dict[str, Path] = {p.name.lower(): p for p in files}
    if "video.m4s" in exact and "audio.m4s" in exact:
        return exact["video.m4s"], exact["audio.m4s"], "exact"

    # 2) 轨道 id 匹配（PC 端 <cid>-1-<id>.m4s）
    video, audio = _classify_by_hint(files, hint)
    if video is not None and audio is not None:
        return video, audio, "hint"

    # 3) 文件名里带 video/audio 字样
    by_name_v, by_name_a = _classify_by_name(files)
    if by_name_v is not None and by_name_a is not None and by_name_v != by_name_a:
        return by_name_v, by_name_a, "name"

    if video is not None or audio is not None:
        v2 = video or by_name_v
        a2 = audio or by_name_a
        if v2 is not None and a2 is not None and v2 != a2:
            return v2, a2, "mixed"

    # 4) ffprobe / 体积兜底
    if allow_probe and len(files) >= _PROBE_MIN_FILES:
        pv, pa = _classify_by_probe(files)
        if pv is not None and pa is not None and pv != pa:
            return pv, pa, "probe"

    return None, None, ""


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------

def _dir_m4s(directory: Path) -> list[Path]:
    out: list[Path] = []
    try:
        with os.scandir(directory) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(M4S_SUFFIX):
                        out.append(Path(entry.path))
                except OSError:
                    continue
    except OSError as exc:
        LOG.debug("无法读取目录 %s: %s", directory, exc)
    return out


def _iter_dirs(root: Path, ctx: _ScanCtx):
    stack = [root]
    seen: set[str] = set()
    while stack:
        cur = stack.pop()
        key = str(cur)
        if key in seen:
            continue
        seen.add(key)
        ctx.scanned_dirs += 1
        yield cur
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        child = Path(entry.path)
                        if _is_excluded(child, ctx.excluded):
                            continue
                        stack.append(child)
                    except OSError:
                        continue
        except OSError as exc:
            LOG.debug("无法遍历 %s: %s", cur, exc)


def build_item(media_dir: Path, root: Path, video: Path, audio: Path, index: int) -> VideoItem:
    info_path, info_kind = meta.find_info_file(media_dir, root)
    raw = meta.read_json(info_path) if info_path else None
    fields = meta.normalize(raw or {}, info_kind, media_dir)

    item = VideoItem(media_dir=media_dir, video_src=video, audio_src=audio,
                     info_path=info_path, info_kind=info_kind, index=index)
    for key in _STR_FIELDS:
        setattr(item, key, fields.get(key) or "")
    for key in _INT_FIELDS:
        setattr(item, key, int(fields.get(key) or 0))
    item.info_raw = fields.get("raw") or {}

    if not item.qid and _QUALITY_DIR_RE.match(media_dir.name):
        item.qid = media_dir.name
    if not item.quality:
        item.quality = item.qid

    item.danmaku_xml = meta.find_danmaku_xml(media_dir, item.cid)
    item.existing_ass = meta.find_existing_ass(media_dir, item.cid)
    item.cover_local = meta.find_cover(media_dir, item.cover_url)

    if item.info_path is None:
        item.message = "未找到元数据文件（entry.json / .videoInfo / .playurl）"
    return item


def scan(root: str | Path, options: Options | None = None,
         extra_exclude: list[Path] | None = None) -> ScanReport:
    """扫描导入目录，返回可转换单元列表。"""
    started = time.time()
    root_path = Path(root).expanduser()
    report = ScanReport()

    if not root_path.exists():
        report.warnings.append(f"导入目录不存在: {root_path}")
        return report
    if not root_path.is_dir():
        report.warnings.append(f"导入目录不是文件夹: {root_path}")
        return report

    excluded: list[Path] = []
    if options and options.output_dir:
        excluded.append(Path(options.output_dir).expanduser().resolve())
    default_out = root_path / "output"
    if default_out.is_dir():
        excluded.append(default_out.resolve())
    for extra in extra_exclude or []:
        excluded.append(Path(extra).resolve())

    ctx = _ScanCtx(root=root_path, excluded=excluded)
    LOG.info("开始扫描: %s", root_path)

    pairs_found = 0
    skipped_unfinished = 0
    orphan_dirs: list[str] = []

    for directory in _iter_dirs(root_path, ctx):
        if _is_excluded(directory, excluded):
            continue
        m4s = _dir_m4s(directory)
        if not m4s:
            continue

        # 先定位元数据，再用它的轨道 id 辅助配对
        info_path, info_kind = meta.find_info_file(directory, root_path)
        hint = meta.parse_track_hint(info_path, info_kind, directory)

        video, audio, how = pair_m4s(m4s, hint)
        if video is None or audio is None:
            if len(m4s) == 1:
                orphan_dirs.append(str(directory))
                LOG.debug("只有单个 m4s，跳过: %s", directory)
            else:
                orphan_dirs.append(str(directory))
                LOG.warning("无法配对音视频（%d 个 m4s）: %s", len(m4s), directory)
            continue

        item = build_item(directory, root_path, video, audio, len(report.items) + 1)
        item.info_raw.setdefault("_pair_method", how)

        if options and options.skip_unfinished and not meta.is_complete(item.status):
            skipped_unfinished += 1
            item.state = ItemState.SKIPPED
            item.message = f"未缓存完成({item.status or '空状态'})"
            LOG.info("跳过未缓存完成: %s", item.primary_title())
            report.items.append(item)
            continue

        pairs_found += 1
        report.items.append(item)

    report.items.sort(key=lambda it: str(it.media_dir).lower())
    for i, item in enumerate(report.items, 1):
        item.index = i

    report.elapsed = time.time() - started
    LOG.info(
        "扫描完成: %d 个可转换单元 / 跳过未完成 %d / 遍历 %d 个目录 / 耗时 %.2fs",
        pairs_found, skipped_unfinished, ctx.scanned_dirs, report.elapsed,
    )
    if orphan_dirs:
        report.warnings.append(f"{len(orphan_dirs)} 个目录里的 m4s 无法配对成音视频")
    return report


def describe(report: ScanReport, limit: int = 20) -> str:
    """生成人类可读的扫描摘要。"""
    lines = [
        f"扫描到 {len(report.items)} 个单元（耗时 {report.elapsed:.2f}s）",
    ]
    for item in report.items[:limit]:
        size = _safe_size(item.video_src) + _safe_size(item.audio_src) if item.video_src else 0
        q = f" [{item.quality}]" if item.quality else ""
        lines.append(
            f"  {item.index:>3}. {item.primary_title()[:48]}{q}  "
            f"{human_size(size)}  {item.media_dir}"
        )
    if len(report.items) > limit:
        lines.append(f"  ... 还有 {len(report.items) - limit} 个")
    for warn in report.warnings:
        lines.append(f"  [!] {warn}")
    return "\n".join(lines)
