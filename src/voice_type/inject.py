"""把识别结果注入当前焦点窗口。

Wayland 下没有 XTest，通过 /dev/uinput 虚拟键盘模拟按键：
优先用内置的 uinput 实现（无外部依赖），ydotool 作为备用。
文字先放进剪贴板再按 Ctrl+V —— 中文/标点/emoji 都能可靠输入。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time

_SHORTCUT_RE = re.compile(r"^[A-Za-z0-9_+-]{1,64}$")


class InjectError(RuntimeError):
    def __init__(self, message: str, copied: bool = False) -> None:
        super().__init__(message)
        self.copied = copied


def validate_shortcut(shortcut: str) -> str:
    value = shortcut.strip().lower()
    if not _SHORTCUT_RE.match(value):
        raise InjectError(f"无效的粘贴快捷键：{shortcut!r}")
    return value


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise InjectError(f"找不到 {tool}，请运行 install.sh 安装系统依赖")
    return path


def _run(cmd: list[str], check: bool = True, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise InjectError(f"命令超时：{' '.join(cmd)}") from exc
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise InjectError(f"{cmd[0]} 执行失败（{proc.returncode}）：{detail}")
    return proc


def _read_clipboard_text() -> str | None:
    try:
        proc = _run(
            [_require("wl-paste"), "--no-newline", "--type", "text"],
            check=False,
            timeout=2.0,
        )
    except InjectError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _copy(text: str) -> None:
    wl_copy = _require("wl-copy")
    try:
        # 注意：wl-copy 会 fork 一个常驻进程持有 selection，必须把 stderr 指到
        # DEVNULL；否则它继承的管道会让 subprocess.run 一直等 EOF。
        proc = subprocess.run(
            [wl_copy, "--type", "text/plain;charset=utf-8"],
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5.0,
        )
    except subprocess.TimeoutExpired as exc:
        raise InjectError("写入剪贴板超时") from exc
    if proc.returncode != 0:
        raise InjectError(f"wl-copy 执行失败（退出码 {proc.returncode}）")


def _restore_later(previous: str, delay: float = 1.2) -> None:
    def worker() -> None:
        time.sleep(delay)
        try:
            _copy(previous)
        except InjectError:
            pass

    # 非 daemon：调用方进程退出前也会等恢复完成（否则短命 CLI 进程会丢恢复）
    threading.Thread(target=worker, daemon=False).start()


def read_clipboard_text() -> str | None:
    """读取当前剪贴板文本（流式注入开始前捕获一次用）。"""
    return _read_clipboard_text()


def copy_to_clipboard(text: str) -> None:
    """只写剪贴板，不模拟按键。"""
    _copy(text)


def schedule_clipboard_restore(previous: str, delay: float = 1.2) -> None:
    """延迟恢复剪贴板（流式注入全部结束后调用一次）。"""
    _restore_later(previous, delay)


def inject_text(
    text: str,
    paste_shortcut: str = "ctrl+v",
    restore_clipboard: bool = True,
) -> None:
    if not text.strip():
        return
    shortcut = validate_shortcut(paste_shortcut)

    # 先写剪贴板：即使按键注入不可用，用户也能手动粘贴，不会丢字。
    previous = _read_clipboard_text() if restore_clipboard else None
    _copy(text)
    # 给剪贴板管理器一点时间拿到 selection，再发按键
    time.sleep(0.15)
    try:
        _press_paste(shortcut)
    except InjectError as exc:
        raise InjectError(str(exc), copied=True) from exc
    if restore_clipboard and previous is not None:
        _restore_later(previous)


def _press_paste(shortcut: str) -> None:
    from . import uinput

    if uinput.available():
        try:
            uinput.send_shortcut(shortcut)
            return
        except uinput.UinputError:
            pass  # 落到 ydotool 备用路径
    ydotool = _require("ydotool")
    _run([ydotool, "key", shortcut])
