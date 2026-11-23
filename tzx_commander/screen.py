# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare ZX Spectrum screen memory and launch the standalone SCR viewer."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Sequence

from tzxlib.tzxblocks import TzxbBlock

from .tape import TapeError, matches_program_data, program_taps

SCREEN_ADDRESS = 0x4000
BITMAP_SIZE = 6144
SCREEN_SIZE = BITMAP_SIZE + 768


def screen_memory(blocks: Sequence[TzxbBlock], index: int) -> bytes | None:
    """Return an SCR image from an addressed load or a screen-sized payload."""
    header, data = program_taps(blocks, index)
    if data is None:
        return None
    if header is None:
        return data.body() if len(data.data) == SCREEN_SIZE + 2 else None
    if header.typeId() != 3 or not matches_program_data(header, data):
        return None

    offset = header.param1() - SCREEN_ADDRESS
    first = max(0, offset)
    last = min(SCREEN_SIZE, offset + header.length())
    if first >= last:
        return data.body() if header.length() == SCREEN_SIZE else None

    image = bytearray(BITMAP_SIZE) + bytearray(b"\x38" * (SCREEN_SIZE - BITMAP_SIZE))
    image[first:last] = data.body()[first - offset:last - offset]
    return bytes(image)


def viewer_script() -> Path:
    root = Path(__file__).resolve().parents[1]
    source = root / "tools" / "scr.py"
    if (root / "pyproject.toml").is_file() and source.is_file():
        return source

    try:
        package = distribution("tzx-commander")
    except PackageNotFoundError:
        package = None
    if package is not None:
        for path in package.files or ():
            if path.parts[-4:] == ("share", "tzx-commander", "tools", "scr.py"):
                script = Path(package.locate_file(path)).resolve()
                if script.is_file():
                    return script

    raise TapeError("Chybí prohlížeč SCR. Přeinstalujte TZX Commander.")


def launch_viewer(screen: bytes) -> None:
    script = viewer_script()
    with TemporaryDirectory(prefix="tzx-commander-") as directory:
        path = Path(directory) / "preview.scr"
        path.write_bytes(screen)
        process = subprocess.run(
            [sys.executable, str(script), str(path)], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace",
            check=False,
        )
    if process.returncode:
        lines = process.stderr.strip().splitlines()
        detail = lines[-1] if lines else f"kód {process.returncode}"
        raise TapeError(f"Prohlížeč SCR nelze spustit. {detail}")
