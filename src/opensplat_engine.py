"""Run a pinned OpenSplat CLI against prepared SfM data."""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path


PRESETS = {
    "FAST": (2000, 2, 100_000, 2),
    "STANDARD": (6000, 1, 300_000, 3),
    "HIGH_QUALITY": (12000, 1, 700_000, 3),
}


def prepare_colmap(project: Path, binary: str, log_path: Path, *, timeout=600) -> list[str]:
    """Build one COLMAP model shared by the Desktop and dedicated Pod workers."""
    sparse = project / "sparse"
    sparse.mkdir()
    images = str(project / "images")
    database = str(project / "database.db")
    deadline = time.monotonic() + timeout

    with log_path.open("wb") as log:
        def run(*args):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("COLMAP reconstruction timed out")
            subprocess.run([binary, *args], stdout=log, stderr=subprocess.STDOUT,
                           timeout=remaining, check=True)

        single_cam_args = []
        total_images = 0
        try:
            from PIL import Image
            sizes = set()
            for img_file in sorted((project / "images").iterdir()):
                if img_file.is_file():
                    total_images += 1
                    with Image.open(img_file) as im_chk:
                        sizes.add(im_chk.size)
            if total_images > 0 and len(sizes) == 1:
                single_cam_args = [
                    "--ImageReader.single_camera", "1",
                    "--ImageReader.camera_model", "SIMPLE_RADIAL",
                    "--ImageReader.default_focal_length_factor", "0.85",
                ]
        except Exception:
            single_cam_args = []

        run("feature_extractor", "--database_path", database, "--image_path", images,
            "--SiftExtraction.use_gpu", "0", *single_cam_args)
        run("exhaustive_matcher", "--database_path", database, "--SiftMatching.use_gpu", "0")
        run("mapper", "--database_path", database, "--image_path", images, "--output_path", str(sparse),
            "--Mapper.min_model_size", "4", "--Mapper.init_min_tri_angle", "4")

        def _collect_models(sp_dir: Path):
            found = []
            if sp_dir.is_dir():
                for cand in sorted(sp_dir.iterdir()):
                    if (cand / "images.bin").is_file():
                        with (cand / "images.bin").open("rb") as reg_f:
                            found.append((int.from_bytes(reg_f.read(8), "little"), cand))
            return found

        models = _collect_models(sparse)
        best_count = max(models)[0] if models else 0
        focal_collapsed = False
        if models and single_cam_args:
            try:
                probe_txt = project / "_probe_txt"
                shutil.rmtree(probe_txt, ignore_errors=True)
                probe_txt.mkdir()
                run("model_converter", "--input_path", str(max(models)[1]), "--output_path", str(probe_txt),
                    "--output_type", "TXT")
                for ln in (probe_txt / "cameras.txt").read_text().splitlines():
                    if ln and not ln.startswith("#"):
                        parts = ln.split()
                        max_dim = max(float(parts[2]), float(parts[3]))
                        f_est = float(parts[4])
                        if f_est < 0.38 * max_dim or f_est > 2.4 * max_dim:
                            focal_collapsed = True
                        break
                shutil.rmtree(probe_txt, ignore_errors=True)
            except Exception:
                pass

        # Forward-motion EXIF-less video sequences suffer focal-length / Z-translation gauge collapse
        # under free bundle adjustment. Re-run mapper with fixed nominal intrinsics if <60% registered or focal drifted.
        if single_cam_args and (best_count < max(6, int(0.6 * total_images)) or focal_collapsed):
            sparse_fixed = project / "sparse_fixed"
            sparse_fixed.mkdir(exist_ok=True)
            try:
                run("mapper", "--database_path", database, "--image_path", images, "--output_path", str(sparse_fixed),
                    "--Mapper.min_model_size", "4", "--Mapper.init_min_tri_angle", "4",
                    "--Mapper.tri_min_angle", "1.0", "--Mapper.ba_refine_focal_length", "0",
                    "--Mapper.ba_refine_extra_params", "0")
                fixed_models = _collect_models(sparse_fixed)
                if fixed_models and (max(fixed_models)[0] > best_count or (focal_collapsed and max(fixed_models)[0] >= 4)):
                    models = fixed_models
            except Exception:
                pass

        if not models or max(models)[0] < 4:
            raise RuntimeError("COLMAP did not register at least four views in a single model.")
        _, best = max(models)
        canonical = sparse / "0"
        if best != canonical:
            if canonical.exists():
                shutil.rmtree(project / "previous_model", ignore_errors=True)
                canonical.rename(project / "previous_model")
            best.rename(canonical)
        text_model = project / "text_model"
        text_model.mkdir()
        run("model_converter", "--input_path", str(canonical), "--output_path", str(text_model),
            "--output_type", "TXT")
    lines = [line for line in (text_model / "images.txt").read_text().splitlines()
             if not line.startswith("#")]
    return [line.split(maxsplit=9)[9] for line in lines[::2]]


def prepare_opensfm(project: Path, destination: Path) -> list[str]:
    """Stage a single trusted ODX/OpenSfM reconstruction without changing its reference frame."""
    reconstruction = project / "opensfm" / "reconstruction.json"
    data = json.loads(reconstruction.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) != 1 or len(data[0].get("points", {})) < 4:
        raise ValueError("OpenSplat requires one connected OpenSfM reconstruction with sparse points")
    shots, cameras = data[0]["shots"], data[0]["cameras"]
    if len(shots) < 4:
        raise ValueError("OpenSplat requires at least four registered shots for training and validation")
    image_dir = (project / "images").resolve()
    sources = []
    for name, shot in sorted(shots.items()):
        if name != Path(name).name or any(ch in name for ch in "\r\n\\"):
            raise ValueError("Unsupported image name in SfM reconstruction")
        if cameras[shot["camera"]]["projection_type"] not in {"perspective", "brown"}:
            raise ValueError("OpenSplat does not support one of the OpenSfM camera projections")
        src = (image_dir / name).resolve(strict=True)
        if src.parent != image_dir or not src.is_file():
            raise ValueError("Registered image is outside the input image directory")
        sources.append(src)
    destination.mkdir()
    shutil.copyfile(reconstruction, destination / "reconstruction.json")
    (destination / "image_list.txt").write_text(
        "".join(f"{src.as_posix()}\n" for src in sources), encoding="utf-8"
    )
    return sorted(shots)


class OpenSplatAdapter:
    def __init__(self, binary: str, *, cpu=False):
        self.binary = str(Path(binary).resolve(strict=True))
        self.cpu = cpu

    def train(self, project: Path, output: Path, preset: str, registered_names: list[str], *,
              timeout=None, iterations=None, progress=None, cancelled=None, input_format="COLMAP"):
        if timeout is None:
            timeout = float(os.environ.get("MOSAICEARTH_OPENSPLAT_TIMEOUT_SECONDS", "10800"))
        if len(registered_names) < 4:
            raise ValueError("OpenSplat requires at least four registered views")
        output.mkdir(parents=True, exist_ok=False)
        version = subprocess.run([self.binary, "--version"], check=True, capture_output=True,
                                 text=True, timeout=90).stdout.strip()
        if not version:
            raise RuntimeError("OpenSplat did not report its version")
        steps, downscale, max_splats, sh = PRESETS[preset]
        steps = int(iterations) if iterations is not None else steps
        if steps < 1:
            raise ValueError("OpenSplat iterations must be positive")
        val_name = sorted(registered_names)[len(registered_names) // 2]
        ply = output / "model.ply"
        cameras_path = output / "cameras.json"
        argv = [
            self.binary, str(project.resolve()), "-o", str(ply.resolve()), "-n", str(steps),
            "--output-cameras", str(cameras_path.resolve()), "--val-image", val_name, "--val",
            "--downscale-factor", str(downscale), "--max-gaussians", str(max_splats),
            "--sh-degree", str(sh), "--refine-every", "100",
        ]
        if Path("/worker/quality/quality.pt").is_file():
            argv += ["--val-render", str((output / "validation").resolve())]
        if self.cpu:
            argv.append("--cpu")
        log = output / "opensplat.log"
        start = time.monotonic()
        with log.open("wb") as writer, log.open("r", encoding="utf-8", errors="replace") as reader:
            with subprocess.Popen(argv, stdout=writer, stderr=subprocess.STDOUT) as proc:
                last_step = 0
                try:
                    while proc.poll() is None:
                        if cancelled and cancelled():
                            raise RuntimeError("OpenSplat training canceled")
                        if time.monotonic() - start > timeout:
                            raise TimeoutError("OpenSplat training timed out")
                        for line in reader.readlines():
                            match = re.match(r"Step (\d+):", line)
                            if match and progress:
                                step = int(match[1])
                                if step > last_step:
                                    progress(step, steps)
                                    last_step = step
                        time.sleep(0.5)
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                if proc.returncode:
                    raise RuntimeError(f"OpenSplat exited with code {proc.returncode}. See {log.name}.")
        log_text = log.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"validation PSNR:\s*([^\s]+)", log_text)
        psnr = float(match[1]) if match else float("nan")
        if not math.isfinite(psnr):
            raise RuntimeError("OpenSplat did not report a finite validation PSNR")
        device = re.search(r"^Using (CUDA|MPS|CPU)\s*$", log_text, re.MULTILINE)
        cameras = json.loads(cameras_path.read_text(encoding="utf-8"))
        if sorted(c["img_name"] for c in cameras) != sorted(registered_names):
            raise RuntimeError("OpenSplat camera export differs from the registered SfM views")
        if any(len(c["position"]) != 3 or not all(math.isfinite(value) for value in c["position"]) for c in cameras):
            raise RuntimeError("OpenSplat exported an invalid camera position")
        with ply.open("rb") as fp:
            header = fp.read(8192).split(b"end_header", 1)
            count = re.search(rb"(?m)^element vertex ([0-9]+)\r?$", header[0])
            if (len(header) != 2 or not count or int(count[1]) < 1
                    or any(f" {name}".encode() not in header[0]
                           for name in ("f_dc_0", "opacity", "scale_0", "rot_0"))):
                raise RuntimeError("OpenSplat output is missing required Gaussian PLY attributes")
            fp.seek(0)
            digest = hashlib.sha256()
            for chunk in iter(lambda: fp.read(1024 * 1024), b""):
                digest.update(chunk)
            sha = digest.hexdigest()
        metadata = {
            "engine": "opensplat",
            "engine_version": version,
            "device": device[1] if device else "unknown",
            "sfm_engine": input_format,
            "preset_id": preset,
            "training_iterations": steps,
            "registered_cameras": len(cameras),
            "training_cameras": len(cameras) - 1 - log_text.count(" (no points in view)"),
            "validation_image": val_name,
            "psnr_val_db": psnr,
            "ssim_val": None,
            "lpips_val": None,
            "splat_count": int(count[1]),
            "ply_sha256": sha,
            "elapsed_seconds": round(time.monotonic() - start, 3),
            "effective_options": {"downscale_factor": downscale, "max_gaussians": max_splats,
                                  "sh_degree": sh, "refine_every": 100, "force_cpu": self.cpu},
            "coordinate_frame": "input_sfm",
        }
        try:
            import resource
            metadata["process_peak_rss_kib"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        except ImportError:
            metadata["process_peak_rss_kib"] = None
        for label, field in (("SSIM", "ssim_val"), ("LPIPS_squeeze_v01", "lpips_val")):
            metric = re.search(r"validation " + label + r":\s*([^\s]+)", log_text)
            if metric:
                value = float(metric[1])
                if not math.isfinite(value):
                    raise RuntimeError("OpenSplat reported a non-finite validation metric")
                metadata[field] = value
        if metadata["lpips_val"] is not None:
            metadata["lpips_method"] = "LPIPS v0.1, SqueezeNet 1.1, original held-out render, RGB in [-1,1]"
            metadata["ssim_method"] = "RGB mean, Gaussian 11x11 sigma=1.5, valid window, range=1"
            metadata["validation_images_sha256"] = {
                name: hashlib.sha256((output / "validation" / name).read_bytes()).hexdigest()
                for name in ("reference.png", "rendered.png")
            }
        (output / "benchmark.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return ply, cameras, metadata


def train_with_cpu_fallback(binary, project, output, preset, names, *, cpu=False, fallback="", on_fallback=None, **kwargs):
    """Retry a CUDA execution fault on CPU, preserving the failed GPU log."""
    try:
        return OpenSplatAdapter(binary, cpu=cpu).train(project, output, preset, names, **kwargs)
    except RuntimeError:
        if kwargs.get('cancelled') and kwargs['cancelled']():
            raise
        log_path = output / "opensplat.log"
        error_log = log_path.read_text(errors="replace") if log_path.is_file() else ""
        gpu_error = any(text in error_log.lower() for text in (
            "cuda error", "cuda out of memory", "cuda runtime", "cuda driver", "cublas_status",
            "cudnn_status", "no kernel image", "invalid device function", "cuda-capable device"))
        if cpu or not fallback or not Path(fallback).is_file() or not gpu_error:
            raise
        # This directory contains only this failed attempt's generated files.
        # The input photos and shared SfM reconstruction remain untouched.
        shutil.rmtree(output)
        if on_fallback:
            on_fallback()
        try:
            ply, cameras, benchmark = OpenSplatAdapter(fallback, cpu=True).train(project, output, preset, names, **kwargs)
        finally:
            output.mkdir(parents=True, exist_ok=True)
            (output / "opensplat-gpu-failed.log").write_text(error_log)
        benchmark["gpu_fallback_reason"] = "CUDA execution failed; completed using the CPU engine. See opensplat-gpu-failed.log."
        return ply, cameras, benchmark
