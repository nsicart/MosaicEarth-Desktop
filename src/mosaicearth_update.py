"""Release idle official Windows launchers before replacing their runtime files."""
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path


def prepare_update(install_dir, processes, fetch, stop):
    root = str(install_dir).rstrip('\\/').replace('/', '\\').lower()
    executable = root + '\\python\\pythonw.exe'
    script = root + '\\src\\mosaicearth_desktop.py'
    owned = [p for p in processes if
             str(p.get('ExecutablePath') or '').lower() == executable and
             script in str(p.get('CommandLine') or '').lower()]
    # Check every instance before stopping any. Other Python apps, the legacy
    # Desktop and custom source builds are never candidates for termination.
    for process in owned:
        command = process['CommandLine']
        match = re.search(r'--port(?:=|\s+)(\d+)', command)
        port = int(match.group(1)) if match else 19754
        try:
            projects = fetch(port, '/api/projects')
            transfers = fetch(port, '/api/transfers')
            if not isinstance(projects, list) or not isinstance(transfers, dict):
                raise ValueError('Unknown application response')
            active = any(p.get('state') in {'running', 'queued'} for p in projects)
            active |= any(t.get('status') == 'running' for t in transfers.values())
        except (OSError, ValueError, TypeError):
            raise ValueError('Close the existing MosaicEarth Desktop launcher, then retry the installation. Its state could not be checked safely.') from None
        if active:
            raise ValueError('MosaicEarth is processing or transferring files. Finish or pause the operation, then retry the installation. Your work has not been stopped.')
    for process in owned:
        stop(int(process['ProcessId']))
    return len(owned)


def main():
    if sys.platform != 'win32' or len(sys.argv) != 2:
        raise ValueError('This update helper requires a Windows installation path.')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"],
        capture_output=True, timeout=45, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    processes = json.loads(result.stdout.decode('utf-8-sig').strip() or '[]')
    if isinstance(processes, dict):
        processes = [processes]

    def fetch(port, path):
        with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=5) as response:
            return json.load(response)

    def stop(pid):
        # Exact executable and script ownership were checked above. Docker jobs
        # run independently and remain intact; the new launcher checks their
        # state before upgrading the platform container.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100001, False, pid)
        if not handle:
            raise OSError('Cannot close the previous MosaicEarth launcher; close it and retry.')
        try:
            if not kernel.TerminateProcess(handle, 0):
                raise OSError('Cannot close the previous MosaicEarth launcher; close it and retry.')
            if kernel.WaitForSingleObject(handle, 10000) != 0:
                raise OSError('The previous MosaicEarth launcher is still closing. Retry the installation.')
        finally:
            kernel.CloseHandle(handle)

    print(f'Previous idle launchers closed: {prepare_update(Path(sys.argv[1]), processes, fetch, stop)}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc))
        sys.exit(1)
