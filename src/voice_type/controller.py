"""录音 → 识别 → 注入 的状态机。不依赖 GTK，GUI 和 CLI 共用。"""

from __future__ import annotations

import os
import re
import threading
from typing import Any, Callable

from . import transcribe
from .audio import AudioError, Recorder
from .inject import (
    InjectError,
    copy_to_clipboard,
    inject_text,
    read_clipboard_text,
    schedule_clipboard_restore,
)
from .notify import notify

IDLE = "idle"
RECORDING = "recording"
TRANSCRIBING = "transcribing"

StateCallback = Callable[[str], None]
TextCallback = Callable[[str], None]
StatusCallback = Callable[[str], None]


def hotkey_label(binding: str) -> str:
    """<Super><Alt>v -> Super+Alt+V，仅用于展示。"""
    tokens = re.findall(r"<([^<>]+)>|([^<>]+)", binding)
    parts: list[str] = []
    for modifier, rest in tokens:
        if modifier:
            parts.append(modifier)
        elif rest.strip():
            parts.append(rest.strip())
    if parts:
        parts[-1] = parts[-1].upper()
    return "+".join(parts)


class Controller:
    def __init__(
        self,
        cfg: dict[str, Any],
        on_state: StateCallback | None = None,
        on_result: TextCallback | None = None,
        on_status: StatusCallback | None = None,
    ) -> None:
        self.cfg = dict(cfg)
        self.on_state = on_state
        self.on_result = on_result
        self.on_status = on_status
        self.state = IDLE
        self.last_text = ""
        self.last_error = ""
        self._recorder: Recorder | None = None
        self._lock = threading.Lock()

    # ---------- 对外操作 ----------

    def update_config(self, cfg: dict[str, Any]) -> None:
        self.cfg = dict(cfg)

    def toggle(self) -> dict[str, Any]:
        with self._lock:
            state = self.state
        if state == IDLE:
            self.start()
        elif state == RECORDING:
            self.stop()
        else:
            notify("正在识别…", "请稍候")
        return self.status()

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.state != IDLE:
                return self.status()
            recorder = Recorder(str(self.cfg.get("source") or ""))
            try:
                recorder.start()
            except AudioError as exc:
                self.last_error = str(exc)
                notify("无法开始录音", str(exc), urgency="critical")
                return self.status()
            self._recorder = recorder
            self.state = RECORDING
            self.last_error = ""
        self._emit_state()
        if self.cfg.get("push_to_talk", True):
            hint = "松开右 Alt 停止并输入"
        else:
            label = hotkey_label(str(self.cfg.get("hotkey") or "<Super><Alt>v"))
            hint = f"再按 {label} 停止并输入"
        notify("🎙 正在录音…", hint)
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self.state != RECORDING:
                return self.status()
            self.state = TRANSCRIBING
            recorder = self._recorder
        self._emit_state()
        if self.on_status:
            self.on_status("正在识别…")
        notify("正在识别…", "请稍候")
        threading.Thread(
            target=self._finish, args=(recorder, dict(self.cfg)), daemon=True
        ).start()
        return self.status()

    def cancel(self) -> dict[str, Any]:
        """放弃当前录音（例如右 Alt 只是轻点了一下）。"""
        with self._lock:
            if self.state != RECORDING:
                return self.status()
            recorder = self._recorder
            self._recorder = None
            self.state = IDLE
        if recorder is not None:
            recorder.cancel()
        self._emit_state()
        notify("已取消录音", "按住右 Alt 说完整句话再松开")
        return self.status()

    def preload(self) -> None:
        """提前下载/加载本地模型，不进入录音状态。"""
        if str(self.cfg.get("backend")) == "online":
            notify("当前为在线识别", "无需下载本地模型")
            return
        model = str(self.cfg.get("local_model") or "large-v3-turbo")
        compute = str(self.cfg.get("compute_type") or "int8")
        source = str(self.cfg.get("model_source") or "hf-mirror")

        def worker() -> None:
            notify("开始加载本地模型", f"{model}（首次使用会自动下载）")
            try:
                transcribe.preload_local(model, compute, source)
            except transcribe.TranscribeError as exc:
                notify("模型加载失败", str(exc), urgency="critical")
                return
            notify("本地模型已就绪", model)

        threading.Thread(target=worker, daemon=True).start()

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = self.state
        return {
            "ok": True,
            "state": state,
            "last_text": self.last_text,
            "last_error": self.last_error,
            "backend": self.cfg.get("backend"),
            "model": self.cfg.get("local_model")
            if self.cfg.get("backend") == "local"
            else self.cfg.get("api_model"),
        }

    # ---------- 内部 ----------

    def _finish(self, recorder: Recorder | None, cfg: dict[str, Any]) -> None:
        path: str | None = None
        try:
            if recorder is None:
                raise AudioError("录音器丢失")
            path = recorder.stop()
            paste_shortcut = str(cfg.get("paste_shortcut") or "ctrl+v")
            restore = bool(cfg.get("restore_clipboard", True))
            # 流式注入：开始前捕获一次剪贴板，全部结束后才恢复
            previous = read_clipboard_text() if restore else None
            streamed: list[str] = []
            inject_failed = False

            def handle_piece(piece: str) -> None:
                nonlocal inject_failed
                streamed.append(piece)
                partial = "".join(streamed).strip()
                self.last_text = partial
                if self.on_result:
                    self.on_result(partial)
                if inject_failed:
                    return
                try:
                    inject_text(piece, paste_shortcut=paste_shortcut, restore_clipboard=False)
                except InjectError as exc:
                    inject_failed = True
                    notify(
                        "自动输入失败，文字已复制",
                        "请手动按 Ctrl+V 粘贴。详情：" + str(exc),
                        urgency="critical",
                    )

            text = transcribe.transcribe(
                path, cfg, status=self.on_status, on_segment=handle_piece
            )
            if text.strip():
                self.last_text = text
                if self.on_result:
                    self.on_result(text)
                if not streamed:
                    # 回调未触发时的兜底：一次性注入
                    try:
                        inject_text(
                            text, paste_shortcut=paste_shortcut, restore_clipboard=restore
                        )
                    except InjectError as exc:
                        inject_failed = True
                        if exc.copied:
                            notify(
                                "自动输入失败，文字已复制",
                                "请手动按 Ctrl+V 粘贴。详情：" + str(exc),
                                urgency="critical",
                            )
                        else:
                            notify("自动输入失败", str(exc), urgency="critical")
                    else:
                        notify("已输入", text[:80])
                elif inject_failed:
                    # 部分片段注入失败：把完整文字放进剪贴板供手动粘贴，不再恢复原剪贴板
                    try:
                        copy_to_clipboard(text)
                    except InjectError:
                        pass
                    notify(
                        "自动输入中断",
                        "完整文字已复制，请手动按 Ctrl+V 粘贴",
                        urgency="critical",
                    )
                else:
                    notify("已输入", text[:80])
                if restore and previous is not None and streamed and not inject_failed:
                    schedule_clipboard_restore(previous)
            else:
                notify("没有识别到内容", "请靠近麦克风再说一次")
        except (AudioError, transcribe.TranscribeError) as exc:
            self.last_error = str(exc)
            notify("语音输入失败", str(exc), urgency="critical")
        except Exception as exc:  # noqa: BLE001 - 后台线程必须兜底
            self.last_error = str(exc)
            notify("语音输入失败", str(exc), urgency="critical")
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            with self._lock:
                self._recorder = None
                self.state = IDLE
            self._emit_state()

    def _emit_state(self) -> None:
        if self.on_state:
            try:
                self.on_state(self.state)
            except Exception:  # noqa: BLE001 - UI 回调失败不能影响状态机
                pass
