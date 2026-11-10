# Install MosaicEarth Desktop from source

The source edition runs locally **without a MosaicEarth account, purchase or activation**. It uses the full interface and the same local processing engines as the official distribution. An account is needed only if you choose to connect to MosaicEarth cloud storage.

## Before you start

- Use an **x86-64/amd64** computer. The supported release targets are Linux and Windows; this guide does not claim macOS or ARM support.
- Install **Python 3.10 or later**, **Git**, **curl**, **OpenSSL** and **Docker**. Provisioning uses a POSIX shell: Linux directly, or an Ubuntu WSL 2 terminal on Windows.
- Keep Internet available during provisioning. Builds download upstream engines, verified UI resources and dependencies. Compiling OpenSplat can take a long time.
- Allow space for engines, build layers and projects. Official runtime setup needs at least 20 GB for CPU and another 50 GB for GPU; source builds also retain compiler and intermediate layers, so budget additional space.
- A GPU is optional. See [GPU requirements](GPU.md) for compatible NVIDIA cards, drivers and Docker GPU access. CPU operation does not need CUDA.

The host launcher uses Python's standard library. You do not need to install `platform/requirements.txt` on the host merely to run Desktop; application dependencies live inside Docker.

## Linux

### 1. Prepare your computer

Install Docker using its [official Linux instructions](https://docs.docker.com/engine/install/). On Debian/Ubuntu, install the remaining host tools:

```bash
sudo apt update
sudo apt install git python3 curl openssl
docker info
```

`docker info` must succeed as the user who will run provisioning and Desktop. Resolve Docker installation or user-access errors first; the provisioning scripts call `docker` directly.

### 2. Download the source

```bash
git clone https://github.com/nsicart/MosaicEarth-Desktop.git
cd MosaicEarth-Desktop
```

The default branch includes the latest documentation and maintenance changes. To use the exact source corresponding to a published installer, check out its tag, for example `git checkout v0.4.4` in a clean checkout. A detached HEAD at a release tag is normal.

### 3. Build the engines and interface

```bash
./scripts/provision-engines.sh
```

This builds NodeODX, the CPU runtime and the complete local interface. It downloads the versioned UI archive and CesiumJS, checks their integrity, and also builds optional GPU engines when compatible Docker GPU access is detected. No MosaicEarth account or cloud processing service is used.

For a CPU-only build, use this command instead:

```bash
ME_DESKTOP_CPU_ONLY=1 ./scripts/provision-engines.sh
```

To add GPU engines later, after configuring the driver and Docker GPU support:

```bash
./scripts/provision-cuda.sh
```

### 4. Start Desktop

```bash
python3 src/mosaicearth_desktop.py
```

The launcher opens the local setup page and then the full interface. Keep its terminal and Docker running. If the browser does not open, visit **http://127.0.0.1:19754** on the same computer.

To select a separate workspace directory:

```bash
python3 src/mosaicearth_desktop.py --data "$HOME/MosaicEarth-Projects"
```

Keep project data outside the source checkout.

## Windows

Build in **Ubuntu under WSL 2**, then launch with **native Windows Python**. Both use Docker Desktop's Linux engine.

1. Install [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/), select its WSL 2 backend and enable Ubuntu under **Settings → Resources → WSL Integration**. Follow [Docker's WSL guide](https://docs.docker.com/desktop/features/wsl/). Keep Docker Desktop running.
2. Install native Windows Python 3.10 or later. Confirm that `py -3 --version` works in PowerShell.
3. Open Ubuntu WSL, install the build tools and check Docker access:

```bash
sudo apt update
sudo apt install git python3 curl openssl
docker info
```

4. In Ubuntu, clone into a folder accessible to Windows. Replace `YOUR_WINDOWS_USER` with your Windows user-folder name:

```bash
cd /mnt/c/Users/YOUR_WINDOWS_USER
git clone https://github.com/nsicart/MosaicEarth-Desktop.git
cd MosaicEarth-Desktop
./scripts/provision-engines.sh
```

Cloning from Ubuntu keeps shell-script line endings suitable for the build. The Windows-folder location lets the native launcher use the same source, resources and manifests. Do not install a second Docker daemon inside Ubuntu; use Docker Desktop integration.

5. After the build succeeds, open **Windows PowerShell**:

```powershell
Set-Location "$env:USERPROFILE\MosaicEarth-Desktop"
py -3 src\mosaicearth_desktop.py
```

The interface opens in an Edge app window when Edge is available, otherwise in the default browser. Source use does not require activation. The ready-made `.exe` is a separate, account-associated distribution; see [Windows installer instructions](INSTALL_WINDOWS.md).

## Your first project

1. Open **MyEarth**, create a local project, then a survey/task as prompted.
2. Add overlapping photographs and select NodeODX photogrammetry or OpenSplat Gaussian reconstruction. You can also import existing supported geospatial products.
3. Start processing and monitor the task. NodeODX Complete provides orthophoto, DSM/DTM, point cloud and mesh; Fast focuses on orthophoto and tiles.
4. Open results, compose layers in **MyMosaics**, or explore with **Fly View**.
5. Connect a cloud account only if you want to synchronize. Local source use needs neither activation nor cloud sign-in.

## Your data and offline use

| Platform | Default workspace |
| --- | --- |
| Linux | `$XDG_DATA_HOME/mosaicearth-desktop`, or `~/.local/share/mosaicearth-desktop` |
| Native Windows launcher | `%LOCALAPPDATA%\mosaicearth-desktop` |

Back up the workspace before changing machines or installations. Closing a browser window does not cancel processing. Finish or cancel active tasks before stopping Docker. Removing the checkout does not remove a workspace stored elsewhere; removing Docker images can require rebuilding the engines.

Initial provisioning, online basemaps, the public catalog and synchronization require Internet. Once provisioned, the local workspace and bundled viewers do not require a cloud session; offline operation includes a coarse NOAA terrain fallback, but does not include a downloaded copy of worldwide imagery or street maps.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Cannot connect to Docker / permission denied | Start Docker and make `docker info` work in the same user environment. On Windows, check WSL Integration for the build and Docker access from PowerShell for the launcher. |
| Missing images / setup asks to install engines | Finish `scripts/provision-engines.sh`. The source edition builds its engines; official account-authorized downloads are not required. |
| Build interrupted / download failed | Re-run provisioning. Docker reuses completed layers; integrity failures must be resolved before launching. |
| GPU unavailable | Check [GPU.md](GPU.md), or build with `ME_DESKTOP_CPU_ONLY=1`. |
| Browser did not open | Visit `http://127.0.0.1:19754` locally and read the setup message. Keep the launcher running. |
| Default port in use | Use `--port 19854` and open the printed URL; the interface also needs the port three numbers higher. |
| Sign-in requested | Check whether you installed an official package or selected an optional cloud feature instead of running the source launcher locally. |

For help, open a [GitHub issue](https://github.com/nsicart/MosaicEarth-Desktop/issues) with your OS, revision and relevant error, without credentials or private data. [Build](BUILD.md) covers packages; [Contributing](CONTRIBUTING.md) explains the repository layout and development checks.
