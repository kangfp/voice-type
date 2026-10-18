"""桌面通知（libnotify）。"""

from __future__ import annotations

import shutil
import subprocess


def notify(title: str, body: str = "", urgency: str = "normal", icon: str = "audio-input-microphone") -> None:
    if not shutil.which("notify-send"):
        return
    cmd = ["notify-send", "--app-name=语音输入", f"--urgency={urgency}"]
    if icon:
        cmd.append(f"--icon={icon}")
    cmd.extend([title, body])
    try:
        subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass
