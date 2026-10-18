"""极简 Unix 域套接字控制协议：一行 JSON 请求，一行 JSON 响应。"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any, Callable

from .config import socket_path


class IpcError(RuntimeError):
    pass


class AlreadyRunning(IpcError):
    pass


def send_command(
    cmd: str,
    path: Path | None = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    target = path or socket_path()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(target))
        sock.sendall(json.dumps({"cmd": cmd}).encode("utf-8") + b"\n")
        data = b""
        while b"\n" not in data and len(data) < 65536:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError) as exc:
        raise IpcError("语音输入服务未运行") from exc
    except socket.timeout as exc:
        raise IpcError("语音输入服务响应超时") from exc
    finally:
        sock.close()
    if not data:
        raise IpcError("服务没有返回数据")
    try:
        payload = json.loads(data.decode("utf-8").split("\n", 1)[0])
    except ValueError as exc:
        raise IpcError("服务返回了无效响应") from exc
    if not isinstance(payload, dict):
        raise IpcError("服务返回了无效响应")
    return payload


def is_running(path: Path | None = None, timeout: float = 0.6) -> bool:
    try:
        send_command("status", path=path, timeout=timeout)
        return True
    except IpcError:
        return False


def handle_one(conn: socket.socket, handler: Callable[[str], dict[str, Any]]) -> None:
    try:
        conn.settimeout(1.0)
        data = b""
        while b"\n" not in data and len(data) < 65536:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        try:
            request = json.loads(data.decode("utf-8").strip() or "{}")
            cmd = str(request.get("cmd") or "status")
        except ValueError:
            cmd = "status"
        response = handler(cmd)
    except Exception as exc:  # noqa: BLE001 - 返回错误而不是让监听线程崩掉
        response = {"ok": False, "error": str(exc)}
    try:
        conn.sendall(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


class ControlServer:
    """跑在 GTK 主循环里的监听套接字。"""

    def __init__(self, handler: Callable[[str], dict[str, Any]], path: Path | None = None) -> None:
        self.handler = handler
        self.path = path or socket_path()
        self._sock: socket.socket | None = None
        self._watch_id: int | None = None

    def start(self) -> None:
        from gi.repository import GLib

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                send_command("status", path=self.path, timeout=0.6)
            except IpcError:
                try:
                    os.unlink(self.path)
                except OSError:
                    pass
            else:
                raise AlreadyRunning("语音输入服务已在运行")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.path))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        sock.listen(8)
        sock.setblocking(False)
        self._sock = sock
        self._watch_id = GLib.io_add_watch(
            sock.fileno(),
            GLib.PRIORITY_DEFAULT,
            GLib.IO_IN,
            self._on_accept,
        )

    def _on_accept(self, fd: int, condition: int) -> bool:
        assert self._sock is not None
        try:
            conn, _ = self._sock.accept()
        except (BlockingIOError, InterruptedError):
            return True
        except OSError:
            return False
        handle_one(conn, self.handler)
        return True

    def stop(self) -> None:
        if self._watch_id is not None:
            try:
                from gi.repository import GLib

                GLib.source_remove(self._watch_id)
            except Exception:  # noqa: BLE001
                pass
            self._watch_id = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        try:
            os.unlink(self.path)
        except OSError:
            pass
