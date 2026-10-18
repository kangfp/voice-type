import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_type.audio import wav_duration  # noqa: E402
from voice_type.config import DEFAULTS, load_config, save_config  # noqa: E402
from voice_type.controller import hotkey_label  # noqa: E402
from voice_type.inject import InjectError, validate_shortcut  # noqa: E402
from voice_type.transcribe import (  # noqa: E402
    TranscribeError,
    normalize_chinese,
    transcription_url,
)


class UrlTests(unittest.TestCase):
    def test_v1_base(self):
        self.assertEqual(
            transcription_url("https://api.openai.com/v1"),
            "https://api.openai.com/v1/audio/transcriptions",
        )

    def test_full_endpoint(self):
        self.assertEqual(
            transcription_url("https://example.com/audio/transcriptions"),
            "https://example.com/audio/transcriptions",
        )

    def test_bare_host(self):
        self.assertEqual(
            transcription_url("http://127.0.0.1:8000/"),
            "http://127.0.0.1:8000/v1/audio/transcriptions",
        )

    def test_empty(self):
        with self.assertRaises(TranscribeError):
            transcription_url("   ")


class ChineseTextTests(unittest.TestCase):
    def test_punctuation_and_spaces(self):
        self.assertEqual(
            normalize_chinese("今天下午3点开会,请把结构计算书带过来."),
            "今天下午3点开会，请把结构计算书带过来。",
        )
        self.assertEqual(normalize_chinese("这是 一个 测试"), "这是一个测试")

    def test_keeps_decimal_point(self):
        self.assertEqual(normalize_chinese("梁高3.5米"), "梁高3.5米")

    def test_english_untouched(self):
        self.assertEqual(normalize_chinese("hello, world."), "hello, world.")


class ShortcutTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(validate_shortcut("CTRL+V"), "ctrl+v")
        self.assertEqual(validate_shortcut("ctrl+shift+v"), "ctrl+shift+v")

    def test_reject_injection(self):
        for bad in ["", "ctrl+v; rm -rf ~", "ctrl v", "a" * 100]:
            with self.assertRaises(InjectError):
                validate_shortcut(bad)


class ConfigTests(unittest.TestCase):
    def test_roundtrip_unknown_keys_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            cfg = dict(DEFAULTS)
            cfg["backend"] = "online"
            cfg["evil"] = "ignored"
            save_config(cfg, path)
            loaded = load_config(path)
            self.assertEqual(loaded["backend"], "online")
            self.assertNotIn("evil", loaded)
            self.assertEqual(loaded["local_model"], "large-v3-turbo")

    def test_missing_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = load_config(Path(tmp) / "nope.json")
            self.assertEqual(loaded, DEFAULTS)

    def test_broken_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_config(path), DEFAULTS)


class AudioTests(unittest.TestCase):
    def test_wav_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.wav")
            with wave.open(path, "wb") as fh:
                fh.setnchannels(1)
                fh.setsampwidth(2)
                fh.setframerate(16000)
                fh.writeframes(b"\x00\x00" * 16000)
            self.assertAlmostEqual(wav_duration(path), 1.0, places=3)


class LabelTests(unittest.TestCase):
    def test_hotkey_label(self):
        self.assertEqual(hotkey_label("<Ctrl><Alt>v"), "Ctrl+Alt+V")
        self.assertEqual(hotkey_label("<Super><Shift>space"), "Super+Shift+SPACE")


class PushToTalkTests(unittest.TestCase):
    class FakeController:
        def __init__(self) -> None:
            self.state = "idle"
            self.calls: list[str] = []

        def start(self) -> None:
            self.calls.append("start")
            self.state = "recording"

        def stop(self) -> None:
            self.calls.append("stop")
            self.state = "transcribing"

        def cancel(self) -> None:
            self.calls.append("cancel")
            self.state = "idle"

    def test_press_and_release(self):
        from voice_type.ptt import PushToTalkListener

        controller = self.FakeController()
        listener = PushToTalkListener(controller, min_ms=0)
        listener._on_press()
        self.assertEqual(controller.calls, ["start"])
        listener._on_release()
        self.assertEqual(controller.calls, ["start", "stop"])

    def test_short_press_cancels(self):
        from voice_type.ptt import PushToTalkListener

        controller = self.FakeController()
        listener = PushToTalkListener(controller, min_ms=10_000)
        listener._on_press()
        listener._on_release()
        self.assertEqual(controller.calls, ["start", "cancel"])

    def test_ignores_when_busy(self):
        from voice_type.ptt import PushToTalkListener

        controller = self.FakeController()
        controller.state = "transcribing"
        listener = PushToTalkListener(controller, min_ms=0)
        listener._on_press()
        self.assertEqual(controller.calls, [])

    def test_reads_events_from_fifo(self):
        import threading

        from voice_type.ptt import EVENT, KEY_RIGHTALT, PushToTalkListener

        controller = self.FakeController()
        with tempfile.TemporaryDirectory() as tmp:
            fifo = os.path.join(tmp, "events")
            os.mkfifo(fifo)
            # 先占住一个读端，避免监听线程在 writer 打开前读到 EOF 退出
            holder = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
            listener = PushToTalkListener(controller, min_ms=0, devices=[fifo])
            listener.start()
            try:
                with open(fifo, "wb", buffering=0) as writer:
                    writer.write(EVENT.pack(0, 0, 1, KEY_RIGHTALT, 1))
                    deadline = time.time() + 2
                    while controller.state != "recording" and time.time() < deadline:
                        time.sleep(0.02)
                    writer.write(EVENT.pack(0, 0, 1, KEY_RIGHTALT, 0))
                deadline = time.time() + 2
                while controller.state != "transcribing" and time.time() < deadline:
                    time.sleep(0.02)
            finally:
                listener.stop()
                os.close(holder)
            self.assertEqual(controller.calls, ["start", "stop"])

    def test_discovers_absolute_device_paths(self):
        from voice_type.ptt import discover_keyboards

        for path in discover_keyboards():
            self.assertTrue(os.path.isabs(path))


class ControllerTests(unittest.TestCase):
    def test_record_transcribe_inject_flow(self):
        from voice_type import controller as controller_mod
        from voice_type.controller import Controller

        results = []
        states = []

        class FakeRecorder:
            def __init__(self, source=""):
                pass

            def start(self):
                pass

            def stop(self):
                return "/tmp/fake.wav"

        with mock.patch.object(controller_mod, "Recorder", FakeRecorder), mock.patch.object(
            controller_mod.transcribe, "transcribe", return_value="你好，世界。"
        ) as fake_transcribe, mock.patch.object(controller_mod, "inject_text") as fake_inject, mock.patch.object(
            controller_mod, "notify"
        ):
            controller = Controller(
                dict(DEFAULTS),
                on_state=states.append,
                on_result=results.append,
            )
            controller.start()
            self.assertEqual(controller.state, "recording")
            controller.stop()
            deadline = time.time() + 5
            while controller.state != "idle" and time.time() < deadline:
                time.sleep(0.02)
            fake_transcribe.assert_called_once()
            fake_inject.assert_called_once()
            self.assertEqual(results, ["你好，世界。"])
            self.assertEqual(controller.state, "idle")
            self.assertEqual(controller.last_text, "你好，世界。")

    def test_inject_failure_keeps_text(self):
        from voice_type import controller as controller_mod
        from voice_type.controller import Controller
        from voice_type.inject import InjectError

        class FakeRecorder:
            def __init__(self, source=""):
                pass

            def start(self):
                pass

            def stop(self):
                return "/tmp/fake.wav"

        with mock.patch.object(controller_mod, "Recorder", FakeRecorder), mock.patch.object(
            controller_mod.transcribe, "transcribe", return_value="文字"
        ), mock.patch.object(
            controller_mod, "inject_text", side_effect=InjectError("no ydotool", copied=True)
        ), mock.patch.object(controller_mod, "notify") as fake_notify:
            controller = Controller(dict(DEFAULTS))
            controller.start()
            controller.stop()
            deadline = time.time() + 5
            while controller.state != "idle" and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(controller.last_text, "文字")
            messages = " ".join(str(c.args) for c in fake_notify.call_args_list)
            self.assertIn("文字已复制", messages)


if __name__ == "__main__":
    unittest.main()
