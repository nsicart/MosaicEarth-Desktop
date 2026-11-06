# Install on Linux

Install the amd64 Debian package with `sudo apt install ./mosaicearth-desktop_0.1.4_amd64.deb`. Run `mosaicearth-desktop` from a terminal or launch it from the applications menu. Use the printed loopback URL if the browser does not open.

The Docker CLI must be able to use an existing Docker daemon. Provision the two engine images using the matching source release and the procedure in [Build](BUILD.md). If your machine uses an existing sudo configuration for Docker, you can run `ME_DOCKER='sudo -n docker' mosaicearth-desktop` in a terminal. A project can be created before the engine images are provisioned.

For local processing, create a project, import overlapping images and choose a processing engine. Use a separate project for each workflow to retain a readily accessible result. For a raster, select a georeferenced GeoTIFF and choose **Import and preview**. An active job interrupted by an application restart must be started again.

For optional local CUDA on a compatible Linux NVIDIA GPU, provision the CUDA image from [Build](BUILD.md) and launch with `mosaicearth-desktop --gaussian-device cuda`. The default launch uses CPU. CUDA requires the NVIDIA driver and Container Toolkit; the engine container runs with networking disabled. The optional MyEarth sync sends the project name after sign-in. It leaves images, models and offline processing on the machine.

Project data lives under `$XDG_DATA_HOME/mosaicearth-desktop`, or `~/.local/share/mosaicearth-desktop` by default. Back up that directory for portability. Stop the application before updating the package. `sudo apt remove mosaicearth-desktop` removes the application files while preserving the per-user workspace and Docker images.
