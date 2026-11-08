# Local GPU processing

Desktop 0.4 uses the same shared web interface with local CPU or NVIDIA CUDA engines. The host launcher tests a CUDA computation inside Docker; a graphics card name alone is not enough. In Automatic mode it selects the compatible NVIDIA device with the most memory. Processing settings also allow CPU only. Settings cannot change while tasks or transfers are active.

The current CUDA build targets NVIDIA compute capability 6.0–8.9 (Pascal, Volta, Turing, Ampere and Ada), with at least 2 GiB device memory. Larger datasets need more memory. AMD, Intel, older NVIDIA and newer unsupported CUDA architectures use CPU. OpenSplat uses CUDA 11.8; the pinned NodeODX GPU image uses CUDA 12.9. A driver sufficient for OpenSplat but not NodeODX selects GPU for OpenSplat and CPU for NodeODX. COLMAP feature extraction and matching remain on CPU in this release; GPU acceleration applies to OpenSplat training and the GPU-supported stages of ODX, not every processing stage.

On Windows, Docker Desktop must use WSL 2 and a compatible NVIDIA Windows driver. On Linux, Docker needs the NVIDIA driver and NVIDIA Container Toolkit configured. These are host prerequisites; Desktop does not silently install or replace drivers. If GPU passthrough fails, CPU processing remains available. See [Docker's Windows GPU instructions](https://docs.docker.com/desktop/features/gpu/) and [NVIDIA Container Toolkit installation](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

The optional GPU download is approximately 9.1 GB; allow an additional 50 GB free during installation, plus working space for projects. The installer checks available space before downloading.

Official installations first provision the verified CPU engines and then the optional GPU bundle if the runtime check succeeds. Downloads resume, SHA-256 and Docker image IDs are checked, and insufficient disk space or an unavailable GPU bundle leaves the CPU engines usable. Open Processing settings → Install local engines to retry. Automatic mode checks the hardware again on workspace startup. No cloud GPU is provisioned or charged.

Source builds need no account. `scripts/provision-engines.sh` builds the CPU engines and, when compatible Docker GPU access is detected, also invokes `scripts/provision-cuda.sh`. `ME_DESKTOP_CPU_ONLY=1` builds CPU engines only. To add GPU support later, run `scripts/provision-cuda.sh` and reopen Desktop. CUDA development tools remain in build stages; the distributed OpenSplat runtime contains the required runtime libraries and both GPU and CPU executables.

If a GPU container fails to start, the launcher attempts its CPU engine without deleting workspace data. If OpenSplat encounters a CUDA execution or memory error, it preserves the GPU log, reuses the real SfM reconstruction, and retries training once on CPU. Invalid input, explicit cancellation and unrelated processing errors are reported normally. The completed model records its actual device and any GPU fallback. CPU processing can take considerably longer.

The menu reports the configured engine devices. A per-task fallback is recorded in that task's output metadata and logs. Closing the UI does not stop reconstruction. Updates and device changes preserve photos, results, cloud associations and account activation.

Validation of this candidate release is still in progress. Hardware-specific results will be recorded in the release notes after complete reconstruction and installer tests on both operating systems.
