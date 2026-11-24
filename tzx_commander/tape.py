# SPDX-License-Identifier: GPL-3.0-or-later
"""Image framing, block operations and file writes for the commander."""

from __future__ import annotations

import errno
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from struct import error as StructError, unpack
from typing import BinaryIO, Callable, Iterator, Sequence

try:
    import fcntl
except ImportError:
    fcntl = None

from tzxlib.tapfile import TapFile, TapHeader
from tzxlib.tzxblocks import TzxbBlock, TzxbData
from tzxlib.tzxfile import TzxFile

FORMATS = {".tap", ".tzx"}
TZX_MAGIC = b"ZXTape!\x1a"
STRUCTURAL = set(range(0x21, 0x29)) | {0x5A}
TAP_METADATA = {0x30, 0x32, 0x33, 0x34, 0x35}
NO_LINK = {errno.EPERM, errno.EXDEV, errno.ENOSYS, errno.EOPNOTSUPP}


class TapeError(Exception):
    pass


def tape_format(path: Path) -> str:
    extension = path.suffix.lower()
    if extension not in FORMATS:
        raise TapeError("Název obrazu musí končit .tap nebo .tzx.")
    return extension


def file_stamp(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class BlockReader:
    """Read block fields exactly, including fields read by tzxlib."""

    def __init__(self, stream: BinaryIO, size: int):
        self.stream = stream
        self.remaining = size

    def read(self, count: int) -> bytes:
        if count < 0 or count > self.remaining:
            raise TapeError("Neúplný blok nebo hlavička.")
        data = self.stream.read(count)
        if len(data) != count:
            raise TapeError("Neúplný blok nebo hlavička.")
        self.remaining -= count
        return data


@dataclass
class Tape:
    path: Path
    blocks: list[TzxbBlock]
    version: tuple[int, int]
    extension: str
    stamp: tuple[int, ...]

    @classmethod
    def open(cls, path: Path) -> Tape:
        extension = tape_format(path)
        path = path.resolve(strict=True)
        if not path.is_file():
            raise TapeError("Očekáván soubor obrazu TAP/TZX.")
        with path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            reader = BlockReader(stream, info.st_size)
            version = (TzxFile.MAJOR, TzxFile.MINOR)
            if extension == ".tzx":
                header = reader.read(10)
                if not header.startswith(TZX_MAGIC) or header[8] != TzxFile.MAJOR:
                    raise TapeError("Neplatná hlavička nebo nepodporovaná verze TZX.")
                version = (header[8], header[9])
            blocks = []
            while reader.remaining:
                try:
                    if extension == ".tap":
                        size = unpack("<H", reader.read(2))[0]
                        if size < 2:
                            raise TapeError("Bloku TAP chybí příznak nebo kontrolní součet.")
                        block = TzxbData()
                        block.setup(TapFile.create(reader.read(size)))
                    else:
                        block = TzxbBlock.createBlock(reader.read(1)[0])
                        block.read(reader)
                    blocks.append(block)
                except (TapeError, OSError, ValueError, IndexError, StructError) as error:
                    raise TapeError(f"Blok {len(blocks) + 1:03}: {error}") from error
            stamp = file_stamp(info)
            if file_stamp(os.fstat(stream.fileno())) != stamp:
                raise TapeError("Obraz se při čtení změnil. Otevřete jej znovu.")
        return cls(path, blocks, version, extension, stamp)

    def selected(self, indices: Sequence[int]) -> list[TzxbBlock]:
        return [self.blocks[index] for index in indices]

    def append(self, blocks: Sequence[TzxbBlock], version: tuple[int, int]) -> None:
        require_independent(self.blocks)
        require_independent(blocks)
        save_image(self.path, [*self.blocks, *blocks], max(self.version, version),
                   self.stamp, extension=self.extension)

    def without(self, indices: Sequence[int]) -> None:
        require_independent(self.blocks)
        removed = set(indices)
        remaining = [block for index, block in enumerate(self.blocks) if index not in removed]
        save_image(self.path, remaining, self.version, self.stamp, extension=self.extension)


def require_independent(blocks: Sequence[TzxbBlock]) -> None:
    if any(block.id in STRUCTURAL for block in blocks):
        raise TapeError("Tato úprava by mohla porušit skupiny, skoky či jiné vazby bloků TZX.")


def tap_losses(blocks: Sequence[TzxbBlock]) -> list[str]:
    metadata = 0
    pauses = 0
    for index, block in enumerate(blocks, 1):
        if block.id in TAP_METADATA:
            metadata += 1
        elif block.id != 0x10 or len(block.tap.data) < 2:
            raise TapeError(f"Blok {index} (ID {block.id:02X}) nelze bezpečně uložit do TAP.")
        elif block.data[:2] != b"\xe8\x03":
            pauses += 1
    losses = []
    if metadata:
        losses.append(f"vynechá metadata ({metadata})")
    if pauses:
        losses.append(f"nahradí pauzy výchozí délkou ({pauses})")
    return losses


def unchanged(path: Path, expected: tuple[int, ...]) -> None:
    if file_stamp(path.lstat()) != expected:
        raise TapeError(f"Soubor {path.name} se mezitím změnil. Obnovte panel (Ctrl+R).")


@contextmanager
def checked_update(path: Path, expected: tuple[int, ...]) -> Iterator[None]:
    # On POSIX, serialize updates made by other instances to this inode.
    if fcntl is None or stat.S_ISLNK(path.lstat().st_mode):
        unchanged(path, expected)
        yield
        return
    with path.open("rb") as current:
        fcntl.flock(current.fileno(), fcntl.LOCK_EX)
        unchanged(path, expected)
        yield


def remove_image(path: Path, expected: tuple[int, ...]) -> None:
    with checked_update(path, expected):
        path.unlink()


def write_file(
    path: Path,
    writer: Callable[[BinaryIO], object],
    expected: tuple[int, ...] | None = None,
    mode: int | None = None,
) -> None:
    """Stage writes beside the destination and protect any existing image."""
    if expected is not None:
        unchanged(path, expected)
        mode = stat.S_IMODE(path.stat().st_mode)

    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tzxc-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            if mode is not None:
                temporary.chmod(mode)
            if expected is not None:
                with checked_update(path, expected):
                    os.replace(temporary, path)
                return
            try:
                os.link(temporary, path)
            except OSError as error:
                if error.errno not in NO_LINK:
                    raise
                # Filesystems such as FAT do not support hard links.
                with temporary.open("rb") as source:
                    destination = path.open("xb")
                    try:
                        with destination:
                            shutil.copyfileobj(source, destination)
                            destination.flush()
                            os.fsync(destination.fileno())
                        if mode is not None:
                            path.chmod(mode)
                    except BaseException:
                        path.unlink(missing_ok=True)
                        raise
        finally:
            stream.close()
            temporary.unlink(missing_ok=True)


def save_image(
    path: Path,
    blocks: Sequence[TzxbBlock],
    version: tuple[int, int] = (TzxFile.MAJOR, TzxFile.MINOR),
    expected: tuple[int, ...] | None = None,
    *,
    extension: str | None = None,
) -> None:
    extension = extension or tape_format(path)
    if extension == ".tap":
        tap_losses(blocks)

    def write(stream: BinaryIO) -> None:
        if extension == ".tzx":
            stream.write(TZX_MAGIC + bytes(version))
            for block in blocks:
                block.write(stream)
        else:
            for block in blocks:
                if block.id == 0x10:
                    block.writeTap(stream)

    write_file(path, write, expected)


def transfer_image(source: Path, target: Path, *, move: bool = False) -> None:
    if tape_format(source) != tape_format(target):
        raise TapeError("Pro změnu formátu použijte převod klávesou F9.")
    original = file_stamp(source.lstat())
    if move and source.is_symlink():
        # A moved relative symlink must still point at the same image.
        target.symlink_to(source.resolve(strict=True))
    else:
        with source.open("rb") as stream:
            info = os.fstat(stream.fileno())

            def copy(destination: BinaryIO) -> None:
                shutil.copyfileobj(stream, destination)
                if file_stamp(os.fstat(stream.fileno())) != file_stamp(info):
                    raise TapeError("Zdroj se při kopírování změnil.")

            write_file(target, copy, mode=stat.S_IMODE(info.st_mode))
    if move:
        try:
            remove_image(source, original)
        except (OSError, TapeError) as error:
            raise TapeError(f"Cíl vznikl, zdroj zůstal na místě. {error}") from error


def block_text(block: TzxbBlock) -> str:
    try:
        return str(block).strip() or block.info() or ""
    except (ValueError, IndexError, TypeError, StructError):
        return ""


def _nearby_tap(blocks: Sequence[TzxbBlock], index: int, direction: int) -> TapFile | None:
    """Look past annotations and pauses that keep playback going."""
    stop = len(blocks) if direction > 0 else -1
    for position in range(index + direction, stop, direction):
        block = blocks[position]
        if block.id in TAP_METADATA or (block.id == 0x20 and block.length() > 0):
            continue
        return getattr(block, "tap", None)
    return None


def program_taps(blocks: Sequence[TzxbBlock], index: int) -> tuple[TapHeader | None, TapFile | None]:
    """Return the possible header and data associated with the selected block."""
    tap = getattr(blocks[index], "tap", None)
    if isinstance(tap, TapHeader):
        return tap, _nearby_tap(blocks, index, 1)
    if tap is None:
        return None, None
    header = _nearby_tap(blocks, index, -1)
    return header if isinstance(header, TapHeader) else None, tap


def matches_program_data(header: TapFile | None, data: TapFile | None) -> bool:
    return (
        isinstance(header, TapHeader) and data is not None
        and data.data[:1] == b"\xff" and len(data.data) == header.length() + 2
    )


def unpaired_flag(blocks: Sequence[TzxbBlock], index: int) -> int | None:
    """Return the stored flag byte when the block has no matching partner."""
    tap = getattr(blocks[index], "tap", None)
    if tap is None or not tap.data:
        return None
    header, data = program_taps(blocks, index)
    return None if matches_program_data(header, data) else tap.data[0]


def program_indices(blocks: Sequence[TzxbBlock], index: int) -> list[int]:
    """Select an adjacent ZX Spectrum header and its matching data block."""
    block = blocks[index]
    tap = getattr(block, "tap", None)
    start = index if isinstance(tap, TapHeader) else index - 1
    if 0 <= start < len(blocks) - 1:
        header = getattr(blocks[start], "tap", None)
        data = getattr(blocks[start + 1], "tap", None)
        if matches_program_data(header, data):
            return [start, start + 1]
    return [index]
