"""环境自检：给 `voice-type doctor` 和 GUI 用。"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import hotkey, ipc
from .config import config_path, load_config


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""
    critical: bool = True


def _model_cache_dir(model: str) -> Path:
    home = os.environ.get("HF_HOME")
    root = Path(home) / "hub" if home else Path.home() / ".cache" / "huggingface" / "hub"
    repo = f"Systran/faster-whisper-{model}"
    try:
        from faster_whisper.utils import _MODELS

        repo = _MODELS.get(model, repo)
    except Exception:  # noqa: BLE001 - faster-whisper 未安装时退回默认命名
        pass
    return root / ("models--" + repo.replace("/", "--"))


def run_checks() -> list[Check]:
    checks: list[Check] = []

    # GTK / PyGObject
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk

        _ = (Adw, Gtk)
        checks.append(Check("GTK4 / libadwaita", True, "可用"))
    except Exception as exc:  # noqa: BLE001
        checks.append(
            Check(
                "GTK4 / libadwaita",
                False,
                str(exc),
                "sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1",
            )
        )

    # 命令行工具
    for tool, package in [
        ("pw-record", "pipewire-bin"),
        ("wl-copy", "wl-clipboard"),
        ("wl-paste", "wl-clipboard"),
        ("notify-send", "libnotify-bin"),
        ("ydotool", "ydotool"),
    ]:
        path = shutil.which(tool)
        checks.append(
            Check(
                f"命令 {tool}",
                bool(path),
                path or "未找到",
                f"sudo apt install {package}",
            )
        )

    # /dev/uinput 权限（ydotool 直接注入必需）
    uinput = Path("/dev/uinput")
    readable = uinput.exists() and os.access(uinput, os.R_OK | os.W_OK)
    checks.append(
        Check(
            "/dev/uinput 可写",
            readable,
            "可写（input 组）" if readable else "不可写",
            "sudo usermod -aG input $USER 后重新登录",
        )
    )

    # 右 Alt 按住说话：需要读取键盘事件设备
    cfg = load_config()
    if cfg.get("push_to_talk", True):
        from . import ptt

        keyboards = ptt.discover_keyboards()
        readable_keyboards = [p for p in keyboards if os.access(p, os.R_OK)]
        checks.append(
            Check(
                "右 Alt 按住说话",
                bool(readable_keyboards),
                (
                    f"{len(readable_keyboards)}/{len(keyboards)} 个键盘设备可读"
                    if keyboards
                    else "未发现键盘事件设备"
                ),
                "sudo usermod -aG input $USER 后重新登录",
                critical=False,
            )
        )

    # 服务状态
    running = ipc.is_running()
    checks.append(
        Check(
            "后台服务",
            running,
            "正在运行" if running else "未运行",
            "~/.local/bin/voice-type gui --hidden &",
            critical=False,
        )
    )

    # 快捷键
    try:
        hk = hotkey.info()
        installed = hk.get("installed") == "yes"
        checks.append(
            Check(
                "全局快捷键",
                installed,
                f"{hk.get('binding')} → {hk.get('command')}" if installed else "未安装",
                "voice-type install-hotkey",
                critical=False,
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("全局快捷键", False, str(exc), "voice-type install-hotkey", critical=False))

    # 配置与模型
    checks.append(Check("配置文件", True, str(config_path()), critical=False))
    if cfg.get("backend") == "online":
        url = str(cfg.get("api_base_url") or "").strip()
        checks.append(
            Check(
                "在线 API 配置",
                bool(url),
                url or "Base URL 为空",
                "在设置里填写 API Base URL",
            )
        )
    else:
        model = str(cfg.get("local_model") or "large-v3-turbo")
        cache = _model_cache_dir(model)
        cached = cache.exists()
        checks.append(
            Check(
                "本地模型缓存",
                cached,
                f"{model} 已缓存" if cached else f"{model} 未缓存（首次识别会下载）",
                "voice-type download-model",
                critical=False,
            )
        )

    return checks


def has_failure(checks: list[Check]) -> bool:
    return any(c.critical and not c.ok for c in checks)


def format_checks(checks: list[Check]) -> str:
    lines: list[str] = []
    for check in checks:
        mark = "✓" if check.ok else ("✗" if check.critical else "!")
        line = f"{mark} {check.name}：{check.detail}"
        if not check.ok and check.hint:
            line += f"\n    → {check.hint}"
        lines.append(line)
    return "\n".join(lines)
