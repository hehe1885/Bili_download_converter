"""选项持久化。"""

from __future__ import annotations

import dataclasses
import enum
import json
import logging
from pathlib import Path
from typing import Any

from .models import AssOptions, LayoutMode, Options

LOG = logging.getLogger("m4s")

DEFAULT_CONFIG_NAME = "config.json"


def _default_config_path() -> Path:
    """优先放程序目录；不可写时退回 %APPDATA%。"""
    here = Path(__file__).resolve().parent.parent
    if _is_writable(here):
        return here / DEFAULT_CONFIG_NAME
    import os

    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Bili_download_converter" / DEFAULT_CONFIG_NAME
    return Path.home() / ".Bili_download_converter" / DEFAULT_CONFIG_NAME


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _from_dict(cls: type, data: dict[str, Any]) -> Any:
    """按 dataclass 字段默认值的类型做宽松还原，忽略未知键。"""
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        raw = data[f.name]
        default = f.default
        if isinstance(default, enum.Enum):
            try:
                kwargs[f.name] = type(default)(raw)
            except ValueError:
                LOG.warning("配置项 %s=%r 非法，使用默认值 %r", f.name, raw, default)
        elif isinstance(default, bool):
            kwargs[f.name] = bool(raw)
        elif isinstance(default, int):
            try:
                kwargs[f.name] = int(raw)
            except (TypeError, ValueError):
                pass
        elif isinstance(default, float):
            try:
                kwargs[f.name] = float(raw)
            except (TypeError, ValueError):
                pass
        elif isinstance(default, str):
            kwargs[f.name] = str(raw)
        elif isinstance(default, list):
            kwargs[f.name] = list(raw) if isinstance(raw, (list, tuple)) else default
        elif f.name == "ass":
            kwargs["ass"] = _from_dict(AssOptions, raw or {})
    return cls(**kwargs)


def _to_dict(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        if isinstance(value, enum.Enum):
            out[f.name] = value.value
        elif dataclasses.is_dataclass(value):
            out[f.name] = _to_dict(value)
        elif isinstance(value, Path):
            out[f.name] = str(value)
        else:
            out[f.name] = value
    return out


def load_options(path: str | Path | None = None) -> Options:
    cfg = Path(path) if path else _default_config_path()
    if not cfg.is_file():
        return Options()
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("读取配置失败(%s)，使用默认选项: %s", cfg, exc)
        return Options()
    if not isinstance(data, dict):
        return Options()
    try:
        return _from_dict(Options, data)
    except TypeError as exc:  # pragma: no cover - 防御
        LOG.warning("配置结构不兼容，使用默认选项: %s", exc)
        return Options()


def save_options(options: Options, path: str | Path | None = None) -> Path:
    cfg = Path(path) if path else _default_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg.with_suffix(cfg.suffix + ".tmp")
    tmp.write_text(json.dumps(_to_dict(options), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cfg)
    return cfg


def default_config_path() -> Path:
    return _default_config_path()


__all__ = [
    "load_options",
    "save_options",
    "default_config_path",
    "LayoutMode",
    "Options",
    "AssOptions",
]
