from pathlib import Path

from tools.build_bundle import cuda_binaries


def test_cuda_runtime_packaging_keeps_dependencies_and_excludes_display_driver(tmp_path):
    runtime = tmp_path / "CUDA with spaces"
    (runtime / "bin").mkdir(parents=True)
    for name in ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll", "cudart64_12.dll", "nvcuda.dll", "cudart32_65.dll"):
        (runtime / "bin" / name).touch()
    paths = cuda_binaries("win32", {"CUDA_PATH": str(runtime), "PATH": str(runtime / "bin")})
    names = [Path(source).name for source, destination in paths]
    assert len(names) == len(set(names))
    assert "cublas64_12.dll" in names and "cudnn64_9.dll" in names
    assert "nvcuda.dll" not in names
    assert "cudart32_65.dll" not in names
