"""Punto de entrada de la GUI: planta gui."""
from __future__ import annotations

import sys
from importlib import resources


def main() -> None:
    from PySide6 import QtGui, QtWidgets

    from .window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Planta motor-generador")
    app.setDesktopFileName("planta")  # Wayland asocia el ícono por el .desktop
    app.setWindowIcon(QtGui.QIcon(str(resources.files("planta").joinpath("gui", "icon.png"))))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
