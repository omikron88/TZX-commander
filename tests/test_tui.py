# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import tempfile
import unittest
from pathlib import Path

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from tzx_commander.tui import Commander, Panel


class CommanderTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_keys_and_editable_path(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as input_pipe:
            root = Path(directory).resolve()
            tape_path = root / "prázdná kazeta.tap"
            tape_path.write_bytes(b"")
            panels = [Panel(root), Panel(root)]
            for panel in panels:
                panel.load(root)

            commander = Commander(panels, input=input_pipe, output=DummyOutput())
            rendered = asyncio.Event()
            commander.app.after_render += lambda app: rendered.set()
            run = asyncio.create_task(commander.app.run_async())

            async def wait_for(condition):
                while not condition():
                    await rendered.wait()
                    rendered.clear()

            async def send(keys, condition):
                input_pipe.send_text(keys)
                await asyncio.wait_for(wait_for(condition), timeout=5)

            try:
                await asyncio.wait_for(rendered.wait(), timeout=5)
                await send("\t", lambda: commander.active == 1)
                await send("\x1bOQ", commander.editing_path)  # F2
                await send("\x15" + tape_path.name + "\r", lambda: panels[1].tape is not None)
                self.assertEqual(panels[1].location, tape_path)
                self.assertEqual(panels[0].location, root)

                await send("\x1bOQ", commander.editing_path)
                await send("\x1b", lambda: commander.dialog is None)
                self.assertEqual(panels[1].location, tape_path)
                input_pipe.send_text("\x1b[21~")  # F10
                self.assertEqual(await asyncio.wait_for(run, timeout=5), 0)
            finally:
                if not run.done():
                    run.cancel()
                    await asyncio.gather(run, return_exceptions=True)
