"""通用工具：文件名清洗、子进程调用、日志。"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Sequence

LOG = logging.getLogger("m4s")

# ---------------------------------------------------------------------------
# 文件名处理
# ---------------------------------------------------------------------------

#: 「替换」档的字符对照表。
#:
#: 只动 Windows 禁止的那 9 个字符 ``< > : " / \ | ? *``，把它们换成"长得像但合法"
#: 的替身——文件名既用得了，又一眼认得出来：
#:
#:     : ?  ->  ： ？      半角换全角
#:     / \  ->  ／ ＼      半角换全角
#:     | *  ->  ｜ ＊      半角换全角
#:     < >  ->  《 》      尖括号没有全角字形，改用中文书名号
#:     "    ->  '        半角双引号换单引号
#:
#: 其余字符一律不碰：``（）``、``【】``、空格、中文标点、emoji 全部原样保留。
#:
#: 设计原则：只放"不换就会出事"的字符。``（）``、``【】``、空格本身是合法的，
#: 换成半角反而把文件名改得认不出原标题，所以一律不动。
#: 替身也一律选**同形异码**的全角字，而不是 ``#`` / ``_`` / ``-`` ——
#: 后者跟原标题差得太远，一眼认不出是同一个视频。
_REPLACE_TABLE = str.maketrans(
    {
        "<": "《",
        ">": "》",
        '"': "'",
        ":": "：",
        "?": "？",
        "\\": "＼",
        "/": "／",
        "|": "｜",
        "*": "＊",
    }
)

#: 仅 Windows 绝对不允许出现在文件名里的字符。
_WIN_ILLEGAL_TABLE = str.maketrans({c: "_" for c in '<>:"/\\|?*'})

_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def replace_special(name: str) -> str:
    """「替换」档：把 Windows 禁止的字符换成"长得像但合法"的替身，并去掉首尾空白。

    只碰 :data:`_REPLACE_TABLE` 里那 9 个字符，其余（``（）``、``【】``、空格、
    中文标点）一律原样保留。

    两点约定：
    1. 换成**全角替身**（``／＼｜＊``、``：？``）或者 ``《》`` / ``'``，
       而不是 ``#`` / ``_`` / ``-``，这样文件名更接近原标题，见上面的表；
    2. **先 strip 再替换**，并且不替换空格——若先把空格换成 ``_`` 再 TrimSpace，
       首尾空格会变成首尾下划线（``"  x  "`` -> ``"__x__"``），那时已经救不回来。
    """
    if not name:
        return ""
    return name.strip().translate(_REPLACE_TABLE).strip()


def _windows_fixup(name: str) -> str:
    """Windows 会拒绝的边角情况：控制字符、结尾的点和空格、保留设备名。"""
    name = _CONTROL_RE.sub("", name)
    name = name.rstrip(" .")
    if not name:
        return ""
    stem = name.split(".")[0].upper()
    if stem in _WIN_RESERVED:
        name = "_" + name
    return name


def sanitize_name(name: str, mode: str = "safe") -> str:
    """按 ``mode`` 清洗一个路径片段。可用档位见 :class:`NameFilter`。

    ``safe``   "替换"（默认）：走 :func:`replace_special` 的对照表 + Windows 兜底；
    ``keep``   "最小改动"：只把 Windows 非法字符换成下划线，其余原样保留；
    ``go``     历史档位，等价于 ``safe`` 去掉兜底那一步，仅为兼容旧配置保留。
    """
    if not name:
        return ""
    mode = (mode or "safe").lower()
    if mode == "keep":
        return _windows_fixup(name.translate(_WIN_ILLEGAL_TABLE))
    if mode == "safe":
        return _windows_fixup(replace_special(name))
    return replace_special(name)


def sanitize_path_part(part: str, mode: str) -> str:
    """清洗模板渲染结果里的一段（可能是多级目录，用 / 分隔）。"""
    segs = [sanitize_name(s, mode) for s in re.split(r"[\\/]+", part)]
    return "/".join(s for s in segs if s)


def safe_join(root: Path, relative: str) -> Path:
    """把相对片段拼到 root 下，并阻止 ``..`` 逃逸。"""
    relative = relative.replace("\\", "/").lstrip("/")
    candidate = (root / relative).resolve()
    root_resolved = Path(root).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        raise ValueError(f"输出路径逃逸出根目录: {relative!r}") from None
    return candidate


# ---------------------------------------------------------------------------
# 杂项
# ---------------------------------------------------------------------------

def null2str(value: str | None, fallback: str) -> str:
    """空值兜底：取不到就返回 ``fallback``。"""
    return value if value else fallback


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0 or unit == "TB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024.0
    return f"{num:.1f} TB"


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def silent_remove(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    except OSError as exc:  # pragma: no cover - 权限问题
        LOG.debug("删除失败 %s: %s", path, exc)


def unique_path(path: Path) -> Path:
    """同名时返回 ``xxx_1.mp4`` / ``xxx_2.mp4`` …"""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for i in range(1, 10000):
        cand = parent / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
    raise FileExistsError(f"无法为 {path} 找到可用文件名")


def free_port_hint() -> int:  # pragma: no cover - 占位，GUI 阶段可能用到
    return 0


def run_command(
    args: Sequence[str],
    *,
    timeout: float | None = None,
    log_cb: Callable[[str], None] | None = None,
    cwd: str | Path | None = None,
) -> tuple[int, str]:
    """运行外部程序并返回 ``(returncode, 合并输出)``。

    使用 ``CREATE_NO_WINDOW``，避免 ffmpeg / MP4Box 弹黑框。
    """
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    try:
        proc = subprocess.run(
            [str(a) for a in args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
    except FileNotFoundError as exc:
        return 127, f"找不到可执行文件: {exc}"
    except subprocess.TimeoutExpired as exc:  # pragma: no cover
        out = exc.output.decode("utf-8", "replace") if exc.output else ""
        return 124, f"执行超时({timeout}s)\n{out}"
    text = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
    if log_cb and text.strip():
        for line in text.splitlines():
            if line.strip():
                log_cb(line.rstrip())
    return proc.returncode, text


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

class CallbackHandler(logging.Handler):
    """把日志转发给回调（CLI 打印 / GUI 日志面板）。"""

    def __init__(self, callback: Callable[[str], None], level: int = logging.INFO) -> None:
        super().__init__(level)
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - 简单转发
        try:
            self.callback(self.format(record))
        except Exception:  # noqa: BLE001 - 日志失败不能拖垮主流程
            self.handleError(record)


def setup_logging(
    verbose: bool = False,
    log_file: Path | None = None,
    callback: Callable[[str], None] | None = None,
) -> None:
    """配置根 logger：控制台 + 可选文件 + 可选回调。"""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)-5s] %(message)s", "%H:%M:%S")

    # 打包成 GUI 程序（pythonw / PyInstaller --windowed）时 stdout 可能是 None，
    # 此时不能挂 StreamHandler，否则每条日志都会走 handleError 往 stderr 抛。
    if sys.stdout is not None:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(fmt)
        stream.setLevel(logging.DEBUG if verbose else logging.INFO)
        root.addHandler(stream)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-5s] %(message)s",
                                          "%Y-%m-%d %H:%M:%S"))
        fh.setLevel(logging.DEBUG)
        root.addHandler(fh)

    if callback:
        cb = CallbackHandler(callback, logging.DEBUG if verbose else logging.INFO)
        cb.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-5s] %(message)s", "%H:%M:%S"))
        root.addHandler(cb)


def make_work_dir(base: str | Path | None = None, prefix: str = "m4s-conv-") -> Path:
    """创建一个临时工作目录（中间产物放这里，不污染缓存目录）。"""
    if base:
        root = Path(base)
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=prefix, dir=str(root)))
    return Path(tempfile.mkdtemp(prefix=prefix))


def which_all(*names: str) -> list[Path]:
    found: list[Path] = []
    for name in names:
        p = shutil.which(name)
        if p:
            found.append(Path(p))
    return found


def walk_files(root: Path) -> Iterable[Path]:
    """容错的递归文件遍历（跳过无权限目录）。"""
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            yield Path(entry.path)
                    except OSError:
                        continue
        except OSError as exc:
            LOG.debug("跳过目录 %s: %s", cur, exc)


def walk_dirs(root: Path) -> Iterable[Path]:
    stack = [root]
    while stack:
        cur = stack.pop()
        yield cur
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError as exc:
            LOG.debug("跳过目录 %s: %s", cur, exc)
