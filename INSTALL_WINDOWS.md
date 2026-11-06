# Install on Windows

Run the x64 installer, then open MosaicEarth Desktop from the Start menu. Compare the SHA-256 value with the manifest before running the installer. The build recipe produces an unsigned installer. Windows may display a reputation warning for unsigned downloads.

Use Docker Desktop in Linux-container mode with its WSL2 backend. From a Git checkout in a WSL2 distribution with Docker integration enabled, provision both engines with `./scripts/provision-engines.sh`. Check Docker access from Windows using `docker image inspect mosaicearth-desktop/local-engine:0.1.0 mosaicearth-desktop/nodeodx:0.1.0`.

The application and embedded Python runtime are Windows builds. The processing engines run in the local Linux backend. CPU is the configured OpenSplat backend. NVIDIA CUDA on Windows requires separate runtime validation.

The installer uninstalls from **Installed apps** and preserves data in `%LOCALAPPDATA%\mosaicearth-desktop`. Export or back up that directory before changing user profiles. Collect the local job log and source revision when reporting issues.
