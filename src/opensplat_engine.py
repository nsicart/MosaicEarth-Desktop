"""Run a pinned OpenSplat CLI against prepared SfM data."""

import hashlib
import json
import math
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

        run("feature_extractor", "--database_path", database, "--image_path", images,
            "--SiftExtraction.use_gpu", "0", "--SiftExtraction.num_threads", "2",
            "--SiftExtraction.max_image_size", "1600")
        run("exhaustive_matcher", "--database_path", database, "--SiftMatching.use_gpu", "0",
            "--SiftMatching.num_threads", "2")
        run("mapper", "--database_path", database, "--image_path", images, "--output_path", str(sparse),
            "--Mapper.min_model_size", "4", "--Mapper.num_threads", "2")
        models = []
        for candidate in sorted(sparse.iterdir()):
            if (candidate / "images.bin").is_file():
                with (candidate / "images.bin").open("rb") as registered:
                    models.append((int.from_bytes(registered.read(8), "little"), candidate))
        if not models or max(models)[0] < 4:
            raise RuntimeError("COLMAP did not register at least four views in a single model.")
        _, best = max(models)
        canonical = sparse / "0"
        if best != canonical:
            if canonical.exists():
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
              timeout=1800, iterations=None, progress=None, cancelled=None, input_format="COLMAP"):
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
        (output / "benchmark.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return ply, cameras, metadata
