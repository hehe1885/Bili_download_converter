"""合并引擎：ffmpeg（默认）与 MP4Box（可选）。

ffmpeg 直接吃 B 站缓存的 fMP4 ``.m4s``，用 ``-c copy`` 无损合并；只有在文件头
不是合法 box（例如老客户端留下的 ``000000000`` 前缀）时才落一份剥离后的临时副本。
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .models import Engine, Options
from .utils import run_command

LOG = logging.getLogger("m4s")

M4S_PREFIX = b"000000000"

_FFMPEG_HINTS = (
    r"C:\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
)
_MP4BOX_HINTS = (
    r"C:\Program Files\GPAC\mp4box.exe",
    r"C:\GPAC\mp4box.exe",
)


@dataclass
class MuxTools:
    ffmpeg: Path | None = None
    ffprobe: Path | None = None
    mp4box: Path | None = None

    @property
    def available(self) -> bool:
        return self.ffmpeg is not None or self.mp4box is not None

    def describe(self) -> str:
        parts = []
        if self.ffmpeg:
            parts.append(f"ffmpeg={self.ffmpeg}")
        if self.mp4box:
            parts.append(f"MP4Box={self.mp4box}")
        return " / ".join(parts) or "未找到任何合并引擎"


@dataclass
class MuxResult:
    ok: bool
    output: Path | None = None
    message: str = ""
    command: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 探测可执行文件
# ---------------------------------------------------------------------------

def _first_existing(candidates: list[str | Path]) -> Path | None:
    for cand in candidates:
        if not cand:
            continue
        p = Path(cand)
        if p.is_file():
            return p
    return None


def find_tools(options: Options | None = None) -> MuxTools:
    opts = options or Options()
    tools = MuxTools()

    here = Path(__file__).resolve().parent.parent
    local_bin = [here / "bin" / "ffmpeg.exe", here / "bin" / "ffmpeg",
                 Path.cwd() / "bin" / "ffmpeg.exe"]

    ffmpeg = _first_existing([opts.ffmpeg_path] if opts.ffmpeg_path else [])
    if ffmpeg is None:
        ffmpeg = _first_existing([*local_bin, *_FFMPEG_HINTS])
    if ffmpeg is None:
        found = shutil.which("ffmpeg")
        ffmpeg = Path(found) if found else None
    tools.ffmpeg = ffmpeg

    if ffmpeg is not None:
        for name in ("ffprobe.exe", "ffprobe"):
            sibling = ffmpeg.with_name(name)
            if sibling.is_file():
                tools.ffprobe = sibling
                break
    if tools.ffprobe is None:
        found = shutil.which("ffprobe")
        tools.ffprobe = Path(found) if found else None

    mp4box = _first_existing([opts.mp4box_path] if opts.mp4box_path else [])
    if mp4box is None:
        mp4box = _first_existing([here / "bin" / "MP4Box.exe", Path.cwd() / "bin" / "MP4Box.exe"])
    if mp4box is None:
        found = shutil.which("MP4Box") or shutil.which("mp4box")
        mp4box = Path(found) if found else None
    tools.mp4box = mp4box
    return tools


def pick_engine(options: Options, tools: MuxTools | None = None) -> Engine:
    """按选项与探测结果决定实际使用哪个引擎。"""
    tools = tools or find_tools(options)
    if options.engine is Engine.FFMPEG:
        return Engine.FFMPEG
    if options.engine is Engine.MP4BOX:
        return Engine.MP4BOX
    if tools.ffmpeg is not None:
        return Engine.FFMPEG
    if tools.mp4box is not None:
        return Engine.MP4BOX
    return Engine.FFMPEG


# ---------------------------------------------------------------------------
# 媒体探测
# ---------------------------------------------------------------------------

def _sniff_handler_type(path: Path, read_size: int = 4 << 20) -> str:
    """纯 Python 兜底：在 moov/trak/mdia/hdlr 里找 handler_type（vide/soun）。"""
    try:
        with open(path, "rb") as fh:
            data = fh.read(read_size)
    except OSError:
        return ""

    def boxes(start: int, end: int):
        pos = start
        while pos + 8 <= end:
            size = int.from_bytes(data[pos:pos + 4], "big")
            btype = data[pos + 4:pos + 8]
            header = 8
            if size == 1:
                if pos + 16 > end:
                    return
                size = int.from_bytes(data[pos + 8:pos + 16], "big")
                header = 16
            elif size == 0:
                size = end - pos
            if size < header or pos + size > end:
                return
            yield btype, pos + header, pos + size
            pos += size

    depth_limit = 6
    stack = [(0, len(data), 0)]
    while stack:
        start, end, depth = stack.pop()
        if depth > depth_limit:
            continue
        for btype, body_start, body_end in boxes(start, end):
            if btype == b"hdlr":
                if body_start + 12 <= body_end:
                    ht = data[body_start + 8:body_start + 12]
                    if ht == b"vide":
                        return "video"
                    if ht == b"soun":
                        return "audio"
            elif btype in (b"moov", b"trak", b"mdia"):
                stack.append((body_start, body_end, depth + 1))
    return ""


def probe_tracks(path: Path, tools: MuxTools | None = None) -> str:
    """返回 ``"video"`` / ``"audio"`` / ``""``。优先纯 Python 嗅探，失败再用 ffprobe。"""
    kind = _sniff_handler_type(path)
    if kind:
        return kind
    tools = tools or find_tools()
    if tools.ffprobe is None:
        return ""
    code, out = run_command([
        str(tools.ffprobe), "-v", "error", "-show_entries", "stream=codec_type",
        "-of", "csv=p=0", str(path),
    ], timeout=30)
    if code != 0:
        return ""
    lines = [ln.strip().lower() for ln in out.splitlines() if ln.strip()]
    if "video" in lines:
        return "video"
    if "audio" in lines:
        return "audio"
    return ""


def probe_video_dims(path: Path, tools: MuxTools | None = None) -> tuple[int, int, int]:
    """返回 ``(宽, 高, 时长毫秒)``，失败返回 ``(0, 0, 0)``。"""
    tools = tools or find_tools()
    if tools.ffprobe is None:
        return 0, 0, 0
    code, out = run_command([
        str(tools.ffprobe), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=0", str(path),
    ], timeout=60)
    if code != 0:
        return 0, 0, 0
    width = height = 0
    duration_ms = 0
    for line in out.splitlines():
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        try:
            if key == "width":
                width = int(float(value))
            elif key == "height":
                height = int(float(value))
            elif key == "duration":
                duration_ms = int(float(value) * 1000)
        except ValueError:
            continue
    return width, height, duration_ms


# ---------------------------------------------------------------------------
# m4s 预处理
# ---------------------------------------------------------------------------

def find_payload_offset(head: bytes) -> int:
    """算出真正的 MP4 数据从第几个字节开始。

    - 合法 box（``size + 'ftyp'``）→ 0
    - 老客户端遗留的 9 个 ASCII ``'0'`` 前缀 → 9
    - 其它情况在头部里找 ``ftyp``，把 box 起点当作数据起点
    """
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return 0
    if head[:9] == M4S_PREFIX:
        return 9
    idx = head.find(b"ftyp")
    if idx >= 4:
        return idx - 4
    return 0


def ensure_playable(src: Path, workdir: Path | None = None) -> tuple[Path, bool]:
    """返回 ``(可用路径, 是否落过剥离副本)``；无需剥离时直接返回原文件，不做拷贝。"""
    try:
        with open(src, "rb") as fh:
            head = fh.read(4096)
    except OSError as exc:
        LOG.warning("读取文件头失败 %s: %s", src, exc)
        return src, False

    offset = find_payload_offset(head)
    if offset <= 0 or offset >= len(head) and offset > 0 and offset > _file_size(src):
        return src, False

    if workdir is None:
        workdir = src.parent
    workdir.mkdir(parents=True, exist_ok=True)
    dest = workdir / f"{src.stem}-stripped{src.suffix}"
    try:
        with open(src, "rb") as fin, open(dest, "wb") as fout:
            fin.seek(offset)
            shutil.copyfileobj(fin, fout, length=1 << 20)
    except OSError as exc:
        LOG.warning("剥离前缀失败 %s: %s", src, exc)
        return src, False
    LOG.debug("剥离 %d 字节前缀: %s -> %s", offset, src.name, dest.name)
    return dest, True


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------

def build_ffmpeg_command(
    ffmpeg: Path,
    video: Path,
    audio: Path,
    output: Path,
    tags: dict[str, str] | None = None,
    cover: Path | None = None,
    container: str = "mp4",
) -> list[str]:
    args = [str(ffmpeg), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-i", str(video), "-i", str(audio)]
    maps = ["-map", "0:v:0", "-map", "1:a:0"]
    if cover is not None:
        args += ["-i", str(cover)]
        maps += ["-map", "2:v:0"]
    args += maps + ["-c", "copy"]
    if cover is not None:
        args += ["-disposition:v:1", "attached_pic"]
    if container.lower() == "mp4":
        args += ["-movflags", "+faststart"]
    for key, value in (tags or {}).items():
        if value:
            args += ["-metadata", f"{key}={value}"]
    args.append(str(output))
    return args


def build_mp4box_command(
    mp4box: Path,
    video: Path,
    audio: Path,
    output: Path,
    tags: dict[str, str] | None = None,
    overlay: bool = False,
    item_id: str = "",
) -> list[str]:
    args = [str(mp4box)]
    if overlay:
        args.append("-force")
    args += ["-charset", "utf8"]
    if tags:
        tag_str = ":".join(f"{k}={v}" for k, v in tags.items() if v)
        if tag_str:
            args += ["-tags", tag_str]
    if item_id:
        args += ["-cprt", str(item_id)]
    args += ["-add", f"{video}#video", "-add", f"{audio}#audio", "-new", str(output)]
    return args


def mux(
    video: Path,
    audio: Path,
    output: Path,
    options: Options,
    tags: dict[str, str] | None = None,
    cover: Path | None = None,
    tools: MuxTools | None = None,
    engine: Engine | None = None,
    log_cb=None,
) -> MuxResult:
    """把音视频合并到 ``output``。"""
    tools = tools or find_tools(options)
    engine = engine or pick_engine(options, tools)
    output.parent.mkdir(parents=True, exist_ok=True)

    if engine is Engine.MP4BOX and tools.mp4box is not None:
        cmd = build_mp4box_command(tools.mp4box, video, audio, output, tags,
                                   overlay=True, item_id=(tags or {}).get("album", ""))
        code, out = run_command(cmd, timeout=3600, log_cb=log_cb)
        if code == 0 and output.is_file() and output.stat().st_size > 0:
            return MuxResult(True, output, "MP4Box 合并成功", cmd)
        LOG.warning("MP4Box 合并失败(code=%s)，回退 ffmpeg: %s", code, out.strip()[:400])

    if tools.ffmpeg is None:
        return MuxResult(False, None, "未找到可用的合并引擎（ffmpeg / MP4Box）")

    cmd = build_ffmpeg_command(tools.ffmpeg, video, audio, output, tags, cover,
                               options.container)
    code, out = run_command(cmd, timeout=3600, log_cb=log_cb)
    if code != 0 or not output.is_file() or output.stat().st_size == 0:
        # 封面嵌入失败时降级重试一次（去掉封面）
        if cover is not None:
            LOG.warning("带封面的合并失败，去掉封面重试: %s", out.strip()[:300])
            cmd = build_ffmpeg_command(tools.ffmpeg, video, audio, output, tags, None,
                                      options.container)
            code, out = run_command(cmd, timeout=3600, log_cb=log_cb)
        if code != 0 or not output.is_file() or output.stat().st_size == 0:
            return MuxResult(False, None, f"ffmpeg 合并失败(code={code}): {out.strip()[:500]}", cmd)
    return MuxResult(True, output, "ffmpeg 合并成功", cmd)


def read_mp4_tags(path: Path, tools: MuxTools | None = None) -> dict[str, str]:
    """读取容器元数据，用于增量去重判断。"""
    tools = tools or find_tools()
    if tools.ffprobe is None:
        return {}
    code, out = run_command([
        str(tools.ffprobe), "-v", "error", "-show_entries",
        "format_tags=title,artist,album,comment", "-of", "default=noprint_wrappers=1",
        str(path),
    ], timeout=60)
    if code != 0:
        return {}
    tags: dict[str, str] = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        key = key.strip().lower()
        if key.startswith("tag:"):
            key = key[4:]
        if key:
            tags[key] = value.strip()
    return tags


def check_tools(options: Options) -> tuple[bool, str]:
    tools = find_tools(options)
    return tools.available, tools.describe()


__all__ = [
    "MuxTools", "MuxResult", "find_tools", "pick_engine", "probe_tracks",
    "probe_video_dims", "ensure_playable", "find_payload_offset", "mux",
    "read_mp4_tags", "build_ffmpeg_command", "build_mp4box_command", "check_tools",
    "M4S_PREFIX",
]
