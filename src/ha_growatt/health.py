"""Passive liveness checks; never open an inverter or cloud connection."""

import json
import os
import time
from pathlib import Path


def _process_exists(pid: int) -> bool:
    if os.name != "nt":
        os.kill(pid, 0)
        return True
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def write_health(path: Path, mode: str) -> None:
    snapshot = {"pid": os.getpid(), "updated": time.time(), "mode": mode}
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(snapshot), encoding="utf-8")
    temporary.replace(path)


def healthy(path: Path, maximum_age: float = 30) -> bool:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if type(state["pid"]) is not int or state["pid"] <= 0:
            return False
        if not 0 <= time.time() - state["updated"] <= maximum_age:
            return False
        return _process_exists(state["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def clear_health(path: Path) -> None:
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass
