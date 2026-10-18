"""通过 GNOME 自定义快捷键注册全局热键。

GNOME 46 的 Wayland 会话不允许普通程序全局抓按键，但 settings-daemon
的自定义快捷键会执行一条命令 —— 我们让命令调用 `voice-type toggle`。
"""

from __future__ import annotations

import os

MEDIA_KEYS_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
CUSTOM_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/voice-type/"
APP_NAME = "语音输入"


class HotkeyError(RuntimeError):
    pass


def _gio():
    try:
        from gi.repository import Gio
    except (ImportError, ValueError) as exc:  # pragma: no cover
        raise HotkeyError("缺少 PyGObject，无法配置 GNOME 快捷键") from exc
    return Gio


def _custom_settings():
    Gio = _gio()
    try:
        return Gio.Settings.new_with_path(CUSTOM_SCHEMA, CUSTOM_PATH)
    except Exception as exc:  # noqa: BLE001 - GLib.Error
        raise HotkeyError(f"找不到 GNOME 快捷键 schema：{exc}") from exc


def install(binding: str, command: str) -> None:
    Gio = _gio()
    try:
        media = Gio.Settings.new(MEDIA_KEYS_SCHEMA)
    except Exception as exc:  # noqa: BLE001
        raise HotkeyError(f"无法读取 GNOME 快捷键设置：{exc}") from exc
    paths = list(media.get_strv("custom-keybindings"))
    if CUSTOM_PATH not in paths:
        paths.append(CUSTOM_PATH)
        media.set_strv("custom-keybindings", paths)
    custom = _custom_settings()
    custom.set_string("name", APP_NAME)
    custom.set_string("command", command)
    custom.set_string("binding", binding)
    Gio.Settings.sync()


def uninstall() -> None:
    Gio = _gio()
    media = Gio.Settings.new(MEDIA_KEYS_SCHEMA)
    paths = [p for p in media.get_strv("custom-keybindings") if p != CUSTOM_PATH]
    media.set_strv("custom-keybindings", paths)
    try:
        custom = _custom_settings()
        for key in ("name", "command", "binding"):
            custom.reset(key)
    except HotkeyError:
        pass
    Gio.Settings.sync()


def info() -> dict[str, str | None]:
    Gio = _gio()
    media = Gio.Settings.new(MEDIA_KEYS_SCHEMA)
    installed = CUSTOM_PATH in media.get_strv("custom-keybindings")
    result: dict[str, str | None] = {
        "installed": "yes" if installed else "no",
        "binding": None,
        "command": None,
    }
    if installed:
        custom = _custom_settings()
        result["binding"] = custom.get_string("binding")
        result["command"] = custom.get_string("command")
    return result


def default_command() -> str:
    """热键要执行的命令（尽量用 ~/.local/bin/voice-type 包装脚本）。"""
    import shlex
    import shutil
    import sys

    exe = shutil.which("voice-type")
    if exe:
        return f"{shlex.quote(os.path.realpath(exe))} toggle"
    return f"{shlex.quote(sys.executable)} -m voice_type toggle"
