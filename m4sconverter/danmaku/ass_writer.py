"""ASS 弹幕生成。

头部模板、四个 Style（Roll / Top / Bottom / SC）、滚动与顶底弹幕的坐标公式、
轨道防重叠、密度限制、颜色 ``&HBBGGRR``、时间戳 ``H:MM:SS.cc`` 均为本版
自行实现，输出格式稳定，主流播放器（mpv / PotPlayer / VLC / 小丸工具箱）通用。
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

LOG = logging.getLogger("m4s")


class BulletType(enum.IntEnum):
    NONE = 0      # 高级弹幕（本版丢弃）
    ROLL = 1
    TOP = 2
    BOTTOM = 3
    SC = 4        # 超级聊天


#: B 站弹幕 ``p`` 字段第 2 项 → 内部类型。
_MODE_MAP = {1: BulletType.ROLL, 6: BulletType.ROLL, 5: BulletType.TOP,
             4: BulletType.BOTTOM, 7: BulletType.NONE}

#: 类型过滤器用的字符代号。
_TYPE_CODE = {
    "r": BulletType.ROLL,
    "t": BulletType.TOP,
    "b": BulletType.BOTTOM,
    "s": BulletType.SC,
}


# ---------------------------------------------------------------------------
# 数据与配置
# ---------------------------------------------------------------------------

@dataclass
class Bullet:
    """一条弹幕。"""

    time_ms: int
    type: BulletType
    rgb: int                 # 0xRRGGBB
    text: str
    length: int = 0

    def __post_init__(self) -> None:
        if not self.length:
            self.length = len(self.text)

    def ass_color(self) -> str:
        """``&HBBGGRR``（无 alpha），ASS 标准的颜色写法。"""
        return "&H{:02X}{:02X}{:02X}".format(self.rgb & 0xFF, (self.rgb >> 8) & 0xFF,
                                             (self.rgb >> 16) & 0xFF)


@dataclass
class AssConfig:
    """ASS 参数（默认值按中文弹幕的常见观感给定）。"""

    font_name: str = "黑体"
    font_size: int = 26
    alpha: float = 0.3
    bold: bool = True
    outline: int = 0
    shadow: int = 1
    outline_rgb: str = "0x49516A"
    outline_alpha: float = 0.1
    roll_time: float = 15.0
    fix_time: float = 5.0
    time_shift: float = 0.0
    width: int = 1920
    height: int = 1080
    roll_range: float = 1.0
    fixed_range: float = 1.0
    spacing: int = 0
    density: int = 0
    overlay: bool = False
    convert: str = "s -> r"
    block_keywords: list[str] = field(default_factory=list)

    # 运行期派生（毫秒）
    roll_speed_ms: int = field(init=False, default=15000)
    fix_time_ms: int = field(init=False, default=5000)

    def __post_init__(self) -> None:
        self.roll_speed_ms = int(round(self.roll_time * 1000))
        self.fix_time_ms = int(round(self.fix_time * 1000))


def parse_argb(alpha: float, rgb: str) -> int:
    """``0xRRGGBB`` + 透明度 → ARGB：``(int(alpha*255) << 24) | rgb``。"""
    text = rgb.strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    try:
        value = int(text, 16)
    except (TypeError, ValueError):
        return 0
    a = int(alpha * 255) & 0xFF
    return ((a << 24) | (value & 0xFFFFFF)) & 0xFFFFFFFF


def _ass_hex_with_alpha(argb: int) -> str:
    """``&HAABBGGRR``。"""
    a = (argb >> 24) & 0xFF
    r = (argb >> 16) & 0xFF
    g = (argb >> 8) & 0xFF
    b = argb & 0xFF
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def time_to_string(ms: int) -> str:
    """毫秒 → ASS 时间戳 ``H:MM:SS.cc``，百分秒**截断**、不四舍五入。"""
    if ms < 0:
        ms = 0
    cs = (ms % 1000) // 10
    total = ms // 1000
    s = total % 60
    total //= 60
    m = total % 60
    h = total // 60
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


# ---------------------------------------------------------------------------
# 轨道
# ---------------------------------------------------------------------------

class _RollTrack:
    """滚动弹幕轨道表（对应 Go 的 ``rollTrack``）。"""

    def __init__(self, cfg: AssConfig) -> None:
        fh = cfg.font_size + cfg.spacing
        h = cfg.height * cfg.roll_range + cfg.spacing
        count = int(h / fh) if fh > 0 else 0
        self.time_point: list[float] = [0.0] * max(count, 0)
        self.bullet_len: list[float] = [0.0] * max(count, 0)
        self._cfg = cfg

    def find(self, bullet: Bullet) -> tuple[int, bool]:
        cfg = self._cfg
        bullet_len = cfg.font_size * bullet.length
        time_point = bullet.time_ms
        speed = cfg.roll_speed_ms
        width = cfg.width
        tolerate = 0
        longest = 0.0

        for i in range(len(self.time_point)):
            prev_t = self.time_point[i]
            if prev_t == 0:
                self.time_point[i] = time_point
                self.bullet_len[i] = bullet_len
                return i, True
            t = time_point - prev_t
            v = (self.bullet_len[i] + width) / speed if speed else 0.0
            if v * t < self.bullet_len[i]:
                continue
            if bullet_len <= self.bullet_len[i]:
                self.time_point[i] = time_point
                self.bullet_len[i] = bullet_len
                return i, True
            threshold = speed - width * speed / (width + bullet_len)
            if t > threshold:
                self.time_point[i] = time_point
                self.bullet_len[i] = bullet_len
                return i, True
            if cfg.overlay and t > longest:
                longest = t
                tolerate = i
        return tolerate, cfg.overlay


class _FixedTrack:
    """顶部/底部弹幕轨道表（对应 Go 的 ``fixedTrack``，存上一次占用的时间）。"""

    def __init__(self, cfg: AssConfig) -> None:
        fh = cfg.font_size + cfg.spacing
        h = cfg.height * cfg.fixed_range + cfg.spacing
        count = int(h / fh) if fh > 0 else 0
        self.slots: list[int] = [0] * max(count, 0)
        self._cfg = cfg

    def find(self, bullet: Bullet) -> tuple[int, bool]:
        cfg = self._cfg
        time_point = bullet.time_ms
        for i, prev in enumerate(self.slots):
            if prev == 0 or (time_point - prev) > cfg.fix_time_ms:
                self.slots[i] = time_point
                return i, True
        return 0, cfg.overlay


class _DensityQualifier:
    """对应 Go 的 ``densityQualifier``（``split = 5``，只作用于滚动弹幕）。"""

    SPLIT = 5

    def __init__(self, limit: int, time_slice_ms: int) -> None:
        self.limit = limit // self.SPLIT
        self.time_slice = time_slice_ms // self.SPLIT if self.SPLIT else 0
        self.point = 0
        self.density = 0

    def check(self, bullet: Bullet) -> bool:
        if self.limit == 0:
            return True
        if self.point + self.time_slice <= bullet.time_ms:
            self.point = bullet.time_ms
            self.density = 1
            return True
        if self.density >= self.limit:
            return False
        self.density += 1
        return True


# ---------------------------------------------------------------------------
# 过滤器
# ---------------------------------------------------------------------------

def parse_convert_rule(rule: str) -> dict[BulletType, BulletType | None]:
    """解析 ``"s -> r"`` 形式的类型转换规则（对齐 Go 的逐字符配对）。"""
    mapping: dict[BulletType, BulletType | None] = {}
    if not rule or "->" not in rule:
        return mapping
    left, _, right = rule.partition("->")
    left = left.strip()
    right = right.strip()
    # Go 的实现是把两边反转后逐字符配对
    for src_ch, dst_ch in zip(reversed(left), reversed(right)):
        src = _TYPE_CODE.get(src_ch)
        if src is None or src is BulletType.NONE:
            continue
        mapping[src] = _TYPE_CODE.get(dst_ch)  # None 表示丢弃
    return mapping


def _apply_filters(bullets: Iterable[Bullet], cfg: AssConfig) -> list[Bullet]:
    mapping = parse_convert_rule(cfg.convert)
    keywords = [k for k in (cfg.block_keywords or []) if k]
    out: list[Bullet] = []
    for b in bullets:
        if keywords and any(k in b.text for k in keywords):
            continue
        if b.type in mapping:
            target = mapping[b.type]
            if target is None:
                continue
            b.type = target
        if b.type is BulletType.NONE:
            continue
        out.append(b)
    return out


# ---------------------------------------------------------------------------
# 写出
# ---------------------------------------------------------------------------

_HEADER_TEMPLATE = (
    "[Script Info]\n"
    "; Roomid: {roomid} Name: {name} Title: {title}\n"
    "Title: LiveDanmaku ASS file\n"
    "ScriptType: v4.00+\n"
    "PlayResX: {width}\n"
    "PlayResY: {height}\n"
    "Collisions: Normal\n"
    "WrapStyle: 2\n"
    "Timer: 100.0000\n"
)

_STYLE_HEADER = (
    "\n[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
    "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
    "MarginV, Encoding\n"
)

_STYLE_LINE = (
    "Style: {name},{font},{size},{primary},&H00FFFFFF,{outline},{back},{bold},"
    "0,0,0,100,100,0,0,1,{outline_w},{shadow},{align},0,0,0,1\n"
)

_EVENTS_HEADER = (
    "\n[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def _bti(bold: bool) -> str:
    return "-1" if bold else "0"


def write_ass(
    bullets: Sequence[Bullet],
    cfg: AssConfig,
    out_path: Path,
    header: tuple[str, str, str] = ("", "", ""),
) -> dict[str, int]:
    """生成 ASS 文件，返回统计信息 ``{total, written, dropped}``。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    text_argb = parse_argb(cfg.alpha, "0xffffff")
    outline_argb = parse_argb(cfg.outline_alpha, cfg.outline_rgb)
    if outline_argb == 0:
        # 描边色缺省时的兜底常量
        outline_argb = 0x1E49516A

    primary = _ass_hex_with_alpha(text_argb)
    outline_hex = _ass_hex_with_alpha(outline_argb)

    roomid, name, title = header
    stats = {"total": len(bullets), "written": 0, "dropped": 0}

    items = _apply_filters(bullets, cfg)

    roll_track = _RollTrack(cfg)
    top_track = _FixedTrack(cfg)
    bottom_track = _FixedTrack(cfg)
    qualifier = _DensityQualifier(cfg.density, cfg.roll_speed_ms)
    shift_ms = int(round(cfg.time_shift * 1000))

    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_HEADER_TEMPLATE.format(roomid=roomid, name=name, title=title,
                                         width=cfg.width, height=cfg.height))
        fh.write(_STYLE_HEADER)
        style_kwargs = {
            "font": cfg.font_name, "size": cfg.font_size, "primary": primary,
            "outline": outline_hex, "back": outline_hex, "bold": _bti(cfg.bold),
            "outline_w": cfg.outline, "shadow": cfg.shadow,
        }
        for style_name, align in (("Roll", 8), ("Top", 8), ("Bottom", 2), ("SC", 8)):
            fh.write(_STYLE_LINE.format(name=style_name, align=align, **style_kwargs))
        fh.write(_EVENTS_HEADER)

        for bullet in items:
            time_ms = bullet.time_ms + shift_ms
            if time_ms < 0:
                time_ms = 0
            text = bullet.text.replace("\r\n", "\\N").replace("\n", "\\N").replace("\r", "\\N")
            color = bullet.ass_color()

            if bullet.type is BulletType.ROLL:
                if not qualifier.check(bullet):
                    stats["dropped"] += 1
                    continue
                track_id, ok = roll_track.find(bullet)
                if not ok:
                    stats["dropped"] += 1
                    continue
                offset = track_id * cfg.spacing
                bullet_len = cfg.font_size * bullet.length
                sx = cfg.width + (bullet_len >> 1)
                sy = cfg.font_size * track_id + offset
                ex = -(bullet_len >> 1)
                ey = sy
                start = time_to_string(time_ms)
                end = time_to_string(time_ms + cfg.roll_speed_ms)
                fh.write(f"Dialogue: 0,{start},{end},Roll,,0000,0000,0000,,"
                         f"{{\\move({sx},{sy},{ex},{ey})\\c{color}}}{text}\n")

            elif bullet.type is BulletType.TOP:
                track_id, ok = top_track.find(bullet)
                if not ok:
                    stats["dropped"] += 1
                    continue
                x = cfg.width // 2
                y = cfg.font_size * track_id + track_id * cfg.spacing
                start = time_to_string(time_ms)
                end = time_to_string(time_ms + cfg.fix_time_ms)
                fh.write(f"Dialogue: 0,{start},{end},Top,,0000,0000,0000,,"
                         f"{{\\pos({x},{y})\\c{color}}}{text}\n")

            elif bullet.type is BulletType.BOTTOM:
                track_id, ok = bottom_track.find(bullet)
                if not ok:
                    stats["dropped"] += 1
                    continue
                x = cfg.width // 2
                y = cfg.height - cfg.font_size * track_id - track_id * cfg.spacing
                start = time_to_string(time_ms)
                end = time_to_string(time_ms + cfg.fix_time_ms)
                fh.write(f"Dialogue: 0,{start},{end},Bottom,,0000,0000,0000,,"
                         f"{{\\pos({x},{y})\\c{color}}}{text}\n")

            else:
                stats["dropped"] += 1
                continue
            stats["written"] += 1

    LOG.debug("ASS 写出: %s (写入 %d / 丢弃 %d)", out_path, stats["written"], stats["dropped"])
    return stats
