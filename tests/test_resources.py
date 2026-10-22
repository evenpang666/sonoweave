import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from sonoweave.recognition import AdaptiveRecognizer
from sonoweave.resources import GPU, MIB, available_gpus, gguf_block_count, translation_layers, whisper_compute_types
from sonoweave.translation import Translator


def gguf(path, blocks=32):
    key = b"qwen35.block_count"
    with path.open("wb") as stream:
        stream.write(struct.pack("<4sIQQQ", b"GGUF", 3, 0, 1, len(key)))
        stream.write(key + struct.pack("<II", 4, blocks))
    return path


@pytest.fixture(autouse=True)
def model_size(monkeypatch):
    stat = Path.stat

    def with_weights(path, *args, **kwargs):
        value = stat(path, *args, **kwargs)
        if path.name == "model.gguf":
            return os.stat_result((*value[:6], 2740937888, *value[7:]))
        return value

    monkeypatch.setattr(Path, "stat", with_weights)


def test_pressure_keeps_models_accelerated_when_possible_then_uses_cpu(tmp_path):
    path = gguf(tmp_path / "model.gguf")
    assert gguf_block_count(path) == 32
    roomy = GPU(0, 10 * 1024 * MIB, 12 * 1024 * MIB, "test")
    tight = GPU(0, 3 * 1024 * MIB, 4 * 1024 * MIB, "test")
    full = GPU(0, 500 * MIB, 4 * 1024 * MIB, "test")
    assert translation_layers(path, 2048, roomy)[0] == -1
    assert 0 < translation_layers(path, 2048, tight)[0] < 32
    assert translation_layers(path, 2048, full) == (0,)
    assert whisper_compute_types(roomy, 483546902)[0] == "float16"
    assert whisper_compute_types(GPU(0, 1700 * MIB, tight.total, "test"), 483546902) == ("int8_float16",)
    assert whisper_compute_types(full, 483546902) == ()


def test_visible_devices_remap_gpu_ordinals_and_can_disable_cuda(monkeypatch):
    monkeypatch.setattr("sonoweave.resources.subprocess.run", lambda *a, **k: SimpleNamespace(
        stdout="0, GPU-a, 4000, 8000, A\n1, GPU-b, 6000, 12000, B\n"))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1,0")
    assert [(g.index, g.name) for g in available_gpus()] == [(0, "B"), (1, "A")]
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "GPU-b")
    assert available_gpus()[0].index == 0 and available_gpus()[0].name == "B"
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "-1")
    assert not available_gpus()
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    assert not available_gpus()


def test_lazy_whisper_oom_retries_same_audio_without_publishing_partial_text():
    def broken():
        yield "partial GPU text"
        raise RuntimeError("CUDA out of memory")

    gpu = Mock(model=Mock(device="cuda"))
    gpu.transcribe.return_value = (broken(), "gpu-info")
    cpu = Mock(model=Mock(device="cpu"))
    cpu.transcribe.return_value = (iter(["complete CPU text"]), "cpu-info")
    factory = Mock(return_value=cpu)
    model = AdaptiveRecognizer(gpu, factory)
    audio = np.zeros(16000)
    assert model.transcribe(audio, language="en") == (("complete CPU text",), "cpu-info")
    assert gpu.transcribe.call_args == cpu.transcribe.call_args
    gpu.model.unload_model.assert_called_once()
    factory.assert_called_once()
    assert model.model.device == "cpu"


def test_whisper_input_errors_do_not_reload_or_hide_failure():
    gpu = Mock(model=Mock(device="cuda"))
    gpu.detect_language.side_effect = ValueError("invalid waveform")
    factory = Mock()
    with pytest.raises(ValueError, match="invalid waveform"):
        AdaptiveRecognizer(gpu, factory).detect_language(audio=np.zeros(1))
    factory.assert_not_called()


@pytest.fixture
def fake_llama(tmp_path, monkeypatch):
    package = tmp_path / "llama_cpp"
    (package / "lib").mkdir(parents=True)
    (package / "lib/ggml-cuda.dll").touch()
    factory = Mock()
    module = SimpleNamespace(__file__=str(package / "__init__.py"), Llama=factory,
                             llama_supports_gpu_offload=lambda: True, LLAMA_SPLIT_MODE_NONE=0,
                             StoppingCriteriaList=list)
    monkeypatch.setitem(sys.modules, "llama_cpp", module)
    monkeypatch.setattr("ctranslate2.get_cuda_device_count", lambda: 1)
    monkeypatch.setattr("sonoweave.translation.configure_cuda", lambda: None)
    monkeypatch.setattr("sonoweave.translation.best_gpu", lambda: GPU(0, 10 * 1024 * MIB, 12 * 1024 * MIB, "test"))
    monkeypatch.delenv("SONOWEAVE_TRANSLATE_DEVICE", raising=False)
    return factory, gguf(tmp_path / "model.gguf")


def test_qwen_gpu_load_failure_reduces_offload_then_cpu(fake_llama):
    factory, path = fake_llama
    factory.side_effect = [RuntimeError("failed to allocate CUDA buffer"),
                           RuntimeError("failed to allocate CUDA buffer"), Mock()]
    translator = Translator(model_path=path)
    assert translator.device == "CPU"
    assert [c.kwargs["n_gpu_layers"] for c in factory.call_args_list] == [-1, 16, 0]
    translator.close()


def test_qwen_runtime_oom_discards_partial_stream_and_retries_on_cpu(fake_llama):
    factory, path = fake_llama

    def broken(**kwargs):
        yield {"choices": [{"text": "partial", "finish_reason": None}]}
        raise RuntimeError("CUDA out of memory")

    gpu, cpu = Mock(), Mock()
    gpu.create_completion.side_effect = broken
    cpu.create_completion.return_value = iter([{"choices": [{"text": "complete", "finish_reason": "stop"}]}])
    for model in (gpu, cpu):
        model.n_ctx.return_value = 2048
    factory.side_effect = [gpu, cpu]
    translator = Translator(model_path=path)
    assert translator._generate((1, 2, 3), 256, None) == "complete"
    assert translator.device == "CPU"
    gpu.close.assert_called_once()
    assert gpu.create_completion.call_args.kwargs["prompt"] == cpu.create_completion.call_args.kwargs["prompt"]
    assert factory.call_args.kwargs["n_gpu_layers"] == 0
    translator.close()


def test_qwen_explicit_cpu_never_queries_gpu_or_offloads(fake_llama, monkeypatch):
    factory, path = fake_llama
    monkeypatch.setenv("SONOWEAVE_TRANSLATE_DEVICE", "cpu")
    monkeypatch.setattr("sonoweave.translation.best_gpu", lambda: pytest.fail("CPU should not query GPU"))
    translator = Translator(model_path=path)
    assert factory.call_args.kwargs["n_gpu_layers"] == 0
    assert factory.call_args.kwargs["offload_kqv"] is False
    assert factory.call_args.kwargs["op_offload"] is False
    assert translator.device == "CPU"
    translator.close()
