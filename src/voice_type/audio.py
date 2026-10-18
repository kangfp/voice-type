"""麦克风录音：用 pw-record（PipeWire 原生工具）录制裸 PCM，再封装为 WAV。

为什么不用 parecord：Ubuntu 24.04 上 parecord 经 PulseAudio 兼容层可能
录不到任何数据（实测 0 字节）；pw-record 稳定可靠，也不需要额外依赖。
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import wave

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # s16


class AudioError(RuntimeError):
    pass


def wav_duration(path: str) -> float:
    try:
        with wave.open(path, "rb") as fh:
            frames = fh.getnframes()
            rate = fh.getframerate() or SAMPLE_RATE
            return frames / float(rate)
    except (wave.Error, EOFError, OSError):
        try:
            size = os.path.getsize(path)
        except OSError:
            return 0.0
        return max(0.0, (size - 44) / (SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS))


def _raw_to_wav(raw_path: str) -> str:
    wav_path = raw_path[:-4] + ".wav" if raw_path.endswith(".raw") else raw_path + ".wav"
    with wave.open(wav_path, "wb") as out:
        out.setnchannels(CHANNELS)
        out.setsampwidth(SAMPLE_WIDTH)
        out.setframerate(SAMPLE_RATE)
        with open(raw_path, "rb") as src:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                out.writeframes(chunk)
    return wav_path


class Recorder:
    """一次录音会话。start() 后调用 stop() 拿 WAV 路径。"""

    def __init__(self, source: str = "") -> None:
        self.source = source.strip()
        self.path: str | None = None
        self._raw_path: str | None = None
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self._proc is not None:
            raise AudioError("录音已经在进行")
        fd, raw_path = tempfile.mkstemp(prefix="voice-type-", suffix=".raw")
        os.close(fd)
        self._raw_path = raw_path
        cmd = [
            "pw-record",
            "--rate",
            str(SAMPLE_RATE),
            "--channels",
            str(CHANNELS),
            "--format",
            "s16",
        ]
        if self.source:
            cmd.extend(["--target", self.source])
        cmd.append(raw_path)
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            self._cleanup_files()
            raise AudioError("找不到 pw-record，请安装 pipewire-bin") from exc
        time.sleep(0.15)
        code = self._proc.poll()
        if code is not None:
            err = self._read_stderr(self._proc)
            self._cleanup_files()
            self._proc = None
            raise AudioError(f"无法开始录音（pw-record 退出码 {code}）：{err}")

    def stop(self) -> str:
        """优雅停止录音并返回 WAV 路径。录音太短会抛 AudioError。"""
        proc = self._proc
        raw_path = self._raw_path
        if proc is None or raw_path is None:
            raise AudioError("当前没有录音")
        self._proc = None
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            err = self._read_stderr(proc)
        else:
            err = self._read_stderr(proc)

        try:
            size = os.path.getsize(raw_path)
        except OSError:
            size = 0
        duration = size / float(SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS)
        if duration < 0.25:
            self._cleanup_files()
            raise AudioError(f"录音太短（{duration:.2f}s）：{err or '没有采集到声音'}")

        wav_path = _raw_to_wav(raw_path)
        try:
            os.unlink(raw_path)
        except OSError:
            pass
        self._raw_path = None
        self.path = wav_path
        return wav_path

    def cancel(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        self._cleanup_files()

    def _read_stderr(self, proc: subprocess.Popen[bytes] | None = None) -> str:
        proc = proc or self._proc
        if proc is None or proc.stderr is None:
            return ""
        try:
            data = proc.stderr.read() or b""
        except (OSError, ValueError):
            data = b""
        return data.decode("utf-8", "replace").strip()

    def _cleanup_files(self) -> None:
        for path in (self._raw_path, self.path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        self._raw_path = None
        self.path = None
