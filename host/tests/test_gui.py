"""Prueba de humo de la GUI sin pantalla (QT_QPA_PLATFORM=offscreen) contra sim://."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from planta.gui.window import MainWindow  # noqa: E402
from planta.recording import read_csv  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _run(app, sec):
    t = time.monotonic()
    while time.monotonic() - t < sec:
        app.processEvents()
        time.sleep(0.01)


def test_conectar_aplicar_grabar(app, tmp_path):
    w = MainWindow()
    w.dir_ed.setText(str(tmp_path))
    w.port_cb.setCurrentText("sim://")
    w._toggle_connect()
    assert w.dev is not None
    w.wave_cb.setCurrentIndex(2)
    w._apply_wave()
    _run(app, 0.3)
    w._toggle_record()
    _run(app, 0.8)
    w._zero()
    _run(app, 0.3)
    w._toggle_record()
    files = list(tmp_path.glob("captura_*.csv"))
    assert len(files) == 1
    meta, cols = read_csv(files[0])
    assert meta["origen"] == "GUI"
    assert "DC pct=0" in meta["cambios_onda"]
    assert cols["t_s"][0] == 0
    assert int(meta["muestras_faltantes"]) == 0
    assert w.missing == 0
    w.close()
    assert w.dev is None


def test_onda_invalida_no_se_aplica(app, monkeypatch):
    errores = []
    w = MainWindow()
    monkeypatch.setattr(w, "_error", errores.append)
    w.port_cb.setCurrentText("sim://")
    w._toggle_connect()
    w.wave_cb.setCurrentIndex(2)
    w.sin_off.setValue(20)
    w.sin_amp.setValue(30)  # offset - amp < 0
    w._apply_wave()
    assert errores and w.wave.kind == "dc"
    w.close()
