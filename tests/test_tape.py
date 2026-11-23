# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise image edits against the installed tzxtools library."""

import errno
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from struct import pack
from threading import Barrier
from unittest.mock import patch

from tzx_commander.screen import BITMAP_SIZE, SCREEN_SIZE, launch_viewer, screen_memory
from tzx_commander.tape import (
    TZX_MAGIC,
    Tape,
    TapeError,
    block_text,
    program_indices,
    save_image,
    tap_losses,
    transfer_image,
    write_file,
)


def fragment(flag, body):
    checksum = flag
    for value in body:
        checksum ^= value
    return pack("<H", len(body) + 2) + bytes([flag]) + body + bytes([checksum])


def code_header(address, length, type_id=3):
    return fragment(0, bytes([type_id]) + b"SCREEN    " + pack("<HHH", length, address, 0))


class TapeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def test_tap_tzx_round_trip_and_delete(self):
        path = self.directory / "source.TAP"
        # A high bit in the first length byte is valid even before the first header.
        original = fragment(255, bytes(range(128))) + fragment(255, b"test")
        path.write_bytes(original)
        source = Tape.open(path)
        self.assertEqual(source.blocks[0].dump(), bytes(range(128)))

        intermediate = self.directory / "intermediate.tzx"
        save_image(intermediate, source.blocks, (1, 13))
        converted = Tape.open(intermediate)
        self.assertEqual(converted.version, (1, 13))
        self.assertEqual(tap_losses(converted.blocks), [])

        result = self.directory / "result.tap"
        save_image(result, converted.blocks)
        self.assertEqual(result.read_bytes(), original)

        converted.without([0])
        reduced = Tape.open(intermediate)
        self.assertEqual(reduced.version, (1, 13))
        self.assertEqual([block.dump() for block in reduced.blocks], [b"test"])

    def test_matching_header_can_select_whole_program(self):
        path = self.directory / "program.tap"
        header = bytes([3]) + b"DEMO      " + pack("<HHH", 4, 32768, 0)
        path.write_bytes(fragment(0, header) + fragment(255, b"data") + fragment(255, b"next"))
        blocks = Tape.open(path).blocks
        self.assertEqual(program_indices(blocks, 0), [0, 1])
        self.assertEqual(program_indices(blocks, 1), [0, 1])
        self.assertEqual(program_indices(blocks, 2), [2])

    def test_truncated_images_are_rejected(self):
        for name, data in [
            ("partial-length.tap", b"\x02"),
            ("partial-data.tap", b"\x02\0\xff"),
            ("partial-header.tzx", TZX_MAGIC + b"\x01"),
            ("partial-block.tzx", TZX_MAGIC + b"\x01\x14\x20\x01"),
            ("partial-body.tzx", TZX_MAGIC + b"\x01\x14\x10\xe8\x03\x04\0\xff"),
        ]:
            with self.subTest(name=name):
                path = self.directory / name
                path.write_bytes(data)
                with self.assertRaises(TapeError):
                    Tape.open(path)

    def test_incomplete_metadata_can_be_listed(self):
        path = self.directory / "metadata.tzx"
        path.write_bytes(TZX_MAGIC + b"\x01\x14\x32\x02\0\x01\x00")
        self.assertEqual(block_text(Tape.open(path).blocks[0]), "")

    def test_tap_reports_metadata_and_pause_loss(self):
        path = self.directory / "timing.tzx"
        path.write_bytes(TZX_MAGIC + b"\x01\x14\x30\x03ABC\x10\xf4\x01" + fragment(255, b"A"))
        tape = Tape.open(path)
        self.assertEqual(len(tap_losses(tape.blocks)), 2)
        target = self.directory / "converted.tap"
        save_image(target, tape.blocks)
        self.assertEqual(target.read_bytes(), fragment(255, b"A"))

    def test_unsafe_tzx_edits_and_conversion_leave_files_untouched(self):
        path = self.directory / "jump.tzx"
        data = TZX_MAGIC + b"\x01\x14\x23\x01\0\x10\xe8\x03" + fragment(255, b"data")
        path.write_bytes(data)
        tape = Tape.open(path)
        with self.assertRaises(TapeError):
            tape.without([1])
        with self.assertRaises(TapeError):
            tape.append(tape.selected([1]), tape.version)
        target = self.directory / "jump.tap"
        with self.assertRaises(TapeError):
            save_image(target, tape.blocks)
        self.assertFalse(target.exists())
        self.assertEqual(path.read_bytes(), data)

    def test_append_refuses_a_stale_image(self):
        path = self.directory / "changed.tap"
        path.write_bytes(fragment(255, b"original"))
        previous = Tape.open(path)
        path.write_bytes(fragment(255, b"external change"))
        with self.assertRaises(TapeError):
            previous.append(previous.blocks, previous.version)
        self.assertEqual(path.read_bytes(), fragment(255, b"external change"))

    @unittest.skipUnless(os.name == "posix", "Requires POSIX file locks")
    def test_concurrent_updates_cannot_overwrite_each_other(self):
        path = self.directory / "shared.tap"
        path.write_bytes(fragment(255, b"original"))
        tape = Tape.open(path)
        ready = Barrier(2, timeout=5)

        def update(body):
            data = fragment(255, body)

            def write(stream):
                stream.write(data)
                ready.wait()

            try:
                write_file(path, write, tape.stamp)
            except TapeError:
                return None
            return data

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, (b"first", b"second")))
        self.assertEqual(results.count(None), 1)
        self.assertIn(path.read_bytes(), results)

    def test_append_via_symlink_preserves_link_and_updates_image(self):
        original = self.directory / "original"
        original.write_bytes(fragment(255, b"first"))
        link = self.directory / "alias.tap"
        try:
            link.symlink_to(original)
        except OSError as error:
            self.skipTest(f"Symlinks are unavailable: {error}")
        tape = Tape.open(link)
        tape.append(tape.blocks, tape.version)
        self.assertTrue(link.is_symlink())
        self.assertEqual(original.read_bytes(), fragment(255, b"first") * 2)

    def test_new_outputs_never_overwrite_and_move_preserves_bytes(self):
        source = self.directory / "-zdroj kazety.tap"
        source.write_bytes(fragment(255, b"original"))
        target = self.directory / "cíl.tap"
        save_image(target, [])
        with self.assertRaises(FileExistsError):
            transfer_image(source, target, move=True)
        self.assertTrue(source.exists())
        self.assertEqual(target.read_bytes(), b"")
        target.unlink()
        transfer_image(source, target, move=True)
        self.assertFalse(source.exists())
        self.assertEqual(target.read_bytes(), fragment(255, b"original"))

    def test_filesystem_without_hard_links_still_protects_outputs(self):
        target = self.directory / "removable.tap"
        with patch("tzx_commander.tape.os.link", side_effect=OSError(errno.EOPNOTSUPP, "unsupported")):
            write_file(target, lambda stream: stream.write(b"original"))
            with self.assertRaises(FileExistsError):
                write_file(target, lambda stream: stream.write(b"replacement"))
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(list(self.directory.iterdir()), [target])

    def test_failed_write_leaves_no_partial_output(self):
        output = self.directory / "partial.tzx"

        def fail(stream):
            stream.write(b"partial")
            raise OSError("write failure")

        with self.assertRaises(OSError):
            write_file(output, fail)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_full_screen_preview_from_header_and_data(self):
        path = self.directory / "screen.tap"
        screen = bytes(range(256)) * 27
        path.write_bytes(code_header(0x4000, len(screen)) + fragment(255, screen))
        tape = Tape.open(path)
        self.assertEqual(screen_memory(tape.blocks, 0), screen)
        self.assertEqual(screen_memory(tape.blocks, 1), screen)

    def test_headerless_screen_requires_exact_payload_size(self):
        for size in (SCREEN_SIZE - 1, SCREEN_SIZE, SCREEN_SIZE + 1):
            with self.subTest(size=size):
                path = self.directory / "headerless.tap"
                data = b"\x18" * size
                path.write_bytes(fragment(255, data))
                tape = Tape.open(path)
                self.assertEqual(screen_memory(tape.blocks, 0), data if size == SCREEN_SIZE else None)

    def test_preview_clips_a_load_at_both_screen_boundaries(self):
        for address, data, first, visible in (
            (0x3FFE, b"ABCD", 0, b"CD"),
            (0x5801, b"\x47\x42", BITMAP_SIZE + 1, b"\x47\x42"),
            (0x5AFE, b"EFGH", SCREEN_SIZE - 2, b"EF"),
        ):
            with self.subTest(address=address):
                path = self.directory / "partial.tap"
                path.write_bytes(code_header(address, len(data)) + fragment(255, data))
                tape = Tape.open(path)
                expected = bytearray(BITMAP_SIZE) + bytearray(b"\x38" * (SCREEN_SIZE - BITMAP_SIZE))
                expected[first:first + len(visible)] = visible
                self.assertEqual(screen_memory(tape.blocks, 0), expected)

    def test_known_header_prevents_headerless_detection(self):
        data = bytes(SCREEN_SIZE)
        for address, type_id, declared_size in (
            (0x5B00, 3, SCREEN_SIZE),
            (0x4000, 0, SCREEN_SIZE),
            (0x4000, 3, SCREEN_SIZE - 1),
        ):
            with self.subTest(address=address, type_id=type_id, length=declared_size):
                path = self.directory / "not-screen.tap"
                path.write_bytes(code_header(address, declared_size, type_id) + fragment(255, data))
                tape = Tape.open(path)
                self.assertIsNone(screen_memory(tape.blocks, 0))
                self.assertIsNone(screen_memory(tape.blocks, 1))

    def test_tzx_screen_pair_can_contain_an_annotation_and_pause(self):
        path = self.directory / "screen.tzx"
        header = code_header(0x4000, 2)
        data = fragment(255, b"AB")
        first = TZX_MAGIC + b"\x01\x14\x10\xe8\x03" + header
        last = b"\x10\xe8\x03" + data
        path.write_bytes(first + b"\x30\x03ABC\x20\xe8\x03" + last)
        tape = Tape.open(path)
        self.assertEqual(screen_memory(tape.blocks, 0)[:2], b"AB")
        self.assertEqual(screen_memory(tape.blocks, 3)[:2], b"AB")
        self.assertIsNone(screen_memory(tape.blocks, 1))
        path.write_bytes(first + b"\x23\x01\x00" + last)
        self.assertIsNone(screen_memory(Tape.open(path).blocks, 0))

    def test_viewer_receives_a_temporary_scr_file(self):
        contents = bytes(SCREEN_SIZE)

        def run(arguments, **kwargs):
            executable, script, image = arguments
            self.assertEqual(executable, sys.executable)
            self.assertEqual(Path(script).name, "scr.py")
            self.assertEqual(Path(image).read_bytes(), contents)
            return subprocess.CompletedProcess(arguments, 0)

        with patch("tzx_commander.screen.subprocess.run", side_effect=run) as call:
            launch_viewer(contents)
        image = Path(call.call_args.args[0][-1])
        self.assertFalse(image.exists())


if __name__ == "__main__":
    unittest.main()
