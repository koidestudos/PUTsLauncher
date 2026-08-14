from __future__ import annotations

import os
import subprocess
import sys

# Floor for the RAM slider — Minecraft with Forge needs at least this much.
MIN_RAM_GB = 2


def total_ram_gb() -> int:
    """Physical RAM of this machine in GB (0 when it cannot be determined)."""
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return 0
            return int(status.ullTotalPhys // (1024**3))

        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
            return int(int(out.stdout.strip()) // (1024**3))

        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int((pages * page_size) // (1024**3))
    except Exception:
        return 0


def max_ram_gb() -> int:
    """
    Upper bound for the RAM slider: the machine's own memory. Asking the JVM for
    more than the computer has just makes Minecraft fail to start.
    """
    total = total_ram_gb()
    if total <= 0:
        return 8  # unknown machine — keep the old conservative ceiling
    return max(MIN_RAM_GB, total)


def clamp_ram_gb(value: int) -> int:
    """Keep a configured amount inside [MIN_RAM_GB, machine total]."""
    try:
        wanted = int(value)
    except (TypeError, ValueError):
        wanted = 4
    return max(MIN_RAM_GB, min(wanted, max_ram_gb()))
