"""Verified FFmpeg installation and persistent Windows PATH registration.

Downloads are unprivileged. Only copying to Program Files and updating machine
PATH run in an elevated Windows helper; the converter itself stays unelevated.
"""
from __future__ import annotations

import base64
import hashlib
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
CHECKSUM_URL = DOWNLOAD_URL + ".sha256"
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_BINARY = 300 * 1024 * 1024
Progress = Callable[[int, str], None]


class InstallCancelled(Exception):
    pass


def install_bin(system: bool = False) -> Path:
    if system:
        return Path(os.environ.get("ProgramW6432", os.environ.get("ProgramFiles", r"C:\Program Files"))) / "Bili_download_converter" / "ffmpeg" / "bin"
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Bili_download_converter" / "ffmpeg" / "bin"


def validate_ffmpeg(path: Path) -> bool:
    try:
        result = subprocess.run([str(path), "-version"], capture_output=True,
                                timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.returncode == 0 and result.stdout.lower().startswith(b"ffmpeg version")
    except (OSError, subprocess.TimeoutExpired):
        return False


def add_path_entry(value: str, entry: str) -> str:
    """Preserve existing PATH verbatim, including expandable variables."""
    def norm(p: str) -> str:
        return os.path.expandvars(p.strip().strip('"')).rstrip("/\\").casefold()
    if norm(entry) in {norm(p) for p in value.split(";") if p.strip()}:
        return value
    return value + (";" if value and not value.endswith(";") else "") + entry


def _broadcast_environment() -> None:
    import ctypes
    from ctypes import wintypes
    result = ctypes.c_size_t()
    send = ctypes.WinDLL("user32", use_last_error=True).SendMessageTimeoutW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                     wintypes.LPCWSTR, wintypes.UINT, wintypes.UINT,
                     ctypes.POINTER(ctypes.c_size_t)]
    send.restype = wintypes.LPARAM
    send(0xFFFF, 0x001A, 0, "Environment", 0x0002, 500, ctypes.byref(result))


def register_user_path(directory: Path) -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            value, kind = "", winreg.REG_EXPAND_SZ
        updated = add_path_entry(value, str(directory))
        if updated != value:
            winreg.SetValueEx(key, "Path", 0, kind, updated)
        if winreg.QueryValueEx(key, "Path")[0] != updated:
            raise OSError("用户 PATH 写入验证失败")
    _broadcast_environment()


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def system_install_script(source: Path, hashes: dict[str, str]) -> str:
    """Fixed destination is resolved in the elevated account, never from a form."""
    entries = "; ".join(f"{_ps_quote(n)} = {_ps_quote(h)}" for n, h in hashes.items())
    return r"""
$ErrorActionPreference = 'Stop'
try {
  $source = SOURCE
  $expected = @{ HASHES }
  $base = [Environment]::GetFolderPath('ProgramFiles')
  $dest = Join-Path $base 'Bili_download_converter\ffmpeg\bin'
  New-Item -ItemType Directory -Force -Path $dest | Out-Null
  foreach ($name in @('ffmpeg.exe', 'ffprobe.exe')) {
    $src = Join-Path $source $name
    if ((Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash -ne $expected[$name]) { throw 'Source checksum mismatch' }
    $dst = Join-Path $dest $name
    Copy-Item -LiteralPath $src -Destination $dst -Force
    if ((Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash -ne $expected[$name]) { throw 'Installed checksum mismatch' }
  }
  foreach ($name in @('LICENSE', 'README.txt')) {
    $src = Join-Path $source $name
    if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination (Join-Path $dest $name) -Force }
  }
  $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey('SYSTEM\CurrentControlSet\Control\Session Manager\Environment', $true)
  try {
    $value = $key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    $kind = $key.GetValueKind('Path')
    $found = @($value.Split(';') | Where-Object { [Environment]::ExpandEnvironmentVariables($_.Trim().Trim('"')).TrimEnd('\') -ieq $dest.TrimEnd('\') })
    if ($found.Count -eq 0) {
      $value = $value.TrimEnd(';') + ';' + $dest
      $key.SetValue('Path', $value, $kind)
    }
    if ($key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) -cne $value) { throw 'PATH verification failed' }
  } finally { $key.Close() }
  exit 0
} catch { exit 1 }
""".replace("SOURCE", _ps_quote(str(source))).replace("HASHES", entries)


def _run_elevated(script: str) -> None:
    import ctypes
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole.CoInitializeEx.restype = ctypes.c_long
    initialized = ole.CoInitializeEx(None, 2) in (0, 1)
    try:
        _run_elevated_windows(script)
    finally:
        if initialized:
            ole.CoUninitialize()


def _run_elevated_windows(script: str) -> None:
    import ctypes
    from ctypes import wintypes

    class ExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    info = ExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000100  # process handle + no async shell execution
    info.lpVerb = "runas"
    info.lpFile = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    info.lpParameters = "-NoProfile -NonInteractive -EncodedCommand " + encoded
    info.nShow = 0
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == 1223:
            raise InstallCancelled("已取消管理员授权。可取消勾选系统 PATH，改为当前用户安装。")
        raise ctypes.WinError(error)
    try:
        # No forced termination: never leave an elevated PATH edit half finished.
        if kernel.WaitForSingleObject(info.hProcess, 0xFFFFFFFF) != 0:
            raise OSError("无法等待管理员安装进程")
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(info.hProcess, ctypes.byref(code)) or code.value:
            raise OSError("系统安装或 PATH 写入失败，请检查权限后重试，或改为当前用户安装。")
    finally:
        kernel.CloseHandle(info.hProcess)


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise InstallCancelled("安装已取消，尚未修改 PATH。")


def download_archive(destination: Path, cancel: threading.Event, progress: Progress) -> None:
    request = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "Bili_download_converter/0.2"})
    with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as out:
        total = int(response.headers.get("Content-Length", 0))
        if total > MAX_ARCHIVE:
            raise ValueError("下载包超过大小限制")
        downloaded = 0
        while True:
            _check_cancel(cancel)
            data = response.read(256 * 1024)
            if not data:
                break
            downloaded += len(data)
            if downloaded > MAX_ARCHIVE:
                raise ValueError("下载包超过大小限制")
            out.write(data)
            percent = min(65, int(downloaded / total * 65)) if total else 0
            size = f"{downloaded / 1048576:.1f} MB"
            if total:
                size += f" / {total / 1048576:.1f} MB"
            progress(percent, "正在下载 FFmpeg · " + size)


def verify_archive(archive: Path) -> None:
    with urllib.request.urlopen(CHECKSUM_URL, timeout=30) as response:
        checksum = response.read(4096).decode("ascii").split()[0]
    if not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
        raise ValueError("下载源未提供有效的 SHA-256 校验值，请稍后重试")
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.casefold() != checksum.casefold():
        raise ValueError("下载包 SHA-256 校验失败，请重新下载")


def extract_tools(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for name in ("ffmpeg.exe", "ffprobe.exe", "LICENSE", "README.txt"):
            matches = [i for i in package.infolist() if not i.is_dir() and
                       i.filename.replace("\\", "/").split("/")[-1] == name]
            if len(matches) != 1:
                if name.endswith(".exe"):
                    raise ValueError(f"下载包缺少或包含重复的 {name}")
                continue
            member = matches[0]
            if member.file_size > MAX_BINARY:
                raise ValueError("解压文件超过大小限制")
            # Copy only named entries; never extract archive-supplied paths.
            with package.open(member) as src, (destination / name).open("wb") as dst:
                shutil.copyfileobj(src, dst)


def install_ffmpeg(system: bool, cancel: threading.Event, progress: Progress) -> Path:
    if os.name != "nt" or platform.machine().lower() not in ("amd64", "x86_64", "arm64"):
        raise OSError("一键安装支持 Windows 64 位系统；其他系统请手动安装 FFmpeg。")
    with tempfile.TemporaryDirectory(prefix="bili-ffmpeg-") as tmp:
        root = Path(tmp)
        archive = root / "ffmpeg.zip"
        download_archive(archive, cancel, progress)
        _check_cancel(cancel)
        progress(68, "正在校验下载包 · SHA-256")
        verify_archive(archive)
        _check_cancel(cancel)
        progress(74, "正在解压 FFmpeg 和 FFprobe")
        staging = root / "bin"
        extract_tools(archive, staging)
        if not validate_ffmpeg(staging / "ffmpeg.exe"):
            raise OSError("FFmpeg 无法在此电脑运行，安装未完成")
        _check_cancel(cancel)
        progress(82, "正在申请管理员权限并写入系统 PATH…" if system else "正在安装并写入用户 PATH…")
        target = install_bin(system)
        if system:
            hashes = {name: hashlib.sha256((staging / name).read_bytes()).hexdigest()
                      for name in ("ffmpeg.exe", "ffprobe.exe")}
            _run_elevated(system_install_script(staging, hashes))
            _broadcast_environment()
        else:
            target.mkdir(parents=True, exist_ok=True)
            for file in staging.iterdir():
                shutil.copy2(file, target / file.name)
            register_user_path(target)
        # Refresh this process too; already-open terminals must be reopened.
        os.environ["PATH"] = add_path_entry(os.environ.get("PATH", ""), str(target))
        progress(96, "正在验证安装结果")
        executable = target / "ffmpeg.exe"
        if not validate_ffmpeg(executable):
            raise OSError("已复制文件，但 FFmpeg 运行验证失败，请重试")
        progress(100, "FFmpeg 已安装，PATH 已更新")
        return executable
