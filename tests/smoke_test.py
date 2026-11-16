"""离线冒烟测试：不依赖网络、不依赖真实缓存目录。

只覆盖纯逻辑单元（命名、过滤、ASS 时间/颜色、hash、m4s 头探测），
真实转换请用 ``python cli.py -i <目录> --dry-run``。

运行::

    python tests/smoke_test.py
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from m4sconverter.danmaku.ass_writer import (  # noqa: E402
    AssConfig,
    Bullet,
    BulletType,
    _RollTrack,
    parse_argb,
    parse_convert_rule,
    time_to_string,
    write_ass,
)
from m4sconverter.danmaku.xml_parser import parse_bullet_p, parse_danmaku_pb  # noqa: E402
from m4sconverter.hashfile import combined_md5  # noqa: E402
from m4sconverter.metadata import normalize, parse_track_hint  # noqa: E402
from m4sconverter.models import LayoutMode, NameFilter, Options, VideoItem  # noqa: E402
from m4sconverter.mux import _sniff_handler_type, find_payload_offset  # noqa: E402
from m4sconverter.scanner import pair_m4s  # noqa: E402
from m4sconverter.naming import (  # noqa: E402
    build_context,
    render_template,
    resolve_output_path,
)
from m4sconverter.utils import replace_special, sanitize_name, unique_path  # noqa: E402

FAILED: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}\n         got ={got!r}\n         want={want!r}")
        FAILED.append(label)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def make_item() -> VideoItem:
    item = VideoItem(media_dir=Path("X:/dl/116278203322248/c_36914136347/112"))
    item.title = "示例视频第一讲"
    item.part = "P1"
    item.up = "示例UP主"
    item.group = "某UP"
    item.avid = "116278203322248"
    item.cid = "36914136347"
    item.qid = "112"
    item.quality = "1080P"
    item.width = 1080
    item.height = 1920
    item.duration_ms = 141666
    item.danmaku_count = 595
    item.index = 1
    return item


# ---------------------------------------------------------------------------
def test_filter() -> None:
    section("「替换」档改名规则")
    check("（）原样保留", replace_special("标题（补档）"), "标题（补档）")
    check("【】原样保留", replace_special("【测试】示例"), "【测试】示例")
    check("<>->《》", replace_special("a<b>c"), "a《b》c")
    check("/->／ 全角", replace_special("演练-2021/04/15"), "演练-2021／04／15")
    check("\\->＼ 全角", replace_special("a\\b"), "a＼b")
    check('"->\'', replace_special('他说"你好"'), "他说'你好'")
    check("|->｜ 全角", replace_special("a|b"), "a｜b")
    check("?->？", replace_special("为什么?"), "为什么？")
    check("*->＊ 全角", replace_special("a*b"), "a＊b")
    check(":->：", replace_special("12:30"), "12：30")
    check("空格原样保留", replace_special("a b"), "a b")
    check("中文标点原样保留", replace_special("标题、带《书名号》和，逗号"), "标题、带《书名号》和，逗号")
    check("首尾裁剪", replace_special("  空格  "), "空格")
    check("替换后不含半角非法字符",
          set(replace_special("a/b\\c:d*e?f|g\"h<i>j")) & set('<>:"/\\|?*'), set())
    check("替换档去控制字符", sanitize_name("a\x01b", NameFilter.STANDARD), "ab")
    check("替换档去尾点", sanitize_name("标题...", NameFilter.STANDARD), "标题")
    check("替换档避开保留名", sanitize_name("CON", NameFilter.STANDARD), "_CON")
    check("最小改动保留空格与全角", sanitize_name("【补档】a b", NameFilter.MINIMAL), "【补档】a b")
    check("最小改动只换非法字符",
          sanitize_name('a/b\\c:d*e?f|g"h', NameFilter.MINIMAL), "a_b_c_d_e_f_g_h")
    # 旧配置里的 "go" 要能静默迁移到 STANDARD，否则老用户一打开就报错。
    check("旧值 go 迁移", NameFilter("go"), NameFilter.STANDARD)
    check("只有两档", len(list(NameFilter)), 2)


def test_naming() -> None:
    section("命名模板")
    item = make_item()
    ctx = build_context(item, "mp4")

    check("基础模板", render_template("{title}.{ext}", ctx)[0],
          "示例视频第一讲.mp4")
    check("分组模板", render_template("{group}-{up}/{part}.{ext}", ctx)[0],
          "某UP-示例UP主/P1.mp4")
    check("质量占位", render_template("{title} [{quality}].{ext}", ctx)[0],
          "示例视频第一讲 [1080P].mp4")
    check("序号补零", render_template("{index:03d}_{title}.{ext}", ctx)[0],
          "001_示例视频第一讲.mp4")
    check("未知占位符被报出", render_template("{bogus}/{title}.{ext}", ctx)[1], ["bogus"])
    check("primary_title", item.primary_title(), "示例视频第一讲")
    check("duration_s", round(item.duration_s, 3), 141.666)


def test_path_safety() -> None:
    """回归测试：标题里的 / 和 \\ 绝不能变成嵌套目录。"""
    section("路径安全（回归）")
    root = Path("X:/out")
    opts = Options(output_dir=root, layout=LayoutMode.FLAT)

    slash = make_item()
    slash.title = "示例视频-2026/05/20"
    path, _ = resolve_output_path(slash, opts, root)
    check("斜杠标题不产生嵌套目录", path.parent, root)
    check("斜杠标题 -> 全角／", path.name, "示例视频-2026／05／20.mp4")

    back = make_item()
    back.title = "示例视频-2024\\06\\07"
    path, _ = resolve_output_path(back, opts, root)
    check("反斜杠标题不产生嵌套目录", path.parent, root)
    check("反斜杠标题 -> 全角＼", path.name, "示例视频-2024＼06＼07.mp4")

    dot = make_item()
    dot.title = "标题.."
    path, _ = resolve_output_path(dot, Options(output_dir=root, layout=LayoutMode.FLAT,
                                               name_filter=NameFilter.STANDARD), root)
    check("safe 模式去尾部点", path.name, "标题.mp4")

    # 模板里显式写的 / 仍然应该生成目录层级
    nested = Options(output_dir=root, layout=LayoutMode.TEMPLATE,
                     template="{group}-{up}/{title}.{ext}")
    path, _ = resolve_output_path(make_item(), nested, root)
    check("模板的 / 仍产生目录", path.parent.name, "某UP-示例UP主")
    check("模板的 / 文件名", path.name, "示例视频第一讲.mp4")

    # 逃逸防护
    esc = Options(output_dir=root, layout=LayoutMode.TEMPLATE, template="../../evil.{ext}")
    path, warns = resolve_output_path(make_item(), esc, root)
    check("../ 被挡在输出目录内", root in path.parents or path.parent == root, True)


def test_ass_time_and_color() -> None:
    section("ASS 时间与颜色")
    check("0 毫秒", time_to_string(0), "0:00:00.00")
    check("400 毫秒", time_to_string(400), "0:00:00.40")
    check("141666 毫秒", time_to_string(141666), "0:02:21.66")
    check("毫秒截断(非四舍五入)", time_to_string(1999), "0:00:01.99")
    check("3661000 毫秒", time_to_string(3661000), "1:01:01.00")
    check("alpha 0.3 + 白", hex(parse_argb(0.3, "0xFFFFFF")), hex(0x4CFFFFFF))
    check("alpha 1.0 + 黑", hex(parse_argb(1.0, "0x000000")), hex(0xFF000000))


def test_ass_text() -> None:
    section("ASS 文本生成")
    # p 字段顺序：0=时间(秒) 1=模式 2=字号 3=颜色(十进制RGB) 4..7=其它
    check("类型 1 滚动", parse_bullet_p("1.0,1,25,16777215,0,0,0,0", "hi").type, BulletType.ROLL)
    check("类型 5 顶部", parse_bullet_p("1.0,5,25,16777215,0,0,0,0", "hi").type, BulletType.TOP)
    check("类型 4 底部", parse_bullet_p("1.0,4,25,16777215,0,0,0,0", "hi").type, BulletType.BOTTOM)
    check("类型 6 滚动", parse_bullet_p("1.0,6,25,16777215,0,0,0,0", "hi").type, BulletType.ROLL)
    check("类型 7 丢弃", parse_bullet_p("1.0,7,25,16777215,0,0,0,0", "hi"), None)
    check("未知类型默认滚动", parse_bullet_p("1.0,9,25,16777215,0,0,0,0", "hi").type,
          BulletType.ROLL)
    check("p 字段不足丢弃", parse_bullet_p("1,2,25", "hi"), None)
    check("秒 -> 毫秒", parse_bullet_p("1.5,1,25,16777215,0,0,0,0", "hi").time_ms, 1500)
    check("白 -> &HFFFFFF",
          parse_bullet_p("1.0,1,25,16777215,0,0,0,0", "hi").ass_color(), "&HFFFFFF")
    check("红 -> &H0000FF (BBGGRR)",
          parse_bullet_p("1.0,1,25,16711680,0,0,0,0", "hi").ass_color(), "&H0000FF")
    check("换行转 \\N", parse_bullet_p("1.0,1,25,16777215,0,0,0,0", "a\nb").text, "a\\Nb")
    check("convert 规则 s -> r", parse_convert_rule("s -> r"),
          {BulletType.SC: BulletType.ROLL})
    check("convert 规则 t -> 丢弃", parse_convert_rule("t -> _"),
          {BulletType.TOP: None})

    cfg = AssConfig(width=1920, height=1080)
    check("轨道数 1080/26", len(_RollTrack(cfg).time_point), 41)
    check("竖屏轨道数 1920/26", len(_RollTrack(AssConfig(width=1080, height=1920)).time_point), 73)

    bullets = [
        Bullet(time_ms=400, type=BulletType.ROLL, rgb=0xFFFFFF, text="测试滚动"),
        Bullet(time_ms=1000, type=BulletType.TOP, rgb=0xFF0000, text="测试顶部"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "t.ass"
        stat = write_ass(bullets, cfg, out, ("1", "up", "title"))
        text = out.read_text(encoding="utf-8")
        check("写出条数", stat["written"], 2)
        check("头部 PlayResX", "PlayResX: 1920" in text, True)
        check("头部房间号", "; Roomid: 1 Name: up Title: title" in text, True)
        check("样式 4 条", sum(1 for ln in text.splitlines() if ln.startswith("Style: ")), 4)
        check("滚动用 move", "{\\move(" in text, True)
        check("固定用 pos", "{\\pos(" in text, True)
        check("颜色 BBGGRR", "\\c&H0000FF" in text, True)
        check("结尾换行", text.endswith("\n"), True)

        # 0 条弹幕也要产出合法文件
        empty = Path(tmp) / "empty.ass"
        write_ass([], cfg, empty, ("", "", ""))
        check("空弹幕仍写出header", "PlayResX: 1920" in empty.read_text(encoding="utf-8"), True)


def test_hash() -> None:
    section("hash 与 m4s 头剥离")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        v, a = d / "v.bin", d / "a.bin"
        v.write_bytes(b"VIDEO" * 100)
        a.write_bytes(b"AUDIO" * 100)
        want = hashlib.md5(b"VIDEO" * 100 + b"AUDIO" * 100).hexdigest()
        got = combined_md5(v, a)
        check("md5(视频字节 + 音频字节)", got, want)
        check("32 位小写 hex", len(got), 32)

    check("ftyp 在 0 偏移 -> 不剥离", find_payload_offset(b"\x00\x00\x00\x24ftypisom"), 0)
    check("9 个 ASCII 0 前缀 -> 剥离 9",
          find_payload_offset(b"000000000\x00\x00\x00\x24ftyp"), 9)
    check("非法头回退找 ftyp", find_payload_offset(b"\x01\x02\x03\x04ftypisom"), 0)


def test_unique_path() -> None:
    section("同名去重")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "a.mp4"
        check("不存在时原样", unique_path(p), p)
        p.write_bytes(b"x")
        check("存在时加 _1", unique_path(p).name, "a_1.mp4")
        (Path(tmp) / "a_1.mp4").write_bytes(b"x")
        check("再冲突加 _2", unique_path(p).name, "a_2.mp4")


def test_pc_cache() -> None:
    section("PC 端缓存识别与配对")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "43617619619"
        d.mkdir()
        (d / "videoInfo.json").write_text(
            '{"type":"ugc","cid":43617619619,"title":"测试PC视频","uname":"测试UP",'
            '"duration":294,"danmaku":42,"qn":112,"status":"completed","p":1,"tabName":"P1"}',
            encoding="utf-8",
        )
        (d / ".playurl").write_text(
            '{"code":0,"data":{"quality":112,"timelength":293988,'
            '"support_formats":[{"quality":112,"display_desc":"1080P","superscript":"高码率"}],'
            '"dash":{"video":[{"id":112,"width":1920,"height":1080,'
            '"baseUrl":"https://example.com/43617619619-1-30112.m4s"}],'
            '"audio":[{"id":30280,"baseUrl":"https://example.com/43617619619-1-30280.m4s"}]}}}',
            encoding="utf-8",
        )
        v_file = d / "43617619619-1-30112.m4s"
        a_file = d / "43617619619-1-30280.m4s"
        # 构造带 9 字节 '000000000' 前缀的最小 ftyp + moov/trak/mdia/hdlr 结构
        hdlr_v = b"\x00\x00\x00\x21hdlr\x00\x00\x00\x00\x00\x00\x00\x00vide" + b"\x00" * 13
        hdlr_a = b"\x00\x00\x00\x21hdlr\x00\x00\x00\x00\x00\x00\x00\x00soun" + b"\x00" * 13

        def wrap_box(btype: bytes, payload: bytes) -> bytes:
            return (len(payload) + 8).to_bytes(4, "big") + btype + payload

        ftyp = wrap_box(b"ftyp", b"iso5\x00\x00\x02\x00iso5iso6")
        v_file.write_bytes(b"000000000" + ftyp + wrap_box(b"moov", wrap_box(b"trak", wrap_box(b"mdia", hdlr_v))))
        a_file.write_bytes(b"000000000" + ftyp + wrap_box(b"moov", wrap_box(b"trak", wrap_box(b"mdia", hdlr_a))))

        check("带 000000000 前缀嗅探 video", _sniff_handler_type(v_file), "video")
        check("带 000000000 前缀嗅探 audio", _sniff_handler_type(a_file), "audio")

        hint = parse_track_hint(d / "videoInfo.json", "videoInfo.json", d)
        check("PC .playurl 提取 30112 视频轨道", "30112" in hint.video_ids, True)
        check("PC .playurl 提取 30280 音频轨道", "30280" in hint.audio_ids, True)

        vp, ap, how = pair_m4s([v_file, a_file], hint)
        check("PC m4s 配对成功", (vp, ap, how), (v_file, a_file, "hint"))

        fields = normalize(
            {"cid": 43617619619, "title": "测试PC视频", "uname": "测试UP",
             "duration": 294, "danmaku": 42, "qn": 112, "status": "completed"},
            "videoInfo.json",
            d,
        )
        check("PC 画质号 qn", fields["qid"], "112")
        check("PC 画质描述", fields["quality"], "1080P 高码率")
        check("PC 弹幕数", fields["danmaku_count"], 42)
        check("PC 毫秒时长", fields["duration_ms"], 293988)
        check("PC 宽高", (fields["width"], fields["height"]), (1920, 1080))


def main() -> int:
    print("B站缓存转换器 —— 离线冒烟测试")
    test_filter()
    test_naming()
    test_path_safety()
    test_ass_time_and_color()
    test_ass_text()
    test_hash()
    test_unique_path()
    test_pc_cache()
    print("\n" + "=" * 60)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for name in FAILED:
            print(f"  - {name}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
