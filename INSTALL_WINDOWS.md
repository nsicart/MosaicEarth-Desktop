# Install on Windows

Download `MosaicEarth-Desktop-0.2.1-x64-Setup.exe` from [Desktop](https://app.mosaicearth.app/en/Desktop), using the account that purchased Desktop or has an eligible Studio plan. Compare its SHA-256 with the published manifest. This is a native graphical NSIS wizard, with no manual `.bat` step. It is unsigned; Windows can display an unknown-publisher/reputation prompt.

Use Windows 10/11 x64. The wizard shows the license, activates the purchasing account for this computer, chooses an installation folder, creates shortcuts and provides an uninstaller. It never saves the password. Activation supports two active devices; manage them on the Desktop website. The signed grant allows 30 days offline; reactivate online after expiry. Copying that grant to a different device does not activate it.

Install Docker Desktop with WSL 2 and Linux containers. Launch MosaicEarth Desktop from Start, open **Installation and local engines**, and choose **Install local engines / resume**. The account-authorized download is about 1.3 GB, includes NodeODX and the CPU OpenSplat engine, and is checked against pinned SHA-256 and Docker image IDs. Allow at least 20 GB of free space, plus project data. Keep Docker running while processing. Reconnection resumes engine and project downloads.

The application and embedded Python are native Windows builds; the processing containers run in Docker's local Linux backend with networking disabled. This release validates the graphical installer, account activation, installed Pythonw server and uninstallation under Wine. Actual WSL 2/Docker Desktop processing on physical Windows hardware has not been independently validated; Linux local processing has. CUDA on Windows is not advertised as validated.

Project data lives in `%LOCALAPPDATA%\mosaicearth-desktop`. Uninstall from Installed apps; the workspace and downloaded Docker images are preserved. Back up the workspace before changing Windows profiles. The source edition can instead run directly from a Git checkout without account activation; see [Build](BUILD.md).
