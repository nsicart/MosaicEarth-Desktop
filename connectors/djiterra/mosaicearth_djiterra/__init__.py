"""MosaicEarth Open-Source DJI Terra Connector (0.4.0)."""
from .connector import (
    inspect_djiterra_project,
    launch_gui,
    main,
    pull_cloud_for_djiterra,
    stage_djiterra_folder,
    watch_djiterra_directory,
)

__version__ = "0.4.0"
__all__ = [
    "inspect_djiterra_project",
    "stage_djiterra_folder",
    "watch_djiterra_directory",
    "pull_cloud_for_djiterra",
    "launch_gui",
    "main",
]
