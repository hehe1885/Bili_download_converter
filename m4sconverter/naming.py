"""输出路径渲染：布局模式、命名模板、文件名清洗、同名冲突处理。"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from pathlib import Path
from typing import Callable

from .models import ConflictMode, LayoutMode, NameFilter, Options, VideoItem
from .utils import replace_special, safe_join, sanitize_name, unique_path

LOG = logging.getLogger("m4s")

#: 模板可用占位符。
TEMPLATE_FIELDS: dict[str, str] = {
    "title": "视频标题（download_subtitle → title → 分P名）",
    "part": "分P名",
    "up": "UP 主",
    "group": "合集 / 分组名",
    "series": "番剧 / 系列名",
    "avid": "AV 号",
    "bvid": "BV 号",
    "cid": "CID",
    "qid": "画质代码（64/80/112）",
    "quality": "画质描述（1080P）",
    "date": "转换日期 YYYY-MM-DD",
    "time": "转换时间 HHMMSS",
    "index": "任务序号（可用 {index:03d}）",
    "danmaku": "弹幕条数",
    "w": "视频宽度",
    "h": "视频高度",
    "duration": "时长（秒）",
    "original": "缓存目录名",
    "ext": "扩展名（不含点）",
}

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)((?::[^{}]*)?)\}")


# ---------------------------------------------------------------------------
# 占位符上下文
# ---------------------------------------------------------------------------

def build_context(
    item: VideoItem,
    ext: str,
    now: _dt.datetime | None = None,
    mode: str = "go",
) -> dict[str, object]:
    """构造模板变量表。

    **所有字符串字段在进入上下文前就已清洗**——这一步很关键：渲染结果里的
    ``/`` 此后只可能来自模板本身（用户显式写的目录层级），视频标题里的
    ``/``、``\\`` 绝不可能被误当成目录分隔符拆出嵌套目录。
    """
    now = now or _dt.datetime.now()
    raw: dict[str, object] = {
        "title": item.primary_title(),
        "part": item.part_title(),
        "up": item.up or item.owner_id or "",
        "group": item.group or "",
        "series": item.series_title or "",
        "avid": item.avid,
        "bvid": item.bvid,
        "cid": item.cid,
        "qid": item.qid,
        "quality": item.quality,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H%M%S"),
        "index": item.index,
        "danmaku": item.danmaku_count,
        "w": item.width,
        "h": item.height,
        "duration": int(item.duration_s),
        "original": item.media_dir.name,
        "ext": ext,
    }
    return {
        key: (sanitize_name(value, mode) if isinstance(value, str) else value)
        for key, value in raw.items()
    }


def render_template(template: str, ctx: dict[str, object]) -> tuple[str, list[str]]:
    """渲染模板，返回 ``(结果, 未识别占位符列表)``。未知占位符替换为空串。"""
    unknown: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        name, spec = match.group(1), match.group(2)
        if name not in ctx:
            if name not in unknown:
                unknown.append(name)
            return ""
        value = ctx[name]
        if spec:
            fmt_spec = spec[1:]
            if fmt_spec:
                try:
                    return format(value, fmt_spec)
                except (ValueError, TypeError):
                    return str(value)
        return str(value)

    return _PLACEHOLDER_RE.sub(_sub, template), unknown


# ---------------------------------------------------------------------------
# 布局
# ---------------------------------------------------------------------------

def layout_template(layout: LayoutMode, custom: str) -> str:
    """把布局模式翻译成路径模板。"""
    if layout is LayoutMode.ORIGINAL:
        return "{group}-{up}/{part}.{ext}"
    if layout is LayoutMode.TITLE_DIR:
        return "{title}/{title}.{ext}"
    if layout is LayoutMode.UP_DIR:
        return "{up}/{title}.{ext}"
    if layout is LayoutMode.TEMPLATE:
        return custom or "{title}.{ext}"
    return "{title}.{ext}"  # FLAT


def _ensure_extension(rendered: str, ext: str) -> str:
    """模板没写扩展名时补上。"""
    tail = rendered.rsplit("/", 1)[-1]
    if "." not in tail:
        return f"{rendered}.{ext}"
    return rendered


def resolve_output_path(
    item: VideoItem,
    options: Options,
    output_root: Path,
    now: _dt.datetime | None = None,
) -> tuple[Path, list[str]]:
    """计算目标路径（不做同名冲突处理）。返回 ``(绝对路径, 警告列表)``。"""
    ext = "mkv" if (options.container or "mp4").lower() == "mkv" else "mp4"
    mode = options.name_filter.value if isinstance(options.name_filter, NameFilter) else str(options.name_filter)
    ctx = build_context(item, ext, now, mode)
    template = layout_template(options.layout, options.template)
    rendered, unknown = render_template(template, ctx)
    rendered = _ensure_extension(rendered, ext)

    warnings: list[str] = []
    if unknown:
        warnings.append(f"模板中的未知占位符已置空: {', '.join('{'+u+'}' for u in unknown)}")

    # 此处 rendered 里的 "/" 只可能来自模板（数据字段已在 build_context 清洗过），
    # 因此可以安全地按层级拆目录；再逐段清洗一次作为兜底。
    parts = [p for p in re.split(r"[\\/]+", rendered) if p and p not in (".", "..")]
    if not parts:
        parts = [f"untitled.{ext}"]
    cleaned: list[str] = []
    for seg in parts:
        seg = sanitize_name(seg, mode)
        if seg:
            cleaned.append(seg)
    if not cleaned:
        cleaned = [f"untitled.{ext}"]
    if not cleaned[-1].lower().endswith(f".{ext}"):
        cleaned[-1] = f"{cleaned[-1]}.{ext}"

    try:
        path = safe_join(output_root, "/".join(cleaned))
    except ValueError as exc:
        warnings.append(str(exc))
        path = output_root / cleaned[-1]
    return path, warnings


def preview_path(item: VideoItem, options: Options, output_root: Path) -> str:
    """给 GUI / CLI 做实时预览。"""
    path, _ = resolve_output_path(item, options, output_root)
    return str(path)


# ---------------------------------------------------------------------------
# 冲突处理
# ---------------------------------------------------------------------------

def apply_conflict(
    path: Path,
    mode: ConflictMode,
    ask: Callable[[Path], bool] | None = None,
) -> Path | None:
    """按冲突策略决定最终写入路径；返回 ``None`` 表示跳过该项。

    ``ConflictMode.ASK`` 时用 ``ask(path)`` 询问，返回 True 表示覆盖。
    """
    if not path.exists():
        return path
    if mode is ConflictMode.OVERWRITE:
        return path
    if mode is ConflictMode.RENAME:
        return unique_path(path)
    if mode is ConflictMode.ASK and ask is not None:
        return path if ask(path) else unique_path(path)
    return None


def output_root_for(import_root: Path, options: Options) -> Path:
    """确定输出根目录。空值 = ``<导入目录>/output``。"""
    if options.output_dir.strip():
        base = Path(options.output_dir).expanduser()
        if not options.output_per_import:
            return base
        sub = sanitize_name(import_root.name, "safe")
        return (base / sub) if sub else base
    return import_root / "output"


__all__ = [
    "TEMPLATE_FIELDS",
    "build_context",
    "render_template",
    "layout_template",
    "resolve_output_path",
    "preview_path",
    "apply_conflict",
    "output_root_for",
    "replace_special",
]
