"""Release idle official Windows launchers before replacing their runtime files."""
import json
import socket
import struct
import subprocess
import sys
import urllib.request
from pathlib import Path


def prepare_update(install_dir, processes, fetch, stop):
    root = str(install_dir).rstrip('\\/').replace('/', '\\').lower()
    executable = root + '\\python\\pythonw.exe'
    owned = [p for p in processes if
             str(p.get('ExecutablePath') or '').lower() == executable]
    # Check every instance before stopping any. Other Python apps, the legacy
    # Desktop and custom source builds are never candidates for termination.
    for process in owned:
        ports = process.get('Ports', [])
        if len(ports) != 1:
            raise ValueError('Close the existing MosaicEarth Desktop launcher, then retry. Its local service is not ready.')
        port = ports[0]
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


def windows_processes():
    """Use native process and TCP ownership APIs; WMI can stall on Windows 11."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.K32EnumProcesses.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.K32EnumProcesses.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    count = 4096
    while True:
        ids = (wintypes.DWORD * count)()
        used = wintypes.DWORD()
        if not kernel.K32EnumProcesses(ids, ctypes.sizeof(ids), ctypes.byref(used)):
            raise OSError('Windows could not check the running applications. Close MosaicEarth and retry.')
        if used.value < ctypes.sizeof(ids):
            break
        count *= 2
    tcp = ctypes.WinDLL('iphlpapi').GetExtendedTcpTable
    tcp.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL, wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    tcp.restype = wintypes.DWORD
    size = wintypes.DWORD()
    tcp(None, ctypes.byref(size), False, socket.AF_INET, 3, 0)
    for _ in range(3):
        table = ctypes.create_string_buffer(size.value)
        result = tcp(table, ctypes.byref(size), False, socket.AF_INET, 3, 0)
        if result != 122:
            break
    if result:
        raise OSError('Windows could not check the local workspace. Close MosaicEarth and retry.')
    ports = {}
    for state, addr, port, remote, remote_port, pid in struct.iter_unpack('<6I', table.raw[4:4 + 24 * struct.unpack_from('<I', table.raw)[0]]):
        if addr == 0x0100007f:
            ports.setdefault(pid, []).append(socket.ntohs(port & 0xffff))
    processes = []
    for pid in ids[:used.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            continue
        try:
            name = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(name))
            if kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
                processes.append({'ProcessId': pid, 'ExecutablePath': name.value, 'Ports': ports.get(pid, [])})
        finally:
            kernel.CloseHandle(handle)
    return processes


def main():
    if sys.platform != 'win32' or len(sys.argv) != 2:
        raise ValueError('This update helper requires a Windows installation path.')
    processes = windows_processes()

    def fetch(port, path):
        with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=5) as response:
            return json.load(response)

    def stop(pid):
        # The dedicated installation executable and TCP ownership were checked above. Docker jobs
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
        handle = kernel.OpenProcess(0x00101001, False, pid)
        if not handle:
            raise OSError('Cannot close the previous MosaicEarth launcher; close it and retry.')
        try:
            kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
            name = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(name))
            expected = str(Path(sys.argv[1]) / 'python' / 'pythonw.exe').lower()
            if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)) or name.value.lower() != expected:
                raise OSError('The previous application changed. Retry the installation.')
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
