"""Punto de entrada del ejecutable empaquetado (PyInstaller).

`Planta --selftest` verifica el ejecutable sin pantalla ni Arduino: firmware
empaquetado, ventana, simulador y adquisición. Sale con 0 si todo anda.
"""
import os
import sys


def selftest() -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import time

    from PySide6 import QtWidgets

    from planta.device import list_ports
    from planta.flasher import bundled_hex, parse_hex
    from planta.gui.window import MainWindow
    from planta.protocol import Wave

    path, version = bundled_hex()
    size = len(parse_hex(path.read_text()))
    print(f"firmware empaquetado: {version}, {size} bytes")
    print("puertos:", ", ".join(list_ports()))
    app = QtWidgets.QApplication([])
    w = MainWindow()
    w.port_cb.setCurrentText("sim://")
    w._toggle_connect()
    w._set_wave(Wave.sin(1, 60, 20))
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.5:
        app.processEvents()
        time.sleep(0.01)
    n = w.buf.n
    w.close()
    print(f"muestras del simulador: {n}, faltantes: {w.missing}")
    ok = size > 5000 and n > 500 and w.missing == 0
    print("selftest", "OK" if ok else "FALLÓ")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    from planta.gui.app import main
    main()
