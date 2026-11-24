# SPDX-License-Identifier: GPL-2.0-only
"""Run one screen decompressor in the callback-driven Z80 emulator."""

import sys
from struct import pack

from z80 import Z80

BASIC_ADDRESS = 23755
SCREEN_START = 0x4000
SCREEN_END = 0x5B00
STACK_BOTTOM = 0xFF00
STACK_TOP = 0xFFFE
ROM_RET = 0x0052
RETURN_ADDRESS = 3
MAX_PROGRAM_SIZE = STACK_BOTTOM - BASIC_ADDRESS
MAX_INSTRUCTIONS = 250_000


def unpack_screen(program: bytes, entry_offset: int, string_end: int) -> bytes:
    if not 0 <= entry_offset < string_end <= len(program) <= MAX_PROGRAM_SIZE:
        raise ValueError("BASIC nebo vstupní adresa se nevejde do paměti Z80.")

    entry = BASIC_ADDRESS + entry_offset
    end = BASIC_ADDRESS + string_end
    memory = bytearray(65536)
    memory[BASIC_ADDRESS:BASIC_ADDRESS + len(program)] = program
    memory[:RETURN_ADDRESS] = b"\xcd" + pack("<H", entry)
    # The loader calls this RET to get its own address from the stack.
    memory[ROM_RET] = 0xC9
    written = bytearray(SCREEN_END - SCREEN_START)

    def write(address: int, value: int) -> None:
        if address < SCREEN_START:
            raise ValueError(f"Zápis na adresu ROM ${address:04X}.")
        memory[address] = value
        if SCREEN_START <= address < SCREEN_END:
            written[address - SCREEN_START] = 1

    def fetch(address: int) -> int:
        if address not in (0, ROM_RET) and not entry <= address < end:
            raise ValueError(f"Instrukce mimo dekompresor na adrese ${address:04X}.")
        return memory[address]

    def deny_io(*_args):
        raise ValueError("Dekompresor použil vstupní nebo výstupní port.")

    cpu = Z80(memory.__getitem__, write, fetch=fetch, input_port=deny_io, output_port=deny_io)
    cpu.r.sp = STACK_TOP
    for _ in range(MAX_INSTRUCTIONS):
        cpu.step()
        if cpu.r.pc == RETURN_ADDRESS:
            if cpu.r.sp != STACK_TOP or not all(written):
                raise ValueError("Dekompresor se nevrátil s celou obrazovkou.")
            return bytes(memory[SCREEN_START:SCREEN_END])
        if cpu.halted:
            raise ValueError("Dekompresor se zastavil před návratem.")
    raise ValueError("Dekompresor překročil limit instrukcí.")


def main() -> int:
    try:
        entry_offset, string_end = map(int, sys.argv[1:])
        program = sys.stdin.buffer.read(MAX_PROGRAM_SIZE + 1)
        sys.stdout.buffer.write(unpack_screen(program, entry_offset, string_end))
    except (ValueError, IndexError, KeyError) as error:
        sys.stderr.buffer.write(str(error).encode("utf-8"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
