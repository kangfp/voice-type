"""语音识别：本地 faster-whisper 或 OpenAI 兼容的在线 API。"""

from __future__ import annotations

import os
import re
import threading
import wave
from pathlib import Path
from typing import Any, Callable

# 国内网络访问 HuggingFace 经常超时；默认走镜像，用户可用 HF_ENDPOINT 覆盖。
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# hf-mirror 不支持 Xet 传输，禁用后走普通 HTTP 下载
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

StatusCallback = Callable[[str], None]

_MODEL: Any = None
_MODEL_KEY: tuple[str, str] | None = None
_MODEL_LOCK = threading.Lock()


class TranscribeError(RuntimeError):
    pass


def transcription_url(base_url: str) -> str:
    """把用户填的 base URL 规范成 /v1/audio/transcriptions 完整地址。"""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise TranscribeError("在线 API 的 Base URL 为空")
    if base.endswith("/audio/transcriptions"):
        return base
    if base.endswith("/v1"):
        return base + "/audio/transcriptions"
    return base + "/v1/audio/transcriptions"


_CJK = r"一-鿿"


def normalize_chinese(text: str) -> str:
    """Whisper 中文常输出半角标点，转成全角并去掉汉字间多余空格。"""
    if not text:
        return text
    text = re.sub(f"(?<=[{_CJK}]),(?=[{_CJK}])", "，", text)
    text = re.sub(f"(?<=[{_CJK}])\\.(?=$|[{_CJK}])", "。", text)
    text = re.sub(f"(?<=[{_CJK}])\\?(?=$|[{_CJK}])", "？", text)
    text = re.sub(f"(?<=[{_CJK}])!(?=$|[{_CJK}])", "！", text)
    text = re.sub(f"(?<=[{_CJK}]):(?=[{_CJK}])", "：", text)
    text = re.sub(f"(?<=[{_CJK}]);(?=[{_CJK}])", "；", text)
    text = re.sub(f"(?<=[{_CJK}])[ \\t]+(?=[{_CJK}])", "", text)
    return text


def _local_prompt(language: str, configured: str) -> str | None:
    base = "以下是普通话的句子，请使用简体中文和标点。" if language == "zh" else ""
    extra = configured.strip()
    if extra:
        # 用户补充的术语/人名追加在默认提示后面，用作解码偏置
        return f"{base} {extra}".strip()
    return base or None


def _load_local(model_size: str, compute_type: str) -> Any:
    global _MODEL, _MODEL_KEY
    key = (model_size, compute_type)
    with _MODEL_LOCK:
        if _MODEL is not None and _MODEL_KEY == key:
            return _MODEL
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on install
            raise TranscribeError(
                "未安装 faster-whisper，请运行 install.sh 或 uv pip install -e ."
            ) from exc
        _MODEL = None
        _MODEL_KEY = None
        try:
            _MODEL = WhisperModel(
                model_size,
                device="cpu",
                compute_type=compute_type,
                cpu_threads=os.cpu_count() or 4,
            )
        except Exception as exc:  # noqa: BLE001 - 网络/模型/内存错误统一提示
            raise TranscribeError(f"加载本地模型 {model_size} 失败：{exc}") from exc
        _MODEL_KEY = key
        return _MODEL


def preload_local(model_size: str, compute_type: str = "int8") -> None:
    """提前下载并加载模型（GUI 的「预下载模型」按钮用）。"""
    _load_local(model_size, compute_type)


def _read_wav_float32(path: str) -> Any:
    """自己解码 WAV，避免 faster-whisper 依赖的 PyAV 版本问题。"""
    import numpy as np

    with wave.open(path, "rb") as fh:
        channels = fh.getnchannels()
        width = fh.getsampwidth()
        rate = fh.getframerate()
        frames = fh.readframes(fh.getnframes())
    if rate != 16000 or channels != 1 or width != 2:
        raise TranscribeError(
            f"不支持的音频格式：{rate}Hz / {channels} 声道 / {width * 8}bit（需要 16k 单声道 16bit）"
        )
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe_local(
    path: str,
    cfg: dict[str, Any],
    status: StatusCallback | None = None,
) -> str:
    model_size = str(cfg.get("local_model") or "large-v3-turbo")
    compute_type = str(cfg.get("compute_type") or "int8")
    if status:
        status(f"正在加载模型 {model_size}（首次使用会从 hf-mirror 下载）…")
    model = _load_local(model_size, compute_type)
    language = str(cfg.get("language") or "zh")
    lang = None if language in ("", "auto") else language
    if status:
        status("正在识别…")
    audio = _read_wav_float32(path)
    try:
        segments, info = model.transcribe(
            audio,
            language=lang,
            beam_size=int(cfg.get("beam_size") or 5),
            vad_filter=bool(cfg.get("vad_filter")),
            condition_on_previous_text=False,
            initial_prompt=_local_prompt(language, str(cfg.get("initial_prompt") or "")),
        )
        text = "".join(seg.text for seg in segments).strip()
    except Exception as exc:  # noqa: BLE001
        raise TranscribeError(f"本地识别失败：{exc}") from exc
    detected = getattr(info, "language", None) if info is not None else None
    if language == "zh" or detected == "zh":
        text = normalize_chinese(text)
    return text


def transcribe_online(
    path: str,
    cfg: dict[str, Any],
    status: StatusCallback | None = None,
) -> str:
    import httpx

    url = transcription_url(str(cfg.get("api_base_url") or ""))
    api_key = str(cfg.get("api_key") or "").strip()
    model = str(cfg.get("api_model") or "whisper-1")
    language = str(cfg.get("language") or "zh")
    data: dict[str, str] = {"model": model, "response_format": "json"}
    if language not in ("", "auto"):
        data["language"] = language
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if status:
        status("正在请求在线识别 API…")
    try:
        with open(path, "rb") as fh:
            resp = httpx.post(
                url,
                headers=headers,
                data=data,
                files={"file": (Path(path).name, fh, "audio/wav")},
                timeout=180.0,
            )
    except httpx.HTTPError as exc:
        raise TranscribeError(f"在线 API 请求失败：{exc}") from exc
    if resp.status_code >= 400:
        snippet = resp.text[:300].strip()
        raise TranscribeError(f"在线 API 返回 {resp.status_code}：{snippet}")
    ctype = resp.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            payload = resp.json()
        except ValueError as exc:
            raise TranscribeError("在线 API 返回了无效 JSON") from exc
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise TranscribeError(f"在线 API 响应缺少 text 字段：{str(payload)[:200]}")
        result = text.strip()
    else:
        # 有些自建服务直接返回纯文本
        result = resp.text.strip()
    if language == "zh":
        result = normalize_chinese(result)
    return result


def transcribe(
    path: str,
    cfg: dict[str, Any],
    status: StatusCallback | None = None,
) -> str:
    backend = str(cfg.get("backend") or "local")
    if backend == "online":
        return transcribe_online(path, cfg, status)
    return transcribe_local(path, cfg, status)
