# Contributing

Contributions to the interface, processing, connectors and documentation are welcome. Open an issue with your operating system, release version, expected behavior and a small reproducible example. Report vulnerabilities privately as described in [Security](SECURITY.md).

## Development rules

- Keep local processing independent of the MosaicEarth cloud backend. Cloud features must be explicitly initiated by the user and use public service endpoints.
- Use synthetic fixtures or published datasets with documented licenses. Never commit credentials, customer projects, local workspaces, databases, caches or generated installers.
- Keep processing containers isolated. Preserve third-party source and notices, and pin externally fetched application resources.
- Run `python3 -m unittest discover -s tests` with `platform/requirements.txt` installed in a development virtual environment. Verify the installed package before labeling a release.

## Repository map

| Location | What it contains |
| --- | --- |
| `src/` | Desktop launcher, workspace, activation, GPU selection and synchronization client. |
| `platform/` | Shared interface backend and manifests identifying source and UI resources. |
| `engines/` | Docker recipes, pinned processing dependencies and compatibility patches. |
| [Standalone Connectors](CONNECTORS.md) | Independent open-source repositories for GCP Studio, Android, iOS Bridge, QGIS, ArcGIS Pro, DJI Terra, PIX4D, Postshot, Blender, Metashape, CloudCompare, RealityCapture & WebODM. |
| `resources/` | Branding, local UI assets, public verification keys and engine manifests. |
| `scripts/` | Provisioning, resource verification, packaging and validation tools. |
| `packaging/` | Shortcuts, Windows installer recipe and public signature-verification material. |
| `tests/` | Regression tests using isolated fixtures. |
| `.github/workflows/` | Build automation. |
| `docs/images/` | Product screenshots, provenance and map credits. |
| `installers/` | Local build output; generated binaries are ignored by Git and published as release attachments. |

Start with [installation from source](INSTALL_FROM_SOURCE.md). See [Build](BUILD.md) for packaging and [third-party licenses](THIRD_PARTY_LICENSES.md) for dependencies and map sources.
