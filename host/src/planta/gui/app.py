"""Punto de entrada de la GUI: planta gui."""
from __future__ import annotations

import sys


def main() -> None:
    from PySide6 import QtWidgets

    from .window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Planta motor-generador")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
