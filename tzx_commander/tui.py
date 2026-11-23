# SPDX-License-Identifier: GPL-3.0-or-later
"""Keyboard-driven, two-panel terminal interface."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Awaitable, Callable, Literal, Mapping, Sequence

from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (
    ConditionalContainer, Float, FloatContainer, FormattedTextControl, Layout, Window,
)
from prompt_toolkit.output import Output
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea
from tzxlib.tapfile import TapHeader

from .listing import ListingLine, basic_listing, code_listing
from .screen import launch_viewer, screen_memory
from .tape import (
    FORMATS,
    Tape,
    TapeError,
    block_text,
    file_stamp,
    matches_program_data,
    program_indices,
    program_taps,
    remove_image,
    require_independent,
    save_image,
    tap_losses,
    tape_format,
    transfer_image,
    unchanged,
    write_file,
)

HELP = [
    "Tab / Shift+Tab  Přepne aktivní panel.",
    "Šipky, PgUp/PgDn, Home/End  Posunou výběr.",
    "Enter  Otevře adresář/obraz nebo zobrazí detail bloku.",
    "Backspace  Zavře obraz nebo přejde do nadřazeného adresáře.",
    "Mezera / Insert  Označí blok. P  Označí související hlavičku a data.",
    "F1  Nápověda",
    "F2  Otevře zadanou cestu.",
    "F3  Zobrazí detail obrazu nebo bloku a hexadecimální náhled.",
    "B  Zobrazí listing BASICu.",
    "A  Zobrazí disassembler Z80 pro blok CODE se známou adresou.",
    "V  Otevře obrazovku ZX Spectrum z panelu nebo detailu bloku.",
    "F4  Uloží binární data aktuálního bloku.",
    "F5  Kopíruje obraz nebo bloky do druhého panelu.",
    "F6  Přesune nebo přejmenuje soubor obrazu.",
    "F7  Vytvoří adresář nebo prázdný obraz TAP/TZX.",
    "F8  Po potvrzení smaže obraz nebo vybrané bloky.",
    "F9  Převede obraz mezi TAP a TZX do souboru.",
    "F10 / Esc  Ukončí program.",
    "Ctrl+R  Obnoví obsah obou panelů.",
]
KEYBAR = "F1 Pom F2 Cesta F3 Náhl F4 Exp F5 Kop F6 Přes F7 Nový F8 Smaz F9 Přev F10 Konec"


LISTING_VIEWS = {
    "b": (0, "Listing BASIC", basic_listing),
    "a": (3, "Disassembler Z80", code_listing),
}
ViewerAction = tuple[str, Callable[[], Awaitable[None]]]


@dataclass
class Row:
    name: str
    detail: str = ""
    path: Path | None = None
    index: int | None = None


@dataclass
class Panel:
    location: Path
    tape: Tape | None = None
    rows: list[Row] = field(default_factory=list)
    cursor: int = 0
    top: int = 0
    marked: set[int] = field(default_factory=set)

    def load(self, path: Path, selection: Path | int | None = None) -> None:
        path = Path(os.path.abspath(path.expanduser()))
        tape = None
        rows = []
        if path.is_dir():
            if path != path.parent:
                rows.append(Row("..", "nahoru", path.parent))
            entries = sorted(path.iterdir(), key=lambda entry: entry.name.casefold())
            directories = []
            files = []
            for entry in entries:
                if entry.is_dir():
                    directories.append(Row(entry.name + "/", "<ADR>", entry))
                elif entry.suffix.lower() in FORMATS and entry.is_file():
                    files.append(Row(entry.name, f"{entry.stat().st_size:,} B", entry))
            rows.extend(directories)
            rows.extend(files)
        else:
            tape = Tape.open(path)
            rows.append(Row("..", "zavřít", path.parent))
            for index, block in enumerate(tape.blocks):
                title = block_text(block) or block.type
                rows.append(Row(f"{index + 1:03}  {title}", f"{block.id:02X}", index=index))
        cursor = 1 if tape is not None and tape.blocks else 0
        if selection is not None:
            for position, row in enumerate(rows):
                if row.path == selection or row.index == selection:
                    cursor = position
                    break
        self.location, self.tape, self.rows = path, tape, rows
        self.cursor, self.top = cursor, 0
        self.marked.clear()

    @property
    def row(self) -> Row | None:
        return self.rows[self.cursor] if self.rows else None

    @property
    def directory(self) -> Path:
        return self.location.parent if self.tape is not None else self.location

    def refresh(self) -> None:
        row = self.row
        selection = None if row is None else (row.path if row.path is not None else row.index)
        if self.tape is not None and not self.location.is_file():
            self.load(self.location.parent)
        else:
            self.load(self.location, selection)

    def chosen(self) -> list[int]:
        if self.marked:
            return sorted(self.marked)
        row = self.row
        return [row.index] if row is not None and row.index is not None else []

    def move(self, change: int, visible: int) -> None:
        self.cursor = max(0, min(self.cursor + change, len(self.rows) - 1))
        self.scroll(visible)

    def scroll(self, visible: int) -> None:
        self.top = max(0, min(self.top, len(self.rows) - visible))
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + visible:
            self.top = self.cursor - visible + 1


def fit(value: str, width: int, *, pad: bool = True) -> str:
    parts = []
    for character in value:
        if not character.isprintable():
            character = " "
        columns = get_cwidth(character)
        if columns > width:
            break
        parts.append(character)
        width -= columns
    return "".join(parts) + (" " * width if pad else "")


def fit_viewer_line(line: ListingLine, left: int, width: int) -> StyleAndTextTuples:
    if isinstance(line, str):
        return [("", " " + fit(line[left:], width))]

    result: StyleAndTextTuples = [("", " ")]
    for style, text, *_ in line:
        if left >= len(text):
            left -= len(text)
            continue
        remaining = text[left:]
        visible = fit(remaining, width, pad=False)
        if visible:
            result.append((style, visible))
        width -= get_cwidth(visible)
        if width <= 0 or len(visible) < len(remaining):
            break
        left = 0
    return result


def viewer_line_width(line: ListingLine) -> int:
    return get_cwidth(line) if isinstance(line, str) else fragment_list_width(line)


def scroll_delta(key: Keys | str, page: int, count: int) -> int:
    return {
        Keys.Up: -1, Keys.Down: 1, Keys.PageUp: -page, Keys.PageDown: page,
        Keys.Home: -count, Keys.End: count,
    }.get(key, 0)


@dataclass
class Dialog:
    mode: Literal["viewer", "path", "confirm", "create"]
    title: str
    lines: Sequence[ListingLine]
    footer: str
    answer: asyncio.Future[str | None]
    top: int = 0
    left: int = 0
    viewer_keys: tuple[str, ...] = ()


class Commander:
    def __init__(
        self, panels: list[Panel], *, input: Input | None = None, output: Output | None = None
    ):
        self.panels = panels
        self.active = 0
        self.status = ""
        self.dialog: Dialog | None = None
        self.operation: asyncio.Task[None] | None = None
        self.actions = {
            Keys.F1: self.help, Keys.F2: self.go_to, Keys.F3: self.inspect,
            Keys.F4: self.extract, Keys.F5: self.copy, Keys.F6: self.move_image,
            Keys.F7: self.create, Keys.F8: self.remove, Keys.F9: self.convert,
            Keys.Enter: self.open_row, Keys.ControlJ: self.open_row,
            "v": self.preview_screen, "V": self.preview_screen,
            "b": partial(self.preview_listing, "b"), "B": partial(self.preview_listing, "b"),
            "a": partial(self.preview_listing, "a"), "A": partial(self.preview_listing, "a"),
        }

        self.view = FormattedTextControl(self.draw, focusable=True)
        self.path_field = TextArea(
            multiline=False, wrap_lines=False, height=1, style="class:mark",
            prompt="> ", accept_handler=self.accept_path,
        )
        layout = Layout(FloatContainer(
            Window(self.view, style="class:body", always_hide_cursor=True),
            [Float(top=3, left=1, right=1, content=ConditionalContainer(
                self.path_field, filter=Condition(self.editing_path)
            ))],
        ), focused_element=self.view)

        bindings = KeyBindings()
        for key in (
            *self.actions, Keys.F10, Keys.Tab, Keys.BackTab, Keys.Backspace,
            Keys.Up, Keys.Down, Keys.Left, Keys.Right, Keys.PageUp, Keys.PageDown, Keys.Home, Keys.End,
            Keys.ControlR, Keys.Insert, " ", "p", "P", "y", "Y", "n", "N",
            "1", "2", "3",
        ):
            bindings.add(key, filter=~Condition(self.editing_path))(self.handle_key)
        bindings.add(Keys.Escape, eager=True)(self.handle_key)
        bindings.add(Keys.ControlC)(lambda event: event.app.exit(result=130))
        self.app: Application[int] = Application(
            layout=layout, key_bindings=bindings, full_screen=True, erase_when_done=True,
            style=Style.from_dict({
                "body": "ansiwhite bg:ansiblue",
                "header": "ansiblue bg:ansicyan bold",
                "selection": "ansiblue bg:ansicyan",
                "mark": "ansiyellow bg:ansiblue bold",
                "basic-number": "ansibrightcyan bg:ansiblue bold",
            }),
            input=input, output=output,
        )
        self.app.ttimeoutlen = 0.05

    @property
    def panel(self) -> Panel:
        return self.panels[self.active]

    @property
    def other(self) -> Panel:
        return self.panels[1 - self.active]

    def editing_path(self) -> bool:
        return self.dialog is not None and self.dialog.mode == "path"

    def panel_lines(self, panel: Panel, active: bool, width: int, visible: int) -> list[StyleAndTextTuples]:
        inner = width - 2
        label = fit(f" {panel.location} ", inner).rstrip()
        title_style = "class:header" if active else "bold"
        rows: list[StyleAndTextTuples] = [
            [("", "┌"), (title_style, label), ("", "─" * (inner - get_cwidth(label)) + "┐")],
        ]
        detail_width = 6 if panel.tape is not None else 13
        name_width = inner - detail_width - 2
        name, detail = ("Blok / typ", "ID") if panel.tape is not None else ("Název", "Velikost")
        headings = f" {fit(name, name_width)} {fit(detail, detail_width)}"
        rows.append([("", "│"), ("class:mark", headings), ("", "│")])

        panel.scroll(visible)
        for position in range(panel.top, panel.top + visible):
            if position >= len(panel.rows):
                rows.append([("", "│" + " " * inner + "│")])
                continue
            row = panel.rows[position]
            marked = row.index in panel.marked
            selected = active and position == panel.cursor
            style = "class:selection" if selected else "class:mark" if marked else ""
            line = ("*" if marked else " ") + fit(row.name, name_width) + " " + fit(row.detail, detail_width)
            rows.append([("", "│"), (style, line), ("", "│")])

        if panel.tape is None:
            count = sum(row.path != panel.location.parent for row in panel.rows)
            footer = f"{count} položek"
        else:
            footer = f"{len(panel.tape.blocks)} bloků"
            if panel.marked:
                footer += f" / {len(panel.marked)} označeno"
        label = fit(f" {footer}", inner - 1).rstrip() + " "
        rows.append([("", "└" + label + "─" * (inner - get_cwidth(label)) + "┘")])
        return rows

    def draw(self) -> StyleAndTextTuples:
        height, width = self.app.output.get_size()
        dialog = self.dialog
        if dialog is not None:
            page = max(0, height - 3)
            dialog.top = min(dialog.top, max(0, len(dialog.lines) - page))
            lines: list[StyleAndTextTuples] = [[("class:header", fit(" " + dialog.title, width))]]
            lines.extend(fit_viewer_line(line, dialog.left, width - 2)
                         for line in dialog.lines[dialog.top:dialog.top + page])
            if height > 1:
                lines.extend([] for _ in range(height - len(lines) - 1))
                lines.append([("class:header", fit(dialog.footer, width))])
        elif width < 56 or height < 12:
            lines = [[("", fit("Zvětšete terminál alespoň na 56 × 12. Esc ukončí program.", width))]]
        else:
            visible = height - 7
            left = self.panel_lines(self.panels[0], self.active == 0, width // 2, visible)
            right = self.panel_lines(self.panels[1], self.active == 1, width - width // 2, visible)
            row = self.panel.row
            status = self.status or (row.name if row else str(self.panel.location))
            tab_help = "Tab Panel  Mezera Označit  P Pár"
            if row is not None and row.index is not None:
                tab_help += "  B BASIC  A Z80  V Obrazovka"
            keybar = KEYBAR if width >= len(KEYBAR) else "F1 Pomoc  F2–F9 Akce  F10 Konec"
            lines = [[("class:header", fit(" TZX Commander", width))]]
            lines.extend(a + b for a, b in zip(left, right))
            lines.extend([
                [("", " " + fit(status, width - 2))],
                [("class:mark", " " + fit(tab_help, width - 2))],
                [("class:header", fit(keybar, width))],
            ])
        result: StyleAndTextTuples = []
        for index, line in enumerate(lines):
            if index:
                result.append(("", "\n"))
            result.extend(line)
        return result

    async def modal(
        self, mode: Literal["viewer", "path", "confirm", "create"], title: str,
        lines: Sequence[ListingLine], footer: str, entry: str = "", *, viewer_keys: tuple[str, ...] = (),
    ) -> str | None:
        answer = asyncio.get_running_loop().create_future()
        self.dialog = Dialog(mode, title, lines, footer, answer, viewer_keys=viewer_keys)
        if mode == "path":
            self.path_field.buffer.set_document(Document(entry, len(entry)))
            self.app.layout.focus(self.path_field)
        self.app.invalidate()
        try:
            return await answer
        finally:
            self.dialog = None
            self.app.layout.focus(self.view)
            self.app.invalidate()

    def accept_path(self, buffer: Buffer) -> bool:
        dialog = self.dialog
        if dialog is not None and dialog.mode == "path" and not dialog.answer.done():
            dialog.answer.set_result(buffer.text if buffer.text.strip() else None)
        return True

    async def viewer(
        self, title: str, lines: Sequence[ListingLine], *, actions: Mapping[str, ViewerAction] | None = None
    ) -> None:
        actions = actions or {}
        footer = " Esc zpět  ·  ↑↓←→ PgUp/PgDn Home/End"
        if actions:
            shortcuts = "  ·  ".join(f"{key.upper()} {label}" for key, (label, _) in actions.items())
            footer = f" {shortcuts}  ·{footer}"
        while True:
            action = await self.modal("viewer", title, lines, footer, viewer_keys=tuple(actions))
            if action is None:
                return
            await actions[action][1]()

    async def help(self) -> None:
        await self.viewer("Nápověda", HELP)

    async def ask(self, title: str, label: str, value: str = "") -> str | None:
        return await self.modal("path", title, [label], " Enter potvrdit  ·  Esc zpět", value)

    async def target(self, title: str, base: Path, name: str) -> Path | None:
        value = await self.ask(title, f"Uložit do {base}  ·  Zadejte název nebo cestu", name)
        return base / Path(value).expanduser() if value is not None else None

    async def confirm(self, title: str, *lines: str) -> bool:
        return await self.modal("confirm", title, list(lines), " A ano  ·  N / Esc zpět") == "a"

    def refresh(self) -> None:
        errors = []
        for panel in self.panels:
            try:
                panel.refresh()
            except (OSError, TapeError) as error:
                errors.append(str(error))
        if errors:
            raise TapeError(" / ".join(errors))

    def disk_image(self) -> Path:
        row = self.panel.row
        if self.panel.tape is not None:
            return self.panel.location
        if row and row.path is not None and row.path.is_file():
            return row.path
        raise TapeError("Vyberte obraz TAP/TZX.")

    async def open_row(self) -> None:
        row = self.panel.row
        if row is None:
            return
        if row.path is not None:
            selection = self.panel.location if row.name == ".." else None
            self.panel.load(row.path, selection)
        else:
            await self.inspect()

    def parent(self) -> None:
        self.panel.load(self.panel.location.parent, self.panel.location)

    async def go_to(self) -> None:
        value = await self.ask("Otevřít cestu", "Adresář nebo obraz TAP/TZX", str(self.panel.location))
        if value is not None:
            self.panel.load(self.panel.directory / Path(value).expanduser())

    async def inspect(self) -> None:
        tape = self.panel.tape
        row = self.panel.row
        if tape is not None and row is not None and row.index is not None:
            block = tape.blocks[row.index]
            lines = [f"ID {block.id:02X}  {block.type}", *block_text(block).splitlines()]
            data = block.dump()
            if data is not None:
                lines.extend(["", f"{len(data):,} B dat"])
                for position in range(0, min(4096, len(data)), 16):
                    chunk = data[position:position + 16]
                    hexes = chunk.hex(" ")
                    printable = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
                    lines.append(f"{position:06X}  {hexes:<47}  {printable}")
                if len(data) > 4096:
                    lines.append("Náhled zobrazuje prvních 4096 B. F4 uloží celý blok.")
            actions: dict[str, ViewerAction] = {}
            header, payload = program_taps(tape.blocks, row.index)
            if header is not None and matches_program_data(header, payload):
                for key, (type_id, label, _) in LISTING_VIEWS.items():
                    if header.typeId() == type_id:
                        actions[key] = (label, partial(self.preview_listing, key))
            screen = screen_memory(tape.blocks, row.index)
            if screen is not None:
                actions["v"] = ("Obrazovka", partial(self.show_screen, screen))
            await self.viewer(f"{self.panel.location.name} / blok {row.index + 1:03}", lines, actions=actions)
            return

        path = self.disk_image()
        tape = tape or Tape.open(path)
        fmt = tape_format(path)[1:].upper()
        if fmt == "TZX":
            fmt += f" {tape.version[0]}.{tape.version[1]:02}"
        lines = [f"{fmt}  /  {len(tape.blocks)} bloků", ""]
        lines.extend(f"{index:03}  {block.id:02X}  {block_text(block) or block.type}"
                     for index, block in enumerate(tape.blocks, 1))
        await self.viewer(path.name, lines)

    async def preview_listing(self, key: str) -> None:
        tape, row = self.panel.tape, self.panel.row
        if tape is None or row is None or row.index is None:
            raise TapeError("Otevřete obraz kazety a vyberte blok.")
        type_id, label, render = LISTING_VIEWS[key]
        header, payload = program_taps(tape.blocks, row.index)
        if (header is None or header.typeId() != type_id
                or payload is None or not matches_program_data(header, payload)):
            raise TapeError(f"{label} vyžaduje odpovídající hlavičku a datový blok.")
        lines = render(header, payload.body())
        await self.viewer(f"{self.panel.location.name} / blok {row.index + 1:03} / {label}", lines)

    async def preview_screen(self) -> None:
        tape, row = self.panel.tape, self.panel.row
        if tape is None or row is None or row.index is None:
            raise TapeError("Otevřete obraz kazety a vyberte blok.")
        screen = screen_memory(tape.blocks, row.index)
        if screen is None:
            raise TapeError("Ve vybraném bloku nelze rozpoznat obrazovku ZX Spectrum.")
        await self.show_screen(screen)

    async def show_screen(self, screen: bytes) -> None:
        await run_in_terminal(lambda: launch_viewer(screen), in_executor=True)

    def mark(self, pair: bool) -> None:
        row = self.panel.row
        if self.panel.tape is None or row is None or row.index is None:
            return
        indices = set(program_indices(self.panel.tape.blocks, row.index) if pair else [row.index])
        if indices <= self.panel.marked:
            self.panel.marked -= indices
        else:
            self.panel.marked |= indices

    async def allow_tap(self, path: Path, blocks) -> bool:
        if tape_format(path) != ".tap":
            return True
        losses = tap_losses(blocks)
        return not losses or await self.confirm(
            "Uložení do TAP", f"TAP {', '.join(losses)}.", "Pokračovat v uložení?"
        )

    async def copy(self) -> None:
        source, destination = self.panel, self.other
        if source.tape is None and destination.tape is None:
            image = self.disk_image()
            target = await self.target("Kopírovat obraz", destination.directory, image.name)
            if target is not None:
                transfer_image(image, target)
                self.refresh()
                self.status = f"Zkopírováno do {target.name}."
            return

        if source.tape is not None:
            indices = source.chosen()
            if not indices:
                raise TapeError("Vyberte alespoň jeden blok.")
            tape = source.tape
            blocks = tape.selected(indices)
            extension = tape_format(source.location)
            stem = source.location.stem
        else:
            image = self.disk_image()
            tape = Tape.open(image)
            blocks = tape.blocks
            extension, stem = tape_format(image), image.stem
        unchanged(tape.path, tape.stamp)
        require_independent(blocks)
        if destination.tape is not None:
            if tape.path == destination.tape.path or tape.stamp[:2] == destination.tape.stamp[:2]:
                raise TapeError("Cílem je tentýž obraz.")
            if await self.allow_tap(destination.location, blocks) and await self.confirm(
                "Přidat bloky", f"Přidat {len(blocks)} bloků na konec {destination.location.name}?"
            ):
                destination.tape.append(blocks, tape.version)
                self.refresh()
                self.status = f"Přidáno {len(blocks)} bloků."
        else:
            target = await self.target("Uložit bloky jako obraz", destination.directory, stem + "-bloky" + extension)
            if target is not None and await self.allow_tap(target, blocks):
                save_image(target, blocks, tape.version)
                self.refresh()
                self.status = f"Uloženo do {target.name}."

    async def move_image(self) -> None:
        if self.panel.tape is not None:
            raise TapeError("Nejprve zavřete obraz klávesou Backspace.")
        image = self.disk_image()
        target = await self.target("Přesunout / přejmenovat obraz", self.other.directory, image.name)
        if target is not None:
            transfer_image(image, target, move=True)
            self.refresh()
            self.status = f"Přesunuto do {target}."

    async def create(self) -> None:
        key = await self.modal(
            "create", "Nový", ["1  Adresář", "2  Obraz TAP", "3  Obraz TZX"], " 1–3 vybrat  ·  Esc zpět"
        )
        if key is None:
            return
        extension = {"1": "", "2": ".tap", "3": ".tzx"}[key]
        title = "Vytvořit obraz" if extension else "Vytvořit adresář"
        target = await self.target(title, self.panel.directory, "nova-kazeta" + extension if extension else "")
        if target is None:
            return
        if extension:
            if not target.suffix:
                target = target.with_suffix(extension)
            if tape_format(target) != extension:
                raise TapeError(f"Zvolený formát vyžaduje příponu {extension}.")
            save_image(target, [])
        else:
            target.mkdir()
        self.refresh()
        self.status = f"Vytvořeno {target.name}."

    async def remove(self) -> None:
        panel = self.panel
        if panel.tape is not None:
            indices = panel.chosen()
            if not indices:
                raise TapeError("Vyberte alespoň jeden blok.")
            require_independent(panel.tape.blocks)
            if not await self.confirm("Smazat bloky", f"Smazat {len(indices)} bloků z {panel.location.name}?"):
                return
            panel.tape.without(indices)
            self.refresh()
            self.status = f"Smazáno {len(indices)} bloků."
        else:
            path = self.disk_image()
            stamp = file_stamp(path.lstat())
            if await self.confirm("Smazat obraz", f"Trvale smazat {path.name}?"):
                remove_image(path, stamp)
                self.refresh()
                self.status = f"Smazáno {path.name}."

    async def convert(self) -> None:
        image = self.disk_image()
        tape = self.panel.tape or Tape.open(image)
        extension = ".tzx" if tape_format(image) == ".tap" else ".tap"
        target = await self.target("Převést obraz", self.other.directory, image.with_suffix(extension).name)
        if target is None:
            return
        if tape_format(target) != extension:
            raise TapeError(f"Při převodu zvolte příponu {extension}.")
        if await self.allow_tap(target, tape.blocks):
            unchanged(tape.path, tape.stamp)
            save_image(target, tape.blocks, tape.version)
            self.refresh()
            self.status = f"Převedeno do {target.name}."

    async def extract(self) -> None:
        tape, row = self.panel.tape, self.panel.row
        if tape is None or row is None or row.index is None:
            raise TapeError("Otevřete obraz a vyberte datový blok.")
        index = row.index
        block = tape.blocks[index]
        tap = getattr(block, "tap", None)
        data = block.dump()
        if data is None or isinstance(tap, TapHeader):
            raise TapeError("Vybraný blok nemá data k exportu.")
        extension = ".bin"
        pair = program_indices(tape.blocks, index)
        if len(pair) == 2:
            header = tape.blocks[pair[0]].tap
            if header.typeId() == 3 and header.param1() == 0x4000 and len(data) == 6912:
                extension = ".scr"
        if tap is not None and not tap.valid():
            if not await self.confirm("Export", "Blok má chybný kontrolní součet. Uložit data?"):
                return
        name = f"{self.panel.location.stem}-{index + 1:03}{extension}"
        target = await self.target("Export dat", self.other.directory, name)
        if target is not None:
            unchanged(tape.path, tape.stamp)
            write_file(target, lambda stream: stream.write(data))
            self.status = f"Exportováno {len(data)} B do {target.name}."

    def handle_dialog_key(self, dialog: Dialog, key: Keys | str) -> None:
        if dialog.answer.done():
            return
        if key == Keys.Escape:
            dialog.answer.set_result(None)
        elif dialog.mode == "viewer":
            if key in (Keys.Enter, Keys.ControlJ, Keys.F3, Keys.F10):
                dialog.answer.set_result(None)
            elif key.lower() in dialog.viewer_keys:
                dialog.answer.set_result(key.lower())
            elif key in (Keys.Left, Keys.Right):
                width = self.app.output.get_size().columns - 2
                longest = max(map(viewer_line_width, dialog.lines), default=0)
                shift = 8 if key == Keys.Right else -8
                dialog.left = max(0, min(dialog.left + shift, longest - width))
            else:
                page = max(1, self.app.output.get_size().rows - 3)
                shift = scroll_delta(key, page, len(dialog.lines))
                dialog.top = max(0, min(dialog.top + shift, len(dialog.lines) - page))
        elif dialog.mode == "confirm":
            if key in ("a", "A", "y", "Y"):
                dialog.answer.set_result("a")
            elif key in ("n", "N", Keys.Enter, Keys.ControlJ):
                dialog.answer.set_result(None)
        elif dialog.mode == "create" and key in ("1", "2", "3"):
            dialog.answer.set_result(key)

    def handle_key(self, event: KeyPressEvent) -> None:
        key = event.key_sequence[-1].key
        if self.dialog is not None:
            self.handle_dialog_key(self.dialog, key)
            return
        if key in (Keys.Escape, Keys.F10):
            event.app.exit(result=0)
            return
        height, width = self.app.output.get_size()
        if self.operation is not None or height < 12 or width < 56:
            return
        self.status = ""
        visible = height - 7
        shift = scroll_delta(key, visible, len(self.panel.rows))
        try:
            if key in (Keys.Tab, Keys.BackTab):
                self.active = 1 - self.active
            elif shift:
                self.panel.move(shift, visible)
            elif key == Keys.Backspace:
                self.parent()
            elif key == Keys.ControlR:
                self.refresh()
            elif key in (" ", Keys.Insert, "p", "P"):
                self.mark(key in ("p", "P"))
            elif key in self.actions:
                self.operation = self.app.create_background_task(self.perform(self.actions[key]))
        except (OSError, TapeError, ValueError, RuntimeError) as error:
            self.report_error(error)

    async def perform(self, action: Callable[[], Awaitable[None]]) -> None:
        try:
            await action()
        except (OSError, TapeError, ValueError, RuntimeError) as error:
            self.report_error(error)
        finally:
            self.operation = None
            self.app.invalidate()

    def report_error(self, error: Exception) -> None:
        if isinstance(error, FileExistsError) and (error.filename2 or error.filename):
            name = Path(error.filename2 or error.filename).name
            self.status = f"Cíl {name} již existuje."
        else:
            self.status = str(error)

    def run(self) -> int:
        return self.app.run()
