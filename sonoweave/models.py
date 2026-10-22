"""Pinned model provisioning, separate from offline inference. Never uploads audio/text."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import time

import requests
from filelock import FileLock, Timeout

from .config import TRANSLATION_MODELS, model_location, speaker_model_location, translation_model_location


class DownloadCancelled(Exception):
    pass


@dataclass(frozen=True)
class ModelFile:
    repo: str
    revision: str
    filename: str
    size: int
    sha256: str


ASR_REPO = "Systran/faster-whisper-small"
ASR_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
ASR_FILES = (
    ("model.bin", 483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
    ("config.json", 2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
    ("tokenizer.json", 2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
    ("vocabulary.txt", 459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
)

SPEAKER_FILES = (
    ModelFile("csukuangfj/sherpa-onnx-pyannote-segmentation-3-0", "9403a6902bb58e3d5ae8c7e77c3422de279db2e0",
              "model.int8.onnx", 1540506, "d582f4b4c6b48205de7e0643c57df0df5615a3c176189be3fc461e9d18827b5d"),
    ModelFile("csukuangfj/speaker-embedding-models", "0743f301363dec56491a490f6d6cbc9d67f9a3bf",
              "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx", 28281164,
              "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2"),
)

SEPARATION_FILE = ModelFile("welcomyou/convtasnet-libri2mix-16k-onnx", "da50e0fa7789356790994bc898290134fef5d42d",
                           "convtasnet_16k.onnx", 20154131,
                           "22185d8e13bf5251c0eeab09e52099ac76c063cd9a5e5df1f5c242f535f6f151")


def ensure_speaker_models(progress=lambda text: None, cancelled=lambda: False):
    for spec in SPEAKER_FILES:
        ensure_file(speaker_model_location(spec.filename), spec, progress, cancelled)


def _check_cancel(cancelled):
    if cancelled():
        raise DownloadCancelled


def _valid(path, spec, cancelled):
    if not path.is_file() or path.stat().st_size != spec.size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            _check_cancel(cancelled)
            digest.update(block)
    return digest.hexdigest() == spec.sha256


def ensure_file(path: Path, spec: ModelFile, progress=lambda text: None, cancelled=lambda: False):
    _check_cancel(cancelled)
    progress(f"正在校验 {spec.filename}…")
    if _valid(path, spec, cancelled):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(path) + ".lock")
    while True:
        _check_cancel(cancelled)
        try:
            lock.acquire(timeout=0.2)
            break
        except Timeout:
            progress(f"正在等待模型文件：{spec.filename}")
    try:
        progress(f"正在校验 {spec.filename}…")
        if _valid(path, spec, cancelled):
            return path
        partial = path.with_suffix(path.suffix + ".part")
        if _valid(partial, spec, cancelled):
            partial.replace(path)
            return path
        if partial.exists() and partial.stat().st_size >= spec.size:
            partial.unlink()
        endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
        url = f"{endpoint}/{spec.repo}/resolve/{spec.revision}/{spec.filename}"
        for attempt in range(3):
            _check_cancel(cancelled)
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {"Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            progress(f"正在下载 {spec.filename} · {offset / spec.size:.0%}")
            try:
                with requests.get(url, headers=headers, stream=True, timeout=(15, 30)) as response:
                    response.raise_for_status()
                    if response.status_code == 206:
                        content_range = response.headers.get("Content-Range", "")
                        if not content_range.startswith(f"bytes {offset}-") or not content_range.endswith(f"/{spec.size}"):
                            raise RuntimeError("模型下载断点响应无效，请右键重试")
                    elif response.status_code == 200:
                        offset = 0  # Server ignored Range: restart, never append a full file.
                    else:
                        raise RuntimeError("模型下载响应无效，请右键重试")
                    last_report = 0.0
                    with partial.open("ab" if offset else "wb") as stream:
                        for block in response.iter_content(chunk_size=1024 * 1024):
                            _check_cancel(cancelled)
                            if not block:
                                continue
                            offset += len(block)
                            if offset > spec.size:
                                raise RuntimeError("模型下载大小异常，请右键重试")
                            stream.write(block)
                            now = time.monotonic()
                            if now - last_report >= 0.5:
                                progress(f"正在下载 {spec.filename} · {offset / spec.size:.0%} · {offset / 1e6:.0f}/{spec.size / 1e6:.0f} MB")
                                last_report = now
                if offset != spec.size:
                    raise requests.ConnectionError("Incomplete model download")
                break
            except requests.RequestException as exc:
                if attempt == 2:
                    raise RuntimeError("模型下载失败，已保留下载进度；请检查网络后右键重试") from exc
        progress(f"正在校验 {spec.filename}…")
        if not _valid(partial, spec, cancelled):
            partial.unlink(missing_ok=True)
            raise RuntimeError("模型校验失败，请右键重试下载")
        _check_cancel(cancelled)
        partial.replace(path)
        return path
    finally:
        lock.release()


def ensure_models(model="4b", progress=lambda text: None, cancelled=lambda: False, *, speaker_colors=False):
    spec = TRANSLATION_MODELS[model]
    asr = Path(model_location())
    if os.environ.get("SONOWEAVE_MODEL"):
        if not (asr / "model.bin").is_file():
            raise RuntimeError("自定义识别模型不存在，请检查 SONOWEAVE_MODEL")
    else:
        for name, size, sha in ASR_FILES:
            ensure_file(asr / name, ModelFile(ASR_REPO, ASR_REVISION, name, size, sha), progress, cancelled)
    if speaker_colors:
        ensure_speaker_models(progress, cancelled)
    path = translation_model_location(model)
    ensure_file(path, ModelFile(spec["repo"], spec["revision"], spec["filename"], spec["bytes"], spec["sha256"]), progress, cancelled)
    return path
