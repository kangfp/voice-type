"""原生 uinput 虚拟键盘：不依赖 ydotool/ydotoold。

Ubuntu 仓库的 ydotool 0.1.8 只有客户端没有守护进程，全新系统上不可用；
直接写 /dev/uinput 创建一个临时虚拟键盘，按下 Ctrl(+Shift)+V 后销毁。
需要 /dev/uinput 可写（input 组 + udev 规则，deb 包会自带规则）。
"""

from __future__ import annotations

import fcntl
import os
import struct
import time

UINPUT_PATH = "/dev/uinput"

EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0

KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_V = 47

UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

# struct input_event：timeval(2x long) + type(u16) + code(u16) + value(s32)
_EVENT = struct.Struct("@llHHi")
# struct uinput_user_dev：name[80] + input_id(4x u16) + ff_effects_max(u32)
# + absmax/absmin/absfuzz/absflat[64]（各 64x s32）
_UINPUT_DEV = struct.Struct("@80sHHHHi" + "i" * 64 * 4)

_SHORTCUT_KEYS = {
    "ctrl+v": (KEY_LEFTCTRL, KEY_V),
    "ctrl+shift+v": (KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_V),
}


class UinputError(RuntimeError):
    pass


def available() -> bool:
    return os.path.exists(UINPUT_PATH) and os.access(UINPUT_PATH, os.W_OK)


def _event(fd: int, etype: int, code: int, value: int) -> None:
    os.write(fd, _EVENT.pack(0, 0, etype, code, value))


def send_shortcut(shortcut: str) -> None:
    """创建虚拟键盘并按一次快捷键（如 ctrl+v / ctrl+shift+v）。"""
    keys = _SHORTCUT_KEYS.get(shortcut.strip().lower())
    if keys is None:
        raise UinputError(f"不支持的快捷键：{shortcut}")
    try:
        fd = os.open(UINPUT_PATH, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        raise UinputError(
            f"无法打开 {UINPUT_PATH}：{exc}（需要在 input 组并安装 udev 规则）"
        ) from exc
    try:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        for key in keys:
            fcntl.ioctl(fd, UI_SET_KEYBIT, key)
        # BUS_USB=0x03；abs 数组全 0（纯键盘设备）
        dev = _UINPUT_DEV.pack(b"voice-type-keyboard", 0x03, 0x1234, 0x5678, 1, 0, *([0] * 256))
        os.write(fd, dev)
        fcntl.ioctl(fd, UI_DEV_CREATE)
        # 给 libinput/合成器一点时间注册新设备，否则首批按键会被丢弃
        time.sleep(0.3)
        for key in keys:
            _event(fd, EV_KEY, key, 1)
        _event(fd, EV_SYN, SYN_REPORT, 0)
        time.sleep(0.05)
        for key in reversed(keys):
            _event(fd, EV_KEY, key, 0)
        _event(fd, EV_SYN, SYN_REPORT, 0)
        time.sleep(0.05)
    except OSError as exc:
        raise UinputError(f"uinput 注入失败：{exc}") from exc
    finally:
        try:
            fcntl.ioctl(fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(fd)
