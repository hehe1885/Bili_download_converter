"""生成 README 用的界面预览图。

默认使用**内置演示数据**（在 `D:\\B站缓存\\download` 下临时造一份假的缓存目录，
出图后立刻删掉），这样公开出去的截图里不会出现任何真实视频标题，也不会泄漏用户名：

    python tests/make_screenshot.py                    # -> docs/gui.png
    python tests/make_screenshot.py out.png            # 指定输出

想用自己真实的缓存目录出图（仅供本地看，别提交）：

    python tests/make_screenshot.py --real "D:\\B站缓存\\download" out.png

两点注意：
* 不要设 `QT_QPA_PLATFORM=offscreen` —— 离屏插件在 Windows 上找不到字体，
  截出来是一张全空白的图。用默认（原生）平台插件才能正常渲染中文。
* 截图用的选项是脚本里写死的一组标准值，并且读写的是临时 config.json，
  所以既不受本机上次保存的配置影响，也不会反过来污染用户配置。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import QApplication  # noqa: E402

import gui.main_window as mw  # noqa: E402
from m4sconverter import scanner  # noqa: E402
from m4sconverter.models import LayoutMode, Options  # noqa: E402

#: 演示数据：全是编造的标题，避免把真实观看记录放进公开仓库。
DEMO_ITEMS = [
    # (标题, UP主, 画质描述, qid, 时长毫秒, 弹幕数, 宽, 高)
    ("示例视频 · 第一讲", "示例UP主", "1080P", 80, 754_000, 1284, 1920, 1080),
    ("演示视频：标题里带全角括号（测试）", "演示频道", "720P", 64, 201_000, 356, 1280, 720),
    ("示例合集 EP01 开箱演示", "教程UP", "1080P", 80, 1_507_000, 2048, 1920, 1080),
    ("演示视频 A｜B 替换效果", "示例UP主", "1080P", 80, 495_000, 612, 1920, 1080),
    ("示例视频-2026/05/20（日期里的斜杠）", "演示频道", "720P", 64, 107_000, 98, 1280, 720),
    ("演示用竖屏视频", "教程UP", "1080P", 112, 31_900, 42, 888, 1920),
]

#: 截图里显示的缓存目录。挑一个干净、真实、且**不含用户名**的路径，
#: 免得把 C:\Users\<你的名字>\... 一并公开出去。
DISPLAY_ROOT = Path("D:\\B站缓存\\download")


def _pick_demo_root() -> Path:
    """优先用好看的固定路径；不可写就退回临时目录。"""
    try:
        DISPLAY_ROOT.mkdir(parents=True, exist_ok=True)
        (DISPLAY_ROOT / ".w").write_text("", encoding="utf-8")
        (DISPLAY_ROOT / ".w").unlink()
        return DISPLAY_ROOT
    except OSError:
        return Path(tempfile.mkdtemp(prefix="m4s-shot-")) / "download"


def build_demo_cache(root: Path) -> Path:
    """造一份结构上以假乱真的缓存目录，让 scanner 自己去解析。"""
    for i, (title, up, qdesc, qid, ms, danmaku, w, h) in enumerate(DEMO_ITEMS, start=1):
        avid = 100000000 + i
        cid = 40000000000 + i
        media = root / str(avid) / f"c_{cid}" / str(qid)
        media.mkdir(parents=True, exist_ok=True)

        entry = {
            "media_type": 2,
            "has_dash_audio": True,
            "is_completed": True,
            "title": title,
            "type_tag": "80",
            "owner_name": up,
            "video_quality": qid,
            "prefered_video_quality": qid,
            "quality_pithy_description": qdesc,
            "total_time_milli": ms,
            "danmaku_count": danmaku,
            "status": "completed",
            "avid": avid,
            "page_data": {
                "cid": cid,
                "page": 1,
                "part": f"P{i}",
                "width": w,
                "height": h,
            },
        }
        (media.parent / "entry.json").write_text(
            json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (media.parent / "danmaku.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<i><chatserver>chat.bilibili.com</chatserver></i>\n',
            encoding="utf-8",
        )
        # 内容无关紧要，scanner 只按文件名配对，不会真去解码。
        (media / "video.m4s").write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)
        (media / "audio.m4s").write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)
    return root


def render(src: Path, out: Path, note: str) -> int:
    app = QApplication([])

    # 用一个临时 config.json，避免读进本机上次保存的选项（否则截图内容会飘），
    # 也避免退出时把演示配置写回用户真正的配置文件。
    cfg_dir = Path(tempfile.mkdtemp(prefix="m4s-shot-cfg-"))
    win = mw.MainWindow(config_path=cfg_dir / "config.json")
    win.resize(1360, 820)
    win.show()

    # 显式给一组"标准"选项，出图才有可比性。
    opt = Options()
    opt.import_dir = str(src)
    opt.output_dir = ""            # 留空 = <导入目录>\output
    opt.layout = LayoutMode.FLAT   # 平铺：output\视频名.mp4
    opt.ass.enabled = True
    win.options_panel.load_from(opt)

    report = scanner.scan(src, opt)
    win._on_scanned(report)
    win._append_log(note)
    for _ in range(4):
        app.processEvents()

    out.parent.mkdir(parents=True, exist_ok=True)
    ok = win.grab().save(str(out))
    print(("已保存 " if ok else "保存失败 ") + str(out) + f"（{len(report.items)} 个任务）")
    shutil.rmtree(cfg_dir, ignore_errors=True)
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 README 界面预览图")
    ap.add_argument("--real", metavar="缓存目录", default=None,
                    help="改用真实缓存目录出图（不要提交这种图）")
    ap.add_argument("out", nargs="?", default=str(ROOT / "docs" / "gui.png"),
                    help="输出路径，默认 docs/gui.png")
    args = ap.parse_args()

    out = Path(args.out)

    if args.real:
        return render(Path(args.real), out, "示例：使用的是真实缓存目录（仅供本地查看）")

    root = _pick_demo_root()
    try:
        build_demo_cache(root)
        return render(root, out, f"示例：{len(DEMO_ITEMS)} 个任务（内置演示数据，非真实缓存）")
    finally:
        # 只删我们自己造的那几个 avid 目录，绝不碰 root 下别的东西。
        for i in range(1, len(DEMO_ITEMS) + 1):
            shutil.rmtree(root / str(100000000 + i), ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
