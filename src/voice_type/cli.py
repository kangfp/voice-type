"""voice-type 命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, hotkey, ipc
from .config import load_config, save_config, state_dir


def _dist_packages_env() -> dict[str, str]:
    env = os.environ.copy()
    dist = "/usr/lib/python3/dist-packages"
    if Path(dist).is_dir() and dist not in env.get("PYTHONPATH", ""):
        env["PYTHONPATH"] = dist + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _gui_log_path() -> Path:
    return state_dir() / "gui.log"


def _spawn_daemon(visible: bool, wait: float = 12.0) -> bool:
    log_path = _gui_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "voice_type", "gui"]
    if not visible:
        cmd.append("--hidden")
    with open(log_path, "ab") as log:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            cwd=str(Path.home()),
            env=_dist_packages_env(),
        )
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if ipc.is_running(timeout=0.3):
            return True
        time.sleep(0.15)
    return False


def _send(cmd: str, allow_spawn: bool = True) -> dict:
    try:
        return ipc.send_command(cmd)
    except ipc.IpcError:
        if not allow_spawn:
            raise
    if cmd == "show":
        if not _spawn_daemon(visible=True):
            raise ipc.IpcError(f"无法启动后台服务，请查看 {_gui_log_path()}")
        return {"ok": True, "state": "starting", "message": "已启动"}
    if not _spawn_daemon(visible=False):
        raise ipc.IpcError(f"无法启动后台服务，请查看 {_gui_log_path()}")
    return ipc.send_command(cmd)


def _print_status(resp: dict) -> None:
    state = str(resp.get("state") or "unknown")
    labels = {
        "idle": "空闲",
        "recording": "🎙 正在录音… 再按一次快捷键停止",
        "transcribing": "正在识别…",
        "starting": "正在启动…",
        "offline": "服务未运行",
    }
    print(labels.get(state, state))
    if resp.get("last_error"):
        print(f"上次错误：{resp['last_error']}", file=sys.stderr)


def _cmd_gui(args: argparse.Namespace) -> int:
    from .gui import VoiceTypeApplication

    app = VoiceTypeApplication(start_hidden=args.hidden)
    return int(app.run([sys.argv[0]]) or 0)


def _cmd_toggle_like(args: argparse.Namespace) -> int:
    # 只有 show 允许自动拉起后台；toggle/start/stop 要求应用已经打开，
    # 这样关闭窗口就是真正退出，不会被快捷键悄悄复活。
    allow_spawn = args.command == "show"
    try:
        resp = _send(args.command, allow_spawn=allow_spawn)
    except ipc.IpcError as exc:
        if args.command == "status":
            print(json.dumps({"ok": False, "state": "offline", "error": str(exc)}, ensure_ascii=False, indent=2))
        elif args.command == "quit":
            print(f"✗ {exc}", file=sys.stderr)
        else:
            from .notify import notify

            notify("语音输入未运行", "请先从应用菜单打开「语音输入」", urgency="normal")
            print(f"✗ {exc}", file=sys.stderr)
        return 1
    if args.command == "status":
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    elif args.command == "show":
        print(resp.get("message") or "已显示窗口")
    elif args.command == "quit":
        print("已退出")
    else:
        _print_status(resp)
    return 0 if resp.get("ok", True) else 1


def _cmd_install_hotkey(args: argparse.Namespace) -> int:
    cfg = load_config()
    binding = args.binding or str(cfg.get("hotkey") or "<Super><Alt>v")
    command = hotkey.default_command()
    try:
        hotkey.install(binding, command)
    except hotkey.HotkeyError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    cfg["hotkey"] = binding
    save_config(cfg)
    print(f"✓ 全局快捷键已设置：{binding}")
    print(f"  执行命令：{command}")
    print("  可在「GNOME 设置 → 键盘 → 自定义快捷键」中修改。")
    return 0


def _cmd_uninstall_hotkey(_: argparse.Namespace) -> int:
    try:
        hotkey.uninstall()
    except hotkey.HotkeyError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print("✓ 已移除全局快捷键")
    return 0


def _cmd_doctor(_: argparse.Namespace) -> int:
    from .diagnostics import format_checks, has_failure, run_checks

    checks = run_checks()
    print(format_checks(checks))
    return 1 if has_failure(checks) else 0


def _cmd_download_model(args: argparse.Namespace) -> int:
    from . import models, transcribe

    cfg = load_config()
    model = args.model or str(cfg.get("local_model") or "large-v3-turbo")
    compute = args.compute_type or str(cfg.get("compute_type") or "int8")
    source = args.source or str(cfg.get("model_source") or "hf-mirror")
    print(f"正在准备本地模型 {model}（来源：{models.SOURCE_LABELS.get(source, source)}）…")

    def show_progress(frac: float, desc: str) -> None:
        print(f"\r  {frac * 100:5.1f}% {desc[:60]}", end="", flush=True)

    try:
        transcribe.preload_local(
            model, compute, source, status=lambda s: print(s), progress=show_progress
        )
        if sys.stdout.isatty():
            print()
    except transcribe.TranscribeError as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        return 1
    print("✓ 模型已就绪")
    return 0


def _cmd_config(_: argparse.Namespace) -> int:
    from .config import config_path

    path = config_path()
    print(f"配置文件：{path}")
    print(json.dumps(load_config(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice-type",
        description="Ubuntu GNOME Wayland 语音输入：本地 Whisper 或 OpenAI 兼容 API。",
    )
    parser.add_argument("--version", action="version", version=f"voice-type {__version__}")
    sub = parser.add_subparsers(dest="command")

    gui = sub.add_parser("gui", help="启动控制窗口/后台服务")
    gui.add_argument("--hidden", action="store_true", help="启动时不显示窗口")

    for name, help_text in [
        ("toggle", "开始/停止录音（热键调用这个）"),
        ("start", "开始录音"),
        ("stop", "停止录音并识别"),
        ("status", "查看当前状态（JSON）"),
        ("show", "显示控制窗口"),
        ("quit", "退出后台服务"),
    ]:
        sub.add_parser(name, help=help_text)

    install_hotkey = sub.add_parser("install-hotkey", help="注册 GNOME 全局快捷键")
    install_hotkey.add_argument("--binding", help="gsettings 语法，如 '<Super><Alt>v'")

    sub.add_parser("uninstall-hotkey", help="移除全局快捷键")
    sub.add_parser("doctor", help="检查系统依赖与配置")

    download = sub.add_parser("download-model", help="预先下载/加载本地模型")
    download.add_argument("--model", help="tiny/base/small/medium/large-v3-turbo/large-v3")
    download.add_argument("--compute-type", help="int8/float32 等")
    download.add_argument("--source", choices=["huggingface", "hf-mirror", "modelscope"], help="模型下载来源（默认读配置）")

    sub.add_parser("config", help="打印配置文件和当前配置")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "gui":
        return _cmd_gui(args)
    if args.command in {"toggle", "start", "stop", "status", "show", "quit"}:
        return _cmd_toggle_like(args)
    if args.command == "install-hotkey":
        return _cmd_install_hotkey(args)
    if args.command == "uninstall-hotkey":
        return _cmd_uninstall_hotkey(args)
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "download-model":
        return _cmd_download_model(args)
    if args.command == "config":
        return _cmd_config(args)
    parser.error(f"未知命令：{args.command}")
    return 2
