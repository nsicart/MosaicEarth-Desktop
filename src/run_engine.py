"""Engine entry points executed in local Docker with networking disabled."""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path

from opensplat_engine import OpenSplatAdapter, prepare_colmap


def gaussian(work, steps):
    names = prepare_colmap(work, "colmap", work / "colmap.log", timeout=1800)
    OpenSplatAdapter("/opt/opensplat/bin/opensplat", cpu=True).train(
        work, work / "output", "FAST", names, timeout=7200, iterations=steps,
    )


def raster(work):
    source = next(work.glob("source.*"))
    warped = work / "preview.tif"
    preview = work / "preview.png"
    subprocess.run(["gdalwarp", "-t_srs", "EPSG:4326", "-of", "GTiff", "-dstalpha",
                    "-ts", "1024", "0", str(source), str(warped)], check=True)
    subprocess.run(["gdal_translate", "-of", "PNG", "-ot", "Byte", str(warped), str(preview)], check=True)
    info = json.loads(subprocess.check_output(["gdalinfo", "-json", str(warped)]))
    lower = info["cornerCoordinates"]["lowerLeft"]
    upper = info["cornerCoordinates"]["upperRight"]
    (work / "bounds.json").write_text(json.dumps([*lower, *upper]))
    warped.unlink()


def odx(work, token):
    import requests

    origin = "http://127.0.0.1:3000"
    http = requests.Session()
    http.trust_env = False
    http.params["token"] = token

    def request(method, path, **kwargs):
        for attempt in range(5):
            try:
                response = http.request(method, origin + path, timeout=120, **kwargs)
                response.raise_for_status()
                return response
            except requests.ConnectionError:
                if attempt == 4:
                    raise
                time.sleep(1)

    def api(method, path, **kwargs):
        data = request(method, path, **kwargs).json()
        if data.get("error") or data.get("success") is False:
            raise RuntimeError(f"NodeODX rejected {method} {path}")
        return data

    for attempt in range(60):
        try:
            api("GET", "/info")
            break
        except requests.ConnectionError:
            if attempt == 59:
                raise
    opts = [
        {"name": "fast-orthophoto", "value": True}, {"name": "max-concurrency", "value": 4},
        {"name": "orthophoto-resolution", "value": 5}, {"name": "dsm", "value": True},
        {"name": "dtm", "value": True},
    ]
    name = "Local photogrammetry"
    options = json.dumps(opts)
    uid = api("POST", "/task/new/init", files={"name": (None, name), "options": (None, options)})["uuid"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", uid):
        raise ValueError("Invalid NodeODX task ID")
    for image in sorted((work / "images").iterdir()):
        with image.open("rb") as stream:
            api("POST", f"/task/new/upload/{uid}", files={"images": (image.name, stream)})
    api("POST", f"/task/new/commit/{uid}", data={"name": name, "options": options})
    deadline = time.monotonic() + 4 * 3600
    while time.monotonic() < deadline:
        info = api("GET", f"/task/{uid}/info")
        code = info["status"]["code"]
        print(f"NodeODX status {code} · {info.get('progress', 0):.0f}%", flush=True)
        if code == 40:
            with request("GET", f"/task/{uid}/download/all.zip", stream=True) as response:
                with (work / "all.zip").open("wb") as dest:
                    size = 0
                    for block in response.iter_content(1024 * 1024):
                        size += len(block)
                        if size > 8 * 1024**3:
                            raise ValueError("NodeODX result exceeds 8 GB")
                        dest.write(block)
            return
        if code in (30, 50):
            print(request("GET", f"/task/{uid}/output").text[-6000:], flush=True)
            raise RuntimeError("NodeODX processing stopped")
        time.sleep(5)
    raise TimeoutError("NodeODX exceeded four hours")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("engine", choices=("gaussian", "odx", "raster"))
    parser.add_argument("--steps", type=int, default=2000)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    work = Path("/work")
    if args.engine == "gaussian":
        gaussian(work, args.steps)
    elif args.engine == "raster":
        raster(work)
    else:
        odx(work, args.token)
