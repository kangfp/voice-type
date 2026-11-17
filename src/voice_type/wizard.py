"""首次启动向导：环境检查 → 模型来源 → 下载模型 → 快捷键与完成。

配置里 setup_completed=False 时由主窗口加载本向导；完成后交回主界面。
向导只收集设置并调用 on_finished(updates)，不直接写配置文件。
"""

from __future__ import annotations

import getpass
import subprocess
import threading
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import hotkey, models  # noqa: E402
from .diagnostics import format_checks, run_checks  # noqa: E402

FinishedCallback = Callable[[dict[str, Any]], None]

STEP_TITLES = ["环境检查", "模型来源", "下载模型", "快捷键与完成"]


class SetupWizard:
    def __init__(self, cfg: dict[str, Any], on_finished: FinishedCallback) -> None:
        self.cfg = dict(cfg)
        self.on_finished = on_finished
        self.source = str(cfg.get("model_source") or models.SOURCE_MIRROR)
        self.model = str(cfg.get("local_model") or models.DEFAULT_MODEL)
        self.online = str(cfg.get("backend")) == "online"
        self._cancel = threading.Event()
        self._downloading = False
        self._probe_done = False
        self._permission_fixed = False
        self._build()

    # ---------- 构建 ----------

    def _build(self) -> None:
        # 导航控件必须先于各步骤页面创建：页面构建时会调用 _refresh_nav()
        self._index = 0
        self.step_label = Gtk.Label()
        self.step_label.add_css_class("dim-label")
        self.back_button = Gtk.Button(label="上一步")
        self.back_button.connect("clicked", lambda *_: self._go(-1))
        self.next_button = Gtk.Button(label="下一步")
        self.next_button.add_css_class("suggested-action")
        self.next_button.connect("clicked", lambda *_: self._go(1))
        skip_button = Gtk.Button(label="跳过向导")
        skip_button.add_css_class("flat")
        skip_button.connect("clicked", lambda *_: self._finish(skipped=True))

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nav.set_margin_top(8)
        nav.set_margin_bottom(12)
        nav.set_margin_start(12)
        nav.set_margin_end(12)
        nav.append(skip_button)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        nav.append(spacer)
        nav.append(self.step_label)
        nav.append(self.back_button)
        nav.append(self.next_button)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.add_named(self._build_step_checks(), "checks")
        self.stack.add_named(self._build_step_source(), "source")
        self.stack.add_named(self._build_step_download(), "download")
        self.stack.add_named(self._build_step_finish(), "finish")

        intro = Adw.PreferencesGroup(
            title="欢迎使用语音输入",
            description="按住右 Alt 说话，松开后文字自动输入到当前应用。完成以下几步即可开始使用。",
        )
        intro.set_margin_top(12)
        intro.set_margin_start(12)
        intro.set_margin_end(12)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(320)
        scroller.set_vexpand(True)
        scroller.set_child(self.stack)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(intro)
        box.append(scroller)
        box.append(nav)
        self.widget = box
        self._index = 0
        self._refresh_nav()

    def _wrap(self, group: Adw.PreferencesGroup) -> Gtk.Widget:
        # 不用 PreferencesPage 包步骤：它是滚动容器，自然高度为 0，
        # 放进 Stack 会把内容压扁。用普通 Box，滚动交给外层 ScrolledWindow。
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(group)
        return box

    # ---------- 第 1 步：环境检查 ----------

    def _build_step_checks(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(
            title="第 1 步 · 环境检查",
            description="检查系统依赖与权限。缺少权限时可以一键修复（需要管理员密码）。",
        )
        self.checks_label = Gtk.Label(label="正在检查…", xalign=0)
        self.checks_label.set_wrap(True)
        self.checks_label.add_css_class("monospace")
        self.checks_label.set_margin_start(12)
        self.checks_label.set_margin_end(12)
        group.add(self.checks_label)

        self.fix_button = Gtk.Button(label="修复权限（加入 input 组）")
        self.fix_button.set_valign(Gtk.Align.CENTER)
        self.fix_button.connect("clicked", self._on_fix_permissions)
        recheck_button = Gtk.Button(label="重新检查")
        recheck_button.set_valign(Gtk.Align.CENTER)
        recheck_button.connect("clicked", lambda *_: self._run_checks())
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(recheck_button)
        box.append(self.fix_button)
        row = Adw.ActionRow(title="权限修复", subtitle="修复后需要注销并重新登录才生效")
        row.add_suffix(box)
        group.add(row)
        GLib.idle_add(self._run_checks)
        return self._wrap(group)

    def _run_checks(self) -> bool:
        self.checks_label.set_label("正在检查…")

        def worker() -> None:
            checks = run_checks()
            text = format_checks(checks)
            need_fix = any(
                not c.ok and c.name in ("/dev/uinput 可写", "右 Alt 按住说话") for c in checks
            )
            GLib.idle_add(self._show_checks, text, need_fix)

        threading.Thread(target=worker, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _show_checks(self, text: str, need_fix: bool) -> bool:
        self.checks_label.set_label(text)
        self.fix_button.set_visible(need_fix)
        return GLib.SOURCE_REMOVE

    def _on_fix_permissions(self, _button: Gtk.Button) -> None:
        self.fix_button.set_sensitive(False)
        user = getpass.getuser()

        def worker() -> None:
            try:
                proc = subprocess.run(
                    ["pkexec", "usermod", "-aG", "input", user],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                ok = proc.returncode == 0
                detail = (proc.stderr or "").strip()[:200]
            except (OSError, subprocess.TimeoutExpired) as exc:
                ok, detail = False, str(exc)
            GLib.idle_add(self._permission_result, ok, detail)

        threading.Thread(target=worker, daemon=True).start()

    def _permission_result(self, ok: bool, detail: str) -> bool:
        self.fix_button.set_sensitive(True)
        if ok:
            self._permission_fixed = True
            self.checks_label.set_label(
                self.checks_label.get_label()
                + "\n\n✓ 已加入 input 组。请注销并重新登录后，右 Alt 和文字注入才会生效。"
            )
        else:
            self.checks_label.set_label(
                self.checks_label.get_label() + f"\n\n✗ 权限修复失败：{detail or '已取消'}"
            )
        return GLib.SOURCE_REMOVE

    # ---------- 第 2 步：模型来源 ----------

    def _build_step_source(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(
            title="第 2 步 · 选择模型下载来源",
            description="国外用户建议 Hugging Face；国内用户建议魔搭 ModelScope 或 hf-mirror 镜像。",
        )
        self.source_checks: dict[str, Gtk.CheckButton] = {}
        first: Gtk.CheckButton | None = None
        for source_id, label, url in models.SOURCES:
            check = Gtk.CheckButton(label=label)
            check.set_tooltip_text(url)
            if first is None:
                first = check
            else:
                check.set_group(first)
            check.set_active(source_id == self.source)
            check.connect("toggled", self._on_source_toggled, source_id)
            self.source_checks[source_id] = check
            row = Adw.ActionRow(title=label, subtitle=url)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            group.add(row)

        self.probe_label = Gtk.Label(label="", xalign=0)
        self.probe_label.add_css_class("dim-label")
        self.probe_label.set_wrap(True)
        self.probe_label.set_margin_start(12)
        self.probe_label.set_margin_end(12)
        group.add(self.probe_label)
        self.probe_apply_button = Gtk.Button(label="采用推荐")
        self.probe_apply_button.set_valign(Gtk.Align.CENTER)
        self.probe_apply_button.set_visible(False)
        self.probe_apply_button.connect("clicked", self._on_apply_probe)
        probe_button = Gtk.Button(label="检测网络并推荐")
        probe_button.set_valign(Gtk.Align.CENTER)
        probe_button.connect("clicked", lambda *_: self._run_probe())
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(self.probe_apply_button)
        box.append(probe_button)
        row = Adw.ActionRow(title="自动选择", subtitle="依次探测三个来源的连通性，推荐第一个可达的")
        row.add_suffix(box)
        group.add(row)
        return self._wrap(group)

    def _on_source_toggled(self, check: Gtk.CheckButton, source_id: str) -> None:
        if check.get_active():
            self.source = source_id

    def _run_probe(self) -> bool:
        self.probe_label.set_label("正在探测网络（每个来源最多 3 秒）…")

        def worker() -> None:
            recommended = models.probe_source()
            GLib.idle_add(self._show_probe, recommended)

        threading.Thread(target=worker, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _show_probe(self, recommended: str) -> bool:
        self._probe_done = True
        self._recommended = recommended
        # 只推荐、不覆盖用户已选的来源
        if recommended == self.source:
            self.probe_label.set_label(f"✓ 当前选择可达：{models.SOURCE_LABELS[recommended]}")
            self.probe_apply_button.set_visible(False)
        else:
            self.probe_label.set_label(
                f"✓ 推荐使用：{models.SOURCE_LABELS[recommended]}（点「采用推荐」切换）"
            )
            self.probe_apply_button.set_visible(True)
        return GLib.SOURCE_REMOVE

    def _on_apply_probe(self, _button: Gtk.Button) -> None:
        recommended = getattr(self, "_recommended", None)
        if recommended:
            self.source_checks[recommended].set_active(True)
            self.probe_apply_button.set_visible(False)
            self.probe_label.set_label(f"✓ 已切换为：{models.SOURCE_LABELS[recommended]}")

    # ---------- 第 3 步：下载模型 ----------

    def _build_step_download(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(
            title="第 3 步 · 选择并下载模型",
            description="模型只需下载一次，之后完全离线识别。也可以跳过下载，改用在线 API。",
        )
        labels = [
            f"{m}（约 {models.MODEL_SPECS[m].size_gb:.2f} GB）" for m in models.MODEL_IDS
        ]
        self.model_row = Adw.ComboRow(title="模型大小", model=Gtk.StringList.new(labels))
        self.model_row.set_selected(
            models.MODEL_IDS.index(self.model)
            if self.model in models.MODEL_IDS
            else models.MODEL_IDS.index(models.DEFAULT_MODEL)
        )
        self.model_row.connect("notify::selected", self._on_model_changed)
        group.add(self.model_row)

        self.model_note = Gtk.Label(xalign=0)
        self.model_note.set_wrap(True)
        self.model_note.add_css_class("dim-label")
        self.model_note.set_margin_start(12)
        self.model_note.set_margin_end(12)
        group.add(self.model_note)

        self.online_row = Adw.SwitchRow(
            title="跳过下载，使用在线 API",
            subtitle="需要之后在设置里填写自己的 OpenAI 兼容服务地址",
            active=self.online,
        )
        self.online_row.connect("notify::active", self._on_online_toggled)
        group.add(self.online_row)

        self.download_button = Gtk.Button(label="下载模型")
        self.download_button.add_css_class("suggested-action")
        self.download_button.set_valign(Gtk.Align.CENTER)
        self.download_button.connect("clicked", self._on_download)
        self.cancel_button = Gtk.Button(label="取消")
        self.cancel_button.set_valign(Gtk.Align.CENTER)
        self.cancel_button.set_visible(False)
        self.cancel_button.connect("clicked", self._on_cancel_download)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(self.cancel_button)
        box.append(self.download_button)
        self.download_row = Adw.ActionRow(title="下载", subtitle="支持断点续传，中断后重新下载会接着传")
        self.download_row.add_suffix(box)
        group.add(self.download_row)

        self.progress = Gtk.ProgressBar()
        self.progress.set_margin_start(12)
        self.progress.set_margin_end(12)
        self.progress.set_show_text(True)
        group.add(self.progress)
        self.download_status = Gtk.Label(label="", xalign=0)
        self.download_status.set_wrap(True)
        self.download_status.set_margin_start(12)
        self.download_status.set_margin_end(12)
        group.add(self.download_status)
        self._refresh_download_state()
        return self._wrap(group)

    def _on_model_changed(self, *_args: Any) -> None:
        self.model = models.MODEL_IDS[self.model_row.get_selected()]
        self._refresh_download_state()

    def _on_online_toggled(self, row: Adw.SwitchRow, *_args: Any) -> None:
        self.online = row.get_active()
        self._refresh_download_state()

    def _refresh_download_state(self) -> None:
        spec = models.MODEL_SPECS.get(self.model)
        cached = models.is_cached(self.model, self.source)
        note = spec.note if spec else ""
        free = models.free_disk_gb()
        if self.online:
            self.model_note.set_label("已选择在线 API，无需下载模型。")
            self.download_row.set_visible(False)
            self.progress.set_visible(False)
        else:
            extra = "✓ 该模型已缓存，无需下载。" if cached else f"磁盘剩余 {free:.1f} GB。"
            self.model_note.set_label(f"{note}\n{extra}")
            self.download_row.set_visible(True)
            self.progress.set_visible(True)
            self.download_button.set_sensitive(not cached and not self._downloading)
            if cached:
                self.progress.set_fraction(1.0)
                self.progress.set_text("已就绪")
            need_space = (spec.size_gb if spec else 0) + 0.5
            if not cached and free < need_space:
                self.download_status.set_label("✗ 磁盘空间不足，请清理后再下载。")
                self.download_button.set_sensitive(False)
        self._refresh_nav()

    def _on_download(self, _button: Gtk.Button) -> None:
        if self._downloading:
            return
        self._downloading = True
        self._cancel.clear()
        self.download_button.set_sensitive(False)
        self.cancel_button.set_visible(True)
        self.progress.set_fraction(0.0)
        self.download_status.set_label("正在下载…")
        model, source = self.model, self.source

        def on_progress(frac: float, desc: str) -> None:
            GLib.idle_add(self._set_progress, frac, desc)

        def on_status(text: str) -> None:
            GLib.idle_add(self._set_download_status, text)

        def worker() -> None:
            try:
                models.download_model(
                    model, source, status=on_status, progress=on_progress, cancel=self._cancel
                )
            except models.ModelError as exc:
                GLib.idle_add(self._download_failed, str(exc))
                return
            except Exception as exc:  # noqa: BLE001 - 下载线程必须兜底
                GLib.idle_add(self._download_failed, str(exc))
                return
            GLib.idle_add(self._download_done)

        threading.Thread(target=worker, daemon=True).start()

    def _on_cancel_download(self, _button: Gtk.Button) -> None:
        self._cancel.set()
        self._set_download_status("正在取消…")

    def _set_progress(self, frac: float, desc: str) -> bool:
        self.progress.set_fraction(frac)
        self.progress.set_text(f"{frac * 100:.0f}% {desc[:40]}")
        return GLib.SOURCE_REMOVE

    def _set_download_status(self, text: str) -> bool:
        self.download_status.set_label(text)
        return GLib.SOURCE_REMOVE

    def _download_done(self) -> bool:
        self._downloading = False
        self.cancel_button.set_visible(False)
        self.progress.set_fraction(1.0)
        self.progress.set_text("完成")
        self.download_status.set_label("✓ 模型下载完成，可以进入下一步。")
        self._refresh_download_state()
        return GLib.SOURCE_REMOVE

    def _download_failed(self, message: str) -> bool:
        self._downloading = False
        self.cancel_button.set_visible(False)
        self.download_button.set_sensitive(True)
        self.download_status.set_label(f"✗ {message}\n可检查网络后重试，或换一个模型来源。")
        return GLib.SOURCE_REMOVE

    # ---------- 第 4 步：快捷键与完成 ----------

    def _build_step_finish(self) -> Gtk.Widget:
        group = Adw.PreferencesGroup(
            title="第 4 步 · 全局快捷键",
            description="注册备用快捷键 Super+Alt+V（开始/停止）。右 Alt 按住说话无需注册，开窗口即可用。",
        )
        self.hotkey_status = Gtk.Label(label="尚未注册", xalign=0)
        self.hotkey_status.add_css_class("dim-label")
        self.hotkey_status.set_margin_start(12)
        self.hotkey_status.set_margin_end(12)
        group.add(self.hotkey_status)
        install_button = Gtk.Button(label="注册快捷键")
        install_button.set_valign(Gtk.Align.CENTER)
        install_button.connect("clicked", self._on_install_hotkey)
        row = Adw.ActionRow(title="Super+Alt+V", subtitle="可在 GNOME 设置 → 键盘 → 自定义快捷键中修改")
        row.add_suffix(install_button)
        group.add(row)

        self.finish_note = Gtk.Label(xalign=0)
        self.finish_note.set_wrap(True)
        self.finish_note.set_margin_start(12)
        self.finish_note.set_margin_end(12)
        group.add(self.finish_note)
        return self._wrap(group)

    def _on_install_hotkey(self, _button: Gtk.Button) -> None:
        binding = str(self.cfg.get("hotkey") or "<Super><Alt>v")
        try:
            hotkey.install(binding, hotkey.default_command())
        except hotkey.HotkeyError as exc:
            self.hotkey_status.set_label(f"✗ 注册失败：{exc}")
            return
        self.hotkey_status.set_label(f"✓ 已注册：{binding}")

    # ---------- 导航 ----------

    def _go(self, delta: int) -> None:
        if delta > 0 and self._index == len(STEP_TITLES) - 1:
            self._finish()
            return
        new = self._index + delta
        if not 0 <= new < len(STEP_TITLES):
            return
        self._index = new
        self.stack.set_visible_child_name(["checks", "source", "download", "finish"][new])
        if new == 1 and not self._probe_done:
            self._run_probe()
        if new == 2:
            self._refresh_download_state()
        if new == 3:
            note = "点击「完成」保存设置并开始使用。"
            if self._permission_fixed:
                note += "\n注意：刚修复了权限，请注销并重新登录后再使用右 Alt 和自动输入。"
            if self.online:
                note += "\n在线 API 的地址和 Key 请在主界面「识别设置」里填写。"
            self.finish_note.set_label(note)
        self._refresh_nav()

    def _refresh_nav(self) -> None:
        self.step_label.set_label(f"第 {self._index + 1} / {len(STEP_TITLES)} 步 · {STEP_TITLES[self._index]}")
        self.back_button.set_sensitive(self._index > 0)
        if self._index == len(STEP_TITLES) - 1:
            self.next_button.set_label("完成")
            self.next_button.set_sensitive(True)
        else:
            self.next_button.set_label("下一步")
            can_next = True
            if self._index == 2 and not self.online:
                can_next = models.is_cached(self.model, self.source) and not self._downloading
            self.next_button.set_sensitive(can_next)

    def _finish(self, skipped: bool = False) -> None:
        updates: dict[str, Any] = {"setup_completed": True}
        if not skipped:
            updates["model_source"] = self.source
            updates["local_model"] = self.model
            updates["backend"] = "online" if self.online else "local"
        self.on_finished(updates)

    def build(self) -> Gtk.Widget:
        return self.widget
