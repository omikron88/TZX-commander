# SPDX-License-Identifier: GPL-3.0-or-later
"""BASIC listings and Z80 disassembly for ZX Spectrum program blocks."""

from __future__ import annotations

import re
from io import BytesIO
from struct import error as StructError

from tzxlib.convert import convertToBasic
from tzxlib.tapfile import TapHeader
from tzxlib.z80dis import disassemble

from .tape import TapeError

RELATIVE_ADDRESS_OVERFLOW = re.compile(r"\$(-[0-9A-F]+|[0-9A-F]{5,})")


def basic_listing(header: TapHeader, data: bytes) -> list[str]:
    program_end = header.param2()
    if program_end > len(data):
        raise TapeError("Hlavička BASICu uvádí začátek proměnných za koncem bloku.")
    if program_end == 0:
        return ["Program neobsahuje žádné řádky."]

    with BytesIO() as output:
        convertToBasic(data[:program_end], output)
        lines = output.getvalue().decode("utf-8").splitlines()
    if not lines:
        raise TapeError("Blok neobsahuje čitelné řádky BASICu.")
    return lines


def code_listing(header: TapHeader, data: bytes) -> list[str]:
    if not data:
        return ["Blok neobsahuje žádný kód."]

    lines = []
    position = 0
    origin = header.param1()
    while position < len(data):
        address = (origin + position) & 0xFFFF
        # Bound the decoder's lookahead even for malformed runs of index prefixes.
        window = data[position:position + 64]
        try:
            instruction, length = disassemble(window, 0, address)
        except (IndexError, StructError, UnboundLocalError):
            instruction, length = f"db ${window[0]:02X}", 1
        instruction = RELATIVE_ADDRESS_OVERFLOW.sub(
            lambda match: f"${int(match[1], 16) & 0xFFFF:04X}", instruction
        )
        encoded = window[:min(length, 6)].hex(" ").upper()
        if length > 6:
            encoded = encoded[:14] + " …"
        lines.append(f"{address:04X}  {encoded:<17}  {instruction}")
        position += length
    return lines
