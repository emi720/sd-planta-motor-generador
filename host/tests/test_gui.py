"""Prueba de humo de la GUI sin pantalla (QT_QPA_PLATFORM=offscreen) contra sim://."""
import os
import time

import numpy as np

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
    files = list(tmp_path.glob("*_captura.csv"))
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


def test_pausa_congela_y_explora(app):
    w = MainWindow()
    w.port_cb.setCurrentText("sim://")
    w._toggle_connect()
    w._set_wave(__import__("planta.protocol", fromlist=["Wave"]).Wave.dc(60))
    _run(app, 0.5)
    w._toggle_pause()
    assert w.paused and w.all_btn.isEnabled()
    n_snap = len(w.snap)
    x_before = w.c_in.getData()[0][-1]
    _run(app, 0.4)
    assert w.c_in.getData()[0][-1] == x_before   # el gráfico no avanza
    assert w.buf.n > n_snap                       # la adquisición sigue
    w.p_in.setXRange(x_before - 0.01, x_before, padding=0)
    _run(app, 0.1)
    x, y = w.c_pwm.getData()
    assert set(np.unique(y)) <= {0.0, 12.0} and len(x) >= 4 * 9  # pulsos
    w._toggle_pause()
    assert not w.paused
    w.ov_chk.setChecked(True)
    assert not w.p_out.isVisible() and not w.in_cb.isEnabled()
    w.close()


def test_cargar_firmware_desde_la_gui(app, monkeypatch):
    from PySide6 import QtWidgets as W

    import planta.gui.window as win
    calls = []

    def fake_flash(port, progress):
        calls.append(port)
        progress("grabando", 1.0)
        progress("verificando", 1.0)
        return "0.2.0"

    monkeypatch.setattr(win, "flash_bundled", fake_flash)
    monkeypatch.setattr(W.QMessageBox, "question", lambda *a, **k: W.QMessageBox.Yes)
    monkeypatch.setattr(W.QMessageBox, "information", lambda *a, **k: None)
    w = MainWindow()
    w.port_cb.setCurrentText("/dev/ttyFAKE0")
    w._flash()
    assert calls == ["/dev/ttyFAKE0"]
    assert "grabado y verificado" in w.log.toPlainText()
    w.port_cb.setCurrentText("sim://")
    w._toggle_connect()
    assert not w.flash_btn.isEnabled()   # conectado: no se puede grabar
    w.close()
