# SPDX-License-Identifier: GPL-3.0-or-later
"""BASIC listings and Z80 disassembly for ZX Spectrum program blocks."""

from __future__ import annotations

import re
from struct import error as StructError, unpack_from

from prompt_toolkit.formatted_text import FormattedText
from tzxlib.convert import convChar
from tzxlib.tapfile import TapHeader
from tzxlib.z80dis import disassemble

from .tape import TapeError
from .variables import format_number, variable_listing

RELATIVE_ADDRESS_OVERFLOW = re.compile(r"\$(-[0-9A-F]+|[0-9A-F]{5,})")


ListingLine = str | FormattedText
NUMBER_MARKER = 0x0E
NUMBER_STYLE = "class:basic-number"


def _number_annotation(data: bytes) -> str:
    encoded = data.hex(" ").upper()
    try:
        value = format_number(data)
    except ValueError as error:
        value = str(error)
    return f" [{encoded} → {value}]" if encoded else f" [{value}]"


def _basic_line(line_number: int, data: bytes) -> ListingLine:
    parts = []
    source = [f"{line_number:4d}"]
    quoted = False
    remark = False
    last_significant = 0
    space_before = False
    position = 0
    while position < len(data):
        byte = data[position]
        position += 1
        if byte == NUMBER_MARKER and not (quoted or remark):
            number = data[position:position + 5]
            position += len(number)
            # DEF FN also stores five bytes behind each formal parameter.
            if last_significant in b"0123456789.":
                parts.append(("", "".join(source)))
                parts.append((NUMBER_STYLE, _number_annotation(number)))
                source.clear()
            last_significant = 0
            continue
        if 0x10 <= byte <= 0x17:
            position += 1 if byte <= 0x15 else 2
            continue
        if not remark:
            if byte == 0x22:
                quoted = not quoted
            elif byte == 0xEA and not quoted:
                remark = True
        text = convChar(byte, space_before)
        source.append(text)
        space_before = text.endswith(" ")
        if byte > 0x20:
            last_significant = byte
    if parts:
        if source:
            parts.append(("", "".join(source)))
        return FormattedText(parts)
    return "".join(source)


def basic_listing(header: TapHeader, data: bytes) -> list[ListingLine]:
    program_end = header.param2()
    if program_end > len(data):
        raise TapeError("Hlavička BASICu uvádí začátek proměnných za koncem bloku.")

    lines: list[ListingLine] = []
    position = 0
    while position + 4 < program_end:
        line_number = unpack_from(">H", data, position)[0]
        line_length = unpack_from("<H", data, position + 2)[0]
        position += 4
        if position + line_length > program_end:
            break
        body = data[position:position + line_length].removesuffix(b"\x0D")
        lines.append(_basic_line(line_number, body))
        position += line_length

    if not lines:
        if program_end:
            raise TapeError("Blok neobsahuje čitelné řádky BASICu.")
        lines.append("Program neobsahuje žádné řádky.")

    return [*lines, "", "Uložené proměnné", *variable_listing(data[program_end:])]


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
