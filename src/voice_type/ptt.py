"""右 Alt 按住说话（push-to-talk）。

GNOME 的自定义快捷键只有“按下”没有“松开”，也无法把单独的 Alt 当键，
所以直接读取 /dev/input/event*（用户需在 input 组）。读取是只读的，
不 grab 设备，右 Alt / AltGr 原本的字符输入功能不受影响。
"""

from __future__ import annotations

import glob
import os
import select
import struct
import threading
import time
from typing import Callable

EV_KEY = 0x01
KEY_RIGHTALT = 100
EVENT = struct.Struct("@llHHi")
EVENT_SIZE = EVENT.size


class PushToTalkError(RuntimeError):
    pass


def discover_keyboards() -> list[str]:
    """通过 udev 的 *-event-kbd 链接找到所有键盘事件设备。"""
    paths: list[str] = []
    for pattern in ("/dev/input/by-id/*-event-kbd", "/dev/input/by-path/*-event-kbd"):
        for link in glob.glob(pattern):
            real = os.path.realpath(link)
            if real not in paths:
                paths.append(real)
    return paths


class PushToTalkListener:
    """监听右 Alt 的按下/松开，驱动 Controller 开始/停止录音。"""

    def __init__(
        self,
        controller,
        min_ms: int = 350,
        devices: list[str] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.controller = controller
        self.min_ms = max(0, int(min_ms))
        self.devices = devices
        self.on_error = on_error
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._ptt_active = False
        self._press_time = 0.0
        self._supervisor: threading.Thread | None = None
        self._error_times: dict[str, float] = {}

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self.devices is not None:
            if not self.devices:
                raise PushToTalkError("没有可监听的键盘设备")
            for path in self.devices:
                self._spawn_reader(path)
            return
        if not discover_keyboards():
            raise PushToTalkError(
                "找不到键盘设备；请确认当前用户在 input 组（需要重新登录）"
            )
        self._supervisor = threading.Thread(target=self._supervise, daemon=True)
        self._supervisor.start()

    def stop(self) -> None:
        self._stop.set()

    # ---------- 设备监听 ----------

    def _supervise(self) -> None:
        readers: dict[str, threading.Thread] = {}
        while not self._stop.is_set():
            for path in list(readers):
                if not readers[path].is_alive():
                    del readers[path]
            paths = discover_keyboards()
            if not paths:
                self._report("discover", "找不到可读的键盘设备（input 组权限？）")
            for path in paths:
                if path not in readers:
                    readers[path] = self._spawn_reader(path)
            self._stop.wait(2.0)

    def _spawn_reader(self, path: str) -> threading.Thread:
        thread = threading.Thread(
            target=self._read_loop, args=(path,), daemon=True
        )
        thread.start()
        return thread

    def _read_loop(self, path: str) -> None:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            self._report(path, f"无法读取 {path}：{exc}")
            return
        buffer = b""
        try:
            while not self._stop.is_set():
                try:
                    ready, _, _ = select.select([fd], [], [], 0.5)
                except (OSError, ValueError):
                    break
                if not ready:
                    continue
                try:
                    chunk = os.read(fd, EVENT_SIZE * 32)
                except BlockingIOError:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
                while len(buffer) >= EVENT_SIZE:
                    raw, buffer = buffer[:EVENT_SIZE], buffer[EVENT_SIZE:]
                    try:
                        _sec, _usec, etype, code, value = EVENT.unpack(raw)
                    except struct.error:
                        continue
                    if etype == EV_KEY and code == KEY_RIGHTALT:
                        self._handle(value)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def _report(self, key: str, message: str) -> None:
        now = time.monotonic()
        if now - self._error_times.get(key, 0.0) < 60.0:
            return
        self._error_times[key] = now
        if self.on_error:
            self.on_error(message)

    # ---------- 按下 / 松开 ----------

    def _handle(self, value: int) -> None:
        if value == 1:
            self._on_press()
        elif value == 0:
            self._on_release()
        # value == 2 是自动重复，忽略

    def _on_press(self) -> None:
        with self._lock:
            if self._ptt_active:
                return
            if getattr(self.controller, "state", "idle") != "idle":
                return
            self._ptt_active = True
            self._press_time = time.monotonic()
        self.controller.start()
        if getattr(self.controller, "state", "idle") != "recording":
            with self._lock:
                self._ptt_active = False

    def _on_release(self) -> None:
        with self._lock:
            if not self._ptt_active:
                return
            self._ptt_active = False
            elapsed_ms = (time.monotonic() - self._press_time) * 1000.0
        if elapsed_ms < self.min_ms:
            self.controller.cancel()
        else:
            self.controller.stop()
