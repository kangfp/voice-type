"""配置读写。

配置文件是普通 JSON，放在 ~/.config/voice-type/config.json。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

APP_NAME = "voice-type"

DEFAULTS: dict[str, Any] = {
    # "local" = faster-whisper；"online" = OpenAI 兼容 /v1/audio/transcriptions
    "backend": "local",
    # faster-whisper 模型：tiny / base / small / medium / large-v3-turbo / large-v3
    "local_model": "large-v3-turbo",
    # 模型下载来源：huggingface / hf-mirror / modelscope（首次启动向导里选择）
    "model_source": "hf-mirror",
    # 首次启动向导是否已完成
    "setup_completed": False,
    "compute_type": "int8",
    "beam_size": 5,
    # 自动去掉前后静音，避免 Whisper 在静音/噪声上凭空生成文字
    "vad_filter": True,
    # 语言：zh / en / auto ...
    "language": "zh",
    # 在线 API
    "api_base_url": "",
    "api_key": "",
    "api_model": "whisper-1",
    # 录音设备，留空 = 系统默认麦克风
    "source": "",
    # 粘贴快捷键：ctrl+v 或 ctrl+shift+v（终端里通常用后者）
    "paste_shortcut": "ctrl+v",
    # 粘贴后是否恢复原来的剪贴板内容
    "restore_clipboard": True,
    # 右 Alt 按住说话：按下开始录音，松开识别
    "push_to_talk": True,
    # 全局快捷键（gsettings 语法）。默认避开 Word/WPS 的 Ctrl+Alt+V。
    "hotkey": "<Super><Alt>v",
    # 本地识别时的提示词（留空则中文自动使用简体提示）
    "initial_prompt": "",
}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / APP_NAME


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base)
    return Path(tempfile.gettempdir()) / f"{APP_NAME}-{os.getuid()}"


def socket_path() -> Path:
    return runtime_dir() / f"{APP_NAME}.sock"


def load_config(path: Path | None = None) -> dict[str, Any]:
    """读取配置；文件不存在或损坏时返回默认值（并保留坏文件备份）。"""
    target = path or config_path()
    cfg = dict(DEFAULTS)
    if not target.exists():
        return cfg
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if isinstance(data, dict):
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    return cfg


def save_config(cfg: dict[str, Any], path: Path | None = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULTS)
    merged.update({k: cfg[k] for k in DEFAULTS if k in cfg})
    fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return target
