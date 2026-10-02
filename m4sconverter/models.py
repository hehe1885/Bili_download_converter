"""数据模型。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


class LayoutMode(str, enum.Enum):
    """输出布局模式。"""

    ORIGINAL = "original"      # output\{合集}-{UP}\{分P}.mp4   —— 合集分组
    TITLE_DIR = "title_dir"    # output\{标题}\{标题}.mp4
    FLAT = "flat"              # output\{标题}.mp4              —— 用户默认
    UP_DIR = "up_dir"          # output\{UP}\{标题}.mp4
    TEMPLATE = "template"      # 用户自定义模板


class ConflictMode(str, enum.Enum):
    SKIP = "skip"
    OVERWRITE = "overwrite"
    RENAME = "rename"
    ASK = "ask"


class Engine(str, enum.Enum):
    AUTO = "auto"
    FFMPEG = "ffmpeg"
    MP4BOX = "mp4box"


class HashMode(str, enum.Enum):
    WRITE = "write"            # 写 .hash 且用于去重（默认）
    DEDUP_ONLY = "dedup_only"  # 只用于去重，不落盘
    OFF = "off"


class NameFilter(str, enum.Enum):
    """文件名清洗规则。

    **只有两种**——早先给过三档（细粒度替换 / 替换+Windows 兜底 / 只换非法字符），
    用户看一眼就说"三种容易让别人看不懂"。现在收敛成两档，界面上写大白话。

    为什么不给"什么都不替换"这一档：标题里一旦出现 ``/``，Windows 会把它当成
    目录分隔符，本该是一个文件却会多建一层目录、甚至直接报错。所以 Windows
    不允许的字符必须换掉，两档都只碰这 9 个字符，区别只在**换成什么**。

    ``STANDARD``（界面写作"替换"，默认）
        把 Windows 禁止的 ``< > : " / \\ | ? *`` 换成"长得像但合法"的替身：
        半角 ``/ \\ | *`` 换成**全角** ``／＼｜＊``，半角 ``: ?`` 换成 ``：？``，
        ``< >`` 换成中文书名号 ``《》``，``"`` 换成 ``'``。
        其余字符（``（）``、``【】``、空格、中文标点、emoji）全部原样保留。
        再顺手避开 Windows 的边角限制：删掉控制字符、去掉结尾的点和空格、
        给 ``CON``/``PRN``/``AUX``/``NUL``/``COM1-9``/``LPT1-9`` 前加下划线。
        文件名既合法又一眼认得出来，推荐。

    ``MINIMAL``（界面写作"最小改动"）
        同样只碰那 9 个字符，但一律换成下划线 ``_``。
    """

    STANDARD = "safe"
    MINIMAL = "keep"

    @classmethod
    def _missing_(cls, value: object) -> "NameFilter | None":
        """兼容旧配置。

        v0.1 时期写过 ``"go"``（那个写法粒度太细，现在已并入 STANDARD），
        老配置文件再打开时不能报错，所以在这里做一次静默迁移。
        """
        if isinstance(value, str) and value.strip().lower() == "go":
            return cls.STANDARD
        return None


class ItemState(str, enum.Enum):
    PENDING = "pending"
    CONVERTING = "converting"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return {
            ItemState.PENDING: "待转换",
            ItemState.CONVERTING: "转换中",
            ItemState.DONE: "已完成",
            ItemState.SKIPPED: "已跳过",
            ItemState.FAILED: "失败",
        }[self]


@dataclass
class AssOptions:
    """ASS 弹幕生成参数（默认值按中文弹幕的常见观感给定）。"""

    enabled: bool = True
    reuse_existing: bool = False      # 缓存目录已有 danmaku.ass 时直接复用
    font_name: str = "黑体"
    font_size: int = 26
    alpha: float = 0.3                # 文字透明度 0~1（0=不透明）
    bold: bool = True
    outline: int = 0
    shadow: int = 1
    outline_color: str = "0x49516A"
    outline_alpha: float = 0.1
    shadow_color: str = "0x49516A"
    shadow_alpha: float = 0.1
    roll_time: float = 15.0           # 滚动弹幕显示秒数
    fix_time: float = 5.0             # 顶部/底部弹幕显示秒数
    time_shift: float = 0.0           # 时间偏移秒数
    roll_range: float = 1.0           # 滚动弹幕可用屏高比例
    fixed_range: float = 1.0          # 固定弹幕可用屏高比例
    spacing: int = 0                  # 轨道额外间距
    density: int = 0                  # 同屏密度上限，0=不限
    overlay: bool = False             # 抢不到轨道时是否重叠显示
    canvas: str = "auto"              # auto | 1920x1080 | 自定义 WxH
    convert: str = "s -> r"           # 弹幕类型转换规则
    block_keywords: list[str] = field(default_factory=list)
    download_missing: bool = True     # 本地无 xml 时联网抓取
    keep_xml_copy: bool = True        # 把源 danmaku.xml 复制到输出目录


@dataclass
class Options:
    """一次转换任务的全部选项。"""

    # --- 输入输出 ---
    import_dir: str = ""
    output_dir: str = ""              # 空 = <导入目录>\output
    output_per_import: bool = False   # 自定义输出目录下再按导入目录名分子目录

    # --- 命名 ---
    layout: LayoutMode = LayoutMode.FLAT
    template: str = "{title}.mp4"
    name_filter: NameFilter = NameFilter.STANDARD
    conflict: ConflictMode = ConflictMode.SKIP

    # --- 附属文件 ---
    write_metadata: bool = True       # mp4 title/artist/album
    export_cover: bool = True         # 导出 cover.jpg
    embed_cover: bool = False         # 封面写入 mp4
    write_nfo: bool = True            # .nfo 刮削文件
    copy_danmaku_xml: bool = True     # 导出 danmaku.xml 副本
    container: str = "mp4"            # mp4 | mkv

    # --- hash ---
    hash_mode: HashMode = HashMode.WRITE   # write=写文件且去重 / dedup_only=只去重 / off

    # --- 策略 ---
    skip_unfinished: bool = True
    skip_existing: bool = True
    summarize_unmerged: bool = False  # 中间产物汇总到「未合并文件」
    cleanup_intermediate: bool = True
    open_output_dir: bool = False
    workers: int = 1

    # --- 引擎 ---
    engine: Engine = Engine.AUTO
    ffmpeg_path: str = ""
    mp4box_path: str = ""
    work_dir: str = ""                # 空 = 系统临时目录

    # --- 弹幕 ---
    ass: AssOptions = field(default_factory=AssOptions)


@dataclass
class VideoItem:
    """一个可转换的视频单元。"""

    # ---- 路径 ----
    media_dir: Path                       # 存放 m4s 的目录
    video_src: Path | None = None
    audio_src: Path | None = None
    info_path: Path | None = None
    info_kind: str = ""                   # entry.json / videoInfo.json / .videoInfo / .playurl
    danmaku_xml: Path | None = None
    existing_ass: Path | None = None
    cover_local: Path | None = None

    # ---- 元数据 ----
    title: str = ""
    part: str = ""
    up: str = ""
    group: str = ""
    avid: str = ""
    bvid: str = ""
    cid: str = ""
    qid: str = ""
    quality: str = ""                     # 画质描述，如 1080P
    status: str = ""
    owner_id: str = ""
    item_id: str = ""
    group_id: str = ""
    uid: str = ""
    width: int = 0
    height: int = 0
    duration_ms: int = 0
    danmaku_count: int = 0
    cover_url: str = ""
    series_title: str = ""
    info_raw: dict = field(default_factory=dict)

    # ---- 运行时 ----
    index: int = 0
    selected: bool = True
    state: ItemState = ItemState.PENDING
    message: str = ""
    progress: float = 0.0
    output_path: Path | None = None

    # ------------------------------------------------------------------
    @property
    def duration_s(self) -> float:
        return self.duration_ms / 1000.0

    @property
    def has_danmaku_source(self) -> bool:
        return bool(self.danmaku_xml) or bool(self.existing_ass)

    def primary_title(self) -> str:
        """用于命名的首选标题（对齐 Go 的 title 变量，再逐级兜底）。"""
        for cand in (self.title, self.part, self.series_title):
            if cand and cand.strip():
                return cand.strip()
        if self.group or self.up:
            joined = f"{self.group}-{self.up}".strip("-")
            if joined:
                return joined
        return self.media_dir.name

    def part_title(self) -> str:
        return (self.part or self.title or self.media_dir.name).strip()

    def replace_with(self, other: "VideoItem") -> None:
        self.__dict__.update(other.__dict__)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<VideoItem {self.primary_title()!r} @ {self.media_dir}>"


@dataclass
class ConvertResult:
    item: VideoItem
    ok: bool
    output: Path | None = None
    skipped: bool = False
    error: str = ""


@dataclass
class ScanReport:
    items: list[VideoItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scanned_dirs: int = 0
    elapsed: float = 0.0
