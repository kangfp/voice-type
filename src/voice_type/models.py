"""本地模型目录与下载：Hugging Face / hf-mirror / 魔搭 ModelScope 三个来源。

模型本体是 faster-whisper 用的 CTranslate2 格式。三个来源的仓库内容一致，
下载后统一得到本地目录，WhisperModel 直接加载，不需要格式转换。
"""

from __future__ import annotations

import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SOURCE_HF = "huggingface"
SOURCE_MIRROR = "hf-mirror"
SOURCE_MS = "modelscope"

# 展示顺序即向导里的选项顺序
SOURCES: list[tuple[str, str, str]] = [
    (SOURCE_HF, "Hugging Face（国外官方源）", "https://huggingface.co"),
    (SOURCE_MIRROR, "HF 镜像 hf-mirror.com（国内可用）", "https://hf-mirror.com"),
    (SOURCE_MS, "魔搭 ModelScope（国内官方源）", "https://modelscope.cn"),
]
SOURCE_IDS = [s[0] for s in SOURCES]
SOURCE_LABELS = {s[0]: s[1] for s in SOURCES}

ProgressCallback = Callable[[float, str], None]
StatusCallback = Callable[[str], None]


class ModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelSpec:
    size_gb: float
    note: str


# 体积是 int8 量化前的近似值，仅用于向导展示
MODEL_SPECS: dict[str, ModelSpec] = {
    "tiny": ModelSpec(0.075, "最快，准确率最低，适合试机"),
    "base": ModelSpec(0.14, "很快，简单句子够用"),
    "small": ModelSpec(0.46, "速度与准确率均衡"),
    "medium": ModelSpec(1.5, "较准，CPU 上偏慢"),
    "large-v3-turbo": ModelSpec(1.6, "推荐：接近 large-v3 的准确率，速度快数倍"),
    "large-v3": ModelSpec(3.1, "最准，体积和耗时最大"),
}
MODEL_IDS = list(MODEL_SPECS)
DEFAULT_MODEL = "large-v3-turbo"

# 魔搭上的 CTranslate2 仓库（已逐个验证存在）
_MS_REPOS = {
    "tiny": "pengzhendong/faster-whisper-tiny",
    "base": "pengzhendong/faster-whisper-base",
    "small": "pengzhendong/faster-whisper-small",
    "medium": "pengzhendong/faster-whisper-medium",
    "large-v3": "pengzhendong/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}


def hf_repo(model: str) -> str:
    """faster-whisper 在 Hugging Face 上的仓库名（turbo 是社区转换版）。"""
    try:
        from faster_whisper.utils import _MODELS

        repo = _MODELS.get(model)
        if repo:
            return repo
    except Exception:  # noqa: BLE001 - faster-whisper 未安装时退回默认命名
        pass
    return f"Systran/faster-whisper-{model}"


def repo_for(model: str, source: str) -> str:
    if source == SOURCE_MS:
        repo = _MS_REPOS.get(model)
        if not repo:
            raise ModelError(f"魔搭源暂不提供模型 {model}")
        return repo
    return hf_repo(model)


def endpoint_for(source: str) -> str:
    if source == SOURCE_HF:
        return "https://huggingface.co"
    if source == SOURCE_MS:
        return "https://modelscope.cn"
    return "https://hf-mirror.com"


def apply_endpoint(source: str) -> None:
    """按来源设置 HF_ENDPOINT；魔搭不走 huggingface_hub，无需设置。"""
    if source == SOURCE_MS:
        return
    os.environ["HF_ENDPOINT"] = endpoint_for(source)
    # hf-mirror 不支持 Xet 传输；官方源禁用也无副作用
    os.environ["HF_HUB_DISABLE_XET"] = "1"


def modelscope_cache_root() -> Path:
    base = os.environ.get("MODELSCOPE_CACHE")
    if base:
        return Path(base) / "hub"
    return Path.home() / ".cache" / "modelscope" / "hub"


def hf_cache_root() -> Path:
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def ms_model_dir(model: str) -> Path:
    """魔搭新版缓存的仓库根目录：hub/models/<owner>--<name>/。"""
    repo = _MS_REPOS.get(model, f"pengzhendong/faster-whisper-{model}")
    owner, name = repo.split("/", 1)
    return modelscope_cache_root() / "models" / f"{owner}--{name}"


def _ms_find_snapshot(model: str) -> Path | None:
    """兼容新旧两种魔搭缓存布局，找到含 model.bin 的目录。"""
    snapshots = ms_model_dir(model) / "snapshots"
    if snapshots.is_dir():
        candidates = sorted(snapshots.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        for snap in candidates:
            if (snap / "model.bin").exists():
                return snap
    # 旧版布局：hub/<owner>/<name>/
    repo = _MS_REPOS.get(model, f"pengzhendong/faster-whisper-{model}")
    legacy = modelscope_cache_root() / repo
    if (legacy / "model.bin").exists():
        return legacy
    return None


def hf_model_dir(model: str) -> Path:
    return hf_cache_root() / ("models--" + hf_repo(model).replace("/", "--"))


def _hf_snapshot_dir(model: str) -> Path | None:
    """HF 缓存里最新快照目录；没有完整快照时返回 None。"""
    snapshots = hf_model_dir(model) / "snapshots"
    if not snapshots.is_dir():
        return None
    candidates = sorted(snapshots.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for snap in candidates:
        if (snap / "model.bin").exists():
            return snap
    return None


def is_cached(model: str, source: str) -> bool:
    if source == SOURCE_MS:
        return _ms_find_snapshot(model) is not None
    return _hf_snapshot_dir(model) is not None


def cached_model_dir(model: str, source: str) -> Path | None:
    """已缓存则返回可直接加载的本地目录，否则 None。"""
    if source == SOURCE_MS:
        return _ms_find_snapshot(model)
    return _hf_snapshot_dir(model)


def resolve_model(
    model: str,
    source: str,
    status: StatusCallback | None = None,
    progress: ProgressCallback | None = None,
) -> str:
    """返回可加载的模型路径；未缓存时先下载。供 transcribe 统一调用。"""
    if source not in SOURCE_IDS:
        source = SOURCE_MIRROR
    cached = cached_model_dir(model, source)
    if cached is not None:
        return str(cached)
    if status:
        status(f"正在从 {SOURCE_LABELS[source]} 下载模型 {model}…")
    return download_model(model, source, status=status, progress=progress)


def _make_tqdm(progress: ProgressCallback, cancel: threading.Event | None = None):
    """把 huggingface_hub 的 tqdm 进度桥接到 (0..1, 描述) 回调。"""
    from tqdm import tqdm as _tqdm

    class _ProgressTqdm(_tqdm):
        def display(self, msg=None, pos=None):
            # 取消时在这里抛异常，中断 huggingface_hub 的下载循环
            _check_cancel(cancel)
            try:
                total = self.total or 0
                frac = (self.n / total) if total else 0.0
                desc = self.desc or ""
                progress(min(1.0, max(0.0, frac)), str(desc))
            except Exception:  # noqa: BLE001 - 进度回调失败不能影响下载
                pass
            return super().display(msg, pos)

    return _ProgressTqdm


def download_model(
    model: str,
    source: str,
    status: StatusCallback | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> str:
    """下载模型到本地缓存，返回模型目录。支持断点续传（两个源都原生支持）。"""
    if model not in MODEL_SPECS:
        raise ModelError(f"未知模型：{model}")
    if source not in SOURCE_IDS:
        raise ModelError(f"未知模型来源：{source}")
    repo = repo_for(model, source)
    if source == SOURCE_MS:
        return _download_ms(model, repo, status, cancel, progress)
    return _download_hf(model, repo, source, status, progress, cancel)


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ModelError("下载已取消")


def _download_hf(
    model: str,
    repo: str,
    source: str,
    status: StatusCallback | None,
    progress: ProgressCallback | None,
    cancel: threading.Event | None,
) -> str:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise ModelError("缺少 huggingface_hub（faster-whisper 的依赖），请重新安装") from exc
    apply_endpoint(source)
    kwargs: dict = {
        "repo_id": repo,
        "allow_patterns": ["*.json", "*.bin", "*.txt", "*.model"],
        "max_workers": 4,
    }
    if progress is not None:
        kwargs["tqdm_class"] = _make_tqdm(progress, cancel)
    elif cancel is not None:
        kwargs["tqdm_class"] = _make_tqdm(lambda *_: None, cancel)
    if status:
        status(f"正在从 {endpoint_for(source)} 下载 {repo}…")
    try:
        path = snapshot_download(**kwargs)
    except Exception as exc:  # noqa: BLE001 - 网络错误统一提示
        _check_cancel(cancel)
        raise ModelError(f"下载失败（{endpoint_for(source)}）：{exc}") from exc
    _check_cancel(cancel)
    return path


def _download_ms(
    model: str,
    repo: str,
    status: StatusCallback | None,
    cancel: threading.Event | None,
    progress: ProgressCallback | None = None,
) -> str:
    try:
        from modelscope import snapshot_download
        from modelscope.hub.callback import ProgressCallback as MsProgressCallback
    except ImportError as exc:
        raise ModelError("缺少 modelscope 库，请运行：pip install modelscope") from exc
    _check_cancel(cancel)
    if status:
        status(f"正在从魔搭下载 {repo}…")

    class _Bridge(MsProgressCallback):
        """把魔搭的每文件进度桥接到统一的 (0..1, 描述) 回调。"""

        def __init__(self, filename: str, file_size: int) -> None:
            super().__init__(filename, file_size)
            self._done = 0

        def update(self, size: int) -> None:
            _check_cancel(cancel)
            if progress is None:
                return
            self._done += size
            frac = (self._done / self.file_size) if self.file_size else 0.0
            try:
                progress(min(1.0, max(0.0, frac)), self.filename)
            except Exception:  # noqa: BLE001 - 进度回调失败不能影响下载
                pass

    try:
        path = snapshot_download(
            repo,
            cache_dir=str(modelscope_cache_root()),
            progress_callbacks=[_Bridge],
        )
    except Exception as exc:  # noqa: BLE001
        _check_cancel(cancel)
        raise ModelError(f"魔搭下载失败：{exc}") from exc
    _check_cancel(cancel)
    return path


def free_disk_gb(path: Path | None = None) -> float:
    target = path or Path.home()
    try:
        return shutil.disk_usage(target).free / (1 << 30)
    except OSError:
        return 0.0


def probe_source(timeout: float = 3.0) -> str:
    """探测哪个源可达，返回推荐来源。全部不可达时退回魔搭。"""
    import httpx

    for source, _label, url in SOURCES:
        try:
            resp = httpx.get(url, timeout=timeout, follow_redirects=True)
            if resp.status_code < 500:
                return source
        except httpx.HTTPError:
            continue
    return SOURCE_MS
