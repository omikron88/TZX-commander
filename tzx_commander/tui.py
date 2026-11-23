# SPDX-License-Identifier: GPL-3.0-or-later
"""Keyboard-driven, two-panel terminal interface."""

from __future__ import annotations

import curses
import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from tzxlib.tapfile import TapHeader

from .tape import (
    FORMATS,
    Tape,
    TapeError,
    block_text,
    file_stamp,
    program_indices,
    remove_image,
    require_independent,
    save_image,
    tap_losses,
    tape_format,
    transfer_image,
    unchanged,
    write_file,
)

ENTER = ("\n", "\r", curses.KEY_ENTER)
BACKSPACE = ("\b", "\x7f", curses.KEY_BACKSPACE)
HELP = [
    "Tab / Shift+Tab  Přepne aktivní panel.",
    "Šipky, PgUp/PgDn, Home/End  Posunou výběr.",
    "Enter  Otevře adresář/obraz nebo zobrazí detail bloku.",
    "Backspace  Zavře obraz nebo přejde do nadřazeného adresáře.",
    "Mezera / Insert  Označí blok. P  Označí související hlavičku a data.",
    "F1  Nápověda",
    "F2  Otevře zadanou cestu.",
    "F3  Zobrazí detail obrazu nebo bloku a hexadecimální náhled.",
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


def clean(value: str) -> str:
    return "".join(character if character.isprintable() else " " for character in value)


def cell_width(character: str) -> int:
    if unicodedata.combining(character):
        return 0
    return 2 if unicodedata.east_asian_width(character) in ("F", "W") else 1


def clip(value: str, width: int) -> str:
    if width <= 0:
        return ""
    result = []
    for character in value:
        width -= cell_width(character)
        if width < 0:
            break
        result.append(character)
    return "".join(result)


class Commander:
    def __init__(self, screen, panels: list[Panel]):
        self.screen = screen
        self.panels = panels
        self.active = 0
        self.status = ""
        self.body = 0
        self.header = curses.A_REVERSE | curses.A_BOLD
        self.selection = curses.A_REVERSE
        self.mark_style = curses.A_BOLD
        if curses.has_colors():
            curses.start_color()
            curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
            curses.init_pair(2, curses.COLOR_BLUE, curses.COLOR_CYAN)
            curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLUE)
            self.body = curses.color_pair(1)
            self.header = curses.color_pair(2) | curses.A_BOLD
            self.selection = curses.color_pair(2)
            self.mark_style = curses.color_pair(3) | curses.A_BOLD
        self.screen.bkgd(" ", self.body)
        self.cursor_visible(False)

    @property
    def panel(self) -> Panel:
        return self.panels[self.active]

    @property
    def other(self) -> Panel:
        return self.panels[1 - self.active]

    def cursor_visible(self, visible: bool) -> None:
        try:
            curses.curs_set(int(visible))
        except curses.error:
            pass

    def put(self, y: int, x: int, value: str, width: int, attr: int = 0) -> None:
        height, columns = self.screen.getmaxyx()
        if not (0 <= y < height and 0 <= x < columns):
            return
        width = min(width, columns - x - (y == height - 1))
        value = clip(clean(value), width)
        if value:
            self.screen.addstr(y, x, value, attr)

    def draw_panel(self, panel: Panel, active: bool, x: int, width: int, bottom: int) -> None:
        self.screen.vline(1, x, curses.ACS_VLINE, bottom)
        self.screen.vline(1, x + width - 1, curses.ACS_VLINE, bottom)
        for y, left, right in [(1, curses.ACS_ULCORNER, curses.ACS_URCORNER),
                               (bottom, curses.ACS_LLCORNER, curses.ACS_LRCORNER)]:
            self.screen.hline(y, x + 1, curses.ACS_HLINE, width - 2)
            self.screen.addch(y, x, left)
            self.screen.addch(y, x + width - 1, right)
        title = f" {panel.location} "
        self.put(1, x + 1, title, width - 2, self.header if active else curses.A_BOLD)
        headings = ("Blok / typ", "ID") if panel.tape is not None else ("Název", "Velikost")
        detail_width = 6 if panel.tape is not None else 13
        detail_x = x + width - detail_width - 2
        self.put(2, x + 2, headings[0], detail_x - x - 3, self.mark_style)
        self.put(2, detail_x, headings[1], detail_width, self.mark_style)

        visible = bottom - 3
        panel.scroll(visible)
        for offset, row in enumerate(panel.rows[panel.top:panel.top + visible]):
            selected = active and panel.top + offset == panel.cursor
            marked = row.index in panel.marked
            attr = self.selection if selected else self.mark_style if marked else self.body
            y = 3 + offset
            if selected:
                self.put(y, x + 1, " " * (width - 2), width - 2, attr)
            self.put(y, x + 1, "*" if marked else " ", 1, attr)
            self.put(y, x + 2, row.name, detail_x - x - 3, attr)
            self.put(y, detail_x, row.detail, detail_width, attr)
        if panel.tape is None:
            count = sum(row.path != panel.location.parent for row in panel.rows)
            footer = f" {count} položek "
        else:
            footer = f" {len(panel.tape.blocks)} bloků"
            if panel.marked:
                footer += f" / {len(panel.marked)} označeno"
            footer += " "
        self.put(bottom, x + 1, footer, width - 2)

    def draw(self) -> int:
        height, width = self.screen.getmaxyx()
        self.screen.erase()
        if width < 56 or height < 12:
            self.put(0, 0, "Zvětšete terminál alespoň na 56 × 12. Esc ukončí program.", width)
            self.screen.refresh()
            return 0
        self.put(0, 0, " TZX Commander".ljust(width), width, self.header)
        bottom = height - 4
        self.draw_panel(self.panels[0], self.active == 0, 0, width // 2, bottom)
        self.draw_panel(self.panels[1], self.active == 1, width // 2, width - width // 2, bottom)
        row = self.panel.row
        status = self.status or (row.name if row else str(self.panel.location))
        tab_help = "Tab  Přepnout panel   Mezera  Označit blok   P  Označit pár"
        self.put(height - 3, 1, status, width - 2)
        self.put(height - 2, 1, tab_help, width - 2, self.mark_style)
        keybar = KEYBAR if width >= len(KEYBAR) else "F1 Pomoc  F2–F9 Akce  F10 Konec"
        self.put(height - 1, 0, keybar.ljust(width), width, self.header)
        self.screen.refresh()
        return bottom - 3

    def modal(self, title: str, lines: list[str], footer: str, top: int = 0) -> tuple[int, int]:
        height, width = self.screen.getmaxyx()
        self.screen.erase()
        self.put(0, 0, (" " + title).ljust(width), width, self.header)
        for offset, line in enumerate(lines[top:top + max(1, height - 3)], 1):
            self.put(offset, 1, line, width - 2, self.body)
        if height > 1:
            self.put(height - 1, 0, footer.ljust(width), width, self.header)
        self.screen.refresh()
        return height, width

    def viewer(self, title: str, lines: list[str]) -> None:
        top = 0
        while True:
            height, _ = self.modal(title, lines, " ↑/↓  PgUp/PgDn  Home/End  ·  Esc zpět", top)
            key = self.screen.get_wch()
            if key in (*ENTER, "\x1b", curses.KEY_F3, curses.KEY_F10):
                return
            page = max(1, height - 3)
            steps = {curses.KEY_UP: -1, curses.KEY_DOWN: 1,
                     curses.KEY_PPAGE: -page, curses.KEY_NPAGE: page,
                     curses.KEY_HOME: -len(lines), curses.KEY_END: len(lines)}
            top = max(0, min(top + steps.get(key, 0), len(lines) - page))

    def ask(self, title: str, label: str, value: str = "") -> str | None:
        cursor = len(value)
        self.cursor_visible(True)
        try:
            while True:
                height, width = self.modal(title, [label], " Enter potvrdit  ·  Esc zpět")
                limit = max(1, width - 4)
                start = cursor
                used = 0
                while start and used + cell_width(value[start - 1]) < limit:
                    start -= 1
                    used += cell_width(value[start])
                if height > 4 and width > 4:
                    self.put(3, 1, "> " + value[start:], width - 2, self.mark_style)
                    self.screen.move(3, 3 + used)
                    self.screen.refresh()
                key = self.screen.get_wch()
                if key in ENTER:
                    return value if value.strip() else None
                if key == "\x1b":
                    return None
                if key == curses.KEY_LEFT:
                    cursor = max(0, cursor - 1)
                elif key == curses.KEY_RIGHT:
                    cursor = min(len(value), cursor + 1)
                elif key in (curses.KEY_HOME, "\x01"):
                    cursor = 0
                elif key in (curses.KEY_END, "\x05"):
                    cursor = len(value)
                elif key == "\x15":
                    value, cursor = "", 0
                elif key in BACKSPACE and cursor:
                    value = value[:cursor - 1] + value[cursor:]
                    cursor -= 1
                elif key == curses.KEY_DC:
                    value = value[:cursor] + value[cursor + 1:]
                elif isinstance(key, str) and key.isprintable():
                    value = value[:cursor] + key + value[cursor:]
                    cursor += 1
        finally:
            self.cursor_visible(False)

    def target(self, title: str, base: Path, name: str) -> Path | None:
        value = self.ask(title, f"Uložit do {base}  ·  Zadejte název nebo cestu", name)
        return base / Path(value).expanduser() if value is not None else None

    def confirm(self, title: str, *lines: str) -> bool:
        while True:
            self.modal(title, list(lines), " A ano  ·  N / Esc zpět")
            key = self.screen.get_wch()
            if key in ("a", "A", "y", "Y"):
                return True
            if key in ("n", "N", "\x1b", *ENTER):
                return False

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

    def open_row(self) -> None:
        row = self.panel.row
        if row is None:
            return
        if row.path is not None:
            selection = self.panel.location if row.name == ".." else None
            self.panel.load(row.path, selection)
        else:
            self.inspect()

    def parent(self) -> None:
        self.panel.load(self.panel.location.parent, self.panel.location)

    def go_to(self) -> None:
        value = self.ask("Otevřít cestu", "Adresář nebo obraz TAP/TZX", str(self.panel.location))
        if value is not None:
            self.panel.load(self.panel.directory / Path(value).expanduser())

    def inspect(self) -> None:
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
            self.viewer(f"{self.panel.location.name} / blok {row.index + 1:03}", lines)
            return

        path = self.disk_image()
        tape = tape or Tape.open(path)
        fmt = tape_format(path)[1:].upper()
        if fmt == "TZX":
            fmt += f" {tape.version[0]}.{tape.version[1]:02}"
        lines = [f"{fmt}  /  {len(tape.blocks)} bloků", ""]
        lines.extend(f"{index:03}  {block.id:02X}  {block_text(block) or block.type}"
                     for index, block in enumerate(tape.blocks, 1))
        self.viewer(path.name, lines)

    def mark(self, pair: bool) -> None:
        row = self.panel.row
        if self.panel.tape is None or row is None or row.index is None:
            return
        indices = set(program_indices(self.panel.tape.blocks, row.index) if pair else [row.index])
        if indices <= self.panel.marked:
            self.panel.marked -= indices
        else:
            self.panel.marked |= indices

    def allow_tap(self, path: Path, blocks) -> bool:
        if tape_format(path) != ".tap":
            return True
        losses = tap_losses(blocks)
        return not losses or self.confirm(
            "Uložení do TAP", f"TAP {', '.join(losses)}.", "Pokračovat v uložení?"
        )

    def copy(self) -> None:
        source, destination = self.panel, self.other
        if source.tape is None and destination.tape is None:
            image = self.disk_image()
            target = self.target("Kopírovat obraz", destination.directory, image.name)
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
            if self.allow_tap(destination.location, blocks) and self.confirm(
                "Přidat bloky", f"Přidat {len(blocks)} bloků na konec {destination.location.name}?"
            ):
                destination.tape.append(blocks, tape.version)
                self.refresh()
                self.status = f"Přidáno {len(blocks)} bloků."
        else:
            target = self.target("Uložit bloky jako obraz", destination.directory, stem + "-bloky" + extension)
            if target is not None and self.allow_tap(target, blocks):
                save_image(target, blocks, tape.version)
                self.refresh()
                self.status = f"Uloženo do {target.name}."

    def move_image(self) -> None:
        if self.panel.tape is not None:
            raise TapeError("Nejprve zavřete obraz klávesou Backspace.")
        image = self.disk_image()
        target = self.target("Přesunout / přejmenovat obraz", self.other.directory, image.name)
        if target is not None:
            transfer_image(image, target, move=True)
            self.refresh()
            self.status = f"Přesunuto do {target}."

    def create(self) -> None:
        while True:
            self.modal("Nový", ["1  Adresář", "2  Obraz TAP", "3  Obraz TZX"], " 1–3 vybrat  ·  Esc zpět")
            key = self.screen.get_wch()
            if key == "\x1b":
                return
            if key in ("1", "2", "3"):
                break
        extension = {"1": "", "2": ".tap", "3": ".tzx"}[key]
        title = "Vytvořit obraz" if extension else "Vytvořit adresář"
        target = self.target(title, self.panel.directory, "nova-kazeta" + extension if extension else "")
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

    def remove(self) -> None:
        panel = self.panel
        if panel.tape is not None:
            indices = panel.chosen()
            if not indices:
                raise TapeError("Vyberte alespoň jeden blok.")
            require_independent(panel.tape.blocks)
            if not self.confirm("Smazat bloky", f"Smazat {len(indices)} bloků z {panel.location.name}?"):
                return
            panel.tape.without(indices)
            self.refresh()
            self.status = f"Smazáno {len(indices)} bloků."
        else:
            path = self.disk_image()
            stamp = file_stamp(path.lstat())
            if self.confirm("Smazat obraz", f"Trvale smazat {path.name}?"):
                remove_image(path, stamp)
                self.refresh()
                self.status = f"Smazáno {path.name}."

    def convert(self) -> None:
        image = self.disk_image()
        tape = self.panel.tape or Tape.open(image)
        extension = ".tzx" if tape_format(image) == ".tap" else ".tap"
        target = self.target("Převést obraz", self.other.directory, image.with_suffix(extension).name)
        if target is None:
            return
        if tape_format(target) != extension:
            raise TapeError(f"Při převodu zvolte příponu {extension}.")
        if self.allow_tap(target, tape.blocks):
            unchanged(tape.path, tape.stamp)
            save_image(target, tape.blocks, tape.version)
            self.refresh()
            self.status = f"Převedeno do {target.name}."

    def extract(self) -> None:
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
            if not self.confirm("Export", "Blok má chybný kontrolní součet. Uložit data?"):
                return
        name = f"{self.panel.location.stem}-{index + 1:03}{extension}"
        target = self.target("Export dat", self.other.directory, name)
        if target is not None:
            unchanged(tape.path, tape.stamp)
            write_file(target, lambda stream: stream.write(data))
            self.status = f"Exportováno {len(data)} B do {target.name}."

    def run(self) -> None:
        actions = {
            curses.KEY_F1: lambda: self.viewer("Nápověda", HELP),
            curses.KEY_F2: self.go_to, curses.KEY_F3: self.inspect,
            curses.KEY_F4: self.extract, curses.KEY_F5: self.copy,
            curses.KEY_F6: self.move_image, curses.KEY_F7: self.create,
            curses.KEY_F8: self.remove, curses.KEY_F9: self.convert,
            "\x12": self.refresh, " ": lambda: self.mark(False), curses.KEY_IC: lambda: self.mark(False),
            "p": lambda: self.mark(True), "P": lambda: self.mark(True),
        }
        while True:
            visible = self.draw()
            key = self.screen.get_wch()
            if key in ("\x1b", curses.KEY_F10):
                return
            if not visible or key == curses.KEY_RESIZE:
                continue
            self.status = ""
            steps = {curses.KEY_UP: -1, curses.KEY_DOWN: 1,
                     curses.KEY_PPAGE: -visible, curses.KEY_NPAGE: visible,
                     curses.KEY_HOME: -len(self.panel.rows), curses.KEY_END: len(self.panel.rows)}
            try:
                if key in ("\t", curses.KEY_BTAB):
                    self.active = 1 - self.active
                elif key in steps:
                    self.panel.move(steps[key], visible)
                elif key in ENTER:
                    self.open_row()
                elif key in BACKSPACE:
                    self.parent()
                elif key in actions:
                    actions[key]()
            except FileExistsError as error:
                name = Path(error.filename2 or error.filename).name
                self.status = f"Cíl {name} již existuje."
            except (OSError, TapeError, ValueError, RuntimeError) as error:
                self.status = str(error)
