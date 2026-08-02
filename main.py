#!/usr/bin/env python3
"""PUTs Launcher entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_path() -> None:
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def main() -> int:
    _ensure_path()
    from launcher.ui.app import run_app

    run_app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
