# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line entry point for TZX Commander."""

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="tzx-commander", description="Dvoupanelový správce obrazů kazet TAP a TZX."
    )
    parser.add_argument("left", metavar="LEVÝ", type=Path, nargs="?", default=Path.cwd(),
                        help="cesta k adresáři nebo obrazu v levém panelu")
    parser.add_argument("right", metavar="PRAVÝ", type=Path, nargs="?", default=Path.cwd(),
                        help="cesta k adresáři nebo obrazu v pravém panelu")
    args = parser.parse_args()

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(1, "TZX Commander vyžaduje interaktivní terminál.\n")

    from .tape import TapeError
    from .tui import Commander, Panel

    panels = [Panel(args.left), Panel(args.right)]
    try:
        for panel in panels:
            panel.load(panel.location)
        return Commander(panels).run()
    except (OSError, TapeError, RuntimeError, ValueError) as error:
        parser.exit(1, f"TZX Commander: {error}\n")
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
