"""GTK4 / libadwaita 控制窗口。

设计取舍：GNOME 46 默认没有系统托盘，因此不做托盘图标；
窗口关闭时只是隐藏，后台服务继续监听快捷键。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import hotkey, ipc, ptt  # noqa: E402
from .config import config_path, load_config, save_config  # noqa: E402
from .controller import (  # noqa: E402
    IDLE,
    RECORDING,
    TRANSCRIBING,
    Controller,
    hotkey_label,
)
from .diagnostics import format_checks, run_checks  # noqa: E402
from .notify import notify  # noqa: E402

APP_ID = "io.github.voicetype.VoiceType"
LOCAL_MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
PASTE_SHORTCUTS = [("ctrl+v", "Ctrl+V（大多数应用）"), ("ctrl+shift+v", "Ctrl+Shift+V（终端）")]

STATE_TEXT = {
    IDLE: ("空闲", "按住右 Alt 说话，或按快捷键开始"),
    RECORDING: ("正在录音…", "松开右 Alt 停止并识别；快捷键模式下再按一次停止"),
    TRANSCRIBING: ("正在识别…", "请稍候"),
}


class VoiceTypeApplication(Adw.Application):
    def __init__(self, start_hidden: bool = False) -> None:
        # 使用 GApplication 默认的唯一实例模式：重复启动时由 DBus 激活已有实例，
        # 第二个进程自动退出，不会出现两个后台进程抢同一个控制套接字。
        super().__init__(application_id=APP_ID)
        self.start_hidden = start_hidden
        self.cfg = load_config()
        self.controller = Controller(
            self.cfg,
            on_state=self._on_state,
            on_result=self._on_result,
            on_status=self._on_status,
        )
        self.server: ipc.ControlServer | None = None
        self.ptt_listener: ptt.PushToTalkListener | None = None
        self.window: Adw.ApplicationWindow | None = None
        self.toast_overlay: Adw.ToastOverlay | None = None
        self._ready = False
        self.connect("activate", self._on_activate)
        self.connect("shutdown", self._on_shutdown)
        self._install_actions()

    # ---------- 生命周期 ----------

    def _install_actions(self) -> None:
        for name, callback in [
            ("copy-last", self._on_copy_action),
            ("doctor", self._on_doctor_action),
            ("exit", self._on_exit_action),
        ]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def _on_activate(self, _app: Adw.Application) -> None:
        if self.server is None:
            server = ipc.ControlServer(self._handle_command)
            try:
                server.start()
            except ipc.AlreadyRunning:
                # 已有实例：让它把窗口显示出来，然后退出本进程。
                # 这里直接 os._exit，避免在 activate 阶段调用 quit() 后进程悬挂。
                try:
                    ipc.send_command("show")
                except ipc.IpcError:
                    pass
                os._exit(0)
            except OSError as exc:
                print(f"无法启动控制套接字：{exc}", file=sys.stderr)
                self.quit()
                return
            self.server = server

        if self.window is None:
            self._build_window()

        if not self.start_hidden and self.window is not None:
            self._show_window()
        self.start_hidden = False
        self._sync_ptt()

    def _on_shutdown(self, _app: Adw.Application) -> None:
        if self.ptt_listener is not None:
            self.ptt_listener.stop()
            self.ptt_listener = None
        if self.server is not None:
            self.server.stop()
            self.server = None

    # ---------- 控制协议 ----------

    def _handle_command(self, cmd: str) -> dict[str, Any]:
        if cmd == "toggle":
            return self.controller.toggle()
        if cmd == "start":
            return self.controller.start()
        if cmd == "stop":
            return self.controller.stop()
        if cmd == "status":
            return self.controller.status()
        if cmd == "show":
            GLib.idle_add(self._show_window)
            return {"ok": True, "state": self.controller.state, "message": "已显示窗口"}
        if cmd == "quit":
            GLib.idle_add(self._request_quit)
            return {"ok": True, "state": "idle", "message": "已退出"}
        return {"ok": False, "error": f"未知命令：{cmd}"}

    # ---------- 界面 ----------

    def _build_window(self) -> None:
        self.window = Adw.ApplicationWindow(application=self, title="语音输入")
        self.window.set_default_size(560, 760)
        self.window.connect("close-request", self._on_close_request)

        header = Adw.HeaderBar()
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu = Gio.Menu()
        menu.append("复制最近结果", "app.copy-last")
        menu.append("环境自检", "app.doctor")
        menu.append("退出", "app.exit")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        self.toast_overlay = Adw.ToastOverlay()
        page = Adw.PreferencesPage()
        self.toast_overlay.set_child(page)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self.toast_overlay)
        self.window.set_content(toolbar_view)

        page.add(self._build_status_group())
        page.add(self._build_result_group())
        page.add(self._build_settings_group())
        page.add(self._build_hotkey_group())
        self._ready = True
        self._update_state(self.controller.state)

    def _build_status_group(self) -> Adw.PreferencesGroup:
        assert self.window is not None
        group = Adw.PreferencesGroup(title="语音输入")
        self.status_row = Adw.ActionRow(
            title=STATE_TEXT[IDLE][0],
            subtitle=self._idle_subtitle(),
        )
        self.spinner = Gtk.Spinner()
        self.status_row.add_suffix(self.spinner)
        group.add(self.status_row)

        self.toggle_button = Gtk.Button(label="开始录音")
        self.toggle_button.add_css_class("suggested-action")
        self.toggle_button.set_valign(Gtk.Align.CENTER)
        self.toggle_button.connect("clicked", self._on_toggle_clicked)
        toggle_row = Adw.ActionRow(
            title="录音",
            subtitle="开始后窗口会自动隐藏，把焦点留给正在编辑的应用",
        )
        toggle_row.add_suffix(self.toggle_button)
        toggle_row.set_activatable_widget(self.toggle_button)
        group.add(toggle_row)
        return group

    def _build_result_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="最近识别")
        self.result_view = Gtk.TextView()
        self.result_view.set_editable(False)
        self.result_view.set_cursor_visible(False)
        self.result_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.result_view.set_top_margin(8)
        self.result_view.set_bottom_margin(8)
        self.result_view.set_left_margin(10)
        self.result_view.set_right_margin(10)
        self.result_buffer = self.result_view.get_buffer()
        self.result_buffer.set_text(self.controller.last_text)

        scroller = Gtk.ScrolledWindow()
        scroller.set_min_content_height(96)
        scroller.set_has_frame(True)
        scroller.set_child(self.result_view)
        scroller.set_margin_top(4)
        scroller.set_margin_bottom(4)
        scroller.set_margin_start(12)
        scroller.set_margin_end(12)
        group.add(scroller)

        copy_button = Gtk.Button(label="复制")
        copy_button.set_valign(Gtk.Align.CENTER)
        copy_button.connect("clicked", lambda *_: self._copy_last_text())
        copy_row = Adw.ActionRow(title="复制到剪贴板")
        copy_row.add_suffix(copy_button)
        copy_row.set_activatable_widget(copy_button)
        group.add(copy_row)
        return group

    def _build_settings_group(self) -> Adw.PreferencesGroup:
        cfg = self.cfg
        group = Adw.PreferencesGroup(title="识别设置", description="修改后点击「保存设置」立即生效")

        self.backend_row = Adw.ComboRow(
            title="识别方式",
            subtitle="本地模型完全离线；在线 API 需要填写自己的服务地址",
            model=Gtk.StringList.new(["本地模型（faster-whisper）", "在线 API（OpenAI 兼容）"]),
        )
        self.backend_row.set_selected(0 if cfg.get("backend") == "local" else 1)
        group.add(self.backend_row)

        self.local_model_row = Adw.ComboRow(
            title="本地模型",
            subtitle="推荐 large-v3-turbo（比 medium 更准，CPU 上速度相当）",
            model=Gtk.StringList.new(LOCAL_MODELS),
        )
        model = str(cfg.get("local_model") or "large-v3-turbo")
        self.local_model_row.set_selected(
            LOCAL_MODELS.index(model) if model in LOCAL_MODELS else LOCAL_MODELS.index("large-v3-turbo")
        )
        group.add(self.local_model_row)

        self.language_row = Adw.EntryRow(title="语言（zh / en / auto）")
        self.language_row.set_text(str(cfg.get("language") or "zh"))
        group.add(self.language_row)

        self.prompt_row = Adw.EntryRow(title="识别提示词（术语、人名，可留空）")
        self.prompt_row.set_text(str(cfg.get("initial_prompt") or ""))
        group.add(self.prompt_row)

        self.source_row = Adw.EntryRow(
            title="麦克风设备（留空=系统默认，可用 pactl list short sources 查看）"
        )
        self.source_row.set_text(str(cfg.get("source") or ""))
        group.add(self.source_row)

        self.api_base_row = Adw.EntryRow(title="API Base URL（如 https://api.example.com/v1）")
        self.api_base_row.set_text(str(cfg.get("api_base_url") or ""))
        group.add(self.api_base_row)

        self.api_key_row = Adw.PasswordEntryRow(title="API Key")
        self.api_key_row.set_text(str(cfg.get("api_key") or ""))
        group.add(self.api_key_row)

        self.api_model_row = Adw.EntryRow(title="在线模型名（如 whisper-1 或 FunAudioLLM/SenseVoiceSmall）")
        self.api_model_row.set_text(str(cfg.get("api_model") or "whisper-1"))
        group.add(self.api_model_row)

        self.paste_row = Adw.ComboRow(
            title="粘贴快捷键",
            subtitle="终端里 Ctrl+V 通常不是粘贴，需选 Ctrl+Shift+V",
            model=Gtk.StringList.new([label for _, label in PASTE_SHORTCUTS]),
        )
        current_shortcut = str(cfg.get("paste_shortcut") or "ctrl+v")
        self.paste_row.set_selected(
            1 if current_shortcut == "ctrl+shift+v" else 0
        )
        group.add(self.paste_row)

        self.restore_row = Adw.SwitchRow(
            title="粘贴后恢复原剪贴板",
            subtitle="关闭后，识别文字会留在剪贴板里",
            active=bool(cfg.get("restore_clipboard", True)),
        )
        group.add(self.restore_row)

        self.vad_row = Adw.SwitchRow(
            title="自动去除静音（VAD）",
            subtitle="建议开启，可避免在静音或噪声上凭空生成文字",
            active=bool(cfg.get("vad_filter", True)),
        )
        group.add(self.vad_row)

        self.ptt_row = Adw.SwitchRow(
            title="右 Alt 按住说话",
            subtitle="按下右 Alt 开始录音，松开自动识别；用 AltGr 输入特殊字符时可能误触",
            active=bool(cfg.get("push_to_talk", True)),
        )
        group.add(self.ptt_row)

        save_button = Gtk.Button(label="保存设置")
        save_button.add_css_class("suggested-action")
        save_button.set_valign(Gtk.Align.CENTER)
        save_button.connect("clicked", self._on_save_settings)
        preload_button = Gtk.Button(label="预下载模型")
        preload_button.set_valign(Gtk.Align.CENTER)
        preload_button.connect("clicked", self._on_preload_model)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(preload_button)
        box.append(save_button)
        row = Adw.ActionRow(title="应用设置")
        row.add_suffix(box)
        group.add(row)
        return group

    def _build_hotkey_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="全局快捷键")
        self.hotkey_row = Adw.ActionRow(
            title=GLib.markup_escape_text(str(self.cfg.get("hotkey") or "<Super><Alt>v")),
            subtitle="通过 GNOME 自定义快捷键实现",
        )
        install_button = Gtk.Button(label="安装/更新")
        install_button.set_valign(Gtk.Align.CENTER)
        install_button.connect("clicked", self._on_install_hotkey)
        remove_button = Gtk.Button(label="移除")
        remove_button.set_valign(Gtk.Align.CENTER)
        remove_button.connect("clicked", self._on_remove_hotkey)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(remove_button)
        box.append(install_button)
        self.hotkey_row.add_suffix(box)
        group.add(self.hotkey_row)

        config_row = Adw.ActionRow(
            title="配置文件",
            subtitle=str(config_path()),
        )
        group.add(config_row)
        return group

    # ---------- 事件 ----------

    def _on_toggle_clicked(self, _button: Gtk.Button) -> None:
        state = self.controller.state
        if state == TRANSCRIBING:
            self._toast("正在识别，请稍候…")
            return
        self.controller.toggle()

    def _on_save_settings(self, _button: Gtk.Button) -> None:
        cfg = dict(self.cfg)
        old_ptt = bool(self.cfg.get("push_to_talk", True))
        cfg["backend"] = "local" if self.backend_row.get_selected() == 0 else "online"
        cfg["local_model"] = LOCAL_MODELS[self.local_model_row.get_selected()]
        cfg["language"] = self.language_row.get_text().strip() or "zh"
        cfg["initial_prompt"] = self.prompt_row.get_text().strip()
        cfg["source"] = self.source_row.get_text().strip()
        cfg["api_base_url"] = self.api_base_row.get_text().strip()
        cfg["api_key"] = self.api_key_row.get_text().strip()
        cfg["api_model"] = self.api_model_row.get_text().strip() or "whisper-1"
        cfg["paste_shortcut"] = PASTE_SHORTCUTS[self.paste_row.get_selected()][0]
        cfg["restore_clipboard"] = self.restore_row.get_active()
        cfg["vad_filter"] = self.vad_row.get_active()
        cfg["push_to_talk"] = self.ptt_row.get_active()
        try:
            save_config(cfg)
        except OSError as exc:
            self._toast(f"保存失败：{exc}")
            return
        self.cfg = cfg
        self.controller.update_config(cfg)
        if cfg["push_to_talk"] != old_ptt:
            self._sync_ptt()
        self._toast("设置已保存")

    def _sync_ptt(self) -> None:
        want = bool(self.cfg.get("push_to_talk", True))
        if want and self.ptt_listener is None:
            listener = ptt.PushToTalkListener(
                self.controller, on_error=self._on_ptt_error
            )
            try:
                listener.start()
            except ptt.PushToTalkError as exc:
                notify("右 Alt 按住说话不可用", str(exc), urgency="critical")
                self._toast(f"右 Alt 监听失败：{exc}")
                return
            self.ptt_listener = listener
        elif not want and self.ptt_listener is not None:
            self.ptt_listener.stop()
            self.ptt_listener = None

    def _on_ptt_error(self, message: str) -> None:
        GLib.idle_add(self._show_ptt_error, message)

    def _show_ptt_error(self, message: str) -> bool:
        self._toast(message)
        notify("右 Alt 监听失败", message, urgency="critical")
        return GLib.SOURCE_REMOVE

    def _on_preload_model(self, _button: Gtk.Button) -> None:
        if self.controller.state != IDLE:
            self._toast("请先停止当前录音/识别")
            return
        self.controller.preload()
        self._toast("已开始加载模型，完成或失败会有通知")

    def _on_install_hotkey(self, _button: Gtk.Button) -> None:
        binding = str(self.cfg.get("hotkey") or "<Super><Alt>v")
        try:
            hotkey.install(binding, hotkey.default_command())
        except hotkey.HotkeyError as exc:
            self._toast(f"设置失败：{exc}")
            return
        self.hotkey_row.set_title(GLib.markup_escape_text(binding))
        self._toast(f"快捷键已设置：{hotkey_label(binding)}")

    def _on_remove_hotkey(self, _button: Gtk.Button) -> None:
        try:
            hotkey.uninstall()
        except hotkey.HotkeyError as exc:
            self._toast(f"移除失败：{exc}")
            return
        self._toast("快捷键已移除")

    def _on_copy_action(self, _action: Gio.SimpleAction, _param: Any) -> None:
        self._copy_last_text()

    def _on_doctor_action(self, _action: Gio.SimpleAction, _param: Any) -> None:
        checks = run_checks()
        dialog = Adw.MessageDialog(
            transient_for=self.window,
            heading="环境自检",
            body=GLib.markup_escape_text(format_checks(checks)),
        )
        dialog.add_response("close", "关闭")
        dialog.set_default_response("close")
        dialog.present()

    def _on_exit_action(self, _action: Gio.SimpleAction, _param: Any) -> None:
        self._request_quit()

    def _on_close_request(self, _window: Adw.ApplicationWindow) -> bool:
        # 关闭窗口 = 退出应用（不再隐藏到后台）
        self._request_quit()
        return True

    def _request_quit(self) -> None:
        if self.controller.state == RECORDING:
            self.controller.cancel()
        # 延迟到主循环空闲时再 quit，并加一个兜底：2.5 秒后仍未退出就强制结束进程。
        GLib.idle_add(self.quit)
        threading.Thread(target=self._force_exit, daemon=True).start()

    @staticmethod
    def _force_exit() -> None:
        time.sleep(2.5)
        os._exit(0)

    # ---------- 状态刷新 ----------

    def _on_state(self, state: str) -> None:
        GLib.idle_add(self._update_state, state)

    def _on_result(self, text: str) -> None:
        GLib.idle_add(self._set_result_text, text)

    def _on_status(self, text: str) -> None:
        GLib.idle_add(self._set_status_subtitle, text)

    def _update_state(self, state: str) -> bool:
        if not self._ready or self.window is None:
            return GLib.SOURCE_REMOVE
        title, subtitle = STATE_TEXT.get(state, (state, ""))
        if state == IDLE:
            subtitle = self._idle_subtitle()
        self.status_row.set_title(title)
        self.status_row.set_subtitle(subtitle)
        if state == TRANSCRIBING:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.toggle_button.set_label("停止并识别" if state == RECORDING else "开始录音")
        # 录音期间保持窗口可见（显示「正在录音…」），松开右 Alt / 停止后再隐藏，
        # 把焦点还给目标应用，等识别完成后注入文字。
        if state == TRANSCRIBING and self.window.get_visible():
            self.window.set_visible(False)
        if state == IDLE:
            self._set_result_text(self.controller.last_text)
        return GLib.SOURCE_REMOVE

    def _set_result_text(self, text: str) -> bool:
        if not self._ready:
            return GLib.SOURCE_REMOVE
        self.result_buffer.set_text(text or "")
        return GLib.SOURCE_REMOVE

    def _set_status_subtitle(self, text: str) -> bool:
        if self._ready:
            self.status_row.set_subtitle(text)
        return GLib.SOURCE_REMOVE

    def _idle_subtitle(self) -> str:
        label = hotkey_label(str(self.cfg.get("hotkey") or "<Super><Alt>v"))
        if self.cfg.get("push_to_talk", True):
            return f"按住右 Alt 说话；或按 {label} 开始/停止"
        return f"按 {label} 开始/停止，或点击下方按钮"

    def _show_window(self) -> bool:
        if self.window is None:
            return GLib.SOURCE_REMOVE
        self.window.set_visible(True)
        self.window.present()
        return GLib.SOURCE_REMOVE

    def _copy_last_text(self) -> None:
        text = self.controller.last_text
        if not text:
            self._toast("还没有识别结果")
            return
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)
        self._toast("已复制")

    def _toast(self, text: str) -> None:
        if self.toast_overlay is not None:
            self.toast_overlay.add_toast(Adw.Toast.new(text))
