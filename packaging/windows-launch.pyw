"""Windowed Windows entry point: keep background CLI processes windowless.

pythonw has no console for Docker's CLI children to inherit. Apply this policy
once, before importing the host, so all its background threads use it. GUI
programs such as Edge and Docker Desktop still open their normal windows.
The Linux/container runtime does not use this packaging entry point.
"""
import runpy
import subprocess
from pathlib import Path


def configure_background_processes():
    original = subprocess.Popen

    class BackgroundProcess(original):
        def __init__(self, *args, **kwargs):
            flags = kwargs.get('creationflags', 0)
            # Preserve an explicit request for a separate interactive console.
            if not flags & (subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS):
                kwargs['creationflags'] = flags | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = BackgroundProcess


if __name__ == '__main__':
    configure_background_processes()
    runpy.run_path(str(Path(__file__).resolve().parent / 'src/mosaicearth_desktop.py'),
                   run_name='__main__')
