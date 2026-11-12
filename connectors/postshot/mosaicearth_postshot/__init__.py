"""MosaicEarth Open-Source Jawset Postshot & LichtFeld Studio 3DGS Connector (0.4.0)."""
from .connector import (
    copy_text_to_clipboard,
    extract_georef_from_companion_inputs,
    fetch_share_and_oembed_links,
    launch_gui,
    main,
    pull_cloud_splats,
    stage_postshot_splat,
    watch_postshot_exports,
)

__version__ = "0.4.0"
__all__ = [
    "extract_georef_from_companion_inputs",
    "stage_postshot_splat",
    "fetch_share_and_oembed_links",
    "copy_text_to_clipboard",
    "watch_postshot_exports",
    "pull_cloud_splats",
    "launch_gui",
    "main",
]
