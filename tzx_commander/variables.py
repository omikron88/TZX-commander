# SPDX-License-Identifier: GPL-3.0-or-later
"""Decode the ZX Spectrum BASIC variable area recorded after the program."""

from __future__ import annotations

from io import BytesIO
from itertools import product
from math import ldexp, prod
from typing import Iterator

from tzxlib.convert import convChar

STRING = 0x40
NUMBER = 0x60
NUMBER_ARRAY = 0x80
LONG_NUMBER = 0xA0
CHARACTER_ARRAY = 0xC0
FOR_COUNTER = 0xE0
END_MARKER = b"\x80"


def _read(stream: BytesIO, size: int) -> bytes:
    value = stream.read(size)
    if len(value) != size:
        raise ValueError("neúplný záznam")
    return value


def _word(stream: BytesIO) -> int:
    return int.from_bytes(_read(stream, 2), "little")


def format_number(data: bytes) -> str:
    if len(data) != 5:
        raise ValueError("neúplný zápis čísla")
    exponent, sign_byte = data[:2]
    if exponent == 0:
        if sign_byte not in (0, 0xFF) or data[4] != 0:
            raise ValueError("neplatný zápis celého čísla")
        integer = int.from_bytes(data[2:4], "little")
        return str(integer - 0x10000 if sign_byte == 0xFF else integer)

    # The sign bit replaces the leading one in the 32-bit binary mantissa.
    mantissa = int.from_bytes(data[1:], "big") | 0x80000000
    number = ldexp(mantissa, exponent - 128 - 32)
    return format(-number if sign_byte & 0x80 else number, ".10g")


def _number(stream: BytesIO) -> str:
    return format_number(_read(stream, 5))


def _string(data: bytes) -> str:
    characters = []
    for byte in data:
        if byte < 0x20:
            characters.append(f"\\x{byte:02X}")
        else:
            characters.append(convChar(byte).replace("\\", "\\\\").replace('"', '\\"'))
    return '"' + "".join(characters) + '"'


def _long_name(stream: BytesIO, first: str) -> str:
    characters = [first]
    while True:
        byte = _read(stream, 1)[0]
        char = chr(byte & 0x7F).lower()
        if not char.isalnum():
            raise ValueError("neplatný název proměnné")
        characters.append(char)
        if byte & 0x80:
            return "".join(characters)


def _subscript(indices: tuple[int, ...]) -> str:
    return "(" + ",".join(map(str, indices)) + ")" if indices else ""


def _array_rows(stream: BytesIO, name: str, is_string: bool) -> Iterator[str]:
    record_length = _word(stream)
    array = BytesIO(_read(stream, record_length))
    dimension_count = _read(array, 1)[0]
    dimensions = tuple(_word(array) for _ in range(dimension_count))
    element_size = 1 if is_string else 5
    if not dimensions or 0 in dimensions or array.tell() + prod(dimensions) * element_size != record_length:
        raise ValueError("rozměry pole neodpovídají délce dat")

    yield f"DIM {name}{_subscript(dimensions)}"
    index_dimensions = dimensions[:-1] if is_string else dimensions
    for indices in product(*(range(1, size + 1) for size in index_dimensions)):
        value = _string(_read(array, dimensions[-1])) if is_string else _number(array)
        yield f"  {name}{_subscript(indices)} = {value}"


def _variable_rows(stream: BytesIO, tag: int) -> Iterator[str]:
    kind, letter = tag & 0xE0, tag & 0x1F
    if not 1 <= letter <= 26:
        raise ValueError("neplatný název proměnné")
    name = chr(ord("a") + letter - 1)

    if kind == STRING:
        yield f"{name}$ = {_string(_read(stream, _word(stream)))}"
    elif kind == NUMBER:
        yield f"{name} = {_number(stream)}"
    elif kind == NUMBER_ARRAY:
        yield from _array_rows(stream, name, is_string=False)
    elif kind == LONG_NUMBER:
        yield f"{_long_name(stream, name)} = {_number(stream)}"
    elif kind == CHARACTER_ARRAY:
        yield from _array_rows(stream, name + "$", is_string=True)
    elif kind == FOR_COUNTER:
        value, limit, step = (_number(stream) for _ in range(3))
        line = _word(stream)
        statement = _read(stream, 1)[0]
        yield f"{name} = {value}  (FOR, mez {limit}, krok {step}, řádek {line}, příkaz {statement})"
    else:
        raise ValueError(f"neznámý typ ${kind:02X}")


def variable_listing(data: bytes) -> list[str]:
    lines = []
    with BytesIO(data) as stream:
        while tag := stream.read(1):
            offset = stream.tell() - 1
            try:
                if tag == END_MARKER:
                    if offset + 1 != len(data):
                        raise ValueError("data za koncovou značkou")
                    break
                lines.extend(_variable_rows(stream, tag[0]))
            except ValueError as error:
                lines.append(f"Poškozená oblast proměnných na pozici ${offset:04X} ({error}).")
                break
    return lines or ["Žádné uložené proměnné."]
