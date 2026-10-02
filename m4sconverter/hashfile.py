"""组合 MD5 与去重。

固定格式，便于跨版本去重：
``md5(视频字节 + 音频字节)`` 的 32 位小写 hex，与 mp4 同目录同主名的 ``.hash``。
哈希对象是「剥离前缀后的」音视频数据，顺序固定为先视频后音频。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

LOG = logging.getLogger("m4s")

HASH_SUFFIX = ".hash"
_CHUNK = 1 << 20
#: 拿不到 hash 时，文件大小相差不超过这个值就视为同一份。
SIZE_TOLERANCE = 1 << 20


def combined_md5(video: Path, audio: Path) -> str:
    """``md5(video_bytes || audio_bytes)``；任一路读取失败返回空串。"""
    digest = hashlib.md5()
    for path in (video, audio):
        try:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError as exc:
            LOG.warning("计算哈希失败 %s: %s", path, exc)
            return ""
    return digest.hexdigest()


def hash_path_for(media: Path) -> Path:
    """``xxx.mp4`` → ``xxx.hash``（用 ``with_suffix``，避免全路径字符串替换的坑）。"""
    return media.with_suffix(HASH_SUFFIX)


def read_hash(media: Path) -> str:
    path = hash_path_for(media)
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def write_hash(media: Path, value: str) -> bool:
    if not value:
        return False
    path = hash_path_for(media)
    try:
        path.write_text(value, encoding="utf-8")
    except OSError as exc:
        LOG.warning("写入 hash 失败 %s: %s", path, exc)
        return False
    return True


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def find_duplicate(
    media: Path,
    video: Path,
    audio: Path,
    input_hash: str = "",
) -> Path | None:
    """在同一目录里找内容相同的已存在文件。

    先比 ``.hash``（精确），再退化为「大小相差 ≤1MB」的近似判断。
    """
    if not media.exists():
        return None

    if input_hash:
        if read_hash(media) == input_hash:
            return media

    target_size = _size(video) + _size(audio)
    if target_size and abs(_size(media) - target_size) <= SIZE_TOLERANCE:
        return media

    directory = media.parent
    try:
        siblings = [p for p in directory.iterdir() if p.is_file()
                    and p.suffix.lower() in (".mp4", ".mkv")]
    except OSError:
        return None
    for other in siblings:
        if other == media:
            continue
        if input_hash and read_hash(other) == input_hash:
            return other
        if target_size and abs(_size(other) - target_size) <= SIZE_TOLERANCE:
            return other
    return None


def cleanup_hashes_orphans(directory: Path) -> None:  # pragma: no cover - 暂未使用
    """删除没有同名 mp4 的孤儿 .hash。"""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    stems = {p.stem for p in entries if p.suffix.lower() in (".mp4", ".mkv")}
    for p in entries:
        if p.suffix.lower() == HASH_SUFFIX and p.stem not in stems:
            try:
                p.unlink()
            except OSError:
                continue


__all__ = [
    "combined_md5",
    "hash_path_for",
    "read_hash",
    "write_hash",
    "find_duplicate",
    "cleanup_hashes_orphans",
    "HASH_SUFFIX",
    "SIZE_TOLERANCE",
]
