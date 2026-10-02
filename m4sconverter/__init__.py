"""B站缓存转换器：把 B 站缓存里的 m4s 音视频合并成 mp4，并生成 ASS 弹幕。

模块划分::

    models.py      数据模型（VideoItem / Options / 枚举）
    utils.py       通用工具（文件名清洗 / 子进程 / 日志）
    config.py      选项持久化
    metadata.py    entry.json / index.json / .playurl / .videoInfo 解析与兜底
    scanner.py     缓存目录扫描与结构探测
    naming.py      输出路径渲染（模板 / 布局 / 冲突处理）
    danmaku/       弹幕 XML 解析与 ASS 生成
    mux.py         合并引擎（ffmpeg / MP4Box）
    hashfile.py    组合 MD5 与去重
    converter.py   单任务转换流水线
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
