"""单任务转换流水线。

每个可转换单元走 10 步：

1. 校验音视频轨道存在
2. 渲染输出路径（命名模板 → 清洗）
3. 预处理 m4s（仅在文件头不是合法 box 时剥离前缀，产出一份临时副本）
4. 探测分辨率 → 决定 ASS 画布
5. 计算组合 MD5 并查重（命中则跳过）
6. 同名冲突处理（跳过 / 覆盖 / 改名 / 询问）
7. 生成 ASS 弹幕
8. 合并音视频（ffmpeg / MP4Box）
9. 输出附属文件（封面 / NFO / danmaku.xml 副本）
10. 写 .hash，清理中间产物

中间产物写在系统临时目录，**不碰缓存目录里的任何源文件**。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import hashfile
from . import metadata as meta
from . import naming
from . import nfo as nfo_mod
from .danmaku import AssConfig, parse_danmaku_xml, write_ass
from .danmaku.fetcher import fetch_danmaku
from .models import (
    ConvertResult,
    HashMode,
    ItemState,
    Options,
    VideoItem,
)
from .mux import MuxTools, ensure_playable, find_tools, mux, pick_engine, probe_video_dims
from .utils import make_work_dir, sanitize_name, silent_remove

LOG = logging.getLogger("m4s")

ProgressCb = Callable[[VideoItem, str, float], None]
AskCb = Callable[[Path], bool]

_CANVAS_RE = re.compile(r"^\s*(\d{1,5})\s*[xX*×]\s*(\d{1,5})\s*$")

#: 各阶段在总进度里的占比。
_STEP_PREPARE = 0.10
_STEP_ASS = 0.28
_STEP_MUX = 0.45
_STEP_SIDECAR = 0.88


class Converter:
    """把 ``VideoItem`` 列表转成 mp4。线程安全（每个任务用独立临时目录）。"""

    def __init__(
        self,
        options: Options,
        progress_cb: ProgressCb | None = None,
        log_cb: Callable[[str], None] | None = None,
        ask_cb: AskCb | None = None,
        output_root: Path | None = None,
    ) -> None:
        self.options = options
        self.progress_cb = progress_cb
        self.log_cb = log_cb
        self.ask_cb = ask_cb
        self.tools: MuxTools = find_tools(options)
        self.engine = pick_engine(options, self.tools)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._import_root = Path(options.import_dir).expanduser() if options.import_dir else None
        self._output_root = Path(output_root).expanduser() if output_root else None

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def resolve_output_root(self, item: VideoItem) -> Path:
        if self._output_root is not None:
            return self._output_root
        root = self._import_root or item.media_dir
        return naming.output_root_for(root, self.options)

    def run(self, items: Sequence[VideoItem], workers: int | None = None) -> list[ConvertResult]:
        """转换所有 ``selected`` 的单元。返回顺序与输入一致。"""
        targets = [it for it in items if it.selected]
        if not targets:
            LOG.warning("没有勾选任何任务")
            return []

        count = max(1, int(workers or self.options.workers or 1))
        LOG.info("开始转换 %d 个任务（引擎=%s，并发=%d）", len(targets), self.engine.value, count)

        if count == 1:
            results: list[ConvertResult] = []
            for item in targets:
                if self._stop.is_set():
                    LOG.warning("已取消，剩余 %d 个任务未处理", len(targets) - len(results))
                    break
                results.append(self.convert(item))
            return results

        with ThreadPoolExecutor(max_workers=count, thread_name_prefix="m4s") as pool:
            return list(pool.map(self.convert, targets))

    def convert(self, item: VideoItem) -> ConvertResult:
        """转换单个单元，任何异常都被收敛成失败的 ``ConvertResult``。"""
        if self._stop.is_set():
            return ConvertResult(item, ok=False, skipped=True, error="已取消")

        item.state = ItemState.CONVERTING
        item.progress = 0.0
        item.message = ""
        self._emit(item, "开始", 0.0)

        started = time.time()
        try:
            result = self._convert_one(item)
        except Exception as exc:  # noqa: BLE001 - 单个任务失败不能拖垮队列
            LOG.exception("转换异常: %s", item.primary_title())
            result = ConvertResult(item, ok=False, error=f"{type(exc).__name__}: {exc}")

        if result.skipped:
            item.state = ItemState.SKIPPED
        elif result.ok:
            item.state = ItemState.DONE
            item.output_path = result.output
        else:
            item.state = ItemState.FAILED

        item.message = result.error or item.state.label
        item.progress = 1.0
        self._emit(item, item.state.label, 1.0)

        elapsed = time.time() - started
        if result.skipped:
            LOG.info("[跳过] %s —— %s（%.2fs）", item.primary_title(), result.error, elapsed)
        elif result.ok:
            LOG.info("[完成] %s -> %s（%.2fs）", item.primary_title(), result.output, elapsed)
        else:
            LOG.error("[失败] %s —— %s", item.primary_title(), result.error)
        return result

    def open_output_dir(self, path: Path | None = None) -> None:
        target = path or self._output_root or (
            self._import_root / "output" if self._import_root else None)
        if target is None or not Path(target).exists():
            return
        try:
            if os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys_platform() == "darwin":
                os.system(f'open "{target}"')
            else:
                os.system(f'xdg-open "{target}"')
        except OSError as exc:  # pragma: no cover
            LOG.warning("打开输出目录失败: %s", exc)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _emit(self, item: VideoItem, stage: str, percent: float) -> None:
        item.progress = max(item.progress, min(1.0, percent))
        if self.progress_cb is None:
            return
        try:
            self.progress_cb(item, stage, item.progress)
        except Exception:  # noqa: BLE001 - UI 回调异常不影响转换
            LOG.debug("进度回调异常", exc_info=True)

    def _convert_one(self, item: VideoItem) -> ConvertResult:
        opts = self.options

        if item.video_src is None or item.audio_src is None:
            return ConvertResult(item, ok=False, error="缺少音视频轨道")
        for src in (item.video_src, item.audio_src):
            if not src.is_file():
                return ConvertResult(item, ok=False, error=f"源文件不存在: {src}")

        output_root = self.resolve_output_root(item)
        target, warnings = naming.resolve_output_path(item, opts, output_root)
        for warn in warnings:
            LOG.warning("%s: %s", item.primary_title(), warn)

        work = make_work_dir(opts.work_dir or None, prefix="m4s-conv-")
        try:
            # --- 3. 预处理 ---
            self._emit(item, "预处理 m4s", _STEP_PREPARE * 0.4)
            video, v_stripped = ensure_playable(item.video_src, work)
            audio, a_stripped = ensure_playable(item.audio_src, work)
            if v_stripped or a_stripped:
                LOG.info("剥离 m4s 前缀: %s", item.primary_title())

            # --- 4. 探测分辨率（决定 ASS 画布）---
            width, height, duration_ms = probe_video_dims(video, self.tools)
            if width:
                item.width = width
            if height:
                item.height = height
            if duration_ms:
                item.duration_ms = duration_ms
            self._emit(item, "预处理 m4s", _STEP_PREPARE)

            # --- 5. 查重 ---
            input_hash = ""
            if opts.hash_mode is not HashMode.OFF:
                input_hash = hashfile.combined_md5(video, audio)

            if opts.skip_existing:
                duplicate = hashfile.find_duplicate(target, video, audio, input_hash)
                if duplicate is not None:
                    return ConvertResult(item, ok=True, output=duplicate, skipped=True,
                                         error="已存在相同内容的文件")

            # --- 6. 冲突处理 ---
            final = naming.apply_conflict(target, opts.conflict, self.ask_cb)
            if final is None:
                return ConvertResult(item, ok=True, output=target, skipped=True,
                                     error="同名文件已存在")
            if final != target:
                LOG.info("同名冲突，改为写入 %s", final.name)
            base = final.with_suffix("")

            # --- 7. ASS ---
            if opts.ass.enabled:
                self._emit(item, "生成弹幕字幕", _STEP_ASS * 0.6)
                self._make_ass(item, base.with_suffix(".ass"), work)
            self._emit(item, "生成弹幕字幕", _STEP_ASS)

            # --- 8. 合并 ---
            self._emit(item, "合并音视频", _STEP_MUX * 0.6)
            cover = None
            if opts.embed_cover and item.cover_local and item.cover_local.is_file():
                cover = item.cover_local
            tags = self._tags(item) if opts.write_metadata else None
            result = mux(video, audio, final, opts, tags=tags, cover=cover,
                         tools=self.tools, engine=self.engine)
            if not result.ok:
                if opts.summarize_unmerged:
                    self._summarize_unmerged(item, output_root)
                return ConvertResult(item, ok=False, error=result.message)
            self._emit(item, "合并音视频", _STEP_MUX)

            # --- 9. 附属文件 ---
            self._emit(item, "输出附属文件", _STEP_MUX + 0.1)
            self._write_sidecars(item, base, final)

            # --- 10. hash + 清理 ---
            if opts.hash_mode is HashMode.WRITE and input_hash:
                if hashfile.write_hash(final, input_hash):
                    LOG.debug("写入 hash: %s", hashfile.hash_path_for(final).name)
            self._emit(item, "完成", 1.0)
            return ConvertResult(item, ok=True, output=final)
        finally:
            if self.options.cleanup_intermediate:
                silent_remove(work)
            else:
                LOG.info("保留中间产物目录: %s", work)

    # ------------------------------------------------------------------
    # 附属产物
    # ------------------------------------------------------------------

    def _make_ass(self, item: VideoItem, ass_path: Path, work: Path) -> Path | None:
        cfg_opts = self.options.ass

        if cfg_opts.reuse_existing and item.existing_ass and item.existing_ass.is_file():
            try:
                ass_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item.existing_ass, ass_path)
                LOG.info("复用缓存目录已有字幕: %s", item.existing_ass.name)
                return ass_path
            except OSError as exc:
                LOG.warning("复用已有字幕失败，改为重新生成: %s", exc)

        xml = fetch_danmaku(item.danmaku_xml, item.cid, work,
                            allow_download=cfg_opts.download_missing)
        if xml is None:
            LOG.warning("没有可用的弹幕文件，跳过字幕: %s", item.primary_title())
            return None

        bullets, header = parse_danmaku_xml(xml)
        if not bullets:
            # 该稿件确实没有任何弹幕（源 XML 里 <d> 数为 0）。
            # 仍然写一个"只有头部 + 样式"的合法 ASS，保证开启 ass 开关时
            # 每个 mp4 配一个同名 .ass。
            LOG.warning("弹幕内容为空，生成空白字幕: %s", xml.name)

        try:
            stats = write_ass(bullets, self._ass_config(item), ass_path, header.as_tuple())
        except OSError as exc:
            LOG.error("写入 ASS 失败 %s: %s", ass_path, exc)
            return None

        if bullets:
            LOG.info("生成字幕 %s（写入 %d/%d 条，丢弃 %d 条）",
                     ass_path.name, stats["written"], stats["total"], stats["dropped"])
        else:
            LOG.info("生成空白字幕 %s（源弹幕为 0 条）", ass_path.name)
        if not item.danmaku_count:
            item.danmaku_count = stats["written"]
        return ass_path

    def _ass_config(self, item: VideoItem) -> AssConfig:
        a = self.options.ass
        canvas = (a.canvas or "auto").strip()
        width, height = 1920, 1080

        if canvas.lower() != "auto":
            match = _CANVAS_RE.match(canvas)
            if match:
                width, height = int(match.group(1)), int(match.group(2))
            elif item.width and item.height:
                width, height = item.width, item.height
        elif item.width and item.height:
            # 竖屏视频套 1920x1080 画布会把弹幕拉伸变形，所以默认用实际分辨率
            width, height = item.width, item.height

        return AssConfig(
            font_name=a.font_name,
            font_size=a.font_size,
            alpha=a.alpha,
            bold=a.bold,
            outline=a.outline,
            shadow=a.shadow,
            outline_rgb=a.outline_color,
            outline_alpha=a.outline_alpha,
            roll_time=a.roll_time,
            fix_time=a.fix_time,
            time_shift=a.time_shift,
            width=width,
            height=height,
            roll_range=a.roll_range,
            fixed_range=a.fixed_range,
            spacing=a.spacing,
            density=a.density,
            overlay=a.overlay,
            convert=a.convert,
            block_keywords=list(a.block_keywords),
        )

    def _tags(self, item: VideoItem) -> dict[str, str]:
        """容器元数据，取媒体库能直接读懂的写法。

        ``title`` = 视频标题、``artist`` = UP 主、``album`` = 合集，
        原始 id（av / BV / cid / up）塞进 ``comment``，方便日后追溯。
        """
        ids = " ".join(p for p in (
            f"av{item.avid}" if item.avid else "",
            f"BV{item.bvid}" if item.bvid else "",
            f"cid={item.cid}" if item.cid else "",
            f"up={item.up}" if item.up else "",
        ) if p)
        return {
            "title": item.primary_title(),
            "artist": item.up or item.owner_id,
            "album": item.group or item.series_title,
            "comment": ids,
        }

    def _write_sidecars(self, item: VideoItem, base: Path, media: Path) -> None:
        opts = self.options
        poster_name = ""

        if opts.export_cover and item.cover_local and item.cover_local.is_file():
            dest = base.with_name(base.name + "-poster" + item.cover_local.suffix.lower())
            try:
                shutil.copy2(item.cover_local, dest)
                poster_name = dest.name
                LOG.debug("导出封面: %s", dest.name)
            except OSError as exc:
                LOG.warning("导出封面失败: %s", exc)

        if opts.copy_danmaku_xml and item.danmaku_xml and item.danmaku_xml.is_file():
            dest = base.with_name(base.name + ".danmaku.xml")
            try:
                shutil.copy2(item.danmaku_xml, dest)
            except OSError as exc:
                LOG.warning("导出 danmaku.xml 失败: %s", exc)

        if opts.write_nfo:
            nfo_mod.write_nfo(base.with_suffix(".nfo"), item, poster_name=poster_name)

    def _summarize_unmerged(self, item: VideoItem, output_root: Path) -> None:
        dest_dir = output_root / "未合并文件"
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            LOG.warning("创建汇总目录失败: %s", exc)
            return
        stem = sanitize_name(item.primary_title(), "safe") or item.media_dir.name
        for src, tag in ((item.video_src, "video"), (item.audio_src, "audio")):
            if src is None or not src.is_file():
                continue
            dest = dest_dir / f"{stem}_{tag}{src.suffix}"
            if dest.exists():
                continue
            try:
                shutil.copy2(src, dest)
            except OSError as exc:
                LOG.warning("汇总未合并文件失败 %s: %s", src.name, exc)


def sys_platform() -> str:
    import sys

    return sys.platform


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------

def summarize_results(results: Iterable[ConvertResult]) -> str:
    done = skipped = failed = 0
    outputs: list[Path] = []
    for res in results:
        if res.skipped:
            skipped += 1
        elif res.ok:
            done += 1
            if res.output:
                outputs.append(res.output)
        else:
            failed += 1
    lines = [
        "=" * 60,
        f"完成 {done} / 跳过 {skipped} / 失败 {failed}（共 {done + skipped + failed}）",
    ]
    if outputs:
        lines.append("输出文件:")
        lines.extend(f"  {p}" for p in outputs)
    failures = [r for r in results if not r.ok and not r.skipped]
    if failures:
        lines.append("失败明细:")
        lines.extend(f"  {r.item.primary_title()} —— {r.error}" for r in failures)
    lines.append("=" * 60)
    return "\n".join(lines)


__all__ = ["Converter", "summarize_results", "ProgressCb", "AskCb"]
