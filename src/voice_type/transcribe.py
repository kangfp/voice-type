"""语音识别：本地 faster-whisper 或 OpenAI 兼容的在线 API。"""

from __future__ import annotations

import os
import re
import threading
import wave
from pathlib import Path
from typing import Any, Callable

from . import models

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
    # 逗号后可以是汉字或段尾（流式分段时逗号可能落在片段末尾）
    text = re.sub(f"(?<=[{_CJK}]),(?=[{_CJK}]|$)", "，", text)
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


def _load_local(
    model_size: str,
    compute_type: str,
    source: str = "hf-mirror",
    status: StatusCallback | None = None,
    progress: models.ProgressCallback | None = None,
) -> Any:
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
            # 统一解析成本地目录：已缓存直接用，未缓存按所选来源下载
            model_path = models.resolve_model(model_size, source, status=status, progress=progress)
            _MODEL = WhisperModel(
                model_path,
                device="cpu",
                compute_type=compute_type,
                cpu_threads=os.cpu_count() or 4,
            )
        except models.ModelError as exc:
            raise TranscribeError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - 网络/模型/内存错误统一提示
            raise TranscribeError(f"加载本地模型 {model_size} 失败：{exc}") from exc
        _MODEL_KEY = key
        return _MODEL


def preload_local(
    model_size: str,
    compute_type: str = "int8",
    source: str = "hf-mirror",
    status: StatusCallback | None = None,
    progress: models.ProgressCallback | None = None,
) -> None:
    """提前下载并加载模型（GUI 的「预下载模型」按钮用）。"""
    _load_local(model_size, compute_type, source, status, progress)


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


# 流式切块上限：超过此时长的语音按静音边界拆成多块逐块识别
_STREAM_CHUNK_SECONDS = 8.0


def _quiet_cut(audio: Any, target: int, window: int = 16000) -> int:
    """在 target 前后 1 秒内找能量最低的 50ms 帧作为切点，尽量不切断字。"""
    import numpy as np

    lo = max(0, target - window)
    hi = min(len(audio), target + window)
    step = 800  # 50ms @16kHz
    best, best_energy = target, None
    for pos in range(lo, hi, step):
        frame = audio[pos : pos + step]
        if len(frame) == 0:
            continue
        energy = float(np.mean(frame * frame))
        if best_energy is None or energy < best_energy:
            best_energy, best = energy, pos
    return best


def _speech_chunks(audio: Any, chunk_samples: int) -> list[tuple[int, int]]:
    """用 Silero VAD 找语音段，按静音边界合并成不超过 chunk_samples 的块。

    Whisper 默认把 2 秒内的停顿合并，长音频往往整段只有一个 segment，
    无法流式输出。这里用更灵敏的静音阈值（250ms）在语句间隙处切块；
    自然语速下停顿可能更短，超长的单一语音区再按能量最低点强制拆分。
    """
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    timestamps = get_speech_timestamps(
        audio,
        VadOptions(min_silence_duration_ms=250, speech_pad_ms=200),
    )
    if not timestamps:
        return []
    merged: list[tuple[int, int]] = []
    start, end = timestamps[0]["start"], timestamps[0]["end"]
    for ts in timestamps[1:]:
        if ts["end"] - start <= chunk_samples:
            end = ts["end"]
        else:
            merged.append((start, end))
            start, end = ts["start"], ts["end"]
    merged.append((start, end))
    # 强制拆分超长块（连续说话没有明显停顿时 VAD 只给一个大区）
    chunks: list[tuple[int, int]] = []
    for start, end in merged:
        while end - start > chunk_samples:
            cut = _quiet_cut(audio, start + chunk_samples)
            cut = max(start + chunk_samples // 2, cut)
            # 残余不足 1 秒就不拆了，并入本块——碎块会诱发 Whisper 幻听
            if end - cut < 16000:
                break
            chunks.append((start, cut))
            start = cut
        # 跳过 0.3 秒以下的碎块（VAD 噪声点），同样为避免幻听
        if end - start >= 4800:
            chunks.append((start, end))
    return chunks


def transcribe_local(
    path: str,
    cfg: dict[str, Any],
    status: StatusCallback | None = None,
    on_segment: Callable[[str], None] | None = None,
) -> str:
    model_size = str(cfg.get("local_model") or "large-v3-turbo")
    compute_type = str(cfg.get("compute_type") or "int8")
    source = str(cfg.get("model_source") or "hf-mirror")
    if status:
        status(f"正在加载模型 {model_size}…")
    model = _load_local(model_size, compute_type, source, status)
    language = str(cfg.get("language") or "zh")
    lang = None if language in ("", "auto") else language
    if status:
        status("正在识别…")
    audio = _read_wav_float32(path)
    prompt = _local_prompt(language, str(cfg.get("initial_prompt") or ""))
    beam = int(cfg.get("beam_size") or 5)
    try:
        ranges = _speech_chunks(audio, int(_STREAM_CHUNK_SECONDS * 16000))
        if not ranges:
            return ""
        pieces: list[str] = []
        zh_mode = language == "zh"
        for idx, (start, end) in enumerate(ranges, 1):
            if status and len(ranges) > 1:
                status(f"正在识别（第 {idx}/{len(ranges)} 段）…")
            segments, info = model.transcribe(
                audio[start:end],
                language=lang,
                beam_size=beam,
                # 块已按 VAD 裁剪掉静音，无需再过滤
                vad_filter=False,
                condition_on_previous_text=False,
                initial_prompt=prompt,
            )
            detected = getattr(info, "language", None) if info is not None else None
            if detected == "zh":
                zh_mode = True
            # 逐段流式输出：每解码完一段就回调一次，而不是等全部结束
            for seg in segments:
                piece = seg.text
                if zh_mode:
                    piece = normalize_chinese(piece).strip()
                if not piece.strip():
                    continue
                pieces.append(piece)
                if on_segment:
                    on_segment(piece)
        text = "".join(pieces).strip()
    except Exception as exc:  # noqa: BLE001
        raise TranscribeError(f"本地识别失败：{exc}") from exc
    return text


def transcribe_online(
    path: str,
    cfg: dict[str, Any],
    status: StatusCallback | None = None,
    on_segment: Callable[[str], None] | None = None,
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
    if result and on_segment:
        on_segment(result)
    return result


def transcribe(
    path: str,
    cfg: dict[str, Any],
    status: StatusCallback | None = None,
    on_segment: Callable[[str], None] | None = None,
) -> str:
    backend = str(cfg.get("backend") or "local")
    if backend == "online":
        return transcribe_online(path, cfg, status, on_segment)
    return transcribe_local(path, cfg, status, on_segment)
