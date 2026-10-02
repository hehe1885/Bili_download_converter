"""弹幕获取：本地查找 + 缺失时联网下载。

两点加固：同时支持 gzip 与 deflate 两种压缩，以及按顺序尝试多个镜像/接口。
"""

from __future__ import annotations

import gzip
import logging
import urllib.error
import urllib.request
import zlib
from pathlib import Path

LOG = logging.getLogger("m4s")

COMMENT_URL = "https://comment.bilibili.com/{cid}.xml"
API_URL = "https://api.bilibili.com/x/v1/dm/list.so?oid={cid}"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def _decompress(data: bytes, encoding: str) -> bytes:
    """解压 HTTP 响应体；编码未知时按魔数嗅探。"""
    enc = (encoding or "").lower()
    if "gzip" in enc or data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except OSError:
            pass
    if "deflate" in enc or data[:1] == b"\x78":
        try:
            return zlib.decompress(data)
        except zlib.error:
            try:
                return zlib.decompress(data, -zlib.MAX_WBITS)
            except zlib.error:
                pass
    return data


def download_xml(cid: str, dest: Path, timeout: float = 10.0) -> Path | None:
    """下载 ``<cid>.xml`` 到 ``dest``，成功返回路径。"""
    if not cid:
        return None
    urls = [COMMENT_URL.format(cid=cid), API_URL.format(cid=cid)]
    for url in urls:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Referer": "https://www.bilibili.com/",
            "Accept-Encoding": "gzip, deflate",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                body = _decompress(raw, resp.headers.get("Content-Encoding", ""))
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            LOG.debug("下载弹幕失败 %s: %s", url, exc)
            continue

        if not body or b"<" not in body[:512]:
            LOG.debug("弹幕响应不是 XML: %s", url)
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
        except OSError as exc:
            LOG.warning("写入弹幕文件失败 %s: %s", dest, exc)
            return None
        LOG.info("已下载弹幕: %s (%d 字节)", dest.name, len(body))
        return dest

    LOG.warning("弹幕下载失败(cid=%s)，两个接口都不可用", cid)
    return None


def fetch_danmaku(local: Path | None, cid: str, cache_dir: Path,
                  allow_download: bool = True, timeout: float = 10.0) -> Path | None:
    """优先用本地 xml；没有则下载到 ``cache_dir/<cid>.xml``。"""
    if local is not None and local.is_file() and local.stat().st_size > 0:
        return local
    if not allow_download or not cid:
        return None
    dest = cache_dir / f"{cid}.xml"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    return download_xml(cid, dest, timeout=timeout)
