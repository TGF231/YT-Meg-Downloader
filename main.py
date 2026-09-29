#!/usr/bin/env python3
"""Ponto de entrada da interface gráfica.

Uso:
    python main.py
"""

import sys


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main() -> int:
    if sys.version_info < (3, 9):
        return _fail("É necessário Python 3.9 ou superior.")
    try:
        from pen_drive_maker.gui import main as gui_main
    except ImportError as exc:
        return _fail(
            f"Dependência ausente: {exc}\n"
            "Instale com: pip install -r requirements.txt"
        )
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
