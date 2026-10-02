"""命令行入口（P1 阶段的可用界面）。

用法示例::

    python cli.py -i "D:\\B站缓存\\download"
    python cli.py -i <缓存目录> --layout flat --no-hash --no-ass --dry-run
    python cli.py -i <缓存目录> --layout template --template "{up}/{date}_{title}"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m4sconverter import __version__
from m4sconverter.config import load_options, save_options
from m4sconverter.converter import Converter, summarize_results
from m4sconverter.models import (
    ConflictMode,
    Engine,
    HashMode,
    ItemState,
    LayoutMode,
    NameFilter,
    Options,
)
from m4sconverter.mux import check_tools
from m4sconverter.naming import preview_path, TEMPLATE_FIELDS
from m4sconverter.scanner import describe, scan
from m4sconverter.utils import setup_logging

_LAYOUTS = {
    "original": LayoutMode.ORIGINAL,
    "title_dir": LayoutMode.TITLE_DIR,
    "flat": LayoutMode.FLAT,
    "up_dir": LayoutMode.UP_DIR,
    "template": LayoutMode.TEMPLATE,
}


def _make_stdout_safe() -> None:
    """Windows 控制台默认是 GBK，遇到生僻字符会直接抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # pragma: no cover - 非文本流
            continue


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="Bili_download_converter",
        description="B站缓存转换器：把 B 站缓存的 m4s 合并成 mp4，并生成 ASS 弹幕、hash、nfo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="命名模板可用占位符:\n" + "\n".join(
            f"  {{{k}}}{' ' * max(0, 10 - len(k))} {v}" for k, v in TEMPLATE_FIELDS.items()
        ),
    )
    p.add_argument("-v", "--version", action="version", version=f"B站缓存转换器 {__version__}")

    src = p.add_argument_group("输入输出")
    src.add_argument("-i", "-c", "--input", "--cachepath", dest="input", default="",
                     help="导入目录（缓存根目录），默认读取配置或 bilibili 默认缓存路径")
    src.add_argument("-o", "--output", dest="output", default="",
                     help="输出目录，默认 <导入目录>/output")
    src.add_argument("--per-import", action="store_true",
                     help="自定义输出目录下再按导入目录名分子目录")
    src.add_argument("--container", choices=["mp4", "mkv"], default=None, help="输出容器")

    name = p.add_argument_group("命名")
    name.add_argument("--layout", choices=list(_LAYOUTS), default=None,
                      help="original=合集-UP/分P | title_dir=标题/标题 | flat=标题（默认）| "
                           "up_dir=UP/标题 | template=自定义")
    name.add_argument("--template", default=None, help="自定义命名模板，配合 --layout template")
    name.add_argument("--name-filter", choices=["safe", "keep"], default=None,
                      help="文件名清洗：safe=替换（默认，把文件名禁止的字符换成"
                           "\"长得像但合法\"的替身）| "
                           "keep=最小改动（仅把那 9 个禁止字符换成 _）")
    name.add_argument("--conflict", choices=["skip", "overwrite", "rename", "ask"], default=None,
                      help="同名文件处理，默认 skip（跳过，不覆盖已有文件）")
    name.add_argument("--preview", action="store_true",
                      help="只预览第一条任务的输出路径后退出")

    ass = p.add_argument_group("弹幕字幕")
    ass.add_argument("-a", "--assoff", action="store_true",
                     help="不生成 ASS 弹幕字幕")
    ass.add_argument("--ass-reuse", action="store_true",
                     help="缓存目录已有 danmaku.ass 时直接复用")
    ass.add_argument("--font", default=None, help="字体名，默认 黑体")
    ass.add_argument("--font-size", type=int, default=None, help="字号，默认 26")
    ass.add_argument("--alpha", type=float, default=None, help="文字透明度 0~1，默认 0.3")
    ass.add_argument("--no-bold", action="store_true", help="关闭粗体")
    ass.add_argument("--outline", type=int, default=None, help="描边宽度，默认 0")
    ass.add_argument("--shadow", type=int, default=None, help="阴影大小，默认 1")
    ass.add_argument("--roll-time", type=float, default=None, help="滚动弹幕显示秒数，默认 15")
    ass.add_argument("--fix-time", type=float, default=None, help="顶/底弹幕显示秒数，默认 5")
    ass.add_argument("--time-shift", type=float, default=None, help="弹幕时间偏移秒数")
    ass.add_argument("--roll-range", type=float, default=None, help="滚动弹幕可用屏高比例")
    ass.add_argument("--fixed-range", type=float, default=None, help="固定弹幕可用屏高比例")
    ass.add_argument("--spacing", type=int, default=None, help="轨道额外间距")
    ass.add_argument("--density", type=int, default=None, help="同屏密度上限，0=不限")
    ass.add_argument("--overlay", action="store_true", help="抢不到轨道时重叠显示")
    ass.add_argument("--canvas", default=None, help="画布 auto（默认，用视频实际分辨率）或 1920x1080")
    ass.add_argument("--convert", default=None, help='弹幕类型转换规则，默认 "s -> r"')
    ass.add_argument("--block", action="append", default=None, help="屏蔽关键词，可重复")
    ass.add_argument("--no-download-danmaku", action="store_true", help="本地无弹幕时不联网抓取")

    extra = p.add_argument_group("附属文件")
    extra.add_argument("--no-metadata", action="store_true", help="不写 mp4 title/artist/album")
    extra.add_argument("--no-cover", action="store_true", help="不导出封面")
    extra.add_argument("--cover-in-mp4", action="store_true", help="把封面嵌进 mp4")
    extra.add_argument("--no-nfo", action="store_true", help="不生成 .nfo")
    extra.add_argument("--no-danmaku-xml", action="store_true", help="不导出 danmaku.xml 副本")

    hsh = p.add_argument_group("hash 文件")
    hsh.add_argument("--hash-mode", choices=["write", "dedup_only", "off"], default=None,
                     help="write=写 .hash 并去重（默认）| dedup_only=只去重不写 | off=不用")
    hsh.add_argument("--no-hash", action="store_true", help="等价于 --hash-mode off")

    be = p.add_argument_group("行为")
    be.add_argument("--no-skip-existing", action="store_true", help="不跳过已存在的相同视频")
    be.add_argument("--include-unfinished", action="store_true", help="也处理未缓存完成的视频")
    be.add_argument("-u", "--summarize", action="store_true", help="未被合并的文件汇总到「未合并文件」")
    be.add_argument("--keep-intermediate", action="store_true", help="保留临时中间产物")
    be.add_argument("--open", action="store_true", help="结束后打开输出目录")
    be.add_argument("-j", "--workers", type=int, default=None, help="并发数，默认 1")
    be.add_argument("--limit", type=int, default=0, help="只处理前 N 个单元")

    eng = p.add_argument_group("引擎")
    eng.add_argument("--engine", choices=["auto", "ffmpeg", "mp4box"], default=None)
    eng.add_argument("--ffmpeg", default=None, help="ffmpeg.exe 路径")
    eng.add_argument("--mp4box", default=None, help="MP4Box 路径")
    eng.add_argument("--work-dir", default=None, help="中间产物目录，默认系统临时目录")

    misc = p.add_argument_group("其它")
    misc.add_argument("--config", default="", help="配置文件路径")
    misc.add_argument("--save-config", default="", help="把本次参数保存到指定配置文件后退出")
    misc.add_argument("--list", action="store_true", help="只列出扫描结果，不转换")
    misc.add_argument("--dry-run", action="store_true", help="只显示将要做什么，不实际转换")
    misc.add_argument("--check", action="store_true", help="检查合并引擎是否可用后退出")
    misc.add_argument("--verbose", action="store_true", help="输出调试日志")
    misc.add_argument("-y", "--yes", action="store_true", help="跳过所有交互确认")
    return p


def options_from_args(args: argparse.Namespace) -> Options:
    opts = load_options(args.config) if args.config else Options()

    if args.input:
        opts.import_dir = args.input
    if args.output:
        opts.output_dir = args.output
    if args.per_import:
        opts.output_per_import = True

    if args.layout:
        opts.layout = _LAYOUTS[args.layout]
    if args.template is not None:
        opts.template = args.template
    if args.name_filter:
        opts.name_filter = NameFilter(args.name_filter)
    if args.conflict:
        opts.conflict = ConflictMode(args.conflict)
    if args.container:
        opts.container = args.container

    if args.assoff:
        opts.ass.enabled = False
    if args.ass_reuse:
        opts.ass.reuse_existing = True
    if args.font is not None:
        opts.ass.font_name = args.font
    if args.font_size is not None:
        opts.ass.font_size = args.font_size
    if args.alpha is not None:
        opts.ass.alpha = args.alpha
    if args.no_bold:
        opts.ass.bold = False
    if args.outline is not None:
        opts.ass.outline = args.outline
    if args.shadow is not None:
        opts.ass.shadow = args.shadow
    if args.roll_time is not None:
        opts.ass.roll_time = args.roll_time
    if args.fix_time is not None:
        opts.ass.fix_time = args.fix_time
    if args.time_shift is not None:
        opts.ass.time_shift = args.time_shift
    if args.roll_range is not None:
        opts.ass.roll_range = args.roll_range
    if args.fixed_range is not None:
        opts.ass.fixed_range = args.fixed_range
    if args.spacing is not None:
        opts.ass.spacing = args.spacing
    if args.density is not None:
        opts.ass.density = args.density
    if args.overlay:
        opts.ass.overlay = True
    if args.canvas is not None:
        opts.ass.canvas = args.canvas
    if args.convert is not None:
        opts.ass.convert = args.convert
    if args.block:
        opts.ass.block_keywords = list(args.block)
    if args.no_download_danmaku:
        opts.ass.download_missing = False

    if args.no_metadata:
        opts.write_metadata = False
    if args.no_cover:
        opts.export_cover = False
    if args.cover_in_mp4:
        opts.embed_cover = True
    if args.no_nfo:
        opts.write_nfo = False
    if args.no_danmaku_xml:
        opts.copy_danmaku_xml = False

    if args.hash_mode:
        opts.hash_mode = HashMode(args.hash_mode)
    if args.no_hash:
        opts.hash_mode = HashMode.OFF

    if args.no_skip_existing:
        opts.skip_existing = False
    if args.include_unfinished:
        opts.skip_unfinished = False
    if args.summarize:
        opts.summarize_unmerged = True
    if args.keep_intermediate:
        opts.cleanup_intermediate = False
    if args.open:
        opts.open_output_dir = True
    if args.workers is not None:
        opts.workers = max(1, args.workers)

    if args.engine:
        opts.engine = Engine(args.engine)
    if args.ffmpeg:
        opts.ffmpeg_path = args.ffmpeg
    if args.mp4box:
        opts.mp4box_path = args.mp4box
    if args.work_dir:
        opts.work_dir = args.work_dir

    return opts


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _make_stdout_safe()
    parser = build_parser()
    args = parser.parse_args(argv)

    setup_logging(verbose=args.verbose)

    if args.check:
        ok, desc = check_tools(load_options(args.config) if args.config else Options())
        print(("[OK] 可用" if ok else "[X] 不可用") + f": {desc}")
        return 0 if ok else 1

    opts = options_from_args(args)

    if args.save_config:
        path = save_options(opts, Path(args.save_config))
        print(f"配置已保存: {path}")
        return 0

    if not opts.import_dir:
        parser.error("必须用 -i/--input 指定导入目录（或配置文件里已设置）")

    import_root = Path(opts.import_dir).expanduser()
    reported = scan(import_root, opts)
    print(describe(reported, limit=40))

    targets = [it for it in reported.items if it.state is not ItemState.SKIPPED]
    if args.limit and args.limit > 0:
        targets = targets[:args.limit]
    for i, item in enumerate(reported.items):
        item.selected = item in targets
        if i >= 0:
            item.index = i + 1
    if not targets:
        print("没有可转换的视频。")
        return 0

    if args.preview:
        opts.import_dir = str(import_root)
        converter = Converter(opts)
        # 全部列出而不是只列第一条——用户正是靠这一步确认命名模板 / 文件名清洗
        # 到底把标题变成了什么样，只看第一条看不出规则的效果。
        print(f"预览输出路径（共 {len(targets)} 个）:")
        for item in targets[:80]:
            print(f"  {item.index:>3}. {preview_path(item, opts, converter.resolve_output_root(item))}")
        if len(targets) > 80:
            print(f"  … 其余 {len(targets) - 80} 个略")
        return 0

    if args.list:
        return 0

    if args.dry_run:
        conv = Converter(opts)
        print(f"引擎: {conv.tools.describe()}")
        print(f"实际使用: {conv.engine.value}")
        print("将输出到:")
        for item in targets:
            print(f"  {item.primary_title()[:50]} -> "
                  f"{preview_path(item, opts, conv.resolve_output_root(item))}")
        return 0

    ok, desc = check_tools(opts)
    if not ok:
        print(f"[X] 未找到可用的合并引擎，请安装 ffmpeg 或用 --ffmpeg 指定路径。{desc}")
        return 2

    ask = None
    if opts.conflict is ConflictMode.ASK and not args.yes:
        def ask(path: Path) -> bool:  # type: ignore[misc]
            answer = input(f"文件已存在: {path}\n覆盖？[y/N/a(全部覆盖)] ").strip().lower()
            return answer in ("y", "yes", "a", "all")

    conv = Converter(opts, ask_cb=ask)
    print(f"合并引擎: {conv.engine.value}（{conv.tools.describe()}）")
    results = conv.run(targets)
    print(summarize_results(results))

    if opts.open_output_dir:
        conv.open_output_dir()
    return 0 if all(r.ok or r.skipped for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
